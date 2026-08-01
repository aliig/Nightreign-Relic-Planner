"""Required-row hard-constraint tests (optimizer v4).

Explicit required_effects/required_families must be enforced by the solvers:
every returned loadout covers them when the vessel can; vessels that can't
return best-effort loadouts flagged meets_requirements=False; covering
results rank above non-covering ones.

Synthetic effects are picked by distinct display NAME + stack-type ONLY —
filtering on conflict/exclusivity ids would starve the fixture (only 4 game
effects have conflict_id == -1).  All-stack fixtures make context scores a
plain sum of weights (no dedup, no conflict penalties), so totals are exact.
"""
import pytest

from nrplanner import BuildScorer, SourceDataHandler, VesselOptimizer
from nrplanner.models import (
    BuildDefinition, OwnedRelic, RelicInventory, VesselResult, WeightGroup,
)

EMPTY = 4294967295  # EMPTY_EFFECT sentinel

VESSEL = {
    "Colors": ("Red", "Red", "Red"),
    "Name": "Synth Vessel",
    "Character": "All",
    "unlockFlag": 0,
    "_id": 77,
}


def _pick_stack_effects(
    ds: SourceDataHandler, all_effects: list[dict], n: int, taken: set[str],
) -> list[int]:
    """n stack-type effects with distinct (and unseen) display names."""
    out: list[int] = []
    for e in all_effects:
        eid = e["id"]
        name = ds.get_effect_name(eid)
        if not name or name in ("", "Empty") or name.startswith("Effect "):
            continue
        if name in taken:
            continue
        if ds.get_effect_stacking_type(eid) != "stack":
            continue
        taken.add(name)
        out.append(eid)
        if len(out) == n:
            return out
    pytest.skip("not enough distinct stack-type effects in game data")


def _make_relic(effects: list[int], ga_handle: int,
                color: str = "Red") -> OwnedRelic:
    effects = (effects + [EMPTY] * 3)[:3]
    return OwnedRelic(
        ga_handle=ga_handle,
        item_id=100 + 2147483648,
        real_id=100,
        color=color,
        effects=effects,
        curses=[EMPTY, EMPTY, EMPTY],
        is_deep=False,
        name="Test Relic",
        tier="Delicate",
    )


def _assigned_effects(result: VesselResult) -> set[int]:
    out: set[int] = set()
    for a in result.assignments:
        if a.relic is not None:
            out.update(a.relic.all_effects)
    return out


@pytest.fixture(scope="module")
def optimizer(ds: SourceDataHandler) -> VesselOptimizer:
    return VesselOptimizer(ds, BuildScorer(ds))


@pytest.fixture(scope="module")
def effs(ds: SourceDataHandler, all_effects: list[dict]) -> dict:
    """One required effect R, six group effects H1..H6, one loss effect N."""
    taken: set[str] = set()
    picked = _pick_stack_effects(ds, all_effects, 8, taken)
    return {"R": picked[0], "H": picked[1:7], "N": picked[7]}


def _build(effs: dict, required: list[int] | None = None,
           required_families: list[str] | None = None,
           group_weight: int = 60, n_weight: int = 0,
           pinned: list[int] | None = None) -> BuildDefinition:
    groups = [WeightGroup(weight=group_weight, effects=effs["H"])]
    if n_weight:
        groups.append(WeightGroup(weight=n_weight, effects=[effs["N"]]))
    return BuildDefinition(
        id="req-test", name="Required Test", character="Wylder",
        groups=groups,
        required_effects=required or [],
        required_families=required_families or [],
        include_deep=False,
        curse_max=1,
        pinned_relics=pinned or [],
    )


