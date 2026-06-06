"""Shared converter: a persisted Build row -> nrplanner BuildDefinition.

Single source of truth so the build signature computed when a snapshot is
written (optimize) and when it is reset (build edit) can never drift.
"""
from app.models import Build
from nrplanner.models import BuildDefinition, WeightGroup


def build_def_from_db(build: Build) -> BuildDefinition:
    return BuildDefinition(
        id=str(build.id),
        name=build.name,
        character=build.character,
        groups=[WeightGroup(**g) for g in (build.groups or [])],
        required_effects=build.required_effects or [],
        required_families=build.required_families or [],
        excluded_effects=build.excluded_effects or [],
        excluded_families=build.excluded_families or [],
        include_deep=build.include_deep,
        curse_max=build.curse_max,
        default_curse_weight=build.default_curse_weight,
        pinned_relics=build.pinned_relics or [],
        excluded_stacking_categories=build.excluded_stacking_categories or [],
        effect_limits={int(k): v for k, v in (build.effect_limits or {}).items()},
        family_limits=build.family_limits or {},
    )
