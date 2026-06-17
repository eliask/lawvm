"""Phase-3b parity gate: the Legal Surface Graph round-trips to fi_refs rows.

Pro r5 §D3 ("Stage 2 parity projection"), Phase 3b ("close the payload gaps").
Proves that

    extract_all_reference_mentions -> reference_mention_to_row              (A)

and

    build_legal_surface_graph -> graph_to_fi_refs_rows                      (B)

produce the SAME fi_refs row representation — now on the FULL 14-field schema,
not just a round-trippable subset.

PHASE 3b: FULL-ROW PARITY (the five gaps are closed)
────────────────────────────────────────────────────
Phase 3 documented five gaps where (B) could not reconstruct a fi_refs field
from the node payload: source byte span, validity interval, source provision
section, target_stat_hash, and a cardinality gap (mentions with empty/
unlocatable surface never minted a ``reference_expr`` node). The lens now stashes
each mention's AUTHORITATIVE fi_refs fields in the ``reference_expr`` payload
(kept DISTINCT from the graph's char-coordinate ``source_ref``), and mints a
node for EVERY mention. So this test now asserts:

  1. FULL-ROW PARITY on all 14 fi_refs fields, as an order-insensitive MULTISET,
     over EVERY extractor mention (no expressibility filter — every mention is
     expressible now).
  2. CARDINALITY IDENTITY: |graph rows| == |all extractor mentions| (no mention
     dropped to a residual).
  3. The field sets partition the full schema (no field silently slips out).

These run on synthetic statutes plus, when the canonical corpus is available
(``LAWVM_CANONICAL_DATA_ROOT``), real Finlex statutes (>=4 statutes, >=3 mentions
each).
"""
from __future__ import annotations

import os
from collections import Counter

import pytest

