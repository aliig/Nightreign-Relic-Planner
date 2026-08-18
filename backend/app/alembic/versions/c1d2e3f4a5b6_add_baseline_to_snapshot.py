"""Add the sticky change baseline to optimization_snapshot

Changes used to be measured against the PREVIOUS RUN, because the snapshot's
layouts served as both cache and diff baseline.  Every run therefore
re-baselined, and a change could be overwritten before the user ever read it
(upload a save, then buy relics in Relic Rites, and the save-to-save story was
gone).  ``baseline`` splits the two: the other columns stay the cache, this one
holds the state the user last acknowledged.

Backfilled from each row's current state, so no existing build reports a
spurious change on its next run.  ``inputs.base_relics_hash`` (the save's own
inventory, staged diff excluded) did not exist before this migration; for rows
with no staged diff it equals relics_hash exactly, and for the rare row written
mid-staging it is the closest honest value — worst case that build narrates one
extra change on its next pure-save run.

Revision ID: c1d2e3f4a5b6
Revises: a0b1c2d3e4f5
Create Date: 2026-08-17 00:00:00.000000
"""
import json

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c1d2e3f4a5b6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_snapshot",
        sa.Column("baseline", sa.JSON(), nullable=True),
    )

    # Typed table construct (not raw SQL): lets SQLAlchemy serialize the dict
    # into the JSON column itself, which raw text params cannot do on Postgres.
    snapshot = sa.table(
        "optimization_snapshot",
        sa.column("id"),
        sa.column("relics_hash"),
        sa.column("build_hash"),
        sa.column("game_data_version"),
        sa.column("optimizer_version"),
        sa.column("staged_signature"),
        sa.column("top_layouts", sa.JSON()),
        sa.column("best_score"),
        sa.column("baseline", sa.JSON()),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            snapshot.c.id,
            snapshot.c.relics_hash,
            snapshot.c.build_hash,
            snapshot.c.game_data_version,
            snapshot.c.optimizer_version,
            snapshot.c.staged_signature,
            snapshot.c.top_layouts,
            snapshot.c.best_score,
        )
    ).mappings().all()

    for row in rows:
        layouts = row["top_layouts"]
        if isinstance(layouts, str):  # some drivers hand JSON back as text
            layouts = json.loads(layouts or "[]")
        bind.execute(
            snapshot.update()
            .where(snapshot.c.id == row["id"])
            .values(
                baseline={
                    "layouts": layouts or [],
                    "best_score": row["best_score"] or 0,
                    "inputs": {
                        "base_relics_hash": row["relics_hash"],
                        "relics_hash": row["relics_hash"],
                        "build_hash": row["build_hash"],
                        "game_data_version": row["game_data_version"],
                        # str(): the runtime writes this as a string, and a
                        # bare int here would never compare equal — every
                        # backfilled row would claim a game_data change on its
                        # next run.
                        "optimizer_version": str(row["optimizer_version"]),
                        "staged_signature": row["staged_signature"],
                    },
                }
            )
        )


def downgrade() -> None:
    op.drop_column("optimization_snapshot", "baseline")
