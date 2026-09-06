"""Vessel slot optimizer — backtrack (exhaustive) + greedy solvers."""
from __future__ import annotations

import logging
import time
from concurrent.futures import Executor, Future, as_completed
from dataclasses import dataclass

from nrplanner.data import SourceDataHandler
from nrplanner.models import (
    BuildDefinition, OwnedRelic, RelicInventory,
    SlotAssignment, VesselResult, VesselState,
)
from nrplanner import solver_bridge
from nrplanner.scoring import BuildScorer

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

@dataclass(slots=True)
class BuildSolveContext:
    """Everything the vessels of ONE build share, prepared once up front.

    The process pool pickled the whole inventory into every vessel task
    (10-12x per build), rebuilt a RelicInventory in the worker and recompiled
    every relic profile from scratch.  With the solver in Rust that overhead
    dominates, so vessels now run on threads and share this context by
    reference instead.

    Every lazily-built cache the vessels read — the scorer's per-build memos
    and the compiled Rust inventory — is warmed here, on the submitting
    thread, so the worker threads only ever read.  The scorer is per context,
    so concurrent builds never share one.
    """
    build: BuildDefinition
    inventory: RelicInventory
    optimizer: "VesselOptimizer"


def warm_data_source(ds: SourceDataHandler) -> None:
    """Force the data source's lazy caches before any task can race on them.

    ``SourceDataHandler``'s caches are built non-atomically, so they must be
    populated while only one thread is running — at pool init, exactly as the
    process pool's worker initializer did per worker.
    """
    _ = ds._reachable_effect_ids  # noqa: F841  # cached_property
    ds.get_effect_stacking_type(0)  # loads _stacking_cache
    ds.get_effect_family(0)  # loads _effect_families


def make_solve_context(data_source: SourceDataHandler,
                       build: BuildDefinition,
                       inventory: RelicInventory) -> BuildSolveContext:
    """Prepare one build's shared solve context (see BuildSolveContext)."""
    scorer = BuildScorer(data_source)
    optimizer = VesselOptimizer(data_source, scorer)
    scorer.bind_inventory(inventory)
    scorer._ensure_build_cache(build)
    scorer.get_desired_conflict_weights(build)
    scorer.get_desired_compat_effects(build)
    elbn, flm = optimizer._prepare_limits(build)
    req_specs = optimizer._requirement_specs(build)
    # Compile the inventory once per build instead of once per vessel task.
    solver_bridge.get_bundle(optimizer, build, inventory, elbn, flm, req_specs)
    return BuildSolveContext(
        build=build, inventory=inventory, optimizer=optimizer)


def _optimize_vessel_task_ctx(
    ctx: BuildSolveContext,
    vessel_data: dict,
    max_per_vessel: int,
    deadline_secs: float = DEFAULT_BACKTRACK_DEADLINE_SECS,
) -> tuple[int, str, list[VesselResult], float]:
    """One vessel, sharing its build's context by reference (thread pool)."""
    t0 = time.perf_counter()
    results = ctx.optimizer.optimize(
        ctx.build, ctx.inventory, vessel_data, max_per_vessel,
        deadline_secs=deadline_secs)
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
                "nodes": 0, "truncated": False, "solve_ms": 0.0,
                "candidates": [], "skipped": True,
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
        t_solve = time.perf_counter()
        raw_free, search_truncated, nodes_expanded, cand_counts =             self._solve_free_slots(
                build, inventory, slot_colors, free_slot_indices,
                pinned_handles, excluded_handles, req_specs, pinned_mask,
                full_mask, top_n, deadline_secs, desired_cw,
                desired_compat_effs, effect_limit_by_name, family_limit_map)
        self.last_solve_stats = {
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

    def _solve_free_slots(
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
        """Candidate selection, then the Rust solver.

        The seam between "which relics may go in each free slot" — game data
        and Pydantic, so it stays here — and the closed-form search.
        Everything build-dependent is compiled once per (build, inventory)
        into a CoreBundle; this call only picks each free slot's candidates
        out of it and hands the index lists across.  ``desired_cw`` is unused:
        conflict penalties are already baked into the compiled profiles.
        """
        bundle = solver_bridge.get_bundle(
            self, build, inventory, effect_limit_by_name, family_limit_map,
            req_specs)
        cand_lists = [
            solver_bridge.slot_candidates(
                bundle, slot_colors[i], i >= 3, pinned_handles,
                excluded_handles)
            for i in free_slot_indices
        ]
        # Leaf-level excluded-category validation needs absolute slot order;
        # with pinned slots the free-slot indices no longer align, so the
        # post-hoc filter alone handles those (rare) builds.
        validate_leaves = bool(desired_compat_effs) and not pinned_handles
        raw_free, truncated, nodes = solver_bridge.solve_free_slots(
            bundle, cand_lists, top_n, build.curse_max, deadline_secs,
            validate_leaves, full_mask, pinned_mask)
        return raw_free, truncated, nodes, [len(c) for c in cand_lists]

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
        executor: Executor | None = None,
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
        ctx = make_solve_context(self.data_source, build, inventory)
        futures: dict[Future, dict] = {}
        for v in vessels:
            vd = dict(v)
            vd["_id"] = v["vessel_id"]
            fut = executor.submit(
                _optimize_vessel_task_ctx, ctx, vd, max_per_vessel,
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
        executor: Executor | None = None,
        deadline_secs: float = DEFAULT_BACKTRACK_DEADLINE_SECS,
        presubmitted: dict[Future, dict] | None = None,
        vessel_ids: set[int] | None = None,
        carried: list[VesselResult] | None = None,
    ):
        """Like optimize_all_vessels but yields events for SSE streaming.

        Yields dicts:
            {"type": "progress", "vessel": i, "total": n, "name": vessel_name}
            {"type": "result", "data": list[VesselResult]}   (final event)

        When *executor* is provided, vessels are optimized in parallel on
        the pool.  Progress events arrive as each vessel completes
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
                             executor: Executor | None = None,
                             deadline_secs: float = DEFAULT_BACKTRACK_DEADLINE_SECS,
                             ) -> list[VesselResult]:
        """Optimize all vessels for a hero. Returns top_n globally ranked results.

        Results that meet requirements come before those that don't, then sorted
        by score descending.

        When *executor* is provided, vessels are optimized in parallel on
        the pool.
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
