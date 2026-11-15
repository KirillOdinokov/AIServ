-- Подтягивание новых поставщиков в Base (2026-10-29)
-- 1) 10 поставщиков Amalor, у которых был пустой amalor_slug -> не попадали на /review
-- 2) 5 новых поставщиков портала (id 336-340) -> появляются в правой колонке /review для привязки

BEGIN;

-- 1. Amalor-only записи без amalor_slug (только те, чей slug свободен в amalor_slug)
UPDATE suppliers SET amalor_slug = slug
WHERE source = 'amalor'
  AND amalor_slug IS NULL
  AND slug IN (
    'rwl', 'baswool', 'komitexgeo', 'agroteks_gexa', 'vilatherm',
    'ico_russia', 'gexa_ecospan-geo', 'geospan_gexa', 'gexa_isospan', 'rockmelt'
  );

-- 2a. germoizol уже есть как amalor-запись с тем же slug -> привязываем portal_id
UPDATE suppliers
SET portal_id = 337, name = 'ПК Гермоизол', role = 'manufacturer', source = 'portal'
WHERE slug = 'germoizol' AND portal_id IS NULL;

-- 2b. Остальные новые поставщики портала
INSERT INTO suppliers (name, slug, portal_id, role, shipping, source)
VALUES
  ('ТК Фабрика Декоративного Бетона', 'fabrika-dekorativnogo-betona', 336, 'trader', NULL, 'portal'),
  ('ТК ООО "СИНТЕЗИКА"', 'ooo-sintezika', 338, 'trader',
   'Доверенность с паспортными данными. Отгрузка на основании СТС', 'portal'),
  ('ПК АО "Хантсман-НМГ"', 'ao-khantsman-nmg', 339, 'manufacturer',
   'Отгрузка по паспортным данным .', 'portal'),
  ('ПК ООО СВИЗСПЛАСТ', 'ooo-svizsplast', 340, 'manufacturer', NULL, 'portal')
ON CONFLICT (slug) DO NOTHING;

COMMIT;
