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
                dx: float, dy: float,
                gap: float = 0.0) -> Tuple[int, int]:
    """Number of cartons that fit in usable_x x usable_y given an inter-
    carton gap.  n cartons span n*d + (n-1)*gap, so:
        n <= (usable + gap) / (d + gap).
    """
    if dx <= 0 or dy <= 0:
        return 0, 0
    nx = int((usable_x + gap) // (dx + gap)) if dx + gap > 0 else 0
    ny = int((usable_y + gap) // (dy + gap)) if dy + gap > 0 else 0
    return max(nx, 0), max(ny, 0)


def _stride(d: float, gap: float) -> float:
    """Distance from the bottom-left of one carton to the bottom-left of
    its neighbour along the same axis."""
    return d + gap


# ---------------------------------------------------------------------------
# Layer pattern generators
# ---------------------------------------------------------------------------

def _normal_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                  face_x, face_y, face_z, gap: float = 0.0) -> LayerPattern:
    nx, ny = _grid_count(usable_x, usable_y, dx, dy, gap)
    sx, sy = _stride(dx, gap), _stride(dy, gap)
    placements = []
    for i in range(nx):
        for j in range(ny):
            placements.append(PlacedCarton(
                x=ox + i * sx, y=oy + j * sy, z=0.0,
                dx=dx, dy=dy, dz=dz, rotation=0,
                face_x=face_x, face_y=face_y, face_z=face_z,
            ))
    return LayerPattern(placements=placements, pattern_name="normal",
                        case_dx=dx, case_dy=dy, case_dz=dz)


def _rotated_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                   face_x, face_y, face_z, gap: float = 0.0) -> LayerPattern:
    """Single-orientation layer with every carton rotated 90° about Z."""
    nx, ny = _grid_count(usable_x, usable_y, dy, dx, gap)
    sx, sy = _stride(dy, gap), _stride(dx, gap)
    placements = []
    for i in range(nx):
        for j in range(ny):
            placements.append(PlacedCarton(
                x=ox + i * sx, y=oy + j * sy, z=0.0,
                dx=dy, dy=dx, dz=dz, rotation=1,
                # rotated => face labels swap on X/Y
                face_x=face_y, face_y=face_x, face_z=face_z,
            ))
    return LayerPattern(placements=placements, pattern_name="rotated",
                        case_dx=dy, case_dy=dx, case_dz=dz)


