"""
Save file decryption and binary parsing.

Handles .sl2 (PC/BND4) and memory.dat (PS4) formats.
Binary offset math is intentionally verbatim from the original — do not refactor.
"""
import os
import shutil
import struct
from pathlib import Path
from typing import NamedTuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from nrplanner.constants import ITEM_TYPE_RELIC, ITEM_TYPE_WEAPON, ITEM_TYPE_ARMOR

# AES-128-CBC key for Nightreign .sl2 files
# Credit: jtesta/souls_givifier, Nordgaren/ArmoredCore6SaveTransferTool
_DS2_KEY = b'\x18\xF6\x32\x66\x05\xBD\x17\x8A\x55\x24\x52\x3A\xC0\xA0\xC6\x09'
_IV_SIZE = 0x10


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class RawRelic(NamedTuple):
    """Relic parsed from save data, before game-data enrichment."""
    ga_handle:   int  # 32-bit game-world handle (0xC0000000 type bits + unique id)
    item_id:     int  # raw item_id from save (real_id = item_id - 2147483648)
    effect_1:    int
    effect_2:    int
    effect_3:    int
    sec_effect1: int  # curse slot 1
    sec_effect2: int  # curse slot 2
    sec_effect3: int  # curse slot 3
    offset:      int  # byte offset within USERDATA file
    size:        int  # byte size of this item record


# ---------------------------------------------------------------------------
# Binary item parser (verbatim offsets — do not refactor math)
# ---------------------------------------------------------------------------

class Item:
    BASE_SIZE = 8  # gaitem_handle(4) + item_id(4)

    def __init__(self, gaitem_handle, item_id, effect_1, effect_2, effect_3,
                 durability, unk_1, sec_effect1, sec_effect2, sec_effect3,
                 unk_2, offset, extra=None, size=BASE_SIZE):
        self.gaitem_handle = gaitem_handle
        self.item_id       = item_id
        self.effect_1      = effect_1
        self.effect_2      = effect_2
        self.effect_3      = effect_3
        self.durability    = durability
        self.unk_1         = unk_1
        self.sec_effect1   = sec_effect1
        self.sec_effect2   = sec_effect2
        self.sec_effect3   = sec_effect3
        self.unk_2         = unk_2
        self.offset        = offset
        self.size          = size
        self.padding       = extra or ()

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> "Item":
        data_len = len(data)
        if offset + cls.BASE_SIZE > data_len:
            return cls(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, offset, size=cls.BASE_SIZE)

        gaitem_handle, item_id = struct.unpack_from("<II", data, offset)
        type_bits = gaitem_handle & 0xF0000000
        cursor = offset + cls.BASE_SIZE
        size = cls.BASE_SIZE

        durability = unk_1 = unk_2 = 0
        effect_1 = effect_2 = effect_3 = 0
        sec_effect1 = sec_effect2 = sec_effect3 = 0
        padding = ()

        if gaitem_handle != 0:
            if type_bits == ITEM_TYPE_WEAPON:
                cursor += 80
                size = cursor - offset
            elif type_bits == ITEM_TYPE_ARMOR:
                cursor += 8
                size = cursor - offset
            elif type_bits == ITEM_TYPE_RELIC:
                if cursor + 8 > data_len:
                    return cls(gaitem_handle, item_id, 0, 0, 0, 0, 0, 0, 0, 0, 0, offset, size=cls.BASE_SIZE)
                durability, unk_1 = struct.unpack_from("<II", data, cursor)
                cursor += 8

                if cursor + 12 > data_len:
                    return cls(gaitem_handle, item_id, 0, 0, 0, durability, unk_1, 0, 0, 0, 0, offset, size=cursor - offset)
                effect_1, effect_2, effect_3 = struct.unpack_from("<III", data, cursor)
                cursor += 12

                if cursor + 0x1C > data_len:
                    return cls(gaitem_handle, item_id, effect_1, effect_2, effect_3, durability, unk_1, 0, 0, 0, 0, offset, size=cursor - offset)
                padding = struct.unpack_from("<7I", data, cursor)
                cursor += 0x1C

                if cursor + 12 > data_len:
                    return cls(gaitem_handle, item_id, effect_1, effect_2, effect_3, durability, unk_1, 0, 0, 0, 0, offset, extra=padding, size=cursor - offset)
                sec_effect1, sec_effect2, sec_effect3 = struct.unpack_from("<III", data, cursor)
                cursor += 12

                if cursor + 4 > data_len:
                    return cls(gaitem_handle, item_id, effect_1, effect_2, effect_3, durability, unk_1, sec_effect1, sec_effect2, sec_effect3, 0, offset, extra=padding, size=cursor - offset)
                unk_2 = struct.unpack_from("<I", data, cursor)[0]
                cursor += 12
                size = cursor - offset

        return cls(gaitem_handle, item_id, effect_1, effect_2, effect_3,
                   durability, unk_1, sec_effect1, sec_effect2, sec_effect3,
                   unk_2, offset, extra=padding, size=size)


