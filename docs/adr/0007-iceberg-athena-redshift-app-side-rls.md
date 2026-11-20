# ADR-007: Iceberg on S3, Athena by default, Redshift for live and hot sources, RLS in the compiler

## Status

Accepted, 2026-11-20.

P1.16 and P2.10 set the per-source split between Athena and Redshift; they do not reopen the storage format or where RLS is enforced.

## Context

Every `.hyper` extract must pass through Tableau's `hyperd` engine once, and the data then needs an open home in the customer's account. On Athena, Iceberg tables support transactional `MERGE` (engine v3), `OPTIMIZE` and `VACUUM`. Athena costs $5 per TB scanned and nothing when idle, but allows 200 active DML queries per account in us-east-1, queued ones included: 50 users opening eight-tile dashboards at once already exceed it. Athena's result reuse matches only exact repeats within one workgroup, and Lake Formation fine-grained access both switches it off and needs a per-user identity at query time. Redshift Serverless starts at $1.50 per active hour at 4 RPU and handles concurrency, joins and repeat queries better, but bills a 60-second minimum per query.

## Decision

- **Storage:** extracts land as Iceberg v2 tables on S3, registered in the Glue Data Catalog (Hyper `COPY TO` Parquet, then Athena CTAS).
- **Engines:** Athena by default, with one workgroup per site. Redshift Serverless or provisioned serves live warehouse sources and hot or highly concurrent models, chosen per semantic model.
- **Row-level security:** enforced by the compiler ([ADR-003](0003-own-tableau-semantics-compiler.md)), which injects predicates from the viewer's session as bind parameters on both engines; user functions never compile to the engine's `current_user`. Coarse per-site grants, plus Lake Formation data filters or Redshift RLS where a hard tenant boundary needs them, are defence in depth.
- **Caching is mandatory at scale:** a result cache keyed on hash(compiled SQL, RLS context, data version), rollup tables, pre-warming after refresh, and per-site cost guardrails.

## Consequences

### Positive

- Data stays in open tables in the customer's AWS account, with atomic refresh, `MERGE` for incremental refresh and snapshots for rollback.
- No idle cost on the default path.
- RLS behaves identically on both engines and leaves caching possible.

### Negative

- RLS correctness rests on our compiler and governed query service, which makes both security-critical (persona tests in P3.4, RLS parity in P5.5).
- We run Iceberg maintenance and invalidate caches after every refresh (P6.2).
- Athena's interactive latency is unproven; no public p95 benchmark exists.
- Two engines mean two dialects to test.

## Alternatives considered

- **Redshift for everything:** more cost, a 60-second minimum on sporadic traffic, and no benefit for small extracts.
- **Plain Parquet re-created by CTAS into versioned prefixes:** simpler, but no `MERGE` and no snapshots.
- **Lake Formation fine-grained access as the primary RLS:** disables Athena result reuse and needs per-user identities.

## Validation and revisit triggers

- **P1.9:** row-count and aggregate parity between Hyper and Athena for converted extracts.
- **P1.16, P2.10:** p50/p95 latency (cold, warm, concurrent) on representative extracts sets the default engine by data size.
- **P7.2:** 1,000 users with a 50-dashboard burst meet the Athena concurrency and cache hit-rate targets.
- **Revisit if:** a customer mandates engine-level enforcement, Athena's quotas change materially, or Iceberg v3 becomes necessary (Hyper reads only v1 and v2).

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-007), [§7](../BUILD_PLAN.md#7-phases-and-milestones) (P1.9, P2.7, P2.10) and [§11](../BUILD_PLAN.md#11-risks-and-mitigations) (R4)
- [Research report: storage and engines](../research/research-report.md#a-thin-governed-core-a-json-spec-and-an-owned-compiler-anchor-the-build); notes: [data layer](../research/notes/aws_architecture_ai_authoring.md#1-data-layer-hyper-conversion-iceberg-vs-parquet-athena-vs-redshift-caching-query-apis-rls-refresh-scheduling)
- Athena: [quotas](https://docs.aws.amazon.com/general/latest/gr/athena.html), [result reuse](https://docs.aws.amazon.com/athena/latest/ug/reusing-query-results.html), [MERGE](https://docs.aws.amazon.com/athena/latest/ug/merge-into-statement.html), [pricing](https://aws.amazon.com/athena/pricing/)
- [Redshift pricing](https://aws.amazon.com/redshift/pricing/), [Redshift RLS](https://docs.aws.amazon.com/redshift/latest/dg/t_rls.html), [Lake Formation data filters](https://docs.aws.amazon.com/lake-formation/latest/dg/data-filtering.html)
