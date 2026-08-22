"""Who the save-to-save comparison is allowed to run against.

"Changes since your last save" is only meaningful within one game account AND
one character.  Upload a friend's save to try the app on their inventory and a
naive diff reports your entire collection as lost; delete a character and roll a
new one in the same slot and it does the same thing.  These cover the two gates
and the reason reported back to the upload page, which used to suppress the
comparison silently — indistinguishable from "nothing changed".
"""
import uuid
from types import SimpleNamespace
from typing import Any

from nrplanner.changes import build_signature
from nrplanner.models import (
    BuildDefinition,
    OwnedRelic,
    SlotAssignment,
    VesselResult,
    WeightGroup,
)
from nrplanner.optimizer import OPTIMIZER_VERSION

from app.api.routes.saves import (
    _account_reason,
    _AffectedBuild,
    _builds_without_snapshots,
    _compute_relic_delta,
    _identify_affected_builds,
    _remap_carried_handles,
    _rerun_vessel_ids,
    _restarted_slots,
    _same_account,
)
from app.core.game_data import game_data_version, get_game_data
from app.models import (
    Build,
    ParsedProfileData,
    ParsedRelicData,
    Profile,
    Relic,
)

EMPTY = 4294967295


def _fp(n: int) -> tuple:
    """A distinct relic content fingerprint, as the slot maps hold them."""
    return (100 + n, n, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY)


def _fps(*ns: int) -> list[tuple]:
    return [_fp(n) for n in ns]


class TestAccountGate:
    def test_same_owner_compares(self) -> None:
        assert _same_account("7656119", "7656119") is True
        assert _account_reason("7656119", "7656119") == "ok"

    def test_a_friends_save_is_not_compared(self) -> None:
        assert _same_account("7656119", "7656120") is False
        assert _account_reason("7656119", "7656120") == "different_account"

    def test_unreadable_owner_still_compares_but_says_so(self) -> None:
        """PS4 memory.dat has no readable owner anchor.  Fail OPEN — an unknown
        owner must never hide a real change — but flag it, so a user testing a
        friend's console save can see why the diff looks absurd."""
        assert _same_account(None, "7656119") is True
        assert _same_account("7656119", None) is True
        assert _account_reason(None, "7656119") == "unverified_owner"
        assert _account_reason("7656119", None) == "unverified_owner"


class TestRestartedSlots:
    def test_ordinary_session_is_not_a_restart(self) -> None:
        """Play keeps almost the whole inventory: a few new relics, a few sold."""
        old = {0: _fps(*range(20))}
        new = {0: _fps(*range(2, 24))}
        assert _restarted_slots(old, new) == set()

    def test_zero_overlap_is_a_different_character(self) -> None:
        old = {0: _fps(*range(20))}
        new = {0: _fps(*range(100, 103))}
        assert _restarted_slots(old, new) == {0}

    def test_only_the_restarted_slot_is_flagged(self) -> None:
        old = {0: _fps(*range(20)), 1: _fps(*range(30, 50))}
        new = {0: _fps(*range(100, 103)), 1: _fps(*range(31, 50))}
        assert _restarted_slots(old, new) == {0}

    def test_small_previous_inventory_is_left_alone(self) -> None:
        """Under the threshold the signal is too weak: a new character really
        can hold three relics and sell all three."""
        old = {0: _fps(1, 2, 3)}
        new = {0: _fps(50, 51)}
        assert _restarted_slots(old, new) == set()

    def test_missing_or_empty_new_slot_is_a_normal_diff(self) -> None:
        """Nothing at all in the slot is a real (if drastic) change, not a
        different character — the ordinary diff can say so."""
        assert _restarted_slots({0: _fps(*range(20))}, {}) == set()
        assert _restarted_slots({0: _fps(*range(20))}, {0: []}) == set()


