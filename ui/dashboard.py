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

try:
    import psutil
    _HAS_PSUTIL = True
except Exception:
    _HAS_PSUTIL = False

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
    ctk.CTkLabel(parent, text=label, font=ctk.CTkFont("Segoe UI", 12),
        text_color=TXT3, fg_color="transparent", anchor="w"
    ).grid(row=row + 1, column=0, sticky="w", padx=14, pady=4)
    lbl = ctk.CTkLabel(parent, text=value, font=ctk.CTkFont("Segoe UI", 12, "bold"),
        text_color=value_color or TXT, fg_color="transparent", anchor="e")
    lbl.grid(row=row + 1, column=1, sticky="e", padx=14, pady=4)
    return lbl


def _section(parent, title, icon=""):
    box = ctk.CTkFrame(parent, fg_color=GLASS, corner_radius=10,
        border_width=1, border_color=GLASS_BDR)
    box.grid_columnconfigure(0, weight=1)
    box.grid_columnconfigure(1, weight=0)
    hdr = f"{icon}  {title}" if icon else title
    ctk.CTkLabel(box, text=hdr, font=ctk.CTkFont("Segoe UI", 13, "bold"),
        text_color=TXT, fg_color=GLASS, anchor="w"
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 6))
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

        body = ctk.CTkScrollableFrame(self, fg_color=BG1, corner_radius=0,
            scrollbar_button_color=BG3)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        body.grid_columnconfigure(0, weight=1)
        self._body = body

        # Today's Statistics
        ctk.CTkLabel(body, text="Today's Statistics", font=ctk.CTkFont("Segoe UI", 14, "bold"),
            text_color=TXT, fg_color=BG1, anchor="w").pack(anchor="w", padx=4, pady=(4, 8))
        today_row = ctk.CTkFrame(body, fg_color=BG1, corner_radius=0)
        today_row.pack(fill="x", pady=(0, 16))
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
        row2.pack(fill="x", pady=(0, 16))
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
        ai_items = [("provider", "Current Provider"), ("model", "Current Model"),
                    ("requests", "API Requests"), ("requests_saved", "API Requests Saved"),
                    ("cost", "Est. Cost"), ("cost_saved", "Est. Cost Saved")]
        for i, (key, label) in enumerate(ai_items):
            self._ai_rows[key] = _kv_row(self._ai_box, label, "—", i)

        self._insights_box = _section(row2, "Productivity Insights", "🏆")
        self._insight_rows = {}
        insight_items = [("week", "Images This Week"), ("hours_saved", "Est. Hours Saved"),
                          ("requests_saved", "Est. Requests Saved"),
                          ("avg_score", "Avg. Metadata Quality"), ("speed", "Avg. Processing Speed")]
        for i, (key, label) in enumerate(insight_items):
            self._insight_rows[key] = _kv_row(self._insights_box, label, "—", i)
        # Hidden for now — replaced on-screen by the Quick Actions grid
        # below, which now occupies this exact spot (columns 2-3 of this
        # row). Refresh logic keeps running so the numbers stay current
        # for whenever this comes back.

        self._sys_box = _section(row2, "System Status")
        self._sys_rows = {}
        sys_items = [("worker", "Worker Status"), ("bg_tasks", "Background Tasks"),
                     ("queue", "Queue Status"), ("cpu", "CPU Usage"), ("ram", "RAM Usage"),
                     ("smart", "Smart Workflow")]
        for i, (key, label) in enumerate(sys_items):
            self._sys_rows[key] = _kv_row(self._sys_box, label, "—", i)
        # Also hidden — see note above.

        # Quick Actions now sits where Productivity Insights + System
        # Status used to be (columns 2-3 of row2), sized to their exact
        # combined footprint, as a 2-column x 3-row grid.
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

        # Recent Activity + Chart
        row3 = ctk.CTkFrame(body, fg_color=BG1, corner_radius=0)
        row3.pack(fill="x", pady=(0, 16))
        row3.grid_columnconfigure(0, weight=1); row3.grid_columnconfigure(1, weight=2)

        self._activity_box = _section(row3, "Recent Activity")
        self._activity_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._activity_list = ctk.CTkFrame(self._activity_box, fg_color="transparent")
        self._activity_list.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 12))

        chart_box = _section(row3, "Activity (Last 7 Days)")
        chart_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        legend = ctk.CTkFrame(chart_box, fg_color="transparent")
        legend.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))
        for key, color in CHART_COLORS.items():
            dot = ctk.CTkFrame(legend, fg_color="transparent")
            dot.pack(side="left", padx=(0, 12))
            ctk.CTkLabel(dot, text="●", text_color=color, fg_color="transparent",
                font=ctk.CTkFont("Segoe UI", 12)).pack(side="left")
            ctk.CTkLabel(dot, text=CHART_LABELS[key], text_color=TXT3, fg_color="transparent",
                font=ctk.CTkFont("Segoe UI", 10)).pack(side="left", padx=(2, 0))
        self._chart_canvas = tkinter.Canvas(chart_box, height=180, bg=BG2,
            highlightthickness=0)
        self._chart_canvas.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 12))

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

    def _set_card(self, key, value, sub=""):
        card = self._today_cards[key]
        children = card.winfo_children()
        # Value is always the first packed CTkLabel after the icon; the
        # corner label (if any) is place()d, not packed, so it's excluded
        # from this ordering automatically.
        packed = [w for w in children if str(w.winfo_manager()) == "pack"]
        if len(packed) >= 2:
            packed[1].configure(text=str(value))  # value label
        if card._corner_lbl is not None:
            card._corner_lbl.configure(text=sub)
        elif len(packed) > 3:
            packed[3].configure(text=sub)

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
            lbl.configure(text=f"{lt.get(key, 0):,}")

    def _refresh_ai_usage(self):
        lt = stats_db.lifetime_summary()
        provider = getattr(self.app, "_last_ai_provider", None) or "—"
        model = getattr(self.app, "_last_ai_model", None) or "—"
        self._ai_rows["provider"].configure(text=provider)
        self._ai_rows["model"].configure(text=model)
        self._ai_rows["requests"].configure(text=f"{lt['total_api_requests']:,}")
        self._ai_rows["requests_saved"].configure(text=f"{lt['total_api_requests_saved']:,}",
            text_color=GRN if lt["total_api_requests_saved"] else TXT)
        self._ai_rows["cost"].configure(text=f"${lt['est_api_cost']:.2f}")
        self._ai_rows["cost_saved"].configure(text=f"${lt['est_api_cost_saved']:.2f}", text_color=GRN)

    def _refresh_insights(self):
        lt = stats_db.lifetime_summary()
        days, series = stats_db.last_n_days_series(7)
        week_total = sum(series["files_processed"])
        # Rough estimate: each API request saved represents ~30s of the
        # manual review it would otherwise have taken — clearly an
        # estimate, not a measured figure.
        hours_saved = (lt["total_api_requests_saved"] * 30) / 3600
        avg_score_row = stats_db.today_summary()["avg_score"]
        speed = None
        if lt["total_processing_seconds"] > 0 and lt["total_files_processed"] > 0:
            speed = lt["total_files_processed"] / (lt["total_processing_seconds"] / 60)
        self._insight_rows["week"].configure(text=f"{week_total:,}")
        self._insight_rows["hours_saved"].configure(text=f"{hours_saved:.1f} h")
        self._insight_rows["requests_saved"].configure(text=f"{lt['total_api_requests_saved']:,}")
        self._insight_rows["avg_score"].configure(
            text=f"{avg_score_row:.1f}%" if avg_score_row is not None else "—")
        self._insight_rows["speed"].configure(
            text=f"{speed:.1f} img/min" if speed else "—")

    def _refresh_system(self):
        running = getattr(self.app, "ai_running", False)
        self._sys_rows["worker"].configure(text="Active" if running else "Idle",
            text_color=GRN if running else TXT3)
        bg = 1 if running else 0
        sw = getattr(getattr(self.app, "_smart_frame", None), "pipeline", None)
        sw_running = bool(sw and sw.stage and sw.stage != "complete")
        if sw_running: bg += 1
        self._sys_rows["bg_tasks"].configure(text=str(bg))
        self._sys_rows["queue"].configure(text="Processing" if running else "Empty")
        if _HAS_PSUTIL:
            try:
                self._sys_rows["cpu"].configure(text=f"{psutil.cpu_percent(interval=0):.0f}%")
                self._sys_rows["ram"].configure(text=f"{psutil.virtual_memory().percent:.0f}%")
            except Exception:
                self._sys_rows["cpu"].configure(text="—"); self._sys_rows["ram"].configure(text="—")
        else:
            self._sys_rows["cpu"].configure(text="—"); self._sys_rows["ram"].configure(text="—")
        self._sys_rows["smart"].configure(text="Running" if sw_running else "Idle",
            text_color=AMB_BTN if sw_running else TXT3)

    def _refresh_activity(self):
        for w in self._activity_list.winfo_children():
            w.destroy()
        rows = stats_db.recent_activity(6)
        if not rows:
            ctk.CTkLabel(self._activity_list, text="No activity yet.",
                font=ctk.CTkFont("Segoe UI", 12), text_color=TXT3,
                fg_color="transparent").pack(anchor="w", padx=10, pady=10)
            return
        labels = {"metadata_generation": "Metadata generation", "embedding": "Embedding",
                   "prompt_generation": "Prompt generation", "prompt_to_prompt": "Prompt-to-Prompt",
                   "smart_workflow_run": "Smart Workflow"}
        for ts, kind, status, count, detail in rows:
            row = ctk.CTkFrame(self._activity_list, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=3)
            icon = "✓" if status == "completed" else "✕"
            color = GRN if status == "completed" else RED_BTN
            ctk.CTkLabel(row, text=icon, text_color=color, fg_color="transparent",
                font=ctk.CTkFont("Segoe UI", 12, "bold"), width=16).pack(side="left")
            txt = ctk.CTkFrame(row, fg_color="transparent")
            txt.pack(side="left", fill="x", expand=True, padx=(4, 0))
            title = f"{labels.get(kind, kind)} {'completed' if status=='completed' else 'failed'}"
            ctk.CTkLabel(txt, text=title, font=ctk.CTkFont("Segoe UI", 11, "bold"),
                text_color=TXT2, fg_color="transparent", anchor="w").pack(anchor="w")
            sub = detail or f"Count: {count}"
            ctk.CTkLabel(txt, text=sub, font=ctk.CTkFont("Segoe UI", 10),
                text_color=TXT3, fg_color="transparent", anchor="w").pack(anchor="w")
            ctk.CTkLabel(row, text=self._relative_time(ts), font=ctk.CTkFont("Segoe UI", 10),
                text_color=TXT3, fg_color="transparent").pack(side="right")

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
        c.delete("all")
        c.update_idletasks()
        w = max(c.winfo_width(), 200); h = 180
        pad_l, pad_r, pad_t, pad_b = 34, 10, 10, 20
        plot_w, plot_h = max(w - pad_l - pad_r, 10), h - pad_t - pad_b
        days, series = stats_db.last_n_days_series(7)
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
