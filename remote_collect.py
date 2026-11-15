#!/usr/bin/env python3
"""Печатает сырые счётчики и конфиг сервера в JSON (для pull-сбора по SSH).

Запускается на удалённом узле: `python3 -` (скрипт приходит по stdin).
Возвращает накопительные счётчики CPU (общие и по ядрам) и сети — центральный
сервер сам считает дельту. Плюс конфиг железа (CPU/RAM/диски), best-effort.
"""
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request


def _read_cpu():
    total_all = idle_all = 0
    cores = []
    with open("/proc/stat") as f:
        for line in f:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            name = parts[0]
            vals = [int(x) for x in parts[1:9]]
            total = sum(vals)
            idle = vals[3] + vals[4]
            if name == "cpu":
                total_all, idle_all = total, idle
            else:
                cores.append([total, idle])
    return total_all, idle_all, cores


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


def _read_cpuinfo():
    model = None
    sockets = set()
    threads = 0
    cores_per_socket = 0
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                k, _, v = line.partition(":")
                k = k.strip().lower()
                v = v.strip()
                if k == "model name" and not model:
                    model = v
                elif k == "physical id":
                    sockets.add(v)
                elif k == "processor":
                    threads += 1
                elif k == "cpu cores":
                    try:
                        cores_per_socket = max(cores_per_socket, int(v))
                    except ValueError:
                        pass
    except OSError:
        pass
    nsock = len(sockets) if sockets else 1
    cores = cores_per_socket * nsock if cores_per_socket else threads
    return {
        "model": model,
        "sockets": nsock,
        "cores": cores,
        "threads": threads or None,
    }


def _run(cmd):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=8)
        if p.returncode == 0:
            return p.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _read_ram_info(total):
    """Частота/слоты — через dmidecode (нужен root; иначе только объём)."""
    info = {"total": total, "type": None, "speed": None,
            "slots_used": None, "slots_total": None}
    out = _run(["dmidecode", "-t", "17"])
    if not out:
        out = _run(["sudo", "-n", "dmidecode", "-t", "17"])
    if not out:
        return info
    slots_total = 0
    used = 0
    speeds = set()
    types = set()
    for block in out.split("Memory Device"):
        slots_total += 1
        if "No Module Installed" in block:
            continue
        m = re.search(r"^\s*Size:\s*(\d+)\s*(MB|GB|TB)", block, re.MULTILINE)
        if not m:
            continue
        used += 1
        sp = re.search(r"^\s*Speed:\s*([\w\s/]+)$", block, re.MULTILINE)
        if sp:
            val = sp.group(1).strip()
            if val and "Unknown" not in val:
                speeds.add(val)
        tp = re.search(r"^\s*Type:\s*(\w+)$", block, re.MULTILINE)
        if tp:
            types.add(tp.group(1))
    info["slots_total"] = slots_total or None
    info["slots_used"] = used or None
    if speeds:
        info["speed"] = ", ".join(sorted(speeds))
    if types:
        info["type"] = ", ".join(sorted(types))
    return info


def _read_md_levels():
    levels = {}
    try:
        with open("/proc/mdstat", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"(md\d+)\s*:\s*active\s+(raid\d+)", line)
                if m:
                    levels["/dev/" + m.group(1)] = m.group(2).upper()
    except OSError:
        pass
    return levels


def _lsblk_tree():
    """Вернуть map: mountpoint -> инфо о диске (с предками)."""
    out = _run(["lsblk", "-J", "-b", "-o",
                "NAME,SIZE,TYPE,ROTA,MODEL,FSTYPE,MOUNTPOINT"])
    if not out:
        return {}
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    by_mount = {}

    def walk(node, ancestors):
        chain = ancestors + [node]
        mp = node.get("mountpoint")
        if mp:
            by_mount[mp] = _describe(chain)
        for ch in node.get("children") or []:
            walk(ch, chain)

    for dev in data.get("blockdevices") or []:
        walk(dev, [])
    return by_mount


def _describe(chain):
    types = [str(n.get("type") or "").lower() for n in chain]
    raid = None
    for t in types:
        if t.startswith("raid") or t in ("linear", "multipath"):
            raid = t.upper()
    has_lvm = "lvm" in types
    # физический диск — первый узел типа disk
    phys = next((n for n in chain if str(n.get("type")).lower() == "disk"), None)
    rota = None
    model = None
    if phys:
        r = phys.get("rota")
        if r is not None:
            rota = int(r)
        model = phys.get("model")
    array = None
    if raid and has_lvm:
        array = f"{raid} + LVM"
    elif raid:
        array = raid
    elif has_lvm:
        array = "LVM"
    return {"rota": rota, "model": model, "array": array}


def _detect_disks():
    real_fs = {"ext4", "ext3", "ext2", "xfs", "btrfs", "zfs", "f2fs"}
    lsblk = _lsblk_tree()
    md_levels = _read_md_levels()
    disk = {}
    disks = []
    seen_dev = set()
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as f:
            mounts = [ln.split() for ln in f if ln.strip()]
    except OSError:
        mounts = []
    for parts in mounts:
        if len(parts) < 3:
            continue
        dev, mount, fstype = parts[0], parts[1], parts[2]
        if fstype not in real_fs:
            continue
        if dev in seen_dev:
            # bind-mount того же диска — не дублируем
            continue
        seen_dev.add(dev)
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total < 5 * 1024 ** 3:
            continue
        free = st.f_bavail * st.f_frsize
        used = total - free
        disk[mount] = {"used": used, "total": total}

        meta = lsblk.get(mount, {})
        array = meta.get("array")
        if not array:
            base = "/dev/md" + "".join(ch for ch in os.path.basename(dev) if ch.isdigit()) \
                if dev.startswith("/dev/md") else None
            if base and base in md_levels:
                array = md_levels[base]
        disks.append({
            "mount": mount,
            "device": dev,
            "fstype": fstype,
            "size": total,
            "used": used,
            "rota": meta.get("rota"),
            "model": meta.get("model"),
            "array": array,
        })
    return disk, disks


