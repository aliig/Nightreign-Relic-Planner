"""Hero loadout and vessel parsing from USERDATA binary data."""
import re
import struct
from dataclasses import dataclass, field

from nrplanner.constants import ITEM_TYPE_RELIC
from nrplanner.data import SourceDataHandler


@dataclass
class HeroLoadout:
    hero_type: int
    cur_preset_idx: int
    cur_vessel_id: int
    vessels: list   # list[dict] — keys: vessel_id, relics, offsets
    offsets: dict   # hero-level byte offsets
    presets: list = field(default_factory=list)

    def add_preset(self, hero_type: int, index: int, name: str,
                   vessel_id: int, relics: list, offsets: dict,
                   counter: int, timestamp: int) -> None:
        self.presets.append({
            "hero_type": hero_type, "index": index, "name": name,
            "vessel_id": vessel_id, "relics": relics, "offsets": offsets,
            "counter": counter, "timestamp": timestamp,
        })


class VesselParser:
    _MAGIC = re.compile(
        re.escape(bytes.fromhex("C2000300002C000003000A0004004600")) +
        re.escape(bytes.fromhex("64000000"))
    )

    def __init__(self, data_handler: SourceDataHandler):
        self.game_data = data_handler
        self.heroes: dict[int, HeroLoadout] = {}
        self.relic_ga_hero_map: dict[int, set[int]] = {}
        self.base_offset: int | None = None

    def parse(self, data: bytes) -> None:
        """Parse hero loadouts from a USERDATA binary blob."""
        heroes: dict[int, HeroLoadout] = {}
        self.relic_ga_hero_map = {}
        self.base_offset = None

        match = self._MAGIC.search(data)
        if not match:
            print("[Error] Magic pattern not found in vessel data.")
            return

        self.base_offset = match.start()
        cursor = match.end()

        # --- Section 1: Fixed 10 hero slots ---
        last_hero_type = None
        for _ in range(10):
            h_start = cursor
            hero_type, cur_idx = struct.unpack_from("<BB", data, cursor)
            hero_type = int(hero_type)
            hero_offsets = {
                "base": h_start,
                "cur_preset_idx": h_start + 1,
                "cur_vessel_id": h_start + 4,
            }
            cursor += 4  # hero_type, cur_idx, 2 bytes padding

            cur_v_id = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4

            universal_vessels = []
            for _ in range(4):
                v_start = cursor
                v_id = struct.unpack_from("<I", data, cursor)[0]
                cursor += 4
                relics = list(struct.unpack_from("<6I", data, cursor))
                self._register_relic_handles(relics, hero_type)
                universal_vessels.append({
                    "vessel_id": v_id,
                    "relics": relics,
                    "offsets": {"vessel_id": v_start, "relics": v_start + 4},
                })
                cursor += 24

            heroes[hero_type] = HeroLoadout(
                hero_type, int(cur_idx), cur_v_id, universal_vessels, hero_offsets)
            last_hero_type = hero_type

        # --- Section 2: Hero-specific vessels ---
        while cursor < len(data):
            v_start = cursor
            v_id = struct.unpack_from("<I", data, cursor)[0]
            if v_id == 0:
                cursor += 4
                break
            cursor += 4
            relics = list(struct.unpack_from("<6I", data, cursor))

            v_meta = self.game_data.get_vessel_data(v_id)
            target_hero = v_meta.get("hero_type") if v_meta else None
            assigned_id = last_hero_type if target_hero == 11 else target_hero
            self._register_relic_handles(relics, assigned_id)

            if assigned_id in heroes:
                heroes[assigned_id].vessels.append({
                    "vessel_id": v_id,
                    "relics": relics,
                    "offsets": {"vessel_id": v_start, "relics": v_start + 4},
                })
            cursor += 24

        for h in heroes.values():
            h.vessels.sort(key=lambda v: v["vessel_id"])

        # --- Section 3: Custom presets ---
        preset_index = 0
        while cursor < len(data):
            p_start = cursor
            header = struct.unpack_from("<B", data, cursor)[0]
            if header != 0x01:
                break

            p_offsets = {
                "base": p_start,
                "hero_type": p_start + 1,
                "counter": p_start + 3,
                "name": p_start + 4,
                "vessel_id": p_start + 44,
                "relics": p_start + 48,
                "timestamp": p_start + 72,
            }

            cursor += 1
            h_id = int(struct.unpack_from("<H", data, cursor)[0])
            cursor += 2
            counter_val = struct.unpack_from("<B", data, cursor)[0]
            cursor += 1

            name = data[cursor:cursor + 36].decode("utf-16", errors="ignore").strip("\x00")
            cursor += 36 + 4  # name + padding

            v_id = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4
            relics = list(struct.unpack_from("<6I", data, cursor))
            cursor += 24
            self._register_relic_handles(relics, h_id)

            timestamp = struct.unpack_from("<Q", data, cursor)[0]
            cursor += 8

            if h_id in heroes:
                heroes[h_id].add_preset(
                    h_id, preset_index, name, v_id, relics, p_offsets, counter_val, timestamp)

            preset_index += 1
            if counter_val == 0:
                break

        self.heroes = heroes

    def _register_relic_handles(self, relics: list[int], hero_type: int | None) -> None:
        if hero_type is None:
            return
        for r in relics:
            if (r & 0xF0000000) == ITEM_TYPE_RELIC and r != 0:
                self.relic_ga_hero_map.setdefault(r, set()).add(hero_type)


class LoadoutHandler:
    """Read-only hero loadout facade over VesselParser."""

    def __init__(self, game_data: SourceDataHandler):
        self.parser = VesselParser(game_data)
        self.all_presets: list[dict] = []

    @property
    def heroes(self) -> dict[int, HeroLoadout]:
        return self.parser.heroes

    @property
    def relic_ga_hero_map(self) -> dict[int, set[int]]:
        return self.parser.relic_ga_hero_map

    def parse(self, data: bytes) -> None:
        """Parse hero loadouts from a USERDATA binary blob."""
        self.parser.parse(data)
        self.all_presets = sorted(
            (p for h in self.heroes.values() for p in h.presets),
            key=lambda p: p["index"],
        )

    def check_hero(self, hero_type: int) -> bool:
        return hero_type in self.heroes

    def check_vessel(self, hero_type: int, vessel_id: int) -> bool:
        if not self.check_hero(hero_type):
            raise ValueError(f"Hero {hero_type} not found")
        return any(v["vessel_id"] == vessel_id for v in self.heroes[hero_type].vessels)

    def get_vessel_id(self, hero_type: int, vessel_index: int) -> int:
        vessels = self.heroes[hero_type].vessels
        if 0 <= vessel_index < len(vessels):
            return vessels[vessel_index]["vessel_id"]
        raise ValueError(f"Invalid vessel index {vessel_index}")

    def get_vessel_index_in_hero(self, hero_type: int, vessel_id: int) -> int:
        if self.check_vessel(hero_type, vessel_id):
            for idx, v in enumerate(self.heroes[hero_type].vessels):
                if v["vessel_id"] == vessel_id:
                    return idx
        return -1

    def get_relic_ga_handle(self, hero_type: int, vessel_id: int, relic_index: int) -> int:
        if not self.check_vessel(hero_type, vessel_id):
            raise ValueError("Vessel not found")
        if not 0 <= relic_index <= 5:
            raise ValueError(f"Invalid relic index {relic_index}")
        for v in self.heroes[hero_type].vessels:
            if v["vessel_id"] == vessel_id:
                return v["relics"][relic_index]
        raise ValueError("Vessel not found")
