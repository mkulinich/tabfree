# tabfree-migrate

TabFree's migration toolkit. Every operation is **read-only** against Tableau Server and Tableau Cloud.

Phase 0 ships:
- `tabfree-migrate smoke`: signs in with a personal access token and reports what the account can see. It reports server and REST API versions, counts of projects, workbooks, views, data sources, flows, users and groups, whether the Metadata API is available, and optionally some project and workbook names.
- `tabfree-migrate corpus-check`: verifies the test corpus against `corpus/manifest.json`, checking that every file exists, its hash matches, and its XML parses.

The crawler, downloader, ground-truth capture, TWB parser and extract converter follow in Phase 1 (`docs/BUILD_PLAN.md`, P1.1–P1.9).

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
