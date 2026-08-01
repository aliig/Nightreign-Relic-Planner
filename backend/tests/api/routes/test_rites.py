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

    def test_rejects_malformed_staged_mints(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("save.sl2", b"BND4dummy")},
            data={
                "slot_index": "0",
                "builds": json.dumps([_MINIMAL_BUILD]),
                "buckets": json.dumps([_SCENIC_BUCKET]),
                "staged_mints": "not-json",
            },
        )
        assert resp.status_code == 422
        assert "staged_mints" in str(resp.json()["detail"])

    def test_rejects_malformed_staged_favorites(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("save.sl2", b"BND4dummy")},
            data={
                "slot_index": "0",
                "builds": json.dumps([_MINIMAL_BUILD]),
                "buckets": json.dumps([_SCENIC_BUCKET]),
                "staged_favorites": json.dumps([123]),  # array, not an object
            },
        )
        assert resp.status_code == 422
        assert "staged_favorites" in str(resp.json()["detail"])

    def test_rejects_staged_mint_missing_fields(self, client: TestClient) -> None:
        """A mint spec must carry handle/real_id/effects/curses (StagedMint)."""
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("save.sl2", b"BND4dummy")},
            data={
                "slot_index": "0",
                "builds": json.dumps([_MINIMAL_BUILD]),
                "buckets": json.dumps([_SCENIC_BUCKET]),
                "staged_mints": json.dumps([{"handle": -1}]),
            },
        )
        assert resp.status_code == 422
        assert "staged_mints" in str(resp.json()["detail"])


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


