"""Tests for the Relic Rites engine (rites.py).

Uses the real SourceDataHandler (`ds` fixture) + real Wylder vessels. Generation is
stubbed for determinism; the optimizer runs for real (small/fast settings).
"""
import pytest

from nrplanner import SourceDataHandler
from nrplanner.constants import EMPTY_EFFECT as EMPTY
from nrplanner.generator import GeneratedRelic
from nrplanner.models import BuildDefinition, OwnedRelic, WeightGroup
from nrplanner.rites import (
    _SYNTH_HANDLE_BASE,
    _reserve_handles,
    BuildContext,
    PurchaseBucket,
    bulk_acquire,
    generated_to_owned,
    relic_matches_rules,
    select_cull_handles,
    unused_owned_handles,
)

# Small/fast optimizer settings for tests.
_OPT = dict(top_n=3, max_per_vessel=2, deadline_secs=3.0)


def _gen(effects, color="Red", is_deep=False, real_id=200) -> GeneratedRelic:
    eff = (list(effects) + [EMPTY, EMPTY, EMPTY])[:3]
    ec = sum(1 for e in eff if e not in (EMPTY, 0))
    return GeneratedRelic(
        real_id=real_id, item_id=real_id + 0x80000000, color=color, tier=ec,
        is_deep=is_deep, effects=tuple(eff), curses=(EMPTY, EMPTY, EMPTY),
        odds_source="targeted", name=f"Test {color}",
        effect_names=("", "", ""), curse_names=("", "", ""),
    )


def _owned(effects, color="Red", is_deep=False, ga_handle=0xC1000001) -> OwnedRelic:
    eff = (list(effects) + [EMPTY, EMPTY, EMPTY])[:3]
    ec = sum(1 for e in eff if e not in (EMPTY, 0))
    tier = "Grand" if ec >= 3 else ("Polished" if ec == 2 else "Delicate")
    return OwnedRelic(
        ga_handle=ga_handle, item_id=real_id_of(color), real_id=200, color=color,
        effects=eff, curses=[EMPTY, EMPTY, EMPTY], is_deep=is_deep, name="Owned", tier=tier,
    )


def real_id_of(_color):
    return 200 + 0x80000000


class _StubGen:
    """Deterministic generator: cycles a fixed list of GeneratedRelics."""
    def __init__(self, relics):
        self._relics = list(relics)
        self.i = 0

    def roll(self, **_kw) -> GeneratedRelic:
        r = self._relics[self.i % len(self._relics)]
        self.i += 1
        return r


def _ctx(eff_ids, name="B1") -> BuildContext:
    build = BuildDefinition(
        id="b", name=name, character="Wylder", include_deep=False, curse_max=1,
        groups=[WeightGroup(weight=100, effects=list(eff_ids))],
    )
    return BuildContext(build=build, hero_type=1, name=name)


@pytest.fixture(scope="module")
def wylder_eff(all_effects, ds: SourceDataHandler):
    """An effect that scores for Wylder (so the optimizer will place it)."""
    for e in all_effects:
        allow = e.get("allow_per_character")
        if allow is None or allow.get("Wylder", True):
            return e["id"]
    return all_effects[0]["id"]


# ---------------------------------------------------------------------------
# Pure helpers (no optimizer)
# ---------------------------------------------------------------------------

def test_reserve_handles_unique_and_avoids_taken():
    orig = {_SYNTH_HANDLE_BASE, _SYNTH_HANDLE_BASE + 2}
    taken = set(orig)
    hs = _reserve_handles(5, taken)
    assert len(hs) == 5 and len(set(hs)) == 5
    assert not (set(hs) & orig), "must skip already-taken handles"
    assert all(h >= _SYNTH_HANDLE_BASE for h in hs)


def test_generated_to_owned_maps_fields_and_tier():
    g = _gen([111, 222, EMPTY], color="Blue", is_deep=True, real_id=2010005)
    o = generated_to_owned(g, 0xC0F00099)
    assert o.ga_handle == 0xC0F00099
    assert o.real_id == 2010005
    assert o.color == "Blue"
    assert o.effects == [111, 222, EMPTY]
    assert o.is_deep is True
    assert o.effect_count == 2
    assert o.tier == "Polished"


# ---------------------------------------------------------------------------
# Engine (optimizer-backed)
# ---------------------------------------------------------------------------

