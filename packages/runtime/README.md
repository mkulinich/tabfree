# @tabfree/runtime

The dashboard runtime: everything that runs in the browser around a TabFree dashboard.

**Phase 0 (now):** [`src/protocol.ts`](src/protocol.ts), the typed message protocol between a sandboxed dashboard and the host page, with strict validators for both directions.

**Phase 1 (P1.11):**
- the host bridge and the dashboard-side SDK (`tabfree.query()`, filters, parameters, actions)
- the first `<tf-*>` web components (`tf-dashboard`, `tf-chart`, `tf-filter`, `tf-crosstab`)

**Phase 2 (P2.6):** the JS table-calculation engine.

## Security model (ADR-0002)

- **Isolated frame:** dashboards render in an iframe on a separate origin with `sandbox="allow-scripts"`, without `allow-same-origin`, and under a strict CSP (`connect-src 'none'`). They hold no cookies or tokens and cannot make network requests.
- **Private channel:** the frame's origin is opaque, so the host authenticates the `hello` message by `event.source`. It then moves all traffic onto a dedicated `MessagePort`. A document that replaces the dashboard never receives data.
- **Named queries only:** dashboards ask for data by query name only. The host runs queries through the governed query API, which applies permissions and row-level security on the server.
- **Strict validation:** every message is validated on receipt. Unknown types, unexpected keys, non-finite numbers, oversized payloads and prototype-pollution keys are dropped.
- **Known residual risk:** custom JavaScript in a dashboard can leak data the viewer is allowed to see by navigating its own frame elsewhere. Browsers cannot block this from the host. Component-only dashboards contain no custom JS, and custom JS requires a static scan plus human review before publishing.

```bash
npm test -w @tabfree/runtime
npm run typecheck -w @tabfree/runtime
```
