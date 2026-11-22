# TabFree

TabFree (a codename) is an open-source replacement for Tableau Server and Tableau Desktop on AWS.

- **Dashboards are HTML**, written by Claude or by people, using `<tf-*>` components. They contain no
  SQL, credentials or network calls: data comes only from named queries in the dashboard's
  `tabfree.json`, which the governed query service compiles and runs.
- **Data stays in AWS.** Queries run natively on Athena or Redshift. Extracts become Iceberg tables.
- **Tableau semantics are kept.** Calculated fields, LODs, table calculations, filters, parameters, sets,
  actions, permissions and row-level security work the way Tableau users expect.
- **A migration factory** crawls Tableau Server read-only, converts workbooks and data sources, and
  proves each dashboard against Tableau's own output before anyone approves it.
- **Claude edits dashboards** through an MCP server and "Edit with Claude" in the portal, on drafts that
  a person reviews and publishes.

Start with the guide for your role:

| You are | Read |
|---|---|
| Running TabFree | [Administration](guides/admin.md), [Deploy on AWS](operations/deploy.md) |
| Moving off Tableau | [Migrating from Tableau](guides/migration.md), [Cutover](operations/cutover.md) |
| Building dashboards | [Authoring dashboards](guides/authoring.md) |
| Integrating | [API reference](reference/api.md) |
| Reviewing the design | [Architecture](guides/architecture.md), [Decisions](adr/README.md), [Threat model](security/threat-model.md) |

Try it locally with sample data and no AWS account: `make demo` (Docker), or without Docker:

```bash
make setup && npm run build -w @tabfree/runtime
TABFREE_DEV_LOGIN=1 uv run uvicorn tabfree_api.main:app_factory --factory --port 8765
```