class TestRelicDeltaSkipsRestartedSlots:
    @staticmethod
    def _db_relic(profile_id: uuid.UUID, n: int) -> Relic:
        return Relic(
            owner_id=uuid.uuid4(),
            profile_id=profile_id,
            ga_handle=0xC0000000 + n,
            item_id=100 + n + 2147483648,
            real_id=100 + n,
            color="Red",
            effect_1=n, effect_2=EMPTY, effect_3=EMPTY,
            curse_1=EMPTY, curse_2=EMPTY, curse_3=EMPTY,
            is_deep=False,
            name=f"Relic {n}",
            tier="Delicate",
        )

    @staticmethod
    def _parsed(slot: int, ns: list[int]) -> ParsedProfileData:
        return ParsedProfileData(
            slot_index=slot,
            name="Tarnished",
            relic_count=len(ns),
            relics=[
                ParsedRelicData(
                    ga_handle=0xD0000000 + n,
                    item_id=100 + n + 2147483648,
                    real_id=100 + n,
                    color="Red",
                    effect_1=n, effect_2=EMPTY, effect_3=EMPTY,
                    curse_1=EMPTY, curse_2=EMPTY, curse_3=EMPTY,
                    is_deep=False,
                    name=f"Relic {n}",
                    tier="Delicate",
                )
                for n in ns
            ],
        )

    def test_counts_a_normal_change(self) -> None:
        prof_id = uuid.uuid4()
        old = [self._db_relic(prof_id, n) for n in range(5)]
        new = [self._parsed(0, list(range(1, 7)))]
        delta = _compute_relic_delta(old, new, {prof_id: 0}, set())
        assert (delta.added, delta.removed) == (2, 1)

    def test_a_restarted_slot_contributes_nothing(self) -> None:
        """Without the skip this reports 20 relics lost and 3 gained — a scary,
        entirely fictional headline for someone who simply re-rolled."""
        prof_id = uuid.uuid4()
        old = [self._db_relic(prof_id, n) for n in range(20)]
        new = [self._parsed(0, [100, 101, 102])]
        delta = _compute_relic_delta(old, new, {prof_id: 0}, {0})
        assert (delta.added, delta.removed) == (0, 0)

    def test_other_slots_still_count(self) -> None:
        restarted_id, kept_id = uuid.uuid4(), uuid.uuid4()
        old = [self._db_relic(restarted_id, n) for n in range(20)]
        old += [self._db_relic(kept_id, n) for n in range(50, 55)]
        new = [self._parsed(0, [100, 101]), self._parsed(1, list(range(50, 57)))]
        delta = _compute_relic_delta(
            old, new, {restarted_id: 0, kept_id: 1}, {0}
        )
        assert (delta.added, delta.removed) == (2, 0)


class TestNeverOptimizedBuilds:
    """A build with no snapshot has no results for the upload diff to
    invalidate, so the snapshot-driven scan cannot see it at all — it would sit
    resultless upload after upload.  These join the run on their own."""

    @staticmethod
    def _build(name: str) -> Build:
        return Build(id=uuid.uuid4(), owner_id=uuid.uuid4(), name=name,
                     character="Wylder")

    @staticmethod
    def _profile(slot: int) -> ParsedProfileData:
        return ParsedProfileData(
            slot_index=slot, name="Tarnished", relic_count=0, relics=[]
        )

    def test_only_builds_with_no_snapshot_are_added(self) -> None:
        optimized, fresh = self._build("Old"), self._build("New")
        out = _builds_without_snapshots(
            [optimized, fresh], {optimized.id}, [self._profile(0)]
        )
        assert [ab.build.id for ab in out] == [fresh.id]
        assert out[0].broken_pins == []

    def test_aimed_at_the_slot_the_ui_shows(self) -> None:
        """profiles[0] — lowest slot_index — is what the builds list and the
        optimize page default to; a snapshot for any other slot would be
        invisible there."""
        fresh = self._build("New")
        out = _builds_without_snapshots(
            [fresh], set(), [self._profile(2), self._profile(1)]
        )
        assert [ab.slot_index for ab in out] == [1]

    def test_ordered_by_name(self) -> None:
        builds = [self._build("Zephyr"), self._build("Alpha"), self._build("Mid")]
        out = _builds_without_snapshots(builds, set(), [self._profile(0)])
        assert [ab.build.name for ab in out] == ["Alpha", "Mid", "Zephyr"]

    def test_no_save_slots_means_nothing_to_optimize_against(self) -> None:
        out = _builds_without_snapshots([self._build("New")], set(), [])
        assert out == []


