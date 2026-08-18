"""Save file upload, profile discovery, and relic inventory endpoints."""
import hashlib
import json
import queue
import tempfile
import threading
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from sqlmodel import Session, col, select

from app.api.deps import (
    CurrentUser,
    GameDataDep,
    OptimizerPoolDep,
    OptionalUser,
    SessionDep,
)
from app.core.build_def import build_def_from_db
from app.core.config import settings
from app.core.db import engine
from app.core.game_data import (
    game_data_version,
    get_items_json,
    get_relic_generator,
    get_relic_lots,
)
from app.models import (
    AddRelicSpec,
    Build,
    LoadoutOp,
    LoadoutsPublic,
    OptimizationSnapshot,
    RitesKeeper,
    RitesPlanResponse,
    ProfilePublic,
    ProfilesPublic,
    Profile,
    ParsedLoadoutData,
    ParsedProfileData,
    ParsedRelicData,
    Relic,
    RelicDelta,
    SaveComparison,
    RelicPublic,
    RelicsPublic,
    SaveUpload,
    SaveStatusPublic,
    StagedMint,
    UploadResponse,
)
from app.core.snapshot_baseline import (
    baseline_layouts,
    causes_since,
    legacy_cause,
    make_baseline,
    snapshot_inputs,
)
from app.core.staged import effective_slot_state
from nrplanner import (
    AddCapacityError,
    InvalidReason,
    LoadoutHandler,
    RelicChecker,
    RelicInventory,
    VesselWriteError,
    add_capacity,
    add_preset,
    add_relics,
    adjust_murks,
    build_relic_record,
    delete_preset,
    discover_characters,
    decrypt_sl2,
    delete_relics,
    overwrite_preset,
    parse_relics,
    read_acquisition_ids,
    read_favorite_handles,
    read_murks,
    read_owner_steam_id,
    rename_preset,
    repack_sl2,
    reset_all_presets,
    reset_all_vessels,
    sell_value,
    set_favorites,
    split_memory_dat,
)
from nrplanner.constants import CHARACTER_NAMES, EMPTY_EFFECT, RELIC_STORAGE_CAP
from nrplanner.cumulative import summarize_cumulative_effects
from nrplanner.changes import (
    build_signature,
    diff_results,
    fingerprint_owned,
    multiset_diff,
    relevant_relics_signature,
    relevant_to_build,
    relic_fingerprint,
    relics_signature,
    relics_signature_from_fingerprints,
    serialize_match_keys,
    serialize_top_layouts,
)
from nrplanner.models import (
    BuildChange,
    BuildDefinition,
    OwnedRelic,
    RelicRef,
    VesselResult,
    WeightGroup,
)
from nrplanner.optimizer import OPTIMIZER_VERSION, VesselOptimizer
from nrplanner.rites import (
    BuildContext,
    PurchaseBucket,
    bulk_acquire,
)
from nrplanner.scoring import BuildScorer

router = APIRouter(prefix="/saves", tags=["saves"])


def _compute_handle_remap(
    old_relics: list[Relic],
    new_profiles: list[ParsedProfileData],
) -> dict[int, int]:
    """Return a mapping {old_ga_handle: new_ga_handle} based on relic content.

    Relics are matched by fingerprint (real_id + effects + curses).  When
    multiple relics share the same fingerprint they are paired in encounter
    order so each old handle maps to a distinct new handle.  Old handles whose
    relic is absent from the new save are simply omitted — callers should drop
    any pinned references to missing handles.
    """
    old_fp: dict[tuple, list[int]] = defaultdict(list)
    for r in old_relics:
        old_fp[relic_fingerprint(
            r.real_id, (r.effect_1, r.effect_2, r.effect_3),
            (r.curse_1, r.curse_2, r.curse_3),
        )].append(r.ga_handle)

    new_fp: dict[tuple, list[int]] = defaultdict(list)
    for prof in new_profiles:
        for r in prof.relics:
            new_fp[relic_fingerprint(
                r.real_id, (r.effect_1, r.effect_2, r.effect_3),
                (r.curse_1, r.curse_2, r.curse_3),
            )].append(r.ga_handle)

    remap: dict[int, int] = {}
    for fp, old_handles in old_fp.items():
        new_handles = new_fp.get(fp, [])
        for old_h, new_h in zip(old_handles, new_handles):
            remap[old_h] = new_h

    return remap


def _db_relic_fingerprint(r: Relic) -> tuple:
    return relic_fingerprint(
        r.real_id, (r.effect_1, r.effect_2, r.effect_3),
        (r.curse_1, r.curse_2, r.curse_3),
    )


def _parsed_relic_fingerprint(r: ParsedRelicData) -> tuple:
    return relic_fingerprint(
        r.real_id, (r.effect_1, r.effect_2, r.effect_3),
        (r.curse_1, r.curse_2, r.curse_3),
    )


def _relic_ref_from_db(r: Relic) -> RelicRef:
    return RelicRef(
        real_id=r.real_id, name=r.name, color=r.color,
        effects=[r.effect_1, r.effect_2, r.effect_3],
        curses=[r.curse_1, r.curse_2, r.curse_3],
    )


def _build_def_for_relevance(build: Build) -> BuildDefinition:
    """Minimal BuildDefinition carrying only the fields relevance scanning reads."""
    return BuildDefinition(
        id=str(build.id),
        name=build.name,
        character=build.character,
        groups=[WeightGroup(**g) for g in (build.groups or [])],
        required_effects=build.required_effects or [],
        required_families=build.required_families or [],
    )


def _same_account(old_owner_id: str | None, new_owner_id: str | None) -> bool:
    """Whether two uploads can be treated as the SAME game account.

    The save-to-save comparison ("changes since last save") is only meaningful
    within one account — comparing slot N of account A against slot N of account
    B is nonsense. We only suppress the comparison when we can PROVE the accounts
    differ (both owner IDs known and unequal). If either ID is unknown (PS4
    saves, pre-column rows, or an unreadable anchor) we fall back to comparing,
    so an unknown owner never hides a legitimate change.
    """
    if old_owner_id is None or new_owner_id is None:
        return True
    return old_owner_id == new_owner_id


def _account_reason(old_owner_id: str | None, new_owner_id: str | None) -> str:
    """Why the account check reached its verdict — for the upload summary.

    Suppression used to be silent, which reads exactly like "nothing changed":
    a player testing a friend's save, or on PS4 where no owner anchor is
    readable, had no way to tell whether the comparison ran at all.
    """
    if old_owner_id is not None and new_owner_id is not None:
        return "ok" if old_owner_id == new_owner_id else "different_account"
    # A save whose owner we cannot read is compared anyway (never hide a real
    # change) but the summary says the identity is unproven.
    return "unverified_owner"


# A slot whose relics have NOTHING in common with the previous save is a
# different character, not a changed one: real play keeps almost the whole
# inventory between sessions, and even a heavy cull leaves overlap.  Below this
# many previous relics the signal is too weak to act on (a brand-new character
# legitimately has a handful, and losing them all is plausible).
_RESTART_MIN_OLD_RELICS = 10


def _restarted_slots(
    old_by_slot: dict[int, list], new_by_slot: dict[int, list]
) -> set[int]:
    """Slots holding a DIFFERENT character than the previous save.

    The comparison is keyed by slot_index, which is the save's stable character
    slot — but a player who deletes a character and starts another reuses that
    slot, and diffing the two would report the whole old inventory as lost.
    Detected by content overlap rather than by name: the profile name is
    account-wide and renameable, so it cannot identify a character.
    """
    restarted: set[int] = set()
    for slot, old_fps in old_by_slot.items():
        if len(old_fps) < _RESTART_MIN_OLD_RELICS:
            continue
        new_fps = new_by_slot.get(slot)
        if not new_fps:
            continue  # slot now empty/absent — handled as a normal diff
        if not (Counter(old_fps) & Counter(new_fps)):
            restarted.add(slot)
    return restarted


def _compute_relic_delta(
    old_relics: list[Relic],
    new_profiles: list[ParsedProfileData],
    old_slot_of: dict[uuid.UUID, int] | None = None,
    skip_slots: set[int] | None = None,
) -> RelicDelta:
    """Relics gained/lost versus the previous save, across the whole account.

    ``skip_slots`` drops character slots that hold a different character than
    last time (see ``_restarted_slots``); counting those would report a fresh
    character's empty shelf as hundreds of relics lost.
    """
    skip = skip_slots or set()
    slot_of = old_slot_of or {}
    old_fps = [
        _db_relic_fingerprint(r)
        for r in old_relics
        if slot_of.get(r.profile_id) not in skip
    ]
    new_fps = [
        _parsed_relic_fingerprint(r)
        for prof in new_profiles
        if prof.slot_index not in skip
        for r in prof.relics
    ]
    added, removed = multiset_diff(old_fps, new_fps)
    return RelicDelta(added=len(added), removed=len(removed))


@dataclass
class _AffectedBuild:
    """A build whose optimal arrangement may have changed due to relic diff."""
    build: Build
    slot_index: int
    broken_pins: list[RelicRef] = field(default_factory=list)


def _slot_fingerprints(
    old_relics: list[Relic],
    old_profiles: list[Profile],
    new_profiles: list[ParsedProfileData],
) -> tuple[dict[uuid.UUID, int], dict[int, list], dict[int, list]]:
    """(profile_id → slot, old relic fingerprints by slot, new ones by slot)."""
    old_slot_of = {p.id: p.slot_index for p in old_profiles}

    old_by_slot: dict[int, list] = defaultdict(list)
    for r in old_relics:
        slot = old_slot_of.get(r.profile_id)
        if slot is not None:
            old_by_slot[slot].append(_db_relic_fingerprint(r))

    new_by_slot: dict[int, list] = defaultdict(list)
    for prof in new_profiles:
        for r in prof.relics:
            new_by_slot[prof.slot_index].append(_parsed_relic_fingerprint(r))

    return old_slot_of, old_by_slot, new_by_slot


def _identify_affected_builds(
    session: Any,
    ds: Any,
    owner_id: uuid.UUID,
    old_relics: list[Relic],
    old_profiles: list[Profile],
    new_profiles: list[ParsedProfileData],
    db_builds: list[Build],
    handle_remap: dict[int, int],
    skip_slots: set[int] | None = None,
) -> list[_AffectedBuild]:
    """Identify builds whose stored arrangement may have changed (read-only).

    Returns the affected (build, slot_index, broken_pins) without mutating any
    snapshot state.  Callers decide whether to flag or re-optimize.

    ``skip_slots`` are slots holding a different character than last time; their
    builds are left alone entirely rather than diffed against a stranger.
    """
    skip = skip_slots or set()
    old_slot_of, old_by_slot, new_by_slot = _slot_fingerprints(
        old_relics, old_profiles, new_profiles
    )

    old_by_handle = {r.ga_handle: r for r in old_relics}
    builds_by_id = {b.id: b for b in db_builds}

    snaps = session.exec(
        select(OptimizationSnapshot).where(OptimizationSnapshot.owner_id == owner_id)
    ).all()

    affected: list[_AffectedBuild] = []
    for snap in snaps:
        build = builds_by_id.get(snap.build_id)
        if build is None:
            continue
        if snap.slot_index in skip:
            continue

        added, removed = multiset_diff(
            old_by_slot.get(snap.slot_index, []),
            new_by_slot.get(snap.slot_index, []),
        )
        build_def = _build_def_for_relevance(build)
        relevant_added, relevant_removed = relevant_to_build(build_def, added, removed, ds)

        broken: list[RelicRef] = []
        for handle in build.pinned_relics or []:
            if handle in handle_remap:
                continue
            old_relic = old_by_handle.get(handle)
            if old_relic is None or old_slot_of.get(old_relic.profile_id) != snap.slot_index:
                continue
            broken.append(_relic_ref_from_db(old_relic))

        if relevant_added == 0 and relevant_removed == 0 and not broken:
            continue

        affected.append(_AffectedBuild(
            build=build, slot_index=snap.slot_index, broken_pins=broken,
        ))

    return affected


