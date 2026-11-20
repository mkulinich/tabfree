# Tableau Feature Catalog for TabFree Migration Planning (Tableau Server, Tableau Cloud, Tableau Desktop / Web Authoring; status as of 2026-11-20)

**Purpose.** A structured catalog of what Tableau does, with lifecycle status and a migration priority for each feature, for "TabFree": a free, open-source replacement for Tableau Server + Tableau Desktop on AWS. In TabFree, AI (Claude Code) authors HTML dashboards, queries run on Athena/Redshift, and content is migrated from an existing **Tableau Server**.

**How to read the tables**
- **Priority** (analyst judgment for TabFree, not a Tableau statement): **P0** Must-have (its absence blocks migration of typical enterprise dashboards or day-to-day server use); **P1** Should-have (expected soon after launch; some customers will treat as P0); **P2** Nice-to-have; **P3** Rare / skip; **Skip** = retired in Tableau, so nothing needs migrating.
- **Layer** (authoring tables only, analyst judgment): **SQL** = mostly a data/query concern (can be compiled to Athena/Redshift SQL); **FE** = mostly a rendering/interaction concern (HTML/JS front end); **Both** = needs query support and front-end state/rendering.
- **Status** = Current / Deprecated / Retired, with dates and versions where a source confirms them. "Cloud-only" is flagged because the migration source is Tableau Server, so a Cloud-only feature is, by definition, not in use in the source estate.
- Description and Status cells are sourced (Source column). Priority, Justification and Layer cells are inferences.
- Documentation note: help.tableau.com pages answered HTTP 200 to plain HTTP clients during this research, but redirected some automated fetchers to Markdown copies on docs.salesforce.com, e.g. [Server release highlights](https://docs.salesforce.com/tableau/current/en-us/server/ipa_server_whats_new.md) and [Advanced Management](https://docs.salesforce.com/tableau/current/en-us/online/itm_intro.md). tableau.com marketing pages returned HTTP 403 to automated fetches, and the official Release Navigator viz on Tableau Public sits behind a bot challenge. 2025–2026 feature facts therefore come partly from search snippets and third-party release trackers, which are labeled as such.

---

## 1. Product landscape and lifecycle as of November 2026: what is current, retired, deprecated, or new (2023.x–2026.x)?

### Takeaway
By late 2026 Tableau's investment has moved heavily into AI and agentic features: Pulse, Tableau Agent, Tableau Next, Tableau Studio (GA Nov 2026), Tableau Data Apps, and a hosted MCP server. These are mostly Cloud-first and gated to the top editions (Tableau+/Cloud+/Server+). Several older features have been retired: Ask Data and Lenses and legacy Metrics (Cloud Feb 2024 / Server 2024.2), Data Stories (2025.1), and View Acceleration plus the Data Acceleration APIs (Cloud June 2026 / Server 2026.2). Web Data Connector 2.x is deprecated, and a REST API connector replaced it in 2026.1. The core viz, calculation, dashboard, permission and subscription feature set that migrated dashboards depend on is unchanged and current.

### Cited Findings

**1a. Release cadence and support windows**

| Item | Fact | Source |
|---|---|---|
| Release trains (late 2026) | Server maintenance builds released Sep 22, 2026: 2026.2.3 (current), 2025.3.10, 2025.1.20. The 2026.3 feature release was published around Nov 2026, and Cloud also gets monthly-named drops (e.g., "Tableau March 2026", "Tableau August 2026"). | [Releasebot tracker](https://releasebot.io/updates/tableau); [DataTerrain](https://dataterrain.com/analyzing-tableau-current-version) |
| Cadence (conflicting) | DataTerrain says Desktop/Server ship numbered releases "roughly twice a year". The tracker shows three numbered feature releases per year (2026.1, 2026.2, 2026.3), with Server maintenance trains only on some of them (2025.1, 2025.3, 2026.2). | [DataTerrain](https://dataterrain.com/analyzing-tableau-current-version); [Releasebot](https://releasebot.io/updates/tableau) |
| Support lifecycle | 2023.3 support ended Oct 24, 2025. 2024.2 support ended Jul 1, 2026. 2025.1 is supported until Mar 19, 2027. | [Tableau Technical Support Programs](https://www.tableau.com/support/services) (via search snippet) |
| Latest Server release-highlights page (docs) | Lists Server 2025.3 additions: Tableau Agent in Server (Prep, Desktop, web authoring), site-level SCIM with site-specific SAML, and Tableau trusted extensions. It also lists one retirement: the Tableau Mobile malware-detection setting. | [Server release highlights](https://docs.salesforce.com/tableau/current/en-us/server/ipa_server_whats_new.md) |

**1b. Retired and deprecated features (with dates)**

| Feature | Status | Date / version | Replacement / note | Source |
|---|---|---|---|---|
| Ask Data (NL query) | Retired | Tableau Cloud Feb 2024; Tableau Server 2024.2 | Pulse / Tableau Agent | [REST API: Ask Data Lens methods (retired)](https://help.tableau.com/release-preview/api/rest_api/en-us/REST/rest_api_ref_ask_data_lens.htm) |
| Ask Data Lenses | Retired | With Ask Data. Lens REST endpoints retired in API 3.22 | none | [Lens API](https://help.tableau.com/release-preview/api/rest_api/en-us/REST/rest_api_ref_ask_data_lens.htm) |
| Metrics (legacy) | Retired | Cloud Feb 2024; Server 2024.2. Not auto-migrated to Pulse | Pulse metrics | [Metrics (Retired)](https://help.tableau.com/current/online/en-us/metrics_create.htm); [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Embedding legacy metrics | Retired | Oct 2023 (Cloud); Server 2023.3 | none | [Connected apps](https://help.tableau.com/current/server/en-us/connected_apps.htm) |
| Data Stories (auto-narrative dashboard object) | Retired | Desktop, Cloud and Server, Jan 2025 (2025.1) | Pulse / AI features | [Data Stories](https://help.tableau.com/current/pro/desktop/en-us/data_stories.htm) (via search snippet) |
| View Acceleration and Data Acceleration APIs | Retired | Cloud June 2026; Server 2026.2 | none (pre-computation now the customer's problem) | [View Acceleration](https://help.tableau.com/current/server/en-us/data_acceleration.htm) |
| JavaScript API v1/v2 (embedding) | Deprecated | Deprecated from 2024.1. Remains available until the last compatible version reaches end of life | Embedding API v3 | [Embedding API migration guide](https://help.tableau.com/current/api/embedding_api/en-us/docs/embedding_api_migration_guide.html) (via search snippet) |
| Web Data Connector 2.x | Deprecated, then removed (dates conflict) | Deprecated 2023.1; KB said retirement in 2025. A third-party blog says WDC is "being removed in 2026.1" | REST API Connector (GA 2026.1 in Server/Desktop/Prep; Cloud "coming soon"), WDC 3.0 | [Tableau KB](https://kb.tableau.com/articles/en_US/Issue/web-data-connectors-deprecated-in-2023-1-release); [HipsterVizNinja 2026.1](https://www.hipstervizninja.com/blog/tableau-2026-1-new-features/) |
| Marketo and Oracle Eloqua connectors | Deprecated / removed (third-party reports) | Removed starting 2026.1; JDBC replacements on Tableau Exchange | Exchange connectors | [DataTerrain](https://dataterrain.com/analyzing-tableau-current-version); [Aegis Softtech](https://www.aegissofttech.com/insights/tableau-server-new-features/) |
| "Sensitive data" data quality warning | Changed | Became a sensitivity label (Cloud June 2023; Server 2023.3) | Sensitivity labels | [Data quality warnings](https://help.tableau.com/current/server/en-us/dm_dqw.htm) |
| Tableau Mobile malware-detection setting | Retired | Server 2025.3 | none | [Server release highlights](https://docs.salesforce.com/tableau/current/en-us/server/ipa_server_whats_new.md) |
| Advanced Management as a standalone add-on | Packaging retired | Not sold standalone after Sep 16, 2024. Now included in Enterprise, Cloud+ and the Tableau+ bundle | Editions | [Advanced Management on Cloud](https://docs.salesforce.com/tableau/current/en-us/online/itm_intro.md); [Tableau blog](https://tableau.com/blog/advanced-management-manage-secure-and-scale-analytics) |
| Tableau Desktop Public Edition installer | Changed | From 2026.1, one Desktop installer covers both "Professional Edition" and "Free Edition". The docs still list a Public Edition installer | Desktop Free Edition | [Desktop deployment: where's the installer](https://help.tableau.com/current/desktopdeploy/en-us/desktop_deploy_download.htm) (via search snippet); [Keyrus](https://keyrus.com/uk/en/insights/massive-news-for-the-data-community-tableau-desktop-is-free) |

**1c. Notable additions by release (2023.1 → Nov 2026)**

| Release | Notable additions relevant to a replacement product | Source |
|---|---|---|
| 2023.1 | Publish to Personal Space from Desktop. Admins can hide Data Guide for all users. Data mapping for Accelerators (data mapper improved in 2023.2). | [Personal Space](https://help.tableau.com/current/server/en-us/personal_space.htm); [Data Guide](https://help.tableau.com/current/pro/desktop/en-us/data_guide.htm); [Accelerators](https://help.tableau.com/current/online/en-us/accelerators.htm) |
| 2023.3 | Dynamic axis ranges, Embedding Playground, native Lightning Web Component (LWC). Site-wide extract/flow monitoring warnings and custom data-quality labels (Server 2023.3). Viz in Tooltip disabled on totals. Redshift IAM Identity Center trusted identity propagation (Server 2023.3.4; Desktop 2024.1). | [Tableau 2023.3](https://tableau.com/2023-3-features); [DQW](https://help.tableau.com/current/server/en-us/dm_dqw.htm); [Viz in Tooltip](https://help.tableau.com/current/pro/desktop/en-us/viz_in_tooltip.htm); [AWS blog](https://aws.amazon.com/blogs/big-data/integrate-tableau-and-okta-with-amazon-redshift-using-aws-iam-identity-center) |
| 2024.1 | Pulse insight summaries (Cloud). JS API v1/v2 deprecated. | [AI in Tableau](https://help.tableau.com/current/tableau/en-us/tableau_gai_solutions.htm); [Embedding migration guide](https://help.tableau.com/current/api/embedding_api/en-us/docs/embedding_api_migration_guide.html) |
| 2024.2 | Multi-fact relationships (shared dimensions), Viz Extensions, Tableau Agent (Einstein Copilot) in Cloud viz authoring (Aug 2024), Tableau Agent in Catalog, group sets (Server 2024.2), dynamic group membership via JWT assertions (Server 2024.2). | [2024.2 release blog](https://tableau.com/blog/release-tableau-einstein-copilot-tableau-multi-fact-relationships-viz-extensions); [Relationships](https://help.tableau.com/current/pro/desktop/en-us/datasource_relationships_learnmorepage.htm); [Group sets](https://help.tableau.com/current/server/en-us/group_sets.htm); [Connected apps](https://help.tableau.com/current/server/en-us/connected_apps.htm) |
| 2024.3 | Tableau Cloud Manager (multi-site, multi-region), Table Viz Extension, spatial parameters and spatial calcs (Symdifference, Intersection, Difference), Tableau app for Microsoft Teams. | [2024.3 release blog](https://tableau.com/blog/release-tableau-cloud-manager-tables-viz-extension-spatial-parameters) |
| 2025.1 | Tableau Agent in Desktop and in Pulse. User attribute functions for JWT (Server 2025.1). VizQL Data Service JWT access (Server, Feb 2025). Non-English AI languages. | [AI in Tableau](https://help.tableau.com/current/tableau/en-us/tableau_gai_solutions.htm); [Connected apps](https://help.tableau.com/current/server/en-us/connected_apps.htm) |
| 2025.2 | Tableau Agent in Prep (Cloud). Redshift "Show Other Databases" / datashares (ODBC 2.x). Pulse threshold alerts and Tableau MCP support for Pulse tools (Aug 2025). | [AI in Tableau](https://help.tableau.com/current/tableau/en-us/tableau_gai_solutions.htm); [Redshift connector](https://help.tableau.com/current/pro/desktop/en-us/examples_amazonredshift.htm); [Tableau Aug 2025](https://www.tableau.com/2025-2-august-features) (via search snippet) |
| 2025.3 | Tableau Agent in Server (Desktop, Prep, web authoring). Site-level SCIM on Server. Trusted extensions. Unified access tokens (UATs). External tokens for SCIM. Dashboard Narratives. Q&A Calibration. | [Server release highlights](https://docs.salesforce.com/tableau/current/en-us/server/ipa_server_whats_new.md); [Embedding auth](https://help.tableau.com/current/api/embedding_api/en-us/docs/embedding_api_auth.html); [Tableau 2025.3](https://www.tableau.com/2025-3-features) (via search snippet) |
| 2026.1 (Mar 2026) | Rounded corners on dashboard objects. Mixed-geometry maps. View Data Model enhancements. REST API Connector GA (Server/Desktop/Prep). Starburst connector. RHEL 10 for Server. OIDC-based SCIM (Cloud). Bridge job monitoring and Activity Log events. AI color palettes. Prep spatial calcs. Keyboard-only pill placement (accessibility). Amazon S3 connector still Beta. | [Tableau 2026.1](https://www.tableau.com/2026-1-features) (via search snippet); [HipsterVizNinja](https://www.hipstervizninja.com/blog/tableau-2026-1-new-features/) |
| 2026.2 | Composable data sources (rolling out Jun–Jul across Cloud/Server/Desktop). Hosted Tableau MCP (Cloud, OAuth 2.1). Cloud capacity management. Tableau Agent in Dashboards (Cloud beta / Server pilot). Admin profiles GA (Server). Composable views and Pulse metrics LWC. User Attribute function for SAML/OIDC. VizQL Data Service live dashboard queries. Drive-time area maps. SVG export. Workbook-wide management of viz extensions. | [Releasebot](https://releasebot.io/updates/tableau); [Tableau 2026.2](https://www.tableau.com/2026-2-features) (via search snippet); [Viz Extensions](https://help.tableau.com/current/pro/desktop/en-us/viz_extensions.htm) |
| 2026.3 (Nov 2026) | Tableau Knowledge (knowledge graphs for agents). Workbook Business Preferences (Markdown business rules for the agent; beta). Bring Your Own LLM (Server beta). Tableau Data Apps (beta). Tableau MCP admin tools. Data Source Edit API. Rserve over HTTP. Excel add-in GA. Revision Notes. Server pre-built container images and custom certificates. Dark mode for vizzes. Transparent tooltips. Improved Server exports. Dashboard Narratives and Q&A on Server 2026.3 (requires Server+). | [Releasebot](https://releasebot.io/updates/tableau); [AI in Tableau](https://help.tableau.com/current/tableau/en-us/tableau_gai_solutions.htm) |
| Nov 2026 | **Tableau Studio**, described as "the new Tableau Desktop" for agentic authoring: agent plus "Tableau Skills", builds and publishes analytics and Data Apps. GA in Cloud and Server Nov 2026. Requires Desktop 2026.3.1+ and a Creator with Tableau+/Cloud+/Server+. | [Tableau Studio](https://www.tableau.com/products/tableau-studio); [Tableau new features (Nov 2026)](https://www.tableau.com/products/new-features); [What is Tableau Studio](https://www.tableau.com/blog/what-is-tableau-studio) (via search snippets) |

**1d. Editions and packaging (context for what customers have licensed)**

| Item | Fact | Source |
|---|---|---|
| Cloud editions | Standard, Enterprise, and the Tableau+ bundle (Cloud-exclusive). Another official page names the top Cloud tier "Cloud+". | [Tableau pricing](https://www.tableau.com/pricing); [Cloud pricing](https://www.tableau.com/pricing/cloud) (via search snippets) |
| Server editions | Standard and Enterprise, plus a newer "Server+" tier (contents not detailed in sources found). | [Tableau pricing](https://www.tableau.com/pricing) (via search snippet) |
| Enterprise contents | Enterprise includes Standard plus Data Management, Advanced Management and eLearning. | [Tableau pricing](https://www.tableau.com/pricing) (via search snippet) |
| AI gating | Many AI features require Cloud+/Tableau+ (or Server+ for 2026.3 Dashboard Narratives on Server). | [AI in Tableau](https://help.tableau.com/current/tableau/en-us/tableau_gai_solutions.htm) |

### Inferences
- The migration source is Tableau Server, so Cloud-only features are not in the source estate and are not migration blockers (P3): Pulse, Activity Log, Admin Insights, Bridge, Tableau Cloud Manager, on-demand access, hosted MCP. They may still be useful as product-roadmap ideas.
- Retired features need no runtime support. Older workbooks may still contain their artifacts, e.g. Ask Data or Data Story dashboard objects, legacy metric subscriptions, or JS API v2 embed code. The migrator should detect these and report or drop them cleanly.
- Tableau Studio ("vibe coding" analytics with skills) and Tableau Data Apps confirm that the market is moving toward the AI-authored approach TabFree is taking. This is a competitive signal, not a migration requirement.
- Data Management and Advanced Management features (Catalog, DQW, virtual connections, Prep Conductor, Content Migration Tool, Resource Monitoring Tool) appear only in estates with Enterprise or legacy add-on licenses. Check what the customer licensed before raising their priority.

### Gaps
- The official Release Navigator viz (with per-feature "since" versions) could not be read: Tableau Public is behind a bot challenge, which was not bypassed. As a result, introduction versions for many pre-2023 features (DZV, parameter actions, set actions, dynamic parameters, relationships 2020.2, etc.) are not individually verified here. Only versions stated on fetched pages are given.
- tableau.com release pages (2025-x/2026-x features, pricing, Studio blog) returned HTTP 403. Their facts come from search-engine snippets and third-party trackers (Releasebot, DataTerrain, HipsterVizNinja), not from the pages themselves.
- No official page found confirming the 2026.1 WDC removal or the Marketo/Eloqua removal (third-party only), and none confirming whether Tableau Server 2026.1 shipped as a full Server release.
- Contents of the "Server+" edition were not found.

---
## 2. Server/Cloud platform: complete feature catalog with migration priority

### Takeaway
For server users, day one needs: project hierarchy, workbooks/views and published data sources, users and groups, a faithful permission model (allow/deny rules, project defaults, locked projects), SAML/OIDC SSO, row-level security, scheduled data refresh, email subscriptions (PNG/PDF), PDF/image/crosstab downloads, search and browse, and reliable caching, backup and HA. Collaboration (favorites, custom views, alerts, comments), admin analytics, embedding and APIs are P1–P2. AI/NLQ, Cloud-only, and Data Management/Advanced Management add-on features are mostly P2–P3 for a migration from Tableau Server.

### Cited Findings
Priority, Justification = inferences; other columns sourced.

**2.1 Content organization and navigation**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Sites (multi-tenancy) | Isolated tenants, each with its own users, groups, projects and content. In multi-site environments the view URL includes the site ID | Current | P1 (P0: at least one "Default" site) | Many enterprise servers run several sites (departments, external partners). An MVP can start with one tenant and map extra sites to tenants later | [Sites](https://help.tableau.com/current/server/en-us/sites_intro.htm); [View URL structure](https://help.tableau.com/current/pro/desktop/en-us/embed_structure.htm) |
| Projects | Containers for workbooks, data sources and flows, and the unit of permission management | Current | P0 | All migrated content lives in projects; permissions hang off them | [Projects](https://help.tableau.com/current/server/en-us/projects.htm) |
| Nested projects | Projects within projects; Recycle Bin respects the hierarchy | Current | P0 | Enterprise estates use deep hierarchies; paths must survive migration for findability and permissions | [Projects](https://help.tableau.com/current/server/en-us/projects.htm); [Recycle Bin](https://help.tableau.com/current/server/en-us/recycle_bin.htm) |
| Project leaders / project-level admin | Delegated management of content and permissions within a project | Current | P1 | Large orgs delegate admin to business units | [Permissions for projects](https://help.tableau.com/current/server/en-us/permissions_projects.htm) |
| Home page | Landing page (Home), Explore, notifications center, quick search | Current | P0 | Every user's entry point | [Tour your Tableau site](https://help.tableau.com/current/pro/desktop/en-us/navigating.htm) |
| Explore (browse all content) | Browse all content you can access, by type (top-level projects by default), with filters and sort | Current | P0 | Primary way users find dashboards | [Tour your Tableau site](https://help.tableau.com/current/pro/desktop/en-us/navigating.htm) |
| Search (quick + full) | Type-ahead quick search with past queries and owner shortcuts; full results page filterable by content type | Current | P0 | Users must find migrated content immediately | [Search your site](https://help.tableau.com/current/pro/desktop/en-us/search.htm) |
| Favorites | Per-user starring of any content type; backed by the Collections service | Current | P1 | Highly visible, cheap to build, and migratable per user. Losing favorites is a common complaint | [Mark favorites](https://help.tableau.com/current/pro/desktop/en-us/favorites.htm); [Collections service](https://help.tableau.com/current/server/en-us/server_process_collections-service.htm) |
| Recents | Recently accessed views, metrics and flows | Current | P1 | Convenience; derivable from TabFree's own access log | [Tour your Tableau site](https://help.tableau.com/current/pro/desktop/en-us/navigating.htm) |
| Shared with Me | Items other users shared with you | Current | P2 | Depends on Share feature | [Tour your Tableau site](https://help.tableau.com/current/pro/desktop/en-us/navigating.htm) |
| Collections | User-curated lists of content across projects; permissions stay with each item. Service added in Server 2021.2 | Current | P2 | Navigation aid; low risk; migratable via API | [Collections service](https://help.tableau.com/current/server/en-us/server_process_collections-service.htm); [InterWorks](https://interworks.com/blog/2023/10/10/what-are-tableau-collections-and-how-do-i-use-them/) |
| Tags | User-applied keywords used in search and filtering | Current | P2 | Some orgs tag heavily for governance; cheap | [Use tags](https://help.tableau.com/current/pro/desktop/en-us/tags.htm) |
| Personal Space | Private area for Explorers and Creators. Publish to it from Desktop since 2023.1 | Current | P2 | An authoring workflow; TabFree authoring is AI-driven | [Personal Space](https://help.tableau.com/current/server/en-us/personal_space.htm) |
| Upload workbooks/flows from web | Upload .twb(x) from Home/Explore; flows from Cloud 2026.2.4 | Current | P3 | Publishing is replaced by the TabFree AI/git workflow | [Tour your Tableau site](https://help.tableau.com/current/pro/desktop/en-us/navigating.htm) |
| Project images / descriptions | Thumbnails and descriptions for projects | Current | P3 | Cosmetic | [Customize your server](https://help.tableau.com/current/server/en-us/customize.htm) |

**2.2 Content types**

| Content type | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Workbooks | Container of sheets, dashboards and stories, with embedded or published data connections | Current | P0 | The unit of migration | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Views (sheets, dashboards, stories) | Individually browsable and permissionable views | Current | P0 | Users bookmark and subscribe to views | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| "Show sheets as tabs" semantics | Views inherit workbook permissions unless the project is locked or tabs are hidden, in which case each view has its own permissions | Current | P0 | Changes both navigation and permission evaluation; the migrator must honor it | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Published data sources | Reusable, permissioned connections or extracts with shared calcs, hierarchies and metadata, used by many workbooks | Current | P0 | Governed enterprise dashboards usually connect to published data sources; TabFree needs an equivalent semantic "data source" object | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm); [Certified data](https://help.tableau.com/current/server/en-us/datasource_certified.htm) |
| Flows (Tableau Prep) | Prep flows published and scheduled with Prep Conductor | Current (Prep Conductor needs Data Management) | P2 (P1 if heavily used) | Replace with SQL pipelines (Athena CTAS, Glue, dbt) at migration | [Prep Conductor](https://help.tableau.com/current/prep/en-us/prep_conductor_overview.htm) |
| Virtual connections | Centrally managed shared connections with embedded credentials, extract schedules and data policies | Current (Data Management) | P2 | Less common; translate to TabFree data sources plus RLS | [Virtual connections](https://help.tableau.com/current/server/en-us/dm_vconn_overview.htm) |
| Data roles | Custom semantic roles for field validation, created in Prep | Current | P3 | Rare | [Tour your Tableau site](https://help.tableau.com/current/pro/desktop/en-us/navigating.htm); [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| External assets (databases, tables) | Catalog-indexed assets inside projects (Cloud Oct 2022 / Server 2022.3, with Data Management) | Current | P3 | Catalog metadata only | [Projects](https://help.tableau.com/current/server/en-us/projects.htm) |
| Ask Data lenses | Curated Ask Data scopes | Retired (Cloud Feb 2024 / Server 2024.2) | Skip | Retired | [Lens API](https://help.tableau.com/release-preview/api/rest_api/en-us/REST/rest_api_ref_ask_data_lens.htm) |
| Metrics (legacy) | KPI snapshots built from views | Retired (Cloud Feb 2024 / Server 2024.2) | Skip | Retired; not auto-migrated even within Tableau | [Metrics (Retired)](https://help.tableau.com/current/online/en-us/metrics_create.htm) |
| Pulse metric definitions and metrics | KPI definitions on published data sources, with digests, insights, goals and thresholds | Current, **Cloud-only** | P3 (migration) / P2 (roadmap) | Not present on Tableau Server estates | [Pulse release notes](https://help.tableau.com/current/online/en-us/pulse_intro.htm); [The Bricks (3rd party)](https://www.thebricks.com/resources/guide-when-will-tableau-pulse-be-available) |
| Composable data sources | Combine and extend published data sources | New, 2026.2 (rolling out) | P3 | Too new to appear in source estates | [Releasebot](https://releasebot.io/updates/tableau) |
| Tableau Data Apps | Custom "vibe-coded" analytic web apps hosted in Tableau | Beta 2026.3; GA Nov 2026 per Tableau | P3 | Equivalent to TabFree's HTML dashboards; nothing to migrate | [Releasebot](https://releasebot.io/updates/tableau); [Tableau new features](https://www.tableau.com/products/new-features) (via search snippet) |

**2.3 Users, site roles, licensing, groups**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Server Administrator | Full configuration and content access across all sites (Server only) | Current | P0 | Map to TabFree platform admin | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm) |
| Site Administrator Creator / Explorer | Site-level user, group and content management. The Creator variant can also connect to new data | Current | P0 | Map to tenant admin | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm) |
| Creator | Connect to data; build and publish workbooks, data sources and flows | Current | P0 | Map to "author" (in TabFree, someone who drives the AI author and publishes) | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm) |
| Explorer (can publish) | Publish workbooks from the web using existing data sources; edit and save existing workbooks | Current | P0 | Map to "editor" | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm) |
| Explorer | Interact, subscribe, create data-driven alerts, open for ad-hoc analysis without saving | Current | P0 | Map to "explorer" | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm) |
| Viewer | View and interact, subscribe, download image or summary data. Cannot connect to data, publish, or set alerts | Current | P0 | Largest user population; capability ceilings must be honored | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm) |
| Unlicensed | Cannot sign in (e.g., license exhausted, imported unlicensed, removed owners) | Current | P1 | Needed to migrate content owners who no longer have access | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm) |
| License levels (Creator / Explorer / Viewer) | A site role caps the maximum capabilities a user can have, whatever the permission rules say | Current | P0 (as a capability ceiling, not billing) | TabFree is free, but role ceilings determine effective permissions | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm); [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Admin profiles | Delegated admin profiles that can be assigned to Creator/Explorer users | New: Server GA 2026.2; Cloud 2026.3 | P3 | Too new to appear in source estates | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm); [Releasebot](https://releasebot.io/updates/tableau) |
| Local groups | Site-level groups used in permission rules | Current | P0 | Most permission rules target groups | [Permissions](https://help.tableau.com/current/server/en-us/permissions.htm) |
| AD/LDAP group import and sync | Import directory groups; sync on schedule or on demand. Sync can promote but never demote site roles | Current | P1 | Common on-prem pattern. On AWS, IdP/SCIM groups usually replace it | [Sync external directory groups](https://help.tableau.com/current/server/en-us/groups_globalsync.htm) (via search snippet) |
| Grant license (role) on sign-in / minimum site role | Group-level minimum site role applied at sign-in or sync | Current | P1 | Automates onboarding; needed to reproduce role assignment logic | [Grant license on sign in](https://help.tableau.com/current/server/en-us/grant_role.htm) (via search snippet) |
| Group sets | Container of groups; a user must belong to all groups in the set. Server 2024.2+. Cannot be subscribed; not supported in JWT | New, 2024.2 | P2 | Rare so far | [Group sets](https://help.tableau.com/current/server/en-us/group_sets.htm); [Subscriptions](https://help.tableau.com/current/server/en-us/subscribe_user.htm); [Embedding auth](https://help.tableau.com/current/api/embedding_api/en-us/docs/embedding_api_auth.html) |
| Dynamic group membership via JWT assertions | Group membership asserted at runtime for embedding (Server 2024.2+) | Current | P2 | Embedding-only | [Connected apps](https://help.tableau.com/current/server/en-us/connected_apps.htm) |

**2.4 Permissions**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Permission rules | Per user or group, each capability is Allowed, Denied or Unspecified; effective permissions come from comparing rules (deny wins) | Current | P0 | Core security contract; must be translated exactly | [Permissions](https://help.tableau.com/current/server/en-us/permissions.htm); [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Permission templates | View, Explore, Publish, Administer, None, Denied (cumulative starting points; editing makes "Custom") | Current | P1 | A UI convenience; the migrator maps capabilities directly | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Workbook / view capabilities | View; Filter; View/Add Comments; Download Image/PDF; Download Summary Data; Share Customized; Download Full Data; Web Edit; Run Explain Data; Full Data Query; Download Workbook/Save a Copy; Overwrite; API Access; AI Access; Create/Refresh Metrics; Move; Delete; Set Permissions; Refresh Extracts | Current (Metrics capability is legacy) | P0 for View/Filter/Download Image-PDF/Summary/Full Data/Overwrite/Move/Delete/Set Permissions; P1 Comments/Share Customized; P3 Web Edit/Explain/AI/Metrics | Export rights are compliance-sensitive | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Data source capabilities | View, Connect, Download Data Source, Overwrite, API Access, Create Metric Definitions, Delete, Set Permissions, Refresh Extracts | Current | P0 (View/Connect/Overwrite/Delete/Set Permissions) | Governs who can build on a data source | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Flow capabilities | View, Download Flow, Run, Overwrite, Move, Delete, Set Permissions | Current | P2 | Only if flows are migrated | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Virtual connection, data role, database, table, collection capabilities | Mostly View, Connect and Overwrite. Virtual connections have Connect off by default; collections are View only | Current | P2–P3 | Add-on or minor objects | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Project permissions and default permissions | Project View/Publish, plus default rules per content type applied to new content | Current | P0 | Determines permissions of everything published later | [Permissions for projects](https://help.tableau.com/current/server/en-us/permissions_projects.htm) |
| Locked projects (content permissions locked to project) | Content cannot carry custom permissions; view-level permissions are set per view when locked | Current | P0 | Common enterprise governance pattern | [Permissions for projects](https://help.tableau.com/current/server/en-us/permissions_projects.htm); [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Ownership and ownership transfer | Content owners and admins have implicit rights; ownership survives deletion to the Recycle Bin | Current | P0 | Needed for permission evaluation and for orphaned content | [Permissions](https://help.tableau.com/current/server/en-us/permissions.htm); [Recycle Bin](https://help.tableau.com/current/server/en-us/recycle_bin.htm) |

**2.5 Identity and authentication**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Local authentication | Tableau-managed passwords, password policy, lockout; works with any identity store | Current | P0 | Bootstrap admin, service accounts, small sites | [User authentication](https://help.tableau.com/current/server/en-us/security_auth.htm) |
| Active Directory identity store (+ NTLM/SSPI automatic logon, Windows) | AD as user/group store; SSPI cannot be combined with SAML, trusted auth, load balancers or proxies | Current | P1 | Many on-prem servers use AD. On AWS, prefer IdP SSO plus SCIM; AD sync optional | [User authentication](https://help.tableau.com/current/server/en-us/security_auth.htm) |
| LDAP identity store / LDAP bind | Generic LDAP (GSSAPI or simple bind) | Current | P2 | Less common than AD/SAML | [User authentication](https://help.tableau.com/current/server/en-us/security_auth.htm) |
| SAML 2.0 (server-wide) | External IdP SSO | Current | P0 | Table stakes for enterprise SSO | [SAML](https://help.tableau.com/current/server/en-us/saml.htm) |
| Site-specific SAML | Per-site IdP (local identity store only) | Current | P1 | Multi-site estates | [User authentication](https://help.tableau.com/current/server/en-us/security_auth.htm) |
| OpenID Connect | OIDC SSO (local identity store only; no AD/LDAP) | Current | P0 (SAML **or** OIDC must exist day one) | Okta, Entra and Cognito all support OIDC | [OpenID Connect](https://help.tableau.com/current/server/en-us/openid_auth.htm); [User authentication](https://help.tableau.com/current/server/en-us/security_auth.htm) |
| Kerberos | AD Kerberos SSO, incl. delegation to data sources | Current | P2 | Mostly used for SQL Server/Hive delegation; Athena/Redshift use IAM | [Kerberos](https://help.tableau.com/current/server/en-us/kerberos.htm) |
| Mutual SSL | Client-certificate authentication | Current | P3 | Niche (government/defense) | [Mutual SSL](https://help.tableau.com/current/server/en-us/ssl_config_mutual.htm) |
| Trusted authentication (trusted tickets) | Web server pre-authenticates users for embedded views | Current (legacy pattern) | P2 | Only for legacy portal embeds | [Trusted authentication](https://help.tableau.com/current/server/en-us/trusted_auth.htm) |
| Connected apps (direct trust JWT; EAS/OAuth 2.0 trust) | SSO for embedded content and JWT access to REST and Metadata API (Server Oct 2023) and VizQL Data Service (Feb 2025). Server help says "Beginning with Tableau Server 2022.1"; embedding docs date connected apps to 2021.4 | Current | P1 (P0 if dashboards are embedded in portals) | The modern embedding SSO pattern | [Connected apps](https://help.tableau.com/current/server/en-us/connected_apps.htm); [Embedding auth](https://help.tableau.com/current/api/embedding_api/en-us/docs/embedding_api_auth.html) |
| Unified access tokens (UATs) | Additional token option for embedded-user authentication (2025.3) | New | P3 | Too new | [Embedding auth](https://help.tableau.com/current/api/embedding_api/en-us/docs/embedding_api_auth.html) |
| Personal access tokens (PATs) | Token sign-in for REST API automation | Current | P1 | Admin scripts and CI integrations | [User authentication](https://help.tableau.com/current/server/en-us/security_auth.htm) |
| SCIM provisioning | IdP-driven user and group provisioning (not authentication). Server: site-level SCIM with site SAML (2025.3), external tokens (2025.3). Cloud: OIDC-based SCIM (2026.1), SCIM on custom domains (2026.3) | Current | P1 | Standard for Okta/Entra-managed enterprises | [SCIM (Server)](https://help.tableau.com/current/server/en-us/scim_config_online.htm); [Server release highlights](https://docs.salesforce.com/tableau/current/en-us/server/ipa_server_whats_new.md); [Connected apps](https://help.tableau.com/current/server/en-us/connected_apps.htm); [Releasebot](https://releasebot.io/updates/tableau) |
| Identity pools | Several identity stores/IdPs serving different user populations on one Server | Current | P2 | Mixed internal/external populations | [Customize your server](https://help.tableau.com/current/server/en-us/customize.htm) |

**2.6 Row-level security (RLS) and data entitlement**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| User filters | Workbook-level filters mapping users/groups to allowed members | Current | P0 | Common in published workbooks; a security regression if lost | [User filters](https://help.tableau.com/current/pro/desktop/en-us/publish_userfilters.htm) |
| Dynamic RLS calcs + entitlement tables | Calculated fields using USERNAME(), FULLNAME(), ISMEMBEROF(), USERDOMAIN(), joined to an entitlement table and enforced through a data source filter on a published data source | Current | P0 | Most scalable and most common enterprise RLS pattern | [RLS options](https://help.tableau.com/current/server/en-us/rls_options_overview.htm); [Data source filters](https://help.tableau.com/current/pro/desktop/en-us/filtering_datasource.htm) |
| Data source filters as a security boundary | Filters baked into published data sources that consumers cannot remove | Current | P0 | Part of the RLS contract | [Data source filters](https://help.tableau.com/current/pro/desktop/en-us/filtering_datasource.htm) |
| Database-side identity (Initial SQL parameters, impersonation) | Initial SQL can pass the Tableau user to the DB for native RLS | Current | P1 | Redshift RLS and Lake Formation patterns benefit | [Initial SQL](https://help.tableau.com/current/pro/desktop/en-us/connect_basic_initialsql.htm) |
| Redshift IAM Identity Center trusted identity propagation | Per-user identity flows from IdP → Tableau → Redshift (Desktop 2024.1, Server 2023.3.4) | Current | P1 | AWS-native per-user entitlements | [AWS Big Data blog](https://aws.amazon.com/blogs/big-data/integrate-tableau-and-okta-with-amazon-redshift-using-aws-iam-identity-center); [Redshift connector](https://help.tableau.com/current/pro/desktop/en-us/examples_amazonredshift.htm) |
| User attribute functions (USERATTRIBUTE, USERATTRIBUTEINCLUDES) | RLS on attributes passed in the JWT (Server 2025.1+; SAML/OIDC 2026.2). Requires a site setting; not usable with Bridge | Current | P2 | Mostly embedded/multi-tenant use | [Connected apps](https://help.tableau.com/current/server/en-us/connected_apps.htm); [Embedding user attributes](https://help.tableau.com/current/api/embedding_api/en-us/docs/embedding_api_user_attributes.html); [Releasebot](https://releasebot.io/updates/tableau) |
| Data policies (virtual connections) | Connection-level RLS policies | Current (Data Management) | P2 | Add-on; translate to TabFree RLS rules | [Virtual connections](https://help.tableau.com/current/server/en-us/dm_vconn_overview.htm) |

**2.7 Data sources, connections and freshness**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Live connections | Queries sent to the source database at view time | Current | P0 | Athena/Redshift live querying is TabFree's core | [Data freshness policy](https://help.tableau.com/current/server/en-us/data_freshness_policy.htm) |
| Extracts (.hyper) | Snapshot copies refreshed on schedule; full or incremental | Current | P0 | Extracts are pervasive. Migration must re-materialize them as S3/Athena (or Redshift) tables | [Refresh extracts](https://help.tableau.com/current/pro/desktop/en-us/extracting_refresh.htm) |
| Full extract refresh | Rebuild the extract from the source | Current | P0 | Default refresh mode | [Refresh extracts](https://help.tableau.com/current/pro/desktop/en-us/extracting_refresh.htm) |
| Incremental extract refresh | Append new rows based on an increasing column | Current | P1 | Large fact tables; maps to partition-append in S3 | [Refresh extracts](https://help.tableau.com/current/pro/desktop/en-us/extracting_refresh.htm) |
| Refresh schedules | Admin-defined schedules (hourly, daily, weekly, monthly), plus custom schedules where enabled | Current | P0 | Data freshness expectations must carry over | [Create or modify a schedule](https://help.tableau.com/current/server/en-us/schedule_add.htm); [Manage schedules](https://help.tableau.com/current/server/en-us/schedule_manage_create.htm); [Subscriptions](https://help.tableau.com/current/server/en-us/subscribe_user.htm) |
| Data source certification | Mark trusted data sources with notes | Current | P1 | Governance signal users rely on | [Certified data](https://help.tableau.com/current/server/en-us/datasource_certified.htm) |
| Embedded credentials vs prompt user | Data source authentication separate from server sign-in | Current | P0 (concept) | TabFree needs service identities (IAM roles) and optionally per-user credentials | [User authentication](https://help.tableau.com/current/server/en-us/security_auth.htm) |
| OAuth to data sources (Athena IAM OAuth/OIDC, Redshift IAM / Identity Center) | IdP-based database authentication | Current | P2 | Only for per-user DB identity | [Athena](https://help.tableau.com/current/pro/desktop/en-us/examples_amazonathena.htm); [Redshift](https://help.tableau.com/current/pro/desktop/en-us/examples_amazonredshift.htm) |
| Workbook data freshness policy / query cache TTL | Owners can set a policy for live workbooks (site default 12 hours or "always live"); admins set server cache policy (`tsm data-access caching`) | Current | P0 (as caching semantics) | Sets user expectations about staleness and cost | [Data freshness policy](https://help.tableau.com/current/server/en-us/data_freshness_policy.htm); [Configure data cache (2018.2)](https://help.tableau.com/v2018.2/server/en-us/config_cache.htm) |
| Tableau Bridge | Client that refreshes or queries private-network data for Tableau Cloud | Current, **Cloud-only** | P3 | Not used by Server estates; AWS networking replaces it | [Tableau Bridge](https://help.tableau.com/current/online/en-us/to_sync_local_data.htm) |
| Prep Conductor | Schedules and monitors Prep flows on Server/Cloud (Data Management) | Current | P2 | Migrate flows to SQL/ETL rather than replicate | [Prep Conductor](https://help.tableau.com/current/prep/en-us/prep_conductor_overview.htm) |
| Web Data Connector (WDC) / REST API connector | Custom web-API connectors. WDC 2.x deprecated 2023.1; REST API connector GA 2026.1 | Deprecated / New | P2 | Rebuild as ingestion jobs into S3 | [Tableau KB](https://kb.tableau.com/articles/en_US/Issue/web-data-connectors-deprecated-in-2023-1-release); [HipsterVizNinja](https://www.hipstervizninja.com/blog/tableau-2026-1-new-features/) |

**2.8 Consumption, distribution and collaboration**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Interactive view in browser | Render and interact (filter, select, tooltips) per the View and Filter capabilities | Current | P0 | Core experience | [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Subscriptions | Scheduled emails of a view or whole workbook with PNG and/or PDF (paper size, orientation), custom subject and message. Owners, project leaders and admins can subscribe others (not group sets). Triggers: schedule or "when data refreshes". Suspended after >5 failures. Optional data-quality warnings | Current | P0 | Heavily used distribution channel; users notice immediately if it stops | [Subscribe to views](https://help.tableau.com/current/server/en-us/subscribe_user.htm) |
| Conditional subscriptions ("don't send if view is empty") | Send only when the view contains data | Current | P1 | Used for exception reporting | [Subscribe to views](https://help.tableau.com/current/server/en-us/subscribe_user.htm) |
| Data-driven alerts | Threshold alerts on a view's measure, delivered by email, in-site notification, or the Tableau for Slack app. Visible-to-others / "Add me" | Current | P1 | Popular with Explorers; Viewers cannot create them | [Data-driven alerts](https://help.tableau.com/current/pro/desktop/en-us/data_alerts.htm); [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm) |
| Custom views | Save filter, sort and selection state as a named view; share it; set a default. URLs use IDs since 2022.3 | Current | P1 (P0 if usage is high) | User-generated state; must be migrated per user | [Custom views](https://help.tableau.com/current/server/en-us/customview.htm) |
| Comments and @mentions | Comment threads on views with @mentions and data snapshots (no snapshot on user-filtered views). Notifications by email, in-app or Slack | Current | P2 | Moderate use; could be migrated as history | [Comment on views](https://help.tableau.com/current/pro/desktop/en-us/comment.htm) |
| Share (direct or link) | Share content with users, which sends a notification and adds it to "Shared with Me"; copy link or embed code | Current | P1 | Expected collaboration affordance | [Share web content](https://help.tableau.com/current/pro/desktop/en-us/shareworkbooks.htm) |
| Notifications center | In-app notifications (alerts, comments, shares) | Current | P2 | Email covers the MVP | [Tour your Tableau site](https://help.tableau.com/current/pro/desktop/en-us/navigating.htm); [Comment on views](https://help.tableau.com/current/pro/desktop/en-us/comment.htm) |
| Download PDF / image / PowerPoint / crosstab | Toolbar and dashboard Download objects export PDF, PowerPoint, PNG, or crosstab of selected sheets (crosstab only once published) | Current | P0 (PDF, image, crosstab); P2 (PowerPoint) | Board packs and Excel hand-offs are daily enterprise behavior | [Create a dashboard (objects)](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm); [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| View underlying / summary data | Inspect and download the data behind marks (permission-gated) | Current | P1 | Analysts rely on it; compliance-sensitive | [View underlying data](https://help.tableau.com/current/pro/desktop/en-us/inspectdata_viewdata.htm) |
| URL-addressable exports | Links to a PNG, PDF or CSV of a view | Current | P1 | Used by scripts and portals | [Export views (Desktop)](https://help.tableau.com/current/pro/desktop/en-us/save_export_image.htm) |
| Tableau Mobile app + offline previews | iOS/Android app. Favorites cached as interactive previews; offline has no filtering or Viz-in-Tooltip | Current | P2 (app) / P3 (offline) | Responsive web suffices first | [Tableau Mobile](https://help.tableau.com/current/mobile/mobile-user/en-us/tableau_mobile_interact.htm); [Mobile deployment](https://help.tableau.com/current/blueprint/en-us/bp_tableau_mobile.htm); [Trailhead](https://trailhead.salesforce.com/es-MX/content/learn/modules/tableau-mobile-quick-look/explore-tableau-mobile-capabilities) |
| Microsoft Teams / Slack integrations | Teams app (2024.3). Slack app for notifications; Pulse unfurls | Current | P2 | Nice for adoption | [2024.3 release blog](https://tableau.com/blog/release-tableau-cloud-manager-tables-viz-extension-spatial-parameters); [Data-driven alerts](https://help.tableau.com/current/pro/desktop/en-us/data_alerts.htm) |
| Tableau add-in for Microsoft Excel | Bring Tableau data into Excel with refresh (GA 2026.3, Cloud and Server) | New | P3 | Too new | [Releasebot](https://releasebot.io/updates/tableau) |

**2.9 AI, natural language and "smart" features**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Ask Data | Natural-language query builder | **Retired** Cloud Feb 2024 / Server 2024.2 | Skip | Retired | [Lens API](https://help.tableau.com/release-preview/api/rest_api/en-us/REST/rest_api_ref_ask_data_lens.htm) |
| Explain Data | Statistical explanations for a selected mark (current behavior since 2021.2); "Run Explain Data" capability | Current | P3 | Rarely central to dashboards; TabFree's AI can offer an analogue | [Explain Data](https://help.tableau.com/current/pro/desktop/en-us/explain_data.htm); [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm) |
| Data Guide | Side pane with dashboard/data details; admins can hide it (2023.1+) | Current | P3 | Low impact | [Data Guide](https://help.tableau.com/current/pro/desktop/en-us/data_guide.htm) |
| Data Stories | Auto-generated narrative dashboard object | **Retired** Jan 2025 (2025.1) | Skip | Retired | [Data Stories](https://help.tableau.com/current/pro/desktop/en-us/data_stories.htm) |
| Tableau Pulse | KPI metrics with AI insight summaries (2024.1+), digests (email/Slack/Teams), goals and thresholds, alerts, Tableau Agent in Pulse Q&A, Pulse dashboard object | Current, **Cloud-only** | P3 (migration) / P2 (roadmap) | Not in Server estates | [Pulse release notes](https://help.tableau.com/current/online/en-us/pulse_intro.htm); [AI in Tableau](https://help.tableau.com/current/tableau/en-us/tableau_gai_solutions.htm); [The Bricks (3rd party)](https://www.thebricks.com/resources/guide-when-will-tableau-pulse-be-available) |
| Tableau Agent (formerly Einstein Copilot) | AI assistant for viz authoring and calcs (Cloud Aug 2024; Desktop 2025.1; Server 2025.3), Prep (Cloud 2025.2; Server 2025.3), Catalog descriptions (Cloud 2024.2), Dashboards: Narratives and Q&A (2026.2.4; Server 2026.3 needs Server+) | Current | P3 | Replaced by Claude Code authoring | [AI in Tableau](https://help.tableau.com/current/tableau/en-us/tableau_gai_solutions.htm); [Tableau Agent](https://help.tableau.com/current/online/en-us/web_author_einstein.htm) |
| Workbook Business Preferences | Upload a Markdown file of business rules and KPIs to guide the agent (open beta 2026.3) | Beta | P3 | Conceptually parallels TabFree's agent-instruction files | [Releasebot](https://releasebot.io/updates/tableau) |
| Bring Your Own LLM (Server) | Route Server AI through a corporate LLM gateway (beta 2026.3) | Beta | P3 | n/a | [Releasebot](https://releasebot.io/updates/tableau) |
| Tableau Knowledge | Knowledge graphs giving agents governed context (GA Cloud+/Tableau+ 2026.3) | New | P3 | n/a | [Releasebot](https://releasebot.io/updates/tableau) |
| Tableau MCP (hosted) | Cloud-hosted MCP server (OAuth 2.1) GA 2026.2; admin tools 2026.3 | New, Cloud | P2 | TabFree should expose its own MCP server for Claude (strategic, not a migration need) | [Releasebot](https://releasebot.io/updates/tableau) |
| Tableau Next | Separate agentic analytics product on the Salesforce/Agentforce platform with Tableau Semantics (announced Apr 15, 2025). Skills include Data Pro, Concierge and Inspector | Current (separate product) | P3 | Not part of Server estates | [Salesforce press release](https://www.salesforce.com/news/press-releases/2025/04/15/tableau-next-announcement/); [TC25 recap](https://www.tableau.com/en-gb/blog/top-10-announcements-keynote-tc25) |
| Tableau Studio | Agentic authoring workspace, "the new Tableau Desktop" (GA Nov 2026) | New | P3 (competitive reference) | The direct analogue of TabFree's AI authoring | [Tableau Studio](https://www.tableau.com/products/tableau-studio) (via search snippet) |

**2.10 Governance and Data Management add-on**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Tableau Catalog (lineage, impact analysis, descriptions) | Indexes content and external assets, shows lineage (since 2019.3, Data Management) | Current | P2 | Valuable but not a migration blocker; TabFree can derive lineage from SQL | [Catalog](https://help.tableau.com/current/server/en-us/dm_catalog_overview.htm) |
| Data quality warnings | Warnings on data sources, tables, flows (2019.3), virtual connections (2022.1), columns (2022.3). Auto-monitoring of refresh failures (site-wide 2023.3). Custom labels (2023.3) | Current | P2 | Migrate as annotations | [Data quality warnings](https://help.tableau.com/current/server/en-us/dm_dqw.htm) |
| Sensitivity labels | Separate label class, replacing the "Sensitive data" DQW (Cloud June 2023 / Server 2023.3) | Current | P2 | Compliance metadata | [Data quality warnings](https://help.tableau.com/current/server/en-us/dm_dqw.htm) |
| Data Source Edit API | Programmatically update field and table descriptions (2026.3) | New | P3 | n/a | [Releasebot](https://releasebot.io/updates/tableau) |

**2.11 Administration, monitoring and audit**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Admin views (Server) | Built-in dashboards for usage, traffic, performance, background tasks and space | Current | P1 | Admins need usage stats immediately (also to retire unused content) | [Admin views](https://help.tableau.com/current/server/en-us/adminview.htm); [Antares (90-day view counts for retirement)](https://getantares.io/tableau-dashboard-complexity-analysis/) |
| PostgreSQL repository | Server's PostgreSQL metadata repository (existence confirmed by a 2025.3.10 log-growth fix note). Admins querying it for custom usage reporting is common practice (not verified in a fetched source) | Current | P2 (runtime) / P0 (as migration *input*) | The migrator reads usage, permissions and subscriptions from it or from the REST API | [Releasebot (repository log-growth fix 2025.3.10)](https://releasebot.io/updates/tableau) |
| Activity Log | Tenant and site event audit log (~15 min latency, 365-day retention, via TCM REST API or S3). Bridge client events 2026.1 | Current, **Cloud-only** (Advanced Management) | P3 (migration); TabFree's own audit log is P1 | Not in Server estates; auditors will still ask for an audit trail | [Advanced Management on Cloud](https://docs.salesforce.com/tableau/current/en-us/online/itm_intro.md); [HipsterVizNinja](https://www.hipstervizninja.com/blog/tableau-2026-1-new-features/) |
| Admin Insights | Curated Cloud data sources: TS Events, TS Users, Groups, Site Content, Viz Load Times, Job Performance, Permissions, Subscriptions, Tokens. 90-day retention (365 with Advanced Management) | Current, **Cloud-only** | P3 (equivalent usage analytics = P1) | Good template for TabFree's admin datasets | [Admin Insights](https://help.tableau.com/current/online/en-us/adminview_insights.htm) |
| Revision history and restore | Prior versions of workbooks and data sources; revision history travels with a workbook into the Recycle Bin | Current | P1 | Git-backed HTML dashboards make this natural | [Recycle Bin](https://help.tableau.com/current/server/en-us/recycle_bin.htm) |
| Revision Notes | Notes on workbook and data source revisions (2026.3) | New | P3 | Commit messages are the analogue | [Releasebot](https://releasebot.io/updates/tableau) |
| Recycle Bin | Restore deleted workbooks, data sources, projects and flows (admin-enabled). Restore loses permissions, subscriptions, custom views, alerts, tags, favorites, comments and schedules | Current | P2 | Safety net; soft-delete is cheap to build | [Recycle Bin](https://help.tableau.com/current/server/en-us/recycle_bin.htm) |
| Content Migration Tool | No-code migration plans between projects, sites or servers, with transformations (e.g., connection rewrites); Advanced Management | Current | P3 | TabFree needs its own migrator; CMT plans show the transformations required | [Content Migration Tool](https://help.tableau.com/current/server/en-us/cmt-intro.htm); [Tableau blog](https://tableau.com/blog/advanced-management-manage-secure-and-scale-analytics) |
| Resource Monitoring Tool | Hardware and application metrics side by side (Advanced Management) | Current | P3 | Use CloudWatch | [Tableau blog](https://tableau.com/blog/advanced-management-manage-secure-and-scale-analytics) |
| Customer-managed encryption keys | Site-specific keys for extracts (Cloud, Advanced Management) | Current | P3 | AWS KMS covers this natively | [Advanced Management on Cloud](https://docs.salesforce.com/tableau/current/en-us/online/itm_intro.md) |

**2.12 Developer, embedding and integration**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| Embedding API v3 | Web components (`<tableau-viz>`, etc.); filtering via API attributes rather than URL params (Server 2022.3+) | Current | P1 (P0 if portals embed dashboards) | Enterprise intranets and portals embed views | [Embedding API](https://help.tableau.com/current/api/embedding_api/en-us/index.html); [View URL structure](https://help.tableau.com/current/pro/desktop/en-us/embed_structure.htm) |
| JavaScript API v1/v2 | Legacy embedding library | **Deprecated** since 2024.1 | P2 | Legacy portal code needs a shim or rewrite | [Embedding migration guide](https://help.tableau.com/current/api/embedding_api/en-us/docs/embedding_api_migration_guide.html) |
| Embed code parameters / URL parameters | Embed-code `param` options; URL-parameter filtering for prior embedding versions | Current | P1 | Old links and portals pass filters by URL; TabFree should accept legacy query strings | [Embed code parameters](https://help.tableau.com/current/pro/desktop/en-us/embed_list.htm); [View URL structure](https://help.tableau.com/current/pro/desktop/en-us/embed_structure.htm) |
| On-demand access (embedded) | JWT-based access without provisioning users (`https://tableau.com/oda` claim) | Current, **Cloud-only** (2023) | P3 | Not in Server estates | [Tableau blog](https://www.tableau.com/blog/on-demand-access-embedded-analytics) |
| Embedding Playground; Lightning Web Components | Embed-code generator (2023.3). Native Salesforce LWC (2023.3); composable views and Pulse LWC (2026.2) | Current | P3 | Salesforce-specific | [Tableau 2023.3](https://tableau.com/2023-3-features); [Releasebot](https://releasebot.io/updates/tableau) |
| REST API | Sites, users, groups, projects, permissions, publish/download content, schedules, subscriptions, jobs | Current | P1 (runtime) / **P0 as migration source** | The migrator extracts everything through it; admins have scripts that use it | [REST API](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api.htm) |
| Metadata API (GraphQL) | Lineage and field/calculation metadata (from Catalog) | Current | P2 (runtime) / P0 (migration inventory) | Ideal for inventorying calcs, LODs and data sources before migration | [Metadata API](https://help.tableau.com/current/api/metadata_api/en-us/index.html) |
| VizQL Data Service | Headless queries against published (and from 2026.3, workbook) data sources; live dashboard queries 2026.2 | Current | P3 (runtime) / P1 (migration validation: pull "golden" results) | Can supply reference numbers for regression tests | [VizQL Data Service](https://help.tableau.com/current/api/vizql-data-service/en-us/index.html); [Releasebot](https://releasebot.io/updates/tableau) |
| Webhooks | Events: Datasource/Workbook Created/Updated/Deleted and Refresh Started/Succeeded/Failed; ViewDeleted; Admin Promoted/Demoted; UserDeleted; Site Created/Updated/Deleted; Label Created/Updated/Deleted (Cloud only) | Current | P2 | Integrations (e.g., refresh-failure paging) | [Webhooks events](https://help.tableau.com/current/developer/webhooks/en-us/docs/webhooks-events-payload.html) |
| Extensions API (dashboard / viz extensions) | Web-app extensions: sandboxed (Server 2019.4+) or network-enabled; viz extensions on the Marks card (2024.2); Tableau-hosted trusted extensions (2025.3) | Current | P2 | Workbooks with write-back or other third-party extensions need case-by-case equivalents | [Dashboard extensions](https://help.tableau.com/current/pro/desktop/en-us/dashboard_extensions.htm); [Viz extensions](https://help.tableau.com/current/pro/desktop/en-us/viz_extensions.htm); [Server release highlights](https://docs.salesforce.com/tableau/current/en-us/server/ipa_server_whats_new.md) |
| Hyper API | Programmatic read/write of .hyper extract files (Parquet→Hyper sample documented) | Current | P0 **as migration tooling** | Converting .hyper extracts to Parquet/Iceberg for Athena | [Hyper API](https://help.tableau.com/current/api/hyper_api/en-us); [Parquet→Hyper sample](https://github.com/tableau/hyper-api-samples/blob/main/Community-Supported/parquet-to-hyper/create_hyper_file_from_parquet.py) |

**2.13 Platform operations (self-hosted Server) and multi-site**

| Feature | Description | Status | Priority | Justification | Source |
|---|---|---|---|---|---|
| TSM (Tableau Services Manager) | CLI, web UI and API for configuration, topology and maintenance | Current | P3 | Replace with IaC (CDK/Terraform) and managed AWS services | [TSM overview](https://help.tableau.com/current/server/en-us/tsm_overview.htm) |
| Multi-node and high availability | Redundant processes and failover across nodes | Current | P1 | Enterprises expect HA; on AWS use multi-AZ ECS/EKS + Aurora + S3 | [High availability](https://help.tableau.com/current/server/en-us/distrib_ha.htm) |
| Node roles / workload management | Dedicated backgrounder roles (extracts, subscriptions, flows) | Current | P2 | Job-queue isolation in TabFree | [Node roles](https://help.tableau.com/current/server/en-us/server_node_roles.htm) |
| Backup and restore | Repository + file store backup and restore | Current | P0 | Operational must-have | [Backup and restore](https://help.tableau.com/current/server/en-us/backup_restore.htm) |
| Query and data caching | Server query cache governed by freshness policy; viewers can refresh data | Current | P0 | Athena latency and per-query cost make a result cache essential | [Data freshness policy](https://help.tableau.com/current/server/en-us/data_freshness_policy.htm) |
| View Acceleration / Data Acceleration APIs | Pre-computed view queries | **Retired** Cloud June 2026 / Server 2026.2 | Skip | Retired | [View Acceleration](https://help.tableau.com/current/server/en-us/data_acceleration.htm) |
| Branding and customization | Server name, logos, sign-in note, welcome banner (240 chars), custom fonts, language and locale, project images | Current | P1 | Cheap; visible to every user | [Customize your server](https://help.tableau.com/current/server/en-us/customize.htm) |
| Multi-site administration | Create and manage sites, per-site admins and settings | Current | P1 | Needed for multi-site estates | [Add or edit sites](https://help.tableau.com/current/server/en-us/sites_add.htm) |
| Site settings (feature toggles) | e.g., enable Recycle Bin, custom schedules, AI in Tableau | Current | P1 | Per-tenant configurability | [Recycle Bin](https://help.tableau.com/current/server/en-us/recycle_bin.htm); [AI in Tableau](https://help.tableau.com/current/tableau/en-us/tableau_gai_solutions.htm) |
| Tableau Cloud Manager; capacity management; cross-region continuity; Private Connect | Cloud multi-site/multi-region management (2024.3); capacity shifting (2026.2); multi-region DR and AWS/Azure private connectivity (2026.3) | Current, **Cloud-only** | P3 | Not in Server estates | [2024.3 release blog](https://tableau.com/blog/release-tableau-cloud-manager-tables-viz-extension-spatial-parameters); [Releasebot](https://releasebot.io/updates/tableau) |
| Server container images, custom certificates CLI, RHEL 10 | Pre-built images and certificate CLI (2026.3); RHEL 10 support (2026.1) | New | P3 | Ops conveniences | [Releasebot](https://releasebot.io/updates/tableau); [Tableau 2026.1](https://www.tableau.com/2026-1-features) (via search snippet) |

### Inferences
- **The migration exercises the API surface as much as the UI.** Day one requires extracting projects, permissions, groups, users, subscriptions, custom views, favorites, alerts, schedules and usage from the Tableau REST API / repository / Metadata API, and converting .hyper extracts (Hyper API) into S3 tables. These are P0 *migration tools* even where the runtime feature is P1.
- **User-generated state is the silent failure mode.** Subscriptions, custom views, favorites, data-driven alerts and comments belong to users, not workbooks. If they are not migrated, users experience the migration as data loss, even when every dashboard renders correctly.
- **The permission model must be translated as "effective permissions", not copied rule by rule.** Site-role ceilings, deny-wins, locked projects and view-level permissions for tab-hidden workbooks can make naive rule copies over- or under-share.
- **TabFree should treat its own audit log and usage analytics as P1.** Server customers already have admin views. Admin Insights and Activity Log show what admins expect (sign-ins, publishes, view access, job performance, permissions, subscriptions, tokens).
- Because authoring is AI-driven, web edit, Personal Space, upload and Tableau Agent are not needed as UI (P3). Explorers who use web edit for ad-hoc analysis will still need a self-service path (e.g., "ask the AI for a variant"), which is P1 as a *capability*.

### Gaps
- Exact introduction versions for favorites, recents, comments, custom views, data-driven alerts, revision history and Recycle Bin were not found in fetched pages.
- No official confirmation was found that Pulse will ever run on Tableau Server; the "Cloud-only" status rests on Tableau's Pulse release notes ("Applies to: Tableau Cloud") and third-party reporting.
- The admin views' full list of built-in dashboards was not enumerated (page exists; content not extracted).
- No source found on whether Hyper can export directly to Parquet (only Parquet→Hyper is documented); an export path via Hyper API queries plus a Parquet writer is an inference.

---
## 3. Desktop / web authoring: complete feature catalog with migration priority and SQL-vs-front-end split

### Takeaway
For migrated dashboards, what matters is the *runtime semantics* of authored features, not the authoring UI. P0 covers these areas:
- **Charts and layout:** bar, line, text/crosstab tables with totals, filled/symbol maps, dual-axis combos, tiled and floating dashboard layouts with text and image objects.
- **Calculations:** calculated fields, LOD expressions, table calculations and quick table calcs; parameters, groups, aliases, hierarchies and drill.
- **Filtering and interaction:** dimension, measure, date, top-N and context filters with Tableau's order of operations; filter and highlight actions; tooltips.
- **Data modeling:** relationships, joins, custom SQL and blending semantics.

Most calculation and filter semantics belong in SQL (Athena/Redshift). Table calcs, blends and sets need both SQL and front-end state. Layout, actions, tooltips and formatting are front-end.

### Cited Findings
Layer, Priority and Justification are inferences; other columns are sourced.

**3.1 Connectors (AWS-first, plus common enterprise sources)**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Amazon Athena | JDBC connector; `athena.[region].amazonaws.com:443`, S3 staging directory, workgroup in server string; access keys or IAM OAuth/OIDC with Role ARN; Initial SQL; custom SQL; JDBC properties file | Current | SQL | P0 | TabFree's primary engine | [Amazon Athena](https://help.tableau.com/current/pro/desktop/en-us/examples_amazonathena.htm) |
| Amazon Redshift | ODBC connector; username/password, OAuth with IAM Role or IAM Identity Center; SSL; Initial SQL; "Show Other Databases"/datashares (2025.2, ODBC 2.x); Redshift Spectrum external S3 tables since Tableau 10.4 | Current | SQL | P0 | Second engine; many existing estates already use it | [Amazon Redshift](https://help.tableau.com/current/pro/desktop/en-us/examples_amazonredshift.htm); [AWS blog (Spectrum)](https://aws.amazon.com/blogs/big-data/tableau-10-4-supports-amazon-redshift-spectrum-with-external-amazon-s3-tables) |
| Amazon S3 (driverless) | Connect to files in a bucket (CSV/Excel/Parquet; wildcard union for CSV). Access-key auth only (no session token or role assumption); not in Prep web authoring or virtual connections | **Beta** (Exchange listing "Amazon S3 (beta)") | SQL | P1 | Workbooks reading S3 files directly → re-point to Athena external tables | [Amazon S3](https://help.tableau.com/current/pro/desktop/en-us/examples_amazons3.htm); [Tableau Exchange](https://extensiongallery.tableau.com/products/868) |
| File sources (Excel, text/CSV, etc.) | File-based connections, typically extracted | Current | SQL | P0 | File-based extracts are pervasive in enterprise workbooks; migration must land them in S3/Athena | [Supported connectors](https://help.tableau.com/current/pro/desktop/en-us/exampleconnections_overview.htm) |
| Other enterprise databases (SQL Server, Oracle, Snowflake, Databricks, PostgreSQL, MySQL, Teradata, SAP HANA, BigQuery, Salesforce, etc.) | Native connectors | Current | SQL | P1 | Reach via Athena federated queries or replicate to S3/Redshift | [Supported connectors](https://help.tableau.com/current/pro/desktop/en-us/exampleconnections_overview.htm) |
| Starburst | Connector with JWT for Galaxy (GA 2026.1) | New | SQL | P2 | Trino-family; dialect close to Athena | [Tableau 2026.1](https://www.tableau.com/2026-1-features) (via search snippet) |
| REST API connector / WDC | Web-API sources (WDC 2.x deprecated; REST API connector GA 2026.1) | Deprecated / New | SQL | P2 | Rebuild as ingestion | [Tableau KB](https://kb.tableau.com/articles/en_US/Issue/web-data-connectors-deprecated-in-2023-1-release); [HipsterVizNinja](https://www.hipstervizninja.com/blog/tableau-2026-1-new-features/) |
| Marketo / Oracle Eloqua | Marketing connectors | Removed 2026.1 (third-party reports) | SQL | P3 | Gone in Tableau too | [DataTerrain](https://dataterrain.com/analyzing-tableau-current-version) |

**3.2 Data modeling**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Relationships (logical layer) | Context-aware joins resolved per viz at query time; the default model since 2020.2 | Current | SQL | P0 | Common in post-2020 workbooks. The query generator must pick tables and join types per viz and aggregate at native grain | [Relationships](https://help.tableau.com/current/pro/desktop/en-us/datasource_relationships_learnmorepage.htm) |
| Multi-fact relationships (shared dimensions) | Multiple fact tables relate through shared dimensions (2024.2+) | Current | SQL | P2 | New; rare in estates so far | [Relationships](https://help.tableau.com/current/pro/desktop/en-us/datasource_relationships_learnmorepage.htm) |
| Physical joins | Inner, left, right and full-outer joins, incl. join calculations | Current | SQL | P0 | Ubiquitous | [Join your data](https://help.tableau.com/current/pro/desktop/en-us/joining_tables.htm) |
| Cross-database joins | Joins across different connections | Current | SQL | P1 | Needs federation or co-location in S3 | [Join your data](https://help.tableau.com/current/pro/desktop/en-us/joining_tables.htm) |
| Unions (manual and wildcard) | Append tables or files | Current | SQL | P1 | `UNION ALL` or S3 prefix tables | [Union](https://help.tableau.com/current/pro/desktop/en-us/union.htm) |
| Pivot | Columns-to-rows | Current | SQL | P1 | `UNNEST`/`CROSS JOIN` in SQL | [Pivot](https://help.tableau.com/current/pro/desktop/en-us/pivot.htm) |
| Data Interpreter | Cleans messy Excel/CSV layouts | Current | SQL (ingest) | P2 | Applied once at migration | [Data Interpreter](https://help.tableau.com/current/pro/desktop/en-us/data_interpreter.htm) |
| Custom SQL | Connect to a SQL query instead of tables | Current | SQL | P0 | Common in enterprise; needs dialect translation (e.g., T-SQL → Trino/Redshift) | [Custom SQL](https://help.tableau.com/current/pro/desktop/en-us/customsql.htm) |
| Initial SQL | SQL run on connect (can pass the Tableau user) | Current | SQL | P1 | Session setup and DB-side RLS | [Initial SQL](https://help.tableau.com/current/pro/desktop/en-us/connect_basic_initialsql.htm) |
| Data blending | Primary/secondary sources linked on fields; secondary aggregated then left-joined per viz | Current (legacy but widespread) | Both | P0 | Common in older workbooks. Must reproduce post-aggregation left-join semantics (incl. "*" for multiple values) | [Blend your data](https://help.tableau.com/current/pro/desktop/en-us/multiple_connections.htm) |
| Data types and roles | Dimension/measure; discrete/continuous (headers vs axes) | Current | Both | P0 | Determines rendering (headers vs axes) and aggregation | [Data types](https://help.tableau.com/current/pro/desktop/en-us/datafields_typesandroles_datatypes.htm) |
| Fiscal year / date properties | Fiscal year start and week settings | Current | SQL | P1 | Finance dashboards depend on it | [Fiscal dates](https://help.tableau.com/current/pro/desktop/en-us/dates_fiscal.htm) |
| Data source and extract filters | Filters applied to the whole source or extract | Current | SQL | P0 | Also an RLS boundary | [Data source filters](https://help.tableau.com/current/pro/desktop/en-us/filtering_datasource.htm) |

**3.3 Fields and calculations**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Calculated fields | Row-level and aggregate formulas; number, string, date, type-conversion, logical, aggregate, user, pass-through (RAWSQL) and spatial functions | Current | SQL | P0 | Present in nearly every workbook | [Formatting calculations / functions](https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_formulas.htm) |
| User functions (USERNAME, FULLNAME, ISMEMBEROF, USERDOMAIN) | Identity-aware calcs | Current | SQL | P0 | RLS dependency | [RLS options](https://help.tableau.com/current/server/en-us/rls_options_overview.htm) |
| LOD expressions (FIXED / INCLUDE / EXCLUDE) | Aggregate at a specified level independent of the viz. FIXED is computed before dimension filters (unless they are context filters) | Current | SQL | P0 | Very common and the top-cited migration difficulty | [LOD expressions](https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_lod.htm); [FIXED](https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_lod_fixed.htm); [Order of operations](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm) |
| Table calculations | Post-aggregation calcs with addressing/partitioning (table across/down, pane, cell, specific dimensions, restarting every) | Current | Both | P0 | Running totals, % of total, rank and YoY are everywhere. Use window functions or client-side computation | [Table calculations](https://help.tableau.com/current/pro/desktop/en-us/calculations_tablecalculations.htm); [Table calc functions](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_tablecalculation.htm) |
| Quick table calculations | One-click running total, difference, % difference, % of total, rank, percentile, moving average, YTD, compound growth, YoY | Current | Both | P0 | Most common table-calc entry point | [Quick table calcs](https://help.tableau.com/current/pro/desktop/en-us/calculations_tablecalculations_quick.htm) |
| Parameters | User inputs (list, range or all values) used in calcs, filters, reference lines and bins | Current | Both | P0 | Very common (metric switchers, top-N inputs, what-ifs) | [Create parameters](https://help.tableau.com/current/pro/desktop/en-us/parameters_create.htm) |
| Dynamic parameters | Refresh list values / current value from data when the workbook opens | Current | Both | P1 | Common in date-default patterns | [Create parameters](https://help.tableau.com/current/pro/desktop/en-us/parameters_create.htm) |
| Sets (manual, conditional, top N; combined sets; IN/OUT) | Subsets of members used in calcs, filters and encodings | Current | Both | P1 | Moderate frequency; translate to boolean SQL expressions plus front-end state | [Create sets](https://help.tableau.com/current/pro/desktop/en-us/sortgroup_sets_create.htm) |
| Groups | Ad-hoc member grouping (incl. "Other") | Current | SQL | P0 | Very common; CASE expressions | [Groups](https://help.tableau.com/current/pro/desktop/en-us/sortgroup_groups_creating.htm) |
| Bins | Fixed-size (parameterizable) numeric bins | Current | SQL | P1 | Histograms | [Bins](https://help.tableau.com/current/pro/desktop/en-us/calculations_bins.htm) |
| Hierarchies and drill-down | Field hierarchies with +/- drill; date hierarchies | Current | Both | P0 | Date drill is everywhere | [Hierarchies](https://help.tableau.com/current/pro/desktop/en-us/qs_hierarchies.htm) |
| Date parts vs date values; custom date formats | Discrete date parts vs continuous truncations; format strings | Current | Both | P0 | Affects grouping SQL and axis rendering | [Custom date formats](https://help.tableau.com/current/pro/desktop/en-us/dates_custom_date_formats.htm) |
| Aliases | Display labels for members | Current | FE (or SQL CASE) | P0 | Visible everywhere | [Aliases](https://help.tableau.com/current/pro/desktop/en-us/datafields_fieldproperties_aliases_ex1editing.htm) |
| Measure Names / Measure Values | Generated fields for multi-measure tables and charts | Current | Both | P0 | Crosstabs and KPI tables rely on them | [Measure Values and Measure Names](https://help.tableau.com/current/pro/desktop/en-us/datafields_understanddatawindow_meavalues.htm) |
| Default properties and number formats | Default aggregation, number format, color and sort per field | Current | FE | P0 | Number formatting fidelity is user-visible | [Format at workbook level](https://help.tableau.com/current/pro/desktop/en-us/formatting_workbook.htm) |
| Geographic roles | Assign geo roles to fields for built-in geocoding | Current | Both | P0 (if maps) | Maps are frequent | [Geographic roles](https://help.tableau.com/current/pro/desktop/en-us/maps_geographicroles.htm) |
| Dynamic axis ranges | Parameter- or calc-driven axis min/max (2023.3) | Current | FE | P2 | Newer; easy in JS | [Tableau 2023.3](https://tableau.com/2023-3-features) |
| Spatial calcs and spatial parameters | MAKEPOINT/BUFFER/etc.; spatial parameters and set-operation calcs (2024.3) | Current | SQL | P3 | Athena has geospatial functions if needed | [2024.3 release blog](https://tableau.com/blog/release-tableau-cloud-manager-tables-viz-extension-spatial-parameters) |
| Analytics-extension functions (SCRIPT_*) | Pass expressions to TabPy, R, MATLAB, Einstein Discovery | Current | SQL/external | P3 | Rare; external service | [Analytics extensions](https://help.tableau.com/current/pro/desktop/en-us/r_connection_manage.htm) |

**3.4 Visualization types (Show Me and mark types)**

| Viz type | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Bar (horizontal, stacked, side-by-side) | Bar mark charts | Current | FE | P0 | In 60% of sampled Tableau Public dashboards | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm); [Bar charts](https://help.tableau.com/current/pro/desktop/en-us/buildexamples_bar.htm); [Census (arXiv)](https://arxiv.org/pdf/2306.16513) |
| Line (continuous / discrete; dual lines) | Trend lines over time | Current | FE | P0 | In 25% of sampled dashboards | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm); [Census](https://arxiv.org/pdf/2306.16513) |
| Text table / crosstab (with totals, subtotals) | Tabular views | Current | Both | P0 | Tables in 24% of sampled dashboards; enterprise usage likely higher (inference) | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm); [Census](https://arxiv.org/pdf/2306.16513) |
| Maps: filled (choropleth) and symbol | Geocoded maps | Current | FE | P0 | Maps in 25% of sampled dashboards | [Build maps](https://help.tableau.com/current/pro/desktop/en-us/maps_build.htm); [Census](https://arxiv.org/pdf/2306.16513) |
| Dual-axis / combined-axis / dual combination | Two measures on synchronized or independent axes (e.g., bar + line) | Current | FE | P0 | Very common in KPI dashboards. Antares notes dense dual-axis views take design time | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm); [Antares](https://getantares.io/tableau-dashboard-complexity-analysis/) |
| KPI "big numbers" (text marks) | Single-value text tiles | Current | FE | P0 | Standard executive-dashboard pattern (inference) | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Highlight table / heat map | Color-encoded tables and square-mark grids | Current | FE | P1 | Common in ops reporting | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm) |
| Area (continuous / discrete) | Stacked areas | Current | FE | P1 | Moderate | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm) |
| Pie / donut | Pie mark with Angle (donut via dual-axis pie) | Current | FE | P1 | Common despite best practice | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm); [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Scatter plot | Two continuous measures | Current | FE | P1 | Analytical dashboards | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm) |
| Histogram | Binned distribution | Current | Both | P1 | Uses bins | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm) |
| Treemap | Nested rectangles | Current | FE | P2 | Occasional | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm) |
| Packed bubbles; circle views | Bubble packing / dot plots | Current | FE | P2 | Occasional | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm) |
| Box-and-whisker | Distribution summary | Current | Both | P2 | Analytical niche | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm) |
| Gantt | Gantt bar mark (also basis for waterfalls) | Current | FE | P2 | Project dashboards | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm) |
| Bullet graph | Actual vs target with bands | Current | FE | P2 | KPI niche | [Show Me](https://help.tableau.com/current/pro/desktop/en-us/buildauto_showme.htm) |
| Density map | Heat-style map mark | Current | FE | P2 | Occasional | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Waterfall | Gantt + running-sum pattern | Current (pattern) | Both | P2 | Sankey/waterfall found in <0.5% of sampled dashboards | [Census](https://arxiv.org/pdf/2306.16513) |
| Sankey, radial, other custom (polygon/densification) | Bespoke charts via polygons, data densification or viz extensions | Current | FE | P3 | <0.5% of sampled dashboards; AI can hand-build these | [Census](https://arxiv.org/pdf/2306.16513); [Viz extensions](https://help.tableau.com/current/pro/desktop/en-us/viz_extensions.htm) |
| Viz Extensions (Marks-card custom viz; Sankey etc.) and Table Viz Extension | Third-party or Tableau-built web viz types (2024.2; Table 2024.3) | Current | FE | P3 | New and rare | [Viz extensions](https://help.tableau.com/current/pro/desktop/en-us/viz_extensions.htm); [2024.3 release blog](https://tableau.com/blog/release-tableau-cloud-manager-tables-viz-extension-spatial-parameters) |

**3.5 Marks card and encodings**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Mark types | Automatic, Bar, Line, Area, Square, Circle, Shape, Text, Map, Pie, Gantt Bar, Polygon, Density | Current | FE | P0 (Bar, Line, Area, Circle, Square, Text, Map); P1 (Pie, Shape, Gantt); P2 (Polygon, Density) | Core rendering | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Color | Categorical and quantitative (sequential/diverging, stepped) palettes; per-member colors | Current | FE | P0 | Universal; legend-driven highlight (43% of interactive dashboards) | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm); [Census](https://arxiv.org/pdf/2306.16513) |
| Size | Size encoding | Current | FE | P1 | Common in scatter and maps | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Label / Text | Mark labels (all, min/max, most recent, selected) | Current | FE | P0 | Universal | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Detail | Adds grain without visual encoding | Current | SQL | P0 | Changes GROUP BY | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Tooltip | Custom tooltip text with fields | Current | FE | P0 | Universal | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Shape | Shape encoding incl. custom shapes | Current | FE | P1 | Moderate | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Path | Draw order for line/polygon marks | Current | FE | P2 | Custom charts | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Angle | Pie slice angle | Current | FE | P1 | Pies | [Mark properties](https://help.tableau.com/current/pro/desktop/en-us/viewparts_marks_markproperties.htm) |
| Shelves (Columns/Rows/Filters/Pages) and multiple Marks cards | Shelf-driven layout (small multiples/panes), per-measure Marks cards | Current | Both | P0 | Rows/Columns structure defines panes and headers | [Shelves and cards](https://help.tableau.com/current/pro/desktop/en-us/buildmanual_shelves.htm) |

**3.6 Analytics pane**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Totals / subtotals / grand totals | Row and column totals in tables and charts | Current | Both | P0 | Crosstabs require them; may need separate rollup queries (e.g., GROUPING SETS) | [Analytics pane](https://help.tableau.com/current/pro/desktop/en-us/environ_workspace_analytics_pane.htm) |
| Reference lines (constant, average, median, parameter) | Lines per table, pane or cell | Current | Both | P1 | Targets and averages are common | [Reference lines](https://help.tableau.com/current/pro/desktop/en-us/reference_lines.htm) |
| Reference bands and distributions (percentiles, std dev, quantiles) | Shaded bands | Current | Both | P2 | Less common | [Reference lines](https://help.tableau.com/current/pro/desktop/en-us/reference_lines.htm) |
| Box plots (analytics) | Quartile boxes | Current | Both | P2 | Niche | [Analytics pane](https://help.tableau.com/current/pro/desktop/en-us/environ_workspace_analytics_pane.htm) |
| Trend lines | Linear/log/exponential/polynomial/power with model stats | Current | Both | P1 | Regression in SQL or JS | [Trend lines](https://help.tableau.com/current/pro/desktop/en-us/trendlines_add.htm) |
| Forecasting | Exponential-smoothing forecasts | Current | Both | P2 | Occasional | [Forecast](https://help.tableau.com/current/pro/desktop/en-us/forecast_create.htm) |
| Clustering | k-means clusters | Current | Both | P3 | Rare in dashboards | [Clustering](https://help.tableau.com/current/pro/desktop/en-us/clustering.htm) |

**3.7 Filters**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Dimension filters (General / Wildcard / Condition / Top tabs) | Include/exclude members, patterns, conditions, top/bottom N | Current | SQL | P0 | Filter-control interactions appear in 69% of interactive dashboards | [Filter data](https://help.tableau.com/current/pro/desktop/en-us/filtering.htm); [Census](https://arxiv.org/pdf/2306.16513) |
| Measure filters | Range, at least, at most, special (null) | Current | SQL | P0 | Common | [Filter data](https://help.tableau.com/current/pro/desktop/en-us/filtering.htm) |
| Date filters | Relative dates, range of dates, discrete dates, latest date | Current | SQL | P0 | Relative-date filters drive "last N months" dashboards | [Filter data](https://help.tableau.com/current/pro/desktop/en-us/filtering.htm) |
| Top N / Bottom N | Rank-limited filters by field or formula | Current | SQL | P0 | Common; interacts with context filters | [Filter data](https://help.tableau.com/current/pro/desktop/en-us/filtering.htm) |
| Context filters | Filters applied before FIXED LOD and Top N | Current | SQL | P0 | Correctness depends on them | [Context filters](https://help.tableau.com/current/pro/desktop/en-us/filtering_context.htm); [Order of operations](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm) |
| Table-calculation filters | Filters evaluated after table calcs | Current | FE | P1 | Used in "hide but keep in calc" tricks | [Order of operations](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm) |
| Apply to worksheets (scope) | Only this sheet, selected sheets, all using this data source, all using related data sources | Current | FE | P0 | Dashboards share filters across sheets | [Filter data](https://help.tableau.com/current/pro/desktop/en-us/filtering.htm) |
| Only relevant values / all values in database / in hierarchy / in context | Cascading-filter domains | Current | SQL | P1 | Cascading filters are common; each needs a domain query | [Filter data](https://help.tableau.com/current/pro/desktop/en-us/filtering.htm) |
| Filter cards and modes | Single-value list/dropdown/slider; multi-value list/dropdown/custom list; wildcard; range sliders; relative-date and date-range controls; include/exclude; "(All)" | Current | FE | P0 | The main end-user control surface | [Filter data](https://help.tableau.com/current/pro/desktop/en-us/filtering.htm) |
| Order of operations | Extract → data source → context → FIXED LOD → dimension → INCLUDE/EXCLUDE → measure → … → table calc filters | Current | Both | P0 | The source of most "numbers don't match" migration defects | [Order of operations](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm) |

**3.8 Dashboards and layout**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Tiled layout and layout containers | Horizontal/vertical containers controlling resize behavior | Current | FE | P0 | Every dashboard | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm); [Floating and tiled](https://help.tableau.com/current/pro/desktop/en-us/dashboards_organize_floatingandtiled.htm) |
| Floating objects | Absolutely positioned, overlapping items | Current | FE | P0 | Common in designed dashboards (overlays, backgrounds) | [Floating and tiled](https://help.tableau.com/current/pro/desktop/en-us/dashboards_organize_floatingandtiled.htm) |
| Dashboard sizing and device-specific layouts | Desktop/tablet/phone layouts | Current | FE | P1 | Phone layouts matter for mobile users | [Device layouts](https://help.tableau.com/current/pro/desktop/en-us/dashboards_dsd_create.htm) |
| Text objects | Headers and explanations (incl. dynamic field/parameter text in titles) | Current | FE | P0 | Text is 21% of dashboard blocks in the census | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm); [Census](https://arxiv.org/pdf/2306.16513) |
| Image objects | Images with optional click-through URL (HTTPS; scripted SVG blocked) | Current | FE | P0 | Logos and icons ("multimedia" 7% of blocks) | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm); [Census](https://arxiv.org/pdf/2306.16513) |
| Web page objects | Embedded web pages (admins can block) | Current | FE | P2 | Occasional | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm) |
| Blank objects, padding, borders, backgrounds | Spacing and visual styling | Current | FE | P0 | Layout fidelity | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm) |
| Show/hide buttons | Toggle container visibility | Current | FE | P1 | Common for collapsible filter panels | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm) |
| Navigation buttons | Go to another dashboard, sheet or story | Current | FE | P1 | Multi-page "apps" | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm) |
| Download buttons | PDF, PowerPoint, PNG, or crosstab of selected sheets | Current | FE + server | P1 | Export UX | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm) |
| Dynamic zone visibility (DZV) | Show/hide zones based on a boolean, single-value field or parameter; pairs with parameter actions | Current | FE | P1 | Increasingly common in modern dashboards; cheap in JS | [Dynamic zone visibility](https://help.tableau.com/current/pro/desktop/en-us/dynamic_zone_visibility.htm) |
| Extension objects (dashboard extensions) | Web-app extensions (write-back, custom UI) | Current | FE | P2 | Case-by-case replacements | [Dashboard extensions](https://help.tableau.com/current/pro/desktop/en-us/dashboard_extensions.htm) |
| Pulse metric objects | Embed Pulse metric cards | Current (Cloud) | FE | P3 | Not in Server estates | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm) |
| Rounded corners | Native corner radius on dashboard objects (2026.1) | New | FE | P3 | Too new | [HipsterVizNinja](https://www.hipstervizninja.com/blog/tableau-2026-1-new-features/) |

**3.9 Actions and interactivity**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Filter actions | Select, hover or menu-triggered cross-filtering, with clear-selection behaviors | Current | Both | P0 | Chart→chart interaction in 46% of interactive dashboards | [Filter actions](https://help.tableau.com/current/pro/desktop/en-us/actions_filter.htm); [Census](https://arxiv.org/pdf/2306.16513) |
| Highlight actions (incl. legend highlighting) | Emphasize related marks | Current | FE | P0 | Legend→chart in 43% of interactive dashboards | [Highlight actions](https://help.tableau.com/current/pro/desktop/en-us/actions_highlight.htm); [Census](https://arxiv.org/pdf/2306.16513) |
| URL actions | Open links built from field values (new tab or web object) | Current | FE | P1 | Drill-out to CRM/ERP | [URL actions](https://help.tableau.com/current/pro/desktop/en-us/actions_url.htm) |
| Go to Sheet / navigation actions | Jump to another sheet or dashboard on interaction | Current | FE | P1 | Drill-through patterns | [Actions and dashboards](https://help.tableau.com/current/pro/desktop/en-us/actions_dashboards.htm) |
| Parameter actions | Set parameter values by clicking marks | Current | Both | P1 | Powers DZV and metric switching. Cited as harder to map in Power BI | [Parameter actions](https://help.tableau.com/current/pro/desktop/en-us/actions_parameters.htm); [CSA feature mapping](https://fgarofalo56.github.io/csa-inabox/migrations/tableau-to-powerbi/feature-mapping-complete/) |
| Set actions | Assign, add or remove set members on interaction | Current | Both | P1 | Hardest interactivity per Antares; must keep set state and re-query | [Set actions](https://help.tableau.com/current/pro/desktop/en-us/actions_sets.htm); [Antares](https://getantares.io/tableau-dashboard-complexity-analysis/) |
| View underlying / summary data from marks | Inspect and download data behind marks | Current | SQL | P1 | Permission-gated analyst feature | [View underlying data](https://help.tableau.com/current/pro/desktop/en-us/inspectdata_viewdata.htm) |
| Sorting (computed, header/axis sort icons, nested, manual) | Sort by field, alphabetic, manual or data-source order | Current | Both | P0 | Universal | [Sorting](https://help.tableau.com/current/pro/desktop/en-us/sortgroup_sorting_computed_howto.htm) |
| Drill up/down | +/- on hierarchies | Current | Both | P0 | Universal for dates | [Hierarchies](https://help.tableau.com/current/pro/desktop/en-us/qs_hierarchies.htm) |

**3.10 Stories, tooltips, formatting**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Stories | Sequenced story points with captions and navigator | Current | FE | P2 | Less common in operational BI | [Stories](https://help.tableau.com/current/pro/desktop/en-us/story_create.htm) |
| Viz in Tooltip | A worksheet rendered inside a tooltip, filtered to the hovered mark (not on totals since 2023.3) | Current | Both | P1 | Increasingly common; a nested render plus filtered query | [Viz in Tooltip](https://help.tableau.com/current/pro/desktop/en-us/viz_in_tooltip.htm) |
| Workbook / worksheet formatting | Fonts, lines, shading, borders at workbook, sheet, pane and header level | Current | FE | P0 | Visual fidelity | [Format at workbook level](https://help.tableau.com/current/pro/desktop/en-us/formatting_workbook.htm) |
| Number and date formats; units | Currency, %, custom formats, K/M/B units, locale | Current | FE | P0 | Visible everywhere | [Custom date formats](https://help.tableau.com/current/pro/desktop/en-us/dates_custom_date_formats.htm); [Format at workbook level](https://help.tableau.com/current/pro/desktop/en-us/formatting_workbook.htm) |
| Annotations | Mark, point and area annotations | Current | FE | P2 | Occasional | [Annotations](https://help.tableau.com/current/pro/desktop/en-us/annotations_annotations_add.htm) |
| Dark mode vizzes; transparent tooltips | Theming features (2026.3) | New | FE | P3 | Too new | [Releasebot](https://releasebot.io/updates/tableau) |

**3.11 Maps and spatial**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Built-in geocoding | Country/region, state/province, county, city, postal code, airport, CBSA/MSA, congressional district, etc. | Current | Both | P0 | Needed for any standard-geography map | [Geographic roles](https://help.tableau.com/current/pro/desktop/en-us/maps_geographicroles.htm) |
| Map layers (multiple marks layers) | Several layers on one map | Current | FE | P2 | Newer designs | [Map layers](https://help.tableau.com/current/pro/desktop/en-us/maps_marks_layers.htm) |
| Custom geocoding | Import custom locations | Current | Both | P2 | Some estates | [Custom geocoding](https://help.tableau.com/current/pro/desktop/en-us/custom_geocoding.htm) |
| Spatial files | Shapefile, KML, GeoJSON, TopoJSON, MapInfo, Esri File Geodatabase; spatial joins | Current | Both | P1 | Custom sales territories are common in enterprise (inference) | [Spatial files](https://help.tableau.com/current/pro/desktop/en-us/maps_shapefiles.htm) |
| Background map sources | Tableau, Mapbox and WMS basemaps and map layers | Current | FE | P2 | Use an OSS basemap | [Map sources](https://help.tableau.com/current/pro/desktop/en-us/maps_mapsources.htm) |
| Background images | Plot x/y on custom images (floor plans) | Current | FE | P3 | Rare | [Background images](https://help.tableau.com/current/pro/desktop/en-us/bkimages.htm) |
| Mixed-geometry maps; drive-time areas | 2026.1 / 2026.2 features | New | Both | P3 | Too new | [HipsterVizNinja](https://www.hipstervizninja.com/blog/tableau-2026-1-new-features/); [Releasebot](https://releasebot.io/updates/tableau) |

**3.12 Exports, accessibility, localization and misc.**

| Feature | Description | Status | Layer | Priority | Justification | Source |
|---|---|---|---|---|---|---|
| Export image | BMP/JPEG/PNG/SVG from Desktop; PNG from web | Current | FE/server | P1 | Common for slide decks | [Export views](https://help.tableau.com/current/pro/desktop/en-us/save_export_image.htm) |
| Export PDF | PDF of view, dashboard or workbook | Current | Server render | P0 | Board packs and subscriptions | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm); [Subscriptions](https://help.tableau.com/current/server/en-us/subscribe_user.htm) |
| Export crosstab to Excel / CSV | Crosstab of view data to Excel; data to CSV/Access | Current | SQL + server | P0 | Daily enterprise behavior | [Export data](https://help.tableau.com/current/pro/desktop/en-us/save_export_data.htm) |
| Export PowerPoint | Slides of dashboards | Current | Server render | P2 | Occasional | [Create a dashboard](https://help.tableau.com/current/pro/desktop/en-us/dashboards_create.htm) |
| Accessibility | Accessible views and keyboard use; keyboard-only pill placement 2026.1 | Current | FE | P1 (P0 for public sector) | Legal requirement for many organizations | [Accessibility](https://help.tableau.com/current/pro/desktop/en-us/accessibility_overview.htm); [Tableau 2026.1](https://www.tableau.com/2026-1-features) (via search snippet) |
| Localization | UI language and locale for views (number/date formats) | Current | FE | P1 | Global orgs | [Customize your server](https://help.tableau.com/current/server/en-us/customize.htm) |
| Pages shelf / animation playback | Page through values with playback | Current | FE | P3 | Rare in enterprise | [Shelves and cards](https://help.tableau.com/current/pro/desktop/en-us/buildmanual_shelves.htm) |
| Dashboard Starters / Accelerators | Prebuilt templates with data mapper (2023.1/2023.2) | Current | n/a | P3 | Authoring aid | [Accelerators](https://help.tableau.com/current/online/en-us/accelerators.htm) |
| Web authoring (edit in browser) | Create and edit workbooks in the browser (Creators/Explorers) | Current | n/a | P3 as UI; P1 as a self-service capability (via AI) | Replaced by the AI author | [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm) |

### Inferences
- **Recommended SQL/FE architecture split.** Compile everything above the aggregation boundary to SQL: calcs, LOD, groups, bins, filters, context, Top N, relationships and joins, blends as post-aggregation joins, RLS. Do table calcs either as SQL window functions or in a small JS "table calc engine" that supports Tableau addressing/partitioning. Keep layout, actions, DZV, show/hide, tooltips, VIT and formatting in the front end, with a shared state store (filters, parameters, sets, selections) that re-queries.
- **Priority is about migrated-dashboard fidelity.** A feature can be P0 even though nobody will *author* it in the Tableau sense, because existing workbooks use it and the AI translator plus runtime must reproduce it (e.g., blending, quick table calcs).
- **The highest-leverage engineering investment is a Tableau-semantics conformance suite.** It should compare TabFree numbers against Tableau's own exports (crosstab CSV, VizQL Data Service) for LOD/table-calc/context-filter/blend cases, because the order of operations drives silent numeric drift.

### Gaps
- No primary source quantifies how often enterprise (non-Public) workbooks use LODs, table calcs, sets, blends, DZV or Viz-in-Tooltip. The P0/P1 split for those relies on migration-vendor rubrics and judgment.
- Introduction versions for many authoring features (DZV, parameter actions, dynamic parameters, map layers, image role) were not verified in fetched pages.
- Image role (images from URLs in table cells) and some workbook-theme details were not verified and are not catalogued as separate rows.

---
## 4. Which features most commonly appear in real-world workbooks (evidence for P0/P1)?

### Takeaway
No public dataset gives feature frequencies for *enterprise* Tableau workbooks. The best quantitative proxy is Tableau Research's census of 25,620 multi-chart Tableau Public dashboards:
- Bar charts appear in 60% of dashboards; line charts, maps and tables in about a quarter each; Sankey/waterfall-type bespoke charts in under 0.5%.
- Three quarters of dashboards are interactive, mostly through filter controls (69% of interactive dashboards), chart-to-chart actions (46%) and legend highlighting (43%).

Migration vendors agree that *effort* is driven by LODs, table calcs, sets/set actions, blends, custom SQL and chained actions, not by sheet or dashboard counts.

### Cited Findings
- **Corpus.** Tableau Public holds ~5M workbooks. 1,342,794 (~25%) met the minimum spec version, and only 150,276 (11%) of those contained a dashboard. The top 10% by impressions gave 15,090 workbooks and 42,951 dashboards; the 25,620 dashboards with ≥2 charts were analyzed (published 2011–2021, >99% after 2014). Authors are from Univ. of Maryland, Tableau Research and UW. — [Purich et al., "Toward a Scalable Census of Dashboard Designs in the Wild" (arXiv 2306.16513)](https://arxiv.org/pdf/2306.16513)
- **Dashboard composition.** 250,794 blocks in total; 2–267 per dashboard, median 8. Charts are 49% of blocks, text 21%, filter widgets 15%, legends 9%, images/multimedia 7%. Charts and text together are >70% of blocks. — [arXiv 2306.16513](https://arxiv.org/pdf/2306.16513)
- **Chart types.** Bar charts in 15,392 of 25,620 dashboards (60%); line 6,524 (25%); maps 6,454 (25%); tables 6,154 (24%). Bespoke charts such as Sankey and waterfall appear in only 116 dashboards (<0.5%). — [arXiv 2306.16513](https://arxiv.org/pdf/2306.16513)
- **Interactivity.**
  - Share interactive: 19,304 of 25,620 dashboards (75%).
  - Interaction connections per dashboard: 1 to 992, median 6, mode 2. On average 58% of possible interactions are wired.
  - By type: filter→chart in 69% of interactive dashboards, chart→chart in 46%, legend→chart in 43%.
  - Only 30% of the 343,929 interactions connect adjacent blocks.
  - Source: [arXiv 2306.16513](https://arxiv.org/pdf/2306.16513)
- **Calculated-field volume (anecdotal).** Pulling calculated fields from public .twbx files via the Tableau Public MCP server suggests workbooks with more than 50 calculated fields are common. — [Vizney](https://www.vizney.com/analyzing-tableau-public-dashboards-with-claude/)
- **Calculated-field hygiene.** A Metadata API data dictionary built over a Tableau site found many calculated fields duplicated or unused. — [Anyalitica](https://anyalitica.dev/blog/2021-01-11-tableau-data-dictionary-metadata) (via search snippet)
- **Effort predictors.** "Visual, sheet and calculated-field counts correlate weakly with effort." Dashboard retirement decisions use 90-day view counts from Tableau admin views. — [Antares complexity rubric](https://getantares.io/tableau-dashboard-complexity-analysis/)
- **Scoring inputs.** A Power BI consultancy scores each workbook on counts of calculated fields, LOD expressions, table calculations, parameters and sets. It notes that heavy-LOD, table-calc and custom-SQL workbooks take much longer to convert. — [PowerBIConsulting guide](https://powerbiconsulting.com/blog/tableau-to-power-bi-migration-guide-2026) (via search snippet)

### Inferences
- The census supports P0 for bar, line, text-table/crosstab, and filled/symbol maps; filter cards; filter and highlight actions (incl. legend highlighting); and text/image dashboard objects. Bespoke charts (Sankey, waterfall, radial) are long-tail (P2–P3), and the AI author can hand-build them on demand.
- Enterprise estates likely differ from Tableau Public. They probably have more crosstabs, Excel/PDF exports, published data sources, RLS and subscriptions, and less bespoke design. This is unverified, which is why exports, subscriptions and RLS are P0 on operational grounds rather than census grounds.
- **Measure the actual source estate before locking the backlog.** Parse every .twb (XML) to count calculations by type ("{FIXED"/"{INCLUDE"/"{EXCLUDE", table-calc functions and addressing), blends (multiple datasources per worksheet with linking fields), action types (filter/highlight/URL/sheet/parameter/set), zones (floating, DZV, buttons, extensions, web objects), viz-in-tooltip references, user filters and custom SQL. Weight each count by view traffic from the repository/REST usage data, and retire dashboards with no 90-day views before converting them.
- Calculated-field duplication suggests TabFree should consolidate calcs into published "semantic" data sources during migration rather than copy them per workbook.

### Gaps
- No quantitative enterprise survey (e.g., from a migration vendor's scanned estates) of LOD, table-calc, set, blend, DZV or VIT frequency was found. Vendor pages give qualitative rubrics only.
- The census did not analyze calculations, parameters, sets, LODs, tooltips or dashboard actions beyond filter/highlight/navigation edges. Its corpus predates 2022 features such as DZV.
- The census is Tableau Public's most-viewed 10%, which is biased toward designed, storytelling dashboards.

---

## 5. Which Tableau features are hardest to migrate to other BI tools?

### Takeaway
Across Tableau→Power BI migration guidance:
- **Calculations:** LOD expressions and table calculations (running/window calcs and complex addressing) are consistently the hardest.
- **Interactivity:** sets, set actions and chained dashboard actions are called the hardest category because the target tool has no single equivalent object.
- **Modeling:** blends must become explicit models.
- **Layout and visuals:** dual-axis, custom shapes, dense crosstabs, bespoke maps and bespoke layouts take per-view design time.

Viz-in-tooltip and DZV are rarely rated hard for Power BI (page tooltips and bookmarks), and no Looker- or Sigma-specific difficulty data was found.

### Cited Findings
- **Power BI difficulty ratings ([CSA feature mapping](https://fgarofalo56.github.io/csa-inabox/migrations/tableau-to-powerbi/feature-mapping-complete/)):**

| Rating | Tableau features (Power BI equivalent / note) |
|---|---|
| High | LOD FIXED (CALCULATE + ALLEXCEPT); INCLUDE (iterators over VALUES); EXCLUDE (CALCULATE + ALL); RUNNING_SUM; WINDOW_SUM/AVG; density/hex-bin maps |
| Medium | RANK; LOOKUP; PERCENT_DIFFERENCE; INDEX; sets ("No native set concept"); data blending ("Data blending does not exist in Power BI"); set actions; parameter actions ("More limited than Tableau parameter actions"); packed bubble, box plot, Gantt, bullet (custom visuals); Stories; RLS; Pulse |
| Low | Viz in tooltip (report-page tooltips); custom views; subscriptions; data-driven alerts; comments |

- **Antares rubric ([Antares](https://getantares.io/tableau-dashboard-complexity-analysis/)):**
  - "Complex" means nested LOD logic over blended sources, custom SQL, sets and set actions, dense dual-axis views, bespoke layouts, and row-level filters.
  - Interactivity is the hardest category: "Sets, set actions and chained dashboard actions have no single Power BI object behind them." One set action can outweigh "forty plain calculated fields."
  - "Bar, line and scatter charts map across directly." "Dual-axis combinations, custom shapes, dense cross-tabs and bespoke maps take design time per view."
  - Score a workbook by its *highest* dimension, not the average.
- **Antares on blends and custom SQL:** both must be rebuilt as an explicit model with relationships. — [Antares](https://getantares.io/tableau-dashboard-complexity-analysis/)
- **Table calculations** are called "the hardest 10%" because they depend on the visual's layout. LODs become CALCULATE with ALLEXCEPT/REMOVEFILTERS evaluated in filter context. — [Tableau→Power BI migration playbook (GitHub)](https://github.com/Hakian14/tableau-to-powerbi-migration-playbook) (via search snippet)
- **Redesign over transcription:** Microsoft guidance notes teams often re-implement Tableau interactivity with native features (bookmarks, drilldowns, simpler navigation) instead of copying Tableau workarounds. — [Microsoft Tech Community](https://techcommunity.microsoft.com/t5/analytics-on-azure-blog/tableau-to-power-bi-semantic-layer-first-approach-for-cloud/ba-p/4481009) (via search snippet)
- **Tableau semantics behind subtle mismatches:**
  - FIXED LODs are computed before dimension filters unless those filters are added to context; Top N and FIXED therefore depend on context filters. — [Order of operations](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm); [Context filters](https://help.tableau.com/current/pro/desktop/en-us/filtering_context.htm)
  - Relationships aggregate at the native grain of each table to avoid fan-out. — [Relationships](https://help.tableau.com/current/pro/desktop/en-us/datasource_relationships_learnmorepage.htm)
  - Blends aggregate the secondary source before joining. — [Blend your data](https://help.tableau.com/current/pro/desktop/en-us/multiple_connections.htm)

### Inferences
TabFree targets SQL plus custom HTML/JS rather than DAX and a fixed visual set, so its difficulty profile differs from Power BI's. Estimated difficulty for TabFree's AI translator plus runtime:

| Feature | Why it is hard | TabFree approach | Difficulty for TabFree |
|---|---|---|---|
| Table calcs with non-default addressing/partitioning, nested table calcs, table-calc filters | Depend on viz layout (which dimensions are "across/down") and run after aggregation | Emit SQL window functions where partitioning is static; otherwise a client-side table-calc engine mirroring Tableau semantics | High |
| LOD expressions (nested LODs; INCLUDE/EXCLUDE relative to viz grain; FIXED vs context filters) | Results depend on viz level of detail and order of operations | FIXED → pre-aggregated subquery joined on fixed dims; INCLUDE/EXCLUDE → nested aggregation at viz grain; honor context-filter placement | High (SQL-native but semantics-heavy) |
| Order of operations (context, data source, Top N, measure, table-calc filters) | Silent numeric drift | Central query planner with explicit filter stages; conformance tests | High |
| Data blending | Post-aggregation left join on linking fields; "*" for multiple values; secondary filters | Generate per-viz SQL: aggregate secondary at linking grain, LEFT JOIN | Medium–High |
| Relationships (logical layer) | Join culling and native-grain aggregation per viz | Query planner choosing tables per viz; aggregate before join | Medium–High |
| Totals/subtotals with non-additive measures (COUNTD, AVG, ratios) | Totals must be recomputed, not summed | GROUPING SETS / ROLLUP or separate total queries | Medium |
| Sets + set actions, parameter actions, chained actions | Stateful interactivity driving re-queries | Front-end state store (filters/params/sets/selection) and a declarative action graph | Medium |
| Viz in Tooltip | Nested render filtered by hovered mark; per-hover queries | Lazy per-mark query with caching | Medium |
| Custom maps (custom geocoding, spatial files, map layers, density) | Geometry pipelines and basemaps | GeoJSON in S3 + open-source map library; Athena geospatial where needed | Medium |
| Pixel-faithful layouts (floating overlays, device layouts) | Tableau's container/sizing model | CSS grid/flex + absolute layers; per-device breakpoints | Medium |
| Custom SQL dialect translation | T-SQL/Oracle/Snowflake SQL → Trino (Athena) or Redshift | LLM translation + validation queries | Medium |
| RLS translation (user filters, ISMEMBEROF, entitlement joins) | Security-critical; group semantics | Central policy layer injecting predicates; tests per persona | Medium (high stakes) |
| DZV, show/hide buttons, navigation buttons | Simple boolean visibility | Front-end only | Low |
| Dashboard/viz extensions | Arbitrary third-party JS | Case-by-case rewrite | Varies |

### Gaps
- No Looker- or Sigma-specific complexity guides were found in this research; the evidence is Power BI-centric.
- No source quantifies viz-in-tooltip or DZV migration difficulty; the CSA mapping rates VIT "Low" for Power BI and has no DZV row.
- Most migration sources are vendor marketing or consultancy blogs whose effort claims are not independently verified.

---

## 6. P0 summary: what must exist on day one

### Takeaway
Day one must provide the core platform, faithful data/query semantics, core rendering and interaction, and the migration tooling (details below). Everything else can follow in P1 waves.

### Cited Findings
- **Content and permissions:** workbooks, views and published data sources are the permissioned content types, and capabilities include View, Filter, Download Image/PDF, Download Summary/Full Data, Overwrite, Move, Delete and Set Permissions. — [Permission capabilities](https://help.tableau.com/current/server/en-us/permissions_capabilities.htm)
- **Site roles:** Viewer, Explorer, Explorer (can publish), Creator and the admin roles cap capabilities. — [Site roles](https://help.tableau.com/current/server/en-us/users_site_roles.htm)
- **Subscriptions** deliver PNG/PDF on schedules or on data refresh. — [Subscriptions](https://help.tableau.com/current/server/en-us/subscribe_user.htm)
- **RLS** relies on user filters, USERNAME()/ISMEMBEROF() calcs with entitlement tables, and data source filters. — [RLS options](https://help.tableau.com/current/server/en-us/rls_options_overview.htm); [User filters](https://help.tableau.com/current/pro/desktop/en-us/publish_userfilters.htm)
- **Usage evidence:** bar, line, maps and tables dominate, and filter controls plus filter/highlight actions dominate interaction. — [arXiv 2306.16513](https://arxiv.org/pdf/2306.16513)
- **Hardest semantics:** LOD, table calcs, sets/actions and blends are the effort drivers. — [Antares](https://getantares.io/tableau-dashboard-complexity-analysis/); [CSA mapping](https://fgarofalo56.github.io/csa-inabox/migrations/tableau-to-powerbi/feature-mapping-complete/)

### Inferences: P0 list

**Platform / server (day one)**
1. Tenancy and content tree: at least one site (multi-site P1), projects and **nested projects** with preserved paths; workbooks → dashboards/views (honoring "show sheets as tabs") and **published data sources** as first-class objects.
2. Users and **groups**, with Tableau site roles mapped to TabFree roles (Viewer / Explorer / Explorer-can-publish / Creator / Site admin / Server admin) as capability ceilings.
3. **Permission engine** with allow/deny/unspecified rules for users and groups, project default permissions, **locked projects**, view-level permissions, owner rights, and effective-permission evaluation (deny wins, role ceilings), at least for View, Filter/interact, Download Image/PDF, Download Summary/Full Data, Publish/Overwrite, Move/Delete and Set Permissions.
4. **SSO via SAML 2.0 or OIDC**, plus local accounts for admins and service users.
5. **Row-level security:** user filters; USERNAME()/FULLNAME()/ISMEMBEROF()-based calcs with entitlement tables; non-removable data-source filters.
6. **Search, Explore/browse and Home** (favorites and recents are P1 but cheap).
7. **Scheduled data refresh** (full) with job status and failure notification, plus a **query/result cache** with a freshness policy (Athena latency and cost).
8. **Email subscriptions** (view or workbook; PNG/PDF; schedule or on-refresh).
9. **Exports:** PDF, PNG and crosstab (Excel/CSV), all permission-gated.
10. **Ops basics:** backup/restore, HA on AWS, audit logging of sign-in, view and export events.
11. **Stable links** (inference): redirects or URL compatibility from old Tableau view URLs, so bookmarks and links in emails and wikis keep working.

**Data / query semantics (SQL layer)**
1. Live Athena and Redshift connections with IAM/service credentials; legacy extracts (.hyper) re-materialized as S3/Athena or Redshift tables.
2. Data model translation: **relationships** (per-viz join culling, native-grain aggregation), **physical joins**, **custom SQL** (dialect-translated), **data blending** semantics.
3. Calculated fields: full core function library (logical, string, date, number, type conversion, aggregates, user functions).
4. **LOD expressions** (FIXED / INCLUDE / EXCLUDE) and **Tableau's order of operations** (data source → context → FIXED → dimension → measure → table-calc filters).
5. **Table calculations and quick table calcs** (running total, % of total, difference/% difference, rank, moving average, YoY/YTD) with addressing/partitioning (SQL window functions or client engine).
6. **Filters:** dimension (include/exclude, wildcard, condition), measure ranges, **relative and range date filters**, **Top N**, **context filters**, data source filters.
7. **Parameters** in calcs and filters; **groups**, **aliases**, **hierarchies/drill**, date parts vs continuous dates, Measure Names/Values.
8. **Totals and subtotals** recomputed correctly for non-additive measures.

**Rendering / interaction (front end)**
1. Viz types: **bar** (stacked, side-by-side), **line**, **text table/crosstab** (+ totals), **filled and symbol maps** (built-in geocoding), **dual-axis combos**, KPI text tiles.
2. Encodings: color, label, detail, tooltip (size/shape/angle P1), number/date formats and default field formatting.
3. Dashboards: **tiled + floating layouts with containers**, fixed/automatic sizing, **text and image objects**, legends, and filter/parameter cards in Tableau's control styles (dropdown, list, slider, date range/relative).
4. **Filter actions and highlight actions** (incl. legend highlighting); "apply filter to selected/all worksheets" scope.
5. Sorting (incl. header sort) and drill up/down.

**Migration tooling (P0 even though not end-user features)**
1. Extraction via the Tableau REST API, Metadata API and repository: content, permissions, groups, users, schedules, subscriptions, custom views, favorites, alerts, usage.
2. .hyper → Parquet/Iceberg conversion (Hyper API), loaded to S3 and Athena/Redshift.
3. .twb XML parser + AI translator, with a **numeric conformance suite** comparing TabFree outputs to Tableau crosstab/VizQL Data Service results.
4. Inventory and usage-weighted triage that retires unused content before conversion.

**First P1 wave (immediately after day one):**
- Interactivity: sets and set actions; parameter actions; DZV; show/hide and navigation buttons; Viz in Tooltip; URL and go-to-sheet actions; device layouts.
- User state: custom views (promote to P0 if usage is high); data-driven alerts; conditional subscriptions; favorites and recents; sharing; view underlying data.
- Admin: admin usage views; revision history; branding; multi-site.
- Identity and APIs: Embedding API v3 / connected apps (P0 if portals embed); REST API for admins; AD/LDAP group sync or SCIM.
- Data: incremental refresh; certification.
- Charts and analytics: spatial files; reference and trend lines; scatter, pie, area, heatmap and histogram; unions, pivots, Initial SQL and cross-database joins.
- Experience: accessibility; localization.

### Gaps
- P0 boundaries for custom views, embedding and multi-site depend on the specific customer estate (usage telemetry not available to this research).
- No enterprise-level frequency data was found to confirm that blending and table calcs are prevalent enough for P0 in every estate. They are kept P0 because migration vendors flag them as top effort drivers and silent-error risks.
