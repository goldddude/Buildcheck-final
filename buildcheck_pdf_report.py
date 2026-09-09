from __future__ import annotations

import html
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Table, TableStyle


def build_pdf_report(payload: dict, selected_schemes: list[dict]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="BuildCheck Compliance Report",
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("BuildCheck Compliance Report", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Project: {payload['project']['project_name']}", styles["Normal"]))
    story.append(Paragraph(f"Planning Authority: {payload['authority_name']}", styles["Normal"]))
    story.append(Paragraph(f"Building Type: {payload['project']['building_type']}", styles["Normal"]))
    selected_scheme_names = [scheme["name"] for scheme in selected_schemes]
    story.append(Paragraph("Selected Maharashtra Schemes: " + (", ".join(selected_scheme_names) or "None selected"), styles["Normal"]))
    story.append(Spacer(1, 12))

    area = payload["area"]
    summary = [
        ["Compliance Score", f"{payload['compliance_score']}%"],
        ["Total Area", f"{area['total_area_m2']:.2f} m2"],
        ["Total Area", f"{area['total_area_ft2']:.2f} ft2"],
        ["Drawing Method", area["method"]],
    ]
    story.append(_table(summary, [52 * mm, 112 * mm]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Main Points Noticed", styles["Heading2"]))
    for point in payload["observations"]:
        story.append(Paragraph("- " + point, styles["Normal"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Selected Government Schemes", styles["Heading2"]))
    scheme_rows = [["Category", "Scheme", "Eligibility", "Key Benefits"]]
    for scheme in selected_schemes:
        scheme_rows.append([scheme["category"], scheme["name"], scheme["eligibility"], scheme["benefits"]])
    if len(scheme_rows) == 1:
        scheme_rows.append(["-", "No scheme selected", "-", "-"])
    story.append(_table(scheme_rows, [28 * mm, 38 * mm, 50 * mm, 48 * mm], header=True))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Improvements Needed", styles["Heading2"]))
    for item in payload["improvements"]:
        story.append(Paragraph("- " + item, styles["Normal"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Failed / Review Items", styles["Heading2"]))
    check_rows = [["Scope", "Status", "Required", "Suggested Fix"]]
    for check in payload["checks"]:
        if check["status"] != "Pass":
            check_rows.append([check["scope"], check["status"], check["required"], check["suggested_fix"]])
    if len(check_rows) == 1:
        check_rows.append(["No failed or review items", "Pass", "-", "No correction needed."])
    story.append(_table(check_rows, [38 * mm, 22 * mm, 45 * mm, 59 * mm], header=True))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Area Summary", styles["Heading2"]))
    area_rows = [["Area Item", "m2", "ft2", "Confidence"]]
    for item in area["items"][:40]:
        area_rows.append([item["label"], f"{item['area_m2']:.2f}", f"{item['area_ft2']:.2f}", item["confidence"]])
    if len(area_rows) == 1:
        area_rows.append(["No measurable area found", "0.00", "0.00", "Review"])
    story.append(_table(area_rows, [65 * mm, 28 * mm, 34 * mm, 30 * mm], header=True))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Accuracy Note", styles["Heading2"]))
    story.append(
        Paragraph(
            "This prototype uses CAD/vector geometry where available and seed compliance thresholds. "
            "Before statutory use, replace seed rules with verified clause records and map the official CAD layers.",
            styles["Normal"],
        )
    )

    doc.build(story)
    return buffer.getvalue()


def _table(rows: list[list[str]], widths: list[float], header: bool = False) -> Table:
    body_style = ParagraphStyle("Cell", fontName="Helvetica", fontSize=7, leading=8)
    header_style = ParagraphStyle("HeaderCell", fontName="Helvetica-Bold", fontSize=7, leading=8)
    wrapped = [
        [
            Paragraph(html.escape(str(cell)), header_style if header and row_index == 0 else body_style)
            for cell in row
        ]
        for row_index, row in enumerate(rows)
    ]
    table = Table(wrapped, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d8e1ee")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3fa")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    table.setStyle(TableStyle(style))
    return table
