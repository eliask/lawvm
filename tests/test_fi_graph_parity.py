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
import datetime as dt
from collections import Counter
from dataclasses import replace
from typing import Any, cast

import pytest

from lawvm.core.legal_surface_assembler import SurfaceAssemblyError
from lawvm.core.reference_mention import (
    ReferenceMention,
    reference_mention_to_row,
)
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.projection import (
    PAYLOAD_GAP_ROW_FIELDS,
    ROUND_TRIPPABLE_ROW_FIELDS,
    ReferenceSuccessorChainWitness,
    ReferenceSuccessorProjectionRow,
    graph_to_fi_refs_rows,
    graph_to_reference_successor_rows,
    graph_to_reference_mentions,
)
from lawvm.finland.references.resolve import (
    StatuteSuccessorEdge,
    SuccessorReferenceReasonCode,
    SuccessorReferenceResolutionBasis,
    SuccessorReferenceStatus,
)
from lawvm.finland.references.elliptical_resolve import resolve_elliptical_mentions
from lawvm.finland.references.ref_mention_extractor import (
    extract_all_reference_mentions,
)


def _pipeline_mentions(xml_bytes: bytes, statute_id: str) -> list[ReferenceMention]:
    """The mention set that actually FEEDS the graph's ReferenceLens.

    The ``ReferenceLens`` does not project the raw extractor output directly: it
    first runs the elliptical-resolution pass (``resolve_elliptical_mentions``),
    which fills a bare-momentti / bare-kohta internal reference's omitted address
    against the materialized AKN tree (order- and cardinality-preserving). The
    parity baseline must mirror that same pipeline, or the graph (which DID run
    the pass) would diverge from a baseline that did NOT — a comparison artifact,
    not a real fidelity gap. This is the like-for-like reference path.
    """
    result = extract_all_reference_mentions(xml_bytes, statute_id)
    return [
        res.mention
        for res in resolve_elliptical_mentions(list(result.mentions), xml_bytes)
    ]


