"""Filler / dunnage spread post-processing.

When the heuristic packs N cartons into a footprint smaller than the
pallet's usable area, the load is unstable: the cartons sit in a tight
cluster with empty pallet around them.  Industrial practice is to spread
the cartons out so the outer edges touch the pallet, and fill the gaps
between cartons with foam blocks, corrugated dunnage, or void-fill.

``spread_layer`` takes a ``LayerPattern`` and the pallet usable area,
returns a new ``LayerPattern`` whose placements have been scaled in X
and Y so the bounding box now equals the usable area.  Carton sizes are
**unchanged** — only positions move.

``filler_rectangles`` decomposes the empty area inside the usable region
(after the spread) into axis-aligned rectangles you can hand to a
dunnage supplier.  The decomposition is by horizontal slabs between
consecutive carton-edge y-coordinates; within each slab the empty
sub-intervals along X are emitted as rectangles.
"""
from __future__ import annotations

from typing import List, Tuple

from ..models import LayerPattern, PlacedCarton


# ---------------------------------------------------------------------------
# Spread (rescale placements to fill usable area)
# ---------------------------------------------------------------------------

def spread_layer(layer: LayerPattern,
                 usable_x: float, usable_y: float,
                 ox: float = 0.0, oy: float = 0.0) -> LayerPattern:
    """Return a new layer with placements scaled so the layer's bounding
    box equals ``(usable_x, usable_y)``.

    Each carton's left edge is repositioned by its **relative slot** in
    the original bounding box, scaled into the new available slot
    ``[0, usable_x - dx]``.  This preserves the row/column structure
    (cartons that shared a left edge still share one), keeps carton
    sizes constant, and never causes overlap because the scale factor
    is >= 1 in each axis.

    If the layer is already at full width / height in an axis, that
    axis is left untouched.
    """
    if not layer.placements:
        return layer

    bb_x0 = min(p.x for p in layer.placements)
    bb_x1 = max(p.x + p.dx for p in layer.placements)
    bb_y0 = min(p.y for p in layer.placements)
    bb_y1 = max(p.y + p.dy for p in layer.placements)
    bb_w  = bb_x1 - bb_x0
    bb_h  = bb_y1 - bb_y0

    new_placements: List[PlacedCarton] = []
    for p in layer.placements:
        # X
        denom_x = bb_w - p.dx
        if denom_x > 1e-6 and usable_x > p.dx:
            rel = (p.x - bb_x0) / denom_x
            new_x = ox + rel * (usable_x - p.dx)
        else:
            # Single column or carton wider than usable — centre it.
            new_x = ox + max(0.0, (usable_x - p.dx) / 2.0)
        # Y
        denom_y = bb_h - p.dy
        if denom_y > 1e-6 and usable_y > p.dy:
            rel = (p.y - bb_y0) / denom_y
            new_y = oy + rel * (usable_y - p.dy)
        else:
            new_y = oy + max(0.0, (usable_y - p.dy) / 2.0)

        new_placements.append(PlacedCarton(
            x=new_x, y=new_y, z=p.z,
            dx=p.dx, dy=p.dy, dz=p.dz,
            rotation=p.rotation,
            face_x=p.face_x, face_y=p.face_y, face_z=p.face_z,
            barcode_visible=p.barcode_visible,
        ))

    return LayerPattern(
        placements=new_placements,
        pattern_name=layer.pattern_name + "+spread",
        case_dx=layer.case_dx, case_dy=layer.case_dy, case_dz=layer.case_dz,
    )


# ---------------------------------------------------------------------------
# Filler rectangle decomposition
# ---------------------------------------------------------------------------

def _max_empty_rectangle(obstacles: List[Tuple[float, float, float, float]],
                         x_lo: float, x_hi: float,
                         y_lo: float, y_hi: float
                         ) -> Tuple[float, float, float, float] | None:
    """Largest axis-aligned rectangle inside ``[x_lo, x_hi] x [y_lo, y_hi]``
    that does not overlap any obstacle in ``obstacles``.

    Brute force over all candidate corner coordinates (carton edges +
    bounds), pruning candidates whose bbox area cannot beat the current
    best.  O((nx*ny)^2 * N) in the worst case; fine for the carton
    counts we deal with (typically <= 30 obstacles, ~60 unique
    coordinates per axis).
    """
    xs = {x_lo, x_hi}
    ys = {y_lo, y_hi}
    for ox_, oy_, ow_, oh_ in obstacles:
        xs.add(max(x_lo, ox_));         xs.add(min(x_hi, ox_ + ow_))
        ys.add(max(y_lo, oy_));         ys.add(min(y_hi, oy_ + oh_))
    xs = sorted(x for x in xs if x_lo - 1e-9 <= x <= x_hi + 1e-9)
    ys = sorted(y for y in ys if y_lo - 1e-9 <= y <= y_hi + 1e-9)

    best = None
    best_area = 0.0
    nx = len(xs); ny = len(ys)
    for i in range(nx - 1):
        for j in range(i + 1, nx):
            w = xs[j] - xs[i]
            if w * (y_hi - y_lo) <= best_area:
                continue        # this column can't beat best even at full height
            for k in range(ny - 1):
                for ll in range(k + 1, ny):
                    h = ys[ll] - ys[k]
                    area = w * h
                    if area <= best_area:
                        continue
                    # Empty check
                    x0_, x1_ = xs[i], xs[j]
                    y0_, y1_ = ys[k], ys[ll]
                    ok = True
                    for ox_, oy_, ow_, oh_ in obstacles:
                        if (ox_ < x1_ - 1e-9 and ox_ + ow_ > x0_ + 1e-9
                                and oy_ < y1_ - 1e-9 and oy_ + oh_ > y0_ + 1e-9):
                            ok = False
                            break
                    if ok:
                        best_area = area
                        best = (x0_, y0_, w, h)
    return best


def filler_rectangles(layer: LayerPattern,
                      usable_x: float, usable_y: float,
                      ox: float = 0.0, oy: float = 0.0,
                      min_dim: float = 1.0,
                      ) -> List[Tuple[float, float, float, float]]:
    """Decompose the void area into the **fewest, biggest** axis-aligned
    rectangles ``(x, y, w, h)``.

    Greedy "maximum empty rectangle" extraction: repeatedly find the
    largest empty rectangle, append it to the result, treat it as an
    obstacle, and repeat.  This minimises the number of dunnage pieces
    needed by maximising the size of each.

    Rectangles smaller than ``min_dim`` mm in either dimension are
    discarded (they're rounding noise, not orderable dunnage).
    """
    obstacles: List[Tuple[float, float, float, float]] = [
        (p.x, p.y, p.dx, p.dy) for p in layer.placements
    ]
    x_lo, x_hi = ox, ox + usable_x
    y_lo, y_hi = oy, oy + usable_y

    rects: List[Tuple[float, float, float, float]] = []
    # Safety cap so a malformed input can't infinite-loop.
    for _ in range(256):
        r = _max_empty_rectangle(obstacles, x_lo, x_hi, y_lo, y_hi)
        if r is None:
            break
        if min(r[2], r[3]) < min_dim:
            break
        rects.append(r)
        obstacles.append(r)

    return rects
