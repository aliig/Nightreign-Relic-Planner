"""Tests for POST /api/v1/optimize/ endpoint.

Uses inline mode (no DB) for unit tests so no prior upload is required.
"""
import pytest
from fastapi.testclient import TestClient

from nrplanner.constants import EMPTY_EFFECT

EMPTY = EMPTY_EFFECT

_MINIMAL_BUILD = {
    "id": "inline-test",
    "name": "Test Build",
    "character": "Wylder",
    "groups": [],
    "required_effects": [100],
    "required_families": [],
    "excluded_effects": [],
    "excluded_families": [],
    "include_deep": False,
    "curse_max": 1,
}

_MINIMAL_RELIC = {
    "ga_handle": 0xC0000001,
    "item_id": 100 + 2147483648,
    "real_id": 100,
    "color": "Red",
    "effects": [100, EMPTY, EMPTY],
    "curses": [EMPTY, EMPTY, EMPTY],
    "is_deep": False,
    "name": "Test Relic",
    "tier": "Delicate",
}

_MINIMAL_RELIC_BLUE = {
    **_MINIMAL_RELIC,
    "ga_handle": 0xC0000002,
    "color": "Blue",
}

_MINIMAL_RELIC_GREEN = {
    **_MINIMAL_RELIC,
    "ga_handle": 0xC0000003,
    "color": "Green",
}


