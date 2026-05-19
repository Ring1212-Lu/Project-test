"""Multi-objective pallet stacking optimizer.

The optimizer searches over carton orientations and per-layer layouts
(normal / rotated / mixed / interlock) to maximize the total case count
while taking barcode side-face exposure into account.

All dimensions are in millimetres (mm).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Tuple, Optional
import itertools
import math


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Case:
    """A rectangular carton."""
    length: float   # L (mm) - typically the dimension carrying the barcode side
    width:  float   # W (mm)
    height: float   # H (mm)
    weight: float = 0.0   # kg per case
    name:   str = "Case"


@dataclass
class Pallet:
    """Pallet with reserved margins."""
    length: float           # mm  (X)
    width:  float           # mm  (Y)
    height: float = 150.0   # pallet platform height (mm)
    max_total_height: float = 1800.0   # max allowed stack height incl. pallet
    margin_front: float = 0.0
    margin_back:  float = 0.0
    margin_left:  float = 0.0
    margin_right: float = 0.0
    name: str = "Standard Pallet"

    @property
    def usable_length(self) -> float:
        return max(0.0, self.length - self.margin_left - self.margin_right)

    @property
    def usable_width(self) -> float:
        return max(0.0, self.width - self.margin_front - self.margin_back)

    @property
    def usable_height(self) -> float:
        return max(0.0, self.max_total_height - self.height)


@dataclass
class PlacedCase:
    """A single carton placed on the pallet."""
    x: float          # bottom-left corner X (mm) - relative to pallet origin
    y: float          # bottom-left corner Y (mm)
    z: float          # bottom Z (mm) - relative to pallet top (excluding pallet height)
    dx: float         # footprint along X
    dy: float         # footprint along Y
    dz: float         # height along Z
    # orientation indices map case axes (L,W,H) onto pallet (X,Y,Z)
    # rotation: 0 = L along X, 1 = L along Y (90° rotated about Z)
    rotation: int = 0
    # which case face points along +X / +Y / +Z (after orientation):
    # values are one of "L", "W", "H" -> used to assign colors during rendering
    face_x: str = "L"
    face_y: str = "W"
    face_z: str = "H"


@dataclass
class LayerPattern:
    """A single layer arrangement on top of the pallet."""
    placements: List[PlacedCase]
    pattern_name: str  # "normal", "rotated", "mixed", "interlock-A", "interlock-B"
    case_dx: float
    case_dy: float
    case_dz: float

    @property
    def count(self) -> int:
        return len(self.placements)


@dataclass
class StackingSolution:
    """Full pallet stacking solution."""
    case: Case
    pallet: Pallet
    # orientation: which case dim is along Z ("L","W","H")
    vertical_axis: str
    case_dx: float   # footprint dx on pallet (mm) for the dominant orientation
    case_dy: float   # footprint dy on pallet (mm) for the dominant orientation
    case_dz: float   # case height along Z (mm)

    layers: List[LayerPattern] = field(default_factory=list)
    layout_name: str = "normal"            # normal / rotated / mixed / interlock
    interlock: bool = False

    cases_per_layer: int = 0
    layer_count: int = 0
    total_cases: int = 0

    area_utilization: float = 0.0          # 0..1
    volume_utilization: float = 0.0        # 0..1
    barcode_exposure: float = 0.0          # 0..1

    score: float = 0.0

    def to_summary(self) -> dict:
        return {
            "layout": self.layout_name,
            "vertical_axis": self.vertical_axis,
            "case_dx": self.case_dx,
            "case_dy": self.case_dy,
            "case_dz": self.case_dz,
            "cases_per_layer": self.cases_per_layer,
            "layer_count": self.layer_count,
            "total_cases": self.total_cases,
            "area_utilization": self.area_utilization,
            "volume_utilization": self.volume_utilization,
            "barcode_exposure": self.barcode_exposure,
            "interlock": self.interlock,
            "score": self.score,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# All 6 possible orientations of a 3D case (which axis is up, and which
# of the remaining 2 is along X).  Each tuple = (dx, dy, dz, fx, fy, fz)
# where fx/fy/fz says which face of the case points along +X/+Y/+Z.
def _all_orientations(case: Case):
    L, W, H = case.length, case.width, case.height
    # (dx, dy, dz, face_x, face_y, face_z, vertical_axis_label)
    return [
        (L, W, H, "L", "W", "H", "H"),
        (W, L, H, "W", "L", "H", "H"),
        (L, H, W, "L", "H", "W", "W"),
        (H, L, W, "H", "L", "W", "W"),
        (W, H, L, "W", "H", "L", "L"),
        (H, W, L, "H", "W", "L", "L"),
    ]


def _grid_count(usable_x: float, usable_y: float, dx: float, dy: float) -> Tuple[int, int]:
    if dx <= 0 or dy <= 0:
        return 0, 0
    nx = int(usable_x // dx)
    ny = int(usable_y // dy)
    return max(nx, 0), max(ny, 0)


# ---------------------------------------------------------------------------
# Layer pattern generators
# ---------------------------------------------------------------------------

def _normal_layer(dx: float, dy: float, dz: float,
                  usable_x: float, usable_y: float,
                  origin_x: float, origin_y: float) -> LayerPattern:
    """Simple uniform grid, all cases oriented identically."""
    nx, ny = _grid_count(usable_x, usable_y, dx, dy)
    placements = []
    for i in range(nx):
        for j in range(ny):
            placements.append(PlacedCase(
                x=origin_x + i * dx,
                y=origin_y + j * dy,
                z=0.0,
                dx=dx, dy=dy, dz=dz,
                rotation=0,
            ))
    return LayerPattern(placements=placements, pattern_name="normal",
                        case_dx=dx, case_dy=dy, case_dz=dz)


def _rotated_layer(dx: float, dy: float, dz: float,
                   usable_x: float, usable_y: float,
                   origin_x: float, origin_y: float) -> LayerPattern:
    """Uniform grid but each case rotated 90° about Z."""
    nx, ny = _grid_count(usable_x, usable_y, dy, dx)
    placements = []
    for i in range(nx):
        for j in range(ny):
            placements.append(PlacedCase(
                x=origin_x + i * dy,
                y=origin_y + j * dx,
                z=0.0,
                dx=dy, dy=dx, dz=dz,
                rotation=1,
            ))
    return LayerPattern(placements=placements, pattern_name="rotated",
                        case_dx=dy, case_dy=dx, case_dz=dz)


def _mixed_layer(dx: float, dy: float, dz: float,
                 usable_x: float, usable_y: float,
                 origin_x: float, origin_y: float) -> LayerPattern:
    """Mixed pattern: a block of normal-oriented cases + a strip of rotated
    cases to fill leftover space. We try splitting along X then along Y and
    keep whichever yields more cases.
    """
    best: Optional[LayerPattern] = None

    # Split along X: rows of normal cases until remaining X is too small for dx,
    # then fill remaining strip with rotated cases (footprint dy x dx).
    for split_axis in ("X", "Y"):
        placements: List[PlacedCase] = []
        if split_axis == "X":
            n_normal_x = int(usable_x // dx)
            ny_normal = int(usable_y // dy)
            used_x = n_normal_x * dx
            remain_x = usable_x - used_x
            for i in range(n_normal_x):
                for j in range(ny_normal):
                    placements.append(PlacedCase(
                        x=origin_x + i * dx, y=origin_y + j * dy, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                    ))
            if remain_x >= dy:
                nx_r = int(remain_x // dy)
                ny_r = int(usable_y // dx)
                for i in range(nx_r):
                    for j in range(ny_r):
                        placements.append(PlacedCase(
                            x=origin_x + used_x + i * dy,
                            y=origin_y + j * dx,
                            z=0.0,
                            dx=dy, dy=dx, dz=dz, rotation=1,
                        ))
        else:  # split along Y
            n_normal_y = int(usable_y // dy)
            nx_normal = int(usable_x // dx)
            used_y = n_normal_y * dy
            remain_y = usable_y - used_y
            for i in range(nx_normal):
                for j in range(n_normal_y):
                    placements.append(PlacedCase(
                        x=origin_x + i * dx, y=origin_y + j * dy, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                    ))
            if remain_y >= dx:
                ny_r = int(remain_y // dx)
                nx_r = int(usable_x // dy)
                for i in range(nx_r):
                    for j in range(ny_r):
                        placements.append(PlacedCase(
                            x=origin_x + i * dy,
                            y=origin_y + used_y + j * dx,
                            z=0.0,
                            dx=dy, dy=dx, dz=dz, rotation=1,
                        ))
        pattern = LayerPattern(placements=placements, pattern_name="mixed",
                               case_dx=dx, case_dy=dy, case_dz=dz)
        if best is None or pattern.count > best.count:
            best = pattern
    return best if best is not None else _normal_layer(
        dx, dy, dz, usable_x, usable_y, origin_x, origin_y)


def _interlock_layers(base_layer: LayerPattern,
                      usable_x: float, usable_y: float,
                      origin_x: float, origin_y: float) -> LayerPattern:
    """Produce the alternate "B" layer for an interlock stack.

    The B layer is the base layer rotated 90° about Z, re-tiled inside the
    usable rectangle. The two layers alternate to give interlocking
    stability. If the rotated version can't fit, we return None.
    """
    dx = base_layer.case_dx
    dy = base_layer.case_dy
    dz = base_layer.case_dz
    # rotate footprint 90°
    rotated = _normal_layer(dy, dx, dz, usable_x, usable_y, origin_x, origin_y)
    rotated.pattern_name = "interlock-B"
    return rotated


# ---------------------------------------------------------------------------
# Solution evaluation
# ---------------------------------------------------------------------------

def _evaluate_layer(layer: LayerPattern,
                    pallet: Pallet,
                    case_dz: float) -> Tuple[int, int, float]:
    """Return (case_count, side_faces_visible, layer_area_used)."""
    if not layer.placements:
        return 0, 0, 0.0

    count = layer.count
    area_used = sum(p.dx * p.dy for p in layer.placements)

    # Determine outward-facing barcode count (we count cases on the perimeter
    # whose long side faces the pallet edge; for simplicity we count cases
    # that touch the usable-area perimeter on at least one side.)
    usable_x = pallet.usable_length
    usable_y = pallet.usable_width
    ox = pallet.margin_left
    oy = pallet.margin_front
    edge_tol = 1.0  # mm tolerance

    perimeter = 0
    for p in layer.placements:
        on_left   = abs(p.x - ox) < edge_tol
        on_right  = abs((p.x + p.dx) - (ox + usable_x)) < edge_tol
        on_front  = abs(p.y - oy) < edge_tol
        on_back   = abs((p.y + p.dy) - (oy + usable_y)) < edge_tol
        if on_left or on_right or on_front or on_back:
            perimeter += 1
    return count, perimeter, area_used


def _compose_stack(layer_a: LayerPattern,
                   layer_b: Optional[LayerPattern],
                   pallet: Pallet,
                   case_dz: float,
                   interlock: bool) -> List[LayerPattern]:
    """Generate the stack of layers (copies of A/B) up to max height."""
    if case_dz <= 0:
        return []
    n_layers = int(pallet.usable_height // case_dz)
    layers: List[LayerPattern] = []
    for i in range(n_layers):
        src = layer_b if (interlock and layer_b is not None and i % 2 == 1) else layer_a
        # Make a shifted copy with proper z
        new_placements = []
        for p in src.placements:
            new_placements.append(PlacedCase(
                x=p.x, y=p.y, z=i * case_dz,
                dx=p.dx, dy=p.dy, dz=p.dz,
                rotation=p.rotation,
            ))
        layers.append(LayerPattern(placements=new_placements,
                                   pattern_name=src.pattern_name,
                                   case_dx=src.case_dx,
                                   case_dy=src.case_dy,
                                   case_dz=src.case_dz))
    return layers


def _assign_face_labels(solution: StackingSolution) -> None:
    """Tag each placed case with which face (L/W/H) points along X/Y/Z,
    so the renderer can paint the correct colour (Front=blue side / Side=green
    side / Top=orange top).

    Convention:
      - vertical_axis is the case axis pointing up -> "Top" face colour.
      - For each placed case, face_x / face_y is the case axis pointing
        along pallet +X / +Y. Front face = pallet +Y direction (or -Y).
        Side face = pallet +X direction.
    """
    v = solution.vertical_axis
    axes = ["L", "W", "H"]
    horizontal = [a for a in axes if a != v]
    # rotation 0: face_x=horizontal[0], face_y=horizontal[1]
    # rotation 1: swapped
    for layer in solution.layers:
        for p in layer.placements:
            if p.rotation == 0:
                p.face_x, p.face_y, p.face_z = horizontal[0], horizontal[1], v
            else:
                p.face_x, p.face_y, p.face_z = horizontal[1], horizontal[0], v


# ---------------------------------------------------------------------------
# Public optimizer entry-point
# ---------------------------------------------------------------------------

def optimize(case: Case, pallet: Pallet,
             top_n: int = 5,
             allow_interlock: bool = True,
             barcode_weight: float = 0.15) -> List[StackingSolution]:
    """Generate the top-N stacking solutions ranked by multi-objective score.

    Primary objective: total case count (weight 1.0).
    Secondary: barcode exposure ratio (default weight 0.15).
    """
    candidates: List[StackingSolution] = []

    # tag set to suppress near-identical patterns
    seen = set()

    orientations = _all_orientations(case)
    for dx_case, dy_case, dz_case, fx, fy, fz, v_axis in orientations:
        if dz_case > pallet.usable_height:
            continue
        usable_x = pallet.usable_length
        usable_y = pallet.usable_width
        ox = pallet.margin_left
        oy = pallet.margin_front

        if dx_case > usable_x and dy_case > usable_y:
            # try swapping below as a "rotated" layer
            pass

        layer_variants: List[Tuple[str, LayerPattern]] = []
        if dx_case <= usable_x and dy_case <= usable_y:
            layer_variants.append(("normal",
                _normal_layer(dx_case, dy_case, dz_case, usable_x, usable_y, ox, oy)))
        if dy_case <= usable_x and dx_case <= usable_y:
            layer_variants.append(("rotated",
                _rotated_layer(dx_case, dy_case, dz_case, usable_x, usable_y, ox, oy)))
        if dx_case <= usable_x and dy_case <= usable_y:
            layer_variants.append(("mixed",
                _mixed_layer(dx_case, dy_case, dz_case, usable_x, usable_y, ox, oy)))

        for layout_name, layer in layer_variants:
            if layer.count == 0:
                continue

            # --- without interlock --------------------------------------
            layers = _compose_stack(layer, None, pallet, dz_case, interlock=False)
            if not layers:
                continue
            sol = _build_solution(case, pallet, layers, layer, layout_name,
                                  dx_case, dy_case, dz_case, v_axis,
                                  interlock=False, barcode_weight=barcode_weight)
            key = (round(sol.case_dx, 1), round(sol.case_dy, 1),
                   round(sol.case_dz, 1), layout_name, False)
            if key not in seen:
                candidates.append(sol)
                seen.add(key)

            # --- with interlock -----------------------------------------
            if allow_interlock and layout_name in ("normal", "rotated"):
                alt = _interlock_layers(layer, usable_x, usable_y, ox, oy)
                if alt is not None and alt.count > 0:
                    int_layers = _compose_stack(layer, alt, pallet, dz_case, True)
                    if int_layers:
                        sol2 = _build_solution(
                            case, pallet, int_layers, layer,
                            layout_name + "+interlock",
                            dx_case, dy_case, dz_case, v_axis,
                            interlock=True, barcode_weight=barcode_weight)
                        key2 = (round(sol2.case_dx, 1), round(sol2.case_dy, 1),
                                round(sol2.case_dz, 1),
                                layout_name + "+interlock", True)
                        if key2 not in seen:
                            candidates.append(sol2)
                            seen.add(key2)

    # rank
    candidates.sort(key=lambda s: s.score, reverse=True)
    return candidates[:top_n]


def _build_solution(case: Case, pallet: Pallet,
                    layers: List[LayerPattern],
                    base_layer: LayerPattern,
                    layout_name: str,
                    dx_case: float, dy_case: float, dz_case: float,
                    v_axis: str,
                    interlock: bool,
                    barcode_weight: float) -> StackingSolution:
    """Wrap a stack of layers into a StackingSolution and score it."""
    total_cases = sum(L.count for L in layers)
    cases_per_layer = base_layer.count
    layer_count = len(layers)

    # area utilization based on base layer
    layer_area_used = sum(p.dx * p.dy for p in base_layer.placements)
    pallet_usable_area = pallet.usable_length * pallet.usable_width
    area_util = layer_area_used / pallet_usable_area if pallet_usable_area > 0 else 0.0

    # volume utilization
    case_vol = dx_case * dy_case * dz_case
    pallet_usable_vol = pallet_usable_area * pallet.usable_height
    vol_util = (total_cases * case_vol) / pallet_usable_vol if pallet_usable_vol > 0 else 0.0

    # barcode exposure: perimeter cases / total cases * layers
    _, perim_cases, _ = _evaluate_layer(base_layer, pallet, dz_case)
    if cases_per_layer > 0:
        exposure = perim_cases / cases_per_layer
    else:
        exposure = 0.0

    sol = StackingSolution(
        case=case, pallet=pallet,
        vertical_axis=v_axis,
        case_dx=dx_case, case_dy=dy_case, case_dz=dz_case,
        layers=layers,
        layout_name=layout_name,
        interlock=interlock,
        cases_per_layer=cases_per_layer,
        layer_count=layer_count,
        total_cases=total_cases,
        area_utilization=area_util,
        volume_utilization=vol_util,
        barcode_exposure=exposure,
    )

    # multi-objective score:
    # primary = total_cases (normalized softly by 1+log)
    # secondary = barcode exposure
    # small bonus for interlock for stability when total cases tie
    sol.score = total_cases * (1.0 + barcode_weight * exposure) \
                + (0.001 if interlock else 0.0)

    _assign_face_labels(sol)
    return sol


# Convenience: produce a human-readable comparison table for the top-N
def compare_solutions(solutions: List[StackingSolution]) -> List[dict]:
    rows = []
    for i, s in enumerate(solutions, start=1):
        rows.append({
            "rank": i,
            "layout": s.layout_name,
            "vertical_axis": s.vertical_axis,
            "cases_per_layer": s.cases_per_layer,
            "layers": s.layer_count,
            "total_cases": s.total_cases,
            "area_util_%": round(s.area_utilization * 100.0, 2),
            "volume_util_%": round(s.volume_utilization * 100.0, 2),
            "barcode_exposure_%": round(s.barcode_exposure * 100.0, 2),
            "interlock": s.interlock,
            "score": round(s.score, 3),
        })
    return rows
