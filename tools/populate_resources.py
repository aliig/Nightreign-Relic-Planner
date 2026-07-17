# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cryptography>=40.0.0",
#   "zstandard>=0.21.0",
# ]
# ///
"""populate_resources.py — Regenerate nrplanner/resources/ from Nightreign game data.

Binary format knowledge derived from Smithbox (https://github.com/vawser/Smithbox)
by vawser et al., licensed under the MIT License.

Usage:
    uv run tools/populate_resources.py [--game-dir PATH]
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import struct
import sys
import zlib
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import zstandard as zstd
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAME_DIR_DEFAULT = (
    r"C:\Program Files (x86)\Steam\steamapps\common"
    r"\ELDEN RING NIGHTREIGN\Game"
)

NR_REGULATION_KEY = bytes.fromhex(
    "9a8ee90c4c01a43168a17d9d75e4a7d02107ebcf43d5acb0554f941601b57918"
)

COLOR_MAP = ["Red", "Blue", "Yellow", "Green", "White"]

LOCALE_MAP: dict[str, str] = {
    "en_US": "engus",
    "ja_JP": "jpnjp",
    "fr_FR": "frafr",
    "de_DE": "deude",
    "it_IT": "itait",
    "es_ES": "spaes",
    "es_AR": "spaar",
    "ko_KR": "korkr",
    "zh_CN": "zhocn",
    "zh_TW": "zhotw",
    "pl_PL": "polpl",
    "pt_BR": "porbr",
    "ru_RU": "rusru",
    "th_TH": "thath",
    "ar_AE": "araae",
}

# FMG stems we extract from item_dlc01.msgbnd.dcx
# In NR, item_dlc01.msgbnd.dcx contains ALL FMGs (both base and DLC).
FMG_STEMS = [
    "AntiqueName", "AttachEffectName", "NpcName", "GoodsName",
    "AntiqueName_dlc01", "AttachEffectName_dlc01", "NpcName_dlc01", "GoodsName_dlc01",
]

# Param files written as CSVs into resources/param/ (game_data_version hashes these,
# so keep this to the relic-effect params ONLY — acquisition-side params below are
# parsed in-memory for relic_lots.json and never emitted as CSV).
TARGET_PARAMS: dict[str, str] = {
    "EquipParamAntique": "EquipParamAntique.param",
    "AttachEffectParam": "AttachEffectParam.param",
    "AttachEffectTableParam": "AttachEffectTableParam.param",
    "AntiqueStandParam": "AntiqueStandParam.param",
}

# Acquisition-side params parsed in-memory ONLY (relic purchase / drop lots, Phase 2).
# NOT written as CSVs — see PARAM_PARSERS note. ItemTableParam is huge (34730 rows)
# and MUST stay in-memory: it is the "Item Table" a flatstone shop row rolls (Phase 2
# relic_lots.json). See generate_relic_lots_json for the shop -> table -> template chain.
ACQUISITION_PARAMS: dict[str, str] = {
    "ShopLineupParam": "ShopLineupParam.param",
    "ItemTableParam": "ItemTableParam.param",
    "ItemLotParam_map": "ItemLotParam_map.param",
    "ItemLotParam_enemy": "ItemLotParam_enemy.param",
    "EquipParamGoods": "EquipParamGoods.param",
}

# RSA public keys for BHD5 archives (PKCS#1 PEM, from Smithbox ArchiveKeys.cs)
NR_ARCHIVE_KEYS: dict[str, str] = {
    "data0": """\
-----BEGIN RSA PUBLIC KEY-----
MIIBDAKCAQEAz8F9U1V9hgKs40gdzl1ZOf3IBirf6xUEzXtDd6oSEBE6XiYocvAB
ykiK+WMdAaJL7HJ58Gt2xSRxA3t9toCGKMI/3gNAfcR0BV83gsQo0O0dVP0fqyxX
lA2pGN5B4IE8aLWPX2cNNFSFKAdjYnzsYSevzef/pgnpV1ZgPf2j2SQwNGSufYeN
3Owji8l0K2C0fKIx6gSO0cK9kvTIm8AdpvzZbBkTylT1jF3m8DsSA1OFzFJTdFyZ
bTRi85M6bmv6rHtvZc5OW21dye7Q6fmLlxOyMetLTu4dpOXjHAAf/LFTbfQpXFr9
aXO4O6I7nWDJn7FRzNlLkb8RwSyZ1/KWyQIFALEDsAc=
-----END RSA PUBLIC KEY-----""",
    "data1": """\
-----BEGIN RSA PUBLIC KEY-----
MIIBDAKCAQEA0E6dtnDmT6d2+VaNkPzomUNv+T6896H//RAaTR2guPACMDNZpAsF
vV3MfNcR2BS6Cbxl55MmMWsmsZs1s293MuOdS+c99vmZbNYcXWjx0uJGO+VrRXe4
3TRzmQFh1uD+Xcq6+wYfTrGyLOdAtmwdDXNvW8jYoFDM7nsuoPKOXKtKd0uz7/MK
ZYLk1J7pAoBQqw9VD5qi2Ih86zn0VWm5lLMTI0qnutOzpZVDvZWBg/jr4Nbnr/Ox
PLeJO1tFuRuHUPuBAWtYM/J23MPqqKkQrG5z2r7PexUI744UPdmo3Sn+Mqynuxxv
V9SEhska6pStzn8R9i94wOKPTQ32HEFuUQIFAP////8=
-----END RSA PUBLIC KEY-----""",
    "data2": """\
-----BEGIN RSA PUBLIC KEY-----
MIIBDAKCAQEAqpkf9yHnx8k84+WXITLFUW/STypXjZMPuw842pzNHa5L7v9gU4M5
hBHwTQs0YIcfnf+mbjqoJYnmYPBblxLjFXgwT4ICJdpnPMY75BwD0Nv28/CvvIsA
0QQWOhUeOXnm5BT26dGYi3CHHPvD14F76tJt3TO/CC3fyhdxne9Cra5G87aGTJGv
0ImsU0KPCizYX/RHQ2jdJdlB5BHzkMgLhIaEdhC3nhIqMJDNQNGKMo7rRV1tAEGf
0zIZ23PGEsPsbVg31nnnRoq338WfD9ArZZG6bM11vlfVcYmrJs7v4vBjKXnYVwVX
0rQGIfSNDnaZcEj4tsl04AqnupTdvSrHXwIFANOg6RU=
-----END RSA PUBLIC KEY-----""",
    "data3": """\
-----BEGIN RSA PUBLIC KEY-----
MIIBCwKCAQEAwm2Rcw4eoP8FgWijxw1X8b9rEVFsVqy7rXWcH2yVm61yYBlzPlTq
Kqnc2VeqZSh/TLXeFY3+Om2X78RQxZNS3L3OokvD7l/0wqPIpXSSumeeL8UAZm5k
7nFA2m2HJfc+F07kNwwCEqhmFs5YQIMnWyIrqnEax/qSncFErLjIYMBMArVnVLE8
WqgsD7N8lW937dlUcT2TaPh1HfjavKOSUy/OHM9zaneyDL4NRmDdU8GmNXTSm5kP
YoSRCDIvFVj0g5iaXr60eRh0d+40TctoBUdtaoJCPOyRlmkE7qU6Q9FyyvMNbhtf
D95d+6IJejNd7kvyV/ISlB37kb2Uh9TavwIEOqKLtw==
-----END RSA PUBLIC KEY-----""",
    "dlc01": """\
-----BEGIN RSA PUBLIC KEY-----
MIIBDAKCAQEA1q4MOehlD++h5Ietq9Jk97eGOJL2zDpDcu9Wk6RXK1+R3LycMBQl
L/hnPg/qqvcoViA7wLX5GOFr5lo6dtKaQqlBkBqgYHGIdBvioBPZ8BuXAjYr3sm8
N0SYC2TNHXmfw6yFC+ePsrl+gNldrO//XXY27hsGgcegfWr6JuQaJti/BOKlGb8A
RbKwyIqGc5WiWj/v0tGE1cdPi0fLQRbTrLFaQtx1roQVqsQuJ5zRGTpnj/mhaJtq
J7V0s5gLG5CCevx71lN8m7oyWk2JemzSLvllwv4tjtzrw3jNQtiYb8nzy2Spjibs
vX1iRCg5btMSiNPcSeIJ5jX+FUW9LSnrkwIFAKhopbM=
-----END RSA PUBLIC KEY-----""",
}

# sd/ subdirectory archives share a separate RSA key
NR_SD_ARCHIVE_KEYS: dict[str, str] = {
    "sd": """\
