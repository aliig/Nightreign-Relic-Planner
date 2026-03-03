"""Pydantic models for relics, builds, and optimizer results.

These are the FastAPI-ready schemas — keep field names stable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

from nrplanner.constants import EMPTY_EFFECT, is_unique_relic
from nrplanner.data import SourceDataHandler
from nrplanner.save import RawRelic


# ---------------------------------------------------------------------------
# Weight group system
# ---------------------------------------------------------------------------

# Scoring weight applied to required effects (also a hard constraint)
REQUIRED_WEIGHT = 100

# Penalty per excess curse beyond build.curse_max
CURSE_EXCESS_PENALTY = -200


class WeightGroup(BaseModel):
    """A user-defined group of effects/families sharing a scoring weight."""
    model_config = ConfigDict(frozen=True)

    weight: int                    # Scoring weight; negative = penalty
    effects: list[int] = Field(default_factory=list)   # Effect IDs in this group
    families: list[str] = Field(default_factory=list)  # Family names (magnitude-weighted)


# ---------------------------------------------------------------------------
# Relic models
# ---------------------------------------------------------------------------

class OwnedRelic(BaseModel):
    """A relic owned by the player, parsed from save data."""
    ga_handle: int
    item_id: int    # raw value from save
    real_id: int    # item_id - 2147483648
    color: str      # "Red" | "Blue" | "Yellow" | "Green" | "White"
    effects: list[int]
    curses: list[int]
    is_deep: bool
    name: str
    tier: str       # "Grand" | "Polished" | "Delicate"

    @computed_field
    @property
    def effect_count(self) -> int:
        return sum(1 for e in self.effects if e not in (EMPTY_EFFECT, 0))

    @computed_field
    @property
    def curse_count(self) -> int:
        return sum(1 for c in self.curses if c not in (EMPTY_EFFECT, 0))

    @computed_field
    @property
    def all_effects(self) -> list[int]:
        return [e for e in self.effects + self.curses if e not in (EMPTY_EFFECT, 0)]


class RelicInventory:
    """Queryable collection of owned relics (not Pydantic — internal use)."""

    def __init__(self, ga_relics: list[RawRelic], items_json: dict,
                 data_source: SourceDataHandler):
        self.relics: list[OwnedRelic] = []
        self._build(ga_relics, items_json, data_source)

    def _build(self, ga_relics: list[RawRelic], items_json: dict,
               data_source: SourceDataHandler) -> None:
        seen_unique_ids: set[int] = set()
        for r in ga_relics:
            real_id = r.item_id - 2147483648
            if is_unique_relic(real_id):
                if real_id in seen_unique_ids:
                    continue
                seen_unique_ids.add(real_id)
            info = items_json.get(str(real_id), {})
            color = info.get("color", "Red")
            if color is None:
                continue  # skip Flatstones, etc.
            name = info.get("name", f"Relic {real_id}")
            effects = [r.effect_1, r.effect_2, r.effect_3]
            curses  = [r.sec_effect1, r.sec_effect2, r.sec_effect3]
            effect_count = sum(1 for e in effects if e not in (EMPTY_EFFECT, 0))
            tier = "Grand" if effect_count >= 3 else ("Polished" if effect_count == 2 else "Delicate")
            self.relics.append(OwnedRelic(
                ga_handle=r.ga_handle,
                item_id=r.item_id,
                real_id=real_id,
                color=color,
                effects=effects,
                curses=curses,
                is_deep=data_source.is_deep_relic(real_id),
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
        """Relics eligible for a slot (color + deep/standard type only)."""
        return [
            r for r in self.relics
            if r.is_deep == is_deep_slot
            and (slot_color == "White" or r.color == slot_color)
        ]

    @classmethod
    def from_owned_relics(cls, relics: list[OwnedRelic]) -> "RelicInventory":
        """Construct directly from already-parsed OwnedRelics (skip save parsing)."""
        instance = cls.__new__(cls)
        instance.relics = list(relics)
        return instance

    def __len__(self) -> int:
        return len(self.relics)


# ---------------------------------------------------------------------------
# Build definition
# ---------------------------------------------------------------------------

class BuildDefinition(BaseModel):
    """User-defined build configuration. Stable API schema."""
    id: str
    name: str
    character: str
    groups: list[WeightGroup] = Field(default_factory=list)
    required_effects: list[int] = Field(default_factory=list)
    required_families: list[str] = Field(default_factory=list)
    excluded_effects: list[int] = Field(default_factory=list)
    excluded_families: list[str] = Field(default_factory=list)
    include_deep: bool = True
    curse_max: int = 1  # max times the same curse is tolerated (0=avoid all)
    pinned_relics: list[int] = Field(default_factory=list)  # ga_handle IDs to force-assign
    excluded_stacking_categories: list[int] = Field(default_factory=list)  # compatibilityId values
    effect_limits: dict[int, int] = Field(default_factory=dict)   # effect_id → max_count
    family_limits: dict[str, int] = Field(default_factory=dict)   # family_name → max_count

    def get_weight_for_effect(self, effect_id: int) -> tuple[str, int] | None:
        """Return (category, weight) for a direct effect ID lookup.

        Category is "required", "excluded", or "group".
        Returns None if the effect is not mentioned in this build.
        """
        if effect_id in self.required_effects:
            return ("required", REQUIRED_WEIGHT)
        if effect_id in self.excluded_effects:
            return ("excluded", 0)
        for g in self.groups:
            if effect_id in g.effects:
                return ("group", g.weight)
        return None

    def get_weight_for_family(self, family_name: str) -> tuple[str, int] | None:
        """Return (category, weight) for a family name lookup.

        Returns None if the family is not mentioned in this build.
        """
        if family_name in self.required_families:
            return ("required", REQUIRED_WEIGHT)
        if family_name in self.excluded_families:
            return ("excluded", 0)
        for g in self.groups:
            if family_name in g.families:
                return ("group", g.weight)
        return None

    def all_prioritized_effects(self) -> set[int]:
        """All effect IDs explicitly mentioned in this build."""
        result: set[int] = set()
        result.update(self.required_effects)
        result.update(self.excluded_effects)
        for g in self.groups:
            result.update(g.effects)
        return result

    def get_effective_requirements(self) -> tuple[list[int], list[str]]:
        """Derive requirements from highest-weight group for missing-effect signaling.

        If explicit required_effects/required_families are set (legacy builds),
        use those. Otherwise, derive from the highest positive-weight group.
        """
        if self.required_effects or self.required_families:
            return self.required_effects, self.required_families
        if not self.groups:
            return [], []
        best = max(self.groups, key=lambda g: g.weight)
        if best.weight <= 0:
            return [], []
        return list(best.effects), list(best.families)


# ---------------------------------------------------------------------------
# Optimizer results
# ---------------------------------------------------------------------------

class SlotAssignment(BaseModel):
    """A relic assigned to one vessel slot."""
    slot_index: int
    slot_color: str
    is_deep: bool
    relic: Optional[OwnedRelic] = None
    score: int
    breakdown: list[dict[str, Any]]


class VesselResult(BaseModel):
    """Optimization result for a single vessel. Ready as FastAPI response."""
    vessel_id: int
    vessel_name: str
    vessel_character: str
    unlock_flag: int
    slot_colors: tuple[str, ...]
    assignments: list[SlotAssignment]
    total_score: int
    meets_requirements: bool = True
    missing_requirements: list[int | str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Vessel stacking state
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PlacementDelta:
    """Reversible record of state changes from placing one relic."""
    effect_ids: frozenset[int]
    exclusivity_ids: frozenset[int]
    no_stack_exclusivity_ids: frozenset[int]
    no_stack_compat_ids: frozenset[int]
    desired_compat_placed: frozenset[int]
    curse_ids: tuple[int, ...]
    limited_name_increments: tuple[str, ...] = ()


class VesselState:
    """Mutable stacking state for a vessel being built up slot-by-slot.

    Encapsulates the 6 mutable tracking sets/dicts plus the 2 build-level
    precomputed values that were previously threaded as loose parameters.
    """
    __slots__ = (
        'data_source',
        'effect_ids', 'exclusivity_ids', 'no_stack_exclusivity_ids',
        'no_stack_compat_ids', 'curse_counts', 'desired_compat_placed',
        'desired_conflict_weights', 'desired_compat_effects',
        # User-defined effect/family limits
        'limited_counts', 'effect_limit_by_name', 'family_limit_map',
        'limited_names',
    )

    def __init__(
        self,
        data_source: SourceDataHandler,
        desired_conflict_weights: dict[int, int] | None = None,
        desired_compat_effects: dict[int, set[int]] | None = None,
        effect_limit_by_name: dict[str, int] | None = None,
        family_limit_map: dict[str, int] | None = None,
    ):
        self.data_source = data_source
        self.effect_ids: set[int] = set()
        self.exclusivity_ids: set[int] = set()
        self.no_stack_exclusivity_ids: set[int] = set()
        self.no_stack_compat_ids: set[int] = set()
        self.curse_counts: dict[int, int] = {}
        self.desired_compat_placed: set[int] = set()
        # Build-level precomputed (immutable after init)
        self.desired_conflict_weights = desired_conflict_weights
        self.desired_compat_effects = desired_compat_effects
        # User-defined limits (immutable after init)
        self.effect_limit_by_name: dict[str, int] = effect_limit_by_name or {}
        self.family_limit_map: dict[str, int] = family_limit_map or {}
        self.limited_names: frozenset[str] = frozenset(
            list(self.effect_limit_by_name) + list(self.family_limit_map)
        )
        self.limited_counts: dict[str, int] = {}

    def place(self, relic: OwnedRelic) -> PlacementDelta:
        """Compute and apply state changes for placing a relic. Returns delta for undo.

        IMPORTANT: The delta only records IDs that were NOT already in the
        state.  This is critical for correct backtracking — remove() uses set
        subtraction, so recording already-present IDs would cause them to be
        incorrectly removed when undoing this placement.
        """
        added_eff: set[int] = set()
        added_excl: set[int] = set()
        added_ns_excl: set[int] = set()
        added_ns_compat: set[int] = set()

        ds = self.data_source
        for eff in relic.all_effects:
            if eff not in self.effect_ids:
                added_eff.add(eff)
            text_id = ds.get_effect_text_id(eff)
            if text_id != -1 and text_id != eff and text_id not in self.effect_ids:
                added_eff.add(text_id)
            compat = ds.get_effect_conflict_id(eff)
            stype = ds.get_effect_stacking_type(eff)
            excl = ds.get_effect_exclusivity_id(eff)
            if excl != -1:
                if excl not in self.exclusivity_ids:
                    added_excl.add(excl)
                if stype == "no_stack" and excl not in self.no_stack_exclusivity_ids:
                    added_ns_excl.add(excl)
            # Rule 1: no_stack base placed (self-referencing compat)
            if stype == "no_stack" and compat != -1 and compat == eff:
                if compat not in self.no_stack_compat_ids:
                    added_ns_compat.add(compat)
            # Rule 2: variant placed that points to a real no_stack tier-family base.
            # Guard: compat must be self-referencing (a real tier-family base ID, not a
            # mega-group sentinel like 100).  Add the base's eff_id to effect_ids so the
            # base is blocked via the identity check (eff_id in vessel_effect_ids).
            # Do NOT add to no_stack_compat_ids — that would incorrectly block sibling
            # variants (e.g. HP Restore +2 blocked when +1 is placed).
            elif compat != -1 and compat != eff:
                if (ds.get_effect_conflict_id(compat) == compat
                        and ds.get_effect_stacking_type(compat) == "no_stack"):
                    if compat not in self.effect_ids:
                        added_eff.add(compat)

        # Desired compat tracking
        added_dcp: set[int] = set()
        dce = self.desired_compat_effects
        if dce:
            for eff in relic.all_effects:
                compat = ds.get_effect_conflict_id(eff)
                if compat == -1 or compat not in dce:
                    continue
                if compat in self.desired_compat_placed:
                    continue  # already tracked
                desired_set = dce[compat]
                if eff in desired_set:
                    added_dcp.add(compat)
                else:
                    text_id = ds.get_effect_text_id(eff)
                    if text_id != -1 and text_id in desired_set:
                        added_dcp.add(compat)

        # Curse tracking
        curse_ids = tuple(
            c for c in relic.curses if c not in (EMPTY_EFFECT, 0)
        )

        # Limit tracking — count each limited name/family once per relic
        limited_increments: list[str] = []
        if self.limited_names:
            seen_for_limits: set[str] = set()
            for eff in relic.all_effects:
                eff_name = ds.get_effect_name(eff)
                if (eff_name and eff_name in self.effect_limit_by_name
                        and eff_name not in seen_for_limits):
                    seen_for_limits.add(eff_name)
                    limited_increments.append(eff_name)
                family = ds.get_effect_family(eff)
                if (family and family in self.family_limit_map
                        and family not in seen_for_limits):
                    seen_for_limits.add(family)
                    limited_increments.append(family)

        # Apply mutations
        self.effect_ids.update(added_eff)
        self.exclusivity_ids.update(added_excl)
        self.no_stack_exclusivity_ids.update(added_ns_excl)
        self.no_stack_compat_ids.update(added_ns_compat)
        self.desired_compat_placed.update(added_dcp)
        for cid in curse_ids:
            self.curse_counts[cid] = self.curse_counts.get(cid, 0) + 1
        for name in limited_increments:
            self.limited_counts[name] = self.limited_counts.get(name, 0) + 1

        return PlacementDelta(
            effect_ids=frozenset(added_eff),
            exclusivity_ids=frozenset(added_excl),
            no_stack_exclusivity_ids=frozenset(added_ns_excl),
            no_stack_compat_ids=frozenset(added_ns_compat),
            desired_compat_placed=frozenset(added_dcp),
            curse_ids=curse_ids,
            limited_name_increments=tuple(limited_increments),
        )

    def remove(self, delta: PlacementDelta) -> None:
        """Undo a placement (for backtracking)."""
        self.effect_ids -= delta.effect_ids
        self.exclusivity_ids -= delta.exclusivity_ids
        self.no_stack_exclusivity_ids -= delta.no_stack_exclusivity_ids
        self.no_stack_compat_ids -= delta.no_stack_compat_ids
        self.desired_compat_placed -= delta.desired_compat_placed
        for cid in delta.curse_ids:
            self.curse_counts[cid] -= 1
            if self.curse_counts[cid] == 0:
                del self.curse_counts[cid]
        for name in delta.limited_name_increments:
            self.limited_counts[name] -= 1
            if self.limited_counts[name] == 0:
                del self.limited_counts[name]

    def copy(self) -> VesselState:
        """Shallow copy for branching (e.g. result builder needs a fresh copy)."""
        clone = VesselState.__new__(VesselState)
        clone.data_source = self.data_source
        clone.effect_ids = set(self.effect_ids)
        clone.exclusivity_ids = set(self.exclusivity_ids)
        clone.no_stack_exclusivity_ids = set(self.no_stack_exclusivity_ids)
        clone.no_stack_compat_ids = set(self.no_stack_compat_ids)
        clone.curse_counts = dict(self.curse_counts)
        clone.desired_compat_placed = set(self.desired_compat_placed)
        clone.desired_conflict_weights = self.desired_conflict_weights
        clone.desired_compat_effects = self.desired_compat_effects
        # Limits: copy mutable counter, share immutable config
        clone.effect_limit_by_name = self.effect_limit_by_name
        clone.family_limit_map = self.family_limit_map
        clone.limited_names = self.limited_names
        clone.limited_counts = dict(self.limited_counts)
        return clone
