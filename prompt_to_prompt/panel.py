"""Prompt-to-Prompt Generator workspace — a standalone page, not tied to
any image import. One prompt in, N new variations out — or, in Image to
Prompt mode, one reference image in, N variations inspired by it out."""
import os, csv as csv_mod
import customtkinter as ctk
from tkinter import filedialog, messagebox, StringVar, IntVar, BooleanVar
from ui.theme import (BG1,BG2,BG3,BG4,GLASS,GLASS_BDR,TXT,TXT2,TXT3,
    GRN,GRN_H,GRN_DIM,RED_BTN,RED_BTN_H,RED_DIM,AMB_BTN,AMB_DIM,CYAN,ABSOLUTE_BG)
from ui.dnd import DND_AVAILABLE, DND_FILES
from core.utils import make_thumb
from core.constants import IMAGE_EXTS
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
        self._register_page_wide_drop()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 10))
        hdr.grid_columnconfigure(0, weight=1)
        hdr_left = ctk.CTkFrame(hdr, fg_color=BG1, corner_radius=0)
        hdr_left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(hdr_left, text="🔄  Prompt-to-Prompt Generator",
            font=ctk.CTkFont("Segoe UI", 20, "bold"), text_color=TXT, fg_color=BG1
        ).pack(anchor="w")
        ctk.CTkLabel(hdr_left, text="Give it one prompt — get back several fresh variations.",
            font=ctk.CTkFont("Segoe UI", 11), text_color=TXT3, fg_color=BG1
        ).pack(anchor="w")
        self._reset_btn = ctk.CTkButton(hdr, text="↺  Reset", width=90, height=32,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), fg_color=BG3, hover_color=RED_DIM,
            text_color=TXT2, corner_radius=8, command=self._on_reset)
        self._reset_btn.grid(row=0, column=1, sticky="ne")

        # Mode toggle: From Text (original behavior) vs From Image (new —
        # one reference image in, N prompts inspired by it out, via the
        # same vision-capable call path the Prompt Generator page uses).
        self.mode_var = StringVar(value="text")
        mode_row = ctk.CTkFrame(hdr_left, fg_color="transparent")
        mode_row.pack(anchor="w", pady=(8, 0))
        self._mode_text_btn = ctk.CTkButton(mode_row, text="📝  From Text", height=30,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), corner_radius=6,
            command=lambda: self._set_mode("text"))
        self._mode_text_btn.pack(side="left", padx=(0, 4))
        self._mode_image_btn = ctk.CTkButton(mode_row, text="🖼  From Image", height=30,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), corner_radius=6,
            command=lambda: self._set_mode("image"))
        self._mode_image_btn.pack(side="left")

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
        self._input_box.pack(fill="x", padx=14, pady=(0, 4))
        self._word_count_lbl = ctk.CTkLabel(left, text="0 words",
            font=ctk.CTkFont("Segoe UI", 9), text_color=TXT3, fg_color=GLASS, anchor="e")
        self._word_count_lbl.pack(anchor="e", padx=14, pady=(0, 8))
        self._input_box.bind("<KeyRelease>", self._update_word_count)

        # Image to Prompt mode's input: up to 15 reference images shown as
        # a 5x3 thumbnail grid, analyzed together. This frame is packed
        # with fill="both", expand=True (see _set_mode) so it claims every
        # bit of vertical space the left panel has left over once
        # everything below it (Generate, sliders, buttons) has what it
        # needs — not a small fixed-height box floating above empty space.
        self._image_grid_frame = ctk.CTkFrame(left, fg_color="transparent")
        for c in range(5):
            self._image_grid_frame.grid_columnconfigure(c, weight=1, uniform="imgcol")
        for r in range(3):
            self._image_grid_frame.grid_rowconfigure(r, weight=1, uniform="imgrow")
        self._source_images = []  # up to 15 paths, in slot order
        self._image_slots = []
        for i in range(15):
            slot = self._make_image_slot(self._image_grid_frame, i)
            slot.grid(row=i // 5, column=i % 5, sticky="nsew", padx=3, pady=3)
            self._image_slots.append(slot)

        browse_row = ctk.CTkFrame(left, fg_color="transparent")
        self._browse_row = browse_row
        self._browse_btn = ctk.CTkButton(browse_row, text="Browse…", height=28, width=90,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), fg_color=BG3, hover_color=BG4,
            text_color=TXT2, corner_radius=6, command=self._browse_images)
        self._browse_btn.pack(side="left")
        self._image_count_lbl = ctk.CTkLabel(browse_row, text="0 / 15 images — drag anywhere on this page",
            font=ctk.CTkFont("Segoe UI", 9), text_color=TXT3, fg_color="transparent")
        self._image_count_lbl.pack(side="left", padx=(10, 0))
        # Whole-page drop target, not just this grid — registered once,
        # recursively, over every widget in the panel (see __init__).

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

        ctk.CTkLabel(left, text="Prompt Length (words)", font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=TXT3, fg_color=GLASS, anchor="w").pack(anchor="w", padx=14)
        wc_row = ctk.CTkFrame(left, fg_color="transparent")
        wc_row.pack(fill="x", padx=14, pady=(4, 14))
        self.word_target_var = IntVar(value=30)
        self._word_target_lbl = ctk.CTkLabel(wc_row, text="30 words", width=64,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=GRN, fg_color="transparent", anchor="e")
        self._word_target_lbl.pack(side="right")
        ctk.CTkSlider(wc_row, from_=10, to=100, number_of_steps=90,
            variable=self.word_target_var, command=self._on_word_target_change,
            progress_color=GRN, button_color=TXT, button_hover_color=TXT2,
            fg_color=BG3, height=16).pack(side="left", fill="x", expand=True, padx=(0, 10))

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

        self._set_mode("text")

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

    # ── mode toggle (From Text / From Image) ────────────────────────
    def _set_mode(self, mode):
        self.mode_var.set(mode)
        self._mode_text_btn.configure(fg_color=GRN if mode == "text" else BG3,
            text_color=ABSOLUTE_BG if mode == "text" else TXT2)
        self._mode_image_btn.configure(fg_color=GRN if mode == "image" else BG3,
            text_color=ABSOLUTE_BG if mode == "image" else TXT2)
        if mode == "text":
            self._image_grid_frame.pack_forget()
            self._browse_row.pack_forget()
            self._input_box.pack(fill="x", padx=14, pady=(0, 4))
            self._word_count_lbl.pack(anchor="e", padx=14, pady=(0, 8))
        else:
            self._input_box.pack_forget()
            self._word_count_lbl.pack_forget()
            # fill="both", expand=True: this is what makes the grid claim
            # all the left panel's remaining vertical space instead of
            # sitting as a small fixed box with dead space below it.
            self._image_grid_frame.pack(fill="both", expand=True, padx=14, pady=(0, 6))
            self._browse_row.pack(fill="x", padx=14, pady=(0, 12))

    def _update_word_count(self, event=None):
        text = self._input_box.get("1.0", "end-1c")
        n = len(text.split())
        self._word_count_lbl.configure(text=f"{n} word{'s' if n != 1 else ''}")

    def _on_word_target_change(self, value):
        self._word_target_lbl.configure(text=f"{int(value)} words")

    # ── multi-image grid (up to 15, 5x3) ─────────────────────────────
    def _make_image_slot(self, parent, index):
        slot = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=8,
            border_width=1, border_color=GLASS_BDR)
        icon = ctk.CTkLabel(slot, text="+", font=ctk.CTkFont("Segoe UI", 22),
            text_color=TXT3, fg_color="transparent")
        icon.place(relx=0.5, rely=0.5, anchor="center")
        remove_btn = ctk.CTkButton(slot, text="×", width=18, height=18,
            font=ctk.CTkFont("Segoe UI", 11, "bold"), fg_color=RED_DIM,
            hover_color=RED_BTN_H, text_color=RED_BTN, corner_radius=9,
            command=lambda i=index: self._remove_image_slot(i))
        slot._icon = icon
        slot._remove_btn = remove_btn
        for w in (slot, icon):
            w.bind("<Button-1>", lambda e, i=index: self._on_slot_click(i))
            w.configure(cursor="hand2")
        return slot

    def _on_slot_click(self, index):
        if index < len(self._source_images):
            return  # occupied -- only the × button removes it
        self._browse_images(target_index=index)

    def _remove_image_slot(self, index):
        if index < len(self._source_images):
            path = self._source_images.pop(index)
            try:
                from core.utils import remove_thumb_cache_for
                remove_thumb_cache_for([path], sizes=((90, 90),))
            except Exception:
                pass
            self._refresh_image_slots()

    def _refresh_image_slots(self):
        # image="" (not image=None) when clearing a slot's thumbnail: a
        # real, reproducible crash was found in testing where clearing
        # many previously-thumbnail-filled labels back to back with
        # image=None hit "_tkinter.TclError: image ... doesn't exist" —
        # something about customtkinter/Tk's internal image-name
        # bookkeeping when several labels release an image reference in
        # quick succession within the same widget-tree context. Reliably
        # reproduced with 15 real slots, did not reproduce in a plain
        # isolated Tk root, and did not reproduce in the separate card
        # pool's own rebind() path (which clears images the same way, but
        # more gradually). image="" avoids it entirely and is otherwise
        # equivalent. Left the card pool's own image=None calls alone
        # since they didn't reproduce any problem under the same stress.
        for i, slot in enumerate(self._image_slots):
            if i < len(self._source_images):
                path = self._source_images[i]
                try:
                    img = make_thumb(path, (90, 90))
                except Exception:
                    img = None
                if img is not None:
                    slot._icon.configure(image=img, text="")
                    slot._icon._image = img
                else:
                    slot._icon.configure(image="", text="⚠", text_color=AMB_BTN)
                slot._remove_btn.place(relx=1.0, rely=0.0, x=-2, y=2, anchor="ne")
            else:
                slot._icon.configure(image="", text="+", text_color=TXT3)
                slot._remove_btn.place_forget()
        n = len(self._source_images)
        self._image_count_lbl.configure(
            text=f"{n} / 15 images — drag anywhere on this page" if n < 15
                 else "15 / 15 images (full)")

    def _browse_images(self, target_index=None):
        exts = " ".join(f"*{e}" for e in IMAGE_EXTS)
        remaining = 15 - len(self._source_images)
        if remaining <= 0:
            messagebox.showinfo("Full", "You already have 15 reference images — remove one first.",
                                 parent=self.app)
            return
        paths = filedialog.askopenfilenames(title="Choose reference image(s)",
            filetypes=[("Images", exts)])
        if paths:
            self._add_images(list(paths))

    def _add_images(self, paths):
        remaining = 15 - len(self._source_images)
        if remaining <= 0:
            messagebox.showinfo("Full", "You already have 15 reference images — remove one first.",
                                 parent=self.app)
            return
        added = 0
        for p in paths:
            if len(self._source_images) >= 15:
                break
            if p in self._source_images:
                continue
            self._source_images.append(p)
            added += 1
        if added:
            self._refresh_image_slots()
        if len(paths) > added:
            messagebox.showinfo("Some images skipped",
                f"Only added {added} — the reference grid holds up to 15 images at once.",
                parent=self.app)

    def _on_page_drop(self, event):
        raw = event.data
        paths = [p.strip("{}") for p in raw.split("} {")] if "{" in raw else raw.split()
        paths = [p.strip("{}") for p in paths]
        images = [p for p in paths if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
        if not images:
            return
        if self.mode_var.get() != "image":
            self._set_mode("image")
        self._add_images(images)

    def _register_page_wide_drop(self):
        """The whole page is a drop target, not just the thumbnail grid —
        recursively registered over every widget in the panel at build
        time (same fix class as an earlier drag-and-drop bug in the
        Meta Embedder page: a drop landing on any OTHER widget than the
        few narrow ones explicitly registered just went nowhere)."""
        if not DND_AVAILABLE:
            return
        def _reg(w):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_page_drop)
            except Exception:
                pass
            try:
                children = w.winfo_children()
            except Exception:
                children = []
            for c in children:
                _reg(c)
        _reg(self)

    def _on_reset(self):
        """Clears everything Prompt-to-Prompt is currently holding: the
        generated prompt list, the text input, and every reference image
        — including their disk-cached thumbnails, not just the in-memory
        list, since the person explicitly asked for the temp folder to be
        cleaned up too, not just the on-screen state."""
        if (self._rows or self._source_images or self._input_box.get("1.0","end-1c").strip()) \
                and not messagebox.askyesno("Reset",
                "Clear all generated prompts, the current input, and every reference image?",
                parent=self.app):
            return
        self._render_output([])
        self._input_box.delete("1.0", "end")
        self._update_word_count()
        if self._source_images:
            try:
                from core.utils import remove_thumb_cache_for
                remove_thumb_cache_for(list(self._source_images), sizes=((90, 90),))
            except Exception:
                pass
        self._source_images = []
        self._refresh_image_slots()
        self._prog_lbl.configure(text="Ready.")
        self._prog_bar.set(0)

    # ── engine wiring ───────────────────────────────────────────────
    def _wire_engine(self):
        self.engine.on_progress = lambda d, t, m: self.app.after(0, self._handle_progress, d, t, m)
        self.engine.on_complete = lambda prompts: self.app.after(0, self._handle_complete, prompts)
        self.engine.on_error = lambda msg: self.app.after(0, self._handle_error, msg)

    def _on_generate(self):
        from engine.ai_providers import get_active_keys
        if not get_active_keys(self.app.prefs):
            messagebox.showerror("No API Keys", "Open 'AI Providers' and add at least one active key.",
                                  parent=self.app)
            return
        if self.mode_var.get() == "image":
            if not self._source_images:
                messagebox.showinfo("No images", "Drag one or more images in, or click Browse, first.",
                                     parent=self.app)
                return
            source_image, text = list(self._source_images), None
        else:
            text = self._input_box.get("1.0", "end-1c").strip()
            if not text:
                messagebox.showinfo("No prompt", "Type or paste a prompt first.", parent=self.app)
                return
            source_image = None
        self._gen_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal", text="⏸  Pause")
        self._stop_btn.configure(state="normal")
        self._prog_bar.set(0)
        self.engine.start(text, self.count_var.get(), self.creativity_var.get(), self.style_var.get(),
                           source_image=source_image, target_words=self.word_target_var.get())

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
        if self.mode_var.get() == "image":
            if not self._source_images:
                messagebox.showinfo("No images", "Drag one or more images in, or click Browse, first.",
                                     parent=self.app)
                return
            source_image, text = list(self._source_images), None
        else:
            text = self._input_box.get("1.0", "end-1c").strip()
            if not text:
                messagebox.showinfo("No prompt", "Type or paste a prompt first.", parent=self.app)
                return
            source_image = None
        keep = [r.text for r in self._rows if not r.selected()]
        n = len(selected)
        self._gen_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal", text="⏸  Pause")
        self._stop_btn.configure(state="normal")
        self._prog_bar.set(0)
        self._pending_keep = keep
        self.engine.start(text, n, self.creativity_var.get(), self.style_var.get(),
                           source_image=source_image, target_words=self.word_target_var.get())
