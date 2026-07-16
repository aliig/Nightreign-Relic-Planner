"""Retarget a Nightreign .sl2 save from one Steam ID to another.

A .sl2 is tied to the Steam account that created it: the game refuses to load a
save whose embedded Steam ID does not match the logged-in user. Rewriting that
ID lets a save be opened under a different account and then handed back.

Where the owning account's ID lives (verified empirically against real saves,
not from docs):

  * ``USERDATA_10`` (profile/menu data) at offset ``0x8``  -- fixed. This is the
    authoritative record of who owns the save.
  * each populated character slot (``USERDATA_00``..``09``) -- offset VARIES
    between saves, so a fixed-offset patch is not safe

We therefore search-and-replace the 8-byte value rather than writing to known
offsets. That is also self-correcting on the return trip: if the game stamps the
borrowing account's ID into new places while you play, patching back sweeps them
up too.

Crucially, a save may also contain OTHER players' Steam IDs -- co-op partners
get recorded in the character slot's session data. Those are not the owner and
must survive untouched, so we replace only occurrences of the ID found at the
``USERDATA_10`` anchor rather than everything that looks like a Steam ID.

Known limitation: an owner-ID occurrence and a co-op-partner-ID occurrence are
indistinguishable byte patterns. If account B borrows A's save and plays co-op
*with A*, patching back B->A could collapse a genuine reference to A. Play solo
on a borrowed save (buying relics is a solo hub activity anyway) and this cannot
arise; the tool warns if the destination ID is already present.

A SteamID64 for an individual account is ``0x0110000100000000 + account_id``, so
the upper 4 bytes are always ``01 00 10 01`` little-endian. That constant makes
detection an exact search instead of a guess.

Checksums and encryption are handled by ``nrplanner.writer.repack_sl2``, which
recomputes each entry's MD5 trailer and re-encrypts it.

Usage
-----
Read the Steam ID out of a save::

    python scripts/transfer_save.py show friend.sl2

Retarget a save, taking the destination ID from another save file::

    python scripts/transfer_save.py patch friend.sl2 --to-from mine.sl2 -o out.sl2

...or from an explicit ID (the folder name under Nightreign/ is your Steam ID)::

    python scripts/transfer_save.py patch friend.sl2 --to 76561198039949473 -o out.sl2
"""
import argparse
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nrplanner.save import (
    PROFILE_ENTRY_INDEX,
    decrypt_sl2,
    find_steam_ids,
    is_steam_id,
    owner_steam_id_from_blob,
)
from nrplanner.writer import patch_slot_checksum, repack_sl2

# Steam-ID save logic (constants, scanning, and the owner anchor) lives in
# nrplanner.save so the library and this script share one source of truth.


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


def scan(blobs: dict[int, bytes]) -> dict[int, list[tuple[int, int]]]:
    """Return {entry index: [(offset, steam_id)]} for entries containing an ID."""
    found = {}
    for index, blob in sorted(blobs.items()):
        hits = find_steam_ids(blob)
        if hits:
            found[index] = hits
    return found


def owner_id(blobs: dict[int, bytes], label: str) -> int:
    """Return the Steam ID of the account that owns this save.

    Read from the fixed profile anchor rather than inferred from a sweep: a save
    legitimately contains co-op partners' IDs too, and only this one is the owner.
    """
    blob = blobs.get(PROFILE_ENTRY_INDEX)
    if blob is None:
        raise ValueError(
            f"{label}: no USERDATA_{PROFILE_ENTRY_INDEX:02d} entry -- "
            "is this a Nightreign .sl2?"
        )
    value = owner_steam_id_from_blob(blob)
    if value is None:
        raise ValueError(
            f"{label}: the profile anchor does not hold a SteamID64. "
            "This may not be a Nightreign save."
        )
    return value


def other_ids(blobs: dict[int, bytes], owner: int) -> set[int]:
    """Steam IDs in the save that are not the owner's (co-op partners etc.)."""
    return {
        value
        for hits in scan(blobs).values()
        for _, value in hits
        if value != owner
    }


def retarget(raw: bytes, blobs: dict[int, bytes], old_id: int,
             new_id: int) -> tuple[bytes, dict[int, bytes], int]:
    """Rewrite every occurrence of old_id to new_id and repack the .sl2.

    Returns (new .sl2 bytes, expected patched blobs, occurrences replaced).
    """
    old_bytes = struct.pack("<Q", old_id)
    new_bytes = struct.pack("<Q", new_id)

    expected: dict[int, bytes] = {}
    replaced = 0

    for index, blob in sorted(blobs.items()):
        hits = [off for off, value in find_steam_ids(blob) if value == old_id]
        if not hits:
            continue
        patched = bytearray(blob)
        for offset in hits:
            assert patched[offset:offset + 8] == old_bytes
            patched[offset:offset + 8] = new_bytes
            replaced += 1
        # repack_sl2 recomputes this itself; we mirror it so the verify pass can
        # compare the output byte-for-byte against a locally derived expectation.
        patch_slot_checksum(patched)
        expected[index] = bytes(patched)

    if not replaced:
        raise ValueError(f"No occurrences of Steam ID {old_id} found to replace.")

    return repack_sl2(raw, expected), expected, replaced


