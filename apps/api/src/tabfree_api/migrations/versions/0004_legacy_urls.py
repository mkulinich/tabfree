"""Old Tableau URLs (P3.9 redirects).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-20 22:33:47.125913
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "legacy_urls",
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="dashboard", nullable=False),
        sa.Column("content_id", sa.String(length=100), nullable=False),
        sa.CheckConstraint("kind IN ('dashboard', 'model', 'connection')", name=op.f("ck_legacy_urls_kind")),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_legacy_urls_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("site_id", "path", name=op.f("pk_legacy_urls")),
    )


def downgrade() -> None:
    op.drop_table("legacy_urls")
