"""Обработка прайс-листов: загрузка, разбор, матчинг, применение."""
import json
import re
import threading
import traceback
import urllib.request
import uuid
from datetime import date, datetime
from pathlib import Path

import db
import notifier
import price_engine
import price_matcher
import price_parser
import price_reply
import rex_price_service
import vilpe_price_service

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads" / "prices"
ALLOWED_EXT = {".doc", ".docx", ".xls", ".xlsx", ".csv"}
MAX_BYTES = 60 * 1024 * 1024


def _safe_name(filename):
    name = Path(filename or "price").name
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name, flags=re.UNICODE)
    return name[:160] or "price"


def guess_price_date(filename):
    m = re.search(r"(\d{1,2})[._-](\d{1,2})[._-](\d{4})", filename or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def save_upload(data, filename):
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Поддерживаются .doc, .docx, .xls, .xlsx, .csv")
    if len(data) > MAX_BYTES:
        raise ValueError("Файл больше 60 МБ")
    day = datetime.now().strftime("%Y%m%d")
    folder = UPLOAD_DIR / day
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / (uuid.uuid4().hex[:12] + "_" + _safe_name(filename))
    stored.write_bytes(data)
    return stored, ext


def create_price_list(filename, stored_path, file_type, supplier_id, price_date=None, note=None):
    row = db.query(
        "INSERT INTO price_lists (supplier_id, filename, stored_path, file_type, source, status, "
        "price_date, notes) VALUES (%s, %s, %s, %s, 'upload', 'uploaded', %s, %s) RETURNING id",
        (supplier_id, filename, str(stored_path), file_type, price_date, note),
    )
    return row[0]["id"]


def start_processing(price_list_id):
    thread = threading.Thread(target=process_price_list, args=(price_list_id,), daemon=True)
    thread.start()


def recover_stuck():
    """Пометить задачи, прерванные перезапуском сервиса."""
    return db.execute(
        "UPDATE price_lists SET status = 'failed', "
        "error = COALESCE(error, 'Обработка прервана перезапуском сервиса') "
        "WHERE status IN ('uploaded', 'parsing', 'matching')"
    )


def _preset_key(header):
    return price_matcher.norm(header or "").replace(" ", "")


def _apply_column_presets(columns, supplier_id):
    """Применить сохранённые типы/наценку/основную колонку для поставщика."""
    if not supplier_id:
        return
    presets = db.query(
        "SELECT header_norm, price_type, markup_pct, is_base FROM price_column_presets "
        "WHERE supplier_id = %s",
        (supplier_id,),
    )
    by_key = {p["header_norm"]: p for p in presets}
    preset_base = None
    for col in columns:
        key = _preset_key(col.get("header"))
        preset = by_key.get(key)
        if not preset:
            continue
        col["price_type"] = preset["price_type"]
        col["markup_pct"] = float(preset["markup_pct"] or 0)
        col["is_base"] = bool(preset["is_base"])
        if preset["is_base"] and preset_base is None:
            preset_base = col
    if preset_base is not None:
        for col in columns:
            col["is_base"] = col is preset_base
        return
    bases = [c for c in columns if c.get("is_base")]
    if not bases:
        for col in columns:
            if col.get("price_type") != "ignore":
                col["is_base"] = True
                break
    elif len(bases) > 1:
        for col in bases[1:]:
            col["is_base"] = False


def process_price_list(price_list_id):
    """Разбор/применение прайса + автоответ отправителю письма."""
    try:
        result = _process_price_list(price_list_id)
    except Exception as exc:
        traceback.print_exc()
        db.execute(
            "UPDATE price_lists SET status = 'failed', error = %s WHERE id = %s",
            (str(exc)[:2000], price_list_id),
        )
        result = {"ok": False, "error": str(exc)[:500]}
    try:
        price_reply.reply_for_price_list(price_list_id, result)
    except Exception:
        traceback.print_exc()


def _process_price_list(price_list_id):
    db.execute("UPDATE price_lists SET status='parsing', error=NULL WHERE id=%s", (price_list_id,))
    rec = db.query("SELECT * FROM price_lists WHERE id=%s", (price_list_id,))[0]
    # прайс ТД РЕКС (rex.moscow) обрабатывается специализированным контуром
    if rec.get("supplier_id"):
        sup = db.query("SELECT slug FROM suppliers WHERE id=%s", (rec["supplier_id"],))
        if sup and sup[0]["slug"] == rex_price_service.REX_SUPPLIER_SLUG:
            return rex_price_service.process_price_list(price_list_id)
        if sup and sup[0]["slug"] == vilpe_price_service.VILPE_SUPPLIER_SLUG:
            return vilpe_price_service.process_price_list(price_list_id)

    parsed = price_parser.parse_file(rec["stored_path"])
    columns = parsed["columns"]
    rows = parsed["rows"]
    _apply_column_presets(columns, rec.get("supplier_id"))
    base_col = next((c for c in columns if c.get("is_base")), columns[0] if columns else None)

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM price_list_columns WHERE price_list_id = %s", (price_list_id,))
            cur.execute("DELETE FROM price_list_items WHERE price_list_id = %s", (price_list_id,))
            for col in columns:
                cur.execute(
                    "INSERT INTO price_list_columns (price_list_id, col_index, header, price_type, "
                    "markup_pct, is_base, volume_min, volume_max, currency, unit) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        price_list_id, col["col_index"], col["header"], col["price_type"],
                        col.get("markup_pct") or 0, bool(col["is_base"]),
                        col["volume_min"], col["volume_max"], col["currency"], col["unit"],
                    ),
                )
            for row in rows:
                prices = row["prices"]
                base_price = prices.get(str(base_col["col_index"])) if base_col else None
                raw_extra = dict(row["raw_extra"] or {})
                if row.get("section"):
                    raw_extra["Раздел"] = row["section"]
                cur.execute(
                    "INSERT INTO price_list_items (price_list_id, row_num, raw_name, raw_sku, "
                    "raw_unit, raw_price, raw_currency, raw_extra, normalized_name, prices, "
                    "raw_row, match_status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'RUB', %s, %s, %s, %s, 'pending')",
                    (
                        price_list_id, row["row_num"], row["raw_name"], row["raw_sku"],
                        row["raw_unit"], base_price,
                        json.dumps(raw_extra, ensure_ascii=False),
                        price_matcher.norm(row["raw_name"]),
                        json.dumps(prices, ensure_ascii=False),
                        json.dumps(row["raw_row"], ensure_ascii=False),
                    ),
                )
            meta = dict(parsed.get("meta") or {})
            meta["price_columns"] = len(columns)
            cur.execute(
                "UPDATE price_lists SET rows_total = %s, meta = %s, status = 'matching' WHERE id = %s",
                (len(rows), json.dumps(meta, ensure_ascii=False), price_list_id),
            )
        conn.commit()
    run_matching(price_list_id)
    final = db.query(
        "SELECT status, rows_total, rows_matched FROM price_lists WHERE id = %s",
        (price_list_id,),
    )[0]
    return {
        "ok": True,
        "auto_applied": False,
        "status": final.get("status"),
        "rows": final.get("rows_total") or 0,
        "matched": final.get("rows_matched") or 0,
    }


