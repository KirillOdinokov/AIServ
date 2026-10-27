ALTER TABLE products ADD COLUMN IF NOT EXISTS amalor_id BIGINT UNIQUE;
ALTER TABLE products ADD COLUMN IF NOT EXISTS category_id BIGINT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS brand_id BIGINT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS brand TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS manufacturer TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS supplier_slug TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS supplier_url TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS price NUMERIC(12,2);
ALTER TABLE products ADD COLUMN IF NOT EXISTS price_unit TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS in_stock TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_published BOOLEAN;
ALTER TABLE products ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'amalor';
ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding vector(1024);

CREATE INDEX IF NOT EXISTS products_embedding_hnsw
  ON products USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS products_supplier_slug ON products(supplier_slug);
CREATE INDEX IF NOT EXISTS products_amalor_id ON products(amalor_id);

ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS slug TEXT UNIQUE;
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS amalor_slug TEXT UNIQUE;
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS portal_id INT UNIQUE;
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS role TEXT;
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS shipping TEXT;
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS website TEXT;
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS source TEXT;

CREATE TABLE IF NOT EXISTS categories (
  id SERIAL PRIMARY KEY,
  amalor_id BIGINT UNIQUE,
  name TEXT NOT NULL,
  slug TEXT,
  parent_id BIGINT,
  source TEXT
);

CREATE TABLE IF NOT EXISTS brands (
  id SERIAL PRIMARY KEY,
  amalor_id BIGINT UNIQUE,
  name TEXT NOT NULL,
  slug TEXT,
  source TEXT
);

CREATE TABLE IF NOT EXISTS supplier_products (
  id SERIAL PRIMARY KEY,
  supplier_id INT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
  product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  product_type TEXT DEFAULT 'main',
  price NUMERIC(12,2),
  price_unit TEXT,
  supplier_url TEXT,
  in_stock TEXT,
  supplier_sku TEXT,
  source TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (supplier_id, product_id, product_type)
);

CREATE TABLE IF NOT EXISTS supplier_aliases (
  id SERIAL PRIMARY KEY,
  alias TEXT NOT NULL UNIQUE,
  supplier_id INT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
  source TEXT
);
