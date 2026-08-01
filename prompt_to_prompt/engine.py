"""Prompt-to-Prompt Generator engine.

Takes one existing prompt and generates N new variations inspired by it.
Text-only (no image), so this reuses call_with_failover with path=None
(see engine/ai_providers.py) — the same failover/key-rotation engine
Standard/Smart Workflow use, never a duplicate implementation.

Large counts are split into batches (one AI call each) run through the
app's existing bounded worker pool, both for real progress feedback and
so a single oversized request doesn't risk truncation/low quality.
"""
import re, threading, time

from engine.ai_providers import call_with_failover
from engine.prompt_generator import build_prompt_to_prompt_prompt
from core import stats_db

BATCH_SIZE = 10


def _parse_prompts(raw, expected):
    """One prompt per line in the ideal case; defensively strips leading
    numbering/bullets ('1.', '-', '•') in case the model didn't fully
    comply with the no-numbering instruction."""
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    out = []
    for l in lines:
        l = re.sub(r"^[\-\*\u2022]\s*", "", l)
        l = re.sub(r"^\d+[\.\)]\s*", "", l)
        if l:
            out.append(l)
    return out[:expected] if expected else out


def _normalize(p):
    return re.sub(r"[^a-z0-9 ]", "", p.lower()).strip()


def dedupe(prompts):
    """Exact + near-duplicate (normalized-text) removal — spec requires
    avoiding duplicates; this is the safety net on top of asking the AI
    not to repeat itself, since batches are generated independently and
    can't see each other's output."""
    seen, out = set(), []
    for p in prompts:
        key = _normalize(p)
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


class PromptToPromptEngine:
    def __init__(self, app):
        self.app = app
        self.stop_flag = False
        self.paused = False
        self.running = False
        self.results = []  # list of prompt strings, in order produced
        self.errors = []
        self.on_progress = None    # (done, total, msg)
        self.on_complete = None    # (prompts: list[str])
        self.on_error = None       # (message)

    def _wait_while_paused(self):
        while self.paused and not self.stop_flag:
            time.sleep(0.2)

    def stop(self):
        self.stop_flag = True

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused

    def start(self, original_prompt, count, creativity, style):
        self.stop_flag = False
        self.paused = False
        self.results = []
        self.errors = []
        self.running = True
        threading.Thread(target=self._run, args=(original_prompt, count, creativity, style),
                          daemon=True).start()

    def _run(self, original_prompt, count, creativity, style):
        start_time = time.time()
        batches = []
        remaining = count
        while remaining > 0:
            n = min(BATCH_SIZE, remaining)
            batches.append(n)
            remaining -= n

        lock = threading.Lock()
        done_batches = [0]
        total_batches = len(batches)
        collected = []

        def worker(batch_n, i):
            self._wait_while_paused()
            if self.stop_flag:
                return
            prompt = build_prompt_to_prompt_prompt(
                original_prompt, batch_n, creativity, style, avoid=collected[:20])
            try:
                raw, provider, model_id, key_idx = call_with_failover(None, prompt, self.app.prefs)
                self.app._last_ai_provider, self.app._last_ai_model = provider, model_id
                parsed = _parse_prompts(raw, batch_n)
                with lock:
                    collected.extend(parsed)
                    done_batches[0] += 1
            except Exception as e:
                with lock:
                    self.errors.append(str(e)[:150])
                    done_batches[0] += 1
            if self.on_progress:
                self.on_progress(done_batches[0], total_batches,
                                  f"Generating… batch {done_batches[0]}/{total_batches}")

        ev = threading.Event()
        concurrency = max(1, min(6, int(getattr(self.app, "ai_concurrency_var", None)
                                          and self.app.ai_concurrency_var.get() or 3)))
        self.app._task_mgr.run_batch(batches, worker, max_workers=concurrency, on_all_done=ev.set)
        ev.wait()

        self.results = dedupe(collected)
        seconds = time.time() - start_time
        if self.results:
            stats_db.record("prompt_to_prompt", "completed", count=len(self.results),
                             api_requests=total_batches, seconds=seconds,
                             detail=f"Prompts: {len(self.results)}")
        if self.errors and not self.results:
            if self.on_error:
                self.on_error(f"All batches failed. Last error: {self.errors[-1]}")
            self.running = False
            return
        self.running = False
        if self.on_complete:
            self.on_complete(self.results)
