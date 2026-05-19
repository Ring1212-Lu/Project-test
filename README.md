# Pallet Stacking Tool

A Cape Pack-style desktop tool that computes the optimal carton-to-pallet
stacking pattern and produces **engineering-style** drawings, a single-page
A4 PDF report, and an Excel summary.

All dimensions in **millimetres (mm)**, weights in **kilograms (kg)**.

---

## Highlights

| Area | Capabilities |
|------|--------------|
| Algorithm | Searches every cube orientation (6) × layer pattern (`normal` / `rotated` / `mixed`) × optional `interlock` stacking. Multi-objective score `total_cases × 1000 + barcode_exposure × 100 + area_util × 10` — maximises case count first, then barcode side-face exposure. |
| Constraints | Pallet footprint, max stack height, configurable reserved margins (front/back/left/right). |
| Barcode model | Each carton has a `barcode_face_axis` (default `L` = side face). Barcode is counted as outward-facing only when the *barcode face* touches the usable pallet boundary. |
| Drawings | Engineering / CAD style — **orthographic isometric** 3D (no perspective), Top view, Front view, Side view, single-carton orientation. Thick pallet outline, thin carton edges, dashed usable area, tinted reserved-margin band. |
| Colour rule (fixed) | Front face = **blue**, Side face = **green**, Top face = **orange** — consistent across every figure and the PDF. |
| Reports | Single-page A4 PDF (header + materials + 4 image panels + footer/revision), optional Top-5 appendix, and 3-sheet Excel summary (Summary / Top-5 / Layer Placements). |
| GUI | Tkinter desktop window: inputs, top-N comparison list, live engineering-style preview, PDF + Excel export. |

---

## Install

Requires **Python 3.12** (also works on 3.11).

```bash
python3.12 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`tkinter` ships with the standard CPython installer. On some Linux distros:
`sudo apt install python3-tk`.

---

## Run

### Desktop GUI (default)

```bash
python -m pallet_stacking.main
```

Workflow: enter carton + pallet dimensions → press **Calculate** → click any
row in the **Top Solutions** list to inspect → **Preview Layout** for a
full-screen 4-panel engineering view → **Export PDF…** or **Export
Excel…** to save the report.

### Headless CLI

```bash
python -m pallet_stacking.main --cli \
  --case-l 400 --case-w 300 --case-h 250 --case-weight 8 --case-name "SKU-001" \
  --pallet-l 1200 --pallet-w 1000 --pallet-h 150 --max-height 1800 \
  --margin-front 10 --margin-back 10 --margin-left 10 --margin-right 10 \
  --pallet-weight 25 \
  --barcode-face L \
  --top-n 5 \
  --pdf report.pdf --excel summary.xlsx
```

Outputs the Top-N comparison as JSON on stdout and writes both files.

---

## Sample data

`examples/sample_data.json` carries a ready-to-use input set
(400 × 300 × 250 mm case, EUR pallet 1200 × 1000, max H 1800,
10 mm margins each side). Expected primary result with defaults:
**10 cases / layer × 6 layers = 60 cases**, ≈ 99 % area util,
≈ 83 % volume util.

---

## Project layout

```
pallet_stacking/
├── __init__.py            # global FACE_COLORS + LINE_STYLE constants
├── main.py                # CLI / GUI entry point
├── models/
│   ├── carton.py          # Carton (with barcode_face_axis)
│   ├── pallet.py          # Pallet (with reserved margins)
│   └── stacking_result.py # PlacedCarton, LayerPattern, StackingResult
├── core/
│   ├── geometry.py        # overlap / containment / barcode-visibility checks
│   ├── scoring.py         # multi-objective scoring formula
│   └── optimizer.py       # main search
├── render/
│   ├── top_view.py        # plan view + carton-orientation diagram
│   ├── side_view.py       # side + front elevations
│   ├── view3d.py          # orthographic isometric 3D (engineering style)
│   └── _common.py         # shared line / colour helpers
├── export/
│   ├── pdf_export.py      # single-page A4 (Cape Pack-style) PDF report
│   └── excel_export.py    # 3-sheet Excel summary
└── gui/
    └── main_window.py     # tkinter desktop UI
examples/
└── sample_data.json
requirements.txt
```

---

## Algorithm overview

1. Enumerate all **6 cube orientations** of the carton.
2. For each orientation build candidate layer patterns:
   * **normal** — uniform grid, all cartons identically oriented.
   * **rotated** — grid of 90°-rotated cartons.
   * **mixed** — block of normal cartons + filler strip of rotated cartons
     (split point chosen along whichever axis yields more cartons).
3. Optionally generate an **interlock** pair (layer A + 90°-rotated layer B,
   alternating) for stack stability.
4. Stack copies of the chosen layer up to `max_total_height`.
5. Tag every placed carton with `barcode_visible` (the barcode face touches
   a usable-area edge).
6. Score the result:
   ```
   score = total_cases * 1000
         + barcode_exposure * 100
         + area_utilization * 10
   ```
   (interlock adds a +0.5 tiebreaker for stability when scores tie.)
7. Return the **top N** ranked solutions.

---

## Drawing style

Per the project spec, every drawing follows **engineering / CAD** conventions:

* **Orthographic / isometric projection** — `matplotlib`'s `proj_type='ortho'`
  with `elev=30°, azim=-45°`. No perspective distortion; carton edges stay
  parallel.
* **Pallet outline** — solid, `linewidth = 1.8`.
* **Usable area outline** — dashed, `linewidth = 0.9`.
* **Reserved margin band** — light fill (`#ffe9e1` with red dotted border).
* **Carton outline** — solid, `linewidth = 0.35`.
* **Dimension labels** — auto-placed arrows with white text boxes.

Forbidden: realistic rendering, perspective camera, shadows, gradient
shading, photo-style materials.

---

## Future work hooks

The architecture leaves room for:

* `interlock_stacking` — additional brick / pinwheel / herringbone patterns
  (currently the simple A/B 90° interlock is supported).
* `mixed_carton` — multiple SKUs per pallet (the algorithm is layer-based
  so a multi-orientation per-layer search is a straight extension of
  `core/optimizer.py`).
* `container_loading` — outer container packing built on top of the pallet
  solver.

---

## Notes

* Units: **mm** for dimensions, **kg** for weight.
* Figures render at **200–220 DPI**; the PDF is sized for **A4** printing.
* The face-colour rule (Front = blue, Side = green, Top = orange) is defined
  once in `pallet_stacking/__init__.py` (`FACE_COLORS`) and is consistently
  applied by every module.
