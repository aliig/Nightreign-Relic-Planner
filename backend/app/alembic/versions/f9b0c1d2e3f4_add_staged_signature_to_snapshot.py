"""Add staged_signature to optimization_snapshot

Nullable and deliberately NOT backfilled: NULL means "computed from the pure
save inventory", which is exactly true of every pre-existing row.  The column
is a cause-attribution marker (BuildChange.cause = "staged" when the staged
in-app diff moved between runs), never a freshness input — freshness stays on
the content hashes — so a NULL here cannot cause a silently-never-hitting
cache, unlike the freshness-hash columns the backfill rule exists for.

Revision ID: f9b0c1d2e3f4
Revises: e7f8a9b0c1d2
Create Date: 2026-07-31 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f9b0c1d2e3f4"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_snapshot",
        sa.Column("staged_signature", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("optimization_snapshot", "staged_signature")
