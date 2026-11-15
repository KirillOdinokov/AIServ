-- Read-only API для плагина и версии очереди site_updates.
BEGIN;

CREATE SEQUENCE IF NOT EXISTS site_update_version_seq;

ALTER TABLE site_updates ADD COLUMN IF NOT EXISTS version BIGINT;
UPDATE site_updates SET version = nextval('site_update_version_seq') WHERE version IS NULL;
ALTER TABLE site_updates ALTER COLUMN version SET DEFAULT nextval('site_update_version_seq');
ALTER TABLE site_updates ALTER COLUMN version SET NOT NULL;
CREATE INDEX IF NOT EXISTS site_updates_site_version ON site_updates(site_id, version);

CREATE UNIQUE INDEX IF NOT EXISTS sites_api_key_unique ON sites(api_key) WHERE api_key IS NOT NULL;

COMMIT;
