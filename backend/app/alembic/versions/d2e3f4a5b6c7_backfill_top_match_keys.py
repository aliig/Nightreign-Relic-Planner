"""Backfill top_match_keys from each snapshot's stored results

The "your saved loadout is this build's suggestion #N" badge reads only
``top_match_keys``.  Rows written before that column existed have it empty, and
the comment on the column assumed they would refill "on the next optimize" —
but a snapshot only re-optimizes when its inputs go stale, so a build the user
had already optimized stayed silent indefinitely.  In practice that meant the
badge appeared for a handful of recently-run builds and nothing else, which
reads as "the feature doesn't work".

Nothing needs recomputing: ``full_results`` holds the complete result dumps in
display order, and a match key is a pure function of (vessel_id, relic contents)
— exactly what ``result_match_key`` derives.  Rows with no stored results are
left empty; they have nothing to rank against and refill on their next run.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-18 00:00:00.000000
"""
import json

import sqlalchemy as sa
from alembic import op

from nrplanner.changes import layout_match_key, relic_fingerprint

# revision identifiers, used by Alembic.
revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def _match_key(result: dict) -> str:
    """Mirror of nrplanner.changes.result_match_key over a serialized result."""
    fps = [
        relic_fingerprint(
            relic["real_id"], relic.get("effects") or [], relic.get("curses") or []
        )
        for assignment in result.get("assignments") or []
        if (relic := assignment.get("relic")) is not None
    ]
    return layout_match_key(result["vessel_id"], fps)


def upgrade() -> None:
    snapshot = sa.table(
        "optimization_snapshot",
        sa.column("id"),
        sa.column("full_results", sa.JSON()),
        sa.column("top_match_keys", sa.JSON()),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            snapshot.c.id, snapshot.c.full_results, snapshot.c.top_match_keys
        )
    ).mappings().all()

    for row in rows:
        if row["top_match_keys"]:
            continue
        results = row["full_results"]
        if isinstance(results, str):  # some drivers hand JSON back as text
            results = json.loads(results or "[]")
        if not results:
            continue
        bind.execute(
            snapshot.update()
            .where(snapshot.c.id == row["id"])
            .values(top_match_keys=[_match_key(r) for r in results])
        )


def downgrade() -> None:
    """One-way: the keys are derived data, and which rows were empty before is
    not recorded.  Dropping them would only re-break the badge."""
