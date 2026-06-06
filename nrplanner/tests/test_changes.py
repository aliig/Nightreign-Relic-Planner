"""Tests for nrplanner.changes — pure save-diff change detection.

Fingerprint/signature/diff logic needs no game data; relevance tests use the
real SourceDataHandler (``ds`` fixture) to resolve effect families.
"""
from nrplanner.changes import (
    build_signature,
    diff_results,
    fingerprint_owned,
    multiset_diff,
    relic_fingerprint,
    relevant_to_build,
    relics_signature,
    serialize_top_layouts,
)
from nrplanner.models import (
    BuildDefinition,
    OwnedRelic,
    SlotAssignment,
    VesselResult,
    WeightGroup,
)

EMPTY = 4294967295


def _relic(real_id: int, effects: list[int], curses: list[int] | None = None,
           name: str = "R", color: str = "Red") -> OwnedRelic:
    effects = (effects + [EMPTY, EMPTY, EMPTY])[:3]
    curses = ((curses or []) + [EMPTY, EMPTY, EMPTY])[:3]
    return OwnedRelic(
        ga_handle=0xC0000000 + real_id,
        item_id=real_id + 2147483648,
        real_id=real_id,
        color=color,
        effects=effects,
        curses=curses,
        is_deep=False,
        name=name,
        tier="Delicate",
    )


def _vessel(relics: list[OwnedRelic], score: int, *, truncated: bool = False,
            vessel_id: int = 1) -> VesselResult:
    assignments = [
        SlotAssignment(slot_index=i, slot_color="Red", is_deep=False,
                       relic=r, score=0, breakdown=[])
        for i, r in enumerate(relics)
    ]
    return VesselResult(
        vessel_id=vessel_id, vessel_name="V", vessel_character="Wylder",
        unlock_flag=0, slot_colors=("Red",) * len(relics),
        assignments=assignments, total_score=score, search_truncated=truncated,
    )


def _build(**kw) -> BuildDefinition:
    return BuildDefinition(id="b", name="B", character="Wylder", **kw)


class TestFingerprint:
    def test_matches_manual_tuple(self):
        r = _relic(100, [10, 20, EMPTY], [5])
        assert fingerprint_owned(r) == (100, 10, 20, EMPTY, 5, EMPTY, EMPTY)

    def test_pads_short_inputs(self):
        assert relic_fingerprint(7, [1], []) == (7, 1, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY)


class TestSignatures:
    def test_relics_signature_order_independent(self):
        a, b = _relic(1, [10]), _relic(2, [20])
        assert relics_signature([a, b]) == relics_signature([b, a])

    def test_relics_signature_sensitive_to_membership(self):
        a, b = _relic(1, [10]), _relic(2, [20])
        assert relics_signature([a, b]) != relics_signature([a])

    def test_relics_signature_counts_duplicates(self):
        a = _relic(1, [10])
        assert relics_signature([a]) != relics_signature([a, _relic(1, [10])])

    def test_build_signature_ignores_name_and_id(self):
        b1 = _build(required_effects=[10])
        b2 = BuildDefinition(id="other", name="Renamed", character="Wylder",
                             required_effects=[10])
        assert build_signature(b1) == build_signature(b2)

    def test_build_signature_changes_on_weight(self):
        b1 = _build(groups=[WeightGroup(weight=10, effects=[10])])
        b2 = _build(groups=[WeightGroup(weight=20, effects=[10])])
        assert build_signature(b1) != build_signature(b2)


class TestMultisetDiff:
    def test_added_and_removed(self):
        added, removed = multiset_diff([(1,), (2,)], [(2,), (3,)])
        assert added == [(3,)] and removed == [(1,)]

    def test_duplicates_counted(self):
        added, removed = multiset_diff([(1,)], [(1,), (1,)])
        assert added == [(1,)] and removed == []


class TestRelevance:
    def test_added_relevant_when_shares_required_effect(self, ds, all_effects):
        eff = all_effects[0]["id"]
        build = _build(required_effects=[eff])
        added = [fingerprint_owned(_relic(100, [eff]))]
        ra, _ = relevant_to_build(build, added, [], ds)
        assert ra == 1

    def test_irrelevant_when_no_overlap(self, ds, all_effects):
        eff = all_effects[0]["id"]
        build = _build(required_effects=[eff])
        # 999999999 is not a real effect id → no family, no text id.
        added = [relic_fingerprint(100, [999999999], [])]
        ra, _ = relevant_to_build(build, added, [], ds)
        assert ra == 0


class TestDiffResults:
    def test_new_when_no_prior(self):
        change = diff_results(None, [_vessel([_relic(1, [10])], 50)])
        assert change.status == "new"
        assert change.best_after == 50
        assert len(change.entered) == 1

    def test_unchanged_identical(self):
        old = serialize_top_layouts([_vessel([_relic(1, [10])], 50)])
        change = diff_results(old, [_vessel([_relic(1, [10])], 50)])
        assert change.status == "unchanged"
        assert change.delta == 0
        assert change.entered == [] and change.left == []

    def test_improved_with_new_relic(self):
        old = serialize_top_layouts([_vessel([_relic(1, [10])], 50)])
        change = diff_results(old, [_vessel([_relic(2, [10, 20])], 70)])
        assert change.status == "improved"
        assert change.delta == 20
        assert any(r.real_id == 2 for r in change.entered)
        assert any(r.real_id == 1 for r in change.left)
        assert change.reliable is True

    def test_degraded_when_lower(self):
        old = serialize_top_layouts([_vessel([_relic(2, [10, 20])], 70)])
        change = diff_results(old, [_vessel([_relic(1, [10])], 50)])
        assert change.status == "degraded"
        assert change.delta == -20

    def test_reordered_same_score_different_relic(self):
        old = serialize_top_layouts([_vessel([_relic(1, [10])], 50)])
        change = diff_results(old, [_vessel([_relic(2, [10])], 50)])
        assert change.status == "reordered"

    def test_truncated_improvement_unreliable(self):
        old = serialize_top_layouts([_vessel([_relic(1, [10])], 50)])
        change = diff_results(old, [_vessel([_relic(2, [10, 20])], 70, truncated=True)])
        assert change.status == "improved"
        assert change.reliable is False
