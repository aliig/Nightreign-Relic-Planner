"""Tests for RelicGenerator (generator.py).

Uses the real SourceDataHandler (session-scoped ``ds`` fixture in conftest.py). All
template/effect IDs are derived at runtime from loaded game data so the tests stay valid
across patches. Fully offline — no save fixture needed.
"""
import random
from collections import Counter

import pytest

from nrplanner import InvalidReason, RelicChecker, SourceDataHandler
from nrplanner.constants import COLOR_MAP, EMPTY_EFFECT, RELIC_GROUPS
from nrplanner.generator import RelicGenerator

_EMPTY = {-1, 0, EMPTY_EFFECT}


def _templates(ds: SourceDataHandler, is_deep: bool, version: str):
    """(real_id, color, tier) for every template in the version's pool range."""
    key = f"{'deep' if is_deep else 'store'}_{'103' if version == '1.03' else '102'}"
    lo, hi = RELIC_GROUPS[key]
    out = []
    for rid in ds.relic_table.index:
        rid = int(rid)
        if not (lo <= rid <= hi):
            continue
        tier = ds.get_relic_slot_count(rid)[0]
        if tier == 0:
            continue
        color = COLOR_MAP[int(ds.relic_table.loc[rid, "relicColor"])]
        out.append((rid, color, tier))
    return out


@pytest.fixture(scope="module")
def gen(ds: SourceDataHandler) -> RelicGenerator:
    return RelicGenerator(ds)


@pytest.fixture(scope="module")
def checker(ds: SourceDataHandler) -> RelicChecker:
    return RelicChecker([], ds)


# ---------------------------------------------------------------------------
# Data assumptions the generator relies on
# ---------------------------------------------------------------------------

class TestDataInvariants:
    def test_store_and_deep_templates_exist(self, ds: SourceDataHandler) -> None:
        assert _templates(ds, is_deep=False, version="1.03"), "no store_103 templates"
        assert _templates(ds, is_deep=True, version="1.03"), "no deep_103 templates"

    def test_deep_pools_are_disjoint(self, ds: SourceDataHandler) -> None:
        """Curse-required pool 2000000 shares no effect with the curse-free deep pools."""
        p_req = set(ds.get_pool_effects_strict(2000000))
        p_free1 = set(ds.get_pool_effects_strict(2100000))
        p_free2 = set(ds.get_pool_effects_strict(2200000))
        assert p_req, "curse-required pool 2000000 is empty"
        assert p_req & p_free1 == set()
        assert p_req & p_free2 == set()

    def test_curse_required_pool_effects_all_need_curse(
        self, ds: SourceDataHandler
    ) -> None:
        for e in ds.get_pool_effects_strict(2000000):
            assert ds.effect_needs_curse(e), f"effect {e} in pool 2000000 must need a curse"

    def test_weighted_effects_match_strict_membership(
        self, ds: SourceDataHandler
    ) -> None:
        """get_pool_weighted_effects membership == get_pool_effects_strict, weights > 0."""
        for pool in (2000000, 2100000, 2200000):
            weighted = ds.get_pool_weighted_effects(pool)
            strict = ds.get_pool_effects_strict(pool)
            assert [e for e, _ in weighted] == list(strict)
            assert all(w > 0 for _, w in weighted)


# ---------------------------------------------------------------------------
# Legality — the core guarantee
# ---------------------------------------------------------------------------

class TestLegality:
    def _assert_legal(self, ds, checker, relic) -> None:
        arr = list(relic.effects) + list(relic.curses)
        assert checker.check_invalidity(relic.real_id, arr) == InvalidReason.NONE
        # No duplicate real effects
        real = [e for e in relic.effects if e not in _EMPTY]
        assert len(real) == len(set(real)), f"duplicate effect in {relic}"
        # No two effects/curses share a conflict (compatibilityId) group
        cids = [ds.get_effect_conflict_id(e) for e in arr if e not in _EMPTY]
        cids = [c for c in cids if c != -1]
        assert len(cids) == len(set(cids)), f"conflicting effects in {relic}"

    @pytest.mark.parametrize("is_deep", [False, True])
    def test_every_template_rolls_legal(
        self, ds, gen, checker, is_deep: bool
    ) -> None:
        """Every (color, tier) template in the version pool rolls legal relics."""
        templates = _templates(ds, is_deep, "1.03")
        rng = random.Random(1234)
        for real_id, color, tier in templates:
            for _ in range(6):
                relic = gen.roll(is_deep=is_deep, mode="targeted",
                                 color=color, tier=tier, rng=rng)
                assert relic.tier == tier
                self._assert_legal(ds, checker, relic)

    def test_random_mode_rolls_legal(self, ds, gen, checker) -> None:
        rng = random.Random(99)
        for is_deep in (False, True):
            for _ in range(80):
                relic = gen.roll(is_deep=is_deep, mode="random", rng=rng)
                self._assert_legal(ds, checker, relic)

    def test_tier_matches_effect_count(self, ds, gen) -> None:
        rng = random.Random(7)
        for is_deep in (False, True):
            for _ in range(50):
                relic = gen.roll(is_deep=is_deep, mode="random", rng=rng)
                filled = sum(1 for e in relic.effects if e not in _EMPTY)
                assert filled == relic.tier


