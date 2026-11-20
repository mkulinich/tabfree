# Tableau Server / Tableau Cloud APIs and Migration Mechanics: what TabFree's migration crawler can extract, and how

Research date: 2026-11-20. Sources: help.tableau.com pages (generated 2026-11-19/20), the Tableau data dictionary for 2026.3, and Tableau GitHub repos read with the `gh` CLI (read-only; no code was run). No calls were made to any Tableau Server or Cloud site. Endpoint paths below use `{v}` for the API version, `{site}` for the site LUID, and the base `/api/{v}/sites/{site}`. The base is left out when it is obvious.

---

## Q1. REST API: current version and Server mapping, authentication, multi-site, pagination, rate limits

### Takeaway
As of November 2026 the newest REST API is **3.30** (Tableau Cloud October 2026 / Tableau Server 2026.3). The crawler should detect the version at run time through `GET /api/{v}/serverinfo` and switch features on or off from there. For a Server crawler, the best authentication is a **connected-app JWT** (it allows concurrent sessions and access scopes), or several PATs. A single PAT can't hold concurrent sessions. Every REST session is bound to **one site**. `switchSite` exists only on Server, so Cloud needs one sign-in per site.

### Cited Findings
**Versions**
- Version mapping table: 2026.3 → 3.30, 2026.2 → 3.29, 2026.1 → 3.28, 2025.3 → 3.27, 2025.2 (Cloud only) → 3.26, 2025.1 → 3.25, 2024.3 (Cloud only) → 3.24, 2024.2 → 3.23, 2024.1 (Cloud only) → 3.22, 2023.3 → 3.21, 2023.1 → 3.19 — [REST API and Resource Versions](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_versions.htm)
- Release labels in What's New: 3.30 = "Tableau Cloud October 2026 / Tableau Server 2026.3"; 3.29 = "Tableau Cloud June 2026 / Tableau Server 2026.2"; 3.28 = "Tableau Cloud February 2026" (no Server release named); 3.27 = "Tableau Cloud December 2025 / Tableau Server 2025.3" — [What's New in the REST API](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm). The docs disagree with each other here. The versions table lists "2026.1 → 3.28" with no "Cloud only" flag. The Collections methods, though, say "Available in API 3.28 (Cloud February 2026 / Server 2026.2)" — [Collections Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_collections.htm).
- There are two endpoint families. "Classic" endpoints are `{server}/api/{api-version}/sites/{site-luid}/...`. "Per-resource versioned" endpoints are `{server}/api/-/...`; these infer the site from the sign-in context and are versioned independently — [REST API and Resource Versions](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_versions.htm)
- `GET /api/{v}/serverinfo` returns `<productVersion build=...>` and `<restApiVersion>` — [Server Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_server.htm)
- Notable recent additions: 3.29 added Recycle Bin, Bridge clients/pools/domains, and per-site Tableau MCP settings, and retired the Data Acceleration methods. 3.30 added Bridge routing rules and published-data-source "work copy" editing (requires Data Management or Tableau+). Tableau Cloud September 2026 added Knowledge Graph methods (lineage, downstream impact, semantic context) — [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm); the Knowledge Graph is "Not available for Tableau Server" — [Tableau Knowledge Graph Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_tableau_knowledge_graph.htm)

