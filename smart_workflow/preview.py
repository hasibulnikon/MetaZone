"""Stage 1 — Preview Generation.

Downscales every original image to a small temporary preview (long side
~768-1024px) before anything ever touches the AI. Previews are what get
sent to Stage 2 (inspection) and Stage 4 (metadata generation) — the
original file is never uploaded and never modified until Stage 6
(embedding), which writes back into the ORIGINAL path, not the preview.

Vector/video files (svg/eps/ai/mp4/mov) have no raster preview to build;
callers fall back to sending the original path in that case (same as the
Standard Workflow already does for those extensions).
"""
import os
from PIL import Image
from core.constants import VECTOR_EXTS, VIDEO_EXTS

PREVIEW_MAX_SIDE = 1024
PREVIEW_CACHE_DIRNAME = ".smartzone_cache"


def cache_dir_for(folder):
    d = os.path.join(folder, PREVIEW_CACHE_DIRNAME)
    os.makedirs(d, exist_ok=True)
    return d


def make_preview(path, cache_dir, max_side=PREVIEW_MAX_SIDE):
    """Build one preview on a worker thread. Returns the preview path, or
    the original path unchanged for vector/video files that can't be
    rasterized. Never raises — a failed preview just falls back to the
    original file so Stage 2/4 can still attempt it."""
    ext = os.path.splitext(path)[1].lower()
    if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
        return path
    try:
        img = Image.open(path)
        img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if longest > max_side:
            scale = max_side / float(longest)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        name = f"{abs(hash(path)) % (10**10)}_{os.path.basename(path)}"
        out = os.path.join(cache_dir, os.path.splitext(name)[0] + ".jpg")
        img.save(out, "JPEG", quality=85)
        return out
    except Exception:
        return path


def generate_previews(paths, cache_dir, task_mgr, max_workers=6, on_progress=None, stop_flag=None):
    """Bounded-parallel preview generation for a batch. Returns
    {original_path: preview_path}. Blocks the calling (pipeline) thread —
    it's meant to be called from the pipeline's own background thread,
    not the UI thread."""
    import threading
    result = {}
    lock = threading.Lock()
    done = [0]

    def worker(path, i):
        if stop_flag and stop_flag():
            return
        prev = make_preview(path, cache_dir)
        with lock:
            result[path] = prev
            done[0] += 1
        if on_progress:
            on_progress(done[0], len(paths))

    done_event = threading.Event()
    task_mgr.run_batch(paths, worker, max_workers=max_workers,
                        on_all_done=done_event.set)
    done_event.wait()
    return result


def cleanup_cache(cache_dir):
    """Delete every temporary preview + the cache folder itself. Called
    at the very end of Stage 7, and also safe to call early if the
    pipeline is stopped."""
    try:
        import shutil
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir, ignore_errors=True)
    except Exception:
        pass
