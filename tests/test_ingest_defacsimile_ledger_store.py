"""Hermetic tests: de-facsimile ledger persistence (Decision 5).

``ParsedIrStore.put_ledger`` / ``get_ledger`` round-trip the FULL ledger through a
sibling content-addressed blob; ``defacsimile_manifest_summary`` yields the
op/tier histograms + SINGLE_WITNESS-drop count the manifest carries. Temp farchive,
no network.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from lawvm.core.source_document.ir import AssuranceTier
from lawvm.ingest.defacsimile import (
    DeFacsimileClaim,
    DeFacsimileLedger,
    DeFacsimileOp,
)
from lawvm.ingest.parsed_store import (
    ParsedIrStore,
    defacsimile_ledger_locator,
    defacsimile_manifest_summary,
    deserialize_defacsimile_ledger,
    serialize_defacsimile_ledger,
)
from lawvm.ingest.simulacrum import SpanRef

_DIGEST = "b" * 64


def _ledger() -> DeFacsimileLedger:
    return DeFacsimileLedger(
        claims=(
            DeFacsimileClaim(
                DeFacsimileOp.DROP_FURNITURE,
                (SpanRef(1, (0,)),),
                AssuranceTier.SINGLE_WITNESS,
                ("defacsimile_adjudicator",),
                rationale="header",
            ),
            DeFacsimileClaim(
                DeFacsimileOp.REJOIN,
                (SpanRef(1, (1,)), SpanRef(2, (0,))),
                AssuranceTier.MULTI_WITNESS_ADJUDICATED,
                ("defacsimile_adjudicator", "affordance:margin_band"),
                absorbed=(SpanRef(2, (0, 0)),),
            ),
        )
    )


def test_ledger_serialize_roundtrip_byte_stable() -> None:
    ledger = _ledger()
    b1 = serialize_defacsimile_ledger(ledger)
    b2 = serialize_defacsimile_ledger(deserialize_defacsimile_ledger(b1))
    assert b1 == b2
    back = deserialize_defacsimile_ledger(b1)
    assert back.claims[0].op is DeFacsimileOp.DROP_FURNITURE
    assert back.claims[1].absorbed == (SpanRef(2, (0, 0)),)
    assert back.claims[1].tier is AssuranceTier.MULTI_WITNESS_ADJUDICATED


def test_put_get_ledger_roundtrip_via_store() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "parsed.farchive")
        store = ParsedIrStore(path)
        try:
            locator = defacsimile_ledger_locator(_DIGEST, "adjudicated_vision", "v1+defacsimile.v1")
            store.put_ledger(locator, _ledger(), source_digest=_DIGEST)
            got = store.get_ledger(locator)
        finally:
            store.close()
    assert got is not None
    assert len(got.claims) == 2
    assert got.claims[0].targets == (SpanRef(1, (0,)),)


def test_get_ledger_absent_is_none() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "parsed.farchive")
        store = ParsedIrStore(path)
        try:
            assert store.get_ledger("parsed/nope/x@y/defacsimile_ledger.json") is None
        finally:
            store.close()


def test_manifest_summary_histograms_and_sw_drop_count() -> None:
    ledger = DeFacsimileLedger(
        claims=(
            DeFacsimileClaim(DeFacsimileOp.DROP_FURNITURE, (SpanRef(1, (0,)),), AssuranceTier.SINGLE_WITNESS, ("a",)),
            DeFacsimileClaim(DeFacsimileOp.DROP_FURNITURE, (SpanRef(1, (1,)),), AssuranceTier.MULTI_WITNESS_ADJUDICATED, ("a",)),
            DeFacsimileClaim(DeFacsimileOp.REJOIN, (SpanRef(1, (2,)), SpanRef(2, (0,))), AssuranceTier.SINGLE_WITNESS, ("a",)),
        )
    )
    s = defacsimile_manifest_summary(ledger)
    assert s["op_histogram"] == {"drop_furniture": 2, "rejoin": 1}
    assert s["tier_histogram"] == {"SINGLE_WITNESS": 2, "MULTI_WITNESS_ADJUDICATED": 1}
    # only the SINGLE_WITNESS drop counts (the MW one does not; REJOIN is not a drop).
    assert s["single_witness_drop_count"] == 1
    assert s["claim_count"] == 3
