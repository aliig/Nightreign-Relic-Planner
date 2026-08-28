"""Split the change baseline into effective vs. pure-save

``baseline`` is the arrangement the user last acknowledged, and reviewing a
Relic Rites change advanced it to layouts built from staged purchases — relics
that were never in any save.  Discarding those purchases and uploading a newer
save then diffed the new save against them, so every purchase was reported as
"gone from your save", scored as a percentage loss, and blamed on the save.

``save_baseline`` is the same record restricted to pure-save runs.  A pure-save
run (every upload) diffs against it; a staged run keeps diffing against
``baseline``.

Backfill: rows whose baseline was built from a pure-save run copy straight
across, so no existing build reports a spurious change on its next run.  Rows
whose baseline carries a staged_signature are left NULL on purpose — their
recorded arrangement is exactly the contaminated one this migration exists to
stop trusting, and there is no pure-save arrangement stored to recover.  Those
builds report status "new" once (no comparison, no false loss) and re-baseline
silently on their next run.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-27 00:00:00.000000
"""
import json

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_snapshot",
        sa.Column("save_baseline", sa.JSON(), nullable=True),
    )

    # Typed table construct (not raw SQL): lets SQLAlchemy serialize the dict
    # into the JSON column itself, which raw text params cannot do on Postgres.
    snapshot = sa.table(
        "optimization_snapshot",
        sa.column("id"),
        sa.column("baseline", sa.JSON()),
        sa.column("save_baseline", sa.JSON()),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.select(snapshot.c.id, snapshot.c.baseline)
    ).mappings().all()

    for row in rows:
        baseline = row["baseline"]
        if isinstance(baseline, str):  # some drivers hand JSON back as text
            baseline = json.loads(baseline or "null")
        if not baseline:
            continue  # never optimized — nothing to carry
        if (baseline.get("inputs") or {}).get("staged_signature") is not None:
            continue  # staged arrangement: deliberately left NULL, see above
        bind.execute(
            snapshot.update()
            .where(snapshot.c.id == row["id"])
            .values(save_baseline=baseline)
        )


def downgrade() -> None:
    op.drop_column("optimization_snapshot", "save_baseline")
