"""Хранилище метрик серверов (SQLite) для страницы мониторинга /servers.

Отдельная БД metrics.db, чтобы не смешивать с Базой товаров.
Объём: 8 серверов × 1440 сэмплов/день ≈ 11.5k строк/день — SQLite держит легко.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

import config

DB_PATH = Path(config.BASE_DIR) / "metrics.db"

_lock = threading.Lock()

_PERIODS = {
    # period: (span_sec, bucket_sec, strftime)
    "hour": (3600, 60, "%H:%M"),
    "day": (86400, 300, "%H:%M"),
    "week": (7 * 86400, 3600, "%d.%m %H:%M"),
    "month": (30 * 86400, 86400, "%d.%m"),
}


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _columns(c, table):
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def init_db():
    with _conn() as c:
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        c.execute("""
            CREATE TABLE IF NOT EXISTS server_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server TEXT NOT NULL,
                ts INTEGER NOT NULL,
                cpu REAL,
                cpu_temp REAL,
                cpu_cores TEXT,
                gpu_temp REAL,
                ram_used INTEGER,
                ram_total INTEGER,
                disk_used INTEGER,
                disk_total INTEGER,
                net_rx INTEGER,
                net_tx INTEGER,
                load1 REAL,
                uptime INTEGER,
                disk_json TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_sm_server_ts "
                  "ON server_metrics(server, ts)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS metrics_raw_state (
                server TEXT PRIMARY KEY,
                ts INTEGER NOT NULL,
                cpu_total INTEGER,
                cpu_idle INTEGER,
                cpu_cores TEXT,
                net_rx INTEGER,
                net_tx INTEGER
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS server_info (
                server TEXT PRIMARY KEY,
                updated_at INTEGER NOT NULL,
                info_json TEXT
            )
        """)
        # миграции для старых БД
        cols = _columns(c, "server_metrics")
        if "cpu_cores" not in cols:
            c.execute("ALTER TABLE server_metrics ADD COLUMN cpu_cores TEXT")
        if "cpu_temp" not in cols:
            c.execute("ALTER TABLE server_metrics ADD COLUMN cpu_temp REAL")
        if "gpu_temp" not in cols:
            c.execute("ALTER TABLE server_metrics ADD COLUMN gpu_temp REAL")
        rcols = _columns(c, "metrics_raw_state")
        if "cpu_cores" not in rcols:
            c.execute("ALTER TABLE metrics_raw_state ADD COLUMN cpu_cores TEXT")
        c.commit()


def _float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _primary_disk(disk):
    """Основной раздел для истории: корень "/", иначе самый большой."""
    if not disk:
        return None, None
    if "/" in disk:
        info = disk["/"]
        try:
            return int(info.get("used") or 0), int(info.get("total") or 0)
        except (TypeError, ValueError, AttributeError):
            pass
    best = None
    for info in disk.values():
        try:
            total = int(info.get("total") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if total and (best is None or total > best[0]):
            best = (total, int(info.get("used") or 0))
    if best is None:
        return None, None
    return best[1], best[0]


def add_sample(data):
    """Вставить один сэмпл. data — словарь из агента/пуллера."""
    server = str(data.get("server") or "").strip()
    if not server:
        raise ValueError("empty server")
    ts = int(data.get("ts") or time.time())
    disk = data.get("disk") or {}
    disk_used, disk_total = _primary_disk(disk)
    cores = data.get("cpu_cores")
    row = (
        server, ts,
        _float(data.get("cpu")),
        _float(data.get("cpu_temp")),
        json.dumps(cores) if cores is not None else None,
        _float(data.get("gpu_temp")),
        _int(data.get("ram_used")),
        _int(data.get("ram_total")),
        disk_used, disk_total,
        _int(data.get("net_rx")),
        _int(data.get("net_tx")),
        _float(data.get("load1")),
        _int(data.get("uptime")),
        json.dumps(disk, ensure_ascii=False),
    )
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO server_metrics "
            "(server, ts, cpu, cpu_temp, cpu_cores, gpu_temp, ram_used, ram_total, disk_used, "
            " disk_total, net_rx, net_tx, load1, uptime, disk_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        c.commit()


def set_server_info(server, info):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO server_info (server, updated_at, info_json) "
            "VALUES (?,?,?) ON CONFLICT(server) DO UPDATE SET "
            "updated_at=excluded.updated_at, info_json=excluded.info_json",
            (server, int(time.time()), json.dumps(info, ensure_ascii=False)))
        c.commit()


def get_server_info(server):
    with _lock, _conn() as c:
        r = c.execute("SELECT info_json FROM server_info WHERE server=?",
                      (server,)).fetchone()
        if not r or not r["info_json"]:
            return None
        try:
            return json.loads(r["info_json"])
        except ValueError:
            return None


def list_servers():
    """Список серверов с последним сэмплом и конфигом."""
    with _lock, _conn() as c:
        names = [r["server"] for r in c.execute(
            "SELECT DISTINCT server FROM server_metrics ORDER BY server")]
        infos = {r["server"]: r["info_json"]
                 for r in c.execute("SELECT server, info_json FROM server_info")}
        result = []
        for name in names:
            r = c.execute(
                "SELECT * FROM server_metrics WHERE server=? "
                "ORDER BY ts DESC LIMIT 1", (name,)).fetchone()
            if r is None:
                continue
            try:
                disk = json.loads(r["disk_json"]) if r["disk_json"] else {}
            except (ValueError, TypeError):
                disk = {}
            try:
                info = json.loads(infos[name]) if infos.get(name) else None
            except ValueError:
                info = None
            result.append({
                "server": name,
                "ts": r["ts"],
                "cpu": r["cpu"],
                "cpu_temp": r["cpu_temp"],
                "gpu_temp": r["gpu_temp"],
                "cpu_cores": _load_cores(r["cpu_cores"]),
                "ram_used": r["ram_used"],
                "ram_total": r["ram_total"],
                "disk": disk,
                "disk_used": r["disk_used"],
                "disk_total": r["disk_total"],
                "net_rx": r["net_rx"],
                "net_tx": r["net_tx"],
                "load1": r["load1"],
                "uptime": r["uptime"],
                "info": info,
            })
        return result


def _load_cores(raw):
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else None
    except ValueError:
        return None


def get_server(server):
    for s in list_servers():
        if s["server"] == server:
            return s
    return None


def series(server, period):
    """Агрегированный ряд по серверу за период (включая per-core CPU и диск в ГБ)."""
    if period not in _PERIODS:
        period = "day"
    span, bucket, fmt = _PERIODS[period]
    now = int(time.time())
    start = now - span
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT (ts/?)*? AS b, "
            " AVG(cpu) AS cpu, "
            " AVG(cpu_temp) AS cpu_temp, "
            " AVG(gpu_temp) AS gpu_temp, "
            " AVG(ram_used) AS ram_used, MAX(ram_total) AS ram_total, "
            " AVG(disk_used) AS disk_used, MAX(disk_total) AS disk_total, "
            " AVG(net_rx) AS net_rx, AVG(net_tx) AS net_tx, AVG(load1) AS load1, "
            " cpu_cores "
            "FROM server_metrics WHERE server=? AND ts>=? "
            "GROUP BY b, cpu_cores ORDER BY b",
            (bucket, bucket, server, start)).fetchall()

    # сгруппировать по бакетам (из-за GROUP BY b, cpu_cores строк на бакет > 1)
    buckets = {}
    latest_disk_total = 0
    for r in rows:
        b = int(r["b"])
        d = buckets.setdefault(b, {
            "cpu": [], "cpu_temp": [], "gpu_temp": [], "ram_used": [], "ram_total": 0, "disk_used": [],
            "disk_total": 0, "net_rx": [], "net_tx": [], "load1": [],
            "cores": [],
        })
        for key in ("cpu", "cpu_temp", "gpu_temp", "ram_used", "disk_used", "net_rx", "net_tx", "load1"):
            if r[key] is not None:
                d[key].append(r[key])
        if r["ram_total"]:
            d["ram_total"] = max(d["ram_total"], r["ram_total"])
        if r["disk_total"]:
            d["disk_total"] = max(d["disk_total"], r["disk_total"])
            latest_disk_total = r["disk_total"]
        cores = _load_cores(r["cpu_cores"])
        if cores:
            d["cores"].append(cores)

    ncores = 0
    for d in buckets.values():
        for arr in d["cores"]:
            ncores = max(ncores, len(arr))

    labels = []
    s = {"cpu": [], "cpu_temp": [], "gpu_temp": [], "ram": [], "disk_gb": [], "net_rx": [],
         "net_tx": [], "load1": [], "cores": [[] for _ in range(ncores)]}
    gib = 1024 ** 3
    for b in sorted(buckets):
        d = buckets[b]
        labels.append(time.strftime(fmt, time.localtime(b)))
        s["cpu"].append(_avg(d["cpu"]))
        s["cpu_temp"].append(_avg(d["cpu_temp"]))
        s["gpu_temp"].append(_avg(d["gpu_temp"]))
        ram = None
        if d["ram_used"] and d["ram_total"]:
            ram = round(sum(d["ram_used"]) / len(d["ram_used"]) / d["ram_total"] * 100, 1)
        s["ram"].append(ram)
        disk = None
        if d["disk_used"]:
            disk = round(sum(d["disk_used"]) / len(d["disk_used"]) / gib, 1)
        s["disk_gb"].append(disk)
        s["net_rx"].append(_avg(d["net_rx"]))
        s["net_tx"].append(_avg(d["net_tx"]))
        s["load1"].append(_avg(d["load1"]))
        for i in range(ncores):
            vals = [arr[i] for arr in d["cores"] if i < len(arr) and arr[i] is not None]
            s["cores"][i].append(round(sum(vals) / len(vals), 1) if vals else None)

    return {
        "server": server,
        "period": period,
        "labels": labels,
        "series": s,
        "disk_total_gb": round(latest_disk_total / gib, 1) if latest_disk_total else None,
    }


def _avg(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return round(sum(float(v) for v in vals) / len(vals), 1)


def get_raw_state(server):
    with _lock, _conn() as c:
        r = c.execute(
            "SELECT ts, cpu_total, cpu_idle, cpu_cores, net_rx, net_tx "
            "FROM metrics_raw_state WHERE server=?", (server,)).fetchone()
        return dict(r) if r else None


def set_raw_state(server, ts, cpu_total, cpu_idle, net_rx, net_tx, cpu_cores=None):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO metrics_raw_state "
            "(server, ts, cpu_total, cpu_idle, net_rx, net_tx, cpu_cores) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(server) DO UPDATE SET "
            "ts=excluded.ts, cpu_total=excluded.cpu_total, "
            "cpu_idle=excluded.cpu_idle, net_rx=excluded.net_rx, "
            "net_tx=excluded.net_tx, cpu_cores=excluded.cpu_cores",
            (server, int(ts), int(cpu_total), int(cpu_idle),
             int(net_rx), int(net_tx),
             json.dumps(cpu_cores) if cpu_cores is not None else None))
        c.commit()


def delete_old(days=365):
    cutoff = int(time.time()) - days * 86400
    with _lock, _conn() as c:
        c.execute("DELETE FROM server_metrics WHERE ts < ?", (cutoff,))
        c.commit()


init_db()
