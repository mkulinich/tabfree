"""AI authoring (Phase 4): drafts, OAuth, AI usage, agent sessions.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21 15:17:06.752333
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("requests", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_ai_usage_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("site_id", "username", "day", name=op.f("pk_ai_usage")),
    )
    op.create_table(
        "drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="dashboard", nullable=False),
        sa.Column("content_id", sa.String(length=100), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=True),
        sa.Column("base_revision", sa.Integer(), nullable=True),
        sa.Column(
            "files",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("agent_session", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("publish_code_sha256", sa.String(length=64), nullable=True),
        sa.Column("publish_files_sha256", sa.String(length=64), nullable=True),
        sa.Column("publish_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_message", sa.Text(), server_default="", nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("published_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('open', 'published', 'discarded')", name=op.f("ck_drafts_status")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_drafts_created_by_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_drafts_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drafts")),
    )
    with op.batch_alter_table("drafts", schema=None) as batch_op:
        batch_op.create_index("ix_drafts_site_content", ["site_id", "kind", "content_id"], unique=False)

    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "redirect_uris",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_oauth_clients_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_clients")),
    )
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column(
            "events",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "transcript",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('quick', 'sandbox')", name=op.f("ck_agent_sessions_mode")),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed', 'cancelled')", name=op.f("ck_agent_sessions_status")
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["drafts.id"], name=op.f("fk_agent_sessions_draft_id_drafts"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_agent_sessions_site_id_sites"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_agent_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_sessions")),
    )
    op.create_table(
        "oauth_codes",
        sa.Column("code_sha256", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=200), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("family", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.id"],
            name=op.f("fk_oauth_codes_client_id_oauth_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_oauth_codes_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("code_sha256", name=op.f("pk_oauth_codes")),
    )
    op.create_table(
        "oauth_tokens",
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=200), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("agent_session", sa.String(length=64), nullable=True),
        sa.Column("family", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('access', 'refresh')", name=op.f("ck_oauth_tokens_kind")),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.id"],
            name=op.f("fk_oauth_tokens_client_id_oauth_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_oauth_tokens_site_id_sites"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_oauth_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("token_sha256", name=op.f("pk_oauth_tokens")),
    )
    with op.batch_alter_table("oauth_tokens", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_oauth_tokens_family"), ["family"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("oauth_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_oauth_tokens_family"))

    op.drop_table("oauth_tokens")
    op.drop_table("oauth_codes")
    op.drop_table("agent_sessions")
    op.drop_table("oauth_clients")
    with op.batch_alter_table("drafts", schema=None) as batch_op:
        batch_op.drop_index("ix_drafts_site_content")

    op.drop_table("drafts")
    op.drop_table("ai_usage")
