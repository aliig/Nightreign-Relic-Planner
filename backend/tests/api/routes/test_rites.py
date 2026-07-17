"""Tests for POST /api/v1/saves/rites/plan (bulk purchase + build-aware cull).

Validation paths run against any DB; the full-plan test is gated on the real save
fixture (backend/tests/fixtures/NR0000.sl2), mirroring the nrplanner writer tests.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "NR0000.sl2"
requires_fixture = pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present — copy NR0000.sl2 to backend/tests/fixtures/",
)

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

_SCENIC_BUCKET = {"is_deep": False, "version": "1.03", "quantity": 5}


@pytest.mark.usefixtures("override_game_data")
class TestRitesPlanValidation:
    def test_requires_a_build(self, client: TestClient) -> None:
        """No builds -> 422 (keeping/culling are build-aware)."""
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("save.sl2", b"BND4dummy")},
            data={"slot_index": "0", "buckets": json.dumps([_SCENIC_BUCKET])},
        )
        assert resp.status_code == 422

    def test_rejects_bad_stop_mode(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("save.sl2", b"BND4dummy")},
            data={
                "slot_index": "0",
                "builds": json.dumps([_MINIMAL_BUILD]),
                "buckets": json.dumps([_SCENIC_BUCKET]),
                "stop_mode": "bogus",
            },
        )
        assert resp.status_code == 422


@requires_fixture
@pytest.mark.usefixtures("override_game_data")
class TestRitesPlanFull:
    def test_plan_is_well_formed(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
            data={
                "slot_index": "0",
                "builds": json.dumps([_MINIMAL_BUILD]),
                "buckets": json.dumps([_SCENIC_BUCKET]),
                "stop_mode": "fixed",
                "top_n": "5",
            },
        )
        assert resp.status_code == 200, resp.text
        plan = resp.json()

        # counts + Murk math are internally consistent and faithful (never negative).
        assert plan["generated"] <= 5
        assert plan["kept"] + plan["duds"] == plan["generated"]
        assert plan["kept"] == len(plan["keepers"])
        assert plan["murk_after"] >= 0
        assert plan["murk_after"] == plan["murk_before"] - (
            plan["murk_cost"] - plan["murk_refunded"]
        )
        assert plan["murk_delta"] == plan["murk_after"] - plan["murk_before"]

        # keepers carry a mint-ready spec.
        for k in plan["keepers"]:
            assert {"real_id", "effects", "curses", "color", "tier", "builds"} <= set(k)
            assert len(k["effects"]) == 3 and len(k["curses"]) == 3

        # cull candidates are plain owned ga_handles.
        assert isinstance(plan["cull_candidates"], list)
        assert all(isinstance(h, int) for h in plan["cull_candidates"])
        assert plan["storage_left"] >= 0
