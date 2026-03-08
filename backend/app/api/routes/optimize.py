"""Vessel optimization endpoint.

Supports two modes:
- **DB mode** (authenticated): provide build_id + profile_id — data loaded from DB.
- **Inline mode** (any): provide a full BuildDefinition + list[OwnedRelic].

The character class used for vessel filtering is always taken from build_def.character.
"""
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import GameDataDep, OptionalUser, OptimizerPoolDep, SessionDep
from app.core.config import settings
from app.models import Build, Profile, Relic
from nrplanner.constants import CHARACTER_NAMES
from nrplanner.models import BuildDefinition, OwnedRelic, RelicInventory, VesselResult, WeightGroup
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
    executor: Any = None,
) -> list[VesselResult]:
    hero_type = _resolve_hero_type(character_name)
    inventory = RelicInventory.from_owned_relics(owned_relics)
    scorer = BuildScorer(ds)
    optimizer = VesselOptimizer(ds, scorer)
    return optimizer.optimize_all_vessels(
        build_def, inventory, hero_type,
        top_n=top_n, max_per_vessel=max_per_vessel,
        executor=executor,
    )


class OptimizeRequest(BaseModel):
    # --- DB mode (authenticated) ---
    build_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None

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
    executor: OptimizerPoolDep = None,
) -> list[VesselResult]:
    """Run vessel optimization and return ranked VesselResults.

    **DB mode** — authenticated users may supply `build_id` + `profile_id` to
    reference persisted data:
    ```json
    { "build_id": "...", "profile_id": "..." }
    ```

    **Inline mode** — supply the full build definition and relic list:
    ```json
    { "build": {...}, "relics": [...] }
    ```

    The character class used for vessel filtering is taken from `build.character` in
    both modes. This matches the class selected when the build was created.
    """
    using_db = req.build_id is not None or req.profile_id is not None
    using_inline = req.build is not None or req.relics is not None

    if using_db and using_inline:
        raise HTTPException(
            status_code=422,
            detail="Provide either (build_id + profile_id) or (build + relics), not both.",
        )

    if using_db:
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required for DB mode")
        if req.build_id is None or req.profile_id is None:
            raise HTTPException(
                status_code=422,
                detail="DB mode requires both build_id and profile_id.",
            )

        db_build = session.get(Build, req.build_id)
        if not db_build or db_build.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Build not found")

        profile = session.get(Profile, req.profile_id)
        if not profile or profile.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Profile not found")

        build_def = BuildDefinition(
            id=str(db_build.id),
            name=db_build.name,
            character=db_build.character,
            groups=[WeightGroup(**g) for g in (db_build.groups or [])],
            required_effects=db_build.required_effects or [],
            required_families=db_build.required_families or [],
            excluded_effects=db_build.excluded_effects or [],
            excluded_families=db_build.excluded_families or [],
            include_deep=db_build.include_deep,
            curse_max=db_build.curse_max,
            pinned_relics=db_build.pinned_relics or [],
            excluded_stacking_categories=db_build.excluded_stacking_categories or [],
            effect_limits={int(k): v for k, v in (db_build.effect_limits or {}).items()},
            family_limits=db_build.family_limits or {},
        )

        db_relics = session.exec(
            select(Relic).where(Relic.profile_id == req.profile_id)
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
        req.top_n, req.max_per_vessel, ds, executor=executor,
    )


@router.post("/stream")
def run_optimize_stream(
    req: OptimizeRequest,
    ds: GameDataDep,
    current_user: OptionalUser,
    session: SessionDep,
    executor: OptimizerPoolDep = None,
) -> StreamingResponse:
    """Same as POST /optimize/ but streams SSE progress events while running.

    Emits ``data:`` lines in the format::

        {"type": "progress", "vessel": 3, "total": 12, "name": "Iron Sentinel"}
        {"type": "result",   "data": [...VesselResult...]}
        {"type": "error",    "detail": "..."}   (on optimizer failure)

    HTTP-level errors (auth, bad request, not found) are raised normally
    before streaming begins.
    """
    # --- Resolve build_def + owned_relics (may raise HTTPException) ---
    using_db = req.build_id is not None or req.profile_id is not None
    using_inline = req.build is not None or req.relics is not None

    if using_db and using_inline:
        raise HTTPException(
            status_code=422,
            detail="Provide either (build_id + profile_id) or (build + relics), not both.",
        )

    if using_db:
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required for DB mode")
        if req.build_id is None or req.profile_id is None:
            raise HTTPException(
                status_code=422,
                detail="DB mode requires both build_id and profile_id.",
            )

        db_build = session.get(Build, req.build_id)
        if not db_build or db_build.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Build not found")

        profile = session.get(Profile, req.profile_id)
        if not profile or profile.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Profile not found")

        build_def = BuildDefinition(
            id=str(db_build.id),
            name=db_build.name,
            character=db_build.character,
            groups=[WeightGroup(**g) for g in (db_build.groups or [])],
            required_effects=db_build.required_effects or [],
            required_families=db_build.required_families or [],
            excluded_effects=db_build.excluded_effects or [],
            excluded_families=db_build.excluded_families or [],
            include_deep=db_build.include_deep,
            curse_max=db_build.curse_max,
            pinned_relics=db_build.pinned_relics or [],
            excluded_stacking_categories=db_build.excluded_stacking_categories or [],
            effect_limits={int(k): v for k, v in (db_build.effect_limits or {}).items()},
            family_limits=db_build.family_limits or {},
        )

        db_relics = session.exec(
            select(Relic).where(Relic.profile_id == req.profile_id)
        ).all()
        owned_relics: list[OwnedRelic] = [
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

    # --- Streaming generator ---
    def _generate():
        try:
            hero_type = _resolve_hero_type(build_def.character)
            inventory = RelicInventory.from_owned_relics(owned_relics)
            scorer = BuildScorer(ds)
            optimizer = VesselOptimizer(ds, scorer)
            for event in optimizer.optimize_vessels_streaming(
                build_def, inventory, hero_type, req.top_n, req.max_per_vessel,
                executor=executor,
            ):
                if event["type"] == "result":
                    payload = {
                        "type": "result",
                        "data": [r.model_dump(mode="json") for r in event["data"]],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                else:
                    yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
