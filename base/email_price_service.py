"""Изменение цены по письму без прайса.

Пример: «надо заменить цену на https://rex.moscow/reks-epo-uni-shot-long 7000 рублей».

Белый список: отправитель с домена `enix.ru` или с домена самого сайта
(динамически из `sites.domain`), плюс `EMAIL_PRICE_EXTRA_DOMAINS`.
URL определяет сайт и позицию, цена — итоговая цена сайта.
"""
import os
import re
import traceback
from urllib.parse import unquote, urlsplit

import db
import mailer
import notifier
import price_service

DRY_RUN = os.getenv("EMAIL_PRICE_DRY_RUN", "") not in ("", "0", "false", "no")
EXTRA_DOMAINS = [
    d.strip().lower()
    for d in os.getenv("EMAIL_PRICE_EXTRA_DOMAINS", "").split(",")
    if d.strip()
]
MAX_PRICE = float(os.getenv("EMAIL_PRICE_MAX", "10000000"))

TRIGGER_RE = re.compile(
    r"(новая\s+цена|новую\s+цену|новой\s+цене|"
    r"цен|измен|помен|смен|замен|обнов|постав|установ|прав|сдела|стоимост|price)",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
PRICE_RE = re.compile(
    r"(?<![\d.,])"
    r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?|\d{3,9}(?:[.,]\d{1,2})?)"
    r"(?![\d])"
)


def _parse_price(text):
    for m in PRICE_RE.finditer(text or ""):
        raw = m.group(1).replace("\u00a0", " ").replace(" ", "").replace(",", ".")
        try:
            value = round(float(raw), 2)
        except ValueError:
            continue
        if 0 < value <= MAX_PRICE:
            return value
    return None


def looks_like_request(text):
    return bool(text and TRIGGER_RE.search(text) and URL_RE.search(text) and _parse_price(text))


def parse_commands(text):
    """Найти в тексте пары «URL + цена» (по одной на строку, можно несколько строк)."""
    cmds = []
    for line in (text or "").splitlines():
        urls = URL_RE.findall(line)
        if not urls:
            continue
        for url in urls:
            clean = url.rstrip(".,;:!?)]»\"'")
            idx = line.find(clean)
            tail = line[idx + len(clean):] if idx >= 0 else ""
            price = _parse_price(tail)
            if price is None and idx > 0:
                price = _parse_price(line[:idx][-80:])
            if price is None:
                continue
            parts = urlsplit(clean)
            host = (parts.hostname or "").lower()
            path = parts.path.strip("/")
            slug = unquote(path.rsplit("/", 1)[-1]) if path else ""
            if slug.lower().endswith(".html"):
                slug = slug[:-5]
            if not host or not slug:
                continue
            cmds.append({"url": clean, "host": host, "slug": slug, "price": price})
    unique = {}
    for cmd in cmds:
        unique[(cmd["host"], cmd["slug"])] = cmd
    return list(unique.values())


def _sender_domain(from_addr):
    if not from_addr or "@" not in from_addr:
        return ""
    return from_addr.rsplit("@", 1)[1].strip().lower()


def sender_allowed(from_addr, site):
    domain = _sender_domain(from_addr)
    if not domain:
        return False
    site_domain = (site.get("domain") or "").lower()
    return (
        domain == "enix.ru"
        or domain in EXTRA_DOMAINS
        or (site_domain and domain == site_domain)
    )


def _money(value):
    if value is None:
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(num - round(num)) < 0.005:
        return "{:,.0f}".format(num).replace(",", " ") + " ₽"
    return "{:,.2f}".format(num).replace(",", " ").replace(".", ",") + " ₽"


def _find_site(host):
    host = (host or "").lower()
    candidates = {host}
    if host.startswith("www."):
        candidates.add(host[4:])
    else:
        candidates.add("www." + host)
    rows = db.query(
        "SELECT * FROM sites WHERE lower(domain) = ANY(%s) ORDER BY active DESC LIMIT 1",
        (list(candidates),),
    )
    return rows[0] if rows else None


def _find_product(site, slug):
    rows = db.query(
        "SELECT ps.product_id, ps.site_product_id, ps.site_sku, ps.current_price, "
        "       p.name, p.price, p.price_type "
        "FROM product_sites ps JOIN products p ON p.id = ps.product_id "
        "WHERE ps.site_id = %s AND (ps.site_sku = %s OR ps.site_product_id = %s) LIMIT 1",
        (site["id"], slug, slug),
    )
    if rows:
        row = dict(rows[0])
        row["slug"] = slug
        return row
    rows = db.query(
        "SELECT sp.product_id, sp.remote_id, sp.sku, sp.url, p.name, p.price, p.price_type, "
        "       ps.current_price, ps.site_product_id, ps.site_sku "
        "FROM site_products sp JOIN products p ON p.id = sp.product_id "
        "LEFT JOIN product_sites ps ON ps.site_id = sp.site_id AND ps.product_id = sp.product_id "
        "WHERE sp.site_id = %s AND (sp.sku = %s OR sp.remote_id::text = %s) LIMIT 1",
        (site["id"], slug, slug),
    )
    if rows:
        row = dict(rows[0])
        row["slug"] = slug
        row["site_product_id"] = row.get("site_product_id") or row.get("remote_id") or slug
        row["site_sku"] = row.get("site_sku") or slug
        return row
    if site.get("supplier_id"):
        rows = db.query(
            "SELECT p.id AS product_id, p.name, p.price, p.price_type "
            "FROM products p JOIN suppliers s ON s.slug = p.supplier_slug "
            "WHERE s.id = %s AND p.sku = %s LIMIT 1",
            (site["supplier_id"], slug),
        )
        if rows:
            row = dict(rows[0])
            row["slug"] = slug
            row["site_product_id"] = slug
            return row
    return None


