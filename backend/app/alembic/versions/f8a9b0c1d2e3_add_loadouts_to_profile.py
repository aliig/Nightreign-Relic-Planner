"""Add profile.loadouts (in-game relic loadout presets)

Adds profile.loadouts, a JSON cache of the in-game relic loadout presets parsed
from the uploaded save (list of ParsedLoadoutData dicts). Replaced on every
re-upload; served by GET /saves/profiles/{id}/loadouts and the Relic Loadouts page.

Revision ID: f8a9b0c1d2e3
Revises: e6f7a8b9c0d2
Create Date: 2026-06-18 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f8a9b0c1d2e3"
down_revision = "e6f7a8b9c0d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile",
        sa.Column(
            "loadouts", sa.JSON(), nullable=False, server_default="[]"
        ),
    )


def downgrade() -> None:
    op.drop_column("profile", "loadouts")
