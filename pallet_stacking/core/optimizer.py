"""Pallet stacking search.

Tries every cube orientation of the carton (6) and three layer pattern
families (``normal`` / ``rotated`` / ``mixed``).  Each family may also be
combined with ``interlock`` stacking, where layer B is layer A rotated
90° about Z and alternates with A.

The result list is ranked by the scoring function in ``core.scoring``.
"""
from __future__ import annotations

from typing import List, Tuple, Optional

from ..models import Carton, Pallet, PlacedCarton, LayerPattern, StackingResult
from .geometry import is_barcode_visible, validate_solution
from .scoring import score_solution


# ---------------------------------------------------------------------------
# Carton orientations
# ---------------------------------------------------------------------------

# Each entry = (dx, dy, dz, face_x, face_y, face_z, vertical_axis_label)
def _all_orientations(carton: Carton):
    L, W, H = carton.length, carton.width, carton.height
    return [
        (L, W, H, "L", "W", "H", "H"),
        (W, L, H, "W", "L", "H", "H"),
        (L, H, W, "L", "H", "W", "W"),
        (H, L, W, "H", "L", "W", "W"),
        (W, H, L, "W", "H", "L", "L"),
        (H, W, L, "H", "W", "L", "L"),
    ]


def _grid_count(usable_x: float, usable_y: float,
                dx: float, dy: float) -> Tuple[int, int]:
    if dx <= 0 or dy <= 0:
        return 0, 0
    return max(int(usable_x // dx), 0), max(int(usable_y // dy), 0)


# ---------------------------------------------------------------------------
# Layer pattern generators
# ---------------------------------------------------------------------------

def _normal_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                  face_x, face_y, face_z) -> LayerPattern:
    nx, ny = _grid_count(usable_x, usable_y, dx, dy)
    placements = []
    for i in range(nx):
        for j in range(ny):
            placements.append(PlacedCarton(
                x=ox + i * dx, y=oy + j * dy, z=0.0,
                dx=dx, dy=dy, dz=dz, rotation=0,
                face_x=face_x, face_y=face_y, face_z=face_z,
            ))
    return LayerPattern(placements=placements, pattern_name="normal",
                        case_dx=dx, case_dy=dy, case_dz=dz)


def _rotated_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                   face_x, face_y, face_z) -> LayerPattern:
    """Single-orientation layer with every carton rotated 90° about Z."""
    nx, ny = _grid_count(usable_x, usable_y, dy, dx)
    placements = []
    for i in range(nx):
        for j in range(ny):
            placements.append(PlacedCarton(
                x=ox + i * dy, y=oy + j * dx, z=0.0,
                dx=dy, dy=dx, dz=dz, rotation=1,
                # rotated => face labels swap on X/Y
                face_x=face_y, face_y=face_x, face_z=face_z,
            ))
    return LayerPattern(placements=placements, pattern_name="rotated",
                        case_dx=dy, case_dy=dx, case_dz=dz)


