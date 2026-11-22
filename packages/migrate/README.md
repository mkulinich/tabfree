# tabfree-migrate

TabFree's migration toolkit. Every operation is **read-only** against Tableau Server and Tableau Cloud.

Phase 0 ships:
- `tabfree-migrate smoke`: signs in with a personal access token and reports what the account can see. It reports server and REST API versions, counts of projects, workbooks, views, data sources, flows, users and groups, whether the Metadata API is available, and optionally some project and workbook names.
- `tabfree-migrate corpus-check`: verifies the test corpus against `corpus/manifest.json`, checking that every file exists, its hash matches, and its XML parses.

Phase 1 adds the crawler (`crawl`), TWB parser (`parse`), construct census (`census`), extract converter (`convert-hyper`), and the discovery commands described below (`docs/BUILD_PLAN.md`, P1.1–P1.9).

## TWB parser and the Tableau IR (P2.1)

`tabfree_migrate.twb.parse_file(path)` turns a `.twb/.twbx/.tds/.tdsx` into a `tabfree/tableau-ir@1` document, so the model generator and query planner never re-read the XML. It covers connections, relations with join clauses, object-graph relationships, extract settings, columns (calcs, parameters, groups, bins, sets, hierarchies, folders, aliases, formats, geographic roles), worksheets (parsed shelves and encodings, typed filters, sorts, table calcs, totals), dashboards (zones, floating zones, device layouts), all action types and stories.

- `tabfree_migrate.fieldref.parse_field_ref("[ds].[sum:Sales:qk]")` and `parse_shelf("([a] / [b])")` are the public helpers for Tableau's reference grammar.
- Feature-flag names (`_.fcp.Flag.true...relation`) are resolved first; the `true` variant wins.
- `tabfree_migrate.xsd.validate_file(path, schema_dir)` checks a file against the vendored XSD (needs the `xmlschema` dev dependency). Pre-2026 files deviate; `tests/xsd_known_deviations.json` lists each file's known deviation codes, and the test fails on any change.

## Smoke test against your Tableau Server

```bash
export TABLEAU_SERVER_URL=https://tableau.example.com
export TABLEAU_SITE=""                 # site content URL; empty for the Default site
export TABLEAU_PAT_NAME=tabfree-smoke
export TABLEAU_PAT_SECRET=...          # inject from a secret manager; never commit it
uv run tabfree-migrate smoke --sample 5
uv run tabfree-migrate smoke --json
```

The token's permissions decide what is visible. A site administrator sees everything on the site, and listing sites requires a server administrator. The secret is read only from the environment and is never printed.

## Discovery: harvests, downloads and ground truth (P1.2–P1.5)

> **Unverified against a live server.** Everything in this section is built and tested offline against fakes (`tabfree_migrate.testing`) and SQLite fixtures. Metadata API field names, repository table and column names, and TSC export behaviour follow Tableau's published documentation and must be checked on the first real run. A query that a server version rejects fails only its own entity or table; the run records the error and carries on.

All four commands are read-only against Tableau, take connection settings from environment variables only, and never print secrets. They share one SQLite file (`--db`, default `inventory.db`) with `crawl`, so run `crawl` first.

| Command | Task | Reads | Writes |
|---|---|---|---|
| `harvest-metadata` | P1.2 | Metadata API (GraphQL `query` only) | `metadata_objects`, `metadata_edges`, `metadata_checkpoints`, `metadata_runs` |
| `harvest-repository` | P1.3 | `workgroup` Postgres as `readonly` | `repository_rows`, `repository_checkpoints`, `repository_runs` |
| `download` | P1.4 | REST workbook/data source downloads | `blobs/sha256/…` and `downloads/manifest.json` in `--out` |
| `capture` | P1.5 | REST view image/PDF/data/crosstab exports | `blobs/sha256/…` and `ground_truth/index.json` in `--out` |

`--out` is a local directory or `s3://bucket/prefix`. S3 needs the `aws` extra (`uv sync --extra aws`, boto3) and uses the standard AWS credential chain.

### Metadata API (`harvest-metadata`)

