# Disaster recovery plan

**Targets.** Recovery time objective (RTO): 1 hour for an AZ or database failure, 8 hours for the loss of
a region. Recovery point objective (RPO): 5 minutes within the region (Aurora point-in-time recovery),
24 hours across regions (the daily backup copy).

| Failure | What happens | What to do |
|---|---|---|
| One task crashes | ECS replaces it; the load balancer stops sending it traffic | nothing |
| An AZ goes down | API tasks in other AZs keep serving (at least two, in different AZs); each AZ has its own NAT gateway; Aurora fails over to the reader in about 30 seconds | watch; autoscaling adds tasks |
| A bad deploy | The deployment circuit breaker rolls back failing tasks | if it passed health checks but misbehaves: set the previous image digest, `tofu apply` |
| Data deleted or corrupted | – | recycle bin or revision restore; else point-in-time restore (backup-restore.md) |
| Athena or Redshift unavailable | Dashboards show query errors; cached results still serve | nothing to restore; status page |
| Region lost | – | the regional recovery below |

## Regional recovery

1. Declare the incident and freeze changes (announce read-only).
2. In the DR region, restore the latest metadata snapshot from the DR backup vault (AWS Backup
   console or `aws backup start-restore-job`) into a new Aurora cluster.
3. Apply `infra/terraform/envs/production` with `region` set to the DR region and the restored
   cluster imported (`tofu import module.metadata_db.aws_rds_cluster.this <id>`), or with a new
   environment copy pointing at it.
4. The lake: Iceberg tables in the primary region are unavailable. Point models at replicated data if
   S3 cross-region replication is enabled for the lake bucket (recommended for regulated data), or run
   the refresh jobs to rebuild from the sources (P6.2), then bump data versions.
5. Switch DNS to the DR load balancer. Unsubscribe-link and embed signing keys are regenerated in the
   new region: embedded dashboards need their connected apps re-keyed.
6. When the primary region returns, fail back the same way during a quiet window.

Run a regional recovery exercise once a year, from a copied snapshot into a sandbox account.
