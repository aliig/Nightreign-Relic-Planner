"""Compiled-profile equivalence: score_profile/place_profile vs legacy.

The solver hot loop runs on compiled RelicProfiles (BuildScorer.compile_profile)
instead of score_relic_in_context/VesselState.place.  These property tests pin
the two implementations together over randomized builds, relics, and placement
sequences using real game data — any divergence is a solver-correctness bug.
"""
import random

import pytest

from nrplanner import SourceDataHandler
from nrplanner.constants import EMPTY_EFFECT as EMPTY
from nrplanner.models import (
    BuildDefinition,
    OwnedRelic,
    RelicInventory,
    VesselState,
    WeightGroup,
)
from nrplanner.optimizer import VesselOptimizer
from nrplanner.scoring import BuildScorer, score_profile


def _random_build(rng: random.Random, ids: list[int], fams: list[str],
                  cat_members: list[int]) -> BuildDefinition:
    groups = []
    for _ in range(rng.randint(1, 4)):
        effs = rng.sample(ids, k=rng.randint(0, 4))
        if cat_members and rng.random() < 0.5:
            effs.append(rng.choice(cat_members))
        groups.append(WeightGroup(
            weight=rng.choice([-20, -5, 5, 10, 25, 40]),
            effects=effs,
            families=rng.sample(fams, k=rng.randint(0, 2)),
        ))
    effect_limits = {}
    if rng.random() < 0.5:
        for eid in rng.sample(ids, k=rng.randint(1, 2)):
            effect_limits[eid] = rng.randint(1, 2)
    family_limits = {}
    if rng.random() < 0.5:
        family_limits[rng.choice(fams)] = rng.randint(1, 2)
    return BuildDefinition(
        id="rand", name="rand", character="Wylder",
        groups=groups,
        required_effects=rng.sample(ids, k=rng.randint(0, 2)),
        excluded_effects=rng.sample(ids, k=rng.randint(0, 2)),
        excluded_stacking_categories=(
            [300, 6630000] if rng.random() < 0.7 else []),
        effect_limits=effect_limits,
        family_limits=family_limits,
        default_curse_weight=rng.choice([-10, -5, 0, 3]),
        curse_max=rng.randint(0, 2),
    )


def _random_relic(rng: random.Random, pool: list[int],
                  cat_members: list[int], handle: int) -> OwnedRelic:
    effects = rng.sample(pool, k=rng.randint(1, 3))
    if cat_members and rng.random() < 0.4:
        effects[rng.randrange(len(effects))] = rng.choice(cat_members)
    curses = rng.sample(pool, k=rng.randint(0, 2))
    effects = (effects + [EMPTY, EMPTY, EMPTY])[:3]
    curses = (curses + [EMPTY, EMPTY, EMPTY])[:3]
    return OwnedRelic(
        ga_handle=handle, item_id=100 + 2147483648, real_id=100,
        color="Red", effects=effects, curses=curses, is_deep=False,
        name=f"R{handle}", tier="Delicate",
    )


def _assert_states_equal(a: VesselState, b: VesselState) -> None:
    assert a.effect_ids == b.effect_ids
    assert a.exclusivity_ids == b.exclusivity_ids
    assert a.no_stack_exclusivity_ids == b.no_stack_exclusivity_ids
    assert a.no_stack_compat_ids == b.no_stack_compat_ids
    assert a.curse_counts == b.curse_counts
    assert a.desired_compat_placed == b.desired_compat_placed
    assert a.limited_counts == b.limited_counts


