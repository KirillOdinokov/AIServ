"""Сопоставление строк прайса с товарами Базы."""
import re

GENERIC = re.compile(
    r"\b(стеклоткань|стеклопластик|стеклолента|стеклосетка|полотно|рулонный|"
    r"ткань|черная|чёрная|стекло|материал)\b",
    re.IGNORECASE,
)
MODEL_RE = re.compile(r"[а-яa-z]{1,6}[\s\-/]?\d+(?:[\-/]\d+)*[а-яa-z0-9]*", re.IGNORECASE)


def norm(text):
    s = (text or "").lower().replace("ё", "е")
    s = s.replace("®", " ").replace("™", " ")
    s = re.sub(r"[^a-zа-я0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_alt(text):
    """Вариант для «Эз»/«Black»: эз→э3, black→черная."""
    return norm(text).replace("эз", "э3").replace("black", "черная")


def norm_no_v(text):
    """Убрать суффикс «в» у числовых токенов: 200в→200."""
    s = norm_alt(text)
    return re.sub(r"(?<!\w)(\d+)в(?!\w)", r"\1", s)


def tokens(text):
    return [t for t in norm(text).split() if t]


def variants(name):
    base = norm(name)
    out = []
    for value in (
        base,
        norm(re.sub(r"\([^)]*\)", " ", name or "")),
        norm(GENERIC.sub(" ", base)),
        norm(GENERIC.sub(" ", re.sub(r"\([^)]*\)", " ", name or ""))),
    ):
        if value and value not in out:
            out.append(value)
    if any(x in base for x in ("black", "черн", "эз")):
        for value in (norm_alt(name), norm_no_v(name)):
            if value and value not in out:
                out.append(value)
    return out or [base]


def weighted_variants(name):
    """Варианты названия с весом: полное название важнее, чем без скобок."""
    base = norm(name)
    out = []

    def add(value, weight):
        if value and all(existing != value for existing, _ in out):
            out.append((value, weight))

    add(base, 1.0)
    add(norm(re.sub(r"\([^)]*\)", " ", name or "")), 0.94)
    add(norm(GENERIC.sub(" ", base)), 0.9)
    add(norm(GENERIC.sub(" ", re.sub(r"\([^)]*\)", " ", name or ""))), 0.86)
    if any(x in base for x in ("black", "черн", "эз")):
        add(norm_alt(name), 0.96)
        add(norm_no_v(name), 0.94)
    return out or [(base, 1.0)]


def _subset(short, long):
    a, b = set(tokens(short)), set(tokens(long))
    return bool(a) and a.issubset(b)


def _contains_sequence(long_tokens, short_tokens):
    if not short_tokens or len(short_tokens) > len(long_tokens):
        return False
    n = len(short_tokens)
    for i in range(len(long_tokens) - n + 1):
        if long_tokens[i:i + n] == short_tokens:
            return True
    return False


def _add(cands, row, score, method):
    pid = row["id"]
    prev = cands.get(pid)
    rank = float(score)
    item = {
        "id": pid,
        "product_id": pid,
        "sku": row.get("sku"),
        "manufacturer_sku": row.get("manufacturer_sku"),
        "name": row.get("name"),
        "price": float(row["price"]) if row.get("price") is not None else None,
        "supplier_slug": row.get("supplier_slug"),
        "score": round(min(1.0, rank), 4),
        "rank": round(rank, 4),
        "method": method,
    }
    if not prev or item["rank"] > prev["rank"]:
        cands[pid] = item


def _bonus(row, supplier):
    score = 0.0
    same = bool(supplier and supplier.get("amalor_slug")
                and row.get("supplier_slug") == supplier["amalor_slug"])
    if same:
        score += 0.02
    if row.get("price") is not None:
        score += 0.04
        if same:
            score += 0.12
    if row.get("is_published"):
        score += 0.02
    if row.get("manufacturer_sku"):
        score += 0.02
    return score


def _dedupe_candidates(cands):
    """Оставить по одному каноническому товару на одинаковое название."""
    groups = {}
    for cand in cands.values():
        key = norm(cand.get("name") or "")
        if not key:
            key = "id:%s" % cand["product_id"]
        groups.setdefault(key, []).append(cand)
    result = {}
    for group in groups.values():
        group.sort(
            key=lambda c: (
                c.get("rank", 0), c.get("price") is not None,
                c.get("manufacturer_sku") is not None, -c.get("product_id", 0),
            ),
            reverse=True,
        )
        best = dict(group[0])
        best["duplicate_count"] = len(group) - 1
        if len(group) > 1:
            best["duplicate_ids"] = [c["product_id"] for c in group[1:]]
        result[best["product_id"]] = best
    return result


def _search(conn, sql, params, cands, supplier, method):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        for row in cur.fetchall():
            sim = row.get("sim")
            score = float(sim) if sim is not None else 0.5
            if row.get("normalized_name") and row["normalized_name"] == params[0]:
                score = 1.0
            _add(cands, row, score + _bonus(row, supplier), method)


def match_item(conn, item, supplier):
    """Вернуть (product_id, score, method, status, candidates)."""
    raw_name = item.get("raw_name") or ""
    raw_sku = (item.get("raw_sku") or "").strip()
    supplier_id = supplier.get("id") if supplier else None

    # 1. Память сопоставлений
    if supplier_id:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.sku, p.manufacturer_sku, p.name, p.price, p.is_published, "
                "       p.supplier_slug "
                "FROM mapping_memory mm JOIN products p ON p.id = mm.product_id "
                "WHERE mm.supplier_id = %s AND mm.raw_name_norm = %s LIMIT 1",
                (supplier_id, norm(raw_name)),
            )
            row = cur.fetchone()
            if row:
                return row["id"], 1.0, "memory", "auto", [_candidate(row, 1.0, "memory")]

    # 2. Артикул
    if raw_sku:
        sku_norm = re.sub(r"[^a-zа-я0-9]", "", raw_sku.lower())
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, sku, manufacturer_sku, name, price, is_published, supplier_slug "
                "FROM products "
                "WHERE lower(sku) = lower(%s) OR lower(manufacturer_sku) = lower(%s) "
                "   OR manufacturer_sku_norm = %s LIMIT 5",
                (raw_sku, raw_sku, sku_norm),
            )
            rows = cur.fetchall()
            if len(rows) == 1:
                row = rows[0]
                return row["id"], 1.0, "sku", "auto", [_candidate(row, 1.0, "sku")]
            if rows:
                cands = {}
                for row in rows:
                    _add(cands, row, 0.95, "sku")
                cands = _dedupe_candidates(cands)
                ordered = sorted(cands.values(), key=lambda c: c["rank"], reverse=True)
                return None, ordered[0]["score"], "sku", "pending", ordered[:5]

    cands = {}
    with conn.cursor() as cur:
        cur.execute("SET LOCAL pg_trgm.similarity_threshold = 0.28")

    for v in variants(raw_name):
        _search(
            conn,
            "SELECT id, sku, manufacturer_sku, name, normalized_name, price, is_published, supplier_slug, "
            "       similarity(normalized_name, %s) AS sim "
            "FROM products WHERE normalized_name %% %s "
            "ORDER BY sim DESC, (price IS NOT NULL) DESC, id LIMIT 12",
            (v, v), cands, supplier, "trgm",
        )
        _search(
            conn,
            "SELECT id, sku, manufacturer_sku, name, normalized_name, price, is_published, supplier_slug, "
            "       similarity(normalized_name, %s) AS sim "
            "FROM products WHERE normalized_name LIKE %s "
            "ORDER BY sim DESC, (price IS NOT NULL) DESC, id LIMIT 12",
            (v, "%" + v + "%"), cands, supplier, "name",
        )
        for token in MODEL_RE.findall(v):
            token = token.strip()
            if len(token) < 3:
                continue
            _search(
                conn,
                "SELECT id, sku, manufacturer_sku, name, normalized_name, price, is_published, supplier_slug, "
                "       similarity(normalized_name, %s) AS sim "
                "FROM products WHERE normalized_name LIKE %s "
                "ORDER BY sim DESC, (price IS NOT NULL) DESC, id LIMIT 8",
                (v, "%" + token + "%"), cands, supplier, "token",
            )

    if not cands:
        return None, None, None, "pending", []

    # усиливаем кандидатов: все токены названия прайса (включая размер в скобках)
    price_token_variants = [(tokens(v), w) for v, w in weighted_variants(raw_name)]
    for cand in cands.values():
        cand_token_variants = [
            tokens(norm(cand["name"])),
            tokens(norm_alt(cand["name"])),
            tokens(norm_no_v(cand["name"])),
        ]
        best_base = 0.0
        for price_tokens, weight in price_token_variants:
            if not price_tokens:
                continue
            for cand_tokens in cand_token_variants:
                if not cand_tokens or not set(price_tokens).issubset(set(cand_tokens)):
                    continue
                base = (0.98 if _contains_sequence(cand_tokens, price_tokens) else 0.9) \
                    - (1.0 - weight)
                best_base = max(best_base, base)
        if not best_base:
            continue
        rank = max(cand["rank"], best_base + _bonus(cand, supplier))
        cand["rank"] = round(rank, 4)
        cand["score"] = round(min(1.0, rank), 4)
        cand["method"] = "token"

    cands = _dedupe_candidates(cands)
    ordered = sorted(
        cands.values(),
        key=lambda c: (
            c["rank"], c["price"] is not None, c["manufacturer_sku"] is not None,
            c["supplier_slug"] == (supplier or {}).get("amalor_slug"), -c["product_id"],
        ),
        reverse=True,
    )
    best = ordered[0]
    second = ordered[1] if len(ordered) > 1 else None
    best_score = best["score"]

    canonical_best = best["price"] is not None and best["manufacturer_sku"] is not None
    canonical_second = bool(second and second["price"] is not None and second["manufacturer_sku"] is not None)
    margin = best["rank"] - (second["rank"] if second else 0)
    if best_score >= 0.88 and (margin >= 0.03 or (canonical_best and not canonical_second)):
        return best["product_id"], best_score, best["method"], "auto", ordered[:5]

    return None, best_score, best["method"], "pending", ordered[:5]


