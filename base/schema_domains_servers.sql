-- Сущности «домен» и «сервер», привязка к сайтам.
-- Применять от роли base: psql -h 127.0.0.1 -U base -d base -f schema_domains_servers.sql

BEGIN;

-- Домен: где продлевать (registrar), на каком аккаунте, даты регистрации/окончания.
-- Заполняется вручную при добавлении сайта (whois не тянем).
CREATE TABLE IF NOT EXISTS domains (
  id                SERIAL PRIMARY KEY,
  site_id           INT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  registrar         TEXT,                 -- Beget
  account           TEXT,                 -- odinoknx
  registration_date DATE,                 -- 2022-07-26
  expiration_date   DATE,                 -- когда продлевать
  notes             TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (name)
);
CREATE INDEX IF NOT EXISTS domains_site ON domains(site_id);

-- Сервер: метаданные хостинга, привязаны по имени к /servers (metrics.db).
CREATE TABLE IF NOT EXISTS servers (
  id              SERIAL PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,   -- имя сервера в мониторинге /servers
  os              TEXT,                   -- Ubuntu 24.04
  tariff_monthly  NUMERIC(12,2),          -- 2989 (руб/мес)
  balance         NUMERIC(12,2),          -- 9762 (остаток)
  has_gpu         BOOLEAN NOT NULL DEFAULT false,  -- для графика температуры GPU
  host            TEXT,                   -- IP/хост
  notes           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE sites ADD COLUMN IF NOT EXISTS server_id INT
  REFERENCES servers(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS sites_server ON sites(server_id);

COMMIT;
