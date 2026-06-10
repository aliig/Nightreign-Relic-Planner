"""Accuracy test — compares optimizer output against brute-force on every vessel.

Verifies that the optimizer never returns a score lower than the true
optimum for any vessel.  The brute force is built to be a sound oracle:

- Candidate filtering and branch-and-bound pruning use the sum of POSITIVE
  effect weights as the upper bound.  A relic's in-context score can exceed
  its *net* pre-score (e.g. a negatively-weighted duplicate dedups to 0), so
  net-based bounds and filters are NOT admissible once negative weights or
  curse weights are in play.
- A relic with context score <= 0 is only skipped when it can neither (a)
  unlock a desired excluded-category effect for a later placement (placing
  the desired effect flips later same-category competitors from -penalty to
  0), nor (b) pre-pay a shared negative effect (a later copy of a
  negatively-weighted no_stack/unique effect dedups to 0 against it).
  These are the only two mechanisms by which adding a relic can RAISE other
  relics' scores.
- Leaves that violate the excluded-stacking-category validity rule (an
  undesired competitor placed left of, or without, the desired effect) are
  rejected, mirroring the optimizer's post-hoc filter.
- The winning brute-force assignment is additionally re-scored through the
  optimizer's final result builder (tier-family direction correction
  included), and the optimizer must match or beat that final-model score too.

Run with:  uv run pytest nrplanner/tests/test_accuracy_vs_bruteforce.py -v -s
"""
import json
import tempfile
import time
from collections import Counter
from pathlib import Path

import pytest

from nrplanner import (
    BuildScorer,
    SourceDataHandler,
    VesselOptimizer,
    decrypt_sl2,
    discover_characters,
    parse_relics,
)
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import (
    BuildDefinition,
    OwnedRelic,
    RelicInventory,
    VesselState,
    WeightGroup,
)

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent
    / "backend" / "tests" / "fixtures" / "NR0000.sl2"
)

_GUARDIAN_CHAR_SKILL = 6500100
_GUARDIAN_STR_DEX    = 6641000
_GUARDIAN_HP_RESTORE = 7012000
_GUARDIAN_SHOCKWAVE  = 7033400
_DAMAGE_NEG_LOW_HP  = 340800
_PHYS_ATK_UP_3      = 6001400
_VIGOR_3            = 7000002


def _make_guardian_build() -> BuildDefinition:
    return BuildDefinition(
        id="guardian-accuracy",
        name="accuracy (Guardian)",
        character="Guardian",
        groups=[
            WeightGroup(weight=10, effects=[
                _GUARDIAN_CHAR_SKILL, _GUARDIAN_STR_DEX,
                _GUARDIAN_HP_RESTORE, _GUARDIAN_SHOCKWAVE,
            ]),
            WeightGroup(weight=5, effects=[_DAMAGE_NEG_LOW_HP, _PHYS_ATK_UP_3]),
            WeightGroup(weight=1, effects=[_VIGOR_3]),
        ],
        required_effects=[],
        excluded_effects=[],
        excluded_stacking_categories=[300, 6630000],
        include_deep=True,
        curse_max=1,
    )


def _make_simple_build() -> BuildDefinition:
    """Single-group build — different scoring profile."""
    return BuildDefinition(
        id="simple-accuracy",
        name="accuracy (simple)",
        character="Guardian",
        groups=[
            WeightGroup(weight=10, effects=[_GUARDIAN_CHAR_SKILL, _GUARDIAN_STR_DEX]),
        ],
        required_effects=[],
        excluded_effects=[],
        include_deep=True,
        curse_max=1,
    )


def _make_curse_heavy_build() -> BuildDefinition:
    """Build with curse penalties — tests curse_max / default_curse_weight."""
    return BuildDefinition(
        id="curse-accuracy",
        name="accuracy (curse-heavy)",
        character="Guardian",
        groups=[
            WeightGroup(weight=10, effects=[
                _GUARDIAN_CHAR_SKILL, _GUARDIAN_STR_DEX,
                _GUARDIAN_HP_RESTORE, _GUARDIAN_SHOCKWAVE,
            ]),
            WeightGroup(weight=5, effects=[_DAMAGE_NEG_LOW_HP, _PHYS_ATK_UP_3]),
        ],
        required_effects=[],
        excluded_effects=[],
        excluded_stacking_categories=[300, 6630000],
        include_deep=True,
        curse_max=0,
        default_curse_weight=-3,
    )


