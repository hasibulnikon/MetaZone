"""Configuration popup — API Keys and Theme, as two pages behind a page
selector at the top."""
import threading
import customtkinter as ctk
from tkinter import messagebox, StringVar
from core.constants import (AI_PROVIDERS, VISIBLE_PROVIDERS,
    THEME_BG_PRESETS, THEME_ACCENT_PRESETS)
from core.config import save_prefs
from core.utils import model_label, model_id_from_label, relaunch_app
from engine.ai_providers import validate_key
from ui.theme import (BG1,BG2,BG3,BG4,GLASS_BDR,GLASS_BDR_AC,TXT,TXT2,TXT3,
    GRN,GRN_H,GRN_DIM,RED_BTN,RED_DIM,AMB_BTN,ABSOLUTE_BG)

class APIManagerWindow(ctk.CTkToplevel):
    def __init__(self,parent,prefs,on_close=None):
        super().__init__(parent); self.title("Configuration")
        self.configure(fg_color=BG1); self.resizable(False,False); self.grab_set()
        self.prefs=prefs; self.on_close=on_close; self._cur=VISIBLE_PROVIDERS[0]
        self._page="keys"
        self._build(); self._center(920,620)
        self.protocol("WM_DELETE_WINDOW",self._done)

    def _center(self,w,h):
        self.update_idletasks()
        x=self.master.winfo_x()+(self.master.winfo_width()-w)//2
        y=self.master.winfo_y()+(self.master.winfo_height()-h)//2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _tab_text(self,p):
        n=sum(1 for k in self.prefs.get("ai_keys",{}).get(p,[]) if k.get("active"))
        return p+(f" ●{n}" if n else "")

    def _build(self):
        self.grid_columnconfigure(0,weight=1); self.grid_rowconfigure(3,weight=1)
        hdr=ctk.CTkFrame(self,fg_color=BG2,corner_radius=0,height=52)
        hdr.grid(row=0,column=0,sticky="ew"); hdr.grid_propagate(False)
        hdr.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(hdr,text="Configuration",
            font=ctk.CTkFont("Segoe UI",15,"bold"),text_color=TXT,fg_color=BG2
        ).grid(row=0,column=0,sticky="w",padx=18,pady=14)
        ctk.CTkButton(hdr,text="✕",width=34,height=34,fg_color="transparent",
            hover_color=RED_DIM,text_color=TXT3,corner_radius=6,command=self._done
        ).grid(row=0,column=1,padx=10)

        page_bar=ctk.CTkFrame(self,fg_color=BG2,corner_radius=0,height=44)
        page_bar.grid(row=1,column=0,sticky="ew"); page_bar.grid_propagate(False)
        self._page_keys_btn=ctk.CTkButton(page_bar,text="API Keys",width=120,height=30,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,corner_radius=8,
            command=lambda:self._switch_page("keys"))
        self._page_keys_btn.pack(side="left",padx=(14,4),pady=7)
        self._page_theme_btn=ctk.CTkButton(page_bar,text="Theme",width=120,height=30,
            font=ctk.CTkFont("Segoe UI",11,"bold"),
            fg_color="transparent",hover_color=BG3,text_color=TXT3,corner_radius=8,
            command=lambda:self._switch_page("theme"))
        self._page_theme_btn.pack(side="left",padx=4,pady=7)

        tab_bar=ctk.CTkFrame(self,fg_color=BG2,corner_radius=0,height=50)
        tab_bar.grid(row=2,column=0,sticky="ew"); tab_bar.grid_propagate(False)
        self._keys_tab_bar=tab_bar
        self._tabs={}
        for p in VISIBLE_PROVIDERS:
            btn=ctk.CTkButton(tab_bar,text=self._tab_text(p),width=116,height=34,
                font=ctk.CTkFont("Segoe UI",11,"bold"),
                fg_color=GRN if p==self._cur else BG3,
                hover_color=GRN_H,text_color=ABSOLUTE_BG if p==self._cur else TXT2,
                corner_radius=8,command=lambda pv=p:self._switch(pv))
            btn.pack(side="left",padx=(8 if p==VISIBLE_PROVIDERS[0] else 3,0),pady=8)
            self._tabs[p]=btn
        self._keys_body=ctk.CTkFrame(self,fg_color=BG1,corner_radius=0)
        self._keys_body.grid(row=3,column=0,sticky="nsew")
        self._keys_body.grid_columnconfigure(0,weight=0); self._keys_body.grid_columnconfigure(1,weight=1)
        self._keys_body.grid_rowconfigure(0,weight=1)
        self._lp=ctk.CTkFrame(self._keys_body,fg_color=BG2,corner_radius=0,width=420)
        self._lp.grid(row=0,column=0,sticky="nsew"); self._lp.grid_propagate(False)
        self._rp=ctk.CTkFrame(self._keys_body,fg_color=BG1,corner_radius=0)
        self._rp.grid(row=0,column=1,sticky="nsew",padx=(1,0))
        self._rp.grid_columnconfigure(0,weight=1); self._rp.grid_rowconfigure(1,weight=1)

        self._theme_body=ctk.CTkFrame(self,fg_color=BG1,corner_radius=0)
        self._theme_body.grid(row=2,column=0,rowspan=2,sticky="nsew")
        self._theme_body.grid_remove()  # hidden until Theme page is selected
        self._build_theme_page()

        ftr=ctk.CTkFrame(self,fg_color=BG2,corner_radius=0,height=52)
        ftr.grid(row=4,column=0,sticky="ew"); ftr.grid_propagate(False)
        ctk.CTkButton(ftr,text="Done",width=100,height=34,
            font=ctk.CTkFont("Segoe UI",13,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,corner_radius=8,
            command=self._done).pack(side="right",padx=16,pady=9)
        self._render()

    def _switch_page(self,page):
        self._page=page
        keys_active=(page=="keys")
        self._page_keys_btn.configure(fg_color=GRN if keys_active else "transparent",
            text_color=ABSOLUTE_BG if keys_active else TXT3)
        self._page_theme_btn.configure(fg_color="transparent" if keys_active else GRN,
            text_color=TXT3 if keys_active else ABSOLUTE_BG)
        if keys_active:
            self._keys_tab_bar.grid(); self._keys_body.grid()
            self._theme_body.grid_remove()
        else:
            self._keys_tab_bar.grid_remove(); self._keys_body.grid_remove()
            self._theme_body.grid()

    def _build_theme_page(self):
        from ui.theme import BG1 as _cur_bg1
        self._staged_bg=self.prefs.get("theme_bg_base") or _cur_bg1
        self._staged_accent=self.prefs.get("theme_accent_base") or GRN

        wrap=ctk.CTkScrollableFrame(self._theme_body,fg_color=BG1,corner_radius=0)
        wrap.pack(fill="both",expand=True)

        ctk.CTkLabel(wrap,text="Background Color",font=ctk.CTkFont("Segoe UI",12,"bold"),
            text_color=TXT,fg_color=BG1).pack(anchor="w",padx=20,pady=(20,2))
        ctk.CTkLabel(wrap,text="One color — every panel/card shade is generated from it.",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,fg_color=BG1
        ).pack(anchor="w",padx=20,pady=(0,10))
        bg_row=ctk.CTkFrame(wrap,fg_color="transparent",corner_radius=0)
        bg_row.pack(anchor="w",padx=20,pady=(0,8))
        self._bg_swatches={}
        for name,hexval in THEME_BG_PRESETS.items():
            self._bg_swatches[hexval]=self._make_swatch(bg_row,hexval,
                lambda h=hexval:self._stage_bg(h))
        self._bg_hex_var=StringVar(value=self._staged_bg)
        self._build_hex_row(wrap,self._bg_hex_var,self._stage_bg)

        ctk.CTkFrame(wrap,fg_color=GLASS_BDR,height=1,corner_radius=0).pack(fill="x",padx=20,pady=16)

        ctk.CTkLabel(wrap,text="Accent Color",font=ctk.CTkFont("Segoe UI",12,"bold"),
            text_color=TXT,fg_color=BG1).pack(anchor="w",padx=20,pady=(0,2))
        ctk.CTkLabel(wrap,text="Used for buttons, highlights, and active states.",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,fg_color=BG1
        ).pack(anchor="w",padx=20,pady=(0,10))
        acc_row=ctk.CTkFrame(wrap,fg_color="transparent",corner_radius=0)
        acc_row.pack(anchor="w",padx=20,pady=(0,8))
        self._accent_swatches={}
        for name,hexval in THEME_ACCENT_PRESETS.items():
            self._accent_swatches[hexval]=self._make_swatch(acc_row,hexval,
                lambda h=hexval:self._stage_accent(h))
        self._accent_hex_var=StringVar(value=self._staged_accent)
        self._build_hex_row(wrap,self._accent_hex_var,self._stage_accent)

        self._refresh_swatch_selection()

        note=ctk.CTkLabel(wrap,
            text="Applying restarts Meta Zone to take effect. If you have\n"
                 "unsaved files loaded, save or export them first.",
            font=ctk.CTkFont("Segoe UI",10),text_color=TXT3,fg_color=BG1,justify="left")
        note.pack(anchor="w",padx=20,pady=(20,10))
        ctk.CTkButton(wrap,text="Apply Theme (Restart)",height=40,width=220,
            font=ctk.CTkFont("Segoe UI",12,"bold"),
            fg_color=GRN,hover_color=GRN_H,text_color=ABSOLUTE_BG,corner_radius=8,
            command=self._confirm_apply_theme).pack(anchor="w",padx=20,pady=(0,20))

    def _make_swatch(self,parent,hexval,command):
        b=ctk.CTkButton(parent,text="",width=34,height=34,corner_radius=17,
            fg_color=hexval,hover_color=hexval,border_width=2,border_color=BG3,
            command=command)
        b.pack(side="left",padx=5)
        return b

    def _build_hex_row(self,parent,var,on_change):
        row=ctk.CTkFrame(parent,fg_color="transparent",corner_radius=0)
        row.pack(anchor="w",padx=20,pady=(4,0))
        ctk.CTkLabel(row,text="Hex:",font=ctk.CTkFont("Segoe UI",10),
            text_color=TXT3,fg_color="transparent").pack(side="left",padx=(0,6))
        ent=ctk.CTkEntry(row,textvariable=var,width=110,height=28,
            font=ctk.CTkFont("Consolas",11),fg_color=BG3,text_color=TXT,
            border_color=GLASS_BDR,corner_radius=6)
        ent.pack(side="left")
        def _commit(_e=None):
            v=var.get().strip()
            if not v.startswith("#"): v="#"+v
            if len(v) in (4,7):
                on_change(v)
            else:
                messagebox.showwarning("Invalid hex","Use a hex color like #1a1a1a.",parent=self)
        ent.bind("<Return>",_commit); ent.bind("<FocusOut>",_commit)

    def _stage_bg(self,hexval):
        self._staged_bg=hexval; self._bg_hex_var.set(hexval)
        self._refresh_swatch_selection()

    def _stage_accent(self,hexval):
        self._staged_accent=hexval; self._accent_hex_var.set(hexval)
        self._refresh_swatch_selection()

    def _refresh_swatch_selection(self):
        for hexval,btn in self._bg_swatches.items():
            btn.configure(border_color=GRN if hexval.lower()==self._staged_bg.lower() else BG3)
        for hexval,btn in self._accent_swatches.items():
            btn.configure(border_color=GRN if hexval.lower()==self._staged_accent.lower() else BG3)

    def _confirm_apply_theme(self):
        proceed=messagebox.askyesno("Apply Theme",
            "Meta Zone needs to restart to apply the new theme.\n\n"
            "If you have files loaded that you haven't saved or exported "
            "yet, they'll be lost — save or export first if needed.\n\n"
            "Restart now?",parent=self)
        if not proceed: return
        self.prefs["theme_bg_base"]=self._staged_bg
        self.prefs["theme_accent_base"]=self._staged_accent
        save_prefs(self.prefs)
        relaunch_app()

    def _switch(self,p):
        self._cur=p
        for pv,btn in self._tabs.items():
            s=(pv==p)
            btn.configure(fg_color=GRN if s else BG3,
                text_color=ABSOLUTE_BG if s else TXT2,
                text=self._tab_text(pv))
        self._render()

    def _render(self):
        for w in self._lp.winfo_children(): w.destroy()
        for w in self._rp.winfo_children(): w.destroy()
        p=self._cur; cfg=AI_PROVIDERS[p]
        keys=self.prefs.setdefault("ai_keys",{}).setdefault(p,[])
        models=cfg["models"]
        cur_id=self.prefs.setdefault("ai_models",{}).get(p,models[0][1])
        inner=ctk.CTkScrollableFrame(self._lp,fg_color=BG2,scrollbar_button_color=BG3,corner_radius=0)
        inner.place(relx=0,rely=0,relwidth=1,relheight=1)
        inner.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(inner,text="CONFIGURATION",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=GRN,fg_color=BG2).pack(anchor="w",padx=18,pady=(16,8))
        ctk.CTkLabel(inner,text="Model Selection",font=ctk.CTkFont("Segoe UI",12),
            text_color=TXT2,fg_color=BG2).pack(anchor="w",padx=18,pady=(0,4))
        mv=StringVar(value=model_label(p,cur_id))
        ctk.CTkComboBox(inner,variable=mv,values=[m[0] for m in models],state="readonly",
            font=ctk.CTkFont("Segoe UI",12),fg_color=BG3,text_color=TXT,border_color=GRN_DIM,
            border_width=2,button_color=GRN,button_hover_color=GRN_H,dropdown_fg_color=BG4,
            dropdown_text_color=TXT,dropdown_hover_color=GRN_DIM,
            dropdown_font=ctk.CTkFont("Segoe UI",12),corner_radius=8,height=40,
            command=lambda v:self._save_model(p,v)).pack(fill="x",padx=18,pady=(0,16))
        ctk.CTkFrame(inner,fg_color=GLASS_BDR,height=1,corner_radius=0).pack(fill="x")
        ctk.CTkLabel(inner,text="Add New API Key",font=ctk.CTkFont("Segoe UI",12),
            text_color=TXT2,fg_color=BG2).pack(anchor="w",padx=18,pady=(14,4))
        nkv=StringVar()
        er=ctk.CTkFrame(inner,fg_color=BG2,corner_radius=0)
        er.pack(fill="x",padx=18,pady=(0,4)); er.grid_columnconfigure(0,weight=1)
        entry=ctk.CTkEntry(er,textvariable=nkv,placeholder_text="Paste API key here...",show="•",
            font=ctk.CTkFont("Segoe UI",12),fg_color=BG3,text_color=TXT,
            border_color=GLASS_BDR,corner_radius=8,height=40)
        entry.grid(row=0,column=0,sticky="ew")
        vld_lbl=ctk.CTkLabel(inner,text="",font=ctk.CTkFont("Segoe UI",11),text_color=TXT3,fg_color=BG2)
        ctk.CTkButton(er,text="Save",width=76,height=40,
            font=ctk.CTkFont("Segoe UI",12,"bold"),fg_color=GRN,hover_color=GRN_H,
            text_color=ABSOLUTE_BG,corner_radius=8,
            command=lambda:self._add_key(p,nkv.get().strip(),vld_lbl)
        ).grid(row=0,column=1,padx=(8,0))
        vld_lbl.pack(anchor="w",padx=18,pady=(2,10))
        def _live_validate(e=None):
            kv=nkv.get().strip()
            if len(kv)<8: vld_lbl.configure(text="",text_color=TXT3); return
            vld_lbl.configure(text="⟳  Checking…",text_color=AMB_BTN)
            def _run():
                ok,msg=validate_key(p,kv)
                self.after(0,lambda:vld_lbl.configure(
                    text="✓  Valid" if ok else f"✗  {msg}",text_color=GRN if ok else RED_BTN))
            threading.Thread(target=_run,daemon=True).start()
        entry.bind("<FocusOut>",_live_validate); entry.bind("<Return>",_live_validate)
        ctk.CTkButton(inner,text=f"🔑  Get API Key from {p}",height=38,
            font=ctk.CTkFont("Segoe UI",11),fg_color=BG3,hover_color=BG4,text_color=TXT2,
            border_width=1,border_color=GLASS_BDR,corner_radius=8,
            command=lambda:self._open_url(cfg["key_url"])).pack(fill="x",padx=18,pady=(0,10))
        aa_row=ctk.CTkFrame(inner,fg_color=BG2,corner_radius=0)
        aa_row.pack(fill="x",padx=18,pady=(0,18))
        aa_row.grid_columnconfigure((0,1),weight=1)
        ctk.CTkButton(aa_row,text="Activate All",height=34,
            font=ctk.CTkFont("Segoe UI",11,"bold"),fg_color=BG3,hover_color=GRN_DIM,
            text_color=TXT2,border_width=1,border_color=GLASS_BDR,corner_radius=8,
            command=lambda:self._activate_all(p)).grid(row=0,column=0,sticky="ew",padx=(0,4))
        ctk.CTkButton(aa_row,text="Deactivate All",height=34,
            font=ctk.CTkFont("Segoe UI",11,"bold"),fg_color=BG3,hover_color=RED_DIM,
            text_color=TXT2,border_width=1,border_color=GLASS_BDR,corner_radius=8,
            command=lambda:self._deactivate_all(p)).grid(row=0,column=1,sticky="ew",padx=(4,0))
        # RIGHT
        ctk.CTkLabel(self._rp,text="STORED KEYS",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=TXT2,fg_color=BG1).pack(anchor="w",padx=16,pady=(16,8))
        ks=ctk.CTkScrollableFrame(self._rp,fg_color=BG1,corner_radius=0,scrollbar_button_color=BG3)
        ks.pack(fill="both",expand=True); ks.grid_columnconfigure(0,weight=1)
        if not keys:
            ctk.CTkLabel(ks,text="No keys saved yet.",font=ctk.CTkFont("Segoe UI",12),
                text_color=TXT3,fg_color=BG1).pack(pady=30); return
        for i,k in enumerate(keys): self._key_card(ks,p,i,k)

    def _key_card(self,parent,prov,idx,k):
        is_active=k.get("active",False); kv=k.get("key","")
        key_disp="..."+kv[-10:] if len(kv)>10 else kv
        card=ctk.CTkFrame(parent,fg_color="#0a1a0a" if is_active else BG3,
            corner_radius=10,border_width=1,border_color=GLASS_BDR_AC if is_active else GLASS_BDR)
        card.pack(fill="x",padx=12,pady=(0,8)); card.grid_columnconfigure(1,weight=1)
        ctk.CTkLabel(card,text="🔑",font=ctk.CTkFont("Segoe UI",14),
            fg_color="transparent",text_color=TXT2).grid(row=0,column=0,padx=(12,8),pady=(10,4),sticky="w")
        kf=ctk.CTkFrame(card,fg_color="transparent",corner_radius=0)
        kf.grid(row=0,column=1,sticky="ew",pady=(10,4))
        ctk.CTkLabel(kf,text=key_disp,font=ctk.CTkFont("Consolas",12,"bold"),
            text_color=TXT,fg_color="transparent",anchor="w").pack(anchor="w")
        if is_active:
            ctk.CTkLabel(card,text="● Active",font=ctk.CTkFont("Segoe UI",10,"bold"),
                fg_color=GRN_DIM,text_color=GRN,corner_radius=20,padx=10,pady=3
            ).grid(row=0,column=2,padx=(0,10),pady=(10,4),sticky="e")
        af=ctk.CTkFrame(card,fg_color="transparent",corner_radius=0)
        af.grid(row=1,column=0,columnspan=3,sticky="ew",padx=10,pady=(0,8))
        ctk.CTkButton(af,text="👁",width=34,height=28,fg_color="transparent",hover_color=BG4,
            text_color=TXT3,corner_radius=6,
            command=lambda kv2=kv,lb=kf:self._toggle_show(kv2,lb)).pack(side="left",padx=(0,4))
        ctk.CTkButton(af,text="⧉",width=34,height=28,fg_color="transparent",hover_color=BG4,
            text_color=TXT3,corner_radius=6,
            command=lambda kv2=kv:self._copy(kv2)).pack(side="left",padx=(0,4))
        vl=ctk.CTkLabel(af,text="? Test",font=ctk.CTkFont("Segoe UI",10,"bold"),
            text_color=TXT3,fg_color=BG4,corner_radius=6,padx=8,pady=4,cursor="hand2")
        vl.pack(side="left",padx=(0,4))
        def _test(e=None,kv2=kv,lb=vl):
            lb.configure(text="⟳…",text_color=AMB_BTN)
            def _r():
                ok,msg=validate_key(prov,kv2)
                self.after(0,lambda:lb.configure(text="✓ OK" if ok else "✗ Bad",
                    text_color=GRN if ok else RED_BTN))
            threading.Thread(target=_r,daemon=True).start()
        vl.bind("<Button-1>",_test)
        if not is_active:
            ctk.CTkButton(af,text="Activate",width=84,height=28,
                font=ctk.CTkFont("Segoe UI",10,"bold"),fg_color=BG4,hover_color=GRN_DIM,
                text_color=TXT2,border_width=1,border_color=GLASS_BDR,corner_radius=6,
                command=lambda i=idx:self._activate(prov,i)).pack(side="left",padx=(0,4))
        else:
            ctk.CTkButton(af,text="Deactivate",width=90,height=28,
                font=ctk.CTkFont("Segoe UI",10,"bold"),fg_color=BG4,hover_color=RED_DIM,
                text_color=TXT2,border_width=1,border_color=GLASS_BDR,corner_radius=6,
                command=lambda i=idx:self._deactivate(prov,i)).pack(side="left",padx=(0,4))
        ctk.CTkButton(af,text="🗑",width=34,height=28,fg_color="transparent",
            hover_color=RED_DIM,text_color=TXT3,corner_radius=6,
            command=lambda i=idx:self._del(prov,i)).pack(side="right")

    def _toggle_show(self,kv,lf):
        ch=lf.winfo_children()
        if ch:
            short="..."+kv[-10:] if len(kv)>10 else kv
            ch[0].configure(text=kv if ch[0].cget("text")==short else short)
    def _copy(self,kv): self.clipboard_clear(); self.clipboard_append(kv)
    def _activate(self,p,i): self.prefs["ai_keys"][p][i]["active"]=True; save_prefs(self.prefs); self._switch(p)
    def _deactivate(self,p,i): self.prefs["ai_keys"][p][i]["active"]=False; save_prefs(self.prefs); self._switch(p)
    def _activate_all(self,p):
        keys=self.prefs.get("ai_keys",{}).get(p,[])
        if not keys: return
        for k in keys: k["active"]=True
        save_prefs(self.prefs); self._switch(p)
    def _deactivate_all(self,p):
        keys=self.prefs.get("ai_keys",{}).get(p,[])
        if not keys: return
        for k in keys: k["active"]=False
        save_prefs(self.prefs); self._switch(p)
    def _del(self,p,i):
        if not messagebox.askyesno("Delete","Delete this key?",parent=self): return
        self.prefs["ai_keys"][p].pop(i); save_prefs(self.prefs); self._switch(p)
    def _add_key(self,p,key,vld_lbl=None):
        if not key: messagebox.showwarning("Empty","Paste a key first.",parent=self); return
        keys=self.prefs["ai_keys"][p]
        if any(k["key"]==key for k in keys): messagebox.showinfo("Duplicate","Already saved.",parent=self); return
        # A new key joins as active WITHOUT touching any other key's active
        # state — this used to deactivate every other active key for the
        # provider, silently wiping out the whole failover set the moment
        # someone added one more key.
        keys.append({"key":key,"active":True})
        save_prefs(self.prefs); self._switch(p)
    def _save_model(self,p,label):
        self.prefs.setdefault("ai_models",{})[p]=model_id_from_label(p,label); save_prefs(self.prefs)
    def _open_url(self,url):
        import webbrowser; webbrowser.open(url)
    def _done(self):
        if self.on_close: self.on_close()
        self.destroy()


# ══════════════════════════════════════════════════════════════════════
#  EMBED WINDOW (compact popup)
# ══════════════════════════════════════════════════════════════════════
