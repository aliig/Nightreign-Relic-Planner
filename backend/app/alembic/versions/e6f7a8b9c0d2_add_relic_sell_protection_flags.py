"""Add relic sell-protection flags and profile Murk currency

Adds relic.is_favorite, relic.equipped (sell-protection flags parsed from the
save) and profile.murks (current in-game currency, shown in the inventory UI
and credited when selling relics).

Revision ID: e6f7a8b9c0d2
Revises: d5e6f7a8b9c0
Create Date: 2026-06-18 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e6f7a8b9c0d2"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "relic",
        sa.Column(
            "is_favorite", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "relic",
        sa.Column(
            "equipped", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "profile",
        sa.Column(
            "murks", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("profile", "murks")
    op.drop_column("relic", "equipped")
    op.drop_column("relic", "is_favorite")
