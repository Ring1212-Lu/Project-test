"""Multi-objective scoring of a stacking solution.

    score = (total_cases          * cases_weight)
          + (barcode_exposure     * barcode_weight)
          + (area_utilization     * area_weight)

Default weights follow the project spec:
    cases_weight   = 1000
    barcode_weight = 100
    area_weight    = 10
"""
from __future__ import annotations

from ..models import StackingResult


def score_solution(result: StackingResult,
                   cases_weight: float = 1000.0,
                   barcode_weight: float = 100.0,
                   area_weight: float = 10.0) -> float:
    s = (result.total_cases * cases_weight
         + result.barcode_exposure * barcode_weight
         + result.area_utilization * area_weight)
    # very small tiebreaker - interlock = stability
    if result.interlock:
        s += 0.5
    return s
