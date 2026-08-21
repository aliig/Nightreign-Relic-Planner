"""Vessel-level reuse must be indistinguishable from a full re-optimization.

A relic can only sit in a slot whose colour and deep-ness it matches, so an
upload that adds (say) one yellow standard relic cannot move any vessel without
a yellow-or-white standard slot.  Those vessels keep their cached results and
are never re-solved.

That is only sound while the diff is ADDITIONS ONLY, because the cache is the
snapshot's GLOBAL top-N, not a per-vessel table: untouched vessels keep their
exact scores and re-run vessels can only score higher, so nothing that missed
the old cut can climb into the new one.  A removal can lower a re-run vessel
and promote a layout the snapshot no longer holds — the route falls back to a
full run there, and ``test_a_removal_is_not_reusable`` documents the failure it
would otherwise cause.

The parity test is the one that matters: partial and full runs must return the
same ranked results, on real game data, for every colour.
"""
import pytest

from nrplanner import BuildScorer, SourceDataHandler, VesselOptimizer
from nrplanner.changes import vessel_accepts, vessels_needing_rerun
from nrplanner.models import (
    BuildDefinition,
    OwnedRelic,
    RelicInventory,
    VesselResult,
    WeightGroup,
)

EMPTY = 4294967295
COLORS = ("Red", "Blue", "Yellow", "Green")
WYLDER = 1


def _relic(handle: int, effects: list[int], color: str,
           is_deep: bool = False) -> OwnedRelic:
    effects = (effects + [EMPTY, EMPTY, EMPTY])[:3]
    return OwnedRelic(
        ga_handle=handle,
        item_id=handle + 2147483648,
        real_id=100 + (handle % 50),
        color=color,
        effects=effects,
        curses=[EMPTY, EMPTY, EMPTY],
        is_deep=is_deep,
        name=f"Relic {handle}",
        tier="Delicate",
    )


@pytest.fixture(scope="module")
def optimizer(ds: SourceDataHandler) -> VesselOptimizer:
    return VesselOptimizer(ds, BuildScorer(ds))


@pytest.fixture(scope="module")
def wanted_effects(ds: SourceDataHandler) -> list[int]:
    """A handful of ordinary, positively-scoring effect IDs."""
    ids = [
        e["id"] for e in ds.get_all_effects_list()
        if ds.get_effect_family(e["id"]) is not None
    ]
    return ids[:6]


def _build(effects: list[int], include_deep: bool = True) -> BuildDefinition:
    return BuildDefinition(
        id="reuse-test", name="Reuse Test", character="Wylder",
        groups=[WeightGroup(weight=50, effects=effects)],
        include_deep=include_deep, curse_max=1,
    )


def _ranked(results: list[VesselResult]) -> list[tuple]:
    return [(r.vessel_id, r.total_score) for r in results]


def _run_full(optimizer, build, relics) -> list[VesselResult]:
    return optimizer.optimize_all_vessels(
        build, RelicInventory.from_owned_relics(relics), WYLDER,
        top_n=10, max_per_vessel=3,
    )


def _run_partial(optimizer, build, relics, cached, rerun_ids):
    """A full run's cheaper twin: re-solve rerun_ids, carry the rest."""
    carried = [r for r in cached if r.vessel_id not in rerun_ids]
    out = None
    for event in optimizer.optimize_vessels_streaming(
        build, RelicInventory.from_owned_relics(relics), WYLDER,
        top_n=10, max_per_vessel=3,
        vessel_ids=rerun_ids, carried=carried,
    ):
        if event["type"] == "result":
            out = event["data"]
    return out


class TestVesselAccepts:
    """The pruning rule duplicates RelicInventory.get_candidates, because it
    must decide placement from a fingerprint with no inventory to filter.
    This pins the copy to the original across every real vessel."""

    def test_matches_get_candidates(self, ds: SourceDataHandler) -> None:
        checked = 0
        for hero_type in range(1, 11):
            for vessel in ds.get_all_vessels_for_hero(hero_type):
                colors = vessel["Colors"]
                for color in COLORS:
                    for is_deep in (False, True):
                        relic = _relic(0xC0001000, [], color, is_deep)
                        inv = RelicInventory.from_owned_relics([relic])
                        placeable = any(
                            inv.get_candidates(colors[i], i >= 3)
                            for i in range(len(colors))
                        )
                        assert vessel_accepts(colors, (color, is_deep)) is placeable, (
                            f"{vessel['Name']} {colors} vs {color} deep={is_deep}"
                        )
                        checked += 1
        assert checked > 500, "vessel table looks empty — test proves nothing"

    def test_deep_relics_reach_nothing_when_the_build_excludes_deep(
        self, ds: SourceDataHandler
    ) -> None:
        """include_deep=False never looks past slot 2, so a deep addition
        cannot touch a single vessel — the whole build is reusable."""
        vessels = ds.get_all_vessels_for_hero(WYLDER)
        keys = {(c, True) for c in COLORS}
        assert vessels_needing_rerun(vessels, keys, include_deep=False) == set()
        assert vessels_needing_rerun(vessels, keys, include_deep=True) != set()

    def test_one_colour_leaves_vessels_untouched(
        self, ds: SourceDataHandler
    ) -> None:
        """The property the whole optimization rests on: a single-colour diff
        never reaches every vessel."""
        vessels = ds.get_all_vessels_for_hero(WYLDER)
        for color in COLORS:
            touched = vessels_needing_rerun(vessels, {(color, False)})
            assert 0 < len(touched) < len(vessels), color


