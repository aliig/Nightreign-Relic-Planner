import struct
import re
from source_data_handler import SourceDataHandler


class HeroLoadout:
    def __init__(self, hero_id, cur_preset_idx, cur_vessel_id, vessels, offsets):
        self.hero_id = hero_id
        self.cur_preset_idx = cur_preset_idx
        self.cur_vessel_id = cur_vessel_id
        self.vessels = vessels
        self.presets = []
        # Stores offsets for hero-level fields
        self.offsets = offsets

    def add_preset(self, idx, name, vessel_id, relics, offset_dict, counter_val, timestamp):
        self.presets.append({
            "index": idx,
            "name": name,
            "vessel_id": vessel_id,
            "relics": relics,
            "offsets": offset_dict,
            "counter": counter_val,
            "timestamp": timestamp
        })


class VesselParser:
    def __init__(self, data: bytes, data_handler: SourceDataHandler):
        self.data = data
        self.handler = data_handler
        self.heroes = {} 

    def parse(self):
        magic_pattern = re.escape(bytes.fromhex("C2000300002C000003000A0004004600"))
        marker = re.escape(bytes.fromhex("64000000"))
        
        match = re.search(magic_pattern + marker, self.data)
        if not match:
            print("[Error] Magic pattern not found.")
            return

        cursor = match.start()
        # Record the start of the entire block if needed
        self.base_offset = cursor
        cursor = match.end()

        # 1. Hero ID Section (Fixed 10 heroes)
        last_hero_id = None
        for _ in range(10):
            # Record hero-level offsets
            h_start = cursor
            hero_id, cur_idx = struct.unpack_from("<BB", self.data, cursor)
            
            hero_offsets = {
                "base": h_start,
                "cur_preset_idx": h_start + 1,
                "cur_vessel_id": h_start + 4
            }
            cursor += 4 # Skip ID, Idx, Padding
            
            cur_v_id = struct.unpack_from("<I", self.data, cursor)[0]
            cursor += 4
            
            universal_vessels = []
            for _ in range(4):
                v_start = cursor
                v_id = struct.unpack_from("<I", self.data, cursor)[0]
                cursor += 4
                relics = struct.unpack_from("<6I", self.data, cursor)
                
                universal_vessels.append({
                    "vessel_id": v_id,
                    "relics": relics,
                    "offsets": {
                        "vessel_id": v_start,
                        "relics": v_start + 4
                    }
                })
                cursor += 24
            
            self.heroes[hero_id] = HeroLoadout(hero_id, cur_idx, cur_v_id, universal_vessels, hero_offsets)
            last_hero_id = hero_id

        # 2. Hero Vessels
        while cursor < len(self.data):
            v_start = cursor
            v_id = struct.unpack_from("<I", self.data, cursor)[0]
            if v_id == 0: 
                cursor += 4
                break
            
            cursor += 4
            relics = struct.unpack_from("<6I", self.data, cursor)
            
            v_meta = self.handler.get_vessel_data(v_id)
            target_hero = v_meta.get("hero_type") if v_meta else None
            assigned_id = last_hero_id if target_hero == 11 else target_hero
            
            if assigned_id in self.heroes:
                self.heroes[assigned_id].vessels.append({
                    "vessel_id": v_id,
                    "relics": relics,
                    "offsets": {
                        "vessel_id": v_start,
                        "relics": v_start + 4
                    }
                })
            cursor += 24
        # Sort hero loadout vessels by vessel id
        for h_id in self.heroes:
            self.heroes[h_id].vessels.sort(key=lambda x: x["vessel_id"])

        # 3. Custom Presets Section
        preset_index = 0
        while cursor < len(self.data):
            p_start = cursor
            header = struct.unpack_from("<B", self.data, cursor)[0]
            if header != 0x01:
                break
            
            # Offsets for custom preset fields
            p_offsets = {
                "base": p_start,
                "hero_id": p_start + 1,
                "counter": p_start + 3,
                "name": p_start + 4,
                "vessel_id": p_start + 44,  # 4 + 36 + 4 padding
                "relics": p_start + 48,
                "timestamp": p_start + 72  # not sure
            }
            
            cursor += 1
            h_id = struct.unpack_from("<H", self.data, cursor)[0]
            cursor += 2
            counter_val = struct.unpack_from("<B", self.data, cursor)[0]
            cursor += 1
            
            name = self.data[cursor:cursor + 36].decode('utf-16', errors='ignore').strip('\x00')
            cursor += 36 + 4 # Name + Padding
            
            v_id = struct.unpack_from("<I", self.data, cursor)[0]
            cursor += 4
            
            relics = struct.unpack_from("<6I", self.data, cursor)
            cursor += 24  # Relics
            
            timestamp = struct.unpack_from("<Q", self.data, cursor)[0]  # not sure
            cursor += 8
            
            if h_id in self.heroes:
                self.heroes[h_id].add_preset(preset_index, name, v_id, relics, p_offsets, counter_val, timestamp)
                
            preset_index += 1
            
            if counter_val == 0:
                break

    def display_results(self):
        """
        Terminal output with formatted offsets (06X), hero_id (int), and relics (08X).
        """
        print(f"\n{'='*80}")
        print(f"{'Vessel Parser Results':^80}")
        print(f"{'='*80}")

        # Sort by hero_id for a cleaner list
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
    def __init__(self, data: bytes):
        """
        Initialize the modifier with binary data.
        :param data: The original binary data from the save file.
        """
        self.data = bytearray(data)

    def update_hero_loadout(self, hero_loadout: HeroLoadout):
        """
        Update all fields of a specific hero loadout based on its offsets.
        """
        # 1. Update Hero-level fields
        struct.pack_into("<B", self.data, hero_loadout.offsets["cur_preset_idx"], hero_loadout.cur_preset_idx)
        struct.pack_into("<I", self.data, hero_loadout.offsets["cur_vessel_id"], hero_loadout.cur_vessel_id)

        # 2. Update Vessels (including Global sequences assigned to this hero)
        for v in hero_loadout.vessels:
            struct.pack_into("<I", self.data, v["offsets"]["vessel_id"], v["vessel_id"])
            struct.pack_into("<6I", self.data, v["offsets"]["relics"], *v["relics"])

        # 3. Update Custom Presets
        for p in hero_loadout.presets:
            p_off = p["offsets"]
            # Update counter
            struct.pack_into("<B", self.data, p_off["counter"], p["counter"])
            
            # Update Vessel ID and Relics in preset
            struct.pack_into("<I", self.data, p_off["vessel_id"], p["vessel_id"])
            struct.pack_into("<6I", self.data, p_off["relics"], *p["relics"])
            
            # Update Name (if modified, ensuring it's 36 bytes UTF-16)
            name_bytes = p["name"].encode('utf-16le').ljust(36, b'\x00')[:36]
            self.data[p_off["name"]:p_off["name"] + 36] = name_bytes
            
            # Update Timestamp
            struct.pack_into("<Q", self.data, p_off["timestamp"], p["timestamp"])

    def set_value(self, offset: int, fmt: str, value):
        """
        Generic method to set a value at a specific offset.
        :param fmt: struct format string (e.g., '<I', '<B')
        """
        struct.pack_into(fmt, self.data, offset, value)

    def get_updated_data(self) -> bytes:
        """
        Return the modified data as immutable bytes.
        """
        return bytes(self.data)
