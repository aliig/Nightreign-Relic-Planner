"""Build definition persistence (JSON CRUD)."""
import pathlib
import uuid
from typing import Optional

import orjson

from nrplanner.models import ALL_TIER_KEYS, BuildDefinition


class BuildStore:
    """Persists BuildDefinitions to a JSON file."""

    CURRENT_VERSION = 4

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
                tiers = b.get("tiers", {})
                migrated_tiers = {
                    "required":    tiers.get("required", tiers.get("must_have", [])),
                    # v1-v3: "nice_to_have" was preferred; v4+ it's its own tier
                    "preferred":   tiers.get("preferred", tiers.get("nice_to_have", []) if version < 4 else []),
                    "nice_to_have": tiers.get("nice_to_have", []) if version >= 4 else [],
                    "avoid":       tiers.get("avoid", tiers.get("low_priority", [])),
                    "blacklist":   tiers.get("blacklist", []),
                }
                family_tiers = b.get("family_tiers", {k: [] for k in ALL_TIER_KEYS})
                for key in ALL_TIER_KEYS:
                    family_tiers.setdefault(key, [])
                self.builds[build_id] = BuildDefinition.model_validate({
                    "id":           build_id,
                    "name":         b["name"],
                    "character":    b["character"],
                    "tiers":        migrated_tiers,
                    "family_tiers": family_tiers,
                    "include_deep": b.get("include_deep", True),
                    "curse_max":    b.get("curse_max", 1),
                })
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
