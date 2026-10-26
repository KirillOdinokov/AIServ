"""Сборщик статистики нагрузки сервера.

Каждые INTERVAL секунд снимает метрики (CPU/GPU/RAM/VRAM/swap/диски)
и дописывает одну JSON-строку в HISTORY_FILE. Запускается как systemd-сервис.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

import psutil

HISTORY_FILE = Path("/home/kirill/flux/stats_history.jsonl")
INTERVAL = 5  # секунд


def read_cpu_temp():
    """Температура CPU (°C) или None."""
    try:
        base = Path("/sys/class/thermal")
        for zone in base.iterdir():
            t = (zone / "type").read_text().strip()
            if t in ("x86_pkg_temp", "cpu-thermal", "Tctl", "k10temp", "coretemp"):
                raw = (zone / "temp").read_text().strip()
                val = int(raw)
                return round(val / 1000, 1) if val > 1000 else val
        for zone in base.iterdir():
            if (zone / "temp").exists():
                raw = (zone / "temp").read_text().strip()
                val = int(raw)
                return round(val / 1000, 1) if val > 1000 else val
    except Exception:
        pass
    return None


def read_gpu():
    """(temp, util%, vram_used_mb, vram_total_mb) или None."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            timeout=5,
        ).decode("utf-8", errors="replace").strip()
        line = out.splitlines()[0]
        temp, util, vram_used, vram_total = [int(x) for x in line.split(",")]
        return temp, util, vram_used, vram_total
    except Exception:
        return None


def sample():
    s = {"ts": int(time.time())}
    s["cpu_percent"] = psutil.cpu_percent(interval=None)
    s["cpu_temp"] = read_cpu_temp()

    gpu = read_gpu()
    if gpu:
        s["gpu_temp"], s["gpu_util"], s["vram_used_mb"], s["vram_total_mb"] = gpu

    mem = psutil.virtual_memory()
    s["ram_used_mb"] = round(mem.used / 1024 / 1024)
    s["ram_total_mb"] = round(mem.total / 1024 / 1024)

    swap = psutil.swap_memory()
    s["swap_used_mb"] = round(swap.used / 1024 / 1024)

    disk = shutil.disk_usage("/")
    s["disk_root_gb"] = round(disk.used / 1024 ** 3, 1)
    if Path("/data").exists():
        try:
            d = shutil.disk_usage("/data")
            s["disk_data_gb"] = round(d.used / 1024 ** 3, 1)
        except OSError:
            pass
    return s


def main():
    psutil.cpu_percent(interval=None)  # прогрев первого замера
    while True:
        try:
            s = sample()
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
                f.flush()
        except Exception as e:
            print(f"[stats_monitor] error: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
