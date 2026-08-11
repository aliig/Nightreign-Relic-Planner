"""Write-back for in-game hero loadouts: equipped vessels + saved relic presets.

This is the write inverse of the read path in ``vessel.py`` (Sections 1-3) and a
companion to ``writer.py`` (which handles the relic inventory).  Every function
here operates on a *decrypted* USERDATA character blob and returns a new blob of
the **same total length** — the BND4 repack in ``writer.repack_sl2`` rejects any
size change, and the slot MD5 is recomputed there (do not patch it here).

Binary facts decoded from a real save (see ``vessel.py`` for the read parser):

  Preset record = 80 bytes:
    +0   u8    header (0x01 = active record)
    +1   u16   hero_type   (1-based: Wylder=1 .. Undertaker=10)
    +3   u8    counter     (recency rank: 0 == NEWEST, higher == older)
    +4   36B   name        (UTF-16-LE, up to 18 chars, NUL right-padded)
    +40  4B    padding
    +44  u32   vessel_id
    +48  6xu32 relic ga_handles (0 = empty slot)
    +72  u64   timestamp   (Windows FILETIME: 100ns ticks since 1601-01-01 UTC)

  ``counter`` is a recency rank over the ACTIVE records only (a dense permutation
  of 0..N-1, 0 == newest).  It is NOT tied to physical position: reusing a
  mid-array hole puts the newest record (counter 0) in the middle of the array.

  Section 3 is a FIXED-SIZE array of MAX_PRESETS (100) 80-byte slots. Active
  records (header 0x01) are NOT necessarily contiguous — a delete leaves a hole.
  The array never grows or shrinks, so every write op below is length-preserving
  WITHOUT moving any downstream bytes:
    * add:    write the new record into the first non-active slot in place (a
      tombstoned hole is reused before the untouched tail), and bump every
      existing record's counter by 1 (the new record is newest, counter 0).
    * delete: tombstone the record IN PLACE (header -> 0x00, hero_type -> 0,
      vessel_id -> 0xFFFFFFFF, relics + timestamp -> 0; name and counter bytes
      are left stale), and decrement the counter of every record OLDER than it
      (counter > the deleted record's).  Nothing is compacted.
    * rename/overwrite: rewrite a record's 80 bytes in place.
  Both are byte-for-byte what the game does, decoded from a real save pair
  (NR0000_pre.sl2 -> NR0000_post.sl2: two in-game deletes + one in-game add;
  see nrplanner/tests/test_vessel_game_parity.py, which replays it).
  An earlier belief that add had to grow the region (shifting downstream sections
  + a mid-blob reclaim) was a misread of the first empty slot's 0xFFFFFFFF marker
  as a section separator; that shift produced "Save data is corrupted" in-game.
  The region is pre-allocated — never grow it.

  A hero's ``cur_preset_idx`` (Section-1 block +1) is an index into the PHYSICAL
  100-slot array, NOT a position among the active records.  Proof from the save
  pair: heroes carry indices 62/76/89 while only 53 records are active, and the
  two in-game deletes left every other hero's pointer untouched.  This is why
  deletes must never compact — moving a record to another physical slot silently
  repoints whichever heroes had presets after it equipped (the reported
  "equipped loadouts shuffled after export" bug).  Dangling pointers (into an
  empty or tombstoned slot) are normal and the game tolerates them.

Equipped vessels (Sections 1-2) are reset purely in place (zeroing relic fields).
"""
import struct
import time
from dataclasses import dataclass

from nrplanner.vessel import VesselParser

# --- preset record layout (offsets within an 80-byte record) ---------------
PRESET_SIZE = 80
_OFF_HEADER = 0
_OFF_HERO = 1
_OFF_COUNTER = 3
_OFF_NAME = 4
_NAME_BYTES = 36           # 18 UTF-16 code units
_OFF_VESSEL = 44
_OFF_RELICS = 48
_N_RELICS = 6
_OFF_TIMESTAMP = 72
_HEADER_ACTIVE = 0x01
MAX_PRESETS = 100
MAX_NAME_UNITS = 18        # UTF-16 code units the game allows

