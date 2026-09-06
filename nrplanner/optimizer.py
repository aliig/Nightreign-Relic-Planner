"""Vessel slot optimizer — backtrack (exhaustive) + greedy solvers."""
from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ProcessPoolExecutor, as_completed

from nrplanner.constants import EMPTY_EFFECT
from nrplanner.data import SourceDataHandler
from nrplanner.models import (
    BuildDefinition, OwnedRelic, RelicInventory,
    SlotAssignment, VesselResult, VesselState,
)
from nrplanner import solver_bridge
from nrplanner.scoring import BuildScorer, score_profile

log = logging.getLogger(__name__)

# Time budget for one vessel's exhaustive backtracking search.  The solver is
# optimal when it finishes within this window; if it is hit, it returns the
# best layout found so far (always >= greedy) and flags the result via
# VesselResult.search_truncated.  Generous by default — pruning normally makes
# the search finish in well under a second even on large inventories.
DEFAULT_BACKTRACK_DEADLINE_SECS = 10.0

# Bump whenever a change to the solver/scoring could alter optimization results.
# Recorded in OptimizationSnapshot provenance so old snapshots are known to be
# incomparable after an algorithm change (forces a fresh diff baseline).
# v2: positive-sum candidate filter/bounds + dedup/unlock-aware ctx<=0 skip —
#     the solver can now find optima that include net-negative relics.
# v3: sign-forked effect/family limits — a limit on a negatively-weighted
#     effect is a per-effect tolerance (excess disqualifies, and for curses it
#     replaces the build-wide curse_max) instead of a score cap.
# v4: Required entries are a hard constraint — when a vessel can cover them,
#     every returned loadout does; covering results rank above non-covering
#     ones; get_effective_requirements no longer derives pseudo-requirements
#     from the highest-weight group.
# v5: effects the build's character cannot use are inert — the game greys them
#     out, so they no longer score, occupy a stacking slot, or trip the
#     excluded-stacking-category suppression (e.g. a Seppuku relic is fair game
#     on Raider, who starts with a colossal weapon).
OPTIMIZER_VERSION = 5

# Engine name -> VesselOptimizer method implementing the free-slot solver seam.
# Both entries share one signature; see _solve_free_slots_python.
_SOLVER_METHODS = {
    "python": "_solve_free_slots_python",
    "rust": "_solve_free_slots_rust",
}

# ---------------------------------------------------------------------------
# Worker-process globals (set once per worker by init_optimizer_worker)
# ---------------------------------------------------------------------------
_worker_ds: SourceDataHandler | None = None
_worker_scorer: BuildScorer | None = None


def init_optimizer_worker() -> None:
    """ProcessPoolExecutor initializer — one SourceDataHandler per worker."""
    global _worker_ds, _worker_scorer
    _worker_ds = SourceDataHandler(language="en_US")
    _worker_scorer = BuildScorer(_worker_ds)
    # Warm up lazy caches so the first task doesn't pay init cost
    _ = _worker_ds._reachable_effect_ids  # noqa: F841  # cached_property
    _worker_ds.get_effect_stacking_type(0)  # loads _stacking_cache
    _worker_ds.get_effect_family(0)  # loads _effect_families


def _optimize_vessel_task(
    build: BuildDefinition,
    relics: list[OwnedRelic],
    vessel_data: dict,
    max_per_vessel: int,
    deadline_secs: float = DEFAULT_BACKTRACK_DEADLINE_SECS,
) -> tuple[int, str, list[VesselResult], float]:
    """Top-level picklable worker function for ProcessPoolExecutor."""
    assert _worker_ds is not None and _worker_scorer is not None
    t0 = time.perf_counter()
    inventory = RelicInventory.from_owned_relics(relics)
    optimizer = VesselOptimizer(_worker_ds, _worker_scorer)
    results = optimizer.optimize(
        build, inventory, vessel_data, max_per_vessel, deadline_secs=deadline_secs)
    vessel_id = vessel_data.get("_id", 0)
    for r in results:
        r.vessel_id = vessel_id
    solve_ms = (time.perf_counter() - t0) * 1000.0
    return (vessel_id, vessel_data["Name"], results, solve_ms)


