"""Make hashed_password nullable for OAuth users

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-02-22 00:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("user", "hashed_password", nullable=True)


def downgrade() -> None:
    op.alter_column("user", "hashed_password", nullable=False)
