-- Логистика: адреса складов поставщиков + ускорение выборки по товарам
BEGIN;

CREATE TABLE IF NOT EXISTS supplier_addresses (
  id                SERIAL PRIMARY KEY,
  supplier_id       INT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
  portal_address_id BIGINT UNIQUE,
  label             TEXT,
  full_address      TEXT NOT NULL,
  city              TEXT,
  lat               DOUBLE PRECISION,
  lon               DOUBLE PRECISION,
  is_main           BOOLEAN NOT NULL DEFAULT false,
  source            TEXT DEFAULT 'portal'
);

CREATE INDEX IF NOT EXISTS supplier_addresses_supplier ON supplier_addresses(supplier_id);
CREATE INDEX IF NOT EXISTS supplier_products_product ON supplier_products(product_id);

COMMIT;
