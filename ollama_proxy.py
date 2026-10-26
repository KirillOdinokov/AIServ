"""VRAM-aware прокси между opencode и локальным Ollama.

Слушает :11435, проксирует OpenAI-совместимые запросы в Ollama (:11434).
Перед запросом проверяет свободную VRAM через nvidia-smi: если она занята
генерацией изображений (ComfyUI/FLUX), возвращает 503 «VRAM занята»,
вместо того чтобы Ollama пытался загрузить модель и падал с OOM.

Эндпоинты:
  - /v1/*            — проксирование в Ollama (chat/completions, models и т.д.)
  - /unload          — выгрузить модель из VRAM (ollama stop)
  - /health          — проверка + текущее состояние VRAM
"""
import subprocess

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

OLLAMA_URL = "http://127.0.0.1:11434"

# Порог свободной VRAM (МБ). Если свободно меньше — считаем, что VRAM занята
# генерацией изображений/видео и отдаём ошибку, не трогая Ollama.
MIN_FREE_VRAM_MB = 6000

app = FastAPI(title="Ollama VRAM Proxy")


def _nvidia_smi():
    """Вернуть (used_mb, total_mb, free_mb) или None, если nvidia-smi недоступен."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            timeout=5,
        ).decode("utf-8", errors="replace").strip()
        line = out.splitlines()[0]
        used, total, free = [int(x) for x in line.split(",")]
        return used, total, free
    except Exception:
        return None


def _vram_info():
    info = _nvidia_smi()
    if info is None:
        return {"available": False, "message": "nvidia-smi недоступен"}
    used, total, free = info
    return {
        "available": True,
        "used_mb": used,
        "total_mb": total,
        "free_mb": free,
        "busy": free < MIN_FREE_VRAM_MB,
        "min_free_mb": MIN_FREE_VRAM_MB,
    }


@app.get("/health")
def health():
    return {"ok": True, "vram": _vram_info()}


@app.post("/unload")
def unload(body: dict = None):
    """Выгрузить модель из VRAM. body: {"model": "qwen3:14b"} — либо все."""
    body = body or {}
    model = (body.get("model") or "").strip()

    if model:
        targets = [model]
    else:
        # список загруженных моделей из Ollama
        try:
            r = httpx.get(f"{OLLAMA_URL}/api/ps", timeout=10)
            targets = [m.get("name") for m in r.json().get("models", []) if m.get("name")]
        except Exception:
            targets = []

    stopped = []
    for name in targets:
        try:
            subprocess.run(["ollama", "stop", name], capture_output=True, timeout=120)
            stopped.append(name)
        except Exception:
            continue

    return {"ok": True, "stopped": stopped, "vram": _vram_info()}


def _should_block(request: Request) -> bool:
    """Блокировать ли запрос при нехватке VRAM.

    Блокируем только чат-запросы (генерацию текста). Список моделей и прочие
    GET-запросы не требуют загрузки модели и проходят всегда.
    """
    path = request.url.path
    if path.rstrip("/").endswith("/chat/completions"):
        info = _vram_info()
        return info.get("busy", False)
    return False


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy(request: Request, path: str):
    if _should_block(request):
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": "VRAM занята — идёт генерация изображений/видео. "
                               "Дождитесь окончания или выгрузите модель через /unload.",
                    "type": "vram_busy",
                }
            },
        )

    # Проксируем в Ollama
    url = f"{OLLAMA_URL}/{path}"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length", "connection")}
    body = await request.body()

    try:
        client = httpx.Client(timeout=None)
        req = client.build_request(
            request.method, url, headers=headers, content=body
        )
        upstream = client.send(req, stream=True)

        content_type = upstream.headers.get("content-type", "")
        is_stream = "text/event-stream" in content_type or "application/x-ndjson" in content_type

        if is_stream:
            async def gen():
                try:
                    for chunk in upstream.iter_bytes():
                        yield chunk
                finally:
                    upstream.close()
                    client.close()
            return StreamingResponse(
                gen(),
                status_code=upstream.status_code,
                media_type=content_type,
                headers={"Cache-Control": "no-cache"},
            )

        resp_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in ("content-length", "transfer-encoding", "connection")
        }
        content = upstream.read()
        upstream.close()
        client.close()
        return Response(content=content, status_code=upstream.status_code,
                        headers=resp_headers)
    except httpx.ConnectError:
        raise HTTPException(502, "Ollama недоступен (не запущен на :11434)")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=11435, log_level="warning")
