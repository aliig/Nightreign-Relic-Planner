"""Effective inventory: DB relics + staged in-app edits, per request.

The frontend stages save edits client-side (sold relics, Relic Rites purchases
a.k.a. "mints") and passes them with each optimizer request as a stateless
diff — the same philosophy as the rites ``sold_handles`` form field.  This
module turns (profile inventory + staged diff) into the *effective* inventory
the optimizer and the snapshot freshness gate operate on.

Fidelity: staged mints are re-validated exactly like the export path
(``_export_added_save``) — safe relic id + RelicChecker legality — and their
display fields (name/color/tier/is_deep) are derived from game data, never
trusted from the client.
"""
import hashlib
import json

from fastapi import HTTPException

from app.models import StagedMint
from nrplanner.changes import relic_fingerprint
from nrplanner.checker import InvalidReason, RelicChecker
from nrplanner.constants import EMPTY_EFFECT
from nrplanner.models import OwnedRelic


def staged_diff_signature(
    staged_sells: list[int], staged_mints: list[StagedMint]
) -> str | None:
    """Canonical hash of a staged diff; None when the diff is empty.

    Stored on OptimizationSnapshot.staged_signature for cause attribution only
    (never a freshness input — freshness stays content-hash based).  Mints hash
    by content fingerprint, not synthetic handle, so re-staging the same relics
    under different handles is the same diff.
    """
    if not staged_sells and not staged_mints:
        return None
    payload = {
        "sells": sorted(staged_sells),
        "mints": sorted(
            relic_fingerprint(m.real_id, m.effects, m.curses) for m in staged_mints
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def apply_staged_diff(
    owned: list[OwnedRelic],
    staged_sells: list[int],
    staged_mints: list[StagedMint],
    ds,
    items_json: dict,
) -> list[OwnedRelic]:
    """Return the effective inventory: ``owned`` minus sells plus mints.

    Sells referencing unknown handles are silently ignored (rites precedent —
    the diff may be slightly ahead of a re-uploaded save).  Mints are strictly
    validated; any illegal mint is a 422 so an impossible relic can never enter
    an optimization.
    """
    if not staged_sells and not staged_mints:
        return owned

    sold = set(staged_sells)
    effective = [r for r in owned if r.ga_handle not in sold]

    if not staged_mints:
        return effective

    checker = RelicChecker([], ds)
    safe_ids = set(ds.get_safe_relic_ids())
    seen_handles: set[int] = set()
    for i, m in enumerate(staged_mints):
        if m.handle >= 0:
            raise HTTPException(
                status_code=422,
                detail=f"Staged mint #{i}: handle must be a negative synthetic "
                       f"ga_handle (got {m.handle}).",
            )
        if m.handle in seen_handles:
            raise HTTPException(
                status_code=422,
                detail=f"Staged mint #{i}: duplicate synthetic handle {m.handle}.",
            )
        seen_handles.add(m.handle)
        if m.real_id not in safe_ids:
            raise HTTPException(
                status_code=422,
                detail=f"Staged mint #{i} (id {m.real_id}) is not a mintable relic.",
            )
        effects = (list(m.effects) + [EMPTY_EFFECT] * 3)[:3]
        curses = (list(m.curses) + [EMPTY_EFFECT] * 3)[:3]
        reason = checker.check_invalidity(m.real_id, effects + curses)
        if reason != InvalidReason.NONE:
            raise HTTPException(
                status_code=422,
                detail=f"Staged mint #{i} is not a legal relic ({reason.name}).",
            )
        info = items_json.get(str(m.real_id), {})
        color = info.get("color")
        if color is None:
            raise HTTPException(
                status_code=422,
                detail=f"Staged mint #{i} (id {m.real_id}) is not a relic item.",
            )
        effect_count = sum(1 for e in effects if e not in (EMPTY_EFFECT, 0))
        tier = (
            "Grand" if effect_count >= 3
            else ("Polished" if effect_count == 2 else "Delicate")
        )
        effective.append(OwnedRelic(
            ga_handle=m.handle,
            item_id=m.real_id + 2147483648,
            real_id=m.real_id,
            color=color,
            effects=effects,
            curses=curses,
            is_deep=ds.is_deep_relic(m.real_id),
            name=info.get("name", f"Relic {m.real_id}"),
            tier=tier,
        ))
    return effective