def run_matching(price_list_id, only_open=False):
    rec = db.query("SELECT * FROM price_lists WHERE id = %s", (price_list_id,))[0]
    supplier = None
    if rec.get("supplier_id"):
        rows = db.query("SELECT id, name, amalor_slug FROM suppliers WHERE id = %s", (rec["supplier_id"],))
        supplier = rows[0] if rows else None
    where = "WHERE price_list_id = %s"
    if only_open:
        where += " AND match_status IN ('pending', 'auto')"
    items = db.query(
        "SELECT id, raw_name, raw_sku, prices FROM price_list_items "
        + where + " ORDER BY row_num",
        (price_list_id,),
    )
    with db.connect() as conn:
        with conn.cursor() as cur:
            for item in items:
                prices = item.get("prices") or {}
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if not any(v is not None for v in prices.values()):
                    cur.execute(
                        "UPDATE price_list_items SET matched_product_id = NULL, "
                        "match_score = NULL, match_method = 'no_price', match_status = 'no_price', "
                        "candidates = '[]'::jsonb, product_sku = NULL, product_name = NULL, "
                        "matched_at = NULL, note = 'нет цены в прайсе' WHERE id = %s",
                        (item["id"],),
                    )
                    continue
                product_id, score, method, status, cands = price_matcher.match_item(conn, item, supplier)
                best = cands[0] if cands else None
                cur.execute(
                    "UPDATE price_list_items SET matched_product_id = %s, match_score = %s, "
                    "match_method = %s, match_status = %s, candidates = %s, matched_at = now(), "
                    "product_sku = %s, product_name = %s WHERE id = %s",
                    (
                        product_id, score, method, status,
                        json.dumps(cands, ensure_ascii=False),
                        ((best.get("manufacturer_sku") or best.get("sku")) if best else None),
                        best.get("name") if best else None,
                        item["id"],
                    ),
                )
        conn.commit()
    recalc(price_list_id)
    db.execute("UPDATE price_lists SET status = 'review' WHERE id = %s", (price_list_id,))


