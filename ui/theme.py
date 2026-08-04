"""Single source of truth for all colors, fonts, and layout constants
used across the UI. Change the app's look by editing this file only —
no other UI module should hardcode a color or spacing value.

NOTE: an older, entirely-unused legacy palette (deep-blue "META_*" and
deep-green "EMB_*" theme, plus RED/BLU aliases) was removed during the
module split — verified via static analysis that nothing referenced
those names anywhere in the app; this is the one real palette that was
already in effect (it was defined a second time later in the old single
file, silently overriding the dead one).

THEME CUSTOMIZATION: background and accent are user-choosable (see
Configuration > Theme). Rather than storing every shade separately, the
person picks ONE background color and ONE accent color; every other
shade (BG1-BG4/GLASS/borders, accent hover/dim) is derived from those
two via a fixed lightness step — see generate_bg_ladder/
generate_accent_variants. This module reads the saved base colors once,
at import time, and computes the ladder — that's what makes "apply on
restart" work: a fresh process re-imports this module and gets the new
values, no live-reactive plumbing needed anywhere else in the app.
"""
import customtkinter as ctk
from core.config import load_prefs

def init_ctk_theme():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")


# ── Color math ───────────────────────────────────────────────────────
def _hex_to_rgb(h):
    h=h.lstrip("#")
    if len(h)==3: h="".join(c*2 for c in h)
    return tuple(int(h[i:i+2],16) for i in (0,2,4))

def _rgb_to_hex(rgb):
    r,g,b=(max(0,min(255,int(v))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"

def _lighten(hex_color,amount):
    r,g,b=_hex_to_rgb(hex_color)
    return _rgb_to_hex((r+amount,g+amount,b+amount))

def _darken(hex_color,amount):
    r,g,b=_hex_to_rgb(hex_color)
    return _rgb_to_hex((r-amount,g-amount,b-amount))

def generate_bg_ladder(base_hex):
    """One background color -> the full BG1-BG4/GLASS/border ladder,
    each step a little lighter than the last so panels/cards/borders
    stay visually distinct while reading as one consistent shade.

    NAV_BG is separate from this ladder's usual "lighter each step"
    direction — the nav rail needs to visually read as its own region
    against the main content (BG1), not just another panel shade. Darkens
    BG1 when there's room to (the normal case); when BG1 is already at or
    near black, darkening further wouldn't be visible, so it lightens
    slightly instead — either way, nav never ends up looking the same as
    the content behind it."""
    r,g,b=_hex_to_rgb(base_hex)
    very_light=(r+g+b)>650  # a hypothetical light theme — none of the
                             # current presets are, but this keeps the
                             # logic correct if one's ever added
    nav_bg=_darken(base_hex,9) if very_light else _lighten(base_hex,9)
    return {
        "BG1":base_hex,
        "BG2":_lighten(base_hex,7),
        "BG3":_lighten(base_hex,16),
        "BG4":_lighten(base_hex,24),
        "GLASS":_lighten(base_hex,10),
        "GLASS_BDR":_lighten(base_hex,32),
        "NAV_BG":nav_bg,
    }

def generate_accent_variants(base_hex):
    """One accent color -> its own hover (lighter, for button hover
    states) and dim (much darker, for subtle backgrounds behind badges/
    pills) variants."""
    return {
        "ACCENT":base_hex,
        "ACCENT_H":_lighten(base_hex,24),
        "ACCENT_DIM":_darken(base_hex,70),
    }


_prefs=load_prefs()
_bg_base=_prefs.get("theme_bg_base")
_accent_base=_prefs.get("theme_accent_base")

_bg=generate_bg_ladder(_bg_base) if _bg_base else generate_bg_ladder("#0a0a0a")
_accent=generate_accent_variants(_accent_base) if _accent_base else {
    "ACCENT":"#00c853","ACCENT_H":"#00a040","ACCENT_DIM":"#00331a",
}

# ── Palette — background/accent derived above; text and the semantic
# status colors (danger/warning) are NOT user-customizable by request,
# so they stay fixed regardless of theme choice. ──────────────────────
BG1=_bg["BG1"]; BG2=_bg["BG2"]; BG3=_bg["BG3"]; BG4=_bg["BG4"]
NAV_BG=_bg["NAV_BG"]
GLASS=_bg["GLASS"]; GLASS_BDR=_bg["GLASS_BDR"]; GLASS_BDR_AC=_accent["ACCENT"]
TXT="#f0f0f0"; TXT2="#a0a0a0"; TXT3="#505050"
GRN=_accent["ACCENT"]; GRN_H=_accent["ACCENT_H"]; GRN_DIM=_accent["ACCENT_DIM"]
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
