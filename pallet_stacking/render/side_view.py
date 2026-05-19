"""Side / Front orthographic elevations."""
from __future__ import annotations

from matplotlib.patches import Rectangle

from .. import FACE_COLORS, LINE_STYLE
from ..models import StackingResult
from ._common import add_dim_arrow, style_axis_2d


def draw_side_view(ax, result: StackingResult, show_dimensions: bool = True):
    """Elevation looking down the +Y axis (X / Z plane)."""
    pallet = result.pallet
    # pallet platform
    ax.add_patch(Rectangle((0, 0), pallet.length, pallet.height,
                           facecolor="#b08454",
                           edgecolor="black", linewidth=0.5))

    for layer in result.layers:
        for p in layer.placements:
            ax.add_patch(Rectangle(
                (p.x, pallet.height + p.z), p.dx, p.dz,
                facecolor=FACE_COLORS["side"],
                edgecolor=LINE_STYLE["carton_edge"]["color"],
                linewidth=LINE_STYLE["carton_edge"]["linewidth"]))

    stack_top = result.total_height()
    ax.set_xlim(-pallet.length * 0.08, pallet.length * 1.08)
    ax.set_ylim(-stack_top * 0.10, stack_top * 1.10)
    style_axis_2d(ax,
                  title=f"Side View — {result.layer_count} layers",
                  xlabel="X (mm)", ylabel="Z (mm)")

    if show_dimensions:
        add_dim_arrow(ax,
                      (0, -stack_top * 0.05),
                      (pallet.length, -stack_top * 0.05),
                      f"L = {pallet.length:.0f} mm")
        add_dim_arrow(ax,
                      (-pallet.length * 0.05, 0),
                      (-pallet.length * 0.05, stack_top),
                      f"H = {stack_top:.0f} mm", vertical=True)


def draw_front_view(ax, result: StackingResult, show_dimensions: bool = True):
    """Elevation looking down the +X axis (Y / Z plane)."""
    pallet = result.pallet
    ax.add_patch(Rectangle((0, 0), pallet.width, pallet.height,
                           facecolor="#b08454",
                           edgecolor="black", linewidth=0.5))

    for layer in result.layers:
        for p in layer.placements:
            ax.add_patch(Rectangle(
                (p.y, pallet.height + p.z), p.dy, p.dz,
                facecolor=FACE_COLORS["front"],
                edgecolor=LINE_STYLE["carton_edge"]["color"],
                linewidth=LINE_STYLE["carton_edge"]["linewidth"]))

    stack_top = result.total_height()
    ax.set_xlim(-pallet.width * 0.08, pallet.width * 1.08)
    ax.set_ylim(-stack_top * 0.10, stack_top * 1.10)
    style_axis_2d(ax,
                  title=f"Front View — {result.layer_count} layers",
                  xlabel="Y (mm)", ylabel="Z (mm)")

    if show_dimensions:
        add_dim_arrow(ax,
                      (0, -stack_top * 0.05),
                      (pallet.width, -stack_top * 0.05),
                      f"W = {pallet.width:.0f} mm")
        add_dim_arrow(ax,
                      (-pallet.width * 0.05, 0),
                      (-pallet.width * 0.05, stack_top),
                      f"H = {stack_top:.0f} mm", vertical=True)
