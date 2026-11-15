#!/usr/bin/env python3
"""Pull-сбор метрик с узлов по SSH и запись в metrics.db.

Запускается на центральном сервере (flux) по таймеру каждую минуту.
Список узлов — metrics_nodes.json (в корне проекта):

    [
      {"server": "webnara", "ssh": "webnara"},
      {"server": "office",  "ssh": "office"}
    ]

Поле "ssh" — алиас из ~/.ssh/config (или user@host). Скрипт remote_collect.py
передаётся узлу через stdin и печатает сырые счётчики; дельту CPU (общую и
по ядрам) и сети считаем здесь по сохранённому состоянию.
"""
import json
import subprocess
import sys
from pathlib import Path

import config
import server_metrics

NODES_FILE = Path(config.BASE_DIR) / "metrics_nodes.json"
COLLECT_SCRIPT = Path(config.BASE_DIR) / "remote_collect.py"


def load_nodes():
    try:
        data = json.loads(NODES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"nodes config error: {e}", file=sys.stderr)
        return []
    if isinstance(data, dict):
        data = data.get("nodes", [])
    return [n for n in data
            if isinstance(n, dict) and (n.get("ssh") or n.get("local"))]


def collect_local():
    proc = subprocess.run(
        [sys.executable, str(COLLECT_SCRIPT)],
        capture_output=True, timeout=40)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(err[:300] or f"exit {proc.returncode}")
    return json.loads(proc.stdout.decode("utf-8", "replace"))


def collect_remote(alias, python="python3"):
    with open(COLLECT_SCRIPT, "rb") as f:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             alias, python, "-"],
            stdin=f, capture_output=True, timeout=40)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(err[:300] or f"ssh exit {proc.returncode}")
    return json.loads(proc.stdout.decode("utf-8", "replace"))


def _prev_cores(prev):
    raw = prev.get("cpu_cores") if prev else None
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else None
    except (ValueError, TypeError):
        return None


def _core_pcts(cur, prev):
    if not prev:
        return None
    out = []
    for i, c in enumerate(cur):
        if i >= len(prev):
            break
        try:
            dt = int(c[0]) - int(prev[i][0])
            di = int(c[1]) - int(prev[i][1])
        except (IndexError, TypeError, ValueError):
            out.append(None)
            continue
        out.append(round(100.0 * (dt - di) / dt, 1) if dt > 0 else None)
    return out or None


def build_sample(server, raw):
    import time
    now = float(raw.get("ts") or 0) or time.time()
    prev = server_metrics.get_raw_state(server)

    cpu = None
    rx_rate = tx_rate = None
    if prev:
        dt = max(now - float(prev.get("ts") or now), 0.001)
        if dt < 15:
            # слишком близко к прошлому замеру — не пишем (иначе иглы в сети)
            return None
        dtotal = int(raw["cpu_total"]) - int(prev.get("cpu_total") or 0)
        didle = int(raw["cpu_idle"]) - int(prev.get("cpu_idle") or 0)
        if dtotal > 0:
            cpu = round(100.0 * (dtotal - didle) / dtotal, 1)
        rx_rate = max(0, int((int(raw["net_rx"]) - int(prev.get("net_rx") or 0)) / dt))
        tx_rate = max(0, int((int(raw["net_tx"]) - int(prev.get("net_tx") or 0)) / dt))

    cores = _core_pcts(raw.get("cpu_cores") or [], _prev_cores(prev))

    server_metrics.set_raw_state(
        server, now, raw["cpu_total"], raw["cpu_idle"],
        raw["net_rx"], raw["net_tx"], raw.get("cpu_cores"))

    if raw.get("info"):
        server_metrics.set_server_info(server, raw["info"])

    return {
        "server": server,
        "ts": now,
        "cpu": cpu,
        "cpu_temp": raw.get("cpu_temp"),
        "gpu_temp": raw.get("gpu_temp"),
        "cpu_cores": cores,
        "ram_used": raw.get("ram_used"),
        "ram_total": raw.get("ram_total"),
        "disk": raw.get("disk") or {},
        "net_rx": rx_rate,
        "net_tx": tx_rate,
        "load1": raw.get("load1"),
        "uptime": raw.get("uptime"),
    }


def main():
    nodes = load_nodes()
    if not nodes:
        print("no nodes configured")
        return 0
    failed = 0
    for node in nodes:
        server = node.get("server") or node.get("ssh") or "local"
        try:
            if node.get("local"):
                raw = collect_local()
            else:
                raw = collect_remote(node["ssh"], node.get("python", "python3"))
            ext_ip = node.get("external_ip")
            if ext_ip:
                info = raw.get("info") or {}
                info["external_ip"] = ext_ip
                raw["info"] = info
                raw["external_ip"] = ext_ip
            sample = build_sample(server, raw)
            if sample is None:
                print(f"{server}: skip (too soon)")
                continue
            server_metrics.add_sample(sample)
            print(f"{server}: cpu={sample['cpu']} "
                  f"cores={len(sample['cpu_cores'] or [])} "
                  f"net={sample['net_rx']}/{sample['net_tx']} ok")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"{server}: FAIL {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
