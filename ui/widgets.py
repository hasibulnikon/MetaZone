"""Reusable result-list widgets: the bulk-import progress dialog and
the per-file metadata result card (thumbnail, title/description/
keywords, status, regenerate)."""
import os, threading, tkinter
import customtkinter as ctk
from core.utils import make_thumb, format_filesize
from ui.theme import (BG2,BG3,BG4,GLASS,GLASS_BDR,TXT,TXT2,TXT3,
    GRN,GRN_DIM,RED_BTN,RED_DIM,AMB_BTN,AMB_DIM,CYAN)

class ImportProgressDialog(ctk.CTkToplevel):
    def __init__(self,parent,total):
        super().__init__(parent)
        self.title("Importing Images")
        self.configure(fg_color=BG2); self.resizable(False,False)
        self.grab_set(); self.protocol("WM_DELETE_WINDOW",lambda:None)
        self.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(self,text="⟳  Importing Images…",font=ctk.CTkFont("Segoe UI",14,"bold"),
            text_color=TXT,fg_color=BG2).grid(row=0,column=0,padx=24,pady=(20,8))
        self._lbl=ctk.CTkLabel(self,text=f"0 / {total} files",font=ctk.CTkFont("Segoe UI",11),
            text_color=TXT2,fg_color=BG2)
        self._lbl.grid(row=1,column=0,padx=24,pady=(0,10))
        self._bar=ctk.CTkProgressBar(self,progress_color=GRN,fg_color=BG3,
            height=10,corner_radius=5,width=320)
        self._bar.grid(row=2,column=0,padx=24,pady=(0,20)); self._bar.set(0)
        self.update_idletasks()
        w,h=380,130
        x=parent.winfo_x()+(parent.winfo_width()-w)//2
        y=parent.winfo_y()+(parent.winfo_height()-h)//2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def update_progress(self,done,total):
        self._lbl.configure(text=f"{done} / {total} files")
        self._bar.set(done/total if total else 0)

    def finish(self):
        try: self.grab_release()
        except Exception: pass
        self.destroy()


# ══════════════════════════════════════════════════════════════════════
#  RESULT CARD
# ══════════════════════════════════════════════════════════════════════



