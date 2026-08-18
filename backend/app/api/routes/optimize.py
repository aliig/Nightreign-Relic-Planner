"""Vessel optimization endpoint.

Supports two modes:
- **DB mode** (authenticated): provide build_id + profile_id — data loaded from DB.
- **Inline mode** (any): provide a full BuildDefinition + list[OwnedRelic].

The character class used for vessel filtering is always taken from build_def.character.

DB mode optionally accepts the client's staged in-app diff (staged_sells +
staged_mints, see app.core.staged): the run then uses the EFFECTIVE inventory
— profile relics minus sells plus validated mints under negative synthetic
handles — so results mirror the app's live-document state. Because snapshot
hashes are content-based, a staged run's snapshot becomes valid for the pure
save the user gets by actually exporting and re-uploading those edits.

DB-mode runs also upsert an OptimizationSnapshot keyed by (build_id, slot_index)
and return a BuildChange describing how the best arrangement moved versus the
build's BASELINE — the state the user last acknowledged, which only advances on
review (see app.core.snapshot_baseline).  That is what powers the "your build
may have improved" notification after a newer save is uploaded, and what keeps
it alive across a Relic Rites spree instead of overwriting it: `causes` names
every input that moved (relics / staged / build_edit / game_data), and staged
purchases are narrated rather than suppressed, flagged per relic so the user
knows they still owe them to the save file.  Inline (anonymous) runs persist
nothing and return change=null.
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
from app.core.game_data import game_data_version, get_items_json
from app.core.snapshot_baseline import (
    baseline_layouts,
    causes_since,
    is_narratable,
    legacy_cause,
    make_baseline,
    snapshot_inputs,
)
from app.core.staged import apply_staged_diff, staged_diff_signature
from app.models import (
    Build,
    OptimizationSnapshot,
    Profile,
    Relic,
    StagedMint,
    get_datetime_utc,
)
from nrplanner.changes import (
    build_signature,
    diff_results,
    fingerprint_owned,
    layout_match_key,
    mark_staged_refs,
    relic_fingerprint,
    relevant_relics_signature,
    relics_signature,
    serialize_match_keys,
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

    # --- Staged in-app edits (DB mode only; see app.core.staged) ---
    # The optimizer runs on the EFFECTIVE inventory: profile relics minus
    # staged_sells (ga_handles) plus staged_mints (validated, negative
    # synthetic handles).  Inline mode applies its diff client-side instead.
    staged_sells: list[int] = Field(default_factory=list)
    staged_mints: list[StagedMint] = Field(default_factory=list)

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

    # --- Staged in-app edits (DB mode only; same semantics as OptimizeRequest) ---
    staged_sells: list[int] = Field(default_factory=list)
    staged_mints: list[StagedMint] = Field(default_factory=list)

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
    # Hash of the SAVE's own inventory, staged diff excluded.  Cause attribution
    # needs it to tell "your save changed" from "you bought relics in the app":
    # a staged mint moves the effective inventory hash but not this one.
    base_relics_hash: str


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
    ds: Any,
) -> tuple[BuildDefinition, list[OwnedRelic], _SnapshotCtx | None]:
    """Resolve a request into (build_def, owned_relics, snapshot_ctx).

    DB mode returns the EFFECTIVE inventory (profile relics with the staged
    diff applied).  snapshot_ctx is None for inline mode (nothing persisted).
    Raises HTTPException on the same auth/validation conditions as before.
    """
    using_db = req.build_id is not None or req.profile_id is not None
    using_inline = req.build is not None or req.relics is not None
    using_staged = bool(req.staged_sells or req.staged_mints)

    if using_db and using_inline:
        raise HTTPException(
            status_code=422,
            detail="Provide either (build_id + profile_id) or (build + relics), not both.",
        )
    if using_staged and not using_db:
        raise HTTPException(
            status_code=422,
            detail="staged_sells/staged_mints require DB mode; apply the diff "
                   "to the inline relics list client-side instead.",
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
        base_relics = _owned_from_db(list(db_relics))
        owned_relics = apply_staged_diff(
            base_relics,
            req.staged_sells, req.staged_mints, ds, get_items_json(),
        )
        if len(owned_relics) > settings.MAX_RELICS_PER_OPTIMIZE:
            raise HTTPException(
                status_code=422,
                detail=f"Too many relics (max {settings.MAX_RELICS_PER_OPTIMIZE}).",
            )
        ctx = _SnapshotCtx(
            owner_id=current_user.id,
            build_id=db_build.id,
            build_name=db_build.name,
            slot_index=profile.slot_index,
            base_relics_hash=relics_signature(base_relics),
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

def _apply_snapshot(
    session: Session,
    ctx: _SnapshotCtx,
    build_def: BuildDefinition,
    owned_relics: list[OwnedRelic],
    results: list[VesselResult],
    ds: Any,
    top_n: int,
    max_per_vessel: int,
    staged_sells: list[int],
    staged_mints: list[StagedMint],
) -> BuildChange:
    """Diff a fresh optimization against the build's BASELINE, then upsert it.

    The baseline is the state the user last acknowledged, not the previous run
    (see app.core.snapshot_baseline), so a change survives until it is read and
    several events compose into one verdict.  It advances here only when this
    run has no news in it — a build edit or a game-data bump re-baselines
    silently, exactly as before; anything the user should see is left standing
    (and marked unreviewed) until they review it.

    Returns the BuildChange (build identity + causes filled).  Commits the session.
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
    staged_signature = staged_diff_signature(staged_sells, staged_mints)
    relevant_hash = relevant_relics_signature(
        build_def, [(fingerprint_owned(r), r.ga_handle) for r in owned_relics], ds
    )
    inputs = snapshot_inputs(
        base_relics_hash=ctx.base_relics_hash,
        relics_hash=relics_hash,
        build_hash=build_hash,
        game_data_version=gdv,
        optimizer_version=str(OPTIMIZER_VERSION),
        staged_signature=staged_signature,
    )

    baseline = snap.baseline if snap else None
    change = diff_results(
        baseline_layouts(baseline), results, owned=owned_relics
    )
    change.build_id = str(ctx.build_id)
    change.build_name = ctx.build_name
    change.slot_index = ctx.slot_index
    change.causes = causes_since(baseline, inputs)
    change.cause = legacy_cause(change.causes)
    # Name the relics that are purchases-in-waiting rather than save contents.
    mark_staged_refs(
        change,
        [relic_fingerprint(m.real_id, m.effects, m.curses) for m in staged_mints],
    )

    top_layouts = serialize_top_layouts(results)
    # Result identities in DISPLAY order -- lets the builds page recognise an
    # in-game loadout as "result #N" without loading full_results.
    top_match_keys = serialize_match_keys(results)
    # cumulative_effects is a serve-time presentation field (recomputed on every
    # response, incl. POST /snapshot/query) — never persist it in the snapshot.
    full_results = [r.model_dump(mode="json", exclude={"cumulative_effects"}) for r in results]
    best_score = max((r.total_score for r in results), default=0)
    any_truncated = any(r.search_truncated for r in results)
    change_json = change.model_dump(mode="json")

    # Nothing the user needs to hear (their own build edit, a game-data bump, or
    # no movement at all) — fold this run into the baseline so the NEXT change is
    # measured from here.  News is left un-baselined and unreviewed.
    news = is_narratable(change.causes)
    fresh_baseline = make_baseline(
        layouts=top_layouts, best_score=best_score, inputs=inputs
    )

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
            staged_signature=staged_signature,
            top_n=top_n,
            max_per_vessel=max_per_vessel,
            top_layouts=top_layouts,
            top_match_keys=top_match_keys,
            full_results=full_results,
            best_score=best_score,
            any_truncated=any_truncated,
            last_change=change_json,
            reviewed=True,
            baseline=fresh_baseline,
        )
    else:
        snap.relics_hash = relics_hash
        snap.build_hash = build_hash
        snap.game_data_version = gdv
        snap.optimizer_version = OPTIMIZER_VERSION
        snap.relevant_relics_hash = relevant_hash
        snap.staged_signature = staged_signature
        snap.top_n = top_n
        snap.max_per_vessel = max_per_vessel
        snap.top_layouts = top_layouts
        snap.top_match_keys = top_match_keys
        snap.full_results = full_results
        snap.best_score = best_score
        snap.any_truncated = any_truncated
        snap.last_change = change_json
        if news:
            # Unread news stays unread; a build the user already reviewed goes
            # back to unreviewed so the change list picks it up.
            snap.reviewed = False
        else:
            snap.baseline = fresh_baseline
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
    build_def, owned_relics, ctx = _resolve(req, current_user, session, ds)
    results = _run_optimizer(
        build_def, owned_relics, build_def.character,
        req.top_n, req.max_per_vessel, ds, executor=executor,
    )
    if ctx is not None:
        try:
            _apply_snapshot(session, ctx, build_def, owned_relics, results, ds,
                            req.top_n, req.max_per_vessel,
                            req.staged_sells, req.staged_mints)
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
    build_def, owned_relics, ctx = _resolve(req, current_user, session, ds)

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
                                    req.staged_sells, req.staged_mints,
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
        staged_sells=req.staged_sells,
        staged_mints=req.staged_mints,
        build=req.build,
        relics=req.relics,
    )
    build_def, owned_relics, _ctx = _resolve(base, current_user, session, ds)

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


