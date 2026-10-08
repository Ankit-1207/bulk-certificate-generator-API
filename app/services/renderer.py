"""The single predefined certificate template, rendered to PDF with ReportLab."""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)

NAVY = colors.HexColor("#1F2A44")
GOLD = colors.HexColor("#B08D57")
CREAM = colors.HexColor("#FBF8F1")
GREY = colors.HexColor("#555555")


@dataclass(frozen=True)
class CertificateData:
    recipient_name: str
    title: str
    course_name: str
    issuer: str
    issue_date: date
    verification_code: str


def _fit_font_size(text: str, font: str, max_size: int, min_size: int, max_width: float) -> int:
    """Largest font size (<= max_size) at which text fits in max_width."""
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def render_certificate(data: CertificateData, output_path: Path) -> Path:
    """Render one certificate to output_path.

    Writes to a temp file and renames, so a crash never leaves a half-written PDF
    at the final path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")

    pdf = canvas.Canvas(str(tmp_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    pdf.setTitle(f"{data.title} - {data.recipient_name}")
    pdf.setAuthor(data.issuer)
    pdf.setSubject(data.course_name)

    center = PAGE_WIDTH / 2
    text_width = PAGE_WIDTH - 200

    # Background and double border.
    pdf.setFillColor(CREAM)
    pdf.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)
    pdf.setStrokeColor(NAVY)
    pdf.setLineWidth(8)
    pdf.rect(24, 24, PAGE_WIDTH - 48, PAGE_HEIGHT - 48)
    pdf.setStrokeColor(GOLD)
    pdf.setLineWidth(1.5)
    pdf.rect(40, 40, PAGE_WIDTH - 80, PAGE_HEIGHT - 80)

    # Title.
    title = data.title.upper()
    pdf.setFillColor(NAVY)
    pdf.setFont("Times-Bold", _fit_font_size(title, "Times-Bold", 38, 18, text_width))
    pdf.drawCentredString(center, PAGE_HEIGHT - 130, title)

    pdf.setStrokeColor(GOLD)
    pdf.setLineWidth(2)
    pdf.line(center - 120, PAGE_HEIGHT - 148, center + 120, PAGE_HEIGHT - 148)

    pdf.setFillColor(GREY)
    pdf.setFont("Helvetica", 15)
    pdf.drawCentredString(center, PAGE_HEIGHT - 195, "This is to certify that")

    # Recipient name: shrink to fit long names rather than overflowing the border.
    pdf.setFillColor(NAVY)
    name_size = _fit_font_size(data.recipient_name, "Times-BoldItalic", 46, 16, text_width)
    pdf.setFont("Times-BoldItalic", name_size)
    pdf.drawCentredString(center, PAGE_HEIGHT - 255, data.recipient_name)

    pdf.setFillColor(GREY)
    pdf.setFont("Helvetica", 15)
    pdf.drawCentredString(center, PAGE_HEIGHT - 305, "has successfully completed")

    pdf.setFillColor(NAVY)
    course_size = _fit_font_size(data.course_name, "Helvetica-Bold", 24, 12, text_width)
    pdf.setFont("Helvetica-Bold", course_size)
    pdf.drawCentredString(center, PAGE_HEIGHT - 345, data.course_name)

    # Footer: date on the left, issuer on the right.
    footer_y = 120
    pdf.setStrokeColor(NAVY)
    pdf.setLineWidth(1)
    for x in (PAGE_WIDTH * 0.25, PAGE_WIDTH * 0.75):
        pdf.line(x - 110, footer_y, x + 110, footer_y)

    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawCentredString(PAGE_WIDTH * 0.25, footer_y + 8, data.issue_date.strftime("%B %d, %Y"))
    issuer_size = _fit_font_size(data.issuer, "Helvetica-Bold", 13, 8, 220)
    pdf.setFont("Helvetica-Bold", issuer_size)
    pdf.drawCentredString(PAGE_WIDTH * 0.75, footer_y + 8, data.issuer)

    pdf.setFillColor(GREY)
    pdf.setFont("Helvetica", 10)
    pdf.drawCentredString(PAGE_WIDTH * 0.25, footer_y - 15, "Date of Issue")
    pdf.drawCentredString(PAGE_WIDTH * 0.75, footer_y - 15, "Issued By")

    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(center, 58, f"Certificate ID: {data.verification_code}")

    pdf.showPage()
    pdf.save()

    os.replace(tmp_path, output_path)
    return output_path
