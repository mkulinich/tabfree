# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions will follow [Semantic Versioning](https://semver.org/) from 1.0.

## [Unreleased]

### Added

- Phase 7: production OpenTofu environment (multi-AZ ECS, Aurora with readers and point-in-time
  recovery, KMS, WAF, Secrets Manager, AWS Backup with a copy to a second region); the API image; a
  Helm chart; a `docker compose` demo; logical backup and restore (`tabfree/backup@1`); a load-test
  harness with release targets; security CI (CodeQL, dependency audits, secret scanning, IaC scanning,
  SBOM, signed images); number and date localisation; keyboard access, screen-reader data tables and
  axe-core checks for components; delta migration for cutover; the documentation site; governance,
  RFC process and roadmap.
- Phase 6: jobs, schedules, refreshes, subscriptions, alerts, webhooks, custom views, sharing,
  comments, collections, lifecycle, embedding, SCIM, usage dashboards, several sites, sets and set
  actions, viz in tooltip and more chart types.
- Phase 5: the migration factory, import, the repair agent, verification and the migration console.
- Phase 4: drafts, validation, the MCP server with OAuth, Edit with Claude and the `tabfree` CLI.
- Phases 0–3: schemas, the Tableau crawler and converters, the semantics compiler and TDVT runner,
  the governed query service, permissions and RLS, the runtime and the portal.