class MetaResultCard(ctk.CTkFrame):
    # Left info panel is a fixed pixel width — it never grows/shrinks when
    # description is toggled or the window is resized; the thumbnail inside
    # it is doubled in size (60px -> 120px) per feedback.
    LEFT_PANEL_W = 120
    THUMB_SIZE = 60

    STATUS_STYLE = {
        "waiting": ("○  Waiting",  TXT3, BG4),
        "working": ("⟳  Working…", AMB_BTN, AMB_DIM),
        "done":    ("✓  Done",     GRN, GRN_DIM),
        "failed":  ("✗  Failed",   RED_BTN, RED_DIM),
        "stopped": ("■  Stopped",  TXT3, BG4),
    }

    def __init__(self,master,path,result,on_redo,mode="meta",request_thumb=None,
                 expanded=True,on_toggle_expand=None,show_desc=True,**kw):
        super().__init__(master,fg_color=GLASS,corner_radius=10,
            border_width=1,border_color=GLASS_BDR,**kw)
        self.path=path; self.result=dict(result); self.mode=mode
        self._boxes={}; self._hdr_lbls={}; self._preview_lbls={}
        self._expanded=expanded; self._on_toggle_expand=on_toggle_expand
        self._show_desc=show_desc
        self._build(on_redo)
        if request_thumb:
            request_thumb(self.path,self._tlbl)
        else:
            threading.Thread(target=self._load_thumb,daemon=True).start()

    def _build(self,on_redo):
        if not self._expanded:
            self._build_compact_card(on_redo)
            self._refresh_status()
            return

        # Left panel is now a FIXED pixel width (not a proportional weight)
        # so it never expands or shrinks when description/title toggle —
        # freed space always goes to title/description instead.
        self.grid_columnconfigure(0,weight=0,minsize=self.LEFT_PANEL_W)
        self.grid_columnconfigure(1,weight=40 if self._show_desc else 100)
        self.grid_columnconfigure(2,weight=60 if self._show_desc else 0)
        self.grid_rowconfigure(0,weight=1)

        # ── Left panel: thumbnail, filename, filesize, API used, status,
        # Regenerate — everything about THIS file, nothing editable. Wrapped
        # in its own bordered box so it reads as one solid unit that stays
        # put regardless of what the right side does.
        left_outer=ctk.CTkFrame(self,fg_color=BG3,corner_radius=8,
            border_width=1,border_color=GLASS_BDR,width=self.LEFT_PANEL_W)
        left_outer.grid(row=0,column=0,rowspan=2,sticky="nsew",padx=(8,6),pady=8)
        left_outer.grid_propagate(False)
        left=ctk.CTkFrame(left_outer,fg_color="transparent",corner_radius=0)
        left.pack(fill="both",expand=True,padx=6,pady=6)
        left.grid_columnconfigure(0,weight=1)

        # Thumbnail — doubled in size (60px -> 120px) per feedback.
        tf=ctk.CTkFrame(left,fg_color=BG4,corner_radius=6,
            width=self.THUMB_SIZE,height=self.THUMB_SIZE)
        tf.grid(row=0,column=0,sticky="w",pady=(0,4)); tf.grid_propagate(False)
        self._tlbl=ctk.CTkLabel(tf,text="🖼",font=ctk.CTkFont("Segoe UI",26),
            fg_color=BG4,text_color=TXT3,width=self.THUMB_SIZE-2,
            height=self.THUMB_SIZE-2,corner_radius=6)
        self._tlbl.pack()

        fname=os.path.basename(self.path)
        ctk.CTkLabel(left,text=(fname[:26]+"…") if len(fname)>26 else fname,
            font=ctk.CTkFont("Segoe UI",11,"bold"),text_color=TXT2,
            fg_color="transparent",anchor="w",justify="left"
        ).grid(row=1,column=0,sticky="ew",pady=(0,1))

        self._size_lbl=ctk.CTkLabel(left,text=format_filesize(self.path),
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,
            fg_color="transparent",anchor="w")
        self._size_lbl.grid(row=2,column=0,sticky="ew",pady=(0,1))

        self._model_lbl=ctk.CTkLabel(left,text="",font=ctk.CTkFont("Segoe UI",10),
            text_color=TXT3,fg_color="transparent",anchor="w",justify="left",
            wraplength=self.LEFT_PANEL_W-24)
        self._model_lbl.grid(row=3,column=0,sticky="ew",pady=(0,2))

        self._status_lbl=ctk.CTkLabel(left,text="",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=TXT3,fg_color=BG4,corner_radius=20,padx=8,pady=3,anchor="w")
        self._status_lbl.grid(row=4,column=0,sticky="w",pady=(0,4))

        self._redo_btn=ctk.CTkButton(left,text="Regenerate",height=28,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=BG4,hover_color=AMB_DIM,text_color=AMB_BTN,
            border_width=1,border_color=AMB_BTN,corner_radius=8,command=on_redo)
        self._redo_btn.grid(row=5,column=0,sticky="ew")

        if self.mode=="prompt":
            self._build_prompt_fields()
        else:
            self._build_meta_fields()

        if self._on_toggle_expand:
            self._exp_btn=ctk.CTkButton(self,text="⌃  Collapse",width=90,height=24,
                font=ctk.CTkFont("Segoe UI",10,"bold"),
                fg_color=BG4,hover_color=BG3,text_color=TXT2,
                corner_radius=8,command=self._on_toggle_expand)
            self._exp_btn.place(relx=1.0,x=-8,y=6,anchor="ne")
            self._exp_btn.lift()

        self._refresh_status()

    def _build_compact_card(self,on_redo):
        """Deliberately kept thin (~92px) and cheap — no Textboxes, no
        multi-row metadata panel — for large batches where hundreds of
        these may be materialized/dematerialized per second while
        scrolling. This is the ONE card style that intentionally does NOT
        follow the left-panel/title/description map — that map is for the
        detailed view you get by clicking the ⌄ to expand a specific
        card."""
        self.grid_columnconfigure(0,weight=1)
        top=ctk.CTkFrame(self,fg_color="transparent",corner_radius=0)
        top.grid(row=0,column=0,sticky="ew",padx=8,pady=(8,2))
        top.grid_columnconfigure(1,weight=1)

        tf=ctk.CTkFrame(top,fg_color=BG3,corner_radius=6,width=40,height=40)
        tf.grid(row=0,column=0,rowspan=2,padx=(0,8)); tf.grid_propagate(False)
        self._tlbl=ctk.CTkLabel(tf,text="🖼",font=ctk.CTkFont("Segoe UI",13),
            fg_color=BG3,text_color=TXT3,width=38,height=38,corner_radius=6)
        self._tlbl.pack()

        fname=os.path.basename(self.path)
        ctk.CTkLabel(top,text=(fname[:34]+"…") if len(fname)>34 else fname,
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT2,
            fg_color="transparent",anchor="w"
        ).grid(row=0,column=1,sticky="w")

        botrow=ctk.CTkFrame(top,fg_color="transparent",corner_radius=0)
        botrow.grid(row=1,column=1,sticky="w")
        self._status_lbl=ctk.CTkLabel(botrow,text="",font=ctk.CTkFont("Segoe UI",8,"bold"),
            text_color=TXT3,fg_color=BG4,corner_radius=20,padx=6,pady=2)
        self._status_lbl.pack(side="left")
        self._model_lbl=ctk.CTkLabel(botrow,text="",font=ctk.CTkFont("Segoe UI",8),
            text_color=TXT3,fg_color="transparent",padx=4)
        self._model_lbl.pack(side="left")

        self._redo_btn=ctk.CTkButton(top,text="Regen",width=52,height=24,
            font=ctk.CTkFont("Segoe UI",9,"bold"),
            fg_color=BG4,hover_color=AMB_DIM,text_color=AMB_BTN,
            border_width=1,border_color=AMB_BTN,corner_radius=6,command=on_redo)
        self._redo_btn.grid(row=0,column=2,rowspan=2,padx=(4,0))

        if self._on_toggle_expand:
            exp_btn=ctk.CTkButton(top,text="⌄",width=22,height=40,
                font=ctk.CTkFont("Segoe UI",11,"bold"),
                fg_color=BG4,hover_color=BG3,text_color=TXT2,
                corner_radius=8,command=self._on_toggle_expand)
            exp_btn.grid(row=0,column=3,rowspan=2,padx=(4,0))

        self._build_compact_fields()

    def _build_compact_fields(self):
        """Content preview line(s) under the thin top row — plain Labels,
        no Textboxes/copy-paste buttons."""
        val = self.result.get("prompt","") if self.mode=="prompt" else self.result.get("title","")
        kw = self.result.get("kw","") if self.mode!="prompt" else ""
        desc = self.result.get("desc","") if self.mode!="prompt" else ""
        prev=ctk.CTkFrame(self,fg_color="transparent",corner_radius=0)
        prev.grid(row=1,column=0,sticky="ew",padx=8,pady=(0,8))
        prev.grid_columnconfigure(0,weight=1)
        line1=(val[:70]+"…") if len(val)>70 else (val or "—")
        l1=ctk.CTkLabel(prev,text=line1,font=ctk.CTkFont("Segoe UI",10),
            text_color=TXT2,fg_color="transparent",anchor="w",justify="left")
        l1.grid(row=0,column=0,sticky="ew")
        self._preview_lbls["line1"]=l1
        if self.mode!="prompt":
            sub=(desc[:60]+"…") if len(desc)>60 else desc
            kwn=len([x for x in kw.split(",") if x.strip()])
            l2=ctk.CTkLabel(prev,text=f"{sub}   ·   {kwn} keywords" if sub else f"{kwn} keywords",
                font=ctk.CTkFont("Segoe UI",9),text_color=TXT3,fg_color="transparent",
                anchor="w",justify="left")
            l2.grid(row=1,column=0,sticky="ew")
            self._preview_lbls["line2"]=l2

    def _build_prompt_fields(self):
        prompt_val=self.result.get("prompt","")
        wrap=ctk.CTkFrame(self,fg_color="transparent",corner_radius=0)
        wrap.grid(row=0,column=1,columnspan=2,sticky="nsew",padx=(6,8),pady=8)
        wrap.grid_columnconfigure(0,weight=1)
        hdr=ctk.CTkFrame(wrap,fg_color="transparent",corner_radius=0)
        hdr.grid(row=0,column=0,sticky="ew",pady=(0,2))
        hdr.grid_columnconfigure(1,weight=1)
        lbl=ctk.CTkLabel(hdr,text="Prompt  (0 words)",
            font=ctk.CTkFont("Segoe UI",9,"bold"),text_color=TXT3,
            fg_color="transparent")
        lbl.grid(row=0,column=0,sticky="w")
        self._hdr_lbls["prompt"]=("Prompt",lbl,False)
        bf=ctk.CTkFrame(hdr,fg_color="transparent",corner_radius=0)
        bf.grid(row=0,column=2,sticky="e")
        ctk.CTkButton(bf,text="⧉",width=28,height=20,font=ctk.CTkFont("Segoe UI",9),
            fg_color=BG4,hover_color=BG3,text_color=TXT3,corner_radius=10,
            command=lambda:self._copy("prompt")).pack(side="left",padx=(0,2))
        ctk.CTkButton(bf,text="⎙",width=28,height=20,font=ctk.CTkFont("Segoe UI",9),
            fg_color=BG4,hover_color=BG3,text_color=TXT3,corner_radius=10,
            command=lambda:self._paste("prompt")).pack(side="left")
        box=ctk.CTkTextbox(wrap,height=170,font=ctk.CTkFont("Segoe UI",10),
            fg_color=BG3,text_color=CYAN,border_color=GLASS_BDR,border_width=1,
            corner_radius=6,wrap="word")
        box.grid(row=1,column=0,sticky="ew")
        if prompt_val: box.insert("1.0",prompt_val)
        self._boxes["prompt"]=box
        self._recount("prompt")
        def _upd(e=None):
            self.result["prompt"]=box.get("1.0","end-1c")
            self._recount("prompt")
        box.bind("<KeyRelease>",_upd)

    def _build_meta_fields(self):
        title=self.result.get("title","")
        desc=self.result.get("desc","")
        kw=self.result.get("kw","")

        title_col=ctk.CTkFrame(self,fg_color="transparent",corner_radius=0)
        title_col.grid(row=0,column=1,sticky="nsew",
            padx=(6,4) if self._show_desc else (6,8),pady=(8,4))
        title_col.grid_columnconfigure(0,weight=1)
        self._build_field_box(title_col,"title","Title",title,CYAN,84)

        if self._show_desc:
            desc_col=ctk.CTkFrame(self,fg_color="transparent",corner_radius=0)
            desc_col.grid(row=0,column=2,sticky="nsew",padx=(4,8),pady=(8,4))
            desc_col.grid_columnconfigure(0,weight=1)
            self._build_field_box(desc_col,"desc","Description",desc,TXT2,84)

        # Keywords spans the full right-side width, underneath title+desc —
        # not part of the original left/title/desc map, but keywords need
        # to live somewhere, and a full-width row here keeps title and
        # description as the two side-by-side boxes as specced.
        kw_wrap=ctk.CTkFrame(self,fg_color="transparent",corner_radius=0)
        kw_wrap.grid(row=1,column=1,columnspan=(2 if self._show_desc else 1),
            sticky="ew",padx=(6,8),pady=(0,8))
        kw_wrap.grid_columnconfigure(0,weight=1)
        self._build_field_box(kw_wrap,"kw","Keywords",kw,GRN,63)

    def _build_field_box(self,parent,key,label,val,color,height):
        hdr=ctk.CTkFrame(parent,fg_color="transparent",corner_radius=0)
        hdr.grid(row=0,column=0,sticky="ew",pady=(0,2))
        hdr.grid_columnconfigure(1,weight=1)
        lbl=ctk.CTkLabel(hdr,text=label,font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=TXT3,fg_color="transparent")
        lbl.grid(row=0,column=0,sticky="w")
        self._hdr_lbls[key]=(label,lbl,key=="kw")
        bf=ctk.CTkFrame(hdr,fg_color="transparent",corner_radius=0)
        bf.grid(row=0,column=2,sticky="e")
        ctk.CTkButton(bf,text="⧉",width=26,height=18,font=ctk.CTkFont("Segoe UI",9),
            fg_color=BG4,hover_color=BG3,text_color=TXT3,corner_radius=9,
            command=lambda k=key:self._copy(k)).pack(side="left",padx=(0,2))
        ctk.CTkButton(bf,text="⎙",width=26,height=18,font=ctk.CTkFont("Segoe UI",9),
            fg_color=BG4,hover_color=BG3,text_color=TXT3,corner_radius=9,
            command=lambda k=key:self._paste(k)).pack(side="left")
        box=ctk.CTkTextbox(parent,height=height,font=ctk.CTkFont("Segoe UI",11),
            fg_color=BG3,text_color=color,border_color=GLASS_BDR,border_width=1,
            corner_radius=6,wrap="word")
        box.grid(row=1,column=0,sticky="ew")
        if val: box.insert("1.0",val)
        self._boxes[key]=box
        self._recount(key)
        def _upd(e=None,k=key,b=box):
            self.result[k]=b.get("1.0","end-1c")
            self._recount(k)
        box.bind("<KeyRelease>",_upd)

    def _recount(self,key):
        if key not in self._hdr_lbls or key not in self._boxes: return
        base,lbl,is_kw=self._hdr_lbls[key]
        val=self._boxes[key].get("1.0","end-1c")
        if is_kw:
            n=len([x for x in val.split(",") if x.strip()])
            lbl.configure(text=f"{base}  ({n})")
        elif key=="prompt":
            n=len(val.split()) if val.strip() else 0
            lbl.configure(text=f"{base}  ({n} words)")
        else:
            lbl.configure(text=f"{base}  ({len(val)} chars)")

    def _refresh_status(self):
        status=self.result.get("status","waiting")
        text,fg,bg=self.STATUS_STYLE.get(status,self.STATUS_STYLE["waiting"])
        self._status_lbl.configure(text=text,text_color=fg,fg_color=bg)
        err=self.result.get("error","")
        model_used=self.result.get("model_used","")
        if status=="failed" and err:
            self._model_lbl.configure(text=f"⚠ {err[:60]}",text_color=RED_BTN)
        else:
            self._model_lbl.configure(text=model_used,text_color=TXT3)
        self.configure(border_color=GLASS_BDR if status!="failed" else RED_BTN)

    def apply_result(self,result):
        """Update this card IN PLACE with a new result dict — used instead of
        destroying/recreating cards on every generation, redo, or retry."""
        self.result=dict(result)
        if self.mode=="prompt":
            box=self._boxes.get("prompt")
            if box:
                box.delete("1.0","end")
                val=self.result.get("prompt","")
                if val: box.insert("1.0",val)
                self._recount("prompt")
        else:
            for key in ("title","desc","kw"):
                box=self._boxes.get(key)
                if box:
                    box.delete("1.0","end")
                    val=self.result.get(key,"")
                    if val: box.insert("1.0",val)
                    self._recount(key)
        if self._preview_lbls:
            val = self.result.get("prompt","") if self.mode=="prompt" else self.result.get("title","")
            kw = self.result.get("kw","") if self.mode!="prompt" else ""
            desc = self.result.get("desc","") if self.mode!="prompt" else ""
            if "line1" in self._preview_lbls:
                line1=(val[:70]+"…") if len(val)>70 else (val or "—")
                self._preview_lbls["line1"].configure(text=line1)
            if "line2" in self._preview_lbls:
                sub=(desc[:60]+"…") if len(desc)>60 else desc
                kwn=len([x for x in kw.split(",") if x.strip()])
                self._preview_lbls["line2"].configure(
                    text=f"{sub}   ·   {kwn} keywords" if sub else f"{kwn} keywords")
        self._refresh_status()

    def set_waiting(self):
        self.result={"status":"waiting"}
        self._refresh_status()

    def set_working(self):
        self.result={"status":"working"}
        self._refresh_status()

    def _copy(self,key):
        val=self._boxes[key].get("1.0","end-1c") if key in self._boxes else self.result.get(key,"")
        self.clipboard_clear(); self.clipboard_append(val)

    def _paste(self,key):
        try: clip=self.clipboard_get()
        except: return
        if key in self._boxes:
            self._boxes[key].delete("1.0","end")
            self._boxes[key].insert("1.0",clip)
            self.result[key]=clip

    def get_result(self):
        for k,b in self._boxes.items():
            self.result[k]=b.get("1.0","end-1c")
        return self.result

    def _load_thumb(self):
        s=self.THUMB_SIZE-2
        img=make_thumb(self.path,(s,s))
        if img:
            self.after(0,lambda:(self._tlbl.configure(image=img,text=""),
                setattr(self._tlbl,"_image",img)))


