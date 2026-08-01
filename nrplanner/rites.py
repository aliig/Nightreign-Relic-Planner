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
from collections.abc import Callable
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

# Empty effect/curse slot sentinels.
_EMPTY_SLOTS = {EMPTY_EFFECT, 0, -1}


def _matches_one(relic: OwnedRelic, rule: dict) -> bool:
    """True if `relic` satisfies every present condition of `rule` (AND).

    Conditions (all optional): effect_counts (relic.effect_count in set), colors
    (relic.color in set), is_deep (bool ==), has_effect_ids (relic has ALL), has_curse_ids
    (relic has ALL), has_any_curse (bool, whether relic has >=1 non-empty curse). An empty
    rule (no recognized conditions) matches NOTHING.
    """
    checked = False
    counts = rule.get("effect_counts")
    if counts:
        checked = True
        if relic.effect_count not in counts:
            return False
    colors = rule.get("colors")
    if colors:
        checked = True
        if relic.color not in colors:
            return False
    is_deep = rule.get("is_deep")
    if is_deep is not None:
        checked = True
        if bool(relic.is_deep) != bool(is_deep):
            return False
    has_effects = rule.get("has_effect_ids")
    if has_effects:
        checked = True
        eff = set(relic.effects)
        if not all(e in eff for e in has_effects):
            return False
    has_curses = rule.get("has_curse_ids")
    if has_curses:
        checked = True
        cur = set(relic.curses)
        if not all(c in cur for c in has_curses):
            return False
    has_any_curse = rule.get("has_any_curse")
    if has_any_curse is not None:
        checked = True
        present = any(c not in _EMPTY_SLOTS for c in relic.curses)
        if present != bool(has_any_curse):
            return False
    return checked


def relic_matches_rules(relic: OwnedRelic, rules: list[dict] | None) -> bool:
    """True if `relic` matches ANY rule in the set (OR). Empty/None set matches nothing."""
    return any(_matches_one(relic, r) for r in (rules or []))


@dataclass
class BuildContext:
    """A saved build resolved for optimization."""
    build: BuildDefinition
    hero_type: int          # 1-based (Wylder=1 .. Undertaker=10)
    name: str
    # DB build id (stringified UUID) when resolved from a saved build; "" for
    # inline/anonymous builds.  Keys the per-plan owned-only solve cache, so it
    # must be collision-free — names are not unique.
    build_id: str = ""


@dataclass
class PurchaseBucket:
    """One flatstone type to buy from."""
    is_deep: bool
    version: str            # "1.03" | "1.02"
    buy_cost: int           # Murk per relic
    quantity: int | None = None   # fixed-mode count (ignored in budget/all_murk modes)


@dataclass
class Keeper:
    """A generated relic worth keeping, and why."""
    relic: OwnedRelic
    builds: list[str] = field(default_factory=list)   # build names whose top-N use its content
    # Best 1-based loadout rank per entry in `builds` (aligned; 1 = the build's
    # single best loadout). 0 when unknown.
    ranks: list[int] = field(default_factory=list)
    reason: str = "build"                              # "build" | "inclusion"


@dataclass
class BulkResult:
    keepers: list[Keeper]
    generated: int          # relics actually bought (all_murk: the affordable prefix)
    kept: int
    duds: int
    murk_before: int        # the caller's wallet base (never includes extra_budget)
    murk_after: int         # >= 0; includes extra_budget (staged-sell refunds)
    murk_gross_cost: int    # total spent buying everything bought
    murk_refunded: int      # total credited from selling the duds (excludes extra_budget)
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
                 current_murk: int, gen_max: int,
                 extra_budget: int = 0) -> tuple[list[int], bool]:
    """How many relics to generate per bucket. Approximate — exact Murk is settled later."""
    if stop_mode == "fixed":
        counts = [max(0, b.quantity or 0) for b in buckets]
    else:
        if stop_mode == "budget" and budget is not None:
            target = budget
        else:
            target = current_murk + extra_budget
        target = max(0, target)
        share = target / len(buckets) if buckets else 0
        counts = []
        for b in buckets:
            if stop_mode == "all_murk":
                # Overshoot on purpose: assume every roll is a max-refund dud
                # (sell_value(3): scenic 600−550=50, deep 1800−1100=700) so the
                # settlement walk — not this estimate — decides where spending
                # stops, and refunds are never left unspent. gen_max caps rolls.
                net_per = max(1, b.buy_cost - sell_value(3, b.is_deep))
            else:
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


