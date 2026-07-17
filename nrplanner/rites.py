"""Relic Rites — bulk acquire + build-aware cull engine.

Turns Murk into build-relevant relics without the in-game grind: bulk-generate
purchases, keep only relics that a saved build's optimizer would actually place in its
top-N loadouts, and "sell" (refund) the rest — faithfully projecting the buy/sell Murk
economy. Also identifies owned relics no build uses (build-aware cull candidates).

Pure library code: no backend/FastAPI, no persistence. Keeper detection is by CONTENT
fingerprint (real_id + effects + curses), because the optimizer dedups content-equal
relics and may represent a generated relic by a content-equal owned copy.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from nrplanner.changes import fingerprint_owned
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.generator import GeneratedRelic, RelicGenerator
from nrplanner.models import BuildDefinition, OwnedRelic, RelicInventory
from nrplanner.optimizer import VesselOptimizer
from nrplanner.scoring import BuildScorer
from nrplanner.writer import sell_value

# Synthetic ga_handle base for generated relics (relic-type nibble 0xC). Every relic in
# an optimizer inventory needs a unique handle; generated relics have none.
_SYNTH_HANDLE_BASE = 0xC0F00000


@dataclass
class BuildContext:
    """A saved build resolved for optimization."""
    build: BuildDefinition
    hero_type: int          # 1-based (Wylder=1 .. Undertaker=10)
    name: str


@dataclass
class PurchaseBucket:
    """One flatstone type to buy from."""
    is_deep: bool
    version: str            # "1.03" | "1.02"
    buy_cost: int           # Murk per relic
    quantity: int | None = None   # fixed-mode count (ignored in budget/all_murk modes)


@dataclass
class Keeper:
    """A generated relic worth keeping, and which builds' top-N use its content."""
    relic: OwnedRelic
    builds: list[str] = field(default_factory=list)


@dataclass
class BulkResult:
    keepers: list[Keeper]
    generated: int
    kept: int
    duds: int
    murk_before: int
    murk_after: int
    murk_gross_cost: int    # total spent buying everything generated
    murk_refunded: int      # total credited from selling the duds
    limited_by: str | None  # "murk" | "storage" | "gen_max" | None


def _tier_for(effect_count: int) -> str:
    return "Grand" if effect_count >= 3 else ("Polished" if effect_count == 2 else "Delicate")


def generated_to_owned(g: GeneratedRelic, ga_handle: int) -> OwnedRelic:
    """Convert a rolled relic to an OwnedRelic with the given synthetic handle."""
    effects = list(g.effects)
    curses = list(g.curses)
    ec = sum(1 for e in effects if e not in (EMPTY_EFFECT, 0))
    return OwnedRelic(
        ga_handle=ga_handle,
        item_id=g.item_id,
        real_id=g.real_id,
        color=g.color,
        effects=effects,
        curses=curses,
        is_deep=g.is_deep,
        name=g.name,
        tier=_tier_for(ec),
    )


def _reserve_handles(count: int, taken: set[int]) -> list[int]:
    """Return `count` distinct synthetic handles, none already in `taken`."""
    out: list[int] = []
    c = 0
    while len(out) < count:
        h = _SYNTH_HANDLE_BASE + c
        c += 1
        if h in taken:
            continue
        taken.add(h)
        out.append(h)
    return out


