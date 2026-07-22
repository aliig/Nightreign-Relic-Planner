"""Vessel optimization endpoint.

Supports two modes:
- **DB mode** (authenticated): provide build_id + profile_id — data loaded from DB.
- **Inline mode** (any): provide a full BuildDefinition + list[OwnedRelic].

The character class used for vessel filtering is always taken from build_def.character.

DB-mode runs also upsert an OptimizationSnapshot keyed by (build_id, slot_index)
and return a BuildChange describing how the best arrangement moved versus the
previous snapshot — this powers the "your build may have improved" notification
after a newer save is uploaded.  Inline (anonymous) runs persist nothing and
return change=null.
"""
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import (
    CurrentUser,
    GameDataDep,
    OptionalUser,
    OptimizerPoolDep,
    SessionDep,
)
from app.core.build_def import build_def_from_db
from app.core.config import settings
from app.core.db import engine
from app.core.game_data import game_data_version
from app.models import (
    Build,
    OptimizationSnapshot,
    Profile,
    Relic,
    get_datetime_utc,
)
from nrplanner.changes import (
    build_signature,
    diff_results,
    fingerprint_owned,
    relevant_relics_signature,
    relic_fingerprint,
    relics_signature,
    serialize_top_layouts,
)
from nrplanner.constants import CHARACTER_NAMES
from nrplanner.cumulative import summarize_cumulative_effects
from nrplanner.models import (
    BuildChange,
    BuildDefinition,
    OwnedRelic,
    RelicInventory,
    VesselResult,
)
from nrplanner.optimizer import OPTIMIZER_VERSION, VesselOptimizer
from nrplanner.scoring import BuildScorer

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


class LockedSlot(BaseModel):
    """A relic frozen in its exact slot during a single-slot re-optimization."""
    slot_index: int
    ga_handle: int


