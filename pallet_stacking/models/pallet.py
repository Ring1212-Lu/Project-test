"""Pallet model with reserved (unusable) margins."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Pallet:
    """A pallet platform with reserved margins and an inter-carton gap.

    All dimensions in mm.

    ``carton_gap`` is the spacing required between adjacent cartons on
    the same layer (and between layers).  Use it to model real-world
    tolerances: corrugated bulge, shrink-wrap thickness, alignment
    slop, etc.
    """
    length: float
    width:  float
    height: float = 150.0                # pallet platform thickness
    max_total_height: float = 1800.0     # max allowed stack height (incl. pallet)
    margin_front: float = 0.0
    margin_back:  float = 0.0
    margin_left:  float = 0.0
    margin_right: float = 0.0
    carton_gap: float = 0.0              # mm spacing between adjacent cartons
    weight: float = 0.0                  # empty pallet weight in kg
    name: str = "Standard Pallet"

    @property
    def usable_length(self) -> float:
        return max(0.0, self.length - self.margin_left - self.margin_right)

    @property
    def usable_width(self) -> float:
        return max(0.0, self.width - self.margin_front - self.margin_back)

    @property
    def usable_area(self) -> float:
        return self.usable_length * self.usable_width

    @property
    def usable_height(self) -> float:
        return max(0.0, self.max_total_height - self.height)

    @property
    def usable_volume(self) -> float:
        return self.usable_area * self.usable_height
