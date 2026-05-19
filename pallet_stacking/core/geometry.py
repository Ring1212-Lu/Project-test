"""Geometric placement helpers: overlap, containment, barcode visibility."""
from __future__ import annotations
from typing import List

from ..models import PlacedCarton, Pallet, LayerPattern, StackingResult


EPS = 0.5  # mm tolerance


def overlaps(a: PlacedCarton, b: PlacedCarton, eps: float = EPS) -> bool:
    """True iff two placed cartons intersect in 3D."""
    if a.z + a.dz <= b.z + eps or b.z + b.dz <= a.z + eps:
        return False
    if a.x + a.dx <= b.x + eps or b.x + b.dx <= a.x + eps:
        return False
    if a.y + a.dy <= b.y + eps or b.y + b.dy <= a.y + eps:
        return False
    return True


def within_usable(p: PlacedCarton, pallet: Pallet, eps: float = EPS) -> bool:
    """True iff the carton sits inside the usable pallet area + below max height."""
    ox = pallet.margin_left
    oy = pallet.margin_front
    if p.x < ox - eps:
        return False
    if p.y < oy - eps:
        return False
    if p.x + p.dx > ox + pallet.usable_length + eps:
        return False
    if p.y + p.dy > oy + pallet.usable_width + eps:
        return False
    if p.z + p.dz > pallet.usable_height + eps:
        return False
    return True


def is_barcode_visible(p: PlacedCarton, pallet: Pallet,
                       barcode_face_axis: str = "L",
                       eps: float = 1.0) -> bool:
    """Whether the carton's barcode face touches the usable-area boundary.

    The barcode face is normal to the case-local axis ``barcode_face_axis``.
    After placement we know which pallet axis (X, Y or Z) that case-axis
    runs along (``p.face_x`` / ``p.face_y`` / ``p.face_z``).

    Visible iff the barcode face is parallel to a vertical pallet edge AND
    the carton sits flush against that edge.
    """
    ox = pallet.margin_left
    oy = pallet.margin_front
    edge_left   = ox
    edge_right  = ox + pallet.usable_length
    edge_front  = oy
    edge_back   = oy + pallet.usable_width

    if p.face_x == barcode_face_axis:
        return (abs(p.x - edge_left)            < eps or
                abs((p.x + p.dx) - edge_right)  < eps)
    if p.face_y == barcode_face_axis:
        return (abs(p.y - edge_front)           < eps or
                abs((p.y + p.dy) - edge_back)   < eps)
    # face_z == barcode_face_axis : barcode points up or down, not visible
    return False


def validate_layer(layer: LayerPattern, pallet: Pallet) -> List[str]:
    """Return a list of validation error strings (empty when layer is legal)."""
    errs: List[str] = []
    for i, p in enumerate(layer.placements):
        if not within_usable(p, pallet):
            errs.append(f"carton {i} outside usable area")
        for j in range(i + 1, len(layer.placements)):
            if overlaps(p, layer.placements[j]):
                errs.append(f"cartons {i} and {j} overlap")
    return errs


def validate_solution(result: StackingResult) -> List[str]:
    """Validate every layer in the solution."""
    errs: List[str] = []
    for k, layer in enumerate(result.layers):
        for e in validate_layer(layer, result.pallet):
            errs.append(f"[layer {k+1}] {e}")
    return errs
