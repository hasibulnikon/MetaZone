"""Project recovery — saves Smart Workflow progress to disk after every
stage (and periodically during the longer stages) so an interrupted run
can resume from the last completed stage instead of restarting.

State lives at <folder>/.smartzone_cache/state.json, next to the preview
cache for that same run — so cleaning up the cache folder at the end of
a successful Stage 7 also clears the recovery state automatically.
"""
import os, json, time

STATE_FILENAME = "state.json"

STAGES = [
    "previews", "inspection", "selection", "generation",
    "optimization", "embedding", "organization",
]


def state_path(cache_dir):
    return os.path.join(cache_dir, STATE_FILENAME)


def save_state(cache_dir, data):
    data = dict(data)
    data["saved_at"] = time.time()
    path = state_path(cache_dir)
    tmp = path + ".tmp"
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def load_state(cache_dir):
    try:
        with open(state_path(cache_dir)) as f:
            return json.load(f)
    except Exception:
        return None


def find_resumable(folder):
    """Returns the saved state dict if `folder` has an unfinished Smart
    Workflow run, else None."""
    from smart_workflow.preview import cache_dir_for
    cache_dir = os.path.join(folder, ".smartzone_cache")
    if not os.path.isdir(cache_dir):
        return None
    data = load_state(cache_dir)
    if data and data.get("stage") not in (None, "complete"):
        return data
    return None


def clear_state(cache_dir):
    try:
        p = state_path(cache_dir)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass
