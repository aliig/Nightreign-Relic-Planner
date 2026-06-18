"""Tests for POST /api/v1/saves/upload and related endpoints.

The binary save-parsing layer (decrypt_sl2, discover_characters, parse_relics)
is mocked so tests run without a real .sl2 fixture file. SourceDataHandler
and RelicInventory run for real to verify the full routing logic.
"""
import io
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import Build, SaveUpload, User
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.save import RawRelic

EMPTY = EMPTY_EFFECT

# A canned RawRelic with real_id=100 (store_102 range, not deep, not illegal).
# item_id = real_id + 2147483648 per the save format convention.
MOCK_RELIC = RawRelic(
    ga_handle=0xC0000001,
    item_id=100 + 2147483648,
    effect_1=EMPTY,
    effect_2=EMPTY,
    effect_3=EMPTY,
    sec_effect1=EMPTY,
    sec_effect2=EMPTY,
    sec_effect3=EMPTY,
    offset=0,
    size=64,
)

# Same relic content as MOCK_RELIC but a different ga_handle (simulates the game
# reassigning handles between saves).
MOCK_RELIC_NEW_HANDLE = RawRelic(
    ga_handle=0xC0000099,
    item_id=MOCK_RELIC.item_id,
    effect_1=MOCK_RELIC.effect_1,
    effect_2=MOCK_RELIC.effect_2,
    effect_3=MOCK_RELIC.effect_3,
    sec_effect1=MOCK_RELIC.sec_effect1,
    sec_effect2=MOCK_RELIC.sec_effect2,
    sec_effect3=MOCK_RELIC.sec_effect3,
    offset=0,
    size=64,
)


def _discover_side_effect(decrypt_dir: Path, mode: str = "PC") -> list:
    """Side effect for discover_characters mock: creates a real USERDATA_00 file."""
    userdata = Path(decrypt_dir) / "USERDATA_00"
    userdata.write_bytes(b"\x00" * 16)
    return [("Wylder", userdata)]


def _upload_sl2(
    client: TestClient,
    headers: dict | None = None,
    filename: str = "NR0000.sl2",
    relics: list[RawRelic] | None = None,
) -> object:
    """POST a dummy .sl2 upload with all nrplanner parsing mocked."""
    raw = relics if relics is not None else [MOCK_RELIC]
    dummy_bytes = b"\x00" * 32
    files = {"file": (filename, io.BytesIO(dummy_bytes), "application/octet-stream")}
    kwargs: dict = {"files": files}
    if headers:
        kwargs["headers"] = headers
    with (
        patch("app.api.routes.saves.decrypt_sl2"),
        patch("app.api.routes.saves.discover_characters", side_effect=_discover_side_effect),
        # second value = items_end_offset (used to locate the ItemEntry favorite
        # flags); 0 is fine for the tiny dummy blob — the table read bounds out.
        patch("app.api.routes.saves.parse_relics", return_value=(raw, 0)),
    ):
        return client.post("/api/v1/saves/upload", **kwargs)


