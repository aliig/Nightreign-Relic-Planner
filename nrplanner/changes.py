"""Change detection between optimization runs.

Pure, framework-agnostic logic shared by the web backend (and usable by the
CLI): content fingerprints for relics, hashable signatures of the three inputs
an optimization depends on (relics / build config / game data), and a diff of
two optimization results into a :class:`~nrplanner.models.BuildChange`.

This powers the "your build may have a better arrangement" notification shown
when a newer save file is uploaded.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Optional

from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import (
    BuildChange,
    BuildDefinition,
    OwnedRelic,
    RelicRef,
    VesselResult,
)

if TYPE_CHECKING:
    from nrplanner.data import SourceDataHandler

# A relic's content identity, stable across saves.  ga_handle is deliberately
# NOT included — the game reassigns handles between saves (see the upload
# handle-remap), so identity must be by content.
Fingerprint = tuple  # (real_id, e1, e2, e3, c1, c2, c3)

_EMPTY = (EMPTY_EFFECT, 0)

# Score deltas at or below this magnitude are treated as noise when a result's
# exhaustive search was truncated by its time budget (search order is then
# wall-clock dependent), so we don't cry "improved!" over nothing.
DEFAULT_EPSILON = 1


# ---------------------------------------------------------------------------
# Fingerprints & signatures
# ---------------------------------------------------------------------------

def relic_fingerprint(
    real_id: int, effects: Sequence[int], curses: Sequence[int]
) -> Fingerprint:
    """Canonical content fingerprint ``(real_id, e1, e2, e3, c1, c2, c3)``.

    Effects/curses are taken positionally and padded to three slots.  This is
    the single source of truth for relic identity across saves — the save-upload
    handle remap uses it too, so the two never drift apart.
    """
    e = list(effects) + [EMPTY_EFFECT, EMPTY_EFFECT, EMPTY_EFFECT]
    c = list(curses) + [EMPTY_EFFECT, EMPTY_EFFECT, EMPTY_EFFECT]
    return (real_id, e[0], e[1], e[2], c[0], c[1], c[2])


def fingerprint_owned(relic: OwnedRelic) -> Fingerprint:
    return relic_fingerprint(relic.real_id, relic.effects, relic.curses)


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def relics_signature(relics: Iterable[OwnedRelic]) -> str:
    """Order-independent hash of a relic inventory (multiset of fingerprints)."""
    return _sha(sorted(fingerprint_owned(r) for r in relics))


def relics_signature_from_fingerprints(fps: Iterable[Fingerprint]) -> str:
    return _sha(sorted(fps))


def build_signature(build: BuildDefinition) -> str:
    """Hash of the scoring-relevant build fields, **order included**.

    Order is preserved (lists are not sorted) because the optimizer is
    order-sensitive — e.g. leftmost-wins priority among excluded stacking
    categories — so reordering effects/groups can change the result and must
    therefore change the signature (which resets the build's snapshot).
    Excludes id/name/timestamps so a rename does not invalidate.  effect/family
    limits are maps, so their items are sorted for a canonical hash.
    """
    payload = {
        "character": build.character,
        "groups": [
            {
                "weight": g.weight,
                "effects": list(g.effects),
                "families": list(g.families),
            }
            for g in build.groups
        ],
        "required_effects": list(build.required_effects),
        "required_families": list(build.required_families),
        "excluded_effects": list(build.excluded_effects),
        "excluded_families": list(build.excluded_families),
        "include_deep": build.include_deep,
        "curse_max": build.curse_max,
        "default_curse_weight": build.default_curse_weight,
        "pinned_relics": list(build.pinned_relics),
        "excluded_stacking_categories": list(build.excluded_stacking_categories),
        "effect_limits": {str(k): v for k, v in sorted(build.effect_limits.items())},
        "family_limits": dict(sorted(build.family_limits.items())),
        "family_weight_floors": dict(sorted(build.family_weight_floors.items())),
    }
    return _sha(payload)


# ---------------------------------------------------------------------------
# Multiset diff & relevance (cheap, eager — runs at save upload)
# ---------------------------------------------------------------------------

def multiset_diff(
    old_fps: Iterable[Fingerprint], new_fps: Iterable[Fingerprint]
) -> tuple[list[Fingerprint], list[Fingerprint]]:
    """Return ``(added, removed)`` as multisets — duplicates counted correctly."""
    old_c = Counter(old_fps)
    new_c = Counter(new_fps)
    return list((new_c - old_c).elements()), list((old_c - new_c).elements())


def build_positive_sets(
    build: BuildDefinition, ds: "SourceDataHandler"
) -> tuple[set[int], set[str], set[str]]:
    """(effect IDs incl. family-expanded, family names, display names) the build *wants*.

    Mirrors the scorer's positive resolution chain (direct id → text_id →
    display name → family) as a SUPERSET: any relic that could earn a positive
    pre-score matches at least one of these sets.  Display names cover alias
    resolution (same name, unrelated IDs/text_ids) exactly like BuildScorer's
    name cache — built only from directly listed positive effect IDs.
    """
    eff_ids: set[int] = set(build.required_effects)
    fams: set[str] = set(build.required_families)
    for g in build.groups:
        if g.weight > 0:
            eff_ids.update(g.effects)
            fams.update(g.families)
    names: set[str] = set()
    for eid in eff_ids:
        name = ds.get_effect_name(eid)
        if name and name != "Empty" and not name.startswith("Effect "):
            names.add(name)
    expanded = set(eff_ids)
    for fam in fams:
        expanded |= ds.get_family_effect_ids(fam)
    return expanded, fams, names


def build_positive_effects(
    build: BuildDefinition, ds: "SourceDataHandler"
) -> tuple[set[int], set[str]]:
    """Effect IDs (incl. family-expanded) and family names the build *wants*."""
    pos_ids, pos_fams, _names = build_positive_sets(build, ds)
    return pos_ids, pos_fams


def _fp_all_ids(fp: Fingerprint) -> tuple[int, ...]:
    """All non-empty effect AND curse IDs (fp[1:7]) — curses resolve through the
    same positive chain in the scorer, so relevance must scan them too."""
    return tuple(e for e in fp[1:7] if e not in _EMPTY)


def _fp_has_curse(fp: Fingerprint) -> bool:
    return any(c not in _EMPTY for c in fp[4:7])


def _fp_is_relevant(
    fp: Fingerprint, pos_ids: set[int], pos_fams: set[str], pos_names: set[str],
    ds: "SourceDataHandler", curses_relevant: bool = False,
) -> bool:
    if curses_relevant and _fp_has_curse(fp):
        return True
    for e in _fp_all_ids(fp):
        if e in pos_ids:
            return True
        tid = ds.get_effect_text_id(e)
        if tid != -1 and tid in pos_ids:
            return True
        if pos_names and ds.get_effect_name(e) in pos_names:
            return True
        fam = ds.get_effect_family(e)
        if fam is not None and fam in pos_fams:
            return True
    return False


def relevant_to_build(
    build: BuildDefinition,
    added: Iterable[Fingerprint],
    removed: Iterable[Fingerprint],
    ds: "SourceDataHandler",
) -> tuple[int, int]:
    """``(relevant_added, relevant_removed)`` — relics sharing a wanted effect/family."""
    pos_ids, pos_fams, pos_names = build_positive_sets(build, ds)
    curses_rel = build.default_curse_weight > 0
    ra = sum(1 for fp in added
             if _fp_is_relevant(fp, pos_ids, pos_fams, pos_names, ds, curses_rel))
    rr = sum(1 for fp in removed
             if _fp_is_relevant(fp, pos_ids, pos_fams, pos_names, ds, curses_rel))
    return ra, rr


def relevant_fingerprints(
    build: BuildDefinition,
    relics: Iterable[tuple[Fingerprint, int]],
    ds: "SourceDataHandler",
) -> list[Fingerprint]:
    """Fingerprints of the relics that can affect this build's optimum.

    ``relics`` is (fingerprint, ga_handle) pairs.  A relic is included when its
    content is positively relevant (it could earn a positive pre-score, i.e.
    enter the optimizer's candidate lists) OR its handle is pinned by the build
    (pinned relics participate regardless of score).  Everything else is
    provably invisible to the solver: candidates require positive_pre_score>0,
    and every in-context structure (exclusion prefilter, ctx-helper map) is
    derived from candidates only.
    """
    pos_ids, pos_fams, pos_names = build_positive_sets(build, ds)
    curses_rel = build.default_curse_weight > 0
    pinned = set(build.pinned_relics)
    return [
        fp for fp, handle in relics
        if handle in pinned
        or _fp_is_relevant(fp, pos_ids, pos_fams, pos_names, ds, curses_rel)
    ]


def relevant_relics_signature(
    build: BuildDefinition,
    relics: Iterable[tuple[Fingerprint, int]],
    ds: "SourceDataHandler",
) -> str:
    """Order-independent hash of the build-relevant relic subset.

    Adding or removing relics that cannot affect this build's optimum leaves
    the signature unchanged, so the snapshot freshness gate keeps serving
    cached results across irrelevant inventory churn (the common case after a
    play session).  Same multiset-of-fingerprints shape as
    :func:`relics_signature`.
    """
    return _sha(sorted(relevant_fingerprints(build, relics, ds)))


# ---------------------------------------------------------------------------
# Result serialization & precise diff (runs on optimize)
# ---------------------------------------------------------------------------

def relic_ref_from_owned(r: OwnedRelic) -> RelicRef:
    return RelicRef(
        real_id=r.real_id,
        name=r.name,
        color=r.color,
        effects=list(r.effects),
        curses=list(r.curses),
        tier=r.tier,
        is_deep=r.is_deep,
    )


def serialize_layout(result: VesselResult) -> dict:
    """Compact, handle-free snapshot of one vessel result (for DB storage)."""
    return {
        "vessel_id": result.vessel_id,
        "vessel_name": result.vessel_name,
        "total_score": result.total_score,
        "search_truncated": result.search_truncated,
        "relics": [
            relic_ref_from_owned(a.relic).model_dump()
            for a in result.assignments
            if a.relic is not None
        ],
    }


def serialize_top_layouts(results: Sequence[VesselResult], n: int = 3) -> list[dict]:
    ranked = sorted(results, key=lambda r: r.total_score, reverse=True)
    return [serialize_layout(r) for r in ranked[:n]]


def layout_match_key(vessel_id: int, relics: Iterable[Fingerprint]) -> str:
    """Identity of a vessel setup: the vessel plus its relic content multiset.

    Deliberately order-insensitive.  A vessel's slots are colour-constrained, so
    two setups holding the same relics on the same vessel play identically no
    matter which same-colour slot each relic sits in — and the optimizer already
    collapses those into one canonical arrangement.  Interchangeable duplicate
    copies of a relic fall out for free (fingerprints are content, not
    ga_handle, so two copies produce the same entry).

    Used to recognise an in-game loadout preset as "this is optimizer result
    #N": the same relation the frontend's savedLoadoutMatch equivalent tier
    computes for the optimize page's "Saved" badge.
    """
    return _sha([vessel_id, sorted(relics)])


def result_match_key(result: VesselResult) -> str:
    return layout_match_key(
        result.vessel_id,
        (fingerprint_owned(a.relic) for a in result.assignments if a.relic is not None),
    )


def serialize_match_keys(results: Sequence[VesselResult]) -> list[str]:
    """Match keys in the results' own DISPLAY order — index 0 is the top card.

    NOT re-sorted by score (unlike ``serialize_top_layouts``, whose job is the
    diff baseline): the persisted order is the ranked order the optimize page
    renders (requirement-covering first, score-descending within each tier), so
    a key's index is exactly the rank the user sees.
    """
    return [result_match_key(r) for r in results]


def _result_relic_fps(result: VesselResult) -> Counter:
    return Counter(
        fingerprint_owned(a.relic) for a in result.assignments if a.relic is not None
    )


def _layout_relic_fps(layout: dict) -> Counter:
    return Counter(
        relic_fingerprint(r["real_id"], r["effects"], r["curses"])
        for r in layout.get("relics", [])
    )


def _entered_refs(result: VesselResult, wanted: Counter) -> list[RelicRef]:
    remaining = Counter(wanted)
    out: list[RelicRef] = []
    for a in result.assignments:
        if a.relic is None:
            continue
        fp = fingerprint_owned(a.relic)
        if remaining.get(fp, 0) > 0:
            remaining[fp] -= 1
            out.append(relic_ref_from_owned(a.relic))
    return out


def _left_refs(
    layout: dict, wanted: Counter, spare: Optional[Counter] = None
) -> list[RelicRef]:
    """Refs for the relics that dropped out of ``layout``.

    ``spare`` (when given) is the multiset of currently-owned copies NOT used by
    the new layout — i.e. what a dropped copy could still be sitting in the save
    as.  Consuming it per copy keeps the duplicate case honest: if the old layout
    used two copies and only one is still owned, one ref is ``still_owned=True``
    and the other False.  Left None when the caller passed no inventory.
    """
    remaining = Counter(wanted)
    unclaimed = Counter(spare) if spare is not None else None
    out: list[RelicRef] = []
    for r in layout.get("relics", []):
        fp = relic_fingerprint(r["real_id"], r["effects"], r["curses"])
        if remaining.get(fp, 0) > 0:
            remaining[fp] -= 1
            ref = RelicRef(**r)
            if unclaimed is not None:
                ref.still_owned = unclaimed[fp] > 0
                if ref.still_owned:
                    unclaimed[fp] -= 1
            out.append(ref)
    return out


def diff_results(
    old_layouts: Optional[list[dict]],
    new_results: Sequence[VesselResult],
    *,
    epsilon: int = DEFAULT_EPSILON,
    owned: Optional[Iterable[OwnedRelic]] = None,
) -> BuildChange:
    """Diff a stored snapshot's layouts against a fresh optimization.

    Compares the single best (highest-scoring) arrangement on each side and
    returns a BuildChange with status/scores/entered/left/reliable filled.
    Callers set build_id / slot_index / cause afterwards.

    ``owned`` is the inventory the new results were computed from.  Passing it
    marks every departed relic ``still_owned`` — the difference between "you no
    longer have this" and "your best setup stopped using it", which the layout
    diff alone cannot tell apart and which callers must not guess at.
    """
    new_best = max(new_results, key=lambda r: r.total_score, default=None)
    old_best = (
        max(old_layouts, key=lambda layout: layout["total_score"])
        if old_layouts
        else None
    )
    owned_fps = (
        Counter(fingerprint_owned(r) for r in owned) if owned is not None else None
    )

    # First time we've ever optimized this build+slot — there is no prior
    # arrangement to compare against, so nothing has "entered".  Record the
    # baseline only (otherwise run one would tag every relic in the layout NEW).
    if old_best is None:
        return BuildChange(
            status="new",
            best_after=new_best.total_score if new_best else None,
            reliable=not (new_best.search_truncated if new_best else False),
        )

    best_before = old_best["total_score"]

    # New inventory can no longer fill the vessel at all — nothing is in use, so
    # every owned copy is spare.
    if new_best is None:
        return BuildChange(
            status="degraded",
            best_before=best_before,
            left=_left_refs(old_best, _layout_relic_fps(old_best), owned_fps),
        )

    best_after = new_best.total_score
    delta = best_after - best_before

    new_fps = _result_relic_fps(new_best)
    old_fps = _layout_relic_fps(old_best)
    entered_fps = new_fps - old_fps
    left_fps = old_fps - new_fps

    truncated = new_best.search_truncated or bool(old_best.get("search_truncated"))

    if delta > epsilon:
        status = "improved"
    elif delta < -epsilon:
        status = "degraded"
    elif entered_fps or left_fps:
        status = "reordered"
    else:
        status = "unchanged"

    # An improvement/regression found under a truncated (non-exhaustive) search
    # is flagged unreliable rather than suppressed — the delta may be search noise.
    reliable = not (truncated and status in ("improved", "degraded"))

    return BuildChange(
        status=status,
        best_before=best_before,
        best_after=best_after,
        delta=delta,
        entered=_entered_refs(new_best, entered_fps),
        left=_left_refs(
            old_best, left_fps,
            None if owned_fps is None else owned_fps - new_fps,
        ),
        reliable=reliable,
    )
