"""Pydantic models for relics, builds, and optimizer results.

These are the FastAPI-ready schemas — keep field names stable.
"""
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
