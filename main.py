"""FastAPI приложение с веб-интерфейсом."""
import csv
import io
import json
import os
import random
import secrets
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, HTMLResponse, Response, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

import config
from store import store, Job, Item, Variant
from task_queue import queue
from downloader import downloader
from llm_store import llm_store, LlmJob, LlmItem
from llm_queue import llm_queue
from ref_store import ref_store, RefJob, RefItem, RefVariant
from ref_queue import ref_queue
from thumb_store import thumb_store, ThumbJob
import thumb_worker
import llm_client
import wp_publish
import nanobanana_client
import comfy_client
import image_search
from upscale_store import upscale_store, UpscaleBatch, UpscaleItem
from generator import generator

app = FastAPI(title="AI ENIX")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ─── Авторизация (веб-форма логина + cookie-сессия) ─────────────────────────

AUTH_USER = os.getenv("FLUX_USER", "kirill")
AUTH_PASSWORD = os.getenv("FLUX_PASSWORD", "")
SESSION_COOKIE = "flux_session"
_sessions = set()  # валидные токены сессий (в памяти)


def _new_session():
    token = secrets.token_hex(32)
    _sessions.add(token)
    return token


class AuthMiddleware(BaseHTTPMiddleware):
    """Пускать только авторизованных (кроме /login, /api/login, /api/logout, /static)."""
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (path.startswith("/login")
                or path == "/api/login"
                or path == "/api/logout"
                or path.startswith("/static")):
            return await call_next(request)
        token = request.cookies.get(SESSION_COOKIE)
        if token not in _sessions:
            if path.startswith("/api/"):
                return Response('{"error":"unauthorized"}', status_code=401,
                                media_type="application/json")
            return RedirectResponse(url="/login", status_code=302)
        return await call_next(request)


app.add_middleware(AuthMiddleware)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.post("/api/login")
async def api_login(request: Request):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    if username == AUTH_USER and password == AUTH_PASSWORD:
        token = _new_session()
        resp = JSONResponse({"ok": True})
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60*60*24*30)
        return resp
    return JSONResponse({"ok": False, "error": "Неверный логин или пароль"}, status_code=401)


