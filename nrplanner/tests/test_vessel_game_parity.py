"""Game-parity replay: our preset writes must reproduce the game's own bytes.

Source of truth is a real save pair captured from one in-game session:

    backend/tests/fixtures/NR0000_pre.sl2   before
    backend/tests/fixtures/NR0000_post.sl2  after 2 in-game deletes + 1 in-game add
                                            ('test', Executor, vessel 8002)

What the pair proves, and what these tests lock in:

  * delete tombstones IN PLACE (header -> 0, hero -> 0, vessel_id -> 0xFFFFFFFF,
    relics + timestamp -> 0; name and counter bytes left stale). No compaction:
    every survivor keeps its physical slot.
  * counter is a dense recency rank (0 == newest) over the active records only.
    A delete decrements the counter of every record OLDER than the deleted one.
  * add reuses the first non-active slot — in the pair the game's own add landed
    in the hole its delete had just made (physical slot 5) — bumps every existing
    counter by 1, and repoints the recipient hero's cur_preset_idx at that
    PHYSICAL slot (Executor 62 -> 5).
  * cur_preset_idx is a physical slot index: the pre save carries 62/76/89 with
    only 53 active records, and the two deletes left every other hero's pointer
    untouched. Compacting on delete silently repoints heroes — the "equipped
    loadout shows another character's preset after an export" bug.

Relic ga_handles are NOT compared: the game renumbers every handle in the save on
each write, so they legitimately differ between the two files.

Both fixtures are gitignored; the module skips when they are absent.
"""
import struct
import tempfile
from pathlib import Path

import pytest

from nrplanner import vessel_writer as vw
from nrplanner.save import decrypt_sl2

_FIXTURE_DIR = Path(__file__).parents[2] / "backend" / "tests" / "fixtures"
PRE = _FIXTURE_DIR / "NR0000_pre.sl2"
POST = _FIXTURE_DIR / "NR0000_post.sl2"
SLOT_INDEX = 0

pytestmark = pytest.mark.skipif(
    not (PRE.exists() and POST.exists()),
    reason="save pair NR0000_pre.sl2 / NR0000_post.sl2 not present")


def _blob(path: Path) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = decrypt_sl2(path, output_dir=tmp)
        return (Path(out) / f"USERDATA_{SLOT_INDEX:02d}").read_bytes()


@pytest.fixture(scope="module")
def pre() -> bytes:
    return _blob(PRE)


@pytest.fixture(scope="module")
def post() -> bytes:
    return _blob(POST)


def _section3(blob) -> int:
    return vw._walk_vessels(blob, vw._locate_region(blob))[1]


def _slot_fields(blob):
    """Per-physical-slot identity for all MAX_PRESETS slots, relics excluded.

    (header, hero_type, counter, name, vessel_id, timestamp) — enough to pin down
    tombstone bytes, recency ranks and physical placement.
    """
    s3 = _section3(blob)
    rows = []
    for i in range(vw.MAX_PRESETS):
        b = s3 + i * vw.PRESET_SIZE
        rows.append((
            blob[b + vw._OFF_HEADER],
            struct.unpack_from("<H", blob, b + vw._OFF_HERO)[0],
            blob[b + vw._OFF_COUNTER],
            bytes(blob[b + vw._OFF_NAME:b + vw._OFF_NAME + vw._NAME_BYTES]),
            struct.unpack_from("<I", blob, b + vw._OFF_VESSEL)[0],
            struct.unpack_from("<Q", blob, b + vw._OFF_TIMESTAMP)[0],
        ))
    return rows


def _hero_pointers(blob):
    """[(hero_type, cur_preset_idx)] for the 10 fixed Section-1 hero blocks."""
    cur = vw._locate_region(blob)
    rows = []
    for _ in range(vw._HERO_SLOTS):
        rows.append((blob[cur], blob[cur + vw._OFF_CUR_PRESET_IDX]))
        cur += vw._HERO_BLOCK
    return rows


