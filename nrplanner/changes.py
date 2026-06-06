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


def build_positive_effects(
    build: BuildDefinition, ds: "SourceDataHandler"
) -> tuple[set[int], set[str]]:
    """Effect IDs (incl. family-expanded) and family names the build *wants*."""
    eff_ids: set[int] = set(build.required_effects)
    fams: set[str] = set(build.required_families)
    for g in build.groups:
        if g.weight > 0:
            eff_ids.update(g.effects)
            fams.update(g.families)
    expanded = set(eff_ids)
    for fam in fams:
        expanded |= ds.get_family_effect_ids(fam)
    return expanded, fams


def _fp_effect_ids(fp: Fingerprint) -> tuple[int, ...]:
    return tuple(e for e in fp[1:4] if e not in _EMPTY)


def _fp_is_relevant(
    fp: Fingerprint, pos_ids: set[int], pos_fams: set[str], ds: "SourceDataHandler"
) -> bool:
    for e in _fp_effect_ids(fp):
        if e in pos_ids:
            return True
        tid = ds.get_effect_text_id(e)
        if tid != -1 and tid in pos_ids:
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
    pos_ids, pos_fams = build_positive_effects(build, ds)
    ra = sum(1 for fp in added if _fp_is_relevant(fp, pos_ids, pos_fams, ds))
    rr = sum(1 for fp in removed if _fp_is_relevant(fp, pos_ids, pos_fams, ds))
    return ra, rr


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


def _left_refs(layout: dict, wanted: Counter) -> list[RelicRef]:
    remaining = Counter(wanted)
    out: list[RelicRef] = []
    for r in layout.get("relics", []):
        fp = relic_fingerprint(r["real_id"], r["effects"], r["curses"])
        if remaining.get(fp, 0) > 0:
            remaining[fp] -= 1
            out.append(RelicRef(**r))
    return out


def diff_results(
    old_layouts: Optional[list[dict]],
    new_results: Sequence[VesselResult],
    *,
    epsilon: int = DEFAULT_EPSILON,
) -> BuildChange:
    """Diff a stored snapshot's layouts against a fresh optimization.

    Compares the single best (highest-scoring) arrangement on each side and
    returns a BuildChange with status/scores/entered/left/reliable filled.
    Callers set build_id / slot_index / cause afterwards.
    """
    new_best = max(new_results, key=lambda r: r.total_score, default=None)
    old_best = (
        max(old_layouts, key=lambda layout: layout["total_score"])
        if old_layouts
        else None
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

    # New inventory can no longer fill the vessel at all.
    if new_best is None:
        return BuildChange(
            status="degraded",
            best_before=best_before,
            left=_left_refs(old_best, _layout_relic_fps(old_best)),
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
        left=_left_refs(old_best, left_fps),
        reliable=reliable,
    )
