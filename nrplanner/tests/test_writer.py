"""Tests for save write-back (nrplanner/writer.py).

Round-trips the real fixture save: decrypt -> delete a relic + credit Murk ->
repack/re-encrypt -> re-decrypt, and asserts the edit took effect while every
invariant the game relies on is preserved (slot length, MD5, other relics).
"""
import hashlib
import json
import struct
import tempfile
from pathlib import Path

import pytest

from nrplanner import decrypt_sl2, parse_relics
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import RelicInventory
from nrplanner.save import _parse_items
from nrplanner.vessel import LoadoutHandler
from nrplanner.writer import (
    AddCapacityError,
    add_capacity,
    add_relics,
    build_relic_record,
    delete_relics,
    patch_slot_checksum,
    read_favorite_handles,
    read_murks,
    repack_sl2,
    sell_value,
    set_favorites,
)

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent
    / "backend" / "tests" / "fixtures" / "NR0000.sl2"
)

requires_fixture = pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present — copy NR0000.sl2 to backend/tests/fixtures/",
)


# ---------------------------------------------------------------------------
# Pure logic (no fixture needed)
# ---------------------------------------------------------------------------

class TestSellValue:
    @pytest.mark.parametrize("effects,deep,expected", [
        (1, False, 150),
        (2, False, 350),
        (3, False, 550),
        (1, True, 300),
        (2, True, 700),
        (3, True, 1100),
    ])
    def test_in_game_prices(self, effects, deep, expected):
        assert sell_value(effects, deep) == expected

    def test_effect_count_clamped(self):
        # Out-of-range property counts clamp into [1, 3].
        assert sell_value(0, False) == 150
        assert sell_value(5, False) == 550


def test_patch_slot_checksum_roundtrip():
    blob = bytearray(b"\x11" * 200)
    patch_slot_checksum(blob)
    end = len(blob) - 28
    expected = hashlib.md5(bytes(blob[4:end]), usedforsecurity=False).digest()
    assert bytes(blob[end:end + 16]) == expected


class TestBuildRelicRecord:
    """build_relic_record offset math — no save fixture needed."""

    # item_id(0x04), effects(0x10/0x14/0x18), curses(0x38/0x3C/0x40).
    _WRITTEN = set(range(0x04, 0x08)) | set(range(0x10, 0x1C)) | set(range(0x38, 0x44))

    def test_writes_fields_and_preserves_all_other_bytes(self):
        template = bytearray(i & 0xFF for i in range(80))  # distinct filler
        struct.pack_into("<I", template, 0x00, 0xC0001234)  # relic-type handle
        template = bytes(template)

        real_id = 205
        rec = build_relic_record(real_id, [310000, 320000, EMPTY_EFFECT],
                                 [EMPTY_EFFECT, EMPTY_EFFECT, EMPTY_EFFECT], template)

        assert len(rec) == 80
        assert struct.unpack_from("<I", rec, 0x04)[0] == real_id + 0x80000000
        assert list(struct.unpack_from("<III", rec, 0x10)) == [310000, 320000, EMPTY_EFFECT]
        assert list(struct.unpack_from("<III", rec, 0x38)) == [EMPTY_EFFECT] * 3
        # Handle left as-is (add_relics rewrites it); everything else preserved.
        assert struct.unpack_from("<I", rec, 0x00)[0] == 0xC0001234
        for i in range(80):
            if i not in self._WRITTEN:
                assert rec[i] == template[i], f"byte {i:#x} changed unexpectedly"

    def test_pads_short_effect_and_curse_lists(self):
        template = bytes(bytearray([0xC0, 0, 0, 0]) + bytearray(76))
        rec = build_relic_record(100, [310000], [], template)
        assert list(struct.unpack_from("<III", rec, 0x10)) == [310000, EMPTY_EFFECT, EMPTY_EFFECT]
        assert list(struct.unpack_from("<III", rec, 0x38)) == [EMPTY_EFFECT] * 3

    def test_rejects_wrong_template_size(self):
        with pytest.raises(ValueError):
            build_relic_record(100, [], [], b"\x00" * 79)


# ---------------------------------------------------------------------------
# Round-trip against the real fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def raw_save() -> bytes:
    return FIXTURE_PATH.read_bytes()