def _assert_successor_projection_span_slices_to_text(
    row: ReferenceSuccessorProjectionRow,
    *,
    xml_bytes: bytes,
    expected_text: str,
) -> None:
    """Assert the successor row keeps a usable byte-span witness."""
    assert row.source_span_file
    assert row.source_span_byte_offset is not None
    assert row.source_span_len is not None
    assert row.source_span_len > 0
    assert (
        xml_bytes[
            row.source_span_byte_offset : row.source_span_byte_offset
            + row.source_span_len
        ]
        == expected_text.encode("utf-8")
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

_XML_RADIATION_SUCCESSOR = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_3"><num>3 §</num><content>
    <p>Tätä lakia ei sovelleta säteilylaissa (592/1991) tarkoitettuun toimintaan.</p>
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
    return [
        reference_mention_to_row(m)
        for m in _pipeline_mentions(xml_bytes, statute_id)
    ]


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
    pipeline = _pipeline_mentions(xml_bytes, statute_id)
    graph = build_legal_surface_graph(xml_bytes, statute_id)
    n_graph = len(graph_to_fi_refs_rows(graph))
    assert n_graph == len(pipeline)


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

    The extractor anchors a ``<ref>``-element mention's authoritative byte span
    to the INNER citation surface (the ``5 §:aa`` text node), not the ``<ref>``
    wrapper — the same surface it records as ``surface_text``. The projection
    must reproduce that span byte-identically (a verbatim slice of xml_bytes that
    equals the row's surface), without confusing it with the graph's char anchor.
    """
    statute_id, xml_bytes = _SYNTHETIC_CASES[1]  # the <ref>-element case
    graph = build_legal_surface_graph(xml_bytes, statute_id)
    mentions = graph_to_reference_mentions(graph)
    rows = graph_to_fi_refs_rows(graph)
    # Pair each row with its reconstructed mention (same order) to read the
    # surface the extractor anchored the span to — surface_text is not a fi_refs
    # row column, but rides the reconstructed ReferenceMention.
    ref_pairs = [
        (row, m)
        for row, m in zip(rows, mentions, strict=True)
        if row["phrase_lemma"] == "ref_element"
    ]
    assert ref_pairs, "expected at least one ref_element row for the <ref> fixture"
    for row, mention in ref_pairs:
        assert row["source_span_file"] == statute_id
        assert isinstance(row["source_span_byte_offset"], int)
        assert isinstance(row["source_span_len"], int)
        # The recovered byte span slices back to the inner citation surface the
        # extractor anchored (the same text it stores as surface_text), verbatim.
        off = row["source_span_byte_offset"]
        length = row["source_span_len"]
        sliced = xml_bytes[off : off + length]
        assert length > 0
        assert sliced == mention.surface_text.encode("utf-8")
        assert not sliced.startswith(b"<ref")


def test_graph_to_reference_mentions_returns_valid_records() -> None:
    """The reconstructed records are well-formed ReferenceMention instances."""
    statute_id, xml_bytes = _SYNTHETIC_CASES[0]
    graph = build_legal_surface_graph(xml_bytes, statute_id)
    mentions = graph_to_reference_mentions(graph)
    assert mentions
    for m in mentions:
        assert isinstance(m, ReferenceMention)


def test_graph_projects_reference_successor_rows_without_rewriting_fi_refs() -> None:
    """B5 successor rows are public projection rows, not fi_refs rewrites."""
    edge = StatuteSuccessorEdge(
        predecessor_work_id="1991/592",
        successor_work_id="859/2018",
        effective_from=dt.date(2018, 12, 15),
        witness_id="finlex:1991/592:repealed-by:859/2018",
        witness_text="Tämä laki on kumottu lailla 859/2018.",
    )
    graph = build_legal_surface_graph(
        _XML_RADIATION_SUCCESSOR,
        "527/2014",
        statute_registry=object(),
        successor_edges=(edge,),
        successor_as_of="2026-01-01",
        surface_time="2026-01-01",
    )

    successor_rows = graph_to_reference_successor_rows(graph)
    assert len(successor_rows) == 1
    successor_row = successor_rows[0]
    assert successor_row.source_work_id == "527/2014"
    assert successor_row.source_provision_ref_str == "527/2014"
    _assert_successor_projection_span_slices_to_text(
        successor_row,
        xml_bytes=_XML_RADIATION_SUCCESSOR,
        expected_text="säteilylaissa (592/1991)",
    )
    assert successor_row.surface_text == "säteilylaissa (592/1991)"
    assert successor_row.literal_work_id == "1991/592"
    assert successor_row.operative_work_id == "859/2018"
    assert successor_row.successor_as_of == "2026-01-01"
    assert successor_row.successor_status is SuccessorReferenceStatus.RESOLVED
    assert (
        successor_row.successor_resolution_basis
        is SuccessorReferenceResolutionBasis.SUCCESSOR_CHAIN
    )
    assert successor_row.successor_candidates == ("859/2018",)
    assert successor_row.successor_rejected_candidates == ()
    assert (
        successor_row.successor_reason_code
        is SuccessorReferenceReasonCode.UNIQUE_WITNESSED_SUCCESSOR_CHAIN
    )
    assert successor_row.successor_chain == (
        ReferenceSuccessorChainWitness(
            predecessor_work_id="1991/592",
            successor_work_id="859/2018",
            effective_from=dt.date(2018, 12, 15),
            witness_id="finlex:1991/592:repealed-by:859/2018",
            witness_text="Tämä laki on kumottu lailla 859/2018.",
            rule_id="fi.reference_successor.witnessed_edge",
        ),
    )

    fi_refs_rows = graph_to_fi_refs_rows(graph)
    assert len(fi_refs_rows) == 1
    assert fi_refs_rows[0]["target_statute_id"] == "1991/592"
    assert fi_refs_rows[0]["target_provision_ref_str"] == "1991/592"


def test_reference_successor_projection_rejects_unknown_status_payload() -> None:
    """Successor graph payload strings are retyped before row projection."""
    edge = StatuteSuccessorEdge(
        predecessor_work_id="1991/592",
        successor_work_id="859/2018",
        effective_from=dt.date(2018, 12, 15),
        witness_id="finlex:1991/592:repealed-by:859/2018",
        witness_text="Tämä laki on kumottu lailla 859/2018.",
    )
    graph = build_legal_surface_graph(
        _XML_RADIATION_SUCCESSOR,
        "527/2014",
        statute_registry=object(),
        successor_edges=(edge,),
        successor_as_of="2026-01-01",
        surface_time="2026-01-01",
    )
    nodes = dict(graph.nodes)
    resolution_node = next(
        node
        for node in nodes.values()
        if "successor_resolution_status" in node.payload
    )
    bad_payload = dict(resolution_node.payload)
    bad_payload["successor_resolution_status"] = "handwaved"
    nodes[resolution_node.node_id] = replace(
        resolution_node,
        payload=bad_payload,
    )
    bad_graph = replace(graph, nodes=nodes)

    with pytest.raises(ValueError, match="handwaved"):
        graph_to_reference_successor_rows(bad_graph)


def test_reference_successor_projection_rejects_invalid_chain_date() -> None:
    """Successor chain witnesses retype effective_from before row projection."""
    edge = StatuteSuccessorEdge(
        predecessor_work_id="1991/592",
        successor_work_id="859/2018",
        effective_from=dt.date(2018, 12, 15),
        witness_id="finlex:1991/592:repealed-by:859/2018",
        witness_text="Tämä laki on kumottu lailla 859/2018.",
    )
    graph = build_legal_surface_graph(
        _XML_RADIATION_SUCCESSOR,
        "527/2014",
        statute_registry=object(),
        successor_edges=(edge,),
        successor_as_of="2026-01-01",
        surface_time="2026-01-01",
    )
    nodes = dict(graph.nodes)
    resolution_node = next(
        node
        for node in nodes.values()
        if "successor_resolution_status" in node.payload
    )
    bad_payload = dict(resolution_node.payload)
    chain = list(cast(list[dict[str, Any]], bad_payload["successor_chain"]))
    first = dict(chain[0])
    first["effective_from"] = "15.12.2018"
    chain[0] = first
    bad_payload["successor_chain"] = chain
    nodes[resolution_node.node_id] = replace(
        resolution_node,
        payload=bad_payload,
    )
    bad_graph = replace(graph, nodes=nodes)

    with pytest.raises(ValueError, match="effective_from must be an ISO date"):
        graph_to_reference_successor_rows(bad_graph)


def test_graph_without_successor_context_projects_no_successor_rows() -> None:
    graph = build_legal_surface_graph(_XML_RADIATION_SUCCESSOR, "527/2014")
    assert graph_to_reference_successor_rows(graph) == []


# An internal cite plus discourse anaphors (``tämän lain`` / ``mainitun lain``).
# The AnaphoraLens (fi.anaphora.v0) reuses the ``reference_expr`` node kind for a
# uniform census, but its payload carries a discourse ``resolution_status``, not
# the fi_refs ``cite_confidence`` — so a projection that read EVERY
# ``reference_expr`` node (regardless of lens) would fail loud on the absent
# cite_confidence. This fixture mints both an fi.references.v0 expr AND an
# fi.anaphora.v0 expr so the regression is exercised.
_XML_WITH_ANAPHORA = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Tata lakia sovelletaan ymparistonsuojelulain (527/2014) 5 §:ssa
    tarkoitettuun toimintaan. Tata lakia ei sovelleta merialueisiin.
    Mainitun lain mukaan menetellaan.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")


def test_projection_ignores_anaphora_lens_reference_expr_nodes() -> None:
    """A graph with anaphora ``reference_expr`` nodes still projects cleanly.

    Regression for the cross-lens collision: the discourse AnaphoraLens mints
    ``reference_expr`` nodes without a ``cite_confidence`` payload (it carries a
    discourse ``resolution_status`` instead). The fi_refs projection is the
    inverse of the H1 ReferenceLens ONLY, so it must scope to that lens and not
    mis-read the anaphora census nodes (which would fail loud on the absent
    cite_confidence). Every projected mention carries a valid cite_confidence and
    its expr/resolution agree.
    """
    statute_id = "900/2024"
    graph = build_legal_surface_graph(_XML_WITH_ANAPHORA, statute_id)

    # The fixture must actually mint BOTH lenses' reference_expr nodes, else the
    # regression isn't exercised.
    expr_lenses = {
        node.lens_id
        for node in graph.nodes.values()
        if node.node_kind == "reference_expr"
    }
    assert "fi.anaphora.v0" in expr_lenses, "fixture must mint an anaphora expr node"
    assert "fi.references.v0" in expr_lenses, "fixture must mint a references expr node"

    # No crash, and every reconstructed mention has a real cite_confidence.
    mentions = graph_to_reference_mentions(graph)
    assert mentions
    for m in mentions:
        assert m.cite_confidence is not None

    # The fi_refs rows round-trip against the same elliptical-aware pipeline; the
    # anaphora-lens expr nodes contribute NO fi_refs row.
    expected = Counter(_key(r) for r in _extractor_rows(statute_id, _XML_WITH_ANAPHORA))
    actual = Counter(_key(r) for r in graph_to_fi_refs_rows(graph))
    assert actual == expected
    n_anaphora_expr = sum(
        1
        for node in graph.nodes.values()
        if node.node_kind == "reference_expr" and node.lens_id == "fi.anaphora.v0"
    )
    assert n_anaphora_expr >= 1
    assert len(graph_to_fi_refs_rows(graph)) == len(
        _pipeline_mentions(_XML_WITH_ANAPHORA, statute_id)
    )


# ── Real-corpus parity (opt-in via the canonical data root) ───────────────────


def _corpus_available() -> bool:
    return bool(os.environ.get("LAWVM_CANONICAL_DATA_ROOT"))


@pytest.mark.skipif(
    not _corpus_available(),
    reason="LAWVM_CANONICAL_DATA_ROOT not set; real-corpus parity skipped",
)
@pytest.mark.slow
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
        pipeline = _pipeline_mentions(xml_bytes, statute_id)
        if len(pipeline) < 3:
            continue

        expected = Counter(_key(r) for r in _extractor_rows(statute_id, xml_bytes))
        try:
            graph = build_legal_surface_graph(xml_bytes, statute_id)
        except SurfaceAssemblyError:
            # Skip a statute whose MULTI-LENS graph cannot be assembled for a
            # reason ORTHOGONAL to reference round-tripping (e.g. a defect in a
            # non-reference lens such as fi.definitions.v0). This gate proves the
            # references/projection parity, not every lens's assembly invariant;
            # an unrelated lens defect must not mask or fail the reference gate.
            continue
        graph_rows = graph_to_fi_refs_rows(graph)
        actual = Counter(_key(r) for r in graph_rows)

        # Cardinality identity (no mention dropped).
        assert len(graph_rows) == len(pipeline), (
            f"{statute_id}: cardinality diverged "
            f"(graph {len(graph_rows)} vs extractor {len(pipeline)})"
        )
        # Full-row parity.
        assert actual == expected, (
            f"{statute_id}: real-corpus graph->fi_refs FULL-row parity diverged.\n"
            f"  only in extractor: {sorted(map(str, (expected - actual).elements()))[:5]}\n"
            f"  only in graph:     {sorted(map(str, (actual - expected).elements()))[:5]}"
        )
        checked += 1

    assert checked >= 4, f"expected >=4 real-corpus statutes checked, got {checked}"