# ---------------------------------------------------------------------------
# BND4 decryption (PC .sl2)
# ---------------------------------------------------------------------------

class BND4Entry:
    def __init__(self, raw_data: bytes, index: int, output_dir: str,
                 size: int, offset: int, footer_length: int):
        self.index         = index
        self.size          = size
        self.footer_length = footer_length
        self._output_dir   = output_dir
        self._name         = f"USERDATA_{index:02d}"
        encrypted          = raw_data[offset:offset + size]
        self._iv           = encrypted[:_IV_SIZE]
        self._payload      = encrypted[_IV_SIZE:]
        self.decrypted     = False

    def decrypt(self) -> None:
        decryptor = Cipher(algorithms.AES(_DS2_KEY), modes.CBC(self._iv)).decryptor()
        data = decryptor.update(self._payload) + decryptor.finalize()
        os.makedirs(self._output_dir, exist_ok=True)
        with open(os.path.join(self._output_dir, self._name), "wb") as f:
            f.write(data)
        self.decrypted = True


def decrypt_sl2(input_file: str | Path,
                output_dir: str | Path | None = None,
                log_callback=None) -> Path:
    """Decrypt a BND4 .sl2 save file. Returns path to decrypted output directory."""
    input_file = Path(input_file)
    if output_dir is None:
        output_dir = input_file.parent / "decrypted_output"
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    raw = input_file.read_bytes()
    log(f"Read {len(raw)} bytes from {input_file}.")

    if raw[:4] != b'BND4':
        raise ValueError("Not a valid BND4 save file (missing BND4 header).")

    num_entries = struct.unpack("<i", raw[12:16])[0]
    log(f"BND4 entries: {num_entries}")

    BND4_HEADER_LEN      = 64
    BND4_ENTRY_HEADER_LEN = 32

    for i in range(num_entries):
        pos = BND4_HEADER_LEN + BND4_ENTRY_HEADER_LEN * i
        if pos + BND4_ENTRY_HEADER_LEN > len(raw):
            log(f"Warning: file too small to read entry #{i} header")
            break

        header = raw[pos:pos + BND4_ENTRY_HEADER_LEN]
        if header[:8] != b'\x40\x00\x00\x00\xff\xff\xff\xff':
            log(f"Warning: entry #{i} unexpected magic — skipping")
            continue

        size        = struct.unpack("<i", header[8:12])[0]
        data_offset = struct.unpack("<i", header[16:20])[0]
        footer_len  = struct.unpack("<i", header[24:28])[0]

        if size <= 0 or size > 1_000_000_000:
            log(f"Warning: entry #{i} invalid size {size} — skipping")
            continue
        if data_offset <= 0 or data_offset + size > len(raw):
            log(f"Warning: entry #{i} invalid offset {data_offset} — skipping")
            continue

        try:
            entry = BND4Entry(raw, i, str(output_dir), size, data_offset, footer_len)
            entry.decrypt()
        except Exception as e:
            log(f"Error decrypting entry #{i}: {e}")
            continue

    return output_dir


