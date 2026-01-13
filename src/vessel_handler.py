import struct
import re
from source_data_handler import SourceDataHandler
from relic_checker import RelicChecker
import time


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

    def add_preset(self, index, name, vessel_id, relics, offsets, counter, timestamp):
        self.presets.append({
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

    def __init__(self, user_data: bytes, data_handler: SourceDataHandler):
        self.user_data = user_data
        self.game_data = data_handler
        self.heroes: dict[int, HeroLoadout] = {}
        self.relic_ga_hero_map = {}
        self.base_offset = None

    def parse(self):
        heroes = {}
        magic_pattern = re.escape(bytes.fromhex("C2000300002C000003000A0004004600"))
        marker = re.escape(bytes.fromhex("64000000"))

        match = re.search(magic_pattern + marker, self.user_data)
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
            hero_type, cur_idx = struct.unpack_from("<BB", self.user_data, cursor)
            hero_type = int(hero_type)
            cur_idx = int(cur_idx)

            hero_offsets = {
                "base": h_start,
                "cur_preset_idx": h_start + 1,
                "cur_vessel_id": h_start + 4
            }
            cursor += 4  # Skip ID, Idx, Padding

            cur_v_id = struct.unpack_from("<I", self.user_data, cursor)[0]
            cursor += 4

            universal_vessels = []
            for _ in range(4):
                v_start = cursor
                v_id = struct.unpack_from("<I", self.user_data, cursor)[0]
                cursor += 4
                relics = struct.unpack_from("<6I", self.user_data, cursor)
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
        while cursor < len(self.user_data):
            v_start = cursor
            v_id = struct.unpack_from("<I", self.user_data, cursor)[0]
            if v_id == 0:
                cursor += 4
                break

            cursor += 4
            relics = struct.unpack_from("<6I", self.user_data, cursor)
            for r in relics:
                if (r & 0xF0000000) == self.ITEM_TYPE_RELIC and r != 0:
                    if r not in self.relic_ga_hero_map:
                        self.relic_ga_hero_map[r] = set()
                    self.relic_ga_hero_map[r].add(hero_type)

            v_meta = self.game_data.get_vessel_data(v_id)
            target_hero = v_meta.get("hero_type") if v_meta else None
            assigned_id = last_hero_type if target_hero == 11 else target_hero

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
        while cursor < len(self.user_data):
            p_start = cursor
            header = struct.unpack_from("<B", self.user_data, cursor)[0]
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
            h_id = struct.unpack_from("<H", self.user_data, cursor)[0]
            cursor += 2
            counter_val = struct.unpack_from("<B", self.user_data, cursor)[0]
            cursor += 1

            name = self.user_data[cursor:cursor + 36].decode('utf-16', errors='ignore').strip('\x00')
            cursor += 36 + 4  # Name + Padding

            v_id = struct.unpack_from("<I", self.user_data, cursor)[0]
            cursor += 4

            relics = list(struct.unpack_from("<6I", self.user_data, cursor))
            cursor += 24  # Relics
            for r in relics:
                if (r & 0xF0000000) == self.ITEM_TYPE_RELIC and r != 0:
                    if r not in self.relic_ga_hero_map:
                        self.relic_ga_hero_map[r] = set()
                    self.relic_ga_hero_map[r].add(h_id)

            timestamp = struct.unpack_from("<Q", self.user_data, cursor)[0]  # not sure
            cursor += 8

            if h_id in heroes:
                heroes[h_id].add_preset(preset_index, name, v_id, relics, p_offsets, counter_val, timestamp)

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

        print(f"\n{'='*80}")


class VesselModifier:
    def __init__(self, user_data: bytes):
        """
        Initialize the modifier with binary data.
        :param data: The original binary data from the save file.
        """
        self.user_data = bytearray(user_data)

    def update_hero_loadout(self, hero_loadout: HeroLoadout):
        """
        Update all fields of a specific hero loadout based on its offsets.
        """
        # 1. Update Hero-level fields
        struct.pack_into("<B", self.user_data, hero_loadout.offsets["cur_preset_idx"], hero_loadout.cur_preset_idx)
        struct.pack_into("<I", self.user_data, hero_loadout.offsets["cur_vessel_id"], hero_loadout.cur_vessel_id)

        # 2. Update Vessels (including Global sequences assigned to this hero)
        for v in hero_loadout.vessels:
            struct.pack_into("<I", self.user_data, v["offsets"]["vessel_id"], v["vessel_id"])
            struct.pack_into("<6I", self.user_data, v["offsets"]["relics"], *v["relics"])

        # 3. Update Custom Presets
        for p in hero_loadout.presets:
            p_off = p["offsets"]
            # Update counter
            struct.pack_into("<B", self.user_data, p_off["counter"], p["counter"])

            # Update Vessel ID and Relics in preset
            struct.pack_into("<I", self.user_data, p_off["vessel_id"], p["vessel_id"])
            struct.pack_into("<6I", self.user_data, p_off["relics"], *p["relics"])

            # Update Name (if modified, ensuring it's 36 bytes UTF-16)
            name_bytes = p["name"].encode('utf-16le').ljust(36, b'\x00')[:36]
            self.user_data[p_off["name"]:p_off["name"] + 36] = name_bytes

            # Update Timestamp
            struct.pack_into("<Q", self.user_data, p_off["timestamp"], p["timestamp"])

    def set_value(self, offset: int, fmt: str, value):
        """
        Generic method to set a value at a specific offset.
        :param fmt: struct format string (e.g., '<I', '<B')
        """
        struct.pack_into(fmt, self.user_data, offset, value)

    def get_updated_data(self) -> bytes:
        """
        Return the modified data as immutable bytes.
        """
        return bytes(self.user_data)


class LoadoutHandler:
    class PresetsCapacityFullError(Exception):
        pass

    def __init__(self, data: bytes, data_handler: SourceDataHandler):
        self.parser = VesselParser(data, data_handler)
        self.modifier = VesselModifier(data)
        self.data_handler = data_handler
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

    def display_results(self):
        self.parser.display_results()

    def update_hero_loadout(self, hero_index: int):
        self.modifier.update_hero_loadout(self.heroes[hero_index])

    def get_modified_data(self) -> bytes:
        return self.modifier.get_updated_data()

    def reload_data(self, data: bytes, loadout_edited: bool = True):
        self.modifier.user_data = bytearray(data)
        self.parser.user_data = bytearray(data)
        if loadout_edited:
            self.parse()

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
        _vessel_info = self.data_handler.get_vessel_data(vessel_id)
        if not _vessel_info:
            return

        if _vessel_info["hero_type"] != 11 and _vessel_info["hero_type"] != hero_type:
            raise ValueError("This vessel is not assigned to this hero")

        if len(self.all_presets) > 100:
            raise LoadoutHandler.PresetsCapacityFullError("Maximum preset capacity reached.")

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
            "index": len(self.all_presets),
            "name": name,
            "vessel_id": vessel_id,
            "relics": relics,
            "counter": 0,
            "timestamp": new_timestamp,
            "offsets": new_preset_offsets
        }
        for preset in self.all_presets:
            preset["counter"] += 1
        self.heroes[hero_type].add_preset(**new_preset)
        self.all_presets = [p for h in self.heroes.values() for p in h.presets]
        self.update_hero_loadout(hero_type)
        return self.modifier.get_updated_data()

    def replace_vessel_relic(self, hero_type: int, vessel_id: int,
                             relic_index: int, new_relic_item):
        _vessel_info = self.data_handler.get_vessel_data(vessel_id)
        if not _vessel_info:
            return
        
        if not (0 <= relic_index <= 5):
            raise ValueError("Invalid relic index")

        if _vessel_info["hero_type"] != 11 and _vessel_info["hero_type"] != hero_type:
            raise ValueError("This vessel is not assigned to this hero")

        if new_relic_item:
            type_bits = new_relic_item.gaitem_handle & 0xF0000000
            if type_bits == self.ITEM_TYPE_RELIC:
                real_id = new_relic_item.item_id - 2147483648
                is_deep_relic = RelicChecker.is_deep_relic(real_id)
                if relic_index < 3 and is_deep_relic:
                    raise ValueError("Cannot replace normal slot with deep relic")
                if relic_index >= 3 and not is_deep_relic:
                    raise ValueError("Cannot replace deep slot with normal relic")
                slot_color = _vessel_info['Colors'][relic_index]
                new_relic_color = self.data_handler.get_relic_origin_structure()[str(real_id)]["color"]
                if slot_color != new_relic_color:
                    raise ValueError("Color mismatch")

                # Valid
                for vessel in self.heroes[hero_type].vessels:
                    if vessel["vessel_id"] == vessel_id:
                        vessel["relics"][relic_index] = new_relic_item.gaitem_handle
                        self.update_hero_loadout(self.heroes[hero_type])
                        return self.get_modified_data()
            else:
                raise ValueError("Invalid item type")
        else:
            # Valid
            for vessel in self.heroes[hero_type].vessels:
                if vessel["vessel_id"] == vessel_id:
                    vessel["relics"][relic_index] = 0
                    self.update_hero_loadout(self.heroes[hero_type])
                    return self.get_modified_data()
