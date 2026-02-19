"""
Relic Build Optimizer - Core Logic
Scores relics against user-defined build priorities and finds optimal
relic assignments for vessel slots.
"""
import uuid
import time
import pathlib
from dataclasses import dataclass, field
from typing import Optional

import orjson

from globals import COLOR_MAP, RELIC_GROUPS
from source_data_handler import SourceDataHandler


# ---------------------------------------------------------------------------
# Tier configuration — single source of truth for all tier metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TierConfig:
    """Immutable definition of a single build tier."""
    key: str                    # Internal key used in storage / logic
    display_name: str           # User-facing label
    color: str                  # Hex color for UI
    weight: int                 # Scoring weight (0 = not scored)
    scored: bool                # Contributes to numeric scoring
    magnitude_weighted: bool    # Family magnitude weighting applies
    is_must_have: bool          # Hard constraint — at least one member required
    is_exclusion: bool          # Absolute filter — relics with this are removed
    show_debuffs_first: bool = False  # UI: surface curses at top of search

    @property
    def label_suffix(self) -> str:
        """Parenthetical suffix shown next to the display name in the UI."""
        if self.is_must_have:
            return f" ({self.weight:+d} pts, Must Have)"
        if self.scored:
            return f" ({self.weight:+d} pts)"
        if self.is_exclusion:
            return " (Absolute Exclusion)"
        return ""


# Ordered list — UI renders tiers in this order
TIERS: list[TierConfig] = [
    TierConfig("required",    "Essential",    "#FF4444", 100, True,  True,  True,  False),
    TierConfig("preferred",   "Preferred",    "#4488FF",  50, True,  True,  False, False),
    TierConfig("nice_to_have","Nice-to-Have", "#44BB88",  25, True,  True,  False, False),
    TierConfig("avoid",       "Avoid",        "#888888", -20, True,  False, False, False),
    TierConfig("blacklist",   "Excluded",     "#FF8C00",   0, False, False, False, True, True),
]

# Derived lookups — used throughout optimizer and UI
TIER_MAP: dict[str, TierConfig] = {t.key: t for t in TIERS}
ALL_TIER_KEYS: list[str] = [t.key for t in TIERS]
TIER_WEIGHTS: dict[str, int] = {t.key: t.weight for t in TIERS}
SCORED_TIERS: tuple[str, ...] = tuple(t.key for t in TIERS if t.scored)
MAGNITUDE_TIERS: tuple[str, ...] = tuple(t.key for t in TIERS if t.magnitude_weighted)

# Penalty applied per excess curse beyond the build's curse_max limit
CURSE_EXCESS_PENALTY = -200

# Small bonus for relics with more effect slots (tiebreaker)
TIER_BONUS = {3: 5, 2: 2, 1: 0, 0: 0}

EMPTY_EFFECT = 4294967295


@dataclass
class OwnedRelic:
    """A relic owned by the player, parsed from save data."""
    ga_handle: int
    item_id: int       # raw item_id from save
    real_id: int        # item_id - 2147483648
    color: str          # "Red", "Blue", "Yellow", "Green", "White"
    effects: list       # [effect_1, effect_2, effect_3]
    curses: list        # [sec_effect1, sec_effect2, sec_effect3]
    is_deep: bool
    name: str
    tier: str           # "Grand", "Polished", "Delicate"

    @property
    def effect_count(self) -> int:
        return sum(1 for e in self.effects if e != EMPTY_EFFECT and e != 0)

    @property
    def curse_count(self) -> int:
        return sum(1 for c in self.curses if c != EMPTY_EFFECT and c != 0)

    @property
    def all_effects(self) -> list:
        """All non-empty effects and curses combined."""
        return [e for e in self.effects + self.curses
                if e != EMPTY_EFFECT and e != 0]


