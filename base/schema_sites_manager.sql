-- Пользователи office.enix.ru + телефон/менеджер сайта
BEGIN;

CREATE TABLE IF NOT EXISTS portal_users (
  portal_id   BIGINT PRIMARY KEY,
  username    TEXT,
  full_name   TEXT,
  email       TEXT,
  phone       TEXT,
  department  TEXT,
  position    TEXT,
  is_active   BOOLEAN NOT NULL DEFAULT true,
  updated_at  TIMESTAMPTZ,
  synced_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE sites ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS phone_normalized TEXT;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS manager_portal_user_id BIGINT
  REFERENCES portal_users(portal_id) ON DELETE SET NULL;

COMMIT;
