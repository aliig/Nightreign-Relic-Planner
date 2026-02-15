"""
Nightreign Relic Planner
Read-only save analyzer with relic build optimization.
"""
from main_file import decrypt_ds2_sl2
import json, shutil, os, struct
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional
import sys
import re

from basic_class import Item
import globals
from globals import ITEM_TYPE_RELIC, WORKING_DIR, COLOR_MAP

from relic_checker import RelicChecker
from source_data_handler import SourceDataHandler, get_system_language
from vessel_handler import LoadoutHandler
from optimizer_tab import OptimizerTab


# ---------------------------------------------------------------------------
# Global variables
# ---------------------------------------------------------------------------
os.chdir(WORKING_DIR)

data_source: Optional[SourceDataHandler] = None
relic_checker: Optional[RelicChecker] = None
loadout_handler: Optional[LoadoutHandler] = None
items_json = {}
effects_json = {}
userdata_path = None

MODE = None
char_name_list = []
ga_relic = []
ga_items = []
ga_acquisition_order = {}
current_murks = 0
current_sigs = 0
AOB_search = '00 00 00 00 0A 00 00 00 ?? ?? 00 00 00 00 00 00 06'
from_aob_steam = 44
steam_id = None

RELIC_COLOR_HEX = {
    'Red': '#FF4444',
    'Blue': '#4488FF',
    'Yellow': '#B8860B',
    'Green': '#44BB44',
    'White': '#AAAAAA',
    None: '#888888',
}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
def _ensure_data_source():
    global data_source
    if data_source is None:
        data_source = SourceDataHandler(language=get_system_language())


def get_base_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).resolve().parent


CONFIG_FILE = os.path.join(get_base_dir(), "editor_config.json")


def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Warning: Could not save config: {e}")


def load_json_data():
    global items_json, effects_json
    _ensure_data_source()
    try:
        items_json = data_source.get_relic_origin_structure()
        effects_json = data_source.get_effect_origin_structure()
        return True
    except FileNotFoundError as e:
        messagebox.showerror("Error",
                             f"Data files not found: {str(e)}")
        return False


# ---------------------------------------------------------------------------
# Save file parsing (read-only)
# ---------------------------------------------------------------------------
def parse_items(data_type, start_offset, slot_count=5120):
    items = []
    offset = start_offset
    for _ in range(slot_count):
        item = Item.from_bytes(data_type, offset)
        items.append(item)
        offset += item.size
    return items, offset


def parse_inventory_acquisition_order(data_type, items_end_offset):
    global ga_acquisition_order
    ga_acquisition_order = {}
    inventory_start = items_end_offset + 0x650
    relic_ga_set = set()
    for relic in ga_relic:
        relic_ga_set.add(relic[0])
    if not relic_ga_set:
        return ga_acquisition_order
    inventory_data = data_type[inventory_start:]
    for offset in range(0, min(len(inventory_data) - 14, 0x3000), 2):
        potential_ga = struct.unpack_from('<I', inventory_data, offset + 4)[0]
        if potential_ga in relic_ga_set and potential_ga not in ga_acquisition_order:
            acq_id = struct.unpack_from('<H', inventory_data, offset + 12)[0]
            ga_acquisition_order[potential_ga] = acq_id
    return ga_acquisition_order


def gaprint(data_type):
    global ga_relic, ga_items
    ga_items = []
    ga_relic = []
    start_offset = 0x14
    slot_count = 5120
    items, end_offset = parse_items(data_type, start_offset, slot_count)
    for item in items:
        type_bits = item.gaitem_handle & 0xF0000000
        parsed_item = (
            item.gaitem_handle,
            item.item_id,
            item.effect_1,
            item.effect_2,
            item.effect_3,
            item.sec_effect1,
            item.sec_effect2,
            item.sec_effect3,
            item.offset,
            item.size,
        )
        ga_items.append(parsed_item)
        if type_bits == ITEM_TYPE_RELIC:
            ga_relic.append(parsed_item)
    parse_inventory_acquisition_order(data_type, end_offset)
    return end_offset


