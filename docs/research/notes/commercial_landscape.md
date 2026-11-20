# Commercial Landscape: Tableau Migration Tools and Services, AI-Native BI, and Tableau's Own Direction (as of 2026-11-20)

Scope: commercial vendors and Tableau/Salesforce only. GitHub repos and open-source BI platforms are covered by other researchers; open-source items appear here only where a commercial vendor ships them, or as pointers. Unless a claim says "verified in docs", automation and accuracy percentages are **vendor claims**. Dates are given as reported by the source. Where my direct fetch failed (HTTP 403/404, or the domain was blocked), the Gaps sections say so.

---

## 1. Tableau migration tools and accelerators (any target): what they parse, claimed fidelity, cost

### Takeaway
The market is crowded and mostly services-led, and **every commercial converter targets another proprietary BI product**: Power BI/Fabric, Amazon Quick Sight, Looker, Sigma, Databricks AI/BI, Preset, or Snowflake Streamlit. Since mid-2026 the platform vendors have shipped LLM-agent converters that read exported Tableau files: AWS Transform BI agents (May 2026), Databricks Genie Code bulk import (GA 19 Nov 2026), Snowflake Cortex Code's Tableau-to-Streamlit skill (preview), and Sigma's open-source Claude Code "Migration Skills" (beta). Claimed automation runs from 60% to 98%. All of it is self-reported, no independent benchmark exists, and the most technically candid docs (Snowflake, Databricks) say outright that converted output must be verified by hand. Almost none publish prices. The exception is Antares, at $200 per dashboard.

### Cited Findings

