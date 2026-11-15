-- Публикация: задачи «Расширение ассортимента».
-- Применять от роли base: psql -h 127.0.0.1 -U base -d base -f schema_publication.sql

BEGIN;

CREATE TABLE IF NOT EXISTS publication_tasks (
  id            SERIAL PRIMARY KEY,
  type          TEXT NOT NULL DEFAULT 'assortment',
  title         TEXT,
  supplier_id   INT REFERENCES suppliers(id) ON DELETE SET NULL,
  price_list_id INT REFERENCES price_lists(id) ON DELETE SET NULL,
  status        TEXT NOT NULL DEFAULT 'new',
  items_total   INT NOT NULL DEFAULT 0,
  meta          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (type, price_list_id)
);
CREATE INDEX IF NOT EXISTS publication_tasks_type ON publication_tasks(type, created_at DESC);

CREATE TABLE IF NOT EXISTS publication_items (
  id                 SERIAL PRIMARY KEY,
  task_id            INT NOT NULL REFERENCES publication_tasks(id) ON DELETE CASCADE,
  price_list_item_id INT REFERENCES price_list_items(id) ON DELETE SET NULL,
  raw_name           TEXT NOT NULL,
  raw_sku            TEXT,
  raw_unit           TEXT,
  prices             JSONB NOT NULL DEFAULT '{}'::jsonb,
  raw_extra          JSONB NOT NULL DEFAULT '{}'::jsonb,
  raw_row            JSONB NOT NULL DEFAULT '[]'::jsonb,
  raw_data           JSONB NOT NULL DEFAULT '{}'::jsonb,
  status             TEXT NOT NULL DEFAULT 'new',
  product_id         INT REFERENCES products(id) ON DELETE SET NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (task_id, price_list_item_id)
);
CREATE INDEX IF NOT EXISTS publication_items_task ON publication_items(task_id);

COMMIT;
