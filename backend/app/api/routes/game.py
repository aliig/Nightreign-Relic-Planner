"""Public game-data endpoints — no auth required.

All data is static and loaded once at startup via the SourceDataHandler singleton.
"""
from typing import Any

from fastapi import APIRouter, HTTPException
from nrplanner.constants import CHARACTER_NAME_ID, CHARACTER_NAMES, RELIC_COLOR_HEX
from nrplanner.cumulative import summarize_cumulative_effects
from nrplanner.generator import GenerationError
from nrplanner.models import CumulativeEffectGroup
from pydantic import BaseModel

from app.api.deps import GameDataDep
from app.core.game_data import RelicGeneratorDep
from app.models import GeneratedRelicPublic, RollRelicRequest

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
        for name, hero_type in zip(CHARACTER_NAMES[:-1], CHARACTER_NAME_ID, strict=True)
        # Exclude "All" sentinel (last entry has no hero_type)
    ]


@router.get("/vessels/{hero_type}")
def get_vessels(hero_type: int, ds: GameDataDep) -> list[dict[str, Any]]:
    """All vessels available for a hero (hero-specific + shared)."""
    return ds.get_all_vessels_for_hero(hero_type)


@router.get("/stacking-categories")
def get_stacking_categories(ds: GameDataDep) -> list[dict[str, Any]]:
    """Mutually-exclusive effect groups available for bulk exclusion."""
    return ds.get_stacking_categories()


@router.get("/colors")
def get_colors() -> dict[str, str]:
    """Relic color names mapped to their display hex codes."""
    return {k: v for k, v in RELIC_COLOR_HEX.items() if k is not None}


class CumulativeRequest(BaseModel):
    # One flat list of effect IDs (effects + curses, duplicates kept) per loadout.
    effect_id_groups: list[list[int]]


@router.post("/cumulative-effects")
def cumulative_effects(
    body: CumulativeRequest, ds: GameDataDep
) -> list[list[CumulativeEffectGroup]]:
    """Cumulative stacked-effect summary for each group of effect IDs.

    Stateless game-data computation (no auth) so anonymous loadouts can show the
    same % bonuses the optimizer does. Mirrors the optimizer's per-vessel summary.
    """
    return [summarize_cumulative_effects(ids, ds) for ids in body.effect_id_groups]


@router.post("/roll-relic", response_model=GeneratedRelicPublic)
def roll_relic(
    body: RollRelicRequest, generator: RelicGeneratorDep
) -> GeneratedRelicPublic:
    """Roll one relic the way an in-game purchase does (weighted-random effects).

    Stateless game-data computation (no auth). ``mode="random"`` is a surprise
    purchase (color/tier from the acquisition odds); ``mode="targeted"`` uses the
    given color+tier. The effect roll is always exact; ``odds_source`` reports the
    fidelity of the color/tier meta-roll.
    """
    try:
        relic = generator.roll(
            is_deep=body.is_deep,
            version=body.version,
            mode=body.mode,
            color=body.color,
            tier=body.tier,
            seed=body.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GenerationError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GeneratedRelicPublic(
        real_id=relic.real_id,
        item_id=relic.item_id,
        color=relic.color,
        tier=relic.tier,
        is_deep=relic.is_deep,
        effects=list(relic.effects),
        curses=list(relic.curses),
        odds_source=relic.odds_source,
        name=relic.name,
        effect_names=list(relic.effect_names),
        curse_names=list(relic.curse_names),
    )
