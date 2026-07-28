"""Static configuration: app version, AI provider catalog, platform
keyword/title/description rules, supported file extensions. No logic
here — just data other modules read.
"""

APP_VERSION = "v0.2"  # bump this on each major update: v0.3, v0.4, ...

AI_PROVIDERS = {
    "OpenRouter": {
        "models": [
            ("Qwen 2.5 VL 72B",      "qwen/qwen2.5-vl-72b-instruct:free"),
            ("Qwen 2.5 VL 32B",      "qwen/qwen2.5-vl-32b-instruct:free"),
            ("Gemini 2.0 Flash",     "google/gemini-2.0-flash-exp:free"),
            ("Llama 4 Maverick",     "meta-llama/llama-4-maverick:free"),
            ("Llama 4 Scout",        "meta-llama/llama-4-scout:free"),
            ("Mistral Small 3.1",    "mistralai/mistral-small-3.1-24b-instruct:free"),
        ],
        "key_url": "https://openrouter.ai/keys",
        "key_hint": "Get free key → openrouter.ai",
        "validate": "openrouter",
    },
    "Gemini": {
        "models": [
            ("Gemini 2.5 Flash",     "gemini-2.5-flash"),
            ("Gemini 2.0 Flash",     "gemini-2.0-flash"),
            ("Gemini 1.5 Flash",     "gemini-1.5-flash"),
            ("Gemini 1.5 Pro",       "gemini-1.5-pro"),
        ],
        "key_url": "https://aistudio.google.com/app/apikey",
        "key_hint": "Get free key → aistudio.google.com",
        "validate": "gemini",
    },
    "Mistral": {
        "models": [
            ("Pixtral 12B",  "pixtral-12b-2409"),
            ("Pixtral Large","pixtral-large-2411"),
        ],
        "key_url": "https://console.mistral.ai/api-keys/",
        "key_hint": "Get key → console.mistral.ai",
        "validate": "mistral",
    },
    "Groq": {
        "models": [
            # Groq deprecated both Llama 4 Scout (Jun 2026) and Maverick
            # (Feb 2026) in favor of text-only gpt-oss models. Qwen 3.6 27B
            # is currently Groq's vision-capable model — note it's a
            # preview model on Groq's side, so this may need updating again
            # if Groq's lineup changes (check console.groq.com/docs/vision).
            ("Qwen 3.6 27B (Vision)", "qwen/qwen3.6-27b"),
        ],
        "key_url": "https://console.groq.com/keys",
        "key_hint": "Get free key → console.groq.com",
        "validate": "groq",
    },
    "OpenAI": {
        "models": [
            ("GPT-4o",      "gpt-4o"),
            ("GPT-4o Mini", "gpt-4o-mini"),
            ("GPT-4.1 Nano","gpt-4.1-nano"),
        ],
        "key_url": "https://platform.openai.com/api-keys",
        "key_hint": "Get key → platform.openai.com",
        "validate": "openai",
    },
    "Claude": {
        "models": [
            ("Claude Haiku 4.5",  "claude-haiku-4-5-20251001"),
            ("Claude Sonnet 5",   "claude-sonnet-5"),
        ],
        "key_url": "https://console.anthropic.com/settings/keys",
        "key_hint": "Get key → console.anthropic.com",
        "validate": "claude",
    },
    "Grok": {
        "models": [
            ("Grok 4",       "grok-4"),
            ("Grok 4 Fast",  "grok-4-fast"),
        ],
        "key_url": "https://console.x.ai",
        "key_hint": "Get key → console.x.ai (this is xAI's Grok — different from Groq above)",
        "validate": "grok",
    },
}

CONTENT_SUFFIXES = {
    "Auto Detect":       "",
    "Vector":            "a vector illustration",
    "Illustration":      "a digital illustration/artwork, not a photograph",
    "Transparent PNG":   "isolated on a transparent background",
    "White Background":  "on a solid white background",
    "Silhouette":        "presented as a silhouette",
}

IMAGE_EXTS  = {'.jpg','.jpeg','.png','.gif','.webp','.tiff','.tif'}
VECTOR_EXTS = {'.svg','.eps','.ai'}
VIDEO_EXTS  = {'.mp4','.mov'}
ALL_SUPPORTED_EXTS = IMAGE_EXTS | VECTOR_EXTS | VIDEO_EXTS

AI_PROVIDERS_ORDERED=["Gemini","Mistral","Groq","OpenAI","Claude","Grok","OpenRouter"]

# Hidden from the API Configuration tabs and skipped during generation
# failover — NOT deleted from AI_PROVIDERS/CALLERS/AI_PROVIDERS_ORDERED, so
# re-enabling them later (or if their issues get sorted out) is just
# removing an entry here, nothing structural.
HIDDEN_PROVIDERS={"Grok","Groq"}
VISIBLE_PROVIDERS=[p for p in AI_PROVIDERS_ORDERED if p not in HIDDEN_PROVIDERS]

PLATFORM_RULES = {
    "General":      {"kw":49,"title":300,"desc":250},
    "Adobe Stock":  {"kw":49,"title":150,"desc":250},
    "Shutterstock": {"kw":50,"title":200,"desc":200},
    "Getty Images": {"kw":50,"title":200,"desc":500},
    "Freepik":      {"kw":30,"title":150,"desc":200},
    "Pond5":        {"kw":50,"title":200,"desc":500},
    "iStock":       {"kw":50,"title":200,"desc":200},
    "Vecteezy":     {"kw":50,"title":200,"desc":200},
}
