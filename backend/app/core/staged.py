"""Effective slot state: base save state + staged in-app edits, per request.

The frontend stages save edits client-side (sold relics, Relic Rites purchases
a.k.a. "mints", their net Murk delta) and passes them with each request as a
stateless diff — the same philosophy as the rites ``sold_handles`` form field.
This module is the ONE place that composes (base state + staged diff) into the
*effective* state endpoints compute over:

- ``apply_staged_diff`` — effective inventory (optimizer, snapshot gate, rites)
- ``effective_slot_state`` — inventory + wallet + storage/ghost capacity for
  endpoints that read a whole save slot (rites plan; any future save-reading
  endpoint should start here instead of hand-rolling the composition)

Fidelity: staged mints are re-validated exactly like the export path
(``_export_added_save``) — safe relic id + RelicChecker legality — and their
display fields (name/color/tier/is_deep) are derived from game data, never
trusted from the client.
"""
import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException
from nrplanner.changes import relic_fingerprint
from nrplanner.checker import InvalidReason, RelicChecker
from nrplanner.constants import EMPTY_EFFECT, RELIC_STORAGE_CAP
from nrplanner.models import OwnedRelic
from nrplanner.writer import sell_value

from app.models import StagedMint


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


@dataclass
class EffectiveSlotState:
    """A save slot with the staged diff applied — the LIVE state the app shows.

    Compute over these fields, never the raw save values, wherever staged
    edits should already be reflected. ``murk_raw`` is the only sanctioned
    raw read-through: the rites anti-scum roll seed must hash committed save
    state, never the staged diff (see _rites_roll_seed).
    """
    owned: list[OwnedRelic]     # base − staged sells + staged mints
    wallet: int                 # spendable Murk: max(0, raw + min(0, delta))
    murk_raw: int               # the save's on-disk Murk (seed input only)
    pending_sold_refund: int    # total sell value of the staged sells
    sold_handles: set[int]
    mint_count: int
    storage_left_ingame: int    # free slots under the pure in-game 1950 cap
    ghost_capacity: int         # export-time mintable slots: cap + sells − mints


def effective_slot_state(
    owned_all: list[OwnedRelic],
    murk_raw: int,
    ghost_cap: int,
    staged_sells: list[int],
    staged_mints: list[StagedMint],
    staged_murk_delta: int,
    ds,
    items_json: dict,
) -> EffectiveSlotState:
    """Compose (parsed save slot + staged diff) into the effective slot state.

    - Inventory via ``apply_staged_diff`` (mints legality-gated, sells removed).
    - Wallet: the save's Murk spent down by the staged batch delta. The delta
      is client-supplied (dud refunds cannot be reconstructed from keepers —
      same trust model as /saves/export-add-relics) but only ever LOWERS the
      wallet: a staged batch's net is always <= 0 (buy price exceeds
      sell-back), so positive values are clamped and a client can never
      manufacture planning Murk.
    - Storage: staged mints occupy in-game slots now, and will each consume a
      ghost record at export while every staged sell tombstones one free.

    Callers wanting stricter sell validation than apply_staged_diff's
    silent-ignore (e.g. rites' unknown/protected 422s) check BEFORE composing.
    """
    sold = set(staged_sells)
    owned = apply_staged_diff(owned_all, staged_sells, staged_mints, ds, items_json)
    refund = sum(
        sell_value(o.effect_count, o.is_deep)
        for o in owned_all if o.ga_handle in sold
    )
    return EffectiveSlotState(
        owned=owned,
        wallet=max(0, murk_raw + min(0, staged_murk_delta)),
        murk_raw=murk_raw,
        pending_sold_refund=refund,
        sold_handles=sold,
        mint_count=len(staged_mints),
        storage_left_ingame=max(0, RELIC_STORAGE_CAP - len(owned)),
        ghost_capacity=max(0, ghost_cap + len(sold) - len(staged_mints)),
    )
