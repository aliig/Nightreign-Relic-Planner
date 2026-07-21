"""Fixture-free coverage for the non-contiguous preset array.

An in-game preset delete tombstones a record in place (header byte -> 0x00,
vessel_id -> 0xFFFFFFFF) WITHOUT compacting, so a hole can sit between active
records. The reader must scan the whole fixed 100-slot array by header rather
than stopping at the first non-active slot (which drops every preset after the
hole — the reported "loadout not listing" bug).

These tests hand-build a Section-3 array in memory, so they run on CI where the
real .sl2 save fixture is absent.
"""
import struct

from nrplanner import vessel_writer as vw


def _empty_slot() -> bytes:
    """A never-used slot: all zero (header 0, vessel_id 0)."""
    return bytes(vw.PRESET_SIZE)


def _tombstone() -> bytes:
    """A game-deleted slot: header 0, vessel_id 0xFFFFFFFF, everything else zero."""
    rec = bytearray(vw.PRESET_SIZE)
    struct.pack_into("<I", rec, vw._OFF_VESSEL, 0xFFFFFFFF)
    return bytes(rec)


def _active(hero: int, counter: int, name: str, vessel: int) -> bytes:
    return vw._build_record(hero_type=hero, counter=counter, name=name,
                            vessel_id=vessel, ga_handles=[], timestamp=0)


def _array(*records: bytes) -> bytes:
    """A full MAX_PRESETS-slot array: the given records followed by empty slots."""
    slots = list(records)
    slots += [_empty_slot()] * (vw.MAX_PRESETS - len(slots))
    return b"".join(slots)


def test_walk_presets_skips_tombstone_and_keeps_tail():
    blob = _array(
        _active(1, 4, "a", 1002),
        _active(2, 3, "b", 2002),
        _tombstone(),                        # in-game delete between active records
        _active(3, 1, "c", 3002),
        _active(6, 0, "O Flame Rev", 6006),  # newest, physically after the hole
    )
    presets, chain_end = vw._walk_presets(blob, 0)
    assert [p.name for p in presets] == ["a", "b", "c", "O Flame Rev"]
    assert [p.index for p in presets] == [0, 1, 2, 3]  # active-in-physical-order
    assert chain_end == vw.MAX_PRESETS * vw.PRESET_SIZE


def test_walk_presets_all_empty():
    presets, chain_end = vw._walk_presets(_array(), 0)
    assert presets == []
    assert chain_end == vw.MAX_PRESETS * vw.PRESET_SIZE


def test_walk_presets_stops_at_array_end():
    """Bytes AFTER the fixed 100-slot array (the downstream section) must never be
    read as a preset, even if they happen to start with an active header byte."""
    blob = _array(_active(1, 0, "only", 1002))
    blob += _active(1, 0, "downstream", 9999)  # a look-alike record past the array
    presets, _ = vw._walk_presets(blob, 0)
    assert [p.name for p in presets] == ["only"]
