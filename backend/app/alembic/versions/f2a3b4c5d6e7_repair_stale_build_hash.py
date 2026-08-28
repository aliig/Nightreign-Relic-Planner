"""repair build_hash rows left stale by the pinned-handle remap

Both save-upload paths re-point each build's pinned_relics at the new save's
ga_handles (the game reassigns every handle on save) but never recomputed
build.build_hash.  pinned_relics is part of build_signature, so those rows have
disagreed with their own content ever since.

The damage is silent and permanent: _snapshot_is_fresh compares a snapshot's
(correct) build_hash against the (stale) Build row and judges the build out of
date, while re-optimizing writes back the same correct hash — so the builds
page's "N builds out of date" banner can never clear.  Measured on a real
database: 23 of 68 builds stuck.

Migration a3b4c5d6e7f8 only backfilled NULL hashes; these are non-NULL and
wrong, so they need their own pass.  Recomputing from the live build content is
the definition of the correct value, and the snapshots already hold it — this
alone un-sticks the affected builds without re-running the optimizer.

Revision ID: f2a3b4c5d6e7
Revises: e3f4a5b6c7d8
Create Date: 2026-08-28

"""
from alembic import op
from sqlmodel import Session, select

from app.core.build_def import build_def_from_db
from app.models import Build
from nrplanner.changes import build_signature

# revision identifiers, used by Alembic.
revision = "f2a3b4c5d6e7"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    with Session(op.get_bind()) as session:
        for build in session.exec(select(Build)).all():
            live = build_signature(build_def_from_db(build))
            if build.build_hash != live:
                build.build_hash = live
                session.add(build)
        session.commit()


def downgrade():
    # The stale hashes carried no information — they were simply wrong for the
    # content beside them — so there is nothing meaningful to restore.
    pass
