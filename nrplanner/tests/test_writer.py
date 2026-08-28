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
from nrplanner.save import _parse_items, read_char_name
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
    repair_blob,
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

    # item_id(0x04) + its mirror(0x08), effects(0x10/0x14/0x18), curses(0x38/0x3C/0x40).
    _WRITTEN = (set(range(0x04, 0x0C)) | set(range(0x10, 0x1C))
                | set(range(0x38, 0x44)))

    def test_writes_fields_and_preserves_all_other_bytes(self):
        template = bytearray(i & 0xFF for i in range(80))  # distinct filler
        struct.pack_into("<I", template, 0x00, 0xC0001234)  # relic-type handle
        template = bytes(template)

        real_id = 205
        rec = build_relic_record(real_id, [310000, 320000, EMPTY_EFFECT],
                                 [EMPTY_EFFECT, EMPTY_EFFECT, EMPTY_EFFECT], template)

        assert len(rec) == 80
        assert struct.unpack_from("<I", rec, 0x04)[0] == real_id + 0x80000000
        # 0x08 mirrors item_id — must NOT keep the donor template's id.
        assert struct.unpack_from("<I", rec, 0x08)[0] == real_id + 0x80000000
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


def _read_next_acq_id(blob: bytes) -> int:
    """The save's next-acquisition-id counter (see writer._NEXT_ACQ_ID_REL_OFFSET)."""
    from nrplanner.writer import _NEXT_ACQ_ID_REL_OFFSET

    _, items_end = _parse_items(blob, start_offset=0x14, slot_count=5120)
    return struct.unpack_from("<I", blob, items_end + _NEXT_ACQ_ID_REL_OFFSET)[0]


def _max_acq_id(blob: bytes) -> int:
    from nrplanner.writer import read_acquisition_ids

    _, items_end = _parse_items(blob, start_offset=0x14, slot_count=5120)
    return max(read_acquisition_ids(blob, items_end).values(), default=0)


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
        # Capacity is per-pass: the tail-shift budget renews each export, so a
        # follow-up pass keeps minting from whatever empty slots remain.
        _, res2 = add_relics(new_blob, [record])
        assert res2.ghosts_available == 0
        assert len(res2.minted_handles) == 1

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
class TestGameWrittenInvariants:
    """Invariants the GAME maintains, which every export must also maintain.

    Both were found by diffing a failing export against real saves (2026-08-27);
    the fixture is a game-written save, so it must satisfy them untouched.
    """

    def test_next_acq_counter_is_one_past_the_highest_id(self, userdata):
        # Held in 11 of 11 game-written saves surveyed — this is what pins
        # _NEXT_ACQ_ID_REL_OFFSET to the right field.
        assert _read_next_acq_id(userdata) == _max_acq_id(userdata) + 1

    def test_relic_records_mirror_item_id_at_0x08(self, userdata):
        relics, _ = parse_relics(userdata)
        assert relics, "fixture should contain relics"
        for r in relics:
            if r.size != 80:
                continue
            item_id, mirror = struct.unpack_from("<II", userdata, r.offset + 0x04)
            assert mirror == item_id, (
                f"relic {r.ga_handle:#x} at {r.offset}: 0x08={mirror:#x} "
                f"does not mirror item_id={item_id:#x}")


