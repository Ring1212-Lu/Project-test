"""Orthographic / isometric 3D views (engineering drawing style)."""
from __future__ import annotations

from typing import Optional
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from .. import FACE_COLORS, LINE_STYLE
from ..models import StackingResult
from ._common import (
    setup_iso_axes, draw_box_3d, draw_wire_rect_3d,
    draw_pallet_platform_3d, set_axes_iso,
)


def draw_pallet_3d(ax, result: StackingResult, title: Optional[str] = None,
                   show_boundary: bool = True):
    pallet = result.pallet
    setup_iso_axes(ax)

    # pallet platform
    draw_pallet_platform_3d(ax, pallet)

    # reserved-margin wire on top of the pallet
    if show_boundary:
        # full pallet outline (thick)
        draw_wire_rect_3d(ax, (0, 0, pallet.height),
                          (pallet.length, pallet.width, pallet.height),
                          linewidth=LINE_STYLE["pallet_outline"]["linewidth"],
                          linestyle=LINE_STYLE["pallet_outline"]["linestyle"],
                          color=LINE_STYLE["pallet_outline"]["color"])
        # usable area outline (dashed)
        draw_wire_rect_3d(ax,
                          (pallet.margin_left, pallet.margin_front, pallet.height),
                          (pallet.margin_left + pallet.usable_length,
                           pallet.margin_front + pallet.usable_width,
                           pallet.height),
                          linewidth=LINE_STYLE["usable_area_outline"]["linewidth"],
                          linestyle=LINE_STYLE["usable_area_outline"]["linestyle"],
                          color=LINE_STYLE["usable_area_outline"]["color"])

    for layer in result.layers:
        for p in layer.placements:
            draw_box_3d(ax, p.x, p.y, pallet.height + p.z, p.dx, p.dy, p.dz)

    total_h = result.total_height()
    set_axes_iso(ax, pallet.length, pallet.width, total_h)
    ax.set_xlabel("X (mm)"); ax.set_ylabel("Y (mm)"); ax.set_zlabel("Z (mm)")
    if title is None:
        title = (f"3D Isometric — {result.total_cases} cartons "
                 f"({result.layout_name})")
    ax.set_title(title, fontsize=10)


def draw_single_carton_3d(ax, length: float, width: float, height: float,
                          title: str = "Carton orientation",
                          show_legend: bool = True):
    """Draw a single carton in canonical orientation (L along X, W along Y,
    H along Z).  Used for the orientation diagram in the report."""
    setup_iso_axes(ax)
    draw_box_3d(ax, 0, 0, 0, length, width, height)

    ax.set_xlabel("L (mm)"); ax.set_ylabel("W (mm)"); ax.set_zlabel("H (mm)")
    ax.set_title(title, fontsize=10)
    set_axes_iso(ax, length, width, height)

    if show_legend:
        legend = [
            Patch(facecolor=FACE_COLORS["front"], label="Front (blue)"),
            Patch(facecolor=FACE_COLORS["side"],  label="Side (green)"),
            Patch(facecolor=FACE_COLORS["top"],   label="Top (orange)"),
        ]
        ax.legend(handles=legend, loc="upper left", fontsize=7, frameon=False)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def save_figure(fig, path: str, dpi: int = 220):
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def build_overview_figure(result: StackingResult, fig=None):
    """Compose a four-panel engineering dashboard (used in GUI preview)."""
    if fig is None:
        fig = plt.figure(figsize=(11, 8.5))
    gs = fig.add_gridspec(2, 3, width_ratios=[1, 1, 1.2],
                          hspace=0.4, wspace=0.4)

    # carton orientation
    ax_case = fig.add_subplot(gs[0, 0], projection="3d")
    draw_single_carton_3d(ax_case,
                          result.carton.length,
                          result.carton.width,
                          result.carton.height,
                          title=(f"Carton {result.carton.length:.0f}×"
                                 f"{result.carton.width:.0f}×"
                                 f"{result.carton.height:.0f}"))

    # 3D pallet view (full height column)
    ax_3d = fig.add_subplot(gs[:, 2], projection="3d")
    draw_pallet_3d(ax_3d, result)

    # 2D views
    from .top_view  import draw_top_view
    from .side_view import draw_side_view, draw_front_view
    ax_top   = fig.add_subplot(gs[0, 1])
    draw_top_view(ax_top, result, layer_index=0)
    ax_side  = fig.add_subplot(gs[1, 0])
    draw_side_view(ax_side, result)
    ax_front = fig.add_subplot(gs[1, 1])
    draw_front_view(ax_front, result)

    return fig
