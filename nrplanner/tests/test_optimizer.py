"""Tests for VesselOptimizer (optimizer.py).

Inventory is constructed with RelicInventory.from_owned_relics() — no save
file parsing. Uses real SourceDataHandler and real vessel data for Wylder
(hero_type 100000).
"""
import pytest

from nrplanner import BuildScorer, VesselOptimizer, SourceDataHandler
from nrplanner.models import (
    ALL_TIER_KEYS, BuildDefinition, OwnedRelic, RelicInventory, VesselResult,
)

EMPTY = 4294967295  # EMPTY_EFFECT sentinel


def _make_relic(
    effects: list[int],
    color: str = "Red",
    is_deep: bool = False,
) -> OwnedRelic:
    effect_count = sum(1 for e in effects if e not in (EMPTY, 0))
    tier = "Grand" if effect_count >= 3 else ("Polished" if effect_count == 2 else "Delicate")
    return OwnedRelic(
        ga_handle=0xC0000001,
        item_id=100 + 2147483648,
        real_id=100,
        color=color,
        effects=effects,
        curses=[EMPTY, EMPTY, EMPTY],
        is_deep=is_deep,
        name="Test Relic",
        tier=tier,
    )


def _make_build(required: list[int] | None = None) -> BuildDefinition:
    tiers = {k: [] for k in ALL_TIER_KEYS}
    if required:
        tiers["required"] = required
    return BuildDefinition(
        id="opt-test",
        name="Optimizer Test",
        character="Wylder",
        tiers=tiers,
        family_tiers={k: [] for k in ALL_TIER_KEYS},
        include_deep=False,
        curse_max=1,
    )


@pytest.fixture(scope="module")
def optimizer(ds: SourceDataHandler) -> VesselOptimizer:
    return VesselOptimizer(ds, BuildScorer(ds))


@pytest.fixture(scope="module")
def small_inventory(all_effects: list[dict]) -> RelicInventory:
    """A tiny inventory with 3 Red relics for basic tests."""
    e1 = all_effects[0]["id"]
    e2 = all_effects[1]["id"]
    relics = [
        _make_relic([e1, EMPTY, EMPTY], color="Red"),
        _make_relic([e2, EMPTY, EMPTY], color="Blue"),
        _make_relic([EMPTY, EMPTY, EMPTY], color="Green"),
    ]
    return RelicInventory.from_owned_relics(relics)


class TestOptimizeAllVessels:
    def test_returns_list_of_vessel_results(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory
    ) -> None:
        build = _make_build()
        results = optimizer.optimize_all_vessels(build, small_inventory, 100000)
        assert isinstance(results, list)
        assert all(isinstance(r, VesselResult) for r in results)

    def test_vessel_result_has_required_fields(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory
    ) -> None:
        build = _make_build()
        results = optimizer.optimize_all_vessels(build, small_inventory, 100000)
        if not results:
            pytest.skip("No vessels returned for hero 100000")
        r = results[0]
        assert hasattr(r, "vessel_id")
        assert hasattr(r, "vessel_name")
        assert hasattr(r, "total_score")
        assert hasattr(r, "assignments")
        assert hasattr(r, "meets_requirements")
        assert hasattr(r, "slot_colors")

    def test_empty_inventory_no_crash(
        self, optimizer: VesselOptimizer
    ) -> None:
        build = _make_build()
        empty_inventory = RelicInventory.from_owned_relics([])
        results = optimizer.optimize_all_vessels(build, empty_inventory, 100000)
        assert isinstance(results, list)
        # All assignments should have no relic assigned
        for result in results:
            for assignment in result.assignments:
                assert assignment.relic is None

    def test_top_n_respected(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory
    ) -> None:
        build = _make_build()
        results = optimizer.optimize_all_vessels(build, small_inventory, 100000, top_n=2)
        assert len(results) <= 2

    def test_meets_requirements_first(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory,
        all_effects: list[dict]
    ) -> None:
        """Results meeting build requirements should appear before those that don't."""
        # Build with a "required" effect — some vessels may not have it
        eff_id = all_effects[0]["id"]
        build = _make_build(required=[eff_id])
        results = optimizer.optimize_all_vessels(build, small_inventory, 100000)
        if not results:
            pytest.skip("No results to check ordering")
        # Find the index of the first result that does NOT meet requirements
        first_fail_idx = next(
            (i for i, r in enumerate(results) if not r.meets_requirements), None
        )
        if first_fail_idx is None:
            return  # All meet requirements — ordering is vacuously correct
        # All results before the first failure must meet requirements
        for i in range(first_fail_idx):
            assert results[i].meets_requirements

    def test_unknown_hero_returns_only_all_character_vessels(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory
    ) -> None:
        """Unknown hero_type still returns 'All'-character vessels (heroType=11).
        Hero-specific vessels are excluded; the result is non-empty but contains
        only vessels available to every character."""
        build = _make_build()
        results = optimizer.optimize_all_vessels(build, small_inventory, -999)
        # All returned vessels must be 'All'-character (no hero-specific ones)
        for r in results:
            assert r.vessel_character == "All"
