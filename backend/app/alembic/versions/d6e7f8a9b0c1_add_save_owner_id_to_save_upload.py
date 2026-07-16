"""Add save_upload.save_owner_id (owning account's SteamID64)

Records the SteamID64 of the account that owns an uploaded save, read from the
save's fixed profile anchor (USERDATA_10 @ 0x8). Used to detect when a newly
uploaded save belongs to a different account than the previous upload, so the
"changes since last save" comparison isn't run across unrelated profiles.

Nullable with no backfill: the value only exists in the save file itself, so
rows persisted before this column existed stay NULL until the user re-uploads.
NULL means "owner unknown" and the comparison falls back to today's behaviour
(it only suppresses when it can prove a different account). This is NOT a
freshness-hash column, so no backfill is needed for cache correctness.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-16 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "save_upload",
        sa.Column("save_owner_id", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("save_upload", "save_owner_id")
