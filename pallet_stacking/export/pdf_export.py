"""Cape Pack-style single-page A4 PDF report (with optional appendix).

Page layout (4 zones, top-to-bottom):

    +--------------------------------------------------+
    | Zone 1 : Header table + Materials table          |
    +--------------+-------------+---------------------+
    | Zone 2 : Top View | 3D Isometric | Carton View   |
    +--------------+-------------+---------------------+
    | Zone 3 : Legend + Notes + Revision               |
    +--------------------------------------------------+

A second page (optional) holds the Top-5 comparison table.
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from typing import List, Optional

import matplotlib.pyplot as plt

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles  import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units   import mm
from reportlab.platypus    import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, PageBreak,
)

from ..core    import compare_solutions
from ..models  import StackingResult
from ..render  import (
    draw_top_view, draw_carton_orientation_view,
    draw_pallet_3d,
)
from ..       import FACE_COLORS


# ---------------------------------------------------------------------------
# Styles + helpers
# ---------------------------------------------------------------------------

def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="ReportTitle", parent=s["Heading1"],
                         fontSize=13, alignment=0, spaceAfter=4,
                         textColor=colors.HexColor("#1f4e8e")))
    s.add(ParagraphStyle(name="Small", parent=s["BodyText"],
                         fontSize=8, leading=10))
    s.add(ParagraphStyle(name="FooterNote", parent=s["BodyText"],
                         fontSize=8, leading=10,
                         textColor=colors.HexColor("#333")))
    return s


def _save_fig(fig, dpi: int = 220) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False); f.close()
    fig.savefig(f.name, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return f.name


# ---------------------------------------------------------------------------
# Zone 1 — Header + Materials
# ---------------------------------------------------------------------------

def _header_table(r: StackingResult, product_name, product_code,
                  datafile_name, load_ref, pallet_type, date_str) -> Table:
    cube  = r.volume_utilization * 100.0
    area  = r.area_utilization   * 100.0
    bc    = r.barcode_exposure   * 100.0
    data = [
        ["Product Name",   product_name,           date_str,           ""],
        ["Product Code",   product_code,           f"{r.cases_per_layer}", "Case / Layer"],
        ["DataFile Name",  datafile_name,          f"{r.layer_count}",     "Layers / Load"],
        ["Load Ref",       load_ref,               f"{r.total_cases}",     "Case / Load"],
        ["Cube Used",      f"{cube:.1f} %",        f"{bc:.1f} %",          "Barcode Exposure"],
        ["Area Used",      f"{area:.1f} %",        "",                     ""],
        ["Pallet type",    pallet_type,            "",                     ""],
    ]
    tbl = Table(data, colWidths=[30*mm, 70*mm, 25*mm, 49*mm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("FONTSIZE",  (0, 0), (-1, -1), 9),
        ("FONTNAME",  (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",  (3, 0), (3, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#1f4e8e")),
        ("TEXTCOLOR", (3, 0), (3, -1), colors.HexColor("#1f4e8e")),
        ("ALIGN",     (2, 0), (2, -1), "RIGHT"),
        ("VALIGN",    (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("BOX",       (0, 0), (-1, -1), 0.5,  colors.HexColor("#888")),
    ]))
    return tbl


def _materials_table(r: StackingResult, pallet_weight: float) -> Table:
    c = r.carton; p = r.pallet
    total_h = r.total_height()
    load_net = r.total_cases * c.weight
    load_gross = load_net + pallet_weight
    rows = [
        ["",          "Length",   "Width",   "Height",  "Net",          "Gross"],
        ["Case (OD)", f"{c.length:.1f}", f"{c.width:.1f}",  f"{c.height:.1f}",
         f"{c.weight:.3f}", f"{c.weight:.3f} Kg"],
        ["Pallet",    f"{p.length:.1f}", f"{p.width:.1f}",  f"{p.height:.1f}",
         f"{pallet_weight:.3f}", f"{pallet_weight:.3f} Kg"],
        ["Load",      f"{p.length:.1f}", f"{p.width:.1f}",  f"{total_h:.1f}",
         f"{load_net:.3f}", f"{load_gross:.3f} Kg"],
    ]
    tbl = Table(rows, colWidths=[28*mm, 28*mm, 28*mm, 28*mm, 28*mm, 34*mm],
                hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("FONTSIZE",  (0, 0), (-1, -1), 9),
        ("FONTNAME",  (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",  (0, 1), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f4e8e")),
        ("ALIGN",     (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN",    (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#1f4e8e")),
        ("GRID",      (0, 1), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("BOX",       (0, 0), (-1, -1), 0.5, colors.HexColor("#888")),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# Zones 2 & 3 — image grid
# ---------------------------------------------------------------------------

def _img(figfn, *args, **kwargs) -> str:
    fig = plt.figure(figsize=(4.6, 3.6))
    proj = kwargs.pop("_proj", None)
    if proj == "3d":
        ax = fig.add_subplot(111, projection="3d")
    else:
        ax = fig.add_subplot(111)
    figfn(ax, *args, **kwargs)
    return _save_fig(fig)


def _image_grid(r: StackingResult) -> Table:
    iso3d = _img(draw_pallet_3d, r, _proj="3d")
    top   = _img(draw_top_view, r, layer_index=0)
    ori   = _img(draw_carton_orientation_view, r)

    w = 58 * mm
    h = 62 * mm
    rows = [[
        RLImage(top,   width=w, height=h),
        RLImage(iso3d, width=w, height=h),
        RLImage(ori,   width=w, height=h),
    ]]
    tbl = Table(rows,
                colWidths=[w + 4*mm] * 3,
                rowHeights=[h + 4*mm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("ALIGN",     (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",    (0, 0), (-1, -1), "MIDDLE"),
        ("BOX",       (0, 0), (-1, -1), 0.5, colors.HexColor("#888")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# Zone 4 — Legend + notes
# ---------------------------------------------------------------------------

def _footer_paragraphs(styles, notes: List[str]):
    elems = []
    legend = (
        '<font color="{f}">■</font> Front face  '
        '<font color="{s}">■</font> Side face  '
        '<font color="{t}">■</font> Top face   '
        '|   <font color="#bb4a3a">▒</font> Reserved margin  '
        '<font color="#555555">- -</font> Usable area'
    ).format(f=FACE_COLORS["front"], s=FACE_COLORS["side"], t=FACE_COLORS["top"])
    elems.append(Paragraph(legend, styles["Small"]))
    elems.append(Spacer(1, 2))
    for i, n in enumerate(notes, start=1):
        elems.append(Paragraph(f"{i}. {n}", styles["FooterNote"]))
    return elems


# ---------------------------------------------------------------------------
# Appendix — Top-5 comparison
# ---------------------------------------------------------------------------

def _comparison_table(solutions: List[StackingResult]) -> Table:
    head = ["Rank", "Layout", "Cases/Layer", "Layers", "Total",
            "Area %", "Volume %", "Barcode %", "Interlock", "Score"]
    rows = [head]
    for r in compare_solutions(solutions):
        rows.append([
            r["rank"], r["layout"], r["cases_per_layer"], r["layer_count"],
            r["total_cases"], f'{r["area_util_%"]:.1f}',
            f'{r["volume_util_%"]:.1f}', f'{r["barcode_exposure_%"]:.1f}',
            "Yes" if r["interlock"] else "No",
            f'{r["score"]:.0f}',
        ])
    tbl = Table(rows, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e8e")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8.5),
        ("GRID",       (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.whitesmoke, colors.HexColor("#f3f6fb")]),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_pdf(output_path: str,
               primary: StackingResult,
               top_solutions: Optional[List[StackingResult]] = None,
               product_name: str = "—",
               product_code: str = "—",
               datafile_name: str = "—",
               load_ref: str = "—",
               pallet_type: str = "Standard",
               pallet_weight: float = 0.0,
               date_str: Optional[str] = None,
               revision: str = "Rev. 1.0",
               footer_notes: Optional[List[str]] = None,
               include_appendix: bool = True) -> str:
    styles = _styles()
    if date_str is None:
        date_str = datetime.now().strftime("%Y/%m/%d")
    if footer_notes is None:
        footer_notes = [
            "Generated by the Pallet Stacking Tool.",
            "Dimensions in millimetres; weights in kilograms.",
            "Drawings use orthographic isometric projection (engineering style).",
            f"Revision: {revision}",
            "Issued by: —    Approved by: —    Valid until: —",
        ]

    story = []
    story.append(Paragraph("Pallet Stacking Report", styles["ReportTitle"]))
    # Zone 1
    story.append(_header_table(primary, product_name, product_code,
                               datafile_name, load_ref, pallet_type, date_str))
    story.append(Spacer(1, 4))
    story.append(_materials_table(primary, pallet_weight=pallet_weight))
    story.append(Spacer(1, 6))
    # Zones 2 + 3
    story.append(_image_grid(primary))
    story.append(Spacer(1, 4))
    # Zone 4
    story.extend(_footer_paragraphs(styles, footer_notes))

    if include_appendix and top_solutions:
        story.append(PageBreak())
        story.append(Paragraph("Appendix — Top 5 Stacking Solutions",
                               styles["ReportTitle"]))
        story.append(Spacer(1, 4))
        story.append(_comparison_table(top_solutions))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "Score = total_cases × 1000 + barcode_exposure × 100 "
            "+ area_utilization × 10. Higher is better.",
            styles["Small"]))

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=12*mm,  bottomMargin=12*mm,
        title="Pallet Stacking Report",
        author="Pallet Stacking Tool",
    )
    doc.build(story)
    return output_path
