"""Unit tests for app.core.snapshot_baseline — the seam where a BuildChange
learns WHY it happened.

``diff_results`` builds the change from layouts alone and cannot know that the
optimizer or the game data moved underneath it, so the two backend call sites
(POST /optimize and the save-upload sweep) both delegate that to
``apply_causes``.  The rule these tests pin: a change that crosses a version
boundary keeps its layout diff and loses its score claim.
"""
from nrplanner.models import BuildChange

from app.core.snapshot_baseline import (
    advanced_baselines,
    apply_causes,
    causes_since,
    is_narratable,
    is_staged_baseline,
    make_baseline,
    pick_baseline,
    reviewed_baselines,
    scores_comparable,
    snapshot_inputs,
)


def _inputs(
    *,
    base_relics: str = "r1",
    build: str = "b1",
    gdv: str = "g1",
    optimizer: str = "4",
    staged: str | None = None,
) -> dict:
    return snapshot_inputs(
        base_relics_hash=base_relics,
        relics_hash=base_relics,
        build_hash=build,
        game_data_version=gdv,
        optimizer_version=optimizer,
        staged_signature=staged,
    )


def _baseline(**kw) -> dict:
    return make_baseline(layouts=[{"total_score": 100}], best_score=100,
                         inputs=_inputs(**kw))


def _change() -> BuildChange:
    return BuildChange(status="degraded", best_before=100, best_after=88, delta=-12)


class TestScoresComparable:
    def test_ordinary_causes_leave_the_score_comparable(self):
        assert scores_comparable([])
        assert scores_comparable(["relics"])
        assert scores_comparable(["staged"])
        assert scores_comparable(["relics", "staged", "build_edit"])

    def test_a_rules_change_invalidates_the_comparison(self):
        assert not scores_comparable(["game_data"])
        assert not scores_comparable(["relics", "game_data"])


class TestApplyCauses:
    def test_fills_all_three_derived_fields(self):
        change = _change()
        apply_causes(change, _baseline(), _inputs(base_relics="r2"))
        assert change.causes == ["relics"]
        assert change.cause == "relics"
        assert change.comparable is True

    def test_first_ever_run_has_no_causes_and_stays_comparable(self):
        change = _change()
        apply_causes(change, None, _inputs())
        assert change.causes == []
        assert change.cause is None
        assert change.comparable is True

    def test_optimizer_version_bump_voids_the_score_comparison(self):
        change = _change()
        apply_causes(change, _baseline(optimizer="4"), _inputs(optimizer="5"))
        assert change.causes == ["game_data"]
        assert change.comparable is False

    def test_game_data_bump_voids_it_too(self):
        """A balance change moves effect values, so the old score is no more
        comparable than one from a different optimizer version."""
        change = _change()
        apply_causes(change, _baseline(gdv="g1"), _inputs(gdv="g2"))
        assert change.causes == ["game_data"]
        assert change.comparable is False

    def test_relics_moving_across_a_version_boundary_is_still_news(self):
        """The 2026-08-12 shape: an upload that added relics also crossed the
        v3->v4 Required-row hard constraint, and 11 builds were narrated as
        "weaker" because of the version, not the save.

        The relic movement is real and must still surface; only the -12% is
        withdrawn.
        """
        change = _change()
        apply_causes(
            change, _baseline(base_relics="r1", optimizer="4"),
            _inputs(base_relics="r2", optimizer="5"),
        )
        assert change.causes == ["relics", "game_data"]
        assert change.cause == "mixed"
        assert change.comparable is False
        assert is_narratable(change.causes), "the new relics are still news"

    def test_version_only_crossing_re_baselines_silently(self):
        """Nothing about the user's save moved, so the run is not news — which
        is what lets the caller advance the baseline and clear the stale
        yardstick without ever showing a comparison."""
        change = _change()
        apply_causes(change, _baseline(optimizer="4"), _inputs(optimizer="5"))
        assert not is_narratable(change.causes)

    def test_baseline_written_before_the_version_fields_existed(self):
        """No migration backfills these, so a pre-field baseline reads as a
        version crossing — conservative and correct: we cannot prove the two
        scores share a rule set."""
        change = _change()
        stale = {"layouts": [{"total_score": 100}], "best_score": 100,
                 "inputs": {"base_relics_hash": "r1", "build_hash": "b1"}}
        assert causes_since(stale, _inputs()) == ["game_data"]
        apply_causes(change, stale, _inputs())
        assert change.comparable is False

    def test_staged_purchases_alone_stay_comparable(self):
        change = _change()
        apply_causes(change, _baseline(), _inputs(staged="s1"))
        assert change.causes == ["staged"]
        assert change.comparable is True


