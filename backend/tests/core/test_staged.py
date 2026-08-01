"""Unit tests for app.core.staged.effective_slot_state — the one composition
point turning (parsed save slot + staged diff) into the live state endpoints
compute over. Every save-reading endpoint should build on this, so its
invariants (wallet clamp, storage/ghost math, refund tally) are pinned here
once instead of per-route.
"""
import pytest

from nrplanner.constants import EMPTY_EFFECT, RELIC_STORAGE_CAP
from nrplanner.models import OwnedRelic
from nrplanner.writer import sell_value

from app.core.staged import effective_slot_state
from app.models import StagedMint


def _owned(ga_handle: int, effects: list[int] | None = None,
           is_deep: bool = False) -> OwnedRelic:
    eff = (list(effects or [101]) + [EMPTY_EFFECT] * 3)[:3]
    ec = sum(1 for e in eff if e not in (EMPTY_EFFECT, 0))
    tier = "Grand" if ec >= 3 else ("Polished" if ec == 2 else "Delicate")
    return OwnedRelic(
        ga_handle=ga_handle, item_id=100 + 0x80000000, real_id=100,
        color="Red", effects=eff, curses=[EMPTY_EFFECT] * 3,
        is_deep=is_deep, name="Owned", tier=tier,
    )


def _legal_mint(handle: int = -1) -> StagedMint:
    from app.core.game_data import get_relic_generator

    rolled = get_relic_generator().roll(
        is_deep=False, version="1.03", mode="targeted",
        color="Red", tier=1, seed=4242,
    )
    return StagedMint(
        handle=handle, real_id=rolled.real_id,
        effects=list(rolled.effects), curses=list(rolled.curses),
    )


@pytest.mark.usefixtures("override_game_data")
class TestEffectiveSlotState:
    @staticmethod
    def _ds_items():
        from app.core.game_data import get_game_data, get_items_json

        return get_game_data(), get_items_json()

    def _state(self, owned, murk, cap, sells=(), mints=(), delta=0):
        ds, items = self._ds_items()
        return effective_slot_state(
            owned, murk, cap, list(sells), list(mints), delta, ds, items)

    def test_clean_diff_passes_base_state_through(self):
        owned = [_owned(0xC1000001), _owned(0xC1000002)]
        s = self._state(owned, murk=5_000, cap=3)
        assert s.owned == owned
        assert s.wallet == 5_000 and s.murk_raw == 5_000
        assert s.pending_sold_refund == 0
        assert s.storage_left_ingame == RELIC_STORAGE_CAP - 2
        assert s.ghost_capacity == 3

    def test_staged_batch_spends_the_wallet_down(self):
        s = self._state([], murk=100_000, cap=0, delta=-87_600)
        assert s.wallet == 12_400
        assert s.murk_raw == 100_000  # seed input stays the committed value

    def test_positive_delta_is_clamped(self):
        s = self._state([], murk=5_000, cap=0, delta=10**9)
        assert s.wallet == 5_000

    def test_wallet_never_negative(self):
        s = self._state([], murk=500, cap=0, delta=-10_000)
        assert s.wallet == 0

    def test_sells_refund_and_free_storage_and_ghosts(self):
        owned = [
            _owned(0xC1000001, effects=[101, 102, 103]),  # Grand -> 550
            _owned(0xC1000002, effects=[101], is_deep=True),  # Deep x2 -> 300
            _owned(0xC1000003),
        ]
        s = self._state(
            owned, murk=1_000, cap=1,
            sells=[0xC1000001, 0xC1000002],
        )
        assert {o.ga_handle for o in s.owned} == {0xC1000003}
        assert s.pending_sold_refund == (
            sell_value(3, False) + sell_value(1, True)
        )
        assert s.storage_left_ingame == RELIC_STORAGE_CAP - 1
        assert s.ghost_capacity == 1 + 2  # each sell tombstones a ghost slot

    def test_mints_occupy_storage_and_consume_ghosts(self):
        mint = _legal_mint()
        s = self._state([_owned(0xC1000001)], murk=1_000, cap=1, mints=[mint])
        assert {o.ga_handle for o in s.owned} == {0xC1000001, mint.handle}
        assert s.mint_count == 1
        assert s.storage_left_ingame == RELIC_STORAGE_CAP - 2
        assert s.ghost_capacity == 0  # the staged mint will consume the ghost

    def test_ghost_capacity_never_negative(self):
        s = self._state([], murk=0, cap=0, mints=[_legal_mint()])
        assert s.ghost_capacity == 0
