"""Single source of truth for all colors, fonts, and layout constants
used across the UI. Change the app's look by editing this file only —
no other UI module should hardcode a color or spacing value.

NOTE: an older, entirely-unused legacy palette (deep-blue "META_*" and
deep-green "EMB_*" theme, plus RED/BLU aliases) was removed during the
module split — verified via static analysis that nothing referenced
those names anywhere in the app; this is the one real palette that was
already in effect (it was defined a second time later in the old single
file, silently overriding the dead one).
"""
import customtkinter as ctk

def init_ctk_theme():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")

# ── Black Glassmorphic palette ──────────────────────────────────────
BG1="#0a0a0a"; BG2="#111111"; BG3="#1a1a1a"; BG4="#222222"
GLASS="#161616"; GLASS_BDR="#2a2a2a"; GLASS_BDR_AC="#00c853"
TXT="#f0f0f0"; TXT2="#a0a0a0"; TXT3="#505050"
GRN="#00c853"; GRN_H="#00a040"; GRN_DIM="#00331a"
RED_BTN="#e53935"; RED_BTN_H="#b71c1c"; RED_DIM="#2a0000"
AMB_BTN="#f9a825"; AMB_BTN_H="#c67c00"; AMB_DIM="#2a1a00"
CYAN="#00e5ff"; LOG_BG="#050505"; ABSOLUTE_BG="#000000"

# Used only by the Embed tab's "checking online" status color. Kept as its
# own pair since it predates (and isn't part of) the AMB_BTN button colors
# above, but IS still actively referenced — verified via a full usage scan
# before removing anything else from the old legacy palette.
AMB="#f5c842"; AMB2="#2a2000"

# How many extra rows above/below the visible viewport stay materialized —
# a small cushion so a quick scroll does not show a blank flash before the
# next row's widget gets built.
VIRT_BUFFER=6