class ModernDropdown(ctk.CTkFrame):
    """A CTkComboBox replacement with a fully custom-styled popup.

    CTk's built-in dropdown is a raw tkinter.Menu under the hood — on
    Windows that delegates its outer border/chrome to the native OS menu
    renderer, which can't be recolored through any CTk color option (this
    is a real Tk limitation, not a missed setting). This widget instead
    opens a borderless Toplevel (no native chrome at all) containing a
    CTkFrame we draw ourselves, so the whole popup — background, border,
    corner radius, hover color — actually matches the rest of the app.

    Same variable/set()/command surface as CTkComboBox in readonly mode,
    so it's a drop-in replacement anywhere that used one that way.
    """
    def __init__(self,master,variable,values,command=None,
                 fg_color=None,text_color=None,border_color=None,
                 accent_color=None,dropdown_fg_color=None,
                 dropdown_hover_color=None,dropdown_text_color=None,
                 font=None,height=36,corner_radius=8,**kw):
        super().__init__(master,fg_color=fg_color,corner_radius=corner_radius,
            border_width=2,border_color=border_color,height=height,**kw)
        self.grid_propagate(False)
        self.variable=variable; self.values=values; self.command=command
        self._popup=None
        self._accent=accent_color or text_color
        self._dropdown_fg=dropdown_fg_color or fg_color
        self._dropdown_hover=dropdown_hover_color
        self._dropdown_text=dropdown_text_color or text_color
        self._font=font
        self.grid_columnconfigure(0,weight=1)

        self._label=ctk.CTkLabel(self,text=variable.get(),anchor="w",
            font=font,text_color=text_color,fg_color="transparent")
        self._label.grid(row=0,column=0,sticky="ew",padx=(12,4))
        self._arrow=ctk.CTkLabel(self,text="▾",font=font,
            text_color=text_color,fg_color="transparent",width=16)
        self._arrow.grid(row=0,column=1,sticky="e",padx=(0,10))
        for w in (self,self._label,self._arrow):
            w.bind("<Button-1>",self._toggle)
            w.configure(cursor="hand2")

    def set(self,value):
        self.variable.set(value)
        self._label.configure(text=value)

    def _toggle(self,_e=None):
        if self._popup is not None and self._popup.winfo_exists():
            self._close()
        else:
            self._open()

    def _open(self):
        self.update_idletasks()
        x=self.winfo_rootx(); y=self.winfo_rooty()+self.winfo_height()+2
        w=self.winfo_width()
        row_h=32; pad=6
        popup_h=min(len(self.values)*row_h+pad*2,320)
        top=tkinter.Toplevel(self)
        top.overrideredirect(True)
        try: top.attributes("-topmost",True)
        except Exception: pass
        top.geometry(f"{w}x{popup_h}+{x}+{y}")
        frame=ctk.CTkFrame(top,fg_color=self._dropdown_fg,corner_radius=8,
            border_width=1,border_color=self._accent)
        frame.pack(fill="both",expand=True)
        holder=frame
        if len(self.values)*row_h>popup_h-pad*2:
            holder=ctk.CTkScrollableFrame(frame,fg_color="transparent",
                scrollbar_button_color=self._dropdown_fg)
            holder.pack(fill="both",expand=True,padx=2,pady=2)
        for val in self.values:
            row=ctk.CTkButton(holder,text=val,anchor="w",height=row_h-2,
                font=self._font,fg_color="transparent",
                hover_color=self._dropdown_hover,text_color=self._dropdown_text,
                corner_radius=6,command=lambda v=val:self._select(v))
            row.pack(fill="x",padx=4,pady=1)
        top.bind("<FocusOut>",lambda e:self._close())
        top.focus_force()
        self._popup=top

    def _select(self,value):
        self.set(value)
        self._close()
        if self.command:
            self.command(value)

    def _close(self):
        if self._popup is not None and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup=None