@requires_fixture
class TestRepairBlob:
    """repair_blob heals damage a pre-2026-08-27 export could have baked in.

    The game round-trips that damage forward forever, so exports self-heal it.
    """

    def test_pristine_save_is_left_completely_alone(self, userdata):
        repaired, result = repair_blob(userdata)
        assert repaired == userdata
        assert result.item_id_mirrors_fixed == []
        assert result.next_acq_id_before == result.next_acq_id_after
        assert result.changed is False

    def test_heals_a_stale_item_id_mirror(self, userdata):
        relics, _ = parse_relics(userdata)
        victim = next(r for r in relics if r.size == 80)
        damaged = bytearray(userdata)
        struct.pack_into("<I", damaged, victim.offset + 0x08, 0xDEADBEEF)

        repaired, result = repair_blob(bytes(damaged))

        assert result.item_id_mirrors_fixed == [victim.ga_handle]
        item_id, mirror = struct.unpack_from("<II", repaired, victim.offset + 0x04)
        assert mirror == item_id
        assert repaired == userdata  # byte-for-byte back to the original

    def test_advances_a_counter_left_behind(self, userdata):
        from nrplanner.writer import _NEXT_ACQ_ID_REL_OFFSET

        damaged = bytearray(userdata)
        _, items_end = _parse_items(damaged, start_offset=0x14, slot_count=5120)
        max_acq = _max_acq_id(userdata)
        struct.pack_into("<I", damaged, items_end + _NEXT_ACQ_ID_REL_OFFSET, max_acq - 5)

        repaired, result = repair_blob(bytes(damaged))

        assert result.next_acq_id_before == max_acq - 5
        assert result.next_acq_id_after == max_acq + 1
        assert _read_next_acq_id(repaired) == max_acq + 1
        assert result.changed is True

    def test_is_idempotent_and_length_preserving(self, userdata):
        relics, _ = parse_relics(userdata)
        victim = next(r for r in relics if r.size == 80)
        damaged = bytearray(userdata)
        struct.pack_into("<I", damaged, victim.offset + 0x08, 0xDEADBEEF)

        once, first = repair_blob(bytes(damaged))
        twice, second = repair_blob(once)

        assert twice == once
        assert first.changed is True and second.changed is False
        assert len(once) == len(userdata)

    def test_add_relics_output_needs_no_repair(self, userdata):
        """The writer fixes mean a fresh export is already clean."""
        new_blob, _ = add_relics(userdata, [_clone_relic_record(userdata)] * 2)
        _, result = repair_blob(new_blob)
        assert result.changed is False


@requires_fixture
class TestNextAcquisitionCounter:
    """add_relics must leave the counter ahead of every id it hands out."""

    def test_counter_advances_past_every_new_row(self, userdata):
        record = _clone_relic_record(userdata)
        before = _read_next_acq_id(userdata)
        max_before = _max_acq_id(userdata)

        new_blob, result = add_relics(userdata, [record] * 3)

        assert len(result.added_handles) == 3
        after_max = _max_acq_id(new_blob)
        assert after_max == max_before + 3          # ids handed out contiguously
        assert _read_next_acq_id(new_blob) > after_max
        assert _read_next_acq_id(new_blob) > before

    def test_counter_never_moves_backwards(self, userdata):
        # A counter already further ahead than our watermark must be preserved,
        # not clobbered down to max+1.
        from nrplanner.writer import _NEXT_ACQ_ID_REL_OFFSET

        blob = bytearray(userdata)
        _, items_end = _parse_items(blob, start_offset=0x14, slot_count=5120)
        far_ahead = _max_acq_id(bytes(blob)) + 10_000
        struct.pack_into("<I", blob, items_end + _NEXT_ACQ_ID_REL_OFFSET, far_ahead)

        new_blob, _ = add_relics(bytes(blob), [_clone_relic_record(userdata)])

        assert _read_next_acq_id(new_blob) == far_ahead

    def test_no_adds_leaves_the_counter_untouched(self, userdata):
        before = _read_next_acq_id(userdata)
        new_blob, result = add_relics(userdata, [])
        assert result.added_handles == []
        assert _read_next_acq_id(new_blob) == before


def _clone_relic_record(blob: bytes) -> bytes:
    """An owned relic's raw 80-byte ItemState record, as realistic add input."""
    relics, _ = parse_relics(blob)
    source = next(r for r in relics if r.size == 80)
    return blob[source.offset:source.offset + 80]


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

    def test_add_beyond_ghost_capacity_mints(self, userdata):
        """Beyond the ghost supply adds no longer fail — the excess is minted
        into the arena's empty slots (mechanism 2)."""
        ghosts = _ghost_relics(userdata)
        relics, _ = parse_relics(userdata)
        record = userdata[relics[0].offset:relics[0].offset + 80]
        new_blob, res = add_relics(userdata, [record] * (len(ghosts) + 1))
        assert len(res.minted_handles) == 1
        assert res.tail_shift == 72
        after, _ = parse_relics(new_blob)
        assert len(after) == len(relics) + len(ghosts) + 1

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


def _empty_slot_count(blob: bytes) -> int:
    """Canonical empty arena slots ANYWHERE in the arena (the mintable pool)."""
    from nrplanner.writer import _empty_slots

    items, _ = _parse_items(blob, start_offset=0x14, slot_count=5120)
    return len(_empty_slots(blob, items))