def _flag_affected_snapshots(
    session: Any,
    ds: Any,
    owner_id: uuid.UUID,
    old_relics: list[Relic],
    old_profiles: list[Profile],
    new_profiles: list[ParsedProfileData],
    db_builds: list[Build],
    handle_remap: dict[int, int],
    skip_slots: set[int] | None = None,
) -> list[BuildChange]:
    """Cheaply flag builds whose stored arrangement may have changed.

    Thin wrapper around ``_identify_affected_builds`` that also marks snapshots
    unreviewed.  Used by the non-streaming upload endpoint.
    """
    affected = _identify_affected_builds(
        session, ds, owner_id, old_relics, old_profiles, new_profiles,
        db_builds, handle_remap, skip_slots,
    )
    changes: list[BuildChange] = []
    for ab in affected:
        snap = session.exec(
            select(OptimizationSnapshot).where(
                OptimizationSnapshot.build_id == ab.build.id,
                OptimizationSnapshot.slot_index == ab.slot_index,
            )
        ).first()
        if snap is None:
            continue
        change = BuildChange(
            build_id=str(ab.build.id),
            build_name=ab.build.name,
            slot_index=ab.slot_index,
            status="broken_pin" if ab.broken_pins else "potentially_affected",
            relevant_added=0,
            pinned_removed=ab.broken_pins,
            causes=["relics"],
            cause="relics",
            reliable=False,
        )
        snap.last_change = change.model_dump(mode="json")
        snap.reviewed = False
        session.add(snap)
        changes.append(change)
    return changes


_ALLOWED_EXTENSIONS = {".sl2", ".dat"}


def _detect_platform(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".sl2":
        return "PC"
    if suffix == ".dat":
        return "PS4"
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file type '{suffix}'. Upload a .sl2 (PC) or memory.dat (PS4) file.",
    )


def _character_for_hero_type(hero_type: int) -> str:
    """Map a 1-based save hero_type (Wylder=1..Undertaker=10) to its name."""
    if 1 <= hero_type <= len(CHARACTER_NAMES):
        return CHARACTER_NAMES[hero_type - 1]
    return f"Hero {hero_type}"


def _parsed_loadouts(
    loadout: LoadoutHandler, ds: Any, inventory: Any
) -> list[ParsedLoadoutData]:
    """Build display-enriched loadout presets from a parsed LoadoutHandler.

    Each preset is enriched with the cumulative stacked-effect summary (same
    computation the optimizer uses) so the Loadouts page can show what bonuses a
    saved loadout actually grants.
    """
    by_handle = {r.ga_handle: r for r in inventory.relics}
    out: list[ParsedLoadoutData] = []
    for p in loadout.all_presets:
        vessel = ds.get_vessel_data(p["vessel_id"]) or {}
        effect_ids: list[int] = []
        for h in p["relics"]:
            r = by_handle.get(h)
            if r is not None:
                effect_ids.extend(r.all_effects)
        out.append(ParsedLoadoutData(
            index=p["index"],
            hero_type=p["hero_type"],
            character=_character_for_hero_type(p["hero_type"]),
            name=p["name"],
            vessel_id=p["vessel_id"],
            vessel_name=vessel.get("Name", f"Vessel {p['vessel_id']}"),
            slot_colors=list(vessel.get("Colors", [])),
            ga_handles=list(p["relics"]),
            cumulative_effects=summarize_cumulative_effects(effect_ids, ds),
        ))
    return out


def _parse_save_to_profiles(
    file_bytes: bytes,
    filename: str,
    ds: Any,
    items_json: dict,
) -> tuple[str, str | None, list[ParsedProfileData]]:
    """Decrypt/split save, parse all slots.

    Returns (platform, owner_steam_id, profiles). owner_steam_id is the
    SteamID64 (decimal string) of the account that owns the save, or None when
    it can't be read (PS4 saves, unreadable anchor).
    """
    platform = _detect_platform(filename)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        save_path = tmp_path / filename
        save_path.write_bytes(file_bytes)

        decrypt_dir = tmp_path / "decrypted"
        decrypt_dir.mkdir()

        try:
            if platform == "PC":
                decrypt_sl2(save_path, decrypt_dir)
            else:
                split_memory_dat(save_path, decrypt_dir)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Failed to decrypt save file: {exc}",
            ) from exc

        try:
            char_paths = discover_characters(decrypt_dir, mode=platform)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Failed to discover characters: {exc}",
            ) from exc

        if not char_paths:
            raise HTTPException(
                status_code=422,
                detail="No characters found in save file.",
            )

        owner_steam_id = read_owner_steam_id(decrypt_dir, mode=platform)

        profiles: list[ParsedProfileData] = []
        for char_name, userdata_path in char_paths:
            if not char_name:
                continue
            data = userdata_path.read_bytes()
            raw_relics, items_end = parse_relics(data)
            inventory = RelicInventory(raw_relics, items_json, ds)

            # Sell-protection: relics equipped in a loadout/preset or bookmarked
            # in-game cannot be sold.
            favorite_handles = read_favorite_handles(data, items_end)
            acquisition_ids = read_acquisition_ids(data, items_end)
            murks = read_murks(data, items_end)
            loadout = LoadoutHandler(ds)
            loadout.parse(data)
            equipped_handles = set(loadout.relic_ga_hero_map.keys())

            relics_data = [
                ParsedRelicData(
                    ga_handle=r.ga_handle,
                    item_id=r.item_id,
                    real_id=r.real_id,
                    color=r.color,
                    effect_1=r.effects[0],
                    effect_2=r.effects[1],
                    effect_3=r.effects[2],
                    curse_1=r.curses[0],
                    curse_2=r.curses[1],
                    curse_3=r.curses[2],
                    is_deep=r.is_deep,
                    name=r.name,
                    tier=r.tier,
                    is_favorite=r.ga_handle in favorite_handles,
                    equipped=r.ga_handle in equipped_handles,
                    acquisition_id=acquisition_ids.get(r.ga_handle),
                )
                for r in inventory.relics
            ]

            # Extract slot index from filename (USERDATA_00 → 0)
            slot_index = int(userdata_path.stem.rsplit("_", 1)[-1])

            presets = _parsed_loadouts(loadout, ds, inventory)

            profiles.append(
                ParsedProfileData(
                    slot_index=slot_index,
                    name=char_name,
                    relic_count=len(relics_data),
                    relics=relics_data,
                    murks=murks,
                    presets=presets,
                    presets_used=len(presets),
                )
            )

    return platform, owner_steam_id, profiles


@router.post("/upload", response_model=UploadResponse)
async def upload_save(
    file: UploadFile,
    ds: GameDataDep,
    current_user: OptionalUser,
    session: SessionDep,
) -> UploadResponse:
    """Upload a .sl2 (PC) or memory.dat (PS4) save file.

    - **Anonymous users**: returns parsed profiles + relics (nothing persisted).
    - **Authenticated users**: persists to DB, replacing any previous upload.
    """
    if file.filename is None or Path(file.filename).suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Upload a .sl2 (PC) or memory.dat (PS4) file.",
        )

    file_bytes = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {settings.MAX_UPLOAD_SIZE_MB} MB).",
        )
    items_json = get_items_json()
    # CPU-bound (AES decrypt + binary parse) — keep it off the event loop.
    platform, owner_steam_id, profiles = await run_in_threadpool(
        _parse_save_to_profiles, file_bytes, file.filename, ds, items_json
    )

    if current_user is None:
        # Anonymous — return data only, nothing persisted
        return UploadResponse(
            platform=platform,
            profile_count=len(profiles),
            profiles=profiles,
            persisted=False,
        )

    # The save-to-save comparison only makes sense within one game account.
    # Detect whether this upload belongs to the same account as the previous one
    # (there is at most one SaveUpload per user); read the old owner id before it
    # is deleted below. When we can prove the account differs, the comparison is
    # suppressed rather than run across unrelated profiles.
    old_owner_id = session.exec(
        select(SaveUpload.save_owner_id).where(SaveUpload.owner_id == current_user.id)
    ).first()
    same_account = _same_account(old_owner_id, owner_steam_id)

    # Remap pinned relic handles in the user's builds before old data is deleted.
    # ga_handle values are assigned by the game engine and can change between saves
    # (e.g. when relics are acquired or the inventory is reorganised).  We match
    # relics by content fingerprint so pinned relics survive re-uploads.
    # Pins for relics no longer present in the new save are silently dropped.
    affected_builds: list[BuildChange] = []
    relic_delta = RelicDelta()
    old_relics = session.exec(
        select(Relic).where(Relic.owner_id == current_user.id)
    ).all()
    handle_remap = _compute_handle_remap(list(old_relics), profiles)
    comparison = (
        SaveComparison(
            compared=same_account,
            reason=_account_reason(old_owner_id, owner_steam_id),
        )
        if old_relics
        else SaveComparison(compared=False, reason="no_previous_save")
    )
    if old_relics:
        db_builds = session.exec(
            select(Build).where(Build.owner_id == current_user.id)
        ).all()
        for build in db_builds:
            if not build.pinned_relics:
                continue
            new_pinned = [handle_remap[h] for h in build.pinned_relics if h in handle_remap]
            if new_pinned != build.pinned_relics:
                build.pinned_relics = new_pinned
                session.add(build)
        session.flush()

        # Cheap save-diff: flag builds whose stored arrangement may have changed
        # (snapshots survive the re-upload — they key on slot_index, not profile).
        # Only meaningful within one account: a different account's slot N is a
        # different character, so skip the diff entirely when accounts differ.
        if same_account:
            old_profiles = session.exec(
                select(Profile).where(Profile.owner_id == current_user.id)
            ).all()
            old_slot_of, old_by_slot, new_by_slot = _slot_fingerprints(
                list(old_relics), list(old_profiles), profiles
            )
            restarted = _restarted_slots(old_by_slot, new_by_slot)
            comparison.restarted_slots = sorted(restarted)
            relic_delta = _compute_relic_delta(
                list(old_relics), profiles, old_slot_of, restarted
            )
            affected_builds = _flag_affected_snapshots(
                session, ds, current_user.id, list(old_relics), list(old_profiles),
                profiles, list(db_builds), handle_remap, restarted,
            )

    # Authenticated — delete old upload and persist fresh data
    old_uploads = session.exec(
        select(SaveUpload).where(SaveUpload.owner_id == current_user.id)
    ).all()
    for old in old_uploads:
        session.delete(old)
    session.flush()

    save_upload = SaveUpload(
        owner_id=current_user.id,
        platform=platform,
        profile_count=len(profiles),
        save_owner_id=owner_steam_id,
    )
    session.add(save_upload)
    session.flush()  # get the ID before creating children

    for prof_data in profiles:
        fps = [
            relic_fingerprint(
                r.real_id,
                [r.effect_1, r.effect_2, r.effect_3],
                [r.curse_1, r.curse_2, r.curse_3],
            )
            for r in prof_data.relics
        ]
        profile = Profile(
            owner_id=current_user.id,
            save_upload_id=save_upload.id,
            slot_index=prof_data.slot_index,
            name=prof_data.name,
            relics_hash=relics_signature_from_fingerprints(fps),
            murks=prof_data.murks,
            loadouts=[pl.model_dump() for pl in prof_data.presets],
        )
        session.add(profile)
        session.flush()

        for r in prof_data.relics:
            session.add(Relic(
                owner_id=current_user.id,
                profile_id=profile.id,
                ga_handle=r.ga_handle,
                item_id=r.item_id,
                real_id=r.real_id,
                color=r.color,
                effect_1=r.effect_1,
                effect_2=r.effect_2,
                effect_3=r.effect_3,
                curse_1=r.curse_1,
                curse_2=r.curse_2,
                curse_3=r.curse_3,
                is_deep=r.is_deep,
                name=r.name,
                tier=r.tier,
                is_favorite=r.is_favorite,
                equipped=r.equipped,
                acquisition_id=r.acquisition_id,
            ))

        # Attach DB id to response data
        prof_data.id = profile.id

    session.commit()

    return UploadResponse(
        platform=platform,
        profile_count=len(profiles),
        profiles=profiles,
        save_upload_id=save_upload.id,
        persisted=True,
        relic_delta=relic_delta,
        affected_builds=affected_builds,
        comparison=comparison,
    )


