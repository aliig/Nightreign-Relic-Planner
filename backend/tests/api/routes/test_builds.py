"""Tests for build CRUD endpoints (authentication required for all)."""
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _create_build(
    client: TestClient, headers: dict[str, str], name: str = "Test Build"
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/builds/",
        json={"name": name, "character": "Wylder"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestBuildsRequireAuth:
    def test_list_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/v1/builds/").status_code in (401, 403)

    def test_create_requires_auth(self, client: TestClient) -> None:
        assert client.post(
            "/api/v1/builds/", json={"name": "X", "character": "Wylder"}
        ).status_code in (401, 403)

    def test_get_requires_auth(self, client: TestClient) -> None:
        assert client.get(f"/api/v1/builds/{uuid.uuid4()}").status_code in (401, 403)

    def test_update_requires_auth(self, client: TestClient) -> None:
        assert client.put(
            f"/api/v1/builds/{uuid.uuid4()}", json={"name": "X"}
        ).status_code in (401, 403)

    def test_delete_requires_auth(self, client: TestClient) -> None:
        assert client.delete(f"/api/v1/builds/{uuid.uuid4()}").status_code in (401, 403)


class TestCreateBuild:
    def test_create_ok(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        build = _create_build(client, superuser_token_headers)
        assert build["name"] == "Test Build"
        assert build["character"] == "Wylder"
        assert "id" in build
        # All 6 tier keys must be present with empty lists (includes "bonus" tier)
        expected_keys = {"required", "preferred", "nice_to_have", "bonus", "avoid", "blacklist"}
        assert expected_keys == set(build["tiers"].keys())
        for key, ids in build["tiers"].items():
            assert ids == [], f"Tier '{key}' should be empty on creation"
        assert build["include_deep"] is True
        assert build["curse_max"] == 1

    def test_create_empty_name_returns_422(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/builds/",
            json={"name": "", "character": "Wylder"},
            headers=superuser_token_headers,
        )
        assert response.status_code == 422

    # Clean up after module to avoid polluting other test counts
    @pytest.fixture(autouse=True, scope="module")
    def cleanup_builds(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> Any:
        yield
        # Delete all builds for the superuser after the module runs
        resp = client.get("/api/v1/builds/", headers=superuser_token_headers)
        if resp.status_code == 200:
            for b in resp.json().get("data", []):
                client.delete(f"/api/v1/builds/{b['id']}", headers=superuser_token_headers)


class TestGetBuild:
    @pytest.fixture(scope="class")
    def build(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> Any:
        b = _create_build(client, superuser_token_headers, name="GetTest")
        yield b
        client.delete(f"/api/v1/builds/{b['id']}", headers=superuser_token_headers)

    def test_get_ok(
        self, client: TestClient, superuser_token_headers: dict[str, str], build: dict
    ) -> None:
        response = client.get(
            f"/api/v1/builds/{build['id']}", headers=superuser_token_headers
        )
        assert response.status_code == 200
        assert response.json()["name"] == "GetTest"

    def test_get_not_found(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        response = client.get(
            f"/api/v1/builds/{uuid.uuid4()}", headers=superuser_token_headers
        )
        assert response.status_code == 404

    def test_get_wrong_owner_returns_403(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        normal_user_token_headers: dict[str, str],
        build: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/builds/{build['id']}", headers=normal_user_token_headers
        )
        assert response.status_code == 403


class TestUpdateBuild:
    @pytest.fixture(scope="class")
    def build(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> Any:
        b = _create_build(client, superuser_token_headers, name="UpdateTest")
        yield b
        client.delete(f"/api/v1/builds/{b['id']}", headers=superuser_token_headers)

    def test_update_name(
        self, client: TestClient, superuser_token_headers: dict[str, str], build: dict
    ) -> None:
        response = client.put(
            f"/api/v1/builds/{build['id']}",
            json={"name": "Renamed"},
            headers=superuser_token_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"

    def test_update_tiers(
        self, client: TestClient, superuser_token_headers: dict[str, str], build: dict
    ) -> None:
        new_tiers = {
            "required": [1001],
            "preferred": [],
            "nice_to_have": [],
            "avoid": [],
            "blacklist": [],
        }
        response = client.put(
            f"/api/v1/builds/{build['id']}",
            json={"tiers": new_tiers},
            headers=superuser_token_headers,
        )
        assert response.status_code == 200
        assert response.json()["tiers"]["required"] == [1001]

    def test_update_not_found(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        response = client.put(
            f"/api/v1/builds/{uuid.uuid4()}",
            json={"name": "X"},
            headers=superuser_token_headers,
        )
        assert response.status_code == 404

    def test_update_wrong_owner_returns_403(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        normal_user_token_headers: dict[str, str],
        build: dict,
    ) -> None:
        response = client.put(
            f"/api/v1/builds/{build['id']}",
            json={"name": "X"},
            headers=normal_user_token_headers,
        )
        assert response.status_code == 403


class TestDeleteBuild:
    def test_delete_ok(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        b = _create_build(client, superuser_token_headers, name="ToDelete")
        response = client.delete(
            f"/api/v1/builds/{b['id']}", headers=superuser_token_headers
        )
        assert response.status_code == 200
        # Verify it's gone
        get_resp = client.get(
            f"/api/v1/builds/{b['id']}", headers=superuser_token_headers
        )
        assert get_resp.status_code == 404

    def test_delete_wrong_owner_returns_403(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        normal_user_token_headers: dict[str, str],
    ) -> None:
        b = _create_build(client, superuser_token_headers, name="OtherOwner")
        response = client.delete(
            f"/api/v1/builds/{b['id']}", headers=normal_user_token_headers
        )
        assert response.status_code == 403
        # Clean up
        client.delete(f"/api/v1/builds/{b['id']}", headers=superuser_token_headers)


class TestTierWeightsAndPinnedRelics:
    def test_create_has_null_tier_weights_and_empty_pinned(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        b = _create_build(client, superuser_token_headers, name="WeightsCheck")
        assert b["tier_weights"] is None
        assert b["pinned_relics"] == []
        client.delete(f"/api/v1/builds/{b['id']}", headers=superuser_token_headers)

    def test_update_tier_weights(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        b = _create_build(client, superuser_token_headers, name="WeightsUpdate")
        weights = {"required": 200, "avoid": -100}
        resp = client.put(
            f"/api/v1/builds/{b['id']}",
            json={"tier_weights": weights},
            headers=superuser_token_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["tier_weights"] == weights
        client.delete(f"/api/v1/builds/{b['id']}", headers=superuser_token_headers)

    def test_update_pinned_relics(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        b = _create_build(client, superuser_token_headers, name="PinnedUpdate")
        pinned = [12345, 67890]
        resp = client.put(
            f"/api/v1/builds/{b['id']}",
            json={"pinned_relics": pinned},
            headers=superuser_token_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["pinned_relics"] == pinned
        client.delete(f"/api/v1/builds/{b['id']}", headers=superuser_token_headers)


class TestListBuilds:
    def test_pagination(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        # Create 3 fresh builds
        ids = []
        for i in range(3):
            b = _create_build(client, superuser_token_headers, name=f"PaginationBuild{i}")
            ids.append(b["id"])

        try:
            # Total count must include at least our 3
            resp_all = client.get("/api/v1/builds/", headers=superuser_token_headers)
            assert resp_all.status_code == 200
            total = resp_all.json()["count"]
            assert total >= 3

            # skip=1, limit=1 should return exactly 1 item
            resp_page = client.get(
                "/api/v1/builds/?skip=1&limit=1", headers=superuser_token_headers
            )
            assert resp_page.status_code == 200
            body = resp_page.json()
            assert body["count"] == total
            assert len(body["data"]) == 1
        finally:
            for bid in ids:
                client.delete(f"/api/v1/builds/{bid}", headers=superuser_token_headers)
