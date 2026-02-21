"""Public game-data endpoints — no auth required.

All data is static and loaded once at startup via the SourceDataHandler singleton.
"""
from typing import Any

from fastapi import APIRouter

from app.api.deps import GameDataDep
from nrplanner.constants import CHARACTER_NAME_ID, CHARACTER_NAMES, RELIC_COLOR_HEX
from nrplanner.models import TIERS

router = APIRouter(prefix="/game", tags=["game"])


@router.get("/effects")
def get_effects(ds: GameDataDep) -> list[dict[str, Any]]:
    """All effects with metadata: id, name, family, stacking_type, is_debuff, allow_per_character."""
    return ds.get_all_effects_list()


@router.get("/families")
def get_families(ds: GameDataDep) -> list[dict[str, Any]]:
    """All effect families with member names and IDs."""
    return ds.get_all_families_list()


@router.get("/characters")
def get_characters() -> list[dict[str, Any]]:
    """Playable character names with their hero_type IDs."""
    return [
        {"name": name, "hero_type": hero_type}
        for name, hero_type in zip(CHARACTER_NAMES[:-1], CHARACTER_NAME_ID)
        # Exclude "All" sentinel (last entry has no hero_type)
    ]


@router.get("/vessels/{hero_type}")
def get_vessels(hero_type: int, ds: GameDataDep) -> list[dict[str, Any]]:
    """All vessels available for a hero (hero-specific + shared)."""
    return ds.get_all_vessels_for_hero(hero_type)


@router.get("/tiers")
def get_tiers() -> list[dict[str, Any]]:
    """Tier configuration: required, preferred, nice_to_have, avoid, blacklist."""
    return [t.model_dump() for t in TIERS]


@router.get("/colors")
def get_colors() -> dict[str, str]:
    """Relic color names mapped to their display hex codes."""
    return {k: v for k, v in RELIC_COLOR_HEX.items() if k is not None}
