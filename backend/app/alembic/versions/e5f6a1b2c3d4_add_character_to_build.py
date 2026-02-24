"""Add character column to build

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-02-24 00:00:00.000000

"""
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = "e5f6a1b2c3d4"
down_revision = "d4e5f6a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add as nullable first so existing rows don't fail
    op.add_column(
        "build",
        sa.Column(
            "character",
            sqlmodel.sql.sqltypes.AutoString(length=50),
            nullable=True,
        ),
    )
    # Backfill existing rows with empty string
    op.execute("UPDATE build SET character = '' WHERE character IS NULL")
    # Now make it NOT NULL
    op.alter_column("build", "character", nullable=False)


def downgrade() -> None:
    op.drop_column("build", "character")
