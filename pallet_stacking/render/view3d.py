"""Orthographic / isometric 3D views (engineering drawing style)."""
from __future__ import annotations

from typing import Optional
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from .. import FACE_COLORS, LINE_STYLE
from ..models import StackingResult
from ._common import (
    setup_iso_axes, draw_box_3d, draw_wire_rect_3d,
    box_faces, set_axes_iso,
)


def _pallet_visible_faces(pallet):
    """Return (faces, colors) for the two pallet faces visible from the
    isometric camera (front -Y and right +X).  The hidden faces are
    omitted so matplotlib's painter's algorithm has fewer polygons to
    mis-sort.
    """
    L, W, H = pallet.length, pallet.width, pallet.height
    p = [
        (0, 0, 0), (L, 0, 0), (L, W, 0), (0, W, 0),
        (0, 0, H), (L, 0, H), (L, W, H), (0, W, H),
    ]
    faces = [
        [p[0], p[1], p[5], p[4]],   # front (Y-)
        [p[1], p[2], p[6], p[5]],   # right (X+)
    ]
    colours = ["#b08454", "#8b6440"]   # slightly darker on the side face
    return faces, colours


def _pallet_top_uncovered(pallet, base_layer, resolution: float = 6.0):
    """Return brown polygons covering only the parts of the pallet's top
    surface that are *not* hidden by a carton.  Used to fill the visible
    gap areas (e.g. the 28 mm strip at the back of a frame-Y layout)
    without re-introducing the full pallet top face — which matplotlib
    would mis-sort against the cartons.

    A simple grid rasterisation is used: cells of size ``resolution`` are
    marked covered if any carton overlaps them.  Each connected horizontal
    run of uncovered cells is emitted as a single rectangle (greedy).
    """
    L, W, H = pallet.length, pallet.width, pallet.height
    res = resolution
    nx = max(1, int(L // res))
    ny = max(1, int(W // res))
    covered = [[False] * ny for _ in range(nx)]

    for p in base_layer.placements:
        i0 = max(0, int(p.x // res))
        i1 = min(nx, int(round((p.x + p.dx) / res)))
        j0 = max(0, int(p.y // res))
        j1 = min(ny, int(round((p.y + p.dy) / res)))
        for i in range(i0, i1):
            for j in range(j0, j1):
                covered[i][j] = True

    # Emit horizontal runs of uncovered cells per row -> rectangle polygons
    faces = []
    for j in range(ny):
        i = 0
        while i < nx:
            if covered[i][j]:
                i += 1
                continue
            i_start = i
            while i < nx and not covered[i][j]:
                i += 1
            i_end = i
            x0 = i_start * res
            x1 = i_end   * res
            y0 = j       * res
            y1 = (j + 1) * res
            faces.append([
                (x0, y0, H), (x1, y0, H),
                (x1, y1, H), (x0, y1, H),
            ])
    return faces


def draw_pallet_3d(ax, result: StackingResult, title: Optional[str] = None,
                   show_boundary: bool = True):
    pallet = result.pallet
    setup_iso_axes(ax)

    # ---- assemble every polygon (pallet sides + uncovered top + cartons)
    # into a *single* Poly3DCollection so matplotlib's depth sort can
    # handle the whole scene with a single internal pass.
    all_faces = []
    all_colours = []

    # 1) Pallet's two visible side faces.
    p_faces, p_cols = _pallet_visible_faces(pallet)
    all_faces.extend(p_faces); all_colours.extend(p_cols)

    # 2) Brown patches filling the parts of the pallet top that are NOT
    #    hidden by cartons.  These never overlap with a carton's
    #    footprint so painter's sort cannot mis-order them.
    if result.layers:
        for poly in _pallet_top_uncovered(pallet, result.layers[0]):
            all_faces.append(poly)
            all_colours.append("#b08454")

    # 3) All carton faces.
    for layer in result.layers:
        for pc in layer.placements:
            cf, cc = box_faces(pc.x, pc.y, pallet.height + pc.z,
                               pc.dx, pc.dy, pc.dz,
                               face_x_label=pc.face_x,
                               face_y_label=pc.face_y,
                               face_z_label=pc.face_z)
            all_faces.extend(cf)
            all_colours.extend(cc)

    coll = Poly3DCollection(
        all_faces, facecolors=all_colours,
        edgecolors="black",
        linewidths=LINE_STYLE["carton_edge"]["linewidth"])
    coll.set_alpha(1.0)
    coll.set_zsort("max")
    ax.add_collection3d(coll)

    # Reserved-margin / usable-area wires (optional, drawn after)
    if show_boundary and (pallet.margin_left or pallet.margin_right
                          or pallet.margin_front or pallet.margin_back):
        draw_wire_rect_3d(
            ax,
            (pallet.margin_left, pallet.margin_front, pallet.height + 0.5),
            (pallet.margin_left + pallet.usable_length,
             pallet.margin_front + pallet.usable_width,
             pallet.height + 0.5),
            linewidth=LINE_STYLE["usable_area_outline"]["linewidth"],
            linestyle=LINE_STYLE["usable_area_outline"]["linestyle"],
            color=LINE_STYLE["usable_area_outline"]["color"])

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
    """Compose a three-panel engineering dashboard (used in GUI preview).

    Panels: Carton orientation (3D) | Top view (one layer) | 3D pallet view
    """
    if fig is None:
        fig = plt.figure(figsize=(13, 4.5))
    gs = fig.add_gridspec(1, 3, hspace=0.3, wspace=0.35)

    from .top_view  import draw_top_view

    # carton orientation
    ax_case = fig.add_subplot(gs[0, 0], projection="3d")
    draw_single_carton_3d(ax_case,
                          result.carton.length,
                          result.carton.width,
                          result.carton.height,
                          title=(f"Carton {result.carton.length:.0f}×"
                                 f"{result.carton.width:.0f}×"
                                 f"{result.carton.height:.0f}"))

    # top view
    ax_top = fig.add_subplot(gs[0, 1])
    draw_top_view(ax_top, result, layer_index=0)

    # 3D pallet view
    ax_3d = fig.add_subplot(gs[0, 2], projection="3d")
    draw_pallet_3d(ax_3d, result)

    return fig
