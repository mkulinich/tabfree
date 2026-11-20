# ADR-006: A Tableau-compatible permission engine behind a swappable backend

## Status

Accepted — pending validation (P3.3), 2026-11-20.

The permission model is decided. P3.3 chooses the backend that evaluates it, using the Tableau effective-permission matrix.

## Context

Migrated users must see exactly what Tableau showed them. Tableau evaluates Allowed, Denied and Unspecified capability rules for users and groups, applies project default permissions and locked projects, caps everything by site role, and gives views their own permissions when a workbook's tabs are hidden or its project is locked. Owners, project leaders and administrators have implicit rights that the REST API does not list, so TabFree must compute effective access rather than copy grants. Sources summarise Tableau's rule as "deny wins", but whether an explicit user Allow overrides a group Deny is unconfirmed, and the two readings give the same person different access.

## Decision

Build the permission engine into the API, with rules stored in Postgres:

- Site roles as ceilings: Creator, Explorer, Explorer (can publish), Viewer, Site Administrator.
- Capabilities per content type: View, Filter, Download Image/PDF, Download Summary Data, Download Full Data, Web Edit (Edit with Claude), Share, Subscribe, Overwrite, Move, Delete, Set Permissions; Connect for data models.
- Allow, Deny or Unspecified per rule; user rules over group rules; deny wins among groups.
- Owners and project leaders; project default permissions; locked projects.
- Permission-filtered listing for browse and search.

Callers see one interface: check a capability, list permitted objects. P3.3 picks the backend behind it: a plain Postgres evaluator; embedded Cedar, whose `forbid` gives deny-wins and which unshackle already emits; or OpenFGA, whose list-objects call suits permission-filtered browse at scale.

**The exact precedence must be verified against Tableau's documentation before the model is frozen.**

## Consequences

### Positive

- Migrated permissions mean the same thing, and one matrix proves it.
- No extra service unless OpenFGA wins.
- The backend can change without touching callers.

### Negative

- We own a security-critical evaluator that every API route and MCP tool must call.
- Cedar's `forbid` overrides every `permit`, so a confirmed "user Allow beats group Deny" must be encoded in policy conditions, not rule types.
- OpenFGA would add a service and a sync from Postgres rules to tuples.
- Row-level security is a separate mechanism ([ADR-007](0007-iceberg-athena-redshift-app-side-rls.md)); admins must understand both.

## Alternatives considered

- **OpenFGA from day one** (the research default): kept as a candidate backend, chosen if the matrix or list-filtering performance calls for it.
- **Amazon Verified Permissions:** $5 per million checks and AWS lock-in.
- **Role-based access only:** cannot express capability-level denies, project defaults or locked projects.

## Validation and revisit triggers

- **P3.3:** a table-driven matrix covering user versus group rules, allow/deny/unspecified, nested and locked projects, site-role ceilings, owners, project leaders (including inherited ones) and views of hidden-tab workbooks. A backend must pass all of it; list-filtering latency at the P7.2 scale breaks ties.
- **P5.5 / M5:** for each user, Tableau's "Query Workbooks for User" matches TabFree's View results, with zero unexplained differences.
- **Revisit if:** Tableau's documentation or Server behaviour contradicts the precedence above.

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-006) and [§7](../BUILD_PLAN.md#7-phases-and-milestones) (P3.3, P5.5)
- Research report: [permission contract](../research/research-report.md#day-one-requires-tableaus-permission-contract-and-query-semantics-not-its-authoring-ui), [authorization](../research/research-report.md#a-thin-governed-core-a-json-spec-and-an-owned-compiler-anchor-the-build)
- Tableau: [permissions](https://help.tableau.com/current/server/en-us/permissions.htm), [projects](https://help.tableau.com/current/server/en-us/permissions_projects.htm), [capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm), [site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm), [REST permissions](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_permissions.htm)
- [OpenFGA](https://github.com/openfga/openfga), [Cedar](https://aws.amazon.com/blogs/opensource/cedar-joins-cncf-as-a-sandbox-project), [AVP pricing](https://aws.amazon.com/verified-permissions/pricing/), [unshackle](https://github.com/Querio-App/unshackle)