class TestVesselReuseGate:
    """When may an upload keep a build's cached per-vessel results?

    Only when the cache provably describes the same build, at the same
    version, over exactly the inventory being diffed away from — and the diff
    only ADDS.  The snapshot stores a global top-N, so a removal can promote a
    layout it no longer holds (nrplanner/tests/test_vessel_reuse.py shows the
    divergence).  Each test here removes one leg and expects a full re-run.
    """
    import uuid as _uuid

    VESSELS = [
        {"vessel_id": 1, "Colors": ("Red", "Red", "Red", "Red", "Red", "Red")},
        {"vessel_id": 2, "Colors": ("Blue", "Blue", "Blue",
                                    "Blue", "Blue", "Blue")},
    ]

    @staticmethod
    def _build_def() -> BuildDefinition:
        return BuildDefinition(
            id="b", name="B", character="Wylder",
            groups=[WeightGroup(weight=50, effects=[1001])],
        )

    @classmethod
    def _snap(cls, build_def: BuildDefinition, **over: Any) -> SimpleNamespace:
        fields = {
            "relics_hash": "OLDHASH",
            "build_hash": build_signature(build_def),
            "optimizer_version": OPTIMIZER_VERSION,
            "game_data_version": game_data_version(),
            "staged_signature": None,
            "top_n": 10,
            "max_per_vessel": 3,
        }
        fields.update(over)
        return SimpleNamespace(**fields)

    @classmethod
    def _affected(cls, **over: Any) -> _AffectedBuild:
        build = Build(id=cls._uuid.uuid4(), owner_id=cls._uuid.uuid4(),
                      name="B", character="Wylder")
        fields = {"added_keys": {("Red", False)}, "additions_only": True}
        fields.update(over)
        return _AffectedBuild(build=build, slot_index=0, **fields)

    def _call(self, ab=None, snap=None, old_hash="OLDHASH"):
        bd = self._build_def()
        return _rerun_vessel_ids(
            ab or self._affected(),
            bd,
            self._snap(bd) if snap is None else snap,
            old_hash,
            self.VESSELS,
        )

    def test_reuses_the_vessels_a_red_addition_cannot_reach(self) -> None:
        assert self._call() == {1}

    def test_a_relevant_removal_forces_a_full_run(self) -> None:
        assert self._call(ab=self._affected(additions_only=False)) is None

    def test_a_broken_pin_forces_a_full_run(self) -> None:
        """Pins are pre-assigned before any slot is solved, so a broken one
        changes every vessel — additions_only is False in that case."""
        assert self._call(ab=self._affected(
            additions_only=False, added_keys={("Red", False)})) is None

    def test_nothing_added_means_nothing_to_plan(self) -> None:
        assert self._call(ab=self._affected(added_keys=set())) is None

    def test_no_snapshot_forces_a_full_run(self) -> None:
        """A build with no cached results has nothing to carry."""
        assert _rerun_vessel_ids(
            self._affected(), self._build_def(), None, "OLDHASH", self.VESSELS
        ) is None

    def test_snapshot_from_a_different_inventory_forces_a_full_run(self) -> None:
        """The cache must describe the save we are diffing away from; anything
        else and the carried vessels are answers to a different question."""
        bd = self._build_def()
        assert _rerun_vessel_ids(
            self._affected(), bd, self._snap(bd, relics_hash="SOMETHING_ELSE"),
            "OLDHASH", self.VESSELS,
        ) is None

    def test_unknown_previous_inventory_forces_a_full_run(self) -> None:
        assert self._call(old_hash=None) is None

    def test_staged_results_are_not_a_base(self) -> None:
        """A staged diff means the cached layouts used relics that were never
        in the save."""
        bd = self._build_def()
        assert _rerun_vessel_ids(
            self._affected(), bd, self._snap(bd, staged_signature="abc"),
            "OLDHASH", self.VESSELS,
        ) is None

    def test_edited_build_forces_a_full_run(self) -> None:
        bd = self._build_def()
        assert _rerun_vessel_ids(
            self._affected(), bd, self._snap(bd, build_hash="STALE"),
            "OLDHASH", self.VESSELS,
        ) is None

    def test_a_curse_weighted_build_forces_a_full_run(self) -> None:
        """The relevance scan behind additions_only ignores
        default_curse_weight, so a curse-weighting build could hide a relevant
        removal.  None exists today — this keeps the guard honest if one does.
        """
        bd = BuildDefinition(
            id="b", name="B", character="Wylder",
            groups=[WeightGroup(weight=50, effects=[1001])],
            default_curse_weight=3,
        )
        assert _rerun_vessel_ids(
            self._affected(), bd, self._snap(bd), "OLDHASH", self.VESSELS,
        ) is None

    def test_version_drift_forces_a_full_run(self) -> None:
        bd = self._build_def()
        for over in ({"optimizer_version": OPTIMIZER_VERSION - 1},
                     {"game_data_version": "stale"},
                     {"top_n": 3},
                     {"max_per_vessel": 1}):
            assert _rerun_vessel_ids(
                self._affected(), bd, self._snap(bd, **over),
                "OLDHASH", self.VESSELS,
            ) is None, over


