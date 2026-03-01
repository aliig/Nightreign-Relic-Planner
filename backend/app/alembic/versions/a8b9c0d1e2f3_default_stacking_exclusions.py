"""Update excluded_stacking_categories server_default to [300, 6630000]

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-03-01 00:00:01.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "build",
        "excluded_stacking_categories",
        server_default="[300, 6630000]",
    )


def downgrade() -> None:
    op.alter_column(
        "build",
        "excluded_stacking_categories",
        server_default="[]",
    )