def _plan_counts(buckets: list[PurchaseBucket], stop_mode: str, budget: int | None,
                 current_murk: int, gen_max: int) -> tuple[list[int], bool]:
    """How many relics to generate per bucket. Approximate — exact Murk is settled later."""
    if stop_mode == "fixed":
        counts = [max(0, b.quantity or 0) for b in buckets]
    else:
        target = budget if (stop_mode == "budget" and budget is not None) else current_murk
        target = max(0, target)
        share = target / len(buckets) if buckets else 0
        counts = []
        for b in buckets:
            # Estimate net cost per relic (most get sold back); mid refund estimate.
            net_per = max(1, b.buy_cost - sell_value(2, b.is_deep))
            counts.append(int(share // net_per))
    total = sum(counts)
    gen_max_hit = False
    if total > gen_max and total > 0:
        scale = gen_max / total
        counts = [int(c * scale) for c in counts]
        gen_max_hit = True
    return counts, gen_max_hit


def _optimize_used_fps(build_ctx: BuildContext, inventory: RelicInventory,
                       optimizer: VesselOptimizer, executor, top_n: int,
                       max_per_vessel: int, deadline_secs: float) -> set:
    """Content fingerprints of every relic placed across a build's top-N loadouts."""
    results = optimizer.optimize_all_vessels(
        build_ctx.build, inventory, build_ctx.hero_type,
        top_n=top_n, max_per_vessel=max_per_vessel,
        executor=executor, deadline_secs=deadline_secs,
    )
    used = set()
    for res in results:
        for a in res.assignments:
            if a.relic is not None:
                used.add(fingerprint_owned(a.relic))
    return used


def bulk_acquire(*, builds: list[BuildContext], owned: list[OwnedRelic],
                 current_murk: int, buckets: list[PurchaseBucket],
                 generator: RelicGenerator, ds,
                 stop_mode: str = "fixed", budget: int | None = None,
                 storage_cap_left: int = 10**9,
                 scorer: BuildScorer | None = None,
                 optimizer: VesselOptimizer | None = None, executor=None,
                 top_n: int = 10, max_per_vessel: int = 3, deadline_secs: float = 6.0,
                 gen_max: int = 8000, max_candidates: int = 500,
                 rng: random.Random | None = None, seed: int | None = None) -> BulkResult:
    """Bulk-generate purchases, keep only build-relevant relics, refund the rest.

    Returns a BulkResult with the keepers (tagged by build), counts, and the exact Murk
    delta (buys deducted, dud sells refunded). ``storage_cap_left`` bounds keepers added;
    ``murk_after`` is guaranteed >= 0 (trims keepers/duds to fit the Murk you have).
    """
    scorer = scorer or BuildScorer(ds)
    optimizer = optimizer or VesselOptimizer(ds, scorer)
    if rng is None:
        rng = random.Random(seed)

    # 1. plan + 2. generate ------------------------------------------------------
    counts, gen_max_hit = _plan_counts(buckets, stop_mode, budget, current_murk, gen_max)
    taken = {o.ga_handle for o in owned}
    handles = _reserve_handles(sum(counts), taken)

    generated: list[OwnedRelic] = []
    buy_cost_of: dict[int, int] = {}
    hi = 0
    for bucket, n in zip(buckets, counts):
        for _ in range(n):
            g = generator.roll(is_deep=bucket.is_deep, version=bucket.version,
                               mode="random", rng=rng)
            o = generated_to_owned(g, handles[hi])
            hi += 1
            generated.append(o)
            buy_cost_of[o.ga_handle] = bucket.buy_cost

    def refund(o: OwnedRelic) -> int:
        return sell_value(o.effect_count, o.is_deep)

    # 3. pre-filter: drop redundant + hopeless before the expensive optimize -----
    owned_fps = {fingerprint_owned(o) for o in owned}
    scored: list[tuple[int, OwnedRelic]] = []
    for o in generated:
        if fingerprint_owned(o) in owned_fps:
            continue  # you already own this content -> dud
        best_pre = max((scorer.positive_pre_score(o, b.build) for b in builds), default=0)
        if best_pre <= 0:
            continue  # cannot help any build -> dud
        scored.append((best_pre, o))
    scored.sort(key=lambda x: x[0], reverse=True)
    if len(scored) > max_candidates:
        scored = scored[:max_candidates]  # rest -> duds
    candidates = [o for _, o in scored]
    pre_by_handle = {o.ga_handle: p for p, o in scored}

    # 4. optimize each build over owned + candidates -----------------------------
    inv = RelicInventory.from_owned_relics(owned + candidates)
    build_used: dict[str, set] = {
        b.name: _optimize_used_fps(b, inv, optimizer, executor, top_n,
                                   max_per_vessel, deadline_secs)
        for b in builds
    }

    # 5. keepers = candidates used by some build's top-N (new content only) -------
    all_used: set = set()
    for s in build_used.values():
        all_used |= s
    keeper_fps = all_used - owned_fps

    keepers_by_fp: dict = {}
    for o in candidates:
        fp = fingerprint_owned(o)
        if fp in keeper_fps and fp not in keepers_by_fp:
            names = [name for name, used in build_used.items() if fp in used]
            keepers_by_fp[fp] = Keeper(relic=o, builds=names)
    keepers = list(keepers_by_fp.values())
    keepers.sort(key=lambda k: pre_by_handle.get(k.relic.ga_handle, 0), reverse=True)

    limited: str | None = "gen_max" if gen_max_hit else None

    # 6. storage cap -------------------------------------------------------------
    if storage_cap_left is not None and len(keepers) > storage_cap_left:
        keepers = keepers[:max(0, storage_cap_left)]
        limited = "storage"

    # 7. Murk settlement (guarantee murk_after >= 0) -----------------------------
    keeper_handles = {k.relic.ga_handle for k in keepers}
    gross = sum(buy_cost_of[o.ga_handle] for o in generated)
    refunds = sum(refund(o) for o in generated if o.ga_handle not in keeper_handles)
    net = gross - refunds

    if current_murk - net < 0:
        limited = "murk"
        # 7a. drop cheapest keepers (a dropped keeper becomes a refunded dud)
        keepers.sort(key=lambda k: (pre_by_handle.get(k.relic.ga_handle, 0),
                                    k.relic.effect_count))
        drop = 0
        while current_murk - net < 0 and drop < len(keepers):
            net -= refund(keepers[drop].relic)
            drop += 1
        keepers = keepers[drop:]
        keeper_handles = {k.relic.ga_handle for k in keepers}
        # 7b. still negative -> un-buy the most-expensive-net duds
        if current_murk - net < 0:
            duds = sorted(
                (o for o in generated if o.ga_handle not in keeper_handles),
                key=lambda o: buy_cost_of[o.ga_handle] - refund(o), reverse=True)
            removed: set[int] = set()
            for o in duds:
                if current_murk - net >= 0:
                    break
                net -= (buy_cost_of[o.ga_handle] - refund(o))
                removed.add(o.ga_handle)
            generated = [o for o in generated if o.ga_handle not in removed]
        keepers.sort(key=lambda k: pre_by_handle.get(k.relic.ga_handle, 0), reverse=True)

    # final authoritative tally
    keeper_handles = {k.relic.ga_handle for k in keepers}
    gross = sum(buy_cost_of[o.ga_handle] for o in generated)
    refunds = sum(refund(o) for o in generated if o.ga_handle not in keeper_handles)
    net = gross - refunds
    kept = len(keepers)
    return BulkResult(
        keepers=keepers,
        generated=len(generated),
        kept=kept,
        duds=len(generated) - kept,
        murk_before=current_murk,
        murk_after=current_murk - net,
        murk_gross_cost=gross,
        murk_refunded=refunds,
        limited_by=limited,
    )


def unused_owned_handles(owned: list[OwnedRelic], builds: list[BuildContext], ds,
                         scorer: BuildScorer | None = None,
                         optimizer: VesselOptimizer | None = None, executor=None,
                         top_n: int = 10, max_per_vessel: int = 3,
                         deadline_secs: float = 6.0) -> list[int]:
    """ga_handles of owned relics used by NO build's top-N (build-aware cull candidates).

    Content-equal duplicates: whichever copy the optimizer places is 'used'; the other is
    correctly reported as cullable.
    """
    scorer = scorer or BuildScorer(ds)
    optimizer = optimizer or VesselOptimizer(ds, scorer)
    inv = RelicInventory.from_owned_relics(owned)

    used_fps: set = set()
    for b in builds:
        used_fps |= _optimize_used_fps(b, inv, optimizer, executor, top_n,
                                       max_per_vessel, deadline_secs)

    unused: list[int] = []
    seen_used_fp: set = set()
    for o in owned:
        fp = fingerprint_owned(o)
        if fp in used_fps and fp not in seen_used_fp:
            seen_used_fp.add(fp)   # keep one copy per used content
            continue
        unused.append(o.ga_handle)
    return unused