def _largest_families(ds: SourceDataHandler, n: int) -> list[str]:
    """Family names with the most resolved effect IDs (most commonly rolled)."""
    fams = sorted(
        ds.get_all_families_list(),
        key=lambda f: len(f["member_ids"]),
        reverse=True,
    )
    return [f["name"] for f in fams[:n]]


def _make_negative_weight_build(ds: SourceDataHandler) -> BuildDefinition:
    """Negative group weight + curse weights.

    Stresses the candidate filter and pruning bound: a relic carrying both a
    desired effect and a heavily penalized family member can have net
    pre-score <= 0 while still belonging to the true optimum (its negative
    part dedups to 0 against an already-placed copy).
    """
    penalized = _largest_families(ds, 1)
    return BuildDefinition(
        id="negative-accuracy",
        name="accuracy (negative weights)",
        character="Guardian",
        groups=[
            WeightGroup(weight=10, effects=[
                _GUARDIAN_CHAR_SKILL, _GUARDIAN_STR_DEX,
                _GUARDIAN_HP_RESTORE, _GUARDIAN_SHOCKWAVE,
            ]),
            WeightGroup(weight=5, effects=[_DAMAGE_NEG_LOW_HP, _PHYS_ATK_UP_3]),
            WeightGroup(weight=-9, families=penalized),
        ],
        include_deep=True,
        curse_max=1,
        default_curse_weight=-4,
    )


def _make_limits_build(ds: SourceDataHandler,
                       inventory: RelicInventory) -> BuildDefinition:
    """Per-effect count limits on the most common stackable effects.

    Limits only bite on effects with stacking type "stack" (everything else
    is already capped at one copy by stacking rules), so the targets are
    chosen from the live inventory.
    """
    counts: Counter = Counter()
    for r in inventory.relics:
        for e in set(r.all_effects):
            if ds.get_effect_stacking_type(e) == "stack":
                counts[e] += 1
    common = [e for e, _ in counts.most_common(3)]
    return BuildDefinition(
        id="limits-accuracy",
        name="accuracy (effect limits)",
        character="Guardian",
        groups=[
            WeightGroup(weight=10, effects=common),
            WeightGroup(weight=6, effects=[_GUARDIAN_CHAR_SKILL, _GUARDIAN_STR_DEX]),
        ],
        effect_limits={common[0]: 1} if common else {},
        include_deep=True,
        curse_max=1,
    )


def _make_family_build(ds: SourceDataHandler) -> BuildDefinition:
    """Family-weighted build — exercises magnitude weighting and the
    tier-family direction correction (no_stack bases vs unique variants)."""
    names = _largest_families(ds, 2)
    return BuildDefinition(
        id="family-accuracy",
        name="accuracy (families)",
        character="Guardian",
        groups=[
            WeightGroup(weight=10, families=names[:1]),
            WeightGroup(weight=4, families=names[1:2]),
            WeightGroup(weight=6, effects=[_GUARDIAN_CHAR_SKILL]),
        ],
        include_deep=True,
        curse_max=1,
    )


def _positive_pre_score(scorer: BuildScorer, relic: OwnedRelic,
                        build: BuildDefinition) -> int:
    """Admissible upper bound on the relic's in-context contribution.

    Every effect contributes at most max(0, resolved_weight) regardless of
    stacking state: stack -> w; unique/no_stack -> {w, 0, -penalty};
    limit reached -> 0; excluded-category -> {0, -penalty}; curse-excess -> <0.
    """
    total = 0
    for eff in relic.effects:
        if eff in (EMPTY_EFFECT, 0):
            continue
        cat, weight = scorer._resolve_category_and_weight(eff, build)
        if cat is not None and cat != "excluded" and weight > 0:
            total += weight
    for curse in relic.curses:
        if curse in (EMPTY_EFFECT, 0):
            continue
        cat, weight = scorer._resolve_category_and_weight(curse, build)
        if cat is not None and cat != "excluded":
            if weight > 0:
                total += weight
        elif cat is None and build.default_curse_weight > 0:
            total += build.default_curse_weight
    return total


