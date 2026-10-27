-- Одноразовая установка расширений (выполнять от суперпользователя postgres):
--   sudo -u postgres psql -d base -f init_extensions.sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
