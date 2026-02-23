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