def test_keeper_detection(ds, wylder_eff):
    keeper = _gen([wylder_eff, EMPTY, EMPTY], color="Red")
    dud = _gen([EMPTY, EMPTY, EMPTY], color="Red")   # no effect -> can't help -> dud
    res = bulk_acquire(
        builds=[_ctx([wylder_eff])], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=2)],
        generator=_StubGen([keeper, dud]), ds=ds, stop_mode="fixed",
        storage_cap_left=10, **_OPT,
    )
    assert res.generated == 2
    assert res.kept == 1, "the build-relevant relic should be kept"
    assert res.duds == 1
    assert wylder_eff in res.keepers[0].relic.effects
    assert res.keepers[0].builds == ["B1"]


def test_murk_math(ds, wylder_eff, all_effects):
    unrelated = next(e["id"] for e in all_effects if e["id"] != wylder_eff)
    keeper = _gen([wylder_eff, EMPTY, EMPTY], color="Red")
    dud = _gen([unrelated, EMPTY, EMPTY], color="Red")   # 1 effect, unweighted -> refund 150
    res = bulk_acquire(
        builds=[_ctx([wylder_eff])], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=2)],
        generator=_StubGen([keeper, dud]), ds=ds, storage_cap_left=10, **_OPT,
    )
    assert res.murk_gross_cost == 2 * 600
    assert res.murk_refunded == 150            # one dud, 1 effect, normal relic
    assert res.murk_after == 100_000 - (1200 - 150)
    assert res.murk_after >= 0


def test_storage_cap_limits_keepers(ds, wylder_eff, all_effects):
    e2 = next(e["id"] for e in all_effects if e["id"] != wylder_eff)
    k0 = _gen([wylder_eff, EMPTY, EMPTY], color="Red")
    k1 = _gen([e2, EMPTY, EMPTY], color="Red")
    res = bulk_acquire(
        builds=[_ctx([wylder_eff, e2])], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=2)],
        generator=_StubGen([k0, k1]), ds=ds, storage_cap_left=1, **_OPT,
    )
    assert res.kept == 1
    assert res.limited_by == "storage"


def test_murk_cap_never_negative(ds, wylder_eff):
    # Can only afford 1 relic (600) but ask for 3.
    keeper = _gen([wylder_eff, EMPTY, EMPTY], color="Red")
    res = bulk_acquire(
        builds=[_ctx([wylder_eff])], owned=[], current_murk=700,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=3)],
        generator=_StubGen([keeper]), ds=ds, storage_cap_left=10, **_OPT,
    )
    assert res.murk_after >= 0
    assert res.limited_by == "murk"


def test_unused_owned_handles_flags_only_unused(ds, wylder_eff):
    good = _owned([wylder_eff, EMPTY, EMPTY], color="Red", ga_handle=0xC1000001)
    bad = _owned([EMPTY, EMPTY, EMPTY], color="Red", ga_handle=0xC1000002)
    unused = unused_owned_handles([good, bad], [_ctx([wylder_eff])], ds, **_OPT)
    assert 0xC1000002 in unused, "the unused relic is a cull candidate"
    assert 0xC1000001 not in unused, "a placed relic is not culled"


# ---------------------------------------------------------------------------
# Rule matching (pure) + waterfall + rule-based cull
# ---------------------------------------------------------------------------

def test_relic_matches_rules_each_field():
    r = _owned([111, 222, EMPTY], color="Red")   # 2 effects, Red, not deep, no curse
    assert relic_matches_rules(r, [{"effect_counts": [2]}])
    assert not relic_matches_rules(r, [{"effect_counts": [1, 3]}])
    assert relic_matches_rules(r, [{"colors": ["Red", "Blue"]}])
    assert not relic_matches_rules(r, [{"colors": ["Blue"]}])
    assert relic_matches_rules(r, [{"is_deep": False}])
    assert not relic_matches_rules(r, [{"is_deep": True}])
    assert relic_matches_rules(r, [{"has_effect_ids": [111, 222]}])   # ALL present
    assert not relic_matches_rules(r, [{"has_effect_ids": [111, 999]}])  # 999 absent
    assert relic_matches_rules(r, [{"has_any_curse": False}])
    assert not relic_matches_rules(r, [{"has_any_curse": True}])


