"""Modern dark theme for the tkinter GUI.

Uses ``sv-ttk`` (Sun Valley) when available for a polished Windows-11-like
look, then layers our own accent colours and typography on top via
``ttk.Style``.  Falls back to a hand-tuned dark palette when sv-ttk is
not installed, so the GUI still looks reasonable without the optional
dependency.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

PALETTE = {
    "bg_root":     "#1a1d3a",   # deepest background
    "bg_panel":    "#22264a",   # card / panel background
    "bg_elevated": "#2c3164",   # entries, buttons
    "bg_hover":    "#363c75",
    "text":        "#ffffff",
    "text_muted":  "#a8acc4",
    "accent":      "#9b6bff",   # primary accent (purple)
    "accent_alt":  "#6b9bff",   # secondary accent (blue)
    "accent_warn": "#ff6bd5",   # tertiary (pink/magenta)
    "border":      "#3a3e75",
    "success":     "#3ce0a0",
}


def _resolve_font(root: tk.Tk) -> str:
    """Pick the best modern sans-serif available on this system."""
    families = set(tkfont.families(root))
    for name in ("Segoe UI", "SF Pro Text", "Helvetica Neue",
                 "Inter", "Roboto", "Noto Sans", "DejaVu Sans"):
        if name in families:
            return name
    return "TkDefaultFont"


# ---------------------------------------------------------------------------
# Theme entry point
# ---------------------------------------------------------------------------

def apply_modern_theme(root: tk.Tk) -> None:
    """Apply the dark theme to ``root`` and every widget created afterwards."""
    # Sun Valley dark gives a good base (rounded buttons, modern feel).
    try:
        import sv_ttk
        sv_ttk.set_theme("dark")
    except Exception:
        pass

    font_family = _resolve_font(root)

    # Named fonts -> let every widget inherit consistent typography
    tkfont.nametofont("TkDefaultFont").configure(family=font_family, size=10)
    tkfont.nametofont("TkTextFont").configure(family=font_family, size=10)
    tkfont.nametofont("TkHeadingFont").configure(
        family=font_family, size=11, weight="bold")
    tkfont.nametofont("TkMenuFont").configure(family=font_family, size=10)
    tkfont.nametofont("TkFixedFont").configure(family=font_family, size=10)

    root.configure(bg=PALETTE["bg_root"])

    style = ttk.Style(root)

    # Frame backgrounds
    style.configure("TFrame",      background=PALETTE["bg_root"])
    style.configure("Card.TFrame", background=PALETTE["bg_panel"])

    # LabelFrame: card-style header
    style.configure("Card.TLabelframe",
                    background=PALETTE["bg_panel"],
                    bordercolor=PALETTE["border"],
                    lightcolor=PALETTE["border"],
                    darkcolor=PALETTE["border"],
                    relief="flat",
                    borderwidth=1)
    style.configure("Card.TLabelframe.Label",
                    background=PALETTE["bg_panel"],
                    foreground=PALETTE["accent"],
                    font=(font_family, 10, "bold"))

    # Labels
    style.configure("TLabel",
                    background=PALETTE["bg_panel"],
                    foreground=PALETTE["text"])
    style.configure("Muted.TLabel",
                    background=PALETTE["bg_panel"],
                    foreground=PALETTE["text_muted"])
    style.configure("Status.TLabel",
                    background=PALETTE["bg_root"],
                    foreground=PALETTE["text_muted"],
                    padding=4)

    # Entry / Combobox
    style.configure("TEntry",
                    fieldbackground=PALETTE["bg_elevated"],
                    foreground=PALETTE["text"],
                    insertcolor=PALETTE["accent"],
                    bordercolor=PALETTE["border"],
                    lightcolor=PALETTE["border"],
                    darkcolor=PALETTE["border"])
    style.configure("TCombobox",
                    fieldbackground=PALETTE["bg_elevated"],
                    background=PALETTE["bg_elevated"],
                    foreground=PALETTE["text"],
                    arrowcolor=PALETTE["accent"],
                    bordercolor=PALETTE["border"])
    style.map("TCombobox",
              fieldbackground=[("readonly", PALETTE["bg_elevated"])],
              foreground=[("readonly", PALETTE["text"])])
    root.option_add("*TCombobox*Listbox.background", PALETTE["bg_elevated"])
    root.option_add("*TCombobox*Listbox.foreground", PALETTE["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", PALETTE["text"])

    # Buttons
    style.configure("TButton",
                    background=PALETTE["bg_elevated"],
                    foreground=PALETTE["text"],
                    bordercolor=PALETTE["border"],
                    focusthickness=0,
                    padding=(10, 6))
    style.map("TButton",
              background=[("active", PALETTE["bg_hover"]),
                          ("pressed", PALETTE["accent"])],
              foreground=[("disabled", PALETTE["text_muted"])])

    style.configure("Accent.TButton",
                    background=PALETTE["accent"],
                    foreground="#ffffff",
                    font=(font_family, 10, "bold"),
                    padding=(12, 8))
    style.map("Accent.TButton",
              background=[("active", "#b08aff"),
                          ("pressed", "#7a4dd9")])

    # Checkbutton
    style.configure("TCheckbutton",
                    background=PALETTE["bg_panel"],
                    foreground=PALETTE["text"],
                    focuscolor=PALETTE["accent"])
    style.map("TCheckbutton",
              background=[("active", PALETTE["bg_panel"])])

    # Treeview (top-N table)
    style.configure("Treeview",
                    background=PALETTE["bg_elevated"],
                    fieldbackground=PALETTE["bg_elevated"],
                    foreground=PALETTE["text"],
                    bordercolor=PALETTE["border"],
                    rowheight=22)
    style.configure("Treeview.Heading",
                    background=PALETTE["accent"],
                    foreground="#ffffff",
                    relief="flat",
                    font=(font_family, 9, "bold"))
    style.map("Treeview.Heading",
              background=[("active", "#7a4dd9")])
    style.map("Treeview",
              background=[("selected", PALETTE["accent"])],
              foreground=[("selected", "#ffffff")])

    # Scrollbar
    style.configure("Vertical.TScrollbar",
                    background=PALETTE["bg_panel"],
                    troughcolor=PALETTE["bg_root"],
                    bordercolor=PALETTE["bg_root"],
                    arrowcolor=PALETTE["text_muted"])

    # Matplotlib figure backgrounds — the GUI embeds figures; this helper
    # is for callers to use when creating new Figure objects.

def figure_facecolor() -> str:
    return PALETTE["bg_panel"]
