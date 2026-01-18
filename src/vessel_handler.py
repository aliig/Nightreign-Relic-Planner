import struct
import re
import time
from copy import deepcopy
from source_data_handler import SourceDataHandler
from relic_checker import RelicChecker
from basic_class import Item
import globals
from globals import ITEM_TYPE_RELIC, COLOR_MAP


def get_now_timestamp():
    EPOCH_OFFSET = 11644473600
    now_unix = time.time()
    filetime_long = int((now_unix + EPOCH_OFFSET) * 1000) * 10000
    return filetime_long


class HeroLoadout:
    def __init__(self, hero_type, cur_preset_idx, cur_vessel_id, vessels, offsets):
        self.hero_type = hero_type
        self.cur_preset_idx = cur_preset_idx
        self.cur_vessel_id = cur_vessel_id
        # vessel list[dict]， keys: vessel_id, relics, offsets:dict
        #   offsets store offests for vessel_id and relics, keys: vessel_id, relics
        self.vessels = vessels
        self.presets = []
        # Stores offsets for hero-level fields
        self.offsets = offsets

    def add_preset(self, hero_type, index, name, vessel_id, relics, offsets, counter, timestamp):
        self.presets.append({
            "hero_type": hero_type,
            "index": index,
            "name": name,
            "vessel_id": vessel_id,
            "relics": relics,
            "offsets": offsets,
            "counter": counter,
            "timestamp": timestamp
        })