def recalc(price_list_id):
    db.execute(
        "UPDATE price_lists SET "
        " rows_total = (SELECT count(*) FROM price_list_items WHERE price_list_id = %s), "
        " rows_matched = (SELECT count(*) FROM price_list_items WHERE price_list_id = %s "
        "                 AND matched_product_id IS NOT NULL), "
        " rows_approved = (SELECT count(*) FROM price_list_items WHERE price_list_id = %s "
        "                  AND match_status IN ('approved', 'applied', 'kept')), "
        " rows_rejected = (SELECT count(*) FROM price_list_items WHERE price_list_id = %s "
        "                  AND match_status = 'rejected') "
        "WHERE id = %s",
        (price_list_id, price_list_id, price_list_id, price_list_id, price_list_id),
    )


def update_columns(price_list_id, columns):
    rec = db.query("SELECT supplier_id FROM price_lists WHERE id = %s", (price_list_id,))
    if not rec:
        raise ValueError("Задача не найдена")
    supplier_id = rec[0]["supplier_id"]
    existing = {
        row["col_index"]: row
        for row in db.query(
            "SELECT col_index, header, volume_min, volume_max, unit "
            "FROM price_list_columns WHERE price_list_id = %s",
            (price_list_id,),
        )
    }
    clean = []
    base_count = 0
    for col in columns:
        try:
            col_index = int(col.get("col_index"))
        except (TypeError, ValueError):
            raise ValueError("Некорректная колонка")
        ptype = (col.get("price_type") or "input").strip()
        if ptype not in ("input", "retail", "mrc", "ignore"):
            raise ValueError("Некорректный тип цены")
        try:
            markup = float(col.get("markup_pct") or 0)
        except (TypeError, ValueError):
            raise ValueError("Некорректная наценка")
        if markup < -100 or markup > 1000:
            raise ValueError("Наценка должна быть от -100 до 1000 %")
        is_base = bool(col.get("is_base"))
        if is_base and ptype != "ignore":
            base_count += 1
        clean.append((col_index, ptype, is_base, markup))
    if base_count != 1:
        raise ValueError("Нужно выбрать ровно одну основную цену")
    with db.connect() as conn:
        with conn.cursor() as cur:
            for col_index, ptype, is_base, markup in clean:
                cur.execute(
                    "UPDATE price_list_columns SET price_type = %s, is_base = %s, markup_pct = %s "
                    "WHERE price_list_id = %s AND col_index = %s",
                    (ptype, is_base, markup, price_list_id, col_index),
                )
                row = existing.get(col_index)
                if supplier_id and row:
                    header_norm = _preset_key(row["header"])
                    if not header_norm:
                        continue
                    cur.execute(
                        "INSERT INTO price_column_presets "
                        "(supplier_id, header_norm, header, price_type, markup_pct, is_base, "
                        " volume_min, volume_max, unit) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (supplier_id, header_norm) DO UPDATE SET "
                        " header = EXCLUDED.header, price_type = EXCLUDED.price_type, "
                        " markup_pct = EXCLUDED.markup_pct, is_base = EXCLUDED.is_base, "
                        " volume_min = EXCLUDED.volume_min, volume_max = EXCLUDED.volume_max, "
                        " unit = EXCLUDED.unit, updated_at = now()",
                        (supplier_id, header_norm, row["header"], ptype, markup, is_base,
                         row["volume_min"], row["volume_max"], row["unit"]),
                    )
                    if is_base:
                        cur.execute(
                            "UPDATE price_column_presets SET is_base = false, updated_at = now() "
                            "WHERE supplier_id = %s AND header_norm <> %s",
                            (supplier_id, header_norm),
                        )
        conn.commit()
    return {"ok": True, "remembered": bool(supplier_id)}


