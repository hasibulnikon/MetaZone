"""Main application window: image import/DnD, the AI Generate tab
(card list, virtualization, generation pipeline), Prompt-generation
mode, and CSV export. Everything else (API keys, embedding) opens as
its own popup window from here."""
import os, csv, threading, datetime, queue, bisect, time
import tkinter
import customtkinter as ctk
from tkinter import filedialog, messagebox, StringVar, BooleanVar, IntVar

from core.constants import (APP_VERSION, PLATFORM_RULES, CONTENT_SUFFIXES,
    VECTOR_EXTS, VIDEO_EXTS, ALL_SUPPORTED_EXTS)
from core.config import load_prefs, save_prefs
from core import stats_db
from ui.dashboard import DashboardPage
from prompt_to_prompt.panel import PromptToPromptPanel
from core.utils import (find_exiftool, check_online, make_thumb, make_thumb_min_edge, model_label,
    set_window_icon, clear_thumb_cache, prefetch_thumb_to_cache, wait_stable_and_validate_image)
from smart_workflow.panel import SmartWorkflowPanel
from smart_workflow import state as smart_state
from engine.ai_providers import call_with_failover, get_active_keys
from engine.prompt_generator import build_meta_prompt, build_prompt_prompt
from engine.parser import parse_meta, enforce_single_keywords, _strip_copyright_keywords, smart_trim, dedupe_content_phrase, sanitize_text_punctuation, sanitize_keywords_punctuation
from ui.theme import (BG1,BG2,BG3,BG4,NAV_BG,GLASS,GLASS_BDR,GLASS_BDR_AC,TXT,TXT2,TXT3,
    GRN,GRN_H,GRN_DIM,RED_BTN,RED_BTN_H,RED_DIM,AMB_BTN,AMB_BTN_H,AMB_DIM,
    CYAN,ABSOLUTE_BG,VIRT_BUFFER)
from ui.dnd import DnDCTk, DND_AVAILABLE, DND_FILES
from ui.api_dialog import APIManagerWindow
from ui.embed_window import EmbedWindow
from ui.widgets import ImportProgressDialog, MetaResultCard, ModernDropdown, CompactEditCard
from workers.task_manager import TaskManager

def _lerp_hex(c1,c2,t):
    """Linear-interpolate between two '#rrggbb' colors at t in [0,1] —
    the only building block the fade-in animations need, since CTk has
    no real alpha channel to animate against."""
    t=max(0.0,min(1.0,t))
    r1,g1,b1=int(c1[1:3],16),int(c1[3:5],16),int(c1[5:7],16)
    r2,g2,b2=int(c2[1:3],16),int(c2[3:5],16),int(c2[5:7],16)
    r=round(r1+(r2-r1)*t); g=round(g1+(g2-g1)*t); b=round(b1+(b2-b1)*t)
    return f"#{r:02x}{g:02x}{b:02x}"

def _fmt_secs(s):
    """Human-readable duration for the Processing Queue's ETA field."""
    s=max(0,int(s))
    if s<60: return f"{s}s"
    m,s=divmod(s,60)
    if m<60: return f"{m}m {s}s"
    h,m=divmod(m,60)
    return f"{h}h {m}m"

