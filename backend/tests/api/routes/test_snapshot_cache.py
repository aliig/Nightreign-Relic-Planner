"""Tests for the optimization snapshot cache plumbing.

The GET /optimize/snapshot freshness contract: a snapshot is served when its
build_hash and optimizer/game-data versions match the live inputs AND the
inventory is unchanged — either byte-identical (relics_hash) or unchanged in
the build-RELEVANT subset (relevant_relics_hash), so irrelevant inventory
churn keeps serving the cache.  These tests pin the plumbing that keeps the
contract honest: build_hash on clone, hash parity between the seeding and
snapshot paths, invalidation on build edit, missing-hash staleness, and the
relevant-subset gate.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    Build,
    OptimizationSnapshot,
    Profile,
    Relic,
    SaveUpload,
    User,
)
from nrplanner.changes import relics_signature
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import OwnedRelic

EMPTY = EMPTY_EFFECT


def _test_user(db: Session) -> User:
    user = db.exec(
        select(User).where(User.email == settings.EMAIL_TEST_USER)
    ).first()
    assert user is not None
    return user


def _seed_profile_with_relics(
    db: Session, owner_id: uuid.UUID, *, with_hash: bool
) -> Profile:
    """SaveUpload + Profile + 3 relic rows for the given owner.

    With ``with_hash=True`` the profile gets the same relics_hash the upload
    endpoints compute, so a subsequent DB-mode optimize produces a snapshot
    that must compare fresh against it.
    """
    save = SaveUpload(owner_id=owner_id, platform="PC", profile_count=1)
    db.add(save)
    db.flush()

    owned = [
        OwnedRelic(
            ga_handle=0xC0020000 + i,
            item_id=100 + 2147483648,
            real_id=100,
            color=color,
            effects=[100, EMPTY, EMPTY],
            curses=[EMPTY, EMPTY, EMPTY],
            is_deep=False,
            name="Seeded Relic",
            tier="Delicate",
        )
        for i, color in enumerate(("Red", "Blue", "Green"))
    ]
    profile = Profile(
        owner_id=owner_id,
        save_upload_id=save.id,
        slot_index=0,
        name="Seeded Hero",
        relics_hash=relics_signature(owned) if with_hash else None,
    )
    db.add(profile)
    db.flush()
    for r in owned:
        db.add(Relic(
            owner_id=owner_id,
            profile_id=profile.id,
            ga_handle=r.ga_handle,
            item_id=r.item_id,
            real_id=r.real_id,
            color=r.color,
            effect_1=r.effects[0],
            effect_2=r.effects[1],
            effect_3=r.effects[2],
            curse_1=r.curses[0],
            curse_2=r.curses[1],
            curse_3=r.curses[2],
            is_deep=r.is_deep,
            name=r.name,
            tier=r.tier,
        ))
    db.commit()
    return profile


def _create_build(client: TestClient, headers: dict[str, str]) -> dict:
    resp = client.post(
        "/api/v1/builds/",
        headers=headers,
        json={
            "name": "Snapshot Cache Build",
            "character": "Wylder",
            "groups": [{"weight": 10, "effects": [100], "families": []}],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.usefixtures("override_game_data")
class TestSnapshotCache:
    def test_create_sets_build_hash(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """A freshly created build must carry a non-NULL build_hash, or every
        snapshot for it is permanently stale (build_hash != NULL is always
        true) and the inventory usage count silently under-reports.  Guards
        the legacy gap that migration a3b4c5d6e7f8 backfilled."""
        created = _create_build(client, normal_user_token_headers)
        row = db.get(Build, uuid.UUID(created["id"]))
        assert row is not None
        assert row.build_hash is not None, (
            "create_build must set build_hash; a NULL hash makes every "
            "snapshot for this build look stale forever"
        )

    def test_clone_sets_build_hash(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Clones must carry a build_hash, or GET /optimize/snapshot can
        never report fresh for them (every view re-optimizes)."""
        created = _create_build(client, normal_user_token_headers)

        resp = client.post(
            f"/api/v1/builds/{created['id']}/clone",
            headers=normal_user_token_headers,
        )
        assert resp.status_code == 200, resp.text
        clone_id = resp.json()["id"]

        source_row = db.get(Build, uuid.UUID(created["id"]))
        clone_row = db.get(Build, uuid.UUID(clone_id))
        assert clone_row is not None and source_row is not None
        assert clone_row.build_hash is not None
        # Same scoring-relevant content -> same signature as the source.
        assert clone_row.build_hash == source_row.build_hash

    def test_cumulative_effects_served_but_not_persisted(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """cumulative_effects is a serve-time field: present on every response
        (POST + GET /snapshot) but never written into the stored snapshot."""
        user = _test_user(db)
        profile = _seed_profile_with_relics(db, user.id, with_hash=True)
        build = _create_build(client, normal_user_token_headers)

        run = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={"build_id": build["id"], "profile_id": str(profile.id), "top_n": 5},
        )
        assert run.status_code == 200, run.text
        assert all("cumulative_effects" in r for r in run.json())

        # The persisted snapshot must NOT carry the field.
        snap = db.exec(
            select(OptimizationSnapshot).where(
                OptimizationSnapshot.build_id == uuid.UUID(build["id"]),
                OptimizationSnapshot.slot_index == profile.slot_index,
            )
        ).first()
        assert snap is not None and snap.full_results
        for layout in snap.full_results:
            assert "cumulative_effects" not in layout

        # GET /snapshot reconstructs and re-attaches it at serve time.
        body = client.get(
            "/api/v1/optimize/snapshot",
            params={"build_id": build["id"], "profile_id": str(profile.id)},
            headers=normal_user_token_headers,
        ).json()
        assert body is not None
        assert all("cumulative_effects" in r for r in body["results"])

    def test_snapshot_fresh_after_optimize_then_stale_after_edit(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """DB-mode optimize -> fresh snapshot; scoring-relevant build edit
        -> stale.  Catches hash-computation drift between the seeding path
        (relics_signature) and the snapshot path."""
        user = _test_user(db)
        profile = _seed_profile_with_relics(db, user.id, with_hash=True)
        build = _create_build(client, normal_user_token_headers)

        run = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "top_n": 5,
            },
        )
        assert run.status_code == 200, run.text

        params = {"build_id": build["id"], "profile_id": str(profile.id)}
        snap = client.get(
            "/api/v1/optimize/snapshot",
            params=params,
            headers=normal_user_token_headers,
        )
        assert snap.status_code == 200, snap.text
        body = snap.json()
        assert body is not None, (
            "snapshot must be fresh immediately after a DB-mode optimize — "
            "relics_hash/build_hash parity is broken"
        )
        assert body["results"], "fresh snapshot should contain results"

        # Editing a scoring-relevant field changes build_hash and deletes
        # the now-incomparable snapshot -> stale (null).
        upd = client.put(
            f"/api/v1/builds/{build['id']}",
            headers=normal_user_token_headers,
            json={"groups": [{"weight": 7, "effects": [100], "families": []}]},
        )
        assert upd.status_code == 200, upd.text

        snap_after = client.get(
            "/api/v1/optimize/snapshot",
            params=params,
            headers=normal_user_token_headers,
        )
        assert snap_after.status_code == 200
        assert snap_after.json() is None, (
            "snapshot must be stale after the build's scoring fields change"
        )

    @staticmethod
    def _add_relic_and_rehash(
        db: Session, profile: Profile, *, effect: int, real_id: int = 200
    ) -> None:
        """Append one relic row and refresh profile.relics_hash the way the
        upload endpoints would (whole-inventory signature over all rows)."""
        db.add(Relic(
            owner_id=profile.owner_id,
            profile_id=profile.id,
            ga_handle=0xC0030000 + real_id,
            item_id=real_id + 2147483648,
            real_id=real_id,
            color="Red",
            effect_1=effect, effect_2=EMPTY, effect_3=EMPTY,
            curse_1=EMPTY, curse_2=EMPTY, curse_3=EMPTY,
            is_deep=False,
            name="Churn Relic",
            tier="Delicate",
        ))
        db.flush()
        rows = db.exec(
            select(Relic).where(Relic.profile_id == profile.id)
        ).all()
        owned = [
            OwnedRelic(
                ga_handle=r.ga_handle, item_id=r.item_id, real_id=r.real_id,
                color=r.color,
                effects=[r.effect_1, r.effect_2, r.effect_3],
                curses=[r.curse_1, r.curse_2, r.curse_3],
                is_deep=r.is_deep, name=r.name, tier=r.tier,
            )
            for r in rows
        ]
        profile.relics_hash = relics_signature(owned)
        db.add(profile)
        db.commit()

    def test_snapshot_survives_irrelevant_inventory_churn(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Gaining a relic the build cannot use must NOT stale the snapshot:
        the whole-inventory hash moves but the relevant-subset hash does not,
        so the gate keeps serving the cached results (the fix for every
        save upload forcing a re-optimize of untouched builds)."""
        user = _test_user(db)
        profile = _seed_profile_with_relics(db, user.id, with_hash=True)
        build = _create_build(client, normal_user_token_headers)

        run = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={"build_id": build["id"], "profile_id": str(profile.id), "top_n": 5},
        )
        assert run.status_code == 200, run.text

        # 999999999 resolves to nothing: no direct/text-id/name/family match.
        self._add_relic_and_rehash(db, profile, effect=999999999)

        snap = client.get(
            "/api/v1/optimize/snapshot",
            params={"build_id": build["id"], "profile_id": str(profile.id)},
            headers=normal_user_token_headers,
        )
        assert snap.status_code == 200
        assert snap.json() is not None, (
            "irrelevant inventory churn must not stale the snapshot — the "
            "relevant-subset gate is not being honored"
        )

    def test_snapshot_stale_after_relevant_inventory_change(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Gaining a relic that carries a wanted effect MUST stale the
        snapshot — the optimum may genuinely change."""
        user = _test_user(db)
        profile = _seed_profile_with_relics(db, user.id, with_hash=True)
        build = _create_build(client, normal_user_token_headers)

        run = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={"build_id": build["id"], "profile_id": str(profile.id), "top_n": 5},
        )
        assert run.status_code == 200, run.text

        # Effect 100 is the build's weighted effect (see _create_build).
        self._add_relic_and_rehash(db, profile, effect=100)

        snap = client.get(
            "/api/v1/optimize/snapshot",
            params={"build_id": build["id"], "profile_id": str(profile.id)},
            headers=normal_user_token_headers,
        )
        assert snap.status_code == 200
        assert snap.json() is None, (
            "a build-relevant inventory change must stale the snapshot"
        )

    def test_optimizer_version_bump_stales_snapshot(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """The model docstring promises version-gated freshness: a snapshot
        computed by an older solver must not be served as current."""
        user = _test_user(db)
        profile = _seed_profile_with_relics(db, user.id, with_hash=True)
        build = _create_build(client, normal_user_token_headers)

        run = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={"build_id": build["id"], "profile_id": str(profile.id), "top_n": 5},
        )
        assert run.status_code == 200, run.text

        snap_row = db.exec(
            select(OptimizationSnapshot).where(
                OptimizationSnapshot.build_id == uuid.UUID(build["id"]),
            )
        ).first()
        assert snap_row is not None
        snap_row.optimizer_version = snap_row.optimizer_version - 1
        db.add(snap_row)
        db.commit()

        snap = client.get(
            "/api/v1/optimize/snapshot",
            params={"build_id": build["id"], "profile_id": str(profile.id)},
            headers=normal_user_token_headers,
        )
        assert snap.status_code == 200
        assert snap.json() is None, (
            "an optimizer-version mismatch must stale the snapshot"
        )

    def test_profile_hash_missing_falls_back_to_relic_rows(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """A profile without relics_hash (legacy rows / hashless write paths)
        no longer forces staleness: the relevant-subset gate compares
        content signatures derived from the live Relic rows themselves — the
        same rows the optimizer reads.  Unchanged rows → fresh; a relevant
        row change → stale, with or without the profile hash."""
        user = _test_user(db)
        profile = _seed_profile_with_relics(db, user.id, with_hash=False)
        build = _create_build(client, normal_user_token_headers)

        run = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "top_n": 5,
            },
        )
        assert run.status_code == 200, run.text

        params = {"build_id": build["id"], "profile_id": str(profile.id)}
        snap = client.get(
            "/api/v1/optimize/snapshot",
            params=params,
            headers=normal_user_token_headers,
        )
        assert snap.status_code == 200
        assert snap.json() is not None, (
            "unchanged relic rows must serve the snapshot even when the "
            "profile carries no cached relics_hash"
        )

        # A build-relevant row change must still stale it (hash or no hash).
        db.add(Relic(
            owner_id=profile.owner_id,
            profile_id=profile.id,
            ga_handle=0xC0031234,
            item_id=200 + 2147483648,
            real_id=200,
            color="Red",
            effect_1=100, effect_2=EMPTY, effect_3=EMPTY,
            curse_1=EMPTY, curse_2=EMPTY, curse_3=EMPTY,
            is_deep=False,
            name="Churn Relic",
            tier="Delicate",
        ))
        db.commit()

        snap_after = client.get(
            "/api/v1/optimize/snapshot",
            params=params,
            headers=normal_user_token_headers,
        )
        assert snap_after.status_code == 200
        assert snap_after.json() is None, (
            "a relevant relic-row change must stale the snapshot even "
            "without a profile relics_hash"
        )


