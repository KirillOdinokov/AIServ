"""Разбор прайс-листа ТД РЕКС (4 листа, 4 уровня цен).

Порт logic из E:\\Сайты\\rex.moscow\\tools\\parse_price.py под Базу.
Возвращает items: [{sheet, row, name, unit, packs:[{pack, retail, t3, t10, dist}]}].
`row` — номер строки Excel (1-based), по нему маппинг slug→строка находит позицию.
"""
import re

from openpyxl import load_workbook

# раскладка колонок по листам (1-based)
LAYOUT = {
    "Прайс РиЗБ": dict(name=1, desc=2, pack=3, money=[4, 5, 6, 7], header_row=9),
    "Комплектующие для хим.анкеров": dict(name=1, desc=2, pack=3, money=[4, 5, 6, 7], header_row=9),
    "Прайс ИНЪЕКЦИИ": dict(name=1, desc=2, pack=3, money=[4, 5, 6, 7], header_row=8),
    "Прайс Гидролента": dict(name=1, desc=2, pack=6, money=[7, 8, 9, 10], header_row=8),
}

TOP = {
    "Прайс РиЗБ": [
        "Составы для ремонтных работ", "Инъекционные составы для упрочнения конструкций",
        "Составы для ремонтных работ на полимерной основе", "Защитные составы",
        "Составы для устройства пола", "Составы для ПОДЗЕМНОГО СТРОИТЕЛЬСТВА",
        "Адгезионные составы и связующие", "Изделия для ГЕРМЕТИЗАЦИИ и ГИДРОИЗОЛЯЦИИ швов",
        "Сопутствующие материалы",
    ],
    "Комплектующие для хим.анкеров": [
        "Комплектующие для монтажа", "Оборудование для установки", "Перфорированные втулки",
    ],
    "Прайс ИНЪЕКЦИИ": [
        "Гидроактивный жесткий полиуретановый", "Однокомпонентные гидроактивные",
        "Двухкомпонентные полиуретановые", "Двухкомпонентные составы на основе акриловых смол",
        "Комплектующие для инъектирования", "Оборудование для инъектирование за",
        "Оборудование для инъектирования", "Составы для подготоки и очистки",
    ],
    "Прайс Гидролента": ["Гидроизоляционные ленты", "Адгезионный состав"],
}

UNIT_RE = re.compile(r"^(упаковка|шт\.?|п\.?м\.?|Цена за (п\.?м\.?|упаковку|шт\.?))$", re.I)

NOTE_RE = re.compile(
    r"^(¹|Важные примечания|Важные|Примечан|\d+\.\s|-\s|МО,|Калужская|Затворение|Для затворения|"
    r"Стоимость|Условия|Цены указаны|Прайс-лист действ)", re.I)


def _money(v):
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    if isinstance(v, str):
        s = v.strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
        if re.fullmatch(r"-?\d+(\.\d+)?", s):
            return round(float(s), 2)
    return None


def _clean(v):
    if v is None:
        return ""
    s = str(v).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", " ", s)
    return s.strip()


def parse(path):
    wb = load_workbook(path, data_only=True)
    items = []
    for ws in wb.worksheets:
        lay = LAYOUT.get(ws.title)
        if not lay:
            continue
        cols = lay["money"]
        path_sec, unit, last_item = [], "", None

        for r in range(lay["header_row"] + 1, ws.max_row + 1):
            name = _clean(ws.cell(row=r, column=lay["name"]).value)
            pack = _clean(ws.cell(row=r, column=lay["pack"]).value)
            vals = [_money(ws.cell(row=r, column=c).value) for c in cols]

            if not name and not pack and not any(v is not None for v in vals):
                continue
            text = (name + " " + pack).strip()
            if NOTE_RE.match(text) or NOTE_RE.match(name):
                continue

            if name and not pack and not any(v is not None for v in vals) and UNIT_RE.match(name):
                unit = name
                continue

            is_header = name and not pack and not any(v is not None for v in vals)
            is_component = bool(is_header and last_item
                                and last_item["name"].split()[0].lower() == name.split()[0].lower())
            if is_component:
                last_item["components"].append(name)
                continue
            if is_header:
                if any(name.startswith(p) for p in TOP.get(ws.title, [])):
                    path_sec = [name]
                else:
                    path_sec = ([path_sec[0], name] if path_sec else [name])
                last_item = None
                continue

            if not name and pack and last_item:
                if pack in ("-", "—"):
                    continue
                last_item["packs"].append(dict(pack=pack, unit=unit,
                                               retail=vals[0], t3=vals[1], t10=vals[2], dist=vals[3]))
                continue

            if not name:
                continue

            item = {
                "sheet": ws.title,
                "row": r,
                "section": list(path_sec),
                "name": name,
                "unit": unit,
                "components": [],
                "packs": [dict(pack=pack, unit=unit,
                               retail=vals[0], t3=vals[1], t10=vals[2], dist=vals[3])],
            }
            items.append(item)
            last_item = item

    wb.close()
    return items