def verify(out_path: Path, original: dict[int, bytes],
           expected: dict[int, bytes], old_id: int, new_id: int) -> None:
    """Re-decrypt the written file and prove it is exactly what we intended.

    Every byte is accounted for: patched entries must equal our locally computed
    expectation, untouched entries must be identical to the input, ownership must
    have transferred, the old ID must be entirely gone, and any other players'
    IDs must survive unchanged.
    """
    actual = decrypt_blobs(out_path)

    if actual.keys() != original.keys():
        raise AssertionError(
            f"entry set changed: {sorted(original)} -> {sorted(actual)}"
        )

    for index, blob in sorted(actual.items()):
        want = expected.get(index, original[index])
        if blob != want:
            kind = "patched" if index in expected else "untouched"
            raise AssertionError(f"USERDATA_{index:02d} ({kind}) does not match.")

    got_owner = owner_id(actual, out_path.name)
    if got_owner != new_id:
        raise AssertionError(f"output owner is {got_owner}, expected {new_id}.")

    distinct = {value for hits in scan(actual).values() for _, value in hits}
    if old_id in distinct:
        raise AssertionError(f"old Steam ID {old_id} still present in output.")

    before = other_ids(original, old_id)
    after = other_ids(actual, new_id)
    if before != after:
        raise AssertionError(
            f"third-party Steam IDs changed: {sorted(before)} -> {sorted(after)}"
        )


def cmd_show(args: argparse.Namespace) -> int:
    path = Path(args.save)
    blobs = decrypt_blobs(path)
    owner = owner_id(blobs, path.name)

    print(f"{path.name}: {len(blobs)} BND4 entries")
    for index, hits in sorted(scan(blobs).items()):
        for offset, value in hits:
            role = "owner" if value == owner else "other player"
            print(f"  USERDATA_{index:02d} @ 0x{offset:x}: {value}  ({role})")

    print(f"\nOwner Steam ID: {owner}")
    others = other_ids(blobs, owner)
    if others:
        print(f"Also references {len(others)} other player(s): "
              f"{', '.join(str(v) for v in sorted(others))}")
        print("These are left untouched by 'patch'.")
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    src = Path(args.save)
    out = Path(args.output)

    if out.exists() and not args.force:
        print(f"Refusing to overwrite {out} (pass --force).", file=sys.stderr)
        return 1
    if out.resolve() == src.resolve():
        print("Output must differ from input.", file=sys.stderr)
        return 1

    raw = src.read_bytes()
    blobs = decrypt_blobs(src)
    old_id = owner_id(blobs, src.name)

    if args.to_from:
        ref = Path(args.to_from)
        new_id = owner_id(decrypt_blobs(ref), ref.name)
        print(f"Destination Steam ID {new_id} (read from {ref.name})")
    else:
        new_id = args.to
        if not is_steam_id(new_id):
            print(f"{new_id} is not a valid SteamID64.", file=sys.stderr)
            return 1

    if old_id == new_id:
        print(f"{src.name} already belongs to {new_id}; nothing to do.")
        return 1

    # The destination ID already appearing as a co-op partner makes owner and
    # partner occurrences indistinguishable on the way back. See module docstring.
    if new_id in other_ids(blobs, old_id):
        print(f"WARNING: {new_id} is already referenced in {src.name} as another "
              f"player. Patching back later could collapse that reference.",
              file=sys.stderr)

    print(f"Retargeting {src.name}: {old_id} -> {new_id}")
    patched, expected, replaced = retarget(raw, blobs, old_id, new_id)

    if len(patched) != len(raw):
        print(f"Size changed ({len(raw)} -> {len(patched)}); aborting.",
              file=sys.stderr)
        return 1

    out.write_bytes(patched)
    print(f"Replaced {replaced} occurrence(s) across "
          f"{len(expected)} entr{'y' if len(expected) == 1 else 'ies'}: "
          f"{', '.join(f'USERDATA_{i:02d}' for i in sorted(expected))}")

    verify(out, blobs, expected, old_id, new_id)
    print(f"Verified. Wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retarget a Nightreign .sl2 save to a different Steam ID.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage\n-----\n")[1],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print the Steam ID a save belongs to")
    show.add_argument("save", help="path to a .sl2 save file")
    show.set_defaults(func=cmd_show)

    patch = sub.add_parser("patch", help="rewrite a save's Steam ID")
    patch.add_argument("save", help="path to the .sl2 to retarget")
    patch.add_argument("-o", "--output", required=True, help="path to write")
    dest = patch.add_mutually_exclusive_group(required=True)
    dest.add_argument("--to", type=int, help="destination SteamID64")
    dest.add_argument("--to-from", metavar="SL2",
                      help="take the destination ID from another save file")
    patch.add_argument("--force", action="store_true",
                       help="overwrite the output file if it exists")
    patch.set_defaults(func=cmd_patch)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValueError, AssertionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
