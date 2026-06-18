"""
Save file write-back: delete relics, credit Murk, repack/re-encrypt a .sl2.

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

Deleting a relic must preserve the blob's total byte length (the BND4 repack
rejects size changes). We mirror the proven upstream removal: empty the relic's
ItemState record (relic ~80 bytes -> 8-byte empty record), zero its 14-byte
ItemEntry, decrement the entry count, then re-insert the freed bytes as zero
padding immediately before the final 28-byte trailer.
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
    _parse_items,
)

# --- blob layout constants (relative to items_end) -------------------------
_NAME_REL_OFFSET = 0x94               # player name region
_MURKS_REL_OFFSET = 0x94 + 52         # Murks (u32 little-endian)
_ENTRY_COUNT_REL_OFFSET = 0x94 + 0x5B8  # ItemEntry count (u32), then records
_TRAILER_LEN = 0x1C                   # 28-byte trailer: md5(16) + padding(12)
_EMPTY_STATE = b"\x00" * 8            # empty ItemState record
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
class DeleteResult:
    """Outcome of a delete_relics call."""
    removed_handles: list[int] = field(default_factory=list)
    not_found_handles: list[int] = field(default_factory=list)
    entry_count_before: int = 0
    entry_count_after: int = 0
    murks_before: int = 0
    murks_after: int = 0
    bytes_freed: int = 0


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


def relic_effect_count(item) -> int:
    """Number of properties (non-empty effects) on a parsed Item."""
    effects = (item.effect_1, item.effect_2, item.effect_3)
    return sum(1 for e in effects if e not in (EMPTY_EFFECT, 0))


def patch_slot_checksum(blob: bytearray) -> None:
    """Recompute and patch the per-slot MD5 checksum in place.

    checksum = md5(blob[4 : len-28]); written into blob[len-28 : len-12].
    """
    checksum_end = len(blob) - _TRAILER_LEN  # md5 occupies [len-28 : len-12]
    digest = hashlib.md5(bytes(blob[4:checksum_end]), usedforsecurity=False).digest()
    blob[checksum_end:checksum_end + 16] = digest


def delete_relics(blob: bytes, ga_handles, murk_credit: int = 0) -> tuple[bytes, DeleteResult]:
    """Delete relics by ga_handle and credit Murk, preserving total byte length.

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

    # --- rebuild the ItemState region, emptying targeted relic records ------
    region = bytearray()
    freed = 0
    matched: set[int] = set()
    for item in items:
        original = data[item.offset:item.offset + item.size]
        if item.gaitem_handle in targets:
            if (item.gaitem_handle & 0xF0000000) != ITEM_TYPE_RELIC:
                # Only relics are sellable; leave non-relics untouched.
                region += original
                continue
            region += _EMPTY_STATE
            freed += item.size - len(_EMPTY_STATE)
            matched.add(item.gaitem_handle)
        else:
            region += original

    result.not_found_handles = sorted(targets - matched)
    result.bytes_freed = freed

    # --- operate on the tail (everything from items_end onward) -------------
    tail = bytearray(data[items_end:])

    # Murks
    murks_off = _MURKS_REL_OFFSET
    murks_before = struct.unpack_from("<I", tail, murks_off)[0]
    murks_after = min(murks_before + max(murk_credit, 0), _U32_MAX)
    struct.pack_into("<I", tail, murks_off, murks_after)
    result.murks_before = murks_before
    result.murks_after = murks_after

    # ItemEntry table: zero matching records, decrement count
    count_off = _ENTRY_COUNT_REL_OFFSET
    entries_start = count_off + 4
    entry_count_before = struct.unpack_from("<I", tail, count_off)[0]
    removed = 0
    for i in range(_ITEM_ENTRY_SLOT_COUNT):
        off = entries_start + i * _ITEM_ENTRY_SIZE
        if off + _ITEM_ENTRY_SIZE > len(tail):
            break
        handle = struct.unpack_from("<I", tail, off)[0]
        if handle in matched:
            tail[off:off + _ITEM_ENTRY_SIZE] = b"\x00" * _ITEM_ENTRY_SIZE
            removed += 1
    entry_count_after = max(entry_count_before - removed, 0)
    struct.pack_into("<I", tail, count_off, entry_count_after)
    result.entry_count_before = entry_count_before
    result.entry_count_after = entry_count_after

    # Re-insert freed bytes as zero padding just before the final trailer so
    # the total blob length is preserved.
    if freed:
        tail = tail[:-_TRAILER_LEN] + b"\x00" * freed + tail[-_TRAILER_LEN:]

    new_data = bytearray(data[:0x14]) + region + tail
    assert len(new_data) == len(blob), (
        f"length changed: {len(blob)} -> {len(new_data)}"
    )

    result.removed_handles = sorted(matched)
    return bytes(new_data), result


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
