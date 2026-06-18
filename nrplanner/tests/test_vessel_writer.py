"""Round-trip tests for nrplanner.vessel_writer against the real save fixture.

These prove that every loadout/vessel write op is length-preserving, keeps the
preset chain well-formed (counter invariant), survives a full re-encrypt /
re-decrypt cycle, and leaves a valid slot checksum.  They are the safety net
before any in-game validation.

The fixture (backend/tests/fixtures/NR0000.sl2) is gitignored; tests skip when
it is absent (e.g. on CI).
"""
import hashlib
import struct
import tempfile
from pathlib import Path

import pytest

from nrplanner.save import decrypt_sl2
from nrplanner import vessel_writer as vw

_FIXTURE_DIR = Path(__file__).parents[2] / "backend" / "tests" / "fixtures"
FIXTURE = _FIXTURE_DIR / "NR0000.sl2"
BIG_FIXTURE = _FIXTURE_DIR / "NR0000_6_18_2026.sl2"  # ~95 saved loadouts
SLOT_INDEX = 0  # Ketaman == BND4 entry 0 == USERDATA_00
_TRAILER_LEN = 0x1C

pytestmark = pytest.mark.skipif(not FIXTURE.exists(),
                                reason="save fixture NR0000.sl2 not present")


@pytest.fixture(scope="module")
def raw_sl2() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def blob(raw_sl2: bytes) -> bytes:
    """Decrypted USERDATA_00 (Ketaman) blob."""
    with tempfile.TemporaryDirectory() as tmp:
        out = decrypt_sl2(FIXTURE, output_dir=tmp)
        return (Path(out) / f"USERDATA_{SLOT_INDEX:02d}").read_bytes()


# --- helpers ---------------------------------------------------------------

def _counters_valid(presets) -> bool:
    """A well-formed chain: counters are a permutation of 0..N-1 and the
    physically-last record carries counter 0 (the parser terminator)."""
    if not presets:
        return True
    counters = [p.counter for p in presets]
    return sorted(counters) == list(range(len(presets))) and presets[-1].counter == 0


def _checksum_valid(b: bytes) -> bool:
    end = len(b) - _TRAILER_LEN
    return hashlib.md5(b[4:end], usedforsecurity=False).digest() == b[end:end + 16]


# --- baseline --------------------------------------------------------------

def test_fixture_parses(blob):
    presets = vw.parse_presets(blob)
    assert len(presets) == 59
    assert _counters_valid(presets)
    assert any(p.name == "Skill" for p in presets)


# --- in-place ops ----------------------------------------------------------

def test_reset_all_vessels(blob):
    out, res = vw.reset_all_vessels(blob)
    assert len(out) == len(blob)
    assert res.vessels_cleared > 0
    # every equipped vessel relic field is now zero
    cursor = vw._locate_region(out)
    slots, _ = vw._walk_vessels(out, cursor)
    for s in slots:
        assert struct.unpack_from("<6I", out, s.relics_offset) == (0, 0, 0, 0, 0, 0)
    # presets untouched
    assert len(vw.parse_presets(out)) == 59


def test_reset_all_presets(blob):
    out, res = vw.reset_all_presets(blob)
    assert len(out) == len(blob)
    assert res.presets_cleared == 59
    assert vw.parse_presets(out) == []


def test_rename_preset(blob):
    before = vw.parse_presets(blob)
    out, res = vw.rename_preset(blob, 3, "renamed!")
    assert len(out) == len(blob)
    after = vw.parse_presets(out)
    assert res.old_name == before[3].name
    assert after[3].name == "renamed!"
    # all other records byte-identical
    for i, (a, b) in enumerate(zip(before, after)):
        if i == 3:
            continue
        assert blob[b.base:b.base + vw.PRESET_SIZE] == out[b.base:b.base + vw.PRESET_SIZE]


def test_overwrite_preset(blob):
    before = vw.parse_presets(blob)
    handles = [0xC0800001, 0xC0800002, 0xC0800003, 0, 0, 0]
    out, res = vw.overwrite_preset(blob, 5, vessel_id=2002, ga_handles=handles,
                                   hero_type=2, name="ow")
    assert len(out) == len(blob)
    after = vw.parse_presets(out)
    assert len(after) == len(before)
    assert after[5].name == "ow"
    assert after[5].vessel_id == 2002
    assert after[5].relics == handles
    assert after[5].counter == before[5].counter  # chain position preserved
    assert _counters_valid(after)


# --- structural ops --------------------------------------------------------

def _chain_bounds(b: bytes):
    cursor = vw._locate_region(b)
    _, s3 = vw._walk_vessels(b, cursor)
    _, chain_end = vw._walk_presets(b, s3)
    return s3, chain_end


HANDLES = [0xC0800010, 0xC0800011, 0xC0800012, 0xC0800013, 0xC0800014, 0xC0800015]


