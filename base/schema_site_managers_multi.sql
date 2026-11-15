-- Несколько менеджеров на сайт
BEGIN;

CREATE TABLE IF NOT EXISTS site_managers (
  site_id        INT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  portal_user_id BIGINT NOT NULL REFERENCES portal_users(portal_id) ON DELETE CASCADE,
  is_primary     BOOLEAN NOT NULL DEFAULT false,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (site_id, portal_user_id)
);

CREATE INDEX IF NOT EXISTS site_managers_user ON site_managers(portal_user_id);

INSERT INTO site_managers (site_id, portal_user_id, is_primary)
SELECT id, manager_portal_user_id, true
FROM sites
WHERE manager_portal_user_id IS NOT NULL
ON CONFLICT (site_id, portal_user_id) DO NOTHING;

COMMIT;
