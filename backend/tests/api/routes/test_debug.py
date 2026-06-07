"""Tests for the local-only debug export endpoint.

The router is only mounted when ENVIRONMENT == "local" (the test default) and
every endpoint requires a superuser.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.mark.usefixtures("override_game_data")
class TestDebugExport:
    def test_superuser_export_writes_bundle(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.api.routes.debug as debug_mod

        monkeypatch.setattr(debug_mod, "get_debug_export_dir", lambda: tmp_path)

        # Populate a real build so the row-serialization path is exercised
        # (model_dump + build_def_from_db), not just the empty case.
        created = client.post(
            "/api/v1/builds/",
            json={"name": "Debug Build", "character": "Wylder"},
            headers=superuser_token_headers,
        )
        assert created.status_code == 200, created.text

        resp = client.post(
            "/api/v1/debug/export",
            headers=superuser_token_headers,
            json={"mode": "full", "note": "test"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        bundle = body["bundle"]
        assert {"meta", "user", "scenario", "builds", "profiles", "snapshots"}.issubset(
            bundle
        )
        assert bundle["meta"]["environment"] == "local"
        assert bundle["meta"]["note"] == "test"
        assert bundle["user"]["is_superuser"] is True
        # The created build serialized into the bundle (raw row + canonical build_def).
        match = next(
            (b for b in bundle["builds"] if b["raw"]["name"] == "Debug Build"), None
        )
        assert match is not None, "created build missing from bundle"
        assert match["build_def"]["character"] == "Wylder"
        # Bundle written to the (patched) gitignored export dir.
        assert (tmp_path / "latest.json").exists()
        assert body["written_path"].endswith("latest.json")

    def test_normal_user_forbidden(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
    ) -> None:
        resp = client.post(
            "/api/v1/debug/export",
            headers=normal_user_token_headers,
            json={"mode": "full"},
        )
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, client: TestClient) -> None:
        resp = client.post("/api/v1/debug/export", json={"mode": "full"})
        assert resp.status_code in (401, 403)
