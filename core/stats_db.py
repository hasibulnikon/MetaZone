"""Persistent stats + activity log for the Dashboard workspace.

Every real completion event in the app (metadata generation, embedding,
prompt generation, Smart Workflow runs) calls record() once here. This
is the ONLY source of truth for anything the Dashboard shows as a
number — nothing on the Dashboard is hardcoded or simulated; if a
number would otherwise be fake, the Dashboard shows 0 / "—" instead.

Cost estimates ARE genuinely estimates (there's no real per-provider
billing API wired up), computed from a flat assumed cost-per-request —
the Dashboard labels these "Est." to be honest about that rather than
presenting them as verified figures.
"""
import os, sqlite3, threading, datetime

DB_PATH = os.path.join(os.path.expanduser("~"), ".metazone", "stats.db")
_lock = threading.Lock()

# Flat, clearly-an-estimate per-request cost assumption — not tied to
# any specific provider's real pricing. Used only for the "Est. API
# Cost" / "Est. API Cost Saved" dashboard figures.
ASSUMED_COST_PER_REQUEST = 0.002


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        meta_score REAL,
        api_requests INTEGER NOT NULL DEFAULT 0,
        api_requests_saved INTEGER NOT NULL DEFAULT 0,
        seconds REAL NOT NULL DEFAULT 0,
        detail TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts)")
    return conn


def record(kind, status="completed", count=0, meta_score=None,
           api_requests=0, api_requests_saved=0, seconds=0.0, detail=""):
    """kind: one of 'metadata_generation','embedding','prompt_generation',
    'prompt_to_prompt','smart_workflow_run'. status: 'completed'|'failed'.
    Thread-safe — called directly from worker/completion threads."""
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO activity (ts,kind,status,count,meta_score,"
                "api_requests,api_requests_saved,seconds,detail) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, kind, status, count, meta_score, api_requests,
                 api_requests_saved, seconds, detail))
            conn.commit()
        finally:
            conn.close()


def _today_str():
    return datetime.date.today().isoformat()


def today_summary():
    with _lock:
        conn = _connect()
        try:
            today = _today_str()
            rows = conn.execute(
                "SELECT status,count,meta_score,api_requests FROM activity WHERE substr(ts,1,10)=?",
                (today,)).fetchall()
        finally:
            conn.close()
    processed = sum(c for _, c, _, _ in rows)
    completed = sum(c for st, c, _, _ in rows if st == "completed")
    failed = sum(c for st, c, _, _ in rows if st == "failed")
    scores = [s for _, _, s, _ in rows if s is not None]
    avg_score = (sum(scores) / len(scores)) if scores else None
    api_requests = sum(r for _, _, _, r in rows)
    return {
        "files_processed": processed,
        "completed": completed,
        "failed": failed,
        "avg_score": avg_score,
        "api_requests": api_requests,
    }


def lifetime_summary():
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT kind,status,count,api_requests,api_requests_saved,seconds "
                "FROM activity").fetchall()
        finally:
            conn.close()

    def sum_count(kind_set, status=None):
        return sum(c for k, st, c, _, _, _ in rows
                   if k in kind_set and (status is None or st == status))

    total_files = sum_count({"metadata_generation", "smart_workflow_run"}, "completed")
    total_embedded = sum_count({"embedding"}, "completed")
    total_prompts = sum_count({"prompt_generation"}, "completed")
    total_p2p = sum_count({"prompt_to_prompt"}, "completed")
    # smart_workflow_run rows are recorded one-per-completed-run (the
    # `count` field on that row holds files processed in that run, not
    # a run tally) — so count RUNS by row count, not by summing count.
    total_runs = sum(1 for k, st, _, _, _, _ in rows
                      if k == "smart_workflow_run" and st == "completed")
    total_requests = sum(r for _, _, _, r, _, _ in rows)
    total_saved = sum(s for _, _, _, _, s, _ in rows)
    total_seconds = sum(s for _, _, _, _, _, s in rows)

    return {
        "total_files_processed": total_files,
        "total_metadata_generated": total_files,
        "total_embedded": total_embedded,
        "total_prompt_generations": total_prompts,
        "total_prompt_to_prompt": total_p2p,
        "total_smart_workflow_runs": total_runs,
        "total_projects_completed": total_runs,
        "total_api_requests": total_requests,
        "total_api_requests_saved": total_saved,
        "est_api_cost": total_requests * ASSUMED_COST_PER_REQUEST,
        "est_api_cost_saved": total_saved * ASSUMED_COST_PER_REQUEST,
        "total_processing_seconds": total_seconds,
    }


def last_n_days_series(n=7):
    """Per-day totals for the last n days (including today), oldest
    first — one number per day per series, used for the Dashboard's
    7-day chart. Days with no activity show as 0, not omitted, so the
    chart's x-axis is always evenly spaced."""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT substr(ts,1,10) as day,kind,status,count FROM activity"
            ).fetchall()
        finally:
            conn.close()
    days = [(datetime.date.today() - datetime.timedelta(days=i)).isoformat()
            for i in range(n - 1, -1, -1)]
    series = {"files_processed": [0] * n, "metadata_generated": [0] * n,
              "prompts_generated": [0] * n, "embedded_images": [0] * n}
    day_index = {d: i for i, d in enumerate(days)}
    for day, kind, status, count in rows:
        if day not in day_index or status != "completed":
            continue
        i = day_index[day]
        if kind in ("metadata_generation", "smart_workflow_run"):
            series["files_processed"][i] += count
            series["metadata_generated"][i] += count
        elif kind == "embedding":
            series["files_processed"][i] += count
            series["embedded_images"][i] += count
        elif kind in ("prompt_generation", "prompt_to_prompt"):
            series["prompts_generated"][i] += count
    return days, series


def recent_activity(limit=8):
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT ts,kind,status,count,detail FROM activity "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
    return rows


def reset_lifetime():
    """Wipes every recorded event — exposed from Settings, per spec.
    Irreversible; the caller is responsible for confirming with the
    person first."""
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM activity")
            conn.commit()
        finally:
            conn.close()