class RelicInventory:
    """Queryable collection of owned relics."""

    def __init__(self, ga_relics: list, items_json: dict,
                 data_source: SourceDataHandler):
        self.relics: list[OwnedRelic] = []
        self._build(ga_relics, items_json, data_source)

    def _build(self, ga_relics: list, items_json: dict,
               data_source: SourceDataHandler):
        for r in ga_relics:
            ga_handle = r[0]
            item_id = r[1]
            real_id = item_id - 2147483648
            effects = [r[2], r[3], r[4]]
            curses = [r[5], r[6], r[7]]

            # Look up color and name
            id_str = str(real_id)
            info = items_json.get(id_str, {})
            color = info.get("color", "Red")
            name = info.get("name", f"Relic {real_id}")

            if color is None:
                continue  # Skip non-relic items (Flatstones, etc.)

            is_deep = data_source.is_deep_relic(real_id)

            # Determine tier by non-empty effect count
            effect_count = sum(1 for e in effects
                               if e != EMPTY_EFFECT and e != 0)
            if effect_count >= 3:
                tier = "Grand"
            elif effect_count == 2:
                tier = "Polished"
            else:
                tier = "Delicate"

            self.relics.append(OwnedRelic(
                ga_handle=ga_handle,
                item_id=item_id,
                real_id=real_id,
                color=color,
                effects=effects,
                curses=curses,
                is_deep=is_deep,
                name=name,
                tier=tier,
            ))

    def get_by_color(self, color: str) -> list[OwnedRelic]:
        return [r for r in self.relics if r.color == color]

    def get_standard(self) -> list[OwnedRelic]:
        return [r for r in self.relics if not r.is_deep]

    def get_deep(self) -> list[OwnedRelic]:
        return [r for r in self.relics if r.is_deep]

    def get_candidates(self, slot_color: str, is_deep_slot: bool) -> list[OwnedRelic]:
        """Get relics eligible for a specific slot (by color and type only)."""
        results = []
        for r in self.relics:
            # Standard/deep type must match
            if is_deep_slot != r.is_deep:
                continue
            # Color must match (White slots accept any)
            if slot_color != "White" and r.color != slot_color:
                continue
            results.append(r)
        return results

    def __len__(self):
        return len(self.relics)


@dataclass
class BuildDefinition:
    """A user-defined build configuration."""
    id: str
    name: str
    character: str
    tiers: dict = field(default_factory=lambda: {k: [] for k in ALL_TIER_KEYS})
    family_tiers: dict = field(default_factory=lambda: {k: [] for k in ALL_TIER_KEYS})
    include_deep: bool = True
    curse_max: int = 1  # Max times the same curse is tolerated (0=avoid all, 1=default)

    def all_prioritized_effects(self) -> set:
        """All effect IDs across all tiers."""
        result = set()
        for tier_effects in self.tiers.values():
            result.update(tier_effects)
        return result

    def get_tier_for_effect(self, effect_id: int) -> Optional[str]:
        """Return the tier name an effect belongs to, or None."""
        for tier_name, effects in self.tiers.items():
            if effect_id in effects:
                return tier_name
        return None

    def get_tier_for_family(self, family_name: str) -> Optional[str]:
        """Return the tier name a family belongs to, or None."""
        for tier_name, families in self.family_tiers.items():
            if family_name in families:
                return tier_name
        return None


class BuildStore:
    """Persists build definitions to JSON."""

    def __init__(self, base_dir: pathlib.Path):
        self.file_path = base_dir / "optimizer_builds.json"
        self.builds: dict[str, BuildDefinition] = {}
        self._load()

    def _load(self):
        if not self.file_path.exists():
            return
        try:
            data = orjson.loads(self.file_path.read_bytes())
            for build_id, b in data.get("builds", {}).items():
                # Migrate old tier names to new ones
                tiers = b.get("tiers", {})
                version = data.get("version", 1)
                migrated_tiers = {
                    "required": tiers.get("required", tiers.get("must_have", [])),
                    # v1 "nice_to_have" mapped to preferred (different from v4's nice_to_have)
                    "preferred": tiers.get("preferred", tiers.get("nice_to_have", [])
                                          if version < 4 else []),
                    "nice_to_have": tiers.get("nice_to_have", [])
                                    if version >= 4 else [],
                    "avoid": tiers.get("avoid", tiers.get("low_priority", [])),
                    "blacklist": tiers.get("blacklist", []),
                }
                family_tiers = b.get("family_tiers",
                                     {k: [] for k in ALL_TIER_KEYS})
                # Ensure all tier keys exist (handles older saves)
                for key in ALL_TIER_KEYS:
                    family_tiers.setdefault(key, [])
                self.builds[build_id] = BuildDefinition(
                    id=build_id,
                    name=b["name"],
                    character=b["character"],
                    tiers=migrated_tiers,
                    family_tiers=family_tiers,
                    include_deep=b.get("include_deep", True),
                    curse_max=b.get("curse_max", 1),
                )
        except Exception as e:
            print(f"[BuildStore] Error loading builds: {e}")

    def save(self):
        data = {
            "version": 4,  # Version 4: adds nice_to_have tier, curse_max
            "builds": {}
        }
        for build_id, b in self.builds.items():
            data["builds"][build_id] = {
                "name": b.name,
                "character": b.character,
                "tiers": b.tiers,
                "family_tiers": b.family_tiers,
                "include_deep": b.include_deep,
                "curse_max": b.curse_max,
            }
        self.file_path.write_bytes(
            orjson.dumps(data, option=orjson.OPT_INDENT_2)
        )

    def create(self, name: str, character: str) -> BuildDefinition:
        build_id = str(uuid.uuid4())[:8]
        build = BuildDefinition(id=build_id, name=name, character=character)
        self.builds[build_id] = build
        self.save()
        return build

    def delete(self, build_id: str):
        if build_id in self.builds:
            del self.builds[build_id]
            self.save()

    def rename(self, build_id: str, new_name: str):
        if build_id in self.builds:
            self.builds[build_id].name = new_name
            self.save()

    def update(self, build: BuildDefinition):
        self.builds[build.id] = build
        self.save()

    def get(self, build_id: str) -> Optional[BuildDefinition]:
        return self.builds.get(build_id)

    def list_builds(self) -> list[BuildDefinition]:
        return list(self.builds.values())