@app.post("/api/logout")
async def api_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _sessions.discard(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.on_event("startup")
def startup():
    # Восстанавливаем незавершённые задачи после перезапуска сервера
    queue.resume_all()
    llm_queue.resume_all()
    ref_queue.resume_all()

    # Догружаем незавершённые видео-задачи и продолжаем их дожидаться
    _load_video_jobs()
    import threading as _threading
    for _job_id in list(_video_jobs.keys()):
        _threading.Thread(target=_video_worker, args=(_job_id,), daemon=True).start()

    # Догружаем незавершённые пакеты массового апскейла
    for _b in upscale_store.list_jobs():
        if _b.status == "running":
            _threading.Thread(target=_upscale_worker, args=(_b.id,), daemon=True).start()


# ─── Статус железа сервера ───────────────────────────────────────────────────

def _read_cpu_temp():
    """Температура CPU (°C) — Linux (/sys/class/thermal) или None."""
    try:
        base = Path("/sys/class/thermal")
        for zone in base.iterdir():
            t = (zone / "type").read_text().strip()
            if t in ("x86_pkg_temp", "cpu-thermal", "Tctl", "k10temp", "coretemp"):
                raw = (zone / "temp").read_text().strip()
                val = int(raw)
                return round(val / 1000, 1) if val > 1000 else val
        # fallback: любая thermal zone
        for zone in base.iterdir():
            if (zone / "temp").exists():
                raw = (zone / "temp").read_text().strip()
                val = int(raw)
                return round(val / 1000, 1) if val > 1000 else val
    except Exception:
        pass
    return None


def _read_gpu():
    """Температура/загрузка/VRAM GPU через nvidia-smi, или None."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            timeout=5,
        ).decode("utf-8", errors="replace").strip()
        line = out.splitlines()[0]
        temp, util, vram_used, vram_total = [int(x) for x in line.split(",")]
        return {
            "temp": temp,
            "util": util,
            "vram_used_mb": vram_used,
            "vram_total_mb": vram_total,
        }
    except Exception:
        return None


@app.get("/api/system/stats")
def system_stats():
    """Метрики сервера: CPU, GPU, RAM, диск."""
    import psutil
    import shutil

    cpu_percent = psutil.cpu_percent(interval=0.2)
    cpu_temp = _read_cpu_temp()
    gpu = _read_gpu()

    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = shutil.disk_usage("/")

    # второй диск /data (модели), если смонтирован
    data_disk = None
    if Path("/data").exists():
        try:
            d = shutil.disk_usage("/data")
            data_disk = {
                "used_gb": round(d.used / 1024 / 1024 / 1024, 1),
                "total_gb": round(d.total / 1024 / 1024 / 1024, 1),
                "percent": round(d.used / d.total * 100, 1),
            }
        except OSError:
            data_disk = None

    return {
        "cpu": {
            "percent": cpu_percent,
            "temp": cpu_temp,
            "cores": psutil.cpu_count(logical=True),
        },
        "gpu": gpu,
        "ram": {
            "used_mb": round(mem.used / 1024 / 1024),
            "total_mb": round(mem.total / 1024 / 1024),
            "percent": mem.percent,
        },
        "swap": {
            "used_mb": round(swap.used / 1024 / 1024),
            "total_mb": round(swap.total / 1024 / 1024),
            "percent": swap.percent,
        },
        "disk": {
            "used_gb": round(disk.used / 1024 / 1024 / 1024, 1),
            "total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
            "percent": round(disk.used / disk.total * 100, 1),
        },
        "data_disk": data_disk,
    }


# ─── Статистика для дашборда ────────────────────────────────────────────────

_STATS_FILE = config.BASE_DIR / "stats_history.jsonl"
_STATS_CACHE = {}
_STATS_LOCK = threading.Lock()


def _read_stats_rows():
    rows = []
    if not _STATS_FILE.exists():
        return rows
    try:
        with open(_STATS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        pass
    return rows


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


@app.get("/api/stats/history")
def stats_history(period: str = "day"):
    now = int(time.time())
    with _STATS_LOCK:
        cached = _STATS_CACHE.get(period)
        if cached and cached[0] > now:
            return cached[1]

        if period == "hour":
            span, bucket, fmt = 3600, 60, "%H:%M"
        elif period == "week":
            span, bucket, fmt = 7 * 86400, 3 * 3600, "%d.%m %H:%M"
        else:
            span, bucket, fmt = 86400, 3600, "%H:%M"
        start = now - span

        import shutil as _sh
        root_total = 0.0
        data_total = 0.0
        try:
            root_total = _sh.disk_usage("/").total / 1024 ** 3
            if Path("/data").exists():
                data_total = _sh.disk_usage("/data").total / 1024 ** 3
        except OSError:
            pass

        buckets = {}
        for r in _read_stats_rows():
            ts = r.get("ts")
            if ts is None or ts < start:
                continue
            b = (ts // bucket) * bucket
            d = buckets.setdefault(b, {
                "cpu": [], "gpu": [], "ram": [], "vram": [],
                "disk": [], "data": [], "cpu_temp": [], "gpu_temp": [],
            })
            if r.get("cpu_percent") is not None:
                d["cpu"].append(r["cpu_percent"])
            if r.get("gpu_util") is not None:
                d["gpu"].append(r["gpu_util"])
            if r.get("ram_used_mb") and r.get("ram_total_mb"):
                d["ram"].append(r["ram_used_mb"] / r["ram_total_mb"] * 100)
            if r.get("vram_used_mb") is not None and r.get("vram_total_mb"):
                d["vram"].append(r["vram_used_mb"] / r["vram_total_mb"] * 100)
            if r.get("disk_root_gb") is not None and root_total:
                d["disk"].append(r["disk_root_gb"] / root_total * 100)
            if r.get("disk_data_gb") is not None and data_total:
                d["data"].append(r["disk_data_gb"] / data_total * 100)
            if r.get("cpu_temp") is not None:
                d["cpu_temp"].append(r["cpu_temp"])
            if r.get("gpu_temp") is not None:
                d["gpu_temp"].append(r["gpu_temp"])

        keys = ("cpu", "gpu", "ram", "vram", "disk", "data", "cpu_temp", "gpu_temp")
        labels = []
        series = {k: [] for k in keys}
        for b in sorted(buckets):
            labels.append(time.strftime(fmt, time.localtime(b)))
            for k in keys:
                series[k].append(_avg(buckets[b][k]))

        result = {
            "labels": labels,
            "series": series,
            "totals": {"root_gb": round(root_total, 1), "data_gb": round(data_total, 1)},
        }
        _STATS_CACHE[period] = (now + 60, result)
        return result


_LOG_FILES = {
    "flux": "srv_err.log",
    "flux_out": "srv_out.log",
    "comfy": "comfy_err.log",
    "comfy_out": "comfy_out.log",
}


@app.get("/api/server/logs")
def server_logs(name: str = "flux", lines: int = 200):
    filename = _LOG_FILES.get(name, "srv_err.log")
    path = config.BASE_DIR / filename
    lines = max(10, min(int(lines), 2000))
    if not path.exists():
        return {"name": name, "content": ""}
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
        text = data.decode("utf-8", errors="replace")
        return {"name": name, "content": "\n".join(text.splitlines()[-lines:])}
    except OSError as e:
        return {"name": name, "content": f"Ошибка чтения: {e}"}


@app.get("/api/server/status")
def server_status():
    import platform
    import psutil

    stats = system_stats()
    try:
        loaded = generator.current_model()
    except Exception:
        loaded = None
    stats["loaded_model"] = loaded

    try:
        stats["uptime_sec"] = int(time.time() - psutil.boot_time())
    except Exception:
        stats["uptime_sec"] = None
    stats["hostname"] = platform.node()
    stats["os"] = platform.platform()

    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    stats["cpu"]["model"] = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass

    ollama = []
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        for m in data.get("models", []):
            ollama.append({"name": m.get("name"), "size": m.get("size")})
    except Exception:
        pass
    stats["ollama"] = ollama
    return stats


# ─── Управление сервером ─────────────────────────────────────────────────────

@app.post("/api/server/restart")
def server_restart():
    """Перезапустить веб-сервер (uvicorn). ComfyUI и прокси остаются живыми.

    Лаунчер видит exit-код 42 и поднимает uvicorn заново.
    """
    import os as _os
    import threading

    def _do():
        import time
        time.sleep(1)
        _os._exit(42)

    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "message": "Перезапуск..."}


@app.post("/api/server/stop")
def server_stop():
    """Остановить сервер полностью (flux + ComfyUI + Ollama)."""
    import subprocess
    import threading

    def _do():
        import time
        time.sleep(1)
        subprocess.run(["sudo", "systemctl", "stop", "comfy", "ollama"], check=False)
        subprocess.run(["sudo", "systemctl", "stop", "flux"], check=False)

    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "message": "Остановка сервера..."}


@app.post("/api/server/hard-reset")
def server_hard_reset():
    """Перезагрузить сервер (hard reset)."""
    import subprocess
    import threading

    def _do():
        import time
        time.sleep(1)
        subprocess.run(["sudo", "systemctl", "reboot"], check=False)

    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "message": "Перезагрузка сервера..."}


# ─── Страницы ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"active": "dashboard"})


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request):
    return templates.TemplateResponse(request, "tasks.html", {"active": "tasks"})


@app.get("/server", response_class=HTMLResponse)
def server_page(request: Request):
    return templates.TemplateResponse(request, "server.html", {"active": "server"})


@app.get("/base", response_class=HTMLResponse)
def base_page(request: Request):
    return templates.TemplateResponse(request, "base_placeholder.html", {"active": "base"})


@app.get("/configure/{job_id}", response_class=HTMLResponse)
def configure_page(job_id: str, request: Request):
    return templates.TemplateResponse(request, "configure.html", {"active": "tasks"})


@app.get("/map/{upload_id}", response_class=HTMLResponse)
def map_page(upload_id: str, request: Request):
    return templates.TemplateResponse(request, "map.html", {"active": "tasks"})


@app.get("/result/{job_id}", response_class=HTMLResponse)
def result_page(job_id: str, request: Request):
    return templates.TemplateResponse(request, "result.html", {"active": "tasks"})


@app.get("/llm", response_class=HTMLResponse)
def llm_index(request: Request):
    return templates.TemplateResponse(request, "llm.html", {"active": "llm"})


@app.get("/llm/result/{job_id}", response_class=HTMLResponse)
def llm_result_page(job_id: str, request: Request):
    return templates.TemplateResponse(request, "llm_result.html", {"active": "llm"})


@app.get("/ref/configure/{job_id}", response_class=HTMLResponse)
def ref_configure_page(job_id: str, request: Request):
    return templates.TemplateResponse(request, "ref_configure.html", {"active": "refs"})


@app.get("/ref/map/{upload_id}", response_class=HTMLResponse)
def ref_map_page(upload_id: str, request: Request):
    return templates.TemplateResponse(request, "ref_map.html", {"active": "refs"})


@app.get("/ref/result/{job_id}", response_class=HTMLResponse)
def ref_result_page(job_id: str, request: Request):
    return templates.TemplateResponse(request, "ref_result.html", {"active": "refs"})


@app.get("/thumbs", response_class=HTMLResponse)
def thumbs_page(request: Request):
    return templates.TemplateResponse(request, "thumbs.html", {"active": "thumbs"})


# ─── API: модели ─────────────────────────────────────────────────────────────

@app.get("/api/models")
def list_models():
    result = []
    for mid, info in config.MODELS.items():
        if not info.get("enabled", True):
            continue
        status = downloader.get_status(mid)
        downloaded = downloader.is_downloaded(mid)
        result.append({
            "id": mid,
            "label": info["label"],
            "default_steps": info["default_steps"],
            "video": bool(info.get("video", False)),
            "downloaded": downloaded,
            "download_status": status["status"],
            "download_message": status["message"],
            "download_progress": status["progress"],
        })
    return result


@app.get("/api/nanobanana/models")
def nanobanana_models():
    """Список Nano Banana image-моделей с ценами в кредитах."""
    result = []
    for mid, info in nanobanana_client.NANOBANANA_MODELS.items():
        result.append({
            "id": mid,
            "label": info["label"],
            "credits": info["credits"],
        })
    return {"models": result, "has_key": bool(nanobanana_client.API_KEY)}


@app.post("/api/models/{model_id}/download")
def download_model(model_id: str):
    if model_id not in config.MODELS:
        raise HTTPException(404, "Модель не найдена")
    downloader.download(model_id)
    return {"ok": True, "status": downloader.get_status(model_id)}


# ─── API: задачи ─────────────────────────────────────────────────────────────

import re as _re


def _clean_numeric_id(value):
    """Извлечь только цифры из ID (например 's_3634' -> '3634').

    Используется для колонки filename/ID: у нас ID строго цифровой,
    а в CSV иногда закрадываются префиксы/символы (s_, sku и т.п.).
    """
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    digits = _re.sub(r"\D", "", v)
    return digits or None


def _parse_aspect_ratio(value, max_width=1024, max_height=2048):
    value = (value or "").strip().replace("/", ":")
    if not value:
        return max_width, max_width
    try:
        w, h = value.split(":")
        w = float(w)
        h = float(h)
        if w <= 0 or h <= 0:
            return max_width, max_width
    except (ValueError, AttributeError):
        return max_width, max_width

    width = max_width
    height = round(width * h / w)
    if height > max_height:
        height = max_height
        width = round(height * w / h)
    width = max(8, round(width / 8) * 8)
    height = max(8, round(height / 8) * 8)
    return width, height


def _detect_delimiter(text):
    """Определить разделитель CSV по первой строке."""
    first_line = text.split("\n", 1)[0]
    candidates = [",", ";", "\t", "|"]
    best = ","
    best_count = -1
    for d in candidates:
        c = first_line.count(d)
        if c > best_count:
            best_count = c
            best = d
    return best


@app.post("/api/jobs/upload")
async def upload_csv(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode("utf-8-sig")
    delimiter = _detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    headers = reader.fieldnames or []
    rows = [dict(r) for r in reader]

    if not rows:
        raise HTTPException(400, "CSV пуст")

    upload_id = uuid.uuid4().hex[:12]
    store.add_upload(upload_id, file.filename, headers, rows)
    return {"upload_id": upload_id, "headers": headers, "count": len(rows)}


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str):
    up = store.get_upload(upload_id)
    if up is None:
        raise HTTPException(404, "Загрузка не найдена")
    return {"upload_id": upload_id, "headers": up["headers"], "count": len(up["rows"])}


@app.post("/api/uploads/{upload_id}/create")
async def create_job(upload_id: str, mapping: str = Form(...), logo: UploadFile = File(None)):
    up = store.get_upload(upload_id)
    if up is None:
        raise HTTPException(404, "Загрузка не найдена")

    try:
        mapping = json.loads(mapping or "{}")
    except (ValueError, TypeError):
        mapping = {}
    # {expected_field: source_column}

    def _int(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _float(v, default):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _col(field):
        src = mapping.get(field)
        return src if src else None

    items = []
    for row in up["rows"]:
        def get(field):
            src = _col(field)
            if not src:
                return None
            return row.get(src)

        prompt = (get("prompt") or "").strip()
        if not prompt:
            continue

        width, height = _parse_aspect_ratio(get("aspect_ratio"))
        filename = _clean_numeric_id(get("filename"))

        ref_mode = (get("reference_mode") or "").strip().lower()
        if ref_mode not in ("none", "img2img", "multimodal"):
            ref_mode = "none"
        ref_source = (get("reference_source") or "").strip() or None
        strength = _float(get("strength"), None)

        items.append(Item(
            prompt=prompt,
            filename=filename,
            width=width,
            height=height,
            steps=_int(get("steps"), None) if get("steps") else None,
            guidance=_float(get("guidance_scale"), None) if get("guidance_scale") else None,
            seed=_int(get("seed"), config.DEFAULT_SEED),
            reference_mode=ref_mode,
            reference_source=ref_source,
            strength=strength if strength is not None else 0.7,
        ))

    if not items:
        raise HTTPException(400, "Не найдено строк с промптом (проверьте сопоставление колонки prompt)")

    job = Job(source_name=up["source_name"], items=items)
    store.add_job(job)  # присваивает num, folder_name() становится финальным

    # логотип-референс (опционально): сохраняем в папку задачи
    if logo is not None:
        content = await logo.read()
        if len(content) > 100:
            job_folder = job.folder_name()
            logo_dir = config.OUTPUT_DIR / job_folder
            logo_dir.mkdir(parents=True, exist_ok=True)
            (logo_dir / "logo.png").write_bytes(content)
            job.logo_path = f"{job_folder}/logo.png"
            store.update_job(job)

    store.delete_upload(upload_id)
    return {"id": job.id, "count": len(items)}


@app.get("/api/jobs")
def list_jobs():
    result = []
    for j in store.list_jobs():
        done, total = j.progress()
        result.append({
            "id": j.id,
            "source_name": j.source_name,
            "status": j.status,
            "done": done,
            "total": total,
            "models": j.models,
            "created_at": j.created_at,
        })
    return result


@app.get("/api/tasks")
def list_all_tasks():
    """Объединённый список задач: изображения + тексты."""
    result = []
    for j in store.list_jobs():
        done, total = j.progress()
        result.append({
            "id": j.id,
            "type": "image",
            "source_name": j.source_name,
            "status": j.status,
            "done": done,
            "total": total,
            "models": j.models,
            "created_at": j.created_at,
            "paused": j.paused,
            "eta": j.eta_seconds(),
        })
    for j in llm_store.list_jobs():
        done, total = j.progress()
        result.append({
            "id": j.id,
            "type": "text",
            "source_name": j.source_name,
            "status": j.status,
            "done": done,
            "total": total,
            "models": [],
            "created_at": j.created_at,
            "paused": j.paused,
            "eta": j.eta_seconds(),
        })
    for j in ref_store.list_jobs():
        done, total = j.progress()
        result.append({
            "id": j.id,
            "type": "reference",
            "source_name": j.source_name,
            "status": j.status,
            "done": done,
            "total": total,
            "models": j.modes,
            "created_at": j.created_at,
            "paused": j.paused,
            "eta": j.eta_seconds(),
        })
    for j in upscale_store.list_jobs():
        done, total = j.progress()
        result.append({
            "id": j.id,
            "type": "upscale",
            "source_name": j.source_name,
            "status": j.status,
            "done": done,
            "total": total,
            "models": [],
            "created_at": j.created_at,
            "paused": False,
            "eta": None,
        })
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return result


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return _job_to_dict(job)


@app.get("/api/jobs/{job_id}/progress")
def get_job_progress(job_id: str):
    """Лёгкий статус для опроса (без тяжёлого списка items)."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    done, total = job.progress()
    return {
        "id": job.id,
        "status": job.status,
        "done": done,
        "total": total,
        "eta": job.eta_seconds(),
        "paused": job.paused,
    }


@app.get("/api/jobs/{job_id}/variants")
def get_job_variants(job_id: str):
    """Компактный список вариантов (без текста промптов) для обновления сетки."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    items = []
    for it in job.items:
        items.append({
            "id": it.id,
            "filename": it.filename,
            "approved_variant_id": it.approved_variant_id,
            "variants": [
                {
                    "id": v.id,
                    "model_id": v.model_id,
                    "model_label": config.MODELS[v.model_id]["label"] if v.model_id in config.MODELS else v.model_id,
                    "status": v.status,
                    "filename": v.filename,
                    "error": v.error,
                    "elapsed": v.elapsed,
                }
                for v in it.variants
            ],
        })
    return {"items": items}


@app.post("/api/jobs/{job_id}/configure")
def configure_job(job_id: str, body: dict):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    models = body.get("models", [])
    models = [m for m in models if m in config.MODELS and config.MODELS[m].get("enabled", True)]
    if not models:
        raise HTTPException(400, "Выберите хотя бы одну модель")

    variants = int(body.get("variants", 1))
    variants = max(1, min(4, variants))

    # Референс: настройки по умолчанию для задачи (если в CSV не заданы)
    ref_mode = (body.get("reference_mode") or "none").lower()
    if ref_mode not in ("none", "img2img", "multimodal"):
        ref_mode = "none"
    ref_source = (body.get("reference_source") or "").strip() or None
    strength = float(body.get("strength", 0.7) or 0.7)
    strength = max(0.1, min(1.0, strength))

    job.models = models
    job.variants_per_model = variants

    # Создаём варианты
    for item in job.items:
        # применяем настройки референса на уровень задачи, если не заданы в строке
        if ref_mode != "none" and item.reference_mode == "none":
            item.reference_mode = ref_mode
        if ref_source and not item.reference_source:
            item.reference_source = ref_source
        if ref_mode == "img2img":
            item.strength = strength
        for model_id in models:
            for _ in range(variants):
                item.variants.append(Variant(model_id=model_id, seed=item.seed))

    store.update_job(job)
    return _job_to_dict(job)


@app.post("/api/jobs/{job_id}/run")
def run_job(job_id: str):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    if not job.models:
        raise HTTPException(400, "Сначала настройте модели")
    queue.submit_job(job)
    return _job_to_dict(job)


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    if not queue.pause_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True, "paused": True}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    if not queue.resume_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True, "paused": False}


# ─── Управление очередью (пауза/возобновление) ──────────────────────────────

@app.get("/api/queue/status")
def queue_status():
    # какая модель сейчас реально в VRAM (None, если выгружена по простою)
    now_model = generator.current_model()
    now_label = None
    if now_model and now_model in config.MODELS:
        now_label = config.MODELS[now_model]["label"]

    # суммарное оценочное время завершения всех активных задач
    free_in = 0
    for j in store.list_jobs():
        if j.status in ("queued", "running"):
            e = j.eta_seconds()
            if e:
                free_in += e
    for j in ref_store.list_jobs():
        if j.status in ("queued", "running"):
            e = j.eta_seconds()
            if e:
                free_in += e
    for j in llm_store.list_jobs():
        if j.status in ("queued", "running"):
            e = j.eta_seconds()
            if e:
                free_in += e

    return {
        "paused": queue.is_paused(),
        "now_working": now_model,
        "now_working_label": now_label,
        "free_in": free_in,
    }


@app.post("/api/queue/pause")
def queue_pause():
    queue.pause()
    return {"paused": True}


@app.post("/api/queue/resume")
def queue_resume():
    queue.resume()
    return {"paused": False}


@app.post("/api/jobs/{job_id}/items/{item_id}/regenerate")
def regenerate_item(job_id: str, item_id: str, body: dict):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    item = next((i for i in job.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(404, "Строка не найдена")

    count = int(body.get("count", 1))
    count = max(1, min(4, count))

    for model_id in job.models:
        for _ in range(count):
            v = Variant(model_id=model_id, seed=-1)
            item.variants.append(v)
            queue.submit_variant(job, item, v)

    return _job_to_dict(job)


@app.post("/api/jobs/{job_id}/items/{item_id}/approve")
def approve_item(job_id: str, item_id: str, body: dict):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    item = next((i for i in job.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(404, "Строка не найдена")

    variant_id = body.get("variant_id")
    # Повторный клик по уже выбранному варианту — снимаем выбор
    if item.approved_variant_id == variant_id:
        item.approved_variant_id = None
    else:
        item.approved_variant_id = variant_id
    store.update_job(job)
    return _job_to_dict(job)


@app.post("/api/jobs/{job_id}/approve-all")
def approve_all(job_id: str):
    """Выбрать первый сгенерированный вариант для каждого промпта (без уже выбранного)."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    selected = 0
    for item in job.items:
        if item.approved_variant_id:
            continue
        # первый вариант со статусом done и файлом
        variant = next(
            (v for v in item.variants if v.status == "done" and v.filename),
            None,
        )
        if variant is not None:
            item.approved_variant_id = variant.id
            selected += 1

    store.update_job(job)
    return {"ok": True, "selected": selected}


# ─── Финализация (конвертация в webp) ────────────────────────────────────────

_finalize_status = {}


def _start_finalize(job_id: str, save_originals: bool = False):
    """Запустить финализацию в отдельном потоке с прогрессом."""
    import threading
    import shutil
    from PIL import Image

    job = store.get_job(job_id)
    if job is None:
        return

    fmt = (config.EXPORT_FORMAT or "").lower()
    quality = config.EXPORT_QUALITY

    job_folder = job.folder_name() or f"job_{job.id}"
    images_dir = config.OUTPUT_DIR / job_folder / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    originals_dir = config.OUTPUT_DIR / job_folder / "originals"
    if save_originals:
        originals_dir.mkdir(parents=True, exist_ok=True)

    # собираем список утверждённых вариантов
    items = []
    for item in job.items:
        if not item.approved_variant_id:
            continue
        variant = next((v for v in item.variants if v.id == item.approved_variant_id), None)
        if variant is not None and variant.filename:
            items.append((item, variant))

    total = len(items)
    status = {
        "running": True,
        "done": 0,
        "total": total,
        "copied": 0,
        "converted": 0,
        "originals": 0,
        "errors": 0,
        "finished": False,
        "format": fmt,
        "quality": quality,
        "save_originals": save_originals,
    }
    _finalize_status[job_id] = status

    def worker():
        used_names = {}
        for item, variant in items:
            src = config.OUTPUT_DIR / variant.filename
            # Уникальное имя: при повторяющемся filename/ID добавляем суффикс,
            # чтобы 25 промптов с одинаковыми ID не затирались в 4 файла.
            base_name = item.filename or item.id
            used_names[base_name] = used_names.get(base_name, 0) + 1
            n = used_names[base_name]
            item_id_name = base_name if n == 1 else f"{base_name}-{n}"

            if src.exists():
                # сохранение исходника (PNG)
                if save_originals:
                    try:
                        orig_dst = originals_dir / f"{item_id_name}.png"
                        if src.suffix.lower() == ".png":
                            shutil.copy2(src, orig_dst)
                        else:
                            with Image.open(src) as im:
                                im.save(orig_dst, "PNG")
                        status["originals"] += 1
                    except Exception:
                        pass

                if fmt in ("webp", "jpeg"):
                    dst = images_dir / f"{item_id_name}.{fmt}"
                    try:
                        with Image.open(src) as im:
                            im = im.convert("RGB")
                            save_kwargs = {"quality": quality}
                            if fmt == "webp":
                                save_kwargs["method"] = 6
                            else:
                                save_kwargs["optimize"] = True
                            im.save(dst, fmt.upper(), **save_kwargs)
                        status["converted"] += 1
                    except Exception:
                        try:
                            dst = images_dir / f"{item_id_name}.png"
                            shutil.copy2(src, dst)
                            status["copied"] += 1
                        except Exception:
                            status["errors"] += 1
                else:
                    dst = images_dir / f"{item_id_name}.png"
                    try:
                        shutil.copy2(src, dst)
                        status["copied"] += 1
                    except Exception:
                        status["errors"] += 1
            else:
                status["errors"] += 1

            status["done"] += 1

        status["running"] = False
        status["finished"] = True

    threading.Thread(target=worker, daemon=True).start()


@app.post("/api/jobs/{job_id}/finalize")
def finalize_job(job_id: str, body: dict = None):
    """Запустить финализацию в фоне (конвертация PNG -> webp/jpeg)."""
    body = body or {}
    save_originals = bool(body.get("save_originals", False))
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    if job_id in _finalize_status and _finalize_status[job_id].get("running"):
        return {"ok": True, "already_running": True}
    _start_finalize(job_id, save_originals=save_originals)
    return {"ok": True, "started": True}


@app.get("/api/jobs/{job_id}/finalize/status")
def finalize_status(job_id: str):
    """Текущий прогресс финализации + счётчики готовых файлов."""
    st = _finalize_status.get(job_id, {"running": False, "done": 0, "total": 0})

    # сколько файлов в папке images (готовых к публикации)
    job = store.get_job(job_id)
    images_count = 0
    approved_count = 0
    if job is not None:
        # считаем УНИКАЛЬНЫЕ ID утверждённых (в CSV бывают дубликаты строк)
        unique_ids = set()
        for it in job.items:
            if it.approved_variant_id:
                unique_ids.add(it.filename or it.id)
        approved_count = len(unique_ids)
        job_folder = job.folder_name() or f"job_{job.id}"
        images_dir = config.OUTPUT_DIR / job_folder / "images"
        if images_dir.exists():
            images_count = sum(1 for f in images_dir.iterdir() if f.is_file())

    result = dict(st)
    result["images_count"] = images_count
    result["approved_count"] = approved_count
    result["ready_to_publish"] = (
        not st.get("running", False)
        and images_count > 0
        and images_count >= approved_count
    )
    return result


@app.get("/api/jobs/{job_id}/zip")
def zip_job_images(job_id: str):
    """Скачать ZIP-архив со всеми сохранёнными изображениями (images/*)."""
    import shutil

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    job_folder = job.folder_name() or f"job_{job_id}"
    images_dir = config.OUTPUT_DIR / job_folder / "images"
    if not images_dir.exists() or not any(f.is_file() for f in images_dir.iterdir()):
        raise HTTPException(404, "Нет сохранённых изображений (сначала нажмите «Утвердить выбранные»)")

    zip_tmp_dir = config.OUTPUT_DIR / ".zip_tmp"
    zip_tmp_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{job_folder}_webp"
    archive_path = zip_tmp_dir / f"{base_name}_{uuid.uuid4().hex[:8]}"
    # НЕ with_suffix: в имени папки может быть точка (напр. «.москва»), with_suffix отрежет всё после неё.
    final_zip = Path(str(archive_path) + ".zip")

    try:
        shutil.make_archive(str(archive_path), "zip", root_dir=images_dir)

        from starlette.background import BackgroundTask

        def _cleanup():
            try:
                if final_zip.exists():
                    final_zip.unlink()
            except OSError:
                pass

        return FileResponse(
            final_zip,
            filename=f"{base_name}.zip",
            background=BackgroundTask(_cleanup),
        )
    except Exception as e:
        raise HTTPException(500, f"Ошибка архивации: {e}")


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    # У каждой задачи своя уникальная папка (номер задачи), удаляем её.
    import shutil
    job_folder = job.folder_name() or f"job_{job_id}"
    results_dir = config.OUTPUT_DIR / job_folder
    if results_dir.exists():
        shutil.rmtree(results_dir, ignore_errors=True)

    # очищаем статус финализации
    _finalize_status.pop(job_id, None)

    if not store.delete_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True}


