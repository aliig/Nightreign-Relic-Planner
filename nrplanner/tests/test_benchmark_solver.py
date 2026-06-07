"""Solver performance benchmark — measures optimizer speed and search efficiency.

Runs against the real save fixture and reports timing, node counts, and
prune ratios. Use before/after optimization changes to verify speedups
without accuracy loss.

Run with:  uv run pytest nrplanner/tests/test_benchmark_solver.py -v -s
"""
import json
import tempfile
import time
from pathlib import Path

import pytest

from nrplanner import (
    BuildScorer,
    SourceDataHandler,
    VesselOptimizer,
    decrypt_sl2,
    discover_characters,
    parse_relics,
)
from nrplanner.models import (
    BuildDefinition,
    OwnedRelic,
    RelicInventory,
    VesselState,
    WeightGroup,
)

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent
    / "backend" / "tests" / "fixtures" / "NR0000.sl2"
)

_GUARDIAN_CHAR_SKILL = 6500100
_GUARDIAN_STR_DEX    = 6641000
_GUARDIAN_HP_RESTORE = 7012000
_GUARDIAN_SHOCKWAVE  = 7033400
_DAMAGE_NEG_LOW_HP  = 340800
_PHYS_ATK_UP_3      = 6001400
_VIGOR_3            = 7000002


def _make_guardian_build() -> BuildDefinition:
    return BuildDefinition(
        id="guardian-bench",
        name="bench (Guardian)",
        character="Guardian",
        groups=[
            WeightGroup(weight=10, effects=[
                _GUARDIAN_CHAR_SKILL, _GUARDIAN_STR_DEX,
                _GUARDIAN_HP_RESTORE, _GUARDIAN_SHOCKWAVE,
            ]),
            WeightGroup(weight=5, effects=[_DAMAGE_NEG_LOW_HP, _PHYS_ATK_UP_3]),
            WeightGroup(weight=1, effects=[_VIGOR_3]),
        ],
        required_effects=[],
        excluded_effects=[],
        excluded_stacking_categories=[300, 6630000],
        include_deep=True,
        curse_max=1,
    )


def _make_simple_build() -> BuildDefinition:
    """A minimal build with few effects — tests baseline overhead."""
    return BuildDefinition(
        id="simple-bench",
        name="bench (simple)",
        character="Guardian",
        groups=[WeightGroup(weight=10, effects=[_GUARDIAN_CHAR_SKILL])],
        required_effects=[],
        excluded_effects=[],
        include_deep=True,
        curse_max=1,
    )


@pytest.fixture(scope="module")
def ds() -> SourceDataHandler:
    return SourceDataHandler(language="en_US")


