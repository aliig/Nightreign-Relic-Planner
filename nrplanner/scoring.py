"""Relic scoring with stacking awareness."""
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.data import SourceDataHandler
from nrplanner.models import (
    BuildDefinition, OwnedRelic,
    CURSE_EXCESS_PENALTY, MAGNITUDE_TIERS, SCORED_TIERS, TIER_BONUS, TIER_WEIGHTS,
)


class BuildScorer:
    """Scores relics against a BuildDefinition with effect-stacking awareness."""

    def __init__(self, data_source: SourceDataHandler):
        self.data_source = data_source
        self._name_cache: dict[str, str] = {}
        self._name_cache_tiers = None

    # ------------------------------------------------------------------
    # Tier / weight resolution
    # ------------------------------------------------------------------

    def _get_name_cache(self, build: BuildDefinition) -> dict[str, str]:
        """display_name -> tier cache for name-based effect matching (lazy-built)."""
        if self._name_cache_tiers is build.tiers:
            return self._name_cache
        cache: dict[str, str] = {}
        for tier_name, effect_ids in build.tiers.items():
            for eid in effect_ids:
                name = self.data_source.get_effect_name(eid)
                if name and name != "Empty" and not name.startswith("Effect "):
                    cache.setdefault(name, tier_name)
        self._name_cache = cache
        self._name_cache_tiers = build.tiers
        return cache

    def _resolve_tier_and_weight(self, eff_id: int,
                                  build: BuildDefinition) -> tuple[str | None, int]:
        """Return (tier, weight) for an effect. Falls back to text_id, name, then family."""
        tier = build.get_tier_for_effect(eff_id)
        if not tier:
            text_id = self.data_source.get_effect_text_id(eff_id)
            if text_id != -1 and text_id != eff_id:
                tier = build.get_tier_for_effect(text_id)
        if not tier:
            name = self.data_source.get_effect_name(eff_id)
            tier = self._get_name_cache(build).get(name)
        if tier:
            return tier, TIER_WEIGHTS.get(tier, 0)

        family = self.data_source.get_effect_family(eff_id)
        if family:
            ftier = build.get_tier_for_family(family)
            if ftier:
                if ftier in MAGNITUDE_TIERS:
                    weight = self.data_source.get_family_magnitude_weight(eff_id, TIER_WEIGHTS[ftier])
                    return ftier, weight
                return ftier, TIER_WEIGHTS.get(ftier, 0)
        return None, 0

    # ------------------------------------------------------------------
    # Blacklist
    # ------------------------------------------------------------------

    def has_blacklisted_effect(self, relic: OwnedRelic, build: BuildDefinition) -> bool:
        blacklist_ids = set(build.tiers.get("blacklist", []))
        blacklist_families = build.family_tiers.get("blacklist", [])
        if not blacklist_ids and not blacklist_families:
            return False
        blacklist_names = {
            self.data_source.get_effect_name(eid)
            for eid in blacklist_ids
            if self.data_source.get_effect_name(eid) not in ("", "Empty", None)
        }
        for eff in relic.all_effects:
            if eff in blacklist_ids:
                return True
            text_id = self.data_source.get_effect_text_id(eff)
            if text_id != -1 and text_id != eff and text_id in blacklist_ids:
                return True
            if blacklist_names and self.data_source.get_effect_name(eff) in blacklist_names:
                return True
            if blacklist_families:
                family = self.data_source.get_effect_family(eff)
                if family and family in blacklist_families:
                    return True
        return False

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_relic(self, relic: OwnedRelic, build: BuildDefinition) -> int:
        """Pre-score without stacking context (used for initial sort / pruning)."""
        score = 0
        for eff in relic.effects:
            if eff in (EMPTY_EFFECT, 0):
                continue
            tier, weight = self._resolve_tier_and_weight(eff, build)
            if tier in SCORED_TIERS:
                score += weight
        for curse in relic.curses:
            if curse in (EMPTY_EFFECT, 0):
                continue
            tier, weight = self._resolve_tier_and_weight(curse, build)
            if tier in SCORED_TIERS:
                score += weight
        return score + TIER_BONUS.get(relic.effect_count, 0)

    def _effect_stacking_score(self, eff_id: int, tier: str, weight: int,
                                vessel_effect_ids: set[int],
                                vessel_compat_ids: set[int],
                                vessel_no_stack_compat_ids: set[int]) -> int:
        """Weight of an effect given what's already in the vessel (0 if redundant)."""
        stype    = self.data_source.get_effect_stacking_type(eff_id)
        compat   = self.data_source.get_effect_conflict_id(eff_id)
        text_id  = self.data_source.get_effect_text_id(eff_id)

        if stype == "stack":
            return weight
        if stype == "unique":
            if eff_id in vessel_effect_ids:
                return 0
            if text_id != -1 and text_id in vessel_effect_ids:
                return 0
            if compat != -1 and compat in vessel_no_stack_compat_ids:
                return 0
            return weight
        # no_stack
        if compat != -1 and compat in vessel_compat_ids:
            return 0
        if text_id != -1 and text_id in vessel_effect_ids:
            return 0
        if compat == -1 and eff_id in vessel_effect_ids:
            return 0
        return weight

    def score_relic_in_context(self, relic: OwnedRelic, build: BuildDefinition,
                                vessel_effect_ids: set[int],
                                vessel_compat_ids: set[int],
                                vessel_no_stack_compat_ids: set[int],
                                vessel_curse_counts: dict[int, int] | None = None) -> int:
        """Score considering stacking state of already-assigned relics."""
        score = 0
        for eff in relic.effects:
            if eff in (EMPTY_EFFECT, 0):
                continue
            tier, weight = self._resolve_tier_and_weight(eff, build)
            if tier in SCORED_TIERS:
                score += self._effect_stacking_score(
                    eff, tier, weight,
                    vessel_effect_ids, vessel_compat_ids, vessel_no_stack_compat_ids)
        for curse in relic.curses:
            if curse in (EMPTY_EFFECT, 0):
                continue
            tier, weight = self._resolve_tier_and_weight(curse, build)
            if tier in SCORED_TIERS:
                score += self._effect_stacking_score(
                    curse, tier, weight,
                    vessel_effect_ids, vessel_compat_ids, vessel_no_stack_compat_ids)
        if vessel_curse_counts is not None:
            for curse in relic.curses:
                if curse in (EMPTY_EFFECT, 0):
                    continue
                if vessel_curse_counts.get(curse, 0) >= build.curse_max:
                    score += CURSE_EXCESS_PENALTY
        return score + TIER_BONUS.get(relic.effect_count, 0)

    # ------------------------------------------------------------------
    # Breakdown (for UI / API)
    # ------------------------------------------------------------------

    def _classify_override(self, eff_id: int, vessel_effect_ids: set[int],
                           vessel_compat_ids: set[int],
                           vessel_no_stack_compat_ids: set[int]) -> str:
        text_id = self.data_source.get_effect_text_id(eff_id)
        if eff_id in vessel_effect_ids:
            return "duplicate"
        if text_id != -1 and text_id in vessel_effect_ids:
            return "duplicate"
        return "overridden"

    def get_breakdown(self, relic: OwnedRelic, build: BuildDefinition,
                      other_effect_ids: set[int] | None = None,
                      other_compat_ids: set[int] | None = None,
                      other_no_stack_compat_ids: set[int] | None = None,
                      ) -> list[dict]:
        """Per-effect scoring detail for UI / API display."""
        breakdown = []
        _eff_ids  = other_effect_ids or set()
        _comp_ids = other_compat_ids or set()
        _ns_ids   = other_no_stack_compat_ids or set()

        for is_curse, effs in ((False, relic.effects), (True, relic.curses)):
            for eff in effs:
                if eff in (EMPTY_EFFECT, 0):
                    continue
                tier, weight = self._resolve_tier_and_weight(eff, build)
                base_score = weight if tier else 0
                override_status = None
                if other_effect_ids is not None and tier in SCORED_TIERS:
                    ctx_score = self._effect_stacking_score(
                        eff, tier, weight, _eff_ids, _comp_ids, _ns_ids)
                    if ctx_score == 0 and base_score != 0:
                        override_status = self._classify_override(
                            eff, _eff_ids, _comp_ids, _ns_ids)
                breakdown.append({
                    "effect_id": eff,
                    "name": self.data_source.get_effect_name(eff),
                    "tier": tier,
                    "score": 0 if override_status else base_score,
                    "is_curse": is_curse,
                    "redundant": override_status is not None,
                    "override_status": override_status,
                })
        return breakdown
