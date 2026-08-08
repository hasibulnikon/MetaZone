"""Smart Workflow orchestrator.

Runs the 7-stage pipeline (preview -> inspect -> select -> generate ->
optimize -> embed -> organize) as its own background thread. This module
never touches ui/main_window.py's Standard Workflow state — it reuses
the existing engine (prompt building, parsing, AI failover) and the
existing embedding primitive, but owns its own results/classification
dicts entirely separately.

UI (smart_workflow/panel.py) drives this via callbacks; it never reaches
into pipeline internals beyond the documented callback contract below.
"""
import os, time, threading, shutil, csv as csv_mod

from core.utils import find_exiftool, embed_metadata_one
from engine.ai_providers import call_with_failover
from engine.prompt_generator import build_meta_prompt
from engine.parser import (parse_meta, sanitize_text_punctuation,
    sanitize_keywords_punctuation, enforce_single_keywords,
    _strip_copyright_keywords, smart_trim, dedupe_content_phrase)
from core.constants import CONTENT_SUFFIXES, VECTOR_EXTS, VIDEO_EXTS
from core import stats_db

from smart_workflow.preview import cache_dir_for, generate_previews, cleanup_cache
from smart_workflow.inspector import inspect_one
from smart_workflow import state as state_mod
from smart_workflow.report import write_report

# Words that make metadata unsafe/unusable for stock submission — used by
# Stage 5's quality check (separate purpose from Stage 2's copyright-image
# detection, which looks at the picture itself, not the generated text).
_FLAG_WORDS = {"trademark", "copyright", "©", "®", "™", "disney", "marvel",
               "nike", "apple", "coca-cola", "coca cola"}


