"""AI ENIX — сервис «База» (товары, цены, прайсы, сайты).

Авторизация общая с основным проектом: проверяем cookie `flux_session`
через главный сервис (127.0.0.1:8000/api/session/check).
"""
import asyncio
import json
import os
import re
import secrets
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

import db
import email_fetcher
import notifier
import price_engine
import price_matcher
import price_service
import publication_service
import rex_price_service
import site_monitor
import site_watch

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR.parent / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(title="AI ENIX База")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

PREFIX = os.getenv("BASE_PREFIX", "").rstrip("/")
FLUX_AUTH_CHECK = os.getenv("FLUX_AUTH_CHECK", "http://127.0.0.1:8000/api/session/check")
OFFICE_BASE_URL = os.getenv("OFFICE_BASE_URL", "https://office.enix.ru").rstrip("/")
templates.env.globals["prefix"] = PREFIX
templates.env.globals["office_base"] = OFFICE_BASE_URL


def _norm_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    if digits[0] == "8":
        return "+7" + digits[1:]
    if digits[0] == "7":
        return "+" + digits
    return "+" + digits


def _next_domain_expiry(registration_date):
    """Ближайшая годовщина регистрации домена (домен продлевается ежегодно)."""
    if not registration_date:
        return None
    today = date.today()
    try:
        candidate = registration_date.replace(year=today.year)
        if candidate < today:
            candidate = candidate.replace(year=today.year + 1)
    except ValueError:
        return None
    return candidate


def _resolve_legal_entity(text):
    text = (text or "").strip()
    if not text:
        return None
    row = db.query(
        "SELECT id FROM legal_entities WHERE lower(name) = lower(%s) LIMIT 1", (text,)
    )
    if row:
        return row[0]["id"]
    row = db.query(
        "INSERT INTO legal_entities (name, full_name, is_active) "
        "VALUES (%s, %s, true) RETURNING id",
        (text, text),
    )
    return row[0]["id"]


def _resolve_address(text):
    text = (text or "").strip()
    if not text:
        return None
    row = db.query(
        "SELECT id FROM addresses WHERE lower(full_address) = lower(%s) LIMIT 1", (text,)
    )
    if row:
        return row[0]["id"]
    row = db.query(
        "INSERT INTO addresses (label, full_address, is_active) "
        "VALUES ('Из карточки сайта', %s, true) RETURNING id",
        (text,),
    )
    return row[0]["id"]


@app.on_event("startup")
async def _startup_site_monitor():
    site_monitor.start()
    notifier.start()
    price_service.recover_stuck()
    email_fetcher.start()


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
        if path == "/health" or path.startswith("/static") or path.startswith("/api/site/"):
            return await call_next(request)
        token = request.cookies.get("flux_session")
        if not _is_authorized(token):
            if path.startswith("/api/"):
                return Response('{"error":"unauthorized"}', status_code=401, media_type="application/json")
            next_path = PREFIX + path
            return RedirectResponse(url="/login?next=" + urllib.parse.quote(next_path), status_code=302)
        return await call_next(request)


app.add_middleware(AuthMiddleware)

PRODUCTS_SQL = """
WITH RECURSIVE cat AS (
    SELECT c.amalor_id AS leaf_id, c.amalor_id, c.name, c.parent_id FROM categories c
    UNION ALL
    SELECT cat.leaf_id, c.amalor_id, c.name, c.parent_id
    FROM categories c
    JOIN cat ON c.amalor_id = cat.parent_id
)
SELECT p.id, p.sku, p.name, p.category_id, p.unit, p.price_unit, p.price,
       p.price_currency, p.price_updated_at, p.price_source, p.is_published,
       p.weight_kg, p.dim_length_mm, p.dim_width_mm, p.dim_height_mm, p.volume_m3,
       (SELECT c.name FROM cat c
        WHERE c.leaf_id = p.category_id AND c.parent_id IS NULL
        LIMIT 1) AS head_category,
       COALESCE((
           SELECT json_agg(json_build_object('id', s.id, 'name', s.name, 'slug', s.slug)
                           ORDER BY s.name)
           FROM supplier_products sp
           JOIN suppliers s ON s.id = sp.supplier_id
           WHERE sp.product_id = p.id AND s.portal_id IS NOT NULL
       ), '[]'::json) AS suppliers,
       COALESCE((
           SELECT json_agg(jsonb_build_object('city', a.city, 'label', a.label, 'address', a.full_address))
           FROM (
               SELECT DISTINCT sa.city, sa.label, sa.full_address
               FROM supplier_products sp
               JOIN supplier_addresses sa ON sa.supplier_id = sp.supplier_id
               WHERE sp.product_id = p.id
                 AND (
                   sa.label ILIKE '%%склад%%' OR sa.label ILIKE '%%самовывоз%%'
                   OR sa.label ILIKE '%%погрузк%%' OR sa.label ILIKE '%%отгрузк%%'
                   OR sa.label ILIKE '%%база%%' OR sa.is_main
                   OR NOT EXISTS (
                     SELECT 1 FROM supplier_addresses sa2
                     WHERE sa2.supplier_id = sa.supplier_id
                       AND (sa2.label ILIKE '%%склад%%' OR sa2.label ILIKE '%%самовывоз%%'
                            OR sa2.label ILIKE '%%погрузк%%' OR sa2.label ILIKE '%%отгрузк%%'
                            OR sa2.label ILIKE '%%база%%' OR sa2.is_main)
                   )
                 )
           ) a
       ), '[]'::json) AS addresses,
       COALESCE((
           SELECT json_agg(json_build_object('name', si.name, 'domain', si.domain, 'url', si.base_url)
                           ORDER BY si.name)
           FROM product_sites ps2
           JOIN sites si ON si.id = ps2.site_id
           WHERE ps2.product_id = p.id
       ), '[]'::json) AS sites
FROM products p
{where}
ORDER BY p.id DESC
LIMIT %s OFFSET %s
"""


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
def products_page(request: Request, q: str = "", page: int = 1, per: int = 200):
    per = max(50, min(500, int(per or 200)))
    page = max(1, page)
    offset = (page - 1) * per
    where = ""
    params = []
    if q:
        like = "%" + q + "%"
        where = "WHERE (p.name ILIKE %s OR p.sku ILIKE %s)"
        params = [like, like]
    total = db.query("SELECT count(*) AS n FROM products p " + where, tuple(params))[0]["n"]
    rows = db.query(PRODUCTS_SQL.format(where=where), tuple(params) + (per, offset))
    picker = db.query(
        "SELECT id, name, slug FROM suppliers WHERE portal_id IS NOT NULL ORDER BY name"
    )
    return templates.TemplateResponse(
        request, "products.html",
        {"active": "base_products", "rows": rows, "q": q, "page": page,
         "total": total, "per": per, "picker": picker},
    )


