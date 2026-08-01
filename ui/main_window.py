"""Main application window: image import/DnD, the AI Generate tab
(card list, virtualization, generation pipeline), Prompt-generation
mode, and CSV export. Everything else (API keys, embedding) opens as
its own popup window from here."""
import os, csv, threading, datetime, queue, bisect
import tkinter
import customtkinter as ctk
from tkinter import filedialog, messagebox, StringVar, BooleanVar, IntVar

from core.constants import (APP_VERSION, PLATFORM_RULES, CONTENT_SUFFIXES,
    VECTOR_EXTS, VIDEO_EXTS, ALL_SUPPORTED_EXTS)
from core.config import load_prefs, save_prefs
from core.utils import find_exiftool, check_online, make_thumb, make_thumb_min_edge, model_label
from engine.ai_providers import call_with_failover, get_active_keys
from engine.prompt_generator import build_meta_prompt, build_prompt_prompt
from engine.parser import parse_meta, enforce_single_keywords, _strip_copyright_keywords, smart_trim, dedupe_content_phrase, sanitize_text_punctuation, sanitize_keywords_punctuation
from ui.theme import (BG1,BG2,BG3,BG4,GLASS,GLASS_BDR,TXT,TXT2,TXT3,
    GRN,GRN_H,GRN_DIM,RED_BTN,RED_BTN_H,RED_DIM,AMB_BTN,AMB_BTN_H,AMB_DIM,
    CYAN,ABSOLUTE_BG,VIRT_BUFFER)
from ui.dnd import DnDCTk, DND_AVAILABLE, DND_FILES
from ui.api_dialog import APIManagerWindow
from ui.embed_window import EmbedWindow
from ui.widgets import ImportProgressDialog, MetaResultCard, ModernDropdown, CompactEditCard
from workers.task_manager import TaskManager

