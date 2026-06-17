"""Gate for embedded EU repeal-reference provenance (R6).

When a long-form EU citation brackets a repeal token between two EU act ids:

    "... asetuksen (EY) N:o 1774/2002 kumoamisesta (sivutuoteasetus) annetussa
     Euroopan parlamentin ja neuvoston asetuksessa (EY) N:o 1069/2009"

the OUTER id (1069/2009 — the act actually being cited/applied) is the PRIMARY
target, and the INNER id (1774/2002 — the act it repealed) is provenance, typed
``role="repealed_embedded"`` at the recognizer level and surfaced by the
cross_refs lane as ``edge_subtype="REPEALS_EMBEDDED"``.

Covers:
  - two-id repeal clause => outer primary + inner repealed_embedded (recognizer)
  - cross_refs lane => two distinct edges, the inner one REPEALS_EMBEDDED
  - single-id EU cite => unchanged (regression, role stays "primary")
  - the parenthetical nickname (sivutuoteasetus) is NOT double-counted as an act
"""
from __future__ import annotations

from lawvm.finland.references.cross_refs import extract_eu_refs
from lawvm.finland.references.eu_reference import (
    DIALECT_CROSS_REF,
    recognize_eu_acts,
)

# The canonical R6 specimen clause (sivutuoteasetus). Outer act 1069/2009 is the
# cited/applied act; inner act 1774/2002 is the act it repealed (provenance).
_REPEAL_CLAUSE = (
    "tuotteiden terveyssäännöistä sekä asetuksen (EY) N:o 1774/2002 "
    "kumoamisesta (sivutuoteasetus) annetussa Euroopan parlamentin ja "
    "neuvoston asetuksessa (EY) N:o 1069/2009"
)

# A bare single-id EU cite — must be untouched by the embedded-repeal logic.
_SINGLE_CLAUSE = (
    "annetussa Euroopan parlamentin ja neuvoston asetuksessa (EY) N:o 1069/2009"
)


def _xml(body_text: str) -> bytes:
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><body><section><num>1 §</num><paragraph><content><p>"
        f"{body_text}"
        "</p></content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Recognizer level (eu_reference.recognize_eu_acts)
# ---------------------------------------------------------------------------


def test_two_id_repeal_clause_outer_primary_inner_embedded() -> None:
    acts = recognize_eu_acts(_REPEAL_CLAUSE, dialect=DIALECT_CROSS_REF)
    by_id = {(a.year, a.number): a for a in acts}

    # Exactly the two EU acts, no spurious extras (nickname not recognized).
    assert set(by_id) == {("2002", "1774"), ("2009", "1069")}

    inner = by_id[("2002", "1774")]
    outer = by_id[("2009", "1069")]
    assert inner.role == "repealed_embedded"
    assert outer.role == "primary"
    # The inner act precedes the outer act in source order.
    assert inner.start < outer.start


def test_single_id_cite_role_unchanged() -> None:
    acts = recognize_eu_acts(_SINGLE_CLAUSE, dialect=DIALECT_CROSS_REF)
    assert [(a.year, a.number) for a in acts] == [("2009", "1069")]
    # No second act, so no repeal cue can bracket anything: role stays primary.
    assert acts[0].role == "primary"


def test_nickname_parenthetical_not_recognized_as_act() -> None:
    # (sivutuoteasetus) is a defined-term nickname owned by the R4 binder; the
    # EU recognizer must neither emit it as an act nor let it break outer-id
    # detection. We assert it produces no third EuActRef.
    acts = recognize_eu_acts(_REPEAL_CLAUSE, dialect=DIALECT_CROSS_REF)
    assert len(acts) == 2
    raws = [a.raw for a in acts]
    assert all("sivutuoteasetus" not in r for r in raws)


# ---------------------------------------------------------------------------
# cross_refs lane integration (edge_subtype="REPEALS_EMBEDDED")
# ---------------------------------------------------------------------------


def test_cross_refs_emits_two_typed_edges() -> None:
    edges = extract_eu_refs(_xml(_REPEAL_CLAUSE), "2010/1")
    by_target = {e.target_statute_id: e for e in edges}

    assert "eu/act/2002/1774" in by_target
    assert "eu/act/2009/1069" in by_target

    inner_edge = by_target["eu/act/2002/1774"]
    outer_edge = by_target["eu/act/2009/1069"]
    assert inner_edge.edge_subtype == "REPEALS_EMBEDDED"
    assert outer_edge.edge_subtype == ""
    # Both are textual CITES edges from the source statute.
    assert inner_edge.edge_type == "CITES"
    assert outer_edge.edge_type == "CITES"


def test_cross_refs_single_id_unchanged() -> None:
    edges = extract_eu_refs(_xml(_SINGLE_CLAUSE), "2010/1")
    assert [e.target_statute_id for e in edges] == ["eu/act/2009/1069"]
    assert edges[0].edge_subtype == ""


def test_eu_year_first_slash_directive_recognized() -> None:
    """Year-first slash form ``YEAR/NUMBER/EY`` must be recognised.

    ``Neuvoston direktiivi 2001/23/EY`` is year-first (year 2001, act 23). The
    shared NUMBER/YEAR/FORM recognizer requires a 4-digit MIDDLE group, so the
    small act number after the year was left unrecognised — the citation yielded
    zero EU edges.
    """
    body = "Neuvoston direktiivi 2001/23/EY; EYVL N:o L 82, 22.3.2001."
    edges = extract_eu_refs(_xml(body), "2002/943")
    by_target = {e.target_statute_id: e for e in edges}
    assert "eu/dir/2001/23" in by_target
    edge = by_target["eu/dir/2001/23"]
    assert edge.edge_type == "CITES"
    assert edge.surface_text == "2001/23/EY"


def test_eu_number_first_slash_still_number_first() -> None:
    """The number-first form ``NUMBER/YEAR/EY`` must keep number-first reading.

    The new year-first pattern must not steal the established number-first
    interpretation of e.g. ``999/2001/EY`` (act 999, year 2001).
    """
    edges = extract_eu_refs(_xml("asetus 999/2001/EY"), "2010/1")
    assert [e.target_statute_id for e in edges] == ["eu/reg/2001/999"]


def test_eu_edge_surface_and_byte_span_with_non_ascii_prefix() -> None:
    """EU edges carry a surface and a byte span that slices to that surface.

    Locks in correct surface propagation and char→byte offset conversion even
    when non-ASCII characters precede the citation in the same paragraph.
    """
    body = "Tämä äöä viittaa asetukseen (EU) 2016/679 yleinen tietosuoja."
    xml = _xml(body)
    edges = extract_eu_refs(xml, "2020/1")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.surface_text == "(EU) 2016/679"
    assert edge.source_byte_offset is not None
    sliced = xml[edge.source_byte_offset : edge.source_byte_offset + edge.source_byte_len]
    assert sliced.decode("utf-8") == edge.surface_text