def _owned_from_parsed(relics: list[ParsedRelicData]) -> list[OwnedRelic]:
    """Convert parsed relic data into OwnedRelic for the optimizer."""
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


def _apply_snapshot_for_stream(
    session: Session,
    owner_id: uuid.UUID,
    build: Build,
    build_def: BuildDefinition,
    owned_relics: list[OwnedRelic],
    slot_index: int,
    results: list[VesselResult],
    ds: Any,
    top_n: int,
    max_per_vessel: int,
) -> BuildChange:
    """Diff a fresh post-upload optimization against the build's baseline.

    Mirrors optimize._apply_snapshot; the difference is that an upload run is
    pure-save by definition (the staged diff is discarded when the working file
    is replaced), so ``staged_signature`` is None on this side and any staged
    relics the user had are now either in the save — where the base hash sees
    them — or gone.

    The baseline never advances here: an upload always has news in it, and the
    change must survive until the user actually reads it.
    """
    from app.models import get_datetime_utc

    snap = session.exec(
        select(OptimizationSnapshot).where(
            OptimizationSnapshot.build_id == build.id,
            OptimizationSnapshot.slot_index == slot_index,
        )
    ).first()

    relics_hash = relics_signature(owned_relics)
    build_hash = build_signature(build_def)
    gdv = game_data_version()
    relevant_hash = relevant_relics_signature(
        build_def, [(fingerprint_owned(r), r.ga_handle) for r in owned_relics], ds
    )
    inputs = snapshot_inputs(
        # Pure-save run: the effective inventory IS the save's inventory.
        base_relics_hash=relics_hash,
        relics_hash=relics_hash,
        build_hash=build_hash,
        game_data_version=gdv,
        optimizer_version=str(OPTIMIZER_VERSION),
        staged_signature=None,
    )

    baseline = snap.baseline if snap else None
    change = diff_results(
        baseline_layouts(baseline), results, owned=owned_relics
    )
    change.build_id = str(build.id)
    change.build_name = build.name
    change.slot_index = slot_index
    change.causes = causes_since(baseline, inputs)
    change.cause = legacy_cause(change.causes)

    top_layouts = serialize_top_layouts(results)
    # See optimize.py: result identities in display order for the "saved as
    # loadout #N" badge on the builds page.
    top_match_keys = serialize_match_keys(results)
    full_results = [r.model_dump(mode="json") for r in results]
    best_score = max((r.total_score for r in results), default=0)
    any_truncated = any(r.search_truncated for r in results)
    change_json = change.model_dump(mode="json")

    if snap is None:
        snap = OptimizationSnapshot(
            owner_id=owner_id,
            build_id=build.id,
            slot_index=slot_index,
            relics_hash=relics_hash,
            build_hash=build_hash,
            game_data_version=gdv,
            optimizer_version=OPTIMIZER_VERSION,
            relevant_relics_hash=relevant_hash,
            top_n=top_n,
            max_per_vessel=max_per_vessel,
            top_layouts=top_layouts,
            top_match_keys=top_match_keys,
            full_results=full_results,
            best_score=best_score,
            any_truncated=any_truncated,
            last_change=change_json,
            # First-ever optimization of this build: there is no prior state to
            # compare against, so this run IS the baseline and there is nothing
            # to review.
            baseline=make_baseline(
                layouts=top_layouts, best_score=best_score, inputs=inputs
            ),
            reviewed=True,
        )
    else:
        snap.relics_hash = relics_hash
        snap.build_hash = build_hash
        snap.game_data_version = gdv
        snap.optimizer_version = OPTIMIZER_VERSION
        snap.relevant_relics_hash = relevant_hash
        # Upload runs are pure-inventory by definition — clear any staged
        # marker left by a discarded in-app diff.
        snap.staged_signature = None
        snap.top_n = top_n
        snap.max_per_vessel = max_per_vessel
        snap.top_layouts = top_layouts
        snap.top_match_keys = top_match_keys
        snap.full_results = full_results
        snap.best_score = best_score
        snap.any_truncated = any_truncated
        snap.last_change = change_json
        # Unread until viewed/dismissed — this is what surfaces the build in
        # the builds-page "Changes since your last save" list.  The baseline
        # deliberately stays where it was: reviewing is what advances it.
        snap.reviewed = False
        snap.updated_at = get_datetime_utc()
    session.add(snap)
    session.commit()
    return change


from nrplanner.constants import CHARACTER_NAMES

_CHAR_NAME_TO_HERO_TYPE: dict[str, int] = {
    name: idx for idx, name in enumerate(CHARACTER_NAMES, start=1)
}