class App(DnDCTk):
    VERSION=APP_VERSION

    def __init__(self):
        super().__init__()
        self.title("Meta Zone"); self.configure(fg_color=BG1)
        self.resizable(True,True)
        self.prefs=load_prefs()

        self._all_paths=[]; self._results={}
        self._thumb_queue=queue.Queue()
        self._thumb_job_queue=queue.Queue()
        self._card_by_path={}
        self.ai_running=False; self.ai_stop_flag=False
        self._ai_paused=False; self.current_mode="meta"
        self._path_idx={}; self._source_folder=""
        self._gen_epoch=0
        self._show_desc_mode=True
        self._task_mgr=TaskManager()
        # View Settings — Expanded (1-4 user-selectable columns) or Compact
        # (columns auto-fit to the available width). Both render through
        # the single paginated-grid renderer — see _render_page below.
        self.view_mode_var=StringVar(value=self.prefs.get("view_mode","expanded"))
        self.grid_cols_var=IntVar(value=self.prefs.get("grid_cols",1))
        self.page_size_var=IntVar(value=self.prefs.get("page_size",50))
        self._current_page=0

        # AI settings
        self.ai_title_var    =StringVar(value=str(self.prefs.get("title_len",130)))
        self.ai_desc_var     =StringVar(value=str(self.prefs.get("desc_len",200)))
        self.ai_kw_var       =StringVar(value=str(self.prefs.get("kw_count",49)))
        self.ai_words_var    =StringVar(value=str(self.prefs.get("prompt_words",60)))
        self.ai_custom_var   =StringVar(value=self.prefs.get("custom_prompt",""))
        self.ai_single_kw_var=BooleanVar(value=self.prefs.get("single_keywords",False))
        self.ai_include_desc_var=BooleanVar(value=self.prefs.get("include_desc",True))
        self.ai_avoid_copy_var=BooleanVar(value=self.prefs.get("avoid_copyright",False))
        self.ai_concurrency_var=IntVar(value=self.prefs.get("concurrency",3))
        self.ai_platform_var =StringVar(value=self.prefs.get("platform","Adobe Stock"))
        self.ai_prefix_on_var=BooleanVar(value=False)
        self.ai_suffix_on_var=BooleanVar(value=False)
        self.ai_prefix_text_var=StringVar(value=self.prefs.get("prefix_text",""))
        self.ai_suffix_text_var=StringVar(value=self.prefs.get("suffix_text",""))
        self.ai_content_type_var=StringVar(value=self.prefs.get("content_type","Auto Detect"))

        self._build_ui()
        self._center(1300,900)
        self.minsize(1000,700)
        self.after(200,self._check_et)
        self.after(500,self._online_loop)
        self.after(80,self._poll_thumb_queue)
        self._start_thumb_workers()

    def _start_thumb_workers(self,n=4):
        def worker():
            while True:
                path,size,widget,min_edge=self._thumb_job_queue.get()
                if min_edge:
                    img=make_thumb_min_edge(path,min_edge=min_edge)
                else:
                    img=make_thumb(path,size)
                if img is not None:
                    self._thumb_queue.put((widget,img))
        for _ in range(n):
            threading.Thread(target=worker,daemon=True).start()

    def _request_thumb(self,path,widget,size=(58,58),min_edge=None):
        """Queue a thumbnail decode job for the bounded worker pool instead
        of spawning a fresh OS thread per image — this is what previously
        caused thread-storm freezes/races when importing many images."""
        self._thumb_job_queue.put((path,size,widget,min_edge))

    def _center(self,w,h):
        self.update_idletasks()
        sw=self.winfo_screenwidth(); sh=self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def ts(self): return datetime.datetime.now().strftime("%H:%M:%S")

    # ── Online ─────────────────────────────────────────────────────
    def _online_loop(self):
        def _c():
            online=check_online()
            self.after(0,lambda:self._set_online(online))
            self.after(8000,self._online_loop)
        threading.Thread(target=_c,daemon=True).start()

    def _set_online(self,online):
        self._is_online=online
        self._online_dot.configure(text_color=GRN if online else RED_BTN)
        self._online_lbl.configure(text="Online" if online else "Offline",
            text_color=TXT2 if online else RED_BTN)
        self._blink(0)

    def _blink(self,n=0):
        if n<6:
            base=GRN if getattr(self,"_is_online",True) else RED_BTN
            self._online_dot.configure(text_color=TXT3 if n%2==0 else base)
            self.after(350,lambda:self._blink(n+1))

    # ── Thumb queue ────────────────────────────────────────────────
    def _poll_thumb_queue(self):
        done=0
        try:
            while done<15:
                (w,img)=self._thumb_queue.get_nowait()
                try:
                    if w.winfo_exists(): w.configure(image=img,text=""); w._image=img
                except: pass
                done+=1
        except queue.Empty: pass
        self.after(80,self._poll_thumb_queue)

    # ══════════════════════════════════════════════════════════════
    #  BUILD UI
    # ══════════════════════════════════════════════════════════════
    def _build_ui(self):
        self.grid_columnconfigure(0,weight=1)
        self.grid_rowconfigure(0,weight=0)  # title bar
        self.grid_rowconfigure(1,weight=1)  # content
        self.grid_rowconfigure(2,weight=0)  # status bar
        self._build_titlebar()
        self._build_content()
        self._build_statusbar()

    def _build_titlebar(self):
        tb=ctk.CTkFrame(self,fg_color=BG2,corner_radius=0,height=54)
        tb.grid(row=0,column=0,sticky="ew"); tb.grid_propagate(False)
        tb.grid_columnconfigure(2,weight=1)
        ctk.CTkLabel(tb,text="✦",font=ctk.CTkFont("Segoe UI",16,"bold"),
            fg_color=BG4,text_color=GRN,corner_radius=8,width=28,height=28
        ).grid(row=0,column=0,padx=(16,8),pady=13)
        ctk.CTkLabel(tb,text="Meta Zone",font=ctk.CTkFont("Segoe UI",18,"bold"),
            text_color=TXT,fg_color=BG2).grid(row=0,column=1,sticky="w")
        mid=ctk.CTkFrame(tb,fg_color=BG2,corner_radius=0)
        mid.grid(row=0,column=2,sticky="ew",padx=(8,0))
        ctk.CTkLabel(mid,text=self.VERSION,font=ctk.CTkFont("Segoe UI",9,"bold"),
            text_color=GRN,fg_color=GRN_DIM,corner_radius=20,padx=8,pady=2
        ).pack(side="left")

        # "Metadata AI" looks like a button (same shape/size family as the
        # Embed button next to it) but is inert — it's just showing which
        # mode is currently active, not something to click. Embed opens
        # the Embed window, styled to visually pair with it: dark/gray +
        # white text here, green + black text there (matching the
        # Generate/Configuration button look).
        ctk.CTkButton(mid,text="Metadata AI",width=112,height=30,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=BG4,hover_color=BG4,text_color=TXT,
            border_width=0,corner_radius=8,
            cursor="arrow",state="disabled",text_color_disabled=TXT
        ).pack(side="left",padx=(14,4))
        ctk.CTkButton(mid,text="📋  Embed",width=112,height=30,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,
            border_width=0,corner_radius=8,
            command=self._open_embed
        ).pack(side="left")
        of=ctk.CTkFrame(tb,fg_color=BG3,corner_radius=20)
        of.grid(row=0,column=3,padx=(0,16),pady=12)
        self._online_dot=ctk.CTkLabel(of,text="●",font=ctk.CTkFont("Segoe UI",16),
            text_color=GRN,fg_color=BG3); self._online_dot.pack(side="left",padx=(12,4),pady=4)
        self._online_lbl=ctk.CTkLabel(of,text="Online",font=ctk.CTkFont("Segoe UI",12,"bold"),
            text_color=TXT2,fg_color=BG3); self._online_lbl.pack(side="left",padx=(0,12),pady=4)
        cr=ctk.CTkFrame(tb,fg_color=BG2,corner_radius=0)
        cr.grid(row=0,column=4,padx=(0,18),sticky="e")
        ctk.CTkLabel(cr,text="All Rights Reserved By",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=TXT2,fg_color=BG2).pack(anchor="e")
        ctk.CTkLabel(cr,text="© HASIBNIKON",font=ctk.CTkFont("Segoe UI",13,"bold"),
            text_color=TXT,fg_color=BG2).pack(anchor="e")

    def _toggle_sidebar(self):
        self._sb_collapsed=not self._sb_collapsed
        if self._sb_collapsed:
            self._sb_frame.grid_remove()
            self._sb_expand_btn.place(relx=0.0,rely=0.5,anchor="w")
        else:
            self._sb_frame.grid()
            self._sb_expand_btn.place_forget()

    def _build_content(self):
        content=ctk.CTkFrame(self,fg_color=BG1,corner_radius=0)
        content.grid(row=1,column=0,sticky="nsew")
        content.grid_columnconfigure(0,weight=0)  # sidebar
        content.grid_columnconfigure(1,weight=1)  # main
        content.grid_rowconfigure(0,weight=1)
        self._sb_frame=ctk.CTkFrame(content,fg_color=BG2,corner_radius=0,width=268)
        self._sb_frame.grid(row=0,column=0,sticky="nsew"); self._sb_frame.grid_propagate(False)
        self._main=ctk.CTkFrame(content,fg_color=BG1,corner_radius=0)
        self._main.grid(row=0,column=1,sticky="nsew")
        self._main.grid_columnconfigure(0,weight=1)
        self._main.grid_rowconfigure(1,weight=0)  # upload zone
        self._main.grid_rowconfigure(2,weight=0)  # progress bar
        self._main.grid_rowconfigure(3,weight=1)  # generated section
        self._build_sidebar()
        self._build_main()

        # Collapsible control panel — collapsing frees the whole window
        # for cards during a big generation run instead of losing 268px
        # to settings you're not touching mid-batch.
        self._sb_collapsed=False
        self._sb_collapse_btn=ctk.CTkButton(self._sb_frame,text="⟨",width=18,height=64,
            font=ctk.CTkFont("Segoe UI",13,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,
            corner_radius=0,command=self._toggle_sidebar)
        self._sb_collapse_btn.place(relx=1.0,rely=0.5,anchor="e")
        self._sb_expand_btn=ctk.CTkButton(self._main,text="⟩",width=18,height=64,
            font=ctk.CTkFont("Segoe UI",13,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,
            corner_radius=0,command=self._toggle_sidebar)
        # Not placed yet — only shown once the sidebar is actually collapsed.

    # ── SIDEBAR ────────────────────────────────────────────────────
    def _build_sidebar(self):
        sb=self._sb_frame; sb.grid_rowconfigure(1,weight=1); sb.grid_columnconfigure(0,weight=1)
        hdr_bg=ctk.CTkFrame(sb,fg_color=BG3,corner_radius=0,height=38)
        hdr_bg.grid(row=0,column=0,sticky="ew"); hdr_bg.grid_propagate(False)
        ctk.CTkLabel(hdr_bg,text="CONTROL PANEL",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=TXT3,fg_color=BG3).pack(side="left",padx=12,pady=10)
        inner=ctk.CTkScrollableFrame(sb,fg_color=BG2,scrollbar_button_color=BG3,corner_radius=0)
        inner.grid(row=1,column=0,sticky="nsew"); inner.grid_columnconfigure(0,weight=1)
        self._sb=inner

        # API config
        ctk.CTkButton(inner,text="🔑  Configuration",
            font=ctk.CTkFont("Segoe UI",12,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,
            height=38,corner_radius=8,command=self._open_api_mgr
        ).pack(fill="x",padx=10,pady=(10,3))
        self._api_lbl=ctk.CTkLabel(inner,text="",font=ctk.CTkFont("Segoe UI",10),
            text_color=TXT3,fg_color=BG2); self._api_lbl.pack(anchor="w",padx=12,pady=(0,4))
        self._refresh_api_lbl()

        # Concurrency slider
        self._div(inner)
        cf=ctk.CTkFrame(inner,fg_color=BG2,corner_radius=0)
        cf.pack(fill="x",padx=10,pady=(0,6)); cf.grid_columnconfigure(0,weight=1)
        top=ctk.CTkFrame(cf,fg_color=BG2,corner_radius=0); top.pack(fill="x")
        top.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(top,text="Concurrent Generations",
            font=ctk.CTkFont("Segoe UI",11),text_color=TXT2,fg_color=BG2
        ).grid(row=0,column=0,sticky="w")
        self._conc_lbl=ctk.CTkLabel(top,text=f"{self.ai_concurrency_var.get()}x",
            font=ctk.CTkFont("Segoe UI",10,"bold"),text_color=GRN,
            fg_color=BG3,corner_radius=20,padx=7,pady=2)
        self._conc_lbl.grid(row=0,column=1)
        ctk.CTkSlider(cf,from_=1,to=10,number_of_steps=9,variable=self.ai_concurrency_var,
            progress_color=GRN,fg_color=BG3,button_color=TXT,button_hover_color="#ddffdd",height=14,
            command=lambda v:(self._conc_lbl.configure(text=f"{int(v)}x"),self._save_settings())
        ).pack(fill="x",pady=(3,0))

        # Mode switch
        self._div(inner)
        mf=ctk.CTkFrame(inner,fg_color=BG3,corner_radius=8)
        mf.pack(fill="x",padx=10,pady=(4,8))
        mf.grid_columnconfigure(0,weight=1); mf.grid_columnconfigure(1,weight=1)
        self._meta_mode_btn=ctk.CTkButton(mf,text="≡  METADATA",height=34,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,corner_radius=6,
            command=lambda:self._set_mode("meta"))
        self._meta_mode_btn.grid(row=0,column=0,sticky="ew",padx=(4,2),pady=4)
        self._prompt_mode_btn=ctk.CTkButton(mf,text="✨  PROMPT",height=34,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color="transparent",hover_color=BG4,text_color=TXT3,corner_radius=6,
            command=lambda:self._set_mode("prompt"))
        self._prompt_mode_btn.grid(row=0,column=1,sticky="ew",padx=(2,4),pady=4)

        # Metadata settings
        self._meta_sf=ctk.CTkFrame(inner,fg_color=BG2,corner_radius=0)
        self._meta_sf.pack(fill="x")
        msf=self._meta_sf
        self._lbl(msf,"METADATA SETTINGS")

        # Platform dropdown (styled)
        plat_row=ctk.CTkFrame(msf,fg_color=BG2,corner_radius=0)
        plat_row.pack(fill="x",padx=10,pady=(0,6)); plat_row.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(plat_row,text="Platform",font=ctk.CTkFont("Segoe UI",11),
            text_color=TXT2,fg_color=BG2).grid(row=0,column=0,sticky="w")
        self._plat_combo=ModernDropdown(msf,variable=self.ai_platform_var,
            values=list(PLATFORM_RULES.keys()),
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=BG3,text_color=GRN,border_color=GRN_DIM,
            accent_color=GRN,dropdown_fg_color=BG4,dropdown_text_color=TXT,
            dropdown_hover_color=GRN_DIM,
            corner_radius=8,height=36,command=self._on_platform_change)
        self._plat_combo.pack(fill="x",padx=10,pady=(0,8))
        self._plat_combo.bind("<MouseWheel>",self._on_platform_scroll)
        self._plat_combo.bind("<Button-4>",lambda e:self._on_platform_scroll(e,-1))
        self._plat_combo.bind("<Button-5>",lambda e:self._on_platform_scroll(e,1))

        # File Type — sits right under Platform, same dropdown style. This
        # drives a MANDATORY title directive (see engine/prompt_generator),
        # not just a loose "theme" hint, so Vector/Transparent PNG/White
        # Background actually show up in the title reliably instead of at
        # the model's discretion.
        ftype_row=ctk.CTkFrame(msf,fg_color=BG2,corner_radius=0)
        ftype_row.pack(fill="x",padx=10,pady=(0,6)); ftype_row.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(ftype_row,text="File Type",font=ctk.CTkFont("Segoe UI",11),
            text_color=TXT2,fg_color=BG2).grid(row=0,column=0,sticky="w")
        self._ftype_combo=ModernDropdown(msf,variable=self.ai_content_type_var,
            values=list(CONTENT_SUFFIXES.keys()),
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=BG3,text_color=GRN,border_color=GLASS_BDR,
            accent_color=GRN,dropdown_fg_color=BG4,dropdown_text_color=TXT,
            dropdown_hover_color=GRN_DIM,
            corner_radius=8,height=36,command=lambda _=None:self._save_settings())
        self._ftype_combo.pack(fill="x",padx=10,pady=(0,8))

        self._title_sl=self._slider(msf,"Title Length",self.ai_title_var,10,300,int(self.ai_title_var.get()))
        self._desc_sl =self._slider(msf,"Description Length",self.ai_desc_var,20,500,int(self.ai_desc_var.get()))

        # Generate Description toggle — off by default request: most of the
        # time only title+keywords are needed, and skipping DESCRIPTION
        # entirely from the prompt leaves more of the token budget for
        # keywords (a long description was eating into that budget before
        # keywords got written).
        df=ctk.CTkFrame(msf,fg_color=BG2,corner_radius=0)
        df.pack(fill="x",padx=10,pady=(1,1)); df.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(df,text="Generate Description",font=ctk.CTkFont("Segoe UI",11),
            text_color=TXT2,fg_color="transparent").grid(row=0,column=0,sticky="w")
        self._desc_toggle_sw=ctk.CTkSwitch(df,text="",variable=self.ai_include_desc_var,
            progress_color=GRN,button_color=TXT,fg_color=GLASS_BDR,
            onvalue=True,offvalue=False,width=46,height=24,
            command=self._on_include_desc_change)
        self._desc_toggle_sw.grid(row=0,column=1,sticky="e")
        self._desc_toggle_lock_lbl=ctk.CTkLabel(df,
            text="Locked while files are loaded — Clear All to change this",
            font=ctk.CTkFont("Segoe UI",8),text_color=TXT3,fg_color="transparent")
        self._desc_toggle_lock_lbl.grid(row=1,column=0,columnspan=2,sticky="w")
        self._desc_toggle_lock_lbl.grid_remove()
        if not self.ai_include_desc_var.get():
            self._desc_sl.configure(state="disabled")

        self._kw_sl   =self._slider(msf,"Keywords Count",self.ai_kw_var,5,49,int(self.ai_kw_var.get()))
        # Lock the title/description ceilings to whatever platform was
        # already saved — otherwise the cap only takes effect the first
        # time the person touches the Platform dropdown, not on startup.
        self._on_platform_change(self.ai_platform_var.get())

        # Single Word Keywords now lives inside Advanced Options (see below)


        # Anchor for stable mode switch
        self._sl_anchor=ctk.CTkFrame(inner,fg_color=BG2,height=0,corner_radius=0)
        self._sl_anchor.pack(fill="x")

        # Prompt sliders (hidden initially)
        self._prompt_sf=ctk.CTkFrame(inner,fg_color=BG2,corner_radius=0)
        self._lbl(self._prompt_sf,"PROMPT SETTINGS")
        self._words_sl=self._slider(self._prompt_sf,"Max Prompt Words",
            self.ai_words_var,10,200,int(self.ai_words_var.get()))

        # Custom system prompt — always visible, sits above Advanced Options
        self._div(inner)
        ctk.CTkLabel(inner,text="CUSTOM SYSTEM PROMPT",font=ctk.CTkFont("Segoe UI",9,"bold"),
            text_color=TXT3,fg_color=BG2).pack(anchor="w",padx=12,pady=(4,2))
        self._custom_box=ctk.CTkTextbox(inner,height=68,
            font=ctk.CTkFont("Segoe UI",11),fg_color=BG3,text_color=TXT,
            border_color=GLASS_BDR,border_width=1,corner_radius=8,wrap="word")
        self._custom_box.pack(fill="x",padx=10,pady=(0,4))
        if self.ai_custom_var.get(): self._custom_box.insert("1.0",self.ai_custom_var.get())
        self._custom_box.bind("<KeyRelease>",lambda e:self._save_custom())
        ctk.CTkButton(inner,text="↺  Reset to Default",height=28,
            font=ctk.CTkFont("Segoe UI",11),fg_color="transparent",
            hover_color=BG3,text_color=CYAN,corner_radius=6,anchor="w",
            command=self._reset_defaults).pack(anchor="w",padx=10,pady=(0,10))

        # Advanced options (collapsible) — last section
        self._div(inner)
        self._adv_visible=False
        self._adv_body=ctk.CTkFrame(inner,fg_color=BG2,corner_radius=0)
        self._adv_btn=ctk.CTkButton(inner,text="▶  Advanced Options",height=32,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,
            border_width=1,border_color=GLASS_BDR,corner_radius=8,anchor="w",
            command=self._toggle_advanced)
        self._adv_btn.pack(fill="x",padx=10,pady=(0,4))

        ab=self._adv_body; ab.grid_columnconfigure(0,weight=1)

        # Prefix / Suffix — entry appears directly under its own toggle
        ctk.CTkLabel(ab,text="TITLE PREFIX / SUFFIX",font=ctk.CTkFont("Segoe UI",9,"bold"),
            text_color=TXT3,fg_color=BG2).pack(anchor="w",padx=12,pady=(4,2))
        for label,on_var,text_var in [
            ("Add Prefix",self.ai_prefix_on_var,self.ai_prefix_text_var),
            ("Add Suffix",self.ai_suffix_on_var,self.ai_suffix_text_var),
        ]:
            grp=ctk.CTkFrame(ab,fg_color=BG2,corner_radius=0)
            grp.pack(fill="x",padx=10,pady=(2,0)); grp.grid_columnconfigure(0,weight=1)
            ctk.CTkLabel(grp,text=label,font=ctk.CTkFont("Segoe UI",11),
                text_color=TXT2,fg_color=BG2).grid(row=0,column=0,sticky="w")
            entry=ctk.CTkEntry(grp,textvariable=text_var,
                placeholder_text=f"Type {label.split()[1].lower()} text here…",height=30,
                font=ctk.CTkFont("Segoe UI",11),fg_color=BG3,text_color=TXT,
                border_color=GLASS_BDR,corner_radius=8)
            def _tog(ov=on_var,e=entry,g=grp):
                if ov.get(): e.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(3,4))
                else: e.grid_remove()
            sw=ctk.CTkSwitch(grp,text="",variable=on_var,
                progress_color=GRN,button_color=TXT,fg_color=GLASS_BDR,
                onvalue=True,offvalue=False,width=46,height=24,command=_tog)
            sw.grid(row=0,column=1,sticky="e")

        ctk.CTkFrame(ab,fg_color=GLASS_BDR,height=1,corner_radius=0).pack(fill="x",padx=8,pady=6)

        ctk.CTkFrame(ab,fg_color=GLASS_BDR,height=1,corner_radius=0).pack(fill="x",padx=8,pady=6)

        # Single Word Keywords — moved here from the always-visible section
        rf4=ctk.CTkFrame(ab,fg_color=BG2,corner_radius=0)
        rf4.pack(fill="x",padx=10,pady=(0,2)); rf4.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(rf4,text="Single Word Keywords",font=ctk.CTkFont("Segoe UI",11),
            text_color=TXT2,fg_color="transparent").grid(row=0,column=0,sticky="w")
        ctk.CTkSwitch(rf4,text="",variable=self.ai_single_kw_var,
            progress_color=GRN,button_color=TXT,fg_color=GLASS_BDR,
            onvalue=True,offvalue=False,width=46,height=24,command=self._save_settings
        ).grid(row=0,column=1,sticky="e")

        # Avoid Copyright — moved inside Advanced Options
        rf3=ctk.CTkFrame(ab,fg_color=BG3,corner_radius=8,border_width=1,border_color=GLASS_BDR)
        rf3.pack(fill="x",padx=10,pady=(0,10)); rf3.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(rf3,text="🚫  Avoid Copyright",font=ctk.CTkFont("Segoe UI",11),
            text_color=TXT2,fg_color="transparent",padx=8,pady=4
        ).grid(row=0,column=0,sticky="w",padx=(8,0),pady=(6,6))
        ctk.CTkSwitch(rf3,text="",variable=self.ai_avoid_copy_var,
            progress_color=GRN,button_color=TXT,fg_color=GLASS_BDR,
            onvalue=True,offvalue=False,width=46,height=24,command=self._save_settings
        ).grid(row=0,column=1,sticky="e",padx=(0,8),pady=(6,6))

    def _toggle_advanced(self):
        self._adv_visible=not self._adv_visible
        if self._adv_visible:
            self._adv_body.pack(fill="x",pady=(0,4))
            self._adv_btn.configure(text="▼  Advanced Options")
        else:
            self._adv_body.pack_forget()
            self._adv_btn.configure(text="▶  Advanced Options")

    def _set_mode(self,mode):
        self.current_mode=mode
        if mode=="meta":
            self._meta_mode_btn.configure(fg_color=GRN,text_color=ABSOLUTE_BG)
            self._prompt_mode_btn.configure(fg_color="transparent",text_color=TXT3)
            self._prompt_sf.pack_forget()
            self._meta_sf.pack(fill="x",before=self._sl_anchor)
        else:
            self._prompt_mode_btn.configure(fg_color=GRN,text_color=ABSOLUTE_BG)
            self._meta_mode_btn.configure(fg_color="transparent",text_color=TXT3)
            self._meta_sf.pack_forget()
            self._prompt_sf.pack(fill="x",before=self._sl_anchor)
        self._clear_results()
        for p in list(self._all_paths):
            self._results[p]={"status":"waiting"}
        self._render_page()

    def _on_platform_scroll(self,event,direction=None):
        plats=list(PLATFORM_RULES.keys())
        cur=self.ai_platform_var.get()
        idx=plats.index(cur) if cur in plats else 0
        d=direction if direction is not None else (-1 if getattr(event,"delta",0)>0 else 1)
        idx=(idx+d)%len(plats)
        new_val=plats[idx]
        self._plat_combo.set(new_val)
        self._on_platform_change(new_val)
        return "break"

    def _on_platform_change(self,val):
        rules=PLATFORM_RULES.get(val,{})
        kw_val=min(rules.get("kw",49),49)
        title_max=rules.get("title",300)
        desc_max=rules.get("desc",500)
        # Lock each slider's ceiling to this platform's real recommended
        # max — the whole point is that once Adobe Stock (200) is picked,
        # you CANNOT drag or type above 200 until you switch platforms;
        # you can still go lower if you want a shorter title.
        self._title_sl.configure(to=title_max,number_of_steps=title_max-self._title_sl._from)
        self._title_sl._to=title_max
        self._desc_sl.configure(to=desc_max,number_of_steps=desc_max-self._desc_sl._from)
        self._desc_sl._to=desc_max
        title_val=min(int(self.ai_title_var.get() or title_max),title_max)
        desc_val=min(int(self.ai_desc_var.get() or desc_max),desc_max)
        for var,sl,v in [(self.ai_title_var,self._title_sl,title_val),
                         (self.ai_desc_var,self._desc_sl,desc_val),
                         (self.ai_kw_var,self._kw_sl,kw_val)]:
            var.set(str(v)); sl.set(v)
            lbl=getattr(sl,"_value_label",None)
            if lbl: lbl.configure(text=str(v))
        self.ai_platform_var.set(val); self._save_settings()

    def _reset_defaults(self):
        for var,sl,val in [(self.ai_title_var,self._title_sl,130),
                           (self.ai_desc_var,self._desc_sl,200),
                           (self.ai_kw_var,self._kw_sl,49),
                           (self.ai_words_var,self._words_sl,60)]:
            var.set(str(val)); sl.set(val)
            lbl=getattr(sl,"_value_label",None)
            if lbl: lbl.configure(text=str(val))
        self._custom_box.delete("1.0","end"); self.ai_custom_var.set("")
        self._save_settings()

    def _div(self,parent):
        ctk.CTkFrame(parent,fg_color=GLASS_BDR,height=1,corner_radius=0
        ).pack(fill="x",padx=8,pady=6)

    def _lbl(self,parent,text):
        ctk.CTkLabel(parent,text=text,font=ctk.CTkFont("Segoe UI",9,"bold"),
            text_color=TXT3,fg_color=BG2).pack(anchor="w",padx=12,pady=(4,2))

    def _slider(self,parent,label,var,from_,to,init):
        fr=ctk.CTkFrame(parent,fg_color=BG2,corner_radius=0)
        fr.pack(fill="x",padx=10,pady=(0,6)); fr.grid_columnconfigure(0,weight=1)
        top=ctk.CTkFrame(fr,fg_color=BG2,corner_radius=0); top.pack(fill="x")
        top.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(top,text=label,font=ctk.CTkFont("Segoe UI",11),
            text_color=TXT2,fg_color=BG2).grid(row=0,column=0,sticky="w")
        vl=ctk.CTkLabel(top,text=str(init),font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=GRN,fg_color=BG3,corner_radius=20,padx=7,pady=2)
        vl.grid(row=0,column=1)
        sl=ctk.CTkSlider(fr,from_=from_,to=to,number_of_steps=to-from_,
            progress_color=GRN,fg_color=BG3,button_color=TXT,
            button_hover_color="#ddffdd",height=14)
        sl.set(init); sl.pack(fill="x",pady=(3,0)); sl._value_label=vl
        # Mutable range bounds — a platform's own recommended max can
        # tighten sl._to afterward (see _on_platform_change), without
        # needing to rebuild the widget. sl._from stays fixed; only the
        # ceiling is ever platform-locked.
        sl._from=from_; sl._to=to
        def _upd(v): iv=int(v); var.set(str(iv)); vl.configure(text=str(iv)); self._save_settings()
        sl.configure(command=_upd)

        # Click the number to type an exact value — Enter (or losing focus)
        # snaps the slider to it, clamped to this slider's OWN CURRENT
        # range (sl._from/sl._to, which a platform lock may have tightened
        # since this closure was created — not the original from_/to).
        def _start_edit(_e=None):
            cur=vl.cget("text")
            vl.grid_remove()
            ent=ctk.CTkEntry(top,width=48,height=22,font=ctk.CTkFont("Segoe UI",10,"bold"),
                fg_color=BG3,text_color=GRN,border_color=GRN,border_width=1,
                corner_radius=20,justify="center")
            ent.grid(row=0,column=1); ent.insert(0,cur); ent.focus_set(); ent.select_range(0,"end")
            def _commit(_e=None):
                txt=ent.get().strip()
                try: iv=int(txt)
                except ValueError: iv=int(var.get() or init)
                iv=max(sl._from,min(sl._to,iv))
                ent.destroy(); vl.grid()
                sl.set(iv); _upd(iv)
            def _cancel(_e=None):
                ent.destroy(); vl.grid()
            ent.bind("<Return>",_commit); ent.bind("<FocusOut>",_commit)
            ent.bind("<Escape>",_cancel)
        vl.bind("<Button-1>",_start_edit)
        return sl

    def _save_settings(self):
        self.prefs.update({
            "platform":self.ai_platform_var.get(),
            "title_len":int(self.ai_title_var.get() or 130),
            "desc_len":int(self.ai_desc_var.get() or 200),
            "kw_count":min(int(self.ai_kw_var.get() or 49),49),
            "prompt_words":int(self.ai_words_var.get() or 60),
            "single_keywords":self.ai_single_kw_var.get(),
            "avoid_copyright":self.ai_avoid_copy_var.get(),
            "concurrency":int(self.ai_concurrency_var.get()),
            "prefix_text":self.ai_prefix_text_var.get(),
            "suffix_text":self.ai_suffix_text_var.get(),
            "include_desc":self.ai_include_desc_var.get(),
            "content_type":self.ai_content_type_var.get(),
            "view_mode":self.view_mode_var.get(),
            "grid_cols":int(self.grid_cols_var.get() or 1),
            "page_size":int(self.page_size_var.get() or 50),
        })
        save_prefs(self.prefs)

    def _on_include_desc_change(self):
        self._desc_sl.configure(state="normal" if self.ai_include_desc_var.get() else "disabled")
        self._save_settings()

    def _update_desc_toggle_lock(self):
        """The Generate Description setting can only be changed when no
        files are loaded — it's locked in for the whole batch the moment
        the first file is imported (see _add_images), and stays locked
        until Clear All. This sidesteps the messier alternative of trying
        to let it change live mid-batch with some cards already built one
        way and others another."""
        locked=len(self._all_paths)>0
        self._desc_toggle_sw.configure(state="disabled" if locked else "normal")
        if locked: self._desc_toggle_lock_lbl.grid()
        else: self._desc_toggle_lock_lbl.grid_remove()

    def _save_custom(self):
        v=self._custom_box.get("1.0","end").strip()
        self.ai_custom_var.set(v); self.prefs["custom_prompt"]=v; save_prefs(self.prefs)

    def _refresh_api_lbl(self):
        seq=get_active_keys(self.prefs); total=len(seq)
        providers=list(dict.fromkeys(p for p,_,_,_ in seq))
        if total:
            self._api_lbl.configure(
                text=f"✓ {total} key{'s' if total!=1 else ''} · {len(providers)} provider{'s' if len(providers)!=1 else ''}",
                text_color=GRN)
        else:
            self._api_lbl.configure(text="⚠ No active keys",text_color=RED_BTN)

    def _open_api_mgr(self):
        APIManagerWindow(self,self.prefs,on_close=self._refresh_api_lbl)

    # ── MAIN AREA ──────────────────────────────────────────────────
    def _build_main(self):
        main=self._main

        # TOP action bar (no tab buttons — they're gone)
        topbar=ctk.CTkFrame(main,fg_color=BG2,corner_radius=0,height=50)
        topbar.grid(row=0,column=0,sticky="ew"); topbar.grid_propagate(False)
        topbar.grid_columnconfigure(0,weight=1)

        left_f=ctk.CTkFrame(topbar,fg_color=BG2,corner_radius=0)
        left_f.grid(row=0,column=0,sticky="w",padx=8,pady=8)
        ctk.CTkLabel(left_f,text="✨  Metadata AI",font=ctk.CTkFont("Segoe UI",14,"bold"),
            text_color=TXT,fg_color=BG2).pack(side="left",padx=(0,8))

        btn_f=ctk.CTkFrame(topbar,fg_color=BG2,corner_radius=0)
        btn_f.grid(row=0,column=1,padx=8,pady=8,sticky="e")

        ctk.CTkButton(btn_f,text="🗑  Clear All",width=96,height=32,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=RED_DIM,hover_color=RED_BTN_H,text_color=RED_BTN,
            border_width=1,border_color=RED_BTN,corner_radius=8,
            command=lambda:self._clear_all(confirm=True)).pack(side="left",padx=(0,5))

        # Pause + Stop (hidden until generation starts)
        self._pause_btn=ctk.CTkButton(btn_f,text="⏸  Pause",width=86,height=32,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=AMB_DIM,hover_color=AMB_BTN_H,text_color=AMB_BTN,
            border_width=1,border_color=AMB_BTN,corner_radius=8,command=self._pause_ai)
        self._stop_btn=ctk.CTkButton(btn_f,text="■  Stop",width=82,height=32,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=RED_DIM,hover_color=RED_BTN_H,text_color=RED_BTN,
            border_width=1,border_color=RED_BTN,corner_radius=8,command=self._stop_ai_now)
        self._retry_btn=ctk.CTkButton(btn_f,text="↺  Retry Failed",width=120,height=32,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=AMB_DIM,hover_color=AMB_BTN_H,text_color=AMB_BTN,
            border_width=1,border_color=AMB_BTN,corner_radius=8,command=self._retry_failed)

        self._gen_btn=ctk.CTkButton(btn_f,text="✨  Generate (0)",width=165,height=32,
            font=ctk.CTkFont("Segoe UI",12,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,
            text_color_disabled=ABSOLUTE_BG,corner_radius=8,
            command=self.start_generate)
        self._gen_btn.pack(side="left",padx=(0,5))

        self._export_btn=ctk.CTkButton(btn_f,text="⬇  Export CSV",width=128,height=32,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,
            border_width=1,border_color=GLASS_BDR,corner_radius=8,
            command=self._export_csv)
        self._export_btn.pack(side="left",padx=(0,5))

        # Manual-edit save: syncs any hand-edited title/description/
        # keywords from currently-materialized cards into self._results
        # and writes the working CSV in place — doesn't require going
        # through Export CSV's save dialog first.
        self._save_btn=ctk.CTkButton(btn_f,text="💾 Save",width=64,height=32,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=GRN_DIM,hover_color=GRN_H,text_color=GRN,
            border_width=1,border_color=GRN,corner_radius=8,
            command=self._save_now)
        self._save_btn.pack(side="left",padx=(0,5))

        # UPLOAD BAR — slim text-only strip (thumbnails now live inside each
        # card, so this no longer needs to reserve grid space for them). The
        # WHOLE WINDOW is a drop zone too — see _register_drop_targets below.
        ws=ctk.CTkFrame(main,fg_color=GLASS,corner_radius=12,
            border_width=1,border_color=GLASS_BDR,height=64)
        ws.grid(row=1,column=0,sticky="ew",padx=8,pady=(6,4))
        ws.grid_columnconfigure(0,weight=1); ws.grid_propagate(False)
        self._ws_frame=ws

        self._ws_empty=ctk.CTkLabel(ws,
            text="🖼️  Drag & drop images/video anywhere in this window — or click here to browse\nJPG · PNG · GIF · WEBP · TIFF · SVG · EPS · MP4 · MOV",
            font=ctk.CTkFont("Segoe UI",12),
            text_color=TXT2,fg_color=GLASS,justify="center",anchor="center")
        self._ws_empty.place(relx=0.5,rely=0.5,anchor="center")

        for w in (ws,self._ws_empty):
            w.bind("<Button-1>",lambda e:self._browse_images())

        self._register_drop_targets([ws,self._ws_empty])

        # Progress bar
        prog=ctk.CTkFrame(main,fg_color=BG1,corner_radius=0,height=36)
        prog.grid(row=2,column=0,sticky="ew"); prog.grid_propagate(False)
        prog.grid_columnconfigure(1,weight=1)
        self._prog_lbl=ctk.CTkLabel(prog,text="● System Ready.",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,fg_color=BG1)
        self._prog_lbl.grid(row=0,column=0,padx=(10,8),pady=4)
        self._prog_bar=ctk.CTkProgressBar(prog,progress_color=GRN,fg_color=BG3,
            border_width=1,border_color=GLASS_BDR,height=14,corner_radius=7)
        self._prog_bar.grid(row=0,column=1,sticky="ew",pady=11,padx=(0,8)); self._prog_bar.set(0)
        self._prog_pct=ctk.CTkLabel(prog,text="",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=GRN,fg_color=BG1,width=36)
        self._prog_pct.grid(row=0,column=2,padx=(0,8))

        # Generated Metadata section — 2x grid, 4 cards visible
        gen=ctk.CTkFrame(main,fg_color=GLASS,corner_radius=12,
            border_width=1,border_color=GLASS_BDR)
        gen.grid(row=3,column=0,sticky="nsew",padx=8,pady=(0,8))
        gen.grid_columnconfigure(0,weight=1); gen.grid_rowconfigure(1,weight=1)

        gen_hdr=ctk.CTkFrame(gen,fg_color="transparent",corner_radius=0,height=38)
        gen_hdr.grid(row=0,column=0,sticky="ew",padx=12,pady=(8,0))
        gen_hdr.grid_propagate(False); gen_hdr.grid_columnconfigure(0,weight=1)
        self._gen_count_lbl=ctk.CTkLabel(gen_hdr,text="Generated Metadata (0)",
            font=ctk.CTkFont("Segoe UI",12,"bold"),text_color=TXT,fg_color="transparent")
        self._gen_count_lbl.grid(row=0,column=0,sticky="w")

        vs=ctk.CTkFrame(gen_hdr,fg_color="transparent",corner_radius=0)
        vs.grid(row=0,column=1,sticky="e")
        self._view_expanded_btn=ctk.CTkButton(vs,text="Expanded",width=76,height=26,
            font=ctk.CTkFont("Segoe UI",10,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,corner_radius=6,
            command=lambda:self._set_view_mode("expanded"))
        self._view_expanded_btn.pack(side="left",padx=(0,2))
        self._view_compact_btn=ctk.CTkButton(vs,text="Compact",width=76,height=26,
            font=ctk.CTkFont("Segoe UI",10,"bold"),
            fg_color="transparent",hover_color=BG3,text_color=TXT3,corner_radius=6,
            command=lambda:self._set_view_mode("compact"))
        self._view_compact_btn.pack(side="left",padx=(0,10))
        self._cols_row=ctk.CTkFrame(vs,fg_color="transparent",corner_radius=0)
        self._cols_row.pack(side="left")
        ctk.CTkLabel(self._cols_row,text="Cols:",font=ctk.CTkFont("Segoe UI",10),
            text_color=TXT3,fg_color="transparent").pack(side="left",padx=(0,4))
        self._col_btns={}
        for n in (1,2,3,4):
            b=ctk.CTkButton(self._cols_row,text=str(n),width=26,height=26,
                font=ctk.CTkFont("Segoe UI",10,"bold"),
                fg_color=BG3,hover_color=BG4,text_color=TXT2,corner_radius=6,
                command=lambda n=n:self._set_grid_cols(n))
            b.pack(side="left",padx=1)
            self._col_btns[n]=b
        self._page_nav=ctk.CTkFrame(vs,fg_color="transparent",corner_radius=0)
        self._page_nav.pack(side="left",padx=(10,0))
        self._page_prev_btn=ctk.CTkButton(self._page_nav,text="◀",width=26,height=26,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,corner_radius=6,
            command=lambda:self._go_page(-1))
        self._page_prev_btn.pack(side="left",padx=1)
        self._page_lbl=ctk.CTkLabel(self._page_nav,text="Page 1/1",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT2,fg_color="transparent",width=64)
        self._page_lbl.pack(side="left",padx=4)
        self._page_next_btn=ctk.CTkButton(self._page_nav,text="▶",width=26,height=26,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,corner_radius=6,
            command=lambda:self._go_page(1))
        self._page_next_btn.pack(side="left",padx=1)
        self._page_nav.pack_forget()  # only shown once paged-grid mode is active
        self._refresh_view_settings_ui()

        self._gen_scroll=ctk.CTkScrollableFrame(gen,fg_color="transparent",
            scrollbar_button_color=BG3,scrollbar_button_hover_color=BG4,corner_radius=0)
        self._gen_scroll.grid(row=1,column=0,sticky="nsew",padx=6,pady=(4,6))
        try:
            cur_incr=int(self._gen_scroll._parent_canvas.cget("yscrollincrement") or 1)
            self._gen_scroll._parent_canvas.configure(yscrollincrement=max(cur_incr*8,8))
        except Exception:
            pass

        self._gen_empty_lbl=ctk.CTkLabel(self._gen_scroll,
            text="Results will appear here after generation.",
            font=ctk.CTkFont("Segoe UI",12),text_color=TXT3,fg_color="transparent")
        self._gen_empty_lbl.place(x=0,y=40,relwidth=1)

        # Floating page-nav buttons — quick prev/next page without reaching
        # for the header controls. Placed on `gen` (not `_gen_scroll`) so
        # they float above the list instead of scrolling away with it.
        self._scroll_down_btn=ctk.CTkButton(gen,text="▼",width=36,height=36,
            font=ctk.CTkFont("Segoe UI",14,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,
            border_width=1,border_color=GLASS_BDR,corner_radius=18,
            command=lambda:self._go_page(1))
        self._scroll_down_btn.place(relx=1.0,rely=1.0,x=-14,y=-14,anchor="se")
        self._scroll_up_btn=ctk.CTkButton(gen,text="▲",width=36,height=36,
            font=ctk.CTkFont("Segoe UI",14,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,
            border_width=1,border_color=GLASS_BDR,corner_radius=18,
            command=lambda:self._go_page(-1))
        self._scroll_up_btn.place(relx=1.0,rely=1.0,x=-14,y=-56,anchor="se")
        self._scroll_down_btn.lift(); self._scroll_up_btn.lift()

        # ── Card rendering ────────────────────────────────────────────
        # A single paginated-grid renderer (_render_page) handles every
        # view mode. Each page is bounded by page_size (default 50), so
        # building a page is cheap regardless of how many files are
        # loaded overall (5000+) — no per-card virtualization/placement
        # math is needed, which is what previously let a card's reserved
        # row silently fall out of sync with its actual built size (e.g.
        # when a large import crossed an internal size threshold) and
        # made cards overlap or seem to vanish. Recompute the auto-fit
        # compact column count when the window is resized.
        self._resize_after_id=None
        self._gen_scroll.bind("<Configure>",self._on_results_resize)

        # Cover the rest of the window so dropping anywhere (not just the
        # upload bar) works — tkdnd only fires on widgets that registered.
        self._register_drop_targets([main,topbar,gen,gen_hdr,self._gen_scroll,
                                      self._gen_empty_lbl,self._sb])

    def _open_embed(self):
        # Pass the last generated CSV path (and the image folder just used)
        # if available — this is exactly what makes "generate then embed"
        # a one-click flow instead of re-browsing for everything.
        EmbedWindow(self, csv_path=getattr(self,"_last_csv_path",None),
                    folder_path=self._source_folder or None)

    # ── DnD ────────────────────────────────────────────────────────
    def _register_drop_targets(self,widgets):
        """Register every widget passed in (plus the whole window) as a
        drag-and-drop target, so dropping files ANYWHERE in the app works —
        not just inside the small upload bar."""
        if not DND_AVAILABLE: return
        if not getattr(self,'dnd_native_ok',True):
            # The Python package imported fine but the native tkdnd Tcl
            # extension itself failed to load — drag-and-drop cannot work
            # at all in this case, no matter how many widgets we register.
            print(f"[Meta Zone] Drag-and-drop unavailable: {getattr(self,'dnd_native_error','unknown error')}")
            return
        failures=0
        for w in list(widgets)+[self]:
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<DropEnter>>",self._on_drag_enter)
                w.dnd_bind("<<DropLeave>>",self._on_drag_leave)
                w.dnd_bind("<<Drop>>",self._on_drop)
            except Exception as e:
                failures+=1
                print(f"[Meta Zone] Drop-target registration failed for {w}: {e}")
        if failures:
            print(f"[Meta Zone] {failures}/{len(widgets)+1} drop-target registrations failed — drag-and-drop may not work on all areas.")

    def _on_drag_enter(self,event):
        self._ws_frame.configure(border_color=GRN,fg_color=GRN_DIM)
        self._ws_empty.configure(fg_color=GRN_DIM,text_color=GRN)
        return event.action

    def _on_drag_leave(self,event):
        self._ws_frame.configure(border_color=GLASS_BDR,fg_color=GLASS)
        self._ws_empty.configure(fg_color=GLASS,text_color=TXT2)
        return event.action

    def _on_drop(self,event):
        self._on_drag_leave(event)
        raw=event.data
        paths=[p.strip('{}') for p in raw.split('} {')] if '{' in raw else raw.split()
        paths=[p.strip('{}') for p in paths]
        expanded=[]
        for p in paths:
            if os.path.isdir(p):
                try:
                    for fn in os.listdir(p):
                        fp=os.path.join(p,fn)
                        if os.path.isfile(fp): expanded.append(fp)
                except: pass
            elif os.path.isfile(p): expanded.append(p)
        self._add_images(expanded)

    # ── Image import ───────────────────────────────────────────────
    def _browse_images(self):
        paths=filedialog.askopenfilenames(title="Select images",
            filetypes=[("Supported","*.jpg *.jpeg *.png *.webp *.gif *.tiff *.tif *.svg *.eps *.mp4 *.mov"),
                       ("All","*.*")])
        if paths: self._add_images(list(paths))

    def _add_images(self,paths):
        existing=set(self._all_paths)
        new=[p for p in paths if p not in existing
             and os.path.splitext(p)[1].lower() in ALL_SUPPORTED_EXTS]
        if not new: return
        if not self._source_folder: self._source_folder=os.path.dirname(new[0])
        # The "Generate Description" setting is locked in for the whole
        # batch the moment the first file is imported (see
        # _update_desc_toggle_lock) — this is what it's locked in AS.
        if not self._all_paths:
            self._show_desc_mode=self.ai_include_desc_var.get()
        if len(new)>15:
            self._import_with_progress(new)
        else:
            for p in new: self._make_blank_card(p)
            self._gen_btn.configure(text=f"✨  Generate ({len(self._all_paths)})")
            self._update_progress()
            self._update_desc_toggle_lock()
            self._render_page()
        self._update_dropzone_visibility()

    def _import_with_progress(self,paths):
        """Record every path in small batches (so the event loop is never
        blocked for more than a moment), with a visible progress dialog.
        No widgets are built here — that's _render_page()'s job, once, for
        whichever page is actually showing — so this loop is just cheap
        bookkeeping even for a 5,000-file batch. Thumbnails are decoded
        separately by the bounded worker pool, never on the main thread."""
        dlg=ImportProgressDialog(self,len(paths))
        total=len(paths); state={"i":0}

        def add_batch():
            BATCH=200
            end=min(state["i"]+BATCH,total)
            for idx in range(state["i"],end):
                self._make_blank_card(paths[idx])
            state["i"]=end
            dlg.update_progress(end,total)
            if end<total:
                self.after(1,add_batch)
            else:
                self._gen_btn.configure(text=f"✨  Generate ({len(self._all_paths)})")
                self._update_progress()
                self._update_dropzone_visibility()
                self._update_desc_toggle_lock()
                self._render_page()
                dlg.finish()
        self.after(10,add_batch)

    def _make_blank_card(self,path):
        """Just records the path + its initial result — no widget is ever
        built here. _render_page() is the only thing that ever creates
        card widgets, always for the current page, always from scratch —
        so there's no per-card bookkeeping (row heights, expand state,
        auto-compact thresholds) that can fall out of sync with what's
        actually on screen."""
        self._all_paths.append(path)
        self._results[path]={"status":"waiting"}

    # ── Rendering (single paginated-grid renderer for every view mode) ──
    CARD_MIN_W=340  # target width used to auto-fit Compact columns

    def _on_results_resize(self,event=None):
        """Debounced: recompute Compact's auto-fit column count on resize,
        and only actually re-render if that count changed (otherwise a
        window drag would re-render the whole page on every pixel)."""
        if self._resize_after_id:
            try: self.after_cancel(self._resize_after_id)
            except Exception: pass
        self._resize_after_id=self.after(150,self._maybe_rerender_on_resize)

    def _maybe_rerender_on_resize(self):
        self._resize_after_id=None
        if self.view_mode_var.get()=="compact":
            new_cols=self._auto_compact_cols()
            if new_cols!=getattr(self,"_last_auto_cols",None):
                self._render_page()

    def _auto_compact_cols(self):
        try:
            w=self._gen_scroll.winfo_width() or 1
        except Exception:
            w=1
        return max(1,min(6,w//self.CARD_MIN_W))

    def _render_page(self):
        """The one and only card renderer. Destroys and rebuilds the
        current page's cards from scratch every time — bounded by
        page_size (default 50) regardless of how many files are loaded
        overall, so this stays cheap even at 5,000+ images. Syncs any
        hand-edited text out of the outgoing cards first so it isn't lost."""
        for p,c in list(self._card_by_path.items()):
            self._sync_card_edits(p,c)
            try: c.destroy()
            except: pass
        self._card_by_path={}
        for child in list(self._gen_scroll.winfo_children()):
            if child is not self._gen_empty_lbl:
                try: child.destroy()
                except: pass

        total=len(self._all_paths)
        if total==0:
            self._gen_empty_lbl.place(x=0,y=40,relwidth=1)
            self._page_lbl.configure(text="Page 1/1")
            self._refresh_view_settings_ui()
            return
        self._gen_empty_lbl.place_forget()

        compact=self.view_mode_var.get()=="compact"
        if compact:
            cols=self._auto_compact_cols()
            self._last_auto_cols=cols
        else:
            cols=max(1,min(int(self.grid_cols_var.get() or 1),4))

        size=max(int(self.page_size_var.get() or 50),1)
        n_pages=max((total+size-1)//size,1)
        self._current_page=max(0,min(self._current_page,n_pages-1))
        start=self._current_page*size
        end=min(start+size,total)
        page_paths=self._all_paths[start:end]

        for c in range(cols):
            self._gen_scroll.grid_columnconfigure(c,weight=1)

        show_desc=getattr(self,'_show_desc_mode',True)
        for i,path in enumerate(page_paths):
            result=self._results.get(path,{"status":"waiting"})
            if compact:
                card=CompactEditCard(self._gen_scroll,path,result,
                    on_redo=lambda p=path:self._redo_single(p),mode=self.current_mode,
                    show_desc=show_desc,request_thumb=self._request_thumb)
            else:
                card=MetaResultCard(self._gen_scroll,path,result,
                    on_redo=lambda p=path:self._redo_single(p),mode=self.current_mode,
                    request_thumb=self._request_thumb,expanded=True,
                    on_toggle_expand=None,show_desc=show_desc)
            card.grid(row=i//cols,column=i%cols,sticky="new",padx=4,pady=4)
            self._card_by_path[path]=card

        self._page_lbl.configure(text=f"Page {self._current_page+1}/{n_pages}")
        self._refresh_view_settings_ui()

    def _go_page(self,delta):
        self._current_page+=delta
        self._render_page()

    def _set_view_mode(self,mode):
        self.view_mode_var.set(mode)
        self._current_page=0
        self._save_settings()
        self._render_page()

    def _set_grid_cols(self,n):
        self.grid_cols_var.set(n)
        self._current_page=0
        self._save_settings()
        self._render_page()

    def _refresh_view_settings_ui(self):
        expanded=self.view_mode_var.get()!="compact"
        self._view_expanded_btn.configure(
            fg_color=GRN if expanded else "transparent",
            text_color=ABSOLUTE_BG if expanded else TXT3)
        self._view_compact_btn.configure(
            fg_color="transparent" if expanded else GRN,
            text_color=TXT3 if expanded else ABSOLUTE_BG)
        # Manual column choice only makes sense in Expanded — Compact
        # always auto-fits as many columns as the window width allows.
        if expanded:
            self._cols_row.pack(side="left")
            cur_cols=int(self.grid_cols_var.get() or 1)
            for n,b in self._col_btns.items():
                b.configure(fg_color=GRN if n==cur_cols else BG3,
                            text_color=ABSOLUTE_BG if n==cur_cols else TXT2)
        else:
            self._cols_row.pack_forget()
        total=len(self._all_paths)
        size=max(int(self.page_size_var.get() or 50),1)
        if total>size:
            self._page_nav.pack(side="left",padx=(10,0))
        else:
            self._page_nav.pack_forget()

    def _sync_card_edits(self,path,card):
        """Read back whatever's currently in a card's editable text boxes
        (title/description/keywords, or prompt) into self._results, so a
        manual hand-edit survives the page being rebuilt. Cards with no
        editable boxes at all (the current read-only Compact card) simply
        have nothing to sync — that's expected, not an error."""
        boxes=getattr(card,"_boxes",None)
        if not boxes or path not in self._results:
            return
        for key,box in boxes.items():
            try:
                self._results[path][key]=box.get("1.0","end-1c")
            except Exception:
                pass

    def _update_progress(self,done=None,total=None,msg=None):
        t=total or len(self._all_paths)
        d=done if done is not None else sum(1 for r in self._results.values() if r.get("status")=="done")
        failed=sum(1 for r in self._results.values() if r.get("status")=="failed")
        if t==0:
            self._prog_lbl.configure(text="● System Ready.",text_color=TXT3)
            self._prog_bar.set(0); self._prog_pct.configure(text=""); return
        self._prog_lbl.configure(
            text=msg or f"Generated {d}/{t}  |  {d} successful  |  {failed} failed",
            text_color=TXT2)
        pct=d/t if t else 0
        self._prog_bar.set(pct); self._prog_pct.configure(text=f"{int(pct*100)}%")
        self.p_ok.configure(text=f"✓  {d} done")
        self.p_err.configure(text=f"✗  {failed} failed")
        self.p_pend.configure(text=f"○  {t-d-failed} pending")
        self._gen_count_lbl.configure(text=f"Generated Metadata ({d})")

    def _update_dropzone_visibility(self):
        """The slim drop bar is only useful when the list is empty — once
        files are loaded it just eats vertical space that cards could use.
        Hiding it does NOT disable drag-and-drop: the whole window is
        already registered as a drop target (see _register_drop_targets),
        so dropping files anywhere still works while the bar is hidden."""
        if self._all_paths:
            self._ws_frame.grid_remove()
        else:
            self._ws_frame.grid()

    def _clear_all(self,confirm=True):
        if self.ai_running: messagebox.showwarning("Busy","Stop generation first."); return
        if confirm and self._all_paths:
            if not messagebox.askyesno("Clear","Remove all files and results?"): return
        self._all_paths.clear(); self._results.clear(); self._source_folder=""
        self._last_csv_path=None
        self._gen_epoch+=1
        self._clear_results()
        self._update_desc_toggle_lock()
        self._gen_btn.configure(text="✨  Generate (0)")
        self._update_progress()
        self._update_dropzone_visibility()

    def _clear_results(self):
        for c in self._card_by_path.values():
            try: c.destroy()
            except: pass
        self._card_by_path={}; self._path_idx={}
        self._current_page=0
        try: self._gen_scroll._parent_canvas.yview_moveto(0.0)
        except Exception: pass
        self._gen_count_lbl.configure(text="Generated Metadata (0)")
        self._gen_empty_lbl.place(x=0,y=40,relwidth=1)
        self._page_lbl.configure(text="Page 1/1")

    def _update_card(self,path):
        """Refresh this path's card IF it's on the currently-shown page.
        Most paths won't have a live widget at any given moment once a
        batch is bigger than one page — that's expected, not a bug:
        self._results[path] already holds the fresh data (process_one
        writes it before calling this), and the card will show it the
        moment that page is turned to. We deliberately do NOT force-build
        a widget here, both because it's unnecessary and because doing so
        used to let a stale/stopped batch's late callback resurrect a card
        for a file no longer part of the app at all — the _all_paths guard
        below still protects the data side of that regardless."""
        if path not in self._all_paths: return
        card=self._card_by_path.get(path)
        if card is not None:
            card.apply_result(self._results.get(path,{}))

    # ── Pause / Stop ───────────────────────────────────────────────
    def _pause_ai(self):
        if not self.ai_running: return
        self._ai_paused=not self._ai_paused
        if self._ai_paused:
            self._pause_btn.configure(text="▶  Resume",fg_color=GRN_DIM,text_color=GRN)
            self.set_status("⏸  Paused",AMB_BTN)
        else:
            self._pause_btn.configure(text="⏸  Pause",fg_color=AMB_DIM,text_color=AMB_BTN)
            self.set_status("▶  Resuming…",GRN)

    def _stop_ai_now(self):
        # Signal every in-flight worker to bail at its next checkpoint, and
        # give control back to the user right away rather than waiting for
        # requests already in flight to time out (which could take up to
        # 30s each and made Stop look like it had frozen the app).
        self.ai_stop_flag=True; self._ai_paused=False; self.ai_running=False
        self._gen_epoch+=1
        for p in self._all_paths:
            r=self._results.get(p,{})
            if r.get("status") in ("waiting","working"):
                r["status"]="stopped"
                card=self._card_by_path.get(p)
                if card is not None: card.apply_result(r)
        self._gen_btn.configure(state="normal",text=f"✨  Generate ({len(self._all_paths)})")
        try: self._pause_btn.pack_forget()
        except: pass
        try: self._stop_btn.pack_forget()
        except: pass
        self._pause_btn.configure(text="⏸  Pause",fg_color=AMB_DIM,text_color=AMB_BTN)
        self.set_status("■  Stopped",RED_BTN)

    def _retry_failed(self):
        if self.ai_running: return
        failed=[p for p in self._all_paths if self._results.get(p,{}).get("status") in ("failed","stopped")]
        if not failed: return
        for p in failed:
            self._results[p]={"status":"waiting"}
            if p in self._card_by_path: self._card_by_path[p].set_waiting()
        try: self._retry_btn.pack_forget()
        except: pass
        self.start_generate()

    # ── Generate ───────────────────────────────────────────────────
    def start_generate(self):
        if self.ai_running: messagebox.showwarning("Busy","Already generating."); return
        if not self._all_paths: messagebox.showerror("No Images","Add images first."); return
        if not get_active_keys(self.prefs):
            messagebox.showerror("No API Keys","Open 'Configuration'."); return
        self.ai_running=True; self.ai_stop_flag=False; self._ai_paused=False
        self._gen_epoch+=1; epoch=self._gen_epoch
        self._gen_btn.configure(state="disabled",text="⟳  Generating…")
        self._pause_btn.pack(side="left",padx=(0,4),before=self._gen_btn)
        self._stop_btn.pack(side="left",padx=(0,5),before=self._gen_btn)
        try: self._retry_btn.pack_forget()
        except: pass
        targets=[p for p in self._all_paths
                 if self._results.get(p,{}).get("status") in ("waiting","failed","stopped")]
        for p in targets: self._results[p]={"status":"waiting"}
        threading.Thread(target=self._gen_thread,args=(targets,epoch),daemon=True).start()

    def _gen_thread(self,targets,epoch):
        mode=self.current_mode
        custom=self.ai_custom_var.get()
        single_kw=self.ai_single_kw_var.get()
        avoid_copyright=self.ai_avoid_copy_var.get()
        themes=""  # Content Themes section removed per request — nothing to build here
        # Only apply prefix/suffix if their toggles are ON
        prefix=self.ai_prefix_text_var.get().strip() if self.ai_prefix_on_var.get() else ""
        suffix_title=self.ai_suffix_text_var.get().strip() if self.ai_suffix_on_var.get() else ""
        concurrency=max(1,min(10,int(self.ai_concurrency_var.get())))

        content_type=self.ai_content_type_var.get()
        content_phrase=CONTENT_SUFFIXES.get(content_type,"")

        if mode=="meta":
            tc=int(self.ai_title_var.get() or 130)
            dc=int(self.ai_desc_var.get() or 200)
            kn=min(int(self.ai_kw_var.get() or 49),49)
            include_desc=self.ai_include_desc_var.get()
            prompt=build_meta_prompt(tc,dc,kn,custom,single_kw,themes,prefix,suffix_title,
                                      avoid_copyright,include_desc,content_phrase)
        else:
            mw=int(self.ai_words_var.get() or 60)
            styles=[content_phrase] if content_phrase else []
            prompt=build_prompt_prompt(mw,styles,custom)

        total=len(targets); done_count=0
        lock=threading.Lock()

        def process_one(path,i):
            nonlocal done_count
            try:
                while getattr(self,"_ai_paused",False) and not self.ai_stop_flag:
                    import time; time.sleep(0.3)
                if self.ai_stop_flag or epoch!=self._gen_epoch: return
                fname=os.path.basename(path)
                self._results[path]={"status":"working"}
                self.after(0,lambda p=path:self._update_card(p))
                self.after(0,lambda f=fname,n=i+1,t=total:
                    self._update_progress(done=done_count,total=t,
                        msg=f"⟳  [{n}/{t}] {f}"))
                try:
                    ext=os.path.splitext(path)[1].lower()
                    if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
                        raise ValueError("Vector/video: convert to JPG first")
                    raw,provider,model_id,key_idx=call_with_failover(path,prompt,self.prefs,
                        status_cb=lambda msg:self.after(0,lambda m=msg:self.set_status(f"⟳  {m}",GRN)))
                    if epoch!=self._gen_epoch: return
                    model_used=f"⚙ {provider} · {model_label(provider,model_id)}" + \
                               (f" ({key_idx})" if key_idx else "")
                    if mode=="meta":
                        title,desc,kw=parse_meta(raw)
                        if not include_desc:
                            # The prompt doesn't ask for a description field
                            # at all in this mode, but a model can still
                            # sometimes emit an extra line (occasionally a
                            # second batch of keywords) that gets parsed
                            # into "desc" — force it empty regardless of
                            # what came back, so this can never leak
                            # through no matter how a given model behaves.
                            desc=""
                        # Several stock platforms reject certain punctuation
                        # outright — strip it here rather than trust the
                        # model to have never used it. Title/description may
                        # still use comma, period, hyphen; keywords allow no
                        # punctuation at all, not even a hyphen.
                        title=sanitize_text_punctuation(title)
                        if desc: desc=sanitize_text_punctuation(desc)
                        kw=sanitize_keywords_punctuation(kw)
                        # Apply prefix ONCE — check it's not already there
                        if prefix:
                            if not title.lower().startswith(prefix.lower()):
                                title=prefix+" "+title
                        # Apply suffix ONCE — check it's not already there
                        if suffix_title:
                            if not title.lower().endswith(suffix_title.lower()):
                                title=title+" "+suffix_title
                        if content_phrase:
                            title=dedupe_content_phrase(title,content_phrase)
                        # Trim to char limit
                        if len(title)>tc: title=smart_trim(title,tc,must_include=content_phrase or None)
                        if single_kw: kw=enforce_single_keywords(kw)
                        if avoid_copyright: kw=_strip_copyright_keywords(kw)
                        # HARD CAP the keyword count in code — the prompt asks
                        # for an exact count, but not every provider/model
                        # follows that instruction, so this guarantees the
                        # microstock-site limit is respected regardless.
                        kw_list=[k.strip() for k in kw.split(",") if k.strip()]
                        seen=set(); deduped=[]
                        for k in kw_list:
                            lk=k.lower()
                            if lk not in seen:
                                seen.add(lk); deduped.append(k)
                        # Some providers/models badly undercount (e.g. ~30
                        # when 49 were requested) despite the prompt asking
                        # for an exact number. One automatic retry with a
                        # stronger reminder recovers most of these cases;
                        # if the retry doesn't do better, keep whichever
                        # attempt got more keywords rather than failing.
                        if kn>0 and len(deduped)<kn*0.7:
                            try:
                                retry_prompt=prompt+(
                                    f"\n\nIMPORTANT CORRECTION: your previous attempt only "
                                    f"produced {len(deduped)} keywords — that is NOT enough. "
                                    f"You MUST output EXACTLY {kn} keywords this time, "
                                    f"comma-separated, no fewer.")
                                raw2,_,_,_=call_with_failover(path,retry_prompt,self.prefs,
                                    status_cb=lambda msg:None)
                                _,_,kw2=parse_meta(raw2)
                                kw2=sanitize_keywords_punctuation(kw2)
                                if single_kw: kw2=enforce_single_keywords(kw2)
                                if avoid_copyright: kw2=_strip_copyright_keywords(kw2)
                                kw2_list=[k.strip() for k in kw2.split(",") if k.strip()]
                                seen2=set(); deduped2=[]
                                for k in kw2_list:
                                    lk=k.lower()
                                    if lk not in seen2:
                                        seen2.add(lk); deduped2.append(k)
                                if len(deduped2)>len(deduped):
                                    deduped=deduped2
                            except Exception:
                                pass  # keep the original (undercounted) result rather than fail
                        kw=", ".join(deduped[:kn])
                        self._results[path]={
                            "status":"done","title":title,"desc":desc,
                            "kw":kw,"model_used":model_used}
                    else:
                        self._results[path]={"status":"done",
                            "prompt":raw.strip(),"model_used":model_used}
                    with lock: done_count+=1
                    self.after(0,lambda p=path:self._update_card(p))
                except Exception as e:
                    self._results[path]={"status":"failed","error":str(e)[:120]}
                    self.after(0,lambda p=path:self._update_card(p))
                self.after(0,lambda n=done_count,t=total:
                    self._update_progress(done=n,total=t))
            except Exception:
                # Safety net only — process_one already catches its own
                # per-item errors above and records them as a "failed"
                # result, so this should never actually fire.
                pass

        self._task_mgr.run_batch(targets, process_one, max_workers=concurrency,
            on_all_done=lambda: self.after(0,self._gen_done))

    def _gen_done(self):
        self.ai_running=False; self._ai_paused=False
        total=len(self._all_paths)
        done=sum(1 for r in self._results.values() if r.get("status")=="done")
        failed=sum(1 for r in self._results.values() if r.get("status") in ("failed","stopped"))
        self._gen_btn.configure(state="normal",text=f"✨  Generate ({total})")
        try: self._pause_btn.pack_forget()
        except: pass
        try: self._stop_btn.pack_forget()
        except: pass
        self._pause_btn.configure(text="⏸  Pause",fg_color=AMB_DIM,text_color=AMB_BTN)
        if failed>0:
            self._retry_btn.pack(side="left",padx=(0,5),before=self._gen_btn)
        self.set_status(f"● Done — {done} generated · {failed} failed",
                        GRN if failed==0 else AMB_BTN)
        self._update_progress(done=done,total=total)
        # Auto-save CSV
        if done>0: self._auto_save_csv()

    def _resolve_csv_path(self):
        """Path to write this session's CSV to. Once self._last_csv_path is
        set, every subsequent save in this session reuses that exact file
        (repeated Save/auto-save on the same batch always updates the same
        CSV, never creates a new one). The first time in a fresh session
        (no _last_csv_path yet — e.g. right after Clear All), if the
        default #foldername.csv already exists on disk from an earlier
        batch/session on this same folder, don't silently overwrite it —
        pick the next available numbered variant instead: '#foldername
        (1).csv', then '(2)', etc."""
        existing=getattr(self,"_last_csv_path",None)
        if existing:
            return existing
        folder=self._source_folder or "."
        folder_name=os.path.basename(self._source_folder) if self._source_folder else "export"
        base=os.path.join(folder,f"#{folder_name}.csv")
        if not os.path.exists(base):
            return base
        n=1
        while True:
            candidate=os.path.join(folder,f"#{folder_name} ({n}).csv")
            if not os.path.exists(candidate):
                return candidate
            n+=1

    def _auto_save_csv(self):
        """Save CSV silently to the source folder with #foldername naming."""
        try:
            done_paths=[p for p in self._all_paths if self._results.get(p,{}).get("status")=="done"]
            if not done_paths: return
            out_path=self._resolve_csv_path()
            mode=self.current_mode
            fields=["Filename","Title","Description","Keywords"] if mode=="meta" else ["Filename","Prompt"]
            def row_for(p):
                r=self._results[p]; fn=os.path.basename(p)
                if mode=="meta":
                    return {"Filename":fn,"Title":r.get("title",""),
                            "Description":r.get("desc",""),"Keywords":r.get("kw","")}
                return {"Filename":fn,"Prompt":r.get("prompt","")}
            with open(out_path,'w',newline='',encoding='utf-8-sig') as f:
                w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
                w.writerows(row_for(p) for p in done_paths)
            self._last_csv_path=out_path
            self.set_status(f"✓  Auto-saved → {os.path.basename(out_path)}",GRN)
        except Exception: pass

    def _redo_single(self,path):
        if self.ai_running: return
        self._results[path]={"status":"waiting"}
        if path in self._card_by_path: self._card_by_path[path].set_waiting()
        self.ai_running=True; self.ai_stop_flag=False; self._ai_paused=False
        self._gen_btn.configure(state="disabled")
        self._pause_btn.pack(side="left",padx=(0,4),before=self._gen_btn)
        self._stop_btn.pack(side="left",padx=(0,5),before=self._gen_btn)
        threading.Thread(target=self._gen_thread,args=([path],),daemon=True).start()

    def _save_now(self):
        """Manual save: read back any hand-edited text from every card
        that's currently materialized (not just the one on screen), then
        write straight to the working CSV — the same file auto-save/
        Export CSV use — so edits are captured whether or not the person
        has ever explicitly exported yet."""
        for p,c in list(self._card_by_path.items()):
            self._sync_card_edits(p,c)
        done=[p for p in self._all_paths if self._results.get(p,{}).get("status")=="done"]
        if not done:
            messagebox.showinfo("Nothing to Save","No generated results yet.")
            return
        out_path=self._resolve_csv_path()
        try:
            mode=self.current_mode
            fields=["Filename","Title","Description","Keywords"] if mode=="meta" else ["Filename","Prompt"]
            def row_for(p):
                fn=os.path.basename(p); r=self._results.get(p,{})
                if mode=="meta":
                    return {"Filename":fn,"Title":r.get("title",""),
                            "Description":r.get("desc",""),"Keywords":r.get("kw","")}
                return {"Filename":fn,"Prompt":r.get("prompt","")}
            with open(out_path,'w',newline='',encoding='utf-8-sig') as f:
                w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
                w.writerows(row_for(p) for p in done)
            self._last_csv_path=out_path
            self.set_status(f"✓  Saved — {len(done)} rows → {os.path.basename(out_path)}",GRN)
        except Exception as e:
            messagebox.showerror("Save Error",str(e))

    def _export_csv(self):
        done=[p for p in self._all_paths if self._results.get(p,{}).get("status")=="done"]
        if not done: messagebox.showinfo("No Results","No generated results yet."); return
        suggested=os.path.basename(self._resolve_csv_path())
        path=filedialog.asksaveasfilename(defaultextension=".csv",
            filetypes=[("CSV","*.csv")],initialfile=suggested)
        if not path: return
        try:
            mode=self.current_mode
            fields=["Filename","Title","Description","Keywords"] if mode=="meta" else ["Filename","Prompt"]
            def row_for(p):
                fn=os.path.basename(p)
                # Read latest from card boxes if available (user may have hand-edited them)
                r=None
                card=self._card_by_path.get(p)
                if card is not None: r=card.get_result()
                if r is None: r=self._results.get(p,{})
                if mode=="meta":
                    return {"Filename":fn,"Title":r.get("title",""),
                            "Description":r.get("desc",""),"Keywords":r.get("kw","")}
                return {"Filename":fn,"Prompt":r.get("prompt","")}
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
                w.writerows(row_for(p) for p in done)
            self._last_csv_path=path
            self.set_status(f"✓  CSV saved — {len(done)} rows",GRN)
            messagebox.showinfo("Saved",f"CSV saved:\n{path}")
        except Exception as e: messagebox.showerror("Error",str(e))

    # ── Status bar ─────────────────────────────────────────────────
    def _build_statusbar(self):
        sb=ctk.CTkFrame(self,fg_color=BG2,corner_radius=0,height=40)
        sb.grid(row=2,column=0,sticky="ew"); sb.grid_propagate(False)
        sb.grid_columnconfigure(4,weight=1)
        self.p_ok=ctk.CTkLabel(sb,text="✓  0 done",font=ctk.CTkFont("Segoe UI",10,"bold"),
            fg_color=GRN_DIM,text_color=GRN,corner_radius=20,padx=10,pady=3)
        self.p_ok.grid(row=0,column=0,padx=(10,4),pady=8)
        self.p_err=ctk.CTkLabel(sb,text="✗  0 failed",font=ctk.CTkFont("Segoe UI",10,"bold"),
            fg_color=RED_DIM,text_color=RED_BTN,corner_radius=20,padx=10,pady=3)
        self.p_err.grid(row=0,column=1,padx=4,pady=8)
        self.p_pend=ctk.CTkLabel(sb,text="○  0 pending",font=ctk.CTkFont("Segoe UI",10,"bold"),
            fg_color=AMB_DIM,text_color=AMB_BTN,corner_radius=20,padx=10,pady=3)
        self.p_pend.grid(row=0,column=2,padx=4,pady=8)
        self.sb_status=ctk.CTkLabel(sb,text="",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=GRN,fg_color=BG2)
        self.sb_status.grid(row=0,column=3,padx=(8,0),sticky="w")
        self.sb_et=ctk.CTkLabel(sb,text="ExifTool · checking…",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,fg_color=BG2)
        self.sb_et.grid(row=0,column=5,padx=(0,14))
        dnd_ok=getattr(self,'dnd_native_ok',DND_AVAILABLE)
        self.sb_dnd=ctk.CTkLabel(sb,
            text="Drag & Drop · ready" if dnd_ok else "Drag & Drop · unavailable",
            font=ctk.CTkFont("Segoe UI",10),text_color=(GRN if dnd_ok else RED_BTN))
        self.sb_dnd.grid(row=0,column=6,padx=(0,14))

    def set_status(self,msg,color=None):
        self.sb_status.configure(text=msg,text_color=color or TXT3)

    def _check_et(self):
        et=find_exiftool()
        if et: self.sb_et.configure(text="ExifTool · ready",text_color=GRN)
        else: self.sb_et.configure(text="ExifTool · missing",text_color=RED_BTN)


if __name__=='__main__':
    app=App(); app.mainloop()
