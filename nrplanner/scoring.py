"""Relic scoring with stacking awareness."""
from __future__ import annotations

from nrplanner.constants import EMPTY_EFFECT
from nrplanner.data import SourceDataHandler
from nrplanner.models import (
    BuildDefinition, OwnedRelic, RelicInventory, VesselState,
    CURSE_EXCESS_PENALTY, REQUIRED_WEIGHT,
)

# Dynamic-effect kinds for compiled relic profiles (see compile_profile).
K_STACK, K_UNIQUE, K_NO_STACK, K_EXCL_CAT = 0, 1, 2, 3

_STYPE_CODE = {"stack": K_STACK, "unique": K_UNIQUE, "no_stack": K_NO_STACK}


class RelicProfile:
    """Per-(build, relic) compiled scoring data.

    Splits a relic's score into a state-independent ``static_score`` plus a
    short ``dyn`` tuple of effects whose contribution depends on vessel state,
    and precomputes the id sets a placement contributes — so the search needs
    no data-source or Pydantic access at all.  This is what
    ``solver_bridge`` marshals into the Rust solver's compiled inventory.

    Compiled once per (build, relic) and memoized on the scorer;
    test_profile_equivalence pins the whole chain to the legacy
    ``score_relic_in_context`` / ``place``.
    """
    __slots__ = ("relic", "ga_handle", "static_score", "dyn", "curse_ids",
                 "penalized_curse_ids", "limit_keys", "pos_bound",
                 "net",
                 # Pre-unioned placement sets — one per VesselState
                 # collection, in the order a placement applies them.
                 "eff_set", "excl_set", "ns_excl_set", "ns_compat_set",
                 "dcp_set")

    def __init__(self, relic: OwnedRelic, static_score: int, dyn: tuple,
                 curse_ids: tuple, limit_keys: tuple,
                 pos_bound: int, net: int,
                 penalized_curse_ids: tuple | None = None,
                 eff_set: frozenset = frozenset(),
                 excl_set: frozenset = frozenset(),
                 ns_excl_set: frozenset = frozenset(),
                 ns_compat_set: frozenset = frozenset(),
                 dcp_set: frozenset = frozenset()):
        self.relic = relic
        self.ga_handle = relic.ga_handle
        self.static_score = static_score
        self.dyn = dyn
        self.curse_ids = curse_ids
        # Curses subject to the build-wide curse_max excess check. Excludes
        # curses with an explicit user limit at negative weight — for those the
        # limit IS the tolerance (see the sign fork in the solver's
        # score_profile) and it replaces curse_max.  A placement still counts
        # ALL curse_ids.
        self.penalized_curse_ids = (
            curse_ids if penalized_curse_ids is None else penalized_curse_ids)
        self.eff_set = eff_set
        self.excl_set = excl_set
        self.ns_excl_set = ns_excl_set
        self.ns_compat_set = ns_compat_set
        self.dcp_set = dcp_set
        self.limit_keys = limit_keys
        self.pos_bound = pos_bound
        self.net = net


