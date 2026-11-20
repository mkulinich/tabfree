# ADR-005: Python 3.12+ for the server side, TypeScript for the browser

## Status

Accepted, 2026-11-20.

## Context

The libraries TabFree relies on for fidelity are Python: the Tableau Hyper API (`tableauhyperapi`), Tableau Server Client (TSC), pantab, the VizQL Data Service SDK, and SQLGlot, which provides the `athena`, `redshift` and `trino` dialects the compiler emits ([ADR-003](0003-own-tableau-semantics-compiler.md)). The dashboard runtime, the `<tf-*>` components and the portal run in the browser. The team is three engineers working heavily with Claude Code, so every extra language costs tooling, CI and review attention.

## Decision

Two languages, no more:

- **Python 3.12+** for the API (FastAPI, Pydantic v2, SQLAlchemy 2, Alembic), the compiler, migration tooling, workers and the MCP server (official MCP Python SDK), managed as one uv workspace.
- **TypeScript** for the React portal, `@tabfree/runtime`, the `<tf-*>` components and the table-calc engine ([ADR-004](0004-table-calcs-sql-and-js-engine.md)), managed as npm workspaces.

The JSON Schemas in `packages/schemas` (Tableau IR, semantic model, `tabfree.json` manifest) are the contract between the two sides, and both validate against them. CI runs ruff, mypy and pytest on one side and eslint, tsc and vitest on the other (P0.1).

## Consequences

### Positive

- The Hyper API, TSC, pantab and SQLGlot run in-process, with no cross-language call between the API and the compiler.
- One language per side keeps onboarding and the Claude Code conventions in `CLAUDE.md` simple.
- Shared schemas catch drift between server and runtime early.

### Negative

- Tableau semantics are split across languages: the planner in Python, table calcs in TypeScript. Conformance fixtures must be language-neutral JSON, run against both.
- Two toolchains, two dependency trees and two supply-chain scans (P7.3).
- API performance under load is unproven until the P7.2 load test.

## Alternatives considered

- **All TypeScript:** loses SQLGlot and the mature Python Hyper API, TSC and pantab. Tableau's Rust Hyper client has Node.js bindings, but it is still a release candidate and still needs the closed `hyperd` binary.
- **All Python:** impossible for the in-frame runtime and components.
- **A third language (Rust or Go) for hot paths:** deferred until profiling shows a need.

## Validation and revisit triggers

- **Validation:** P0.1 CI passes on both toolchains, and the Phase 1 vertical slice (P1.8–P1.13) runs end to end across them.
- **Revisit if:** P7.2 shows Python-bound bottlenecks that caching cannot remove, or the compiler has to run in the browser.

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-005), [§5](../BUILD_PLAN.md#5-tech-stack) and [§6](../BUILD_PLAN.md#6-repository-layout)
- [Research report: reuse](../research/research-report.md#tableaus-own-apache-20-releases-are-the-biggest-reuse-win)
- [tableauhyperapi](https://pypi.org/project/tableauhyperapi/), [TSC](https://github.com/tableau/server-client-python), [pantab](https://github.com/innobi/pantab), [VizQL Data Service SDK](https://github.com/tableau/VizQL-Data-Service)
- [SQLGlot dialects](https://github.com/tobymao/sqlglot/tree/main/sqlglot/dialects), [hyper-api-rust](https://github.com/tableau/hyper-api-rust)