@app.get("/api/output/{filename:path}")
def get_output(filename: str):
    path = config.OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(path)


@app.get("/api/thumb/{filename:path}")
def get_thumb(filename: str, size: int = 400):
    """Уменьшенная копия изображения (thumbnail) для превью — экономит трафик."""
    from PIL import Image
    import io as _io

    path = config.OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Файл не найден")

    size = max(64, min(1024, size))
    # кэш-файл рядом с оригиналом (в скрытой папке .thumbs)
    thumb_dir = config.OUTPUT_DIR / ".thumbs"
    thumb_path = thumb_dir / f"{size}_{filename}"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)

    if not thumb_path.exists() or thumb_path.stat().st_mtime < path.stat().st_mtime:
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((size, size))
                thumb_path.parent.mkdir(parents=True, exist_ok=True)
                im.save(thumb_path, "JPEG", quality=70, optimize=True, progressive=True)
        except Exception:
            # если не удалось — отдаём оригинал
            return FileResponse(path)

    return FileResponse(thumb_path, media_type="image/jpeg")


# ─── Сериализация ────────────────────────────────────────────────────────────

def _job_to_dict(job):
    done, total = job.progress()
    return {
        "id": job.id,
        "source_name": job.source_name,
        "status": job.status,
        "models": job.models,
        "variants_per_model": job.variants_per_model,
        "done": done,
        "total": total,
        "created_at": job.created_at,
        "error": job.error,
        "paused": job.paused,
        "eta": job.eta_seconds(),
        "items": [
            {
                "id": it.id,
                "prompt": it.prompt,
                "filename": it.filename,
                "width": it.width,
                "height": it.height,
                "approved_variant_id": it.approved_variant_id,
                "reference_mode": it.reference_mode,
                "reference_source": it.reference_source,
                "strength": it.strength,
                "variants": [
                    {
                        "id": v.id,
                        "model_id": v.model_id,
                        "model_label": config.MODELS[v.model_id]["label"],
                        "seed": v.seed,
                        "status": v.status,
                        "filename": v.filename,
                        "error": v.error,
                        "elapsed": v.elapsed,
                    }
                    for v in it.variants
                ],
            }
            for it in job.items
        ],
    }