class CompactEditCard(ctk.CTkFrame):
    """The user-facing "Compact View" card — distinct from MetaResultCard's
    internal large-batch performance-compact mode (which is read-only by
    design). This one is genuinely editable: small thumbnail, small
    title/description/keywords textboxes with live character/keyword
    counters, and an icon-only Regenerate button — no bordered info box,
    no full status chrome, nothing beyond what's needed to scan and
    lightly edit a page of results quickly.

    Exposes the same _boxes / get_result() surface as MetaResultCard, so
    the app's existing edit-sync logic (_sync_card_edits) works on this
    card unchanged."""
    def __init__(self,master,path,result,on_redo,mode="meta",
                 show_desc=True,request_thumb=None,**kw):
        super().__init__(master,fg_color=BG3,corner_radius=8,
            border_width=1,border_color=GLASS_BDR,**kw)
        self.path=path; self.result=dict(result); self.mode=mode
        self.show_desc=show_desc
        self._boxes={}; self._counter_lbls={}
        self._build(on_redo,request_thumb)

    def _build(self,on_redo,request_thumb):
        self.grid_columnconfigure(0,weight=1)
        top=ctk.CTkFrame(self,fg_color="transparent",corner_radius=0)
        top.grid(row=0,column=0,sticky="ew",padx=8,pady=(8,4))
        top.grid_columnconfigure(1,weight=1)

        tf=ctk.CTkFrame(top,fg_color=BG4,corner_radius=6,width=36,height=36)
        tf.grid(row=0,column=0,rowspan=2,padx=(0,6)); tf.grid_propagate(False)
        self._tlbl=ctk.CTkLabel(tf,text="🖼",font=ctk.CTkFont("Segoe UI",14),
            fg_color=BG4,text_color=TXT3,width=34,height=34,corner_radius=6)
        self._tlbl.pack()
        if request_thumb:
            request_thumb(self.path,self._tlbl,size=(34,34))

        fname=os.path.basename(self.path)
        ctk.CTkLabel(top,text=(fname[:22]+"…") if len(fname)>22 else fname,
            font=ctk.CTkFont("Segoe UI",9),text_color=TXT3,
            fg_color="transparent",anchor="w"
        ).grid(row=0,column=1,rowspan=2,sticky="w")

        self._redo_btn=ctk.CTkButton(top,text="⟳",width=26,height=26,
            font=ctk.CTkFont("Segoe UI",13,"bold"),
            fg_color=BG4,hover_color=AMB_DIM,text_color=AMB_BTN,
            corner_radius=6,command=on_redo)
        self._redo_btn.grid(row=0,column=2,rowspan=2,padx=(4,0))

        if self.mode=="prompt":
            self._build_field(1,"prompt","Prompt",self.result.get("prompt",""),CYAN,70)
        else:
            self._build_field(1,"title","Title",self.result.get("title",""),CYAN,46)
            nxt=2
            if self.show_desc:
                self._build_field(2,"desc","Description",self.result.get("desc",""),TXT2,46)
                nxt=3
            self._build_field(nxt,"kw","Keywords",self.result.get("kw",""),GRN,46)

        self._refresh_status()

    def _build_field(self,row,key,label,value,color,height):
        wrap=ctk.CTkFrame(self,fg_color="transparent",corner_radius=0)
        wrap.grid(row=row,column=0,sticky="ew",padx=8,pady=(0,4))
        wrap.grid_columnconfigure(0,weight=1)
        hdr=ctk.CTkFrame(wrap,fg_color="transparent",corner_radius=0)
        hdr.grid(row=0,column=0,sticky="ew")
        hdr.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(hdr,text=label,font=ctk.CTkFont("Segoe UI",8,"bold"),
            text_color=TXT3,fg_color="transparent").grid(row=0,column=0,sticky="w")
        counter=ctk.CTkLabel(hdr,text="",font=ctk.CTkFont("Segoe UI",8),
            text_color=TXT3,fg_color="transparent")
        counter.grid(row=0,column=1,sticky="e")
        self._counter_lbls[key]=counter

        box=ctk.CTkTextbox(wrap,height=height,font=ctk.CTkFont("Segoe UI",10),
            fg_color=BG4,text_color=color,border_color=GLASS_BDR,border_width=1,
            corner_radius=6,wrap="word")
        box.grid(row=1,column=0,sticky="ew")
        box.insert("1.0",value)
        self._boxes[key]=box
        self._update_counter(key)
        box.bind("<KeyRelease>",lambda e,k=key:self._update_counter(k))

    def _update_counter(self,key):
        box=self._boxes.get(key); lbl=self._counter_lbls.get(key)
        if not box or not lbl: return
        text=box.get("1.0","end-1c")
        if key=="kw":
            n=len([k for k in text.split(",") if k.strip()])
            lbl.configure(text=f"{n} kw")
        else:
            lbl.configure(text=f"{len(text)} ch")

    def _refresh_status(self):
        st=self.result.get("status","")
        color={"done":GRN,"failed":RED_BTN,"waiting":TXT3,"working":AMB_BTN}.get(st,TXT3)
        try: self.configure(border_color=color if st in ("done","failed","working") else GLASS_BDR)
        except Exception: pass

    def apply_result(self,result):
        """Update this card IN PLACE with a new result dict — same
        purpose as MetaResultCard.apply_result: avoids destroying/
        recreating the card on every generation, redo, or retry."""
        self.result=dict(result)
        keys=("prompt",) if self.mode=="prompt" else \
             (("title","desc","kw") if self.show_desc else ("title","kw"))
        for key in keys:
            box=self._boxes.get(key)
            if box:
                box.delete("1.0","end")
                val=self.result.get(key,"")
                if val: box.insert("1.0",val)
                self._update_counter(key)
        self._refresh_status()

    def set_waiting(self):
        self.result={"status":"waiting"}
        self._refresh_status()

    def get_result(self):
        for k,b in self._boxes.items():
            self.result[k]=b.get("1.0","end-1c")
        return self.result

