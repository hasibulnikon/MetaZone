"""Prompt-to-Prompt Generator workspace — a standalone page, not tied to
any image import. One prompt in, N new variations out."""
import os, csv as csv_mod
import customtkinter as ctk
from tkinter import filedialog, messagebox, StringVar, IntVar, BooleanVar
from ui.theme import (BG1,BG2,BG3,BG4,GLASS,GLASS_BDR,TXT,TXT2,TXT3,
    GRN,GRN_H,GRN_DIM,RED_BTN,RED_BTN_H,RED_DIM,AMB_BTN,AMB_DIM,CYAN,ABSOLUTE_BG)
from prompt_to_prompt.engine import PromptToPromptEngine, dedupe

COUNT_OPTIONS = [5, 10, 20, 50, 100]
CREATIVITY_OPTIONS = ["Low", "Medium", "High"]
STYLE_OPTIONS = ["Maintain Original", "Commercial", "Creative", "Minimal", "Highly Detailed"]


class _PromptRow(ctk.CTkFrame):
    def __init__(self, master, text, on_copy):
        super().__init__(master, fg_color=BG2, corner_radius=8,
            border_width=1, border_color=GLASS_BDR)
        self.text = text
        self.var = BooleanVar(value=False)
        self.grid_columnconfigure(1, weight=1)
        ctk.CTkCheckBox(self, text="", variable=self.var, width=20,
            fg_color=GRN, hover_color=GRN_H, border_color=GLASS_BDR
        ).grid(row=0, column=0, padx=(10, 4), pady=10)
        ctk.CTkLabel(self, text=text, font=ctk.CTkFont("Segoe UI", 11),
            text_color=TXT2, fg_color=BG2, anchor="w", justify="left",
            wraplength=560).grid(row=0, column=1, sticky="w", pady=10)
        ctk.CTkButton(self, text="Copy", width=60, height=26,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), fg_color=BG3, hover_color=BG4,
            text_color=TXT2, corner_radius=6, command=lambda: on_copy(text)
        ).grid(row=0, column=2, padx=10)

    def selected(self):
        return self.var.get()


