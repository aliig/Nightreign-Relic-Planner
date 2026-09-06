"""Randomized (build, inventory) scenarios for solver parity + benchmarking.

Two generators, both driven by real game data:

``synthetic_scenarios``  hand-rolled builds and relics (an extension of the
    generators in test_profile_equivalence).  Relics here are NOT necessarily
    legal — the point is adversarial coverage of the scoring/placement rules
    (stacking categories, limits, requirements, pins, exclusions).

``legal_scenarios``  relics rolled by RelicGenerator, i.e. exactly what the
    game can produce.  Duplicate relics are rejected: identical relics are
    unusually easy for the solver to prune, so a duplicate-heavy inventory
    understates real search cost.

Both are imported by the parity test and by scripts/bench_solver.py, so they
live in the package rather than in a test module.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from nrplanner.constants import CHARACTER_NAMES, EMPTY_EFFECT
from nrplanner.data import SourceDataHandler
from nrplanner.generator import GenerationError, RelicGenerator
from nrplanner.models import (
    BuildDefinition, OwnedRelic, RelicInventory, VesselResult, WeightGroup,
)
from nrplanner.rites import generated_to_owned

# Playable Nightfarers ("All" is a UI filter, not a character).
PLAYABLE_CHARACTERS = [c for c in CHARACTER_NAMES if c != "All"]

COLORS = ["Red", "Blue", "Yellow", "Green"]

# Synthetic handles live well above any real ga_handle so a scenario inventory
# can never be confused with a parsed save's.
SYNTHETIC_HANDLE_BASE = 0xC1000000


@dataclass
class Scenario:
    """One solver input: a build plus the inventory it is solved against."""
    name: str
    build: BuildDefinition
    inventory: RelicInventory
    hero_type: int
    relics: list[OwnedRelic] = field(default_factory=list)


def _hero_type_for(character: str) -> int:
    """The vessel hero_type whose vessels this character can equip.

    1-based index into CHARACTER_NAMES, matching the AntiqueStandParam
    ``heroType`` column (1-10) — the same mapping the /optimize route uses.
    """
    return CHARACTER_NAMES.index(character) + 1


def _sample(rng: random.Random, pool: list, k: int) -> list:
    """rng.sample clamped to the pool size (small pools are legitimate)."""
    return rng.sample(pool, k=min(k, len(pool)))


def _random_build(rng: random.Random, ids: list[int], fams: list[str],
                  cat_members: list[int], handles: list[int],
                  character: str, include_deep: bool) -> BuildDefinition:
    """A build exercising every field the solver branches on.

    Extends test_profile_equivalence._random_build with the fields that only
    matter once a full vessel is solved: Required rows, excluded families,
    pinned/excluded relics, family weight floors and deep slots.
    """
    groups = []
    for _ in range(rng.randint(1, 4)):
        # Generous group sizes on purpose: the candidate pre-filter drops every
        # relic with a positive pre-score of 0, so thin builds produce
        # near-empty candidate lists and a search too shallow to prove
        # anything.
        effs = _sample(rng, ids, rng.randint(0, 12))
        if cat_members and rng.random() < 0.5:
            effs.append(rng.choice(cat_members))
        groups.append(WeightGroup(
            weight=rng.choice([-20, -5, 5, 10, 25, 40]),
            effects=effs,
            families=_sample(rng, fams, rng.randint(0, 2)),
        ))
    effect_limits = {}
    if rng.random() < 0.5:
        for eid in _sample(rng, ids, rng.randint(1, 2)):
            effect_limits[eid] = rng.randint(1, 2)
    family_limits = {}
    if rng.random() < 0.5:
        family_limits[rng.choice(fams)] = rng.randint(1, 2)
    family_weight_floors = {}
    if rng.random() < 0.3:
        family_weight_floors[rng.choice(fams)] = rng.choice([5, 10, 20])

    # Required rows are a hard constraint, so they must be satisfiable often
    # enough for the constrained code paths to actually run: draw them from
    # the same id pool the relics are drawn from.
    required_effects = _sample(rng, ids, rng.randint(0, 2))
    required_families = _sample(rng, fams, 1) if rng.random() < 0.25 else []

    pinned_relics = (
        _sample(rng, handles, rng.randint(1, 2))
        if handles and rng.random() < 0.25 else [])
    excluded_relics = (
        _sample(rng, [h for h in handles if h not in pinned_relics],
                rng.randint(1, 3))
        if handles and rng.random() < 0.3 else [])

    return BuildDefinition(
        id="scenario", name="scenario", character=character,
        groups=groups,
        required_effects=required_effects,
        required_families=required_families,
        excluded_effects=_sample(rng, ids, rng.randint(0, 2)),
        excluded_families=_sample(rng, fams, 1) if rng.random() < 0.2 else [],
        excluded_stacking_categories=(
            [300, 6630000] if rng.random() < 0.7 else []),
        effect_limits=effect_limits,
        family_limits=family_limits,
        family_weight_floors=family_weight_floors,
        pinned_relics=pinned_relics,
        excluded_relics=excluded_relics,
        include_deep=include_deep,
        default_curse_weight=rng.choice([-10, -5, 0, 3]),
        curse_max=rng.randint(0, 2),
    )


def _random_relic(rng: random.Random, pool: list[int],
                  cat_members: list[int], handle: int) -> OwnedRelic:
    effects = _sample(rng, pool, rng.randint(1, 3))
    if cat_members and rng.random() < 0.4:
        effects[rng.randrange(len(effects))] = rng.choice(cat_members)
    curses = _sample(rng, pool, rng.randint(0, 2))
    effects = (effects + [EMPTY_EFFECT] * 3)[:3]
    curses = (curses + [EMPTY_EFFECT] * 3)[:3]
    return OwnedRelic(
        ga_handle=handle, item_id=100 + 2147483648, real_id=100,
        color=rng.choice(COLORS), effects=effects, curses=curses,
        is_deep=rng.random() < 0.4, name=f"R{handle}", tier="Delicate",
    )


def _category_members(ds: SourceDataHandler, ids: list[int]) -> list[int]:
    """Effects belonging to one of the two commonly excluded stacking cats."""
    return [e for e in ids if ds.get_effect_conflict_id(e) in (300, 6630000)]


def synthetic_scenarios(ds: SourceDataHandler, seed: int,
                        n: int = 1, n_relics: int = 60) -> list[Scenario]:
    """Adversarial (build, inventory) pairs over real effect IDs."""
    rng = random.Random(seed)
    ids = [e["id"] for e in ds.get_all_effects_list()[:300]]
    ids += [999999901, 999999902]  # unknown ids: no family/text/name resolution
    fams = [f["name"] for f in ds.get_all_families_list()[:20]]
    cat_members = _category_members(ds, ids)

    out: list[Scenario] = []
    for k in range(n):
        relics = [
            _random_relic(rng, ids, cat_members, SYNTHETIC_HANDLE_BASE + i)
            for i in range(n_relics)
        ]
        character = rng.choice(PLAYABLE_CHARACTERS)
        build = _random_build(
            rng, ids, fams, cat_members, [r.ga_handle for r in relics],
            character, include_deep=rng.random() < 0.5)
        out.append(Scenario(
            name=f"synthetic-{seed}-{k}",
            build=build,
            inventory=RelicInventory.from_owned_relics(relics),
            hero_type=_hero_type_for(character),
            relics=relics,
        ))
    return out


def legal_relics(ds: SourceDataHandler, seed: int, n: int) -> list[OwnedRelic]:
    """``n`` DISTINCT relics the game could actually produce.

    Distinctness is by (effects, curses): duplicated relics prune unusually
    well (the solver's used-handle check kills whole subtrees), so an
    inventory full of copies is not representative of a real save.
    """
    rng = random.Random(seed)
    gen = RelicGenerator(ds)
    seen: set[tuple] = set()
    out: list[OwnedRelic] = []
    attempts = 0
    while len(out) < n and attempts < n * 20:
        attempts += 1
        try:
            g = gen.roll(is_deep=rng.random() < 0.35, rng=rng)
        except GenerationError:
            continue
        fp = (tuple(g.effects), tuple(g.curses))
        if fp in seen:
            continue
        seen.add(fp)
        out.append(generated_to_owned(g, SYNTHETIC_HANDLE_BASE + len(out)))
    return out


def common_effect_ids(relics: list[OwnedRelic], top: int = 150) -> list[int]:
    """The effect ids that occur most often across ``relics``.

    Builds weighted on rare ids leave almost every relic at a positive
    pre-score of 0, so they are filtered out before the solver ever sees them.
    Drawing weights from the common ids is what makes a scenario's candidate
    lists — and therefore its search — realistically large.
    """
    counts: dict[int, int] = {}
    for r in relics:
        for e in r.all_effects:
            counts[e] = counts.get(e, 0) + 1
    ranked = sorted(counts, key=lambda e: (-counts[e], e))
    return ranked[:top]


def legal_scenarios(ds: SourceDataHandler, seed: int, n: int = 1,
                    n_relics: int = 200,
                    relics: list[OwnedRelic] | None = None) -> list[Scenario]:
    """(build, inventory) pairs whose relics are all game-legal rolls.

    Pass ``relics`` to reuse one already-rolled inventory across many builds —
    the app's shape (one save, many builds), and much cheaper than rolling a
    fresh inventory per build.
    """
    rng = random.Random(seed ^ 0x5EED)
    if relics is None:
        relics = legal_relics(ds, seed, n_relics)
    ids = common_effect_ids(relics)
    fams = [f["name"] for f in ds.get_all_families_list()[:20]]
    cat_members = _category_members(ds, ids)

    inventory = RelicInventory.from_owned_relics(relics)
    handles = [r.ga_handle for r in relics]
    out: list[Scenario] = []
    for k in range(n):
        character = rng.choice(PLAYABLE_CHARACTERS)
        build = _random_build(
            rng, ids, fams, cat_members, handles,
            character, include_deep=rng.random() < 0.5)
        out.append(Scenario(
            name=f"legal-{seed}-{k}",
            build=build,
            inventory=inventory,
            hero_type=_hero_type_for(character),
            relics=relics,
        ))
    return out


def assignment_signature(vr: VesselResult) -> list[tuple[int | None, int]]:
    """The identity of a solved layout: (relic handle | None, score) per slot.

    Compared instead of the whole VesselResult so a parity failure points at
    the solver rather than at breakdown formatting.
    """
    return [
        (slot.relic.ga_handle if slot.relic else None, slot.score)
        for slot in vr.assignments
    ]