def read_char_name(data):
    name_offset = gaprint(data) + 0x94
    max_chars = 16
    for cur in range(name_offset, name_offset + max_chars * 2, 2):
        if data[cur:cur + 2] == b'\x00\x00':
            max_chars = (cur - name_offset) // 2
            break
    raw_name = data[name_offset:name_offset + max_chars * 2]
    name = raw_name.decode("utf-16-le", errors="ignore").rstrip("\x00")
    return name if name else None


def read_murks_and_sigs(data):
    global current_murks, current_sigs
    offset = gaprint(data)
    name_offset = offset + 0x94
    murks_offset = name_offset + 52
    sigs_offset = name_offset - 64
    current_murks = struct.unpack_from('<I', data, murks_offset)[0]
    current_sigs = struct.unpack_from('<I', data, sigs_offset)[0]
    return current_murks, current_sigs


def split_files(file_path, folder_name):
    file_name = os.path.basename(file_path)
    split_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             folder_name)
    if os.path.exists(split_dir):
        shutil.rmtree(split_dir)
    os.makedirs(split_dir, exist_ok=True)

    if file_name.lower() == 'memory.dat':
        with open(file_path, "rb") as f:
            header = f.read(0x80)
            with open(os.path.join(split_dir, "header"), "wb") as out:
                out.write(header)
            chunk_size = 0x100000
            for i in range(10):
                data = f.read(chunk_size)
                if not data:
                    break
                with open(os.path.join(split_dir, f"userdata{i}"), "wb") as out:
                    data = bytearray(data)
                    data = (0x00100010).to_bytes(4, "little") + data
                    out.write(data)
            regulation = f.read()
            if regulation:
                with open(os.path.join(split_dir, "regulation"), "wb") as out:
                    out.write(regulation)

    elif file_path.lower().endswith('.sl2'):
        decrypt_ds2_sl2(file_path)


def name_to_path():
    global char_name_list, MODE
    char_name_list = []
    unpacked_folder = WORKING_DIR / 'decrypted_output'
    prefix = "userdata" if MODE == 'PS4' else "USERDATA_0"
    for i in range(10):
        file_path = os.path.join(unpacked_folder, f"{prefix}{i}")
        if not os.path.exists(file_path):
            continue
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()
                if len(file_data) < 0x1000:
                    continue
                name = read_char_name(file_data)
                if name:
                    char_name_list.append((name, file_path))
        except Exception as e:
            print(f"Error reading {file_path}: {e}")


# ---------------------------------------------------------------------------
# AOB search and Steam ID
# ---------------------------------------------------------------------------
def aob_to_pattern(aob: str):
    parts = aob.split()
    pattern = bytearray()
    mask = bytearray()
    for p in parts:
        if p == "??":
            pattern.append(0x00)
            mask.append(0)
        else:
            pattern.append(int(p, 16))
            mask.append(1)
    return bytes(pattern), bytes(mask)


def aob_search(data: bytes, aob: str):
    pattern, mask = aob_to_pattern(aob)
    L = len(pattern)
    mv = memoryview(data)
    start = 0x58524
    end = len(data) - L + 1
    for i in range(start, end):
        for j in range(L):
            b = mv[i + j]
            if mask[j]:
                if b != pattern[j]:
                    break
            else:
                continue
        else:
            return [i]
    return []


def find_steam_id(section_data):
    offsets = aob_search(section_data, AOB_search)
    if not offsets:
        return None
    offset = offsets[0] + from_aob_steam
    steam_bytes = section_data[offset:offset + 8]
    return steam_bytes.hex().upper()