-----BEGIN RSA PUBLIC KEY-----
MIIBCwKCAQEA19Y/R69SXASLOgInwfAXjAXuWSTQ6GP7XNoMDY0ThefISGG2p7G5
oQDpvK9oMGISCqHTr4ijs31GoC0dBG5Vnl1dRO+teXORoy+vlM3dRc1XyBXWkLM8
8O8PkhWeisf2EGyAa1jGjAAPNblKIAWbUFsxW2Ve7PKRF3FQAIiSPiOIbc24C3zE
TpbKDCVoDlm80DTv+Fg2ZdgD985ZDGtwBvg+RRe19iLg7imcrHeZdvqI/CzaY+r3
l5hFle31jjWopOm8sORZUMAWPFxuGm+lnB7v0iCCTboq+YC24sOXNabjsgnKkQF1
1G7uQz1qjnmQxnp3FgbnHRe1I3mCwELuvwIEOC192w==
-----END RSA PUBLIC KEY-----""",
    "sd_dlc01": """\
-----BEGIN RSA PUBLIC KEY-----
MIIBCwKCAQEA19Y/R69SXASLOgInwfAXjAXuWSTQ6GP7XNoMDY0ThefISGG2p7G5
oQDpvK9oMGISCqHTr4ijs31GoC0dBG5Vnl1dRO+teXORoy+vlM3dRc1XyBXWkLM8
8O8PkhWeisf2EGyAa1jGjAAPNblKIAWbUFsxW2Ve7PKRF3FQAIiSPiOIbc24C3zE
TpbKDCVoDlm80DTv+Fg2ZdgD985ZDGtwBvg+RRe19iLg7imcrHeZdvqI/CzaY+r3
l5hFle31jjWopOm8sORZUMAWPFxuGm+lnB7v0iCCTboq+YC24sOXNabjsgnKkQF1
1G7uQz1qjnmQxnp3FgbnHRe1I3mCwELuvwIEOC192w==
-----END RSA PUBLIC KEY-----""",
}

# ---------------------------------------------------------------------------
# Low-level binary helpers
# ---------------------------------------------------------------------------


def _reverse_bits(b: int) -> int:
    """Reverse the bits of a single byte."""
    return int(f"{b:08b}"[::-1], 2)


_oodle_dll = None


def _get_oodle(game_dir: Path):
    """Lazy-load the Oodle decompression DLL from the game directory."""
    global _oodle_dll
    if _oodle_dll is None:
        dll_path = game_dir / "oo2core_9_win64.dll"
        if not dll_path.exists():
            raise FileNotFoundError(
                f"Oodle DLL not found: {dll_path}\n"
                "The game uses Oodle Kraken compression and needs oo2core_9_win64.dll."
            )
        _oodle_dll = ctypes.cdll.LoadLibrary(str(dll_path))
    return _oodle_dll


def _oodle_decompress(game_dir: Path, comp_data: bytes, raw_len: int) -> bytes:
    """Decompress data using Oodle (Kraken) via the game's DLL."""
    oodle = _get_oodle(game_dir)
    raw_buf = ctypes.create_string_buffer(raw_len)
    result = oodle.OodleLZ_Decompress(
        comp_data, len(comp_data),
        raw_buf, raw_len,
        0, 0, 0,       # fuzzSafe, checkCRC, verbosity
        None, 0,        # decBufBase, decBufSize
        None, None,     # fpCallback, callbackUserData
        None, 0,        # decoderMemory, decoderMemorySize
        0,              # threadPhase
    )
    if result != raw_len:
        raise ValueError(f"Oodle decompress failed: got {result}, expected {raw_len}")
    return raw_buf.raw


def dcx_decompress(data: bytes, game_dir: Path | None = None) -> bytes:
    """Decompress a DCX (ZSTD, DFLT, or KRAK) container. Returns raw inner data."""
    magic = data[:4]
    if magic == b"DCX\0":
        # Check compression type string at offset 0x28
        comp_type = data[0x28:0x2C]
        # DCS block: uncompressed_size at 0x1C, compressed_size at 0x20
        uncompressed_size = struct.unpack_from(">I", data, 0x1C)[0]
        compressed_size = struct.unpack_from(">I", data, 0x20)[0]
        compressed = data[76 : 76 + compressed_size]

        if comp_type == b"KRAK":
            if game_dir is None:
                raise ValueError("KRAK decompression requires game_dir for Oodle DLL")
            return _oodle_decompress(game_dir, compressed, uncompressed_size)
        elif comp_type == b"ZSTD":
            dctx = zstd.ZstdDecompressor()
            return dctx.decompress(compressed, max_output_size=256 * 1024 * 1024)
        elif comp_type == b"DFLT":
            return zlib.decompress(compressed, -15)
        else:
            raise ValueError(f"Unknown DCX compression: {comp_type!r}")
    elif magic == b"DCP\0":
        comp_type = data[4:8]
        if comp_type == b"DFLT":
            compressed_size = struct.unpack_from(">I", data, 0x28)[0]
            compressed = data[0x2C : 0x2C + compressed_size]
            return zlib.decompress(compressed, -15)
        elif comp_type == b"ZSTD":
            compressed_size = struct.unpack_from(">I", data, 0x28)[0]
            compressed = data[0x2C : 0x2C + compressed_size]
            dctx = zstd.ZstdDecompressor()
            return dctx.decompress(compressed, max_output_size=256 * 1024 * 1024)
        else:
            raise ValueError(f"Unknown DCP compression: {comp_type!r}")
    else:
        raise ValueError(f"Not a DCX file: magic={magic!r}")


def _read_utf16_at(data: bytes, offset: int) -> str:
    """Read a null-terminated UTF-16LE string at the given offset."""
    chars = []
    while offset + 1 < len(data):
        c = struct.unpack_from("<H", data, offset)[0]
        if c == 0:
            break
        chars.append(chr(c))
        offset += 2
    return "".join(chars)


def _read_shift_jis_at(data: bytes, offset: int) -> str:
    """Read a null-terminated Shift-JIS string at the given offset."""
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("shift_jis", errors="replace")


def parse_bnd4(data: bytes, game_dir: Path | None = None) -> list[tuple[int, str, bytes]]:
    """Parse a BND4 container. Returns list of (id, name, file_data)."""
    assert data[:4] == b"BND4", f"Not BND4: {data[:4]!r}"

    # Header
    big_endian = bool(data[9])
    bit_big_endian = not bool(data[10])
    endian = ">" if big_endian else "<"

    file_count = struct.unpack_from(f"{endian}I", data, 12)[0]
    # offset 16: assert 0x40 (header size)
    # offset 24: version string (8 bytes)
    # offset 32: file_header_size (i64)
    # offset 40: headers_end (i64)
    unicode = bool(data[48])

    # Format byte at offset 49, with bit reversal
    raw_format = data[49]
    if bit_big_endian:
        fmt_flags = raw_format
    else:
        reverse = (raw_format & 1) != 0 and (raw_format & 0x80) == 0
        fmt_flags = raw_format if reverse else _reverse_bits(raw_format)

    has_ids = bool(fmt_flags & 0x02)
    has_names = bool(fmt_flags & (0x04 | 0x08))
    has_long_offsets = bool(fmt_flags & 0x10)
    has_compression = bool(fmt_flags & 0x20)

    # extended byte at offset 50
    extended = data[50]
    # offset 52: padding i32
    # offset 56: hash_table_offset or 0 (i64)

    # File headers start at 0x40
    pos = 0x40

    files = []
    for _ in range(file_count):
        # File flags byte (with bit reversal based on bitBigEndian)
        raw_file_flags = data[pos]
        if bit_big_endian:
            file_flags = raw_file_flags
        else:
            file_flags = _reverse_bits(raw_file_flags)
        is_compressed = bool(file_flags & 0x01)
        pos += 1

        # 3 zero bytes
        pos += 3
        # assert -1 (i32)
        pos += 4

        # compressed_size (i64)
        compressed_size = struct.unpack_from(f"{endian}q", data, pos)[0]
        pos += 8

        uncompressed_size = -1
        if has_compression:
            uncompressed_size = struct.unpack_from(f"{endian}q", data, pos)[0]
            pos += 8

        if has_long_offsets:
            data_offset = struct.unpack_from(f"{endian}q", data, pos)[0]
            pos += 8
        else:
            data_offset = struct.unpack_from(f"{endian}I", data, pos)[0]
            pos += 4

        file_id = -1
        if has_ids:
            file_id = struct.unpack_from(f"{endian}i", data, pos)[0]
            pos += 4

        name = ""
        if has_names:
            name_offset = struct.unpack_from(f"{endian}I", data, pos)[0]
            pos += 4
            if unicode:
                name = _read_utf16_at(data, name_offset)
            else:
                name = _read_shift_jis_at(data, name_offset)

        # Special case: Format.Names1 only (0x04 without IDs) has an extra i64
        if fmt_flags == 0x04:
            pos += 8

        # Read file data
        raw_file_data = data[data_offset : data_offset + compressed_size]
        if is_compressed and raw_file_data[:4] in (b"DCX\0", b"DCP\0"):
            raw_file_data = dcx_decompress(raw_file_data, game_dir)

        files.append((file_id, name, bytes(raw_file_data)))

    return files


