"""Добавить товар сайта в Base вручную (когда его нет в Amalor).

Использование: python add_site_product.py <domain> <remote_id> [sku]
Создаёт products + supplier_products + product_sites + site_products + override.
"""
import base64
import json
import re
import sys
import urllib.request

import db


def norm_name(value):
    s = (value or "").lower().replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def wc_get(site, path):
    base = (site["base_url"] or ("https://" + site["domain"])).rstrip("/")
    auth = base64.b64encode(("%s:%s" % (site["wc_key"], site["wc_secret"])).encode()).decode()
    req = urllib.request.Request(
        base + "/wp-json/wc/v3/" + path,
        headers={"User-Agent": "AI-ENIX/1.0", "Authorization": "Basic " + auth},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def main(domain, remote_id, sku=None):
    sites = db.query(
        "SELECT id, domain, base_url, wc_key, wc_secret, supplier_id FROM sites WHERE domain=%s",
        (domain,),
    )
    if not sites:
        print("site not found")
        return 1
    site = sites[0]
    p = wc_get(site, "products/%d" % remote_id)
    name = p["name"]
    nname = norm_name(name)
    supplier_slug = None
    if site["supplier_id"]:
        r = db.query("SELECT amalor_slug FROM suppliers WHERE id=%s", (site["supplier_id"],))
        supplier_slug = r[0]["amalor_slug"] if r else None

    existing = db.query(
        "SELECT id, sku FROM products WHERE normalized_name=%s "
        "AND supplier_slug IS NOT DISTINCT FROM %s ORDER BY id LIMIT 1",
        (nname, supplier_slug),
    )
    if existing:
        pid = existing[0]["id"]
        print("using existing base product", pid, existing[0]["sku"])
    else:
        sku = sku or ("MANUAL-%d" % remote_id)
        price = p.get("price") or p.get("regular_price")
        try:
            price = float(price) if price not in (None, "") else None
        except ValueError:
            price = None
        row = db.query(
            "INSERT INTO products (sku, name, normalized_name, description, price, "
            "price_currency, price_source, price_updated_at, supplier_slug, supplier_url, "
            "source, is_published) "
            "VALUES (%s, %s, %s, %s, %s, 'RUB', 'manual', now(), %s, %s, 'manual', true) "
            "RETURNING id",
            (sku, name, nname, p.get("description") or None, price,
             supplier_slug, p.get("permalink")),
        )
        pid = row[0]["id"]
        print("created base product", pid, sku)

    if site["supplier_id"]:
        db.execute(
            "INSERT INTO supplier_products (supplier_id, product_id, product_type, "
            "price, supplier_url, source) VALUES (%s, %s, 'main', %s, %s, 'manual') "
            "ON CONFLICT (supplier_id, product_id, product_type) DO NOTHING",
            (site["supplier_id"], pid,
             float(p["price"]) if p.get("price") not in (None, "") else None,
             p.get("permalink")),
        )

    db.execute(
        "INSERT INTO product_sites (product_id, site_id, site_product_id, site_sku, "
        "current_price, currency, sync_status, last_sync_at) "
        "VALUES (%s, %s, %s, %s, %s, 'RUB', 'manual', now()) "
        "ON CONFLICT (site_id, site_product_id) DO UPDATE SET "
        "product_id=EXCLUDED.product_id, site_sku=EXCLUDED.site_sku, "
        "current_price=EXCLUDED.current_price, sync_status='manual', last_sync_at=now()",
        (pid, site["id"], str(remote_id), p.get("sku"),
         float(p["price"]) if p.get("price") not in (None, "") else None),
    )
    db.execute(
        "UPDATE site_products SET product_id=%s, matched_by='manual' "
        "WHERE site_id=%s AND remote_id=%s",
        (pid, site["id"], remote_id),
    )
    db.execute(
        "INSERT INTO site_product_overrides (site_id, remote_id, product_id, note) "
        "VALUES (%s, %s, %s, 'добавлено в Base вручную') "
        "ON CONFLICT (site_id, remote_id) DO UPDATE SET product_id=EXCLUDED.product_id",
        (site["id"], remote_id, pid),
    )
    print("done: product", pid, "mapped to site", site["id"])
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(sys.argv[1], int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else None))