**Authentication**
- The docs compare four sign-in methods: PAT, connected-apps JWT, unified access token (UAT) JWT, and username/password. REST access by method: PAT on Cloud and Server; connected apps on Cloud and Server; UAT on Cloud only; username/password on Server, "limited in Tableau Cloud". "Allows concurrent user sessions" is marked for connected apps (Cloud and Server) and UAT (Cloud), but not for PATs or username/password. "Supports user impersonation" is marked for Tableau Server with PATs and with username/password. "Supports access scopes" is marked for connected apps and UAT — [Signing In and Signing Out (concepts)](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_auth.htm)
- On Cloud with MFA and Tableau authentication, PATs are required. Username/password sign-in is not allowed; UATs are the alternative — [concepts_auth](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_auth.htm)
- A PAT expires if unused for 15 consecutive days. On Server, PATs expire after one year by default. On Cloud, expiry follows a site setting — [concepts_auth](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_auth.htm)
- UATs (Cloud only, from December 2025) are generated through the Tableau Cloud Manager (TCM) REST API. They share access scopes with connected apps — [concepts_auth](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_auth.htm); [What's New 3.27](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm)
- API 3.27 added more JWT scopes on Cloud and a subset on Server. Many read methods now list scopes, for example `tableau:views:download` for Query View Data and `tableau:embedded_credentials:download` for the keychain download — [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm); [Workbooks and Views Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm)
- A credentials token is good for 240 minutes by default (`wgserver.session.idle_limit`). The token works only on the site you signed in to; using it against another site returns 403. A Server system administrator signing in with username and password can impersonate any user — [concepts_auth](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_auth.htm)

**Multi-site**
- `POST /api/{v}/auth/switchSite` switches to another site the user can access, returns a new token, and invalidates the old one. "This method is not available on Tableau Cloud." — [Authentication Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_authentication.htm)
- `GET /api/{v}/sites` (Query Sites) returns the sites the caller can access and "is not available for Tableau Cloud" — [Site Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_site.htm)

**Pagination, filtering, field selection**
- List methods return `<pagination pageNumber pageSize totalAvailable>`. The default page size is 100. The docs say any positive integer is allowed, but they also list an error "The specified page size is larger than the maximum page size" — [Paginating Results](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_paging.htm); [Users and Groups Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_users_and_groups.htm)
- Filter expressions use `field:operator:value` with operators such as `eq, gt, gte, lt, lte, in, has, cieq, like`. Query Workbooks for Site can filter on `updatedAt` with `gt`/`gte`, which supports incremental crawls — [Filtering and Sorting](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_filtering_and_sorting.htm)
- The `fields=` parameter accepts `_all_`, `_default_`, and dotted resource fields such as `owner.name` — [Using Fields in the REST API](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_fields.htm)
- After a resource is created, the search index lags, so an immediate query "might not be up to date" — [Workbooks and Views Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm)

**Rate limits and concurrency (mainly Tableau Cloud)**
- On Tableau Cloud, concurrent requests to endpoints that start long-running jobs (Query View Data is one) are capped at 20. Requests over the cap get HTTP 429 and should be retried later — [Workbooks and Views Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm)
- Cloud tenant capacity:
  - **Extract APIs** (Run Extract Refresh Task, Update Data Source Now, Update Workbook Now, `tabcmd refreshextracts`): Standard 120 calls/hour; Enterprise 240 + 1 per Creator; Cloud+/Tableau+ 480 + 2 per Creator.
  - **Publish APIs:** Standard 800/hour; Enterprise 1,200 + 1 per 2 Creators; Cloud+/Tableau+ 2,400 + 1 per Creator.
  - **Visualizations:** up to 600 requests per hour per user.
  - **VDS:** 100 calls per Creator license per hour.
  - Over-limit calls return `429000: Too Many Requests … Please retry after N second(s)`.
  - Sources: [Tableau Cloud Site Capacity](https://help.tableau.com/current/online/en-us/to_site_capacity.htm); [TCM capacity](https://help.tableau.com/current/online/en-us/cloud_manager_capacity.htm)

### Inferences
- Build the crawler around runtime feature flags keyed on `restApiVersion`. Supporting 3.19 (2023.1) through 3.30 covers most Server estates still in service. Keychain export needs 3.24 or later, VC JSON 3.23, group sets 3.22, custom-view data and PDF 3.24, SVG images 3.29, and collections 3.28 (Cloud) or Server 2026.2.
- For parallelism on Server, use a connected app (direct trust) with read scopes. The alternative is a pool of PATs, one per worker, because PATs don't allow concurrent sessions. For multi-site Server, sign in once per site, or use `switchSite` in a single-threaded orchestrator. Per-site workers each need their own token.
- Downloads and view renders are the expensive calls. On a Cloud source, budget for the 600 visualization requests per hour per user and the 20-concurrent long-job cap. On a Server source, throttle to protect the backgrounder and VizQL processes; Server has no published REST rate limit (see Gaps).

### Gaps
- I found no documented maximum `pageSize` value for REST list methods. The error exists but the number isn't stated in the pages I read. Practitioners commonly use 1000, but I couldn't verify that here.
- I found no published REST rate limit for Tableau Server, other than the Metadata API throttling covered in Q4.
- It is unclear whether a Tableau Server **2026.1** release exists. The docs conflict (see above).

---

## Q2. REST API object inventory: what can be extracted (endpoints, versions, limits) and what is retired

### Takeaway
Nearly every governance and content object can be read through the REST API: sites, projects, workbooks and views, data sources, custom views, users, groups, group sets, permissions and default permissions, schedules and tasks, subscriptions, alerts, favorites, tags, labels, collections, flows, virtual connections with their policies, webhooks, and revisions.

There are four main gaps:
- **Data-driven alert conditions** (operator and threshold) can't be read back.
- **Comments** have no REST endpoint.
- **Embedded credentials** can only be exported encrypted for a Tableau destination.
- **Usage statistics** are thin on Server: only view counts. Rich usage needs the repository on Server, and Admin Insights or the Activity Log on Cloud.

Legacy Metrics and Ask Data lenses are retired (Cloud February 2024, Server 2024.2).

### Cited Findings
#### Master table: object → API → notes and limits

| Object | Read endpoint(s) (classic unless noted) | Since / scope | Notes, limits | Source |
|---|---|---|---|---|
| Server info | `GET /api/{v}/serverinfo` | — | Returns product version and REST API version | [Server](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_server.htm) |
| Sites | `GET /api/{v}/sites`; `GET /sites/{id}` or by name / content URL; `GET /sites/{site}/settings/embedding`; `GET /sites/{site}/settings/notifications`; `GET /sites/{site}/personalSpace`; `GET /sites/{site}/content/recent` | — | Query Sites is not available on Cloud | [Site](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_site.htm); [Notifications](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_notifications.htm) |
| Projects | `GET /projects` | owner settable since 3.21 | Attributes: `parentProjectId` (nesting), owner, `contentPermissions` = `ManagedByOwner` (default) / `LockedToProject` / `LockedToProjectWithoutNested` (3.8+) | [Projects](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_projects.htm) |
| Project permissions and defaults | `GET /projects/{id}/permissions`; `GET /projects/{id}/default-permissions/{workbooks\|datasources\|flows\|metrics\|lenses\|dataroles\|virtualconnections\|databases\|tables}` | "Replace" methods 3.23 | Project capabilities: ProjectLeader, Read, Write | [Permissions](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_permissions.htm) |
| Workbooks | `GET /workbooks`; `GET /workbooks/{id}`; `GET /workbooks/{content-url}` (3.17); `GET /users/{uid}/workbooks`; `GET /workbooks/{id}/connections`; `GET /workbooks/{id}/views` | — | Filter on `updatedAt` for incremental runs | [Workbooks & Views](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm) |
| Workbook file | `GET /workbooks/{id}/content?includeExtract=False` | — | `includeExtract=False` skips extracts on large workbooks; the result is .twb/.twbx | same |
| Workbook renders | `GET /workbooks/{id}/pdf` (filters 3.24; vizWidth/vizHeight 3.26); `GET /workbooks/{id}/powerpoint`; `GET /workbooks/{id}/previewImage` | — | `maxAge` minimum is 1 minute | same; [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm) |
| Workbook revisions | `GET /workbooks/{id}/revisions`; `GET /workbooks/{id}/revisions/{n}/content?includeExtract=` | — | Revision history must be enabled on the site | [Revisions](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_revisions.htm) |
| Workbook encrypted keychain | `POST /workbooks/{id}/retrieveKeychain` | 3.24; scope `tableau:embedded_credentials:download` (3.27) | Can be applied only to the linked Tableau destination (see Q7) | [Workbooks & Views](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm) |
| Views (sheets/dashboards) | `GET /views?includeUsageStatistics=true`; `GET /views/{id}`; Get View by Path; `GET /workbooks/{wid}/views/{vid}/previewImage` | — | Usage statistics return a view count only | same |
| View data | `GET /views/{id}/data?vf_<field>=<value>` (CSV) | 2.8; scope `tableau:views:download` | **Summary data only.** For a dashboard, **only the first view's data** is returned. Cloud caps concurrent long jobs at 20 (429) | same |
| View image | `GET /views/{id}/image?resolution=high&maxAge=&vizHeight=&vizWidth=&format=png\|svg` | svg in 3.29 | `vf_` filters supported | same |
| View PDF / Excel | `GET /views/{id}/pdf?type=&orientation=`; `GET /views/{id}/crosstab/excel` | Excel 3.9-era sample | `vf_` filters supported | same |
| Custom views | `GET /customviews`; `GET /customviews/{id}`; `/customviews/{id}/image`; `/data` (3.24); `/pdf` (3.24); `GET /customviews/{id}/default/users` (3.21) | list/get/image 3.18 | Download and publish of the custom-view definition use an **experimental path** `/api/exp/sites/{site}/customviews/{id}/content` (used by TSC) | [Workbooks & Views](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm); [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm); [TSC custom_views_endpoint.py](https://github.com/tableau/server-client-python/blob/master/tableauserverclient/server/endpoint/custom_views_endpoint.py) |
| Published data sources | `GET /datasources`; `GET /datasources/{id}`; `GET /datasources/{id}/content?includeExtract=`; `GET /datasources/{id}/connections`; `GET /datasources/{id}/revisions` (+`/{n}/content`); `POST /datasources/{id}/retrieveKeychain` | — | Attributes: `isCertified`, `certificationNote`, `hasExtracts`, `encryptExtracts`, `useRemoteQueryAgent` (Bridge), `size`, `webpageUrl`. Connection attributes: `type, serverAddress, serverPort, userName, authenticationType, useOAuthManagedKeychain, queryTaggingEnabled`; **no password** | [Data Sources](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_data_sources.htm) |
| Data-source semantic elements | `GET /api/{v}/tds/sites/{site}/datasource/{luid}` (read columns, field descriptions, table descriptions) | 3.30; Data Management or Tableau+ | Same family as the work-copy edit methods | same |
| Labels, DQW, certification | `GET /dataQualityWarnings/{content-type}/{luid}`; `GET /labels/{label-luid}`; `GET /labelValues`; `GET /labelCategories`; `GET /dataQualityCertifications/{content-type}/{luid}` | labels 3.16 Cloud / 3.17 Server | Labels cover certification, DQW, sensitivity and custom categories | [Metadata Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_metadata.htm); [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm) |
| External assets (Catalog) | `GET /databases`; `GET /tables`; `GET /tables/{id}/columns`; database/table permissions; `POST /databaseAndTables/lookup` (3.13) | Data Management for writes | — | [Metadata Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_metadata.htm) |
| Users | `GET /users`; `GET /users/{id}`; `GET /users/{id}/groups`; `POST /users/{id}/retrieveSavedCreds` (the method page says 3.23; What's New lists it under 3.24); `GET /users/{id}/personal-access-tokens` (Cloud) | — | `siteRole` values: Creator, Explorer, ExplorerCanPublish, ServerAdministrator, SiteAdministratorExplorer, SiteAdministratorCreator, Unlicensed, ReadOnly, Viewer. Other attributes: `authSetting`, `idpConfigurationId`, `email` (Cloud, July 2025), language/locale | [Users & Groups](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_users_and_groups.htm); [Authentication](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_authentication.htm) |
| Groups | `GET /groups` (filter by luid/id, 3.22); `GET /groups/{id}/users` | — | `<domain name="local">` vs an AD domain; AD-synced groups carry `<import domainName siteRole grantLicenseMode="onLogin\|onSync">`; `externalUserEnabled` (Cloud on-demand access) | [Users & Groups](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_users_and_groups.htm) |
| Group sets | `GET /groupsets`; `GET /groupsets/{id}` | 3.22 (Cloud Feb 2024 / Server 2024.2) | Can be permission grantees (grantee type "groupset" in the repository) | same; [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm) |
| Content permissions | `GET /{workbooks\|datasources\|views\|flows\|projects\|virtualconnections\|collections}/{id}/permissions`; lens permissions (retired) | Replace methods 3.23 | Returns explicit grants only; see the capability list below | [Permissions](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_permissions.htm) |
| Server schedules (Server only) | `GET /api/{v}/schedules`; `GET /schedules/{id}`; `GET /sites/{site}/schedules/{id}/extracts` | Not on Cloud | Attributes: `state` (Active/Suspended), `priority`, `type` (Extract/Subscription/Flow), `frequency`, `nextRunAt`. Batch state update 3.27 | [Jobs, Tasks & Schedules](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_jobs_tasks_and_schedules.htm) |
| Extract refresh tasks | `GET /tasks/extractRefreshes`; `GET /tasks/extractRefreshes/{id}` | Cloud request variant 3.20 | Server and Cloud return different payloads. Task `type` = `FullRefresh` or `IncrementalRefresh`; also priority and `consecutiveFailedCount`. On Cloud, frequency details are inline | [Extract & Encryption](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_extract_and_encryption.htm) |
| Flows (Prep) | `GET /flows`; `GET /flows/{id}`; `/content` (.tflx); `/connections`; `/permissions`; `GET /flows/runs`; `GET /tasks/runFlow`; `GET /tasks/linked` (3.15); `POST /flows/{id}/retrieveKeychain` (3.25) | — | Flow capabilities: ChangeHierarchy, ChangePermissions, Delete, Execute, ExportXml, Read, WebAuthoringForFlows, Write | [Flow](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_flow.htm) |
| Jobs | `GET /jobs`; `GET /jobs/{id}` | — | Background job history and status | [Jobs, Tasks & Schedules](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_jobs_tasks_and_schedules.htm) |
| Subscriptions | `GET /subscriptions`; `GET /subscriptions/{id}` | Cloud frequency variant 3.20 | See Q9 | [Subscriptions](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_subscriptions.htm) |
| Data-driven alerts | `GET /dataAlerts`; `GET /dataAlerts/{id}` | Create is Cloud only (3.20) | GET **omits condition and threshold** (see Q9) | [Notifications](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_notifications.htm) |
| Webhooks | `GET /webhooks`; `GET /webhooks/{id}`; `GET /webhooks/{id}/test` | 3.6 | — | same |
| Favorites | `GET /favorites/{user-id}` | — | Must be called per user | [Favorites](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_favorites.htm) |
| Tags | Embedded in item responses; `PUT /{type}/{id}/tags`; batch `tags:batchCreate` / `tags:BatchDelete` | — | Tags exist on workbooks, views, data sources, flows, VCs, databases, tables, columns | [Metadata Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_metadata.htm) |
| Collections | `GET /api/-/collections`; `GET /api/-/collections/{luid}`; `GET /api/-/collections/{luid}/items`; collection permissions | 3.28 (Cloud Feb 2026 / Server 2026.2) | Users see only the items they're permitted to see | [Collections](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_collections.htm) |
| Virtual connections and data policies | `GET /virtualconnections`; `GET /virtualconnections/{id}` (**JSON content incl. policy collection**, 3.23); `GET /virtualconnections/{id}/content` (.tvcx, 3.30); `/connections`; `/revisions`; `/permissions` | Server since 3.19 | The .tvcx contains a .tvc schema (tables, columns, connections, policy collection) plus `Data/Extracts/*.hyper`. Encrypted extracts are decrypted when packaged | [Virtual Connections](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_virtual_connections.htm) |
| Usage statistics | Server: views `includeUsageStatistics`. Cloud only: `GET /api/-/content/usage-stats/{type}/{luid}`; `POST /api/-/content/usage-stats` (batch) | 3.17 Cloud | Usage-stats endpoints are "Not available for Tableau Server" | [Content Exploration](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_content_exploration.htm) |
| Search | `GET /api/-/search`; `GET /api/-/suggestions` | 3.16 / 3.19 | Results are permission-filtered | same |
| Recycle bin | `GET /recyclebin/{content-type}`; `GET /recyclebin/{content-type}/{id}` | 3.29 | — | [Recycle Bin](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_recycle_bin.htm) |
| Bridge | Clients, pools, domains (3.29); routing rules (3.30) | — | Data sources and connections expose `useRemoteQueryAgent` | [Bridge](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_bridge.htm); [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm) |
| Connected apps / EAS | `GET /connected-apps/direct-trust`; `GET /connected-apps/external-authorization-servers` | 3.14 | — | [Connected Apps](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_connected_app.htm) |
| Tableau MCP site settings | `GET /sites/{site}/settings/mcp` | 3.29 | — | [Tableau MCP Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_tableau_mcp.htm) |
| Pulse | `/api/-/pulse/...` (definitions, metrics, subscriptions, alerts, insights) | 3.21+ | "Not available for Tableau Server" | [Pulse](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_pulse.htm) |

#### Permission semantics
- A grant has three parts: a grantee (user, group, or group set, identified by LUID), a capability name, and a mode of `Allow` or `Deny` (case-sensitive). Explicit permissions are listed. Implicit permissions (admin, owner) are **not listed** in query results — [Permissions concepts](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_permissions.htm)
- Capabilities by content type:
  - **Workbook:** AddComment, ChangeHierarchy, ChangePermissions, CreateRefreshMetrics, Delete, ExportData, ExportImage, ExportXml, ExtractRefresh (3.28), Filter, Read, RunExplainData, ShareView, ViewComments, ViewUnderlyingData, WebAuthoring, Write.
  - **Data source:** ChangePermissions, Connect, Delete, ExportXml, ExtractRefresh, Read, Write, SaveAs, and PulseMetricDefine (3.25).
  - **View:** AddComment, ChangePermissions, Delete, ExportData, ExportImage, ExportXml, Filter, Read, ShareView, ViewComments, ViewUnderlyingData, WebAuthoring, Write.
  - **Virtual connection:** Read, Connect, Overwrite, ChangeHierarchy, Delete, ChangePermissions.
  - **Database/table:** Read, Write, ChangePermissions, ChangeHierarchy.
  - Sources: [Permissions Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_permissions.htm); [Metadata Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_metadata.htm); [What's New 3.25/3.28](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm)
- REST names versus UI names: ChangeHierarchy = Move; ChangePermissions = Set Permissions; Execute = Run Flow; ExportData = View Summary Data; ExportXml = Download; ExtractRefresh = Refresh Extracts; Read = View; ShareView = Share Customized; Write = Save; WebAuthoring = Web Edit; **VizqlDataApiAccess = "API Access"**; InheritedProjectLeader and ProjectLeader = Project Leader — [Permissions concepts](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_permissions.htm)
- Locking default permissions with `LockedToProject` applies them to content at publish time, and content owners can't change them. A Project Leader on a parent project gets `InheritedProjectLeader` on child projects even when explicitly denied on the child — [Permissions concepts](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_permissions.htm)

#### Retired, deprecated, and changed items (with dates)
- **Legacy Metrics:** retired in Tableau Cloud February 2024 and Server 2024.2 (REST methods retired in API 3.22). Embedding legacy metrics was retired in October 2023 / Server 2023.3 — [Metrics Methods – Retired](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_metrics.htm)
- **Ask Data and lenses:** retired in Cloud February 2024 and Server 2024.2 (API 3.22) — [Ask Data Lens Methods – Retired](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_ask_data_lens.htm); [Ask Data help](https://help.tableau.com/current/pro/desktop/en-us/ask_data.htm). The Metadata API stopped returning metrics and lens objects at the same time (Cloud February 2024, Server 2024.2) — [Metadata API Release Notes](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_release_notes.html)
- **Dashboard Extensions Settings methods:** retired in 3.21 and replaced by Tableau Extensions Settings methods. **Recommendations** methods were deprecated in 3.19. **Data Acceleration** methods were retired in 3.29. "Add Flow Task to Schedule" was retired for Cloud in 3.22. The per-connection `queryTaggingEnabled` attribute was deprecated in 3.23 — [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm); [Workbooks & Views](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm)

### Inferences
- **Comments are a REST gap.** I found no comments endpoint in the reference pages I extracted. The repository has a `comments` table ([data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm)), and the Migration SDK lists "Comments" as not supported ([planning](https://help.tableau.com/current/api/migration_sdk/en-us/docs/planning.html)). On Server, read comments from the repository. On Cloud, they're probably not extractable.
- **Effective permissions have to be computed by TabFree.** REST returns explicit Allow/Deny per grantee only. TabFree has to reproduce Tableau's evaluation itself: owner and admin implicit grants, Deny-wins rules, project locking, and group-set intersection.
- Hidden worksheets have no REST view (see the Metadata API `Sheet.luid` note in Q4). For hidden sheets, data and image exports aren't possible through REST, so the workbook XML must be parsed.

### Gaps
- I didn't capture the webhook event-name list. The REST page lists the methods, but my extraction didn't pick up the event enumeration.
- I couldn't confirm a REST method that lists **all labels** on a site. Reading per label LUID and the Metadata API `labels`/`upstreamLabels` edges are the confirmed routes.
- Whether group-set membership is an intersection (a user must be in all member groups) wasn't confirmed in the pages I read.

---

## Q3. Tableau tooling: TSC (Python), tabcmd 2.0, Content Migration Tool, Migration SDK — coverage, licenses, reuse

### Takeaway
Use **TSC (MIT)** as the REST client inside the crawler. Treat **tableau-migration-sdk (Apache-2.0, C# with a Python wrapper)** as the reference for ordering, batching, manifests, and transforms, not as a runtime dependency. It exists to publish into Tableau, Server→Cloud and also Server→Server and Cloud→Cloud. **CMT** is a licensed Windows GUI tool and not useful to TabFree. **tabcmd 2.0 (MIT)** is a cross-platform CLI that is handy for ad-hoc exports.

### Cited Findings
- **TSC (tableau/server-client-python):** MIT, latest release v0.42 (2026-10-15). Its endpoint modules cover auth, custom_views, data_alert, databases, datasources, default_permissions, dqw, extensions, favorites, fileuploads, flow_runs, flow_task, flows, groups, groupsets, jobs, linked_tasks, metadata (GraphQL), metrics, oidc, permissions, projects, schedules, server_info, sites, subscriptions, tables, tasks, users, views, virtual_connections, webhooks, and workbooks — [GitHub tree](https://github.com/tableau/server-client-python/tree/master/tableauserverclient/server/endpoint). The workbook and data-source endpoints implement download, revisions, connections, permissions, PDF/PPTX/preview, tags, DQW, and extract operations, but **no keychain methods** — [workbooks_endpoint.py](https://github.com/tableau/server-client-python/blob/master/tableauserverclient/server/endpoint/workbooks_endpoint.py). TSC's custom view download/publish uses `/api/exp/sites/{site}/customviews/{id}/content` — [custom_views_endpoint.py](https://github.com/tableau/server-client-python/blob/master/tableauserverclient/server/endpoint/custom_views_endpoint.py)
- **tabcmd 2.0 (tableau/tabcmd):** MIT, Python, v2.0.21 (2026-07-10). Its commands include login, export, get_url, publish, delete, runschedule, create/refresh/encrypt/decrypt extracts, groups, projects, sites, and users. `export` takes `--pdf`, `--fullpdf`, `--png`, `--csv`, `--pagelayout`, `--pagesize`, `--width`, `--height`, `--filter`, and `--resolution` — [tabcmd repo](https://github.com/tableau/tabcmd); [export_command.py](https://github.com/tableau/tabcmd/blob/main/tabcmd/commands/datasources_and_workbooks/export_command.py)
- **Content Migration Tool (CMT):**
  - "Applies to: Tableau Advanced Management".
  - Copies content between Server sites. Between separate installations it requires user-based licensing.
  - Tableau now says CMT "is not recommended for migrations when moving from Tableau Server to Tableau Cloud … use the Tableau Migration SDK."
  - Sources: [About CMT](https://help.tableau.com/current/server/en-us/cmt-intro.htm)
  - A practitioner notes that CMT "require[s] a very high level of licensing in both of your environments" and that subscriptions, alerts, and user-created custom views don't come over — [Keyrus](https://keyrus.com/us/en/insights/top-10-tableau-cloud-migration-challenges)
- **Migration SDK:**
  - The repo now lives at **github.com/tableau/tableau-migration-sdk**. The `tableau/tableau-migration` path in the brief returned 404 from the GitHub API on 2026-11-20.
  - License Apache-2.0, C#. Distributed on NuGet as `Tableau.Migration` and on PyPI as `tableau_migration` (needs a .NET runtime). Latest release `release/6.1.0` (2026-06-01).
  - Described as "designed for moving from Tableau Server to Tableau Cloud."
  - Sources: [README](https://github.com/tableau/tableau-migration-sdk); [Tableau org repo listing via gh](https://github.com/tableau)
  - Sibling repos: `tableau-migration-app` (Apache-2.0) and `tableau-metadata-explorer` (Apache-2.0) — [Tableau GitHub org](https://github.com/tableau)
- The SDK is "a local application that primarily downloads content … to a local machine and then publishes it", and it "exclusively uses the Tableau REST API" — [Architecture](https://help.tableau.com/current/api/migration_sdk/en-us/docs/architecture.html)
- The SDK has pipelines `ServerToCloudMigrationPipeline`, `ServerToServerMigrationPipeline`, and `CloudToCloudMigrationPipeline`. Server→Cloud content order: **Users → Groups → GroupSets → Projects → DataSources → Flows → Workbooks → ExtractRefreshTasks → CustomViews → Subscriptions → FlowRunTasks → Favorites** — [ServerToCloudMigrationPipeline.cs](https://github.com/tableau/tableau-migration-sdk/blob/main/src/Tableau.Migration/Engine/Pipelines/ServerToCloudMigrationPipeline.cs)
- SDK source areas include Engine/Hooks (filters, mappings, transformers), Engine/Manifest, Engine/Conversion (schedule converters), Api/EmbeddedCredentials, Api/Labels, Api/Tags, Api/Paging, and an Api/Simulation layer — [repo tree](https://github.com/tableau/tableau-migration-sdk/tree/main/src/Tableau.Migration)
- **What the SDK migrates:**
  - Custom views: ownership and default-for users.
  - Data sources: ownership, permissions, tags, labels, embedded credentials except managed OAuth.
  - Workbooks: ownership, permissions, tags, embedded credentials except managed OAuth. Workbook labels are **not** migrated.
  - Groups: membership, except "All Users".
  - Also group sets, projects (permissions, ownership), and favorites (except flow favorites).
  - Extract refresh, subscription, and Prep flow task schedules are converted to Cloud-compatible schedules. Schedule name, priority, execution order, and weekly-multi-day schedules are not carried, and sub-hourly intervals become hourly.
  - Source: [Supported Content Types](https://help.tableau.com/current/api/migration_sdk/en-us/docs/supported_content_types.html)
- **What the SDK does not migrate:** Ask Data lenses, collections, comments, data labels, data roles, data-driven alerts, workbook and data-source descriptions, extensions, external-asset attributes, metrics, personal spaces, revisions, server/site customizations, URL actions (need host rewrites), usage and engagement data, user filters, user settings, and virtual connections and policies — [Planning](https://help.tableau.com/current/api/migration_sdk/en-us/docs/planning.html). The same page tells users to resubscribe, while the Supported Content Types page and the pipeline code include Server→Cloud subscription migration. The docs are internally inconsistent — [Supported Content Types](https://help.tableau.com/current/api/migration_sdk/en-us/docs/supported_content_types.html)
- **Document API (tableau/document-api-python):** MIT and "unsupported". It reads and updates connection info (server, username, database, auth type, connection type) and reads fields, including fields used by sheets, in TWB/TWBX/TDS/TDSX. It can't create files from scratch or update field information — [README](https://github.com/tableau/document-api-python)

### Inferences
- **Reuse:** Apache-2.0 allows TabFree to copy SDK code or patterns with attribution and a NOTICE file. The SDK's destination side is Tableau, so its "publish" half isn't reusable. Worth borrowing: the content order, the hooks model (filters, mappings, transformers), the manifest (per-item status and errors, so runs are resumable and idempotent), the paging helpers, and the schedule-conversion logic (Server schedule → frequency details). A Python crawler on TSC plus our own manifest is likely simpler than embedding a .NET runtime.
- Tableau itself treats revisions, comments, alerts, VCs/policies, user filters, URL actions, and usage history as **non-migratable**. Those are exactly the areas where TabFree will need custom extraction (repository on Server) and custom translation.

### Gaps
- I didn't review the SDK's manifest JSON schema in detail or its licensing NOTICE requirements.
- I didn't check whether TSC ships collections, labels, keychain, or Pulse support in unreleased branches.

---

## Q4. Metadata API (GraphQL): availability, licensing, model, example queries, limits

### Takeaway
The Metadata API (`POST https://<server>/api/metadata/graphql`, using the REST token) is the fastest way to build a lineage inventory: workbooks → sheets/dashboards → fields used → calc formulas → embedded and published data sources → upstream tables, columns, and databases, plus custom SQL text.

On **Tableau Server** it is free but **disabled by default** (`tsm maintenance metadata-services enable`). On **Tableau Cloud**, as of October 2026, it **requires Data Management**; sites already using it without Data Management keep access until June 2027. Limits on Server: 20,000 nodes and 20 seconds per query by default, a 1,000-item page size, and token-bucket throttling.

### Cited Findings
- The endpoint is `http(s)://<server-name>/api/metadata/graphql`, and GraphiQL is at `/metadata/graphiql/`. It authenticates with the REST API token. POST with `application/json`, POST with `application/graphql`, and GET with `?query=` are all accepted — [Get Started](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_start.html)
- **Cloud licensing change:** "Tableau Cloud requires Data Management (or an edition that includes it) to access the Metadata API. This requirement started with Tableau Cloud October 2026. If you were using the Metadata API without Data Management at that time, you can continue doing so until June 2027." "Tableau Server does not [require it]." — [Get Started](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_start.html)
- **Server enablement:** The API is installed but disabled. `tsm maintenance metadata-services enable` starts initial ingestion and restarts some services. Running it again rebuilds the index. While the index builds, queries return `BACKFILL_RUNNING` warnings and results may be incomplete — [Get Started](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_start.html); [Common Errors](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_errors.html)
- **Permissions:** Results follow the View capability. Without Data Management, Server users see external assets (databases, tables) only if the "derived permissions" setting is on. It's on by default with Data Management and must be enabled manually without it. Non-admins may hit `FILTER_REQUIRED` and `PERMISSIONS_MODE_SWITCHED` — [How Permissions Work](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_permissions.html); [Common Errors](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_errors.html)
- **Limits:**
  - `NODE_LIMIT_EXCEEDED` and `TIME_LIMIT_EXCEEDED` return partial results as warnings, not errors — [Common Errors](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_errors.html)
  - Server defaults: `metadata.query.limits.time` = 20 s (recommended maximum 60 s); `metadata.query.limits.count` = 20,000 nodes (recommended maximum 100,000) — [tsm configuration set options](https://help.tableau.com/current/server/en-us/cli_configuration-set_tsm.htm)
  - Throttling: Server 2023.3 and later (`metadata.query.throttling.enabled` true, `queryCostCapacity` 20,000,000, `tokenRefilledPerSecond` 5,555); Cloud since June 2023, not configurable there. Throttled queries get `RATE_EXCEEDED` — [tsm options](https://help.tableau.com/current/server/en-us/cli_configuration-set_tsm.htm); [Common Errors](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_errors.html); [Release Notes](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_release_notes.html)
  - Pagination uses `*Connection` objects with `first` plus `offset`, or with `after` (cursor) and `pageInfo {hasNextPage endCursor}`. The maximum page size is 1,000 (`MAX_PAGE_SIZE_EXCEEDED`), and you can't mix `after` and `offset` — [Example Queries](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_examples.html); [Common Errors](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_errors.html)
- **Model:**
  - Content: published and embedded data sources, workbooks, sheets, dashboards (including stories), fields (calculated, column, group, bin, set, hierarchy, combined, combined set), data-source filters, parameters, flows, virtual connections and VC tables.
  - External assets: databases (including files and WDC) and tables (including custom SQL). "Cubes are not supported."
  - Sources: [Understand the Metadata Model](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_model.html)
- **Object edges** (from the reference pages):
  - `Workbook`: `luid, name, projectLuid, projectName, vizportalUrlId, uri, owner, containsUnsupportedCustomSql, sheets, dashboards, views, embeddedDatasources, parameters, upstreamDatasources, upstreamTables, upstreamDatabases, upstreamVirtualConnections, upstreamFlows, upstreamLabels, upstreamDataQualityWarnings, tags` — [Workbook](https://help.tableau.com/current/api/metadata_api/en-us/reference/workbook.doc.html)
  - `Sheet`: `luid`. Its description reads "Locally unique identifier used for the REST API … (Blank if worksheet is hidden in Workbook)". Also `documentViewId, path, index, containedInDashboards, sheetFieldInstances, worksheetFields, datasourceFields, upstreamFields, upstreamColumns, upstreamTables, upstreamDatasources, upstreamDatabases` — [Sheet](https://help.tableau.com/current/api/metadata_api/en-us/reference/sheet.doc.html)
  - `Dashboard`: `luid, name, path, sheets, upstreamSheetFieldInstances, upstreamFields, upstreamDatasources` — [Dashboard](https://help.tableau.com/current/api/metadata_api/en-us/reference/dashboard.doc.html)
  - `CalculatedField`: `formula, hasUserReference` ("True if field formula … involves a user function (for example, USERNAME or ISMEMBEROF)"), `role, dataType, aggregation, isHidden, fields, referencedByFields, referencedByFilters, directSheets, downstreamSheets, downstreamDashboards` — [CalculatedField](https://help.tableau.com/current/api/metadata_api/en-us/reference/calculatedfield.doc.html)
  - `EmbeddedDatasource`: `hasExtracts, hasUserReference, containsUnsupportedCustomSql, extractLastRefreshTime, extractLastIncrementalUpdateTime, fields, datasourceFilters, parentPublishedDatasources, upstreamTables, upstreamDatabases, upstreamDatasources` — [EmbeddedDatasource](https://help.tableau.com/current/api/metadata_api/en-us/reference/embeddeddatasource.doc.html)
  - `CustomSQLTable`: `query` ("Text of the query"), `connectionType, isUnsupportedCustomSql, database, columns, tables, downstreamWorkbooks, downstreamDatasources` — [CustomSQLTable](https://help.tableau.com/current/api/metadata_api/en-us/reference/customsqltable.doc.html)
  - `Parameter`: `name, parentName, datasource, workbook, referencedByCalculations` — [Parameter](https://help.tableau.com/current/api/metadata_api/en-us/reference/parameter.doc.html)
  - `DatasourceField` (a workbook field pointing to a published data source field): `remoteField, datasource` — [DatasourceField](https://help.tableau.com/current/api/metadata_api/en-us/reference/datasourcefield.doc.html)
- Since Cloud June 2023, fields can be filtered by a word-prefix text filter (`fields(filter: {text: "age"})`). Since 2023.3, Metadata API tokens obtained by connected-app JWT are honored — [Release Notes](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_release_notes.html)

#### Example GraphQL queries for a migration inventory
These use edge names from the reference pages above. The comments mark field names worth re-verifying in GraphiQL. One of Tableau's own examples spells the edge `embeddedDataSources`, while the reference says `embeddedDatasources` — [Example Queries](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_examples.html).

```graphql
# 1) Workbook → sheets/dashboards → fields used → upstream lineage (page with cursors; keep first <= ~50 to stay under 20k nodes)
query WorkbookInventory($after: String) {
  workbooksConnection(first: 25, after: $after, orderBy: {field: NAME, direction: ASC}) {
    pageInfo { hasNextPage endCursor }
    nodes {
      luid name projectLuid projectName vizportalUrlId updatedAt containsUnsupportedCustomSql
      sheets { luid documentViewId name path
               containedInDashboards { luid name }
               sheetFieldInstances { name __typename }
               upstreamFields { name __typename } }
      dashboards { luid name path sheets { luid name } }
      parameters { name }
      embeddedDatasources {
        name hasExtracts hasUserReference containsUnsupportedCustomSql
        parentPublishedDatasources { luid name }          # published-DS dependency
        upstreamTables { name schema database { name connectionType } }   # verify DatabaseTable fields
        fields { name isHidden __typename
                 ... on CalculatedField { formula hasUserReference role dataType } }
      }
    }
  }
}

# 2) Published data sources with lineage + certification (for the semantic layer)
query PublishedDS($after: String) {
  publishedDatasourcesConnection(first: 50, after: $after, orderBy: {field: NAME, direction: ASC}) {
    pageInfo { hasNextPage endCursor }
    nodes { luid name hasExtracts isCertified          # isCertified on PublishedDatasource: verify
            fields { name __typename ... on CalculatedField { formula hasUserReference } }
            upstreamTables { name database { name connectionType } }
            downstreamWorkbooks { luid name } }
  }
}

# 3) Custom SQL inventory (re-pointing to Athena/Redshift needs dialect review)
query CustomSQL($after: String) {
  customSQLTablesConnection(first: 100, after: $after) {     # root field name: verify in GraphiQL
    pageInfo { hasNextPage endCursor }
    nodes { name query connectionType isUnsupportedCustomSql
            database { name connectionType }
            downstreamWorkbooks { luid name } downstreamDatasources { name } }
  }
}
```

### Inferences
- **Server-source crawl:** Enable the Metadata API before the migration window, because the backfill on large estates can take a while. Cap `first` so results stay under 20,000 nodes, and fall back to per-workbook queries filtered by `luid` when you see `NODE_LIMIT_EXCEEDED`. Treat every warning as "partial result, retry narrower."
- **Cloud-source crawl after October 2026:** Without Data Management you can't rely on the Metadata API (unless grandfathered until June 2027). TabFree should treat **workbook/TDS XML parsing** as the primary source of truth for fields, calcs, and connections, and the Metadata API as an accelerator and cross-check.
- `hasUserReference` on calculated fields and embedded data sources is a cheap first-pass RLS detector (see Q8). `containsUnsupportedCustomSql` marks places where lineage, and so automated re-pointing, is unreliable.
- `Sheet.luid` is blank for hidden worksheets. Join Metadata sheets to REST views on `luid` and treat sheets with no luid as hidden, so REST image and data export isn't available for them.

### Gaps
- I didn't confirm whether the root query is named `customSQLTablesConnection` or the `User`/`DatabaseTable` field names (`username`, `schema`, `fullName`). Verify in GraphiQL.
- The Cloud node and time limits aren't published. Only the Server TSM defaults are documented.

---

## Q5. Tableau Server repository (workgroup PostgreSQL) and Tableau Cloud equivalents

### Takeaway
On **Server**, the `workgroup` PostgreSQL repository (port 8060, `readonly` user) gives TabFree what REST doesn't:
- full usage history (`http_requests`, `historical_events`, `views_stats`)
- job history (`background_jobs`)
- the **alert data specification blob**
- subscription targets, including custom views and PDF options
- comments, collections and favorites (`asset_lists`)
- virtual connection data policies as JSON (`querypolicycollection.payload`)
- extract sizes
- raw permission rows (`next_gen_permissions`, `permissions_templates`)

On **Cloud** there is no repository. The substitutes are Admin Insights data sources (90/365 days), the Activity Log (events), TCM Platform Data **entity snapshots** (Parquet tables mirroring core repository tables; Enterprise/Tableau+), and the Cloud-only usage-stats API.

### Cited Findings
- **Repository access:** `tsm data-access repository-access enable --repository-username readonly --repository-password <PASSWORD>`, port 8060 (`pgsql.port`), database `workgroup`, optional SSL. The `tableau` user sees views starting with `_`, and `readonly` sees more tables. "Do not directly modify the repository." — [Collect Data with the Tableau Server Repository](https://help.tableau.com/current/server/en-us/perf_collect_server_repo.htm)
- The data dictionary is "As-Is" support, and "the structure and contents may change without warning" — [About the Data Dictionary](https://help.tableau.com/current/server/en-us/data_dictionary.htm). The 2026.3 version is at [tableau.github.io/tableau-data-dictionary/2026.3](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm), and the repo has no license file ([GitHub](https://github.com/tableau/tableau-data-dictionary)).
- Key tables in the 2026.3 dictionary ([data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm)):
  - `http_requests`: controller, action, http_request_uri, user_id, site_id, currentsheet, vizql_session, created_at/completed_at, status, remote_ip.
  - `historical_events` and `historical_event_types` (name, action_type such as Create/Access), with `hist_*` dimension tables (hist_users, hist_workbooks, hist_views, hist_datasources, hist_tasks, hist_schedules, hist_capabilities, hist_collections, …).
  - `views_stats`: user_id, view_id, nviews (cumulative), time (last access), device_type.
  - `background_jobs`: job_type, job_name, args, notes, started_at/completed_at, finish_code (0 success, 1 failure), priority, title/subtitle, correlation_id, luid.
  - `extracts`: workbook_id/datasource_id, descriptor, size in bytes.
  - `workbooks`: size, revision, `embedded` = "An encrypted keychain holding any credentials stored with the workbook", data_engine_extracts, refreshable_extracts, incrementable_extracts, extracts_refreshed_at.
  - `workbook_versions`: version_number, publisher_id, size, notes.
  - `subscriptions`: subject, user_id, schedule_id, creator_id, data_condition_type, is_refresh_extract_triggered, attach_pdf, attach_image, target_type/target_id, subscription_message_id, last_sent.
  - `subscriptions_views` / `subscriptions_workbooks` (repository_url), `subscriptions_customized_views`, `pdf_subscription_options` (page size, orientation), `subscription_messages`.
  - `data_alerts`: title, creator_id, workbook_id, view_id, customized_view_id, **`data_specification` = "Platform-parsable blob that describes which data to track"**, notification_interval_in_minutes, suspend_state, device_type, public.
  - `data_alerts_recipients`: recipient_id, recipient_type (user or group), last_sent, last_state.
  - `tasks`: schedule_id, type, obj_id/obj_type, priority, args (YAML), state, consecutive_failure_count, historical_run_time.
  - `schedules`: schedule_type 0–3 = Hourly/Daily/Weekly/Monthly, day_of_week_mask, day_of_month_mask, start_at_minute, minute_interval, end_at_minute, scheduled_action (0 Extracts, 1 Subscriptions, …), timezoneid, priority, serial_collection_id.
  - `next_gen_permissions` / `permissions_templates`: grantee_type user/group/groupset; `permission` integer 1–4 combining Allow/Deny and grantee/owner semantics; `container_type`/`container_id` for project defaults.
  - `capabilities`: name, display_name.
  - `projects`: `controlled_permissions_enabled`, `nested_projects_permissions_included`, `controlling_permissions_project_id`, `parent_project_id`.
  - `groups`: domain_id, minimum_site_role, grant_license_mode, last_synchronized.
  - `groupsets` and `groupset_groups`.
  - `customized_views`: public, start_view_id, hidden, accessed_at; also `user_default_customized_views`.
  - `asset_lists` (list_type collection or favorites, visibility) and `asset_list_items`.
  - `comments` (comment text, commentable_id = view).
  - `content_usage`.
  - `data_connections`: dbclass incl. **`sqlproxy`**, server, port, dbname, username, `password` (boolean "password is embedded"), `keychain`, authentication, using_remote_query_agent, has_extract.
  - `published_connections` (virtual connections) and `querypolicycollection` (**payload = "JSON blob representing the policy collection"**).
  - `system_users` (email, auth_user_id, domain_id, encrypted keychain).
- **Admin Insights (Cloud):** a Cloud-only project of curated data sources: **TS Events, TS Users, Groups, Site Content, Viz Load Times, Job Performance, Permissions, Subscriptions, Tokens**. Retention is up to 90 days by default and 365 days with Advanced Management, refreshed daily or weekly — [Manage Admin Insights](https://help.tableau.com/current/online/en-us/adminview_insights_manage.htm); [Use Admin Insights](https://help.tableau.com/current/online/en-us/adminview_insights.htm)
- **Activity Log (Cloud):** "Applies to: Tableau Advanced Management". JSON events for tenant (TCM) and site levels. Site events come through the TCM REST API (`GET https://cloudmanager.tableau.com/api/v1/tenants/{tenantId}/sites/{siteId}/activitylog`) or are delivered to a customer S3 bucket with a KMS key. Default tier: daily batches, 14-day retention. Advanced Management / Enterprise / Tableau+: under 15 minutes, 365 days. The feature moved to Hyperforce between March 16 and 31, 2026 — [Activity Log](https://help.tableau.com/current/online/en-us/activity_log_overview.htm); [Platform Data](https://help.tableau.com/current/api/cloud-manager/en-us/docs/platform_data.html). Event types include permission create/delete events, `content_owner_change`, group/group-set membership, `hist_access_view`, `hist_access_datasource`, `hist_export_summary_data`, `hist_download_workbook`, `hist_login_with_pat`, and many `hist_*` create/delete events — [Site Event Type Reference](https://help.tableau.com/current/online/en-us/activity_log_schema.htm)
- **Activity Log (Server, Advanced Management):** events are written locally to `vizportal` and `vizqlserver` log folders as `<process>-cepp-canonical-events_*.log` — [Activity Log (Server)](https://help.tableau.com/current/server/en-us/activity_log_overview.htm)
- **TCM Platform Data entity snapshots (Cloud, Enterprise/Tableau+):**
  - "Point-in-time snapshots … such as workbooks, data sources, published connections, users, and groups", stored as **Parquet**.
  - Endpoint: `GET https://cloudmanager.tableau.com/api/v1/tenants/{tenantId}/sites/{siteId}/snapshots?startTime=&endTime=&entityType=`. Start time must be no more than 14 days in the past.
  - Sources: [Platform Data](https://help.tableau.com/current/api/cloud-manager/en-us/docs/platform_data.html)
  - Entity tables: capabilities, data_connections, datasources, flows, group_users, groups, next_gen_permissions, projects, projects_contents, published_connection(s)/revisions/tables, schedules, subscriptions, system_users, taggings, tags, tasks, users, views, workbooks — [Entity Snapshots Reference](https://help.tableau.com/current/online/en-us/activity_log_entity_snapshots_reference.htm)

### Inferences
- **Server:** Make a read-only repository connection an optional but recommended crawler input. Without it, TabFree can't recover alert conditions, comments, full usage history (for complexity and retirement scoring), or subscription-to-custom-view links. Also pin queries to a dictionary version, because the schema changes between releases.
- **Cloud (Enterprise/Tableau+):** entity snapshots give a repository-like bulk inventory in Parquet. They are cheap to load into Athena, which is itself a natural fit for TabFree on AWS. For usage, combine the Admin Insights "TS Events" data (downloadable as a published data source, or queryable through VDS) with Activity Log events.
- `data_alerts.data_specification` is undocumented ("platform-parsable blob"). Decoding it means reverse-engineering, with a fallback of asking alert owners to re-enter conditions.

### Gaps
- The format of `data_specification`, `subscriptions.data_condition_type` values, and the `next_gen_permissions.permission` 1–4 bitmap semantics aren't documented beyond the one-line descriptions.
- The Cloud entity-snapshot column schemas weren't reviewed. Only the table list was captured.

---

## Q6. VizQL Data Service (VDS) and the official Tableau MCP server: snapshotting data and validating migrated results

### Takeaway
VDS (`POST /api/v1/vizql-data-service/query-datasource` and `/read-metadata`) is available on Cloud and on **Server 2025.1+**. It queries **published** (and composable) data sources, and since 2026.2 also workbook data sources within an interactive session. That makes it the best tool for **ground-truth result sets to validate** TabFree's re-pointed Athena/Redshift queries.

It's a poor bulk-snapshot tool because of usage caps (100 queries per hour per Creator on Cloud) and unsupported calc types. For bulk data, download extracts (.hyper) instead. Tableau MCP (Apache-2.0) wraps REST, VDS, and Pulse as agent tools. It's useful for agent-driven inventory and QA, but it adds nothing the APIs don't already offer.

### Cited Findings
- VDS works "only … with published data sources, composable data sources, or with workbook data sources in an interactive session". Endpoints are `POST https://{pod}.online.tableau.com/api/v1/vizql-data-service/query-datasource` and `/read-metadata`. "To use VDS with Tableau Server, you must be on Tableau Server 2025.1 or later." — [VDS Introduction](https://help.tableau.com/current/api/vizql-data-service/en-us/index.html)
- Permissions: the **API Access** capability (`VizqlDataApiAccess`) is required. For a workbook data source, View, API Access, and Full Data Query on the workbook, plus View, API Access, and Connect on any upstream published data source — [VDS Configuration](https://help.tableau.com/current/api/vizql-data-service/en-us/docs/vds_configuration.html); [Permissions concepts](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_permissions.htm)
- **Limitations:**
  - Not supported: spatial calcs, SCRIPT_* (R/Python), analytics extensions, pass-through RAWSQL, fiscal-date calcs, COUNT(table), sets and combined fields, cube data sources, bins/groups in filters, and date-time aggregations such as HOUR/MINUTE.
  - Usage is capped at 100 queries per hour per Creator license on a site.
  - Before 2026.1, responses were limited to 1 GB. From 2026.1 there is no size limit (streaming), with a 30-minute query timeout.
  - Source: [VDS Limitations](https://help.tableau.com/current/api/vizql-data-service/en-us/docs/vds_limitations.html)
- **Features by release:**
  - 2025.2: multiple credentials, Python SDK.
  - 2025.3: read-metadata returns groups, bins, parameters, column class, and **calculation formulas**; table calcs (quick, custom, nested); data-source model (logical tables and relationships).
  - 2026.1: SSE streaming, row limits, aliases, hidden fields, number formats, field descriptions, LOD indicator, groups in queries, list supported functions.
  - 2026.2: query workbook data sources (embedded) in a session; composable data sources.
  - Source: [VDS What's New](https://help.tableau.com/current/api/vizql-data-service/en-us/docs/vds_whats_new.html)
- The VDS Python sample repo `tableau/VizQL-Data-Service` is Apache-2.0 — [GitHub](https://github.com/tableau/VizQL-Data-Service)
- **Tableau MCP (`tableau/tableau-mcp`):**
  - Apache-2.0, TypeScript, v6.0.4 (2026-11-09).
  - Hosted at `https://mcp.tableau.com` with OAuth 2.1 for Cloud. Server customers self-host (`npx @tableau/mcp-server` with SERVER, SITE_NAME, PAT_NAME, PAT_VALUE).
  - A separate Desktop authoring server build also exists.
  - Sources: [README](https://github.com/tableau/tableau-mcp)
  - Web tools include `list-workbooks, get-workbook, download-workbook, list-views, get-view, get-view-data, get-view-image, list-custom-views, get-custom-view-data, get-custom-view-image, list-datasources, get-datasource-metadata, query-datasource, list-projects, list-users, list-jobs, list-flows, list-extract-refresh-tasks, search-content, query-admin-insights`, Pulse tools, knowledge-context tools, and publish tools — [toolName.ts](https://github.com/tableau/tableau-mcp/blob/main/src/tools/web/toolName.ts)
  - Desktop tools include `getXml`, `validateWorkbookXml`, and `exportWorksheetImage` — [tableau-mcp src tree](https://github.com/tableau/tableau-mcp/tree/main/src/tools/desktop)
  - Per-site MCP configuration over REST arrived in 3.29 — [Tableau MCP Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_tableau_mcp.htm)

### Inferences
- **Validation harness:**
  1. For each migrated dashboard, generate a canonical query: dimensions, measures with aggregation, and filters taken from the workbook XML or Metadata API.
  2. Run it through VDS against the source published data source.
  3. Run the equivalent SQL on Athena/Redshift.
  4. Diff the two with tolerances.
  - VDS's read-metadata (formulas, LOD flag, model) also helps translate calcs.
- **Per-sheet validation:** Use Query View Data (CSV of summary data, with `vf_` filters) as a cheap check for workbook-embedded sources on servers older than 2026.2. It returns only the first view for dashboards, so call it per worksheet view.
- **RLS validation:** On Server, sign in as an admin and impersonate a sample user to capture what each persona sees, through view data or VDS. This is the only reliable oracle for user filters.
- **Bulk data movement:** Use `includeExtract=True` downloads (.tdsx/.twbx contain .hyper) or the VC .tvcx extracts, plus the Hyper API, not VDS.

### Gaps
- I didn't verify whether Query View Data/Image calls count against Cloud's 600 requests/hour/user visualization rate.
- I didn't verify Hyper API export formats (for example Parquet via COPY TO). The `tableau/hyper-db` docs check found no matching file.

---

## Q7. Credentials: can embedded passwords or OAuth tokens be exported, and how to re-map connections to Athena/Redshift

### Takeaway
No. Embedded credentials can only be exported as an **encrypted keychain bound to a specific Tableau destination**: the destination's public key, URL, and site LUID, authorized by the Tableau admin, for Tableau→Tableau migration. Managed OAuth credentials aren't migratable even then. TabFree has to capture connection *metadata* (class, server, port, database, username, auth type, whether a password is embedded, Bridge use, custom SQL, initial SQL) and re-provision credentials natively in AWS.

### Cited Findings
- **Download Workbook Encrypted Keychain** (`POST /workbooks/{id}/retrieveKeychain`): "Gets an encrypted version of the embedded credentials … These credentials can only be migrated to the linked destination Tableau Cloud or Tableau Server." Public and private keys must be created first. The request body names `destinationSiteUrlNamespace`, `destinationSiteLuid`, and `destinationServerUrl`. Scope is `tableau:embedded_credentials:download` — [Workbooks & Views](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm). Equivalent methods exist for data sources and flows (flows in 3.25), and user saved credentials come through `retrieveSavedCreds` (3.23) — [Data Sources](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_data_sources.htm); [Users & Groups](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_users_and_groups.htm); [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm)
- **CMT credential migration:**
  - Server→Cloud only, and both sides need Advanced Management.
  - A Cloud site admin generates a key pair. A TSM admin runs `tsm security authorize-credential-migration … --destination-public-encryption-key <key>`; authorization expires in 7 days by default.
  - The content manifest is encrypted. OAuth connections aren't supported.
  - Source: [Migrate Workbooks and Data Sources with Embedded Credentials](https://help.tableau.com/current/server/en-us/cmt-migration_embedded_credentials.htm)
- The Migration SDK migrates embedded credentials "except for managed OAuth embedded credentials" — [Supported Content Types](https://help.tableau.com/current/api/migration_sdk/en-us/docs/supported_content_types.html)
- **Repository:** `workbooks.embedded` is "an encrypted keychain". `data_connections.password` is only a boolean, and `data_connections.keychain` is an encoded attribute bag. `system_users.keychain` is encrypted — [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm)
- **Connection metadata available through REST:** `type, serverAddress, serverPort, userName, authenticationType, useOAuthManagedKeychain, queryTaggingEnabled` per connection, and `useRemoteQueryAgent` (Bridge) per data source — [Data Sources](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_data_sources.htm). The repository `data_connections.dbclass` includes `sqlproxy`, which marks references to published data sources — [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm)
- The Document API can read and update server, username, and database in TWB/TDS files — [document-api-python](https://github.com/tableau/document-api-python)
- Initial SQL may hold `[TableauServerUser]`, `[TableauServerUserFull]`, `[TableauApp]`, `[TableauVersion]`, and `[WorkbookName]` parameters, which are used for impersonation and database RLS — [Run Initial SQL](https://help.tableau.com/current/pro/desktop/en-us/connect_basic_initialsql.htm)
- Practitioner guides confirm that embedded passwords must be re-entered after a move, and that IP allowlists and Bridge for private data need re-planning — [Keyrus](https://keyrus.com/us/en/insights/top-10-tableau-cloud-migration-challenges)

### Inferences
- **Connection re-mapping recipe:**
  1. Inventory every connection: REST connections, plus XML `<connection class=... server=... dbname=... schema=...>`, `<relation type='table'|'text'>` (custom SQL), and `one-time-sql` (initial SQL).
  2. Build a mapping table of source (class, server, db, schema, table) → target (Athena catalog.db.table or Redshift db.schema.table).
  3. Translate custom SQL dialects, for example T-SQL/Oracle → Trino (Athena) or Redshift SQL.
  4. Use IAM roles, Secrets Manager, or Redshift IAM auth in place of embedded passwords.
  - For `sqlproxy` (published data source) connections, resolve to the TabFree semantic model migrated from that data source.
- **Extract-only data sources** (Excel/CSV/Hyper-only with no live source) must be materialized from the extract into S3/Athena. **Bridge/live-only private sources** need network connectivity planning on AWS, flagged by `useRemoteQueryAgent`.
- Don't call `retrieveKeychain`. Its output is only usable by Tableau and requires destination-side keys that TabFree can't legitimately obtain.

### Gaps
- I didn't establish how `.twb` XML encodes OAuth-managed versus password connections.

---

## Q8. Row-level security: detecting each Tableau RLS style via API/XML and translating it into a policy model

### Takeaway
Tableau has four RLS styles:
1. Manual **user filters** (per workbook or data source mappings of user → values).
2. **Dynamic calcs** using user functions (USERNAME, ISMEMBEROF, …) against entitlement tables.
3. **Virtual-connection data policies** (Data Management, 2021.4+).
4. **Database-native RLS** driven by **initial SQL** (`[TableauServerUser]`).

All four can be detected: through the Metadata API `hasUserReference`, through XML calc formulas and data-source filters, through VC JSON policy collections (REST 3.23+, or repository `querypolicycollection`), and through initial SQL in connection XML. They translate naturally into a single TabFree policy model: subject attributes (user, groups, attributes) → predicate over policy columns, optionally joined to entitlement tables.

### Cited Findings
- The four Tableau RLS options: manual user filter (simple, high maintenance, per workbook; permissions must stop users from downloading or editing to strip the filter), dynamic user filter with a security field and USERNAME(), data policy on virtual connections (Data Management, Creator, enforced server-side for every query, recommended by Tableau), and existing database RLS — [Overview of Row-Level Security Options](https://help.tableau.com/current/server/en-us/rls_options_overview.htm)
- User functions: FULLNAME(), ISFULLNAME, ISMEMBEROF, ISUSERNAME, USERDOMAIN(), USERNAME(), **USERATTRIBUTE, USERATTRIBUTEINCLUDES**. The user-attribute functions need attributes passed at runtime through a JWT (connected apps on Cloud and on Server 2025.1+, UAT from December 2025) or through SAML/OIDC claims. They can't be used with Bridge, and they return NULL/FALSE in authoring previews — [User Functions](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_user.htm). The Update Site `attributeCaptureEnabled` setting enables them (Server embedding 3.25; Cloud SSO 3.27) — [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm)
- **Data policy concepts:** policy table (filtered fact table), policy column, entitlement table (a policy column plus a column mapped to the policy table), and policy condition (an expression evaluated per row; rows are shown when it is TRUE). Policies don't override Tableau permissions; they apply after them — [About Virtual Connections and Data Policies](https://help.tableau.com/current/online/en-us/dm_vconn_overview.htm)
- **VC export:**
  - JSON download (3.23) returns the VC schema rendered as JSON.
  - The .tvcx (3.30) contains the `.tvc` schema with "tables, columns, connections, and policy collection".
  - Sources: [Virtual Connections Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_virtual_connections.htm)
  - The repository stores `querypolicycollection.payload` as a "JSON blob representing the policy collection" — [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm)
- **Metadata API:** `CalculatedField.hasUserReference` and `EmbeddedDatasource.hasUserReference` are "True if … formula … involves a user function (for example, USERNAME or ISMEMBEROF)". `CalculatedField.formula` holds the text, and `referencedByFilters` shows filter use — [CalculatedField](https://help.tableau.com/current/api/metadata_api/en-us/reference/calculatedfield.doc.html); [EmbeddedDatasource](https://help.tableau.com/current/api/metadata_api/en-us/reference/embeddeddatasource.doc.html)
- **Initial SQL:** `[TableauServerUser]` and `[TableauServerUserFull]` support impersonation and database RLS (for example Oracle VPD) — [Run Initial SQL](https://help.tableau.com/current/pro/desktop/en-us/connect_basic_initialsql.htm)
- Tableau's own migration guidance: "User filters: Review the destination row-level-security approach, then recreate or update filters to reference Cloud users, groups, or entitlement data. Validate access with representative user identities." The same guidance applies to VCs: "Update user or group references when source identities differ" — [Migration SDK Planning](https://help.tableau.com/current/api/migration_sdk/en-us/docs/planning.html)

### Inferences
- **Detection pipeline:**
  1. Metadata API: list calcs and data sources with `hasUserReference=true`.
  2. Parse workbook and TDS XML for calcs whose formulas contain user functions. Record where each calc is used: data-source filters (strongest signal of RLS), context or worksheet filters, or only display.
  3. Detect manual user filters, which are sets/groups mapping usernames to members. The XML shape wasn't verified (see Gaps).
  4. Read VC JSON policy collections.
  5. Scan connection `one-time-sql` for `[TableauServerUser*]`.
  6. Note published data sources whose RLS sits in data-source filters (it then applies to all downstream workbooks).
- **Translation model (TabFree):**
  - `Policy { scope: dataset/table; subject_expr: user | group | attribute; predicate: SQL over policy columns; entitlement_join?: (entitlement_table, key mapping); applies_to: [semantic models] }`.
  - Map USERNAME() → `current_user.username` (normalize domain via USERDOMAIN/FULLNAME), ISMEMBEROF('g') → `'g' IN current_user.groups`, USERATTRIBUTE('k') → IdP claim `k`.
  - Candidate enforcement points: TabFree's query layer injecting predicates, or AWS-native Redshift RLS and Lake Formation row filters for Athena. These AWS features weren't verified in this research.
- Because RLS outcomes depend on identity mapping, user and group identity reconciliation (AD domain, `authSetting`, email) must be done **before** policy translation. Validate with impersonated per-persona results captured from the source (see Q6).

### Gaps
- I didn't verify the exact XML representation of manual user filters ("Create User Filter" sets) or the JSON schema of VC policy collections.
- I didn't research Redshift and Lake Formation RLS feature details.

---

## Q9. Subscriptions, data-driven alerts, schedules: what is extractable to recreate them

### Takeaway
**Subscriptions** are fully readable through REST: subject, message, content type and id, attachments (image, PDF, page options), the send-if-empty flag, suspended state, recipient user, and the Server schedule id or Cloud frequency details. Custom-view targets and data-condition flags need the repository.

**Schedules:**
- On Server: global, read through `GET /api/{v}/schedules`, with priority and execution order.
- On Cloud: per-task frequency details.

**Extract refresh tasks** expose Full versus Incremental.

**Data-driven alerts** are only partly readable. REST gives subject, frequency, public flag, owner, view/workbook/project, and recipients, but **not the condition or threshold**; on Server those live in the repository `data_alerts.data_specification` blob.

### Cited Findings
- **List Subscriptions** returns `subject, message, attachImage, attachPdf, suspended, pageOrientation, pageSizeOption`, `<content id type="View|Workbook" sendIfViewEmpty>`, `<schedule id name>` (Server) or `<schedule frequency>` with `<frequencyDetails start end><intervals>…` (Cloud), and `<user id name>` (one recipient per subscription). Cloud-specific create/get/update variants arrived in 3.20 — [Subscriptions Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_subscriptions.htm); [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm)
- Repository subscription detail: `subscriptions.data_condition_type`, `is_refresh_extract_triggered`, `target_type/target_id`, `creator_id`, `subscription_message_id`. Also `subscriptions_customized_views` (subscriptions to custom views), `pdf_subscription_options`, and `subscriptions_views/workbooks` (repository_url) — [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm)
- **Data-driven alerts:** `GET /dataAlerts/{id}` returns `subject, creatorId, createdAt, updatedAt, frequency, public, owner, view{workbook, project}, recipients`. **Create Data Driven Alert** (Cloud only, 3.20) accepts `alertCondition` (above, above-equal, below, below-equal, equal), `alertThreshold`, `frequency` (once, frequently, hourly, daily, weekly…), `visibility`, `device`, `worksheetName` or `customViewId`, and `subject` — [Notifications Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_notifications.htm). In the repository: `data_alerts.data_specification` ("platform-parsable blob that describes which data to track"), `notification_interval_in_minutes`, `suspend_state`, and `customized_view_id`. Recipients carry `recipient_type` user or group — [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm)
- **Server schedules:** `GET /api/{v}/schedules` (not on Cloud) returns `state, priority, type (Extract/Subscription/Flow), frequency, nextRunAt` and frequency details. `GET /sites/{site}/schedules/{id}/extracts` lists the tasks on a schedule — [Jobs, Tasks & Schedules](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_jobs_tasks_and_schedules.htm). In the repository: `schedules.schedule_type` (Hourly/Daily/Weekly/Monthly), day masks, start/end minute, `minute_interval`, `timezoneid`, `scheduled_action`, and `serial_collection_id` (serial execution) — [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm)
- **Extract refresh tasks:** `type="FullRefresh"|"IncrementalRefresh"`, `priority`, `consecutiveFailedCount`, a target workbook or data source, and the schedule (Server) or inline frequency (Cloud). VC tasks can do table-level incremental refreshes — [Extract & Encryption](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_extract_and_encryption.htm). Since 3.28, ad-hoc "Update Now" can request `incremental="true"` — [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm)
- **Flow schedules:** `GET /tasks/runFlow`; linked tasks (chained flows) through `GET /tasks/linked` — [Flow Methods](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_flow.htm)
- Server→Cloud schedule conversion caveats from the Migration SDK: set time zones explicitly. Sub-hourly intervals become hourly. Names, priority, execution order, and weekly-multi-day schedules aren't carried — [Supported Content Types](https://help.tableau.com/current/api/migration_sdk/en-us/docs/supported_content_types.html)

### Inferences
- **TabFree schedule model:** store an RRULE plus an IANA timezone (from `schedules.timezoneid` on Server) and keep priority and serial or parallel execution, since TabFree isn't bound by Cloud's restrictions. Map Full and Incremental refresh to the TabFree data-refresh job type. Incremental needs the extract's incremental key column, which comes from data-source XML.
- **Subscriptions:** recreate per recipient user. Group subscriptions appear as individual user rows in the API (one `<user>` per subscription). Keep `sendIfViewEmpty`, the attachment formats, and the custom-view target (from the repository on Server).
- **Alerts on Server:** decode `data_specification` where possible. Otherwise migrate alerts as "pending configuration" with owner, recipients, view, and frequency pre-filled.

### Gaps
- `data_condition_type` values and the alert `data_specification` format are undocumented.
- I didn't confirm whether subscribing a group creates per-user subscription rows or a group-level row.

---

## Q10. Migration crawler design: order of operations, idempotency, scale, multi-site, inventory and complexity scoring, known pitfalls

### Takeaway
Use a two-phase, manifest-driven, resumable crawler:
- **Discover:** cheap list and metadata calls plus Metadata API lineage, giving an inventory and a dependency graph.
- **Extract:** downloads, renders, and data, in dependency order.

Order the work identity first, then containers, then data sources, then workbooks, then tasks and notifications, then user-level items. This mirrors the Migration SDK pipeline. Key every object by `(server, site LUID, object LUID, updatedAt/revision)` so runs are idempotent and incremental. Throttle to each platform's documented limits. Score each workbook's complexity from features that are known to be hard to translate (LOD, table calcs, parameters, actions, blends, custom SQL, user functions, extensions, R/Python/spatial) together with usage.

### Cited Findings
- **Tableau's own dependency order** (Migration SDK, Server→Cloud): Users → Groups → GroupSets → Projects → DataSources → Flows → Workbooks → ExtractRefreshTasks → CustomViews → Subscriptions → FlowRunTasks → Favorites — [ServerToCloudMigrationPipeline.cs](https://github.com/tableau/tableau-migration-sdk/blob/main/src/Tableau.Migration/Engine/Pipelines/ServerToCloudMigrationPipeline.cs)
- **Platform limits to design around:**
  - PATs don't allow concurrent sessions; connected-app JWTs do — [concepts_auth](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_auth.htm)
  - Tokens expire after 240 minutes by default — [concepts_auth](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_auth.htm)
  - Cloud allows 20 concurrent long-running requests (429) — [Workbooks & Views](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm)
  - Cloud visualization limit is 600 requests per hour per user — [Site Capacity](https://help.tableau.com/current/online/en-us/to_site_capacity.htm)
  - Metadata API: 20,000 nodes, 20 s, 1,000 per page, throttling — [tsm options](https://help.tableau.com/current/server/en-us/cli_configuration-set_tsm.htm); [Example Queries](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_examples.html)
  - VDS: 100 queries per hour per Creator — [VDS Limitations](https://help.tableau.com/current/api/vizql-data-service/en-us/docs/vds_limitations.html)
- **Size and performance levers:** `includeExtract=False` for "workbooks or data sources that have large extracts" — [Workbooks & Views](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm). Extract sizes per workbook or data source come from the repository `extracts.size` and `workbooks.size` — [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm). Incremental discovery can filter on `updatedAt:gte:<ts>` — [Filtering and Sorting](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_filtering_and_sorting.htm)
- **Published data source dependencies:**
  - Metadata `EmbeddedDatasource.parentPublishedDatasources` and `DatasourceField.remoteField` — [EmbeddedDatasource](https://help.tableau.com/current/api/metadata_api/en-us/reference/embeddeddatasource.doc.html); [DatasourceField](https://help.tableau.com/current/api/metadata_api/en-us/reference/datasourcefield.doc.html)
  - Repository `dbclass = sqlproxy` — [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm)
- **Bridge and live-only sources:** `useRemoteQueryAgent` on data sources and `using_remote_query_agent` on connections — [Data Sources](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_data_sources.htm); [data dictionary](https://tableau.github.io/tableau-data-dictionary/2026.3/data_dictionary.htm). Bridge clients and pools are readable from 3.29 — [What's New](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_whats_new.htm)
- **Calc features Tableau's own query service can't handle**, a useful "hard to translate" list: spatial, SCRIPT_*, analytics extensions, RAWSQL, fiscal dates, COUNT(table), sets, combined fields, groups referenced in calcs, date-time HOUR/MINUTE aggregations — [VDS Limitations](https://help.tableau.com/current/api/vizql-data-service/en-us/docs/vds_limitations.html)
- **Known pitfalls, Server→Cloud and Server→anything:**
  - URL actions referencing Server host names. Usage history not migratable. Revisions, comments, alerts, collections, labels, descriptions, data roles, and VCs/policies need manual recreation. Personal spaces. Extensions need review — [Migration SDK Planning](https://help.tableau.com/current/api/migration_sdk/en-us/docs/planning.html)
  - Legacy .tde extracts need conversion to Hyper. Custom REST, Hyper, and Metadata scripts need porting. IP allowlists. Embedded-content URL and authentication changes. Bridge for private data. Subscriptions, alerts, and custom views not moved by CMT — [Keyrus](https://keyrus.com/us/en/insights/top-10-tableau-cloud-migration-challenges)
  - Owner mapping matters: the SDK migrates ownership, and migration of users, groups, and group sets precedes content — [Supported Content Types](https://help.tableau.com/current/api/migration_sdk/en-us/docs/supported_content_types.html)
  - `containsUnsupportedCustomSql` means "lineage may be incomplete" — [Workbook](https://help.tableau.com/current/api/metadata_api/en-us/reference/workbook.doc.html)
- **Pitfalls, Tableau→other BI** (practitioner and vendor blogs found via search; moderate reliability):
  - LOD expressions (FIXED/INCLUDE/EXCLUDE) are consistently named the hardest part. Table calculations (RANK, RUNNING_SUM) have no native equivalent. String parameters need workarounds (disconnected tables).
  - Calculation dependency order matters; rebuilding out of order causes cascading failures.
  - Sources recommend redesign over pixel-perfect replication, and a semantic-layer-first approach.
  - Sources: [Microsoft Tech Community – semantic-layer-first approach](https://techcommunity.microsoft.com/t5/analytics-on-azure-blog/tableau-to-power-bi-semantic-layer-first-approach-for-cloud/ba-p/4481009); [The Data School – parameters don't translate directly](https://www.thedataschool.co.uk/zainul-abedin-natha/power-bi-migration-part-3-why-tableau-parameters-dont-translate-directly-to-power-bi/); [The Data School – lessons rebuilding dashboards](https://www.thedataschool.co.uk/zainul-abedin-natha/tableau-10)

### Inferences
**Recommended order of operations (per source server; parallel per site):**
1. **Preflight:**
   - `serverinfo` sets version feature flags.
   - Auth plan: connected app or PAT pool per site.
   - Check whether the Metadata API is enabled (Server) or licensed (Cloud after October 2026).
   - Check repository access (Server) or entity-snapshot access (Cloud).
   - Record sites: Query Sites on Server; a configured list on Cloud.
2. **Identity:** users (site role, auth setting, IdP id, email, lastLogin), groups (local or AD domain, minimum site role, grant mode, members), group sets. Flag users with role Unlicensed, deleted owners, and users with no recent login. Build the identity-mapping table (Tableau user → TabFree/IdP principal) **before** any permission translation.
3. **Containers and governance:** projects (tree, owner, contentPermissions mode), project permissions, default permissions for every content type, collections (3.28+/2026.2+), tags, labels, DQW, certifications.
4. **Data layer:** virtual connections (JSON with policies), published data sources (metadata, connections, permissions, revisions list, `.tdsx` without extract first, then extracts selectively), flows (`.tflx`, connections, tasks), and Metadata API lineage (published data sources, custom SQL, tables, databases).
5. **Presentation layer:** workbooks (metadata, connections, permissions, `.twb(x)` with `includeExtract=False`), views (usage stats, permissions), and per-view renders (PNG/SVG for AI re-authoring, CSV summary data for validation baselines, PDF for visual diffs). Custom views and their default-for users.
6. **Operations:** schedules (Server), extract refresh tasks (Full/Incremental), flow tasks and linked tasks, subscriptions, data-driven alerts (REST plus repository), webhooks, favorites (per user), and usage history (repository on Server; Admin Insights, Activity Log, and usage-stats API on Cloud).
7. **Report:** inventory plus dependency graph plus complexity scores plus gaps list.

**Idempotency and scale:**
- Use a manifest DB (SQLite or Postgres/DynamoDB). Key each object on (source_url, site_luid, type, luid), storing `updatedAt`/revision, a content hash of the downloaded file, an extraction status per artifact, and the error.
- Re-runs skip unchanged items (same `updatedAt` and hash). Use exponential backoff on 429 and 5xx. Use per-site worker pools sized to platform limits, and stream large downloads to S3 with multipart uploads.
- For thousands of workbooks: discovery is O(pages) list calls. Downloads without extracts are fast. Pull extracts only for data sources being materialized into S3. Renders are the bottleneck, so make them optional, prioritized by usage, and capped per hour.

**Orphans and edge cases:**
- Content whose owner is deleted or unlicensed: reassign to a configured fallback owner and flag it in the report.
- Hidden sheets (no view LUID).
- Workbooks connected to published data sources on another site: these can't be resolved in-site; flag them.
- Live-only or Bridge sources: data can't be pulled; record the connection for re-platforming.
- Extract-only file sources (Excel/CSV): the extract is the only copy of the data.

**Complexity score per workbook** (weights to calibrate):
- (#dashboards + #worksheets incl. hidden)
- \#calculated fields; LOD count ({FIXED|INCLUDE|EXCLUDE} in formulas)
- table-calc functions (WINDOW_*, RUNNING_*, RANK*, INDEX, LOOKUP, PREVIOUS_VALUE, TOTAL)
- parameters and parameter actions
- dashboard actions (filter/highlight/URL/set/parameter) and set/group/bin/hierarchy/combined fields
- data sources per workbook (blending)
- custom SQL / initial SQL
- user functions (RLS)
- SCRIPT_* / RAWSQL / spatial / MAKEPOINT / analytics extensions
- dashboard extensions and web-page objects
- trend lines, forecasts, reference lines
- maps
- story points
- device layouts
- Combine with usage (views in 90 days, distinct users) and recency to rank migrate / rebuild / retire.
- Read the inputs mainly from XML parsing plus Metadata API counts.

### Gaps
- I couldn't find first-hand, quantified post-mortems of large Tableau→non-Tableau migrations: effort per workbook, or how often LOD and table calcs appear in real estates. Most sources are vendor or partner content.
- I found no documented REST rate limits for Tableau Server, so throttle values have to be set empirically.
- I didn't verify the XML element names for dashboard actions, extensions, device layouts, and user filters. The TWB schema is undocumented; the Document API exposes only connections and fields.
