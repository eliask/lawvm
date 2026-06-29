"""Post-construction freeze of ``WriteReceipt.pre_hashes`` / ``post_hashes``.

The apply write-receipt seam (``core/write_receipt.py``) is the producer-side
record of one landed semantic write; its ``pre_hashes`` / ``post_hashes``
mappings are canonical structural subtree hashes that downstream consumers
(mutation events, certificate transition leaves) read as evidence. A late
mutation of the dict post-construction would silently rewrite history —
exactly the silent-failure shape AGENTS.md §1.9 / §1.10 forbids at a typed
semantic boundary.

``ExecutionAuthorization.detail`` took this same shape via ``freeze_mapping``
(see ``core/execution_authorization.py``); this pins the parallel behaviour
on ``WriteReceipt``.

Run:
    uv run pytest tests/test_write_receipt_freeze.py -v
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.frozen_values import FrozenDict
from lawvm.core.write_receipt import WriteReceipt


def _bare_receipt(
    *,
    pre_hashes: dict[str, str] | None = None,
    post_hashes: dict[str, str] | None = None,
) -> WriteReceipt:
    """Build a minimal receipt exercising only the hash-mapping fields."""
    return WriteReceipt(
        op_id="op-test-001",
        helper="test_helper",
        action="REPLACE",
        bound_target_path=None,
        landed_primary_path=None,
        pre_hashes=pre_hashes or {"section/1": "abc123"},
        post_hashes=post_hashes or {"section/1": "def456"},
    )


class TestWriteReceiptHashesAreFrozen:
    """``pre_hashes`` / ``post_hashes`` MUST be ``FrozenDict`` instances that
    reject mutation post-construction, mirroring ``ExecutionAuthorization.detail``."""

    def test_pre_hashes_is_frozendict_instance(self) -> None:
        receipt = _bare_receipt()
        assert isinstance(receipt.pre_hashes, FrozenDict), (
            "pre_hashes must be frozen via freeze_mapping at __post_init__; "
            "a plain dict would let late mutation silently rewrite history."
        )

    def test_post_hashes_is_frozendict_instance(self) -> None:
        receipt = _bare_receipt()
        assert isinstance(receipt.post_hashes, FrozenDict), (
            "post_hashes must be frozen via freeze_mapping at __post_init__; "
            "a plain dict would let late mutation silently rewrite history."
        )

    def test_setitem_on_pre_hashes_raises(self) -> None:
        receipt = _bare_receipt()
        hashes = cast(Any, receipt.pre_hashes)
        with pytest.raises(TypeError, match="FrozenDict is immutable"):
            hashes["section/1"] = "tampered"

    def test_setitem_on_post_hashes_raises(self) -> None:
        receipt = _bare_receipt()
        hashes = cast(Any, receipt.post_hashes)
        with pytest.raises(TypeError, match="FrozenDict is immutable"):
            hashes["section/1"] = "tampered"

    def test_setitem_new_key_on_pre_hashes_raises(self) -> None:
        receipt = _bare_receipt()
        hashes = cast(Any, receipt.pre_hashes)
        with pytest.raises(TypeError, match="FrozenDict is immutable"):
            hashes["section/new"] = "tampered"

    def test_pop_on_pre_hashes_raises(self) -> None:
        receipt = _bare_receipt()
        hashes = cast(Any, receipt.pre_hashes)
        with pytest.raises(TypeError, match="FrozenDict is immutable"):
            hashes.pop("section/1")

    def test_clear_on_post_hashes_raises(self) -> None:
        receipt = _bare_receipt()
        hashes = cast(Any, receipt.post_hashes)
        with pytest.raises(TypeError, match="FrozenDict is immutable"):
            hashes.clear()

    def test_update_on_pre_hashes_raises(self) -> None:
        receipt = _bare_receipt()
        hashes = cast(Any, receipt.pre_hashes)
        with pytest.raises(TypeError, match="FrozenDict is immutable"):
            hashes.update({"section/1": "tampered"})

    def test_setdefault_on_post_hashes_raises(self) -> None:
        receipt = _bare_receipt()
        hashes = cast(Any, receipt.post_hashes)
        with pytest.raises(TypeError, match="FrozenDict is immutable"):
            hashes.setdefault("section/2", "tampered")


class TestWriteReceiptDefaultFactoriesProduceFrozen:
    """The default_factory=dict defaults MUST also produce frozen dicts when no
    value is supplied, so callers building a receipt without explicit hashes
    cannot accidentally receive a mutable dict at the boundary."""

    def test_default_pre_hashes_is_frozen(self) -> None:
        receipt = WriteReceipt(
            op_id="op-test-002",
            helper="test_helper",
            action="REPLACE",
            bound_target_path=None,
            landed_primary_path=None,
        )
        assert isinstance(receipt.pre_hashes, FrozenDict)
        assert isinstance(receipt.post_hashes, FrozenDict)

    def test_default_pre_hashes_rejects_mutation(self) -> None:
        receipt = WriteReceipt(
            op_id="op-test-003",
            helper="test_helper",
            action="REPLACE",
            bound_target_path=None,
            landed_primary_path=None,
        )
        hashes = cast(Any, receipt.pre_hashes)
        with pytest.raises(TypeError, match="FrozenDict is immutable"):
            hashes["any"] = "rejected"


class TestWriteReceiptFreezeIsDeep:
    """Nested values inside the hash mappings must also be frozen —
    ``freeze_mapping`` recursively freezes nested containers (per
    ``core/frozen_values.py``), and the receipt hashes are typed as
    ``Mapping[str, str]`` so this guards against the case where a caller hands
    in a dict-of-dict and would otherwise receive a partially-mutable view."""

    def test_deepcopy_round_trip_preserves_freeze(self) -> None:
        import copy

        receipt = _bare_receipt()
        cloned = copy.deepcopy(receipt)
        assert isinstance(cloned.pre_hashes, FrozenDict)
        assert isinstance(cloned.post_hashes, FrozenDict)
        hashes = cast(Any, cloned.pre_hashes)
        with pytest.raises(TypeError, match="FrozenDict is immutable"):
            hashes["section/1"] = "tampered"

    def test_underlying_dict_passing_is_copied_not_aliased(self) -> None:
        # Mutating the source dict after construction MUST NOT leak into the
        # receipt — the freeze is a value copy, not a reference alias.
        pre_source = {"section/1": "abc123"}
        receipt = _bare_receipt(pre_hashes=pre_source)
        pre_source["section/1"] = "tampered"
        assert receipt.pre_hashes["section/1"] == "abc123", (
            "freeze_mapping must deep-copy its input so the receipt's view "
            "is unaffected by later mutation of the source mapping."
        )
