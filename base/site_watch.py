"""Временный мониторинг списка сайтов из заявки.

Проверка выполняется с сервера по запросу, результаты не сохраняются в БД.
Телефоны из исходного списка исключены.
"""
import asyncio
import ipaddress
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(6.0, connect=3.0, read=5.0, write=5.0, pool=5.0)
MAX_CONCURRENCY = 60

GROUPS = ("Основные", "Без телефона", "Гидрошпонки", "Мёртвые")

_GROUPS = {
    "Основные": [
        "www.enix.ru",
        "enix.ru.com",
        "www.geosintetika.moscow",
        "www.геосинтетика.москва",
        "izoru.ru",
        "vodolot.ru",
        "www.pologprom.ru",
        "www.soudal.enix.ru",
        "www.vilpe-croco.ru",
        "www.руфикс.рф",
        "www.изорок.москва",
        "www.steklotkan-t.ru",
        "www.tekhnoelast.ru",
        "www.gidroshponki.ru",
        "gidroshponki.ru",
        "www.зеленые-кровли.москва",
        "aquashov.ru",
        "www.aquashponka.ru",
        "aquashponka.ru",
        "www.rock-trade.ru",
        "www.wilaterm.ru",
        "www.фиксатор-арматуры.рф",
        "www.waterkeys.ru",
        "www.пвх-мембраны.рф",
        "www.steklotkani.ru",
        "роксы.рф",
        "www.hidroshponki.ru",
        "www.pvc-membrana.ru",
        "www.bentoprofil.ru",
        "www.plastfix.ru",
        "vilatherm.ru",
        "rock-sale.ru",
        "vilpe-roofing.ru",
        "fasten-trade.ru",
        "gerniit.ru",
        "mapei.moscow",
        "siksmes.ru",
        "semacos.ru",
        "rex.moscow",
        "fachmann.moscow",
        "huntsman-rus.ru",
        "emaco-sika.ru",
        "www.gernitprofil.ru",
        "fahmann.ru",
        "vodoo.ru",
        "авто-линолеум.рф",
        "ogneiz.ru",
        "waterstop-rx.ru",
        "www.gidrogarants.ru",
        "www.gidroshponki.spb.ru",
        "venti.spb.ru",
        "www.shponki-aquastop.ru",
        "www.stroishov.ru",
        "www.gidro-shponka.ru",
        "www.gernit-bentonit.ru",
        "www.exjoint.ru",
        "www.hidroshponka.ru",
        "decopran.ru",
        "www.waterbar.ru",
        "www.waterbars.ru",
        "www.gidro-shponki.ru",
        "www.besaflex.ru",
        "odinokova.ru",
        "alpinizm.pro",
        "office.enix.ru",
        "amalor.ru",
    ],
    "Без телефона": [
        "www.nitriflex.ru",
        "www.elastoflex.ru",
        "www.sikaplan.ru",
    ],
    "Гидрошпонки": [
        "arhangelsk.gidroshponki.ru",
        "astrahan.gidroshponki.ru",
        "barnaul.gidroshponki.ru",
        "belgorod.gidroshponki.ru",
        "bryansk.gidroshponki.ru",
        "chelny.gidroshponki.ru",
        "chita.gidroshponki.ru",
        "donetsk.gidroshponki.ru",
        "grozny.gidroshponki.ru",
        "habarovsk.gidroshponki.ru",
        "himki.gidroshponki.ru",
        "ivanovo.gidroshponki.ru",
        "izhevsk.gidroshponki.ru",
        "kaliningrad.gidroshponki.ru",
        "kaluga.gidroshponki.ru",
        "kirov.gidroshponki.ru",
        "kostroma.gidroshponki.ru",
        "kursk.gidroshponki.ru",
        "lipezk.gidroshponki.ru",
        "luberzy.gidroshponki.ru",
        "lugansk.gidroshponki.ru",
        "mahachkala.gidroshponki.ru",
        "mariupol.gidroshponki.ru",
        "penza.gidroshponki.ru",
        "rostov.gidroshponki.ru",
        "ryazan.gidroshponki.ru",
        "saratov.gidroshponki.ru",
        "sevastopol.gidroshponki.ru",
        "simferopol.gidroshponki.ru",
        "smolensk.gidroshponki.ru",
        "sochi.gidroshponki.ru",
        "stavropol.gidroshponki.ru",
        "tagil.gidroshponki.ru",
        "toliatti.gidroshponki.ru",
        "tula.gidroshponki.ru",
        "tumen.gidroshponki.ru",
        "tver.gidroshponki.ru",
        "ulan-ude.gidroshponki.ru",
        "vladikavkaz.gidroshponki.ru",
        "vladimir.gidroshponki.ru",
        "vladivostok.gidroshponki.ru",
        "volgograd.gidroshponki.ru",
        "voronezh.gidroshponki.ru",
        "yaroslavl.gidroshponki.ru",
    ],
    "Мёртвые": [
        "brit.moscow",
        "sika-pro.ru",
        "autolin.ru",
        "breet.ru",
        "tclp.ru",
        "tekrep.ru",
        "vilps.ru",
        "x-anker.ru",
        "mktep.ru",
        "termoanker.ru",
        "him-krep.ru",
        "scorpfast.ru",
        "ze-roof.ru",
        "www.gidrokontur.ru",
        "www.geoisol.ru",
        "брит.рф",
        "дефлекс.рф",
        "promtexizol.ru",
        "vodoluk.ru",
        "gernit.ru",
        "vilaterm.ru",
        "redstop.ru",
        "deformix.ru",
    ],
}

