"""Pydantic models for relics, builds, and optimizer results.

These are the FastAPI-ready schemas — keep field names stable.
"""
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

from nrplanner.constants import EMPTY_EFFECT, is_unique_relic
from nrplanner.data import SourceDataHandler
from nrplanner.save import RawRelic


# ---------------------------------------------------------------------------
# Tier system
# ---------------------------------------------------------------------------

class TierConfig(BaseModel):
    """Immutable definition of a single build tier."""
    model_config = ConfigDict(frozen=True)

    key: str
    display_name: str
    color: str
    weight: int
    scored: bool
    magnitude_weighted: bool
    is_must_have: bool
    is_exclusion: bool
    show_debuffs_first: bool = False

    @computed_field
    @property
    def label_suffix(self) -> str:
        if self.is_must_have:
            return f" ({self.weight:+d} pts, Must Have)"
        if self.scored:
            return f" ({self.weight:+d} pts)"
        if self.is_exclusion:
            return " (Absolute Exclusion)"
        return ""


TIERS: list[TierConfig] = [
    TierConfig(key="required",     display_name="Essential",    color="#FF4444", weight=100, scored=True,  magnitude_weighted=True,  is_must_have=True,  is_exclusion=False),
    TierConfig(key="preferred",    display_name="Preferred",    color="#4488FF", weight=50,  scored=True,  magnitude_weighted=True,  is_must_have=False, is_exclusion=False),
    TierConfig(key="nice_to_have", display_name="Nice-to-Have", color="#44BB88", weight=25,  scored=True,  magnitude_weighted=True,  is_must_have=False, is_exclusion=False),
    TierConfig(key="bonus",        display_name="Bonus",        color="#9966CC", weight=10,  scored=True,  magnitude_weighted=True,  is_must_have=False, is_exclusion=False),
    TierConfig(key="avoid",        display_name="Avoid",        color="#888888", weight=-20, scored=True,  magnitude_weighted=False, is_must_have=False, is_exclusion=False),
    TierConfig(key="blacklist",    display_name="Excluded",     color="#FF8C00", weight=0,   scored=False, magnitude_weighted=False, is_must_have=False, is_exclusion=True, show_debuffs_first=True),
]

TIER_MAP:               dict[str, TierConfig] = {t.key: t for t in TIERS}
ALL_TIER_KEYS:          list[str]             = [t.key for t in TIERS]
TIER_WEIGHTS:           dict[str, int]        = {t.key: t.weight for t in TIERS}
SCORED_TIERS:           tuple[str, ...]       = tuple(t.key for t in TIERS if t.scored)
MAGNITUDE_TIERS:        tuple[str, ...]       = tuple(t.key for t in TIERS if t.magnitude_weighted)

# Penalty per excess curse beyond build.curse_max
CURSE_EXCESS_PENALTY = -200


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
    tiers: dict[str, list[int]] = Field(
        default_factory=lambda: {k: [] for k in ALL_TIER_KEYS})
    family_tiers: dict[str, list[str]] = Field(
        default_factory=lambda: {k: [] for k in ALL_TIER_KEYS})
    include_deep: bool = True
    curse_max: int = 1  # max times the same curse is tolerated (0=avoid all)
    tier_weights: dict[str, int] | None = None  # per-build overrides; None = use defaults
    pinned_relics: list[int] = Field(default_factory=list)  # ga_handle IDs to force-assign

    def get_effective_weights(self) -> dict[str, int]:
        """Return tier weights merged with any per-build overrides."""
        if self.tier_weights:
            return {**TIER_WEIGHTS, **self.tier_weights}
        return dict(TIER_WEIGHTS)

    def all_prioritized_effects(self) -> set[int]:
        result: set[int] = set()
        for effects in self.tiers.values():
            result.update(effects)
        return result

    def get_tier_for_effect(self, effect_id: int) -> Optional[str]:
        for tier_name, effects in self.tiers.items():
            if effect_id in effects:
                return tier_name
        return None

    def get_tier_for_family(self, family_name: str) -> Optional[str]:
        for tier_name, families in self.family_tiers.items():
            if family_name in families:
                return tier_name
        return None


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
