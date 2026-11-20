# TabFree local development stack

A `docker compose` stack that stands in for the AWS services TabFree uses (ADR-007, ADR-009). This is task P0.3.

| Service | Stands in for | Image | Host endpoint |
|---|---|---|---|
| `postgres` | Aurora PostgreSQL (metadata DB) | `postgres:17.12-trixie` | `localhost:5432` |
| `objectstore` | S3 | `rustfs/rustfs:1.0.6` | S3 API `http://localhost:9000`, console `http://localhost:9001` |
| `iceberg-rest` | Glue Data Catalog | `apache/iceberg-rest-fixture:1.10.1` | `http://localhost:8181` |
| `trino` | Athena engine v3 | `trinodb/trino:484` | `http://localhost:8080` (web UI at `/ui/`) |
| `aws-emulator` | SQS and SES | `motoserver/moto:5.2.5` | `http://localhost:4566` (dashboard at `/moto-api/`) |
| `bootstrap` | (setup job) | `amazon/aws-cli:2.41.2` | none |

> **Status:** the stack was written without Docker available, so it **has not been booted yet**. The YAML parses, and the image tags, entrypoints, health endpoints and config property names were checked against the registries and upstream sources. The first person to run it should treat it as untested and fix anything that breaks.

## Start and stop

From the repository root (`make dev` wraps the first command):

```sh
docker compose -f infra/docker/compose.yaml up -d --wait   # returns once the bootstrap has finished
docker compose -f infra/docker/compose.yaml ps             # status and health
docker compose -f infra/docker/compose.yaml logs -f bootstrap
docker compose -f infra/docker/compose.yaml down           # stop, keep data
docker compose -f infra/docker/compose.yaml down -v        # stop and delete all local data
```

Needs Docker Compose v2.20 or later (for `depends_on.restart`). All host ports bind to `127.0.0.1` only. To move a port, set `TABFREE_PG_PORT`, `TABFREE_S3_PORT`, `TABFREE_S3_CONSOLE_PORT`, `TABFREE_ICEBERG_REST_PORT`, `TABFREE_TRINO_PORT` or `TABFREE_AWS_PORT` in your shell.

Trino is capped at 4 GiB of memory (`mem_limit`). Lower it in `compose.yaml` on a small machine.

## What the bootstrap creates

The `bootstrap` service ([`bootstrap/bootstrap.sh`](bootstrap/bootstrap.sh)) is idempotent. It creates:

- S3 buckets `tabfree-lake` and `tabfree-raw`
- SQS queue `tabfree-jobs` with dead-letter queue `tabfree-jobs-dlq` (after 5 receives); these are placeholders until the worker design (P6.1) names the real queues
- a verified SES sender domain, `example.com`, so the app can send from any `…@example.com` address
- the Iceberg namespace `tabfree_dev` at `s3://tabfree-lake/warehouse/tabfree_dev`
- the sample table `iceberg.tabfree_dev.sample_orders`: 12 made-up orders, Iceberg v2, Parquet, partitioned by month ([`bootstrap/sample-data.sql`](bootstrap/sample-data.sql))

When it has finished, it stays up and reports healthy, which is what `up --wait` waits for. If it fails, it exits non-zero and `up --wait` fails; read `logs bootstrap`.

Moto keeps its state in memory. Compose re-runs the bootstrap when you `restart aws-emulator`. If Moto restarts any other way, run `docker compose -f infra/docker/compose.yaml restart bootstrap`. Postgres, object-store data and the Iceberg catalog persist in named volumes (`tabfree_pgdata`, `tabfree_objectstore-data`, `tabfree_iceberg-catalog`).

## Local credentials (not secrets)

These are fixed, public placeholders for this local stack only. They protect nothing, and they must never be used anywhere else.

| What | Value |
|---|---|
| Postgres | user `tabfree`, password `tabfree-local-not-a-secret`, database `tabfree` |
| Postgres URL | `postgresql://tabfree:tabfree-local-not-a-secret@localhost:5432/tabfree` |
| S3 / object-store console | access key `tabfree-local`, secret key `tabfree-local-not-a-secret` |
| SQS / SES (Moto) | any access key and secret; region `us-east-1` |
| Trino | no password; any user name |

## Connecting

**Trino CLI.** The image includes the CLI:

```sh
docker compose -f infra/docker/compose.yaml exec trino trino --catalog iceberg --schema tabfree_dev
```

```sql
SELECT region, sum(sales) AS sales, sum(profit) AS profit
FROM sample_orders
GROUP BY region
ORDER BY sales DESC;
```

From the host, use any Trino client against `http://localhost:8080`, for example `trino --server http://localhost:8080 --catalog iceberg --schema tabfree_dev`, or the Python `trino` package.

**AWS SDKs (boto3 1.28+, AWS CLI 2.13+).** Point each service at the emulators with the standard service-specific endpoint variables, and the app code needs no local-only branches. With a custom S3 endpoint, botocore uses path-style addressing automatically.

