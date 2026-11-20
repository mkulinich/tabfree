# Contributing to TabFree

Thanks for helping build an open replacement for Tableau Server. This guide covers how work is organised, how to set up a development environment, and what every change needs before it merges.

## How work is organised

- [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) is the roadmap. Every task has an ID such as `P1.4`. Reference it in your branch name (`p1.4-ground-truth-capture`), in commit messages and in the PR title.
- Significant technical decisions are recorded as ADRs in [docs/adr/](docs/adr/). If a change contradicts an ADR, propose a new or superseding ADR in the same PR.
- Phases advance on their exit criteria, not on dates.

## Development setup

```bash
make setup   # uv sync --all-packages && npm install
make check   # what CI runs: lint, type-check, tests
```

- **Python:** 3.12+, managed by [uv](https://docs.astral.sh/uv/). It is a workspace: each package under `packages/` or `apps/` has its own `pyproject.toml`, and they all share one `uv.lock`. Use the src layout. Distributions are named `tabfree-*` and modules `tabfree_*`.
- **TypeScript:** npm workspaces, strict compiler settings, ESM, vitest, eslint with typescript-eslint.
- **Infrastructure:** OpenTofu/Terraform under `infra/terraform`. `make infra-validate` checks formatting and validates without touching AWS.
- **Local stack:** `make dev-up` starts Postgres, Trino with an Iceberg catalog, and an S3 emulator. It needs Docker.

## Every change needs

1. Tests. New behaviour gets unit tests; bug fixes get a regression test. For conversion and compiler bugs, also add a corpus case.
2. `make check` passing locally.
3. Docs updated when behaviour, commands or schemas change.
4. Schema changes are deliberate. The JSON Schemas in `packages/schemas` are the contract between migration, compiler, runtime and AI tooling. Breaking changes bump the `schema` version string (for example `tabfree/dashboard@0` → `@1`) and update the examples.

## Rules that protect users

- **Dashboards never contain SQL or credentials.** All data flows through named queries in `tabfree.json`, which the governed query service compiles and runs with the viewer's permissions and row-level security.
- **Tableau files are untrusted input.** Parse XML with `defusedxml`. Never execute anything found in a workbook, extract or corpus file.
- **Source servers are read-only.** Migration tooling must never modify the Tableau Server it reads from.
- **No secrets in the repo.** Read credentials from environment variables, ideally injected by a secret manager. Never commit `.env` files, tokens, customer workbooks or extracts. `corpus/private/` is gitignored for local customer files.
- **Data reaching an LLM is untrusted.** Values, column names and workbook text passed to Claude are fenced as data, never as instructions.

## Dependency hygiene

- Add dependencies only when needed, and prefer permissive licences (Apache-2.0, MIT, BSD). AGPL, SSPL, BSL and other non-OSI licences are not accepted in shipped code.
- Commit lockfiles (`uv.lock`, `package-lock.json`).
- When adding or upgrading packages, prefer versions that have been published for at least a week: `uv lock --exclude-newer <date>` and `npm install --before <date>`.

## Using Claude Code

The repository's [CLAUDE.md](CLAUDE.md) gives Claude Code the commands, layout and rules above. Claude-authored changes go through the same review and checks as any other change.

## Sign-off

Sign off your commits (`git commit -s`) to certify the [Developer Certificate of Origin](https://developercertificate.org/) for your contribution.