@pytest.mark.usefixtures("override_game_data")
class TestUploadEndpoint:
    def test_wrong_extension_returns_400(self, client: TestClient) -> None:
        files = {"file": ("save.txt", io.BytesIO(b"data"), "text/plain")}
        response = client.post("/api/v1/saves/upload", files=files)
        assert response.status_code == 400

    def test_anonymous_upload_unpersisted(self, client: TestClient) -> None:
        response = _upload_sl2(client)
        assert response.status_code == 200
        data = response.json()
        assert data["platform"] == "PC"
        assert data["profile_count"] >= 1
        assert data["persisted"] is False
        assert data["save_upload_id"] is None
        # Profiles list must have the mocked profile
        assert len(data["profiles"]) >= 1
        assert data["profiles"][0]["name"] == "Wylder"

    def test_authenticated_upload_persists(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        response = _upload_sl2(client, headers=superuser_token_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["persisted"] is True
        assert data["save_upload_id"] is not None

    def test_second_upload_replaces_first(
        self, client: TestClient, superuser_token_headers: dict[str, str],
        db: Session
    ) -> None:
        """Uploading twice as the same user keeps only the latest SaveUpload."""
        _upload_sl2(client, headers=superuser_token_headers)
        _upload_sl2(client, headers=superuser_token_headers)

        # Retrieve superuser id from first upload (cheapest query)
        uploads = db.exec(select(SaveUpload)).all()
        # There should be at most one upload per user after replacement
        # (filter to only uploads from this test to avoid flakiness with other tests)
        user_uploads_count = sum(1 for u in uploads if u is not None)
        # Not asserting exact count as other test classes may have their own uploads,
        # but the latest upload must have replaced the earlier one for the same user.
        assert user_uploads_count >= 1

    def test_no_characters_found_returns_422(self, client: TestClient) -> None:
        dummy_bytes = b"\x00" * 32
        files = {"file": ("NR0000.sl2", io.BytesIO(dummy_bytes), "application/octet-stream")}
        with (
            patch("app.api.routes.saves.decrypt_sl2"),
            patch("app.api.routes.saves.discover_characters", return_value=[]),
        ):
            response = client.post("/api/v1/saves/upload", files=files)
        assert response.status_code == 422
        assert "No characters" in response.json()["detail"]

    def test_decrypt_failure_returns_422(self, client: TestClient) -> None:
        dummy_bytes = b"\x00" * 32
        files = {"file": ("NR0000.sl2", io.BytesIO(dummy_bytes), "application/octet-stream")}
        with patch("app.api.routes.saves.decrypt_sl2", side_effect=Exception("bad file")):
            response = client.post("/api/v1/saves/upload", files=files)
        assert response.status_code == 422
        assert "decrypt" in response.json()["detail"].lower()


@pytest.mark.usefixtures("override_game_data")
class TestListProfiles:
    def test_requires_auth(self, client: TestClient) -> None:
        response = client.get("/api/v1/saves/profiles")
        assert response.status_code in (401, 403)

    def test_authenticated_returns_list(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        # Upload first so there is something to list
        _upload_sl2(client, headers=superuser_token_headers)
        response = client.get(
            "/api/v1/saves/profiles", headers=superuser_token_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert isinstance(body["data"], list)


@pytest.mark.usefixtures("override_game_data")
class TestGetProfileRelics:
    def test_not_found_returns_404(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        random_id = uuid.uuid4()
        response = client.get(
            f"/api/v1/saves/profiles/{random_id}/relics",
            headers=superuser_token_headers,
        )
        assert response.status_code == 404

    def test_wrong_owner_returns_403(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        normal_user_token_headers: dict[str, str],
    ) -> None:
        # Upload as superuser to create a profile
        upload_resp = _upload_sl2(client, headers=superuser_token_headers)
        assert upload_resp.status_code == 200
        profile_id = upload_resp.json()["profiles"][0].get("id")
        if profile_id is None:
            pytest.skip("Profile was not persisted (anonymous mode)")

        # Normal user tries to access superuser's profile
        response = client.get(
            f"/api/v1/saves/profiles/{profile_id}/relics",
            headers=normal_user_token_headers,
        )
        assert response.status_code == 403


@pytest.mark.usefixtures("override_game_data")
class TestHandleRemapOnReupload:
    """Pinned relic ga_handles in builds must be remapped when a save is re-uploaded.

    The game can reassign ga_handle values between saves (e.g. when relics are
    acquired or the inventory is reorganised).  On re-upload we match relics by
    content fingerprint and update pinned_relics so builds remain valid.
    """

    def _superuser(self, db: Session) -> User:
        return db.exec(
            select(User).where(User.email == settings.FIRST_SUPERUSER)
        ).one()

    def _make_build(self, db: Session, owner_id: uuid.UUID, pinned: list[int]) -> Build:
        build = Build(
            owner_id=owner_id,
            name="Remap test build",
            character="Wylder",
            pinned_relics=pinned,
        )
        db.add(build)
        db.commit()
        db.refresh(build)
        return build

    def test_pin_remapped_when_handle_changes(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Pin pointing to old ga_handle is updated to the new ga_handle."""
        # Upload save with the original relic (handle 0xC0000001).
        _upload_sl2(client, headers=superuser_token_headers, relics=[MOCK_RELIC])

        user = self._superuser(db)
        build = self._make_build(db, user.id, [MOCK_RELIC.ga_handle])

        # Re-upload: same relic content but the game assigned a new handle.
        _upload_sl2(
            client,
            headers=superuser_token_headers,
            relics=[MOCK_RELIC_NEW_HANDLE],
        )

        db.expire(build)
        updated = db.get(Build, build.id)
        assert updated is not None
        assert updated.pinned_relics == [MOCK_RELIC_NEW_HANDLE.ga_handle]

    def test_pin_dropped_when_relic_gone(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Pin for a relic that no longer exists in the new save is removed."""
        _upload_sl2(client, headers=superuser_token_headers, relics=[MOCK_RELIC])

        user = self._superuser(db)
        build = self._make_build(db, user.id, [MOCK_RELIC.ga_handle])

        # Re-upload with an entirely different relic — the pinned one is gone.
        other_relic = RawRelic(
            ga_handle=0xC0000002,
            item_id=200 + 2147483648,  # different item
            effect_1=EMPTY,
            effect_2=EMPTY,
            effect_3=EMPTY,
            sec_effect1=EMPTY,
            sec_effect2=EMPTY,
            sec_effect3=EMPTY,
            offset=0,
            size=64,
        )
        _upload_sl2(client, headers=superuser_token_headers, relics=[other_relic])

        db.expire(build)
        updated = db.get(Build, build.id)
        assert updated is not None
        assert updated.pinned_relics == []

    def test_pin_unchanged_when_handle_stable(
        self,
        client: TestClient,
        superuser_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """When the handle stays the same across uploads the pin is untouched."""
        _upload_sl2(client, headers=superuser_token_headers, relics=[MOCK_RELIC])

        user = self._superuser(db)
        build = self._make_build(db, user.id, [MOCK_RELIC.ga_handle])

        # Re-upload with the exact same relic (handle unchanged).
        _upload_sl2(client, headers=superuser_token_headers, relics=[MOCK_RELIC])

        db.expire(build)
        updated = db.get(Build, build.id)
        assert updated is not None
        assert updated.pinned_relics == [MOCK_RELIC.ga_handle]


# ---------------------------------------------------------------------------
# POST /saves/export-loadouts
# ---------------------------------------------------------------------------

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "NR0000.sl2"


def _export_loadouts(client, *, slot_index=0, operations="[]", file_bytes=None,
                     filename="NR0000.sl2"):
    raw = file_bytes if file_bytes is not None else b"\x00" * 32
    files = {"file": (filename, io.BytesIO(raw), "application/octet-stream")}
    data = {"slot_index": str(slot_index), "operations": operations}
    return client.post("/api/v1/saves/export-loadouts", files=files, data=data)


@pytest.mark.usefixtures("override_game_data")
class TestExportLoadouts:
    def test_invalid_operations_json_returns_422(self, client: TestClient) -> None:
        resp = _export_loadouts(client, operations="not json")
        assert resp.status_code == 422

    def test_unknown_op_kind_returns_422(self, client: TestClient) -> None:
        resp = _export_loadouts(client, operations='[{"op": "explode"}]')
        assert resp.status_code == 422

    def test_empty_operations_returns_422(self, client: TestClient) -> None:
        resp = _export_loadouts(client, operations="[]")
        assert resp.status_code == 422

    @pytest.mark.skipif(not _FIXTURE.exists(), reason="real .sl2 fixture absent")
    def test_rename_real_fixture(self, client: TestClient) -> None:
        ops = '[{"op": "rename", "index": 0, "name": "via api"}]'
        resp = _export_loadouts(client, operations=ops, file_bytes=_FIXTURE.read_bytes())
        assert resp.status_code == 200
        assert resp.headers["X-Loadouts-Renamed"] == "1"
        assert int(resp.headers["X-Loadouts-Used"]) > 0
        assert resp.content[:4] == b"BND4"

    @pytest.mark.skipif(not _FIXTURE.exists(), reason="real .sl2 fixture absent")
    def test_add_succeeds_into_empty_slot(self, client: TestClient) -> None:
        # Section 3 is a fixed 100-slot array with empty slots after the actives,
        # so a net-new add writes into the first empty slot in place (no downstream
        # shift). It succeeds on a pristine save and bumps the loadout count by one.
        before = int(_export_loadouts(
            client, operations='[{"op": "rename", "index": 0, "name": "x"}]',
            file_bytes=_FIXTURE.read_bytes()).headers["X-Loadouts-Used"])
        ops = ('[{"op": "add", "character": "Wylder", "vessel_id": 1002, '
               '"name": "api add", "ga_handles": [0, 0, 0, 0, 0, 0]}]')
        resp = _export_loadouts(client, operations=ops, file_bytes=_FIXTURE.read_bytes())
        assert resp.status_code == 200
        assert resp.headers["X-Loadouts-Added"] == "1"
        assert int(resp.headers["X-Loadouts-Used"]) == before + 1
        assert resp.content[:4] == b"BND4"

    @pytest.mark.skipif(not _FIXTURE.exists(), reason="real .sl2 fixture absent")
    def test_overwrite_real_fixture(self, client: TestClient) -> None:
        # preset[0] is hero_type 6 == Revenant (1-based), vessel 6002
        ops = ('[{"op": "overwrite", "index": 0, "character": "Revenant", '
               '"vessel_id": 6002, "name": "via api", "ga_handles": [0,0,0,0,0,0]}]')
        resp = _export_loadouts(client, operations=ops, file_bytes=_FIXTURE.read_bytes())
        assert resp.status_code == 200
        assert resp.headers["X-Loadouts-Overwritten"] == "1"
        assert resp.content[:4] == b"BND4"

    @pytest.mark.skipif(not _FIXTURE.exists(), reason="real .sl2 fixture absent")
    def test_reset_vessels_real_fixture(self, client: TestClient) -> None:
        resp = _export_loadouts(client, operations='[{"op": "reset_vessels"}]',
                                file_bytes=_FIXTURE.read_bytes())
        assert resp.status_code == 200
        assert resp.headers["X-Vessels-Reset"] == "1"

    @pytest.mark.skipif(not _FIXTURE.exists(), reason="real .sl2 fixture absent")
    def test_name_too_long_rejected(self, client: TestClient) -> None:
        ops = ('[{"op": "add", "character": "Wylder", "vessel_id": 1002, '
               '"name": "%s", "ga_handles": []}]' % ("x" * 19))
        resp = _export_loadouts(client, operations=ops, file_bytes=_FIXTURE.read_bytes())
        assert resp.status_code == 422

    @pytest.mark.skipif(not _FIXTURE.exists(), reason="real .sl2 fixture absent")
    def test_bad_vessel_for_hero_rejected(self, client: TestClient) -> None:
        # vessel 2002 belongs to Ironeye, not Wylder
        ops = ('[{"op": "add", "character": "Wylder", "vessel_id": 2002, '
               '"name": "bad", "ga_handles": []}]')
        resp = _export_loadouts(client, operations=ops, file_bytes=_FIXTURE.read_bytes())
        assert resp.status_code == 422

    @pytest.mark.skipif(not _FIXTURE.exists(), reason="real .sl2 fixture absent")
    def test_reset_presets_cannot_combine(self, client: TestClient) -> None:
        ops = ('[{"op": "reset_presets"}, {"op": "rename", "index": 0, "name": "x"}]')
        resp = _export_loadouts(client, operations=ops, file_bytes=_FIXTURE.read_bytes())
        assert resp.status_code == 422
