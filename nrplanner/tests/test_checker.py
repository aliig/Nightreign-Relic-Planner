"""Tests for RelicChecker (checker.py).

Uses the real SourceDataHandler. Valid relic/effect IDs are derived
at runtime from loaded game data to stay valid across patches.
"""
import pytest

from nrplanner import InvalidReason, RelicChecker, SourceDataHandler

EMPTY = 4294967295  # EMPTY_EFFECT sentinel


@pytest.fixture(scope="module")
def checker(ds: SourceDataHandler) -> RelicChecker:
    return RelicChecker(ga_relic=[], data_source=ds)


class TestCheckInvalidity:
    def test_all_empty_effects_returns_none(
        self, checker: RelicChecker, safe_relic_ids: list[int]
    ) -> None:
        """A valid relic with all-empty effect slots is always valid."""
        relic_id = safe_relic_ids[0]
        result = checker.check_invalidity(relic_id, [EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY])
        assert result == InvalidReason.NONE

    def test_illegal_range_returns_in_illegal_range(
        self, checker: RelicChecker
    ) -> None:
        # 20001 is within the "illegal" range (20000–30035)
        result = checker.check_invalidity(20001, [EMPTY] * 6)
        assert result == InvalidReason.IN_ILLEGAL_RANGE

    def test_out_of_range_relic_returns_invalid_item(
        self, checker: RelicChecker
    ) -> None:
        # 50 is below RELIC_RANGE (100, 2013322)
        result = checker.check_invalidity(50, [EMPTY] * 6)
        assert result == InvalidReason.INVALID_ITEM

    def test_effect_not_in_rollable_pool(
        self, checker: RelicChecker, safe_relic_ids: list[int],
        ds: SourceDataHandler, all_effects: list[dict]
    ) -> None:
        """An effect that doesn't belong to the relic's pool is rejected."""
        relic_id = safe_relic_ids[0]
        pools = ds.get_relic_pools_seq(relic_id)

        # Find a pool that actually has rollable effects
        valid_pool = next((p for p in pools[:3] if p != -1), None)
        if valid_pool is None:
            pytest.skip("No active effect pools for first safe relic")

        valid_effects = set(ds.get_pool_rollable_effects(valid_pool))

        # Find an effect not in this pool
        bad_effect = next(
            (e["id"] for e in all_effects if e["id"] not in valid_effects),
            None,
        )
        if bad_effect is None:
            pytest.skip("Could not find an effect outside the relic's pool")

        result = checker.check_invalidity(relic_id, [bad_effect, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY])
        assert result == InvalidReason.EFF_NOT_IN_ROLLABLE_POOL

    def test_return_1st_invalid_idx_gives_tuple(
        self, checker: RelicChecker
    ) -> None:
        result = checker.check_invalidity(50, [EMPTY] * 6, return_1st_invalid_idx=True)
        assert isinstance(result, tuple)
        reason, idx = result
        assert isinstance(reason, InvalidReason)
        assert isinstance(idx, int)


class TestFindIdRange:
    def test_known_id_returns_range(self, checker: RelicChecker) -> None:
        result = checker.find_id_range(100)
        assert result is not None
        name, (lo, hi) = result
        assert name == "store_102"
        assert lo <= 100 <= hi

    def test_unknown_id_returns_none(self, checker: RelicChecker) -> None:
        assert checker.find_id_range(99999) is None


class TestIsDeepRelic:
    def test_deep_relic_true(self) -> None:
        assert RelicChecker.is_deep_relic(2000000) is True

    def test_standard_relic_false(self) -> None:
        assert RelicChecker.is_deep_relic(100) is False
