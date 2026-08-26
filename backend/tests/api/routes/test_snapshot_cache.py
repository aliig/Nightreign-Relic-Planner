"""Tests for the optimization snapshot cache plumbing.

The POST /optimize/snapshot/query freshness contract: a snapshot is served
when its build_hash and optimizer/game-data versions match the live inputs AND
the inventory is unchanged — either byte-identical (relics_hash) or unchanged
in the build-RELEVANT subset (relevant_relics_hash), so irrelevant inventory
churn keeps serving the cache.  The inventory side is the EFFECTIVE inventory:
profile relics with the request's staged diff (sells/mints) applied.  These
tests pin the plumbing that keeps the contract honest: build_hash on clone,
hash parity between the seeding and snapshot paths, invalidation on build
edit, missing-hash staleness, the relevant-subset gate, and the staged-diff
freshness/auto-heal/cause-attribution semantics.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import (
    Build,
    OptimizationSnapshot,
    Profile,
    Relic,
)
from nrplanner.changes import relics_signature
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import OwnedRelic
from tests.utils.seeding import (
    create_build,
    get_test_user,
    query_snapshot,
    relic_row_from_owned,
    seed_profile_with_relics,
)

EMPTY = EMPTY_EFFECT


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
        created = create_build(client, normal_user_token_headers)
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
        """Clones must carry a build_hash, or the snapshot query can
        never report fresh for them (every view re-optimizes)."""
        created = create_build(client, normal_user_token_headers)

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
        (POST /optimize/ + snapshot query) but never written into the stored
        snapshot."""
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

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

        # The snapshot query reconstructs and re-attaches it at serve time.
        body = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
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
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

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

        snap = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
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

        snap_after = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
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
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

        run = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={"build_id": build["id"], "profile_id": str(profile.id), "top_n": 5},
        )
        assert run.status_code == 200, run.text

        # 999999999 resolves to nothing: no direct/text-id/name/family match.
        self._add_relic_and_rehash(db, profile, effect=999999999)

        snap = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
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
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

        run = client.post(
            "/api/v1/optimize/",
            headers=normal_user_token_headers,
            json={"build_id": build["id"], "profile_id": str(profile.id), "top_n": 5},
        )
        assert run.status_code == 200, run.text

        # Effect 100 is the build's weighted effect (see create_build).
        self._add_relic_and_rehash(db, profile, effect=100)

        snap = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
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
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

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

        snap = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
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
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=False)
        build = create_build(client, normal_user_token_headers)

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

        snap = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
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

        snap_after = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert snap_after.status_code == 200
        assert snap_after.json() is None, (
            "a relevant relic-row change must stale the snapshot even "
            "without a profile relics_hash"
        )


def _legal_mint(handle: int = -1) -> dict:
    """A guaranteed-legal StagedMint payload, rolled from real game data.

    Deterministic seed → stable across runs; the generator only emits relics
    that pass RelicChecker, so this always survives staged-mint validation.
    """
    from app.core.game_data import get_relic_generator

    rolled = get_relic_generator().roll(is_deep=False, version="1.03", seed=1234)
    return {
        "handle": handle,
        "real_id": rolled.real_id,
        "effects": list(rolled.effects),
        "curses": list(rolled.curses),
    }


