-- Схема сервиса «База» (PostgreSQL 18 + pgvector + pg_trgm)
-- Расширения vector/pg_trgm устанавливаются один раз администратором (см. init_extensions.sql).
-- Применять от имени роли base: psql -h 127.0.0.1 -U base -d base -f schema.sql

CREATE TABLE IF NOT EXISTS suppliers (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  contact_email TEXT,
  notes         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sites (
  id               SERIAL PRIMARY KEY,
  name             TEXT NOT NULL,
  domain           TEXT NOT NULL UNIQUE,
  platform         TEXT NOT NULL DEFAULT 'woocommerce',
  base_url         TEXT,
  wp_user          TEXT,
  wp_app_password  TEXT,
  wc_key           TEXT,
  wc_secret        TEXT,
  plugin_key       TEXT,
  active           BOOLEAN NOT NULL DEFAULT true,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
  id              SERIAL PRIMARY KEY,
  sku             TEXT UNIQUE,
  name            TEXT NOT NULL,
  normalized_name TEXT,
  description     TEXT,
  category        TEXT,
  unit            TEXT,
  weight_kg       NUMERIC(10,3),
  dim_length_mm   NUMERIC(10,1),
  dim_width_mm    NUMERIC(10,1),
  dim_height_mm   NUMERIC(10,1),
  volume_m3       NUMERIC(10,4),
  packaging       TEXT,
  min_order       NUMERIC(10,2),
  lead_time_days  INT,
  attributes      JSONB NOT NULL DEFAULT '{}'::jsonb,
  archived        BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS products_name_trgm ON products USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS products_norm_trgm ON products USING gin (normalized_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS product_sites (
  id              SERIAL PRIMARY KEY,
  product_id      INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  site_id         INT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  site_product_id TEXT,
  site_sku        TEXT,
  current_price   NUMERIC(12,2),
  currency        TEXT DEFAULT 'RUB',
  last_sync_at    TIMESTAMPTZ,
  sync_status     TEXT,
  UNIQUE (site_id, site_product_id)
);
CREATE INDEX IF NOT EXISTS product_sites_product ON product_sites(product_id);
CREATE INDEX IF NOT EXISTS product_sites_sku ON product_sites(site_sku);

CREATE TABLE IF NOT EXISTS emails (
  id              SERIAL PRIMARY KEY,
  message_id      TEXT UNIQUE,
  mailbox         TEXT,
  from_addr       TEXT,
  subject         TEXT,
  received_at     TIMESTAMPTZ,
  has_attachments BOOLEAN DEFAULT false,
  body_text       TEXT,
  kind            TEXT,
  status          TEXT NOT NULL DEFAULT 'new',
  processed_at    TIMESTAMPTZ,
  notes           TEXT
);

CREATE TABLE IF NOT EXISTS price_lists (
  id          SERIAL PRIMARY KEY,
  supplier_id INT REFERENCES suppliers(id) ON DELETE SET NULL,
  filename    TEXT,
  stored_path TEXT,
  file_type   TEXT,
  source      TEXT NOT NULL DEFAULT 'upload',
  email_id    INT REFERENCES emails(id) ON DELETE SET NULL,
  status      TEXT NOT NULL DEFAULT 'new',
  rows_total  INT DEFAULT 0,
  rows_matched INT DEFAULT 0,
  notes       TEXT,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  parsed_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS price_list_items (
  id                 SERIAL PRIMARY KEY,
  price_list_id      INT NOT NULL REFERENCES price_lists(id) ON DELETE CASCADE,
  row_num            INT,
  raw_name           TEXT NOT NULL,
  raw_sku            TEXT,
  raw_price          NUMERIC(12,2),
  raw_currency       TEXT,
  raw_unit           TEXT,
  raw_extra          JSONB NOT NULL DEFAULT '{}'::jsonb,
  normalized_name    TEXT,
  matched_product_id INT REFERENCES products(id) ON DELETE SET NULL,
  match_score        NUMERIC(5,4),
  match_method       TEXT,
  match_status       TEXT NOT NULL DEFAULT 'pending',
  applied_price      NUMERIC(12,2),
  applied_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS pli_norm_trgm ON price_list_items USING gin (normalized_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS pli_status ON price_list_items(match_status);

CREATE TABLE IF NOT EXISTS price_changes (
  id                 SERIAL PRIMARY KEY,
  product_id         INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  site_id            INT REFERENCES sites(id) ON DELETE SET NULL,
  old_price          NUMERIC(12,2),
  new_price          NUMERIC(12,2),
  currency           TEXT DEFAULT 'RUB',
  source             TEXT NOT NULL,
  price_list_item_id INT REFERENCES price_list_items(id) ON DELETE SET NULL,
  review_id          INT,
  changed_by         TEXT,
  note               TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS price_changes_product ON price_changes(product_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reviews (
  id                 SERIAL PRIMARY KEY,
  price_list_item_id INT NOT NULL REFERENCES price_list_items(id) ON DELETE CASCADE,
  product_id         INT REFERENCES products(id) ON DELETE SET NULL,
  confidence         NUMERIC(5,4),
  method             TEXT,
  token              TEXT UNIQUE NOT NULL,
  status             TEXT NOT NULL DEFAULT 'pending',
  email_to           TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at        TIMESTAMPTZ,
  resolved_by        TEXT,
  note               TEXT
);
CREATE INDEX IF NOT EXISTS reviews_status ON reviews(status);

CREATE TABLE IF NOT EXISTS mapping_memory (
  id            SERIAL PRIMARY KEY,
  supplier_id   INT REFERENCES suppliers(id) ON DELETE CASCADE,
  raw_name_norm TEXT NOT NULL,
  product_id    INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  hit_count     INT NOT NULL DEFAULT 0,
  UNIQUE (supplier_id, raw_name_norm)
);

CREATE TABLE IF NOT EXISTS site_updates (
  id         SERIAL PRIMARY KEY,
  site_id    INT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  new_price  NUMERIC(12,2),
  currency   TEXT DEFAULT 'RUB',
  status     TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at    TIMESTAMPTZ,
  acked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS site_updates_pending ON site_updates(site_id, status);

CREATE TABLE IF NOT EXISTS email_attachments (
  id            SERIAL PRIMARY KEY,
  email_id      INT NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
  filename      TEXT,
  stored_path   TEXT,
  file_type     TEXT,
  price_list_id INT REFERENCES price_lists(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id          SERIAL PRIMARY KEY,
  title       TEXT,
  source_type TEXT,
  source_path TEXT,
  site_id     INT REFERENCES sites(id) ON DELETE SET NULL,
  meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunks (
  id          SERIAL PRIMARY KEY,
  document_id INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  content     TEXT NOT NULL,
  embedding   vector(1024),
  meta        JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS document_chunks_embedding
  ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id         SERIAL PRIMARY KEY,
  site_id    INT REFERENCES sites(id) ON DELETE SET NULL,
  title      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id         SERIAL PRIMARY KEY,
  session_id INT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_session ON chat_messages(session_id, created_at);
