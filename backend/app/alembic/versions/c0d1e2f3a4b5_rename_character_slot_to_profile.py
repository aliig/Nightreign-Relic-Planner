"""Rename character_slot table to profile, character_id FK to profile_id

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-03-08 00:00:01.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("character_slot", "profile")
    op.alter_column("relic", "character_id", new_column_name="profile_id")
    op.alter_column("save_upload", "character_count", new_column_name="profile_count")


def downgrade() -> None:
    op.alter_column("save_upload", "profile_count", new_column_name="character_count")
    op.alter_column("relic", "profile_id", new_column_name="character_id")
    op.rename_table("profile", "character_slot")
