"""Разбор прайс-листов: .doc/.docx/.xls/.xlsx/.csv.

Возвращает колонки с типами цен и строки с значениями.
"""
import csv
import io
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from lxml import html as lxml_html

PRICE_WORDS = ("цена", "стоим", "руб", "опт", "розн", "вход", "закуп", "мрц", "прайс")
NAME_WORDS = ("наимен", "материал", "товар", "продукц", "позиц", "описан", "номенклат")
SKU_WORDS = ("артикул", "код", "sku", "номер")
UNIT_WORDS = ("ед.", "ед ", "единиц", "ед.изм", "ед. изм", "изм.")
EXTRA_WORDS = ("гост", "ту ", "ту.", "област", "примен", "примеч", "норм", "документ", "страна", "бренд", "марка")


def clean_text(value):
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def parse_price(value):
    """'2 8-00' -> 28.0, '29 -5 0' -> 29.5, '179,00' -> 179.0."""
    s = clean_text(value).lower()
    if not s:
        return None
    if "запрос" in s or s in ("-", "—", "нет", "нет цены"):
        return None
    for word in ("руб.", "руб", "р.", "₽", "с ндс", "ндс", "в т.ч."):
        s = s.replace(word, "")
    s = s.strip()
    if re.search(r"[a-zа-я]", s):
        return None
    s = re.sub(r"\s+", "", s)
    if not s:
        return None
    m = re.fullmatch(r"(\d+)-(\d{2})", s)
    if m:
        return round(int(m.group(1)) + int(m.group(2)) / 100.0, 2)
    m = re.fullmatch(r"(\d+)[.,](\d{1,2})", s)
    if m:
        return round(float(m.group(1) + "." + m.group(2)), 2)
    m = re.fullmatch(r"\d+", s)
    if m:
        return float(s)
    return None


def _parse_volume(header):
    h = (header or "").lower()
    value = None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*тыс", h)
    if m:
        value = float(m.group(1).replace(",", ".")) * 1000
    else:
        m = re.search(r"(\d{3,})", h)
        if m:
            value = float(m.group(1))
    if value is None:
        return None, None
    if "более" in h or "свыше" in h or "от " in h:
        return value, None
    if "до " in h or "менее" in h:
        return None, value
    return None, None


def _parse_unit(header):
    h = (header or "").lower()
    if "м2" in h or "м²" in h or "кв.м" in h:
        return "м2"
    if "п.м" in h or "пог.м" in h:
        return "п.м."
    if "шт" in h:
        return "шт"
    if "кг" in h:
        return "кг"
    if "т " in h or h.endswith(" т"):
        return "т"
    return None


def detect_price_type(header):
    h = (header or "").lower()
    if "мрц" in h:
        return "mrc"
    if "розн" in h or "rrc" in h:
        return "retail"
    return "input"


def _has_any(text, words):
    return any(w in (text or "").lower() for w in words)


def _table_grid(table):
    """Развернуть таблицу с colspan/rowspan в прямоугольную сетку строк."""
    raw_rows = []
    for tr in table.xpath(".//tr"):
        cells = []
        for cell in tr.xpath("./td|./th"):
            try:
                cs = max(1, int(cell.get("colspan") or 1))
            except ValueError:
                cs = 1
            try:
                rs = max(1, int(cell.get("rowspan") or 1))
            except ValueError:
                rs = 1
            cells.append((clean_text(" ".join(cell.itertext())), cs, rs))
        if cells:
            raw_rows.append(cells)

    grid = []
    for r, cells in enumerate(raw_rows):
        while len(grid) <= r:
            grid.append([])
        c = 0
        for text, cs, rs in cells:
            while c < len(grid[r]) and grid[r][c] is not None:
                c += 1
            for dr in range(rs):
                rr = r + dr
                while len(grid) <= rr:
                    grid.append([])
                for dc in range(cs):
                    cc = c + dc
                    while len(grid[rr]) <= cc:
                        grid[rr].append(None)
                    if grid[rr][cc] is None:
                        grid[rr][cc] = text
            c += cs

    width = max((len(row) for row in grid), default=0)
    for row in grid:
        row.extend([None] * (width - len(row)))
    return grid


def _row_texts(row):
    return [clean_text(v) for v in row if clean_text(v)]


def _is_section(row):
    texts = _row_texts(row)
    if len(texts) < 1:
        return False
    if any(parse_price(v) is not None for v in texts):
        return False
    # одна и та же надпись (colspan) либо короткая строка без цен
    return len(set(texts)) == 1 and len(texts[0]) > 2