@app.post("/api/products/suppliers")
async def products_set_supplier(request: Request):
    body = await request.json()
    try:
        ids = [int(x) for x in (body.get("product_ids") or [])]
        supplier_id = int(body.get("supplier_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "bad request")
    mode = body.get("mode") or "add"
    if not ids or not supplier_id or mode not in ("add", "change"):
        raise HTTPException(400, "bad request")
    if len(ids) > 1000:
        raise HTTPException(400, "too many products")
    if not db.query("SELECT id FROM suppliers WHERE id = %s AND portal_id IS NOT NULL", (supplier_id,)):
        raise HTTPException(400, "supplier not found")
    if mode == "change":
        db.execute(
            "DELETE FROM supplier_products sp USING suppliers s "
            "WHERE sp.supplier_id = s.id AND s.portal_id IS NOT NULL "
            "AND sp.product_id = ANY(%s)",
            (ids,),
        )
    inserted = db.execute(
        "INSERT INTO supplier_products (supplier_id, product_id, product_type, source) "
        "SELECT %s, x, 'manual', 'manual' FROM unnest(%s::int[]) AS x "
        "ON CONFLICT (supplier_id, product_id, product_type) DO NOTHING",
        (supplier_id, ids),
    )
    return {"ok": True, "inserted": inserted, "mode": mode}


@app.post("/api/products/bulk")
async def products_bulk(request: Request):
    body = await request.json()
    try:
        ids = [int(x) for x in (body.get("product_ids") or [])]
    except (TypeError, ValueError):
        raise HTTPException(400, "bad request")
    if not ids or len(ids) > 1000:
        raise HTTPException(400, "bad product_ids")

    supplier_mode = body.get("supplier_mode") or "none"
    if supplier_mode not in ("none", "add", "change"):
        raise HTTPException(400, "bad supplier_mode")
    supplier_id = None
    if supplier_mode != "none":
        try:
            supplier_id = int(body.get("supplier_id") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "bad supplier")
        if not db.query("SELECT id FROM suppliers WHERE id = %s AND portal_id IS NOT NULL", (supplier_id,)):
            raise HTTPException(400, "supplier not found")

    unit = (body.get("unit") or "").strip()
    price_op = body.get("price_op") or "none"
    if price_op not in ("none", "percent", "fixed"):
        raise HTTPException(400, "bad price_op")
    price_value = 0.0
    if price_op != "none":
        try:
            price_value = float(body.get("price_value") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "bad price value")

    result = {"ok": True, "supplier": 0, "unit": 0, "price": 0}
    with db.connect() as conn:
        with conn.cursor() as cur:
            if supplier_mode == "change":
                cur.execute(
                    "DELETE FROM supplier_products sp USING suppliers s "
                    "WHERE sp.supplier_id = s.id AND s.portal_id IS NOT NULL "
                    "AND sp.product_id = ANY(%s)",
                    (ids,),
                )
            if supplier_mode in ("add", "change"):
                cur.execute(
                    "INSERT INTO supplier_products (supplier_id, product_id, product_type, source) "
                    "SELECT %s, x, 'manual', 'manual' FROM unnest(%s::int[]) AS x "
                    "ON CONFLICT (supplier_id, product_id, product_type) DO NOTHING",
                    (supplier_id, ids),
                )
                result["supplier"] = cur.rowcount

            if unit:
                cur.execute("UPDATE products SET unit = %s WHERE id = ANY(%s)", (unit, ids))
                result["unit"] = cur.rowcount

            if price_op != "none":
                cur.execute(
                    "SELECT id, price FROM products WHERE id = ANY(%s) AND price IS NOT NULL FOR UPDATE",
                    (ids,),
                )
                old = {row["id"]: row["price"] for row in cur.fetchall()}
                for pid, old_price in old.items():
                    if price_op == "percent":
                        new_price = round(float(old_price) * (1 + price_value / 100.0), 2)
                    else:
                        new_price = round(float(old_price) + price_value, 2)
                    if new_price < 0:
                        new_price = 0.0
                    cur.execute(
                        "UPDATE products SET price = %s, price_updated_at = now(), price_source = 'manual' "
                        "WHERE id = %s",
                        (new_price, pid),
                    )
                    cur.execute(
                        "INSERT INTO price_changes (product_id, old_price, new_price, source, applied, reason, "
                        "changed_by, source_updated_at) "
                        "VALUES (%s, %s, %s, 'manual', true, 'bulk', 'ui', now())",
                        (pid, old_price, new_price),
                    )
                result["price"] = len(old)
        conn.commit()
    return result


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request, q: str = "", page: int = 1, per: int = 50):
    per = max(20, min(100, int(per or 50)))
    page = max(1, page)
    offset = (page - 1) * per
    raw = (q or "").strip()
    like = "%" + raw + "%"
    total = db.query(
        "SELECT count(*) AS n FROM suppliers s WHERE s.amalor_slug IS NOT NULL "
        "AND (%s = '%%' OR s.name ILIKE %s OR s.amalor_slug ILIKE %s)",
        (like, like, like),
    )[0]["n"]
    amalor = db.query(
        "SELECT s.id, s.name, s.slug, s.amalor_slug, s.website, "
        "(SELECT count(*) FROM supplier_products sp WHERE sp.supplier_id = s.id) AS products, "
        "(SELECT p.supplier_url FROM products p WHERE p.supplier_slug = s.amalor_slug "
        " AND p.supplier_url IS NOT NULL LIMIT 1) AS sample_url "
        "FROM suppliers s WHERE s.amalor_slug IS NOT NULL "
        "AND (%s = '%%' OR s.name ILIKE %s OR s.amalor_slug ILIKE %s) "
        "ORDER BY s.amalor_slug LIMIT %s OFFSET %s",
        (like, like, like, per, offset),
    )
    office = db.query(
        "SELECT id, name, slug FROM suppliers WHERE portal_id IS NOT NULL ORDER BY name"
    )
    matches = {}
    for r in db.query("SELECT amalor_slug, office_supplier_id FROM supplier_matches"):
        matches.setdefault(r["amalor_slug"], set()).add(r["office_supplier_id"])
    return templates.TemplateResponse(request, "review.html", {
        "active": "base_review", "amalor": amalor, "office": office,
        "matches": matches, "q": q, "page": page, "per": per, "total": total,
    })

@app.post("/api/review/supplier-links")
async def supplier_links(request: Request):
    body = await request.json()
    amalor_slug = (body.get("amalor_slug") or "").strip()
    try:
        office_ids = [int(x) for x in (body.get("office_ids") or [])]
    except (TypeError, ValueError):
        raise HTTPException(400, "bad office ids")
    if not amalor_slug:
        raise HTTPException(400, "bad amalor slug")
    db.execute("DELETE FROM supplier_matches WHERE amalor_slug = %s", (amalor_slug,))
    for oid in office_ids:
        db.execute(
            "INSERT INTO supplier_matches (amalor_slug, office_supplier_id, source) "
            "VALUES (%s, %s, 'manual') ON CONFLICT DO NOTHING",
            (amalor_slug, oid),
        )
    if office_ids:
        db.execute(
            "UPDATE suppliers SET amalor_slug = NULL WHERE amalor_slug = %s AND id <> %s",
            (amalor_slug, office_ids[0]),
        )
        db.execute(
            "UPDATE suppliers SET amalor_slug = %s WHERE id = %s",
            (amalor_slug, office_ids[0]),
        )
    else:
        db.execute("UPDATE suppliers SET amalor_slug = NULL WHERE amalor_slug = %s", (amalor_slug,))
    return {"ok": True, "count": len(office_ids)}


@app.get("/sites", response_class=HTMLResponse)
def sites_page(request: Request):
    sites = db.query(
        "SELECT s.id, s.name, s.domain, s.base_url, s.active, s.currency, s.price_mode, "
        "s.sync_enabled, s.supplier_id, s.remote_products, s.remote_products_at, "
        "s.phone, s.manager_portal_user_id, "
        "sup.name AS configured_supplier, "
        "le.name AS legal_entity_name, ad.city AS address_city, ad.full_address AS address_text, "
        "(SELECT string_agg(u.full_name, ', ' ORDER BY u.full_name) "
        " FROM site_managers sm JOIN portal_users u ON u.portal_id = sm.portal_user_id "
        " WHERE sm.site_id = s.id) AS manager_names "
        "FROM sites s "
        "LEFT JOIN suppliers sup ON sup.id = s.supplier_id "
        "LEFT JOIN legal_entities le ON le.id = s.legal_entity_id "
        "LEFT JOIN addresses ad ON ad.id = s.address_id "
        "ORDER BY s.name"
    )
    checks = {
        r["site_id"]: r
        for r in db.query(
            "SELECT DISTINCT ON (site_id) site_id, checked_at, ok, status_code, ttfb_ms, "
            "total_ms, error FROM site_checks ORDER BY site_id, checked_at DESC"
        )
    }
    uptimes = {
        r["site_id"]: r["uptime"]
        for r in db.query(
            "SELECT site_id, round(avg(CASE WHEN ok THEN 1 ELSE 0 END) * 100, 1) AS uptime "
            "FROM site_checks WHERE checked_at > now() - interval '24 hours' GROUP BY site_id"
        )
    }
    products = {
        r["site_id"]: r["n"]
        for r in db.query("SELECT site_id, count(*) AS n FROM product_sites GROUP BY site_id")
    }
    domain_expiries = {
        r["site_id"]: _next_domain_expiry(r["registration_date"])
        for r in db.query(
            "SELECT DISTINCT ON (site_id) site_id, registration_date FROM domains "
            "ORDER BY site_id, id"
        )
    }
    top_suppliers = {}
    for r in db.query(
        "SELECT DISTINCT ON (ps.site_id) ps.site_id, sup.name, count(*) AS n "
        "FROM product_sites ps "
        "JOIN supplier_products sp ON sp.product_id = ps.product_id "
        "JOIN suppliers sup ON sup.id = sp.supplier_id AND sup.portal_id IS NOT NULL "
        "GROUP BY ps.site_id, sup.id, sup.name "
        "ORDER BY ps.site_id, count(*) DESC"
    ):
        top_suppliers[r["site_id"]] = r["name"]

    now = datetime.now(timezone.utc)
    cards = []
    for s in sites:
        chk = checks.get(s["id"])
        if not chk:
            state = "idle"
        elif not chk["ok"]:
            state = "bad"
        elif (now - chk["checked_at"]).total_seconds() > 7200:
            state = "warn"
        else:
            state = "ok"
        cards.append({
            **s,
            "check": chk,
            "state": state,
            "uptime": uptimes.get(s["id"]),
            "products": (s["remote_products"] if s["remote_products"] is not None
                         else products.get(s["id"], 0)),
            "main_supplier": s["configured_supplier"] or top_suppliers.get(s["id"]),
            "domain_expires": domain_expiries.get(s["id"]),
        })
    return templates.TemplateResponse(request, "site_dashboard.html", {
        "active": "sites", "cards": cards,
    })


@app.get("/sites/list", response_class=HTMLResponse)
def sites_list_page(request: Request):
    rows = db.query("SELECT * FROM sites ORDER BY id")
    return templates.TemplateResponse(request, "sites.html", {"active": "base_sites", "rows": rows})


@app.get("/monitor", response_class=HTMLResponse)
def monitor_page(request: Request):
    return templates.TemplateResponse(request, "site_monitor.html", {
        "active": "site_monitor",
        "sites": [
            {
                "host": s["host"],
                "group": s["group"],
                "old_ips": site_watch.snapshot_ips(s["host"]),
            }
            for s in site_watch.SITES
        ],
        "groups": site_watch.GROUPS,
        "snapshot_created_at": site_watch.SNAPSHOT_CREATED_AT,
        "provider_ip": os.getenv("PROVIDER_IP", "193.176.78.3"),
        "provider_host": os.getenv("PROVIDER_HOST", "www.enix.ru"),
    })


SITE_FIELDS = (
    "id, name, domain, platform, base_url, active, price_mode, price_multiplier, "
    "price_random_min_pct, price_random_pct, price_round, price_min, price_max, "
    "price_priority, sync_enabled, currency, supplier_id, remote_products, "
    "remote_products_at, wp_user, phone, phone_normalized, manager_portal_user_id, "
    "address_id, legal_entity_id, api_key, remote_api_url, remote_api_key, server_id"
)


def _upsert_domain(site_id, name, registrar, account, reg_date, exp_date):
    """Вставить/обновить строку домена (primary = name == site.domain)."""
    if not name:
        return
    db.execute(
        "INSERT INTO domains (site_id, name, registrar, account, registration_date, expiration_date) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (name) DO UPDATE SET "
        "  registrar = EXCLUDED.registrar, account = EXCLUDED.account, "
        "  registration_date = EXCLUDED.registration_date, "
        "  expiration_date = EXCLUDED.expiration_date",
        (site_id, name, registrar or None, account or None, reg_date or None, exp_date or None),
    )


def _parse_date(v):
    if not v:
        return None
    v = str(v).strip()
    if not v:
        return None
    return v


def _resolve_server(body):
    sid = body.get("server_id")
    if sid in (None, "", 0):
        return None
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        raise HTTPException(400, "bad server_id")
    if not db.query("SELECT 1 FROM servers WHERE id = %s", (sid,)):
        raise HTTPException(400, "server not found")
    return sid


@app.get("/sites/managers", response_class=HTMLResponse)
def site_managers_page(request: Request):
    rows = db.query(
        "SELECT u.portal_id, u.full_name, u.email, u.phone, u.department, u.position, "
        "       u.is_active, count(DISTINCT s.id) AS sites_count, "
        "       string_agg(DISTINCT s.name, ', ' ORDER BY s.name) AS site_names "
        "FROM portal_users u "
        "LEFT JOIN site_managers sm ON sm.portal_user_id = u.portal_id "
        "LEFT JOIN sites s ON s.id = sm.site_id "
        "WHERE u.department = 'Продажи' "
        "GROUP BY u.portal_id "
        "ORDER BY u.is_active DESC, u.department NULLS LAST, u.full_name"
    )
    return templates.TemplateResponse(request, "site_managers.html", {
        "active": "sites", "rows": rows,
    })


@app.get("/sites/addresses", response_class=HTMLResponse)
def site_addresses_page(request: Request):
    rows = db.query(
        "SELECT a.*, count(s.id) AS sites_count, "
        "       string_agg(s.name, ', ' ORDER BY s.name) AS site_names "
        "FROM addresses a LEFT JOIN sites s ON s.address_id = a.id "
        "GROUP BY a.id ORDER BY a.is_active DESC, a.city NULLS LAST, a.full_address"
    )
    return templates.TemplateResponse(request, "site_addresses.html", {
        "active": "sites", "rows": rows,
    })


@app.get("/sites/legal-entities", response_class=HTMLResponse)
def site_legal_entities_page(request: Request):
    rows = db.query(
        "SELECT l.*, count(s.id) AS sites_count, "
        "       string_agg(s.name, ', ' ORDER BY s.name) AS site_names "
        "FROM legal_entities l LEFT JOIN sites s ON s.legal_entity_id = l.id "
        "GROUP BY l.id ORDER BY l.is_active DESC, l.name"
    )
    return templates.TemplateResponse(request, "site_legal_entities.html", {
        "active": "sites", "rows": rows,
    })


@app.post("/api/addresses")
async def api_address_create(request: Request):
    body = await request.json()
    full_address = (body.get("full_address") or "").strip()
    if not full_address:
        raise HTTPException(400, "Укажите адрес")
    try:
        lat = float(body["lat"]) if body.get("lat") not in (None, "") else None
        lon = float(body["lon"]) if body.get("lon") not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(400, "bad coordinates")
    row = db.query(
        "INSERT INTO addresses (label, full_address, city, region, postal_code, lat, lon, is_active) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            (body.get("label") or "").strip() or None, full_address,
            (body.get("city") or "").strip() or None,
            (body.get("region") or "").strip() or None,
            (body.get("postal_code") or "").strip() or None,
            lat, lon, bool(body.get("is_active", True)),
        ),
    )
    return {"ok": True, "id": row[0]["id"]}


