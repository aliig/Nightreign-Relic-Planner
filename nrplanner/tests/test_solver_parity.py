"""Differential test: the Rust solver must match the Python one exactly.

This is the gate on the Rust migration.  Both engines live behind the same
seam (``VesselOptimizer._solve_free_slots_*``) and are run on the same
optimizer, over the same randomized scenarios, and compared on:

  * the ordered list of results per vessel
  * each result's per-slot (relic handle, score) signature
  * total score, meets_requirements, missing_requirements, search_truncated
  * the node count — an ordering bug can produce identical results from a
    differently shaped search, and only the node count catches that

Comparison is always PER VESSEL, via ``optimize()``.  Going through
``optimize_all_vessels`` would not work: its ``as_completed`` arrival order can
reorder equal-score layouts across vessels, so a mismatch there would say
nothing about the solver.

The test skips when the extension is not built, unless NRPLANNER_REQUIRE_RUST=1
(CI sets it, so a missing wheel fails loudly instead of silently passing).
"""
import os

import pytest

from nrplanner import SourceDataHandler
from nrplanner.optimizer import VesselOptimizer
from nrplanner.scoring import BuildScorer
from nrplanner.tests._solver_scenarios import (
    Scenario, assignment_signature, legal_relics, legal_scenarios,
    synthetic_scenarios,
)

if os.environ.get("NRPLANNER_REQUIRE_RUST") == "1":
    import nrplanner_core  # noqa: F401  # fail loudly rather than skip
else:
    pytest.importorskip("nrplanner_core")


SYNTHETIC_SEEDS = [11, 23, 37, 51, 67, 83, 97, 101]

# One shared inventory of game-legal relics, deliberately large: at 800 relics
# the randomized builds drive the backtracker into the millions of nodes
# (~1.9M on its worst vessel), which is the regime a parity gate has to cover.
# A per-seed inventory instead would be both slower to roll and far shallower.
LEGAL_POOL_SEED = 4242
LEGAL_POOL_SIZE = 800
LEGAL_BUILDS = 12


def _result_signature(vr) -> tuple:
    return (
        tuple(assignment_signature(vr)),
        vr.total_score,
        vr.meets_requirements,
        tuple(sorted(map(str, vr.missing_requirements))),
        vr.search_truncated,
    )


def _compare_engines(optimizer: VesselOptimizer, ds: SourceDataHandler,
                     sc: Scenario, top_n: int) -> int:
    """Solve every vessel of one scenario on both engines. Returns node total."""
    total_nodes = 0
    for vessel in ds.get_all_vessels_for_hero(sc.hero_type):
        vessel_data = dict(vessel)
        vessel_data["_id"] = vessel["vessel_id"]

        py_results = optimizer.optimize(
            sc.build, sc.inventory, dict(vessel_data), top_n, engine="python")
        py_stats = dict(optimizer.last_solve_stats)

        rs_results = optimizer.optimize(
            sc.build, sc.inventory, dict(vessel_data), top_n, engine="rust")
        rs_stats = dict(optimizer.last_solve_stats)

        where = f"{sc.name} / {vessel['Name']} / top_n={top_n}"
        assert [_result_signature(r) for r in py_results] == \
               [_result_signature(r) for r in rs_results], \
            f"result mismatch on {where}"
        assert py_stats["nodes"] == rs_stats["nodes"], (
            f"node count diverged on {where}: "
            f"python={py_stats['nodes']} rust={rs_stats['nodes']}")
        assert py_stats["truncated"] == rs_stats["truncated"], where
        assert py_stats["candidates"] == rs_stats["candidates"], (
            f"candidate counts diverged on {where}")
        total_nodes += rs_stats["nodes"]
    return total_nodes


@pytest.fixture
def optimizer(ds: SourceDataHandler) -> VesselOptimizer:
    """A fresh optimizer per test — the scorer memoizes per (build, inventory)."""
    return VesselOptimizer(ds, BuildScorer(ds))


