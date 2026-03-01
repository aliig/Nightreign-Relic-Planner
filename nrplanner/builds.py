"""Build definition persistence (JSON CRUD)."""
import pathlib
import uuid
from typing import Optional

import orjson

from nrplanner.models import BuildDefinition, WeightGroup


_LEGACY_DEFAULT_WEIGHTS: dict[str, int] = {
    "preferred":   50,
    "nice_to_have": 25,
    "bonus":       10,
    "avoid":       -20,
}


def _migrate_to_v5(build_id: str, b: dict) -> dict:
    """Convert a pre-v5 build dict (tier-based) to the v5 weight-group schema."""
    tiers = b.get("tiers", {})
    family_tiers = b.get("family_tiers", {})
    tier_weights_override = b.get("tier_weights", {}) or {}

    required_effects = tiers.get("required", tiers.get("must_have", []))
    required_families = family_tiers.get("required", family_tiers.get("must_have", []))
    excluded_effects = tiers.get("blacklist", [])
    excluded_families = family_tiers.get("blacklist", [])

    groups = []
    for tier_key, default_w in _LEGACY_DEFAULT_WEIGHTS.items():
        effs = tiers.get(tier_key, [])
        fams = family_tiers.get(tier_key, [])
        if effs or fams:
            weight = tier_weights_override.get(tier_key, default_w)
            groups.append({"weight": weight, "effects": effs, "families": fams})

    return {
        "id": build_id,
        "name": b["name"],
        "character": b["character"],
        "groups": groups,
        "required_effects": required_effects,
        "required_families": required_families,
        "excluded_effects": excluded_effects,
        "excluded_families": excluded_families,
        "include_deep": b.get("include_deep", True),
        "curse_max": b.get("curse_max", 1),
        "pinned_relics": b.get("pinned_relics", []),
    }


class BuildStore:
    """Persists BuildDefinitions to a JSON file."""

    CURRENT_VERSION = 6

    def __init__(self, base_dir: pathlib.Path):
        self.file_path = base_dir / "optimizer_builds.json"
        self.builds: dict[str, BuildDefinition] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self.file_path.exists():
            return
        try:
            raw = orjson.loads(self.file_path.read_bytes())
            version = raw.get("version", 1)
            for build_id, b in raw.get("builds", {}).items():
                if version < 5:
                    # v1-v4 used tier-based schema; migrate to weight groups
                    b = _migrate_to_v5(build_id, b)
                else:
                    b["id"] = build_id
                self.builds[build_id] = BuildDefinition.model_validate(b)
        except Exception as e:
            print(f"[BuildStore] Error loading builds: {e}")

    def save(self) -> None:
        data = {
            "version": self.CURRENT_VERSION,
            "builds": {
                bid: b.model_dump(exclude={"id"})
                for bid, b in self.builds.items()
            },
        }
        self.file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, name: str, character: str) -> BuildDefinition:
        build_id = str(uuid.uuid4())[:8]
        build = BuildDefinition(id=build_id, name=name, character=character)
        self.builds[build_id] = build
        self.save()
        return build

    def get(self, build_id: str) -> Optional[BuildDefinition]:
        return self.builds.get(build_id)

    def list_builds(self) -> list[BuildDefinition]:
        return list(self.builds.values())

    def update(self, build: BuildDefinition) -> None:
        self.builds[build.id] = build
        self.save()

    def rename(self, build_id: str, new_name: str) -> None:
        if build_id in self.builds:
            self.builds[build_id].name = new_name
            self.save()

    def delete(self, build_id: str) -> None:
        if build_id in self.builds:
            del self.builds[build_id]
            self.save()
