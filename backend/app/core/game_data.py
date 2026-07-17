"""SourceDataHandler singleton — loaded once at startup, shared across all requests."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from nrplanner import RelicGenerator, SourceDataHandler


@lru_cache(maxsize=1)
def get_game_data() -> SourceDataHandler:
    return SourceDataHandler(language="en_US")


@lru_cache(maxsize=1)
def get_relic_lots() -> dict:
    """Acquisition-odds table for relic purchases (real color/tier weights).

    Loaded from relic_lots.json if present; otherwise {} and the generator falls
    back to labelled approximate odds. The effect roll is exact either way.
    """
    import nrplanner as _pkg
    path = Path(_pkg.__file__).parent / "resources" / "json" / "relic_lots.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def get_relic_generator() -> RelicGenerator:
    """Shared RelicGenerator singleton (its pool/template caches persist across rolls)."""
    return RelicGenerator(get_game_data(), lots=get_relic_lots())


@lru_cache(maxsize=1)
def get_items_json() -> dict:
    """Load items.json from nrplanner package resources (maps real_id → {name, color})."""
    import nrplanner as _pkg
    path = Path(_pkg.__file__).parent / "resources" / "json" / "items.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def game_data_version() -> str:
    """Stable fingerprint of the canonical game data.

    No explicit version ships with the game data, so we hash the param CSVs
    (effect / relic / vessel definitions) AND the resource JSON files
    (stacking_rules.json, effect_bonus_values.json, effects.json, items.json
    — all of which feed scoring/optimization).  Stored as OptimizationSnapshot
    provenance: a data/balance change to any of them then invalidates stale
    snapshots automatically, without any special-casing.
    """
    import nrplanner as _pkg

    resources = Path(_pkg.__file__).parent / "resources"
    h = hashlib.sha256()
    for path in sorted(resources.glob("param/*.csv")) + sorted(resources.glob("json/*.json")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


GameDataDep = Annotated[SourceDataHandler, Depends(get_game_data)]
RelicGeneratorDep = Annotated[RelicGenerator, Depends(get_relic_generator)]
