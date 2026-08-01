"""
Save file write-back: delete/add relics, credit Murk, repack/re-encrypt a .sl2.

This is the inverse of the read path in ``save.py``. The binary layout and
offset math here are deliberately kept consistent with ``save.py`` (which is
verbatim from the original tool) — do not refactor the offsets.

Write recipe (per character USERDATA blob, ``items_end`` = return of
``parse_relics``):

  * player name   at ``items_end + 0x94``
  * Murks (u32)   at ``items_end + 0x94 + 52``
  * ItemEntry cnt at ``items_end + 0x94 + 0x5B8``  (u32, then the entry table)
  * ItemEntry recs start at ``... + 4`` — 3065 x 14 bytes
  * slot MD5      ``md5(blob[4 : len-28])`` written at ``blob[len-28 : len-12]``
  * encryption    AES-128-CBC, key ``_DS2_KEY``, fresh 16-byte IV per entry

Selling a relic must preserve the blob's total byte length (the BND4 repack
rejects size changes). We tombstone in place: the relic's 80-byte ItemState
record is left byte-for-byte intact and only its 14-byte ItemEntry row is
zeroed (entry count decremented). The intact record becomes a "ghost" — a
Layer-1 record with no Layer-2 row, the exact shape the game itself leaves
after a run session (ghost resurrection in-game validated 2026-07-14) — so
``add_relics`` can reuse it: every sell raises ``add_capacity`` by one.
"""
import hashlib
import os
import struct
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from nrplanner.constants import EMPTY_EFFECT, ITEM_TYPE_RELIC
from nrplanner.save import (
    _DS2_KEY,
    _ITEM_ENTRY_SIZE,
    _ITEM_ENTRY_SLOT_COUNT,
    _IV_SIZE,
    _parse_active_handles,
    _parse_items,
)

# --- blob layout constants (relative to items_end) -------------------------
_NAME_REL_OFFSET = 0x94               # player name region
_MURKS_REL_OFFSET = 0x94 + 52         # Murks (u32 little-endian)
_ENTRY_COUNT_REL_OFFSET = 0x94 + 0x5B8  # ItemEntry count (u32), then records
_TRAILER_LEN = 0x1C                   # 28-byte trailer: md5(16) + padding(12)
_U32_MAX = 0xFFFFFFFF

# --- BND4 container constants (mirror save.decrypt_sl2) --------------------
_BND4_HEADER_LEN = 64
_BND4_ENTRY_HEADER_LEN = 32

# --- in-game relic sell prices (Murk), keyed by # of properties (effects) --
# Deep relics sell for double. Source: in-game values supplied by the user.
_SELL_BY_EFFECT_COUNT = {1: 150, 2: 350, 3: 550}


@dataclass
class FavoriteResult:
    """Outcome of a set_favorites call."""
    changed_handles: list[int] = field(default_factory=list)
    not_found_handles: list[int] = field(default_factory=list)


@dataclass
class AddResult:
    """Outcome of an add_relics call."""
    added_handles: list[int] = field(default_factory=list)  # per input record
    entry_count_before: int = 0
    entry_count_after: int = 0
    ghosts_available: int = 0


class AddCapacityError(ValueError):
    """Not enough reusable ghost records / free ItemEntry slots for the add."""


@dataclass
class DeleteResult:
    """Outcome of a delete_relics call."""
    removed_handles: list[int] = field(default_factory=list)
    not_found_handles: list[int] = field(default_factory=list)
    entry_count_before: int = 0
    entry_count_after: int = 0
    murks_before: int = 0
    murks_after: int = 0


def sell_value(effect_count: int, is_deep: bool) -> int:
    """Murk credited for selling one relic.

    effect_count is the number of properties (1-3); deep relics sell for double.
    """
    clamped = min(max(effect_count, 1), 3)
    base = _SELL_BY_EFFECT_COUNT[clamped]
    return base * (2 if is_deep else 1)


