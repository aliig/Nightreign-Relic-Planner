"""Solver engine selection (and, from Phase 1, the Rust bridge).

The free-slot solver has two interchangeable implementations behind one seam
(``VesselOptimizer._solve_free_slots_*``).  ``NRPLANNER_SOLVER`` picks which:

    auto    (default) use the Rust extension when it imports, else Python
    rust    require the Rust extension; raise at import if it is missing
    python  always use the pure-Python solver

The switch exists only for the Rust migration: it is what lets the
differential parity test run both engines in one process.  It goes away with
the Python solver at switchover.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from nrplanner.constants import EMPTY_EFFECT

log = logging.getLogger(__name__)

VALID_ENGINES = ("auto", "rust", "python")

# The Rust extension module, or None when it is not installed.
try:  # pragma: no cover - depends on whether the wheel is built
    import nrplanner_core  # type: ignore
except ImportError:  # pragma: no cover
    nrplanner_core = None


def _resolve_default() -> str:
    """The engine every optimize() call uses unless it overrides it."""
    requested = os.environ.get("NRPLANNER_SOLVER", "auto").strip().lower()
    if requested not in VALID_ENGINES:
        raise ValueError(
            f"NRPLANNER_SOLVER={requested!r} is not one of {VALID_ENGINES}")
    if requested == "rust":
        if nrplanner_core is None:
            raise RuntimeError(
                "NRPLANNER_SOLVER=rust but the nrplanner_core extension is not "
                "installed — build it with "
                "`uv run maturin develop --release -m crates/nrplanner_core/Cargo.toml`"
            )
        return "rust"
    if requested == "python":
        return "python"
    return "rust" if nrplanner_core is not None else "python"


ENGINE: str = _resolve_default()

log.info("nrplanner solver engine=%s core=%s", ENGINE,
         "present" if nrplanner_core is not None else "absent")


def resolve_engine(engine: str | None) -> str:
    """Per-call engine override -> concrete engine name."""
    if engine is None:
        return ENGINE
    engine = engine.strip().lower()
    if engine not in VALID_ENGINES:
        raise ValueError(f"engine={engine!r} is not one of {VALID_ENGINES}")
    if engine == "auto":
        return ENGINE
    if engine == "rust" and nrplanner_core is None:
        raise RuntimeError(
            "engine='rust' requested but the nrplanner_core extension is not "
            "installed")
    return engine

# ---------------------------------------------------------------------------
# Compiled inventory bundle
# ---------------------------------------------------------------------------
#
# Every per-slot filter in `optimize()` except `get_candidates(color, is_deep)`
# depends only on (build, relic) and reads only fields in
# `BuildScorer._scoring_sig`.  So one compiled bundle is exact for a whole
# (scoring signature, inventory identity) pair, and shares the lifetime of the
# scorer's `_profile_memo` — invalidated at exactly the two points that memo
# is: `_ensure_build_cache` and `bind_inventory`.
#
# Pinned/excluded relic handles are deliberately NOT part of the signature, so
# they are filtered per vessel (see `slot_candidates`) rather than baked in.

# Requirement coverage and the leaf orphan check both travel as u64 bitmasks.
MAX_BITMASK_BITS = 64


@dataclass(slots=True)
class CoreBundle:
    """One compiled inventory plus the Python-side index it maps back to."""
    core: object                                   # CompiledInventory
    profiles: list                                 # index -> RelicProfile
    handle_to_index: dict[int, int]
    # (slot_color, is_deep) -> profile indices, stable net-desc.  Built lazily:
    # a build only ever touches the colours its hero's vessels actually have.
    pools: dict[tuple[str, bool], list[int]] = field(default_factory=dict)


class _Interner:
    """Raw game id / limit-key string -> dense index.

    One namespace for ids (effects, text aliases, exclusivity, compat, curses)
    and one for limit keys.  Interning only has to be injective: the solver
    keeps each collection in its own bitset, so a raw exclusivity id colliding
    numerically with an effect id is as harmless in Rust as it is in Python.
    """

    def __init__(self) -> None:
        self._map: dict = {}

    def __call__(self, key) -> int:
        got = self._map.get(key)
        if got is None:
            got = len(self._map)
            self._map[key] = got
        return got

    def maybe(self, key: int) -> int:
        """Intern, mapping the -1 "absent" sentinel through unchanged."""
        return -1 if key == -1 else self(key)

    def __len__(self) -> int:
        return len(self._map)


def _leaf_masks(scorer, ds, relic, checked_compats, desired_expanded):
    """(desired, undesired) bitmasks for the leaf orphan check.

    Mirrors the predicates in `has_orphaned_excl_category_effects`: the desired
    side matches raw effect ids against the desired set expanded with text-id
    aliases (no inert filter — that check does not apply one), the undesired
    side additionally skips inert effects and requires the effect's conflict id
    to BE the compat.
    """
    desired = undesired = 0
    for bit, compat in enumerate(checked_compats):
        expanded = desired_expanded[compat]
        for eff in relic.all_effects:
            if eff in expanded:
                desired |= 1 << bit
                continue
            if scorer._is_inert(eff):
                continue  # greyed out — never wins the compat
            if ds.get_effect_conflict_id(eff) == compat:
                undesired |= 1 << bit
    return desired, undesired


def _relic_unlocks_and_neg_keys(scorer, ds, build, relic, dce):
    """The per-relic half of `_ctx_helper_map`.

    ``unlocks``: excluded-category compat ids whose DESIRED effect this relic
    carries (placing it flips later same-category competitors from -penalty to
    0).  ``neg_keys``: canonical ids of negatively-weighted no_stack/unique
    effects — a later copy dedups to 0 against this one, so paying the penalty
    once can be globally optimal.  The ">= 2 candidates share the key" filter
    depends on which relics are eligible for a given vessel, so it stays in
    Rust.
    """
    unlocks: list[int] = []
    if dce:
        seen_compat: set[int] = set()
        for eff in relic.all_effects:
            if scorer._is_inert(eff):
                continue  # mirrors VesselState.place
            compat = ds.get_effect_conflict_id(eff)
            if compat == -1 or compat not in dce or compat in seen_compat:
                continue
            dset = dce[compat]
            text_id = ds.get_effect_text_id(eff)
            if eff in dset or (text_id != -1 and text_id in dset):
                seen_compat.add(compat)
                unlocks.append(compat)

    keys: list[int] = []
    seen_keys: set[int] = set()
    for eff, is_curse in ([(e, False) for e in relic.effects]
                          + [(c, True) for c in relic.curses]):
        if eff in (EMPTY_EFFECT, 0) or scorer._is_inert(eff):
            continue
        cat, weight = scorer._resolve_category_and_weight(eff, build)
        negative = (
            (cat is not None and cat != "excluded" and weight < 0)
            or (cat is None and is_curse and build.default_curse_weight < 0))
        if not negative:
            continue
        if ds.get_effect_stacking_type(eff) == "stack":
            continue  # stacking effects never dedup
        text_id = ds.get_effect_text_id(eff)
        key = text_id if text_id != -1 else eff
        if key not in seen_keys:
            seen_keys.add(key)
            keys.append(key)
    return unlocks, keys


# CSR blocks shipped to Rust: (offsets column, payload column).
_CSR_BLOCKS = (
    ("curse_off", "curse_ids"), ("pcurse_off", "pcurse_ids"),
    ("eff_off", "eff_ids"), ("excl_off", "excl_ids"),
    ("nsexcl_off", "nsexcl_ids"), ("nscompat_off", "nscompat_ids"),
    ("dcp_off", "dcp_ids"), ("limit_off", "limit_keys"),
    ("unlock_off", "unlock_ids"), ("neg_off", "neg_keys"),
)

_COLUMNS = (
    "handle", "static_score", "pos_bound", "net", "req_mask",
    "leaf_desired_mask", "leaf_undesired_mask",
    "dyn_off", "dyn_kind", "dyn_weight", "dyn_eff", "dyn_text", "dyn_excl",
    "dyn_compat", "dyn_penalty", "dyn_lname", "dyn_lname_max", "dyn_lfam",
    "dyn_lfam_max",
) + tuple(name for pair in _CSR_BLOCKS for name in pair)


def _compile_bundle(optimizer, build, inventory, elbn, flm,
                    req_specs) -> CoreBundle:
    """Compile every eligible relic into one Rust-side inventory.

    Applies exactly the build-dependent candidate filters `optimize()` applies
    per slot (excluded effects, then the positive-pre-score floor with the
    Required-carrier escape), so the only thing left per slot is the colour /
    deep split and this vessel's pinned/excluded handles.
    """
    if nrplanner_core is None:  # pragma: no cover - guarded by resolve_engine
        raise RuntimeError("the nrplanner_core extension is not installed")
    if len(req_specs) > MAX_BITMASK_BITS:
        raise ValueError(
            f"{len(req_specs)} Required entries exceeds the "
            f"{MAX_BITMASK_BITS}-bit coverage mask the solver uses")

    scorer = optimizer.scorer
    ds = optimizer.data_source
    scorer._ensure_build_cache(build)
    dce = scorer.get_desired_compat_effects(build) or {}

    # Excluded-category compats the leaf check actually inspects, in a stable
    # order so bit positions are reproducible across runs.
    excl_cats = set(build.excluded_stacking_categories)
    checked_compats = sorted(c for c in dce if c in excl_cats)
    if len(checked_compats) > MAX_BITMASK_BITS:
        raise ValueError(
            f"{len(checked_compats)} checked stacking categories exceeds the "
            f"{MAX_BITMASK_BITS}-bit leaf mask")
    desired_expanded: dict[int, set[int]] = {}
    for compat in checked_compats:
        expanded = set(dce[compat])
        for d in dce[compat]:
            text_id = ds.get_effect_text_id(d)
            if text_id != -1:
                expanded.add(text_id)
        desired_expanded[compat] = expanded

    ids = _Interner()
    limits = _Interner()
    cols: dict[str, list[int]] = {name: [] for name in _COLUMNS}
    for off_name, _payload in _CSR_BLOCKS:
        cols[off_name].append(0)
    cols["dyn_off"].append(0)

    profiles: list = []
    handle_to_index: dict[int, int] = {}

    for relic in inventory.relics:
        if scorer.has_excluded_effect(relic, build, dce):
            continue
        mask = optimizer._relic_req_mask(relic, req_specs) if req_specs else 0
        # A requirement carrier survives even at pos <= 0 — it can be mandatory
        # at a net loss.
        if mask == 0 and scorer.positive_pre_score(relic, build) <= 0:
            continue
        prof = scorer.compile_profile(relic, build, elbn, flm)

        handle_to_index[relic.ga_handle] = len(profiles)
        profiles.append(prof)

        cols["handle"].append(relic.ga_handle)
        cols["static_score"].append(prof.static_score)
        cols["pos_bound"].append(prof.pos_bound)
        cols["net"].append(prof.net)
        cols["req_mask"].append(mask)
        desired, undesired = _leaf_masks(
            scorer, ds, relic, checked_compats, desired_expanded)
        cols["leaf_desired_mask"].append(desired)
        cols["leaf_undesired_mask"].append(undesired)

        for (kind, weight, eff, text_id, excl, compat, penalty,
             lname, lfam) in prof.dyn:
            cols["dyn_kind"].append(kind)
            cols["dyn_weight"].append(weight)
            cols["dyn_eff"].append(ids(eff))
            cols["dyn_text"].append(ids.maybe(text_id))
            cols["dyn_excl"].append(ids.maybe(excl))
            cols["dyn_compat"].append(ids.maybe(compat))
            cols["dyn_penalty"].append(penalty)
            # Thresholds ride along per entry: an effect name that is also a
            # family name shares one counter but has two separate limits.
            cols["dyn_lname"].append(-1 if lname is None else limits(lname))
            cols["dyn_lname_max"].append(0 if lname is None else elbn[lname])
            cols["dyn_lfam"].append(-1 if lfam is None else limits(lfam))
            cols["dyn_lfam_max"].append(0 if lfam is None else flm[lfam])
        cols["dyn_off"].append(len(cols["dyn_kind"]))

        unlocks, neg_keys = _relic_unlocks_and_neg_keys(
            scorer, ds, build, relic, dce)
        for name, values in (
            ("curse_ids", prof.curse_ids),
            ("pcurse_ids", prof.penalized_curse_ids),
            ("eff_ids", prof.eff_set),
            ("excl_ids", prof.excl_set),
            ("nsexcl_ids", prof.ns_excl_set),
            ("nscompat_ids", prof.ns_compat_set),
            ("dcp_ids", prof.dcp_set),
            ("unlock_ids", unlocks),
            ("neg_keys", neg_keys),
        ):
            cols[name].extend(ids(v) for v in values)
        cols["limit_keys"].extend(limits(k) for k in prof.limit_keys)
        for off_name, payload in _CSR_BLOCKS:
            cols[off_name].append(len(cols[payload]))

    core = nrplanner_core.compile_inventory(cols, len(ids), len(limits))
    return CoreBundle(core=core, profiles=profiles,
                      handle_to_index=handle_to_index)


def get_bundle(optimizer, build, inventory, elbn, flm,
               req_specs) -> CoreBundle:
    """The compiled bundle for this (build, inventory), compiling on demand.

    Cached on the scorer next to `_profile_memo`, which it is derived from and
    therefore shares an invalidation with.
    """
    scorer = optimizer.scorer
    bundle = scorer._core_bundle
    if bundle is None:
        bundle = _compile_bundle(
            optimizer, build, inventory, elbn or {}, flm or {}, req_specs)
        scorer._core_bundle = bundle
    return bundle


def slot_candidates(bundle: CoreBundle, slot_color: str, is_deep: bool,
                    pinned: set[int], excluded: set[int]) -> list[int]:
    """Candidate profile indices for one slot, in stable net-desc order.

    The colour/deep pool is cached on the bundle; this vessel's pinned and
    excluded handles are filtered out afterwards, which leaves the survivors in
    the same relative order as filtering before the stable sort does in Python.
    """
    key = (slot_color, is_deep)
    pool = bundle.pools.get(key)
    if pool is None:
        profiles = bundle.profiles
        pool = [
            i for i, p in enumerate(profiles)
            if p.relic.is_deep == is_deep
            and (slot_color == "White" or p.relic.color == slot_color)
        ]
        pool.sort(key=lambda i: profiles[i].net, reverse=True)
        bundle.pools[key] = pool
    if not pinned and not excluded:
        return pool
    profiles = bundle.profiles
    return [i for i in pool
            if profiles[i].ga_handle not in pinned
            and profiles[i].ga_handle not in excluded]


def solve_free_slots(bundle: CoreBundle, cand_lists: list[list[int]],
                     top_n: int, curse_max: int, deadline_secs: float,
                     validate_leaves: bool, full_mask: int, pinned_mask: int):
    """Run the Rust solver and map profile indices back to OwnedRelics."""
    raw, truncated, nodes = nrplanner_core.solve_vessel(
        bundle.core, cand_lists, top_n, curse_max, deadline_secs,
        validate_leaves, full_mask, pinned_mask)
    profiles = bundle.profiles
    layouts = [
        [(profiles[i].relic if i >= 0 else None, score) for i, score in a]
        for a in raw
    ]
    return layouts, truncated, nodes
