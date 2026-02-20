"""nrplanner — Nightreign relic build optimizer."""

from nrplanner.save import (
    RawRelic,
    decrypt_sl2,
    split_memory_dat,
    parse_relics,
    read_char_name,
    discover_characters,
)
from nrplanner.data import SourceDataHandler
from nrplanner.checker import RelicChecker, InvalidReason, is_curse_invalid
from nrplanner.vessel import LoadoutHandler
from nrplanner.models import (
    TierConfig, TIERS, TIER_MAP, ALL_TIER_KEYS,
    OwnedRelic, RelicInventory,
    BuildDefinition,
    SlotAssignment, VesselResult,
)
from nrplanner.scoring import BuildScorer
from nrplanner.optimizer import VesselOptimizer
from nrplanner.builds import BuildStore

__all__ = [
    # Save parsing
    "RawRelic", "decrypt_sl2", "split_memory_dat",
    "parse_relics", "read_char_name", "discover_characters",
    # Game data
    "SourceDataHandler",
    # Validation
    "RelicChecker", "InvalidReason", "is_curse_invalid",
    # Loadout
    "LoadoutHandler",
    # Models
    "TierConfig", "TIERS", "TIER_MAP", "ALL_TIER_KEYS",
    "OwnedRelic", "RelicInventory",
    "BuildDefinition",
    "SlotAssignment", "VesselResult",
    # Optimizer
    "BuildScorer", "VesselOptimizer",
    # Persistence
    "BuildStore",
]
