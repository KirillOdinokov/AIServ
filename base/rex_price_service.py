"""Применение прайса ТД РЕКС к сайту rex.moscow: slug -> цена.

Прайс разбирается rex_price_parser.parse() (4 листа, sheet+row), маппинг
slug -> (лист, строка, вариант упаковки) — в site_price_mapping. Розничная цена
становится базовой ценой товара; итоговая цена сайта считается через
price_engine.compute_site_price() и кладётся в product_sites.current_price.
"""
import traceback

import db
import price_engine
import rex_price_parser

REX_SUPPLIER_SLUG = "td-reks"


def parse_price_list(path):
    return rex_price_parser.parse(path)


def apply_mapping(site_id, items):
    """items из rex_price_parser.parse(). Возвращает ({slug: retail}, missing)."""
    index = {}
    for it in items:
        index[(it["sheet"], it["row"])] = it

    out = {}
    missing = []
    for m in db.query(
        "SELECT slug, sheet, row_num, pack_variant FROM site_price_mapping WHERE site_id = %s",
        (site_id,),
    ):
        it = index.get((m["sheet"], m["row_num"]))
        if not it:
            missing.append((m["slug"], m["sheet"], m["row_num"]))
            continue
        pv = m["pack_variant"] or 0
        pack = it["packs"][pv] if 0 <= pv < len(it["packs"]) else it["packs"][0]
        out[m["slug"]] = pack["retail"]
    return out, missing


def _apply_to_site(site, prices):
    site_id = site["id"]
    for slug, retail in prices.items():
        prod = db.query(
            "SELECT id FROM products WHERE sku = %s AND supplier_slug = %s",
            (slug, REX_SUPPLIER_SLUG),
        )
        if not prod:
            continue
        pid = prod[0]["id"]
        db.execute(
            "UPDATE products SET price = %s, input_price = %s, price_source = 'rex_price', "
            "price_updated_at = now() WHERE id = %s",
            (retail, retail, pid),
        )
        site_price = price_engine.compute_site_price(retail, site, pid)
        db.execute(
            "INSERT INTO product_sites (product_id, site_id, site_product_id, site_sku, "
            "current_price, currency, sync_status, last_sync_at) "
            "VALUES (%s, %s, %s, %s, %s, 'RUB', 'synced', now()) "
            "ON CONFLICT (site_id, site_product_id) DO UPDATE SET "
            "current_price = EXCLUDED.current_price, sync_status = 'synced', last_sync_at = now()",
            (pid, site_id, slug, slug, site_price),
        )


def process_price_list(price_list_id):
    """Полный цикл для прайса ТД РЕКС: парсинг -> маппинг -> цены сайта."""
    rec = db.query("SELECT * FROM price_lists WHERE id = %s", (price_list_id,))
    if not rec:
        return
    rec = rec[0]
    db.execute("UPDATE price_lists SET status='parsing', error=NULL WHERE id=%s", (price_list_id,))
    try:
        items = rex_price_parser.parse(rec["stored_path"])
        sites = db.query("SELECT * FROM sites WHERE supplier_id = %s", (rec["supplier_id"],))
        applied_slugs = 0
        for site in sites:
            prices, missing = apply_mapping(site["id"], items)
            _apply_to_site(site, prices)
            applied_slugs += len(prices)
        db.execute(
            "UPDATE price_lists SET status='applied', applied_at=now(), "
            "rows_total=%s, rows_matched=%s, meta=jsonb_build_object('rex', true) WHERE id=%s",
            (len(items), applied_slugs, price_list_id),
        )
    except Exception as exc:
        traceback.print_exc()
        db.execute(
            "UPDATE price_lists SET status='failed', error=%s WHERE id=%s",
            (str(exc)[:2000], price_list_id),
        )


def get_site_prices(site_id):
    """{slug: итоговая цена} для сайта (из product_sites.current_price)."""
    rows = db.query(
        "SELECT site_sku, current_price FROM product_sites WHERE site_id = %s AND site_sku IS NOT NULL",
        (site_id,),
    )
    return {r["site_sku"]: r["current_price"] for r in rows}