class VesselOptimizer:
    """Finds optimal relic assignments for vessel slots."""

    def __init__(self, data_source: SourceDataHandler, scorer: BuildScorer):
        self.data_source = data_source
        self.scorer = scorer
        # Filled in by optimize() — engine/nodes/truncated/solve_ms/candidates
        # of the most recent free-slot solve.  Diagnostics only (the parity
        # test and scripts/bench_solver.py read it); nothing in the app does.
        self.last_solve_stats: dict = {}

    @staticmethod
    def _placed_effects_per_slot(result: VesselResult) -> list[set[int]]:
        """Per-slot effect IDs (effects + curses), in slot order (leftmost first)."""
        return [
            set(slot.relic.all_effects) if slot.relic else set()
            for slot in result.assignments
        ]

    @staticmethod
    def _log_run_summary(kind: str, build: BuildDefinition, hero_type: int,
                         n_vessels: int, n_relics: int,
                         all_results: list[VesselResult], t_run: float) -> None:
        truncated = sum(1 for r in all_results if r.search_truncated)
        log.info(
            "optimize run kind=%s build=%r hero=%d vessels=%d relics=%d "
            "truncated_results=%d wall_ms=%.0f",
            kind, build.name, hero_type, n_vessels, n_relics, truncated,
            (time.perf_counter() - t_run) * 1000.0,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def optimize(self, build: BuildDefinition, inventory: RelicInventory,
                 vessel_data: dict, top_n: int = 3,
                 deadline_secs: float = DEFAULT_BACKTRACK_DEADLINE_SECS,
                 engine: str | None = None,
                 ) -> list[VesselResult]:
        """Best relic assignments for one vessel. Returns up to top_n results.

        Explicit Required entries are a hard constraint: when this vessel can
        cover them, every returned loadout does.  When it can't, the ordinary
        unconstrained best-effort results are returned with
        meets_requirements=False.
        """
        # Compiled relic profiles are memoized by ga_handle, which only
        # identifies a relic within one inventory -- see bind_inventory.
        self.scorer.bind_inventory(inventory)

        slot_colors = vessel_data["Colors"]
        num_slots = 6 if build.include_deep else 3
        t_start = time.perf_counter()

        # Precompute conflict penalty weights once per optimization call.
        desired_cw = self.scorer.get_desired_conflict_weights(build)
        desired_compat_effs = self.scorer.get_desired_compat_effects(build)

        # Pre-resolve user-defined effect/family limits.
        effect_limit_by_name, family_limit_map = self._prepare_limits(build)

        # Pre-assign pinned relics; returns (None, ...) if any can't fit this vessel.
        pinned_map, slot_owner = self._pre_assign_pinned(
            build, inventory, slot_colors, num_slots)
        if pinned_map is None:
            # No solve ran, so the previous vessel's stats must not stand.
            self.last_solve_stats = {
                "engine": solver_bridge.resolve_engine(engine), "nodes": 0,
                "truncated": False, "solve_ms": 0.0, "candidates": [],
                "skipped": True,
            }
            return []  # vessel incompatible with pinned relics — exclude

        pinned_handles: set[int] = set(pinned_map.keys())
        excluded_handles: set[int] = set(build.excluded_relics)
        free_slot_indices = [i for i in range(num_slots) if slot_owner[i] is None]

        # Requirement hard-constraint: explicit Required entries only.
        # req_masks maps ga_handle -> bitmask of covered requirement specs;
        # pinned relics count toward coverage via pinned_mask.
        req_specs = self._requirement_specs(build)
        full_mask = (1 << len(req_specs)) - 1
        pinned_mask = 0
        if req_specs:
            for relic in pinned_map.values():
                pinned_mask |= self._relic_req_mask(relic, req_specs)

        num_free = len(free_slot_indices)
        engine_name = solver_bridge.resolve_engine(engine)
        solve = getattr(self, _SOLVER_METHODS[engine_name])
        t_solve = time.perf_counter()
        raw_free, search_truncated, nodes_expanded, cand_counts = solve(
            build, inventory, slot_colors, free_slot_indices,
            pinned_handles, excluded_handles, req_specs, pinned_mask,
            full_mask, top_n, deadline_secs, desired_cw, desired_compat_effs,
            effect_limit_by_name, family_limit_map)
        self.last_solve_stats = {
            "engine": engine_name,
            "nodes": nodes_expanded,
            "truncated": search_truncated,
            "solve_ms": (time.perf_counter() - t_solve) * 1000.0,
            "candidates": cand_counts,
        }


        # When solvers find no useful free-slot relics, still produce one
        # result so pinned relics (if any) are represented.
        if not raw_free:
            raw_free = [[(None, 0)] * num_free]

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

        # Drop results where no relic was assigned at all
        raw = [r for r in raw if any(relic is not None for relic, _ in r)]

        results = [
            self._build_vessel_result(
                assignment, num_slots, slot_colors, vessel_data, build, desired_cw,
                desired_compat_effs, effect_limit_by_name, family_limit_map,
                search_truncated=search_truncated)
            for assignment in raw
        ]

        # Post-hoc filter: drop results where the desired effect of an
        # excluded stacking category is missing OR is overridden by an
        # undesired competitor placed to its left (in-game, the leftmost
        # effect in a no_stack compat wins).
        if desired_compat_effs:
            results = [
                r for r in results
                if not self.scorer.has_orphaned_excl_category_effects(
                    self._placed_effects_per_slot(r), build, desired_compat_effs)
            ]

        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "vessel=%r slots=%d candidates=%s nodes=%d truncated=%s elapsed_ms=%.1f",
                vessel_data.get("Name"), num_slots,
                cand_counts,
                nodes_expanded, search_truncated,
                (time.perf_counter() - t_start) * 1000.0,
            )
        return results

    def _solve_free_slots_python(
        self,
        build: BuildDefinition,
        inventory: RelicInventory,
        slot_colors: tuple,
        free_slot_indices: list[int],
        pinned_handles: set[int],
        excluded_handles: set[int],
        req_specs: list[tuple[frozenset[int], str | None]],
        pinned_mask: int,
        full_mask: int,
        top_n: int,
        deadline_secs: float,
        desired_cw: dict[int, int] | None,
        desired_compat_effs: dict[int, set[int]] | None,
        effect_limit_by_name: dict[str, int] | None,
        family_limit_map: dict[str, int] | None,
    ) -> tuple[list[list], bool, int, list[int]]:
        """Candidate build + greedy + backtrack + merge for the free slots.

        The solver seam: everything between "which relics may go in each free
        slot" and "turn raw assignments into VesselResults".  Returns
        ``(raw_free, search_truncated, nodes_expanded, candidate_counts)``
        where ``raw_free`` is a list of per-free-slot ``(relic|None, score)``
        assignments.
        """
        search_truncated = False
        nodes_expanded = 0
        # ga_handle -> bitmask of requirement specs the relic satisfies,
        # filled lazily as candidates are walked.
        req_masks: dict[int, int] = {}

        candidates_per_free_slot = []
        for i in free_slot_indices:
            is_deep = i >= 3
            candidates = inventory.get_candidates(slot_colors[i], is_deep)
            candidates = [
                r for r in candidates
                if not self.scorer.has_excluded_effect(r, build, desired_compat_effs)
                and r.ga_handle not in pinned_handles
                and r.ga_handle not in excluded_handles
            ]
            # Pre-filter on the POSITIVE pre-score (sum of positive weights):
            # an effect contributes at most max(0, weight) in any context, so
            # pos <= 0 relics can never help and are safe to drop.  The net
            # pre-score is NOT a sound filter — a negatively-weighted
            # duplicate dedups to 0 in context, so a net<=0 relic can still
            # belong to the optimum.  Candidates are ordered by net score
            # (finds good solutions early => aggressive pruning); the stored
            # value is the positive bound, which is what pruning must use.
            # Survivors are compiled into RelicProfiles (memoized per build on
            # the scorer, so slots/vessels sharing a relic compile it once) —
            # the solvers below run entirely on profiles.
            scored = []
            for r in candidates:
                r_mask = 0
                if req_specs and r.ga_handle not in req_masks:
                    r_mask = self._relic_req_mask(r, req_specs)
                    req_masks[r.ga_handle] = r_mask
                elif req_specs:
                    r_mask = req_masks[r.ga_handle]
                # A requirement carrier survives even at pos <= 0 — it can be
                # mandatory at a net loss (e.g. a family-required tier whose
                # magnitude weight rounds to 0 on an otherwise dead relic).
                if r_mask == 0 and self.scorer.positive_pre_score(r, build) <= 0:
                    continue
                prof = self.scorer.compile_profile(
                    r, build, effect_limit_by_name, family_limit_map)
                scored.append((prof.net, prof.pos_bound, prof))
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates_per_free_slot.append([(pos, p) for _net, pos, p in scored])

        num_free = len(free_slot_indices)

        # Per-free-slot suffix unions of requirement coverage: what the slots
        # from index j onward can still contribute.  suffix[num_free] = 0
        # doubles as leaf rejection in the backtracker.  The union is
        # optimistic (ignores one-relic-per-slot and handle reuse), so it is
        # a sound prune but "feasible" here does not guarantee coverable.
        req_suffix_union: list[int] = []
        vessel_can_cover = True
        if req_specs:
            req_suffix_union = [0] * (num_free + 1)
            for j in range(num_free - 1, -1, -1):
                slot_union = 0
                for _pos, p in candidates_per_free_slot[j]:
                    slot_union |= req_masks.get(p.ga_handle, 0)
                req_suffix_union[j] = req_suffix_union[j + 1] | slot_union
            vessel_can_cover = (pinned_mask | req_suffix_union[0]) == full_mask
        # When the vessel can't possibly cover, fall back to the ordinary
        # unconstrained solve — its best-effort results are flagged
        # meets_requirements=False by _build_vessel_result.
        constrained = bool(req_specs) and vessel_can_cover

        def _covers(assignment: list) -> bool:
            m = pinned_mask
            for r, _s in assignment:
                if r is not None:
                    m |= req_masks.get(r.ga_handle, 0)
            return m == full_mask

        if num_free == 0:
            raw_free: list[list] = [[]]
        else:
            # Always run greedy first — it's fast O(n*k) and gives a
            # baseline score that seeds backtracking for aggressive pruning.
            raw_free = self._greedy_solve(
                candidates_per_free_slot, num_free, build, top_n, desired_cw,
                desired_compat_effs, effect_limit_by_name, family_limit_map)

            # Always run the exhaustive backtracking solver for an optimal
            # assignment.  Greedy commits each slot before later slots are
            # scored, so it can miss globally better placements — e.g. when a
            # family/effect limit only bites once a later deep slot is filled,
            # greedy over-values the earlier slot and locks in a worse relic.
            # The per-call deadline in _backtrack_solve bounds worst-case
            # runtime, and the greedy result is retained in the merge below as
            # a floor whenever the search is truncated.  (Previously this was
            # gated behind a `total <= 500` candidate cap, which silently
            # disabled optimal search for large inventories.)
            if constrained:
                # Only a requirement-COVERING assignment may seed the pruning
                # threshold: the covering optimum can score below the best
                # unconstrained greedy, so a non-covering seed could prune
                # every valid optimum away.
                covering_scores = [
                    sum(s for _, s in a) for a in raw_free if _covers(a)]
                if covering_scores:
                    initial_threshold = max(covering_scores) - 1
                else:
                    cover = self._greedy_cover_once(
                        candidates_per_free_slot, num_free, build, req_masks,
                        req_suffix_union, full_mask, pinned_mask, desired_cw,
                        desired_compat_effs, effect_limit_by_name,
                        family_limit_map)
                    if cover is not None:
                        raw_free.append(cover)
                        initial_threshold = sum(s for _, s in cover) - 1
                    else:
                        initial_threshold = -(1 << 60)  # search unseeded
            else:
                greedy_best = max(
                    (sum(s for _, s in a) for a in raw_free), default=0)
                initial_threshold = greedy_best - 1
            ctx_helpers = self._ctx_helper_map(
                candidates_per_free_slot, build, desired_compat_effs)
            # Leaf-level excluded-category validation needs absolute slot
            # order; with pinned slots the free-slot indices no longer align,
            # so the post-hoc filter alone handles those (rare) builds.
            validate_leaves = bool(desired_compat_effs) and not pinned_handles
            bt_results, search_truncated, nodes_expanded = self._backtrack_solve(
                candidates_per_free_slot, num_free, build, top_n, desired_cw,
                desired_compat_effs, initial_threshold=initial_threshold,
                effect_limit_by_name=effect_limit_by_name,
                family_limit_map=family_limit_map, deadline_secs=deadline_secs,
                ctx_helpers=ctx_helpers, validate_leaves=validate_leaves,
                req_masks=req_masks if constrained else None,
                req_suffix_union=req_suffix_union if constrained else None,
                req_full_mask=full_mask, req_initial_mask=pinned_mask)
            if bt_results:
                # Merge and deduplicate by relic set
                seen: set[frozenset] = set()
                merged: list[list] = []
                for assignment in bt_results + raw_free:
                    key = frozenset(
                        r.ga_handle for r, _ in assignment if r is not None)
                    if key not in seen:
                        seen.add(key)
                        merged.append(assignment)
                raw_free = merged
            if constrained:
                # Constrained backtrack results always cover; greedy layouts
                # may not.  When any covering layout exists, non-covering ones
                # are dropped; when none does (truly infeasible slot packing,
                # or a truncated search), greedy stays as the flagged fallback.
                covering = [a for a in raw_free if _covers(a)]
                if covering:
                    raw_free = covering

        return (raw_free, search_truncated, nodes_expanded,
                [len(c) for c in candidates_per_free_slot])

    # ------------------------------------------------------------------
    # Requirement hard-constraint helpers
    # ------------------------------------------------------------------

    def _requirement_specs(
        self, build: BuildDefinition,
    ) -> list[tuple[frozenset[int], str | None]]:
        """One (match_ids, match_name) spec per explicit Required entry.

        A relic satisfies a spec when any of its effects — or their text_id
        aliases — is in ``match_ids``, or (effect requirements with a real
        display name) shares the requirement's display name.  Mirrors the
        alias resolution of the post-hoc missing-requirements check in
        ``_build_vessel_result`` so enforcement and reporting always agree.
        """
        ds = self.data_source
        specs: list[tuple[frozenset[int], str | None]] = []
        for req_id in build.required_effects:
            name = ds.get_effect_name(req_id)
            specs.append((frozenset((req_id,)),
                          name if name and name not in ("", "Empty") else None))
        for family in build.required_families:
            specs.append((frozenset(ds.get_family_effect_ids(family)), None))
        return specs

    def _relic_req_mask(
        self, relic: OwnedRelic,
        req_specs: list[tuple[frozenset[int], str | None]],
    ) -> int:
        """Bitmask of requirement specs this relic satisfies (bit i = spec i)."""
        ds = self.data_source
        mask = 0
        for bit, (match_ids, match_name) in enumerate(req_specs):
            for eff in relic.all_effects:
                text_id = ds.get_effect_text_id(eff)
                if (eff in match_ids
                        or (text_id != -1 and text_id in match_ids)
                        or (match_name is not None
                            and (ds.get_effect_name(eff) == match_name
                                 or (text_id != -1
                                     and ds.get_effect_name(text_id)
                                     == match_name)))):
                    mask |= 1 << bit
                    break
        return mask

    def _prepare_limits(
        self, build: BuildDefinition,
    ) -> tuple[dict[str, int] | None, dict[str, int] | None]:
        """Resolve user-defined effect/family limits to name-keyed maps."""
        effect_limit_by_name: dict[str, int] | None = None
        family_limit_map: dict[str, int] | None = None
        if build.effect_limits:
            effect_limit_by_name = {}
            for eff_id, max_count in build.effect_limits.items():
                name = self.data_source.get_effect_name(eff_id)
                if name and name not in ("", "Empty"):
                    effect_limit_by_name[name] = min(
                        effect_limit_by_name.get(name, max_count), max_count)
        if build.family_limits:
            family_limit_map = dict(build.family_limits)
        return effect_limit_by_name, family_limit_map

    def optimize_locked_slot(
        self,
        build: BuildDefinition,
        inventory: RelicInventory,
        vessel_data: dict,
        locked: dict[int, int],
        struck_slot_index: int,
        top_n: int = 1,
    ) -> list[VesselResult]:
        """Re-fill ONE slot while every other slot stays frozen in place.

        Unlike ``pinned_relics`` — which re-packs relics first-fit and can move
        them into different slots — this keeps each locked relic in its exact
        slot and only searches ``struck_slot_index``.  The returned arrangement
        therefore differs from the input in that one slot only, so the total
        score is a clean function of the struck relic and stays monotonic across
        repeated strikes (each strike removes one option from a fixed layout).

        ``locked`` maps slot_index -> ga_handle for the slots to freeze.
        Candidates for the struck slot exclude the locked relics and
        ``build.excluded_relics``.  The empty-slot option is included, so the
        struck slot may come back empty when no relic improves the build.
        Returns up to ``top_n`` VesselResults ranked by total score.
        """
        slot_colors = vessel_data["Colors"]
        num_slots = 6 if build.include_deep else 3
        desired_cw = self.scorer.get_desired_conflict_weights(build)
        desired_compat = self.scorer.get_desired_compat_effects(build)
        effect_limit_by_name, family_limit_map = self._prepare_limits(build)

        by_handle = {r.ga_handle: r for r in inventory.relics}
        base: list = [(None, 0)] * num_slots
        locked_handles: set[int] = set()
        for i in range(num_slots):
            if i == struck_slot_index:
                continue
            relic = by_handle.get(locked.get(i)) if locked.get(i) is not None else None
            if relic is not None:
                base[i] = (relic, 0)
                locked_handles.add(relic.ga_handle)

        excluded = set(build.excluded_relics)
        is_deep = struck_slot_index >= 3
        candidates = [
            r for r in inventory.get_candidates(slot_colors[struck_slot_index], is_deep)
            if r.ga_handle not in locked_handles
            and r.ga_handle not in excluded
            and not self.scorer.has_excluded_effect(r, build, desired_compat)
        ]

        results: list[VesselResult] = []
        seen: set[tuple] = set()
        # `None` = leave the struck slot empty (always a valid fallback).
        for cand in [None, *candidates]:
            assignment = list(base)
            assignment[struck_slot_index] = (cand, 0)
            if all(relic is None for relic, _ in assignment):
                continue
            vr = self._build_vessel_result(
                assignment, num_slots, slot_colors, vessel_data, build,
                desired_cw, desired_compat, effect_limit_by_name, family_limit_map)
            fp = vr.layout_fingerprint()
            if fp in seen:
                continue
            seen.add(fp)
            results.append(vr)

        # Requirement-covering replacements outrank higher-scoring ones that
        # would lose a required entry (same tiering as _dedup_rank).
        results.sort(key=lambda r: (not r.meets_requirements, -r.total_score))
        return results[:top_n]

    def submit_all_vessels(
        self,
        build: BuildDefinition,
        inventory: RelicInventory,
        hero_type: int,
        max_per_vessel: int = 3,
        executor: ProcessPoolExecutor | None = None,
        deadline_secs: float = DEFAULT_BACKTRACK_DEADLINE_SECS,
        vessel_ids: set[int] | None = None,
    ) -> dict[Future, dict]:
        """Submit one pool task per vessel; returns the future→vessel map.

        Multi-build flows submit the NEXT build's vessels before draining the
        current build's futures (depth-1 prefetch): the pool's FIFO queue then
        fills idle workers during each build's completion tail.  Consume with
        ``collect_all_vessels`` or ``optimize_vessels_streaming(presubmitted=…)``.

        ``vessel_ids`` restricts the run to those vessels; the caller is then
        responsible for supplying the rest as ``carried`` results.
        """
        vessels = self._vessel_subset(hero_type, vessel_ids)
        relics = inventory.relics
        futures: dict[Future, dict] = {}
        for v in vessels:
            vd = dict(v)
            vd["_id"] = v["vessel_id"]
            fut = executor.submit(
                _optimize_vessel_task, build, relics, vd, max_per_vessel,
                deadline_secs,
            )
            futures[fut] = v
        return futures

    def _vessel_subset(self, hero_type: int,
                       vessel_ids: set[int] | None) -> list[dict]:
        """The hero's vessels, optionally narrowed to ``vessel_ids``."""
        vessels = list(self.data_source.get_all_vessels_for_hero(hero_type))
        if vessel_ids is None:
            return vessels
        return [v for v in vessels if v["vessel_id"] in vessel_ids]

    @staticmethod
    def _dedup_rank(all_results: list[VesselResult], top_n: int) -> list[VesselResult]:
        """Drop functionally identical layouts (same effects per slot, different
        physical copies of a relic), then rank requirement-covering results
        first, score-descending within each tier."""
        seen_layouts: set[tuple] = set()
        unique: list[VesselResult] = []
        for r in all_results:
            fp = r.layout_fingerprint()
            if fp not in seen_layouts:
                seen_layouts.add(fp)
                unique.append(r)
        unique.sort(key=lambda r: (not r.meets_requirements, -r.total_score))
        return unique[:top_n]

    def collect_all_vessels(
        self,
        build: BuildDefinition,
        hero_type: int,
        futures: dict[Future, dict],
        top_n: int = 10,
        n_relics: int = 0,
    ) -> list[VesselResult]:
        """Gather previously submitted vessel tasks into ranked results."""
        all_results: list[VesselResult] = []
        t_run = time.perf_counter()
        for future in as_completed(futures):
            _vid, name, results, solve_ms = future.result()
            all_results.extend(results)
            log.debug("vessel=%r solve_ms=%.1f (worker)", name, solve_ms)
        self._log_run_summary("all_vessels", build, hero_type, len(futures),
                              n_relics, all_results, t_run)
        return self._dedup_rank(all_results, top_n)

    def optimize_vessels_streaming(
        self,
        build: BuildDefinition,
        inventory: RelicInventory,
        hero_type: int,
        top_n: int = 10,
        max_per_vessel: int = 3,
        executor: ProcessPoolExecutor | None = None,
        deadline_secs: float = DEFAULT_BACKTRACK_DEADLINE_SECS,
        presubmitted: dict[Future, dict] | None = None,
        vessel_ids: set[int] | None = None,
        carried: list[VesselResult] | None = None,
    ):
        """Like optimize_all_vessels but yields events for SSE streaming.

        Yields dicts:
            {"type": "progress", "vessel": i, "total": n, "name": vessel_name}
            {"type": "result", "data": list[VesselResult]}   (final event)

        When *executor* is provided, vessels are optimized in parallel across
        worker processes.  Progress events arrive as each vessel completes
        (non-deterministic order).  ``presubmitted`` (from
        ``submit_all_vessels``, requires *executor*) consumes already-submitted
        futures instead of submitting fresh ones — multi-build flows use it
        for depth-1 prefetch across builds.

        ``vessel_ids`` optimizes only those vessels and ``carried`` supplies
        results for the ones left out, so a caller that can prove some vessels
        are untouched pays only for the rest.  Carried results join the final
        ranking exactly as freshly computed ones do — the caller owns the proof
        that they are still valid.
        """
        all_results: list[VesselResult] = list(carried or [])
        t_run = time.perf_counter()

        if executor is None:
            # Sequential path (unchanged apart from the vessel subset)
            vessels = self._vessel_subset(hero_type, vessel_ids)
            total = len(vessels)
            for i, v in enumerate(vessels):
                vessel_data = dict(v)
                vessel_data["_id"] = v["vessel_id"]
                results = self.optimize(build, inventory, vessel_data, max_per_vessel,
                                        deadline_secs=deadline_secs)
                for r in results:
                    r.vessel_id = v["vessel_id"]
                all_results.extend(results)
                yield {"type": "progress", "vessel": i + 1, "total": total, "name": v["Name"]}
        else:
            futures = (presubmitted if presubmitted is not None
                       else self.submit_all_vessels(
                           build, inventory, hero_type, max_per_vessel,
                           executor, deadline_secs, vessel_ids))
            total = len(futures)
            completed = 0
            for future in as_completed(futures):
                _vid, name, results, solve_ms = future.result()
                all_results.extend(results)
                completed += 1
                log.debug("vessel=%r solve_ms=%.1f (worker)", name, solve_ms)
                yield {"type": "progress", "vessel": completed, "total": total, "name": name}

        self._log_run_summary("streaming", build, hero_type, total,
                              len(inventory), all_results, t_run)
        yield {"type": "result", "data": self._dedup_rank(all_results, top_n)}

    def optimize_all_vessels(self, build: BuildDefinition, inventory: RelicInventory,
                             hero_type: int, top_n: int = 10,
                             max_per_vessel: int = 3,
                             executor: ProcessPoolExecutor | None = None,
                             deadline_secs: float = DEFAULT_BACKTRACK_DEADLINE_SECS,
                             ) -> list[VesselResult]:
        """Optimize all vessels for a hero. Returns top_n globally ranked results.

        Results that meet requirements come before those that don't, then sorted
        by score descending.

        When *executor* is provided, vessels are optimized in parallel across
        worker processes.
        """
        if executor is not None:
            futures = self.submit_all_vessels(
                build, inventory, hero_type, max_per_vessel, executor,
                deadline_secs)
            return self.collect_all_vessels(
                build, hero_type, futures, top_n, n_relics=len(inventory))

        vessels = list(self.data_source.get_all_vessels_for_hero(hero_type))
        all_results: list[VesselResult] = []
        t_run = time.perf_counter()
        for v in vessels:
            vessel_data = dict(v)
            vessel_data["_id"] = v["vessel_id"]
            results = self.optimize(build, inventory, vessel_data, max_per_vessel,
                                    deadline_secs=deadline_secs)
            for r in results:
                r.vessel_id = v["vessel_id"]
            all_results.extend(results)

        self._log_run_summary("all_vessels", build, hero_type, len(vessels),
                              len(inventory), all_results, t_run)
        return self._dedup_rank(all_results, top_n)

    # ------------------------------------------------------------------
    # Result builder
    # ------------------------------------------------------------------

    def _build_vessel_result(self, assignments: list, num_slots: int,
                             slot_colors: tuple, vessel_data: dict,
                             build: BuildDefinition,
                             desired_conflict_weights: dict[int, int] | None = None,
                             desired_compat_effects: dict[int, set[int]] | None = None,
                             effect_limit_by_name: dict[str, int] | None = None,
                             family_limit_map: dict[str, int] | None = None,
                             search_truncated: bool = False,
                             ) -> VesselResult:
        """Construct VesselResult from raw slot assignments (left-to-right priority)."""
        slot_results: list[tuple] = [(None, 0, [])] * num_slots
        assigned_effect_ids: set[int] = set()
        state = VesselState(
            self.data_source,
            desired_conflict_weights=desired_conflict_weights,
            desired_compat_effects=desired_compat_effects,
            effect_limit_by_name=effect_limit_by_name,
            family_limit_map=family_limit_map,
            character=build.character,
        )
        total_score = 0

        for i in range(num_slots):
            relic = assignments[i][0]
            if relic:
                score = self.scorer.score_relic_in_context(relic, build, state)
                breakdown = self.scorer.get_breakdown(relic, build, state)
                assigned_effect_ids.update(relic.all_effects)
                for eff in relic.all_effects:
                    text_id = self.data_source.get_effect_text_id(eff)
                    if text_id != -1:
                        assigned_effect_ids.add(text_id)
                state.place(relic)
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
        eff_reqs, fam_reqs = build.get_effective_requirements()
        required_ids = set(eff_reqs)

        # Name-based resolution: if a required effect ID wasn't found directly or via
        # text_id, check if any assigned effect resolves to the same display name.
        # Required for alias resolution — many game effects share a display name
        # but have completely different IDs and text_ids.
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
        for family in fam_reqs:
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
            search_truncated=search_truncated,
        )

    # ------------------------------------------------------------------
    # Solvers
    # ------------------------------------------------------------------

    def _greedy_solve(self, candidates_per_slot: list, num_slots: int,
                      build: BuildDefinition, top_n: int = 3,
                      desired_cw: dict[int, int] | None = None,
                      desired_compat_effs: dict[int, set[int]] | None = None,
                      effect_limit_by_name: dict[str, int] | None = None,
                      family_limit_map: dict[str, int] | None = None,
                      ) -> list[list]:
        results: list[list] = []
        excluded: set[int] = set()
        seen: set[frozenset] = set()

        for _ in range(top_n):
            assignment = self._greedy_solve_once(
                candidates_per_slot, num_slots, build, excluded, desired_cw,
                desired_compat_effs, effect_limit_by_name, family_limit_map)
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

        return results

    def _greedy_solve_once(self, candidates_per_slot: list, num_slots: int,
                           build: BuildDefinition,
                           excluded_handles: set[int] | None = None,
                           desired_cw: dict[int, int] | None = None,
                           desired_compat_effs: dict[int, set[int]] | None = None,
                           effect_limit_by_name: dict[str, int] | None = None,
                           family_limit_map: dict[str, int] | None = None,
                           ) -> list:
        assigned: list = [None] * num_slots
        used: set[int] = set(excluded_handles or ())
        curse_max = build.curse_max
        state = VesselState(
            self.data_source,
            desired_conflict_weights=desired_cw,
            desired_compat_effects=desired_compat_effs,
            effect_limit_by_name=effect_limit_by_name,
            family_limit_map=family_limit_map,
            character=build.character,
        )

        for slot_idx in range(num_slots):
            best: tuple | None = None
            for _, prof in candidates_per_slot[slot_idx]:
                if prof.ga_handle in used:
                    continue
                score = score_profile(prof, state, curse_max)
                if best is None or score > best[0]:
                    best = (score, prof)

            if best is None or best[0] <= 0:
                assigned[slot_idx] = (None, 0)
                continue

            score, prof = best
            assigned[slot_idx] = (prof.relic, score)
            used.add(prof.ga_handle)
            state.place_profile(prof)

        return assigned

    def _greedy_cover_once(self, candidates_per_slot: list, num_slots: int,
                           build: BuildDefinition,
                           req_masks: dict[int, int],
                           req_suffix_union: list[int],
                           req_full_mask: int,
                           req_initial_mask: int,
                           desired_cw: dict[int, int] | None = None,
                           desired_compat_effs: dict[int, set[int]] | None = None,
                           effect_limit_by_name: dict[str, int] | None = None,
                           family_limit_map: dict[str, int] | None = None,
                           ) -> list | None:
        """Greedy fill that guarantees requirement coverage when it succeeds.

        Same shape as ``_greedy_solve_once``, but each slot only considers
        options that keep the remaining slots able to cover the still-missing
        requirements (suffix-union feasibility).  When the empty slot would
        break feasibility, the best feasible candidate is placed even at a
        negative context score — a required entry can be mandatory at a net
        loss.  Returns ``None`` when some slot has no feasible option (e.g.
        two requirements competing for the last slot that can hold either):
        the suffix-union check is necessary but not sufficient, so this can
        happen even on a "feasible-looking" vessel.
        """
        assigned: list = [None] * num_slots
        used: set[int] = set()
        curse_max = build.curse_max
        state = VesselState(
            self.data_source,
            desired_conflict_weights=desired_cw,
            desired_compat_effects=desired_compat_effs,
            effect_limit_by_name=effect_limit_by_name,
            family_limit_map=family_limit_map,
            character=build.character,
        )
        mask = req_initial_mask

        for slot_idx in range(num_slots):
            rest = req_suffix_union[slot_idx + 1]
            empty_ok = (mask | rest) == req_full_mask
            best: tuple | None = None  # (score, new_bits, prof)
            for _, prof in candidates_per_slot[slot_idx]:
                if prof.ga_handle in used:
                    continue
                pmask = req_masks.get(prof.ga_handle, 0)
                if (mask | pmask | rest) != req_full_mask:
                    continue
                score = score_profile(prof, state, curse_max)
                new_bits = (pmask & ~mask).bit_count()
                if best is None or (score, new_bits) > (best[0], best[1]):
                    best = (score, new_bits, prof)

            if best is None:
                if not empty_ok:
                    return None  # no single candidate keeps this slot feasible
                assigned[slot_idx] = (None, 0)
                continue
            score, _new_bits, prof = best
            if empty_ok and score <= 0:
                assigned[slot_idx] = (None, 0)
                continue
            assigned[slot_idx] = (prof.relic, score)
            used.add(prof.ga_handle)
            state.place_profile(prof)
            mask |= req_masks.get(prof.ga_handle, 0)

        return assigned if mask == req_full_mask else None

    def _backtrack_solve(self, candidates_per_slot: list, num_slots: int,
                         build: BuildDefinition, top_n: int = 3,
                         desired_cw: dict[int, int] | None = None,
                         desired_compat_effs: dict[int, set[int]] | None = None,
                         initial_threshold: int = -1,
                         effect_limit_by_name: dict[str, int] | None = None,
                         family_limit_map: dict[str, int] | None = None,
                         deadline_secs: float = DEFAULT_BACKTRACK_DEADLINE_SECS,
                         ctx_helpers: dict[int, tuple[frozenset[int], frozenset[int]]] | None = None,
                         validate_leaves: bool = False,
                         req_masks: dict[int, int] | None = None,
                         req_suffix_union: list[int] | None = None,
                         req_full_mask: int = 0,
                         req_initial_mask: int = 0,
                         ) -> tuple[list[list], bool, int]:
        top: list[tuple[int, list]] = []
        seen: set[frozenset] = set()
        min_threshold = initial_threshold
        deadline = time.time() + deadline_secs
        truncated = False
        nodes = 0
        # Requirement hard-constraint mode: when req_masks is set, every
        # returned assignment covers req_full_mask.  The entry check prunes
        # branches whose remaining slots can't cover the missing requirements;
        # req_suffix_union[num_slots] == 0 makes it double as leaf rejection.
        constrained = req_masks is not None

        # Admissible remaining-score bound: best POSITIVE pre-score per slot,
        # as suffix sums.  (Candidates are net-ordered, so the per-slot max
        # must be computed explicitly rather than taken from the list head.)
        max_pos_per_slot = [
            max((p for p, _ in c), default=0) for c in candidates_per_slot
        ]
        suffix_pos = [0] * (num_slots + 1)
        for s in range(num_slots - 1, -1, -1):
            suffix_pos[s] = suffix_pos[s + 1] + max_pos_per_slot[s]

        # Per-slot suffix max of pos bounds over the net-ordered list: once
        # even the best remaining candidate can't clear the threshold, the
        # whole tail is prunable with a break instead of per-candidate
        # continues.
        suffix_max_per_slot: list[list[int]] = []
        for c in candidates_per_slot:
            smax = [0] * (len(c) + 1)
            for j in range(len(c) - 1, -1, -1):
                smax[j] = smax[j + 1] if smax[j + 1] > c[j][0] else c[j][0]
            suffix_max_per_slot.append(smax)

        curse_max = build.curse_max

        state = VesselState(
            self.data_source,
            desired_conflict_weights=desired_cw,
            desired_compat_effects=desired_compat_effs,
            effect_limit_by_name=effect_limit_by_name,
            family_limit_map=family_limit_map,
            character=build.character,
        )

        def backtrack(slot_idx: int, current: list, used: set[int],
                      score: int, req_mask: int) -> None:
            nonlocal min_threshold, truncated, nodes
            nodes += 1
            # Sample the clock on the first node and every 1024 thereafter,
            # rather than every node — the per-node call was ~2% of solve time
            # on its own.  The first-node check keeps an already-expired
            # deadline truncating immediately (callers pass one deliberately).
            # `truncated` is still checked every node so the unwind stays
            # prompt once tripped.
            if not ((nodes - 1) & 1023) and time.time() > deadline:
                truncated = True
            if truncated:
                return

            if constrained and (
                    req_mask | req_suffix_union[slot_idx]) != req_full_mask:
                return  # remaining slots can't cover the missing requirements

            if slot_idx == num_slots:
                if score > min_threshold or len(top) < top_n:
                    key = frozenset(used)
                    if key not in seen:
                        # Reject layouts that orphan or priority-block a
                        # desired excluded-category effect — keeping them out
                        # of `top` stops invalid layouts from inflating the
                        # pruning threshold past valid optima.
                        if validate_leaves and self.scorer.has_orphaned_excl_category_effects(
                            [set(r.all_effects) if r is not None else set()
                             for r, _ in current],
                            build, desired_compat_effs,
                        ):
                            return
                        seen.add(key)
                        top.append((score, list(current)))
                        top.sort(key=lambda x: x[0], reverse=True)
                        if len(top) > top_n:
                            removed_key = frozenset(
                                r.ga_handle for r, _ in top.pop()[1] if r is not None)
                            seen.discard(removed_key)
                        # While top isn't full, fall back to -1 — but never
                        # above initial_threshold: an unseeded constrained
                        # search may have a NEGATIVE covering optimum, and a
                        # -1 threshold would prune the path to it.
                        min_threshold = (top[-1][0] if len(top) == top_n
                                         else min(-1, initial_threshold))
                return

            # Try candidates first (sorted by net pre-score desc) so that
            # high-scoring solutions are found early, establishing
            # aggressive min_threshold for pruning.
            remaining_max = suffix_pos[slot_idx + 1]
            cands = candidates_per_slot[slot_idx]
            smax = suffix_max_per_slot[slot_idx]
            for ci in range(len(cands)):
                if score + smax[ci] + remaining_max <= min_threshold:
                    break  # no remaining candidate's pos bound can clear it
                pos_bound, prof = cands[ci]
                if prof.ga_handle in used:
                    continue
                if score + pos_bound + remaining_max <= min_threshold:
                    continue  # admissible upper-bound prune

                ctx_score = score_profile(prof, state, curse_max)
                if ctx_score <= 0:
                    # An empty slot is at least as good UNLESS this relic
                    # provides a still-missing requirement (it can be
                    # mandatory at a net loss), or can still raise later
                    # relics' scores: by placing a desired excluded-category
                    # effect (flips later competitors from -penalty to 0), or
                    # by pre-paying a shared negative effect whose later copy
                    # dedups to 0.
                    if not (constrained
                            and req_masks.get(prof.ga_handle, 0) & ~req_mask):
                        helper = ctx_helpers.get(prof.ga_handle) if ctx_helpers else None
                        if helper is None:
                            continue
                        unlocks, dedups = helper
                        if (unlocks <= state.desired_compat_placed
                                and dedups <= state.effect_ids):
                            continue
                if score + ctx_score + remaining_max <= min_threshold:
                    continue  # actual-score prune

                current[slot_idx] = (prof.relic, ctx_score)
                used.add(prof.ga_handle)
                delta = state.place_profile(prof)

                backtrack(slot_idx + 1, current, used, score + ctx_score,
                          (req_mask | req_masks.get(prof.ga_handle, 0))
                          if constrained else 0)

                used.discard(prof.ga_handle)
                state.remove(delta)

            # Try empty slot last (score 0 — worst case)
            current[slot_idx] = (None, 0)
            backtrack(slot_idx + 1, current, used, score, req_mask)

        backtrack(0, [(None, 0)] * num_slots, set(), 0, req_initial_mask)
        valid = [(s, a) for s, a in top if any(r is not None for r, _ in a)]
        return [assignment for _, assignment in valid], truncated, nodes

    def _ctx_helper_map(
        self,
        candidates_per_slot: list,
        build: BuildDefinition,
        desired_compat_effs: dict[int, set[int]] | None,
    ) -> dict[int, tuple[frozenset[int], frozenset[int]]] | None:
        """Per-relic reasons a ctx<=0 placement may still pay off later.

        Maps ga_handle -> (unlock_compats, shared_negative_keys):
        - unlock_compats: excluded-category compat IDs whose DESIRED effect
          the relic carries (placing it flips later same-category
          competitors from -penalty to 0).
        - shared_negative_keys: canonical IDs (text_id when present) of
          negatively-weighted no_stack/unique effects that at least one
          other candidate also carries — a later copy dedups to 0 against
          this one, so paying the penalty once can be globally optimal.

        Relics with neither are omitted; returns None when the map is empty
        (the common case for all-positive builds, letting the solver skip
        the lookup entirely).
        """
        ds = self.data_source
        # Binds the scorer's per-build caches so _is_inert below is valid.
        self.scorer._ensure_build_cache(build)
        is_inert = self.scorer._is_inert
        dce = desired_compat_effs or {}
        unlocks_map: dict[int, frozenset[int]] = {}
        per_relic_neg: dict[int, set[int]] = {}
        neg_counts: dict[int, int] = {}

        seen: set[int] = set()
        for cands in candidates_per_slot:
            for _pos, prof in cands:
                r = prof.relic
                if r.ga_handle in seen:
                    continue
                seen.add(r.ga_handle)

                if dce:
                    unlocks: set[int] = set()
                    for eff in r.all_effects:
                        if is_inert(eff):
                            continue  # mirrors VesselState.place
                        compat = ds.get_effect_conflict_id(eff)
                        if compat == -1 or compat not in dce:
                            continue
                        dset = dce[compat]
                        if eff in dset:
                            unlocks.add(compat)
                            continue
                        text_id = ds.get_effect_text_id(eff)
                        if text_id != -1 and text_id in dset:
                            unlocks.add(compat)
                    if unlocks:
                        unlocks_map[r.ga_handle] = frozenset(unlocks)

                keys: set[int] = set()
                for eff, is_curse in (
                    [(e, False) for e in r.effects]
                    + [(c, True) for c in r.curses]
                ):
                    if eff in (EMPTY_EFFECT, 0) or is_inert(eff):
                        continue
                    cat, weight = self.scorer._resolve_category_and_weight(eff, build)
                    negative = (
                        (cat is not None and cat != "excluded" and weight < 0)
                        or (cat is None and is_curse
                            and build.default_curse_weight < 0)
                    )
                    if not negative:
                        continue
                    if ds.get_effect_stacking_type(eff) == "stack":
                        continue  # stacking effects never dedup
                    text_id = ds.get_effect_text_id(eff)
                    keys.add(text_id if text_id != -1 else eff)
                if keys:
                    per_relic_neg[r.ga_handle] = keys
                    for k in keys:
                        neg_counts[k] = neg_counts.get(k, 0) + 1

        result: dict[int, tuple[frozenset[int], frozenset[int]]] = {}
        for handle in seen:
            unlocks = unlocks_map.get(handle, frozenset())
            shared = frozenset(
                k for k in per_relic_neg.get(handle, ())
                if neg_counts.get(k, 0) >= 2
            )
            if unlocks or shared:
                result[handle] = (unlocks, shared)
        return result or None

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
            ga_handle->OwnedRelic and slot_owner[i] is the ga_handle assigned
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
          1. Mark the base redundant (score -> 0).
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
                cat = entry.get("category")
                if cat is None or cat == "excluded":
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
                _, weight = self.scorer._resolve_category_and_weight(eff_id, build)
                if weight <= 0:
                    continue
                entry["score"] = weight
                entry["redundant"] = False
                entry["override_status"] = None
                slot_results[slot_i] = (relic, slot_score + weight, breakdown)
                total_score += weight

        return total_score