@pytest.fixture(scope="module")
def userdata() -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        decrypt_sl2(FIXTURE_PATH, tmpdir)
        return (Path(tmpdir) / "USERDATA_00").read_bytes()


@pytest.fixture(scope="module")
def items_json() -> dict:
    import nrplanner as _pkg
    path = Path(_pkg.__file__).parent / "resources" / "json" / "items.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _decrypt_blob(raw: bytes, name: str = "USERDATA_00") -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "in.sl2").write_bytes(raw)
        decrypt_sl2(tmp / "in.sl2", tmp / "out")
        return (tmp / "out" / name).read_bytes()


def _read_murks(blob: bytes) -> int:
    _, items_end = _parse_items(blob, start_offset=0x14, slot_count=5120)
    return struct.unpack_from("<I", blob, items_end + 0x94 + 52)[0]


def _read_entry_count(blob: bytes) -> int:
    _, items_end = _parse_items(blob, start_offset=0x14, slot_count=5120)
    return struct.unpack_from("<I", blob, items_end + 0x94 + 0x5B8)[0]


def _pick_deletable(userdata, items_json, ds) -> object:
    """First owned relic that is neither equipped nor bookmarked."""
    raw_relics, items_end = parse_relics(userdata)
    inv = RelicInventory(raw_relics, items_json, ds)

    loadout = LoadoutHandler(ds)
    loadout.parse(userdata)
    equipped = set(loadout.relic_ga_hero_map.keys())
    favorites = read_favorite_handles(userdata, items_end)

    for relic in inv.relics:
        if relic.ga_handle not in equipped and relic.ga_handle not in favorites:
            return relic
    pytest.skip("No non-equipped, non-favorite relic available in fixture")


