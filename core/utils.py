"""Small stateless helpers: exiftool discovery, file matching, thumbnail
generation, filesize formatting, provider model-id <-> label lookups.
No AI calls beyond image encoding, no app state.
"""
import os, sys, subprocess, base64, socket, time, hashlib
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
    this app's own exe/script.

    Retries briefly on a miss rather than failing on the very first
    check: right after relaunch_app() restarts the process, Windows/AV
    can still be settling a lock on the freshly re-extracted onefile
    temp folder, which made os.path.exists() report a false negative
    for a file that genuinely is there — showing 'ExifTool missing' in
    red immediately after a theme change even though it wasn't."""
    def _resolve():
        if getattr(sys,'frozen',False):
            b = os.path.join(sys._MEIPASS,'exiftool_pkg','exiftool.exe')
            if os.path.exists(b): return b
        base = _app_root()
        for n in ['exiftool.exe','exiftool']:
            p = os.path.join(base,n)
            if os.path.exists(p): return p
        return None
    found = _resolve()
    if found or not getattr(sys,'frozen',False):
        return found
    for _ in range(4):
        time.sleep(0.25)
        found = _resolve()
        if found:
            return found
    return None

def _icon_paths():
    """Resolve icon.ico/icon.png the same way find_exiftool resolves
    exiftool.exe — bundled next to the frozen EXE (PyInstaller --icon +
    --add-data), or sitting next to app.py in dev."""
    ico = png = None
    if getattr(sys,'frozen',False):
        b = getattr(sys,'_MEIPASS',None)
        if b:
            c = os.path.join(b,'icon.ico')
            if os.path.exists(c): ico = c
            c = os.path.join(b,'icon.png')
            if os.path.exists(c): png = c
    base = _app_root()
    if not ico:
        c = os.path.join(base,'icon.ico')
        if os.path.exists(c): ico = c
    if not png:
        c = os.path.join(base,'icon.png')
        if os.path.exists(c): png = c
    return ico, png

def set_window_icon(window):
    """Applies Meta Zone's real icon to a Tk window's titlebar (and, on
    Windows, the taskbar) instead of the generic Tk/PyInstaller default.
    Safe to call on every window (main app, Embed, API Manager) — quietly
    does nothing if no icon file is bundled rather than raising."""
    ico, png = _icon_paths()
    try:
        if ico and os.name=='nt':
            window.iconbitmap(ico)
            return
    except Exception:
        pass
    if png:
        try:
            from tkinter import PhotoImage
            photo = PhotoImage(file=png)
            window.iconphoto(True, photo)
            window._icon_photo_ref = photo  # keep a reference alive
        except Exception:
            pass

GEN_PREVIEW_MAX_SIDE = 1280

def _gen_preview_cache_dir():
    """Separate from the thumbnail cache — these are full-quality-enough
    JPEGs meant for the AI to actually analyze, not tiny display
    thumbnails, so they're kept in their own subfolder even though both
    live under the same common MetaZone folder."""
    try:
        from core.config import _common_pref_dir
        base = os.path.join(_common_pref_dir(), ".cache", "gen_previews")
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".metazone_cache", "gen_previews")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        return None
    return base

def prepare_generation_preview(path, max_side=GEN_PREVIEW_MAX_SIDE):
    """Resolves the path that should actually be SENT to the AI for
    metadata generation. For a large/high-res original (the 8-15MB
    upscaled-image case this exists for) this returns a cached, already-
    downscaled JPEG instead — same visual content, dramatically smaller
    upload — which is exactly what made generation slow for those files
    in the first place: the network upload and the provider's own image
    processing both scale with file size, not with how much detail an
    AI actually needs to write a title/description/keywords.

    Returns the ORIGINAL path unchanged (never raises, never blocks
    generation) when: the file is a vector/video (no raster preview
    possible), it's already at or under max_side on its long edge (a
    preview would just be a same-size recompress, not worth the extra
    file), or anything about building one fails."""
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
            return path
        cache_dir = _gen_preview_cache_dir()
        if not cache_dir:
            return path
        key = _thumb_cache_key(path, f"genprev{max_side}")
        cache_path = os.path.join(cache_dir, key + ".jpg")
        if os.path.exists(cache_path):
            return cache_path
        img = Image.open(path)
        w, h = img.size
        if max(w, h) <= max_side:
            return path  # already small enough — original is fine as-is
        img = img.convert("RGB")
        scale = max_side / float(max(w, h))
        new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        img.save(cache_path, "JPEG", quality=90)
        _thumb_cache_cleanup(cache_dir)
        return cache_path
    except Exception:
        return path

