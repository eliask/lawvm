"""Chapter-qualified CROSS-STATUTE references (N1) + surface joiner fidelity (N7).

A body / cross-statute citation can open with a chapter qualifier before the
section: ``poliisilain (872/2011) 9 luvun 9 b §`` (chapter 9, section 9b). The
body tail parser carries the chapter onto every expanded target so the plain-text
lane builds a chapter-qualified AKN path (``chp_9__sec_9b``) — the SAME modeling
the internal lane uses — instead of silently dropping the chapter. A chapter with
no following section (``5 luvussa``) yields a chapter-only target so it is never
dropped.

N7: the recorded surface preserves the author's original disjunctive ``tai``
joiner (the body parse rewrites it to ``ja`` only to enumerate the list).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from lawvm.finland.references.sections import (
    chapter_akn_path,
    parse_body_provision_tail,
    parse_body_provision_tail_spanned,
)
from lawvm.finland.references.ref_mention_extractor import (
    _PLAIN_TEXT_RECOGNIZER,
    extract_plain_text_statute_mentions,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _chsec(text: str) -> list[tuple[str | None, str]]:
    return [(t.chapter, t.section_label) for t in parse_body_provision_tail(text)]


# ── N1: parser carries the chapter prefix ───────────────────────────────────


def test_chapter_qualified_section() -> None:
    assert _chsec("9 luvun 9 b §:n nojalla") == [("9", "9b")]


def test_chapter_qualified_section_inessive() -> None:
    assert _chsec("13 luvun 3 §:ssä") == [("13", "3")]


def test_chapter_only_no_section() -> None:
    # Chapter with no § → one chapter-only target (section deferred).
    assert _chsec("5 luvussa tai") == [("5", "")]


def test_chapter_coordination_cross_product() -> None:
    assert _chsec("3 ja 4 luvun 5 §") == [("3", "5"), ("4", "5")]


def test_chapter_qualified_section_list() -> None:
    assert _chsec("38 luvun 1 tai 2 §") == [("38", "1"), ("38", "2")]


def test_no_chapter_unchanged() -> None:
    # A bare section run with no chapter prefix is unaffected (chapter=None).
    assert _chsec("9 b §:n nojalla") == [(None, "9b")]


def test_chapter_akn_path_shape() -> None:
    assert chapter_akn_path("9", "9b") == "chp_9__sec_9b"
    assert chapter_akn_path("5") == "chp_5"


# ── N1: plain-text lane builds the chapter-qualified target path ─────────────


def _p(text: str) -> ET.Element[str]:
    return ET.fromstring(f'<p xmlns="{_AKN}">{text}</p>')


def test_plain_text_chapter_qualified_target_path() -> None:
    # poliisilain (872/2011) 9 luvun 9 b § → target id is the canonical
    # corpus-key orientation YEAR/NUMBER (2011/872), not the visible 872/2011.
    p = _p("poliisilain (872/2011) 9 luvun 9 b §:n nojalla käynnistetyn")
    [hit] = [h for h in _PLAIN_TEXT_RECOGNIZER.scan_precise(p) if h.statute_id == "2011/872"]
    assert hit.chapter == "9"
    assert hit.section_label == "9b"


def test_plain_text_chapter_qualified_mention_path() -> None:
    xml = (
        f'<akomaNtoso xmlns="{_AKN}"><act><body><section><num>1 §</num>'
        "<paragraph><content><p>poliisilain (872/2011) 9 luvun 9 b §:n nojalla "
        "käynnistetyn yhteistyön johdosta</p></content></paragraph>"
        "</section></body></act></akomaNtoso>"
    ).encode("utf-8")
    res = extract_plain_text_statute_mentions(xml, "999/2020")
    paths = {
        m.target_provision_ref.provision_path
        for m in res.mentions
        if m.target_provision_ref and m.target_provision_ref.statute_id == "2011/872"
    }
    assert paths == {"chp_9__sec_9b"}


def test_plain_text_chapter_only_is_statute_only() -> None:
    xml = (
        f'<akomaNtoso xmlns="{_AKN}"><act><body><section><num>1 §</num>'
        "<paragraph><content><p>noudatetaan elintarvikelain (297/2021) 5 luvussa "
        "tarkoitettua menettelyä</p></content></paragraph>"
        "</section></body></act></akomaNtoso>"
    ).encode("utf-8")
    res = extract_plain_text_statute_mentions(xml, "999/2020")
    chapter_only = [
        m
        for m in res.mentions
        if m.target_provision_ref
        and m.target_provision_ref.statute_id == "2021/297"
    ]
    assert len(chapter_only) == 1
    m = chapter_only[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.provision_path == "chp_5"
    assert m.target_provision_ref.section_label == ""
    # Chapter known, section deferred → STATUTE_ONLY, never widened.
    assert m.cite_confidence.value == "statute_only"


# ── N7: recorded surface preserves the author's ``tai`` joiner ───────────────


def test_consumed_surface_preserves_tai_joiner() -> None:
    sp = parse_body_provision_tail_spanned("1 tai 2 §:n nojalla")
    assert sp.consumed_text == "1 tai 2 §:n"
    # Targets still enumerate both members (parse uses the ``ja`` semantics).
    assert [t.section_label for t in sp.targets] == ["1", "2"]


def test_consumed_surface_preserves_tai_with_chapter() -> None:
    sp = parse_body_provision_tail_spanned("38 luvun 1 tai 2 §:ssä")
    assert sp.consumed_text == "38 luvun 1 tai 2 §:ssä"
    assert [(t.chapter, t.section_label) for t in sp.targets] == [
        ("38", "1"),
        ("38", "2"),
    ]


def test_consumed_surface_preserves_multiple_tai() -> None:
    sp = parse_body_provision_tail_spanned("114, 115 tai 155 §:n säädetään")
    assert sp.consumed_text == "114, 115 tai 155 §:n"
    assert [t.section_label for t in sp.targets] == ["114", "115", "155"]


# ── Multi-chapter enumeration under one statute head (gap [5]) ───────────────
#
# A cross-statute tail under ONE statute head can span MULTIPLE chapter clauses
# (``rikoslain 17 luvun 18 §:ssä, 20 luvussa, 21 luvun 1—3 §:ssä``). Every chapter
# clause must enumerate its own chapter-qualified members; previously only the
# first chapter clause was captured and the rest silently dropped.


def test_multi_chapter_enumeration_spans_all_clauses() -> None:
    sp = parse_body_provision_tail_spanned(
        "17 luvun 18, 18 a tai 19 §:ssä, 20 luvussa, "
        "21 luvun 1—3 tai 6 §:ssä, 31 luvun 1—4 §:ssä, 50 luvun 1—4 §:ssä"
    )
    pairs = [(t.chapter, t.section_label) for t in sp.targets]
    assert pairs == [
        ("17", "18"),
        ("17", "18a"),
        ("17", "19"),
        ("20", ""),  # chapter-only clause, section deferred
        ("21", "1"),
        ("21", "2"),
        ("21", "3"),
        ("21", "6"),
        ("31", "1"),
        ("31", "2"),
        ("31", "3"),
        ("31", "4"),
        ("50", "1"),
        ("50", "2"),
        ("50", "3"),
        ("50", "4"),
    ]
    # The consumed surface spans the whole multi-chapter run (author ``tai`` kept).
    assert sp.consumed_text.endswith("50 luvun 1—4 §:ssä")
    assert "20 luvussa" in sp.consumed_text


def test_multi_chapter_two_clauses_minimal() -> None:
    sp = parse_body_provision_tail_spanned("3 luvun 1 §:ssä ja 5 luvun 2 §:ssä")
    assert [(t.chapter, t.section_label) for t in sp.targets] == [
        ("3", "1"),
        ("5", "2"),
    ]


def test_single_chapter_clause_unchanged_by_multichapter_path() -> None:
    """A coordinated section run with no second chapter prefix stays one clause."""
    sp = parse_body_provision_tail_spanned("9 luvun 9 a ja 9 b §:ssä")
    assert [(t.chapter, t.section_label) for t in sp.targets] == [
        ("9", "9a"),
        ("9", "9b"),
    ]


def test_multi_chapter_end_to_end_by_name() -> None:
    """The by-name lane lifts every chapter clause to a chapter-qualified ref."""
    from lawvm.finland.references.by_name import recognize_by_name_refs

    text = (
        "rikoslain 17 luvun 18, 18 a tai 19 §:ssä, 20 luvussa, "
        "21 luvun 1—3 tai 6 §:ssä"
    )
    paths = [
        m.target_provision_ref.provision_path
        for m in recognize_by_name_refs(text)
        if m.target_provision_ref
    ]
    assert "chp_20" in paths
    assert "chp_21__sec_6" in paths
    assert "chp_17__sec_18a" in paths
