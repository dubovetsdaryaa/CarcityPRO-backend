from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


GREEN = "007C5A"
WHITE = "FFFFFF"
DARK = "1D2523"
LINE = "DDE5E1"


def _column_name(index: int) -> str:
    value = index
    result = ""

    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result

    return result


def _cell_xml(row: int, column: int, value: Any, style: int = 0) -> str:
    reference = f"{_column_name(column)}{row}"
    style_attr = f' s="{style}"' if style else ""
    text = "" if value is None else str(value)

    return (
        f'<c r="{reference}" t="inlineStr"{style_attr}>'
        f'<is><t xml:space="preserve">{escape(text)}</t></is>'
        f"</c>"
    )


def _sheet_xml(
    rows: list[list[Any]],
    widths: list[float],
) -> str:
    row_xml = []

    for row_index, values in enumerate(rows, start=1):
        style = 1 if row_index == 1 else 0
        cells = "".join(
            _cell_xml(
                row=row_index,
                column=column_index,
                value=value,
                style=style,
            )
            for column_index, value in enumerate(values, start=1)
        )
        row_xml.append(f'<row r="{row_index}">{cells}</row>')

    columns_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, start=1)
    )

    max_column = _column_name(max(len(rows[0]) if rows else 1, 1))
    max_row = max(len(rows), 1)

    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:{max_column}{max_row}"/>
  <sheetViews>
    <sheetView workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
    </sheetView>
  </sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>{columns_xml}</cols>
  <sheetData>{''.join(row_xml)}</sheetData>
  <autoFilter ref="A1:{max_column}{max_row}"/>
</worksheet>'''


def build_acts_xlsx(
    acts: list[dict[str, Any]],
    *,
    almaty_tz,
) -> bytes:
    acts_rows: list[list[Any]] = [
        [
            "Номер акта",
            "Дата",
            "Telegram ID",
            "Имя пользователя",
            "Username",
            "СТО",
            "Мастер",
            "Автомобиль",
            "Количество позиций",
        ]
    ]

    positions_rows: list[list[Any]] = [
        [
            "Номер акта",
            "№ позиции",
            "Тип",
            "Группа",
            "Позиция",
            "Расположение",
            "Кол-во",
            "Цена",
        ]
    ]

    for act in acts:
        created_at = act.get("created_at")

        if created_at is not None:
            created_text = created_at.astimezone(almaty_tz).strftime(
                "%d.%m.%Y %H:%M"
            )
        else:
            created_text = ""

        full_name = " ".join(
            part
            for part in [
                str(act.get("first_name") or "").strip(),
                str(act.get("last_name") or "").strip(),
            ]
            if part
        )

        username = str(act.get("username") or "").strip()
        username = f"@{username}" if username else ""

        acts_rows.append(
            [
                act.get("act_number") or "",
                created_text,
                act.get("telegram_id") or "",
                full_name,
                username,
                act.get("sto") or "",
                act.get("master") or "",
                act.get("car") or "",
                act.get("items_count") or 0,
            ]
        )

        items = act.get("items") or []

        for item_index, item in enumerate(items, start=1):
            mode = str(item.get("mode") or "")
            type_name = (
                "Автозапчасть"
                if mode == "parts"
                else "Услуга СТО"
            )

            positions_rows.append(
                [
                    act.get("act_number") or "",
                    item_index,
                    type_name,
                    item.get("group") or "",
                    item.get("item") or "",
                    item.get("position") or "",
                    item.get("quantity") or "",
                    item.get("price") or "",
                ]
            )

    workbook_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Акты" sheetId="1" r:id="rId1"/>
    <sheet name="Позиции" sheetId="2" r:id="rId2"/>
  </sheets>
</workbook>'''

    workbook_rels_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
                Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
                Target="worksheets/sheet2.xml"/>
  <Relationship Id="rId3"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
                Target="styles.xml"/>
</Relationships>'''

    root_rels_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
                Target="xl/workbook.xml"/>
  <Relationship Id="rId2"
                Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
                Target="docProps/core.xml"/>
  <Relationship Id="rId3"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties"
                Target="docProps/app.xml"/>
</Relationships>'''

    content_types_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels"
           ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml"
           ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml"
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml"
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/worksheets/sheet2.xml"
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml"
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml"
            ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml"
            ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''

    styles_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font>
      <sz val="11"/>
      <color rgb="FF{DARK}"/>
      <name val="Arial"/>
    </font>
    <font>
      <b/>
      <sz val="11"/>
      <color rgb="FF{WHITE}"/>
      <name val="Arial"/>
    </font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill>
      <patternFill patternType="solid">
        <fgColor rgb="FF{GREEN}"/>
        <bgColor indexed="64"/>
      </patternFill>
    </fill>
  </fills>
  <borders count="2">
    <border>
      <left/><right/><top/><bottom/><diagonal/>
    </border>
    <border>
      <left style="thin"><color rgb="FF{LINE}"/></left>
      <right style="thin"><color rgb="FF{LINE}"/></right>
      <top style="thin"><color rgb="FF{LINE}"/></top>
      <bottom style="thin"><color rgb="FF{LINE}"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0"
        fontId="0"
        fillId="0"
        borderId="1"
        xfId="0"
        applyBorder="1"
        applyAlignment="1">
      <alignment vertical="top" wrapText="1"/>
    </xf>
    <xf numFmtId="0"
        fontId="1"
        fillId="2"
        borderId="1"
        xfId="0"
        applyFont="1"
        applyFill="1"
        applyBorder="1"
        applyAlignment="1">
      <alignment horizontal="center" vertical="center" wrapText="1"/>
    </xf>
  </cellXfs>
  <cellStyles count="1">
    <cellStyle name="Normal" xfId="0" builtinId="0"/>
  </cellStyles>
</styleSheet>'''

    core_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
    xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:dcmitype="http://purl.org/dc/dcmitype/"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>CarcityPRO — экспорт актов</dc:title>
  <dc:creator>CarcityPRO</dc:creator>
</cp:coreProperties>'''

    app_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>CarcityPRO</Application>
  <TitlesOfParts>
    <vt:vector size="2" baseType="lpstr">
      <vt:lpstr>Акты</vt:lpstr>
      <vt:lpstr>Позиции</vt:lpstr>
    </vt:vector>
  </TitlesOfParts>
</Properties>'''

    sheet1_xml = _sheet_xml(
        acts_rows,
        widths=[28, 19, 17, 24, 22, 28, 22, 30, 20],
    )
    sheet2_xml = _sheet_xml(
        positions_rows,
        widths=[28, 14, 18, 28, 35, 25, 14, 18],
    )

    buffer = BytesIO()

    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", root_rels_xml)
        archive.writestr("docProps/core.xml", core_xml)
        archive.writestr("docProps/app.xml", app_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet1_xml)
        archive.writestr("xl/worksheets/sheet2.xml", sheet2_xml)

    return buffer.getvalue()
