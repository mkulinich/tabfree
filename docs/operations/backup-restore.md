# Backup and restore runbook

| What | How | Kept | Recovery point |
|---|---|---|---|
| Metadata database | Aurora continuous backup (point-in-time recovery) | 14 days | 5 minutes |
| Metadata database | AWS Backup daily snapshot, copied to the DR region | 35 days | 24 hours |
| Metadata database | AWS Backup monthly snapshot (cold after 30 days), copied | 365 days | 1 month |
| Lake (Iceberg tables, extracts) | S3 versioning; source systems can rebuild it (refresh jobs) | per bucket rules | – |
| Raw zone (Tableau downloads, ground truth) | S3 versioning, archived at cutover (P7.7) | until decommissioning + 1 year | – |
| Configuration | This repository and OpenTofu state (versioned S3) | always | – |

The metadata database is what matters: content, revisions, permissions, users, schedules, jobs,
audit. The lake is derived data.

## Restore to a point in time (most incidents)

Use for accidental deletes or a bad migration. Restoring creates a **new** cluster; then switch to it.

1. Stop writes: scale the API and worker services to 0 (`aws ecs update-service --desired-count 0`).
2. Restore: `aws rds restore-db-cluster-to-point-in-time --source-db-cluster-identifier tabfree-prod-metadata --db-cluster-identifier tabfree-prod-metadata-restored --restore-to-time <UTC>` plus a `db.serverless` instance (see `infra/scripts/aurora-restore-drill.sh`).
3. Check it: `python -m tabfree_api.backup export` from a one-off task against the restored cluster,
   then `verify`; compare row counts with the incident report.
4. Switch: either rename the clusters (old to `-broken`, restored to the original name, so the
   endpoint stays the same), or set the app's `db_host` to the new endpoint and `tofu apply`.
5. Scale the services back up. Keep the broken cluster until the incident review is done.

For a single deleted dashboard or model, prefer the **recycle bin** (30 days, `/admin/recycle-bin`)
or **revision history** (restore an older revision); no database restore needed.

## Restore drill (monthly, P7.1)

`infra/scripts/aurora-restore-drill.sh <cluster> <time>` restores into a throwaway cluster, times it,
exports and verifies every table with TabFree's backup tool, and deletes it. Record the date, the
restore time and the result in the DR log. The logical backup format is tested in CI: every table is
restored row for row into SQLite and Postgres (`apps/api/tests/test_backup.py`).

## Logical backups (moves, self-hosting)

```bash
python -m tabfree_api.backup export tabfree-$(date -u +%F).jsonl.gz   # every table, checksummed
python -m tabfree_api.backup verify tabfree-2027-05-01.jsonl.gz
python -m tabfree_api.backup restore tabfree-2027-05-01.jsonl.gz      # into an empty, migrated database
```

The file contains password and token hashes and webhook secrets: store it encrypted, like the database.
