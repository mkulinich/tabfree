-- TabFree local sample data. Idempotent: safe to run again.
-- Run by bootstrap.sh through Trino's HTTP API. You can also run it by hand:
--   docker compose -f infra/docker/compose.yaml exec -T trino trino < infra/docker/bootstrap/sample-data.sql
-- Format rules for bootstrap.sh: one statement per block, ending with ";" at
-- the end of a line; full-line "--" comments are skipped.

-- An Iceberg namespace is a Trino schema. The explicit location mirrors the
-- AWS layout (s3://<lake bucket>/warehouse/<database>/).
CREATE SCHEMA IF NOT EXISTS iceberg.tabfree_dev
WITH (location = 's3://tabfree-lake/warehouse/tabfree_dev');

-- A tiny, made-up orders table so `SELECT` works out of the box.
CREATE TABLE IF NOT EXISTS iceberg.tabfree_dev.sample_orders
WITH (
  format = 'PARQUET',
  format_version = 2,
  partitioning = ARRAY['month(order_date)']
)
AS
SELECT
  CAST(order_id AS bigint) AS order_id,
  CAST(order_date AS date) AS order_date,
  CAST(region AS varchar) AS region,
  CAST(category AS varchar) AS category,
  CAST(sales AS decimal(12, 2)) AS sales,
  CAST(profit AS decimal(12, 2)) AS profit,
  CAST(quantity AS integer) AS quantity
FROM (
  VALUES
    (1001, DATE '2026-01-04', 'East', 'Furniture', 412.50, 61.20, 3),
    (1002, DATE '2026-01-11', 'West', 'Technology', 1299.00, 233.80, 1),
    (1003, DATE '2026-01-19', 'Central', 'Office Supplies', 38.75, 9.10, 5),
    (1004, DATE '2026-01-27', 'South', 'Furniture', 860.00, -42.00, 2),
    (1005, DATE '2026-02-02', 'East', 'Technology', 249.99, 37.50, 1),
    (1006, DATE '2026-02-09', 'West', 'Office Supplies', 74.20, 22.26, 8),
    (1007, DATE '2026-02-16', 'Central', 'Furniture', 515.40, 15.46, 2),
    (1008, DATE '2026-02-23', 'South', 'Technology', 1810.00, 362.00, 2),
    (1009, DATE '2026-03-03', 'East', 'Office Supplies', 19.90, 6.97, 10),
    (1010, DATE '2026-03-10', 'West', 'Furniture', 1120.00, -112.00, 4),
    (1011, DATE '2026-03-17', 'Central', 'Technology', 640.00, 96.00, 2),
    (1012, DATE '2026-03-24', 'South', 'Office Supplies', 55.60, 13.90, 4)
) AS t (order_id, order_date, region, category, sales, profit, quantity);