def approve_auto(price_list_id):
    n = db.execute(
        "UPDATE price_list_items SET match_status = 'approved' "
        "WHERE price_list_id = %s AND match_status = 'auto'",
        (price_list_id,),
    )
    recalc(price_list_id)
    return {"ok": True, "count": n}


def update_item(price_list_id, item_id, product_id=None, status=None,
                manual_price=None, set_manual=False):
    item = db.query(
        "SELECT * FROM price_list_items WHERE id = %s AND price_list_id = %s",
        (item_id, price_list_id),
    )
    if not item:
        raise ValueError("Строка не найдена")
    fields = []
    params = []
    if product_id is not None:
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            raise ValueError("Некорректный товар")
        prod = db.query(
            "SELECT id, sku, manufacturer_sku, name FROM products WHERE id = %s", (product_id,)
        )
        if not prod:
            raise ValueError("Товар не найден")
        fields += ["matched_product_id = %s", "match_method = 'manual'", "match_score = 1.0",
                   "matched_at = now()", "product_sku = %s", "product_name = %s",
                   "applied_price = NULL", "applied_input_price = NULL", "applied_at = NULL"]
        params += [product_id, prod[0]["manufacturer_sku"] or prod[0]["sku"], prod[0]["name"]]
        status = status or "approved"
        rec = db.query(
            "SELECT pl.supplier_id, i.raw_name FROM price_list_items i "
            "JOIN price_lists pl ON pl.id = i.price_list_id WHERE i.id = %s",
            (item_id,),
        )
        if rec and rec[0]["supplier_id"]:
            db.execute(
                "INSERT INTO mapping_memory (supplier_id, raw_name_norm, product_id) "
                "VALUES (%s, %s, %s) ON CONFLICT (supplier_id, raw_name_norm) "
                "DO UPDATE SET product_id = EXCLUDED.product_id, "
                "hit_count = mapping_memory.hit_count + 1",
                (rec[0]["supplier_id"], price_matcher.norm(rec[0]["raw_name"]), product_id),
            )
    if set_manual:
        if manual_price in (None, ""):
            fields.append("manual_price = NULL")
        else:
            try:
                value = float(manual_price)
            except (TypeError, ValueError):
                raise ValueError("Некорректная ручная цена")
            if value <= 0:
                raise ValueError("Ручная цена должна быть больше 0")
            fields.append("manual_price = %s")
            params.append(round(value, 2))
    if status is not None:
        if status not in ("pending", "auto", "approved", "rejected"):
            raise ValueError("Некорректный статус")
        fields.append("match_status = %s")
        params.append(status)
        if status == "pending":
            fields.append("matched_product_id = NULL")
            fields.append("product_sku = NULL")
            fields.append("product_name = NULL")
    if not fields:
        raise ValueError("Нет изменений")
    params.append(item_id)
    db.execute(
        "UPDATE price_list_items SET " + ", ".join(fields) + " WHERE id = %s", tuple(params)
    )
    recalc(price_list_id)
    return {"ok": True}