from lawvm.core.reference_mention import (
    ReferenceMention,
    reference_mention_to_row,
)
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.projection import (
    PAYLOAD_GAP_ROW_FIELDS,
    ROUND_TRIPPABLE_ROW_FIELDS,
    graph_to_fi_refs_rows,
    graph_to_reference_mentions,
)
from lawvm.finland.references.ref_mention_extractor import (
    extract_all_reference_mentions,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


# ── Synthetic statute fixtures (varied lanes) ────────────────────────────────

# Plain-text id cite + internal § ref + a same-statute § ref.
_XML_PLAIN_AND_INTERNAL = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Tata lakia sovelletaan ymparistonsuojelulain (527/2014) 5 §:ssa tarkoitettuun toimintaan.</p>
    <p>Edella 1 momentissa tarkoitettuun toimintaan sovelletaan myos 5 §:n saannoksia.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

# AKN <ref> element cites (carry surface_text) + an internal § ref.
_XML_REF_ELEMENTS = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Sovelletaan <ref href="/akn/fi/act/statute/2014/527/sec_5">5 §:aa</ref> mukaisesti.</p>
    <p>Lisaksi noudatetaan <ref href="/akn/fi/act/statute/2011/379/sec_3">3 §:aa</ref>.</p>
    <p>Edella 1 momentissa tarkoitetaan 2 §:ssa saadettya.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

# A vague (OPEN, targetless) catch-all + a cross-statute by-name-ish cite.
_XML_VAGUE = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Jollei muussa laissa toisin saadeta, sovelletaan tata lakia.</p>
    <p>Sovelletaan myos tieliikennelain (729/2018) 12 §:aa.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

_SYNTHETIC_CASES = [
    ("123/2020", _XML_PLAIN_AND_INTERNAL),
    ("200/2019", _XML_REF_ELEMENTS),
    ("300/2021", _XML_VAGUE),
]

#: The full fi_refs schema, derived from a real serialized row (the parity key).
_ALL_ROW_FIELDS = tuple(
    reference_mention_to_row(
        extract_all_reference_mentions(_XML_REF_ELEMENTS, "200/2019").mentions[0]
    ).keys()
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _key(row: dict[str, object]) -> tuple[object, ...]:
    """The FULL-row parity key for one fi_refs row (all 14 fields)."""
    return tuple(row.get(f) for f in _ALL_ROW_FIELDS)


def _extractor_rows(statute_id: str, xml_bytes: bytes) -> list[dict[str, object]]:
    result = extract_all_reference_mentions(xml_bytes, statute_id)
    return [reference_mention_to_row(m) for m in result.mentions]


# ── The parity gate ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("statute_id,xml_bytes", _SYNTHETIC_CASES)
def test_graph_round_trips_to_fi_refs_rows_synthetic(
    statute_id: str, xml_bytes: bytes
) -> None:
    """Graph projection multiset == extractor multiset on ALL 14 fi_refs fields."""
    expected = Counter(_key(r) for r in _extractor_rows(statute_id, xml_bytes))

    graph = build_legal_surface_graph(xml_bytes, statute_id)
    actual = Counter(_key(r) for r in graph_to_fi_refs_rows(graph))

    assert actual == expected, (
        f"{statute_id}: graph->fi_refs FULL-row parity diverged.\n"
        f"  only in extractor: {sorted(map(str, (expected - actual).elements()))}\n"
        f"  only in graph:     {sorted(map(str, (actual - expected).elements()))}"
    )


@pytest.mark.parametrize("statute_id,xml_bytes", _SYNTHETIC_CASES)
def test_cardinality_identity_no_mention_dropped(
    statute_id: str, xml_bytes: bytes
) -> None:
    """Every extractor mention mints exactly one reference_expr node (no drop).

    The Phase-3b cardinality-gap closure: |graph rows| == |all extractor
    mentions|. No mention is silently dropped to a residual, including those with
    empty/unlocatable surface (plain_text / metadata / EU edges).
    """
    result = extract_all_reference_mentions(xml_bytes, statute_id)
    graph = build_legal_surface_graph(xml_bytes, statute_id)
    n_graph = len(graph_to_fi_refs_rows(graph))
    assert n_graph == len(result.mentions)


def test_full_schema_round_trips() -> None:
    """ROUND_TRIPPABLE now equals the full fi_refs schema; the gap set is empty.

    Pins the Phase-3b contract: every fi_refs field round-trips, and no field is
    parked in a gap set. A regression that re-introduces a payload gap would have
    to remove a field from ROUND_TRIPPABLE (caught here) to pass the parity gate.
    """
    statute_id, xml_bytes = _SYNTHETIC_CASES[1]
    result = extract_all_reference_mentions(xml_bytes, statute_id)
    assert result.mentions, "fixture must yield mentions"
    schema_fields = set(reference_mention_to_row(result.mentions[0]).keys())

    round_trippable = set(ROUND_TRIPPABLE_ROW_FIELDS)
    gaps = set(PAYLOAD_GAP_ROW_FIELDS)
    assert gaps == set(), f"no payload gaps should remain; got {gaps}"
    assert round_trippable.isdisjoint(gaps)
    assert round_trippable == schema_fields


def test_authoritative_byte_span_round_trips_for_ref_lane() -> None:
    """The <ref> lane's authoritative BYTE span survives via the payload.

    The graph's ``source_ref`` is a CHAR anchor into raw_text; the row's byte
    span is the extractor's BYTE offset into xml_bytes. They are distinct
    coordinate spaces, and the byte span must come through field-identically.
    """
    statute_id, xml_bytes = _SYNTHETIC_CASES[1]  # the <ref>-element case
    graph = build_legal_surface_graph(xml_bytes, statute_id)
    rows = graph_to_fi_refs_rows(graph)
    ref_rows = [r for r in rows if r["phrase_lemma"] == "ref_element"]
    assert ref_rows, "expected at least one ref_element row for the <ref> fixture"
    for row in ref_rows:
        assert row["source_span_file"] == statute_id
        assert isinstance(row["source_span_byte_offset"], int)
        assert isinstance(row["source_span_len"], int)
        # The recovered byte span slices back to the <ref>…</ref> element.
        off = row["source_span_byte_offset"]
        length = row["source_span_len"]
        sliced = xml_bytes[off : off + length]
        assert sliced.startswith(b"<ref")
        assert sliced.endswith(b"</ref>")


def test_graph_to_reference_mentions_returns_valid_records() -> None:
    """The reconstructed records are well-formed ReferenceMention instances."""
    statute_id, xml_bytes = _SYNTHETIC_CASES[0]
    graph = build_legal_surface_graph(xml_bytes, statute_id)
    mentions = graph_to_reference_mentions(graph)
    assert mentions
    for m in mentions:
        assert isinstance(m, ReferenceMention)


# ── Real-corpus parity (opt-in via the canonical data root) ───────────────────


def _corpus_available() -> bool:
    return bool(os.environ.get("LAWVM_CANONICAL_DATA_ROOT"))


@pytest.mark.skipif(
    not _corpus_available(),
    reason="LAWVM_CANONICAL_DATA_ROOT not set; real-corpus parity skipped",
)
def test_graph_round_trips_real_corpus_statutes() -> None:
    """FULL-row parity + cardinality identity on real Finlex statutes.

    Checks >=4 statutes that each yield >=3 mentions; asserts the graph
    projection reproduces every fi_refs row exactly (14-field multiset) and drops
    no mention.
    """
    from lawvm.finland.corpus import get_corpus_store

    store = get_corpus_store()
    all_ids = store.list_statute_ids()
    checked = 0
    for statute_id in all_ids:
        if checked >= 4:
            break
        try:
            xml_bytes = store.read_oracle(statute_id)
        except Exception:
            continue
        if not xml_bytes:
            continue
        result = extract_all_reference_mentions(xml_bytes, statute_id)
        if len(result.mentions) < 3:
            continue

        expected = Counter(_key(r) for r in _extractor_rows(statute_id, xml_bytes))
        graph = build_legal_surface_graph(xml_bytes, statute_id)
        graph_rows = graph_to_fi_refs_rows(graph)
        actual = Counter(_key(r) for r in graph_rows)

        # Cardinality identity (no mention dropped).
        assert len(graph_rows) == len(result.mentions), (
            f"{statute_id}: cardinality diverged "
            f"(graph {len(graph_rows)} vs extractor {len(result.mentions)})"
        )
        # Full-row parity.
        assert actual == expected, (
            f"{statute_id}: real-corpus graph->fi_refs FULL-row parity diverged.\n"
            f"  only in extractor: {sorted(map(str, (expected - actual).elements()))[:5]}\n"
            f"  only in graph:     {sorted(map(str, (actual - expected).elements()))[:5]}"
        )
        checked += 1

    assert checked >= 4, f"expected >=4 real-corpus statutes checked, got {checked}"