def test_rule_and_within_or_across_empty_matches_nothing():
    r = _owned([111, EMPTY, EMPTY], color="Red")   # 1 effect, Red
    assert relic_matches_rules(r, [{"effect_counts": [1], "colors": ["Red"]}])   # AND
    assert not relic_matches_rules(r, [{"effect_counts": [1], "colors": ["Blue"]}])
    assert relic_matches_rules(r, [{"colors": ["Blue"]}, {"colors": ["Red"]}])   # OR set
    assert not relic_matches_rules(r, [{}])        # empty rule matches nothing
    assert not relic_matches_rules(r, [])          # empty set
    assert not relic_matches_rules(r, None)


def test_curse_rules():
    cursed = OwnedRelic(
        ga_handle=0xC1000009, item_id=2010000 + 0x80000000, real_id=2010000, color="Red",
        effects=[111, EMPTY, EMPTY], curses=[555, EMPTY, EMPTY], is_deep=True,
        name="x", tier="Delicate")
    assert relic_matches_rules(cursed, [{"has_any_curse": True}])
    assert relic_matches_rules(cursed, [{"has_curse_ids": [555]}])
    assert not relic_matches_rules(cursed, [{"has_curse_ids": [999]}])
    assert relic_matches_rules(cursed, [{"is_deep": True}])


def test_inclusion_wins_over_exclusion(ds, wylder_eff):
    relic = _gen([wylder_eff, EMPTY, EMPTY], color="Red")   # matches both rules below
    res = bulk_acquire(
        builds=[_ctx([wylder_eff])], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=1)],
        generator=_StubGen([relic]), ds=ds, storage_cap_left=10,
        inclusion_rules=[{"colors": ["Red"]}],
        exclusion_rules=[{"effect_counts": [1]}],
        **_OPT,
    )
    assert res.kept == 1
    assert res.keepers[0].reason == "inclusion"


def test_exclusion_sells_a_build_relevant_relic(ds, wylder_eff):
    relic = _gen([wylder_eff, EMPTY, EMPTY], color="Red")   # would be a build keeper
    res = bulk_acquire(
        builds=[_ctx([wylder_eff])], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=1)],
        generator=_StubGen([relic]), ds=ds, storage_cap_left=10,
        exclusion_rules=[{"effect_counts": [1]}],   # "sell all 1-property"
        **_OPT,
    )
    assert res.kept == 0
    assert res.duds == 1


def test_inclusion_keeps_a_build_useless_relic(ds, wylder_eff):
    useless = _gen([EMPTY, EMPTY, EMPTY], color="Red")   # no build would place it
    res = bulk_acquire(
        builds=[_ctx([wylder_eff])], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=1)],
        generator=_StubGen([useless]), ds=ds, storage_cap_left=10,
        inclusion_rules=[{"colors": ["Red"]}],
        **_OPT,
    )
    assert res.kept == 1
    assert res.keepers[0].reason == "inclusion"


def test_murk_math_with_force_keep_and_force_sell(ds, wylder_eff):
    keep = _gen([wylder_eff, EMPTY, EMPTY], color="Blue")   # inclusion (Blue) -> kept
    sell = _gen([wylder_eff, EMPTY, EMPTY], color="Red")    # exclusion (Red) -> sold (refund 150)
    res = bulk_acquire(
        builds=[_ctx([wylder_eff])], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=2)],
        generator=_StubGen([keep, sell]), ds=ds, storage_cap_left=10,
        inclusion_rules=[{"colors": ["Blue"]}],
        exclusion_rules=[{"colors": ["Red"]}],
        **_OPT,
    )
    assert res.kept == 1 and res.keepers[0].reason == "inclusion"
    assert res.murk_gross_cost == 1200
    assert res.murk_refunded == 150
    assert res.murk_after == 100_000 - (1200 - 150)
    assert res.murk_after >= 0