class SlotAlternativeRequest(BaseModel):
    """Re-optimize a single vessel slot, freezing every other slot in place.

    Powers the "strike a relic" UI: keep each relic in ``locked_slots`` in its
    exact slot, exclude the struck relic(s), and re-fill only
    ``struck_slot_index`` with the next-best relic.  Freezing positions (rather
    than positionless pinning) guarantees the rest of the layout — and its
    scores — stay put, so the total moves only as a function of the struck slot.
    Same dual-mode inventory inputs as :class:`OptimizeRequest` (DB mode =
    build_id + profile_id; inline mode = build + relics).  Persists nothing.
    """
    # --- DB mode (authenticated) ---
    build_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None

    # --- Inline mode (anonymous or authenticated) ---
    build: BuildDefinition | None = None
    relics: list[OwnedRelic] | None = None

    # --- Strike params ---
    vessel_id: int
    struck_slot_index: int
    locked_slots: list[LockedSlot] = Field(default_factory=list)
    excluded_ga_handles: list[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request resolution (shared by both endpoints)
# ---------------------------------------------------------------------------

@dataclass
class _SnapshotCtx:
    """Identity of the snapshot a DB-mode run reads/writes (None for inline mode)."""
    owner_id: uuid.UUID
    build_id: uuid.UUID
    build_name: str
    slot_index: int


def _owned_from_db(relics: list[Relic]) -> list[OwnedRelic]:
    return [
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
        for r in relics
    ]


def _resolve(
    req: OptimizeRequest,
    current_user: Any,
    session: Session,
) -> tuple[BuildDefinition, list[OwnedRelic], _SnapshotCtx | None]:
    """Resolve a request into (build_def, owned_relics, snapshot_ctx).

    snapshot_ctx is None for inline mode (nothing persisted).  Raises
    HTTPException on the same auth/validation conditions as before.
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

        build_def = build_def_from_db(db_build)
        db_relics = session.exec(
            select(Relic).where(Relic.profile_id == req.profile_id)
        ).all()
        owned_relics = _owned_from_db(list(db_relics))
        ctx = _SnapshotCtx(
            owner_id=current_user.id,
            build_id=db_build.id,
            build_name=db_build.name,
            slot_index=profile.slot_index,
        )
        return build_def, owned_relics, ctx

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
    return req.build, req.relics, None


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


# ---------------------------------------------------------------------------
# Snapshot persistence + change detection (DB mode only)
# ---------------------------------------------------------------------------

def _attribute_cause(
    snap: OptimizationSnapshot | None, relics_hash: str, build_hash: str, gdv: str
) -> str | None:
    """Which input moved since the last snapshot — for the BuildChange.cause field."""
    if snap is None:
        return None
    relics_changed = snap.relics_hash != relics_hash
    build_changed = snap.build_hash != build_hash
    data_changed = (
        snap.game_data_version != gdv or snap.optimizer_version != OPTIMIZER_VERSION
    )
    if relics_changed and build_changed:
        return "mixed"
    if relics_changed:
        return "relics"
    if build_changed:
        return "build_edit"
    if data_changed:
        return "game_data"
    return None


def _apply_snapshot(
    session: Session,
    ctx: _SnapshotCtx,
    build_def: BuildDefinition,
    owned_relics: list[OwnedRelic],
    results: list[VesselResult],
    ds: Any,
    top_n: int,
    max_per_vessel: int,
) -> BuildChange:
    """Diff a fresh optimization against the stored snapshot, then upsert it.

    Returns the BuildChange (build identity + cause filled).  Commits the session.
    """
    snap = session.exec(
        select(OptimizationSnapshot).where(
            OptimizationSnapshot.build_id == ctx.build_id,
            OptimizationSnapshot.slot_index == ctx.slot_index,
        )
    ).first()

    relics_hash = relics_signature(owned_relics)
    build_hash = build_signature(build_def)
    gdv = game_data_version()
    relevant_hash = relevant_relics_signature(
        build_def, [(fingerprint_owned(r), r.ga_handle) for r in owned_relics], ds
    )

    change = diff_results(snap.top_layouts if snap else None, results)
    change.build_id = str(ctx.build_id)
    change.build_name = ctx.build_name
    change.slot_index = ctx.slot_index
    change.cause = _attribute_cause(snap, relics_hash, build_hash, gdv)

    top_layouts = serialize_top_layouts(results)
    # cumulative_effects is a serve-time presentation field (recomputed on every
    # response, incl. GET /snapshot) — never persist it in the snapshot.
    full_results = [r.model_dump(mode="json", exclude={"cumulative_effects"}) for r in results]
    best_score = max((r.total_score for r in results), default=0)
    any_truncated = any(r.search_truncated for r in results)
    change_json = change.model_dump(mode="json")

    if snap is None:
        snap = OptimizationSnapshot(
            owner_id=ctx.owner_id,
            build_id=ctx.build_id,
            slot_index=ctx.slot_index,
            relics_hash=relics_hash,
            build_hash=build_hash,
            game_data_version=gdv,
            optimizer_version=OPTIMIZER_VERSION,
            relevant_relics_hash=relevant_hash,
            top_n=top_n,
            max_per_vessel=max_per_vessel,
            top_layouts=top_layouts,
            full_results=full_results,
            best_score=best_score,
            any_truncated=any_truncated,
            last_change=change_json,
            reviewed=True,
        )
    else:
        snap.relics_hash = relics_hash
        snap.build_hash = build_hash
        snap.game_data_version = gdv
        snap.optimizer_version = OPTIMIZER_VERSION
        snap.relevant_relics_hash = relevant_hash
        snap.top_n = top_n
        snap.max_per_vessel = max_per_vessel
        snap.top_layouts = top_layouts
        snap.full_results = full_results
        snap.best_score = best_score
        snap.any_truncated = any_truncated
        snap.last_change = change_json
        snap.reviewed = True
        snap.updated_at = get_datetime_utc()
    session.add(snap)
    session.commit()
    return change


# ---------------------------------------------------------------------------
# Cumulative stacked-effect summary (serve-time enrichment; not persisted)
# ---------------------------------------------------------------------------

def _attach_cumulative(results: list[VesselResult], ds: Any) -> None:
    """Populate each VesselResult.cumulative_effects in place from placed relics.

    Pure/derived: computed fresh on every response (POST, stream, snapshot,
    strike) so the field never needs to live in the persisted snapshot.
    """
    for r in results:
        ids: list[int] = []
        for a in r.assignments:
            if a.relic is not None:
                ids.extend(a.relic.all_effects)
        r.cumulative_effects = summarize_cumulative_effects(ids, ds)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", response_model=list[VesselResult])
def run_optimize(
    req: OptimizeRequest,
    ds: GameDataDep,
    current_user: OptionalUser,
    session: SessionDep,
    executor: OptimizerPoolDep = None,
) -> list[VesselResult]:
    """Run vessel optimization and return ranked VesselResults.

    DB mode (`build_id` + `profile_id`) additionally upserts the build's
    optimization snapshot as a side effect.  See module docstring.
    """
    build_def, owned_relics, ctx = _resolve(req, current_user, session)
    results = _run_optimizer(
        build_def, owned_relics, build_def.character,
        req.top_n, req.max_per_vessel, ds, executor=executor,
    )
    if ctx is not None:
        try:
            _apply_snapshot(session, ctx, build_def, owned_relics, results, ds,
                            req.top_n, req.max_per_vessel)
        except Exception:
            # Snapshotting is best-effort — never fail the optimize response.
            session.rollback()
    _attach_cumulative(results, ds)
    return results


@router.post("/stream")
def run_optimize_stream(
    req: OptimizeRequest,
    ds: GameDataDep,
    current_user: OptionalUser,
    session: SessionDep,
    executor: OptimizerPoolDep = None,
) -> StreamingResponse:
    """Same as POST /optimize/ but streams SSE progress events while running.

    Emits ``data:`` lines::

        {"type": "progress", "vessel": 3, "total": 12, "name": "Iron Sentinel"}
        {"type": "result",   "data": [...VesselResult...], "change": {...}|null}
        {"type": "error",    "detail": "..."}

    ``change`` is a BuildChange for DB-mode runs (null for inline/anonymous).
    HTTP-level errors (auth, bad request, not found) are raised before streaming.
    """
    # Resolve up-front so auth/validation errors surface as normal HTTP errors.
    build_def, owned_relics, ctx = _resolve(req, current_user, session)

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
                    results = event["data"]
                    change = None
                    if ctx is not None:
                        # Fresh session: the request-scoped one may be closed by
                        # the time this StreamingResponse generator runs.
                        try:
                            with Session(engine) as snap_session:
                                change = _apply_snapshot(
                                    snap_session, ctx, build_def, owned_relics,
                                    results, ds, req.top_n, req.max_per_vessel,
                                )
                        except Exception:
                            change = None  # best-effort; don't break the stream
                    _attach_cumulative(results, ds)
                    payload = {
                        "type": "result",
                        "data": [r.model_dump(mode="json") for r in results],
                        "change": change.model_dump(mode="json") if change else None,
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


@router.post("/slot-alternative", response_model=VesselResult | None)
def optimize_slot_alternative(
    req: SlotAlternativeRequest,
    ds: GameDataDep,
    current_user: OptionalUser,
    session: SessionDep,
) -> VesselResult | None:
    """Re-optimize one vessel slot while every other slot stays frozen in place.

    Freezes ``locked_slots`` (each relic in its exact slot), removes
    ``excluded_ga_handles`` from the candidate pool, and re-fills only
    ``struck_slot_index`` with the next-best relic, so the rest of the layout
    (and its scores) never moves.  Returns ``null`` only when the whole vessel
    comes back empty; the common "no replacement" case returns a vessel whose
    struck slot relic is null.  Auth/ownership is enforced by ``_resolve``
    exactly as for ``/optimize``; nothing is persisted (no snapshot).
    """
    base = OptimizeRequest(
        build_id=req.build_id,
        profile_id=req.profile_id,
        build=req.build,
        relics=req.relics,
    )
    build_def, owned_relics, _ctx = _resolve(base, current_user, session)

    excluded = set(req.excluded_ga_handles)
    # Freeze every other slot in its exact position. A struck/excluded relic is
    # never treated as locked, and the struck slot itself is never locked.
    locked = {
        ls.slot_index: ls.ga_handle
        for ls in req.locked_slots
        if ls.ga_handle not in excluded and ls.slot_index != req.struck_slot_index
    }
    build_def = build_def.model_copy(update={"excluded_relics": list(excluded)})

    vessel_data = ds.get_vessel_data(req.vessel_id)
    if vessel_data is None:
        raise HTTPException(status_code=404, detail="Vessel not found")
    vessel_data["_id"] = req.vessel_id

    inventory = RelicInventory.from_owned_relics(owned_relics)
    optimizer = VesselOptimizer(ds, BuildScorer(ds))
    results = optimizer.optimize_locked_slot(
        build_def, inventory, vessel_data, locked, req.struck_slot_index, top_n=1)
    if not results:
        return None
    # optimize_locked_slot leaves vessel_id for the caller to assign.
    results[0].vessel_id = req.vessel_id
    _attach_cumulative(results, ds)
    return results[0]


class SnapshotResponse(BaseModel):
    """Cached optimization results from a fresh snapshot."""
    results: list[VesselResult]
    last_change: BuildChange | None = None
    computed_at: str | None = None


@router.get("/snapshot", response_model=SnapshotResponse | None)
def get_snapshot(
    build_id: uuid.UUID,
    profile_id: uuid.UUID,
    current_user: CurrentUser,
    session: SessionDep,
    ds: GameDataDep,
) -> SnapshotResponse | None:
    """Return cached optimization results if the snapshot is fresh.

    A snapshot is fresh when its relics_hash and build_hash match the current
    live inputs (cached on the Profile and Build rows at write time).
    Returns null if stale or missing — the frontend should then trigger a
    fresh optimization.
    """
    db_build = session.get(Build, build_id)
    if not db_build or db_build.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Build not found")

    profile = session.get(Profile, profile_id)
    if not profile or profile.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Profile not found")

    snap = session.exec(
        select(OptimizationSnapshot).where(
            OptimizationSnapshot.build_id == build_id,
            OptimizationSnapshot.slot_index == profile.slot_index,
        )
    ).first()
    if snap is None:
        return None

    # Treat missing hashes as stale — None == None must never count as fresh
    # (legacy rows predating hash caching, or builds/profiles written by a
    # path that skipped hash computation).  Version checks implement the
    # model's documented contract: a solver or game-data change invalidates
    # stored results even when the inputs' hashes still match.
    if (
        snap.relics_hash is None
        or snap.build_hash is None
        or snap.build_hash != db_build.build_hash
        or snap.optimizer_version != OPTIMIZER_VERSION
        or snap.game_data_version != game_data_version()
    ):
        return None

    if snap.relics_hash != profile.relics_hash:
        # Whole-inventory hash moved, but the optimum depends only on the
        # build-RELEVANT subset (see relevant_relics_signature).  Serve the
        # snapshot when that subset is unchanged; legacy rows without the
        # stored hash stay stale (one re-optimize refills them).
        if snap.relevant_relics_hash is None:
            return None
        db_relics = session.exec(
            select(Relic).where(Relic.profile_id == profile.id)
        ).all()
        pairs = [
            (
                relic_fingerprint(
                    r.real_id,
                    (r.effect_1, r.effect_2, r.effect_3),
                    (r.curse_1, r.curse_2, r.curse_3),
                ),
                r.ga_handle,
            )
            for r in db_relics
        ]
        live_relevant = relevant_relics_signature(
            build_def_from_db(db_build), pairs, ds
        )
        if snap.relevant_relics_hash != live_relevant:
            return None

    # Snapshot is fresh — serve the stored full results.  top_layouts is the
    # compact diff baseline and cannot reconstruct VesselResults; legacy rows
    # without full_results are treated as stale (one re-optimize refills).
    if not snap.full_results:
        return None
    results = [VesselResult(**layout) for layout in snap.full_results]
    _attach_cumulative(results, ds)
    last_change = BuildChange(**snap.last_change) if snap.last_change else None

    return SnapshotResponse(
        results=results,
        last_change=last_change,
        computed_at=snap.computed_at.isoformat() if snap.computed_at else None,
    )


class BuildSnapshotSummary(BaseModel):
    """A build's most recent optimization change.

    Embeds the full :class:`BuildChange` so the frontend can render rich,
    relic-aware change text (verdict + relative % + which relics moved) and
    filter on ``reviewed`` — all without a full snapshot load.  Deciding which
    changes are "interesting" is left to the frontend formatter.
    """
    build_id: str
    change: BuildChange | None = None
    reviewed: bool = True
    best_score: int = 0
    computed_at: str | None = None


@router.get("/summaries", response_model=list[BuildSnapshotSummary])
def list_build_summaries(
    current_user: CurrentUser,
    session: SessionDep,
) -> list[BuildSnapshotSummary]:
    """Return the most recent optimization change for each of the user's builds.

    Powers both the subtle per-build score badge and the "Changes since your
    last save" list on the builds page.  Only returns builds that have at least
    one snapshot; builds that have never been optimized are omitted.
    """
    snaps = session.exec(
        select(OptimizationSnapshot).where(
            OptimizationSnapshot.owner_id == current_user.id,
        )
    ).all()

    summaries: list[BuildSnapshotSummary] = []
    for snap in snaps:
        change = BuildChange(**snap.last_change) if snap.last_change else None
        summaries.append(BuildSnapshotSummary(
            build_id=str(snap.build_id),
            change=change,
            reviewed=snap.reviewed,
            best_score=snap.best_score,
            computed_at=snap.computed_at.isoformat() if snap.computed_at else None,
        ))
    return summaries


@router.post("/summaries/{build_id}/reviewed", status_code=204)
def mark_change_reviewed(
    build_id: uuid.UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    """Mark a build's change as seen, removing it from the unread changes list.

    Flips ``reviewed`` on every snapshot the user owns for this build (a build
    may have one snapshot per profile slot).  Idempotent.
    """
    snaps = session.exec(
        select(OptimizationSnapshot).where(
            OptimizationSnapshot.build_id == build_id,
            OptimizationSnapshot.owner_id == current_user.id,
        )
    ).all()
    for snap in snaps:
        snap.reviewed = True
        session.add(snap)
    session.commit()
