"""Tests for save-file parsing — especially the ItemEntry phantom filter.

The save file has two inventory layers:
  Layer 1 (ItemState): 5120 variable-size item slots containing ALL items
                       ever created, including run-session ghosts.
  Layer 2 (ItemEntry): 3065 fixed-size metadata records — only items with
                       a non-zero ga_handle here are truly owned.

parse_relics() must cross-reference both layers so that phantom entries
(items in ItemState but absent from ItemEntry) never reach downstream code.
"""
import json
import struct
import tempfile
from collections import defaultdict
from pathlib import Path

import pytest

from nrplanner import SourceDataHandler, decrypt_sl2, parse_relics
from nrplanner.constants import ITEM_TYPE_RELIC
from nrplanner.models import RelicInventory
from nrplanner.save import (
    PROFILE_ENTRY_FILE,
    PROFILE_ID_OFFSET,
    STEAM_ID_BASE,
    STEAM_ID_MAX,
    _parse_active_handles,
    _parse_items,
    is_steam_id,
    owner_steam_id_from_blob,
    read_owner_steam_id,
)

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent
    / "backend" / "tests" / "fixtures" / "NR0000.sl2"
)


@pytest.fixture(scope="module")
def userdata() -> bytes:
    """Decrypt the fixture save and return the first USERDATA blob."""
    with tempfile.TemporaryDirectory() as tmpdir:
        decrypt_sl2(FIXTURE_PATH, tmpdir)
        return (Path(tmpdir) / "USERDATA_00").read_bytes()


@pytest.fixture(scope="module")
def items_json() -> dict:
    import nrplanner as _pkg
    path = Path(_pkg.__file__).parent / "resources" / "json" / "items.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present — copy NR0000.sl2 to backend/tests/fixtures/",
)
class TestPhantomRelicFilter:
    """Regression guard: parse_relics must exclude run-session ghost entries."""

    def test_no_duplicate_fingerprints(
        self, userdata: bytes, items_json: dict, ds: SourceDataHandler,
    ) -> None:
        """No two returned relics should share the exact same
        (real_id, effects, curses) fingerprint."""
        raw_relics, _ = parse_relics(userdata)
        inv = RelicInventory(raw_relics, items_json, ds)

        groups: dict[tuple, list] = defaultdict(list)
        for r in inv.relics:
            fp = (r.real_id, *r.effects, *r.curses)
            groups[fp].append(r)

        dups = {fp: rs for fp, rs in groups.items() if len(rs) > 1}
        assert not dups, (
            f"Found {len(dups)} duplicate fingerprint group(s) — "
            f"phantom relics are leaking through. "
            f"First duplicate: {next(iter(dups.values()))[0].name}"
        )

    def test_all_returned_handles_in_entry_table(self, userdata: bytes) -> None:
        """Every ga_handle returned by parse_relics must exist in the
        ItemEntry table (Layer 2)."""
        raw_relics, end_offset = parse_relics(userdata)
        active = _parse_active_handles(userdata, end_offset)

        missing = [r for r in raw_relics if r.ga_handle not in active]
        assert not missing, (
            f"{len(missing)} relic(s) returned by parse_relics are not in "
            f"the ItemEntry table: "
            f"{[hex(r.ga_handle) for r in missing[:5]]}"
        )

    def test_phantoms_exist_in_state_but_not_entry(self, userdata: bytes) -> None:
        """The fixture save is known to contain phantom relics in ItemState.
        Verify they exist in the raw item array but are correctly excluded
        by the ItemEntry filter."""
        items, end_offset = _parse_items(userdata, start_offset=0x14, slot_count=5120)
        active = _parse_active_handles(userdata, end_offset)

        state_relic_handles = {
            it.gaitem_handle for it in items
            if (it.gaitem_handle & 0xF0000000) == ITEM_TYPE_RELIC
        }
        phantoms = state_relic_handles - active

        # The fixture has 30 known phantom handles — if the fixture changes,
        # the exact count may shift, but there should always be some.
        assert len(phantoms) > 0, (
            "Expected phantom relics in the fixture save — "
            "if the fixture changed, update this test"
        )

        # None of those phantoms should appear in parse_relics output
        raw_relics, _ = parse_relics(userdata)
        returned_handles = {r.ga_handle for r in raw_relics}
        leaked = phantoms & returned_handles
        assert not leaked, (
            f"{len(leaked)} phantom handle(s) leaked into parse_relics output: "
            f"{[hex(h) for h in sorted(leaked)[:5]]}"
        )

    def test_entry_table_offset_sanity(self, userdata: bytes) -> None:
        """The ItemEntry table must start at a sane offset and its stored
        count must be plausible."""
        _, end_offset = _parse_items(userdata, start_offset=0x14, slot_count=5120)
        table_offset = end_offset + 0x94 + 0x5B8
        stored_count = struct.unpack_from("<I", userdata, table_offset)[0]

        # Stored count should be reasonable (not garbage)
        assert 1 <= stored_count <= 3065, (
            f"Stored entry count {stored_count} looks wrong — "
            f"possible offset miscalculation"
        )

        # Active count should be close to stored count (±small delta from
        # in-flight state is acceptable)
        active = _parse_active_handles(userdata, end_offset)
        assert abs(len(active) - stored_count) < 20, (
            f"Active handle count ({len(active)}) diverges too far from "
            f"stored count ({stored_count})"
        )


