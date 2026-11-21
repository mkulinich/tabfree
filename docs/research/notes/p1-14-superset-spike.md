# P1.14: Superset spike, desk phase (PRELIMINARY)

*Desk research, 2026-11-20. **This is not the three-day hands-on spike.** No Superset instance was run. Findings come from Superset's docs and source on GitHub, read through `gh api`. Where a conclusion depends on runtime behaviour, it is marked "to verify".*

**Version under test.** The latest stable release is **6.1.0**. **7.0.0rc3** was tagged on 2026-11-19, and 7.0 is the major that will be current going into 2027, so I read the `7.0.0rc3` tag and `master`. 7.0 changes the permission model substantially (see Q2), so the hands-on spike should use 7.0.

## Q1. Can a `.supx` extension host a sandboxed dashboard content type whose queries proxy through Superset's data API?

**Preliminary answer: no, not as a `.supx` extension in 6.1 or 7.0.** The pieces exist only in part. Some have been proposed and are unmerged.

### What extensions can contribute today

From [contribution-types.md](https://github.com/apache/superset/blob/master/docs/developer_docs/extensions/contribution-types.md) and the frontend source:

- **Views.** These go only at named locations, and the only locations the host renders are SQL Lab's: `sqllab.leftSidebar`, `rightSidebar`, `panels`, `editor`, `statusBar`, `results` and `queryHistory` ([SqlLab/contributions.ts](https://github.com/apache/superset/blob/7.0.0rc3/superset-frontend/src/SqlLab/contributions.ts)). `ViewListExtension` is used only inside SQL Lab. **There is no dashboard, list-page or top-level route location.**
- **Other slots:** commands, menus (SQL Lab), editor replacement, a chat panel, and a semantic-layer provider (behind the testing-stage `SEMANTIC_LAYERS` flag).
- **Backend:** REST endpoints under `/extensions/{publisher}/{name}/`, MCP tools and prompts, an extension storage API (ephemeral and persistent), and tasks.
- **Build-time UI overrides are not `.supx`.** `getExtensionsRegistry()` keys such as `dashboard.nav.right` and `dashboard.slice.header` must be compiled into the deployment's own frontend build. The version-history doc says outright that this "is a build-time UI override, not a registration API for separately loaded `.supx` extensions" ([version-history.md](https://github.com/apache/superset/blob/master/docs/developer_docs/extensions/extension-points/version-history.md); key list in [ui-overrides/types.ts](https://github.com/apache/superset/blob/7.0.0rc3/superset-frontend/packages/superset-ui-core/src/ui-overrides/types.ts)).

### What's missing for a dashboard content type

- **A page to mount on.** SIP-235, "navbar extension points and extension-owned pages", is open: an extension "has no host surface to mount on and no route to be reached at" ([#45248](https://github.com/apache/superset/issues/45248)).
- **A dashboard renderer slot.** PR [#41703](https://github.com/apache/superset/pull/41703) ("add dashboard renderer contribution point") was **closed unmerged** on 2026-07-11 and folded into SIP-219 ([#41779](https://github.com/apache/superset/issues/41779), open). Even as proposed it was "view-mode only", with the host still owning chart data fetching. It would have swapped how a Superset dashboard is drawn, not added a new content type. `registerDashboardRenderer` has no hits in the repo.
- **`dashboard` and `explore` client APIs.** These are an open PR, [#44276](https://github.com/apache/superset/pull/44276).

### Sandboxing

Per [security.md](https://github.com/apache/superset/blob/master/docs/developer_docs/extensions/security.md), "all extensions run in the same context as the host application, without additional sandboxing". A sandbox "in the future (as part of an additional SIP)" is planned but not designed. Extensions load through Webpack Module Federation, sharing the host's React and antd ([architecture.md](https://github.com/apache/superset/blob/master/docs/developer_docs/extensions/architecture.md)).

None of this blocks ADR-002 by itself. Our extension could still create its own `<iframe sandbox>` and run TabFree's postMessage bridge. But the trust boundary would be our code inside Superset's origin, with Superset's CSP (Talisman) to accommodate. To verify: whether Superset's CSP permits `srcdoc` or blob frames.

### Data path

`POST /api/v1/chart/data` takes a **client-composed** `QueryContext`, meaning a datasource plus columns, metrics, filters and extras. `ChartDataCommand.validate()` calls `security_manager.raise_for_access(query_context=…)`. That check is **per datasource**: schema or `datasource_access` permission, editorship, or a dashboard- or chart-scoped grant. The scoped grant requires `form_data.dashboardId` or `slice_id` to name a **stored chart** on that dashboard with the same datasource ([charts/data/api.py](https://github.com/apache/superset/blob/7.0.0rc3/superset/charts/data/api.py), [security/manager.py](https://github.com/apache/superset/blob/7.0.0rc3/superset/security/manager.py), around L5640–5700). Two consequences follow:

1. A sandboxed HTML dashboard that called `/chart/data` directly would hold a **generic query capability** over every dataset the user can access, including ad hoc metric and column SQL expressions. That breaks ADR-002 rule 1 (named queries only).
   - To keep the named-query contract, the extension's **backend** endpoint must resolve `tabfree.json` named queries into `QueryContext`s server-side.
   - `superset_core` exposes **no public query-execution API** for extensions. Its `queries` module has only DAOs and a dialect helper ([superset-core tree](https://github.com/apache/superset/tree/7.0.0rc3/superset-core/src/superset_core)). The endpoint would have to import Superset internals (`QueryContextFactory`, `ChartDataCommand`), which are not semver-stable.
2. **Per-dashboard governance doesn't carry over.** An HTML dashboard stored in extension storage is not a `Dashboard` row, so none of these apply to it:
   - 7.0's `editors` and `viewers`;
   - the list filters (`DashboardAccessFilter`);
   - export and import;
   - alerts and reports;
   - embedded guest tokens, whose `resources` name dashboards.

   The dashboard-scoped data grant also needs stored Slices. So we would either create shadow Superset charts per named query, or reimplement content permissions in the extension. The second is the ADR-001 objection: "parallel to Superset's own".

### A route that partly works

7.0's **semantic-layer extension point** lets an extension register a provider that discovers views and runs queries ([semantic-layers.md](https://github.com/apache/superset/blob/master/docs/developer_docs/extensions/extension-points/semantic-layers.md)). TabFree's compiler (ADR-003) could be a provider, so Superset charts would get Tableau semantics. But the flag is at testing stage, and the doc says "semantic views refuse guest row rules", which blocks embedded RLS. It also doesn't solve the HTML content type.

The fallbacks that do work are a **custom frontend build** (a viz plugin rendering a TabFree iframe per chart, or `DYNAMIC_PLUGINS`, which is at testing stage) or a fork. Both fail the "without forking" spirit.

## Q2. Can Superset's roles pass a Tableau effective-permission matrix without forking?

**Preliminary answer: no.** 7.0 closes part of the gap with Subjects, groups and per-object viewers, but it has no deny, no capability-level grants per object, and no project hierarchy.

### Superset's model in 7.0

From [security.mdx@7.0.0rc3](https://github.com/apache/superset/blob/7.0.0rc3/docs/admin_docs/security/security.mdx) and [UPDATING.md](https://github.com/apache/superset/blob/7.0.0rc3/UPDATING.md) ("Owners, dashboard roles, and RLS roles replaced by Subjects"):

- **FAB roles** hold permission–view pairs (`can_read` on `Dashboard`, `datasource_access` on `[db].[table]`, …). Permissions are the union of a user's roles. FAB documents no deny or negative permission ([FAB security](https://flask-appbuilder.readthedocs.io/en/latest/security.html)). FAB groups exist and can carry roles.
- **Subjects** (user, group or role) are assigned as `editors` (replacing `owners`) and, with `ENABLE_VIEWERS`, as `viewers` of individual dashboards and charts. `DASHBOARD_RBAC` and dashboard `roles` are removed. A resource with no viewers **falls back to dataset-based access**: anyone who can read a dataset can see every published dashboard that uses it. Dashboards must be published to be visible.
- **RLS**: Regular filters (applied to the listed subjects) and Base filters (applied to everyone except them), combined by group key (OR within a key, AND across keys). Clauses can use Jinja such as `{{ current_username() }}` when `ENABLE_TEMPLATE_PROCESSING` is on.
- **Hooks that work without a fork:**
  - `CUSTOM_SECURITY_MANAGER`, which can override `raise_for_access` and related methods ([config.py L244](https://github.com/apache/superset/blob/7.0.0rc3/superset/config.py));
  - `EXTRA_EDITORS_RESOLVER`, whose doc names "a folder-permission system" as the use case;
  - `EXTRA_ACCESS_QUERY_FILTERS` and `EXTRA_ACCESS_QUERY_EXCLUSIONS` for list visibility. The docs say "An exclusion … is not an access check" and that `EXTRA_EDITORS_RESOLVER` grants "cannot be reflected in list-view filtering".

### Gaps against the ADR-006 matrix

Tableau's documented order, confirmed this pass in [Effective permissions](https://docs.salesforce.com/tableau/current/en-us/server/permission_effective.md):
1. The site role gates every capability.
2. Admins, owners and project leaders get capabilities before any rules are checked.
3. User rules apply next (a user Deny or Allow decides).
4. Then group rules (any group Deny denies).
5. Then group sets.
6. Otherwise the capability is denied.

| Matrix dimension | Superset 7.0 | Gap |
|---|---|---|
| Explicit Deny; user rule over group rule | Union of grants; no deny | **Missing.** Base RLS filters deny rows, not content |
| ~12 capabilities per object (View, Filter, Download Summary/Full Data, Image/PDF, Web Edit, Share, Subscribe, Overwrite, Move, Delete, Set Permissions) | Per object: viewer or editor. Others are **global** role permissions (e.g. CSV export on every chart the user can see) | **Missing** per-object capabilities |
| Nested projects, default permissions, inheritance | No projects. SIP-223 folders ([#42798](https://github.com/apache/superset/issues/42798), open) state "Folder access does not grant access to dashboards inside the folder" | **Missing**, and the proposal rules out inheritance |
| Locked or customizable projects | None | **Missing** |
| Project leaders (including inherited ones) | None. Could be simulated with `EXTRA_EDITORS_RESOLVER`, but not in list filters | **Missing** |
| Site-role ceilings | Roles approximate them (Gamma ≈ Viewer, Alpha ≈ Creator) | Partial. The ceiling over per-object grants holds only if every role path is checked; to verify |
| Owner rights, except Set Permissions in locked projects | Editors can edit and add viewers. With `VIEWER_PROMISCUOUS_MODE` "they can share the data with anyone" | Partial; no locked exception |
| Workbook vs view permissions (hidden tabs) | Dashboard and chart are separate objects | Partial; mapping to verify |
| Data source Connect | `datasource_access` per dataset | Roughly equivalent |
| No implicit access | Dataset-based fallback when a dashboard has no viewers | **Semantic mismatch.** Every migrated dashboard needs explicit viewers |
| Sites (multi-tenant) | One namespace per instance | **Missing** |
| Group sets | None | **Missing** |
| Permission-filtered browse | List filters, plus additive or subtractive hooks | Partial |

**"Without forking" is technically achievable but empty.** A `CUSTOM_SECURITY_MANAGER` plus the hooks could host a Tableau evaluator. That evaluator would be ADR-006's engine, written against Superset internals that are not a stable API. The listing hooks would still have to agree with `raise_for_access` on every route: charts, dashboards, chart data, exports, reports, MCP and guest tokens. The work matches building our own engine, plus an upstream-tracking burden. That burden is real: 7.0's UPDATING.md has several breaking access-check changes, such as published-only fallbacks and edit-gated version history.

## Recommendation for ADR-001

**Keep "build new".** Both questions fail at desk level, and so ADR-001 says to "record the result here and set the status to Accepted".
- Q1 fails because there is no extension-owned page or dashboard content slot, no sandbox, and no public governed-query API.
- Q2 fails because there is no deny, no per-object capabilities and no project inheritance.

I suggest keeping the status as *Accepted — pending hands-on confirmation* until the shortened spike below finishes, and adding a revisit trigger: **SIP-235 (extension pages) and SIP-219 (default-tier providers) merged, plus a public query-execution API in `superset_core`.**

Borrow the following from Superset rather than its runtime:
- the semantic-layer provider contract, as a possible later integration so Superset users can query TabFree models;
- the subject (user/group/role) assignment model, and the published-gate lessons in UPDATING.md;
- the RLS group-key OR/AND combination, which is a useful design for ADR-007.

## What the hands-on spike must still verify (cut to about 1.5 days)

1. Run 7.0.0rc3 (or 7.0.0 final) with `ENABLE_EXTENSIONS`, `ENABLE_VIEWERS`, `EMBEDDED_SUPERSET` and `SEMANTIC_LAYERS`.
2. Q1:
   - Build a minimal `.supx` that mounts a view and creates an `<iframe sandbox="allow-scripts">`. Confirm Superset's default CSP allows it.
   - Confirm that no non-SQL Lab mount point exists without a custom build.
   - Prototype an `/extensions/…` endpoint that runs a named query through `ChartDataCommand`. Record which internal imports it needs and whether RLS and guest RLS apply.
3. Q2: encode the first cut of the P3.3 matrix (user vs group, allow/deny/unspecified, nested and locked projects, leaders, owners, site roles) as Superset fixtures. Record pass/fail per row, using only configuration and hooks.
4. Check for anything in 7.0 final that changes the above, such as SIP-223 or SIP-235 landing.
