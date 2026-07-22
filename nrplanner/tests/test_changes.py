"""Tests for nrplanner.changes — pure save-diff change detection.

Fingerprint/signature/diff logic needs no game data; relevance tests use the
real SourceDataHandler (``ds`` fixture) to resolve effect families.
"""
from nrplanner.changes import (
    build_signature,
    diff_results,
    fingerprint_owned,
    multiset_diff,
    relevant_relics_signature,
    relevant_to_build,
    relic_fingerprint,
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

    def test_build_signature_changes_on_reorder(self):
        # Order matters to the optimizer (leftmost-wins), so a reorder must
        # change the signature → resets the build's change-tracking snapshot.
        b1 = _build(groups=[WeightGroup(weight=10, effects=[10, 20])])
        b2 = _build(groups=[WeightGroup(weight=10, effects=[20, 10])])
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

    def test_wanted_effect_in_curse_slot_is_relevant(self, ds, all_effects):
        # Curses resolve through the same positive chain in the scorer, so a
        # relic carrying the wanted effect as a CURSE must count as relevant.
        eff = all_effects[0]["id"]
        build = _build(groups=[WeightGroup(weight=10, effects=[eff])])
        added = [fingerprint_owned(_relic(100, [], curses=[eff]))]
        ra, _ = relevant_to_build(build, added, [], ds)
        assert ra == 1

    def test_positive_default_curse_weight_makes_cursed_relics_relevant(self, ds):
        build = _build(default_curse_weight=5)
        cursed = [relic_fingerprint(100, [999999999], [999999998])]
        clean = [relic_fingerprint(100, [999999999], [])]
        assert relevant_to_build(build, cursed, [], ds) == (1, 0)
        assert relevant_to_build(build, clean, [], ds) == (0, 0)

    def test_name_alias_is_relevant(self, ds, all_effects):
        # Many game effects share a display name with unrelated IDs/text_ids;
        # the scorer matches those by name, so relevance must too.
        by_name: dict[str, list[int]] = {}
        for e in all_effects:
            name = e.get("name")
            if name and name != "Empty" and not name.startswith("Effect "):
                by_name.setdefault(name, []).append(e["id"])
        pair = None
        for _name, ids in by_name.items():
            for listed in ids:
                for carried in ids:
                    if carried == listed:
                        continue
                    tid = ds.get_effect_text_id(carried)
                    if carried != listed and tid != listed:
                        pair = (listed, carried)
                        break
                if pair:
                    break
            if pair:
                break
        assert pair is not None, "game data has no name-aliased effect pair"
        listed, carried = pair
        build = _build(groups=[WeightGroup(weight=10, effects=[listed])])
        added = [fingerprint_owned(_relic(100, [carried]))]
        ra, _ = relevant_to_build(build, added, [], ds)
        assert ra == 1


class TestRelevantRelicsSignature:
    @staticmethod
    def _pairs(relics: list[OwnedRelic]) -> list[tuple]:
        return [(fingerprint_owned(r), r.ga_handle) for r in relics]

    def test_irrelevant_churn_keeps_signature(self, ds, all_effects):
        eff = all_effects[0]["id"]
        build = _build(required_effects=[eff])
        keeper = _relic(100, [eff])
        junk = _relic(200, [999999999])
        base = relevant_relics_signature(build, self._pairs([keeper]), ds)
        with_junk = relevant_relics_signature(build, self._pairs([keeper, junk]), ds)
        assert base == with_junk

    def test_relevant_add_changes_signature(self, ds, all_effects):
        eff = all_effects[0]["id"]
        build = _build(required_effects=[eff])
        keeper = _relic(100, [eff])
        second = _relic(300, [eff])
        base = relevant_relics_signature(build, self._pairs([keeper]), ds)
        grown = relevant_relics_signature(build, self._pairs([keeper, second]), ds)
        assert base != grown

    def test_pinned_relic_included_despite_zero_score(self, ds, all_effects):
        eff = all_effects[0]["id"]
        junk = _relic(200, [999999999])
        pinned_build = _build(required_effects=[eff],
                              pinned_relics=[junk.ga_handle])
        plain_build = _build(required_effects=[eff])
        with_junk = self._pairs([junk])
        # Pinned: the junk relic is part of the optimization input.
        assert (relevant_relics_signature(pinned_build, with_junk, ds)
                != relevant_relics_signature(pinned_build, [], ds))
        # Not pinned: the same junk relic is invisible.
        assert (relevant_relics_signature(plain_build, with_junk, ds)
                == relevant_relics_signature(plain_build, [], ds))

    def test_order_independent(self, ds, all_effects):
        eff = all_effects[0]["id"]
        build = _build(required_effects=[eff])
        a, b = _relic(100, [eff]), _relic(300, [eff])
        assert (relevant_relics_signature(build, self._pairs([a, b]), ds)
                == relevant_relics_signature(build, self._pairs([b, a]), ds))


class TestDiffResults:
    def test_new_when_no_prior(self):
        change = diff_results(None, [_vessel([_relic(1, [10])], 50)])
        assert change.status == "new"
        assert change.best_after == 50
        # First run: no prior baseline, so nothing is flagged as newly entered.
        assert change.entered == []

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