def _mixed_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                 face_x, face_y, face_z, gap: float = 0.0) -> LayerPattern:
    """Block of normal + filler strip of rotated cartons.  Picks whichever
    split (along X or along Y) yields more cartons."""
    best: Optional[LayerPattern] = None
    sx_n, sy_n = _stride(dx, gap), _stride(dy, gap)        # normal strides
    sx_r, sy_r = _stride(dy, gap), _stride(dx, gap)        # rotated strides
    for split in ("X", "Y"):
        placements: List[PlacedCarton] = []
        if split == "X":
            n_normal_x = int((usable_x + gap) // sx_n) if sx_n > 0 else 0
            ny_normal  = int((usable_y + gap) // sy_n) if sy_n > 0 else 0
            used_x = n_normal_x * sx_n  # next free X position (no gap after)
            remain_x = usable_x - used_x
            for i in range(n_normal_x):
                for j in range(ny_normal):
                    placements.append(PlacedCarton(
                        x=ox + i * sx_n, y=oy + j * sy_n, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z,
                    ))
            if remain_x >= dy:
                nx_r = int((remain_x + gap) // sx_r) if sx_r > 0 else 0
                ny_r = int((usable_y + gap) // sy_r) if sy_r > 0 else 0
                for i in range(nx_r):
                    for j in range(ny_r):
                        placements.append(PlacedCarton(
                            x=ox + used_x + i * sx_r,
                            y=oy + j * sy_r, z=0.0,
                            dx=dy, dy=dx, dz=dz, rotation=1,
                            face_x=face_y, face_y=face_x, face_z=face_z,
                        ))
        else:
            n_normal_y = int((usable_y + gap) // sy_n) if sy_n > 0 else 0
            nx_normal  = int((usable_x + gap) // sx_n) if sx_n > 0 else 0
            used_y = n_normal_y * sy_n
            remain_y = usable_y - used_y
            for i in range(nx_normal):
                for j in range(n_normal_y):
                    placements.append(PlacedCarton(
                        x=ox + i * sx_n, y=oy + j * sy_n, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z,
                    ))
            if remain_y >= dx:
                ny_r = int((remain_y + gap) // sy_r) if sy_r > 0 else 0
                nx_r = int((usable_x + gap) // sx_r) if sx_r > 0 else 0
                for i in range(nx_r):
                    for j in range(ny_r):
                        placements.append(PlacedCarton(
                            x=ox + i * sx_r,
                            y=oy + used_y + j * sy_r, z=0.0,
                            dx=dy, dy=dx, dz=dz, rotation=1,
                            face_x=face_y, face_y=face_x, face_z=face_z,
                        ))
        pattern = LayerPattern(placements=placements, pattern_name="mixed",
                               case_dx=dx, case_dy=dy, case_dz=dz)
        if best is None or pattern.count > best.count:
            best = pattern
    return best if best is not None else _normal_layer(
        dx, dy, dz, usable_x, usable_y, ox, oy, face_x, face_y, face_z, gap)


def _frame_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                 face_x, face_y, face_z, axis: str = "Y",
                 gap: float = 0.0) -> Optional[LayerPattern]:
    """Frame / pinwheel pattern: a strip of *rotated* cartons hugging the
    pallet edge plus the rest of the area filled with normal cartons.

    Double-strip (front + back) is used only when it fits without a
    see-through gap; otherwise a single-strip + centre layout is used.
    Inter-carton ``gap`` is honoured on all strides.
    """
    placements: List[PlacedCarton] = []
    sx_n, sy_n = _stride(dx, gap), _stride(dy, gap)
    sx_r, sy_r = _stride(dy, gap), _stride(dx, gap)

    if axis == "Y":
        if dx > usable_y or dy > usable_x:
            return None
        nx_strip = int((usable_x + gap) // sx_r) if sx_r > 0 else 0
        if nx_strip == 0:
            return None

        # would a double strip (front + back) + centre fit cleanly?
        centre_h_double = usable_y - 2 * dx - gap   # gap above & below the centre band
        ny_centre_double = max(int((centre_h_double + gap) // sy_n), 0) if sy_n > 0 else 0
        residual = centre_h_double - (ny_centre_double * dy
                                       + max(0, ny_centre_double - 1) * gap)
        use_double = (2 * dx + gap <= usable_y and ny_centre_double >= 1
                      and residual < 1.0)

        if use_double:
            for i in range(nx_strip):           # front strip
                placements.append(PlacedCarton(
                    x=ox + i * sx_r, y=oy, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
            nx_centre = int((usable_x + gap) // sx_n) if sx_n > 0 else 0
            for i in range(nx_centre):          # centre band (snug)
                for j in range(ny_centre_double):
                    placements.append(PlacedCarton(
                        x=ox + i * sx_n,
                        y=oy + dx + gap + j * sy_n, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z))
            back_y = oy + usable_y - dx
            for i in range(nx_strip):
                placements.append(PlacedCarton(
                    x=ox + i * sx_r, y=back_y, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
        else:
            for i in range(nx_strip):
                placements.append(PlacedCarton(
                    x=ox + i * sx_r, y=oy, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
            centre_h = usable_y - dx - gap
            ny_centre = int((centre_h + gap) // sy_n) if sy_n > 0 else 0
            if ny_centre == 0:
                return None
            nx_centre = int((usable_x + gap) // sx_n) if sx_n > 0 else 0
            for i in range(nx_centre):
                for j in range(ny_centre):
                    placements.append(PlacedCarton(
                        x=ox + i * sx_n,
                        y=oy + dx + gap + j * sy_n, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z))
    else:  # axis == "X"
        # Left/right strips contain *rotated* cartons whose width is dy.
        if dy > usable_x or dx > usable_y:
            return None
        ny_strip = int((usable_y + gap) // sy_r) if sy_r > 0 else 0
        if ny_strip == 0:
            return None

        centre_w_double = usable_x - 2 * dy - gap
        nx_centre_double = max(int((centre_w_double + gap) // sx_n), 0) if sx_n > 0 else 0
        residual = centre_w_double - (nx_centre_double * dx
                                       + max(0, nx_centre_double - 1) * gap)
        use_double = (2 * dy + gap <= usable_x and nx_centre_double >= 1
                      and residual < 1.0)

        if use_double:
            for j in range(ny_strip):           # left strip
                placements.append(PlacedCarton(
                    x=ox, y=oy + j * sy_r, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
            ny_centre = int((usable_y + gap) // sy_n) if sy_n > 0 else 0
            for i in range(nx_centre_double):
                for j in range(ny_centre):
                    placements.append(PlacedCarton(
                        x=ox + dy + gap + i * sx_n,
                        y=oy + j * sy_n, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z))
            right_x = ox + usable_x - dy
            for j in range(ny_strip):
                placements.append(PlacedCarton(
                    x=right_x, y=oy + j * sy_r, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
        else:
            for j in range(ny_strip):
                placements.append(PlacedCarton(
                    x=ox, y=oy + j * sy_r, z=0.0,
                    dx=dy, dy=dx, dz=dz, rotation=1,
                    face_x=face_y, face_y=face_x, face_z=face_z))
            centre_w = usable_x - dy - gap
            nx_centre = int((centre_w + gap) // sx_n) if sx_n > 0 else 0
            if nx_centre == 0:
                return None
            ny_centre = int((usable_y + gap) // sy_n) if sy_n > 0 else 0
            for i in range(nx_centre):
                for j in range(ny_centre):
                    placements.append(PlacedCarton(
                        x=ox + dy + gap + i * sx_n,
                        y=oy + j * sy_n, z=0.0,
                        dx=dx, dy=dy, dz=dz, rotation=0,
                        face_x=face_x, face_y=face_y, face_z=face_z))

    if not placements:
        return None
    return LayerPattern(placements=placements, pattern_name=f"frame-{axis}",
                        case_dx=dx, case_dy=dy, case_dz=dz)


def _pinwheel_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                    face_x, face_y, face_z,
                    gap: float = 0.0) -> Optional[LayerPattern]:
    """4-edge pinwheel pattern (a.k.a. Cape Pack pinwheel / spinwheel).

    LEFT/RIGHT = normal cartons hugging side edges.
    FRONT/BACK = rotated cartons hugging front/back edges.
    CENTRE    = whatever fits in the inner rectangle.
    """
    placements: List[PlacedCarton] = []
    sx_n, sy_n = _stride(dx, gap), _stride(dy, gap)
    sx_r, sy_r = _stride(dy, gap), _stride(dx, gap)

    # require room for strips on opposite edges (with one gap between strip
    # and the centre band)
    if (2 * dx + gap >= usable_x or 2 * dx + gap >= usable_y
            or dy > usable_x or dy > usable_y):
        return None

    # --- LEFT + RIGHT (normal orientation) -------------------------
    ny_lr = int((usable_y + gap) // sy_n) if sy_n > 0 else 0
    if ny_lr == 0:
        return None
    for j in range(ny_lr):
        placements.append(PlacedCarton(
            x=ox, y=oy + j * sy_n, z=0.0,
            dx=dx, dy=dy, dz=dz, rotation=0,
            face_x=face_x, face_y=face_y, face_z=face_z))
    right_x = ox + usable_x - dx
    for j in range(ny_lr):
        placements.append(PlacedCarton(
            x=right_x, y=oy + j * sy_n, z=0.0,
            dx=dx, dy=dy, dz=dz, rotation=0,
            face_x=face_x, face_y=face_y, face_z=face_z))

    # --- FRONT + BACK strips (rotated; in the corridor between L and R)
    inner_x = usable_x - 2 * dx - 2 * gap  # subtract two gaps (L↔inner, inner↔R)
    nx_fb = int((inner_x + gap) // sx_r) if sx_r > 0 else 0
    inner_x_start = ox + dx + gap
    for i in range(nx_fb):
        placements.append(PlacedCarton(
            x=inner_x_start + i * sx_r, y=oy, z=0.0,
            dx=dy, dy=dx, dz=dz, rotation=1,
            face_x=face_y, face_y=face_x, face_z=face_z))
    back_y = oy + usable_y - dx
    for i in range(nx_fb):
        placements.append(PlacedCarton(
            x=inner_x_start + i * sx_r, y=back_y, z=0.0,
            dx=dy, dy=dx, dz=dz, rotation=1,
            face_x=face_y, face_y=face_x, face_z=face_z))

    # --- CENTRE: normal cartons in the inner rectangle ------------
    cx0 = ox + dx + gap
    cx1 = ox + usable_x - dx - gap
    cy0 = oy + dx + gap
    cy1 = oy + usable_y - dx - gap
    cw  = cx1 - cx0
    ch  = cy1 - cy0
    if cw >= dx and ch >= dy:
        nx_c = int((cw + gap) // sx_n) if sx_n > 0 else 0
        ny_c = int((ch + gap) // sy_n) if sy_n > 0 else 0
        for i in range(nx_c):
            for j in range(ny_c):
                placements.append(PlacedCarton(
                    x=cx0 + i * sx_n, y=cy0 + j * sy_n, z=0.0,
                    dx=dx, dy=dy, dz=dz, rotation=0,
                    face_x=face_x, face_y=face_y, face_z=face_z))

    if not placements:
        return None
    return LayerPattern(placements=placements, pattern_name="pinwheel",
                        case_dx=dx, case_dy=dy, case_dz=dz)


def _interlock_partner(base_layer: LayerPattern,
                       usable_x, usable_y, ox, oy,
                       face_x, face_y, face_z,
                       gap: float = 0.0) -> Optional[LayerPattern]:
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
    gap = max(0.0, getattr(pallet, "carton_gap", 0.0))
    stride_z = case_dz + gap
    # n layers fit if n*case_dz + (n-1)*gap <= usable_height
    n_layers = int((pallet.usable_height + gap) // stride_z) if stride_z > 0 else 0
    layers: List[LayerPattern] = []
    for i in range(n_layers):
        src = layer_b if (interlock and layer_b is not None and i % 2 == 1) else layer_a
        new_placements = [PlacedCarton(
            x=p.x, y=p.y, z=i * stride_z,
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

    gap = max(0.0, getattr(pallet, "carton_gap", 0.0))

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
                _normal_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                              fx, fy, fz, gap)))
        if dy <= usable_x and dx <= usable_y:
            variants.append(("rotated",
                _rotated_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                               fx, fy, fz, gap)))
        if dx <= usable_x and dy <= usable_y:
            variants.append(("mixed",
                _mixed_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                             fx, fy, fz, gap)))
        # Frame: rotated cartons on one pair of opposite edges + centre.
        for axis in ("Y", "X"):
            frame = _frame_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                                 fx, fy, fz, axis=axis, gap=gap)
            if frame is not None:
                variants.append((f"frame-{axis}", frame))
        # 4-edge pinwheel: cartons on every pallet edge - maximises
        # barcode side-out exposure.
        pinwheel = _pinwheel_layer(dx, dy, dz, usable_x, usable_y, ox, oy,
                                   fx, fy, fz, gap=gap)
        if pinwheel is not None:
            variants.append(("pinwheel", pinwheel))

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
                                         fx, fy, fz, gap=gap)
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
