"""SourceDataHandler singleton — loaded once at startup, shared across all requests."""
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


GameDataDep = Annotated[SourceDataHandler, Depends(get_game_data)]
