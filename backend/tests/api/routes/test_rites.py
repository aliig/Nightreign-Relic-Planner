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

    @pytest.mark.parametrize("top_n", [0, 11])
    def test_rejects_out_of_range_top_n(self, client: TestClient, top_n: int) -> None:
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("save.sl2", b"BND4dummy")},
            data={
                "slot_index": "0",
                "builds": json.dumps([_MINIMAL_BUILD]),
                "buckets": json.dumps([_SCENIC_BUCKET]),
                "top_n": str(top_n),
            },
        )
        assert resp.status_code == 422
        assert "top_n" in resp.json()["detail"]

    def test_rejects_malformed_sold_handles(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("save.sl2", b"BND4dummy")},
            data={
                "slot_index": "0",
                "builds": json.dumps([_MINIMAL_BUILD]),
                "buckets": json.dumps([{"is_deep": False, "version": "1.03"}]),
                "stop_mode": "all_murk",
                "sold_handles": "not-json",
            },
        )
        assert resp.status_code == 422

    def test_rejects_sold_handles_outside_all_murk(self, client: TestClient) -> None:
        """Staged sells only feed the all_murk cycle — fixed/budget stay untouched."""
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("save.sl2", b"BND4dummy")},
            data={
                "slot_index": "0",
                "builds": json.dumps([_MINIMAL_BUILD]),
                "buckets": json.dumps([_SCENIC_BUCKET]),
                "stop_mode": "fixed",
                "sold_handles": json.dumps([123]),
            },
        )
        assert resp.status_code == 422
        assert "all_murk" in resp.json()["detail"]


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
        assert plan["murk_delta"] == plan["murk_refunded"] - plan["murk_cost"]
        assert plan["murk_after"] == (
            plan["murk_before"] + plan["pending_sold_refund"] + plan["murk_delta"]
        )
        assert plan["pending_sold"] == 0  # fixed mode never consumes staged sells

        # keepers carry a mint-ready spec, with loadout ranks aligned to builds.
        for k in plan["keepers"]:
            assert {"real_id", "effects", "curses", "color", "tier", "builds"} <= set(k)
            assert len(k["effects"]) == 3 and len(k["curses"]) == 3
            assert len(k["build_ranks"]) == len(k["builds"])
            if k["reason"] == "build":
                assert all(r >= 1 for r in k["build_ranks"])

        # pre-owned relics are never analyzed or sold by the plan.
        assert "cull_candidates" not in plan
        assert plan["storage_left"] >= 0


@requires_fixture
@pytest.mark.usefixtures("override_game_data")
class TestRitesAllMurkCycle:
    """all_murk walk + staged sells (sold_handles) against the real fixture.

    Uses no builds + a match-everything exclusion rule so no optimizer runs —
    the test exercises generation, the walk, and the staged-sell plumbing only.
    """

    _SELL_ALL = json.dumps([{"effect_counts": [1, 2, 3]}])

    @staticmethod
    def _save_state():
        """(owned_count, murk, one sellable ga_handle) from the fixture."""
        import tempfile
        from pathlib import Path as _P

        from nrplanner import LoadoutHandler, decrypt_sl2, parse_relics
        from nrplanner.writer import read_favorite_handles

        from app.core.game_data import get_game_data

        with tempfile.TemporaryDirectory() as td:
            decrypt_sl2(FIXTURE_PATH, td)
            blob = (_P(td) / "USERDATA_00").read_bytes()
        relics, items_end = parse_relics(blob)
        ds = get_game_data()
        loadout = LoadoutHandler(ds)
        loadout.parse(blob)
        equipped = set(loadout.relic_ga_hero_map.keys())
        favorites = read_favorite_handles(blob, items_end)
        sellable = next(
            r.ga_handle for r in relics
            if r.ga_handle not in equipped and r.ga_handle not in favorites
        )
        return len(relics), sellable, equipped | favorites

    def _plan(self, client: TestClient, sold: list[int]) -> dict:
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
            data={
                "slot_index": "0",
                "buckets": json.dumps([{"is_deep": False, "version": "1.03"}]),
                "stop_mode": "all_murk",
                "exclusion_rules": self._SELL_ALL,
                "sold_handles": json.dumps(sold),
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_staged_sell_frees_slot_and_funds_walk(
        self, client: TestClient
    ) -> None:
        owned_count, sellable, _ = self._save_state()
        plan = self._plan(client, [sellable])

        assert plan["pending_sold"] == 1
        assert plan["pending_sold_refund"] > 0
        # Pure in-game cap, post-staged-sells; no ghost-capacity clamp.
        assert plan["storage_left"] == 1950 - (owned_count - 1)
        # Walk invariants: everything sold back, wallet drained below one buy.
        assert plan["kept"] == 0 and plan["duds"] == plan["generated"]
        assert plan["limited_by"] == "murk"
        assert 0 <= plan["murk_after"] < 600
        assert plan["murk_delta"] == plan["murk_refunded"] - plan["murk_cost"]
        assert plan["murk_after"] == (
            plan["murk_before"] + plan["pending_sold_refund"] + plan["murk_delta"]
        )

    def test_deterministic_rerun(self, client: TestClient) -> None:
        """Same save + same staged sells -> byte-identical plan (anti-save-scum)."""
        _, sellable, _ = self._save_state()
        assert self._plan(client, [sellable]) == self._plan(client, [sellable])

    def test_protected_sold_handle_rejected(self, client: TestClient) -> None:
        _, _, protected = self._save_state()
        if not protected:
            pytest.skip("fixture has no equipped/bookmarked relic")
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
            data={
                "slot_index": "0",
                "buckets": json.dumps([{"is_deep": False, "version": "1.03"}]),
                "stop_mode": "all_murk",
                "sold_handles": json.dumps([next(iter(protected))]),
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "protected_relics"

    def test_unknown_sold_handle_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
            data={
                "slot_index": "0",
                "buckets": json.dumps([{"is_deep": False, "version": "1.03"}]),
                "stop_mode": "all_murk",
                "sold_handles": json.dumps([0xDEADBEEF]),
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "unknown_relics"


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

    def test_staged_snapshot_seed_rejected_against_raw_save(
        self, override_game_data
    ) -> None:
        """A snapshot computed from a STAGED effective inventory (its relevant
        hash covers mint fingerprints the raw save does not contain) must fail
        seed validation against the uploaded save — rites reuse only ever
        trusts owned-only results, so a staged optimizer run can never leak
        hypothetical relics into the keeper walk."""
        from nrplanner.changes import (  # noqa: PLC0415
            fingerprint_owned,
            relevant_relics_signature,
        )
        from nrplanner.models import OwnedRelic  # noqa: PLC0415

        from app.core.game_data import get_game_data  # noqa: PLC0415
        ds = get_game_data()
        fn, ctx, owned, seed, _fps = self._setup(ds)
        empty = 4294967295
        # Build-relevant staged mint (effect 100) under a synthetic handle.
        mint = OwnedRelic(
            ga_handle=-1, item_id=100 + 2147483648, real_id=100,
            color="Red", effects=[100, empty, empty],
            curses=[empty, empty, empty], is_deep=False, name="Mint",
            tier="Delicate",
        )
        effective = owned + [mint]
        staged_seed = {
            **seed,
            "relevant_relics_hash": relevant_relics_signature(
                ctx.build,
                [(fingerprint_owned(o), o.ga_handle) for o in effective],
                ds,
            ),
        }
        assert fn([ctx], {ctx.build_id: staged_seed}, owned, ds) == {}
