"""Статистика сайтов (люди vs боты) из логов nginx на webnara.

Собирается удалённым парсером sites_stats_remote.py по SSH и кэшируется.
"""
import json
import subprocess
import threading
import time

_SSH = "webnara"
_REMOTE = "/home/kirill/sites_stats_remote.py"
_TTL = 300
_cache = {}
_lock = threading.Lock()


def get_stats(period="day"):
    if period not in ("hour", "day", "yesterday", "week"):
        period = "day"
    now = time.time()
    with _lock:
        cached = _cache.get(period)
        if cached and cached[0] > now:
            return cached[1]
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", _SSH,
         "python3", _REMOTE, period],
        capture_output=True, timeout=90)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(err[:300] or f"ssh exit {proc.returncode}")
    data = json.loads(proc.stdout.decode("utf-8", "replace"))
    with _lock:
        _cache[period] = (now + _TTL, data)
    return data