# ---------------------------------------------------------------------------
# GUI Application
# ---------------------------------------------------------------------------
class RelicPlannerGUI:
    """Main application - read-only save analyzer with build optimizer."""

    def __init__(self, root):
        self.root = root
        self.root.title("Nightreign Relic Planner")
        self.root.geometry("1200x750")

        _ensure_data_source()

        # Notebook with 2 tabs
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill='both', expand=True)

        # Tab 1: File
        self.file_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.file_frame, text="File")
        self._setup_file_tab()

        # Tab 2: Optimizer
        self.optimizer_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.optimizer_frame, text="Optimizer")

        # Load JSON data for optimizer
        load_json_data()
        self.optimizer_ui = OptimizerTab(
            self.optimizer_frame, data_source, items_json, effects_json,
            get_base_dir())

        # Try to auto-load last file
        self.root.after(100, self._try_load_last_file)

    def _setup_file_tab(self):
        """Setup the file loading tab."""
        # Top bar: Open file button
        top_frame = ttk.Frame(self.file_frame)
        top_frame.pack(fill='x', padx=10, pady=10)

        ttk.Button(top_frame, text="Open Save File",
                   command=self._open_file).pack(side='left', padx=5)

        self.file_label = ttk.Label(top_frame, text="No file loaded",
                                    foreground='gray')
        self.file_label.pack(side='left', padx=15)

        # Character buttons area
        char_label_frame = ttk.LabelFrame(self.file_frame,
                                          text="Characters")
        char_label_frame.pack(fill='x', padx=10, pady=5)

        self.char_button_frame = ttk.Frame(char_label_frame)
        self.char_button_frame.pack(fill='x', padx=5, pady=5)
        self.char_buttons = []

        # Stats display
        stats_frame = ttk.LabelFrame(self.file_frame, text="Character Info")
        stats_frame.pack(fill='x', padx=10, pady=5)

        stats_inner = ttk.Frame(stats_frame)
        stats_inner.pack(fill='x', padx=10, pady=5)

        self.char_name_label = ttk.Label(stats_inner, text="Character: -")
        self.char_name_label.pack(anchor='w')

        stats_row = ttk.Frame(stats_inner)
        stats_row.pack(fill='x', pady=2)
        self.murks_label = ttk.Label(stats_row, text="Murks: -")
        self.murks_label.pack(side='left', padx=(0, 20))
        self.sigs_label = ttk.Label(stats_row, text="Sigs: -")
        self.sigs_label.pack(side='left', padx=(0, 20))
        self.relic_count_label = ttk.Label(stats_row, text="Relics: -")
        self.relic_count_label.pack(side='left')

        self.steam_id_label = ttk.Label(stats_inner, text="Steam ID: -")
        self.steam_id_label.pack(anchor='w', pady=(2, 0))

        # Info text
        info_frame = ttk.LabelFrame(self.file_frame, text="How to Use")
        info_frame.pack(fill='both', expand=True, padx=10, pady=5)

        info_text = (
            "1. Click 'Open Save File' to load your Nightreign save "
            "(.sl2 for PC, memory.dat for PS4)\n"
            "2. Select a character to load their relic inventory\n"
            "3. Switch to the Optimizer tab to create builds and "
            "find optimal relic combinations\n\n"
            "Save file locations:\n"
            "  PC: %APPDATA%/EldenRing/Nightreign/<steam_id>/NR0000.sl2\n"
            "  PS4: Decrypted memory.dat"
        )
        ttk.Label(info_frame, text=info_text, wraplength=700,
                  justify='left').pack(padx=10, pady=10, anchor='w')

    def _open_file(self):
        global MODE
        file_path = filedialog.askopenfilename(
            title="Select Save File",
            filetypes=[("Save Files", "*.sl2"), ("PS4 Saves", "*.dat"),
                       ("All Files", "*.*")]
        )
        if not file_path:
            return

        file_name = os.path.basename(file_path)

        # Determine mode
        if file_name.lower() == 'memory.dat':
            MODE = 'PS4'
        elif file_path.lower().endswith('.sl2'):
            try:
                with open(file_path, 'rb') as f:
                    header = f.read(4)
                if header != b'BND4':
                    messagebox.showerror(
                        "Error",
                        "Not a valid BND4 save file.")
                    return
                MODE = 'PC'
            except Exception as e:
                messagebox.showerror("Error", f"Could not read file: {e}")
                return
        else:
            messagebox.showerror(
                "Error",
                "Please select a .sl2 (PC) or memory.dat (PS4) file.")
            return

        # Decrypt/extract
        split_files(file_path, 'decrypted_output')

        # Load data
        if not load_json_data():
            return

        # Get character names
        name_to_path()

        # Save to config
        config = load_config()
        config['last_file'] = file_path
        config['last_mode'] = MODE
        config['last_char_index'] = 0
        save_config(config)

        self.file_label.config(
            text=f"Loaded: {file_name}", foreground='black')
        self._display_character_buttons()

    def _display_character_buttons(self):
        for widget in self.char_button_frame.winfo_children():
            widget.destroy()

        style = ttk.Style()
        style.configure("Char.TButton", font=('Arial', 10), padding=5)
        style.configure("Highlighted.TButton", font=('Arial', 10),
                        padding=5, foreground="red")

        self.char_buttons = []
        columns = 4
        for idx, (name, path) in enumerate(char_name_list):
            row = idx // columns
            col = idx % columns
            btn = ttk.Button(
                self.char_button_frame,
                text=f"{idx + 1}. {name}",
                style="Char.TButton",
                command=lambda b_idx=idx, p=path, n=name:
                    self._on_character_click(b_idx, p, n),
                width=20
            )
            btn.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            self.char_buttons.append(btn)

        for c in range(columns):
            self.char_button_frame.grid_columnconfigure(c, weight=1)

    def _on_character_click(self, idx, path, name):
        for b in self.char_buttons:
            b.configure(style="Char.TButton")
        self.char_buttons[idx].configure(style="Highlighted.TButton")

        config = load_config()
        config['last_char_index'] = idx
        save_config(config)

        self._load_character(path, name)

    def _load_character(self, path, name=None):
        global userdata_path, steam_id, ga_relic, relic_checker, loadout_handler

        userdata_path = path
        try:
            with open(path, "rb") as f:
                globals.data = bytearray(f.read())

            # Parse items/relics
            gaprint(globals.data)

            # Parse vessels and presets
            loadout_handler = LoadoutHandler(data_source, ga_relic)
            loadout_handler.parse()

            # Initialize relic checker
            relic_checker = RelicChecker(ga_relic=ga_relic,
                                        data_source=data_source)

            # Read stats
            read_murks_and_sigs(globals.data)
            steam_id = find_steam_id(globals.data)

            # Update stats display
            char_name = name or read_char_name(globals.data) or "Unknown"
            self.char_name_label.config(text=f"Character: {char_name}")
            self.murks_label.config(text=f"Murks: {current_murks:,}")
            self.sigs_label.config(text=f"Sigs: {current_sigs:,}")
            self.relic_count_label.config(
                text=f"Relics: {len(ga_relic)}")
            self.steam_id_label.config(
                text=f"Steam ID: {steam_id or 'N/A'}")

            # Notify optimizer
            self.optimizer_ui.on_inventory_loaded(ga_relic, relic_checker)

            # Switch to optimizer tab
            self.notebook.select(self.optimizer_frame)

        except struct.error as e:
            messagebox.showerror(
                "Error",
                f"Failed to load character: Data structure error\n\n"
                f"Details: {str(e)}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            messagebox.showerror("Error",
                                 f"Failed to load character: {str(e)}")

    def _try_load_last_file(self):
        """Auto-load the last opened file on startup."""
        config = load_config()
        last_file = config.get('last_file')
        last_mode = config.get('last_mode')
        last_char = config.get('last_char_index', 0)

        if not last_file or not os.path.exists(last_file):
            return

        global MODE
        MODE = last_mode

        try:
            split_files(last_file, 'decrypted_output')
            if not load_json_data():
                return

            # Update optimizer with fresh JSON data
            self.optimizer_ui.items_json = items_json
            self.optimizer_ui.effects_json = effects_json

            name_to_path()

            self.file_label.config(
                text=f"Loaded: {os.path.basename(last_file)}",
                foreground='black')
            self._display_character_buttons()

            # Auto-select last character
            if 0 <= last_char < len(char_name_list):
                name, path = char_name_list[last_char]
                self.char_buttons[last_char].configure(
                    style="Highlighted.TButton")
                self._load_character(path, name)

        except Exception as e:
            print(f"Could not auto-load last file: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    root = tk.Tk()
    app = RelicPlannerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
