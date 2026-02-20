"""Vessel slot optimizer — backtrack (exhaustive) + greedy solvers."""
import time

from nrplanner.constants import EMPTY_EFFECT
from nrplanner.data import SourceDataHandler
from nrplanner.models import (
    BuildDefinition, OwnedRelic, RelicInventory,
    SlotAssignment, VesselResult,
)
from nrplanner.scoring import BuildScorer


class VesselOptimizer:
    """Finds optimal relic assignments for vessel slots."""

    def __init__(self, data_source: SourceDataHandler, scorer: BuildScorer):
        self.data_source = data_source
        self.scorer = scorer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def optimize(self, build: BuildDefinition, inventory: RelicInventory,
                 vessel_data: dict, top_n: int = 3) -> list[VesselResult]:
        """Best relic assignments for one vessel. Returns up to top_n results."""
        slot_colors = vessel_data["Colors"]
        num_slots = 6 if build.include_deep else 3

        candidates_per_slot = []
        for i in range(num_slots):
            is_deep = i >= 3
            candidates = inventory.get_candidates(slot_colors[i], is_deep)
            candidates = [r for r in candidates if not self.scorer.has_blacklisted_effect(r, build)]
            scored = sorted(
                ((self.scorer.score_relic(r, build), r) for r in candidates),
                key=lambda x: x[0], reverse=True,
            )
            candidates_per_slot.append(scored)

        total = sum(len(c) for c in candidates_per_slot)
        if total <= 200 and num_slots <= 6:
            raw = self._backtrack_solve(candidates_per_slot, num_slots, build, top_n)
        else:
            raw = self._greedy_solve(candidates_per_slot, num_slots, build, top_n)

        return [
            self._build_vessel_result(assignment, num_slots, slot_colors, vessel_data, build)
            for assignment in raw
        ]

    def optimize_all_vessels(self, build: BuildDefinition, inventory: RelicInventory,
                             hero_type: int, top_n: int = 10,
                             max_per_vessel: int = 3) -> list[VesselResult]:
        """Optimize all vessels for a hero. Returns top_n globally ranked results.

        Results that meet requirements come before those that don't, then sorted
        by score descending.
        """
        all_results: list[VesselResult] = []
        for v in self.data_source.get_all_vessels_for_hero(hero_type):
            vessel_data = dict(v)
            vessel_data["_id"] = v["vessel_id"]
            results = self.optimize(build, inventory, vessel_data, max_per_vessel)
            for r in results:
                r.vessel_id = v["vessel_id"]
            all_results.extend(results)

        all_results.sort(key=lambda r: (not r.meets_requirements, -r.total_score))
        return all_results[:top_n]

    # ------------------------------------------------------------------
    # Result builder
    # ------------------------------------------------------------------

    def _build_vessel_result(self, assignments: list, num_slots: int,
                             slot_colors: tuple, vessel_data: dict,
                             build: BuildDefinition) -> VesselResult:
        """Construct VesselResult from raw slot assignments (left-to-right priority)."""
        slot_results: list[tuple] = [(None, 0, [])] * num_slots
        assigned_effect_ids: set[int] = set()
        vessel_eff: set[int] = set()
        vessel_compat: set[int] = set()
        vessel_no_stack: set[int] = set()
        vessel_curse_counts: dict[int, int] = {}
        total_score = 0

        for i in range(num_slots):
            relic = assignments[i][0]
            if relic:
                score = self.scorer.score_relic_in_context(
                    relic, build, vessel_eff, vessel_compat, vessel_no_stack, vessel_curse_counts)
                breakdown = self.scorer.get_breakdown(relic, build, vessel_eff, vessel_compat, vessel_no_stack)
                assigned_effect_ids.update(relic.all_effects)
                for eff in relic.all_effects:
                    text_id = self.data_source.get_effect_text_id(eff)
                    if text_id != -1:
                        assigned_effect_ids.add(text_id)
                e, c, ns = self._get_relic_stacking_adds(relic)
                vessel_eff.update(e); vessel_compat.update(c); vessel_no_stack.update(ns)
                for curse in self._get_relic_curse_ids(relic):
                    vessel_curse_counts[curse] = vessel_curse_counts.get(curse, 0) + 1
            else:
                score, breakdown = 0, []
            slot_results[i] = (relic, score, breakdown)
            total_score += score

        slot_assignments = [
            SlotAssignment(
                slot_index=i,
                slot_color=slot_colors[i],
                is_deep=i >= 3,
                relic=slot_results[i][0],
                score=slot_results[i][1],
                breakdown=slot_results[i][2],
            )
            for i in range(num_slots)
        ]

        missing: list[int | str] = []
        required_ids = set(build.tiers.get("required", []))
        missing.extend(required_ids - assigned_effect_ids)
        for family in build.family_tiers.get("required", []):
            family_ids = self.data_source.get_family_effect_ids(family)
            if not (assigned_effect_ids & family_ids):
                missing.append(family)

        return VesselResult(
            vessel_id=vessel_data.get("_id", 0),
            vessel_name=vessel_data["Name"],
            vessel_character=vessel_data["Character"],
            unlock_flag=vessel_data["unlockFlag"],
            slot_colors=slot_colors,
            assignments=slot_assignments,
            total_score=total_score,
            meets_requirements=len(missing) == 0,
            missing_requirements=missing,
        )

    # ------------------------------------------------------------------
    # Solvers
    # ------------------------------------------------------------------

    def _greedy_solve(self, candidates_per_slot: list, num_slots: int,
                      build: BuildDefinition, top_n: int = 3) -> list[list]:
        results: list[list] = []
        excluded: set[int] = set()
        seen: set[frozenset] = set()

        for _ in range(top_n):
            assignment = self._greedy_solve_once(candidates_per_slot, num_slots, build, excluded)
            handles = frozenset(r.ga_handle for r, _ in assignment if r is not None)
            if not handles or handles in seen:
                break
            seen.add(handles)
            results.append(assignment)
            # Force diversity: exclude best relic from next pass
            best_handle, best_score = None, -1
            for relic, score in assignment:
                if relic and score > best_score:
                    best_score = score
                    best_handle = relic.ga_handle
            if best_handle:
                excluded.add(best_handle)

        return results or [[(None, 0)] * num_slots]

    def _greedy_solve_once(self, candidates_per_slot: list, num_slots: int,
                           build: BuildDefinition,
                           excluded_handles: set[int] | None = None) -> list:
        assigned: list = [None] * num_slots
        used: set[int] = set(excluded_handles or ())
        vessel_eff: set[int] = set()
        vessel_compat: set[int] = set()
        vessel_no_stack: set[int] = set()
        vessel_curse_counts: dict[int, int] = {}

        for slot_idx in range(num_slots):
            best: tuple | None = None
            for _, relic in candidates_per_slot[slot_idx]:
                if relic.ga_handle in used:
                    continue
                score = self.scorer.score_relic_in_context(
                    relic, build, vessel_eff, vessel_compat, vessel_no_stack, vessel_curse_counts)
                if best is None or score > best[0]:
                    best = (score, relic)

            if best is None:
                assigned[slot_idx] = (None, 0)
                continue

            score, relic = best
            assigned[slot_idx] = (relic, score)
            used.add(relic.ga_handle)
            e, c, ns = self._get_relic_stacking_adds(relic)
            vessel_eff.update(e); vessel_compat.update(c); vessel_no_stack.update(ns)
            for curse in self._get_relic_curse_ids(relic):
                vessel_curse_counts[curse] = vessel_curse_counts.get(curse, 0) + 1

        return assigned

    def _backtrack_solve(self, candidates_per_slot: list, num_slots: int,
                         build: BuildDefinition, top_n: int = 3) -> list[list]:
        top: list[tuple[int, list]] = []
        seen: set[frozenset] = set()
        min_threshold = -1
        deadline = time.time() + 2.0

        def backtrack(slot_idx: int, current: list, used: set[int],
                      v_eff: set, v_compat: set, v_no_stack: set,
                      curse_counts: dict[int, int], score: int) -> None:
            nonlocal min_threshold
            if time.time() > deadline:
                return

            if slot_idx == num_slots:
                if score > min_threshold or len(top) < top_n:
                    key = frozenset(used)
                    if key not in seen:
                        seen.add(key)
                        top.append((score, list(current)))
                        top.sort(key=lambda x: x[0], reverse=True)
                        if len(top) > top_n:
                            removed_key = frozenset(
                                r.ga_handle for r, _ in top.pop()[1] if r is not None)
                            seen.discard(removed_key)
                        min_threshold = top[-1][0] if len(top) == top_n else -1
                return

            # Try empty slot
            current[slot_idx] = (None, 0)
            backtrack(slot_idx + 1, current, used, v_eff, v_compat, v_no_stack, curse_counts, score)

            remaining_max = sum(
                candidates_per_slot[s][0][0] if candidates_per_slot[s] else 0
                for s in range(slot_idx + 1, num_slots)
            )
            for pre_score, relic in candidates_per_slot[slot_idx]:
                if relic.ga_handle in used:
                    continue
                if score + pre_score + remaining_max <= min_threshold:
                    continue  # upper-bound prune

                ctx_score = self.scorer.score_relic_in_context(
                    relic, build, v_eff, v_compat, v_no_stack, curse_counts)
                if score + ctx_score + remaining_max <= min_threshold:
                    continue  # actual-score prune

                added_eff, added_compat, added_ns = self._get_relic_stacking_adds(relic)
                added_curses = self._get_relic_curse_ids(relic)

                current[slot_idx] = (relic, ctx_score)
                used.add(relic.ga_handle)
                v_eff.update(added_eff); v_compat.update(added_compat); v_no_stack.update(added_ns)
                for cid in added_curses:
                    curse_counts[cid] = curse_counts.get(cid, 0) + 1

                backtrack(slot_idx + 1, current, used, v_eff, v_compat, v_no_stack,
                          curse_counts, score + ctx_score)

                used.discard(relic.ga_handle)
                v_eff -= added_eff; v_compat -= added_compat; v_no_stack -= added_ns
                for cid in added_curses:
                    curse_counts[cid] -= 1
                    if curse_counts[cid] == 0:
                        del curse_counts[cid]

        backtrack(0, [(None, 0)] * num_slots, set(), set(), set(), set(), {}, 0)
        return [assignment for _, assignment in top] or [[(None, 0)] * num_slots]

    # ------------------------------------------------------------------
    # Stacking state helpers
    # ------------------------------------------------------------------

    def _get_relic_stacking_adds(self, relic: OwnedRelic) -> tuple[set, set, set]:
        effect_ids: set[int] = set()
        compat_ids: set[int] = set()
        no_stack_compat_ids: set[int] = set()
        for eff in relic.all_effects:
            effect_ids.add(eff)
            text_id = self.data_source.get_effect_text_id(eff)
            if text_id != -1 and text_id != eff:
                effect_ids.add(text_id)
            compat = self.data_source.get_effect_conflict_id(eff)
            if compat != -1:
                compat_ids.add(compat)
                if self.data_source.get_effect_stacking_type(eff) == "no_stack":
                    no_stack_compat_ids.add(compat)
        return effect_ids, compat_ids, no_stack_compat_ids

    @staticmethod
    def _get_relic_curse_ids(relic: OwnedRelic) -> list[int]:
        return [c for c in relic.curses if c not in (EMPTY_EFFECT, 0)]
