"""Add top_match_ranks to optimization_snapshot (tie-aware result ranks)

The builds page's "your saved loadout is suggestion #N" badge derived N from
the loadout's POSITION in ``top_match_keys``.  Positions are not ranks: the
optimizer sorts by ``(not meets_requirements, -total_score)`` and breaks score
ties arbitrarily, so saving the third of three identically-scoring loadouts
showed "#3 of 10" for a joint-best pick -- it read as "you chose a worse setup"
when nothing outranked it.

``top_match_ranks`` stores the competition rank of each entry (ties share a
rank; the next distinct result skips), computed at write time where the result
scores are in hand.  Backfilled from ``full_results``, which keeps the
``total_score`` and ``meets_requirements`` of every stored result in display
order -- the same reasoning as d2e3f4a5b6c7's backfill: a snapshot only
recomputes when its inputs go stale, so "it refills on the next optimize" would
leave already-optimized builds showing the old misleading rank indefinitely.

Rows with no stored results are left empty; the route falls back to positional
rank there, which is exactly the previous behaviour.

Revision ID: 3fc1a2b4d5e6
Revises: 2eb0357175a0
Create Date: 2026-09-08 00:00:00.000000
"""
import json

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3fc1a2b4d5e6"
down_revision = "2eb0357175a0"
branch_labels = None
depends_on = None


def _ranks(results: list[dict]) -> list[int]:
    """Mirror of nrplanner.changes.serialize_match_ranks over serialized results."""
    order = [
        (not r.get("meets_requirements", True), -(r.get("total_score") or 0))
        for r in results
    ]
    return [1 + sum(1 for other in order if other < key) for key in order]


def upgrade() -> None:
    op.add_column(
        "optimization_snapshot",
        sa.Column(
            "top_match_ranks",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )

    snapshot = sa.table(
        "optimization_snapshot",
        sa.column("id"),
        sa.column("full_results", sa.JSON()),
        sa.column("top_match_ranks", sa.JSON()),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(snapshot.c.id, snapshot.c.full_results)
    ).mappings().all()

    for row in rows:
        results = row["full_results"]
        if isinstance(results, str):  # some drivers hand JSON back as text
            results = json.loads(results or "[]")
        if not results:
            continue
        bind.execute(
            snapshot.update()
            .where(snapshot.c.id == row["id"])
            .values(top_match_ranks=_ranks(results))
        )


def downgrade() -> None:
    op.drop_column("optimization_snapshot", "top_match_ranks")