@requires_fixture
@pytest.mark.usefixtures("override_game_data")
class TestRitesStagedMurk:
    """Live Murk emulation: staged (unexported) rites purchases persist into
    every later plan — the wallet spends down, staged mints occupy storage,
    and re-planning can never double-spend or re-roll. Scenarios mirror real
    user journeys (spend → stage → refresh/re-run → export).

    No builds and no rules -> the plan keeps every generated relic and runs
    no optimizer, so these exercise generation + Murk/storage accounting only.
    """

    _SCENIC = {"is_deep": False, "version": "1.03"}
    _DEEP = {"is_deep": True, "version": "1.03"}

    @staticmethod
    def _profile(client: TestClient) -> dict:
        with FIXTURE_PATH.open("rb") as f:
            resp = client.post(
                "/api/v1/saves/upload",
                files={"file": ("NR0000.sl2", f, "application/octet-stream")},
            )
        assert resp.status_code == 200, resp.text
        return next(p for p in resp.json()["profiles"] if p["relics"])

    @staticmethod
    def _plan(client: TestClient, slot: int, **extra) -> dict:
        data = {"slot_index": str(slot), **extra}
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
            data=data,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    @staticmethod
    def _fp(k: dict) -> tuple:
        return (k["real_id"], tuple(k["effects"]), tuple(k["curses"]))

    @staticmethod
    def _stage(keepers: list[dict], start: int = -1) -> list[dict]:
        """Keepers -> the wire shape the frontend stages (synthetic handles)."""
        return [
            {
                "handle": start - i,
                "real_id": k["real_id"],
                "effects": k["effects"],
                "curses": k["curses"],
            }
            for i, k in enumerate(keepers)
        ]

    def _spend_plan(self, client: TestClient, slot: int, qty: int) -> dict:
        """A fixed-mode 'buy qty scenics, keep all' plan (no builds/rules)."""
        return self._plan(
            client, slot,
            buckets=json.dumps([{**self._SCENIC, "quantity": qty}]),
            stop_mode="fixed",
        )

    def test_staged_spend_persists_and_replays_the_stream(
        self, client: TestClient
    ) -> None:
        """THE reported bug: spend Murk, stage the keepers, come back — the
        wallet must stay spent, and a same-bucket re-run must replay the same
        roll stream (no free re-roll, Murk only ever declines further)."""
        prof = self._profile(client)
        murk = prof["murks"]
        if murk < 2500:
            pytest.skip("fixture wallet too small for a 2x2-scenic scenario")

        plan_a = self._spend_plan(client, prof["slot_index"], qty=2)
        if plan_a["limited_by"]:
            pytest.skip(f"fixture-limited plan: {plan_a['limited_by']}")
        assert plan_a["kept"] == 2 and plan_a["murk_delta"] == -1200
        assert plan_a["murk_before"] == murk

        staged = {
            "staged_mints": json.dumps(self._stage(plan_a["keepers"])),
            "staged_murk_delta": str(plan_a["murk_delta"]),
        }
        plan_b = self._plan(
            client, prof["slot_index"],
            buckets=json.dumps([{**self._SCENIC, "quantity": 2}]),
            stop_mode="fixed", **staged,
        )
        # The wallet stayed spent — re-planning starts from the live value.
        assert plan_b["murk_before"] == murk - 1200
        # Same save state -> same roll stream: the re-run regenerates the very
        # same relics (buying real duplicate copies, never new rolls).
        assert sorted(map(self._fp, plan_b["keepers"])) == sorted(
            map(self._fp, plan_a["keepers"])
        )
        # And it pays full price for them again — no manufactured value.
        assert plan_b["murk_delta"] == -1200
        assert plan_b["murk_after"] == (
            plan_b["murk_before"]
            + plan_b["pending_sold_refund"]
            + plan_b["murk_delta"]
        )

    def test_identical_staged_state_is_deterministic(
        self, client: TestClient
    ) -> None:
        """Spamming Find Keepers with the same staged diff changes nothing."""
        prof = self._profile(client)
        if prof["murks"] < 2500:
            pytest.skip("fixture wallet too small")
        plan_a = self._spend_plan(client, prof["slot_index"], qty=2)
        staged = {
            "staged_mints": json.dumps(self._stage(plan_a["keepers"])),
            "staged_murk_delta": str(plan_a["murk_delta"]),
        }
        args = dict(
            buckets=json.dumps([{**self._SCENIC, "quantity": 2}]),
            stop_mode="fixed", **staged,
        )
        first = self._plan(client, prof["slot_index"], **args)
        second = self._plan(client, prof["slot_index"], **args)
        assert first == second

    def test_positive_staged_delta_is_clamped(self, client: TestClient) -> None:
        """A client cannot manufacture planning Murk with a positive delta."""
        prof = self._profile(client)
        plan = self._plan(
            client, prof["slot_index"],
            buckets=json.dumps([{**self._SCENIC, "quantity": 1}]),
            stop_mode="fixed", staged_murk_delta=str(10**9),
        )
        assert plan["murk_before"] == prof["murks"]

    def test_staged_mints_occupy_storage(self, client: TestClient) -> None:
        """all_murk storage: staged (unexported) mints already hold slots."""
        prof = self._profile(client)
        if prof["murks"] < 2500:
            pytest.skip("fixture wallet too small")
        plan_a = self._spend_plan(client, prof["slot_index"], qty=2)
        sell_all = json.dumps([{"effect_counts": [1, 2, 3]}])
        base = self._plan(
            client, prof["slot_index"],
            buckets=json.dumps([self._SCENIC]), stop_mode="all_murk",
            exclusion_rules=sell_all,
        )
        staged = self._plan(
            client, prof["slot_index"],
            buckets=json.dumps([self._SCENIC]), stop_mode="all_murk",
            exclusion_rules=sell_all,
            staged_mints=json.dumps(self._stage(plan_a["keepers"])),
            staged_murk_delta=str(plan_a["murk_delta"]),
        )
        assert staged["storage_left"] == base["storage_left"] - 2

    def test_unmintable_staged_mint_rejected(self, client: TestClient) -> None:
        """Fidelity gate: an impossible relic can never enter the world state."""
        prof = self._profile(client)
        resp = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
            data={
                "slot_index": str(prof["slot_index"]),
                "buckets": json.dumps([{**self._SCENIC, "quantity": 1}]),
                "stop_mode": "fixed",
                "staged_mints": json.dumps(
                    [{"handle": -1, "real_id": 0, "effects": [], "curses": []}]
                ),
            },
        )
        assert resp.status_code == 422
        assert "mintable" in str(resp.json()["detail"])

    def test_sold_handles_credit_in_fixed_mode(self, client: TestClient) -> None:
        """Staged sells now fund every mode (in-game: sell first, then buy)."""
        prof = self._profile(client)
        sellable = next(
            (r for r in prof["relics"]
             if not r["equipped"] and not r["is_favorite"]),
            None,
        )
        if sellable is None:
            pytest.skip("fixture has no sellable relic")
        plan = self._plan(
            client, prof["slot_index"],
            buckets=json.dumps([{**self._SCENIC, "quantity": 1}]),
            stop_mode="fixed",
            sold_handles=json.dumps([sellable["ga_handle"]]),
        )
        assert plan["pending_sold"] == 1
        assert plan["pending_sold_refund"] > 0
        assert plan["murk_after"] == (
            plan["murk_before"]
            + plan["pending_sold_refund"]
            + plan["murk_delta"]
        )

    def test_bucket_streams_are_independent(self, client: TestClient) -> None:
        """Adding a deep bucket must not re-roll the scenic stream — one
        category's Nth roll is fixed for a given save, no cross-bucket
        perturbation (closes the 'buy 1 deep to shake the dice' lever)."""
        prof = self._profile(client)
        if prof["murks"] < 3500:
            pytest.skip("fixture wallet too small for scenic+deep")
        scenic_only = self._spend_plan(client, prof["slot_index"], qty=2)
        mixed = self._plan(
            client, prof["slot_index"],
            buckets=json.dumps([
                {**self._DEEP, "quantity": 1},
                {**self._SCENIC, "quantity": 2},
            ]),
            stop_mode="fixed",
        )
        if scenic_only["limited_by"] or mixed["limited_by"]:
            pytest.skip("fixture-limited plan")
        scenic_fps = sorted(
            self._fp(k) for k in mixed["keepers"] if not k["is_deep"]
        )
        assert scenic_fps == sorted(map(self._fp, scenic_only["keepers"]))

    def test_staged_favorites_drive_the_protected_sell_gate(
        self, client: TestClient
    ) -> None:
        """The gate judges LIVE bookmark state: un-bookmarking in-app makes a
        relic sellable (no 422), and bookmarking in-app protects one."""
        prof = self._profile(client)
        buckets = json.dumps([{**self._SCENIC, "quantity": 1}])
        base = {
            "slot_index": str(prof["slot_index"]),
            "buckets": buckets,
            "stop_mode": "fixed",
        }

        favorited = next(
            (r for r in prof["relics"]
             if r["is_favorite"] and not r["equipped"]),
            None,
        )
        if favorited is not None:
            h = favorited["ga_handle"]
            blocked = client.post(
                "/api/v1/saves/rites/plan",
                files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
                data={**base, "sold_handles": json.dumps([h])},
            )
            assert blocked.status_code == 422
            assert blocked.json()["detail"]["error"] == "protected_relics"

            unblocked = client.post(
                "/api/v1/saves/rites/plan",
                files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
                data={
                    **base,
                    "sold_handles": json.dumps([h]),
                    "staged_favorites": json.dumps({str(h): False}),
                },
            )
            assert unblocked.status_code == 200, unblocked.text
            assert unblocked.json()["pending_sold"] == 1

        sellable = next(
            (r for r in prof["relics"]
             if not r["equipped"] and not r["is_favorite"]),
            None,
        )
        if sellable is None and favorited is None:
            pytest.skip("fixture has neither a favorited nor a sellable relic")
        if sellable is not None:
            h = sellable["ga_handle"]
            protected = client.post(
                "/api/v1/saves/rites/plan",
                files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
                data={
                    **base,
                    "sold_handles": json.dumps([h]),
                    "staged_favorites": json.dumps({str(h): True}),
                },
            )
            assert protected.status_code == 422
            assert protected.json()["detail"]["error"] == "protected_relics"

    def test_add_capacity_reflects_the_staged_diff(
        self, client: TestClient
    ) -> None:
        """add_capacity is the EFFECTIVE export capacity: staged sells free a
        ghost slot each, already-staged mints have claimed theirs."""
        prof = self._profile(client)
        if prof["murks"] < 2500:
            pytest.skip("fixture wallet too small")
        plan_a = self._spend_plan(client, prof["slot_index"], qty=2)
        if plan_a["limited_by"]:
            pytest.skip(f"fixture-limited plan: {plan_a['limited_by']}")
        base_cap = plan_a["add_capacity"]

        minted = self._plan(
            client, prof["slot_index"],
            buckets=json.dumps([{**self._SCENIC, "quantity": 1}]),
            stop_mode="fixed",
            staged_mints=json.dumps(self._stage(plan_a["keepers"])),
            staged_murk_delta=str(plan_a["murk_delta"]),
        )
        assert minted["add_capacity"] == max(0, base_cap - 2)

        sellable = next(
            (r for r in prof["relics"]
             if not r["equipped"] and not r["is_favorite"]),
            None,
        )
        if sellable is not None:
            sold = self._plan(
                client, prof["slot_index"],
                buckets=json.dumps([{**self._SCENIC, "quantity": 1}]),
                stop_mode="fixed",
                sold_handles=json.dumps([sellable["ga_handle"]]),
            )
            assert sold["add_capacity"] == base_cap + 1

    def test_export_round_trip_lands_effective_murk(
        self, client: TestClient
    ) -> None:
        """Full journey, exactly as the app exports it: plan (with a staged
        sell) → export sells FIRST (credit + freed ghost slot) → mint with the
        batch delta → the final save holds exactly the plan's murk_after, and
        a fresh plan on the new save starts from it (export is what unlocks
        new rolls)."""
        prof = self._profile(client)
        if prof["murks"] < 1500:
            pytest.skip("fixture wallet too small")
        sellable = next(
            (r for r in prof["relics"]
             if not r["equipped"] and not r["is_favorite"]),
            None,
        )
        if sellable is None:
            pytest.skip("fixture has no sellable relic")

        plan = self._plan(
            client, prof["slot_index"],
            buckets=json.dumps([{**self._SCENIC, "quantity": 1}]),
            stop_mode="fixed",
            sold_handles=json.dumps([sellable["ga_handle"]]),
        )
        if plan["limited_by"]:
            pytest.skip(f"fixture-limited plan: {plan['limited_by']}")
        assert plan["kept"] == 1

        # Step 1 — sells land first (their credit + ghost slot fund the mint).
        sold = client.post(
            "/api/v1/saves/export",
            files={"file": ("NR0000.sl2", FIXTURE_PATH.read_bytes())},
            data={
                "slot_index": str(prof["slot_index"]),
                "ga_handles": json.dumps([sellable["ga_handle"]]),
            },
        )
        assert sold.status_code == 200, sold.text

        # Step 2 — mint the staged keepers with the batch's net delta.
        specs = [
            {"real_id": k["real_id"], "effects": k["effects"],
             "curses": k["curses"]}
            for k in plan["keepers"]
        ]
        exported = client.post(
            "/api/v1/saves/export-add-relics",
            files={"file": ("NR0000_edited.sl2", sold.content,
                            "application/octet-stream")},
            data={
                "slot_index": str(prof["slot_index"]),
                "specs": json.dumps(specs),
                "murk_delta": str(plan["murk_delta"]),
            },
        )
        assert exported.status_code == 200, exported.text
        assert exported.headers["x-murks-total"] == str(plan["murk_after"])

        reup = client.post(
            "/api/v1/saves/upload",
            files={"file": ("NR0000_edited2.sl2", exported.content,
                            "application/octet-stream")},
        )
        assert reup.status_code == 200, reup.text
        new_prof = next(
            p for p in reup.json()["profiles"]
            if p["slot_index"] == prof["slot_index"]
        )
        assert new_prof["murks"] == plan["murk_after"]
        assert new_prof["relic_count"] == prof["relic_count"]  # -1 sold, +1 minted

        # The committed save is a NEW state: planning starts from the landed
        # wallet with no staged fields — the diff never carries over.
        fresh = client.post(
            "/api/v1/saves/rites/plan",
            files={"file": ("fresh.sl2", exported.content,
                            "application/octet-stream")},
            data={
                "slot_index": str(prof["slot_index"]),
                "buckets": json.dumps([{**self._SCENIC, "quantity": 1}]),
                "stop_mode": "fixed",
            },
        )
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["murk_before"] == plan["murk_after"]


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
