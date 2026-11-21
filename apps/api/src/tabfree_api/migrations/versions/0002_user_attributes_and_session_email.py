"""User attributes and session email (P3.4: USERATTRIBUTE and entitlement matching by email).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20 22:07:50.776541
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(length=320), server_default="", nullable=False))
        batch_op.add_column(
            sa.Column(
                "attributes",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "attributes",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("attributes")

    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_column("attributes")
        batch_op.drop_column("email")
