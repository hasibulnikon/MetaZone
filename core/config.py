"""User preferences: load/save prefs.json (API keys, provider settings,
last-used folders, UI toggles). Atomic writes so a crash mid-save never
corrupts the file.
"""
import os, sys, json

def prefs_path():
    # Resolve relative to the app entry point (app.py), not this module,
    # so prefs.json always lands next to the EXE / app.py exactly like it
    # did before the module split.
    if getattr(sys,"frozen",False):
        base = os.path.dirname(sys.executable)
    else:
        main_mod = sys.modules.get("__main__")
        main_file = getattr(main_mod,"__file__",None)
        base = os.path.dirname(os.path.abspath(main_file)) if main_file else os.getcwd()
    return os.path.join(base,"prefs.json")

def load_prefs():
    path = prefs_path()
    try:
        with open(path) as f: return json.load(f)
    except Exception:
        # Corrupted or missing prefs.json — preserve the broken file for
        # inspection instead of silently discarding it, then start fresh.
        if os.path.exists(path):
            try: os.replace(path, path + ".corrupt")
            except Exception: pass
        return {}

def save_prefs(p):
    """Atomic write: write to a temp file then rename over the real one.
    This prevents prefs.json from ever being left half-written if the
    app freezes, crashes, or is killed mid-save — which is what silently
    drops stored API keys."""
    path = prefs_path()
    tmp = path + ".tmp"
    try:
        with open(tmp,'w') as f:
            json.dump(p,f,indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception: pass
