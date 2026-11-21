# ADR-002: Dashboards are HTML bundles with a governed data contract

## Status

Accepted — pending validation (P1.15), 2026-11-20.

Deciding task: the P1.15 authoring eval, which can make a JSON spec primary; the data contract stays unchanged either way.

## Context

The project brief explicitly requires that Claude write dashboards as HTML, because HTML is what AI writes most easily. Each dashboard is then executable code from a model that also reads untrusted data. Lightdash and Tableau sandbox such apps behind a query proxy. The research report preferred a governed JSON spec with HTML widgets as an escape hatch: a spec can be schema-validated, generated deterministically from a `.twb`, patched with small operations and rendered identically for PDF subscriptions.

## Decision

A dashboard is a bundle: the `tabfree.json` manifest (semantic model, parameters, filters, named queries, table calcs, actions), which the server validates and enforces; `index.html`, written with `<tf-*>` web components; and optional `app.js`, `style.css` and `assets/`.

- Named semantic queries are the only data path. The frame requests a query by name; the API checks capabilities, compiles it with the user's RLS policy and bindings, binds parameters, caps rows and bytes, and executes.
- The frame runs on a separate origin with `sandbox="allow-scripts"` and no `allow-same-origin`, under a CSP with `connect-src 'none'` and scripts only from the runtime and bundle. No tokens or cookies enter it.
- A component-only dashboard has no custom JS and is effectively a spec. Custom JS is the escape hatch, behind a static scan and a mandatory human review gate.

## Consequences

### Positive

- Meets the HTML requirement; governance lives in the manifest, not the HTML, and Claude never writes SQL.
- Components keep dashboards consistent and make PDF renders, diffs and migration output deterministic.

### Negative

- Edits rewrite HTML instead of applying small JSON operations, costing more tokens. The validator must cross-check every `<tf-*>` reference against the manifest, and the component API becomes a versioned contract (`tabfree/dashboard@1`).
- **Bridge:** the frame's origin is opaque, so `event.origin` is `"null"` and cannot be verified. The host must check `event.source === iframe.contentWindow`, then move all traffic onto a dedicated `MessageChannel` port transferred once during a hello/connect handshake. If the frame navigates away the port is lost, so a replacement document never receives data.
- **Accepted, documented residual risk:** custom JS can exfiltrate data the viewer is allowed to see by navigating its own frame to an attacker URL. Neither sandbox nor CSP blocks frame self-navigation (CSP `navigate-to` was dropped). Mitigations: component-only dashboards contain no custom JS; custom JS gets a static scan (location and navigation assignments, `eval`, network APIs) and a mandatory human review gate before publish; every publish is audited.

## Alternatives considered

- **Governed JSON spec primary, HTML widgets as the escape hatch** (the research default): safer for patch-style edits but further from the requirement.
- **Raw HTML with direct SQL:** ungovernable.

## Validation and revisit triggers

- **P1.15:** Claude rebuilds 20 corpus dashboards both ways, scored on validity, render success, data correctness, visual similarity, 10 scripted change requests and tokens used.
- **Security tests (P4.5, P7.3):** messages from any other window are ignored; after the frame navigates, nothing reaches the new document; the scan flags navigation, `eval` and network calls in custom-JS fixtures; custom JS cannot publish without an audited human approval.
- **2026-11-20, P1.15 pilot (HTML arm only, preliminary):** Claude agents completed 11 of 11 scripted edits on 3 dashboards. None used custom JS, every change went through the manifest, and each used roughly 30–40k tokens. The validator caught reference errors, but the pilot showed that semantic checks are needed before publishing, because Top N interacting with selections and defaults that return nothing both passed. The JSON-spec arm and the renderer comparison are still to do. See [p1-15-authoring-pilot.md](../research/notes/p1-15-authoring-pilot.md).
- **Revisit if:** more than about 5% of in-use dashboards need custom JS (the research assumed 95% coverage), or edit tokens dominate AI cost (R6).

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-002), [§4.1](../BUILD_PLAN.md#41-the-dashboard-bundle-adr-002) and [§11](../BUILD_PLAN.md#11-risks-and-mitigations) (R3, R5)
- [Research report](../research/research-report.md#a-thin-governed-core-a-json-spec-and-an-owned-compiler-anchor-the-build)
- [Lightdash data apps](https://github.com/lightdash/lightdash/blob/HEAD/docs/data-apps.md), [Tableau Data Apps](https://www.salesforce.com/blog/tableau-data-apps-from-prompt-to-trusted-agentic-analytics/), [MCP Apps spec](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)