def _used_fps_from_results(results) -> set:
    """Content fingerprints of every relic placed across ranked VesselResults."""
    used = set()
    for res in results:
        for a in res.assignments:
            if a.relic is not None:
                used.add(fingerprint_owned(a.relic))
    return used


def _used_fp_ranks(results) -> dict:
    """Content fingerprint -> best (lowest) 1-based loadout rank it appears at.

    ``results`` is the globally ranked top-N list from ``optimize_all_vessels``
    (best first), so list position IS the rank shown on the optimizer page.
    """
    ranks: dict = {}
    for pos, res in enumerate(results, start=1):
        for a in res.assignments:
            if a.relic is not None:
                fp = fingerprint_owned(a.relic)
                if fp not in ranks:
                    ranks[fp] = pos
    return ranks


def _optimize_used_fps(build_ctx: BuildContext, inventory: RelicInventory,
                       optimizer: VesselOptimizer, executor, top_n: int,
                       max_per_vessel: int, deadline_secs: float) -> set:
    """Content fingerprints of every relic placed across a build's top-N loadouts."""
    results = optimizer.optimize_all_vessels(
        build_ctx.build, inventory, build_ctx.hero_type,
        top_n=top_n, max_per_vessel=max_per_vessel,
        executor=executor, deadline_secs=deadline_secs,
    )
    return _used_fps_from_results(results)


def _ctx_key(build_ctx: BuildContext) -> str:
    """Collision-free cache key for one build within a single plan."""
    return build_ctx.build_id or f"anon:{id(build_ctx)}"


def _owned_used_fps(build_ctx: BuildContext, owned: list[OwnedRelic],
                    optimizer: VesselOptimizer, executor, top_n: int,
                    max_per_vessel: int, deadline_secs: float,
                    cache: dict[str, set] | None,
                    futures: dict | None = None) -> set:
    """Used-fps of a build's top-N over the OWNED-ONLY inventory, memoized.

    ``cache`` is shared across the purchase and cull passes of one rites plan
    and may be pre-seeded by the backend from a fresh optimization snapshot
    (the stored top-N for exactly this build + inventory + run params), so the
    owned-only optimization runs at most once per build per plan — and not at
    all on a snapshot hit.

    ``futures`` (from ``submit_all_vessels`` over the owned inventory) collects
    an already-prefetched solve instead of submitting a fresh one; callers only
    presubmit for cache misses.
    """
    key = _ctx_key(build_ctx)
    if cache is not None and key in cache:
        return cache[key]
    if futures is not None:
        results = optimizer.collect_all_vessels(
            build_ctx.build, build_ctx.hero_type, futures, top_n=top_n,
            n_relics=len(owned))
        fps = _used_fps_from_results(results)
    else:
        fps = _optimize_used_fps(
            build_ctx, RelicInventory.from_owned_relics(owned), optimizer,
            executor, top_n, max_per_vessel, deadline_secs)
    if cache is not None:
        cache[key] = fps
    return fps