@router.post("/upload/stream")
async def upload_save_stream(
    file: UploadFile,
    ds: GameDataDep,
    current_user: CurrentUser,
    session: SessionDep,
    executor: OptimizerPoolDep = None,
) -> StreamingResponse:
    """Upload a save and auto-optimize all affected builds, streaming progress.

    Returns SSE events:
      - upload_complete: save parsed and persisted
      - optimize_start: beginning optimization of a build
      - optimize_progress: per-vessel progress within a build
      - optimize_done: build optimization finished with BuildChange
      - complete: all builds processed
    """
    if file.filename is None or Path(file.filename).suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Upload a .sl2 (PC) or memory.dat (PS4) file.",
        )

    file_bytes = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {settings.MAX_UPLOAD_SIZE_MB} MB).",
        )
    items_json = get_items_json()
    # CPU-bound (AES decrypt + binary parse) — keep it off the event loop.
    platform, owner_steam_id, profiles = await run_in_threadpool(
        _parse_save_to_profiles, file_bytes, file.filename, ds, items_json
    )

    # Same-account check (see upload_save): only compare/re-optimize against the
    # previous upload when we can't prove it belongs to a different account.
    old_owner_id = session.exec(
        select(SaveUpload.save_owner_id).where(SaveUpload.owner_id == current_user.id)
    ).first()
    same_account = _same_account(old_owner_id, owner_steam_id)

    # Compute remap and identify affected builds before persisting
    old_relics = list(session.exec(
        select(Relic).where(Relic.owner_id == current_user.id)
    ).all())
    handle_remap = _compute_handle_remap(old_relics, profiles)
    affected: list[_AffectedBuild] = []
    relic_delta = RelicDelta()
    comparison = (
        SaveComparison(
            compared=same_account,
            reason=_account_reason(old_owner_id, owner_steam_id),
        )
        if old_relics
        else SaveComparison(compared=False, reason="no_previous_save")
    )

    if old_relics:
        db_builds = list(session.exec(
            select(Build).where(Build.owner_id == current_user.id)
        ).all())
        for build in db_builds:
            if not build.pinned_relics:
                continue
            new_pinned = [handle_remap[h] for h in build.pinned_relics if h in handle_remap]
            if new_pinned != build.pinned_relics:
                build.pinned_relics = new_pinned
                session.add(build)
        session.flush()

        # A different account's slot N is a different character — skip the diff
        # and the eager re-optimization when accounts differ.
        if same_account:
            old_profiles = list(session.exec(
                select(Profile).where(Profile.owner_id == current_user.id)
            ).all())
            old_slot_of, old_by_slot, new_by_slot = _slot_fingerprints(
                old_relics, old_profiles, profiles
            )
            restarted = _restarted_slots(old_by_slot, new_by_slot)
            comparison.restarted_slots = sorted(restarted)
            relic_delta = _compute_relic_delta(
                old_relics, profiles, old_slot_of, restarted
            )
            affected = _identify_affected_builds(
                session, ds, current_user.id, old_relics, old_profiles,
                profiles, db_builds, handle_remap, restarted,
            )

    # Persist fresh data (same as non-streaming upload)
    old_uploads = session.exec(
        select(SaveUpload).where(SaveUpload.owner_id == current_user.id)
    ).all()
    for old in old_uploads:
        session.delete(old)
    session.flush()

    save_upload = SaveUpload(
        owner_id=current_user.id,
        platform=platform,
        profile_count=len(profiles),
        save_owner_id=owner_steam_id,
    )
    session.add(save_upload)
    session.flush()

    # Map slot_index -> new Profile.id for snapshot lookup
    slot_to_profile_id: dict[int, uuid.UUID] = {}
    for prof_data in profiles:
        fps = [
            relic_fingerprint(
                r.real_id,
                [r.effect_1, r.effect_2, r.effect_3],
                [r.curse_1, r.curse_2, r.curse_3],
            )
            for r in prof_data.relics
        ]
        profile = Profile(
            owner_id=current_user.id,
            save_upload_id=save_upload.id,
            slot_index=prof_data.slot_index,
            name=prof_data.name,
            relics_hash=relics_signature_from_fingerprints(fps),
            murks=prof_data.murks,
            loadouts=[pl.model_dump() for pl in prof_data.presets],
        )
        session.add(profile)
        session.flush()
        slot_to_profile_id[prof_data.slot_index] = profile.id

        for r in prof_data.relics:
            session.add(Relic(
                owner_id=current_user.id,
                profile_id=profile.id,
                ga_handle=r.ga_handle,
                item_id=r.item_id,
                real_id=r.real_id,
                color=r.color,
                effect_1=r.effect_1,
                effect_2=r.effect_2,
                effect_3=r.effect_3,
                curse_1=r.curse_1,
                curse_2=r.curse_2,
                curse_3=r.curse_3,
                is_deep=r.is_deep,
                name=r.name,
                tier=r.tier,
                is_favorite=r.is_favorite,
                equipped=r.equipped,
                acquisition_id=r.acquisition_id,
            ))

        prof_data.id = profile.id

    session.commit()

    # Prepare data for the streaming generator (must not reference the
    # request-scoped session after it's closed).
    owner_id = current_user.id
    upload_data = {
        "platform": platform,
        "profile_count": len(profiles),
        "profiles": [p.model_dump(mode="json") for p in profiles],
        "persisted": True,
        "relic_delta": relic_delta.model_dump(mode="json"),
        "comparison": comparison.model_dump(mode="json"),
        "save_upload_id": str(save_upload.id),
    }

    # Pre-compute relics per slot for optimization
    relics_by_slot: dict[int, list[OwnedRelic]] = {}
    for prof_data in profiles:
        relics_by_slot[prof_data.slot_index] = _owned_from_parsed(prof_data.relics)

    # Snapshot affected build info so the generator doesn't touch the session
    affected_info = [
        (ab.build.id, ab.build.name, ab.build.character, ab.slot_index, ab.broken_pins)
        for ab in affected
    ]
    # We need BuildDefinitions for optimization — compute while session is alive
    build_defs: dict[uuid.UUID, BuildDefinition] = {
        ab.build.id: build_def_from_db(ab.build) for ab in affected
    }

    def _generate():
        yield f"data: {json.dumps({'type': 'upload_complete', 'data': upload_data})}\n\n"

        if not affected_info:
            yield f"data: {json.dumps({'type': 'complete', 'changes': []})}\n\n"
            return

        all_changes: list[dict] = []
        total = len(affected_info)

        # Pre-compute per-build skip decisions (relics + build unchanged) in
        # one session, so the prefetch below never submits pool tasks for a
        # build that will be skipped.
        skip_flags: list[bool] = []
        try:
            with Session(engine) as snap_session:
                for b_id, _bn, _ch, b_slot, _bp in affected_info:
                    b_relics = relics_by_slot.get(b_slot, [])
                    snap = snap_session.exec(
                        select(OptimizationSnapshot).where(
                            OptimizationSnapshot.build_id == b_id,
                            OptimizationSnapshot.slot_index == b_slot,
                        )
                    ).first()
                    skip_flags.append(bool(
                        snap
                        and snap.relics_hash == relics_signature(b_relics)
                        and snap.build_hash == build_signature(build_defs[b_id])
                        and snap.optimizer_version == OPTIMIZER_VERSION
                        and snap.game_data_version == game_data_version()
                    ))
        except Exception:
            skip_flags = [False] * total

        scorer = BuildScorer(ds)
        optimizer = VesselOptimizer(ds, scorer)

        def _submit_for(idx0: int) -> dict | None:
            """Vessel futures for affected_info[idx0], or None (no pool /
            skipped / unknown hero — the unknown-hero error surfaces when the
            build is processed)."""
            if executor is None or skip_flags[idx0]:
                return None
            b_id, _bn, b_char, b_slot, _bp = affected_info[idx0]
            b_hero = _CHAR_NAME_TO_HERO_TYPE.get(b_char)
            if b_hero is None:
                return None
            b_inventory = RelicInventory.from_owned_relics(
                relics_by_slot.get(b_slot, []))
            return optimizer.submit_all_vessels(
                build_defs[b_id], b_inventory, b_hero,
                max_per_vessel=3, executor=executor)

        # Depth-1 prefetch: the next non-skipped build's vessel tasks are
        # submitted before draining the current build's futures, so pool
        # workers idle during a build's completion tail start early on the
        # next.  Per-build SSE event order is unchanged — prefetched futures
        # are simply collected instantly when their build's turn comes.
        pending: tuple[int, dict] | None = None

        def _prefetch_after(i0: int) -> None:
            nonlocal pending
            if executor is None or pending is not None:
                return
            for j in range(i0 + 1, total):
                if not skip_flags[j]:
                    futures = _submit_for(j)
                    if futures is not None:
                        pending = (j, futures)
                    return

        for idx0, (build_id, build_name, character, slot_index, broken_pins) in enumerate(affected_info):
            idx = idx0 + 1
            yield f"data: {json.dumps({'type': 'optimize_start', 'build_id': str(build_id), 'build_name': build_name, 'index': idx, 'total': total})}\n\n"

            owned_relics = relics_by_slot.get(slot_index, [])
            build_def = build_defs[build_id]

            if skip_flags[idx0]:
                change_data = BuildChange(
                    build_id=str(build_id),
                    build_name=build_name,
                    slot_index=slot_index,
                    status="unchanged",
                    causes=["relics"],
                    cause="relics",
                    reliable=True,
                ).model_dump(mode="json")
                all_changes.append(change_data)
                yield f"data: {json.dumps({'type': 'optimize_done', 'build_id': str(build_id), 'change': change_data})}\n\n"
                continue

            # Run optimization with streaming progress
            try:
                hero_type = _CHAR_NAME_TO_HERO_TYPE.get(character)
                if hero_type is None:
                    raise ValueError(f"Unknown character '{character}'")

                futures = None
                if pending is not None and pending[0] == idx0:
                    futures = pending[1]
                    pending = None
                if futures is None:
                    futures = _submit_for(idx0)  # None when executor is None
                _prefetch_after(idx0)

                inventory = RelicInventory.from_owned_relics(owned_relics)

                results: list[VesselResult] = []
                for event in optimizer.optimize_vessels_streaming(
                    build_def, inventory, hero_type,
                    top_n=10, max_per_vessel=3,
                    executor=executor, presubmitted=futures,
                ):
                    if event["type"] == "progress":
                        yield f"data: {json.dumps({'type': 'optimize_progress', 'build_id': str(build_id), 'vessel': event['vessel'], 'total': event['total'], 'name': event['name']})}\n\n"
                    elif event["type"] == "result":
                        results = event["data"]

                # Persist snapshot
                with Session(engine) as snap_session:
                    # Re-load the build row in this session for FK integrity
                    build_row = snap_session.get(Build, build_id)
                    if build_row:
                        change = _apply_snapshot_for_stream(
                            snap_session, owner_id, build_row, build_def,
                            owned_relics, slot_index, results, ds,
                            top_n=10, max_per_vessel=3,
                        )
                        if broken_pins:
                            change.pinned_removed = broken_pins
                        change_data = change.model_dump(mode="json")
                    else:
                        change_data = {"status": "error", "build_id": str(build_id)}
            except Exception as exc:
                change_data = {
                    "status": "error",
                    "build_id": str(build_id),
                    "detail": str(exc),
                }

            all_changes.append(change_data)
            yield f"data: {json.dumps({'type': 'optimize_done', 'build_id': str(build_id), 'change': change_data})}\n\n"

        yield f"data: {json.dumps({'type': 'complete', 'changes': all_changes})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/status", response_model=SaveStatusPublic | None)
def get_save_status(
    session: SessionDep,
    current_user: CurrentUser,
) -> SaveStatusPublic | None:
    """Return metadata about the user's most recent save upload, or null if none."""
    upload = session.exec(
        select(SaveUpload).where(SaveUpload.owner_id == current_user.id)
    ).first()

    if not upload:
        return None

    profiles = session.exec(
        select(Profile)
        .where(Profile.save_upload_id == upload.id)
        .order_by(col(Profile.slot_index))
    ).all()

    return SaveStatusPublic(
        id=upload.id,
        platform=upload.platform,
        uploaded_at=upload.uploaded_at,
        profile_count=upload.profile_count,
        profile_names=[p.name for p in profiles],
    )


@router.get("/profiles", response_model=ProfilesPublic)
def list_profiles(session: SessionDep, current_user: CurrentUser) -> ProfilesPublic:
    """List all saved profiles for the current user."""
    statement = (
        select(Profile)
        .where(Profile.owner_id == current_user.id)
        .order_by(col(Profile.slot_index))
    )
    profiles = session.exec(statement).all()
    return ProfilesPublic(
        data=[ProfilePublic.model_validate(p) for p in profiles],
        count=len(profiles),
    )


