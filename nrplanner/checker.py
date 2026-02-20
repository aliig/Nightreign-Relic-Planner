"""Relic validity checking — pure validation only, no write-mode state."""
from enum import IntEnum, auto, unique
from typing import Optional

from nrplanner.constants import RELIC_GROUPS
from nrplanner.data import SourceDataHandler

_EMPTY = {-1, 0, 4294967295}


@unique
class InvalidReason(IntEnum):
    VALIDATION_ERROR         = -1
    NONE                     = 0
    IN_ILLEGAL_RANGE         = auto()
    INVALID_ITEM             = auto()
    EFF_MUST_EMPTY           = auto()
    EFF_NOT_IN_ROLLABLE_POOL = auto()
    EFF_CONFLICT             = auto()
    CURSE_MUST_EMPTY         = auto()
    CURSE_REQUIRED_BY_EFFECT = auto()
    CURSE_NOT_IN_ROLLABLE_POOL = auto()
    CURSE_CONFLICT           = auto()
    CURSES_NOT_ENOUGH        = auto()
    EFFS_NOT_SORTED          = auto()


def is_curse_invalid(reason: int) -> bool:
    return reason in (
        InvalidReason.CURSE_MUST_EMPTY,
        InvalidReason.CURSE_REQUIRED_BY_EFFECT,
        InvalidReason.CURSE_NOT_IN_ROLLABLE_POOL,
        InvalidReason.CURSE_CONFLICT,
        InvalidReason.CURSES_NOT_ENOUGH,
    )


_SEQUENCES = [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]]


