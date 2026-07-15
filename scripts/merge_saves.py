"""Merge relics + loadout presets from one Nightreign save into another.

Copies every relic that exists in the source save but not the destination
(matched by CONTENT — item_id + effects + curses — because the game renumbers
ga_handles between saves), then copies any loadout presets missing from the
destination, remapping their relic references onto destination handles.

Relics are added by resurrecting ghost ItemState records (see
writer.add_relics); the destination's Murk, unlocks, and all other progression
are untouched. The merged file is written to --out; neither input is modified.

Usage (from repo root):
    uv run python scripts/merge_saves.py SOURCE.sl2 DEST.sl2 --out MERGED.sl2 [--slot 0]
"""
import argparse
import struct
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nrplanner.save import decrypt_sl2, parse_relics, read_char_name
from nrplanner.vessel_writer import add_preset, parse_presets
from nrplanner.writer import add_relics, repack_sl2

RELIC_STATE_SIZE = 80


def content_key(r):
    """Relic identity by content — ga_handles are NOT stable across saves."""
    return (r.item_id, r.effect_1, r.effect_2, r.effect_3,
            r.sec_effect1, r.sec_effect2, r.sec_effect3)


def load_blob(sl2_path: Path, slot: int, tmp: Path, tag: str) -> bytes:
    out = tmp / tag
    decrypt_sl2(sl2_path, out)
    blob_path = out / f"USERDATA_{slot:02d}"
    if not blob_path.exists():
        raise SystemExit(f"{sl2_path}: no USERDATA_{slot:02d} slot")
    return blob_path.read_bytes()


def preset_key(p):
    """Preset identity: name + hero + vessel (timestamps/counters differ)."""
    return (p.name, p.hero_type, p.vessel_id)


def merge(src_blob: bytes, dst_blob: bytes) -> tuple[bytes, list[str]]:
    """Return (merged destination blob, human-readable change log)."""
    log: list[str] = []
    src_relics, _ = parse_relics(src_blob)
    dst_relics, _ = parse_relics(dst_blob)

    # --- relics: source-unique by content multiset ---------------------------
    dst_content = Counter(content_key(r) for r in dst_relics)
    need = Counter(content_key(r) for r in src_relics) - dst_content
    to_copy = []
    remaining = Counter(need)
    for r in src_relics:
        if remaining[content_key(r)] > 0:
            remaining[content_key(r)] -= 1
            to_copy.append(r)

    merged = dst_blob
    new_handle_by_content: dict[tuple, list[int]] = {}
    if to_copy:
        records = [src_blob[r.offset:r.offset + RELIC_STATE_SIZE] for r in to_copy]
        merged, result = add_relics(merged, records)
        for r, h in zip(to_copy, result.added_handles):
            new_handle_by_content.setdefault(content_key(r), []).append(h)
        log.append(f"relics added: {len(result.added_handles)} "
                   f"(ghosts available: {result.ghosts_available}; ItemEntry count "
                   f"{result.entry_count_before} -> {result.entry_count_after})")
    else:
        log.append("relics added: 0 (destination already has every source relic)")

    # --- presets: source-only, with relic handles remapped by content --------
    src_by_handle = {r.ga_handle: r for r in src_relics}
    dst_handles_by_content: dict[tuple, list[int]] = {}
    for r in dst_relics:
        dst_handles_by_content.setdefault(content_key(r), []).append(r.ga_handle)
    for k, handles in new_handle_by_content.items():
        dst_handles_by_content.setdefault(k, []).extend(handles)

    existing = {preset_key(p) for p in parse_presets(dst_blob)}
    for p in parse_presets(src_blob):
        if preset_key(p) in existing:
            continue
        mapped, used = [], set()
        for h in p.relics:
            if h == 0:
                mapped.append(0)
                continue
            src_r = src_by_handle.get(h)
            if src_r is None:
                raise SystemExit(
                    f"preset {p.name!r} references handle 0x{h:08X} "
                    f"not in the source inventory — aborting")
            # Don't map two distinct source relics onto one destination relic
            # within the same preset.
            candidates = [c for c in dst_handles_by_content.get(content_key(src_r), [])
                          if c not in used]
            if not candidates:
                raise SystemExit(
                    f"preset {p.name!r}: no destination relic matches the content of "
                    f"source handle 0x{h:08X} — aborting")
            mapped.append(candidates[0])
            used.add(candidates[0])
        merged, res = add_preset(
            merged, hero_type=p.hero_type, name=p.name, vessel_id=p.vessel_id,
            ga_handles=mapped, timestamp=p.timestamp)
        log.append(f"preset added: {p.name!r} (hero {p.hero_type}, vessel "
                   f"{p.vessel_id}, slot {res.slot_index}, "
                   f"{sum(1 for h in mapped if h)} relics remapped)")

    return merged, log


def verify(src_blob: bytes, dst_blob: bytes, merged: bytes) -> None:
    """Assert the merged blob is exactly destination + source-unique content."""
    assert len(merged) == len(dst_blob), "blob length changed"
    src_c = Counter(content_key(r) for r in parse_relics(src_blob)[0])
    dst_c = Counter(content_key(r) for r in parse_relics(dst_blob)[0])
    got_c = Counter(content_key(r) for r in parse_relics(merged)[0])
    assert got_c == dst_c + (src_c - dst_c), "merged relic contents mismatch"

    src_p = {preset_key(p) for p in parse_presets(src_blob)}
    dst_p = {preset_key(p) for p in parse_presets(dst_blob)}
    got_p = {preset_key(p) for p in parse_presets(merged)}
    assert got_p == dst_p | src_p, "merged preset set mismatch"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path, help=".sl2 to copy relics/presets FROM")
    ap.add_argument("dest", type=Path, help=".sl2 to merge INTO (not modified)")
    ap.add_argument("--out", type=Path, required=True, help="merged .sl2 output path")
    ap.add_argument("--slot", type=int, default=0, help="profile slot (default 0)")
    args = ap.parse_args()

    if args.out.resolve() in (args.source.resolve(), args.dest.resolve()):
        raise SystemExit("--out must not overwrite an input file")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        src_blob = load_blob(args.source, args.slot, tmp, "src")
        dst_blob = load_blob(args.dest, args.slot, tmp, "dst")

        src_name = read_char_name(src_blob, parse_relics(src_blob)[1])
        dst_name = read_char_name(dst_blob, parse_relics(dst_blob)[1])
        print(f"source slot {args.slot}: {src_name!r}, "
              f"{len(parse_relics(src_blob)[0])} relics, "
              f"{len(parse_presets(src_blob))} presets")
        print(f"dest   slot {args.slot}: {dst_name!r}, "
              f"{len(parse_relics(dst_blob)[0])} relics, "
              f"{len(parse_presets(dst_blob))} presets")

        merged, log = merge(src_blob, dst_blob)
        for line in log:
            print(f"  {line}")

        verify(src_blob, dst_blob, merged)
        print(f"verified: {len(parse_relics(merged)[0])} relics, "
              f"{len(parse_presets(merged))} presets in merged blob")

        out_bytes = repack_sl2(args.dest.read_bytes(), {args.slot: merged})
        args.out.write_bytes(out_bytes)
        print(f"wrote {args.out} ({len(out_bytes)} bytes)")


if __name__ == "__main__":
    main()