@router.get("/profiles/{profile_id}/relics", response_model=RelicsPublic)
def get_profile_relics(
    profile_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> RelicsPublic:
    """Get all relics for a saved profile."""
    profile = session.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    statement = (
        select(Relic)
        .where(Relic.profile_id == profile_id)
        .order_by(col(Relic.name))
    )
    relics = session.exec(statement).all()
    return RelicsPublic(
        data=[RelicPublic.model_validate(r) for r in relics],
        count=len(relics),
    )


@router.get("/profiles/{profile_id}/loadouts", response_model=LoadoutsPublic)
def get_profile_loadouts(
    profile_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    ds: GameDataDep,
) -> LoadoutsPublic:
    """Get the in-game relic loadout presets parsed from a saved profile."""
    profile = session.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    loadouts = [ParsedLoadoutData.model_validate(p) for p in (profile.loadouts or [])]

    # Backfill cumulative effects for loadouts stored before the field existed
    # (deterministic from the loadout's relics — recompute rather than re-upload).
    if any(not l.cumulative_effects for l in loadouts):
        relics = session.exec(
            select(Relic).where(Relic.profile_id == profile_id)
        ).all()
        eff_by_handle = {
            r.ga_handle: [r.effect_1, r.effect_2, r.effect_3,
                          r.curse_1, r.curse_2, r.curse_3]
            for r in relics
        }
        for lo in loadouts:
            if lo.cumulative_effects:
                continue
            ids: list[int] = []
            for h in lo.ga_handles:
                ids.extend(eff_by_handle.get(h, []))
            lo.cumulative_effects = summarize_cumulative_effects(ids, ds)

    return LoadoutsPublic(
        data=loadouts,
        count=len(loadouts),
        used=len(loadouts),
        capacity=100,
    )


# ---------------------------------------------------------------------------
# Save export (sell/delete relics, credit Murk, re-encrypt)
# ---------------------------------------------------------------------------

def _edited_filename(filename: str) -> str:
    """NR0000.sl2 -> NR0000_edited.sl2."""
    p = Path(filename)
    return f"{p.stem}_edited{p.suffix or '.sl2'}"


def _export_modified_save(
    file_bytes: bytes,
    filename: str,
    slot_index: int,
    ga_handles: set[int],
    favorite_changes: dict[int, bool],
    ds: Any,
    items_json: dict,
) -> tuple[bytes, dict]:
    """Decrypt one character slot, apply bookmark changes + sell relics, re-encrypt.

    Bookmark changes are applied first so a relic can be un-bookmarked and sold
    in the same export. Returns (new_save_bytes, summary). Raises HTTPException
    on validation errors. The embedded Steam ID is left unchanged (same-account
    re-import only).
    """
    platform = _detect_platform(filename)
    if platform != "PC":
        raise HTTPException(
            status_code=422,
            detail="Save export currently supports PC (.sl2) saves only.",
        )
    if file_bytes[:4] != b"BND4":
        raise HTTPException(
            status_code=422,
            detail="Not a valid PC .sl2 save file (missing BND4 header).",
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        save_path = tmp_path / Path(filename).name
        save_path.write_bytes(file_bytes)
        decrypt_dir = tmp_path / "decrypted"
        decrypt_dir.mkdir()

        try:
            decrypt_sl2(save_path, decrypt_dir)
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Failed to decrypt save file: {exc}"
            ) from exc

        userdata_path = decrypt_dir / f"USERDATA_{slot_index:02d}"
        if not userdata_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Character slot {slot_index} not found in this save file.",
            )
        blob = userdata_path.read_bytes()

        raw_relics, items_end = parse_relics(blob)
        inventory = RelicInventory(raw_relics, items_json, ds)
        owned = {r.ga_handle: r for r in inventory.relics}
        loadout = LoadoutHandler(ds)
        loadout.parse(blob)
        equipped = set(loadout.relic_ga_hero_map.keys())

        # --- validate that all referenced relics exist in this save --------
        referenced = ga_handles | set(favorite_changes)
        missing = sorted(h for h in referenced if h not in owned)
        if missing:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unknown_relics",
                    "message": "Some selected relics are not in this save file. "
                               "Make sure you uploaded the same save the inventory came from.",
                    "ga_handles": missing,
                },
            )

        # --- apply bookmark changes first ----------------------------------
        fav_changed = 0
        if favorite_changes:
            blob, fav_result = set_favorites(blob, favorite_changes)
            fav_changed = len(fav_result.changed_handles)

        # Re-read favorites from the (possibly updated) blob so an un-bookmark
        # in this same request unlocks the relic for selling.
        favorites = read_favorite_handles(blob, items_end)

        # --- validate sells against current protection state ---------------
        blocked_equipped = sorted(h for h in ga_handles if h in equipped)
        blocked_favorite = sorted(h for h in ga_handles if h in favorites)
        if blocked_equipped or blocked_favorite:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "protected_relics",
                    "message": "Cannot sell equipped or bookmarked relics.",
                    "equipped": blocked_equipped,
                    "favorite": blocked_favorite,
                },
            )

        # --- sell + repack -------------------------------------------------
        murk_credit = sum(
            sell_value(owned[h].effect_count, owned[h].is_deep) for h in ga_handles
        )
        new_blob, del_result = delete_relics(blob, ga_handles, murk_credit=murk_credit)
        new_save = repack_sl2(file_bytes, {slot_index: new_blob})

    summary = {
        "removed": len(del_result.removed_handles),
        "favorites_changed": fav_changed,
        "murk_credit": murk_credit,
        "murks_before": del_result.murks_before,
        "murks_after": del_result.murks_after,
    }
    return new_save, summary


@router.post("/export")
async def export_save(
    file: UploadFile,
    ds: GameDataDep,
    current_user: OptionalUser,
    slot_index: int = Form(...),
    ga_handles: str = Form("[]"),
    favorite_changes: str = Form("{}"),
) -> Response:
    """Apply inventory edits to one character slot and return a modified .sl2
    the user can re-import into their own game.

    - ``ga_handles``: JSON array of relic ga_handles to sell (delete + credit Murk).
    - ``favorite_changes``: JSON object mapping ga_handle -> bool to
      bookmark/unbookmark relics.

    Stateless: the original save is provided in the request (we do not persist
    raw saves). Works for anonymous and authenticated users alike.
    """
    items_json = get_items_json()

    try:
        handles = {int(h) for h in json.loads(ga_handles)}
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="ga_handles must be a JSON array of integers.",
        ) from exc

    try:
        fav_changes = {int(k): bool(v) for k, v in json.loads(favorite_changes).items()}
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="favorite_changes must be a JSON object of {ga_handle: bool}.",
        ) from exc

    if not handles and not fav_changes:
        raise HTTPException(status_code=422, detail="No changes selected.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="Empty save file.")
    filename = file.filename or "save.sl2"

    new_save, summary = await run_in_threadpool(
        _export_modified_save,
        file_bytes,
        filename,
        slot_index,
        handles,
        fav_changes,
        ds,
        items_json,
    )

    out_name = _edited_filename(filename)
    return Response(
        content=new_save,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-Relics-Removed": str(summary["removed"]),
            "X-Favorites-Changed": str(summary["favorites_changed"]),
            "X-Murks-Credited": str(summary["murk_credit"]),
            "X-Murks-Total": str(summary["murks_after"]),
            "Access-Control-Expose-Headers": (
                "Content-Disposition, X-Relics-Removed, X-Favorites-Changed, "
                "X-Murks-Credited, X-Murks-Total"
            ),
        },
    )


# ---------------------------------------------------------------------------
# Relic minting ("purchase" generated relics into a save)
# ---------------------------------------------------------------------------

_RELIC_RECORD_SIZE = 80  # full relic ItemState record (see nrplanner.save)


def _export_added_save(
    file_bytes: bytes,
    filename: str,
    slot_index: int,
    specs: list[AddRelicSpec],
    ds: Any,
    murk_delta: int = 0,
) -> tuple[bytes, dict]:
    """Mint generated relics into one character slot and re-encrypt.

    Each spec is re-validated as a legal relic, packed into an 80-byte ItemState
    record templated from one of the save's OWN relics (so durability/unknown/
    trailing bytes are provably correct), added via ghost resurrection — then
    virgin-tail minting beyond the ghost supply — and the slot re-encrypted.
    Returns (new_save_bytes, summary). Raises HTTPException on
    validation/capacity errors. The embedded Steam ID is left unchanged.
    """
    blob = _decrypt_slot_blob(file_bytes, filename, slot_index)

    raw_relics, _ = parse_relics(blob)
    # Structural capacity AND the in-game 1950 storage cap — minting must never
    # push the save past what the game itself would hold (1:1 fidelity).
    cap = min(add_capacity(blob), max(0, RELIC_STORAGE_CAP - len(raw_relics)))

    if specs:
        donor = next(
            (r for r in raw_relics if r.size == _RELIC_RECORD_SIZE), None)
        if donor is None:
            raise HTTPException(
                status_code=422,
                detail="This save has no existing relic to use as a record template. "
                       "Acquire at least one relic in-game first.",
            )
        template = blob[donor.offset:donor.offset + _RELIC_RECORD_SIZE]

        checker = RelicChecker([], ds)
        safe_ids = set(ds.get_safe_relic_ids())

        records = []
        for i, spec in enumerate(specs):
            if spec.real_id not in safe_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"Relic #{i} (id {spec.real_id}) is not a mintable relic.",
                )
            effects = (list(spec.effects) + [EMPTY_EFFECT] * 3)[:3]
            curses = (list(spec.curses) + [EMPTY_EFFECT] * 3)[:3]
            reason = checker.check_invalidity(spec.real_id, effects + curses)
            if reason != InvalidReason.NONE:
                raise HTTPException(
                    status_code=422,
                    detail=f"Relic #{i} is not a legal relic ({reason.name}).",
                )
            records.append(
                build_relic_record(spec.real_id, effects, curses, template))

        if len(records) > cap:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "add_capacity",
                    "message": f"This save can accept only {cap} more relic(s) at once "
                               "(reusable inventory records + mintable free slots, "
                               "within the 1950 relic storage cap). Add fewer at a time.",
                    "capacity": cap,
                    "requested": len(records),
                },
            )

        try:
            new_blob, add_result = add_relics(blob, records)
        except AddCapacityError as exc:  # pragma: no cover - guarded by cap check above
            raise HTTPException(
                status_code=422,
                detail={"error": "add_capacity", "message": str(exc),
                        "capacity": cap},
            ) from exc
        added_handles = list(add_result.added_handles)
    else:
        # Pure Murk settlement: an all-dud rites batch mints nothing, but the
        # buy/sell spread it lost is still a real spend (1:1 fidelity — the
        # in-game walk would have cost it).
        new_blob, added_handles = blob, []

    # Apply the net purchase Murk delta (negative = spent, positive = refund) so the
    # buy/sell economy is faithful on export. Fidelity: never lets Murk go negative.
    murks_after: int | None = None
    if murk_delta:
        new_blob, _before, murks_after = adjust_murks(new_blob, murk_delta)

    new_save = repack_sl2(file_bytes, {slot_index: new_blob})
    summary = {
        "added": len(added_handles),
        # Real ga_handles assigned by ghost resurrection, ordered 1:1 with the
        # input specs — the client substitutes these for the synthetic handles
        # its staged loadouts reference.
        "added_handles": added_handles,
        "capacity": cap,
        "murks_after": murks_after,
    }
    return new_save, summary


@router.post("/export-add-relics")
async def export_add_relics(
    file: UploadFile,
    ds: GameDataDep,
    current_user: OptionalUser,
    slot_index: int = Form(...),
    specs: str = Form("[]"),
    murk_delta: int = Form(0),
) -> Response:
    """Mint generated relics into one character slot and return a modified .sl2.

    ``specs``: JSON array of {real_id, effects, curses} from prior rolls.
    ``murk_delta``: signed net Murk to apply (e.g. a bulk-purchase cost minus dud
    refunds). Stateless (the save is uploaded in the request); anonymous + auth alike.
    """
    from pydantic import TypeAdapter, ValidationError

    try:
        parsed = TypeAdapter(list[AddRelicSpec]).validate_python(json.loads(specs))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid specs payload: {exc}"
        ) from exc

    # Zero specs is valid WITH a Murk delta: an all-dud rites batch exports as
    # a pure spend (rolling is buying; the spread loss must reach the save).
    if not parsed and not murk_delta:
        raise HTTPException(status_code=422, detail="Nothing to export.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="Empty save file.")
    filename = file.filename or "save.sl2"

    new_save, summary = await run_in_threadpool(
        _export_added_save, file_bytes, filename, slot_index, parsed, ds, murk_delta,
    )

    out_name = _edited_filename(filename)
    murks_after = summary.get("murks_after")
    return Response(
        content=new_save,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-Relics-Added": str(summary["added"]),
            "X-Added-Handles": json.dumps(summary["added_handles"]),
            "X-Add-Capacity": str(summary["capacity"]),
            "X-Murks-Total": "" if murks_after is None else str(murks_after),
            "Access-Control-Expose-Headers": (
                "Content-Disposition, X-Relics-Added, X-Added-Handles, "
                "X-Add-Capacity, X-Murks-Total"
            ),
        },
    )


# ---------------------------------------------------------------------------
# Relic Rites (bulk purchase simulation)
# ---------------------------------------------------------------------------