class BuildScorer:
    """Scores relics against a build definition with stacking awareness."""

    def __init__(self, data_source: SourceDataHandler):
        self.data_source = data_source
        self._name_cache: dict[str, str] = {}
        self._name_cache_tiers = None

    def _get_name_cache(self, build: BuildDefinition) -> dict[str, str]:
        """Lazy-build a display_name -> tier cache for name-based matching.

        Handles cases where multiple param IDs share the same display name
        (e.g., different contexts for "Improved Damage Negation at Low HP").
        """
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
                                  build: BuildDefinition) -> tuple:
        """Resolve tier and base weight for an effect.

        Checks individual effect tiers first, then family tiers.
        Falls back to attachTextId and display name for variant effects.
        Returns (tier, weight) or (None, 0) if unmatched.
        """
        # Individual effect check (direct ID)
        tier = build.get_tier_for_effect(eff_id)
        if not tier:
            # Variant effects: try canonical text ID
            text_id = self.data_source.get_effect_text_id(eff_id)
            if text_id != -1 and text_id != eff_id:
                tier = build.get_tier_for_effect(text_id)
        if not tier:
            # Name-based fallback: different param IDs, same display name
            name_cache = self._get_name_cache(build)
            eff_name = self.data_source.get_effect_name(eff_id)
            if eff_name in name_cache:
                tier = name_cache[eff_name]
        if tier:
            return tier, TIER_WEIGHTS.get(tier, 0)

        # Family check (get_effect_family already has text_id fallback)
        family_name = self.data_source.get_effect_family(eff_id)
        if family_name:
            ftier = build.get_tier_for_family(family_name)
            if ftier:
                if ftier in MAGNITUDE_TIERS:
                    weight = self.data_source.get_family_magnitude_weight(
                        eff_id, TIER_WEIGHTS[ftier])
                    return ftier, weight
                else:
                    return ftier, TIER_WEIGHTS.get(ftier, 0)
        return None, 0

    def has_blacklisted_effect(self, relic: OwnedRelic,
                                build: BuildDefinition) -> bool:
        """Check if relic has any blacklisted effects."""
        blacklist_ids = set(build.tiers.get("blacklist", []))
        blacklist_families = build.family_tiers.get("blacklist", [])
        if not blacklist_ids and not blacklist_families:
            return False
        # Build name set for blacklisted effects (name-based matching)
        blacklist_names = set()
        for eid in blacklist_ids:
            name = self.data_source.get_effect_name(eid)
            if name and name != "Empty":
                blacklist_names.add(name)
        for eff in relic.all_effects:
            if eff in blacklist_ids:
                return True
            # Also check variant effects via text_id
            text_id = self.data_source.get_effect_text_id(eff)
            if text_id != -1 and text_id != eff and text_id in blacklist_ids:
                return True
            # Name-based check for same-name variants
            if blacklist_names:
                eff_name = self.data_source.get_effect_name(eff)
                if eff_name in blacklist_names:
                    return True
            if blacklist_families:
                family = self.data_source.get_effect_family(eff)
                if family and family in blacklist_families:
                    return True
        return False

    def score_relic(self, relic: OwnedRelic,
                    build: BuildDefinition) -> int:
        """Score a relic without stacking context (used for initial sorting)."""
        score = 0
        for eff in relic.effects:
            if eff == EMPTY_EFFECT or eff == 0:
                continue
            tier, weight = self._resolve_tier_and_weight(eff, build)
            if tier in SCORED_TIERS:
                score += weight
        for curse in relic.curses:
            if curse == EMPTY_EFFECT or curse == 0:
                continue
            tier, weight = self._resolve_tier_and_weight(curse, build)
            if tier in SCORED_TIERS:
                score += weight
        score += TIER_BONUS.get(relic.effect_count, 0)
        return score

    def _effect_stacking_score(self, eff_id: int, tier: str,
                                weight: int,
                                vessel_effect_ids: set,
                                vessel_compat_ids: set,
                                vessel_no_stack_compat_ids: set) -> int:
        """Score a single effect considering stacking context.

        Returns the weight if the effect provides value, 0 if redundant.
        Uses attachTextId to detect variant effects (different param ID
        but functionally identical to the base effect).
        """
        stype = self.data_source.get_effect_stacking_type(eff_id)
        compat_id = self.data_source.get_effect_conflict_id(eff_id)
        text_id = self.data_source.get_effect_text_id(eff_id)

        if stype == "stack":
            return weight
        elif stype == "unique":
            if eff_id in vessel_effect_ids:
                return 0  # Exact duplicate
            if text_id != -1 and text_id in vessel_effect_ids:
                return 0  # Variant of existing effect
            if compat_id != -1 and compat_id in vessel_no_stack_compat_ids:
                return 0  # Blocked by a no_stack in same group
            return weight
        else:  # no_stack
            if compat_id != -1 and compat_id in vessel_compat_ids:
                return 0  # Any group member already present
            if text_id != -1 and text_id in vessel_effect_ids:
                return 0  # Variant of existing effect
            if compat_id == -1 and eff_id in vessel_effect_ids:
                return 0  # No group, same effect present
            return weight

    def score_relic_in_context(self, relic: OwnedRelic,
                                build: BuildDefinition,
                                vessel_effect_ids: set,
                                vessel_compat_ids: set,
                                vessel_no_stack_compat_ids: set,
                                vessel_curse_counts: dict = None) -> int:
        """Score a relic considering what's already assigned to the vessel."""
        score = 0
        for eff in relic.effects:
            if eff == EMPTY_EFFECT or eff == 0:
                continue
            tier, weight = self._resolve_tier_and_weight(eff, build)
            if tier in SCORED_TIERS:
                score += self._effect_stacking_score(
                    eff, tier, weight, vessel_effect_ids,
                    vessel_compat_ids, vessel_no_stack_compat_ids)
        for curse in relic.curses:
            if curse == EMPTY_EFFECT or curse == 0:
                continue
            tier, weight = self._resolve_tier_and_weight(curse, build)
            if tier in SCORED_TIERS:
                score += self._effect_stacking_score(
                    curse, tier, weight, vessel_effect_ids,
                    vessel_compat_ids, vessel_no_stack_compat_ids)
        # Penalize relics whose curses would exceed curse_max
        if vessel_curse_counts is not None:
            curse_max = build.curse_max
            for curse in relic.curses:
                if curse == EMPTY_EFFECT or curse == 0:
                    continue
                current_count = vessel_curse_counts.get(curse, 0)
                if current_count >= curse_max:
                    score += CURSE_EXCESS_PENALTY
        score += TIER_BONUS.get(relic.effect_count, 0)
        return score

    def get_breakdown(self, relic: OwnedRelic,
                      build: BuildDefinition,
                      other_effect_ids: set = None,
                      other_compat_ids: set = None,
                      other_no_stack_compat_ids: set = None) -> list[dict]:
        """Detailed per-effect scoring for UI display.

        If stacking context is provided (other_* params = effects from OTHER
        relics in the vessel), marks redundant effects.
        """
        breakdown = []
        for eff in relic.effects:
            if eff == EMPTY_EFFECT or eff == 0:
                continue
            tier, weight = self._resolve_tier_and_weight(eff, build)
            name = self.data_source.get_effect_name(eff)
            base_score = weight if tier else 0
            redundant = False
            if other_effect_ids is not None and tier in SCORED_TIERS:
                ctx_score = self._effect_stacking_score(
                    eff, tier, weight, other_effect_ids,
                    other_compat_ids or set(),
                    other_no_stack_compat_ids or set())
                redundant = (ctx_score == 0 and base_score != 0)
            breakdown.append({
                "effect_id": eff,
                "name": name,
                "tier": tier,
                "score": 0 if redundant else base_score,
                "is_curse": False,
                "redundant": redundant,
            })

        for curse in relic.curses:
            if curse == EMPTY_EFFECT or curse == 0:
                continue
            tier, weight = self._resolve_tier_and_weight(curse, build)
            name = self.data_source.get_effect_name(curse)
            base_score = weight if tier else 0
            redundant = False
            if other_effect_ids is not None and tier in SCORED_TIERS:
                ctx_score = self._effect_stacking_score(
                    curse, tier, weight, other_effect_ids,
                    other_compat_ids or set(),
                    other_no_stack_compat_ids or set())
                redundant = (ctx_score == 0 and base_score != 0)
            breakdown.append({
                "effect_id": curse,
                "name": name,
                "tier": tier,
                "score": 0 if redundant else base_score,
                "is_curse": True,
                "redundant": redundant,
            })

        return breakdown


