"""Drag-and-drop bootstrap: ensures tkinterdnd2 is importable (dev vs
frozen EXE), and the DnDCTk mixin the main window inherits from."""
import sys
import customtkinter as ctk

def _ensure_tkdnd():
    if getattr(sys, 'frozen', False):
        # tkinterdnd2's package files were bundled at _MEIPASS/tkinterdnd2/
        # (see build.yml's --add-data=...;tkinterdnd2). For "import
        # tkinterdnd2" to find them, the PARENT of that folder needs to be
        # on sys.path — i.e. _MEIPASS itself — not the tkinterdnd2 folder's
        # own path. Adding the package's own directory here was a bug:
        # Python would then look for _MEIPASS/tkinterdnd2/tkinterdnd2/,
        # which never exists, so this line silently did nothing useful.
        if sys._MEIPASS not in sys.path:
            sys.path.insert(0, sys._MEIPASS)
        return
    # Plain .py — install silently if missing
    try:
        import tkinterdnd2  # noqa: F401
    except ImportError:
        import subprocess as _sp
        try:
            _sp.check_call([sys.executable, "-m", "pip", "install",
                            "tkinterdnd2", "--quiet"], timeout=60)
        except Exception:
            pass

_ensure_tkdnd()

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False
    DND_FILES = None

# ══════════════════════════════════════════════════════════════════════
#  DnD MIXIN
# ══════════════════════════════════════════════════════════════════════
if DND_AVAILABLE:
    class DnDCTk(ctk.CTk,TkinterDnD.DnDWrapper):
        def __init__(self,*a,**kw):
            super().__init__(*a,**kw)
            self.dnd_native_ok=False
            try:
                self.TkdndVersion=TkinterDnD._require(self)
                self.dnd_native_ok=True
            except Exception as e:
                # Import succeeding (DND_AVAILABLE=True) does NOT guarantee
                # the native tkdnd Tcl extension actually loaded — this used
                # to fail silently with drag-and-drop just not doing
                # anything and no way to tell why. Now it's recorded so the
                # status bar can actually say so.
                self.dnd_native_error=str(e)
else:
    DnDCTk=ctk.CTk