# --- vessel region layout (mirror vessel.py cursor math) -------------------
_MAGIC_LEN = 20            # len of VesselParser._MAGIC match (16 + 4 bytes)
_HERO_SLOTS = 10
_VESSELS_PER_HERO = 4      # universal vessels in each Section-1 hero block
_VESSEL_REC = 28           # vessel_id (4) + relics[6] (24)
_VESSEL_RELICS = 24
_HERO_BLOCK = 4 + 4 + _VESSELS_PER_HERO * _VESSEL_REC  # 120: one Section-1 hero
_OFF_CUR_PRESET_IDX = 1    # cur_preset_idx byte within a hero block
_EMPTY_VESSEL_ID = 0xFFFFFFFF  # an empty preset slot's vessel_id marker
_NO_PRESET_IDX = 0xFF      # cur_preset_idx sentinel meaning "none equipped"

# --- blob trailer (mirror writer.py) ---------------------------------------
_TRAILER_LEN = 0x1C        # md5(16) + padding(12)

# FILETIME epoch: 1601-01-01 UTC, in 100ns ticks; Unix epoch offset.
_FILETIME_EPOCH_DELTA = 116444736000000000  # ticks between 1601 and 1970


class VesselWriteError(ValueError):
    """Base class for loadout/vessel write failures."""


class RegionNotFoundError(VesselWriteError):
    """The vessel-region magic pattern was not found in the blob."""


class PresetIndexError(VesselWriteError):
    """A preset index does not exist in the chain."""


class InvalidNameError(VesselWriteError):
    """A preset name exceeds the in-game length limit."""


class PresetCapacityError(VesselWriteError):
    """Adding would exceed the 100-preset cap, or no free space could be reclaimed."""


@dataclass
class VesselSlot:
    """One equipped-vessel record located in Section 1 or 2."""
    relics_offset: int     # absolute offset of the relics[6] field (24 bytes)
    vessel_id: int
    section: int


@dataclass
class PresetRecord:
    """One Section-3 preset record located in the chain."""
    base: int              # absolute offset of the 80-byte record
    index: int             # 0-based position in blob order
    hero_type: int
    counter: int
    name: str
    vessel_id: int
    relics: list           # 6 ga_handles
    timestamp: int


@dataclass
class ResetVesselsResult:
    vessels_cleared: int = 0
    relics_unequipped: int = 0


@dataclass
class ResetPresetsResult:
    presets_cleared: int = 0


@dataclass
class AddPresetResult:
    name: str = ""
    hero_type: int = 0
    vessel_id: int = 0
    presets_before: int = 0
    presets_after: int = 0
    slot_index: int = 0        # fixed-array slot the new record was written into
    made_current: bool = False  # whether the hero's cur_preset_idx was repointed


@dataclass
class DeletePresetResult:
    name: str = ""
    presets_before: int = 0
    presets_after: int = 0


@dataclass
class RenamePresetResult:
    old_name: str = ""
    new_name: str = ""


@dataclass
class OverwritePresetResult:
    name: str = ""
    hero_type: int = 0
    vessel_id: int = 0


# ---------------------------------------------------------------------------
# Region location & walking
# ---------------------------------------------------------------------------

def _locate_region(blob) -> int:
    """Return the absolute offset just after the vessel-region magic pattern.

    This is the cursor start used by every section walk below (== match.end()).
    """
    match = VesselParser._MAGIC.search(bytes(blob))
    if not match:
        raise RegionNotFoundError("vessel-region magic pattern not found in blob")
    return match.end()