class App(DnDCTk):
    VERSION=APP_VERSION

    def __init__(self):
        super().__init__()
        self.title("Meta Zone"); self.configure(fg_color=BG1)
        self.resizable(True,True)
        set_window_icon(self)
        self.prefs=load_prefs()

        # Screen-aware scaling: this UI's dimensions are still fixed
        # pixel values throughout (a full rewrite to percentage-based
        # layout for every one of the thousands of hardcoded sizes across
        # this codebase is a far bigger undertaking than fits in one
        # pass — flagged honestly rather than half-done; see CHANGELOG).
        # This applies CustomTkinter's own global widget-scaling
        # mechanism instead, scaled to how the actual screen compares to
        # the ~1920x1080 display this UI was laid out assuming — on a
        # meaningfully smaller screen (a reported real case: a 720p
        # monitor, where the app opened larger than the screen itself and
        # its bottom controls were pushed out of view with no way to
        # reach them), every widget shrinks together instead of a handful
        # of fixed-size elements overflowing off-screen while the rest of
        # the app doesn't adapt at all.
        try:
            sw,sh=self.winfo_screenwidth(),self.winfo_screenheight()
            scale=min(1.0, sh/1080, sw/1920)
            scale=max(scale, 0.6)  # never shrink below 60% -- text needs to stay legible
            ctk.set_widget_scaling(scale)
        except Exception:
            sw=sh=None

        self._all_paths=[]; self._results={}
        self._thumb_queue=queue.Queue()
        self._thumb_job_queue=queue.Queue()
        self._ui_action_queue=queue.Queue()
        # ^ generation worker threads (process_one) push callables here
        # instead of calling self.after()/touching any widget directly —
        # see _poll_ui_actions. Consolidates the fix applied to the
        # online-status loop and generation-completion flag: NO Tk call
        # ever originates on a background thread for this path, which
        # removes the whole class of bug those two turned out to be.
        self._card_by_path={}
        # Completion order — NOT import order. Cards render in the order
        # their OWN generation actually finished, appended once and never
        # reordered after. Rendering by import order instead (the
        # original approach) meant every already-on-screen card would
        # shift position whenever an EARLIER-in-import-order image
        # finished AFTER a later one already had a card — completions
        # rarely land in import order once there's any real concurrency,
        # so this was happening on nearly every single completion, which
        # is what actually caused the "constant deforming and reforming"
        # complaint — no fade animation was ever going to mask a card
        # underneath it physically relocating. A path is appended here
        # exactly once, the first time it reaches done/failed; redoing it
        # later updates its content in place without moving it.
        self._completion_order=[]
        self.ai_running=False; self.ai_stop_flag=False
        self._ai_paused=False; self.current_mode="meta"
        self._path_idx={}; self._source_folder=""
        self._gen_epoch=0
        self._gen_retry_count=0  # Processing Queue panel's "Retries" stat
        self._show_desc_mode=True
        self._task_mgr=TaskManager()
        self._gen_done_flag=False
        # ^ set (not called) from TaskManager's background "watch" thread
        # when a Standard Workflow batch finishes — see _poll_gen_done.
        # This used to be `on_all_done=lambda: self.after(0,self._gen_done)`,
        # which calls a Tk method (self.after) directly from that
        # background thread on every single generation completion — not
        # thread-safe, and a very likely repeat contributor to the
        # "app randomly freezes/goes Not Responding after a while"
        # reports across several versions, the same class of bug already
        # found and fixed once for the online-status loop. A plain bool
        # set from a worker thread is safe (GIL-protected); polling it
        # from a main-thread-scheduled after() loop means NO Tk call
        # ever happens off the main thread for this path.
        self._last_ai_provider=None; self._last_ai_model=None
        # View Settings — Expanded or Compact. Column count is no longer
        # user-chosen: both modes auto-fit columns to the available width
        # (see _auto_grid_cols), and there is no pagination at all any
        # more — every loaded image renders in one continuously
        # scrolling grid (see _render_page).
        self.view_mode_var=StringVar(value=self.prefs.get("view_mode","expanded"))

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
        # Minimum size is also screen-relative now, not a fixed floor —
        # 1000x700 alone left almost no margin on a 720p display (barely
        # room for the OS taskbar), which is part of the same real
        # scaling bug as the window's default size above.
        try:
            sw2,sh2=self.winfo_screenwidth(),self.winfo_screenheight()
            self.minsize(min(1000,int(sw2*0.75)), min(700,int(sh2*0.75)))
        except Exception:
            self.minsize(1000,700)
        self.after(200,self._check_et)
        self.after(500,self._online_loop)
        self.after(80,self._poll_thumb_queue)
        self.after(100,self._poll_gen_done)
        self.after(30,self._poll_ui_actions)
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
                    self._thumb_queue.put((widget,img,path))
        for _ in range(n):
            threading.Thread(target=worker,daemon=True).start()

    def _request_thumb(self,path,widget,size=(58,58),min_edge=None):
        """Queue a thumbnail decode job for the bounded worker pool instead
        of spawning a fresh OS thread per image — this is what previously
        caused thread-storm freezes/races when importing many images.

        Tags the widget with the path it currently wants (_thumb_want_path)
        so a stale delivery can be told apart from a current one — see
        _poll_thumb_queue. This matters because pool-slot cards get
        rebind()ed to a different path constantly as results come in out
        of import order, and with only 4 workers pulling a shared queue,
        an OLDER job for a path this exact widget no longer represents can
        easily still be mid-decode (worse the larger the images) when a
        NEWER request for the widget's new path goes out — nothing
        previously stopped that stale result from landing after the
        correct one and silently overwriting it with the wrong image, or
        landing as the only delivery at all if the correct one was itself
        superseded again before it got picked up. Without this tag, more
        churn (bigger batches, more concurrency, bigger/slower-to-decode
        images) directly means more chances for the wrong or missing
        thumbnail to be whatever's left on screen."""
        widget._thumb_want_path=path
        self._thumb_job_queue.put((path,size,widget,min_edge))

    def _center(self,w,h):
        self.update_idletasks()
        sw=self.winfo_screenwidth(); sh=self.winfo_screenheight()
        # Never open larger than the actual screen. A fixed 1300x900
        # window is both wider AND taller than a 1280x720 display —
        # this is the confirmed real cause of a reported bug ("the app
        # was huge, bottom options were not showing at all" on a 720p
        # monitor): the window plainly could not fit on screen, and
        # its bottom rows (status bar, action buttons) ended up pushed
        # past the visible screen area / behind the taskbar with no way
        # to reach them.
        w=min(w,int(sw*0.92)); h=min(h,int(sh*0.88))
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def ts(self): return datetime.datetime.now().strftime("%H:%M:%S")

    # ── Online ─────────────────────────────────────────────────────
    def _online_loop(self):
        def _c():
            online=check_online()
            # Never call self.after() (or anything Tk) from this thread —
            # push the update through the same safe queue every other
            # worker uses; the reschedule itself is safe here since this
            # loop's own driver is the self.after(500,...)/self.after(8000,...)
            # scheduled from _poll_ui_actions below, which runs on the main
            # thread.
            self._ui_action_queue.put(lambda:self._set_online(online))
            self._ui_action_queue.put(lambda:self.after(8000,self._online_loop))
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
    def _poll_ui_actions(self):
        # Bounded per tick — a burst of many files finishing at once
        # (high concurrency) drains over a few ticks instead of doing
        # unbounded work in one go, which is its own mild throttle.
        for _ in range(40):
            try:
                action=self._ui_action_queue.get_nowait()
            except queue.Empty:
                break
            try:
                action()
            except Exception:
                pass
        self.after(30,self._poll_ui_actions)

    def _poll_gen_done(self):
        if self._gen_done_flag:
            self._gen_done_flag=False
            self._gen_done()
        self.after(100,self._poll_gen_done)

    def _poll_thumb_queue(self):
        done=0
        try:
            while done<15:
                (w,img,path)=self._thumb_queue.get_nowait()
                try:
                    # Discard stale deliveries: only apply if this widget
                    # still actually wants THIS path's thumbnail right now
                    # (see _request_thumb's docstring for why this matters).
                    if w.winfo_exists() and getattr(w,"_thumb_want_path",None)==path:
                        w.configure(image=img,text=""); w._image=img
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
        self.grid_rowconfigure(1,weight=1)  # shell (nav + pages)
        self.grid_rowconfigure(2,weight=0)  # status bar
        self._build_titlebar()
        self._build_shell()
        self._build_statusbar()

    def _build_shell(self):
        """Permanent global navigation (left) + a page area (right) that
        holds every workspace stacked in the SAME grid cell, switched
        with tkraise()/lower() — never destroyed/rebuilt, so background
        processing (a running generation batch, an active Smart Workflow
        run) is never interrupted by switching pages; only what's
        displayed changes. Each nav item maps to one page; Metadata
        Generator / Smart Workflow / Prompt Generator all share the SAME
        underlying page (built by _build_content, unchanged) — clicking
        between them just drives its existing workflow/mode toggles, so
        none of that logic is duplicated."""
        shell=ctk.CTkFrame(self,fg_color=BG1,corner_radius=0)
        shell.grid(row=1,column=0,sticky="nsew")
        shell.grid_columnconfigure(0,weight=0)
        shell.grid_columnconfigure(1,weight=1)
        shell.grid_rowconfigure(0,weight=1)

        self._page_area=ctk.CTkFrame(shell,fg_color=BG1,corner_radius=0)
        self._page_area.grid(row=0,column=1,sticky="nsew")
        self._page_area.grid_columnconfigure(0,weight=1)
        self._page_area.grid_rowconfigure(0,weight=1)
        self._pages={}
        self._nav_btns={}
        self._nav_active="dashboard"

        self._build_global_nav(shell)     # needs self._pages to exist first (buttons wired below)
        self._build_content()             # existing sidebar+main pair -> the "generator" page
        self._build_dashboard_page()
        self._build_prompt_to_prompt_page()
        self._build_simple_pages()

        self._nav_to("dashboard")

    def _build_global_nav(self,shell):
        # v0.7: the expandable/collapsible sidebar is gone. There is now
        # exactly one nav style — the icon-over-short-label "compact" one
        # that used to be the collapsed state — permanently. No toggle
        # button, no expanded (icon+long-label, 210px-wide) mode, no
        # runtime width switching. Icons got noticeably bigger (a plain
        # CTkButton can't mix two font sizes in one string, so each item
        # is now a small compound widget — an icon label over a name
        # label — instead of a single-font button) and labels are
        # unchanged text at +1pt.
        NAV_W=100  # was 76 -- +30% per request, so bigger icons/labels
                   # (see _make_nav_item) actually have room to breathe
        # border_width/border_color: a real separator line, not just a
        # color difference. This project's own Meta Generator/Smart
        # Workflow settings sidebar (_sb_frame) uses BG2, and a previous
        # nav rewrite had accidentally reverted nav to plain BG2 too, so
        # on any page besides the Dashboard the two sat flush against
        # each other in the exact same color and read as one panel.
        # NAV_BG (theme-aware — darkens the base color, or lightens it if
        # the base is already very dark/light, so it's never the same
        # tone as whatever's next to it either way) fixes the color; the
        # border means the separation holds even if some future panel
        # happens to pick the same tone by coincidence.
        nav=ctk.CTkFrame(shell,fg_color=NAV_BG,corner_radius=0,width=NAV_W,
            border_width=1,border_color=GLASS_BDR)
        nav.grid(row=0,column=0,sticky="nsew"); nav.grid_propagate(False)
        self._nav_w=NAV_W

        # Exact requested order: Dashboard, Meta Generator, Smart Workflow,
        # Meta Embedder, Prompt Generator, Prompt to Prompt, API Manager,
        # Settings, License, Help.
        items=[
            ("dashboard","🏠","Dashboard","Home",False),
            ("metadata_gen","📝","Meta Generator","Meta",False),
            ("smart","🚀","Smart Workflow","Smart",False),
            ("embedder","📦","Meta Embedder","Embed",False),
            ("prompt_gen","✨","Prompt Generator","Prompt",False),
            ("prompt_to_prompt","🔄","Prompt to Prompt","P2P",False),
            ("ai_providers","🤖","API Manager","API",False),
            ("settings","⚙","Settings","Setting",False),
            ("license","🔑","License","License",True),
            ("help","❓","Help","Help",True),
        ]
        self._nav_items=items
        for key,icon,label,short,coming_soon in items:
            btn=self._make_nav_item(nav,key,icon,short,coming_soon)
            btn.pack(fill="x",padx=6,pady=3)
            self._nav_btns[key]=btn
        self._nav_frame=nav

    def _make_nav_item(self,parent,key,icon,short,coming_soon):
        """One compound nav entry: a bigger icon glyph stacked over its
        short label, in a plain frame that behaves like a button (click
        anywhere on it navigates; hover/active recolor the whole frame).
        Kept as a frame rather than a CTkButton specifically so the icon
        and label can each have their own font size."""
        item=ctk.CTkFrame(parent,fg_color="transparent",corner_radius=8,height=58)
        item.grid_propagate(False)
        icon_lbl=ctk.CTkLabel(item,text=icon,font=ctk.CTkFont("Segoe UI",20),
            fg_color="transparent",text_color=(TXT3 if coming_soon else TXT2))
        icon_lbl.pack(pady=(9,0))
        text_lbl=ctk.CTkLabel(item,text=short,font=ctk.CTkFont("Segoe UI",10,
            "bold" if key not in ("license","help") else "normal"),
            fg_color="transparent",text_color=(TXT3 if coming_soon else TXT2))
        text_lbl.pack(pady=(1,7))
        item._icon_lbl=icon_lbl; item._text_lbl=text_lbl; item._coming_soon=coming_soon
        for w in (item,icon_lbl,text_lbl):
            w.bind("<Button-1>",lambda e,k=key:self._nav_to(k))
            w.bind("<Enter>",lambda e,it=item:self._nav_item_hover(it,True))
            w.bind("<Leave>",lambda e,it=item:self._nav_item_hover(it,False))
            w.configure(cursor="hand2")
        return item

    def _nav_item_hover(self,item,entering):
        if item is self._nav_btns.get(self._nav_active):
            return  # active item keeps its own highlight, hover doesn't override it
        item.configure(fg_color=BG3 if entering else "transparent")

    def _refresh_nav_highlight(self):
        for key,item in self._nav_btns.items():
            active=(key==self._nav_active)
            item.configure(fg_color=GRN_DIM if active else "transparent")
            color=GRN if active else (TXT3 if item._coming_soon else TXT2)
            item._icon_lbl.configure(text_color=color)
            item._text_lbl.configure(text_color=color)

    def _ensure_lazy_page(self,key):
        if key in self._pages or key not in getattr(self,"_lazy_page_specs",{}):
            return
        from ui.embed_window import EmbedContent
        from ui.api_dialog import APIManagerContent
        icon,title=self._lazy_page_specs[key]
        page=ctk.CTkFrame(self._page_area,fg_color=BG1,corner_radius=0)
        page.grid(row=0,column=0,sticky="nsew")
        page.grid_columnconfigure(0,weight=1); page.grid_rowconfigure(1,weight=1)
        hdr=ctk.CTkFrame(page,fg_color=BG1,corner_radius=0)
        hdr.grid(row=0,column=0,sticky="ew",padx=24,pady=(20,10))
        ctk.CTkLabel(hdr,text=f"{icon}  {title}",font=ctk.CTkFont("Segoe UI",20,"bold"),
            fg_color=BG1,text_color=TXT).pack(anchor="w")
        if key=="embedder":
            content=EmbedContent(page,fg_color=BG1)
        elif key=="ai_providers":
            content=APIManagerContent(page,self.prefs,mode="api",fg_color=BG1)
        else:
            content=APIManagerContent(page,self.prefs,mode="settings",fg_color=BG1)
        content.grid(row=1,column=0,sticky="nsew",padx=24,pady=(0,20))
        self._pages[key]=page

    def _nav_to(self,key):
        """The single switchboard for every workspace. Raising a page
        never touches any other page's widgets or running work — a
        Standard/Smart Workflow batch, or an Embed run, keeps going
        exactly as it was regardless of which page this shows."""
        self._ensure_lazy_page(key)
        self._nav_active=key
        self._refresh_nav_highlight()
        page=self._pages.get(key)
        if page is None:
            return
        page.tkraise()
        if key=="dashboard":
            self._dashboard_page.refresh()
            self._dash_ctrl_frame.grid()
        else:
            self._dash_ctrl_frame.grid_remove()
        if key=="smart":
            self._set_workflow("smart")
        elif key=="metadata_gen":
            self._set_workflow("standard"); self._set_mode("meta")
        elif key=="prompt_gen":
            self._set_workflow("standard"); self._set_mode("prompt")

    def _stop_all(self):
        """Pauses every running workflow in place — never closes anything,
        never discards progress. Standard Workflow's own Stop already
        preserves partial results; Smart Workflow's own Stop already saves
        resumable state — this just triggers both of those, plus an
        Embed run in progress, whichever are actually active."""
        stopped_any=False
        if getattr(self,"ai_running",False):
            self.ai_stop_flag=True; stopped_any=True
        sw=getattr(getattr(self,"_smart_frame",None),"pipeline",None)
        if sw and getattr(sw,"stage",None) and sw.stage!="complete":
            sw.stop(); stopped_any=True
        if stopped_any:
            self.set_status("⏹  Stop All — every running workflow was paused.",AMB_BTN)
        else:
            self.set_status("Nothing is currently running.",TXT3)

    def _refresh_to_default_view(self):
        """Refreshes the CURRENT window back to its default view — never
        touches any running process. On Dashboard this re-pulls the
        latest stats; it does not stop, pause, or restart anything."""
        self._dashboard_page.refresh()
        self.set_status("⟳  Refreshed.",TXT2)

    def _build_simple_pages(self):
        """Embedder/AI Providers/Settings are built LAZILY — the first
        time each is actually navigated to (see _ensure_lazy_page in
        _nav_to), not here at startup. Profiling showed constructing all
        three eagerly cost ~1s of App() startup time for pages most
        sessions may never even visit in a given run. Once built, each
        is cached exactly like every other page (never rebuilt, never
        destroyed) — this only changes WHEN construction happens, not
        the reuse guarantee. License/Help are trivial placeholders, kept
        eager since they cost nothing worth deferring."""
        self._lazy_page_specs={
            "embedder":("📦","Meta Embedder"),
            "ai_providers":("🤖","API Manager"),
            "settings":("⚙","Settings"),
        }

        placeholder_specs=[
            ("license","🔑","License","Coming soon.",None,None),
            ("help","❓","Help","Coming soon.",None,None),
        ]
        for key,icon,title,desc,btn_text,cmd in placeholder_specs:
            page=ctk.CTkFrame(self._page_area,fg_color=BG1,corner_radius=0)
            page.grid(row=0,column=0,sticky="nsew")
            wrap=ctk.CTkFrame(page,fg_color="transparent",corner_radius=0)
            wrap.place(relx=0.5,rely=0.42,anchor="center")
            ctk.CTkLabel(wrap,text=icon,font=ctk.CTkFont("Segoe UI",40),
                fg_color=BG1,text_color=TXT2).pack()
            ctk.CTkLabel(wrap,text=title,font=ctk.CTkFont("Segoe UI",20,"bold"),
                fg_color=BG1,text_color=TXT).pack(pady=(10,4))
            ctk.CTkLabel(wrap,text=desc,font=ctk.CTkFont("Segoe UI",12),
                fg_color=BG1,text_color=TXT3).pack(pady=(0,16))
            self._pages[key]=page

    def _build_dashboard_page(self):
        self._dashboard_page=DashboardPage(self._page_area,self)
        self._dashboard_page.grid(row=0,column=0,sticky="nsew")
        self._pages["dashboard"]=self._dashboard_page

    def _build_prompt_to_prompt_page(self):
        self._p2p_page=PromptToPromptPanel(self._page_area,self)
        self._p2p_page.grid(row=0,column=0,sticky="nsew")
        self._pages["prompt_to_prompt"]=self._p2p_page

    def _toggle_sidebar(self):
        self._sb_collapsed=not self._sb_collapsed
        if self._sb_collapsed:
            self._sb_frame.grid_remove()
            self._sb_expand_btn.place(relx=0.0,rely=0.5,anchor="w")
        else:
            self._sb_frame.grid()
            self._sb_expand_btn.place_forget()

    def _build_content(self):
        content=ctk.CTkFrame(self._page_area,fg_color=BG1,corner_radius=0)
        content.grid(row=0,column=0,sticky="nsew")
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
        self._pages["metadata_gen"]=content
        self._pages["smart"]=content
        self._pages["prompt_gen"]=content

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

    def _build_titlebar(self):
        tb=ctk.CTkFrame(self,fg_color=BG2,corner_radius=0,height=54)
        tb.grid(row=0,column=0,sticky="ew"); tb.grid_propagate(False)
        tb.grid_columnconfigure(2,weight=1)
        logo_img=None
        try:
            from core.utils import _icon_paths
            _,png=_icon_paths()
            if png:
                from PIL import Image
                logo_img=ctk.CTkImage(Image.open(png),size=(28,28))
        except Exception:
            logo_img=None
        if logo_img is not None:
            self._logo_img_ref=logo_img  # keep alive
            ctk.CTkLabel(tb,text="",image=logo_img,fg_color=BG2
            ).grid(row=0,column=0,padx=(16,8),pady=13)
        else:
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

        # "Metadata AI" / "Embed" used to sit here permanently — removed.
        # Metadata AI's own controls now only appear inside the Metadata
        # Generator page, and Embed only inside the Embedder page.

        # Dashboard-only controls: Stop All, then Refresh, then the online
        # indicator (in that left-to-right order) — only visible while the
        # Dashboard page is active (toggled from _nav_to).
        dash_ctrl=ctk.CTkFrame(tb,fg_color=BG2,corner_radius=0)
        dash_ctrl.grid(row=0,column=3,padx=(0,10),pady=12)
        self._stopall_btn=ctk.CTkButton(dash_ctrl,text="⏹  Stop All",width=96,height=30,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=RED_DIM,hover_color=RED_BTN_H,text_color=RED_BTN,
            border_width=0,corner_radius=8,command=self._stop_all)
        self._stopall_btn.pack(side="left",padx=(0,6))
        self._refresh_btn=ctk.CTkButton(dash_ctrl,text="⟳  Refresh",width=96,height=30,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,
            border_width=0,corner_radius=8,command=self._refresh_to_default_view)
        self._refresh_btn.pack(side="left")
        self._dash_ctrl_frame=dash_ctrl
        self._dash_ctrl_widgets=[self._stopall_btn,self._refresh_btn]

        of=ctk.CTkFrame(tb,fg_color=BG3,corner_radius=20)
        of.grid(row=0,column=4,padx=(0,16),pady=12)
        self._online_dot=ctk.CTkLabel(of,text="●",font=ctk.CTkFont("Segoe UI",16),
            text_color=GRN,fg_color=BG3); self._online_dot.pack(side="left",padx=(12,4),pady=4)
        self._online_lbl=ctk.CTkLabel(of,text="Online",font=ctk.CTkFont("Segoe UI",12,"bold"),
            text_color=TXT2,fg_color=BG3); self._online_lbl.pack(side="left",padx=(0,12),pady=4)
        self._dash_ctrl_frame.grid_remove()  # hidden until Dashboard is active
        cr=ctk.CTkFrame(tb,fg_color=BG2,corner_radius=0)
        cr.grid(row=0,column=5,padx=(0,18),sticky="e")
        ctk.CTkLabel(cr,text="All Rights Reserved By",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=TXT2,fg_color=BG2).pack(anchor="e")
        ctk.CTkLabel(cr,text="© HASIBNIKON",font=ctk.CTkFont("Segoe UI",13,"bold"),
            text_color=TXT,fg_color=BG2).pack(anchor="e")

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

        # API config — quick popup shortcut (the full inline page version
        # lives behind the nav's API Manager item; this is the compact
        # popup for quick access while working in Metadata Generator).
        ctk.CTkButton(inner,text="🔑  API Manager",
            font=ctk.CTkFont("Segoe UI",12,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,
            height=38,corner_radius=8,command=self._open_api_mgr
        ).pack(fill="x",padx=10,pady=(10,3))
        self._api_lbl=ctk.CTkLabel(inner,text="",font=ctk.CTkFont("Segoe UI",10),
            text_color=TXT3,fg_color=BG2); self._api_lbl.pack(anchor="w",padx=12,pady=(0,4))
        self._refresh_api_lbl()

        # Workflow selection now lives ONLY in the global nav (Smart
        # Workflow / Meta Generator are separate nav items) — no more
        # side-by-side toggle buttons duplicating that choice here.
        self._div(inner)
        self.workflow_var=StringVar(value="standard")

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
        ctk.CTkSlider(cf,from_=1,to=20,number_of_steps=19,variable=self.ai_concurrency_var,
            progress_color=GRN,fg_color=BG3,button_color=TXT,button_hover_color="#ddffdd",height=14,
            command=lambda v:(self._conc_lbl.configure(text=f"{int(v)}x"),self._save_settings())
        ).pack(fill="x",pady=(3,0))

        # Metadata/Prompt mode is now chosen by which nav item you're on
        # (Meta Generator vs Prompt Generator) — no in-sidebar toggle.

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
        if self.current_mode==mode:
            return  # already here — do NOT clear/reset a possibly in-progress batch
        self.current_mode=mode
        if mode=="meta":
            self._prompt_sf.pack_forget()
            self._meta_sf.pack(fill="x",before=self._sl_anchor)
        else:
            self._meta_sf.pack_forget()
            self._prompt_sf.pack(fill="x",before=self._sl_anchor)
        self._clear_results()
        for p in list(self._all_paths):
            self._results[p]={"status":"waiting"}
        self._render_page()

    def _set_workflow(self,mode):
        if self.workflow_var.get()==mode:
            return  # already here — avoid redundant raise/refresh churn
        self.workflow_var.set(mode)
        active=mode=="smart"
        if active:
            self._smart_frame.tkraise()
            self._smart_frame.refresh_file_count()
        else:
            self._smart_frame.lower()

    def _check_smart_resume(self):
        """Startup check for an interrupted Smart Workflow run — per spec,
        this only ever looks at the folder the last run was actually
        working in (saved to prefs the moment a run starts), never
        anything Standard Workflow touches."""
        folder=self.prefs.get("last_smart_folder","")
        if not folder: return
        resumable=smart_state.find_resumable(folder)
        if not resumable: return
        if messagebox.askyesno("Resume previous Smart Workflow?",
                f"An unfinished Smart Workflow run was found in:\n{folder}\n\nResume it?",
                parent=self):
            self._set_workflow("smart")
            self._smart_frame.resume_from(resumable,folder)

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
        APIManagerWindow(self,self.prefs,on_close=self._refresh_api_lbl,mode="api")

    def _open_settings(self):
        # Theme lives ONLY here now — nowhere else in the app opens it.
        APIManagerWindow(self,self.prefs,on_close=self._refresh_api_lbl,mode="settings")

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

        self._embed_gen_btn=ctk.CTkButton(btn_f,text="📋  Embed",width=92,height=32,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,
            corner_radius=8,command=self._open_embed)
        # Not packed yet — only shown by _gen_done() after a full, natural
        # completion (never mid-run, never after Stop/Pause), and hidden
        # again by Clear All / a fresh Generate click.

        self._clear_all_btn=ctk.CTkButton(btn_f,text="🗑  Clear All",width=96,height=32,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=RED_DIM,hover_color=RED_BTN_H,text_color=RED_BTN,
            border_width=1,border_color=RED_BTN,corner_radius=8,
            command=lambda:self._clear_all(confirm=True))
        self._clear_all_btn.pack(side="left",padx=(0,5))

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

        # Progress bar + Processing Queue stats strip (Current File is
        # already the progress label's own message, e.g. "[12/40] x.jpg";
        # this second row adds Completed/Remaining/ETA/Avg/Model/Retries,
        # visible only while a batch is actually running).
        prog=ctk.CTkFrame(main,fg_color=BG1,corner_radius=0,height=58)
        prog.grid(row=2,column=0,sticky="ew"); prog.grid_propagate(False)
        prog.grid_columnconfigure(1,weight=1)
        self._prog_lbl=ctk.CTkLabel(prog,text="● System Ready.",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,fg_color=BG1)
        self._prog_lbl.grid(row=0,column=0,padx=(10,8),pady=(4,0))
        self._prog_bar=ctk.CTkProgressBar(prog,progress_color=GRN,fg_color=BG3,
            border_width=1,border_color=GLASS_BDR,height=14,corner_radius=7)
        self._prog_bar.grid(row=0,column=1,sticky="ew",pady=(9,0),padx=(0,8)); self._prog_bar.set(0)
        self._prog_pct=ctk.CTkLabel(prog,text="",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=GRN,fg_color=BG1,width=36)
        self._prog_pct.grid(row=0,column=2,padx=(0,8),pady=(4,0))

        self._proc_stats_lbl=ctk.CTkLabel(prog,text="",
            font=ctk.CTkFont("Segoe UI",9),text_color=TXT3,fg_color=BG1,anchor="w")
        self._proc_stats_lbl.grid(row=1,column=0,columnspan=3,sticky="ew",padx=10,pady=(2,4))

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

        # Total-count indicator — shows "N images" normally, or the live
        # Completed/Remaining while generating (Working View, which used
        # to live in this spot as a toggle, is gone — the results grid
        # just auto-scrolls to the newest card as it arrives instead;
        # see _render_page / _scroll_to_bottom).
        self._page_lbl=ctk.CTkLabel(vs,text="0 images",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT2,fg_color="transparent")
        self._page_lbl.pack(side="left",padx=(10,0))
        self._refresh_view_settings_ui()

        self._gen_scroll=ctk.CTkScrollableFrame(gen,fg_color="transparent",
            scrollbar_button_color=BG3,scrollbar_button_hover_color=BG4,corner_radius=0)
        self._gen_scroll.grid(row=1,column=0,sticky="nsew",padx=6,pady=(4,6))
        # yscrollincrement=1 (not the old *8 speed hack): Tk's canvas
        # quantizes yview_moveto to the nearest multiple of this value on
        # at least some platforms/versions, which used to leave the
        # scroll-button clamp (see _scroll_results) off by up to one
        # increment. 1px makes that quantization a non-issue; the actual
        # scroll distance per click is computed directly from real card
        # geometry now, not from this value.
        try:
            self._gen_scroll._parent_canvas.configure(yscrollincrement=1)
        except Exception:
            pass
        # Click-and-drag scrolling — hold the left mouse button anywhere
        # on a card (or empty canvas background) and move up/down to
        # scroll live, like a touch-scroll gesture. Cards fill nearly the
        # entire canvas width/height, so binding only on the canvas
        # itself leaves almost no exposed background to grab — this also
        # binds on every card widget directly (see _bind_drag_scroll,
        # called from _render_page after each card is built/rebound).
        # Interactive sub-widgets (textboxes, the Regenerate button)
        # aren't touched, so editing/clicking still works exactly as
        # before; a genuine drag only engages once the pointer has moved
        # past a small threshold, so a plain click/edit never mis-fires
        # as a scroll.
        self._drag_scroll_active=False
        def _drag_start(event):
            self._drag_scroll_active=False  # not yet — only after real movement
            self._drag_start_y=event.y_root
            try:
                self._drag_start_frac=self._gen_scroll._parent_canvas.yview()[0]
            except Exception:
                self._drag_start_frac=0.0
        def _drag_motion(event):
            canvas=self._gen_scroll._parent_canvas
            dy=event.y_root-getattr(self,"_drag_start_y",event.y_root)
            if not self._drag_scroll_active:
                if abs(dy)<6:
                    return  # still just a click/edit, not a drag yet
                self._drag_scroll_active=True
            try:
                bbox=canvas.bbox("all")
                if not bbox:
                    return
                total_h=bbox[3]-bbox[1]
                view_h=canvas.winfo_height()
                if total_h<=view_h:
                    return
                new_frac=self._drag_start_frac-(dy/total_h)
                canvas.yview_moveto(max(0.0,min(1.0,new_frac)))
            except Exception:
                pass
        def _drag_end(event):
            self._drag_scroll_active=False
        self._drag_scroll_handlers=(_drag_start,_drag_motion,_drag_end)
        self._gen_scroll._parent_canvas.bind("<ButtonPress-1>",_drag_start,add="+")
        self._gen_scroll._parent_canvas.bind("<B1-Motion>",_drag_motion,add="+")
        self._gen_scroll._parent_canvas.bind("<ButtonRelease-1>",_drag_end,add="+")

        self._gen_empty_lbl=ctk.CTkLabel(self._gen_scroll,
            text="Results will appear here after generation.",
            font=ctk.CTkFont("Segoe UI",12),text_color=TXT3,fg_color="transparent")
        self._gen_empty_lbl.place(x=0,y=40,relwidth=1)

        # Floating scroll buttons — a mouse-wheel alternative for the
        # current page's content, NOT page navigation (that's the ◀/▶
        # Page Nav controls in the header). Placed on `gen` (not
        # `_gen_scroll`) so they float above the list instead of
        # scrolling away with it.
        self._scroll_down_btn=ctk.CTkButton(gen,text="▼",width=36,height=36,
            font=ctk.CTkFont("Segoe UI",14,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,
            border_width=1,border_color=GLASS_BDR,corner_radius=18,
            command=lambda:self._scroll_results(1))
        # x=-38 (not -14): CTkScrollableFrame's own vertical scrollbar sits
        # ~20px wide right at the results panel's right edge. At -14 these
        # floating buttons sat directly on top of the bottom of that
        # scrollbar's track — covering its draggable thumb and eating its
        # clicks whenever the thumb happened to be near the bottom, which
        # is exactly when it's most likely to need dragging. -38 clears it
        # with a small gap.
        self._scroll_down_btn.place(relx=1.0,rely=1.0,x=-38,y=-14,anchor="se")
        self._scroll_up_btn=ctk.CTkButton(gen,text="▲",width=36,height=36,
            font=ctk.CTkFont("Segoe UI",14,"bold"),
            fg_color=BG3,hover_color=BG4,text_color=TXT2,
            border_width=1,border_color=GLASS_BDR,corner_radius=18,
            command=lambda:self._scroll_results(-1))
        self._scroll_up_btn.place(relx=1.0,rely=1.0,x=-38,y=-56,anchor="se")
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

        # ── Smart Workflow (separate module, never touches the above) ──
        # Occupies the same grid cells as the topbar/upload-zone/progress-
        # bar/results rows (0-3), raised over all of them with tkraise()/
        # lowered with lower() — neither mode's widgets are ever destroyed
        # or rebuilt on switch. Files are imported the same way regardless
        # of which mode is currently showing (drag-and-drop/Browse write
        # straight to self._all_paths); switch to Smart Workflow once
        # they're loaded to hand that same file list to its own pipeline.
        self._smart_frame=SmartWorkflowPanel(self._main,self)
        self._smart_frame.grid(row=0,column=0,rowspan=4,sticky="nsew")
        self._smart_frame.lower()
        # Smart Workflow's panel is raised OVER every Standard-Workflow
        # widget registered above (ws, main, gen, etc.) — since it's on
        # top, drops landing on it never reached those widgets, and the
        # panel itself was never registered as its own drop target. That's
        # the "drag & drop does nothing / stays at 0 files loaded while on
        # Smart Workflow" bug. Register the panel and its scrollable body
        # too, routed through the exact same _on_drop -> _add_images path.
        self._register_drop_targets([self._smart_frame,self._smart_frame._body])
        self.after(400,self._check_smart_resume)

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
        self._validate_and_add_dropped(expanded)

    def _validate_and_add_dropped(self,paths):
        """Confirms every dropped file is actually a complete, readable
        image (see wait_stable_and_validate_image's docstring for the
        real bug this exists to catch — a browser-drag race that used to
        produce a card with no thumbnail, then a misleading "all API
        keys failed" error at generation time) before handing accepted
        files to _add_images. The check itself can briefly block (up to
        ~1s per file if one needs a moment to finish being written), so
        it runs on a background thread, never the UI thread."""
        candidates=[p for p in paths
                    if os.path.splitext(p)[1].lower() in ALL_SUPPORTED_EXTS]
        if not candidates:
            self._add_images(paths)  # nothing recognized -- let _add_images's
            return                    # own filtering produce its usual no-op
        def _work():
            good=[]; bad=[]
            for p in candidates:
                ext=os.path.splitext(p)[1].lower()
                if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
                    # Not something PIL can open at all -- always was, and
                    # still is, accepted without this check.
                    good.append(p); continue
                ok,reason=wait_stable_and_validate_image(p)
                if ok: good.append(p)
                else: bad.append((p,reason))
            def _apply():
                if good:
                    self._add_images(good)
                if bad:
                    lines="\n".join(f"•  {os.path.basename(p)} — {reason}" for p,reason in bad[:6])
                    more=f"\n…and {len(bad)-6} more" if len(bad)>6 else ""
                    messagebox.showwarning("Some files couldn't be imported",
                        "These didn't come through as complete, readable images — this "
                        "usually means they were dragged directly from a web browser rather "
                        "than from a file already saved to disk (the browser can hand over a "
                        "file before it's actually finished writing it). Try saving/"
                        "downloading the image first, then dragging it in from your file "
                        "manager instead:\n\n"+lines+more,parent=self)
            self._ui_action_queue.put(_apply)
        threading.Thread(target=_work,daemon=True).start()

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
        # Keep Smart Workflow's "N files loaded" label live even though
        # its widgets are never rebuilt — without this it stayed stuck at
        # "0 files loaded" after a drop while Smart Workflow was the
        # visible panel, even once the drop itself worked.
        if hasattr(self,"_smart_frame"):
            self._smart_frame.refresh_file_count()
        self._prefetch_all_thumbnails(new)

    def _prefetch_all_thumbnails(self,paths):
        """Warms the disk cache for the WHOLE imported batch right away,
        not just whichever page happens to be on screen — so paging
        through a large batch later hits the cache instantly instead of
        generating thumbnails on demand per page. Runs on a small fixed
        pool of background threads (never the main thread, never touches
        any widget) so a 5,000-image import can't spawn thousands of
        threads at once."""
        def _worker(chunk):
            for p in chunk:
                prefetch_thumb_to_cache(p,size=(MetaResultCard.THUMB_SIZE,MetaResultCard.THUMB_SIZE))
                prefetch_thumb_to_cache(p,min_edge=CompactEditCard.THUMB_MIN_EDGE)
        n_workers=4
        chunks=[paths[i::n_workers] for i in range(n_workers)]
        for c in chunks:
            if c:
                threading.Thread(target=_worker,args=(c,),daemon=True).start()

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
                if hasattr(self,"_smart_frame"):
                    self._smart_frame.refresh_file_count()
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

    # ── Rendering (single continuously-scrolling grid renderer, all
    # view modes, no pagination) ──

    def _on_results_resize(self,event=None):
        """Debounced: recompute Compact's auto-fit column count on resize,
        and only actually re-render if that count changed (otherwise a
        window drag would re-render the whole page on every pixel).
        Guards on width FIRST, before even scheduling the debounce timer
        — a pure window move (not resize) still fires <Configure> on
        child widgets in some window managers even though nothing about
        this widget's own size changed, which was showing up as cards
        deforming/reforming just from dragging the window around."""
        w=getattr(event,"width",None) if event is not None else None
        if w is not None and w==getattr(self,"_last_results_width",None):
            return
        if w is not None:
            self._last_results_width=w
        if self._resize_after_id:
            try: self.after_cancel(self._resize_after_id)
            except Exception: pass
        self._resize_after_id=self.after(150,self._maybe_rerender_on_resize)

    def _maybe_rerender_on_resize(self):
        self._resize_after_id=None
        new_cols=self._auto_grid_cols()
        if new_cols!=getattr(self,"_last_auto_cols",None):
            self._render_page()

    def _auto_grid_cols(self):
        """Compact still auto-fits by width (3 cols in a smaller window, 4
        in a larger one — there's no crisp "maximized" concept that
        matters for a narrow card). Expanded is different by request: 2
        columns unless the window is ACTUALLY maximized ("full window
        mode"), in which case 3 — a real maximize/restore state check,
        not a width guess, since a wide-but-not-maximized window should
        still only get 2."""
        try:
            w=self._gen_scroll.winfo_width() or 1
        except Exception:
            w=1
        if self.view_mode_var.get()=="compact":
            return 4 if w>=900 else 3
        try:
            maximized=self.state()=="zoomed"
        except Exception:
            maximized=False
        return 3 if maximized else 2

    def _bind_drag_scroll(self,card):
        """Attaches the same drag-to-scroll gesture (see its setup in
        _build_content) onto every non-interactive widget in the card,
        recursively — not just the card's own background and the
        thumbnail. Per feedback, clicking anywhere that isn't an actual
        control should be able to start a drag-scroll, not just those two
        spots. CTkTextbox (the editable title/description fields),
        CTkEntry, and CTkButton (Regenerate, Expand/Collapse, copy/paste
        icons) are skipped so typing and clicking those still works
        exactly as before — everything else (labels, the status badge,
        filename, snippet text, the plain background frames wrapping
        them) becomes a drag handle. A card is only ever bound once, right
        after it's built — rebinding an existing pooled card to a new
        path doesn't rebuild its widget tree, so nothing needs re-binding
        then."""
        if not hasattr(self,"_drag_scroll_handlers"):
            return
        start,motion,end=self._drag_scroll_handlers
        skip_types=(ctk.CTkTextbox,ctk.CTkEntry,ctk.CTkButton,
                    tkinter.Text,tkinter.Entry)
        def _bind_recursive(w):
            if isinstance(w,skip_types):
                return
            try:
                w.bind("<ButtonPress-1>",start,add="+")
                w.bind("<B1-Motion>",motion,add="+")
                w.bind("<ButtonRelease-1>",end,add="+")
            except Exception:
                pass
            try:
                children=w.winfo_children()
            except Exception:
                children=[]
            for c in children:
                _bind_recursive(c)
        _bind_recursive(card)

    def _fade_in_card(self,card):
        """Fade-in for a freshly created card widget (only ever called the
        first time a pool slot is built — reused/rebound cards never
        re-fade). Rewritten per feedback that the first version was "very
        short and barely noticeable": that version only interpolated the
        card frame's own fg_color/border_color from BG1 to GLASS, but
        those two colors are only 10 RGB points apart to begin with (by
        design — GLASS is meant to read as a subtle step up from the
        background, not a bright card) — animating between two colors
        that close together is essentially invisible regardless of
        duration or step count, and the card's own frame is also mostly
        hidden behind its own child widgets (thumbnail, text) which
        weren't fading at all, so most of the card's visible area
        appeared instantly anyway.
        This version: (1) longer and smoother — 16 steps at 20ms = 320ms
        total, up from 6 steps at 30ms = 180ms; (2) the border does a
        genuine two-phase flash-then-settle through the accent color
        (BG1 → accent → resting GLASS_BDR) instead of a flat tiny-delta
        lerp, since the accent color has real contrast against the
        background to actually be seen and read as motion, not just a
        recolor."""
        end_fg=GLASS
        steps=16
        def _step(i):
            try:
                if not card.winfo_exists(): return
                t=i/steps
                fg=_lerp_hex(BG1,end_fg,t)
                if t<0.55:
                    bdr=_lerp_hex(BG1,GLASS_BDR_AC,t/0.55)
                else:
                    bdr=_lerp_hex(GLASS_BDR_AC,GLASS_BDR,(t-0.55)/0.45)
                card.configure(fg_color=fg,border_color=bdr,border_width=1)
                if i<steps:
                    card.after(20,lambda:_step(i+1))
                else:
                    card.configure(border_width=1,border_color=GLASS_BDR)
            except Exception:
                pass
        _step(0)

    def _render_page(self):
        """The one and only card renderer. Reuses a pool of already-built
        card widgets instead of destroying and rebuilding them every time
        — measured at ~6.5s to rebuild 50 full Expanded cards from
        scratch on a 372-image batch, which is what made page navigation,
        grid-column changes, and even the odd redundant re-render feel
        broken (frozen main thread => stuck scrollbar/arrows, and the
        visible 'deform then reform' flash as the old cards vanished
        before the new ones appeared). Widget CONSTRUCTION was always the
        expensive part, never the data — so now construction only
        happens the first time a pool slot is needed, or when swapping
        between Expanded and Compact (genuinely different widget
        classes, so those pools are kept separate and simply hidden/
        shown rather than shared)."""
        total=len(self._all_paths)
        compact=self.view_mode_var.get()=="compact"
        pool_attr="_compact_pool" if compact else "_expanded_pool"
        other_pool_attr="_expanded_pool" if compact else "_compact_pool"
        if not hasattr(self,"_compact_pool"): self._compact_pool=[]
        if not hasattr(self,"_expanded_pool"): self._expanded_pool=[]
        pool=getattr(self,pool_attr)
        other_pool=getattr(self,other_pool_attr)
        for c in other_pool:
            c.grid_remove()

        if total==0:
            for c in pool: c.grid_remove()
            self._card_by_path={}
            self._gen_empty_lbl.place(x=0,y=40,relwidth=1)
            self._page_lbl.configure(text="0 images")
            self._refresh_view_settings_ui()
            return
        self._gen_empty_lbl.place_forget()

        cols=self._auto_grid_cols()
        # Column count changing (window resized, or Expanded just crossed
        # the maximize threshold) means every already-visible card is
        # about to jump to a new row/column position, and each one's
        # available width just changed too — which can make its own text
        # rewrap at the same instant. Per feedback that this "deformation
        # and reformation" during a reflow looks bad, cards that were
        # already on screen get a quick dim-then-restore flash timed
        # around the regrid instead of just snapping, so the layout jump
        # reads as one soft transition rather than an abrupt jolt. Only
        # applies when cols actually changed — a normal render (new card
        # arriving, status update) never triggers it.
        cols_changed=(cols!=getattr(self,"_last_auto_cols",None)
                      and getattr(self,"_last_auto_cols",None) is not None)
        self._last_auto_cols=cols

        # No pagination and — per the new card-creation workflow — no
        # placeholder cards either: a path only ever gets a card once its
        # metadata is actually done (or failed). "waiting"/"working"
        # paths simply have no widget at all until then; see the
        # Processing Queue panel (the progress-bar stats strip) for what
        # to show meanwhile. (Working View, which used to be a separate
        # toggle for this, is gone — every finished image just renders
        # here directly, and the grid auto-scrolls to follow the newest
        # one in; see _scroll_to_bottom below.)
        all_paths_set=set(self._all_paths)
        page_paths=[p for p in self._completion_order
                    if p in all_paths_set
                    and self._results.get(p,{}).get("status") in ("done","failed")]

        if not page_paths:
            for c in pool: c.grid_remove()
            self._card_by_path={}
            self._gen_empty_lbl.configure(text=(
                "Generating… finished results will appear here as each image completes."
                if self.ai_running else
                "Results will appear here after generation."))
            self._gen_empty_lbl.place(x=0,y=40,relwidth=1)
            self._page_lbl.configure(text="0 images")
            self._refresh_view_settings_ui()
            return
        self._gen_empty_lbl.place_forget()

        for c in range(cols):
            self._gen_scroll.grid_columnconfigure(c,weight=1)

        show_desc=getattr(self,'_show_desc_mode',True)
        old_card_by_path=self._card_by_path
        new_card_by_path={}
        used_paths=set(page_paths)
        # Sync outgoing cards' edits before they're either rebound to a
        # new path or hidden — same safety this always had.
        for p,c in old_card_by_path.items():
            self._sync_card_edits(p,c)

        any_new_card=False
        for i,path in enumerate(page_paths):
            result=self._results.get(path,{"status":"waiting"})
            if i < len(pool):
                card=pool[i]
                if card.path!=path:
                    card.rebind(path,result,on_redo=lambda p=path:self._redo_single(p))
                    if compact:
                        self._request_thumb(path,card._tlbl,min_edge=CompactEditCard.THUMB_MIN_EDGE)
                    else:
                        self._request_thumb(path,card._tlbl,size=(MetaResultCard.THUMB_SIZE,MetaResultCard.THUMB_SIZE))
                else:
                    card.apply_result(result)
            else:
                if compact:
                    card=CompactEditCard(self._gen_scroll,path,result,
                        on_redo=lambda p=path:self._redo_single(p),mode=self.current_mode,
                        show_desc=show_desc,request_thumb=self._request_thumb)
                else:
                    card=MetaResultCard(self._gen_scroll,path,result,
                        on_redo=lambda p=path:self._redo_single(p),mode=self.current_mode,
                        request_thumb=self._request_thumb,expanded=True,
                        on_toggle_expand=None,show_desc=show_desc)
                self._bind_drag_scroll(card)
                pool.append(card)
                self._fade_in_card(card)
                any_new_card=True
            card.grid(row=i//cols,column=i%cols,sticky="new",padx=4,pady=4)
            new_card_by_path[path]=card

        # Any pool slots beyond what this page needs just hide — they
        # stay alive for the next page/column change to reuse, instead
        # of being destroyed and having to be rebuilt later.
        for j in range(len(page_paths),len(pool)):
            pool[j].grid_remove()

        self._card_by_path=new_card_by_path
        self._page_lbl.configure(text=f"{total} image{'s' if total!=1 else ''}")
        self._refresh_view_settings_ui()
        if cols_changed:
            self._flash_regrid_transition(list(new_card_by_path.values()))
        if any_new_card:
            # Follow the newest card in automatically — this is the direct
            # replacement for the old Working View toggle: instead of a
            # separate mode that only showed in-flight cards, the grid
            # itself just keeps the latest result in view as it arrives.
            self.after(30,self._scroll_to_bottom)

    def _flash_regrid_transition(self,cards):
        """Soft dim-then-restore flash applied to every visible card at
        once, timed around a column-count change (see _render_page) —
        masks the instant jump of every card relocating to a new
        row/column and rewrapping to a new width in the same frame, per
        feedback that the raw reflow "does not look good". One shared
        step counter driving every card's color together (not N
        independent per-card animations) keeps this cheap even for a
        large grid — 8 steps at 18ms = ~145ms total, quick enough not to
        get in the way of actually using the app."""
        steps=8
        def _step(i):
            t=i/steps
            # Dip down toward the background for the first half, then
            # climb back to the resting GLASS color for the second —
            # a triangle wave, not a one-way fade, since these cards
            # already have their real content and just need to visually
            # "settle" into the new layout rather than announce arrival.
            depth=1-abs((t-0.5)*2)  # 0 -> 1 -> 0 across the animation
            fg=_lerp_hex(GLASS,BG2,depth*0.6)
            for card in cards:
                try:
                    if card.winfo_exists():
                        card.configure(fg_color=fg)
                except Exception:
                    pass
            if i<steps and cards:
                try:
                    self.after(18,lambda:_step(i+1))
                except Exception:
                    pass
            else:
                for card in cards:
                    try:
                        if card.winfo_exists(): card.configure(fg_color=GLASS)
                    except Exception:
                        pass
        if cards:
            _step(0)

    def _scroll_to_bottom(self):
        # Force the scrollregion to sync to the CURRENT content bbox
        # first — CTkScrollableFrame's own lazy update (bound to its
        # inner frame's <Configure>) doesn't reliably catch up in time
        # right after a card was just added, which left yview_moveto
        # trusting a stale/empty scrollregion and silently doing nothing.
        # Same fix as _scroll_results already needed for the same reason.
        try:
            canvas=self._gen_scroll._parent_canvas
            bbox=canvas.bbox("all")
            if bbox: canvas.configure(scrollregion=bbox)
            canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _maybe_refresh_grid(self,force=False):
        """Debounced re-render: collapses a burst of near-simultaneous
        status transitions (e.g. concurrency=20 all finishing within the
        same tick) into one render instead of twenty, without losing any
        of them (each call reschedules to the same short delay, so the
        last one in a burst wins and always fires). force=True is set by
        _update_card whenever a path just finished and doesn't have a
        card yet — since cards are never created for waiting/working
        paths (see _render_page), a newly "done" image needs an actual
        render to appear at all. (This used to also fire on every tick
        while a since-removed Working View toggle was on; it's only ever
        force-triggered now.)"""
        if not force:
            return
        if getattr(self,"_grid_refresh_after_id",None):
            try: self.after_cancel(self._grid_refresh_after_id)
            except Exception: pass
        self._grid_refresh_after_id=self.after(60,self._render_page)

    def _rebuild_card_pools(self):
        """Full teardown — only needed when the underlying file list or
        mode changes in a way that makes every pooled card's content
        meaningless to reuse (Clear All, a brand-new import replacing
        everything, or switching between Metadata/Prompt mode which uses
        different field sets). Grid-column changes and page navigation
        deliberately do NOT call this — see _render_page."""
        for pool_attr in ("_expanded_pool","_compact_pool"):
            pool=getattr(self,pool_attr,None)
            if not pool: continue
            for c in pool:
                try: c.destroy()
                except Exception: pass
            setattr(self,pool_attr,[])
        self._card_by_path={}

    def _scroll_results(self,direction):
        """Move by ~2 card-heights per click, computed fresh from the
        CURRENT canvas content every time — not from Tk's yscrollincrement
        (platform/version-dependent; on at least one real build it
        defaulted low enough that clicks barely moved at all) and not by
        trusting the canvas's own scrollregion cache (cards keep
        resizing/relaying-out live during generation, and Tk does not
        automatically re-clamp an existing scroll position when
        scrollregion shrinks after the fact — that's what let repeated
        clicking near either end drift into space beyond the actual first
        or last card, with nothing there to show and no snap-back).
        Recomputing bbox("all") and re-deriving + clamping the target
        fraction on every single click makes each click self-correcting
        regardless of what happened to the layout since the last one."""
        canvas=self._gen_scroll._parent_canvas
        try:
            bbox=canvas.bbox("all")
            if not bbox:
                return
            canvas.configure(scrollregion=bbox)  # sync before trusting it below
            total_h=bbox[3]-bbox[1]
            view_h=canvas.winfo_height() or 1
            max_top_px=max(0,total_h-view_h)
            if max_top_px<=0:
                return  # everything already fits -- nothing to scroll, ever
            card_h=self._representative_card_height() or 220
            delta_px=card_h*2*direction
            cur_top_px=canvas.yview()[0]*total_h
            new_top_px=max(0,min(max_top_px,cur_top_px+delta_px))
            canvas.yview_moveto(new_top_px/total_h)
        except Exception:
            pass

    def _representative_card_height(self):
        """A real currently-rendered card's height, used so the scroll
        buttons move a consistent, content-aware distance instead of a
        guessed constant — Expanded and Compact cards are quite
        different heights, and either can also vary with content."""
        for c in self._card_by_path.values():
            try:
                h=c.winfo_height()
                if h>1: return h
            except Exception:
                continue
        return None

    def _set_view_mode(self,mode):
        self.view_mode_var.set(mode)
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
            self._prog_bar.set(0); self._prog_pct.configure(text="")
            self._proc_stats_lbl.configure(text="")
            return
        self._prog_lbl.configure(
            text=msg or f"Generated {d}/{t}  |  {d} successful  |  {failed} failed",
            text_color=TXT2)
        pct=d/t if t else 0
        self._prog_bar.set(pct); self._prog_pct.configure(text=f"{int(pct*100)}%")
        self.p_ok.configure(text=f"✓  {d} done")
        self.p_err.configure(text=f"✗  {failed} failed")
        self.p_pend.configure(text=f"○  {t-d-failed} pending")
        self._gen_count_lbl.configure(text=f"Generated Metadata ({d})")

        # Processing Queue stats strip — Completed/Remaining/Avg/ETA/
        # Model/Retries. Only meaningful (and only shown) while a batch
        # is actually running; blank the rest of the time.
        if self.ai_running and d+failed>0:
            # max(..., 0.05): guards against elapsed coming out ~0 (or, in
            # a race where _gen_start_time was captured a hair after this
            # call, fractionally negative) right at a batch's very start
            # — a tiny floor here instead of letting avg/ETA show a
            # nonsense negative number for a moment.
            elapsed=max(time.time()-getattr(self,"_gen_start_time",time.time()),0.05)
            avg=elapsed/(d+failed)
            remaining=max(t-d-failed,0)
            eta=avg*remaining
            model_bit=f"{self._last_ai_provider} · {self._last_ai_model}" \
                if getattr(self,"_last_ai_provider",None) else "—"
            self._proc_stats_lbl.configure(text=(
                f"Completed: {d+failed}/{t}   ·   Remaining: {remaining}   ·   "
                f"Avg: {avg:.1f}s/img   ·   Est. remaining: {_fmt_secs(eta)}   ·   "
                f"Model: {model_bit}   ·   Retries: {self._gen_retry_count}"))
        elif not self.ai_running:
            self._proc_stats_lbl.configure(text="")

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
        # Wipe cached thumbnails too — background thread since the cache
        # folder can hold thousands of files; this never touches
        # prefs.json (different folder, see clear_thumb_cache's own note).
        threading.Thread(target=clear_thumb_cache,daemon=True).start()

    def _clear_results(self):
        self._rebuild_card_pools()
        self._path_idx={}
        self._completion_order=[]
        try: self._gen_scroll._parent_canvas.yview_moveto(0.0)
        except Exception: pass
        self._gen_count_lbl.configure(text="Generated Metadata (0)")
        self._gen_empty_lbl.place(x=0,y=40,relwidth=1)
        self._page_lbl.configure(text="0 images")
        try: self._embed_gen_btn.pack_forget()
        except Exception: pass

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
        status=self._results.get(path,{}).get("status")
        if status in ("done","failed") and path not in self._completion_order:
            self._completion_order.append(path)
        newly_finished=(status in ("done","failed") and card is None)
        # newly_finished: no card exists for this path yet (it was never
        # "waiting"/"working" on screen — see _render_page) — this status
        # transition is the only moment it will ever get one, so a render
        # must happen here or it would never appear at all.
        self._maybe_refresh_grid(force=newly_finished)

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
        try: self._embed_gen_btn.pack_forget()
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
            messagebox.showerror("No API Keys","Open 'API Manager'."); return
        self.ai_running=True; self.ai_stop_flag=False; self._ai_paused=False
        self._gen_epoch+=1; epoch=self._gen_epoch
        self._gen_start_time=time.time()
        self._gen_retry_count=0
        self._gen_btn.configure(state="disabled",text="⟳  Generating…")
        self._pause_btn.pack(side="left",padx=(0,4),before=self._gen_btn)
        self._stop_btn.pack(side="left",padx=(0,5),before=self._gen_btn)
        try: self._embed_gen_btn.pack_forget()
        except: pass
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
        concurrency=max(1,min(20,int(self.ai_concurrency_var.get())))

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
                self._ui_action_queue.put(lambda p=path:self._update_card(p))
                self._ui_action_queue.put(lambda f=fname,n=i+1,t=total:
                    self._update_progress(done=done_count,total=t,
                        msg=f"⟳  [{n}/{t}] {f}"))
                try:
                    ext=os.path.splitext(path)[1].lower()
                    if ext in VECTOR_EXTS or ext in VIDEO_EXTS:
                        raise ValueError("Vector/video: convert to JPG first")
                    raw,provider,model_id,key_idx=call_with_failover(path,prompt,self.prefs,
                        status_cb=lambda msg:self._ui_action_queue.put(
                            lambda m=msg:self.set_status(f"⟳  {m}",GRN)))
                    if epoch!=self._gen_epoch: return
                    self._last_ai_provider,self._last_ai_model=provider,model_id
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
                            with lock: self._gen_retry_count+=1
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
                    self._ui_action_queue.put(lambda p=path:self._update_card(p))
                except Exception as e:
                    self._results[path]={"status":"failed","error":str(e)[:120]}
                    self._ui_action_queue.put(lambda p=path:self._update_card(p))
                self._ui_action_queue.put(lambda n=done_count,t=total:
                    self._update_progress(done=n,total=t))
            except Exception:
                # Safety net only — process_one already catches its own
                # per-item errors above and records them as a "failed"
                # result, so this should never actually fire.
                pass

        self._task_mgr.run_batch(targets, process_one, max_workers=concurrency,
            on_all_done=lambda: setattr(self,"_gen_done_flag",True))

    def _gen_done(self):
        self.ai_running=False; self._ai_paused=False
        # Force a final render right away — cards only ever get created
        # via the debounced _maybe_refresh_grid render, and with
        # a fast/high-concurrency batch it's possible for the very last
        # completions to still have a pending debounce timer when the
        # batch itself finishes. Without this, those last images could
        # sit fully "done" in self._results but never actually get a
        # card until something unrelated happened to trigger a render.
        if getattr(self,"_grid_refresh_after_id",None):
            try: self.after_cancel(self._grid_refresh_after_id)
            except Exception: pass
            self._grid_refresh_after_id=None
        self._render_page()
        total=len(self._all_paths)
        done=sum(1 for r in self._results.values() if r.get("status")=="done")
        failed=sum(1 for r in self._results.values() if r.get("status") in ("failed","stopped"))
        self._gen_btn.configure(state="normal",text=f"✨  Generate ({total})")
        try: self._pause_btn.pack_forget()
        except: pass
        try: self._stop_btn.pack_forget()
        except: pass
        self._pause_btn.configure(text="⏸  Pause",fg_color=AMB_DIM,text_color=AMB_BTN)
        # Embed only belongs here after a genuine, natural completion —
        # never mid-run (this method only runs once the whole batch has
        # returned), and never after the user hit Stop.
        if not self.ai_stop_flag and done>0:
            self._embed_gen_btn.pack(side="left",padx=(0,5),before=self._clear_all_btn)
        else:
            try: self._embed_gen_btn.pack_forget()
            except: pass
        if failed>0:
            self._retry_btn.pack(side="left",padx=(0,5),before=self._gen_btn)
        self.set_status(f"● Done — {done} generated · {failed} failed",
                        GRN if failed==0 else AMB_BTN)
        self._update_progress(done=done,total=total)
        seconds=time.time()-getattr(self,"_gen_start_time",time.time())
        kind="prompt_generation" if self.current_mode=="prompt" else "metadata_generation"
        if done>0:
            stats_db.record(kind,"completed",count=done,api_requests=done,seconds=seconds,
                             detail=f"{'Prompts' if kind=='prompt_generation' else 'Files'}: {done}")
        if failed>0:
            stats_db.record(kind,"failed",count=failed,api_requests=failed)
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
        def _c():
            et=find_exiftool()
            self._ui_action_queue.put(lambda:self.sb_et.configure(
                text="ExifTool · ready" if et else "ExifTool · missing",
                text_color=GRN if et else RED_BTN))
        threading.Thread(target=_c,daemon=True).start()


if __name__=='__main__':
    app=App(); app.mainloop()
