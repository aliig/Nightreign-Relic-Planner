"""Add tier_weights and pinned_relics to build

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-02-22 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a1b2c3"
down_revision = "c3d4e5f6a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "build",
        sa.Column("tier_weights", sa.JSON(), nullable=True),
    )
    op.add_column(
        "build",
        sa.Column("pinned_relics", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("build", "pinned_relics")
    op.drop_column("build", "tier_weights")
