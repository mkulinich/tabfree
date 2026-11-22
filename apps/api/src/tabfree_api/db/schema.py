"""The metadata schema (BUILD_PLAN P3.1): sites, users and groups, nested projects, content with
immutable revisions, permission rules, sessions, personal access tokens and the audit log.

Postgres in every deployed environment (Aurora PostgreSQL, ADR-009); SQLite works for tests and a
laptop. Alembic migrations in ``tabfree_api/migrations`` create these tables; a test checks that
the migrations and this module agree.

Keys: sites, projects and content have readable ids (slugs) scoped to their site, so URLs and the
migration factory can name them. Users have a surrogate id; the username is unique per database.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.expression import false

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=convention)

#: JSON on SQLite, JSONB on Postgres.
Document = JSON().with_variant(postgresql.JSONB(), "postgresql")
Timestamp = DateTime(timezone=True)

SITE_ROLES = ("unlicensed", "viewer", "explorer", "explorer_can_publish", "creator", "site_administrator")
CONTENT_KINDS = ("dashboard", "model", "connection")
RESOURCE_KINDS = ("project", *CONTENT_KINDS)
ASSET_PERMISSIONS = ("customizable", "locked", "locked_not_nested")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


sites = Table(
    "sites",
    metadata,
    Column("id", String(100), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("default_site_role", String(32), nullable=False, server_default="viewer"),
    #: Per-site engine settings and guardrails: athenaWorkgroup, dailyScanBudgetBytes, ...
    Column("settings", Document, nullable=False, default=dict),
    Column("created_at", Timestamp, nullable=False),
    CheckConstraint(_in("default_site_role", SITE_ROLES), name="default_site_role"),
)

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(255), nullable=False, unique=True),
    Column("fullname", String(255), nullable=False, server_default=""),
    Column("email", String(320)),
    #: "idp" (OIDC/SAML through the broker) or "local" (password, argon2id hash below).
    Column("auth_source", String(16), nullable=False, server_default="idp"),
    Column("password_hash", Text),
    Column("disabled", Boolean, nullable=False, server_default=false()),
    Column("created_at", Timestamp, nullable=False),
    Column("last_sign_in_at", Timestamp),
    #: Admin-set user attributes for RLS (USERATTRIBUTE); identity-provider claims override them.
    Column("attributes", Document, nullable=False, default=dict),
    CheckConstraint(_in("auth_source", ("idp", "local")), name="auth_source"),
)

site_users = Table(
    "site_users",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("site_role", String(32), nullable=False),
    CheckConstraint(_in("site_role", SITE_ROLES), name="site_role"),
)

groups = Table(
    "groups",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(255), nullable=False),
    #: "local" (managed in TabFree) or "idp" (synced from the identity provider's groups claim/SCIM).
    Column("source", String(16), nullable=False, server_default="local"),
    #: Tableau's "grant role on sign-in": members get at least this site role.
    Column("minimum_site_role", String(32)),
    UniqueConstraint("site_id", "name"),
)

group_members = Table(
    "group_members",
    metadata,
    Column("group_id", ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)

projects = Table(
    "projects",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(100), primary_key=True),
    Column("parent_id", String(100)),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("owner_id", ForeignKey("users.id", ondelete="SET NULL")),
    #: Tableau's asset permissions: "customizable", "locked" (including nested projects) or
    #: "locked_not_nested". Locked content uses the project's default rules, not its own.
    Column("asset_permissions", String(24), nullable=False, server_default="customizable"),
    Column("created_at", Timestamp, nullable=False),
    ForeignKeyConstraint(["site_id", "parent_id"], ["projects.site_id", "projects.id"], ondelete="CASCADE"),
    UniqueConstraint("site_id", "parent_id", "name"),
    CheckConstraint(_in("asset_permissions", ASSET_PERMISSIONS), name="asset_permissions"),
)

content = Table(
    "content",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("kind", String(16), primary_key=True),
    Column("id", String(100), primary_key=True),
    Column("project_id", String(100)),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("owner_id", ForeignKey("users.id", ondelete="SET NULL")),
    Column("current_revision", Integer),
    #: Models: bumped by extract refresh (P6.2); part of every result-cache key.
    Column("data_version", String(100), nullable=False, server_default=""),
    #: Where the item came from, e.g. {"system": "tableau", "luid": ...} (migration factory).
    Column("origin", Document),
    Column("created_at", Timestamp, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
    Column("deleted_at", Timestamp),
    #: P6.6: certification (models and dashboards) and a data quality warning shown to viewers.
    Column("certified", Boolean, nullable=False, server_default=sa_false()),
    Column("certification_note", Text, nullable=False, server_default=""),
    Column("certified_by", String(255)),
    Column("quality_warning", Document),
    ForeignKeyConstraint(["site_id", "project_id"], ["projects.site_id", "projects.id"]),
    CheckConstraint(_in("kind", CONTENT_KINDS), name="kind"),
)

content_revisions = Table(
    "content_revisions",
    metadata,
    Column("site_id", String(100), primary_key=True),
    Column("kind", String(16), primary_key=True),
    Column("content_id", String(100), primary_key=True),
    Column("number", Integer, primary_key=True),
    #: The manifest (dashboards), the model document (models) or the connection settings.
    Column("document", Document, nullable=False),
    #: Other files of a dashboard bundle, path → text (index.html, scripts, styles).
    Column("files", Document, nullable=False, default=dict),
    Column("sha256", String(64), nullable=False),
    Column("message", Text, nullable=False, server_default=""),
    Column("created_by", ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", Timestamp, nullable=False),
    ForeignKeyConstraint(
        ["site_id", "kind", "content_id"],
        ["content.site_id", "content.kind", "content.id"],
        ondelete="CASCADE",
    ),
)

permission_rules = Table(
    "permission_rules",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    Column("resource_kind", String(16), nullable=False),
    Column("resource_id", String(100), nullable=False),
    #: On projects: the content type a default rule applies to ("project" for the project itself,
    #: "dashboard", "model", ...). Null on content. Project leaders are rules on a project with
    #: content type "project" and capability "project_leader" (a status in Tableau, not a capability).
    Column("content_type", String(16)),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE")),
    Column("group_id", ForeignKey("groups.id", ondelete="CASCADE")),
    Column("capability", String(40), nullable=False),
    Column("decision", String(8), nullable=False),
    CheckConstraint(_in("resource_kind", RESOURCE_KINDS), name="resource_kind"),
    CheckConstraint(_in("decision", ("allow", "deny")), name="decision"),
    CheckConstraint("(user_id IS NULL) <> (group_id IS NULL)", name="one_grantee"),
    UniqueConstraint(
        "site_id",
        "resource_kind",
        "resource_id",
        "content_type",
        "user_id",
        "group_id",
        "capability",
        name="uq_permission_rules_rule",
    ),
    Index("ix_permission_rules_resource", "site_id", "resource_kind", "resource_id"),
)

sessions = Table(
    "sessions",
    metadata,
    #: Only the SHA-256 of the session token is stored.
    Column("token_sha256", String(64), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    Column("fullname", String(255), nullable=False),
    #: Group names at sign-in (identity provider claim plus local memberships).
    Column("groups", Document, nullable=False),
    Column("site_role", String(32), nullable=False),
    Column("email", String(320), nullable=False, server_default=""),
    #: Limited sessions (P6.8 embedding): what the token may call; None is a full session.
    Column("scopes", Document),
    #: User attributes at sign-in (RLS ``USERATTRIBUTE``).
    Column("attributes", Document, nullable=False, default=dict),
    Column("created_at", Timestamp, nullable=False),
    Column("expires_at", Timestamp, nullable=False, index=True),
)

oidc_flows = Table(
    "oidc_flows",
    metadata,
    Column("state_sha256", String(64), primary_key=True),
    Column("nonce", String(100), nullable=False),
    Column("verifier", String(200), nullable=False),
    Column("return_to", Text, nullable=False),
    Column("expires_at", Timestamp, nullable=False),
)

personal_access_tokens = Table(
    "personal_access_tokens",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(100), nullable=False),
    #: The secret part is shown once; only its SHA-256 is kept.
    Column("secret_sha256", String(64), nullable=False, unique=True),
    Column("created_at", Timestamp, nullable=False),
    Column("expires_at", Timestamp, nullable=False),
    Column("last_used_at", Timestamp),
    Column("revoked_at", Timestamp),
    UniqueConstraint("user_id", "site_id", "name"),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("at", Timestamp, nullable=False),
    Column("site_id", String(100)),
    #: The username (kept as text so the log survives user deletion).
    Column("actor", String(255)),
    #: e.g. sign_in, sign_out, view, query, export, permission_change, content_publish.
    Column("action", String(40), nullable=False),
    Column("resource_kind", String(16)),
    Column("resource_id", String(100)),
    #: allow / deny for permission decisions, ok / error otherwise.
    Column("outcome", String(8), nullable=False),
    #: Never data values: hashes, counts, durations, reasons.
    Column("detail", Document, nullable=False, default=dict),
    Index("ix_audit_events_site_at", "site_id", "at"),
)

favorites = Table(
    "favorites",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("kind", String(16), primary_key=True),
    Column("content_id", String(100), primary_key=True),
    Column("created_at", Timestamp, nullable=False),
    CheckConstraint(_in("kind", RESOURCE_KINDS), name="kind"),
)

#: Old Tableau URLs (P3.9): "views/<workbook>/<sheet>" → a TabFree dashboard, filled by the migration.
legacy_urls = Table(
    "legacy_urls",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    #: Normalised path, e.g. "views/Superstore/Overview" (no site prefix, no query string).
    Column("path", String(1024), primary_key=True),
    Column("kind", String(16), nullable=False, server_default="dashboard"),
    Column("content_id", String(100), nullable=False),
    CheckConstraint(_in("kind", CONTENT_KINDS), name="kind"),
)

# ---- Phase 4: AI authoring ------------------------------------------------------------------------

DRAFT_STATUSES = ("open", "published", "discarded")

drafts = Table(
    "drafts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(16), nullable=False, server_default="dashboard"),
    #: The dashboard the draft edits, or the id a new dashboard will get.
    Column("content_id", String(100), nullable=False),
    #: For a new dashboard: the project it will be published to.
    Column("project_id", String(100)),
    #: The revision the draft started from (None: a new dashboard).
    Column("base_revision", Integer),
    #: The bundle: path → text ("tabfree.json", "index.html").
    Column("files", Document, nullable=False, default=dict),
    Column("created_by", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    #: The agent session that made the latest change (None: edited directly).
    Column("agent_session", String(64)),
    Column("status", String(16), nullable=False, server_default="open"),
    #: Two-step publish: the SHA-256 of the confirmation code, the files it covers and its expiry.
    Column("publish_code_sha256", String(64)),
    Column("publish_files_sha256", String(64)),
    Column("publish_expires_at", Timestamp),
    Column("publish_message", Text, nullable=False, server_default=""),
    #: An approver's sign-off when the site requires approval (username, at).
    Column("approved_by", String(255)),
    Column("published_revision", Integer),
    Column("created_at", Timestamp, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
    CheckConstraint(_in("status", DRAFT_STATUSES), name="status"),
    Index("ix_drafts_site_content", "site_id", "kind", "content_id"),
)

oauth_clients = Table(
    "oauth_clients",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(200), nullable=False),
    Column("redirect_uris", Document, nullable=False),
    Column("created_at", Timestamp, nullable=False),
)

oauth_codes = Table(
    "oauth_codes",
    metadata,
    Column("code_sha256", String(64), primary_key=True),
    Column("client_id", ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("code_challenge", String(128), nullable=False),
    Column("scope", String(200), nullable=False),
    Column("resource", Text, nullable=False),
    Column("expires_at", Timestamp, nullable=False),
    Column("used_at", Timestamp),
    #: The token family issued from this code (revoked if the code is replayed).
    Column("family", String(36)),
)

oauth_tokens = Table(
    "oauth_tokens",
    metadata,
    Column("token_sha256", String(64), primary_key=True),
    Column("kind", String(8), nullable=False),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    #: None for tokens minted inside TabFree for an agent session (on behalf of the user).
    Column("client_id", ForeignKey("oauth_clients.id", ondelete="CASCADE")),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("scope", String(200), nullable=False),
    Column("resource", Text, nullable=False),
    Column("agent_session", String(64)),
    #: Refresh tokens rotate; a replayed one revokes its whole family.
    Column("family", String(36), nullable=False, index=True),
    Column("created_at", Timestamp, nullable=False),
    Column("expires_at", Timestamp, nullable=False),
    Column("revoked_at", Timestamp),
    CheckConstraint(_in("kind", ("access", "refresh")), name="kind"),
)

ai_usage = Table(
    "ai_usage",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("username", String(255), primary_key=True),
    #: UTC day, YYYY-MM-DD.
    Column("day", String(10), primary_key=True),
    Column("requests", Integer, nullable=False, server_default="0"),
    Column("input_tokens", BigInteger, nullable=False, server_default="0"),
    Column("output_tokens", BigInteger, nullable=False, server_default="0"),
)

agent_sessions = Table(
    "agent_sessions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("draft_id", ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False),
    #: "quick" (in-process tool loop) or "sandbox" (a Fargate task with the Agent SDK).
    Column("mode", String(16), nullable=False),
    Column("model", String(100), nullable=False),
    Column("status", String(16), nullable=False, server_default="running"),
    #: Progress events shown in the chat panel: [{"type": ..., "text": ..., "at": ...}].
    Column("events", Document, nullable=False, default=list),
    #: The conversation so far, for follow-up turns (quick mode).
    Column("transcript", Document, nullable=False, default=list),
    Column("input_tokens", BigInteger, nullable=False, server_default="0"),
    Column("output_tokens", BigInteger, nullable=False, server_default="0"),
    Column("created_at", Timestamp, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
    CheckConstraint(_in("mode", ("quick", "sandbox")), name="mode"),
    CheckConstraint(_in("status", ("running", "done", "failed", "cancelled")), name="status"),
)

# ---- Phase 5: the migration factory -----------------------------------------------------------------

#: converted: a baseline bundle awaits repair or review; manual: needs a person to rebuild it;
#: review: repaired (or not needed) and waiting for a reviewer; changes_requested: the reviewer sent
#: instructions to the repair agent; approved: published; rebuild: marked for a manual rebuild;
#: retired: not migrated (archived with its ground truth).
MIGRATION_STATUSES = (
    "converted",
    "manual",
    "repairing",
    "review",
    "changes_requested",
    "approved",
    "rebuild",
    "retired",
)

#: One Tableau dashboard (or view published on its own) on its way to TabFree.
migration_items = Table(
    "migration_items",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(100), primary_key=True),  # the TabFree dashboard id
    Column("title", String(255), nullable=False),
    Column("workbook", String(255), nullable=False, server_default=""),
    Column("project_id", String(100)),
    Column("owner", String(255)),
    Column("wave", Integer),
    Column("usage", BigInteger, nullable=False, server_default="0"),
    Column("status", String(24), nullable=False),
    #: The draft holding the converted bundle (repairs and reviews happen on it).
    Column("draft_id", String(36)),
    #: The factory's report.json: coverage, unsupported features, notes, references.
    Column("report", Document, nullable=False, default=dict),
    #: Latest fidelity: {score, grade, components: {coverage, data, visual, validation}}.
    Column("fidelity", Document),
    #: Tableau's PNG of the view (ground truth) and the latest TabFree render.
    Column("reference_png", LargeBinary),
    Column("render_png", LargeBinary),
    #: Captured summary-data CSVs per query: {query: csv text}.
    Column("reference_csv", Document, nullable=False, default=dict),
    Column("repair_iterations", Integer, nullable=False, server_default="0"),
    Column("reviewer", String(255)),
    Column("note", Text, nullable=False, server_default=""),
    Column("published_revision", Integer),
    Column("created_at", Timestamp, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
    CheckConstraint(_in("status", MIGRATION_STATUSES), name="status"),
    Index("ix_migration_items_wave", "site_id", "wave", "status"),
)

#: Everything that happened to an item: imports, repairs, verification, reviewer actions.
migration_events = Table(
    "migration_events",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    Column("item_id", String(100), nullable=False),
    Column("action", String(40), nullable=False),
    Column("by", String(255)),
    Column("note", Text, nullable=False, server_default=""),
    Column("data", Document),
    Column("created_at", Timestamp, nullable=False),
    Index("ix_migration_events_item", "site_id", "item_id"),
)

#: Governance that waits for Phase 6 features (subscriptions, refresh jobs, alerts, custom views),
#: the decision queue and verification reports, kept as documents from the factory and the checks.
migration_records = Table(
    "migration_records",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("kind", String(32), primary_key=True),  # job | alert | custom_view | decision | verification
    Column("key", String(200), primary_key=True),
    Column("data", Document, nullable=False),
    Column("status", String(32), nullable=False, server_default="pending"),
    Column("updated_at", Timestamp, nullable=False),
)

# ---- Phase 6: Tableau Server feature parity -----------------------------------------------------------

JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")

#: Named, shared schedules (P6.1), like Tableau's: {frequency, every, at, weekdays, day, timezone}.
schedules = Table(
    "schedules",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(36), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("spec", Document, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=sa_true()),
    Column("next_run_at", Timestamp),
    Column("last_run_at", Timestamp),
    Column("created_at", Timestamp, nullable=False),
    UniqueConstraint("site_id", "name", name="uq_schedules_site_name"),
    Index("ix_schedules_due", "enabled", "next_run_at"),
)

#: The job queue and its history (ADR-013).
jobs = Table(
    "jobs",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
    #: refresh | subscription | alert | warm | webhook | rollup
    Column("kind", String(24), nullable=False),
    Column("payload", Document, nullable=False, default=dict),
    Column("status", String(12), nullable=False, server_default="queued"),
    Column("priority", Integer, nullable=False, server_default="0"),
    Column("run_at", Timestamp, nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("max_attempts", Integer, nullable=False, server_default="3"),
    Column("locked_by", String(100)),
    Column("locked_until", Timestamp),
    Column("schedule_id", String(36)),
    #: Who or what asked for it: a username, "schedule", or "event:<name>".
    Column("created_by", String(255)),
    #: A queued or running job with the same key is not enqueued twice.
    Column("dedupe_key", String(200)),
    Column("error", Text),
    Column("result", Document),
    Column("created_at", Timestamp, nullable=False),
    Column("started_at", Timestamp),
    Column("finished_at", Timestamp),
    CheckConstraint(_in("status", JOB_STATUSES), name="status"),
    Index("ix_jobs_claim", "status", "run_at", "priority"),
    Index("ix_jobs_site_created", "site_id", "created_at"),
    Index("ix_jobs_dedupe", "site_id", "dedupe_key"),
)

#: Extract refresh tasks (P6.2): which model refreshes how, on which schedule.
refresh_tasks = Table(
    "refresh_tasks",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(36), primary_key=True),
    Column("model_id", String(100), nullable=False),
    #: full | incremental | materialized_view
    Column("mode", String(24), nullable=False),
    Column("schedule_id", String(36)),
    #: Per table: source SQL or table, the target, the incremental key; see jobs/refresh.py.
    Column("settings", Document, nullable=False, default=dict),
    Column("owner", String(255)),
    Column("enabled", Boolean, nullable=False, server_default=sa_true()),
    Column("created_at", Timestamp, nullable=False),
)

#: Subscriptions (P6.3): a dashboard emailed to recipients, rendered for each of them.
subscriptions = Table(
    "subscriptions",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(36), primary_key=True),
    Column("owner", String(255), nullable=False),
    Column("dashboard_id", String(100), nullable=False),
    Column("subject", String(255), nullable=False),
    Column("message", Text, nullable=False, server_default=""),
    #: [{"user": name} | {"group": name} | {"email": address}]
    Column("recipients", Document, nullable=False, default=list),
    Column("format", String(8), nullable=False, server_default="png"),
    #: Dashboard state (filters, parameters) or a custom view to render.
    Column("state", Document, nullable=False, default=dict),
    Column("custom_view_id", String(36)),
    Column("schedule_id", String(36)),
    #: Send after this model refreshes, instead of (or as well as) on a schedule.
    Column("after_refresh_of", String(100)),
    Column("only_if_data", Boolean, nullable=False, server_default=sa_false()),
    Column("webhook_url", Text),
    #: active | paused | pending_confirmation (external recipients wait for the owner)
    Column("status", String(24), nullable=False, server_default="active"),
    Column("last_sent_at", Timestamp),
    Column("created_at", Timestamp, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
)

#: Recipients who unsubscribed (from one subscription, or from all when subscription_id is "*").
mail_suppressions = Table(
    "mail_suppressions",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("address", String(320), primary_key=True),
    Column("subscription_id", String(36), primary_key=True),
    Column("created_at", Timestamp, nullable=False),
)

#: Data-driven alerts (P6.4) with the state that stops the same alert repeating.
data_alerts = Table(
    "data_alerts",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(36), primary_key=True),
    Column("owner", String(255), nullable=False),
    Column("title", String(255), nullable=False),
    Column("dashboard_id", String(100), nullable=False),
    Column("query", String(64), nullable=False),
    Column("measure", String(256), nullable=False),
    #: {"operator": ">" | ">=" | "<" | "<=" | "=" | "!=", "threshold": number, "aggregate": "any"|"all"|"sum"}
    Column("condition", Document, nullable=False),
    Column("state", Document, nullable=False, default=dict),
    Column("recipients", Document, nullable=False, default=list),
    Column("schedule_id", String(36)),
    Column("after_refresh_of", String(100)),
    #: How often to notify while the condition stays true: once | every_check | daily
    Column("repeat", String(16), nullable=False, server_default="once"),
    Column("enabled", Boolean, nullable=False, server_default=sa_true()),
    Column("firing", Boolean, nullable=False, server_default=sa_false()),
    Column("last_value", Document),
    Column("last_checked_at", Timestamp),
    Column("last_notified_at", Timestamp),
    Column("created_at", Timestamp, nullable=False),
)

#: Custom views (P6.5): saved filter and parameter state, private or shared, and "default for me".
custom_views = Table(
    "custom_views",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(36), primary_key=True),
    Column("dashboard_id", String(100), nullable=False),
    Column("owner", String(255), nullable=False),
    Column("name", String(200), nullable=False),
    Column("state", Document, nullable=False, default=dict),
    Column("shared", Boolean, nullable=False, server_default=sa_false()),
    Column("created_at", Timestamp, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
    Index("ix_custom_views_dashboard", "site_id", "dashboard_id"),
)

custom_view_defaults = Table(
    "custom_view_defaults",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("username", String(255), primary_key=True),
    Column("dashboard_id", String(100), primary_key=True),
    Column("custom_view_id", String(36), nullable=False),
)

#: Sharing links (P6.5): a short id for a dashboard in a given state; viewers still need View.
share_links = Table(
    "share_links",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(24), primary_key=True),
    Column("dashboard_id", String(100), nullable=False),
    Column("state", Document, nullable=False, default=dict),
    Column("created_by", String(255), nullable=False),
    Column("created_at", Timestamp, nullable=False),
)

comments = Table(
    "comments",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(36), primary_key=True),
    Column("dashboard_id", String(100), nullable=False),
    Column("author", String(255), nullable=False),
    Column("body", Text, nullable=False),
    Column("mentions", Document, nullable=False, default=list),
    #: The dashboard state the comment was written about (filters, parameters).
    Column("state", Document),
    Column("created_at", Timestamp, nullable=False),
    Column("deleted_at", Timestamp),
    Index("ix_comments_dashboard", "site_id", "dashboard_id"),
)

content_tags = Table(
    "content_tags",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("kind", String(16), primary_key=True),
    Column("content_id", String(100), primary_key=True),
    Column("tag", String(50), primary_key=True),
    Index("ix_content_tags_tag", "site_id", "tag"),
)

collections = Table(
    "collections",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(36), primary_key=True),
    Column("owner", String(255), nullable=False),
    Column("name", String(200), nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("shared", Boolean, nullable=False, server_default=sa_false()),
    #: [{"kind": "dashboard"|"model", "id": ...}], in order.
    Column("items", Document, nullable=False, default=list),
    Column("created_at", Timestamp, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
)

#: Outgoing webhooks (P6.9): events posted to a URL, signed with HMAC-SHA256.
webhooks = Table(
    "webhooks",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(36), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("url", Text, nullable=False),
    Column("events", Document, nullable=False, default=list),
    #: The signing secret; the database is encrypted at rest (KMS). Shown once, when created.
    Column("secret", String(100), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=sa_true()),
    Column("created_by", String(255)),
    Column("created_at", Timestamp, nullable=False),
)

#: Connected apps for embedding (P6.8): who may sign embed tokens, and where frames may appear.
connected_apps = Table(
    "connected_apps",
    metadata,
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(36), primary_key=True),  # the client id (JWT "iss")
    Column("name", String(200), nullable=False),
    Column("secret_id", String(36), nullable=False),  # JWT "kid"
    Column("secret", String(100), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=sa_true()),
    #: Origins allowed to frame dashboards (Content-Security-Policy frame-ancestors).
    Column("domains", Document, nullable=False, default=list),
    #: Projects whose content the app may embed (empty: all).
    Column("projects", Document, nullable=False, default=list),
    Column("created_at", Timestamp, nullable=False),
)

#: Embed tokens already used (single-use "jti"), kept until they expire.
embed_jtis = Table(
    "embed_jtis",
    metadata,
    Column("jti", String(100), primary_key=True),
    Column("expires_at", Timestamp, nullable=False, index=True),
)