def _parse_sold_handles(raw: str) -> set[int]:
    """Parse the staged-sell ga_handles form field. 422 on malformed input.

    Staged sells apply in EVERY stop mode: their relics are gone from the
    plan's world state (freed storage) and their refund is spendable Murk —
    in-game the player can sell at the shop before buying, so crediting the
    refund in fixed/budget plans is as faithful as funding the all_murk walk.
    """
    try:
        val = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="sold_handles must be a JSON array of integers.") from exc
    if not isinstance(val, list) or not all(isinstance(h, int) for h in val):
        raise HTTPException(
            status_code=422,
            detail="sold_handles must be a JSON array of integers.")
    return set(val)


def _parse_staged_favorites(raw: str) -> dict[int, bool]:
    """Parse staged bookmark toggles ({ga_handle: desired}) — 422 on bad input.

    Same shape as /saves/export's ``favorite_changes``. The protected-sell
    gate judges the live bookmark state, so a bookmark staged OFF makes its
    relic sellable and one staged ON protects it.
    """
    try:
        val = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="staged_favorites must be a JSON object of "
                   "ga_handle -> bool.") from exc
    if not isinstance(val, dict):
        raise HTTPException(
            status_code=422,
            detail="staged_favorites must be a JSON object of "
                   "ga_handle -> bool.")
    try:
        return {int(k): bool(v) for k, v in val.items()}
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="staged_favorites must be a JSON object of "
                   "ga_handle -> bool.") from exc


def _parse_staged_mints(raw: str) -> list[StagedMint]:
    """Parse the staged-mints form field (JSON array of StagedMint). 422 on bad input.

    These are Relic Rites purchases already staged in the app but not yet
    exported — the same stateless diff the optimizer receives. Legality is
    enforced later by ``apply_staged_diff`` (RelicChecker + safe relic ids),
    so an impossible relic can never enter the plan's world state.
    """
    from pydantic import TypeAdapter, ValidationError

    try:
        val = json.loads(raw or "[]")
        return TypeAdapter(list[StagedMint]).validate_python(val)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"staged_mints must be a JSON array of staged mints: {exc}",
        ) from exc


def _resolve_rites_builds(
    build_ids_raw: str, builds_raw: str, current_user: Any, session: Session
) -> list[BuildContext]:
    """Resolve saved (auth) or inline (anon) builds into BuildContexts.

    Mirrors optimize.py's dual mode: ``build_ids`` (DB, ownership-checked) OR inline
    ``builds`` — not both. Owned relics come from the uploaded save, not from here.
    """
    from pydantic import TypeAdapter, ValidationError

    try:
        ids = [uuid.UUID(str(x)) for x in json.loads(build_ids_raw or "[]")]
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid build_ids: {exc}") from exc
    try:
        inline = json.loads(builds_raw or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid builds: {exc}") from exc

    if ids and inline:
        raise HTTPException(
            status_code=422, detail="Provide build_ids OR inline builds, not both."
        )

    ctxs: list[BuildContext] = []
    if ids:
        if current_user is None:
            raise HTTPException(
                status_code=401, detail="Authentication required to use saved builds."
            )
        for bid in ids:
            db_build = session.get(Build, bid)
            if not db_build or db_build.owner_id != current_user.id:
                raise HTTPException(status_code=404, detail=f"Build {bid} not found.")
            bdef = build_def_from_db(db_build)
            ctxs.append(BuildContext(
                build=bdef,
                hero_type=_resolve_hero_type(bdef.character),
                name=db_build.name,
                build_id=str(db_build.id),
            ))
    elif inline:
        try:
            bdefs = TypeAdapter(list[BuildDefinition]).validate_python(inline)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=f"Invalid inline builds: {exc}"
            ) from exc
        for i, bdef in enumerate(bdefs):
            ctxs.append(BuildContext(
                build=bdef,
                hero_type=_resolve_hero_type(bdef.character),
                name=getattr(bdef, "name", None) or f"Build {i + 1}",
            ))

    return ctxs  # may be empty: no builds -> rules-only keep/cull, no optimizer runs


def _resolve_buckets(buckets_raw: str) -> list[PurchaseBucket]:
    """Parse purchase buckets, pulling buy_cost from relic_lots.json (never the client).

    1:1 fidelity: prices come from the extracted game data. MVP supports only the
    Murk-priced Scenic / Deep Scenic flatstones; the Sigil-priced Large flatstones are
    rejected until the Sovereign-Sigil save offset is known.
    """
    lots = get_relic_lots()
    try:
        raw = json.loads(buckets_raw or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid buckets: {exc}") from exc

    out: list[PurchaseBucket] = []
    for b in raw:
        is_deep = bool(b.get("is_deep", False))
        version = str(b.get("version", "1.03"))
        quantity = b.get("quantity")
        key = f"{'deep' if is_deep else 'scenic'}_{version}"
        lot = lots.get(key)
        if not lot or "buy_cost" not in lot:
            raise HTTPException(
                status_code=422, detail=f"No price data for flatstone '{key}'."
            )
        if str(lot.get("buy_currency", "Murk")).lower() != "murk":
            raise HTTPException(
                status_code=422,
                detail=f"'{key}' is priced in Sovereign Sigils — not yet supported.",
            )
        out.append(PurchaseBucket(
            is_deep=is_deep,
            version=version,
            buy_cost=int(lot["buy_cost"]),
            quantity=int(quantity) if quantity is not None else None,
        ))
    return out


def _parse_rules(raw: str, field_name: str) -> list[dict]:
    """Parse a JSON array of cull rules (objects). 422 on malformed input."""
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422, detail=f"{field_name} must be a JSON array.") from exc
    if not isinstance(val, list) or not all(isinstance(r, dict) for r in val):
        raise HTTPException(
            status_code=422, detail=f"{field_name} must be a JSON array of rule objects.")
    return val


def _rites_roll_seed(slot_index: int, current_murk: int, owned: list) -> int:
    """Deterministic RNG seed from the save's current purchasable state.

    Anti-save-scum (1:1 fidelity, see CLAUDE.md): re-running the purchase on the SAME
    save state reproduces the identical relics, so declining to export and retrying
    gains nothing. A genuinely different roll requires actually CHANGING the save state
    — exporting and re-importing, which spends the Murk and adds the relics — mirroring
    the game, where a purchase can't be previewed-and-retried for free. Uses a stable
    hash (not builtin hash()) so it's identical across processes/restarts.
    """
    fp = f"{slot_index}|{current_murk}|{relics_signature(owned)}"
    return int.from_bytes(hashlib.sha256(fp.encode()).digest()[:8], "big")


# Must match bulk_acquire's max_per_vessel default — the snapshot-seed gate
# compares stored run params against this value.
_RITES_MAX_PER_VESSEL = 3


def _rites_snapshot_seed_rows(
    session: Session, build_ctxs: list[BuildContext], slot_index: int,
    top_n: int, max_per_vessel: int = _RITES_MAX_PER_VESSEL,
) -> dict[str, dict]:
    """Per-build snapshot data for rites reuse, extracted while a session is open.

    Only candidate seeds: rows whose stored run params match the rites request
    and that carry full_results + a relevant-subset hash.  Freshness against
    the UPLOADED save's inventory can only be judged inside ``_rites_plan``
    (the file is parsed there), so validation happens in
    ``_validated_seed_fps`` — this helper just packs plain, thread-safe dicts.
    """
    seeds: dict[str, dict] = {}
    for ctx in build_ctxs:
        if not ctx.build_id:
            continue  # inline/anonymous build — no snapshot exists
        snap = session.exec(
            select(OptimizationSnapshot).where(
                OptimizationSnapshot.build_id == uuid.UUID(ctx.build_id),
                OptimizationSnapshot.slot_index == slot_index,
            )
        ).first()
        if (
            snap is None
            or not snap.full_results
            or snap.relevant_relics_hash is None
            or snap.top_n != top_n
            or snap.max_per_vessel != max_per_vessel
        ):
            continue
        used: set[tuple] = set()
        for layout in snap.full_results:
            for a in layout.get("assignments", []):
                r = a.get("relic")
                if r:
                    used.add(relic_fingerprint(
                        r["real_id"], r["effects"], r["curses"]))
        seeds[ctx.build_id] = {
            "build_hash": snap.build_hash,
            "game_data_version": snap.game_data_version,
            "optimizer_version": snap.optimizer_version,
            "relevant_relics_hash": snap.relevant_relics_hash,
            "used_fps": used,
        }
    return seeds


def _validated_seed_fps(
    build_ctxs: list[BuildContext],
    seeds: dict[str, dict] | None,
    owned: list[OwnedRelic],
    ds: Any,
) -> dict[str, set]:
    """Owned-only used-fps per build, from snapshots proven fresh for THIS save.

    Fresh = same scoring definition (build_hash), same solver + game data
    versions, and an unchanged build-relevant relic subset in the uploaded
    inventory.  Then the stored top-N *is* the owned-only optimization result
    — and reusing it keeps cull decisions consistent with what the optimizer
    page displays.
    """
    if not seeds:
        return {}
    owned_pairs = [(fingerprint_owned(o), o.ga_handle) for o in owned]
    gdv = game_data_version()
    out: dict[str, set] = {}
    for ctx in build_ctxs:
        seed = seeds.get(ctx.build_id) if ctx.build_id else None
        if seed is None:
            continue
        if (
            seed["build_hash"] == build_signature(ctx.build)
            and seed["game_data_version"] == gdv
            and seed["optimizer_version"] == OPTIMIZER_VERSION
            and seed["relevant_relics_hash"]
            == relevant_relics_signature(ctx.build, owned_pairs, ds)
        ):
            out[ctx.build_id] = set(seed["used_fps"])
    return out


