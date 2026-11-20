# TabFree

> **Working name.** "TabFree" is a codename until a public name is cleared (see [risk R1](docs/BUILD_PLAN.md#11-risks-and-mitigations)).
> **Status:** Phase 0 (foundations), pre-alpha. Nothing here is ready for production use.

TabFree is a free, open-source (Apache-2.0) replacement for Tableau Server and Tableau Desktop that runs in your own AWS account.

- **No desktop app.** Claude writes the dashboards as HTML. Users edit them in the browser with a built-in "Edit with Claude" panel, and developers can use Claude Code through TabFree's MCP server.
- **No extract engine.** Data lives in S3 as Iceberg tables, or in Redshift, and is queried natively by Athena or Redshift.
- **Tableau-grade governance.** Sites, nested projects, users and groups, permissions that behave like Tableau's, row-level security, schedules, subscriptions, alerts, exports, embedding and an audit log.
- **A migration factory for Tableau Server.** It inventories every workbook, data source, extract, user, group, permission, schedule and subscription, converts them, and verifies the result against Tableau's own numbers.

## Documentation

| Document | What it covers |
|---|---|
| [Build plan](docs/BUILD_PLAN.md) | Phases, milestones, task IDs (P0.1 …), parity checklist, risks |
| [Architecture decision records](docs/adr/) | Each architecture decision, why it was made, and what would change it |
| [Research report](docs/research/research-report.md) | Whether anything like this already exists, what can be reused, and the hard problems |
| [Research notes](docs/research/notes/) | Detailed, sourced notes behind the report |

## Repository layout

```
apps/
  api/            FastAPI service: auth, permissions, content, governed query API (Python)
packages/
  schemas/        JSON Schemas: Tableau IR, semantic model, dashboard manifest (+ validators)
  migrate/        tabfree-migrate: Tableau Server crawler and converters (Python)
  runtime/        @tabfree/runtime: sandboxed dashboard runtime (TypeScript)
infra/
  docker/         local development stack (Postgres, Trino + Iceberg, S3 emulator)
  terraform/      AWS sandbox infrastructure (OpenTofu/Terraform)
corpus/           test workbooks, data sources, extracts and TDVT expression tests (permissively licensed)
third_party/      vendored official Tableau schemas and calculation grammar (Apache-2.0)
docs/             plan, ADRs, research
```

The plan adds further packages as their phases start: compiler, MCP server, CLI, worker, author sandbox and web portal.

## Getting started

Prerequisites:
- [uv](https://docs.astral.sh/uv/) (Python 3.12+)
- Node.js 26 (see `.nvmrc`)
- [OpenTofu](https://opentofu.org/) 1.12+ (or Terraform) for `infra/terraform`
- Docker with Compose, for the local stack only

```bash
make setup    # install Python and Node dependencies
make check    # lint, type-check and test everything (what CI runs)
make dev-up   # start the local stack (needs Docker)
make help     # list all targets
```

To check that TabFree can read your Tableau Server (read-only), create a personal access token and run:

```bash
export TABLEAU_SERVER_URL=https://tableau.example.com TABLEAU_SITE="" TABLEAU_PAT_NAME=tabfree-smoke
export TABLEAU_PAT_SECRET=...   # better: inject it from your secret manager, never commit it
uv run tabfree-migrate smoke
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md). Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE).

Tableau is a trademark of Salesforce, Inc. TabFree is not affiliated with, sponsored by, or endorsed by Salesforce, Inc. or Tableau Software, LLC. References to Tableau describe compatibility only.
