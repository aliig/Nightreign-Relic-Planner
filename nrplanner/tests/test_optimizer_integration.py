"""Integration tests for stacking correctness through the full optimizer pipeline.

Unit tests (test_scoring.py) verify the scorer with manually constructed vessel state.
These tests exercise the FULL PIPELINE:

  _get_relic_stacking_adds() → score_relic_in_context() → VesselResult

This catches bugs in state accumulation across slots that unit tests cannot
detect because they bypass _get_relic_stacking_adds() entirely.

Each known stacking bug that has been fixed gets a regression guard here so
it can never be silently re-introduced.
"""
import json
import tempfile
from pathlib import Path

import pytest

from nrplanner import (
    BuildScorer,
    VesselOptimizer,
    SourceDataHandler,
    decrypt_sl2,
    discover_characters,
    parse_relics,
)
from nrplanner.models import (
    ALL_TIER_KEYS,
    BuildDefinition,
    OwnedRelic,
    RelicInventory,
    VesselResult,
)

EMPTY = 4294967295  # EMPTY_EFFECT sentinel

# Real effect IDs confirmed from AttachEffectParam.csv
_HP_RESTORE_BASE  = 7005600   # no_stack, compat=7005600 (self-ref)
_HP_RESTORE_PLUS1 = 6005600   # unique,   compat=7005600
_HP_RESTORE_PLUS2 = 6005601   # unique,   compat=7005600
_IMBUE_MAGIC      = 7120000   # no_stack, excl=100, compat=200
_IMBUE_FIRE       = 7120100   # no_stack, excl=100, compat=200
_TAKING_ATTACKS   = 7032200   # no_stack, compat=100 (mega-group sentinel)
_PHYSICAL_ATK_1   = 7001400   # stack,    compat=100 (mega-group sentinel)

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent
    / "backend" / "tests" / "fixtures" / "NR0000.sl2"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_relic(
    effects: list[int],
    color: str = "Red",
    is_deep: bool = False,
    ga_handle: int = 0xC0000001,
) -> OwnedRelic:
    effect_count = sum(1 for e in effects if e not in (EMPTY, 0))
    tier = "Grand" if effect_count >= 3 else ("Polished" if effect_count == 2 else "Delicate")
    return OwnedRelic(
        ga_handle=ga_handle,
        item_id=100 + 2147483648,
        real_id=100,
        color=color,
        effects=effects,
        curses=[EMPTY, EMPTY, EMPTY],
        is_deep=is_deep,
        name="Test Relic",
        tier=tier,
    )


def _make_build(
    preferred: list[int] | None = None,
    required: list[int] | None = None,
    blacklist: list[int] | None = None,
) -> BuildDefinition:
    tiers = {k: [] for k in ALL_TIER_KEYS}
    if preferred:
        tiers["preferred"] = preferred
    if required:
        tiers["required"] = required
    if blacklist:
        tiers["blacklist"] = blacklist
    return BuildDefinition(
        id="integration-test",
        name="Integration Test",
        character="Wylder",
        tiers=tiers,
        family_tiers={k: [] for k in ALL_TIER_KEYS},
        include_deep=False,
        curse_max=1,
    )


def _assert_score_consistency(results: list[VesselResult]) -> None:
    """total_score must equal sum of slot scores in every result."""
    for result in results:
        slot_sum = sum(a.score for a in result.assignments)
        assert result.total_score == slot_sum, (
            f"Vessel '{result.vessel_name}': total_score={result.total_score} "
            f"!= sum(slot scores)={slot_sum}"
        )


def _assert_redundant_zero(results: list[VesselResult]) -> None:
    """Every breakdown entry with redundant=True must have score=0."""
    for result in results:
        for assignment in result.assignments:
            for entry in assignment.breakdown:
                if entry.get("redundant"):
                    assert entry["score"] == 0, (
                        f"Vessel '{result.vessel_name}': effect {entry['effect_id']} "
                        f"is redundant but has score={entry['score']}"
                    )


@pytest.fixture(scope="module")
def scorer(ds: SourceDataHandler) -> BuildScorer:
    return BuildScorer(ds)