def _walk_vessels(blob, cursor: int) -> tuple[list[VesselSlot], int]:
    """Walk Sections 1 & 2, returning every equipped-vessel relic field plus the
    cursor at the start of Section 3 (the preset chain).

    Mirrors ``VesselParser.parse`` exactly so offsets line up byte-for-byte.
    We intentionally walk the raw region (not ``hero.vessels``) so Section-2
    vessels whose hero is unmapped in the parser are still included.
    """
    slots: list[VesselSlot] = []
    # Section 1: 10 fixed hero slots, each = 8-byte header + 4 universal vessels.
    for _ in range(_HERO_SLOTS):
        cursor += 4  # hero_type(1) + cur_preset_idx(1) + pad(2)
        cursor += 4  # cur_vessel_id(4)
        for _ in range(_VESSELS_PER_HERO):
            v_id = struct.unpack_from("<I", blob, cursor)[0]
            slots.append(VesselSlot(relics_offset=cursor + 4, vessel_id=v_id, section=1))
            cursor += _VESSEL_REC
    # Section 2: hero-specific vessels until a vessel_id == 0 terminator.
    while cursor < len(blob):
        v_id = struct.unpack_from("<I", blob, cursor)[0]
        if v_id == 0:
            cursor += 4
            break
        slots.append(VesselSlot(relics_offset=cursor + 4, vessel_id=v_id, section=2))
        cursor += _VESSEL_REC
    return slots, cursor


def _walk_presets(blob, cursor: int) -> tuple[list[PresetRecord], int]:
    """Walk Section 3's fixed MAX_PRESETS-slot array, returning every active
    record (header 0x01) in physical order plus the offset one past the whole
    array (its end == the start of the downstream section).

    Active records are NOT guaranteed contiguous: an in-game delete tombstones a
    record in place (header -> 0x00, vessel_id -> 0xFFFFFFFF) WITHOUT compacting,
    so a hole can sit between active records. We therefore scan all MAX_PRESETS
    slots by header rather than stopping at the first non-active slot or the
    counter==0 terminator — either would drop every preset physically after an
    in-game-deleted one (verified against a real save whose newest presets sat
    after a mid-array tombstone). ``index`` is the active-in-physical-order
    position, matching the reader in ``vessel.py`` so the frontend edit index maps
    to the same record in both the list and every write op.
    """
    presets: list[PresetRecord] = []
    idx = 0
    start = cursor
    for _ in range(MAX_PRESETS):
        if cursor + PRESET_SIZE > len(blob):
            break
        if blob[cursor + _OFF_HEADER] == _HEADER_ACTIVE:
            hero_type = struct.unpack_from("<H", blob, cursor + _OFF_HERO)[0]
            counter = blob[cursor + _OFF_COUNTER]
            name = (bytes(blob[cursor + _OFF_NAME:cursor + _OFF_NAME + _NAME_BYTES])
                    .decode("utf-16-le", errors="ignore").rstrip("\x00"))
            vessel_id = struct.unpack_from("<I", blob, cursor + _OFF_VESSEL)[0]
            relics = list(struct.unpack_from("<6I", blob, cursor + _OFF_RELICS))
            timestamp = struct.unpack_from("<Q", blob, cursor + _OFF_TIMESTAMP)[0]
            presets.append(PresetRecord(cursor, idx, hero_type, counter, name,
                                        vessel_id, relics, timestamp))
            idx += 1
        cursor += PRESET_SIZE
    return presets, start + MAX_PRESETS * PRESET_SIZE


def parse_presets(blob) -> list[PresetRecord]:
    """Public helper: return the active preset chain in blob order."""
    cursor = _locate_region(blob)
    _, section3_start = _walk_vessels(blob, cursor)
    presets, _ = _walk_presets(blob, section3_start)
    return presets


def _slot_headers(blob, section3_start: int) -> list[int]:
    """Header byte of each of the MAX_PRESETS fixed 80-byte slots.

    Section 3 is a fixed-size array (always MAX_PRESETS slots). We classify by
    the header byte directly — header 0x01 == active, anything else == empty —
    rather than walking until counter==0 (which would undercount if an active
    record somehow followed the counter-0 terminator). Used by add_preset to find
    the active count and the first empty slot to write into.
    """
    end = section3_start + MAX_PRESETS * PRESET_SIZE
    if end > len(blob):
        raise RegionNotFoundError(
            f"preset region truncated: need {MAX_PRESETS} slots from "
            f"0x{section3_start:X}, blob ends at 0x{len(blob):X}")
    return [blob[section3_start + i * PRESET_SIZE + _OFF_HEADER]
            for i in range(MAX_PRESETS)]


