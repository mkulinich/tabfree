# GitHub sweep: open-source code that implements parts of a "Tableau clone / Tableau migration" platform (TabFree)

All star counts, push dates, licenses and languages below were read from the GitHub REST API (`gh api`) on 2026-11-20. Activity labels: **active** = pushed on or after 2026-05-20; **recent** = pushed 2025-11-20 to 2026-05-19; **stale** = pushed before 2025-11-20; **archived** = archived flag set on GitHub. "Reuse" ratings (High/Medium/Low) are my judgement of value to TabFree, not a property of the repo.

## Q1. Does any repo already deliver the whole TabFree vision (Tableau Server migration + web/HTML or AI-authored dashboards + Athena/Redshift + permissions)?

### Takeaway
No. Nothing open-source combines Tableau Server migration, AI-authored HTML dashboards, Athena/Redshift storage and enforced permissions. The closest is **Querio-App/unshackle** (Apache-2.0, Python, repo created 2026-10-22, v0.3.0 on 2026-11-19). It pulls content from Tableau Server or Cloud and turns calculations, dashboards, group permissions and row-level filters into open files, then checks the numbers against Tableau. But it writes Jupyter notebooks, not hosted HTML dashboards. It has no server that enforces permissions, no Athena or Redshift loading, and none of Tableau Server's operational features. By my estimate it covers about a third of TabFree, all of it in the migration and translation half.

### Cited Findings
**Search coverage (method note, my own process):** 173 GitHub repository/topic searches (including topic:tableau, topic:tableau-server, topic:tableau-api, topic:hyper-api, topic:tableau-alternative, topic:bi-migration, topic:twb, topic:tableau-mcp and others). Also 24 GitHub code searches (ANTLR/Lark grammars, `datasource-dependencies` parsers in TS/JS/Go/Rust/Java, twb+pbip/lookml/superset/vega-lite/echarts/quicksight/metabase/plotly/streamlit), a full scan of the PyPI simple index (945,045 project names, 86 Tableau-related names), npm registry searches, GitLab project searches, and 5 web searches. READMEs and metadata of about 150 repos were read through `gh api`. No repo was cloned or run.

