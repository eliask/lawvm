"""Whole-Chapter / whole-Part body-container insert eId derivation.

A whole-chapter insert target (``part:4/chapter:7A``) addresses a body
structural container, not a section. UK body containers carry hierarchical
eIds (``part-4``, ``part-4-chapter-7A``, ``chapter-7A``) while the sections
nested under them keep their own FLAT ``section-NNN`` eId. The target-eid
derivation must produce the container eId for the chapter/part itself and must
refuse to derive (return empty) when no structural label is available, so
placement is never invented.
"""

from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.target_anchors import _fallback_target_eid


def test_fallback_eid_for_chapter_container() -> None:
    addr = LegalAddress((("part", "4"), ("chapter", "7A")))
    assert _fallback_target_eid(addr) == "part-4-chapter-7a"


def test_fallback_eid_for_part_container() -> None:
    addr = LegalAddress((("part", "9"),))
    assert _fallback_target_eid(addr) == "part-9"


def test_fallback_eid_for_bare_chapter_container() -> None:
    addr = LegalAddress((("chapter", "11"),))
    assert _fallback_target_eid(addr) == "chapter-11"


def test_fallback_eid_for_section_is_flat_and_unchanged() -> None:
    # A section target keeps its flat eId and is NOT prefixed by any container.
    assert _fallback_target_eid(LegalAddress((("section", "289A"),))) == "section-289a"
    assert (
        _fallback_target_eid(LegalAddress((("section", "289A"), ("subsection", "1"))))
        == "section-289a-1"
    )


def test_fallback_eid_refuses_when_no_structural_label() -> None:
    # No section, part, or chapter label: derive nothing rather than guess.
    assert _fallback_target_eid(LegalAddress(())) == ""
    assert _fallback_target_eid(LegalAddress((("crossheading", ""),))) == ""