def _uptime():
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError):
        return None


def _read_ips():
    ips = []
    out = _run(["hostname", "-I"])
    if out:
        ips = [t for t in out.split() if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", t)]
    if not ips:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127."):
                    ips.append(ip)
        except OSError:
            pass
    seen = set()
    result = []
    for ip in ips:
        if not _useful_ip(ip):
            continue
        if ip not in seen:
            seen.add(ip)
            result.append(ip)
    return result


def _useful_ip(ip):
    """Отсечь loopback, docker-мосты 172.16/12 и link-local."""
    try:
        octets = [int(x) for x in ip.split(".")]
    except ValueError:
        return False
    if octets[0] == 127 or ip == "0.0.0.0":
        return False
    if octets[0] == 169 and octets[1] == 254:
        return False
    if octets[0] == 172 and 16 <= octets[1] <= 31:
        return False
    return True


def _read_cpu_temp():
    """Температура CPU, °C (best-effort): coretemp/k10temp, иначе thermal_zone."""
    pkg = None
    any_t = None
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            name = open(os.path.join(h, "name")).read().strip().lower()
        except OSError:
            continue
        if name not in ("coretemp", "k10temp", "zenpower", "cpu_thermal",
                        "acpitz", "it87", "nct6775"):
            continue
        for f in glob.glob(os.path.join(h, "temp*_input")):
            try:
                v = int(open(f).read().strip()) / 1000.0
            except (OSError, ValueError):
                continue
            if not (0 < v < 150):
                continue
            lbl = ""
            lab = f.replace("_input", "_label")
            if os.path.exists(lab):
                try:
                    lbl = open(lab).read().strip().lower()
                except OSError:
                    lbl = ""
            if "package" in lbl or "tctl" in lbl or "tdie" in lbl:
                pkg = v if pkg is None else max(pkg, v)
            any_t = v if any_t is None else max(any_t, v)
    if pkg is not None:
        return round(pkg, 1)
    if any_t is not None:
        return round(any_t, 1)
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            t = open(os.path.join(z, "type")).read().strip().lower()
            v = int(open(os.path.join(z, "temp")).read().strip()) / 1000.0
        except (OSError, ValueError):
            continue
        if ("cpu" in t or "pkg" in t or "x86" in t or "acpitz" in t or "soc" in t) \
                and 0 < v < 150:
            any_t = v if any_t is None else max(any_t, v)
    return round(any_t, 1) if any_t is not None else None


def _read_gpu():
    """(temp_c, util%, vram_used_mb, vram_total_mb) или None (нет GPU/nvidia-smi)."""
    out = _run(["nvidia-smi",
                "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits"])
    if not out:
        return None
    try:
        line = out.strip().splitlines()[0]
        temp, util, vram_used, vram_total = [int(x) for x in line.split(",")]
        return temp, util, vram_used, vram_total
    except (ValueError, IndexError):
        return None
_EXTIP_CACHE = "/tmp/.ai_enix_extip"
_IP_RE = re.compile(r"(\d{1,3}\.){3}\d{1,3}")


def _read_external_ip():
    """Публичный IP узла. Кэш на 1 час, чтобы не дёргать сервис каждый раз."""
    try:
        if os.path.exists(_EXTIP_CACHE):
            age = time.time() - os.path.getmtime(_EXTIP_CACHE)
            if age < 3600:
                with open(_EXTIP_CACHE) as f:
                    ip = f.read().strip()
                if _IP_RE.fullmatch(ip):
                    return ip
    except OSError:
        pass
    for url in ("https://api.ipify.org", "https://icanhazip.com",
                "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                ip = r.read(64).decode("utf-8", "replace").strip()
            if _IP_RE.fullmatch(ip):
                try:
                    with open(_EXTIP_CACHE, "w") as f:
                        f.write(ip)
                except OSError:
                    pass
                return ip
        except Exception:  # noqa: BLE001
            continue
    return None


def main():
    cpu_total, cpu_idle, cpu_cores = _read_cpu()
    rx, tx = _read_net()
    used, total = _read_mem()
    disk, disks = _detect_disks()
    try:
        load1 = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        load1 = None
    cpuinfo = _read_cpuinfo()
    raminfo = _read_ram_info(total)
    ips = _read_ips()
    ext_ip = _read_external_ip()
    cpu_temp = _read_cpu_temp()
    gpu = _read_gpu()
    print(json.dumps({
        "hostname": socket.gethostname(),
        "ips": ips,
        "external_ip": ext_ip,
        "ts": time.time(),
        "cpu_total": cpu_total,
        "cpu_idle": cpu_idle,
        "cpu_cores": cpu_cores,
        "cpu_temp": cpu_temp,
        "gpu_temp": gpu[0] if gpu else None,
        "net_rx": rx,
        "net_tx": tx,
        "ram_used": used,
        "ram_total": total,
        "disk": disk,
        "disks": disks,
        "load1": load1,
        "uptime": _uptime(),
        "info": {
            "hostname": socket.gethostname(),
            "ips": ips,
            "external_ip": ext_ip,
            "cpu": cpuinfo,
            "ram": raminfo,
            "disks": disks,
        },
    }))


if __name__ == "__main__":
    main()
