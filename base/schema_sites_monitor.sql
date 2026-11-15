-- Мониторинг сайтов (2026-10-29)
BEGIN;

ALTER TABLE sites ADD COLUMN IF NOT EXISTS supplier_id INT REFERENCES suppliers(id) ON DELETE SET NULL;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS remote_products INT;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS remote_products_at TIMESTAMPTZ;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS price_random_min_pct NUMERIC(6,4) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS site_checks (
  id          BIGSERIAL PRIMARY KEY,
  site_id     INT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  ok          BOOLEAN NOT NULL,
  status_code INT,
  ttfb_ms     INT,
  total_ms    INT,
  error       TEXT
);

CREATE INDEX IF NOT EXISTS site_checks_site_time ON site_checks(site_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS site_checks_time ON site_checks(checked_at DESC);

COMMIT;
