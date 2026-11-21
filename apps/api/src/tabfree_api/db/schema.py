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
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
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