def apply_price_list(price_list_id, include_auto=False, include_applied=False):
    rec = db.query("SELECT * FROM price_lists WHERE id = %s", (price_list_id,))
    if not rec:
        raise ValueError("Задача не найдена")
    rec = rec[0]
    columns = db.query(
        "SELECT col_index, header, price_type, markup_pct, is_base FROM price_list_columns "
        "WHERE price_list_id = %s ORDER BY col_index",
        (price_list_id,),
    )
    base = next((c for c in columns if c["is_base"]), None)
    if not base:
        raise ValueError("Не выбрана основная цена")
    base_col = str(base["col_index"])

    items = db.query(
        "SELECT i.*, p.price AS old_price, p.name AS base_name "
        "FROM price_list_items i JOIN products p ON p.id = i.matched_product_id "
        "WHERE i.price_list_id = %s AND i.matched_product_id IS NOT NULL "
        "AND (i.match_status = 'approved' OR (%s AND i.match_status = 'auto') "
        "     OR (%s AND i.match_status = 'applied')) "
        "ORDER BY i.row_num",
        (price_list_id, include_auto, include_applied),
    )
    if not items:
        raise ValueError("Нет утверждённых строк для применения")

    markup = float(base.get("markup_pct") or 0)
    applied = 0
    kept = 0
    skipped = 0
    site_updates = 0
    ping_site_ids = set()
    site_changes = {}
    site_meta = {}
    with db.connect() as conn:
        with conn.cursor() as cur:
            for item in items:
                prices = item.get("prices") or {}
                if isinstance(prices, str):
                    prices = json.loads(prices)
                raw = prices.get(base_col)
                raw_value = float(raw) if raw is not None else None
                manual = item.get("manual_price")
                input_price = None
                markup_pct = None
                if manual is None and (raw_value is None or raw_value == 0):
                    old_price = item.get("old_price")
                    if old_price is not None and float(old_price) != 0:
                        cur.execute(
                            "UPDATE price_list_items SET match_status = 'kept', "
                            "applied_price = %s, note = 'цена не менялась: в прайсе нет цены' "
                            "WHERE id = %s",
                            (old_price, item["id"]),
                        )
                        kept += 1
                    else:
                        skipped += 1
                    continue
                if manual is not None:
                    retail = float(manual)
                    if raw_value not in (None, 0) and base["price_type"] == "input":
                        input_price = raw_value
                    ptype = "retail"
                    source = "price_list_manual"
                elif base["price_type"] == "input":
                    markup_pct = markup
                    input_price = raw_value
                    retail = round(raw_value * (1 + markup / 100.0), 2)
                    ptype = "retail"
                    source = "price_list_input"
                elif base["price_type"] == "mrc":
                    retail = raw_value
                    ptype = "mrc"
                    source = "price_list_mrc"
                else:
                    retail = raw_value
                    ptype = "retail"
                    source = "price_list_retail"

                product_id = item["matched_product_id"]
                cur.execute(
                    "UPDATE products SET price = %s, price_updated_at = now(), "
                    "price_source = %s, price_type = %s, input_price = %s, "
                    "input_markup_pct = %s, "
                    "mrc_price = CASE WHEN %s = 'mrc' THEN %s ELSE NULL END "
                    "WHERE id = %s",
                    (retail, source, ptype, input_price, markup_pct, ptype, retail, product_id),
                )
                cur.execute(
                    "INSERT INTO price_changes (product_id, old_price, new_price, input_price, "
                    "markup_pct, source, price_list_item_id, applied, reason, changed_by, "
                    "source_updated_at, price_type) "
                    "VALUES (%s, %s, %s, %s, %s, 'price_list', %s, true, 'price_list', 'ui', "
                    "now(), %s)",
                    (product_id, item.get("old_price"), retail, input_price, markup_pct,
                     item["id"], ptype),
                )
                if rec.get("supplier_id"):
                    cur.execute(
                        "INSERT INTO mapping_memory (supplier_id, raw_name_norm, product_id) "
                        "VALUES (%s, %s, %s) "
                        "ON CONFLICT (supplier_id, raw_name_norm) "
                        "DO UPDATE SET product_id = EXCLUDED.product_id, "
                        "hit_count = mapping_memory.hit_count + 1",
                        (rec["supplier_id"], price_matcher.norm(item["raw_name"]), product_id),
                    )
                    cur.execute(
                        "INSERT INTO supplier_products (supplier_id, product_id, product_type, source) "
                        "VALUES (%s, %s, 'price_list', 'price_list') "
                        "ON CONFLICT (supplier_id, product_id, product_type) DO NOTHING",
                        (rec["supplier_id"], product_id),
                    )
                cur.execute(
                    "UPDATE price_list_items SET match_status = 'applied', applied_price = %s, "
                    "applied_input_price = %s, applied_at = now() WHERE id = %s",
                    (retail, input_price, item["id"]),
                )
                applied += 1

                cur.execute(
                    "SELECT s.*, max(ps.current_price) AS site_current_price "
                    "FROM sites s JOIN product_sites ps ON ps.site_id = s.id "
                    "WHERE ps.product_id = %s AND s.sync_enabled = true AND s.active = true "
                    "GROUP BY s.id",
                    (product_id,),
                )
                for site in cur.fetchall():
                    ping_site_ids.add(site["id"])
                    new_site_price = price_engine.compute_site_price(
                        retail, site, product_id, price_type=ptype,
                        mrc_price=retail if ptype == "mrc" else None,
                    )
                    if new_site_price is None:
                        continue
                    old_site_price = site.get("site_current_price")
                    if old_site_price is None:
                        old_site_price = item.get("old_price")
                    site_meta[site["id"]] = (site["name"], site.get("domain"))
                    try:
                        different = (old_site_price is None
                                     or float(old_site_price) != float(new_site_price))
                    except (TypeError, ValueError):
                        different = True
                    if different:
                        site_changes[site["id"]] = site_changes.get(site["id"], 0) + 1
                    cur.execute(
                        "UPDATE site_updates SET old_price = COALESCE(old_price, %s), "
                        "new_price = %s, currency = %s, source_updated_at = now(), error = NULL, "
                        "version = nextval('site_update_version_seq') "
                        "WHERE site_id = %s AND product_id = %s AND status = 'pending'",
                        (old_site_price, new_site_price, site.get("currency") or "RUB",
                         site["id"], product_id),
                    )
                    if cur.rowcount == 0:
                        cur.execute(
                            "INSERT INTO site_updates (site_id, product_id, old_price, new_price, "
                            "currency, status, source_updated_at) "
                            "VALUES (%s, %s, %s, %s, 'RUB', 'pending', now())",
                            (site["id"], product_id, old_site_price, new_site_price,
                             site.get("currency") or "RUB"),
                        )
                    # актуальная цена сайта для API (Amalor и др.), без очереди плагина
                    cur.execute(
                        "UPDATE product_sites SET current_price = %s, last_sync_at = now() "
                        "WHERE site_id = %s AND product_id = %s",
                        (new_site_price, site["id"], product_id),
                    )
                    site_updates += 1
        conn.commit()

    for site_id in ping_site_ids:
        ping_site(site_id)
    recalc(price_list_id)
    stats = db.query(
        "SELECT count(*) FILTER (WHERE match_status IN ('pending', 'auto')) AS open, "
        "count(*) FILTER (WHERE match_status IN ('applied', 'kept')) AS done "
        "FROM price_list_items WHERE price_list_id = %s",
        (price_list_id,),
    )[0]
    open_count = stats["open"]
    done_count = stats["done"]
    if open_count == 0:
        new_status = "applied"
    elif done_count > 0:
        new_status = "partial"
    else:
        new_status = "review"
    db.execute(
        "UPDATE price_lists SET status = %s, applied_at = now() WHERE id = %s",
        (new_status, price_list_id),
    )
    rec_filename = db.query("SELECT filename FROM price_lists WHERE id = %s", (price_list_id,))
    filename = rec_filename[0]["filename"] if rec_filename else "прайс"
    for sid, count in site_changes.items():
        if not count:
            continue
        name, domain = site_meta.get(sid, ("", ""))
        notifier.add_event(
            "price_update", name or domain,
            "Цены обновлены: %s" % (name or domain),
            "%s — %d поз. по прайсу «%s» (поставлено в очередь обновления сайта)."
            % (domain or name, count, filename),
            "price_update:%s:%s" % (price_list_id, sid),
        )
    return {"ok": True, "applied": applied, "kept": kept, "skipped": skipped,
            "open": open_count, "site_updates": site_updates, "status": new_status}


