-- Модуль «Прайсы»: колонки цен, кандидаты матчинга, типы цен.
-- Применять от роли base: psql -h 127.0.0.1 -U base -d base -f schema_prices.sql

BEGIN;

ALTER TABLE price_lists ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE price_lists ADD COLUMN IF NOT EXISTS rows_approved INT NOT NULL DEFAULT 0;
ALTER TABLE price_lists ADD COLUMN IF NOT EXISTS rows_rejected INT NOT NULL DEFAULT 0;
ALTER TABLE price_lists ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE price_lists ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;

ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS prices JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS raw_row JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS note TEXT;
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS unit TEXT;
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS product_sku TEXT;
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS product_name TEXT;
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS matched_at TIMESTAMPTZ;
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS manual_price NUMERIC(12,2);
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS applied_input_price NUMERIC(12,2);

ALTER TABLE products ADD COLUMN IF NOT EXISTS price_type TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS mrc_price NUMERIC(12,2);
ALTER TABLE products ADD COLUMN IF NOT EXISTS input_price NUMERIC(12,2);
ALTER TABLE products ADD COLUMN IF NOT EXISTS input_markup_pct NUMERIC(8,2);
ALTER TABLE price_changes ADD COLUMN IF NOT EXISTS price_type TEXT;
ALTER TABLE price_changes ADD COLUMN IF NOT EXISTS input_price NUMERIC(12,2);
ALTER TABLE price_changes ADD COLUMN IF NOT EXISTS markup_pct NUMERIC(8,2);

CREATE TABLE IF NOT EXISTS price_list_columns (
  id          SERIAL PRIMARY KEY,
  price_list_id INT NOT NULL REFERENCES price_lists(id) ON DELETE CASCADE,
  col_index   INT NOT NULL,
  header      TEXT,
  price_type  TEXT NOT NULL DEFAULT 'input',
  markup_pct  NUMERIC(8,2) NOT NULL DEFAULT 0,
  is_base     BOOLEAN NOT NULL DEFAULT false,
  volume_min  NUMERIC(12,2),
  volume_max  NUMERIC(12,2),
  currency    TEXT NOT NULL DEFAULT 'RUB',
  unit        TEXT,
  UNIQUE (price_list_id, col_index)
);
ALTER TABLE price_list_columns ADD COLUMN IF NOT EXISTS markup_pct NUMERIC(8,2) NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS price_list_columns_list ON price_list_columns(price_list_id);

CREATE TABLE IF NOT EXISTS price_column_presets (
  id            SERIAL PRIMARY KEY,
  supplier_id   INT REFERENCES suppliers(id) ON DELETE CASCADE,
  header_norm   TEXT NOT NULL,
  header        TEXT,
  price_type    TEXT NOT NULL DEFAULT 'input',
  markup_pct    NUMERIC(8,2) NOT NULL DEFAULT 0,
  is_base       BOOLEAN NOT NULL DEFAULT false,
  volume_min    NUMERIC(12,2),
  volume_max    NUMERIC(12,2),
  unit          TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (supplier_id, header_norm)
);

CREATE INDEX IF NOT EXISTS price_lists_status ON price_lists(status);
CREATE INDEX IF NOT EXISTS price_lists_supplier ON price_lists(supplier_id, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS pli_list_status ON price_list_items(price_list_id, match_status);

COMMIT;
