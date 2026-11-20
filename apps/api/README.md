# tabfree-api

The TabFree API service (FastAPI): sign-in, permissions, the portal page and the governed query API. Phase 1 (P1.13) is the minimal version. Content and permissions are read from a site file and sessions live in SQLite. Phase 3 moves both to Postgres and adds SAML, PATs and SCIM.

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
| `authz.py` | Permission checks in ADR-006 order: site administrator, then the site-role ceiling, then owner or project leader, then a user rule, then a group deny, then a group allow; anything unspecified is denied. Locked projects apply their own rules to their dashboards. |
| `content.py` | The site: projects, dashboards (bundles on disk), semantic models, users' site roles. Dev site: [`dev/site.yaml`](dev/site.yaml). |
| `service.py` | The governed query service: binds dashboard state to the manifest, compiles with the viewer's identity (RLS), runs, caps rows, writes an audit line (never values). |
| `engines.py` | DuckDB (local sample data), Athena (`ExecutionParameters`) and the Redshift Data API (named parameters). The AWS engines are unit-tested with fake clients only. |
| `portal.py` | `/auth/*`, `/api/me`, `/api/dashboards[/{id}[/query|/domain]]`, `/runtime/*.js`, and the portal page. |
| `preview.py` | `/preview`: the same dashboards with a user picker and no sign-in (dev only). |

State-changing requests need the header `X-TabFree-Request: 1` (CSRF defence). Dashboards a viewer may not see return 404, not 403.

## Configuration (`TABFREE_*` environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `TABFREE_ENVIRONMENT` | `dev` | The dev logins, the preview and the sample engine refuse to start outside `dev`/`test`. |
| `TABFREE_BASE_URL` | `http://localhost:8000` | Public URL; the OIDC redirect is `{base}/auth/callback`, and cookies are Secure when https. |
| `TABFREE_SITE_FILE` | `apps/api/dev/site.yaml` | Projects, dashboards, permissions, site roles. |
| `TABFREE_ENGINE` | `sample` | `sample` (DuckDB with generated data) or `aws` (each model's Athena/Redshift connection; needs `tabfree-api[aws]`). |
| `TABFREE_OIDC_ISSUER`, `TABFREE_OIDC_CLIENT_ID`, `TABFREE_OIDC_CLIENT_SECRET` | — | Identity provider (Cognito, Okta, Entra ID, Keycloak…). |
| `TABFREE_OIDC_USERNAME_CLAIM`, `TABFREE_OIDC_GROUPS_CLAIM` | `preferred_username`, `groups` | Claims for the username (RLS `USERNAME()`) and groups (permissions, `ISMEMBEROF()`). |
| `TABFREE_DEV_LOGIN`, `TABFREE_PREVIEW` | off | Development helpers with no authentication. |

Never put the client secret in a file. Inject it from the secret store at start-up, for example:

```bash
secret run -e TABFREE_OIDC_CLIENT_SECRET="Cognito - Client Secret - TabFree" -- \
  uv run uvicorn tabfree_api.main:app_factory --factory
```