class VesselParser:
    # Items type
    ITEM_TYPE_EMPTY = 0x00000000
    ITEM_TYPE_WEAPON = 0x80000000
    ITEM_TYPE_ARMOR = 0x90000000
    ITEM_TYPE_RELIC = 0xC0000000

    def __init__(self, data_handler: SourceDataHandler):
        self.game_data = data_handler
        self.heroes: dict[int, HeroLoadout] = {}
        self.relic_ga_hero_map = {}
        self.base_offset = None

    def parse(self):
        heroes = {}
        self.relic_ga_hero_map = {}
        self.base_offset = None
        magic_pattern = re.escape(bytes.fromhex("C2000300002C000003000A0004004600"))
        marker = re.escape(bytes.fromhex("64000000"))

        match = re.search(magic_pattern + marker, globals.data)
        if not match:
            print("[Error] Magic pattern not found.")
            return

        cursor = match.start()
        self.base_offset = cursor

        # Record the start of the entire block if needed
        self.base_offset = cursor
        cursor = match.end()

        # 1. Hero ID Section (Fixed 10 heroes)
        last_hero_type = None
        for _ in range(10):
            # Record hero-level offsets
            h_start = cursor
            hero_type, cur_idx = struct.unpack_from("<BB", globals.data, cursor)
            hero_type = int(hero_type)
            cur_idx = int(cur_idx)

            hero_offsets = {
                "base": h_start,
                "cur_preset_idx": h_start + 1,
                "cur_vessel_id": h_start + 4
            }
            cursor += 4  # Skip ID, Idx, Padding

            cur_v_id = struct.unpack_from("<I", globals.data, cursor)[0]
            cursor += 4

            universal_vessels = []
            for _ in range(4):
                v_start = cursor
                v_id = struct.unpack_from("<I", globals.data, cursor)[0]
                cursor += 4
                relics = list(struct.unpack_from("<6I", globals.data, cursor))
                for r in relics:
                    if (r & 0xF0000000) == self.ITEM_TYPE_RELIC and r != 0:
                        if r not in self.relic_ga_hero_map:
                            self.relic_ga_hero_map[r] = set()
                        self.relic_ga_hero_map[r].add(hero_type)
                universal_vessels.append({
                    "vessel_id": v_id,
                    "relics": relics,
                    "offsets": {
                        "vessel_id": v_start,
                        "relics": v_start + 4
                    }
                })
                cursor += 24

            heroes[hero_type] = HeroLoadout(hero_type, cur_idx, cur_v_id, universal_vessels, hero_offsets)
            last_hero_type = hero_type

        # 2. Hero Vessels
        while cursor < len(globals.data):
            v_start = cursor
            v_id = struct.unpack_from("<I", globals.data, cursor)[0]
            if v_id == 0:
                cursor += 4
                break

            cursor += 4
            relics = list(struct.unpack_from("<6I", globals.data, cursor))

            v_meta = self.game_data.get_vessel_data(v_id)
            target_hero = v_meta.get("hero_type") if v_meta else None
            assigned_id = last_hero_type if target_hero == 11 else target_hero

            for r in relics:
                if (r & 0xF0000000) == self.ITEM_TYPE_RELIC and r != 0:
                    if r not in self.relic_ga_hero_map:
                        self.relic_ga_hero_map[r] = set()
                    self.relic_ga_hero_map[r].add(assigned_id)

            if assigned_id in heroes:
                heroes[assigned_id].vessels.append({
                    "vessel_id": v_id,
                    "relics": relics,
                    "offsets": {
                        "vessel_id": v_start,
                        "relics": v_start + 4
                    }
                })
            cursor += 24
        # Sort hero loadout vessels by vessel id
        for h_type in heroes:
            heroes[h_type].vessels.sort(key=lambda x: x["vessel_id"])

        # 3. Custom Presets Section
        preset_index = 0
        while cursor < len(globals.data):
            p_start = cursor
            header = struct.unpack_from("<B", globals.data, cursor)[0]
            if header != 0x01:
                break

            # Offsets for custom preset fields
            p_offsets = {
                "base": p_start,
                "hero_type": p_start + 1,
                "counter": p_start + 3,
                "name": p_start + 4,
                "vessel_id": p_start + 44,  # 4 + 36 + 4 padding
                "relics": p_start + 48,
                "timestamp": p_start + 72  # not sure
            }

            cursor += 1
            h_id = int(struct.unpack_from("<H", globals.data, cursor)[0])
            cursor += 2
            counter_val = struct.unpack_from("<B", globals.data, cursor)[0]
            cursor += 1

            name = globals.data[cursor:cursor + 36].decode('utf-16', errors='ignore').strip('\x00')
            cursor += 36 + 4  # Name + Padding

            v_id = struct.unpack_from("<I", globals.data, cursor)[0]
            cursor += 4

            relics = list(struct.unpack_from("<6I", globals.data, cursor))
            cursor += 24  # Relics
            for r in relics:
                if (r & 0xF0000000) == self.ITEM_TYPE_RELIC and r != 0:
                    if r not in self.relic_ga_hero_map:
                        self.relic_ga_hero_map[r] = set()
                    self.relic_ga_hero_map[r].add(h_id)

            timestamp = struct.unpack_from("<Q", globals.data, cursor)[0]  # not sure
            cursor += 8

            if h_id in heroes:
                heroes[h_id].add_preset(h_id, preset_index, name, v_id, relics, p_offsets, counter_val, timestamp)

            preset_index += 1

            if counter_val == 0:
                break
        self.heroes = heroes

    def display_results(self):
        """
        Terminal output with formatted offsets (06X), hero_type (int), and relics (08X).
        """
        print(f"\n{'='*80}")
        print(f"{'Vessel Parser Results':^80}")
        print(f"{'='*80}")

        # Sort by hero_type for a cleaner list
        for h_id in sorted(self.heroes.keys()):
            loadout = self.heroes[h_id]
            h_off = loadout.offsets

            print(f"\n[Hero ID: {h_id}]")
            print(f"  - Base Offset: 0x{h_off['base']:06X}")
            print(f"  - Current Preset Index: {loadout.cur_preset_idx if loadout.cur_preset_idx != 255 else 'None'} (At: 0x{h_off['cur_preset_idx']:06X})")
            print(f"  - Current Vessel ID: {loadout.cur_vessel_id} (At: 0x{h_off['cur_vessel_id']:06X})")

            # Vessels Section
            print(f"  - Vessels ({len(loadout.vessels)} total):")
            for i, v in enumerate(loadout.vessels):
                v_off = v['offsets']
                relics_str = ", ".join([f"0x{r:08X}" for r in v['relics']])
                print(f"    [{i:02d}] ID: {v['vessel_id']} (At: 0x{v_off['vessel_id']:06X})")
                print(f"         Relics: [{relics_str}] (At: 0x{v_off['relics']:06X})")

            # Custom Presets Section
            if loadout.presets:
                print(f"  - Custom Presets ({len(loadout.presets)} total):")
                for p in loadout.presets:
                    p_off = p['offsets']
                    relics_str = ", ".join([f"0x{r:08X}" for r in p['relics']])
                    print(f"    * Name: {p['name']:<18} (At: 0x{p_off['name']:06X})")
                    print(f"      Index: {p['index']:<2}")
                    print(f"      Counter: {p.get('counter', 'N/A'):>2}      (At: 0x{p_off['counter']:06X})")
                    print(f"      Vessel ID: {p['vessel_id']:<8} (At: 0x{p_off['vessel_id']:06X})")
                    print(f"      Relics: [{relics_str}] (At: 0x{p_off['relics']:06X})")
                    print(f"      Timestamp: {p.get('timestamp', 'N/A')} (At: 0x{p_off['timestamp']:06X})")
            else:
                print("  - No Custom Presets found.")

        # print ga_hero_type_map
        print(f"\n{'='*80}")
        print(f"{'Relic GA Handle to Hero Type Map':^80}")
        print(f"{'='*80}")
        for r_ga in sorted(self.relic_ga_hero_map.keys()):
            heroes = self.relic_ga_hero_map[r_ga]
            heroes_str = ", ".join([str(h) for h in heroes])
            print(f"0x{r_ga:08X}: [{heroes_str}]")

        print(f"\n{'='*80}")