@pytest.mark.usefixtures("override_game_data")
class TestBuildSummaries:
    """The /optimize/summaries feed and the mark-reviewed dismissal endpoint
    that back the builds page badge + "Changes since your last save" list."""

    def test_summaries_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/v1/optimize/summaries").status_code in (401, 403)

    def test_summaries_embeds_change_and_reviewed_flag(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """A DB-mode optimize records a snapshot; /summaries surfaces its
        embedded BuildChange and reviewed flag.  A manual optimize is the user
        actively looking, so it is reviewed; the first run's change is "new"."""
        user = _test_user(db)
        profile = _seed_profile_with_relics(db, user.id, with_hash=True)
        build = _create_build(client, normal_user_token_headers)

        run = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "top_n": 5,
            },
        )
        assert run.status_code == 200, run.text

        resp = client.get(
            "/api/v1/optimize/summaries", headers=normal_user_token_headers
        )
        assert resp.status_code == 200, resp.text
        rows = [r for r in resp.json() if r["build_id"] == build["id"]]
        assert len(rows) == 1, "exactly one snapshot summary for the build"
        row = rows[0]
        assert row["reviewed"] is True, "a manual optimize marks the change reviewed"
        assert row["change"] is not None
        assert row["change"]["status"] == "new", "first-ever optimize has no baseline"

    def test_mark_reviewed_clears_unread_change(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """POST /summaries/{id}/reviewed flips an unread (upload-style) change
        to reviewed so it leaves the "changes since last save" list."""
        user = _test_user(db)
        profile = _seed_profile_with_relics(db, user.id, with_hash=True)
        build = _create_build(client, normal_user_token_headers)
        client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={
                "build_id": build["id"],
                "profile_id": str(profile.id),
                "top_n": 5,
            },
        )

        # Simulate the streaming-upload path, which leaves the change unread.
        snap = db.exec(
            select(OptimizationSnapshot).where(
                OptimizationSnapshot.build_id == uuid.UUID(build["id"]),
            )
        ).first()
        assert snap is not None
        snap.reviewed = False
        db.add(snap)
        db.commit()

        before = client.get(
            "/api/v1/optimize/summaries", headers=normal_user_token_headers
        )
        unread = next(r for r in before.json() if r["build_id"] == build["id"])
        assert unread["reviewed"] is False

        ack = client.post(
            f"/api/v1/optimize/summaries/{build['id']}/reviewed",
            headers=normal_user_token_headers,
        )
        assert ack.status_code == 204, ack.text

        after = client.get(
            "/api/v1/optimize/summaries", headers=normal_user_token_headers
        )
        read = next(r for r in after.json() if r["build_id"] == build["id"])
        assert read["reviewed"] is True, "dismissal must persist on the snapshot"
