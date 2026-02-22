"""Tests for POST /api/v1/optimize/ endpoint.

Uses inline mode (no DB) for unit tests so no prior upload is required.
"""
import pytest
from fastapi.testclient import TestClient

from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import ALL_TIER_KEYS

EMPTY = EMPTY_EFFECT

_DEFAULT_TIERS = {k: [] for k in ALL_TIER_KEYS}
_DEFAULT_FAMILY_TIERS = {k: [] for k in ALL_TIER_KEYS}

_MINIMAL_BUILD = {
    "id": "inline-test",
    "name": "Test Build",
    "character": "Wylder",
    "tiers": _DEFAULT_TIERS,
    "family_tiers": _DEFAULT_FAMILY_TIERS,
    "include_deep": False,
    "curse_max": 1,
}

_MINIMAL_RELIC = {
    "ga_handle": 0xC0000001,
    "item_id": 100 + 2147483648,
    "real_id": 100,
    "color": "Red",
    "effects": [EMPTY, EMPTY, EMPTY],
    "curses": [EMPTY, EMPTY, EMPTY],
    "is_deep": False,
    "name": "Test Relic",
    "tier": "Delicate",
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

    def test_missing_character_name_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": _MINIMAL_BUILD,
                "relics": [],
                # character_name omitted
            },
        )
        assert response.status_code == 422

    def test_unknown_character_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": _MINIMAL_BUILD,
                "relics": [],
                "character_name": "NotARealCharacter",
            },
        )
        assert response.status_code == 422
        assert "Unknown character" in response.json()["detail"]

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


@pytest.mark.usefixtures("override_game_data")
class TestDbMode:
    def test_db_mode_requires_auth(self, client: TestClient) -> None:
        import uuid

        response = client.post(
            "/api/v1/optimize/",
            json={
                "build_id": str(uuid.uuid4()),
                "character_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 401