def _set_cur_preset_idx(blob, region_start: int, hero_type: int, slot: int) -> bool:
    """Point a hero's cur_preset_idx (Section-1 block +1) at PHYSICAL slot ``slot``.

    Scans the 10 fixed hero blocks for the one whose first byte == hero_type.
    Returns True if set. Mirrors what the game does when you save a new loadout:
    the recipient hero's currently-equipped preset becomes the new one (verified
    on the save pair — the in-game add landed in physical slot 5 and Executor's
    pointer went 62 -> 5).
    """
    cur = region_start
    for _ in range(_HERO_SLOTS):
        if blob[cur] == hero_type:
            blob[cur + _OFF_CUR_PRESET_IDX] = slot & 0xFF
            return True
        cur += _HERO_BLOCK
    return False


def _tombstone_record(data, base: int) -> None:
    """Mark the 80-byte record at ``base`` deleted, exactly as the game does.

    Clears header, hero_type, vessel_id (-> the 0xFFFFFFFF delete marker), the
    six relic handles and the timestamp; the name and counter bytes are left
    STALE on purpose — the real in-game delete leaves them untouched, and every
    reader classifies a slot by its header byte.
    """
    data[base + _OFF_HEADER] = 0x00
    struct.pack_into("<H", data, base + _OFF_HERO, 0)
    struct.pack_into("<I", data, base + _OFF_VESSEL, _EMPTY_VESSEL_ID)
    data[base + _OFF_RELICS:base + _OFF_RELICS + _N_RELICS * 4] = b"\x00" * (_N_RELICS * 4)
    struct.pack_into("<Q", data, base + _OFF_TIMESTAMP, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_filetime() -> int:
    """Current time as a Windows FILETIME (100ns ticks since 1601-01-01 UTC)."""
    return int(time.time() * 10_000_000) + _FILETIME_EPOCH_DELTA


def _encode_name(name: str) -> bytes:
    """Encode a preset name to the fixed 36-byte UTF-16-LE field, NUL-padded."""
    raw = name.encode("utf-16-le")
    if len(raw) > _NAME_BYTES:
        raise InvalidNameError(
            f"name {name!r} is {len(raw)//2} UTF-16 units; max {MAX_NAME_UNITS}")
    return raw + b"\x00" * (_NAME_BYTES - len(raw))


def _build_record(*, hero_type: int, counter: int, name: str, vessel_id: int,
                  ga_handles, timestamp: int) -> bytes:
    rec = bytearray(PRESET_SIZE)
    rec[_OFF_HEADER] = _HEADER_ACTIVE
    struct.pack_into("<H", rec, _OFF_HERO, hero_type & 0xFFFF)
    rec[_OFF_COUNTER] = counter & 0xFF
    rec[_OFF_NAME:_OFF_NAME + _NAME_BYTES] = _encode_name(name)
    struct.pack_into("<I", rec, _OFF_VESSEL, vessel_id & 0xFFFFFFFF)
    handles = list(ga_handles)[:_N_RELICS]
    handles += [0] * (_N_RELICS - len(handles))
    for i, h in enumerate(handles):
        struct.pack_into("<I", rec, _OFF_RELICS + i * 4, h & 0xFFFFFFFF)
    struct.pack_into("<Q", rec, _OFF_TIMESTAMP, timestamp & 0xFFFFFFFFFFFFFFFF)
    return bytes(rec)


def _assert_len(new: bytes, blob, op: str) -> None:
    if len(new) != len(blob):
        raise VesselWriteError(
            f"{op}: length changed {len(blob)} -> {len(new)} (must be preserved)")


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def reset_all_vessels(blob) -> tuple[bytes, ResetVesselsResult]:
    """Unequip every relic from all equipped vessels (Sections 1 & 2).

    Zeros each vessel's relics[6] field in place; vessel ownership/pointers are
    left intact.  Length-preserving.
    """
    data = bytearray(blob)
    cursor = _locate_region(data)
    slots, _ = _walk_vessels(data, cursor)
    result = ResetVesselsResult()
    for slot in slots:
        block = data[slot.relics_offset:slot.relics_offset + _VESSEL_RELICS]
        nonzero = sum(1 for h in struct.unpack("<6I", block) if h != 0)
        if nonzero:
            data[slot.relics_offset:slot.relics_offset + _VESSEL_RELICS] = b"\x00" * _VESSEL_RELICS
            result.vessels_cleared += 1
            result.relics_unequipped += nonzero
    out = bytes(data)
    _assert_len(out, blob, "reset_all_vessels")
    return out, result


def reset_all_presets(blob) -> tuple[bytes, ResetPresetsResult]:
    """Delete every saved relic loadout preset (clear the Section-3 chain).

    Zeros the active chain in place so the parser reads zero presets, and clears
    each hero's cur_preset_idx so nothing references a removed preset.
    Length-preserving.
    """
    data = bytearray(blob)
    cursor = _locate_region(data)

    # Clear each hero's cur_preset_idx (Section-1 header byte at +1).
    h_cursor = cursor
    for _ in range(_HERO_SLOTS):
        data[h_cursor + 1] = 0
        h_cursor += 4 + 4 + _VESSELS_PER_HERO * _VESSEL_REC

    _, section3_start = _walk_vessels(data, cursor)
    presets, chain_end = _walk_presets(data, section3_start)
    if presets:
        data[section3_start:chain_end] = b"\x00" * (chain_end - section3_start)
    result = ResetPresetsResult(presets_cleared=len(presets))
    out = bytes(data)
    _assert_len(out, blob, "reset_all_presets")
    return out, result


def rename_preset(blob, index: int, new_name: str) -> tuple[bytes, RenamePresetResult]:
    """Rename the preset at ``index`` (blob order). In-place, length-preserving."""
    data = bytearray(blob)
    presets = parse_presets(data)
    if not 0 <= index < len(presets):
        raise PresetIndexError(f"preset index {index} out of range (0..{len(presets)-1})")
    p = presets[index]
    encoded = _encode_name(new_name)
    data[p.base + _OFF_NAME:p.base + _OFF_NAME + _NAME_BYTES] = encoded
    out = bytes(data)
    _assert_len(out, blob, "rename_preset")
    return out, RenamePresetResult(old_name=p.name, new_name=new_name)


def overwrite_preset(blob, index: int, *, vessel_id: int, ga_handles,
                     hero_type: int | None = None, name: str | None = None,
                     timestamp: int | None = None) -> tuple[bytes, OverwritePresetResult]:
    """Replace the relics/vessel (and optionally name/hero/timestamp) of an
    existing preset in place. Keeps header + counter (chain position). Safest
    way to push an optimizer result into an existing loadout slot.
    """
    data = bytearray(blob)
    presets = parse_presets(data)
    if not 0 <= index < len(presets):
        raise PresetIndexError(f"preset index {index} out of range (0..{len(presets)-1})")
    p = presets[index]
    rec = _build_record(
        hero_type=hero_type if hero_type is not None else p.hero_type,
        counter=p.counter,
        name=name if name is not None else p.name,
        vessel_id=vessel_id,
        ga_handles=ga_handles,
        timestamp=timestamp if timestamp is not None else now_filetime(),
    )
    data[p.base:p.base + PRESET_SIZE] = rec
    out = bytes(data)
    _assert_len(out, blob, "overwrite_preset")
    return out, OverwritePresetResult(
        name=name if name is not None else p.name,
        hero_type=hero_type if hero_type is not None else p.hero_type,
        vessel_id=vessel_id)


def add_preset(blob, *, hero_type: int, name: str, vessel_id: int, ga_handles,
               timestamp: int | None = None, update_current: bool = False
               ) -> tuple[bytes, AddPresetResult]:
    """Add a new relic loadout preset, writing into the first free slot IN PLACE.

    Section 3 is a fixed-size array of MAX_PRESETS (100) 80-byte slots. Adding a
    loadout means writing the 80-byte record into the first slot whose header is
    not 0x01 — a tombstoned hole left by an earlier delete is reused before the
    untouched tail — and bumping every existing record's counter by 1 (the new
    record is the newest, counter 0). Nothing moves and the blob length is
    unchanged — this is byte-for-byte what the game does (verified against a real
    in-game 95->96 add, and again on the save pair, where the game's own add
    landed in the hole its delete had just made).

    By default the hero's currently-equipped preset (cur_preset_idx) is left
    alone — adding a loadout should not change what's equipped. Pass
    ``update_current=True`` to point the recipient hero's cur_preset_idx at the
    new preset, exactly as the game does when you save a loadout in-game (proven
    safe: a real save with that pointer set loads fine).

    Raises PresetCapacityError only when all MAX_PRESETS slots are already active.
    The caller is responsible for ensuring every non-zero ga_handle references a
    relic that exists in this slot's inventory.
    """
    data = bytearray(blob)
    region_start = _locate_region(data)
    _, section3_start = _walk_vessels(data, region_start)

    headers = _slot_headers(data, section3_start)
    active = [i for i, h in enumerate(headers) if h == _HEADER_ACTIVE]
    n = len(active)
    if n >= MAX_PRESETS:
        raise PresetCapacityError(f"already at the {MAX_PRESETS}-loadout limit")
    if timestamp is None:
        timestamp = now_filetime()

    # First empty slot (== n when the active slots are contiguous, which holds for
    # every game-written save). The game appends here; we match it exactly.
    insertion = next(i for i, h in enumerate(headers) if h != _HEADER_ACTIVE)
    ins_off = section3_start + insertion * PRESET_SIZE

    # Bump every existing record's display counter (newest = counter 0).
    for i in active:
        off = section3_start + i * PRESET_SIZE + _OFF_COUNTER
        data[off] = (data[off] + 1) & 0xFF

    record = _build_record(hero_type=hero_type, counter=0, name=name,
                           vessel_id=vessel_id, ga_handles=ga_handles,
                           timestamp=timestamp)
    data[ins_off:ins_off + PRESET_SIZE] = record

    # cur_preset_idx is a PHYSICAL slot index, so the hero is repointed at the
    # slot we just wrote — never at the record's position among the actives.
    made_current = False
    if update_current:
        made_current = _set_cur_preset_idx(data, region_start, hero_type, insertion)

    out = bytes(data)
    _assert_len(out, blob, "add_preset")
    return out, AddPresetResult(name=name, hero_type=hero_type, vessel_id=vessel_id,
                                presets_before=n, presets_after=n + 1,
                                slot_index=insertion, made_current=made_current)


def delete_preset(blob, index: int) -> tuple[bytes, DeletePresetResult]:
    """Delete the preset at ``index`` (active-in-physical-order) by tombstoning it
    IN PLACE, exactly as the game does.

    The record's own slot is cleared (see ``_tombstone_record``) and every record
    OLDER than it (counter > its counter) has its counter decremented, keeping the
    active counters a dense 0..M-1 recency ranking. Every surviving record stays in
    its own physical slot.

    NEVER compact: a hero's cur_preset_idx is a physical slot index, so shifting
    survivors up a slot silently repoints whichever heroes had a later preset
    equipped — that is what made equipped loadouts show another character's preset
    after an export. Only the preset array region is touched; nothing outside it
    moves (shifting downstream sections corrupts the save: an earlier version that
    padded bytes before the trailer produced "Save data is corrupted" in-game).
    """
    data = bytearray(blob)
    cursor = _locate_region(data)
    _, section3_start = _walk_vessels(data, cursor)
    presets, _ = _walk_presets(data, section3_start)
    if not 0 <= index < len(presets):
        raise PresetIndexError(f"preset index {index} out of range (0..{len(presets)-1})")

    target = presets[index]
    n = len(presets)

    _tombstone_record(data, target.base)

    # Close the rank gap: everything older than the deleted record moves up one.
    for p in presets:
        if p.base != target.base and p.counter > target.counter:
            data[p.base + _OFF_COUNTER] = (p.counter - 1) & 0xFF

    out = bytes(data)
    _assert_len(out, blob, "delete_preset")
    return out, DeletePresetResult(name=target.name, presets_before=n,
                                   presets_after=n - 1)