def bulk_acquire(*, builds: list[BuildContext], owned: list[OwnedRelic],
                 current_murk: int, buckets: list[PurchaseBucket],
                 generator: RelicGenerator, ds,
                 stop_mode: str = "fixed", budget: int | None = None,
                 inclusion_rules: list[dict] | None = None,
                 exclusion_rules: list[dict] | None = None,
                 storage_cap_left: int = 10**9,
                 extra_budget: int = 0,
                 extra_reserved_handles: set[int] | None = None,
                 scorer: BuildScorer | None = None,
                 optimizer: VesselOptimizer | None = None, executor=None,
                 top_n: int = 10, max_per_vessel: int = 3, deadline_secs: float = 6.0,
                 gen_max: int = 8000, max_candidates: int = 500,
                 progress: Callable[[int, int, str], None] | None = None,
                 rng: random.Random | None = None, seed: int | None = None,
                 owned_used_fps: dict[str, set] | None = None) -> BulkResult:
    """Bulk-generate purchases, keep only build-relevant relics, refund the rest.

    Returns a BulkResult with the keepers (tagged by build), counts, and the exact Murk
    delta (buys deducted, dud sells refunded). ``murk_after`` is guaranteed >= 0.

    Settlement depends on ``stop_mode``:

    * ``fixed`` / ``budget`` — one static settlement: ``storage_cap_left`` bounds
      keepers added (lowest priority trimmed first), then keepers/duds are
      dropped to fit the Murk you have (``current_murk + extra_budget`` — a
      staged sell's refund is spendable in every mode, since in-game the
      player can sell at the shop before buying).
    * ``all_murk`` — a chronological per-relic walk that emulates the in-game
      buy/sell cycle: each roll is bought in stream order (needs Murk and one
      free storage slot), keepers consume a slot, duds are sold back on the spot
      and their refunds fund further buying. ``storage_cap_left`` here means
      free slots under the pure in-game 1950 cap. ``extra_budget`` (refund value
      of the caller's already-staged sells of owned relics) is credited before
      the first buy; it is excluded from ``murk_gross_cost``/``murk_refunded``
      but included in ``murk_after``. ``owned`` must already exclude those
      staged-sold relics; pass their handles via ``extra_reserved_handles`` so
      synthetic handles cannot collide with them.

    ``owned_used_fps`` is the per-plan owned-only solve cache (see
    ``_owned_used_fps``): builds for which NO generated relic can score are
    resolved from it instead of re-optimizing the enlarged inventory — exact,
    because relics with positive_pre_score <= 0 never enter candidate lists.
    """
    scorer = scorer or BuildScorer(ds)
    optimizer = optimizer or VesselOptimizer(ds, scorer)

    def _bucket_rng(bucket: PurchaseBucket) -> random.Random:
        # Independent deterministic sub-stream per flatstone type: a bucket's
        # Nth roll depends only on (seed, is_deep, version), never on the other
        # buckets' presence or quantities. Anti-scum: toggling one category's
        # quantity can never perturb another category's rolls (a shared stream
        # would let "buy 1 deep first" re-roll every scenic). An explicit
        # ``rng`` overrides this with a single shared stream (test hook).
        if rng is not None:
            return rng
        if seed is None:
            return random.Random()
        return random.Random(f"{seed}|{int(bucket.is_deep)}|{bucket.version}")

    # 1. plan + 2. generate ------------------------------------------------------
    counts, gen_max_hit = _plan_counts(
        buckets, stop_mode, budget, current_murk, gen_max, extra_budget)
    taken = {o.ga_handle for o in owned} | set(extra_reserved_handles or ())
    handles = _reserve_handles(sum(counts), taken)

    generated: list[OwnedRelic] = []
    buy_cost_of: dict[int, int] = {}
    hi = 0
    for bucket, n in zip(buckets, counts):
        brng = _bucket_rng(bucket)
        for _ in range(n):
            g = generator.roll(is_deep=bucket.is_deep, version=bucket.version,
                               mode="random", rng=brng)
            o = generated_to_owned(g, handles[hi])
            hi += 1
            generated.append(o)
            buy_cost_of[o.ga_handle] = bucket.buy_cost

    def refund(o: OwnedRelic) -> int:
        return sell_value(o.effect_count, o.is_deep)

    # 3. classify (waterfall): inclusion keep > exclusion sell > build-aware/keep --
    owned_fps = {fingerprint_owned(o) for o in owned}
    pre_by_handle: dict[int, int] = {}
    keepers: list[Keeper] = []
    limited: str | None = "gen_max" if gen_max_hit else None

    if builds:
        # Build-aware path: rules override; otherwise keep only content a build's top-N
        # would place. Pre-filter with positive_pre_score to keep the optimizer input small.
        # Build-outer scoring: one scorer cache rebind per build (not per relic),
        # and the per-build positive sets drive the per-build solve skip below.
        pre_of: dict[int, int] = {}
        pos_handles_by_build: dict[str, set[int]] = {}
        for b in builds:
            pos_handles: set[int] = set()
            for o in generated:
                s = scorer.positive_pre_score(o, b.build)
                if s > 0:
                    pos_handles.add(o.ga_handle)
                    if s > pre_of.get(o.ga_handle, 0):
                        pre_of[o.ga_handle] = s
            pos_handles_by_build[_ctx_key(b)] = pos_handles

        force_keep: list[OwnedRelic] = []
        scored: list[tuple[int, OwnedRelic]] = []
        for o in generated:
            if relic_matches_rules(o, inclusion_rules):
                pre_by_handle[o.ga_handle] = pre_of.get(o.ga_handle, 0)
                force_keep.append(o)
                continue
            if relic_matches_rules(o, exclusion_rules):
                continue  # explicit "always sell" -> dud
            if fingerprint_owned(o) in owned_fps:
                continue  # you already own this content -> dud
            best_pre = pre_of.get(o.ga_handle, 0)
            if best_pre <= 0:
                continue  # cannot help any build -> dud
            pre_by_handle[o.ga_handle] = best_pre
            scored.append((best_pre, o))
        scored.sort(key=lambda x: x[0], reverse=True)
        if len(scored) > max_candidates:
            scored = scored[:max_candidates]  # rest -> duds
        candidates = [o for _, o in scored]

        # 4. optimize each build once over owned + forced keepers + candidates.
        #    Builds where NO generated relic scores positive skip the enlarged
        #    solve: those relics never enter candidate lists, so the build's
        #    top-N over owned+extras is exactly the owned-only result (served
        #    from the shared cache / snapshot seed when available).
        #    Depth-1 prefetch: the next solving build's vessel tasks are
        #    submitted before draining the current build's, so pool workers
        #    idle during a build's completion tail start early on the next.
        inv = RelicInventory.from_owned_relics(owned + force_keep + candidates)
        extra_handles = {o.ga_handle for o in force_keep + candidates}
        solve_flags = [
            bool(pos_handles_by_build[_ctx_key(b)] & extra_handles)
            for b in builds
        ]
        pending: tuple[int, dict] | None = None  # (build index, futures)

        def _prefetch_after(i0: int) -> None:
            nonlocal pending
            if executor is None or pending is not None:
                return
            for j in range(i0 + 1, len(builds)):
                if solve_flags[j]:
                    pending = (j, optimizer.submit_all_vessels(
                        builds[j].build, inv, builds[j].hero_type,
                        max_per_vessel=max_per_vessel, executor=executor,
                        deadline_secs=deadline_secs))
                    return

        # Solved builds map fp -> best loadout rank; skipped builds keep the
        # owned-only fp SET from the shared cache (a candidate fp can never
        # appear there — candidates are new content, owned dups are duds).
        build_used: dict[str, dict | set] = {}
        total_builds = len(builds)
        for i, b in enumerate(builds):
            if progress is not None:
                progress(i + 1, total_builds, b.name)
            if not solve_flags[i]:
                build_used[b.name] = _owned_used_fps(
                    b, owned, optimizer, executor, top_n, max_per_vessel,
                    deadline_secs, owned_used_fps)
                continue
            futures = None
            if pending is not None and pending[0] == i:
                futures = pending[1]
                pending = None
            if futures is None and executor is not None:
                futures = optimizer.submit_all_vessels(
                    b.build, inv, b.hero_type, max_per_vessel=max_per_vessel,
                    executor=executor, deadline_secs=deadline_secs)
            _prefetch_after(i)
            if futures is not None:
                build_used[b.name] = _used_fp_ranks(
                    optimizer.collect_all_vessels(
                        b.build, b.hero_type, futures, top_n=top_n,
                        n_relics=len(inv)))
            else:
                build_used[b.name] = _used_fp_ranks(
                    optimizer.optimize_all_vessels(
                        b.build, inv, b.hero_type, top_n=top_n,
                        max_per_vessel=max_per_vessel, executor=executor,
                        deadline_secs=deadline_secs))
        all_used: set = set()
        for s in build_used.values():
            all_used.update(s)

        # 5. keepers = inclusion-forced + build-used candidates --------------------
        seen_fp: set = set()
        for o in force_keep:            # explicit "always keep" wins the waterfall
            fp = fingerprint_owned(o)
            if fp in seen_fp:
                continue                # dedup identical copies
            seen_fp.add(fp)
            keepers.append(Keeper(relic=o, builds=[], reason="inclusion"))
        for o in candidates:            # new content placed in a build's top-N
            fp = fingerprint_owned(o)
            if fp in seen_fp or fp in owned_fps or fp not in all_used:
                continue
            seen_fp.add(fp)
            names: list[str] = []
            ranks: list[int] = []
            for name, used in build_used.items():
                if fp in used:
                    names.append(name)
                    ranks.append(used[fp] if isinstance(used, dict) else 0)
            keepers.append(Keeper(relic=o, builds=names, ranks=ranks,
                                  reason="build"))
    else:
        # No builds selected -> NO optimizer runs (instant). Keep every generated relic
        # except those an exclusion rule sells (inclusion still wins). Each kept instance
        # is a distinct purchased relic (no content dedup here).
        for o in generated:
            incl = relic_matches_rules(o, inclusion_rules)
            if not incl and relic_matches_rules(o, exclusion_rules):
                continue  # explicit "always sell"
            keepers.append(Keeper(relic=o, builds=[],
                                  reason="inclusion" if incl else "kept"))

    def _keep_priority(k: Keeper) -> tuple:
        # inclusion outranks build/keep; then pre-score, tier, deep (trim worst first).
        return (1 if k.reason == "inclusion" else 0,
                pre_by_handle.get(k.relic.ga_handle, 0),
                k.relic.effect_count,
                1 if k.relic.is_deep else 0)

    keepers.sort(key=_keep_priority, reverse=True)

    if stop_mode == "all_murk":
        # 6b. (all_murk) Chronological settlement walk — emulates the in-game
        #     buy/sell cycle at the shop: buy one relic (needs Murk AND one free
        #     storage slot, even if immediately resold), keep it (slot consumed)
        #     or sell it back on the spot (refund credited, slot released).
        #     Stops at the first unaffordable or unfittable roll; every later
        #     roll is un-bought entirely. Duds never occupy storage and their
        #     refunds immediately fund further buying — all legal in-game
        #     (purchase quantity 1-10 is selectable and selling is available at
        #     the shop), just smoother. Pre-owned relics are never sold here.
        keeper_handles = {k.relic.ga_handle for k in keepers}
        murk = current_murk + extra_budget  # staged-sell refunds land first
        slots_free = max(0, storage_cap_left)
        settled = gross = refunds = 0
        kept_handles: set[int] = set()
        for o in generated:                 # generation order == roll order
            cost = buy_cost_of[o.ga_handle]
            if slots_free <= 0:             # buying transits storage even if resold
                limited = "storage"
                break
            if murk - cost < 0:             # buying may not overdraw the wallet
                limited = "murk"
                break
            murk -= cost
            gross += cost
            settled += 1
            if o.ga_handle in keeper_handles:
                kept_handles.add(o.ga_handle)
                slots_free -= 1
            else:
                r = refund(o)
                murk += r
                refunds += r
        # Filtering the priority-sorted list keeps presentation order.
        keepers = [k for k in keepers if k.relic.ga_handle in kept_handles]
        return BulkResult(
            keepers=keepers,
            generated=settled,
            kept=len(keepers),
            duds=settled - len(keepers),
            murk_before=current_murk,
            murk_after=murk,
            murk_gross_cost=gross,
            murk_refunded=refunds,
            limited_by=limited,
        )

    # 6. storage cap (fixed/budget: drop lowest-priority keepers first) ----------
    if storage_cap_left is not None and len(keepers) > storage_cap_left:
        keepers = keepers[:max(0, storage_cap_left)]
        limited = "storage"

    # 7. Murk settlement (guarantee murk_after >= 0) -----------------------------
    #    The wallet includes extra_budget (staged-sell refunds): in-game the
    #    player can sell those relics at the shop before buying, so the refund
    #    is spendable in every mode, not just the all_murk cycle walk.
    wallet = current_murk + extra_budget
    keeper_handles = {k.relic.ga_handle for k in keepers}
    gross = sum(buy_cost_of[o.ga_handle] for o in generated)
    refunds = sum(refund(o) for o in generated if o.ga_handle not in keeper_handles)
    net = gross - refunds

    if wallet - net < 0:
        limited = "murk"
        # 7a. drop lowest-priority keepers first (build before inclusion); a dropped
        #     keeper becomes a refunded dud, which improves affordability.
        keepers.sort(key=_keep_priority)
        drop = 0
        while wallet - net < 0 and drop < len(keepers):
            net -= refund(keepers[drop].relic)
            drop += 1
        keepers = keepers[drop:]
        keeper_handles = {k.relic.ga_handle for k in keepers}
        # 7b. still negative -> un-buy the most-expensive-net duds
        if wallet - net < 0:
            duds = sorted(
                (o for o in generated if o.ga_handle not in keeper_handles),
                key=lambda o: buy_cost_of[o.ga_handle] - refund(o), reverse=True)
            removed: set[int] = set()
            for o in duds:
                if wallet - net >= 0:
                    break
                net -= (buy_cost_of[o.ga_handle] - refund(o))
                removed.add(o.ga_handle)
            generated = [o for o in generated if o.ga_handle not in removed]
        keepers.sort(key=_keep_priority, reverse=True)

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
        murk_after=wallet - net,
        murk_gross_cost=gross,
        murk_refunded=refunds,
        limited_by=limited,
    )