class VesselModifier:
    def __init__(self):
        """
        Initialize the modifier with binary data.
        :param data: The original binary data from the save file.
        """
        pass

    def update_hero_loadout(self, hero_loadout: HeroLoadout):
        """
        Update all fields of a specific hero loadout based on its offsets.
        """
        # 1. Update Hero-level fields
        struct.pack_into("<B", globals.data, hero_loadout.offsets["cur_preset_idx"], hero_loadout.cur_preset_idx)
        struct.pack_into("<I", globals.data, hero_loadout.offsets["cur_vessel_id"], hero_loadout.cur_vessel_id)

        # 2. Update Vessels (including Global sequences assigned to this hero)
        for v in hero_loadout.vessels:
            struct.pack_into("<I", globals.data, v["offsets"]["vessel_id"], v["vessel_id"])
            struct.pack_into("<6I", globals.data, v["offsets"]["relics"], *v["relics"])

        # 3. Update Custom Presets
        for p in hero_loadout.presets:
            p_off = p["offsets"]
            # Make sure header is 0x01 and hero_type is correct
            struct.pack_into("<B", globals.data, p_off["base"], 0x01)
            struct.pack_into("<H", globals.data, p_off["hero_type"], p["hero_type"])
            # Update counter
            struct.pack_into("<B", globals.data, p_off["counter"], p["counter"])

            # Update Vessel ID and Relics in preset
            struct.pack_into("<I", globals.data, p_off["vessel_id"], p["vessel_id"])
            struct.pack_into("<6I", globals.data, p_off["relics"], *p["relics"])

            # Update Name (if modified, ensuring it's 36 bytes UTF-16)
            name_bytes = p["name"].encode('utf-16le').ljust(36, b'\x00')[:36]
            globals.data[p_off["name"]:p_off["name"] + 36] = name_bytes

            # Update Timestamp
            struct.pack_into("<Q", globals.data, p_off["timestamp"], p["timestamp"])

    def update_all_loadouts(self, heroes: dict):
        """
        Update all hero loadouts.
        """
        for hero_loadout in heroes.values():
            self.update_hero_loadout(hero_loadout)

    def set_value(self, offset: int, fmt: str, value):
        """
        Generic method to set a value at a specific offset.
        :param fmt: struct format string (e.g., '<I', '<B')
        """
        struct.pack_into(fmt, globals.data, offset, value)