@app.post("/api/legal-entities")
async def api_legal_entity_create(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Укажите название юрлица")
    row = db.query(
        "INSERT INTO legal_entities (name, full_name, inn, kpp, ogrn, legal_address, "
        "phone, email, is_active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            name, (body.get("full_name") or "").strip() or None,
            (body.get("inn") or "").strip() or None,
            (body.get("kpp") or "").strip() or None,
            (body.get("ogrn") or "").strip() or None,
            (body.get("legal_address") or "").strip() or None,
            (body.get("phone") or "").strip() or None,
            (body.get("email") or "").strip() or None,
            bool(body.get("is_active", True)),
        ),
    )
    return {"ok": True, "id": row[0]["id"]}


@app.get("/sites/new", response_class=HTMLResponse)
def site_new_page(request: Request):
    users = db.query(
        "SELECT portal_id, full_name, department, position, phone, is_active "
        "FROM portal_users WHERE department = 'Продажи' "
        "ORDER BY is_active DESC, department NULLS LAST, full_name"
    )
    addresses = db.query(
        "SELECT id, label, full_address, city FROM addresses "
        "ORDER BY is_active DESC, city NULLS LAST, full_address"
    )
    legal_entities = db.query(
        "SELECT id, name, inn FROM legal_entities ORDER BY is_active DESC, name"
    )
    servers = db.query(
        "SELECT id, name, os, tariff_monthly, balance, has_gpu, host FROM servers ORDER BY name"
    )
    return templates.TemplateResponse(request, "site_new.html", {
        "active": "sites", "users": users,
        "addresses": addresses, "legal_entities": legal_entities,
        "servers": servers,
    })


