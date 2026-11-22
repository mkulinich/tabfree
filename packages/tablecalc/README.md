# @tabfree/tablecalc

Tableau table calculations over a query result, in TypeScript with no runtime dependencies. This is the source of truth for table calcs in TabFree ([ADR-004](../../docs/adr/0004-table-calcs-sql-and-js-engine.md)); the compiler can push the simple ones down to SQL as an optimisation.

```ts
import { applyTableCalcs } from "@tabfree/tablecalc";

const out = applyTableCalcs(
  { columns: [{ name: "Region", type: "string", role: "dimension" }, { name: "Month", type: "date", role: "dimension" },
              { name: "Sales", type: "float", role: "measure" }], rows },
  [
    { name: "Running", type: "running_sum", of: "Sales", computeUsing: "specific", addressing: ["Month"] },
    { name: "3M avg", type: "custom", expr: "WINDOW_AVG(SUM([Sales]), -2, 0)", computeUsing: "table_across" },
  ],
  { across: ["Month"], densify: ["Month"] },
);
```

It covers the following:
- **Compute Using:**
  - table and pane across/down and their combinations
  - cell and specific (addressing and partitioning)
  - `orderBy`, "at the level" and "restarting every"
- **Every quick table calc:** running, window, moving, difference, percent difference, percent of total, percentile, every rank variant, lookup, index, first, last, size and total, plus `nullIfIncomplete` and secondary calculations.
- **Custom formulas:**
  - `WINDOW_*`, `RUNNING_*`, `LOOKUP`, `PREVIOUS_VALUE`, `RANK_*`, `INDEX`, `FIRST`, `LAST`, `SIZE` and `TOTAL`
  - `IF`, `CASE`, `IIF`, `ZN`, `ISNULL` and arithmetic
  - Formulas are parsed against a fixed function list and never evaluated as code.
- **Densification and table-calc filters.**

Tests in `test/` use hand-computed values and Tableau's documented rank examples.
