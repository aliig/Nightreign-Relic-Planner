"""fix stale '%' in build family-name references

Commit 155250e renamed the scarlet-rot / frostbite "attack power up vs
afflicted enemy" effect families in stacking_rules.json, replacing a stray '%'
(a newline placeholder carried over from the source spreadsheet) with a space.
That fixed the source data and every NEW family assignment, but builds saved
beforehand still store the old name -- e.g.
"Attack power up when facing scarlet rot-afflicted%enemy" -- inside their
groups[].families, required_families, excluded_families, family_limits, and
family_weight_floors.

The stale name no longer matches any current family, so (a) the editor chip
renders a literal '%' and (b) the family weight is silently ignored by the
optimizer (get_family_effect_ids returns []).  This one-time migration rewrites
'%' -> ' ' in every stored family-name reference and recomputes build_hash, so
the affected snapshots correctly go stale and re-optimize with the now-working
weight.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-06-20 00:00:00.000000

"""
from alembic import op
from sqlmodel import Session, select

from app.core.build_def import build_def_from_db
from app.models import Build
from nrplanner.changes import build_signature


# revision identifiers, used by Alembic.
revision = 'b4c5d6e7f8a9'
down_revision = 'a3b4c5d6e7f8'
branch_labels = None
depends_on = None


def _fix(name):
    """'%' is never part of a valid family name -- it only ever appears as the
    stale newline placeholder -- so a plain replace is safe and matches what
    commit 155250e did to stacking_rules.json."""
    return name.replace('%', ' ') if isinstance(name, str) else name


def upgrade():
    with Session(op.get_bind()) as session:
        for build in session.exec(select(Build)).all():
            new_groups = []
            for g in (build.groups or []):
                fams = g.get('families', [])
                fixed = [_fix(f) for f in fams]
                new_groups.append({**g, 'families': fixed} if fixed != fams else g)

            new_required = [_fix(f) for f in (build.required_families or [])]
            new_excluded = [_fix(f) for f in (build.excluded_families or [])]
            new_limits = {_fix(k): v for k, v in (build.family_limits or {}).items()}
            new_floors = {_fix(k): v for k, v in (build.family_weight_floors or {}).items()}

            if (
                new_groups == (build.groups or [])
                and new_required == (build.required_families or [])
                and new_excluded == (build.excluded_families or [])
                and new_limits == (build.family_limits or {})
                and new_floors == (build.family_weight_floors or {})
            ):
                continue

            # Reassign new objects so SQLAlchemy detects the change -- a plain
            # Column(JSON) does not track in-place list/dict mutation.
            build.groups = new_groups
            build.required_families = new_required
            build.excluded_families = new_excluded
            build.family_limits = new_limits
            build.family_weight_floors = new_floors
            build.build_hash = build_signature(build_def_from_db(build))
            session.add(build)
        session.commit()


def downgrade():
    # A corrected, space-separated family name is indistinguishable from one
    # that was always spaced, so the original '%' placeholder cannot be safely
    # restored. Nothing to undo.
    pass