class TestPartialEqualsFull:
    @pytest.mark.parametrize("added_color", COLORS)
    def test_additions_only_matches_a_full_run(
        self, optimizer, ds, wanted_effects, added_color
    ) -> None:
        build = _build(wanted_effects)
        base = [
            _relic(0xC0000100 + i, [wanted_effects[i % len(wanted_effects)]],
                   COLORS[i % len(COLORS)], is_deep=i % 3 == 0)
            for i in range(24)
        ]
        cached = _run_full(optimizer, build, base)

        added = [
            _relic(0xC0000900 + i, wanted_effects[:3], added_color, is_deep=False)
            for i in range(3)
        ]
        after = base + added

        rerun_ids = vessels_needing_rerun(
            ds.get_all_vessels_for_hero(WYLDER), {(added_color, False)},
            include_deep=build.include_deep,
        )
        all_ids = {v["vessel_id"] for v in ds.get_all_vessels_for_hero(WYLDER)}
        assert rerun_ids < all_ids, (
            "nothing was pruned — this parity check would pass vacuously"
        )

        partial = _run_partial(optimizer, build, after, cached, rerun_ids)
        full = _run_full(optimizer, build, after)

        assert max(r.total_score for r in full) > 0, (
            "every layout scored 0 — ties would hide a divergence"
        )
        assert _ranked(partial) == _ranked(full), (
            f"partial run diverged for a {added_color} addition "
            f"(re-ran {len(rerun_ids)} vessels)"
        )

    def test_nothing_placeable_reuses_everything(
        self, optimizer, ds, wanted_effects
    ) -> None:
        """A build that ignores deep slots plus a deep-only addition: no vessel
        is re-solved at all, and the answer still matches a full run."""
        build = _build(wanted_effects, include_deep=False)
        base = [
            _relic(0xC0000100 + i, [wanted_effects[i % len(wanted_effects)]],
                   COLORS[i % len(COLORS)])
            for i in range(24)
        ]
        cached = _run_full(optimizer, build, base)
        after = base + [
            _relic(0xC0000900 + i, wanted_effects[:3], "Red", is_deep=True)
            for i in range(3)
        ]

        rerun_ids = vessels_needing_rerun(
            ds.get_all_vessels_for_hero(WYLDER), {("Red", True)},
            include_deep=False,
        )
        assert rerun_ids == set()
        partial = _run_partial(optimizer, build, after, cached, rerun_ids)
        assert _ranked(partial) == _ranked(_run_full(optimizer, build, after))

    def test_a_removal_is_not_reusable(
        self, optimizer, ds, wanted_effects
    ) -> None:
        """Why the route refuses to reuse across removals.

        Deleting relics can only lower the vessels that held them, which lets
        layouts the snapshot dropped rise into the top-N.  Carrying the old cut
        cannot reproduce them, so the two runs disagree — the guard in
        _rerun_vessel_ids is what keeps this out of production.
        """
        build = _build(wanted_effects)
        base = [
            _relic(0xC0000100 + i, [wanted_effects[i % len(wanted_effects)]],
                   COLORS[i % len(COLORS)], is_deep=i % 3 == 0)
            for i in range(24)
        ]
        cached = _run_full(optimizer, build, base)
        survivors = [r for r in base if r.color != "Red"]

        rerun_ids = vessels_needing_rerun(
            ds.get_all_vessels_for_hero(WYLDER), {("Red", False)},
            include_deep=build.include_deep,
        )
        partial = _run_partial(optimizer, build, survivors, cached, rerun_ids)
        full = _run_full(optimizer, build, survivors)

        # Carried results reference relics that no longer exist, so the partial
        # answer is wrong.  If this ever starts passing, the removal guard has
        # become unnecessary — verify before relaxing it.
        assert _ranked(partial) != _ranked(full)