# ─── LLM: генерация описаний ────────────────────────────────────────────────

def _llm_job_to_dict(job):
    done, total = job.progress()
    return {
        "id": job.id,
        "source_name": job.source_name,
        "status": job.status,
        "done": done,
        "total": total,
        "created_at": job.created_at,
        "paused": job.paused,
        "eta": job.eta_seconds(),
        "items": [
            {
                "id": it.id,
                "item_id": it.item_id,
                "title": it.title,
                "excerpt": it.excerpt,
                "content": it.content,
                "new_excerpt": it.new_excerpt,
                "new_content": it.new_content,
                "status": it.status,
                "error": it.error,
                "elapsed": it.elapsed,
            }
            for it in job.items
        ],
    }


@app.post("/api/llm/upload")
async def llm_upload(file: UploadFile = File(...), model: str = "deepseek"):
    content = await file.read()
    text = content.decode("utf-8-sig")
    delimiter = _detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    # model приходит как query-параметр (frontend шлёт в FormData)
    if model not in ("deepseek", "local", "ollama_cloud"):
        model = "deepseek"

    items = []
    for row in reader:
        item_id = (row.get("ID") or row.get("id") or "").strip()
        title = (row.get("post_title") or row.get("title") or "").strip()
        excerpt = (row.get("post_excerpt") or row.get("excerpt") or "").strip() or None
        content_full = (row.get("post_content") or row.get("content") or "").strip() or None

        if not title and not item_id:
            continue

        items.append(LlmItem(
            item_id=item_id,
            title=title,
            excerpt=excerpt,
            content=content_full,
        ))

    if not items:
        raise HTTPException(400, "CSV не содержит строк с товарами")

    job = LlmJob(source_name=file.filename, items=items, model=model)
    llm_store.add_job(job)
    return {"id": job.id, "count": len(items)}


@app.get("/api/llm/jobs")
def llm_list_jobs():
    result = []
    for j in llm_store.list_jobs():
        done, total = j.progress()
        result.append({
            "id": j.id,
            "source_name": j.source_name,
            "status": j.status,
            "done": done,
            "total": total,
            "created_at": j.created_at,
        })
    return result


@app.get("/api/llm/jobs/{job_id}")
def llm_get_job(job_id: str):
    job = llm_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return _llm_job_to_dict(job)


@app.post("/api/llm/jobs/{job_id}/run")
def llm_run_job(job_id: str):
    job = llm_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    llm_queue.submit_job(job)
    return _llm_job_to_dict(job)


@app.post("/api/llm/jobs/{job_id}/pause")
def llm_pause_job(job_id: str):
    if not llm_queue.pause_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True, "paused": True}


@app.post("/api/llm/jobs/{job_id}/resume")
def llm_resume_job(job_id: str):
    if not llm_queue.resume_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True, "paused": False}


@app.delete("/api/llm/jobs/{job_id}")
def llm_delete_job(job_id: str):
    if not llm_store.delete_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True}


@app.get("/api/llm/jobs/{job_id}/export")
def llm_export(job_id: str):
    job = llm_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    import urllib.parse
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["ID", "post_title", "post_excerpt", "post_content"])
    for it in job.items:
        writer.writerow([
            it.item_id,
            it.title,
            it.new_excerpt or "",
            it.new_content or "",
        ])

    data = out.getvalue().encode("utf-8-sig")

    # Имя файла для заголовка Content-Disposition. Кириллицу в HTTP-заголовке
    # Starlette кодирует в latin-1 → UnicodeEncodeError (500). Поэтому отдаём
    # ASCII-fallback + RFC 5987 (filename*=UTF-8'') для нормальных браузеров.
    fname = f"{job.source_name}_rewritten.csv"
    ascii_name = wp_publish.translit(job.source_name or "descriptions")
    ascii_name = "".join(ch if ord(ch) < 128 and ch not in '"\\' else "_" for ch in ascii_name)
    ascii_name = ascii_name.replace(" ", "_").strip("._") or "descriptions"
    ascii_fname = f"{ascii_name}_rewritten.csv"
    quoted = urllib.parse.quote(fname)
    content_disposition = (
        'attachment; filename="' + ascii_fname + '"; filename*=UTF-8\'\'' + quoted
    )

    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": content_disposition},
    )


@app.get("/api/llm/status")
def llm_status():
    return llm_queue.get_status()


@app.post("/api/llm/pause")
def llm_pause():
    llm_queue.pause()
    return {"paused": True}


@app.post("/api/llm/resume")
def llm_resume():
    llm_queue.resume()
    return {"paused": False}


# ─── Генерация по референсам ───────────────────────────────────────────────

def _parse_ref_csv_rows(rows, mapping=None):
    """Разобрать строки CSV референсов по сопоставлению колонок.

    mapping: {expected_field: source_column}. Если None — авто-поиск по именам.
    expected: title (post_title), id, image, type.
    """
    def _get(row, *names):
        for n in names:
            for k, v in row.items():
                if (k or "").strip().lower() == n:
                    return (v or "").strip()
        return ""

    def _col(field):
        if mapping:
            return mapping.get(field)
        return None

    items = []
    for row in rows:
        # сопоставление: либо из mapping, либо авто-поиск по именам
        if mapping:
            title = (row.get(_col("title")) or "").strip()
            item_id = _clean_numeric_id(row.get(_col("id")))
            image_url = (row.get(_col("image")) or "").strip() or None
            rtype = (row.get(_col("type")) or "").strip().lower() or "standart"
        else:
            title = _get(row, "post_title", "title")
            item_id = _clean_numeric_id(_get(row, "id"))
            image_url = _get(row, "image") or None
            rtype = (_get(row, "type") or "standart").strip().lower()

        if not title and not item_id:
            continue

        # all_product -> storage + standart
        expand = config.REF_TYPE_MULTI.get(rtype, [rtype])

        for t in expand:
            info = config.REF_TYPES.get(t)
            if info is None:
                continue
            it = RefItem(
                title=title,
                item_id=item_id,
                image_url=image_url,
                ref_type=t,
                width=info["width"],
                height=info["height"],
            )
            items.append(it)

    return items


