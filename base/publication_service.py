"""Задачи публикации: расширение ассортимента из несопоставленных строк прайса."""
import json

import db


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


def create_assortment_task(price_list_id):
    rec = db.query(
        "SELECT pl.*, s.name AS supplier_name FROM price_lists pl "
        "LEFT JOIN suppliers s ON s.id = pl.supplier_id WHERE pl.id = %s",
        (price_list_id,),
    )
    if not rec:
        raise ValueError("Задача прайса не найдена")
    pl = rec[0]

    columns = db.query(
        "SELECT col_index, header, price_type, markup_pct, unit, volume_min, volume_max "
        "FROM price_list_columns WHERE price_list_id = %s ORDER BY col_index",
        (price_list_id,),
    )
    columns_meta = [
        {
            "col_index": c["col_index"],
            "header": c["header"],
            "price_type": c["price_type"],
            "markup_pct": float(c["markup_pct"] or 0),
            "unit": c["unit"],
            "volume_min": float(c["volume_min"]) if c["volume_min"] is not None else None,
            "volume_max": float(c["volume_max"]) if c["volume_max"] is not None else None,
        }
        for c in columns
    ]

    items = db.query(
        "SELECT * FROM price_list_items WHERE price_list_id = %s "
        "AND (matched_product_id IS NULL OR match_status = 'rejected') "
        "ORDER BY row_num",
        (price_list_id,),
    )
    if not items:
        raise ValueError("Все строки сопоставлены — новых товаров нет")

    title = "Расширение ассортимента — " + (pl.get("filename") or ("прайс #%s" % price_list_id))
    meta = {
        "price_list_id": price_list_id,
        "filename": pl.get("filename"),
        "price_date": pl["price_date"].isoformat() if pl.get("price_date") else None,
        "supplier_id": pl.get("supplier_id"),
        "supplier_name": pl.get("supplier_name"),
        "columns": columns_meta,
    }

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO publication_tasks (type, title, supplier_id, price_list_id, "
                "status, items_total, meta) "
                "VALUES ('assortment', %s, %s, %s, 'new', %s, %s) "
                "ON CONFLICT (type, price_list_id) DO UPDATE SET "
                " title = EXCLUDED.title, supplier_id = EXCLUDED.supplier_id, "
                " items_total = EXCLUDED.items_total, meta = EXCLUDED.meta, updated_at = now() "
                "RETURNING id",
                (
                    title, pl.get("supplier_id"), price_list_id, len(items),
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            task_id = cur.fetchone()["id"]
            cur.execute("DELETE FROM publication_items WHERE task_id = %s", (task_id,))

            for item in items:
                prices = _json(item.get("prices"), {})
                raw_extra = _json(item.get("raw_extra"), {})
                raw_row = _json(item.get("raw_row"), [])
                raw_data = {
                    "raw_name": item["raw_name"],
                    "raw_sku": item.get("raw_sku"),
                    "raw_unit": item.get("raw_unit"),
                    "prices": prices,
                    "raw_extra": raw_extra,
                    "raw_row": raw_row,
                    "section": raw_extra.get("Раздел"),
                    "price_columns": columns_meta,
                    "source": meta,
                }
                cur.execute(
                    "INSERT INTO publication_items (task_id, price_list_item_id, raw_name, "
                    "raw_sku, raw_unit, prices, raw_extra, raw_row, raw_data) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        task_id, item["id"], item["raw_name"], item.get("raw_sku"),
                        item.get("raw_unit"),
                        json.dumps(prices, ensure_ascii=False),
                        json.dumps(raw_extra, ensure_ascii=False),
                        json.dumps(raw_row, ensure_ascii=False),
                        json.dumps(raw_data, ensure_ascii=False),
                    ),
                )
        conn.commit()
    return task_id
