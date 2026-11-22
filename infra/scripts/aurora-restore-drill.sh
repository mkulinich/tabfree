#!/usr/bin/env bash
# Aurora restore drill (BUILD_PLAN P7.1): restore the production cluster to a point in time into a
# throwaway cluster, check it, and delete it. Run monthly; record the result in the DR log.
#
#   AWS_PROFILE=tabfree-prod infra/scripts/aurora-restore-drill.sh tabfree-prod-metadata 2027-05-01T06:00:00Z
#
# Needs: the AWS CLI with rds:RestoreDBClusterToPointInTime, rds:CreateDBInstance, rds:Delete*, and
# network access to the private subnets (run it from a bastion task or CloudShell VPC environment).
# Not run yet: written without AWS access.
set -euo pipefail

source_cluster="${1:?source cluster identifier}"
restore_time="${2:?UTC time, e.g. 2027-05-01T06:00:00Z}"
drill="${source_cluster}-drill-$(date -u +%Y%m%d%H%M)"

echo "restoring ${source_cluster} at ${restore_time} into ${drill}"
subnets=$(aws rds describe-db-clusters --db-cluster-identifier "$source_cluster" \
  --query 'DBClusters[0].DBSubnetGroup' --output text)
groups=$(aws rds describe-db-clusters --db-cluster-identifier "$source_cluster" \
  --query 'DBClusters[0].VpcSecurityGroups[].VpcSecurityGroupId' --output text)
started=$(date +%s)
aws rds restore-db-cluster-to-point-in-time \
  --source-db-cluster-identifier "$source_cluster" \
  --db-cluster-identifier "$drill" \
  --restore-to-time "$restore_time" \
  --db-subnet-group-name "$subnets" \
  --vpc-security-group-ids $groups \
  --serverless-v2-scaling-configuration MinCapacity=0.5,MaxCapacity=4 \
  --tags Key=Purpose,Value=restore-drill >/dev/null
aws rds create-db-instance --db-instance-identifier "${drill}-1" --db-cluster-identifier "$drill" \
  --engine aurora-postgresql --db-instance-class db.serverless >/dev/null
aws rds wait db-instance-available --db-instance-identifier "${drill}-1"
echo "restored in $(( $(date +%s) - started )) seconds (the recovery time objective is 1 hour)"

endpoint=$(aws rds describe-db-clusters --db-cluster-identifier "$drill" --query 'DBClusters[0].Endpoint' --output text)
secret=$(aws rds describe-db-clusters --db-cluster-identifier "$source_cluster" --query 'DBClusters[0].MasterUserSecret.SecretArn' --output text)
# The check: the schema revision, and row counts of the main tables, as TabFree's backup tool reads them.
TABFREE_DB_HOST="$endpoint" TABFREE_DB_USER="$(aws secretsmanager get-secret-value --secret-id "$secret" --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["username"])')" \
TABFREE_DB_PASSWORD="$(aws secretsmanager get-secret-value --secret-id "$secret" --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')" \
  python -m tabfree_api.backup export "/tmp/${drill}.jsonl.gz"
python -m tabfree_api.backup verify "/tmp/${drill}.jsonl.gz"
rm -f "/tmp/${drill}.jsonl.gz"

echo "deleting ${drill}"
aws rds delete-db-instance --db-instance-identifier "${drill}-1" --skip-final-snapshot >/dev/null
aws rds wait db-instance-deleted --db-instance-identifier "${drill}-1"
aws rds delete-db-cluster --db-cluster-identifier "$drill" --skip-final-snapshot >/dev/null
echo "drill complete"
