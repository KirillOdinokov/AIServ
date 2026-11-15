"""Сервис «Тачки»: подбор референса, промпт и публичные ссылки на исходники."""
import io
import os
import re
import secrets
import time
from pathlib import Path

import config
import image_search

SOURCE_DIR = config.OUTPUT_DIR / "cars_sources"
SOURCE_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR = config.OUTPUT_DIR / "cars"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

_TOKEN_RE = re.compile(r"^[a-f0-9]{24,64}\.(png|jpg|jpeg|webp)$", re.I)


def new_token():
    return secrets.token_hex(16)


def source_filename(token, ext="png"):
    ext = (ext or "png").lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ("png", "jpg", "webp"):
        ext = "png"
    return f"{token}.{ext}"


def source_path(filename):
    """Безопасный путь к публичному исходнику (только cars_sources)."""
    if not _TOKEN_RE.match(filename or ""):
        return None
    p = SOURCE_DIR / filename
    return p if p.is_file() else None


def public_url(filename):
    return f"{config.PUBLIC_BASE_URL}/public/cars/{filename}"


def _normalize_image(data, dest, max_side=1600):
    """Проверить/перекодировать изображение с ограничением размера.

    Для публичных референсов используем JPEG — Nano Banana быстрее забирает
    ссылку, а для автомобильного фото качества 1600 px более чем достаточно.
    """
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.suffix.lower() in (".jpg", ".jpeg"):
            im.save(dest, "JPEG", quality=92, optimize=True)
        else:
            im.save(dest, "PNG")


def save_uploaded(data, max_bytes=30 * 1024 * 1024):
    """Сохранить загруженное пользователем фото, вернуть имя файла в cars_sources."""
    if not data:
        raise ValueError("Пустой файл")
    if len(data) > max_bytes:
        raise ValueError("Файл больше 30 МБ")
    token = new_token()
    name = source_filename(token, "jpg")
    dest = SOURCE_DIR / name
    try:
        _normalize_image(data, dest)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise ValueError(f"Не удалось прочитать изображение: {e}")
    return name


def save_remote(url, max_bytes=30 * 1024 * 1024):
    """Скачать внешний референс и сохранить его в cars_sources (PNG)."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Референс больше 30 МБ")
    token = new_token()
    name = source_filename(token, "png")
    dest = SOURCE_DIR / name
    try:
        _normalize_image(data, dest)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise ValueError(f"Референс не является изображением: {e}")
    return name


def _landscape_score(result):
    """Предпочитаем горизонтальные фотографии автомобиля, а не портретные карточки."""
    try:
        w = int(result.get("width") or 0)
        h = int(result.get("height") or 0)
    except (TypeError, ValueError):
        w = h = 0
    if not w or not h:
        return 0
    ar = w / h
    if w >= 1000 and h >= 600 and 1.15 <= ar <= 3.2:
        return w * h
    return 0


def search_car_photo(brand, model, body_color=""):
    """Найти фотографию автомобиля через DuckDuckGo и сохранить её локально.

    Ищем горизонтальный экстерьер: DDG часто отдаёт портретные карточки
    объявлений, с которыми модели хуже справляются.
    """
    color = (body_color or "").strip()
    if color == "как на фото":
        color = ""
    queries = [
        f"{brand} {model} {color} экстерьер".strip(),
        f"{brand} {model} {color} автомобиль".strip(),
        f"{brand} {model} exterior".strip(),
        f"{brand} {model} кузов".strip(),
    ]
    last_error = None
    for query in queries:
        try:
            results = image_search.search_images(query, max_results=12, use_suffix=False)
        except Exception as e:
            last_error = e
            continue
        ranked = sorted(results, key=_landscape_score, reverse=True)
        # Если горизонтальных нет — берём первый результат как есть.
        candidates = [r for r in ranked if _landscape_score(r)] or ranked
        for result in candidates[:4]:
            url = result.get("image")
            if not url:
                continue
            token = new_token()
            temp = SOURCE_DIR / f"{token}.download"
            try:
                if not image_search.download_image(url, temp):
                    continue
                name = source_filename(token, "jpg")
                _normalize_image(temp.read_bytes(), SOURCE_DIR / name)
                return name, url
            except Exception as e:
                last_error = e
            finally:
                temp.unlink(missing_ok=True)
    if last_error:
        raise RuntimeError(f"Не удалось найти фото автомобиля: {last_error}")
    return None, None


def cleanup_sources(max_age_hours=24 * 30):
    """Удалить старые публичные исходники, чтобы папка не разрасталась."""
    cutoff = time.time() - max_age_hours * 3600
    try:
        for p in SOURCE_DIR.iterdir():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass


def build_prompt(brand, model, body_color, finish_label, film_color, anti_chrome,
                 has_reference=True):
    """Собрать промпт для FLUX/Nano Banana."""
    car = " ".join(x for x in (brand, model) if x).strip()
    if has_reference and car:
        subject = f"the exact {car} from the reference image"
    elif has_reference:
        subject = "the exact car from the reference image"
    elif car:
        subject = f"a {car}"
    else:
        subject = "the car"

    body = (body_color or "").strip()
    body_clause = ""
    if body and body != "как на фото":
        body_clause = f" The car body color is {body}."

    film = (film_color or "").strip()
    if film and film != "в цвет кузова":
        film_clause = f"in {film} {finish_label} vinyl film"
    else:
        film_clause = f"in {finish_label} vinyl film in the same color as the car body"

    anti = (anti_chrome or "none").strip()
    if anti == "matte_black":
        anti_clause = ("All chrome parts (grille, window trim, door handles, mirrors, badges "
                       "and bumper inserts) are covered in matte black.")
    elif anti == "gloss_black":
        anti_clause = ("All chrome parts (grille, window trim, door handles, mirrors, badges "
                       "and bumper inserts) are covered in gloss black.")
    else:
        anti_clause = "Keep the original chrome parts unchanged."

    return (
        f"Professional automotive detailing studio photograph of {subject}. "
        f"The car is fully wrapped {film_clause}.{body_clause} "
        "Preserve the exact car model, body shape, proportions, wheels, headlights, grille, "
        "windows, interior visible through glass and all original design lines; do not replace "
        "the car with another model. "
        f"{anti_clause} "
        "The car is photographed in a clean high-end detailing center with a polished floor, "
        "soft professional studio lighting, a three-quarter front view, the whole car in frame, "
        "photorealistic, sharp reflections on the film, premium catalog quality. "
        "No text, no watermarks, no people, no added logos or signs."
    )