# ---------------------------------------------------------------------------
# PARAM parsing
# ---------------------------------------------------------------------------


def parse_param_rows(data: bytes) -> list[tuple[int, bytes]]:
    """Parse a PARAM binary and return list of (row_id, row_data_bytes).

    Supports NR format: LongDataOffset | OffsetParamType.
    """
    # Read format flags at 0x2C
    format_2d = data[0x2D]  # 0x2C=BigEndian, 0x2D=Format2D

    has_long_data_offset = bool(format_2d & 0x04)
    has_offset_param_type = bool(format_2d & 0x80)

    if not has_long_data_offset:
        raise ValueError(f"Unsupported PARAM format: 0x{format_2d:02X} (no LongDataOffset)")

    # Row count at offset 10
    row_count = struct.unpack_from("<H", data, 10)[0]

    # Row headers start at offset 64 (0x40) for NR format
    # Each row header: ID(i32) + pad(i32) + DataOffset(i64) + nameOffset(i64) = 24 bytes
    row_header_size = 24
    headers_start = 0x40

    rows = []
    for i in range(row_count):
        hdr_off = headers_start + i * row_header_size
        row_id = struct.unpack_from("<i", data, hdr_off)[0]
        row_data_offset = struct.unpack_from("<q", data, hdr_off + 8)[0]
        rows.append((row_id, row_data_offset))

    # Determine row data size from consecutive offsets
    result = []
    for i, (row_id, offset) in enumerate(rows):
        if i + 1 < len(rows):
            next_offset = rows[i + 1][1]
            size = next_offset - offset
        else:
            # Last row: use remaining data or strings_offset
            strings_offset = struct.unpack_from("<I", data, 0)[0]
            if strings_offset > offset:
                size = strings_offset - offset
            else:
                size = len(data) - offset
        result.append((row_id, data[offset : offset + size]))

    return result


# ---------------------------------------------------------------------------
# Per-param row parsers -> CSV column values
# ---------------------------------------------------------------------------


def _fmt_arr(bs: bytes | list[int]) -> str:
    """Format a byte array as [v1|v2|...|vN]."""
    vals = list(bs)
    return "[" + "|".join(str(v) for v in vals) + "]"


# CSV column order for each param
EQUIP_PARAM_ANTIQUE_COLS = [
    "disableParam_NT", "disableParamReserve1", "disableParamReserve2",
    "relicColor", "sortGroupId", "isSalable",
    "isDeepRelic", "colorIcon", "unknown_2",
    "sortId", "padding",
    "sortId_unk",
    "attachEffectTableId_1", "attachEffectTableId_2", "attachEffectTableId_3",
    "iconId",
    "attachEffectTableId_curse1", "attachEffectTableId_curse2", "attachEffectTableId_curse3",
    "endPadding",
]

ATTACH_EFFECT_PARAM_COLS = [
    "onHitSpEffect", "unknown_1", "unknown_2",
    "passiveSpEffectId_1", "passiveSpEffectId_2", "passiveSpEffectId_3",
    "compatibilityId", "attachTextId",
    "isPersistentEffect", "isNumericEffect", "isStrongestEffect", "unknown_8a4",
    "isDebuff", "unknown_8c", "unknown_8d",
    "displayPercentageSymbol",
    "allowWylder", "allowGuardian", "allowIroneye", "allowDuchess",
    "allowRaider", "allowRevenant", "allowRecluse", "allowExecutor",
    "allowScholar", "allowUndertaker", "unknown_9c3", "unknown_9d",
    "statusIconId", "overrideBaseEffectId", "displayedModifierValue",
    "overrideEffectId", "attachFilterParamId", "exclusivityId", "permanentSpEffectId",
]

ATTACH_EFFECT_TABLE_PARAM_COLS = [
    "unknown_0", "attachEffectId", "chanceWeight", "chanceWeight_dlc",
]

ANTIQUE_STAND_PARAM_COLS = [
    "disableParam_NT", "disableParamReserve1", "disableParamReserve2",
    "iconId",
    "heroType", "relicSlot1", "relicSlot2", "relicSlot3",
    "unlockFlag", "goodsId",
    "deepRelicSlot1", "deepRelicSlot2", "deepRelicSlot3",
    "endPadding",
]


def _parse_equip_param_antique(row: bytes) -> dict[str, str]:
    """48-byte EquipParamAntique row -> CSV column values."""
    b0 = row[0]
    b7 = row[7]
    s32s = struct.unpack_from("<8i", row, 12)
    return {
        "disableParam_NT": str(b0 & 1),
        "disableParamReserve1": str((b0 >> 1) & 0x7F),
        "disableParamReserve2": _fmt_arr(row[1:4]),
        "relicColor": str(row[4]),
        "sortGroupId": str(row[5]),
        "isSalable": str(row[6]),
        "isDeepRelic": str(b7 & 1),
        "colorIcon": str((b7 >> 1) & 1),
        "unknown_2": str((b7 >> 2) & 0x3F),
        "sortId": str(struct.unpack_from("<H", row, 8)[0]),
        "padding": _fmt_arr(row[10:12]),
        "sortId_unk": str(s32s[0]),
        "attachEffectTableId_1": str(s32s[1]),
        "attachEffectTableId_2": str(s32s[2]),
        "attachEffectTableId_3": str(s32s[3]),
        "iconId": str(s32s[4]),
        "attachEffectTableId_curse1": str(s32s[5]),
        "attachEffectTableId_curse2": str(s32s[6]),
        "attachEffectTableId_curse3": str(s32s[7]),
        "endPadding": _fmt_arr(row[44:48]),
    }


def _parse_attach_effect_param(row: bytes) -> dict[str, str]:
    """68-byte AttachEffectParam row -> CSV column values."""
    s32_0 = struct.unpack_from("<8i", row, 0)
    b32 = row[32]
    b37 = row[37]
    b38 = row[38]
    s32_1 = struct.unpack_from("<7i", row, 40)
    return {
        "onHitSpEffect": str(s32_0[0]),
        "unknown_1": str(s32_0[1]),
        "unknown_2": str(s32_0[2]),
        "passiveSpEffectId_1": str(s32_0[3]),
        "passiveSpEffectId_2": str(s32_0[4]),
        "passiveSpEffectId_3": str(s32_0[5]),
        "compatibilityId": str(s32_0[6]),
        "attachTextId": str(s32_0[7]),
        "isPersistentEffect": str(b32 & 1),
        "isNumericEffect": str((b32 >> 1) & 1),
        "isStrongestEffect": str((b32 >> 2) & 1),
        "unknown_8a4": str((b32 >> 3) & 0x1F),
        "isDebuff": str(row[33]),
        "unknown_8c": str(row[34]),
        "unknown_8d": str(row[35]),
        "displayPercentageSymbol": str(row[36]),
        "allowWylder": str(b37 & 1),
        "allowGuardian": str((b37 >> 1) & 1),
        "allowIroneye": str((b37 >> 2) & 1),
        "allowDuchess": str((b37 >> 3) & 1),
        "allowRaider": str((b37 >> 4) & 1),
        "allowRevenant": str((b37 >> 5) & 1),
        "allowRecluse": str((b37 >> 6) & 1),
        "allowExecutor": str((b37 >> 7) & 1),
        "allowScholar": str(b38 & 1),
        "allowUndertaker": str((b38 >> 1) & 1),
        "unknown_9c3": str((b38 >> 2) & 0x3F),
        "unknown_9d": str(row[39]),
        "statusIconId": str(s32_1[0]),
        "overrideBaseEffectId": str(s32_1[1]),
        "displayedModifierValue": str(s32_1[2]),
        "overrideEffectId": str(s32_1[3]),
        "attachFilterParamId": str(s32_1[4]),
        "exclusivityId": str(s32_1[5]),
        "permanentSpEffectId": str(s32_1[6]),
    }


