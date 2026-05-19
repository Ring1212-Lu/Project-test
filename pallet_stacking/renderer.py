"""Matplotlib rendering for the pallet stacking tool.

Provides:
    - draw_single_case_3d    (single carton with Front=blue / Side=green / Top=orange)
    - draw_top_view
    - draw_side_view
    - draw_front_view
    - draw_pallet_3d
    - save_figure            (helper that saves to a PNG at high DPI)

All drawings use a globally consistent face colour scheme defined in
``pallet_stacking.FACE_COLORS``.

Coordinate convention (mm):
    X axis -> pallet length (left/right)  -> "Side" face when looking down +X
    Y axis -> pallet width  (front/back)  -> "Front" face when looking down +Y
    Z axis -> upward                       -> "Top" face when looking down +Z
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple
import matplotlib
# Use a non-interactive backend so this works headless (when called from
# PDF export). The GUI module switches the backend when running interactively.
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyArrowPatch
from matplotlib.collections import PatchCollection
import numpy as np

# 3D
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from . import FACE_COLORS
from .optimizer import StackingSolution, LayerPattern, PlacedCase, Pallet


HIGH_DPI = 200


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _face_color_for(face_label: str, mapping: dict[str, str]) -> str:
    """Given a case face label ("L"/"W"/"H") and a mapping from face label
    to "front"/"side"/"top", return the actual color hex."""
    role = mapping.get(face_label, "side")
    return FACE_COLORS[role]


def _box_faces(x: float, y: float, z: float,
               dx: float, dy: float, dz: float,
               face_x_label: str = "L",
               face_y_label: str = "W",
               face_z_label: str = "H") -> Tuple[List[List[Tuple[float, float, float]]], List[str]]:
    """Return polygon faces and per-face fill colors for one box.

    Coloring rule (global, fixed):
      - The two faces normal to Y axis (front/back) => "front" colour (blue)
      - The two faces normal to X axis (left/right) => "side"  colour (green)
      - The two faces normal to Z axis (top/bottom) => "top"   colour (orange)
    Labels (face_*_label) are not used for colour here because the rule is
    based on pallet axes, not the case's own L/W/H. They are still kept in
    PlacedCase for downstream metadata.
    """
    p = [
        (x,    y,    z),
        (x+dx, y,    z),
        (x+dx, y+dy, z),
        (x,    y+dy, z),
        (x,    y,    z+dz),
        (x+dx, y,    z+dz),
        (x+dx, y+dy, z+dz),
        (x,    y+dy, z+dz),
    ]
    faces = [
        [p[0], p[1], p[2], p[3]],   # bottom (Z-)
        [p[4], p[5], p[6], p[7]],   # top    (Z+)
        [p[0], p[1], p[5], p[4]],   # front  (Y-)
        [p[3], p[2], p[6], p[7]],   # back   (Y+)
        [p[1], p[2], p[6], p[5]],   # right  (X+)
        [p[0], p[3], p[7], p[4]],   # left   (X-)
    ]
    colors = [
        FACE_COLORS["top"],     # bottom shares top color (same face)
        FACE_COLORS["top"],
        FACE_COLORS["front"],
        FACE_COLORS["front"],
        FACE_COLORS["side"],
        FACE_COLORS["side"],
    ]
    return faces, colors


def _draw_box_3d(ax, x, y, z, dx, dy, dz, alpha=0.9, edgecolor="k", linewidth=0.4):
    faces, colors = _box_faces(x, y, z, dx, dy, dz)
    coll = Poly3DCollection(faces, facecolors=colors, edgecolors=edgecolor,
                            linewidths=linewidth, alpha=alpha)
    ax.add_collection3d(coll)


def _set_axes_equal_3d(ax, x_range, y_range, z_range):
    max_range = max(x_range, y_range, z_range)
    ax.set_xlim(0, max_range)
    ax.set_ylim(0, max_range)
    ax.set_zlim(0, max_range)


# ---------------------------------------------------------------------------
# Single case (3D, isometric)
# ---------------------------------------------------------------------------

def draw_single_case_3d(ax, case_length: float, case_width: float, case_height: float,
                        title: str = "Carton"):
    """Render a single carton with the three colour-coded faces.

    Always: L along X, W along Y, H along Z (canonical orientation).
    """
    _draw_box_3d(ax, 0, 0, 0, case_length, case_width, case_height,
                 alpha=0.95, edgecolor="black", linewidth=0.8)

    ax.set_xlabel("L (mm)")
    ax.set_ylabel("W (mm)")
    ax.set_zlabel("H (mm)")
    ax.set_title(title)

    m = max(case_length, case_width, case_height) * 1.2
    ax.set_xlim(0, m); ax.set_ylim(0, m); ax.set_zlim(0, m)
    ax.view_init(elev=20, azim=-60)

    # legend
    from matplotlib.patches import Patch
    legend = [
        Patch(facecolor=FACE_COLORS["front"], label="Front (blue)"),
        Patch(facecolor=FACE_COLORS["side"],  label="Side (green)"),
        Patch(facecolor=FACE_COLORS["top"],   label="Top (orange)"),
    ]
    ax.legend(handles=legend, loc="upper left", fontsize=8)


# ---------------------------------------------------------------------------
# Top view (XY plane)
# ---------------------------------------------------------------------------

def draw_top_view(ax, solution: StackingSolution, layer_index: int = 0,
                  show_dimensions: bool = True):
    """Plan view of one layer."""
    pallet = solution.pallet

    # pallet outline
    ax.add_patch(Rectangle((0, 0), pallet.length, pallet.width,
                           fill=False, linewidth=1.2, edgecolor="black"))

    # usable rectangle (after margins)
    ax.add_patch(Rectangle(
        (pallet.margin_left, pallet.margin_front),
        pallet.usable_length, pallet.usable_width,
        fill=False, linestyle="--", linewidth=0.8, edgecolor="gray"))

    if 0 <= layer_index < len(solution.layers):
        layer = solution.layers[layer_index]
        for p in layer.placements:
            ax.add_patch(Rectangle((p.x, p.y), p.dx, p.dy,
                                   facecolor=FACE_COLORS["top"],
                                   edgecolor="black", linewidth=0.5, alpha=0.85))

    ax.set_xlim(-pallet.length * 0.05, pallet.length * 1.05)
    ax.set_ylim(-pallet.width  * 0.05, pallet.width  * 1.05)
    ax.set_aspect("equal")
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_title(f"Top View — layer {layer_index + 1} "
                 f"({solution.cases_per_layer} cases)")

    if show_dimensions:
        _annotate_dimension(ax, (0, -pallet.width * 0.04),
                            (pallet.length, -pallet.width * 0.04),
                            f"L = {pallet.length:.0f} mm")
        _annotate_dimension(ax, (-pallet.length * 0.04, 0),
                            (-pallet.length * 0.04, pallet.width),
                            f"W = {pallet.width:.0f} mm",
                            vertical=True)


# ---------------------------------------------------------------------------
# Side view (X / Z plane, looking from +Y towards -Y)
# ---------------------------------------------------------------------------

def draw_side_view(ax, solution: StackingSolution, show_dimensions: bool = True):
    pallet = solution.pallet
    # pallet body (drawn as a rectangle at Z 0..pallet.height)
    ax.add_patch(Rectangle((0, 0), pallet.length, pallet.height,
                           facecolor="#a8784a", edgecolor="black", linewidth=0.6))

    for layer in solution.layers:
        for p in layer.placements:
            ax.add_patch(Rectangle(
                (p.x, pallet.height + p.z), p.dx, p.dz,
                facecolor=FACE_COLORS["side"],
                edgecolor="black", linewidth=0.4, alpha=0.6))

    stack_top = pallet.height + solution.layer_count * solution.case_dz \
        if solution.layers else pallet.height

    ax.set_xlim(-pallet.length * 0.05, pallet.length * 1.05)
    ax.set_ylim(-stack_top * 0.05, stack_top * 1.15)
    ax.set_aspect("equal")
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Z (mm)")
    ax.set_title(f"Side View — {solution.layer_count} layers, "
                 f"total height {stack_top:.0f} mm")

    if show_dimensions:
        _annotate_dimension(ax, (0, -stack_top * 0.04),
                            (pallet.length, -stack_top * 0.04),
                            f"L = {pallet.length:.0f} mm")
        _annotate_dimension(ax, (-pallet.length * 0.04, 0),
                            (-pallet.length * 0.04, stack_top),
                            f"H = {stack_top:.0f} mm",
                            vertical=True)


# ---------------------------------------------------------------------------
# Front view (Y / Z plane, looking from +X towards -X)
# ---------------------------------------------------------------------------

def draw_front_view(ax, solution: StackingSolution, show_dimensions: bool = True):
    pallet = solution.pallet
    ax.add_patch(Rectangle((0, 0), pallet.width, pallet.height,
                           facecolor="#a8784a", edgecolor="black", linewidth=0.6))

    for layer in solution.layers:
        for p in layer.placements:
            ax.add_patch(Rectangle(
                (p.y, pallet.height + p.z), p.dy, p.dz,
                facecolor=FACE_COLORS["front"],
                edgecolor="black", linewidth=0.4, alpha=0.6))

    stack_top = pallet.height + solution.layer_count * solution.case_dz \
        if solution.layers else pallet.height

    ax.set_xlim(-pallet.width * 0.05, pallet.width * 1.05)
    ax.set_ylim(-stack_top * 0.05, stack_top * 1.15)
    ax.set_aspect("equal")
    ax.set_xlabel("Y (mm)")
    ax.set_ylabel("Z (mm)")
    ax.set_title(f"Front View — {solution.layer_count} layers")

    if show_dimensions:
        _annotate_dimension(ax, (0, -stack_top * 0.04),
                            (pallet.width, -stack_top * 0.04),
                            f"W = {pallet.width:.0f} mm")
        _annotate_dimension(ax, (-pallet.width * 0.04, 0),
                            (-pallet.width * 0.04, stack_top),
                            f"H = {stack_top:.0f} mm",
                            vertical=True)


# ---------------------------------------------------------------------------
# 3D pallet view
# ---------------------------------------------------------------------------

def draw_pallet_3d(ax, solution: StackingSolution, title: Optional[str] = None):
    pallet = solution.pallet
    # pallet platform
    _draw_pallet_platform_3d(ax, pallet)

    # cases
    for layer in solution.layers:
        for p in layer.placements:
            _draw_box_3d(ax, p.x, p.y, pallet.height + p.z,
                         p.dx, p.dy, p.dz,
                         alpha=0.88, edgecolor="black", linewidth=0.3)

    total_h = pallet.height + solution.layer_count * solution.case_dz \
        if solution.layers else pallet.height
    m = max(pallet.length, pallet.width, total_h) * 1.05
    ax.set_xlim(0, m); ax.set_ylim(0, m); ax.set_zlim(0, m)
    ax.set_xlabel("X (mm) — pallet length")
    ax.set_ylabel("Y (mm) — pallet width")
    ax.set_zlabel("Z (mm) — height")
    ax.view_init(elev=22, azim=-55)
    if title is None:
        title = (f"3D Pallet View — {solution.total_cases} cases "
                 f"({solution.layout_name}{', interlock' if solution.interlock else ''})")
    ax.set_title(title)


def _draw_pallet_platform_3d(ax, pallet: Pallet):
    # Render the pallet as a single coloured box for clarity
    L, W, H = pallet.length, pallet.width, pallet.height
    p = [
        (0, 0, 0), (L, 0, 0), (L, W, 0), (0, W, 0),
        (0, 0, H), (L, 0, H), (L, W, H), (0, W, H),
    ]
    faces = [
        [p[0], p[1], p[2], p[3]],
        [p[4], p[5], p[6], p[7]],
        [p[0], p[1], p[5], p[4]],
        [p[3], p[2], p[6], p[7]],
        [p[1], p[2], p[6], p[5]],
        [p[0], p[3], p[7], p[4]],
    ]
    coll = Poly3DCollection(faces, facecolors="#a8784a",
                            edgecolors="black", linewidths=0.4, alpha=0.85)
    ax.add_collection3d(coll)


# ---------------------------------------------------------------------------
# Dimension annotations
# ---------------------------------------------------------------------------

def _annotate_dimension(ax, p0, p1, label, vertical=False):
    arrow = FancyArrowPatch(p0, p1, arrowstyle="<->", mutation_scale=10,
                            color="black", linewidth=0.8)
    ax.add_patch(arrow)
    mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
    if vertical:
        ax.text(mid[0], mid[1], label, rotation=90,
                ha="right", va="center", fontsize=8,
                bbox=dict(boxstyle="round,pad=0.15",
                          facecolor="white", edgecolor="none"))
    else:
        ax.text(mid[0], mid[1], label, ha="center", va="top", fontsize=8,
                bbox=dict(boxstyle="round,pad=0.15",
                          facecolor="white", edgecolor="none"))


# ---------------------------------------------------------------------------
# Convenience: save a figure at high DPI
# ---------------------------------------------------------------------------

def save_figure(fig, path: str, dpi: int = HIGH_DPI):
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Compose a one-shot multi-panel preview (used by GUI preview window
# and by the PDF report builder)
# ---------------------------------------------------------------------------

def build_overview_figure(solution: StackingSolution,
                          fig=None) -> "matplotlib.figure.Figure":
    """Compose a 2x2 dashboard: single carton / top / side / front + 3D."""
    if fig is None:
        fig = plt.figure(figsize=(11, 8.5))

    gs = fig.add_gridspec(2, 3, width_ratios=[1, 1, 1.2], hspace=0.35, wspace=0.35)

    ax_case = fig.add_subplot(gs[0, 0], projection="3d")
    draw_single_case_3d(ax_case,
                        solution.case.length, solution.case.width,
                        solution.case.height,
                        title=f"Carton ({solution.case.length:.0f}×"
                              f"{solution.case.width:.0f}×"
                              f"{solution.case.height:.0f})")

    ax_top = fig.add_subplot(gs[0, 1])
    draw_top_view(ax_top, solution, layer_index=0)

    ax_3d = fig.add_subplot(gs[:, 2], projection="3d")
    draw_pallet_3d(ax_3d, solution)

    ax_side = fig.add_subplot(gs[1, 0])
    draw_side_view(ax_side, solution)

    ax_front = fig.add_subplot(gs[1, 1])
    draw_front_view(ax_front, solution)

    return fig
