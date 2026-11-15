"""Поиск изображений в DuckDuckGo и скачивание референсов."""
import re
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
    # Стоковые/декоративные картинки и случайные «красивые» сайты:
    # для референса товара они почти всегда дают мусор.
    "dreamstime.com", "shutterstock.com", "istockphoto.com",
    "depositphotos.com", "gettyimages.com", "alamy.com",
    "123rf.com", "bigstockphoto.com", "canstockphoto.com",
    "stock.adobe.com", "freepik.com", "bigfoto.name",
    "pinimg.com", "blog.mail.ru", "mapametro.com",
    "wikiway.com", "geography.su", "kirmuseum.org",
    "orlyairport.net", "playstation.com", "gamerantimages.com",
    "independent.co.uk", "saltinourhair.com",
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


def search_images(query, max_results=5, retries=4, use_suffix=True):
    """Поиск картинок через DuckDuckGo (пакет ddgs).

    use_suffix=False — искать ровно по названию товара (без «на белом фоне»):
    для точных моделей (артикулов) суффикс сбивает выдачу.
    Возвращает список {title, image, width, height}.
    Пустой результат (rate limit) тоже повторяется с задержкой.
    """
    from ddgs import DDGS
    cleaned = _clean_query(query) if use_suffix else (query or "").strip()
    for attempt in range(retries):
        _throttle()
        try:
            with DDGS() as d:
                # запрашиваем с запасом, чтобы после фильтрации осталось достаточно
                res = list(d.images(cleaned, region="ru-ru", max_results=max(8, max_results + 5)))
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


def _search_yandex_images(query, max_results=10):
    """Неофициальный поиск по картинкам Яндекса.

    У Яндекса нет публичного Image Search API для текстового запроса, но
    HTML-выдача отдаёт JSON с origUrl/размерами. Это основной провайдер для
    российских товаров: DDG Image Search часто возвращает нерелевантные
    «красивые» картинки (Тадж-Махал, туристические достопримечательности).
    """
    import html as _html
    import re as _re

    text = (query or "").strip()
    if not text:
        return []
    _throttle()
    url = "https://yandex.ru/images/search?text=" + urllib.parse.quote(text)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            page = resp.read().decode("utf-8", "replace")
    except Exception:
        return []
    page = _html.unescape(page)
    if "captcha" in page.lower()[:5000]:
        return []

    pattern = _re.compile(
        r'"snippet":\{"title":"(.*?)","domain":"(.*?)","url":"(.*?)".{0,4000}?'
        r'"img_href":"(https?://[^"]+)"',
        _re.S,
    )
    results = []
    seen = set()
    for m in pattern.finditer(page):
        title, domain, source, img = m.groups()
        if not img or img in seen:
            continue
        seen.add(img)
        title = _re.sub(r"<[^>]+>", "", title or "").strip()
        results.append({
            "title": title,
            "image": img,  # обычно CDN Яндекса — быстрее и без hotlink-защиты
            "width": 0,
            "height": 0,
            "domain": domain,
            "url": source,
        })
        if len(results) >= max_results:
            break
    return results


def download_image(url, dest: Path, timeout=10):
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


def find_reference_image(query, dest: Path, max_results=8, min_width=0, min_height=0,
                         use_suffix=True, allow_ddg=True):
    """Найти самую крупную релевантную картинку по запросу и скачать её в dest.

    Основной источник — Яндекс.Картинки; DDG используется только как fallback,
    если allow_ddg=True. Для референсов товаров fallback отключён: лучше
    сгенерировать без референса, чем взять случайный Тадж-Махал.
    """
    results = _search_yandex_images(query, max_results=max_results)
    from_yandex = bool(results)
    if not results and allow_ddg:
        results = search_images(query, max_results=max_results, use_suffix=use_suffix)
    if not results:
        return None

    # Жёсткий фильтр по минимальному разрешению (если задан)
    def _dim(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def area(r):
        return _dim(r.get("width")) * _dim(r.get("height"))

    def _tokens(s):
        return {t for t in re.split(r"[^0-9a-zа-яё]+", (s or "").lower()) if len(t) >= 2}

    q_tokens = _tokens(query)

    def relevance(r):
        # Учитываем и заголовок, и URL: у карточек товаров артикул часто есть
        # только в имени файла, а заголовок бывает пустым.
        text = f"{r.get('title') or ''} {r.get('image') or ''}"
        return len(q_tokens & _tokens(text))

    def is_relevant(r):
        text = f"{r.get('title') or ''} {r.get('image') or ''}"
        matched = q_tokens & _tokens(text)
        if not matched:
            return False
        # Если в названии есть артикул/модель (ECo110S, Decra, R-104), картинка
        # должна совпасть хотя бы с одним таким «отличительным» токеном.
        distinctive = {t for t in q_tokens if any(ch.isdigit() for ch in t) or len(t) >= 6}
        if distinctive and not (matched & distinctive):
            return False
        # Для длинных названий одного общего слова («вентилятор», «товар»)
        # недостаточно — это как раз то, что раньше давало Тадж-Махал и Ганешу.
        if len(q_tokens) >= 3 and len(matched) < 2:
            return False
        return True

    # DDG нестабилен: если в первой выдаче нет уверенного совпадения по
    # названию/модели — делаем второй запрос и объединяем результаты.
    if not from_yandex and max((relevance(r) for r in results), default=0) < 3:
        seen = {r.get("image") for r in results}
        for r in search_images(query, max_results=max_results, use_suffix=use_suffix):
            img = r.get("image")
            if img and img not in seen:
                seen.add(img)
                results.append(r)

    # Страховка: отбрасываем исключённые домены (ozon/wildberries/стоки),
    # даже если они прошли через CDN-прокси Яндекса.
    filtered = [r for r in results
                if not _is_excluded(r.get("image")) and not _is_excluded(r.get("url"))]
    if filtered:
        results = filtered

    # Не отдаём случайную «красивую» картинку, если она вообще не совпала
    # с названием/артикулом. Лучше сгенерировать без референса.
    results = [r for r in results if is_relevant(r)]
    if not results:
        return None

    if min_width or min_height:
        hard = [r for r in results
                if _dim(r.get("width")) >= min_width and _dim(r.get("height")) >= min_height]
        if not hard:
            hard = results  # fallback: не нашли крупную — берём любую
        results = hard

    # Сортируем по релевантности (совпадение слов запроса в заголовке), затем
    # по площади: самое крупное фото легко оказывается ДРУГИМ товаром (например,
    # «клиновой анкер» вместо «анкер А8-800 L»), поэтому сначала релевантность.
    results = sorted(results, key=lambda r: (relevance(r), area(r)), reverse=True)

    # Скачиваем по релевантности, пока не получится валидное изображение.
    # Ограничиваем перебор: битые внешние URL не должны задерживать очередь.
    for r in results[:5]:
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
