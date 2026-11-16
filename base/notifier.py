"""Уведомления: сайты, серверы, нагрузка CPU/RAM, TTFB.

Источники данных:
  * site_checks (PostgreSQL «Базы») — проверки сайтов из site_monitor;
  * metrics.db (flux) — метрики серверов, которые собирает metrics-pull.

Повтор одной и той же проблемы не создаёт новых записей: активное
уведомление обновляется по dedup_key, восстановление его закрывает.
"""
import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import db

logger = logging.getLogger("base.notifier")

CHECK_INTERVAL = int(os.getenv("NOTIFY_INTERVAL_SECONDS", "60"))
CPU_PCT = float(os.getenv("NOTIFY_CPU_PCT", "80"))
RAM_PCT = float(os.getenv("NOTIFY_RAM_PCT", "80"))
TTFB_MS = int(os.getenv("NOTIFY_TTFB_MS", "1500"))
SERVER_STALE_SECONDS = int(os.getenv("NOTIFY_SERVER_STALE_SECONDS", "300"))
LOAD_WINDOW_SECONDS = int(os.getenv("NOTIFY_LOAD_WINDOW_SECONDS", "300"))
METRICS_DB = os.getenv("FLUX_METRICS_DB", "/home/kirill/flux/metrics.db")

MSK = timezone(timedelta(hours=3))
_task = None


def _fmt_ts(ts):
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, MSK)
        elif ts.tzinfo:
            dt = ts.astimezone(MSK)
        else:
            dt = ts.replace(tzinfo=timezone.utc).astimezone(MSK)
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return ""


def _upsert(kind, severity, subject, title, message, dedup_key):
    db.execute(
        "INSERT INTO notifications (dedup_key, kind, severity, subject, title, message) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (dedup_key) WHERE status = 'active' DO UPDATE SET "
        "severity = EXCLUDED.severity, title = EXCLUDED.title, "
        "message = EXCLUDED.message, updated_at = now()",
        (dedup_key, kind, severity, subject[:200], title[:300], message[:1000]),
    )


def _resolve(dedup_key):
    db.execute(
        "UPDATE notifications SET status = 'resolved', resolved_at = now(), updated_at = now() "
        "WHERE dedup_key = %s AND status = 'active'",
        (dedup_key,),
    )


def add_event(kind, subject, title, message, dedup_key):
    """Событие (публикация, изменение цен): без закрытия, без повторов по dedup_key."""
    rows = db.query(
        "INSERT INTO notifications (dedup_key, kind, severity, subject, title, message, status) "
        "SELECT %s, %s, 'info', %s, %s, %s, 'event' "
        "WHERE NOT EXISTS (SELECT 1 FROM notifications WHERE dedup_key = %s) "
        "RETURNING id",
        (dedup_key, kind, subject[:200], title[:300], message[:1000], dedup_key),
    )
    return bool(rows)


def _recent_checks():
    rows = db.query(
        "SELECT site_id, ok, status_code, ttfb_ms, error, checked_at, "
        "       row_number() OVER (PARTITION BY site_id ORDER BY checked_at DESC) AS rn "
        "FROM site_checks WHERE checked_at > now() - interval '2 hours'"
    )
    out = {}
    for r in rows:
        if r["rn"] <= 2:
            out.setdefault(r["site_id"], []).append(r)
    return out


def _check_sites():
    sites = db.query("SELECT id, name, domain, base_url FROM sites WHERE active ORDER BY id")
    checks = _recent_checks()
    for site in sites:
        rows = checks.get(site["id"]) or []
        if not rows:
            continue
        url = (site.get("base_url") or "").strip() or ("https://" + (site.get("domain") or ""))
        key_down = "site_down:%s" % site["id"]
        key_ttfb = "ttfb_high:%s" % site["id"]

        if len(rows) >= 2 and not any(r["ok"] for r in rows[:2]):
            last = rows[0]
            if last.get("error"):
                why = last["error"]
            elif last.get("status_code"):
                why = "HTTP %s" % last["status_code"]
            else:
                why = "нет ответа"
            _upsert(
                "site_down", "critical", site["name"],
                "Сайт недоступен: %s" % site["name"],
                "%s — %s. Проверка %s." % (url, why, _fmt_ts(last["checked_at"])),
                key_down,
            )
        else:
            _resolve(key_down)

        if (len(rows) >= 2
                and all(r["ok"] and r.get("ttfb_ms") is not None for r in rows[:2])
                and all(r["ttfb_ms"] >= TTFB_MS for r in rows[:2])):
            last = rows[0]
            _upsert(
                "ttfb_high", "warning", site["name"],
                "Медленный отклик: %s" % site["name"],
                "%s — TTFB %d мс (порог %d мс). Проверка %s."
                % (url, int(last["ttfb_ms"]), TTFB_MS, _fmt_ts(last["checked_at"])),
                key_ttfb,
            )
        else:
            _resolve(key_ttfb)