@app.post("/api/ref/upload")
async def ref_upload(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode("utf-8-sig")
    delimiter = _detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = reader.fieldnames or []
    rows = [dict(r) for r in reader]

    if not rows:
        raise HTTPException(400, "CSV пуст")

    upload_id = uuid.uuid4().hex[:12]
    store.add_upload(upload_id, file.filename, headers, rows)
    return {"upload_id": upload_id, "headers": headers, "count": len(rows)}


@app.get("/api/ref/uploads/{upload_id}")
def ref_get_upload(upload_id: str):
    up = store.get_upload(upload_id)
    if up is None:
        raise HTTPException(404, "Загрузка не найдена")
    return {"upload_id": upload_id, "headers": up["headers"], "count": len(up["rows"])}


@app.post("/api/ref/uploads/{upload_id}/create")
def ref_create_job(upload_id: str, body: dict):
    up = store.get_upload(upload_id)
    if up is None:
        raise HTTPException(404, "Загрузка не найдена")

    mapping = body.get("mapping", {}) or {}
    items = _parse_ref_csv_rows(up["rows"], mapping=mapping or None)
    if not items:
        raise HTTPException(400, "Не найдено строк (проверьте сопоставление колонок title/ID)")

    job = RefJob(source_name=up["source_name"], items=items)
    ref_store.add_job(job)
    store.delete_upload(upload_id)
    return {"id": job.id, "count": len(items)}


@app.get("/api/ref/jobs")
def ref_list_jobs():
    result = []
    for j in ref_store.list_jobs():
        done, total = j.progress()
        result.append({
            "id": j.id,
            "source_name": j.source_name,
            "status": j.status,
            "done": done,
            "total": total,
            "modes": j.modes,
            "created_at": j.created_at,
            "paused": j.paused,
            "eta": j.eta_seconds(),
        })
    return result


@app.get("/api/ref/jobs/{job_id}")
def ref_get_job(job_id: str):
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return _ref_job_to_dict(job)


@app.get("/api/ref/jobs/{job_id}/progress")
def ref_get_progress(job_id: str):
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    done, total = job.progress()
    return {
        "id": job.id,
        "status": job.status,
        "done": done,
        "total": total,
        "eta": job.eta_seconds(),
        "paused": job.paused,
    }


@app.post("/api/ref/jobs/{job_id}/configure")
def ref_configure(job_id: str, body: dict):
    """Настроить задачу: site_name, режимы (img2img/multimodal), strength.

    Применяет настройки и создаёт варианты (по одному на каждый выбранный режим).
    """
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    site_name = (body.get("site_name") or "").strip()
    modes = body.get("modes", [])
    modes = [m for m in modes if m in ("img2img", "multimodal")]
    if not modes:
        raise HTTPException(400, "Выберите хотя бы один режим (img2img или multimodal)")

    try:
        strength = float(body.get("strength", 0.5) or 0.5)
    except (TypeError, ValueError):
        strength = 0.5
    strength = max(0.1, min(1.0, strength))

    # количество копий на режим (по умолчанию 1 на каждый)
    copies = body.get("copies", {}) or {}
    copies_map = {}
    for mode in modes:
        try:
            c = int(copies.get(mode, 1))
        except (TypeError, ValueError):
            c = 1
        copies_map[mode] = max(1, min(9, c))

    job.site_name = site_name
    job.modes = modes
    job.copies = copies_map
    job.strength = strength
    job.watermark = bool(body.get("watermark", False))

    # Привязка режима к модели
    for item in job.items:
        # строим промпт по шаблону типа (или по правилу Watermark)
        if job.watermark:
            item.prompt = config.REF_WATERMARK_RULE
        else:
            info = config.REF_TYPES.get(item.ref_type, {})
            tpl = info.get("prompt", "")
            item.prompt = tpl.format(title=item.title or "", site=site_name or "")

        # имя выходного файла: ID + суффикс типа (чтобы all_product не пересекался)
        base = item.item_id or item.title or item.id
        item.output_name = f"{base}-{item.ref_type}"

        # варианты: copies на каждый режим
        for mode in modes:
            model_id = "flux2-klein-4b"
            for _ in range(copies_map.get(mode, 1)):
                item.variants.append(RefVariant(
                    model_id=model_id,
                    reference_mode=mode,
                    strength=strength,
                ))

    ref_store.update_job(job)
    return _ref_job_to_dict(job)


@app.post("/api/ref/jobs/{job_id}/overlay")
async def ref_upload_overlay(job_id: str, file: UploadFile = File(...)):
    """Загрузить PNG-водяной знак, который накладывается поверх каждого сгенерированного изображения."""
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    content = await file.read()
    if len(content) < 100:
        raise HTTPException(400, "Файл пуст или повреждён")

    # сохраняем PNG в папку задачи
    job_folder = job.folder_name() or f"refjob_{job.id}"
    overlay_dir = config.OUTPUT_DIR / job_folder
    overlay_dir.mkdir(parents=True, exist_ok=True)
    overlay_file = overlay_dir / "overlay.png"
    overlay_file.write_bytes(content)

    # проверяем, что это валидное изображение
    from PIL import Image
    import io as _io
    try:
        with Image.open(_io.BytesIO(content)) as im:
            im.verify()
    except Exception:
        overlay_file.unlink(missing_ok=True)
        raise HTTPException(400, "Файл не является корректным изображением")

    job.overlay_path = f"{job_folder}/overlay.png"
    ref_store.update_job(job)
    return {"ok": True, "overlay_path": job.overlay_path}


@app.post("/api/ref/jobs/{job_id}/overlay/remove")
def ref_remove_overlay(job_id: str):
    """Убрать PNG-водяной знак."""
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    if job.overlay_path:
        p = config.OUTPUT_DIR / job.overlay_path
        if p.exists():
            p.unlink(missing_ok=True)
    job.overlay_path = None
    ref_store.update_job(job)
    return {"ok": True}


@app.post("/api/ref/jobs/{job_id}/run")
def ref_run(job_id: str):
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    if not job.modes:
        raise HTTPException(400, "Сначала настройте режимы")
    ref_queue.submit_job(job)
    return _ref_job_to_dict(job)


@app.post("/api/ref/jobs/{job_id}/pause")
def ref_pause(job_id: str):
    if not ref_queue.pause_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True, "paused": True}


@app.post("/api/ref/jobs/{job_id}/resume")
def ref_resume(job_id: str):
    if not ref_queue.resume_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True, "paused": False}