def unused_owned_handles(owned: list[OwnedRelic], builds: list[BuildContext], ds,
                         scorer: BuildScorer | None = None,
                         optimizer: VesselOptimizer | None = None, executor=None,
                         top_n: int = 10, max_per_vessel: int = 3,
                         deadline_secs: float = 6.0,
                         progress: Callable[[int, int, str], None] | None = None,
                         owned_used_fps: dict[str, set] | None = None
                         ) -> list[int]:
    """ga_handles of owned relics used by NO build's top-N (build-aware cull candidates).

    Content-equal duplicates: whichever copy the optimizer places is 'used'; the other is
    correctly reported as cullable.

    ``owned_used_fps`` is the per-plan owned-only solve cache shared with the
    purchase pass (and possibly snapshot-seeded) — each build's owned-only
    optimization runs at most once per plan.
    """
    scorer = scorer or BuildScorer(ds)
    optimizer = optimizer or VesselOptimizer(ds, scorer)

    # Depth-1 prefetch across cache-missing builds (see bulk_acquire).
    owned_inv = RelicInventory.from_owned_relics(owned)
    pending: tuple[int, dict] | None = None

    def _is_miss(j: int) -> bool:
        return owned_used_fps is None or _ctx_key(builds[j]) not in owned_used_fps

    def _prefetch_after(i0: int) -> None:
        nonlocal pending
        if executor is None or pending is not None:
            return
        for j in range(i0 + 1, len(builds)):
            if _is_miss(j):
                pending = (j, optimizer.submit_all_vessels(
                    builds[j].build, owned_inv, builds[j].hero_type,
                    max_per_vessel=max_per_vessel, executor=executor,
                    deadline_secs=deadline_secs))
                return

    used_fps: set = set()
    total = len(builds)
    for i, b in enumerate(builds):
        if progress is not None:
            progress(i + 1, total, b.name)
        futures = None
        if pending is not None and pending[0] == i:
            futures = pending[1]
            pending = None
        if futures is None and executor is not None and _is_miss(i):
            futures = optimizer.submit_all_vessels(
                b.build, owned_inv, b.hero_type, max_per_vessel=max_per_vessel,
                executor=executor, deadline_secs=deadline_secs)
        _prefetch_after(i)
        used_fps |= _owned_used_fps(b, owned, optimizer, executor, top_n,
                                    max_per_vessel, deadline_secs,
                                    owned_used_fps, futures=futures)

    unused: list[int] = []
    seen_used_fp: set = set()
    for o in owned:
        fp = fingerprint_owned(o)
        if fp in used_fps and fp not in seen_used_fp:
            seen_used_fp.add(fp)   # keep one copy per used content
            continue
        unused.append(o.ga_handle)
    return unused


