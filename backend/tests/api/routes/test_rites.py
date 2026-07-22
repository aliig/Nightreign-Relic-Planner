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


class TestValidatedSeedFps:
    """_validated_seed_fps: snapshot-seed freshness against the uploaded save."""

    @staticmethod
    def _setup(ds):
        from nrplanner.changes import (  # noqa: PLC0415
            build_signature,
            fingerprint_owned,
            relevant_relics_signature,
        )
        from nrplanner.models import (  # noqa: PLC0415
            BuildDefinition,
            OwnedRelic,
            WeightGroup,
        )
        from nrplanner.optimizer import OPTIMIZER_VERSION  # noqa: PLC0415
        from nrplanner.rites import BuildContext  # noqa: PLC0415

        from app.api.routes.saves import _validated_seed_fps  # noqa: PLC0415
        from app.core.game_data import game_data_version  # noqa: PLC0415

        empty = 4294967295
        build = BuildDefinition(
            id="b", name="B", character="Wylder",
            groups=[WeightGroup(weight=10, effects=[100])],
        )
        ctx = BuildContext(build=build, hero_type=1, name="B",
                           build_id="11111111-1111-1111-1111-111111111111")
        owned = [OwnedRelic(
            ga_handle=0xC1000001, item_id=100 + 2147483648, real_id=100,
            color="Red", effects=[100, empty, empty],
            curses=[empty, empty, empty], is_deep=False, name="R",
            tier="Delicate",
        )]
        fps = {fingerprint_owned(owned[0])}
        seed = {
            "build_hash": build_signature(build),
            "game_data_version": game_data_version(),
            "optimizer_version": OPTIMIZER_VERSION,
            "relevant_relics_hash": relevant_relics_signature(
                build, [(fingerprint_owned(o), o.ga_handle) for o in owned], ds),
            "used_fps": fps,
        }
        return _validated_seed_fps, ctx, owned, seed, fps

    def test_fresh_seed_accepted(self, override_game_data) -> None:
        from app.core.game_data import get_game_data  # noqa: PLC0415
        ds = get_game_data()
        fn, ctx, owned, seed, fps = self._setup(ds)
        out = fn([ctx], {ctx.build_id: seed}, owned, ds)
        assert out == {ctx.build_id: fps}

    def test_stale_build_hash_rejected(self, override_game_data) -> None:
        from app.core.game_data import get_game_data  # noqa: PLC0415
        ds = get_game_data()
        fn, ctx, owned, seed, _fps = self._setup(ds)
        seed = {**seed, "build_hash": "not-the-hash"}
        assert fn([ctx], {ctx.build_id: seed}, owned, ds) == {}

    def test_relevant_inventory_change_rejected(self, override_game_data) -> None:
        from nrplanner.models import OwnedRelic  # noqa: PLC0415

        from app.core.game_data import get_game_data  # noqa: PLC0415
        ds = get_game_data()
        fn, ctx, owned, seed, _fps = self._setup(ds)
        empty = 4294967295
        grown = owned + [OwnedRelic(
            ga_handle=0xC1000002, item_id=100 + 2147483648, real_id=100,
            color="Red", effects=[100, empty, empty],
            curses=[empty, empty, empty], is_deep=False, name="R2",
            tier="Delicate",
        )]
        assert fn([ctx], {ctx.build_id: seed}, grown, ds) == {}

    def test_irrelevant_inventory_change_accepted(self, override_game_data) -> None:
        from app.core.game_data import get_game_data  # noqa: PLC0415
        ds = get_game_data()
        from nrplanner.models import OwnedRelic  # noqa: PLC0415
        fn, ctx, owned, seed, fps = self._setup(ds)
        empty = 4294967295
        grown = owned + [OwnedRelic(
            ga_handle=0xC1000003, item_id=200 + 2147483648, real_id=200,
            color="Red", effects=[999999999, empty, empty],
            curses=[empty, empty, empty], is_deep=False, name="Junk",
            tier="Delicate",
        )]
        assert fn([ctx], {ctx.build_id: seed}, grown, ds) == {ctx.build_id: fps}

    def test_version_mismatch_rejected(self, override_game_data) -> None:
        from app.core.game_data import get_game_data  # noqa: PLC0415
        ds = get_game_data()
        fn, ctx, owned, seed, _fps = self._setup(ds)
        seed = {**seed, "optimizer_version": seed["optimizer_version"] - 1}
        assert fn([ctx], {ctx.build_id: seed}, owned, ds) == {}
