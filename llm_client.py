"""Клиент для генерации описаний товаров через DeepSeek API + поиск DuckDuckGo."""
import datetime
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import config

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# Peak-окна DeepSeek (UTC): 01:00–04:00 и 06:00–10:00
PEAK_WINDOWS_UTC = [(1, 4), (6, 10)]

# Токен из файла или переменной окружения
def _load_token():
    token_file = config.BASE_DIR / "deepseek_token.txt"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    import os
    return os.getenv("DEEPSEEK_API_KEY", "").strip()

DEEPSEEK_TOKEN = _load_token()

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3:14b"

# Ollama Cloud (подписка) — напрямую через API ollama.com
OLLAMA_CLOUD_URL = "https://ollama.com/api/chat"
OLLAMA_CLOUD_MODEL = "qwen3.5:397b"


def _load_ollama_token():
    token_file = config.BASE_DIR / "ollama_token.txt"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    import os
    return os.getenv("OLLAMA_API_KEY", "").strip()

OLLAMA_CLOUD_TOKEN = _load_ollama_token()


def is_peak_time(now=None):
    """True, если сейчас peak-время (дорогой тариф DeepSeek).

    Проверяет по UTC. Peak-окна: 01:00–04:00 и 06:00–10:00 UTC.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    hour = now.hour + now.minute / 60.0
    for start, end in PEAK_WINDOWS_UTC:
        if start <= hour < end:
            return True
    return False


def peak_windows_local(offset_hours=3):
    """Вернуть peak-окна в локальном времени (по умолчанию UTC+3, Москва)."""
    return [(s + offset_hours, e + offset_hours) for s, e in PEAK_WINDOWS_UTC]


def _post_chat(messages, max_tokens=4000, temperature=0.7):
    """Запрос к DeepSeek chat completions."""
    if not DEEPSEEK_TOKEN:
        raise RuntimeError("Не задан API-ключ DeepSeek (файл deepseek_token.txt)")

    body = json.dumps({
        "model": "deepseek-v4-flash",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        # Отключаем thinking mode — для рерайта рассуждения не нужны,
        # это экономит reasoning-токены (тарифицируются как output).
        "thinking": {"type": "disabled"},
    }).encode("utf-8")

    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_TOKEN}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _strip_markdown(text):
    """Убрать ```...``` обёртку, если модель её добавила."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _post_chat_local(messages, max_tokens=4000, temperature=0.7):
    """Запрос к локальной модели через Ollama."""
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["message"]["content"]


def _post_chat_ollama_cloud(messages, max_tokens=4000, temperature=0.7):
    """Запрос к Ollama Cloud (подписка) напрямую через API ollama.com."""
    if not OLLAMA_CLOUD_TOKEN:
        raise RuntimeError("Не задан API-ключ Ollama Cloud (файл ollama_token.txt)")

    body = json.dumps({
        "model": OLLAMA_CLOUD_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_CLOUD_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OLLAMA_CLOUD_TOKEN}",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["message"]["content"]


def _chat(messages, max_tokens=4000, temperature=0.7, model="deepseek"):
    """Выбор провайдера: deepseek (API), local (Ollama) или ollama_cloud."""
    if model == "local":
        return _post_chat_local(messages, max_tokens, temperature)
    if model == "ollama_cloud":
        return _post_chat_ollama_cloud(messages, max_tokens, temperature)
    return _post_chat(messages, max_tokens, temperature)


# ─── Поиск DuckDuckGo ─────────────────────────────────────────────────────────

def duckduckgo_search(query, max_results=5, retries=3):
    """Поиск через DuckDuckGo (lhtml), возвращает список {title, snippet, url}.

    С повтором при rate limit (DuckDuckGo может блокировать частые запросы).
    """
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            break
        except Exception:
            if attempt == retries - 1:
                return []
            time.sleep(2 * (attempt + 1))
    else:
        return []

    results = []
    # Результаты: <a class="result__a" href="...">title</a> и <a class="result__snippet">snippet</a>
    titles = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)

    for i, (href, title) in enumerate(titles[:max_results]):
        title = re.sub(r"<[^>]+>", "", title).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
        if href.startswith("//"):
            href = "https:" + href
        results.append({"title": title, "snippet": snippet, "url": href})
    return results


def fetch_page_text(url, max_chars=6000):
    """Скачать страницу по URL и вернуть извлечённый текст (без HTML-тегов)."""
    import urllib.request as _urlreq
    try:
        req = _urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlreq.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""

    # убираем скрипты/стили
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    # убираем теги, оставляем текст
    text = re.sub(r"<[^>]+>", " ", html)
    # схлопываем пробелы
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


