"""Add top_match_keys to optimization_snapshot

One content match key per stored result, in the results' own display order, so
the builds page can tell that an in-game loadout preset IS optimizer result #N
without loading the heavy full_results blob for every build.

Non-null with a '[]' server default and deliberately NOT backfilled: computing
a key requires the relic contents inside full_results, and an empty list is
honest ("we don't know this build's result identities yet").  It is not a
freshness input, so unlike the freshness-hash columns the backfill rule exists
for, an empty value cannot make a cache silently never hit -- it only withholds
one display badge until that build's next optimize.

Revision ID: a0b1c2d3e4f5
Revises: f9b0c1d2e3f4
Create Date: 2026-08-17 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a0b1c2d3e4f5"
down_revision = "f9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_snapshot",
        sa.Column(
            "top_match_keys",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("optimization_snapshot", "top_match_keys")
