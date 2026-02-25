"""Tests for BuildScorer (scoring.py).

OwnedRelic objects are constructed directly via Pydantic — no save parsing.
BuildDefinition tiers are populated with real effect IDs from game data.
"""
import pytest

from nrplanner import BuildScorer, SourceDataHandler
from nrplanner.models import ALL_TIER_KEYS, BuildDefinition, OwnedRelic

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
) -> BuildDefinition:
    tiers = {k: [] for k in ALL_TIER_KEYS}
    if required:
        tiers["required"] = required
    if avoid:
        tiers["avoid"] = avoid
    if blacklist:
        tiers["blacklist"] = blacklist
    return BuildDefinition(
        id="test-build",
        name="Test Build",
        character="Wylder",
        tiers=tiers,
        family_tiers={k: [] for k in ALL_TIER_KEYS},
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


class TestHasBlacklistedEffect:
    def test_empty_blacklist_returns_false(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        build = _make_build()  # no blacklist
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.has_blacklisted_effect(relic, build) is False

    def test_effect_in_blacklist_returns_true(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        build = _make_build(blacklist=[eff_id])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.has_blacklisted_effect(relic, build) is True

    def test_effect_not_in_blacklist_returns_false(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        other_eff = all_effects[1]["id"]
        build = _make_build(blacklist=[other_eff])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        assert scorer.has_blacklisted_effect(relic, build) is False


class TestCustomTierWeights:
    def test_higher_weight_yields_higher_score(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        base_build = _make_build(required=[eff_id])
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        default_score = scorer.score_relic(relic, base_build)

        tiers = {k: [] for k in ALL_TIER_KEYS}
        tiers["required"] = [eff_id]
        high_build = BuildDefinition(
            id="hw", name="HW", character="Wylder",
            tiers=tiers,
            family_tiers={k: [] for k in ALL_TIER_KEYS},
            include_deep=False,
            curse_max=1,
            tier_weights={"required": 200},
        )
        assert scorer.score_relic(relic, high_build) > default_score

    def test_none_tier_weights_uses_defaults(
        self, scorer: BuildScorer, all_effects: list[dict]
    ) -> None:
        eff_id = all_effects[0]["id"]
        relic = _make_relic([eff_id, EMPTY, EMPTY])
        tiers = {k: [] for k in ALL_TIER_KEYS}
        tiers["required"] = [eff_id]
        build_explicit_none = BuildDefinition(
            id="n", name="N", character="Wylder",
            tiers=tiers,
            family_tiers={k: [] for k in ALL_TIER_KEYS},
            include_deep=False,
            curse_max=1,
            tier_weights=None,
        )
        build_defaults = _make_build(required=[eff_id])
        assert scorer.score_relic(relic, build_explicit_none) == scorer.score_relic(relic, build_defaults)


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
        required_keys = {"effect_id", "name", "tier", "score", "is_curse", "redundant"}
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
) -> tuple[set[int], set[int], set[int]]:
    """Build (vessel_effect_ids, vessel_exclusivity_ids, vessel_no_stack_excl_ids)
    as if relics with *effect_ids* were already placed in earlier slots."""
    eff_ids: set[int] = set()
    excl_ids: set[int] = set()
    ns_excl_ids: set[int] = set()
    for eid in effect_ids:
        eff_ids.add(eid)
        text_id = ds.get_effect_text_id(eid)
        if text_id != -1 and text_id != eid:
            eff_ids.add(text_id)
        excl = ds.get_effect_exclusivity_id(eid)
        if excl != -1:
            excl_ids.add(excl)
            if ds.get_effect_stacking_type(eid) == "no_stack":
                ns_excl_ids.add(excl)
    return eff_ids, excl_ids, ns_excl_ids


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
        v_eff, v_excl, v_ns = _vessel_state_from_effects(ds, [_GUARD_COUNTER_HP])

        score = scorer.score_relic_in_context(relic, build, v_eff, v_excl, v_ns)
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
        v_eff, v_excl, v_ns = _vessel_state_from_effects(ds, [_GUARD_COUNTER_HP])

        breakdown = scorer.get_breakdown(relic, build, v_eff, v_excl, v_ns)
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
        v_eff, v_excl, v_ns = _vessel_state_from_effects(ds, [_TAKING_ATTACKS_UP])

        score = scorer.score_relic_in_context(relic, build, v_eff, v_excl, v_ns)
        assert score == 0, "Duplicate no_stack effect must score 0"

    def test_duplicate_no_stack_effect_marked_redundant_in_breakdown(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        build = _make_build(required=[_TAKING_ATTACKS_UP])
        relic = _make_relic([_TAKING_ATTACKS_UP, EMPTY, EMPTY])
        v_eff, v_excl, v_ns = _vessel_state_from_effects(ds, [_TAKING_ATTACKS_UP])

        breakdown = scorer.get_breakdown(relic, build, v_eff, v_excl, v_ns)
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
        v_eff, v_excl, v_ns = _vessel_state_from_effects(ds, [_IMBUE_MAGIC])

        score = scorer.score_relic_in_context(relic, build, v_eff, v_excl, v_ns)
        assert score == 0, (
            "Different imbues with same exclusivityId must conflict"
        )

    def test_different_weapon_imbues_marked_redundant_in_breakdown(
        self, scorer: BuildScorer, ds: SourceDataHandler,
    ) -> None:
        build = _make_build(required=[_IMBUE_MAGIC, _IMBUE_FIRE])
        relic = _make_relic([_IMBUE_FIRE, EMPTY, EMPTY])
        v_eff, v_excl, v_ns = _vessel_state_from_effects(ds, [_IMBUE_MAGIC])

        breakdown = scorer.get_breakdown(relic, build, v_eff, v_excl, v_ns)
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
        v_eff, v_excl, v_ns = _vessel_state_from_effects(ds, [_SKILL_PHALANX])

        score = scorer.score_relic_in_context(relic, build, v_eff, v_excl, v_ns)
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
        v_eff, v_excl, v_ns = _vessel_state_from_effects(ds, [_GUARD_COUNTER_HP])

        score = scorer.score_relic_in_context(relic, build, v_eff, v_excl, v_ns)
        assert score > 0, "Stack-type effects must always score positively"
