-- Логистические данные из Knowledge (steklotkani)
BEGIN;
ALTER TABLE products ADD COLUMN IF NOT EXISTS logistics jsonb;
COMMIT;