def _free_entry_rows(blob: bytes) -> int:
    from nrplanner.save import _ITEM_ENTRY_SIZE, _ITEM_ENTRY_SLOT_COUNT

    _, items_end = _parse_items(blob, start_offset=0x14, slot_count=5120)
    entries_start = items_end + 0x94 + 0x5B8 + 4
    return sum(
        1 for i in range(_ITEM_ENTRY_SLOT_COUNT)
        if struct.unpack_from("<I", blob, entries_start + i * _ITEM_ENTRY_SIZE)[0] == 0
    )


def _live_entry_rows(blob: bytes) -> int:
    from nrplanner.save import _ITEM_ENTRY_SLOT_COUNT

    return _ITEM_ENTRY_SLOT_COUNT - _free_entry_rows(blob)


def _trailing_empty_run(blob: bytes) -> int:
    """Canonical empties in a CONTIGUOUS run at the arena's end.

    Replicates the pre-2026-08-27 mint rule so the regression test can show it
    is 0 exactly where minting must still work.
    """
    from nrplanner.writer import _EMPTY_SLOT_SENTINEL

    items, _ = _parse_items(blob, start_offset=0x14, slot_count=5120)
    run = 0
    for it in reversed(items):
        if (it.gaitem_handle != 0 or it.size != 8
                or blob[it.offset:it.offset + 8] != _EMPTY_SLOT_SENTINEL):
            break
        run += 1
    return run


