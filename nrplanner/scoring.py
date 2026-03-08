"""Relic scoring with stacking awareness."""
from __future__ import annotations

from nrplanner.constants import EMPTY_EFFECT
from nrplanner.data import SourceDataHandler
from nrplanner.models import (
    BuildDefinition, OwnedRelic, VesselState,
    CURSE_EXCESS_PENALTY, REQUIRED_WEIGHT,
)


class BuildScorer:
    """Scores relics against a BuildDefinition with effect-stacking awareness."""

    def __init__(self, data_source: SourceDataHandler):
        self.data_source = data_source
        self._name_cache: dict[str, tuple[str, int]] = {}
        self._name_cache_key: object = None

    # ------------------------------------------------------------------
    # Category / weight resolution
    # ------------------------------------------------------------------

    def _get_name_cache(self, build: BuildDefinition) -> dict[str, tuple[str, int]]:
        """display_name -> (category, weight) cache for name-based effect matching.

        Required for alias resolution: many game effects share the same
        display name but have completely different IDs and text_ids.
        """
        cache_key = (id(build.required_effects), id(build.excluded_effects),
                     tuple(id(g) for g in build.groups))
        if self._name_cache_key == cache_key:
            return self._name_cache
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
        self._name_cache_key = cache_key
        return cache

    def _resolve_category_and_weight(self, eff_id: int,
                                      build: BuildDefinition) -> tuple[str | None, int]:
        """Return (category, weight) for an effect.

        Category is "required", "excluded", "group", or None (unassigned).
        Falls back through: direct ID -> text_id -> name -> family.

        Name-based matching is required for alias resolution — many game
        effects share a display name but have different IDs and text_ids.
        Family matching (via ``g.families``) adds magnitude weighting.
        """
        result = build.get_weight_for_effect(eff_id)
        if not result:
            text_id = self.data_source.get_effect_text_id(eff_id)
            if text_id != -1 and text_id != eff_id:
                result = build.get_weight_for_effect(text_id)
        if not result:
            name = self.data_source.get_effect_name(eff_id)
            result = self._get_name_cache(build).get(name)
        if result:
            return result

        family = self.data_source.get_effect_family(eff_id)
        if family:
            fresult = build.get_weight_for_family(family)
            if fresult:
                cat, base_w = fresult
                if cat == "excluded":
                    return cat, 0
                # All families get magnitude weighting (positive and negative)
                weight = self.data_source.get_family_magnitude_weight(eff_id, base_w)
                return cat, weight
        return None, 0

    # ------------------------------------------------------------------
    # Exclusion check
    # ------------------------------------------------------------------

    def _is_effect_protected(self, eff_id: int, build: BuildDefinition) -> bool:
        """True if effect is explicitly in required/weight groups (overrides category exclusion)."""
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
            result = self._get_name_cache(build).get(name)
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
        """
        excl_ids = set(build.excluded_effects)
        excl_families = build.excluded_families
        excl_categories = set(build.excluded_stacking_categories)
        if not excl_ids and not excl_families and not excl_categories:
            return False
        excl_names = {
            self.data_source.get_effect_name(eid)
            for eid in excl_ids
            if self.data_source.get_effect_name(eid) not in ("", "Empty", None)
        }
        _desired = desired_compat_effects or {}
        for eff in relic.all_effects:
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

    # Keep old name as alias for backwards compatibility during transition
    def has_blacklisted_effect(self, relic: OwnedRelic, build: BuildDefinition,
                               desired_compat_effects: dict[int, set[int]] | None = None,
                               ) -> bool:
        return self.has_excluded_effect(relic, build, desired_compat_effects)

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
        """
        result: dict[int, set[int]] = {}
        excl_cats = set(build.excluded_stacking_categories)
        if not excl_cats:
            return result
        for eff_id in self._all_build_effect_ids(build):
            compat = self.data_source.get_effect_conflict_id(eff_id)
            if compat != -1 and compat in excl_cats:
                if self._is_effect_protected(eff_id, build):
                    result.setdefault(compat, set()).add(eff_id)
        return result

    def has_orphaned_excl_category_effects(
        self,
        placed_effects: set[int],
        build: BuildDefinition,
        desired_compat_effects: dict[int, set[int]] | None = None,
    ) -> bool:
        """True if placed effects contain an undesired excluded-category effect
        without the corresponding desired effect also being present.

        Called after all vessel slots are assigned to validate that the result
        doesn't include "orphaned" excluded-category effects (e.g. Dormant
        Power Helps Discover Greataxes when only Greatshields was desired).
        """
        dce = desired_compat_effects or {}
        if not dce:
            return False
        excl_cats = set(build.excluded_stacking_categories)
        # For each excluded category with desired effects, check whether
        # an undesired member is placed without the desired member.
        for compat, desired_ids in dce.items():
            if compat not in excl_cats:
                continue
            # Expand desired IDs to include text_id aliases
            desired_expanded: set[int] = set(desired_ids)
            for d in desired_ids:
                text_id = self.data_source.get_effect_text_id(d)
                if text_id != -1:
                    desired_expanded.add(text_id)
            # Check if desired effect is among placed effects
            if placed_effects & desired_expanded:
                continue  # desired is present — OK
            # Desired not present — check if any undesired from this compat was placed
            for eff in placed_effects:
                if eff in desired_expanded:
                    continue
                eff_compat = self.data_source.get_effect_conflict_id(eff)
                if eff_compat == compat:
                    return True  # orphaned undesired effect found
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
        if compat not in set(build.excluded_stacking_categories):
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
        score = 0
        for eff in relic.effects:
            if eff in (EMPTY_EFFECT, 0):
                continue
            cat, weight = self._resolve_category_and_weight(eff, build)
            if cat is not None and cat != "excluded":
                score += weight
        for curse in relic.curses:
            if curse in (EMPTY_EFFECT, 0):
                continue
            cat, weight = self._resolve_category_and_weight(curse, build)
            if cat is not None and cat != "excluded":
                score += weight
        return score

    def get_desired_conflict_weights(self, build: BuildDefinition) -> dict[int, int]:
        """Map compatibilityId -> max weight of desired effects in that group.

        Used by the optimizer to penalize relics whose no_stack effects
        block a desired effect sharing the same compatibilityId group.
        """
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
        return result

    def _effect_stacking_score(self, eff_id: int, category: str, weight: int,
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

    def score_relic_in_context(self, relic: OwnedRelic, build: BuildDefinition,
                                state: VesselState) -> int:
        """Score considering stacking state of already-assigned relics."""
        score = 0
        for eff in relic.effects:
            if eff in (EMPTY_EFFECT, 0):
                continue
            cat, weight = self._resolve_category_and_weight(eff, build)
            if cat is not None and cat != "excluded":
                if self._is_at_limit(eff, state):
                    continue  # at user-defined limit, score 0 (neutral)
                # Positional stacking category handling
                if self._is_excl_category_effect(eff, build, state):
                    adj, _ = self._excluded_category_score(eff, weight, build, state)
                    score += adj
                else:
                    score += self._effect_stacking_score(eff, cat, weight, state)
        for curse in relic.curses:
            if curse in (EMPTY_EFFECT, 0):
                continue
            cat, weight = self._resolve_category_and_weight(curse, build)
            if cat is not None and cat != "excluded":
                if self._is_at_limit(curse, state):
                    continue  # at user-defined limit, score 0 (neutral)
                if self._is_excl_category_effect(curse, build, state):
                    adj, _ = self._excluded_category_score(curse, weight, build, state)
                    score += adj
                else:
                    score += self._effect_stacking_score(curse, cat, weight, state)
        for curse in relic.curses:
            if curse in (EMPTY_EFFECT, 0):
                continue
            if state.curse_counts.get(curse, 0) >= build.curse_max:
                score += CURSE_EXCESS_PENALTY
        return score

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
        breakdown = []
        has_state = state is not None

        for is_curse, effs in ((False, relic.effects), (True, relic.curses)):
            for eff in effs:
                if eff in (EMPTY_EFFECT, 0):
                    continue
                cat, weight = self._resolve_category_and_weight(eff, build)
                base_score = weight if (cat is not None and cat != "excluded") else 0
                override_status = None

                # Positional stacking category handling
                if (has_state and cat is not None and cat != "excluded"
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
                    # User-defined limit check (before stacking)
                    if self._is_at_limit(eff, state):
                        override_status = "limit_reached"
                    else:
                        ctx_score = self._effect_stacking_score(
                            eff, cat, weight, state)
                        if ctx_score < 0 and ctx_score != weight:
                            override_status = "conflict_penalty"
                        elif ctx_score == 0 and base_score != 0:
                            override_status = self._classify_override(eff, state)
                final_score = base_score
                if override_status == "conflict_penalty":
                    final_score = ctx_score  # negative
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
