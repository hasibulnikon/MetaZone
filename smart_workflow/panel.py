"""Smart Workflow's UI panel. Lives inside ui/main_window.py's main
content area — raised over the ENTIRE Standard Workflow view (topbar,
upload zone, progress bar, card list) with tkraise()/lower() when Smart
Workflow mode is selected, so neither mode's widgets are ever destroyed
or rebuilt on switch. Files are imported the normal way (drag-and-drop
or Browse, in Standard mode) before switching to Smart Workflow to hand
that same file list — app._all_paths — to its own 7-stage pipeline.
"""
import os, threading
import customtkinter as ctk
from tkinter import messagebox, BooleanVar, StringVar
from ui.theme import (BG1,BG2,BG3,BG4,GLASS,GLASS_BDR,TXT,TXT2,TXT3,
    GRN,GRN_H,GRN_DIM,RED_BTN,RED_BTN_H,RED_DIM,AMB_BTN,AMB_DIM,ABSOLUTE_BG)
from core.config import save_prefs
from smart_workflow.pipeline import SmartWorkflowPipeline
from smart_workflow import state as state_mod

STAGE_LABELS = {
    "previews":     "1 · Preview Generation",
    "inspection":   "2 · AI Quality Inspection",
    "selection":    "3 · Image Selection",
    "generation":   "4 · Metadata Generation",
    "optimization": "5 · Metadata Optimization",
    "embedding":    "6 · Embedding",
    "organization": "7 · Organization & Cleanup",
}
STAGE_ORDER = list(STAGE_LABELS.keys())


