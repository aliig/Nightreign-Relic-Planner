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
    ProfilePublic,
    ProfilesPublic,
    Profile,
    ParsedProfileData,
    ParsedRelicData,
    Relic,
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
    _Fp = tuple  # (real_id, e1, e2, e3, c1, c2, c3)

    def _fp(real_id: int, e1: int, e2: int, e3: int, c1: int, c2: int, c3: int) -> _Fp:
        return (real_id, e1, e2, e3, c1, c2, c3)

    old_fp: dict[_Fp, list[int]] = defaultdict(list)
    for r in old_relics:
        old_fp[_fp(r.real_id, r.effect_1, r.effect_2, r.effect_3,
                   r.curse_1, r.curse_2, r.curse_3)].append(r.ga_handle)

    new_fp: dict[_Fp, list[int]] = defaultdict(list)
    for prof in new_profiles:
        for r in prof.relics:
            new_fp[_fp(r.real_id, r.effect_1, r.effect_2, r.effect_3,
                       r.curse_1, r.curse_2, r.curse_3)].append(r.ga_handle)

    remap: dict[int, int] = {}
    for fp, old_handles in old_fp.items():
        new_handles = new_fp.get(fp, [])
        for old_h, new_h in zip(old_handles, new_handles):
            remap[old_h] = new_h

    return remap

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