@app.post("/api/ref/jobs/{job_id}/items/{item_id}/regenerate")
def ref_regenerate(job_id: str, item_id: str, body: dict):
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    item = next((i for i in job.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(404, "Строка не найдена")

    count = int(body.get("count", 1))
    count = max(1, min(4, count))

    for mode in job.modes:
        model_id = "flux2-klein-4b"
        for _ in range(count):
            v = RefVariant(model_id=model_id, reference_mode=mode, strength=job.strength)
            item.variants.append(v)
            ref_queue.submit_variant(job, item, v)

    return _ref_job_to_dict(job)


@app.post("/api/ref/jobs/{job_id}/items/{item_id}/approve")
def ref_approve(job_id: str, item_id: str, body: dict):
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    item = next((i for i in job.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(404, "Строка не найдена")

    variant_id = body.get("variant_id")
    if item.approved_variant_id == variant_id:
        item.approved_variant_id = None
    else:
        item.approved_variant_id = variant_id
    ref_store.update_job(job)
    return _ref_job_to_dict(job)


@app.post("/api/ref/jobs/{job_id}/approve-all")
def ref_approve_all(job_id: str):
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    selected = 0
    for item in job.items:
        if item.approved_variant_id:
            continue
        variant = next((v for v in item.variants if v.status == "done" and v.filename), None)
        if variant is not None:
            item.approved_variant_id = variant.id
            selected += 1

    ref_store.update_job(job)
    return {"ok": True, "selected": selected}


@app.get("/api/ref/jobs/{job_id}/variants")
def ref_get_variants(job_id: str):
    """Компактный список вариантов для обновления сетки."""
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    items = []
    for it in job.items:
        items.append({
            "id": it.id,
            "title": it.title,
            "ref_type": it.ref_type,
            "output_name": it.output_name,
            "source_path": it.source_path,
            "source_site": it.source_site,
            "source_fetched_at": it.source_fetched_at,
            "approved_variant_id": it.approved_variant_id,
            "variants": [
                {
                    "id": v.id,
                    "model_id": v.model_id,
                    "model_label": config.MODELS[v.model_id]["label"] if v.model_id in config.MODELS else v.model_id,
                    "reference_mode": v.reference_mode,
                    "status": v.status,
                    "filename": v.filename,
                    "error": v.error,
                    "elapsed": v.elapsed,
                }
                for v in it.variants
            ],
        })
    return {"items": items}


def _ref_job_to_dict(job):
    done, total = job.progress()
    return {
        "id": job.id,
        "source_name": job.source_name,
        "status": job.status,
        "site_name": job.site_name,
        "modes": job.modes,
        "copies": job.copies,
        "strength": job.strength,
        "watermark": job.watermark,
        "overlay_path": job.overlay_path,
        "done": done,
        "total": total,
        "created_at": job.created_at,
        "paused": job.paused,
        "eta": job.eta_seconds(),
        "items": [
            {
                "id": it.id,
                "title": it.title,
                "item_id": it.item_id,
                "image_url": it.image_url,
                "ref_type": it.ref_type,
                "width": it.width,
                "height": it.height,
                "output_name": it.output_name,
                "source_path": it.source_path,
                "source_site": it.source_site,
                "source_fetched_at": it.source_fetched_at,
                "prompt": it.prompt,
                "approved_variant_id": it.approved_variant_id,
                "variants": [
                    {
                        "id": v.id,
                        "model_id": v.model_id,
                        "model_label": config.MODELS[v.model_id]["label"] if v.model_id in config.MODELS else v.model_id,
                        "reference_mode": v.reference_mode,
                        "seed": v.seed,
                        "status": v.status,
                        "filename": v.filename,
                        "error": v.error,
                        "elapsed": v.elapsed,
                    }
                    for v in it.variants
                ],
            }
            for it in job.items
        ],
    }


# ─── Финализация референсов (images/png + images/webp) ─────────────────────

_ref_finalize_status = {}


def _start_ref_finalize(job_id: str):
    import threading
    import shutil
    from PIL import Image

    job = ref_store.get_job(job_id)
    if job is None:
        return

    job_folder = job.folder_name() or f"refjob_{job.id}"
    base = config.OUTPUT_DIR / job_folder
    png_dir = base / "images" / "png"
    webp_dir = base / "images" / "webp"
    png_dir.mkdir(parents=True, exist_ok=True)
    webp_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for item in job.items:
        if not item.approved_variant_id:
            continue
        variant = next((v for v in item.variants if v.id == item.approved_variant_id), None)
        if variant is not None and variant.filename:
            items.append((item, variant))

    total = len(items)
    status = {
        "running": True, "done": 0, "total": total,
        "converted": 0, "copied": 0, "errors": 0, "finished": False,
    }
    _ref_finalize_status[job_id] = status

    def worker():
        for item, variant in items:
            src = config.OUTPUT_DIR / variant.filename
            name = item.output_name or item.item_id or item.id

            if src.exists():
                # PNG — исходник
                try:
                    dst_png = png_dir / f"{name}.png"
                    if src.suffix.lower() == ".png":
                        shutil.copy2(src, dst_png)
                    else:
                        with Image.open(src) as im:
                            im.save(dst_png, "PNG")
                    status["copied"] += 1
                except Exception:
                    status["errors"] += 1

                # WebP — готовое к загрузке
                try:
                    dst_webp = webp_dir / f"{name}.webp"
                    with Image.open(src) as im:
                        im = im.convert("RGB")
                        im.save(dst_webp, "WEBP", quality=config.EXPORT_QUALITY, method=6)
                    status["converted"] += 1
                except Exception:
                    status["errors"] += 1
            else:
                status["errors"] += 1

            status["done"] += 1

        status["running"] = False
        status["finished"] = True

    threading.Thread(target=worker, daemon=True).start()


@app.post("/api/ref/jobs/{job_id}/finalize")
def ref_finalize(job_id: str):
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    if job_id in _ref_finalize_status and _ref_finalize_status[job_id].get("running"):
        return {"ok": True, "already_running": True}
    _start_ref_finalize(job_id)
    return {"ok": True, "started": True}


@app.get("/api/ref/jobs/{job_id}/finalize/status")
def ref_finalize_status(job_id: str):
    return _ref_finalize_status.get(job_id, {"running": False, "done": 0, "total": 0, "finished": False})


@app.delete("/api/ref/jobs/{job_id}")
def ref_delete_job(job_id: str):
    job = ref_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    import shutil
    job_folder = job.folder_name() or f"refjob_{job_id}"
    results_dir = config.OUTPUT_DIR / job_folder
    if results_dir.exists():
        shutil.rmtree(results_dir, ignore_errors=True)

    _ref_finalize_status.pop(job_id, None)

    if not ref_store.delete_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True}


# ─── Миниатюры категорий (WP/WooCommerce) ──────────────────────────────────


def _thumb_job_to_dict(job):
    done, total = job.progress()
    return {
        "id": job.id,
        "site": job.site,
        "source_name": job.source_name,
        "status": job.status,
        "done": done,
        "total": total,
        "created_at": job.created_at,
        "paused": job.paused,
        "error": job.error,
        "items": [
            {
                "id": it.id,
                "category_id": it.category_id,
                "category_name": it.category_name,
                "category_slug": it.category_slug,
                "product_id": it.product_id,
                "product_name": it.product_name,
                "product_image_url": it.product_image_url,
                "status": it.status,
                "filename": it.filename,
                "media_id": it.media_id,
                "error": it.error,
                "elapsed": it.elapsed,
            }
            for it in job.items
        ],
    }


@app.post("/api/thumbs/fetch")
def thumbs_fetch(body: dict):
    """Создать задачу миниатюр: получить категории и товары-референсы."""
    site = (body.get("site") or "").strip()
    if not site:
        raise HTTPException(400, "Адрес сайта не задан")
    if not (body.get("wc_key") and body.get("wc_secret")):
        raise HTTPException(400, "Укажите WooCommerce Consumer Key / Secret")
    if not (body.get("wp_user") and body.get("wp_app_password")):
        raise HTTPException(400, "Укажите WordPress логин / Application Password")

    job = ThumbJob(source_name=site, site=site)
    thumb_store.add_job(job)
    thumb_worker.start_fetch(job.id, body)
    return {"id": job.id}


@app.get("/api/thumbs/jobs")
def thumbs_list_jobs():
    result = []
    for j in thumb_store.list_jobs():
        done, total = j.progress()
        result.append({
            "id": j.id,
            "site": j.site,
            "status": j.status,
            "done": done,
            "total": total,
            "created_at": j.created_at,
        })
    return result


@app.get("/api/thumbs/{job_id}")
def thumbs_get_job(job_id: str):
    job = thumb_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return _thumb_job_to_dict(job)


@app.get("/api/thumbs/{job_id}/progress")
def thumbs_progress(job_id: str):
    job = thumb_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    done, total = job.progress()
    return {"id": job.id, "status": job.status, "done": done, "total": total,
            "error": job.error}


@app.post("/api/thumbs/{job_id}/run")
def thumbs_run(job_id: str, body: dict):
    """Запустить генерацию и загрузку миниатюр (доступы передаются заново)."""
    job = thumb_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    if job.status == "running":
        return {"ok": True, "already_running": True}
    if job.status != "ready":
        raise HTTPException(400, "Категории ещё не получены")
    if not (body.get("wc_key") and body.get("wc_secret")):
        raise HTTPException(400, "Укажите WooCommerce Consumer Key / Secret")
    if not (body.get("wp_user") and body.get("wp_app_password")):
        raise HTTPException(400, "Укажите WordPress логин / Application Password")

    thumb_worker.start_run(job_id, body)
    return {"ok": True, "started": True}


@app.delete("/api/thumbs/{job_id}")
def thumbs_delete_job(job_id: str):
    job = thumb_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    import shutil
    job_folder = job.folder_name() or f"thumbs_{job_id}"
    results_dir = config.OUTPUT_DIR / job_folder
    if results_dir.exists():
        shutil.rmtree(results_dir, ignore_errors=True)

    if not thumb_store.delete_job(job_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True}


# ─── Генерация по единому промпту ──────────────────────────────────────────

_single_jobs = {}  # id -> dict (in-memory, теряется при рестарте)


def _single_slug(text, limit=40):
    import re as _re
    t = _re.sub(r'[\\/:*?"<>|]', "", text or "")
    t = _re.sub(r"\s+", "_", t.strip())
    return t[:limit].strip("._") or "image"


def _cleanup_single_temp():
    """Удалить temp-папки старше 24 часов."""
    import shutil
    import time as _time
    temp_base = config.OUTPUT_DIR / "temp"
    if not temp_base.exists():
        return
    now = _time.time()
    for d in temp_base.iterdir():
        if not d.is_dir():
            continue
        try:
            if now - d.stat().st_mtime > 24 * 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


@app.get("/single", response_class=HTMLResponse)
def single_page(request: Request):
    return templates.TemplateResponse(request, "single.html", {"active": "single"})


@app.post("/api/single/generate")
def single_generate(body: dict):
    import threading as _threading
    import time as _time

    prompt = (body.get("prompt") or "").strip()
    models = body.get("models") or []
    if not prompt:
        raise HTTPException(400, "Введите промпт")

    local_models = [m for m in models
                    if m in config.MODELS and not config.MODELS[m].get("video")]
    nb_models = [m for m in models if m in nanobanana_client.NANOBANANA_MODELS]
    all_models = []
    for m in models:
        if (m in local_models or m in nb_models) and m not in all_models:
            all_models.append(m)
    if not all_models:
        raise HTTPException(400, "Выберите хотя бы одну модель")

    _cleanup_single_temp()

    aspect_ratio = body.get("aspect_ratio") or "1:1"
    resolution = body.get("resolution") or "2k"
    width, height = _parse_aspect_ratio(aspect_ratio, 1024, 2048)
    seed = body.get("seed")

    def _label(mid):
        if mid in config.MODELS:
            return config.MODELS[mid]["label"]
        return nanobanana_client.NANOBANANA_MODELS[mid]["label"]

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "prompt": prompt,
        "status": "running",
        "created_at": _time.time(),
        "saved": False,
        "models": [
            {"model_id": m, "label": _label(m),
             "status": "pending", "filename": None, "error": None}
            for m in all_models
        ],
    }
    _single_jobs[job_id] = job

    def worker():
        temp_dir = config.OUTPUT_DIR / "temp" / job_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        for m in job["models"]:
            model_id = m["model_id"]
            m["status"] = "running"
            try:
                if model_id in nanobanana_client.NANOBANANA_MODELS:
                    urls, credits = nanobanana_client.generate(
                        model_id, prompt, aspect_ratio=aspect_ratio, resolution=resolution)
                    if not urls:
                        raise RuntimeError("Nano Banana: пустой результат")
                    ext = ".jpeg" if urls[0].lower().endswith((".jpg", ".jpeg")) else ".png"
                    out_path = temp_dir / f"{model_id}{ext}"
                    nanobanana_client.download_image(urls[0], out_path)
                    m["filename"] = f"temp/{job_id}/{out_path.name}"
                    m["status"] = "done"
                else:
                    info = config.MODELS[model_id]
                    out_path = temp_dir / f"{model_id}.png"
                    generator.generate(
                        prompt, model_id, width, height,
                        info["default_steps"], info.get("guidance_scale", 1.0), seed,
                        out_path,
                    )
                    m["filename"] = f"temp/{job_id}/{out_path.name}"
                    m["status"] = "done"
            except Exception as e:
                m["status"] = "error"
                m["error"] = str(e)
        job["status"] = "done"

    _threading.Thread(target=worker, daemon=True).start()
    return {"id": job_id}


@app.get("/api/single/{job_id}")
def single_get(job_id: str):
    job = _single_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return job


@app.post("/api/single/{job_id}/save")
def single_save(job_id: str):
    import shutil

    job = _single_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    if job["status"] != "done":
        raise HTTPException(400, "Генерация ещё не завершена")

    save_dir = config.OUTPUT_DIR / "Single Image"
    save_dir.mkdir(parents=True, exist_ok=True)
    slug = _single_slug(job["prompt"])
    saved = []
    for m in job["models"]:
        if m["status"] != "done" or not m["filename"]:
            continue
        src = config.OUTPUT_DIR / m["filename"]
        if not src.exists():
            continue
        ext = src.suffix or ".png"
        dst = save_dir / f"{slug}_{m['model_id']}{ext}"
        i = 1
        while dst.exists():
            dst = save_dir / f"{slug}_{m['model_id']}_{i}{ext}"
            i += 1
        shutil.move(str(src), str(dst))
        m["filename"] = f"Single Image/{dst.name}"
        saved.append(m["filename"])
    job["saved"] = True
    return {"ok": True, "saved": saved}


# ─── Upscale (повышение чёткости картинки) ────────────────────────────────

_upscale_jobs = {}
UPSCALE_MODELS = ("flux2-klein-4b", "krea2-turbo")


@app.get("/upscale", response_class=HTMLResponse)
def upscale_page(request: Request):
    return templates.TemplateResponse(request, "upscale.html", {"active": "upscale"})


@app.get("/upscale/{batch_id}", response_class=HTMLResponse)
def upscale_batch_page(batch_id: str, request: Request):
    return templates.TemplateResponse(request, "upscale.html", {"active": "upscale"})


@app.post("/api/upscale/generate")
async def upscale_generate(model: str = Form(...), prompt: str = Form(...),
                           image: UploadFile = File(...)):
    import threading as _threading

    if model not in UPSCALE_MODELS:
        raise HTTPException(400, "Выберите модель")

    content = await image.read()
    if len(content) < 100:
        raise HTTPException(400, "Файл пуст или повреждён")

    info = config.MODELS[model]
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "model_id": model, "label": info["label"],
           "status": "running", "filename": None, "error": None}
    _upscale_jobs[job_id] = job

    def worker():
        temp_dir = config.OUTPUT_DIR / "temp" / job_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        input_path = temp_dir / "input.png"
        input_path.write_bytes(content)

        out_path = temp_dir / "upscaled.png"
        seed = random.randint(0, 2**32 - 1)
        try:
            # klein — multimodal, krea2 — img2img
            ref_mode = "multimodal" if model == "flux2-klein-4b" else "img2img"
            generator.generate(
                prompt, model, 1024, 1024,
                info["default_steps"], info.get("guidance_scale", 1.0), seed, out_path,
                reference_image=input_path,
                reference_mode=ref_mode,
                strength=0.5,
            )
            job["filename"] = f"temp/{job_id}/{out_path.name}"
            job["status"] = "done"
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)

    _threading.Thread(target=worker, daemon=True).start()
    return {"id": job_id}


