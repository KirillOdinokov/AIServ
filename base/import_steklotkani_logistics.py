"""Импорт логистики steklotkani из Knowledge/steklotkani-logistics в Base."""
import csv
import json
import re

import db

SITE_ID = 2
CSV_PATH = "/tmp/logistics_steklotkani.csv"
SOURCE = "knowledge/steklotkani-logistics"


def norm_sku(value):
    return re.sub(r"[^A-Z0-9/-]", "", (value or "").upper())


def num(value):
    v = (value or "").strip().replace(",", ".")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def mm(value):
    v = num(value)
    return None if v is None else v * 1000.0


rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig"), delimiter=";"))
sp = {
    int(r["remote_id"]): r["product_id"]
    for r in db.query(
        "SELECT remote_id, product_id FROM site_products "
        "WHERE site_id = %s AND remote_id IS NOT NULL AND product_id IS NOT NULL",
        (SITE_ID,),
    )
}
sku_map = {
    r["manufacturer_sku_norm"]: r["id"]
    for r in db.query(
        "SELECT id, manufacturer_sku_norm FROM products "
        "WHERE supplier_slug='steklotkani' AND is_published "
        "AND manufacturer_sku_norm IS NOT NULL"
    )
}

matched = skipped = 0
with_w = with_d = with_v = 0
skipped_ids = []

for row in rows:
    remote_id = (row.get("id") or "").strip()
    pid = sp.get(int(remote_id)) if remote_id.isdigit() else None
    if pid is None:
        pid = sku_map.get(norm_sku(row.get("sku")))
    if pid is None:
        skipped += 1
        skipped_ids.append(remote_id)
        continue

    weight = num(row.get("weight_kg"))
    volume = num(row.get("volume_m3"))
    length, width, height = mm(row.get("length_m")), mm(row.get("width_m")), mm(row.get("height_m"))

    logistics = {
        "availability": row.get("availability") or None,
        "dims_m": row.get("dims_m") or None,
        "winding_m": num(row.get("winding_m")),
        "density_g_m2": num(row.get("density_g_m2")),
        "material_width_cm": num(row.get("material_width_cm")),
        "obreshetka": row.get("obreshetka") or None,
        "price": num(row.get("price")),
        "sku": row.get("sku") or None,
        "title": row.get("title") or None,
        "source": SOURCE,
        "updated": row.get("updated") or None,
    }

    db.execute(
        "UPDATE products SET weight_kg=%s, dim_length_mm=%s, dim_width_mm=%s, "
        "dim_height_mm=%s, volume_m3=%s, logistics=%s::jsonb WHERE id=%s",
        (weight, length, width, height, volume,
         json.dumps(logistics, ensure_ascii=False), pid),
    )
    matched += 1
    if weight is not None:
        with_w += 1
    if length is not None or width is not None or height is not None:
        with_d += 1
    if volume is not None:
        with_v += 1

print("rows:", len(rows), "| matched:", matched, "| skipped:", skipped)
print("with weight:", with_w, "| with dims:", with_d, "| with volume:", with_v)
if skipped_ids:
    print("skipped ids:", ", ".join(skipped_ids))
