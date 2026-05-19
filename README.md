# Pallet Stacking Tool

A Cape Pack–style desktop application that computes optimal carton-to-pallet
stacking patterns and produces a professional A4 PDF report.

All dimensions in **millimetres (mm)**.

---

## Features

| Area | Capabilities |
|------|--------------|
| Optimization | Tries every carton orientation, plus `normal` / `rotated` / `mixed` / `interlock` layer patterns. Multi-objective scoring: **maximise total cases** then **maximise barcode side-face exposure**. |
| Constraints | Pallet footprint, max stack height, configurable reserved margins (front/back/left/right). |
| Visualization | Single-carton 3D, top / side / front views, full 3D pallet — all high-DPI matplotlib. **Colour-coded faces** consistent across every figure: **Front = blue**, **Side = green**, **Top = orange**. |
| Reporting | A4 PDF with basic info, computed result, all diagrams, and a Top-5 comparison table. |
| GUI | Tkinter desktop window with inputs, top-N comparison list, live preview, and PDF export. |
| Extensibility | Modular layout (`optimizer.py`, `renderer.py`, `pdf_export.py`, `gui.py`, `main.py`). Hooks for future interlock variants, mixed-carton, container loading. |

---

## Install

Requires **Python 3.12**.

```bash
python3.12 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`tkinter` ships with the standard CPython installer. On some Linux
distributions install it separately: `sudo apt install python3-tk`.

---

## Run

### Desktop GUI (default)

```bash
python -m pallet_stacking.main
```

Enter the case dimensions / weight, pallet dimensions, max stack height,
and the four reserved margins; press **Calculate**. The top solution is
shown in the preview pane. Click any row in the **Top Solutions** list to
inspect a different candidate, **Preview Layout** for a full-screen view,
or **Export PDF…** to save the report.

### Headless CLI

Produces a JSON comparison of the top-N solutions on stdout and (optionally)
writes a PDF report:

```bash
python -m pallet_stacking.main --cli \
  --case-l 400 --case-w 300 --case-h 250 --case-weight 8 --case-name "SKU-001" \
  --pallet-l 1200 --pallet-w 1000 --pallet-h 150 --max-height 1800 \
  --margin-front 10 --margin-back 10 --margin-left 10 --margin-right 10 \
  --top-n 5 \
  --pdf report.pdf
```

---

## Sample data

`examples/sample_data.json` contains a ready-to-use input set:

| Field        | Value                |
|--------------|----------------------|
| Case         | 400 × 300 × 250 mm, 8 kg |
| Pallet       | 1200 × 1000 mm (EUR)    |
| Pallet height| 150 mm               |
| Max stack H  | 1800 mm              |
| Margins      | 10 mm each side      |

Expected primary result with the defaults above: **10 cases per layer × 6 layers = 60 cases**, ≈ 100 % area utilisation, ≈ 83 % volume utilisation.

---

## Project layout

```
pallet_stacking/
├── __init__.py          # global FACE_COLORS constants
├── optimizer.py         # core algorithm + data classes
├── renderer.py          # matplotlib 2D / 3D drawing
├── pdf_export.py        # reportlab A4 report
├── gui.py               # tkinter GUI
└── main.py              # CLI / GUI entry point
examples/
└── sample_data.json
requirements.txt
```

---

## Algorithm overview

For each of the 6 cube orientations of the carton (which axis points up,
and which way it is rotated):

1. Build candidate layer patterns:
   * **normal** – uniform grid, all cases identically oriented.
   * **rotated** – grid of 90°-rotated cases.
   * **mixed** – a block of normal cases plus a filler strip of rotated cases.
2. Optionally generate an **interlock** pair (layer A + 90°-rotated layer B,
   stacked alternately).
3. Stack copies of the chosen layer up to the max height limit.
4. Score the solution:
   `score = total_cases × (1 + barcode_weight × barcode_exposure)`
   (interlock receives a tiny tie-breaker bonus for stability).
5. Return the **top N** solutions.

The barcode-exposure proxy counts the fraction of cases on the layer
perimeter — these are the cases whose side faces (green) point outward
and are easiest to scan.

---

## Future work hooks

* `interlock_stacking` – more elaborate brick / pinwheel / herringbone patterns.
* `mixed_carton` – multiple SKUs per pallet.
* `container_loading` – 20'/40' container packing on top of the pallet solver.

---

## Notes

* All units are **mm** (millimetres).
* All figures render at high DPI (200) and the PDF is sized for **A4** printing.
* The face-colour rule (Front = blue, Side = green, Top = orange) is defined
  once in `pallet_stacking/__init__.py` and is used by every module.