@dataclass
class SlotAssignment:
    """A relic assigned to a specific vessel slot."""
    slot_index: int
    slot_color: str
    is_deep: bool
    relic: Optional[OwnedRelic]
    score: int
    breakdown: list


@dataclass
class VesselResult:
    """Optimization result for a single vessel."""
    vessel_id: int
    vessel_name: str
    vessel_character: str
    unlock_flag: int
    slot_colors: tuple  # 6-tuple of color strings
    assignments: list   # list[SlotAssignment]
    total_score: int
    meets_requirements: bool = True  # False if any required effects are missing
    missing_requirements: list = field(default_factory=list)  # IDs of missing required effects


class VesselOptimizer:
    """Finds optimal relic assignment for vessel slots."""

    def __init__(self, data_source: SourceDataHandler, scorer: BuildScorer):
        self.data_source = data_source
        self.scorer = scorer

    def _get_relic_stacking_adds(self, relic: OwnedRelic) -> tuple:
        """Get stacking state contributions for a relic.

        Returns (effect_ids, compat_ids, no_stack_compat_ids) — the sets
        that this relic adds to the vessel's stacking context.
        Also includes attachTextId values so variant effects are recognized
        as duplicates of the base effect.
        """
        effect_ids = set()
        compat_ids = set()
        no_stack_compat_ids = set()
        for eff in relic.all_effects:
            effect_ids.add(eff)
            # Also track canonical text ID for variant dedup
            text_id = self.data_source.get_effect_text_id(eff)
            if text_id != -1 and text_id != eff:
                effect_ids.add(text_id)
            compat_id = self.data_source.get_effect_conflict_id(eff)
            if compat_id != -1:
                compat_ids.add(compat_id)
                if self.data_source.get_effect_stacking_type(eff) == "no_stack":
                    no_stack_compat_ids.add(compat_id)
        return effect_ids, compat_ids, no_stack_compat_ids

    @staticmethod
    def _get_relic_curse_ids(relic: OwnedRelic) -> list:
        """Get non-empty curse IDs from a relic."""
        return [c for c in relic.curses if c != EMPTY_EFFECT and c != 0]

    def optimize(self, build: BuildDefinition,
                 inventory: RelicInventory,
                 vessel_data: dict) -> VesselResult:
        """Find best relic assignment for a single vessel."""
        slot_colors = vessel_data["Colors"]  # 6-tuple
        num_slots = 6 if build.include_deep else 3

        # Build candidate lists per slot
        candidates_per_slot = []
        for i in range(num_slots):
            is_deep = i >= 3
            slot_color = slot_colors[i]
            candidates = inventory.get_candidates(slot_color, is_deep)

            # Filter out blacklisted relics
            candidates = [
                r for r in candidates
                if not self.scorer.has_blacklisted_effect(r, build)
            ]

            # Pre-score (without stacking context) for sorting and pruning
            scored = []
            for relic in candidates:
                score = self.scorer.score_relic(relic, build)
                scored.append((score, relic))
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates_per_slot.append(scored)

        # Choose algorithm based on candidate count
        total_candidates = sum(len(c) for c in candidates_per_slot)

        if total_candidates <= 200 and num_slots <= 6:
            assignments = self._backtrack_solve(
                candidates_per_slot, num_slots, build
            )
        else:
            assignments = self._greedy_solve(
                candidates_per_slot, num_slots, build
            )

        # Build result with incremental stacking context for breakdowns
        slot_assignments = []
        total_score = 0
        assigned_effect_ids = set()
        vessel_eff = set()
        vessel_compat = set()
        vessel_no_stack = set()
        vessel_curse_counts: dict[int, int] = {}

        for i in range(num_slots):
            is_deep = i >= 3
            slot_color = slot_colors[i]
            relic = assignments[i][0]

            if relic:
                score = self.scorer.score_relic_in_context(
                    relic, build, vessel_eff, vessel_compat, vessel_no_stack,
                    vessel_curse_counts)
                breakdown = self.scorer.get_breakdown(
                    relic, build, vessel_eff, vessel_compat, vessel_no_stack)
                assigned_effect_ids.update(relic.all_effects)
                # Also track canonical text IDs for requirement matching
                for eff in relic.all_effects:
                    text_id = self.data_source.get_effect_text_id(eff)
                    if text_id != -1:
                        assigned_effect_ids.add(text_id)
                # Update stacking state for subsequent relics
                e, c, ns = self._get_relic_stacking_adds(relic)
                vessel_eff.update(e)
                vessel_compat.update(c)
                vessel_no_stack.update(ns)
                for curse_id in self._get_relic_curse_ids(relic):
                    vessel_curse_counts[curse_id] = \
                        vessel_curse_counts.get(curse_id, 0) + 1
            else:
                score = 0
                breakdown = []

            slot_assignments.append(SlotAssignment(
                slot_index=i,
                slot_color=slot_color,
                is_deep=is_deep,
                relic=relic,
                score=score,
                breakdown=breakdown,
            ))
            total_score += score

        # Check if all required effects are present
        missing_requirements = []
        # Individual required effects
        required_ids = set(build.tiers.get("required", []))
        missing_requirements.extend(required_ids - assigned_effect_ids)
        # Family required effects
        for family_name in build.family_tiers.get("required", []):
            family_ids = self.data_source.get_family_effect_ids(family_name)
            if not (assigned_effect_ids & family_ids):
                missing_requirements.append(family_name)
        meets_requirements = len(missing_requirements) == 0

        return VesselResult(
            vessel_id=vessel_data.get("_id", 0),
            vessel_name=vessel_data["Name"],
            vessel_character=vessel_data["Character"],
            unlock_flag=vessel_data["unlockFlag"],
            slot_colors=slot_colors,
            assignments=slot_assignments,
            total_score=total_score,
            meets_requirements=meets_requirements,
            missing_requirements=missing_requirements,
        )

    def _greedy_solve(self, candidates_per_slot: list, num_slots: int,
                      build: BuildDefinition) -> list:
        """Greedy assignment with stacking-aware re-scoring.

        Iteratively picks the best (slot, relic) pair considering current
        vessel stacking state, then updates state and repeats.
        """
        assigned = [None] * num_slots  # (relic, score) per slot
        used_handles = set()
        vessel_eff = set()
        vessel_compat = set()
        vessel_no_stack = set()
        vessel_curse_counts: dict[int, int] = {}

        for _ in range(num_slots):
            best = None  # (score, slot_idx, relic)

            for slot_idx in range(num_slots):
                if assigned[slot_idx] is not None:
                    continue
                for _, relic in candidates_per_slot[slot_idx]:
                    if relic.ga_handle in used_handles:
                        continue
                    score = self.scorer.score_relic_in_context(
                        relic, build, vessel_eff,
                        vessel_compat, vessel_no_stack,
                        vessel_curse_counts)
                    if best is None or score > best[0]:
                        best = (score, slot_idx, relic)

            if best is None:
                break  # No candidates for any remaining slot

            score, slot_idx, relic = best
            assigned[slot_idx] = (relic, score)
            used_handles.add(relic.ga_handle)
            e, c, ns = self._get_relic_stacking_adds(relic)
            vessel_eff.update(e)
            vessel_compat.update(c)
            vessel_no_stack.update(ns)
            for curse_id in self._get_relic_curse_ids(relic):
                vessel_curse_counts[curse_id] = \
                    vessel_curse_counts.get(curse_id, 0) + 1

        # Fill empty slots
        for i in range(num_slots):
            if assigned[i] is None:
                assigned[i] = (None, 0)

        return assigned

    def _backtrack_solve(self, candidates_per_slot: list, num_slots: int,
                         build: BuildDefinition) -> list:
        """Exhaustive search with stacking-aware scoring and pruning."""
        best_score = [-1]
        best_assignment = [None]
        start_time = time.time()
        timeout = 2.0  # seconds

        def backtrack(slot_idx, current_assignment, used_handles,
                      vessel_eff, vessel_compat, vessel_no_stack,
                      vessel_curse_counts, current_score):
            if time.time() - start_time > timeout:
                return

            if slot_idx == num_slots:
                if current_score > best_score[0]:
                    best_score[0] = current_score
                    best_assignment[0] = list(current_assignment)
                return

            # Try assigning no relic to this slot
            current_assignment[slot_idx] = (None, 0)
            backtrack(slot_idx + 1, current_assignment, used_handles,
                      vessel_eff, vessel_compat, vessel_no_stack,
                      vessel_curse_counts, current_score)

            # Try each candidate
            for pre_score, relic in candidates_per_slot[slot_idx]:
                if relic.ga_handle in used_handles:
                    continue

                # Prune: pre-computed scores are upper bounds (stacking can
                # only reduce scores), so use them for fast pruning
                remaining_max = sum(
                    candidates_per_slot[s][0][0]
                    if candidates_per_slot[s] else 0
                    for s in range(slot_idx + 1, num_slots)
                )
                if current_score + pre_score + remaining_max <= best_score[0]:
                    continue

                # Score with stacking context
                score = self.scorer.score_relic_in_context(
                    relic, build, vessel_eff,
                    vessel_compat, vessel_no_stack,
                    vessel_curse_counts)

                # Prune again with actual score
                if current_score + score + remaining_max <= best_score[0]:
                    continue

                # Compute stacking state additions
                added_eff, added_compat, added_no_stack = \
                    self._get_relic_stacking_adds(relic)
                added_curses = self._get_relic_curse_ids(relic)

                # Assign
                current_assignment[slot_idx] = (relic, score)
                used_handles.add(relic.ga_handle)
                vessel_eff.update(added_eff)
                vessel_compat.update(added_compat)
                vessel_no_stack.update(added_no_stack)
                for cid in added_curses:
                    vessel_curse_counts[cid] = \
                        vessel_curse_counts.get(cid, 0) + 1

                backtrack(slot_idx + 1, current_assignment, used_handles,
                          vessel_eff, vessel_compat, vessel_no_stack,
                          vessel_curse_counts, current_score + score)

                # Unassign
                used_handles.discard(relic.ga_handle)
                vessel_eff -= added_eff
                vessel_compat -= added_compat
                vessel_no_stack -= added_no_stack
                for cid in added_curses:
                    vessel_curse_counts[cid] -= 1
                    if vessel_curse_counts[cid] == 0:
                        del vessel_curse_counts[cid]

        initial = [(None, 0)] * num_slots
        backtrack(0, initial, set(), set(), set(), set(), {}, 0)

        if best_assignment[0] is None:
            return [(None, 0)] * num_slots
        return best_assignment[0]

    def optimize_all_vessels(self, build: BuildDefinition,
                             inventory: RelicInventory,
                             hero_type: int) -> list[VesselResult]:
        """Try all vessels for a character, return top results sorted by score.

        Results that meet requirements are sorted first, then by score.
        Results that don't meet requirements are sorted last.
        """
        vessels = self.data_source.get_all_vessels_for_hero(hero_type)
        results = []

        for v in vessels:
            vessel_data = v.copy()
            vessel_data["_id"] = v["vessel_id"]
            result = self.optimize(build, inventory, vessel_data)
            result.vessel_id = v["vessel_id"]
            results.append(result)

        # Sort: vessels meeting requirements first (by score descending),
        # then vessels not meeting requirements (by score descending)
        results.sort(key=lambda r: (not r.meets_requirements, -r.total_score))
        return results