class TestHardConstraint:
    def test_every_result_covers_required_effect(
        self, optimizer: VesselOptimizer, effs: dict,
    ) -> None:
        """Carrier (+100) must be included even though three non-carriers
        (+120 each) outscore it — the unconstrained optimum is not covering."""
        h = effs["H"]
        relics = [
            _make_relic([effs["R"]], 0xC0000001),          # carrier: +100
            _make_relic([h[0], h[1]], 0xC0000002),         # +120
            _make_relic([h[2], h[3]], 0xC0000003),         # +120
            _make_relic([h[4], h[5]], 0xC0000004),         # +120
        ]
        inventory = RelicInventory.from_owned_relics(relics)
        build = _build(effs, required=[effs["R"]])
        results = optimizer.optimize(build, inventory, dict(VESSEL))
        assert results, "expected covering results"
        for r in results:
            assert r.meets_requirements, r.missing_requirements
            assert effs["R"] in _assigned_effects(r)
        # Optimum: carrier (100) + best two non-carriers (120 + 120).
        assert max(r.total_score for r in results) == 340

    def test_uncoverable_vessel_returns_flagged_best_effort(
        self, optimizer: VesselOptimizer, effs: dict,
    ) -> None:
        h = effs["H"]
        relics = [
            _make_relic([h[0], h[1]], 0xC0000012),
            _make_relic([h[2], h[3]], 0xC0000013),
            _make_relic([h[4], h[5]], 0xC0000014),
        ]
        inventory = RelicInventory.from_owned_relics(relics)
        build = _build(effs, required=[effs["R"]])
        results = optimizer.optimize(build, inventory, dict(VESSEL))
        assert results, "best-effort results expected even when uncoverable"
        for r in results:
            assert not r.meets_requirements
            assert r.missing_requirements == [effs["R"]]
        # Best effort is still the unconstrained optimum.
        assert max(r.total_score for r in results) == 360

    def test_carrier_mandatory_at_net_loss(
        self, optimizer: VesselOptimizer, effs: dict,
    ) -> None:
        """A required carrier with negative context score must still be
        placed (backtracker ctx<=0 skip bypass + covering greedy seed)."""
        h = effs["H"]
        relics = [
            # carrier: R (+100) + N twice (-100 each) = -100 in context
            _make_relic([effs["R"], effs["N"], effs["N"]], 0xC0000021),
            _make_relic([h[0]], 0xC0000022),               # +60
            _make_relic([h[1]], 0xC0000023),               # +60
            _make_relic([h[2]], 0xC0000024),               # +60
        ]
        inventory = RelicInventory.from_owned_relics(relics)
        build = _build(effs, required=[effs["R"]], n_weight=-100)
        results = optimizer.optimize(build, inventory, dict(VESSEL))
        assert results, "expected covering results"
        for r in results:
            assert r.meets_requirements, r.missing_requirements
            assert effs["R"] in _assigned_effects(r)
        # Optimum: carrier (-100) + best two others (60 + 60).
        assert max(r.total_score for r in results) == 20

    def test_family_requirement_satisfied_by_any_member(
        self, optimizer: VesselOptimizer, ds: SourceDataHandler, effs: dict,
    ) -> None:
        family = None
        member = None
        for fam in ds.get_all_families_list():
            ids = ds.get_family_effect_ids(fam["name"])
            if ids:
                family = fam["name"]
                member = next(iter(ids))
                break
        if family is None:
            pytest.skip("no families with member effects in game data")
        h = effs["H"]
        relics = [
            _make_relic([member], 0xC0000031),
            _make_relic([h[0], h[1]], 0xC0000032),
            _make_relic([h[2], h[3]], 0xC0000033),
            _make_relic([h[4], h[5]], 0xC0000034),
        ]
        inventory = RelicInventory.from_owned_relics(relics)
        build = _build(effs, required_families=[family])
        results = optimizer.optimize(build, inventory, dict(VESSEL))
        assert results, "expected covering results"
        fam_ids = ds.get_family_effect_ids(family)
        for r in results:
            assert r.meets_requirements, r.missing_requirements
            assert _assigned_effects(r) & fam_ids

    def test_pinned_relic_counts_toward_coverage(
        self, optimizer: VesselOptimizer, effs: dict,
    ) -> None:
        """Pinned carrier covers the requirement; free-slot candidates don't
        carry it, so coverage must come from the pinned pre-assignment."""
        h = effs["H"]
        carrier_handle = 0xC0000041
        relics = [
            _make_relic([effs["R"]], carrier_handle),
            _make_relic([h[0], h[1]], 0xC0000042),
            _make_relic([h[2], h[3]], 0xC0000043),
        ]
        inventory = RelicInventory.from_owned_relics(relics)
        build = _build(effs, required=[effs["R"]], pinned=[carrier_handle])
        results = optimizer.optimize(build, inventory, dict(VESSEL))
        assert results, "expected covering results"
        for r in results:
            assert r.meets_requirements, r.missing_requirements
            handles = {a.relic.ga_handle for a in r.assignments if a.relic}
            assert carrier_handle in handles


class TestCoveringFirstRanking:
    @staticmethod
    def _vr(vid: int, score: int, meets: bool) -> VesselResult:
        return VesselResult(
            vessel_id=vid, vessel_name=f"V{vid}", vessel_character="All",
            unlock_flag=0, slot_colors=("Red", "Red", "Red"), assignments=[],
            total_score=score, meets_requirements=meets,
            missing_requirements=[] if meets else ["X"],
        )

    def test_dedup_rank_orders_covering_first(self) -> None:
        results = [
            self._vr(1, 100, False),
            self._vr(2, 50, True),
            self._vr(3, 200, True),
            self._vr(4, 10, False),
        ]
        ranked = VesselOptimizer._dedup_rank(results, top_n=10)
        assert [(r.vessel_id, r.meets_requirements) for r in ranked] == [
            (3, True), (2, True), (1, False), (4, False),
        ]

    def test_locked_slot_prefers_covering_replacement(
        self, optimizer: VesselOptimizer, effs: dict,
    ) -> None:
        """Strike re-fill: a covering candidate outranks a higher-scoring
        non-covering one."""
        h = effs["H"]
        b_handle, c_handle = 0xC0000051, 0xC0000052
        relics = [
            _make_relic([h[0], h[1]], b_handle),           # locked, +120
            _make_relic([h[2], h[3]], c_handle),           # locked, +120
            _make_relic([effs["R"]], 0xC0000053),          # covering, +100
            _make_relic([h[4], h[5]], 0xC0000054),         # non-covering, +120
        ]
        inventory = RelicInventory.from_owned_relics(relics)
        build = _build(effs, required=[effs["R"]])
        results = optimizer.optimize_locked_slot(
            build, inventory, dict(VESSEL),
            locked={0: b_handle, 1: c_handle}, struck_slot_index=2, top_n=3)
        assert results
        top = results[0]
        assert top.meets_requirements
        assert effs["R"] in _assigned_effects(top)
        # The higher-scoring non-covering alternative ranks below it.
        assert any(
            not r.meets_requirements and r.total_score > top.total_score
            for r in results[1:]
        )
