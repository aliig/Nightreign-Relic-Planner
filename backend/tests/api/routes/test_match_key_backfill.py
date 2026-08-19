"""The migration that recovers top_match_keys from stored results.

Snapshots written before the top_match_keys column existed left the builds-page
"your save is suggestion #N" badge permanently silent — a snapshot only
re-optimizes when its inputs go stale, so "it refills on the next optimize" was
never going to happen for a build the user had already run.  Migration
d2e3f4a5b6c7 derives the keys from full_results instead.

The derivation is a hand-written mirror of nrplanner.changes.result_match_key
over serialized results (a migration must not depend on live model classes), so
what needs pinning is that the two agree — a silent drift would fill the column
with keys that match nothing.
"""
import importlib.util
from pathlib import Path

from nrplanner.changes import result_match_key
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import OwnedRelic, SlotAssignment, VesselResult

EMPTY = EMPTY_EFFECT

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "app" / "alembic" / "versions" / "d2e3f4a5b6c7_backfill_top_match_keys.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_backfill_keys", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _relic(real_id: int, effect: int) -> OwnedRelic:
    return OwnedRelic(
        ga_handle=0xC0000000 + real_id,
        item_id=real_id + 2147483648,
        real_id=real_id,
        color="Red",
        effects=[effect, EMPTY, EMPTY],
        curses=[EMPTY, EMPTY, EMPTY],
        is_deep=False,
        name=f"Relic {real_id}",
        tier="Delicate",
    )


def _result(*relics: OwnedRelic | None) -> VesselResult:
    return VesselResult(
        vessel_id=42,
        vessel_name="Test Vessel",
        vessel_character="Raider",
        unlock_flag=0,
        slot_colors=["Red"] * len(relics),
        total_score=100,
        assignments=[
            SlotAssignment(
                slot_index=i,
                slot_color="Red",
                is_deep=False,
                relic=r,
                score=0,
                breakdown=[],
            )
            for i, r in enumerate(relics)
        ],
    )


class TestBackfilledKeyMatchesTheLibrary:
    def test_agrees_on_a_full_vessel(self) -> None:
        migration = _load_migration()
        result = _result(_relic(1, 100), _relic(2, 200), _relic(3, 300))
        assert migration._match_key(
            result.model_dump(mode="json")
        ) == result_match_key(result)

    def test_agrees_when_slots_are_empty(self) -> None:
        """Empty slots carry no relic; both sides must skip them identically or
        every partially-filled vessel gets an unmatchable key."""
        migration = _load_migration()
        result = _result(_relic(1, 100), None, _relic(3, 300))
        assert migration._match_key(
            result.model_dump(mode="json")
        ) == result_match_key(result)

    def test_agrees_with_duplicate_relic_copies(self) -> None:
        migration = _load_migration()
        result = _result(_relic(1, 100), _relic(1, 100), _relic(2, 200))
        assert migration._match_key(
            result.model_dump(mode="json")
        ) == result_match_key(result)

    def test_different_arrangements_stay_distinguishable(self) -> None:
        migration = _load_migration()
        a = _result(_relic(1, 100), _relic(2, 200))
        b = _result(_relic(1, 100), _relic(3, 300))
        assert migration._match_key(a.model_dump(mode="json")) != migration._match_key(
            b.model_dump(mode="json")
        )