def test_select_cull_handles_rules_and_protection(ds, wylder_eff):
    used = _owned([wylder_eff, EMPTY, EMPTY], color="Red", ga_handle=0xC1000001)
    unused = _owned([EMPTY, EMPTY, EMPTY], color="Red", ga_handle=0xC1000002)
    owned = [used, unused]
    builds = [_ctx([wylder_eff])]

    # build-aware only: unused is a candidate, used is not
    base = select_cull_handles(owned, builds, ds, **_OPT)
    assert 0xC1000002 in base and 0xC1000001 not in base

    # exclusion sells a build-used relic
    excl = select_cull_handles(
        owned, builds, ds, exclusion_rules=[{"has_effect_ids": [wylder_eff]}], **_OPT)
    assert 0xC1000001 in excl

    # inclusion protects an otherwise-cullable relic
    incl = select_cull_handles(
        owned, builds, ds, inclusion_rules=[{"colors": ["Red"]}], **_OPT)
    assert 0xC1000002 not in incl

    # explicitly protected handles are never culled
    prot = select_cull_handles(owned, builds, ds, protected_handles={0xC1000002}, **_OPT)
    assert 0xC1000002 not in prot


# ---------------------------------------------------------------------------
# No builds selected -> rules-only, NO optimizer runs (the hang fix)
# ---------------------------------------------------------------------------

def test_no_builds_keeps_all_generated(ds):
    a = _gen([111, EMPTY, EMPTY], color="Red")
    b = _gen([222, EMPTY, EMPTY], color="Blue")
    res = bulk_acquire(
        builds=[], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=2)],
        generator=_StubGen([a, b]), ds=ds, storage_cap_left=10, **_OPT,
    )
    assert res.generated == 2
    assert res.kept == 2, "no builds -> keep everything (rules-only, no optimizer)"
    assert res.duds == 0
    assert all(k.reason == "kept" for k in res.keepers)


def test_no_builds_exclusion_sells_matched(ds):
    keep = _gen([111, EMPTY, EMPTY], color="Blue")
    sell = _gen([222, EMPTY, EMPTY], color="Red")   # excluded by color
    res = bulk_acquire(
        builds=[], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=2)],
        generator=_StubGen([keep, sell]), ds=ds, storage_cap_left=10,
        exclusion_rules=[{"colors": ["Red"]}], **_OPT,
    )
    assert res.kept == 1 and res.duds == 1
    assert res.keepers[0].relic.color == "Blue"


def test_no_builds_inclusion_still_wins(ds):
    r = _gen([111, EMPTY, EMPTY], color="Red")   # matches both rules
    res = bulk_acquire(
        builds=[], owned=[], current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=1)],
        generator=_StubGen([r]), ds=ds, storage_cap_left=10,
        inclusion_rules=[{"colors": ["Red"]}],
        exclusion_rules=[{"effect_counts": [1]}], **_OPT,
    )
    assert res.kept == 1 and res.keepers[0].reason == "inclusion"


def test_no_builds_cull_is_exclusion_only(ds):
    a = _owned([111, EMPTY, EMPTY], color="Red", ga_handle=0xC1000001)
    b = _owned([222, EMPTY, EMPTY], color="Blue", ga_handle=0xC1000002)
    owned = [a, b]
    # no builds + no rules -> nothing culled (no optimizer, no exclusion match)
    assert select_cull_handles(owned, [], ds, **_OPT) == []
    # no builds + exclusion -> only the matched relic
    out = select_cull_handles(owned, [], ds, exclusion_rules=[{"colors": ["Red"]}], **_OPT)
    assert out == [0xC1000001]


def test_progress_callback_fires_per_build(ds, wylder_eff):
    calls: list[tuple[int, int, str]] = []
    keeper = _gen([wylder_eff, EMPTY, EMPTY], color="Red")
    bulk_acquire(
        builds=[_ctx([wylder_eff], "B1"), _ctx([wylder_eff], "B2")], owned=[],
        current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=1)],
        generator=_StubGen([keeper]), ds=ds, storage_cap_left=10,
        progress=lambda i, t, name: calls.append((i, t, name)), **_OPT,
    )
    assert [c[0] for c in calls] == [1, 2]     # one call per build, in order
    assert all(c[1] == 2 for c in calls)
    assert {c[2] for c in calls} == {"B1", "B2"}


def test_cull_progress_callback_fires(ds, wylder_eff):
    calls: list[tuple[int, int, str]] = []
    owned = [_owned([wylder_eff, EMPTY, EMPTY], color="Red", ga_handle=0xC1000001)]
    select_cull_handles(
        owned, [_ctx([wylder_eff], "B1")], ds,
        progress=lambda i, t, name: calls.append((i, t, name)), **_OPT)
    assert calls and calls[0] == (1, 1, "B1")


# ---------------------------------------------------------------------------
# Owned-only solve cache: per-build purchase skip + purchase/cull sharing
# ---------------------------------------------------------------------------