@app.get("/api/upscale/{job_id}")
def upscale_get(job_id: str):
    job = _upscale_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return job


# ─── Массовый upscale (CSV: id / image URL) ────────────────────────────────


def _upscale_worker(batch_id):
    """Обработать пакет апскейла: скачать → upscale → webp. Переживает рестарт."""
    from PIL import Image as _Image

    batch = upscale_store.get_job(batch_id)
    if batch is None:
        return

    info = config.MODELS[batch.model]
    folder = batch.folder_name()
    batch_dir = config.OUTPUT_DIR / "upscale" / folder
    batch_dir.mkdir(parents=True, exist_ok=True)
    src_dir = config.OUTPUT_DIR / "temp" / f"upscale_src_{batch.id}"
    src_dir.mkdir(parents=True, exist_ok=True)
    ref_mode = "multimodal" if batch.model == "flux2-klein-4b" else "img2img"

    for item in batch.items:
        if item.status in ("done", "error"):
            continue  # возобновление: пропускаем уже обработанные
        item.status = "running"
        upscale_store.update_job(batch)
        try:
            src = src_dir / f"{item.id}"
            if not image_search.download_image(item.image_url, src):
                raise RuntimeError("Не удалось скачать исходное изображение")

            raw_path = batch_dir / f"{item.id}_raw.png"
            seed = random.randint(0, 2**32 - 1)
            generator.generate(
                batch.prompt, batch.model, 1024, 1024,
                info["default_steps"], info.get("guidance_scale", 1.0), seed, raw_path,
                reference_image=src, reference_mode=ref_mode, strength=0.5,
            )

            # конвертация в webp
            webp_path = batch_dir / f"{item.item_id}.webp"
            with _Image.open(raw_path) as im:
                im = im.convert("RGB")
                im.save(webp_path, "WEBP", quality=config.EXPORT_QUALITY, method=6)
            try:
                raw_path.unlink(missing_ok=True)
            except OSError:
                pass

            item.filename = f"upscale/{folder}/{item.item_id}.webp"
            item.status = "done"
        except Exception as e:
            item.status = "error"
            item.error = str(e)
        upscale_store.update_job(batch)

    batch.status = "done"
    upscale_store.update_job(batch)


@app.post("/api/upscale/batch")
async def upscale_batch(model: str = Form(...), prompt: str = Form(...),
                        file: UploadFile = File(...)):
    import csv as _csv
    import io as _io
    import threading as _threading

    if model not in UPSCALE_MODELS:
        raise HTTPException(400, "Выберите модель")

    content = await file.read()
    text = content.decode("utf-8-sig")
    delimiter = _detect_delimiter(text)
    reader = _csv.DictReader(_io.StringIO(text), delimiter=delimiter)

    items = []
    for row in reader:
        item_id = (row.get("id") or row.get("ID") or "").strip()
        image_url = (row.get("image") or row.get("url") or row.get("image_url") or "").strip()
        if not item_id or not image_url:
            continue
        items.append(UpscaleItem(item_id, image_url))

    if not items:
        raise HTTPException(400, "CSV не содержит строк с колонками id и image (URL)")

    batch = UpscaleBatch(source_name=file.filename, model=model, prompt=prompt, items=items)
    upscale_store.add_job(batch)

    _threading.Thread(target=_upscale_worker, args=(batch.id,), daemon=True).start()
    return {"id": batch.id}


@app.get("/api/upscale/batches")
def upscale_batches_list():
    result = []
    for b in upscale_store.list_jobs():
        done, total = b.progress()
        result.append({
            "id": b.id,
            "source_name": b.source_name,
            "status": b.status,
            "done": done,
            "total": total,
            "created_at": b.created_at,
        })
    return {"jobs": result}


@app.get("/api/upscale/batch/{batch_id}")
def upscale_batch_get(batch_id: str):
    batch = upscale_store.get_job(batch_id)
    if batch is None:
        raise HTTPException(404, "Задача не найдена")
    done, total = batch.progress()
    return {
        "id": batch.id,
        "source_name": batch.source_name,
        "model": batch.model,
        "status": batch.status,
        "done": done,
        "total": total,
        "folder": batch.folder_name(),
        "items": [
            {"id": it.id, "item_id": it.item_id, "image_url": it.image_url,
             "status": it.status, "filename": it.filename, "error": it.error}
            for it in batch.items
        ],
    }


@app.get("/api/upscale/batch/{batch_id}/zip")
def upscale_batch_zip(batch_id: str):
    import shutil as _shutil

    batch = upscale_store.get_job(batch_id)
    if batch is None:
        raise HTTPException(404, "Задача не найдена")

    folder = batch.folder_name()
    batch_dir = config.OUTPUT_DIR / "upscale" / folder
    if not batch_dir.exists() or not any(f.is_file() for f in batch_dir.iterdir()):
        raise HTTPException(404, "Нет результатов")

    zip_tmp_dir = config.OUTPUT_DIR / ".zip_tmp"
    zip_tmp_dir.mkdir(parents=True, exist_ok=True)
    archive_path = zip_tmp_dir / f"upscale_{batch_id}_{uuid.uuid4().hex[:8]}"
    final_zip = Path(str(archive_path) + ".zip")

    try:
        _shutil.make_archive(str(archive_path), "zip", root_dir=batch_dir)

        from starlette.background import BackgroundTask

        def _cleanup():
            try:
                if final_zip.exists():
                    final_zip.unlink()
            except OSError:
                pass

        return FileResponse(
            final_zip,
            filename=f"{folder}.zip",
            background=BackgroundTask(_cleanup),
        )
    except Exception as e:
        raise HTTPException(500, f"Ошибка архивации: {e}")


@app.delete("/api/upscale/batch/{batch_id}")
def upscale_batch_delete(batch_id: str):
    import shutil as _shutil

    batch = upscale_store.get_job(batch_id)
    if batch is None:
        raise HTTPException(404, "Задача не найдена")

    folder = batch.folder_name()
    batch_dir = config.OUTPUT_DIR / "upscale" / folder
    if batch_dir.exists():
        _shutil.rmtree(batch_dir, ignore_errors=True)

    if not upscale_store.delete_job(batch_id):
        raise HTTPException(404, "Задача не найдена")
    return {"ok": True}


# ─── Генерация видео ───────────────────────────────────────────────────────

_video_jobs = {}
_video_jobs_file = config.BASE_DIR / "video_jobs.json"


def _build_comfy_files(model_id):
    """Собрать словарь файлов модели для ComfyUI (model/clip/vae)."""
    info = config.MODELS[model_id]
    files = {}
    for repo_id, filename, folder in info["files"]:
        local_name = filename.split("/")[-1]
        if folder == "diffusion_models":
            if "unconditional" in local_name:
                files["model_uncond"] = local_name
            else:
                files["model"] = local_name
        elif folder == "text_encoders":
            files["clip"] = local_name
        elif folder == "vae":
            files["vae"] = local_name
        elif folder == "loras":
            files["lora"] = local_name
    return files


def _save_video_jobs():
    try:
        data = {"jobs": list(_video_jobs.values())}
        tmp = _video_jobs_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(_video_jobs_file))
    except Exception:
        pass


def _load_video_jobs():
    try:
        if _video_jobs_file.exists():
            data = json.loads(_video_jobs_file.read_text(encoding="utf-8"))
            for j in data.get("jobs", []):
                if j.get("status") == "running" and j.get("prompt_id"):
                    _video_jobs[j["id"]] = j
    except Exception:
        pass


def _video_worker(job_id):
    job = _video_jobs.get(job_id)
    if job is None:
        return
    prompt_id = job.get("prompt_id")
    try:
        result = comfy_client.wait_for_result(prompt_id, timeout=1800)
        data = comfy_client.get_image_bytes(result["filename"], result["subfolder"], result["type"])
        videos_dir = config.OUTPUT_DIR / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)
        out_path = videos_dir / f"{job_id}.mp4"
        out_path.write_bytes(data)
        job["filename"] = f"videos/{out_path.name}"
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    _save_video_jobs()


@app.get("/video", response_class=HTMLResponse)
def video_page(request: Request):
    return templates.TemplateResponse(request, "video.html", {"active": "video"})


