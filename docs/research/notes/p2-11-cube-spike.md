# P2.11: Cube spike, desk phase (PRELIMINARY)

*Desk research, 2026-11-20. **This is not the hands-on spike.** No Cube instance was run. Findings come from Cube's public docs and its source on GitHub, read through `gh api` at `master` (release **v1.8.31**, tagged 2026-11-20). Conclusions that depend on runtime behaviour are marked "to verify".*

**Question (ADR-003).** Can Cube Core run the compiler's staged SQL (data source, RLS and context-filter CTEs; FIXED/INCLUDE/EXCLUDE CTEs joined at the combined grain; Top N semi-joins; viewer identity as bind parameters) *unchanged*, while also supplying pre-aggregations, caching and access policies?

**Preliminary answer: no.** Cube's caching, rollups and policies all attach to queries that Cube itself plans from its own data model. Cube has no passthrough path for SQL against raw tables. **Recommendation: the governed query service runs compiled SQL directly, with its own result cache and compiler-matched rollups.** We would take design ideas from Cube but not run it as a component. Details follow.

**Licence and activity.** The repository is Apache-2.0 by default, with some MIT-licensed parts ([LICENSE](https://github.com/cube-js/cube/blob/master/LICENSE)). Releases ship almost daily: v1.8.27 through v1.8.31 all came out between 17 and 20 November. Tesseract, the Rust SQL planner, reached GA and became the default in v1.7 ([blog, 2026-07-08](https://cube.dev/blog/cube-core-v1-7-tesseract-ga-data-modeling-performance)).

## 1. Can Cube execute pre-compiled SQL and still cache it?

Cube has three entry points for SQL, and none of them runs our SQL as we wrote it.

- **The SQL API queries cubes and views, not tables.** The reference describes `FROM` as taking "source cube tables" and says "cubes are a read-only data source" ([SQL API reference](https://docs.cube.dev/reference/core-data-apis/sql-api/reference)). Every query is either rewritten into a Cube query (regular, or regular plus post-processing) or **pushed down**: Cube transpiles it and "generate[s] the SQL for an upstream data source" ([queries](https://docs.cube.dev/reference/core-data-apis/queries), [query format](https://docs.cube.dev/reference/core-data-apis/sql-api/query-format)). Pushdown accepts "pretty much any SQL", and v1.7 added window functions and FULL/RIGHT joins. Even so, the SQL that reaches Athena is Cube's regeneration of our query, not our query. ADR-003's adoption test requires the SQL to run unchanged, so pushdown fails it by construction.
- **To use the SQL API at all**, every Iceberg table would need a cube (`sql_table`) with every column declared as a dimension, and the compiler would have to emit SQL against cube names. Equivalence would then depend on Cube's transpiler handling null-safe joins, `IS NOT DISTINCT FROM` and correlated semi-joins. The reference documents none of these (to verify).
- **A cube's `sql` can be "any valid SQL query"** that returns a plain table ([cube reference](https://docs.cube.dev/reference/data-modeling/cube)). We could therefore wrap each compiled worksheet query as its own cube. But data models are compiled per `context_to_app_id`, and refresh keys belong to cubes ([config](https://docs.cube.dev/reference/configuration/config)). Generating a cube for every worksheet and every filter state would turn Cube's model compiler into a per-query code generator, and none of the rollup matching below would apply to those cubes. I would not pursue this.
- **Caching of pushdown queries is limited.** Pushed-down queries "benefit from in-memory cache". They can use pre-aggregations in Cube Store, or in the source when `external: false`, but only when Cube can map them onto members (`CUBESQL_SQL_PUSH_DOWN_PRE_AGGREGATIONS`; [env vars](https://docs.cube.dev/reference/configuration/environment-variables)). Otherwise "the query is sent to the data source as usual".
- **Row cap.** `CUBEJS_DB_QUERY_LIMIT` defaults to 50,000 rows. Inner regular queries are cut at that limit, and "the result can be incorrect without any error being raised" ([query format](https://docs.cube.dev/reference/core-data-apis/sql-api/query-format)). Cross-tabs and table-calc partitions that ADR-004 sends to the JS engine can exceed it.

## 2. Pre-aggregations: could a TabFree LOD query hit a rollup?

Rollup matching is defined over Cube members ([matching](https://docs.cube.dev/docs/pre-aggregations/matching-pre-aggregations)). A query matches a rollup only if all of the following hold:

- every leaf measure is additive;
- the rollup contains every queried dimension, filter dimension and leaf measure;
- no measure is multiplied across a `one_to_many` join;
- the time dimension has a granularity;
- every filter dimension is also a query dimension.

None of this inspects arbitrary SQL. A staged LOD query written in raw SQL cannot hit a rollup unless the pushdown planner first recognises it as member queries.

**The closer fit is Cube's own multi-stage measures.** Tesseract's `grain` parameter, with `keep_only`, `include` and `exclude`, corresponds closely to FIXED, INCLUDE and EXCLUDE, and each stage becomes CTEs ([measures](https://docs.cube.dev/docs/data-modeling/measures); `grain` landed in the 2026-08 [changelog](https://cube.dev/changelog/2026-08-21-changelog-bin-and-group)). Since v1.7, a multi-stage query can be served "by a separate matching pre-aggregation per subquery". But using it would make Cube the semantic model, which ADR-003 rejected. It would also mean re-encoding Tableau's order of operations in Cube's terms:

- Inner stages *inherit* the query's filters unless the measure overrides them with a `filter` parameter.
- In Tableau, FIXED ignores dimension filters unless they are context filters, so the semantics depend on per-view filter state.
- The docs also warn that combined `grain.include` inputs can silently cross-join.

On top of that, table calcs (ADR-004), blends and native-grain relationships have no equivalent in Cube.

**Storage on AWS.**

- Rollups go to Cube Store as Parquet by default ([using pre-aggregations](https://docs.cube.dev/docs/pre-aggregations/using-pre-aggregations)).
- **Athena:** the default *batching* build strategy is read-only. Export-bucket builds use `UNLOAD` and need `s3:PutObject`. Setting `external: false` builds rollups in Athena with CTAS, which needs `glue:CreateTable` ([Athena driver](https://docs.cube.dev/admin/connect-to-data/data-sources/aws-athena)).
- **Redshift:** neither build strategy is read-only, and the user must own a pre-aggregations schema ([Redshift driver](https://docs.cube.dev/admin/connect-to-data/data-sources/aws-redshift)).

**Conclusion.** The rollups that matter for TabFree are ones the compiler can match itself. It knows each worksheet's grain, which measures are additive, and which FIXED dimensions are a subset of a rollup's dimensions. Writing those rollups as Iceberg CTAS tables (ADR-007) keeps them in the customer's open storage, not in Cube Store.

## 3. Access policies and RLS vs bind-parameter RLS

- **How Cube enforces RLS.** It uses `securityContext` (a JWT or `check_sql_auth`), `query_rewrite`, and YAML/JS access policies with `member_level` includes/excludes, masking and `row_level` filters on members ([access policies](https://docs.cube.dev/reference/data-modeling/data-access-policies), [RLS](https://docs.cube.dev/docs/data-modeling/access-control/row-level-security)). `userAttributes` and the group-management UI are Cube Cloud only; Core gets `securityContext`.
- **Pushdown limits.** For pushdown queries, `query_rewrite` supports "only applying additional filters" ([queries](https://docs.cube.dev/reference/core-data-apis/queries)). TabFree's RLS predicate must sit in the data-source stage, *before* the FIXED CTEs, or the LOD values leak rows the viewer cannot see. The docs do not say where Cube places a policy filter inside a CTE-staged pushdown query (to verify; this is the security-critical question).
- **The Athena driver inlines parameters.** `startQuery` calls `applyParams` → `formatAnsi(query, params)` and sends a plain `QueryString`. It does not use Athena `ExecutionParameters`, and it sets no `ResultReuseConfiguration` ([AthenaDriver.ts](https://github.com/cube-js/cube/blob/master/packages/cubejs-athena-driver/src/AthenaDriver.ts)). On Athena, our bind parameters would become escaped literals. The Redshift driver builds on the Postgres driver, so real parameters survive there.
- **Multi-tenancy.** Per-site Athena workgroups or Redshift schemas need `driver_factory` plus a per-tenant `context_to_orchestrator_id`, which gives each tenant its own connections, queues and caches. They also need a per-tenant `pre_aggregations_schema` (which "Cube will wipe out… before use") and `scheduled_refresh_contexts` for the refresh worker ([config](https://docs.cube.dev/reference/configuration/config)). This works, but every site multiplies orchestrators and refresh work.

TabFree would still need its own permission engine (ADR-006) and compiler RLS. Cube's policies would be a second, differently shaped enforcement point to keep in step with the first, not a replacement.

## 4. Caching

- **What Cube caches.** "The cache key is a generated SQL statement with any existing query-dependent pre-aggregations." Freshness comes from per-cube **refresh keys**, which are polled. The default on Athena is `every: 2 minute`, or a `sql` check such as `SELECT MAX(updated_at)` ([caching](https://docs.cube.dev/docs/pre-aggregations), [cube reference](https://docs.cube.dev/reference/data-modeling/cube)). "There's no straightforward way to disable caching." In production, cache and queue state live in Cube Store (`CUBEJS_CACHE_AND_QUEUE_DRIVER=cubestore`). I found no documented on-demand invalidation API in Core.
- **What TabFree plans.** ADR-007's key is hash(compiled SQL, RLS context, data version). The data version is the Iceberg snapshot ID that our refresh pipeline already knows, so invalidation is *pushed* from refresh events (P6.2) rather than polled.
- **The polling cost.** On Athena, each `sql` refresh key costs a billed query and an active-query slot, against the 200-per-account quota from ADR-007. Multiplied by cubes and sites, that is a real cost. With `every`-based keys, data can instead be up to two minutes stale after a refresh.
- **Isolation.** When RLS values are inlined, the generated SQL differs per viewer, so isolation comes from the SQL text itself. That is the same property our own key gets by hashing the RLS context explicitly.
- **Worth copying:** stale-while-revalidate (`CUBESTORE_QUERY_CACHE_STALE_WHILE_REVALIDATE`, new in 1.7), refresh work kept separate from serving instances, and partitioned incremental rollups with an `update_window`.

## 5. Operating Cube on ECS Fargate

The production topology from [Cube Core deployment](https://docs.cube.dev/cube-core/deployment) needs a minimum of six tasks:

| Component | Count | Sizing per task |
|---|---|---|
| API instances | 2 | 2 vCPU, 3 GB |
| Refresh worker | 1 | ≥ 6 GB (say 2 vCPU) |
| Cube Store router | 1 | 4 vCPU, 6 GB |
| Cube Store workers | 2 | 4 vCPU, 8 GB |

- **Cost.** That totals about 18 vCPU and 34 GB. At us-east-1 x86 on-demand rates (about $0.0405 per vCPU-hour and $0.0044 per GB-hour, consistent with the worked examples on [Fargate pricing](https://aws.amazon.com/fargate/pricing/)), it comes to roughly **$640 a month** before Savings Plans. ADR-009 estimates a whole small install at about $275 a month.
- **Throughput.** The docs put each API instance at about 1 to 10 requests per second.
- **Storage.** Cube Store needs a shared remote directory (S3), with its own compaction and partition tuning.
- **Upgrades.** Near-daily releases mean frequent upgrade decisions.

**Driver maturity.**

- **Athena:** IAM roles, workgroups, `UNLOAD` export.
- **Redshift:** IAM authentication uses `GetClusterCredentialsWithIAM`, which is a provisioned-cluster API ([RedshiftIAMCredentialsProvider.ts](https://github.com/cube-js/cube/blob/master/packages/cubejs-redshift-driver/src/RedshiftIAMCredentialsProvider.ts)). The docs do not mention Redshift Serverless, our default Redshift option. Password authentication against a Serverless endpoint should work over the Postgres protocol (to verify).
- **Open issues:** a GitHub search returns 18 open issues mentioning Athena and 22 mentioning Redshift. That is unremarkable for a repository with about 1,190 open issues.

## Recommendation for ADR-003

**Own service, own cache.** The governed query service executes compiled SQL directly on Athena and Redshift. It caches results under hash(SQL, RLS context, Iceberg snapshot) and invalidates on refresh events. The compiler matches worksheets to Iceberg rollup tables it defines itself.

Cube fails the adoption test ADR-003 itself set ("runs the staged LOD SQL unchanged"). It fails on three counts:

- the SQL API cannot address raw tables;
- pushdown regenerates the SQL;
- rollups and policies attach only to Cube members.

The only way to get Cube's rollups for LODs is to adopt its data model (Tesseract `grain`), which ADR-003 already rejected, and which still lacks Tableau's filter staging, table calcs and blends.

A **hybrid** (Cube serving only simple additive worksheets) would mean two execution paths, two RLS enforcement points and about $640 a month of extra baseline for the easiest queries, which are also the easiest to cache ourselves. I do not recommend it.

**Revisit if:** Cube adds a documented raw-SQL passthrough that keeps its cache and refresh keys, or if a customer wants a Postgres-wire SQL endpoint for third-party BI tools. In the second case Cube could sit *beside* TabFree, fed by Ossie export, not under it.

## What the hands-on spike must verify (1–2 days, local stack)

Run Cube v1.8.x with Cube Store in Docker against the `make dev-up` Trino and Iceberg stack (Cube's Trino driver is to confirm; Athena itself for item 6). Create `sql_table` cubes over two corpus extracts, then check:

1. **Pushdown fidelity.** Send five compiled worksheets (FIXED with a context filter, INCLUDE, EXCLUDE, Top N semi-join, null-safe LOD join), rewritten to cube names, through the SQL API. Capture the SQL Cube emits, diff it against the compiler's output, and compare result sets with Tableau's CSVs.
2. **Rollup hits.** Define a rollup covering one worksheet's grain and check whether any pushdown LOD query is served from it.
3. **RLS placement.** Check where `query_rewrite` and access-policy filters land inside a CTE-staged pushdown query. Does the filter reach the FIXED stage, or only the outer query?
4. **Cache isolation.** Two `securityContext`s with different RLS values, sending identical SQL, must never share a cache entry.
5. **Postgres-wire behaviour.** Prepared statements with `$1` parameters, and behaviour at the 50,000-row limit.
6. **Athena refresh-key cost.** Count refresh-key queries per hour per cube on Athena, and confirm that literal inlining happens in `formatAnsi` as described above.
7. **Fargate fit.** Cube Store memory and compaction with an S3 remote directory under a 50-dashboard burst (stretch goal).

**Decision rule.** If items 1 and 2 fail, close P2.11 as "own service" and amend ADR-003. Adoption would need items 1 to 4 all to pass.