def _parse_attach_effect_table_param(row: bytes) -> dict[str, str]:
    """12-byte AttachEffectTableParam row -> CSV column values."""
    vals = struct.unpack_from("<iiHh", row, 0)
    return {
        "unknown_0": str(vals[0]),
        "attachEffectId": str(vals[1]),
        "chanceWeight": str(vals[2]),
        "chanceWeight_dlc": str(vals[3]),
    }


def _parse_antique_stand_param(row: bytes) -> dict[str, str]:
    """20 or 24-byte AntiqueStandParam row -> CSV column values."""
    b0 = row[0]
    icon_id = struct.unpack_from("<i", row, 4)[0]
    hero_type, slot1, slot2, slot3 = struct.unpack_from("<bbbb", row, 8)
    unlock_flag, goods_id = struct.unpack_from("<ii", row, 12)

    result = {
        "disableParam_NT": str(b0 & 1),
        "disableParamReserve1": str((b0 >> 1) & 0x7F),
        "disableParamReserve2": _fmt_arr(row[1:4]),
        "iconId": str(icon_id),
        "heroType": str(hero_type),
        "relicSlot1": str(slot1),
        "relicSlot2": str(slot2),
        "relicSlot3": str(slot3),
        "unlockFlag": str(unlock_flag),
        "goodsId": str(goods_id),
    }

    # DLC fields (row size >= 24)
    if len(row) >= 24:
        ds1, ds2, ds3 = struct.unpack_from("<bbb", row, 20)
        result["deepRelicSlot1"] = str(ds1)
        result["deepRelicSlot2"] = str(ds2)
        result["deepRelicSlot3"] = str(ds3)
        result["endPadding"] = str(row[23])
    else:
        result["deepRelicSlot1"] = "0"
        result["deepRelicSlot2"] = "0"
        result["deepRelicSlot3"] = "0"
        result["endPadding"] = "0"

    return result


# ---------------------------------------------------------------------------
# Acquisition-side params (Phase 2): shop lineup, item lots, goods.
# Field offsets verified against Smithbox NR paramdefs (vawser/Smithbox
# src/Smithbox.Data/Assets/PARAM/NR/Defs) AND the raw regulation bytes.
# ---------------------------------------------------------------------------

# ITEMLOT_PARAM_ST (224 bytes). ITEMLOT_ITEMCATEGORY: 0=None 1=Good 2=Weapon
# 3=Armor 4=Accessory 5=Relic 6=CustomWeapon 7=ItemTable(nested lot).
ITEM_LOT_PARAM_COLS = (
    [f"lotItemId{i:02d}" for i in range(1, 9)]
    + [f"lotItemCategory{i:02d}" for i in range(1, 9)]
    + [f"lotItemBasePoint{i:02d}" for i in range(1, 9)]
    + [f"cumulateLotPoint{i:02d}" for i in range(1, 9)]
    + [f"lotItemNum{i:02d}" for i in range(1, 9)]
    + ["getItemFlagId"]
)

# SHOP_LINEUP_PARAM (64 bytes). SHOP_LINEUP_EQUIPTYPE: 0=Weapon 1=Protector
# 2=Accessory 3=Good 4=Relic 5=ItemTable(rolls a lot) 6=CustomWeapon.
SHOP_LINEUP_PARAM_COLS = [
    "equipId", "value", "mtrlId", "eventFlag_forStock", "eventFlag_forRelease",
    "sellQuantity", "equipType", "costType", "setNum",
    "value_Add", "value_Magnification", "iconId", "nameMsgId", "menuTitleMsgId",
]

# EQUIP_PARAM_GOODS_ST (224 bytes) — focused subset of leading fields.
EQUIP_PARAM_GOODS_COLS = [
    "refId_default", "sellValue", "sortId", "goodsType", "refCategory",
    "goodsUseAnim", "maxNum", "refId_1", "vagrantItemLotId",
]


def _parse_item_lot_param(row: bytes) -> dict[str, str]:
    """224-byte ITEMLOT_PARAM_ST row -> CSV column values (8 weighted slots)."""
    ids = struct.unpack_from("<8i", row, 0x00)
    cats = struct.unpack_from("<8i", row, 0x20)
    base = struct.unpack_from("<8H", row, 0x40)
    cumulate = struct.unpack_from("<8H", row, 0x50)
    nums = struct.unpack_from("<8B", row, 0x8A)
    get_item_flag = struct.unpack_from("<I", row, 0x80)[0]
    out: dict[str, str] = {}
    for i in range(8):
        out[f"lotItemId{i + 1:02d}"] = str(ids[i])
        out[f"lotItemCategory{i + 1:02d}"] = str(cats[i])
        out[f"lotItemBasePoint{i + 1:02d}"] = str(base[i])
        out[f"cumulateLotPoint{i + 1:02d}"] = str(cumulate[i])
        out[f"lotItemNum{i + 1:02d}"] = str(nums[i])
    out["getItemFlagId"] = str(get_item_flag)
    return out


def _parse_shop_lineup_param(row: bytes) -> dict[str, str]:
    """64-byte SHOP_LINEUP_PARAM row -> CSV column values."""
    equip_id, value, mtrl_id = struct.unpack_from("<3i", row, 0x04)
    stock, release = struct.unpack_from("<2I", row, 0x10)
    sell_qty = struct.unpack_from("<h", row, 0x18)[0]
    value_add = struct.unpack_from("<i", row, 0x20)[0]
    value_mag = struct.unpack_from("<f", row, 0x24)[0]
    icon_id, name_msg, menu_title = struct.unpack_from("<3i", row, 0x28)
    return {
        "equipId": str(equip_id),
        "value": str(value),
        "mtrlId": str(mtrl_id),
        "eventFlag_forStock": str(stock),
        "eventFlag_forRelease": str(release),
        "sellQuantity": str(sell_qty),
        "equipType": str(row[0x1B]),
        "costType": str(row[0x1C]),
        "setNum": str(struct.unpack_from("<H", row, 0x1E)[0]),
        "value_Add": str(value_add),
        "value_Magnification": f"{value_mag:g}",
        "iconId": str(icon_id),
        "nameMsgId": str(name_msg),
        "menuTitleMsgId": str(menu_title),
    }


def _parse_equip_param_goods(row: bytes) -> dict[str, str]:
    """224-byte EQUIP_PARAM_GOODS_ST row -> CSV column values (leading subset)."""
    ref_default = struct.unpack_from("<i", row, 0x04)[0]
    sell_value = struct.unpack_from("<i", row, 0x14)[0]
    sort_id = struct.unpack_from("<i", row, 0x20)[0]
    max_num = struct.unpack_from("<h", row, 0x3A)[0]
    ref_1 = struct.unpack_from("<i", row, 0x4C)[0]
    vagrant_lot = struct.unpack_from("<i", row, 0x54)[0]
    return {
        "refId_default": str(ref_default),
        "sellValue": str(sell_value),
        "sortId": str(sort_id),
        "goodsType": str(row[0x3E]),
        "refCategory": str(row[0x3F]),
        "goodsUseAnim": str(row[0x42]),
        "maxNum": str(max_num),
        "refId_1": str(ref_1),
        "vagrantItemLotId": str(vagrant_lot),
    }


