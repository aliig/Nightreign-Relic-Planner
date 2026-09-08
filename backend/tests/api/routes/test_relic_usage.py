"""Tests for POST /optimize/relic-usage — the inventory page's cull tiers.

This endpoint answers "which builds use this relic, and is it safe to sell?"
for a whole inventory in one request.  Two things it must never get wrong:

1. **Copy binding.**  Several physically distinct relics share one content
   fingerprint, and a stored layout cannot say which copy it placed.  Binding
   the wrong number of copies (or all of them, which is what keying usage by
   ``real_id`` did) makes the page report relics as used that are free to sell.
2. **Never over-report ``dead``.**  A stale build's relics stay reported and
   get flagged; a user with no builds is never told to sell everything.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from nrplanner.changes import relics_signature
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import OwnedRelic
from nrplanner.optimizer import OPTIMIZER_VERSION
from sqlmodel import Session, select

from app.core.config import settings
from app.core.game_data import game_data_version
from app.models import Build, OptimizationSnapshot, Profile, User
from tests.utils.seeding import (
    create_build,
    get_test_user,
    seed_profile_with_relics,
)

EMPTY = EMPTY_EFFECT

# The effect create_build's default weight group wants.
WANTED = 100
# An effect id the game data does not know: no name, no family, no text_id, so
# nothing can make a relic carrying only it relevant.
UNWANTED = 999999999


def _relic(handle: int, real_id: int, effects: list[int], *,
           color: str = "Red", is_deep: bool = False) -> OwnedRelic:
    effects = (effects + [EMPTY, EMPTY, EMPTY])[:3]
    return OwnedRelic(
        ga_handle=handle,
        item_id=real_id + 2147483648,
        real_id=real_id,
        color=color,
        effects=effects,
        curses=[EMPTY, EMPTY, EMPTY],
        is_deep=is_deep,
        name=f"Relic {real_id}",
        tier="Delicate",
    )


def _layout(relics: list[OwnedRelic]) -> dict:
    """The stored-snapshot shape the endpoint reads: assignments -> relic."""
    return {
        "assignments": [
            {
                "slot_index": i,
                "relic": {
                    "real_id": r.real_id,
                    "effects": list(r.effects),
                    "curses": list(r.curses),
                },
            }
            for i, r in enumerate(relics)
        ]
    }


def _write_snapshot(
    db: Session, build_id: str, profile: Profile, layouts: list[dict],
    owned: list[OwnedRelic], *, fresh: bool = True,
) -> OptimizationSnapshot:
    """Store hand-built layouts as this build's snapshot.

    Written directly rather than by running the optimizer: these tests are
    about how ranked layouts BIND to physical copies, which needs exact control
    over which relic appears at which rank.
    """
    build = db.get(Build, uuid.UUID(build_id))
    assert build is not None
    snap = OptimizationSnapshot(
        owner_id=build.owner_id,
        build_id=build.id,
        slot_index=profile.slot_index,
        relics_hash=relics_signature(owned),
        build_hash=build.build_hash if fresh else "stale" * 12,
        game_data_version=game_data_version(),
        optimizer_version=OPTIMIZER_VERSION,
        top_layouts=[],
        full_results=layouts,
    )
    db.add(snap)
    db.commit()
    return snap


def _legal_mint(handle: int, seed: int = 1000) -> dict:
    """A StagedMint payload the mint validator accepts.

    Staged mints are checked against the real rollable pool (1:1 fidelity), so
    a hand-written effect list is rejected — roll one from the generator.
    """
    from app.core.game_data import get_relic_generator

    rolled = get_relic_generator().roll(
        is_deep=False, version="1.03", mode="targeted",
        color="Red", tier=1, seed=seed,
    )
    return {
        "handle": handle,
        "real_id": rolled.real_id,
        "effects": list(rolled.effects),
        "curses": list(rolled.curses),
    }


def _owned_like(mint: dict, handle: int) -> OwnedRelic:
    """An owned relic with the same CONTENT as a staged mint."""
    return OwnedRelic(
        ga_handle=handle,
        item_id=mint["real_id"] + 2147483648,
        real_id=mint["real_id"],
        color="Red",
        effects=list(mint["effects"]),
        curses=list(mint["curses"]),
        is_deep=False,
        name="Minted Twin",
        tier="Delicate",
    )


def _make_build(client: TestClient, headers: dict[str, str], **fields) -> dict:
    """Create a build, then PUT the fields BuildCreate does not accept.

    BuildCreate only takes name/character/groups; required_effects and
    include_deep — both load-bearing for tiering — arrive through the update
    route, which also recomputes build_hash.
    """
    build = create_build(client, headers, **{
        k: v for k, v in fields.items() if k in ("name", "character", "groups")
    })
    rest = {k: v for k, v in fields.items()
            if k not in ("name", "character", "groups")}
    if not rest:
        return build
    resp = client.put(
        f"/api/v1/builds/{build['id']}", headers=headers, json=rest)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _usage(client: TestClient, headers: dict[str, str], profile: Profile,
           **body) -> dict:
    resp = client.post(
        "/api/v1/optimize/relic-usage",
        headers=headers,
        json={"profile_id": str(profile.id), **body},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_handle(body: dict) -> dict[int, dict]:
    return {r["ga_handle"]: r for r in body["relics"]}


@pytest.mark.usefixtures("override_game_data")
class TestCopyBinding:
    """A layout binds as many copies as it actually places — no more."""

    def test_one_placement_binds_exactly_one_of_three_copies(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """THE regression test for the real_id bug.

        Three relics share a content fingerprint.  The best layout places one.
        Exactly one may read as used — keying usage by relic TYPE marked all
        three, hiding two sellable relics from every cull filter.
        """
        user = get_test_user(db)
        owned = [_relic(0xC0000001 + i, 100, [WANTED]) for i in range(3)]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        _write_snapshot(db, build["id"], profile, [_layout([owned[0]])], owned)

        rows = _by_handle(_usage(client, normal_user_token_headers, profile))
        placed = [h for h, r in rows.items() if r["used_by"]]
        assert len(placed) == 1
        assert rows[placed[0]]["tier"] == "in_use"
        for h in rows:
            if h != placed[0]:
                assert rows[h]["tier"] not in ("in_use", "backup")

    def test_binds_per_rank_not_per_max(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Rank 1 needs one copy, rank 3 needs two: the second copy is a
        BACKUP at rank 3, not a second in_use.  Binding every copy at the
        fingerprint's best rank would claim the best loadout uses two."""
        user = get_test_user(db)
        owned = [_relic(0xC0000001 + i, 100, [WANTED]) for i in range(3)]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        _write_snapshot(db, build["id"], profile, [
            _layout([owned[0]]),
            _layout([owned[0]]),
            _layout([owned[0], owned[1]]),
        ], owned)

        rows = _by_handle(_usage(client, normal_user_token_headers, profile))
        tiers = sorted(r["tier"] for r in rows.values())
        assert tiers.count("in_use") == 1
        assert tiers.count("backup") == 1
        used = next(r for r in rows.values() if r["tier"] == "in_use")
        backup = next(r for r in rows.values() if r["tier"] == "backup")
        assert used["used_by"][0]["rank"] == 1
        assert backup["used_by"][0]["rank"] == 3

    def test_demand_above_copies_owned_binds_what_exists(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """A snapshot that predates a sale wants 3 and finds 2: bind both, 200,
        and let the build report itself out of date."""
        user = get_test_user(db)
        owned = [_relic(0xC0000001 + i, 100, [WANTED]) for i in range(2)]
        sold = _relic(0xC0000003, 100, [WANTED])
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        _write_snapshot(
            db, build["id"], profile,
            [_layout([owned[0], owned[1], sold])],
            owned + [sold],  # the snapshot's inventory still had all three
        )

        body = _usage(client, normal_user_token_headers, profile)
        rows = _by_handle(body)
        assert len(rows) == 2
        assert all(r["tier"] == "in_use" for r in rows.values())
        assert body["builds"][0]["fresh"] is False

    def test_owned_copy_binds_before_a_staged_mint(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Mint handles are negative; a plain numeric sort would bind a
        speculative, not-yet-exported purchase ahead of a content-identical
        relic the user actually owns, demoting the owned one."""
        user = get_test_user(db)
        mint = _legal_mint(-1)
        owned = [_owned_like(mint, 0xC0000001)]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        # The snapshot's inventory already contains both copies, so the layout
        # binds against a two-copy pool.
        _write_snapshot(db, build["id"], profile, [_layout([owned[0]])],
                        owned + [_owned_like(mint, -1)])

        body = _usage(client, normal_user_token_headers, profile,
                      staged_mints=[mint])
        rows = _by_handle(body)
        assert len(rows) == 2
        assert rows[0xC0000001]["tier"] == "in_use"
        assert rows[-1]["tier"] != "in_use"
        # Interchangeable copies are grouped so the UI can explain the split.
        assert rows[0xC0000001]["content_group"] == rows[-1]["content_group"]


@pytest.mark.usefixtures("override_game_data")
class TestTiers:
    def test_rank_one_is_in_use_and_lower_ranks_are_backup(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        user = get_test_user(db)
        best = _relic(0xC0000001, 100, [WANTED])
        alt = _relic(0xC0000002, 101, [WANTED])
        owned = [best, alt]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        _write_snapshot(db, build["id"], profile,
                        [_layout([best]), _layout([alt])], owned)

        rows = _by_handle(_usage(client, normal_user_token_headers, profile))
        assert rows[best.ga_handle]["tier"] == "in_use"
        assert rows[alt.ga_handle]["tier"] == "backup"
        assert rows[alt.ga_handle]["used_by"][0]["rank"] == 2

    def test_relic_carrying_a_required_effect_is_never_dead(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """positive_pre_score would score a required-but-inert effect 0 and
        call this relic dead.  Relevance has no inertness check, so it can't."""
        user = get_test_user(db)
        owned = [_relic(0xC0000001, 100, [WANTED])]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        _make_build(
            client, normal_user_token_headers,
            groups=[], required_effects=[WANTED],
        )

        rows = _by_handle(_usage(client, normal_user_token_headers, profile))
        assert rows[0xC0000001]["tier"] == "contender"

    def test_relic_no_build_wants_is_dead(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        user = get_test_user(db)
        junk = _relic(0xC0000002, 101, [UNWANTED])
        owned = [_relic(0xC0000001, 100, [WANTED]), junk]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        _write_snapshot(db, build["id"], profile, [_layout([owned[0]])], owned)

        rows = _by_handle(_usage(client, normal_user_token_headers, profile))
        assert rows[junk.ga_handle]["tier"] == "dead"
        assert rows[junk.ga_handle]["uncertain"] is False

    def test_stale_build_flags_only_what_it_could_want(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Uncertainty must be narrow.  A relic the stale build could want is
        flagged and never dead; a relic nobody wants stays dead and certain.
        If this cannot hold, uncertainty degrades to "everything, always"."""
        user = get_test_user(db)
        wanted = _relic(0xC0000001, 100, [WANTED])
        junk = _relic(0xC0000002, 101, [UNWANTED])
        owned = [wanted, junk]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        _write_snapshot(db, build["id"], profile, [], owned, fresh=False)

        body = _usage(client, normal_user_token_headers, profile)
        assert body["builds"][0]["fresh"] is False
        rows = _by_handle(body)
        assert rows[wanted.ga_handle]["uncertain"] is True
        assert rows[wanted.ga_handle]["tier"] == "contender"
        assert rows[junk.ga_handle]["uncertain"] is False
        assert rows[junk.ga_handle]["tier"] == "dead"

    def test_deep_relic_is_dead_when_no_build_takes_deep(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Placement eligibility strengthens dead for free: an include_deep=
        False build never looks past a vessel's standard slots."""
        user = get_test_user(db)
        deep = _relic(0xC0000002, 101, [WANTED], is_deep=True)
        owned = [_relic(0xC0000001, 100, [WANTED]), deep]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        _make_build(client, normal_user_token_headers, include_deep=False)

        rows = _by_handle(_usage(client, normal_user_token_headers, profile))
        assert rows[deep.ga_handle]["tier"] == "dead"
        assert rows[0xC0000001]["tier"] == "contender"

    def test_zero_builds_marks_nothing_dead(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """The sell-everything catastrophe: with nothing to judge against, the
        answer is "I don't know", not "all of it is disposable"."""
        user = get_test_user(db)
        owned = [_relic(0xC0000001, 100, [UNWANTED])]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)

        body = _usage(client, normal_user_token_headers, profile)
        assert body["builds"] == []
        assert all(r["tier"] == "contender" for r in body["relics"])
        assert all(r["uncertain"] is False for r in body["relics"])

    def test_dead_implies_certain(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Structural invariant: dead means no build could want it, and only a
        build that could want it can make it uncertain."""
        user = get_test_user(db)
        owned = [
            _relic(0xC0000001, 100, [WANTED]),
            _relic(0xC0000002, 101, [UNWANTED]),
            _relic(0xC0000003, 102, [WANTED], is_deep=True),
        ]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        _write_snapshot(db, build["id"], profile, [], owned, fresh=False)

        body = _usage(client, normal_user_token_headers, profile)
        for r in body["relics"]:
            if r["tier"] == "dead":
                assert r["uncertain"] is False, r


@pytest.mark.usefixtures("override_game_data")
class TestFreshnessAndCoverage:
    def test_stale_build_still_contributes_its_placements(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Dropping a stale build's placements is how keepers bought in Relic
        Rites started reading as unused.  Report them and flag them."""
        user = get_test_user(db)
        owned = [_relic(0xC0000001, 100, [WANTED])]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        _write_snapshot(db, build["id"], profile, [_layout([owned[0]])],
                        owned, fresh=False)

        body = _usage(client, normal_user_token_headers, profile)
        assert body["builds"][0]["fresh"] is False
        assert body["builds"][0]["optimized"] is True
        rows = _by_handle(body)
        assert rows[0xC0000001]["tier"] == "in_use"
        assert rows[0xC0000001]["uncertain"] is True

    def test_never_optimized_build_is_reported_separately(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """"Never run" is actionable and specific; "stale" is not the same
        thing, so the two flags stay separate."""
        user = get_test_user(db)
        owned = [_relic(0xC0000001, 100, [WANTED])]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)

        body = _usage(client, normal_user_token_headers, profile)
        info = next(b for b in body["builds"] if b["build_id"] == build["id"])
        assert info["fresh"] is False
        assert info["optimized"] is False
        assert info["name"] == build["name"]

    def test_freshness_matches_the_freshness_endpoint(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """One freshness rule, two readers.  If these ever disagree, the
        inventory page and the builds page tell the user different stories."""
        user = get_test_user(db)
        owned = [_relic(0xC0000001, 100, [WANTED]),
                 _relic(0xC0000002, 101, [WANTED])]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        fresh_build = create_build(client, normal_user_token_headers)
        stale_build = create_build(client, normal_user_token_headers, name="Stale")
        create_build(client, normal_user_token_headers, name="Never run")
        _write_snapshot(db, fresh_build["id"], profile,
                        [_layout([owned[0]])], owned)
        _write_snapshot(db, stale_build["id"], profile,
                        [_layout([owned[1]])], owned, fresh=False)

        usage = _usage(client, normal_user_token_headers, profile)
        resp = client.post(
            "/api/v1/optimize/freshness",
            headers=normal_user_token_headers,
            json={"profile_id": str(profile.id)},
        )
        assert resp.status_code == 200, resp.text
        expected = {b["build_id"]: b["fresh"] for b in resp.json()}
        assert {b["build_id"]: b["fresh"] for b in usage["builds"]} == expected

    def test_every_effective_relic_appears_exactly_once(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        user = get_test_user(db)
        owned = [_relic(0xC0000001 + i, 100 + i, [WANTED]) for i in range(4)]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)

        body = _usage(client, normal_user_token_headers, profile,
                      staged_mints=[_legal_mint(-1)])
        handles = [r["ga_handle"] for r in body["relics"]]
        assert len(handles) == len(set(handles)) == 5
        assert {h for h in handles if h > 0} == {r.ga_handle for r in owned}
        assert sum(1 for h in handles if h < 0) == 1

    def test_staged_sells_are_not_part_of_the_request(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Pins the cache-key contract: sells change on every trash click, and
        including them is exactly what made the usage map blank itself."""
        user = get_test_user(db)
        owned = [_relic(0xC0000001, 100, [WANTED]),
                 _relic(0xC0000002, 101, [WANTED])]
        profile = seed_profile_with_relics(db, user.id, with_hash=True, owned=owned)
        build = create_build(client, normal_user_token_headers)
        _write_snapshot(db, build["id"], profile, [_layout([owned[0]])], owned)

        plain = _usage(client, normal_user_token_headers, profile)
        with_sells = _usage(client, normal_user_token_headers, profile,
                            staged_sells=[0xC0000001])
        assert with_sells == plain

    def test_requires_authentication(self, client: TestClient) -> None:
        resp = client.post("/api/v1/optimize/relic-usage",
                           json={"profile_id": str(uuid.uuid4())})
        assert resp.status_code == 401

    def test_unknown_profile_is_404(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
    ) -> None:
        resp = client.post(
            "/api/v1/optimize/relic-usage",
            headers=normal_user_token_headers,
            json={"profile_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404

    def test_another_users_profile_is_404(
        self, client: TestClient, normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        other_user = db.exec(
            select(User).where(User.email != settings.EMAIL_TEST_USER)
        ).first()
        assert other_user is not None
        foreign = seed_profile_with_relics(db, other_user.id, with_hash=True)
        resp = client.post(
            "/api/v1/optimize/relic-usage",
            headers=normal_user_token_headers,
            json={"profile_id": str(foreign.id)},
        )
        assert resp.status_code == 404
