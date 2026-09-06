"""Depth-1 prefetch plumbing: presubmitted futures must be result-identical.

Multi-build flows submit the NEXT build's vessel tasks before draining the
current build's (submit_all_vessels + presubmitted=/collect_all_vessels).
This spins a real thread pool — where the vessels of one build share a single
BuildSolveContext by reference — and pins that interleaved consumption
produces exactly the sequential in-process results for every build.
"""
from concurrent.futures import ThreadPoolExecutor


from nrplanner import SourceDataHandler
from nrplanner.constants import EMPTY_EFFECT as EMPTY
from nrplanner.models import (
    BuildDefinition,
    OwnedRelic,
    RelicInventory,
    WeightGroup,
)
from nrplanner.optimizer import VesselOptimizer
from nrplanner.scoring import BuildScorer

# High top_n so score ties never straddle the cutoff (arrival order between the
# parallel and sequential paths may rank equal-score layouts differently).
_TOP_N = 50
_MAX_PER_VESSEL = 2
_DEADLINE = 10.0


def _result_set(results) -> set[tuple]:
    return {(r.total_score, r.layout_fingerprint()) for r in results}


def _relic(handle: int, effects: list[int], color: str) -> OwnedRelic:
    eff = (list(effects) + [EMPTY, EMPTY, EMPTY])[:3]
    return OwnedRelic(
        ga_handle=handle, item_id=200 + 2147483648, real_id=200, color=color,
        effects=eff, curses=[EMPTY, EMPTY, EMPTY], is_deep=False,
        name=f"R{handle}", tier="Delicate",
    )


def test_presubmitted_futures_match_sequential(
    ds: SourceDataHandler, all_effects
) -> None:
    wylder_ok = [
        e["id"] for e in all_effects
        if (e.get("allow_per_character") or {}).get("Wylder", True)
    ][:12]
    assert len(wylder_ok) >= 4

    relics = [
        _relic(0xC1000000 + i, [wylder_ok[i % len(wylder_ok)]], color)
        for i, color in enumerate(
            ["Red", "Blue", "Green", "Yellow"] * 8)
    ]
    inv = RelicInventory.from_owned_relics(relics)

    build_a = BuildDefinition(
        id="a", name="A", character="Wylder", include_deep=False,
        groups=[WeightGroup(weight=10, effects=wylder_ok[:6])],
    )
    build_b = BuildDefinition(
        id="b", name="B", character="Wylder", include_deep=False,
        groups=[WeightGroup(weight=10, effects=wylder_ok[6:])],
    )

    scorer = BuildScorer(ds)
    opt = VesselOptimizer(ds, scorer)

    base_a = _result_set(opt.optimize_all_vessels(
        build_a, inv, 1, top_n=_TOP_N, max_per_vessel=_MAX_PER_VESSEL,
        deadline_secs=_DEADLINE))
    base_b = _result_set(opt.optimize_all_vessels(
        build_b, inv, 1, top_n=_TOP_N, max_per_vessel=_MAX_PER_VESSEL,
        deadline_secs=_DEADLINE))

    pool = ThreadPoolExecutor(max_workers=2,
                              thread_name_prefix="nr-test")
    try:
        # Prefetch pattern: submit BOTH builds up front, then drain in order.
        fut_a = opt.submit_all_vessels(
            build_a, inv, 1, _MAX_PER_VESSEL, pool, _DEADLINE)
        fut_b = opt.submit_all_vessels(
            build_b, inv, 1, _MAX_PER_VESSEL, pool, _DEADLINE)

        res_a = opt.collect_all_vessels(
            build_a, 1, fut_a, top_n=_TOP_N, n_relics=len(inv))

        events = list(opt.optimize_vessels_streaming(
            build_b, inv, 1, top_n=_TOP_N, max_per_vessel=_MAX_PER_VESSEL,
            executor=pool, presubmitted=fut_b))
    finally:
        pool.shutdown()

    assert _result_set(res_a) == base_a, "prefetched build A diverged"

    progress = [e for e in events if e["type"] == "progress"]
    assert len(progress) == len(fut_b), "one progress event per vessel"
    assert progress[-1]["vessel"] == len(fut_b)
    assert events[-1]["type"] == "result"
    assert _result_set(events[-1]["data"]) == base_b, "prefetched build B diverged"