# ─── Генерация описаний ──────────────────────────────────────────────────────

REWRITE_SYSTEM = (
    "Ты — профессиональный копирайтер и технический писатель. "
    "Ты пишешь описания товаров для интернет-магазина на русском языке. "
    "Используй технический язык и терминологию профессионалов из промышленности и строительства. "
    "Пиши чётко, информативно, без воды и рекламных штампов. "
    "Не выдумывай характеристики, которых нет в исходных данных. "
    "Отвечай строго в формате JSON без markdown-обёртки."
)


def rewrite_excerpt(title, source_text, model="deepseek"):
    """Короткое описание ~350 символов."""
    prompt = (
        f"Товар: {title}\n\n"
        f"Исходные данные о товаре:\n{source_text}\n\n"
        "Напиши короткое описание товара длиной не более 350 символов. "
        "Обязательно закончи описание точкой (завершённым предложением, без обрыва на полуслове). "
        "Техническим языком, с профессиональной терминологией. "
        "Один абзац, без маркированных списков и заголовков.\n\n"
        'Верни строго JSON: {"excerpt": "текст"}'
    )
    try:
        raw = _chat(
            [{"role": "system", "content": REWRITE_SYSTEM}, {"role": "user", "content": prompt}],
            max_tokens=800,
            model=model,
        )
        data = json.loads(_strip_markdown(raw))
        return data.get("excerpt", "").strip()
    except Exception as e:
        raise RuntimeError(f"Ошибка генерации короткого описания: {e}")


def rewrite_content(title, source_text, model="deepseek"):
    """Длинное описание 1500-3000 символов, разбитое на абзацы с h2."""
    prompt = (
        f"Товар: {title}\n\n"
        f"Исходные данные о товаре:\n{source_text}\n\n"
        "Напиши подробное техническое описание товара длиной 1500-3000 символов. "
        "Разбей текст на логические абзацы, каждый начинается с заголовка h2 "
        "(например: <h2>Назначение</h2>, <h2>Характеристики</h2>, <h2>Применение</h2>). "
        "Используй профессиональную терминологию промышленности и строительства. "
        "Не выдумывай характеристики, которых нет в исходных данных.\n\n"
        'Верни строго JSON: {"content": "текст с h2-заголовками"}'
    )
    try:
        raw = _chat(
            [{"role": "system", "content": REWRITE_SYSTEM}, {"role": "user", "content": prompt}],
            max_tokens=5000,
            model=model,
        )
        data = json.loads(_strip_markdown(raw))
        return data.get("content", "").strip()
    except Exception as e:
        raise RuntimeError(f"Ошибка генерации длинного описания: {e}")


def _truncate_excerpt(text, limit=380):
    """Обрезать текст до limit символов по границе предложения.

    Не режем на полуслове: ищем последнюю точку/восклицание/вопрос в пределах
    лимита, при недоступности — последний пробел.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    for i in range(len(window) - 1, -1, -1):
        if window[i] in ".!?…":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt in ("", " ", "\n", ")", '"', "'", "»"):
                return text[: i + 1].rstrip()
    sp = window.rfind(" ")
    if sp > 0:
        return text[:sp].rstrip()
    return window


def generate_description(title, excerpt=None, content=None, model="deepseek"):
    """Генерирует короткое и длинное описание товара.

    Если есть excerpt/content — делает рерайт. Если чего-то нет —
    ищет данные через DuckDuckGo и генерирует на их основе.
    Возвращает (excerpt, content).
    """
    # Собираем исходные данные
    source_parts = []
    if excerpt:
        source_parts.append(f"Короткое описание: {excerpt}")
    if content:
        source_parts.append(f"Полное описание: {content}")

    if not source_parts:
        # Нет данных — ищем в интернете
        search_results = duckduckgo_search(title)
        snippets = "\n".join(
            f"- {r['title']}: {r['snippet']}" for r in search_results if r["snippet"]
        )
        if not snippets:
            snippets = f"Название товара: {title}"
        source_text = f"Данные из поиска по товару:\n{snippets}"
    else:
        source_text = "\n\n".join(source_parts)

    new_excerpt = rewrite_excerpt(title, source_text, model=model)
    new_content = rewrite_content(title, source_text, model=model)

    # Ограничиваем длину по границе предложения (не обрываем на полуслове)
    new_excerpt = _truncate_excerpt(new_excerpt)
    return new_excerpt, new_content