@app.get("/sites/{site_id}/reconcile", response_class=HTMLResponse)
def site_reconcile_page(request: Request, site_id: int):
    rows = db.query("SELECT id, name, domain, base_url, supplier_id FROM sites WHERE id = %s", (site_id,))
    if not rows:
        raise HTTPException(404, "site not found")
    site = rows[0]
    supplier = None
    supplier_slug = None
    if site["supplier_id"]:
        r = db.query("SELECT id, name, amalor_slug FROM suppliers WHERE id = %s", (site["supplier_id"],))
        if r:
            supplier = r[0]
            supplier_slug = supplier["amalor_slug"]

    site_stats = db.query(
        "SELECT count(*) AS rows, count(product_id) AS matched, "
        "count(*) FILTER (WHERE product_id IS NULL) AS unmatched "
        "FROM site_products WHERE site_id = %s",
        (site_id,),
    )[0]

    base_stats = {"rows": 0, "names": 0}
    matched_names = 0
    missing = []
    extra_base_rows = 0
    if supplier_slug:
        base_stats = db.query(
            "SELECT count(*) AS rows, count(DISTINCT normalized_name) AS names "
            "FROM products WHERE supplier_slug = %s",
            (supplier_slug,),
        )[0]
        matched_names = db.query(
            "SELECT count(DISTINCT p.normalized_name) FROM site_products sp "
            "JOIN products p ON p.id = sp.product_id WHERE sp.site_id = %s",
            (site_id,),
        )[0]["count"]
        missing = db.query(
            "SELECT min(p.sku) AS am_sku, min(p.manufacturer_sku) AS msku, "
            "       min(p.name) AS name, min(p.price) AS price "
            "FROM products p WHERE p.supplier_slug = %s "
            "GROUP BY p.normalized_name "
            "HAVING NOT EXISTS ("
            "  SELECT 1 FROM products p2 JOIN site_products sp ON sp.product_id = p2.id "
            "  WHERE sp.site_id = %s AND p2.normalized_name = p.normalized_name) "
            "ORDER BY 3",
            (supplier_slug, site_id),
        )
        extra_base_rows = db.query(
            "SELECT count(*) AS n FROM products p WHERE p.supplier_slug = %s "
            "AND NOT EXISTS (SELECT 1 FROM site_products sp "
            "                WHERE sp.site_id = %s AND sp.product_id = p.id)",
            (supplier_slug, site_id),
        )[0]["n"]

    site_unmatched = db.query(
        "SELECT sku, name, url, matched_by FROM site_products "
        "WHERE site_id = %s AND product_id IS NULL ORDER BY name",
        (site_id,),
    )
    dup_skus = db.query(
        "SELECT sku_norm, count(*) AS n, string_agg(name, ' | ' ORDER BY name) AS names "
        "FROM site_products WHERE site_id = %s AND sku_norm <> '' "
        "GROUP BY sku_norm HAVING count(*) > 1 ORDER BY n DESC",
        (site_id,),
    )
    dup_site_rows = db.query(
        "SELECT p.sku AS am_sku, p.name AS base_name, count(*) AS n, "
        "       string_agg(sp.sku || ' | ' || sp.name, ' ;; ' ORDER BY sp.sku) AS site_rows "
        "FROM site_products sp JOIN products p ON p.id = sp.product_id "
        "WHERE sp.site_id = %s AND sp.product_id IS NOT NULL "
        "GROUP BY p.id, p.sku, p.name HAVING count(*) > 1 ORDER BY n DESC, p.name",
        (site_id,),
    )
    return templates.TemplateResponse(request, "site_reconcile.html", {
        "active": "sites", "site": site, "supplier": supplier,
        "site_stats": site_stats, "base_stats": base_stats,
        "matched_names": matched_names, "missing": missing,
        "extra_base_rows": extra_base_rows, "site_unmatched": site_unmatched,
        "dup_skus": dup_skus, "dup_site_rows": dup_site_rows,
    })


@app.get("/sites/{site_id}", response_class=HTMLResponse)
def site_detail_page(request: Request, site_id: int):
    row = db.query("SELECT " + SITE_FIELDS + " FROM sites WHERE id = %s", (site_id,))
    if not row:
        raise HTTPException(404, "site not found")
    site = row[0]
    suppliers = db.query("SELECT id, name FROM suppliers WHERE portal_id IS NOT NULL ORDER BY name")
    portal_users = db.query(
        "SELECT portal_id, full_name, department, position, phone, is_active "
        "FROM portal_users WHERE department = 'Продажи' "
        "ORDER BY is_active DESC, department NULLS LAST, full_name"
    )
    manager_name = None
    for u in portal_users:
        if u["portal_id"] == site["manager_portal_user_id"]:
            manager_name = u["full_name"]
            break
    addresses = db.query(
        "SELECT id, label, full_address, city, region, postal_code FROM addresses "
        "ORDER BY is_active DESC, city NULLS LAST, full_address"
    )
    legal_entities = db.query(
        "SELECT id, name, inn, ogrn FROM legal_entities ORDER BY is_active DESC, name"
    )
    selected_manager_ids = {
        r["portal_user_id"]
        for r in db.query(
            "SELECT portal_user_id FROM site_managers WHERE site_id = %s", (site_id,)
        )
    }
    if not selected_manager_ids and site["manager_portal_user_id"]:
        selected_manager_ids.add(site["manager_portal_user_id"])
    address_text = None
    for a in addresses:
        if a["id"] == site["address_id"]:
            address_text = a["full_address"]
            break
    legal_entity_name = None
    for l in legal_entities:
        if l["id"] == site["legal_entity_id"]:
            legal_entity_name = l["name"]
            break
    site_supplier_name = None
    if site["supplier_id"]:
        r = db.query("SELECT name FROM suppliers WHERE id = %s", (site["supplier_id"],))
        site_supplier_name = r[0]["name"] if r else None
    if not site_supplier_name:
        r = db.query(
            "SELECT sup.name, count(*) AS n FROM product_sites ps "
            "JOIN supplier_products sp ON sp.product_id = ps.product_id "
            "JOIN suppliers sup ON sup.id = sp.supplier_id AND sup.portal_id IS NOT NULL "
            "WHERE ps.site_id = %s GROUP BY sup.id, sup.name ORDER BY n DESC LIMIT 1",
            (site_id,),
        )
        site_supplier_name = r[0]["name"] if r else None
    checks = db.query(
        "SELECT checked_at, ok, status_code, ttfb_ms, total_ms, error FROM site_checks "
        "WHERE site_id = %s ORDER BY checked_at DESC LIMIT 1",
        (site_id,),
    )
    uptime = db.query(
        "SELECT round(avg(CASE WHEN ok THEN 1 ELSE 0 END) "
        "  FILTER (WHERE checked_at > now() - interval '24 hours') * 100, 1) AS u24, "
        "round(avg(CASE WHEN ok THEN 1 ELSE 0 END) "
        "  FILTER (WHERE checked_at > now() - interval '7 days') * 100, 1) AS u7 "
        "FROM site_checks WHERE site_id = %s",
        (site_id,),
    )[0]
    preview = []
    for p in db.query(
        "SELECT id, sku, name, price, price_unit, price_type, mrc_price FROM products "
        "WHERE price IS NOT NULL AND price > 0 ORDER BY id LIMIT 6"
    ):
        base = float(p["price"])
        site_price = price_engine.compute_site_price(
            p["price"], site, p["id"],
            price_type=p.get("price_type"), mrc_price=p.get("mrc_price"),
        )
        preview.append({
            "id": p["id"], "sku": p["sku"], "name": p["name"], "unit": p["price_unit"],
            "base": base, "site_price": site_price,
            "uplift": round((site_price / base - 1) * 100, 1) if base else None,
        })
    servers = db.query(
        "SELECT id, name, os, tariff_monthly, balance, has_gpu, host FROM servers ORDER BY name"
    )
    domain = db.query(
        "SELECT name, registrar, account, registration_date, expiration_date "
        "FROM domains WHERE site_id = %s ORDER BY id LIMIT 1",
        (site_id,),
    )
    domain = domain[0] if domain else None
    domain_expires = _next_domain_expiry(domain["registration_date"] if domain else None)
    server = None
    if site.get("server_id"):
        r = db.query(
            "SELECT id, name, os, tariff_monthly, balance, has_gpu, host "
            "FROM servers WHERE id = %s",
            (site["server_id"],),
        )
        server = r[0] if r else None
    server_months = None
    server_block_date = None
    if server and server.get("tariff_monthly") and server.get("balance") is not None:
        try:
            months = float(server["balance"]) / float(server["tariff_monthly"])
            server_months = round(months, 1)
            server_block_date = (
                datetime.now() + timedelta(days=months * 30.44)
            ).strftime("%d.%m.%Y")
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return templates.TemplateResponse(request, "site_detail.html", {
        "active": "sites", "site": site, "suppliers": suppliers,
        "portal_users": portal_users, "manager_name": manager_name,
        "selected_manager_ids": selected_manager_ids,
        "addresses": addresses, "legal_entities": legal_entities,
        "address_text": address_text, "legal_entity_name": legal_entity_name,
        "site_supplier_name": site_supplier_name,
        "last": checks[0] if checks else None, "uptime": uptime, "preview": preview,
        "servers": servers, "domain": domain, "server": server,
        "server_months": server_months, "server_block_date": server_block_date,
        "domain_expires": domain_expires,
    })


