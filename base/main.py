"""AI ENIX — сервис «База» (товары, цены, прайсы, сайты).

Авторизация общая с основным проектом: проверяем cookie `flux_session`
через главный сервис (127.0.0.1:8000/api/session/check).
"""
import os
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

import db

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR.parent / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(title="AI ENIX База")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

PREFIX = os.getenv("BASE_PREFIX", "").rstrip("/")
FLUX_AUTH_CHECK = os.getenv("FLUX_AUTH_CHECK", "http://127.0.0.1:8000/api/session/check")
templates.env.globals["prefix"] = PREFIX


def _is_authorized(token):
    if not token:
        return False
    try:
        req = urllib.request.Request(FLUX_AUTH_CHECK, headers={"Cookie": "flux_session=" + token})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/health" or path.startswith("/static"):
            return await call_next(request)
        token = request.cookies.get("flux_session")
        if not _is_authorized(token):
            if path.startswith("/api/"):
                return Response('{"error":"unauthorized"}', status_code=401, media_type="application/json")
            next_path = PREFIX + path
            return RedirectResponse(url="/login?next=" + urllib.parse.quote(next_path), status_code=302)
        return await call_next(request)


app.add_middleware(AuthMiddleware)


@app.get("/health")
def health():
    try:
        db.query("SELECT 1 AS ok")
        return {"ok": True, "db": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    counts = {}
    for table in ("products", "sites", "suppliers", "price_lists", "price_list_items"):
        counts[table] = db.query("SELECT count(*) AS n FROM " + table)[0]["n"]
    counts["reviews_pending"] = db.query("SELECT count(*) AS n FROM reviews WHERE status='pending'")[0]["n"]
    return templates.TemplateResponse(request, "dashboard.html", {"active": "base_dashboard", "counts": counts})


@app.get("/products", response_class=HTMLResponse)
def products_page(request: Request, q: str = "", page: int = 1):
    per = 50
    page = max(1, page)
    offset = (page - 1) * per
    if q:
        like = "%" + q + "%"
        rows = db.query(
            "SELECT * FROM products WHERE name ILIKE %s OR sku ILIKE %s ORDER BY id DESC LIMIT %s OFFSET %s",
            (like, like, per, offset),
        )
        total = db.query("SELECT count(*) AS n FROM products WHERE name ILIKE %s OR sku ILIKE %s", (like, like))[0]["n"]
    else:
        rows = db.query("SELECT * FROM products ORDER BY id DESC LIMIT %s OFFSET %s", (per, offset))
        total = db.query("SELECT count(*) AS n FROM products")[0]["n"]
    return templates.TemplateResponse(
        request, "products.html",
        {"active": "base_products", "rows": rows, "q": q, "page": page, "total": total, "per": per},
    )


@app.get("/sites", response_class=HTMLResponse)
def sites_page(request: Request):
    rows = db.query("SELECT * FROM sites ORDER BY id")
    return templates.TemplateResponse(request, "sites.html", {"active": "base_sites", "rows": rows})


@app.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(request: Request):
    rows = db.query("SELECT * FROM suppliers ORDER BY id")
    return templates.TemplateResponse(request, "suppliers.html", {"active": "base_suppliers", "rows": rows})
