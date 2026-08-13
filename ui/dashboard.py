"""The Dashboard workspace — Meta Zone's landing page.

Every number here comes from core.stats_db (persisted, real events) or
from live app state (current queue/running status, CPU/RAM). Nothing is
hardcoded or simulated — an app that's never run a batch shows zeros,
not sample data.
"""
import os, tkinter
import customtkinter as ctk
from ui.theme import (BG1,BG2,BG3,BG4,GLASS,GLASS_BDR,TXT,TXT2,TXT3,
    GRN,GRN_H,GRN_DIM,RED_BTN,RED_DIM,AMB_BTN,AMB_DIM,CYAN,ABSOLUTE_BG)
from core import stats_db
from engine.ai_providers import get_active_keys

# psutil / CPU+RAM tracking removed per v0.7.2 request — it was showing
# N/A for this person regardless of the priming/diagnostics added in
# v0.7.1, so rather than keep two rows that reliably show nothing useful,
# they're gone. (See CHANGELOG for what was tried.)

CHART_COLORS = {
    "files_processed": "#3b82f6",
    "metadata_generated": GRN,
    "prompts_generated": "#a855f7",
    "embedded_images": AMB_BTN,
}
CHART_LABELS = {
    "files_processed": "Files Processed",
    "metadata_generated": "Metadata Generated",
    "prompts_generated": "Prompts Generated",
    "embedded_images": "Embedded Images",
}


def _stat_card(parent, icon, value, label, sub, color, corner=False):
    card = ctk.CTkFrame(parent, fg_color=GLASS, corner_radius=10,
        border_width=1, border_color=GLASS_BDR)
    # Non-interactive by design — nothing in here is clickable, so give it
    # the plain arrow cursor rather than anything implying interaction.
    card.configure(cursor="arrow")
    icon_lbl = ctk.CTkLabel(card, text=icon, font=ctk.CTkFont("Segoe UI", 17),
        fg_color=color, text_color=TXT, corner_radius=8, width=34, height=34)
    icon_lbl.pack(anchor="w", padx=14, pady=(14, 8))
    if corner:
        # Idle/Empty-style status text sits in the top-right corner of the
        # card instead of floating as a caption underneath the value.
        corner_lbl = ctk.CTkLabel(card, text=sub, font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=TXT3, fg_color=GLASS, anchor="e")
        corner_lbl.place(relx=1.0, x=-12, y=14, anchor="ne")
        card._corner_lbl = corner_lbl
    else:
        card._corner_lbl = None
    ctk.CTkLabel(card, text=value, font=ctk.CTkFont("Segoe UI", 23, "bold"),
        text_color=TXT, fg_color=GLASS, anchor="w").pack(anchor="w", padx=14)
    ctk.CTkLabel(card, text=label, font=ctk.CTkFont("Segoe UI", 12),
        text_color=TXT2, fg_color=GLASS, anchor="w").pack(anchor="w", padx=14, pady=(0, 4))
    if not corner:
        if sub:
            ctk.CTkLabel(card, text=sub, font=ctk.CTkFont("Segoe UI", 10),
                text_color=TXT3, fg_color=GLASS, anchor="w").pack(anchor="w", padx=14, pady=(0, 12))
        else:
            ctk.CTkLabel(card, text="", fg_color=GLASS, height=8).pack()
    else:
        ctk.CTkLabel(card, text="", fg_color=GLASS, height=8).pack()
    return card


def _kv_row(parent, label, value, row, value_color=None):
    # height=20 explicit: CTkLabel silently defaults to height=28
    # regardless of font size (same thing that made the compact cards
    # waste vertical space) -- every kv_row in every dashboard panel was
    # quietly a third taller than its 12pt text actually needed.
    ctk.CTkLabel(parent, text=label, font=ctk.CTkFont("Segoe UI", 12),
        text_color=TXT3, fg_color="transparent", anchor="w", height=20
    ).grid(row=row + 1, column=0, sticky="w", padx=14, pady=2)
    lbl = ctk.CTkLabel(parent, text=value, font=ctk.CTkFont("Segoe UI", 12, "bold"),
        text_color=value_color or TXT, fg_color="transparent", anchor="e", height=20)
    lbl.grid(row=row + 1, column=1, sticky="e", padx=14, pady=2)
    return lbl


