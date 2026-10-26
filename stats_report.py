"""Отчёт по истории нагрузки (stats_history.jsonl).

Выводит среднее и максимум по CPU/GPU/RAM/VRAM/swap за период,
а также распределение по часам.
"""
import json
import sys
import time
from pathlib import Path

HISTORY_FILE = Path("/home/kirill/flux/stats_history.jsonl")


def load():
    rows = []
    if not HISTORY_FILE.exists():
        return rows
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def stats(rows, key):
    vals = [r[key] for r in rows if key in r and r[key] is not None]
    if not vals:
        return None
    return sum(vals) / len(vals), max(vals), min(vals)


def fmt_gb(mb):
    return f"{mb / 1024:.1f}"


def main():
    rows = load()
    if not rows:
        print("Нет данных в stats_history.jsonl")
        return

    first = rows[0]["ts"]
    last = rows[-1]["ts"]
    span = last - first
    hours = span / 3600
    interval = span / max(len(rows) - 1, 1)

    print(f"Сэмплов: {len(rows)}")
    print(f"Период:  {time.strftime('%Y-%m-%d %H:%M', time.localtime(first))} — "
          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(last))} "
          f"({hours:.1f} ч, шаг ~{interval:.0f} с)")
    print()

    fields = [
        ("cpu_percent", "CPU %", "%.1f%%", "%.1f%%"),
        ("cpu_temp", "CPU темп, °C", "%.1f", "%.1f"),
        ("gpu_util", "GPU util %", "%.1f%%", "%.1f%%"),
        ("gpu_temp", "GPU темп, °C", "%.1f", "%.1f"),
        ("vram_used_mb", "VRAM, ГБ", None, None),
        ("ram_used_mb", "RAM, ГБ", None, None),
        ("swap_used_mb", "SWAP, ГБ", None, None),
    ]

    print(f"{'Метрика':<16}{'Среднее':>12}{'Макс':>12}{'Мин':>12}")
    print("-" * 52)
    for key, label, avgf, _ in fields:
        st = stats(rows, key)
        if st is None:
            print(f"{label:<16}{'—':>12}{'—':>12}{'—':>12}")
            continue
        avg, mx, mn = st
        if "mb" in key:
            print(f"{label:<16}{fmt_gb(avg):>10} ГБ{fmt_gb(mx):>10} ГБ{fmt_gb(mn):>10} ГБ")
        else:
            print(f"{label:<16}{avg:>11.1f}{mx:>11.1f}{mn:>11.1f}")

    # RAM/VRAM в % от общего
    total_mb = None
    for r in rows:
        if "ram_total_mb" in r:
            total_mb = r["ram_total_mb"]
            break
    if total_mb:
        st = stats(rows, "ram_used_mb")
        if st:
            print(f"\nRAM от общего ({fmt_gb(total_mb)} ГБ): "
                  f"средн {st[0]/total_mb*100:.0f}%, макс {st[1]/total_mb*100:.0f}%")
    vram_total = None
    for r in rows:
        if "vram_total_mb" in r:
            vram_total = r["vram_total_mb"]
            break
    if vram_total:
        st = stats(rows, "vram_used_mb")
        if st:
            print(f"VRAM от общего ({fmt_gb(vram_total)} ГБ): "
                  f"средн {st[0]/vram_total*100:.0f}%, макс {st[1]/vram_total*100:.0f}%")

    # Диски
    for key, label in [("disk_root_gb", "Диск /"), ("disk_data_gb", "Диск /data")]:
        st = stats(rows, key)
        if st:
            print(f"{label}: средн {st[0]:.0f} ГБ, макс {st[1]:.0f} ГБ")

    # Почасовое среднее CPU/GPU (пики по времени суток)
    print("\nПочасовое (средн CPU% / средн GPU% / макс VRAM ГБ):")
    by_hour = {}
    for r in rows:
        h = time.localtime(r["ts"]).tm_hour
        b = by_hour.setdefault(h, {"cpu": [], "gpu": [], "vram": []})
        if "cpu_percent" in r:
            b["cpu"].append(r["cpu_percent"])
        if "gpu_util" in r:
            b["gpu"].append(r["gpu_util"])
        if "vram_used_mb" in r:
            b["vram"].append(r["vram_used_mb"])
    for h in sorted(by_hour):
        b = by_hour[h]
        cpu = sum(b["cpu"]) / len(b["cpu"]) if b["cpu"] else 0
        gpu = sum(b["gpu"]) / len(b["gpu"]) if b["gpu"] else 0
        vram = max(b["vram"]) / 1024 if b["vram"] else 0
        print(f"  {h:02d}:00  CPU {cpu:5.1f}%  GPU {gpu:5.1f}%  VRAM макс {vram:5.1f} ГБ")


if __name__ == "__main__":
    main()
