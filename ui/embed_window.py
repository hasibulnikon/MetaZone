"""Embed Metadata tab window — batch-writes CSV metadata into image/
vector/video files via ExifTool."""
import os, sys, csv, re, subprocess, threading, time, queue
import customtkinter as ctk
from tkinter import filedialog, messagebox, StringVar, BooleanVar
from core.utils import find_exiftool, find_file, find_recursive, build_file_index, index_lookup, embed_metadata_one, set_window_icon
from core import stats_db
from ui.theme import (BG1,BG2,BG3,BG4,GLASS,GLASS_BDR,TXT,TXT2,TXT3,
    GRN,GRN_H,GRN_DIM,RED_BTN,RED_BTN_H,RED_DIM,LOG_BG,ABSOLUTE_BG,AMB,AMB2)
from ui.dnd import DND_AVAILABLE, DND_FILES
from workers.task_manager import TaskManager

class EmbedContent(ctk.CTkFrame):
    """The actual Embed Metadata body — usable either embedded directly in
    a nav page or wrapped in a popup Toplevel. See APIManagerContent for
    why this split exists."""
    def __init__(self,parent,csv_path=None,folder_path=None,fg_color=None):
        super().__init__(parent,fg_color=fg_color or BG1,corner_radius=0)
        self.csv_rows=[]; self.csv_headers=[]; self.embed_running=False
        self.col_combos={}  # set for real inside _build(); defensive default so
                             # a partial/failed build can never raise the
                             # "no attribute 'col_combos'" error on later use
        self.csv_path_var=StringVar(); self.folder_path_var=StringVar()
        self.col_file_var=StringVar(value="(skip)"); self.col_title_var=StringVar(value="(skip)")
        self.col_kw_var=StringVar(value="(skip)"); self.col_desc_var=StringVar(value="(skip)")
        self.match_only_var=BooleanVar(value=True); self.subfolder_var=BooleanVar(value=True)
        self.rm_prog_var=BooleanVar(value=True); self.rm_copy_var=BooleanVar(value=True)
        # Remove Copyright stays functional (still applied on embed) but is
        # hidden from the toggle grid for now — Replace Filename takes its
        # spot in the UI instead.
        self.replace_filename_var=BooleanVar(value=False)
        self._task_mgr=TaskManager()
        self._rename_lock=threading.Lock()
        # Embedding runs up to 6 rows in parallel (TaskManager/ThreadPoolExecutor),
        # each reporting progress the instant it finishes — that means up to 6
        # background threads (plus TaskManager's own watcher thread for the
        # final on_all_done) all wanting to touch the UI concurrently. Calling
        # self.after() directly from a background thread is the exact pattern
        # already root-caused elsewhere in this app to silently corrupt Tcl's
        # internal state and freeze the whole window — and this path is hotter
        # than either of those (once per ROW, with real concurrency, not once
        # per batch from a single thread). Every UI touch from a worker thread
        # goes through this queue instead; only the poll below, running on the
        # main thread via self.after, ever calls a Tk method.
        self._ui_action_queue=queue.Queue()
        self.after(30,self._poll_ui_actions)
        self._build()
        # Auto-load whatever was just generated, so "generate then embed" is
        # a one-click flow instead of re-browsing for the CSV and folder.
        if folder_path:
            self.folder_path_var.set(folder_path)
            self.folder_status.configure(text=f"✓ {os.path.basename(folder_path)}",
                fg_color=GRN_DIM,text_color=GRN)
        if csv_path: self._do_load_csv(csv_path)

    def _poll_ui_actions(self):
        try:
            for _ in range(200):
                self._ui_action_queue.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            if self.winfo_exists():
                self.after(30,self._poll_ui_actions)
        except Exception:
            pass

    def _build(self):
        self.grid_columnconfigure(0,weight=1); self.grid_columnconfigure(1,weight=0,minsize=260)
        self.grid_rowconfigure(0,weight=1)
        body=ctk.CTkFrame(self,fg_color=BG1,corner_radius=0)
        body.grid(row=0,column=0,sticky="nsew",padx=12,pady=12)
        body.grid_columnconfigure(0,weight=1)

        # 1. CSV row (also a drop target)
        r1=self._section(body,"1","Load CSV",self._load_csv,0)
        self.csv_status=ctk.CTkLabel(r1,text="No CSV loaded",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,fg_color=BG3,
            corner_radius=8,padx=8,pady=2)
        self.csv_status.grid(row=1,column=0,columnspan=3,sticky="w",padx=10,pady=(0,10))
        self._csv_drop_frame=r1
        self._register_csv_drop([r1,self.csv_status])

        # 2. File Location row (renamed from "Image Folder" — also used for
        # vector/video files, not just images) + match-count preview
        r2=self._section(body,"2","File Location",self._browse_folder,1)
        self.folder_status=ctk.CTkLabel(r2,text="No folder selected",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,fg_color=BG3,
            corner_radius=8,padx=8,pady=2)
        self.folder_status.grid(row=1,column=0,columnspan=2,sticky="w",padx=10,pady=(0,10))
        self.match_status=ctk.CTkLabel(r2,text="",
            font=ctk.CTkFont("Segoe UI",10,"bold"),text_color=TXT3,fg_color=BG3,
            corner_radius=8,padx=8,pady=2)
        self.match_status.grid(row=1,column=2,sticky="e",padx=10,pady=(0,10))
        self._register_folder_drop([r2,self.folder_status])

        # Column map (compact 2x2)
        cmap=ctk.CTkFrame(body,fg_color=GLASS,corner_radius=10,border_width=1,border_color=GLASS_BDR)
        cmap.grid(row=2,column=0,sticky="ew",pady=(0,8))
        cmap.grid_columnconfigure(0,weight=1); cmap.grid_columnconfigure(1,weight=1)
        self.col_combos={}
        fields=[("Filename",self.col_file_var),("Title",self.col_title_var),
                ("Keywords",self.col_kw_var),("Description",self.col_desc_var)]
        for i,(lbl,var) in enumerate(fields):
            r,c=i//2,i%2
            cell=ctk.CTkFrame(cmap,fg_color="transparent",corner_radius=0)
            cell.grid(row=r,column=c,sticky="ew",padx=(8 if c==0 else 4,4 if c==0 else 8),pady=4)
            cell.grid_columnconfigure(0,weight=1)
            ctk.CTkLabel(cell,text=lbl.upper(),font=ctk.CTkFont("Segoe UI",9,"bold"),
                text_color=TXT3,fg_color="transparent").pack(anchor="w")
            cb=ctk.CTkComboBox(cell,variable=var,values=["(skip)"],state="readonly",
                font=ctk.CTkFont("Segoe UI",11),fg_color=BG3,text_color=TXT,
                border_color=GRN_DIM,border_width=2,button_color=GRN,button_hover_color=GRN_H,
                dropdown_fg_color=BG4,dropdown_text_color=TXT,dropdown_hover_color=GRN_DIM,
                dropdown_font=ctk.CTkFont("Segoe UI",11),
                corner_radius=8,height=34,command=lambda v:self._update_match_preview())
            cb.pack(fill="x",pady=(2,0)); self.col_combos[lbl]=cb

        # 4 toggles in 2x2 grid
        opts=ctk.CTkFrame(body,fg_color=GLASS,corner_radius=10,border_width=1,border_color=GLASS_BDR)
        opts.grid(row=3,column=0,sticky="ew",pady=(0,8))
        opts.grid_columnconfigure(0,weight=1); opts.grid_columnconfigure(1,weight=1)
        toggles=[
            ("Match Filename Only",self.match_only_var),
            ("Include Sub-Folders",self.subfolder_var),
            ("Remove Program Name",self.rm_prog_var),
            ("Replace Filename",self.replace_filename_var),
        ]
        for i,(lbl,var) in enumerate(toggles):
            r,c=i//2,i%2
            tf=ctk.CTkFrame(opts,fg_color="transparent",corner_radius=0)
            tf.grid(row=r,column=c,sticky="ew",padx=10,pady=6)
            tf.grid_columnconfigure(0,weight=1)
            ctk.CTkLabel(tf,text=lbl,font=ctk.CTkFont("Segoe UI",11),
                text_color=TXT2,fg_color="transparent").grid(row=0,column=0,sticky="w")
            ctk.CTkSwitch(tf,text="",variable=var,progress_color=GRN,button_color=TXT,
                fg_color=GLASS_BDR,onvalue=True,offvalue=False,width=44,height=22,
                command=lambda:self._update_match_preview()
            ).grid(row=0,column=1,sticky="e")

        # Action row
        af=ctk.CTkFrame(body,fg_color="transparent",corner_radius=0)
        af.grid(row=4,column=0,sticky="ew",pady=(0,8))
        af.grid_columnconfigure(0,weight=1)
        self._emb_btn=ctk.CTkButton(af,text="▶  Start Embedding",height=44,
            font=ctk.CTkFont("Segoe UI",13,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,
            text_color_disabled=ABSOLUTE_BG,corner_radius=22,
            command=self._start)
        self._emb_btn.grid(row=0,column=0,sticky="ew")
        ctk.CTkButton(af,text="↺",width=44,height=44,
            font=ctk.CTkFont("Segoe UI",18,"bold"),fg_color=RED_DIM,hover_color=RED_BTN_H,
            text_color=RED_BTN,corner_radius=22,command=self._reset
        ).grid(row=0,column=1,padx=(6,0))

        # Progress bar + live succeeded/failed/not-found counts — same
        # pattern as the Generate tab's progress row, so embedding a large
        # batch is no longer a silent multi-second wait with no feedback.
        prog=ctk.CTkFrame(body,fg_color=GLASS,corner_radius=10,
            border_width=1,border_color=GLASS_BDR)
        prog.grid(row=5,column=0,sticky="ew",pady=(0,4))
        prog.grid_columnconfigure(0,weight=1)
        self._embed_prog_bar=ctk.CTkProgressBar(prog,progress_color=GRN,fg_color=BG3,
            border_width=1,border_color=GLASS_BDR,height=14,corner_radius=7)
        self._embed_prog_bar.grid(row=0,column=0,sticky="ew",padx=10,pady=(12,6))
        self._embed_prog_bar.set(0)
        self._embed_counts_lbl=ctk.CTkLabel(prog,
            text="0 succeeded  ·  0 failed  ·  0 not found",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,fg_color="transparent")
        self._embed_counts_lbl.grid(row=1,column=0,sticky="w",padx=10,pady=(0,10))

        # Activity Log — right-hand panel (matches the old v1.2 layout)
        log_panel=ctk.CTkFrame(self,fg_color=BG2,corner_radius=0)
        log_panel.grid(row=0,column=1,sticky="nsew",padx=(0,12),pady=12)
        log_panel.grid_columnconfigure(0,weight=1); log_panel.grid_rowconfigure(1,weight=1)
        lp_hdr=ctk.CTkFrame(log_panel,fg_color=BG2,corner_radius=0)
        lp_hdr.grid(row=0,column=0,sticky="ew",padx=10,pady=(10,6))
        lp_hdr.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(lp_hdr,text="ACTIVITY LOG",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=TXT3,fg_color=BG2).grid(row=0,column=0,sticky="w")
        ctk.CTkButton(lp_hdr,text="Clear",width=54,height=22,
            font=ctk.CTkFont("Segoe UI",9,"bold"),fg_color=BG3,hover_color=BG4,
            text_color=TXT2,corner_radius=6,command=self._clear_log
        ).grid(row=0,column=1,sticky="e")
        self._log=ctk.CTkTextbox(log_panel,font=ctk.CTkFont("Consolas",10),
            fg_color=LOG_BG,text_color=TXT,corner_radius=8,state="disabled")
        self._log.grid(row=1,column=0,sticky="nsew",padx=10,pady=(0,10))

        # Catch-all drop target covering the WHOLE page, not just the two
        # narrow CSV/File-Location rows. Same bug class already found and
        # fixed once for Smart Workflow ("raised panel covered Standard's
        # drop targets, was never registered as one itself" — v0.6): the
        # popup embedder is small enough that its two drop rows fill most
        # of the window, so it's hard to miss them; the full-page embedder
        # sits inside the much bigger main window with a lot of open space
        # around those same two rows, and dropping anywhere outside their
        # exact bounds previously landed on nothing registered at all.
        # Registering `self` (and `body`) means a drop ANYWHERE on this
        # page now resolves to something — a .csv routes to the CSV
        # loader, anything else (a folder, or a stray image/video file) to
        # the File Location handler — matching what a person actually
        # expects when dropping "onto the embedder", not just onto one
        # specific row of it.
        self._register_generic_drop([self,body])

    def _register_generic_drop(self,widgets):
        if not DND_AVAILABLE: return
        for w in widgets:
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>",self._on_generic_drop)
            except Exception: pass

    def _on_generic_drop(self,event):
        raw=event.data
        paths=[p.strip('{}') for p in raw.split('} {')] if '{' in raw else raw.split()
        paths=[p.strip('{}') for p in paths]
        if not paths: return event.action
        if paths[0].lower().endswith(".csv") and os.path.isfile(paths[0]):
            return self._on_csv_drop(event)
        return self._on_folder_drop(event)

    def _section(self,parent,num,title,cmd,row):
        f=ctk.CTkFrame(parent,fg_color=GLASS,corner_radius=10,border_width=1,border_color=GLASS_BDR)
        f.grid(row=row,column=0,sticky="ew",pady=(0,8)); f.grid_columnconfigure(1,weight=1)
        ctk.CTkLabel(f,text=num,font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=GRN,text_color=ABSOLUTE_BG,corner_radius=50,width=28,height=28
        ).grid(row=0,column=0,padx=(10,8),pady=8)
        ctk.CTkLabel(f,text=title,font=ctk.CTkFont("Segoe UI",12,"bold"),
            text_color=TXT2,fg_color="transparent").grid(row=0,column=1,sticky="w")
        ctk.CTkButton(f,text="Browse",width=86,height=28,
            font=ctk.CTkFont("Segoe UI",11,"bold"),fg_color=GRN,hover_color=GRN_H,
            text_color=ABSOLUTE_BG,corner_radius=14,command=cmd
        ).grid(row=0,column=2,padx=(0,10),pady=8)
        return f

    def _load_csv(self):
        p=filedialog.askopenfilename(title="Select CSV",filetypes=[("CSV","*.csv"),("All","*.*")])
        if p: self._do_load_csv(p)

    def _do_load_csv(self,path):
        try:
            with open(path,newline='',encoding='utf-8-sig') as f:
                reader=csv.DictReader(f)
                self.csv_rows=list(reader); self.csv_headers=list(reader.fieldnames or [])
            self.csv_path_var.set(path)
            self.csv_status.configure(text=f"✓ {len(self.csv_rows)} rows",
                fg_color=GRN_DIM,text_color=GRN)
            self._update_combos(); self._log_msg(f"✓ CSV loaded — {len(self.csv_rows)} rows")
            # Auto-fill the file location with the CSV's own folder when it's
            # not set yet — still fully editable/overridable via Browse.
            if not self.folder_path_var.get():
                guess=os.path.dirname(path)
                if guess:
                    self.folder_path_var.set(guess)
                    self.folder_status.configure(text=f"✓ {os.path.basename(guess)} (from CSV)",
                        fg_color=GRN_DIM,text_color=GRN)
            self._update_match_preview()
        except Exception as e: messagebox.showerror("CSV Error",str(e),parent=self)

    def _browse_folder(self):
        start=self.folder_path_var.get() or None
        p=filedialog.askdirectory(title="Select file location",parent=self,
            initialdir=start if start and os.path.isdir(start) else None)
        if p:
            self.folder_path_var.set(p)
            self.folder_status.configure(text=f"✓ {os.path.basename(p)}",
                fg_color=GRN_DIM,text_color=GRN)
            self._update_match_preview()

    def _update_combos(self):
        opts=["(skip)"]+self.csv_headers
        hints={"Filename":["filename","file","name","image"],"Title":["title"],
               "Keywords":["keyword","tag","kw"],"Description":["desc","caption","description"]}
        vmap={"Filename":self.col_file_var,"Title":self.col_title_var,
              "Keywords":self.col_kw_var,"Description":self.col_desc_var}
        for lbl,cb in self.col_combos.items():
            cb.configure(values=opts)
            g=next((c for h in hints.get(lbl,[]) for c in self.csv_headers if h in c.lower()),"")
            vmap[lbl].set(g or "(skip)")

    def _update_match_preview(self):
        """Show how many CSV rows actually resolve to a real file in the
        chosen location, BEFORE the user commits to starting the embed.
        Runs the (potentially slow, for a big nested real folder tree)
        directory scan on a background thread and applies the result
        through the app's existing thread-safe queue — this used to call
        find_file/find_recursive once PER ROW directly on the main
        thread during construction, which for subfolder search meant up
        to N full independent os.walk scans blocking the UI with no
        indicator at all (see build_file_index's docstring — this is the
        confirmed root cause of a reported freeze on a 70-row batch)."""
        folder=self.folder_path_var.get(); fc=self.col_file_var.get()
        if not folder or not self.csv_rows or not fc or fc=="(skip)":
            self.match_status.configure(text="",fg_color=BG3,text_color=TXT3); return
        recursive=self.subfolder_var.get()
        use_ext=self.match_only_var.get()
        rows=list(self.csv_rows)
        self._match_epoch=getattr(self,"_match_epoch",0)+1
        my_epoch=self._match_epoch
        self.match_status.configure(text="🔍 Checking files…",fg_color=BG3,text_color=TXT3)

        def _work():
            index=build_file_index(folder,recursive)
            matched=sum(1 for row in rows
                        if index_lookup(index,row.get(fc) or "",use_ext))
            total=len(rows)
            def _apply():
                if my_epoch!=self._match_epoch:
                    return  # a newer check (folder/column/checkbox changed) superseded this one
                self._file_index=index; self._file_index_key=(folder,recursive)
                ok=matched==total
                self.match_status.configure(text=f"🔍 {matched}/{total} files matched",
                    fg_color=GRN_DIM if ok else AMB2,text_color=GRN if ok else AMB)
            self._ui_action_queue.put(_apply)
        threading.Thread(target=_work,daemon=True).start()

    def _register_csv_drop(self,widgets):
        """Let the CSV row accept a dragged-in .csv file directly."""
        if not DND_AVAILABLE: return
        for w in widgets:
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>",self._on_csv_drop)
            except Exception: pass

    def _register_folder_drop(self,widgets):
        """Let the File Location row accept a dragged-in folder directly —
        also accepts a dropped file, in which case its containing folder
        is used, matching how people naturally drag things in."""
        if not DND_AVAILABLE: return
        for w in widgets:
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>",self._on_folder_drop)
            except Exception: pass

    def _on_folder_drop(self,event):
        raw=event.data
        paths=[p.strip('{}') for p in raw.split('} {')] if '{' in raw else raw.split()
        paths=[p.strip('{}') for p in paths]
        if not paths: return event.action
        p=paths[0]
        folder=p if os.path.isdir(p) else os.path.dirname(p)
        if folder and os.path.isdir(folder):
            self.folder_path_var.set(folder)
            self.folder_status.configure(text=f"✓ {os.path.basename(folder)}",
                fg_color=GRN_DIM,text_color=GRN)
            self._update_match_preview()
        else:
            messagebox.showwarning("Not a folder","Drop a folder here.",parent=self)
        return event.action

    def _on_csv_drop(self,event):
        raw=event.data
        paths=[p.strip('{}') for p in raw.split('} {')] if '{' in raw else raw.split()
        paths=[p.strip('{}') for p in paths]
        csvs=[p for p in paths if p.lower().endswith('.csv')]
        if csvs: self._do_load_csv(csvs[0])
        else: messagebox.showwarning("Not a CSV","Drop a .csv file here.",parent=self)
        return event.action

    def _log_msg(self,msg):
        self._log.configure(state="normal")
        self._log.insert("end",f"{msg}\n"); self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal"); self._log.delete("1.0","end"); self._log.configure(state="disabled")

    def _reset(self):
        self.csv_rows=[]; self.csv_headers=[]
        self.csv_path_var.set(""); self.folder_path_var.set("")
        self.csv_status.configure(text="No CSV loaded",fg_color=BG3,text_color=TXT3)
        self.folder_status.configure(text="No folder selected",fg_color=BG3,text_color=TXT3)
        self.match_status.configure(text="",fg_color=BG3,text_color=TXT3)
        for cb in self.col_combos.values(): cb.configure(values=["(skip)"]); cb.set("(skip)")
        self._emb_btn.configure(state="normal",text="▶  Start Embedding")
        self._embed_prog_bar.set(0)
        self._embed_counts_lbl.configure(text="0 succeeded  ·  0 failed  ·  0 not found")
        self._clear_log()

    def _rename_to_title(self,fp,title):
        """Rename fp to a filesystem-safe name built from the first 8
        words of title, preserving the original extension. Returns the
        new path on success, or None (and logs why) if it couldn't.
        Locked end-to-end (collision check through the actual rename)
        since this runs from parallel worker threads — two files sharing
        the same title must never both compute the same free name before
        either has renamed, or one would silently overwrite the other."""
        words=title.strip().split()[:8]
        base=" ".join(words)
        base=re.sub(r'[<>:"/\\|?*]',"",base).strip()
        base=re.sub(r"\s+"," ",base)
        if not base:
            return None
        ext=os.path.splitext(fp)[1]
        directory=os.path.dirname(fp)
        with self._rename_lock:
            new_path=os.path.join(directory,base+ext)
            n=1
            while os.path.exists(new_path) and os.path.normcase(new_path)!=os.path.normcase(fp):
                new_path=os.path.join(directory,f"{base} ({n}){ext}")
                n+=1
            try:
                os.rename(fp,new_path)
                return new_path
            except Exception as ex:
                self._ui_action_queue.put(lambda e=str(ex):self._log_msg(f"⚠  Rename failed: {e}"))
                return None

    def _start(self):
        if self.embed_running: return
        et=find_exiftool()
        if not et: messagebox.showerror("ExifTool","Place exiftool.exe next to this app.",parent=self); return
        if not self.csv_rows: messagebox.showerror("No CSV","Load a CSV first.",parent=self); return
        if not self.folder_path_var.get(): messagebox.showerror("No folder","Select folder.",parent=self); return
        fc=self.col_file_var.get()
        if not fc or fc=="(skip)": messagebox.showerror("Column","Select filename column.",parent=self); return
        self.embed_running=True
        self._emb_btn.configure(state="disabled",text="⟳  Processing…")
        self._embed_start_time=time.time()
        threading.Thread(target=self._embed_thread,args=(et,),daemon=True).start()

    def _embed_thread(self,et):
        # Re-resolve rather than trust the path found when the button was
        # clicked — the window can sit open a while before Start is pressed.
        fresh=find_exiftool()
        if fresh: et=fresh
        elif not os.path.exists(et):
            self._ui_action_queue.put(lambda:(self._log_msg(f"✗  ExifTool not found at {et}"),
                messagebox.showerror("ExifTool",
                    "exiftool.exe is missing. Place it next to Meta Zone's "
                    "own .exe (not a system PATH install) and try again.",parent=self),
                self._emb_btn.configure(state="normal",text="▶  Start Embedding"),
                setattr(self,'embed_running',False)))
            return
        folder=self.folder_path_var.get(); col_f=self.col_file_var.get()
        col_t=self.col_title_var.get(); col_k=self.col_kw_var.get(); col_d=self.col_desc_var.get()
        use_sub=self.subfolder_var.get(); use_ext=self.match_only_var.get()
        rm_prog=self.rm_prog_var.get(); rm_copy=self.rm_copy_var.get()
        replace_fn=self.replace_filename_var.get()
        total=len(self.csv_rows)
        # Reuse the index the match-preview already built for this exact
        # folder+subfolder-search combination if it's still current;
        # otherwise (e.g. embed was started before the preview finished,
        # or settings changed since) build it fresh, once, right here —
        # either way this is ONE scan for the whole batch, not one scan
        # per row. See build_file_index's docstring for why that matters.
        if getattr(self,"_file_index",None) is not None and getattr(self,"_file_index_key",None)==(folder,use_sub):
            index=self._file_index
        else:
            index=build_file_index(folder,use_sub)
            self._file_index=index; self._file_index_key=(folder,use_sub)
        self._ui_action_queue.put(lambda:self._log_msg(f"▶  Started — {total} rows"))
        self._ui_action_queue.put(lambda:(self._embed_prog_bar.set(0),
            self._embed_counts_lbl.configure(text=f"0 succeeded  ·  0 failed  ·  0 not found")))

        counts={"ok":0,"skipped":0,"errors":0}
        lock=threading.Lock()
        done=[0]

        def _update_progress_ui():
            pct=done[0]/total if total else 0
            self._embed_prog_bar.set(pct)
            self._embed_counts_lbl.configure(
                text=f"{counts['ok']} succeeded  ·  {counts['errors']} failed  ·  "
                     f"{counts['skipped']} not found  ({done[0]}/{total})")

        def process_row(row,i):
            fn=(row.get(col_f) or "").strip()
            if not fn:
                with lock: counts["skipped"]+=1; done[0]+=1
                self._ui_action_queue.put(_update_progress_ui)
                return
            fp=index_lookup(index,fn,use_ext)
            if not fp:
                with lock: counts["skipped"]+=1; done[0]+=1
                self._ui_action_queue.put(lambda f=fn:(self._log_msg(f"⚠  Not found: {f}"),_update_progress_ui()))
                return
            title=(row.get(col_t) or "").strip() if col_t and col_t!="(skip)" else ""
            kw_raw=(row.get(col_k) or "").strip() if col_k and col_k!="(skip)" else ""
            desc=(row.get(col_d) or "").strip() if col_d and col_d!="(skip)" else ""
            actual=os.path.basename(fp)
            ok,msg,final_path=embed_metadata_one(et,fp,title,kw_raw,desc,rm_prog,rm_copy)
            if ok:
                final_name=os.path.basename(final_path)
                if replace_fn and title:
                    new_path=self._rename_to_title(final_path,title)
                    if new_path: final_name=os.path.basename(new_path)
                # msg is just the plain post-embed basename unless
                # _detect_and_fix_extension had to correct a real/extension
                # format mismatch, in which case it's "name.ext  (note...)"
                # — surface that note (a file actually got renamed on disk,
                # never do that silently) even though final_name above may
                # have moved on again from a title-based rename since.
                note=f"  ({msg.split('  (',1)[1]}" if "  (" in msg else ""
                with lock: counts["ok"]+=1; done[0]+=1
                self._ui_action_queue.put(lambda fn=final_name,n=note:
                    (self._log_msg(f"✓  {fn}{n}"),_update_progress_ui()))
            else:
                with lock: counts["errors"]+=1; done[0]+=1
                self._ui_action_queue.put(lambda fn=actual,e=msg:(self._log_msg(f"✗  {fn} — {e}"),_update_progress_ui()))

        def _finish():
            summary=f"{counts['ok']} embedded · {counts['skipped']} not found · {counts['errors']} errors"
            self._ui_action_queue.put(lambda:(self._log_msg(f"● Done — {summary}"),
                self._embed_prog_bar.set(1.0),_update_progress_ui(),
                self._emb_btn.configure(state="normal",text="▶  Start Again"),
                setattr(self,'embed_running',False)))
            seconds=time.time()-getattr(self,"_embed_start_time",time.time())
            if counts["ok"]>0:
                stats_db.record("embedding","completed",count=counts["ok"],seconds=seconds,
                                 detail=f"Files: {counts['ok']}")
            if counts["errors"]>0:
                stats_db.record("embedding","failed",count=counts["errors"])

        # Bounded parallel embedding (exiftool calls are mostly I/O wait,
        # not CPU-bound) — this is what actually makes a 100+ file batch
        # fast instead of running one exiftool process at a time.
        self._task_mgr.run_batch(self.csv_rows,process_row,max_workers=6,on_all_done=_finish)


class EmbedWindow(ctk.CTkToplevel):
    """Thin popup wrapper around EmbedContent — title bar and close button
    only; everything else lives in the shared content frame."""
    def __init__(self,parent,csv_path=None,folder_path=None):
        super().__init__(parent); self.title("Embed Metadata")
        self.configure(fg_color=BG1); self.resizable(True,True)
        set_window_icon(self)
        self.grab_set()
        self.grid_columnconfigure(0,weight=1); self.grid_rowconfigure(1,weight=1)

        hdr=ctk.CTkFrame(self,fg_color=BG2,corner_radius=0,height=50)
        hdr.grid(row=0,column=0,sticky="ew"); hdr.grid_propagate(False)
        hdr.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(hdr,text="📋  Embed Metadata",
            font=ctk.CTkFont("Segoe UI",14,"bold"),text_color=TXT,fg_color=BG2
        ).grid(row=0,column=0,sticky="w",padx=16,pady=13)
        ctk.CTkButton(hdr,text="✕",width=32,height=32,fg_color="transparent",
            hover_color=RED_DIM,text_color=TXT3,corner_radius=6,
            command=self.destroy).grid(row=0,column=1,padx=10)

        self.content=EmbedContent(self,csv_path=csv_path,folder_path=folder_path)
        self.content.grid(row=1,column=0,sticky="nsew")

        # Widened to fit the right-hand Activity Log panel. Height matches
        # the form's natural content height (~546px measured) plus a small
        # margin — it used to be hardcoded to 640px, which left a dead gap
        # below the Start Embedding button.
        self._center(1180,610)
        self.minsize(900,600)
        self.protocol("WM_DELETE_WINDOW",self.destroy)

    def _center(self,w,h):
        self.update_idletasks()
        x=self.master.winfo_x()+(self.master.winfo_width()-w)//2
        y=self.master.winfo_y()+(self.master.winfo_height()-h)//2
        self.geometry(f"{w}x{h}+{x}+{y}")