@requires_fixture
class TestRoundTrip:
    def test_delete_one_relic_full_roundtrip(self, raw_save, userdata, items_json, ds):
        victim = _pick_deletable(userdata, items_json, ds)
        credit = sell_value(victim.effect_count, victim.is_deep)

        murks_before = _read_murks(userdata)
        count_before = _read_entry_count(userdata)

        new_blob, result = delete_relics(userdata, {victim.ga_handle}, murk_credit=credit)

        # length preserved (required by the BND4 repack)
        assert len(new_blob) == len(userdata)
        assert result.removed_handles == [victim.ga_handle]
        assert result.not_found_handles == []
        assert result.murks_after == min(murks_before + credit, 0xFFFFFFFF)
        assert result.entry_count_after == count_before - 1

        # repack only entry 0 and re-decrypt
        repacked = repack_sl2(raw_save, {0: new_blob})
        assert repacked[:4] == b"BND4"
        assert len(repacked) == len(raw_save)
        rt_blob = _decrypt_blob(repacked)
        assert len(rt_blob) == len(userdata)

        # MD5 trailer valid on the round-tripped blob
        end = len(rt_blob) - 28
        expected_md5 = hashlib.md5(rt_blob[4:end], usedforsecurity=False).digest()
        assert rt_blob[end:end + 16] == expected_md5

        # victim gone, others intact, count + murks reflect the edit
        before_relics, _ = parse_relics(userdata)
        after_relics, _ = parse_relics(rt_blob)
        before_handles = {r.ga_handle for r in before_relics}
        after_handles = {r.ga_handle for r in after_relics}

        assert victim.ga_handle in before_handles
        assert victim.ga_handle not in after_handles
        assert before_handles - after_handles == {victim.ga_handle}
        assert _read_entry_count(rt_blob) == count_before - 1
        assert _read_murks(rt_blob) == min(murks_before + credit, 0xFFFFFFFF)

        # Tombstone: the sold record survives as a resurrectable ghost, so
        # selling RAISES add_capacity by one (1 ghost + 1 free ItemEntry row).
        assert victim.ga_handle in {g.gaitem_handle for g in _ghost_relics(new_blob)}
        assert add_capacity(new_blob) == add_capacity(userdata) + 1

    def test_sell_then_mint_reuses_freed_slot(self, userdata, items_json, ds):
        """The export chain runs sells before mints: a slot freed by a sell must
        be mintable in the same pass, exactly like any pre-existing ghost."""
        victim = _pick_deletable(userdata, items_json, ds)
        sold_blob, _ = delete_relics(userdata, {victim.ga_handle})
        cap = add_capacity(sold_blob)
        assert cap == add_capacity(userdata) + 1

        relics, _ = parse_relics(sold_blob)
        record = sold_blob[relics[0].offset:relics[0].offset + 80]
        new_blob, res = add_relics(sold_blob, [record] * cap)
        assert res.entry_count_after == res.entry_count_before + cap
        with pytest.raises(AddCapacityError):
            add_relics(new_blob, [record])

    def test_other_entries_unchanged(self, raw_save, userdata, items_json, ds):
        """Repacking entry 0 must leave the other BND4 entries byte-identical."""
        victim = _pick_deletable(userdata, items_json, ds)
        new_blob, _ = delete_relics(userdata, {victim.ga_handle})
        repacked = repack_sl2(raw_save, {0: new_blob})

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            decrypt_sl2(FIXTURE_PATH, tmp / "orig")
            (tmp / "edited.sl2").write_bytes(repacked)
            decrypt_sl2(tmp / "edited.sl2", tmp / "new")
            # any slot other than 00 must be identical
            for other in ("USERDATA_01", "USERDATA_02"):
                o = tmp / "orig" / other
                n = tmp / "new" / other
                if o.exists() and n.exists():
                    assert o.read_bytes() == n.read_bytes(), f"{other} changed"

    def test_unknown_handle_reported_not_found(self, userdata):
        new_blob, result = delete_relics(userdata, {0xDEADBEEF})
        assert result.removed_handles == []
        assert result.not_found_handles == [0xDEADBEEF]
        assert new_blob == userdata  # nothing freed -> identical bytes

    def test_read_favorite_handles_returns_set(self, userdata):
        _, items_end = parse_relics(userdata)
        favs = read_favorite_handles(userdata, items_end)
        assert isinstance(favs, set)

    def test_read_murks_is_plausible(self, userdata):
        _, items_end = parse_relics(userdata)
        murks = read_murks(userdata, items_end)
        assert isinstance(murks, int)
        assert 0 <= murks <= 0xFFFFFFFF

    def test_set_favorites_bookmark_roundtrip(self, raw_save, userdata, items_json, ds):
        victim = _pick_deletable(userdata, items_json, ds)  # an un-bookmarked relic
        _, items_end = parse_relics(userdata)
        assert victim.ga_handle not in read_favorite_handles(userdata, items_end)

        # bookmark it
        new_blob, result = set_favorites(userdata, {victim.ga_handle: True})
        assert result.changed_handles == [victim.ga_handle]
        assert len(new_blob) == len(userdata)
        assert victim.ga_handle in read_favorite_handles(new_blob, items_end)

        # survives a full repack/re-decrypt
        repacked = repack_sl2(raw_save, {0: new_blob})
        rt_blob = _decrypt_blob(repacked)
        assert victim.ga_handle in read_favorite_handles(rt_blob, items_end)

        # and can be un-bookmarked again
        cleared, _ = set_favorites(rt_blob, {victim.ga_handle: False})
        assert victim.ga_handle not in read_favorite_handles(cleared, items_end)

    def test_set_favorites_reports_not_found(self, userdata):
        new_blob, result = set_favorites(userdata, {0xDEADBEEF: True})
        assert result.changed_handles == []
        assert result.not_found_handles == [0xDEADBEEF]
        assert new_blob == userdata

    def test_repack_rejects_wrong_size(self, raw_save, userdata):
        with pytest.raises(ValueError):
            repack_sl2(raw_save, {0: userdata + b"\x00"})


def _ghost_relics(blob: bytes):
    """Ghost relic records: full 80-byte Layer-1 relics with no ItemEntry row."""
    from nrplanner.constants import ITEM_TYPE_RELIC
    from nrplanner.save import _parse_active_handles

    items, items_end = _parse_items(blob, start_offset=0x14, slot_count=5120)
    active = _parse_active_handles(blob, items_end)
    return [
        it for it in items
        if (it.gaitem_handle & 0xF0000000) == ITEM_TYPE_RELIC
        and it.size == 80 and it.gaitem_handle not in active
    ]