class TestPickBaseline:
    """Which of the two baselines a run is measured against.

    The bug this splits apart: dismissing a Relic Rites change folded staged
    purchases into the single baseline, so discarding them and uploading a newer
    save reported relics that were never in any save as "gone from your save".
    """

    SAVE = make_baseline(layouts=[{"total_score": 100}], best_score=100,
                         inputs=_inputs(base_relics="r1"))
    STAGED = make_baseline(layouts=[{"total_score": 180}], best_score=180,
                           inputs=_inputs(base_relics="r1", staged="s1"))

    def test_a_pure_save_run_ignores_the_staged_baseline(self):
        """The upload path. r1 -> r2 is a genuinely newer save; the purchases
        were discarded with the working file and must not be diffed against."""
        assert pick_baseline(
            self.STAGED, self.SAVE, staged=False, base_relics_hash="r2"
        ) is self.SAVE

    def test_a_staged_run_uses_the_staged_baseline(self):
        assert pick_baseline(
            self.STAGED, self.SAVE, staged=True, base_relics_hash="r1"
        ) is self.STAGED

    def test_a_staged_baseline_dies_with_the_save_it_was_built_on(self):
        """Buy, dismiss, discard, upload, then buy again: the first spree's
        relics exist nowhere now, so the second spree is measured from the save
        rather than reporting them lost all over again."""
        assert pick_baseline(
            self.STAGED, self.SAVE, staged=True, base_relics_hash="r2"
        ) is self.SAVE

    def test_an_unstaged_baseline_survives_a_newer_save(self):
        """The composition the sticky baseline exists for — upload without
        reviewing, then go shopping, and both moves land in ONE verdict."""
        assert pick_baseline(
            self.SAVE, self.SAVE, staged=True, base_relics_hash="r2"
        ) is self.SAVE

    def test_no_save_baseline_means_no_comparison(self):
        """Rows the migration would not backfill: only ever acknowledged in a
        staged state, so there is no honest pure-save arrangement to name."""
        assert pick_baseline(
            self.STAGED, None, staged=False, base_relics_hash="r2"
        ) is None

    def test_first_ever_run_has_neither(self):
        assert pick_baseline(
            None, None, staged=False, base_relics_hash="r1") is None
        assert pick_baseline(
            None, None, staged=True, base_relics_hash="r1") is None


class TestIsStagedBaseline:
    def test_reads_the_baselines_own_recorded_inputs(self):
        assert is_staged_baseline(
            make_baseline(layouts=[], best_score=0, inputs=_inputs(staged="s1")))
        assert not is_staged_baseline(
            make_baseline(layouts=[], best_score=0, inputs=_inputs()))

    def test_absent_baseline_is_not_staged(self):
        assert not is_staged_baseline(None)
        assert not is_staged_baseline({})


class TestAdvancedBaselines:
    FRESH = make_baseline(layouts=[{"total_score": 7}], best_score=7,
                          inputs=_inputs())
    PRIOR_SAVE = make_baseline(layouts=[{"total_score": 5}], best_score=5,
                               inputs=_inputs())

    PENDING = make_baseline(layouts=[{"total_score": 6}], best_score=6,
                            inputs=_inputs())

    def test_a_pure_save_advance_moves_both(self):
        assert advanced_baselines(
            self.FRESH, self.PRIOR_SAVE, None, staged=False
        ) == (self.FRESH, self.FRESH, None)

    def test_a_staged_advance_leaves_the_save_track_alone(self):
        """Dismissing a purchase means "I saw what buying that did", never
        "those relics are in my save"."""
        assert advanced_baselines(
            self.FRESH, self.PRIOR_SAVE, None, staged=True
        ) == (self.FRESH, self.PRIOR_SAVE, None)

    def test_a_pure_save_advance_consumes_a_parked_arrangement(self):
        """FRESH is itself a newer pure-save arrangement, so the parked one is
        superseded rather than left to be promoted later."""
        assert advanced_baselines(
            self.FRESH, self.PRIOR_SAVE, self.PENDING, staged=False
        ) == (self.FRESH, self.FRESH, None)

    def test_a_staged_advance_keeps_a_parked_arrangement_waiting(self):
        assert advanced_baselines(
            self.FRESH, self.PRIOR_SAVE, self.PENDING, staged=True
        ) == (self.FRESH, self.PRIOR_SAVE, self.PENDING)


class TestReviewedBaselines:
    """Review is the only place a parked pure-save arrangement can land."""

    FRESH = make_baseline(layouts=[{"total_score": 7}], best_score=7,
                          inputs=_inputs(staged="s1"))
    PRIOR_SAVE = make_baseline(layouts=[{"total_score": 5}], best_score=5,
                               inputs=_inputs())
    PENDING = make_baseline(layouts=[{"total_score": 6}], best_score=6,
                            inputs=_inputs())

    def test_promotes_the_parked_arrangement_on_a_staged_review(self):
        """The bug this fixes: reviewing an upload's change while a Relic Rites
        diff is standing used to advance ONLY the effective track, freezing the
        save track at the save that preceded the first purchase."""
        assert reviewed_baselines(
            self.FRESH, self.PRIOR_SAVE, self.PENDING, staged=True
        ) == (self.FRESH, self.PENDING, None)

    def test_a_staged_review_with_nothing_parked_is_unchanged(self):
        assert reviewed_baselines(
            self.FRESH, self.PRIOR_SAVE, None, staged=True
        ) == (self.FRESH, self.PRIOR_SAVE, None)

    def test_never_puts_a_staged_arrangement_on_the_save_track(self):
        _baseline, save_bl, _pending = reviewed_baselines(
            self.FRESH, self.PRIOR_SAVE, self.PENDING, staged=True
        )
        assert save_bl is not self.FRESH

    def test_a_pure_save_review_prefers_its_own_fresh_arrangement(self):
        """A pure-save review IS the newest save state; the parked one is older
        and merely gets cleared."""
        fresh_pure = make_baseline(layouts=[{"total_score": 7}], best_score=7,
                                   inputs=_inputs())
        assert reviewed_baselines(
            fresh_pure, self.PRIOR_SAVE, self.PENDING, staged=False
        ) == (fresh_pure, fresh_pure, None)