@app.get("/api/sites/{site_id}/history")
def api_site_history(site_id: int, hours: int = 24):
    hours = max(1, min(720, int(hours or 24)))
    rows = db.query(
        "SELECT checked_at, ok, status_code, ttfb_ms, total_ms, error FROM site_checks "
        "WHERE site_id = %s AND checked_at > now() - make_interval(hours => %s) "
        "ORDER BY checked_at",
        (site_id, hours),
    )
    return {
        "site_id": site_id,
        "hours": hours,
        "points": [
            {"t": r["checked_at"].isoformat(), "ok": r["ok"], "code": r["status_code"],
             "ttfb": r["ttfb_ms"], "total": r["total_ms"], "error": r["error"]}
            for r in rows
        ],
    }


@app.post("/api/sites/{site_id}/settings")
async def api_site_update(site_id: int, request: Request):
    body = await request.json()
    if not db.query("SELECT id FROM sites WHERE id = %s", (site_id,)):
        raise HTTPException(404, "site not found")

    def fnum(key, default=None):
        val = body.get(key, default)
        if val in (None, ""):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            raise HTTPException(400, "bad " + key)

    price_mode = (body.get("price_mode") or "source").strip()
    if price_mode not in ("source", "derived", "manual"):
        raise HTTPException(400, "bad price_mode")
    if "price_markup_pct" in body:
        multiplier = round(1 + (fnum("price_markup_pct") or 0) / 100.0, 4)
    else:
        multiplier = fnum("price_multiplier", 1)
    rmin = fnum("price_random_min_pct", 0) or 0
    rmax = fnum("price_random_pct", 0) or 0
    rmin, rmax = max(0, rmin), max(0, rmax)
    if rmax < rmin:
        rmin, rmax = rmax, rmin

    fields = {
        "price_mode": price_mode,
        "price_multiplier": multiplier,
        "price_random_min_pct": rmin,
        "price_random_pct": rmax,
        "price_round": fnum("price_round"),
        "price_min": fnum("price_min"),
        "price_max": fnum("price_max"),
        "price_priority": int(body.get("price_priority") or 100),
        "currency": (body.get("currency") or "RUB").strip()[:8],
        "supplier_id": int(body["supplier_id"]) if body.get("supplier_id") else None,
        "phone": (body.get("phone") or "").strip() or None,
        "phone_normalized": _norm_phone(body.get("phone")),
        "sync_enabled": bool(body.get("sync_enabled")),
        "active": bool(body.get("active")),
        "server_id": _resolve_server(body),
    }

    update_managers = False
    manager_ids = []
    if "manager_portal_user_ids" in body:
        try:
            manager_ids = [int(x) for x in (body.get("manager_portal_user_ids") or [])]
        except (TypeError, ValueError):
            raise HTTPException(400, "bad managers")
        update_managers = True
    elif "manager_portal_user_id" in body:
        if body.get("manager_portal_user_id"):
            manager_ids = [int(body["manager_portal_user_id"])]
        update_managers = True
    for mid in manager_ids:
        if not db.query(
            "SELECT 1 FROM portal_users WHERE portal_id = %s AND department = 'Продажи'",
            (mid,),
        ):
            raise HTTPException(400, "manager not found")
    if update_managers:
        fields["manager_portal_user_id"] = manager_ids[0] if manager_ids else None

    if "legal_entity_text" in body:
        fields["legal_entity_id"] = _resolve_legal_entity(body.get("legal_entity_text"))
    elif "legal_entity_id" in body:
        lid = int(body["legal_entity_id"]) if body.get("legal_entity_id") else None
        if lid and not db.query("SELECT 1 FROM legal_entities WHERE id = %s", (lid,)):
            raise HTTPException(400, "legal entity not found")
        fields["legal_entity_id"] = lid

    if "address_text" in body:
        fields["address_id"] = _resolve_address(body.get("address_text"))
    elif "address_id" in body:
        aid = int(body["address_id"]) if body.get("address_id") else None
        if aid and not db.query("SELECT 1 FROM addresses WHERE id = %s", (aid,)):
            raise HTTPException(400, "address not found")
        fields["address_id"] = aid

    if (body.get("name") or "").strip():
        fields["name"] = body["name"].strip()
    if "api_key" in body:
        key = (body.get("api_key") or "").strip()
        if key and len(key) < 24:
            raise HTTPException(400, "API-ключ слишком короткий (минимум 24 символа)")
        fields["api_key"] = key or None
    if "remote_api_url" in body:
        fields["remote_api_url"] = (body.get("remote_api_url") or "").strip() or None
    if "remote_api_key" in body:
        fields["remote_api_key"] = (body.get("remote_api_key") or "").strip() or None
    sets = ", ".join(k + " = %s" for k in fields)
    db.execute(
        "UPDATE sites SET " + sets + " WHERE id = %s",
        (*fields.values(), site_id),
    )
    if "domain_registrar" in body:
        dom = db.query("SELECT domain FROM sites WHERE id = %s", (site_id,))
        if dom:
            _upsert_domain(
                site_id, dom[0]["domain"],
                body.get("domain_registrar"), body.get("domain_account"),
                _parse_date(body.get("domain_registration_date")),
                _parse_date(body.get("domain_expiration_date")),
            )
    if update_managers:
        db.execute("DELETE FROM site_managers WHERE site_id = %s", (site_id,))
        for i, mid in enumerate(manager_ids):
            db.execute(
                "INSERT INTO site_managers (site_id, portal_user_id, is_primary) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (site_id, mid, i == 0),
            )
    price_keys = {
        "price_mode", "price_markup_pct", "price_multiplier", "price_random_min_pct",
        "price_random_pct", "price_round", "price_min", "price_max",
    }
    recomputed = 0
    if price_keys.intersection(body.keys()):
        recomputed = price_service.recompute_site_queue(site_id)
        if recomputed:
            price_service.ping_site(site_id)
    return {"ok": True, "recomputed": recomputed}