class _CountingOptimizer:
    """Records the inventory size of every optimize_all_vessels call."""

    def __init__(self, monkeypatch):
        from nrplanner.optimizer import VesselOptimizer
        self.inventory_sizes: list[int] = []
        original = VesselOptimizer.optimize_all_vessels

        def _wrapped(opt_self, build, inventory, *args, **kwargs):
            self.inventory_sizes.append(len(inventory))
            return original(opt_self, build, inventory, *args, **kwargs)

        monkeypatch.setattr(VesselOptimizer, "optimize_all_vessels", _wrapped)


def test_purchase_pass_skips_builds_with_no_relevant_candidates(
    ds, wylder_eff, monkeypatch
):
    """A build none of the generated relics can score for must not pay the
    enlarged owned+candidates solve — its result is the owned-only one."""
    counter = _CountingOptimizer(monkeypatch)
    keeper = _gen([wylder_eff, EMPTY, EMPTY], color="Red")
    # Different content from the roll — the owned-duplicate dud rule must not fire.
    owned = [_owned([111, EMPTY, EMPTY], ga_handle=0xC1000001)]
    relevant = _ctx([wylder_eff], "Relevant")
    unrelated = _ctx([999999999], "Unrelated")  # nothing rolled matches it

    res = bulk_acquire(
        builds=[relevant, unrelated], owned=owned, current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=1)],
        generator=_StubGen([keeper]), ds=ds, stop_mode="fixed",
        storage_cap_left=10, **_OPT,
    )
    assert res.kept == 1 and res.keepers[0].builds == ["Relevant"]
    # Relevant solved over owned+1 candidate; Unrelated over owned only.
    assert sorted(counter.inventory_sizes) == [len(owned), len(owned) + 1]


def test_owned_solve_shared_between_purchase_and_cull(ds, wylder_eff, monkeypatch):
    """The purchase pass's owned-only solve for a skipped build must be reused
    by the cull pass through the shared cache (one solve per build per plan)."""
    counter = _CountingOptimizer(monkeypatch)
    keeper = _gen([wylder_eff, EMPTY, EMPTY], color="Red")
    owned = [_owned([111, EMPTY, EMPTY], ga_handle=0xC1000001)]
    relevant = _ctx([wylder_eff], "Relevant")
    unrelated = _ctx([999999999], "Unrelated")
    cache: dict[str, set] = {}

    bulk_acquire(
        builds=[relevant, unrelated], owned=owned, current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=1)],
        generator=_StubGen([keeper]), ds=ds, stop_mode="fixed",
        storage_cap_left=10, owned_used_fps=cache, **_OPT,
    )
    purchase_calls = len(counter.inventory_sizes)
    assert purchase_calls == 2  # enlarged (Relevant) + owned-only (Unrelated)

    select_cull_handles(
        owned, [relevant, unrelated], ds, owned_used_fps=cache, **_OPT)
    cull_calls = len(counter.inventory_sizes) - purchase_calls
    # Unrelated's owned-only result is cached; only Relevant solves again.
    assert cull_calls == 1


def test_seeded_cache_skips_all_solves(ds, monkeypatch):
    """A pre-seeded cache (snapshot reuse) must fully replace optimizer runs
    when no generated relic is relevant to any build."""
    counter = _CountingOptimizer(monkeypatch)
    junk = _gen([EMPTY, EMPTY, EMPTY], color="Red")
    owned = [_owned([111, EMPTY, EMPTY], ga_handle=0xC1000001)]
    ctx = _ctx([999999999], "Seeded")
    ctx.build_id = "seed-build-id"
    cache: dict[str, set] = {"seed-build-id": set()}

    bulk_acquire(
        builds=[ctx], owned=owned, current_murk=100_000,
        buckets=[PurchaseBucket(False, "1.03", 600, quantity=1)],
        generator=_StubGen([junk]), ds=ds, stop_mode="fixed",
        storage_cap_left=10, owned_used_fps=cache, **_OPT,
    )
    cull = select_cull_handles(owned, [ctx], ds, owned_used_fps=cache, **_OPT)
    assert counter.inventory_sizes == [], "seeded cache must avoid every solve"
    # Empty used-set seed -> the owned relic is unused -> cull candidate.
    assert cull == [0xC1000001]