def split_memory_dat(file_path: str | Path,
                     output_dir: str | Path | None = None) -> Path:
    """Split a PS4 memory.dat save into USERDATA chunks. Returns output directory."""
    file_path = Path(file_path)
    if output_dir is None:
        output_dir = file_path.parent / "decrypted_output"
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir()

    with open(file_path, "rb") as f:
        (output_dir / "header").write_bytes(f.read(0x80))
        chunk_size = 0x100000
        for i in range(10):
            chunk = f.read(chunk_size)
            if not chunk:
                break
            data = (0x00100010).to_bytes(4, "little") + bytearray(chunk)
            (output_dir / f"userdata{i}").write_bytes(data)
        regulation = f.read()
        if regulation:
            (output_dir / "regulation").write_bytes(regulation)

    return output_dir


# ---------------------------------------------------------------------------
# Relic / character parsing (verbatim offsets — do not refactor math)
# ---------------------------------------------------------------------------

def _parse_items(data: bytes, start_offset: int,
                 slot_count: int = 5120) -> tuple[list[Item], int]:
    items = []
    offset = start_offset
    for _ in range(slot_count):
        item = Item.from_bytes(data, offset)
        items.append(item)
        offset += item.size
    return items, offset


def parse_relics(data: bytes) -> tuple[list[RawRelic], int]:
    """Parse relic inventory from a USERDATA binary blob.

    Returns (relics, items_end_offset).
    items_end_offset is needed to locate the character name:
        name_offset = items_end_offset + 0x94
    """
    items, end_offset = _parse_items(data, start_offset=0x14, slot_count=5120)
    relics = []
    for item in items:
        if (item.gaitem_handle & 0xF0000000) == ITEM_TYPE_RELIC:
            relics.append(RawRelic(
                ga_handle=item.gaitem_handle,
                item_id=item.item_id,
                effect_1=item.effect_1,
                effect_2=item.effect_2,
                effect_3=item.effect_3,
                sec_effect1=item.sec_effect1,
                sec_effect2=item.sec_effect2,
                sec_effect3=item.sec_effect3,
                offset=item.offset,
                size=item.size,
            ))
    return relics, end_offset


def read_char_name(data: bytes, items_end_offset: int) -> str | None:
    """Read character name from a USERDATA binary blob.

    items_end_offset is the return value of parse_relics().
    """
    name_offset = items_end_offset + 0x94
    max_chars = 16
    for cur in range(name_offset, name_offset + max_chars * 2, 2):
        if data[cur:cur + 2] == b'\x00\x00':
            max_chars = (cur - name_offset) // 2
            break
    raw = data[name_offset:name_offset + max_chars * 2]
    name = raw.decode("utf-16-le", errors="ignore").rstrip("\x00")
    return name if name else None


def discover_characters(decrypted_dir: str | Path,
                        mode: str = "PC") -> list[tuple[str, Path]]:
    """Enumerate characters from a decrypted save directory.

    Returns list of (character_name, file_path) in slot order.
    mode: "PC" (USERDATA_0x) or "PS4" (userdatax).
    """
    decrypted_dir = Path(decrypted_dir)
    prefix = "userdata" if mode == "PS4" else "USERDATA_0"
    results = []
    for i in range(10):
        file_path = decrypted_dir / f"{prefix}{i}"
        if not file_path.exists():
            continue
        try:
            data = file_path.read_bytes()
            if len(data) < 0x1000:
                continue
            _, end_offset = parse_relics(data)
            name = read_char_name(data, end_offset)
            if name:
                results.append((name, file_path))
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
    return results