class TestOwnerSteamId:
    """The owning-account SteamID64 reader used to tell whether two save
    uploads came from the same account (drives the save-to-save comparison)."""

    VALID_SID = 76561198039949473  # the real fixture account

    @staticmethod
    def _profile_blob(value: int, *, length: int = 64) -> bytes:
        """A USERDATA_10-shaped blob with ``value`` packed at the owner anchor."""
        buf = bytearray(length)
        struct.pack_into("<Q", buf, PROFILE_ID_OFFSET, value)
        return bytes(buf)

    def test_is_steam_id_bounds(self) -> None:
        assert is_steam_id(STEAM_ID_BASE)
        assert is_steam_id(STEAM_ID_MAX)
        assert is_steam_id(self.VALID_SID)
        assert not is_steam_id(STEAM_ID_BASE - 1)
        assert not is_steam_id(STEAM_ID_MAX + 1)
        assert not is_steam_id(0)
        assert not is_steam_id(12345)

    def test_owner_from_blob_valid(self) -> None:
        assert owner_steam_id_from_blob(self._profile_blob(self.VALID_SID)) == self.VALID_SID

    def test_owner_from_blob_too_short(self) -> None:
        assert owner_steam_id_from_blob(b"\x00" * (PROFILE_ID_OFFSET + 4)) is None

    def test_owner_from_blob_non_steam_value(self) -> None:
        assert owner_steam_id_from_blob(self._profile_blob(42)) is None

    def test_read_ps4_is_none(self, tmp_path) -> None:
        # PS4 memory.dat uses a PSN account, not a SteamID64.
        (tmp_path / PROFILE_ENTRY_FILE).write_bytes(self._profile_blob(self.VALID_SID))
        assert read_owner_steam_id(tmp_path, mode="PS4") is None

    def test_read_missing_profile_entry(self, tmp_path) -> None:
        assert read_owner_steam_id(tmp_path, mode="PC") is None

    def test_read_reads_profile_entry_as_string(self, tmp_path) -> None:
        (tmp_path / PROFILE_ENTRY_FILE).write_bytes(self._profile_blob(self.VALID_SID))
        assert read_owner_steam_id(tmp_path, mode="PC") == str(self.VALID_SID)

    def test_read_non_steam_value_is_none(self, tmp_path) -> None:
        (tmp_path / PROFILE_ENTRY_FILE).write_bytes(self._profile_blob(7))
        assert read_owner_steam_id(tmp_path, mode="PC") is None


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present — copy NR0000.sl2 to backend/tests/fixtures/",
)
def test_read_owner_steam_id_real_fixture() -> None:
    """The reader recovers the real fixture account's SteamID64 end-to-end."""
    with tempfile.TemporaryDirectory() as tmpdir:
        decrypt_sl2(FIXTURE_PATH, tmpdir)
        assert read_owner_steam_id(tmpdir, mode="PC") == "76561198039949473"
