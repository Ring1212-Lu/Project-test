"""Result data classes (one placed carton, one layer, one full result)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .carton import Carton
    from .pallet import Pallet


@dataclass
class PlacedCarton:
    """One carton placed somewhere on the pallet.

    Coordinates are mm relative to the pallet origin (bottom-left of the
    pallet platform). ``z`` is measured *above* the pallet platform (so
    z=0 sits on top of the pallet at world-Z = pallet.height).
    """
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float
    rotation: int = 0           # 0 or 1 (90° about Z)
    # which case-axis label ("L"/"W"/"H") points along pallet +X / +Y / +Z
    face_x: str = "L"
    face_y: str = "W"
    face_z: str = "H"
    barcode_visible: bool = False


@dataclass
class LayerPattern:
    """A single layer arrangement on top of the pallet."""
    placements: List[PlacedCarton]
    pattern_name: str          # "normal" | "rotated" | "mixed" | "interlock-B"
    case_dx: float
    case_dy: float
    case_dz: float

    @property
    def count(self) -> int:
        return len(self.placements)


@dataclass
class StackingResult:
    """A full pallet stacking solution."""
    carton: "Carton"
    pallet: "Pallet"
    vertical_axis: str          # "L" | "W" | "H" -> which case axis is up
    case_dx: float
    case_dy: float
    case_dz: float
    layers: List[LayerPattern] = field(default_factory=list)
    layout_name: str = "normal"
    interlock: bool = False
    cases_per_layer: int = 0
    layer_count: int = 0
    total_cases: int = 0
    area_utilization: float = 0.0   # 0..1
    volume_utilization: float = 0.0 # 0..1
    barcode_exposure: float = 0.0   # 0..1
    score: float = 0.0

    def total_height(self) -> float:
        return self.pallet.height + self.layer_count * self.case_dz

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
            "area_util_%": round(self.area_utilization * 100.0, 2),
            "volume_util_%": round(self.volume_utilization * 100.0, 2),
            "barcode_exposure_%": round(self.barcode_exposure * 100.0, 2),
            "interlock": self.interlock,
            "score": round(self.score, 3),
        }
