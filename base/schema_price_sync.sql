BEGIN;

ALTER TABLE products ADD COLUMN IF NOT EXISTS price_updated_at TIMESTAMPTZ;
ALTER TABLE products ADD COLUMN IF NOT EXISTS price_source TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS price_currency TEXT DEFAULT 'RUB';

ALTER TABLE product_sites ADD COLUMN IF NOT EXISTS price_updated_at TIMESTAMPTZ;

ALTER TABLE price_changes ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ;
ALTER TABLE price_changes ADD COLUMN IF NOT EXISTS applied BOOLEAN;
ALTER TABLE price_changes ADD COLUMN IF NOT EXISTS reason TEXT;

ALTER TABLE site_updates ADD COLUMN IF NOT EXISTS old_price NUMERIC(12,2);
ALTER TABLE site_updates ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ;
ALTER TABLE site_updates ADD COLUMN IF NOT EXISTS error TEXT;

ALTER TABLE sites ADD COLUMN IF NOT EXISTS price_mode TEXT NOT NULL DEFAULT 'source';
ALTER TABLE sites ADD COLUMN IF NOT EXISTS price_multiplier NUMERIC(8,4) NOT NULL DEFAULT 1;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS price_random_pct NUMERIC(6,4) NOT NULL DEFAULT 0;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS price_round NUMERIC(10,2);
ALTER TABLE sites ADD COLUMN IF NOT EXISTS price_min NUMERIC(12,2);
ALTER TABLE sites ADD COLUMN IF NOT EXISTS price_max NUMERIC(12,2);
ALTER TABLE sites ADD COLUMN IF NOT EXISTS price_priority INT NOT NULL DEFAULT 100;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS api_key TEXT;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS remote_api_url TEXT;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS remote_api_key TEXT;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS sync_enabled BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'RUB';

ALTER TABLE price_lists ADD COLUMN IF NOT EXISTS price_date TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS products_price_updated ON products(price_updated_at DESC);
CREATE INDEX IF NOT EXISTS product_sites_price_updated ON product_sites(price_updated_at DESC);

UPDATE sites
SET platform = 'amalor',
    price_mode = 'source',
    price_priority = 100,
    sync_enabled = true,
    currency = 'RUB',
    price_multiplier = 1,
    price_random_pct = 0
WHERE domain = 'amalor.ru';

COMMIT;
