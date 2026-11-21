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

`tabfree-compile tdvt` runs Tableau's TDVT expression suites (vendored in `corpus/tdvt`) on DuckDB:

| Suite | Passed | Notes |
|---|---|---|
| standard | 761 of 769 | Calcs, operators, strings, numbers, dates, casts, logic |
| lodcalcs | 5 of 5 | FIXED, INCLUDE and EXCLUDE, including nested LODs |
| regexcalcs | 145 of 148, with the ICU-only file run on the real engines | REGEXP_MATCH, REGEXP_EXTRACT, REGEXP_EXTRACT_NTH and REGEXP_REPLACE |
| iso8601week | 194 of 194 | ISO-8601 calendar |
| dateparse, percentile, median, pretest | 30 of 30 | |
| **Total** | **1,135 of 1,146 (99.0%)** | |

The 11 failures are listed with reasons in `tdvt.KNOWN_DEVIATIONS`; most are errors in the expected files themselves. `tests/test_tdvt.py` fails if anything else breaks.

Generated SQL for every test parses as Athena, Redshift and Trino. `python -m tabfree_api.conformance --engine athena|redshift` runs the same suites on the real engines; the nightly `conformance` workflow does this once AWS is configured.

Other checks:
- **`tabfree_compiler.parity`:** compares a view's result with Tableau's own CSV export.
- **`tabfree_migrate.vds`:** asks Tableau's VizQL Data Service for its answer to a calculation.

## Phase 2 additions

- **The full function map:**
  - regex functions
  - the ISO-8601 calendar
  - DATEPARSE with ICU patterns
  - PERCENTILE, CORR and COVAR
  - FINDNTH, PROPER, MAKEDATETIME and MAKETIME
  - USERATTRIBUTEINCLUDES
  - fiscal years on date pills
  - an explicit list of unsupported functions, each with its reason
- **Planner:**
  - groups, bins, and sets (member, Top N and condition)
  - totals and subtotals, re-aggregated rather than summed
  - Measure Names/Values
  - computed sorts
  - native-grain aggregation over relationships
  - data blending (`compile_blend`)
- **Calculation dependency graph** with each calc's kind (`analyze_calcs`).
- **SQL window push-down of table calcs** (`table_calcs="sql"`), with `deferred_table_calcs` for the rest.

## Not yet

- **Functions:** spatial functions (Phase 4); RAWSQL, SCRIPT_* and MODEL_* are refused with a reason.
- **Data modelling:**
  - many-to-many relationships
  - blends across different connections (two queries joined in the service)
  - entitlement-table RLS
- **Engines:** running on Athena and Redshift. The SQL parses for both, but the TDVT runs there need an AWS account (P2.9/P2.10).