SITES = []
_seen = set()
for _group in GROUPS:
    for _host in _GROUPS[_group]:
        _host = _host.strip()
        if not _host or _host in _seen:
            continue
        _seen.add(_host)
        SITES.append({"host": _host, "group": _group})

SNAPSHOT_PATH = Path(__file__).with_name("site_ip_snapshot.json")
try:
    _SNAPSHOT_DATA = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
except Exception:
    _SNAPSHOT_DATA = {}
SNAPSHOT_CREATED_AT = _SNAPSHOT_DATA.get("created_at")
SNAPSHOT_HOSTS = _SNAPSHOT_DATA.get("hosts") or {}


def snapshot_ips(host):
    entry = SNAPSHOT_HOSTS.get(host) or {}
    return list(entry.get("ips") or [])


def _idna(host):
    try:
        return host.encode("idna").decode("ascii")
    except Exception:
        return host


async def _probe(client, site):
    host = site["host"]
    ascii_host = _idna(host)
    last_error = None
    for scheme in ("https", "http"):
        started = time.perf_counter()
        try:
            async with client.stream("GET", f"{scheme}://{ascii_host}/") as resp:
                try:
                    async for _chunk in resp.aiter_bytes(1024):
                        break
                except Exception:
                    pass
                ms = int((time.perf_counter() - started) * 1000)
                code = resp.status_code
                if code < 400:
                    state = "ok"
                elif code < 500:
                    state = "warn"
                else:
                    state = "fail"
                note = "HTTPS недоступен, сайт отвечает по HTTP" if scheme == "http" else None
                return {
                    **site,
                    "url": str(resp.url),
                    "scheme": scheme,
                    "status_code": code,
                    "state": state,
                    "ms": ms,
                    "error": None,
                    "note": note,
                }
        except Exception as exc:
            message = str(exc).strip()
            last_error = type(exc).__name__
            if message:
                last_error += ": " + message
    return {
        **site,
        "url": f"https://{ascii_host}/",
        "scheme": None,
        "status_code": None,
        "state": "fail",
        "ms": None,
        "error": (last_error or "unknown error")[:300],
        "note": None,
    }


async def _run_curl_async(cmd, timeout=10):
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return None, str(exc)[:300]
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None, "curl: timeout"
    if proc.returncode != 0:
        return None, (stderr.decode("utf-8", "replace") or "curl error").strip()[:300]
    return stdout.decode("utf-8", "replace"), None


