"""backfill build_hash on legacy builds

Migration a2b3c4d5e6f7 added build.build_hash as nullable but did not backfill
existing rows.  A NULL build_hash makes GET /optimize/snapshot treat every
snapshot for that build as permanently stale (build_hash != NULL is always
true), so the inventory usage count silently under-reports.  All current write
paths (create/update/clone) set the hash, so this one-time backfill closes the
legacy gap.  Recomputing build_signature on an unedited build reproduces the
exact hash its snapshot already stored, so those snapshots become fresh again.

Revision ID: a3b4c5d6e7f8
Revises: f8a9b0c1d2e3
Create Date: 2026-06-18 00:00:00.000000

"""
from alembic import op
from sqlmodel import Session, select

from app.core.build_def import build_def_from_db
from app.models import Build
from nrplanner.changes import build_signature


# revision identifiers, used by Alembic.
revision = 'a3b4c5d6e7f8'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def upgrade():
    with Session(op.get_bind()) as session:
        builds = session.exec(select(Build).where(Build.build_hash.is_(None))).all()
        for build in builds:
            build.build_hash = build_signature(build_def_from_db(build))
            session.add(build)
        session.commit()


def downgrade():
    # Backfilling a previously-NULL value is not reversibly distinguishable
    # from a legitimately-set hash, so there is nothing safe to undo.
    pass
