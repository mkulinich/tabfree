# ADR-011: Tableau's formula syntax is TabFree's calculation language

## Status

Accepted, 2026-11-20.

## Context

Every migrated workbook carries calculated fields written in Tableau's formula language, and the analysts who will use TabFree already know it. The compiler ([ADR-003](0003-own-tableau-semantics-compiler.md)) has to parse that language anyway, using Tableau's official Apache-2.0 grammar, and TDVT pairs about 860 test files of Tableau formulas with expected results. Calculations are also what Claude edits, through "Edit with Claude" and MCP, so the language users see is the language every AI change must be checked in.

## Decision

Tableau's formula syntax is the user-facing calculation language: in calculated fields in semantic models, and in the measures and calculations of named queries in the `tabfree.json` manifest.

- The TabFree compiler is the only path from a formula to SQL. Neither users nor Claude write SQL in dashboards or models; raw SQL remains an admin-only tool.
- Migrated formulas are stored verbatim alongside their parsed form, so the original text is never lost.
- Every Claude edit to a formula goes through the same compiler, the compile check on both dialects and the conformance tests as a migrated formula.
- Constructs TabFree cannot run (`SCRIPT_*`, `MODEL_*`) fail compilation with a clear error; `RAWSQL` needs dialect review.
- TabFree adds no functions or syntax of its own in v1. Any extension needs a new ADR.

## Consequences

### Positive

- Migration keeps calculations verbatim, so reviewers compare like with like.
- Analysts' existing skills stay useful.
- One grammar and one conformance suite cover migration, manual edits and AI edits.

### Negative

- TabFree inherits Tableau's quirks by design: float division, `IIF` NULL handling, boundary-counting `DATEDIFF`, and LODs and table calcs whose results depend on the view. Newcomers who know SQL face a learning curve, so documentation matters (P7.6).
- The vendored grammar must track upstream changes and new Tableau functions.
- There is no escape hatch to engine-specific SQL functions outside admin review.

## Alternatives considered

- **A new expression language:** loses verbatim migration and makes every analyst relearn.
- **SQL snippets:** dialect-specific (Athena and Redshift differ), bypass the semantic model and RLS injection, and cannot be checked with TDVT.
- **Ossie or MetricFlow definitions as the user language:** no existing semantic layer models Tableau's grain-dependent LODs, and Ossie's spec is still incubating.

## Validation and revisit triggers

- **P2.2:** every calculation in the corpus parses with the vendored grammar; each failure becomes a grammar fix or a documented exception.
- **P2.9 / M2:** at least 95% of TDVT tests pass on both dialects.
- **Revisit if:** users repeatedly need constructs the language lacks, or Tableau changes the language incompatibly.

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-011) and [§7](../BUILD_PLAN.md#7-phases-and-milestones) (P2.2, P2.3, P2.9)
- Research report: [owned compiler](../research/research-report.md#a-thin-governed-core-a-json-spec-and-an-owned-compiler-anchor-the-build), [Tableau's reusable assets](../research/research-report.md#tableaus-own-apache-20-releases-are-the-biggest-reuse-win)
- [Calculation grammar](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool/blob/main/packages/evals/src/validator/grammar/TableauCalculationGrammar.g4), [CALC_GRAMMAR.md](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool/blob/main/docs/CALC_GRAMMAR.md)
- [TDVT expression tests](https://github.com/tableau/connector-plugin-sdk/tree/master/tdvt/tdvt/exprtests/standard), [FullFunctionMap.tdd](https://github.com/tableau/connector-plugin-sdk/blob/master/samples/components/dialects/FullFunctionMap.tdd)