def select_cull_handles(owned: list[OwnedRelic], builds: list[BuildContext], ds, *,
                        inclusion_rules: list[dict] | None = None,
                        exclusion_rules: list[dict] | None = None,
                        protected_handles: set[int] | None = None,
                        scorer: BuildScorer | None = None,
                        optimizer: VesselOptimizer | None = None, executor=None,
                        top_n: int = 10, max_per_vessel: int = 3,
                        deadline_secs: float = 6.0,
                        progress: Callable[[int, int, str], None] | None = None,
                        owned_used_fps: dict[str, set] | None = None
                        ) -> list[int]:
    """ga_handles of owned relics to SELL, per the cull waterfall.

    A relic is a cull candidate when (used by NO build's top-N OR it matches an exclusion
    rule) AND it does NOT match an inclusion rule AND it is not protected (equipped /
    bookmarked). Inclusion always wins (protects) and protected relics are never sold.
    With no builds, culling is exclusion-rules-only (no optimizer runs).
    """
    protected = protected_handles or set()
    if builds:
        build_unused = set(unused_owned_handles(
            owned, builds, ds, scorer=scorer, optimizer=optimizer, executor=executor,
            top_n=top_n, max_per_vessel=max_per_vessel, deadline_secs=deadline_secs,
            progress=progress, owned_used_fps=owned_used_fps))
    else:
        build_unused: set = set()  # no builds -> exclusion-rules-only cull, no optimizer

    out: list[int] = []
    for o in owned:
        if o.ga_handle in protected:
            continue
        if relic_matches_rules(o, inclusion_rules):
            continue  # explicit keep protects from culling
        if o.ga_handle in build_unused or relic_matches_rules(o, exclusion_rules):
            out.append(o.ga_handle)
    return out
