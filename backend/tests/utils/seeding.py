"""Shared DB seeding + snapshot-query helpers for optimize/snapshot tests."""
import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import Profile, Relic, SaveUpload, User
from nrplanner.changes import relics_signature
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import OwnedRelic

EMPTY = EMPTY_EFFECT


def get_test_user(db: Session) -> User:
    user = db.exec(
        select(User).where(User.email == settings.EMAIL_TEST_USER)
    ).first()
    assert user is not None
    return user


def default_owned_relics() -> list[OwnedRelic]:
    """3 one-effect relics (effect 100) in distinct colors.

    The three share a real_id, so they are ONE relic as far as content identity
    goes (nrplanner.changes.relic_fingerprint is real_id + effects + curses,
    deliberately colour-free).  That is load-bearing, not incidental: only one
    of them is ever placed, every vessel that can hold one ties at the same
    score, and which one wins depends on the order the optimizer pool's futures
    complete.  Because their fingerprints are equal, that race is invisible to
    diff_results, which compares the top layout's fingerprint multiset -- give
    them distinct real_ids and TestStagedSnapshotCache
    ::test_review_advances_the_baseline starts reporting "reordered" instead of
    "unchanged" on roughly a fifth of full-suite runs.

    Tests that need to tell the seeded relics APART must therefore bring their
    own inventory (see _distinct_owned_relics in test_loadout_ranks.py) rather
    than making these distinct.
    """
    return [
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


def seed_profile_with_relics(
    db: Session,
    owner_id: uuid.UUID,
    *,
    with_hash: bool,
    owned: list[OwnedRelic] | None = None,
) -> Profile:
    """SaveUpload + Profile + relic rows for the given owner.

    With ``with_hash=True`` the profile gets the same relics_hash the upload
    endpoints compute, so a subsequent DB-mode optimize produces a snapshot
    that must compare fresh against it.
    """
    save = SaveUpload(owner_id=owner_id, platform="PC", profile_count=1)
    db.add(save)
    db.flush()

    if owned is None:
        owned = default_owned_relics()
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
        db.add(relic_row_from_owned(profile, r))
    db.commit()
    return profile


def relic_row_from_owned(profile: Profile, r: OwnedRelic) -> Relic:
    return Relic(
        owner_id=profile.owner_id,
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
    )


def create_build(
    client: TestClient, headers: dict[str, str], **overrides
) -> dict:
    payload = {
        "name": "Snapshot Cache Build",
        "character": "Wylder",
        "groups": [{"weight": 10, "effects": [100], "families": []}],
        **overrides,
    }
    resp = client.post("/api/v1/builds/", headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def query_snapshot(
    client: TestClient,
    headers: dict[str, str],
    build_id: str,
    profile_id: str,
    *,
    staged_sells: list[int] | None = None,
    staged_mints: list[dict] | None = None,
):
    """POST /optimize/snapshot/query with an optional staged diff."""
    body: dict = {"build_id": build_id, "profile_id": profile_id}
    if staged_sells:
        body["staged_sells"] = staged_sells
    if staged_mints:
        body["staged_mints"] = staged_mints
    return client.post(
        "/api/v1/optimize/snapshot/query", json=body, headers=headers
    )
