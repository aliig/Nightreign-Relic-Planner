"""Vessel slot optimizer — backtrack (exhaustive) + greedy solvers."""
import time

from nrplanner.constants import EMPTY_EFFECT
from nrplanner.data import SourceDataHandler
from nrplanner.models import (
    BuildDefinition, OwnedRelic, RelicInventory,
    SlotAssignment, VesselResult, SCORED_TIERS,
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

        # Pre-assign pinned relics; returns (None, ...) if any can't fit this vessel.
        pinned_map, slot_owner = self._pre_assign_pinned(
            build, inventory, slot_colors, num_slots)
        if pinned_map is None:
            return []  # vessel incompatible with pinned relics — exclude

        pinned_handles: set[int] = set(pinned_map.keys())
        free_slot_indices = [i for i in range(num_slots) if slot_owner[i] is None]

        candidates_per_free_slot = []
        for i in free_slot_indices:
            is_deep = i >= 3
            candidates = inventory.get_candidates(slot_colors[i], is_deep)
            candidates = [
                r for r in candidates
                if not self.scorer.has_blacklisted_effect(r, build)
                and r.ga_handle not in pinned_handles
            ]
            scored = sorted(
                ((self.scorer.score_relic(r, build), r) for r in candidates),
                key=lambda x: x[0], reverse=True,
            )
            candidates_per_free_slot.append(scored)

        num_free = len(free_slot_indices)

        if num_free == 0:
            raw_free: list[list] = [[]]
        else:
            total = sum(len(c) for c in candidates_per_free_slot)
            if total <= 200 and num_free <= 6:
                raw_free = self._backtrack_solve(candidates_per_free_slot, num_free, build, top_n)
            else:
                raw_free = self._greedy_solve(candidates_per_free_slot, num_free, build, top_n)

        # Merge free-slot results back into full num_slots assignments
        raw: list[list] = []
        for free_assignment in raw_free:
            full: list = [(None, 0)] * num_slots
            for j, i in enumerate(free_slot_indices):
                full[i] = free_assignment[j]
            for i in range(num_slots):
                if slot_owner[i] is not None:
                    full[i] = (pinned_map[slot_owner[i]], 0)
            raw.append(full)

        return [
            self._build_vessel_result(assignment, num_slots, slot_colors, vessel_data, build)
            for assignment in raw
        ]

    def optimize_vessels_streaming(
        self,
        build: BuildDefinition,
        inventory: RelicInventory,
        hero_type: int,
        top_n: int = 10,
        max_per_vessel: int = 3,
    ):
        """Like optimize_all_vessels but yields events for SSE streaming.

        Yields dicts:
            {"type": "progress", "vessel": i, "total": n, "name": vessel_name}
            {"type": "result", "data": list[VesselResult]}   (final event)
        """
        vessels = list(self.data_source.get_all_vessels_for_hero(hero_type))
        total = len(vessels)
        all_results: list[VesselResult] = []

        for i, v in enumerate(vessels):
            vessel_data = dict(v)
            vessel_data["_id"] = v["vessel_id"]
            results = self.optimize(build, inventory, vessel_data, max_per_vessel)
            for r in results:
                r.vessel_id = v["vessel_id"]
            all_results.extend(results)
            yield {"type": "progress", "vessel": i + 1, "total": total, "name": v["Name"]}

        all_results.sort(key=lambda r: (not r.meets_requirements, -r.total_score))
        yield {"type": "result", "data": all_results[:top_n]}

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
        vessel_excl: set[int] = set()
        vessel_no_stack_excl: set[int] = set()
        vessel_no_stack_compat: set[int] = set()
        vessel_curse_counts: dict[int, int] = {}
        total_score = 0

        for i in range(num_slots):
            relic = assignments[i][0]
            if relic:
                score = self.scorer.score_relic_in_context(
                    relic, build, vessel_eff, vessel_excl, vessel_no_stack_excl,
                    vessel_curse_counts, vessel_no_stack_compat)
                breakdown = self.scorer.get_breakdown(
                    relic, build, vessel_eff, vessel_excl, vessel_no_stack_excl, vessel_no_stack_compat)
                assigned_effect_ids.update(relic.all_effects)
                for eff in relic.all_effects:
                    text_id = self.data_source.get_effect_text_id(eff)
                    if text_id != -1:
                        assigned_effect_ids.add(text_id)
                e, c, ns, nsc = self._get_relic_stacking_adds(relic)
                vessel_eff.update(e); vessel_excl.update(c); vessel_no_stack_excl.update(ns)
                vessel_no_stack_compat.update(nsc)
                for curse in self._get_relic_curse_ids(relic):
                    vessel_curse_counts[curse] = vessel_curse_counts.get(curse, 0) + 1
            else:
                score, breakdown = 0, []
            slot_results[i] = (relic, score, breakdown)
            total_score += score

        # Post-process: tier-family direction correction.
        # Per game rules, unique variants (+1/+2) always override the no_stack
        # base (+0) — the base is redundant whenever any variant is present.
        # The left-to-right slot loop above may have assigned the base first
        # (e.g. standard slot 0-2 before deep slot 3-5), causing the variants
        # to be falsely blocked.  Detect and fix that here.
        total_score = self._fix_tier_family_direction(slot_results, build, total_score)

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

        # Name-based resolution: if a required effect ID wasn't found directly or via
        # text_id, check if any assigned effect resolves to the same display name.
        # This mirrors the name-fallback in BuildScorer._resolve_tier_and_weight and
        # prevents false-positive "missing" warnings when alias effect IDs are used.
        uncovered = required_ids - assigned_effect_ids
        if uncovered:
            required_name_to_id: dict[str, int] = {}
            for req_id in uncovered:
                name = self.data_source.get_effect_name(req_id)
                if name and name not in ("", "Empty"):
                    required_name_to_id[name] = req_id
            if required_name_to_id:
                for eff in list(assigned_effect_ids):
                    name = self.data_source.get_effect_name(eff)
                    if name and name in required_name_to_id:
                        assigned_effect_ids.add(required_name_to_id[name])

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
        vessel_excl: set[int] = set()
        vessel_no_stack_excl: set[int] = set()
        vessel_no_stack_compat: set[int] = set()
        vessel_curse_counts: dict[int, int] = {}

        for slot_idx in range(num_slots):
            best: tuple | None = None
            for _, relic in candidates_per_slot[slot_idx]:
                if relic.ga_handle in used:
                    continue
                score = self.scorer.score_relic_in_context(
                    relic, build, vessel_eff, vessel_excl, vessel_no_stack_excl,
                    vessel_curse_counts, vessel_no_stack_compat)
                if best is None or score > best[0]:
                    best = (score, relic)

            if best is None:
                assigned[slot_idx] = (None, 0)
                continue

            score, relic = best
            assigned[slot_idx] = (relic, score)
            used.add(relic.ga_handle)
            e, c, ns, nsc = self._get_relic_stacking_adds(relic)
            vessel_eff.update(e); vessel_excl.update(c); vessel_no_stack_excl.update(ns)
            vessel_no_stack_compat.update(nsc)
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
                      v_eff: set, v_excl: set, v_no_stack_excl: set,
                      v_no_stack_compat: set,
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
            backtrack(slot_idx + 1, current, used, v_eff, v_excl, v_no_stack_excl,
                      v_no_stack_compat, curse_counts, score)

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
                    relic, build, v_eff, v_excl, v_no_stack_excl,
                    curse_counts, v_no_stack_compat)
                if score + ctx_score + remaining_max <= min_threshold:
                    continue  # actual-score prune

                added_eff, added_excl, added_ns, added_nsc = self._get_relic_stacking_adds(relic)
                added_curses = self._get_relic_curse_ids(relic)

                current[slot_idx] = (relic, ctx_score)
                used.add(relic.ga_handle)
                v_eff.update(added_eff); v_excl.update(added_excl); v_no_stack_excl.update(added_ns)
                v_no_stack_compat.update(added_nsc)
                for cid in added_curses:
                    curse_counts[cid] = curse_counts.get(cid, 0) + 1

                backtrack(slot_idx + 1, current, used, v_eff, v_excl, v_no_stack_excl,
                          v_no_stack_compat, curse_counts, score + ctx_score)

                used.discard(relic.ga_handle)
                v_eff -= added_eff; v_excl -= added_excl; v_no_stack_excl -= added_ns
                v_no_stack_compat -= added_nsc
                for cid in added_curses:
                    curse_counts[cid] -= 1
                    if curse_counts[cid] == 0:
                        del curse_counts[cid]

        backtrack(0, [(None, 0)] * num_slots, set(), set(), set(), set(), set(), {}, 0)
        return [assignment for _, assignment in top] or [[(None, 0)] * num_slots]

    # ------------------------------------------------------------------
    # Pinned relic pre-assignment
    # ------------------------------------------------------------------

    def _pre_assign_pinned(
        self, build: BuildDefinition, inventory: RelicInventory,
        slot_colors: tuple, num_slots: int,
    ) -> tuple[dict[int, OwnedRelic] | None, list[int | None]]:
        """Try to pre-assign pinned relics to vessel slots.

        Returns:
            (pinned_map, slot_owner) on success, where pinned_map maps
            ga_handle→OwnedRelic and slot_owner[i] is the ga_handle assigned
            to slot i (or None if the slot is free).
            Returns (None, []) if any pinned relic cannot fit any available slot
            (meaning this vessel should be excluded from results).
        """
        slot_owner: list[int | None] = [None] * num_slots
        if not build.pinned_relics:
            return {}, slot_owner

        pinned_map: dict[int, OwnedRelic] = {
            r.ga_handle: r
            for r in inventory.relics
            if r.ga_handle in build.pinned_relics
        }
        used_slots: set[int] = set()

        for ga_handle in build.pinned_relics:
            relic = pinned_map.get(ga_handle)
            if relic is None:
                continue  # pinned relic not in this character's inventory — skip

            assigned = False
            for i in range(num_slots):
                if i in used_slots:
                    continue
                is_deep = i >= 3
                if relic.is_deep != is_deep:
                    continue
                if slot_colors[i] != "White" and relic.color != slot_colors[i]:
                    continue
                slot_owner[i] = ga_handle
                used_slots.add(i)
                assigned = True
                break

            if not assigned:
                return None, []  # cannot fit — exclude vessel

        return pinned_map, slot_owner

    # ------------------------------------------------------------------
    # Tier-family direction correction
    # ------------------------------------------------------------------

    def _fix_tier_family_direction(
        self, slot_results: list, build: BuildDefinition, total_score: int,
    ) -> int:
        """Correct the no_stack-base vs. unique-variant scoring direction.

        The scoring loop assigns relics left-to-right (standard slots before
        deep slots).  If a no_stack base lands in an earlier slot it blocks
        all unique variants that arrive later, even though the game says the
        variant always overrides the base.

        For each tier-family compat group that contains BOTH a no_stack base
        AND at least one unique variant in the same vessel:
          1. Mark the base redundant (score → 0).
          2. Re-score variants in slot order: each unique eff_id scores once
             (the first occurrence wins); subsequent identical eff_ids stay
             redundant as duplicates.
        """
        # Collect all scored effects that belong to a real tier-family group.
        # Key: compat_id (the no_stack base's eff_id, which is self-referencing)
        # Value: list of (slot_i, bk_j, eff_id, stype)
        family_map: dict[int, list[tuple[int, int, int, str]]] = {}
        for slot_i, (relic, _score, breakdown) in enumerate(slot_results):
            if not relic:
                continue
            for bk_j, entry in enumerate(breakdown):
                tier = entry.get("tier")
                if tier not in SCORED_TIERS:
                    continue
                eff_id = entry["effect_id"]
                compat = self.data_source.get_effect_conflict_id(eff_id)
                # Only real tier-family groups: compat is self-referencing
                if compat == -1 or self.data_source.get_effect_conflict_id(compat) != compat:
                    continue
                stype = self.data_source.get_effect_stacking_type(eff_id)
                if stype in ("no_stack", "unique"):
                    family_map.setdefault(compat, []).append((slot_i, bk_j, eff_id, stype))

        for compat, members in family_map.items():
            has_base    = any(s == "no_stack" for _, _, _, s in members)
            has_variant = any(s == "unique"   for _, _, _, s in members)
            if not (has_base and has_variant):
                continue

            # Step 1: mark all no_stack bases as redundant.
            for slot_i, bk_j, eff_id, stype in members:
                if stype != "no_stack":
                    continue
                relic, slot_score, breakdown = slot_results[slot_i]
                entry = breakdown[bk_j]
                old = entry["score"]
                if old > 0:
                    entry["score"] = 0
                    entry["redundant"] = True
                    entry["override_status"] = "overridden"
                    slot_results[slot_i] = (relic, slot_score - old, breakdown)
                    total_score -= old

            # Step 2: re-score variants in slot order.
            # Each unique eff_id may score once; identical eff_ids after the
            # first are duplicates and stay redundant.
            placed_variant_effs: set[int] = set()
            for slot_i, bk_j, eff_id, stype in sorted(members, key=lambda m: m[0]):
                if stype != "unique":
                    continue
                if eff_id in placed_variant_effs:
                    # Duplicate of an already-scored variant — stays redundant.
                    continue
                placed_variant_effs.add(eff_id)
                relic, slot_score, breakdown = slot_results[slot_i]
                entry = breakdown[bk_j]
                if not entry["redundant"]:
                    # Already scoring correctly (e.g. variant was placed before
                    # the base in the original loop).
                    continue
                # This variant was wrongly blocked by the base — restore its score.
                _, weight = self.scorer._resolve_tier_and_weight(eff_id, build)
                if weight <= 0:
                    continue
                entry["score"] = weight
                entry["redundant"] = False
                entry["override_status"] = None
                slot_results[slot_i] = (relic, slot_score + weight, breakdown)
                total_score += weight

        return total_score

    # ------------------------------------------------------------------
    # Stacking state helpers
    # ------------------------------------------------------------------

    def _get_relic_stacking_adds(self, relic: OwnedRelic) -> tuple[set, set, set, set]:
        effect_ids: set[int] = set()
        exclusivity_ids: set[int] = set()
        no_stack_exclusivity_ids: set[int] = set()
        no_stack_compat_ids: set[int] = set()
        for eff in relic.all_effects:
            effect_ids.add(eff)
            text_id = self.data_source.get_effect_text_id(eff)
            if text_id != -1 and text_id != eff:
                effect_ids.add(text_id)
            compat = self.data_source.get_effect_conflict_id(eff)
            stype = self.data_source.get_effect_stacking_type(eff)
            excl = self.data_source.get_effect_exclusivity_id(eff)
            if excl != -1:
                exclusivity_ids.add(excl)
                if stype == "no_stack":
                    no_stack_exclusivity_ids.add(excl)
            # Tier-family compat tracking for no_stack mutual exclusion:
            # Rule 1: no_stack base placed (compat == eff_id, self-referencing)
            if stype == "no_stack" and compat != -1 and compat == eff:
                no_stack_compat_ids.add(compat)
            # Rule 2: variant placed that points to a real no_stack tier-family base.
            # Guard: compat must be self-referencing (a real tier-family base ID, not a
            # mega-group sentinel like 100).  Add the base's eff_id to effect_ids so the
            # base is blocked via the identity check (eff_id in vessel_effect_ids).
            # Do NOT add to no_stack_compat_ids — that would incorrectly block sibling
            # variants (e.g. HP Restore +2 blocked when +1 is placed).
            elif compat != -1 and compat != eff:
                if (self.data_source.get_effect_conflict_id(compat) == compat
                        and self.data_source.get_effect_stacking_type(compat) == "no_stack"):
                    effect_ids.add(compat)
        return effect_ids, exclusivity_ids, no_stack_exclusivity_ids, no_stack_compat_ids

    @staticmethod
    def _get_relic_curse_ids(relic: OwnedRelic) -> list[int]:
        return [c for c in relic.curses if c not in (EMPTY_EFFECT, 0)]
