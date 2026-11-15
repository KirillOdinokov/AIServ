"""Безопасное создание кэшированных thumbnail-превью."""
from pathlib import Path

from PIL import Image, PngImagePlugin

import config

# Некоторые PNG из поиска содержат большой iCCP-чанк; по умолчанию Pillow
# отказывается его распаковывать и /api/thumb отдаёт многомегабайтный оригинал.
PngImagePlugin.MAX_TEXT_CHUNK = max(getattr(PngImagePlugin, "MAX_TEXT_CHUNK", 0), 64 * 1024 * 1024)
PngImagePlugin.MAX_TEXT_MEMORY = max(getattr(PngImagePlugin, "MAX_TEXT_MEMORY", 0), 256 * 1024 * 1024)


def thumb_path(filename, size=400):
    return config.OUTPUT_DIR / ".thumbs" / f"{size}_{filename}"


def ensure_thumb(path, size=400):
    """Создать JPEG-превью рядом с оригиналом. Возвращает путь или None."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        rel = path.relative_to(config.OUTPUT_DIR)
    except ValueError:
        rel = Path(path.name)
    dst = thumb_path(str(rel).replace("\\", "/"), size)
    try:
        if dst.exists() and dst.stat().st_mtime >= path.stat().st_mtime:
            return dst
        with Image.open(path) as im:
            try:
                im.draft("RGB", (size, size))
            except Exception:
                pass
            im = im.convert("RGB")
            resample = getattr(Image, "Resampling", Image).BILINEAR
            im.thumbnail((size, size), resample)
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst, "JPEG", quality=70, optimize=True, progressive=True)
        return dst
    except Exception as e:
        print(f"[thumb] Не удалось сделать превью {path}: {e}")
        return None
