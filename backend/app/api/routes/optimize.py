"""Vessel optimization endpoint.

Supports two modes:
- **DB mode** (authenticated): provide build_id + character_id — data loaded from DB.
- **Inline mode** (any): provide a full BuildDefinition + list[OwnedRelic].

The character class used for vessel filtering is always taken from build_def.character.
"""
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import GameDataDep, OptionalUser, SessionDep
from app.core.config import settings
from app.models import Build, CharacterSlot, Relic
from nrplanner.constants import CHARACTER_NAMES
from nrplanner.models import BuildDefinition, OwnedRelic, RelicInventory, VesselResult
from nrplanner.scoring import BuildScorer
from nrplanner.optimizer import VesselOptimizer

router = APIRouter(prefix="/optimize", tags=["optimize"])

# Map character name → 1-based hero index matching the CSV heroType column (1-10),
# NOT the NPC text file IDs from CHARACTER_NAME_ID.
_CHAR_NAME_TO_HERO_TYPE: dict[str, int] = {
    name: idx for idx, name in enumerate(CHARACTER_NAMES, start=1)
}


def _resolve_hero_type(character_name: str) -> int:
    hero_type = _CHAR_NAME_TO_HERO_TYPE.get(character_name)
    if hero_type is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown character '{character_name}'. "
                   f"Valid names: {list(_CHAR_NAME_TO_HERO_TYPE)}",
        )
    return hero_type


def _run_optimizer(
    build_def: BuildDefinition,
    owned_relics: list[OwnedRelic],
    character_name: str,
    top_n: int,
    max_per_vessel: int,
    ds: Any,
) -> list[VesselResult]:
    hero_type = _resolve_hero_type(character_name)
    inventory = RelicInventory.from_owned_relics(owned_relics)
    scorer = BuildScorer(ds)
    optimizer = VesselOptimizer(ds, scorer)
    return optimizer.optimize_all_vessels(
        build_def, inventory, hero_type,
        top_n=top_n, max_per_vessel=max_per_vessel,
    )


class OptimizeRequest(BaseModel):
    # --- DB mode (authenticated) ---
    build_id: uuid.UUID | None = None
    character_id: uuid.UUID | None = None

    # --- Inline mode (anonymous or authenticated) ---
    build: BuildDefinition | None = None
    relics: list[OwnedRelic] | None = None

    # --- Common params ---
    top_n: int = Field(default=10, ge=1, le=50)
    max_per_vessel: int = Field(default=3, ge=1, le=5)


@router.post("/", response_model=list[VesselResult])
def run_optimize(
    req: OptimizeRequest,
    ds: GameDataDep,
    current_user: OptionalUser,
    session: SessionDep,
) -> list[VesselResult]:
    """Run vessel optimization and return ranked VesselResults.

    **DB mode** — authenticated users may supply `build_id` + `character_id` to
    reference persisted data:
    ```json
    { "build_id": "...", "character_id": "..." }
    ```

    **Inline mode** — supply the full build definition and relic list:
    ```json
    { "build": {...}, "relics": [...] }
    ```

    The character class used for vessel filtering is taken from `build.character` in
    both modes. This matches the class selected when the build was created.
    """
    using_db = req.build_id is not None or req.character_id is not None
    using_inline = req.build is not None or req.relics is not None

    if using_db and using_inline:
        raise HTTPException(
            status_code=422,
            detail="Provide either (build_id + character_id) or (build + relics), not both.",
        )

    if using_db:
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required for DB mode")
        if req.build_id is None or req.character_id is None:
            raise HTTPException(
                status_code=422,
                detail="DB mode requires both build_id and character_id.",
            )

        db_build = session.get(Build, req.build_id)
        if not db_build or db_build.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Build not found")

        char_slot = session.get(CharacterSlot, req.character_id)
        if not char_slot or char_slot.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Character not found")

        build_def = BuildDefinition(
            id=str(db_build.id),
            name=db_build.name,
            character=db_build.character,
            tiers=db_build.tiers,
            family_tiers=db_build.family_tiers,
            include_deep=db_build.include_deep,
            curse_max=db_build.curse_max,
            tier_weights=db_build.tier_weights,
            pinned_relics=db_build.pinned_relics or [],
        )

        db_relics = session.exec(
            select(Relic).where(Relic.character_id == req.character_id)
        ).all()
        owned_relics = [
            OwnedRelic(
                ga_handle=r.ga_handle,
                item_id=r.item_id,
                real_id=r.real_id,
                color=r.color,
                effects=[r.effect_1, r.effect_2, r.effect_3],
                curses=[r.curse_1, r.curse_2, r.curse_3],
                is_deep=r.is_deep,
                name=r.name,
                tier=r.tier,
            )
            for r in db_relics
        ]

    else:
        if req.build is None or req.relics is None:
            raise HTTPException(
                status_code=422,
                detail="Inline mode requires build and relics.",
            )
        if len(req.relics) > settings.MAX_RELICS_PER_OPTIMIZE:
            raise HTTPException(
                status_code=422,
                detail=f"Too many relics (max {settings.MAX_RELICS_PER_OPTIMIZE}).",
            )
        build_def = req.build
        owned_relics = req.relics

    return _run_optimizer(
        build_def, owned_relics, build_def.character,
        req.top_n, req.max_per_vessel, ds,
    )
