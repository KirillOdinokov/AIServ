-- Доп. поля для прайсов с текстовыми строками (PDF/страницы), например АКВАСТОП.
ALTER TABLE site_price_mapping ADD COLUMN IF NOT EXISTS row_text TEXT;
ALTER TABLE site_price_mapping ADD COLUMN IF NOT EXISTS supplier_sku TEXT;
ALTER TABLE site_price_mapping ADD COLUMN IF NOT EXISTS expected_price NUMERIC(12,2);
ALTER TABLE site_price_mapping ADD COLUMN IF NOT EXISTS note TEXT;
