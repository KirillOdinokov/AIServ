-- Артикулы: поставщика (из Amalor) + зеркало каталога сайта
BEGIN;

ALTER TABLE products ADD COLUMN IF NOT EXISTS manufacturer_sku TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS manufacturer_sku_norm TEXT;
CREATE INDEX IF NOT EXISTS products_manufacturer_sku_norm
  ON products(manufacturer_sku_norm);

CREATE TABLE IF NOT EXISTS site_products (
  id          SERIAL PRIMARY KEY,
  site_id     INT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  remote_id   BIGINT,
  sku         TEXT,
  sku_norm    TEXT,
  name        TEXT,
  name_norm   TEXT,
  price       NUMERIC(12,2),
  url         TEXT,
  product_id  INT REFERENCES products(id) ON DELETE SET NULL,
  matched_by  TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (site_id, remote_id)
);

CREATE INDEX IF NOT EXISTS site_products_product ON site_products(product_id);
CREATE INDEX IF NOT EXISTS site_products_sku ON site_products(site_id, sku_norm);

COMMIT;
