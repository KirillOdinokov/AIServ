"""Мониторинг доступности сайтов: пинг раз в 5 минут + TTFB.

Проверки пишутся в таблицу site_checks (лог аптайма по каждому сайту).
Интервал задаётся SITE_CHECK_INTERVAL (секунды).
"""
import asyncio
import base64
import logging
import os
import time
import urllib.error
import urllib.request

import db

logger = logging.getLogger("base.site_monitor")

CHECK_INTERVAL = int(os.getenv("SITE_CHECK_INTERVAL", "300"))
TIMEOUT = 20
UA = "Mozilla/5.0 (compatible; AI-ENIX-Monitor/1.0)"

_lock = asyncio.Lock()
_task = None


def _site_url(site):
    url = (site.get("base_url") or "").strip()
    if not url:
        url = "https://" + (site.get("domain") or "").strip()
    return url


def _probe(site):
    """Синхронная проверка одного сайта (выполняется в отдельном потоке)."""
    url = _site_url(site)
    t0 = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            ttfb = int((time.perf_counter() - t0) * 1000)
            try:
                resp.read(2048)
            except Exception:
                pass
            total = int((time.perf_counter() - t0) * 1000)
            code = resp.status
            ok = 200 <= code < 400
            return {"ok": ok, "status_code": code, "ttfb_ms": ttfb,
                    "total_ms": total, "error": None}
    except urllib.error.HTTPError as e:
        ttfb = int((time.perf_counter() - t0) * 1000)
        return {"ok": 200 <= e.code < 400, "status_code": e.code,
                "ttfb_ms": ttfb, "total_ms": ttfb, "error": None}
    except Exception as e:
        total = int((time.perf_counter() - t0) * 1000)
        return {"ok": False, "status_code": None, "ttfb_ms": None,
                "total_ms": total, "error": str(e)[:500]}


def _store(site_id, res):
    db.execute(
        "INSERT INTO site_checks (site_id, ok, status_code, ttfb_ms, total_ms, error) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (site_id, res["ok"], res["status_code"], res["ttfb_ms"], res["total_ms"], res["error"]),
    )


def _wc_products_count(site):
    """Количество товаров сайта через WooCommerce REST API (X-WP-Total)."""
    url = _site_url(site).rstrip("/") + "/wp-json/wc/v3/products?per_page=1"
    auth = base64.b64encode(
        ("%s:%s" % (site["wc_key"], site["wc_secret"])).encode()
    ).decode()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Authorization": "Basic " + auth})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        total = resp.headers.get("X-WP-Total")
        return int(total) if total else None


def check_site(site):
    res = _probe(site)
    _store(site["id"], res)
    if site.get("platform") == "woocommerce" and site.get("wc_key") and site.get("wc_secret"):
        try:
            total = _wc_products_count(site)
            if total is not None:
                db.execute(
                    "UPDATE sites SET remote_products = %s, remote_products_at = now() WHERE id = %s",
                    (total, site["id"]),
                )
        except Exception:
            logger.exception("site %s: wc products count failed", site.get("id"))
    logger.info("site %s (%s): ok=%s code=%s ttfb=%sms", site["id"], site.get("domain"),
                res["ok"], res["status_code"], res["ttfb_ms"])
    return {"site_id": site["id"], **res}


def check_all():
    sites = db.query(
        "SELECT id, name, domain, base_url, platform, wc_key, wc_secret "
        "FROM sites WHERE active ORDER BY id"
    )
    results = []
    for site in sites:
        try:
            results.append(check_site(site))
        except Exception as e:
            logger.exception("site %s check failed", site.get("id"))
            results.append({"site_id": site["id"], "ok": False, "error": str(e)[:500]})
    try:
        db.execute("DELETE FROM site_checks WHERE checked_at < now() - interval '90 days'")
    except Exception:
        logger.exception("site_checks cleanup failed")
    return {"ok": True, "checked": len(results), "results": results}


async def _run_once():
    async with _lock:
        await asyncio.to_thread(check_all)


async def loop():
    await asyncio.sleep(5)
    while True:
        try:
            await _run_once()
        except Exception:
            logger.exception("site monitor iteration failed")
        await asyncio.sleep(CHECK_INTERVAL)


def start():
    """Запустить фоновый цикл (один раз на процесс)."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(loop())
    return _task