@app.post("/api/sites")
async def api_site_create(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    domain = (body.get("domain") or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain).strip("/")
    if not name or not domain:
        raise HTTPException(400, "Укажите название и домен")
    platform = (body.get("platform") or "woocommerce").strip()
    if platform not in ("woocommerce", "amalor", "custom"):
        raise HTTPException(400, "bad platform")
    manager_ids = []
    if "manager_portal_user_ids" in body:
        try:
            manager_ids = [int(x) for x in (body.get("manager_portal_user_ids") or [])]
        except (TypeError, ValueError):
            raise HTTPException(400, "bad managers")
    elif body.get("manager_portal_user_id"):
        manager_ids = [int(body["manager_portal_user_id"])]
    for mid in manager_ids:
        if not db.query(
            "SELECT 1 FROM portal_users WHERE portal_id = %s AND department = 'Продажи'",
            (mid,),
        ):
            raise HTTPException(400, "manager not found")
    manager_id = manager_ids[0] if manager_ids else None

    if "legal_entity_text" in body:
        legal_entity_id = _resolve_legal_entity(body.get("legal_entity_text"))
    else:
        legal_entity_id = int(body["legal_entity_id"]) if body.get("legal_entity_id") else None
        if legal_entity_id and not db.query("SELECT 1 FROM legal_entities WHERE id = %s", (legal_entity_id,)):
            raise HTTPException(400, "legal entity not found")

    if "address_text" in body:
        address_id = _resolve_address(body.get("address_text"))
    else:
        address_id = int(body["address_id"]) if body.get("address_id") else None
        if address_id and not db.query("SELECT 1 FROM addresses WHERE id = %s", (address_id,)):
            raise HTTPException(400, "address not found")
    price_mode = (body.get("price_mode") or "source").strip()
    if price_mode not in ("source", "derived", "manual"):
        raise HTTPException(400, "bad price_mode")
    try:
        markup = float(body.get("price_markup_pct") or 0)
    except (TypeError, ValueError):
        markup = 0.0
    try:
        row = db.query(
            "INSERT INTO sites (name, domain, platform, base_url, phone, phone_normalized, "
            "manager_portal_user_id, address_id, legal_entity_id, active, price_mode, price_multiplier, "
            "price_priority, sync_enabled, currency, wp_user, wp_app_password, wc_key, wc_secret, server_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 100, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (
                name, domain, platform, (body.get("base_url") or "").strip() or None,
                (body.get("phone") or "").strip() or None, _norm_phone(body.get("phone")),
                manager_id, address_id, legal_entity_id,
                bool(body.get("active", True)), price_mode,
                round(1 + markup / 100.0, 4), bool(body.get("sync_enabled", True)),
                (body.get("currency") or "RUB").strip()[:8] or "RUB",
                (body.get("wp_user") or "").strip() or None,
                (body.get("wp_app_password") or "").strip() or None,
                (body.get("wc_key") or "").strip() or None,
                (body.get("wc_secret") or "").strip() or None,
                _resolve_server(body),
            ),
        )
    except Exception as e:
        if "duplicate key" in str(e):
            raise HTTPException(400, "Сайт с таким доменом уже добавлен")
        raise
    site_id = row[0]["id"]
    for i, mid in enumerate(manager_ids):
        db.execute(
            "INSERT INTO site_managers (site_id, portal_user_id, is_primary) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (site_id, mid, i == 0),
        )
    if "domain_registrar" in body:
        _upsert_domain(
            site_id, domain,
            body.get("domain_registrar"), body.get("domain_account"),
            _parse_date(body.get("domain_registration_date")),
            _parse_date(body.get("domain_expiration_date")),
        )
    return {"ok": True, "id": site_id}


@app.get("/api/site/{site_id}/prices")
def api_site_prices(site_id: int):
    """Итоговые цены сайта (slug -> цена) — для статик-генератора rex.moscow и др."""
    if not db.query("SELECT 1 FROM sites WHERE id = %s", (site_id,)):
        raise HTTPException(404, "site not found")
    return {"site_id": site_id, "prices": rex_price_service.get_site_prices(site_id)}


@app.get("/api/site-hosting")
def api_site_hosting():
    """Сайты по серверам (для страницы /servers в основном интерфейсе)."""
    rows = db.query(
        "SELECT sv.name AS server_name, s.id, s.name, s.domain, "
        "       COALESCE(NULLIF(btrim(s.base_url), ''), 'https://' || s.domain) AS url "
        "FROM sites s JOIN servers sv ON sv.id = s.server_id "
        "ORDER BY sv.name, s.name"
    )
    out = {}
    for row in rows:
        out.setdefault(row["server_name"], []).append({
            "id": row["id"], "name": row["name"],
            "domain": row["domain"], "url": row["url"],
        })
    return {"servers": out}