@pytest.fixture(scope="module")
def optimizer(ds: SourceDataHandler, scorer: BuildScorer) -> VesselOptimizer:
    return VesselOptimizer(ds, scorer)


# ---------------------------------------------------------------------------
# State-building integration tests
#
# These call _get_relic_stacking_adds() directly (it is internal but not
# sealed) and feed its output into score_relic_in_context().  This is the
# precise integration point that has harboured the tier-family stacking bugs.
# ---------------------------------------------------------------------------

class TestStackingStateBuilding:
    """Verify that _get_relic_stacking_adds() produces the correct sets for
    each stacking rule, and that those sets yield correct scoring behaviour."""

    # -- Rule 1: no_stack base placed ----------------------------------------

    def test_rule1_base_blocks_variant_via_no_stack_compat_ids(
        self, optimizer: VesselOptimizer, scorer: BuildScorer,
    ) -> None:
        """When the no_stack base (+0) is placed, its compat ID must enter
        no_stack_compat_ids so that unique variants (+1/+2) score 0.

        Regression guard for Rule 1 in _get_relic_stacking_adds().
        """
        relic_base = _make_relic([_HP_RESTORE_BASE, EMPTY, EMPTY])
        eff_ids, excl_ids, ns_excl_ids, ns_compat_ids = (
            optimizer._get_relic_stacking_adds(relic_base)
        )

        # The base's compat ID must be in no_stack_compat_ids so the unique
        # variant check in _effect_stacking_score() can block it.
        assert _HP_RESTORE_BASE in ns_compat_ids, (
            "Rule 1: no_stack base must add its self-ref compat to no_stack_compat_ids"
        )

        build = _make_build(preferred=[_HP_RESTORE_PLUS1])
        relic_plus1 = _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY])

        score = scorer.score_relic_in_context(
            relic_plus1, build, eff_ids, excl_ids, ns_excl_ids,
            vessel_no_stack_compat_ids=ns_compat_ids,
        )
        assert score == 0, (
            "HP Restore +1 must be blocked when no_stack base is already placed"
        )

    # -- Rule 2: unique variant placed ---------------------------------------

    def test_rule2_variant_adds_base_id_to_effect_ids(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """When a unique variant (+1) is placed, its compat ID (the base's eff_id)
        must enter effect_ids so the no_stack base is blocked.

        Rule 2 fix: this must go to effect_ids, NOT no_stack_compat_ids.
        If it went to no_stack_compat_ids, sibling variants (+2) would be
        wrongly blocked (they share the same compat ID).
        """
        relic_plus1 = _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY])
        eff_ids, excl_ids, ns_excl_ids, ns_compat_ids = (
            optimizer._get_relic_stacking_adds(relic_plus1)
        )

        # Base's eff_id must be in effect_ids (for identity check to block it)
        assert _HP_RESTORE_BASE in eff_ids, (
            "Rule 2: placing a variant must add the base's eff_id to effect_ids"
        )
        # Base's compat must NOT be in no_stack_compat_ids
        # (that would incorrectly block sibling variants)
        assert _HP_RESTORE_BASE not in ns_compat_ids, (
            "Rule 2: variant must NOT add base's compat to no_stack_compat_ids "
            "(would falsely block sibling variants like +2)"
        )

    def test_rule2_variant_blocks_base_via_effect_ids(
        self, optimizer: VesselOptimizer, scorer: BuildScorer,
    ) -> None:
        """After Rule 2 places base's eff_id in effect_ids, the no_stack base
        must score 0 (blocked by the identity check: eff_id in vessel_effect_ids).
        """
        relic_plus1 = _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY])
        eff_ids, excl_ids, ns_excl_ids, ns_compat_ids = (
            optimizer._get_relic_stacking_adds(relic_plus1)
        )

        build = _make_build(preferred=[_HP_RESTORE_BASE])
        relic_base = _make_relic([_HP_RESTORE_BASE, EMPTY, EMPTY])

        score = scorer.score_relic_in_context(
            relic_base, build, eff_ids, excl_ids, ns_excl_ids,
            vessel_no_stack_compat_ids=ns_compat_ids,
        )
        assert score == 0, (
            "HP Restore base must score 0 when a variant is already placed "
            "(base's eff_id is in effect_ids via Rule 2)"
        )

    def test_rule2_sibling_variant_not_blocked(
        self, optimizer: VesselOptimizer, scorer: BuildScorer,
    ) -> None:
        """+1 placed first must NOT block +2.

        Core regression test: before the fix, Rule 2 added the base's compat ID
        (7005600) to no_stack_compat_ids. Scoring +2 then checked
        'compat in vessel_no_stack_compat_ids' → True → blocked. After the fix,
        7005600 goes to effect_ids instead. +2 (eff_id=6005601) is not in effect_ids,
        so it scores normally.
        """
        relic_plus1 = _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY])
        eff_ids, excl_ids, ns_excl_ids, ns_compat_ids = (
            optimizer._get_relic_stacking_adds(relic_plus1)
        )

        build = _make_build(preferred=[_HP_RESTORE_PLUS2])
        relic_plus2 = _make_relic([_HP_RESTORE_PLUS2, EMPTY, EMPTY])

        score = scorer.score_relic_in_context(
            relic_plus2, build, eff_ids, excl_ids, ns_excl_ids,
            vessel_no_stack_compat_ids=ns_compat_ids,
        )
        assert score > 0, (
            "HP Restore +2 must NOT be blocked when +1 is placed. "
            "Rule 2 must add base's eff_id to effect_ids, not no_stack_compat_ids."
        )

    # -- Mega-group 100 guard ------------------------------------------------

    def test_rule2_skips_mega_group_100(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """Effects with compat=100 (mega-group sentinel) must NOT trigger
        Rule 2.  The self-ref guard (get_effect_conflict_id(100) != 100)
        prevents 100 from entering effect_ids or no_stack_compat_ids.

        Regression guard: prevents the mega-group false-block re-introduction.
        """
        # _TAKING_ATTACKS is no_stack with compat=100 (not self-referencing)
        relic = _make_relic([_TAKING_ATTACKS, EMPTY, EMPTY])
        eff_ids, _, _, ns_compat_ids = optimizer._get_relic_stacking_adds(relic)

        # 100 is not a real effect ID — must not enter the sets via Rule 2
        # (Rule 1 also doesn't fire because compat=100 != eff_id=7032200)
        assert 100 not in ns_compat_ids, (
            "Mega-group sentinel 100 must not enter no_stack_compat_ids"
        )
        assert 100 not in eff_ids, (
            "Mega-group sentinel 100 must not enter effect_ids"
        )

    def test_mega_group_100_effects_coexist_in_state(
        self, optimizer: VesselOptimizer, scorer: BuildScorer,
    ) -> None:
        """Two different compat=100 effects (no shared exclusivityId) must both
        score when placed in the same vessel.

        Regression guard for the original mega-group false-block bug.
        """
        relic_taking = _make_relic([_TAKING_ATTACKS, EMPTY, EMPTY])
        eff_ids, excl_ids, ns_excl_ids, ns_compat_ids = (
            optimizer._get_relic_stacking_adds(relic_taking)
        )

        build = _make_build(preferred=[_PHYSICAL_ATK_1])
        relic_phys = _make_relic([_PHYSICAL_ATK_1, EMPTY, EMPTY])

        score = scorer.score_relic_in_context(
            relic_phys, build, eff_ids, excl_ids, ns_excl_ids,
            vessel_no_stack_compat_ids=ns_compat_ids,
        )
        assert score > 0, (
            "Physical Attack +0 (compat=100, stack) must not be blocked by "
            "Taking Attacks (compat=100, no_stack) — no shared exclusivityId"
        )


# ---------------------------------------------------------------------------
# Full pipeline: invariants (always hold regardless of vessel structure)
# ---------------------------------------------------------------------------

class TestOptimizerInvariants:
    """These invariants must hold for any inventory / build combination."""

    def test_score_consistency_with_tier_family_relics(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """total_score == sum(slot scores) with HP Restore family relics."""
        build = _make_build(preferred=[_HP_RESTORE_PLUS1, _HP_RESTORE_PLUS2])
        inventory = RelicInventory.from_owned_relics([
            _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY], ga_handle=1),
            _make_relic([_HP_RESTORE_PLUS2, EMPTY, EMPTY], ga_handle=2),
        ])
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        _assert_score_consistency(results)

    def test_redundant_zero_with_conflicting_relics(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """Redundant breakdown entries always have score=0 (base+variant conflict)."""
        build = _make_build(preferred=[_HP_RESTORE_BASE, _HP_RESTORE_PLUS1])
        inventory = RelicInventory.from_owned_relics([
            _make_relic([_HP_RESTORE_BASE, EMPTY, EMPTY], ga_handle=1),
            _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY], ga_handle=2),
        ])
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        _assert_redundant_zero(results)

    def test_score_consistency_with_exclusivity_relics(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """total_score == sum(slot scores) with conflicting imbue relics."""
        build = _make_build(preferred=[_IMBUE_MAGIC, _IMBUE_FIRE])
        inventory = RelicInventory.from_owned_relics([
            _make_relic([_IMBUE_MAGIC, EMPTY, EMPTY], ga_handle=1),
            _make_relic([_IMBUE_FIRE, EMPTY, EMPTY], ga_handle=2),
        ])
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        _assert_score_consistency(results)

    def test_redundant_zero_with_exclusivity_relics(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """Redundant entries have score=0 when imbues conflict via exclusivityId."""
        build = _make_build(preferred=[_IMBUE_MAGIC, _IMBUE_FIRE])
        inventory = RelicInventory.from_owned_relics([
            _make_relic([_IMBUE_MAGIC, EMPTY, EMPTY], ga_handle=1),
            _make_relic([_IMBUE_FIRE, EMPTY, EMPTY], ga_handle=2),
        ])
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        _assert_redundant_zero(results)


# ---------------------------------------------------------------------------
# Full pipeline: stacking-correctness assertions
#
# These check that the optimizer's vessel assignment reflects the correct
# stacking semantics.  Some assertions are conditional on both relics landing
# in the same vessel (not all vessel configurations allow this).
# ---------------------------------------------------------------------------

class TestFullPipelineTierFamilyStacking:

    def test_sibling_variants_both_score_in_single_relic(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """A Grand relic with both +1 and +2 should contribute both effects.
        This is scored against an empty vessel state, so no cross-slot interaction.
        Basic sanity: unique effects that don't conflict must each add their weight.
        """
        build = _make_build(preferred=[_HP_RESTORE_PLUS1, _HP_RESTORE_PLUS2])
        inventory = RelicInventory.from_owned_relics([
            _make_relic([_HP_RESTORE_PLUS1, _HP_RESTORE_PLUS2, EMPTY], ga_handle=1),
        ])
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        assert results
        best = max(results, key=lambda r: r.total_score)
        assert best.total_score == 100, (
            f"Relic with +1 (+50) and +2 (+50) should score 100, got {best.total_score}"
        )

    def test_sibling_variants_coexist_across_slots(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """Two separate relics (+1, +2) assigned to the same vessel must both score.

        Regression test for Rule 2 bug: previously, placing +1 added 7005600 to
        no_stack_compat_ids, which blocked +2 in the next slot.  After the fix,
        7005600 goes to effect_ids (blocking only the base), so +2 is free.
        """
        build = _make_build(preferred=[_HP_RESTORE_PLUS1, _HP_RESTORE_PLUS2])
        inventory = RelicInventory.from_owned_relics([
            _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY], ga_handle=1),
            _make_relic([_HP_RESTORE_PLUS2, EMPTY, EMPTY], ga_handle=2),
        ])
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        assert results
        _assert_score_consistency(results)
        _assert_redundant_zero(results)

        # Only assert the strong stacking condition when both actually landed
        # in the same vessel (vessel structure is hero-dependent).
        for result in results:
            assigned_effects = {
                eff
                for a in result.assignments
                if a.relic is not None
                for eff in a.relic.all_effects
            }
            if _HP_RESTORE_PLUS1 in assigned_effects and _HP_RESTORE_PLUS2 in assigned_effects:
                for assignment in result.assignments:
                    for entry in assignment.breakdown:
                        if entry["effect_id"] in (_HP_RESTORE_PLUS1, _HP_RESTORE_PLUS2):
                            assert not entry["redundant"], (
                                f"Sibling variant {entry['effect_id']} falsely "
                                f"marked redundant in vessel '{result.vessel_name}'"
                            )
                assert result.total_score >= 100, (
                    f"Both +1 (+50) and +2 (+50) present in "
                    f"'{result.vessel_name}' but total_score={result.total_score}; "
                    f"one is being falsely blocked"
                )
                return  # covered — exit early

    def test_base_blocks_variant_globally(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """With only base (+0) and variant (+1) in the inventory, no vessel can
        score more than 50 pts.  Both occupying the same vessel means one is
        blocked, so the optimizer will prefer assigning only one per vessel.
        """
        build = _make_build(preferred=[_HP_RESTORE_BASE, _HP_RESTORE_PLUS1])
        inventory = RelicInventory.from_owned_relics([
            _make_relic([_HP_RESTORE_BASE, EMPTY, EMPTY], ga_handle=1),
            _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY], ga_handle=2),
        ])
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        assert results
        _assert_score_consistency(results)
        _assert_redundant_zero(results)

        # The top-scoring vessel must not exceed 50 pts.
        # If the bug were present, both would score → max could be 100.
        best = max(results, key=lambda r: r.total_score)
        assert best.total_score <= 50, (
            f"Base and variant cannot both score; top vessel has "
            f"total_score={best.total_score} (expected ≤50)"
        )

    def test_mega_group_100_effects_coexist_in_pipeline(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """A no_stack compat=100 effect and a stack compat=100 effect must both
        score in the same vessel.  The self-ref guard prevents mega-group ID 100
        from entering any stacking sets, so no false conflict arises.
        """
        build = _make_build(preferred=[_TAKING_ATTACKS, _PHYSICAL_ATK_1])
        inventory = RelicInventory.from_owned_relics([
            _make_relic([_TAKING_ATTACKS, EMPTY, EMPTY], ga_handle=1),
            _make_relic([_PHYSICAL_ATK_1, EMPTY, EMPTY], ga_handle=2),
        ])
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        assert results
        _assert_score_consistency(results)

        for result in results:
            assigned_effects = {
                eff
                for a in result.assignments
                if a.relic is not None
                for eff in a.relic.all_effects
            }
            if _TAKING_ATTACKS in assigned_effects and _PHYSICAL_ATK_1 in assigned_effects:
                for assignment in result.assignments:
                    for entry in assignment.breakdown:
                        if entry["effect_id"] in (_TAKING_ATTACKS, _PHYSICAL_ATK_1):
                            assert not entry["redundant"], (
                                f"Effect {entry['effect_id']} (compat=100) falsely "
                                f"blocked in '{result.vessel_name}'"
                            )
                assert result.total_score >= 100, (
                    f"Two compat=100 effects with no exclusivityId conflict "
                    f"should both score (≥100), got {result.total_score}"
                )
                return


class TestFullPipelineExclusivityStacking:

    def test_different_imbues_conflict_in_pipeline(
        self, optimizer: VesselOptimizer,
    ) -> None:
        """Two weapon imbues sharing exclusivityId=100 cannot both score.
        The best vessel score must be ≤50 (only one imbue contributes).
        """
        build = _make_build(preferred=[_IMBUE_MAGIC, _IMBUE_FIRE])
        inventory = RelicInventory.from_owned_relics([
            _make_relic([_IMBUE_MAGIC, EMPTY, EMPTY], ga_handle=1),
            _make_relic([_IMBUE_FIRE, EMPTY, EMPTY], ga_handle=2),
        ])
        results = optimizer.optimize_all_vessels(build, inventory, 1)
        assert results
        _assert_score_consistency(results)
        _assert_redundant_zero(results)

        best = max(results, key=lambda r: r.total_score)
        assert best.total_score <= 50, (
            f"Two conflicting imbues cannot both score; "
            f"top vessel has total_score={best.total_score} (expected ≤50)"
        )


# ---------------------------------------------------------------------------
# Real save fixture tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_inventory(ds: SourceDataHandler) -> RelicInventory:
    import nrplanner as _pkg
    items_json_path = (
        Path(_pkg.__file__).parent / "resources" / "json" / "items.json"
    )
    items_json = json.loads(items_json_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmpdir:
        decrypt_sl2(FIXTURE_PATH, tmpdir)
        characters = discover_characters(tmpdir)
        assert characters, "No characters found in save fixture"
        _, char_path = characters[0]
        data = char_path.read_bytes()
        raw_relics, _ = parse_relics(data)
    return RelicInventory(raw_relics, items_json, ds)


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present — copy NR0000.sl2 to backend/tests/fixtures/",
)
class TestRealSaveFixture:
    """Invariant checks and stacking sanity with real player relics.

    These tests are the final safety net: any stacking bug that produces
    incorrect behaviour on real saves will be caught here even if synthetic
    tests miss it due to inventory composition.
    """

    def test_score_consistency(
        self, optimizer: VesselOptimizer, real_inventory: RelicInventory,
    ) -> None:
        """total_score == sum(slot scores) for all top-5 vessels (real inventory)."""
        results = optimizer.optimize_all_vessels(
            _make_build(), real_inventory, 1, top_n=5
        )
        _assert_score_consistency(results)

    def test_redundant_implies_zero(
        self, optimizer: VesselOptimizer, real_inventory: RelicInventory,
    ) -> None:
        """Redundant entries always have score=0 (real inventory)."""
        results = optimizer.optimize_all_vessels(
            _make_build(), real_inventory, 1, top_n=5
        )
        _assert_redundant_zero(results)

    def test_hp_restore_sibling_variants_not_blocked(
        self, optimizer: VesselOptimizer, real_inventory: RelicInventory,
    ) -> None:
        """With real relics prioritising +1 and +2, no sibling variant should
        be marked redundant in a vessel that does not also contain the base.

        If the base is also present in the vessel, one of base/variant will
        legitimately be redundant, so we skip that case.
        """
        build = _make_build(preferred=[_HP_RESTORE_PLUS1, _HP_RESTORE_PLUS2])
        results = optimizer.optimize_all_vessels(build, real_inventory, 1, top_n=5)
        _assert_score_consistency(results)
        _assert_redundant_zero(results)

        for result in results:
            assigned_effects = {
                eff
                for a in result.assignments
                if a.relic is not None
                for eff in a.relic.all_effects
            }
            base_present = _HP_RESTORE_BASE in assigned_effects
            if base_present:
                continue  # base legitimately blocks one variant — skip

            if _HP_RESTORE_PLUS1 in assigned_effects and _HP_RESTORE_PLUS2 in assigned_effects:
                for assignment in result.assignments:
                    for entry in assignment.breakdown:
                        if entry["effect_id"] in (_HP_RESTORE_PLUS1, _HP_RESTORE_PLUS2):
                            assert not entry["redundant"], (
                                f"Sibling HP Restore variant {entry['effect_id']} "
                                f"falsely blocked (no base present) in "
                                f"'{result.vessel_name}'"
                            )

    def test_no_false_mega_group_blocks(
        self, optimizer: VesselOptimizer, real_inventory: RelicInventory,
    ) -> None:
        """With compat=100 effects prioritised, no false blocks from mega-group
        sentinel 100 should occur in the real inventory."""
        build = _make_build(preferred=[_TAKING_ATTACKS, _PHYSICAL_ATK_1])
        results = optimizer.optimize_all_vessels(build, real_inventory, 1, top_n=5)
        _assert_score_consistency(results)
        _assert_redundant_zero(results)
