"""Tests for GET /api/v1/game/* endpoints (no auth required)."""
import pytest
from fastapi.testclient import TestClient


@pytest.mark.usefixtures("override_game_data")
class TestGameEndpoints:
    def test_get_effects_ok(self, client: TestClient) -> None:
        response = client.get("/api/v1/game/effects")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        first = data[0]
        assert "id" in first
        assert "name" in first

    def test_get_families_ok(self, client: TestClient) -> None:
        response = client.get("/api/v1/game/families")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_get_characters_ok(self, client: TestClient) -> None:
        response = client.get("/api/v1/game/characters")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        names = [c["name"] for c in data]
        assert "Wylder" in names
        # Every entry must have a hero_type
        for char in data:
            assert "name" in char
            assert "hero_type" in char

    def test_get_vessels_known_hero(self, client: TestClient) -> None:
        # hero_type 100000 = Wylder
        response = client.get("/api/v1/game/vessels/100000")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_get_vessels_unknown_hero(self, client: TestClient) -> None:
        # Unknown hero_type returns only 'All'-character vessels (heroType=11),
        # not an empty list, because those vessels are shared across all heroes.
        response = client.get("/api/v1/game/vessels/0")
        assert response.status_code == 200
        data = response.json()
        for vessel in data:
            assert vessel["Character"] == "All"

    def test_get_tiers_ok(self, client: TestClient) -> None:
        response = client.get("/api/v1/game/tiers")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        tier_keys = {t["key"] for t in data}
        assert tier_keys == {"required", "preferred", "nice_to_have", "bonus", "avoid", "blacklist"}

    def test_get_colors_ok(self, client: TestClient) -> None:
        response = client.get("/api/v1/game/colors")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        expected = {"Red", "Blue", "Yellow", "Green", "White"}
        assert expected.issubset(data.keys())
