"""Relic generation — roll a legal relic the way the in-game purchase does.

Each effect slot is a weighted-random draw from the real game pool
(``AttachEffectTableParam`` via ``SourceDataHandler.get_pool_weighted_effects``), with
duplicate and conflict (``compatibilityId``) exclusion so the result is legal by
construction. Deep relics additionally draw a curse — without replacement — for every
placed effect that requires one (``effect_needs_curse``). Every roll is validated by
``RelicChecker`` as a fail-closed guard.

Fidelity: the *effect* roll is always exact (real weights). Only the *color/tier*
acquisition meta-roll is approximate when exact lot data (Phase 2) is unavailable; the
result's ``odds_source`` records which was used.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from nrplanner.checker import InvalidReason, RelicChecker
from nrplanner.constants import COLOR_MAP, EMPTY_EFFECT, RELIC_GROUPS
from nrplanner.data import SourceDataHandler

# Tier = number of primary effect slots.
_TIER_NAMES = {1: "Delicate", 2: "Polished", 3: "Grand"}
# Store/deep purchase pools contain no White relics (White is not purchasable).
# Source: the EquipParamAntique grids for store_10x / deep_10x are exactly 4 colors x tiers x copies
# (no White), verified during regulation.bin extraction — see tools/populate_resources.py and
# nrplanner/resources/json/relic_lots.json.
_PURCHASABLE_COLORS = ["Red", "Blue", "Yellow", "Green"]
# FALLBACK tier odds — used ONLY when relic_lots.json is absent (a broken/partial install).
# Authoritative odds come from relic_lots.json, whose weights are derived EXACTLY from the game
# (regulation.bin ShopLineupParam equipType=5 -> ItemTableParam chanceWeights -> EquipParamAntique;
# full chain in tools/RELIC_GENERATION_RE.md). This single fallback uses the exact SCENIC split
# (Delicate/Polished/Grand = 45/35/20); deep's real split is 10/50/40, so the fallback is only
# approximate for deep. Color is uniform (proven from the template grid).
_APPROX_TIER_WEIGHTS = {1: 45, 2: 35, 3: 20}


class GenerationError(RuntimeError):
    """A legal relic could not be generated within the retry budget."""


class _DeadEnd(Exception):
    """Internal: a slot's candidate set was fully excluded — retry the whole relic."""


@dataclass(frozen=True)
class GeneratedRelic:
    """A rolled relic. ``effects``/``curses`` are in canonical (save-ready) order."""
    real_id: int
    item_id: int                       # real_id + 0x80000000 (raw save value)
    color: str
    tier: int                          # 1..3 = number of primary effect slots
    is_deep: bool
    effects: tuple[int, int, int]      # EMPTY_EFFECT for an empty slot
    curses: tuple[int, int, int]
    odds_source: str                   # "exact" | "approximate" | "targeted" (color/tier only)
    name: str
    effect_names: tuple[str, str, str]
    curse_names: tuple[str, str, str]