def clear_gen_preview_cache():
    """Wipes cached generation previews — same never-touches-prefs.json
    guarantee as clear_thumb_cache, since this only ever looks inside its
    own .cache/gen_previews subfolder."""
    cache_dir = _gen_preview_cache_dir()
    if not cache_dir or not os.path.isdir(cache_dir):
        return 0
    n = 0
    for entry in os.listdir(cache_dir):
        p = os.path.join(cache_dir, entry)
        try:
            if os.path.isfile(p):
                os.remove(p)
                n += 1
        except Exception:
            pass
    return n

def clear_thumb_cache():
    """Wipes every cached thumbnail file. Deliberately only ever touches
    files INSIDE the .cache/thumbs folder — prefs.json lives one level up
    in the common MetaZone folder itself and is never in scope here, by
    construction, not just by convention."""
    cache_dir = _thumb_cache_dir()
    if not cache_dir or not os.path.isdir(cache_dir):
        return 0
    n = 0
    for entry in os.listdir(cache_dir):
        p = os.path.join(cache_dir, entry)
        try:
            if os.path.isfile(p):
                os.remove(p)
                n += 1
        except Exception:
            pass
    return n

def prefetch_thumb_to_cache(path, size=None, min_edge=None, max_edge=170):
    """Resizes and writes ONE thumbnail straight to the disk cache —
    deliberately never constructs a CTkImage or touches Tk at all, so
    this is safe to call from as many background threads as needed for
    a whole-batch prefetch (see main_window._prefetch_all_thumbnails),
    not just the bounded single-widget worker pool that make_thumb/
    make_thumb_min_edge use when a card is actually on screen."""
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
            return
        cache_dir = _thumb_cache_dir()
        if not cache_dir:
            return
        if min_edge:
            key = _thumb_cache_key(path, f"edge{min_edge}x{max_edge}")
        else:
            size = size or (120, 85)
            key = _thumb_cache_key(path, f"box{size[0]}x{size[1]}")
        cache_path = os.path.join(cache_dir, key + ".jpg")
        if os.path.exists(cache_path):
            return  # already cached — nothing to do
        img = Image.open(path).convert("RGB")
        if min_edge:
            w, h = img.size
            if w <= 0 or h <= 0:
                return
            short = min(w, h)
            scale = min_edge / float(short)
            new_w, new_h = int(round(w * scale)), int(round(h * scale))
            if max(new_w, new_h) > max_edge:
                scale2 = max_edge / float(max(new_w, new_h))
                new_w, new_h = int(round(new_w * scale2)), int(round(new_h * scale2))
            img = img.resize((max(new_w, 1), max(new_h, 1)), Image.LANCZOS)
        else:
            img.thumbnail(size, Image.LANCZOS)
        img.save(cache_path, "JPEG", quality=85)
        _thumb_cache_cleanup(cache_dir)
    except Exception:
        pass

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

def _thumb_cache_dir():
    """Shared thumbnail cache — lives next to prefs.json in the same
    common MetaZone folder (see core/config.py's _common_pref_dir) so it
    persists across app versions the same way settings do."""
    try:
        from core.config import _common_pref_dir
        base = os.path.join(_common_pref_dir(), ".cache", "thumbs")
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".metazone_cache", "thumbs")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        return None
    return base

