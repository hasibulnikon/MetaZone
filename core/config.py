"""User preferences: load/save prefs.json (API keys, provider settings,
last-used folders, UI toggles). Atomic writes so a crash mid-save never
corrupts the file.
"""
import os, sys, json

def _common_pref_dir():
    """A single shared folder every installed/running version of the app
    reads and writes prefs.json from, so settings survive switching
    between v0.x/v1.x builds and can't be lost by deleting an old
    version's folder by accident.
    On Windows this is <SystemDrive>\\MetaZone (normally C:\\MetaZone).
    On non-Windows (dev/test) it falls back to a MetaZone folder under
    the user's home directory, since there's no C: drive concept."""
    if os.name=="nt":
        drive = os.environ.get("SystemDrive","C:")
        base = os.path.join(f"{drive}\\", "MetaZone")
    else:
        base = os.path.join(os.path.expanduser("~"), "MetaZone")
    return base

def _legacy_prefs_path():
    # Where prefs.json used to live (next to the EXE / app.py) — only
    # consulted once, to migrate an existing user's settings forward.
    if getattr(sys,"frozen",False):
        base = os.path.dirname(sys.executable)
    else:
        main_mod = sys.modules.get("__main__")
        main_file = getattr(main_mod,"__file__",None)
        base = os.path.dirname(os.path.abspath(main_file)) if main_file else os.getcwd()
    return os.path.join(base,"prefs.json")

def prefs_path():
    base = _common_pref_dir()
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        # Can't create/access the common folder (permissions, no C: drive,
        # etc.) — fall back to the old next-to-the-app location rather
        # than crashing on startup.
        return _legacy_prefs_path()
    path = os.path.join(base, "prefs.json")
    if not os.path.exists(path):
        # First run against the new shared location — migrate a legacy
        # prefs.json (if any) so existing settings/API keys aren't lost.
        legacy = _legacy_prefs_path()
        if os.path.exists(legacy):
            try:
                import shutil
                shutil.copy2(legacy, path)
            except Exception:
                pass
    return path

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
