"""AI provider calls, API-key validation, and the failover engine that
rotates across providers/keys until one succeeds.
"""
import json, urllib.request, urllib.error
from core.constants import AI_PROVIDERS, HIDDEN_PROVIDERS
from core.utils import img_to_b64, model_label

def _post(url,body,headers,timeout=30):
    req=urllib.request.Request(url,data=json.dumps(body).encode(),
                               headers=headers,method="POST")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            raw=e.read().decode(errors='replace')
            try: msg=json.loads(raw).get("error",{}).get("message") or raw[:300]
            except: msg=raw[:300]
        except: msg=str(e)
        raise RuntimeError(f"HTTP {e.code}: {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {str(e.reason)}")

def _normalize_paths(path):
    """Every provider call ultimately accepts EITHER a single image path
    (the original, still-default behavior — Meta Generator, Smart
    Workflow, single-image Prompt Generator, and Prompt-to-Prompt's
    original Image mode all still pass a plain string, completely
    unaffected by this) OR a list of paths, for Prompt-to-Prompt's
    multi-image Image mode (up to 15 reference images analyzed together
    in one call). This just normalizes either shape to a list once, so
    every provider function below can loop over it uniformly."""
    if not path: return []
    if isinstance(path,(list,tuple)): return list(path)
    return [path]

def call_gemini(key,model,path,prompt,max_tokens=2200):
    parts=[{"text":prompt}]
    for p in reversed(_normalize_paths(path)):
        b64,mime=img_to_b64(p)
        parts.insert(0,{"inline_data":{"mime_type":mime,"data":b64}})
    r=_post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        {"contents":[{"parts":parts}],
         "generationConfig":{"temperature":0.3,"maxOutputTokens":max_tokens}},
        {"Content-Type":"application/json"})
    try: return r["candidates"][0]["content"]["parts"][0]["text"]
    except: raise RuntimeError(f"Gemini parse error: {str(r)[:200]}")