@requires_fixture
class TestAddRelics:
    def test_add_cloned_relic_full_roundtrip(self, raw_save, userdata):
        ghosts = _ghost_relics(userdata)
        if not ghosts:
            pytest.skip("fixture save has no ghost records to resurrect")

        # Clone an owned relic's raw 80-byte record as realistic input.
        before_relics, _ = parse_relics(userdata)
        source = next(r for r in before_relics if r.size == 80)
        record = userdata[source.offset:source.offset + 80]
        count_before = _read_entry_count(userdata)

        new_blob, result = add_relics(userdata, [record])

        assert len(new_blob) == len(userdata)
        assert result.entry_count_before == count_before
        assert result.entry_count_after == count_before + 1
        new_handle = result.added_handles[0]
        assert new_handle == ghosts[0].gaitem_handle
        assert new_handle != source.ga_handle

        # Survives a full repack/re-decrypt.
        repacked = repack_sl2(raw_save, {0: new_blob})
        rt_blob = _decrypt_blob(repacked)
        after_relics, _ = parse_relics(rt_blob)
        after_by_handle = {r.ga_handle: r for r in after_relics}

        assert len(after_relics) == len(before_relics) + 1
        added = after_by_handle[new_handle]
        # Content identical to the source relic; only the handle differs.
        assert (added.item_id, added.effect_1, added.effect_2, added.effect_3,
                added.sec_effect1, added.sec_effect2, added.sec_effect3) == (
            source.item_id, source.effect_1, source.effect_2, source.effect_3,
            source.sec_effect1, source.sec_effect2, source.sec_effect3)
        # Every pre-existing relic untouched.
        for r in before_relics:
            assert r.ga_handle in after_by_handle
        assert _read_entry_count(rt_blob) == count_before + 1

    def test_add_beyond_ghost_capacity_raises(self, userdata):
        ghosts = _ghost_relics(userdata)
        relics, _ = parse_relics(userdata)
        record = userdata[relics[0].offset:relics[0].offset + 80]
        with pytest.raises(AddCapacityError):
            add_relics(userdata, [record] * (len(ghosts) + 1))

    def test_add_rejects_malformed_records(self, userdata):
        relics, _ = parse_relics(userdata)
        record = userdata[relics[0].offset:relics[0].offset + 80]
        with pytest.raises(ValueError):
            add_relics(userdata, [record[:79]])  # wrong length
        with pytest.raises(ValueError):
            add_relics(userdata, [b"\x00" * 80])  # not a relic handle

    def test_add_capacity_matches_binding_constraint(self, userdata):
        cap = add_capacity(userdata)
        assert cap >= 0
        relics, _ = parse_relics(userdata)
        record = userdata[relics[0].offset:relics[0].offset + 80]
        if cap:
            _, res = add_relics(userdata, [record] * cap)
            assert res.entry_count_after == res.entry_count_before + cap
        with pytest.raises(AddCapacityError):
            add_relics(userdata, [record] * (cap + 1))

    def test_build_generated_relic_full_roundtrip(self, raw_save, userdata, ds):
        """Roll a legal relic, mint it via build_relic_record, and round-trip it.

        The minted relic must carry the ROLLED spec (item_id + effects + curses), not
        the donor template's, while every donor byte outside those fields is preserved.
        """
        from nrplanner.generator import RelicGenerator

        ghosts = _ghost_relics(userdata)
        if not ghosts:
            pytest.skip("fixture save has no ghost records to resurrect")

        before, _ = parse_relics(userdata)
        donor = next(r for r in before if r.size == 80)
        template = userdata[donor.offset:donor.offset + 80]

        # Prefer a deep relic that actually rolled a curse (exercises curse bytes).
        gen = RelicGenerator(ds)
        relic = gen.roll(is_deep=True, mode="random", seed=0)
        for s in range(1, 60):
            if any(c not in (EMPTY_EFFECT,) for c in relic.curses):
                break
            relic = gen.roll(is_deep=True, mode="random", seed=s)

        record = build_relic_record(relic.real_id, relic.effects, relic.curses, template)
        new_blob, result = add_relics(userdata, [record])
        assert len(new_blob) == len(userdata)

        repacked = repack_sl2(raw_save, {0: new_blob})
        rt = _decrypt_blob(repacked)
        after, _ = parse_relics(rt)
        added = {r.ga_handle: r for r in after}[result.added_handles[0]]

        assert added.item_id == relic.item_id
        assert (added.effect_1, added.effect_2, added.effect_3) == relic.effects
        assert (added.sec_effect1, added.sec_effect2, added.sec_effect3) == relic.curses
