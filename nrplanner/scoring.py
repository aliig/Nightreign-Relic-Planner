"""Relic scoring with stacking awareness."""
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.data import SourceDataHandler
from nrplanner.models import (
    BuildDefinition, OwnedRelic,
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
        """display_name -> (category, weight) cache for name-based effect matching."""
        cache_key = (id(build.required_effects), id(build.excluded_effects),
                     tuple(id(g) for g in build.groups))
        if self._name_cache_key == cache_key:
            return self._name_cache
        cache: dict[str, tuple[str, int]] = {}
        # required effects
        for eid in build.required_effects:
            name = self.data_source.get_effect_name(eid)
            if name and name != "Empty" and not name.startswith("Effect "):
                cache.setdefault(name, ("required", REQUIRED_WEIGHT))
        # excluded effects
        for eid in build.excluded_effects:
            name = self.data_source.get_effect_name(eid)
            if name and name != "Empty" and not name.startswith("Effect "):
                cache.setdefault(name, ("excluded", 0))
        # group effects
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

    def has_excluded_effect(self, relic: OwnedRelic, build: BuildDefinition) -> bool:
        """True if the relic contains any excluded effect, family, or stacking category."""
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
            # Stacking category exclusion
            if excl_categories:
                compat = self.data_source.get_effect_conflict_id(eff)
                if compat != -1 and compat in excl_categories:
                    if not self._is_effect_protected(eff, build):
                        return True
        return False

    # Keep old name as alias for backwards compatibility during transition
    def has_blacklisted_effect(self, relic: OwnedRelic, build: BuildDefinition) -> bool:
        return self.has_excluded_effect(relic, build)

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
                                vessel_effect_ids: set[int],
                                vessel_exclusivity_ids: set[int],
                                vessel_no_stack_exclusivity_ids: set[int],
                                vessel_no_stack_compat_ids: set[int] | None = None,
                                desired_conflict_weights: dict[int, int] | None = None,
                                ) -> int:
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
            if eff_id in vessel_effect_ids:
                return 0
            if text_id != -1 and text_id in vessel_effect_ids:
                return 0
            if excl != -1 and excl in vessel_no_stack_exclusivity_ids:
                return self._conflict_penalty(eff_id, desired_conflict_weights)
            # Tier-family cross-check: unique variants blocked by no_stack base
            if vessel_no_stack_compat_ids is not None:
                compat = self.data_source.get_effect_conflict_id(eff_id)
                if compat != -1 and compat in vessel_no_stack_compat_ids:
                    return self._conflict_penalty(eff_id, desired_conflict_weights)
            return weight
        # no_stack
        if excl != -1 and excl in vessel_exclusivity_ids:
            return self._conflict_penalty(eff_id, desired_conflict_weights)
        if text_id != -1 and text_id in vessel_effect_ids:
            return 0
        if eff_id in vessel_effect_ids:
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

    def score_relic_in_context(self, relic: OwnedRelic, build: BuildDefinition,
                                vessel_effect_ids: set[int],
                                vessel_exclusivity_ids: set[int],
                                vessel_no_stack_exclusivity_ids: set[int],
                                vessel_curse_counts: dict[int, int] | None = None,
                                vessel_no_stack_compat_ids: set[int] | None = None,
                                desired_conflict_weights: dict[int, int] | None = None,
                                ) -> int:
        """Score considering stacking state of already-assigned relics."""
        score = 0
        for eff in relic.effects:
            if eff in (EMPTY_EFFECT, 0):
                continue
            cat, weight = self._resolve_category_and_weight(eff, build)
            if cat is not None and cat != "excluded":
                score += self._effect_stacking_score(
                    eff, cat, weight,
                    vessel_effect_ids, vessel_exclusivity_ids,
                    vessel_no_stack_exclusivity_ids, vessel_no_stack_compat_ids,
                    desired_conflict_weights)
        for curse in relic.curses:
            if curse in (EMPTY_EFFECT, 0):
                continue
            cat, weight = self._resolve_category_and_weight(curse, build)
            if cat is not None and cat != "excluded":
                score += self._effect_stacking_score(
                    curse, cat, weight,
                    vessel_effect_ids, vessel_exclusivity_ids,
                    vessel_no_stack_exclusivity_ids, vessel_no_stack_compat_ids,
                    desired_conflict_weights)
        if vessel_curse_counts is not None:
            for curse in relic.curses:
                if curse in (EMPTY_EFFECT, 0):
                    continue
                if vessel_curse_counts.get(curse, 0) >= build.curse_max:
                    score += CURSE_EXCESS_PENALTY
        return score

    # ------------------------------------------------------------------
    # Breakdown (for UI / API)
    # ------------------------------------------------------------------

    def _classify_override(self, eff_id: int, vessel_effect_ids: set[int],
                           vessel_exclusivity_ids: set[int],
                           vessel_no_stack_exclusivity_ids: set[int],
                           vessel_no_stack_compat_ids: set[int] | None = None) -> str:
        text_id = self.data_source.get_effect_text_id(eff_id)
        if eff_id in vessel_effect_ids:
            return "duplicate"
        if text_id != -1 and text_id in vessel_effect_ids:
            return "duplicate"
        # Tier-family conflict (unique blocked by no_stack base via compat)
        if vessel_no_stack_compat_ids is not None:
            compat = self.data_source.get_effect_conflict_id(eff_id)
            if compat != -1 and compat in vessel_no_stack_compat_ids:
                return "overridden"
        return "overridden"

    def get_breakdown(self, relic: OwnedRelic, build: BuildDefinition,
                      other_effect_ids: set[int] | None = None,
                      other_exclusivity_ids: set[int] | None = None,
                      other_no_stack_exclusivity_ids: set[int] | None = None,
                      other_no_stack_compat_ids: set[int] | None = None,
                      desired_conflict_weights: dict[int, int] | None = None,
                      ) -> list[dict]:
        """Per-effect scoring detail for UI / API display."""
        breakdown = []
        _eff_ids  = other_effect_ids or set()
        _excl_ids = other_exclusivity_ids or set()
        _ns_ids   = other_no_stack_exclusivity_ids or set()
        _ns_compat = other_no_stack_compat_ids

        for is_curse, effs in ((False, relic.effects), (True, relic.curses)):
            for eff in effs:
                if eff in (EMPTY_EFFECT, 0):
                    continue
                cat, weight = self._resolve_category_and_weight(eff, build)
                base_score = weight if (cat is not None and cat != "excluded") else 0
                override_status = None
                if other_effect_ids is not None and cat is not None and cat != "excluded":
                    ctx_score = self._effect_stacking_score(
                        eff, cat, weight, _eff_ids, _excl_ids, _ns_ids, _ns_compat,
                        desired_conflict_weights)
                    if ctx_score < 0:
                        override_status = "conflict_penalty"
                    elif ctx_score == 0 and base_score != 0:
                        override_status = self._classify_override(
                            eff, _eff_ids, _excl_ids, _ns_ids, _ns_compat)
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
