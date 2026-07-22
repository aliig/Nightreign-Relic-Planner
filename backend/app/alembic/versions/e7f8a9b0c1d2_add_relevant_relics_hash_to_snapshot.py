"""Add relevant_relics_hash + run params to optimization_snapshot

All three columns are nullable and deliberately NOT backfilled: computing the
relevant hash requires game data plus per-build relevance resolution, and a
NULL here only falls back to the previous behavior (coarse whole-inventory
relics_hash compare for freshness; no rites reuse) — one re-optimize refills
them.  Not a silently-never-hitting freshness hash.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-20 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_snapshot",
        sa.Column("relevant_relics_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "optimization_snapshot",
        sa.Column("top_n", sa.Integer(), nullable=True),
    )
    op.add_column(
        "optimization_snapshot",
        sa.Column("max_per_vessel", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("optimization_snapshot", "max_per_vessel")
    op.drop_column("optimization_snapshot", "top_n")
    op.drop_column("optimization_snapshot", "relevant_relics_hash")
