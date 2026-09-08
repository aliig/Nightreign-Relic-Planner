"""Tests for nrplanner.changes — pure save-diff change detection.

Fingerprint/signature/diff logic needs no game data; relevance tests use the
real SourceDataHandler (``ds`` fixture) to resolve effect families.
"""
from nrplanner.changes import (
    _fp_is_relevant,
    build_positive_sets,
    build_signature,
    diff_results,
    fingerprint_owned,
    fp_matches,
    layout_match_key,
    mark_staged_refs,
    multiset_diff,
    relevance_index,
    relevant_relics_signature,
    relevant_to_build,
    relic_fingerprint,
    relics_signature,
    result_match_key,
    serialize_match_keys,
    serialize_match_ranks,
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


class TestRelevanceIndexParity:
    """fp_matches must answer exactly what _fp_is_relevant answers.

    The indexed form duplicates the relevance rule for speed (see fp_matches),
    so like vessel_accepts it needs a test pinning the two together — over real
    game data, across builds shaped differently enough to exercise every branch
    of the resolution chain (direct id, text_id, display name, family, curse).
    """

    def _fingerprints(self, all_effects) -> list:
        # A spread of real effect ids over the effect and curse slots, plus one
        # id the game data does not know (no name/family/text_id at all).
        ids = [e["id"] for e in all_effects]
        step = max(1, len(ids) // 60)
        sample = ids[::step][:60]
        fps = [relic_fingerprint(100 + i, [e], []) for i, e in enumerate(sample)]
        fps += [relic_fingerprint(200 + i, [], [e]) for i, e in enumerate(sample[:20])]
        fps.append(relic_fingerprint(300, [999999999], []))
        fps.append(relic_fingerprint(301, [999999999], [999999998]))
        return fps

    def _builds(self, ds, all_effects) -> list:
        ids = [e["id"] for e in all_effects]
        families = sorted({
            f for f in (ds.get_effect_family(e) for e in ids[:400]) if f
        })[:3]
        return [
            _build(required_effects=[ids[0]]),
            _build(groups=[WeightGroup(weight=10, effects=ids[5:15])]),
            _build(groups=[WeightGroup(weight=-5, effects=ids[20:25])]),
            _build(groups=[WeightGroup(weight=10, families=families)]),
            _build(required_families=families[:1], default_curse_weight=3),
            _build(),  # wants nothing at all
        ]

    def test_matches_the_scalar_rule(self, ds, all_effects):
        fps = self._fingerprints(all_effects)
        index = relevance_index(fps, ds)
        assert len(index) == len(set(fps))
        checked = 0
        relevant = 0
        for build in self._builds(ds, all_effects):
            pos_ids, pos_fams, pos_names = build_positive_sets(build, ds)
            curses_rel = build.default_curse_weight > 0
            for fp in fps:
                expected = _fp_is_relevant(
                    fp, pos_ids, pos_fams, pos_names, ds, curses_rel)
                actual = fp_matches(
                    index[fp], pos_ids, pos_fams, pos_names, curses_rel)
                assert actual == expected, (build.required_effects, fp)
                checked += 1
                relevant += expected
        # Guard against a vacuous pass: the sample must actually hit the
        # relevant branches, not agree on "no" everywhere.
        assert checked > 0 and relevant > 10


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

    def test_stays_version_blind(self):
        """``diff_results`` sees layouts, never an input signature, so it can
        never decide the two scores share a rule set — it says "comparable" and
        the backend (app.core.snapshot_baseline.apply_causes) overrides that
        when the baseline's optimizer/game-data version differs.  Keeping the
        knowledge in one place is why this function takes no version."""
        old = serialize_top_layouts([_vessel([_relic(1, [10])], 50)])
        change = diff_results(old, [_vessel([_relic(2, [10, 20])], 70)])
        assert change.comparable is True

    def test_left_refs_carry_display_metadata(self):
        gone = _relic(1, [10], name="Stalwart Horn", color="Blue")
        gone = gone.model_copy(update={"tier": "Grand", "is_deep": True})
        old = serialize_top_layouts([_vessel([gone], 70)])
        change = diff_results(old, [_vessel([_relic(2, [10])], 50)])
        ref = next(r for r in change.left if r.real_id == 1)
        assert (ref.name, ref.color, ref.tier, ref.is_deep) == (
            "Stalwart Horn", "Blue", "Grand", True)


class TestStillOwned:
    """`left` is a layout diff, not an inventory diff — a departed relic may or
    may not still be in the save, and only `owned` can tell the two apart."""

    def test_unknown_without_inventory(self):
        old = serialize_top_layouts([_vessel([_relic(1, [10])], 70)])
        change = diff_results(old, [_vessel([_relic(2, [10])], 50)])
        assert [r.still_owned for r in change.left] == [None]

    def test_still_owned_when_relic_remains_in_save(self):
        benched, used = _relic(1, [10]), _relic(2, [10])
        old = serialize_top_layouts([_vessel([benched], 70)])
        change = diff_results(old, [_vessel([used], 50)], owned=[benched, used])
        assert [(r.real_id, r.still_owned) for r in change.left] == [(1, True)]

    def test_not_owned_when_relic_left_the_save(self):
        sold, used = _relic(1, [10]), _relic(2, [10])
        old = serialize_top_layouts([_vessel([sold], 70)])
        change = diff_results(old, [_vessel([used], 50)], owned=[used])
        assert [(r.real_id, r.still_owned) for r in change.left] == [(1, False)]

    def test_duplicate_copies_accounted_per_copy(self):
        # Owned two identical copies, used both; one was sold and the other is
        # merely benched, so exactly one ref may claim to still be in the save.
        dupe = _relic(1, [10])
        old = serialize_top_layouts([_vessel([dupe, dupe], 70)])
        change = diff_results(old, [_vessel([_relic(2, [10])], 50)], owned=[dupe])
        assert sorted(r.still_owned for r in change.left) == [False, True]

    def test_copy_still_in_use_is_not_double_counted(self):
        # One copy stayed in the layout; the other has no spare to claim.
        dupe = _relic(1, [10])
        old = serialize_top_layouts([_vessel([dupe, dupe], 70)])
        change = diff_results(old, [_vessel([dupe], 50)], owned=[dupe])
        assert [r.still_owned for r in change.left] == [False]

    def test_unfillable_vessel_marks_survivors(self):
        kept, sold = _relic(1, [10]), _relic(2, [10])
        old = serialize_top_layouts([_vessel([kept, sold], 70)])
        change = diff_results(old, [], owned=[kept])
        assert {(r.real_id, r.still_owned) for r in change.left} == {
            (1, True), (2, False)}


class TestMatchKeys:
    """Result identity used to recognise an in-game loadout as "result #N"."""

    def test_slot_order_does_not_change_identity(self):
        a, b = _relic(1, [10]), _relic(2, [20])
        assert result_match_key(_vessel([a, b], 50)) == result_match_key(
            _vessel([b, a], 50))

    def test_duplicate_copies_are_interchangeable(self):
        # Two physical copies of the same relic: swapping which copy sits in
        # which slot is not a different setup.
        copy_a, copy_b = _relic(1, [10]), _relic(1, [10])
        copy_b.ga_handle = copy_a.ga_handle + 1
        assert result_match_key(_vessel([copy_a, _relic(2, [20])], 50)) == (
            result_match_key(_vessel([copy_b, _relic(2, [20])], 50)))

    def test_score_and_vessel_name_do_not_affect_identity(self):
        # Only the vessel and its relics identify a setup — a rescored result
        # for the same arrangement must still match a saved loadout.
        a = _relic(1, [10])
        assert result_match_key(_vessel([a], 50)) == result_match_key(
            _vessel([a], 999))

    def test_different_vessel_is_a_different_setup(self):
        a = _relic(1, [10])
        assert result_match_key(_vessel([a], 50, vessel_id=1)) != (
            result_match_key(_vessel([a], 50, vessel_id=2)))

    def test_different_relics_are_different_setups(self):
        assert result_match_key(_vessel([_relic(1, [10])], 50)) != (
            result_match_key(_vessel([_relic(1, [11])], 50)))

    def test_empty_slots_are_ignored(self):
        # A 3-slot result holding one relic and a saved preset with the same
        # relic plus two empty slots are the same setup.
        a = _relic(1, [10])
        assert result_match_key(_vessel([a], 50)) == layout_match_key(
            1, [fingerprint_owned(a)])

    def test_serialize_keeps_display_order_not_score_order(self):
        # The rank a user sees is the results' own order (covering-first), so
        # the keys must NOT be re-sorted by score the way top_layouts is.
        low, high = _vessel([_relic(1, [10])], 10), _vessel([_relic(2, [20])], 90)
        keys = serialize_match_keys([low, high])
        assert keys == [result_match_key(low), result_match_key(high)]

    def test_ranks_share_a_position_across_a_score_tie(self):
        # The bug this exists for: three equally scoring results sit at list
        # positions 1/2/3 in whatever order the search produced them, so saving
        # the third was reported as "#3" — a joint-best pick shown as beaten.
        tied = [_vessel([_relic(i, [10])], 50, vessel_id=i) for i in (1, 2, 3)]
        worse = _vessel([_relic(4, [10])], 10, vessel_id=4)
        assert serialize_match_ranks([*tied, worse]) == [1, 1, 1, 4]

    def test_ranks_follow_distinct_scores(self):
        high, mid, low = (
            _vessel([_relic(i, [10])], score, vessel_id=i)
            for i, score in ((1, 90), (2, 50), (3, 10))
        )
        assert serialize_match_ranks([high, mid, low]) == [1, 2, 3]

    def test_covering_outranks_a_higher_scoring_miss(self):
        # Same tiering the optimizer sorts by: a result that misses a Required
        # entry never ties with one that covers it, whatever it scores.
        covering = _vessel([_relic(1, [10])], 10)
        missing = _vessel([_relic(2, [20])], 90, vessel_id=2)
        missing.meets_requirements = False
        assert serialize_match_ranks([covering, missing]) == [1, 2]

    def test_ranks_line_up_with_the_keys_they_annotate(self):
        results = [_vessel([_relic(i, [10])], 50, vessel_id=i) for i in (1, 2)]
        assert len(serialize_match_ranks(results)) == len(
            serialize_match_keys(results))

    def test_key_matches_a_loadout_built_from_handles(self):
        # The shape the endpoint uses: relic fingerprints resolved from a
        # preset's ga_handles produce the same key as the result itself.
        a, b = _relic(1, [10]), _relic(2, [20, 21])
        result = _vessel([a, b], 50)
        by_handle = {r.ga_handle: fingerprint_owned(r) for r in (a, b)}
        preset_handles = [b.ga_handle, 0, a.ga_handle]
        key = layout_match_key(
            1, [by_handle[h] for h in preset_handles if h != 0])
        assert key == result_match_key(result)


class TestMarkStagedRefs:
    """Relics bought in Relic Rites are owned but not in the save file yet, so
    a change list has to name which half is which."""

    def _change(self):
        from nrplanner.models import BuildChange, RelicRef

        return BuildChange(
            status="improved",
            entered=[
                RelicRef(real_id=10, name="Bought", effects=[1, EMPTY, EMPTY],
                         curses=[EMPTY, EMPTY, EMPTY]),
                RelicRef(real_id=20, name="From the save",
                         effects=[2, EMPTY, EMPTY],
                         curses=[EMPTY, EMPTY, EMPTY]),
            ],
        )

    def test_flags_only_the_staged_relic(self):
        change = self._change()
        mark_staged_refs(change, [relic_fingerprint(10, [1], [])])
        assert [r.staged for r in change.entered] == [True, False]

    def test_matches_by_content_not_identity(self):
        """Mints carry synthetic handles and the game renumbers real ones, so
        the match has to be on content — a different copy of the same relic
        content is the same relic for this purpose."""
        change = self._change()
        mark_staged_refs(change, [relic_fingerprint(10, [1, EMPTY, EMPTY], [])])
        assert change.entered[0].staged is True

    def test_a_different_effect_is_a_different_relic(self):
        change = self._change()
        mark_staged_refs(change, [relic_fingerprint(10, [999], [])])
        assert [r.staged for r in change.entered] == [False, False]

    def test_empty_staged_list_touches_nothing(self):
        change = self._change()
        mark_staged_refs(change, [])
        assert [r.staged for r in change.entered] == [False, False]

    def test_covers_departed_and_pinned_relics_too(self):
        """A relic can be bought and then dropped by the optimizer, or be a
        pin that a purchase displaced — both still need the flag."""
        from nrplanner.models import BuildChange, RelicRef

        ref = RelicRef(real_id=10, name="Bought", effects=[1, EMPTY, EMPTY],
                       curses=[EMPTY, EMPTY, EMPTY])
        change = BuildChange(status="degraded", left=[ref],
                             pinned_removed=[ref.model_copy()])
        mark_staged_refs(change, [relic_fingerprint(10, [1], [])])
        assert change.left[0].staged is True
        assert change.pinned_removed[0].staged is True
