"""Regression: expired-temporary provisions are minted as §0 ``temporary_expiry`` tombstones.

Statute 2002/1290 has provisions that were INSERTed as
``variant_kind="temporary"`` versions with an explicit ``expires`` date now in
the past and were NEVER repealed. Before the fix they silently dropped out of
``materialized_state.ir`` (the active-version selector returns ``None`` and the
materializer skips the address) with NO ``TombstoneRecord`` minted — an
unaccounted disappearance, i.e. an AGENTS.md §0 over-repeal-visibility
violation.

The fix mints a distinct ``TombstoneRecord`` with
``disposition == "temporary_expiry"`` (and ``variant_kind == "temporary"``) for
each such dropped subtree root, so the sunset-expiry disappearance is surfaced
in ``products.tombstones`` exactly the way a classic repeal is — without
disturbing the legitimate single-tombstone REPEALs.

NOTE ON SCOPE: the structural-stage ``unowned_violation`` residuals for these
addresses are produced by a SEPARATE per-op write-receipt mechanism
(``tree_ops.structural_stage_result`` / ``aggregate_structural_stage``), and
2002/1290 carries ~1088 such residuals pre-existing. There is no
tombstone→residual reconciliation contract in the pipeline, so minting a
tombstone does NOT (and is not expected to) clear those residuals; that would be
a separate cross-cutting change across the whole residual family / all
jurisdictions. This test therefore asserts the tombstone accounting, not
residual neutralization.
"""

from __future__ import annotations

import pytest

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.finland.replay_entrypoint import ReplayXmlRequest, replay_xml
from lawvm.finland.replay_products import ReplayProducts

# The five TRUE §0 section-level silent-drops named in the investigation note:
# each INSERTed as a temporary, expired, never repealed.
#   ch2a/§12b, ch4/§9, ch4/§10, ch5/§3a, ch11/§4e
_EXPECTED_TEMPORARY_EXPIRY_SECTIONS = {
    ("section", "12b"),
    ("section", "9"),
    ("section", "10"),
    ("section", "3a"),
    ("section", "4e"),
}


def _address_leaf(address: LegalAddress) -> tuple[str, str]:
    kind, label = address.path[-1]
    return (kind, label)


def _addr_str(address: LegalAddress) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in address.path)


@pytest.fixture(scope="module")
def products_2002_1290() -> ReplayProducts:
    result = replay_xml(request=ReplayXmlRequest(parent_id="2002/1290", quiet=True))
    return result.products


def test_five_named_sections_are_temporary_expiry_tombstones(
    products_2002_1290: ReplayProducts,
) -> None:
    tombstones = products_2002_1290.tombstones
    temporary_expiry = [
        tomb for tomb in tombstones if tomb.disposition == "temporary_expiry"
    ]

    # Every temporary_expiry tombstone must be typed as a temporary sunset, not a
    # repeal, and carry an enacting source statute.
    for tomb in temporary_expiry:
        assert tomb.variant_kind == "temporary", _addr_str(tomb.address)
        assert tomb.source_statute, _addr_str(tomb.address)

    leaves = {_address_leaf(tomb.address) for tomb in temporary_expiry}
    missing = _EXPECTED_TEMPORARY_EXPIRY_SECTIONS - leaves
    assert not missing, (
        "expected each silent-dropped expired-temporary section to be minted as a "
        f"temporary_expiry tombstone; missing {sorted(missing)}"
    )


def test_temporary_expiry_addresses_are_absent_from_materialized_state(
    products_2002_1290: ReplayProducts,
) -> None:
    """A temporary_expiry tombstone is only minted for a genuinely dropped address."""
    materialized = products_2002_1290.materialized_state.ir

    present: set[tuple[tuple[str, str], ...]] = set()

    def _walk(node: IRNode, path: list[tuple[str, str]]) -> None:
        here = path + [(node.kind.value, node.label or "")]
        present.add(tuple((k, l) for k, l in here if k and l))
        for child in node.children:
            _walk(child, here)

    _walk(materialized, [])

    temporary_expiry = [
        tomb
        for tomb in products_2002_1290.tombstones
        if tomb.disposition == "temporary_expiry"
    ]
    assert temporary_expiry, "expected 2002/1290 to mint temporary_expiry tombstones"

    for tomb in temporary_expiry:
        key = tuple((kind, label) for kind, label in tomb.address.path)
        # The tombstoned address (by its trailing path) must not appear as a live
        # materialized node — the disappearance is real, not a false positive.
        suffix_present = any(node_key[-len(key):] == key for node_key in present)
        assert not suffix_present, (
            f"temporary_expiry tombstone {_addr_str(tomb.address)} is still present "
            "in the materialized state — it is not a genuine silent-drop"
        )


def test_temporary_expiry_tombstones_do_not_nest(
    products_2002_1290: ReplayProducts,
) -> None:
    """One tombstone per dropped subtree root (matching the repeal convention)."""
    temporary_expiry = [
        tomb
        for tomb in products_2002_1290.tombstones
        if tomb.disposition == "temporary_expiry"
    ]
    keys = {
        tuple((kind, label) for kind, label in tomb.address.path)
        for tomb in temporary_expiry
    }
    nested = [
        key
        for key in keys
        if any(other != key and key[: len(other)] == other for other in keys)
    ]
    assert not nested, (
        "temporary_expiry tombstones must not nest under one another; found child "
        f"tombstones under a tombstoned ancestor: {sorted(nested)}"
    )


def test_legitimate_repeal_tombstones_remain_permanent_repeals(
    products_2002_1290: ReplayProducts,
) -> None:
    """Legitimate REPEAL tombstones are untouched by the temporary-expiry fix.

    Control: ch13/§5 (repealed by 2024/336 "kumotaan ... 13 luvun 5 §") must stay
    a permanent REPEAL tombstone, NOT be reclassified as a temporary_expiry.
    """
    tombstones = products_2002_1290.tombstones
    repeals = [tomb for tomb in tombstones if tomb.disposition == "repeal"]

    assert repeals, "expected 2002/1290 to carry legitimate repeal tombstones"

    # Every repeal tombstone stays a permanent repeal.
    for tomb in repeals:
        assert tomb.disposition == "repeal", _addr_str(tomb.address)

    # Control: ch13/§5 is a permanent REPEAL, never a temporary_expiry.
    ch13_s5 = [
        tomb
        for tomb in tombstones
        if tomb.address.path[-1] == ("section", "5")
        and any(node == ("chapter", "13") for node in tomb.address.path)
    ]
    assert ch13_s5, "expected a tombstone for the ch13/§5 control"
    for tomb in ch13_s5:
        assert tomb.disposition == "repeal"
        assert tomb.variant_kind == "permanent"
