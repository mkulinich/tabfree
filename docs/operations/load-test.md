# Load testing (BUILD_PLAN P7.2)

`python -m tabfree_api.loadtest` simulates people using dashboards the way the portal does: open a
dashboard (the bundle, then every query at once), change a filter half of the time (the filter's
domain, then the affected queries), and wait a few seconds. A **burst** phase first has `--burst` people
open the same dashboard at the same moment, as when a meeting starts.

```bash
uv run python -m tabfree_api.loadtest https://staging.tabfree.example --tokens tokens.txt \
  --users 1000 --minutes 15 --burst 50 --admin-token-file admin.txt --report report.json
```

`tokens.txt` holds one personal access token per line for **test accounts on a staging site** (never
real people's tokens, never production). Spread them across RLS groups so caching is realistic. Keep the
token files out of the repository and delete them after the run.

## Targets

The run exits 1 when a target is missed, so it can gate a release. Override with `--targets file.json`.

| Target | Default | Measured as |
|---|---|---|
| `open_p95_s` | 4.0 s | Whole dashboard open, all queries, 95th percentile |
| `burst_open_p95_s` | 8.0 s | The same during the 50-person burst |
| `query_p95_s` | 2.5 s | One named query |
| `error_rate` | 1 % | Responses with status 400 or more, and network errors |
| `cache_hit_rate` | 60 % | From the server (`/api/admin/usage`), with an administrator's token |

## Athena quotas and settings

At 1,000 people with an 8-second think time, the steady state is about 125 dashboard opens a minute;
with five queries a dashboard and a 60 % hit rate, that is about 250 Athena queries a minute, most of
them short. Before the run:

- **Active DML queries** per account and region default to a few tens for on-demand capacity. Ask AWS for
  an increase to at least 100, or use **provisioned capacity** (DPUs) for the site's workgroup, which
  removes the per-account queue for that workgroup.
- Set the site's `maxConcurrentQueries` (site settings) below the Athena limit, so TabFree queues
  queries itself rather than Athena throttling them.
- Check the workgroup's per-query scan limit and the site's daily scan budget cover the run.
- Warm the cache as production does (the refresh jobs' cache warming) and run once cold for comparison.

## Reading the report

`report.json` has percentiles per step (`list`, `bundle`, `query`, `domain`, `query_filtered`), the
whole-dashboard open times, errors by step and status, throughput, and the server's own numbers (query
count, p50 and p95, cache hit rate, bytes scanned). A slow `bundle` points at the API or database; slow
`query` with a low hit rate points at Athena capacity or models without rollups.

> **Status:** the harness is tested in-process against the sample engine. The 1,000-person run on AWS
> staging has not been done yet.