@pytest.mark.parametrize("seed", [11, 23, 37, 51])
def test_profiles_match_legacy_scoring_and_place(
    ds: SourceDataHandler, all_effects, seed: int
) -> None:
    rng = random.Random(seed)
    ids = [e["id"] for e in all_effects[:300]]
    ids += [999999901, 999999902]  # unknown ids: no family/text/name resolution
    fams = [f["name"] for f in ds.get_all_families_list()[:20]]
    cat_members = [
        e["id"] for e in all_effects
        if ds.get_effect_conflict_id(e["id"]) in (300, 6630000)
    ]

    scorer = BuildScorer(ds)
    optimizer = VesselOptimizer(ds, scorer)

    for build_i in range(8):
        build = _random_build(rng, ids, fams, cat_members)
        dcw = scorer.get_desired_conflict_weights(build)
        dce = scorer.get_desired_compat_effects(build)
        elbn, flm = optimizer._prepare_limits(build)

        def mk_state() -> VesselState:
            # character must match what compile_profile filters on, exactly as
            # VesselOptimizer constructs it — otherwise place() would register
            # effects the compiled profile treats as inert.
            return VesselState(
                ds, desired_conflict_weights=dcw, desired_compat_effects=dce,
                effect_limit_by_name=elbn, family_limit_map=flm,
                character=build.character,
            )

        legacy, compiled = mk_state(), mk_state()
        relics = [
            _random_relic(rng, ids, cat_members, 0xC1000000 + build_i * 100 + i)
            for i in range(12)
        ]

        for _step in range(5):
            for r in relics:
                prof = scorer.compile_profile(r, build, elbn, flm)
                got = score_profile(prof, compiled, build.curse_max)
                want = scorer.score_relic_in_context(r, build, legacy)
                assert got == want, (
                    f"seed={seed} build={build_i} relic={r.ga_handle:#x}: "
                    f"score_profile={got} != legacy={want}\n"
                    f"effects={r.effects} curses={r.curses}\n"
                    f"build={build.model_dump()}"
                )
            placed = rng.choice(relics)
            prof = scorer.compile_profile(placed, build, elbn, flm)
            d_legacy = legacy.place(placed)
            d_compiled = compiled.place_profile(prof)
            assert d_legacy == d_compiled, (
                f"seed={seed} build={build_i} placed={placed.ga_handle:#x}: "
                f"deltas diverge\nlegacy={d_legacy}\ncompiled={d_compiled}"
            )
            _assert_states_equal(legacy, compiled)


def test_a_reused_ga_handle_does_not_serve_a_stale_profile(
    ds: SourceDataHandler,
) -> None:
    """A handle identifies a relic only within ONE inventory.

    The game renumbers every ga_handle when it writes a save, and a pool worker
    outlives any single request, so the same scorer legitimately sees one handle
    standing for different relics.  The compiled-profile memo is keyed by
    handle, and a RelicProfile carries its ``relic``, so a memo that outlives
    its inventory places and scores the WRONG relic -- silently, with a
    plausible score.  The build cache cannot catch it: both runs here have the
    same _scoring_sig.

    Both relics must be worth placing (an unweighted one is dropped by the
    positive_pre_score filter before it ever reaches the memo), and they carry
    DIFFERENT weights so a stale profile shows up in the score as well as in
    the relic it places.
    """
    build = BuildDefinition(
        id="b", name="b", character="Wylder",
        groups=[
            WeightGroup(weight=10, effects=[100], families=[]),
            WeightGroup(weight=40, effects=[101], families=[]),
        ],
    )
    scorer = BuildScorer(ds)
    optimizer = VesselOptimizer(ds, scorer)
    vessel = dict(next(iter(ds.get_all_vessels_for_hero(1))))
    vessel["_id"] = vessel["vessel_id"]

    def run(real_id: int, effect: int):
        relic = OwnedRelic(
            ga_handle=0xC0020000,  # the SAME handle both times
            item_id=real_id + 2147483648, real_id=real_id,
            color=vessel["Colors"][0],
            effects=[effect, EMPTY, EMPTY], curses=[EMPTY, EMPTY, EMPTY],
            is_deep=False, name=f"Relic {real_id}", tier="Delicate",
        )
        results = optimizer.optimize(
            build, RelicInventory.from_owned_relics([relic]), vessel, top_n=1)
        assert results, f"real_id={real_id} produced no result"
        placed = [a.relic for a in results[0].assignments if a.relic]
        assert placed, f"real_id={real_id} was not placed"
        return results[0], placed

    first, _ = run(100, 100)
    assert first.total_score == 10, first.total_score

    # Same handle, different relic, different weight.
    second, placed = run(127, 101)
    assert [r.real_id for r in placed] == [127], (
        f"stale profile served the previous inventory's relic: "
        f"{[r.real_id for r in placed]}"
    )
    assert second.total_score == 40, (
        f"stale profile scored the previous relic's effect: {second.total_score}"
    )