def _oa_style_content(path,prompt):
    """Content list shared by every OpenAI-chat-format provider
    (OpenRouter/OpenAI/Groq/Grok/Mistral) — image part(s) omitted
    entirely for a text-only call (Prompt-to-Prompt's text mode), so
    those providers never see an image field they didn't ask for.
    Supports one image or several — see _normalize_paths."""
    content=[{"type":"text","text":prompt}]
    for p in reversed(_normalize_paths(path)):
        b64,mime=img_to_b64(p)
        content.insert(0,{"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}})
    return content

def _claude_style_content(path,prompt):
    content=[{"type":"text","text":prompt}]
    for p in reversed(_normalize_paths(path)):
        b64,mime=img_to_b64(p)
        content.insert(0,{"type":"image","source":{"type":"base64","media_type":mime,"data":b64}})
    return content

def call_openrouter(key,model,path,prompt,max_tokens=2200):
    r=_post("https://openrouter.ai/api/v1/chat/completions",
        {"model":model,"max_tokens":max_tokens,"messages":[
            {"role":"user","content":_oa_style_content(path,prompt)}]},
        {"Content-Type":"application/json","Authorization":f"Bearer {key}",
         "HTTP-Referer":"https://metazone.app","X-Title":"Meta Zone"})
    try: return r["choices"][0]["message"]["content"]
    except: raise RuntimeError(f"OpenRouter parse error: {str(r)[:200]}")

def call_claude(key,model,path,prompt,max_tokens=2200):
    r=_post("https://api.anthropic.com/v1/messages",
        {"model":model,"max_tokens":max_tokens,"messages":[
            {"role":"user","content":_claude_style_content(path,prompt)}]},
        {"Content-Type":"application/json","x-api-key":key,"anthropic-version":"2023-06-01"})
    try: return r["content"][0]["text"]
    except: raise RuntimeError(f"Claude parse error: {str(r)[:200]}")

def call_openai(key,model,path,prompt,max_tokens=2200):
    r=_post("https://api.openai.com/v1/chat/completions",
        {"model":model,"max_tokens":max_tokens,"messages":[
            {"role":"user","content":_oa_style_content(path,prompt)}]},
        {"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    try: return r["choices"][0]["message"]["content"]
    except: raise RuntimeError(f"OpenAI parse error: {str(r)[:200]}")

def call_groq(key,model,path,prompt,max_tokens=2200):
    r=_post("https://api.groq.com/openai/v1/chat/completions",
        {"model":model,"max_tokens":max_tokens,"messages":[
            {"role":"user","content":_oa_style_content(path,prompt)}]},
        {"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    try: return r["choices"][0]["message"]["content"]
    except: raise RuntimeError(f"Groq parse error: {str(r)[:200]}")

def call_grok(key,model,path,prompt,max_tokens=2200):
    """xAI's Grok — not to be confused with Groq (LPU inference cloud)
    above. Different company, different endpoint, different key format
    (xAI keys look like 'xai-...'; Groq keys look like 'gsk_...')."""
    r=_post("https://api.x.ai/v1/chat/completions",
        {"model":model,"max_tokens":max_tokens,"messages":[
            {"role":"user","content":_oa_style_content(path,prompt)}]},
        {"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    try: return r["choices"][0]["message"]["content"]
    except: raise RuntimeError(f"Grok parse error: {str(r)[:200]}")

def call_mistral(key,model,path,prompt,max_tokens=2200):
    r=_post("https://api.mistral.ai/v1/chat/completions",
        {"model":model,"max_tokens":max_tokens,"messages":[
            {"role":"user","content":_oa_style_content(path,prompt)}]},
        {"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    try: return r["choices"][0]["message"]["content"]
    except: raise RuntimeError(f"Mistral parse error: {str(r)[:200]}")

CALLERS={"Gemini":call_gemini,"OpenRouter":call_openrouter,"Claude":call_claude,
         "OpenAI":call_openai,"Groq":call_groq,"Mistral":call_mistral,"Grok":call_grok}

# ── API key validation (lightweight, cheap calls) ──────────────────────
def validate_key(provider, key):
    """Returns (ok: bool, message: str)"""
    key = key.strip()
    if not key:
        return False, "Empty key"
    try:
        if provider == "Gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=12) as r:
                json.loads(r.read())
            return True, "Valid"
        elif provider == "OpenRouter":
            req = urllib.request.Request("https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {key}"}, method="GET")
            with urllib.request.urlopen(req, timeout=12) as r:
                json.loads(r.read())
            return True, "Valid"
        elif provider == "Mistral":
            req = urllib.request.Request("https://api.mistral.ai/v1/models",
                headers={"Authorization": f"Bearer {key}"}, method="GET")
            with urllib.request.urlopen(req, timeout=12) as r:
                json.loads(r.read())
            return True, "Valid"
        elif provider == "Groq":
            req = urllib.request.Request("https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"}, method="GET")
            with urllib.request.urlopen(req, timeout=12) as r:
                json.loads(r.read())
            return True, "Valid"
        elif provider == "OpenAI":
            req = urllib.request.Request("https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"}, method="GET")
            with urllib.request.urlopen(req, timeout=12) as r:
                json.loads(r.read())
            return True, "Valid"
        elif provider == "Claude":
            body = json.dumps({"model":"claude-haiku-4-5-20251001","max_tokens":1,
                               "messages":[{"role":"user","content":"hi"}]}).encode()
            req = urllib.request.Request("https://api.anthropic.com/v1/messages",
                data=body, headers={"Content-Type":"application/json","x-api-key":key,
                "anthropic-version":"2023-06-01"}, method="POST")
            with urllib.request.urlopen(req, timeout=12) as r:
                json.loads(r.read())
            return True, "Valid"
        elif provider == "Grok":
            req = urllib.request.Request("https://api.x.ai/v1/models",
                headers={"Authorization": f"Bearer {key}"}, method="GET")
            with urllib.request.urlopen(req, timeout=12) as r:
                json.loads(r.read())
            return True, "Valid"
    except urllib.error.HTTPError as e:
        try: body=e.read().decode("utf-8","replace")[:200]
        except Exception: body=""
        if e.code in (401, 403):
            return False, f"Invalid key" + (f" — {body}" if body else "")
        elif e.code == 429:
            return True, "Valid (rate-limited)"
        else:
            return False, f"HTTP {e.code}" + (f" — {body}" if body else "")
    except Exception as e:
        return False, f"Error: {str(e)[:40]}"
    return False, "Unknown"

def get_active_keys(prefs):
    seq=[]
    for provider,cfg in AI_PROVIDERS.items():
        if provider in HIDDEN_PROVIDERS: continue
        keys=prefs.get("ai_keys",{}).get(provider,[])
        model=prefs.get("ai_models",{}).get(provider, cfg["models"][0][1])
        active_keys=[k for k in keys if k.get("active") and k.get("key")]
        for i,k in enumerate(active_keys,1):
            seq.append((provider,k["key"],model,i))
    return seq

def call_with_failover(path,prompt,prefs,status_cb=None,max_tokens=2200):
    """Try each active key exactly once in order.
    On failure, immediately move to the next key.
    If all keys fail, raise with the last error."""
    seq=get_active_keys(prefs)
    if not seq: raise RuntimeError("No active API keys. Open 'Configuration'.")
    last_err=""
    for provider,key,model,key_idx in seq:
        try:
            if status_cb:
                status_cb(f"{provider} · {model_label(provider,model)}…")
            raw=CALLERS[provider](key,model,path,prompt,max_tokens=max_tokens)
            return raw,provider,model,key_idx
        except Exception as e:
            last_err=f"{provider}: {str(e)[:120]}"
            # Log the failure and immediately try the next key
            continue
    raise RuntimeError(f"All keys failed. Last error: {last_err}")

