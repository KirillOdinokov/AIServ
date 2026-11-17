"""Применение прайса VILPE к сайту vilpe-roofing.ru.

Прайс разбирается vilpe_price_parser.parse(). Маппинг slug -> (лист, строка,
вариант упаковки) — в site_price_mapping. Если строка в новом прайсе сдвинулась,
используется fallback по артикулу VILPE (products.manufacturer_sku).
"""
import json
import traceback

import db
import notifier
import price_engine
import vilpe_price_parser

VILPE_SUPPLIER_SLUG = "vilpe"


def _norm_sku(value):
    return vilpe_price_parser.norm_code(value)


def process_price_list(price_list_id, dry_run=False):
    """Разбор/применение прайса VILPE. dry_run — только сверка, без записи."""
    rec = db.query("SELECT * FROM price_lists WHERE id = %s", (price_list_id,))
    if not rec:
        return {"ok": False, "error": "прайс не найден"}
    rec = rec[0]
    if not dry_run:
        db.execute(
            "UPDATE price_lists SET status='parsing', error=NULL WHERE id=%s",
            (price_list_id,),
        )
    try:
        items = vilpe_price_parser.parse(rec["stored_path"])
        by_row = {
            (str(it.get("sheet") or "").strip(), it.get("row")): it
            for it in items
        }
        by_code = {}
        for it in items:
            code = _norm_sku(it.get("code"))
            if code:
                by_code.setdefault(code, it)

        sites = db.query(
            "SELECT * FROM sites WHERE supplier_id = %s", (rec.get("supplier_id"),)
        )
        if not sites:
            sup = db.query(
                "SELECT name FROM suppliers WHERE id = %s", (rec.get("supplier_id"),)
            ) if rec.get("supplier_id") else []
            sup_name = sup[0]["name"] if sup else "неизвестный поставщик"
            notifier.add_event(
                "price_no_site", "Прайс не применён",
                "Прайс не применён: у поставщика нет сайта",
                "Прайс «%s» (%s) не применён: ни один сайт не привязан к поставщику. "
                "Привяжите сайт в настройках и переобработайте прайс."
                % (rec["filename"], sup_name),
                "price_no_site:%s:%s" % (rec.get("supplier_id"), price_list_id),
            )

        per_site = []
        changed_total = 0
        mapped_total = 0
        for site in sites:
            products = db.query(
                "SELECT ps.site_sku AS slug, p.id AS product_id, p.name, p.price, "
                "       p.price_type, p.mrc_price, p.manufacturer_sku "
                "FROM product_sites ps JOIN products p ON p.id = ps.product_id "
                "WHERE ps.site_id = %s",
                (site["id"],),
            )
            by_slug = {r["slug"]: r for r in products}
            mapping = db.query(
                "SELECT slug, sheet, row_num, pack_variant FROM site_price_mapping "
                "WHERE site_id = %s",
                (site["id"],),
            )
            changed, missing = [], []
            mapped = 0
            savo_skipped = 0
            for m in mapping:
                prod = by_slug.get(m["slug"])
                if not prod:
                    missing.append(m["slug"])
                    continue
                # SAVO идёт отдельным прайсом; основной лист VILPE его не трогает
                if "savo" in (prod.get("name") or "").lower():
                    savo_skipped += 1
                    continue
                item = by_row.get((str(m.get("sheet") or "").strip(), m.get("row_num")))
                if not item:
                    code = _norm_sku(prod.get("manufacturer_sku"))
                    item = by_code.get(code) if code else None
                if not item:
                    missing.append(m["slug"])
                    continue
                mapped += 1
                # цены VILPE на сайте — целые рубли, дробная часть отбрасывается
                retail = int(float(item["price"]))
                old = prod.get("price")
                if old is None or float(old) != retail:
                    site_price = price_engine.compute_site_price(
                        retail, site, prod["product_id"],
                        price_type=prod.get("price_type"),
                        mrc_price=prod.get("mrc_price"),
                    )
                    changed.append({
                        "slug": m["slug"],
                        "product_id": prod["product_id"],
                        "name": prod.get("name") or m["slug"],
                        "old": (float(old) if old is not None else None),
                        "new": retail,
                        "site_price": (float(site_price) if site_price is not None else None),
                    })
            mapped_total += mapped
            changed_total += len(changed)

            if changed and not dry_run:
                with db.connect() as conn:
                    with conn.cursor() as cur:
                        for ch in changed:
                            site_price = ch["site_price"]
                            cur.execute(
                                "UPDATE products SET price = %s, input_price = %s, "
                                "price_source = 'vilpe_price', price_updated_at = now() "
                                "WHERE id = %s",
                                (ch["new"], ch["new"], ch["product_id"]),
                            )
                            cur.execute(
                                "UPDATE product_sites SET current_price = %s, "
                                "sync_status = 'synced', last_sync_at = now() "
                                "WHERE site_id = %s AND product_id = %s",
                                (site_price, site["id"], ch["product_id"]),
                            )
                    conn.commit()

            per_site.append({
                "site_id": site["id"],
                "name": site["name"],
                "domain": site["domain"],
                "updated": len(changed),
                "mapped": mapped,
                "missing": len(missing),
                "missing_slugs": missing[:50],
                "skipped_savo": savo_skipped,
                "items": [
                    {"slug": c["slug"], "name": c["name"], "old": c["old"], "new": c["new"]}
                    for c in changed
                ],
            })

        result = {
            "ok": True,
            "auto_applied": True,
            "sites": per_site,
            "updated": changed_total,
            "rows": len(items),
            "mapped": mapped_total,
            "dry_run": dry_run,
        }
        if dry_run:
            return result

        meta_sites = [
            {k: v for k, v in s.items() if k not in ("items", "missing_slugs")}
            for s in per_site
        ]
        db.execute(
            "UPDATE price_lists SET status='applied', applied_at=now(), "
            "rows_total=%s, rows_matched=%s, "
            "meta=jsonb_build_object('vilpe', true, 'sites', %s::jsonb) WHERE id=%s",
            (len(items), mapped_total, json.dumps(meta_sites, ensure_ascii=False), price_list_id),
        )
        for site in per_site:
            if site["updated"] > 0:
                notifier.add_event(
                    "price_update", site["name"],
                    "Цены обновлены: %s" % site["name"],
                    "%s — %d поз. по прайсу «%s». Сайт подхватит изменения в течение 30 минут."
                    % (site["domain"] or site["name"], site["updated"], rec["filename"]),
                    "price_update:%s:%s" % (price_list_id, site["site_id"]),
                )
        return result
    except Exception as exc:
        traceback.print_exc()
        if not dry_run:
            db.execute(
                "UPDATE price_lists SET status='failed', error=%s WHERE id=%s",
                (str(exc)[:2000], price_list_id),
            )
        return {"ok": False, "error": str(exc)[:500]}