def _section(parent, title, icon=""):
    box = ctk.CTkFrame(parent, fg_color=GLASS, corner_radius=10,
        border_width=1, border_color=GLASS_BDR)
    box.grid_columnconfigure(0, weight=1)
    box.grid_columnconfigure(1, weight=0)
    hdr = f"{icon}  {title}" if icon else title
    ctk.CTkLabel(box, text=hdr, font=ctk.CTkFont("Segoe UI", 13, "bold"),
        text_color=TXT, fg_color=GLASS, anchor="w"
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(8, 4))
    return box


class DashboardPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=BG1, corner_radius=0)
        self.app = app
        self._build()
        self.after(1500, self._auto_refresh)

    # ── build ───────────────────────────────────────────────────────
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 10))
        ctk.CTkLabel(hdr, text="Dashboard", font=ctk.CTkFont("Segoe UI", 23, "bold"),
            text_color=TXT, fg_color=BG1).pack(anchor="w")
        ctk.CTkLabel(hdr, text="Overview of your productivity and system status",
            font=ctk.CTkFont("Segoe UI", 12), text_color=TXT3, fg_color=BG1
        ).pack(anchor="w")

        body = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        body.grid_columnconfigure(0, weight=1)
        # Row 4 (the Activity chart, added below) is the only row that
        # stretches — everything above it is sized to its own content, so
        # in a maximized/large window the chart grows to fill whatever's
        # left instead of the page just leaving dead space underneath it.
        # This used to be a CTkScrollableFrame with everything pack()ed
        # top-down, which sized itself to its content and left any extra
        # window height as visible empty space below — the opposite of
        # what a percentage/window-relative layout should do.
        body.grid_rowconfigure(4, weight=1, minsize=130)
        self._body = body

        # Today's Statistics
        ctk.CTkLabel(body, text="Today's Statistics", font=ctk.CTkFont("Segoe UI", 14, "bold"),
            text_color=TXT, fg_color=BG1, anchor="w").grid(row=0, column=0, sticky="w", padx=4, pady=(4, 8))
        today_row = ctk.CTkFrame(body, fg_color=BG1, corner_radius=0)
        today_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for i in range(6): today_row.grid_columnconfigure(i, weight=1, uniform="today")
        self._today_cards = {}
        specs = [("processed", "📁", "Files Processed", "#2563eb"),
                 ("completed", "✓", "Completed", GRN),
                 ("failed", "✕", "Failed", RED_BTN),
                 ("running", "⟳", "Running Tasks", AMB_BTN),
                 ("queue", "📋", "In Queue", "#8b5cf6"),
                 ("score", "★", "Avg. Metadata Score", CYAN)]
        for i, (key, icon, label, color) in enumerate(specs):
            corner = key in ("running", "queue")
            card = _stat_card(today_row, icon, "0", label, "", color, corner=corner)
            card.grid(row=0, column=i, sticky="nsew", padx=4)
            self._today_cards[key] = card

        # Lifetime + AI Usage + Productivity + System Status (row of 4)
        row2 = ctk.CTkFrame(body, fg_color=BG1, corner_radius=0)
        row2.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for i in range(4): row2.grid_columnconfigure(i, weight=1, uniform="row2")

        self._lifetime_box = _section(row2, "Lifetime Statistics")
        self._lifetime_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._lifetime_rows = {}
        life_items = [("total_files_processed", "Files Processed"),
                      ("total_metadata_generated", "Metadata Generated"),
                      ("total_embedded", "Embedded Images"),
                      ("total_prompt_generations", "Prompt Generations"),
                      ("total_prompt_to_prompt", "Prompt-to-Prompt"),
                      ("total_smart_workflow_runs", "Smart Workflow Runs"),
                      ("total_projects_completed", "Projects Completed")]
        for i, (key, label) in enumerate(life_items):
            self._lifetime_rows[key] = _kv_row(self._lifetime_box, label, "0", i)

        self._ai_box = _section(row2, "AI Usage", "🤖")
        self._ai_box.grid(row=0, column=1, sticky="nsew", padx=6)
        self._ai_rows = {}
        # "Est. Cost" replaced with a daily-capacity estimate: this app is
        # free-providers-only, so a dollar figure here was never the
        # right number to show anyone. "Capacity Left Today" instead
        # estimates how many more images the CURRENTLY stored active keys
        # could process before hitting their daily request limit —
        # active_keys × daily_limit_per_key, minus requests already made
        # today. daily_limit_per_key is a per-key editable setting (see
        # the small ✎ next to the row below), not a hardcoded number:
        # free-tier daily limits vary by provider AND by model within a
        # provider, and change over time at the provider's discretion, so
        # guessing a single number and presenting it as fact would
        # eventually just be quietly wrong for someone.
        ai_items = [("provider", "Current Provider"), ("model", "Current Model"),
                    ("requests", "API Requests"), ("requests_saved", "API Requests Saved"),
                    ("capacity", "Est. Capacity Left Today"), ("used_today", "Used Today")]
        for i, (key, label) in enumerate(ai_items):
            self._ai_rows[key] = _kv_row(self._ai_box, label, "—", i)
        edit_row = ctk.CTkFrame(self._ai_box, fg_color="transparent")
        edit_row.grid(row=len(ai_items) + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(2, 5))
        ctk.CTkLabel(edit_row, text="Daily limit per key (adjust to match your provider):",
            font=ctk.CTkFont("Segoe UI", 9), text_color=TXT3, fg_color="transparent"
            ).pack(side="left")
        self._daily_limit_entry = ctk.CTkEntry(edit_row, width=48, height=20,
            font=ctk.CTkFont("Segoe UI", 9), fg_color=BG3, border_width=0, corner_radius=4)
        self._daily_limit_entry.insert(0, str(getattr(self.app, "prefs", {}).get("ai_daily_limit_per_key", 250)))
        self._daily_limit_entry.pack(side="right")
        self._daily_limit_entry.bind("<FocusOut>", self._on_daily_limit_changed)
        self._daily_limit_entry.bind("<Return>", self._on_daily_limit_changed)

        # Quick Actions sits in columns 2-3 of row2, as before.
        qa_box = ctk.CTkFrame(row2, fg_color="transparent", corner_radius=0)
        qa_box.grid(row=0, column=2, columnspan=2, sticky="nsew", padx=(6, 0))
        qa_box.grid_columnconfigure((0, 1), weight=1)
        for r in range(3): qa_box.grid_rowconfigure(r, weight=1)
        actions = [
            ("🚀  Smart Workflow", "#7c3aed", lambda: self.app._nav_to("smart")),
            ("📝  Meta Generator", "#2563eb", lambda: self.app._nav_to("metadata_gen")),
            ("📦  Meta Embedder", GRN, lambda: self.app._nav_to("embedder")),
            ("✨  Prompt Generator", "#d97706", lambda: self.app._nav_to("prompt_gen")),
            ("🔄  Prompt-to-Prompt", "#0891b2", lambda: self.app._nav_to("prompt_to_prompt")),
            ("⏮  Resume Last Project", "#334155", self._resume_last_project),
        ]
        for i, (text, color, cmd) in enumerate(actions):
            ctk.CTkButton(qa_box, text=text, height=48, font=ctk.CTkFont("Segoe UI", 14, "bold"),
                fg_color=color, hover_color=color, text_color=TXT, corner_radius=8,
                command=cmd).grid(row=i // 2, column=i % 2, sticky="nsew", padx=4, pady=4)

        # Recent Activity + Productivity Insights + System Status — one
        # row, three equal-width columns. Productivity Insights and
        # System Status used to be built but never gridded (hidden behind
        # Quick Actions); they now have a real home instead of just
        # running their refresh logic for nothing.
        row_mid = ctk.CTkFrame(body, fg_color=BG1, corner_radius=0)
        row_mid.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        for i in range(3): row_mid.grid_columnconfigure(i, weight=1, uniform="row_mid")

        self._activity_box = _section(row_mid, "Recent Activity")
        self._activity_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._activity_list = ctk.CTkFrame(self._activity_box, fg_color="transparent")
        self._activity_list.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 8))

        self._insights_box = _section(row_mid, "Productivity Insights", "🏆")
        self._insights_box.grid(row=0, column=1, sticky="nsew", padx=6)
        self._insight_rows = {}
        # "Est. Hours Saved" removed per request — a rough 30s-per-request
        # guess never had a solid basis and wasn't worth defending.
        insight_items = [("week", "Images This Week"),
                          ("requests_saved", "Est. Requests Saved"),
                          ("avg_score", "Avg. Metadata Quality"), ("speed", "Avg. Processing Speed")]
        for i, (key, label) in enumerate(insight_items):
            self._insight_rows[key] = _kv_row(self._insights_box, label, "—", i)

        self._sys_box = _section(row_mid, "System Status")
        self._sys_box.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        self._sys_rows = {}
        # CPU/RAM removed per request — they were showing N/A for this
        # person regardless of the psutil priming/diagnostics added last
        # round, so rather than keep displaying a permanently-broken
        # reading, the rows are gone until/unless the underlying cause
        # (likely PyInstaller not bundling psutil's compiled backend,
        # per the v0.7.1 notes — still unverified without the built EXE)
        # is actually confirmed and fixed.
        sys_items = [("worker", "Worker Status"), ("bg_tasks", "Background Tasks"),
                     ("queue", "Queue Status"), ("smart", "Smart Workflow")]
        for i, (key, label) in enumerate(sys_items):
            self._sys_rows[key] = _kv_row(self._sys_box, label, "—", i)

        # Activity chart moves to its own row below, full width — it
        # doesn't need to be this tall on its own, so its canvas height
        # drops from 180 to 90 (it used to have to fill the same row
        # height as Recent Activity's ~7 rows of text; on its own row it
        # only needs enough height for the plot itself).
        row_bottom = ctk.CTkFrame(body, fg_color=BG1, corner_radius=0)
        row_bottom.grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        row_bottom.grid_columnconfigure(0, weight=1)
        row_bottom.grid_rowconfigure(0, weight=1)

        chart_box = _section(row_bottom, "Activity (Last 7 Days)")
        chart_box.grid(row=0, column=0, sticky="nsew")
        chart_box.grid_rowconfigure(2, weight=1)
        legend = ctk.CTkFrame(chart_box, fg_color="transparent")
        legend.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))
        for key, color in CHART_COLORS.items():
            dot = ctk.CTkFrame(legend, fg_color="transparent")
            dot.pack(side="left", padx=(0, 12))
            ctk.CTkLabel(dot, text="●", text_color=color, fg_color="transparent",
                font=ctk.CTkFont("Segoe UI", 12)).pack(side="left")
            ctk.CTkLabel(dot, text=CHART_LABELS[key], text_color=TXT3, fg_color="transparent",
                font=ctk.CTkFont("Segoe UI", 10)).pack(side="left", padx=(2, 0))
        self._chart_canvas = tkinter.Canvas(chart_box, height=72, bg=BG2,
            highlightthickness=0)
        # sticky="nsew" (not just "ew"): the canvas now actually grows
        # taller when chart_box's row does, instead of staying pinned to
        # a fixed 72px with dead space opening up below it. The resize
        # binding keeps the drawn chart in sync with that new height —
        # a Canvas doesn't automatically redraw its existing content
        # differently just because it got taller.
        self._chart_canvas.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 8))
        self._chart_resize_after_id = None
        self._chart_canvas.bind("<Configure>", self._on_chart_canvas_resize)

    def _on_chart_canvas_resize(self, event):
        # Debounced: a live window drag fires many Configure events in a
        # burst, and each real redraw walks 7 days of series data plus
        # draws every line/point/gridline — not something to do dozens of
        # times a second mid-drag. Note: no need to re-configure the
        # canvas's own height here — grid + sticky="nsew" + row weight
        # already resized it; this event is just notice that it happened,
        # so all that's needed is to redraw the chart to match.
        if self._chart_resize_after_id:
            try: self.after_cancel(self._chart_resize_after_id)
            except Exception: pass
        self._chart_resize_after_id = self.after(80, self._refresh_chart)

    # ── refresh ─────────────────────────────────────────────────────
    def _auto_refresh(self):
        if self.winfo_viewable():
            self.refresh()
        self.after(4000, self._auto_refresh)

    def refresh(self):
        self._refresh_today()
        self._refresh_lifetime()
        self._refresh_ai_usage()
        self._refresh_insights()
        self._refresh_system()
        self._refresh_activity()
        self._refresh_chart()

    def _apply(self, widget, text, color=None):
        """Update a label's text (and optionally color) only if it
        actually changed. CTkLabel.configure() forces a full internal
        redraw on every call regardless of whether the value is
        different — calling it on 20+ widgets every 4 seconds, all at
        once, is what made the whole dashboard look like it was
        blinking. Skipping the no-op calls removes that; a genuine
        change gets a brief highlight pulse instead of an instant snap."""
        text = str(text)
        try:
            changed = widget.cget("text") != text
        except Exception:
            changed = True
        if color is not None:
            try:
                changed = changed or widget.cget("text_color") != color
            except Exception:
                pass
        if not changed:
            return
        kwargs = {"text": text}
        if color is not None:
            kwargs["text_color"] = color
        widget.configure(**kwargs)
        self._pulse(widget, color)

    def _pulse(self, widget, base_color=None):
        """Subtle ~250ms highlight flash on a value that just changed.
        CTkLabel has no real alpha channel to fade, so this pulses the
        text color toward the accent and back instead — enough motion
        to say "this number moved" without being a jarring snap."""
        try:
            base = base_color if base_color is not None else widget.cget("text_color")
            widget.configure(text_color=CYAN)
            widget.after(220, lambda: widget.configure(text_color=base))
        except Exception:
            pass

    def _set_card(self, key, value, sub=""):
        card = self._today_cards[key]
        children = card.winfo_children()
        # Value is always the first packed CTkLabel after the icon; the
        # corner label (if any) is place()d, not packed, so it's excluded
        # from this ordering automatically.
        packed = [w for w in children if str(w.winfo_manager()) == "pack"]
        if len(packed) >= 2:
            self._apply(packed[1], value)  # value label
        if card._corner_lbl is not None:
            self._apply(card._corner_lbl, sub)
        elif len(packed) > 3:
            self._apply(packed[3], sub)

    def _refresh_today(self):
        t = stats_db.today_summary()
        running = 1 if getattr(self.app, "ai_running", False) else 0
        sw_stage = getattr(getattr(self.app, "_smart_frame", None), "pipeline", None)
        if sw_stage and sw_stage.stage and sw_stage.stage != "complete":
            running += 1
        queue_size = 0
        if getattr(self.app, "ai_running", False):
            results = getattr(self.app, "_results", {}) or {}
            queue_size = sum(1 for r in results.values()
                              if r.get("status") not in ("done", "failed"))
        self._set_card("processed", f"{t['files_processed']:,}")
        self._set_card("completed", f"{t['completed']:,}")
        self._set_card("failed", f"{t['failed']:,}")
        self._set_card("running", str(running), "View in progress" if running else "Idle")
        self._set_card("queue", f"{queue_size:,}", "Waiting to start" if queue_size else "Empty")
        score_txt = f"{t['avg_score']:.1f}%" if t["avg_score"] is not None else "—"
        self._set_card("score", score_txt)

    def _refresh_lifetime(self):
        lt = stats_db.lifetime_summary()
        for key, lbl in self._lifetime_rows.items():
            self._apply(lbl, f"{lt.get(key, 0):,}")

    def _refresh_ai_usage(self):
        lt = stats_db.lifetime_summary()
        today = stats_db.today_summary()
        provider = getattr(self.app, "_last_ai_provider", None) or "—"
        model = getattr(self.app, "_last_ai_model", None) or "—"
        self._apply(self._ai_rows["provider"], provider)
        self._apply(self._ai_rows["model"], model)
        self._apply(self._ai_rows["requests"], f"{lt['total_api_requests']:,}")
        self._apply(self._ai_rows["requests_saved"], f"{lt['total_api_requests_saved']:,}",
            color=GRN if lt["total_api_requests_saved"] else TXT)

        try:
            active_keys = len(get_active_keys(self.app.prefs))
        except Exception:
            active_keys = 0
        try:
            limit_per_key = int(self.app.prefs.get("ai_daily_limit_per_key", 250))
        except Exception:
            limit_per_key = 250
        used_today = today.get("api_requests", 0)
        total_capacity = active_keys * limit_per_key
        remaining = max(total_capacity - used_today, 0)
        if active_keys == 0:
            cap_text = "No active keys"
            cap_color = TXT3
        else:
            cap_text = f"~{remaining:,} images"
            cap_color = GRN if remaining > 0 else RED_BTN
        self._apply(self._ai_rows["capacity"], cap_text, color=cap_color)
        self._apply(self._ai_rows["used_today"],
            f"{used_today:,} / {total_capacity:,}" if active_keys else f"{used_today:,}")

    def _on_daily_limit_changed(self, event=None):
        try:
            val = max(1, int(self._daily_limit_entry.get().strip() or 250))
        except Exception:
            val = 250
        self._daily_limit_entry.delete(0, "end")
        self._daily_limit_entry.insert(0, str(val))
        self.app.prefs["ai_daily_limit_per_key"] = val
        try:
            from core.config import save_prefs
            save_prefs(self.app.prefs)
        except Exception:
            pass
        self._refresh_ai_usage()

    def _refresh_insights(self):
        lt = stats_db.lifetime_summary()
        days, series = stats_db.last_n_days_series(7)
        week_total = sum(series["files_processed"])
        avg_score_row = stats_db.today_summary()["avg_score"]
        speed = None
        if lt["total_processing_seconds"] > 0 and lt["total_files_processed"] > 0:
            speed = lt["total_files_processed"] / (lt["total_processing_seconds"] / 60)
        self._apply(self._insight_rows["week"], f"{week_total:,}")
        self._apply(self._insight_rows["requests_saved"], f"{lt['total_api_requests_saved']:,}")
        self._apply(self._insight_rows["avg_score"],
            f"{avg_score_row:.1f}%" if avg_score_row is not None else "—")
        self._apply(self._insight_rows["speed"],
            f"{speed:.1f} img/min" if speed else "—")

    def _refresh_system(self):
        running = getattr(self.app, "ai_running", False)
        self._apply(self._sys_rows["worker"], "Active" if running else "Idle",
            color=GRN if running else TXT3)
        bg = 1 if running else 0
        sw = getattr(getattr(self.app, "_smart_frame", None), "pipeline", None)
        sw_running = bool(sw and sw.stage and sw.stage != "complete")
        if sw_running: bg += 1
        self._apply(self._sys_rows["bg_tasks"], str(bg))
        self._apply(self._sys_rows["queue"], "Processing" if running else "Empty")
        self._apply(self._sys_rows["smart"], "Running" if sw_running else "Idle",
            color=AMB_BTN if sw_running else TXT3)

    def _refresh_activity(self):
        # 4, not 6 -- and each row is now one line, not two (see below).
        # Both changes are about the same thing: this panel's natural
        # height was dictating the whole row's height (Tk grid stretches
        # every cell in a row to the tallest one), leaving Productivity
        # Insights and System Status padded with dead space to match it.
        rows = stats_db.recent_activity(4)
        # Recent Activity rebuilt its whole widget tree (destroy + recreate
        # every row) on EVERY 4s tick regardless of whether anything had
        # actually happened — that full teardown/rebuild is a much bigger
        # visual flash than a plain text update, and is the main thing that
        # made "everything blinking" complaint accurate. Only rebuild when
        # the underlying rows actually differ from last time.
        if rows == getattr(self, "_last_activity_rows", None):
            return
        self._last_activity_rows = rows
        for w in self._activity_list.winfo_children():
            w.destroy()
        if not rows:
            ctk.CTkLabel(self._activity_list, text="No activity yet.",
                font=ctk.CTkFont("Segoe UI", 12), text_color=TXT3,
                fg_color="transparent").pack(anchor="w", padx=10, pady=10)
            return
        labels = {"metadata_generation": "Metadata generation", "embedding": "Embedding",
                   "prompt_generation": "Prompt generation", "prompt_to_prompt": "Prompt-to-Prompt",
                   "smart_workflow_run": "Smart Workflow"}
        for ts, kind, status, count, detail in rows:
            row = ctk.CTkFrame(self._activity_list, fg_color="transparent", height=22)
            row.pack(fill="x", padx=10, pady=2)
            icon = "✓" if status == "completed" else "✕"
            color = GRN if status == "completed" else RED_BTN
            ctk.CTkLabel(row, text=icon, text_color=color, fg_color="transparent",
                font=ctk.CTkFont("Segoe UI", 11, "bold"), width=16, height=18).pack(side="left")
            title = f"{labels.get(kind, kind)} {'completed' if status=='completed' else 'failed'}"
            sub = detail or f"Count: {count}"
            ctk.CTkLabel(row, text=f"{title}  ·  {sub}", font=ctk.CTkFont("Segoe UI", 10, "bold"),
                text_color=TXT2, fg_color="transparent", anchor="w", height=18).pack(
                side="left", fill="x", expand=True, padx=(4, 0))
            ctk.CTkLabel(row, text=self._relative_time(ts), font=ctk.CTkFont("Segoe UI", 9),
                text_color=TXT3, fg_color="transparent", height=18).pack(side="right")

    def _relative_time(self, ts):
        import datetime
        try:
            dt = datetime.datetime.fromisoformat(ts)
        except Exception:
            return ""
        delta = datetime.datetime.now() - dt
        secs = delta.total_seconds()
        if secs < 60: return "just now"
        if secs < 3600: return f"{int(secs//60)} min ago"
        if secs < 86400: return f"{int(secs//3600)} hr ago"
        return f"{int(secs//86400)} d ago"

    def _refresh_chart(self):
        c = self._chart_canvas
        c.update_idletasks()
        w = max(c.winfo_width(), 200)
        h_now = max(c.winfo_height(), 40)
        days, series = stats_db.last_n_days_series(7)
        cache_key = (w, h_now, days, tuple(tuple(v) for v in series.values()))
        # Same fix as Recent Activity: a canvas delete("all")+full redraw
        # every 4s reads as a flash even though the underlying numbers
        # rarely change that often. Skip it entirely when nothing that
        # would actually change the drawing (width or the 7-day series)
        # has changed since last time.
        if cache_key == getattr(self, "_last_chart_key", None):
            return
        self._last_chart_key = cache_key
        c.delete("all")
        # winfo_height(), not cget("height"): the canvas is now
        # grid-stretched to fill its row (see the resize binding above),
        # and cget("height") would keep reporting the original fixed
        # value it was constructed with, not the actual current size —
        # which is exactly what would have kept the chart small even
        # after the row around it grew to fill the window.
        try:
            h = max(c.winfo_height(), 40)
        except Exception:
            h = 180
        pad_l, pad_r, pad_t, pad_b = 34, 10, 10, 20
        plot_w, plot_h = max(w - pad_l - pad_r, 10), h - pad_t - pad_b
        max_v = max([1] + [v for s in series.values() for v in s])
        # gridlines
        for i in range(4):
            y = pad_t + plot_h * i / 3
            c.create_line(pad_l, y, w - pad_r, y, fill=BG3)
            val = round(max_v * (1 - i / 3))
            c.create_text(pad_l - 6, y, text=str(val), fill=TXT3, anchor="e", font=("Segoe UI", 8))
        n = len(days)
        for i, day in enumerate(days):
            x = pad_l + plot_w * i / max(n - 1, 1)
            import datetime
            try:
                label = datetime.date.fromisoformat(day).strftime("%a")
            except Exception:
                label = ""
            c.create_text(x, h - pad_b + 10, text=label, fill=TXT3, font=("Segoe UI", 8))
        for key, values in series.items():
            color = CHART_COLORS[key]
            pts = []
            for i, v in enumerate(values):
                x = pad_l + plot_w * i / max(n - 1, 1)
                y = pad_t + plot_h * (1 - v / max_v)
                pts.append((x, y))
            for i in range(len(pts) - 1):
                c.create_line(*pts[i], *pts[i + 1], fill=color, width=2, smooth=True)
            for x, y in pts:
                c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")

    def _resume_last_project(self):
        from tkinter import messagebox
        from smart_workflow import state as state_mod
        folder = self.app.prefs.get("last_smart_folder", "")
        resumable = state_mod.find_resumable(folder) if folder else None
        if not resumable:
            messagebox.showinfo("No project to resume",
                "There's no unfinished Smart Workflow run to resume.", parent=self.app)
            return
        self.app._nav_to("smart")
        self.app._smart_frame.resume_from(resumable, folder)
