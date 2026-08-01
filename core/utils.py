"""Small stateless helpers: exiftool discovery, file matching, thumbnail
generation, filesize formatting, provider model-id <-> label lookups.
No AI calls beyond image encoding, no app state.
"""
import os, sys, subprocess, base64, socket
import customtkinter as ctk
from PIL import Image
from core.constants import AI_PROVIDERS, VECTOR_EXTS, VIDEO_EXTS

def model_label(provider, model_id):
    for label, mid in AI_PROVIDERS.get(provider, {}).get("models", []):
        if mid == model_id:
            return label
    return model_id.split("/")[-1].split(":")[0][:22]

def model_id_from_label(provider, label):
    for lbl, mid in AI_PROVIDERS.get(provider, {}).get("models", []):
        if lbl == label:
            return mid
    return label

def _app_root():
    # Resolve relative to the app entry point (app.py), not this module —
    # exiftool.exe ships next to app.py / the EXE, not next to core/utils.py.
    if getattr(sys,"frozen",False):
        return os.path.dirname(sys.executable)
    main_mod = sys.modules.get("__main__")
    main_file = getattr(main_mod,"__file__",None)
    return os.path.dirname(os.path.abspath(main_file)) if main_file else os.getcwd()

def find_exiftool():
    """Resolve exiftool.exe. Deliberately does NOT fall back to scanning the
    system PATH: a stray/leftover exiftool.exe from some other app's
    PyInstaller temp extraction folder (a _MEIxxxxxx dir) can end up on
    PATH and then vanish once that other process exits, which is exactly
    what produced 'Cannot find file at ...\\_MEIxxxxxx\\...\\exiftool.exe'
    errors here even though the CSV/folder were fine. Only trust paths
    that are actually ours: bundled with this build, or sitting next to
    this app's own exe/script."""
    if getattr(sys,'frozen',False):
        b = os.path.join(sys._MEIPASS,'exiftool_pkg','exiftool.exe')
        if os.path.exists(b): return b
    base = _app_root()
    for n in ['exiftool.exe','exiftool']:
        p = os.path.join(base,n)
        if os.path.exists(p): return p
    return None

def find_file(folder,name,match_ext):
    exact=os.path.join(folder,name)
    if os.path.exists(exact): return exact
    if match_ext:
        base=os.path.splitext(name)[0]
        try:
            for f in os.listdir(folder):
                if os.path.splitext(f)[0].lower()==base.lower():
                    return os.path.join(folder,f)
        except: pass
    return None

def find_recursive(folder,name,match_ext):
    r=find_file(folder,name,match_ext)
    if r: return r
    try:
        for root,dirs,files in os.walk(folder):
            if root==folder: continue
            r=find_file(root,name,match_ext)
            if r: return r
    except: pass
    return None

def check_online():
    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False

def make_thumb(path, size=(120,85)):
    """Build a CTkImage off the main thread. Returns None on failure."""
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
            return None
        img = Image.open(path)
        img = img.convert("RGB")
        img.thumbnail(size, Image.LANCZOS)
        return ctk.CTkImage(img, size=img.size)
    except Exception:
        return None


def make_thumb_min_edge(path, min_edge=100, max_edge=170):
    """Compact View's thumbnail: unlike make_thumb's bounding-box fit
    (both sides <= size), this scales so the SHORTER side is exactly
    min_edge and the longer side follows the image's own aspect ratio —
    capped at max_edge so an extreme panorama/vertical image can't blow
    out the card's layout."""
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
            return None
        img = Image.open(path)
        img = img.convert("RGB")
        w, h = img.size
        if w <= 0 or h <= 0:
            return None
        short = min(w, h)
        scale = min_edge / float(short)
        new_w, new_h = int(round(w * scale)), int(round(h * scale))
        if max(new_w, new_h) > max_edge:
            scale2 = max_edge / float(max(new_w, new_h))
            new_w, new_h = int(round(new_w * scale2)), int(round(new_h * scale2))
        new_w, new_h = max(new_w, 1), max(new_h, 1)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        return ctk.CTkImage(img, size=(new_w, new_h))
    except Exception:
        return None


