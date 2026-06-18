"""Top View (X-Y plan) — engineering layout style."""
from __future__ import annotations

from matplotlib.patches import Rectangle, FancyArrowPatch
from matplotlib.lines import Line2D

from .. import FACE_COLORS, LINE_STYLE
from ..models import StackingResult
from ._common import draw_pallet_outline_2d, add_dim_arrow, style_axis_2d


def draw_top_view(ax, result: StackingResult, layer_index: int = 0,
                  show_dimensions: bool = True,
                  fillers=None):
    """Plan view of one layer.

    Cartons are drawn as orange rectangles (top face) outlined in black.
    Cartons with visible barcode (touching the usable boundary) get a
    thin green tick on the relevant edge.

    If ``fillers`` is provided (a list of ``(x, y, w, h)`` tuples), each
    rectangle is drawn behind the cartons in a hatched light-blue style
    to mark dunnage / void-fill positions.
    """
    pallet = result.pallet
    draw_pallet_outline_2d(ax, pallet, show_margin=True)

    if fillers:
        for fx, fy, fw, fh in fillers:
            ax.add_patch(Rectangle(
                (fx, fy), fw, fh,
                facecolor="#CFE2F3", edgecolor="#5B8CB5",
                linewidth=0.8, hatch="///", alpha=0.65, zorder=0.5))

    AXIS_TO_ROLE = {"L": "side", "W": "front", "H": "top"}
    if 0 <= layer_index < len(result.layers):
        layer = result.layers[layer_index]
        for p in layer.placements:
            # Top view shows the upward-facing case-local face.  Colour by
            # which case axis is vertical, so the same case face keeps the
            # same colour across orientations.
            face_color = FACE_COLORS[AXIS_TO_ROLE.get(p.face_z, "top")]
            ax.add_patch(Rectangle(
                (p.x, p.y), p.dx, p.dy,
                facecolor=face_color,
                edgecolor=LINE_STYLE["carton_edge"]["color"],
                linewidth=LINE_STYLE["carton_edge"]["linewidth"]))
            if p.barcode_visible:
                # green tick along the outward side
                ox = pallet.margin_left
                oy = pallet.margin_front
                el = ox; er = ox + pallet.usable_length
                ef = oy; eb = oy + pallet.usable_width
                if abs(p.x - el) < 1.0:
                    ax.plot([p.x, p.x], [p.y, p.y + p.dy],
                            color=FACE_COLORS["side"], linewidth=2.2)
                if abs((p.x + p.dx) - er) < 1.0:
                    ax.plot([p.x + p.dx, p.x + p.dx],
                            [p.y, p.y + p.dy],
                            color=FACE_COLORS["side"], linewidth=2.2)
                if abs(p.y - ef) < 1.0:
                    ax.plot([p.x, p.x + p.dx], [p.y, p.y],
                            color=FACE_COLORS["side"], linewidth=2.2)
                if abs((p.y + p.dy) - eb) < 1.0:
                    ax.plot([p.x, p.x + p.dx], [p.y + p.dy, p.y + p.dy],
                            color=FACE_COLORS["side"], linewidth=2.2)

    ax.set_xlim(-pallet.length * 0.22, pallet.length * 1.06)
    ax.set_ylim(-pallet.width  * 0.22, pallet.width  * 1.06)
    style_axis_2d(ax,
                  title=f"Top View — Layer 1  ({result.cases_per_layer} cartons)")
    ax.xaxis.labelpad = 8
    ax.yaxis.labelpad = 8

    if show_dimensions:
        y_off = -pallet.width  * 0.15
        x_off = -pallet.length * 0.15
        add_dim_arrow(ax, (0, y_off), (pallet.length, y_off),
                      f"L = {pallet.length:.0f} mm")
        add_dim_arrow(ax, (x_off, 0), (x_off, pallet.width),
                      f"W = {pallet.width:.0f} mm", vertical=True)


def draw_carton_orientation_view(ax, result: StackingResult):
    """A small 'one carton with arrows' diagram showing axis orientation
    on the pallet (which case axis is L, W, H after orientation)."""
    pallet = result.pallet
    dx = result.case_dx
    dy = result.case_dy

    pad = max(dx, dy) * 0.4
    ax.add_patch(Rectangle((pad, pad), dx, dy,
                           facecolor=FACE_COLORS["top"],
                           edgecolor="black",
                           linewidth=LINE_STYLE["carton_edge"]["linewidth"]))

    # axis arrows
    arrow_kw = dict(arrowstyle="->", mutation_scale=12, color="#222",
                    linewidth=1.0)
    ax.add_patch(FancyArrowPatch((pad, pad), (pad + dx, pad), **arrow_kw))
    ax.add_patch(FancyArrowPatch((pad, pad), (pad, pad + dy), **arrow_kw))

    ax.text(pad + dx / 2, pad - max(dx, dy) * 0.08,
            f"{result.case_dx:.0f} mm", ha="center", va="top", fontsize=9)
    ax.text(pad - max(dx, dy) * 0.08, pad + dy / 2,
            f"{result.case_dy:.0f} mm", rotation=90, ha="right", va="center",
            fontsize=9)

    # H label (vertical axis dimension printed in corner)
    ax.text(pad + dx + max(dx, dy) * 0.05, pad,
            f"H = {result.case_dz:.0f} mm", ha="left", va="bottom",
            fontsize=9)

    ax.set_xlim(0, pad * 2 + dx)
    ax.set_ylim(0, pad * 2 + dy)
    style_axis_2d(ax, title=f"Carton Orientation ({result.layout_name})",
                  xlabel="", ylabel="")
    ax.set_xticks([])
    ax.set_yticks([])
