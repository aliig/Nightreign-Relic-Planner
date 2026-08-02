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

Adding beyond the ghost supply mints into the arena's virgin tail: the last
records of the 5120-slot ItemState arena are untouched 8-byte empties, and
converting one to an 80-byte relic record grows the arena by 72 bytes,
shifting everything after it. This mirrors the game's own acquisition write:
each USERDATA slot is a fixed 1,048,608-byte envelope whose serialized data
is items_end-relative, and diffing one account's real saves (2026-07-15 /
07-28 / 08-01 snapshots) showed the game itself grew the arena +2,320/+432/
+14,016 bytes between saves, shifting the whole tail and letting the final
bytes fall off; the last ~380 KiB of the envelope churns 100% between saves
(stale serializer residue, never read back). ``_MAX_TAIL_SHIFT`` keeps our
shift orders of magnitude inside that observed slack.
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
    minted_handles: list[int] = field(default_factory=list)  # subset minted into the virgin tail
    tail_shift: int = 0  # bytes the post-arena tail moved (72 per minted relic)


class AddCapacityError(ValueError):
    """Not enough ghost records + virgin tail slots / free ItemEntry rows for the add."""


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

# --- arena-tail minting ------------------------------------------------------
_EMPTY_RECORD_SIZE = 8  # untouched arena slot: gaitem_handle(4) + item_id(4)
# Canonical empty-slot encoding: handle 0, item_id 0xFFFFFFFF. Observed
# uniformly on every dead slot (4,484 + 3,821 records across two accounts'
# saves, 2026-08-01 arena survey) — never zero-padded, never stale ids.
_EMPTY_SLOT_SENTINEL = b"\x00\x00\x00\x00\xff\xff\xff\xff"
_MINT_GROWTH = _RELIC_STATE_SIZE - _EMPTY_RECORD_SIZE  # 72 bytes per minted relic
# Per-export tail-shift budget. The envelope's final ~380 KiB is stale
# serializer residue (churns 100% between real game saves; snapshot diff of
# one account's 2026-07-15/07-28/08-01 saves), and the game itself shifted
# the tail +14,016 B in a single save. 64 KiB (= 910 relics) stays far inside
# that, and the 1950 storage cap / free ItemEntry rows bind first in practice.
_MAX_TAIL_SHIFT = 0x10000


def _virgin_tail_slots(data, items) -> list:
    """Trailing arena records that are canonical empty slots (see sentinel).

    Returned in arena order; these are always the LAST records of the arena.
    Only this contiguous run is mintable — converting the tail-most empties
    keeps every live record's offset unchanged and needs a single tail move.
    """
    run = []
    for item in reversed(items):
        if item.gaitem_handle != 0 or item.size != _EMPTY_RECORD_SIZE:
            break
        if data[item.offset:item.offset + _EMPTY_RECORD_SIZE] != _EMPTY_SLOT_SENTINEL:
            break
        run.append(item)
    run.reverse()
    return run


