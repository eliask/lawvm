"""Tests for the Finland SourceSurfaceBundle builder (Pro r5 Phase 1)."""
from __future__ import annotations

from lawvm.finland.legal_surface.bundle import (
    build_surface_bundle,
    decode_body_text,
    locate_span,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Tata lakia sovelletaan 5 §:ssa tarkoitettuun toimintaan.</p>
          <p>Lisaksi 5 §:ssa saadetaan poikkeuksesta.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")


def test_decode_body_text_joins_paragraphs() -> None:
    text = decode_body_text(_XML)
    assert "Tata lakia sovelletaan" in text
    assert "Lisaksi 5" in text
    # newline-joined paragraphs => two lines from the two <p> elements
    assert text.count("\n") >= 1


def test_decode_body_text_parse_error_is_empty() -> None:
    assert decode_body_text(b"<not xml") == ""
    assert decode_body_text(b"") == ""


def test_build_surface_bundle_shape() -> None:
    bundle = build_surface_bundle(_XML, "123/2020", surface_time="2020-06-01")
    assert bundle.jurisdiction == "fi"
    assert bundle.subject.work_id == "123/2020"
    assert bundle.subject.surface_time == "2020-06-01"
    assert bundle.subject.scope == {"kind": "whole_work"}
    assert len(bundle.units) == 1
    unit = bundle.units[0]
    assert unit.source_unit_id == "123/2020#body"
    assert unit.work_id == "123/2020"
    assert unit.metadata["xml_bytes"] == _XML
    # the body source_ref spans the whole raw_text
    assert unit.source_ref.char_start == 0
    assert unit.source_ref.char_end == len(unit.raw_text)


def test_build_surface_bundle_hash_is_content_addressed() -> None:
    b1 = build_surface_bundle(_XML, "123/2020")
    b2 = build_surface_bundle(_XML, "123/2020")
    assert b1.subject.source_bundle_hash == b2.subject.source_bundle_hash
    assert b1.units[0].source_hash == b2.units[0].source_hash


def test_locate_span_repeated_surface_advances_cursor() -> None:
    bundle = build_surface_bundle(_XML, "123/2020")
    unit = bundle.units[0]
    # "5 §:ssa" appears twice; the cursor must map them to distinct offsets.
    ref1, cur1 = locate_span(unit, "5 §:ssa")
    assert ref1 is not None
    ref2, cur2 = locate_span(unit, "5 §:ssa", cursor=cur1)
    assert ref2 is not None
    assert ref2.char_start > ref1.char_start
    # each ref slices back to the exact surface
    assert unit.raw_text[ref1.char_start : ref1.char_end] == "5 §:ssa"
    assert unit.raw_text[ref2.char_start : ref2.char_end] == "5 §:ssa"


def test_locate_span_absent_surface_is_none() -> None:
    bundle = build_surface_bundle(_XML, "123/2020")
    unit = bundle.units[0]
    ref, cur = locate_span(unit, "this text is not present", cursor=0)
    assert ref is None
    assert cur == 0
