"""Unit tests for the discriminated unions in app.models.

These pin behaviour that the route tests only reach indirectly.  The
export-loadouts route tests in tests/api/routes/test_saves.py exercise most op
kinds end-to-end, but all of them except the unknown-tag case are skipif-gated
on the gitignored NR0000.sl2 fixture, so they do not run in CI -- and the
``delete`` op has no route test at all.  Parsing is fixture-free, so it is
pinned here instead.
"""
import pytest
from pydantic import TypeAdapter, ValidationError

from app.models import (
    AddLoadoutOp,
    DeleteLoadoutOp,
    LoadoutOp,
    OverwriteLoadoutOp,
    RenameLoadoutOp,
    ResetPresetsOp,
    ResetVesselsOp,
)

# Built exactly as POST /saves/export-loadouts builds it (see saves.py).
_ADAPTER = TypeAdapter(list[LoadoutOp])

_OVERWRITE = {
    "op": "overwrite", "index": 0, "character": "Revenant",
    "vessel_id": 6002, "ga_handles": [0, 0, 0, 0, 0, 0],
}
_ADD = {
    "op": "add", "character": "Wylder", "vessel_id": 1002,
    "ga_handles": [0, 0, 0, 0, 0, 0], "name": "api add",
}


class TestLoadoutOpUnion:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"op": "reset_vessels"}, ResetVesselsOp),
            ({"op": "reset_presets"}, ResetPresetsOp),
            ({"op": "delete", "index": 3}, DeleteLoadoutOp),
            ({"op": "rename", "index": 1, "name": "x"}, RenameLoadoutOp),
            (_OVERWRITE, OverwriteLoadoutOp),
            (_ADD, AddLoadoutOp),
        ],
    )
    def test_each_tag_dispatches_to_its_own_variant(
        self, payload: dict, expected: type
    ) -> None:
        # The point of a discriminated union: the "op" tag alone selects the
        # model.  A union that merely *accepts* the payload is not enough --
        # assert the concrete class, or a permissive sibling could shadow it.
        (parsed,) = _ADAPTER.validate_python([payload])
        assert type(parsed) is expected
        assert parsed.op == payload["op"]

    def test_unknown_tag_is_rejected_by_the_discriminator(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _ADAPTER.validate_python([{"op": "explode"}])
        assert exc.value.errors()[0]["type"] == "union_tag_invalid"

    def test_missing_tag_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _ADAPTER.validate_python([{"index": 0, "name": "x"}])
        assert exc.value.errors()[0]["type"] == "union_tag_not_found"

    def test_tag_selects_the_variant_before_shape_is_checked(self) -> None:
        # "rename" requires a name.  If the tag genuinely picks RenameLoadoutOp,
        # the failure is a missing *field* -- not a union-tag error and not a
        # silent match against some other variant.
        with pytest.raises(ValidationError) as exc:
            _ADAPTER.validate_python([{"op": "rename", "index": 0}])
        (err,) = exc.value.errors()
        assert err["type"] == "missing"
        assert err["loc"][-1] == "name"

    def test_batch_preserves_order_and_mixes_kinds(self) -> None:
        # The route applies ops as a batch, so order and per-item typing matter.
        ops = _ADAPTER.validate_python(
            [{"op": "delete", "index": 2}, {"op": "rename", "index": 0, "name": "n"},
             {"op": "reset_vessels"}]
        )
        assert [type(o) for o in ops] == [
            DeleteLoadoutOp, RenameLoadoutOp, ResetVesselsOp
        ]
        assert ops[0].index == 2
        assert ops[1].name == "n"

    def test_optional_name_on_overwrite_defaults_to_none(self) -> None:
        (parsed,) = _ADAPTER.validate_python([_OVERWRITE])
        assert parsed.name is None
