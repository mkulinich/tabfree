# ADR-010: License the project under Apache-2.0

## Status

Accepted, 2026-11-20.

## Context

TabFree must be free and open source, acceptable to enterprises and embedders, and able to build on Tableau's open releases and the wider ecosystem. Apache-2.0 is the license of Superset, Cube, OpenFGA and the Hyper API, and it carries an explicit patent grant. The key dependencies are compatible: the Hyper API, ECharts, Perspective and Playwright are Apache-2.0, SQLGlot is MIT and Vega-Lite is BSD-3. Some nearby code is not: AG Grid Enterprise is commercial, cwtwb is AGPL, and Tableau's repository data dictionary has no license file. The TypeScript Claude Agent SDK is under Anthropic's Commercial Terms of Service.

The working name raises a separate risk. Salesforce's trademark guidelines, which cover Tableau, forbid using "any recognizable portion" or an abbreviation of its marks in a product or domain name.

## Decision

- License the whole repository under Apache-2.0, with `LICENSE` and `NOTICE` at the root (P0.1).
- Core dependencies must carry OSI-approved permissive licenses; copyleft and commercial packages stay out. Vendored code, such as the official calculation grammar, keeps its attribution in `NOTICE`. The Hyper API wheel's bundled license is rechecked on every upgrade, because the native `hyperd` binary ships inside it.
- AI authoring stays an optional module ([ADR-008](0008-sandboxed-claude-agent-runtime.md)), so the non-OSI Agent SDK is never required.
- If a commercial edition is ever wanted, it goes in a separately licensed `ee/` directory, as Lightdash does. None is planned.
- **Trademark:** the public name must not contain a recognizable portion of "Tableau" (risk R1, task P0.7). Tableau is named only nominatively ("migrate from Tableau Server"), less prominently than the product name, with the attribution "Tableau is a trademark of Salesforce, Inc." No Tableau logos, and neutral component names such as `hyper-to-iceberg`.

## Consequences

### Positive

- Familiar to enterprise legal teams, with an explicit patent grant.
- Compatible with every planned dependency.
- The compiler and conformance suite can be reused by other converter projects (P2.12).

### Negative

- Anyone may run TabFree as a closed service without contributing back.
- License hygiene is ongoing work: `NOTICE` upkeep, attribution, dependency scanning and an SBOM (P7.3).
- The rename touches package names, domains and documentation before launch.

## Alternatives considered

- **AGPL:** deters embedders and integrators and complicates the "free replacement" message.
- **MIT:** no explicit patent grant.
- **Source-available (BSL or similar):** not open source, which contradicts the project's purpose.

## Validation and revisit triggers

- **CI:** a dependency license scan fails the build on licenses outside the allowlist.
- **P0.7:** a clearance search and counsel's review of the public name before the repository goes public.
- **Revisit if:** a core dependency changes license, or a commercial edition is proposed.

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-010), [§7](../BUILD_PLAN.md#7-phases-and-milestones) (P0.1, P0.7) and [§11](../BUILD_PLAN.md#11-risks-and-mitigations) (R1, R13)
- Research report: [name and license](../research/research-report.md#the-name-the-license-and-tableaus-roadmap-carry-the-non-technical-risk); notes: [licensing and legal](../research/notes/aws_architecture_ai_authoring.md#6-licensing-and-legal-project-license-tabfree-name-and-trademark-and-hyper-api-dependency)
- [Salesforce trademark guidelines](https://www.salesforce.com/company/legal/tmcusageguidelines/)
- [tableauhyperapi on PyPI](https://pypi.org/project/tableauhyperapi/), [Hyper API releases](https://tableau.github.io/hyper-db/docs/releases)
- [Lightdash license](https://github.com/lightdash/lightdash/blob/HEAD/LICENSE), [Metabase license](https://github.com/metabase/metabase/blob/HEAD/LICENSE.txt), [AG Grid](https://github.com/ag-grid/ag-grid), [TypeScript Agent SDK](https://github.com/anthropics/claude-agent-sdk-typescript)
