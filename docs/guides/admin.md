# Administration guide

This guide is for site and server administrators. Settings are `TABFREE_*` environment variables
(`apps/api/src/tabfree_api/settings.py` lists every one with its default); secrets are never put in
files: on AWS they come from Secrets Manager, locally inject them with `secret run`.

## Sites

A server can host several sites, each with its own people, projects, content, Athena workgroup and
audit log. Requests are routed to a site by host name (`TABFREE_SITE_HOSTS`, e.g.
`{"sales.tabfree.example": "sales"}`); other hosts go to `TABFREE_SITE_ID`. Sessions are bound to the
site they were created on.

```bash
uv run python -m tabfree_api.sites list
uv run python -m tabfree_api.sites create sales --name "Sales"
uv run python -m tabfree_api.db create-admin sales admin      # a local administrator; password on stdin
```

## People, groups and site roles

Sign-in is OpenID Connect (`TABFREE_OIDC_ISSUER`, `_CLIENT_ID`, `_CLIENT_SECRET`). Group membership comes
from the `groups` claim (`TABFREE_OIDC_GROUPS_CLAIM`) and RLS attributes from the claims in
`TABFREE_OIDC_ATTRIBUTE_CLAIMS` (used by `USERATTRIBUTE('region')` in models). Identity providers can also
provision people and groups with **SCIM 2.0** at `/scim/v2`, authenticating with a personal access
token of a dedicated site-administrator account; deprovisioning disables the account and keeps its content.

Site roles follow Tableau's: Site Administrator, Creator, Explorer (can publish), Explorer, Viewer and
Unlicensed. New people get the site's default role.

## Permissions and row-level security

Permissions work like Tableau's: rules on projects, dashboards and models grant or deny capabilities to
people and groups; deny wins; locked projects apply their rules to everything inside. The permission
engine is described in ADR-006. Row-level security is declared in semantic models (user filters,
entitlement tables, `USERNAME()`, `ISMEMBEROF()`, `USERATTRIBUTE()`) and compiled into every query,
including Claude's (`run_governed_query`) and ad-hoc explore queries.

## Content lifecycle

Dashboards and models have revisions (restore any of them), owners, certification, data-quality
warnings, tags and collections. Deleting moves content to the recycle bin for the retention period.
Moving content between projects re-evaluates permissions.

## Schedules, refreshes, subscriptions and alerts

Jobs live in the metadata database (ADR-013) and are run by the worker:

```bash
uv run python -m tabfree_api.jobs worker --scheduler --all-sites
```

Schedules have time zones. Job kinds: model refreshes (Iceberg CTAS with a view swap, Redshift swap,
MERGE, materialised views, rollups), subscriptions (email with a PNG or PDF), data alerts, webhooks,
cache warming and the daily usage export. Failed jobs retry with backoff; the admin page lists them.
Mail goes through Amazon SES (`TABFREE_MAIL_BACKEND=ses`).

## Usage

The *Usage* page shows views, people, query latency, cache hits and bytes scanned from the audit log.
For dashboards over usage, set `TABFREE_USAGE_LAKE` (an S3 prefix) and run
`python -m tabfree_api.sites install-usage`: it adds the `tabfree_usage` model and the `site-usage`
dashboard in an administrators-only project, and schedules the daily export.

## Embedding

Register a **connected app** on the admin page, then sign short-lived JWTs (HS256, at most 10 minutes,
single use) in your application with the person's username, scopes and RLS attributes, and load
dashboards with `/embed.js`. Allowed parent origins become `frame-ancestors`.

## Cost guardrails

Each site has an Athena workgroup, a daily scan budget and a concurrency limit (site file `query:`
block, or the site settings). Results are cached by query, filters, the person's RLS context and the
model's data version, which refreshes bump.

## Backups

See [Backup and restore](../operations/backup-restore.md) and [Disaster recovery](../operations/disaster-recovery.md).
