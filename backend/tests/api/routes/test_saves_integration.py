"""Integration test: upload a REAL .sl2 save file through the full stack.

Requires the fixture file to be present at backend/tests/fixtures/NR0000.sl2.
Copy it with:
    cp "C:\\Users\\aliig\\AppData\\Roaming\\Nightreign\\76561198039949473\\NR0000.sl2" \\
       backend/tests/fixtures/NR0000.sl2

Run with:
    uv run pytest backend/tests/ -m integration -v
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

FIXTURE_PATH = Path(__file__).parent.parent.parent / "fixtures" / "NR0000.sl2"
# A save from the SAME account with a materially different relic inventory
# (~hundreds of relics added/removed vs NR0000.sl2), used to prove the
# save-to-save comparison actually runs — or is suppressed — as expected.
ADDED_FIXTURE_PATH = FIXTURE_PATH.parent / "NR0000-added-to-96.sl2"

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present — run with -m integration after copying NR0000.sl2",
)
@pytest.mark.usefixtures("override_game_data")
def test_upload_real_sl2_anonymous(client: TestClient) -> None:
    """Upload the real NR0000.sl2 and verify the full nrplanner parsing stack."""
    with FIXTURE_PATH.open("rb") as f:
        response = client.post(
            "/api/v1/saves/upload",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
        )

    assert response.status_code == 200, response.text
    data = response.json()

    assert data["platform"] == "PC"
    assert data["profile_count"] >= 1
    assert data["persisted"] is False  # anonymous upload
    assert len(data["profiles"]) >= 1

    valid_colors = {"Red", "Blue", "Yellow", "Green", "White"}
    for prof in data["profiles"]:
        assert isinstance(prof["name"], str)
        assert prof["name"] != ""
        assert isinstance(prof["relics"], list)
        for relic in prof["relics"]:
            assert relic["color"] in valid_colors, f"Unexpected color: {relic['color']}"
            assert isinstance(relic["effect_1"], int)
            assert isinstance(relic["effect_2"], int)
            assert isinstance(relic["effect_3"], int)
        # Every owned relic has an ItemEntry row, so acquisition_id must be a
        # unique positive int for all of them (it drives the inventory's
        # newest/oldest-acquired sort).
        acq_ids = [r["acquisition_id"] for r in prof["relics"]]
        assert all(isinstance(a, int) and a > 0 for a in acq_ids)
        assert len(set(acq_ids)) == len(acq_ids)


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present",
)
@pytest.mark.usefixtures("override_game_data")
def test_upload_real_sl2_authenticated(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """Authenticated upload with real fixture — data is persisted to DB."""
    with FIXTURE_PATH.open("rb") as f:
        response = client.post(
            "/api/v1/saves/upload",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            headers=superuser_token_headers,
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["persisted"] is True
    assert data["save_upload_id"] is not None

    # acquisition_id must survive persistence (not just the parse response).
    from app.models import Relic

    relics = db.exec(select(Relic)).all()
    assert relics
    assert all(
        r.acquisition_id is not None and r.acquisition_id > 0 for r in relics
    )


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present",
)
@pytest.mark.usefixtures("override_game_data")
def test_stream_upload_sets_profile_relics_hash(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """The streaming upload must cache relics_hash on each Profile exactly
    like the non-streaming path — GET /optimize/snapshot freshness compares
    against it, so a missing hash forces a re-optimize on every view."""
    from app.core.config import settings
    from app.models import Profile, User

    with FIXTURE_PATH.open("rb") as f:
        response = client.post(
            "/api/v1/saves/upload/stream",
            files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            headers=superuser_token_headers,
        )

    assert response.status_code == 200, response.text
    assert "upload_complete" in response.text

    user = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).first()
    assert user is not None
    profiles = db.exec(
        select(Profile).where(Profile.owner_id == user.id)
    ).all()
    assert profiles, "streaming upload should persist profiles"
    for profile in profiles:
        assert profile.relics_hash, (
            f"profile slot {profile.slot_index} has no relics_hash — "
            "snapshot freshness will never hit for streaming uploads"
        )


def _upload(client: TestClient, path: Path, headers: dict[str, str]):
    with path.open("rb") as f:
        return client.post(
            "/api/v1/saves/upload",
            files={"file": (path.name, f, "application/octet-stream")},
            headers=headers,
        )


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present",
)
@pytest.mark.usefixtures("override_game_data")
def test_upload_persists_save_owner_id(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """The uploaded save's owning-account SteamID64 is stored on SaveUpload so
    the next upload can tell whether it belongs to the same account."""
    from app.core.config import settings
    from app.models import SaveUpload, User

    resp = _upload(client, FIXTURE_PATH, superuser_token_headers)
    assert resp.status_code == 200, resp.text

    user = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).first()
    assert user is not None
    save = db.exec(
        select(SaveUpload).where(SaveUpload.owner_id == user.id)
    ).first()
    assert save is not None
    assert save.save_owner_id == "76561198039949473"


@pytest.mark.skipif(
    not (FIXTURE_PATH.exists() and ADDED_FIXTURE_PATH.exists()),
    reason="Both real save fixtures required",
)
@pytest.mark.usefixtures("override_game_data")
def test_same_account_reupload_reports_relic_delta(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """Two uploads from the SAME account produce a real relic delta — the
    save-to-save comparison runs as normal."""
    first = _upload(client, FIXTURE_PATH, superuser_token_headers)
    assert first.status_code == 200, first.text

    second = _upload(client, ADDED_FIXTURE_PATH, superuser_token_headers)
    assert second.status_code == 200, second.text
    delta = second.json()["relic_delta"]
    assert delta["added"] > 0 or delta["removed"] > 0, (
        "same-account re-upload with a changed inventory should report a delta"
    )


@pytest.mark.skipif(
    not (FIXTURE_PATH.exists() and ADDED_FIXTURE_PATH.exists()),
    reason="Both real save fixtures required",
)
@pytest.mark.usefixtures("override_game_data")
def test_different_account_reupload_suppresses_comparison(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the newly uploaded save belongs to a DIFFERENT account, the
    comparison is suppressed even though the relic inventory differs — no
    relic delta and no affected builds are reported."""
    # First upload stores the real account id (76561198039949473).
    first = _upload(client, FIXTURE_PATH, superuser_token_headers)
    assert first.status_code == 200, first.text

    # Force the SECOND upload to look like a different account. The relics
    # genuinely differ (added fixture), so any reported delta would prove the
    # comparison wrongly ran across accounts.
    monkeypatch.setattr(
        "app.api.routes.saves.read_owner_steam_id",
        lambda *args, **kwargs: "11111111111111111",
    )
    second = _upload(client, ADDED_FIXTURE_PATH, superuser_token_headers)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["relic_delta"]["added"] == 0
    assert body["relic_delta"]["removed"] == 0
    assert body["affected_builds"] == []