def test_add_into_freed_slot_is_in_place(blob):
    """Delete frees an 80-byte zero slot; add writes into it, shifting nothing.

    The whole delete+add round-trip must touch only the chain region — bytes
    after the original chain_end must be byte-identical (shifting them corrupts
    the save in-game)."""
    _, orig_end = _chain_bounds(blob)
    freed, _ = vw.delete_preset(blob, 5)           # frees a zero slot at end of chain
    out, res = vw.add_preset(freed, hero_type=1, name="NEW LOADOUT",
                             vessel_id=1002, ga_handles=HANDLES)
    assert len(out) == len(blob)
    assert res.presets_after == 59
    # nothing past the original chain end moved across the whole round-trip
    assert out[orig_end:] == blob[orig_end:]
    after = vw.parse_presets(out)
    assert len(after) == 59
    assert _counters_valid(after)
    new = [p for p in after if p.name == "NEW LOADOUT"]
    assert len(new) == 1
    assert new[0].counter == 0  # newest preset is the terminator (counter 0)
    assert new[0].vessel_id == 1002 and new[0].relics == HANDLES


def test_add_into_empty_slot_no_delete(blob):
    """Section 3 is a fixed 100-slot array with empty slots after the actives, so
    add works directly on a pristine save — no delete needed. Writes into the
    first empty slot in place; nothing past the 100-slot block moves."""
    region = vw._locate_region(blob)
    _, s3 = vw._walk_vessels(blob, region)
    block_end = s3 + vw.MAX_PRESETS * vw.PRESET_SIZE
    before = vw.parse_presets(blob)

    out, res = vw.add_preset(blob, hero_type=1, name="DIRECT ADD",
                             vessel_id=1002, ga_handles=HANDLES)
    assert len(out) == len(blob)
    assert res.presets_before == len(before)
    assert res.presets_after == len(before) + 1
    assert res.slot_index == len(before)          # first empty == active count
    assert out[block_end:] == blob[block_end:]     # downstream untouched
    assert out[:s3] == blob[:s3]                    # S1/S2 untouched (no update_current)
    after = vw.parse_presets(out)
    assert len(after) == len(before) + 1
    assert _counters_valid(after)
    new = [p for p in after if p.name == "DIRECT ADD"]
    assert len(new) == 1 and new[0].counter == 0
    assert new[0].vessel_id == 1002 and new[0].relics == HANDLES
    # every old preset preserved with counter+1
    for a, b in zip(before, after[:len(before)]):
        assert a.name == b.name and a.vessel_id == b.vessel_id and a.relics == b.relics
        assert b.counter == a.counter + 1


def test_add_update_current_repoints_hero(blob):
    """update_current=True points the recipient hero's cur_preset_idx at the new
    preset (matches the game); default leaves it untouched."""
    region = vw._locate_region(blob)

    def cur_idx(b, hero):
        cur = region
        for _ in range(vw._HERO_SLOTS):
            if b[cur] == hero:
                return b[cur + 1]
            cur += vw._HERO_BLOCK
        return None

    n = len(vw.parse_presets(blob))
    before_idx = cur_idx(blob, 1)

    out_default, res_d = vw.add_preset(blob, hero_type=1, name="a",
                                       vessel_id=1002, ga_handles=HANDLES)
    assert res_d.made_current is False
    assert cur_idx(out_default, 1) == before_idx          # untouched by default

    out_cur, res_c = vw.add_preset(blob, hero_type=1, name="b", vessel_id=1002,
                                   ga_handles=HANDLES, update_current=True)
    assert res_c.made_current is True
    assert cur_idx(out_cur, 1) == n                        # new preset's index


def test_add_refuses_only_when_full(blob):
    """Add refuses with PresetCapacityError only when all 100 slots are active."""
    b = blob
    # fill remaining empty slots up to the 100-slot cap
    while len(vw.parse_presets(b)) < vw.MAX_PRESETS:
        b, _ = vw.add_preset(b, hero_type=1, name="fill", vessel_id=1002,
                             ga_handles=HANDLES)
    assert len(vw.parse_presets(b)) == vw.MAX_PRESETS
    with pytest.raises(vw.PresetCapacityError):
        vw.add_preset(b, hero_type=1, name="overflow", vessel_id=1002,
                      ga_handles=HANDLES)


def test_delete_preset(blob):
    before = vw.parse_presets(blob)
    target = before[10]
    s3, chain_end = _chain_bounds(blob)
    out, res = vw.delete_preset(blob, 10)
    assert len(out) == len(blob)
    assert res.presets_after == 58
    # CRITICAL: delete must touch ONLY the chain region — nothing before s3 or
    # after chain_end may change (shifting downstream data corrupts the save).
    assert out[:s3] == blob[:s3]
    assert out[chain_end:] == blob[chain_end:]
    after = vw.parse_presets(out)
    assert len(after) == 58
    assert _counters_valid(after)
    # the removed record's identity is gone (matched by name+vessel+relics)
    sig = (target.name, target.vessel_id, tuple(target.relics))
    remaining = [(p.name, p.vessel_id, tuple(p.relics)) for p in after]
    assert remaining.count(sig) == [
        (p.name, p.vessel_id, tuple(p.relics)) for p in before].count(sig) - 1