def _metrics_rows():
    try:
        con = sqlite3.connect("file:%s?mode=ro" % METRICS_DB, uri=True, timeout=5)
    except Exception:
        return None
    try:
        last = con.execute(
            "SELECT server, MAX(ts) FROM server_metrics GROUP BY server"
        ).fetchall()
        load = con.execute(
            "SELECT server, AVG(cpu), "
            "       AVG(CASE WHEN ram_total > 0 THEN 100.0 * ram_used / ram_total END), "
            "       COUNT(*) "
            "FROM server_metrics WHERE ts >= ? GROUP BY server",
            (int(time.time()) - LOAD_WINDOW_SECONDS,),
        ).fetchall()
        return {"last": last, "load": load}
    except Exception:
        logger.exception("metrics.db read failed")
        return None
    finally:
        con.close()


def _check_servers(data):
    now = time.time()
    last = data["last"]
    if not last:
        return
    stale = [(name, ts) for name, ts in last if now - ts > SERVER_STALE_SECONDS]
    if stale and len(stale) == len(last):
        oldest = min(ts for _n, ts in last)
        _upsert(
            "monitor_down", "critical", "Мониторинг серверов",
            "Нет свежих метрик ни от одного сервера",
            "Похоже, не работает сбор метрик (metrics-pull). Последние данные: %s."
            % _fmt_ts(oldest),
            "monitor_down",
        )
        for name, _ts in last:
            _resolve("server_down:%s" % name)
        return
    _resolve("monitor_down")
    for name, ts in last:
        key = "server_down:%s" % name
        age = int(now - ts)
        if age > SERVER_STALE_SECONDS:
            _upsert(
                "server_down", "critical", name,
                "Сервер недоступен: %s" % name,
                "Нет метрик %d мин (порог %d мин). Последние данные: %s. "
                "Проверьте питание, сеть и ssh." % (age // 60, SERVER_STALE_SECONDS // 60, _fmt_ts(ts)),
                key,
            )
        else:
            _resolve(key)


def _check_load(data):
    now = time.time()
    last_ts = {name: ts for name, ts in data["last"]}
    for server, cpu, ram, n in data["load"]:
        if now - (last_ts.get(server) or 0) > SERVER_STALE_SECONDS:
            _resolve("cpu_high:%s" % server)
            _resolve("ram_high:%s" % server)
            continue
        if n and n >= 3 and cpu is not None and cpu > CPU_PCT:
            _upsert(
                "cpu_high", "warning", server,
                "Высокая нагрузка CPU: %s" % server,
                "Средняя за %d мин: %.0f%% (порог %.0f%%)."
                % (LOAD_WINDOW_SECONDS // 60, cpu, CPU_PCT),
                "cpu_high:%s" % server,
            )
        else:
            _resolve("cpu_high:%s" % server)
        if n and n >= 3 and ram is not None and ram > RAM_PCT:
            _upsert(
                "ram_high", "warning", server,
                "Высокая нагрузка RAM: %s" % server,
                "Средняя за %d мин: %.0f%% (порог %.0f%%)."
                % (LOAD_WINDOW_SECONDS // 60, ram, RAM_PCT),
                "ram_high:%s" % server,
            )
        else:
            _resolve("ram_high:%s" % server)


def _cleanup():
    db.execute(
        "DELETE FROM notifications "
        "WHERE (status = 'resolved' AND resolved_at < now() - interval '30 days') "
        "   OR created_at < now() - interval '90 days'"
    )


def check_all():
    summary = {"sites": False, "servers": False, "load": False}
    try:
        _check_sites()
        summary["sites"] = True
    except Exception:
        logger.exception("site notification checks failed")
    data = _metrics_rows()
    if data:
        try:
            _check_servers(data)
            summary["servers"] = True
        except Exception:
            logger.exception("server notification checks failed")
        try:
            _check_load(data)
            summary["load"] = True
        except Exception:
            logger.exception("load notification checks failed")
    try:
        _cleanup()
    except Exception:
        logger.exception("notifications cleanup failed")
    return summary


async def loop():
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.to_thread(check_all)
        except Exception:
            logger.exception("notifier iteration failed")
        await asyncio.sleep(CHECK_INTERVAL)


def start():
    """Запустить фоновый цикл (один раз на процесс)."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(loop())
    return _task