def recompute_site_queue(site_id):
    """Пересчитать ожидающие обновления сайта по текущим настройкам цен."""
    rows = db.query("SELECT * FROM sites WHERE id = %s", (site_id,))
    if not rows:
        raise ValueError("Сайт не найден")
    site = rows[0]
    updates = db.query(
        "SELECT su.id, su.product_id, p.price AS base_price, p.price_type, p.mrc_price "
        "FROM site_updates su JOIN products p ON p.id = su.product_id "
        "WHERE su.site_id = %s AND su.status = 'pending'",
        (site_id,),
    )
    changed = 0
    with db.connect() as conn:
        with conn.cursor() as cur:
            for update in updates:
                new_price = price_engine.compute_site_price(
                    update["base_price"], site, update["product_id"],
                    price_type=update.get("price_type"),
                    mrc_price=update.get("mrc_price"),
                )
                if new_price is None:
                    continue
                cur.execute(
                    "UPDATE site_updates SET new_price = %s, "
                    "version = nextval('site_update_version_seq'), "
                    "source_updated_at = now() WHERE id = %s",
                    (new_price, update["id"]),
                )
                cur.execute(
                    "UPDATE product_sites SET current_price = %s, last_sync_at = now() "
                    "WHERE site_id = %s AND product_id = %s",
                    (new_price, site_id, update["product_id"]),
                )
                changed += 1
        conn.commit()
    return changed


