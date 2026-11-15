DROP TABLE IF EXISTS stg_amalor_price_state;
CREATE TABLE stg_amalor_price_state (
    amalor_id bigint,
    current_price numeric,
    supplier_url text,
    last_seen_at timestamptz,
    changed_at timestamptz,
    changed_price numeric
);
\copy stg_amalor_price_state FROM '/tmp/amalor_price_state.csv' WITH (FORMAT csv, HEADER true)
CREATE INDEX stg_amalor_price_state_id ON stg_amalor_price_state (amalor_id);

UPDATE products p
SET price = s.current_price,
    price_updated_at = COALESCE(s.changed_at, s.last_seen_at),
    price_source = 'amalor',
    price_currency = 'RUB'
FROM stg_amalor_price_state s
WHERE p.amalor_id = s.amalor_id AND s.current_price IS NOT NULL;

UPDATE product_sites ps
SET current_price = p.price,
    price_updated_at = p.price_updated_at,
    last_sync_at = now(),
    sync_status = CASE WHEN p.price_updated_at IS NOT NULL THEN 'synced' ELSE ps.sync_status END
FROM products p
WHERE ps.product_id = p.id
  AND ps.site_id = (SELECT id FROM sites WHERE domain = 'amalor.ru')
  AND p.price IS NOT NULL;

SELECT count(*) AS products_total,
       count(*) FILTER (WHERE price > 0) AS priced_gt0,
       count(*) FILTER (WHERE price = 0) AS priced_zero,
       count(price_updated_at) AS with_date,
       count(*) FILTER (WHERE price_source = 'amalor') AS source_amalor,
       min(price_updated_at) AS min_date,
       max(price_updated_at) AS max_date
FROM products;

SELECT count(*) AS site_rows,
       count(current_price) AS with_price,
       count(price_updated_at) AS with_date,
       min(price_updated_at) AS min_date,
       max(price_updated_at) AS max_date
FROM product_sites
WHERE site_id = (SELECT id FROM sites WHERE domain = 'amalor.ru');

SELECT id, name, domain, platform, price_mode, price_priority, sync_enabled, currency,
       price_multiplier, price_random_pct,
       CASE WHEN api_key IS NULL THEN 'none' ELSE 'set' END AS api_key_state
FROM sites;

SELECT amalor_id, name, price, price_updated_at, price_source
FROM products WHERE price > 0 ORDER BY random() LIMIT 5;
