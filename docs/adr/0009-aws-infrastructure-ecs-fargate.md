# ADR-009: AWS infrastructure on ECS Fargate and managed services, deployed with OpenTofu/Terraform

## Status

Accepted, 2026-11-20.

## Context

TabFree runs in the customer's own AWS account. Small installs must be cheap and need no cluster to operate, each AI authoring session needs its own task ([ADR-008](0008-sandboxed-claude-agent-runtime.md)), and contributors must be able to run the stack without AWS. Groups drive permissions, so group membership must be provisioned reliably, yet Cognito documents no SCIM support.

## Decision

- **Compute:** ECS Fargate for the API, MCP server, workers, Playwright renderers and author sandboxes.
- **State:** Aurora PostgreSQL for metadata, revisions and audit; S3 for raw downloads, the lake and query results.
- **Jobs and email:** EventBridge Scheduler → SQS → workers; SES for subscriptions. A Postgres-backed queue replaces SQS for self-hosters (P6.1).
- **Identity:** Cognito as the OIDC/SAML broker by default. The API trusts any OIDC issuer, so Okta, Entra ID or Keycloak can connect directly, and Keycloak is the self-hosted alternative to Cognito. TabFree implements its own SCIM 2.0 endpoint.
- **Supporting services:** Secrets Manager, KMS and CloudWatch.
- **Delivery:** OpenTofu/Terraform modules; `docker compose` for local development (Postgres; Trino, an Iceberg REST catalog and MinIO standing in for Athena; LocalStack for SQS and SES); a Helm chart for EKS later (P7.5).

**Cost estimate, not a quote:** a bottom-up model at us-east-1 list prices puts infrastructure at about $250–350 a month for ~50 users (point estimate about $275) and about $1,400 a month for ~1,000 users on the Athena path. Claude tokens are extra: about $150–450 and $1,400–2,900 a month respectively. Optional Redshift Serverless adds about $800 a month at the larger size. The model leaves out CloudWatch, KMS, Secrets Manager, backups, WAF and data transfer out, and its usage assumptions are unvalidated.

## Consequences

### Positive

- No cluster to run; managed services billed by use.
- One task per agent session falls out of Fargate's model.
- The whole stack runs locally without an AWS account.

### Negative

- NAT gateways, load balancers and the Aurora floor dominate the cost of small installs.
- SQS, EventBridge, SES and Cognito need local or non-AWS substitutes behind interfaces.
- Trino is not Athena, so nightly dialect tests against real Athena and Redshift are required.
- We build and secure SCIM ourselves, and Kubernetes shops wait for the Helm chart.

## Alternatives considered

- **EKS first:** heavier to operate for small installs.
- **AWS CDK:** workable, but AWS-only and with less open-source reach than Terraform/OpenTofu.
- **Keycloak as the default broker:** SCIM has been supported since 26.8, but it is one more service to run; kept as the alternative.

## Validation and revisit triggers

- **P0.3, P0.4:** `make dev` boots the local stack, and Terraform builds the AWS sandbox account.
- **Cost:** re-estimate from real sandbox billing after Phase 2.
- **P7.1, P7.2:** high availability with a tested restore, and a 1,000-user load test.
- **Revisit if:** adopters need Kubernetes before P7.5, measured cost materially exceeds the estimate, or Cognito adds SCIM.

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-009), [§4](../BUILD_PLAN.md#4-target-architecture) and [§5](../BUILD_PLAN.md#5-tech-stack)
- [Research report: cost model](../research/research-report.md#a-thin-governed-core-a-json-spec-and-an-owned-compiler-anchor-the-build); notes: [platform services and cost](../research/notes/aws_architecture_ai_authoring.md#4-platform-services-auth-authorization-model-metadata-audit-scheduling-email-snapshots-alerts-embedding-deployment-and-cost)
- Pricing: [Fargate](https://aws.amazon.com/fargate/pricing/), [Aurora](https://aws.amazon.com/rds/aurora/pricing/), [Cognito](https://aws.amazon.com/cognito/pricing/), [EventBridge](https://aws.amazon.com/eventbridge/pricing/), [SES](https://aws.amazon.com/ses/pricing/)
- [Keycloak 26.8 release](https://www.keycloak.org/2026/10/keycloak-2680-released)