- unshackle "moves your metrics, dashboards and permissions out of a BI tool and into files in a Git repository you own". It maps calculated fields to Apache Ossie metrics, dashboards to Jupyter notebooks (one SQL query and one chart per tile), permissions and row-level security to Cedar policies and SQL row filters, and descriptions to Markdown. Of the 13 BI tools it supports, Tableau is rated "Most complete" — [unshackle README](https://github.com/Querio-App/unshackle)
- For Tableau, unshackle connects with a personal access token (PAT) to Server or Cloud, "takes inventory, downloads workbooks and data sources", and maps:
  - FIXED LODs to Ossie metrics
  - worksheets, dashboards and stories to notebooks, with filters and parameters as notebook parameters
  - group permissions on workbooks to Cedar policies
  - `USERNAME()`/`ISMEMBEROF()` user filters to SQL row filters

  Left as TODO: table calculations, INCLUDE/EXCLUDE LODs, FIXED LODs on several dimensions, parameters inside metrics, custom SQL, some blends, cross-database joins, some sets/groups/bins, user-level grants without an email or group, and project default permissions — [unshackle Tableau doc](https://github.com/Querio-App/unshackle/blob/main/docs/tools/tableau.md)
- unshackle copies `.hyper` extract data to a git-ignored `data/` folder only through Tableau's Hyper API (`pip install "unshackle[hyper]"`). `unshackle verify --init tableau` builds parity cases from Tableau's saved view data, and `migrate` saves the data of the 20 most viewed views — [unshackle Tableau doc](https://github.com/Querio-App/unshackle/blob/main/docs/tools/tableau.md)
- unshackle has an MCP server (`overview`, `search`, `read`, bounded `run_query`) with deployment guides for Railway, AWS and Cloud Run. Its README says "Policies are agent context and are not enforced by `run_query`" — [unshackle README](https://github.com/Querio-App/unshackle)
- Apache Ossie, unshackle's target format, is an "industry wide specification effort to standardize how we exchange semantic metadata across analytics, AI and BI platforms" (2,763 stars) — [apache/ossie](https://github.com/apache/ossie). Cedar is at [cedar-policy/cedar](https://github.com/cedar-policy/cedar) (1,890 stars).
- The other end-to-end AI migration tools all target proprietary BI platforms:
  - Tableau/Looker/Power BI to **Hex**, with a screenshot-diff parity loop — [Humphrey4data/tableau-to-hex-skill](https://github.com/Humphrey4data/tableau-to-hex-skill) (91 stars) and [hex-inc/hex-skills](https://github.com/hex-inc/hex-skills)
  - Tableau to **Sigma**, plus `tableau-vds-to-cdw`, which pulls datasource data through the VizQL Data Service and lands it in Snowflake or Databricks — [sigmacomputing/sigma-migration-skills](https://github.com/sigmacomputing/sigma-migration-skills)
  - Tableau to **ThoughtSpot** — [thoughtspot/thoughtspot-agent-skills](https://github.com/thoughtspot/thoughtspot-agent-skills)
  - many Tableau to **Power BI** converters — see Q3
- Tableau's own repos are heading toward AI-authored HTML dashboards, but inside Tableau. The official plugin's `tableau-data-app-authoring` skill builds "a custom viz-extension web app that queries a published datasource live", with the agent writing all the code (`app.js`), then packages it as a `.twbx` and publishes it — [tableau-plugin SKILL.md](https://github.com/tableau/tableau-plugin/blob/main/src/plugin/skills/tableau-data-app-authoring/SKILL.md). The official MCP server has `scaffold-data-app` and `render-interactive-viz` tools — [tableau-mcp toolName.ts](https://github.com/tableau/tableau-mcp/blob/main/src/tools/web/toolName.ts)
- Web searches for open-source Tableau-to-Power BI or Tableau-to-Superset converters returned only commercial products and services: Pulse Convert ([AppSource](https://appsource.microsoft.com/en-gb/product/saas/officesolution1640276900203.pulse_convert)), MigraBI ([Product Hunt](https://www.producthunt.com/products/migrabi)), MAQ Software ([Microsoft Marketplace](https://marketplace.microsoft.com/en-us/product/maqsoftware.tableau_to_microsoft_powerbi_migration_assessment)), Kanerika FLIP ([kanerika.com](https://kanerika.com/services/migration/tableau-to-power-bi/)) and Andolasoft Tableau-to-Superset consulting ([andolasoft.com](https://andolasoft.com/what-we-do/tableau-migration-services)).
- Large "Tableau alternative" projects mention no Tableau-file import in their GitHub descriptions: DataEase, "An open-source BI tool alternative to Tableau", 24,710 stars — [dataease/dataease](https://github.com/dataease/dataease); Graphic Walker, "An open source alternative to Tableau. Embeddable visual analytic", 3,292 stars — [Kanaries/graphic-walker](https://github.com/Kanaries/graphic-walker).

**TabFree component vs. closest open-source code**

| TabFree component | Closest existing open code | Status |
|---|---|---|
| Inventory + bulk export from Tableau Server | [tableau/server-client-python](https://github.com/tableau/server-client-python), [tableau/tabcmd](https://github.com/tableau/tabcmd), [tableau/tableau-mcp](https://github.com/tableau/tableau-mcp), [unshackle](https://github.com/Querio-App/unshackle) | Covered by official libraries |
| .twb/.twbx/.tds/.tdsx → open semantic model + dashboard spec | [unshackle](https://github.com/Querio-App/unshackle) (Ossie + notebooks); PBIP converters (Q3) | Partial |
| Calculated fields, LOD and table calcs → SQL | Official ANTLR grammar in [Tableau-AI-Model-Evaluation-Tool](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool); [tableau_aid](https://github.com/TheRealHrishav/tableau_aid) (Presto); [scapeTableau](https://github.com/lukashalim/scapeTableau) | Partial: no full table-calc or INCLUDE/EXCLUDE semantics |
| .hyper → open format | Hyper API / [pantab](https://github.com/innobi/pantab) / [hyper-api-rust](https://github.com/tableau/hyper-api-rust), all through the proprietary `hyperd` binary | Covered only through Tableau's binary |
| .tde → open format | [tde-to-hyper sample](https://github.com/tableau/hyper-api-samples/tree/main/Community-Supported/tde-to-hyper) (Hyper API pinned to 0.0.17537) | Legacy path only |
| Extract data landed in Athena/Redshift | Nothing found; nearest pattern is Sigma's VDS → Snowflake/Databricks skill | Not covered |
| AI (Claude via MCP)-authored HTML dashboards + hosting | Official data-app skill (inside Tableau only); unshackle notebooks | Not covered as standalone open source |
| Permissions / RLS migration | unshackle (Cedar + SQL row filters), TSC permissions endpoints, [tableau-cloud-audit](https://github.com/simboli/tableau-cloud-audit) | Partial: translated, not enforced |
| Server features (sites/projects, schedules, subscriptions, alerts, embedding, usage) | Nothing found | Not covered |
| Parity verification | unshackle `verify`, the Hex skill's screenshot diff, the Guust toolkit's comparisons | Partial |

### Inferences
- Every piece TabFree needs exists in some form, but not as one product. The only integrated migration tool with an open target (unshackle) stops at "files in Git + notebooks + MCP", and the integrated tools with a runtime all target proprietary SaaS.
- By the table above, unshackle alone covers roughly 30–35% of TabFree's scope (inventory, translation, permission and RLS translation, verification). Adding the official Tableau libraries (TSC, tableau-mcp, Hyper API, VizQL Data Service, XSD, calc grammar) brings the existing parts, mostly migration-side, to roughly half. The runtime half (hosted HTML dashboards, permission enforcement, schedules, subscriptions, alerts, Athena/Redshift integration) has no open-source Tableau-specific code. These percentages are my estimates, not measured.
- Tableau shipping an official skill that lets an agent write HTML/JS data apps on published data sources validates TabFree's authoring model. It also means Tableau may compete directly on "AI-authored dashboards", but only inside Tableau.

### Gaps
- No repo was run. unshackle's fidelity on real enterprise Tableau Server estates is unverified (11 stars, about one month old).
- Gitee and other non-GitHub Chinese code hosts were not searched. The npm package `@guandata/tableau-migration` (Tableau → Guandata BI toolkit, 0.3.1, 2026-10-31) suggests Chinese-vendor migration tooling exists that GitHub search does not surface — [npm](https://www.npmjs.com/package/@guandata/tableau-migration).
- GitHub repository search matches names, descriptions and topics. Projects that mention Tableau only deep in their READMEs or code can be missed. For example, the query "tableau to sigma" returned 0 repos even though Sigma's official Tableau converter exists (it was found by code search).

## Q2. Top-20 ranked shortlist: what TabFree should reuse, fork or learn from first

### Takeaway
The most reusable assets are Tableau's own open-source primitives: the MCP server, TSC, the Hyper API samples and Rust client, the VizQL Data Service SDK, the TWB XSD and the calc-language ANTLR grammar. Beyond those, unshackle is the one community project worth forking. Everything else is a pattern to learn from, typically a converter with under 25 stars created in 2026.

### Cited Findings
| # | Repo | Stars | Last push | License | Role in TabFree | Action |
|---|---|---|---|---|---|---|
| 1 | [Querio-App/unshackle](https://github.com/Querio-App/unshackle) | 11 | 2026-11-19 | Apache-2.0 | Tableau Server/Cloud → Ossie metrics, notebooks, Cedar policies, RLS SQL, parity checks, MCP (338 commits; repo created 2026-10-22) | Fork/extend (High) |
| 2 | [tableau/tableau-mcp](https://github.com/tableau/tableau-mcp) | 378 | 2026-11-20 | Apache-2.0 | 55 web tools incl. `download-workbook`, `get-view-data`, `get-view-image`, `query-datasource`, `query-admin-insights`; v6.0.4 (2026-11-09) | Use as the extraction interface for Claude (High) |
| 3 | [tableau/server-client-python](https://github.com/tableau/server-client-python) | 718 | 2026-11-17 | MIT | REST API library with permissions and default-permissions endpoints; v0.42 (2026-10-15) | Use as-is (High) |
| 4 | [innobi/pantab](https://github.com/innobi/pantab) | 122 | 2026-04-08 | BSD-3-Clause | .hyper ↔ pandas/Arrow; v5.3.0 | Use for bulk .hyper → Parquet (High) |
| 5 | [tableau/hyper-api-samples](https://github.com/tableau/hyper-api-samples) | 154 | 2026-03-17 | MIT | tde-to-hyper, hyper-to-csv, parquet-to-hyper, native-s3, s3-to-hyper | Copy scripts (High) |
| 6 | [tableau/Tableau-AI-Model-Evaluation-Tool](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool) | 0 | 2026-11-06 | Apache-2.0 | Contains `TableauCalculationGrammar.g4` (+ Prep `TableauExpression.g4`) | Extract the grammar for a calc → SQL compiler (High) |
| 7 | [tableau/tableau-document-schemas](https://github.com/tableau/tableau-document-schemas) | 68 | 2026-11-03 | Apache-2.0 | Official TWB XSD (2026.1–2026.3) | Generate/validate the TWB parser (High) |
| 8 | [tableau/VizQL-Data-Service](https://github.com/tableau/VizQL-Data-Service) | 13 | 2026-10-07 | Apache-2.0 | OpenAPI + Python SDK to query published data sources | Pull extract data and ground-truth aggregates (High) |
| 9 | [tableau/hyper-api-rust](https://github.com/tableau/hyper-api-rust) | 3 | 2026-11-09 | Apache-2.0 | Rust/Node Hyper client with Arrow; `hyperdb-mcp` | Fast .hyper streaming; MCP over extracts (High) |
| 10 | [tableau/tableau-data-dictionary](https://github.com/tableau/tableau-data-dictionary) | 54 | 2026-10-02 | none | Workgroup PostgreSQL schema per release | Plan migration of schedules, subscriptions and usage (High) |
| 11 | [sigmacomputing/sigma-migration-skills](https://github.com/sigmacomputing/sigma-migration-skills) | 3 | 2026-11-18 | Apache-2.0 | `tableau-assessment`, `tableau-vds-to-cdw` | Adapt the VDS → warehouse landing for Athena/Redshift (Medium) |
| 12 | [Guust-Franssens/tableau-to-powerbi-migration](https://github.com/Guust-Franssens/tableau-to-powerbi-migration) | 21 | 2026-11-19 | MIT | `parse_tableau.py` → `migration-spec.json`; Copilot agents; .hyper row extraction | Borrow the spec extractor and agent loop (Medium) |
| 13 | [Humphrey4data/tableau-to-hex-skill](https://github.com/Humphrey4data/tableau-to-hex-skill) | 91 | 2026-07-30 | MIT | Extract → validate SQL → rebuild → screenshot-diff parity | Learn the methodology (Medium) |
| 14 | [thoughtspot/thoughtspot-agent-skills](https://github.com/thoughtspot/thoughtspot-agent-skills) | 7 | 2026-11-19 | other/custom | `ts-convert-from-tableau` with a detailed coverage matrix | Use the matrix as a parity checklist (Medium) |
| 15 | [TheRealHrishav/tableau_aid](https://github.com/TheRealHrishav/tableau_aid) | 1 | 2026-09-01 | MIT | Lark grammar → **Presto SQL** + dependency DAG | Borrow (Athena runs Presto/Trino SQL) (Medium) |
| 16 | [lukashalim/scapeTableau](https://github.com/lukashalim/scapeTableau) | 1 | 2026-11-12 | MIT | AST → ANSI SQL/DAX; LODs → window functions/subqueries | Borrow the LOD strategy (Medium) |
| 17 | [weisberg/pytableau](https://github.com/weisberg/pytableau) | 7 | 2026-10-03 | MIT | Python TWB object model (PyPI 3.0.0) | Evaluate as a parser base (Medium) |
| 18 | [agredyaev/tabkit](https://github.com/agredyaev/tabkit) | 0 | 2026-11-18 | MIT | Rust TWB parse/lineage/edit + MCP + signed HTML dashboard extension | Evaluate (Medium) |
| 19 | [tableau/tableau-migration-sdk](https://github.com/tableau/tableau-migration-sdk) | 27 | 2026-10-29 | Apache-2.0 | Content enumeration, mapping, hooks/transformers (incl. permissions) | Learn the migration-engine design (Medium) |
| 20 | [evidence-dev/evidence](https://github.com/evidence-dev/evidence) | 7,134 | 2026-11-19 | MIT | "Business intelligence as code ... in SQL and markdown" | Candidate output format and runtime for AI-authored HTML dashboards (Medium) |

Sources for the table cells are the repo links. Additional facts:
- unshackle has 338 commits on main (GitHub API) — [unshackle commits](https://github.com/Querio-App/unshackle/commits/main); release v0.3.0 (2026-11-19) — [unshackle releases](https://github.com/Querio-App/unshackle/releases)
- tableau-mcp v6.0.4 — [tableau-mcp releases](https://github.com/tableau/tableau-mcp/releases)
- TSC v0.42 — [TSC releases](https://github.com/tableau/server-client-python/releases)
- pantab v5.3.0 — [pantab releases](https://github.com/innobi/pantab/releases)

Honorable mentions:
- [cyphou/TableauToPowerBI](https://github.com/cyphou/TableauToPowerBI): rich visual and connector mapping tables, but GitHub detects no license file
- [datahub-project/datahub](https://github.com/datahub-project/datahub/tree/master/metadata-ingestion/src/datahub/ingestion/source/tableau): a proven Tableau metadata harvester
- [dbt-labs/dbt-charts](https://github.com/dbt-labs/dbt-charts): an agent-oriented dashboard YAML format
- [vega/vega-lite](https://github.com/vega/vega-lite): a compile target for worksheets
- [Kanaries/graphic-walker](https://github.com/Kanaries/graphic-walker): a self-service exploration UI
- [ionutsd/twbx_to_pbip](https://github.com/ionutsd/twbx_to_pbip): Show-Me-style mark inference
- [tableau-China/dsh-tableau-twb-viewer](https://github.com/tableau-China/dsh-tableau-twb-viewer): renders dashboard zone layout as SVG

### Inferences
- **Fork:** unshackle, the only Apache-2.0 end-to-end Tableau-to-open-files pipeline with permissions.
- **Depend on:** tableau-mcp, TSC, tableauhyperapi/pantab, hyper-api-rust and the VDS SDK.
- **Vendor in:** the XSD and the calc grammar.
- **Learn from:** the converter repos (Guust, ThoughtSpot matrix, Hex skill, tableau_aid, scapeTableau) rather than depending on them. They are single-maintainer, mostly under 25 stars, and target other platforms.
- Licenses: most shortlisted repos are MIT or Apache-2.0. ThoughtSpot's skills (NOASSERTION), cyphou (no license file) and cwtwb (AGPL-3.0) need legal review before any code reuse.

### Gaps
- I could not compare the shortlisted parsers (pytableau, tabkit, Guust, unshackle) on a common corpus of real workbooks. Ranking them needs a hands-on benchmark.

## Q3. Tableau workbook (.twb/.twbx/.tds) parsers and converters: what exists, and how complete is it?

### Takeaway
There are dozens of TWB parsers and converters, nearly all created in 2025–2026 and nearly all targeting **Power BI**. The typical pattern:
- data sources, fields and calculated formulas: parsed reliably
- worksheets: mapped to a fixed set of chart types
- dashboards: mapped to grid or zone geometry
- actions: reported but not converted
- table calcs, INCLUDE/EXCLUDE LODs, sets, blends and custom marks: refused or left for review

I found no converter of note to Superset, Metabase, Vega-Lite, ECharts, Plotly, Streamlit, Dash or Evidence. Looker and QuickSight have only assessment or MVP tools.

### Cited Findings
- **twbx_to_pbip** (narrow by design): "one `.twbx` with a live connection to a single CSV"; bar/column/line/pie/card/table; one dashboard → one page with zone geometry preserved; no calculated fields, filters, parameters, multiple data sources, joins, extracts, stories, actions, dual axis or maps. It infers chart types "the way Tableau's own Show Me does" because "Tableau has no chart types" — [ionutsd/twbx_to_pbip](https://github.com/ionutsd/twbx_to_pbip)
- **T2PBI Studio**: covers data sources, fields, calculated fields, parameters, worksheets, dashboards, actions (reported) and custom SQL (reported). It refuses sets/set actions, INCLUDE/EXCLUDE LODs, table calculations without stated addressing, `SCRIPT_*`, group/bin fields and cross-source blends, and does not reproduce dashboards pixel-for-pixel — [nomie7/t2pbi-studio](https://github.com/nomie7/t2pbi-studio)
- **tab2pbi**, on Tableau's Superstore sample: 17 calculations → 3 measures + 1 calculated column + 4 parameters, 9 skipped (23.5% coverage). 32 worksheets → 11 visuals emitted (34.4%); 21 skipped for custom shapes, insufficient fields, custom geometry, gantt and dual axis — [AyushSingh110/Tableau-to-Power-BI-semantic-compiler](https://github.com/AyushSingh110/Tableau-to-Power-BI-semantic-compiler)
- **cyphou/TableauToPowerBI** reports 164 visual mapping records, 97 connector dispatch keys and 23 object types, plus its own "Corpus gate FAILED: 30/44 artifacts migrated ... 12 FAIL / 10 WARN / 5 PASS quality verdicts" (2026-11-13). It also has a Prep-flow parser and a `.hyper` reader module — [cyphou/TableauToPowerBI](https://github.com/cyphou/TableauToPowerBI)
- **Guust toolkit**: `scripts/parse_tableau.py` captures "every data source, field, calculated-field formula, worksheet encoding, dashboard layout element, and reference line" into `migration-spec.json`. It has a 48-test suite, is exercised on 16 public Tableau Public workbooks, and translates FIXED LODs and table calcs with an AI agent — [capabilities doc](https://github.com/Guust-Franssens/tableau-to-powerbi-migration/blob/master/docs/capabilities-and-limitations.md). It requires Windows, Power BI Desktop, GitHub Copilot CLI, Python 3.11+, uv and Node 20+ — [README](https://github.com/Guust-Franssens/tableau-to-powerbi-migration)
- **rasgiza accelerator**: "there is no GA first-party one-click Tableau→Power BI converter". The repo automates "the mechanical 80%" and flags "the 20% that stays a human decision" — [rasgiza/tableau-migration-accelerator](https://github.com/rasgiza/tableau-migration-accelerator)
- **twb2pbi-migration-kit**: a YAML function-mapping table gives "88.5% auto-map on fixtures" — [MallikaChunduru/twb2pbi-migration-kit](https://github.com/MallikaChunduru/twb2pbi-migration-kit)
- **ThoughtSpot** `ts-convert-from-tableau`: its coverage matrix enumerates mapped constructs (physical tables, custom SQL, joins, blending "same connection only", data types, parameters in custom SQL, ...), with rows numbered past 130 — [coverage-matrix.md](https://github.com/thoughtspot/thoughtspot-agent-skills/blob/main/agents/cli/ts-convert-from-tableau/references/coverage-matrix.md). The older archived `ps_tools` converted .tds/.twb to TML — [thoughtspot/ps_tools](https://github.com/thoughtspot/ps_tools)
- **Looker**: SquareShift's accelerator only produces assessment JSON from Server or local files — [SquareShiftTech/tableau-to-looker-accelerator](https://github.com/SquareShiftTech/tableau-to-looker-accelerator). `carinlotan/tableau-to-looker` is empty (0 KB) — [repo](https://github.com/carinlotan/tableau-to-looker)
- **Superset**: the only repo found is a demo that keeps a Tableau dashboard and a Superset dashboard in sync with 152 automated KPI comparisons; it has no converter — [Vishalsugur/ParityBI](https://github.com/Vishalsugur/ParityBI-Validated-Tableau-to-Superset-Migration)
- **QuickSight**: an MVP whose GenAI generation and deployment "are mocked / dry-run locally", with a rule-based translator by default — [leducse/tableau-quicksight-migration-assistant](https://github.com/leducse/tableau-quicksight-migration-assistant)
- **dbt/SQL**:
  - TWB → dbt stg/fct/dim + MetricFlow, with LODs translated "to CTE templates" (no license) — [jklardata/tableau-twb-to-dbt](https://github.com/jklardata/tableau-twb-to-dbt)
  - Tableau Prep flows → dbt SQL — [VDFaller/dbt-prep-flow-converter](https://github.com/VDFaller/dbt-prep-flow-converter)
- **Hex, Omni, Strategy, Guandata**: skill- or package-level converters only — [hex-inc/hex-skills](https://github.com/hex-inc/hex-skills), [SSchneider22/tableau-twb-to-omni-semantic-layer](https://github.com/SSchneider22/tableau-twb-to-omni-semantic-layer), [tzockoll-creator/Tableau-Strategy-Migration-Skill](https://github.com/tzockoll-creator/Tableau-Strategy-Migration-Skill), [npm @guandata/tableau-migration](https://www.npmjs.com/package/@guandata/tableau-migration)
- **Browser rendering of TWB without Tableau**: the closest is a plugin that shows a `.twb`'s "1:1 SVG dashboard layout", field references and metadata. It is read-only metadata; it does not parse `.twbx` or render data — [tableau-China/dsh-tableau-twb-viewer](https://github.com/tableau-China/dsh-tableau-twb-viewer)
- **Official XSD**: `tableau/tableau-document-schemas` provides XSDs per Tableau version, "formally published in February 2026" — [README](https://github.com/tableau/tableau-document-schemas)
- **Generic parsers** (no converter):
  - [weisberg/pytableau](https://github.com/weisberg/pytableau): Python, PyPI 3.0.0 (2026-10-02)
  - [agredyaev/tabkit](https://github.com/agredyaev/tabkit): Rust; inspects datasources, fields, calculations, parameters, worksheets and dashboards; lineage SQL → field → calc → worksheet → dashboard
  - [tableau/document-api-python](https://github.com/tableau/document-api-python): last release v0.11, 2022-11-07 — [releases](https://github.com/tableau/document-api-python/releases)
  - [PrigasG/twbparser](https://github.com/PrigasG/twbparser): R
  - [HandcartCactus/tableau-xray](https://github.com/HandcartCactus/tableau-xray): archived

### Inferences
- A converter to a web stack (Vega-Lite/ECharts/HTML) would be new work. The reusable bits are Show-Me chart inference (ionutsd's `marks.py`), worksheet-encoding extraction (Guust's `parse_tableau.py`), zone-layout rendering (the dsh viewer), the construct checklist (ThoughtSpot's coverage matrix) and the XSD.
- Coverage numbers like 23.5% calcs and 34.4% visuals on Superstore suggest full automation is unrealistic. An agent-in-the-loop design with a deterministic IR plus LLM repair and parity gates (Guust, Hex skill, nomie7, unshackle) is the prevailing and more credible pattern.

### Gaps
- None of the converters publishes fidelity results on large private estates, apart from cyphou's self-reported corpus gate. All numbers are self-reported by repo authors.
- No converter targets Superset, Metabase, Vega-Lite, ECharts, Plotly, Streamlit or Evidence. Code searches for "twb" combined with those names returned only unrelated hits. Closed-source or unindexed projects could exist.

## Q4. Tableau calculated-field / LOD / table-calc translators and grammars

### Takeaway
An **official Apache-2.0 ANTLR4 grammar** for the Tableau calculation language now exists, inside `tableau/Tableau-AI-Model-Evaluation-Tool`. Small community transpilers target Presto, ANSI SQL, Snowflake/BigQuery SQL and DAX. None correctly handles the full semantics of INCLUDE/EXCLUDE LODs and table calculations: these depend on the visualization's grain and addressing, which lives in the worksheet XML, not the formula.

### Cited Findings
- The Calculated Fields validator "uses the checked-in ANTLR grammar at `packages/evals/src/validator/grammar/TableauCalculationGrammar.g4`", and generated TypeScript parsers are checked in. Prep uses a separate editor-style grammar (`TableauExpression.g4`) that "accepts incomplete constructs" — [CALC_GRAMMAR.md](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool/blob/main/docs/CALC_GRAMMAR.md)
- The grammar header reads "Calculation grammar based on work by Dan Cory". It defines `LODSCOPE: FIXED | INCLUDE | EXCLUDE` and is 212 lines; the repo license is Apache-2.0 — [TableauCalculationGrammar.g4](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool/blob/main/packages/evals/src/validator/grammar/TableauCalculationGrammar.g4)
- **tableau_aid**: extracts calcs, builds a dependency DAG and transpiles with a `lark` grammar to **Presto SQL** as one `WITH ... SELECT`. "LOD expressions and parameters are recognized but not translated — they render as placeholders" — [TheRealHrishav/tableau_aid](https://github.com/TheRealHrishav/tableau_aid)
- **BIEscape/scapeTableau**: transpiles `IIF`, `ZN`, `IFNULL`, `STARTSWITH`, `CONTAINS` and `CASE` to ANSI SQL and DAX. "`FIXED` / `INCLUDE` / `EXCLUDE` become window functions or grain subqueries (INCLUDE/EXCLUDE use worksheet row/column grain)"; DAX LODs are left as TODO — [lukashalim/scapeTableau](https://github.com/lukashalim/scapeTableau)
- **jklardata**: translates LODs to CTE templates and offers "window function rewrite hints for untranslatable table calcs (`RUNNING_SUM`, `RANK`, `INDEX`, etc.)" (no license) — [jklardata/tableau-twb-to-dbt](https://github.com/jklardata/tableau-twb-to-dbt)
- **unshackle**: translates measures, calculated fields, ratios and FIXED LODs. Table calculations, INCLUDE/EXCLUDE LODs and multi-dimension FIXED LODs are left as TODO — [unshackle Tableau doc](https://github.com/Querio-App/unshackle/blob/main/docs/tools/tableau.md)
- **AST/lexer-based Tableau → DAX compilers**:
  - [sourabhxmishra/TableauCalcToPowerBIDAX](https://github.com/sourabhxmishra/TableauCalcToPowerBIDAX): `twb_parser.py`, `calc_to_dax.py`, `calc_to_m.py`
  - [Satyam1616/biforge](https://github.com/Satyam1616/biforge): `calc_lexer.py`, `calc_parser.py`, `calc_ast.py`, `dax_transpile.py`
- **Tableau-LSP**: a VS Code language server for Tableau calcs (`.twbl`) with completion, hover, diagnostics and formatting — [TrueCrimeDev/Tableau-LSP](https://github.com/TrueCrimeDev/Tableau-LSP)
- **ThoughtSpot** `ts-object-formula-translate`: translates one formula from Tableau (and other dialects) into ThoughtSpot — [thoughtspot-agent-skills](https://github.com/thoughtspot/thoughtspot-agent-skills)
- **Official calc skills go the other way**: Tableau's `calc-translator` "turns one SQL, Excel, or Power BI DAX expression into a working Tableau calc" — [calc-translator SKILL.md](https://github.com/tableau/tableau-ai-utilities/blob/main/tableau-studio-skills/tableau-agent/calc-family/calc-translator/SKILL.md)
- A web search found no other open-source Tableau formula grammar. The closest public material was a vendor guide with hand-written SQL equivalents for FIXED and INCLUDE — [Honeydew Tableau guide](https://honeydew.ai/docs/migration/tableau-guide)

### Inferences
- The best path for TabFree:
  - generate a parser from the official `.g4` (Python or TypeScript targets)
  - build an AST → Trino/Athena SQL emitter (Presto dialect, as in tableau_aid)
  - resolve FIXED LODs as grain subqueries, as BIEscape and unshackle do
  - compile INCLUDE/EXCLUDE LODs and table calcs per worksheet, using the shelf, partition and addressing info from the TWB
  - leave remaining hard cases to Claude with parity checks
- Because the official grammar lives in an evaluation tool, not a published package, TabFree should vendor it with attribution (Apache-2.0 allows this) and track upstream changes.

### Gaps
- I did not verify how completely the official grammar covers every Tableau function (spatial, regex, `SCRIPT_*`, user functions), or whether it accepts all formula text found in real workbooks.
- I found no open-source test suite of Tableau formulas paired with expected SQL results. The repo's `public-suites/calcs` cases target AI evaluation, and I did not inspect whether they could serve this purpose.

## Q5. .hyper and .tde readers, writers and converters

### Takeaway
Every working `.hyper` reader found goes through Tableau's closed-source Hyper engine (`hyperd`), via `tableauhyperapi`, pantab or the new official pure-Rust client, which still needs the `hyperd` executable. I found no reverse-engineered `.hyper` format and no independent reader. The one "DuckDB extension for Hyper" is an empty template. Hyper itself can read Parquet, Iceberg, CSV and Arrow and read from S3, and since 2023 it has `COPY TO` and Arrow export. `.tde` can be read only through an older Hyper API's `external(... format => 'tde')` (official sample pinned to 0.0.17537) or upgraded in Tableau Desktop 2024.2 or earlier.

### Cited Findings
- Hyper release notes:
  - 0.0.17360 (2023-07-05): "Support for the `COPY TO` statement was added" and "experimental support for reading and writing Arrow"
  - 0.0.17537 (2023-08-02): Arrow read/write "stable and ready for use in production"
  - 0.0.15735 (2022-10-05): "New support for Apache Iceberg as an external format" and "reading external files from S3 is now enabled by default"

  Source: [hyper-db releases.md](https://github.com/tableau/hyper-db/blob/main/website/docs/releases.md)
- The Hyper docs say Hyper can "read widespread data formats such as CSVs, Parquet and Iceberg files" — [hyper-db docs index](https://github.com/tableau/hyper-db/blob/main/website/docs/index.md). The pandas guide queries `external('orders_10rows.parquet')` through pantab — [pandas_integration.md](https://github.com/tableau/hyper-db/blob/main/website/docs/guides/pandas_integration.md)
- `tableauhyperapi` on PyPI: version 0.0.26963 (2026-10-17), license field "Apache-2.0" — [PyPI](https://pypi.org/project/tableauhyperapi/)
- **hyper-api-rust** is "a pure-Rust implementation of the Hyper database API, using the PostgreSQL wire protocol ... without any C library dependencies". It still needs "the `hyperd` executable, obtained with `make download-hyperd`". Features include Arrow IPC, gRPC read-only transport, the `hyperdb-mcp` crate ("MCP server for LLM-driven SQL analytics on `.hyper` files") and Node.js bindings — [tableau/hyper-api-rust](https://github.com/tableau/hyper-api-rust); latest release v1.0.0-rc.5 (2026-10-06) — [releases](https://github.com/tableau/hyper-api-rust/releases)
- pantab "Read/Write pandas DataFrames with Tableau Hyper Extracts", v5.3.0 (2026-04-08) — [innobi/pantab](https://github.com/innobi/pantab). Tableau's `hyper-to-csv` sample uses pantab — [hyper-to-csv README](https://github.com/tableau/hyper-api-samples/tree/main/Community-Supported/hyper-to-csv)
- The official `tde-to-hyper` sample reads `.tde` with `external(<path>, format => 'tde', "table" => ...)` and requires "Hyper API version `0.0.17537`". The folder was last changed 2023-08-29 — [tde-to-hyper](https://github.com/tableau/hyper-api-samples/tree/main/Community-Supported/tde-to-hyper)
- Tableau Help: "2024.2 is the last version of Tableau Desktop that supports any .tde-based workbooks, data sources, or bookmarks". .tde uploads to Tableau Server have been disabled since 2023.1.0 — [Extract Upgrade to .hyper Format](https://help.tableau.com/current/pro/desktop/en-us/extracting_upgrade.htm)
- `Coco-Alemana/duckdb_hyper` is described as "A DuckDB extension for Tableau's Hyper file type", but its README and `src/quack_extension.cpp` are the unmodified DuckDB extension template — [Coco-Alemana/duckdb_hyper](https://github.com/Coco-Alemana/duckdb_hyper)
- cyphou's `hyper_reader.py` tries `tableauhyperapi` first, then "stdlib `sqlite3` — works for older SQLite-compatible `.hyper` files", then "binary header scanning — last resort for metadata-only extraction" — [cyphou/TableauToPowerBI](https://github.com/cyphou/TableauToPowerBI). I could not verify the SQLite claim.
- A web search found no public reverse-engineered specification of `.hyper`. The documented route is the Hyper API — [Tableau Hyper API help](https://help.tableau.com/current/api/hyper_api/en-us). The Hyper docs describe several "database file format versions": version 0 is the default "for maximum compatibility", and later versions add improvements such as better compression, selected with `default_database_version` — [optimize.md](https://github.com/tableau/hyper-db/blob/main/website/docs/guides/hyper_file/optimize.md). Any independent reader would have to track several on-disk versions.
- **Other .hyper tools**, all built on the Hyper API:
  - [unshackle](https://github.com/Querio-App/unshackle)
  - [Guust toolkit](https://github.com/Guust-Franssens/tableau-to-powerbi-migration) (`extract_hyper_data.py`)
  - [USEReady/TabToPbi-Migrate](https://github.com/USEReady/TabToPbi-Migrate) (every `.hyper` → typed CSV)
  - [yguo91/Tableau_Hyper_File_Converter](https://github.com/yguo91/Tableau_Hyper_File_Converter) (browser UI → CSV/JSON)
  - [lukashalim/scapeTableau](https://github.com/lukashalim/scapeTableau) (`.hyper` → Excel)
- **Writers** into Hyper (wrong direction for TabFree): [hyperleaup](https://github.com/goodwillpunning/hyperleaup) (Spark), [dataiku plugin](https://github.com/dataiku/dss-plugin-tableau-hyper) and [tableau-hyper-management](https://github.com/danielgp/tableau-hyper-management). Legacy TDE writers are all stale: [jlmorton/tableau](https://github.com/jlmorton/tableau), [node-tableau-sdk](https://github.com/tableau-mkt-archived/node-tableau-sdk) (archived), [RTableau](https://github.com/zhiruiwang/RTableau)
- **AWS reverse direction**: a Step Functions/Batch workflow that reads TDSX metadata, plans Redshift `UNLOAD`s and rebuilds Hyper extracts — [mcoirad/tableau-hyper-refresher](https://github.com/mcoirad/tableau-hyper-refresher)
- **Name collisions to ignore**: `lyhyl/TDEDecrypt` and `uncryptor/TDecryptor` handle `.tde` files of the game Teardown, not Tableau — [lyhyl/TDEDecrypt](https://github.com/lyhyl/TDEDecrypt)

### Inferences
- TabFree's converter will have to run Tableau's `hyperd` (via `tableauhyperapi`, pantab or hyper-api-rust) at least once per extract. The practical pipeline:
  - `.tde` → `.hyper` with the pinned 0.0.17537 sample
  - `.hyper` → Arrow/Parquet (pantab or Rust Arrow export, or `COPY TO`)
  - S3 → Glue/Athena tables, or Redshift `COPY`
- Alternatively, skip files entirely: query published extracts through VizQL Data Service (as Sigma's `tableau-vds-to-cdw` does) and land the results in Athena or Redshift.
- Extracts often duplicate warehouse tables. Repointing to the source (the unshackle and Hex-skill approach) can avoid moving extract data at all.

### Gaps
- I could not confirm that `COPY TO` writes **Parquet**, as opposed to only CSV/Arrow, or that it can write directly to S3. A web search summary pointed to an S3 Parquet example in the hyper-db docs, but that page now returns 404 and the Salesforce SQL reference returned 403.
- Whether current Hyper API versions still support `format => 'tde'` is unknown; the sample pins an August 2023 build.
- The license terms for redistributing or running `hyperd` server-side in a commercial or free product were not reviewed. PyPI lists Apache-2.0 for the Python package, but `hyperd` source is not public.

## Q6. Tableau Server/Cloud export, backup, inventory, migration, permissions, lineage and repository tools

### Takeaway
The official primitives are complete and active: TSC, REST samples, tabcmd, the Migration SDK (Tableau-to-Tableau only), the VizQL Data Service, the Metadata API and the workgroup data dictionary. Community tools add estate collectors, permission exporters and permissions-as-code, TSM backups to S3, and lineage harvesters, including DataHub's and OpenMetadata's Tableau connectors. No open tool migrates Server objects such as schedules, subscriptions, data-driven alerts and custom views to a non-Tableau platform.

### Cited Findings
- TSC has `permissions_endpoint.py` and `default_permissions_endpoint.py`, plus samples such as `query_permissions.py` and `add_default_permission.py` — [tableau/server-client-python](https://github.com/tableau/server-client-python/tree/development/tableauserverclient/server/endpoint). Latest release v0.42 on 2026-10-15; 1,178 commits — [releases](https://github.com/tableau/server-client-python/releases)
- tabcmd is "a Python based app that replicates the functionality of the existing TabCMD"; v2.0.21 on 2026-07-10 — [tableau/tabcmd](https://github.com/tableau/tabcmd)
- The Migration SDK is "designed for moving from Tableau Server to Tableau Cloud" (pip `tableau_migration`, NuGet `Tableau.Migration`) — [tableau-migration-sdk](https://github.com/tableau/tableau-migration-sdk). It covers Users, Groups, Projects, Data Sources, Workbooks, Extract Refresh Tasks and Custom Views — [tableau-migration-app README](https://github.com/tableau/tableau-migration-app). It ships permission hooks and transformers (`UpdatePermissionsHook`, `ModifyPermissionsTransformer`, `migration_content_permissions.py`) — [repo tree](https://github.com/tableau/tableau-migration-sdk)
- The older TabMigrate ("moving Tableau content between multiple Tableau Server environments") is archived (last push 2023-03-29) — [tableau/TabMigrate](https://github.com/tableau/TabMigrate)
- The repository data dictionary covers "the tables and views in the 'workgroup' PostgreSQL database of the Tableau Server repository", per release — [tableau-data-dictionary](https://github.com/tableau/tableau-data-dictionary). Community Tableau Server Insights publishes data sources over that repository ("a maze of confusing tables"); its last release was Oct 2021 — [community-tableau-server-insights](https://github.com/tableau/community-tableau-server-insights)
- **tableau-cloud-audit** collects "users, groups, memberships, projects, workbooks, views, data sources, connections, permission rules (incl. project default templates) and the Admin Insights activity sources" into one DuckDB file, pseudonymised. It is a "working alpha" and Tableau Cloud only — [simboli/tableau-cloud-audit](https://github.com/simboli/tableau-cloud-audit)
- **Permission tools**:
  - config-driven CLI for projects, groups and permissions — [jaybythebay/tableau-permissions](https://github.com/jaybythebay/tableau-permissions)
  - GUI bulk exporter — [tabata140/tableau-permission-exporter](https://github.com/tabata140/tableau-permission-exporter)
  - PHP CSV exporter (2018) — [jezwev/Tableau-Permissions](https://github.com/jezwev/Tableau-Permissions)
  - repository-SQL approach (2021) — [Ch-Ruslan/tableau_permissions_via_postgree](https://github.com/Ch-Ruslan/tableau_permissions_via_postgree)
- **Backups**:
  - TSM backups → S3 with Zabbix — [v3111707/tableau-full-backup-to-s3](https://github.com/v3111707/tableau-full-backup-to-s3)
  - workbooks and data sources → git (GitLab) — [brilliant-data/tableau-server-backup](https://gitlab.com/brilliant-data/tableau-server-backup)
  - PowerShell backup/restore (2019) — [GalenBusch](https://github.com/GalenBusch/Tableau-Server-Backup-Restore)
- **Lineage**:
  - DataHub's Tableau source (`tableau.py`, `tableau_virtual_connections.py`, `tableau_initial_sql.py` ...) — [datahub](https://github.com/datahub-project/datahub/tree/master/metadata-ingestion/src/datahub/ingestion/source/tableau) (13,006 stars)
  - OpenMetadata Tableau connector — [OpenMetadata](https://github.com/open-metadata/OpenMetadata/tree/main/ingestion/src/metadata/ingestion/source/dashboard/tableau) (15,820 stars)
  - Amundsen is archived — [amundsen](https://github.com/amundsen-io/amundsen)
  - read-only lineage MCP over the Metadata API — [tdries/tableau-graphQL-mcp](https://github.com/tdries/tableau-graphQL-mcp)
  - official Metadata Explorer app — [tableau/tableau-metadata-explorer](https://github.com/tableau/tableau-metadata-explorer)
- **Stale but notable libraries**:
  - [bryanthowell-tableau/tableau_tools](https://github.com/bryanthowell-tableau/tableau_tools) (223 stars, last push 2022-02-15): REST, XML modification, tabcmd and repository tools
  - [divinorum-webb/tableau-api-lib](https://github.com/divinorum-webb/tableau-api-lib) (105 stars, 2023-10-24)
  - Palette Insight monitoring stack (GPL-3.0, last push 2020) — [palette-software/palette-insight](https://github.com/palette-software/palette-insight)
- **Data capture without APIs**: TableauScraper "scrape[s] data from Tableau viz" (137 stars, last push 2024-05-08) — [bertrandmartel/tableau-scraping](https://github.com/bertrandmartel/tableau-scraping)
- **GitLab**: the GitLab data team's TableauConMan (content management; PyPI 0.1.18, 2024-04-24) — [gitlab-data/TableauConMan](https://gitlab.com/gitlab-data/TableauConMan); [PyPI](https://pypi.org/project/TableauConMan/)

### Inferences
- **Inventory/export:** TSC + VDS + Metadata API. Copy tableau-cloud-audit's schema ideas for the permissions model (effective permissions, project default templates).
- **Server-side objects:** schedules, subscriptions, data-driven alerts, favorites, custom views, comments and usage history must be read from the REST API or the workgroup DB (use the data dictionary) and rebuilt in TabFree. No open code does this today.
- **Permissions to RBAC:** unshackle's Cedar mapping and jaybythebay's YAML model are the two reusable designs.

### Gaps
- I did not check the REST API or TSC for coverage of data-driven alerts and subscription details on current Server versions. Some objects may only be reachable through the repository database.

## Q7. Tableau MCP servers and Claude/LLM-driven Tableau tools

### Takeaway
The official `tableau/tableau-mcp` is mature: 378 stars, v6.0.4, Server and Cloud support, hosted at mcp.tableau.com. It exposes nearly everything a migration agent needs. The official `hyperdb-mcp` exposes `.hyper` files to LLMs. There are 50+ community Tableau MCP servers and Claude skills, most of them small. LLM tools that convert Tableau dashboards into code target notebooks (unshackle) or proprietary BI (Hex, Sigma, ThoughtSpot, Power BI). Many "LLM writes .twb" tools exist, but they author into Tableau, the opposite direction.

### Cited Findings
- tableau-mcp web tools (55) include:
  - `list-workbooks`, `get-workbook`, `download-workbook`, `move-workbook`
  - `list-views`, `get-view-data`, `get-view-image`, `get-custom-view-data/image`
  - `list-datasources`, `get-datasource-metadata`, `query-datasource`
  - `publish-workbook`, `publish-datasource`
  - `list-extract-refresh-tasks`, `list-jobs`, `list-users`, `update-user`, `delete-content`
  - `query-admin-insights`, Pulse tools, `render-interactive-viz`, `scaffold-data-app`

  Source: [toolName.ts](https://github.com/tableau/tableau-mcp/blob/main/src/tools/web/toolName.ts)
- A separate Desktop authoring server (78 tools) includes `get-xml`, `get-worksheet-underlying-data`, `export-dashboard-image`, `validate-workbook-xml` and `author-calc` — [desktop toolName.ts](https://github.com/tableau/tableau-mcp/blob/main/src/tools/desktop/toolName.ts)
- The hosted Tableau MCP at `https://mcp.tableau.com` uses OAuth 2.1 with per-user permissions. The npm package `@tableau/mcp-server` "contains the Web server for Tableau Cloud and Tableau Server" and is configured with `SERVER`, `PAT_NAME` and `PAT_VALUE` — [tableau-mcp README](https://github.com/tableau/tableau-mcp); npm 6.0.4 published 2026-11-09 — [npm](https://www.npmjs.com/package/@tableau/mcp-server)
- `hyperdb-mcp` (in hyper-api-rust) is installable with `npm install -g hyperdb-mcp` and bundles a matching `hyperd` — [hyper-api-rust README](https://github.com/tableau/hyper-api-rust)
- The official multi-platform plugin (Claude, ChatGPT/Codex, Amazon Quick) ships skills `tableau-dashboard-advisor`, `tableau-data-app-authoring` and `tableau-data-quality-sentinel` — [tableau/tableau-plugin](https://github.com/tableau/tableau-plugin). The official skill catalog for Tableau Studio/MCP includes `calc-*` skills, `workbook-auditor` and `tableau-governance-scanner` — [tableau/tableau-ai-utilities](https://github.com/tableau/tableau-ai-utilities)
- **Community MCP servers**:
  - [wjsutton/tableau-public-mcp](https://github.com/wjsutton/tableau-public-mcp) (30 stars, Tableau Public)
  - [TheInformationLab/tableau_mcp_starter_kit](https://github.com/TheInformationLab/tableau_mcp_starter_kit) (31 stars, LangChain)
  - [SebAustin/tableau-mcp-publish](https://github.com/SebAustin/tableau-mcp-publish) ("the write side the official Tableau MCP doesn't cover")
  - [salesoptecs/tableau-mcp](https://github.com/salesoptecs/tableau-mcp) (one-file self-hosted: search, render, export)
  - [agredyaev/tabkit](https://github.com/agredyaev/tabkit) (TWB runtime + MCP)
  - [aidatacooper/cwtwb](https://github.com/aidatacooper/cwtwb) (51 stars, AGPL-3.0, twb generation + MCP)
  - [banthia14aman/tableau-builder-mcp](https://github.com/banthia14aman/tableau-builder-mcp) (JSON spec → .twbx)
  - [zavora-ai/mcp-bi](https://github.com/zavora-ai/mcp-bi) (multi-BI, Superset first)
- **LLM authoring into Tableau** (wrong direction for TabFree, but useful prompt and pattern sources):
  - [blakefeiza/vizwright](https://github.com/blakefeiza/vizwright): .twb XML authoring with a Tableau Cloud render loop
  - [laviDrori0702/tableau-dashboard-creator-skill](https://github.com/laviDrori0702/tableau-dashboard-creator-skill) (35 stars): spec + "interactive HTML mock"
  - [chami1221-eng/tableau-twb-xml-toolkit](https://github.com/chami1221-eng/tableau-twb-xml-toolkit)
  - [Kokoabassplayer/tableau-agent-toolkit](https://github.com/Kokoabassplayer/tableau-agent-toolkit)
  - [HanifIsya/tableau-workbook-skill](https://github.com/HanifIsya/tableau-workbook-skill)
  - [AnkurNapa/tableau-dashboard-skill](https://github.com/AnkurNapa/tableau-dashboard-skill)
- An unofficial tool drives Tableau Cloud's "internal VizQL command API" via Playwright — [CooperFryar/tableau-vizql-bridge](https://github.com/CooperFryar/tableau-vizql-bridge)
- **LLM-driven migration out of Tableau**:
  - Hex skill (Tableau "battle-tested ... proven on a production migration") — [Humphrey4data/tableau-to-hex-skill](https://github.com/Humphrey4data/tableau-to-hex-skill)
  - Copilot CLI agents → Power BI — [Guust-Franssens](https://github.com/Guust-Franssens/tableau-to-powerbi-migration)
  - optional DeepSeek/OpenAI/Anthropic pass for refused fields — [nomie7/t2pbi-studio](https://github.com/nomie7/t2pbi-studio)
  - Claude refinement of calcs → dbt SQL — [jklardata/tableau-twb-to-dbt](https://github.com/jklardata/tableau-twb-to-dbt)
  - unshackle "is built with Claude Code", with agent guides in `migration/skills/` — [unshackle](https://github.com/Querio-App/unshackle)

### Inferences
- For TabFree's migration agent, the official tableau-mcp already provides the extraction calls: download workbook, view data, view images, VDS queries and admin insights. The `get-view-image` and `get-view-data` tools give "ground truth" for screenshot and number parity checks after conversion.
- For authoring, TabFree needs its own MCP server, targeting its HTML dashboard format and Athena/Redshift. The official data-app skill and dbt-charts' agent-oriented YAML are the closest design references.

### Gaps
- I did not test tableau-mcp against Tableau Server (as opposed to Cloud) or check its minimum Server version for VDS-backed tools. VDS-backed tools depend on VizQL Data Service availability on the Server version (`getVizqlDataServiceDisabledError.ts` exists in the repo — [repo](https://github.com/tableau/tableau-mcp/tree/main/src/tools/web)).

## Q8. "Tableau clone / open-source Tableau / Tableau alternative" repos and in-browser rendering of workbooks

### Takeaway
Repos that call themselves Tableau alternatives fall into three groups: large general BI platforms (DataEase, Superset, Metabase), embeddable drag-and-drop explorers (Graphic Walker/PyGWalker) and toy clones. None of them imports `.twb` files or renders Tableau workbooks. I found no open-source Tableau Server alternative; searches for "tableau server alternative", "open source tableau server" and "tableau public alternative" returned no repos.

### Cited Findings
- **Large platforms and explorers**:
  - [dataease/dataease](https://github.com/dataease/dataease): 24,710 stars; "An open-source BI tool alternative to Tableau"; GitHub license "Other"
  - [Kanaries/graphic-walker](https://github.com/Kanaries/graphic-walker): 3,292 stars, Apache-2.0; "An open source alternative to Tableau. Embeddable visual analytic"; topics include vega-lite
  - [Kanaries/pygwalker](https://github.com/Kanaries/pygwalker): 16,004 stars
  - [Kanaries/Rath](https://github.com/Kanaries/Rath): 4,686 stars, AGPL-3.0
- **Toy-scale "Tableau clones"**:
  - [zainhoda/orbgo](https://github.com/zainhoda/orbgo) (12 stars, 2018)
  - [aymanmali/quackboard](https://github.com/aymanmali/quackboard) (DuckDB-Wasm + ECharts + Next.js, created 2026-11-19)
  - [joheld/atelier-analytics](https://github.com/joheld/atelier-analytics) (DuckDB-WASM + Arrow + Observable Plot)
  - [rohankumardubey/mini_tableau](https://github.com/rohankumardubey/mini_tableau), [Akhil-Kashyap/tableau-clone](https://github.com/Akhil-Kashyap/tableau-clone), [yashacon/tableau_clone](https://github.com/yashacon/tableau_clone), [alexmahesh/Tableau_Alternative](https://github.com/alexmahesh/Tableau_Alternative)
- **Dashboards-as-code formats an AI could author**:
  - Evidence ("Business intelligence as code ... in SQL and markdown", 7,134 stars, MIT) — [evidence-dev/evidence](https://github.com/evidence-dev/evidence)
  - dbt Charts ("a clear YAML syntax for dashboards that's easy for agents to write and humans to audit", 602 stars, Apache-2.0, created 2026-08-14) — [dbt-labs/dbt-charts](https://github.com/dbt-labs/dbt-charts)
  - Vega-Lite (5,563 stars) — [vega/vega-lite](https://github.com/vega/vega-lite)
  - Voyager (Show-Me-style recommender, last push 2023-03-02) — [vega/voyager](https://github.com/vega/voyager)
- **Runtimes with RBAC and Athena/Redshift connectivity**, but no Tableau import:
  - [apache/superset](https://github.com/apache/superset) (75,586 stars)
  - [metabase/metabase](https://github.com/metabase/metabase) (50,442 stars)
  - [lightdash/lightdash](https://github.com/lightdash/lightdash) (6,241 stars)
- **In-browser TWB tools without Tableau**:
  - zone-layout SVG viewer — [tableau-China/dsh-tableau-twb-viewer](https://github.com/tableau-China/dsh-tableau-twb-viewer)
  - auditing and lineage — [andey0Saikiran/tableau-lineage](https://github.com/andey0Saikiran/tableau-lineage)
  - WASM TWBX → PBIP converter (site at ionutsd.github.io) — [ionutsd/twbx_to_pbip](https://github.com/ionutsd/twbx_to_pbip)

  None renders worksheet marks with data.
- **Example of a team replacing a Tableau report with a generated HTML file**: "A single self-contained HTML file with the weekly data embedded", built from an Excel export as an "interim Tableau replacement" — [cloudability/msp-health-dashboard](https://github.com/cloudability/msp-health-dashboard)

### Inferences
- TabFree's runtime has two options: build on an existing open BI server (Superset, which has RBAC and Athena/Redshift drivers) and add migration plus AI authoring, or host its own HTML dashboards in a lighter dashboards-as-code format (Evidence, dbt-charts, or Vega-Lite inside custom HTML).
- No project renders Tableau worksheets in a browser, so TwB → web chart compilation is greenfield.

### Gaps
- Superset's, Metabase's and Lightdash's exact Athena/Redshift support and permission models were not verified in this sweep. That belongs to the runtime research thread.

## Q9. Useful official Tableau org (github.com/tableau) repositories

### Takeaway
Of 93 public repos in the tableau org, about 15 matter for TabFree:
- extraction: tableau-mcp, server-client-python, rest-api-samples, tabcmd, VizQL-Data-Service, tableau-metadata-explorer
- format specs: tableau-document-schemas, the calc grammar in Tableau-AI-Model-Evaluation-Tool, document-api-python
- data conversion: hyper-api-samples, hyper-db, hyper-api-rust
- Server-object migration: tableau-data-dictionary, community-tableau-server-insights
- design references: tableau-migration-sdk/app, tableau-plugin

Extensions, embedding, WDC and connector SDKs matter only for a coexistence period.

### Cited Findings
- `gh api orgs/tableau/repos` listed 93 public repos (2026-11-20); the most-starred are TabPy (1,653), server-client-python (718), webdataconnector (688), rest-api-samples (436), tableau-mcp (378) and document-api-python (362) — [github.com/tableau](https://github.com/tableau)
- **Archived** official tools:
  - [TabMigrate](https://github.com/tableau/TabMigrate) (2023)
  - [TabMon](https://github.com/tableau/TabMon) (2024)
  - [metadata-api-samples](https://github.com/tableau/metadata-api-samples) (2021)
  - [tab-compare](https://github.com/tableau/tab-compare) (2022), "visual comparison tool to understand differences in dashboards across different versions"
  - [vail](https://github.com/tableau/vail) (2021), Visual Analytic Intent Language
  - [js-api-samples](https://github.com/tableau/js-api-samples)
  - [VizAlerts](https://github.com/tableau/VizAlerts) ("Data-driven alerting for Tableau Server", archived)
- **Newer official repos (2025–2026)**:
  - [tableau-document-schemas](https://github.com/tableau/tableau-document-schemas)
  - [hyper-api-rust](https://github.com/tableau/hyper-api-rust)
  - [tableau-plugin](https://github.com/tableau/tableau-plugin)
  - [tableau-ai-utilities](https://github.com/tableau/tableau-ai-utilities)
  - [tableau-metadata-explorer](https://github.com/tableau/tableau-metadata-explorer)
  - [Tableau-AI-Model-Evaluation-Tool](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool)
  - [tab-s3-converter](https://github.com/tableau/tab-s3-converter) (rewrites the legacy `class='s3'` connector to `class='amazon-s3'`, stdlib only)
- The Hyper API .NET binding was deprecated and open-sourced as [tableau/hyper-api-dotnet](https://github.com/tableau/hyper-api-dotnet) (archived) — [releases.md](https://github.com/tableau/hyper-db/blob/main/website/docs/releases.md)
- LogShark (4.3.0, 2026-11-10) analyzes Server/Desktop logs — [tableau/Logshark](https://github.com/tableau/Logshark)
- Tableau Server in Kubernetes docs/templates — [tableau/tableau-server-in-kubernetes](https://github.com/tableau/tableau-server-in-kubernetes)

### Inferences
- VizAlerts (archived, MIT) is worth reading when re-implementing data-driven alerts in TabFree, since it was Tableau's open implementation of the feature on Server.
- The fact that Tableau published an XSD and a calc grammar under Apache-2.0 in 2026 lowers TabFree's legal and technical risk in parsing TWB.

### Gaps
- I did not review each official repo's license text or contributor terms beyond the SPDX id GitHub reports. `tableau-data-dictionary` and `tabjolt` show no license, so reuse of their content needs checking.

## Q10. Full long list of relevant repositories (165 catalogued) plus PyPI / npm / GitLab packages

### Takeaway
I catalogued 165 relevant repos across 10 categories. Most Tableau-specific community code was created in 2025–2026, has fewer than 25 stars and has one maintainer; the field is nascent and fragmented. The only large projects are the official Tableau repos, general-purpose BI platforms, and catalogs with Tableau connectors.

### Cited Findings
Each row's repo link is its source; metadata comes from the GitHub API on 2026-11-20.

#### Official Tableau org (github.com/tableau)

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [tableau/server-client-python](https://github.com/tableau/server-client-python) | 718 | 2026-11-17 (active) | MIT | Python | TSC: Python library for the Tableau Server REST API; includes permissions and default-permissions endpoints. | Production; v0.42 (2026-10-15), 1,178 commits | **High** - Backbone for inventory, bulk download, permissions/schedules export. |
| [tableau/webdataconnector](https://github.com/tableau/webdataconnector) | 688 | 2026-10-19 (active) | MIT | HTML | Web Data Connector SDK. | Active | **Low** - Not needed after migration. |
| [tableau/rest-api-samples](https://github.com/tableau/rest-api-samples) | 436 | 2026-11-16 (active) | MIT | Python | Official REST API samples (Python etc.). | Maintained samples | **Medium** - Copy-paste patterns for endpoints TSC lacks. |
| [tableau/tableau-mcp](https://github.com/tableau/tableau-mcp) | 378 | 2026-11-20 (active) | Apache-2.0 | TypeScript | Official MCP server. Web server (Cloud+Server, PAT/OAuth) exposes 55 tools incl. list/get/download-workbook, query-datasource (VizQL Data Service), get-view-data, get-view-image, publish-workbook/datasource, query-admin-insights, render-interactive-viz, scaffold-data-app; separate Desktop authoring server (~78 tools). | Production; v6.0.4 (2026-11-09) | **High** - Best Claude-native extraction path for workbooks, view data and view images (parity ground truth). |
| [tableau/document-api-python](https://github.com/tableau/document-api-python) | 362 | 2026-07-02 (active) | MIT | Python | Create/modify .twb/.tds (mainly connection/datasource XML). | Low activity; last release v0.11 (2022-11-07) | **Medium** - Useful for connection rewriting; no worksheet/mark model. |
| [tableau/extensions-api](https://github.com/tableau/extensions-api) | 295 | 2026-11-18 (active) | Apache-2.0 | JavaScript | Extensions API docs/samples. | Active | **Low** - Only if dashboards stay inside Tableau. |
| [tableau/hyper-api-samples](https://github.com/tableau/hyper-api-samples) | 154 | 2026-03-17 (recent) | MIT | Python | Hyper API samples incl. tde-to-hyper (reads .tde via external(... format=>'tde') pinned to Hyper API 0.0.17537), hyper-to-csv, parquet-to-hyper, native-s3, s3-to-hyper, clouddb-extractor. | Maintained (2026-03) | **High** - Ready-made converters for .tde->.hyper and .hyper->CSV; S3 patterns. |
| [tableau/community-tableau-server-insights](https://github.com/tableau/community-tableau-server-insights) | 150 | 2026-06-02 (active) | MIT | Python | Community data sources (.twb) over the Server PostgreSQL repository (workgroup DB). | Stale content (last release 2021), repo pushed 2026-06 | **Medium** - Shows which repository tables answer usage/schedule/subscription questions. |
| [tableau/TabMigrate](https://github.com/tableau/TabMigrate) | 121 | 2023-03-29 (archived) | MIT | C# | Legacy tool to move content between Tableau Server environments. | Archived (2023) | **Low** - Historic reference only. |
| [tableau/Logshark](https://github.com/tableau/Logshark) | 119 | 2026-11-11 (active) | MIT | C# | Log analysis utility building workbooks from Server/Desktop logs. | Active; 4.3.0 (2026-11-10) | **Low** - Ops tooling for the old server only. |
| [tableau/connector-plugin-sdk](https://github.com/tableau/connector-plugin-sdk) | 116 | 2026-07-21 (active) | MIT | Python | SDK for Tableau connector plugins (TACO). | Active (2026-07) | **Low** - Only for coexistence period. |
| [tableau/TabMon](https://github.com/tableau/TabMon) | 108 | 2024-03-18 (archived) | MIT | C# | Server performance monitoring service. | Archived (2024) | **Low** - Historic. |
| [tableau/tabjolt](https://github.com/tableau/tabjolt) | 108 | 2026-06-02 (active) | none | - | Load generator for Tableau Server. | Repo pushed 2026-06; no license file | **Low** - Could baseline old-server performance. |
| [tableau/query-graphs](https://github.com/tableau/query-graphs) | 105 | 2026-11-12 (active) | MIT | TypeScript | Visualizer for query plans (Hyper, Postgres, Tableau). | Active (2026-11) | **Low** - Debug aid only. |
| [tableau/metadata-api-samples](https://github.com/tableau/metadata-api-samples) | 84 | 2021-08-03 (archived) | none | - | Metadata API GraphQL samples. | Archived (2021) | **Low** - Old query examples. |
| [tableau/tableau_langchain](https://github.com/tableau/tableau_langchain) | 82 | 2026-02-27 (recent) | MIT | Python | LangChain/LangGraph tools for Tableau (VDS querying etc.). | Active (2026-02) | **Low** - Superseded by tableau-mcp for Claude use. |
| [tableau/tableau-document-schemas](https://github.com/tableau/tableau-document-schemas) | 68 | 2026-11-03 (active) | Apache-2.0 | - | Official XSD for the TWB format (schemas for 2026.1, 2026.2, 2026.3), formally published Feb 2026. | Official, new (16 commits) | **High** - Authoritative grammar to validate/generate a TWB parser or IR. |
| [tableau/embedding-api-v3-samples](https://github.com/tableau/embedding-api-v3-samples) | 57 | 2026-06-26 (active) | MIT | TypeScript | Embedding API v3 samples. | Active | **Low** - Coexistence/embedding only. |
| [tableau/tableau-data-dictionary](https://github.com/tableau/tableau-data-dictionary) | 54 | 2026-10-02 (active) | none | HTML | Data dictionary for the workgroup PostgreSQL repository, per release. | Active (2026-10) | **High** - Needed to migrate schedules, subscriptions, alerts, favorites, usage history. |
| [tableau/tabcmd](https://github.com/tableau/tabcmd) | 51 | 2026-11-18 (active) | MIT | Python | Python re-implementation of tabcmd CLI (export, get, publish, etc.). | Active; v2.0.21 (2026-07-10) | **Medium** - Scriptable bulk export of PDFs/PNGs/CSVs and content. |
| [tableau/tableau-server-in-kubernetes](https://github.com/tableau/tableau-server-in-kubernetes) | 47 | 2026-06-02 (active) | MIT | - | Docs/templates to run Tableau Server in Kubernetes. | Active | **Low** - Infra for the system being replaced. |
| [tableau/hyper-db](https://github.com/tableau/hyper-db) | 38 | 2026-10-14 (active) | other/custom | TypeScript | Hyper docs + release notes (COPY TO added 0.0.17360, Arrow read/write, Parquet/Iceberg/CSV external formats, S3 reads). | Active docs (2026-10) | **High** - Defines what Hyper can export directly (Parquet/Arrow) for Athena landing. |
| [tableau/tableau-migration-sdk](https://github.com/tableau/tableau-migration-sdk) | 27 | 2026-10-29 (active) | Apache-2.0 | C# | C#/.NET + Python (pip tableau_migration) SDK for Server-to-Cloud migration: users, groups, projects, data sources, workbooks, extract refresh tasks, custom views; hooks/transformers incl. permissions. | Production; 6.1.0 (2026-06-01) | **Medium** - Target is Tableau Cloud only, but content enumeration, mapping and hook design are reusable. |
| [tableau/VizQL-Data-Service](https://github.com/tableau/VizQL-Data-Service) | 13 | 2026-10-07 (active) | Apache-2.0 | Python | OpenAPI schema + Python SDK (vizql-data-service-py) for VizQL Data Service (query published data sources incl. extracts). | Active (2026-10) | **High** - Pull extract data from Server without downloading .hyper; semantic queries for parity checks. |
| [tableau/tableau-migration-app](https://github.com/tableau/tableau-migration-app) | 11 | 2026-06-02 (active) | Apache-2.0 | C# | Desktop GUI on top of the Migration SDK for simple Server-to-Cloud moves. | Active (2026-06) | **Low** - Tableau-to-Tableau only. |
| [tableau/tab-compare](https://github.com/tableau/tab-compare) | 10 | 2022-12-01 (archived) | MIT | Python | Visual comparison of dashboards across Tableau versions. | Archived (2022) | **Low** - Idea source for visual parity testing. |
| [tableau/tableau-plugin](https://github.com/tableau/tableau-plugin) | 9 | 2026-11-20 (active) | Apache-2.0 | Python | Official AI plugin source for Claude/ChatGPT/Amazon Quick; skills: dashboard-advisor, data-app-authoring (AI writes HTML/JS viz-extension app on a published datasource, packages .twbx), data-quality-sentinel. | New (2026-08 to 2026-11) | **Medium** - Confirms vendor direction toward AI-authored HTML dashboards; reusable skill patterns. |
| [tableau/vail](https://github.com/tableau/vail) | 4 | 2021-06-11 (archived) | other/custom | TypeScript | VAIL (Visual Analytic Intent Language) for inferring visualization intent. | Archived (2021) | **Low** - Research reference for NL-to-viz. |
| [tableau/hyper-api-rust](https://github.com/tableau/hyper-api-rust) | 3 | 2026-11-09 (active) | Apache-2.0 | Rust | Pure-Rust Hyper client over PG wire protocol (still needs the hyperd binary); Arrow insert/read, gRPC; crates incl. hyperdb-mcp (MCP server for SQL on .hyper) and Node.js bindings. | Active; 1.0.0-rc.5 (2026-10-06), 396 commits | **High** - Fast .hyper -> Arrow -> Parquet path and an official MCP over .hyper files. |
| [tableau/tableau-metadata-explorer](https://github.com/tableau/tableau-metadata-explorer) | 1 | 2026-08-07 (active) | Apache-2.0 | Python | App to explore Metadata API (GraphQL) incl. NL questions, governance analyses, lineage graph. | New (2026-08) | **Medium** - Lineage inventory reference for migration scoping. |
| [tableau/Tableau-AI-Model-Evaluation-Tool](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool) | 0 | 2026-11-06 (active) | Apache-2.0 | TypeScript | Evaluates Tableau Server AI features; contains ANTLR4 grammars for the calc language (TableauCalculationGrammar.g4, "based on work by Dan Cory"; Prep TableauExpression.g4) with TS parsers. | Active (2026-11), Apache-2.0 | **High** - Only official machine-readable grammar for Tableau formulas (incl. FIXED/INCLUDE/EXCLUDE). |
| [tableau/tableau-ai-utilities](https://github.com/tableau/tableau-ai-utilities) | 0 | 2026-11-17 (active) | Apache-2.0 | Python | Catalog of Tableau Agent / MCP skills (calc-*, workbook-auditor, governance-scanner, etc.). | New (2026-10) | **Low** - Skills translate INTO Tableau; mostly useful as prompt patterns. |
| [tableau/tab-s3-converter](https://github.com/tableau/tab-s3-converter) | 0 | 2026-11-04 (active) | Apache-2.0 | Python | Rewrites legacy S3 connector XML to the Amazon S3 connector in .twb/.tds/.twbx/.tdsx; stdlib only. | New (2026-11) | **Low** - Example of deterministic connection rewriting. |

#### Workbook parsers, SDKs, viewers

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [aidatacooper/cwtwb](https://github.com/aidatacooper/cwtwb) | 51 | 2026-11-14 (active) | AGPL-3.0 | Python | Python toolkit + MCP server for reproducible .twb/.twbx generation, validation and migration. | Active; PyPI 0.28.0 (2026-11-14) | **Low** - AGPL-3.0 and authoring-into-Tableau direction. |
| [scinana/tableauCalculationExport](https://github.com/scinana/tableauCalculationExport) | 42 | 2024-11-01 (stale) | none | Jupyter Notebook | Notebook exporting all calculated fields from a workbook. | Stale (2024-11) | **Low** - No license. |
| [bpewyllie/TableauDesktopPy](https://github.com/bpewyllie/TableauDesktopPy) | 12 | 2022-03-31 (stale) | MIT | Python | Metadata extraction from Desktop workbook files. | Stale (2022) | **Low** - Abandoned. |
| [andey0Saikiran/tableau-lineage](https://github.com/andey0Saikiran/tableau-lineage) | 8 | 2026-11-16 (active) | MIT | TypeScript | In-browser Tableau workbook auditor: dead fields, duplicate calcs, lint, diffs, dependency graphs, stored SQL; MCP server. | Active (2026-11) | **Medium** - Client-side TWB parsing + lineage UI in TypeScript (MIT). |
| [weisberg/pytableau](https://github.com/weisberg/pytableau) | 7 | 2026-10-03 (active) | MIT | Python | "Unified Python SDK for Tableau workbook engineering": data, connections, semantic model, presentation, packaging, fleet ops, governance; 3.0 adds XML patches, semantic validation, joins/relationships, dual axes, table calcs. | Young; PyPI 3.0.0 (2026-10-02) | **Medium** - Most complete pure-Python TWB object model found; very new, single author. |
| [JVijeh/tableau-prep-analyzer-suite](https://github.com/JVijeh/tableau-prep-analyzer-suite) | 3 | 2026-04-24 (recent) | MIT | Python | Analyzes Tableau Prep .tfl flows (field usage, transformations). | Active (2026-04) | **Low** - Prep-flow inventory reference. |
| [PrigasG/twbparser](https://github.com/PrigasG/twbparser) | 2 | 2026-09-28 (active) | other/custom | R | R package to parse TWB/TWBX. | Active (2026-09) | **Low** - R-only. |
| [KeshiaRose/twb-data-source-mapper](https://github.com/KeshiaRose/twb-data-source-mapper) | 2 | 2025-08-05 (stale) | MIT | JavaScript | Maps which sheets use which data sources. | Small (2025-08) | **Low** - Trivial. |
| [florian-drouet/tableau-sql-parser](https://github.com/florian-drouet/tableau-sql-parser) | 1 | 2025-05-27 (stale) | Apache-2.0 | Python | Parses Tableau custom SQL to extract tables/columns. | Stale-ish (2025-05) | **Low** - Narrow utility. |
| [gitlab-data/TableauConMan](https://gitlab.com/gitlab-data/TableauConMan) | 1 | 2026-08-05 (active) | ? | Python | GitLab data team tool for managing Tableau content (PyPI TableauConMan 0.1.18). | GitLab-hosted (2026-08 activity) | **Low** - Content-as-code idea; GitLab, not GitHub. |
| [agredyaev/tabkit](https://github.com/agredyaev/tabkit) | 0 | 2026-11-18 (active) | MIT | Rust | Rust "headless TWB/TWBX runtime" + MCP: inspect datasources/fields/calcs/params/worksheets/dashboards, lineage SQL->field->calc->worksheet->dashboard, authoring, signed HTML/CSS/JS dashboard extension, REST publish. | Brand new; v0.9.1 (2026-11-18), 101 commits | **Medium** - MIT Rust parser + lineage + MCP; 0 stars, unproven. |
| [TrueCrimeDev/Tableau-LSP](https://github.com/TrueCrimeDev/Tableau-LSP) | 0 | 2026-09-24 (active) | MIT | TypeScript | VS Code language server for Tableau calc language (.twbl): parsing, diagnostics, hover, workbook editing. | Active (2026-09) | **Medium** - TypeScript calc parser/semantics usable for IDE or translator. |
| [tableau-China/dsh-tableau-twb-viewer](https://github.com/tableau-China/dsh-tableau-twb-viewer) | 0 | 2026-10-13 (active) | Apache-2.0 | TypeScript | DeepSeek-Harness plugin: renders .twb dashboard layout 1:1 as SVG in browser, field lineage, metadata (no .twbx, no data). | Active; npm 0.5.5 (2026-10-13) | **Medium** - Only open code found that re-renders TWB dashboard zone layout in a browser. |
| [HandcartCactus/tableau-xray](https://github.com/HandcartCactus/tableau-xray) | 0 | 2023-03-28 (archived) | MIT | Python | High-level Python parser for .twb/.twbx. | Archived (2023) | **Low** - Abandoned. |
| [mharidy93/Tableau-Dependency-Analyzer](https://github.com/mharidy93/Tableau-Dependency-Analyzer) | 0 | 2026-04-20 (recent) | MIT | TypeScript | Visual calc-field dependency explorer. | Single commit era (2026-04) | **Low** - Small. |

#### Tableau -> Power BI converters

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [Guust-Franssens/tableau-to-powerbi-migration](https://github.com/Guust-Franssens/tableau-to-powerbi-migration) | 21 | 2026-11-19 (active) | MIT | Python | Copilot CLI agents + deterministic parse_tableau.py (data sources, fields, calcs, worksheet encodings, dashboard layout, reference lines) -> PBIP; .hyper rows via tableauhyperapi; tested on 16 Tableau Public workbooks. | Active PoC; 91 open issues | **Medium** - Good TWB->spec extractor and agent-repair loop; Windows/Power BI Desktop-bound. |
| [ionutsd/twbx_to_pbip](https://github.com/ionutsd/twbx_to_pbip) | 2 | 2026-08-24 (active) | MIT | Python | Stdlib-only .twbx -> PBIP; runs in browser via WebAssembly; infers chart type like Show Me; single CSV, no calcs/filters/params. | PoC (2026-08) | **Medium** - marks.py Show-Me inference is reusable for HTML chart selection. |
| [cyphou/TableauToPowerBI](https://github.com/cyphou/TableauToPowerBI) | 1 | 2026-11-19 (active) | none | Python | Large Python generator of PBIP (PBIR+TMDL+M) from .twb/.twbx: DAX converter, 164 visual mappings, 97 connector keys, Prep-flow parser, hyper_reader; self-reports corpus gate FAILED 30/44. | Very active (192 commits, ~15k tests); 1 star | **Medium** - Richest mapping tables; GitHub detects no license file. |
| [rasgiza/tableau-migration-accelerator](https://github.com/rasgiza/tableau-migration-accelerator) | 1 | 2026-08-03 (active) | MIT | Python | Offline accelerator: schema, types, safe-subset calc->DAX, TMDL, PBIP; 13-workbook test estate. | PoC (2026-08) | **Low** - Power BI specific. |
| [nomie7/t2pbi-studio](https://github.com/nomie7/t2pbi-studio) | 0 | 2026-09-10 (active) | none | Python | Deterministic .twb/.twbx -> PBIP with IR, auto/review/refuse tiers, 6 validation gates, optional LLM pass for refused fields. | New (2026-09) | **Low** - No license; good design reference. |
| [AyushSingh110/Tableau-to-Power-BI-semantic-compiler](https://github.com/AyushSingh110/Tableau-to-Power-BI-semantic-compiler) | 0 | 2026-07-31 (active) | MIT | Python | IR-based compiler to Power BI TOM + experimental PBIR; reports 23.5% calc and 34.4% visual coverage on Superstore. | Active (2026-07) | **Medium** - Honest failure taxonomy; MIT. |
| [sourabhxmishra/TableauCalcToPowerBIDAX](https://github.com/sourabhxmishra/TableauCalcToPowerBIDAX) | 0 | 2026-07-07 (active) | MIT | Python | AST compiler: Tableau calcs -> DAX / Power Query M + side-by-side report; stdlib. | Small (2026-07) | **Medium** - Parser front-end reusable for SQL back-end. |
| [Satyam1616/biforge](https://github.com/Satyam1616/biforge) | 0 | 2026-10-02 (active) | MIT | Python | Offline Tableau -> Fabric/Power BI engine with calc lexer/parser/AST -> DAX; web UI. | Small (2026-10) | **Low** - Parser reusable; target irrelevant. |
| [MallikaChunduru/twb2pbi-migration-kit](https://github.com/MallikaChunduru/twb2pbi-migration-kit) | 0 | 2026-08-11 (active) | MIT | Python | Parses .twb with ElementTree; maps functions to DAX via reviewable YAML table (88.5% auto-map on fixtures). | Small (2026-08) | **Low** - YAML function-mapping idea. |
| [USEReady/TabToPbi-Migrate](https://github.com/USEReady/TabToPbi-Migrate) | 0 | 2026-04-15 (recent) | none | Python | Consultancy tool: TWB->metadata dict, Hyper->CSV via tableauhyperapi, PBI model builder. | Small (2026-04) | **Low** - No license. |
| [ckcampbell248/tableau-to-powerbi-skill](https://github.com/ckcampbell248/tableau-to-powerbi-skill) | 0 | 2026-11-03 (active) | none | Python | Agent skill for assessed Tableau->Power BI migration (TMDL/PBIR/PBIP). | New (2026-11) | **Low** - Skill only. |
| [Salamandastron1/tableau-to-powerbi-agentic-demo](https://github.com/Salamandastron1/tableau-to-powerbi-agentic-demo) | 0 | 2026-11-12 (active) | none | Python | Copilot agents + MCP tools demo on synthetic data. | New (2026-11) | **Low** - Demo. |
| [jennyney5/tableau-rayfin-migration](https://github.com/jennyney5/tableau-rayfin-migration) | 0 | 2026-11-18 (active) | none | Python | Tableau->Fabric/Rayfin migration skill with read-only inventory/export/validation helpers. | New (2026-11) | **Low** - Skill only. |
| [nitinbothe/tableau_to_pbi](https://github.com/nitinbothe/tableau_to_pbi) | 0 | 2026-07-06 (active) | none | Python | Tableau -> Power BI conversion code base. | Small (2026-07) | **Low** - No docs/license. |

#### Tableau -> other BI / semantic layer / code targets

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [Humphrey4data/tableau-to-hex-skill](https://github.com/Humphrey4data/tableau-to-hex-skill) | 91 | 2026-07-30 (active) | MIT | - | Agent skill migrating Tableau/Looker/Power BI dashboards to Hex apps; extracts .twbx XML, validates SQL vs warehouse, rebuilds with Hex AI, screenshot-diff parity loop. | Active (2026-07) | **Medium** - Best documented AI migration + visual-parity methodology; target proprietary. |
| [Querio-App/unshackle](https://github.com/Querio-App/unshackle) | 11 | 2026-11-19 (active) | Apache-2.0 | Python | Moves Tableau (most complete of 13 BI tools) into a Git repo: calcs/FIXED LODs -> Apache Ossie metrics, worksheets/dashboards -> Jupyter notebooks (SQL+chart per tile), group permissions -> Cedar policies, USERNAME()/ISMEMBEROF() user filters -> SQL row filters, .hyper data copy (Hyper API), verify vs Tableau view data, MCP server, AWS deploy guide. | Very active; v0.3.0 (2026-11-19), 338 commits since 2026-10-22 | **High** - Closest open-source match to TabFree migration layer; Apache-2.0, Python. |
| [Kyligence/zen-ml-toolkit](https://github.com/Kyligence/zen-ml-toolkit) | 9 | 2023-05-22 (stale) | Apache-2.0 | Java | Kyligence metrics toolkit; extracts metrics from Tableau .twb/.tds. | Stale (2023) | **Low** - Abandoned. |
| [hex-inc/hex-skills](https://github.com/hex-inc/hex-skills) | 8 | 2026-11-11 (active) | MIT | LookML | Official Hex skills incl. tableau-migration (.twb -> migration brief -> generative app, SQL-fidelity gate + visual QA loop). | Active (2026-11) | **Medium** - Vendor-built pattern; target proprietary. |
| [thoughtspot/thoughtspot-agent-skills](https://github.com/thoughtspot/thoughtspot-agent-skills) | 7 | 2026-11-19 (active) | other/custom | Python | Official ThoughtSpot skills incl. ts-convert-from-tableau (twb -> table/model TML + liveboards) with a 130+ row coverage matrix and Tableau formula translation. | Active (2026-11) | **Medium** - Coverage matrix is a ready-made checklist of Tableau constructs; license NOASSERTION. |
| [sigmacomputing/sigma-migration-skills](https://github.com/sigmacomputing/sigma-migration-skills) | 3 | 2026-11-18 (active) | Apache-2.0 | Ruby | Official Sigma converters incl. tableau-to-sigma, tableau-assessment and tableau-vds-to-cdw (pulls datasource data via VDS and lands it in Snowflake/Databricks). | Active (2026-11) | **Medium** - VDS->warehouse data-landing skill is directly adaptable to Athena/Redshift. |
| [fgarofalo56/csa-inabox](https://github.com/fgarofalo56/csa-inabox) | 3 | 2026-11-20 (active) | MIT | TypeScript | "Cloud-scale analytics in a box" docs incl. a Tableau->Power BI migration guide set: complete feature mapping, server migration, Prep migration, calc conversion, embedding migration, TCO. | Active docs (2026-11) | **Medium** - Feature-mapping checklist of Tableau Server/Desktop features to plan parity scope (MIT). |
| [VDFaller/dbt-prep-flow-converter](https://github.com/VDFaller/dbt-prep-flow-converter) | 2 | 2025-05-06 (stale) | Apache-2.0 | Python | Converts Tableau Prep flows (.tfl) to dbt SQL. | Stale-ish (2025-05) | **Medium** - Only open Prep->SQL converter found. |
| [AtScaleInc/ps-utils](https://github.com/AtScaleInc/ps-utils) | 1 | 2026-11-17 (active) | Apache-2.0 | TypeScript | AtScale PS CLI: generate Tableau/Excel/Power BI workbooks from AtScale models; Tableau gap analysis. | Active (2026-11) | **Low** - Reverse direction. |
| [tzockoll-creator/Tableau-Strategy-Migration-Skill](https://github.com/tzockoll-creator/Tableau-Strategy-Migration-Skill) | 1 | 2026-02-12 (recent) | none | Python | Skill to migrate Tableau dashboards to Strategy (MicroStrategy). | Single drop (2026-02) | **Low** - Target proprietary. |
| [smurphy6492/tableau-migration-toolkit](https://github.com/smurphy6492/tableau-migration-toolkit) | 1 | 2026-03-24 (recent) | MIT | Python | Claude Code skill/methodology for repointing Tableau workbooks from legacy DBs to cloud platforms (98 dashboards). | Single drop (2026-03) | **Low** - Repointing within Tableau. |
| [thoughtspot/ps_tools](https://github.com/thoughtspot/ps_tools) | 0 | 2023-05-16 (archived) | MIT | Python | CLI converting Tableau .tds/.twb to ThoughtSpot TML. | Archived (2023) | **Low** - Superseded. |
| [anujseth2/ts-tableau-to-thoughtspot](https://github.com/anujseth2/ts-tableau-to-thoughtspot) | 0 | 2026-07-21 (active) | other/custom | Python | Fork-style copy of ThoughtSpot agent skills with extra chart/layout fidelity. | Active (2026-07) | **Low** - Derivative. |
| [SquareShiftTech/tableau-to-looker-accelerator](https://github.com/SquareShiftTech/tableau-to-looker-accelerator) | 0 | 2026-01-12 (recent) | none | Python | Tableau->Looker assessment: download from Server or read local twb, emit JSON. | Stale-ish (2026-01) | **Low** - No license; assessment only. |
| [Vishalsugur/ParityBI-Validated-Tableau-to-Superset-Migration](https://github.com/Vishalsugur/ParityBI-Validated-Tableau-to-Superset-Migration) | 0 | 2026-10-20 (active) | none | Python | Demo: Airflow + Postgres feeding a Tableau workbook and Superset dashboards with 152 automated KPI parity checks. | Demo (2026-10) | **Low** - Parity-check pattern only; no converter. |
| [leducse/tableau-quicksight-migration-assistant](https://github.com/leducse/tableau-quicksight-migration-assistant) | 0 | 2026-06-06 (active) | none | Python | MVP Tableau->QuickSight: calc/visual/datasource mappings, validation gates, dry-run QuickSight API plan, guarded Bedrock path. | MVP (2026-06) | **Low** - AWS-adjacent ideas; no license. |
| [jklardata/tableau-twb-to-dbt](https://github.com/jklardata/tableau-twb-to-dbt) | 0 | 2026-03-24 (recent) | none | JavaScript | Browser tools: TWB -> dbt (stg/fct/dim, MetricFlow), LOD -> CTE templates, Claude refinement, docs/diff/audit. | Active site (2026-03) | **Low** - No license; good LOD->CTE approach to study. |
| [SSchneider22/tableau-twb-to-omni-semantic-layer](https://github.com/SSchneider22/tableau-twb-to-omni-semantic-layer) | 0 | 2026-04-17 (recent) | none | Python | Skill generating Omni semantic layer from .twb. | Small (2026-04) | **Low** - Target proprietary. |
| [TomOccon/bi-migration-toolkit](https://github.com/TomOccon/bi-migration-toolkit) | 0 | 2026-11-11 (active) | none | Python | Pattern for migrating hundreds of BI dashboards: inventory, fidelity triage into waves, idempotent conversion, readiness report. | New (2026-11) | **Low** - Program-management pattern. |
| [biztory/biztory-tableau-knowledge-mcp](https://github.com/biztory/biztory-tableau-knowledge-mcp) | 0 | 2026-11-13 (active) | MIT | Python | MCP for Tableau Knowledge graph: search, lineage, impact; ports semantic layer to Apache Ossie (OSI). | New (2026-11) | **Low** - Ossie export idea. |
| [scottlittle-atscale/superstore-tableau-migration](https://github.com/scottlittle-atscale/superstore-tableau-migration) | 0 | 2026-09-28 (active) | none | Mermaid | AtScale SML model re-hosting the Superstore workbook semantics on BigQuery. | Demo (2026-09) | **Low** - Example only. |

#### Calc / LOD translators

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [adammico-lab/Tableau-Calc-Engine-BETA](https://github.com/adammico-lab/Tableau-Calc-Engine-BETA) | 4 | 2026-07-13 (active) | Apache-2.0 | - | Prompt/skill for writing, debugging, translating Tableau calcs (LOD, table calcs, sets). | Beta (2026-07) | **Low** - Prompt content, not a parser. |
| [TheRealHrishav/tableau_aid](https://github.com/TheRealHrishav/tableau_aid) | 1 | 2026-09-01 (active) | MIT | Python | Extracts calcs from .twbx, builds dependency DAG, Lark grammar transpiles to Presto SQL, emits one layered WITH query; LOD/params left as placeholders. | Prototype (2026-09) | **Medium** - Presto dialect = Athena; MIT; small. |
| [lukashalim/scapeTableau](https://github.com/lukashalim/scapeTableau) | 1 | 2026-11-12 (active) | MIT | Python | BIEscape: zero-dependency Python AST transpiler of Tableau calcs to ANSI SQL/DAX; FIXED/INCLUDE/EXCLUDE -> window functions or grain subqueries; .hyper -> Excel via Hyper API. | Active (2026-11) | **Medium** - MIT SQL transpiler with LOD handling; young. |
| [vaishalizilpe/deLOD](https://github.com/vaishalizilpe/deLOD) | 0 | 2026-08-16 (active) | none | Python | Claude API tool to generate/explain/debug Tableau calcs from English. | Small (2026-08) | **Low** - LLM wrapper. |

#### .hyper / .tde tooling

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [innobi/pantab](https://github.com/innobi/pantab) | 122 | 2026-04-08 (recent) | BSD-3-Clause | Python | Read/write pandas (and Arrow) frames to/from .hyper via Hyper API. | Mature; v5.3.0 (2026-04-08) | **High** - Simplest bulk .hyper -> DataFrame/Arrow -> Parquet path. |
| [bwiley1/pandleau](https://github.com/bwiley1/pandleau) | 59 | 2019-12-10 (stale) | MIT | Python | Pandas -> .hyper/.tde. | Stale (2019) | **Low** - Legacy. |
| [goodwillpunning/hyperleaup](https://github.com/goodwillpunning/hyperleaup) | 30 | 2026-01-08 (recent) | other/custom | Python | Create/modify .hyper from Spark DataFrames/SQL. | Active-ish (2026-01) | **Low** - Wrong direction (into Hyper). |
| [lyhyl/TDEDecrypt](https://github.com/lyhyl/TDEDecrypt) | 15 | 2026-01-04 (recent) | none | C# | Unrelated: decrypts .tde files of the game Teardown. | n/a | **Low** - Name collision; not Tableau. |
| [zhiruiwang/RTableau](https://github.com/zhiruiwang/RTableau) | 12 | 2018-06-27 (stale) | other/custom | R | R data.frame -> Tableau extract. | Stale (2018) | **Low** - Writer only. |
| [jlmorton/tableau](https://github.com/jlmorton/tableau) | 11 | 2019-04-03 (stale) | Apache-2.0 | Java | Java CSV->TDE converter on legacy Tableau SDK. | Stale (2019) | **Low** - TDE writer only. |
| [tableau-mkt-archived/node-tableau-sdk](https://github.com/tableau-mkt-archived/node-tableau-sdk) | 10 | 2019-01-04 (archived) | none | JavaScript | Node port of legacy Tableau Extract API. | Archived (2019) | **Low** - TDE writer only. |
| [danielgp/tableau-hyper-management](https://github.com/danielgp/tableau-hyper-management) | 8 | 2026-01-29 (recent) | LGPL-3.0 | Python | CSV -> Hyper with column/type detection. | Stale (PyPI 2020; repo 2026-01) | **Low** - Into Hyper. |
| [mcoirad/tableau-hyper-refresher](https://github.com/mcoirad/tableau-hyper-refresher) | 5 | 2026-11-01 (active) | Apache-2.0 | Python | AWS reference workflow (Step Functions, Batch, Lambda, DynamoDB): reads TDSX metadata, plans Redshift UNLOADs, rebuilds/publishes Hyper extracts. | Active (2026-11) | **Medium** - AWS/Redshift + TDSX metadata patterns; reverse direction. |
| [AlexFrid/tableauhyperio](https://github.com/AlexFrid/tableauhyperio) | 5 | 2024-05-03 (stale) | BSD-3-Clause | Python | Pandas <-> Hyper (self-described DEPRECATED). | Deprecated (2024-05) | **Low** - Use pantab. |
| [dataiku/dss-plugin-tableau-hyper](https://github.com/dataiku/dss-plugin-tableau-hyper) | 3 | 2026-10-30 (active) | Apache-2.0 | Python | Dataiku plugin exporting to .hyper. | Active (2026-10) | **Low** - Into Hyper. |
| [yguo91/Tableau_Hyper_File_Converter](https://github.com/yguo91/Tableau_Hyper_File_Converter) | 1 | 2025-10-11 (stale) | none | HTML | Local web UI: .hyper -> CSV/JSON using the official Hyper API. | Small (2025-10) | **Low** - Trivial wrapper; no license. |
| [Coco-Alemana/duckdb_hyper](https://github.com/Coco-Alemana/duckdb_hyper) | 1 | 2025-02-19 (stale) | MIT | Python | Named "DuckDB extension for Tableau Hyper", but contains only the DuckDB extension template (quack example). | Stub (2025-02) | **Low** - No implementation. |
| [chrislee35/orange3-tableau-hyper](https://github.com/chrislee35/orange3-tableau-hyper) | 1 | 2025-04-27 (stale) | other/custom | Python | Orange3 add-on to load/save .hyper. | Small (2025-04) | **Low** - Niche. |
| [CSung28/hyper_inspector_api](https://github.com/CSung28/hyper_inspector_api) | 0 | 2026-06-30 (active) | none | Python | FastAPI app: upload .hyper, inspect tables, NL question -> validated Hyper SQL. | Small (2026-06) | **Low** - Demo. |
| [dbartley2-wiq/tableau-hyper](https://github.com/dbartley2-wiq/tableau-hyper) | 0 | 2024-10-28 (stale) | none | - | Placeholder "fork" of tableauhyperapi PyPI package (Apache-2.0). | Stub (2024-10) | **Low** - Empty. |
| [WillAyd/hyper-meson-patches](https://github.com/WillAyd/hyper-meson-patches) | 0 | 2024-07-10 (stale) | Apache-2.0 | Meson | Meson patch files for Hyper API distribution (used by pantab builds). | Small (2024-07) | **Low** - Build plumbing. |

#### Server/Cloud admin: inventory, permissions, backup, lineage, scraping

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [bryanthowell-tableau/tableau_tools](https://github.com/bryanthowell-tableau/tableau_tools) | 223 | 2022-02-15 (stale) | other/custom | Python | REST API, XML modification, tabcmd and repository tools. | Stale (2022-02) | **Low** - Historic but instructive. |
| [bertrandmartel/tableau-scraping](https://github.com/bertrandmartel/tableau-scraping) | 137 | 2024-05-08 (stale) | MIT | Python | Scrapes data from rendered Tableau vizzes (Public/Server) via the viz session API. | Stale (2024-05) | **Low** - Fallback data capture for parity when APIs are blocked. |
| [divinorum-webb/tableau-api-lib](https://github.com/divinorum-webb/tableau-api-lib) | 105 | 2023-10-24 (stale) | MIT | Python | Python wrapper for every REST API method. | Stale (2023-10) | **Low** - TSC preferred. |
| [simonw/tableau-to-sqlite](https://github.com/simonw/tableau-to-sqlite) | 13 | 2021-07-29 (stale) | Apache-2.0 | Python | Fetch data from Tableau views into SQLite (Datasette tool). | Stale (2021) | **Low** - Same as above. |
| [godatadriven/dbt-bi-exposures](https://github.com/godatadriven/dbt-bi-exposures) | 6 | 2024-05-28 (stale) | MIT | Python | Collects dbt exposures from BI tools incl. Tableau. | Stale (2024-05) | **Low** - Lineage idea. |
| [tdries/tableau-graphQL-mcp](https://github.com/tdries/tableau-graphQL-mcp) | 2 | 2026-11-15 (active) | MIT | Python | Read-only MCP for lineage and impact analysis over Metadata API; Server and Cloud. | Active; PyPI 0.2.0 (2026-10) | **Medium** - Lineage for migration scoping via Claude. |
| [simboli/tableau-cloud-audit](https://github.com/simboli/tableau-cloud-audit) | 1 | 2026-11-03 (active) | Apache-2.0 | Python | Client-run collector: users, groups, memberships, projects, workbooks, views, data sources, connections, permission rules (incl. project default templates) and Admin Insights -> one DuckDB file, pseudonymised. | Working alpha; PyPI 0.2.3 (2026-11-02) | **Medium** - Good permissions/inventory schema; Cloud-only. |
| [GalenBusch/Tableau-Server-Backup-Restore](https://github.com/GalenBusch/Tableau-Server-Backup-Restore) | 1 | 2019-01-25 (stale) | none | PowerShell | PowerShell backup/restore scripts. | Stale (2019) | **Low** - Legacy. |
| [brilliant-data/tableau-server-backup](https://gitlab.com/brilliant-data/tableau-server-backup) | 1 | 2021-09-20 (stale) | ? | ? | Backs up Server workbooks and data sources to a git repository. | Stale (2021) | **Low** - Content-to-git idea. |
| [i-richardwang/tableau-cli](https://github.com/i-richardwang/tableau-cli) | 1 | 2026-11-03 (active) | Apache-2.0 | Python | Agent-friendly CLI for Server/Cloud. | Small (2026-11) | **Low** - Overlaps TSC/MCP. |
| [jaybythebay/tableau-permissions](https://github.com/jaybythebay/tableau-permissions) | 0 | 2026-07-21 (active) | MIT | Python | Config-driven CLI to manage projects, groups and permissions (permissions-as-code). | Small (2026-07) | **Medium** - YAML permission model reusable for TabFree RBAC import. |
| [tabata140/tableau-permission-exporter](https://github.com/tabata140/tableau-permission-exporter) | 0 | 2026-01-21 (recent) | MIT | Python | GUI bulk permission export with user-level analysis. | Small (2026-01) | **Low** - Export reference. |
| [mizcausevic-dev/tableau-permission-audit-lab](https://github.com/mizcausevic-dev/tableau-permission-audit-lab) | 0 | 2026-09-10 (active) | AGPL-3.0 | TypeScript | Permission governance control plane (group sync drift, certification, stale sharing). | Small (2026-09) | **Low** - AGPL. |
| [jezwev/Tableau-Permissions](https://github.com/jezwev/Tableau-Permissions) | 0 | 2018-09-26 (stale) | Unlicense | PHP | PHP sample producing CSVs of object permissions. | Stale (2018) | **Low** - Legacy. |
| [Ch-Ruslan/tableau_permissions_via_postgree](https://github.com/Ch-Ruslan/tableau_permissions_via_postgree) | 0 | 2021-11-19 (stale) | none | - | Workbook permissions via repository SQL. | Stale (2021) | **Low** - SQL idea only. |
| [v3111707/tableau-full-backup-to-s3](https://github.com/v3111707/tableau-full-backup-to-s3) | 0 | 2026-03-23 (recent) | MIT | Python | Automates TSM backups and uploads to S3 with Zabbix monitoring. | Small (2026-03) | **Low** - Backup of old server only. |
| [swapnilyavalkar/tableau-server-backup](https://github.com/swapnilyavalkar/tableau-server-backup) | 0 | 2024-10-24 (stale) | none | Python | Python automation of Server backups. | Stale (2024-10) | **Low** - Old-server ops. |
| [palette-software/palette-insight](https://github.com/palette-software/palette-insight) | 0 | 2020-09-15 (stale) | GPL-3.0 | Shell | Palette Insight Tableau Server monitoring stack (agent, server, Greenplum reporting). | Stale (2016-2022), GPL-3.0 | **Low** - Historic. |
| [tfoldi/tableau-monitoring-execd](https://github.com/tfoldi/tableau-monitoring-execd) | 0 | 2023-03-20 (stale) | BSD-2-Clause | Rust | Telegraf execd for TSM cluster status. | Stale (2023) | **Low** - Ops. |
| [JVijeh/tableau-bulk-permissions-tutorial](https://github.com/JVijeh/tableau-bulk-permissions-tutorial) | 0 | 2026-04-14 (recent) | MIT | Python | DataDevQuest solution: programmatic permission updates via TSC. | Tutorial (2026-04) | **Low** - Sample code. |

#### MCP servers and LLM/agent tools

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [laviDrori0702/tableau-dashboard-creator-skill](https://github.com/laviDrori0702/tableau-dashboard-creator-skill) | 35 | 2026-09-17 (active) | MIT | Python | Agent plugin: dashboard request -> Tableau spec + interactive HTML mock + experimental .twbx. | Active (2026-09) | **Medium** - HTML-mock-from-spec pattern close to TabFree authoring. |
| [TheInformationLab/tableau_mcp_starter_kit](https://github.com/TheInformationLab/tableau_mcp_starter_kit) | 31 | 2026-04-28 (recent) | MIT | Python | Tableau MCP + LangChain starter for Server/Cloud. | Active (2026-04) | **Low** - Wrapper over official MCP. |
| [wjsutton/tableau-public-mcp](https://github.com/wjsutton/tableau-public-mcp) | 30 | 2026-06-12 (active) | Apache-2.0 | TypeScript | MCP server for Tableau Public APIs. | Active (2026-06) | **Low** - Public only. |
| [TomerMoskov/tableau-ai-skill](https://github.com/TomerMoskov/tableau-ai-skill) | 14 | 2026-04-28 (recent) | MIT | Python | Tableau AI skill. | Small (2026-04) | **Low** - Small. |
| [blakefeiza/vizwright](https://github.com/blakefeiza/vizwright) | 6 | 2026-10-27 (active) | MIT | Python | Claude Code agents: analysis, design, .twb XML authoring with Tableau Cloud render loop. | Active (2026-10) | **Low** - Authoring into Tableau. |
| [hetpatel-11/Tableau-MCP](https://github.com/hetpatel-11/Tableau-MCP) | 4 | 2025-06-18 (stale) | MIT | Python | Community Tableau MCP (Python). | Stale-ish (2025-06) | **Low** - Superseded by official. |
| [LokiMCPUniverse/tableau-mcp-server](https://github.com/LokiMCPUniverse/tableau-mcp-server) | 4 | 2026-04-18 (recent) | none | Python | Community Tableau MCP (Python). | Small (2026-04) | **Low** - Superseded by official. |
| [HanifIsya/tableau-workbook-skill](https://github.com/HanifIsya/tableau-workbook-skill) | 3 | 2026-10-29 (active) | MIT | Python | Claude Code skill editing .twb/.twbx with lxml; fixes broken Hyper extracts. | Small (2026-10) | **Low** - Authoring into Tableau. |
| [joeconstantino/test-tableau-mcp](https://github.com/joeconstantino/test-tableau-mcp) | 3 | 2025-04-29 (stale) | Apache-2.0 | Python | POC exposing VDS endpoints via MCP to Claude Desktop. | Stale-ish (2025-04) | **Low** - POC. |
| [banthia14aman/tableau-builder-mcp](https://github.com/banthia14aman/tableau-builder-mcp) | 1 | 2026-10-26 (active) | other/custom | Python | MCP + Claude skill: build .twbx from JSON spec, verify in Tableau Public. | Small (2026-10) | **Low** - Authoring into Tableau. |
| [Kokoabassplayer/tableau-agent-toolkit](https://github.com/Kokoabassplayer/tableau-agent-toolkit) | 1 | 2026-05-13 (recent) | Apache-2.0 | Python | Deterministic .twb generation from YAML specs; validate/package/publish. | Small (2026-05) | **Low** - Authoring into Tableau. |
| [AnkurNapa/tableau-dashboard-skill](https://github.com/AnkurNapa/tableau-dashboard-skill) | 1 | 2026-11-13 (active) | none | Python | Claude Code skill: 207 functions, 71 LOD/table-calc formulas, 53 chart recipes. | Small (2026-11) | **Low** - Knowledge base content. |
| [zavora-ai/mcp-bi](https://github.com/zavora-ai/mcp-bi) | 1 | 2026-09-12 (active) | other/custom | Rust | Rust MCP to read/drill/render saved dashboards; Superset first, Tableau etc. behind one interface. | Small (2026-09) | **Low** - Multi-BI MCP idea. |
| [SebAustin/tableau-mcp-publish](https://github.com/SebAustin/tableau-mcp-publish) | 0 | 2026-07-21 (active) | MIT | TypeScript | MCP write-side: publish Hyper/.tdsx datasources and .twbx workbooks from SQL/DataFrames. | Small (2026-07) | **Low** - Reverse direction. |
| [salesoptecs/tableau-mcp](https://github.com/salesoptecs/tableau-mcp) | 0 | 2026-09-04 (active) | MIT | Python | Single-file self-hosted Tableau Cloud MCP: search, render, export dashboards. | Single drop (2026-09) | **Low** - Small. |
| [CooperFryar/tableau-vizql-bridge](https://github.com/CooperFryar/tableau-vizql-bridge) | 0 | 2026-06-15 (active) | MIT | Python | Drives Tableau Cloud internal VizQL command API via Playwright (reverse-engineered). | Single drop (2026-06) | **Low** - Fragile, unofficial API. |
| [chami1221-eng/tableau-twb-xml-toolkit](https://github.com/chami1221-eng/tableau-twb-xml-toolkit) | 0 | 2026-11-03 (active) | Apache-2.0 | Python | TWB XML patterns, XSD schemas, agent skills and REST publish tools for LLM editing of workbooks. | Active (2026-11) | **Low** - Authoring into Tableau. |

#### Open-source alternatives, clones, building blocks

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [apache/superset](https://github.com/apache/superset) | 75586 | 2026-11-20 (active) | Apache-2.0 | Python | Open-source data exploration/visualization platform (RBAC, SQL Lab, dashboards). | Very active | **Medium** - Possible runtime substrate (RBAC, Athena/Redshift drivers) but no Tableau import. |
| [metabase/metabase](https://github.com/metabase/metabase) | 50442 | 2026-11-20 (active) | other/custom | Clojure | Open-source BI and embedded analytics. | Very active | **Low** - No Tableau import; AGPL/commercial split. |
| [dataease/dataease](https://github.com/dataease/dataease) | 24710 | 2026-11-20 (active) | other/custom | Java | Chinese open-source BI "alternative to Tableau" (Java; ECharts dashboards). | Very active | **Low** - No Tableau import; license non-standard (GitHub: Other). |
| [Kanaries/pygwalker](https://github.com/Kanaries/pygwalker) | 16004 | 2026-09-05 (active) | Apache-2.0 | Python | Python wrapper of Graphic Walker for DataFrames. | Active (2026-09) | **Low** - Notebook-centric. |
| [perspective-dev/perspective](https://github.com/perspective-dev/perspective) | 11616 | 2026-11-20 (active) | Apache-2.0 | Rust | WASM pivot/visualization engine. | Active | **Low** - Pivot tables in HTML. |
| [evidence-dev/evidence](https://github.com/evidence-dev/evidence) | 7134 | 2026-11-19 (active) | MIT | TypeScript | "BI as code": SQL + Markdown -> static interactive HTML dashboards. | Very active | **Medium** - Strong candidate output format for AI-authored HTML dashboards (MIT). |
| [lightdash/lightdash](https://github.com/lightdash/lightdash) | 6241 | 2026-11-20 (active) | other/custom | TypeScript | Agentic BI on dbt. | Very active | **Low** - No Tableau import. |
| [vega/vega-lite](https://github.com/vega/vega-lite) | 5563 | 2026-11-13 (active) | BSD-3-Clause | TypeScript | Grammar of interactive graphics (closest open analogue to VizQL shelves/marks). | Active | **High** - Natural compile target for converted Tableau worksheets. |
| [Kanaries/Rath](https://github.com/Kanaries/Rath) | 4686 | 2026-08-14 (active) | AGPL-3.0 | TypeScript | Automated EDA/visualization platform. | Active (2026-08), AGPL-3.0 | **Low** - AGPL. |
| [Kanaries/graphic-walker](https://github.com/Kanaries/graphic-walker) | 3292 | 2026-11-19 (active) | Apache-2.0 | TypeScript | Embeddable React "open source alternative to Tableau" drag-and-drop explorer (Vega-Lite). | Very active | **Medium** - Drop-in self-service exploration UI for TabFree (Apache-2.0). |
| [vega/voyager](https://github.com/vega/voyager) | 1497 | 2023-03-02 (stale) | other/custom | TypeScript | Visualization recommender/explorer (Polaris/Show-Me lineage). | Stale (2023) | **Low** - Research reference. |
| [uwdata/mosaic](https://github.com/uwdata/mosaic) | 1406 | 2026-11-20 (active) | other/custom | TypeScript | Scalable linked views over DuckDB. | Active | **Low** - Interaction layer idea. |
| [dbt-labs/dbt-charts](https://github.com/dbt-labs/dbt-charts) | 602 | 2026-11-20 (active) | Apache-2.0 | Python | Declarative YAML around SQL compiling to interactive dashboards; designed for agents (MCP/skills). | New, active (2026-11) | **Medium** - Agent-friendly dashboard spec (Apache-2.0). |
| [zainhoda/orbgo](https://github.com/zainhoda/orbgo) | 12 | 2018-08-23 (stale) | none | JavaScript | Tableau alternative generating Pandas code. | Stale (2018) | **Low** - Abandoned. |
| [aymanmali/quackboard](https://github.com/aymanmali/quackboard) | 1 | 2026-11-19 (active) | MIT | TypeScript | "Tableau-like" BI with DuckDB-Wasm + ECharts + Next.js. | Brand new (2026-11-19) | **Low** - Toy-scale. |
| [joheld/atelier-analytics](https://github.com/joheld/atelier-analytics) | 0 | 2026-11-01 (active) | MIT | TypeScript | Serverless in-browser "Tableau alternative" (DuckDB-WASM + Arrow + Observable Plot). | New (2026-11) | **Low** - Toy-scale. |
| [rohankumardubey/mini_tableau](https://github.com/rohankumardubey/mini_tableau) | 0 | 2025-12-16 (recent) | MIT | JavaScript | "MVP tableau clone". | Small (2025-12) | **Low** - Toy. |
| [Akhil-Kashyap/tableau-clone](https://github.com/Akhil-Kashyap/tableau-clone) | 0 | 2021-02-19 (stale) | none | JavaScript | Tableau clone (JS). | Stale (2021) | **Low** - Toy. |
| [yashacon/tableau_clone](https://github.com/yashacon/tableau_clone) | 0 | 2021-02-18 (stale) | none | Python | Tableau clone (Python). | Stale (2021) | **Low** - Toy. |
| [alexmahesh/Tableau_Alternative](https://github.com/alexmahesh/Tableau_Alternative) | 0 | 2023-11-30 (stale) | MIT | Python | Streamlit + PyGWalker Tableau alternative. | Stale (2023) | **Low** - Toy. |

#### Data catalogs with Tableau connectors

| Repo | Stars | Last push (status) | License | Lang | What it does | Completeness / maturity | Reuse for TabFree |
|---|---|---|---|---|---|---|---|
| [dagster-io/dagster](https://github.com/dagster-io/dagster) | 16446 | 2026-11-19 (active) | Apache-2.0 | Python | Orchestrator; dagster-tableau package models Tableau workbooks/sheets as assets. | Very active | **Low** - Orchestration idea. |
| [open-metadata/OpenMetadata](https://github.com/open-metadata/OpenMetadata) | 15820 | 2026-11-20 (active) | Apache-2.0 | TypeScript | Metadata platform with a Tableau dashboard connector. | Very active | **Medium** - Same; Apache-2.0. |
| [datahub-project/datahub](https://github.com/datahub-project/datahub) | 13006 | 2026-11-20 (active) | Apache-2.0 | Python | Metadata platform with a Tableau ingestion source (tableau.py, virtual connections, custom SQL lineage). | Very active | **Medium** - Proven Tableau metadata/lineage harvester to borrow logic from. |
| [amundsen-io/amundsen](https://github.com/amundsen-io/amundsen) | 4781 | 2026-09-10 (archived) | Apache-2.0 | Python | Data catalog (had Tableau extractors). | Archived (2026-09) | **Low** - Archived. |

**PyPI packages** (from the PyPI JSON API; latest version and upload date):
- [tableauserverclient](https://pypi.org/project/tableauserverclient/) 0.42 (2026-10-15)
- [tableauhyperapi](https://pypi.org/project/tableauhyperapi/) 0.0.26963 (2026-10-17)
- [pantab](https://pypi.org/project/pantab/) 5.3.0 (2026-04-08)
- [tableau-migration](https://pypi.org/project/tableau-migration/) 6.1.0 (2026-05-29)
- [vizql-data-service-py](https://pypi.org/project/vizql-data-service-py/) 20263.0.0 (2026-10-07)
- [tableaudocumentapi](https://pypi.org/project/tableaudocumentapi/) 0.11 (2022-11-07)
- [unshackle](https://pypi.org/project/unshackle/) 0.3.0 (2026-11-19)
- [pytableau](https://pypi.org/project/pytableau/) 3.0.0 (2026-10-02)
- [cwtwb](https://pypi.org/project/cwtwb/) 0.28.0 (2026-11-14)
- [tableau-inspector](https://pypi.org/project/tableau-inspector/) 0.28.0 (2026-11-05; "Analyze Tableau workbooks (.twb, .twbx) for common issues"; no repo URL)
- [tableau-cloud-audit](https://pypi.org/project/tableau-cloud-audit/) 0.2.3 (2026-11-02)
- [tableau-permissions](https://pypi.org/project/tableau-permissions/) 0.1.0 (2026-03-10)
- [tableau-graphql-mcp](https://pypi.org/project/tableau-graphql-mcp/) 0.2.0 (2026-10-14)
- [biztory-tableau-knowledge-mcp](https://pypi.org/project/biztory-tableau-knowledge-mcp/) 0.4.0 (2026-11-04; ports Tableau Knowledge to Apache Ossie)
- [tableau-hosted-mcp](https://pypi.org/project/tableau-hosted-mcp/) 0.1.1 (2026-07-18)
- [tableau-cli](https://pypi.org/project/tableau-cli/) 0.1.6 (2026-11-03)
- [dagster-tableau](https://pypi.org/project/dagster-tableau/) 0.29.31 (2026-11-19)
- [langchain-tableau](https://pypi.org/project/langchain-tableau/) 0.4.39 (2025-04-09)
- [tableau-utilities](https://pypi.org/project/tableau-utilities/) 2.2.15 (2025-01-30)
- [tableau-sql-parser](https://pypi.org/project/tableau-sql-parser/) 0.2.2 (2025-05-21)
- [tableau-api-lib](https://pypi.org/project/tableau-api-lib/) 0.1.50 (2022-10-15)
- [tableau-tools](https://pypi.org/project/tableau-tools/) 6.0.0 (2021-04-21)
- [TableauScraper](https://pypi.org/project/TableauScraper/) 0.1.29 (2021-11-10)
- [TableauConMan](https://pypi.org/project/TableauConMan/) 0.1.18 (2024-04-24, GitLab)
- [google-datacatalog-tableau-connector](https://pypi.org/project/google-datacatalog-tableau-connector/) 0.6.2 (2020-12-08)
- [tableau-hyper-management](https://pypi.org/project/tableau-hyper-management/) 1.5.10 (2020-06-15)

**npm packages** (from npm registry search):
- [@tableau/mcp-server](https://www.npmjs.com/package/@tableau/mcp-server) 6.0.4 (2026-11-09)
- [@tableau/embedding-api](https://www.npmjs.com/package/@tableau/embedding-api) 3.16.1
- [@tableau/tableau-ui](https://www.npmjs.com/package/@tableau/tableau-ui) 3.16.0
- [dsh-tableau-twb-viewer](https://www.npmjs.com/package/dsh-tableau-twb-viewer) 0.5.5 (2026-10-13)
- [@guandata/tableau-migration](https://www.npmjs.com/package/@guandata/tableau-migration) 0.3.1 (2026-10-31; Tableau → Guandata BI assessment/extraction/write-back CLI + agent skill; no repo link)
- [@wjsutton/tableau-public-mcp-server](https://www.npmjs.com/package/@wjsutton/tableau-public-mcp-server) 1.0.0
- [@wjsutton/tableau-powerpoint-tools](https://www.npmjs.com/package/@wjsutton/tableau-powerpoint-tools) 0.3.0
- [scrape-tableau](https://www.npmjs.com/package/scrape-tableau) 1.0.0 (2021)

**GitLab**: project search for "tableau" surfaced only [gitlab-data/TableauConMan](https://gitlab.com/gitlab-data/TableauConMan), [brilliant-data/tableau-server-backup](https://gitlab.com/brilliant-data/tableau-server-backup), [sabackup003/tableau-server-backup](https://gitlab.com/sabackup003/tableau-server-backup) ("script for backing up tableau server workbooks and datasources to a git repository"), [jhegele/tabscale](https://gitlab.com/jhegele/tabscale) and a mirror of TSC used for benchmarks.

### Inferences
- Star counts are a poor signal here because almost everything is new. Commit activity and self-reported test evidence are better proxies: unshackle has 338 commits in a month, and cyphou reports roughly 15k tests.
- Many very small repos titled "Tableau to Power BI migration" (portfolio projects, Copilot-generated demos) were found and omitted from the table. Examples: [swemereddy/Tableau-to-Power-BI-Migration](https://github.com/swemereddy/Tableau-to-Power-BI-Migration), [anuragwatane/Tableau-To-PowerBI](https://github.com/anuragwatane/Tableau-To-PowerBI), [architvermaa/bi-migration-tool](https://github.com/architvermaa/bi-migration-tool), [Themind-flayer/Tableau-To-PowerBi-Migration](https://github.com/Themind-flayer/Tableau-To-PowerBi-Migration), [hiteshnnovationalofficesolution/tableau-to-power-bi-migration-tool](https://github.com/hiteshnnovationalofficesolution/tableau-to-power-bi-migration-tool). A similar long tail of tiny Tableau MCP servers exists (e.g. [samiribrh/tableau-mcp](https://github.com/samiribrh/tableau-mcp), [ruobinc/tableau-mcp-ai-chat](https://github.com/ruobinc/tableau-mcp-ai-chat), [twilize5/tableaugen-mcp](https://github.com/twilize5/tableaugen-mcp)).

### Gaps
- Star and push data are a snapshot from 2026-11-20. "Last push" counts any branch push, not necessarily meaningful development.
- The PyPI scan matched only package names containing tableau/twb/hyperapi/pantab/vizql/tabcmd/tabpy. Packages with other names that handle Tableau files would be missed.

## Q11. What has nobody built? (explicit gaps for TabFree)

### Takeaway
Nobody has built the runtime half of TabFree, and several conversion pieces are also missing:
- an open server that hosts migrated Tableau content as AI-authored HTML dashboards with Tableau-Server-like features (sites/projects, enforced permissions and RLS, schedules, subscriptions, alerts, embedding, usage analytics) on AWS with Athena/Redshift
- a TWB → web-chart renderer
- a complete calc/LOD/table-calc → Trino/Athena SQL compiler
- a non-Tableau `.hyper`/`.tde` reader
- a Server-object migrator for schedules, subscriptions and alerts

### Cited Findings
GitHub repository searches returning **zero** repos on 2026-11-20 (search UI links reproduce the query):
- ["tableau to vega-lite"](https://github.com/search?q=tableau+to+vega-lite&type=repositories), ["tableau to echarts"](https://github.com/search?q=tableau+to+echarts&type=repositories), ["tableau to metabase"](https://github.com/search?q=tableau+to+metabase&type=repositories), ["tableau to evidence"](https://github.com/search?q=tableau+to+evidence&type=repositories), ["tableau to html"](https://github.com/search?q=tableau+to+html&type=repositories), ["twbx render"](https://github.com/search?q=twbx+render&type=repositories)
- ["tde reader"](https://github.com/search?q=tde+reader&type=repositories), ["tableau hyper reader"](https://github.com/search?q=tableau+hyper+reader&type=repositories), ["hyper parquet"](https://github.com/search?q=hyper+parquet&type=repositories), ["hyper extract to s3"](https://github.com/search?q=hyper+extract+to+s3&type=repositories), ["tableau extract athena"](https://github.com/search?q=tableau+extract+athena&type=repositories), ["tableau extract to iceberg"](https://github.com/search?q=tableau+extract+to+iceberg&type=repositories), ["tableau to trino"](https://github.com/search?q=tableau+to+trino&type=repositories)
- ["tableau server alternative"](https://github.com/search?q=tableau+server+alternative&type=repositories), ["open source tableau server"](https://github.com/search?q=open+source+tableau+server&type=repositories), ["tableau public alternative"](https://github.com/search?q=tableau+public+alternative&type=repositories)
- ["tableau formula parser"](https://github.com/search?q=tableau+formula+parser&type=repositories), ["LOD expression sql"](https://github.com/search?q=LOD+expression+sql&type=repositories), ["tableau calc to sql"](https://github.com/search?q=tableau+calc+to+sql&type=repositories)
- ["tableau content migration"](https://github.com/search?q=tableau+content+migration&type=repositories), ["tableau rls cedar"](https://github.com/search?q=tableau+rls+cedar&type=repositories), ["workgroup postgres tableau"](https://github.com/search?q=workgroup+postgres+tableau&type=repositories)

Evidence of partial coverage:
- unshackle leaves table calcs, INCLUDE/EXCLUDE LODs, parameters in metrics, blends and project default permissions as TODO, and does not enforce policies — [unshackle](https://github.com/Querio-App/unshackle)
- the only "DuckDB Hyper extension" is an empty template — [Coco-Alemana/duckdb_hyper](https://github.com/Coco-Alemana/duckdb_hyper)
- `.tde` support ended in Desktop 2024.2 — [Tableau Help](https://help.tableau.com/current/pro/desktop/en-us/extracting_upgrade.htm)
- the only Prep → SQL converter found is stale — [VDFaller/dbt-prep-flow-converter](https://github.com/VDFaller/dbt-prep-flow-converter)

### Inferences
Concrete unbuilt components TabFree would have to write, roughly in order of difficulty:
1. **Worksheet → web chart compiler.** Shelves, marks, encodings, dual/combined axes, reference lines, table calcs computed per pane, quick filters, parameters, dashboard zones, actions (filter, highlight, URL, set, parameter) → HTML + Vega-Lite or ECharts.
2. **Calc compiler to Trino/Athena (and Redshift) SQL** with viz-context-aware INCLUDE/EXCLUDE LODs and table-calc addressing. Start from the official ANTLR grammar.
3. **Server-object migration.** Schedules, extract-refresh tasks, subscriptions, data-driven alerts, custom views, favorites, comments, usage history and owner mappings, from the REST API and workgroup DB into TabFree equivalents.
4. **Permission enforcement runtime.** Effective permissions (including project defaults and locked projects), user filters/RLS to Athena (Lake Formation or view-based) and Redshift (RLS policies), plus SSO/SCIM group sync. unshackle stops at policy files.
5. **Extract pipeline.** `.tde` → `.hyper` → Arrow/Parquet → S3 → Glue/Athena or Redshift, with incremental-refresh equivalents. It needs Tableau's `hyperd` once per file, since no open reader exists.
6. **Visual and numeric parity harness.** Productized from the unshackle verify, Hex screenshot-diff and tab-compare ideas.
7. **Tableau-REST-compatible API shim** so existing tabcmd/TSC automation keeps working. No precedent found.

### Gaps
- Zero-result searches are evidence of absence only for repos whose name, description or topics use those words. Private repos, monorepos with Tableau modules, commercial products (e.g. Pulse Convert, Kanerika FLIP, MAQ) and Gitee-hosted code were not covered.
- No hands-on validation was done. Feasibility of items 1–7 is my inference from what the existing projects report they cannot do.
