"""Professional A4 PDF report generation using reportlab."""
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


def _styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle(name="HeadingBig",
                            parent=base["Heading1"], fontSize=18,
                            textColor=colors.HexColor("#222")))
    base.add(ParagraphStyle(name="SectionTitle",
                            parent=base["Heading2"], fontSize=13,
                            textColor=colors.HexColor("#1f4e8e"),
                            spaceBefore=10, spaceAfter=4))
    return base


def _info_table(solution: StackingSolution, product_name: str,
                pallet_type: str, gross_weight: float):
    case = solution.case
    pallet = solution.pallet
    total_height = pallet.height + solution.layer_count * solution.case_dz

    data = [
        ["Product Name",  product_name],
        ["Pallet Type",   pallet_type],
        ["Case Size (LxWxH)", f"{case.length:.0f} × {case.width:.0f} × {case.height:.0f} mm"],
        ["Pallet Size (LxW)", f"{pallet.length:.0f} × {pallet.width:.0f} mm"],
        ["Total Height",  f"{total_height:.0f} mm"],
        ["Gross Weight",  f"{gross_weight:.2f} kg"],
    ]
    return _styled_table(data, col_widths=[55*mm, 100*mm])


def _results_table(solution: StackingSolution):
    data = [
        ["Cases per Layer",        f"{solution.cases_per_layer}"],
        ["Layers per Pallet",      f"{solution.layer_count}"],
        ["Total Cases",            f"{solution.total_cases}"],
        ["Layout",                 solution.layout_name],
        ["Area Utilization",       f"{solution.area_utilization*100:.2f} %"],
        ["Volume Utilization",     f"{solution.volume_utilization*100:.2f} %"],
        ["Barcode Exposure",       f"{solution.barcode_exposure*100:.2f} %"],
        ["Interlock Stacking",     "Yes" if solution.interlock else "No"],
    ]
    return _styled_table(data, col_widths=[55*mm, 100*mm])


def _styled_table(rows, col_widths):
    tbl = Table(rows, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e7eef7")),
        ("TEXTCOLOR",  (0, 0), (0, -1), colors.HexColor("#1f4e8e")),
        ("FONTNAME",   (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
    ]))
    return tbl


def _save_fig_as_png(fig, dpi=200) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    f.close()
    fig.savefig(f.name, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return f.name


def _legend_paragraph(styles):
    text = (
        '<font color="{f}">■</font> Front face '
        '&nbsp;&nbsp;<font color="{s}">■</font> Side face '
        '&nbsp;&nbsp;<font color="{t}">■</font> Top face'
    ).format(f=FACE_COLORS["front"], s=FACE_COLORS["side"], t=FACE_COLORS["top"])
    return Paragraph(text, styles["BodyText"])


def _comparison_table(solutions: List[StackingSolution]):
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
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
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
               pallet_type: str = "Standard",
               gross_weight: float = 0.0) -> str:
    """Render a full A4 PDF report. Returns the file path written."""

    styles = _styles()
    story = []

    story.append(Paragraph("Pallet Stacking Report", styles["HeadingBig"]))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        styles["BodyText"]))
    story.append(Spacer(1, 6))
    story.append(_legend_paragraph(styles))
    story.append(Spacer(1, 8))

    # --- 1. Basic info / results --------------------------------------
    story.append(Paragraph("1. Basic Information", styles["SectionTitle"]))
    story.append(_info_table(primary, product_name, pallet_type, gross_weight))
    story.append(Spacer(1, 8))

    story.append(Paragraph("2. Computed Result", styles["SectionTitle"]))
    story.append(_results_table(primary))
    story.append(Spacer(1, 10))

    # --- 2. Carton orientation diagram --------------------------------
    story.append(Paragraph("3. Carton Orientation", styles["SectionTitle"]))
    fig = plt.figure(figsize=(5.0, 4.0))
    ax = fig.add_subplot(111, projection="3d")
    renderer.draw_single_case_3d(ax, primary.case.length, primary.case.width,
                                 primary.case.height, title="Single Carton")
    img = _save_fig_as_png(fig)
    story.append(RLImage(img, width=140*mm, height=110*mm))
    story.append(Spacer(1, 6))

    # --- 3. Top / Side / Front view -----------------------------------
    story.append(PageBreak())
    story.append(Paragraph("4. Pallet Views", styles["SectionTitle"]))

    fig = plt.figure(figsize=(8.0, 4.0))
    ax = fig.add_subplot(111)
    renderer.draw_top_view(ax, primary, layer_index=0)
    img = _save_fig_as_png(fig)
    story.append(RLImage(img, width=170*mm, height=85*mm))
    story.append(Spacer(1, 6))

    fig = plt.figure(figsize=(8.0, 4.0))
    ax = fig.add_subplot(111)
    renderer.draw_side_view(ax, primary)
    img = _save_fig_as_png(fig)
    story.append(RLImage(img, width=170*mm, height=85*mm))
    story.append(Spacer(1, 6))

    fig = plt.figure(figsize=(8.0, 4.0))
    ax = fig.add_subplot(111)
    renderer.draw_front_view(ax, primary)
    img = _save_fig_as_png(fig)
    story.append(RLImage(img, width=170*mm, height=85*mm))
    story.append(Spacer(1, 6))

    # --- 4. 3D pallet view --------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("5. 3D Pallet View", styles["SectionTitle"]))
    fig = plt.figure(figsize=(8.0, 6.0))
    ax = fig.add_subplot(111, projection="3d")
    renderer.draw_pallet_3d(ax, primary)
    img = _save_fig_as_png(fig)
    story.append(RLImage(img, width=170*mm, height=130*mm))
    story.append(Spacer(1, 10))

    # --- 5. Top-N comparison ------------------------------------------
    if top_solutions:
        story.append(PageBreak())
        story.append(Paragraph("6. Top 5 Stacking Solutions",
                               styles["SectionTitle"]))
        story.append(_comparison_table(top_solutions))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "Solutions are ranked by a multi-objective score that "
            "maximizes total case count and rewards barcode side-face "
            "exposure for easier scanning.",
            styles["BodyText"]))

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=14*mm,  bottomMargin=14*mm,
        title="Pallet Stacking Report",
        author="Pallet Stacking Tool",
    )
    doc.build(story)
    return output_path
