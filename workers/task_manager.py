"""Bounded worker pool for background batch work.

This wraps concurrent.futures.ThreadPoolExecutor instead of spawning a
raw threading.Thread per item guarded by a manual Semaphore (which is
what the AI generation batch used to do). It's a mechanical swap, not
a behavior change: pause/stop/retry/progress are still owned by the
caller (see ui/main_window.py's ai_stop_flag / _ai_paused / _gen_epoch)
— this class only owns "how many workers run at once" and "tell me
when everything submitted has finished."

Worker counts are configurable via max_workers, and the pool is always
bounded — never spawns unlimited threads.
"""
import threading
from concurrent.futures import ThreadPoolExecutor


class TaskManager:
    def __init__(self):
        self._executor = None
        self._lock = threading.Lock()

    def run_batch(self, items, worker_fn, max_workers, on_all_done=None):
        """Submit worker_fn(item, index) for every item to a bounded pool.
        Returns immediately (non-blocking). worker_fn is responsible for
        checking its own abort/pause conditions and for catching its own
        per-item exceptions — exactly like before — so one bad item can
        never take down the batch.

        on_all_done(), if given, fires on a background thread once every
        submitted task has finished (success or failure)."""
        with self._lock:
            executor = ThreadPoolExecutor(max_workers=max(1, max_workers))
            self._executor = executor
        futures = [executor.submit(worker_fn, item, i) for i, item in enumerate(items)]

        def _watch():
            for f in futures:
                try:
                    f.result()
                except Exception:
                    # worker_fn already isolates its own per-item errors;
                    # this is just a safety net so a stray exception can
                    # never kill the watcher thread.
                    pass
            executor.shutdown(wait=False)
            with self._lock:
                if self._executor is executor:
                    self._executor = None
            if on_all_done:
                on_all_done()

        threading.Thread(target=_watch, daemon=True).start()

    def shutdown(self):
        """Best-effort immediate shutdown, e.g. on app close."""
        with self._lock:
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
