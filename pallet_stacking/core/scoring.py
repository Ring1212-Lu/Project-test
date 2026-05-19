"""Multi-objective scoring of a stacking solution.

    score = (total_cases          * cases_weight)
          + (barcode_exposure     * barcode_weight)
          + (area_utilization     * area_weight)
          + (small stability bonus by layout family + interlock flag)

The stability bonus encodes the real-world preference that an interlocking
"frame" (回字形) layer resists horizontal shear far better than a parallel
"normal" grid, even when the two carry the same number of cartons with the
same barcode visibility.  Its magnitude (< 1) is tiny relative to one
extra carton (= cases_weight) or 1 % barcode (= barcode_weight / 100), so
it only re-orders solutions that are otherwise tied on the primary
metrics.

Default weights follow the project spec:
    cases_weight   = 1000
    barcode_weight = 100
    area_weight    = 10
"""
from __future__ import annotations

from ..models import StackingResult


# Higher = more stable layer.  Frame (perimeter rotated, interior straight)
# is the most interlocking single-layer pattern; plain "normal" rows are
# the least.  Values are intentionally < 1 so they only tie-break.
_STABILITY_BONUS = {
    "frame":    0.4,
    "pinwheel": 0.3,
    "mixed":    0.2,
    "rotated":  0.1,
    "normal":   0.0,
}


def _layout_stability(layout_name: str) -> float:
    # Strip "+interlock" / "+spread" suffix and "-X"/"-Y" axis suffix
    # so "frame-X" and "frame-Y+interlock" both map to "frame".
    base = layout_name.split("+", 1)[0].split("-", 1)[0]
    return _STABILITY_BONUS.get(base, 0.0)


def score_solution(result: StackingResult,
                   cases_weight: float = 1000.0,
                   barcode_weight: float = 100.0,
                   area_weight: float = 10.0) -> float:
    s = (result.total_cases * cases_weight
         + result.barcode_exposure * barcode_weight
         + result.area_utilization * area_weight)
    # Stability tie-breakers (sum stays < 1).
    s += _layout_stability(result.layout_name)
    if result.interlock:
        s += 0.5
    return s
