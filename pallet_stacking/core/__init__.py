"""Core algorithms: geometry, scoring, optimization."""
from .geometry import (
    overlaps, within_usable, is_barcode_visible,
    validate_layer, validate_solution,
)
from .scoring import score_solution
from .optimizer import optimize, compare_solutions

__all__ = [
    "overlaps", "within_usable", "is_barcode_visible",
    "validate_layer", "validate_solution",
    "score_solution",
    "optimize", "compare_solutions",
]
