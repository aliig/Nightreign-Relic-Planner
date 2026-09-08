"""Read or adjust a character's Murk balance in a Nightreign .sl2 save.

WHY THIS EXISTS -- and why it is a script, not an app feature. The planner
mimics the game 1:1 and never hands out free currency; that fidelity is the
whole product. This tool deliberately sits outside the app, for the one job the
app must not do: restoring Murk the app itself destroyed. An earlier version of
the relic-purchasing path settled the buy/sell economy incorrectly, leaving a
player who trusted it poorer than any real in-game session would have. Putting
that balance back is repair, not an exploit.

Use it for that. It is not a currency faucet.

Mechanics: Murk is a u32 at ``items_end + 0x94 + 52`` inside a character blob
(``nrplanner.writer._MURKS_REL_OFFSET``); this script calls the library's
``read_murks`` / ``adjust_murks`` rather than re-deriving the offset. Writes go
through ``repair_blob`` (heals invariants inherited from pre-2026-08-27 exports)
and ``repack_sl2`` (checksums + re-encryption), exactly like the app's export
paths. A verify pass then re-decrypts what was written and proves nothing but
the balance moved.

Usage
-----
Show every character's balance::

    python scripts/adjust_murk.py show NR0000.sl2

Credit 1,000,000 Murk to slot 0, writing a new file::

    python scripts/adjust_murk.py adjust NR0000.sl2 --slot 0 --delta 1000000 -o out.sl2

Set an exact balance instead::

    python scripts/adjust_murk.py adjust NR0000.sl2 --slot 0 --to 500000 -o out.sl2
"""
import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nrplanner.save import (
    MAX_CHARACTER_SLOTS,
    PROFILE_ENTRY_INDEX,
    decrypt_sl2,
    parse_relics,
    read_char_name,
    slot_occupancy_from_blob,
)
from nrplanner.writer import adjust_murks, read_murks, repack_sl2, repair_blob

U32_MAX = 0xFFFFFFFF


def decrypt_blobs(sl2_path: Path) -> dict[int, bytes]:
    """Decrypt a .sl2 into {BND4 entry index: decrypted blob}."""
    with tempfile.TemporaryDirectory() as tmp:
        decrypt_sl2(sl2_path, tmp)
        blobs = {}
        for f in sorted(Path(tmp).glob("USERDATA_*")):
            blobs[int(f.name.split("_")[1])] = f.read_bytes()
    if not blobs:
        raise ValueError(f"No USERDATA entries decrypted from {sl2_path}.")
    return blobs


def characters(blobs: dict[int, bytes]) -> list[tuple[int, str, int]]:
    """Return (slot index, character name, Murk) for each live character slot.

    Character slots are BND4 entries 0..MAX_CHARACTER_SLOTS-1, and that index
    doubles as the entry index repack_sl2 (and the app's export routes) take.
    Slots the profile entry marks as dead are skipped: an in-game delete leaves
    the character's blob fully intact, so a deleted character would otherwise
    show up here as a phantom.
    """
    profile = blobs.get(PROFILE_ENTRY_INDEX)
    occupancy = slot_occupancy_from_blob(profile) if profile else None

    out = []
    for i in range(MAX_CHARACTER_SLOTS):
        blob = blobs.get(i)
        if blob is None or len(blob) < 0x1000:
            continue
        if occupancy is not None and not occupancy[i]:
            continue
        _, items_end = parse_relics(blob)
        name = read_char_name(blob, items_end)
        if not name:
            continue
        out.append((i, name, read_murks(blob, items_end)))
    return out


def verify(out_path: Path, original: dict[int, bytes], slot: int,
           expected_murk: int) -> None:
    """Re-decrypt the output and prove nothing but the balance moved.

    Every other character blob must be byte-identical to the input. The edited
    blob must keep its length and hold the balance we asked for; a handful of
    further differing bytes is legitimate (repair_blob healing damage inherited
    from an older export) and is reported rather than failed.
    """
    actual = decrypt_blobs(out_path)
    if actual.keys() != original.keys():
        raise AssertionError(
            f"entry set changed: {sorted(original)} -> {sorted(actual)}")

    for index, blob in sorted(actual.items()):
        if index == slot:
            continue
        if blob != original[index]:
            raise AssertionError(
                f"USERDATA_{index:02d} changed but should not have.")

    edited = actual[slot]
    if len(edited) != len(original[slot]):
        raise AssertionError("edited blob changed length.")
    _, items_end = parse_relics(edited)
    got = read_murks(edited, items_end)
    if got != expected_murk:
        raise AssertionError(f"output Murk is {got}, expected {expected_murk}.")

    differing = sum(1 for a, b in zip(edited, original[slot]) if a != b)
    if differing > 4:
        print(f"  note: {differing} bytes differ in the edited blob "
              "(Murk field plus invariants healed by repair_blob).")


def cmd_show(args: argparse.Namespace) -> int:
    rows = characters(decrypt_blobs(Path(args.save)))
    if not rows:
        print("No live characters found.")
        return 1
    for slot, name, murk in rows:
        print(f"  slot {slot}: {name:<20} {murk:>12,} Murk")
    return 0


def cmd_adjust(args: argparse.Namespace) -> int:
    if (args.delta is None) == (args.to is None):
        print("Give exactly one of --delta or --to.", file=sys.stderr)
        return 2

    path = Path(args.save)
    raw = path.read_bytes()
    blobs = decrypt_blobs(path)

    rows = {slot: (name, murk) for slot, name, murk in characters(blobs)}
    if args.slot not in rows:
        print(f"Slot {args.slot} is not a live character. "
              f"Available: {sorted(rows)}", file=sys.stderr)
        return 1

    name, before = rows[args.slot]
    delta = args.delta if args.delta is not None else args.to - before
    expected = max(0, min(before + delta, U32_MAX))

    print(f"  {name} (slot {args.slot}): {before:,} -> {expected:,} Murk "
          f"({delta:+,})")
    if expected != before + delta:
        print("  note: clamped to the u32 range the game stores.")

    new_blob, got_before, after = adjust_murks(blobs[args.slot], delta)
    assert got_before == before and after == expected

    new_blob, repair = repair_blob(new_blob)
    if repair.changed:
        print(f"  repaired: {len(repair.item_id_mirrors_fixed)} stale item_id "
              f"mirror(s); next-acquisition-id "
              f"{repair.next_acq_id_before} -> {repair.next_acq_id_after}")

    out_path = Path(args.out)
    out_path.write_bytes(repack_sl2(raw, {args.slot: new_blob}))
    verify(out_path, blobs, args.slot, expected)
    print(f"  wrote {out_path} (verified)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read or adjust a character's Murk balance in a .sl2 save.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="print each character's Murk balance")
    p_show.add_argument("save")
    p_show.set_defaults(func=cmd_show)

    p_adjust = sub.add_parser("adjust", help="change a character's Murk balance")
    p_adjust.add_argument("save")
    p_adjust.add_argument("--slot", type=int, required=True,
                          help="character slot index (see `show`)")
    p_adjust.add_argument("--delta", type=int,
                          help="signed amount to add (negative to remove)")
    p_adjust.add_argument("--to", type=int, help="exact balance to set")
    p_adjust.add_argument("-o", "--out", required=True, help="output .sl2 path")
    p_adjust.set_defaults(func=cmd_adjust)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