class PromptToPromptPanel(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=BG1, corner_radius=0)
        self.app = app
        self.engine = PromptToPromptEngine(app)
        self._wire_engine()
        self._rows = []
        self._pending_keep = None
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 10))
        ctk.CTkLabel(hdr, text="🔄  Prompt-to-Prompt Generator",
            font=ctk.CTkFont("Segoe UI", 20, "bold"), text_color=TXT, fg_color=BG1
        ).pack(anchor="w")
        ctk.CTkLabel(hdr, text="Give it one prompt — get back several fresh variations.",
            font=ctk.CTkFont("Segoe UI", 11), text_color=TXT3, fg_color=BG1
        ).pack(anchor="w")

        body = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        body.grid_columnconfigure(0, weight=1, uniform="ptp")
        body.grid_columnconfigure(1, weight=1, uniform="ptp")
        body.grid_rowconfigure(0, weight=1)

        # ── LEFT: input + options ──────────────────────────────────
        left = ctk.CTkFrame(body, fg_color=GLASS, corner_radius=10,
            border_width=1, border_color=GLASS_BDR)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Original Prompt", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=TXT2, fg_color=GLASS, anchor="w").pack(anchor="w", padx=14, pady=(14, 4))
        self._input_box = ctk.CTkTextbox(left, height=140, font=ctk.CTkFont("Segoe UI", 11),
            fg_color=BG2, text_color=TXT, border_color=GLASS_BDR, border_width=1,
            corner_radius=8, wrap="word")
        self._input_box.pack(fill="x", padx=14, pady=(0, 12))

        ctk.CTkLabel(left, text="Generate", font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=TXT3, fg_color=GLASS, anchor="w").pack(anchor="w", padx=14)
        count_row = ctk.CTkFrame(left, fg_color="transparent")
        count_row.pack(fill="x", padx=14, pady=(4, 12))
        self.count_var = IntVar(value=10)
        self._count_btns = {}
        for n in COUNT_OPTIONS:
            b = ctk.CTkButton(count_row, text=str(n), width=44, height=30,
                font=ctk.CTkFont("Segoe UI", 10, "bold"), corner_radius=6,
                fg_color=BG3, hover_color=BG4, text_color=TXT2,
                command=lambda n=n: self._pick_count(n))
            b.pack(side="left", padx=(0, 4))
            self._count_btns[n] = b
        self._pick_count(10)

        ctk.CTkLabel(left, text="Creativity", font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=TXT3, fg_color=GLASS, anchor="w").pack(anchor="w", padx=14)
        creativity_row = ctk.CTkFrame(left, fg_color="transparent")
        creativity_row.pack(fill="x", padx=14, pady=(4, 12))
        self.creativity_var = StringVar(value="Medium")
        self._creativity_btns = {}
        for c in CREATIVITY_OPTIONS:
            b = ctk.CTkButton(creativity_row, text=c, width=70, height=30,
                font=ctk.CTkFont("Segoe UI", 10, "bold"), corner_radius=6,
                fg_color=BG3, hover_color=BG4, text_color=TXT2,
                command=lambda c=c: self._pick_creativity(c))
            b.pack(side="left", padx=(0, 4))
            self._creativity_btns[c] = b
        self._pick_creativity("Medium")

        ctk.CTkLabel(left, text="Prompt Style", font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=TXT3, fg_color=GLASS, anchor="w").pack(anchor="w", padx=14)
        self.style_var = StringVar(value=STYLE_OPTIONS[0])
        style_menu = ctk.CTkOptionMenu(left, values=STYLE_OPTIONS, variable=self.style_var,
            font=ctk.CTkFont("Segoe UI", 10), fg_color=BG2, button_color=BG3,
            button_hover_color=BG4, text_color=TXT2, corner_radius=8)
        style_menu.pack(fill="x", padx=14, pady=(4, 12))

        ctk.CTkLabel(left, text="Language", font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=TXT3, fg_color=GLASS, anchor="w").pack(anchor="w", padx=14)
        ctk.CTkOptionMenu(left, values=["English"], state="disabled",
            font=ctk.CTkFont("Segoe UI", 10), fg_color=BG2, button_color=BG2,
            text_color=TXT3, corner_radius=8).pack(fill="x", padx=14, pady=(4, 14))

        self._gen_btn = ctk.CTkButton(left, text="✨  Generate", height=42,
            font=ctk.CTkFont("Segoe UI", 12, "bold"), fg_color=GRN, hover_color=GRN_H,
            text_color=ABSOLUTE_BG, text_color_disabled=ABSOLUTE_BG, corner_radius=8,
            command=self._on_generate)
        self._gen_btn.pack(fill="x", padx=14, pady=(0, 8))
        stop_row = ctk.CTkFrame(left, fg_color="transparent")
        stop_row.pack(fill="x", padx=14, pady=(0, 14))
        self._pause_btn = ctk.CTkButton(stop_row, text="⏸  Pause", height=32,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), fg_color=BG3, hover_color=BG4,
            text_color=TXT2, corner_radius=8, state="disabled", command=self._on_pause)
        self._pause_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._stop_btn = ctk.CTkButton(stop_row, text="⏹  Cancel", height=32,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), fg_color=RED_DIM, hover_color=RED_BTN_H,
            text_color=RED_BTN, corner_radius=8, state="disabled", command=self._on_stop)
        self._stop_btn.pack(side="left", fill="x", expand=True)

        self._prog_bar = ctk.CTkProgressBar(left, progress_color=GRN, fg_color=BG3,
            border_width=1, border_color=GLASS_BDR, height=14, corner_radius=7)
        self._prog_bar.pack(fill="x", padx=14, pady=(0, 4)); self._prog_bar.set(0)
        self._prog_lbl = ctk.CTkLabel(left, text="Ready.", font=ctk.CTkFont("Segoe UI", 10),
            text_color=TXT3, fg_color=GLASS, anchor="w")
        self._prog_lbl.pack(anchor="w", padx=14, pady=(0, 14))

        # ── RIGHT: output ───────────────────────────────────────────
        right = ctk.CTkFrame(body, fg_color=GLASS, corner_radius=10,
            border_width=1, border_color=GLASS_BDR)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        out_hdr = ctk.CTkFrame(right, fg_color="transparent")
        out_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 4))
        self._out_count_lbl = ctk.CTkLabel(out_hdr, text="Generated Prompts (0)",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=TXT2, fg_color="transparent")
        self._out_count_lbl.pack(side="left")

        actions = ctk.CTkFrame(right, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        btn_specs = [
            ("Select All", self._select_all), ("Copy All", self._copy_all),
            ("Export TXT", self._export_txt), ("Export CSV", self._export_csv),
            ("Regenerate Selected", self._regenerate_selected),
        ]
        for text, cmd in btn_specs:
            ctk.CTkButton(actions, text=text, height=28, font=ctk.CTkFont("Segoe UI", 9, "bold"),
                fg_color=BG3, hover_color=BG4, text_color=TXT2, corner_radius=6,
                command=cmd).pack(side="left", padx=2)

        self._out_scroll = ctk.CTkScrollableFrame(right, fg_color=BG1, corner_radius=0,
            scrollbar_button_color=BG3)
        self._out_scroll.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self._out_scroll.grid_columnconfigure(0, weight=1)
        self._empty_lbl = ctk.CTkLabel(self._out_scroll,
            text="Generated prompts will appear here.", font=ctk.CTkFont("Segoe UI", 11),
            text_color=TXT3, fg_color=BG1)
        self._empty_lbl.pack(pady=30)

    # ── option pickers ──────────────────────────────────────────────
    def _pick_count(self, n):
        self.count_var.set(n)
        for k, b in self._count_btns.items():
            b.configure(fg_color=GRN if k == n else BG3, text_color=ABSOLUTE_BG if k == n else TXT2)

    def _pick_creativity(self, c):
        self.creativity_var.set(c)
        for k, b in self._creativity_btns.items():
            b.configure(fg_color=GRN if k == c else BG3, text_color=ABSOLUTE_BG if k == c else TXT2)

    # ── engine wiring ───────────────────────────────────────────────
    def _wire_engine(self):
        self.engine.on_progress = lambda d, t, m: self.app.after(0, self._handle_progress, d, t, m)
        self.engine.on_complete = lambda prompts: self.app.after(0, self._handle_complete, prompts)
        self.engine.on_error = lambda msg: self.app.after(0, self._handle_error, msg)

    def _on_generate(self):
        text = self._input_box.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("No prompt", "Type or paste a prompt first.", parent=self.app)
            return
        from engine.ai_providers import get_active_keys
        if not get_active_keys(self.app.prefs):
            messagebox.showerror("No API Keys", "Open 'AI Providers' and add at least one active key.",
                                  parent=self.app)
            return
        self._gen_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal", text="⏸  Pause")
        self._stop_btn.configure(state="normal")
        self._prog_bar.set(0)
        self.engine.start(text, self.count_var.get(), self.creativity_var.get(), self.style_var.get())

    def _on_pause(self):
        paused = self.engine.toggle_pause()
        self._pause_btn.configure(text="▶  Resume" if paused else "⏸  Pause")

    def _on_stop(self):
        self.engine.stop()
        self._prog_lbl.configure(text="Cancelling…")

    def _handle_progress(self, done, total, msg):
        self._prog_bar.set(done / total if total else 0)
        self._prog_lbl.configure(text=msg)

    def _handle_complete(self, prompts):
        self._gen_btn.configure(state="normal")
        self._pause_btn.configure(state="disabled")
        self._stop_btn.configure(state="disabled")
        if self._pending_keep is not None:
            prompts = dedupe(self._pending_keep + prompts)
            self._pending_keep = None
        self._prog_lbl.configure(text=f"Done — {len(prompts)} prompt(s) generated.")
        self._render_output(prompts)

    def _handle_error(self, msg):
        self._gen_btn.configure(state="normal")
        self._pause_btn.configure(state="disabled")
        self._stop_btn.configure(state="disabled")
        self._prog_lbl.configure(text=msg, text_color=RED_BTN)
        messagebox.showerror("Generation failed", msg, parent=self.app)

    # ── output list ─────────────────────────────────────────────────
    def _render_output(self, prompts):
        for w in self._out_scroll.winfo_children():
            w.destroy()
        self._rows = []
        if not prompts:
            self._empty_lbl = ctk.CTkLabel(self._out_scroll,
                text="No prompts were generated — try again.",
                font=ctk.CTkFont("Segoe UI", 11), text_color=TXT3, fg_color=BG1)
            self._empty_lbl.pack(pady=30)
            self._out_count_lbl.configure(text="Generated Prompts (0)")
            return
        for p in prompts:
            row = _PromptRow(self._out_scroll, p, self._copy_one)
            row.pack(fill="x", pady=3)
            self._rows.append(row)
        self._out_count_lbl.configure(text=f"Generated Prompts ({len(prompts)})")

    def _copy_one(self, text):
        self.app.clipboard_clear(); self.app.clipboard_append(text)

    def _select_all(self):
        want = not all(r.selected() for r in self._rows) if self._rows else False
        for r in self._rows:
            r.var.set(want)

    def _copy_all(self):
        if not self._rows: return
        self.app.clipboard_clear()
        self.app.clipboard_append("\n".join(r.text for r in self._rows))

    def _export_txt(self):
        if not self._rows:
            messagebox.showinfo("Nothing to export", "Generate some prompts first.", parent=self.app)
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
            filetypes=[("Text file", "*.txt")], title="Export Prompts as TXT")
        if not path: return
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(r.text for r in self._rows))
        messagebox.showinfo("Exported", f"Saved {len(self._rows)} prompts to:\n{path}", parent=self.app)

    def _export_csv(self):
        if not self._rows:
            messagebox.showinfo("Nothing to export", "Generate some prompts first.", parent=self.app)
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
            filetypes=[("CSV file", "*.csv")], title="Export Prompts as CSV")
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv_mod.writer(f)
            w.writerow(["Prompt"])
            for r in self._rows:
                w.writerow([r.text])
        messagebox.showinfo("Exported", f"Saved {len(self._rows)} prompts to:\n{path}", parent=self.app)

    def _regenerate_selected(self):
        selected = [r for r in self._rows if r.selected()]
        if not selected:
            messagebox.showinfo("Nothing selected", "Check some prompts first.", parent=self.app)
            return
        text = self._input_box.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("No prompt", "Type or paste a prompt first.", parent=self.app)
            return
        keep = [r.text for r in self._rows if not r.selected()]
        n = len(selected)
        self._gen_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal", text="⏸  Pause")
        self._stop_btn.configure(state="normal")
        self._prog_bar.set(0)
        self._pending_keep = keep
        self.engine.start(text, n, self.creativity_var.get(), self.style_var.get())