class Validator:
    def __init__(self, ga_relics: list[tuple], game_data: SourceDataHandler):
        self.cur_relics = {}
        for r in ga_relics:
            self.cur_relics[r[0]] = r
        self.game_data = game_data

    def reload_ga_relics(self, ga_relics: list[tuple]):
        self.cur_relics = {}
        for r in ga_relics:
            self.cur_relics[r[0]] = r

    def check_hero(self, heroes: dict[int, HeroLoadout], hero_type: int):
        if not 1 <= hero_type <= 10:
            raise ValueError("Invalid hero type")
        if hero_type not in heroes:
            raise BufferError("Hero not found. The Hero Loadout Structure may be corrupted.")
        return True

    def check_vessel_assignment(self, heroes: dict[int, HeroLoadout], hero_type: int, vessel_id: int):
        if self.check_hero(heroes, hero_type):
            _vessel_info = self.game_data.get_vessel_data(vessel_id)
            if not _vessel_info:
                raise ImportError("Can't find vessel data.")

            if _vessel_info["hero_type"] != 11 and _vessel_info["hero_type"] != hero_type:
                raise ValueError("This vessel is not assigned to this hero")
            else:
                if vessel_id not in [v["vessel_id"] for v in heroes[hero_type].vessels]:
                    raise BufferError("Vessel should be assigned to this hero but not found. The Hero Loadout Structure may be corrupted.")

            return True
        return False

    def validate_vessel(self, heroes: dict[int, HeroLoadout], hero_type: int, vessel:dict):
        # Check is vessel assigned to correct hero
        if self.check_vessel_assignment(heroes, hero_type, vessel["vessel_id"]):
            _vessel_info = self.game_data.get_vessel_data(vessel["vessel_id"])
            # Check whether the relic in each relic slot is valid.
            for relic_index, relic in enumerate(vessel["relics"]):
                if relic == 0:
                    # Empty always Valid
                    continue
                ga_relic: tuple = self.cur_relics.get(relic)
                if not ga_relic:
                    # Can't find relic in inventory
                    raise LookupError("Relic not found in current relics Inventory.")
                type_bits = ga_relic[0] & 0xF0000000
                if type_bits == ITEM_TYPE_RELIC:
                    real_id = ga_relic[1] - 2147483648
                    # Check relic type match
                    is_deep_relic = RelicChecker.is_deep_relic(real_id)
                    if relic_index < 3 and is_deep_relic:
                        # relic type mismatch
                        raise ValueError(f"Found deep slot with normal relic. Slot:{relic_index+1}")
                    if relic_index >= 3 and not is_deep_relic:
                        # relic type mismatch
                        raise ValueError(f"Found normal slot with deep relic. Slot:{relic_index+1}")
                    # Check color match
                    slot_color = _vessel_info['Colors'][relic_index]
                    new_relic_color = self.game_data.get_relic_color(real_id)
                    # new_relic_color = self.game_data.get_relic_origin_structure()[str(real_id)]["color"]
                    if slot_color != new_relic_color and slot_color != COLOR_MAP[4]:
                        # Color mismatch
                        raise ValueError(f"Color mismatch in relic slot {relic_index+1}.")
                    # Check duplicate relics in vessel
                    if 0 <= relic_index < 2:
                        for idx, relic_after in enumerate(vessel["relics"][relic_index + 1:3]):
                            r_af_idx = relic_index + 1 + idx
                            if relic_after != 0 and relic == relic_after:
                                raise ValueError(f"Relic is duplicated with slot: {r_af_idx+1}")
                    if 3 <= relic_index < 5:
                        for idx, relic_after in enumerate(vessel["relics"][relic_index + 1:]):
                            r_af_idx = relic_index + 1 + idx
                            if relic_after != 0 and relic == relic_after:
                                raise ValueError(f"Relic is duplicated with slot: {r_af_idx+1}")  
                else:
                    raise ValueError("Invalid item type")
        return True

    def auto_adjust_cur_equipment(self, heroes: dict[int, HeroLoadout], hero_type: int):
        """
        Automatically adjust the current preset index based on the current vessel's relics.

        Args:
            heroes (dict[int, HeroLoadout]): heroes loadout that needs to be adjusted
            hero_type (int): Specific hero type to adjust
        """
        cur_vessel_id = heroes[hero_type].cur_vessel_id
        cur_vessel = None
        for v in heroes[hero_type].vessels:
            if v["vessel_id"] == cur_vessel_id:
                cur_vessel = v
                break
        _new_preset_idx = 0xFF
        for preset in heroes[hero_type].presets:
            if preset["vessel_id"] == cur_vessel_id and preset['relics'] == cur_vessel["relics"]:
                _new_preset_idx = preset["index"]
                break
        heroes[hero_type].cur_preset_idx = _new_preset_idx

    def heroes_structure_check(self, heroes: dict[int, HeroLoadout]):
        pass

    def validate_preset(self, heroes: dict[int, HeroLoadout], hero_type: int, new_preset: dict):
        _t_vessel = {"vessel_id": new_preset["vessel_id"], "relics": new_preset['relics']}
        _relics_set = set(_t_vessel["relics"])
        _relics_set.discard(0)
        if len(_relics_set) == 0:
            raise ValueError("Preset must contain at least one relic.")
        self.validate_vessel(heroes, hero_type, _t_vessel)
        for preset in heroes[hero_type].presets:
            if preset["index"] == new_preset["index"]:
                raise ValueError("Preset index duplicated. This shouldn't happen.")
            if preset["vessel_id"] == new_preset["vessel_id"] and preset["relics"] == new_preset["relics"]:
                raise ValueError(f"Preset relics combination exists. Preset Name: {preset['name']}")