def _desired_compat_unlocks(ds: SourceDataHandler, relic: OwnedRelic,
                            desired_compat: dict[int, set[int]]) -> frozenset[int]:
    """Excluded-category compat IDs whose desired effect this relic carries."""
    unlocks: set[int] = set()
    for eff in relic.all_effects:
        compat = ds.get_effect_conflict_id(eff)
        if compat == -1 or compat not in desired_compat:
            continue
        dset = desired_compat[compat]
        if eff in dset:
            unlocks.add(compat)
            continue
        text_id = ds.get_effect_text_id(eff)
        if text_id != -1 and text_id in dset:
            unlocks.add(compat)
    return frozenset(unlocks)


def _shared_negative_keys(
    ds: SourceDataHandler,
    scorer: BuildScorer,
    build: BuildDefinition,
    candidates_per_slot: list[list[tuple[int, int, OwnedRelic]]],
) -> dict[int, frozenset[int]]:
    """ga_handle -> negative dedupable effect keys shared with another candidate.

    A negatively-weighted no_stack/unique effect dedups to 0 when a copy is
    already placed, so a relic carrying one can pay the penalty once and make
    a later copy free — the second mechanism by which placing a relic can
    raise other relics' scores.  Keys are canonical (text_id when present).
    """
    per_relic: dict[int, set[int]] = {}
    key_counts: Counter = Counter()
    for cands in candidates_per_slot:
        for _, _, r in cands:
            if r.ga_handle in per_relic:
                continue
            keys: set[int] = set()
            for eff, is_curse in (
                [(e, False) for e in r.effects] + [(c, True) for c in r.curses]
            ):
                if eff in (EMPTY_EFFECT, 0):
                    continue
                cat, w = scorer._resolve_category_and_weight(eff, build)
                negative = (
                    (cat is not None and cat != "excluded" and w < 0)
                    or (cat is None and is_curse and build.default_curse_weight < 0)
                )
                if not negative:
                    continue
                if ds.get_effect_stacking_type(eff) == "stack":
                    continue  # stacking effects never dedup
                text_id = ds.get_effect_text_id(eff)
                keys.add(text_id if text_id != -1 else eff)
            per_relic[r.ga_handle] = keys
            key_counts.update(keys)
    return {
        handle: frozenset(k for k in keys if key_counts[k] >= 2)
        for handle, keys in per_relic.items()
        if any(key_counts[k] >= 2 for k in keys)
    }


