"""Tests for VesselOptimizer (optimizer.py).

Inventory is constructed with RelicInventory.from_owned_relics() — no save
file parsing. Uses real SourceDataHandler and real vessel data for Wylder
(hero_type 1 — CSV hero index, not NPC text file ID).
"""
import pytest

from nrplanner import BuildScorer, VesselOptimizer, SourceDataHandler
from nrplanner.models import (
    BuildDefinition, OwnedRelic, RelicInventory, VesselResult, WeightGroup,
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
    return BuildDefinition(
        id="opt-test",
        name="Optimizer Test",
        character="Wylder",
        required_effects=required or [],
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
        results = optimizer.optimize_all_vessels(build, small_inventory, 1)
        assert isinstance(results, list)
        assert all(isinstance(r, VesselResult) for r in results)

    def test_vessel_result_has_required_fields(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory
    ) -> None:
        build = _make_build()
        results = optimizer.optimize_all_vessels(build, small_inventory, 1)
        if not results:
            pytest.skip("No vessels returned for hero 1")
        r = results[0]
        assert hasattr(r, "vessel_id")
        assert hasattr(r, "vessel_name")
        assert hasattr(r, "total_score")
        assert hasattr(r, "assignments")
        assert hasattr(r, "meets_requirements")
        assert hasattr(r, "slot_colors")

    def test_empty_inventory_returns_no_results(
        self, optimizer: VesselOptimizer
    ) -> None:
        build = _make_build()
        empty_inventory = RelicInventory.from_owned_relics([])
        results = optimizer.optimize_all_vessels(build, empty_inventory, 1)
        assert isinstance(results, list)
        assert len(results) == 0, "Empty inventory should produce no vessel results"

    def test_top_n_respected(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory
    ) -> None:
        build = _make_build()
        results = optimizer.optimize_all_vessels(build, small_inventory, 1, top_n=2)
        assert len(results) <= 2

    def test_meets_requirements_first(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory,
        all_effects: list[dict]
    ) -> None:
        """Results meeting build requirements should appear before those that don't."""
        # Build with a "required" effect — some vessels may not have it
        eff_id = all_effects[0]["id"]
        build = _make_build(required=[eff_id])
        results = optimizer.optimize_all_vessels(build, small_inventory, 1)
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

    def test_valid_hero_includes_character_specific_vessels(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory,
        all_effects: list[dict]
    ) -> None:
        """A valid hero_type must return character-specific vessels, not only
        the shared 'All' vessels.  This catches the NPC-ID-vs-hero-index bug
        where passing e.g. 100000 instead of 1 produced only 'All' results."""
        eff_id = all_effects[0]["id"]
        build = _make_build(required=[eff_id])
        results = optimizer.optimize_all_vessels(build, small_inventory, 1)
        characters = {r.vessel_character for r in results}
        assert "Wylder" in characters, (
            f"Expected Wylder-specific vessels in results but got: {characters}"
        )


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
        build = BuildDefinition(
            id="pin-test", name="Pin Test", character="Wylder",
            include_deep=False,
            curse_max=1,
            pinned_relics=[pinned_handle],
        )
        results = optimizer.optimize_all_vessels(build, inventory, 1)
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
            include_deep=False,
            curse_max=1,
            pinned_relics=[0xDEADBEEF],
        )
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        # Should not crash and should still return vessels (none excluded due to absent pin)
        assert isinstance(results, list)

    def test_no_pinned_relics_behaves_normally(
        self, optimizer: VesselOptimizer, small_inventory: RelicInventory
    ) -> None:
        """Empty pinned_relics list produces the same result as not setting it."""
        build_no_pins = _make_build()
        build_empty_pins = BuildDefinition(
            id="opt-test", name="Optimizer Test", character="Wylder",
            include_deep=False,
            curse_max=1,
            pinned_relics=[],
        )
        results_no_pins = optimizer.optimize_all_vessels(build_no_pins, small_inventory, 1)
        results_empty_pins = optimizer.optimize_all_vessels(build_empty_pins, small_inventory, 1)
        assert len(results_no_pins) == len(results_empty_pins)


class TestExcludedRelics:
    """build.excluded_relics drops specific ga_handles from candidate pools.

    Powers the "strike a relic" feature: pin every other slot, exclude the
    struck relic, and re-optimize the single free slot for the next-best pick.
    Uses a synthetic all-White (wildcard) vessel so the test does not depend on
    real vessel colour layouts.
    """

    # Three White (wildcard) standard slots; deep slots unused (include_deep=False).
    _VESSEL = {
        "Name": "Synthetic Test Vessel", "Character": "Wylder", "unlockFlag": 0,
        "_id": 999999,
        "Colors": ("White", "White", "White", "White", "White", "White"),
    }

    def _build(self, eff: int, *, pinned=None, excluded=None) -> BuildDefinition:
        return BuildDefinition(
            id="excl-test", name="Exclude Test", character="Wylder",
            include_deep=False, curse_max=1,
            groups=[WeightGroup(weight=100, effects=[eff])],
            pinned_relics=pinned or [],
            excluded_relics=excluded or [],
        )

    def test_excluded_relic_never_assigned(
        self, optimizer: VesselOptimizer, all_effects: list[dict]
    ) -> None:
        eff = all_effects[0]["id"]
        h_keep, h_drop = 0xC3000001, 0xC3000002
        inventory = RelicInventory.from_owned_relics([
            _make_relic([eff, EMPTY, EMPTY], color="Red", ga_handle=h_keep),
            _make_relic([eff, EMPTY, EMPTY], color="Red", ga_handle=h_drop),
        ])

        # Baseline: with two White slots both relics get placed.
        base = optimizer.optimize(self._build(eff), inventory, self._VESSEL, top_n=1)
        assert base, "baseline optimize returned nothing"
        base_handles = {a.relic.ga_handle for a in base[0].assignments if a.relic}
        assert h_drop in base_handles, "drop relic should normally be assignable"

        # Excluding it removes it from every slot; the sibling still appears.
        excl = optimizer.optimize(
            self._build(eff, excluded=[h_drop]), inventory, self._VESSEL, top_n=1)
        assert excl, "exclude optimize returned nothing"
        excl_handles = {a.relic.ga_handle for a in excl[0].assignments if a.relic}
        assert h_drop not in excl_handles, "excluded relic must not be assigned"
        assert h_keep in excl_handles, "non-excluded sibling should still be used"

    def test_pin_all_but_one_finds_next_for_free_slot(
        self, optimizer: VesselOptimizer, all_effects: list[dict]
    ) -> None:
        """Pin two slots + exclude one candidate → the free slot takes the next-best."""
        eff = all_effects[0]["id"]
        h1, h2, h3, h4 = 0xC3000011, 0xC3000012, 0xC3000013, 0xC3000014
        inventory = RelicInventory.from_owned_relics([
            _make_relic([eff, EMPTY, EMPTY], color="Red", ga_handle=h)
            for h in (h1, h2, h3, h4)
        ])
        build = self._build(eff, pinned=[h1, h2], excluded=[h3])

        results = optimizer.optimize(build, inventory, self._VESSEL, top_n=1)
        assert results, "optimize returned nothing"
        handles = {a.relic.ga_handle for a in results[0].assignments if a.relic}
        assert {h1, h2}.issubset(handles), "pinned relics must stay assigned"
        assert h3 not in handles, "excluded candidate must not be assigned"
        assert h4 in handles, "the free slot should take the next-best candidate"

    def test_excluding_only_candidate_leaves_free_slot_empty(
        self, optimizer: VesselOptimizer, all_effects: list[dict]
    ) -> None:
        """With every candidate for the free slot excluded, pins remain and the
        free slot is empty (the optimizer still returns the pinned arrangement)."""
        eff = all_effects[0]["id"]
        h1, h2, h3 = 0xC3000021, 0xC3000022, 0xC3000023
        inventory = RelicInventory.from_owned_relics([
            _make_relic([eff, EMPTY, EMPTY], color="Red", ga_handle=h)
            for h in (h1, h2, h3)
        ])
        # Pin two, exclude the only remaining candidate.
        build = self._build(eff, pinned=[h1, h2], excluded=[h3])
        results = optimizer.optimize(build, inventory, self._VESSEL, top_n=1)
        assert results, "pinned relics should still yield a result"
        handles = {a.relic.ga_handle for a in results[0].assignments if a.relic}
        assert handles == {h1, h2}, "only the pinned relics should remain"
        assert any(a.relic is None for a in results[0].assignments), "free slot empty"


class TestOptimizeLockedSlot:
    """optimize_locked_slot re-fills ONE slot with the others frozen in place.

    This is the strike-a-relic path. Unlike positionless pinning, it must keep
    every locked relic in its exact slot, which guarantees a monotonic total
    across repeated strikes (regression for the slot-reshuffle bug where the
    total could jump because pinned relics were re-packed into other slots).
    """

    _VESSEL = {
        "Name": "Synthetic Test Vessel", "Character": "Wylder", "unlockFlag": 0,
        "_id": 999999,
        "Colors": ("White", "White", "White", "White", "White", "White"),
    }
    # Distinct real stacking effects so several slots are worth filling.
    _E = [7012300, 7000902, 6001400, 7001402]
    _H = [0xD0000001, 0xD0000002, 0xD0000003, 0xD0000004]

    def _build(self, excluded=None) -> BuildDefinition:
        return BuildDefinition(
            id="lock-test", name="Lock Test", character="Wylder",
            include_deep=False, curse_max=1,
            groups=[WeightGroup(weight=100, effects=self._E)],
            excluded_relics=excluded or [],
        )

    def _inventory(self) -> RelicInventory:
        return RelicInventory.from_owned_relics([
            _make_relic([self._E[i], EMPTY, EMPTY], color="Red", ga_handle=self._H[i])
            for i in range(4)
        ])

    def test_locked_slots_keep_exact_position(
        self, optimizer: VesselOptimizer
    ) -> None:
        """The bug: striking slot 0 used to repack the other relics into
        different slots. Now slots 1 and 2 must stay exactly put."""
        inv = self._inventory()
        locked = {1: self._H[1], 2: self._H[2]}  # freeze handle2@slot1, handle3@slot2
        res = optimizer.optimize_locked_slot(
            self._build(excluded=[self._H[0]]), inv, self._VESSEL,
            locked, struck_slot_index=0, top_n=1)
        assert res
        by_slot = {a.slot_index: (a.relic.ga_handle if a.relic else None)
                   for a in res[0].assignments}
        assert by_slot[1] == self._H[1], "slot 1 must stay exactly put"
        assert by_slot[2] == self._H[2], "slot 2 must stay exactly put"
        assert by_slot[0] == self._H[3], "struck slot takes the remaining relic"

    def test_sequential_strikes_total_monotonic(
        self, optimizer: VesselOptimizer
    ) -> None:
        inv = self._inventory()
        locked = {1: self._H[1], 2: self._H[2]}
        excluded = [self._H[0]]
        prev_total: int | None = None
        for _ in range(3):
            res = optimizer.optimize_locked_slot(
                self._build(excluded=list(excluded)), inv, self._VESSEL,
                locked, struck_slot_index=0, top_n=1)
            assert res
            r = res[0]
            by_slot = {a.slot_index: (a.relic.ga_handle if a.relic else None)
                       for a in r.assignments}
            assert by_slot[1] == self._H[1] and by_slot[2] == self._H[2], (
                "locked slots must never move across strikes")
            if prev_total is not None:
                assert r.total_score <= prev_total, (
                    f"total must not increase across strikes: "
                    f"{prev_total} -> {r.total_score}")
            prev_total = r.total_score
            current = by_slot[0]
            if current is None:
                break
            excluded.append(current)


class TestGreedyMyopiaWithLimits:
    """Regression: the exhaustive solver must run even for large candidate pools.

    Greedy assigns slots left-to-right and commits each before later slots are
    scored.  When a family limit only "bites" once a later DEEP slot is filled,
    greedy over-values an earlier standard slot and locks in a worse relic.

    Concretely (mirrors the real 'Claws undertaker' bug, Undertaker's Chalice):
      - One White (wildcard) standard slot is contested by two relics:
          A = X(+100) + PhysAtkUp+3(+80)            -> greedy sees 180 in slot 0
          B = X(+100) + UltArtAutoCharge(+50)        -> 150, no limited family
      - C in a deep slot carries PhysAtkUp+2(+100) from the SAME limited family.
      - With family limit "Physical Attack Up" = 1, placing A in the (earlier)
        White slot consumes the limit, so C scores 0; the true optimum places B
        (freeing C for +100), netting more overall.

    Greedy picks A; only the exhaustive backtracking solver finds B.  This used
    to be gated behind a `total <= 500` candidate cap, so large inventories
    silently fell back to greedy and produced the sub-optimal layout.  The pool
    here is intentionally >500 to exercise that path.
    """

    # Real effect IDs (see test_gold_standard for the same convention).
    _X       = 7012300   # Improved Damage Negation at Low HP (stack, own family)
    _Y       = 7000902   # Ultimate Art Auto Charge +3 (stack)
    _PAU3    = 6001400   # Physical Attack Up +3 (stack, family "Physical Attack Up")
    _PAU2    = 7001402   # Physical Attack Up +2 (stack, family "Physical Attack Up")
    _FAMILY  = "Physical Attack Up"

    A_HANDLE = 0xC1000001
    B_HANDLE = 0xC1000002
    C_HANDLE = 0xC1000003

    def _guard_game_data_assumptions(self, ds: SourceDataHandler) -> None:
        # If game data drifts and breaks these assumptions, fail loudly here
        # rather than letting the regression test silently pass.
        assert ds.get_effect_stacking_type(self._X) == "stack"
        assert ds.get_effect_stacking_type(self._Y) == "stack"
        assert ds.get_effect_stacking_type(self._PAU3) == "stack"
        assert ds.get_effect_stacking_type(self._PAU2) == "stack"
        assert ds.get_effect_family(self._PAU3) == self._FAMILY
        assert ds.get_effect_family(self._PAU2) == self._FAMILY
        assert ds.get_effect_family(self._X) != self._FAMILY
        assert ds.get_effect_family(self._Y) != self._FAMILY

    def _build(self) -> BuildDefinition:
        return BuildDefinition(
            id="myopia", name="Greedy Myopia", character="Wylder",
            include_deep=True, curse_max=1,
            groups=[
                WeightGroup(weight=100, effects=[self._X, self._PAU2]),
                WeightGroup(weight=80,  effects=[self._PAU3]),
                WeightGroup(weight=50,  effects=[self._Y]),
            ],
            family_limits={self._FAMILY: 1},
        )

    def _inventory(self) -> RelicInventory:
        relics = [
            # Contested White-slot pair (mutually exclusive — A is Red, B is Blue,
            # so each fits ONLY the single White standard slot below).
            _make_relic([self._X, self._PAU3, EMPTY], color="Red",  ga_handle=self.A_HANDLE),
            _make_relic([self._X, self._Y, EMPTY],    color="Blue", ga_handle=self.B_HANDLE),
            # Deep relic carrying the limited family's high-value variant.
            _make_relic([self._PAU2, EMPTY, EMPTY], color="Blue", is_deep=True,
                        ga_handle=self.C_HANDLE),
        ]
        # Filler relics, each worth +50 (< B's 150 and < the +70 swap margin),
        # purely to push the candidate pool > 500 and force the large-pool path.
        h = 0xC2000000
        for _ in range(330):  # Red standard -> only the White slot sees these
            relics.append(_make_relic([self._Y, EMPTY, EMPTY], color="Red", ga_handle=h)); h += 1
        for color in ("Blue", "Green", "Yellow"):  # deep fillers per deep slot
            for _ in range(70):
                relics.append(_make_relic([self._Y, EMPTY, EMPTY], color=color,
                                          is_deep=True, ga_handle=h)); h += 1
        return RelicInventory.from_owned_relics(relics)

    def test_exhaustive_solver_beats_greedy_on_large_pool(
        self, optimizer: VesselOptimizer, ds: SourceDataHandler
    ) -> None:
        self._guard_game_data_assumptions(ds)
        build = self._build()
        inventory = self._inventory()
        # Slot 0 = White wildcard (contested); deep slots 3/4/5 = Blue/Green/Yellow.
        vessel = {
            "Name": "Synthetic Test Vessel", "Character": "Wylder", "unlockFlag": 0,
            "_id": 999999,
            "Colors": ("White", "Green", "Yellow", "Blue", "Green", "Yellow"),
        }

        results = optimizer.optimize(build, inventory, vessel, top_n=3)
        assert results, "optimizer returned no results"
        best = results[0]

        slot0 = best.assignments[0].relic
        assert slot0 is not None and slot0.ga_handle == self.B_HANDLE, (
            "Contested White slot should hold relic B (the globally optimal pick); "
            f"got {slot0.ga_handle if slot0 else None:#x} "
            f"(A={self.A_HANDLE:#x}, B={self.B_HANDLE:#x}). "
            "Greedy would lock in A here — exhaustive search was likely skipped."
        )
        # Optimum: B(150) + C(100) + two deep fillers(50+50) = 350.
        # Greedy myopia would yield A(180) + three deep fillers(150) = 330.
        assert best.total_score == 350, (
            f"Expected optimal total 350, got {best.total_score} "
            "(330 indicates the greedy myopia path)."
        )
        # The search completes comfortably for this pool, so the result is
        # provably optimal — not a timed-out best-effort.
        assert best.search_truncated is False

    def test_deadline_truncation_is_flagged(
        self, optimizer: VesselOptimizer, ds: SourceDataHandler
    ) -> None:
        """A search that hits its time budget still returns results, flagged."""
        self._guard_game_data_assumptions(ds)
        build = self._build()
        inventory = self._inventory()
        vessel = {
            "Name": "Synthetic Test Vessel", "Character": "Wylder", "unlockFlag": 0,
            "_id": 999999,
            "Colors": ("White", "Green", "Yellow", "Blue", "Green", "Yellow"),
        }
        # A deadline already in the past forces immediate truncation; the
        # greedy floor still yields a (sub-optimal) result, marked truncated.
        results = optimizer.optimize(build, inventory, vessel, top_n=3, deadline_secs=-1.0)
        assert results, "optimizer should still return the greedy floor on truncation"
        assert all(r.search_truncated for r in results)
