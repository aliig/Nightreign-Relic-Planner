"""Tests for POST /api/v1/optimize/ endpoint.

Uses inline mode (no DB) for unit tests so no prior upload is required.
"""
import pytest
from fastapi.testclient import TestClient

from nrplanner.constants import EMPTY_EFFECT

EMPTY = EMPTY_EFFECT

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

_MINIMAL_RELIC = {
    "ga_handle": 0xC0000001,
    "item_id": 100 + 2147483648,
    "real_id": 100,
    "color": "Red",
    "effects": [100, EMPTY, EMPTY],
    "curses": [EMPTY, EMPTY, EMPTY],
    "is_deep": False,
    "name": "Test Relic",
    "tier": "Delicate",
}

_MINIMAL_RELIC_BLUE = {
    **_MINIMAL_RELIC,
    "ga_handle": 0xC0000002,
    "color": "Blue",
}

_MINIMAL_RELIC_GREEN = {
    **_MINIMAL_RELIC,
    "ga_handle": 0xC0000003,
    "color": "Green",
}


@pytest.mark.usefixtures("override_game_data")
class TestInlineMode:
    def test_inline_optimize_empty_relics_ok(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": _MINIMAL_BUILD,
                "relics": [],
                "character_name": "Wylder",
                "top_n": 3,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_inline_optimize_with_relics_ok(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": _MINIMAL_BUILD,
                "relics": [_MINIMAL_RELIC],
                "character_name": "Wylder",
                "top_n": 3,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Each item should be a VesselResult shape
        for result in data:
            assert "vessel_id" in result
            assert "vessel_name" in result
            assert "total_score" in result
            assert "assignments" in result

    def test_cumulative_effects_in_response(self, client: TestClient) -> None:
        """A placed clean-numeric effect surfaces a cumulative_effects group."""
        # 7001500 == "Magic Attack Power Up" (+0). Require it so it gets placed.
        magic = 7001500
        build = {**_MINIMAL_BUILD, "required_effects": [magic]}
        relics = [
            {**_MINIMAL_RELIC, "ga_handle": 0xC0000010 + i, "color": color,
             "effects": [magic, EMPTY, EMPTY]}
            for i, color in enumerate(("Red", "Blue", "Green"))
        ]
        response = client.post(
            "/api/v1/optimize/",
            json={"build": build, "relics": relics, "top_n": 10},
        )
        assert response.status_code == 200
        results = response.json()
        # Field is always present (serialized) on every result.
        assert all("cumulative_effects" in r for r in results)
        # Wherever the relic was placed, the summary names the family.
        placed = [
            r for r in results
            if any(a["relic"] and magic in a["relic"]["effects"] for a in r["assignments"])
        ]
        assert placed, "magic-attack relic should be placed in at least one vessel"
        groups = placed[0]["cumulative_effects"]
        mag_group = next(g for g in groups if g["family"] == "Magic Attack Power Up")
        assert mag_group["mode"] == "multiplicative"
        assert mag_group["cumulative_value"] >= 1.045
        assert mag_group["bonus_display"]  # non-empty preformatted string

    def test_missing_build_returns_422(self, client: TestClient) -> None:
        """Inline mode requires both build and relics."""
        response = client.post(
            "/api/v1/optimize/",
            json={
                # build omitted — only relics provided
                "relics": [],
            },
        )
        assert response.status_code == 422

    def test_unknown_character_returns_422(self, client: TestClient) -> None:
        """An unknown character name in build.character should return 422."""
        bad_build = {**_MINIMAL_BUILD, "character": "NotARealCharacter"}
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": bad_build,
                "relics": [],
            },
        )
        assert response.status_code == 422
        assert "Unknown character" in response.json()["detail"]

    def test_inline_with_pinned_relics_ok(self, client: TestClient) -> None:
        """Inline mode accepts pinned_relics without error."""
        build_with_pins = {**_MINIMAL_BUILD, "pinned_relics": [_MINIMAL_RELIC["ga_handle"]]}
        response = client.post(
            "/api/v1/optimize/",
            json={"build": build_with_pins, "relics": [_MINIMAL_RELIC], "top_n": 3},
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_both_db_and_inline_mode_returns_422(self, client: TestClient) -> None:
        import uuid

        response = client.post(
            "/api/v1/optimize/",
            json={
                "build_id": str(uuid.uuid4()),
                "build": _MINIMAL_BUILD,
                "relics": [],
                "character_name": "Wylder",
            },
        )
        assert response.status_code == 422


    def test_results_include_character_specific_vessels(
        self, client: TestClient,
    ) -> None:
        """Optimization for Wylder must return Wylder-specific vessels,
        not only the shared 'All' vessels.  This catches the NPC-ID-vs-
        hero-index mapping bug in _resolve_hero_type."""
        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": _MINIMAL_BUILD,
                "relics": [_MINIMAL_RELIC, _MINIMAL_RELIC_BLUE, _MINIMAL_RELIC_GREEN],
                "top_n": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        characters = {r["vessel_character"] for r in data}
        assert "Wylder" in characters, (
            f"Expected Wylder-specific vessels but got: {characters}"
        )


@pytest.mark.usefixtures("override_game_data")
class TestDbMode:
    def test_db_mode_requires_auth(self, client: TestClient) -> None:
        import uuid

        response = client.post(
            "/api/v1/optimize/",
            json={
                "build_id": str(uuid.uuid4()),
                "profile_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 401


def _targeted_mints(colors: tuple[str, ...] = ("Red", "Blue", "Green")) -> list[dict]:
    """Legal StagedMint payloads in known colors, rolled from real game data.

    Targeted mode fixes color+tier so placement mirrors the 3-color inline
    tests; the deterministic seed keeps the payload stable across runs.  The
    generator only emits relics that pass RelicChecker, so these always
    survive staged-mint validation.
    """
    from app.core.game_data import get_relic_generator

    gen = get_relic_generator()
    mints = []
    for i, color in enumerate(colors):
        rolled = gen.roll(
            is_deep=False, version="1.03", mode="targeted",
            color=color, tier=1, seed=1000 + i,
        )
        mints.append({
            "handle": -(i + 1),
            "real_id": rolled.real_id,
            "effects": list(rolled.effects),
            "curses": list(rolled.curses),
        })
    return mints


@pytest.mark.usefixtures("override_game_data")
class TestStagedDbMode:
    """staged_sells / staged_mints shape the EFFECTIVE inventory of a DB run."""

    def _seed(self, client, headers, db, **build_overrides):
        from tests.utils.seeding import (
            create_build,
            get_test_user,
            seed_profile_with_relics,
        )

        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, headers, **build_overrides)
        return build, profile

    @staticmethod
    def _placed_handles(results: list[dict]) -> set[int]:
        return {
            a["relic"]["ga_handle"]
            for r in results
            for a in r["assignments"]
            if a["relic"]
        }

    def test_staged_sell_removes_relic_from_results(
        self, client: TestClient, normal_user_token_headers, db
    ) -> None:
        build, profile = self._seed(client, normal_user_token_headers, db)
        sold = 0xC0020000  # first seeded relic; carries the build's effect 100

        baseline = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={"build_id": build["id"], "profile_id": str(profile.id), "top_n": 5},
        )
        assert baseline.status_code == 200, baseline.text
        assert sold in self._placed_handles(baseline.json()), (
            "seeded relic should be placed in the pure baseline"
        )

        staged = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "top_n": 5,
                "staged_sells": [sold],
            },
        )
        assert staged.status_code == 200, staged.text
        assert sold not in self._placed_handles(staged.json()), (
            "a staged-sold relic must never appear in optimization results"
        )

    def test_staged_mints_join_results_under_synthetic_handles(
        self, client: TestClient, normal_user_token_headers, db
    ) -> None:
        mints = _targeted_mints()
        wanted = [
            e for m in mints for e in m["effects"] if e not in (EMPTY, 0)
        ]
        build, profile = self._seed(
            client, normal_user_token_headers, db,
            groups=[{"weight": 10, "effects": wanted, "families": []}],
        )

        resp = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "top_n": 10,
                "staged_mints": mints,
            },
        )
        assert resp.status_code == 200, resp.text
        placed = self._placed_handles(resp.json())
        assert any(h < 0 for h in placed), (
            "staged mints carrying wanted effects should be placed, keyed by "
            "their negative synthetic handles"
        )
        # Server-derived display fields (never trusted from the client).
        minted = next(
            a["relic"]
            for r in resp.json()
            for a in r["assignments"]
            if a["relic"] and a["relic"]["ga_handle"] < 0
        )
        assert minted["name"], "mint display fields must be derived server-side"
        assert minted["color"] in ("Red", "Blue", "Yellow", "Green", "White")

    def test_illegal_mint_rejected(
        self, client: TestClient, normal_user_token_headers, db
    ) -> None:
        build, profile = self._seed(client, normal_user_token_headers, db)
        resp = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "staged_mints": [{
                    "handle": -1, "real_id": 999_999,
                    "effects": [100, EMPTY, EMPTY],
                    "curses": [EMPTY, EMPTY, EMPTY],
                }],
            },
        )
        assert resp.status_code == 422
        assert "not a mintable relic" in resp.json()["detail"]

    def test_nonnegative_mint_handle_rejected(
        self, client: TestClient, normal_user_token_headers, db
    ) -> None:
        build, profile = self._seed(client, normal_user_token_headers, db)
        mint = {**_targeted_mints(("Red",))[0], "handle": 5}
        resp = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "staged_mints": [mint],
            },
        )
        assert resp.status_code == 422
        assert "negative synthetic" in resp.json()["detail"]

    def test_duplicate_mint_handles_rejected(
        self, client: TestClient, normal_user_token_headers, db
    ) -> None:
        build, profile = self._seed(client, normal_user_token_headers, db)
        mint = _targeted_mints(("Red",))[0]
        resp = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "staged_mints": [mint, dict(mint)],
            },
        )
        assert resp.status_code == 422
        assert "duplicate" in resp.json()["detail"].lower()

    def test_staged_with_inline_mode_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/optimize/",
            json={
                "build": _MINIMAL_BUILD,
                "relics": [_MINIMAL_RELIC],
                "staged_sells": [_MINIMAL_RELIC["ga_handle"]],
            },
        )
        assert resp.status_code == 422
        assert "DB mode" in resp.json()["detail"]

    def test_slot_alternative_honors_staged_sells(
        self, client: TestClient, normal_user_token_headers, db
    ) -> None:
        """The strike path resolves the same effective inventory: with every
        owned relic staged-sold there is no replacement candidate left."""
        build, profile = self._seed(client, normal_user_token_headers, db)

        baseline = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={"build_id": build["id"], "profile_id": str(profile.id), "top_n": 5},
        )
        assert baseline.status_code == 200, baseline.text
        top = baseline.json()[0]
        struck_idx = next(
            i for i, a in enumerate(top["assignments"]) if a["relic"]
        )

        resp = client.post(
            "/api/v1/optimize/slot-alternative",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "vessel_id": top["vessel_id"],
                "struck_slot_index": struck_idx,
                "locked_slots": [],
                "excluded_ga_handles": [],
                "staged_sells": [0xC0020000, 0xC0020001, 0xC0020002],
            },
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        if result is not None:
            assert result["assignments"][struck_idx]["relic"] is None, (
                "with the whole inventory staged-sold, the struck slot must "
                "have no replacement"
            )


