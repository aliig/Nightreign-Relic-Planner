"""Add pending_save_baseline to optimization_snapshot

The save track could starve.  ``save_baseline`` only ever advanced on a
pure-save run, but an upload deliberately advances nothing (its change has to
survive until the user reads it), and by the time the user reviews, a staged run
has usually replaced ``staged_signature`` — so ``mark_change_reviewed`` saw a
staged run and moved only the effective ``baseline``.  A user with a standing
Relic Rites diff therefore never advanced ``save_baseline`` again: it stayed
pinned to whatever save preceded their first purchase, and every later upload
was diffed against it, reporting long-owned relics as NEW with a percentage
attached.

``pending_save_baseline`` is the pure-save arrangement waiting to become
``save_baseline``: a pure-save run parks its result here, and review promotes it
and clears it.  A second upload before the review overwrites it, so two uploads
still compose into one verdict while the NEWEST save is what lands on the save
track.

No backfill: NULL means "nothing pending", which is the correct state for every
existing row.  Rows whose save_baseline is already stale stay stale until their
next upload writes a pending one — there is no honest pure-save arrangement
stored to recover for them (same reasoning as e3f4a5b6c7d8's NULL rows).

Revision ID: 2eb0357175a0
Revises: f2a3b4c5d6e7
Create Date: 2026-08-31 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "2eb0357175a0"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_snapshot",
        sa.Column("pending_save_baseline", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("optimization_snapshot", "pending_save_baseline")