class TestCarriedHandleRemap:
    """Cached layouts are re-pointed at the new save's handles.

    The game renumbers every ga_handle when it writes a save, so a layout kept
    from the previous upload references handles that no longer exist.  Content
    survives; handles do not.
    """

    @staticmethod
    def _result(handles: list[int | None]) -> VesselResult:
        assignments = []
        for i, h in enumerate(handles):
            relic = None if h is None else OwnedRelic(
                ga_handle=h, item_id=h + 1, real_id=100, color="Red",
                effects=[1, EMPTY, EMPTY], curses=[EMPTY, EMPTY, EMPTY],
                is_deep=False, name="R", tier="Delicate",
            )
            assignments.append(SlotAssignment(
                slot_index=i, slot_color="Red", is_deep=False,
                relic=relic, score=0, breakdown=[],
            ))
        return VesselResult(
            vessel_id=1, vessel_name="V", vessel_character="Wylder",
            unlock_flag=0, slot_colors=("Red",) * len(handles),
            assignments=assignments, total_score=10,
        )

    def test_rewrites_every_handle(self) -> None:
        results = [self._result([10, 11, None])]
        assert _remap_carried_handles(results, {10: 90, 11: 91}) is True
        assert [a.relic.ga_handle if a.relic else None
                for a in results[0].assignments] == [90, 91, None]

    def test_a_missing_relic_kills_the_reuse(self) -> None:
        """The layout used a relic the new save does not have — it cannot be
        carried, and the caller must re-run the build in full."""
        results = [self._result([10, 11])]
        assert _remap_carried_handles(results, {10: 90}) is False

    def test_empty_slots_are_left_alone(self) -> None:
        results = [self._result([None, None])]
        assert _remap_carried_handles(results, {}) is True


class TestVersionStaleForcesRerun:
    """An optimizer/game-data bump must pull a build into the upload's run.

    The serve path refuses to hand out a snapshot whose version doesn't match,
    so a build left out of the upload would show numbers it will never serve
    until someone opens it by hand.  The relic diff alone can't notice this —
    the inventory is identical.
    """
    OWNER = uuid.uuid4()
    PROFILE_ID = uuid.uuid4()

    @classmethod
    def _unchanged_inventory(cls) -> tuple[list, list, list]:
        """One relic, present identically before and after — an empty diff."""
        old_relic = Relic(
            owner_id=cls.OWNER, profile_id=cls.PROFILE_ID, ga_handle=1,
            item_id=1, real_id=101, color="Red",
            effect_1=1001, effect_2=EMPTY, effect_3=EMPTY,
            curse_1=EMPTY, curse_2=EMPTY, curse_3=EMPTY,
            is_deep=False, name="R", tier="Delicate",
        )
        old_profile = Profile(
            id=cls.PROFILE_ID, owner_id=cls.OWNER,
            save_upload_id=uuid.uuid4(), slot_index=0, name="P",
        )
        new_profile = ParsedProfileData(
            slot_index=0, name="P", relic_count=1,
            relics=[ParsedRelicData(
                ga_handle=2, item_id=1, real_id=101, color="Red",
                effect_1=1001, effect_2=EMPTY, effect_3=EMPTY,
                curse_1=EMPTY, curse_2=EMPTY, curse_3=EMPTY,
                is_deep=False, name="R", tier="Delicate",
            )],
        )
        return [old_relic], [old_profile], [new_profile]

    @classmethod
    def _run(cls, **snap_over: Any) -> list:
        build = Build(id=uuid.uuid4(), owner_id=cls.OWNER,
                      name="B", character="Wylder")
        fields = {
            "build_id": build.id,
            "slot_index": 0,
            "optimizer_version": OPTIMIZER_VERSION,
            "game_data_version": game_data_version(),
        }
        fields.update(snap_over)
        snap = SimpleNamespace(**fields)
        # _identify_affected_builds touches the session only to list snapshots.
        session = SimpleNamespace(
            exec=lambda _stmt: SimpleNamespace(all=lambda: [snap])
        )
        old_relics, old_profiles, new_profiles = cls._unchanged_inventory()
        return _identify_affected_builds(
            session, get_game_data(), cls.OWNER,
            old_relics, old_profiles, new_profiles,
            [build], handle_remap={},
        )

    def test_unchanged_inventory_at_current_version_is_left_alone(
        self,
    ) -> None:
        assert self._run() == []

    def test_optimizer_version_bump_pulls_the_build_in(self) -> None:
        out = self._run(optimizer_version=OPTIMIZER_VERSION - 1)
        assert len(out) == 1
        # Vessel-level reuse would carry layouts computed by the old solver.
        assert out[0].additions_only is False

    def test_game_data_bump_pulls_the_build_in(self) -> None:
        out = self._run(game_data_version="stale")
        assert len(out) == 1
        assert out[0].additions_only is False
