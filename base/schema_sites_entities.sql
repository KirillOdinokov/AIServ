-- Справочники: адреса и юрлица, привязка к сайтам
BEGIN;

CREATE TABLE IF NOT EXISTS addresses (
  id           SERIAL PRIMARY KEY,
  label        TEXT,
  full_address TEXT NOT NULL,
  city         TEXT,
  region       TEXT,
  postal_code  TEXT,
  lat          DOUBLE PRECISION,
  lon          DOUBLE PRECISION,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS legal_entities (
  id           SERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  full_name    TEXT,
  inn          TEXT,
  kpp          TEXT,
  ogrn         TEXT,
  legal_address TEXT,
  phone        TEXT,
  email        TEXT,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS addresses_city ON addresses(city);
CREATE INDEX IF NOT EXISTS legal_entities_name ON legal_entities(name);

ALTER TABLE sites ADD COLUMN IF NOT EXISTS address_id INT
  REFERENCES addresses(id) ON DELETE SET NULL;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS legal_entity_id INT
  REFERENCES legal_entities(id) ON DELETE SET NULL;

COMMIT;
