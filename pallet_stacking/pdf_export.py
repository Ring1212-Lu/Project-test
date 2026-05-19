"""Cape Pack-style single-page A4 PDF report (with optional appendix).

Layout (single A4 portrait page):
    +----------------------------------------------------------+
    | Header info table     (Product / Cube%/Area% / counts)   |
    +----------------------------------------------------------+
    | Materials table       (Case / Pallet / Load: L W H N G)  |
    +----------------------------------------------------------+
    | Front view       |   3D pallet view                       |
    +----------------------------------------------------------+
    | Top view         |   Single carton 3D                     |
    +----------------------------------------------------------+
    | Footer notes (revision / approval / validity)             |
    +----------------------------------------------------------+

A second page (optional) carries the Top-5 comparison table.

Globally consistent face colours (defined in ``pallet_stacking.FACE_COLORS``)
are used on every figure: Front = blue, Side = green, Top = orange.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from typing import List, Optional

import matplotlib.pyplot as plt

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, PageBreak,
)

from .optimizer import StackingSolution, compare_solutions
from . import renderer
from . import FACE_COLORS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="ReportTitle", parent=s["Heading1"],
                         fontSize=13, alignment=0,
                         textColor=colors.HexColor("#1f4e8e"),
                         spaceAfter=4))
    s.add(ParagraphStyle(name="Small", parent=s["BodyText"],
                         fontSize=8, leading=10))
    s.add(ParagraphStyle(name="FooterNote", parent=s["BodyText"],
                         fontSize=8, leading=10,
                         textColor=colors.HexColor("#333")))
    return s


def _save_fig_as_png(fig, dpi: int = 220) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    f.close()
    fig.savefig(f.name, dpi=dpi, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    return f.name


# ---------------------------------------------------------------------------
# Header table (Cape Pack style — 4 columns, mixed label/value)
# ---------------------------------------------------------------------------

def _header_table(solution: StackingSolution,
                  product_name: str,
                  product_code: str,
                  datafile_name: str,
                  load_ref: str,
                  pallet_type: str,
                  date_str: str) -> Table:
    cube_used = solution.volume_utilization * 100.0
    area_used = solution.area_utilization * 100.0
    cases_per_layer = solution.cases_per_layer
    layers = solution.layer_count
    total_cases = solution.total_cases

    data = [
        ["Product Name",   product_name,                     date_str,        ""],
        ["Product Code",   product_code,                     f"{cases_per_layer}", "Case / Layer"],
        ["DataFile Name",  datafile_name,                    f"{layers}",     "Layers / Load"],
        ["Load Ref",       load_ref,                         f"{total_cases}","Case / Load"],
        ["Cube Used",      f"{cube_used:.1f} %",             "",              ""],
        ["Area Used",      f"{area_used:.1f} %",             "",              ""],
        ["Pallet type",    pallet_type,                      "",              ""],
    ]
    col_widths = [30*mm, 70*mm, 25*mm, 49*mm]
    tbl = Table(data, colWidths=col_widths, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("FONTNAME",   (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",   (3, 0), (3, -1), "Helvetica-Bold"),
        ("TEXTCOLOR",  (0, 0), (0, -1), colors.HexColor("#1f4e8e")),
        ("TEXTCOLOR",  (3, 0), (3, -1), colors.HexColor("#1f4e8e")),
        ("ALIGN",      (2, 0), (2, -1), "RIGHT"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("LINEBELOW",  (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("BOX",        (0, 0), (-1, -1), 0.5,  colors.HexColor("#888")),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# Materials table (Case / Pallet / Load × L W H Net Gross)
# ---------------------------------------------------------------------------

def _materials_table(solution: StackingSolution,
                     pallet_weight: float) -> Table:
    case = solution.case
    pallet = solution.pallet
    total_h = pallet.height + solution.layer_count * solution.case_dz
    case_net = case.weight
    case_gross = case.weight  # no per-case packaging adjustment
    load_net = solution.total_cases * case.weight
    load_gross = load_net + pallet_weight

    rows = [
        ["",         "Length",  "Width",   "Height",  "Net",            "Gross"],
        ["Case (OD)",
         f"{case.length:.1f}", f"{case.width:.1f}", f"{case.height:.1f}",
         f"{case_net:.3f}",   f"{case_gross:.3f} Kg"],
        ["Pallet",
         f"{pallet.length:.1f}", f"{pallet.width:.1f}", f"{pallet.height:.1f}",
         f"{pallet_weight:.3f}", f"{pallet_weight:.3f} Kg"],
        ["Load",
         f"{pallet.length:.1f}", f"{pallet.width:.1f}", f"{total_h:.1f}",
         f"{load_net:.3f}",     f"{load_gross:.3f} Kg"],
    ]
    col_widths = [28*mm, 28*mm, 28*mm, 28*mm, 28*mm, 34*mm]
    tbl = Table(rows, colWidths=col_widths, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",      (0, 1), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.HexColor("#1f4e8e")),
        ("ALIGN",         (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("LINEBELOW",     (0, 0), (-1, 0), 0.6, colors.HexColor("#1f4e8e")),
        ("GRID",          (0, 1), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#888")),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# Figures for the four image panels
# ---------------------------------------------------------------------------

def _fig_front(solution: StackingSolution) -> str:
    fig = plt.figure(figsize=(4.6, 4.0))
    ax = fig.add_subplot(111)
    renderer.draw_front_view(ax, solution)
    return _save_fig_as_png(fig)


def _fig_3d(solution: StackingSolution) -> str:
    fig = plt.figure(figsize=(4.6, 4.0))
    ax = fig.add_subplot(111, projection="3d")
    renderer.draw_pallet_3d(ax, solution)
    return _save_fig_as_png(fig)


def _fig_top(solution: StackingSolution) -> str:
    fig = plt.figure(figsize=(4.6, 4.0))
    ax = fig.add_subplot(111)
    renderer.draw_top_view(ax, solution, layer_index=0)
    return _save_fig_as_png(fig)


def _fig_single_case(solution: StackingSolution) -> str:
    fig = plt.figure(figsize=(4.6, 4.0))
    ax = fig.add_subplot(111, projection="3d")
    renderer.draw_single_case_3d(
        ax, solution.case.length, solution.case.width, solution.case.height,
        title=f"Carton {solution.case.length:.0f}×"
              f"{solution.case.width:.0f}×{solution.case.height:.0f}")
    return _save_fig_as_png(fig)


def _diagram_grid(solution: StackingSolution) -> Table:
    front_png = _fig_front(solution)
    pd3_png   = _fig_3d(solution)
    top_png   = _fig_top(solution)
    case_png  = _fig_single_case(solution)

    img_w = 85 * mm
    img_h = 65 * mm

    rows = [
        [RLImage(front_png, width=img_w, height=img_h),
         RLImage(pd3_png,   width=img_w, height=img_h)],
        [RLImage(top_png,   width=img_w, height=img_h),
         RLImage(case_png,  width=img_w, height=img_h)],
    ]
    tbl = Table(rows, colWidths=[img_w + 4*mm, img_w + 4*mm],
                rowHeights=[img_h + 4*mm, img_h + 4*mm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("ALIGN",  (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX",    (0, 0), (-1, -1), 0.5, colors.HexColor("#888")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# Footer notes
# ---------------------------------------------------------------------------

def _footer_paragraphs(styles, notes: List[str]):
    elems = []
    legend = (
        '<font color="{f}">■</font> Front face '
        '&nbsp;&nbsp;<font color="{s}">■</font> Side face '
        '&nbsp;&nbsp;<font color="{t}">■</font> Top face'
    ).format(f=FACE_COLORS["front"], s=FACE_COLORS["side"], t=FACE_COLORS["top"])
    elems.append(Paragraph(legend, styles["Small"]))
    elems.append(Spacer(1, 2))
    for i, n in enumerate(notes, start=1):
        elems.append(Paragraph(f"{i}. {n}", styles["FooterNote"]))
    return elems


# ---------------------------------------------------------------------------
# Optional appendix: top-5 comparison
# ---------------------------------------------------------------------------

def _comparison_table(solutions: List[StackingSolution]) -> Table:
    head = ["Rank", "Layout", "Cases/Layer", "Layers", "Total",
            "Area %", "Volume %", "Barcode %", "Interlock"]
    rows = [head]
    for r in compare_solutions(solutions):
        rows.append([
            r["rank"], r["layout"], r["cases_per_layer"], r["layers"],
            r["total_cases"], f'{r["area_util_%"]:.1f}',
            f'{r["volume_util_%"]:.1f}', f'{r["barcode_exposure_%"]:.1f}',
            "Yes" if r["interlock"] else "No",
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
               primary: StackingSolution,
               top_solutions: Optional[List[StackingSolution]] = None,
               product_name: str = "—",
               product_code: str = "—",
               datafile_name: str = "—",
               load_ref: str = "—",
               pallet_type: str = "Standard",
               pallet_weight: float = 0.0,
               date_str: Optional[str] = None,
               footer_notes: Optional[List[str]] = None,
               include_appendix: bool = True) -> str:
    """Render a Cape Pack-style single-page A4 PDF report.

    Args:
        output_path: where to write the PDF.
        primary: the chosen StackingSolution to report on.
        top_solutions: list of top-N candidates (used only on the appendix page).
        product_name / product_code / datafile_name / load_ref / pallet_type:
            free-form header strings.
        pallet_weight: weight of the empty pallet (kg) - used in the
            Materials table to compute Net/Gross for the Load row.
        date_str: header date string; defaults to today (YYYY/MM/DD).
        footer_notes: bullet notes (revision / approval / validity).
        include_appendix: if True, add a 2nd page with the Top-N comparison
            table.

    Returns: the absolute path that was written.
    """
    styles = _styles()
    if date_str is None:
        date_str = datetime.now().strftime("%Y/%m/%d")
    if footer_notes is None:
        footer_notes = [
            "Generated by the Pallet Stacking Tool.",
            "Dimensions in mm; weights in kg.",
            "Issued by: —",
            "Approved by: —",
            "Valid until: —",
        ]

    story = []

    story.append(Paragraph("Pallet Stacking Report", styles["ReportTitle"]))
    story.append(_header_table(
        primary,
        product_name=product_name,
        product_code=product_code,
        datafile_name=datafile_name,
        load_ref=load_ref,
        pallet_type=pallet_type,
        date_str=date_str))
    story.append(Spacer(1, 4))
    story.append(_materials_table(primary, pallet_weight=pallet_weight))
    story.append(Spacer(1, 4))
    story.append(_diagram_grid(primary))
    story.append(Spacer(1, 4))
    story.extend(_footer_paragraphs(styles, footer_notes))

    if include_appendix and top_solutions:
        story.append(PageBreak())
        story.append(Paragraph("Appendix — Top 5 Stacking Solutions",
                               styles["ReportTitle"]))
        story.append(Spacer(1, 4))
        story.append(_comparison_table(top_solutions))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "Solutions are ranked by a multi-objective score that "
            "maximizes total case count and rewards barcode side-face "
            "exposure for easier scanning.",
            styles["Small"]))

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=12*mm,  bottomMargin=12*mm,
        title="Pallet Stacking Report",
        author="Pallet Stacking Tool",
    )
    doc.build(story)
    return output_path
