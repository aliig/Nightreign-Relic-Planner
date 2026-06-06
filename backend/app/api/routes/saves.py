"""Save file upload, profile discovery, and relic inventory endpoints."""
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile
from sqlmodel import col, select

from app.api.deps import CurrentUser, GameDataDep, OptionalUser, SessionDep
from app.core.config import settings
from app.core.game_data import get_items_json
from app.models import (
    Build,
    OptimizationSnapshot,
    ProfilePublic,
    ProfilesPublic,
    Profile,
    ParsedProfileData,
    ParsedRelicData,
    Relic,
    RelicDelta,
    RelicPublic,
    RelicsPublic,
    SaveUpload,
    SaveStatusPublic,
    UploadResponse,
)
from nrplanner import (
    RelicInventory,
    discover_characters,
    decrypt_sl2,
    parse_relics,
    split_memory_dat,
)
from nrplanner.changes import multiset_diff, relevant_to_build, relic_fingerprint
from nrplanner.models import BuildChange, BuildDefinition, RelicRef, WeightGroup

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


def _compute_relic_delta(
    old_relics: list[Relic], new_profiles: list[ParsedProfileData]
) -> RelicDelta:
    old_fps = [_db_relic_fingerprint(r) for r in old_relics]
    new_fps = [_parsed_relic_fingerprint(r) for prof in new_profiles for r in prof.relics]
    added, removed = multiset_diff(old_fps, new_fps)
    return RelicDelta(added=len(added), removed=len(removed))


def _flag_affected_snapshots(
    session: Any,
    ds: Any,
    owner_id: uuid.UUID,
    old_relics: list[Relic],
    old_profiles: list[Profile],
    new_profiles: list[ParsedProfileData],
    db_builds: list[Build],
    handle_remap: dict[int, int],
) -> list[BuildChange]:
    """Cheaply flag builds whose stored arrangement may have changed.

    For each existing (build, slot) snapshot: diff that slot's relics, test
    relevance to the build, and detect pinned relics that vanished from the save.
    Affected snapshots are marked unreviewed with a coarse ``last_change``; the
    precise before/after is computed later when the build is re-optimized.
    """
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

    old_by_handle = {r.ga_handle: r for r in old_relics}
    builds_by_id = {b.id: b for b in db_builds}

    snaps = session.exec(
        select(OptimizationSnapshot).where(OptimizationSnapshot.owner_id == owner_id)
    ).all()

    affected: list[BuildChange] = []
    for snap in snaps:
        build = builds_by_id.get(snap.build_id)
        if build is None:
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
                continue  # survived the re-upload (relic still present)
            old_relic = old_by_handle.get(handle)
            if old_relic is None or old_slot_of.get(old_relic.profile_id) != snap.slot_index:
                continue
            broken.append(_relic_ref_from_db(old_relic))

        if relevant_added == 0 and relevant_removed == 0 and not broken:
            continue

        change = BuildChange(
            build_id=str(build.id),
            build_name=build.name,
            slot_index=snap.slot_index,
            status="broken_pin" if broken else "potentially_affected",
            relevant_added=relevant_added,
            pinned_removed=broken,
            cause="relics",
            reliable=False,
        )
        snap.last_change = change.model_dump(mode="json")
        snap.reviewed = False
        session.add(snap)
        affected.append(change)

    return affected


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


def _parse_save_to_profiles(
    file_bytes: bytes,
    filename: str,
    ds: Any,
    items_json: dict,
) -> tuple[str, list[ParsedProfileData]]:
    """Decrypt/split save, parse all character slots, return (platform, profiles)."""
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

        profiles: list[ParsedProfileData] = []
        for char_name, userdata_path in char_paths:
            if not char_name:
                continue
            data = userdata_path.read_bytes()
            raw_relics, _ = parse_relics(data)
            inventory = RelicInventory(raw_relics, items_json, ds)

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
                )
                for r in inventory.relics
            ]

            # Extract slot index from filename (USERDATA_00 → 0)
            slot_index = int(userdata_path.stem.rsplit("_", 1)[-1])

            profiles.append(
                ParsedProfileData(
                    slot_index=slot_index,
                    name=char_name,
                    relic_count=len(relics_data),
                    relics=relics_data,
                )
            )

    return platform, profiles


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
    platform, profiles = _parse_save_to_profiles(
        file_bytes, file.filename, ds, items_json
    )

    if current_user is None:
        # Anonymous — return data only, nothing persisted
        return UploadResponse(
            platform=platform,
            profile_count=len(profiles),
            profiles=profiles,
            persisted=False,
        )

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
        old_profiles = session.exec(
            select(Profile).where(Profile.owner_id == current_user.id)
        ).all()
        relic_delta = _compute_relic_delta(list(old_relics), profiles)
        affected_builds = _flag_affected_snapshots(
            session, ds, current_user.id, list(old_relics), list(old_profiles),
            profiles, list(db_builds), handle_remap,
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
    )
    session.add(save_upload)
    session.flush()  # get the ID before creating children

    for prof_data in profiles:
        profile = Profile(
            owner_id=current_user.id,
            save_upload_id=save_upload.id,
            slot_index=prof_data.slot_index,
            name=prof_data.name,
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
