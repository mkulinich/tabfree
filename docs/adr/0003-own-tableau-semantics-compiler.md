# ADR-003: Own the Tableau-semantics compiler, in Python on SQLGlot

## Status

Accepted — pending validation (P2.11), 2026-11-20.

Only the execution and caching layer is open; the P2.11 Cube spike decides it.

## Context

TabFree's core promise is Tableau's numbers on Athena and Redshift, which text translation does not deliver: one open converter translated 23.5% of calculations and 34.4% of worksheets of Tableau's own Superstore sample. The difficulty is semantic. Filters run in a fixed order, so a FIXED LOD ignores dimension filters unless they are context filters; INCLUDE and EXCLUDE depend on the view's grain; relationships query each table at its native grain; blends aggregate the secondary source before joining. Row-level functions differ too (float `/`, `IIF` NULL handling, boundary-counting `DATEDIFF`).

No existing semantic layer models grain-dependent LODs or Tableau's filter staging. Cube Core is the only candidate with Athena and Redshift drivers, a pre-aggregation store and declarative row-level policies in one Apache-2.0 package, but whether it can host staged LOD plans is unverified. Tableau publishes an Apache-2.0 grammar for its calculation language, and TDVT, the VizQL Data Service and captured CSVs provide test oracles.

## Decision

Build `tabfree-compiler` in Python:

1. Parse formulas with the vendored official grammar (ANTLR4 Python target) into a typed AST; build the dependency DAG; classify each calc as row-level, aggregate, LOD or table calc.
2. Plan VizQL-style: grain from shelves and encodings; filters staged in Tableau's order; FIXED/INCLUDE/EXCLUDE as staged CTEs with null-safe joins; Top N and sets as semi-joins; totals re-aggregated, never summed; relationships at native grain; blends as a secondary query left-joined on the link fields.
3. Emit SQLGlot trees for `athena` and `redshift` (`trino` locally). `USERNAME()` and similar functions become bind parameters from the viewer's session.

P2.11 picks the execution and caching layer: Cube Core runs the compiled SQL and supplies pre-aggregations, caching and access policies, or the governed query service runs it directly with its own result cache. The compiler owns semantics either way. Apache Ossie comes later as an import/export format, not the runtime model. LLM translation of single calcs is a gated fallback whose output must pass the same conformance and parity gates and is flagged for review.

## Consequences

### Positive

- Fidelity is measurable per function and per worksheet, on both dialects.
- One compiler serves migration, the runtime and Claude's edits.
- The compiler and conformance suite can ship as standalone packages (P2.12).

### Negative

- The largest engineering investment, on the critical path to M2.
- VizQL internals (LOD join plans, Top N ties, densification) are undocumented and must be learned empirically from Server output.
- If P2.11 rejects Cube, we also own pre-aggregation and cache invalidation.

## Alternatives considered

- **Cube Core as the semantic model:** a mismatch on LODs and table calcs.
- **dbt MetricFlow:** no caching and no RLS.
- **Malloy or Ibis:** Malloy has no Athena or Redshift dialect; Ibis has no Redshift backend.
- **LLM translation as the main path:** neither deterministic nor verifiable at scale.

## Validation and revisit triggers

- **M2:** ≥ 95% of TDVT tests pass on both dialects, and ≥ 90% of corpus worksheets match Tableau's CSV exports.
- **P2.11:** adopt Cube only if it runs the staged LOD SQL unchanged while providing pre-aggregations, caching and access policies.
- **Revisit if:** a dialect's TDVT pass rate stalls below target, or Ossie stabilises with LOD and table-calc coverage.

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-003) and [§7](../BUILD_PLAN.md#7-phases-and-milestones) (P2.2–P2.12)
- Research report: [semantics](../research/research-report.md#semantics-not-xml-parsing-decides-whether-migrated-numbers-match), [owned compiler](../research/research-report.md#a-thin-governed-core-a-json-spec-and-an-owned-compiler-anchor-the-build); notes: [LOD staging](../research/notes/tableau_file_formats.md#q6-how-do-lod-expressions-fixedincludeexclude-translate-to-sql-given-filter-order-of-operations)
- [tab2pbi self-test](https://github.com/AyushSingh110/Tableau-to-Power-BI-semantic-compiler), [Tableau order of operations](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm)
- [Calculation grammar](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool/blob/main/packages/evals/src/validator/grammar/TableauCalculationGrammar.g4), [SQLGlot dialects](https://github.com/tobymao/sqlglot/tree/main/sqlglot/dialects)
- [Cube data access policies](https://cube.dev/docs/product/auth/data-access-policies), [Apache Ossie](https://www.techzine.eu/news/data-management/144719/microsoft-and-google-to-contribute-to-the-apache-ossie-project/)