def read_murks(data: bytes, items_end_offset: int) -> int:
    """Return the player's current Murk (currency) for this character blob.

    Returns 0 if the blob is too short to contain the field (e.g. test stubs).
    """
    off = items_end_offset + _MURKS_REL_OFFSET
    if off + 4 > len(data):
        return 0
    return struct.unpack_from("<I", data, off)[0]


def read_favorite_handles(data: bytes, items_end_offset: int) -> set[int]:
    """Return ga_handles of relics bookmarked (favorited) in-game.

    Reads the ``is_favorite`` byte (+0x0C) of each ItemEntry record alongside
    its ga_handle. Mirrors the table location used by _parse_active_handles.
    """
    table_offset = items_end_offset + _ENTRY_COUNT_REL_OFFSET
    entries_start = table_offset + 4  # skip the stored count field

    favorites: set[int] = set()
    for i in range(_ITEM_ENTRY_SLOT_COUNT):
        off = entries_start + i * _ITEM_ENTRY_SIZE
        if off + _ITEM_ENTRY_SIZE > len(data):
            break
        handle = struct.unpack_from("<I", data, off)[0]
        if handle == 0:
            continue
        is_favorite = data[off + 0x0C]
        if is_favorite != 0:
            favorites.add(handle)
    return favorites


def read_acquisition_ids(data: bytes, items_end_offset: int) -> dict[int, int]:
    """Return ga_handle -> acquisition_id for every owned item.

    acquisition_id (+0x08 in each ItemEntry record) is a global counter the
    game increments for every item ever acquired, so a higher value means the
    item was acquired more recently. Mirrors the table location used by
    _parse_active_handles. Note: ItemState file-position order does NOT track
    acquisition order — the game reuses freed slots.
    """
    table_offset = items_end_offset + _ENTRY_COUNT_REL_OFFSET
    entries_start = table_offset + 4  # skip the stored count field

    acquisition_ids: dict[int, int] = {}
    for i in range(_ITEM_ENTRY_SLOT_COUNT):
        off = entries_start + i * _ITEM_ENTRY_SIZE
        if off + _ITEM_ENTRY_SIZE > len(data):
            break
        handle, _, acq = struct.unpack_from("<III", data, off)
        if handle != 0:
            acquisition_ids[handle] = acq
    return acquisition_ids


def set_favorites(blob: bytes, changes: dict[int, bool]) -> tuple[bytes, FavoriteResult]:
    """Bookmark/unbookmark relics by toggling the ItemEntry is_favorite byte.

    Args:
        blob: a decrypted USERDATA character blob.
        changes: map of ga_handle -> desired favorite state (True=bookmark).

    Returns:
        (new_blob, FavoriteResult). Length-preserving (flips one byte per record).
    """
    data = bytearray(blob)
    _, items_end = _parse_items(data, start_offset=0x14, slot_count=5120)
    entries_start = items_end + _ENTRY_COUNT_REL_OFFSET + 4

    result = FavoriteResult()
    pending = dict(changes)
    for i in range(_ITEM_ENTRY_SLOT_COUNT):
        off = entries_start + i * _ITEM_ENTRY_SIZE
        if off + _ITEM_ENTRY_SIZE > len(data):
            break
        handle = struct.unpack_from("<I", data, off)[0]
        if handle != 0 and handle in pending:
            data[off + 0x0C] = 1 if pending.pop(handle) else 0
            result.changed_handles.append(handle)
    result.changed_handles.sort()
    result.not_found_handles = sorted(pending)
    return bytes(data), result


def patch_slot_checksum(blob: bytearray) -> None:
    """Recompute and patch the per-slot MD5 checksum in place.

    checksum = md5(blob[4 : len-28]); written into blob[len-28 : len-12].
    """
    checksum_end = len(blob) - _TRAILER_LEN  # md5 occupies [len-28 : len-12]
    digest = hashlib.md5(bytes(blob[4:checksum_end]), usedforsecurity=False).digest()
    blob[checksum_end:checksum_end + 16] = digest


