"""Tests for BuildScorer (scoring.py).

OwnedRelic objects are constructed directly via Pydantic — no save parsing.
BuildDefinition tiers are populated with real effect IDs from game data.
"""
import pytest

from nrplanner import BuildScorer, SourceDataHandler
from nrplanner.models import BuildDefinition, OwnedRelic, VesselState, WeightGroup

EMPTY = 4294967295  # EMPTY_EFFECT sentinel


def _make_relic(effects: list[int], curses: list[int] | None = None) -> OwnedRelic:
    curses = curses or [EMPTY, EMPTY, EMPTY]
    effect_count = sum(1 for e in effects if e not in (EMPTY, 0))
    tier = "Grand" if effect_count >= 3 else ("Polished" if effect_count == 2 else "Delicate")
    return OwnedRelic(
        ga_handle=0xC0000001,
        item_id=100 + 2147483648,
        real_id=100,
        color="Red",
        effects=effects,
        curses=curses,
        is_deep=False,
        name="Test Relic",
        tier=tier,
    )


def _make_build(
    required: list[int] | None = None,
    avoid: list[int] | None = None,
    blacklist: list[int] | None = None,
    groups: list[WeightGroup] | None = None,
) -> BuildDefinition:
    return BuildDefinition(
        id="test-build",
        name="Test Build",
        character="Wylder",
        groups=groups or (
            [WeightGroup(weight=-20, effects=avoid)] if avoid else []
        ),
        required_effects=required or [],
        excluded_effects=blacklist or [],
        include_deep=False,
        curse_max=1,
    )


@pytest.fixture(scope="module")
def scorer(ds: SourceDataHandler) -> BuildScorer:
    return BuildScorer(ds)


