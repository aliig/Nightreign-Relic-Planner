"""Add family_weight_floors to build table

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-17 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "build",
        sa.Column(
            "family_weight_floors", sa.JSON(), nullable=False, server_default="{}"
        ),
    )


def downgrade() -> None:
    op.drop_column("build", "family_weight_floors")