def _fresh_relic_handles(items, count: int) -> list[int]:
    """Mint unused relic ga_handles above every existing relic-type handle.

    Observed live handles are ITEM_TYPE_RELIC | low with low sparse around
    0x800054..0x8009CA, and the game renumbers every ga_handle on its next
    save (snapshot diff 2026-08-01: the max live handle DECREASED while the
    relic count grew) — so within-save uniqueness is the only hard
    requirement, which max+1..max+count satisfies.
    """
    lows = [it.gaitem_handle & 0x0FFFFFFF for it in items
            if (it.gaitem_handle & 0xF0000000) == ITEM_TYPE_RELIC]
    base = max(lows, default=0)
    if base + count > 0x0FFFFFFF:
        raise AddCapacityError("relic ga_handle space exhausted")
    return [ITEM_TYPE_RELIC | (base + 1 + i) for i in range(count)]


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
    """Max relics addable to this character blob in one ``add_relics`` pass.

    Two mechanisms, applied in order: resurrecting ghost relic records
    (in-place) and minting into the arena's virgin tail (72-byte grow per
    relic, budgeted by ``_MAX_TAIL_SHIFT``). Every added relic also needs a
    free ItemEntry row. Read-only. The in-game 1950 storage cap is the
    caller's check — this is the blob's structural limit only.
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
    mintable = min(len(_virgin_tail_slots(data, items)),
                   _MAX_TAIL_SHIFT // _MINT_GROWTH)

    count_off = items_end + _ENTRY_COUNT_REL_OFFSET
    entries_start = count_off + 4
    free_slots = 0
    for i in range(_ITEM_ENTRY_SLOT_COUNT):
        off = entries_start + i * _ITEM_ENTRY_SIZE
        if off + _ITEM_ENTRY_SIZE > len(data):
            break
        if struct.unpack_from("<I", data, off)[0] == 0:
            free_slots += 1
    return min(ghosts + mintable, free_slots)


def add_relics(blob: bytes, records) -> tuple[bytes, AddResult]:
    """Add relics: resurrect ghost ItemState records, then mint into the tail.

    ``records`` is an iterable of raw 80-byte relic ItemState records (e.g.
    sliced from another save via RawRelic.offset/size). Unknown fields
    (durability, unk_1, padding, unk_2) are preserved byte-for-byte; only the
    leading ga_handle is rewritten.

    Mechanism 1 — ghost resurrection (preferred, in-game validated
    2026-07-14): a full 80-byte relic record left in Layer 1 (ItemState) with
    no Layer 2 (ItemEntry) row. The new relic overwrites it in place, keeping
    the ghost's game-allocated ga_handle. Zero bytes move.

    Mechanism 2 — virgin-tail mint: beyond the ghost supply, each new relic
    converts one of the arena's trailing all-zero 8-byte empty records into
    an 80-byte relic record and shifts the post-arena tail right by 72 bytes
    — the same items_end-relative grow the game performs on every item
    acquisition (see the module docstring for the real-save snapshot
    evidence). The trailing 72 bytes per mint fall off the end of the
    envelope's stale-residue region; total blob length never changes. Minted
    records get fresh unique handles, renumbered by the game on next save.

    Raises AddCapacityError beyond capacity (nothing written), and ValueError
    on a malformed input record.
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

    n_ghost = min(len(records), len(ghosts))
    n_mint = len(records) - n_ghost
    virgin = _virgin_tail_slots(data, items)
    mint_budget = min(len(virgin), _MAX_TAIL_SHIFT // _MINT_GROWTH)
    if n_mint > mint_budget:
        raise AddCapacityError(
            f"need {len(records)} addable relic slots, save has "
            f"{len(ghosts)} ghost records + {mint_budget} mintable tail slots")

    # --- ItemEntry table: free row indices + acquisition_id watermark -------
    # Scanned pre-shift; the table moves as one block, so row INDICES stay
    # valid and row offsets are recomputed from the post-shift items_end.
    count_off = items_end + _ENTRY_COUNT_REL_OFFSET
    entries_start = count_off + 4
    free_rows: list[int] = []
    max_acq = 0
    for i in range(_ITEM_ENTRY_SLOT_COUNT):
        off = entries_start + i * _ITEM_ENTRY_SIZE
        if off + _ITEM_ENTRY_SIZE > len(data):
            break
        handle, _, acq = struct.unpack_from("<III", data, off)
        if handle == 0:
            free_rows.append(i)
        else:
            max_acq = max(max_acq, acq)
    if len(records) > len(free_rows):
        raise AddCapacityError(
            f"need {len(records)} free ItemEntry slots, save has {len(free_rows)}")

    # --- mechanism 2: grow the arena over the virgin run's FIRST slots ------
    # Consuming the head of the run keeps the surviving empties tail-most, so
    # a minted blob stays mintable on later passes.
    shift = n_mint * _MINT_GROWTH
    minted_handles: list[int] = []
    if n_mint:
        minted_handles = _fresh_relic_handles(items, n_mint)
        mint_zone = virgin[0].offset
        move_src = mint_zone + n_mint * _EMPTY_RECORD_SIZE
        body_end = len(data) - _TRAILER_LEN
        # Shift everything after the consumed empties right by `shift`; the
        # trailing `shift` bytes of the body (stale residue) fall away. The
        # RHS slice copies before assignment, so the overlapping move is safe.
        data[mint_zone + n_mint * _RELIC_STATE_SIZE:body_end] = \
            data[move_src:body_end - shift]
        # The minted records tile [mint_zone, mint_zone + 80*n_mint) exactly
        # over the consumed empties (8*n_mint bytes) plus the opened gap.
        for i in range(n_mint):
            rec = bytearray(records[n_ghost + i])
            struct.pack_into("<I", rec, 0, minted_handles[i])
            off = mint_zone + i * _RELIC_STATE_SIZE
            data[off:off + _RELIC_STATE_SIZE] = rec
    items_end += shift
    result.minted_handles = minted_handles
    result.tail_shift = shift

    # --- mechanism 1: overwrite ghosts in place (offsets precede the tail) --
    for i in range(n_ghost):
        ghost = ghosts[i]
        patched = bytearray(records[i])
        struct.pack_into("<I", patched, 0, ghost.gaitem_handle)
        data[ghost.offset:ghost.offset + _RELIC_STATE_SIZE] = patched

    result.added_handles = [g.gaitem_handle for g in ghosts[:n_ghost]] + minted_handles

    # --- activate every added relic with a fresh ItemEntry row --------------
    count_off = items_end + _ENTRY_COUNT_REL_OFFSET
    entries_start = count_off + 4
    for i, handle in enumerate(result.added_handles):
        off = entries_start + free_rows[i] * _ITEM_ENTRY_SIZE
        # ItemEntry row: amount=1, next acquisition ordinal, not favorite/new.
        struct.pack_into("<IIIBB", data, off, handle, 1, max_acq + 1 + i, 0, 0)

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
