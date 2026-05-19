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
    "carton_edge":          {"linewidth": 0.9, "color": "black"},
    "dimension":            {"linewidth": 0.6, "color": "black"},
}

# ---------------------------------------------------------------------------
# Presets (used by the GUI and the CLI)
# ---------------------------------------------------------------------------

# (label, length_mm, width_mm)
# Note: Standard / GMA pallet uses 120 x 100 cm.
# Euro pallet (EUR-1, ISO1) uses 120 x 80 cm.
# Euro 2 (ISO2) uses 100 x 120 cm. The user's "Euro 100x80" is the typical
# EUR-1 with the long side along Y.
PALLET_PRESETS = {
    "Standard 1200×1000 mm":  (1200, 1000),
    "Euro EUR-1 1200×800 mm": (1200,  800),
    "Euro 1000×800 mm":       (1000,  800),
    "Custom":                  None,
}

# (label, max_total_height_mm)
SHIPPING_PRESETS = {
    "Ocean / Sea (max 2200 mm)": 2200,
    "Air freight (max 1500 mm)": 1500,
    "Custom":                    None,
}
