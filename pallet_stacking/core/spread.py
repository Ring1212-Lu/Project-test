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

def filler_rectangles(layer: LayerPattern,
                      usable_x: float, usable_y: float,
                      ox: float = 0.0, oy: float = 0.0,
                      min_dim: float = 1.0,
                      ) -> List[Tuple[float, float, float, float]]:
    """Decompose the area inside ``[ox, ox+usable_x] x [oy, oy+usable_y]``
    that is *not* covered by any carton in ``layer`` into axis-aligned
    rectangles ``(x, y, w, h)``.

    Algorithm: build a horizontal-strip decomposition.  The y-axis is
    split by every carton's top/bottom edges (plus the pallet edges).
    Within each horizontal strip, the X-axis is the disjoint union of
    [empty intervals] and [carton intervals]; emit one rectangle per
    empty interval.

    Rectangles smaller than ``min_dim`` mm in either dimension are
    discarded (they're rounding noise, not orderable dunnage).
    """
    y_edges = {oy, oy + usable_y}
    for p in layer.placements:
        y_edges.add(max(oy, p.y))
        y_edges.add(min(oy + usable_y, p.y + p.dy))
    y_sorted = sorted(e for e in y_edges if oy - 1e-6 <= e <= oy + usable_y + 1e-6)

    rects: List[Tuple[float, float, float, float]] = []
    x0 = ox
    x1 = ox + usable_x
    for y_lo, y_hi in zip(y_sorted, y_sorted[1:]):
        h = y_hi - y_lo
        if h < min_dim:
            continue
        # Collect X intervals occupied by cartons spanning this strip.
        spans: List[Tuple[float, float]] = []
        for p in layer.placements:
            if p.y <= y_lo + 1e-6 and p.y + p.dy >= y_hi - 1e-6:
                spans.append((max(x0, p.x), min(x1, p.x + p.dx)))
        spans.sort()
        # Walk and emit gaps.
        cursor = x0
        for s_lo, s_hi in spans:
            if s_lo - cursor >= min_dim:
                rects.append((cursor, y_lo, s_lo - cursor, h))
            cursor = max(cursor, s_hi)
        if x1 - cursor >= min_dim:
            rects.append((cursor, y_lo, x1 - cursor, h))

    return rects
