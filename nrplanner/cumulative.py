"""Cumulative stacked-effect summary for a finished vessel.

Pure functions (take a SourceDataHandler) that group a vessel's placed effect
ids by family and compute the real in-game cumulative bonus per family, using
the curated per-effect values in resources/json/effect_bonus_values.json.

Only clean, unconditional, self-stackable numeric effects have curated values;
everything else (conditional, non-stacking, curses) has no value and is silently
ignored. See nrplanner.models.CumulativeEffectGroup for the output shape.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from nrplanner.models import CumulativeEffectGroup, CumulativeEffectTier

if TYPE_CHECKING:
    from nrplanner.data import SourceDataHandler

_TIER_RE = re.compile(r"\+(\d+)%?\s*$")


def _tier_label(name: str) -> str:
    """'+N' parsed from an effect name; '+0' when the base carries no suffix."""
    m = _TIER_RE.search(name)
    return f"+{m.group(1)}" if m else "+0"


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
        g = groups.setdefault(family, {"mode": bonus["mode"], "unit": bonus["unit"], "members": {}})
        slot = g["members"].setdefault(name, {"count": 0, "value": bonus["value"]})
        slot["count"] += 1

    result: list[CumulativeEffectGroup] = []
    for family, g in groups.items():
        mode, unit, members = g["mode"], g["unit"], g["members"]
        # tiers strongest-first ("+4 ×3, +2 ×1, +1 ×1")
        tiers = [
            CumulativeEffectTier(name=name, tier_label=_tier_label(name), count=info["count"])
            for name, info in sorted(members.items(), key=lambda kv: kv[1]["value"], reverse=True)
        ]

        if mode == "multiplicative":
            cum = 1.0
            for info in members.values():
                cum *= info["value"] ** info["count"]
            pct = (cum - 1) * 100
            result.append(CumulativeEffectGroup(
                family=family, mode=mode, unit=unit, tiers=tiers,
                cumulative_value=cum, bonus_percent=pct,
                bonus_display=f"{cum:.2f}× (+{pct:.0f}%)",
            ))
        elif mode == "multiplicative_reduction":
            remaining = 1.0
            for info in members.values():
                remaining *= (1 - info["value"]) ** info["count"]
            reduction = 1 - remaining
            pct = reduction * 100
            result.append(CumulativeEffectGroup(
                family=family, mode=mode, unit=unit, tiers=tiers,
                cumulative_value=reduction, bonus_percent=pct,
                bonus_display=f"+{pct:.0f}% {unit}",
            ))
        else:  # additive_flat
            total = sum(info["value"] * info["count"] for info in members.values())
            result.append(CumulativeEffectGroup(
                family=family, mode=mode, unit=unit, tiers=tiers,
                cumulative_value=float(total), bonus_percent=None,
                bonus_display=f"+{_fmt_num(total)} {unit}",
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
