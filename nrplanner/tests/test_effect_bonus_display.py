"""Per-copy magnitude formatting for the inventory effect tooltip (Phase 6)."""
from nrplanner.data import SourceDataHandler

_fmt = SourceDataHandler._format_effect_bonus


def test_multiplicative_renders_percent():
    assert _fmt({"value": 1.15, "mode": "multiplicative", "unit": "%"}) == "+15%"
    assert _fmt({"value": 1.12, "mode": "multiplicative", "unit": "%"}) == "+12%"


def test_multiplicative_reduction_includes_unit():
    out = _fmt(
        {"value": 0.10, "mode": "multiplicative_reduction", "unit": "Physical Negation"}
    )
    assert out == "+10% Physical Negation"


def test_additive_flat_is_whole_number_plus_unit():
    assert _fmt({"value": 75, "mode": "additive_flat", "unit": "Resistance"}) == "+75 Resistance"
    # Non-integer flat values keep their fractional part.
    assert _fmt({"value": 2.5, "mode": "additive_flat", "unit": "Stamina"}) == "+2.5 Stamina"
