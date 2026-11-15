"""Расчёт цены для сайта (derived: наценка + стабильный рандомайзер)."""
import hashlib


def _random_fraction(site_id, product_id):
    raw = ("%s:%s" % (site_id, product_id)).encode()
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:4], "big") / float(2 ** 32)


def compute_site_price(base_price, site, product_id, price_type=None, mrc_price=None):
    """Вернуть цену для сайта или None, если базовой цены нет."""
    if base_price is None:
        return None
    ptype = (price_type or "").strip().lower()
    if ptype == "mrc":
        # МРЦ — строго такая же цена, без наценок и округлений
        return round(float(mrc_price if mrc_price is not None else base_price), 2)
    price = float(base_price)
    mode = (site.get("price_mode") or "source").strip()
    if mode == "derived":
        price *= float(site.get("price_multiplier") or 1)
        rmin = float(site.get("price_random_min_pct") or 0) / 100.0
        rmax = float(site.get("price_random_pct") or 0) / 100.0
        if rmax > 0:
            if rmax < rmin:
                rmin, rmax = rmax, rmin
            price *= 1 + rmin + (rmax - rmin) * _random_fraction(site["id"], product_id)
    step = site.get("price_round")
    if step:
        step = float(step)
        if step > 0:
            price = round(price / step) * step
    if site.get("price_min") is not None:
        price = max(price, float(site["price_min"]))
    if site.get("price_max") is not None:
        price = min(price, float(site["price_max"]))
    return round(price, 2)
