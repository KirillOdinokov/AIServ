-- Ручные соответствия товаров сайта и базы (переживают переимпорт)
BEGIN;
CREATE TABLE IF NOT EXISTS site_product_overrides (
  site_id     INT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  remote_id   BIGINT NOT NULL,
  product_id  INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (site_id, remote_id)
);
COMMIT;