class SnapshotQuery(BaseModel):
    """Freshness query for a build+profile's cached optimization.

    ``staged_sells``/``staged_mints`` describe the client's staged in-app diff
    (same semantics as OptimizeRequest): freshness is then evaluated against
    the EFFECTIVE inventory, so a snapshot written by a staged run is served
    back to the same staged state — and, because hashes are content-based, to
    the pure state that results from actually exporting + re-uploading it.
    """
    build_id: uuid.UUID
    profile_id: uuid.UUID
    staged_sells: list[int] = Field(default_factory=list)
    staged_mints: list[StagedMint] = Field(default_factory=list)


@router.post("/snapshot/query", response_model=SnapshotResponse | None)
def query_snapshot(
    req: SnapshotQuery,
    current_user: CurrentUser,
    session: SessionDep,
    ds: GameDataDep,
) -> SnapshotResponse | None:
    """Return cached optimization results if the snapshot is fresh.

    A snapshot is fresh when its relics_hash and build_hash match the current
    live inputs (cached on the Profile and Build rows at write time), with the
    staged diff applied to the inventory side first.  Returns null if stale or
    missing — the frontend should then trigger a fresh optimization.
    """
    db_build = session.get(Build, req.build_id)
    if not db_build or db_build.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Build not found")

    profile = session.get(Profile, req.profile_id)
    if not profile or profile.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Profile not found")

    snap = session.exec(
        select(OptimizationSnapshot).where(
            OptimizationSnapshot.build_id == req.build_id,
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

    staged = bool(req.staged_sells or req.staged_mints)
    if staged or snap.relics_hash != profile.relics_hash:
        # Fast path only when clean AND the whole-inventory hash matches; any
        # staged diff forces the effective-inventory compare (Profile.relics_hash
        # is frozen at upload and can never reflect staged edits).
        db_relics = session.exec(
            select(Relic).where(Relic.profile_id == profile.id)
        ).all()
        effective = apply_staged_diff(
            _owned_from_db(list(db_relics)),
            req.staged_sells, req.staged_mints, ds, get_items_json(),
        )
        if snap.relics_hash != relics_signature(effective):
            # Whole-inventory hash moved, but the optimum depends only on the
            # build-RELEVANT subset (see relevant_relics_signature).  Serve the
            # snapshot when that subset is unchanged; legacy rows without the
            # stored hash stay stale (one re-optimize refills them).
            if snap.relevant_relics_hash is None:
                return None
            pairs = [(fingerprint_owned(r), r.ga_handle) for r in effective]
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

    This is also the ONLY place a change with news in it advances the baseline:
    reviewing is the user saying "I've seen where this build stands now", so the
    next change is measured from here.  Until then every re-run keeps comparing
    against the last reviewed state, which is what makes an upload plus a Relic
    Rites spree read as one verdict instead of two lost ones.

    Applies to every snapshot the user owns for this build (a build may have one
    per profile slot).  Idempotent.
    """
    snaps = session.exec(
        select(OptimizationSnapshot).where(
            OptimizationSnapshot.build_id == build_id,
            OptimizationSnapshot.owner_id == current_user.id,
        )
    ).all()
    for snap in snaps:
        snap.reviewed = True
        # Advance the baseline to the state just reviewed.  The inputs are read
        # back off the snapshot's own provenance columns; base_relics_hash is
        # the one value not stored there, so it is carried over from the run
        # that produced this change (the snapshot's current baseline knows it
        # only if the run was pure-save — a staged run keeps the save's hash
        # from the previous baseline, which is exactly what we want: the save
        # itself did not change).
        prev_inputs = (snap.baseline or {}).get("inputs") or {}
        base_hash = (
            snap.relics_hash
            if snap.staged_signature is None
            else prev_inputs.get("base_relics_hash", snap.relics_hash)
        )
        snap.baseline = make_baseline(
            layouts=snap.top_layouts or [],
            best_score=snap.best_score,
            inputs=snapshot_inputs(
                base_relics_hash=base_hash,
                relics_hash=snap.relics_hash,
                build_hash=snap.build_hash,
                game_data_version=snap.game_data_version,
                optimizer_version=str(snap.optimizer_version),
                staged_signature=snap.staged_signature,
            ),
        )
        session.add(snap)
    session.commit()


# ---------------------------------------------------------------------------
# "Your in-game loadout is result #N" (builds page badge)
# ---------------------------------------------------------------------------

class LoadoutRef(BaseModel):
    """One in-game loadout preset to look for among a build's results.

    Sent by the client rather than read from the profile so the LIVE preset
    list is what gets matched — a setup saved from the optimizer but not yet
    exported is staged client-side, and a badge that ignored it would tell the
    user their fresh save isn't there.  Omit the list entirely to match against
    the presets exactly as they sit in the uploaded save.
    """
    index: int
    character: str
    name: str = ""
    vessel_id: int
    ga_handles: list[int] = Field(default_factory=list)


class LoadoutRankRequest(BaseModel):
    profile_id: uuid.UUID
    # Same staged in-app diff the optimizer runs on (see app.core.staged) —
    # needed to resolve a preset that holds a staged Relic Rites purchase.
    staged_sells: list[int] = Field(default_factory=list)
    staged_mints: list[StagedMint] = Field(default_factory=list)
    loadouts: list[LoadoutRef] | None = None


class LoadoutRank(BaseModel):
    """A build whose in-game loadout reproduces one of its optimizer results."""
    build_id: str
    # 1-based position among the build's cached results, in the order the
    # optimize page lists them: 1 = the top suggestion.
    rank: int
    # How many results that snapshot holds (the "of 10" in "#3 of 10").
    total: int
    loadout_index: int
    loadout_name: str


@router.post("/loadout-ranks", response_model=list[LoadoutRank])
def list_loadout_ranks(
    req: LoadoutRankRequest,
    current_user: CurrentUser,
    session: SessionDep,
    ds: GameDataDep,
) -> list[LoadoutRank]:
    """Tell each build which of its optimizer results is already saved in-game.

    Answers, per build, "is what I actually have equipped still the optimizer's
    pick?" — rank 1 means the saved loadout IS the top suggestion, rank 3 means
    the optimizer has since found two better arrangements.

    Identity is content-based (vessel + relic multiset), the same relation the
    optimize page's "Saved" badge uses, so a preset holding the same relics in
    swapped same-colour slots or a different physical copy of a duplicate still
    counts.  Builds whose loadouts match nothing are simply omitted: silence is
    the honest answer for "never saved this build" and "fell out of the top N"
    alike, and neither deserves a badge.

    Reads only ``top_match_keys`` from each snapshot — never the heavy
    full_results blob — so this stays cheap enough for a list page.
    """
    profile = session.get(Profile, req.profile_id)
    if not profile or profile.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Profile not found")

    db_relics = session.exec(
        select(Relic).where(Relic.profile_id == req.profile_id)
    ).all()
    owned = apply_staged_diff(
        _owned_from_db(list(db_relics)),
        req.staged_sells, req.staged_mints, ds, get_items_json(),
    )
    fp_by_handle = {r.ga_handle: fingerprint_owned(r) for r in owned}

    if req.loadouts is not None:
        loadouts = req.loadouts
    else:
        loadouts = [
            LoadoutRef.model_validate(raw) for raw in (profile.loadouts or [])
        ]

    # character -> [(match key, preset index, preset name)].  A preset holding a
    # relic that is no longer in the effective inventory can't be identified at
    # all, so it matches nothing rather than matching as if the slot were empty.
    by_character: dict[str, list[tuple[str, int, str]]] = {}
    for lo in loadouts:
        handles = [h for h in lo.ga_handles if h != 0]
        fps = [fp_by_handle.get(h) for h in handles]
        if any(fp is None for fp in fps):
            continue
        key = layout_match_key(lo.vessel_id, [fp for fp in fps if fp is not None])
        by_character.setdefault(lo.character, []).append((key, lo.index, lo.name))
    if not by_character:
        return []

    builds = session.exec(
        select(Build.id, Build.character).where(Build.owner_id == current_user.id)
    ).all()
    snaps = session.exec(
        select(
            OptimizationSnapshot.build_id, OptimizationSnapshot.top_match_keys
        ).where(
            OptimizationSnapshot.owner_id == current_user.id,
            OptimizationSnapshot.slot_index == profile.slot_index,
        )
    ).all()
    keys_by_build = {str(build_id): keys or [] for build_id, keys in snaps}

    out: list[LoadoutRank] = []
    for build_id, character in builds:
        keys = keys_by_build.get(str(build_id))
        if not keys:
            continue
        candidates = by_character.get(character)
        if not candidates:
            continue
        best: tuple[int, int, str] | None = None
        for key, lo_index, lo_name in candidates:
            if key not in keys:
                continue
            rank = keys.index(key) + 1
            if best is None or rank < best[0]:
                best = (rank, lo_index, lo_name)
        if best is None:
            continue
        out.append(LoadoutRank(
            build_id=str(build_id),
            rank=best[0],
            total=len(keys),
            loadout_index=best[1],
            loadout_name=best[2],
        ))
    return out