def _rites_plan(
    file_bytes: bytes, filename: str, slot_index: int,
    build_ctxs: list[BuildContext], buckets: list[PurchaseBucket],
    stop_mode: str, budget: int | None, ds: Any, executor: Any, top_n: int,
    inclusion_rules: list[dict] | None = None,
    exclusion_rules: list[dict] | None = None,
    progress: Any = None,
    snapshot_seeds: dict[str, dict] | None = None,
    sold_handles: set[int] | None = None,
    staged_mints: list[StagedMint] | None = None,
    staged_murk_delta: int = 0,
    staged_favorites: dict[int, bool] | None = None,
) -> dict:
    """Compute a bulk-purchase plan. Reads current Murk, owned relics, and
    capacity from the uploaded save (1:1 fidelity — never client-supplied). Persists
    nothing.

    Pre-owned relics are NEVER analyzed or sold by this plan — only relics bought
    within the run are auto-sold (duds). ``sold_handles`` (any mode) are owned
    relics the user has already manually staged for sale elsewhere: the plan treats
    them as gone (freed storage, refunds spendable).

    ``staged_mints`` + ``staged_murk_delta`` are the app's live Murk emulation:
    purchases staged in an earlier rites run but not yet exported. Mints join the
    world state as owned content (they occupy storage and dedup re-rolled copies
    to duds — a staged keeper can never be minted twice) and the wallet starts
    from the spent-down value, so re-planning can never double-spend Murk the
    app already committed. The delta is client-supplied (dud refunds from the
    original batch cannot be reconstructed from the keepers alone — same trust
    model as /saves/export-add-relics) but only ever LOWERS the wallet: positive
    deltas are clamped, so a client cannot manufacture planning Murk.

    With no builds selected, keeping is rules-only and NO optimizer runs (instant).
    With builds, one optimize pass runs per build. ``progress`` (if given) is called
    (phase, current, total, message, name) — ``name`` is the current build's name on
    per-build phases, else None — so callers can stream feedback.
    """
    def _emit(phase: str, current: int, total: int, message: str,
              name: str | None = None) -> None:
        if progress is not None:
            progress(phase, current, total, message, name)

    blob = _decrypt_slot_blob(file_bytes, filename, slot_index)
    raw_relics, items_end = parse_relics(blob)
    owned_all = RelicInventory(raw_relics, get_items_json(), ds).relics
    current_murk = read_murks(blob, items_end)
    cap = add_capacity(blob)

    # Parsed once: staged-sell validation here + cull protection below.
    loadout = LoadoutHandler(ds)
    loadout.parse(blob)
    equipped = set(loadout.relic_ga_hero_map.keys())
    favorites = read_favorite_handles(blob, items_end)
    # Bookmark toggles staged in-app override the save's bookmarks — the
    # Inventory page lets a user un-bookmark and trash in one session, so the
    # protected-sell gate below must judge the LIVE bookmark state, exactly
    # like /saves/export applies favorite_changes before its own gate.
    for h, fav in (staged_favorites or {}).items():
        if fav:
            favorites.add(h)
        else:
            favorites.discard(h)

    sold_set = set(sold_handles or ())
    if sold_set:
        known = {o.ga_handle for o in owned_all}
        missing = sorted(h for h in sold_set if h not in known)
        if missing:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unknown_relics",
                    "message": "Some staged sells are not in this save file. "
                               "Make sure you uploaded the same save the "
                               "inventory came from.",
                    "ga_handles": missing,
                },
            )
        blocked_equipped = sorted(h for h in sold_set if h in equipped)
        blocked_favorite = sorted(h for h in sold_set if h in favorites)
        if blocked_equipped or blocked_favorite:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "protected_relics",
                    "message": "Cannot sell equipped or bookmarked relics.",
                    "equipped": blocked_equipped,
                    "favorite": blocked_favorite,
                },
            )

    # The plan's world state, composed by the shared effective-state layer:
    # staged sells are already gone (slots freed, content no longer owned,
    # refund spendable), staged mints are already owned (occupying storage;
    # re-rolled identical content dedups to a dud, so a staged keeper can
    # never be double-minted; legality-validated like the export path), and
    # the wallet is spent down by the staged batch delta (positive deltas
    # clamped — planning Murk can never exceed what the save holds).
    state = effective_slot_state(
        owned_all, current_murk, cap, sorted(sold_set),
        list(staged_mints or ()), staged_murk_delta, ds, get_items_json())
    owned = state.owned
    wallet = state.wallet
    pending_sold_refund = state.pending_sold_refund

    if stop_mode == "all_murk":
        # Pure in-game storage cap. The writer's ghost add-capacity is an
        # export-time concern only — the in-game buy/sell cycle is never
        # constrained by it, so neither is the simulation.
        storage_left = state.storage_left_ingame
    else:
        # Export-time capacity also caps a fixed/budget batch (each staged
        # sell tombstones a ghost slot; each staged mint will consume one).
        storage_left = min(state.storage_left_ingame, state.ghost_capacity)

    scorer = BuildScorer(ds)
    optimizer = VesselOptimizer(ds, scorer)

    # Per-plan owned-only solve cache pre-seeded from optimization snapshots
    # proven fresh for this save — a build with no relevant candidates skips
    # its enlarged solve, and not even the owned-only one on a snapshot hit.
    # Freshness is judged against the EFFECTIVE
    # inventory (staged sells excluded, staged mints included): a snapshot
    # whose relevant subset contains a staged-sold relic — or misses a staged
    # mint a build could use — correctly misses (owned-only re-solve, the
    # accepted cost when the staged diff overlaps a build's relevant relics).
    owned_used: dict[str, set] = _validated_seed_fps(
        build_ctxs, snapshot_seeds, owned, ds)

    keepers: list[RitesKeeper] = []
    generated = kept = duds = 0
    murk_after = wallet + pending_sold_refund
    murk_cost, murk_refunded = 0, 0
    limited_by: str | None = None

    if buckets:
        _emit("generating", 0, len(build_ctxs), "Rolling purchases…")
        r = bulk_acquire(
            builds=build_ctxs, owned=owned, current_murk=wallet,
            buckets=buckets, generator=get_relic_generator(), ds=ds,
            stop_mode=stop_mode, budget=budget,
            inclusion_rules=inclusion_rules, exclusion_rules=exclusion_rules,
            storage_cap_left=storage_left,
            extra_budget=pending_sold_refund,
            extra_reserved_handles=sold_set,
            scorer=scorer, optimizer=optimizer, executor=executor, top_n=top_n,
            max_per_vessel=_RITES_MAX_PER_VESSEL,
            progress=(lambda i, t, name: _emit(
                "matching", i, t, f"Matching build {i}/{t}: {name}", name)),
            # Deterministic per save-state: re-running without exporting can't
            # re-roll. Seeded from the RAW save (owned_all, save Murk) — staged
            # sells AND staged mints are app-side state and must never perturb
            # the roll stream (re-staging a different diff is not a free
            # re-roll; new rolls require export/re-import). A re-run with mints
            # staged replays the same stream: staged content dedups to duds,
            # so it honestly buys-and-resells at a spread loss instead.
            seed=_rites_roll_seed(slot_index, current_murk, owned_all),
            owned_used_fps=owned_used,
        )
        for k in r.keepers:
            rel = k.relic
            keepers.append(RitesKeeper(
                real_id=rel.real_id, item_id=rel.item_id, color=rel.color,
                tier=rel.effect_count, is_deep=rel.is_deep, name=rel.name,
                effects=list(rel.effects), curses=list(rel.curses), builds=k.builds,
                build_ranks=k.ranks, reason=k.reason,
            ))
        generated, kept, duds = r.generated, r.kept, r.duds
        murk_after, murk_cost, murk_refunded = (
            r.murk_after, r.murk_gross_cost, r.murk_refunded)
        limited_by = r.limited_by

    _emit("finalizing", 1, 1, "Finalizing plan…")
    return {
        "keepers": keepers,
        "generated": generated, "kept": kept, "duds": duds,
        # murk_before = the effective wallet (save Murk spent down by the
        # staged mint delta), so the UI's "before -> after" reflects the live
        # app state, not the stale on-disk value.
        "murk_before": wallet, "murk_after": murk_after,
        "murk_cost": murk_cost, "murk_refunded": murk_refunded,
        # Mint-side net ONLY (rides /saves/export-add-relics). Staged-sell
        # refunds are NOT included — the sells credit themselves via
        # /saves/export. Invariant:
        #   murk_after == murk_before + pending_sold_refund + murk_delta
        "murk_delta": murk_refunded - murk_cost,
        "limited_by": limited_by,
        # Effective export capacity, not the raw save's: staged sells free a
        # ghost slot each and already-staged mints have claimed theirs — this
        # is how many MORE relics the next export can actually mint.
        "add_capacity": state.ghost_capacity, "storage_left": storage_left,
        "pending_sold": len(sold_set),
        "pending_sold_refund": pending_sold_refund,
    }


@router.post("/rites/plan", response_model=RitesPlanResponse)
async def rites_plan(
    file: UploadFile,
    ds: GameDataDep,
    current_user: OptionalUser,
    session: SessionDep,
    executor: OptimizerPoolDep = None,
    slot_index: int = Form(...),
    build_ids: str = Form("[]"),
    builds: str = Form("[]"),
    buckets: str = Form("[]"),
    stop_mode: str = Form("fixed"),
    budget: int | None = Form(None),
    top_n: int = Form(10),
    inclusion_rules: str = Form("[]"),
    exclusion_rules: str = Form("[]"),
    sold_handles: str = Form("[]"),
    staged_mints: str = Form("[]"),
    staged_murk_delta: int = Form(0),
    staged_favorites: str = Form("{}"),
) -> RitesPlanResponse:
    """Plan a bulk relic purchase against the uploaded save.

    Reads Murk / owned relics / capacity from the save (never client-supplied — 1:1
    fidelity), rolls purchases with exact effect odds, keeps only relics used in a saved
    build's top-N loadouts, and returns the keepers (to mint) and the exact Murk math.
    Pre-owned relics are never analyzed or sold. Writes NOTHING — the frontend stages
    this and applies it via /saves/export-add-relics (mints + murk_delta). Synchronous
    MVP; the SSE variant below streams progress for large batches.

    ``sold_handles`` / ``staged_mints`` / ``staged_murk_delta`` carry the app's
    staged-but-unexported diff so the plan runs against the LIVE Murk and
    inventory state — staged spend persists until export and can't be
    double-spent (see _rites_plan).
    """
    if stop_mode not in ("fixed", "budget", "all_murk"):
        raise HTTPException(
            status_code=422, detail="stop_mode must be one of fixed|budget|all_murk.")
    if not 1 <= top_n <= 10:
        raise HTTPException(
            status_code=422, detail="top_n must be between 1 and 10.")

    build_ctxs = _resolve_rites_builds(build_ids, builds, current_user, session)
    parsed_buckets = _resolve_buckets(buckets)
    incl = _parse_rules(inclusion_rules, "inclusion_rules")
    excl = _parse_rules(exclusion_rules, "exclusion_rules")
    sold = _parse_sold_handles(sold_handles)
    mints = _parse_staged_mints(staged_mints)
    favs = _parse_staged_favorites(staged_favorites)

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="Empty save file.")
    filename = file.filename or "save.sl2"

    seeds = _rites_snapshot_seed_rows(session, build_ctxs, slot_index, top_n)
    plan = await run_in_threadpool(
        _rites_plan, file_bytes, filename, slot_index, build_ctxs, parsed_buckets,
        stop_mode, budget, ds, executor, top_n, incl, excl,
        snapshot_seeds=seeds, sold_handles=sold,
        staged_mints=mints, staged_murk_delta=staged_murk_delta,
        staged_favorites=favs,
    )
    return RitesPlanResponse(**plan)


