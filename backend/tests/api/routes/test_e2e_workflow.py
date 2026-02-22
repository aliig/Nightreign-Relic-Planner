"""End-to-end integration test: upload save -> create build -> optimize.

Validates the full authenticated user workflow through all API layers using
a real .sl2 save fixture file. Also tests inline (anonymous) optimization
using relics extracted from the upload response.

Requires:
    backend/tests/fixtures/NR0000.sl2 (gitignored, copy from your save location)

Run with:
    cd backend && uv run pytest tests/api/routes/test_e2e_workflow.py -v -m integration
"""
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

FIXTURE_PATH = Path(__file__).parent.parent.parent / "fixtures" / "NR0000.sl2"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not FIXTURE_PATH.exists(),
        reason="Real save fixture not present — copy NR0000.sl2 to backend/tests/fixtures/",
    ),
]


def _upload_relics_to_inline_format(
    parsed_relics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Transform ParsedRelicData dicts (upload response) to OwnedRelic dicts (optimizer input).

    Upload response has flat fields: effect_1, effect_2, effect_3, curse_1, curse_2, curse_3
    Optimizer inline mode expects list fields: effects=[int,int,int], curses=[int,int,int]
    """
    result = []
    for r in parsed_relics:
        result.append({
            "ga_handle": r["ga_handle"],
            "item_id": r["item_id"],
            "real_id": r["real_id"],
            "color": r["color"],
            "effects": [r["effect_1"], r["effect_2"], r["effect_3"]],
            "curses": [r["curse_1"], r["curse_2"], r["curse_3"]],
            "is_deep": r["is_deep"],
            "name": r["name"],
            "tier": r["tier"],
        })
    return result


@pytest.mark.usefixtures("override_game_data")
class TestE2EWorkflow:
    """Full workflow: upload -> game data -> create build -> update build -> optimize (DB + inline)."""

    # Mutable class-level state shared across ordered test methods.
    _state: dict[str, Any] = {}

    @pytest.fixture(autouse=True, scope="class")
    def cleanup(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> Any:
        """Delete created build after all tests in this class finish."""
        yield
        build_id = self.__class__._state.get("build_id")
        if build_id:
            client.delete(
                f"/api/v1/builds/{build_id}",
                headers=superuser_token_headers,
            )

    # ---------------------------------------------------------------
    # Step 1: Upload save (authenticated)
    # ---------------------------------------------------------------

    def test_01_upload_save(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """Upload the real .sl2 as authenticated user; verify persistence."""
        with FIXTURE_PATH.open("rb") as f:
            response = client.post(
                "/api/v1/saves/upload",
                files={"file": ("NR0000.sl2", f, "application/octet-stream")},
                headers=superuser_token_headers,
            )

        assert response.status_code == 200, response.text
        data = response.json()

        assert data["platform"] == "PC"
        assert data["persisted"] is True
        assert data["save_upload_id"] is not None
        assert data["character_count"] >= 1
        assert len(data["characters"]) >= 1

        # Pick the first character with relics
        char = data["characters"][0]
        assert char["id"] is not None, "Authenticated upload must assign character IDs"
        assert isinstance(char["relics"], list)
        assert char["relic_count"] == len(char["relics"])

        # Validate relic structure
        valid_colors = {"Red", "Blue", "Yellow", "Green", "White"}
        for relic in char["relics"]:
            assert relic["color"] in valid_colors
            assert isinstance(relic["effect_1"], int)
            assert isinstance(relic["effect_2"], int)
            assert isinstance(relic["effect_3"], int)
            assert isinstance(relic["curse_1"], int)
            assert isinstance(relic["curse_2"], int)
            assert isinstance(relic["curse_3"], int)
            assert isinstance(relic["is_deep"], bool)
            assert relic["tier"] in ("Grand", "Polished", "Delicate")
            assert isinstance(relic["name"], str) and relic["name"] != ""

        # Stash for downstream tests
        state = self.__class__._state
        state["character_id"] = char["id"]
        state["character_name"] = char["name"]
        state["upload_relics"] = char["relics"]
        state["upload_data"] = data

    # ---------------------------------------------------------------
    # Step 2: Fetch game data (effects for build tier config)
    # ---------------------------------------------------------------

    def test_02_fetch_game_data(self, client: TestClient) -> None:
        """GET /game/effects and /game/characters; pick real IDs for the build."""
        state = self.__class__._state

        # --- Effects ---
        response = client.get("/api/v1/game/effects")
        assert response.status_code == 200
        effects = response.json()
        assert isinstance(effects, list)
        assert len(effects) > 0

        for eff in effects[:5]:
            assert "id" in eff
            assert "name" in eff

        upload_relics = state.get("upload_relics", [])

        # Collect non-empty effect IDs from uploaded relics
        empty_val = 4294967295  # EMPTY_EFFECT sentinel
        relic_effect_ids: set[int] = set()
        for r in upload_relics:
            for key in ("effect_1", "effect_2", "effect_3"):
                eid = r[key]
                if eid not in (empty_val, 0):
                    relic_effect_ids.add(eid)

        # Cross-reference with valid game effects
        all_effect_ids = {e["id"] for e in effects}
        preferred_ids = list(relic_effect_ids & all_effect_ids)[:3]

        # Fallback if no overlap (unlikely with real data)
        if len(preferred_ids) < 2:
            preferred_ids = [e["id"] for e in effects[:3]]

        state["preferred_effect_ids"] = preferred_ids

        # --- Characters (class names) ---
        # The upload returns the player's custom profile name (e.g. "Ketaman"),
        # NOT the class name. Builds require a valid class name for optimization.
        response = client.get("/api/v1/game/characters")
        assert response.status_code == 200
        characters = response.json()
        assert len(characters) > 0

        # Use the first valid class name (not "All")
        class_name = next(
            c["name"] for c in characters if c["name"] != "All"
        )
        state["class_name"] = class_name

    # ---------------------------------------------------------------
    # Step 3: Create build (authenticated)
    # ---------------------------------------------------------------

    def test_03_create_build(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """POST /builds/ with a valid class name from game data."""
        state = self.__class__._state
        class_name = state["class_name"]

        response = client.post(
            "/api/v1/builds/",
            json={"name": "E2E Test Build", "character": class_name},
            headers=superuser_token_headers,
        )

        assert response.status_code == 200, response.text
        build = response.json()

        assert build["name"] == "E2E Test Build"
        assert build["character"] == class_name
        assert "id" in build
        expected_tier_keys = {"required", "preferred", "nice_to_have", "avoid", "blacklist"}
        assert set(build["tiers"].keys()) == expected_tier_keys
        for tier_list in build["tiers"].values():
            assert tier_list == [], "All tiers should be empty on creation"
        assert set(build["family_tiers"].keys()) == expected_tier_keys
        assert build["include_deep"] is True
        assert build["curse_max"] == 1

        state["build_id"] = build["id"]
        state["build_data"] = build

    # ---------------------------------------------------------------
    # Step 4: Update build with tier configuration
    # ---------------------------------------------------------------

    def test_04_update_build_tiers(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """PUT /builds/{id} to set preferred effects from real game data."""
        state = self.__class__._state
        build_id = state["build_id"]
        preferred_ids = state.get("preferred_effect_ids", [])

        new_tiers = {
            "required": [],
            "preferred": preferred_ids,
            "nice_to_have": [],
            "avoid": [],
            "blacklist": [],
        }

        response = client.put(
            f"/api/v1/builds/{build_id}",
            json={"tiers": new_tiers},
            headers=superuser_token_headers,
        )

        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["tiers"]["preferred"] == preferred_ids
        assert updated["name"] == "E2E Test Build"  # unchanged

        state["build_data"] = updated

    # ---------------------------------------------------------------
    # Step 5a: Optimize — DB mode (authenticated)
    # ---------------------------------------------------------------

    def test_05a_optimize_db_mode(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """POST /optimize/ with build_id + character_id (DB mode)."""
        state = self.__class__._state

        response = client.post(
            "/api/v1/optimize/",
            json={
                "build_id": state["build_id"],
                "character_id": state["character_id"],
                "top_n": 5,
                "max_per_vessel": 2,
            },
            headers=superuser_token_headers,
        )

        assert response.status_code == 200, response.text
        results = response.json()

        assert isinstance(results, list)
        assert len(results) > 0, "Optimizer should return at least one vessel result"
        assert len(results) <= 5, "Should respect top_n=5"

        for result in results:
            assert isinstance(result["vessel_name"], str) and result["vessel_name"] != ""
            assert isinstance(result["total_score"], int)
            assert isinstance(result["assignments"], list)
            assert isinstance(result["meets_requirements"], bool)
            assert "slot_colors" in result
            assert "vessel_character" in result

            for assignment in result["assignments"]:
                assert "slot_index" in assignment
                assert "slot_color" in assignment
                assert "is_deep" in assignment
                assert "score" in assignment
                assert isinstance(assignment["breakdown"], list)
                if assignment["relic"] is not None:
                    relic = assignment["relic"]
                    assert relic["color"] in {"Red", "Blue", "Yellow", "Green", "White"}
                    assert isinstance(relic["name"], str)

        state["db_mode_results"] = results

    # ---------------------------------------------------------------
    # Step 5b: Optimize — Inline mode (anonymous)
    # ---------------------------------------------------------------

    def test_05b_optimize_inline_mode(self, client: TestClient) -> None:
        """POST /optimize/ with full build + relics inline (no auth)."""
        state = self.__class__._state
        build_data = state["build_data"]

        # Transform upload relics (flat fields) to optimizer format (list fields)
        inline_relics = _upload_relics_to_inline_format(state["upload_relics"])

        inline_build = {
            "id": str(build_data["id"]),
            "name": build_data["name"],
            "character": build_data["character"],
            "tiers": build_data["tiers"],
            "family_tiers": build_data["family_tiers"],
            "include_deep": build_data["include_deep"],
            "curse_max": build_data["curse_max"],
        }

        response = client.post(
            "/api/v1/optimize/",
            json={
                "build": inline_build,
                "relics": inline_relics,
                "top_n": 5,
                "max_per_vessel": 2,
            },
            # No auth headers — anonymous inline mode
        )

        assert response.status_code == 200, response.text
        results = response.json()

        assert isinstance(results, list)
        assert len(results) > 0, "Inline optimizer should return at least one result"

        for result in results:
            assert "vessel_name" in result
            assert "total_score" in result
            assert "assignments" in result
            assert "meets_requirements" in result

        state["inline_mode_results"] = results

    # ---------------------------------------------------------------
    # Step 6: Cross-validate DB mode vs inline mode results
    # ---------------------------------------------------------------

    def test_06_results_consistent(self) -> None:
        """Both optimization paths should produce identical results given the same inputs."""
        state = self.__class__._state
        db_results = state.get("db_mode_results")
        inline_results = state.get("inline_mode_results")

        assert db_results is not None, "DB mode results missing — test_05a likely failed"
        assert inline_results is not None, "Inline mode results missing — test_05b likely failed"
        assert len(db_results) > 0, "DB mode returned no results"
        assert len(inline_results) > 0, "Inline mode returned no results"

        assert len(db_results) == len(inline_results), (
            f"DB mode returned {len(db_results)} results, "
            f"inline mode returned {len(inline_results)}"
        )

        db_vessels = {r["vessel_name"] for r in db_results}
        inline_vessels = {r["vessel_name"] for r in inline_results}
        assert db_vessels == inline_vessels, (
            f"Vessel sets differ. DB-only: {db_vessels - inline_vessels}, "
            f"Inline-only: {inline_vessels - db_vessels}"
        )

        # Scores should be identical since both use the same relics + build
        for db_r, inline_r in zip(
            sorted(db_results, key=lambda r: r["vessel_name"]),
            sorted(inline_results, key=lambda r: r["vessel_name"]),
        ):
            assert db_r["vessel_name"] == inline_r["vessel_name"]
            assert db_r["total_score"] == inline_r["total_score"], (
                f"Score mismatch for vessel '{db_r['vessel_name']}': "
                f"DB={db_r['total_score']}, Inline={inline_r['total_score']}"
            )

    # ---------------------------------------------------------------
    # Step 7: Verify persisted relics via GET endpoint
    # ---------------------------------------------------------------

    def test_07_get_relics_matches_upload(
        self, client: TestClient, superuser_token_headers: dict[str, str]
    ) -> None:
        """GET /saves/characters/{id}/relics returns the same relics as upload."""
        state = self.__class__._state
        character_id = state["character_id"]
        upload_relics = state["upload_relics"]

        response = client.get(
            f"/api/v1/saves/characters/{character_id}/relics",
            headers=superuser_token_headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["count"] == len(upload_relics)

        # Verify every uploaded relic has a DB counterpart (match by ga_handle)
        db_handles = {r["ga_handle"] for r in body["data"]}
        upload_handles = {r["ga_handle"] for r in upload_relics}
        assert db_handles == upload_handles, (
            f"Relic handle mismatch. Missing from DB: {upload_handles - db_handles}, "
            f"Extra in DB: {db_handles - upload_handles}"
        )