def test_delete_last_preset_keeps_terminator(blob):
    before = vw.parse_presets(blob)
    out, _ = vw.delete_preset(blob, len(before) - 1)
    after = vw.parse_presets(out)
    assert len(after) == len(before) - 1
    assert _counters_valid(after)  # new physically-last must carry counter 0


# --- full encrypt round-trip + checksum ------------------------------------

@pytest.mark.parametrize("make", [
    # delete then add (the delete frees a zero slot the in-place add writes into)
    lambda b: vw.add_preset(vw.delete_preset(b, 0)[0], hero_type=1, name="rt",
                            vessel_id=1002, ga_handles=[0xC0800010, 0, 0, 0, 0, 0])[0],
    lambda b: vw.delete_preset(b, 10)[0],
    lambda b: vw.reset_all_vessels(b)[0],
    lambda b: vw.reset_all_presets(b)[0],
    lambda b: vw.rename_preset(b, 0, "rt")[0],
])
def test_repack_roundtrip(raw_sl2, blob, make):
    from nrplanner.writer import repack_sl2
    modified = make(blob)
    assert len(modified) == len(blob)
    new_sl2 = repack_sl2(raw_sl2, {SLOT_INDEX: modified})
    assert len(new_sl2) == len(raw_sl2)
    # decrypt the repacked file and confirm our blob comes back, checksum valid
    with tempfile.TemporaryDirectory() as tmp:
        FIXTURE_COPY = Path(tmp) / "edited.sl2"
        FIXTURE_COPY.write_bytes(new_sl2)
        out = decrypt_sl2(FIXTURE_COPY, output_dir=Path(tmp) / "dec")
        redec = (Path(out) / f"USERDATA_{SLOT_INDEX:02d}").read_bytes()
    # patch_slot_checksum (run inside repack) makes the trailer match; compare
    # everything except the 16-byte md5 the writer recomputed.
    end = len(modified) - _TRAILER_LEN
    assert redec[:end] == modified[:end]
    assert redec[end + 16:] == modified[end + 16:]
    assert _checksum_valid(redec)


# --- validation / errors ---------------------------------------------------

def test_name_too_long_rejected(blob):
    with pytest.raises(vw.InvalidNameError):
        vw.rename_preset(blob, 0, "x" * 19)  # 19 > 18 UTF-16 units


def test_bad_index_rejected(blob):
    with pytest.raises(vw.PresetIndexError):
        vw.delete_preset(blob, 999)
    with pytest.raises(vw.PresetIndexError):
        vw.overwrite_preset(blob, 999, vessel_id=1, ga_handles=[])


def test_name_exactly_18_ok(blob):
    out, _ = vw.rename_preset(blob, 0, "x" * 18)
    assert vw.parse_presets(out)[0].name == "x" * 18


# --- large-chain / capacity (new ~95-preset fixture) -----------------------

@pytest.fixture(scope="module")
def big_blob() -> bytes:
    if not BIG_FIXTURE.exists():
        pytest.skip("large fixture not present")
    with tempfile.TemporaryDirectory() as tmp:
        out = decrypt_sl2(BIG_FIXTURE, output_dir=tmp)
        return (Path(out) / f"USERDATA_{SLOT_INDEX:02d}").read_bytes()


def test_big_fixture_parses(big_blob):
    presets = vw.parse_presets(big_blob)
    assert len(presets) >= 90
    assert _counters_valid(presets)


def test_big_delete_then_add_in_place(big_blob):
    """On the large save: delete frees a slot, add fills it back — in place,
    nothing past the original chain end moves."""
    _, orig_end = _chain_bounds(big_blob)
    n = len(vw.parse_presets(big_blob))
    freed, _ = vw.delete_preset(big_blob, 3)
    out, res = vw.add_preset(freed, hero_type=2, name="big add",
                             vessel_id=2002, ga_handles=[0xC0800010, 0, 0, 0, 0, 0])
    assert len(out) == len(big_blob)
    assert res.presets_after == n
    assert out[orig_end:] == big_blob[orig_end:]   # downstream untouched
    assert _counters_valid(vw.parse_presets(out))


def test_big_add_repack_roundtrip(raw_sl2_big, big_blob):
    from nrplanner.writer import repack_sl2
    freed, _ = vw.delete_preset(big_blob, 3)
    out, _ = vw.add_preset(freed, hero_type=2, name="big rt", vessel_id=2002,
                           ga_handles=[0xC0800010, 0xC0800011, 0, 0, 0, 0])
    assert len(out) == len(big_blob)
    new_sl2 = repack_sl2(raw_sl2_big, {SLOT_INDEX: out})
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "e.sl2"
        p.write_bytes(new_sl2)
        d = decrypt_sl2(p, output_dir=Path(tmp) / "dec")
        redec = (Path(d) / f"USERDATA_{SLOT_INDEX:02d}").read_bytes()
    end = len(out) - _TRAILER_LEN
    assert redec[:end] == out[:end] and _checksum_valid(redec)


@pytest.fixture(scope="module")
def raw_sl2_big() -> bytes:
    if not BIG_FIXTURE.exists():
        pytest.skip("large fixture not present")
    return BIG_FIXTURE.read_bytes()