@router.post("/rites/plan/stream")
async def rites_plan_stream(
    file: UploadFile,
    ds: GameDataDep,
    current_user: OptionalUser,
    session: SessionDep,
    executor: OptimizerPoolDep = None,
    slot_index: int = Form(...),
    build_ids: str = Form("[]"),
    builds: str = Form("[]"),
    buckets: str = Form("[]"),
    stop_mode: str = Form("fixed"),
    budget: int | None = Form(None),
    top_n: int = Form(10),
    inclusion_rules: str = Form("[]"),
    exclusion_rules: str = Form("[]"),
    sold_handles: str = Form("[]"),
    staged_mints: str = Form("[]"),
    staged_murk_delta: int = Form(0),
    staged_favorites: str = Form("{}"),
) -> StreamingResponse:
    """Streaming (SSE) form of /rites/plan — emits progress while it runs.

    Same inputs as /rites/plan. Emits ``data:`` lines::

        {"type":"progress","phase":str,"current":int,"total":int,"message":str,
         "name":str|null}   # name = current build, on per-build phases only
        {"type":"result","data": <RitesPlanResponse>}
        {"type":"error","detail":"..."}

    Auth/validation errors surface as normal HTTP errors before streaming begins. The
    heavy plan runs in a worker thread; its per-build progress callback is bridged to the
    SSE stream through a thread-safe queue.
    """
    if stop_mode not in ("fixed", "budget", "all_murk"):
        raise HTTPException(
            status_code=422, detail="stop_mode must be one of fixed|budget|all_murk.")
    if not 1 <= top_n <= 10:
        raise HTTPException(
            status_code=422, detail="top_n must be between 1 and 10.")

    build_ctxs = _resolve_rites_builds(build_ids, builds, current_user, session)
    parsed_buckets = _resolve_buckets(buckets)
    incl = _parse_rules(inclusion_rules, "inclusion_rules")
    excl = _parse_rules(exclusion_rules, "exclusion_rules")
    sold = _parse_sold_handles(sold_handles)
    parsed_mints = _parse_staged_mints(staged_mints)
    parsed_favs = _parse_staged_favorites(staged_favorites)

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="Empty save file.")
    filename = file.filename or "save.sl2"

    # Session-bound work happens before the worker thread starts.
    seeds = _rites_snapshot_seed_rows(session, build_ctxs, slot_index, top_n)

    def _events():
        q: queue.Queue = queue.Queue()
        holder: dict[str, Any] = {}

        def _progress(phase, current, total, message, name=None):
            q.put({"type": "progress", "phase": phase, "current": current,
                   "total": total, "message": message, "name": name})

        def _work():
            try:
                holder["plan"] = _rites_plan(
                    file_bytes, filename, slot_index, build_ctxs, parsed_buckets,
                    stop_mode, budget, ds, executor, top_n, incl, excl,
                    progress=_progress, snapshot_seeds=seeds, sold_handles=sold,
                    staged_mints=parsed_mints,
                    staged_murk_delta=staged_murk_delta,
                    staged_favorites=parsed_favs)
            except HTTPException as exc:
                holder["error"] = exc.detail
            except Exception as exc:  # noqa: BLE001 - surfaced as an SSE error event
                holder["error"] = str(exc)
            finally:
                q.put(None)  # sentinel: work finished

        worker = threading.Thread(target=_work, daemon=True)
        worker.start()
        while True:
            item = q.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"
        worker.join()
        if "error" in holder:
            yield f"data: {json.dumps({'type': 'error', 'detail': holder['error']})}\n\n"
        else:
            payload = {"type": "result",
                       "data": RitesPlanResponse(**holder["plan"]).model_dump(mode="json")}
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Loadout export (add/delete/rename/overwrite presets, reset vessels/presets)
# ---------------------------------------------------------------------------

_MAX_PRESETS = 100
_NAME_MAX_UNITS = 18


def _resolve_hero_type(character: str) -> int:
    """Map a hero name to the 1-based save hero_type (Wylder=1 .. Undertaker=10)."""
    try:
        idx = list(CHARACTER_NAMES).index(character)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown character '{character}'.")
    if idx >= len(CHARACTER_NAMES) - 1:  # "All" sentinel — not a real hero
        raise HTTPException(
            status_code=422,
            detail="Loadouts must target a specific character, not 'All'.",
        )
    return idx + 1


def _decrypt_slot_blob(file_bytes: bytes, filename: str, slot_index: int) -> bytes:
    """Decrypt one PC character slot and return its USERDATA blob. Stateless."""
    platform = _detect_platform(filename)
    if platform != "PC":
        raise HTTPException(
            status_code=422,
            detail="Loadout export currently supports PC (.sl2) saves only.",
        )
    if file_bytes[:4] != b"BND4":
        raise HTTPException(
            status_code=422,
            detail="Not a valid PC .sl2 save file (missing BND4 header).",
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        save_path = tmp_path / Path(filename).name
        save_path.write_bytes(file_bytes)
        decrypt_dir = tmp_path / "decrypted"
        decrypt_dir.mkdir()
        try:
            decrypt_sl2(save_path, decrypt_dir)
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Failed to decrypt save file: {exc}"
            ) from exc
        userdata_path = decrypt_dir / f"USERDATA_{slot_index:02d}"
        if not userdata_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Character slot {slot_index} not found in this save file.",
            )
        return userdata_path.read_bytes()


def _validate_name(name: str) -> None:
    if len(name.encode("utf-16-le")) > _NAME_MAX_UNITS * 2:
        raise HTTPException(
            status_code=422,
            detail=f"Loadout name too long (max {_NAME_MAX_UNITS} characters).",
        )


def _validate_loadout_relics(ga_handles, owned: set[int]) -> None:
    if len(ga_handles) > 6:
        raise HTTPException(status_code=422, detail="A loadout has at most 6 relics.")
    missing = sorted(h for h in ga_handles if h != 0 and h not in owned)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unknown_relics",
                "message": "Some relics in this loadout are not in the uploaded save.",
                "ga_handles": missing,
            },
        )


def _export_modified_loadouts(
    file_bytes: bytes,
    filename: str,
    slot_index: int,
    operations: list,
    ds: Any,
    items_json: dict,
) -> tuple[bytes, dict]:
    """Apply a batch of loadout/vessel edits to one character slot and re-encrypt.

    Ops are applied in a deterministic order so that index-based edits stay
    well-defined: renames + overwrites (index-stable) first, then deletes in
    descending index order, then adds. Resets touch independent regions.
    """
    blob = _decrypt_slot_blob(file_bytes, filename, slot_index)

    raw_relics, _ = parse_relics(blob)
    inventory = RelicInventory(raw_relics, items_json, ds)
    owned = {r.ga_handle for r in inventory.relics}
    loadout = LoadoutHandler(ds)
    loadout.parse(blob)
    n_presets = len(loadout.all_presets)

    # --- categorise + validate ------------------------------------------------
    reset_vessels_op = False
    reset_presets_op = False
    renames, overwrites, deletes, adds = [], [], [], []
    for op in operations:
        kind = op.op
        if kind == "reset_vessels":
            reset_vessels_op = True
        elif kind == "reset_presets":
            reset_presets_op = True
        elif kind == "rename":
            _validate_name(op.name)
            renames.append(op)
        elif kind == "overwrite":
            _validate_name(op.name or "")
            _validate_loadout_relics(op.ga_handles, owned)
            overwrites.append(op)
        elif kind == "delete":
            deletes.append(op)
        elif kind == "add":
            _validate_name(op.name)
            _validate_loadout_relics(op.ga_handles, owned)
            adds.append(op)

    if reset_presets_op and (renames or overwrites or deletes or adds):
        raise HTTPException(
            status_code=422,
            detail="'Reset all loadouts' cannot be combined with other loadout edits.",
        )
    if n_presets + len(adds) - len(deletes) > _MAX_PRESETS:
        raise HTTPException(
            status_code=422,
            detail=f"That would exceed the {_MAX_PRESETS}-loadout limit.",
        )
    for op in (*renames, *overwrites, *deletes):
        if not 0 <= op.index < n_presets:
            raise HTTPException(
                status_code=422,
                detail=f"Loadout index {op.index} is out of range (0..{n_presets - 1}).",
            )
    for op in (*overwrites, *adds):
        hero_type = _resolve_hero_type(op.character)
        valid = {v["vessel_id"] for v in ds.get_all_vessels_for_hero(hero_type)}
        if op.vessel_id not in valid:
            raise HTTPException(
                status_code=422,
                detail=f"Vessel {op.vessel_id} is not valid for {op.character}.",
            )

    summary = {"added": 0, "deleted": 0, "renamed": 0, "overwritten": 0,
               "vessels_reset": False, "presets_reset": False}

    # --- apply (deterministic order) -----------------------------------------
    try:
        if reset_vessels_op:
            blob, rv = reset_all_vessels(blob)
            summary["vessels_reset"] = True

        if reset_presets_op:
            blob, rp = reset_all_presets(blob)
            summary["presets_reset"] = True
        else:
            for op in renames:
                blob, _ = rename_preset(blob, op.index, op.name)
                summary["renamed"] += 1
            for op in overwrites:
                blob, _ = overwrite_preset(
                    blob, op.index, vessel_id=op.vessel_id, ga_handles=op.ga_handles,
                    hero_type=_resolve_hero_type(op.character), name=op.name)
                summary["overwritten"] += 1
            for op in sorted(deletes, key=lambda o: o.index, reverse=True):
                blob, _ = delete_preset(blob, op.index)
                summary["deleted"] += 1
            for op in adds:
                blob, _ = add_preset(
                    blob, hero_type=_resolve_hero_type(op.character), name=op.name,
                    vessel_id=op.vessel_id, ga_handles=op.ga_handles)
                summary["added"] += 1
    except VesselWriteError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    new_save = repack_sl2(file_bytes, {slot_index: blob})

    # final loadout count for the client
    final = LoadoutHandler(ds)
    final.parse(blob)
    summary["used"] = len(final.all_presets)
    return new_save, summary


@router.post("/export-loadouts")
async def export_loadouts(
    file: UploadFile,
    ds: GameDataDep,
    current_user: OptionalUser,
    slot_index: int = Form(...),
    operations: str = Form("[]"),
) -> Response:
    """Apply in-game relic-loadout edits to one character slot and return a
    modified .sl2 the user can re-import into their own game.

    ``operations`` is a JSON array of discriminated-union ops (add / delete /
    rename / overwrite / reset_vessels / reset_presets). Stateless: the original
    save is provided in the request. Works for anonymous and authenticated users.
    """
    from pydantic import TypeAdapter, ValidationError

    try:
        ops = TypeAdapter(list[LoadoutOp]).validate_python(json.loads(operations))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid operations payload: {exc}"
        ) from exc

    if not ops:
        raise HTTPException(status_code=422, detail="No loadout changes selected.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="Empty save file.")
    filename = file.filename or "save.sl2"

    items_json = get_items_json()
    new_save, summary = await run_in_threadpool(
        _export_modified_loadouts,
        file_bytes, filename, slot_index, ops, ds, items_json,
    )

    out_name = _edited_filename(filename)
    return Response(
        content=new_save,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-Loadouts-Added": str(summary["added"]),
            "X-Loadouts-Deleted": str(summary["deleted"]),
            "X-Loadouts-Renamed": str(summary["renamed"]),
            "X-Loadouts-Overwritten": str(summary["overwritten"]),
            "X-Vessels-Reset": "1" if summary["vessels_reset"] else "0",
            "X-Presets-Reset": "1" if summary["presets_reset"] else "0",
            "X-Loadouts-Used": str(summary["used"]),
            "Access-Control-Expose-Headers": (
                "Content-Disposition, X-Loadouts-Added, X-Loadouts-Deleted, "
                "X-Loadouts-Renamed, X-Loadouts-Overwritten, X-Vessels-Reset, "
                "X-Presets-Reset, X-Loadouts-Used"
            ),
        },
    )