def _brute_force_vessel(
    ds: SourceDataHandler,
    scorer: BuildScorer,
    optimizer: VesselOptimizer,
    build: BuildDefinition,
    inventory: RelicInventory,
    vessel_data: dict,
) -> tuple[int, list[OwnedRelic | None], int, int]:
    """Exhaustive brute-force for one vessel. Returns (score, assignment, leaves, pruned).

    Sound oracle for the in-context accumulation model: admissible
    positive-sum bounds, unlock-aware skipping, validity-checked leaves.
    """
    desired_cw = scorer.get_desired_conflict_weights(build)
    desired_compat = scorer.get_desired_compat_effects(build)
    effect_limit_by_name, family_limit_map = optimizer._prepare_limits(build)
    slot_colors = vessel_data["Colors"]
    num_slots = 6 if build.include_deep else 3

    candidates_per_slot: list[list[tuple[int, int, OwnedRelic]]] = []
    for i in range(num_slots):
        is_deep = i >= 3
        raw = inventory.get_candidates(slot_colors[i], is_deep)
        scored: list[tuple[int, int, OwnedRelic]] = []
        for r in raw:
            if scorer.has_excluded_effect(r, build, desired_compat):
                continue
            pos = _positive_pre_score(scorer, r, build)
            if pos <= 0:
                continue  # cannot contribute positively in any context
            net = scorer.score_relic(r, build)
            scored.append((net, pos, r))
        # Net-descending order finds good solutions early (better pruning);
        # the BOUND always uses the positive sums.
        scored.sort(key=lambda x: x[0], reverse=True)
        candidates_per_slot.append(scored)

    max_pos_per_slot = [max((p for _, p, _ in c), default=0) for c in candidates_per_slot]
    suffix_pos = [0] * (num_slots + 1)
    for s in range(num_slots - 1, -1, -1):
        suffix_pos[s] = suffix_pos[s + 1] + max_pos_per_slot[s]

    unlock_map: dict[int, frozenset[int]] = {}
    if desired_compat:
        for cands in candidates_per_slot:
            for _, _, r in cands:
                if r.ga_handle in unlock_map:
                    continue
                unlocks = _desired_compat_unlocks(ds, r, desired_compat)
                if unlocks:
                    unlock_map[r.ga_handle] = unlocks
    dedup_map = _shared_negative_keys(ds, scorer, build, candidates_per_slot)

    best_score = 0  # the empty layout is always available
    best_assignment: list[OwnedRelic | None] = [None] * num_slots
    leaves = 0
    pruned = 0

    state = VesselState(
        ds,
        desired_conflict_weights=desired_cw,
        desired_compat_effects=desired_compat,
        effect_limit_by_name=effect_limit_by_name,
        family_limit_map=family_limit_map,
    )

    def leaf(current: list, score: int) -> None:
        nonlocal best_score, best_assignment, leaves
        leaves += 1
        if score <= best_score:
            return
        if desired_compat:
            placed = [set(r.all_effects) if r else set() for r in current]
            if scorer.has_orphaned_excl_category_effects(placed, build, desired_compat):
                return  # invalid per game rules — optimizer rejects these too
        best_score = score
        best_assignment = list(current)

    def backtrack(slot: int, current: list, used: set, score: int) -> None:
        nonlocal pruned

        if slot == num_slots:
            leaf(current, score)
            return

        remaining = suffix_pos[slot + 1]

        for _net, pos, relic in candidates_per_slot[slot]:
            if relic.ga_handle in used:
                continue
            if score + pos + remaining <= best_score:
                pruned += 1
                continue

            ctx = scorer.score_relic_in_context(relic, build, state)
            if ctx <= 0:
                # Sound skip ONLY if this relic can neither place a desired
                # excluded-category effect that is still missing, nor pre-pay
                # a shared negative effect for a later duplicate.
                unlocks = unlock_map.get(relic.ga_handle, frozenset())
                dedups = dedup_map.get(relic.ga_handle, frozenset())
                if (unlocks <= state.desired_compat_placed
                        and dedups <= state.effect_ids):
                    continue
            if score + ctx + remaining <= best_score:
                pruned += 1
                continue

            current[slot] = relic
            used.add(relic.ga_handle)
            delta = state.place(relic)
            backtrack(slot + 1, current, used, score + ctx)
            used.discard(relic.ga_handle)
            state.remove(delta)

        current[slot] = None
        if score + remaining > best_score:
            backtrack(slot + 1, current, used, score)
        else:
            pruned += 1

    backtrack(0, [None] * num_slots, set(), 0)

    # Verify: re-score from scratch
    if best_score > 0:
        verify_state = VesselState(
            ds, desired_conflict_weights=desired_cw,
            desired_compat_effects=desired_compat,
            effect_limit_by_name=effect_limit_by_name,
            family_limit_map=family_limit_map,
        )
        verified = 0
        for relic in best_assignment:
            if relic is not None:
                verified += scorer.score_relic_in_context(relic, build, verify_state)
                verify_state.place(relic)
        assert best_score == verified, (
            f"BF self-check failed: accumulated={best_score}, verified={verified}")

    return best_score, best_assignment, leaves, pruned


def _final_model_score(
    optimizer: VesselOptimizer,
    scorer: BuildScorer,
    build: BuildDefinition,
    vessel_data: dict,
    assignment: list,
) -> int:
    """Score a BF assignment through the optimizer's final result builder.

    Includes the tier-family direction correction, so this is the score the
    app would actually report for the layout.
    """
    if not any(r is not None for r in assignment):
        return 0
    desired_cw = scorer.get_desired_conflict_weights(build)
    desired_compat = scorer.get_desired_compat_effects(build)
    effect_limit_by_name, family_limit_map = optimizer._prepare_limits(build)
    raw = [(r, 0) for r in assignment]
    result = optimizer._build_vessel_result(
        raw, len(assignment), vessel_data["Colors"], vessel_data, build,
        desired_cw, desired_compat, effect_limit_by_name, family_limit_map)
    return result.total_score


