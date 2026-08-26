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
    apply_causes,
    causes_since,
    is_narratable,
    make_baseline,
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
