#!/usr/bin/env python3
"""Агент сбора метрик сервера. Только stdlib — кладётся на любой Linux-сервер.

Раз в 5 минут (systemd timer) собирает CPU/RAM/диски/сеть и POST'ит на
центральный сервер AI ENIX.

Переменные окружения:
  METRICS_URL      — куда слать (по умолчанию https://ai.enix.ru:10443/api/metrics/ingest)
  METRICS_KEY      — общий секрет (заголовок X-Metrics-Key)
  METRICS_SERVER   — метка сервера (по умолчанию hostname)
  METRICS_MOUNTS   — список точек через запятую; по умолчанию авто-определение
  METRICS_STATE    — путь к файлу состояния (по умолчанию ~/.metrics_agent_state.json)

Первый запуск не имеет дельты для CPU/сети — шлёт null, дальше считает средние
за интервал между запусками.
"""
import json
import os
import socket
import sys
import time
import urllib.request

STATE = os.environ.get("METRICS_STATE") or os.path.join(
    os.path.expanduser("~"), ".metrics_agent_state.json")
URL = os.environ.get("METRICS_URL", "https://ai.enix.ru:10443/api/metrics/ingest")
KEY = os.environ.get("METRICS_KEY", "")
LABEL = os.environ.get("METRICS_SERVER") or socket.gethostname()

_MOUNT_OVERRIDE = os.environ.get("METRICS_MOUNTS")


def _read_cpu():
    with open("/proc/stat") as f:
        parts = f.readline().split()
    vals = [int(x) for x in parts[1:9]]  # user nice system idle iowait irq softirq steal
    total = sum(vals)
    idle = vals[3] + vals[4]
    return total, idle


def _read_net():
    rx = tx = 0
    with open("/proc/net/dev") as f:
        for line in f:
            if ":" not in line:
                continue
            iface, data = line.split(":", 1)
            if iface.strip() == "lo":
                continue
            cols = data.split()
            if len(cols) < 9:
                continue
            rx += int(cols[0])
            tx += int(cols[8])
    return rx, tx


def _read_mem():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, _, v = line.partition(":")
            try:
                info[k.strip()] = int(v.split()[0]) * 1024
            except (IndexError, ValueError):
                pass
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    return total - avail, total


def _detect_mounts():
    if _MOUNT_OVERRIDE:
        return [m.strip() for m in _MOUNT_OVERRIDE.split(",") if m.strip()]
    real_fs = {"ext4", "ext3", "ext2", "xfs", "btrfs", "zfs", "f2fs"}
    mounts = []
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount, fstype = parts[1], parts[2]
                if fstype not in real_fs:
                    continue
                try:
                    st = os.statvfs(mount)
                except OSError:
                    continue
                total = st.f_blocks * st.f_frsize
                if total >= 5 * 1024 ** 3:  # только разделы от 5 ГБ
                    mounts.append(mount)
    except OSError:
        pass
    return mounts or ["/"]


def _read_disks(mounts):
    out = {}
    for m in mounts:
        try:
            st = os.statvfs(m)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            out[m] = {"used": total - free, "total": total}
        except OSError:
            continue
    return out


def _load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_state(st):
    try:
        d = os.path.dirname(STATE)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, STATE)
    except OSError as e:
        print(f"state save failed: {e}", file=sys.stderr)


def _uptime():
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError):
        return None


def collect():
    now = time.time()
    prev = _load_state()
    cpu_total, cpu_idle = _read_cpu()
    net_rx, net_tx = _read_net()

    cpu = None
    rx_rate = tx_rate = None
    if prev:
        dt = max(now - float(prev.get("ts", now)), 0.001)
        dtotal = cpu_total - prev.get("cpu_total", cpu_total)
        didle = cpu_idle - prev.get("cpu_idle", cpu_idle)
        if dtotal > 0:
            cpu = round(100.0 * (dtotal - didle) / dtotal, 1)
        rx_rate = max(0, int((net_rx - prev.get("net_rx", net_rx)) / dt))
        tx_rate = max(0, int((net_tx - prev.get("net_tx", net_tx)) / dt))

    ram_used, ram_total = _read_mem()
    mounts = _detect_mounts()
    try:
        load1 = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        load1 = None

    sample = {
        "server": LABEL,
        "ts": now,
        "cpu": cpu,
        "ram_used": ram_used,
        "ram_total": ram_total,
        "disk": _read_disks(mounts),
        "net_rx": rx_rate,
        "net_tx": tx_rate,
        "load1": load1,
        "uptime": _uptime(),
    }
    _save_state({
        "ts": now,
        "cpu_total": cpu_total,
        "cpu_idle": cpu_idle,
        "net_rx": net_rx,
        "net_tx": net_tx,
    })
    return sample


def send(sample):
    if not KEY:
        print("METRICS_KEY is empty", file=sys.stderr)
        return False
    data = json.dumps(sample).encode("utf-8")
    req = urllib.request.Request(
        URL, data=data, method="POST",
        headers={"Content-Type": "application/json", "X-Metrics-Key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"send failed: {e}", file=sys.stderr)
        return False


def main():
    sample = collect()
    ok = send(sample)
    print(f"{sample['server']}: cpu={sample['cpu']} ram={sample['ram_used']}/"
          f"{sample['ram_total']} net={sample['net_rx']}/{sample['net_tx']} "
          f"{'ok' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
