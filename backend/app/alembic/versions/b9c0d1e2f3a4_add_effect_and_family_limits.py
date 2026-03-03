"""Add effect_limits and family_limits to build table

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-03-02 00:00:01.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "build",
        sa.Column("effect_limits", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "build",
        sa.Column("family_limits", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("build", "family_limits")
    op.drop_column("build", "effect_limits")