class SmartWorkflowPanel(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=BG1, corner_radius=0)
        self.app = app
        self.pipeline = SmartWorkflowPipeline(app)
        self._wire_pipeline_callbacks()
        self._build()

    # ── build ───────────────────────────────────────────────────────
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(self, fg_color=BG2, height=52, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew"); hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="⚡  Smart Workflow", font=ctk.CTkFont("Segoe UI", 14, "bold"),
            text_color=TXT, fg_color=BG2).pack(side="left", padx=16, pady=13)
        ctk.CTkLabel(hdr, text="BETA", font=ctk.CTkFont("Segoe UI", 9, "bold"),
            fg_color=AMB_DIM, text_color=AMB_BTN, corner_radius=10, padx=8, pady=2
        ).pack(side="left")
        self._file_count_lbl = ctk.CTkLabel(hdr, text="0 files loaded",
            font=ctk.CTkFont("Segoe UI", 11), text_color=TXT3, fg_color=BG2)
        self._file_count_lbl.pack(side="right", padx=(0, 16))
        ctk.CTkButton(hdr, text="📁  Import Files", height=30, width=120,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), fg_color=BG3, hover_color=BG4,
            text_color=TXT2, corner_radius=8, command=self._on_import
        ).pack(side="right", padx=(0, 8))
        ctk.CTkButton(hdr, text="🗑  Clear", height=30, width=80,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), fg_color=RED_DIM, hover_color=RED_BTN_H,
            text_color=RED_BTN, corner_radius=8, command=self._on_clear
        ).pack(side="right", padx=(0, 8))

        af = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0)
        af.grid(row=1, column=0, sticky="ew")
        self._start_btn = ctk.CTkButton(af, text="▶  Start Smart Workflow", height=38,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), fg_color=GRN, hover_color=GRN_H,
            text_color=ABSOLUTE_BG, text_color_disabled=ABSOLUTE_BG, corner_radius=8,
            command=self._on_start)
        self._start_btn.pack(side="left", padx=(12, 6), pady=8)
        self._pause_btn = ctk.CTkButton(af, text="⏸  Pause", height=38, width=90,
            font=ctk.CTkFont("Segoe UI", 11, "bold"), fg_color=BG3, hover_color=BG4,
            text_color=TXT2, corner_radius=8, state="disabled", command=self._on_pause)
        self._pause_btn.pack(side="left", padx=(0, 6), pady=8)
        self._stop_btn = ctk.CTkButton(af, text="⏹  Stop", height=38, width=80,
            font=ctk.CTkFont("Segoe UI", 11, "bold"), fg_color=RED_DIM, hover_color=RED_BTN_H,
            text_color=RED_BTN, corner_radius=8, state="disabled", command=self._on_stop)
        self._stop_btn.pack(side="left", padx=(0, 6), pady=8)

        self._embed_var = BooleanVar(value=True)
        ef = ctk.CTkFrame(af, fg_color="transparent", corner_radius=0)
        ef.pack(side="right", padx=12)
        ctk.CTkLabel(ef, text="Auto-Embed", font=ctk.CTkFont("Segoe UI", 10),
            text_color=TXT2, fg_color="transparent").pack(side="left", padx=(0, 6))
        ctk.CTkSwitch(ef, text="", variable=self._embed_var, progress_color=GRN,
            button_color=TXT, fg_color=GLASS_BDR, width=40, height=20).pack(side="left")

        # Process All — ON: skip the manual selection step and continue
        # straight into metadata generation for Good + Needs Review the
        # moment Quality Inspection finishes. OFF (default): keep today's
        # manual "pick which files to work with" step at Stage 3.
        self._process_all_var = BooleanVar(value=False)
        pf = ctk.CTkFrame(af, fg_color="transparent", corner_radius=0)
        pf.pack(side="right", padx=12)
        ctk.CTkLabel(pf, text="Process All", font=ctk.CTkFont("Segoe UI", 10),
            text_color=TXT2, fg_color="transparent").pack(side="left", padx=(0, 6))
        ctk.CTkSwitch(pf, text="", variable=self._process_all_var, progress_color=GRN,
            button_color=TXT, fg_color=GLASS_BDR, width=40, height=20).pack(side="left")

        body = ctk.CTkScrollableFrame(self, fg_color=BG1, corner_radius=0,
            scrollbar_button_color=BG3)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        self._body = body

        # Stage progress list
        stages_wrap = ctk.CTkFrame(body, fg_color=GLASS, corner_radius=10,
            border_width=1, border_color=GLASS_BDR)
        stages_wrap.pack(fill="x", padx=16, pady=(16, 10))
        stages_wrap.grid_columnconfigure(1, weight=1)
        self._stage_rows = {}
        for i, key in enumerate(STAGE_ORDER):
            dot = ctk.CTkLabel(stages_wrap, text="○", font=ctk.CTkFont("Segoe UI", 13),
                text_color=TXT3, fg_color="transparent", width=20)
            dot.grid(row=i, column=0, padx=(14, 6), pady=6, sticky="w")
            lbl = ctk.CTkLabel(stages_wrap, text=STAGE_LABELS[key],
                font=ctk.CTkFont("Segoe UI", 11), text_color=TXT2, fg_color="transparent",
                anchor="w")
            lbl.grid(row=i, column=1, sticky="w", pady=6)
            status = ctk.CTkLabel(stages_wrap, text="", font=ctk.CTkFont("Segoe UI", 10),
                text_color=TXT3, fg_color="transparent")
            status.grid(row=i, column=2, sticky="e", padx=(6, 14), pady=6)
            self._stage_rows[key] = (dot, lbl, status)

        self._prog_bar = ctk.CTkProgressBar(body, progress_color=GRN, fg_color=BG3,
            border_width=1, border_color=GLASS_BDR, height=14, corner_radius=7)
        self._prog_bar.pack(fill="x", padx=16, pady=(0, 4)); self._prog_bar.set(0)
        self._prog_lbl = ctk.CTkLabel(body, text="Import some files, then press Start.",
            font=ctk.CTkFont("Segoe UI", 10), text_color=TXT3, fg_color="transparent")
        self._prog_lbl.pack(anchor="w", padx=16, pady=(0, 6))

        # Live run stats — imported / processing / good / bad — under the
        # progress bar, updated on every progress/stage callback.
        stats_row = ctk.CTkFrame(body, fg_color=GLASS, corner_radius=10,
            border_width=1, border_color=GLASS_BDR)
        stats_row.pack(fill="x", padx=16, pady=(0, 10))
        stats_row.grid_columnconfigure((0,1,2,3), weight=1)
        self._stat_lbls = {}
        for i,(key,label,color) in enumerate([
            ("imported","Imported",TXT2),("processing","Processing",AMB_BTN),
            ("good","Good",GRN),("bad","Bad",RED_BTN)]):
            cell = ctk.CTkFrame(stats_row, fg_color="transparent", corner_radius=0)
            cell.grid(row=0, column=i, sticky="ew", padx=10, pady=10)
            val = ctk.CTkLabel(cell, text="0", font=ctk.CTkFont("Segoe UI", 18, "bold"),
                text_color=color, fg_color="transparent")
            val.pack(anchor="center")
            ctk.CTkLabel(cell, text=label, font=ctk.CTkFont("Segoe UI", 9),
                text_color=TXT3, fg_color="transparent").pack(anchor="center")
            self._stat_lbls[key] = val

        # Stage 3 — selection (hidden until inspection finishes)
        self._sel_frame = ctk.CTkFrame(body, fg_color=GLASS, corner_radius=10,
            border_width=1, border_color=GLASS_BDR)
        self._sel_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self._sel_frame, text="IMAGE SELECTION", font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=GRN, fg_color="transparent").pack(anchor="w", padx=14, pady=(12, 2))
        self._sel_counts_lbl = ctk.CTkLabel(self._sel_frame, text="",
            font=ctk.CTkFont("Segoe UI", 11), text_color=TXT2, fg_color="transparent")
        self._sel_counts_lbl.pack(anchor="w", padx=14, pady=(0, 8))
        sel_btns = ctk.CTkFrame(self._sel_frame, fg_color="transparent", corner_radius=0)
        sel_btns.pack(fill="x", padx=14, pady=(0, 14))
        self._sel_btns_frame = sel_btns
        for text, mode in (("Good Only", "good"), ("Good + Needs Review", "good_review"),
                            ("All Images", "all")):
            ctk.CTkButton(sel_btns, text=text, height=32,
                font=ctk.CTkFont("Segoe UI", 10, "bold"), fg_color=BG3, hover_color=GRN_DIM,
                text_color=TXT2, corner_radius=8,
                command=lambda m=mode: self._on_selection_chosen(m)
            ).pack(side="left", padx=(0, 6), fill="x", expand=True)

        # End-of-run report (hidden until complete)
        self._report_frame = ctk.CTkFrame(body, fg_color=GLASS, corner_radius=10,
            border_width=1, border_color=GLASS_BDR)
        self._report_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self._report_frame, text="PROCESSING REPORT",
            font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=GRN,
            fg_color="transparent").pack(anchor="w", padx=14, pady=(12, 4))
        self._report_lbl = ctk.CTkLabel(self._report_frame, text="", justify="left",
            font=ctk.CTkFont("Segoe UI", 11), text_color=TXT2, fg_color="transparent")
        self._report_lbl.pack(anchor="w", padx=14, pady=(0, 12))
        self._report_path_lbl = ctk.CTkLabel(self._report_frame, text="",
            font=ctk.CTkFont("Segoe UI", 9), text_color=TXT3, fg_color="transparent")
        self._report_path_lbl.pack(anchor="w", padx=14, pady=(0, 12))

    def _update_stats(self, imported=None, processing=None, good=None, bad=None):
        vals = {"imported":imported,"processing":processing,"good":good,"bad":bad}
        for key,v in vals.items():
            if v is not None:
                self._stat_lbls[key].configure(text=str(v))

    # ── file-count refresh (called from main_window on import/clear) ──
    def refresh_file_count(self):
        n = len(self.app._all_paths)
        self._file_count_lbl.configure(text=f"{n} file{'s' if n!=1 else ''} loaded")
        self._update_stats(imported=n)

    def _on_import(self):
        self.app._browse_images()
        self.refresh_file_count()

    def _on_clear(self):
        if self.pipeline.stage and self.pipeline.stage != "complete":
            messagebox.showwarning("Smart Workflow running",
                "Stop the current run before clearing.", parent=self.app)
            return
        self.app._clear_all(confirm=True)
        self.refresh_file_count()

    def resume_from(self, resume_state, folder):
        """Entry point for main_window._check_smart_resume() — a person
        confirmed the 'Resume previous Smart Workflow?' prompt at
        startup. The files themselves were never touched by the
        interruption (Standard Workflow's own import list is separate
        and currently empty on a fresh launch), so this only needs to
        hand the pipeline its saved paths/state, not re-import anything."""
        paths = resume_state.get("paths", [])
        self._file_count_lbl.configure(text=f"{len(paths)} files (resumed)")
        for key in STAGE_ORDER:
            self._set_stage_visual(key, "pending")
        self._sel_frame.pack_forget(); self._report_frame.pack_forget()
        self._start_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal", text="⏸  Pause")
        self._stop_btn.configure(state="normal")
        self.app.prefs["last_smart_folder"] = folder
        save_prefs(self.app.prefs)
        self.pipeline.start(paths, folder, auto_embed=self._embed_var.get(),
                             process_all=self._process_all_var.get(),
                             resume_state=resume_state)

    # ── controls ────────────────────────────────────────────────────
    def _on_start(self):
        paths = list(self.app._all_paths)
        if not paths:
            messagebox.showinfo("No files", "Import some files first.", parent=self.app)
            return
        folder = self.app._source_folder or (os.path.dirname(paths[0]) if paths else "")
        if not folder:
            messagebox.showinfo("No folder", "Couldn't determine a folder for the results.", parent=self.app)
            return
        resumable = state_mod.find_resumable(folder)
        resume_state = None
        if resumable:
            if messagebox.askyesno("Resume previous Smart Workflow?",
                    "An unfinished Smart Workflow run was found for this folder. Resume it?",
                    parent=self.app):
                resume_state = resumable
        for key in STAGE_ORDER:
            self._set_stage_visual(key, "pending")
        self._sel_frame.pack_forget(); self._report_frame.pack_forget()
        self._start_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal", text="⏸  Pause")
        self._stop_btn.configure(state="normal")
        self.app.prefs["last_smart_folder"] = folder
        save_prefs(self.app.prefs)
        self.pipeline.start(paths, folder, auto_embed=self._embed_var.get(),
                             process_all=self._process_all_var.get(),
                             resume_state=resume_state)

    def _on_pause(self):
        paused = self.pipeline.toggle_pause()
        self._pause_btn.configure(text="▶  Resume" if paused else "⏸  Pause")

    def _on_stop(self):
        if not messagebox.askyesno("Stop Smart Workflow?",
                "Progress will be saved and you can resume later. Stop now?", parent=self.app):
            return
        self.pipeline.stop()

    def _on_selection_chosen(self, mode):
        self._sel_frame.pack_forget()
        self.pipeline.set_selection(mode)

    # ── pipeline callback wiring (marshalled onto the Tk main thread) ──
    def _wire_pipeline_callbacks(self):
        p = self.pipeline
        p.on_stage_change = lambda name: self.app.after(0, self._handle_stage_change, name)
        p.on_progress = lambda done, total, msg: self.app.after(0, self._handle_progress, done, total, msg)
        p.on_selection_ready = lambda counts: self.app.after(0, self._handle_selection_ready, counts)
        p.on_complete = lambda summary, report_path: self.app.after(0, self._handle_complete, summary, report_path)
        p.on_error = lambda msg: self.app.after(0, self._handle_error, msg)

    def _set_stage_visual(self, key, state):
        dot, lbl, status = self._stage_rows[key]
        if state == "pending":
            dot.configure(text="○", text_color=TXT3); status.configure(text="")
        elif state == "active":
            dot.configure(text="⟳", text_color=AMB_BTN); status.configure(text="running…", text_color=AMB_BTN)
        elif state == "done":
            dot.configure(text="✓", text_color=GRN); status.configure(text="done", text_color=GRN)

    def _handle_stage_change(self, name):
        if name == "complete":
            for key in STAGE_ORDER:
                self._set_stage_visual(key, "done")
            self._prog_lbl.configure(text="Complete.")
            self._pause_btn.configure(state="disabled")
            self._stop_btn.configure(state="disabled")
            self._start_btn.configure(state="normal", text="▶  Start Smart Workflow")
            return
        seen_current = False
        for key in STAGE_ORDER:
            if key == name:
                self._set_stage_visual(key, "active"); seen_current = True
            elif not seen_current:
                self._set_stage_visual(key, "done")
            else:
                self._set_stage_visual(key, "pending")
        self._prog_bar.set(0)
        self._prog_lbl.configure(text=STAGE_LABELS.get(name, name))

    def _handle_progress(self, done, total, msg):
        pct = (done / total) if total else 0
        self._prog_bar.set(pct)
        self._prog_lbl.configure(text=msg)
        self._update_stats(processing=max(total-done, 0))

    def _handle_selection_ready(self, counts):
        auto = self.pipeline.process_all
        note = "" if auto else "\nChoose which images continue to metadata generation:"
        prefix = "🤖 Process All is on — continuing automatically.\n" if auto else ""
        self._sel_counts_lbl.configure(
            text=f"{prefix}🟢 Good: {counts.get('good',0)}    🟡 Needs Review: {counts.get('review',0)}    "
                 f"🔴 Rejected: {counts.get('rejected',0)}{note}")
        self._sel_frame.pack(fill="x", padx=16, pady=(0, 10))
        self._sel_btns_frame.pack_forget() if auto else self._sel_btns_frame.pack(
            fill="x", padx=14, pady=(0, 14))
        self._update_stats(processing=0, good=counts.get("good",0),
            bad=counts.get("review",0)+counts.get("rejected",0))

    def _handle_complete(self, summary, report_path):
        lines = [
            f"Total: {summary['total']}   Processed: {summary['processed']}",
            f"Good: {summary['good']}   Needs Review: {summary['review']}   Rejected: {summary['rejected']}",
            f"Metadata generated: {summary['metadata_generated']}   Embedded: {summary['embedded']}",
            f"Average metadata score: {summary['avg_score']:.0f}%",
            f"Processing time: {summary['elapsed']}   Errors: {summary['errors']}",
        ]
        self._report_lbl.configure(text="\n".join(lines))
        self._report_path_lbl.configure(text=f"Full report saved to: {report_path}")
        self._report_frame.pack(fill="x", padx=16, pady=(0, 16))
        self._start_btn.configure(state="normal", text="▶  Start Smart Workflow")
        self._pause_btn.configure(state="disabled")
        self._stop_btn.configure(state="disabled")
        self._update_stats(processing=0, good=summary.get("good",0),
            bad=summary.get("review",0)+summary.get("rejected",0))
        self.refresh_file_count()

    def _handle_error(self, msg):
        self._prog_lbl.configure(text=msg, text_color=RED_BTN)
        self._start_btn.configure(state="normal", text="▶  Start Smart Workflow")
        self._pause_btn.configure(state="disabled")
        self._stop_btn.configure(state="disabled")