async def _curl_provider_async(ip, host, version, scheme):
    port = 443 if scheme == "https" else 80
    resolve = f"{host}:{port}:[{ip}]" if version == 6 else f"{host}:{port}:{ip}"
    cmd = [
        "curl", "-sS", "-k", "--max-time", "10", "--connect-timeout", "5",
        "--resolve", resolve,
        "-o", "/dev/null",
        "-w", "%{http_code}\t%{time_total}\t%{remote_ip}",
        f"{scheme}://{host}/",
    ]
    stdout, error = await _run_curl_async(cmd, timeout=14)
    if error:
        return None, error
    parts = (stdout or "").strip().split("\t")
    if len(parts) < 3:
        return None, "curl: unexpected output"
    try:
        code = int(parts[0])
        ms = int(float(parts[1]) * 1000)
    except ValueError:
        return None, "curl: unexpected output"
    if code <= 0:
        return None, "нет ответа"
    return {"status_code": code, "ms": ms, "remote_ip": parts[2]}, None


async def _curl_current_async(host, scheme):
    cmd = [
        "curl", "-4", "-sS", "-k", "-L", "--max-redirs", "5",
        "--doh-url", "https://1.1.1.1/dns-query",
        "--max-time", "12", "--connect-timeout", "5",
        "-o", "/dev/null",
        "-w", "%{http_code}\t%{time_total}\t%{remote_ip}\t%{url_effective}",
        f"{scheme}://{host}/",
    ]
    stdout, error = await _run_curl_async(cmd, timeout=16)
    if error:
        return None, error
    parts = (stdout or "").strip().split("\t")
    if len(parts) < 4:
        return None, "curl: unexpected output"
    try:
        code = int(parts[0])
        ms = int(float(parts[1]) * 1000)
    except ValueError:
        return None, "curl: unexpected output"
    if code <= 0:
        return None, "нет ответа"
    return {
        "status_code": code,
        "ms": ms,
        "remote_ip": parts[2],
        "url": parts[3],
    }, None


async def _check_current(host):
    ascii_host = _idna(host)
    last_error = None
    for scheme in ("https", "http"):
        result, error = await _curl_current_async(ascii_host, scheme)
        if result:
            code = result["status_code"]
            state = "ok" if code < 400 else ("warn" if code < 500 else "fail")
            return {
                "host": host,
                "scheme": scheme,
                "status_code": code,
                "ms": result["ms"],
                "remote_ip": result["remote_ip"],
                "url": result["url"],
                "state": state,
                "error": None,
                "note": "HTTPS недоступен, ответ получен по HTTP" if scheme == "http" else None,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        last_error = error
    return {
        "host": host,
        "scheme": None,
        "status_code": None,
        "ms": None,
        "remote_ip": None,
        "url": f"https://{ascii_host}/",
        "state": "fail",
        "error": last_error or "нет ответа",
        "note": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def check_all():
    started = time.perf_counter()
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def run(site):
        async with sem:
            old_ips = snapshot_ips(site["host"])
            if old_ips:
                old_task = check_provider(old_ips[0], site["host"])
            else:
                async def no_old():
                    return {
                        "ip": None,
                        "host": site["host"],
                        "scheme": None,
                        "status_code": None,
                        "ms": None,
                        "remote_ip": None,
                        "state": "unknown",
                        "error": "нет IP в снимке",
                        "note": None,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    }
                old_task = no_old()
            old_result, current = await asyncio.gather(old_task, _check_current(site["host"]))
            return {
                **site,
                "old_ips": old_ips,
                "old_ip": old_ips[0] if old_ips else None,
                "old": old_result,
                "current": current,
                "state": current["state"],
                "status_code": current["status_code"],
                "ms": current["ms"],
                "url": current["url"],
                "current_ip": current["remote_ip"],
                "error": current["error"],
                "note": current["note"],
            }

    results = await asyncio.gather(*(run(site) for site in SITES))

    ok = sum(1 for r in results if r["state"] == "ok")
    warn = sum(1 for r in results if r["state"] == "warn")
    fail = sum(1 for r in results if r["state"] == "fail")
    old_ok = sum(1 for r in results if r["old"]["state"] == "ok")
    old_warn = sum(1 for r in results if r["old"]["state"] == "warn")
    old_fail = sum(1 for r in results if r["old"]["state"] == "fail")
    old_unknown = sum(1 for r in results if r["old"]["state"] == "unknown")
    times = [r["ms"] for r in results if r["ms"] is not None]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_created_at": SNAPSHOT_CREATED_AT,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "summary": {
            "total": len(results),
            "ok": ok,
            "warn": warn,
            "fail": fail,
            "avg_ms": int(sum(times) / len(times)) if times else None,
            "old_ok": old_ok,
            "old_warn": old_warn,
            "old_fail": old_fail,
            "old_unknown": old_unknown,
        },
        "results": results,
    }