_SLOT_BUILD = {
    "id": "slot-alt-test",
    "name": "Slot Alt Build",
    "character": "Wylder",
    "groups": [],
    "required_effects": [100],
    "required_families": [],
    "excluded_effects": [],
    "excluded_families": [],
    "include_deep": False,
    "curse_max": 1,
}


def _color_relics() -> list[dict]:
    """4 relics per colour, all carrying required effect 100.

    Four-per-colour guarantees that after pinning the other (≤2) standard slots
    and excluding one relic, a same-colour alternative still remains for the
    struck slot — so the strike always produces a swap regardless of the chosen
    vessel's colour layout."""
    relics: list[dict] = []
    h = 0xC0010000
    for color in ("Red", "Blue", "Yellow", "Green", "White"):
        for _ in range(4):
            relics.append({
                "ga_handle": h,
                "item_id": 100 + 2147483648,
                "real_id": 100,
                "color": color,
                "effects": [100, EMPTY, EMPTY],
                "curses": [EMPTY, EMPTY, EMPTY],
                "is_deep": False,
                "name": "Test Relic",
                "tier": "Delicate",
            })
            h += 1
    return relics


@pytest.mark.usefixtures("override_game_data")
class TestSlotAlternative:
    """POST /optimize/slot-alternative — re-optimize one slot, others pinned."""

    def _top_result(self, client: TestClient) -> dict:
        resp = client.post(
            "/api/v1/optimize/",
            json={"build": _SLOT_BUILD, "relics": _color_relics(), "top_n": 50},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data, "expected at least one vessel result to strike against"
        return data[0]

    def test_strike_swaps_struck_slot_and_keeps_others(self, client: TestClient) -> None:
        top = self._top_result(client)
        vessel_id = top["vessel_id"]
        assignments = top["assignments"]
        struck_idx = next(i for i, a in enumerate(assignments) if a["relic"])
        struck_handle = assignments[struck_idx]["relic"]["ga_handle"]
        locked = [
            {"slot_index": a["slot_index"], "ga_handle": a["relic"]["ga_handle"]}
            for i, a in enumerate(assignments)
            if i != struck_idx and a["relic"]
        ]

        resp = client.post(
            "/api/v1/optimize/slot-alternative",
            json={
                "build": _SLOT_BUILD,
                "relics": _color_relics(),
                "vessel_id": vessel_id,
                "struck_slot_index": struck_idx,
                "locked_slots": locked,
                "excluded_ga_handles": [struck_handle],
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result is not None, "a same-colour alternative should exist"
        assert result["vessel_id"] == vessel_id

        # The struck slot changed to a different relic.
        new_struck = result["assignments"][struck_idx]["relic"]
        assert new_struck is not None, "struck slot should get a replacement"
        assert new_struck["ga_handle"] != struck_handle

        # Every locked slot kept its EXACT relic in its EXACT position (the bug
        # fix: no positionless repacking).
        new_by_slot = {a["slot_index"]: a["relic"] for a in result["assignments"]}
        for ls in locked:
            kept = new_by_slot[ls["slot_index"]]
            assert kept is not None and kept["ga_handle"] == ls["ga_handle"], (
                f"slot {ls['slot_index']} must keep relic {ls['ga_handle']}, "
                f"got {kept}"
            )

        # The struck relic appears nowhere in the result.
        all_handles = {
            a["relic"]["ga_handle"] for a in result["assignments"] if a["relic"]
        }
        assert struck_handle not in all_handles, "struck relic must be excluded"

    def test_unknown_vessel_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/optimize/slot-alternative",
            json={
                "build": _SLOT_BUILD,
                "relics": [],
                "vessel_id": 999_999_999,
                "struck_slot_index": 0,
                "locked_slots": [],
                "excluded_ga_handles": [],
            },
        )
        assert resp.status_code == 404

    def test_db_mode_requires_auth(self, client: TestClient) -> None:
        import uuid

        resp = client.post(
            "/api/v1/optimize/slot-alternative",
            json={
                "build_id": str(uuid.uuid4()),
                "profile_id": str(uuid.uuid4()),
                "vessel_id": 1,
                "struck_slot_index": 0,
            },
        )
        assert resp.status_code == 401
