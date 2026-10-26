"""Клиент Nano Banana API — внешняя генерация/редактирование изображений.

Асинхронный REST: POST /api/v1/create -> POST /api/v1/check.
Возвращает URL результата, который нужно скачать.
"""
import json
import os
import time
import urllib.request

import config

BASE_URL = "https://nanobanana.io"


def _load_key():
    token_file = config.BASE_DIR / "nanobanana_token.txt"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    return os.getenv("NANOBANANA_API_KEY", "").strip()


API_KEY = _load_key()

# Доступные image-модели: id -> {label, credits: {resolution: cost}}.
# resolution: "1k" | "2k" | "4k" | "standard" (фикс. цена без выбора разрешения).
NANOBANANA_MODELS = {
    "nano-banana-2-1": {"label": "Nano Banana 2.1", "credits": {"1k": 12, "2k": 20, "4k": 30}},
    "nano-banana-2": {"label": "Nano Banana 2", "credits": {"1k": 20, "2k": 30, "4k": 40}},
    "nano-banana-2-lite": {"label": "Nano Banana 2 Lite", "credits": {"standard": 18}},
    "nano-banana-pro": {"label": "Nano Banana Pro", "credits": {"1k": 30, "2k": 30, "4k": 50}},
    "nano-banana": {"label": "Nano Banana", "credits": {"standard": 16}},
    "gpt-image-2-5": {"label": "GPT Image 2.5", "credits": {"1k": 19, "2k": 27, "4k": 41}},
    "gpt-image-2-5-pro": {"label": "GPT Image 2.5 Pro", "credits": {"1k": 19, "2k": 27, "4k": 41}},
    "gpt-image-2": {"label": "GPT Image 2", "credits": {"1k": 20, "2k": 30, "4k": 40}},
    "gpt-image-1-5": {"label": "GPT Image 1.5", "credits": {"standard": 20}},
    "seedream-5-0-pro": {"label": "Seedream 5.0 Pro", "credits": {"1k": 50, "2k": 100}},
    "seedream-5-0-lite": {"label": "Seedream 5.0 Lite", "credits": {"2k": 40, "4k": 50}},
    "seedream-4-5": {"label": "Seedream 4.5", "credits": {"2k": 40, "4k": 50}},
    "seedream-4-0": {"label": "Seedream 4.0", "credits": {"2k": 30, "4k": 40}},
    "glm-image": {"label": "GLM Image", "credits": {"standard": 100}},
    "qwen-image": {"label": "Qwen Image", "credits": {"1k": 25, "2k": 25}},
}


def _post(path, payload, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def credits_for(model_id, resolution="2k"):
    """Сколько кредитов нужно для одной картинки на модели+разрешении (или None)."""
    info = NANOBANANA_MODELS.get(model_id)
    if not info:
        return None
    c = info["credits"]
    if "standard" in c:
        return c["standard"]
    return c.get(resolution)


def create_task(model_id, prompt, images=None, aspect_ratio="1:1", resolution="2k"):
    """Создать задачу генерации. Возвращает {task_id, credits}."""
    if not API_KEY:
        raise RuntimeError("Не задан API-ключ Nano Banana (файл nanobanana_token.txt)")

    inp = {"prompt": prompt, "aspect_ratio": aspect_ratio, "resolution": resolution}
    if images:
        inp["images"] = images

    data = _post("/api/v1/create", {"model": model_id, "input": inp})
    d = data.get("data", {})
    return {"task_id": d.get("task_id"), "credits": d.get("credits")}


def check_task(task_id):
    """Проверить статус задачи. Возвращает {status, results, failed_reason}."""
    data = _post("/api/v1/check", {"task_id": task_id})
    d = data.get("data", {})
    inner = d.get("data") or {}
    return {
        "status": d.get("status"),
        "results": inner.get("results", []),
        "failed_reason": d.get("failed_reason"),
    }


def generate(model_id, prompt, images=None, aspect_ratio="1:1", resolution="2k",
             poll_interval=5, timeout=600):
    """Создать задачу, дождаться завершения и вернуть список URL результата.

    Возвращает (list_of_urls, credits). Бросает RuntimeError при ошибке.
    """
    created = create_task(model_id, prompt, images, aspect_ratio, resolution)
    task_id = created["task_id"]
    if not task_id:
        raise RuntimeError("Nano Banana: не вернул task_id")

    start = time.time()
    while True:
        if time.time() - start > timeout:
            raise TimeoutError("Nano Banana: таймаут ожидания результата")
        status = check_task(task_id)
        if status["status"] == "completed":
            return status["results"], created["credits"]
        if status["status"] == "failed":
            raise RuntimeError(f"Nano Banana: генерация не удалась ({status.get('failed_reason')})")
        time.sleep(poll_interval)


def download_image(url, dest, timeout=60):
    """Скачать картинку по URL в файл. Возвращает True при успехе."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 100:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception:
        return False
