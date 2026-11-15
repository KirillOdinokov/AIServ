"""Каталог и параметры фотореализации рендеров Deformix."""
import csv
import os
import re
from pathlib import Path

import config

DATA_DIR = config.BASE_DIR / "restyle_data"
CUT_DIR = DATA_DIR / "cut"
SCENE_DIR = DATA_DIR / "scene"
TODO_CSV = DATA_DIR / "_todo.csv"
CARDS_CSV = DATA_DIR / "_cards.csv"
INDEX_CSV = DATA_DIR / "_index.csv"
OUTPUT_DIR = config.OUTPUT_DIR / "restyle"

CUT_PROMPT = """Сделай из этого изображения фотографию, снятую на камеру, а не 3D-рендер.

Сохрани без изменений: композицию, ракурс, пропорции, геометрию профиля, линию разреза, все детали, крепёж и белый фон.

Измени только материалы и свет:
— алюминий: микрозерно после анодирования, лёгкие продольные царапины, блик неровный, с потёртостями;
— бетон: поры, раковины, скол на кромке, пыль в углах;
— резина и ТЭП: матовая, мелкая шагрень, без глянца;
— сталь крепежа: следы инструмента, потёртые грани;
— свет: мягкий рассеянный, одна естественная тень под профилем, лёгкое контровое по алюминию;
— оптика: 50 мм, f/8, небольшая глубина резкости на дальнем плане, лёгкое зерно, без аберраций.

Не добавляй: текст, логотипы, людей, новые предметы, водяные знаки, рамки, размерные линии, стрелки. Фон оставь белым. Кадр не кадрируй."""

SCENE_PROMPT = """Сделай из этого изображения фотографию, снятую на камеру, а не 3D-рендер.

Сохрани без изменений: композицию, ракурс, положение шва и разреза, все объекты в кадре.

Измени только материалы, свет и следы эксплуатации:
— бетон: поры, раковины, разнотон, тёмные полосы от шин, пыль и мелкий мусор у шва;
— алюминий профиля: микрозерно, царапины, затёртые до блеска кромки;
— резина компенсатора: матовая шагрень, прилипшая пыль;
— металл техники: реальные блики, грязь на дисках, потёртости на порогах и бортах;
— люди: реальная кожа и ткань, а не «пластик»;
— свет: дневной из проёмов или смешанный, одна доминирующая тень, лёгкое зерно;
— оптика: 35 мм, f/5.6, лёгкая боке на дальнем плане.

Не добавляй: текст, логотипы, водяные знаки, рамки, новых людей и предметы.
Кадр не кадрируй."""

_catalog = None


def _clean_slug(value):
    value = os.path.basename((value or "").strip())
    return re.sub(r"[^a-zA-Z0-9_.-]+", "", value.rsplit(".", 1)[0])


def _read_cards():
    by_slug = {}
    if CARDS_CSV.exists():
        with open(CARDS_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter=";"):
                render = row.get("файл_рендера") or ""
                slug = _clean_slug(render)
                if slug:
                    by_slug.setdefault(slug, []).append(row.get("ID") or "")
    return by_slug


def _read_index():
    by_file = {}
    if INDEX_CSV.exists():
        with open(INDEX_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter=";"):
                by_file[(row.get("file") or "").replace("\\", "/")] = row
    return by_file


def load_catalog():
    global _catalog
    if _catalog is not None:
        return _catalog
    cards = _read_cards()
    index = _read_index()
    index_by_slug = {}
    for row in index.values():
        slug = _clean_slug(row.get("slug") or row.get("file"))
        if slug:
            index_by_slug[slug] = row

    cut = []
    by_slug = {}
    md5_to_item = {}
    if TODO_CSV.exists():
        with open(TODO_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter=";"):
                rel = (row.get("file") or "").replace("\\", "/")
                slug = _clean_slug(row.get("slug") or rel)
                path = DATA_DIR / rel
                item = {
                    "kind": "cut",
                    "slug": slug,
                    "file": rel,
                    "designation": (row.get("designation") or "").strip(),
                    "device": (row.get("device") or "").strip(),
                    "cards": [],
                    "aliases": [],
                    "available": path.exists(),
                }
                cut.append(item)
                by_slug[slug] = item
                md5 = (index_by_slug.get(slug) or {}).get("md5")
                if md5:
                    md5_to_item.setdefault(md5, item)

    for slug, ids in cards.items():
        item = by_slug.get(slug)
        if item is not None:
            item["cards"].extend(ids)
            continue
        row = index_by_slug.get(slug)
        if not row:
            continue
        target = by_slug.get(_clean_slug(row.get("dup_of")))
        if target is None:
            target = md5_to_item.get(row.get("md5"))
        if target is not None:
            target["cards"].extend(ids)
            if slug not in target["aliases"]:
                target["aliases"].append(slug)

    scene = []
    for rel, row in index.items():
        if row.get("kind") != "scene":
            continue
        slug = _clean_slug(rel)
        path = DATA_DIR / rel
        scene.append({
            "kind": "scene",
            "slug": slug,
            "file": rel,
            "designation": (row.get("designation") or "").strip(),
            "device": (row.get("device") or "").strip(),
            "cards": [],
            "available": path.exists(),
        })
    if not scene and SCENE_DIR.exists():
        for path in sorted(SCENE_DIR.glob("*.jpg")):
            scene.append({
                "kind": "scene", "slug": path.stem, "file": f"scene/{path.name}",
                "designation": path.stem, "device": "", "cards": [], "available": True,
            })

    _catalog = {"cut": cut, "scene": scene}
    return _catalog


def get_item(kind, slug):
    kind = "scene" if kind == "scene" else "cut"
    slug = _clean_slug(slug)
    for item in load_catalog().get(kind, []):
        if item["slug"] == slug:
            return item
    return None


def source_path(item):
    if not item:
        return None
    path = DATA_DIR / (item.get("file") or "")
    try:
        path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        return None
    return path if path.exists() else None


def prompt_for(kind):
    return SCENE_PROMPT if kind == "scene" else CUT_PROMPT


def output_size(kind, source):
    """Размер генерации: профили — длинная сторона 1536, сцены — 1280."""
    from PIL import Image
    with Image.open(source) as im:
        w, h = im.size
    long_side = 1280 if kind == "scene" else 1536
    if w >= h:
        width = long_side
        height = max(8, round(width * h / w / 8) * 8)
    else:
        height = long_side
        width = max(8, round(height * w / h / 8) * 8)
    return width, height


def summary():
    cat = load_catalog()
    return {
        "cut": len(cat["cut"]),
        "cut_total": sum(1 for x in cat["cut"]),
        "scene": len(cat["scene"]),
        "cut_available": sum(1 for x in cat["cut"] if x["available"]),
        "scene_available": sum(1 for x in cat["scene"] if x["available"]),
        "cards": sum(len(x["cards"]) for x in cat["cut"]),
    }