@pytest.fixture(scope="module")
def real_inventory(ds: SourceDataHandler) -> RelicInventory:
    import nrplanner as _pkg
    items_json_path = (
        Path(_pkg.__file__).parent / "resources" / "json" / "items.json"
    )
    items_json = json.loads(items_json_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmpdir:
        decrypt_sl2(FIXTURE_PATH, tmpdir)
        characters = discover_characters(tmpdir)
        assert characters, "No characters found in save fixture"
        _, char_path = characters[0]
        data = char_path.read_bytes()
        raw_relics, _ = parse_relics(data)
    return RelicInventory(raw_relics, items_json, ds)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _find_vessel(ds: SourceDataHandler, hero_type: int, name_substr: str) -> dict:
    for v in ds.get_all_vessels_for_hero(hero_type):
        if name_substr in v["Name"]:
            vd = dict(v)
            vd["_id"] = v["vessel_id"]
            return vd
    raise ValueError(f"Vessel '{name_substr}' not found for hero {hero_type}")


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present",
)
class TestBenchmarkSolver:

    def test_single_vessel_timing(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Benchmark single-vessel optimization (Guardian's Chalice)."""
        build = _make_guardian_build()
        scorer = BuildScorer(ds)
        optimizer = VesselOptimizer(ds, scorer)
        chalice = _find_vessel(ds, 2, "Chalice")

        _log(f"\n{'='*70}")
        _log(f"BENCHMARK: Single vessel — {chalice['Name']}")
        _log(f"Inventory: {len(real_inventory.relics)} relics")
        _log(f"{'='*70}")

        # Warm up caches
        optimizer.optimize(build, real_inventory, chalice, top_n=3)

        # Timed runs
        N = 5
        times = []
        for i in range(N):
            t0 = time.perf_counter()
            results = optimizer.optimize(build, real_inventory, chalice, top_n=3)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)

        avg_ms = sum(times) / N * 1000
        min_ms = min(times) * 1000
        max_ms = max(times) * 1000
        best_score = results[0].total_score if results else 0

        _log(f"\n  Runs: {N}")
        _log(f"  Avg: {avg_ms:.1f}ms | Min: {min_ms:.1f}ms | Max: {max_ms:.1f}ms")
        _log(f"  Best score: {best_score}")
        _log(f"  Results: {len(results)}")

    def test_all_vessels_timing(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Benchmark all-vessel optimization (sequential, no process pool)."""
        build = _make_guardian_build()
        scorer = BuildScorer(ds)
        optimizer = VesselOptimizer(ds, scorer)

        _log(f"\n{'='*70}")
        _log(f"BENCHMARK: All vessels (sequential) — Guardian")
        _log(f"{'='*70}")

        # Warm up
        optimizer.optimize_all_vessels(build, real_inventory, hero_type=2, top_n=10)

        N = 3
        times = []
        for i in range(N):
            t0 = time.perf_counter()
            results = optimizer.optimize_all_vessels(
                build, real_inventory, hero_type=2, top_n=10)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)

        avg_ms = sum(times) / N * 1000
        min_ms = min(times) * 1000

        vessels = list(ds.get_all_vessels_for_hero(2))
        _log(f"\n  Vessels: {len(vessels)}")
        _log(f"  Avg: {avg_ms:.0f}ms | Min: {min_ms:.0f}ms")
        _log(f"  Best score: {results[0].total_score if results else 0}")
        _log(f"  Unique results: {len(results)}")

    def test_scoring_microbenchmark(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Microbenchmark score_relic and score_relic_in_context calls."""
        build = _make_guardian_build()
        scorer = BuildScorer(ds)
        desired_cw = scorer.get_desired_conflict_weights(build)
        desired_compat = scorer.get_desired_compat_effects(build)

        _log(f"\n{'='*70}")
        _log(f"BENCHMARK: Scoring microbenchmark")
        _log(f"{'='*70}")

        # Pre-score (no context)
        relics = real_inventory.relics
        N = 10
        t0 = time.perf_counter()
        for _ in range(N):
            for r in relics:
                scorer.score_relic(r, build)
        elapsed_pre = (time.perf_counter() - t0) / N
        _log(f"\n  score_relic × {len(relics)} relics: {elapsed_pre*1000:.1f}ms")
        _log(f"  Per relic: {elapsed_pre/len(relics)*1_000_000:.1f}µs")

        # Context score
        state = VesselState(
            ds, desired_conflict_weights=desired_cw,
            desired_compat_effects=desired_compat,
        )
        # Place 3 relics to create realistic context
        placed = 0
        for r in relics[:20]:
            if placed >= 3:
                break
            if scorer.score_relic(r, build) > 0:
                state.place(r)
                placed += 1

        t0 = time.perf_counter()
        for _ in range(N):
            for r in relics:
                scorer.score_relic_in_context(r, build, state)
        elapsed_ctx = (time.perf_counter() - t0) / N
        _log(f"  score_relic_in_context × {len(relics)}: {elapsed_ctx*1000:.1f}ms")
        _log(f"  Per relic: {elapsed_ctx/len(relics)*1_000_000:.1f}µs")

    def test_data_source_lookup_microbenchmark(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Microbenchmark individual SourceDataHandler lookups."""
        _log(f"\n{'='*70}")
        _log(f"BENCHMARK: Data source lookups")
        _log(f"{'='*70}")

        # Collect all unique effect IDs from inventory
        all_effects: list[int] = []
        for r in real_inventory.relics:
            all_effects.extend(e for e in r.all_effects if e != 0)
        unique_effects = list(set(all_effects))
        _log(f"\n  Unique effect IDs: {len(unique_effects)}")

        N = 100
        methods = [
            ("get_effect_text_id", ds.get_effect_text_id),
            ("get_effect_conflict_id", ds.get_effect_conflict_id),
            ("get_effect_exclusivity_id", ds.get_effect_exclusivity_id),
            ("get_effect_stacking_type", ds.get_effect_stacking_type),
            ("get_effect_name", ds.get_effect_name),
            ("get_effect_family", ds.get_effect_family),
        ]

        for name, method in methods:
            t0 = time.perf_counter()
            for _ in range(N):
                for eff in unique_effects:
                    method(eff)
            elapsed = (time.perf_counter() - t0) / N
            per_call = elapsed / len(unique_effects) * 1_000_000
            _log(f"  {name:30s}: {elapsed*1000:6.1f}ms total, {per_call:5.1f}µs/call")

    def test_backtrack_vs_brute_force(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Compare backtracking solver score against brute-force optimal."""
        build = _make_guardian_build()
        scorer = BuildScorer(ds)
        optimizer = VesselOptimizer(ds, scorer)
        desired_cw = scorer.get_desired_conflict_weights(build)
        desired_compat = scorer.get_desired_compat_effects(build)
        chalice = _find_vessel(ds, 2, "Chalice")
        slot_colors = chalice["Colors"]

        _log(f"\n{'='*70}")
        _log(f"BENCHMARK: Backtrack vs brute-force correctness")
        _log(f"{'='*70}")

        # Run optimizer
        t0 = time.perf_counter()
        results = optimizer.optimize(build, real_inventory, chalice, top_n=3)
        opt_time = time.perf_counter() - t0
        opt_score = results[0].total_score if results else 0

        # Run brute-force (same logic as gold standard)
        candidates_per_slot: list[list[tuple[int, OwnedRelic]]] = []
        for i in range(6):
            is_deep = i >= 3
            raw = real_inventory.get_candidates(slot_colors[i], is_deep)
            filtered = [
                r for r in raw
                if not scorer.has_excluded_effect(r, build, desired_compat)
            ]
            scored = [(scorer.score_relic(r, build), r) for r in filtered]
            scored = [(s, r) for s, r in scored if s > 0]
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates_per_slot.append(scored)

        max_pre_per_slot = [c[0][0] if c else 0 for c in candidates_per_slot]
        best_bf_score = -1
        nodes = 0
        pruned = 0

        state = VesselState(
            ds, desired_conflict_weights=desired_cw,
            desired_compat_effects=desired_compat,
        )

        def bf(slot: int, current: list, used: set, score: int) -> None:
            nonlocal best_bf_score, nodes, pruned
            if slot == 6:
                nodes += 1
                if score > best_bf_score:
                    best_bf_score = score
                return
            remaining = sum(max_pre_per_slot[s] for s in range(slot + 1, 6))
            for pre_score, relic in candidates_per_slot[slot]:
                if relic.ga_handle in used:
                    continue
                if score + pre_score + remaining <= best_bf_score:
                    pruned += 1
                    continue
                ctx = scorer.score_relic_in_context(relic, build, state)
                if ctx <= 0:
                    continue
                if score + ctx + remaining <= best_bf_score:
                    pruned += 1
                    continue
                current[slot] = relic
                used.add(relic.ga_handle)
                delta = state.place(relic)
                bf(slot + 1, current, used, score + ctx)
                used.discard(relic.ga_handle)
                state.remove(delta)
            current[slot] = None
            bf(slot + 1, current, used, score)

        t0 = time.perf_counter()
        bf(0, [None] * 6, set(), 0)
        bf_time = time.perf_counter() - t0

        _log(f"\n  Optimizer:   {opt_score} pts in {opt_time*1000:.1f}ms")
        _log(f"  Brute-force: {best_bf_score} pts in {bf_time*1000:.1f}ms")
        _log(f"  BF nodes: {nodes:,}, pruned: {pruned:,}")
        _log(f"  Speedup: {bf_time/opt_time:.1f}x")
        _log(f"  Match: {'YES' if opt_score >= best_bf_score else 'NO'}")

        assert opt_score >= best_bf_score, (
            f"Optimizer ({opt_score}) worse than brute-force ({best_bf_score})")

    def test_place_remove_microbenchmark(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Microbenchmark VesselState.place() and remove() — the backtrack inner loop."""
        scorer = BuildScorer(ds)
        build = _make_guardian_build()
        desired_cw = scorer.get_desired_conflict_weights(build)
        desired_compat = scorer.get_desired_compat_effects(build)

        _log(f"\n{'='*70}")
        _log(f"BENCHMARK: VesselState place/remove")
        _log(f"{'='*70}")

        # Pick relics with effects
        test_relics = [r for r in real_inventory.relics if r.effect_count >= 2][:50]

        N = 100
        t0 = time.perf_counter()
        for _ in range(N):
            state = VesselState(
                ds, desired_conflict_weights=desired_cw,
                desired_compat_effects=desired_compat,
            )
            deltas = []
            for r in test_relics:
                deltas.append(state.place(r))
            for delta in reversed(deltas):
                state.remove(delta)
        elapsed = (time.perf_counter() - t0) / N
        per_op = elapsed / len(test_relics) / 2 * 1_000_000  # per place or remove

        _log(f"\n  {len(test_relics)} relics × place+remove: {elapsed*1000:.2f}ms")
        _log(f"  Per place/remove: {per_op:.1f}µs")
