# Roadmap

The detailed plan, with task IDs and exit criteria, is [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md). This page
is the short version.

## v1.0: replace Tableau Server for one organisation (Phase 7)

Built: Tableau semantics compiler with TDVT conformance; HTML dashboards with `<tf-*>` components;
governed queries on Athena and Redshift with row-level security; Tableau-compatible permissions;
authoring with Claude over MCP and in the portal; the migration factory with verification and a review
console; schedules, refreshes, subscriptions, alerts, webhooks, embedding, SCIM and several sites;
production infrastructure, backups, a load-test harness, security scanning, accessibility checks.

Still needed for v1.0:

- run the work that needs AWS or the live Tableau Server: extract refreshes on Athena and Redshift, the
  1,000-person load test, the restore drill, the penetration test, the first real migration waves;
- benchmark the result cache against a Cube semantic layer (P2.10) and tune it (P6.10);
- an accessibility audit with screen readers;
- clear the public name and launch the repository (P7.8).

## After v1.0 (candidates, each needs an RFC)

- More engines: Snowflake, BigQuery, Databricks SQL (through the compiler's dialect layer).
- Translated portal text.
- An EventBridge and SQS job path for very large estates (ADR-013 chose the database queue for now).
- Pulse-style metric digests and natural-language questions over governed models.
- A visual editor for people who prefer not to describe changes to Claude.
- Migration from Power BI and Looker into the same factory.
