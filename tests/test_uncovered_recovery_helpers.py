"""Isolation tests for pure helpers extracted from uncovered-body recovery.

These cover the stateless label/heading/part helpers lifted out of the
``_recover_uncovered_body_ops`` closure cascade so they can be tested without
constructing a full ReplayState.
"""
from __future__ import annotations

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.grafter_uncovered import (
    _next_letter_label,
    _part_label_from_path,
    _section_heading_text,
    _xml_part_label,
)


def _section_with_heading(text: str) -> IRNode:
    heading = IRNode(kind=IRNodeKind.HEADING, text=text)
    return IRNode(kind=IRNodeKind.SECTION, label="1", children=(heading,))


def test_section_heading_text_normalizes_and_lowercases() -> None:
    node = _section_with_heading("  Voimaantulo   Säännös ")
    assert _section_heading_text(node) == "voimaantulo säännös"


def test_section_heading_text_empty_when_no_heading() -> None:
    node = IRNode(kind=IRNodeKind.SECTION, label="1", children=())
    assert _section_heading_text(node) == ""


def test_next_letter_label_bare_number_gets_a() -> None:
    assert _next_letter_label("18") == "18a"


def test_next_letter_label_advances_suffix() -> None:
    assert _next_letter_label("18a") == "18b"


def test_next_letter_label_stops_at_z() -> None:
    assert _next_letter_label("18z") is None


def test_next_letter_label_rejects_non_numeric() -> None:
    assert _next_letter_label("foo") is None


def test_xml_part_label_walks_to_part_ancestor() -> None:
    root = etree.fromstring(
        b"<part><num>II OSA</num><chapter><num>3 luku</num>"
        b"<section><num>5 \xc2\xa7</num></section></chapter></part>"
    )
    sec = root.find(".//section")
    assert sec is not None
    # Normalized part label (roman/normalized); just assert it is non-None and stable.
    assert _xml_part_label(sec) is not None


def test_xml_part_label_none_without_part_ancestor() -> None:
    root = etree.fromstring(b"<chapter><num>3 luku</num><section><num>5</num></section></chapter>")
    sec = root.find(".//section")
    assert sec is not None
    assert _xml_part_label(sec) is None


def test_part_label_from_path_finds_part() -> None:
    path = (("part", "2"), ("chapter", "3"), ("section", "5"))
    assert _part_label_from_path(path) == "2"


def test_part_label_from_path_none_when_absent() -> None:
    assert _part_label_from_path((("chapter", "3"), ("section", "5"))) is None
    assert _part_label_from_path(None) is None
