#!/usr/bin/env bash
# Bootstraps the TabFree local stack. Idempotent: safe to run again.
#   1. S3 buckets tabfree-lake and tabfree-raw            (object store)
#   2. SQS queue tabfree-jobs with a dead-letter queue    (Moto)
#   3. SES sender domain identity                         (Moto)
#   4. Iceberg namespace tabfree_dev + sample table       (Trino, sample-data.sql)
# Endpoints come from AWS_ENDPOINT_URL_<SERVICE> variables set in compose.yaml.
# Afterwards it writes /tmp/bootstrap.done (the healthcheck) and idles.
set -euo pipefail

TRINO_URL="${TRINO_URL:-http://trino:8080}"
TRINO_USER="${TRINO_USER:-tabfree-bootstrap}"
SES_SENDER_DOMAIN="${SES_SENDER_DOMAIN:-example.com}"
SQL_FILE="${SQL_FILE:-/bootstrap/sample-data.sql}"

log() { printf '[bootstrap] %s\n' "$*"; }
die() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

for tool in aws curl jq; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool '$tool' not found in the image"
done
rm -f /tmp/bootstrap.done

# --- 1. Buckets -------------------------------------------------------------
for bucket in tabfree-lake tabfree-raw; do
  if aws s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
    log "bucket s3://$bucket already exists"
  else
    aws s3api create-bucket --bucket "$bucket" >/dev/null
    log "created bucket s3://$bucket"
  fi
done

# --- 2. SQS ----------------------------------------------------------------
dlq_url=$(aws sqs create-queue --queue-name tabfree-jobs-dlq --query QueueUrl --output text)
dlq_arn=$(aws sqs get-queue-attributes --queue-url "$dlq_url" \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
queue_attrs=$(jq -nc --arg arn "$dlq_arn" \
  '{VisibilityTimeout: "300",
    RedrivePolicy: ({deadLetterTargetArn: $arn, maxReceiveCount: "5"} | tojson)}')
aws sqs create-queue --queue-name tabfree-jobs --attributes "$queue_attrs" >/dev/null
log "SQS queues ready: tabfree-jobs (dead-letter queue tabfree-jobs-dlq)"

# --- 3. SES ----------------------------------------------------------------
# Moto rejects mail from unverified senders; a verified domain covers every
# address at that domain. verify-domain-identity is idempotent.
aws ses verify-domain-identity --domain "$SES_SENDER_DOMAIN" >/dev/null
log "SES sender domain verified: $SES_SENDER_DOMAIN (send from e.g. noreply@$SES_SENDER_DOMAIN)"

# --- 4. Iceberg via Trino ---------------------------------------------------
# Runs one statement through Trino's HTTP client protocol and prints any
# result rows as tab-separated values.
trino_exec() {
  local sql="$1" resp next
  resp=$(curl -fsS --retry 5 --retry-delay 2 -X POST "$TRINO_URL/v1/statement" \
    -H "X-Trino-User: $TRINO_USER" -H "X-Trino-Source: tabfree-bootstrap" \
    --data-binary "$sql") || die "could not submit statement to Trino at $TRINO_URL"
  while :; do
    if jq -e '.error' >/dev/null <<<"$resp"; then
      die "Trino query failed: $(jq -r '.error.message' <<<"$resp")"
    fi
    jq -r '.data[]? | map(tostring) | @tsv' <<<"$resp"
    next=$(jq -r '.nextUri // empty' <<<"$resp")
    [ -z "$next" ] && break
    resp=$(curl -fsS --retry 5 --retry-delay 2 "$next" -H "X-Trino-User: $TRINO_USER") \
      || die "lost contact with Trino while polling $next"
  done
}

# Splits a SQL file into statements (each ends with ";" at end of line) and
# runs them in order.
run_sql_file() {
  local stmt="" line
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*(--.*)?$ ]] && continue
    stmt+="$line"$'\n'
    if [[ "$line" =~ \;[[:space:]]*$ ]]; then
      stmt="${stmt%"${stmt##*[![:space:]]}"}"   # trim trailing whitespace
      stmt="${stmt%;}"                           # drop the final ";"
      trino_exec "$stmt" >/dev/null
      stmt=""
    fi
  done <"$1"
  [[ -z "${stmt//[[:space:]]/}" ]] || trino_exec "$stmt" >/dev/null
}

run_sql_file "$SQL_FILE"
rows=$(trino_exec "SELECT count(*) FROM iceberg.tabfree_dev.sample_orders")
log "Iceberg table iceberg.tabfree_dev.sample_orders ready ($rows rows)"

touch /tmp/bootstrap.done
log "done; the local stack is ready"
exec sleep infinity
