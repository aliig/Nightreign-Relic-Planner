"""Replace named tiers with weight groups on build table

Revision ID: e6f7a8b9c0d1
Revises: d4e5f6a1b2c3
Create Date: 2026-02-28 00:00:00.000000

Migrates the build table from:
  tiers: dict[str, list[int]]
  family_tiers: dict[str, list[str]]
  tier_weights: dict[str, int] | None

To:
  groups: list[{weight, effects, families}]
  required_effects: list[int]
  required_families: list[str]
  excluded_effects: list[int]
  excluded_families: list[str]
"""
import json

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e6f7a8b9c0d1"
down_revision = "e5f6a1b2c3d4"
branch_labels = None
depends_on = None

# Default weights matching legacy tier system
_LEGACY_DEFAULT_WEIGHTS: dict[str, int] = {
    "preferred":    50,
    "nice_to_have": 25,
    "bonus":        10,
    "avoid":        -20,
}

_DEFAULT_GROUPS = json.dumps([
    {"weight": 50,  "effects": [], "families": []},
    {"weight": 25,  "effects": [], "families": []},
    {"weight": 10,  "effects": [], "families": []},
    {"weight": -20, "effects": [], "families": []},
])


def upgrade() -> None:
    # 1. Add new columns (nullable first so we can populate them)
    op.add_column("build", sa.Column("groups", sa.JSON(), nullable=True))
    op.add_column("build", sa.Column("required_effects", sa.JSON(), nullable=True))
    op.add_column("build", sa.Column("required_families", sa.JSON(), nullable=True))
    op.add_column("build", sa.Column("excluded_effects", sa.JSON(), nullable=True))
    op.add_column("build", sa.Column("excluded_families", sa.JSON(), nullable=True))

    # 2. Data migration: transform existing rows
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, tiers, family_tiers, tier_weights FROM build")
    ).fetchall()

    for row in rows:
        build_id = row[0]
        tiers = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
        family_tiers = json.loads(row[2]) if isinstance(row[2], str) else (row[2] or {})
        tier_weights_override = json.loads(row[3]) if isinstance(row[3], str) else (row[3] or {})

        required_effects = tiers.get("required", [])
        required_families = family_tiers.get("required", [])
        excluded_effects = tiers.get("blacklist", [])
        excluded_families = family_tiers.get("blacklist", [])

        groups = []
        for tier_key, default_w in _LEGACY_DEFAULT_WEIGHTS.items():
            effs = tiers.get(tier_key, [])
            fams = family_tiers.get(tier_key, [])
            if effs or fams:
                weight = tier_weights_override.get(tier_key, default_w)
                groups.append({"weight": weight, "effects": effs, "families": fams})

        # Empty groups gets the default preset groups
        if not groups:
            groups = [
                {"weight": 50,  "effects": [], "families": []},
                {"weight": 25,  "effects": [], "families": []},
                {"weight": 10,  "effects": [], "families": []},
                {"weight": -20, "effects": [], "families": []},
            ]

        conn.execute(
            sa.text(
                "UPDATE build SET groups = :groups,"
                " required_effects = :req_eff,"
                " required_families = :req_fam,"
                " excluded_effects = :excl_eff,"
                " excluded_families = :excl_fam"
                " WHERE id = :id"
            ),
            {
                "groups":    json.dumps(groups),
                "req_eff":   json.dumps(required_effects),
                "req_fam":   json.dumps(required_families),
                "excl_eff":  json.dumps(excluded_effects),
                "excl_fam":  json.dumps(excluded_families),
                "id":        str(build_id),
            },
        )

    # 3. Make new columns NOT NULL with server defaults
    op.alter_column("build", "groups",            nullable=False, server_default=_DEFAULT_GROUPS)
    op.alter_column("build", "required_effects",  nullable=False, server_default="[]")
    op.alter_column("build", "required_families", nullable=False, server_default="[]")
    op.alter_column("build", "excluded_effects",  nullable=False, server_default="[]")
    op.alter_column("build", "excluded_families", nullable=False, server_default="[]")

    # 4. Drop old columns
    op.drop_column("build", "tiers")
    op.drop_column("build", "family_tiers")
    op.drop_column("build", "tier_weights")


def downgrade() -> None:
    # Restore old columns
    op.add_column("build", sa.Column("tiers", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("build", sa.Column("family_tiers", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("build", sa.Column("tier_weights", sa.JSON(), nullable=True))

    # Best-effort reverse migration: put groups back into required/preferred slots
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, groups, required_effects, required_families,"
                " excluded_effects, excluded_families FROM build")
    ).fetchall()

    for row in rows:
        build_id = row[0]
        groups = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or [])
        req_effs = json.loads(row[2]) if isinstance(row[2], str) else (row[2] or [])
        req_fams = json.loads(row[3]) if isinstance(row[3], str) else (row[3] or [])
        excl_effs = json.loads(row[4]) if isinstance(row[4], str) else (row[4] or [])
        excl_fams = json.loads(row[5]) if isinstance(row[5], str) else (row[5] or [])

        tiers: dict = {
            "required": req_effs,
            "preferred": [],
            "nice_to_have": [],
            "bonus": [],
            "avoid": [],
            "blacklist": excl_effs,
        }
        family_tiers: dict = {
            "required": req_fams,
            "preferred": [],
            "nice_to_have": [],
            "bonus": [],
            "avoid": [],
            "blacklist": excl_fams,
        }
        tier_weights: dict = {}

        _w_to_tier = {50: "preferred", 25: "nice_to_have", 10: "bonus", -20: "avoid"}
        for g in groups:
            w = g.get("weight", 0)
            key = _w_to_tier.get(w, "preferred")
            tiers[key].extend(g.get("effects", []))
            family_tiers[key].extend(g.get("families", []))
            if w not in _LEGACY_DEFAULT_WEIGHTS.values():
                tier_weights[key] = w

        conn.execute(
            sa.text(
                "UPDATE build SET tiers = :tiers,"
                " family_tiers = :family_tiers,"
                " tier_weights = :tier_weights"
                " WHERE id = :id"
            ),
            {
                "tiers":        json.dumps(tiers),
                "family_tiers": json.dumps(family_tiers),
                "tier_weights": json.dumps(tier_weights) if tier_weights else None,
                "id":           str(build_id),
            },
        )

    op.drop_column("build", "excluded_families")
    op.drop_column("build", "excluded_effects")
    op.drop_column("build", "required_families")
    op.drop_column("build", "required_effects")
    op.drop_column("build", "groups")
