# ADR-001: Build a new purpose-built server rather than extending Superset or forking Lightdash

## Status

Accepted — pending validation (P1.14), 2026-11-20.

Deciding task: the three-day Superset spike, P1.14. If both of its questions pass, a new ADR supersedes this one and TabFree extends Superset instead.

## Context

TabFree's day-one contract is Tableau's governance and query semantics: sites, nested projects, capability-level allow/deny rules, project defaults, locked projects, site-role ceilings, LOD and filter order of operations, and dashboards as sandboxed HTML bundles ([ADR-002](0002-html-bundle-with-governed-data-contract.md)). The Tableau-semantics compiler, the migrator, the parity harness and the authoring MCP server must be built whichever server hosts them.

Apache Superset is the only permissively licensed server with the governance half: Apache-2.0, Athena and Redshift, roles, row-level security, guest-token embedding, alerts and reports, a Helm chart and, since 5.0, an MCP server. Its `.supx` extensions add features "without modifying the core codebase". But its roles are Admin/Alpha/Gamma plus custom roles, its dashboards are chart grids, and SAML is not documented natively. Lightdash has the closest authoring design (Claude Code apps in a sandboxed iframe, every query proxied and permission-checked), but its data apps, MCP service, SCIM and embedding sit in an `ee/` directory under the Lightdash Source Available License.

## Decision

Build a new server whose content model, permission engine ([ADR-006](0006-tableau-compatible-permission-engine.md)) and governed query service ([ADR-003](0003-own-tableau-semantics-compiler.md)) are designed for Tableau parity. Borrow patterns, not code:

- from Lightdash, the sandboxed frame with a host-side query proxy and append-only versions;
- from Superset, an MCP server that runs inside the user's permissions, previews before saving and audits every call;
- from Tableau's Migration SDK, the crawl order, resumable manifest and hooks.

## Consequences

### Positive

- One content model and one permission model, both shaped like Tableau's, instead of TabFree's running beside Superset's.
- No fork to rebase on upstream.
- The codebase is laid out for maintenance with Claude Code from day one.

### Negative

- We build what Superset already ships: authentication, scheduling, alerts, exports, embedding, admin screens and a Helm chart (Phases 3, 6 and 7).
- No contributor community at launch (risk R14).
- We alone own a larger security surface (P7.3).

## Alternatives considered

- **Extend Superset with a TabFree dashboard content type in a `.supx` extension:** reuses RBAC, RLS, alerts, embedding, MCP and the community, but Tableau's permission model and HTML dashboards would run parallel to Superset's own, and SAML needs custom work. Deferred to P1.14, not rejected.
- **Fork Lightdash:** the parts TabFree needs are source-available, not open source.
- **Fork Metabase or Grafana:** AGPL, with RLS, SAML, fine-grained RBAC or reporting in paid editions; TabFree would become AGPL and still have to rebuild them.

## Validation and revisit triggers

- **P1.14** answers two falsifiable questions:
  1. Can a `.supx` extension host a sandboxed dashboard content type whose queries proxy through Superset's data API?
  2. Can Superset's roles pass the Tableau effective-permission matrix without forking? The spike needs a first cut of the matrix that P3.3 later completes.
- **Both pass:** supersede this ADR with "extend Superset". **Either fails:** record the result here and set the status to Accepted.
- **Revisit if:** Superset adds capability-level denies and nested projects upstream, or Lightdash moves data apps under an OSI license.

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-001) and [§7](../BUILD_PLAN.md#7-phases-and-milestones) (P1.14)
- [Research report: thin governed core](../research/research-report.md#a-thin-governed-core-a-json-spec-and-an-owned-compiler-anchor-the-build)
- Superset: [extensions](https://github.com/apache/superset/blob/HEAD/docs/developer_docs/extensions/overview.md), [security](https://github.com/apache/superset/blob/HEAD/docs/admin_docs/security/security.mdx), [MCP server](https://github.com/apache/superset/blob/HEAD/docs/admin_docs/configuration/mcp-server.mdx)
- Lightdash: [data apps](https://github.com/lightdash/lightdash/blob/HEAD/docs/data-apps.md), [ee license](https://github.com/lightdash/lightdash/blob/HEAD/packages/backend/src/ee/LICENSE), [ee services](https://github.com/lightdash/lightdash/tree/HEAD/packages/backend/src/ee/services)
- [tableau-migration-sdk](https://github.com/tableau/tableau-migration-sdk)
