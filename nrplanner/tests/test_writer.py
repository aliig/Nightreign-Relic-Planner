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
from nrplanner.models import RelicInventory
from nrplanner.save import _parse_items
from nrplanner.vessel import LoadoutHandler
from nrplanner.writer import (
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
