-- Перестройка связей товаров с поставщиками по итогам /base/review (2026-10-29)
-- supplier_matches — основная карта amalor_slug -> office supplier (может быть несколько).
-- Для slug'ов без связей — fallback на suppliers.amalor_slug.
-- Перестраиваются только строки source='amalor' (portal/manual/review сохраняются).

BEGIN;

CREATE TEMP TABLE sp_new ON COMMIT DROP AS
SELECT m.office_supplier_id AS supplier_id, p.id AS product_id,
       p.price, p.price_unit, p.supplier_url, p.in_stock
FROM products p
JOIN supplier_matches m ON m.amalor_slug = p.supplier_slug
UNION ALL
SELECT s.id, p.id, p.price, p.price_unit, p.supplier_url, p.in_stock
FROM products p
JOIN suppliers s ON s.amalor_slug = p.supplier_slug
WHERE NOT EXISTS (SELECT 1 FROM supplier_matches m WHERE m.amalor_slug = p.supplier_slug);

DELETE FROM supplier_products WHERE source = 'amalor';

INSERT INTO supplier_products
  (supplier_id, product_id, product_type, price, price_unit, supplier_url, in_stock, source)
SELECT DISTINCT ON (supplier_id, product_id)
  supplier_id, product_id, 'main', price, price_unit, supplier_url, in_stock, 'amalor'
FROM sp_new
ORDER BY supplier_id, product_id
ON CONFLICT (supplier_id, product_id, product_type) DO NOTHING;

COMMIT;
