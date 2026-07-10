from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import re
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo
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
        ]
    )
    bold = _first_existing(
        [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
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



def _parse_price(value: object) -> Decimal | None:
    """Parse a whole-tenge price from a user-entered string."""
    raw = str(value or "").strip()

    if not raw:
        return None

    digits = re.sub(r"[^\d]", "", raw)

    if not digits:
        return None

    return Decimal(digits)


def _parse_quantity(value: object) -> Decimal:
    """Parse quantity. Empty or invalid quantity means one unit."""
    raw = str(value or "").strip()

    if not raw:
        return Decimal(1)

    match = re.search(r"\d+(?:[.,]\d+)?", raw)

    if not match:
        return Decimal(1)

    quantity = Decimal(
        match.group(0).replace(",", ".")
    )

    if quantity < 0:
        return Decimal(1)

    return quantity


def _format_money(value: Decimal) -> str:
    return f"{int(value):,}".replace(",", " ") + " ₸"


def generate_act_pdf(
    items: list[Mapping[str, object]],
    act_number: str,
    act_info: Mapping[str, object] | None = None,
) -> bytes:
    regular_font, bold_font = register_fonts()

    act_info = act_info or {}
    sto = escape(str(act_info.get("sto") or "-"))
    master = escape(str(act_info.get("master") or "-"))
    car = escape(str(act_info.get("car") or "-"))

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
        fontSize=8.5,
        leading=10.5,
        textColor=colors.white,
    )

    category_style = ParagraphStyle(
        "Category",
        parent=cell_bold_style,
        fontSize=9.5,
        leading=12,
        textColor=GREEN,
    )

    summary_label_style = ParagraphStyle(
        "SummaryLabel",
        parent=cell_style,
        fontSize=9.5,
        leading=12,
        textColor=TEXT,
    )

    summary_value_style = ParagraphStyle(
        "SummaryValue",
        parent=summary_label_style,
        fontName=bold_font,
        alignment=TA_LEFT,
    )

    summary_total_style = ParagraphStyle(
        "SummaryTotal",
        parent=summary_value_style,
        fontSize=10.5,
        leading=13,
        textColor=GREEN,
    )

    marketplace_text_style = ParagraphStyle(
        "MarketplaceText",
        parent=cell_bold_style,
        fontSize=9.5,
        leading=13,
        textColor=TEXT,
        spaceAfter=4,
    )

    marketplace_link_style = ParagraphStyle(
        "MarketplaceLink",
        parent=cell_bold_style,
        fontSize=11.5,
        leading=14.5,
        textColor=GREEN,
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
            Paragraph("Позиция", header_style),
            Paragraph("Расположение", header_style),
            Paragraph("Кол-во", header_style),
            Paragraph("Цена, ₸", header_style),
        ]
    ]

    grouped_items: dict[tuple[str, str], list[Mapping[str, object]]] = {}

    for item in items:
        mode = str(item.get("mode") or "")
        type_name = "Автозапчасть" if mode == "parts" else "Услуга СТО"
        group_name = str(item.get("group") or "-")
        grouped_items.setdefault((type_name, group_name), []).append(item)

    category_rows: list[int] = []
    item_rows: list[int] = []
    item_number = 1
    alternating_index = 0

    parts_total = Decimal(0)
    services_total = Decimal(0)
    has_price = False
    has_parts = False

    for (type_name, group_name), group_items in grouped_items.items():
        category_rows.append(len(table_data))
        table_data.append(
            [
                Paragraph(escape(group_name.upper()), category_style),
                "",
                "",
                "",
                "",
                "",
            ]
        )

        for item in group_items:
            if str(item.get("mode") or "") == "parts":
                has_parts = True

            item_name = escape(str(item.get("item") or "-"))
            position = escape(str(item.get("position") or "-"))
            quantity = escape(str(item.get("quantity") or "-"))
            price_text = escape(str(item.get("price") or "-"))
            price_value = _parse_price(item.get("price"))
            quantity_value = _parse_quantity(item.get("quantity"))

            if price_value is not None:
                has_price = True
                line_total = quantity_value * price_value

                if str(item.get("mode") or "") == "parts":
                    parts_total += line_total
                else:
                    services_total += line_total

            item_rows.append(len(table_data))
            table_data.append(
                [
                    Paragraph(str(item_number), cell_style),
                    Paragraph(type_name, cell_style),
                    Paragraph(item_name, cell_bold_style),
                    Paragraph(position, cell_style),
                    Paragraph(quantity, cell_style),
                    Paragraph(price_text, cell_style),
                ]
            )

            item_number += 1
            alternating_index += 1

    table = Table(
        table_data,
        colWidths=[8 * mm, 31 * mm, 45 * mm, 43 * mm, 20 * mm, 29 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )

    table_style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.55, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]

    for row_index in category_rows:
        table_style_commands.extend(
            [
                ("SPAN", (0, row_index), (-1, row_index)),
                ("BACKGROUND", (0, row_index), (-1, row_index), SOFT_GREEN),
                ("LINEABOVE", (0, row_index), (-1, row_index), 0.8, GREEN),
                ("LINEBELOW", (0, row_index), (-1, row_index), 0.55, LINE),
                ("TOPPADDING", (0, row_index), (-1, row_index), 6),
                ("BOTTOMPADDING", (0, row_index), (-1, row_index), 6),
                ("NOSPLIT", (0, row_index), (-1, min(row_index + 1, len(table_data) - 1))),
            ]
        )

    for display_index, row_index in enumerate(item_rows):
        background = colors.white if display_index % 2 == 0 else SOFT_GREEN
        table_style_commands.append(
            ("BACKGROUND", (0, row_index), (-1, row_index), background)
        )

    table.setStyle(TableStyle(table_style_commands))

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
        ]
    )

    if has_price:
        grand_total = parts_total + services_total

        summary_data = [
            [
                Paragraph("ИТОГО (ОРИЕНТИРОВОЧНО)", header_style),
                "",
            ],
            [
                Paragraph("Запчасти", summary_label_style),
                Paragraph(_format_money(parts_total), summary_value_style),
            ],
            [
                Paragraph("Работа", summary_label_style),
                Paragraph(_format_money(services_total), summary_value_style),
            ],
            [
                Paragraph("<b>Общая сумма</b>", summary_total_style),
                Paragraph(_format_money(grand_total), summary_total_style),
            ],
        ]

        summary_table = Table(
            summary_data,
            colWidths=[116 * mm, 60 * mm],
            hAlign="LEFT",
        )
        summary_table.setStyle(
            TableStyle(
                [
                    ("SPAN", (0, 0), (-1, 0)),
                    ("BACKGROUND", (0, 0), (-1, 0), GREEN),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 3), (-1, 3), SOFT_GREEN),
                    ("BOX", (0, 0), (-1, -1), 0.65, LINE),
                    ("INNERGRID", (0, 1), (-1, -1), 0.55, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )

        story.extend(
            [
                Spacer(1, 5 * mm),
                summary_table,
            ]
        )

    if has_parts:
        marketplace_url = (
            "https://carcity.kz/category/avtozapcasti"
        )

        marketplace_box = Table(
            [
                [
                    [
                        Paragraph(
                            "Необходимые запасные части можно приобрести "
                            "на маркетплейсе Carcity.kz.",
                            marketplace_text_style,
                        ),
                        Paragraph(
                            f'<link href="{marketplace_url}" '
                            'color="#007C5A">'
                            "Перейти к каталогу автозапчастей →"
                            "</link>",
                            marketplace_link_style,
                        ),
                    ]
                ]
            ],
            colWidths=[176 * mm],
            hAlign="LEFT",
        )

        marketplace_box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), SOFT_GREEN),
                    ("BOX", (0, 0), (-1, -1), 0.65, LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )

        story.extend(
            [
                Spacer(1, 5 * mm),
                marketplace_box,
            ]
        )

    story.extend(
        [
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