Pages each entity (`databases`, `databaseTables`, `customSQLTables`, `publishedDatasources`, `embeddedDatasources`, `workbooks`, `sheets`, `calculatedFields`) through its `…Connection` with `first`/`after` cursors. Page sizes start at 50–500 nodes; if Tableau still reports its 20,000-node limit the page size halves and the same cursor is retried. Nested references (workbook → sheet, sheet → field, calc → referenced fields, custom SQL → tables → database, data source → tables) become rows in `metadata_edges`. `hasUserReference` marks data sources with user functions (RLS). `--resume` continues from the last saved cursor.

### Repository (`harvest-repository`)

```bash
# Tableau Server admin, once: tsm data-access repository-access enable --repository-username readonly ...
export TABLEAU_REPO_HOST=tableau.internal TABLEAU_REPO_PORT=8060   # port defaults to 8060
export TABLEAU_REPO_USER=readonly TABLEAU_REPO_DB=workgroup         # the defaults
export TABLEAU_REPO_PASSWORD=...                                    # inject from a secret manager
uv sync --extra repository                                          # psycopg 3
uv run tabfree-migrate harvest-repository --since-days 183
```

Sources: `views_stats`, `http_requests`, `historical_events` (with `historical_event_types` and `hist_*` lookups), `subscriptions`, `subscriptions_views`, `subscriptions_workbooks`, `data_alerts`, `data_alerts_recipients`, `comments`, `workbooks`/`datasources` sizes, `views` (id → LUID, to join usage) and `extracts`. The names come from Tableau's published *workgroup data dictionary*; verify them with `\d <table>` on the target server first.

Safety: each statement is a parameterised `SELECT` checked before it runs; the session is `default_transaction_read_only=on` with a statement timeout (`--statement-timeout`, seconds); every query pages by `id` with `LIMIT` and a per-run cap (`--max-rows`). Append-only tables (`http_requests`, `historical_events`) continue after the last id on later runs. Request URIs lose their query string and `/trusted/<ticket>` segments before storage.

### Downloads (`download`)

Every workbook and published data source is downloaded **without** extracts first; extracts follow only for `--with-extract <luid>` (repeatable) or `--all-extracts`. Files stream to a temporary file while hashed and are stored once by sha256. The manifest keys entries by `<kind>/<luid>/<no-extract|with-extract>` with `updated_at`, hash, size and key; re-runs skip unchanged items, so an interrupted run just continues. Throttling (429) and 5xx responses are retried with backoff. Downloaded files are untrusted: they are never opened, unzipped or executed, and the file type is decided from the zip magic bytes only.

### Ground truth (`capture`)

For each view: PNG (`resolution=high`, fixed `--viz-width`/`--viz-height`, default 1920×1080), PDF, summary CSV and crosstab (`.xlsx`). For the `--top` most used views (usage from `harvest-repository`, else REST `total_views`) the PNG and CSV are also captured under each permutation from `--permutations`:

```json
{
  "default": [{"label": "east", "filters": {"Region": "East"}}],
  "views": {"<view luid>": [{"label": "top5", "parameters": {"Top N": "5"}}]}
}
```

Filters are sent as `vf_<field>=<value>` and parameters as `vf_Parameters.<name>=<value>`. `ground_truth/index.json` records, per capture, the view and workbook, the workbook `updated_at`, the exact options **and the query parameters sent**, the hash, size and time, so every capture can be reproduced. Failed captures are recorded and retried on the next run; successful ones are skipped until the workbook changes (`--force` re-captures).

## Extract conversion at scale (P2.8)

`convert-batch` converts many `.hyper` extracts to Parquet plus Athena DDL, with per-column parity statistics, in parallel and resumably. `parity-check` compares a converted copy with the statistics Hyper recorded. Conversion needs the `hyper` extra (Linux x86-64 or Windows; there is no macOS arm64 wheel), so locally it runs in the converter image (`infra/docker/converter/`) and in CI's `hyper` job.

