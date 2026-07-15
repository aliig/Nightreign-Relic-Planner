"""Add relic.acquisition_id (in-game acquisition order)

The save's ItemEntry table stamps every owned item with a global,
monotonically increasing acquisition counter — higher means acquired more
recently. Stored per relic so the inventory can sort by acquisition order.

Nullable with no backfill: the value only exists in the save file itself,
so rows persisted before this column existed stay NULL until the user
re-uploads their save. (This is NOT a freshness-hash column — relic
fingerprints cover only real_id/effects/curses — so no backfill is needed
for cache correctness.)

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-15 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "relic",
        sa.Column("acquisition_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("relic", "acquisition_id")