def _normalize_provider_target(ip_raw, host_raw):
    host = (host_raw or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("Укажите домен")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except Exception:
        raise ValueError("Некорректный домен")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", ascii_host):
        raise ValueError("Некорректный домен")
    ip = (ip_raw or "").strip()
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError("Некорректный IP")
    return ascii_host, str(addr), addr.version


def _curl_provider(ip, host, version, scheme):
    port = 443 if scheme == "https" else 80
    resolve = f"{host}:{port}:[{ip}]" if version == 6 else f"{host}:{port}:{ip}"
    cmd = [
        "curl", "-sS", "-k", "--max-time", "7", "--connect-timeout", "3",
        "--resolve", resolve,
        "-o", "/dev/null",
        "-w", "%{http_code}\t%{time_total}\t%{remote_ip}",
        f"{scheme}://{host}/",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None, "curl: timeout"
    if proc.returncode != 0:
        return None, (proc.stderr or "curl error").strip()[:300]
    parts = (proc.stdout or "").strip().split("\t")
    if len(parts) < 3:
        return None, "curl: unexpected output"
    try:
        code = int(parts[0])
        ms = int(float(parts[1]) * 1000)
    except ValueError:
        return None, "curl: unexpected output"
    if code <= 0:
        return None, (proc.stderr or "нет ответа").strip()[:300]
    return {"status_code": code, "ms": ms, "remote_ip": parts[2]}, None


def _curl_current(host, scheme):
    cmd = [
        "curl", "-sS", "-k", "-L", "--max-redirs", "5",
        "--max-time", "7", "--connect-timeout", "3",
        "-o", "/dev/null",
        "-w", "%{http_code}\t%{time_total}\t%{remote_ip}\t%{url_effective}",
        f"{scheme}://{host}/",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return None, "curl: timeout"
    if proc.returncode != 0:
        return None, (proc.stderr or "curl error").strip()[:300]
    parts = (proc.stdout or "").strip().split("\t")
    if len(parts) < 4:
        return None, "curl: unexpected output"
    try:
        code = int(parts[0])
        ms = int(float(parts[1]) * 1000)
    except ValueError:
        return None, "curl: unexpected output"
    if code <= 0:
        return None, (proc.stderr or "нет ответа").strip()[:300]
    return {
        "status_code": code,
        "ms": ms,
        "remote_ip": parts[2],
        "url": parts[3],
    }, None


async def check_provider(ip_raw, host_raw):
    host, ip, version = _normalize_provider_target(ip_raw, host_raw)
    last_error = None
    for scheme in ("https", "http"):
        result, error = await _curl_provider_async(ip, host, version, scheme)
        if result:
            code = result["status_code"]
            state = "ok" if code < 400 else ("warn" if code < 500 else "fail")
            return {
                "ip": ip,
                "host": host,
                "scheme": scheme,
                "status_code": code,
                "ms": result["ms"],
                "remote_ip": result["remote_ip"],
                "state": state,
                "error": None,
                "note": "HTTPS недоступен, ответ получен по HTTP" if scheme == "http" else None,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        last_error = error
    return {
        "ip": ip,
        "host": host,
        "scheme": None,
        "status_code": None,
        "ms": None,
        "remote_ip": None,
        "state": "fail",
        "error": last_error or "нет ответа",
        "note": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
