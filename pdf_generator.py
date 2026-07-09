from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Iterable, Mapping

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


GREEN = colors.HexColor("#007C5A")
ORANGE = colors.HexColor("#F9B041")
TEXT = colors.HexColor("#1D2523")
MUTED = colors.HexColor("#6B7280")
LINE = colors.HexColor("#DDE5E1")
SOFT_GREEN = colors.HexColor("#F1F8F5")
ALMATY_TZ = ZoneInfo("Asia/Almaty")

def _first_existing(paths: Iterable[str]) -> str | None:
    for value in paths:
        path = Path(value)
        if path.exists():
            return str(path)
    return None


def register_fonts() -> tuple[str, str]:
    regular = _first_existing(
        [
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
    )
    bold = _first_existing(
        [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
    )

    if not regular or not bold:
        raise RuntimeError(
            "Не найден системный шрифт с поддержкой кириллицы. "
            "На Windows ожидается Arial или Segoe UI."
        )

    pdfmetrics.registerFont(TTFont("CarcityRegular", regular))
    pdfmetrics.registerFont(TTFont("CarcityBold", bold))
    return "CarcityRegular", "CarcityBold"


def generate_act_pdf(
    items: list[Mapping[str, object]],
    act_number: str,
    act_info: Mapping[str, object] | None = None,
) -> bytes:
    regular_font, bold_font = register_fonts()

    act_info = act_info or {}

    def safe(value: object, fallback: str = "—") -> str:
        text = str(value or fallback)
        return escape(text)

    sto = safe(act_info.get("sto"))
    master = safe(act_info.get("master"))
    car = safe(act_info.get("car"))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=15 * mm,
        bottomMargin=16 * mm,
        title=f"Акт дефектовки {act_number}",
        author="CarcityPRO",
    )

    styles = getSampleStyleSheet()

    brand_style = ParagraphStyle(
        "Brand",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=13,
        leading=16,
        textColor=GREEN,
        alignment=TA_LEFT,
        spaceAfter=4,
    )

    title_style = ParagraphStyle(
        "ActTitle",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=20,
        leading=25,
        textColor=TEXT,
        alignment=TA_LEFT,
        spaceAfter=5,
    )

    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=9,
        leading=13,
        textColor=MUTED,
        alignment=TA_LEFT,
    )

    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=9,
        leading=12,
        textColor=TEXT,
    )

    cell_bold_style = ParagraphStyle(
        "CellBold",
        parent=cell_style,
        fontName=bold_font,
    )

    header_style = ParagraphStyle(
        "Header",
        parent=cell_bold_style,
        textColor=colors.white,
    )
    
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=8.5,
        leading=12,
        textColor=MUTED,
        alignment=TA_CENTER,
    )

    now = datetime.now(ALMATY_TZ)

    story = [
        Paragraph("CarcityPRO", brand_style),
        Paragraph("АКТ ДЕФЕКТОВКИ", title_style),
        Paragraph(
            f"Акт № {act_number}<br/>Дата формирования: {now:%d.%m.%Y %H:%M}",
            meta_style,
        ),
        Spacer(1, 8 * mm),
    ]

    table_data = [
        [
            Paragraph("№", header_style),
            Paragraph("Тип", header_style),
            Paragraph("Категория", header_style),
            Paragraph("Позиция", header_style),
            Paragraph("Расположение", header_style),
        ]
    ]

    for index, item in enumerate(items, start=1):
        mode = str(item.get("mode") or "")
        type_name = "Автозапчасть" if mode == "parts" else "Услуга СТО"
        group = safe(item.get("group"))
        item_name = safe(item.get("item"))
        position = safe(item.get("position"))

        table_data.append(
            [
                Paragraph(str(index), cell_style),
                Paragraph(type_name, cell_style),
                Paragraph(group, cell_style),
                Paragraph(item_name, cell_bold_style),
                Paragraph(position, cell_style),
            ]
        )

    table = Table(
        table_data,
        colWidths=[10 * mm, 28 * mm, 42 * mm, 61 * mm, 35 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )

    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GREEN),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.55, LINE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT_GREEN]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    total_style = ParagraphStyle(
        "Total",
        parent=cell_style,
        fontName=bold_font,
        fontSize=10.5,
        textColor=GREEN,
    )

    story.extend(
        [
            table,
            Spacer(1, 7 * mm),
            Paragraph(
                f"<b>Всего выявлено позиций: {len(items)}</b>",
                total_style,
            ),
            Spacer(1, 15 * mm),
            Table(
                [
                    [
                        Paragraph(f"<b>СТО:</b> {sto}", cell_style),
                        Paragraph(f"<b>Мастер:</b> {master}", cell_style),
                    ],
                    [
                        Paragraph(f"<b>Автомобиль:</b> {car}", cell_style),
                        Paragraph("Подпись: _______________________________", cell_style),
                    ],
                ],
                colWidths=[88 * mm, 88 * mm],
                rowHeights=[12 * mm, 12 * mm],
            ),
            Spacer(1, 9 * mm),
            Paragraph(
                "Документ сформирован автоматически в CarcityPRO.",
                footer_style,
            ),
        ]
    )

    def draw_page(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(ORANGE)
        canvas.setLineWidth(2)
        canvas.line(
            document.leftMargin,
            10 * mm,
            A4[0] - document.rightMargin,
            10 * mm,
        )
        canvas.setFont(regular_font, 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(
            A4[0] - document.rightMargin,
            6.5 * mm,
            f"Страница {document.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()