# CSV emitters — ONLY these params are written to resources/param/ (game_data_version
# hashes those CSVs). The acquisition parsers above (_parse_shop_lineup_param etc.) are
# intentionally NOT registered here: they're used in-memory by generate_relic_lots_json.
PARAM_PARSERS: dict[str, tuple[list[str], callable]] = {
    "EquipParamAntique": (EQUIP_PARAM_ANTIQUE_COLS, _parse_equip_param_antique),
    "AttachEffectParam": (ATTACH_EFFECT_PARAM_COLS, _parse_attach_effect_param),
    "AttachEffectTableParam": (ATTACH_EFFECT_TABLE_PARAM_COLS, _parse_attach_effect_table_param),
    "AntiqueStandParam": (ANTIQUE_STAND_PARAM_COLS, _parse_antique_stand_param),
}


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def write_param_csv(
    rows: list[tuple[int, bytes]],
    columns: list[str],
    row_parser: callable,
    output_path: Path,
) -> None:
    """Write a PARAM as CSV with trailing comma after last column."""
    header = "ID,Name," + ",".join(columns) + ","
    lines = [header]
    for row_id, row_data in rows:
        vals = row_parser(row_data)
        parts = [str(row_id), ""]  # Name is always empty
        for col in columns:
            parts.append(vals[col])
        lines.append(",".join(parts))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# FMG parsing
# ---------------------------------------------------------------------------


def parse_fmg(data: bytes) -> list[tuple[int, str | None]]:
    """Parse an FMG binary (DarkSouls3/wide format). Returns sorted (id, text) pairs."""
    # Header
    # byte 0: 0, byte 1: big_endian, byte 2: version, byte 3: 0
    big_endian = bool(data[1])
    version = data[2]
    wide = version == 2  # DarkSouls3

    endian = ">" if big_endian else "<"

    # byte 4: file_size (i32)
    # byte 8: unicode (bool)
    # byte 9-11: pad
    group_count = struct.unpack_from(f"{endian}I", data, 12)[0]
    string_count = struct.unpack_from(f"{endian}I", data, 16)[0]

    pos = 20
    if wide:
        # assert 0xFF (i32)
        pos += 4
        string_offsets_offset = struct.unpack_from(f"{endian}q", data, pos)[0]
        pos += 8
        # assert 0 (i64)
        pos += 8
    else:
        string_offsets_offset = struct.unpack_from(f"{endian}I", data, pos)[0]
        pos += 4
        # assert 0 (i32)
        pos += 4

    # Parse groups
    entries: list[tuple[int, str | None]] = []
    for _ in range(group_count):
        offset_index = struct.unpack_from(f"{endian}I", data, pos)[0]
        first_id = struct.unpack_from(f"{endian}i", data, pos + 4)[0]
        last_id = struct.unpack_from(f"{endian}i", data, pos + 8)[0]
        pos += 12
        if wide:
            pos += 4  # assert 0

        # Read string offsets for this group
        for j in range(last_id - first_id + 1):
            str_idx = offset_index + j
            if wide:
                str_off = struct.unpack_from(f"{endian}q", data, string_offsets_offset + str_idx * 8)[0]
            else:
                str_off = struct.unpack_from(f"{endian}I", data, string_offsets_offset + str_idx * 4)[0]

            entry_id = first_id + j
            if str_off > 0:
                text = _read_utf16_at(data, str_off)
            else:
                text = None

            entries.append((entry_id, text))

    return sorted(entries, key=lambda e: e[0])


# ---------------------------------------------------------------------------
# FMG XML writer
# ---------------------------------------------------------------------------


def write_fmg_xml(
    entries: list[tuple[int, str | None]],
    fmg_filename: str,
    output_path: Path,
) -> None:
    """Write FMG entries as XML matching the existing format."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<fmg>",
        f"<filename>{fmg_filename}</filename>",
        "<compression>None</compression>",
        "<version>DarkSouls3</version>",
        "<bigendian>False</bigendian>",
        "<entries>",
    ]
    for entry_id, text in entries:
        if text is None:
            lines.append(f'<text id="{entry_id}">%null%</text>')
        else:
            lines.append(f'<text id="{entry_id}">{xml_escape(text)}</text>')
    lines.append("</entries>")
    lines.append("</fmg>")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Match existing format: UTF-8 BOM prefix, no trailing newline
    output_path.write_bytes(b"\xef\xbb\xbf" + "\n".join(lines).encode("utf-8"))


# ---------------------------------------------------------------------------
# RSA / BHD5 / BDT helpers
# ---------------------------------------------------------------------------


def _rsa_decrypt_bhd(encrypted: bytes, pem: str) -> bytes:
    """RSA raw-decrypt (no padding) a BHD file using a PKCS#1 public key."""
    key = load_pem_public_key(pem.encode())
    n = key.public_numbers().n
    e = key.public_numbers().e
    key_bytes = (n.bit_length() + 7) // 8  # 256 for RSA-2048
    output_block_size = key_bytes - 1  # 255

    result = bytearray()
    for i in range(0, len(encrypted), key_bytes):
        block = encrypted[i : i + key_bytes]
        c = int.from_bytes(block, "big")
        m = pow(c, e, n)
        result.extend(m.to_bytes(output_block_size, "big"))

    return bytes(result)


def bhd5_path_hash(path: str) -> int:
    """Compute From Software's 64-bit path hash for ER/NR BHD5 lookups.

    Uses PRIME64=0x85 and ulong arithmetic (matching Smithbox BhdDictionary.ComputeHash).
    """
    hashable = path.strip().replace("\\", "/").lower()
    if not hashable.startswith("/"):
        hashable = "/" + hashable
    h = 0
    for c in hashable:
        h = (h * 0x85 + ord(c)) & 0xFFFFFFFFFFFFFFFF
    return h


class BHD5FileHeader:
    __slots__ = ("file_name_hash", "padded_file_size", "unpadded_file_size",
                 "file_offset", "aes_key", "aes_ranges")

    def __init__(self):
        self.file_name_hash: int = 0
        self.padded_file_size: int = 0
        self.unpadded_file_size: int = 0
        self.file_offset: int = 0
        self.aes_key: bytes | None = None
        self.aes_ranges: list[tuple[int, int]] = []


def parse_bhd5(data: bytes) -> list[BHD5FileHeader]:
    """Parse a decrypted BHD5 header. Returns all file headers."""
    assert data[:4] == b"BHD5", f"Not BHD5: {data[:4]!r}"

    # byte 4: big_endian indicator (0xFF = LE for NR)
    # We read as LE (standard for NR)
    # byte 5: unk05, byte 6: 0, byte 7: 0
    # offset 8: 1 (i32)
    # offset 12: file_size (i32)
    bucket_count = struct.unpack_from("<I", data, 16)[0]
    buckets_offset = struct.unpack_from("<I", data, 20)[0]

    # Salt (after main header at offset 24)
    salt_length = struct.unpack_from("<I", data, 24)[0]
    # salt = data[28 : 28 + salt_length]  # Not needed for our purposes

    headers: list[BHD5FileHeader] = []

    pos = buckets_offset
    for _ in range(bucket_count):
        file_header_count = struct.unpack_from("<I", data, pos)[0]
        file_headers_offset = struct.unpack_from("<I", data, pos + 4)[0]
        pos += 8

        for j in range(file_header_count):
            fh_pos = file_headers_offset + j * 40  # ER/NR FileHeader = 40 bytes

            fh = BHD5FileHeader()
            fh.file_name_hash = struct.unpack_from("<Q", data, fh_pos)[0]
            fh.padded_file_size = struct.unpack_from("<i", data, fh_pos + 8)[0]
            fh.unpadded_file_size = struct.unpack_from("<i", data, fh_pos + 12)[0]
            fh.file_offset = struct.unpack_from("<q", data, fh_pos + 16)[0]
            sha_hash_offset = struct.unpack_from("<q", data, fh_pos + 24)[0]
            aes_key_offset = struct.unpack_from("<q", data, fh_pos + 32)[0]

            if aes_key_offset != 0:
                fh.aes_key = data[aes_key_offset : aes_key_offset + 16]
                range_count = struct.unpack_from("<I", data, aes_key_offset + 16)[0]
                for k in range(range_count):
                    roff = aes_key_offset + 20 + k * 16
                    start = struct.unpack_from("<q", data, roff)[0]
                    end = struct.unpack_from("<q", data, roff + 8)[0]
                    fh.aes_ranges.append((start, end))

            headers.append(fh)

    return headers


