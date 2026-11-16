"""Применение прайса ТД РЕКС к сайту rex.moscow: slug -> цена.

Прайс разбирается rex_price_parser.parse() (4 листа, sheet+row), маппинг
slug -> (лист, строка, вариант упаковки) — в site_price_mapping. Розничная цена
становится базовой ценой товара; итоговая цена сайта считается через
price_engine.compute_site_price() и кладётся в product_sites.current_price.
"""
import json
import traceback

import db
import notifier
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
    changed = []
    for slug, retail in prices.items():
        prod = db.query(
            "SELECT id, name, price FROM products WHERE sku = %s AND supplier_slug = %s",
            (slug, REX_SUPPLIER_SLUG),
        )
        if not prod:
            continue
        pid = prod[0]["id"]
        name = prod[0].get("name") or slug
        old = prod[0].get("price")
        try:
            if old is None or float(old) != float(retail):
                changed.append({"slug": slug, "name": name,
                                "old": (float(old) if old is not None else None),
                                "new": float(retail) if retail is not None else None})
        except (TypeError, ValueError):
            changed.append({"slug": slug, "name": name, "old": old, "new": retail})
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
    return changed


def process_price_list(price_list_id):
    """Полный цикл для прайса ТД РЕКС: парсинг -> маппинг -> цены сайта.

    Возвращает результат для автоответа: какие сайты и сколько позиций обновлено.
    """
    rec = db.query("SELECT * FROM price_lists WHERE id = %s", (price_list_id,))
    if not rec:
        return {"ok": False, "error": "прайс не найден"}
    rec = rec[0]
    db.execute("UPDATE price_lists SET status='parsing', error=NULL WHERE id=%s", (price_list_id,))
    try:
        items = rex_price_parser.parse(rec["stored_path"])
        sites = db.query("SELECT * FROM sites WHERE supplier_id = %s", (rec["supplier_id"],))
        if not sites:
            sup = db.query(
                "SELECT name FROM suppliers WHERE id = %s", (rec["supplier_id"],)
            ) if rec.get("supplier_id") else []
            sup_name = sup[0]["name"] if sup else "неизвестный поставщик"
            notifier.add_event(
                "price_no_site", "Прайс не применён",
                "Прайс не применён: у поставщика нет сайта",
                "Прайс «%s» (%s) не применён: ни один сайт не привязан к поставщику. "
                "Привяжите сайт в настройках и переобработайте прайс."
                % (rec["filename"], sup_name),
                "price_no_site:%s:%s" % (rec["supplier_id"], price_list_id),
            )
        applied_slugs = 0
        changed_total = 0
        per_site = []
        for site in sites:
            prices, missing = apply_mapping(site["id"], items)
            changed = _apply_to_site(site, prices)
            applied_slugs += len(prices)
            changed_total += len(changed)
            per_site.append({
                "site_id": site["id"],
                "name": site["name"],
                "domain": site["domain"],
                "updated": len(changed),
                "mapped": len(prices),
                "missing": len(missing),
                "items": changed,
            })
        meta_sites = [
            {k: v for k, v in s.items() if k != "items"}
            for s in per_site
        ]
        db.execute(
            "UPDATE price_lists SET status='applied', applied_at=now(), "
            "rows_total=%s, rows_matched=%s, "
            "meta=jsonb_build_object('rex', true, 'sites', %s::jsonb) WHERE id=%s",
            (len(items), applied_slugs, json.dumps(meta_sites, ensure_ascii=False), price_list_id),
        )
        for site in per_site:
            if site["updated"] > 0:
                notifier.add_event(
                    "price_update", site["name"],
                    "Цены обновлены: %s" % site["name"],
                    "%s — %d поз. по прайсу «%s». Сайт подхватит изменения в течение 10 минут."
                    % (site["domain"] or site["name"], site["updated"], rec["filename"]),
                    "price_update:%s:%s" % (price_list_id, site["site_id"]),
                )
        return {
            "ok": True,
            "auto_applied": True,
            "sites": per_site,
            "updated": changed_total,
            "rows": len(items),
        }
    except Exception as exc:
        traceback.print_exc()
        db.execute(
            "UPDATE price_lists SET status='failed', error=%s WHERE id=%s",
            (str(exc)[:2000], price_list_id),
        )
        return {"ok": False, "error": str(exc)[:500]}


def get_site_prices(site_id):
    """{slug: итоговая цена} для сайта (из product_sites.current_price)."""
    rows = db.query(
        "SELECT site_sku, current_price FROM product_sites WHERE site_id = %s AND site_sku IS NOT NULL",
        (site_id,),
    )
    return {r["site_sku"]: r["current_price"] for r in rows}