def delete_relics(blob: bytes, ga_handles, murk_credit: int = 0) -> tuple[bytes, DeleteResult]:
    """Sell relics by ga_handle and credit Murk, preserving total byte length.

    Tombstones in place: the relic's ItemState record is left intact and only
    its ItemEntry row is removed (see the module docstring). The record becomes
    a resurrectable ghost, so each sell raises ``add_capacity`` by one.

    Args:
        blob: a decrypted USERDATA character blob.
        ga_handles: iterable of relic ga_handles to remove.
        murk_credit: Murk to add to the player's currency (sum of sell values).

    Returns:
        (new_blob, DeleteResult). new_blob has the same length as blob.
    """
    targets = set(ga_handles)
    data = bytearray(blob)
    result = DeleteResult()

    items, items_end = _parse_items(data, start_offset=0x14, slot_count=5120)

    # Only relics are sellable; non-relic and absent targets land in not_found.
    matched = {
        item.gaitem_handle for item in items
        if item.gaitem_handle in targets
        and (item.gaitem_handle & 0xF0000000) == ITEM_TYPE_RELIC
    }
    result.not_found_handles = sorted(targets - matched)

    # Murks
    murks_off = items_end + _MURKS_REL_OFFSET
    murks_before = struct.unpack_from("<I", data, murks_off)[0]
    murks_after = min(murks_before + max(murk_credit, 0), _U32_MAX)
    struct.pack_into("<I", data, murks_off, murks_after)
    result.murks_before = murks_before
    result.murks_after = murks_after

    # ItemEntry table: zero matching records, decrement count. The ItemState
    # records are deliberately untouched — that is what makes them ghosts.
    count_off = items_end + _ENTRY_COUNT_REL_OFFSET
    entries_start = count_off + 4
    entry_count_before = struct.unpack_from("<I", data, count_off)[0]
    removed = 0
    for i in range(_ITEM_ENTRY_SLOT_COUNT):
        off = entries_start + i * _ITEM_ENTRY_SIZE
        if off + _ITEM_ENTRY_SIZE > len(data):
            break
        handle = struct.unpack_from("<I", data, off)[0]
        if handle in matched:
            data[off:off + _ITEM_ENTRY_SIZE] = b"\x00" * _ITEM_ENTRY_SIZE
            removed += 1
    entry_count_after = max(entry_count_before - removed, 0)
    struct.pack_into("<I", data, count_off, entry_count_after)
    result.entry_count_before = entry_count_before
    result.entry_count_after = entry_count_after

    result.removed_handles = sorted(matched)
    return bytes(data), result


_RELIC_STATE_SIZE = 80  # full relic ItemState record (see save.Item.from_bytes)


def build_relic_record(real_id: int, effects, curses, template: bytes) -> bytes:
    """Build an 80-byte relic ItemState record from a spec + a real donor template.

    Every byte except item_id (0x04), the three effects (0x10/0x14/0x18) and the three
    curses (0x38/0x3C/0x40) is copied verbatim from ``template`` — a real 80-byte relic
    record taken from a save of the same game/patch. That preserves durability, the
    unknown fields, the 28-byte padding, and the 8 trailing bytes (0x48..0x4F) that
    ``save.Item.from_bytes`` never parses. The leading ga_handle (0x00) is left as-is; it
    is a placeholder that ``add_relics`` overwrites with a ghost's game-allocated handle.

    Args:
        real_id: the EquipParamAntique row id; stored as ``real_id + 0x80000000``.
        effects: up to 3 primary effect IDs (EMPTY_EFFECT / omitted = empty slot).
        curses:  up to 3 curse effect IDs (EMPTY_EFFECT / omitted = empty slot).
        template: a real 80-byte relic ItemState record to copy constant fields from.

    Returns an 80-byte record ready to hand to ``add_relics``.
    """
    if len(template) != _RELIC_STATE_SIZE:
        raise ValueError(
            f"template is {len(template)} bytes; expected {_RELIC_STATE_SIZE}")
    eff = (list(effects) + [EMPTY_EFFECT] * 3)[:3]
    cur = (list(curses) + [EMPTY_EFFECT] * 3)[:3]

    rec = bytearray(template)
    struct.pack_into("<I", rec, 0x04, (real_id + 0x80000000) & _U32_MAX)
    struct.pack_into("<III", rec, 0x10, *(e & _U32_MAX for e in eff))
    struct.pack_into("<III", rec, 0x38, *(c & _U32_MAX for c in cur))
    return bytes(rec)