@pytest.mark.usefixtures("override_game_data")
class TestStagedSnapshotCache:
    """Staged-diff semantics: effective-inventory freshness, the staged
    cause attribution, and the export→re-upload auto-heal property."""

    def _run(self, client, headers, build_id, profile_id, **extra):
        return client.post(
            "/api/v1/optimize/",
            headers=headers,
            json={
                "build_id": build_id,
                "profile_id": profile_id,
                "top_n": 5,
                **extra,
            },
        )

    def test_staged_run_stores_signature_and_serves_same_staged_state(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)
        sold = 0xC0020000  # first seeded relic

        run = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id),
            staged_sells=[sold],
        )
        assert run.status_code == 200, run.text

        snap_row = db.exec(
            select(OptimizationSnapshot).where(
                OptimizationSnapshot.build_id == uuid.UUID(build["id"]),
            )
        ).first()
        assert snap_row is not None
        assert snap_row.staged_signature is not None, (
            "a staged run must record its staged_signature"
        )

        # Same staged state -> served from cache.
        same = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id),
            staged_sells=[sold],
        )
        assert same.status_code == 200
        assert same.json() is not None, (
            "the snapshot query must serve a staged run back to the same "
            "staged state"
        )

        # Pure state (no staged fields) -> the sold relic is back in the
        # effective inventory and it is build-relevant -> stale.
        pure = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert pure.status_code == 200
        assert pure.json() is None, (
            "a staged-run snapshot must not be served to the pure save state "
            "when the diff touches build-relevant relics (no wedged state — "
            "the client sees stale and re-runs)"
        )

    def test_pure_run_not_served_to_staged_state(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

        run = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert run.status_code == 200, run.text

        staged = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id),
            staged_sells=[0xC0020000],
        )
        assert staged.status_code == 200
        assert staged.json() is None, (
            "a pure-run snapshot must not be served when staged sells remove "
            "a build-relevant relic from the effective inventory"
        )

    def test_staged_sell_of_irrelevant_relic_keeps_cache(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Staging a sell the build cannot use must keep serving the cached
        pure results — the relevant-subset gate applies to staged diffs too."""
        from tests.utils.seeding import default_owned_relics

        irrelevant = OwnedRelic(
            ga_handle=0xC0025555,
            item_id=100 + 2147483648,
            real_id=100,
            color="Red",
            effects=[999999999, EMPTY, EMPTY],
            curses=[EMPTY, EMPTY, EMPTY],
            is_deep=False,
            name="Irrelevant Relic",
            tier="Delicate",
        )
        user = get_test_user(db)
        profile = seed_profile_with_relics(
            db, user.id, with_hash=True,
            owned=default_owned_relics() + [irrelevant],
        )
        build = create_build(client, normal_user_token_headers)

        run = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert run.status_code == 200, run.text

        snap = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id),
            staged_sells=[irrelevant.ga_handle],
        )
        assert snap.status_code == 200
        assert snap.json() is not None, (
            "selling a build-irrelevant relic must not stale the snapshot"
        )

    def test_auto_heal_after_export_and_reupload(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """The load-bearing invariant: a snapshot computed from the EFFECTIVE
        inventory (base − sells + mints) hash-matches the save the user gets
        by actually exporting those edits and re-uploading it — content
        fingerprints carry no handles, so the game's handle renumbering
        cannot break the match.  Simulates the re-upload by rewriting the
        Relic rows + Profile.relics_hash exactly as the upload path would."""
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)
        sold = 0xC0020000
        mint = _legal_mint(handle=-1)

        run = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id),
            staged_sells=[sold], staged_mints=[mint],
        )
        assert run.status_code == 200, run.text

        # Simulate export → in-game import → re-upload: the sold relic is
        # gone, the mint exists under a fresh GAME handle (renumbered), and
        # the profile hash is recomputed from the new rows.
        rows = db.exec(
            select(Relic).where(Relic.profile_id == profile.id)
        ).all()
        for r in rows:
            if r.ga_handle == sold:
                db.delete(r)
        db.flush()
        minted = OwnedRelic(
            ga_handle=0xC0099999,  # game-assigned, unrelated to the synthetic -1
            item_id=mint["real_id"] + 2147483648,
            real_id=mint["real_id"],
            color="Red",  # display fields don't enter the content fingerprint
            effects=(mint["effects"] + [EMPTY] * 3)[:3],
            curses=(mint["curses"] + [EMPTY] * 3)[:3],
            is_deep=False,
            name="Minted",
            tier="Delicate",
        )
        db.add(relic_row_from_owned(profile, minted))
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

        pure = query_snapshot(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert pure.status_code == 200
        assert pure.json() is not None, (
            "auto-heal broken: a staged-run snapshot must be served fresh "
            "once the exported save (same content, renumbered handles) is "
            "re-uploaded"
        )

    def test_cause_attribution_staged_transitions(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Causes name what moved since the BASELINE, not since the last run:
        a staged diff attributes to "staged", dropping it again is a return to
        the baseline (nothing to say), and a real inventory change is
        "relics" — which is compared on the save's own hash, so a staged
        purchase can never masquerade as a newer save."""
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)
        sold = 0xC0020000

        first = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert first.status_code == 200, first.text

        def _snap() -> OptimizationSnapshot:
            row = db.exec(
                select(OptimizationSnapshot).where(
                    OptimizationSnapshot.build_id == uuid.UUID(build["id"]),
                )
            ).first()
            assert row is not None
            db.refresh(row)
            return row

        def _last_change() -> dict | None:
            return _snap().last_change

        # pure → staged: the inventory delta is the staged sell alone.
        staged_run = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id),
            staged_sells=[sold],
        )
        assert staged_run.status_code == 200, staged_run.text
        change = _last_change()
        assert change is not None and change["causes"] == ["staged"], change
        assert change["cause"] == "staged", change

        # A staged purchase is news the user should see, so it must NOT quietly
        # mark the build reviewed (which is how an unread upload change used to
        # get wiped by a trip through Relic Rites).
        assert _snap().reviewed is False

        # staged → pure: back to exactly the baseline state, so there is
        # nothing left to tell the user.
        pure_run = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert pure_run.status_code == 200, pure_run.text
        change = _last_change()
        assert change is not None and change["causes"] == [], change
        assert change["cause"] is None, change

        # pure → pure with a real inventory change is "relics".
        TestSnapshotCache._add_relic_and_rehash(db, profile, effect=100)
        relics_run = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert relics_run.status_code == 200, relics_run.text
        change = _last_change()
        assert change is not None and change["causes"] == ["relics"], change

    def test_cross_version_change_is_marked_incomparable(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """The 2026-08-12 regression, end to end.

        An upload that ADDED relics also crossed an optimizer-version boundary
        (v3 -> v4 made the Required row a hard constraint, which can only lower
        a build's optimum).  The fresh results were then diffed against layouts
        scored under the OLD rules, and 11 builds were narrated as "your save
        made this weaker".

        Both things really moved, so both causes are named and the change is
        still news — but ``comparable`` is False, which is what stops the UI
        quoting a percentage between two incomparable scores.
        """
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

        first = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert first.status_code == 200, first.text

        def _snap() -> OptimizationSnapshot:
            row = db.exec(
                select(OptimizationSnapshot).where(
                    OptimizationSnapshot.build_id == uuid.UUID(build["id"]),
                )
            ).first()
            assert row is not None
            db.refresh(row)
            return row

        # Age the BASELINE to a previous optimizer version, leaving the layouts
        # it recorded in place — exactly the state a snapshot last optimized
        # before a version bump is in.  Reassigned rather than mutated so the
        # JSON column is seen as dirty.
        snap = _snap()
        baseline = dict(snap.baseline)
        baseline["inputs"] = {**baseline["inputs"], "optimizer_version": "3"}
        snap.baseline = baseline
        db.add(snap)
        db.commit()

        # ...and move the inventory too, so the relics hash is genuinely newer.
        TestSnapshotCache._add_relic_and_rehash(db, profile, effect=100)

        rerun = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert rerun.status_code == 200, rerun.text
        change = _snap().last_change
        assert change is not None
        assert change["causes"] == ["relics", "game_data"], change
        assert change["comparable"] is False, (
            "a delta measured across an optimizer-version boundary must not be "
            "presented as the build getting stronger or weaker"
        )
        # The relics arriving is still real news the user must see.
        assert _snap().reviewed is False

    def test_save_change_and_staged_purchase_compose_into_one_change(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """The workflow the sticky baseline exists for: a newer save arrives,
        and before reading the change the user buys relics in Relic Rites.  The
        second run must still measure from the baseline — naming BOTH causes —
        rather than re-baselining on the save and reporting only the purchase.
        """
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

        first = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert first.status_code == 200, first.text

        def _snap() -> OptimizationSnapshot:
            row = db.exec(
                select(OptimizationSnapshot).where(
                    OptimizationSnapshot.build_id == uuid.UUID(build["id"]),
                )
            ).first()
            assert row is not None
            db.refresh(row)
            return row

        baseline_score = _snap().baseline["best_score"]

        # A newer save lands (relic added), and the build re-optimizes.
        TestSnapshotCache._add_relic_and_rehash(db, profile, effect=100)
        save_run = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        )
        assert save_run.status_code == 200, save_run.text
        assert _snap().last_change["causes"] == ["relics"]

        # ...then the user spends Murk in Relic Rites before reading it.
        mint = _legal_mint(handle=-1)
        rites_run = self._run(
            client, normal_user_token_headers, build["id"], str(profile.id),
            staged_mints=[mint],
        )
        assert rites_run.status_code == 200, rites_run.text
        change = _snap().last_change
        assert change["causes"] == ["relics", "staged"], change
        assert change["cause"] == "mixed", change
        # Still measured from the state the user last saw, not from the save
        # run that was never read.
        assert change["best_before"] == baseline_score, change
        assert _snap().baseline["best_score"] == baseline_score

    def test_review_advances_the_baseline(
        self,
        client: TestClient,
        normal_user_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        """Reviewing is the only acknowledgement of news, so it — and only it —
        moves the baseline forward: the same state re-run afterwards has
        nothing left to report."""
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

        assert self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        ).status_code == 200

        TestSnapshotCache._add_relic_and_rehash(db, profile, effect=100)
        assert self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        ).status_code == 200

        def _snap() -> OptimizationSnapshot:
            row = db.exec(
                select(OptimizationSnapshot).where(
                    OptimizationSnapshot.build_id == uuid.UUID(build["id"]),
                )
            ).first()
            assert row is not None
            db.refresh(row)
            return row

        assert _snap().last_change["causes"] == ["relics"]
        assert _snap().reviewed is False

        seen = client.post(
            f"/api/v1/optimize/summaries/{build['id']}/reviewed",
            headers=normal_user_token_headers,
        )
        assert seen.status_code == 204, seen.text
        assert _snap().reviewed is True

        assert self._run(
            client, normal_user_token_headers, build["id"], str(profile.id)
        ).status_code == 200
        change = _snap().last_change
        assert change["causes"] == [], change
        assert change["status"] == "unchanged", change


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
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)

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
        user = get_test_user(db)
        profile = seed_profile_with_relics(db, user.id, with_hash=True)
        build = create_build(client, normal_user_token_headers)
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
