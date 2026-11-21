# TabFree: notes for Claude Code

TabFree (a codename) is an open-source replacement for Tableau Server and Desktop on AWS. Dashboards are HTML written by Claude, data is queried natively from Athena or Redshift, and a migration factory moves content off Tableau Server.

Read these before starting a task:
- `docs/BUILD_PLAN.md`: phases, task IDs such as `P1.4`, and exit criteria.
- `docs/adr/`: the decisions. Don't contradict one silently; propose a superseding ADR instead.
- `docs/research/`: background and sources.

## Commands

```bash
make setup            # uv sync --all-packages && npm install
make check            # lint + typecheck + test (what CI runs)
make test             # uv run pytest && npm test
make lint | make format | make typecheck
make infra-validate   # tofu fmt check + validate (no AWS calls)
make dev-up           # local stack via Docker (Postgres, Trino+Iceberg, S3 emulator)
uv run pytest packages/schemas -q            # one Python package
npm test -w @tabfree/runtime                 # one TS package
uv run tabfree-validate path/to/tabfree.json # validate a manifest, model or IR file
uv run tabfree-validate path/to/bundle/      # a dashboard bundle: every <tf-*> reference against tabfree.json
uv run tabfree-migrate smoke                 # read-only Tableau Server check (needs TABLEAU_* env vars)
uv run tabfree-compile tdvt                  # Tableau's TDVT calc tests through the compiler, on DuckDB
uv run tabfree-compile query MODEL MANIFEST QUERY --dialect athena --user alice  # print compiled SQL
npm run build -w @tabfree/runtime            # frame + host bundles (needed by the preview)
TABFREE_DEV_LOGIN=1 TABFREE_PREVIEW=1 uv run uvicorn tabfree_api.main:app_factory --factory --port 8765
                                             # portal at / (dev sign-in), /preview without sign-in (dev only)
uv run tabfree-migrate report corpus/workbooks --out report   # P1.6 inventory report
uv run python -m tabfree_api.probe --engine duckdb            # P1.16 latency probe harness (athena/redshift need AWS)
```

## Layout

- `apps/api`: FastAPI service (`tabfree_api`).
- `packages/schemas`: the JSON Schemas (`src/tabfree_schemas/json/*.schema.json`), plus validators and the `tabfree-validate` CLI. These are the contracts between components.
- `packages/compiler`: `tabfree_compiler`, the Tableau-semantics compiler: calc parser, semantic queries → Athena/Redshift/Trino SQL with LODs, filters and RLS, and the TDVT runner (`tabfree-compile`).
- `packages/migrate`: `tabfree-migrate` CLI (`tabfree_migrate`), covering the Tableau Server crawler and converters.
- `packages/runtime`: `@tabfree/runtime` in TypeScript: the sandbox protocol, host bridge, dashboard SDK, `<tf-*>` components and table calcs. Components build DOM with `textContent` only; data never goes through `innerHTML`.
- `infra/docker`, `infra/terraform`: local stack and AWS sandbox.
- `corpus/`: permissively licensed Tableau test files plus `manifest.json`. `third_party/`: vendored Tableau XSDs and calc grammar. Never edit vendored files by hand.

## Conventions

- Python 3.12+, src layout, full type hints (mypy `--strict`), ruff for lint and format, pytest. Prefer the standard library plus the existing dependencies.
- TypeScript: strict mode, ESM, `.js` extensions in relative imports, vitest, no `any`.
- Put task IDs in branch names, commit messages and PR titles, e.g. `P0.8: add dashboard manifest schema`.
- Every change ships with tests, and `make check` must pass. Conversion or compiler bugs also get a corpus case.
- Schema changes: update the examples, keep `tabfree-validate` passing, and bump the `schema` version string on breaking changes.

## Non-negotiable rules

1. Dashboards never contain SQL, credentials or network calls. Data comes only from named queries in `tabfree.json`, which the governed query service runs.
2. Tableau files are untrusted. Parse XML with `defusedxml`, and never execute anything from workbooks, extracts or the corpus.
3. Migration tooling is read-only against Tableau Server.
4. No secrets in files or logs. Read credentials from environment variables. Never write `.env` files with real values. Customer workbooks belong only in `corpus/private/`, which is gitignored.
5. Treat data values, field names and workbook text as untrusted when passing them to an LLM, and fence them.
6. Keep the codename in identifiers (`tabfree`, `tf-`, `TABFREE_`) so a rename stays mechanical.