class RelicGenerator:
    """Rolls legal relics using the real game pools + rules.

    Args:
        data_source: a loaded SourceDataHandler.
        lots: optional acquisition-odds table (Phase 2), keyed by lot key
            (see ``_lot_key``) -> ``{"entries": [(real_id, weight), ...],
            "approximate": bool}``. When absent for a requested lot, random mode falls
            back to approximate color/tier odds.
    """

    _MAX_ATTEMPTS = 8

    def __init__(self, data_source: SourceDataHandler, lots: dict | None = None):
        self.ds = data_source
        self.checker = RelicChecker([], data_source)
        self._lots = lots or {}
        # pool_id -> [(effect_id, weight, conflict_id)]
        self._pool_cache: dict[int, list[tuple[int, int, int]]] = {}
        # (is_deep, version) -> [(real_id, color_idx, tier)]
        self._tpl_cache: dict[tuple[bool, str], list[tuple[int, int, int]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def roll(self, *, is_deep: bool = False, version: str = "1.03",
             mode: str = "random", color: str | None = None,
             tier: int | None = None, rng: random.Random | None = None,
             seed: int | None = None) -> GeneratedRelic:
        """Roll one relic.

        mode="random": pick the template from the acquisition lot (exact) or an
            approximate color/tier fallback. mode="targeted": use the given color+tier.
        """
        if rng is None:
            rng = random.Random(seed)
        real_id, odds_source = self._pick_template(is_deep, version, mode, color, tier, rng)

        for _ in range(self._MAX_ATTEMPTS):
            try:
                effects, curses = self._roll_effects(real_id, rng)
            except _DeadEnd:
                continue
            ordered = self.checker.get_valid_order(real_id, effects + curses)
            if ordered is None:
                continue
            if self.checker.check_invalidity(real_id, ordered) != InvalidReason.NONE:
                continue
            return self._finalize(real_id, ordered, odds_source)

        raise GenerationError(
            f"could not generate a legal relic for template {real_id} in "
            f"{self._MAX_ATTEMPTS} attempts")

    # ------------------------------------------------------------------
    # Template selection
    # ------------------------------------------------------------------

    @staticmethod
    def _range_key(is_deep: bool, version: str) -> str:
        v = "103" if version == "1.03" else "102"
        return f"{'deep' if is_deep else 'store'}_{v}"

    @staticmethod
    def _lot_key(is_deep: bool, version: str) -> str:
        return f"{'deep' if is_deep else 'scenic'}_{version}"

    def _templates(self, is_deep: bool, version: str) -> list[tuple[int, int, int]]:
        key = (is_deep, version)
        cached = self._tpl_cache.get(key)
        if cached is not None:
            return cached
        lo, hi = RELIC_GROUPS[self._range_key(is_deep, version)]
        out: list[tuple[int, int, int]] = []
        for rid in self.ds.relic_table.index:
            rid = int(rid)
            if not (lo <= rid <= hi):
                continue
            color_idx = int(self.ds.relic_table.loc[rid, "relicColor"])
            tier = self.ds.get_relic_slot_count(rid)[0]
            if tier == 0:
                continue  # not a rollable relic template
            out.append((rid, color_idx, tier))
        self._tpl_cache[key] = out
        return out

    def _pick_template(self, is_deep: bool, version: str, mode: str,
                       color: str | None, tier: int | None,
                       rng: random.Random) -> tuple[int, str]:
        templates = self._templates(is_deep, version)
        if not templates:
            raise GenerationError(
                f"no templates in {self._range_key(is_deep, version)}")

        if mode == "targeted":
            if color is None or tier is None:
                raise ValueError("targeted mode requires both color and tier")
            if color not in COLOR_MAP:
                raise ValueError(f"unknown color {color!r}")
            cidx = COLOR_MAP.index(color)
            matches = [t for t in templates if t[1] == cidx and t[2] == tier]
            if not matches:
                raise ValueError(
                    f"no {color} tier-{tier} template in "
                    f"{self._range_key(is_deep, version)}")
            return rng.choice(matches)[0], "targeted"

        # random mode: prefer an exact acquisition lot when available.
        lot = self._lots.get(self._lot_key(is_deep, version))
        if lot and lot.get("entries"):
            ids = [int(e[0]) for e in lot["entries"]]
            weights = [int(e[1]) for e in lot["entries"]]
            rid = rng.choices(ids, weights=weights, k=1)[0]
            return rid, ("approximate" if lot.get("approximate") else "exact")

        # approximate fallback: uniform over purchasable colors × a labelled tier split.
        present_colors = {t[1] for t in templates}
        color_pool = [COLOR_MAP.index(c) for c in _PURCHASABLE_COLORS
                      if COLOR_MAP.index(c) in present_colors] or sorted(present_colors)
        cidx = rng.choice(color_pool)
        tiers, tier_w = zip(*_APPROX_TIER_WEIGHTS.items())
        picked_tier = rng.choices(list(tiers), weights=list(tier_w), k=1)[0]
        matches = [t for t in templates if t[1] == cidx and t[2] == picked_tier]
        if not matches:
            matches = [t for t in templates if t[1] == cidx] or templates
        return rng.choice(matches)[0], "approximate"

    # ------------------------------------------------------------------
    # Effect / curse rolling
    # ------------------------------------------------------------------

    def _pool(self, pool_id: int) -> list[tuple[int, int, int]]:
        cached = self._pool_cache.get(pool_id)
        if cached is None:
            cached = [
                (eid, w, self.ds.get_effect_conflict_id(eid))
                for eid, w in self.ds.get_pool_weighted_effects(pool_id)
            ]
            self._pool_cache[pool_id] = cached
        return cached

    def _draw(self, pool_id: int, exclude: set[int], conflicts: set[int],
              rng: random.Random) -> int:
        cands: list[int] = []
        weights: list[int] = []
        for eid, w, cid in self._pool(pool_id):
            if eid in exclude:
                continue
            if cid != -1 and cid in conflicts:
                continue
            cands.append(eid)
            weights.append(w)
        if not cands:
            raise _DeadEnd()
        return rng.choices(cands, weights=weights, k=1)[0]

    def _roll_effects(self, real_id: int,
                      rng: random.Random) -> tuple[list[int], list[int]]:
        """Return (effects[3], curses[3]) with curses positionally paired to effects."""
        pools = [int(p) for p in self.ds.get_relic_pools_seq(real_id)]
        effects = [EMPTY_EFFECT, EMPTY_EFFECT, EMPTY_EFFECT]
        curses = [EMPTY_EFFECT, EMPTY_EFFECT, EMPTY_EFFECT]
        placed: set[int] = set()
        conflicts: set[int] = set()

        for i in range(3):
            if pools[i] == -1:
                continue
            eid = self._draw(pools[i], placed, conflicts, rng)
            effects[i] = eid
            placed.add(eid)
            self._note_conflict(eid, conflicts)

        placed_curses: set[int] = set()
        for i in range(3):
            eid = effects[i]
            if eid == EMPTY_EFFECT or not self.ds.effect_needs_curse(eid):
                continue
            curse_pool = pools[3 + i]
            if curse_pool == -1:
                raise _DeadEnd()  # data inconsistency; retry
            cid = self._draw(curse_pool, placed_curses, conflicts, rng)
            curses[i] = cid
            placed_curses.add(cid)
            self._note_conflict(cid, conflicts)

        return effects, curses

    def _note_conflict(self, effect_id: int, conflicts: set[int]) -> None:
        cid = self.ds.get_effect_conflict_id(effect_id)
        if cid != -1:
            conflicts.add(cid)

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------

    def _finalize(self, real_id: int, ordered: list[int],
                  odds_source: str) -> GeneratedRelic:
        effects = (ordered[0], ordered[1], ordered[2])
        curses = (ordered[3], ordered[4], ordered[5])
        color_idx = int(self.ds.relic_table.loc[real_id, "relicColor"])
        color = COLOR_MAP[color_idx] if 0 <= color_idx < len(COLOR_MAP) else "None"
        tier = self.ds.get_relic_slot_count(real_id)[0]
        is_deep = self.ds.is_deep_relic(real_id)
        name = self._relic_name(real_id) or self._compose_name(tier, color, is_deep)
        return GeneratedRelic(
            real_id=real_id,
            item_id=real_id + 0x80000000,
            color=color,
            tier=tier,
            is_deep=is_deep,
            effects=effects,
            curses=curses,
            odds_source=odds_source,
            name=name,
            effect_names=tuple(self.ds.get_effect_name(e) for e in effects),
            curse_names=tuple(self.ds.get_effect_name(c) for c in curses),
        )

    def _relic_name(self, real_id: int) -> str | None:
        df = self.ds.relic_name
        if df is None:
            return None
        row = df[df["id"] == real_id]
        if row.empty:
            return None
        text = str(row["text"].values[0]).strip()
        return text if text and text != "%null%" else None

    @staticmethod
    def _compose_name(tier: int, color: str, is_deep: bool) -> str:
        label = f"{_TIER_NAMES.get(tier, 'Relic')} {color}"
        return f"{label} (Deep)" if is_deep else label
