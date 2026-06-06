"""SourceDataHandler singleton — loaded once at startup, shared across all requests."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from nrplanner import SourceDataHandler


@lru_cache(maxsize=1)
def get_game_data() -> SourceDataHandler:
    return SourceDataHandler(language="en_US")


@lru_cache(maxsize=1)
def get_items_json() -> dict:
    """Load items.json from nrplanner package resources (maps real_id → {name, color})."""
    import nrplanner as _pkg
    path = Path(_pkg.__file__).parent / "resources" / "json" / "items.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def game_data_version() -> str:
    """Stable fingerprint of the canonical game-data CSVs.

    No explicit version ships with the game data, so we hash the param CSVs
    (effect / relic / vessel definitions).  Stored as OptimizationSnapshot
    provenance: a data/balance change then invalidates stale snapshots
    automatically, without any special-casing.
    """
    import nrplanner as _pkg

    param_dir = Path(_pkg.__file__).parent / "resources" / "param"
    h = hashlib.sha256()
    for path in sorted(param_dir.glob("*.csv")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


GameDataDep = Annotated[SourceDataHandler, Depends(get_game_data)]
