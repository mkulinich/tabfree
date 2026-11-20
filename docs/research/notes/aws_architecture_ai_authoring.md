# TabFree architecture reference: AWS data, semantic and visualization layers, platform services, AI authoring, and licensing

Research notes compiled 2026-11-20. Prices are AWS/Anthropic list prices read on 2026-11-20 for us-east-1 (N. Virginia) unless noted. GitHub stars, licenses and release tags were read with `gh api` on 2026-11-20. Facts sit under "Cited Findings" with inline sources. Recommendations, architecture choices and cost models are my inferences and sit under "Inferences".

---

## 1. Data layer: .hyper conversion, Iceberg vs Parquet, Athena vs Redshift, caching, query APIs, RLS, refresh scheduling

### Takeaway
Convert Tableau extracts with the Tableau Hyper API, which has been Apache-2.0 since July 2024. Its `COPY TO` writes Parquet, or you can stream Arrow. Land the data as Apache Iceberg tables in the AWS Glue Data Catalog and query them with Athena by default. Athena costs $5/TB scanned, nothing when idle, and allows 200 active DML queries in us-east-1. Put a semantic-layer cache and pre-aggregation tier in front of Athena. Athena's own result reuse only matches exact query repeats, and it switches off on tables that use Lake Formation fine-grained access control. Use Redshift Serverless for "live" sources re-pointed to Redshift and for tenants that need high concurrency or sub-second repeat queries. It starts at $1.50/hour active at 4 RPU with a 60-second minimum, and it has a leader-node result cache and auto-refreshing materialized views.

### Cited Findings

