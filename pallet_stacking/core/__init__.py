"""Core algorithms: geometry, scoring, optimization."""
from .geometry import (
    overlaps, within_usable, is_barcode_visible,
    validate_layer, validate_solution,
)
from .scoring import score_solution
from .optimizer import optimize, compare_solutions
from .verifier  import verify_layer, area_upper_bound, VerifyResult
from .spread    import spread_layer, filler_rectangles

__all__ = [
    "overlaps", "within_usable", "is_barcode_visible",
    "validate_layer", "validate_solution",
    "score_solution",
    "optimize", "compare_solutions",
    "verify_layer", "area_upper_bound", "VerifyResult",
    "spread_layer", "filler_rectangles",
]
