"""Regression: internal section-ref byte re-anchoring must respect a left number
boundary so a short section number is not located inside a longer one.

Root cause (observed on Ulkomaalaislaki 2004/301, voimaantulosäännös §215):
``extract_surface_grammar_mentions`` re-anchors each recognized internal
reference to a byte span in the raw XML by a plain ``xml_bytes.find(surface)``.
The surface ``"56 §:ssä"`` is a digit-suffix of an EARLIER ``"156 §:ssä"`` in the
same document, so the naive find returns the embedded occurrence (inside
``156``) instead of the real ``Edellä 56 §:ssä``. The reference is detected, but
its span points at the wrong section, so the viewer renders no link there.

The fix is a left-boundary check in the byte relocation: a surface whose first
character is a digit must not match at a position whose preceding byte is also a
digit (it would be embedded in a longer section number).
"""
from __future__ import annotations

from lawvm.finland.references.ref_mention_extractor import (
    extract_surface_grammar_mentions,
)


# Synthetic statute reproducing the 3-mom / 4-mom collision shape:
#   - an EARLIER paragraph cites "155 ja 156 §:ssä"  (contains substring "56 §:ssä")
#   - a LATER transitional paragraph cites "Edellä 56 §:ssä"
# Both are the same INTERNAL bare-section shape; only the later one is the true
# "56 §:ssä" reference.
_XML = (
    '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    "<body>"
    '<section eId="sec_200"><subsection eId="sec_200__subsec_1"><content>'
    "<p>Unionin kansalainen täyttää 155 ja 156 §:ssä "
    "säädetyt edellytykset.</p>"
    "</content></subsection></section>"
    '<section eId="sec_215"><subsection eId="sec_215__subsec_3"><content>'
    "<p>Edellä 13 §:ssä säädettyä edellytystä "
    "lapsen valokuvasta ulkomaalaisen passissa sovelletaan vain lain "
    "voimaantulon jälkeen myönnettyihin passeihin.</p>"
    "</content></subsection>"
    '<subsection eId="sec_215__subsec_4"><content>'
    "<p>Edellä 56 §:ssä säädettyä pysyvän "
    "oleskeluluvan myöntämisen edellytystä 4 vuoden asumisajasta "
    "sovelletaan tämän lain voimaantulon jälkeen.</p>"
    "</content></subsection></section>"
    "</body></akomaNtoso>"
)


def _internal_mentions_to_section(xml_bytes: bytes, section_label: str):
    res = extract_surface_grammar_mentions(xml_bytes, "TEST/1")
    return [
        m
        for m in res.mentions
        if "INTERNAL" in str(m.cite_kind)
        and m.target_provision_ref is not None
        and m.target_provision_ref.section_label == section_label
    ]


def test_both_internal_section_refs_are_detected() -> None:
    xb = _XML.encode("utf-8")
    assert _internal_mentions_to_section(xb, "13"), "13 §:ssä not detected"
    assert _internal_mentions_to_section(xb, "56"), "56 §:ssä not detected"


def test_short_section_ref_anchors_at_its_own_occurrence_not_inside_longer() -> None:
    xb = _XML.encode("utf-8")

    # The single true "Edellä 56 §:ssä" occurrence in the bytes.
    true_56 = xb.find("Edellä 56 §:ssä".encode("utf-8"))
    assert true_56 >= 0
    true_56_surface = xb.find("56 §:ssä".encode("utf-8"), true_56)

    # The spurious embedded occurrence inside "156 §:ssä" (earlier in the doc).
    embedded_56 = xb.find("56 §:ssä".encode("utf-8"))
    assert 0 <= embedded_56 < true_56_surface
    # Sanity: the embedded one really is preceded by a digit (the "1" of 156).
    assert chr(xb[embedded_56 - 1]).isdigit()

    mentions = _internal_mentions_to_section(xb, "56")
    spans = [m.source_span for m in mentions if m.source_span is not None]
    assert spans, "56 §:ssä internal mention has no anchored span"

    # The re-anchored span must point at the REAL Edellä 56 §:ssä, never at the
    # "56" embedded inside the earlier "156 §:ssä".
    for sp in spans:
        assert sp.byte_offset != embedded_56, (
            "56 §:ssä mis-anchored to the embedded occurrence inside 156 §:ssä"
        )
        assert chr(xb[sp.byte_offset - 1]).isdigit() is False, (
            "56 §:ssä anchored at a position preceded by a digit (embedded in a "
            "longer section number)"
        )
        assert sp.byte_offset == true_56_surface


# A coordinated internal reference enumerates into one mention PER member, all
# carrying the same whole-coordination surface. The integration must share ONE
# span across the members of a single occurrence (not advance its per-surface
# byte cursor onto a LATER occurrence, which mis-anchored member 2+ and starved
# repeated occurrences of a span).
_COORD_XML = (
    '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    "<body>"
    '<section eId="sec_1"><content>'
    "<p>Sovelletaan 47 ja 49 §:ssä säädettyä.</p>"
    "</content></section>"
    '<section eId="sec_2"><content>'
    "<p>Lisäksi 47 ja 49 §:ssä mainittu.</p>"
    "</content></section>"
    "</body></akomaNtoso>"
)


def _coord_section_mentions(xml_bytes: bytes):
    res = extract_surface_grammar_mentions(xml_bytes, "TEST/COORD")
    return [
        m
        for m in res.mentions
        if "INTERNAL" in str(m.cite_kind)
        and m.surface_text == "47 ja 49 §:ssä"
    ]


def test_coordinated_members_share_one_occurrence_span() -> None:
    xb = _COORD_XML.encode("utf-8")
    needle = "47 ja 49 §:ssä".encode("utf-8")
    occ1 = xb.find(needle)
    occ2 = xb.find(needle, occ1 + 1)
    assert 0 <= occ1 < occ2

    mentions = _coord_section_mentions(xb)
    # 2 members (47, 49) × 2 occurrences = 4 mentions; NONE may lose its span.
    assert len(mentions) == 4
    spans = [m.source_span for m in mentions]
    assert all(sp is not None for sp in spans), (
        "a coordinated member lost its span (per-surface cursor walked onto a "
        "later occurrence instead of sharing the occurrence span)"
    )
    offsets = [sp.byte_offset for sp in spans]
    # Both members of each occurrence share that occurrence's span; the two
    # occurrences anchor at their own (distinct, document-order) offsets.
    assert sorted(offsets) == [occ1, occ1, occ2, occ2]


def test_coordinated_reference_not_double_emitted() -> None:
    # The coordinated surface appears ONCE per occurrence in the source; it must
    # not be emitted by more than the enumerated members. Per occurrence: exactly
    # one mention for section 47 and one for section 49 (no duplicate pair).
    xb = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<body><section eId=\"s\"><content>"
        "<p>Sovelletaan 47 ja 49 §:ssä säädettyä.</p>"
        "</content></section></body></akomaNtoso>"
    ).encode("utf-8")
    res = extract_surface_grammar_mentions(xb, "TEST/COORD2")
    labels = sorted(
        m.target_provision_ref.section_label
        for m in res.mentions
        if "INTERNAL" in str(m.cite_kind)
        and m.target_provision_ref is not None
        and m.target_provision_ref.section_label in {"47", "49"}
    )
    assert labels == ["47", "49"], f"double-emission or missing member: {labels}"