def _thumb_cache_key(path, tag):
    """mtime-keyed — a source file being replaced/edited automatically
    invalidates its cached thumbnail without needing to hash file
    contents (which would mean reading the whole file just to decide
    whether to skip reading the whole file)."""
    try:
        mtime = int(os.path.getmtime(path))
        size = os.path.getsize(path)
    except Exception:
        mtime, size = 0, 0
    raw = f"{os.path.abspath(path)}|{tag}|{mtime}|{size}"
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()

def _thumb_cache_cleanup(cache_dir, max_files=4000):
    """Automatic light cleanup — if the cache has grown past max_files,
    delete the oldest ones (by mtime) down to 90% of the cap. Cheap
    check (just a listdir) so it's fine to call opportunistically."""
    try:
        entries = os.listdir(cache_dir)
        if len(entries) <= max_files:
            return
        full = [os.path.join(cache_dir, e) for e in entries]
        full.sort(key=lambda p: os.path.getmtime(p))
        n_remove = len(full) - int(max_files * 0.9)
        for p in full[:n_remove]:
            try: os.remove(p)
            except Exception: pass
    except Exception:
        pass

def make_thumb(path, size=(120,85)):
    """Build a CTkImage off the main thread. Returns None on failure.
    Disk-cached: a resized copy is saved once and reused on later
    imports of the same file, instead of re-opening/re-resizing the
    full original image every single time."""
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
            return None
        cache_dir = _thumb_cache_dir()
        cache_path = None
        if cache_dir:
            key = _thumb_cache_key(path, f"box{size[0]}x{size[1]}")
            cache_path = os.path.join(cache_dir, key + ".jpg")
            if os.path.exists(cache_path):
                try:
                    cimg = Image.open(cache_path); cimg.load()
                    return ctk.CTkImage(cimg, size=cimg.size)
                except Exception:
                    pass  # cache file corrupt/unreadable — fall through and regenerate
        img = Image.open(path)
        img = img.convert("RGB")
        img.thumbnail(size, Image.LANCZOS)
        if cache_path:
            try:
                img.save(cache_path, "JPEG", quality=85)
                _thumb_cache_cleanup(cache_dir)
            except Exception:
                pass
        return ctk.CTkImage(img, size=img.size)
    except Exception:
        return None


def make_thumb_min_edge(path, min_edge=100, max_edge=170):
    """Compact View's thumbnail: unlike make_thumb's bounding-box fit
    (both sides <= size), this scales so the SHORTER side is exactly
    min_edge and the longer side follows the image's own aspect ratio —
    capped at max_edge so an extreme panorama/vertical image can't blow
    out the card's layout. Disk-cached the same way as make_thumb."""
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
            return None
        cache_dir = _thumb_cache_dir()
        cache_path = None
        if cache_dir:
            key = _thumb_cache_key(path, f"edge{min_edge}x{max_edge}")
            cache_path = os.path.join(cache_dir, key + ".jpg")
            if os.path.exists(cache_path):
                try:
                    cimg = Image.open(cache_path); cimg.load()
                    return ctk.CTkImage(cimg, size=cimg.size)
                except Exception:
                    pass
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
        if cache_path:
            try:
                img.save(cache_path, "JPEG", quality=85)
                _thumb_cache_cleanup(cache_dir)
            except Exception:
                pass
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

