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
from engine.prompt_generator import build_prompt_to_prompt_prompt, build_image_to_prompts_prompt
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


_PREAMBLE_RE = re.compile(
    r"^(here (are|is)|sure|certainly|okay|ok,|note:|these (are|prompts)|"
    r"below (are|is)|i've|i have|hope this)",
    re.IGNORECASE)


def _clean_prompts(prompts):
    """Drops lines that clearly aren't a real generated prompt: leading
    meta-commentary the model sometimes adds despite instructions not to
    ('Here are 10 variations:'), and fragments too short to be a usable
    image prompt (a genuine one is essentially never a single 2-3 word
    sentence — that shape is what a token-limit truncation cutting a
    batch off mid-list looks like)."""
    out = []
    for p in prompts:
        words = p.split()
        if len(words) < 4:
            continue
        if _PREAMBLE_RE.match(p.strip()):
            continue
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

    def start(self, original_prompt, count, creativity, style, source_image=None, target_words=None):
        """source_image=None -> the original text-to-prompts mode.
        source_image=<path> or <list of paths> -> Image to Prompt mode:
        original_prompt is ignored, the reference image(s) are sent
        through the vision-capable call path instead (see _run_batches).
        A list of up to 15 paths is analyzed together in one call, not
        as 15 separate calls."""
        self.stop_flag = False
        self.paused = False
        self.results = []
        self.errors = []
        self.running = True
        threading.Thread(target=self._run,
                          args=(original_prompt, count, creativity, style, source_image, target_words),
                          daemon=True).start()

    def _run_batches(self, original_prompt, sizes, creativity, style, collected, lock,
                      progress_base, progress_total, source_image=None, target_words=None):
        """Runs one wave of batches concurrently and returns once all of
        them are done. Each batch is given whatever's in `collected` at
        the moment IT starts (not a fixed snapshot from before the wave),
        so later batches in the same wave still benefit from earlier
        ones finishing first."""
        done_counter = [0]
        image_count = len(source_image) if isinstance(source_image, (list, tuple)) else (1 if source_image else 0)

        def worker(batch_n, i):
            self._wait_while_paused()
            if self.stop_flag:
                return
            with lock:
                avoid_snapshot = list(collected[-20:])
            if source_image:
                prompt = build_image_to_prompts_prompt(batch_n, creativity, style, avoid=avoid_snapshot,
                                                        target_words=target_words, image_count=image_count)
                call_path = source_image
            else:
                prompt = build_prompt_to_prompt_prompt(
                    original_prompt, batch_n, creativity, style, avoid=avoid_snapshot,
                    target_words=target_words)
                call_path = None
            try:
                raw, provider, model_id, key_idx = call_with_failover(
                    call_path, prompt, self.app.prefs, max_tokens=4000)
                self.app._last_ai_provider, self.app._last_ai_model = provider, model_id
                parsed = _clean_prompts(_parse_prompts(raw, batch_n))
                with lock:
                    collected.extend(parsed)
                    done_counter[0] += 1
            except Exception as e:
                with lock:
                    self.errors.append(str(e)[:150])
                    done_counter[0] += 1
            if self.on_progress:
                self.on_progress(progress_base + done_counter[0], progress_total,
                                  f"Generating… batch {progress_base + done_counter[0]}/{progress_total}")

        concurrency = max(1, min(6, int(getattr(self.app, "ai_concurrency_var", None)
                                          and self.app.ai_concurrency_var.get() or 3)))
        ev = threading.Event()
        self.app._task_mgr.run_batch(sizes, worker, max_workers=concurrency, on_all_done=ev.set)
        ev.wait()
        return done_counter[0]

    def _run(self, original_prompt, count, creativity, style, source_image=None, target_words=None):
        start_time = time.time()
        lock = threading.Lock()
        collected = []

        def make_batches(n):
            out = []
            while n > 0:
                b = min(BATCH_SIZE, n)
                out.append(b); n -= b
            return out

        batches = make_batches(count)
        total_batches_est = len(batches)  # for the progress label; may grow below
        self._run_batches(original_prompt, batches, creativity, style, collected, lock,
                           0, total_batches_est, source_image=source_image, target_words=target_words)

        # Concurrent batches inevitably start before earlier ones have
        # populated the avoid-list, so near-duplicate variations get
        # collapsed hard by dedupe() — worst at low creativity, where the
        # model is explicitly asked to stay close to the original wording.
        # Rather than accept an under-delivered result, top up the
        # shortfall with additional avoid-list-aware batches (now that
        # `collected` actually has content to avoid) — bounded so a
        # genuinely exhausted topic can't loop forever.
        self.results = dedupe(collected)
        extra_rounds = 0
        while len(self.results) < count and extra_rounds < 4 and not self.stop_flag:
            shortfall = count - len(self.results)
            topup_batches = make_batches(shortfall)
            total_batches_est += len(topup_batches)
            done_so_far = len(batches) + sum(1 for _ in range(extra_rounds))  # rough, label-only
            self._run_batches(original_prompt, topup_batches, creativity, style, collected, lock,
                               done_so_far, total_batches_est, source_image=source_image, target_words=target_words)
            self.results = dedupe(collected)
            extra_rounds += 1

        self.results = self.results[:count] if count else self.results
        seconds = time.time() - start_time
        if self.results:
            stats_db.record("prompt_to_prompt", "completed", count=len(self.results),
                             api_requests=total_batches_est, seconds=seconds,
                             detail=f"Prompts: {len(self.results)}"
                             + (" (from image)" if source_image else ""))
        if self.errors and not self.results:
            if self.on_error:
                self.on_error(f"All batches failed. Last error: {self.errors[-1]}")
            self.running = False
            return
        self.running = False
        if self.on_complete:
            self.on_complete(self.results)