def img_to_b64(path):
    with open(path,'rb') as f: data=f.read()
    ext=os.path.splitext(path)[1].lower()
    mime={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png',
          '.gif':'image/gif','.webp':'image/webp',
          '.tiff':'image/tiff','.tif':'image/tiff'}.get(ext,'image/jpeg')
    return base64.b64encode(data).decode(),mime

def embed_metadata_one(et, fp, title="", kw_raw="", desc="", rm_prog=False, rm_copy=False):
    """Write title/keywords/description into a single file via ExifTool.
    Pure function, no UI/state — shared by ui/embed_window.py's CSV-driven
    embed flow and smart_workflow's Stage 6, so the exiftool command
    construction only lives in one place.
    Returns (ok: bool, message: str) — message is either the affected
    filename on success or the exiftool error text on failure."""
    cmd=[et,'-overwrite_original','-codedcharacterset=UTF8']
    if title: cmd+=[f'-Title={title}',f'-ObjectName={title}',f'-Headline={title}']
    if kw_raw:
        for kw in [k.strip() for k in kw_raw.replace(';',',').split(',') if k.strip()]:
            cmd+=[f'-Keywords={kw}',f'-Subject={kw}']
    if desc: cmd+=[f'-Description={desc}',f'-Caption-Abstract={desc}']
    if rm_prog: cmd+=['-Software=','-CreatorTool=','-HistorySoftwareAgent=']
    if rm_copy: cmd+=['-Rights=','-Copyright=','-CopyrightNotice=','-Creator=']
    cmd.append(fp)
    try:
        flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
        res=subprocess.run(cmd,capture_output=True,text=True,timeout=30,creationflags=flags)
        if res.returncode==0:
            return True, os.path.basename(fp)
        return False, (res.stderr or res.stdout or "Unknown").strip()
    except Exception as ex:
        return False, str(ex)

def format_filesize(path):
    try:
        n=os.path.getsize(path)
    except Exception:
        return "—"
    for unit in ("B","KB","MB","GB"):
        if n<1024:
            return f"{n:.0f} {unit}" if unit=="B" else f"{n:.1f} {unit}"
        n/=1024
    return f"{n:.1f} TB"

def relaunch_app():
    """Close this process and start a fresh one. Used after applying a
    new theme — colors are derived once at ui/theme.py's import time
    (see that module), so a genuinely fresh process is what makes a new
    choice actually take effect, not a live-reactive rebuild. Handles
    both running from source (python app.py) and the packaged frozen
    EXE — a frozen build has no Python interpreter to hand a script to,
    so it must re-launch sys.executable directly with no arguments.

    IMPORTANT (frozen/onefile only): the child MUST NOT inherit this
    process's _MEIPASS2 env var. PyInstaller's onefile bootloader sets
    _MEIPASS2 internally once it has extracted itself; if a relaunched
    child inherits that value (subprocess.Popen inherits the full
    environment by default), its bootloader assumes it's already
    extracted and tries to run directly off THIS process's temp folder
    instead of doing its own fresh extraction. That folder disappears
    once this process exits, so the child breaks the moment you relaunch
    a second time in the same session (the first relaunch can appear to
    work purely because the old temp folder hasn't been cleaned up yet)."""
    try:
        env=os.environ.copy()
        env.pop("_MEIPASS2",None)
        if getattr(sys,"frozen",False):
            subprocess.Popen([sys.executable],env=env)
        else:
            main_mod=sys.modules.get("__main__")
            main_file=getattr(main_mod,"__file__",None)
            if main_file:
                subprocess.Popen([sys.executable,os.path.abspath(main_file)],env=env)
    finally:
        os._exit(0)