def _aes_ecb_decrypt_ranges(file_data: bytearray, key: bytes, ranges: list[tuple[int, int]]) -> None:
    """Decrypt specified byte ranges in-place with AES-128-ECB."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()

    for start, end in ranges:
        if start == -1 or end == -1 or start == end:
            continue
        length = end - start
        decrypted = decryptor.update(file_data[start : start + length])
        file_data[start : start + length] = decrypted


def read_bdt_file(bdt_path: Path, fh: BHD5FileHeader) -> bytes:
    """Read and decrypt a file from a BDT archive using its BHD5 file header."""
    with open(bdt_path, "rb") as f:
        f.seek(fh.file_offset)
        data = bytearray(f.read(fh.padded_file_size))

    if fh.aes_key and fh.aes_ranges:
        _aes_ecb_decrypt_ranges(data, fh.aes_key, fh.aes_ranges)

    return bytes(data)


# ---------------------------------------------------------------------------
# JSON generators
# ---------------------------------------------------------------------------


def generate_items_json(
    antique_rows: list[tuple[int, bytes]],
    antique_names: dict[int, str | None],
    output_path: Path,
) -> None:
    """Generate items.json from EquipParamAntique + en_US AntiqueName FMG."""
    items: dict[str, dict] = {}
    for row_id, row_data in antique_rows:
        name = antique_names.get(row_id)
        if name is None:
            continue
        relic_color = row_data[4]  # u8 relicColor at byte 4
        color = COLOR_MAP[relic_color] if 0 <= relic_color < len(COLOR_MAP) else None
        items[str(row_id)] = {"name": name, "color": color}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def generate_effects_json(
    effect_rows: list[tuple[int, bytes]],
    effect_names: dict[int, str | None],
    output_path: Path,
) -> None:
    """Generate effects.json from AttachEffectParam + en_US AttachEffectName FMG."""
    effects: dict[str, dict] = {}
    for row_id, row_data in effect_rows:
        name = effect_names.get(row_id)
        if name is None:
            continue
        effects[str(row_id)] = {"name": name}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(effects, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# relic_lots.json — flatstone purchase acquisition odds (Phase 2) — EXACT
# ---------------------------------------------------------------------------
#
# A flatstone bought from the Small Jar Bazaar generates ONE random relic:
# random color + random tier (Delicate/Polished/Grand = 1/2/3 effect slots) +
# rolled effects. The FULL acquisition chain lives inside regulation.bin
# (reverse-engineered 2026-07; byte-level trace in tools/RELIC_GENERATION_RE.md):
#
#   ShopLineupParam row (equipType @0x1B == 5 "Item Table"; equipId @0x04)
#      -> ItemTableParam table whose row-id == that equipId (ids repeat; each
#         row is ONE weighted entry) — fields we read per row:
#           itemCategory @0x04 (5 = Relic leaf, 7 = nested ItemTable),
#           itemId       @0x08 (relic template id, or nested table id),
#           chanceWeight @0x0C (int16, low 16 bits; @0x0E is a separate =1 field)
#      -> EquipParamAntique relic templates (the rolled relic)
#
# equipType=5 literally means "Item Table" -> ItemTableParam (NOT ItemLotParam,
# which is why lot ids 49020/49300/... were never found in ItemLotParam_map/enemy).
# The tier split is therefore EXACT, read straight from ItemTableParam chanceWeight:
#   * Scenic (tables 49000 v1.02 / 49020 v1.03): flat table, per-template weights
#     45/35/20 by tier -> Delicate 45% / Polished 35% / Grand 20% (uniform in tier).
#   * Deep   (tables 49100 v1.02 / 49300 v1.03): nested (cat=7) into per-tier
#     sub-tables weighted 100/500/400 -> Delicate 10% / Polished 50% / Grand 40%
#     (per-template weights are NON-uniform within a tier — real sub-tier structure).
#   * Large scenic (49010/49030) & Deep large (49200/49400): Grand-only tables.
# Color is UNIFORM 25% in every pool: summing ItemTableParam weights per color
# gives exactly 300/1200 (= 25%) for each of Red/Blue/Yellow/Green, and the tables
# reference NO White templates. Corroborated by the EquipParamAntique color x tier
# grid symmetry and by community EFFECT calculators (slavone
# github.com/slavone/nighreign_relic_calculator + ip1259
# github.com/ip1259/Elden-Ring-Nightreign-Legal-Relic-Generator) which confirm
# 100-135 = v1.02 / 200-235 = v1.03 store templates but do NOT model the tier
# meta-roll — that is this ItemTableParam finding.

# ITEMLOT_ITEMCATEGORY (Smithbox NR paramdef): the ItemTableParam entry kinds we
# follow. 5 = a relic template (EquipParamAntique id); 7 = a nested Item Table.
_ITEM_TABLE_CAT_RELIC = 5
_ITEM_TABLE_CAT_TABLE = 7

# key -> (is_deep, version). The two base keys follow the generator's _lot_key
# (f"{'deep' if is_deep else 'scenic'}_{version}"); the large_* / deep_large_* keys
# reuse (is_deep, version) and are distinguished by their own ItemTableParam table
# id (see RELIC_LOT_SHOP_EQUIPID). Order here sets the JSON key order.
RELIC_LOT_POOLS: dict[str, tuple[bool, str]] = {
    "scenic_1.02":       (False, "1.02"),
    "scenic_1.03":       (False, "1.03"),
    "deep_1.02":         (True, "1.02"),
    "deep_1.03":         (True, "1.03"),
    "large_scenic_1.02": (False, "1.02"),
    "large_scenic_1.03": (False, "1.03"),
    "deep_large_1.02":   (True, "1.02"),
    "deep_large_1.03":   (True, "1.03"),
}

# Each flatstone is a ShopLineupParam row (equipType @0x1B == 5 "Item Table") whose
# equipId (@0x04) is BOTH (a) the ItemTableParam table-id we roll for templates and
# (b) the key to that row's price (value @0x08) + currency (costType @0x1C). Verified
# ShopLineupParam rows (regulation.bin): 10110/10112->49000, 10114->49020,
# 10111/10113->49100, 10115->49300, 13100/13101->49010, 13102->49030,
# 13110/13111->49200, 13112->49400. Both patch versions of a flatstone share the price.
RELIC_LOT_SHOP_EQUIPID: dict[str, int] = {
    "scenic_1.02": 49000, "scenic_1.03": 49020,
    "deep_1.02": 49100, "deep_1.03": 49300,
    "large_scenic_1.02": 49010, "large_scenic_1.03": 49030,
    "deep_large_1.02": 49200, "deep_large_1.03": 49400,
}
# Known in-game prices, used only if a shop row is missing/zero. These EXACTLY match
# the ShopLineupParam value/costType read at runtime (verified: scenic 600 Murk,
# deep 1800 Murk [costType 4]; large 5 Sigil, deep_large 10 Sigil [costType 5]).
RELIC_LOT_FALLBACK_COST: dict[str, tuple[int, str]] = {
    "scenic_1.02": (600, "Murk"), "scenic_1.03": (600, "Murk"),
    "deep_1.02": (1800, "Murk"), "deep_1.03": (1800, "Murk"),
    "large_scenic_1.02": (5, "Sovereign Sigil"), "large_scenic_1.03": (5, "Sovereign Sigil"),
    "deep_large_1.02": (10, "Sovereign Sigil"), "deep_large_1.03": (10, "Sovereign Sigil"),
}
# SHOP_LINEUP_COSTTYPE enum (Smithbox NR): the currencies the flatstones use.
_SHOP_COSTTYPE_CURRENCY = {0: "Runes", 4: "Murk", 5: "Sovereign Sigil"}
_SHOP_EQUIPTYPE_ITEM_TABLE = 5  # SHOP_LINEUP_EQUIPTYPE: 5 = Item Table (rolls a lot)


def _flatstone_shop_prices(
    shop_rows: list[tuple[int, bytes]] | None,
) -> dict[int, tuple[int, int]]:
    """Map flatstone lot equipId -> (price, costType) from equipType=5 shop rows."""
    prices: dict[int, tuple[int, int]] = {}
    if not shop_rows:
        return prices
    for _rid, row in shop_rows:
        vals = _parse_shop_lineup_param(row)
        if int(vals["equipType"]) != _SHOP_EQUIPTYPE_ITEM_TABLE:
            continue
        equip_id = int(vals["equipId"])
        if equip_id not in prices:  # first (all copies of a flatstone share the price)
            prices[equip_id] = (int(vals["value"]), int(vals["costType"]))
    return prices


def _index_item_table(
    item_table_rows: list[tuple[int, bytes]],
) -> dict[int, list[bytes]]:
    """Group ItemTableParam rows by table id. Ids repeat: each row is one entry."""
    by_id: dict[int, list[bytes]] = {}
    for rid, row in item_table_rows:
        by_id.setdefault(rid, []).append(row)
    return by_id


def _resolve_item_table(
    by_id: dict[int, list[bytes]], table_id: int
) -> list[tuple[int, int]]:
    """Flatten an ItemTableParam table to ``[(relic_template_id, weight)]``.

    Follows the shop's Item Table exactly (see the module comment above): each row is
    itemCategory @0x04, itemId @0x08, chanceWeight @0x0C (int16, low 16 bits).
    category=5 (``_ITEM_TABLE_CAT_RELIC``) is a relic-template leaf; category=7
    (``_ITEM_TABLE_CAT_TABLE``) is a nested Item Table resolved recursively. Nested
    siblings are brought to a common multiple of their sub-totals so the returned
    integer weights preserve the EXACT within-pool probability ratios (a flat table
    keeps its literal chanceWeights, e.g. scenic 45/35/20).
    """
    children: list[tuple[list[tuple[int, int]], int, int]] = []  # (leaves, subtotal, w)
    for row in by_id.get(table_id, []):
        category = struct.unpack_from("<i", row, 0x04)[0]
        item_id = struct.unpack_from("<i", row, 0x08)[0]
        weight = struct.unpack_from("<h", row, 0x0C)[0]
        if category == _ITEM_TABLE_CAT_RELIC:
            children.append(([(item_id, 1)], 1, weight))
        elif category == _ITEM_TABLE_CAT_TABLE:
            leaves = _resolve_item_table(by_id, item_id)
            subtotal = sum(w for _, w in leaves)
            children.append((leaves, subtotal, weight))
        # any other category is not expected for flatstones and is ignored
    common = 1
    for _leaves, subtotal, _w in children:
        if subtotal:
            common = math.lcm(common, subtotal)
    out: dict[int, int] = {}
    for leaves, subtotal, weight in children:
        if not subtotal:
            continue
        factor = weight * (common // subtotal)
        for leaf, w in leaves:
            out[leaf] = out.get(leaf, 0) + w * factor
    return list(out.items())


def generate_relic_lots_json(
    antique_rows: list[tuple[int, bytes]],
    output_path: Path,
    shop_rows: list[tuple[int, bytes]] | None = None,
    item_table_rows: list[tuple[int, bytes]] | None = None,
) -> None:
    """Generate relic_lots.json (EXACT flatstone purchase odds) from ItemTableParam.

    Emits ``entries`` as ``[real_id, weight]`` pairs read straight from the game:
    each flatstone's ShopLineupParam row (equipType=5) names an ItemTableParam table
    (``RELIC_LOT_SHOP_EQUIPID``) that enumerates the EquipParamAntique templates with
    exact chanceWeights. ``_resolve_item_table`` flattens it (following nested tables)
    to per-template integer weights whose ratios equal the in-game probabilities — an
    EXACT color+tier+template distribution, so ``approximate`` is False.

    ``shop_rows`` (in-memory ShopLineupParam) supplies price/currency; a documented
    fallback (matching the verified in-game values) is used only if absent. When
    ``item_table_rows`` is missing a pool is skipped (no approximation is emitted).
    """
    antique_by_id = {rid: row for rid, row in antique_rows}

    def tier_of(real_id: int) -> int:
        # attachEffectTableId_1/2/3 at offsets 16/20/24 (see _parse_equip_param_antique);
        # tier = number of non-(-1) primary effect slots (1=Delicate/2=Polished/3=Grand).
        row = antique_by_id.get(real_id)
        if row is None:
            return 0
        e1, e2, e3 = struct.unpack_from("<3i", row, 16)
        return sum(1 for x in (e1, e2, e3) if x != -1)

    shop_prices = _flatstone_shop_prices(shop_rows)
    by_table = _index_item_table(item_table_rows) if item_table_rows else {}

    result: dict[str, dict] = {}
    for key, (is_deep, version) in RELIC_LOT_POOLS.items():
        table_id = RELIC_LOT_SHOP_EQUIPID[key]
        leaves = _resolve_item_table(by_table, table_id) if by_table else []
        if not leaves:
            print(f"  WARNING: ItemTableParam table {table_id} ({key}) empty/missing; "
                  "skipping (no odds emitted)")
            continue

        # Reduce to smallest integers (within-pool ratios preserved exactly).
        pool_gcd = 0
        for _rid, w in leaves:
            pool_gcd = math.gcd(pool_gcd, w)
        if pool_gcd > 1:
            leaves = [(rid, w // pool_gcd) for rid, w in leaves]
        leaves.sort(key=lambda e: (tier_of(e[0]), e[0]))  # group by tier for readability

        # tier_weights metadata: aggregate weight per tier, normalized to percent
        # (exact integers for the shipped data: scenic 45/35/20, deep 10/50/40).
        total = sum(w for _, w in leaves)
        tier_agg: dict[int, int] = {}
        for rid, w in leaves:
            tier_agg[tier_of(rid)] = tier_agg.get(tier_of(rid), 0) + w
        tier_weights: dict[str, int | float] = {}
        for t in sorted(tier_agg):
            pct = tier_agg[t] * 100
            tier_weights[str(t)] = pct // total if pct % total == 0 else round(pct / total, 4)

        # Purchase price from the flatstone's ShopLineupParam row (equipType=5),
        # falling back to the verified in-game value when unavailable/zero.
        equip_id = RELIC_LOT_SHOP_EQUIPID.get(key)
        price = shop_prices.get(equip_id) if equip_id is not None else None
        if price and price[0] > 0:
            buy_cost = price[0]
            buy_currency = _SHOP_COSTTYPE_CURRENCY.get(price[1], f"costType{price[1]}")
        else:
            buy_cost, buy_currency = RELIC_LOT_FALLBACK_COST[key]

        grand_only = set(tier_agg) == {3}
        if grand_only:
            note = (
                f"EXACT. Large flatstone: ShopLineupParam equipType=5 equipId={table_id} "
                f"-> ItemTableParam {table_id}; Grand-only, uniform 25% color."
            )
        else:
            note = (
                f"EXACT. ShopLineupParam equipType=5 equipId={table_id} -> ItemTableParam "
                f"{table_id} chanceWeights; uniform 25% color; tier + per-template weights "
                "read from the game (Delicate/Polished/Grand)."
            )
        result[key] = {
            "is_deep": is_deep,
            "version": version,
            "approximate": False,
            "buy_cost": buy_cost,
            "buy_currency": buy_currency,
            "note": note,
            "tier_weights": tier_weights,
            "entries": [[rid, w] for rid, w in leaves],
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate nrplanner/resources/ from Nightreign game data."
    )
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=Path(GAME_DIR_DEFAULT),
        help="Path to the Nightreign Game/ directory",
    )
    args = parser.parse_args()
    game_dir: Path = args.game_dir

    if not game_dir.is_dir():
        print(f"ERROR: Game directory not found: {game_dir}", file=sys.stderr)
        sys.exit(1)

    resources_dir = Path(__file__).resolve().parent.parent / "nrplanner" / "resources"
    param_dir = resources_dir / "param"
    text_dir = resources_dir / "text"
    json_dir = resources_dir / "json"

    # -----------------------------------------------------------------------
    # Phase 1: regulation.bin -> CSV params
    # -----------------------------------------------------------------------
    print("=== Phase 1: regulation.bin -> CSV params ===")
    regulation_path = game_dir / "regulation.bin"
    if not regulation_path.exists():
        print(f"ERROR: {regulation_path} not found", file=sys.stderr)
        sys.exit(1)

    raw = regulation_path.read_bytes()

    # Check if already BND4 (unencrypted)
    if raw[:4] == b"BND4":
        bnd4_data = raw
    else:
        # AES-256-CBC decrypt: first 16 bytes = IV
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7

        iv = raw[:16]
        ciphertext = raw[16:]
        # Pad to 16-byte boundary if needed
        remainder = len(ciphertext) % 16
        if remainder:
            ciphertext += b"\x00" * (16 - remainder)

        cipher = Cipher(algorithms.AES(NR_REGULATION_KEY), modes.CBC(iv))
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(ciphertext) + decryptor.finalize()
        bnd4_data = decrypted

    # DCX decompress if needed
    if bnd4_data[:4] in (b"DCX\0", b"DCP\0"):
        print("  Decompressing DCX...")
        bnd4_data = dcx_decompress(bnd4_data, game_dir)

    # Parse BND4
    print("  Parsing BND4...")
    bnd4_files = parse_bnd4(bnd4_data, game_dir)
    print(f"  Found {len(bnd4_files)} files in regulation.bin")

    # Build lookup: stem -> (id, name, data)
    param_lookup: dict[str, bytes] = {}
    for file_id, name, data in bnd4_files:
        stem = name.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        param_lookup[stem] = data

    # Store parsed row data for JSON generation
    parsed_params: dict[str, list[tuple[int, bytes]]] = {}

    for param_name, param_filename in TARGET_PARAMS.items():
        if param_filename not in param_lookup:
            print(f"  WARNING: {param_filename} not found in regulation.bin")
            continue

        print(f"  Parsing {param_name}...")
        param_data = param_lookup[param_filename]
        rows = parse_param_rows(param_data)
        parsed_params[param_name] = rows

        columns, row_parser = PARAM_PARSERS[param_name]
        csv_path = param_dir / f"{param_name}.csv"
        write_param_csv(rows, columns, row_parser, csv_path)
        print(f"    -> {csv_path.relative_to(resources_dir)} ({len(rows)} rows)")

    # -----------------------------------------------------------------------
    # Phase 2: BHD/BDT archives -> FMG XMLs
    # -----------------------------------------------------------------------
    print("\n=== Phase 2: BHD/BDT archives -> FMG XMLs ===")

    # Decrypt and parse all BHD5 headers, build hash -> (archive_dir, archive_name, fh)
    hash_index: dict[int, tuple[Path, str, BHD5FileHeader]] = {}

    # Main archives (Game/data*.bhd)
    for archive_name, pem in NR_ARCHIVE_KEYS.items():
        bhd_path = game_dir / f"{archive_name}.bhd"
        if not bhd_path.exists():
            print(f"  WARNING: {bhd_path.name} not found, skipping")
            continue

        print(f"  Decrypting {bhd_path.name}...")
        encrypted = bhd_path.read_bytes()
        decrypted = _rsa_decrypt_bhd(encrypted, pem)
        headers = parse_bhd5(decrypted)
        print(f"    Found {len(headers)} file entries")

        for fh in headers:
            hash_index[fh.file_name_hash] = (game_dir, archive_name, fh)

    # sd/ subdirectory archives (Game/sd/sd*.bhd)
    sd_dir = game_dir / "sd"
    for archive_name, pem in NR_SD_ARCHIVE_KEYS.items():
        bhd_path = sd_dir / f"{archive_name}.bhd"
        if not bhd_path.exists():
            print(f"  WARNING: sd/{bhd_path.name} not found, skipping")
            continue

        print(f"  Decrypting sd/{bhd_path.name}...")
        encrypted = bhd_path.read_bytes()
        decrypted = _rsa_decrypt_bhd(encrypted, pem)
        headers = parse_bhd5(decrypted)
        print(f"    Found {len(headers)} file entries")

        for fh in headers:
            hash_index[fh.file_name_hash] = (sd_dir, archive_name, fh)

    # For each locale, extract FMGs from item_dlc01.msgbnd.dcx
    # (In NR, item_dlc01.msgbnd.dcx contains ALL FMGs: base + DLC)
    en_us_fmgs: dict[str, dict[int, str | None]] = {}

    for locale, game_locale in LOCALE_MAP.items():
        print(f"\n  [{locale}]")

        bnd_path = f"msg/{game_locale}/item_dlc01.msgbnd.dcx"
        path_h = bhd5_path_hash(bnd_path)

        if path_h not in hash_index:
            print(f"    item_dlc01: hash {path_h:#018x} not found in any archive")
            continue

        archive_dir, archive_name, fh = hash_index[path_h]
        bdt_path = archive_dir / f"{archive_name}.bdt"

        print(f"    item_dlc01 -> {archive_name} (offset {fh.file_offset:#x})")
        raw_file = read_bdt_file(bdt_path, fh)

        # DCX decompress
        if raw_file[:4] in (b"DCX\0", b"DCP\0"):
            raw_file = dcx_decompress(raw_file, game_dir)

        # Parse BND4
        msgbnd_files = parse_bnd4(raw_file, game_dir)

        # Extract target FMGs
        for fmg_stem in FMG_STEMS:
            target_name = f"{fmg_stem}.fmg"
            fmg_data = None
            for _, fname, fdata in msgbnd_files:
                basename = fname.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
                if basename.lower() == target_name.lower():
                    fmg_data = fdata
                    break

            if fmg_data is None:
                print(f"      {target_name}: NOT FOUND")
                continue

            entries = parse_fmg(fmg_data)
            xml_path = text_dir / locale / f"{fmg_stem}.fmg.xml"
            write_fmg_xml(entries, target_name, xml_path)
            print(f"      {target_name}: {len(entries)} entries")

            # Store en_US data for JSON generation
            if locale == "en_US":
                en_us_fmgs[fmg_stem] = {
                    eid: text for eid, text in entries
                }

    # -----------------------------------------------------------------------
    # Phase 3: JSON files
    # -----------------------------------------------------------------------
    print("\n=== Phase 3: JSON files ===")

    # items.json: EquipParamAntique + AntiqueName (en_US)
    if "EquipParamAntique" in parsed_params:
        # Merge base + DLC antique names
        antique_names: dict[int, str | None] = {}
        for stem in ["AntiqueName", "AntiqueName_dlc01"]:
            if stem in en_us_fmgs:
                for eid, text in en_us_fmgs[stem].items():
                    if text is not None:
                        antique_names[eid] = text

        items_path = json_dir / "items.json"
        generate_items_json(parsed_params["EquipParamAntique"], antique_names, items_path)
        print(f"  items.json: {items_path.relative_to(resources_dir)}")
    else:
        print("  WARNING: EquipParamAntique not available, skipping items.json")

    # effects.json: AttachEffectParam + AttachEffectName (en_US)
    if "AttachEffectParam" in parsed_params:
        effect_names: dict[int, str | None] = {}
        for stem in ["AttachEffectName", "AttachEffectName_dlc01"]:
            if stem in en_us_fmgs:
                for eid, text in en_us_fmgs[stem].items():
                    if text is not None:
                        effect_names[eid] = text

        effects_path = json_dir / "effects.json"
        generate_effects_json(parsed_params["AttachEffectParam"], effect_names, effects_path)
        print(f"  effects.json: {effects_path.relative_to(resources_dir)}")
    else:
        print("  WARNING: AttachEffectParam not available, skipping effects.json")

    # relic_lots.json: EXACT flatstone purchase odds (ShopLineupParam -> ItemTableParam
    # -> EquipParamAntique). ShopLineupParam and ItemTableParam are parsed in-memory here
    # ONLY (never written as CSVs — ItemTableParam is 34730 rows).
    if "EquipParamAntique" in parsed_params:
        shop_filename = ACQUISITION_PARAMS["ShopLineupParam"]
        shop_data = param_lookup.get(shop_filename)
        shop_rows = parse_param_rows(shop_data) if shop_data else None
        if shop_data is None:
            print("  WARNING: ShopLineupParam not found; using fallback buy_cost values")

        item_table_filename = ACQUISITION_PARAMS["ItemTableParam"]
        item_table_data = param_lookup.get(item_table_filename)
        item_table_rows = parse_param_rows(item_table_data) if item_table_data else None
        if item_table_data is None:
            print("  WARNING: ItemTableParam not found; relic_lots odds cannot be emitted")

        relic_lots_path = json_dir / "relic_lots.json"
        generate_relic_lots_json(
            parsed_params["EquipParamAntique"], relic_lots_path,
            shop_rows=shop_rows, item_table_rows=item_table_rows,
        )
        print(f"  relic_lots.json: {relic_lots_path.relative_to(resources_dir)}")
    else:
        print("  WARNING: EquipParamAntique not available, skipping relic_lots.json")

    print("\nDone!")


if __name__ == "__main__":
    main()
