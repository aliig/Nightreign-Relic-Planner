"""Add is_featured to build

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-02-22 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a1b2"
down_revision = "b2c3d4e5f6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "build",
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_build_is_featured", "build", ["is_featured"])


def downgrade() -> None:
    op.drop_index("ix_build_is_featured", table_name="build")
    op.drop_column("build", "is_featured")