class TestScoreRelic:
    def test_required_effect_scores_positive(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        build = _make_build(required=[eff_id])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.score_relic(relic, build) > 0

    def test_avoid_effect_scores_negative(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[1]["id"]
        build = _make_build(avoid=[eff_id])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.score_relic(relic, build) < 0

    def test_no_matching_effects_scores_zero(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[2]["id"]
        build = _make_build()  # empty tiers
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.score_relic(relic, build) == 0

    def test_grand_relic_with_no_tier_match_scores_zero(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff1, eff2, eff3 = all_effects[0]["id"], all_effects[1]["id"], all_effects[2]["id"]
        build = _make_build()  # empty tiers — no bonus for effect count
        relic = _make_relic([eff1, eff2, eff3])  # Grand (3 effects)
        assert scorer.score_relic(relic, build) == 0


class TestHasExcludedEffect:
    def test_empty_exclusions_returns_false(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        build = _make_build()  # no exclusions
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.has_excluded_effect(relic, build) is False

    def test_excluded_effect_returns_true(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        build = _make_build(blacklist=[eff_id])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.has_excluded_effect(relic, build) is True

    def test_non_excluded_effect_returns_false(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        other_eff = all_effects[1]["id"]
        build = _make_build(blacklist=[other_eff])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.has_excluded_effect(relic, build) is False


class TestCustomGroupWeights:
    def test_higher_weight_group_yields_higher_score(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        low_build = _make_build(groups=[WeightGroup(weight=10, effects=[eff_id])])
        high_build = _make_build(groups=[WeightGroup(weight=80, effects=[eff_id])])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.score_relic(relic, high_build) > scorer.score_relic(relic, low_build)

    def test_required_effect_scores_at_required_weight(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        from nrplanner.models import REQUIRED_WEIGHT
        eff_id = all_effects[0]["id"]
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        build = _make_build(required=[eff_id])
        assert scorer.score_relic(relic, build) == REQUIRED_WEIGHT

    def test_negative_weight_group_yields_negative_score(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        build = _make_build(groups=[WeightGroup(weight=-30, effects=[eff_id])])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.score_relic(relic, build) < 0


class TestGetBreakdown:
    def test_returns_list_of_dicts(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        build = _make_build(required=[eff_id])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        breakdown = scorer.get_breakdown(relic, build)
        assert isinstance(breakdown, list)
        assert len(breakdown) > 0

    def test_breakdown_item_has_required_keys(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        build = _make_build(required=[eff_id])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        breakdown = scorer.get_breakdown(relic, build)
        required_keys = {"effect_id", "name", "category", "weight", "score", "is_curse", "redundant"}
        for item in breakdown:
            assert required_keys.issubset(item.keys()), f"Missing keys: {item}"

    def test_relic_with_all_empty_has_empty_breakdown(
        self, scorer: BuildScorer
    ) -> None:
        build = _make_build()
        relic = _make_relic([EMPTY, EMPTY, EMPTY])
        assert scorer.get_breakdown(relic, build) == []


# ---------------------------------------------------------------------------
# Stacking / exclusivity tests
#
# These use real effect IDs from the game data to verify that the
# exclusivityId-based mutual-exclusion logic works correctly:
#
# - Different no_stack offensive buffs (compat=100, excl=-1) must NOT
#   conflict — they only conflict with copies of themselves.
# - Weapon imbues (compat=200, excl=100) truly override each other.
# - Ash-of-war swaps (compat=300, excl=200) truly override each other.
# ---------------------------------------------------------------------------

# Real effect IDs confirmed from AttachEffectParam.csv
_TAKING_ATTACKS_UP = 7032200       # compat=100, excl=-1, no_stack
_GUARD_COUNTER_HP  = 7150000       # compat=100, excl=-1, no_stack
_IMBUE_MAGIC       = 7120000       # compat=200, excl=100, no_stack
_IMBUE_FIRE        = 7120100       # compat=200, excl=100, no_stack
_SKILL_PHALANX     = 7122700       # compat=300, excl=200, no_stack
_SKILL_GRAVITAS    = 7122800       # compat=300, excl=200, no_stack


def _vessel_state_from_effects(
    ds: SourceDataHandler, effect_ids: list[int],
) -> VesselState:
    """Build a VesselState as if relics with *effect_ids* were already
    placed in earlier slots."""
    state = VesselState(ds)
    for eid in effect_ids:
        relic = _make_relic([eid, EMPTY, EMPTY])
        state.place(relic)
    return state


class TestExclusivityStacking:
    """Verify exclusivityId-based conflict detection."""

    # -- Different no_stack offensive buffs (compat=100) coexist -------------

    def test_different_no_stack_offensive_buffs_coexist(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """'Taking attacks improves attack power' should NOT be redundant
        when 'Guard counter boost based on HP' is already placed.
        Both share compatibilityId=100 but have exclusivityId=-1."""
        build = _make_build(required=[_TAKING_ATTACKS_UP, _GUARD_COUNTER_HP])
        relic = _make_relic([_TAKING_ATTACKS_UP, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_GUARD_COUNTER_HP])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score > 0, (
            "Different offensive buffs with excl=-1 must score positively"
        )

    def test_different_no_stack_offensive_buffs_not_redundant_in_breakdown(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """Breakdown should not mark 'Taking attacks improves attack power'
        as redundant when a different compat=100 effect is already placed."""
        build = _make_build(required=[_TAKING_ATTACKS_UP, _GUARD_COUNTER_HP])
        relic = _make_relic([_TAKING_ATTACKS_UP, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_GUARD_COUNTER_HP])

        breakdown = scorer.get_breakdown(relic, build, state)
        for entry in breakdown:
            if entry["effect_id"] == _TAKING_ATTACKS_UP:
                assert not entry["redundant"], (
                    f"Effect should NOT be redundant: {entry}"
                )
                assert entry["score"] > 0
                break
        else:
            pytest.fail("Expected effect not found in breakdown")

    # -- Same no_stack effect IS blocked (self-stacking) ---------------------

    def test_duplicate_no_stack_effect_is_redundant(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """Two copies of 'Taking attacks improves attack power' should NOT
        stack — the second one scores 0."""
        build = _make_build(required=[_TAKING_ATTACKS_UP])
        relic = _make_relic([_TAKING_ATTACKS_UP, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_TAKING_ATTACKS_UP])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score == 0, "Duplicate no_stack effect must score 0"

    def test_duplicate_no_stack_effect_marked_redundant_in_breakdown(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        build = _make_build(required=[_TAKING_ATTACKS_UP])
        relic = _make_relic([_TAKING_ATTACKS_UP, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_TAKING_ATTACKS_UP])

        breakdown = scorer.get_breakdown(relic, build, state)
        for entry in breakdown:
            if entry["effect_id"] == _TAKING_ATTACKS_UP:
                assert entry["redundant"], "Duplicate should be redundant"
                assert entry["override_status"] == "duplicate"
                break
        else:
            pytest.fail("Expected effect not found in breakdown")

    # -- Weapon imbues (excl=100) override each other ------------------------

    def test_different_weapon_imbues_override(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """'Starting armament deals fire' should be redundant when 'starting
        armament deals magic' is already placed. Both have exclusivityId=100."""
        build = _make_build(required=[_IMBUE_MAGIC, _IMBUE_FIRE])
        relic = _make_relic([_IMBUE_FIRE, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_IMBUE_MAGIC])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score == 0, (
            "Different imbues with same exclusivityId must conflict"
        )

    def test_different_weapon_imbues_marked_redundant_in_breakdown(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        build = _make_build(required=[_IMBUE_MAGIC, _IMBUE_FIRE])
        relic = _make_relic([_IMBUE_FIRE, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_IMBUE_MAGIC])

        breakdown = scorer.get_breakdown(relic, build, state)
        for entry in breakdown:
            if entry["effect_id"] == _IMBUE_FIRE:
                assert entry["redundant"], "Second imbue should be redundant"
                assert entry["override_status"] == "overridden"
                break
        else:
            pytest.fail("Expected effect not found in breakdown")

    # -- Ash-of-war swaps (excl=200) override each other ---------------------

    def test_different_ash_of_war_skills_override(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """'Skill to Gravitas' should be redundant when 'Skill to Glintblade
        Phalanx' is already placed. Both have exclusivityId=200."""
        build = _make_build(required=[_SKILL_PHALANX, _SKILL_GRAVITAS])
        relic = _make_relic([_SKILL_GRAVITAS, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_SKILL_PHALANX])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score == 0, (
            "Different ash-of-war skills with same exclusivityId must conflict"
        )

    # -- Stack-type effects always score (sanity check) ----------------------

    def test_stack_type_effects_always_score(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """'Fire Attack Power Up' (stack type) should score even when another
        compat=100 effect is already placed."""
        fire_atk_up = 7001600  # compat=100, stacking type: stack
        build = _make_build(required=[fire_atk_up, _GUARD_COUNTER_HP])
        relic = _make_relic([fire_atk_up, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_GUARD_COUNTER_HP])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score > 0, "Stack-type effects must always score positively"


# ---------------------------------------------------------------------------
# Tier-family stacking tests
#
# Tier families share a compatibilityId. The base (+0) is typically no_stack,
# while +1/+2 variants are unique. The base should NOT stack with +1/+2.
# However, effects where ALL tiers are "unique" (e.g. "Defeating enemies fills
# more of the Art gauge" +0 and +1) SHOULD stack with each other.
# ---------------------------------------------------------------------------

# Real effect IDs from AttachEffectParam.csv
_HP_RESTORE_BASE   = 7005600  # compat=7005600, excl=-1, no_stack
_HP_RESTORE_PLUS1  = 6005600  # compat=7005600, excl=-1, unique
_HP_RESTORE_PLUS2  = 6005601  # compat=7005600, excl=-1, unique

_ART_GAUGE_BASE    = 7090000  # compat=7090000, excl=-1, unique
_ART_GAUGE_PLUS1   = 6090000  # compat=7090000, excl=-1, unique


class TestTierFamilyStacking:
    """Verify tier-family conflict detection via compatibilityId."""

    # -- HP Restoration: no_stack base blocks unique variants ----------------

    def test_hp_restore_base_blocks_plus1(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """HP Restoration +0 (no_stack) is already placed -> +1 (unique)
        should be blocked because they share compat=7005600."""
        build = _make_build(required=[_HP_RESTORE_BASE, _HP_RESTORE_PLUS1])
        relic = _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_HP_RESTORE_BASE])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score == 0, "unique +1 must be blocked when no_stack base is placed"

    def test_hp_restore_base_blocks_plus2(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """HP Restoration +0 (no_stack) is already placed -> +2 (unique)
        should be blocked."""
        build = _make_build(required=[_HP_RESTORE_BASE, _HP_RESTORE_PLUS2])
        relic = _make_relic([_HP_RESTORE_PLUS2, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_HP_RESTORE_BASE])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score == 0, "unique +2 must be blocked when no_stack base is placed"

    def test_hp_restore_plus1_blocks_base(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """HP Restoration +1 (unique) is already placed -> +0 (no_stack)
        should be blocked via identity (compat added to effect_ids)."""
        build = _make_build(required=[_HP_RESTORE_PLUS1, _HP_RESTORE_BASE])
        relic = _make_relic([_HP_RESTORE_BASE, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_HP_RESTORE_PLUS1])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score == 0, "no_stack base must be blocked when unique variant is placed"

    def test_hp_restore_base_marked_redundant_in_breakdown(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """Breakdown should mark +0 as redundant when +1 is placed."""
        build = _make_build(required=[_HP_RESTORE_PLUS1, _HP_RESTORE_BASE])
        relic = _make_relic([_HP_RESTORE_BASE, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_HP_RESTORE_PLUS1])

        breakdown = scorer.get_breakdown(relic, build, state)
        for entry in breakdown:
            if entry["effect_id"] == _HP_RESTORE_BASE:
                assert entry["redundant"], "Base should be marked redundant"
                break
        else:
            pytest.fail("Expected effect not found in breakdown")

    # -- HP Restore +1 and +2 coexist (sibling variants) --------------------

    def test_hp_restore_plus1_and_plus2_coexist(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """+1 (unique) placed first -> +2 (unique) must NOT be blocked.
        Both are variants of the same tier family but are not mutually exclusive.
        Regression test: Rule 2 previously added compat to no_stack_compat_ids,
        which incorrectly blocked sibling variants."""
        build = _make_build(required=[_HP_RESTORE_PLUS1, _HP_RESTORE_PLUS2])
        relic = _make_relic([_HP_RESTORE_PLUS2, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_HP_RESTORE_PLUS1])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score > 0, "+2 must not be blocked when only +1 is placed (no base)"

    def test_hp_restore_plus2_and_plus1_coexist_reverse_order(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """+2 placed first -> +1 must also be unblocked (order symmetric)."""
        build = _make_build(required=[_HP_RESTORE_PLUS2, _HP_RESTORE_PLUS1])
        relic = _make_relic([_HP_RESTORE_PLUS1, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_HP_RESTORE_PLUS2])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score > 0, "+1 must not be blocked when only +2 is placed (no base)"

    # -- Art gauge: both unique -> should coexist -----------------------------

    def test_art_gauge_unique_tiers_coexist(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """'Defeating enemies fills Art gauge' +0 and +1 are BOTH unique.
        Neither should block the other — they must coexist."""
        build = _make_build(required=[_ART_GAUGE_BASE, _ART_GAUGE_PLUS1])
        relic = _make_relic([_ART_GAUGE_PLUS1, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_ART_GAUGE_BASE])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score > 0, "Both-unique tier variants must coexist"

    def test_art_gauge_reverse_order_also_coexists(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """+1 placed first, then +0 — both unique, should still coexist."""
        build = _make_build(required=[_ART_GAUGE_PLUS1, _ART_GAUGE_BASE])
        relic = _make_relic([_ART_GAUGE_BASE, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_ART_GAUGE_PLUS1])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score > 0, "Both-unique tier variants must coexist (reverse order)"

    # -- Mega-group 100 regression guard: no_stack compat=100 must NOT
    #    enter no_stack_compat_ids when compat=100 is a mega-group ---------

    def test_mega_group_100_does_not_cause_false_tier_family_block(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """Guard counter (compat=100, no_stack) should NOT block other
        unrelated compat=100 effects via the tier-family path."""
        fire_atk_up = 7001600  # compat=100, stack type
        build = _make_build(required=[fire_atk_up, _GUARD_COUNTER_HP])
        relic = _make_relic([fire_atk_up, EMPTY, EMPTY])
        state = _vessel_state_from_effects(ds, [_GUARD_COUNTER_HP])

        score = scorer.score_relic_in_context(relic, build, state)
        assert score > 0, "Stack-type compat=100 must not be blocked by no_stack compat=100"


# ---------------------------------------------------------------------------
# BuildDefinition.get_effective_requirements
# ---------------------------------------------------------------------------

class TestGetEffectiveRequirements:
    """Verify that requirements are derived from the highest-weight group."""

    def test_derives_from_highest_weight_group(self) -> None:
        build = BuildDefinition(
            id="t", name="T", character="Wylder",
            groups=[
                WeightGroup(weight=50, effects=[1001, 1002], families=["Fam A"]),
                WeightGroup(weight=25, effects=[2001]),
            ],
        )
        eff, fam = build.get_effective_requirements()
        assert eff == [1001, 1002]
        assert fam == ["Fam A"]

    def test_explicit_required_takes_precedence(self) -> None:
        build = BuildDefinition(
            id="t", name="T", character="Wylder",
            groups=[WeightGroup(weight=50, effects=[1001])],
            required_effects=[9999],
        )
        eff, fam = build.get_effective_requirements()
        assert eff == [9999]
        assert fam == []

    def test_no_groups_returns_empty(self) -> None:
        build = BuildDefinition(id="t", name="T", character="Wylder")
        eff, fam = build.get_effective_requirements()
        assert eff == []
        assert fam == []

    def test_negative_weight_group_returns_empty(self) -> None:
        build = BuildDefinition(
            id="t", name="T", character="Wylder",
            groups=[WeightGroup(weight=-20, effects=[1001])],
        )
        eff, fam = build.get_effective_requirements()
        assert eff == []
        assert fam == []

    def test_zero_weight_group_returns_empty(self) -> None:
        build = BuildDefinition(
            id="t", name="T", character="Wylder",
            groups=[WeightGroup(weight=0, effects=[1001])],
        )
        eff, fam = build.get_effective_requirements()
        assert eff == []
        assert fam == []

    def test_multiple_groups_same_highest_weight(self) -> None:
        """When multiple groups share the highest weight, first one wins."""
        build = BuildDefinition(
            id="t", name="T", character="Wylder",
            groups=[
                WeightGroup(weight=50, effects=[1001]),
                WeightGroup(weight=50, effects=[2001]),
            ],
        )
        eff, _ = build.get_effective_requirements()
        assert eff == [1001]  # first group


# ---------------------------------------------------------------------------
# Excluded stacking category tests
#
# When a stacking category is excluded, relics with undesired effects from
# that category are handled in two layers:
#
# 1. Pre-filter (has_excluded_effect): hard-excludes relics when NO desired
#    effect exists in the same category.  When a desired effect exists, relics
#    are let through for positional scoring (the desired might override them).
#
# 2. Post-hoc validation (has_orphaned_excl_category_effects): after all
#    slots are assigned, drop any result where an undesired effect from the
#    category was placed but the desired effect was NOT.
#
# Together these enforce: "exclude the whole category, except this one effect."
# ---------------------------------------------------------------------------

# "Dormant Power Helps Discover ..." effects — all share compatibilityId=6630000
_DORMANT_DAGGERS      = 6630000  # Dormant Power Helps Discover Daggers
_DORMANT_GREATAXES    = 6631100  # Dormant Power Helps Discover Greataxes
_DORMANT_GREATSHIELDS = 6632900  # Dormant Power Helps Discover Greatshields

_COMPAT_DORMANT = 6630000  # shared compatibilityId


def _dormant_build() -> BuildDefinition:
    """Build that excludes Dormant Power category but desires Greatshields."""
    return BuildDefinition(
        id="test", name="Test", character="Scholar",
        groups=[WeightGroup(weight=10, effects=[_DORMANT_GREATSHIELDS])],
        excluded_stacking_categories=[_COMPAT_DORMANT],
    )


class TestExcludedStackingCategories:
    """Verify the pre-filter layer for excluded stacking categories."""

    # -- Pre-filter: no desired effect in category → hard exclude ------------

    def test_hard_excludes_when_no_desired_effect_in_category(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """When category is excluded and NO desired effect exists in it,
        relics with effects from that category are hard-excluded."""
        build = BuildDefinition(
            id="test", name="Test", character="Scholar",
            excluded_stacking_categories=[_COMPAT_DORMANT],
        )
        relic = _make_relic([_DORMANT_DAGGERS, EMPTY, EMPTY])
        assert scorer.has_excluded_effect(relic, build) is True

    # -- Pre-filter: desired effect present → let through for scoring --------

    def test_undesired_passes_prefilter_when_desired_exists_in_category(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """When a desired effect exists in the excluded category, a relic with
        an undesired variant should pass the pre-filter (positional scoring +
        post-hoc validation handle the rest)."""
        build = _dormant_build()
        relic = _make_relic([_DORMANT_DAGGERS, EMPTY, EMPTY])
        desired = scorer.get_desired_compat_effects(build)
        # NOT excluded by pre-filter — let through for positional scoring
        assert scorer.has_excluded_effect(relic, build, desired) is False

    # -- Pre-filter: desired effect itself is protected ----------------------

    def test_desired_effect_not_excluded_from_category(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """The desired effect (Greatshields) must NOT be excluded — it is
        protected by being in a weight group."""
        build = _dormant_build()
        relic = _make_relic([_DORMANT_GREATSHIELDS, EMPTY, EMPTY])
        assert scorer.has_excluded_effect(relic, build) is False

    # -- Pre-filter: unrelated effects are unaffected ------------------------

    def test_relic_without_excluded_category_not_affected(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """Relics with effects outside the excluded category pass through."""
        build = _dormant_build()
        relic = _make_relic([_TAKING_ATTACKS_UP, EMPTY, EMPTY])
        assert scorer.has_excluded_effect(relic, build) is False


class TestOrphanedExclCategoryEffects:
    """Verify the post-hoc validation layer for excluded stacking categories.

    After all vessel slots are assigned, ``has_orphaned_excl_category_effects``
    checks that no undesired effect from an excluded category was placed
    without the desired counterpart also being present.
    """

    # -- Case 1: desired placed + undesired placed → OK ----------------------

    def test_desired_and_undesired_together_is_ok(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """If the desired effect (Greatshields) IS placed alongside an
        undesired variant (Greataxes), the result is valid.  The game's
        stacking rules mean the desired effect overrides the undesired one."""
        build = _dormant_build()
        desired = scorer.get_desired_compat_effects(build)
        placed = {_DORMANT_GREATSHIELDS, _DORMANT_GREATAXES}
        assert scorer.has_orphaned_excl_category_effects(
            placed, build, desired) is False

    # -- Case 2: only desired placed → OK ------------------------------------

    def test_only_desired_placed_is_ok(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """If only the desired effect is placed (no undesired), the result
        is valid."""
        build = _dormant_build()
        desired = scorer.get_desired_compat_effects(build)
        placed = {_DORMANT_GREATSHIELDS}
        assert scorer.has_orphaned_excl_category_effects(
            placed, build, desired) is False

    # -- Case 3: undesired placed WITHOUT desired → REJECTED -----------------

    def test_undesired_without_desired_is_orphaned(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """If an undesired variant (Greataxes) is placed but the desired
        effect (Greatshields) is NOT, the result is orphaned and must be
        rejected.  This is the core bug scenario: a relic with Greataxes +
        other good effects scored well, but the user never wanted Greataxes."""
        build = _dormant_build()
        desired = scorer.get_desired_compat_effects(build)
        placed = {_DORMANT_GREATAXES}
        assert scorer.has_orphaned_excl_category_effects(
            placed, build, desired) is True

    # -- Case 4: no effects from excluded category → OK ----------------------

    def test_no_excluded_category_effects_is_ok(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """If no effects from the excluded category were placed at all,
        the result is valid."""
        build = _dormant_build()
        desired = scorer.get_desired_compat_effects(build)
        placed = {_TAKING_ATTACKS_UP}
        assert scorer.has_orphaned_excl_category_effects(
            placed, build, desired) is False

    # -- Case 5: no desired_compat_effects at all → always OK ----------------

    def test_no_desired_compat_effects_is_always_ok(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """When there are no desired compat effects (no category has a
        desired member), the post-hoc check always passes.  The pre-filter
        handles all exclusions in this case."""
        build = BuildDefinition(
            id="test", name="Test", character="Scholar",
            excluded_stacking_categories=[_COMPAT_DORMANT],
        )
        desired = scorer.get_desired_compat_effects(build)
        assert desired == {}
        placed = {_DORMANT_DAGGERS}
        assert scorer.has_orphaned_excl_category_effects(
            placed, build, desired) is False

    # -- Case 6: multiple undesired without desired → REJECTED ---------------

    def test_multiple_undesired_without_desired_is_orphaned(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        """Multiple undesired variants placed without the desired effect."""
        build = _dormant_build()
        desired = scorer.get_desired_compat_effects(build)
        placed = {_DORMANT_DAGGERS, _DORMANT_GREATAXES}
        assert scorer.has_orphaned_excl_category_effects(
            placed, build, desired) is True