class SmartWorkflowPipeline:
    def __init__(self, app):
        self.app = app
        self.stop_flag = False
        self.paused = False
        self.stage = None
        self.paths = []
        self.folder = ""
        self.cache_dir = None
        self.previews = {}
        self.classifications = {}
        self.selection_mode = "good_review"   # good | good_review | all
        self.process_all = False   # True = auto-continue past selection with good_review, no manual step
        self.auto_embed = True
        self.results = {}
        self.scores = {}
        self.errors = []
        self.providers_used = set()
        self._selection_event = threading.Event()
        self._start_time = None

        # UI callbacks — all optional, all called via app.after() by the
        # caller-side wrapper in panel.py so they're safe to touch widgets.
        self.on_stage_change = None      # (stage_name)
        self.on_progress = None          # (done, total, msg)
        self.on_selection_ready = None   # (counts_dict)
        self.on_complete = None          # (summary_dict, report_path)
        self.on_error = None             # (message)

    # ── control ─────────────────────────────────────────────────────
    def start(self, paths, folder, selection_mode="good_review", auto_embed=True,
              process_all=False, resume_state=None):
        self.paths = list(paths)
        self.folder = folder
        self.selection_mode = selection_mode
        self.auto_embed = auto_embed
        self.process_all = process_all
        self.stop_flag = False
        self.paused = False
        self._selection_event.clear()
        self.cache_dir = cache_dir_for(folder)
        threading.Thread(target=self._run, args=(resume_state,), daemon=True).start()

    def stop(self):
        self.stop_flag = True
        self._selection_event.set()  # unblock stage-3 wait if stopped there

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused

    def set_selection(self, mode):
        """Called by the UI once the person picks Good Only / Good+Review /
        All at Stage 3 — unblocks the pipeline thread to continue."""
        self.selection_mode = mode
        self._selection_event.set()

    def _wait_while_paused(self):
        while self.paused and not self.stop_flag:
            time.sleep(0.2)

    def _emit_stage(self, name):
        self.stage = name
        if self.on_stage_change:
            self.on_stage_change(name)

    def _save(self, resume_at=None):
        """Checkpoint after a stage finishes. `resume_at` is the NEXT stage
        to run on resume — NOT the one that just completed — otherwise a
        resume would redundantly re-run (and, for Stages 2/4, re-bill API
        calls for) a stage that already finished successfully. Leave
        `resume_at` unset only from _finish_stopped(), where the CURRENT
        (interrupted, not-yet-finished) stage is genuinely what should be
        retried."""
        state_mod.save_state(self.cache_dir, {
            "stage": resume_at or self.stage,
            "folder": self.folder,
            "paths": self.paths,
            "previews": self.previews,
            "classifications": self.classifications,
            "selection_mode": self.selection_mode,
            "process_all": self.process_all,
            "auto_embed": self.auto_embed,
            "results": self.results,
            "scores": self.scores,
        })

    # ── main run loop ───────────────────────────────────────────────
    def _run(self, resume_state=None):
        self._start_time = time.time()
        try:
            if resume_state:
                self.previews = resume_state.get("previews", {})
                self.classifications = resume_state.get("classifications", {})
                self.selection_mode = resume_state.get("selection_mode", self.selection_mode)
                self.process_all = resume_state.get("process_all", self.process_all)
                self.auto_embed = resume_state.get("auto_embed", self.auto_embed)
                self.results = resume_state.get("results", {})
                self.scores = resume_state.get("scores", {})
                resume_stage = resume_state.get("stage")
            else:
                resume_stage = None

            order = state_mod.STAGES
            start_idx = order.index(resume_stage) if resume_stage in order else 0

            if start_idx <= 0:
                self._stage_previews()
                if self.stop_flag: return self._finish_stopped()
            if start_idx <= 1:
                self._stage_inspect()
                if self.stop_flag: return self._finish_stopped()
            if start_idx <= 2:
                self._stage_select()
                if self.stop_flag: return self._finish_stopped()
            if start_idx <= 3:
                self._stage_generate()
                if self.stop_flag: return self._finish_stopped()
            if start_idx <= 4:
                self._stage_optimize()
                if self.stop_flag: return self._finish_stopped()
            if start_idx <= 5:
                self._stage_embed()
                if self.stop_flag: return self._finish_stopped()
            self._stage_organize()
        except Exception as e:
            if self.on_error:
                self.on_error(str(e))

    def _finish_stopped(self):
        self._save()
        if self.on_error:
            self.on_error("Stopped — progress saved, you can resume later.")

    # ── Stage 1 ─────────────────────────────────────────────────────
    def _stage_previews(self):
        self._emit_stage("previews")

        def prog(done, total):
            if self.on_progress:
                self.on_progress(done, total, f"Building previews… {done}/{total}")

        self.previews = generate_previews(
            self.paths, self.cache_dir, self.app._task_mgr,
            max_workers=6, on_progress=prog, stop_flag=lambda: self.stop_flag)
        self._save(resume_at="inspection")

    # ── Stage 2 ─────────────────────────────────────────────────────
    def _stage_inspect(self):
        self._emit_stage("inspection")
        total = len(self.paths)
        done = [0]
        lock = threading.Lock()

        def worker(path, i):
            self._wait_while_paused()
            if self.stop_flag: return
            preview = self.previews.get(path, path)
            try:
                label, conf, issues = inspect_one(preview, self.app.prefs)
            except Exception as e:
                # An inspection failure never blocks the batch — classify
                # conservatively as "review" so a person still sees it.
                label, conf, issues = "review", 0, f"inspection failed: {str(e)[:80]}"
            with lock:
                self.classifications[path] = {"label": label, "confidence": conf, "issues": issues}
                done[0] += 1
            if self.on_progress:
                self.on_progress(done[0], total, f"Inspecting… {done[0]}/{total}")

        ev = threading.Event()
        self.app._task_mgr.run_batch(self.paths, worker, max_workers=4, on_all_done=ev.set)
        ev.wait()
        self._save(resume_at="selection")

    # ── Stage 3 ─────────────────────────────────────────────────────
    def _stage_select(self):
        self._emit_stage("selection")
        counts = {"good": 0, "review": 0, "rejected": 0}
        for c in self.classifications.values():
            counts[c["label"]] = counts.get(c["label"], 0) + 1
        if self.on_selection_ready:
            if self.process_all:
                # Auto-continue on Good + Needs Review — no manual step,
                # but still tell the UI what was found so the numbers show.
                self.selection_mode = "good_review"
                self.on_selection_ready(counts)
                if not self.stop_flag:
                    self._save(resume_at="generation")
                return
            self.on_selection_ready(counts)
            self._selection_event.wait()  # blocks until UI calls set_selection()
            if not self.stop_flag:
                # Selection was actually made — safe to skip straight to
                # generation on a resume from here on.
                self._save(resume_at="generation")

    def _selected_paths(self):
        mode = self.selection_mode
        if mode == "all":
            allowed = {"good", "review", "rejected"}
        elif mode == "good_review":
            allowed = {"good", "review"}
        else:
            allowed = {"good"}
        return [p for p in self.paths if self.classifications.get(p, {}).get("label", "good") in allowed]

    # ── Stage 4 ─────────────────────────────────────────────────────
    def _stage_generate(self):
        self._emit_stage("generation")
        app = self.app
        targets = self._selected_paths()
        custom = app.ai_custom_var.get()
        single_kw = app.ai_single_kw_var.get()
        avoid_copyright = app.ai_avoid_copy_var.get()
        prefix = app.ai_prefix_text_var.get().strip() if app.ai_prefix_on_var.get() else ""
        suffix_title = app.ai_suffix_text_var.get().strip() if app.ai_suffix_on_var.get() else ""
        include_desc = app.ai_include_desc_var.get()
        tc = int(app.ai_title_var.get() or 130)
        dc = int(app.ai_desc_var.get() or 200)
        kn = min(int(app.ai_kw_var.get() or 49), 49)
        content_phrase = CONTENT_SUFFIXES.get(app.ai_content_type_var.get(), "")
        concurrency = max(1, min(10, int(app.ai_concurrency_var.get())))

        prompt = build_meta_prompt(tc, dc, kn, custom, single_kw, "", prefix, suffix_title,
                                    avoid_copyright, include_desc, content_phrase)

        total = len(targets)
        done = [0]
        lock = threading.Lock()

        def worker(path, i):
            self._wait_while_paused()
            if self.stop_flag: return
            # Stage 4 sends the PREVIEW, never the original, to the AI.
            ext = os.path.splitext(path)[1].lower()
            send_path = self.previews.get(path, path)
            try:
                if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
                    raise ValueError("Vector/video: convert to JPG first")
                raw, provider, model_id, key_idx = call_with_failover(send_path, prompt, app.prefs)
                self.providers_used.add(provider)
                title, desc, kw = parse_meta(raw)
                if not include_desc:
                    desc = ""
                title = sanitize_text_punctuation(title)
                if desc: desc = sanitize_text_punctuation(desc)
                kw = sanitize_keywords_punctuation(kw)
                if prefix and not title.lower().startswith(prefix.lower()):
                    title = prefix + " " + title
                if suffix_title and not title.lower().endswith(suffix_title.lower()):
                    title = title + " " + suffix_title
                if content_phrase:
                    title = dedupe_content_phrase(title, content_phrase)
                if len(title) > tc:
                    title = smart_trim(title, tc, must_include=content_phrase or None)
                if single_kw: kw = enforce_single_keywords(kw)
                if avoid_copyright: kw = _strip_copyright_keywords(kw)
                kw_list = [k.strip() for k in kw.split(",") if k.strip()]
                seen, deduped = set(), []
                for k in kw_list:
                    lk = k.lower()
                    if lk not in seen:
                        seen.add(lk); deduped.append(k)
                kw = ", ".join(deduped[:kn])
                with lock:
                    self.results[path] = {"title": title, "desc": desc, "kw": kw, "status": "done"}
                    done[0] += 1
            except Exception as e:
                with lock:
                    self.results[path] = {"status": "failed", "error": str(e)[:120]}
                    self.errors.append((os.path.basename(path), str(e)[:120]))
                    done[0] += 1
            if self.on_progress:
                self.on_progress(done[0], total, f"Generating metadata… {done[0]}/{total}")

        ev = threading.Event()
        app._task_mgr.run_batch(targets, worker, max_workers=concurrency, on_all_done=ev.set)
        ev.wait()
        self._save(resume_at="optimization")

    # ── Stage 5 ─────────────────────────────────────────────────────
    def _reorder_keywords_by_relevance(self, title, kw_list):
        """Spec's Stage 5 calls for checking/fixing 'Keyword ordering' and
        'Keyword importance' — Stage 4's prompt already asks the AI for
        most-relevant-first order, but that's a soft instruction with no
        code-side guarantee. This re-ranks keywords so ones that actually
        appear in the title (the clearest signal of "this is the central
        subject") move to the front. It's a STABLE sort, so within each
        relevance tier the AI's own original relative order is kept —
        this nudges the most on-topic terms up without discarding the
        AI's broader ordering, fabricating nothing, and costing no extra
        API calls."""
        title_words = set(w for w in title.lower().split() if len(w) > 2)
        title_lower = title.lower()

        def relevance(kw):
            kwl = kw.lower()
            if kwl in title_lower:
                return 2  # the whole keyword phrase is in the title
            if any(w in title_words for w in kwl.split()):
                return 1  # at least one word of it is
            return 0

        return sorted(kw_list, key=relevance, reverse=True)

    def _stage_optimize(self):
        self._emit_stage("optimization")
        app = self.app
        kn = min(int(app.ai_kw_var.get() or 49), 49)
        for path, r in self.results.items():
            if r.get("status") != "done":
                continue
            score = 100
            title, desc, kw = r.get("title", ""), r.get("desc", ""), r.get("kw", "")
            kw_list = [k.strip() for k in kw.split(",") if k.strip()]
            if title and kw_list:
                kw_list = self._reorder_keywords_by_relevance(title, kw_list)
                r["kw"] = ", ".join(kw_list)
                kw = r["kw"]
            if not title:
                score -= 30
            if not kw_list:
                score -= 30
            elif kn and len(kw_list) < kn * 0.7:
                score -= 15
            if app.ai_include_desc_var.get() and not desc:
                score -= 10
            lowered = f"{title} {desc} {kw}".lower()
            if any(w in lowered for w in _FLAG_WORDS):
                score -= 20
            if any(ch * 3 in title for ch in ".,-!?"):
                score -= 5
            self.scores[path] = max(0, score)
        self._save(resume_at="embedding")

    # ── Stage 6 ─────────────────────────────────────────────────────
    def _stage_embed(self):
        self._emit_stage("embedding")
        if not self.auto_embed:
            self._save(resume_at="organization")
            return
        et = find_exiftool()
        if not et:
            self.errors.append(("(all files)", "ExifTool not found — CSV was still saved"))
            self._save(resume_at="organization")
            return
        targets = [p for p, r in self.results.items() if r.get("status") == "done"]
        total = len(targets)
        done = [0]
        lock = threading.Lock()

        def worker(path, i):
            self._wait_while_paused()
            if self.stop_flag: return
            r = self.results[path]
            ok, msg, final_path = embed_metadata_one(et, path, r.get("title", ""), r.get("kw", ""), r.get("desc", ""))
            with lock:
                r["embedded"] = ok
                if final_path != path:
                    r["final_path"] = final_path  # extension mismatch was auto-corrected; original path no longer exists
                if not ok:
                    self.errors.append((os.path.basename(path), msg))
                done[0] += 1
            if self.on_progress:
                self.on_progress(done[0], total, f"Embedding… {done[0]}/{total}")

        ev = threading.Event()
        self.app._task_mgr.run_batch(targets, worker, max_workers=6, on_all_done=ev.set)
        ev.wait()
        self._save(resume_at="organization")

    # ── Stage 7 ─────────────────────────────────────────────────────
    def _stage_organize(self):
        self._emit_stage("organization")
        folders = {name: os.path.join(self.folder, name)
                   for name in ("Ready Upload", "Needs Review", "Rejected", "CSV", "Logs")}
        for d in folders.values():
            os.makedirs(d, exist_ok=True)

        dest_for_label = {"good": "Ready Upload", "review": "Needs Review", "rejected": "Rejected"}
        rows = []
        for path in self.paths:
            label = self.classifications.get(path, {}).get("label", "good")
            r = self.results.get(path, {})
            dest_name = dest_for_label.get(label, "Ready Upload")
            dest_dir = folders[dest_name]
            try:
                if os.path.exists(path):
                    shutil.move(path, os.path.join(dest_dir, os.path.basename(path)))
            except Exception as e:
                self.errors.append((os.path.basename(path), f"move failed: {str(e)[:80]}"))
            rows.append({
                "Filename": os.path.basename(path),
                "Title": r.get("title", ""),
                "Description": r.get("desc", ""),
                "Keywords": r.get("kw", ""),
                "Classification": label,
                "Metadata Score": self.scores.get(path, ""),
                "Embedded": r.get("embedded", ""),
            })

        csv_path = os.path.join(folders["CSV"], "smart_workflow_metadata.csv")
        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.DictWriter(f, fieldnames=["Filename", "Title", "Description",
                                                       "Keywords", "Classification",
                                                       "Metadata Score", "Embedded"])
                w.writeheader()
                w.writerows(rows)
        except Exception as e:
            self.errors.append(("CSV", str(e)[:120]))

        summary = self._build_summary()
        report_path = write_report(self.folder, summary)

        cleanup_cache(self.cache_dir)
        self._emit_stage("complete")

        stats_db.record("smart_workflow_run", "completed",
            count=summary["metadata_generated"],
            meta_score=summary["avg_score"] if summary["metadata_generated"] else None,
            api_requests=summary["api_requests"], api_requests_saved=summary["api_requests_saved"],
            seconds=summary["elapsed_seconds"],
            detail=f"Project: {os.path.basename(self.folder.rstrip(os.sep)) or self.folder}")
        if summary["embedded"] > 0:
            stats_db.record("embedding", "completed", count=summary["embedded"],
                             detail="Smart Workflow")

        if self.on_complete:
            self.on_complete(summary, report_path)

    def _build_summary(self):
        counts = {"good": 0, "review": 0, "rejected": 0}
        for c in self.classifications.values():
            counts[c["label"]] = counts.get(c["label"], 0) + 1
        generated = sum(1 for r in self.results.values() if r.get("status") == "done")
        embedded = sum(1 for r in self.results.values() if r.get("embedded"))
        scores = list(self.scores.values())
        avg_score = (sum(scores) / len(scores)) if scores else 0
        elapsed_s = time.time() - self._start_time if self._start_time else 0
        m, s = divmod(int(elapsed_s), 60)
        selected = len(self._selected_paths())
        # "Saved" = generation calls avoided because Stage 2/3 filtered
        # an image out before Stage 4 ever sent it to the AI.
        requests_saved = max(len(self.paths) - selected, 0)
        requests = len(self.paths) + selected  # inspection calls + generation calls
        return {
            "total": len(self.paths),
            "processed": len(self.results),
            "good": counts["good"], "review": counts["review"], "rejected": counts["rejected"],
            "metadata_generated": generated,
            "embedded": embedded,
            "avg_score": avg_score,
            "elapsed": f"{m}m {s}s",
            "elapsed_seconds": elapsed_s,
            "api_requests": requests,
            "api_requests_saved": requests_saved,
            "providers": ", ".join(sorted(self.providers_used)) or "—",
            "errors": len(self.errors),
            "error_list": self.errors,
        }