def adjust_murks(blob: bytes, delta: int) -> tuple[bytes, int, int]:
    """Apply a signed Murk delta to a character blob (clamped to [0, U32_MAX]).

    Used by relic purchasing: negative for buys, positive for sell refunds. Mirrors the
    Murk field location/handling in delete_relics. Returns (new_blob, before, after);
    length-preserving.
    """
    data = bytearray(blob)
    _, items_end = _parse_items(data, start_offset=0x14, slot_count=5120)
    off = items_end + _MURKS_REL_OFFSET
    before = struct.unpack_from("<I", data, off)[0]
    after = max(0, min(before + delta, _U32_MAX))
    struct.pack_into("<I", data, off, after)
    return bytes(data), before, after


def add_capacity(blob: bytes) -> int:
    """Max relics addable to this character blob via ``add_relics``, without growing it.

    Equals ``min(#reusable ghost relic records, #free ItemEntry slots)``. ``add_relics``
    raises AddCapacityError for any add beyond this. Read-only.
    """
    data = bytearray(blob)
    items, items_end = _parse_items(data, start_offset=0x14, slot_count=5120)
    active = _parse_active_handles(data, items_end)
    ghosts = sum(
        1 for item in items
        if (item.gaitem_handle & 0xF0000000) == ITEM_TYPE_RELIC
        and item.size == _RELIC_STATE_SIZE
        and item.gaitem_handle not in active
    )

    count_off = items_end + _ENTRY_COUNT_REL_OFFSET
    entries_start = count_off + 4
    free_slots = 0
    for i in range(_ITEM_ENTRY_SLOT_COUNT):
        off = entries_start + i * _ITEM_ENTRY_SIZE
        if off + _ITEM_ENTRY_SIZE > len(data):
            break
        if struct.unpack_from("<I", data, off)[0] == 0:
            free_slots += 1
    return min(ghosts, free_slots)