class LoadoutHandler:
    """
    Hero Loadout Handler
    Manage hero loadouts, including parsing, modifying, validating, and equipping presets.
    """
    class PresetsCapacityFullError(Exception):
        pass

    def __init__(self, game_data: SourceDataHandler, ga_relics: list[tuple]):
        self.parser = VesselParser(game_data)
        self.modifier = VesselModifier()
        self.validator = Validator(ga_relics, game_data)
        self.game_data = game_data
        self.all_presets = []

    @property
    def heroes(self):
        return self.parser.heroes

    @property
    def relic_ga_hero_map(self):
        return self.parser.relic_ga_hero_map

    def get_vessel_index_in_hero(self, hero_type: int, vessel_id: int):
        if self.check_vessel(hero_type, vessel_id):
            for index, vessel in enumerate(self.heroes[hero_type].vessels):
                if vessel["vessel_id"] == vessel_id:
                    return index
        return -1

    def parse(self):
        self.parser.parse()
        self.all_presets = [p for h in self.heroes.values() for p in h.presets]
        self.all_presets.sort(key=lambda x: x["index"])

    def display_results(self):
        self.parser.display_results()

    def update_hero_loadout(self, hero_index: int):
        self.modifier.update_hero_loadout(self.heroes[hero_index])

    def update_all_loadouts(self):
        self.modifier.update_all_loadouts(self.heroes)

    def reload_ga_relics(self, ga_relics: list[tuple]):
        self.validator.reload_ga_relics(ga_relics)

    def check_hero(self, hero_type: int):
        return hero_type in self.heroes

    def check_vessel(self, hero_type: int, vessel_id: int):
        if not self.check_hero(hero_type):
            raise ValueError("Hero not found")
        return any(v["vessel_id"] == vessel_id for v in self.heroes[hero_type].vessels)

    def get_vessel_id(self, hero_type: int, vessel_index: int):
        if 0 <= vessel_index < len(self.heroes[hero_type].vessels):
            return self.heroes[hero_type].vessels[vessel_index]["vessel_id"]
        else:
            raise ValueError("Invalid vessel index")

    def get_relic_ga_handle(self, hero_type: int, vessel_id: int, relic_index: int):
        if not self.check_vessel(hero_type, vessel_id):
            raise ValueError("Vessel not found")
        if 0 <= relic_index <= 5:
            for v in self.heroes[hero_type].vessels:
                if v["vessel_id"] == vessel_id:
                    return v["relics"][relic_index]
        else:
            raise ValueError("Invalid relic index")

    def equip_preset(self, hero_type: int, preset_index: int):
        if hero_type not in range(1, 11):
            raise ValueError("Invalid hero type")
        if hero_type not in self.heroes:
            raise ValueError("Hero not found. This shouldn't happen. Save file may be corrupted.")
        if preset_index < len(self.all_presets):
            self.heroes[hero_type].cur_preset_idx = preset_index
            self.heroes[hero_type].cur_vessel_id = self.all_presets[preset_index]["vessel_id"]
            for vessel in self.heroes[hero_type].vessels:
                if vessel["vessel_id"] == self.heroes[hero_type].cur_vessel_id:
                    vessel["relics"] = deepcopy(self.all_presets[preset_index]["relics"])
                    break
            self.update_hero_loadout(hero_type)
        else:
            raise ValueError("Invalid preset index")

    def push_preset(self, hero_type: int, vessel_id: int, relics: list[int], name: str):
        """
        Append a new preset to the specified hero's loadout.
        
        :param hero_type: 1-based\n
            sequence: 1~10 for normal heroes, 11 for universal vessels\n
            ['Wylder', 'Guardian', 'Ironeye', 'Duchess', 'Raider',\n
             'Revenant', 'Recluse', 'Executor', 'Scholar', 'Undertaker', 'All']
        :type hero_type: int
        :param vessel_id: vessel ID Like 19001 etc.
        :type vessel_id: int
        :param relics: ga_handles
        :type relics: list[int]
        :param name: Perset Name, Max Chars 18
        :type name: str

        :returns: return the modified data as immutable bytes.
        :rtype: bytes
        """
        # Check Preset Capacity
        if len(self.all_presets) > 100:
            raise LoadoutHandler.PresetsCapacityFullError("Maximum preset capacity reached.")

        # Check Relics Validity

        # All Valid
        # Create a new preset
        # new preset offsets are caculated by last preset
        new_preset_offsets = {
            "base": self.all_presets[-1]["offsets"]["base"] + 80 if self.all_presets else self.parser.base_offset + 120 * 10 + 28 * 70 + 4,  # Heuristic: 10 heroes * 120 bytes + 60 vessels * 28 bytes + 4 bytes padding
            "hero_type": self.all_presets[-1]["offsets"]["base"] + 80 + 1 if self.all_presets else self.parser.base_offset + 120 * 10 + 28 * 70 + 4 + 1,
            "counter": self.all_presets[-1]["offsets"]["base"] + 80 + 3 if self.all_presets else self.parser.base_offset + 120 * 10 + 28 * 70 + 4 + 3,
            "name": self.all_presets[-1]["offsets"]["base"] + 80 + 4 if self.all_presets else self.parser.base_offset + 120 * 10 + 28 * 70 + 4 + 4,
            "vessel_id": self.all_presets[-1]["offsets"]["base"] + 80 + 44 if self.all_presets else self.parser.base_offset + 120 * 10 + 28 * 70 + 4 + 44,
            "relics": self.all_presets[-1]["offsets"]["base"] + 80 + 48 if self.all_presets else self.parser.base_offset + 120 * 10 + 28 * 70 + 4 + 48,
            "timestamp": self.all_presets[-1]["offsets"]["base"] + 80 + 72 if self.all_presets else self.parser.base_offset + 120 * 10 + 28 * 70 + 4 + 72,
        }

        new_timestamp = get_now_timestamp()

        new_preset = {
            "hero_type": hero_type,
            "index": len(self.all_presets),
            "name": name,
            "vessel_id": vessel_id,
            "relics": relics,
            "counter": 0,
            "timestamp": new_timestamp,
            "offsets": new_preset_offsets
        }
        # Check preset, if invalid will raise Exception
        self.validator.validate_preset(self.heroes, hero_type, new_preset)
        for hero in self.heroes.values():
            for preset in hero.presets:
                preset["counter"] += 1
        self.heroes[hero_type].add_preset(**new_preset)
        self.all_presets = [p for h in self.heroes.values() for p in h.presets]
        self.all_presets.sort(key=lambda x: x["index"])
        self.validator.auto_adjust_cur_equipment(self.heroes, hero_type)
        self.update_all_loadouts()

    def replace_vessel_relic(self, hero_type: int, vessel_id: int,
                             relic_index: int, new_relic_ga):
        _new_vessel = None
        vessel_index = 0
        for idx, vessel in enumerate(self.heroes[hero_type].vessels):
            if vessel["vessel_id"] == vessel_id:
                _new_vessel = deepcopy(vessel)
                vessel_index = idx
                break
        if not _new_vessel:
            raise ValueError("Vessel not found")
        _new_vessel["relics"][relic_index] = new_relic_ga
        if self.validator.validate_vessel(self.heroes, hero_type, _new_vessel):
            self.heroes[hero_type].vessels[vessel_index] = _new_vessel
            if self.heroes[hero_type].cur_vessel_id == vessel_id:
                self.validator.auto_adjust_cur_equipment(self.heroes, hero_type)
            self.update_hero_loadout(hero_type)

    def replace_preset_relic(self, hero_type: int, relic_index: int, new_relic_ga,
                             hero_preset_index: int = -1, preset_index: int = -1):
        if hero_preset_index < 0 and preset_index < 0:
            raise ValueError("hero_preset_index or preset_index should be provided")
        if hero_preset_index >= 0 and preset_index >= 0:
            raise ValueError("Only one of hero_preset_index or preset_index should be provided")
        if hero_preset_index >= len(self.heroes[hero_type].presets):
            raise ValueError("Invalid preset index")
        if preset_index >= len(self.all_presets):
            raise ValueError("Invalid preset index")
        if relic_index < 0 or relic_index >= 6:
            raise ValueError("Invalid relic index")

        _new_preset = None
        if preset_index >= 0:
            _new_preset = deepcopy(self.all_presets[preset_index])
        elif hero_preset_index >= 0:
            _new_preset = deepcopy(self.heroes[hero_type].presets[hero_preset_index])
        _new_preset["relics"][relic_index] = new_relic_ga
        _t_vessel = {"vessel_id": _new_preset["vessel_id"], "relics": _new_preset['relics']}
        self.validator.validate_vessel(self.heroes, hero_type, _t_vessel)
        if preset_index >= 0:
            self.all_presets[preset_index]['relics'][relic_index] = new_relic_ga
        elif hero_preset_index >= 0:
            self.heroes[hero_type].presets[hero_preset_index]['relics'][relic_index] = new_relic_ga
        self.validator.auto_adjust_cur_equipment(self.heroes, hero_type)
        self.update_hero_loadout(hero_type)
