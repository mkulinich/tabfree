# tabfree-schemas

JSON Schemas (Draft 2020-12) for the three documents that connect TabFree's components, plus validators and the `tabfree-validate` CLI.

| Document | `schema` value | Written by | Read by |
|---|---|---|---|
| [Dashboard manifest](src/tabfree_schemas/json/dashboard-manifest.schema.json) (`tabfree.json`) | `tabfree/dashboard@0` | Migration manifest generator, Claude, people | Governed query service, runtime, MCP server |
| [Semantic model](src/tabfree_schemas/json/semantic-model.schema.json) (YAML) | `tabfree/model@0` | Model generator, people | Compiler, query service, MCP `describe_model` |
| [Tableau IR](src/tabfree_schemas/json/tableau-ir.schema.json) | `tabfree/tableau-ir@0` | TWB parser | Model and manifest generators, recreation agent |

Key rules the schemas encode (see the ADRs in `docs/adr/`):

- **Dashboards get data only through named queries** declared in `tabfree.json` (ADR-0002). A query names dimensions and measures from a semantic model. The server compiles it with the viewer's row-level security, so there is never SQL in a dashboard.
- **Calculations use Tableau's formula syntax** (ADR-0011), e.g. `SUM([Profit]) / SUM([Sales])` or `{FIXED [Customer Name] : SUM([Sales])}`.
- **Table calculations are declarative** (`tableCalcs`), so they can run as SQL window functions or in the runtime's table-calc engine (ADR-0004).
- **No secrets.** Models reference credentials by Secrets Manager ARN only, and the IR rejects connection passwords and tokens.

## Use

```bash
uv run tabfree-validate packages/schemas/examples/dashboards/sales-overview/tabfree.json
uv run tabfree-validate packages/schemas/examples/models/superstore_sales.model.yaml --kind semantic-model
```

```python
from tabfree_schemas import validate, load_document, SchemaValidationError

kind = validate(load_document(path))  # detects the type from "schema"; raises SchemaValidationError
```

For manifests, `validate` also runs cross-reference checks that JSON Schema can't express:
- model aliases exist
- filter targets name real queries
- sorts and table calcs refer to outputs of their own query
- actions point at real queries and parameters

YAML is read with YAML 1.2 booleans, so `on:` stays a key rather than becoming `true`, and dates stay strings.

## Versioning

The `@0` suffix is the schema's major version. While TabFree is pre-1.0, breaking changes are allowed. Each one updates the examples and is called out in the PR. From v1.0, breaking changes bump the version (`@1`), and the server accepts both versions during a migration window.

## Examples

`examples/` holds a semantic model, a dashboard bundle (`tabfree.json` plus an illustrative `index.html`) and a hand-written IR document. The tests validate all of them.
