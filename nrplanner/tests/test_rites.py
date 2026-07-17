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
