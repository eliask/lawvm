"""Tests for the canonical forward-grant -> DelegationEdge adapter.

The adapter (:func:`...delegation_edge_adapter.extract_delegations_canonical`)
lowers canonical :class:`DelegationGrant` cores to the production
:class:`lawvm.finland.delegation.DelegationEdge` shape the StatuteGraph consumes.
These tests pin the load-bearing invariants of the flip:

  * the ``DelegationEdge`` shape (statute_id / section / eid / delegation_type /
    match_text / quote) is produced from a canonical grant;
  * the ``section`` / ``eid`` edge KEYS come from the Akoma Ntoso markup (the
    scan-unit address), NOT the clause text, so they are stable across the flip;
  * the canonical issuer ``kind`` is used directly as ``delegation_type``;
  * a grant-SHAPED-but-not-a-grant instrument mention (a cross-reference) is NOT
    emitted as an edge (the canonical residue contract carries through).
"""
from __future__ import annotations

from lawvm.finland.delegation import DelegationEdge
from lawvm.finland.legal_surface.delegation_edge_adapter import (
    extract_delegations_canonical,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _statute(*provisions: str) -> bytes:
    body = "".join(provisions)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<akomaNtoso xmlns="{_AKN}"><act><body>{body}</body></act></akomaNtoso>'
    ).encode("utf-8")


def _subsec(sec_eid: str, num: str, ss_eid: str, text: str) -> str:
    return (
        f'<section eId="{sec_eid}"><num>{num} §</num>'
        f'<subsection eId="{ss_eid}"><content><p>{text}</p></content></subsection>'
        f"</section>"
    )


def test_canonical_sourced_edge_shape_and_keys() -> None:
    xml = _statute(
        _subsec(
            "sec_5",
            "5",
            "sec_5__subsec_2",
            "Valtioneuvoston asetuksella säädetään tarkemmin menettelystä.",
        )
    )
    edges = extract_delegations_canonical(xml, "2020/100")
    assert len(edges) == 1
    e = edges[0]
    assert isinstance(e, DelegationEdge)
    assert e.statute_id == "2020/100"
    # section + eid come from the markup (scan-unit address), stable across flip.
    assert e.section == "5"
    assert e.eid == "sec_5__subsec_2"
    # canonical kind used directly as delegation_type.
    assert e.delegation_type == "VN_ASETUS"
    # match_text is the canonical whole-frame surface; quote is the unit head.
    assert "asetuksella" in e.match_text
    assert e.quote.startswith("Valtioneuvoston asetuksella")


def test_agency_grant_typed_agency() -> None:
    xml = _statute(
        _subsec(
            "sec_3",
            "3",
            "sec_3__subsec_1",
            "Virasto voi antaa tarkempia määräyksiä tämän pykälän soveltamisesta.",
        )
    )
    edges = extract_delegations_canonical(xml, "2020/100")
    assert len(edges) == 1
    assert edges[0].delegation_type == "AGENCY"
    assert edges[0].section == "3"


def test_cross_reference_instrument_not_emitted_as_edge() -> None:
    # A cross-reference to an EXISTING decree's section is canonical residue, not a
    # grant — the adapter must emit NO edge for it (the residue contract carries).
    xml = _statute(
        _subsec(
            "sec_7",
            "7",
            "sec_7__subsec_1",
            "Asiasta säädetään valtioneuvoston asetuksen 34 §:n 2 momentissa.",
        )
    )
    edges = extract_delegations_canonical(xml, "2020/100")
    assert edges == []


def test_section_falls_back_when_no_subsection() -> None:
    # No subsection markup: the scan unit is the section, so the eid is the
    # section eid (same fallback the regex extractor uses).
    xml = _statute(
        '<section eId="sec_9"><num>9 §</num><content>'
        "<p>Tarkempia säännöksiä annetaan asetuksella.</p>"
        "</content></section>"
    )
    edges = extract_delegations_canonical(xml, "2020/100")
    assert len(edges) == 1
    assert edges[0].section == "9"
    assert edges[0].eid == "sec_9"


def test_parse_failure_returns_empty_and_records_diagnostic() -> None:
    diags: list = []
    edges = extract_delegations_canonical(b"<not-xml", "2020/100", diagnostics_out=diags)
    assert edges == []
    assert any(d.rule_id == "fi_delegation_extraction_xml_parse_failed" for d in diags)