def _log(msg: str) -> None:
    print(msg, flush=True)


@pytest.fixture(scope="module")
def ds() -> SourceDataHandler:
    return SourceDataHandler(language="en_US")


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


class TestSyntheticSoundness:
    """Constructed inventories that fail when the solver filters or skips
    candidates by NET pre-score (no real save fixture required)."""

    def test_negative_duplicate_dedup_is_found(self, ds: SourceDataHandler) -> None:
        """Two relics share a penalized no_stack effect; the optimum places
        both (the second copy dedups to 0).  Each relic alone nets -2, so a
        net-based candidate filter or an unconditional ctx<=0 skip misses
        the 8-point optimum entirely."""
        no_stack_x = next(
            e["id"] for e in ds.get_all_effects_list()
            if ds.get_effect_stacking_type(e["id"]) == "no_stack"
            and ds.get_effect_conflict_id(e["id"]) == -1
            and ds.get_effect_family(e["id"]) is None
        )
        build = BuildDefinition(
            id="synthetic-negative",
            name="synthetic (negative dedup)",
            character="Guardian",
            groups=[
                WeightGroup(weight=10, effects=[
                    _GUARDIAN_CHAR_SKILL, _GUARDIAN_STR_DEX]),
                WeightGroup(weight=-12, effects=[no_stack_x]),
            ],
            include_deep=False,
            curse_max=5,
        )
        empty = EMPTY_EFFECT
        r1 = OwnedRelic(
            ga_handle=101, item_id=0, real_id=0, color="Red",
            effects=[_GUARDIAN_CHAR_SKILL, no_stack_x, empty],
            curses=[empty, empty, empty],
            is_deep=False, name="R1", tier="Polished",
        )
        r2 = OwnedRelic(
            ga_handle=102, item_id=0, real_id=0, color="Red",
            effects=[_GUARDIAN_STR_DEX, no_stack_x, empty],
            curses=[empty, empty, empty],
            is_deep=False, name="R2", tier="Polished",
        )
        inventory = RelicInventory.from_owned_relics([r1, r2])
        vessel_data = {
            "Name": "Synthetic Vessel", "Character": "All",
            "Colors": ("Red", "Red", "Red", "Red", "Red", "Red"),
            "unlockFlag": 0, "_id": 9999,
        }
        scorer = BuildScorer(ds)
        optimizer = VesselOptimizer(ds, scorer)

        results = optimizer.optimize(build, inventory, vessel_data, top_n=3)

        assert results, "optimizer returned no layouts at all"
        best = results[0]
        placed = sorted(
            a.relic.ga_handle for a in best.assignments if a.relic is not None)
        assert best.total_score == 8 and placed == [101, 102], (
            f"expected both relics for 8 pts (10 - 12 + 10, duplicate penalty "
            f"dedups to 0); got score={best.total_score}, placed={placed}"
        )