def _candidate(row, score, method):
    return {
        "id": row["id"],
        "product_id": row["id"],
        "sku": row.get("sku"),
        "manufacturer_sku": row.get("manufacturer_sku"),
        "name": row.get("name"),
        "price": float(row["price"]) if row.get("price") is not None else None,
        "supplier_slug": row.get("supplier_slug"),
        "score": round(min(1.0, float(score)), 4),
        "rank": round(float(score), 4),
        "method": method,
    }


def search_products(conn, query, limit=50, supplier_slug=None, only_supplier=False):
    q = norm(query)
    if not q:
        if not supplier_slug:
            return []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, sku, manufacturer_sku, name, normalized_name, price, is_published, "
                "       supplier_slug "
                "FROM products WHERE supplier_slug = %s "
                "ORDER BY (price IS NOT NULL) DESC, name LIMIT %s",
                (supplier_slug, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r["id"], "product_id": r["id"], "sku": r["sku"],
                "manufacturer_sku": r["manufacturer_sku"], "name": r["name"],
                "price": float(r["price"]) if r["price"] is not None else None,
                "supplier_slug": r["supplier_slug"], "score": 0.0, "rank": 0.0,
            }
            for r in rows
        ]

    q_variants = []
    for value in (norm(query), norm_alt(query), norm_no_v(query)):
        if value and value not in q_variants:
            q_variants.append(value)

    supplier_filter = " AND supplier_slug = %s" if (only_supplier and supplier_slug) else ""
    rows = {}

    def remember(row):
        prev = rows.get(row["id"])
        if not prev or (row.get("sim") or 0) > (prev.get("sim") or 0):
            rows[row["id"]] = row

    with conn.cursor() as cur:
        cur.execute("SET LOCAL pg_trgm.similarity_threshold = 0.2")
        for qv in q_variants:
            params = [qv, qv]
            sql = (
                "SELECT id, sku, manufacturer_sku, name, normalized_name, price, is_published, "
                "       supplier_slug, similarity(normalized_name, %s) AS sim "
                "FROM products WHERE normalized_name %% %s" + supplier_filter +
                " ORDER BY sim DESC, (price IS NOT NULL) DESC, id LIMIT 30"
            )
            if supplier_filter:
                params.append(supplier_slug)
            cur.execute(sql, tuple(params))
            for row in cur.fetchall():
                remember(row)

            params = [qv, "%" + qv + "%"]
            sql = (
                "SELECT id, sku, manufacturer_sku, name, normalized_name, price, is_published, "
                "       supplier_slug, similarity(normalized_name, %s) AS sim "
                "FROM products WHERE normalized_name LIKE %s" + supplier_filter +
                " ORDER BY sim DESC, (price IS NOT NULL) DESC, id LIMIT 30"
            )
            if supplier_filter:
                params.append(supplier_slug)
            cur.execute(sql, tuple(params))
            for row in cur.fetchall():
                remember(row)

            toks = [t for t in tokens(qv) if len(t) >= 2][:8]
            if toks:
                where = " AND ".join(["normalized_name LIKE %s"] * len(toks))
                params = [qv] + ["%" + t + "%" for t in toks]
                if supplier_filter:
                    where += supplier_filter
                    params.append(supplier_slug)
                cur.execute(
                    "SELECT id, sku, manufacturer_sku, name, normalized_name, price, is_published, "
                    "       supplier_slug, similarity(normalized_name, %s) AS sim "
                    "FROM products WHERE " + where +
                    " ORDER BY (price IS NOT NULL) DESC, id LIMIT 30",
                    tuple(params),
                )
                for row in cur.fetchall():
                    remember(row)

    cands = {}
    for row in rows.values():
        name_variants = [norm(row["name"]), norm_alt(row["name"]), norm_no_v(row["name"])]
        score = float(row.get("sim") or 0)
        if norm(row["name"]) in q_variants:
            score = max(score, 0.99)
        for qv in q_variants:
            q_tok = tokens(qv)
            if not q_tok:
                continue
            for nv in name_variants:
                n_tok = tokens(nv)
                if set(q_tok).issubset(set(n_tok)):
                    base = 0.98 if _contains_sequence(n_tok, q_tok) else 0.9
                    score = max(score, base)
        if only_supplier and supplier_slug and row.get("supplier_slug") == supplier_slug:
            score += 0.05
        if row.get("price") is not None:
            score += 0.02
        if row.get("manufacturer_sku"):
            score += 0.01
        _add(cands, row, score, "search")

    cands = _dedupe_candidates(cands)
    preferred = supplier_slug if only_supplier else None
    ordered = sorted(
        cands.values(),
        key=lambda c: (
            c["rank"], c["price"] is not None, c["manufacturer_sku"] is not None,
            c["supplier_slug"] == preferred, -c["product_id"],
        ),
        reverse=True,
    )
    return ordered[:limit]
