"""Импорт каталога WooCommerce-сайта в site_products и сопоставление с Base.

Сопоставление: SKU поставщика -> точное имя -> нечёткое имя (среди товаров
поставщика сайта). Запуск: python import_site_catalog.py steklotkani.ru
"""
import base64
import json
import re
import sys
import urllib.request
from difflib import SequenceMatcher

import db

UA = "AI-ENIX/1.0"


def norm_sku(value):
    return re.sub(r"[^A-Z0-9/-]", "", (value or "").upper())


def norm_name(value):
    s = (value or "").lower().replace("ё", "е")
    s = re.sub(r"\(?\s*копи(?:ровать|я)\s*\)?", " ", s)
    s = re.sub(r"[^0-9a-zа-я]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_wc(site):
    base = (site["base_url"] or ("https://" + site["domain"])).rstrip("/")
    auth = base64.b64encode(
        ("%s:%s" % (site["wc_key"], site["wc_secret"])).encode()
    ).decode()
    out, page = [], 1
    while True:
        url = (
            base + "/wp-json/wc/v3/products?per_page=100&page=%d"
            "&_fields=id,name,sku,price,permalink" % page
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Authorization": "Basic " + auth}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            batch = json.load(resp)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def main(domain):
    rows = db.query(
        "SELECT id, domain, base_url, wc_key, wc_secret, supplier_id "
        "FROM sites WHERE domain = %s",
        (domain,),
    )
    if not rows:
        print("site not found:", domain)
        return 1
    site = rows[0]
    supplier_slug = None
    if site["supplier_id"]:
        r = db.query("SELECT amalor_slug FROM suppliers WHERE id = %s", (site["supplier_id"],))
        supplier_slug = r[0]["amalor_slug"] if r else None

    products = db.query(
        "SELECT id, manufacturer_sku_norm, normalized_name, supplier_slug "
        "FROM products WHERE is_published"
    )
    pool = [
        p for p in products
        if supplier_slug is None or p["supplier_slug"] == supplier_slug
    ]
    by_sku = {}
    by_name = {}
    for p in pool:
        if p["manufacturer_sku_norm"]:
            by_sku.setdefault(p["manufacturer_sku_norm"], []).append(p)
        key = norm_name(p["normalized_name"])
        if key:
            by_name.setdefault(key, []).append(p)

    by_id = {p["id"]: p for p in products}

    def pick(cands):
        if supplier_slug:
            for c in cands:
                if c["supplier_slug"] == supplier_slug:
                    return c
        return min(cands, key=lambda c: c["id"])

    supplier_products = []
    if supplier_slug:
        supplier_products = db.query(
            "SELECT id, normalized_name FROM products WHERE supplier_slug = %s",
            (supplier_slug,),
        )
        supplier_products = [
            (r["id"], norm_name(r["normalized_name"])) for r in supplier_products
        ]

    wc = fetch_wc(site)
    print("site products:", len(wc), "| supplier:", supplier_slug,
          "| supplier products:", len(supplier_products))

    db.execute("DELETE FROM product_sites WHERE site_id = %s", (site["id"],))
    db.execute("DELETE FROM site_products WHERE site_id = %s", (site["id"],))
    overrides = {
        r["remote_id"]: r["product_id"]
        for r in db.query(
            "SELECT remote_id, product_id FROM site_product_overrides WHERE site_id = %s",
            (site["id"],),
        )
    }

    stats = {"sku": 0, "name": 0, "manual": 0, "none": 0}
    conflicts = []
    ambiguous = 0
    unmatched = []
    matched_ids = set()

    for item in wc:
        sku = item.get("sku") or ""
        name = item.get("name") or ""
        nsku = norm_sku(sku)
        nname = norm_name(name)
        sku_pid = None
        name_pid = None

        if nsku and nsku in by_sku:
            cands = by_sku[nsku]
            sku_pid = pick(cands)["id"]
            if len(cands) > 1:
                ambiguous += 1
        if nname and nname in by_name:
            name_pid = pick(by_name[nname])["id"]

        pid, method = None, None
        if sku_pid and name_pid and sku_pid != name_pid:
            a = by_id.get(sku_pid, {})
            b = by_id.get(name_pid, {})
            if norm_name(a.get("normalized_name")) == norm_name(b.get("normalized_name")):
                # разные строки-дубли одного и того же товара в базе
                pid, method = sku_pid, "sku"
            else:
                # SKU ведёт на другой товар (часто дубль SKU на сайте) — доверяем имени
                pid, method = name_pid, "name"
                conflicts.append({"sku": sku, "name": name,
                                  "sku_product": sku_pid, "name_product": name_pid})
        elif sku_pid:
            pid, method = sku_pid, "sku"
        elif name_pid:
            pid, method = name_pid, "name"
        # fuzzy-матчинг отключён: ложные совпадения по размерам (РСТ-700 → РСТ-200).
        # Спорные позиции размечаются вручную через site_product_overrides.

        if item.get("id") in overrides:
            pid, method = overrides[item.get("id")], "manual"

        if pid is None:
            stats["none"] += 1
            unmatched.append({"sku": sku, "name": name, "url": item.get("permalink")})
        else:
            stats[method] += 1
            matched_ids.add(pid)

        price = item.get("price")
        try:
            price = float(price) if price not in (None, "") else None
        except ValueError:
            price = None

        db.execute(
            "INSERT INTO site_products (site_id, remote_id, sku, sku_norm, name, name_norm, "
            "price, url, product_id, matched_by, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (site_id, remote_id) DO UPDATE SET "
            "sku = EXCLUDED.sku, sku_norm = EXCLUDED.sku_norm, name = EXCLUDED.name, "
            "name_norm = EXCLUDED.name_norm, price = EXCLUDED.price, url = EXCLUDED.url, "
            "product_id = EXCLUDED.product_id, matched_by = EXCLUDED.matched_by, "
            "updated_at = now()",
            (site["id"], item.get("id"), sku, nsku, name, nname, price,
             item.get("permalink"), pid, method),
        )

    db.execute(
        "INSERT INTO product_sites (product_id, site_id, site_product_id, site_sku, "
        "current_price, currency, sync_status, last_sync_at) "
        "SELECT product_id, site_id, remote_id::text, sku, price, 'RUB', "
        "       matched_by, now() "
        "FROM site_products WHERE site_id = %s AND product_id IS NOT NULL "
        "ON CONFLICT (site_id, site_product_id) DO UPDATE SET "
        "product_id = EXCLUDED.product_id, site_sku = EXCLUDED.site_sku, "
        "current_price = EXCLUDED.current_price, sync_status = EXCLUDED.sync_status, "
        "last_sync_at = now()",
        (site["id"],),
    )

    print("matched: sku=%d name=%d manual=%d | unmatched=%d | distinct base products=%d | "
          "ambiguous sku=%d | sku/name conflicts=%d"
          % (stats["sku"], stats["name"], stats["manual"], stats["none"],
             len(matched_ids), ambiguous, len(conflicts)))
    for c in conflicts:
        print("CONFLICT:", c["sku"], "|", c["name"],
              "| sku->", c["sku_product"], "name->", c["name_product"])
    for u in unmatched:
        print("UNMATCHED:", u["sku"], "|", u["name"], "|", u["url"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "steklotkani.ru"))
