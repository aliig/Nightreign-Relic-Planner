"""nrplanner — Nightreign relic build optimizer."""

from nrplanner.save import (
    RawRelic,
    decrypt_sl2,
    split_memory_dat,
    parse_relics,
    read_char_name,
    discover_characters,
    read_owner_steam_id,
)
from nrplanner.data import SourceDataHandler
from nrplanner.checker import RelicChecker, InvalidReason, is_curse_invalid
from nrplanner.vessel import LoadoutHandler
from nrplanner.models import (
    WeightGroup, REQUIRED_WEIGHT,
    OwnedRelic, RelicInventory,
    BuildDefinition,
    SlotAssignment, VesselResult,
    PlacementDelta, VesselState,
    BuildChange, RelicRef,
)
from nrplanner.scoring import BuildScorer
from nrplanner.optimizer import VesselOptimizer
from nrplanner.writer import (
    DeleteResult,
    FavoriteResult,
    delete_relics,
    read_acquisition_ids,
    read_favorite_handles,
    read_murks,
    repack_sl2,
    sell_value,
    set_favorites,
)
from nrplanner.vessel_writer import (
    PresetRecord,
    VesselWriteError,
    add_preset,
    delete_preset,
    now_filetime,
    overwrite_preset,
    parse_presets,
    rename_preset,
    reset_all_presets,
    reset_all_vessels,
)

__all__ = [
    # Save parsing
    "RawRelic", "decrypt_sl2", "split_memory_dat",
    "parse_relics", "read_char_name", "discover_characters",
    "read_owner_steam_id",
    # Game data
    "SourceDataHandler",
    # Validation
    "RelicChecker", "InvalidReason", "is_curse_invalid",
    # Loadout
    "LoadoutHandler",
    # Models
    "WeightGroup", "REQUIRED_WEIGHT",
    "OwnedRelic", "RelicInventory",
    "BuildDefinition",
    "SlotAssignment", "VesselResult",
    "PlacementDelta", "VesselState",
    "BuildChange", "RelicRef",
    # Optimizer
    "BuildScorer", "VesselOptimizer",
    # Save write-back
    "DeleteResult", "FavoriteResult", "delete_relics", "read_acquisition_ids",
    "read_favorite_handles", "read_murks", "repack_sl2", "sell_value",
    "set_favorites",
    # Loadout / vessel write-back
    "PresetRecord", "VesselWriteError", "add_preset", "delete_preset",
    "now_filetime", "overwrite_preset", "parse_presets", "rename_preset",
    "reset_all_presets", "reset_all_vessels",
]