```sh
export AWS_ACCESS_KEY_ID=tabfree-local AWS_SECRET_ACCESS_KEY=tabfree-local-not-a-secret AWS_REGION=us-east-1
export AWS_ENDPOINT_URL_S3=http://localhost:9000
export AWS_ENDPOINT_URL_SQS=http://localhost:4566
export AWS_ENDPOINT_URL_SES=http://localhost:4566
export AWS_ENDPOINT_URL_SESV2=http://localhost:4566
aws s3 ls s3://tabfree-lake/warehouse/ --recursive
aws sqs list-queues
```

Mail "sent" through Moto is not delivered. You can see it in `http://localhost:4566/moto-api/data.json` (under `ses`).

**Iceberg REST catalog.** For example, PyIceberg: `uri=http://localhost:8181`, plus `s3.endpoint=http://localhost:9000`, `s3.path-style-access=true` and the S3 keys above. The catalog does not hand out storage endpoints or credentials, so clients use their own.

## Image choices (checked 2026-11-20)

| Need | Choice | Why | Rejected |
|---|---|---|---|
| Object store | **RustFS 1.0.6** | Apache-2.0; 1.0 line released in 2026 with frequent releases; a single process and volume, configured like MinIO; S3 API plus a web console; the image ships `curl`, and `/health` is a documented endpoint | **MinIO:** `minio/minio` no longer exists on Docker Hub (404), and Quay requires auth; community images stopped in October 2025. **Silo** (MinIO fork): AGPL. **Garage v2.5.0:** AGPL, and needs cluster-layout setup. **SeaweedFS 4.54:** Apache-2.0 and viable, but more ports and components. **Versity v1.11.0:** viable; a POSIX gateway |
| Iceberg REST catalog | **apache/iceberg-rest-fixture 1.10.1** | Apache Iceberg's reference implementation of the REST spec, used in Iceberg, PyIceberg and Trino tests; set up only through environment variables; never pushes endpoints or credentials to clients, so host and container clients both work | **Lakekeeper v0.15.1:** production-grade, but its documented Trino setup relies on STS credential vending (needs a MinIO-like STS) and a management-API bootstrap. **Apache Polaris 1.9.0:** needs realm, principal and grant bootstrapping that we don't need locally |
| Engine | **Trino 484** | Latest release (2026-10-16); Athena engine v3 is Trino-based | — |
| SQS + SES | **Moto 5.2.5** | Apache-2.0; one container emulates SQS, SES and SESv2; no account or token needed | **LocalStack:** since 2026.03 every release needs an auth token tied to an account. **ElasticMQ:** SQS only |
| Metadata DB | **Postgres 17.12** | Same major version as the Aurora PostgreSQL 17 we deploy; the Debian (glibc) variant for collation parity with Aurora | Alpine variant (musl collations differ) |
| Bootstrap | **aws-cli 2.41.2** | Has `bash`, `jq`, `curl` and the AWS CLI | — |

Caveats:
- The fixture's newest versioned tag is 1.10.1 (December 2025). Only `latest` is newer, and we don't use `latest`. The fixture is meant for testing; its SQLite store is fine for one developer, but it is not a shared catalog.
- RustFS is younger than MinIO, and Trino only tests against AWS S3 and MinIO. If you hit S3 compatibility problems, SeaweedFS (`chrislusf/seaweedfs:4.54`, `weed mini`) is the fallback.

## Local Trino vs Athena engine v3

The stack is close enough for compiler and query-service development. It is not a fidelity oracle: run dialect tests against real Athena (nightly, in the sandbox account).

- **Names.** Locally the catalog is `iceberg` and the schema is `tabfree_dev`. In Athena the catalog is `AwsDataCatalog` and the database comes from Terraform (`tabfree_sandbox`). Don't hard-code catalog names in generated SQL; set them on the session (Trino) or in `QueryExecutionContext` (Athena).
- **Versions.** Athena engine v3 is a Trino derivative on its own schedule. Functions, planner behaviour and error messages can differ from Trino 484. Use SQLGlot's `athena` dialect for what you generate and `trino` only for local execution.
- **DDL.** Athena creates Iceberg tables with Hive-style DDL (`PARTITIONED BY (month(order_date))`, `LOCATION`, `TBLPROPERTIES ('table_type'='ICEBERG')`). Trino uses `WITH (partitioning = ARRAY[...])`. Maintenance is `OPTIMIZE … REWRITE DATA USING BIN_PACK` and `VACUUM` in Athena, and `ALTER TABLE … EXECUTE optimize` / `expire_snapshots` / `remove_orphan_files` in Trino.
- **Parameters.** Athena takes positional `?` parameters through `ExecutionParameters` or `PREPARE`/`EXECUTE`. Trino uses `PREPARE`/`EXECUTE … USING` or client-side parameters.
- **Results and limits.** Athena writes results to S3 and pages them through `GetQueryResults`, applies the workgroup's bytes-scanned cutoff, a 30-minute DML timeout and a 262,144-byte query-text limit, and offers result reuse. None of this exists locally.
- **Security.** Locally there is no IAM, no Lake Formation and no encryption. App-side RLS (ADR-007) behaves the same in both places; IAM and Lake Formation backstops don't exist locally.
- **Storage.** No bucket versioning, lifecycle rules or SSE locally, unlike the AWS buckets.
- **Email and queues.** Moto doesn't deliver mail and doesn't enforce SES sandbox limits or sending quotas. Its SQS covers the common API but not every edge case of visibility timeouts or FIFO ordering.
