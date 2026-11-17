"""Разбор розничного прайс-листа VILPE (xlsx) для сервиса «База».

Порт tools/price_extract.py сайта vilpe-roofing.ru: листы прайса, шапка в
первых строках, блоки «название + варианты цвета», цена в рублях за шт.
Возвращает строки с sheet/row/code/price — по ним работает site_price_mapping.
"""
import re

from openpyxl import load_workbook

TYPE_HINT = re.compile(
    r"проходн|вентил|выход|дефлектор|аэратор|люк|реш[её]тк|фланец|сетк|вентилятор|воронк|"
    r"уплотнител|примыкан|адаптер|патруб|заглушк|кожух|окантовк|лист|клапан|короб|"
    r"кронштейн|сверл|насадк|шуруп|саморез|шайб|дюбел|креплени|комплект|монитор|регулятор|"
    r"воздуховод|труб|сборник|кольцо|вытяжк|пленк|клей|герметик|лент", re.I)
SERIES_HINT = re.compile(
    r"\b(FLOW|XL|XXL|PIIPPU|ROOFSEAL|RETROFIT|VAPOUR|SOLAR|ALIPAI|ALPAI|CROCO|KLA|POWER|"
    r"MULTIROOF|UNIROOF|WIVE|VELCO|ROSS|ECo|SAVO|dCH|dIH|dGH|dRH|eCH|eIH|eGH|eRH|"
    r"CH-|IH-|GH-|RH-|PH-|HUOPA|MUOTOKATE|PELTI|CLASSIC|TIILI|UNITILE|HELMI|HARJA|DECRA|"
    r"IO|FR|KM|KP|RHS|SENSE|KREDO|HYYGGE|NOVA|EVO|ACOUDEC|FLEX|FELT)\b", re.I)
NOTE_START = re.compile(r"^(для\s|не\s|или\s|без\s|с\s|со\s|c\s|\(|\*|—|-|\d+\s*шт)", re.I)

CYR2LAT = {"А": "A", "В": "B", "С": "C", "Е": "E", "К": "K",
           "М": "M", "Н": "H", "О": "O", "Р": "P", "Т": "T", "Х": "X"}


def norm_code(value):
    if value is None:
        return ""
    s = str(value).strip()
    s = s.replace("В", "B").replace("С", "C").replace("А", "A")
    s = re.sub(r"\s+", "", s)
    return s.upper()


def _latin(s):
    for k, v in CYR2LAT.items():
        s = s.replace(k, v)
    return s


def looks_like_name(text):
    t = (text or "").strip()
    if not t or NOTE_START.match(t):
        return False
    lat = _latin(t)
    if SERIES_HINT.search(lat) and re.search(r"\d", t):
        return True
    if t[0].islower():
        return False
    return bool(TYPE_HINT.search(t))


def _find_header(ws):
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=12), 1):
        cells = ["" if c.value is None else str(c.value) for c in row]
        code_idx = price_idx = colour_idx = name_idx = None
        for j, cell in enumerate(cells):
            low = cell.lower()
            if name_idx is None and "наименован" in low:
                name_idx = j
            if code_idx is None and "код" in low:
                code_idx = j
            if price_idx is None and "цена" in low and "руб" in low:
                price_idx = j
            if colour_idx is None and ("цвет" in low or "длина" in low):
                colour_idx = j
        if code_idx is not None and price_idx is not None:
            return i, (name_idx or 0), code_idx, price_idx, colour_idx
    return None


def parse(path):
    """xlsx -> [{sheet,row,section,name,colour,code,price}] с числовой ценой > 0."""
    wb = load_workbook(path, data_only=True)
    rows = []
    for ws in wb.worksheets:
        hdr = _find_header(ws)
        if not hdr:
            continue
        hrow, ni, ci, pi, oi = hdr
        name = ""
        section = ""
        block_kind = None
        prev_code = None
        for i in range(hrow + 1, ws.max_row + 1):
            acell = ws.cell(row=i, column=ni + 1)
            aval = acell.value
            atxt = str(aval).strip() if aval is not None else ""
            if "\n" in atxt:
                atxt = atxt.split("\n")[0].strip()
            parts = [p.strip() for p in re.split(r"\s{3,}", atxt) if p.strip()]
            if len(parts) > 1:
                keep = []
                for part in parts[:3]:
                    if len(part) > 60:
                        break
                    keep.append(part)
                atxt = " ".join(keep or parts[:1])
            atxt = atxt.strip()

            font = acell.font
            bold = bool(font and font.bold)
            size = float(font.sz or 10) if font is not None else 10.0
            code = norm_code(ws.cell(row=i, column=ci + 1).value)
            price = ws.cell(row=i, column=pi + 1).value
            colour = ""
            if oi is not None:
                cv = ws.cell(row=i, column=oi + 1).value
                colour = str(cv).strip() if cv is not None else ""

            if atxt and not code and bold and size >= 11:
                section = atxt
                name = ""
                block_kind = None
                prev_code = None
                continue

            block_start = prev_code is None
            if atxt:
                if not looks_like_name(atxt):
                    pass
                elif block_start:
                    block_kind = "bold" if (bold and size <= 10.5) else "plain"
                    name = atxt
                elif block_kind == "plain":
                    name = atxt
                elif bold and 9.5 <= size <= 10.5:
                    name = atxt
                elif _latin(atxt) != _latin(name) and re.search(r"\d{2,4}", atxt) and SERIES_HINT.search(_latin(atxt)):
                    name = atxt

            if not code or not isinstance(price, (int, float)) or price <= 0:
                prev_code = code or None
                continue
            rows.append({
                "sheet": ws.title,
                "row": i,
                "section": section,
                "name": name,
                "colour": colour,
                "code": code,
                "price": float(price),
            })
            prev_code = code
    wb.close()
    return rows
