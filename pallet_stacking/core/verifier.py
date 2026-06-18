"""Independent optimality verification using a CP-SAT model.

The main optimizer in ``optimizer.py`` searches a *heuristic* family of
layer patterns (normal / rotated / mixed / frame / pinwheel) and picks
the best.  This module proves whether that best is also the *global*
optimum, by re-solving the same one-layer packing problem with Google
OR-Tools' constraint-programming SAT engine.

Use it whenever a new carton spec lands and you want a sanity check::

    >>> from pallet_stacking.core.verifier import verify_layer
    >>> verify_layer(carton, pallet, optimizer_count=15)
    VerifyResult(optimizer=15, exact=15, area_upper_bound=16,
                 optimal=True, status='UNSAT@16', wall_s=42.1)

A status of ``UNSAT@N`` means the solver proved that no arrangement of
N cartons fits, so the optimizer's count (= N-1) is provably optimal.
``SAT@N`` means a placement of N cartons exists (counter-example).
``TIMEOUT@N`` means the proof did not finish in the time budget — the
result is then "unknown, but at least optimizer_count is feasible".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import Carton, Pallet, StackingResult


@dataclass
class VerifyResult:
    optimizer_count: int        # what the heuristic optimizer found
    exact_count: Optional[int]  # solver-confirmed maximum (None if timed out)
    area_upper_bound: int       # trivial upper bound from areas
    optimal: Optional[bool]     # True / False / None (unknown)
    status: str                 # e.g. "SAT@16", "UNSAT@16", "TIMEOUT@16"
    wall_s: float


def area_upper_bound(carton_or_footprint, pallet: Pallet) -> int:
    """Floor(usable_area / footprint_area) — an absolute upper bound.

    Accepts either a ``Carton`` (uses its L x W footprint) or a tuple
    ``(footprint_l, footprint_w)`` for an already-rotated footprint.
    """
    if isinstance(carton_or_footprint, Carton):
        fl, fw = carton_or_footprint.length, carton_or_footprint.width
    else:
        fl, fw = carton_or_footprint
    return int((pallet.usable_length * pallet.usable_width) // (fl * fw))


def _try_fit(n: int, pallet_L: int, pallet_W: int,
             carton_L: int, carton_W: int,
             time_limit: float, workers: int):
    """Return (status, wall_seconds) from CP-SAT trying to fit n cartons."""
    from ortools.sat.python import cp_model

    m = cp_model.CpModel()
    x_intervals, y_intervals = [], []
    xs, ys = [], []
    for i in range(n):
        rot = m.NewBoolVar(f"r{i}")
        dx = m.NewIntVar(min(carton_L, carton_W),
                         max(carton_L, carton_W), f"dx{i}")
        dy = m.NewIntVar(min(carton_L, carton_W),
                         max(carton_L, carton_W), f"dy{i}")
        m.Add(dx == carton_L).OnlyEnforceIf(rot.Not())
        m.Add(dy == carton_W).OnlyEnforceIf(rot.Not())
        m.Add(dx == carton_W).OnlyEnforceIf(rot)
        m.Add(dy == carton_L).OnlyEnforceIf(rot)
        x = m.NewIntVar(0, pallet_L, f"x{i}")
        y = m.NewIntVar(0, pallet_W, f"y{i}")
        xe = m.NewIntVar(0, pallet_L, f"xe{i}")
        ye = m.NewIntVar(0, pallet_W, f"ye{i}")
        m.Add(xe == x + dx); m.Add(ye == y + dy)
        m.Add(xe <= pallet_L); m.Add(ye <= pallet_W)
        x_intervals.append(m.NewIntervalVar(x, dx, xe, f"ix{i}"))
        y_intervals.append(m.NewIntervalVar(y, dy, ye, f"iy{i}"))
        xs.append(x); ys.append(y)

    m.AddNoOverlap2D(x_intervals, y_intervals)
    # Symmetry breaking — lex order on (x, y) cuts search dramatically.
    for i in range(n - 1):
        b = m.NewBoolVar(f"b{i}")
        m.Add(xs[i] < xs[i + 1]).OnlyEnforceIf(b)
        m.Add(xs[i] == xs[i + 1]).OnlyEnforceIf(b.Not())
        m.Add(ys[i] <= ys[i + 1]).OnlyEnforceIf(b.Not())

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = workers
    res = solver.Solve(m)
    name = {cp_model.OPTIMAL: "SAT", cp_model.FEASIBLE: "SAT",
            cp_model.INFEASIBLE: "UNSAT",
            cp_model.UNKNOWN: "TIMEOUT"}.get(res, "ERROR")
    return name, solver.WallTime()


def verify_layer(target, pallet: Pallet,
                 optimizer_count: Optional[int] = None,
                 time_limit: float = 120.0,
                 workers: int = 8) -> VerifyResult:
    """Prove (or disprove) that ``optimizer_count`` is the per-layer max.

    ``target`` is either a ``StackingResult`` (the optimizer's #1 solution
    — its actual rotated footprint and cases-per-layer are read directly)
    or a ``Carton`` (use the carton's L x W footprint; ``optimizer_count``
    must then be supplied explicitly).

    The function inspects only the planar packing problem: how many
    copies of the chosen footprint fit on the pallet usable area in any
    in-plane rotation.  Vertical stacking is **not** part of this check.

    Strategy:
      1. Compute the area-based upper bound ``UB``.
      2. If ``optimizer_count > UB`` → impossible, flag INVALID.
      3. If ``optimizer_count == UB`` → optimal by area, no SAT needed.
      4. Otherwise ask CP-SAT: "can N = optimizer_count + 1 fit?"
         - UNSAT  → optimizer_count is provably optimal.
         - SAT    → optimizer missed at least one carton — counter-example
                    proves the heuristic is sub-optimal here.
         - TIMEOUT → unknown.
    """
    if isinstance(target, StackingResult):
        cL = int(round(target.case_dx))
        cW = int(round(target.case_dy))
        if optimizer_count is None:
            optimizer_count = target.cases_per_layer
    elif isinstance(target, Carton):
        cL = int(round(target.length))
        cW = int(round(target.width))
        if optimizer_count is None:
            raise ValueError("optimizer_count must be supplied when target "
                             "is a Carton.")
    else:
        raise TypeError(f"target must be a StackingResult or Carton, got "
                        f"{type(target).__name__}")

    L = int(round(pallet.usable_length))
    W = int(round(pallet.usable_width))
    ub = area_upper_bound((cL, cW), pallet)

    # The heuristic should never claim more cartons than the area allows;
    # if it does, the placement is overlapping or out of bounds.
    if optimizer_count > ub:
        return VerifyResult(optimizer_count, ub, ub, False,
                            status=f"INVALID (exceeds area bound {ub})",
                            wall_s=0.0)
    if optimizer_count == ub:
        return VerifyResult(optimizer_count, optimizer_count, ub, True,
                            status=f"area-tight@{ub}", wall_s=0.0)

    target = optimizer_count + 1
    status, wall = _try_fit(target, L, W, cL, cW, time_limit, workers)
    if status == "UNSAT":
        return VerifyResult(optimizer_count, optimizer_count, ub, True,
                            status=f"UNSAT@{target}", wall_s=wall)
    if status == "SAT":
        return VerifyResult(optimizer_count, target, ub, False,
                            status=f"SAT@{target}", wall_s=wall)
    return VerifyResult(optimizer_count, None, ub, None,
                        status=f"TIMEOUT@{target}", wall_s=wall)
