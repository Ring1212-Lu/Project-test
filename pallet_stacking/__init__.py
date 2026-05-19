"""Pallet Stacking Optimization Tool.

A Cape Pack–style Python toolkit for computing optimal carton-to-pallet
stacking patterns, rendering engineering-style drawings, and producing
PDF + Excel reports.

All dimensions are in **millimetres (mm)**, all weights in **kilograms (kg)**.

Globally consistent face colour rule (used by every renderer & report):
"""

__version__ = "2.0.0"

# Globally consistent face colour rule (do NOT change here without also
# updating the user-facing documentation).
FACE_COLORS = {
    "front": "#1f77ff",   # blue
    "side":  "#2ca02c",   # green
    "top":   "#ff8c1a",   # orange
}

# Line style rules for engineering drawings
LINE_STYLE = {
    "pallet_outline":       {"linewidth": 1.8, "color": "black",  "linestyle": "-"},
    "usable_area_outline":  {"linewidth": 0.9, "color": "#555",  "linestyle": "--"},
    "reserved_margin_fill": {"facecolor": "#ffe9e1", "edgecolor": "#bb4a3a",
                             "linestyle": ":", "linewidth": 0.6, "alpha": 0.4},
    "carton_edge":          {"linewidth": 0.35, "color": "black"},
    "dimension":            {"linewidth": 0.6, "color": "black"},
}