@pytest.mark.slow
@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present",
)
class TestAccuracyVsBruteForce:

    def _run_all_vessels(
        self, ds: SourceDataHandler, inventory: RelicInventory,
        build: BuildDefinition, hero_type: int, label: str,
    ) -> None:
        """Compare optimizer vs brute-force on every vessel for a build."""
        scorer = BuildScorer(ds)
        optimizer = VesselOptimizer(ds, scorer)
        vessels = list(ds.get_all_vessels_for_hero(hero_type))

        _log(f"\n{'='*70}")
        _log(f"ACCURACY TEST: {label}")
        _log(f"Vessels: {len(vessels)} | Relics: {len(inventory.relics)}")
        _log(f"{'='*70}")

        mismatches = []
        total_bf_time = 0
        total_opt_time = 0

        for v in vessels:
            vessel_data = dict(v)
            vessel_data["_id"] = v["vessel_id"]
            name = v["Name"]

            # Optimizer
            t0 = time.perf_counter()
            opt_results = optimizer.optimize(build, inventory, vessel_data, top_n=3)
            opt_time = time.perf_counter() - t0
            opt_score = opt_results[0].total_score if opt_results else 0
            total_opt_time += opt_time

            # Brute force
            t0 = time.perf_counter()
            bf_score, bf_assign, bf_leaves, bf_pruned = _brute_force_vessel(
                ds, scorer, optimizer, build, inventory, vessel_data)
            bf_time = time.perf_counter() - t0
            total_bf_time += bf_time

            # The accumulation-model optimum, and the same arrangement scored
            # through the final result builder (tier-family correction applied).
            bf_effective = max(bf_score, 0)
            bf_final = max(_final_model_score(
                optimizer, scorer, build, vessel_data, bf_assign), 0)
            match = opt_score >= bf_effective and opt_score >= bf_final
            status = "OK" if match else "MISMATCH"

            _log(f"  {name:30s}  opt={opt_score:3d}  bf={bf_effective:3d}  "
                 f"bf_final={bf_final:3d}  leaves={bf_leaves:5,}  "
                 f"pruned={bf_pruned:8,}  opt={opt_time*1000:5.0f}ms  "
                 f"bf={bf_time*1000:6.0f}ms  [{status}]")

            if not match:
                mismatches.append({
                    "vessel": name,
                    "opt_score": opt_score,
                    "bf_score": bf_effective,
                    "bf_final": bf_final,
                    "delta": max(bf_effective, bf_final) - opt_score,
                    "bf_assignment": bf_assign,
                })

        _log(f"\n  Total: opt={total_opt_time*1000:.0f}ms  bf={total_bf_time*1000:.0f}ms")

        if mismatches:
            _log(f"\n  FAILURES ({len(mismatches)}):")
            for m in mismatches:
                _log(f"    {m['vessel']}: optimizer={m['opt_score']}, "
                     f"brute_force={m['bf_score']}, bf_final={m['bf_final']}, "
                     f"delta={m['delta']}")
                for i, relic in enumerate(m["bf_assignment"]):
                    if relic:
                        effs = [ds.get_effect_name(e) for e in relic.all_effects]
                        _log(f"      BF slot {i}: {relic.name} — {', '.join(effs)}")
        else:
            _log(f"\n  ALL {len(vessels)} VESSELS MATCH")

        assert not mismatches, (
            f"{len(mismatches)} vessel(s) where optimizer < brute-force: "
            + ", ".join(f"{m['vessel']} (delta={m['delta']})" for m in mismatches)
        )

    def test_guardian_full_build(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Full Guardian build (3 groups + excluded categories) vs brute-force on all vessels."""
        self._run_all_vessels(
            ds, real_inventory, _make_guardian_build(), hero_type=2,
            label="Guardian full build (3 groups, excl categories)",
        )

    def test_simple_build(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Simple single-group build vs brute-force on all vessels."""
        self._run_all_vessels(
            ds, real_inventory, _make_simple_build(), hero_type=2,
            label="Guardian simple build (1 group, no exclusions)",
        )

    def test_curse_heavy_build(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Build with curse penalties vs brute-force on all vessels."""
        self._run_all_vessels(
            ds, real_inventory, _make_curse_heavy_build(), hero_type=2,
            label="Guardian curse-heavy build (curse_max=0, default_curse=-3)",
        )

    def test_negative_weight_build(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Negative group weights — stresses the net-vs-positive filter/bound."""
        self._run_all_vessels(
            ds, real_inventory, _make_negative_weight_build(ds), hero_type=2,
            label="Guardian negative-weight build (family penalty, curse weights)",
        )

    def test_limits_build(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Per-effect count limits on stackable effects."""
        self._run_all_vessels(
            ds, real_inventory, _make_limits_build(ds, real_inventory), hero_type=2,
            label="Guardian limits build (effect_limits on stackable effects)",
        )

    def test_family_build(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Family-weighted build — magnitude weighting + tier-family correction."""
        self._run_all_vessels(
            ds, real_inventory, _make_family_build(ds), hero_type=2,
            label="Guardian family build (magnitude-weighted families)",
        )