class RelicChecker:
    RELIC_RANGE: tuple[int, int] = (100, 2013322)
    UNIQUENESS_IDS: set[int] = (
        set(range(RELIC_GROUPS['unique_1'][0], RELIC_GROUPS['unique_1'][1] + 1)) |
        set(range(RELIC_GROUPS['unique_2'][0], RELIC_GROUPS['unique_2'][1] + 1))
    )

    def __init__(self, ga_relic: list, data_source: SourceDataHandler):
        self.ga_relic = ga_relic
        self.data_source = data_source

    # ------------------------------------------------------------------
    # Primary validation
    # ------------------------------------------------------------------

    def check_invalidity(self, relic_id: int, effects: list[int],
                         return_1st_invalid_idx: bool = False
                         ) -> InvalidReason | tuple[InvalidReason, int]:
        """Return the first InvalidReason for a relic, or NONE if valid.

        effects: [e1, e2, e3, curse1, curse2, curse3]
        If return_1st_invalid_idx, returns (reason, 0-based effect index or -1).
        """
        def ret(reason, idx=-1):
            return (reason, idx) if return_1st_invalid_idx else reason

        if relic_id in range(RELIC_GROUPS['illegal'][0], RELIC_GROUPS['illegal'][1] + 1):
            return ret(InvalidReason.IN_ILLEGAL_RANGE)

        if relic_id not in range(self.RELIC_RANGE[0], self.RELIC_RANGE[1] + 1):
            return ret(InvalidReason.INVALID_ITEM)

        pool_reason, pool_idx = self._check_relic_effects_in_pool(relic_id, effects)
        if pool_reason != InvalidReason.NONE:
            return ret(pool_reason, pool_idx)

        deep_only = sum(1 for e in effects[:3] if self._effect_needs_curse(e))
        curses_given = sum(1 for c in effects[3:] if c not in _EMPTY)
        if deep_only > curses_given:
            return ret(InvalidReason.CURSES_NOT_ENOUGH)

        conflict_ids: list[int] = []
        for idx, eff in enumerate(effects):
            if eff in _EMPTY:
                continue
            cid = self.data_source.get_effect_conflict_id(eff)
            if cid in conflict_ids and cid != -1:
                reason = InvalidReason.EFF_CONFLICT if idx < 3 else InvalidReason.CURSE_CONFLICT
                return (reason, idx) if return_1st_invalid_idx else reason
            conflict_ids.append(cid)

        sort_ids = [
            float('inf') if e in _EMPTY else self.data_source.get_sort_id(e)
            for e in effects[:3]
        ]
        sorted_effects = sorted(zip(sort_ids, effects[:3]), key=lambda x: (x[0], x[1]))
        for i, (_, eff) in enumerate(sorted_effects):
            if eff != effects[i]:
                return ret(InvalidReason.EFFS_NOT_SORTED)

        return ret(InvalidReason.NONE)

    def check_possible_effects_seq(self, relic_id: int, effects: list[int],
                                   stop_on_valid: bool = False
                                   ) -> list[tuple[tuple[int, int, int], list[InvalidReason]]]:
        """Check all 6 effect orderings against relic pools.

        effects: [e1, e2, e3, curse1, curse2, curse3]
        Returns list of (sequence, [per-slot InvalidReason]).
        """
        try:
            pools = self.data_source.get_relic_pools_seq(relic_id)
        except KeyError:
            return [((-1, -1, -1), [InvalidReason.VALIDATION_ERROR])]

        results = []
        for seq in _SEQUENCES:
            cur_effs   = [effects[i]     for i in seq]
            cur_curses = [effects[i + 3] for i in seq]
            row: list[InvalidReason] = []

            for idx in range(3):
                eff  = cur_effs[idx]
                pool = pools[idx]
                if pool == -1:
                    row.append(InvalidReason.NONE if eff in _EMPTY else InvalidReason.EFF_MUST_EMPTY)
                elif eff in _EMPTY:
                    row.append(InvalidReason.NONE)
                elif eff not in self.data_source.get_pool_rollable_effects(pool):
                    row.append(InvalidReason.EFF_NOT_IN_ROLLABLE_POOL)
                else:
                    row.append(InvalidReason.NONE)

            for idx in range(3):
                curse      = cur_curses[idx]
                eff        = cur_effs[idx]
                curse_pool = pools[idx + 3]
                if curse_pool == -1:
                    row.append(InvalidReason.NONE if curse in _EMPTY else InvalidReason.CURSE_MUST_EMPTY)
                elif curse in _EMPTY:
                    needs = self._effect_needs_curse(eff)
                    row.append(InvalidReason.CURSE_REQUIRED_BY_EFFECT if needs else InvalidReason.NONE)
                elif curse not in self.data_source.get_pool_rollable_effects(curse_pool):
                    row.append(InvalidReason.CURSE_NOT_IN_ROLLABLE_POOL)
                else:
                    row.append(InvalidReason.NONE)

            results.append((tuple(seq), row))
            if stop_on_valid and all(r == InvalidReason.NONE for r in row):
                return results

        return results

    # ------------------------------------------------------------------
    # Strict validity (deep-pool weight checks)
    # ------------------------------------------------------------------

    def is_strict_invalid(self, relic_id: int, effects: list[int],
                          invalid_reason: Optional[InvalidReason] = None) -> bool:
        """True if no permutation exists where all effects have non-zero weight
        in their specific deep pool slot (broader than normal invalidity)."""
        if invalid_reason is None:
            invalid_reason = self.check_invalidity(relic_id, effects)
        if invalid_reason != InvalidReason.NONE:
            return False

        try:
            pools = self.data_source.get_relic_pools_seq(relic_id)
        except KeyError:
            return False

        deep_pools = {2000000, 2100000, 2200000}
        if not any(p in deep_pools for p in pools[:3]):
            return False

        for seq in _SEQUENCES:
            cur_effs = [effects[i] for i in seq]
            valid = True
            for idx in range(3):
                eff  = cur_effs[idx]
                pool = pools[idx]
                if eff in _EMPTY or pool not in deep_pools:
                    continue
                if eff not in self.data_source.get_pool_effects_strict(pool):
                    valid = False
                    break
            if valid:
                return False  # At least one permutation is strictly valid

        return True

    def get_strict_invalid_reason(self, relic_id: int, effects: list[int]) -> str | None:
        """Human-readable reason for strict invalidity, or None if not strictly invalid."""
        if not self.is_strict_invalid(relic_id, effects, InvalidReason.NONE):
            return None
        try:
            pools = self.data_source.get_relic_pools_seq(relic_id)
        except KeyError:
            return "Unknown relic ID"

        deep_pools = {2000000, 2100000, 2200000}
        pool_names = {2000000: "Pool A", 2100000: "Pool B", 2200000: "Pool C"}
        problems = []
        for i, eff in enumerate(effects[:3]):
            if eff in _EMPTY:
                continue
            pool = pools[i]
            if pool not in deep_pools:
                continue
            if eff not in self.data_source.get_pool_effects_strict(pool):
                valid_pools = [
                    pool_names.get(p, str(p))
                    for p in deep_pools
                    if eff in self.data_source.get_pool_effects_strict(p)
                ]
                name = self.data_source.get_effect_name(eff)
                if valid_pools:
                    problems.append(f"'{name}' needs {'/'.join(valid_pools)} but slot {i+1} uses {pool_names.get(pool, str(pool))}")
                else:
                    problems.append(f"'{name}' has 0 weight in all deep pools")

        return "; ".join(problems) if problems else "No valid permutation exists"

    # ------------------------------------------------------------------
    # Effect ordering
    # ------------------------------------------------------------------

    def sort_effects(self, effects: list[int]) -> list[int]:
        """Sort effects by sort_id, keeping curses paired with primary effects.

        effects: [e1, e2, e3, curse1, curse2, curse3]
        """
        curses = effects[3:]
        curse_tuples = sorted(
            (
                (float('inf') if c in _EMPTY else self.data_source.get_sort_id(c), c)
                for c in curses
            ),
            key=lambda x: (x[0], x[1]),
        )
        sorted_curses = [pair[1] for pair in curse_tuples]

        pairs = []
        for eff in effects[:3]:
            if self.data_source.effect_needs_curse(eff):
                curse = sorted_curses.pop(0)
            else:
                curse = sorted_curses.pop()
            sort_id = float('inf') if eff in _EMPTY else self.data_source.get_sort_id(eff)
            pairs.append((sort_id, eff, curse))

        pairs.sort(key=lambda x: (x[0], x[1]))
        return [p[1] for p in pairs] + [p[2] for p in pairs]

    def has_valid_order(self, relic_id: int, effects: list[int]) -> bool:
        """True if any permutation of effects is valid (rollable-pool check)."""
        return self.get_valid_order(relic_id, effects) is not None

    def get_valid_order(self, relic_id: int, effects: list[int]) -> list[int] | None:
        """Return sorted effects if any permutation is rollable-pool valid, else None."""
        try:
            pools = self.data_source.get_relic_pools_seq(relic_id)
        except KeyError:
            return None

        for seq in _SEQUENCES:
            if self._seq_rollable_valid(effects, seq, pools):
                return self.sort_effects(effects)
        return None

    def get_strictly_valid_order(self, relic_id: int, effects: list[int]) -> list[int] | None:
        """Return sorted effects if any permutation is strict-pool valid, else None."""
        try:
            pools = self.data_source.get_relic_pools_seq(relic_id)
        except KeyError:
            return None

        deep_pools = {2000000, 2100000, 2200000}
        for seq in _SEQUENCES:
            cur_effs   = [effects[i]     for i in seq]
            cur_curses = [effects[i + 3] for i in seq]
            valid = True
            for idx in range(3):
                eff        = cur_effs[idx]
                curse      = cur_curses[idx]
                pool       = pools[idx]
                curse_pool = pools[idx + 3]
                if eff in _EMPTY:
                    continue
                if eff not in self.data_source.get_pool_effects_strict(pool):
                    valid = False; break
                if self.data_source.effect_needs_curse(eff):
                    if curse_pool == -1 or curse in _EMPTY:
                        valid = False; break
                    if curse not in self.data_source.get_pool_effects_strict(curse_pool):
                        valid = False; break
                if curse not in _EMPTY and curse_pool == -1:
                    valid = False; break
            if valid:
                return self.sort_effects(effects)

        return None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def find_id_range(self, relic_id: int) -> tuple[str, tuple[int, int]] | None:
        """Return (group_name, (lo, hi)) for the relic ID's RELIC_GROUPS range."""
        for name, (lo, hi) in RELIC_GROUPS.items():
            if lo <= relic_id <= hi:
                return name, (lo, hi)
        return None

    @staticmethod
    def is_deep_relic(relic_id: int) -> bool:
        return SourceDataHandler.is_deep_relic(relic_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_relic_effects_in_pool(self, relic_id: int,
                                     effects: list[int]) -> tuple[InvalidReason, int]:
        results = self.check_possible_effects_seq(relic_id, effects, stop_on_valid=True)
        if not results:
            return InvalidReason.VALIDATION_ERROR, -1
        _, last_row = results[-1]
        if last_row == [InvalidReason.VALIDATION_ERROR]:
            return InvalidReason.VALIDATION_ERROR, -1
        if all(r == InvalidReason.NONE for r in last_row):
            return InvalidReason.NONE, 0
        _, first_row = results[0]
        for idx, res in enumerate(first_row):
            if res != InvalidReason.NONE:
                return res, idx
        return InvalidReason.VALIDATION_ERROR, -1

    def _effect_needs_curse(self, effect_id: int) -> bool:
        return self.data_source.effect_needs_curse(effect_id)

    def _seq_rollable_valid(self, effects: list[int], seq: list[int],
                            pools: list[int]) -> bool:
        cur_effs   = [effects[i]     for i in seq]
        cur_curses = [effects[i + 3] for i in seq]
        for idx in range(3):
            eff        = cur_effs[idx]
            curse      = cur_curses[idx]
            pool       = pools[idx]
            curse_pool = pools[idx + 3]
            if eff in _EMPTY:
                continue
            if eff not in self.data_source.get_pool_rollable_effects(pool):
                return False
            if self.data_source.effect_needs_curse(eff):
                if curse_pool == -1 or curse in _EMPTY:
                    return False
                if curse not in self.data_source.get_pool_rollable_effects(curse_pool):
                    return False
            if curse not in _EMPTY:
                if curse_pool == -1:
                    return False
                if curse not in self.data_source.get_pool_rollable_effects(curse_pool):
                    return False
        return True