def add_relics(blob: bytes, records) -> tuple[bytes, AddResult]:
    """Add relics by resurrecting ghost ItemState records. Fully in-place.

    ``records`` is an iterable of raw 80-byte relic ItemState records (e.g.
    sliced from another save via RawRelic.offset/size). Unknown fields
    (durability, unk_1, padding, unk_2) are preserved byte-for-byte; only the
    leading ga_handle is rewritten.

    Why ghosts: the blob has no spare bytes before its trailer, so the
    ItemState region cannot grow. Instead each new relic overwrites a *ghost*
    — a full 80-byte relic record left in Layer 1 (ItemState) with no Layer 2
    (ItemEntry) row, i.e. an abandoned run-session item. We keep the ghost's
    game-allocated ga_handle (so the game's handle counter is already past it)
    and activate it by writing a fresh ItemEntry row. Zero bytes move — this
    is strictly more conservative than delete_relics, which shifts the tail.

    Raises AddCapacityError if there are fewer ghosts (or free ItemEntry
    slots) than records, and ValueError on a malformed input record.
    """
    records = list(records)
    for i, rec in enumerate(records):
        if len(rec) != _RELIC_STATE_SIZE:
            raise ValueError(
                f"record #{i} is {len(rec)} bytes; expected {_RELIC_STATE_SIZE}")
        handle = struct.unpack_from("<I", rec, 0)[0]
        if (handle & 0xF0000000) != ITEM_TYPE_RELIC:
            raise ValueError(f"record #{i} is not a relic record (handle 0x{handle:08X})")

    data = bytearray(blob)
    result = AddResult()

    items, items_end = _parse_items(data, start_offset=0x14, slot_count=5120)
    active = _parse_active_handles(data, items_end)

    ghosts = [
        item for item in items
        if (item.gaitem_handle & 0xF0000000) == ITEM_TYPE_RELIC
        and item.size == _RELIC_STATE_SIZE
        and item.gaitem_handle not in active
    ]
    result.ghosts_available = len(ghosts)
    if len(records) > len(ghosts):
        raise AddCapacityError(
            f"need {len(records)} reusable ghost records, save has {len(ghosts)}")

    # --- ItemEntry table: free slots + acquisition_id watermark -------------
    count_off = items_end + _ENTRY_COUNT_REL_OFFSET
    entries_start = count_off + 4
    free_slots: list[int] = []
    max_acq = 0
    for i in range(_ITEM_ENTRY_SLOT_COUNT):
        off = entries_start + i * _ITEM_ENTRY_SIZE
        if off + _ITEM_ENTRY_SIZE > len(data):
            break
        handle, _, acq = struct.unpack_from("<III", data, off)
        if handle == 0:
            free_slots.append(off)
        else:
            max_acq = max(max_acq, acq)
    if len(records) > len(free_slots):
        raise AddCapacityError(
            f"need {len(records)} free ItemEntry slots, save has {len(free_slots)}")

    # --- overwrite ghosts + activate, one record at a time ------------------
    for i, rec in enumerate(records):
        ghost = ghosts[i]
        patched = bytearray(rec)
        struct.pack_into("<I", patched, 0, ghost.gaitem_handle)
        data[ghost.offset:ghost.offset + _RELIC_STATE_SIZE] = patched

        # ItemEntry row: amount=1, next acquisition ordinal, not favorite/new.
        struct.pack_into("<IIIBB", data, free_slots[i],
                         ghost.gaitem_handle, 1, max_acq + 1 + i, 0, 0)
        result.added_handles.append(ghost.gaitem_handle)

    entry_count_before = struct.unpack_from("<I", data, count_off)[0]
    entry_count_after = entry_count_before + len(records)
    struct.pack_into("<I", data, count_off, entry_count_after)
    result.entry_count_before = entry_count_before
    result.entry_count_after = entry_count_after

    assert len(data) == len(blob), f"length changed: {len(blob)} -> {len(data)}"
    return bytes(data), result


def _aes_encrypt(plaintext: bytes, iv: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(_DS2_KEY), modes.CBC(iv)).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def repack_sl2(raw: bytes, modified_blobs: dict[int, bytes]) -> bytes:
    """Repackage a BND4 .sl2, re-encrypting only the modified entries.

    Args:
        raw: original full .sl2 file bytes.
        modified_blobs: map of BND4 entry index -> modified decrypted blob.
            Each blob must equal the original decrypted size (size - IV_SIZE).

    Returns:
        New .sl2 file bytes. Entries not in modified_blobs are byte-identical
        to the original. The embedded Steam ID is left unchanged.
    """
    if raw[:4] != b"BND4":
        raise ValueError("Not a valid BND4 save file (missing BND4 header).")

    out = bytearray(raw)
    num_entries = struct.unpack("<i", raw[12:16])[0]

    for index, blob in modified_blobs.items():
        if not 0 <= index < num_entries:
            raise ValueError(f"BND4 entry index {index} out of range (0..{num_entries - 1}).")
        pos = _BND4_HEADER_LEN + _BND4_ENTRY_HEADER_LEN * index
        header = raw[pos:pos + _BND4_ENTRY_HEADER_LEN]
        if header[:8] != b"\x40\x00\x00\x00\xff\xff\xff\xff":
            raise ValueError(f"BND4 entry #{index} unexpected magic.")
        size = struct.unpack("<i", header[8:12])[0]
        data_offset = struct.unpack("<i", header[16:20])[0]

        expected = size - _IV_SIZE
        if len(blob) != expected:
            raise ValueError(
                f"Modified entry #{index} size {len(blob)} != original {expected}."
            )

        patched = bytearray(blob)
        patch_slot_checksum(patched)

        iv = os.urandom(_IV_SIZE)
        encrypted = iv + _aes_encrypt(bytes(patched), iv)
        if len(encrypted) != size:
            raise ValueError(
                f"Re-encrypted entry #{index} size {len(encrypted)} != original {size}."
            )
        out[data_offset:data_offset + size] = encrypted

    return bytes(out)