def ping_site(site_id):
    """Асинхронно пингуем WP-плагин, чтобы он сразу забрал обновления."""
    rows = db.query(
        "SELECT id, name, domain, base_url, api_key, remote_api_url "
        "FROM sites WHERE id = %s",
        (site_id,),
    )
    if not rows:
        return False
    site = rows[0]
    if not site.get("api_key"):
        return False
    url = (site.get("remote_api_url") or "").strip()
    if not url:
        base = (site.get("base_url") or ("https://" + (site.get("domain") or ""))).rstrip("/")
        if not base or not base.startswith(("http://", "https://")):
            return False
        url = base + "/wp-json/odinokov-base/v1/ping"

    def worker():
        try:
            request = urllib.request.Request(
                url,
                data=b"{}",
                method="POST",
                headers={
                    "X-Odinokov-Key": site["api_key"],
                    "Content-Type": "application/json",
                    "User-Agent": "AI-ENIX-Base/1.0",
                },
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                response.read(512)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()
    return True


def delete_price_list(price_list_id):
    rec = db.query("SELECT stored_path FROM price_lists WHERE id = %s", (price_list_id,))
    db.execute("DELETE FROM price_lists WHERE id = %s", (price_list_id,))
    if rec:
        try:
            Path(rec[0]["stored_path"]).unlink(missing_ok=True)
        except OSError:
            pass
    return {"ok": True}
