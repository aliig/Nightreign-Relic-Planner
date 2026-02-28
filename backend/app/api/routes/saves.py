"""Save file upload, character discovery, and relic inventory endpoints."""
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
    CharacterPublic,
    CharactersPublic,
    CharacterSlot,
    ParsedCharacterData,
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
    new_characters: list[ParsedCharacterData],
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
    for char in new_characters:
        for r in char.relics:
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


def _parse_save_to_characters(
    file_bytes: bytes,
    filename: str,
    ds: Any,
    items_json: dict,
) -> tuple[str, list[ParsedCharacterData]]:
    """Decrypt/split save, parse all character slots, return (platform, characters)."""
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

        characters: list[ParsedCharacterData] = []
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

            characters.append(
                ParsedCharacterData(
                    slot_index=slot_index,
                    name=char_name,
                    relic_count=len(relics_data),
                    relics=relics_data,
                )
            )

    return platform, characters


@router.post("/upload", response_model=UploadResponse)
async def upload_save(
    file: UploadFile,
    ds: GameDataDep,
    current_user: OptionalUser,
    session: SessionDep,
) -> UploadResponse:
    """Upload a .sl2 (PC) or memory.dat (PS4) save file.

    - **Anonymous users**: returns parsed characters + relics (nothing persisted).
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
    platform, characters = _parse_save_to_characters(
        file_bytes, file.filename, ds, items_json
    )

    if current_user is None:
        # Anonymous — return data only, nothing persisted
        return UploadResponse(
            platform=platform,
            character_count=len(characters),
            characters=characters,
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
    handle_remap = _compute_handle_remap(list(old_relics), characters)
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
        character_count=len(characters),
    )
    session.add(save_upload)
    session.flush()  # get the ID before creating children

    for char_data in characters:
        char_slot = CharacterSlot(
            owner_id=current_user.id,
            save_upload_id=save_upload.id,
            slot_index=char_data.slot_index,
            name=char_data.name,
        )
        session.add(char_slot)
        session.flush()

        for r in char_data.relics:
            session.add(Relic(
                owner_id=current_user.id,
                character_id=char_slot.id,
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
        char_data.id = char_slot.id

    session.commit()

    return UploadResponse(
        platform=platform,
        character_count=len(characters),
        characters=characters,
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

    characters = session.exec(
        select(CharacterSlot)
        .where(CharacterSlot.save_upload_id == upload.id)
        .order_by(col(CharacterSlot.slot_index))
    ).all()

    return SaveStatusPublic(
        id=upload.id,
        platform=upload.platform,
        uploaded_at=upload.uploaded_at,
        character_count=upload.character_count,
        character_names=[c.name for c in characters],
    )


@router.get("/characters", response_model=CharactersPublic)
def list_characters(session: SessionDep, current_user: CurrentUser) -> CharactersPublic:
    """List all saved character slots for the current user."""
    statement = (
        select(CharacterSlot)
        .where(CharacterSlot.owner_id == current_user.id)
        .order_by(col(CharacterSlot.slot_index))
    )
    chars = session.exec(statement).all()
    return CharactersPublic(
        data=[CharacterPublic.model_validate(c) for c in chars],
        count=len(chars),
    )


@router.get("/characters/{character_id}/relics", response_model=RelicsPublic)
def get_character_relics(
    character_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> RelicsPublic:
    """Get all relics for a saved character slot."""
    char_slot = session.get(CharacterSlot, character_id)
    if not char_slot:
        raise HTTPException(status_code=404, detail="Character not found")
    if char_slot.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    statement = (
        select(Relic)
        .where(Relic.character_id == character_id)
        .order_by(col(Relic.name))
    )
    relics = session.exec(statement).all()
    return RelicsPublic(
        data=[RelicPublic.model_validate(r) for r in relics],
        count=len(relics),
    )
