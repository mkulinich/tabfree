# Sample - Superstore (derived)

`orders.parquet`, `people.parquet` and `returns.parquet` are Tableau's "Sample - Superstore" data, converted
from the spreadsheet packaged inside
`corpus/workbooks/tableau__extension-hierarchy-navigator-sandboxed/docs/Dimensional-Flat example Hierarchy Navigator_v2018.2.twbx`
(`Data/en_US-US/Sample - Superstore.xls`, sha256 `dbc634a4e5d521e8ddfab2487c8d5ca6ddabdca6139726f8241ba780f7356766`).
The licence is that of the source repository (see `corpus/manifest.json`).

Conversion (2026-11-20): the `.xls` was read with `xlrd` 2.0.1 in an isolated interpreter (untrusted input, nothing
executed), the sheets were written to CSV, and DuckDB wrote Parquet with snake_case column names and typed columns
(dates as DATE, `postal_code` as text). `returns` keeps one row per order.

Checks: 9,994 orders from 2016-01-03 to 2019-12-30; total sales 2,297,200.86; total profit 286,397.02 (Tableau's
published Superstore totals); 49 states; 4 regional managers; 296 returned orders.

Used by `packages/schemas/examples/models/superstore.model.yaml` and the P1.12 dashboards. Do not edit by hand.
