"""Cumulative stacked-effect summary for a finished vessel.

Pure functions (take a SourceDataHandler) that group a vessel's placed effect
ids by family and compute the real in-game cumulative bonus per family, using
the curated per-effect values in resources/json/effect_bonus_values.json.

Self-stackable numeric effects (including family-less singletons like weapon-class
attack powers) have curated values, as do "unique" multi-tier families whose
different levels stack (e.g. vs-afflicted-enemy damage) -- for those, duplicate
copies of the same tier are capped at one since they don't stack in-game (different
tiers still do). Everything else (other non-stacking effects, curses) has no value
and is silently ignored. Conditional effects (e.g. "at low HP") carry a
``conditional`` tag so the UI can badge them. See
nrplanner.models.CumulativeEffectGroup for the output shape.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from nrplanner.models import CumulativeEffectGroup, CumulativeEffectTier

if TYPE_CHECKING:
    from nrplanner.data import SourceDataHandler

_TIER_RE = re.compile(r"\+(\d+)%?\s*$")


def _tier_label(name: str, multi: bool) -> str:
    """'+N' parsed from an effect name.

    A suffix-less name is the '+0' base of a tiered family when the group has
    other tiers (``multi``); a lone singleton (e.g. 'Improved Katana Attack
    Power') gets no label so the UI shows just '×N'.
    """
    m = _TIER_RE.search(name)
    if m:
        return f"+{m.group(1)}"
    return "+0" if multi else ""


def _fmt_num(x: float) -> str:
    """Drop a trailing '.0' on whole numbers (60.0 -> '60')."""
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def summarize_cumulative_effects(
    effect_ids: list[int], ds: "SourceDataHandler"
) -> list[CumulativeEffectGroup]:
    """Group placed effect ids by family and compute cumulative bonuses.

    ``effect_ids`` is the flat list of every effect placed on a vessel (counting
    duplicates). Returns groups sorted strongest-first, with ``is_top`` set on the
    single biggest bonus (a percentage group — multiplicative or reduction — wins
    over a flat-stat group).
    """
    # family -> {mode, unit, members: {effect_name: {count, value}}}
    # Keyed by display name (not raw id) so collision/alias ids of the SAME tier
    # (e.g. Physical Attack Up +2 == 7001402 AND 7001409) merge into one tier.
    groups: dict[str, dict] = {}
    for eid in effect_ids:
        bonus = ds.get_effect_bonus_value(eid)
        if bonus is None:
            continue
        name = ds.get_effect_name(eid)
        family = ds.get_effect_family(eid) or name
        g = groups.setdefault(family, {
            "mode": bonus["mode"], "unit": bonus["unit"],
            "conditional": bonus.get("conditional"), "members": {},
        })
        slot = g["members"].setdefault(name, {
            "count": 0, "value": bonus["value"],
            "cap_duplicates": ds.get_effect_stacking_type(eid) == "unique",
        })
        slot["count"] += 1

    result: list[CumulativeEffectGroup] = []
    for family, g in groups.items():
        mode, unit, members = g["mode"], g["unit"], g["members"]
        cond = g["conditional"]
        multi = len(members) > 1
        # Effective copies per tier. Only 'unique' effects cap duplicates: their
        # different tiers stack but a copy doesn't stack with itself, so each
        # distinct tier counts at most once. 'stack' effects accumulate every
        # copy; curated 'no_stack' effects (the elemental negations) keep their
        # existing multiply behaviour. The vs-afflicted-enemy families are the
        # only 'unique' curated effects today.
        for info in members.values():
            info["eff_count"] = min(info["count"], 1) if info["cap_duplicates"] else info["count"]
        # tiers strongest-first ("+4 ×3, +2 ×1, +1 ×1")
        tiers = [
            CumulativeEffectTier(name=name, tier_label=_tier_label(name, multi), count=info["eff_count"])
            for name, info in sorted(members.items(), key=lambda kv: kv[1]["value"], reverse=True)
        ]

        if mode == "multiplicative":
            cum = 1.0
            for info in members.values():
                cum *= info["value"] ** info["eff_count"]
            pct = (cum - 1) * 100
            # "%"-unit effects read as raw multipliers ("1.58× (+58%)"); a named
            # unit (Max HP/FP/Stamina) reads as a percent gain ("+21% Max HP").
            display = f"{cum:.2f}× (+{pct:.0f}%)" if unit == "%" else f"+{pct:.0f}% {unit}"
            result.append(CumulativeEffectGroup(
                family=family, mode=mode, unit=unit, tiers=tiers,
                cumulative_value=cum, bonus_percent=pct,
                bonus_display=display, conditional=cond,
            ))
        elif mode == "multiplicative_reduction":
            remaining = 1.0
            for info in members.values():
                remaining *= (1 - info["value"]) ** info["eff_count"]
            reduction = 1 - remaining
            pct = reduction * 100
            result.append(CumulativeEffectGroup(
                family=family, mode=mode, unit=unit, tiers=tiers,
                cumulative_value=reduction, bonus_percent=pct,
                bonus_display=f"+{pct:.0f}% {unit}", conditional=cond,
            ))
        else:  # additive_flat
            total = sum(info["value"] * info["eff_count"] for info in members.values())
            result.append(CumulativeEffectGroup(
                family=family, mode=mode, unit=unit, tiers=tiers,
                cumulative_value=float(total), bonus_percent=None,
                bonus_display=f"+{_fmt_num(total)} {unit}", conditional=cond,
            ))

    # Percentage groups (mult + reduction) first, biggest % first; then flat by amount.
    result.sort(key=lambda grp: (
        grp.bonus_percent is None,
        -(grp.bonus_percent if grp.bonus_percent is not None else 0.0),
        -grp.cumulative_value,
    ))
    if result:
        result[0].is_top = True
    return result