```bash
# From a directory of .hyper/.tde/.twbx/.tdsx files, or from the store written by `download`:
uv run tabfree-migrate convert-batch --input-dir extracts/ --out converted/ --workers 4
uv run tabfree-migrate convert-batch --store s3://RAW-BUCKET --out s3://LAKE-BUCKET/staging \
    --database tabfree_sandbox --iceberg-root s3://LAKE-BUCKET/warehouse/tabfree_sandbox --shard 0/8
# Verify: DuckDB over the local Parquet, or Athena (needs AWS and the aws extra)
uv run tabfree-migrate parity-check converted/extracts/<sha256>/report.json --root converted/
uv run tabfree-migrate parity-check report.json --engine athena --workgroup tabfree-sandbox
```

- **Identity and resume.** Each extract is keyed by the sha256 of its bytes (a package member is copied out of the zip and hashed; its name never reaches the file system). Output goes under `extracts/<sha256>/`: `<glue_table>/data.parquet` per table and `report.json`. `extracts/manifest.json` records sources and extracts; a re-run skips everything already converted, retries failures, and `--force` redoes all. Parallel Fargate tasks split the work with `--shard INDEX/COUNT` (each shard keeps its own manifest); within a task `--workers` processes each run their own `hyperd`.
- **Report.** Per extract: status (`converted`, `needs-upgrade`, `upgraded`, `unsupported`, `failed`), origin and package member, the IR extract settings, and per table the column mapping with notes, Hyper-side statistics, the table definition, Glue parameters, the Athena DDL (staging table, Iceberg CTAS, drop), an explicit Iceberg `CREATE TABLE` for rebuilds, and the Athena parity query.
- **Extract settings become table definitions.** Filters (kept verbatim, already applied in the data), aggregation/roll-up, sampling and the incremental refresh key are read from the IR's `datasources[].extract` (every key optional). The incremental key is resolved to its Glue column and recorded explicitly (`tabfree.extract.incremental_key`) for the Phase 6 refresh pipeline. Athena accepts only a fixed list of Iceberg table properties, so the recipe goes into the staging table's `TBLPROPERTIES` and into Glue table parameters (`tabfree.*`, applied with `glue:UpdateTable` after the CTAS).
- **Legacy `.tde`.** Detected by magic bytes (`20 02 01 62 a0 1e ab 07`; `.hyper` starts with `Hyper`), never by name, and reported as `needs-upgrade` with the two documented ways out: upgrade on Tableau (Server refresh, or Desktop 2024.2 or earlier) and download again, or run `convert-batch --tde upgrade` in the legacy image (`infra/docker/converter/Dockerfile.tde-legacy`, `tableauhyperapi==0.0.17537`, the build Tableau's `tde-to-hyper` sample needs). That writes `upgraded/<sha256>.hyper` to the output; the next run in the normal image converts it. The legacy path is untested: no `.tde` reader is available in CI.

### Type mapping

Generated from `tabfree_migrate.extract_types` (a test keeps this table in sync):

| Hyper type | Athena / Iceberg type | Export | Parity rule | Notes |
|---|---|---|---|---|
| `BOOL` | `boolean` | as is | boolean | Parity also counts true values. |
| `SMALL_INT` | `integer` | as is | integer | Widened: Iceberg has no 16-bit integer type. Lossless. |
| `INT` | `integer` | as is | integer |  |
| `BIG_INT` | `bigint` | as is | integer |  |
| `OID` | `bigint` | as is | integer | Unsigned 32-bit; fits in bigint. |
| `NUMERIC` | `decimal(p,s)` | as is | decimal | Precision and scale kept up to 38 digits; wider values become double (lossy, flagged). |
| `DOUBLE` | `double` | as is | float | Sums compared with a relative tolerance. |
| `FLOAT` | `float` | as is | float32 | 32-bit REAL; compared with a looser float32 tolerance. |
| `TEXT` | `string` | as is | text | Collation is recorded; Athena compares code points (case-sensitive). |
| `VARCHAR` | `string` | as is | text | Length limit is not enforced by Iceberg. |
| `CHAR` | `string` | as is | text | Fixed length is not kept. |
| `JSON` | `string` | as is | text | Kept as JSON text; parse with json_extract. |
| `DATE` | `date` | as is | date |  |
| `TIMESTAMP` | `timestamp` | as is | timestamp | Wall-clock time without zone; Iceberg timestamp (microseconds). |
| `TIMESTAMP_TZ` | `timestamp` | as is | timestamp_tz | Stored as the UTC instant (Parquet isAdjustedToUTC); zone recorded in tabfree.tz.* parameters. |
| `TIME` | `string` | `CAST(... AS TEXT)` | text | Athena tables have no TIME type: HH:MM:SS[.ffffff] text. |
| `INTERVAL` | `string` | `CAST(... AS TEXT)` | text | Hyper's interval text form. |
| `GEOGRAPHY` | `string` | `CAST(... AS TEXT)` | wkt | WKT text, SRID 4326 (WGS 84) recorded; use ST_GeometryFromText in Athena. |
| `BYTES` | `binary` | as is | binary | Parity checks counts only. |
| `(other)` | `string` | `CAST(... AS TEXT)` | text | Unknown Hyper type: exported as text and flagged in the report. |

Geography is exported as WKT text. WKT carries no SRID, so the table definition records `tabfree.geo.<column>.srid = 4326` (Tableau stores WGS 84 longitude/latitude); query it with `ST_GeometryFromText`. Text collations other than binary are recorded per column (`tabfree.collation.<column>`): Athena compares by code point and case-sensitively, so case-insensitive Tableau filters need `lower()`.

### Parity rules

One query per table computes the statistics in Hyper, in Athena and in DuckDB (the local stand-in for Athena, reading the exported Parquet); `tabfree_migrate.parity` compares them:

| Statistic | Column kinds | Rule |
|---|---|---|
| rows, non-null count, nulls | all | exact |
| distinct count | all but binary | exact; a warning only for text under a non-binary collation |
| sum | integer, decimal | exact, in 38-digit decimal arithmetic (Athena's `SUM(bigint)` would overflow) |
| sum, min, max | double / float | relative tolerance 1e-9 (1e-6 for 32-bit float); NaN equals NaN |
| true count | boolean | exact |
| min, max | integer, decimal, date | exact |
| min, max | timestamp, timestamp with zone | normalised to naive UTC, microseconds |
| min, max | text | exact; a warning only under a non-binary collation |
| min, max | WKT | normalised: upper case, whitespace collapsed |

**Where it runs.** Mapping, settings, `.tde` detection, the batch runner (with a fake converter) and parity (DuckDB over Parquet) are unit-tested on any platform. Real Hyper conversion and Hyper-vs-Parquet parity on corpus extracts run only in CI's `hyper` job. Athena DDL, Glue parameters, the Athena parity run, S3 and the Fargate task need AWS and have not been run yet.

## The migration factory (Phase 5)

`tabfree-migrate factory factory.yaml --out factory/` converts the harvested estate offline. The
config (`tabfree/factory@0`) names the inputs: the download sink or a folder of workbooks, the
inventory and repository harvests, the ground-truth sink, a local copy of convert-batch output, the
remap rules (`tabfree/remap@0`), identity mapping, the fallback owner and wave sizes. It writes:

| Path | What |
|---|---|
| `models/*.model.yaml`, `models/report.json` | one model per data source, connections remapped (P5.1) |
| `decisions.json` | sources no rule covers, grouped by database, with the choices that apply |
| `dashboards/<id>/` | `tabfree.json` (P5.2), `index.html` baseline (P5.3), `README.md`, `report.json` (coverage, unsupported, references), `reference.png`, `reference/*.csv`, `source.json` (IR excerpt for the repair agent) |
| `site.yaml`, `governance.json` | users, groups, projects, rules, redirects; favorites, jobs, alerts, custom views, tags, access reference (P5.4) |
| `waves.json`, `summary.json` | waves by usage and retirements (P5.7); counts and top unsupported features |

`crawl --type favorites --type custom_views --type user_workbooks` adds what governance and access
parity need (read-only REST calls per user). The folder is then imported into the server with
`python -m tabfree_api.migration import` (see the API README).