def _identity(p):
    """A preset's cross-save identity (ga_handles are renumbered every save)."""
    return (p.hero_type, p.name, p.vessel_id, p.timestamp)


def _session_ops(pre, post):
    """The deletes/adds the player made in-game between the two saves."""
    before, after = vw.parse_presets(pre), vw.parse_presets(post)
    post_ids = {_identity(p) for p in after}
    pre_ids = {_identity(p) for p in before}
    deleted = [p for p in before if _identity(p) not in post_ids]
    added = [p for p in after if _identity(p) not in pre_ids]
    return before, after, deleted, added


# --- what the pair itself says (the evidence, asserted) --------------------

def test_pair_is_the_expected_session(pre, post):
    before, after, deleted, added = _session_ops(pre, post)
    assert len(before) == 53 and len(after) == 52
    assert sorted(p.name for p in deleted) == ["carian 3.24.26", "sorc 3.31.26"]
    assert [p.name for p in added] == ["test"]
    assert added[0].hero_type == 8 and added[0].vessel_id == 8002


def test_cur_preset_idx_is_a_physical_slot_index(pre):
    """Pointers exceed the active-record count, so they cannot be ordinals."""
    n_active = len(vw.parse_presets(pre))
    pointers = [idx for _, idx in _hero_pointers(pre)]
    assert n_active == 53
    assert max(pointers) == 89        # no 90th active record exists
    assert any(idx >= n_active for idx in pointers)


def test_game_delete_leaves_a_hole_and_keeps_slots(pre, post):
    """The survivors of the in-game deletes stayed in their own physical slots."""
    post_rows = _slot_fields(post)
    holes = [i for i, r in enumerate(post_rows[:53]) if r[0] != 0x01]
    assert holes == [16]                       # slot 5 was reused by the add
    assert post_rows[16][4] == 0xFFFFFFFF      # tombstone marker
    assert post_rows[16][5] == 0               # timestamp cleared
    assert post_rows[16][3] != b"\x00" * vw._NAME_BYTES   # name left stale
    # a survivor that sat after both deletes is still at its original slot
    pre_rows = _slot_fields(pre)
    assert pre_rows[52][3] == post_rows[52][3]


# --- the replay ------------------------------------------------------------

def test_replay_reproduces_the_game_bytes(pre, post):
    """Applying the same edits through our writer yields the game's own layout."""
    before, after, deleted, added = _session_ops(pre, post)
    new = added[0]

    blob = pre
    # highest index first, so earlier indices stay valid (as the export route does)
    for idx in sorted((p.index for p in deleted), reverse=True):
        blob, _ = vw.delete_preset(blob, idx)
    blob, res = vw.add_preset(
        blob, hero_type=new.hero_type, name=new.name, vessel_id=new.vessel_id,
        ga_handles=new.relics, timestamp=new.timestamp, update_current=True)

    assert res.slot_index == 5          # the game reused the hole too
    assert len(blob) == len(pre)

    ours, theirs = _slot_fields(blob), _slot_fields(post)
    for i, (a, b) in enumerate(zip(ours, theirs)):
        assert a == b, f"physical slot {i} differs: ours={a} game={b}"
    assert _hero_pointers(blob) == _hero_pointers(post)


def test_replay_without_update_current_only_differs_in_the_pointer(pre, post):
    """The one intentional deviation: we do not repoint the hero unless asked."""
    _, _, deleted, added = _session_ops(pre, post)
    new = added[0]

    blob = pre
    for idx in sorted((p.index for p in deleted), reverse=True):
        blob, _ = vw.delete_preset(blob, idx)
    blob, _ = vw.add_preset(blob, hero_type=new.hero_type, name=new.name,
                            vessel_id=new.vessel_id, ga_handles=new.relics,
                            timestamp=new.timestamp)

    assert _slot_fields(blob) == _slot_fields(post)     # array identical either way
    ours, theirs = dict(_hero_pointers(blob)), dict(_hero_pointers(post))
    assert {h: v for h, v in ours.items() if theirs[h] != v} == {8: 62}
