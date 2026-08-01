"""Integration tests for POST /saves/export (sell relics + export modified save).

Requires the real fixture at backend/tests/fixtures/NR0000.sl2.
Run with: uv run pytest backend/tests/ -m integration -v
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nrplanner.constants import EMPTY_EFFECT
from nrplanner.writer import sell_value

FIXTURE_PATH = Path(__file__).parent.parent.parent / "fixtures" / "NR0000.sl2"

pytestmark = pytest.mark.integration

skip_no_fixture = pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present — run with -m integration after copying NR0000.sl2",
)


def _parse_profiles(client: TestClient) -> list[dict]:
    with FIXTURE_PATH.open("rb") as f:
        resp = client.post(
            "/api/v1/saves/upload",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["profiles"]


def _effect_count(relic: dict) -> int:
    effects = (relic["effect_1"], relic["effect_2"], relic["effect_3"])
    return sum(1 for e in effects if e not in (EMPTY_EFFECT, 0))


def _pick(relics, predicate):
    for r in relics:
        if predicate(r):
            return r
    return None


@skip_no_fixture
@pytest.mark.usefixtures("override_game_data")
def test_export_sells_relic_and_credits_murk(client: TestClient) -> None:
    profiles = _parse_profiles(client)
    prof = next(p for p in profiles if p["relics"])
    sellable = _pick(
        prof["relics"], lambda r: not r["equipped"] and not r["is_favorite"]
    )
    assert sellable is not None, "fixture has no sellable relic"

    expected_credit = sell_value(_effect_count(sellable), sellable["is_deep"])

    with FIXTURE_PATH.open("rb") as f:
        resp = client.post(
            "/api/v1/saves/export",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            data={
                "slot_index": str(prof["slot_index"]),
                "ga_handles": json.dumps([sellable["ga_handle"]]),
            },
        )

    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == b"BND4"
    assert "attachment" in resp.headers["content-disposition"]
    assert "_edited.sl2" in resp.headers["content-disposition"]
    assert resp.headers["x-relics-removed"] == "1"
    assert resp.headers["x-murks-credited"] == str(expected_credit)

    # Re-upload the edited save: the slot should have exactly one fewer relic
    # and the sold relic must be gone.
    edited = client.post(
        "/api/v1/saves/upload",
        files={"file": ("NR0000_edited.sl2", resp.content, "application/octet-stream")},
    )
    assert edited.status_code == 200, edited.text
    edited_prof = next(
        p for p in edited.json()["profiles"] if p["slot_index"] == prof["slot_index"]
    )
    assert edited_prof["relic_count"] == prof["relic_count"] - 1
    handles_after = {r["ga_handle"] for r in edited_prof["relics"]}
    assert sellable["ga_handle"] not in handles_after


@skip_no_fixture
@pytest.mark.usefixtures("override_game_data")
def test_export_rejects_unknown_handle(client: TestClient) -> None:
    profiles = _parse_profiles(client)
    prof = next(p for p in profiles if p["relics"])
    with FIXTURE_PATH.open("rb") as f:
        resp = client.post(
            "/api/v1/saves/export",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            data={
                "slot_index": str(prof["slot_index"]),
                "ga_handles": json.dumps([0xDEADBEEF]),
            },
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "unknown_relics"


@skip_no_fixture
@pytest.mark.usefixtures("override_game_data")
def test_export_rejects_protected_relic(client: TestClient) -> None:
    profiles = _parse_profiles(client)
    # Find any equipped or favorite relic across profiles.
    target = None
    slot = None
    for p in profiles:
        protected = _pick(p["relics"], lambda r: r["equipped"] or r["is_favorite"])
        if protected:
            target, slot = protected, p["slot_index"]
            break
    if target is None:
        pytest.skip("fixture has no equipped/favorite relic to test protection")

    with FIXTURE_PATH.open("rb") as f:
        resp = client.post(
            "/api/v1/saves/export",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            data={
                "slot_index": str(slot),
                "ga_handles": json.dumps([target["ga_handle"]]),
            },
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "protected_relics"


@skip_no_fixture
@pytest.mark.usefixtures("override_game_data")
def test_export_requires_selection(client: TestClient) -> None:
    with FIXTURE_PATH.open("rb") as f:
        resp = client.post(
            "/api/v1/saves/export",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            data={"slot_index": "0", "ga_handles": json.dumps([])},
        )
    assert resp.status_code == 422


@skip_no_fixture
@pytest.mark.usefixtures("override_game_data")
def test_export_bookmarks_relic(client: TestClient) -> None:
    profiles = _parse_profiles(client)
    prof = next(p for p in profiles if p["relics"])
    target = _pick(prof["relics"], lambda r: not r["equipped"] and not r["is_favorite"])
    assert target is not None

    with FIXTURE_PATH.open("rb") as f:
        resp = client.post(
            "/api/v1/saves/export",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            data={
                "slot_index": str(prof["slot_index"]),
                "favorite_changes": json.dumps({str(target["ga_handle"]): True}),
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-favorites-changed"] == "1"
    assert resp.headers["x-relics-removed"] == "0"

    # Re-upload: the relic is now bookmarked.
    edited = client.post(
        "/api/v1/saves/upload",
        files={"file": ("NR0000_edited.sl2", resp.content, "application/octet-stream")},
    )
    assert edited.status_code == 200, edited.text
    edited_prof = next(
        p for p in edited.json()["profiles"] if p["slot_index"] == prof["slot_index"]
    )
    edited_relic = next(
        r for r in edited_prof["relics"] if r["ga_handle"] == target["ga_handle"]
    )
    assert edited_relic["is_favorite"] is True


@skip_no_fixture
@pytest.mark.usefixtures("override_game_data")
def test_export_add_relics_returns_assigned_handles(client: TestClient) -> None:
    """Sells → mints chain: the add step must return the real ga_handles it
    assigned (X-Added-Handles, ordered 1:1 with specs) so the client can
    substitute them for the synthetic handles its staged loadouts reference."""
    from app.core.game_data import get_relic_generator

    profiles = _parse_profiles(client)
    prof = next(p for p in profiles if p["relics"])
    sellable = _pick(
        prof["relics"], lambda r: not r["equipped"] and not r["is_favorite"]
    )
    assert sellable is not None, "fixture has no sellable relic"

    # Sell first — each sell frees exactly one ghost slot for the mint step.
    with FIXTURE_PATH.open("rb") as f:
        sold = client.post(
            "/api/v1/saves/export",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            data={
                "slot_index": str(prof["slot_index"]),
                "ga_handles": json.dumps([sellable["ga_handle"]]),
            },
        )
    assert sold.status_code == 200, sold.text

    rolled = get_relic_generator().roll(is_deep=False, version="1.03", seed=77)
    spec = {
        "real_id": rolled.real_id,
        "effects": list(rolled.effects),
        "curses": list(rolled.curses),
    }
    resp = client.post(
        "/api/v1/saves/export-add-relics",
        files={
            "file": ("NR0000_edited.sl2", sold.content, "application/octet-stream")
        },
        data={"slot_index": str(prof["slot_index"]), "specs": json.dumps([spec])},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-relics-added"] == "1"
    handles = json.loads(resp.headers["x-added-handles"])
    assert isinstance(handles, list) and len(handles) == 1
    assert "X-Added-Handles" in resp.headers["access-control-expose-headers"]

    # The assigned handle resolves to the minted content on re-upload.
    edited = client.post(
        "/api/v1/saves/upload",
        files={
            "file": ("NR0000_edited2.sl2", resp.content, "application/octet-stream")
        },
    )
    assert edited.status_code == 200, edited.text
    eprof = next(
        p for p in edited.json()["profiles"]
        if p["slot_index"] == prof["slot_index"]
    )
    minted = next(r for r in eprof["relics"] if r["ga_handle"] == handles[0])
    assert minted["real_id"] == spec["real_id"]
    assert minted["effect_1"] == spec["effects"][0]


@skip_no_fixture
@pytest.mark.usefixtures("override_game_data")
def test_export_chain_loadout_references_minted_relic(client: TestClient) -> None:
    """The full staged-journey export chain (sell → mint → loadout): a loadout
    op whose ga_handles carry the real handle from X-Added-Handles validates
    against the minted save, and the re-uploaded save's preset references the
    resurrected relic."""
    from app.core.game_data import get_relic_generator

    profiles = _parse_profiles(client)
    prof = next(p for p in profiles if p["relics"])
    preset = next(iter(prof.get("presets") or []), None)
    if preset is None:
        pytest.skip("fixture has no in-game preset to source character/vessel")
    sellable = _pick(
        prof["relics"], lambda r: not r["equipped"] and not r["is_favorite"]
    )
    assert sellable is not None, "fixture has no sellable relic"

    with FIXTURE_PATH.open("rb") as f:
        sold = client.post(
            "/api/v1/saves/export",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            data={
                "slot_index": str(prof["slot_index"]),
                "ga_handles": json.dumps([sellable["ga_handle"]]),
            },
        )
    assert sold.status_code == 200, sold.text

    rolled = get_relic_generator().roll(is_deep=False, version="1.03", seed=78)
    minted_resp = client.post(
        "/api/v1/saves/export-add-relics",
        files={"file": ("NR0000_e.sl2", sold.content, "application/octet-stream")},
        data={
            "slot_index": str(prof["slot_index"]),
            "specs": json.dumps([{
                "real_id": rolled.real_id,
                "effects": list(rolled.effects),
                "curses": list(rolled.curses),
            }]),
        },
    )
    assert minted_resp.status_code == 200, minted_resp.text
    minted_handle = json.loads(minted_resp.headers["x-added-handles"])[0]

    ops = [{
        "op": "add",
        "character": preset["character"],
        "vessel_id": preset["vessel_id"],
        "ga_handles": [minted_handle, 0, 0, 0, 0, 0],
        "name": "MintChain",
    }]
    loadout_resp = client.post(
        "/api/v1/saves/export-loadouts",
        files={
            "file": ("NR0000_e2.sl2", minted_resp.content, "application/octet-stream")
        },
        data={
            "slot_index": str(prof["slot_index"]),
            "operations": json.dumps(ops),
        },
    )
    assert loadout_resp.status_code == 200, loadout_resp.text
    assert loadout_resp.headers["x-loadouts-added"] == "1"

    edited = client.post(
        "/api/v1/saves/upload",
        files={
            "file": ("NR0000_e3.sl2", loadout_resp.content, "application/octet-stream")
        },
    )
    assert edited.status_code == 200, edited.text
    eprof = next(
        p for p in edited.json()["profiles"]
        if p["slot_index"] == prof["slot_index"]
    )
    new_preset = next(p for p in eprof["presets"] if p["name"] == "MintChain")
    assert minted_handle in (new_preset["ga_handles"] or []), (
        "the exported preset must reference the freshly minted relic's handle"
    )


@skip_no_fixture
@pytest.mark.usefixtures("override_game_data")
def test_export_exposes_murks(client: TestClient) -> None:
    profiles = _parse_profiles(client)
    prof = next(p for p in profiles if p["relics"])
    # Murk is surfaced on the parsed profile for the inventory UI.
    assert "murks" in prof
    assert isinstance(prof["murks"], int)
