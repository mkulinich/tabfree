# Governance

TabFree is an Apache-2.0 project (ADR-010). This document says who decides what and how.

## Roles

- **Contributors**: anyone who opens an issue, a pull request or an RFC, or reviews one.
- **Committers**: may approve and merge pull requests in the areas they know. Nominated by a maintainer
  after sustained, good-quality contributions; confirmed by lazy consensus of the maintainers (no
  objection within 7 days).
- **Maintainers**: set direction, run releases, handle security reports, and decide when consensus
  fails. Listed in [MAINTAINERS.md](MAINTAINERS.md). New maintainers are chosen by a two-thirds vote of
  the existing ones.

Inactive committers and maintainers (no reviews or commits for 12 months) move to emeritus, and can
come back by asking.

## Decisions

- Most changes: a pull request with one approving review from a committer of that area, green CI, and
  `make check` passing. Two reviews for security-sensitive code (authentication, permissions, RLS, the
  sandbox, the query compiler's handling of user input, the migration tooling's contact with Tableau).
- Architecture: an ADR in `docs/adr/` in the same pull request. An ADR that supersedes another says so.
- Larger changes (a new public API or schema version, a new dependency on an external service, a change
  to the security model, anything that changes the rules in CONTRIBUTING.md): an **RFC** first
  ([process](docs/rfcs/README.md)).
- When consensus fails, maintainers decide by simple majority. Votes and their reasons are recorded on
  the RFC or pull request.

## Releases

Maintainers cut releases from `main` following the [release checklist](docs/operations/release-checklist.md).
Versions follow semantic versioning from 1.0; schema versions (`tabfree/dashboard@N`) change only with a
migration path.

## Code of conduct

Everyone in the project follows the [Code of Conduct](CODE_OF_CONDUCT.md). Maintainers enforce it.

## Changes to this document

By RFC, accepted by a two-thirds vote of the maintainers.