def _apply_change(site, prod, new_price, from_addr, message_id):
    pid = prod["product_id"]
    old_site = prod.get("current_price")
    if old_site is None:
        old_site = prod.get("price")
    old_base = prod.get("price")
    mode = (site.get("price_mode") or "source")
    is_wp = (site.get("platform") or "") == "woocommerce" or bool(site.get("api_key"))
    site_product_id = str(prod.get("site_product_id") or prod.get("slug") or pid)
    site_sku = prod.get("site_sku") or prod.get("slug") or site_product_id

    with db.connect() as conn:
        with conn.cursor() as cur:
            if mode == "source":
                cur.execute(
                    "UPDATE products SET price = %s, input_price = %s, "
                    "price_source = 'manual_email', price_updated_at = now() WHERE id = %s",
                    (new_price, new_price, pid),
                )
            cur.execute(
                "INSERT INTO price_changes (product_id, site_id, old_price, new_price, currency, "
                "source, changed_by, note, applied, reason, source_updated_at) "
                "VALUES (%s, %s, %s, %s, 'RUB', 'manual', %s, %s, true, 'email', now())",
                (pid, site["id"], old_base, new_price, from_addr, "Изменение цены по письму"),
            )
            cur.execute(
                "INSERT INTO product_sites (product_id, site_id, site_product_id, site_sku, "
                "current_price, currency, sync_status, last_sync_at) "
                "VALUES (%s, %s, %s, %s, %s, 'RUB', 'synced', now()) "
                "ON CONFLICT (site_id, site_product_id) DO UPDATE SET "
                "current_price = EXCLUDED.current_price, sync_status = 'synced', last_sync_at = now()",
                (pid, site["id"], site_product_id, site_sku, new_price),
            )
            if is_wp:
                cur.execute(
                    "UPDATE site_updates SET old_price = COALESCE(old_price, %s), new_price = %s, "
                    "currency = 'RUB', source_updated_at = now(), error = NULL, "
                    "version = nextval('site_update_version_seq') "
                    "WHERE site_id = %s AND product_id = %s AND status = 'pending'",
                    (old_site, new_price, site["id"], pid),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        "INSERT INTO site_updates (site_id, product_id, old_price, new_price, "
                        "currency, status, source_updated_at) "
                        "VALUES (%s, %s, %s, %s, 'RUB', 'pending', now())",
                        (site["id"], pid, old_site, new_price),
                    )
        conn.commit()

    if is_wp:
        try:
            price_service.ping_site(site["id"])
        except Exception:
            traceback.print_exc()

    notifier.add_event(
        "price_manual", site["name"],
        "Цена изменена вручную: %s" % (prod.get("name") or prod.get("slug")),
        "%s — %s: было %s → стало %s (письмо %s)."
        % (site["domain"] or site["name"], prod.get("name") or prod.get("slug"),
           _money(old_site), _money(new_price), from_addr),
        "price_manual:%s:%s:%s" % (site["id"], pid, message_id or from_addr),
    )
    return old_site


def _send_reply(to_addr, subject, message_id, results):
    lines = ["Здравствуйте!", "", "Результат изменения цен по вашему письму:"]
    for res in results:
        if res.get("ok"):
            lines.append("  • %s — %s: было %s → стало %s" % (
                res.get("site") or res.get("url"),
                res.get("name") or "",
                _money(res.get("old")), _money(res.get("new")),
            ))
        else:
            lines.append("  • %s — не изменено: %s" % (
                res.get("url") or res.get("site") or "—",
                res.get("error") or "ошибка",
            ))
    lines += [
        "",
        "—",
        "Сервис «База», AI ENIX",
        "Письмо сформировано автоматически, отвечать на него не нужно.",
    ]
    mailer.send(
        to_addr,
        "Re: %s" % (subject or "изменение цены"),
        "\n".join(lines),
        in_reply_to=message_id,
        references=message_id,
        dry_run=DRY_RUN,
    )


def process(from_addr, subject, message_id, text, send_reply=True):
    commands = parse_commands(text)
    if not commands:
        return {"ok": False, "ignored": True, "results": []}

    results = []
    allowed_any = False
    changed = 0
    for cmd in commands:
        site = _find_site(cmd["host"])
        if not site:
            results.append({"ok": False, "url": cmd["url"],
                            "error": "сайт не найден в «Базе»"})
            continue
        if not sender_allowed(from_addr, site):
            continue
        allowed_any = True
        prod = _find_product(site, cmd["slug"])
        if not prod:
            results.append({"ok": False, "url": cmd["url"], "site": site["name"],
                            "error": "позиция не найдена на сайте"})
            continue
        old = prod.get("current_price")
        if old is None:
            old = prod.get("price")
        if DRY_RUN:
            results.append({"ok": True, "dry_run": True, "site": site["name"],
                            "url": cmd["url"], "name": prod.get("name"),
                            "old": old, "new": cmd["price"]})
            continue
        try:
            old = _apply_change(site, prod, cmd["price"], from_addr, message_id)
            changed += 1
            results.append({"ok": True, "site": site["name"], "url": cmd["url"],
                            "name": prod.get("name"), "old": old, "new": cmd["price"]})
        except Exception as exc:
            traceback.print_exc()
            results.append({"ok": False, "url": cmd["url"], "site": site["name"],
                            "error": str(exc)[:300]})

    if not allowed_any:
        return {"ok": False, "ignored": True, "results": []}
    if send_reply and results:
        try:
            _send_reply(from_addr, subject, message_id, results)
        except Exception:
            traceback.print_exc()
    return {"ok": True, "changed": changed, "ignored": False, "results": results}
