\echo === staging vectors ===
DROP TABLE IF EXISTS stg_amalor_vectors;
CREATE TABLE stg_amalor_vectors (product_id text, embedding text);
\copy stg_amalor_vectors FROM '/tmp/amalor_vectors.csv' WITH (FORMAT csv, HEADER true)

\echo === update products.embedding ===
DROP INDEX IF EXISTS products_embedding_hnsw;
UPDATE products p
SET embedding = v.embedding::vector(1024)
FROM stg_amalor_vectors v
WHERE p.amalor_id = v.product_id::bigint;

\echo === rebuild hnsw ===
CREATE INDEX IF NOT EXISTS products_embedding_hnsw
  ON products USING hnsw (embedding vector_cosine_ops);

DROP TABLE IF EXISTS stg_amalor_vectors;

\echo === result ===
SELECT count(*) AS products_with_embedding FROM products WHERE embedding IS NOT NULL;