def _detect_and_fix_extension(fp):
    """If a file's real image format doesn't match its extension, ExifTool's
    format-specific writer refuses to touch it at all — rightly so, since
    writing (say) PNG-specific chunks into what's actually JPEG binary would
    corrupt the file, not just produce a warning. This is a real, fairly
    common occurrence with some AI image-generation tools that export
    JPEG-encoded data with a .png extension (or vice versa).

    There is no ExifTool flag that overrides this — verified directly
    against a real exiftool binary: `-m`, `-F`, `-api IgnoreMinorErrors=1`,
    and an explicit `-fileType=` override all still refuse, because they
    can't safely do otherwise; the file's actual bytes need the OTHER
    writer, unconditionally. The only correct fix is to give the file the
    extension that actually matches its content before writing.

    Returns (path_to_use, note) — note is None if nothing needed to change,
    otherwise a short human-readable description of what was renamed and
    why (meant for the caller's log/status output — never silent, since
    this does change the file's name on disk)."""
    ext=os.path.splitext(fp)[1].lower().lstrip(".")
    fmt_to_ext={"JPEG":"jpg","PNG":"png","WEBP":"webp","TIFF":"tif","BMP":"bmp","GIF":"gif"}
    try:
        with Image.open(fp) as im:
            real_fmt=im.format
    except Exception:
        return fp,None  # can't even open it -- let exiftool report its own error
    real_ext=fmt_to_ext.get(real_fmt)
    if not real_ext or real_ext==ext or (ext=="jpeg" and real_ext=="jpg"):
        return fp,None
    new_fp=os.path.splitext(fp)[0]+"."+real_ext
    if os.path.exists(new_fp):
        return fp,None  # don't clobber an existing file with that name
    try:
        os.rename(fp,new_fp)
        return new_fp,(f"{os.path.basename(fp)} was actually {real_fmt} data saved with a "
                        f".{ext} extension — renamed to {os.path.basename(new_fp)} to embed it")
    except Exception:
        return fp,None

def embed_metadata_one(et, fp, title="", kw_raw="", desc="", rm_prog=False, rm_copy=False):
    """Write title/keywords/description into a single file via ExifTool.
    Pure function, no UI/state — shared by ui/embed_window.py's CSV-driven
    embed flow and smart_workflow's Stage 6, so the exiftool command
    construction only lives in one place.
    Returns (ok: bool, message: str, final_path: str) — final_path is
    normally just fp unchanged, but may differ if a real/extension format
    mismatch was found and fixed first (see _detect_and_fix_extension);
    callers that do anything else with the file afterward (renaming to
    title, logging a filename) should use final_path, not the fp they
    passed in, since fp may no longer exist under that name."""
    fp,rename_note=_detect_and_fix_extension(fp)
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
            msg=os.path.basename(fp)
            if rename_note: msg=f"{msg}  ({rename_note})"
            return True, msg, fp
        return False, (res.stderr or res.stdout or "Unknown").strip(), fp
    except Exception as ex:
        return False, str(ex), fp

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
    work purely because the old temp folder hasn't been cleaned up yet).

    Two more failure modes seen specifically on a SECOND relaunch in one
    session (theme changed twice in a row): a 'Failed to remove
    temporary directory' warning from the bootloader, paired with a
    false 'ExifTool missing' status on the freshly-relaunched window.
    Both point at the same underlying race — Windows/antivirus hasn't
    finished releasing its lock on THIS process's _MEIxxxxxx extraction
    folder (which holds exiftool.exe) at the exact moment we vanish and
    the bootloader tries to clean it up, and/or the new child inherited
    a cwd that no longer exists once this process is gone. Neither is
    fully controllable from here, but both are mitigated by: never
    inheriting this process's (soon-to-be-gone) cwd, confirming the
    child actually started before we exit, and giving the OS a brief
    moment before we do."""
    try:
        env=os.environ.copy()
        env.pop("_MEIPASS2",None)
        # Never hand the child a cwd that lives inside THIS process's
        # onefile temp extraction — that folder is on its way out the
        # moment we exit, and a child that inherited it as its working
        # directory can fail in ways that look identical to a missing
        # bundled file (ExifTool included) even though it extracted fine.
        safe_cwd=os.path.expanduser("~") or None
        if getattr(sys,"frozen",False):
            proc=subprocess.Popen([sys.executable],env=env,cwd=safe_cwd)
        else:
            main_mod=sys.modules.get("__main__")
            main_file=getattr(main_mod,"__file__",None)
            proc=None
            if main_file:
                proc=subprocess.Popen([sys.executable,os.path.abspath(main_file)],
                    env=env,cwd=safe_cwd)
        # Confirm the child is actually alive (didn't crash on the spot)
        # before we disappear — and give Windows/AV a brief window to
        # settle file locks on this process's temp folder before the
        # bootloader tries to remove it.
        if proc is not None:
            time.sleep(0.35)
            proc.poll()  # refresh returncode; ignored either way — best-effort only
        time.sleep(0.15)
    finally:
        os._exit(0)