@app.post("/api/video/generate")
def video_generate(body: dict):
    import threading as _threading

    prompt = (body.get("prompt") or "").strip()
    model_id = (body.get("model") or "").strip()
    if not prompt:
        raise HTTPException(400, "Введите промпт")
    if model_id not in config.MODELS or not config.MODELS[model_id].get("video"):
        raise HTTPException(400, "Выберите видео-модель")

    info = config.MODELS[model_id]
    width = int(body.get("width") or info.get("video_width", 832))
    height = int(body.get("height") or info.get("video_height", 480))
    # Кламп разрешения до нативного для модели: слишком широкое (например,
    # 832×480 для LTX) даёт шум, т.к. модель обучена на своём родном разрешении.
    width = min(width, info.get("video_width", 832))
    height = min(height, info.get("video_height", 480))
    steps = int(body.get("steps") or info["default_steps"])
    fps = float(body.get("fps") or 16)
    duration = float(body.get("duration") or 0)
    length = int(round(duration * fps)) if duration > 0 else None

    seed = body.get("seed")
    if seed is None or seed < 0:
        seed = random.randint(0, 2**32 - 1)

    files = _build_comfy_files(model_id)

    # отправляем в ComfyUI, сохраняем prompt_id — генерация переживёт рестарт flux
    prompt_id = comfy_client.submit_workflow(
        info["workflow"], prompt, width, height, steps, seed, files,
        length=length, fps=fps,
        negative_prompt=(body.get("negative_prompt") or "").strip() or None,
    )

    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "prompt": prompt, "model_id": model_id,
           "label": info["label"], "status": "running", "filename": None,
           "error": None, "prompt_id": prompt_id}
    _video_jobs[job_id] = job
    _save_video_jobs()

    _threading.Thread(target=_video_worker, args=(job_id,), daemon=True).start()
    return {"id": job_id}


@app.get("/api/video/{job_id}")
def video_get(job_id: str):
    job = _video_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return job


# ─── Публикация на WordPress / WooCommerce ───────────────────────────────────

@app.get("/publish/{job_id}", response_class=HTMLResponse)
def publish_page(job_id: str, request: Request):
    return templates.TemplateResponse(request, "publish.html", {"active": "tasks"})


@app.post("/api/publish/{job_id}/test")
def publish_test(job_id: str, body: dict):
    """Проверить доступы к сайту до публикации."""
    try:
        client = wp_publish.WpClient(
            site=body.get("site", ""),
            wp_user=body.get("wp_user"),
            wp_app_password=body.get("wp_app_password"),
            wc_key=body.get("wc_key"),
            wc_secret=body.get("wc_secret"),
        )
        return {"ok": True, "result": client.test_connection()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/publish/{job_id}")
def publish_job(job_id: str, body: dict):
    """Запустить публикацию в фоне. Возвращает сразу, прогресс — отдельно."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")

    if job_id in _publish_status and _publish_status[job_id].get("running"):
        return {"ok": True, "already_running": True}

    _start_publish(job_id, body)
    return {"ok": True, "started": True}


@app.get("/api/publish/{job_id}/status")
def publish_status(job_id: str):
    """Прогресс публикации + сколько уже загружено."""
    return _publish_status.get(job_id, {"running": False, "done": 0, "total": 0, "ok_count": 0, "fail_count": 0, "skipped": 0})


# ─── Фоновая публикация с журналом прогресса ─────────────────────────────────

_publish_status = {}
_publish_log = {}  # job_id -> {filename: {"media_id": ..., "status": ...}}


def _publish_log_path(job):
    job_folder = job.folder_name() or f"job_{job.id}"
    return config.OUTPUT_DIR / job_folder / "publish_progress.json"


def _start_publish(job_id: str, body: dict):
    import threading

    job = store.get_job(job_id)
    if job is None:
        return

    site = body.get("site", "")
    client = wp_publish.WpClient(
        site=site,
        wp_user=body.get("wp_user"),
        wp_app_password=body.get("wp_app_password"),
        wc_key=body.get("wc_key"),
        wc_secret=body.get("wc_secret"),
    )
    insert_in_content = bool(body.get("insert_in_content", False))
    img_class = body.get("img_class", "")
    replace_main = bool(body.get("replace_main", False))

    job_folder = job.folder_name() or f"job_{job.id}"
    images_dir = config.OUTPUT_DIR / job_folder / "images"

    # журнал уже загруженных (для возобновления)
    log_path = _publish_log_path(job)
    log = {}
    if log_path.exists():
        try:
            log = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            log = {}

    files = []
    for f in sorted(images_dir.iterdir()):
        if not f.is_file():
            continue
        base_id, suffix = wp_publish.parse_image_name(f.name)
        if base_id is None:
            continue
        files.append(f)

    total = len(files)
    status = {
        "running": True,
        "done": 0,
        "total": total,
        "ok_count": 0,
        "fail_count": 0,
        "skipped": 0,
        "finished": False,
    }
    _publish_status[job_id] = status

    def worker():
        for f in files:
            base_id, suffix = wp_publish.parse_image_name(f.name)
            name_label = f.name

            # уже загружен ранее — пропускаем (возобновление)
            if name_label in log and log[name_label].get("ok"):
                status["skipped"] += 1
                status["done"] += 1
                continue

            try:
                obj_type, title, _data = client.resolve_object(base_id)
                is_additional = suffix is not None
                alt = title or base_id
                slug = wp_publish.slugify(title) if title else base_id
                filename = f"{slug}-{suffix}.{f.suffix.lstrip('.')}" if suffix else f"{slug}{f.suffix}"

                with open(f, "rb") as fh:
                    file_bytes = fh.read()

                media = client.upload_media(file_bytes, filename, alt)
                media_id = media.get("id")
                src_url = media.get("source_url", "")

                if obj_type == "product":
                    client.assign_to_product(base_id, media_id, replace_main=replace_main)
                elif obj_type == "product_cat":
                    client.assign_to_product_cat(base_id, media_id)
                elif obj_type == "post":
                    client.assign_to_post(base_id, media_id, src_url, alt,
                                          insert_in_content=insert_in_content,
                                          img_class=img_class)
                elif obj_type == "category":
                    client.assign_to_category(base_id, media_id)
                else:
                    raise ValueError(f"Объект с ID {base_id} не найден на сайте")

                log[name_label] = {"ok": True, "media_id": media_id, "title": title}
                status["ok_count"] += 1
            except Exception as e:
                log[name_label] = {"ok": False, "error": str(e)}
                status["fail_count"] += 1

            status["done"] += 1
            # сохраняем журнал после каждого файла (чтобы при обрыве не потерять)
            try:
                log_path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        status["running"] = False
        status["finished"] = True

    threading.Thread(target=worker, daemon=True).start()


# ─── Файловый менеджер ────────────────────────────────────────────────────────

@app.get("/files", response_class=HTMLResponse)
def files_page(request: Request):
    return templates.TemplateResponse(request, "files.html", {"active": "files"})


def _resolve_path(rel):
    """Безопасно разрешить относительный путь внутри OUTPUT_DIR."""
    base = config.OUTPUT_DIR.resolve()
    rel = (rel or "").strip("/")
    target = (base / rel).resolve() if rel else base
    # не даём выйти за пределы OUTPUT_DIR
    if base != target and base not in target.parents:
        target = base
    return target


@app.get("/api/files")
def list_files(path: str = ""):
    """Список папок и файлов в указанной директории OUTPUT_DIR."""
    target = _resolve_path(path)
    if not target.is_dir():
        raise HTTPException(404, "Папка не найдена")

    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            stat = p.stat()
            entries.append({
                "name": p.name,
                "is_dir": p.is_dir(),
                "size": stat.st_size if p.is_file() else None,
                "mtime": stat.st_mtime,
            })
        except OSError:
            continue

    return {
        "path": str(target.relative_to(config.OUTPUT_DIR.resolve())) if target != config.OUTPUT_DIR.resolve() else "",
        "entries": entries,
    }


@app.get("/api/files/download")
def download_file(path: str):
    """Скачать отдельный файл."""
    target = _resolve_path(path)
    if not target.is_file():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(target, filename=target.name)


# Папки/файлы, которые нельзя удалять через файловый менеджер.
PROTECTED_NAMES = {".thumbs", ".zip_tmp", "sources-png"}


@app.delete("/api/files")
def delete_path(path: str):
    """Удалить папку или файл из OUTPUT_DIR.

    Запрещено удалять корень (output) и технические папки (.thumbs, .zip_tmp, sources-png).
    """
    import shutil

    target = _resolve_path(path)
    base = config.OUTPUT_DIR.resolve()

    # корень удалять нельзя
    if target == base:
        raise HTTPException(400, "Нельзя удалить корневую папку output")

    # не даём выйти за пределы OUTPUT_DIR
    if base != target and base not in target.parents:
        raise HTTPException(400, "Недопустимый путь")

    # технические папки защищены
    if target.name in PROTECTED_NAMES:
        raise HTTPException(400, f"Папка «{target.name}» защищена от удаления")

    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    elif target.is_file():
        target.unlink(missing_ok=True)
    else:
        raise HTTPException(404, "Путь не найден")

    return {"ok": True}


@app.get("/api/files/zip")
def zip_directory(path: str = ""):
    """Создать ZIP-архив папки и отдать на скачивание.

    Архив создаётся во временной папке на основном диске (OUTPUT_DIR/.zip_tmp),
    а не в /tmp (tmpfs), чтобы большие архивы не обрезались из-за нехватки места.
    """
    import shutil

    target = _resolve_path(path)
    if not target.is_dir():
        raise HTTPException(404, "Папка не найдена")

    zip_tmp_dir = config.OUTPUT_DIR / ".zip_tmp"
    zip_tmp_dir.mkdir(parents=True, exist_ok=True)

    base_name = target.name or "output"
    archive_path = zip_tmp_dir / f"{base_name}_{uuid.uuid4().hex[:8]}"
    # НЕ with_suffix: в имени папки может быть точка, with_suffix отрежет всё после неё.
    final_zip = Path(str(archive_path) + ".zip")

    try:
        shutil.make_archive(
            str(archive_path),
            "zip",
            root_dir=target.parent,
            base_dir=target.name,
        )

        from starlette.background import BackgroundTask

        def _cleanup():
            try:
                if final_zip.exists():
                    final_zip.unlink()
            except OSError:
                pass

        return FileResponse(
            final_zip,
            filename=f"{base_name}.zip",
            background=BackgroundTask(_cleanup),
        )
    except Exception as e:
        raise HTTPException(500, f"Ошибка архивации: {e}")
