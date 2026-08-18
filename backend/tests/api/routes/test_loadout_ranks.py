"""Tests for POST /api/v1/optimize/loadout-ranks.

The builds-page badge that answers "is what I actually have saved in-game still
the optimizer's pick?".  A build is reported only when one of its in-game
loadout presets reproduces one of its cached optimizer results; the rank is
that result's 1-based position in the list the optimize page renders.

Identity is content-based (vessel + relic multiset), so a preset holding the
same relics in swapped same-colour slots still counts -- these tests pin that,
plus the silences: no snapshot, wrong character, unresolvable relic.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import OptimizationSnapshot, Profile
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import OwnedRelic
from tests.utils.seeding import (
    create_build,
    get_test_user,
    seed_profile_with_relics,
)

EMPTY = EMPTY_EFFECT


def _optimize(client: TestClient, headers: dict, build_id: str, profile_id: str):
    resp = client.post(
        "/api/v1/optimize/",
        headers=headers,
        json={"build_id": build_id, "profile_id": profile_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ranks(client: TestClient, headers: dict, profile_id: str, loadouts):
    resp = client.post(
        "/api/v1/optimize/loadout-ranks",
        headers=headers,
        json={"profile_id": profile_id, "loadouts": loadouts},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _preset_from_result(result: dict, character: str, *, name: str, index: int = 0):
    """A loadout preset holding exactly the relics of an optimizer result."""
    return {
        "index": index,
        "character": character,
        "name": name,
        "vessel_id": result["vessel_id"],
        "ga_handles": [
            (a["relic"]["ga_handle"] if a["relic"] else 0)
            for a in result["assignments"]
        ],
    }


@pytest.mark.usefixtures("override_game_data")
class TestLoadoutRanks:
    def test_saved_top_result_reports_rank_1(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        profile_id = str(profile.id)
        build = create_build(client, normal_user_token_headers)
        results = _optimize(
            client, normal_user_token_headers, build["id"], profile_id
        )
        assert results, "seeded relics should produce at least one result"

        preset = _preset_from_result(results[0], build["character"], name="Best")
        rows = _ranks(client, normal_user_token_headers, profile_id, [preset])
        assert len(rows) == 1
        assert rows[0]["build_id"] == build["id"]
        assert rows[0]["rank"] == 1
        assert rows[0]["total"] == len(results)
        assert rows[0]["loadout_name"] == "Best"

    def test_lower_ranked_result_reports_its_position(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """The whole point of the badge: the optimizer has moved on, and the
        saved setup is now the Nth suggestion rather than the first."""
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        profile_id = str(profile.id)
        build = create_build(client, normal_user_token_headers)
        results = _optimize(
            client, normal_user_token_headers, build["id"], profile_id
        )
        if len(results) < 2:
            pytest.skip("needs at least two distinct results to rank")

        preset = _preset_from_result(results[1], build["character"], name="Stale")
        rows = _ranks(client, normal_user_token_headers, profile_id, [preset])
        assert len(rows) == 1
        assert rows[0]["rank"] == 2

    def test_slot_order_does_not_break_the_match(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """A preset holding the same relics in a different (same-colour)
        arrangement is the same setup in play, and must still match."""
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        profile_id = str(profile.id)
        build = create_build(client, normal_user_token_headers)
        results = _optimize(
            client, normal_user_token_headers, build["id"], profile_id
        )
        preset = _preset_from_result(
            results[0], build["character"], name="Rearranged"
        )
        preset["ga_handles"] = list(reversed(preset["ga_handles"]))

        rows = _ranks(client, normal_user_token_headers, profile_id, [preset])
        assert len(rows) == 1 and rows[0]["rank"] == 1

    def test_best_rank_wins_when_several_presets_match(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        profile_id = str(profile.id)
        build = create_build(client, normal_user_token_headers)
        results = _optimize(
            client, normal_user_token_headers, build["id"], profile_id
        )
        if len(results) < 2:
            pytest.skip("needs at least two distinct results to rank")

        presets = [
            _preset_from_result(results[1], build["character"], name="Old", index=0),
            _preset_from_result(results[0], build["character"], name="New", index=1),
        ]
        rows = _ranks(client, normal_user_token_headers, profile_id, presets)
        assert len(rows) == 1
        assert rows[0]["rank"] == 1 and rows[0]["loadout_name"] == "New"

    def test_other_characters_loadout_is_not_matched(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        profile_id = str(profile.id)
        build = create_build(client, normal_user_token_headers)
        results = _optimize(
            client, normal_user_token_headers, build["id"], profile_id
        )
        preset = _preset_from_result(results[0], "Duchess", name="Wrong hero")
        assert _ranks(
            client, normal_user_token_headers, profile_id, [preset]
        ) == []

    def test_preset_holding_an_unknown_relic_is_silent(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """A preset referencing a relic that is not in the effective inventory
        cannot be identified at all -- it must NOT match as if that slot were
        empty."""
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        profile_id = str(profile.id)
        build = create_build(client, normal_user_token_headers)
        results = _optimize(
            client, normal_user_token_headers, build["id"], profile_id
        )
        preset = _preset_from_result(results[0], build["character"], name="Broken")
        preset["ga_handles"] = [0xDEADBEEF] + preset["ga_handles"][1:]
        assert _ranks(
            client, normal_user_token_headers, profile_id, [preset]
        ) == []

    def test_snapshot_without_match_keys_is_silent(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Legacy snapshots (written before top_match_keys existed) look the
        same as no snapshot at all: silent until the next optimize refills
        them, never a wrong rank."""
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        profile_id = str(profile.id)
        build = create_build(client, normal_user_token_headers)
        results = _optimize(
            client, normal_user_token_headers, build["id"], profile_id
        )
        preset = _preset_from_result(results[0], build["character"], name="Best")

        snap = db.exec(
            select(OptimizationSnapshot).where(
                OptimizationSnapshot.build_id == uuid.UUID(build["id"])
            )
        ).first()
        assert snap is not None
        snap.top_match_keys = []
        db.add(snap)
        db.commit()

        assert _ranks(
            client, normal_user_token_headers, profile_id, [preset]
        ) == []

    def test_duplicate_copies_are_interchangeable(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Two identical relics are the same relic as far as a setup goes.

        The optimizer picks one physical copy; the preset saved in the game may
        hold the other.  Identity is relic CONTENT, never ga_handle, so the
        badge must still recognise the saved loadout -- it plays identically.
        """
        user = get_test_user(db)
        # Every relic identical in content; one twin PAIR per colour, so
        # whatever the optimizer picks, each used relic has an unused twin.
        owned = [
            OwnedRelic(
                ga_handle=0xC0030000 + i,
                item_id=100 + 2147483648,
                real_id=100,
                color=color,
                effects=[100, EMPTY, EMPTY],
                curses=[EMPTY, EMPTY, EMPTY],
                is_deep=False,
                name="Twin Relic",
                tier="Delicate",
            )
            for i, color in enumerate(
                ("Red", "Red", "Blue", "Blue", "Green", "Green")
            )
        ]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        profile_id = str(profile.id)
        build = create_build(client, normal_user_token_headers)
        results = _optimize(
            client, normal_user_token_headers, build["id"], profile_id
        )
        preset = _preset_from_result(results[0], build["character"], name="Twins")

        # Swap every placed relic for its twin: a different set of handles
        # holding exactly the same relics, in the same colours.
        twin = {}
        for a, b in zip(owned[0::2], owned[1::2]):
            twin[a.ga_handle] = b.ga_handle
            twin[b.ga_handle] = a.ga_handle
        swapped = [twin.get(h, h) for h in preset["ga_handles"]]
        assert swapped != preset["ga_handles"], "test needs a placed relic"
        preset["ga_handles"] = swapped

        rows = _ranks(client, normal_user_token_headers, profile_id, [preset])
        assert len(rows) == 1 and rows[0]["rank"] == 1

    def test_foreign_profile_is_rejected(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
    ) -> None:
        resp = client.post(
            "/api/v1/optimize/loadout-ranks",
            headers=normal_user_token_headers,
            json={"profile_id": str(uuid.uuid4()), "loadouts": []},
        )
        assert resp.status_code == 404

    def test_falls_back_to_the_saves_own_presets(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Omitting `loadouts` matches the presets as stored in the save."""
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        profile_id = str(profile.id)
        build = create_build(client, normal_user_token_headers)
        results = _optimize(
            client, normal_user_token_headers, build["id"], profile_id
        )
        preset = _preset_from_result(results[0], build["character"], name="In save")

        row = db.get(Profile, profile.id)
        assert row is not None
        row.loadouts = [{
            **preset,
            "hero_type": 1,
            "vessel_name": "V",
            "slot_colors": [],
            "cumulative_effects": [],
        }]
        db.add(row)
        db.commit()

        resp = client.post(
            "/api/v1/optimize/loadout-ranks",
            headers=normal_user_token_headers,
            json={"profile_id": profile_id},
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1 and rows[0]["rank"] == 1
        assert rows[0]["loadout_name"] == "In save"
