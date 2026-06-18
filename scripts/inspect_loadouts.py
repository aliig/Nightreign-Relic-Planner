"""Inspect the relic-loadout (preset) region of a Nightreign .sl2 save.

Decrypts each character slot and reports the fixed-size preset array: active vs
empty slot counts and a listing of every saved loadout. Useful for debugging the
loadout write ops (add / delete / rename / overwrite).

Section 3 is ALWAYS exactly 100 preset slots (80 bytes each): active records
(header byte == 0x01) fill the front; empty slots (header == 0x00, with
vessel_id == 0xFFFFFFFF as the marker) follow. Adding a loadout writes into the
first empty slot in place — the array never grows. This tool makes that layout
visible so the invariant (active + empty == 100) is easy to eyeball.

Run:  uv run python scripts/inspect_loadouts.py path/to/NR0000.sl2 [--slot N]
"""
import argparse
import struct
import tempfile
from pathlib import Path

from nrplanner.constants import CHARACTER_NAMES
from nrplanner.save import decrypt_sl2, parse_relics, read_char_name
from nrplanner import vessel_writer as vw


def _hero_name(hero_type: int) -> str:
    """1-based hero_type -> class name (11 == 'All'/universal)."""
    if 1 <= hero_type <= len(CHARACTER_NAMES):
        return CHARACTER_NAMES[hero_type - 1]
    return "All" if hero_type == 11 else f"hero?{hero_type}"


def inspect_slot(blob: bytes) -> None:
    try:
        _, items_end = parse_relics(blob)
        name = read_char_name(blob, items_end) or "(unnamed)"
    except Exception:
        name = "(unreadable)"

    try:
        region = vw._locate_region(blob)
    except vw.RegionNotFoundError:
        print("  no loadout region (not a character slot)")
        return

    _, s3 = vw._walk_vessels(blob, region)
    headers = vw._slot_headers(blob, s3)
    active = sum(1 for h in headers if h == vw._HEADER_ACTIVE)
    empty = vw.MAX_PRESETS - active

    print(f"  character: {name}")
    print(f"  preset array: {active} active / {empty} empty / {vw.MAX_PRESETS} total")

    presets = vw.parse_presets(blob)
    if not presets:
        print("  (no saved loadouts)")
        return
    print(f"  loadouts (newest first):")
    for p in sorted(presets, key=lambda r: r.counter):
        relics = ", ".join(f"{r:08X}" if r else "------" for r in p.relics)
        print(f"    [c={p.counter:>2}] {_hero_name(p.hero_type):<10} "
              f"vessel {p.vessel_id:<6} {p.name!r:<22} relics: {relics}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect a .sl2 save's loadout region.")
    ap.add_argument("save", type=Path, help="path to a .sl2 save file")
    ap.add_argument("--slot", type=int, default=None,
                    help="only inspect this BND4 slot index (0-9); default: all")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        out = decrypt_sl2(args.save, output_dir=tmp)
        for i in range(10):
            if args.slot is not None and i != args.slot:
                continue
            udata = Path(out) / f"USERDATA_{i:02d}"
            if not udata.exists():
                continue
            blob = udata.read_bytes()
            if len(blob) < 0x1000:
                continue
            print(f"\nSlot {i} (USERDATA_{i:02d}):")
            inspect_slot(blob)


if __name__ == "__main__":
    main()
