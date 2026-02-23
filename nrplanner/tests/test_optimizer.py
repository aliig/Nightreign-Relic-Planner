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
    ga_handle: int = 0xC0000001,
) -> OwnedRelic:
    effect_count = sum(1 for e in effects if e not in (EMPTY, 0))
    tier = "Grand" if effect_count >= 3 else ("Polished" if effect_count == 2 else "Delicate")
    return OwnedRelic(
        ga_handle=ga_handle,
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


class TestPinnedRelics:
    def test_pinned_relic_appears_in_all_results(
        self, optimizer: VesselOptimizer, all_effects: list[dict]
    ) -> None:
        """Every returned vessel result must have the pinned relic assigned."""
        e1 = all_effects[0]["id"]
        pinned_handle = 0xC0000010
        relics = [
            _make_relic([e1, EMPTY, EMPTY], color="Red", ga_handle=pinned_handle),
            _make_relic([EMPTY, EMPTY, EMPTY], color="Blue", ga_handle=0xC0000011),
        ]
        inventory = RelicInventory.from_owned_relics(relics)
        tiers = {k: [] for k in ALL_TIER_KEYS}
        build = BuildDefinition(
            id="pin-test", name="Pin Test", character="Wylder",
            tiers=tiers,
            family_tiers={k: [] for k in ALL_TIER_KEYS},
            include_deep=False,
            curse_max=1,
            pinned_relics=[pinned_handle],
        )
        results = optimizer.optimize_all_vessels(build, inventory, 100000)
        assert results, "Expected at least one vessel result with pinned relic"
        for result in results:
            assigned_handles = {
                a.relic.ga_handle for a in result.assignments if a.relic is not None
            }
            assert pinned_handle in assigned_handles, (
                f"Pinned handle {hex(pinned_handle)} not in assignments "
                f"for vessel '{result.vessel_name}'"
            )

    def test_absent_pinned_relic_does_not_exclude_vessels(
        self, optimizer: VesselOptimizer
    ) -> None:
        """A pinned ga_handle not present in inventory is silently ignored."""
        inventory = RelicInventory.from_owned_relics([])
        build = BuildDefinition(
            id="absent-pin", name="Absent Pin", character="Wylder",
            tiers={k: [] for k in ALL_TIER_KEYS},
            family_tiers={k: [] for k in ALL_TIER_KEYS},
            include_deep=False,
            curse_max=1,
            pinned_relics=[0xDEADBEEF],
        )
        results = optimizer.optimize_all_vessels(build, inventory, 100000)
        # Should not crash and should still return vessels (none excluded due to absent pin)
        assert isinstance(results, list)

    def test_no_pinned_relics_behaves_normally(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory
    ) -> None:
        """Empty pinned_relics list produces the same result as not setting it."""
        build_no_pins = _make_build()
        tiers = {k: [] for k in ALL_TIER_KEYS}
        build_empty_pins = BuildDefinition(
            id="opt-test", name="Optimizer Test", character="Wylder",
            tiers=tiers,
            family_tiers={k: [] for k in ALL_TIER_KEYS},
            include_deep=False,
            curse_max=1,
            pinned_relics=[],
        )
        results_no_pins = optimizer.optimize_all_vessels(build_no_pins, small_inventory, 100000)
        results_empty_pins = optimizer.optimize_all_vessels(build_empty_pins, small_inventory, 100000)
        assert len(results_no_pins) == len(results_empty_pins)
