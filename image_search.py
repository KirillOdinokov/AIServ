"""Поиск изображений в DuckDuckGo и скачивание референсов."""
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Домены, которые исключаем из выдачи: на их картинках обычно есть
# дополнительные надписи/водяные знаки (маркетплейсы).
# Ozon отдаёт CDN с доменом ozone.ru (с "e"), поэтому оба варианта.
EXCLUDED_DOMAINS = (
    "ozon.ru", "ozone.ru", "ozon.by", "ozon.kz",
    "wildberries.ru", "wildberries.by", "wb.ru",
)

# Суффикс запроса, чтобы отдавать предпочтение чистым предметным фото.
QUERY_SUFFIX = "на белом фоне"

# Троттлинг между запросами — DuckDuckGo агрессивно режет частые запросы.
# Для больших задач (до 5к товаров) это критично.
MIN_INTERVAL = 1.5
_throttle_lock = threading.Lock()
_last_search = 0.0


def _throttle():
    global _last_search
    with _throttle_lock:
        wait = MIN_INTERVAL - (time.time() - _last_search)
        if wait > 0:
            time.sleep(wait)
        _last_search = time.time()


def _fetch(url, headers=None, timeout=30):
    # percent-кодируем не-ASCII символы (кириллица в URL), сохраняя структуру URL
    url = urllib.parse.quote(url or "", safe=":/?&=#%")
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _is_excluded(url):
    """True, если URL принадлежит исключённому домену (ozon/wildberries)."""
    import urllib.parse
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    for d in EXCLUDED_DOMAINS:
        if host == d or host.endswith("." + d):
            return True
    return False


def _clean_query(query):
    """Добавить суффикс к поисковому запросу (если его ещё нет)."""
    q = (query or "").strip()
    if not q:
        return q
    if QUERY_SUFFIX in q.lower():
        return q
    return f"{q} {QUERY_SUFFIX}"


def search_images(query, max_results=5, retries=4):
    """Поиск картинок через DuckDuckGo (пакет ddgs).

    Возвращает список {title, image, width, height}.
    Пустой результат (rate limit) тоже повторяется с задержкой.
    """
    from ddgs import DDGS
    cleaned = _clean_query(query)
    for attempt in range(retries):
        _throttle()
        try:
            with DDGS() as d:
                # запрашиваем с запасом, чтобы после фильтрации осталось достаточно
                res = list(d.images(cleaned, max_results=max(8, max_results + 5)))
            results = []
            for r in res:
                img = r.get("image")
                if not img:
                    continue
                if _is_excluded(img):
                    continue
                results.append({
                    "title": r.get("title", ""),
                    "image": img,
                    "width": r.get("width"),
                    "height": r.get("height"),
                })
                if len(results) >= max_results:
                    break
            if results:
                return results
            # пусто — вероятно rate limit, ждём и пробуем снова
            time.sleep(2 * (attempt + 1))
        except Exception:
            if attempt == retries - 1:
                return []
            time.sleep(2 * (attempt + 1))
    return []


def download_image(url, dest: Path, timeout=30):
    """Скачать изображение в файл. Возвращает True при успехе.

    Проверяет, что скачано реальное изображение (а не HTML-страница с ошибкой),
    и что файл не битый.
    """
    try:
        data = _fetch(url, timeout=timeout)
        if len(data) < 100:
            return False
        # проверяем, что это картинка (а не HTML-заглушка 403/404)
        from PIL import Image
        import io as _io
        try:
            with Image.open(_io.BytesIO(data)) as im:
                im.verify()
        except Exception:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception:
        return False


def find_reference_image(query, dest: Path, max_results=8, min_width=0, min_height=0):
    """Найти самую крупную релевантную картинку по запросу и скачать её в dest.

    Выбирает результат с наибольшим разрешением (пропуская мелкие превью),
    чтобы не брать 167×302 вместо нормального фото. min_width/min_height —
    жёсткий порог разрешения (для post_image нужно >= Full HD).
    Пропускает битые/слишком маленькие. Возвращает URL или None.
    """
    results = search_images(query, max_results=max_results)
    if not results:
        return None

    # Страховка: отбрасываем исключённые домены (ozon/wildberries),
    # даже если они каким-то образом прошли фильтр в search_images.
    filtered = [r for r in results if not _is_excluded(r.get("image"))]
    if filtered:
        results = filtered

    # Жёсткий фильтр по минимальному разрешению (если задан)
    def _dim(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    if min_width or min_height:
        hard = [r for r in results
                if _dim(r.get("width")) >= min_width and _dim(r.get("height")) >= min_height]
        if not hard:
            hard = results  # fallback: не нашли крупную — берём любую
        results = hard

    # Сортируем по площади (ширина×высота) по убыванию — берём самое крупное,
    # но не даём совсем крошечным превью (ширина или высота < 300) обгонять крупные.
    def area(r):
        return _dim(r.get("width")) * _dim(r.get("height"))

    results = sorted(results, key=area, reverse=True)

    # Скачиваем по убыванию размера, пока не получится валидное изображение
    for r in results:
        w = _dim(r.get("width"))
        h = _dim(r.get("height"))
        # пропускаем заведомо мелкие превью, если есть что-то крупнее
        if results and area(results[0]) > area(r) * 3 and min(w, h) < 300:
            continue
        url = r["image"]
        try:
            if download_image(url, dest):
                return url
        except Exception:
            continue
    return None
