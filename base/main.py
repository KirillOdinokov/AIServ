"""AI ENIX — сервис «База» (товары, цены, прайсы, сайты)."""
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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

AUTH_USER = os.getenv("FLUX_USER", "kirill")
AUTH_PASSWORD = os.getenv("FLUX_PASSWORD", "")
SESSION_COOKIE = "base_session"
_sessions = set()

PREFIX = os.getenv("BASE_PREFIX", "").rstrip("/")
templates.env.globals["prefix"] = PREFIX


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/login", "/api/login", "/api/logout", "/health") or path.startswith("/static"):
            return await call_next(request)
        token = request.cookies.get(SESSION_COOKIE)
        if token not in _sessions:
            if path.startswith("/api/"):
                return Response('{"error":"unauthorized"}', status_code=401, media_type="application/json")
            return RedirectResponse(url=PREFIX + "/login", status_code=302)
        return await call_next(request)


app.add_middleware(AuthMiddleware)


@app.get("/health")
def health():
    try:
        db.query("SELECT 1 AS ok")
        return {"ok": True, "db": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.post("/api/login")
async def api_login(request: Request):
    form = await request.form()
    if form.get("username") == AUTH_USER and form.get("password") == AUTH_PASSWORD:
        token = secrets.token_hex(32)
        _sessions.add(token)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30, path=PREFIX or "/")
        return resp
    return JSONResponse({"ok": False, "error": "Неверный логин или пароль"}, status_code=401)


@app.post("/api/logout")
def api_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _sessions.discard(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    counts = {}
    for table in ("products", "sites", "suppliers", "price_lists", "price_list_items"):
        counts[table] = db.query("SELECT count(*) AS n FROM " + table)[0]["n"]
    counts["reviews_pending"] = db.query("SELECT count(*) AS n FROM reviews WHERE status='pending'")[0]["n"]
    return templates.TemplateResponse(request, "dashboard.html", {"active": "dashboard", "counts": counts})


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
        {"active": "products", "rows": rows, "q": q, "page": page, "total": total, "per": per},
    )


@app.get("/sites", response_class=HTMLResponse)
def sites_page(request: Request):
    rows = db.query("SELECT * FROM sites ORDER BY id")
    return templates.TemplateResponse(request, "sites.html", {"active": "sites", "rows": rows})


@app.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(request: Request):
    rows = db.query("SELECT * FROM suppliers ORDER BY id")
    return templates.TemplateResponse(request, "suppliers.html", {"active": "suppliers", "rows": rows})
