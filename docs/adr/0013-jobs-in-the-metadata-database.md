# ADR-013: Run scheduled work from a job queue in the metadata database

## Status

Proposed, 2026-11-22.

Deciding task: P7.2 (load test). If the queue's claim rate or lock contention limits refreshes and subscriptions at 1,000 users, the queue moves to SQS behind the same `JobQueue` interface, fed by EventBridge Scheduler, as BUILD_PLAN P6.1 also allows.

## Context

Phase 6 adds work that runs on a schedule or after an event: extract refreshes (P6.2), subscriptions (P6.3), data-driven alerts (P6.4), cache warming (P6.10) and webhook deliveries (P6.9). BUILD_PLAN P6.1 names two options: EventBridge Scheduler → SQS → workers, or a Postgres-backed queue for self-hosters.

- Every job reads and writes the metadata database anyway (content, permissions, subscriptions, job history), and the history must be queryable from the portal.
- Self-hosters (P7.5, `docker compose`) have Postgres but no EventBridge or SQS. Two code paths would mean two sets of bugs.
- Postgres has `SELECT … FOR UPDATE SKIP LOCKED`, which lets many workers claim jobs without blocking one another. Aurora PostgreSQL (ADR-009) supports it.
- Tableau schedules are wall-clock schedules in a time zone ("daily at 06:00 Europe/London"), which a cron-like loop over a `schedules` table expresses directly; daylight-saving changes need `zoneinfo`, not fixed UTC offsets.

## Decision

We will keep schedules and jobs in the metadata database:

- `schedules` hold named, shared schedules (hourly, daily, weekly, monthly, with a time and a time zone), like Tableau's. Refresh tasks, subscriptions and alerts point at a schedule.
- A **scheduler** loop enqueues one job per task on a due schedule and moves the schedule's next run forward. Several schedulers may run: advancing `next_run_at` is a conditional update, so a schedule fires once.
- **Workers** claim jobs with `FOR UPDATE SKIP LOCKED` (SQLite in tests and development has one writer), with a lease that expires, so a crashed worker's job runs again. Failures retry with exponential backoff up to `max_attempts`; the last failure notifies the owner.
- Every job's status, attempts, error and result stay in `jobs` as the job history the portal shows.
- Workers run as their own ECS service (`python -m tabfree_api.jobs worker`), or inside the API process in development (`TABFREE_JOBS_INLINE=1`).

## Consequences

### Positive

- One code path for AWS and self-hosting; job history is a query, and jobs are in the same transaction scope as the data they change.
- No new AWS services to provision before Phase 7.

### Negative

- The metadata database carries the queue's write load; old jobs need pruning (a retention setting).
- Exactly-once is not guaranteed: a job whose lease expires may run twice, so handlers must be idempotent (refresh swaps, subscription sends keyed by run).
