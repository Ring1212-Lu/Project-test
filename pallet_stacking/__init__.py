"""Pallet Stacking Optimization Tool.

A Cape Pack-like Python toolkit for computing optimal carton-to-pallet
stacking patterns and producing professional PDF reports.
"""

__version__ = "1.0.0"
__all__ = [
    "optimizer",
    "renderer",
    "pdf_export",
    "gui",
]

# Globally consistent face colors used across all modules
FACE_COLORS = {
    "front": "#1f77ff",   # blue
    "side":  "#2ca02c",   # green
    "top":   "#ff8c1a",   # orange
}
