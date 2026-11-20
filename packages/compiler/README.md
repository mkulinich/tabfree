# tabfree-compiler

The Tableau-semantics compiler ([ADR-003](../../docs/adr/0003-own-tableau-semantics-compiler.md)). It turns Tableau calculations and the named queries in a dashboard's `tabfree.json` into SQL for Athena, Redshift and Trino. It aims to give the same numbers Tableau would.

```bash
uv run tabfree-compile query packages/schemas/examples/models/superstore_sales.model.yaml \
  packages/schemas/examples/dashboards/sales-overview/tabfree.json by_region --user alice
uv run tabfree-compile expr MODEL "{FIXED [Customer Name] : SUM([Sales])}" --dialect redshift
uv run tabfree-compile tdvt          # Tableau's own expected results, run on DuckDB
```

```python
from tabfree_compiler import Session, compile_query

compiled = compile_query(
    model_doc,
    manifest["queries"]["by_region"],
    dialect="athena",
    session=Session(username="alice", groups=frozenset({"Sales"})),
    filters=[{"field": "Region", "kind": "categorical", "members": ["East"]}],
)
compiled.sql, compiled.parameters, compiled.columns
```

## What it does

- **Calculations** (`calc.py`): a parser for Tableau's formula language, producing a typed AST ([ADR-012](../../docs/adr/0012-hand-written-calc-parser.md)). It handles fields, parameters, operators, `IF`/`CASE`/`IIF`, `IN`, functions and `{FIXED|INCLUDE|EXCLUDE ...}`.
- **Semantic models** (`model.py`): reads a `tabfree/model@0` document and inlines calculated fields, detecting cycles.
- **Queries** (`sql.py`): follows Tableau's order of operations. The steps run in this order:
  1. Data source filters, row-level security and context filters
  2. FIXED LODs and Top N
  3. Dimension filters
  4. INCLUDE/EXCLUDE LODs
  5. Measure filters

  LOD results join the view at the combined grain of the view and the LOD, so coarser LODs replicate and finer ones aggregate. Tables are joined through the model's many-to-one relationships.
- **Tableau's row-level behaviour:**
  - `/` is float division and returns NULL when dividing by zero.
  - `DATEDIFF` counts boundaries crossed.
  - Weeks start on the model's `weekStart`.
  - `INT` truncates toward zero.
  - Date serials count from 1900-01-01.
  - Out-of-domain maths (such as `LN(0)` or `SQRT(-1)`) returns NULL, not NaN or infinity.
- **Security:**
  - `USERNAME()`, `ISMEMBEROF()`, parameters and similar values become bind parameters (`?` placeholders, or `:name` on Redshift); they never appear in the SQL text.
  - Filter values are escaped literals.
  - Model identifiers are validated and quoted.
  - Formulas are untrusted input: length and nesting are capped, and nothing is evaluated.

## Conformance

`tabfree-compile tdvt` runs Tableau's TDVT expression suites (vendored in `corpus/tdvt`) on DuckDB. As of 2026-11-20, 771 of 779 tests pass across `standard`, `lodcalcs`, `median` and `pretest`. The eight that fail are listed with reasons in `tdvt.KNOWN_DEVIATIONS`, and `tests/test_tdvt.py` fails if anything else breaks. Generated SQL for every test also parses as Athena, Redshift and Trino.

DuckDB checks semantics, not dialect behaviour. Running the same suites on Trino (the local stack) and on real Athena and Redshift is still to do.

## Not yet

- **Functions:** regular expressions, `DATEPARSE`, `PERCENTILE` and the ISO-8601 calendar (P2.3).
- **Table calculations:** they run in the runtime's JS engine (ADR-004).
- **Data modelling:** many-to-many relationships and native-grain relationship queries (P2.5), blending, sets and groups as query inputs, and entitlement-table RLS.