#### Converting .hyper → Parquet/Iceberg
- Hyper API release 0.0.19691 (July 4, 2024): "Hyper API is now available under the Apache 2.0 license." — [Hyper API release notes](https://tableau.github.io/hyper-db/docs/releases)
- PyPI metadata for `tableauhyperapi` lists license "Apache-2.0" with the OSI "Apache Software License" classifier. The latest version is 0.0.26963, uploaded 2026-10-17, and the homepage is tableau.github.io/hyper-db — [PyPI tableauhyperapi](https://pypi.org/project/tableauhyperapi/)
- `COPY TO` arrived in Hyper API 0.0.17360 (July 5, 2023), along with experimental Arrow read/write. Arrow read/write became "stable and ready for use in production" in 0.0.17537 (August 2, 2023). Iceberg became an external format in 0.0.15735 (October 5, 2022). — [Hyper API release notes](https://tableau.github.io/hyper-db/docs/releases)
- `COPY TO` copies a table or query result to one or more files, either a local file or an S3 bucket. The format comes from the `FORMAT` option or is inferred from the file extension, and `MAX_FILE_SIZE` splits output across files. The documented examples write Parquet to S3. — [Hyper COPY TO docs](https://tableau.github.io/hyper-db/docs/sql/command/copy_to) (this URL returned 404 on direct fetch on 2026-11-20; content comes from the search index); [Salesforce SQL reference for COPY TO, "Applies to: Tableau Hyper API"](https://developer.salesforce.com/docs/data/data-cloud-query-guide/references/dc-sql-reference/copy-to.html)
- Hyper's external formats include Parquet (versions 1 and 2) and Iceberg. For Iceberg the path must point to the table directory, and versions 1 and 2 are supported but version 3 is not. — [Hyper external formats](https://tableau.github.io/hyper-db/docs/sql/external/formats)
- Geospatial deprecations: the plain `geography` type and the geospatial functions outside the `tableau` namespace were deprecated in 0.0.21408 (February 13, 2025), and some were removed in 0.0.22106 (May 1, 2025). The .NET Hyper API was deprecated in 0.0.17782 (September 6, 2023). — [Hyper API release notes](https://tableau.github.io/hyper-db/docs/releases)
- pantab 5.3.0 is BSD-3-Clause, describes itself as "Read/Write pandas DataFrames with Tableau Hyper Extracts", and requires `pyarrow>=14.0.0`. The repo has 122 stars and was last pushed 2026-04-08. — [PyPI pantab](https://pypi.org/project/pantab/), [innobi/pantab](https://github.com/innobi/pantab)
- Tableau's MIT-licensed samples include a Parquet→Hyper loader built on `COPY ... FROM ... WITH (FORMAT PARQUET)`. It needs the table definition spelled out explicitly. — [hyper-api-samples parquet-to-hyper](https://github.com/tableau/hyper-api-samples/blob/main/Community-Supported/parquet-to-hyper/create_hyper_file_from_parquet.py)
- DuckDB v2.0.0 (MIT) shipped 2026-10-21 — [duckdb/duckdb](https://github.com/duckdb/duckdb). Its Iceberg extension "adds support for reading and writing Iceberg tables… and attaching Iceberg REST catalogs", with write operations including INSERT, CTAS, UPDATE and MERGE. — [duckdb/duckdb-iceberg](https://github.com/duckdb/duckdb-iceberg)
- PyIceberg 0.12.0 (Apache-2.0) released 2026-09-01 — [apache/iceberg-python](https://github.com/apache/iceberg-python)

#### Iceberg on Athena: refresh and maintenance
- `MERGE INTO` "is transactional and is supported only for Apache Iceberg tables in Athena engine version 3". It supports `WHEN MATCHED … DELETE/UPDATE` and `WHEN NOT MATCHED … INSERT`. It raises an error if one target row matches more than one source row. — [Athena MERGE INTO](https://docs.aws.amazon.com/athena/latest/ug/merge-into-statement.html)
- Athena's Iceberg maintenance and optimizer features — [Athena: optimize Iceberg tables](https://docs.aws.amazon.com/athena/latest/ug/querying-iceberg-data-optimization.html):
  - `OPTIMIZE t REWRITE DATA USING BIN_PACK [WHERE …]` compacts files and merges delete files.
  - `VACUUM` expires snapshots and removes orphan files. Retention is set with `vacuum_max_snapshot_age_seconds`.
  - The cost-based optimizer uses Iceberg statistics. They are on by default for S3 Tables; other tables need the `use_iceberg_statistics` property.
  - Parquet page-level column indexes are used through the `use_iceberg_parquet_column_index` property.
- AWS Glue pricing — [AWS Glue pricing](https://aws.amazon.com/glue/pricing/):
  - The Data Catalog table optimizer (compaction) costs $0.44 per DPU-hour, "billed per second with a 1-minute minimum", the same rate as Glue Spark ETL.
  - The Data Catalog's first 1M objects stored and first 1M requests per month are free. After that, objects cost $1.00 per 100,000 per month, and the worked example implies $1 per million requests.
- S3 Tables pricing:
  - AWS cut S3 Tables compaction processing fees "by up to 90%" in July 2025 — [AWS What's New](https://aws.amazon.com/about-aws/whats-new/2025/07/amazon-s3-tables-reduce-compaction-costs)
  - One comparison quotes S3 Tables storage at $0.0265/GB against $0.023/GB for S3 Standard. This is a secondary source; check the S3 pricing page. — [AWS Builder Center comparison](https://builder.aws.com/content/39n7WU54TBsV3OlKwtJsnL54PVL/amazon-s3-tables-vs-self-managed-apache-iceberg-on-s3-a-technical-deep-dive-for-startups)

#### Athena: price, quotas, result reuse, capacity, parameterization
- Athena pricing — [Athena pricing](https://aws.amazon.com/athena/pricing/):
  - SQL queries cost $5 per TB scanned; the page's example is 3 TB = $15.
  - Federated queries are rounded up to the nearest MB with a 10 MB minimum unless Provisioned Capacity is used.
  - The capacity-reservation example uses $0.30 per DPU-hour, and Apache Spark costs $0.35 per DPU-hour.
- Athena default quotas — [Athena endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/athena.html):

  | Quota | Default |
  |---|---|
  | Active DML queries | us-east-1 200; us-east-2, us-west-2, ap-northeast-1, eu-central-1, eu-west-1 150; ap-northeast-2, ap-south-1, ap-southeast-1/2, eu-west-2 100; all other regions 20 |
  | Active DDL queries | 20 |
  | DML timeout | 30 minutes (adjustable) |
  | DDL timeout | 600 minutes |
  | StartQueryExecution rate | 100/s in major regions (20/s elsewhere); burst multiplier 5 in us-east-1 |
  | GetQueryExecution rate | 500/s in major regions |
  | GetQueryResults rate | 100/s |
- More Athena quota rules — [Athena service quotas](https://docs.aws.amazon.com/athena/latest/ug/service-limits.html):
  - The Active DML quota counts both running and queued queries. Going over it returns `TooManyRequestsException`.
  - The DML timeout can be raised to 240 minutes.
  - Queries can be at most 262,144 bytes.
  - Each workgroup can hold 1,000 prepared statements.
  - A single scan reads at most 1 million partitions.
- Query result reuse — [Athena: reuse query results](https://docs.aws.amazon.com/athena/latest/ug/reusing-query-results.html):
  - Reuse is opt-in per query and works only within the same workgroup.
  - Results expire after a default maximum age of 60 minutes; the most you can set is 7 days. Stale results are possible because Athena does not check source data until the maximum age passes.
  - Reuse is not supported for:
    - Lake Formation governed tables, or S3 locations registered with Lake Formation
    - tables with row/column permissions or fine-grained access control
    - federated catalogs
    - queries that touch more than one catalog or more than 20 tables
    - "managed query results"
  - Non-deterministic functions, and `LIMIT` without `ORDER BY`, are not cached.
  - JDBC needs driver 2.0.34.1000 or later; ODBC needs 1.1.19.1002 or later.
- Capacity reservations — [Athena capacity management](https://docs.aws.amazon.com/athena/latest/ug/capacity-management.html):
  - One DPU is about 4 vCPU and 16 GB.
  - Athena assigns DML queries 4–124 DPUs; DDL queries use 4.
  - The minimum reservation is 4 DPUs. An account can have up to 100 reservations and 1,000 DPUs per region.
  - Reserved queries don't count toward DML/DDL quotas. They can wait in a queue for up to 10 hours.
  - One reservation can serve up to 20 workgroups.
  - Capacity requests "are not guaranteed and can take up to 30 minutes".
  - Reservations aren't available in Tel Aviv, UAE, Bahrain or New Zealand.
- Parameterized queries come in two forms: positional `?` execution parameters on StartQueryExecution, or `PREPARE`/`EXECUTE`. They help "prevent SQL injection attacks" and work only for SELECT, INSERT INTO, CTAS and UNLOAD. Named parameters aren't supported, and prepared statements belong to a workgroup. — [Athena parameterized queries](https://docs.aws.amazon.com/athena/latest/ug/querying-with-prepared-statements.html)

#### Redshift Serverless and provisioned
- Redshift pricing — [Redshift pricing](https://aws.amazon.com/redshift/pricing/):
  - Serverless costs $0.375 per RPU-hour, billed per second "with a 60-second minimum charge". Base capacity can be set from 4 to 1024 RPUs.
  - Serverless Reservations save "up to 24%" (1-year) or "up to 45%" (3-year).
  - Redshift Managed Storage costs $0.024/GB-month.
  - Concurrency Scaling includes "up to one hour of free Concurrency Scaling credits per day" per cluster, accruing to 30 hours.
  - Spectrum costs $5/TB scanned with a 10 MB minimum.
  - A provisioned ra3.4xlarge node costs $3.26/hour on demand.
- The 4-RPU minimum launched June 30, 2025; before that the minimum was 8 RPUs — [AWS What's New](https://aws.amazon.com/about-aws/whats-new/2025/06/amazon-redshift-serverless-4-rpu-capacity-option). 4 RPU costs $1.50 per active hour, each RPU has 16 GB, and 4-RPU workgroups support up to 32 TB of managed storage. The 60-second minimum per query "can inflate costs" for sub-second queries. — [AWS Big Data Blog](https://aws.amazon.com/blogs/big-data/amazon-redshift-serverless-at-4-rpus-high-value-analytics-at-low-cost). The 4-RPU option reached 7 more regions in May 2026. — [AWS What's New 2026-05](https://aws.amazon.com/about-aws/whats-new/2026/05/amazon-redshift-serverless-4-rpu-seven-regions)
- Redshift's result cache lives in leader-node memory, is on by default, and is shared across sessions on the same database. Queries on system tables, Spectrum external tables, leader-only queries and over-large results are excluded. A cached result is returned only "if… the data has not changed". — [Redshift performance docs](https://docs.aws.amazon.com/en_us/redshift/latest/dg/c_challenges_achieving_high_performance_queries.html)
- Materialized views support `AUTO REFRESH YES`. Automatic query rewrite uses only up-to-date (fresh) MVs. Automated materialized views (AutoMV) are on by default. — [MV auto rewrite](https://docs.aws.amazon.com/redshift/latest/dg/materialized-view-auto-rewrite.html), [AutoMV](https://docs.aws.amazon.com/redshift/latest/dg/materialized-view-auto-mv.html)
- Redshift Data API — [Redshift Data API](https://docs.aws.amazon.com/redshift/latest/mgmt/data-api.html):
  - No persistent connection is needed. It authenticates with Secrets Manager, temporary IAM credentials or IAM Identity Center.
  - Limits:

    | Limit | Value |
    |---|---|
    | Maximum query duration | 24 hours |
    | Active queries per cluster | 2,000 |
    | Maximum result size | 500 MB (gzip) |
    | Result retention | 24 hours |
    | Maximum statement size | 200 KB |
  - Named parameters replace values only, not column or table names, and can't set NULL.
  - Session reuse: a session lasts up to 24 hours, a cluster or workgroup allows up to 500 sessions, and each session runs one query at a time.
  - `WaitTimeSeconds` long polling waits up to 30 seconds.
  - CSV results come in 1 MB chunks, up to 15 MB per call.
  - Step Functions doesn't retry by default, so you need to use `ClientToken`.
  - The same page notes that Redshift Python UDFs lose support after June 30, 2026.
- Redshift row-level security policies are defined at the object level for users and roles. Policies that refer to lookup tables also scan those tables, which causes "performance differences" against users with no policy attached. — [Redshift RLS](https://docs.aws.amazon.com/redshift/latest/dg/t_rls.html)

#### Lake Formation fine-grained access
- Lake Formation data filters — [Lake Formation data filtering](https://docs.aws.amazon.com/lake-formation/latest/dg/data-filtering.html):
  - A filter with "all columns" and a row expression gives row-level security.
  - Naming columns plus a row expression gives cell-level security.
  - Cell-level security works in all regions with Athena engine v3 or Redshift Spectrum.
- Cell-level security is not supported on nested columns, views or resource links. — [Lake Formation data filtering limitations](https://docs.aws.amazon.com/en_us/lake-formation/latest/dg/data-filtering-notes.html)
- Lake Formation RLS policies work with Athena, Redshift Spectrum, EMR Spark and Glue ETL. They can't attach to multi-dialect views, and they can coexist with data filters. — [Lake Formation RLS policies](https://docs.aws.amazon.com/lake-formation/latest/dg/rls-policies.html); [Redshift Spectrum + Lake Formation](https://docs.aws.amazon.com/redshift/latest/dg/spectrum-lake-formation.md)
- As noted above, Athena result reuse is not supported on Lake Formation governed tables or on tables with row/column filtering. — [Athena: reuse query results](https://docs.aws.amazon.com/athena/latest/ug/reusing-query-results.html)

### Inferences

**Recommended default and alternative per data-layer component**

| Component | Default | Alternative | Why |
|---|---|---|---|
| .hyper conversion | Hyper API (Apache-2.0), Python, in an ECS Fargate task. Run `COPY (SELECT …) TO 's3://…/part.parquet'` with `MAX_FILE_SIZE` to stage Parquet, then load Iceberg with Athena `INSERT INTO`/`MERGE`, or with PyIceberg from Arrow | pantab (BSD-3) → Arrow → DuckDB 2.x or PyIceberg | The Hyper engine reads its own format faithfully (types, collations) and has been Apache-2.0 since 2024. pantab is lighter for Python-only pipelines. DuckDB is useful for local validation and profiling of converted data. |
| Table format | Iceberg (v2) in the Glue Data Catalog, or S3 Tables where managed compaction is wanted | Plain Parquet plus CTAS into a versioned prefix, then repoint a view | Iceberg gives atomic commits, `MERGE` for incremental refresh, snapshots for rollback and `VACUUM`/`OPTIMIZE`. Plain Parquet is simpler for small full-refresh extracts. |
| Query engine for extracts | Athena engine v3 with one workgroup per TabFree site (cost and isolation) | Redshift Serverless (base 4–8 RPU) with Iceberg read through Spectrum or loaded into RMS | Athena has no idle cost and matches "extract" semantics. Redshift wins on concurrency, joins, result cache and MVs. |
| Live sources | Redshift Serverless or provisioned Redshift, already the customer's warehouse, queried through the Data API | Athena federated query for long-tail sources | Matches the brief: live connections are re-pointed to Redshift. |
| Interactivity tier | Semantic-layer pre-aggregations in Cube Store (see section 2), plus a server-side result cache keyed on hash(semantic query, RLS context, data version) | Athena result reuse (exact repeats ≤7 days) and the Redshift result cache | Gets Tableau-like sub-second response for common filter clicks. Athena reuse can't be combined with Lake Formation fine-grained access. |
| Refresh scheduling | EventBridge Scheduler → Step Functions, which run Athena (CTAS/MERGE), call the Redshift Data API with `ClientToken`, and launch ECS tasks for Hyper conversion | EventBridge Scheduler → SQS → TabFree worker | Durable retries, visibility and per-schedule time zones. The SQS path is simpler for self-hosters. |
| RLS | Enforced by the app in the semantic layer (Cube security context and data access policies) | Lake Formation data filters, or Redshift RLS as defense in depth for hard tenant boundaries | App-enforced RLS works the same on Athena and Redshift and keeps result caching possible. Lake Formation fine-grained access switches off Athena result reuse. |

**Athena vs Redshift for interactive dashboards**
- Athena concurrency is the main risk at mid-size scale. Take 1,000 users and 8-tile dashboards. Without caching, a morning burst of about 50 concurrent dashboard opens means 400 simultaneous queries. That is above the 200 active-DML quota in us-east-1, which counts queued queries too. So server-side caching and pre-aggregations are needed, not optional, past a few hundred users.
- Capacity reservations are a poor fit for small deployments. Each DML query takes at least 4 DPUs, so the minimum 4-DPU reservation runs about one query at a time, at $1.20/hour or roughly $876/month if left on 24/7.
- Redshift Serverless bills a 60-second minimum. Sporadic sub-second dashboard queries spread through the day will pay for close to the whole business day of active time. Keep a warm cache or pre-aggregations in front, or use provisioned or reserved capacity once usage is steady.

**Calling the engines: SDK APIs vs JDBC**
- A Node or Python governed-query service should call Athena through the AWS SDK. Use `StartQueryExecution` with `ExecutionParameters`, the per-site `WorkGroup` and `ResultReuseConfiguration`, then poll `GetQueryExecution` and page through `GetQueryResults`.
- Call Redshift through the Data API with named parameters and `WaitTimeSeconds` long polling.
- JDBC adds a JVM dependency. Neither engine needs persistent connections for the governed-query pattern.
- Identifiers such as columns and tables can't be parameterized on either engine. They must come from the semantic model's allow-list, never from LLM- or user-supplied SQL text.

**Converting Tableau extract semantics**
- .hyper files arrive inside `.twbx` and `.tdsx` zip packages.
- Tableau "extract filters" and aggregated extracts become the Iceberg table's SQL definition.
- Incremental refresh keys become the `MERGE` match columns.
- "Hide unused fields" becomes column pruning.
- Geography columns need a decision (WKT/WKB strings or separate lat/long), because the Hyper geography type is partly deprecated.

**Lake Formation vs app-enforced RLS**
- Lake Formation fine-grained access needs a per-end-user identity at query time. That means IAM Identity Center trusted identity propagation, which the Redshift Data API supports, or role-per-group mapping.
- It also turns off Athena result reuse.
- For a BI server whose API service queries on users' behalf, the practical primary control is app-side RLS in the semantic layer. Coarse Lake Formation or IAM grants per site (databases and workgroups) act as a backstop.

### Gaps
- I found no authoritative latency benchmarks, p50 or p95 for small aggregate queries, comparing Athena with Redshift Serverless, and no official documentation of Redshift Serverless "cold" first-query latency after idle. The team should benchmark both engines on representative extracts.
- I could not confirm whether Hyper's `COPY TO` can write Iceberg directly; the docs list Iceberg as an external format, and the examples I found write Parquet. The COPY TO docs page returned 404 on direct fetch.
- The Athena pricing page extract mentioned the 10 MB per-query minimum only for federated queries. It's unclear whether the same minimum applies to ordinary S3 queries.
- I didn't retrieve Step Functions or Lambda pricing.
- I didn't verify whether DuckDB's Iceberg extension can attach to the Glue Iceberg REST endpoint or S3 Tables.
- I didn't verify how Hyper geography and spatial types map to Parquet or Iceberg types.
- I found no documented maximum concurrent queries for Redshift Serverless; the 2,000 figure is the Data API's active-statement limit.

---

## 2. Semantic/query layer: modeling Tableau data sources and generating dialect-correct SQL

### Takeaway
Use Cube Core as the default semantic layer. It is Apache-2.0 by default with some MIT packages, very active (21k stars, v1.8.30 released 2026-11-19), and maintains Athena, Redshift and DuckDB drivers. It stores pre-aggregations in Cube Store (Parquet/Arrow/DataFusion) and supports declarative RLS through a JWT security context and data access policies. Its YAML models suit LLM authoring and review. Pair it with SQLGlot (MIT) in the converter; SQLGlot already has `athena`, `redshift` and `tableau` dialects. Use Apache Ossie, the former Open Semantic Interchange now incubating at the ASF with a Tableau-contributed translator, as the import and export format. The main alternative is dbt MetricFlow, Apache-2.0 since October 2025. Malloy has no Athena or Redshift dialect. Boring Semantic Layer runs on Ibis, which has an Athena backend but no Redshift backend.

### Cited Findings
- Cube's repository license says code is under "Apache License 2.0" by default, with some packages under MIT. The repo has 21,263 stars, was pushed 2026-11-20, and has latest release v1.8.30 (2026-11-19). Its packages include `cubejs-athena-driver`, `cubejs-redshift-driver`, `cubejs-duckdb-driver` and `cubejs-cubestore-driver`. — [cube-js/cube](https://github.com/cube-js/cube)
- Cube Store is a columnar store for pre-aggregations built on Parquet, Arrow and DataFusion. Cube Core v1.6 added a Cube Store upgrade and multi-stage pre-aggregations. — [Cube blog: Core v1.6](https://cube.dev/blog/cube-core-v1-6-cube-store-upgrade-multi-stage-pre-aggregations). Cube Core v1.5 added calendar cubes and the "SQL API over HTTP". This is per the post title; I didn't fetch the body. — [Cube blog: Core v1.5](https://cube.dev/blog/cube-core-v1-5-performance-calendar-cubes-sql-api-over-http)
- On Athena, Cube pre-aggregations build with batching by default, export buckets must be S3, and `count_distinct_approx` works in pre-aggregations. — [Cube docs: AWS Athena](https://docs.cube.dev/admin/connect-to-data/data-sources/aws-athena.md)
- Cube access control — [Cube data access policies](https://cube.dev/docs/product/auth/data-access-policies), [Cube row-level security](https://cube.dev/docs/product/auth/row-level-security), [Cube security context](https://cube.dev/docs/security/context):
  - Cube builds SQL using the JWT claims in the "security context", including row-level constraints.
  - The docs recommend declarative data access policies by default, with `queryRewrite` for special cases.
  - Multiple matching policies combine with OR. Policy row filters and `query_rewrite` filters both apply.
  - Member-level rules combine with `public` member settings using AND.
  - `userAttributes` is Cube Cloud-only; Cube Core uses `securityContext` directly.
  - Catch-all policies (`group: "*"`) grant full access, so they need care.
- dbt Labs relicensed MetricFlow under Apache 2.0 on October 14, 2025, at Coalesce 2025, tying the move to the Open Semantic Interchange. — [dbt Labs blog](https://www.getdbt.com/blog/dbt-labs-affirms-commitment-to-open-semantic-interchange-by-open-sourcing-metricflow). License history: v0–0.140.0 AGPL, 0.150.0–0.208.2 BSL, Apache-2.0 from 0.209.0. The hosted dbt Semantic Layer service and APIs remain proprietary. This comes from a secondary source. — [Atlan explainer](https://atlan.com/know/ai-agent/semantic-layer/metricflow-apache-2-license-dbt-semantic-layer/). The repo is Apache-2.0 with 1,887 stars — [dbt-labs/metricflow](https://github.com/dbt-labs/metricflow). `dbt-semantic-interfaces` is Apache-2.0. — [dbt-labs/dbt-semantic-interfaces](https://github.com/dbt-labs/dbt-semantic-interfaces)
- Malloy is MIT with 2,624 stars. Its dialect directory contains databricks, duckdb, mysql, postgres, snowflake, sqlserver, standardsql and trino; connection packages cover BigQuery, Databricks, DuckDB, MySQL, Postgres, Snowflake, SQL Server and Trino. There is no Athena or Redshift package. — [malloydata/malloy](https://github.com/malloydata/malloy)
- Boring Semantic Layer is MIT with 491 stars. It is "a lightweight semantic layer based on Ibis", described as "MCP-friendly", built jointly by xorq-labs and boringdata, and "freely inspired by" Malloy. — [boringdata/boring-semantic-layer](https://github.com/boringdata/boring-semantic-layer)
- SQLGlot (MIT, 9,808 stars) ships dialect modules including `athena`, `redshift`, `duckdb`, `trino`, `presto`, `postgres`, `tableau`, `dax` and `prql` — [sqlglot/dialects](https://github.com/tobymao/sqlglot/tree/main/sqlglot/dialects). The `tableau` dialect defines `[ ]` bracket identifiers plus its own parser and generator classes. — [sqlglot tableau.py](https://github.com/tobymao/sqlglot/blob/main/sqlglot/dialects/tableau.py)
- Ibis (Apache-2.0, 6,704 stars) has backends including athena, duckdb, trino, postgres, snowflake and others, but no redshift backend directory. — [ibis/backends](https://github.com/ibis-project/ibis/tree/main/ibis/backends)
- Apache Ossie:
  - It is the former Open Semantic Interchange, accepted into the Apache Incubator in June 2026. It describes semantic models in JSON and YAML as an intermediate format, with converters that translate "from individual products to Ossie and back again". Microsoft is building a bidirectional Power BI converter. — [Techzine, Oct 2, 2026](https://www.techzine.eu/news/data-management/144719/microsoft-and-google-to-contribute-to-the-apache-ossie-project/); [CFOtech](https://cfotech.com.au/story/apache-ossie-joins-incubator-to-standardise-metrics)
  - Its repo is Apache-2.0 with 2,763 stars. — [apache/ossie](https://github.com/apache/ossie)
  - Snowflake launched OSI in September 2025 with Salesforce, dbt Labs and others. — [Snowflake blog](https://www.snowflake.com/en/blog/apache-ossie-open-semantic-interchange-incubator/) (403 on direct fetch; per search index)
  - Tableau says it contributed "the Tableau semantic translator". — [Tableau blog, Aug 20, 2026](https://www.tableau.com/blog/alliance-to-apache-next-chapter-for-open-semantics) (403 on direct fetch; per search index)
- Adjacent open-source "BI as code / agentic BI" projects:
  - Rill: Apache-2.0, "The fastest business intelligence tool for humans and agents" — [rilldata/rill](https://github.com/rilldata/rill)
  - Lightdash: MIT plus an `ee` directory under a separate license, "Agentic BI" — [lightdash/lightdash](https://github.com/lightdash/lightdash)
  - Evidence: MIT, "Business intelligence as code" — [evidence-dev/evidence](https://github.com/evidence-dev/evidence)

### Inferences

**Recommendation:** Cube Core as the default; dbt MetricFlow as the alternative.

**Why Cube Core**
- It is the only candidate here with maintained Athena and Redshift drivers.
- It handles pre-aggregation and caching (Cube Store) and declarative row-level access in one OSI-licensed package.
- It exposes REST, GraphQL and SQL (Postgres-wire) APIs, so the TabFree runtime and MCP tools can send semantic queries (measures, dimensions, filters, time grain) instead of SQL. That gives SQL-injection resistance and RLS enforcement for free.
- YAML models are short and diffable, which suits Claude-authored changes and human review.

**Why MetricFlow as the alternative**
- Pick it if the customer already models in dbt.
- It produces multi-dialect SQL, but it has no caching or pre-aggregation store and no RLS. TabFree would have to build those itself.
- Its hosted APIs are proprietary, so TabFree would call the MetricFlow library directly.

**Not recommended as primary engines**
- Malloy lacks Athena and Redshift dialects. It has a Trino dialect, and Athena engine v3 is Trino-derived, but that compatibility is unverified.
- Boring Semantic Layer and Ibis are attractive for Python and MCP prototyping, but Ibis has no Redshift backend.

**Tableau data source → Cube model conversion pipeline**

| Tableau construct | Cube target |
|---|---|
| Physical tables / custom SQL | `cubes` with `sql_table` or `sql` |
| Logical-layer relationships | Cube `joins` with `relationship` |
| Physical joins | Folded into the cube's SQL |
| Measures, dimensions, data types, default aggregations, folders, hierarchies, aliases, formats | Cube members and `meta` |
| Calculated fields | Parse with SQLGlot's `tableau` dialect plus a custom function map (IIF, ZN, DATETRUNC, CONTAINS…), then transpile to athena/redshift SQL expressions |
| FIXED LOD expressions | Separate grouped subquery cubes or joined pre-aggregated dimensions |
| INCLUDE/EXCLUDE LODs and table calculations (RUNNING_SUM, WINDOW_*, RANK) | Window functions or post-query client-side calcs, flagged for human review |
| Parameters | Cube `FILTER_PARAMS` or request parameters |
| User filters (`USERNAME()`, `ISMEMBEROF()`) | Data access policies over `securityContext` (groups, user attributes) |

Each converted model should come with automatic equivalence tests. Compare aggregates computed by Hyper on the original .hyper with Athena on the Iceberg copy, per measure and dimension.

**Apache Ossie as the interchange format**
Treat Ossie as a strategic import/export format rather than the runtime model, since the spec is still incubating. Watch whether the Tableau translator covers classic `.tds`/`.twb` or only Tableau Semantics / Tableau Next models.

### Gaps
- I didn't find documentation of how Cube expresses Tableau-style FIXED/INCLUDE/EXCLUDE LODs; "multi-stage" features exist but I didn't read their semantics.
- I didn't confirm whether Cube Core ships an official MCP server or whether that's Cube Cloud-only.
- I didn't verify how much of the Tableau calculation language SQLGlot's `tableau` dialect covers; the code inspected shows only tokenizer and identifier rules plus parser and generator classes.
- I couldn't read the Ossie spec's coverage of calculations and LODs, or the input formats of the Tableau translator, because the Tableau and Snowflake pages returned 403.

---

## 3. Visualization and front-end: libraries, dashboard DSL vs raw HTML, and security of AI-generated pages

### Takeaway
Have Claude author a TabFree dashboard spec, a governed JSON document, instead of free-form HTML. Within the spec:
- Use Vega-Lite (BSD-3) as the default chart grammar. It is JSON-schema-validated and declares point and interval selections inline for cross-filtering.
- Use ECharts (Apache-2.0) as the alternative and second renderer for chart types and data volumes Vega-Lite handles poorly.
- Use Perspective (Apache-2.0, WASM) for pivot and crosstab tables and TanStack Table (MIT) for plain tables.
- Use MapLibre GL JS (BSD-3) plus deck.gl (MIT) for maps.

Allow custom HTML/JS only as an escape-hatch widget. It renders in a sandboxed iframe with a strict CSP and no network access except a postMessage bridge to the governed query API. That is the same security model MCP Apps standardizes.

### Cited Findings

**Visualization libraries (gh api, 2026-11-20)**

| Library | License | Stars | Latest release / activity | Source |
|---|---|---|---|---|
| Vega-Lite | BSD-3-Clause | 5,563 | v6.5.0 (2026-10-08) | [vega/vega-lite](https://github.com/vega/vega-lite) |
| Apache ECharts | Apache-2.0 | 67,756 | 6.1.0 (2026-05-19) | [apache/echarts](https://github.com/apache/echarts) |
| Observable Plot | ISC | 5,431 | v0.6.19 (2026-11-05) | [observablehq/plot](https://github.com/observablehq/plot) |
| Plotly.js | MIT | 18,384 | pushed 2026-11-20 | [plotly/plotly.js](https://github.com/plotly/plotly.js) |
| D3 | ISC | 113,974 | pushed 2026-11-03 | [d3/d3](https://github.com/d3/d3) |
| AG Grid | MIT (community packages) + Commercial (`ag-grid-enterprise`) | 15,688 | pushed 2026-11-20 | [ag-grid/ag-grid](https://github.com/ag-grid/ag-grid) |
| TanStack Table | MIT | 28,592 | pushed 2026-11-18 | [TanStack/table](https://github.com/TanStack/table) |
| Perspective | Apache-2.0 | 11,616 | v5.9.3 (2026-11-18); repo moved to `perspective-dev` org | [perspective-dev/perspective](https://github.com/perspective-dev/perspective) |
| MapLibre GL JS | BSD-3-Clause | 12,139 | pushed 2026-11-20 | [maplibre/maplibre-gl-js](https://github.com/maplibre/maplibre-gl-js) |
| deck.gl | MIT | 14,783 | pushed 2026-11-20 | [visgl/deck.gl](https://github.com/visgl/deck.gl) |

**Vega-Lite**
- A parameter becomes a selection when `select` is set. The type is `point` or `interval`.
- Interval selections support `translate` (pan) and `zoom`.
- `resolve` is `global`, `union` or `intersect` across views.
- Selections drive "a filter transform, conditional encoding rule, or scale domain".
- Selections projected over aggregated fields work only within their own view.

— [Vega-Lite selection docs](https://vega.github.io/vega-lite/docs/selection.html)

**ECharts**
- `echarts.connect()` links instances by group id or array, synchronizing tooltip, dataZoom and highlight. This comes from a third-party mirror of the API docs. — [ECharts connect (docs mirror)](https://www.mintlify.com/apache/echarts/api/connect)
- Apache's own test page shows linked dataZoom across charts. — [ECharts test: dataZoom-rainfall-connect](https://appsheet-third-party.googlesource.com/echarts/+/refs/heads/main/test/dataZoom-rainfall-connect.html)
- One 2026 practitioner write-up wires brush-based cross-filtering manually. A `brushEnd` event triggers an app-level refetch of the other charts. — [Mergify blog](https://mergify.com/blog/echarts-brush-zoom-shareable-urls)

**Observable Plot**
Its `src/interactions` directory contains only `pointer`, with no built-in brush module. — [observablehq/plot interactions](https://github.com/observablehq/plot/tree/main/src/interactions)

**Perspective**
Perspective is "an open-source data grid, pivot table and charting component for large, real-time and streaming datasets". Its streaming query engine "runs in-browser via WebAssembly or server-side in Python, Node.js and Rust — or delegates to a database". It reads and writes Apache Arrow, CSV and JSON. — [Perspective README](https://github.com/perspective-dev/perspective)

**AG Grid licensing**
The MIT-licensed packages are `ag-grid-community`, `ag-grid-react`, `ag-grid-angular`, `ag-grid-vue3` and the styles/locale packages. `ag-grid-enterprise` is commercially licensed. — [AG Grid LICENSE](https://github.com/ag-grid/ag-grid)

**Evidence on how reliably LLMs generate charts (no head-to-head across all libraries found)**
- The "Drawing Pandas" benchmark found LLMs do well with Matplotlib and Seaborn, "while challenges persist with Plotly". — [arXiv 2412.02764](https://arxiv.org/abs/2412.02764v1)
- A Vega-Lite evaluation on nvBench across six LLMs and eight prompt strategies found "substantial performance disparities across prompt strategies, chart types, and LLMs". — [arXiv 2401.11255](https://arxiv.org/abs/2401.11255v1)
- VisPlotBench evaluates visualization coding agents across eight languages, including Vega-Lite, with execution and multi-round self-debugging. — [arXiv 2510.23642](https://arxiv.org/html/2510.23642v2)

**MCP Apps security model, reusable for dashboards**
- UIs are predeclared `ui://` resources with MIME type `text/html;profile=mcp-app`, rendered by the host in a sandboxed iframe. The spec describes "Mandatory iframe sandboxing with auditable communication".
- Servers declare `csp.connectDomains` (maps to `connect-src`), `resourceDomains` (script, img, style, font and media sources) and `frameDomains`. If `frameDomains` is empty, `frame-src 'none'` applies.
- The "Host MUST construct CSP headers based on declared domains".
- UI-to-host communication uses MCP JSON-RPC over postMessage.

— [MCP Apps spec 2026-01-26](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx), [ext-apps README](https://github.com/modelcontextprotocol/ext-apps)

**Tableau's embedding model (parity target)**
Tableau's connected-apps direct trust JWT works as follows:
- It is HS256 only, with the secret ID in the `kid` header.
- Required claims are `sub`, `aud:"tableau"`, `exp` (at most 10-minute validity), `jti` and `scp` scopes such as `tableau:views:embed`.
- Each app has two secrets for rotation, and secrets don't expire.
- A domain allowlist is "a security best practice".
- Custom JWT claims feed "user attribute functions" for row-level security.

— [Tableau connected apps (direct trust)](https://docs.salesforce.com/tableau/current/en-us/online/connected_apps_direct.md)

### Inferences

**Recommended defaults**
- Chart grammar: Vega-Lite by default, with ECharts as the alternative and second renderer.
- Tables: TanStack Table for simple grids and Perspective for pivots and crosstabs.
- Maps: MapLibre with deck.gl layers for density, hexbin and arc maps.
- Avoid AG Grid Enterprise, because its commercial license conflicts with "free and open source".
- Use Observable Plot only for static small multiples; it has no built-in brushing.

**Why Vega-Lite by default**
- It is pure JSON with a published schema, so specs can be validated, diffed, linted and safely edited by an agent.
- Cross-filtering and parameter interactions are declarative (`params`), so no JS is needed.
- Its grammar is close to Tableau's shelf model (mark plus encodings plus layers), which makes `.twb` view-to-spec conversion more mechanical.

**Why ECharts as the alternative**
- It has broader native chart coverage for Tableau parity, including treemap, sankey, gauge, sunburst, graph, boxplot and custom Gantt, plus canvas performance for large point counts.
- It is the most-starred library.
- Its option objects often embed JS formatter functions. The TabFree spec should forbid functions and allow only string templates.

**DSL vs raw HTML**

| | TabFree JSON dashboard spec (layout grid, tiles, semantic queries, parameters, filters, cross-filter wiring, chart specs) | Raw HTML/JS pages |
|---|---|---|
| Governance | Schema validation, policy checks (only semantic queries, no URLs), deterministic rendering, PDF snapshot parity | Arbitrary code; needs code review and sandboxing; hard to guarantee RLS-correct data flow |
| AI editability | Small JSON patches (Grafana `patch_dashboard`, Metabase operation lists; see section 5) and low token use | Whole-file rewrites and harder targeted edits, though Claude Code is strong at code |
| Consistency | Shared theme, number formats and accessibility | Each page diverges |
| Flexibility | Bounded by the DSL; escape hatch needed | Unlimited |

The recommended hybrid is the spec for 95% of content, with "custom widget" tiles holding sandboxed HTML/JS for the rest.

**Security architecture for AI-generated content**
1. Render dashboards and custom widgets in iframes on a separate origin, such as `usercontent.tabfree.example`, with `sandbox="allow-scripts"` and no `allow-same-origin`. Never put auth tokens or cookies inside the iframe.
2. Set a strict CSP for each iframe: `default-src 'none'`, scripts from the TabFree runtime bundle by hash, `connect-src 'none'`, `frame-src 'none'`. Data arrives only through a postMessage bridge to the parent runtime, mirroring MCP Apps' declared-CSP model.
3. The parent runtime calls the governed query API with semantic query objects. The server authorizes per content item, checks the user's capabilities on the datasource, injects RLS through the Cube security context, caps rows and bytes, and binds values with Athena `?` or Redshift `:name` parameters. Identifiers come only from the model's allow-list.
4. Embedding mirrors Tableau's model: short-lived (≤10 min), single-use (`jti`) JWTs signed by the host app, scopes, a domain allowlist (CSP `frame-ancestors`) and user-attribute claims mapped into the RLS context.

### Gaps
- I found no head-to-head study of LLM reliability for ECharts versus Vega-Lite or Plotly JSON specs. The team should run its own eval, with a spec validity rate, render success and a visual check.
- I didn't build a verified matrix of Tableau "Show Me" chart types against each library. The coverage claims above (treemap and sankey missing in Vega-Lite, present in ECharts) are from general knowledge and should be checked.
- I didn't confirm from official ECharts docs whether `connect` propagates brush selections.

---

## 4. Platform services: auth, authorization model, metadata, audit, scheduling, email, snapshots, alerts, embedding, deployment, and cost

### Takeaway
- **Hosting:** Run on ECS Fargate with Aurora PostgreSQL Serverless v2 as the metadata store.
- **Identity:** Use OIDC/SAML through Cognito for the AWS-native default; federated MAUs cost $0.015 above 50 free. Keycloak is the alternative, and its SCIM API became supported in 26.8 in October 2026. Either way, TabFree should implement its own SCIM 2.0 endpoint, because the Cognito pricing page says nothing about SCIM.
- **Authorization:** Model Tableau's site → project → content permissions with an embedded Zanzibar-style engine. OpenFGA (Apache-2.0, CNCF incubating) is the default. Cedar or Amazon Verified Permissions ($5 per million authorization requests) is the alternative.
- **Jobs and delivery:** Use EventBridge Scheduler (14M invocations a month free) for jobs, SES ($0.10 per 1,000 emails) for subscriptions, and Playwright Chromium on Fargate for PDF and PNG snapshots.
- **Estimated monthly cost:**

  | Deployment | Infrastructure (Athena path) | Claude tokens (authoring) | Optional Redshift Serverless |
  |---|---|---|---|
  | 50 users | ~$250–350 | ~$150–450 | not included |
  | 1,000 users | ~$1.4k | ~$1.4k–2.9k | ~$0.8k |

### Cited Findings

#### Identity and provisioning
- Cognito user pools — [Cognito pricing](https://aws.amazon.com/cognito/pricing/):
  - Essentials costs $0.015/MAU above a 10,000-MAU free tier. Plus costs $0.020/MAU with no free tier. Lite is tiered from $0.0055/MAU.
  - SAML/OIDC-federated users cost $0.015/MAU above 50 free MAUs, for all tiers.
  - Machine-to-machine token requests cost about $0.00225 each, per the page's example.
  - The page doesn't mention SCIM.
- Keycloak:
  - Apache-2.0, 37,978 stars — [keycloak/keycloak](https://github.com/keycloak/keycloak)
  - The SCIM 2.0 API went from experimental in 26.6 to preview in 26.7 (July 9, 2026) to supported in 26.8.0 (October 2026). — [Keycloak 26.8.0 release](https://www.keycloak.org/2026/10/keycloak-2680-released), [Keycloak 26.7.0 release](https://www.keycloak.org/2026/07/keycloak-2670-released), [Keycloak SCIM experimental](https://www.keycloak.org/2026/04/scim-as-experimental-feature)
  - Earlier third-party reviews noted gaps such as bulk operations and custom attributes. — [WorkOS analysis](https://workos.com/blog/keycloak-experimental-scim-api-vs-workos-directory-sync)
- Other identity providers:

  | Project | License | Stars | Source |
  |---|---|---|---|
  | Zitadel | AGPL-3.0 | 15,621 | [zitadel/zitadel](https://github.com/zitadel/zitadel) |
  | authentik | MIT core; `authentik/enterprise/` under a separate license | 26,391 | [goauthentik/authentik](https://github.com/goauthentik/authentik) |
  | Ory Kratos | Apache-2.0 | 13,962 | [ory/kratos](https://github.com/ory/kratos) |
  | Ory Hydra | Apache-2.0 | 17,674 | [ory/hydra](https://github.com/ory/hydra) |

#### Authorization engines
- OpenFGA:
  - Apache-2.0, 6,164 stars, v1.22.1 released 2026-10-28 — [openfga/openfga](https://github.com/openfga/openfga)
  - It joined CNCF Incubation on October 28, 2025, as announced in the CNCF blog of November 11, 2025. — [OpenFGA blog](https://openfga.dev/blog/incubation-announcement), [CNCF blog](https://www.cncf.io/blog/2025/11/11/openfga-becomes-a-cncf-incubating-project/)
- SpiceDB is Apache-2.0 with 7,229 stars and v1.57.1 released 2026-11-10. It is "Google Zanzibar-inspired". — [authzed/spicedb](https://github.com/authzed/spicedb). Search found no evidence that it is a CNCF project.
- Cedar:
  - Apache-2.0, 1,890 stars, CLI v4.15.0 released 2026-11-16 — [cedar-policy/cedar](https://github.com/cedar-policy/cedar)
  - CNCF Sandbox since October 8, 2025 — [AWS Open Source Blog](https://aws.amazon.com/blogs/opensource/cedar-joins-cncf-as-a-sandbox-project), [CNCF project page](https://www.cncf.io/projects/cedar/)
- Amazon Verified Permissions — [AVP pricing](https://aws.amazon.com/verified-permissions/pricing/):
  - `IsAuthorized` and `IsAuthorizedWithToken` cost $0.000005 per request, which is $5 per million.
  - Batch calls cost $0.00015 each for the first 40M a month, then fall to $0.000075 and $0.00004.
  - Policy management calls cost $0.00004 each.
  - There are no minimum fees.
- Apache Casbin is Apache-2.0 with 20,506 stars; the repo now lives under `apache/casbin` — [apache/casbin](https://github.com/apache/casbin). OPA is Apache-2.0 with 12,501 stars. — [open-policy-agent/opa](https://github.com/open-policy-agent/opa)

#### Compute, network and database prices (us-east-1)
- Fargate, Linux/x86: $0.000011244 per vCPU-second (about $0.0405/vCPU-hour) and about $0.00445/GB-hour. Linux/ARM: about $0.0324/vCPU-hour and $0.00356/GB-hour. Tasks get 20 GB of ephemeral storage by default. Spot is "up to a 70% discount". — [Fargate pricing](https://aws.amazon.com/fargate/pricing/)
- An Application Load Balancer costs $0.0225 per ALB-hour plus $0.008 per LCU-hour. One LCU covers 25 new connections/s, 3,000 active connections/min, 1 GB/hour processed, or 1,000 rule evaluations/s. — [ELB pricing](https://aws.amazon.com/elasticloadbalancing/pricing/)
- Aurora PostgreSQL — [Aurora pricing](https://aws.amazon.com/rds/aurora/pricing/):
  - Serverless v2 costs $0.12/ACU-hour on Aurora Standard and $0.156 on I/O-Optimized.
  - Standard storage costs $0.10/GB-month and I/O costs $0.20 per million.
  - The page contradicts itself on minimum capacity: the table says "0 ACU", the examples say 0.5 ACU.
- NAT gateway costs $0.045/hour and $0.045/GB processed; the page shows these figures for US East (Ohio). Public IPv4 addresses cost $0.005/hour. — [VPC pricing](https://aws.amazon.com/vpc/pricing/)

#### Scheduling, email and snapshots
- EventBridge Scheduler includes 14,000,000 invocations a month free, then costs $1.00 per million. EventBridge Pipes costs $0.40 per million requests. — [EventBridge pricing](https://aws.amazon.com/eventbridge/pricing/)
- SES costs $0.10 per 1,000 outbound emails plus $0.12 per GB of attachments. Dedicated IPs cost $24.95 per IP per month (standard), or $15/month plus per-email fees (managed). — [SES pricing](https://aws.amazon.com/ses/pricing/)
- Playwright is Apache-2.0, has 100,638 stars and released v1.65.1 on 2026-11-13. — [microsoft/playwright](https://github.com/microsoft/playwright)
- `@sparticuz/chromium` is an MIT-licensed "Chromium for Serverless Platforms". It warns that accessible (tagged) PDFs need Chromium recompiled with a patch. — [Sparticuz/chromium](https://github.com/Sparticuz/chromium)
- Community Lambda guides report the 250 MB unzipped layer limit, which a size-optimized Chromium fits. They recommend container images for headroom, 1–2 GB of memory, and planning around the 15-minute timeout and limited `/tmp`. These are secondary sources. — [playwright-chromium-lambda-layer](https://github.com/run-serverless/playwright-chromium-lambda-layer), [BrowserCat guide](https://browsercat.com/post/running-playwright-on-aws-lambda-challenges-solutions)

#### Audit logging precedent and embedding
- Superset's MCP server writes tool calls to Superset's action log and applies no rate limiting of its own. — [Superset MCP server docs](https://superset.apache.org/admin-docs/configuration/mcp-server)
- Claude in Amazon Bedrock emits logs to CloudWatch and CloudTrail. Anthropic recommends keeping at least 30 days. — [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock)
- Tableau's connected-apps JWT design (HS256, ≤10-minute `exp`, `jti`, `scp`, two rotating secrets, domain allowlist, user-attribute claims) is covered in section 3. — [Tableau connected apps](https://docs.salesforce.com/tableau/current/en-us/online/connected_apps_direct.md)

### Inferences

**Recommended default and alternative per platform service**

| Service | Default | Alternative | Reasoning |
|---|---|---|---|
| AuthN | Cognito Essentials user pool as an OIDC/SAML broker to Okta or Entra ID. The app trusts any OIDC issuer, so self-hosters can swap it. | Keycloak 26.8+ on Fargate + Aurora | Cognito is cheap and has no infrastructure to run. Keycloak adds SCIM, finer realm control and portability (Helm). Zitadel's AGPL is less attractive to embed. |
| Provisioning | TabFree's own SCIM 2.0 endpoint (`/scim/v2/Users`, `/Groups`) secured by a bearer token per IdP connection | Keycloak SCIM API, with TabFree syncing from Keycloak | Cognito has no documented SCIM. Groups drive permissions, so TabFree must own group membership. |
| AuthZ engine | OpenFGA, either embedded or as a sidecar on Fargate, using Aurora Postgres as its datastore | Cedar as an embedded library (no network hop), or AVP for AWS-managed | ReBAC fits the nested site/project/content structure and group membership. CNCF incubating, Apache-2.0. AVP would make TabFree AWS-only and adds per-call cost. |
| Metadata DB | Aurora PostgreSQL Serverless v2 (Standard) | RDS PostgreSQL single-AZ for small installs, or a self-hosted Postgres in Helm | It stores users, groups, content, revisions, schedules, audit log and OpenFGA tuples. |
| Scheduling | EventBridge Scheduler (one schedule per subscription, refresh or alert, with time zones) → SQS → worker service | Postgres-backed queue (pg-boss or Graphile Worker) for non-AWS self-hosting | Effectively free at this scale and durable. |
| Email | SES, using configuration sets, DKIM and per-site sending identities | SMTP relay (self-hosters) | $0.10 per 1,000 emails plus $0.12/GB of attachments. |
| PDF/PNG snapshots | Playwright Chromium in a Fargate service with an SQS-driven worker pool. It renders the same dashboard runtime through a service JWT scoped to the subscriber, so RLS applies per recipient. | Lambda container image with `@sparticuz/chromium` for bursty, small loads | Fargate avoids Lambda's size, timeout and `/tmp` limits and keeps fonts consistent. |
| Data-driven alerts | Scheduled evaluation of a semantic query plus a threshold expression stored with the alert, run by the worker. Notify through SES, Slack or webhook. Store the last state to avoid repeat notifications. | Redshift MV + Data API polling for live sources | Reuses the governed query path, so alerts respect RLS. |
| Embedding | Connected-app style: host-signed JWT (≤10 min, `jti`, scopes, user-attribute claims) exchanged for a session; CSP `frame-ancestors` from the allowlist | OIDC SSO inside the iframe | Mirrors Tableau's model, so Tableau-embedding customers can migrate easily. |
| Audit log | Append-only `audit_event` table (who, what, content id, permission decision, query hash, agent session id, tool name), exported to S3 or CloudWatch. Add CloudTrail for AWS API calls. | OpenTelemetry logs to the customer's SIEM | Covers human actions and AI-agent and MCP tool calls in one stream. |
| Compute/orchestration | ECS Fargate (ARM where possible), deployed with Terraform/OpenTofu modules | EKS plus a Helm chart for self-hosters and Kubernetes shops | Fargate has no cluster to manage and gives one task per agent session. A Helm chart broadens adoption. |
| Infrastructure as code | Terraform/OpenTofu, which has a larger open-source community and is cloud-agnostic for self-hosters | AWS CDK (TypeScript), which is more AWS-native | This is a judgment call. I found no sources comparing them for this use case. |

**Authorization model mirroring Tableau (OpenFGA model sketch)**
- **Object types:** `site`, `project` (with `parent: project | site`), `workbook/dashboard`, `datasource`, `group`, `user`.
- **Site roles:** `site#creator | explorer | viewer | site_admin` map to Tableau site roles.
- **Capabilities per content type:**
  - Dashboards: `can_view`, `can_filter`, `can_download_data`, `can_download_image`, `can_web_edit`, `can_subscribe`, `can_overwrite`, `can_delete`, `can_set_permissions`.
  - Datasources: `can_connect`, `can_view`, `can_overwrite`.
  - Each capability is a union of direct grants (`user`, `group#member`) and project inheritance (`from parent`), minus explicit denies. OpenFGA expresses the deny part with "but not" exclusion relations.
- **Permission precedence:** Tableau's rule as described in the brief is that a group deny beats a group allow. Implement it as `allowed = (allow_user) or ((allow_group) but not (deny_group))`, with user-level rules evaluated first. Check Tableau's exact precedence before freezing the model.
- **Locked projects:** Tableau's "locked" project permissions become a project flag that makes content capabilities inherit only from the project.
- **Cedar alternative:** `forbid` policies override `permit`, which gives deny-wins directly.
- **Integration:** Use the authorization engine's "list objects" call to filter browse and search results. Keep RLS separate, in the semantic layer, keyed on user attributes and groups.

**Cost model**

Assumptions are mine. Unit prices are the cited us-east-1 rates. The model excludes CloudWatch, KMS, Secrets Manager, backups, WAF and data transfer out.

*Small deployment: 50 users, ~20 daily active, ~100 dashboard views/day, 300 AI-authoring sessions/month*

| Line item | Sizing | Est. $/month |
|---|---|---|
| API/web + MCP + Cube API (Fargate ARM) | 2 × (1 vCPU, 2 GB) 24/7 | ~58 |
| Worker (schedules, refresh orchestration, alerts, Cube refresh) | 1 × (1 vCPU, 2 GB) | ~29 |
| Cube Store | 1 × (1 vCPU, 4 GB) ARM | ~34 |
| Snapshot renderer | x86 1 vCPU/2 GB, ~60 task-hours | ~3 |
| AI authoring sandboxes | 150 task-hours × (1 vCPU, 2 GB) ARM | ~6 |
| OpenFGA | 1 × (0.5 vCPU, 1 GB) ARM | ~14 |
| Aurora Serverless v2 | 0.5 ACU floor + 10 GB storage | ~45 |
| ALB | 1 ALB, ~1 LCU, 2 public IPv4 | ~30 |
| NAT gateway | 1 NAT + ~20 GB + IPv4 | ~38 |
| Athena | ~1.5 TB refresh scans + ~0.6 TB dashboard scans at $5/TB | ~11 |
| Glue compaction optimizer | ~10 DPU-hours | ~4 |
| S3 | ~100 GB (uses the $0.023/GB S3 Standard figure, secondary source) | ~2 |
| SES | 2,000 emails + 2 GB attachments | <1 |
| Cognito | ≤50 federated MAU (free) | 0 |
| EventBridge Scheduler | free tier | 0 |
| **Infrastructure subtotal** | | **≈ $275 (range ~$250–350)** |
| Claude tokens | 300 sessions × ~$0.5–1.5. This assumes about 400k input tokens (mostly cache reads) and 25k output per session on Sonnet 5.5 ($2/$10 per MTok; cache reads $0.10). Opus 5.5 costs about 2× that. | ~$150–450 |

*Mid-size deployment: 1,000 users, ~300 daily active, ~3,000 views/day, ~70% cache or pre-aggregation hit rate, 2,200 authoring sessions/month*

| Line item | Sizing | Est. $/month |
|---|---|---|
| API/web + MCP | 4 × (1 vCPU, 2 GB) ARM | ~115 |
| Cube API | 2 × (1 vCPU, 2 GB) ARM | ~58 |
| Cube refresh worker | 1 × (1 vCPU, 2 GB) ARM | ~29 |
| Cube Store | router + 2 workers, each (2 vCPU, 8 GB) ARM | ~204 |
| Workers | 2 × (1 vCPU, 2 GB) ARM | ~58 |
| Snapshot renderer pool | 2 × (2 vCPU, 4 GB) x86, always on | ~144 |
| AI sandboxes | 1,100 task-hours (1 vCPU, 2 GB) ARM | ~43 |
| OpenFGA | 2 × (0.5 vCPU, 1 GB) ARM | ~29 |
| Aurora Serverless v2 | writer ~2 ACU + reader ~1 ACU + 50 GB + ~200M I/O | ~308 |
| ALB | ~5 LCU average | ~53 |
| NAT | 2 AZ + ~300 GB | ~87 |
| Athena | ~10 TB refresh + ~22 TB dashboard scans (216k queries × ~100 MB); range ~$100–600 depending on pruning and pre-aggregations | ~158 |
| Glue optimizer | ~100 DPU-hours | ~44 |
| S3 | ~2 TB | ~46 |
| SES | 30k emails + 30 GB attachments | ~7 |
| Cognito | 950 billable federated MAU × $0.015 | ~14 |
| **Infrastructure subtotal (Athena path)** | | **≈ $1,400** |
| Optional Redshift Serverless for live sources | 8 RPU × $0.375 × ~264 active hours + 1 TB RMS | ~$815 |
| Claude tokens | 2,200 sessions × ~$0.65 (Sonnet 5.5) to ~$1.3 (Opus 5.5) | ~$1.4k–2.9k |

Points the cost model brings out:
1. Claude token spend, not infrastructure, is likely the largest variable cost of the AI features. This matches Anthropic's hosting guidance, where tokens typically dominate container cost "by an order of magnitude or more"; see section 5.
2. NAT, ALB and the Aurora floor dominate the small deployment. Self-hosters can cut NAT cost with S3 gateway endpoints and interface endpoints, at the cost of per-endpoint fees I didn't retrieve.
3. Athena capacity reservations for latency isolation would add about $1.9k/month at 24 DPU for business hours only (~260 hours), or about $5.3k/month 24/7. Each DML query uses at least 4 DPUs, so that reservation runs roughly 6 queries at once.

### Gaps
- Cognito SCIM support isn't confirmed either way; the pricing page is silent. Check the Cognito developer guide.
- I didn't retrieve us-east-1-specific NAT, PrivateLink interface endpoint, CloudWatch Logs, Secrets Manager, KMS, ElastiCache/Valkey or Lambda pricing.
- Aurora Serverless v2's minimum ACU and auto-pause behavior conflict on the pricing page.
- Bedrock's per-token Claude prices weren't retrieved; Anthropic says Bedrock pricing is separate and regional endpoints carry a +10% premium (section 5). Token figures above use Anthropic first-party rates.
- I didn't retrieve Tableau's official permission evaluation order (user vs group, allow vs deny, locked projects); it needs confirming against Tableau docs.
- Playwright on Fargate rendering throughput (pages per vCPU-minute) wasn't benchmarked; renderer pool sizing is a guess.

---

## 5. AI authoring inside the product: server-side Claude agents, Bedrock, the TabFree MCP server, versioning and validation, prompt injection

### Takeaway
- Run authoring as one sandboxed agent session per user edit.
  - Use the Claude Agent SDK, which is Claude Code packaged as a library, in an ECS Fargate task for multi-file or HTML work.
  - Use a lighter Messages API tool loop for quick JSON-patch edits.
- Route model calls to Claude in Amazon Bedrock for AWS-native data residency.
  - Bedrock offers regional endpoints at a +10% premium and US/EU/JP/AU inference profiles, and runs with zero Anthropic operator access.
  - Note that Bedrock lacks the MCP connector, Files API, server tools and Managed Agents.
- Give the agent exactly one tool surface: the TabFree MCP server, running as the end user.
  - Design it from what Tableau, Grafana, Superset and Metabase learned: summaries before full documents, handles instead of retyped state, operation-based patches with `validate_only`, two-phase publish and delete, and permission checks at call time.
  - Fence data values as untrusted content.

### Cited Findings

#### Agent SDK and headless Claude Code
- **Hosting:** [Agent SDK hosting](https://code.claude.com/docs/en/agent-sdk/hosting)
  - The SDK spawns a `claude` CLI subprocess per session. The subprocess owns a shell, the working directory and JSONL transcripts on local disk, none of which survive a container restart.
  - Session patterns: ephemeral, long-running, and hybrid (a `SessionStore` is required for hybrid).
  - Suggested starting resources: "1 GiB RAM, 5 GiB disk, and 1 CPU per agent", with Python 3.10+ or Node 18.14+.
  - "Anthropic token cost typically dominates container infrastructure cost by an order of magnitude or more… a minimally provisioned container runs roughly $0.05 per hour."
  - Multi-tenant isolation settings: `settingSources: []`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, a per-tenant `CLAUDE_CONFIG_DIR` and `cwd`, and per-tenant egress rules.
  - Other notes:
    - There is "no top-level session timeout", so use `maxTurns`.
    - OTEL export does not include prompt text or tool inputs by default.
    - On Bedrock the SDK needs outbound HTTPS to the provider's regional endpoint.
  - Managed Agents, an Anthropic-hosted loop that can use a self-hosted sandbox, is offered as the alternative.
- **Secure deployment:** [Securely deploying AI agents](https://code.claude.com/docs/en/agent-sdk/secure-deployment)
  - The guide compares isolation technologies:

    | Technology | Isolation strength | Overhead |
    |---|---|---|
    | Sandbox runtime | Good (secure defaults) | Very low |
    | Docker | Setup-dependent | Low |
    | gVisor | Excellent | Medium/high; up to 10–200× on file-I/O-heavy patterns |
    | Firecracker VMs | Excellent | High; boots in "under 125ms with less than 5 MiB memory overhead" |
  - The hardened Docker example uses `--cap-drop ALL`, `no-new-privileges`, seccomp, `--read-only`, tmpfs, `--network none`, memory, CPU and PID limits, a non-root user, and a proxy over a Unix socket.
  - Recommended credential pattern: a proxy outside the boundary injects credentials (Envoy `credential_injector`, LiteLLM), or tools are custom/MCP so credentials stay outside.
  - Cloud pattern: a private subnet with no internet gateway, egress only to the proxy, minimal IAM, and logging at the proxy.
  - It cites "The Lethal Trifecta for AI Agents".
- **Headless mode:** [Run Claude Code programmatically](https://code.claude.com/docs/en/headless)
  - `claude -p` runs non-interactively.
  - `--bare` "is the recommended mode for scripted and SDK calls". It skips hooks, skills, plugins, MCP servers, auto memory and CLAUDE.md unless they are passed explicitly.
  - Without `--bare`, a `-p` session runs a project's `.claude/settings.json` hooks and connects its `.mcp.json` servers "even in a folder you've never trusted", with no trust dialog.
  - Output formats are `text`, `json` (which includes `total_cost_usd`) and `stream-json`; `--json-schema` gives structured output.
  - Permission controls include `--allowedTools`, `--permission-mode` (`dontAsk`, `acceptEdits`, `auto`), `--permission-prompts none` for unattended runs and a `--max-budget-usd` cap. Piped stdin is limited to 10 MB.
- **SDK licensing:**
  - The TypeScript Agent SDK has npm license "SEE LICENSE IN README.md". The README says use "is governed by Anthropic's Commercial Terms of Service". — [npm @anthropic-ai/claude-agent-sdk](https://www.npmjs.com/package/@anthropic-ai/claude-agent-sdk), [claude-agent-sdk-typescript](https://github.com/anthropics/claude-agent-sdk-typescript)
  - The Python SDK repo is labeled MIT. — [claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python)

#### Claude on Bedrock and model pricing
- Claude in Amazon Bedrock (the `bedrock-mantle` Messages API endpoint) — [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock):
  - It runs "on AWS-managed infrastructure with zero operator access".
  - Model IDs: `anthropic.claude-opus-5-5`, `anthropic.claude-sonnet-5-5`, `anthropic.claude-haiku-5-5`, `anthropic.claude-fable-5-1`, among others.
  - The global endpoint has no premium. Regional endpoints carry "a 10 percent pricing premium" for data residency, and inference profiles cover US, EU, JP and AU.
  - Default quota is 2M input tokens per minute.
  - Not supported on Bedrock:
    - the Files API
    - server-side tools (code execution, web search, web fetch)
    - Agent Skills, the MCP connector and programmatic tool calling
    - Message Batches
    - Claude Managed Agents
    - server-side fallbacks
  - Authentication uses an IAM service role, assumed roles (12-hour maximum) or bearer tokens. Long-term bearer tokens can be blocked through SCPs.
  - The page points to Claude Platform on AWS as the Anthropic-operated alternative billed through AWS Marketplace.
- Anthropic first-party prices per million tokens — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), per Anthropic's model table cached 2026-11-03:

  | Model | Input | Output | Cache reads |
  |---|---|---|---|
  | Claude Opus 5.5 | $4 | $20 | $0.20 |
  | Claude Sonnet 5.5 | $2 | $10 | $0.10 |
  | Claude Haiku 5.5 | $0.10 (prompts ≤100K tokens) | $0.50 | — |

  All have 1M-token context windows.

#### MCP precedents
- **Tableau MCP (official):** [tableau/tableau-mcp](https://github.com/tableau/tableau-mcp), [toolName.ts](https://github.com/tableau/tableau-mcp/blob/main/src/tools/web/toolName.ts)
  - Apache-2.0, 378 stars.
  - Hosted at `https://mcp.tableau.com` with OAuth 2.1, so "every user signs in with their own Tableau Cloud identity, and all existing per-user permissions are enforced".
  - Web tools include `list-datasources`, `get-datasource-metadata`, `query-datasource`, `get-view-image`, `get-embed-token`, `publish-workbook` and `search-content`.
  - Destructive and administrative tools come in confirm pairs: `delete-content` / `confirm-delete-content` and `update-cloud-extract-refresh-task` / `confirm-update-cloud-extract-refresh-task`.
  - `render-interactive-viz` (an "mcp-apps" group) "Renders a workbook or view… as a live, interactive Tableau embed".
  - It also has knowledge-context tools and `scaffold-data-app`.
- **Grafana MCP:** Apache-2.0, 3,688 stars. Its tools include `get_dashboard_summary`, `get_dashboard_property` (JSONPath), `patch_dashboard` ("without requiring the full JSON") and dashboard version listing. It warns that full dashboard JSON "can consume large amounts of context window space". — [grafana/mcp-grafana](https://github.com/grafana/mcp-grafana)
- **Superset MCP (built-in):** [Superset MCP server](https://superset.apache.org/admin-docs/configuration/mcp-server)
  - Tools include `list_datasets`, `get_dataset_info`, `get_chart_type_schema`, `generate_chart`, `update_chart_preview`, `generate_dashboard`, `query_dataset` and `execute_sql`.
  - Auth is JWT (RS256 or HS256). Per-user RBAC is on by default.
  - Embedded guest tokens are limited to a default-deny tool allow-list.
  - Charts are capped at 50,000 rows. `execute_sql` has a 30-second timeout, and a full worker pool returns "busy" instead of queuing.
  - Calls are audited to the action log, and there is no built-in rate limiting.
- **Metabase MCP lessons (November 17, 2026):** [Metabase blog](https://www.metabase.com/blog/mcp-tool-design-lessons)
  - Tool count fell from a 1:1 REST mapping (31) to 24, with batched `get_content`.
  - Write tools take `method: create|update` and there is no delete tool; archive instead.
  - Results are short by default, with an explicit truncation note ("Returned 50 of 212 — continue with offset: 50").
  - A `query_handle` keeps query state on the server, and handles are per-user.
  - Dashboard edits are operation lists applied in one transaction, with a `validate_only` preview.
  - Long instructions are loaded on demand through a `learn` tool. Claude Code capped tool descriptions at 2,048 characters by default.
  - Permissions are checked at call time rather than by filtering the tool list; a missing permission returns 403 `insufficient_scope`.
  - Untrusted data (names, comments, results) is fenced with random boundaries. The post states that fencing "doesn't guarantee" safety and that permission scoping is the main limit on damage.
  - Raw SQL is a separate permission.
- **MCP Apps:**
  - The official extension, spec version 2026-01-26, puts UIs in host-sandboxed iframes with declared CSP. — [MCP Apps spec](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)
  - Supported clients include Claude, ChatGPT, VS Code, Goose and Postman. The repo is moving from MIT to Apache-2.0. — [ext-apps README](https://github.com/modelcontextprotocol/ext-apps)
  - MCP-UI is Apache-2.0 with 5,272 stars. — [MCP-UI-Org/mcp-ui](https://github.com/MCP-UI-Org/mcp-ui)

### Inferences

**Recommended architecture**
- **Default: Agent SDK on Fargate, one task per authoring session**
  - The "Author" API starts one ECS Fargate task per authoring session: ARM, 1 vCPU, 2 GiB, 20 GiB ephemeral storage, in a private subnet with no internet gateway. That suits Fargate's per-task isolation.
  - The task runs the Agent SDK in bare-equivalent mode:
    - `settingSources: []`
    - auto memory off
    - a per-session `cwd` holding the dashboard spec plus any custom widget files
    - `maxTurns` and a budget cap
    - built-in tools limited to Read, Edit and Write in the workspace
    - no WebFetch or WebSearch
    - no Bash, or Bash only behind an allowlist for the validator CLI
  - The model is Bedrock `anthropic.claude-sonnet-5-5`, optionally Opus 5.5 for complex multi-tile builds, reached through the task's IAM role. Network egress is allowed only to the Bedrock endpoint and the TabFree MCP endpoint via a VPC endpoint or proxy.
  - The MCP connection carries a short-lived token minted for the requesting user (on-behalf-of), so every tool call is authorized and RLS-filtered as that user.
  - Transcripts persist to S3 through a `SessionStore` so sessions can resume.
- **Alternative for quick edits:** A Messages API tool-use loop inside the TabFree API, calling the same MCP tools in-process. Use it for "make this stacked" style edits. It has lower latency, no container to start and no shell.
- **Alternative for non-Bedrock deployments:** Managed Agents with a self-hosted sandbox. It isn't available on Bedrock.

**TabFree MCP server tool design**
- **Read tools:**
  - `list_datasources`
  - `describe_datasource`: fields, measures, dimensions, semantic types and a few fenced sample values
  - `search_content`
  - `get_dashboard`: summary by default; `paths=[…]` to read specific subtrees, JSONPath-style
  - `list_revisions` and `diff_revisions`
- **Query tool:**
  - `run_governed_query`: takes a semantic query JSON and returns a `query_handle`, ≤N rows and a truncation note
  - No raw SQL tool by default; `execute_sql` sits behind a separate admin capability
- **Write tools:**
  - `create_dashboard`
  - `patch_dashboard`: a list of operations such as add_tile, set_encoding, wire_filter and move_tile, with a `validate_only` flag
  - `set_custom_widget_code`: find-and-replace edits on widget files
- **Quality tools:**
  - `validate_dashboard`: JSON-schema check, semantic-query compile on Athena and Redshift dialects, performance estimate (bytes scanned or pre-aggregation hit), accessibility and contrast lint, and a CSP/static scan of custom widget code
  - `preview`: a Playwright-rendered PNG plus an MCP Apps `ui://` interactive preview for external MCP clients
- **Lifecycle tools:**
  - `request_publish`, which returns a review id
  - `confirm_publish`, which needs a user with `can_publish` or an approval rule
  - `archive_dashboard` instead of delete
- **On-demand documentation:** `learn(topic)` serves the DSL reference and chart cookbook.
- **Protocol-level controls:**
  - OAuth 2.1 per user, as Tableau's hosted MCP does
  - permission checks at call time, with 403 errors that "name the fix"
  - every call logged to `audit_event` with the agent session id
  - rate limits at the gateway, since Superset shows MCP servers don't do this themselves

**Versioning, review and validation**
- Store every save as an immutable revision row in Postgres. A revision holds the spec JSON, a content hash, a parent revision, author type (human or agent), the agent session id, the prompt summary and the validation results.
- Optionally mirror projects to a Git repository so teams can review in PRs.
- Publishing promotes a revision. A per-project approval policy can require a human approver for agent-authored revisions.
- Automated gates on each revision:
  1. schema and lint checks
  2. query compile on every target dialect
  3. data checks: non-empty results, row counts within a tolerance of the previous revision, and for converted Tableau workbooks, aggregate equivalence against Hyper
  4. a screenshot diff against the previous revision with Playwright at a fixed viewport, data snapshot and font set
  5. a security scan of custom widget code (no network APIs, no `eval`, CSP-compatible)

**Prompt-injection controls when agents read data values**
- Treat column names, descriptions, comments and cell values as untrusted. Return them in fenced blocks with random boundaries, as Metabase does.
- Avoid the "lethal trifecta" structurally:
  - Agent sandboxes have no general egress, only Bedrock and MCP.
  - Dashboards can't make network calls; the CSP rules this out.
  - Alerts and subscriptions that send to external addresses need a human confirmation step and notify the owner.
- The agent runs with the user's own permissions, so injected instructions can't widen data access.
- Publishing and permission changes need two-phase confirmation by a human.

### Gaps
- I didn't retrieve Bedrock's per-token prices for Claude, or whether Claude Opus 5.5 and Sonnet 5.5 are open to all Bedrock customers. The docs say "See Access".
- I didn't verify whether the Agent SDK can use the `bedrock-mantle` endpoint or only `bedrock-runtime`. Hosting docs say only "your provider's regional endpoint".
- I found no measured token usage per dashboard-authoring session. The $0.5–1.5 per session figure is an assumption to validate with a pilot, using `total_cost_usd` from `--output-format json`.
- I didn't check Fargate task cold-start time for the per-session sandbox pattern; it matters for perceived latency.
- I didn't determine which MCP clients support MCP Apps beyond those listed in the ext-apps README.

---

## 6. Licensing and legal: project license, "TabFree" name and trademark, and Hyper API dependency

### Takeaway
- **Hyper API dependency:** Allowed. The Tableau Hyper API has been Apache-2.0 since July 2024, and PyPI lists it as Apache-2.0, so an open-source converter can depend on it and redistribute it.
- **Project license:** Apache-2.0 is recommended. It matches Superset, Cube, Rill, MetricFlow, OpenFGA and the Hyper API.
- **Name:** "TabFree" is a significant trademark risk. Salesforce's trademark guidelines, which explicitly cover Tableau, forbid using "any recognizable portion" of its marks, or abbreviations of them, in a product or domain name. A product positioned as a Tableau replacement makes "Tab-" read as an abbreviation of Tableau.
- **Clearance status:** No "TabFree" trademark turned up in the searches I ran. tabfree.com has been registered since 2005. A distinct name with nominative "compatible with Tableau" wording is the safer path.

### Cited Findings
- **Hyper API license:**
  - "Hyper API is now available under the Apache 2.0 license" (release 0.0.19691, July 4, 2024) — [Hyper API release notes](https://tableau.github.io/hyper-db/docs/releases)
  - PyPI `tableauhyperapi` 0.0.26963 lists license Apache-2.0 with the OSI classifier — [PyPI](https://pypi.org/project/tableauhyperapi/)
  - The Hyper docs repo is MIT, and its SQL docs include PostgreSQL-licensed content — [tableau/hyper-db](https://github.com/tableau/hyper-db)
  - The Hyper samples repo is MIT — [tableau/hyper-api-samples](https://github.com/tableau/hyper-api-samples)
- The legacy Tableau Data Extract API was deprecated. Its old license allowed distribution only "as long as Tableau is notified". — [Tableau Data Extract API page](https://www.tableau.com/data-extract-api)
- **Salesforce trademark guidelines:** [Salesforce Trademark & Copyright Usage Guidelines](https://www.salesforce.com/company/legal/tmcusageguidelines/)
  - References to Salesforce marks "include those of its affiliate brands, including Heroku, MuleSoft, Quip, Slack, and Tableau".
  - "You may not incorporate any of Salesforce's trademarks or copyrights, or any recognizable portion" into a "company name, app name, product name, service name, website or domain name, social media handle". The page's own counter-example is "(Do not) Welo's website is www.tableauapp.com."
  - "You may not use abbreviations or translations of any of Salesforce's trademarks."
  - You may not use names that "may be viewed as being a part or extension of a Salesforce trademark" family, or that "give the impression of affiliation with or endorsement by Salesforce".
  - Plain-text referential use is allowed if:
    - the mark is not part of the offering's name
    - it is less prominent than the offering's name
    - it uses prefixes such as "compatible with", "runs on", "built on" or "integrates with" when true
    - it includes the attribution "[mark] is a trademark of Salesforce, Inc."
  - "Most uses require specific written permission."
- Tableau partner terms bar partners from registering domains that contain "Tableau" or a Tableau product name. — [Tableau Partner Network Master Terms (2022)](https://origin-tableau-www.tableau.com/sites/default/files/2022-03/tableau_partner_network_master_terms.pdf)
- **"TabFree" searches:**
  - Trademarkia returned "0 Trademark Results found for 'tabfree'" — [Trademarkia search](https://www.trademarkia.com/search/trademarks?q=tabfree)
  - A Justia-indexed search for "TabFree" turned up only unrelated TAB- marks, such as TABWARE and TABSOFT — [Justia: TABWARE example](https://trademarks.justia.com/856/00/tabware-85600802.html)
  - A web search found no software product named TabFree, only similarly named tab-management apps.
- **TabFree domains (WHOIS on 2026-11-20; re-verify at [ICANN Lookup](https://lookup.icann.org)):**
  - `tabfree.com` has been registered since 2005-09-11 through GoDaddy, expires 2027-09-11, and resolves to registrar-hosted IPs.
  - WHOIS returned "Domain not found" for `tabfree.io`, `tabfree.org` and `tabfree.ai`.
- **License precedents among open-source BI projects:**

  | Project | License | Source |
  |---|---|---|
  | Apache Superset | Apache-2.0 | [apache/superset](https://github.com/apache/superset) |
  | Metabase | AGPL outside `enterprise/`; Metabase Commercial License inside it, with separate enterprise binaries | [metabase/metabase](https://github.com/metabase/metabase) |
  | Grafana | AGPL-3.0 | [grafana/grafana](https://github.com/grafana/grafana) |
  | Lightdash | MIT, with `packages/backend/src/ee` under a separate license | [lightdash/lightdash](https://github.com/lightdash/lightdash) |
  | Evidence | MIT | [evidence-dev/evidence](https://github.com/evidence-dev/evidence) |
  | Rill | Apache-2.0 | [rilldata/rill](https://github.com/rilldata/rill) |
  | Cube | Apache-2.0 / MIT | [cube-js/cube](https://github.com/cube-js/cube) |
  | MetricFlow | Moved AGPL → BSL → Apache-2.0 (October 2025) | [Atlan explainer](https://atlan.com/know/ai-agent/semantic-layer/metricflow-apache-2-license-dbt-semantic-layer/) |
- **AI dependencies' licenses:**
  - The TypeScript Claude Agent SDK is governed by Anthropic's Commercial Terms of Service, not an OSI license. — [claude-agent-sdk-typescript README](https://github.com/anthropics/claude-agent-sdk-typescript)
  - The MCP Apps repo is moving to Apache-2.0. — [ext-apps LICENSE](https://github.com/modelcontextprotocol/ext-apps)

### Inferences
- **License:** Apache-2.0 is the recommended project license.
  - It has an explicit patent grant, which matters for enterprise adoption, and AWS-centric enterprises are comfortable with it.
  - It is compatible with the key dependencies: the Hyper API, Cube, OpenFGA, ECharts, Perspective and Playwright are Apache-2.0, and Vega-Lite, MapLibre, deck.gl and TanStack are BSD or MIT.
  - AGPL (the Grafana/Metabase model) protects against cloud "strip-mining", but it deters embedding and SaaS integrators and complicates the "free Tableau replacement" message.
  - MIT lacks the patent grant.
  - If the team later wants a commercial edition, use the Lightdash/authentik pattern: a separately licensed `ee/` directory.
  - The only non-OSI runtime dependency is the Claude Agent SDK, under Anthropic's Commercial Terms. Keep AI authoring an optional, pluggable module, and document that operators accept Anthropic or AWS Bedrock terms when they enable it.
- **Trademark:**
  - "TabFree" for a Tableau replacement risks the "recognizable portion / abbreviation / extension of a Salesforce trademark family" provisions, plus general likelihood-of-confusion arguments.
  - The absence of a registered "TABFREE" mark doesn't remove that risk, because the risk comes from Tableau's marks.
  - Recommended steps:
    1. Pick a distinct name with no "Tab"/"Tableau" stem.
    2. Use nominative wording only, such as "imports Tableau workbooks", "compatible with Tableau .hyper extracts" or "migrate from Tableau Server", in plain text that is less prominent than the product name, with the attribution line.
    3. Never use Tableau logos or trade dress.
    4. Run a professional clearance search (USPTO, EUIPO, UKIPO) for the chosen name, in Class 9 (downloadable software) and Class 42 (SaaS).
    5. Register project domains before announcing.
- **Hyper API:**
  - Apache-2.0 allows bundling `tableauhyperapi` in an Apache-2.0 converter.
  - Keep the NOTICE file and attribution, and don't use Tableau marks in the converter's name. For example, call it "hyper-to-iceberg", not "Tableau converter".
  - Recheck the license inside each new wheel, since the native `hyperd` binary ships in the wheel.

### Gaps
- I didn't run a direct search on the USPTO Trademark Search system, EUIPO or UKIPO; it's an interactive web app. Trademarkia and Justia results are secondary.
- I couldn't identify who owns `tabfree.com` (2005 GoDaddy registration) or what it's used for.
- Whether `tabfree.dev` is registered wasn't determined, because WHOIS returned no data.
- I didn't find the full Hyper API license text inside the current wheel. The Apache-2.0 claim rests on the release notes and PyPI metadata, so the team should confirm that the LICENSE bundled in the wheel covers the native binaries.
- None of this is legal advice. Trademark and naming decisions should be reviewed by counsel.
