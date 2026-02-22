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

FIXTURE_PATH = Path(__file__).parent.parent.parent / "fixtures" / "NR0000.sl2"

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
    assert data["character_count"] >= 1
    assert data["persisted"] is False  # anonymous upload
    assert len(data["characters"]) >= 1

    valid_colors = {"Red", "Blue", "Yellow", "Green", "White"}
    for char in data["characters"]:
        assert isinstance(char["name"], str)
        assert char["name"] != ""
        assert isinstance(char["relics"], list)
        for relic in char["relics"]:
            assert relic["color"] in valid_colors, f"Unexpected color: {relic['color']}"
            assert isinstance(relic["effect_1"], int)
            assert isinstance(relic["effect_2"], int)
            assert isinstance(relic["effect_3"], int)


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present",
)
@pytest.mark.usefixtures("override_game_data")
def test_upload_real_sl2_authenticated(
    client: TestClient, superuser_token_headers: dict[str, str]
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