#### Tableau → Power BI / Fabric (largest segment)
- **Pulse Convert (Office Solution AI Labs).**
  - **Method:** Uses an in-house LLM called "Pulse" to interpret Tableau logic and generate Power BI semantic models and DAX. The vendor says it parses .TWB XML "at the node level" (connections, aliases, join logic), outputs a Power BI Project (.PBIP), and uses the **Tableau Server repository (PostgreSQL) plus the Metadata API** to find unused "ghost reports". — [Office Solution product page](https://innovationalofficesolution.com/Tableau-To-PowerBI/)
  - **Claimed scope:** dashboards, visuals, filters, calculated fields; LOD expressions (FIXED/INCLUDE/EXCLUDE) mapped to CALCULATE/SUMMARIZE/FILTER; data blending; extracts to Power BI dataflows; Server items such as "access roles, refresh schedules, and dataset permissions"; colors and fonts; bookmarks and tooltip pages. The page does not explicitly list table calculations, parameters, actions, or RLS. — [Office Solution](https://innovationalofficesolution.com/Tableau-To-PowerBI/)
  - **Fidelity claims (vendor):** "initial accuracy of 75–90%" with "12–25% manual fine-tuning"; a comparison table on the same page says 70–90%, so the figures are inconsistent. Conversion takes "6–16 minutes". A linked press-release title claims an unnamed FMCG enterprise migrated **5,000 dashboards in 3 months at 88% accuracy**. — [Office Solution](https://innovationalofficesolution.com/Tableau-To-PowerBI/)
  - **Pricing and programs:** No price is published. There is a free 5-dashboard proof of concept and a free assessment. — [Office Solution](https://innovationalofficesolution.com/Tableau-To-PowerBI/). Search-result summaries of the Microsoft Marketplace listing say it reached GA in **May 2026**, with Microsoft **ECIF co-funding for 5-dashboard pilots**. I could not open the listing (HTTP 403), so this is unverified. — [Microsoft Marketplace: Pulse Convert](https://marketplace.microsoft.com/en-us/product/officesolution1640276900203.pulseconvert2?tab=overview); a third-party guide cites the 88% figure — [Thinklytics](https://thinklytics.com/insights/tableau-to-power-bi-migration-2026)
- **Kanerika FLIP.**
  - **Claims:** automatically converts Tableau calculated fields, table calculations, and LOD expressions to DAX; checks every migrated report against the Tableau original; flags non-standard or deeply nested logic for engineer review. Its case study reports "80% of the migration effort automated" and "100% of dashboard logic preserved", though complex dashboards were still reviewed manually. — [Kanerika Tableau→Power BI](https://kanerika.com/services/migration/tableau-to-power-bi/)
  - **Pricing:** not public (Azure Marketplace SaaS offer). — [Microsoft Marketplace: Kanerika](https://marketplace.microsoft.com/en-us/product/kanerikainc1719422858450.tableau-to-power-bi-saas-offer); [Antares roundup, 11 Nov 2026](https://getantares.io/best-tableau-to-power-bi-migration-tool/)
- **Impetus LeapLogic.** Its materials variously cite "70 to 95%", "80 to 95%" and "up to 95%" automatic conversion, with row- and cell-level query validation and parallel-run support. This comes from a competitor's (Kanerika's) comparison page, so treat it with caution. — [Kanerika: FLIP vs LeapLogic](https://kanerika.com/compare/flip-vs-leaplogic/)
- **Sparity BIPort.** Reads .twb/.twbx/.tds files and translates LOD expressions and parameters to DAX. Claims "up to 70%" of conversion automated. Sold within Sparity's migration services; no public price. — [Sparity accelerator](https://www.sparity.com/accelerators/tableau-to-power-bi-migration-accelerator/); [Antares roundup](https://getantares.io/best-tableau-to-power-bi-migration-tool/)
- **EPAM migVisor.** Converts Tableau reports to Power BI and refactors queries and visuals automatically (vendor blog). — [EPAM SolutionsHub](https://solutionshub.epam.com/blog/post/tableau-to-power-bi-migration)
- **Viztran (DataKulture).** Claims the highest accuracy in the market, "often 95–98%" (vendor blog). — [DataKulture](https://datakulture.com/blog/tableau-to-power%20bi-migration/)
- **Antares.**
  - **Method:** a "deterministic-first pipeline with guardrailed, validated AI steps" that reads metadata only. One Tableau dashboard becomes one Power BI report page.
  - **Targets:** Power BI/Fabric and Databricks AI/BI.
  - **Pricing:** the only public price found anywhere: **$200 flat per source dashboard**, plus LLM cost "typically under $10 per dashboard". The Analyzer is free.
  - **Fidelity:** Antares itself says "no cross-platform conversion delivers pixel-perfect reproduction". — [Antares roundup (vendor-authored), 11 Nov 2026](https://getantares.io/best-tableau-to-power-bi-migration-tool/)
- **BiChart.** "AI-powered". Migrates workbook metadata (fields, measures, calculated fields, relationships) to PBIP format. Does **not** migrate data or perform automated testing. Sold as an annual subscription of migration credits (one per workbook); no numeric price. — [Antares roundup](https://getantares.io/best-tableau-to-power-bi-migration-tool/)
- **USEReady MigratorIQ.** An agent-based platform with five agents, including a "trust and validation agent". Targets Quick Sight, Tableau Cloud, and Power BI. Price not published; free assessment. — [Antares roundup](https://getantares.io/best-tableau-to-power-bi-migration-tool/). Its AWS Marketplace listing "MigratorIQ4Tableau" (assessment and migration to Amazon Quick) requires Quick Sight Enterprise Edition. — [AWS Marketplace: MigratorIQ4Tableau](https://aws.amazon.com/marketplace/pp/prodview-is23bnwgz6672)
- **DataTerrain.** Services-led, with proprietary automation, QA, and business-user review. Cites 400+ customers and 27,000+ reports built. Fixed-cost migrations; no accuracy claim. — [Antares roundup](https://getantares.io/best-tableau-to-power-bi-migration-tool/)
- **Microsoft first-party.** FabCon Europe 2026 coverage shows **no Tableau-to-Fabric migration agent** from Microsoft. The migration-related announcement was a Fabric data-engineering agent (built on the Osmos acquisition) for ETL and schema work, not Tableau content. — [James Serra, Oct 2026](https://www.jamesserra.com/archive/2026/10/announcements-from-the-microsoft-fabric-community-conference-barcelona-2026/); [Microsoft Azure blog: FabCon 2026](https://azure.microsoft.com/en-us/blog/fabcon-and-sqlcon-2026-in-barcelona-building-the-data-foundation-for-microsoft-copilot-and-agents/). An older Microsoft community answer likewise says there is no supported feature to convert Tableau reports to Power BI. — [Fabric community thread (older, undated in results)](https://community.fabric.microsoft.com/t5/Desktop/Migrate-to-Power-BI-from-Tableau/m-p/617739/highlight/true)
- **Practitioner caution.** Vendor demos tend to use dashboards "from the easy side of the conversion distribution". Before buying, test a dashboard that combines RLS, LOD calculations, and parameter actions. — [Thinklytics 2026 guide](https://thinklytics.com/insights/tableau-to-power-bi-migration-2026)

#### Tableau → Amazon Quick Sight (AWS): most relevant to an AWS-hosted TabFree
- **AWS Transform BI migration agents** (posted **1 May 2026**).
  - **Scope:** converts Tableau and Power BI dashboards into Quick Sight assets, "rebuilding datasets, calculated fields, visualizations, and filters". An Analyzer agent scores migration readiness, then a Converter agent converts, all through AWS Transform's chat interface. Afterwards, Quick admins assign ownership to BI authors, who validate and publish.
  - **Where it runs:** "All processing runs within your AWS account; no data leaves your environment."
  - **Who built it:** Wavicle Data Solutions (an AWS Advanced Consulting Partner).
  - **Purchase:** sold as 4 agents on AWS Marketplace (an Analyzer and a Converter for each of Tableau and Power BI). Available in us-east-1 and can create assets in every commercial region where Quick Sight is offered.
  - **Price:** not listed. Customers are told to ask their account team about "free or discounted AWS Quick migrations" programs.
  - **Not mentioned:** parameters, actions, LODs, permissions. — [Amazon Quick Community announcement, 1 May 2026](https://community.amazonquick.com/t/aws-transform-now-offers-bi-migration-agents-for-power-bi-and-tableau-to-amazon-quick/52292); [AWS Transform launch guide](https://docs.aws.amazon.com/transform/latest/launchguide/aws-transform.html); [AWS ML blog, via search summary only (404 when fetched)](https://aws.amazon.com/blogs/machine-learning/aws-transform-now-automates-bi-migration-to-amazon-quick-in-days/)
- **Wavicle EZConvertBI** (the product behind the agents) claims up to 70% migration-effort reduction (vendor claim, via search summary). — [Wavicle EZConvertBI](https://wavicledata.com/capabilities/data-solutions/ezconvertbi/). Wavicle's AWS-Transform page now redirects to a Deloitte release: **Deloitte acquired "substantially all of the assets of Wavicle Data Solutions" (31 Aug 2026)**. The release does not mention EZConvertBI or the AWS agents. — [Deloitte press release](https://www.deloitte.com/us/en/about/press-room/deloitte-aquires-the-business-of-wavicle-a-top-tier-data-and-ai-engineering-organization.html)
- **Other AWS-target accelerators:** Quantiphi "Quick to QuickSight (Q2Q)" — [Quantiphi](https://ai.quantiphi.com/quick-to-quicksight); USEReady MigratorIQ4Tableau (see above).

#### Tableau → Databricks AI/BI
- **Genie Code bulk migration** (Databricks blog, **19 Nov 2026**, stated **GA**).
  - **Method:** an agent reads exported .twb/.twbx/.tds/.tdsx files (and Power BI .pbit/.tmdl), pairs each workbook with its data source, and generates AI/BI Dashboards plus **metric views** in a chosen catalog and schema.
  - **Requirements:** files must be staged in a Unity Catalog volume; "partner-powered AI features" must be enabled; the data must already be reachable in Databricks (UC tables, Lakeflow Connect, or Lakehouse Federation).
  - **Fidelity caveats from Databricks itself:** "Completed" does not mean every visual is final; uploading "before" screenshots improves layout fidelity; a read-only MCP connection to the source tool "meaningfully improves accuracy"; if a source is missing, the agent may fall back to the closest available table. There is no feature list for LOD, table calcs, or actions, and no pricing. — [Databricks blog](https://www.databricks.com/blog/bulk-migrate-your-dashboards-aibi-genie-code); [Databricks docs: Import BI files using Genie Code](https://docs.databricks.com/aws/en/dashboards/manage/import-bi)
- **Lakebridge / BladeBridge.** Lakebridge is a free Databricks Labs toolkit for **data-warehouse/SQL and ETL** migration (BladeBridge and Morpheus transpilers, plus reconciliation). It is not the dashboard path. — [DataGalaxy engineering blog on DAIS 2025](https://engineering.datagalaxy.com/databricks-unleashes-next-gen-data-ai-innovations-at-data-ai-summit-2025-a2279c8618e1?gi=795d0dd0620c); [Databricks DAIS 2025 announcements](https://www.databricks.com/kr/events/dataaisummit-2025-announcements)
- A third-party guide stresses that a Tableau workbook is not a unit in AI/BI, and that source tables must already be in Databricks and registered in Unity Catalog. — [Antares: Tableau→Databricks AI/BI](https://getantares.io/tableau-to-databricks-ai-bi-migration/)

#### Tableau → Snowflake (the only commercial converter found that outputs *code*)
- **Cortex Code (CoCo) skill `tableau-to-streamlit-in-snowflake`** (Preview, available to all accounts; page undated, ©2026). Converts a Tableau workbook into a **multipage Streamlit-in-Snowflake app (Python)**, one page per dashboard. Inputs are .twb/.twbx/.tds/.tdsx. **It does not read .hyper/.tde extract data.**
  - **Four phases:** analyze and propose a file plan; build a synthetic-data preview; iterate on the UI; cut over to real tables after approval.
  - **Translated (per docs):** row and aggregate calcs to Snowflake SQL; **LOD (FIXED/INCLUDE/EXCLUDE), table calculations, sets, groups, bins**; filters and parameters (including Top N, date and numeric ranges) to Streamlit widgets; filter and highlight actions to chart selections (other action types are reported); worksheets to Streamlit, Altair, `st.dataframe`, or `st.metric`; horizontal layout to column widths. Absolute positions are flowed and heights are approximate.
  - **Blocked until a human supplies a translation:** `SCRIPT_*`, `MODEL_*`, `PREVIOUS_VALUE`, bounded `WINDOW_MEDIAN`/`WINDOW_PERCENTILE`, sets without saved members.
  - **Security:** user filters and `USERNAME()` must be reimplemented as Snowflake row access policies, because "Tableau filter actions are not security".
  - **Snowflake's own caveat:** a compiled query "doesn't prove its results match the workbook", so the user must verify against Tableau.
  - **Pricing:** none stated. — [Snowflake docs](https://docs.snowflake.com/developer-guide/streamlit/migrations-and-upgrades/tableau-to-streamlit)

#### Tableau → Sigma
- **Sigma Migration Skills (beta).** These are **open-source skills a coding agent runs**: install the Tableau converter as a **Claude Code plugin**, or point Cursor or Cortex Code at the skill folders.
  - **Workflow:** six steps (Assess, Discover, Model, Build, Verify, Enhance). Needs Tableau credentials and Sigma API credentials; Sigma's MCP server is recommended.
  - **Scope:** converts workbooks and data sources, "including data that lives only in extracts". A bridge skill lands extract data in Snowflake or Databricks first. Calculations become Sigma formulas; LOD is not specifically mentioned, and permissions migration is not mentioned.
  - **Validation:** parity checks compare numbers and visuals on the same warehouse and mark a result green only on a match. Untranslatable items (for example, dynamic titles) are flagged rather than guessed.
  - **Other sources supported:** Power BI, Looker, Qlik, ThoughtSpot, Quick Sight, Cognos, MicroStrategy, Sisense, GoodData, Domo, Mode, Metabase, and Hex.
  - **Pricing:** none listed. — [Sigma: Migrations](https://www.sigmacomputing.com/product/migrations)

#### Tableau → Looker
- **SquareShift DashPort.** Reads Tableau TWB files, detects chart types and calculated fields, generates draft LookML, and runs parity checks; hosted on Google Cloud. Claims 60–80% of the work automated (unverified). — [SquareShift accelerator](https://www.squareshift.co/post/tableau-to-looker-migration-accelerator); [SquareShift solution page](https://www.squareshift.co/featured-solutions/tableau-to-looker/)
- **Rittman Analytics "Wire" (experimental).** A `twb_to_lookml.py` script with audit, convert, and lint commands. Emits LookML views, explores, and dashboards, plus a `needs_human.json` file; lint fails if any Tableau expression survives. — [Rittman Analytics tutorial](https://docs.rittmananalytics.com/en/latest/docs/tutorials/tableau-to-looker/)
- In a 2024 forum thread, Google said it offered **no automated Tableau→LookML conversion** and pointed to partners such as Impetus LeapLogic. LOD calculations were flagged as the hardest part. — [Google Developer forums](https://discuss.google.dev/t/tableau-to-looker-migration/167796)

#### Tableau → Preset / Apache Superset
- **Preset `tableau-to-preset` agent skill.** A guided agent workflow that converts a .twb/.twbx into a Preset dashboard through Superset MCP tools. It extracts the .twbx, maps dashboards, and asks which ones to convert. It is a skill, not a standalone converter. — [skillsmp listing of preset-io/agent-skills](https://skillsmp.com/creators/preset-io/agent-skills/plugins-preset-mcp-skills-skills-tableau-to-preset)
- **Andolasoft.** Services firm claiming "proprietary tools to map Tableau XML definitions to Superset JSON" (sales page, no technical detail). — [Andolasoft](https://andolasoft.com/what-we-do/tableau-migration-services)

#### Tableau → ThoughtSpot / Omni
- I found no dedicated Tableau converter from ThoughtSpot or Omni (searches returned nothing specific). Sigma lists ThoughtSpot as a *source* it can migrate from. — [Sigma: Migrations](https://www.sigmacomputing.com/product/migrations)

### Inferences
- **Three technical approaches recur:**
  - (a) Deterministic .twb XML parsing with rule-based calc→DAX/SQL translation: Sparity, Kanerika, Pulse Convert's parsing layer, Antares' "deterministic-first" pipeline, Rittman's script.
  - (b) **LLM coding agents working on exported files with human approval gates:** Databricks Genie Code, Snowflake CoCo, Sigma skills, Preset skill, AWS Transform. This is the 2026 trend, and it is essentially the TabFree approach (Claude Code + skills + MCP).
  - (c) Server-side discovery through the Tableau repository, Metadata API, or REST API: Pulse Convert, Sigma skills (Tableau credentials), and Databricks' optional read-only MCP to the source.
- **Two capabilities are almost entirely missing from commercial tools:** .hyper extract conversion and Tableau Server permission migration. Snowflake does not read .hyper. Sigma re-lands extracts into Snowflake or Databricks. Only Pulse Convert claims to migrate "access roles … dataset permissions". Snowflake says Tableau user filters must be rebuilt as warehouse policies. TabFree's ".hyper → open formats" and "keep permissions" goals are therefore under-served.
- **Snowflake's documented translation matrix** (what converts, what is partial, what is blocked) is the most precise public description of Tableau→code conversion. It is a good template for TabFree's own conversion-coverage spec and test plan.
- **"Free migration" is becoming the norm at the platform vendors.** AWS points to "free or discounted" migration programs, Microsoft co-funds pilots via ECIF (unverified), and Databricks, Snowflake, Sigma, and Preset ship the converters as features or skills. A paid converter or service business would compete with these subsidies. A free, open-source TabFree would not be undercut on price, but it would compete with them for attention.

### Gaps
- No public technical detail found for **Datagaps, Travinto, Tredence, Hakkoda, Cognizant, Accenture, Infosys, TCS, LTIMindtree** Tableau offerings (searches returned only Kanerika material). Treat these as services-led with no disclosed fidelity metrics.
- No **independent** fidelity benchmark (LOD, table calcs, actions, layout) exists for any tool. All percentages are vendor-reported and use different definitions ("effort automated" vs "accuracy").
- Pricing is not published for AWS Transform BI agents, Pulse Convert, FLIP, LeapLogic, MigratorIQ, SquareShift, or Sparity.
- Microsoft Marketplace pages returned HTTP 403, and the AWS What's New and ML-blog URLs returned 404 when fetched, so some details come from search-result summaries (marked above).
- An "Enlyft" switching statistic (Tableau→Power BI vs the reverse) appeared in search summaries, but I could not identify or verify its source, so it is excluded from the findings.

---

## 2. Commercial products that convert Tableau to HTML/React/code, or that let "AI write dashboards as code on a governed server": inspectable code vs proprietary objects, and MCP

### Takeaway
No commercial product converts Tableau workbooks into **HTML/React** code. The closest is Snowflake's Tableau→Streamlit (Python) skill. The most direct commercial analog to TabFree's authoring model is **Tableau's own "Data Apps"** (blog post 3 Nov 2026): MCP-connected agents such as Claude, ChatGPT, or Codex write custom HTML/JS web apps (React and d3 bundles tested) that are packaged in .twbx and hosted, governed, and permissioned by Tableau Cloud or Server. Per the developer docs it is in beta, and it requires paid Tableau. Anthropic's own **Claude Data** (GA 10 Nov 2026) lets Claude build live dashboards on Redshift and other warehouses, but it is SaaS on claude.ai, not inspectable code, and has no Tableau migration. Most AI-BI vendors (Omni, Sigma, ThoughtSpot, Power BI, Looker, Databricks, Amazon Quick) generate **proprietary objects**. Evidence (SQL + Markdown in git) and Hex/Observable (notebook and JS cells) are the code-first exceptions. Nearly every BI vendor now ships an MCP server, but few let an external agent fully author dashboards.

### Cited Findings

#### Tableau Data Apps + Tableau MCP + Tableau Studio (Tableau's answer to "AI writes dashboards as code")
- **What a Data App is:** a "custom, interactive analytical web app hosted and powered by the Tableau platform" that "separate[s] the front-end from the underlying query engine".
  - **How it's built:** describe the app in natural language to Tableau Studio, or connect "any MCP-compliant agent, such as Claude Desktop, ChatGPT, or Codex" through the Tableau MCP or Tableau Next MCP server.
  - **Governance:** on Cloud and Server, apps live inside workbooks, query Published or Embedded Data Sources, and inherit Tableau permissions and RLS. DB credentials are never exposed to the app or agent.
  - **Status (as of 3 Nov 2026):** Tableau Next has a beta. The Cloud/Server status is not stated. — [Salesforce/Tableau blog, 3 Nov 2026](https://www.salesforce.com/blog/tableau-data-apps-from-prompt-to-trusted-agentic-analytics/)
- **Mechanics (developer docs):**
  - The app is a custom web interface **packaged in a .twbx**.
  - Agent workflow: check access and tools, `scaffold-data-app`, connect a published data source, write the app, then package and publish (`publish-workbook`, `request-workbook-upload`).
  - Requirements: a Tableau **Cloud** site that supports data apps, a publish-capable role, admin enablement of every REST endpoint the app calls, and an AI client that supports Tableau MCP plus code execution. — [Tableau Extensions API docs: Get Started with Data Apps](https://tableau.github.io/extensions-api/docs/dataapps/trex_dataapp_getstarted/)
  - A Tableau plugin PR tested "real esbuild bundles of React + d3". Apps are enabled per site via "Allow Packages on this Site". — [tableau/tableau-plugin PR #13](https://github.com/tableau/tableau-plugin/pull/13); [PR #8: data-app authoring skill](https://github.com/tableau/tableau-plugin/pull/8)
  - **Conflict on availability:** a search summary of the developer docs says the beta was "available only on Tableau Cloud" as of 7 Nov 2026. The Salesforce blog says apps can publish to Cloud, Server, or Next. — [datastudios.org: Data Apps beta](https://www.datastudios.org/post/tableau-data-apps-beta-claude-chatgpt-mcp-governed-analytics-apps); [Salesforce blog](https://www.salesforce.com/blog/tableau-data-apps-from-prompt-to-trusted-agentic-analytics/)
- **Official Tableau MCP server (`tableau/tableau-mcp`).**
  - **Web Tools** combine the REST API, **VizQL Data Service**, and Metadata API to query curated data models, query and extract data, images, and custom views from published workbooks, work with Pulse metrics, and "manage and administer" the environment.
  - **Desktop Tools** (authoring, added in the **November 2026 / 2026.3.1** release) can create a workbook from a verbal description, model a data source and curate its metadata, apply templates or compliance rules, create viz or dashboard extensions, and run batch compliance checks.
  - **Hosting:** managed service at `https://mcp.tableau.com` on every Tableau Cloud pod; the repo is open source for self-hosting. Some tools are "gated by SKU". — [Tableau MCP docs](https://tableau.github.io/tableau-mcp/docs/intro)
- **Release cadence and new tools** (Oct–Nov 2026; latest v6.0.4 on 9 Nov):
  - New tools: `scaffold-data-app`, `apply-workbook` (refuses applies that would delete sheets), `close-workbook`, `list-saved-datasources`, tools to publish .tds/.tdsx data sources, flow tools with read/write flags, and Knowledge write gating.
  - OAuth scope `tableau:entitlements:read` lets the AI Pulse insight tools confirm a **Tableau+** entitlement.
  - "MCP Apps tools can be enabled for claude.ai".
  - The `scaffold-data-app` version floor was lowered to 2026.3.0 in v4.26.3. — [tableau-mcp releases](https://github.com/tableau/tableau-mcp/releases)
  - Directory listings put the self-hosted package at npm `@tableau/mcp-server` (also Docker), with personal-access-token auth for Server. — [mcpservers.org listing](https://mcpservers.org/en/servers/tableau/tableau-mcp); [GitHub repo](https://github.com/tableau/tableau-mcp)
- **Tableau Studio.** Introduced at Dreamforce (Oct 2026) and described as "the next evolution of Tableau Desktop". It is Desktop with a built-in agent that builds worksheets, calcs, formatting, and dashboards from prompts, and it adds "Workspace Extensions". The authors warn that vibe-coded extensions are "unmaintained, unreviewed, and unowned", and that their own AI-generated network chart was "far from perfect". — [Ladataviz newsletter, 15 Oct 2026](https://newsletter.ladataviz.com/p/the-end-of-tableau-desktop)
  - Tableau says Studio is **built on Tableau Desktop** (download the latest Desktop and activate Studio). The agentic features require a **Creator license with Tableau+, Cloud+, or Server+** (or a Cloud trial). "Tableau Desktop is available to everyone." — [Tableau Studio product page](https://www.tableau.com/products/tableau-studio)
  - November 2026 release notes (via search summary) say Studio is GA in Cloud and Server in November 2026, and help docs (via search summary) cite Desktop 2026.3.1+. — [Tableau new features](https://www.tableau.com/products/new-features). Tableau AI Utilities says Studio adds "a harness for Tableau MCP authoring skills to Tableau Agent". — [Tableau AI Utilities](https://tableau.github.io/tableau-ai-utilities/)

#### Anthropic Claude Data (formerly Claude Dashboards)
- Claude Dashboards launched in beta in October 2026 and became **generally available as "Claude Data" on 10 Nov 2026**.
  - **Data sources:** connects to warehouses or CRMs via connectors; the page names **Amazon Redshift**, BigQuery, and ClickHouse.
  - **Features:** dashboards, data stories, and narrated videos. **Certified queries (beta)** let data teams certify metric definitions, which then show a badge. **Live sharing (beta)** lets viewers see current numbers without connector access, and viewers can run only owner-approved queries. Tracked changes allow rollback, and caching and refresh cadence are set per dashboard.
  - **Plans:** all paid plans; on by default for Team, and for Enterprise from 20 Nov 2026.
  - **Partner integrations:** Amplitude, Mixpanel, Omni, PostHog, Sigma, ThoughtSpot (Tableau is not listed). — [Claude resources: Sheets, Whiteboards, Data](https://claude.com/resources/articles/sheets-whiteboards-data); [@claudeai post](https://x.com/claudeai/status/2120206372478566799)
- **Sigma** was a launch partner: "Send to Sigma" turns a Claude dashboard (SQL, tables, columns, filter defaults) into a native Sigma workbook via Sigma's MCP server, with Sigma RLS/CLS applied. It requires a Sigma Build license with "Use Sigma MCP with OAuth". — [Sigma blog: Claude Dashboards](https://www.sigmacomputing.com/blog/claude-dashboards)
- A vendor comparison (Bruin) characterizes Claude dashboards as **artifacts on claude.ai, "not reviewed code"**, with each chart showing its query. — [Bruin, updated 4 Nov 2026 (vendor-authored)](https://getbruin.com/blog/best-ai-dashboard-tools-2026/)

#### Code-first (inspectable) commercial options
- **Evidence (Evidence Studio on top of open-source Evidence Core).** Reports are defined in **SQL + Markdown and live in git**. An AI assistant generates Evidence code from natural language; the product adds schema-aware autocomplete, validation, and previews. — [Evidence Studio blog](https://evidence.dev/blog/evidence-studio); [Evidence Core blog](https://evidence.dev/blog/evidence-core). Evidence Core launched on Product Hunt with the tagline "Build live analytics with coding agents" (launch date not captured). — [Product Hunt: Evidence Core](https://www.producthunt.com/p/evidence/evidence-core)
- **Snowflake Streamlit via CoCo.** Python app code generated from Tableau (see §1). — [Snowflake docs](https://docs.snowflake.com/developer-guide/streamlit/migrations-and-upgrades/tableau-to-streamlit)
- **Hex.** Notebook-based (SQL and Python cells) with shareable apps. — [Bruin comparison](https://getbruin.com/blog/best-ai-dashboard-tools-2026/). Hex Threads can be started from Slack, Claude, Cursor, or Codex and become Hex projects (per search summary). — [Hex changelog](https://learn.hex.tech/changelog/2025-11-19). A third-party Hex MCP server can update SQL and code cells but **not** visualization, Markdown, or input cells. — [mcpservers.org: hex-mcp](https://mcpservers.org/servers/franccesco/hex-mcp)
- **Observable Canvases.** Collaborative browser whiteboards where dashboards are built with UI, code, or AI, including custom JS visualization nodes. Git integration was not confirmed. — [Observable blog: "Dashboards are dead!"](https://observablehq.com/blog/dashboards-in-observable-canvases)

#### Proprietary-object AI BI, and MCP status (mostly vendor or aggregator claims, late 2025 to 2026)
- **Omni.** Its MCP server lets external agents in Cursor and Claude Code query the governed semantic model and "manage Omni content"; its AI dashboard builder can lay out dashboards from prompts. — [Knowi BI-MCP comparison (competitor-authored)](https://www.knowi.com/blog/bi-mcp-comparison/); [Omni article](https://omni.co/articles/best-bi-tools-for-data-visualization-2026-governed-charts-custom-viz-and-ai-dashboard-builders)
- **Sigma.** Has its own MCP server (chat with Sigma Agents from Claude), Claude Dashboards → Sigma, and Migration Skills as a Claude Code plugin. — [Sigma blog](https://www.sigmacomputing.com/blog/claude-dashboards); [Sigma Migrations](https://www.sigmacomputing.com/product/migrations)
- **ThoughtSpot.** Hosted agentic MCP server. SpotterViz reportedly builds full Liveboards from one prompt; Spotter MCP access is described as a paid add-on. — [Knowi comparison](https://www.knowi.com/blog/bi-mcp-comparison/)
- **Power BI.** A local "Modeling MCP server" has write access to semantic models, and a "Remote MCP server" queries existing models. Report pages and visuals are built by Copilot, not by MCP. — [Knowi comparison](https://www.knowi.com/blog/bi-mcp-comparison/). Microsoft will end support for standalone Power BI Copilot on web, Teams, and mobile by **end of Jan 2027** and will default Microsoft Copilot to on from **8 Dec 2026**. — [Search summary of Fabric/Power BI 2026 updates](https://community.fabric.microsoft.com/blog/fbc_pbiupdatesblog/power-bi-november-2026-feature-summary/5414287); [Retirement notice](https://community.fabric.microsoft.com/blog/fbc_fabricupdatesblogs/retirement-of-fabric-data-agent-integration-in-copilot-in-power-bi/5328344)
- **Looker + Gemini.** External agents use the Looker API and Google's open-source MCP Toolbox. A Looker-managed MCP server and the Dashboard Agents are **in preview**. — [Knowi comparison](https://www.knowi.com/blog/bi-mcp-comparison/)
- **Databricks AI/BI + Genie.** Proprietary dashboards plus metric views. — [Databricks blog](https://www.databricks.com/blog/bulk-migrate-your-dashboards-aibi-genie-code)
- **Amazon Quick (Quick Sight).** Agentic workspace. Creating dashboards and datasets requires the **Enterprise tier ($40/user/month + $250/account/month)**; Essentials ($3) and Professional ($20) users can only view dashboards. Agent hours are metered per second, with Enterprise overage at $3/agent-hour. — [AWS Quick pricing page](https://aws.amazon.com/quick/pricing/)
- **Basedash, Dot, Tableau Pulse:** classified by Bruin as proprietary. — [Bruin comparison](https://getbruin.com/blog/best-ai-dashboard-tools-2026/)

#### Tableau → code conversions (non-commercial, pointers only)
- No tool converts .twb directly into React or HTML. Adjacent pieces exist:
  - An MCP server that downloads workbooks via the REST API and extracts the XML for Claude. — [GitHub: quinnschris/Tableau-Workbook-MCP](https://github.com/quinnschris/Tableau-Workbook-MCP)
  - An MCP server for authoring and publishing .twbx/.tdsx on Tableau Cloud using REST + Hyper API + Document API. — [GitHub: SebAustin/tableau-mcp-publish](https://github.com/SebAustin/tableau-mcp-publish)
  - A Claude Code plugin that turns a request into an HTML mock plus a .twbx. — [GitHub: tableau-dashboard-creator-skill](https://github.com/laviDrori0702/tableau-dashboard-creator-skill)
  - An experiment concluding that LLM-generated .twbx is only "conditionally possible". — [Zenn article](https://zenn.dev/laiken/articles/e939fce210f36f?locale=en)
- Tableau's official Embedding API ships React components, but these embed live Tableau views rather than converting logic to code. — [Tableau Embedding API React docs](https://help.tableau.com/current/api/embedding_api/en-us/docs/embedding_api_react.html)

### Inferences
- **Tableau has adopted the TabFree authoring thesis** (an AI coding agent writes HTML/JS dashboards that run on a governed server through MCP), but as a premium, Tableau-locked feature: Data Apps run on Tableau's query and permission stack, and Studio's agent features need Creator on Tableau+/Cloud+/Server+. TabFree's differentiation therefore narrows to **free/open-source, self-hosted on AWS, no Tableau license, open data formats, and Athena/Redshift-native querying**.
- **Claude Data validates demand for Claude-authored dashboards on Redshift**, with certified queries and live sharing, and it may become the "good enough" option for teams that don't need Tableau-Server-style governance or self-hosting. Its partner list (Sigma, Omni, ThoughtSpot, not Tableau) suggests Anthropic is not positioning it as a Tableau migration path.
- **Inspectable-code outputs are rare.** Only Evidence (Markdown+SQL), Hex and Observable (cells), Streamlit (Python), and Tableau Data Apps (JS inside .twbx) produce code a reviewer can diff. Of these, only Tableau Data Apps ships server-side governance comparable to Tableau Server, and it still requires Tableau.
- **MCP coverage is broad, but authoring through MCP is uneven.** Tableau (Desktop Tools, Data Apps), Omni ("manage content"), and Power BI (semantic-model writes) go furthest. Others are mostly query-only, or preview (Looker).

### Gaps
- I could not open Tableau's own Studio blog post (HTTP 403). Studio's Server availability and the exact version (Nov 2026 release vs Desktop 2026.3.1) conflict across sources.
- Whether Tableau Data Apps are available on **Tableau Server** (not just Cloud) as of 20 Nov 2026 is unresolved, as is Data Apps licensing.
- I found no source for the internal format of Claude Data dashboards, or whether they can be exported as code.
- Not researched in depth: Fabi.ai, Julius, Zenlytic, Basedash MCP details, Observable git support.
- Most MCP capability claims for Omni, ThoughtSpot, Power BI, and Looker come from a competitor-authored comparison (Knowi), not vendor docs.

---

## 3. Tableau's own direction (2025–2026) and pricing

### Takeaway
Tableau is moving to an agentic, Salesforce-centric stack: Tableau Next on Data 360 (launched April 2025), Tableau Semantics, Pulse, Tableau Agent, Tableau Studio (Nov 2026), Data Apps (Nov 2026), and an official MCP server that gained authoring tools in Nov 2026. **Tableau Server is not being retired.** It returns to a four-month release cadence from 2026.3 (Oct 2026), and each release keeps a two-year maintenance window. However, AI features land on Cloud first and are gated to premium editions (Tableau+, Cloud+, Server+). Secondary sources report Server customers must bring their own LLM. Per a secondary source citing tableau.com on 8 Nov 2026, list prices are $75/$42/$15 (Standard) and $115/$70/$35 (Enterprise) per user/month for Creator/Explorer/Viewer. Premium bundles are quote-only, and Tableau Next starts at $40/user/month.

### Cited Findings

#### Product direction
- **Tableau Next.** Launched 17 April 2025 with the Tableau+ SKU; **Tableau Semantics GA** at launch. Concierge, Data Pro, Inspector, and Internal Marketplace were slated for GA between June and late 2025. — [IT Brief UK, 17 Apr 2025](https://itbrief.co.uk/story/salesforce-launches-tableau-next-to-automate-data-analysis); [Salesforce Ben](https://www.salesforceben.com/salesforce-introduces-agentic-analytics-in-tableau-to-solve-rising-data-issues/). Data Pro reached GA in the August 2025 release. — [Tableau Aug 2025 release](https://www.tableau.com/2025-2-august-features). Release notes list "Metrics MCP" and Team Orgs as GA in Tableau Next. — [Tableau 2026.2 features](https://www.tableau.com/2026-2-features)
- **Tableau Next pricing and packaging** (salesforce.com, as of Nov 2026):
  - Starts at **$40/user/month**, billed annually. Roles are **Consumer** and **Creator**. Includes Tableau Agent, Tableau Semantics, and Slack integration.
  - Pricing is now "role-based, eliminating consumption-based pricing (like Flex credits) for data queries, data transforms, and agentic analytics", but "Data Services Credits and/or Flex Credits may still apply" (change date not stated).
  - Every purchaser gets a Salesforce org and a **Data 360** instance; a CRM is not required. Annual contract only.
  - Tableau Next with Tableau Agent is bundled in Sales, Service, and Industry Cloud Core/Advanced/Max editions and Agentforce add-ons. The **Tableau+ Bundle** (Tableau Next + Cloud+) is "Contact Sales". — [Salesforce: Tableau Next pricing](https://www.salesforce.com/analytics/pricing/tableau-next/)
- **Tableau MCP, Data Apps, and Studio:** see §2. — [Tableau MCP docs](https://tableau.github.io/tableau-mcp/docs/intro); [Salesforce blog 3 Nov 2026](https://www.salesforce.com/blog/tableau-data-apps-from-prompt-to-trusted-agentic-analytics/); [Tableau Studio page](https://www.tableau.com/products/tableau-studio)
- **VizQL Data Service (VDS).** A programmatic query API over published data sources, available on Tableau Cloud and on **Tableau Server 2025.1 or later**. — [Tableau Server help: VizQL Data Service](https://help.tableau.com/current/server/en-us/server_process_vizql-data-service.htm); [VDS introduction](https://help.tableau.com/current/api/vizql-data-service/en-us/). In October 2024 Tableau said it was building VDS toward GA for both Cloud and Server. — [Tableau blog, 16 Oct 2024](https://www.tableau.com/blog/use-vizql-data-service-tableau-cloud-site). Tableau MCP's data tools sit on VDS, which must be enabled on Server. — [Tableau MCP docs](https://tableau.github.io/tableau-mcp/docs/intro)
- **Tableau Agent on Server.** A help page documents turning on AI for a Server site. — [Tableau Server help: Turn on AI](https://help.tableau.com/current/server-linux/en-us/sites_gai_server.htm). A secondary source says **Server customers must supply their own LLM** for Tableau Agent, a cost "never on a Tableau invoice". — [datastudios.org, as of 8 Nov 2026](https://www.datastudios.org/post/tableau-pricing-creator-explorer-viewer-standard-enterprise-tableau-plus)

#### Server vs Cloud roadmap
- **No Server end-of-life or end-of-sale announcement was found.** Starting with 2026.3 (October 2026), Tableau Server is "returning to shipping major releases every four months". Technical support for Server and Desktop continues for the same period as maintenance. — [Tableau blog: release cadence](https://www.tableau.com/blog/tableau-updates-product-release-cadence)
- Tableau's support pages list per-release dates. For **2025.3** they show 2 Dec 2025, 2 Dec 2027, and 1 Dec 2028 (presumably release / end of maintenance / end of extended support; column labels not captured). 2026.3 shipped 23 Oct 2026 (per search summary). — [Tableau support services](https://www.tableau.com/support/services); [Tableau Server releases](https://www.tableau.com/support/releases/server)
- Third-party guides say new features reach Cloud 1–3 quarters before Server. Server items on the 2026 roadmap include SCIM and MCP-related features. — [Thinklytics: Server→Cloud 2026](https://thinklytics.com/insights/tableau-cloud-migration-2026); [Luminary roadmap summary (secondary)](https://luminary-analytics.vercel.app/blog/tc26-roadmap)
- InterWorks argues that Gartner's references to "Tableau" blur Server, Cloud, and Next, "each carrying distinct capabilities, pricing models and AI features", and that the vision "requires substantial investment across the Salesforce ecosystem, particularly in Data Cloud". — [InterWorks, 20 Jun 2025](https://interworks.com/blog/2025/06/20/the-2025-gartner-magic-quadrant-what-they-got-right-and-whats-next/)

#### Pricing (2025–2026)
- **List prices** (Cloud and Server per-role pricing is reported as identical; USD per user/month, billed annually; as listed on tableau.com on **8 Nov 2026** per a secondary source):

  | Role | Standard | Enterprise |
  |---|---|---|
  | Creator | $75 | $115 |
  | Explorer | $42 | $70 |
  | Viewer | $15 | $35 |

  - Cloud+ (Tableau Agent, Tableau Knowledge, Pulse, Premier Success, release previews), the Tableau+ Bundle (Cloud+ plus Tableau Next, with "250K credits"), and **Server+** (compute-based, sold in **8-core units**) are all quote-only.
  - At least one Creator is required; annual contracts only. — [datastudios.org](https://www.datastudios.org/post/tableau-pricing-creator-explorer-viewer-standard-enterprise-tableau-plus)
  - The same Standard and Enterprise figures appear in other 2026 guides. — [GetDot](https://www.getdot.ai/blog/tableau-pricing); [Noomaro](https://noomaro.com/articles/tableau-pricing-explained)
  - **Conflict:** another source says that as of August 2026 Tableau's pricing pages showed only "starting at" prices, with Creator and Explorer no longer listed. — [AgentSQL](https://agentsql.com/tableau-pricing)
- **Capacity-based Viewer licensing (Tableau Cloud, from July 2026):** unlimited Viewers via purchased capacity blocks (unpublished price), available on Standard, Enterprise, Cloud+, and Tableau+. It cannot be mixed with role-based Viewers. — [datastudios.org](https://www.datastudios.org/post/tableau-pricing-creator-explorer-viewer-standard-enterprise-tableau-plus); [GetDot](https://www.getdot.ai/blog/tableau-pricing)
- **No 2025–2026 Tableau-specific list-price increase was found.** Salesforce raised list prices an average of **6% on 1 Aug 2025**, but only for Enterprise/Unlimited editions of Sales, Service, Field Service, and select Industry Clouds; none of the coverage says Tableau list prices rose. — [Salesforce pricing update 2025](https://www.salesforce.com/news/stories/pricing-update-2025/); [Salesforce Ben](https://www.salesforceben.com/salesforce-announces-6-pricing-increase-and-unlimited-agentforce-licenses/)
- G2 describes the Tableau+ Bundle as sitting on top of Tableau Enterprise and adding Tableau Next, Tableau Agent, and premium Pulse features. This conflicts with Tableau's "Cloud+ plus Next" description. — [G2 Tableau pricing](https://www.g2.com/products/tableau/pricing); [Salesforce: Tableau Next pricing](https://www.salesforce.com/analytics/pricing/tableau-next/)

#### Analyst standing
- Tableau's own page says it was named a **Leader for the 13th year** in the June 2025 Gartner ABI Magic Quadrant (listed as "Salesforce (Tableau)"). — [Tableau Gartner page](https://www.tableau.com/it-it/gartner-magic-quadrant); report date 16 Jun 2025 — [Gartner abstract](https://www.gartner.com/doc/6576602). Per CX Today, Gartner credited Salesforce's data-prep tooling and automated insights; Microsoft remained the top Leader; Sigma was the only new entrant. — [CX Today](https://www.cxtoday.com/customer-data-platform/gartner-magic-quadrant-for-analytics-and-business-intelligence-abi-platforms-2025-the-rundown/)
- 2026 edition: Tableau says it is a Leader for the 14th consecutive year. Concord's review credits community, deployment flexibility, and a semantic layer "evolving into a composable knowledge graph". — [Tableau NL page](https://www.tableau.com/nl-nl/asset/gartner-magic-quadrant-2024); [Concord](https://www.concordusa.com/blog/what-gartners-2026-magic-quadrant-for-abi-tells-us-about-the-tools-we-work-in-every-day)

### Inferences
- For TabFree's target users (Tableau **Server** customers), the gap is widening: AI features ship to Cloud first, need premium SKUs and, on Server, bring-your-own LLM. That strengthens the "self-hosted + AI-native + free" pitch, but the claim rests partly on secondary sources.
- Because Server is still actively maintained (four-month releases, two-year maintenance windows), there is **no forced-migration event** like an EOL. TabFree adoption will be driven by cost and AI gaps, not deadlines.
- Tableau MCP is open source and works with Tableau Server through VDS, the Metadata API, and REST. TabFree could use it, or the same APIs, as its **migration-extraction front end**: workbook download, data source metadata, permissions, schedules.

### Gaps
- tableau.com/pricing returned HTTP 403 to my fetch, so I could not confirm list prices from a primary source or resolve the conflicting reports about whether Creator and Explorer prices are still displayed. Historical Server-vs-Cloud price differences (whether Server Explorer and Viewer were once cheaper) could not be verified.
- No public Server+ or capacity-block prices, and no Tableau+ price.
- No formal VDS GA announcement date was found. No Pulse GA date was found.
- Exact column meanings in the Server support-dates table were not captured.

---

## 4. Why organizations migrate off Tableau, and how big the "Tableau-exit" market is

### Takeaway
The strongest primary signal is Salesforce itself: CFO Robin Washington cited "increased softness in Tableau bookings and renewals" on the Q1 FY27 call (May 2026), and analysts flagged slower Tableau growth. The commonly cited reasons for leaving are per-seat cost (especially the 53% Creator premium for Enterprise), consolidation onto Microsoft Fabric/Power BI, Salesforce/Data Cloud bundling pressure, and AI features gated to premium SKUs. Nearly all "why we left" material is published by migration vendors or competitors. I found **no independent survey** quantifying churn reasons and **no credible market-size estimate** for Tableau exits.

### Cited Findings
- **Salesforce Q1 FY27 earnings (call ~27 May 2026; article 28 May 2026).** CFO and COO Robin Washington cited "increased softness in Tableau bookings and renewals" as a partial offset to Q2 and FY27 guidance. Analysts flagged slower Tableau growth. — [CIO Dive](https://ciodive.com/news/salesforce-agentforce-earnings-saaspocalypse/821413/); [S&P Global Market Intelligence, 3 Jun 2026](https://www.spglobal.com/market-intelligence/en/news-insights/research/2026/06/salesforce-postq-strong-earnings-overshadowed-by-cautious-outlook). Salesforce does not break out Tableau revenue in the coverage found. Q2 FY27 (26 Aug 2026) raised overall FY27 guidance. — [Salesforce Q2 FY27 press release (PDF)](https://s205.q4cdn.com/626266368/files/doc_financials/2027/q2/CRM-Q2-FY27-Earnings-Press-Release.pdf)
- **Cost per seat.** The move from Standard to Enterprise raises Creator by about 53% and roughly doubles Explorer and Viewer. — [Noomaro](https://noomaro.com/articles/tableau-pricing-explained); [Definite (vendor blog)](https://www.definite.app/blog/best-tableau-alternatives). Comparison sites list Power BI paid plans from about $10/user/month (sources inconsistent: $10 vs $14). — [CostBench](https://costbench.com/compare/tableau-vs-power-bi/); [Tech-Insider](https://tech-insider.org/tableau-vs-power-bi-2026/)
- **Renewal uplifts (unverified).** Two migration-vendor guides claim multi-year Tableau renewals carry 10–25% annual increases, or 20–40% at renewal. The figures conflict, are unsourced, and I could not confirm which guide states which range. — [EPC Group guide](https://www.epcgroup.net/blog/tableau-to-power-bi-migration-enterprise-consolidation-guide-2026); [Lets-Viz guide](https://lets-viz.com/blogs/tableau-to-power-bi-migration-guide)
- **Anecdotal case (vendor-linked).** A 500-seat enterprise faced a $380,000 Tableau Enterprise renewal quote, spent about $95,000 one-time on migration, and reports about $200K/year savings. Enterprise agreements reportedly auto-renew with 60–90-day cancellation windows. — [Beyond the Analytics](https://beyondtheanalytics.com/blog/enterprise-tableau-to-power-bi-migration-case-study-2026)
- **Salesforce gravity (opinion).** Guides argue Tableau has been "pulled toward Salesforce CRM, Data Cloud, and Einstein AI", so non-Salesforce shops "subsidize features they won't use". This is opinion, not measured. — [Definite](https://www.definite.app/blog/best-tableau-alternatives); [Numlytics](https://numlytics.com/blog/tableau-to-power-bi-business-case/). This is consistent with Tableau Next requiring a Salesforce org and Data 360 instance. — [Salesforce: Tableau Next pricing](https://www.salesforce.com/analytics/pricing/tableau-next/). InterWorks notes the Gartner-described vision requires substantial Data Cloud investment. — [InterWorks](https://interworks.com/blog/2025/06/20/the-2025-gartner-magic-quadrant-what-they-got-right-and-whats-next/)
- **AI gating.** Studio's agent features need Creator on Tableau+/Cloud+/Server+ — [Tableau Studio](https://www.tableau.com/products/tableau-studio). Server customers reportedly bring their own LLM — [datastudios.org](https://www.datastudios.org/post/tableau-pricing-creator-explorer-viewer-standard-enterprise-tableau-plus)
- **Counter-signal: Tableau's capability is still valued.** Tableau remains a Gartner Leader (2025 and 2026). — [Tableau](https://www.tableau.com/it-it/gartner-magic-quadrant); [Concord](https://www.concordusa.com/blog/what-gartners-2026-magic-quadrant-for-abi-tells-us-about-the-tools-we-work-in-every-day). Comparisons still rate Tableau superior for complex visual and exploratory analysis. — [CostBench](https://costbench.com/compare/tableau-vs-power-bi/)
- **Migration effort benchmarks (vendor).** 1.5–3 months for 20–50 dashboards; 6–12 months for 150–500 dashboards. — [Lets-Viz](https://lets-viz.com/blogs/tableau-to-power-bi-migration-guide)
- **Data residency and sovereignty.** I found no 2024–2026 source that names data residency as a top driver for leaving Tableau. Third-party guides note Server remains relevant in strictly regulated environments. — [Thinklytics](https://thinklytics.com/insights/tableau-cloud-migration-2026)

### Inferences
- The density of the vendor ecosystem is itself evidence of a material exit market: more than ten Power BI converters, plus AWS, Databricks, Snowflake, Sigma, and Preset all shipping Tableau-specific converters in 2026. So is Salesforce's acknowledgment of weaker Tableau renewals. Neither gives a dollar size.
- The most likely TabFree adopters are self-hosted **Tableau Server** shops on AWS, with large Viewer populations and non-Salesforce CRMs. They feel per-seat cost and the AI premium most, and existing converters (which push to Fabric, Quick Sight, Databricks, Snowflake, or Sigma) do not serve customers who want to stay self-hosted and vendor-neutral.

### Gaps
- No independent survey (Gartner Peer Insights, BARC BI Survey, Dresner) quantifying reasons for leaving Tableau was found in this session.
- No market-size estimate for Tableau migrations or exits. Salesforce does not disclose Tableau revenue in the coverage found.
- The Enlyft switching statistic is unverified (see §1 Gaps).

---

## 5. Community signals (HN, Reddit, Product Hunt), 2024–2026: did anyone launch something close to TabFree?

### Takeaway
I found **no commercial launch or Show HN/Product Hunt post** for a free, self-hosted Tableau Server + Desktop replacement that migrates workbooks, .hyper extracts, and permissions and authors HTML dashboards with Claude Code. The visible 2025–2026 launches are adjacent: Evidence Core ("build live analytics with coding agents"), Claude Dashboards/Data, and vendor migration skills. My tools could not reach Reddit, and Hacker News results were mostly pre-2024, so community coverage is a significant gap.

### Cited Findings
- **Hacker News.** Searches restricted to news.ycombinator.com returned mostly older threads. Representative examples: "Ask HN: Anything better than Tableau…" and "Alternatives to Tableau?", where users recommended Superset, Metabase, Redash, Grafana, Power BI, Looker, and Streamlit/Dash. — [HN 10319777](https://news.ycombinator.com/item?id=10319777); [HN 15028965](https://news.ycombinator.com/item?id=15028965). The only recent-looking item is "A simple way to explore data through a Tableau-like UI directly in your data app" (date not captured). — [HN 42549148](https://news.ycombinator.com/item?id=42549148)
- **Product Hunt.** Evidence Core launched as "Build live analytics with coding agents" (date not captured). — [Product Hunt](https://www.producthunt.com/p/evidence/evidence-core)
- **Open-source alternative listicles (2026)** name Superset, Metabase, Lightdash, Grafana, Helical Insight, WrenAI, and DataEase. None advertises Tableau workbook import. — [Holistics: best open-source BI tools 2026](https://www.holistics.io/blog/best-open-source-bi-tools/); [OpenSourceProjects.cc](https://opensourceprojects.cc/alternatives/tableau); [Helical Insight](https://helicaltech.com/beyond-dashboards-a-free-open-source-tableau-alternative-for-ai-powered-business-intelligence/)
- **Closest artifacts (GitHub, flagged for the GitHub researcher):** Tableau-Workbook-MCP (REST download + XML extraction for Claude), tableau-mcp-publish (author and publish .twbx/.tdsx via REST, Hyper API, and Document API), tableau-dashboard-creator-skill (HTML mock + .twbx), Rittman's twb_to_lookml, Preset's tableau-to-preset skill, and Sigma's migration skills. — [Tableau-Workbook-MCP](https://github.com/quinnschris/Tableau-Workbook-MCP); [tableau-mcp-publish](https://github.com/SebAustin/tableau-mcp-publish); [tableau-dashboard-creator-skill](https://github.com/laviDrori0702/tableau-dashboard-creator-skill); [Rittman](https://docs.rittmananalytics.com/en/latest/docs/tutorials/tableau-to-looker/); [Preset skill](https://skillsmp.com/creators/preset-io/agent-skills/plugins-preset-mcp-skills-skills-tableau-to-preset); [Sigma](https://www.sigmacomputing.com/product/migrations)

### Inferences
- The pattern of "Claude Code + skills + MCP to migrate Tableau" is now **common across vendors** (Sigma, Preset, Snowflake CoCo, Databricks Genie Code, Tableau's own plugin). TabFree's novelty lies in the **destination** (an open, self-hosted Tableau-Server-equivalent on AWS with HTML dashboards and open data formats), not in the migration technique.

### Gaps
- **Reddit was inaccessible** to my search tool (domain blocked). I could not review r/tableau, r/BusinessIntelligence, r/dataengineering, or r/PowerBI threads from 2024–2026. A human should check them directly.
- HN Algolia and Product Hunt search pages were not queried directly. Launch dates for the HN and Product Hunt items above were not captured.

---

## 6. Verdict: does any commercial product or service already deliver the TabFree concept?

### Takeaway
**No.** As of 20 Nov 2026, no commercial product or service combines TabFree's elements: free/open-source; self-hosted on AWS; replaces both Tableau Server and Desktop; migrates *every* workbook, data source, and permission from an existing Tableau Server; converts .twb/.twbx and **.hyper** extracts to open formats; queries Athena/Redshift natively; and authors **HTML dashboards via Claude Code through a built-in MCP server**. Each element exists somewhere, but always attached to a proprietary destination. The closest partial overlaps are listed below. TabFree should **reuse rather than reinvent** where it can: Tableau's open-source MCP and REST/VDS APIs for extraction, Snowflake's and Databricks' published conversion patterns as a fidelity checklist, and vendor-style parity-check workflows.

### Cited Findings
Closest partial overlaps, ranked by conceptual similarity:

| Offering (status/date) | Overlap with TabFree | Missing vs TabFree | Source |
|---|---|---|---|
| **Tableau Data Apps + Tableau MCP + Studio** (Data Apps blog 3 Nov 2026; beta; MCP authoring tools Nov 2026) | Claude/ChatGPT/Codex author custom HTML/JS (React/d3) apps via MCP; hosted with Tableau permissions/RLS | Requires paid Tableau (Studio agentic = Creator on Tableau+/Cloud+/Server+); Data Apps beta reportedly Cloud-only; stays on Tableau; not free/OSS | [Salesforce blog](https://www.salesforce.com/blog/tableau-data-apps-from-prompt-to-trusted-agentic-analytics/); [Data Apps docs](https://tableau.github.io/extensions-api/docs/dataapps/trex_dataapp_getstarted/); [Tableau Studio](https://www.tableau.com/products/tableau-studio) |
| **Snowflake CoCo Tableau→Streamlit** (Preview) | Converts workbooks to **code** (Python); translates LOD, table calcs, sets, parameters, filter/highlight actions | Snowflake-only; doesn't read .hyper; no Server objects (permissions, schedules); Streamlit not HTML | [Snowflake docs](https://docs.snowflake.com/developer-guide/streamlit/migrations-and-upgrades/tableau-to-streamlit) |
| **AWS Transform BI agents (Wavicle, now Deloitte)** (1 May 2026) | Runs in customer's AWS account; Tableau → AWS-native BI; assessment + conversion | Target is proprietary Quick Sight (dashboard creation needs $40/user + $250/acct Enterprise); scope stated as datasets/calcs/visuals/filters only; price unpublished | [Amazon Quick Community](https://community.amazonquick.com/t/aws-transform-now-offers-bi-migration-agents-for-power-bi-and-tableau-to-amazon-quick/52292); [AWS Quick pricing](https://aws.amazon.com/quick/pricing/); [Deloitte](https://www.deloitte.com/us/en/about/press-room/deloitte-aquires-the-business-of-wavicle-a-top-tier-data-and-ai-engineering-organization.html) |
| **Databricks Genie Code bulk import** (GA, 19 Nov 2026) | Bulk agentic conversion of .twb/.twbx/.tds/.tdsx into dashboards + semantic metric views | Databricks/Unity Catalog lock-in; proprietary dashboards; no permission migration stated | [Databricks blog](https://www.databricks.com/blog/bulk-migrate-your-dashboards-aibi-genie-code) |
| **Sigma Migration Skills** (beta) | Open-source **Claude Code plugin** that migrates Tableau with parity checks; handles extract-only data via a bridge | Destination is Sigma SaaS on Snowflake/Databricks; permissions not mentioned | [Sigma Migrations](https://www.sigmacomputing.com/product/migrations) |
| **Claude Data** (GA 10 Nov 2026) | Claude-authored live dashboards on Redshift et al.; certified queries; live sharing (beta) | SaaS on claude.ai; not self-hosted; no Tableau migration; not inspectable code per one vendor review | [Claude resources](https://claude.com/resources/articles/sheets-whiteboards-data); [Bruin](https://getbruin.com/blog/best-ai-dashboard-tools-2026/) |
| **Pulse Convert** (GA ~May 2026, unverified) | Reads .twb XML + Tableau Server repository/Metadata API; claims to migrate access roles, refresh schedules, dataset permissions | Target is Power BI; proprietary; vendor-claimed 75–90% accuracy | [Office Solution](https://innovationalofficesolution.com/Tableau-To-PowerBI/) |
| **Preset tableau-to-preset skill** | Agent converts .twb → Superset-compatible dashboards via MCP | Targets Preset (managed Superset); skill-level, not a platform migration | [skillsmp](https://skillsmp.com/creators/preset-io/agent-skills/plugins-preset-mcp-skills-skills-tableau-to-preset) |

### Inferences
- **The white space that remains:**
  1. A **vendor-neutral, self-hosted destination** with Tableau-Server-like governance (sites, projects, groups, permissions, schedules, subscriptions).
  2. **.hyper → open-format** conversion (Parquet/Iceberg on S3) as a first-class feature.
  3. **Athena-native** querying. No converter names Athena; AWS's path goes to Quick Sight.
  4. **Dashboards as reviewable HTML/JS code in git**, editable by Claude Code via MCP, *outside* Tableau.
- **Main competitive threats to TabFree's value proposition:**
  - Tableau itself absorbing the "AI-authored HTML apps" idea (Data Apps).
  - Anthropic's Claude Data covering lightweight dashboarding on Redshift.
  - Hyperscalers and warehouses offering free or subsidized migration to their own BI.
- **Build-vs-reuse guidance from the landscape:**
  - Reuse Tableau's open-source MCP and its REST, Metadata, and VDS APIs (and the Hyper API) for extraction.
  - Emulate the published translation matrices and "blocked function" lists (Snowflake) and the parity-check gates (Sigma, Databricks).
  - Expect roughly 70–90% automated fidelity at best, with manual follow-up for SCRIPT_/MODEL_ functions, complex table calcs, dynamic titles, and layout.

### Gaps
- Not verified: whether any large SI (Accenture, Deloitte, TCS, Infosys, Cognizant, LTIMindtree) sells a packaged "Tableau → open-source/self-hosted on AWS" offering. None surfaced in searches.
- Not verified: whether Tableau Data Apps run on Tableau **Server** today, which matters for how directly Tableau competes with TabFree's self-hosted pitch.
- Not verified: whether AWS Transform agents convert LOD expressions, parameters, actions, or permissions. Not stated in any source reviewed.
