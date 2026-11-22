# API reference

Every TabFree server documents its own API: the OpenAPI document is at `/api/openapi.json` and an
interactive explorer at `/api/docs`. The same document, generated from the code, is in this site as
[openapi.json](openapi.json) (`make openapi` regenerates it; a test checks it is reproducible).

## Authentication

| Caller | How |
|---|---|
| The portal | A session cookie after OpenID Connect sign-in. Writes need the `X-TabFree-Request: 1` header (CSRF). |
| Scripts and the `tabfree` CLI | `Authorization: Bearer <personal access token>`; tokens carry scopes (read-only tokens cannot write). |
| MCP clients | OAuth 2.1 with PKCE against the server (`/mcp`). |
| Embedding | A connected app's short-lived JWT, exchanged by `/embed.js` for a limited session. |
| Identity providers | SCIM 2.0 at `/scim/v2` with an administrator's token. |

Every call runs as a person, with their permissions and row-level security.

## Main groups

| Path | What |
|---|---|
| `/api/me`, `/api/home`, `/api/site` | The signed-in person, their home page, the site |
| `/api/content`, `/api/dashboards/{id}` | Browse content; a dashboard's bundle |
| `/api/dashboards/{id}/query`, `/domain` | Run a named query or a filter's domain with a filter state |
| `/api/models/{id}/query` | Ad-hoc semantic queries (needs Connect) |
| `/api/drafts`, `/api/agent-sessions`, `/mcp` | Authoring: drafts, validation, preview, publish, Edit with Claude (`/mcp` is not in the OpenAPI document) |
| `/api/projects`, `/api/permissions` | Projects and permission rules |
| `/api/favorites`, `/api/custom-views`, `/api/comments`, `/api/tags`, `/api/collections`, `/api/share` | People's own state |
| `/api/recycle-bin`, `/api/redirects` | Lifecycle and legacy Tableau URLs |
| `/api/schedules`, `/api/refresh-tasks`, `/api/subscriptions`, `/api/alerts`, `/api/webhooks`, `/api/jobs` | Jobs |
| `/api/tokens`, `/api/connected-apps`, `/api/embed` | Personal access tokens and embedding |
| `/api/admin/*` | People, groups, usage and jobs (administrators) |
| `/api/migration/*` | The migration console (site administrators) |

## Webhooks

Webhook deliveries carry `X-TabFree-Signature: sha256=<hex>`, an HMAC-SHA256 of the request body with the
webhook's secret. Compute it over the raw body and compare in constant time. Payloads hold ids and
event names, not data values.
