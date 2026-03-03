"""Tests for build CRUD endpoints (authentication required for all).

NOTE: The function-scoped ``db`` fixture in conftest.py wraps every test in a
SAVEPOINT transaction that is rolled back after the test.  This means we do
**not** need any manual cleanup (client.delete calls, cleanup fixtures, etc.).
Adding manual deletes is actively dangerous — they go through the TestClient
and hit the *real* database connection, permanently deleting data that belongs
to the superuser account used for testing.
"""
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
        # Backend no longer injects default groups (frontend is source of truth)
        assert isinstance(build["groups"], list)
        assert len(build["groups"]) == 0
        assert build["required_effects"] == []
        assert build["required_families"] == []
        assert build["excluded_effects"] == []
        assert build["excluded_families"] == []
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


class TestGetBuild:
    def test_get_ok(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        build = _create_build(client, superuser_token_headers, name="GetTest")
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
    ) -> None:
        build = _create_build(client, superuser_token_headers, name="GetTest")
        response = client.get(
            f"/api/v1/builds/{build['id']}", headers=normal_user_token_headers
        )
        assert response.status_code == 403


class TestUpdateBuild:
    def test_update_name(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        build = _create_build(client, superuser_token_headers, name="UpdateTest")
        response = client.put(
            f"/api/v1/builds/{build['id']}",
            json={"name": "Renamed"},
            headers=superuser_token_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"

    def test_update_required_effects(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        build = _create_build(client, superuser_token_headers, name="UpdateTest")
        response = client.put(
            f"/api/v1/builds/{build['id']}",
            json={"required_effects": [1001]},
            headers=superuser_token_headers,
        )
        assert response.status_code == 200
        assert response.json()["required_effects"] == [1001]

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
    ) -> None:
        build = _create_build(client, superuser_token_headers, name="UpdateTest")
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


class TestGroupsAndPinnedRelics:
    def test_create_has_default_groups_and_empty_pinned(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        b = _create_build(client, superuser_token_headers, name="GroupsCheck")
        assert isinstance(b["groups"], list)
        assert len(b["groups"]) == 0
        assert b["pinned_relics"] == []

    def test_update_groups(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        b = _create_build(client, superuser_token_headers, name="GroupsUpdate")
        new_groups = [{"weight": 75, "effects": [1001], "families": []}]
        resp = client.put(
            f"/api/v1/builds/{b['id']}",
            json={"groups": new_groups},
            headers=superuser_token_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["groups"][0]["weight"] == 75

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


class TestListBuilds:
    def test_pagination(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        # Create 3 fresh builds
        ids = []
        for i in range(3):
            b = _create_build(client, superuser_token_headers, name=f"PaginationBuild{i}")
            ids.append(b["id"])

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
