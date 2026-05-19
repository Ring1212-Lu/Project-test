"""Carton (outer case) model."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Carton:
    """A rectangular shipper carton.

    Attributes
    ----------
    length, width, height : float
        Outer dimensions in mm. L / W / H of the case in its canonical
        upright orientation.
    weight : float
        Net weight in kg.
    name : str
        SKU / product name shown in the report.
    barcode_face_axis : str
        Which case-local axis the *barcode face* is normal to:
            "L" -> barcode is on the L-normal face (the case "side", default)
            "W" -> barcode is on the W-normal face (the case "front")
            "H" -> barcode is on the H-normal face (the case "top")
        The optimizer treats the barcode as outward-facing only when this
        face touches the pallet usable-area boundary.
    """
    length: float
    width:  float
    height: float
    weight: float = 0.0
    name:   str = "Carton"
    barcode_face_axis: str = "L"     # default: side face

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height
