# TabFree threat model

BUILD_PLAN P7.3. Method: data-flow diagram, then STRIDE per boundary, with the control that answers
each threat and where it lives. Reviewed when a boundary changes; last reviewed 2026-11-22.

## Assets

1. **Business data** in Athena and Redshift, and query results (cached in the API).
2. **Who may see what**: permissions, row-level security policies, user attributes.
3. **Content**: dashboards, models, revisions; the Tableau estate during migration.
4. **Credentials**: sessions, personal access tokens, OAuth tokens, embed secrets, signing keys, the
   database password, AWS role credentials, model-provider access.
5. **Personal data**: names, emails, usage and audit logs.

## Trust boundaries

| # | Boundary | Crossed by |
|---|---|---|
| B1 | Internet → load balancer (WAF) | browsers, MCP clients, the CLI, SCIM, embedding hosts, webhook receivers (outbound) |
| B2 | Portal page → dashboard frame | the postMessage bridge (`packages/runtime/src/protocol.ts`) |
| B3 | API → query engines | compiled SQL (Athena, Redshift) |
| B4 | API → model provider | prompts with fenced content (Claude API or Bedrock) |
| B5 | API → authoring sandbox (Fargate) | an on-behalf-of agent token over MCP |
| B6 | Migration tooling → Tableau Server and files | read-only REST/GraphQL; untrusted workbook XML |
| B7 | Site → site (several sites, one database) | the site id on every row and session |

## Threats and controls

| Threat (STRIDE) | Boundary | Control | Where |
|---|---|---|---|
| **S** Stolen session cookie or token reused | B1 | HttpOnly, Secure, SameSite cookies; CSRF header on writes; tokens stored hashed; PAT idle expiry; OAuth refresh rotation with replay detection | `auth.py`, `db/store.py`, `authoring/oauth.py` |
| **S** Forged embed token | B1 | HS256 with a per-app secret, 10-minute lifetime, single-use `jti`, audience and scope checks; limited session for one dashboard | `embed.py` |
| **S** Session from one site used on another | B7 | Sessions, PATs and OAuth tokens all checked against the site | `db/store.py`, `multisite.py` |
| **T** Dashboard code changing data or reaching the network | B2 | Sandboxed frame (`allow-scripts` only), strict CSP (`connect-src 'none'`), separate usercontent origin, validated messages, custom-script scan blocks network/navigation/eval | `runtime/host.ts`, `usercontent.py`, `authoring/validation.py` |
| **T** SQL injection | B3 | Dashboards contain no SQL; the compiler generates it from the semantic model with bind parameters; refresh SQL only from administrators with identifier checks | `tabfree_compiler/sql.py`, `jobs/refresh.py` |
| **T** Malicious Tableau workbook | B6 | `defusedxml`, nothing executed, packages read as zips, sizes bounded | `tabfree_migrate/twb*.py` |
| **R** Denying an action | all | Audit log for sign-ins, views, queries (metrics only), exports, permission changes, publishing, every agent tool call with the agent session id | `db/store.py` (AuditLog) |
| **I** Seeing rows one should not | B3 | RLS compiled into every query (also rollups, which carry the policies or are not used), result cache keyed by RLS context, subscriptions and warming rendered as each person | `sql.py`, `rollups.py`, `cache.py`, `jobs/subscribe.py`, `jobs/warm.py` |
| **I** Data leaving by email or webhook | B1 | External recipients need owner confirmation; webhooks carry ids, never values; alerts only to users with View | `jobs/subscribe.py`, `jobs/webhooks.py`, `jobs/alerts.py` |
| **I** Secrets in logs or files | all | Secrets only from Secrets Manager or the environment; WAF logs redact authorization and cookies; query audit records no values | `infra/terraform/modules/app`, `service.py` |
| **D** Request floods, expensive queries | B1, B3 | WAF rate limit per IP; MCP calls per minute; per-site scan budgets and concurrency; row and byte caps; timeouts | `modules/app`, `authoring/budget.py`, `guardrails.py` |
| **E** Privilege escalation through scoped tokens | B1 | Token scopes checked on every portal call (read-only OAuth tokens cannot write; embed sessions see one dashboard) | `auth.allowed_by_scopes`, `portal.py` |
| **E** Escaping a permission check | all | One permission engine with Tableau's evaluation order, tested against Tableau-verified cases | `authz.py` |

## AI authoring and the sandbox (B4, B5)

The highest-risk area: a model acts for a person, reads data and dashboards written by other people,
and writes code that runs in other people's browsers.

| Threat | Control |
|---|---|
| **Prompt injection** from dashboard text, data values, workbook XML (migration repair) | Untrusted content is fenced with per-call random markers and the system prompts say fenced text is content, never instructions (`authoring/fence.py`, `quick.py`, `migration/repair.py`). The agent acts only through the authoring tools. |
| The agent doing more than the person may | The agent runs **as the person**, with their permissions and RLS (an on-behalf-of token with their scopes); tools re-check every capability. |
| The agent publishing on its own | Two-step publish: only the person confirms (`confirm_publish` refuses agent sessions); optional approval by a project leader. |
| Exfiltration through the dashboard it writes | Bundles cannot make network calls (CSP, frame sandbox) and the script scan rejects network APIs, navigation and dynamic code; validation runs before any publish; diffs are shown to the person. |
| Exfiltration through the model provider | Only fenced, capped samples go to the model (governed queries, at most 200 rows); Bedrock keeps prompts in the AWS account; AI is off unless an administrator turns it on. |
| A compromised sandbox task | One Fargate task per session, unprivileged user, read-only root filesystem, egress only to the model endpoint and the TabFree API, a short-lived token for that one person, `max_turns` and budget caps (ADR-008). |
| Runaway cost | Per-person and per-site daily token budgets; MCP rate limit. |

## Residual risks and follow-ups

- A person with Web Edit can publish misleading (but sandboxed) content: governance, not code; the
  approval policy mitigates.
- Signing secrets for connected apps and webhooks are stored in the database (encrypted at rest by
  KMS); a database compromise exposes them. Follow-up: envelope-encrypt them with KMS.
- The penetration test (scope in `pentest-scope.md`) has not run; it is a release gate for v1.0.
