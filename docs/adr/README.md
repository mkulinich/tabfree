# Architecture decision records

An architecture decision record (ADR) captures one significant decision: the context that forced it, the choice, its consequences, the alternatives rejected, and the evidence that would make us change course. TabFree keeps ADRs so that contributors, human or Claude, can see why the system is shaped the way it is without re-reading the research, and so that reversing a decision is a reviewed change rather than drift.

The first eleven records turn the decision table in [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) into full ADRs (task P0.2). The evidence behind them is in the [research report](../research/research-report.md) and its [notes](../research/notes/).

Write an ADR when a decision is hard to reverse, spans several packages, changes a public contract (the `tabfree.json` manifest, the `<tf-*>` components, the semantic model, the MCP tools, the public API), or affects security, licensing or running cost. Routine implementation choices belong in code review.

## Status values

| Status | Meaning |
|---|---|
| Proposed | Open for review in a pull request. Not binding yet. |
| Accepted | Binding. Build against it. |
| Accepted — pending validation (task ID) | Binding now, but the named spike, eval or test can reverse it. The ADR states what would confirm or reverse it. |
| Superseded | Replaced by a later ADR, named on the status line. Kept for history. |

When a deciding task finishes:

- **The result confirms the decision:** change the status to Accepted and add the dated result, with a link to the evidence, under "Validation and revisit triggers".
- **The result reverses or materially changes it:** write a new ADR that supersedes it, and change the old status line to "Superseded by ADR-NNN". Do not rewrite an accepted decision in place.

Typo fixes, broken links and extra references can be edited in place.

## Index

File names use four digits (`0001-…`); BUILD_PLAN.md and other documents cite the same records as ADR-001 to ADR-011.

| ADR | Title | Status | Deciding task |
|---|---|---|---|
| [ADR-001](0001-build-new-purpose-built-server.md) | Build a new purpose-built server rather than extending Superset or forking Lightdash | Accepted — pending validation (P1.14) | P1.14 Superset spike |
| [ADR-002](0002-html-bundle-with-governed-data-contract.md) | Dashboards are HTML bundles with a governed data contract | Accepted — pending validation (P1.15) | P1.15 authoring eval |
| [ADR-003](0003-own-tableau-semantics-compiler.md) | Own the Tableau-semantics compiler, in Python on SQLGlot | Accepted — pending validation (P2.11) | P2.11 Cube spike (execution and caching layer only) |
| [ADR-004](0004-table-calcs-sql-and-js-engine.md) | Table calculations run as SQL windows and in a JS engine that is the source of truth | Accepted | — |
| [ADR-005](0005-python-and-typescript.md) | Python 3.12+ for the server side, TypeScript for the browser | Accepted | — |
| [ADR-006](0006-tableau-compatible-permission-engine.md) | A Tableau-compatible permission engine behind a swappable backend | Accepted — pending validation (P3.3) | P3.3 effective-permission matrix (backend choice only) |
| [ADR-007](0007-iceberg-athena-redshift-app-side-rls.md) | Iceberg on S3, Athena by default, Redshift for live and hot sources, RLS in the compiler | Accepted | — (P1.16 and P2.10 set the per-source engine split) |
| [ADR-008](0008-sandboxed-claude-agent-runtime.md) | Claude Agent SDK in per-session Fargate sandboxes; AI stays optional | Accepted | — |
| [ADR-009](0009-aws-infrastructure-ecs-fargate.md) | AWS infrastructure on ECS Fargate and managed services, deployed with OpenTofu/Terraform | Accepted | — |
| [ADR-010](0010-apache-2-license.md) | License the project under Apache-2.0 | Accepted | — (public name: P0.7) |
| [ADR-011](0011-tableau-formula-syntax-as-calc-language.md) | Tableau's formula syntax is TabFree's calculation language | Accepted | — |
| [ADR-012](0012-hand-written-calc-parser.md) | Parse Tableau calculations with a hand-written parser, checked against the official grammar | Proposed | — (amends ADR-003 item 1) |

## Adding an ADR

1. Copy [0000-template.md](0000-template.md) to `NNNN-short-kebab-title.md`, using the next unused number. Numbers are never reused, even for superseded or abandoned records.
2. Fill in every section. Keep to one decision and roughly 300–600 words. Cite evidence with relative links to BUILD_PLAN.md, the research report or its notes, and external sources you have read.
3. Set the status to Proposed with today's date, and add a row to the index above.
4. Open a pull request; discussion happens there. When a maintainer approves and merges it, the status becomes Accepted, or Accepted — pending validation with the deciding task named. A rejected proposal is closed unmerged, with the reasoning left in the pull request.
5. If the new ADR supersedes an older one, update the older record's status line and the index in the same pull request.