# ---------------------------------------------------------------------------
# Curses — deep-relic mechanic
# ---------------------------------------------------------------------------

class TestCurses:
    def test_curse_iff_effect_needs_one(self, ds, gen) -> None:
        """Every slot has a curse exactly when its effect requires one; else empty."""
        rng = random.Random(2024)
        saw_curse = False
        for _ in range(200):
            relic = gen.roll(is_deep=True, mode="random", rng=rng)
            for eff, curse in zip(relic.effects, relic.curses):
                if eff not in _EMPTY and ds.effect_needs_curse(eff):
                    assert curse not in _EMPTY, f"missing curse for {eff} in {relic}"
                    saw_curse = True
                else:
                    assert curse in _EMPTY, f"unexpected curse {curse} in {relic}"
        assert saw_curse, "no curse-required deep relic was ever generated"

    def test_normal_relics_never_have_curses(self, ds, gen) -> None:
        rng = random.Random(55)
        for _ in range(300):
            relic = gen.roll(is_deep=False, mode="random", rng=rng)
            assert all(c in _EMPTY for c in relic.curses)


# ---------------------------------------------------------------------------
# Weight distribution — the roll must match the real pool weights
# ---------------------------------------------------------------------------

class TestDistribution:
    def test_draw_matches_pool_weights(self, ds, gen) -> None:
        """An unconstrained slot draw reproduces the pool's normalized weights.

        Tests the roll's odds core directly (no full-relic overhead): a slot with no
        exclusions is exactly a weighted pick over get_pool_weighted_effects, which is
        the marginal distribution of effects[i] for a fresh slot.
        """
        # Use the curse-required deep pool — small enough to check every effect.
        pool = 2000000
        weighted = ds.get_pool_weighted_effects(pool)
        total = sum(w for _, w in weighted)
        expected = {e: w / total for e, w in weighted}

        n = 40000
        rng = random.Random(20240701)
        counts = Counter()
        for _ in range(n):
            counts[gen._draw(pool, set(), set(), rng)] += 1

        for e, p in expected.items():
            emp = counts[e] / n
            # ~4+ sigma band for the least-likely effects at this sample size.
            assert abs(emp - p) < 0.01 + 0.03 * p, (
                f"effect {e}: expected {p:.4f}, got {emp:.4f}")

    def test_slot1_marginal_matches_pool_weights(self, ds, gen) -> None:
        """End-to-end sanity: a tier-1 relic's only effect follows the pool weights."""
        tier1 = next(
            t for t in _templates(ds, is_deep=False, version="1.03") if t[2] == 1
        )
        _, color, _ = tier1
        pool = int(ds.get_relic_pools_seq(tier1[0])[0])
        weighted = ds.get_pool_weighted_effects(pool)
        total = sum(w for _, w in weighted)
        expected = {e: w / total for e, w in weighted}

        n = 3000
        rng = random.Random(101)
        counts = Counter()
        for _ in range(n):
            relic = gen.roll(is_deep=False, mode="targeted", color=color, tier=1, rng=rng)
            counts[relic.effects[0]] += 1

        # Only the highest-probability effects, loose band (small n keeps this fast).
        for e, p in sorted(expected.items(), key=lambda kv: -kv[1])[:5]:
            emp = counts[e] / n
            assert abs(emp - p) < 0.06, f"effect {e}: expected {p:.3f}, got {emp:.3f}"


# ---------------------------------------------------------------------------
# Determinism & modes
# ---------------------------------------------------------------------------

class TestModes:
    def test_same_seed_same_relic(self, gen) -> None:
        a = gen.roll(is_deep=True, mode="random", seed=42)
        b = gen.roll(is_deep=True, mode="random", seed=42)
        assert a == b

    def test_different_seeds_differ(self, gen) -> None:
        # Over several seeds at least one pair must differ (sanity, not strict).
        relics = [gen.roll(is_deep=False, mode="random", seed=s) for s in range(8)]
        assert len({(r.real_id, r.effects) for r in relics}) > 1

    def test_targeted_mode_honors_color_and_tier(self, ds, gen) -> None:
        for real_id, color, tier in _templates(ds, is_deep=False, version="1.03"):
            relic = gen.roll(is_deep=False, mode="targeted",
                             color=color, tier=tier, seed=1)
            assert relic.color == color
            assert relic.tier == tier
            assert relic.odds_source == "targeted"

    def test_targeted_requires_color_and_tier(self, gen) -> None:
        with pytest.raises(ValueError):
            gen.roll(mode="targeted", color="Red")  # missing tier

    def test_random_fallback_is_approximate(self, gen) -> None:
        relic = gen.roll(is_deep=False, mode="random", seed=3)
        assert relic.odds_source == "approximate"
        assert relic.color in ("Red", "Blue", "Yellow", "Green")

    def test_exact_lot_marks_exact_and_uses_entries(self, ds, gen) -> None:
        templates = _templates(ds, is_deep=False, version="1.03")
        target = templates[0][0]
        gen_with_lot = RelicGenerator(ds, lots={
            "scenic_1.03": {"entries": [(target, 100)], "approximate": False}
        })
        relic = gen_with_lot.roll(is_deep=False, mode="random", seed=5)
        assert relic.real_id == target
        assert relic.odds_source == "exact"