def _mixed_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                 face_x, face_y, face_z) -> LayerPattern:
    """Block of normal + filler strip of rotated cartons.  Picks whichever
    split (along X or along Y) yields more cartons."""
    best: Optional[LayerPattern] = None
    for split in ("X", "Y"):
        placements: List[PlacedCarton] = []
        if split == "X":
            n_normal_x = int(usable_x // dx)
            ny_normal  = int(usable_y // dy)
            used_x = n_normal_x * dx
            remain_x = usable_x - used_x
            for i in range(n_normal_x):
                for j in range(ny_normal):
                    placements.append(PlacedCarton(
                        x=ox + i * dx, y=oy + j * dy, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z,
                    ))
            if remain_x >= dy:
                nx_r = int(remain_x // dy)
                ny_r = int(usable_y // dx)
                for i in range(nx_r):
                    for j in range(ny_r):
                        placements.append(PlacedCarton(
                            x=ox + used_x + i * dy,
                            y=oy + j * dx, z=0.0,
                            dx=dy, dy=dx, dz=dz, rotation=1,
                            face_x=face_y, face_y=face_x, face_z=face_z,
                        ))
        else:
            n_normal_y = int(usable_y // dy)
            nx_normal  = int(usable_x // dx)
            used_y = n_normal_y * dy
            remain_y = usable_y - used_y
            for i in range(nx_normal):
                for j in range(n_normal_y):
                    placements.append(PlacedCarton(
                        x=ox + i * dx, y=oy + j * dy, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z,
                    ))
            if remain_y >= dx:
                ny_r = int(remain_y // dx)
                nx_r = int(usable_x // dy)
                for i in range(nx_r):
                    for j in range(ny_r):
                        placements.append(PlacedCarton(
                            x=ox + i * dy,
                            y=oy + used_y + j * dx, z=0.0,
                            dx=dy, dy=dx, dz=dz, rotation=1,
                            face_x=face_y, face_y=face_x, face_z=face_z,
                        ))
        pattern = LayerPattern(placements=placements, pattern_name="mixed",
                               case_dx=dx, case_dy=dy, case_dz=dz)
        if best is None or pattern.count > best.count:
            best = pattern
    return best if best is not None else _normal_layer(
        dx, dy, dz, usable_x, usable_y, ox, oy, face_x, face_y, face_z)


def _frame_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                 face_x, face_y, face_z, axis: str = "Y") -> Optional[LayerPattern]:
    """Frame / pinwheel pattern: a strip of *rotated* cartons hugging the
    pallet edge (so their barcode side faces sit flush with the perimeter)
    plus the rest of the area filled with *normal*-oriented cartons.

    Two strips (front + back, or left + right) are used only when the
    dimensions divide cleanly so that no internal gap exposes the pallet.
    Otherwise a single strip + centre layout is used to avoid see-through
    gaps between rows of cartons.

    Returns None when there isn't enough room for any valid frame.
    """
    placements: List[PlacedCarton] = []

    if axis == "Y":
        if dx > usable_y or dy > usable_x:
            return None
        nx_strip = int(usable_x // dy)
        if nx_strip == 0:
            return None

        # try "double strip + centre" only when the centre fits with no gap
        centre_h_double = usable_y - 2 * dx
        ny_centre_double = max(int(centre_h_double // dy), 0)
        gap_double = centre_h_double - ny_centre_double * dy
        use_double = (2 * dx <= usable_y and ny_centre_double >= 1
                      and gap_double < 1.0)

        if use_double:
            for i in range(nx_strip):           # front strip
                placements.append(PlacedCarton(
                    x=ox + i * dy, y=oy, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
            nx_centre = int(usable_x // dx)
            for i in range(nx_centre):          # centre band (snug)
                for j in range(ny_centre_double):
                    placements.append(PlacedCarton(
                        x=ox + i * dx,
                        y=oy + dx + j * dy, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z))
            back_y = oy + usable_y - dx         # back strip (flush)
            for i in range(nx_strip):
                placements.append(PlacedCarton(
                    x=ox + i * dy, y=back_y, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
        else:
            # single front strip + centre fills the remaining area
            for i in range(nx_strip):
                placements.append(PlacedCarton(
                    x=ox + i * dy, y=oy, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
            centre_h = usable_y - dx
            ny_centre = int(centre_h // dy)
            if ny_centre == 0:
                return None
            nx_centre = int(usable_x // dx)
            for i in range(nx_centre):
                for j in range(ny_centre):
                    placements.append(PlacedCarton(
                        x=ox + i * dx,
                        y=oy + dx + j * dy, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z))
    else:  # axis == "X"
        if dy > usable_x or dx > usable_y:
            return None
        ny_strip = int(usable_y // dy)
        if ny_strip == 0:
            return None

        centre_w_double = usable_x - 2 * dx
        nx_centre_double = max(int(centre_w_double // dx), 0)
        gap_double = centre_w_double - nx_centre_double * dx
        use_double = (2 * dx <= usable_x and nx_centre_double >= 1
                      and gap_double < 1.0)

        if use_double:
            for j in range(ny_strip):           # left strip
                placements.append(PlacedCarton(
                    x=ox, y=oy + j * dy, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
            ny_centre = int(usable_y // dy)
            for i in range(nx_centre_double):   # centre band (snug)
                for j in range(ny_centre):
                    placements.append(PlacedCarton(
                        x=ox + dx + i * dx,
                        y=oy + j * dy, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z))
            right_x = ox + usable_x - dx        # right strip (flush)
            for j in range(ny_strip):
                placements.append(PlacedCarton(
                    x=right_x, y=oy + j * dy, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
        else:
            # single left strip + centre
            for j in range(ny_strip):
                placements.append(PlacedCarton(
                    x=ox, y=oy + j * dy, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
            centre_w = usable_x - dx
            nx_centre = int(centre_w // dx)
            if nx_centre == 0:
                return None
            ny_centre = int(usable_y // dy)
            for i in range(nx_centre):
                for j in range(ny_centre):
                    placements.append(PlacedCarton(
                        x=ox + dx + i * dx,
                        y=oy + j * dy, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z))

    if not placements:
        return None
    return LayerPattern(placements=placements, pattern_name=f"frame-{axis}",
                        case_dx=dx, case_dy=dy, case_dz=dz)


def _interlock_partner(base_layer: LayerPattern,
                       usable_x, usable_y, ox, oy,
                       face_x, face_y, face_z) -> Optional[LayerPattern]:
    """Build the alternate "B" layer (rotated 90°) for an interlock stack."""
    if base_layer.count == 0:
        return None
    dx, dy, dz = base_layer.case_dx, base_layer.case_dy, base_layer.case_dz
    if dy > usable_x or dx > usable_y:
        return None
    rotated = _normal_layer(dy, dx, dz, usable_x, usable_y, ox, oy,
                            face_y, face_x, face_z)
    rotated.pattern_name = "interlock-B"
    return rotated


# ---------------------------------------------------------------------------
# Stack composition
# ---------------------------------------------------------------------------

def _compose_stack(layer_a: LayerPattern,
                   layer_b: Optional[LayerPattern],
                   pallet: Pallet,
                   case_dz: float,
                   interlock: bool) -> List[LayerPattern]:
    if case_dz <= 0:
        return []
    n_layers = int(pallet.usable_height // case_dz)
    layers: List[LayerPattern] = []
    for i in range(n_layers):
        src = layer_b if (interlock and layer_b is not None and i % 2 == 1) else layer_a
        new_placements = [PlacedCarton(
            x=p.x, y=p.y, z=i * case_dz,
            dx=p.dx, dy=p.dy, dz=p.dz,
            rotation=p.rotation,
            face_x=p.face_x, face_y=p.face_y, face_z=p.face_z,
        ) for p in src.placements]
        layers.append(LayerPattern(
            placements=new_placements,
            pattern_name=src.pattern_name,
            case_dx=src.case_dx, case_dy=src.case_dy, case_dz=src.case_dz))
    return layers


# ---------------------------------------------------------------------------
# Public optimizer entry-point
# ---------------------------------------------------------------------------

def optimize(carton: Carton, pallet: Pallet,
             top_n: int = 5,
             allow_interlock: bool = True,
             cases_weight: float = 1000.0,
             barcode_weight: float = 100.0,
             area_weight: float = 10.0,
             validate: bool = False) -> List[StackingResult]:
    """Return the top-N solutions ranked by the multi-objective score."""
    candidates: List[StackingResult] = []
    seen = set()

    for dx, dy, dz, fx, fy, fz, v_axis in _all_orientations(carton):
        if dz > pallet.usable_height:
            continue
        usable_x = pallet.usable_length
        usable_y = pallet.usable_width
        ox = pallet.margin_left
        oy = pallet.margin_front

        variants: List[Tuple[str, LayerPattern]] = []
        if dx <= usable_x and dy <= usable_y:
            variants.append(("normal",
                _normal_layer(dx, dy, dz, usable_x, usable_y, ox, oy, fx, fy, fz)))
        if dy <= usable_x and dx <= usable_y:
            variants.append(("rotated",
                _rotated_layer(dx, dy, dz, usable_x, usable_y, ox, oy, fx, fy, fz)))
        if dx <= usable_x and dy <= usable_y:
            variants.append(("mixed",
                _mixed_layer(dx, dy, dz, usable_x, usable_y, ox, oy, fx, fy, fz)))
        # Frame / pinwheel-style: rotated cartons on perimeter,
        # normal cartons in the middle - boosts barcode side-out exposure.
        for axis in ("Y", "X"):
            frame = _frame_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                                 fx, fy, fz, axis=axis)
            if frame is not None:
                variants.append((f"frame-{axis}", frame))

        for layout_name, layer in variants:
            if layer.count == 0:
                continue

            # without interlock
            layers = _compose_stack(layer, None, pallet, dz, False)
            if layers:
                sol = _build_result(carton, pallet, layers, layer, layout_name,
                                    dx, dy, dz, v_axis, False,
                                    cases_weight, barcode_weight, area_weight)
                k = (round(sol.case_dx, 1), round(sol.case_dy, 1),
                     round(sol.case_dz, 1), layout_name, False)
                if k not in seen:
                    if not validate or not validate_solution(sol):
                        candidates.append(sol)
                        seen.add(k)

            # interlock (only meaningful for single-orientation layers)
            if allow_interlock and layout_name in ("normal", "rotated"):
                alt = _interlock_partner(layer, usable_x, usable_y, ox, oy,
                                         fx, fy, fz)
                if alt is not None and alt.count > 0:
                    int_layers = _compose_stack(layer, alt, pallet, dz, True)
                    if int_layers:
                        sol2 = _build_result(
                            carton, pallet, int_layers, layer,
                            layout_name + "+interlock",
                            dx, dy, dz, v_axis, True,
                            cases_weight, barcode_weight, area_weight)
                        k2 = (round(sol2.case_dx, 1), round(sol2.case_dy, 1),
                              round(sol2.case_dz, 1),
                              layout_name + "+interlock", True)
                        if k2 not in seen:
                            if not validate or not validate_solution(sol2):
                                candidates.append(sol2)
                                seen.add(k2)

    candidates.sort(key=lambda s: s.score, reverse=True)
    return candidates[:top_n]


def _build_result(carton: Carton, pallet: Pallet,
                  layers: List[LayerPattern],
                  base_layer: LayerPattern,
                  layout_name: str,
                  dx: float, dy: float, dz: float, v_axis: str,
                  interlock: bool,
                  cases_weight: float, barcode_weight: float,
                  area_weight: float) -> StackingResult:
    total_cases = sum(L.count for L in layers)
    cases_per_layer = base_layer.count
    layer_count = len(layers)

    # area utilization (single layer)
    layer_area_used = sum(p.dx * p.dy for p in base_layer.placements)
    area_util = (layer_area_used / pallet.usable_area
                 if pallet.usable_area > 0 else 0.0)

    # volume utilization
    case_vol = dx * dy * dz
    vol_util = ((total_cases * case_vol) / pallet.usable_volume
                if pallet.usable_volume > 0 else 0.0)

    # barcode exposure: tag each carton + measure base layer ratio
    visible = 0
    for layer in layers:
        for p in layer.placements:
            p.barcode_visible = is_barcode_visible(
                p, pallet, carton.barcode_face_axis)
    for p in base_layer.placements:
        if is_barcode_visible(p, pallet, carton.barcode_face_axis):
            visible += 1
    exposure = visible / cases_per_layer if cases_per_layer > 0 else 0.0

    result = StackingResult(
        carton=carton, pallet=pallet,
        vertical_axis=v_axis,
        case_dx=dx, case_dy=dy, case_dz=dz,
        layers=layers, layout_name=layout_name, interlock=interlock,
        cases_per_layer=cases_per_layer,
        layer_count=layer_count,
        total_cases=total_cases,
        area_utilization=area_util,
        volume_utilization=vol_util,
        barcode_exposure=exposure,
    )
    result.score = score_solution(result, cases_weight, barcode_weight, area_weight)
    return result


def compare_solutions(solutions: List[StackingResult]) -> List[dict]:
    """Human-readable table for the top-N solutions."""
    return [{**{"rank": i + 1}, **s.to_summary()} for i, s in enumerate(solutions)]
