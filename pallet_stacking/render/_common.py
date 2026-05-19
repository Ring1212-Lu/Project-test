"""Shared helpers for engineering-style drawing."""
from __future__ import annotations

from typing import Tuple, List
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyArrowPatch
from mpl_toolkits.mplot3d.art3d import Poly3DCollection, Line3DCollection

from .. import FACE_COLORS, LINE_STYLE


# ---------------------------------------------------------------------------
# Pallet base outline (engineering line conventions)
# ---------------------------------------------------------------------------

def draw_pallet_outline_2d(ax, pallet, *, show_margin: bool = True):
    """Draw pallet outline (thick solid) + usable area (dashed) on a 2D axis."""
    # Reserved margin band (light fill)
    if show_margin and (pallet.margin_left or pallet.margin_right
                        or pallet.margin_front or pallet.margin_back):
        ax.add_patch(Rectangle(
            (0, 0), pallet.length, pallet.width,
            **LINE_STYLE["reserved_margin_fill"]))

    # Pallet outline (thick)
    ax.add_patch(Rectangle(
        (0, 0), pallet.length, pallet.width, fill=False,
        **{k: v for k, v in LINE_STYLE["pallet_outline"].items()
           if k in ("linewidth", "linestyle")}, edgecolor="black"))

    # Usable area (dashed)
    ax.add_patch(Rectangle(
        (pallet.margin_left, pallet.margin_front),
        pallet.usable_length, pallet.usable_width, fill=False,
        linewidth=LINE_STYLE["usable_area_outline"]["linewidth"],
        linestyle=LINE_STYLE["usable_area_outline"]["linestyle"],
        edgecolor=LINE_STYLE["usable_area_outline"]["color"]))


def add_dim_arrow(ax, p0, p1, label, vertical: bool = False, offset: float = 0):
    """Draw an arrow-style dimension annotation between two points."""
    arrow = FancyArrowPatch(p0, p1, arrowstyle="<->", mutation_scale=9,
                            color=LINE_STYLE["dimension"]["color"],
                            linewidth=LINE_STYLE["dimension"]["linewidth"])
    ax.add_patch(arrow)
    mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
    rot = 90 if vertical else 0
    ax.text(mid[0], mid[1], label, rotation=rot,
            ha="center", va="center", fontsize=8,
            bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                      edgecolor="none"))


# ---------------------------------------------------------------------------
# 3D drawing helpers
# ---------------------------------------------------------------------------

ISO_ELEV = 30.0
ISO_AZIM = -45.0


def setup_iso_axes(ax):
    """Apply orthographic isometric projection + clean look to a 3D axis."""
    try:
        ax.set_proj_type("ortho")    # disable perspective
    except Exception:
        pass
    ax.view_init(elev=ISO_ELEV, azim=ISO_AZIM)

    # remove the grey background panes for a cleaner CAD look
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.fill = False
        axis.pane.set_edgecolor("#dddddd")
        axis.pane.set_linewidth(0.4)
    ax.grid(True, linewidth=0.3, linestyle=":", color="#cccccc")


def box_faces(x, y, z, dx, dy, dz,
              face_x_label: str = "L",
              face_y_label: str = "W",
              face_z_label: str = "H"):
    """Return (faces, colours) for one carton box.

    Colour is fixed by which **case-local axis** is normal to each face,
    so that the carton's own front / side / top face keeps its colour
    regardless of how the carton is rotated on the pallet:

        case axis "L" -> "side"  face (green)
        case axis "W" -> "front" face (blue)
        case axis "H" -> "top"   face (orange)

    The ``face_*_label`` arguments tell which case-local axis is normal
    to each world axis after placement.
    """
    AXIS_TO_ROLE = {"L": "side", "W": "front", "H": "top"}
    color_x = FACE_COLORS[AXIS_TO_ROLE[face_x_label]]
    color_y = FACE_COLORS[AXIS_TO_ROLE[face_y_label]]
    color_z = FACE_COLORS[AXIS_TO_ROLE[face_z_label]]

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
    colours = [color_z, color_z, color_y, color_y, color_x, color_x]
    return faces, colours


def draw_box_3d(ax, x, y, z, dx, dy, dz,
                face_x_label: str = "L",
                face_y_label: str = "W",
                face_z_label: str = "H"):
    """Render one carton box in engineering style (no shading, thin edges)."""
    faces, colours = box_faces(x, y, z, dx, dy, dz,
                               face_x_label, face_y_label, face_z_label)
    coll = Poly3DCollection(
        faces, facecolors=colours,
        edgecolors="black",
        linewidths=LINE_STYLE["carton_edge"]["linewidth"])
    coll.set_alpha(1.0)
    coll.set_zsort("max")
    ax.add_collection3d(coll)


def draw_wire_rect_3d(ax, p0, p1, *, linewidth=1.0, linestyle="-", color="black"):
    """Draw a rectangular wire-frame on the Z=z0 plane between two points."""
    x0, y0, z0 = p0
    x1, y1, z1 = p1
    pts = [
        [(x0, y0, z0), (x1, y0, z0)],
        [(x1, y0, z0), (x1, y1, z0)],
        [(x1, y1, z0), (x0, y1, z0)],
        [(x0, y1, z0), (x0, y0, z0)],
    ]
    coll = Line3DCollection(pts, colors=color, linewidths=linewidth,
                            linestyles=linestyle)
    ax.add_collection3d(coll)


def draw_pallet_platform_3d(ax, pallet):
    """A simple coloured block for the pallet platform."""
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
    coll = Poly3DCollection(faces, facecolors="#b08454",
                            edgecolors="black", linewidths=0.5)
    coll.set_alpha(1.0)
    ax.add_collection3d(coll)


def set_axes_iso(ax, x_max, y_max, z_max, pad_factor: float = 0.05):
    """Use the actual dimensions for the axis ranges and ``box_aspect`` so
    the carton stack visually aligns with the pallet base (no stretched cube).
    """
    pad_x = x_max * pad_factor
    pad_y = y_max * pad_factor
    pad_z = z_max * pad_factor
    ax.set_xlim(-pad_x, x_max + pad_x)
    ax.set_ylim(-pad_y, y_max + pad_y)
    ax.set_zlim(0,       z_max + pad_z)
    ax.set_box_aspect((x_max, y_max, z_max))


def style_axis_2d(ax, *, title: str = "", xlabel: str = "X (mm)",
                  ylabel: str = "Y (mm)"):
    ax.set_aspect("equal")
    ax.set_xlabel(xlabel); ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, fontsize=10)
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
    ax.tick_params(labelsize=8, width=0.5)
