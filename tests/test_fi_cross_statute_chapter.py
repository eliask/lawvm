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
    # poliisilain (872/2011) 9 luvun 9 b § → 872/2011/chp_9__sec_9b.
    p = _p("poliisilain (872/2011) 9 luvun 9 b §:n nojalla käynnistetyn")
    [hit] = [h for h in _PLAIN_TEXT_RECOGNIZER.scan_precise(p) if h.statute_id == "872/2011"]
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
        if m.target_provision_ref and m.target_provision_ref.statute_id == "872/2011"
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
        and m.target_provision_ref.statute_id == "297/2021"
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