@pytest.mark.usefixtures("override_game_data")
class TestInlineMode:
    def test_inline_optimize_empty_relics_ok(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": _MINIMAL_BUILD,
                "relics": [],
                "character_name": "Wylder",
                "top_n": 3,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_inline_optimize_with_relics_ok(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": _MINIMAL_BUILD,
                "relics": [_MINIMAL_RELIC],
                "character_name": "Wylder",
                "top_n": 3,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Each item should be a VesselResult shape
        for result in data:
            assert "vessel_id" in result
            assert "vessel_name" in result
            assert "total_score" in result
            assert "assignments" in result

    def test_missing_build_returns_422(self, client: TestClient) -> None:
        """Inline mode requires both build and relics."""
        response = client.post(
            "/api/v1/optimize/",
            json={
                # build omitted — only relics provided
                "relics": [],
            },
        )
        assert response.status_code == 422

    def test_unknown_character_returns_422(self, client: TestClient) -> None:
        """An unknown character name in build.character should return 422."""
        bad_build = {**_MINIMAL_BUILD, "character": "NotARealCharacter"}
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": bad_build,
                "relics": [],
            },
        )
        assert response.status_code == 422
        assert "Unknown character" in response.json()["detail"]

    def test_inline_with_pinned_relics_ok(self, client: TestClient) -> None:
        """Inline mode accepts pinned_relics without error."""
        build_with_pins = {**_MINIMAL_BUILD, "pinned_relics": [_MINIMAL_RELIC["ga_handle"]]}
        response = client.post(
            "/api/v1/optimize/",
            json={"build": build_with_pins, "relics": [_MINIMAL_RELIC], "top_n": 3},
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_both_db_and_inline_mode_returns_422(self, client: TestClient) -> None:
        import uuid

        response = client.post(
            "/api/v1/optimize/",
            json={
                "build_id": str(uuid.uuid4()),
                "build": _MINIMAL_BUILD,
                "relics": [],
                "character_name": "Wylder",
            },
        )
        assert response.status_code == 422


    def test_results_include_character_specific_vessels(
        self, client: TestClient,
    ) -> None:
        """Optimization for Wylder must return Wylder-specific vessels,
        not only the shared 'All' vessels.  This catches the NPC-ID-vs-
        hero-index mapping bug in _resolve_hero_type."""
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": _MINIMAL_BUILD,
                "relics": [_MINIMAL_RELIC, _MINIMAL_RELIC_BLUE, _MINIMAL_RELIC_GREEN],
                "top_n": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        characters = {r["vessel_character"] for r in data}
        assert "Wylder" in characters, (
            f"Expected Wylder-specific vessels but got: {characters}"
        )


@pytest.mark.usefixtures("override_game_data")
class TestDbMode:
    def test_db_mode_requires_auth(self, client: TestClient) -> None:
        import uuid

        response = client.post(
            "/api/v1/optimize/",
            json={
                "build_id": str(uuid.uuid4()),
                "profile_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 401


_SLOT_BUILD = {
    "id": "slot-alt-test",
    "name": "Slot Alt Build",
    "character": "Wylder",
    "groups": [],
    "required_effects": [100],
    "required_families": [],
    "excluded_effects": [],
    "excluded_families": [],
    "include_deep": False,
    "curse_max": 1,
}


def _color_relics() -> list[dict]:
    """4 relics per colour, all carrying required effect 100.

    Four-per-colour guarantees that after pinning the other (≤2) standard slots
    and excluding one relic, a same-colour alternative still remains for the
    struck slot — so the strike always produces a swap regardless of the chosen
    vessel's colour layout."""
    relics: list[dict] = []
    h = 0xC0010000
    for color in ("Red", "Blue", "Yellow", "Green", "White"):
        for _ in range(4):
            relics.append({
                "ga_handle": h,
                "item_id": 100 + 2147483648,
                "real_id": 100,
                "color": color,
                "effects": [100, EMPTY, EMPTY],
                "curses": [EMPTY, EMPTY, EMPTY],
                "is_deep": False,
                "name": "Test Relic",
                "tier": "Delicate",
            })
            h += 1
    return relics


@pytest.mark.usefixtures("override_game_data")
class TestSlotAlternative:
    """POST /optimize/slot-alternative — re-optimize one slot, others pinned."""

    def _top_result(self, client: TestClient) -> dict:
        resp = client.post(
            "/api/v1/optimize/",
            json={"build": _SLOT_BUILD, "relics": _color_relics(), "top_n": 50},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data, "expected at least one vessel result to strike against"
        return data[0]

    def test_strike_swaps_struck_slot_and_keeps_others(self, client: TestClient) -> None:
        top = self._top_result(client)
        vessel_id = top["vessel_id"]
        assignments = top["assignments"]
        struck_idx = next(i for i, a in enumerate(assignments) if a["relic"])
        struck_handle = assignments[struck_idx]["relic"]["ga_handle"]
        locked = [
            {"slot_index": a["slot_index"], "ga_handle": a["relic"]["ga_handle"]}
            for i, a in enumerate(assignments)
            if i != struck_idx and a["relic"]
        ]

        resp = client.post(
            "/api/v1/optimize/slot-alternative",
            json={
                "build": _SLOT_BUILD,
                "relics": _color_relics(),
                "vessel_id": vessel_id,
                "struck_slot_index": struck_idx,
                "locked_slots": locked,
                "excluded_ga_handles": [struck_handle],
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result is not None, "a same-colour alternative should exist"
        assert result["vessel_id"] == vessel_id

        # The struck slot changed to a different relic.
        new_struck = result["assignments"][struck_idx]["relic"]
        assert new_struck is not None, "struck slot should get a replacement"
        assert new_struck["ga_handle"] != struck_handle

        # Every locked slot kept its EXACT relic in its EXACT position (the bug
        # fix: no positionless repacking).
        new_by_slot = {a["slot_index"]: a["relic"] for a in result["assignments"]}
        for ls in locked:
            kept = new_by_slot[ls["slot_index"]]
            assert kept is not None and kept["ga_handle"] == ls["ga_handle"], (
                f"slot {ls['slot_index']} must keep relic {ls['ga_handle']}, "
                f"got {kept}"
            )

        # The struck relic appears nowhere in the result.
        all_handles = {
            a["relic"]["ga_handle"] for a in result["assignments"] if a["relic"]
        }
        assert struck_handle not in all_handles, "struck relic must be excluded"

    def test_unknown_vessel_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/optimize/slot-alternative",
            json={
                "build": _SLOT_BUILD,
                "relics": [],
                "vessel_id": 999_999_999,
                "struck_slot_index": 0,
                "locked_slots": [],
                "excluded_ga_handles": [],
            },
        )
        assert resp.status_code == 404

    def test_db_mode_requires_auth(self, client: TestClient) -> None:
        import uuid

        resp = client.post(
            "/api/v1/optimize/slot-alternative",
            json={
                "build_id": str(uuid.uuid4()),
                "profile_id": str(uuid.uuid4()),
                "vessel_id": 1,
                "struck_slot_index": 0,
            },
        )
        assert resp.status_code == 401