@requires_fixture
class TestMintRelics:
    """Empty-slot minting: adds beyond the ghost supply convert canonical empty
    arena records (tail-most first) into 80-byte relic records, shifting the
    items_end-relative tail exactly like the game's own acquisition write."""

    def test_mint_full_roundtrip_preserves_everything(self, raw_save, userdata, ds):
        from nrplanner.constants import ITEM_TYPE_RELIC

        ghosts = _ghost_relics(userdata)
        n_mint = 3
        n = len(ghosts) + n_mint

        before_relics, items_end_before = parse_relics(userdata)
        source = next(r for r in before_relics if r.size == 80)
        record = userdata[source.offset:source.offset + 80]

        murks_before = _read_murks(userdata)
        count_before = _read_entry_count(userdata)
        favs_before = read_favorite_handles(userdata, items_end_before)
        empties_before = _empty_slot_count(userdata)
        old_items, _ = _parse_items(userdata, start_offset=0x14, slot_count=5120)
        old_handles = {it.gaitem_handle for it in old_items if it.gaitem_handle}
        loadout_before = LoadoutHandler(ds)
        loadout_before.parse(userdata)

        new_blob, result = add_relics(userdata, [record] * n)

        assert len(new_blob) == len(userdata)
        assert result.tail_shift == n_mint * 72
        assert len(result.minted_handles) == n_mint
        assert result.added_handles[:len(ghosts)] == [g.gaitem_handle for g in ghosts]
        assert result.added_handles[len(ghosts):] == result.minted_handles
        # Fresh handles: relic-typed, unique, unseen anywhere in the old arena.
        assert len(set(result.minted_handles)) == n_mint
        for h in result.minted_handles:
            assert (h & 0xF0000000) == ITEM_TYPE_RELIC
            assert h not in old_handles

        # Each mint consumed exactly one canonical empty slot.
        _, items_end_after_direct = _parse_items(new_blob, start_offset=0x14,
                                                 slot_count=5120)
        assert items_end_after_direct == items_end_before + n_mint * 72
        assert _empty_slot_count(new_blob) == empties_before - n_mint

        # Survives a full repack/re-decrypt.
        repacked = repack_sl2(raw_save, {0: new_blob})
        rt = _decrypt_blob(repacked)
        after_relics, items_end_after = parse_relics(rt)
        after_by_handle = {r.ga_handle: r for r in after_relics}

        assert len(after_relics) == len(before_relics) + n
        for h in result.added_handles:
            added = after_by_handle[h]
            assert (added.item_id, added.effect_1, added.effect_2, added.effect_3,
                    added.sec_effect1, added.sec_effect2, added.sec_effect3) == (
                source.item_id, source.effect_1, source.effect_2, source.effect_3,
                source.sec_effect1, source.sec_effect2, source.sec_effect3)
        for r in before_relics:
            assert r.ga_handle in after_by_handle

        # Every tail structure survives the shift at its items_end-relative spot.
        assert _read_murks(rt) == murks_before
        assert _read_entry_count(rt) == count_before + n
        assert read_favorite_handles(rt, items_end_after) == favs_before
        loadout_after = LoadoutHandler(ds)
        loadout_after.parse(rt)
        assert (loadout_after.parser.base_offset
                == loadout_before.parser.base_offset + n_mint * 72)
        assert loadout_after.relic_ga_hero_map == loadout_before.relic_ga_hero_map

        # Layer 1 / Layer 2 stay consistent: the stored ItemEntry count still
        # equals the live row count, and no two arena records share a handle.
        assert _read_entry_count(rt) == _live_entry_rows(rt)
        rt_items, _ = _parse_items(rt, start_offset=0x14, slot_count=5120)
        rt_handles = [it.gaitem_handle for it in rt_items if it.gaitem_handle]
        assert len(rt_handles) == len(set(rt_handles))

        # The character name rides the items_end-relative shift intact.
        assert (read_char_name(rt, items_end_after)
                == read_char_name(userdata, items_end_before))

        # MD5 trailer valid on the round-tripped blob.
        end = len(rt) - 28
        assert rt[end:end + 16] == hashlib.md5(
            rt[4:end], usedforsecurity=False).digest()

    def test_mint_capacity_composition(self, userdata):
        from nrplanner.writer import _MAX_TAIL_SHIFT, _MINT_GROWTH

        ghosts = len(_ghost_relics(userdata))
        mintable = min(_empty_slot_count(userdata), _MAX_TAIL_SHIFT // _MINT_GROWTH)
        assert add_capacity(userdata) == min(ghosts + mintable, _free_entry_rows(userdata))

    def test_mint_then_mint_again(self, userdata):
        """A minted blob is a valid base for another mint pass.

        Tail-most selection means pass 1 leaves relic records at the arena's
        end, so pass 2 necessarily mints into empties that have live records
        after them — i.e. it exercises the mid-arena rebuild."""
        ghosts = _ghost_relics(userdata)
        relics, _ = parse_relics(userdata)
        record = userdata[relics[0].offset:relics[0].offset + 80]

        first, res1 = add_relics(userdata, [record] * (len(ghosts) + 2))
        assert len(res1.minted_handles) == 2
        second, res2 = add_relics(first, [record] * 2)  # no ghosts left -> pure mint
        assert len(res2.minted_handles) == 2
        assert set(res2.minted_handles).isdisjoint(res1.minted_handles)

        # Pass 2's records sit BEFORE pass 1's, i.e. it rebuilt mid-arena with
        # live records after the mint point.
        offsets = {it.gaitem_handle: it.offset for it
                   in _parse_items(second, start_offset=0x14, slot_count=5120)[0]}
        assert (max(offsets[h] for h in res2.minted_handles)
                < min(offsets[h] for h in res1.minted_handles))

        after, _ = parse_relics(second)
        assert len(after) == len(relics) + len(ghosts) + 4

    def test_corrupt_empty_slot_shrinks_mint_capacity_by_one(self, userdata):
        """A byte off the canonical 00000000/FFFFFFFF empty-slot sentinel
        disqualifies THAT record only — minting still trusts verified sentinel
        slots, but a deviant one no longer disqualifies its neighbours."""
        items, _ = _parse_items(userdata, start_offset=0x14, slot_count=5120)
        assert items[-1].gaitem_handle == 0 and items[-1].size == 8
        empties_before = _empty_slot_count(userdata)
        assert empties_before > 0
        poisoned = bytearray(userdata)
        poisoned[items[-1].offset + 4] = 0x7F  # breaks the item_id sentinel
        assert _empty_slot_count(bytes(poisoned)) == empties_before - 1

    def test_mint_survives_a_blocked_tail(self, raw_save, userdata):
        """THE regression: a save whose LAST arena record is not a canonical
        empty must still mint.

        Real post-session saves park the game's own weapon/armor ghost records
        at the arena's end, so the old trailing-run rule reported zero mintable
        slots (measured 2026-08-26: capacity 12 on a save holding 3,292
        canonical empties). Simulated by poisoning the last record's item_id —
        it still parses as an 8-byte record, so the arena stays in sync, but
        the trailing canonical run collapses to 0.
        """
        from nrplanner.writer import _MAX_TAIL_SHIFT, _MINT_GROWTH

        items, items_end_before = _parse_items(userdata, start_offset=0x14,
                                               slot_count=5120)
        assert items[-1].gaitem_handle == 0 and items[-1].size == 8
        poisoned = bytearray(userdata)
        struct.pack_into("<I", poisoned, items[-1].offset + 4, 0x12345678)
        blocked = bytes(poisoned)

        # Arena still parses identically — only a dead slot's id changed.
        _, items_end_blocked = _parse_items(blocked, start_offset=0x14, slot_count=5120)
        assert items_end_blocked == items_end_before
        assert _trailing_empty_run(blocked) == 0  # the old rule would mint nothing

        ghosts = _ghost_relics(blocked)
        empties = _empty_slot_count(blocked)
        n_mint = 3
        assert empties >= n_mint
        assert add_capacity(blocked) == min(
            len(ghosts) + min(empties, _MAX_TAIL_SHIFT // _MINT_GROWTH),
            _free_entry_rows(blocked))

        before_relics, _ = parse_relics(blocked)
        source = next(r for r in before_relics if r.size == 80)
        record = blocked[source.offset:source.offset + 80]
        n = len(ghosts) + n_mint
        new_blob, res = add_relics(blocked, [record] * n)

        assert len(res.minted_handles) == n_mint
        assert res.tail_shift == n_mint * 72
        assert len(new_blob) == len(blocked)

        rt = _decrypt_blob(repack_sl2(raw_save, {0: new_blob}))
        after_relics, items_end_after = parse_relics(rt)
        by_handle = {r.ga_handle: r for r in after_relics}
        assert len(after_relics) == len(before_relics) + n
        for h in res.added_handles:
            assert h in by_handle
        for r in before_relics:
            assert r.ga_handle in by_handle
        assert items_end_after == items_end_before + n_mint * 72
        assert _empty_slot_count(rt) == empties - n_mint
        assert _read_entry_count(rt) == _live_entry_rows(rt)

        # The blocking record survived the rebuild untouched, still a dead slot.
        rt_items, _ = _parse_items(rt, start_offset=0x14, slot_count=5120)
        assert rt_items[-1].gaitem_handle == 0
        assert rt_items[-1].item_id == 0x12345678

    def test_mint_into_non_contiguous_empties(self, raw_save, userdata):
        """The rebuild must handle chosen empties that are NOT adjacent.

        Real arenas interleave dead slots with live records, so the tail-most
        empties are usually scattered. Simulated by poisoning every other one of
        the last 12 records: the three mintable empties then sit 16 bytes apart
        and each needs its own segment in the rebuild.
        """
        items, items_end_before = _parse_items(userdata, start_offset=0x14,
                                               slot_count=5120)
        poisoned = bytearray(userdata)
        for k in range(1, 13, 2):
            slot = items[-k]
            assert slot.gaitem_handle == 0 and slot.size == 8
            struct.pack_into("<I", poisoned, slot.offset + 4, 0xAAAAAAAA)
        scattered = bytes(poisoned)

        scattered_items, _ = _parse_items(scattered, start_offset=0x14, slot_count=5120)
        from nrplanner.writer import _empty_slots
        chosen = _empty_slots(scattered, scattered_items)[-3:]
        assert all(chosen[i + 1].offset - chosen[i].offset == 16 for i in range(2)), (
            "the mintable empties must be non-adjacent for this test to mean anything")

        ghosts = _ghost_relics(scattered)
        before_relics, _ = parse_relics(scattered)
        source = next(r for r in before_relics if r.size == 80)
        record = scattered[source.offset:source.offset + 80]
        n = len(ghosts) + 3
        new_blob, res = add_relics(scattered, [record] * n)
        assert len(res.minted_handles) == 3
        assert len(new_blob) == len(scattered)

        rt = _decrypt_blob(repack_sl2(raw_save, {0: new_blob}))
        after_items, items_end_after = _parse_items(rt, start_offset=0x14, slot_count=5120)
        assert items_end_after == items_end_before + 3 * 72

        # Each mint landed in its own slot; the six poisoned dead slots between
        # and after them survived byte-for-byte.
        by_handle = {it.gaitem_handle: it for it in after_items}
        for h in res.minted_handles:
            assert by_handle[h].size == 80
        assert sum(1 for it in after_items
                   if it.gaitem_handle == 0 and it.size == 8
                   and it.item_id == 0xAAAAAAAA) == 6

        after_relics, _ = parse_relics(rt)
        relics_by_handle = {r.ga_handle: r for r in after_relics}
        assert len(after_relics) == len(before_relics) + n
        for h in res.minted_handles:
            minted = relics_by_handle[h]
            assert (minted.item_id, minted.effect_1, minted.effect_2,
                    minted.effect_3) == (source.item_id, source.effect_1,
                                         source.effect_2, source.effect_3)
        assert _read_entry_count(rt) == _live_entry_rows(rt)