class BuildScorer:
    """Scores relics against a BuildDefinition with effect-stacking awareness."""

    def __init__(self, data_source: SourceDataHandler):
        self.data_source = data_source
        # Per-build memo caches, bound/reset by _ensure_build_cache
        self._cached_build: BuildDefinition | None = None
        self._cached_sig: tuple | None = None
        self._name_cache: dict[str, tuple[str, int]] = {}
        self._resolve_memo: dict[int, tuple[str | None, int]] = {}
        self._protected_memo: dict[int, bool] = {}
        self._profile_memo: dict[int, RelicProfile] = {}
        # Compiled Rust-side inventory (nrplanner.solver_bridge.CoreBundle).
        # Derived from _profile_memo, so it shares its invalidation exactly.
        self._core_bundle = None
        # The inventory _profile_memo's ga_handle keys refer to (bind_inventory).
        self._memo_inventory: RelicInventory | None = None
        self._character: str | None = None
        self._inert_memo: dict[int, bool] = {}
        self._excl_cats: frozenset[int] = frozenset()
        self._excl_names: set[str] = set()
        self._desired_cw: dict[int, int] | None = None
        self._desired_compat: dict[int, set[int]] | None = None

    # ------------------------------------------------------------------
    # Per-build cache management
    # ------------------------------------------------------------------

    @staticmethod
    def _scoring_sig(build: BuildDefinition) -> tuple:
        """Structural signature of every field scoring resolution reads."""
        return (
            # Character gates which effects are inert (see _is_inert), so every
            # memo below depends on it — omitting it would leak a Raider's
            # resolution cache into an Executor build.
            build.character,
            tuple(build.required_effects),
            tuple(build.required_families),
            tuple(build.excluded_effects),
            tuple(build.excluded_families),
            tuple((g.weight, tuple(g.effects), tuple(g.families))
                  for g in build.groups),
            tuple(build.excluded_stacking_categories),
            tuple(sorted(build.effect_limits.items())),
            tuple(sorted(build.family_limits.items())),
            tuple(sorted(build.family_weight_floors.items())),
            build.default_curse_weight,
            build.curse_max,
        )

    def _ensure_build_cache(self, build: BuildDefinition) -> None:
        """Bind the scorer's memo caches to *build*.

        Identity fast path (`is`) for the per-node hot loop; when identity
        changes, an equal structural signature rebinds without invalidating
        (worker processes unpickle a fresh-but-identical build per task).
        Holding a strong reference to the bound build means an identity hit
        can never be a recycled address.  Assumes builds are not mutated in
        place mid-optimization (API/UI flows always construct fresh
        BuildDefinitions).
        """
        if build is self._cached_build:
            return
        sig = self._scoring_sig(build)
        if sig == self._cached_sig:
            self._cached_build = build
            return
        self._cached_build = build
        self._cached_sig = sig
        self._resolve_memo = {}
        self._protected_memo = {}
        self._profile_memo = {}
        self._core_bundle = None
        self._memo_inventory = None
        self._character = build.character
        self._inert_memo = {}
        self._excl_cats = frozenset(build.excluded_stacking_categories)
        self._excl_names = {
            name for name in (
                self.data_source.get_effect_name(eid)
                for eid in build.excluded_effects
            )
            if name not in ("", "Empty", None)
        }
        self._desired_cw = None
        self._desired_compat = None

        cache: dict[str, tuple[str, int]] = {}
        for eid in build.required_effects:
            name = self.data_source.get_effect_name(eid)
            if name and name != "Empty" and not name.startswith("Effect "):
                cache.setdefault(name, ("required", REQUIRED_WEIGHT))
        for eid in build.excluded_effects:
            name = self.data_source.get_effect_name(eid)
            if name and name != "Empty" and not name.startswith("Effect "):
                cache.setdefault(name, ("excluded", 0))
        for g in build.groups:
            for eid in g.effects:
                name = self.data_source.get_effect_name(eid)
                if name and name != "Empty" and not name.startswith("Effect "):
                    cache.setdefault(name, ("group", g.weight))
        self._name_cache = cache

    # ------------------------------------------------------------------
    # Category / weight resolution
    # ------------------------------------------------------------------

    def _get_name_cache(self, build: BuildDefinition) -> dict[str, tuple[str, int]]:
        """display_name -> (category, weight) cache for name-based effect matching.

        Required for alias resolution: many game effects share the same
        display name but have completely different IDs and text_ids.
        """
        self._ensure_build_cache(build)
        return self._name_cache

    def _is_inert(self, eff_id: int) -> bool:
        """True if this effect does nothing for the bound build's character.

        The game greys out effects that the active Nightfarer cannot use —
        either character-exclusive effects belonging to someone else, or
        armament-skill swaps whose ash of war is incompatible with that
        character's starting weapon (Seppuku on colossal-wielding Raider, the
        sorcery variants without Recluse's staff, the incantation variants
        without Revenant's seal).  Such an effect is treated as ABSENT
        everywhere in scoring: it earns nothing, costs nothing, and — the
        point of this rule — never blocks a usable effect in the same
        stacking category.

        Callers must have run ``_ensure_build_cache`` first.
        """
        hit = self._inert_memo.get(eff_id)
        if hit is None:
            hit = not self.data_source.is_effect_usable_by(eff_id, self._character)
            self._inert_memo[eff_id] = hit
        return hit

    def _resolve_category_and_weight(self, eff_id: int,
                                      build: BuildDefinition) -> tuple[str | None, int]:
        """Return (category, weight) for an effect.

        Category is "required", "excluded", "group", or None (unassigned).
        Falls back through: direct ID -> text_id -> name -> family.
        Memoized per build — resolution is pure given (build, eff_id), and
        the solver asks for the same few hundred effects millions of times.

        Name-based matching is required for alias resolution — many game
        effects share a display name but have different IDs and text_ids.
        Family matching (via ``g.families``) adds magnitude weighting.
        """
        self._ensure_build_cache(build)
        hit = self._resolve_memo.get(eff_id)
        if hit is not None:
            return hit
        if self._is_inert(eff_id):
            # Weighted or not, it cannot fire for this character.
            self._resolve_memo[eff_id] = (None, 0)
            return (None, 0)

        result = build.get_weight_for_effect(eff_id)
        if not result:
            text_id = self.data_source.get_effect_text_id(eff_id)
            if text_id != -1 and text_id != eff_id:
                result = build.get_weight_for_effect(text_id)
        if not result:
            name = self.data_source.get_effect_name(eff_id)
            result = self._name_cache.get(name)
        if not result:
            family = self.data_source.get_effect_family(eff_id)
            if family:
                fresult = build.get_weight_for_family(family)
                if fresult:
                    cat, base_w = fresult
                    if cat == "excluded":
                        result = (cat, 0)
                    else:
                        # All families get magnitude weighting (positive and negative);
                        # an optional per-family floor lifts the weakest tiers.
                        floor = build.family_weight_floors.get(family, 0)
                        result = (cat, self.data_source.get_family_magnitude_weight(
                            eff_id, base_w, floor))
        if not result:
            result = (None, 0)
        self._resolve_memo[eff_id] = result
        return result

    # ------------------------------------------------------------------
    # Exclusion check
    # ------------------------------------------------------------------

    def _is_effect_protected(self, eff_id: int, build: BuildDefinition) -> bool:
        """True if effect is explicitly in required/weight groups (overrides category exclusion).

        Memoized per build (pure given build and eff_id).
        """
        self._ensure_build_cache(build)
        hit = self._protected_memo.get(eff_id)
        if hit is not None:
            return hit
        value = self._compute_protected(eff_id, build)
        self._protected_memo[eff_id] = value
        return value

    def _compute_protected(self, eff_id: int, build: BuildDefinition) -> bool:
        if self._is_inert(eff_id):
            return False  # cannot be a desired exception it can never fire
        result = build.get_weight_for_effect(eff_id)
        if result and result[0] != "excluded":
            return True
        # text_id fallback
        text_id = self.data_source.get_effect_text_id(eff_id)
        if text_id != -1 and text_id != eff_id:
            result = build.get_weight_for_effect(text_id)
            if result and result[0] != "excluded":
                return True
        # name fallback
        name = self.data_source.get_effect_name(eff_id)
        if name:
            result = self._name_cache.get(name)
            if result and result[0] != "excluded":
                return True
        # family fallback
        family = self.data_source.get_effect_family(eff_id)
        if family:
            fresult = build.get_weight_for_family(family)
            if fresult and fresult[0] != "excluded":
                return True
        return False

    def has_excluded_effect(self, relic: OwnedRelic, build: BuildDefinition,
                            desired_compat_effects: dict[int, set[int]] | None = None,
                            ) -> bool:
        """True if the relic contains any excluded effect, family, or stacking category.

        For stacking categories where a desired effect exists (in
        ``desired_compat_effects``), the relic is NOT pre-filtered — positional
        scoring in ``score_relic_in_context`` handles it instead.  Categories
        with no desired effect still trigger hard exclusion.

        Even when let through here, results are post-validated by
        ``has_orphaned_excl_category_effects`` to ensure no undesired
        excluded-category effect appears without its desired counterpart.

        Effects the build's character cannot use are skipped outright: the
        game greys them out, so they exclude nothing (see ``_is_inert``).
        """
        excl_ids = set(build.excluded_effects)
        excl_families = build.excluded_families
        excl_categories = set(build.excluded_stacking_categories)
        if not excl_ids and not excl_families and not excl_categories:
            return False
        self._ensure_build_cache(build)
        excl_names = self._excl_names
        _desired = desired_compat_effects or {}
        for eff in relic.all_effects:
            if self._is_inert(eff):
                continue  # greyed out for this character — nothing to exclude
            if eff in excl_ids:
                return True
            text_id = self.data_source.get_effect_text_id(eff)
            if text_id != -1 and text_id != eff and text_id in excl_ids:
                return True
            if excl_names and self.data_source.get_effect_name(eff) in excl_names:
                return True
            if excl_families:
                family = self.data_source.get_effect_family(eff)
                if family and family in excl_families:
                    return True
            # Stacking category exclusion — only hard-exclude when
            # no desired effect exists for the category.
            if excl_categories:
                compat = self.data_source.get_effect_conflict_id(eff)
                if compat != -1 and compat in excl_categories:
                    if not self._is_effect_protected(eff, build):
                        if compat not in _desired:
                            return True
                        # Desired effect exists → let positional scoring handle it;
                        # post-hoc validation catches orphaned results.
        return False

    # ------------------------------------------------------------------
    # Excluded stacking category helpers
    # ------------------------------------------------------------------

    def _all_build_effect_ids(self, build: BuildDefinition) -> set[int]:
        """All effect IDs referenced by the build (direct + family-expanded)."""
        result: set[int] = set()
        result.update(build.required_effects)
        for g in build.groups:
            result.update(g.effects)
        for family in build.required_families:
            result.update(self.data_source.get_family_effect_ids(family))
        for g in build.groups:
            for family in g.families:
                result.update(self.data_source.get_family_effect_ids(family))
        return result

    def get_desired_compat_effects(self, build: BuildDefinition) -> dict[int, set[int]]:
        """Map compatibilityId -> set of protected effect IDs in that category.

        Only populated for categories in build.excluded_stacking_categories.
        Used to determine if a competing effect would block a desired one.
        Cached per build (pure function of the build).
        """
        self._ensure_build_cache(build)
        if self._desired_compat is not None:
            return self._desired_compat
        result: dict[int, set[int]] = {}
        excl_cats = self._excl_cats
        if excl_cats:
            for eff_id in self._all_build_effect_ids(build):
                compat = self.data_source.get_effect_conflict_id(eff_id)
                if compat != -1 and compat in excl_cats:
                    if self._is_effect_protected(eff_id, build):
                        result.setdefault(compat, set()).add(eff_id)
        self._desired_compat = result
        return result

    def has_orphaned_excl_category_effects(
        self,
        placed_effects_per_slot: list[set[int]],
        build: BuildDefinition,
        desired_compat_effects: dict[int, set[int]] | None = None,
    ) -> bool:
        """True if undesired excluded-category effects compromise the desired one.

        Two failure modes (both rejected):
        1. **Orphaned**: the category has a desired effect and at least one
           undesired effect is placed, but the desired effect is absent.
        2. **Priority-blocked**: the desired effect is placed, but an undesired
           effect in the same compat sits in a slot to its **left**. In-game,
           the leftmost effect in the compat wins, so the undesired one
           activates and the desired one becomes dead weight.

        ``placed_effects_per_slot`` is the per-slot effect set in slot order
        (leftmost first). Callers should include both effects and curses in
        each slot's set, plus text-id aliases of effects when relevant.
        """
        dce = desired_compat_effects or {}
        if not dce:
            return False
        self._ensure_build_cache(build)
        excl_cats = set(build.excluded_stacking_categories)
        for compat, desired_ids in dce.items():
            if compat not in excl_cats:
                continue
            # Expand desired IDs to include text_id aliases
            desired_expanded: set[int] = set(desired_ids)
            for d in desired_ids:
                text_id = self.data_source.get_effect_text_id(d)
                if text_id != -1:
                    desired_expanded.add(text_id)

            leftmost_desired = None
            leftmost_undesired = None
            for slot_idx, slot_effects in enumerate(placed_effects_per_slot):
                if leftmost_desired is None and (slot_effects & desired_expanded):
                    leftmost_desired = slot_idx
                if leftmost_undesired is None:
                    for eff in slot_effects:
                        if eff in desired_expanded:
                            continue
                        if self._is_inert(eff):
                            continue  # greyed out — never wins the compat
                        eff_compat = self.data_source.get_effect_conflict_id(eff)
                        if eff_compat == compat:
                            leftmost_undesired = slot_idx
                            break
                if leftmost_desired is not None and leftmost_undesired is not None:
                    break

            if leftmost_undesired is None:
                continue  # no undesired competitor — OK
            if leftmost_desired is None:
                return True  # orphaned: undesired present, desired absent
            if leftmost_undesired < leftmost_desired:
                return True  # priority-blocked: undesired wins in-game
        return False

    def _excluded_category_score(
        self, eff_id: int, base_weight: int,
        build: BuildDefinition,
        state: VesselState,
    ) -> tuple[int, str | None]:
        """Score for a non-protected effect in an excluded stacking category.

        Returns (score, override_status):
        - Desired effect already placed to the left: (0, "excl_category_nullified")
        - No desired effect in category: (0, "excl_category_nullified")
        - Desired effect NOT yet placed (would block it): (-penalty, "excl_category_blocking")
        """
        dce = state.desired_compat_effects or {}
        compat = self.data_source.get_effect_conflict_id(eff_id)
        desired_in_cat = dce.get(compat)

        if not desired_in_cat:
            return 0, "excl_category_nullified"

        if compat in state.desired_compat_placed:
            # Desired effect is already to the LEFT — this is harmless dead weight
            return 0, "excl_category_nullified"

        # Desired effect NOT yet placed — this would block it from the LEFT
        max_desired_weight = 0
        for desired_eff in desired_in_cat:
            _, w = self._resolve_category_and_weight(desired_eff, build)
            max_desired_weight = max(max_desired_weight, w)
        return -max_desired_weight, "excl_category_blocking"

    def _is_excl_category_effect(self, eff_id: int,
                                  build: BuildDefinition,
                                  state: VesselState,
                                  ) -> bool:
        """True if effect is a non-protected member of an excluded stacking
        category that has desired effects (positional scoring applies)."""
        dce = state.desired_compat_effects or {}
        if not dce:
            return False
        compat = self.data_source.get_effect_conflict_id(eff_id)
        if compat == -1:
            return False
        self._ensure_build_cache(build)
        if self._is_inert(eff_id):
            return False  # greyed out — cannot block the desired effect
        if compat not in self._excl_cats:
            return False
        if compat not in dce:
            return False  # no desired effect → handled by pre-filter
        if self._is_effect_protected(eff_id, build):
            return False  # desired effect itself → scores normally
        return True

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_relic(self, relic: OwnedRelic, build: BuildDefinition) -> int:
        """Pre-score without stacking context (used for initial sort / pruning)."""
        self._ensure_build_cache(build)
        score = 0
        for eff in relic.effects:
            if eff in (EMPTY_EFFECT, 0) or self._is_inert(eff):
                continue
            cat, weight = self._resolve_category_and_weight(eff, build)
            if cat is not None and cat != "excluded":
                score += weight
        for curse in relic.curses:
            # An inert curse costs nothing — not even default_curse_weight.
            if curse in (EMPTY_EFFECT, 0) or self._is_inert(curse):
                continue
            cat, weight = self._resolve_category_and_weight(curse, build)
            if cat is not None and cat != "excluded":
                score += weight
            elif cat is None:
                score += build.default_curse_weight
        return score

    def positive_pre_score(self, relic: OwnedRelic, build: BuildDefinition) -> int:
        """Upper bound on score_relic_in_context for ANY vessel state.

        Sums only positive contributions: every effect scores at most
        max(0, resolved_weight) regardless of stacking state (stack -> w;
        unique/no_stack -> w, 0 or a negative penalty; limit reached -> 0;
        excluded-category -> 0 or a negative penalty; curse excess -> < 0).
        Unlike the net pre-score this is admissible as a pruning bound and a
        candidate filter even when negative weights are in play — a
        negatively-weighted duplicate dedups to 0 in context, so a net<=0
        relic can still belong to the optimum.
        """
        self._ensure_build_cache(build)
        score = 0
        for eff in relic.effects:
            if eff in (EMPTY_EFFECT, 0) or self._is_inert(eff):
                continue
            cat, weight = self._resolve_category_and_weight(eff, build)
            if cat is not None and cat != "excluded" and weight > 0:
                score += weight
        for curse in relic.curses:
            if curse in (EMPTY_EFFECT, 0) or self._is_inert(curse):
                continue
            cat, weight = self._resolve_category_and_weight(curse, build)
            if cat is not None and cat != "excluded":
                if weight > 0:
                    score += weight
            elif cat is None and build.default_curse_weight > 0:
                score += build.default_curse_weight
        return score

    def get_desired_conflict_weights(self, build: BuildDefinition) -> dict[int, int]:
        """Map compatibilityId -> max weight of desired effects in that group.

        Used by the optimizer to penalize relics whose no_stack effects
        block a desired effect sharing the same compatibilityId group.
        Cached per build (pure function of the build).
        """
        self._ensure_build_cache(build)
        if self._desired_cw is not None:
            return self._desired_cw
        result: dict[int, int] = {}
        for eff_id in build.required_effects:
            compat = self.data_source.get_effect_conflict_id(eff_id)
            if compat not in (-1, 100, 900):
                result[compat] = max(result.get(compat, 0), REQUIRED_WEIGHT)
        for g in build.groups:
            if g.weight <= 0:
                continue
            for eff_id in g.effects:
                compat = self.data_source.get_effect_conflict_id(eff_id)
                if compat not in (-1, 100, 900):
                    result[compat] = max(result.get(compat, 0), g.weight)
        self._desired_cw = result
        return result

    def _effect_stacking_score(self, eff_id: int, weight: int,
                                state: VesselState) -> int:
        """Weight of an effect given what's already in the vessel (0 if redundant).

        Uses ``exclusivityId`` for mutual-exclusion between effects that truly
        override each other (e.g. weapon imbues, ash-of-war swaps).  Effects
        with ``exclusivityId == -1`` fall back to identity-based conflict
        detection (same ``text_id`` or ``eff_id``).

        Tier-family stacking (e.g. "HP Restoration +0/+1/+2") is handled via
        ``compatibilityId``: when a ``no_stack`` base is placed, its compat ID
        enters ``vessel_no_stack_compat_ids``, blocking ``unique`` tier variants
        that share the same compat group.

        When ``desired_conflict_weights`` is provided and a stacking conflict
        blocks a desired effect, a negative penalty equal to the desired
        effect's weight is returned instead of 0.
        """
        stype    = self.data_source.get_effect_stacking_type(eff_id)
        excl     = self.data_source.get_effect_exclusivity_id(eff_id)
        text_id  = self.data_source.get_effect_text_id(eff_id)

        if stype == "stack":
            return weight
        if stype == "unique":
            if eff_id in state.effect_ids:
                return 0
            if text_id != -1 and text_id in state.effect_ids:
                return 0
            if excl != -1 and excl in state.no_stack_exclusivity_ids:
                return self._conflict_penalty(eff_id, state.desired_conflict_weights)
            # Tier-family cross-check: unique variants blocked by no_stack base
            compat = self.data_source.get_effect_conflict_id(eff_id)
            if compat != -1 and compat in state.no_stack_compat_ids:
                return self._conflict_penalty(eff_id, state.desired_conflict_weights)
            return weight
        # no_stack
        if excl != -1 and excl in state.exclusivity_ids:
            return self._conflict_penalty(eff_id, state.desired_conflict_weights)
        if text_id != -1 and text_id in state.effect_ids:
            return 0
        if eff_id in state.effect_ids:
            return 0
        return weight

    def _conflict_penalty(self, eff_id: int,
                          desired_conflict_weights: dict[int, int] | None) -> int:
        """Return negative penalty if blocking a desired compat group, else 0."""
        if not desired_conflict_weights:
            return 0
        compat = self.data_source.get_effect_conflict_id(eff_id)
        if compat != -1 and compat in desired_conflict_weights:
            return -desired_conflict_weights[compat]
        return 0

    def _is_at_limit(self, eff_id: int, state: VesselState) -> bool:
        """True if this effect's name or family has reached its user-defined limit."""
        if not state.limited_names:
            return False
        eff_name = self.data_source.get_effect_name(eff_id)
        if eff_name and eff_name in state.effect_limit_by_name:
            if state.limited_counts.get(eff_name, 0) >= state.effect_limit_by_name[eff_name]:
                return True
        family = self.data_source.get_effect_family(eff_id)
        if family and family in state.family_limit_map:
            if state.limited_counts.get(family, 0) >= state.family_limit_map[family]:
                return True
        return False

    def _curse_max_exempt(self, curse_id: int, build: BuildDefinition,
                          state: VesselState) -> bool:
        """True if this curse's excess handling is owned by a user limit.

        A user limit on a negatively-weighted curse is a per-curse tolerance
        that REPLACES the build-wide curse_max for that curse (the sign fork in
        the scoring loops applies CURSE_EXCESS_PENALTY beyond the limit).
        Mirrors compile_profile's ``penalized_curse_ids`` exclusion exactly:
        excluded-category competitors keep global curse_max handling because
        they never reach the limit branch in either path.
        """
        if not state.limited_names:
            return False
        if self._is_excl_category_effect_static(
                curse_id, build, self.get_desired_compat_effects(build)):
            return False
        cat, weight = self._resolve_category_and_weight(curse_id, build)
        if cat is None or cat == "excluded" or weight >= 0:
            return False
        name = self.data_source.get_effect_name(curse_id)
        if name and name in state.effect_limit_by_name:
            return True
        family = self.data_source.get_effect_family(curse_id)
        return bool(family and family in state.family_limit_map)

    def score_relic_in_context(self, relic: OwnedRelic, build: BuildDefinition,
                                state: VesselState) -> int:
        """Score considering stacking state of already-assigned relics."""
        self._ensure_build_cache(build)
        score = 0
        for eff in relic.effects:
            if eff in (EMPTY_EFFECT, 0) or self._is_inert(eff):
                continue
            # Excluded-category positional check fires regardless of whether
            # the effect resolves to a build category. An undesired competitor
            # in the same compat (e.g. wrong-weapon Dormant Power) blocks the
            # desired effect when placed left of it; the penalty must apply
            # even though the effect itself isn't in any weight group.
            if self._is_excl_category_effect(eff, build, state):
                _, w_for_penalty = self._resolve_category_and_weight(eff, build)
                adj, _ = self._excluded_category_score(eff, w_for_penalty, build, state)
                score += adj
                continue
            cat, weight = self._resolve_category_and_weight(eff, build)
            if cat is not None and cat != "excluded":
                if self._is_at_limit(eff, state):
                    # Sign fork: cap for desired effects (extra copies are
                    # neutral), tolerance for undesired ones (excess
                    # disqualifies like excess curses).
                    if weight < 0:
                        score += CURSE_EXCESS_PENALTY
                    continue
                score += self._effect_stacking_score(eff, weight, state)
        for curse in relic.curses:
            if curse in (EMPTY_EFFECT, 0) or self._is_inert(curse):
                continue
            if self._is_excl_category_effect(curse, build, state):
                _, w_for_penalty = self._resolve_category_and_weight(curse, build)
                adj, _ = self._excluded_category_score(curse, w_for_penalty, build, state)
                score += adj
                continue
            cat, weight = self._resolve_category_and_weight(curse, build)
            if cat is not None and cat != "excluded":
                if self._is_at_limit(curse, state):
                    if weight < 0:
                        score += CURSE_EXCESS_PENALTY
                    continue
                score += self._effect_stacking_score(curse, weight, state)
            elif cat is None:
                score += build.default_curse_weight
        for curse in relic.curses:
            if curse in (EMPTY_EFFECT, 0) or self._is_inert(curse):
                continue
            if self._curse_max_exempt(curse, build, state):
                continue  # explicit per-curse tolerance replaces curse_max
            if state.curse_counts.get(curse, 0) >= build.curse_max:
                score += CURSE_EXCESS_PENALTY
        return score

    # ------------------------------------------------------------------
    # Compiled relic profiles (solver hot path)
    # ------------------------------------------------------------------

    def bind_inventory(self, inventory: RelicInventory) -> None:
        """Bind the compiled-profile memo to *inventory*, dropping a stale one.

        ``_profile_memo`` is keyed by ga_handle, which identifies a relic only
        within ONE inventory: the game renumbers every handle when it writes a
        save, and a pool worker outlives any single request, so the same scorer
        legitimately sees one handle standing for different relics.  The build
        cache cannot catch that -- two runs of the same build share a
        _scoring_sig, so nothing invalidates a profile whose handle now belongs
        to a different relic, and since a RelicProfile carries its ``relic``,
        the stale one gets placed and scored.

        Identity, not content: a different object cannot be proven to be the
        same inventory, so it clears.  Every vessel of one run is solved against
        the same RelicInventory, which keeps the memo doing its real job (a
        relic eligible for several slots, and for several vessels, compiles
        once); a worker task unpickles its own copy, so it starts clean.
        Holding a strong reference means an identity hit can never be a
        recycled address.  Assumes an inventory is not mutated in place
        mid-optimization, exactly as _ensure_build_cache assumes of builds.
        """
        if inventory is self._memo_inventory:
            return
        self._memo_inventory = inventory
        self._profile_memo = {}
        self._core_bundle = None

    def compile_profile(self, relic: OwnedRelic, build: BuildDefinition,
                        effect_limit_by_name: dict[str, int] | None = None,
                        family_limit_map: dict[str, int] | None = None,
                        ) -> RelicProfile:
        """Compile a relic's scoring/placement data for one build (memoized).

        The limit maps must be the same name-keyed maps the caller hands to
        ``VesselState`` (see ``VesselOptimizer._prepare_limits``) — they are a
        pure function of the build, which is what makes the per-build memo
        sound.  Branch selection mirrors ``score_relic_in_context`` exactly.
        """
        self._ensure_build_cache(build)
        cached = self._profile_memo.get(relic.ga_handle)
        if cached is not None:
            return cached

        ds = self.data_source
        dce = self.get_desired_compat_effects(build)
        dcw = self.get_desired_conflict_weights(build)
        elbn = effect_limit_by_name or {}
        flm = family_limit_map or {}

        static_score = 0
        dyn: list[tuple] = []
        # Curses whose excess handling is owned by a user limit (negative
        # weight + limit key) — exempt from the build-wide curse_max check.
        tolerated_curses: set[int] = set()

        for eff, is_curse in (
            [(e, False) for e in relic.effects]
            + [(c, True) for c in relic.curses]
        ):
            if eff in (EMPTY_EFFECT, 0) or self._is_inert(eff):
                continue

            if self._is_excl_category_effect_static(eff, build, dce):
                compat = ds.get_effect_conflict_id(eff)
                # max() clamped at 0 — mirrors _excluded_category_score, which
                # accumulates from 0 (a negative desired weight never deepens
                # the blocking penalty).
                blocking = -max(0, max(
                    (self._resolve_category_and_weight(d, build)[1]
                     for d in dce[compat]),
                    default=0,
                ))
                dyn.append((K_EXCL_CAT, 0, eff, -1, -1, compat, blocking,
                            None, None))
                continue

            cat, weight = self._resolve_category_and_weight(eff, build)
            if cat is not None and cat != "excluded":
                lname = lfam = None
                if elbn or flm:
                    name = ds.get_effect_name(eff)
                    if name and name in elbn:
                        lname = name
                    family = ds.get_effect_family(eff)
                    if family and family in flm:
                        lfam = family
                if is_curse and weight < 0 and (lname is not None
                                                or lfam is not None):
                    tolerated_curses.add(eff)
                stype_code = _STYPE_CODE[ds.get_effect_stacking_type(eff)]
                if stype_code == K_STACK and lname is None and lfam is None:
                    static_score += weight
                    continue
                text_id = ds.get_effect_text_id(eff)
                excl = ds.get_effect_exclusivity_id(eff)
                compat = ds.get_effect_conflict_id(eff)
                penalty = (-dcw[compat]
                           if (compat != -1 and compat in dcw) else 0)
                dyn.append((stype_code, weight, eff, text_id, excl, compat,
                            penalty, lname, lfam))
            elif cat is None and is_curse:
                static_score += build.default_curse_weight

        curse_ids = tuple(
            c for c in relic.curses
            if c not in (EMPTY_EFFECT, 0) and not self._is_inert(c))
        penalized_curse_ids = (
            tuple(c for c in curse_ids if c not in tolerated_curses)
            if tolerated_curses else curse_ids)

        # place() metadata — mirrors VesselState.place per effect.
        place_ops: list[tuple] = []
        for eff in relic.all_effects:
            if self._is_inert(eff):
                continue  # mirrors VesselState.place's grey-out skip
            text_id = ds.get_effect_text_id(eff)
            text_add = text_id if (text_id != -1 and text_id != eff) else -1
            compat = ds.get_effect_conflict_id(eff)
            stype = ds.get_effect_stacking_type(eff)
            excl = ds.get_effect_exclusivity_id(eff)
            is_ns = stype == "no_stack"
            self_compat = compat if (is_ns and compat != -1 and compat == eff) else -1
            alias_base = -1
            if compat != -1 and compat != eff:
                if (ds.get_effect_conflict_id(compat) == compat
                        and ds.get_effect_stacking_type(compat) == "no_stack"):
                    alias_base = compat
            desired_compat = -1
            if dce and compat != -1 and compat in dce:
                dset = dce[compat]
                if eff in dset or (text_id != -1 and text_id in dset):
                    desired_compat = compat
            place_ops.append(
                (eff, text_add, excl, is_ns, self_compat, alias_base,
                 desired_compat))

        # Pre-union the place_ops columns into one frozenset per VesselState
        # collection — the form the solver consumes (solver_bridge ships these
        # five as the profile's placement id sets, and the Rust VesselState
        # applies each as one pass).  Derived from place_ops, which mirrors
        # VesselState.place effect by effect, so the two cannot drift; the
        # equivalence test pins the chain end to end.
        eff_set = frozenset(
            [op[0] for op in place_ops]
            + [op[1] for op in place_ops if op[1] != -1]
            + [op[5] for op in place_ops if op[5] != -1])
        excl_set = frozenset(op[2] for op in place_ops if op[2] != -1)
        ns_excl_set = frozenset(
            op[2] for op in place_ops if op[2] != -1 and op[3])
        ns_compat_set = frozenset(op[4] for op in place_ops if op[4] != -1)
        dcp_set = frozenset(op[6] for op in place_ops if op[6] != -1)

        # Limit-counter keys, deduped once per relic (mirrors place()).
        limit_keys: tuple[str, ...] = ()
        if elbn or flm:
            seen: set[str] = set()
            keys: list[str] = []
            for eff in relic.all_effects:
                if self._is_inert(eff):
                    continue
                name = ds.get_effect_name(eff)
                if name and name in elbn and name not in seen:
                    seen.add(name)
                    keys.append(name)
                family = ds.get_effect_family(eff)
                if family and family in flm and family not in seen:
                    seen.add(family)
                    keys.append(family)
            limit_keys = tuple(keys)

        profile = RelicProfile(
            relic=relic,
            static_score=static_score,
            dyn=tuple(dyn),
            curse_ids=curse_ids,
            eff_set=eff_set,
            excl_set=excl_set,
            ns_excl_set=ns_excl_set,
            ns_compat_set=ns_compat_set,
            dcp_set=dcp_set,
            limit_keys=limit_keys,
            pos_bound=self.positive_pre_score(relic, build),
            net=self.score_relic(relic, build),
            penalized_curse_ids=penalized_curse_ids,
        )
        self._profile_memo[relic.ga_handle] = profile
        return profile

    def _is_excl_category_effect_static(self, eff_id: int,
                                        build: BuildDefinition,
                                        dce: dict[int, set[int]]) -> bool:
        """_is_excl_category_effect without a VesselState (dce passed directly)."""
        if not dce:
            return False
        compat = self.data_source.get_effect_conflict_id(eff_id)
        if compat == -1:
            return False
        if self._is_inert(eff_id):
            return False  # greyed out — cannot block the desired effect
        if compat not in self._excl_cats:
            return False
        if compat not in dce:
            return False
        if self._is_effect_protected(eff_id, build):
            return False
        return True

    # ------------------------------------------------------------------
    # Breakdown (for UI / API)
    # ------------------------------------------------------------------

    def _classify_override(self, eff_id: int, state: VesselState) -> str:
        text_id = self.data_source.get_effect_text_id(eff_id)
        if eff_id in state.effect_ids:
            return "duplicate"
        if text_id != -1 and text_id in state.effect_ids:
            return "duplicate"
        # Tier-family conflict (unique blocked by no_stack base via compat)
        compat = self.data_source.get_effect_conflict_id(eff_id)
        if compat != -1 and compat in state.no_stack_compat_ids:
            return "overridden"
        return "overridden"

    def get_breakdown(self, relic: OwnedRelic, build: BuildDefinition,
                      state: VesselState | None = None,
                      ) -> list[dict]:
        """Per-effect scoring detail for UI / API display."""
        self._ensure_build_cache(build)
        breakdown = []
        has_state = state is not None

        for is_curse, effs in ((False, relic.effects), (True, relic.curses)):
            for eff in effs:
                if eff in (EMPTY_EFFECT, 0):
                    continue
                # Inert effects are still listed — the game shows them greyed
                # out rather than hiding them — but contribute nothing.
                if self._is_inert(eff):
                    breakdown.append({
                        "effect_id": eff,
                        "name": self.data_source.get_effect_name(eff),
                        "category": None,
                        "weight": 0,
                        "score": 0,
                        "is_curse": is_curse,
                        "redundant": True,
                        "override_status": "character_incompatible",
                    })
                    continue
                cat, weight = self._resolve_category_and_weight(eff, build)
                base_score = weight if (cat is not None and cat != "excluded") else 0
                # Apply default curse weight for unmatched curses
                if is_curse and cat is None and build.default_curse_weight != 0:
                    cat = "default_curse"
                    weight = build.default_curse_weight
                    base_score = build.default_curse_weight
                override_status = None

                # Positional stacking category handling — fires regardless of
                # whether the effect resolves to a build category, because an
                # undesired competitor (no group/family match) still blocks the
                # desired effect in the same compat when placed to its left.
                if (has_state
                        and self._is_excl_category_effect(eff, build, state)):
                    adj, excl_status = self._excluded_category_score(
                        eff, weight, build, state)
                    breakdown.append({
                        "effect_id": eff,
                        "name": self.data_source.get_effect_name(eff),
                        "category": cat,
                        "weight": weight,
                        "score": adj,
                        "is_curse": is_curse,
                        "redundant": True,
                        "override_status": excl_status,
                    })
                    continue

                if has_state and cat is not None and cat != "excluded":
                    # User-defined limit check (before stacking). Sign fork:
                    # desired effects cap at 0, undesired ones are penalized
                    # beyond their tolerance (mirrors score_relic_in_context).
                    if self._is_at_limit(eff, state):
                        override_status = (
                            "over_limit_penalty" if weight < 0
                            else "limit_reached")
                    else:
                        ctx_score = self._effect_stacking_score(
                            eff, weight, state)
                        if ctx_score < 0 and ctx_score != weight:
                            override_status = "conflict_penalty"
                        elif ctx_score == 0 and base_score != 0:
                            override_status = self._classify_override(eff, state)
                final_score = base_score
                if override_status == "conflict_penalty":
                    final_score = ctx_score  # negative
                elif override_status == "over_limit_penalty":
                    final_score = CURSE_EXCESS_PENALTY
                elif override_status is not None:
                    final_score = 0
                breakdown.append({
                    "effect_id": eff,
                    "name": self.data_source.get_effect_name(eff),
                    "category": cat,
                    "weight": weight,
                    "score": final_score,
                    "is_curse": is_curse,
                    "redundant": override_status is not None,
                    "override_status": override_status,
                })
        return breakdown