@app.post("/api/site/events")
async def api_site_events(request: Request):
    """События сайта (публикация статей) от синка rex.moscow. Заголовок X-Notify-Key."""
    expected = os.getenv("METRICS_KEY", "")
    given = request.headers.get("X-Notify-Key", "")
    if not expected or not secrets.compare_digest(given, expected):
        raise HTTPException(401, "unauthorized")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad json")
    try:
        site_id = int(body.get("site_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "bad site_id")
    rows = db.query("SELECT id, name, domain, base_url FROM sites WHERE id = %s", (site_id,))
    if not rows:
        raise HTTPException(404, "site not found")
    site = rows[0]
    base = (site.get("base_url") or "").strip() or ("https://" + (site.get("domain") or ""))
    stamp = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")
    created = 0
    for item in (body.get("items") or [])[:50]:
        slug = str(item.get("slug") or "").strip()
        title = str(item.get("title") or slug).strip() or slug
        if not slug:
            continue
        url = base.rstrip("/") + "/" + slug
        if notifier.add_event(
            "publication", site["name"],
            "Статья опубликована: %s" % title,
            "%s — %s. Опубликовано %s." % (site["name"], url, stamp),
            "publication:%s:%s" % (site_id, slug),
        ):
            created += 1
    return {"ok": True, "created": created}


@app.post("/api/monitor/check")
async def api_monitor_check():
    return await site_watch.check_all()


@app.post("/api/monitor/provider")
async def api_monitor_provider(request: Request):
    body = await request.json()
    try:
        return await site_watch.check_provider(body.get("ip", ""), body.get("host", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/sites/check")
def api_sites_check():
    return site_monitor.check_all()


@app.post("/api/sites/{site_id}/check")
def api_site_check(site_id: int):
    row = db.query(
        "SELECT id, name, domain, base_url, platform, wc_key, wc_secret FROM sites WHERE id = %s",
        (site_id,),
    )
    if not row:
        raise HTTPException(404, "site not found")
    return site_monitor.check_site(row[0])


@app.get("/api/notifications")
def api_notifications(limit: int = 30):
    limit = max(1, min(int(limit), 100))
    items = db.query(
        "SELECT id, kind, severity, subject, title, message, status, "
        "       created_at, updated_at, resolved_at, read_at "
        "FROM notifications ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    unread = db.query(
        "SELECT count(*) AS c FROM notifications "
        "WHERE status IN ('active', 'event') AND read_at IS NULL"
    )[0]["c"]
    return {"unread": int(unread), "items": items}


@app.post("/api/notifications/read")
async def api_notifications_read(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if payload.get("all"):
        updated = db.execute("UPDATE notifications SET read_at = now() WHERE read_at IS NULL")
        return {"ok": True, "updated": updated}
    ids = []
    for value in payload.get("ids") or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {"ok": True, "updated": 0}
    updated = db.execute(
        "UPDATE notifications SET read_at = now() WHERE id = ANY(%s) AND read_at IS NULL",
        (ids,),
    )
    return {"ok": True, "updated": updated}


@app.post("/api/notifications/check")
async def api_notifications_check():
    summary = await asyncio.to_thread(notifier.check_all)
    return {"ok": True, "summary": summary}


PRICE_STATUS_RU = {
    "uploaded": "загружен",
    "parsing": "разбор",
    "matching": "сопоставление",
    "review": "на проверке",
    "partial": "применён частично",
    "applied": "применён",
    "failed": "ошибка",
}


def _price_json(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


@app.get("/prices", response_class=HTMLResponse)
def prices_page(request: Request):
    rows = db.query(
        "SELECT pl.*, s.name AS supplier_name FROM price_lists pl "
        "LEFT JOIN suppliers s ON s.id = pl.supplier_id "
        "ORDER BY pl.uploaded_at DESC LIMIT 300"
    )
    return templates.TemplateResponse(request, "price_tasks.html", {
        "active": "prices", "rows": rows, "status_ru": PRICE_STATUS_RU,
    })


@app.get("/prices/new", response_class=HTMLResponse)
def price_new_page(request: Request):
    suppliers = db.query("SELECT id, name, amalor_slug FROM suppliers ORDER BY name")
    return templates.TemplateResponse(request, "price_new.html", {
        "active": "price_new", "suppliers": suppliers,
    })


@app.post("/api/prices/upload")
async def api_price_upload(
    request: Request,
    file: UploadFile = File(...),
    supplier_id: str = Form(""),
    supplier_name: str = Form(""),
    price_date: str = Form(""),
    note: str = Form(""),
):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Пустой файл")
    try:
        stored, ext = price_service.save_upload(data, file.filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    sid = None
    if supplier_id.strip():
        try:
            sid = int(supplier_id)
        except ValueError:
            raise HTTPException(400, "Некорректный поставщик")
        if not db.query("SELECT id FROM suppliers WHERE id = %s", (sid,)):
            raise HTTPException(400, "Поставщик не найден")
    elif supplier_name.strip():
        name = supplier_name.strip()
        row = db.query("SELECT id FROM suppliers WHERE lower(name) = lower(%s) LIMIT 1", (name,))
        if row:
            sid = row[0]["id"]
        else:
            sid = db.query("INSERT INTO suppliers (name) VALUES (%s) RETURNING id", (name,))[0]["id"]
    if not sid:
        raise HTTPException(400, "Выберите или укажите поставщика")

    date_value = None
    if price_date.strip():
        try:
            date_value = datetime.strptime(price_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "Некорректная дата прайса")
    else:
        date_value = price_service.guess_price_date(file.filename)

    price_id = price_service.create_price_list(
        file.filename, stored, ext, sid, date_value, note.strip() or None
    )
    price_service.start_processing(price_id)
    return {"ok": True, "id": price_id}


@app.get("/api/prices/search-products")
def api_price_search_products(q: str = "", supplier_id: int = 0, only_supplier: int = 1):
    supplier_slug = None
    if supplier_id:
        rows = db.query("SELECT amalor_slug FROM suppliers WHERE id = %s", (supplier_id,))
        if rows:
            supplier_slug = rows[0]["amalor_slug"]
    with db.connect() as conn:
        return {
            "items": price_matcher.search_products(
                conn, q, 50, supplier_slug, bool(only_supplier)
            )
        }


@app.get("/api/prices/{price_id}")
def api_price_status(price_id: int):
    rec = db.query(
        "SELECT pl.*, s.name AS supplier_name FROM price_lists pl "
        "LEFT JOIN suppliers s ON s.id = pl.supplier_id WHERE pl.id = %s",
        (price_id,),
    )
    if not rec:
        raise HTTPException(404, "Задача не найдена")
    counts = db.query(
        "SELECT count(*) AS total, "
        " count(*) FILTER (WHERE matched_product_id IS NOT NULL) AS matched, "
        " count(*) FILTER (WHERE matched_product_id IS NULL AND match_status <> 'no_price') AS unmatched, "
        " count(*) FILTER (WHERE match_status = 'pending') AS pending, "
        " count(*) FILTER (WHERE match_status = 'auto') AS auto, "
        " count(*) FILTER (WHERE match_status = 'approved') AS approved, "
        " count(*) FILTER (WHERE match_status = 'applied') AS applied, "
        " count(*) FILTER (WHERE match_status = 'rejected') AS rejected, "
        " count(*) FILTER (WHERE match_status = 'no_price') AS no_price, "
        " count(*) FILTER (WHERE match_status = 'kept') AS kept "
        "FROM price_list_items WHERE price_list_id = %s",
        (price_id,),
    )[0]
    return {"price": rec[0], "counts": counts}


@app.get("/prices/{price_id}", response_class=HTMLResponse)
def price_detail_page(
    request: Request, price_id: int, status: str = "all", q: str = "",
    page: int = 1, per: int = 50,
):
    rec = db.query(
        "SELECT pl.*, s.name AS supplier_name, s.amalor_slug AS supplier_amalor_slug "
        "FROM price_lists pl LEFT JOIN suppliers s ON s.id = pl.supplier_id "
        "WHERE pl.id = %s",
        (price_id,),
    )
    if not rec:
        raise HTTPException(404, "Задача не найдена")
    price = rec[0]
    per = max(20, min(200, int(per or 50)))
    page = max(1, int(page or 1))
    offset = (page - 1) * per

    where = ["i.price_list_id = %s"]
    params = [price_id]
    state = (status or "all").strip().lower()
    if state == "unmatched":
        where.append("i.matched_product_id IS NULL AND i.match_status <> 'no_price'")
    elif state == "pending":
        where.append("i.match_status = 'pending'")
    elif state == "auto":
        where.append("i.match_status = 'auto'")
    elif state == "approved":
        where.append("i.match_status IN ('approved', 'applied')")
    elif state == "applied":
        where.append("i.match_status = 'applied'")
    elif state == "rejected":
        where.append("i.match_status = 'rejected'")
    elif state == "no_price":
        where.append("i.match_status = 'no_price'")
    elif state == "kept":
        where.append("i.match_status = 'kept'")
    elif state == "matched":
        where.append("i.matched_product_id IS NOT NULL")
    query = (q or "").strip()
    if query:
        like = "%" + query + "%"
        where.append("(i.raw_name ILIKE %s OR i.product_name ILIKE %s OR i.product_sku ILIKE %s)")
        params += [like, like, like]
    where_sql = " AND ".join(where)

    total = db.query(
        "SELECT count(*) AS n FROM price_list_items i WHERE " + where_sql, tuple(params)
    )[0]["n"]
    items = db.query(
        "SELECT i.*, p.sku AS base_sku, p.name AS db_name, p.price AS db_price, "
        "       p.manufacturer_sku AS base_msku "
        "FROM price_list_items i LEFT JOIN products p ON p.id = i.matched_product_id "
        "WHERE " + where_sql + " ORDER BY i.row_num LIMIT %s OFFSET %s",
        tuple(params + [per, offset]),
    )
    for item in items:
        item["prices"] = _price_json(item.get("prices"), {})
        item["candidates"] = _price_json(item.get("candidates"), [])
        item["raw_extra"] = _price_json(item.get("raw_extra"), {})

    counts = db.query(
        "SELECT count(*) AS total, "
        " count(*) FILTER (WHERE matched_product_id IS NOT NULL) AS matched, "
        " count(*) FILTER (WHERE matched_product_id IS NULL AND match_status <> 'no_price') AS unmatched, "
        " count(*) FILTER (WHERE match_status = 'pending') AS pending, "
        " count(*) FILTER (WHERE match_status = 'auto') AS auto, "
        " count(*) FILTER (WHERE match_status = 'approved') AS approved, "
        " count(*) FILTER (WHERE match_status = 'applied') AS applied, "
        " count(*) FILTER (WHERE match_status = 'rejected') AS rejected, "
        " count(*) FILTER (WHERE match_status = 'no_price') AS no_price, "
        " count(*) FILTER (WHERE match_status = 'kept') AS kept "
        "FROM price_list_items WHERE price_list_id = %s",
        (price_id,),
    )[0]
    columns = db.query(
        "SELECT * FROM price_list_columns WHERE price_list_id = %s ORDER BY col_index",
        (price_id,),
    )
    base_col = next((c for c in columns if c["is_base"]), columns[0] if columns else None)
    for item in items:
        item["price_cells"] = [
            {"col": c, "value": item["prices"].get(str(c["col_index"]))}
            for c in columns
        ]
        raw = item["prices"].get(str(base_col["col_index"])) if base_col else None
        manual = item.get("manual_price")
        if manual is not None:
            item["base_retail"] = float(manual)
        elif raw is not None and base_col is not None:
            if base_col["price_type"] == "input":
                markup = float(base_col.get("markup_pct") or 0)
                item["base_retail"] = round(float(raw) * (1 + markup / 100.0), 2)
            else:
                item["base_retail"] = float(raw)
        else:
            item["base_retail"] = None
    price["meta"] = _price_json(price.get("meta"), {})
    return templates.TemplateResponse(request, "price_detail.html", {
        "active": "price_task", "price": price, "items": items,
        "counts": counts, "columns": columns, "base_col": base_col,
        "state": state, "q": q, "page": page, "per": per, "total": total,
        "status_ru": PRICE_STATUS_RU,
    })


@app.post("/api/prices/{price_id}/columns")
async def api_price_columns(price_id: int, request: Request):
    body = await request.json()
    try:
        return price_service.update_columns(price_id, body.get("columns") or [])
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/prices/{price_id}/approve-auto")
def api_price_approve_auto(price_id: int):
    if not db.query("SELECT id FROM price_lists WHERE id = %s", (price_id,)):
        raise HTTPException(404, "Задача не найдена")
    return price_service.approve_auto(price_id)


@app.post("/api/prices/{price_id}/match")
def api_price_match(price_id: int):
    if not db.query("SELECT id FROM price_lists WHERE id = %s", (price_id,)):
        raise HTTPException(404, "Задача не найдена")
    db.execute(
        "UPDATE price_list_items SET matched_product_id = NULL, match_score = NULL, "
        "match_method = NULL, match_status = 'pending', candidates = '[]'::jsonb, "
        "product_sku = NULL, product_name = NULL, matched_at = NULL "
        "WHERE price_list_id = %s AND match_status IN ('pending', 'auto')",
        (price_id,),
    )
    price_service.run_matching(price_id, only_open=True)
    return {"ok": True}


@app.post("/api/prices/{price_id}/items/{item_id}")
async def api_price_item(price_id: int, item_id: int, request: Request):
    body = await request.json()
    try:
        return price_service.update_item(
            price_id, item_id, body.get("product_id"), body.get("status"),
            body.get("manual_price"), "manual_price" in body,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/prices/{price_id}/apply")
async def api_price_apply(price_id: int, request: Request):
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
    try:
        return price_service.apply_price_list(
            price_id, bool(body.get("include_auto")), bool(body.get("include_applied"))
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/prices/{price_id}")
def api_price_delete(price_id: int):
    if not db.query("SELECT id FROM price_lists WHERE id = %s", (price_id,)):
        raise HTTPException(404, "Задача не найдена")
    return price_service.delete_price_list(price_id)


@app.post("/api/prices/{price_id}/assortment")
def api_price_assortment(price_id: int):
    if not db.query("SELECT id FROM price_lists WHERE id = %s", (price_id,)):
        raise HTTPException(404, "Задача прайса не найдена")
    try:
        task_id = publication_service.create_assortment_task(price_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "id": task_id}


@app.get("/publication/assortment", response_class=HTMLResponse)
def publication_assortment_page(request: Request):
    rows = db.query(
        "SELECT pt.*, s.name AS supplier_name, pl.filename "
        "FROM publication_tasks pt "
        "LEFT JOIN suppliers s ON s.id = pt.supplier_id "
        "LEFT JOIN price_lists pl ON pl.id = pt.price_list_id "
        "WHERE pt.type = 'assortment' ORDER BY pt.created_at DESC"
    )
    return templates.TemplateResponse(request, "publication_assortment.html", {
        "active": "publication_assortment", "rows": rows,
    })


@app.get("/publication/assortment/{task_id}", response_class=HTMLResponse)
def publication_assortment_task_page(request: Request, task_id: int):
    task = db.query(
        "SELECT pt.*, s.name AS supplier_name, pl.filename "
        "FROM publication_tasks pt "
        "LEFT JOIN suppliers s ON s.id = pt.supplier_id "
        "LEFT JOIN price_lists pl ON pl.id = pt.price_list_id "
        "WHERE pt.id = %s AND pt.type = 'assortment'",
        (task_id,),
    )
    if not task:
        raise HTTPException(404, "Задача публикации не найдена")
    task = task[0]
    task["meta"] = _price_json(task.get("meta"), {})
    columns = task["meta"].get("columns") or []
    items = db.query(
        "SELECT * FROM publication_items WHERE task_id = %s ORDER BY id", (task_id,)
    )
    for item in items:
        item["prices"] = _price_json(item.get("prices"), {})
        item["raw_extra"] = _price_json(item.get("raw_extra"), {})
        item["raw_row"] = _price_json(item.get("raw_row"), [])
        item["raw_data"] = _price_json(item.get("raw_data"), {})
        item["price_cells"] = [
            {"col": c, "value": item["prices"].get(str(c["col_index"]))}
            for c in columns
        ]
    return templates.TemplateResponse(request, "publication_assortment_task.html", {
        "active": "publication_assortment", "task": task,
        "columns": columns, "items": items,
    })


@app.get("/api/site/v1/updates")
def api_site_updates(request: Request, after_version: int = 0, limit: int = 200):
    key = (request.headers.get("X-Base-Api-Key") or "").strip()
    if not key:
        raise HTTPException(401, "API key required")
    sites = db.query(
        "SELECT id, name, domain, currency FROM sites "
        "WHERE api_key = %s AND active = true",
        (key,),
    )
    if not sites:
        raise HTTPException(401, "Invalid API key")
    site = sites[0]
    limit = max(1, min(500, int(limit or 200)))
    after_version = max(0, int(after_version or 0))
    rows = db.query(
        "SELECT su.id, su.version, su.product_id, su.old_price, su.new_price, "
        "       su.currency, su.created_at, p.sku, p.manufacturer_sku, p.name, "
        "       (SELECT ps.site_product_id FROM product_sites ps "
        "        WHERE ps.site_id = su.site_id AND ps.product_id = su.product_id "
        "        LIMIT 1) AS site_product_id, "
        "       (SELECT ps.site_sku FROM product_sites ps "
        "        WHERE ps.site_id = su.site_id AND ps.product_id = su.product_id "
        "        LIMIT 1) AS site_sku "
        "FROM site_updates su JOIN products p ON p.id = su.product_id "
        "WHERE su.site_id = %s AND su.version > %s "
        "ORDER BY su.version, su.id LIMIT %s",
        (site["id"], after_version, limit),
    )
    last_version = max([r["version"] for r in rows], default=after_version)
    return {
        "site": {
            "id": site["id"], "name": site["name"], "domain": site["domain"],
            "currency": site["currency"],
        },
        "items": [
            {
                "id": r["id"], "version": r["version"], "product_id": r["product_id"],
                "site_product_id": r["site_product_id"], "site_sku": r["site_sku"],
                "sku": r["sku"], "manufacturer_sku": r["manufacturer_sku"],
                "name": r["name"],
                "old_price": float(r["old_price"]) if r["old_price"] is not None else None,
                "new_price": float(r["new_price"]) if r["new_price"] is not None else None,
                "currency": r["currency"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "last_version": last_version,
        "has_more": len(rows) >= limit,
    }


@app.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(request: Request):
    rows = db.query(
        "SELECT s.id, s.name, s.slug, s.portal_id, s.website, "
        "count(sp.id) AS products "
        "FROM suppliers s LEFT JOIN supplier_products sp ON sp.supplier_id = s.id "
        "WHERE s.portal_id IS NOT NULL "
        "GROUP BY s.id ORDER BY s.name"
    )
    return templates.TemplateResponse(request, "suppliers.html", {"active": "base_suppliers", "rows": rows})