def _groups(grid, header_rows):
    width = len(grid[0]) if grid else 0
    header_parts = [[] for _ in range(width)]
    for r in header_rows:
        row = grid[r]
        for c, value in enumerate(row):
            text = clean_text(value)
            if not text:
                continue
            if header_parts[c] and header_parts[c][-1] == text:
                continue
            header_parts[c].append(text)
    headers = [" / ".join(parts) for parts in header_parts]
    groups = []
    start = 0
    for c in range(1, width + 1):
        if c == width or headers[c] != headers[start]:
            groups.append({"start": start, "end": c - 1, "header": headers[start]})
            start = c
    return groups


def extract_from_grid(grid, source="html"):
    grid = [row for row in grid if any(clean_text(v) for v in row)]
    if not grid:
        raise ValueError("Пустая таблица")

    first_data = None
    for i, row in enumerate(grid):
        texts = _row_texts(row)
        if len(texts) < 2 or _is_section(row):
            continue
        numeric = sum(1 for v in texts if parse_price(v) is not None)
        if numeric >= 1 and i > 0:
            first_data = i
            break
    if first_data is None:
        first_data = 1

    header_rows = [i for i in range(first_data) if not _is_section(grid[i])]
    if not header_rows:
        header_rows = [max(0, first_data - 1)]

    groups = _groups(grid, header_rows)
    if not groups:
        raise ValueError("Не удалось определить колонки")

    name_group = None
    sku_group = None
    unit_group = None
    price_groups = []
    extra_groups = []
    for g in groups:
        h = g["header"].lower()
        if name_group is None and _has_any(h, NAME_WORDS):
            name_group = g
        elif sku_group is None and _has_any(h, SKU_WORDS):
            sku_group = g
        elif unit_group is None and _has_any(h, UNIT_WORDS):
            unit_group = g
        elif _has_any(h, PRICE_WORDS):
            price_groups.append(g)
        else:
            extra_groups.append(g)

    if name_group is None:
        name_group = groups[0]
    if not price_groups:
        # резерв: колонки, где в данных есть числа
        for g in groups:
            if g is name_group:
                continue
            vals = []
            for row in grid[first_data:]:
                for c in range(g["start"], g["end"] + 1):
                    p = parse_price(row[c])
                    if p is not None:
                        vals.append(p)
                        break
            if len(vals) >= max(1, len(grid[first_data:]) // 2):
                price_groups.append(g)
    if not price_groups:
        raise ValueError("В таблице не найдены колонки с ценами")

    columns = []
    for g in price_groups:
        header = g["header"]
        ptype = detect_price_type(header)
        vmin, vmax = _parse_volume(header)
        columns.append({
            "col_index": g["start"],
            "header": header,
            "price_type": ptype,
            "markup_pct": 0,
            "is_base": False,
            "volume_min": vmin,
            "volume_max": vmax,
            "currency": "RUB",
            "unit": _parse_unit(header),
        })

    def _pref(col):
        h = col["header"].lower()
        score = 0
        if col["price_type"] == "input":
            score += 10
        if any(w in h for w in ("опт", "вход", "закуп")):
            score += 5
        if "до 5" in h:
            score += 3
        if "цена" in h:
            score += 1
        return score

    base = max(columns, key=_pref)
    base["is_base"] = True

    def _cell_value(row, group, prefer="first"):
        values = []
        for c in range(group["start"], group["end"] + 1):
            v = clean_text(row[c])
            if v:
                values.append(v)
        if not values:
            return ""
        return values[-1] if prefer == "last" else values[0]

    rows = []
    section = ""
    for r in grid[:first_data]:
        if _is_section(r):
            section = _row_texts(r)[0][:160]
    row_num = 0
    for r in grid[first_data:]:
        if _is_section(r):
            section = _row_texts(r)[0][:160]
            continue
        name = _cell_value(r, name_group)
        if not name:
            continue
        if _has_any(name, ("наимен", "материал", "цена")) and not any(
            parse_price(_cell_value(r, g)) is not None for g in price_groups
        ):
            continue

        prices = {}
        for g in price_groups:
            value = None
            for c in range(g["start"], g["end"] + 1):
                p = parse_price(r[c])
                if p is not None:
                    value = p
                    break
            prices[str(g["start"])] = value

        if all(v is None for v in prices.values()):
            # строка-подзаголовок/раздел
            if len(_row_texts(r)) <= 2:
                section = name[:160]
                continue

        raw_extra = {}
        for g in extra_groups:
            val = _cell_value(r, g, prefer="last")
            if val:
                raw_extra[g["header"] or ("колонка %d" % (g["start"] + 1))] = val

        row_num += 1
        rows.append({
            "row_num": row_num,
            "raw_name": name,
            "raw_sku": _cell_value(r, sku_group) if sku_group else None,
            "raw_unit": _cell_value(r, unit_group) if unit_group else None,
            "prices": prices,
            "raw_extra": raw_extra,
            "raw_row": [clean_text(v) for v in r if clean_text(v)][:60],
            "section": section,
        })

    if not rows:
        raise ValueError("В таблице не найдены строки товаров")

    return {
        "columns": columns,
        "rows": rows,
        "meta": {
            "source": source,
            "name_column": name_group["header"],
            "extra_columns": [g["header"] for g in extra_groups if g["header"]],
            "sections": sorted({r["section"] for r in rows if r["section"] and len(r["section"]) <= 80}),
            "first_data_row": first_data,
        },
    }


def _find_soffice():
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert_to_html(path):
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice не установлен (нужен для .doc/.docx)")
    outdir = Path(tempfile.mkdtemp(prefix="price_html_"))
    profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    cmd = [
        soffice, "--headless", "--norestore", "--nolockcheck",
        "-env:UserInstallation=file://%s" % profile.as_posix(),
        "--convert-to", "html", "--outdir", str(outdir), str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError("LibreOffice: " + proc.stderr.decode("utf-8", "replace")[:500])
    files = sorted(outdir.glob("*.html"))
    if not files:
        raise RuntimeError("LibreOffice не создал HTML")
    return files[0]


def _convert_to_xlsx(path):
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice не установлен (нужен для .xls)")
    outdir = Path(tempfile.mkdtemp(prefix="price_xlsx_"))
    profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    cmd = [
        soffice, "--headless", "--norestore", "--nolockcheck",
        "-env:UserInstallation=file://%s" % profile.as_posix(),
        "--convert-to", "xlsx", "--outdir", str(outdir), str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError("LibreOffice: " + proc.stderr.decode("utf-8", "replace")[:500])
    files = sorted(outdir.glob("*.xlsx"))
    if not files:
        raise RuntimeError("LibreOffice не создал XLSX")
    return files[0]


def _extra_rows(table):
    """Строки из дополнительных таблиц (без цен) — чтобы не терять позиции."""
    rows = []
    seen = set()
    for r in _table_grid(table):
        texts = [clean_text(v) for v in r if clean_text(v)]
        if len(texts) < 2:
            continue
        name = texts[0]
        if not re.search(r"[а-яa-z]", name, re.IGNORECASE):
            continue
        if name.lower().strip() in ("по запросу", "цена", "материал", "наименование"):
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        rows.append({
            "row_num": 0,
            "raw_name": name,
            "raw_sku": None,
            "raw_unit": None,
            "prices": {},
            "raw_extra": {"Доп. таблица": " / ".join(texts[1:])[:500]},
            "raw_row": texts[:60],
            "section": "",
        })
    return rows


def parse_html(path):
    data = Path(path).read_bytes()
    doc = lxml_html.fromstring(data)
    tables = doc.xpath("//table")
    if not tables:
        raise ValueError("В документе нет таблиц")
    table = max(tables, key=lambda t: len(t.xpath(".//tr")))
    result = extract_from_grid(_table_grid(table), source="html")
    seen = {clean_text(r["raw_name"]).lower() for r in result["rows"]}
    extra = []
    for t in tables:
        if t is table:
            continue
        for row in _extra_rows(t):
            key = clean_text(row["raw_name"]).lower()
            if key in seen:
                continue
            seen.add(key)
            extra.append(row)
    if extra:
        start = len(result["rows"])
        for i, row in enumerate(extra, 1):
            row["row_num"] = start + i
        result["rows"].extend(extra)
        result["meta"]["extra_table_rows"] = len(extra)
    return result


def parse_csv(path):
    data = Path(path).read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", "replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(clean_text(v) for v in row)]
    return extract_from_grid(rows, source="csv")


def parse_xlsx(path):
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    best = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            values = [clean_text(v) for v in row]
            if any(values):
                rows.append(values)
        if len(rows) > len(best):
            best = rows
    wb.close()
    if not best:
        raise ValueError("Пустой XLSX")
    return extract_from_grid(best, source="xlsx")


def parse_file(path):
    ext = Path(path).suffix.lower()
    if ext in (".doc", ".docx"):
        return parse_html(convert_to_html(path))
    if ext == ".xls":
        return parse_xlsx(_convert_to_xlsx(path))
    if ext == ".xlsx":
        return parse_xlsx(path)
    if ext == ".csv":
        return parse_csv(path)
    raise ValueError("Неподдерживаемый формат: %s" % ext)