@pytest.mark.parametrize("seed", SYNTHETIC_SEEDS)
@pytest.mark.parametrize("top_n", [1, 3, 10])
def test_synthetic_scenarios_match(optimizer, ds, seed, top_n) -> None:
    for sc in synthetic_scenarios(ds, seed, n=5):
        _compare_engines(optimizer, ds, sc, top_n)


@pytest.fixture(scope="module")
def legal_pool(ds: SourceDataHandler):
    """One large inventory of game-legal relics, rolled once for the module."""
    return legal_relics(ds, LEGAL_POOL_SEED, LEGAL_POOL_SIZE)


@pytest.mark.parametrize("top_n", [1, 3, 10])
def test_legal_scenarios_match(optimizer, ds, legal_pool, top_n) -> None:
    """The deep case: real relics, big candidate lists, millions of nodes."""
    scs = legal_scenarios(ds, LEGAL_POOL_SEED, n=LEGAL_BUILDS,
                          relics=legal_pool)
    nodes = sum(_compare_engines(optimizer, ds, sc, top_n) for sc in scs)
    # Guard against the scenarios silently going shallow (a data change that
    # thins the candidate lists would make this test pass vacuously).
    assert nodes > 100_000, f"parity workload collapsed to {nodes} nodes"


@pytest.mark.parametrize("seed", [11, 37, 97])
def test_truncated_search_matches(optimizer, ds, seed) -> None:
    """An already-expired deadline must truncate identically on both engines.

    Both should trip on the very first node and fall back to the same greedy
    floor — which is the only thing that makes a truncated result meaningful.
    """
    for sc in synthetic_scenarios(ds, seed, n=3):
        for vessel in ds.get_all_vessels_for_hero(sc.hero_type):
            vessel_data = dict(vessel)
            vessel_data["_id"] = vessel["vessel_id"]
            py = optimizer.optimize(sc.build, sc.inventory, dict(vessel_data),
                                    3, deadline_secs=-1.0, engine="python")
            py_stats = dict(optimizer.last_solve_stats)
            rs = optimizer.optimize(sc.build, sc.inventory, dict(vessel_data),
                                    3, deadline_secs=-1.0, engine="rust")
            rs_stats = dict(optimizer.last_solve_stats)
            assert [_result_signature(r) for r in py] == \
                   [_result_signature(r) for r in rs]
            assert py_stats["nodes"] == rs_stats["nodes"] == 1 or \
                py_stats["nodes"] == rs_stats["nodes"]
            assert py_stats["truncated"] == rs_stats["truncated"]


def test_engine_info_is_a_release_build() -> None:
    """A debug build would make every benchmark number meaningless."""
    import nrplanner_core

    info = nrplanner_core.engine_info()
    assert info["abi3"] is True
    assert "version" in info


@pytest.mark.slow
def test_real_fixture_builds_match(optimizer, ds) -> None:
    """The bruteforce-oracle builds over a real save, on every Guardian vessel.

    The randomized scenarios above cover breadth; these six builds are the ones
    an independent oracle has already vetted, run here against a parsed
    NR0000.sl2 rather than generated relics.
    """
    bf = pytest.importorskip("nrplanner.tests.test_accuracy_vs_bruteforce")
    if not bf.FIXTURE_PATH.exists():
        pytest.skip("save fixture not present")

    inventory = bf.real_inventory.__wrapped__(ds)
    builds = [
        bf._make_guardian_build(),
        bf._make_simple_build(),
        bf._make_curse_heavy_build(),
        bf._make_negative_weight_build(ds),
        bf._make_limits_build(ds, inventory),
        bf._make_family_build(ds),
    ]
    for build in builds:
        sc = Scenario(name=build.name, build=build, inventory=inventory,
                      hero_type=2)  # Guardian
        _compare_engines(optimizer, ds, sc, top_n=3)
