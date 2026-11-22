# tabfree-api

The TabFree API service (FastAPI): sign-in, permissions, the portal page and the governed query API. Content, permissions and sessions live in the metadata database (Postgres; P3.1) when `TABFREE_DATABASE_URL` is set. Without it, the Phase 1 mode reads a site file and keeps sessions in SQLite, which is still the quickest way to run the dev site.

```bash
npm run build -w @tabfree/runtime                      # the portal loads the runtime bundles
TABFREE_DEV_LOGIN=1 uv run uvicorn tabfree_api.main:app_factory --factory --port 8765
# http://localhost:8765/  (pick a user; no password)   API docs: /api/docs
uv run pytest apps/api
```

## What is here

| Module | Purpose |
|---|---|
| `auth.py` | OIDC sign-in (authorization code + PKCE, state bound to the browser, nonce, ID token checked against the provider's JWKS for issuer, audience and expiry) and server-side sessions (only SHA-256 hashes stored; cookies are HttpOnly, SameSite=Lax, and Secure on https). |
| `authz.py` | Tableau's effective permissions (ADR-006, P3.3): site-role ceiling per content type, then administrator, project owner or leader (inherited downward), content owner (no Set Permissions when locked), user rule, group deny, group allow; unspecified is denied. Nested projects; customizable, locked and locked-not-nested asset permissions. `evaluate` returns the reason. |
| `content.py` | The site-file site (Phase 1 mode) and the `SiteContent` interface both sites implement. Dev site: [`dev/site.yaml`](dev/site.yaml). |
| `db/` | The metadata database (P3.1): `schema.py` (SQLAlchemy Core), Alembic migrations in `migrations/`, `store.py` (`DbSite`: content with immutable revisions, rules, users and groups; `DbSessionStore`: sessions, OIDC flows and personal access tokens; `LocalAccounts`; `AuditLog`), `seed.py` (import a site file). |
| `service.py` | The governed query service: binds dashboard state to the manifest (or validates an explore query against the schema), compiles with the viewer's identity (RLS), checks the result cache and the site's guardrails, runs with a timeout, caps rows and bytes, and writes an audit line (never values). |
| `cache.py` | Result cache keyed on `sha256(compiled SQL, typed bind values incl. RLS, model, connection, data version, today, caps)`; pluggable backend, in-memory LRU + TTL by default. |
| `guardrails.py` | Per-site cost guardrails: bytes scanned per UTC day against a budget, and a semaphore on concurrent engine queries. Refusals are HTTP 429 with a `code`. |
| `engines.py` | DuckDB (local sample data), Athena (`ExecutionParameters`, workgroup per site, result reuse, bytes scanned) and the Redshift Data API (named parameters). Timeouts cancel the engine query. The AWS engines are unit-tested with fake clients only. |
| `portal.py` | `/auth/*`, `/api/me`, `/api/dashboards[/{id}[/query|/domain]]`, `/api/models/{model}/query`, `/runtime/*.js`, and the portal page. |
| `preview.py` | `/preview`: the same dashboards with a user picker and no sign-in (dev only). |
| `authoring/` | Phase 4: drafts and the two-step publish, the validation pipeline, the MCP server and its OAuth server, AI guardrails, the model provider, the quick-edit loop and the "Edit with Claude" session runner (see below). |

## Metadata database (P3.1, P3.2)

```bash
export TABFREE_DATABASE_URL=sqlite:///tabfree.db          # or postgresql+psycopg://... (inject it with `secret run`)
uv run python -m tabfree_api.db upgrade                    # migrations
uv run python -m tabfree_api.db import-site apps/api/dev/site.yaml
uv run python -m tabfree_api.db create-admin dev breakglass   # local administrator; password on stdin
TABFREE_SITE_ID=dev TABFREE_DEV_LOGIN=1 uv run uvicorn tabfree_api.main:app_factory --factory --port 8765
```

- **Schema:** sites; users (identity-provider or local) and their site roles; groups (with an optional minimum site role, Tableau's grant role on sign-in) and memberships; nested projects with asset permissions; content (dashboards, models, connections); append-only revisions; permission rules (content rules and project defaults per content type); sessions; OIDC flows; personal access tokens; the audit log. On Postgres a trigger makes revisions and the audit log append-only.
- **Revisions:** publishing adds a revision and makes it current. Identical content adds none. A restore adds a new revision with the old content. New content copies its project's default rules.
- **Sign-in:** the session holds the identity provider's groups plus local memberships. The site role is the user's role, raised to any group's minimum. Unknown users join with the site's default role; unlicensed users cannot sign in.
- **Personal access tokens:** `POST /api/tokens {"name": ...}` returns `tfp_<id>.<secret>` once. Send it as `Authorization: Bearer ...`, which needs no CSRF header. A token is valid for up to a year, stops working after 15 days without use (as in Tableau), and can be revoked with `DELETE /api/tokens/{id}`. Group membership and site role are read on each use.
- **Local accounts:** `/auth/local`, with argon2id hashes and a lockout after five failures in fifteen minutes. These are for break-glass administrators and service users; everyone else signs in through the identity provider.
- **SAML** goes through the broker (Cognito, or Okta/Entra/Keycloak federation): TabFree speaks only OIDC.
- **Caching:** permissions are read from a per-process snapshot refreshed after local writes and at most `TABFREE_PERMISSION_CACHE_S` (2 s) old.

## Portal, exports and links (P3.5–P3.9)

- **Portal:** `apps/portal` (React), built into `apps/portal/dist` and served at `/` and its routes. The APIs behind it are in `browse.py`: permission-filtered browse and search, favorites and recents, dashboard capabilities, the model browser, revisions, the permissions dialog (rules, project defaults, effective permissions with reasons), projects, and site administration (users, groups, settings, audit log, old Tableau URLs).
- **Usercontent origin:** set `TABFREE_USERCONTENT_ORIGIN` to a second hostname pointing at this service. Dashboards then load from signed, five-minute `/frame/{id}` URLs there (`usercontent.py`). The frame response carries `sandbox allow-scripts` and a no-network CSP; the portal page gets `script-src 'self'`. That host serves nothing else, and the portal host serves no frames. With several processes, set the same `TABFREE_FRAME_SIGNING_KEY` on all of them (inject it).
- **Exports** (`POST /api/dashboards/{id}/export`, `exports.py`):
  - **What:** `csv` (summary data) and `crosstab` (Excel) of a named query need Download Summary Data. `png` and `pdf` need Download Image/PDF.
  - **How:** data runs as the viewer, with export row and byte caps. Table calcs are computed in SQL where the compiler can push them down; any that only the browser engine can compute are named in `X-TabFree-Omitted`. Text that a spreadsheet would read as a formula is prefixed with `'`. Pictures come from headless Chromium (`TABFREE_EXPORT_RENDERER=playwright`, `tabfree-api[render]`, `playwright install chromium`), signed in as the requester with a two-minute session and opening the dashboard's print view.
- **Old Tableau links:** `/views/<workbook>/<sheet>` and `/t/<site>/views/...` redirect to the dashboard that replaced the view. The portal resolves the fragment forms (`/#/site/<site>/views/...`) through `GET /api/redirects/resolve`. Mappings are added by an administrator or the migration factory with `PUT /api/admin/legacy-urls`; a link to something the viewer may not see answers 404.
- **Audit:** sign-in and sign-out, views, queries (a hash of the request, never filter values), exports, denials, permission and admin changes, tokens. Administrators read it at `/api/admin/audit` and on the portal's Audit log page.

State-changing requests need the header `X-TabFree-Request: 1` (CSRF defence). Dashboards a viewer may not see return 404, not 403.

## AI authoring and MCP (Phase 4)

Everything in `authoring/` goes through one permission-checking service (`service.py`), so the MCP
tools, the portal's draft pages and the agents can do exactly what the signed-in person can.

- **Drafts** (`drafts.py`): private working copies of a bundle (`tabfree.json`, `index.html`,
  `README.md`). Starting one needs Web Edit; a new dashboard needs Publish on its project. Edits are
  patch-style (`files.py`: write, exact replace, JSON pointer set/remove), all-or-nothing.
- **Validation** (`validation.py`, P4.5): manifest and component references; every query compiled on
  Athena and Redshift; every query run as the author (errors fail, empty or all-null results warn);
  custom JavaScript scanned (network, navigation and dynamic code fail); accessibility warnings; and a
  screenshot diff against the published revision when the export renderer is configured.
- **Publishing** takes two steps: `request_publish` validates and returns a confirmation code and the
  diff; `confirm_publish` needs Overwrite (or Publish for a new dashboard), the code, unchanged files
  and, with the site setting `authoringApproval: required`, a project leader's approval. Agents can
  never confirm.
- **MCP server** (`mcp.py`, P4.1): Streamable HTTP at `/mcp` (stateless, JSON responses), 17 tools
  (`tools.py`) including `learn` for the docs in `authoring/docs/`, and an MCP Apps `ui://` preview.
  Data values and dashboard text in tool results are fenced as untrusted (`fence.py`).
- **OAuth 2.1** (`oauth.py`): protected-resource and authorization-server metadata, dynamic client
  registration (public clients, https or loopback redirects), authorization code with S256 PKCE and a
  consent page, one-hour access tokens and rotating 30-day refresh tokens (replay revokes the family),
  revocation. Personal access tokens work as bearer tokens too. Claude Code: add
  `{"type": "http", "url": "https://<server>/mcp"}` to `.mcp.json` (or run `tabfree kit`).
- **Edit with Claude** (`author.py`, `quick.py`, P4.3/P4.4): `POST /api/drafts/{id}/chat` starts a
  session; progress streams from `/api/agent-sessions/{id}/events` (server-sent events). Quick edits
  run an in-process tool loop with the same tools as the user; larger builds start one ECS Fargate task
  per session with the Agent SDK (`apps/author-agent`) and a 30-minute on-behalf-of token.
- **Guardrails** (`budget.py`, P4.6): MCP tool calls per user per minute, and model tokens per user
  and per site per UTC day (`ai_usage` table; site settings `aiDailyTokensPerUser`/`PerSite`).
  Every tool call is audited with the agent session id.
- **Provider** (`provider.py`, P4.7): `TABFREE_AI_PROVIDER=off|anthropic|bedrock`, with a model per
  task (`TABFREE_AI_MODEL_QUICK`, `_BUILD`, `_MIGRATE`). Off by default; drafts, validation, MCP and
  the CLI work without it. The Claude API key comes from `ANTHROPIC_API_KEY` in the environment
  (inject it with `secret run`); Bedrock uses the task role.

## Query service (P2.7)

**Explore API.** `POST /api/models/{model}/query` with `{"query": {"dimensions": [...], "measures": [...], "where": [...], "sort": [...], "limit": n}}` (the manifest schema's `query` definition; `model` comes from the URL). It needs the **Connect** capability on the model, granted in the site file's `models.entries[].permissions` (default deny; site roles below Explorer never get it; models return 404 to viewers without it). RLS applies from the viewer's session exactly as for dashboards. The response is `{columns, rows, truncated, meta: {cache, truncatedBy, scannedBytes}}`; dashboard query responses keep exactly `{columns, rows, truncated}` because the runtime checks that shape.

**Result cache.** Two viewers share an entry only when the engine would receive the same SQL and the same bind values; RLS and user functions are bind values, so different RLS bindings never share. A model's `dataVersion` (site file; the refresh pipeline bumps it in P6.2) is in every key. Hits and misses are counted (`QueryService.stats`) and audited.

**Athena.** One workgroup per site (site file `query.athenaWorkgroup`, else `TABFREE_ATHENA_WORKGROUPS`, else the model's). No output location is sent: the workgroup sets it. Result reuse is requested only for queries with no bind parameters (no RLS or filter values) unless `TABFREE_ATHENA_REUSE_WITH_PARAMETERS` is set, and the query text carries a hash of the data version so a refresh never reuses an old result.

**Guardrails.** Each engine query is charged its bytes scanned (Athena's `DataScannedInBytes`, a configured estimate for Redshift, 0 for DuckDB) against the site's daily budget. Over budget, new engine queries get `429 {"code": "scan_budget_exceeded"}` with `Retry-After` until 00:00 UTC; cached results are still served. A per-site semaphore limits concurrent engine queries; a query that waits longer than the queue timeout gets `429 {"code": "too_many_queries"}`. A query past its timeout is cancelled on the engine and returns 504. The ledger is per process until Phase 3 moves it to shared storage.

**Audit.** `query user=… dashboard=… query:… rows=… truncated=… cache=hit|miss|reuse scanned_bytes=… duration_ms=…` (explore: `model=… explore`); refusals log `refused=<code>`. Never values.

## Configuration (`TABFREE_*` environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `TABFREE_ENVIRONMENT` | `dev` | The dev logins, the preview and the sample engine refuse to start outside `dev`/`test`. |
| `TABFREE_BASE_URL` | `http://localhost:8000` | Public URL; the OIDC redirect is `{base}/auth/callback`, and cookies are Secure when https. |
| `TABFREE_SITE_FILE` | `apps/api/dev/site.yaml` | Projects, dashboards, permissions, site roles (when there is no database). |
| `TABFREE_DATABASE_URL` | none | The metadata database; inject it, since it carries a password. |
| `TABFREE_SITE_ID` | `default` | The database site this process serves. |
| `TABFREE_DATABASE_AUTO_MIGRATE` | off | Run migrations at start-up (dev and test only). |
| `TABFREE_PERMISSION_CACHE_S` | `2` | Most seconds a permission change made in another process takes to apply. |
| `TABFREE_USERCONTENT_ORIGIN`, `TABFREE_FRAME_SIGNING_KEY` | none | Separate origin for dashboard frames, and the shared key for their signed URLs. |
| `TABFREE_EXPORT_ROW_LIMIT`, `TABFREE_EXPORT_MAX_BYTES` | 1,000,000, 256 MiB | Caps for CSV and Excel exports. |
| `TABFREE_EXPORT_RENDERER`, `TABFREE_EXPORT_BASE_URL` | `none`, base URL | `playwright` turns on PNG/PDF exports; the URL the renderer opens. |
| `TABFREE_OIDC_ATTRIBUTE_CLAIMS` | `[]` | ID-token claims copied into user attributes for RLS (`USERATTRIBUTE`). |
| `TABFREE_ENGINE` | `sample` | `sample` (DuckDB with generated data) or `aws` (each model's Athena/Redshift connection; needs `tabfree-api[aws]`). |
| `TABFREE_OIDC_ISSUER`, `TABFREE_OIDC_CLIENT_ID`, `TABFREE_OIDC_CLIENT_SECRET` | — | Identity provider (Cognito, Okta, Entra ID, Keycloak…). |
| `TABFREE_OIDC_USERNAME_CLAIM`, `TABFREE_OIDC_GROUPS_CLAIM` | `preferred_username`, `groups` | Claims for the username (RLS `USERNAME()`) and groups (permissions, `ISMEMBEROF()`). |
| `TABFREE_DEV_LOGIN`, `TABFREE_PREVIEW` | off | Development helpers with no authentication. |
| `TABFREE_QUERY_ROW_LIMIT`, `TABFREE_QUERY_MAX_BYTES` | `10000`, 16 MiB | Row cap and approximate JSON byte cap per result; more is cut off and flagged `truncated`. |
| `TABFREE_QUERY_TIMEOUT_S` | `60` | Per-query timeout; the engine query is cancelled (504). |
| `TABFREE_RESULT_CACHE_ENTRIES`, `TABFREE_RESULT_CACHE_TTL_S` | `2000`, `300` | In-memory result cache size (0 turns it off) and freshness. |
| `TABFREE_ATHENA_RESULT_REUSE_MINUTES` | `60` | Athena result reuse maximum age (0 turns it off). |
| `TABFREE_ATHENA_REUSE_WITH_PARAMETERS` | off | Also reuse for parameterized (RLS) queries; leave off until verified on real Athena (P2.10). |
| `TABFREE_ATHENA_WORKGROUPS` | `{}` | JSON map of site id to Athena workgroup; the site file's `query.athenaWorkgroup` wins. |
| `TABFREE_DAILY_SCAN_BUDGET_BYTES` | none | Default daily bytes-scanned budget per site; the site file's `query.dailyScanBudgetBytes` wins. |
| `TABFREE_REDSHIFT_SCAN_ESTIMATE_BYTES` | 100 MiB | Bytes charged per Redshift query (the Data API does not report them). |
| `TABFREE_MAX_CONCURRENT_QUERIES`, `TABFREE_QUEUE_TIMEOUT_S` | `20`, `10` | Concurrent engine queries per site (site file `query.maxConcurrentQueries` wins) and how long a query waits for a slot. |

Never put the client secret in a file. Inject it from the secret store at start-up, for example:

```bash
secret run -e TABFREE_OIDC_CLIENT_SECRET="Cognito - Client Secret - TabFree" -- \
  uv run uvicorn tabfree_api.main:app_factory --factory
```

## Migration factory (Phase 5)

```bash
python -m tabfree_api.migration import factory/ --by admin       # governance + one item per dashboard
python -m tabfree_api.migration repair --wave 1 --by admin       # Claude repair agent (needs AI on)
python -m tabfree_api.migration verify --wave 1 --by admin       # data + visual parity, fidelity
python -m tabfree_api.migration access --by admin                # access parity against Tableau
python -m tabfree_api.migration rls ITEM samples/                # samples/<user>/<query>.csv
```

Imported dashboards are not published: each is a migration item whose bundle sits in a draft owned by
the importing administrator. The console at `/migration` (site administrators) shows Tableau's PNG
next to the live draft with its fidelity score, and the reviewer approves (publishes with the Tableau
owner, project, rules, old-URL redirects and favorites), requests changes (the note goes to the repair
agent), marks it for a manual rebuild, or retires it. Subscriptions, refresh jobs, alerts and custom
views are kept as records for Phase 6. Tables: `migration_items`, `migration_events`,
`migration_records` (migration 0006).
