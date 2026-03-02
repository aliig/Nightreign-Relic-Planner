"""Gold-standard brute-force solver — proves the MAXIMUM possible score.

This test exhaustively explores every legal relic combination for
Guardian's Chalice and reports the true optimal.

If this test gets 61 (not 62), the problem is in scoring/parsing, not
the optimizer algorithm.

Run with:  uv run pytest nrplanner/tests/test_gold_standard.py -v -s
"""
import json
import tempfile
import time
from pathlib import Path

import pytest

from nrplanner import (
    BuildScorer,
    SourceDataHandler,
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

# Effect IDs from effects.json
_GUARDIAN_CHAR_SKILL = 6500100
_GUARDIAN_STR_DEX    = 6641000
_GUARDIAN_HP_RESTORE = 7012000
_GUARDIAN_SHOCKWAVE  = 7033400
_DAMAGE_NEG_LOW_HP  = 340800
# NOTE: 6001400's FMG entry says "+4", but attachTextId→7001403 resolves to "+3".
# The true "+4" effect is 6001401 (whose attachTextId=6001400 → FMG "+4").
_PHYS_ATK_UP_3      = 6001400
_VIGOR_3            = 7000002


def _log(msg: str) -> None:
    print(msg, flush=True)


def _make_guardian_build() -> BuildDefinition:
    return BuildDefinition(
        id="guardian-test",
        name="test (Guardian)",
        character="Guardian",
        groups=[
            WeightGroup(
                weight=10,
                effects=[
                    _GUARDIAN_CHAR_SKILL,
                    _GUARDIAN_STR_DEX,
                    _GUARDIAN_HP_RESTORE,
                    _GUARDIAN_SHOCKWAVE,
                ],
            ),
            WeightGroup(
                weight=5,
                effects=[_DAMAGE_NEG_LOW_HP, _PHYS_ATK_UP_3],
            ),
            WeightGroup(
                weight=1,
                effects=[_VIGOR_3],
            ),
        ],
        required_effects=[],
        excluded_effects=[],
        excluded_stacking_categories=[300, 6630000],
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


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present",
)
class TestGoldStandard:

    def test_brute_force_optimal(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Exhaustive brute-force with lossless pruning and NO time limit."""
        build = _make_guardian_build()
        scorer = BuildScorer(ds)
        desired_cw = scorer.get_desired_conflict_weights(build)
        desired_compat_effs = scorer.get_desired_compat_effects(build)

        # Find Guardian's Chalice
        vessels = list(ds.get_all_vessels_for_hero(2))
        chalice = None
        for v in vessels:
            if "Chalice" in v["Name"]:
                chalice = dict(v)
                chalice["_id"] = v["vessel_id"]
                break
        assert chalice is not None, "Guardian's Chalice not found"
        slot_colors = chalice["Colors"]

        _log(f"\n{'='*70}")
        _log(f"GOLD STANDARD BRUTE FORCE — {chalice['Name']}")
        _log(f"Slot colors: {slot_colors}")

        # Build candidate lists per slot (same filtering as optimizer)
        candidates_per_slot: list[list[tuple[int, OwnedRelic]]] = []
        for i in range(6):
            is_deep = i >= 3
            raw = real_inventory.get_candidates(slot_colors[i], is_deep)
            filtered = [
                r for r in raw
                if not scorer.has_excluded_effect(r, build, desired_compat_effs)
            ]
            scored = [(scorer.score_relic(r, build), r) for r in filtered]
            scored = [(s, r) for s, r in scored if s > 0]
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates_per_slot.append(scored)
            _log(f"  Slot {i} ({slot_colors[i]}, {'deep' if is_deep else 'std'}): "
                 f"{len(scored)} useful candidates")

        # Show theoretical search space
        total_combos = 1
        for c in candidates_per_slot:
            total_combos *= (len(c) + 1)
        _log(f"\n  Search space: {total_combos:,} combinations (before pruning)")

        # Run exhaustive backtracking with NO time limit
        _log(f"\n  Starting brute-force backtracking (no time limit)...")
        t0 = time.perf_counter()

        # Pre-compute max pre_score per slot for upper-bound pruning
        max_pre_per_slot = []
        for cands in candidates_per_slot:
            max_pre_per_slot.append(cands[0][0] if cands else 0)

        best_score = -1
        best_assignment: list[OwnedRelic | None] = [None] * 6
        nodes_explored = 0
        branches_pruned = 0

        state = VesselState(
            ds,
            desired_conflict_weights=desired_cw,
            desired_compat_effects=desired_compat_effs,
        )

        def backtrack(slot_idx: int, current: list, used: set,
                      score: int) -> None:
            nonlocal best_score, best_assignment, nodes_explored, branches_pruned

            if slot_idx == 6:
                nodes_explored += 1
                if score > best_score:
                    best_score = score
                    best_assignment = list(current)
                    elapsed = time.perf_counter() - t0
                    _log(f"    NEW BEST: {best_score} at {elapsed:.1f}s "
                         f"({nodes_explored:,} leaves, {branches_pruned:,} pruned)")
                if nodes_explored % 100_000 == 0:
                    elapsed = time.perf_counter() - t0
                    _log(f"    ... {nodes_explored:,} leaves, "
                         f"{branches_pruned:,} pruned in {elapsed:.1f}s "
                         f"(best: {best_score})")
                return

            # Upper bound: score + max possible from remaining slots
            remaining_max = sum(max_pre_per_slot[s] for s in range(slot_idx, 6))
            if score + remaining_max <= best_score:
                branches_pruned += 1
                return

            # Try each candidate (sorted by pre_score desc for faster pruning)
            remaining_after = sum(
                max_pre_per_slot[s] for s in range(slot_idx + 1, 6))

            for pre_score, relic in candidates_per_slot[slot_idx]:
                if relic.ga_handle in used:
                    continue
                # Pre-score upper bound
                if score + pre_score + remaining_after <= best_score:
                    branches_pruned += 1
                    continue

                ctx_score = scorer.score_relic_in_context(relic, build, state)

                # Actual score upper bound
                if score + ctx_score + remaining_after <= best_score:
                    branches_pruned += 1
                    continue

                current[slot_idx] = relic
                used.add(relic.ga_handle)
                delta = state.place(relic)

                backtrack(slot_idx + 1, current, used, score + ctx_score)

                used.discard(relic.ga_handle)
                state.remove(delta)

            # Try empty slot
            current[slot_idx] = None
            if score + remaining_after > best_score:
                backtrack(slot_idx + 1, current, used, score)
            else:
                branches_pruned += 1

        backtrack(0, [None] * 6, set(), 0)

        elapsed = time.perf_counter() - t0
        _log(f"\n  DONE: {nodes_explored:,} leaves, {branches_pruned:,} pruned in {elapsed:.1f}s")

        # Verify: re-score the best assignment from scratch with a fresh state
        verify_state = VesselState(ds, desired_conflict_weights=desired_cw,
                                    desired_compat_effects=desired_compat_effs)
        verified_score = 0
        for relic in best_assignment:
            if relic is not None:
                verified_score += scorer.score_relic_in_context(relic, build, verify_state)
                verify_state.place(relic)

        _log(f"\n  Backtrack accumulated: {best_score}, Verified re-score: {verified_score}")
        assert best_score == verified_score, (
            f"VesselState bug! Backtrack={best_score}, verified={verified_score}")

        # Print best result
        _log(f"\n{'='*70}")
        _log(f"GOLD STANDARD OPTIMAL SCORE: {best_score}")
        _log(f"{'='*70}")
        for i, relic in enumerate(best_assignment):
            if relic is not None:
                eff_names = [ds.get_effect_name(e) for e in relic.all_effects if e != 0]
                ctx_state = VesselState(ds, desired_conflict_weights=desired_cw,
                                        desired_compat_effects=desired_compat_effs)
                for j in range(i):
                    if best_assignment[j] is not None:
                        ctx_state.place(best_assignment[j])
                slot_score = scorer.score_relic_in_context(relic, build, ctx_state)
                _log(f"  Slot {i} ({slot_colors[i]}): {relic.name} [{slot_score} pts] — "
                     f"{', '.join(eff_names)}")
            else:
                _log(f"  Slot {i} ({slot_colors[i]}): EMPTY")

        # Compare with greedy
        _log(f"\n--- Greedy (left-to-right) for comparison ---")
        greedy_state = VesselState(ds, desired_conflict_weights=desired_cw,
                                    desired_compat_effects=desired_compat_effs)
        greedy_score = 0
        greedy_used: set[int] = set()
        for slot_idx in range(6):
            best_relic = None
            best_ctx = 0
            for _, relic in candidates_per_slot[slot_idx]:
                if relic.ga_handle in greedy_used:
                    continue
                ctx = scorer.score_relic_in_context(relic, build, greedy_state)
                if ctx > best_ctx:
                    best_ctx = ctx
                    best_relic = relic
            if best_relic is not None:
                eff_names = [ds.get_effect_name(e) for e in best_relic.all_effects if e != 0]
                _log(f"  Slot {slot_idx} ({slot_colors[slot_idx]}): "
                     f"{best_relic.name} [{best_ctx} pts] — {', '.join(eff_names)}")
                greedy_used.add(best_relic.ga_handle)
                greedy_state.place(best_relic)
                greedy_score += best_ctx
            else:
                _log(f"  Slot {slot_idx} ({slot_colors[slot_idx]}): EMPTY")
        _log(f"  Greedy total: {greedy_score}")

        _log(f"\n  VERDICT: brute_force={best_score}, greedy={greedy_score}, "
             f"diff={best_score - greedy_score}")
        if best_score == greedy_score:
            _log("  => Greedy IS optimal for this build. Issue is in scoring, not solver.")
        else:
            _log("  => Greedy is SUBOPTIMAL. Optimizer algorithm needs improvement.")

        assert best_score >= 61, f"Brute force found only {best_score}"
