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
