# ADR-004: Table calculations run as SQL windows and in a JS engine that is the source of truth

## Status

Accepted, 2026-11-20.

## Context

Tableau computes table calculations "on the local data in Tableau": over the view's aggregated result, partitioned and addressed by the view's dimensions, with compute-using options such as table, pane, cell, specific dimensions, "at the level" and "restarting every". SQL window functions reproduce the static cases (running sums, ranks, `LOOKUP`, percent of total) but not, cleanly:

- densification, where Tableau inserts missing addressing members such as months without sales;
- `PREVIOUS_VALUE`, which is recursive;
- `TOTAL` over non-additive measures, which re-aggregates the underlying rows;
- secondary calcs, and table-calc filters that hide marks which still feed the calc.

Several defaults (compute-using when unspecified, member ordering, when densification happens) are undocumented. Snowflake's converter blocks `PREVIOUS_VALUE` until a human translates it.

## Decision

Table calcs run in both places, under one declared semantics:

- The compiler ([ADR-003](0003-own-tableau-semantics-compiler.md)) writes each table calc into the `tabfree.json` manifest as a declarative `tableCalcs` entry: type, partitioning, addressing, frame and options.
- Where partitioning is static and the calc is window-expressible, the compiler also emits SQL window push-down, used when a partition is too large to send to the browser.
- `@tabfree/tablecalc`, a TypeScript engine in `@tabfree/runtime`, evaluates the declared entries over the result set. It is the source of truth for densification, `PREVIOUS_VALUE`, non-additive `TOTAL` and secondary calcs, and wherever the two paths disagree.
- Server-side paths that return table-calc values (exports, snapshots, data-driven alerts, `run_governed_query`) run the same engine, in the Playwright renderer or a Node worker.

## Consequences

### Positive

- Mirrors Tableau's own split: aggregation in the database, table calcs locally.
- Exotic semantics become ordinary, unit-testable code rather than contorted SQL.
- Re-sorting or changing compute-using recomputes without a new query.

### Negative

- Two implementations must agree; the conformance fixtures run both.
- The engine needs whole partitions. If a result hits the governed query service's row cap, the runtime falls back to push-down where equivalent, or refuses; it never computes over truncated data.
- Server-side workers need a JavaScript runtime, and table-calc semantics live in TypeScript while the rest of the compiler is Python ([ADR-005](0005-python-and-typescript.md)).

## Alternatives considered

- **SQL only:** densification and `PREVIOUS_VALUE` need generated calendars and recursive CTEs, and undocumented defaults are hard to emulate in SQL.
- **Client only:** every partition must reach the browser, which fails on large data and row caps.
- **DuckDB-WASM over the result set:** moves the same SQL limits into the browser.

## Validation and revisit triggers

- **P2.6** builds both paths. **P2.9** checks per-view CSV parity for every corpus worksheet with table calcs, through both paths where both apply.
- Undocumented defaults are learned from Tableau's output (captured CSVs, VizQL Data Service custom table calcs) and pinned as corpus cases.
- **Revisit if:** the two paths diverge in ways fixtures cannot pin down, or in-use dashboards hit browser memory limits.

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-004) and [§7](../BUILD_PLAN.md#7-phases-and-milestones) (P2.6, P2.9)
- [Research report: semantics](../research/research-report.md#semantics-not-xml-parsing-decides-whether-migrated-numbers-match); notes: [table calculations (Q7)](../research/notes/tableau_file_formats.md#q7-how-do-table-calculations-translate-sql-window-functions-or-client-side-computation)
- Tableau: [table calculations](https://help.tableau.com/current/pro/desktop/en-us/calculations_tablecalculations.htm), [table calculation functions](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_tablecalculation.htm)
- [Snowflake Tableau-to-Streamlit](https://docs.snowflake.com/developer-guide/streamlit/migrations-and-upgrades/tableau-to-streamlit)
