"""Body-mode sub-reference recognition for the cross-statute reference lane.

The shared sub-reference recognizer (``grammar.subref``) is mode-parameterized:
``mode="amendment"`` (default) keeps the inessive ``momentissa`` a WORD (a pinned
hard constraint — amendment johtolauses embed ``N §:n M momentissa tarkoitettu``
relative clauses inside statute names); ``mode="body"`` promotes the body-only
inessive MOMENTTI forms so a body citation gets full momentti / range /
coordination precision.

These tests pin the body-mode behavior at the recognizer level and assert the
amendment default is untouched.
"""

from __future__ import annotations

# Importing grammar.sections first establishes the module import order the
# subref/sections circular relocation requires.
import lawvm.finland.johtolause.grammar.sections  # noqa: F401
from lawvm.core.reference_mention import ProvisionRef
from lawvm.finland.johtolause.grammar.subref import SubRef, recognize_sub_refs
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.references.sections import (
    BodyProvisionTarget,
    parse_body_provision_tail,
)


def _after_pykala(text: str) -> int:
    toks = tokenize(text)
    return next(i for i, t in enumerate(toks) if t.cat == "PYKALA") + 1


# ── recognize_sub_refs, body mode ──────────────────────────────────────────


def test_body_single_momentti() -> None:
    toks = tokenize("1 momentissa")
    subs, _end = recognize_sub_refs(toks, 0, mode="body")
    assert subs == [SubRef(momentti=1)]


def test_body_momentti_coordination() -> None:
    toks = tokenize("1 ja 2 momentissa")
    subs, _end = recognize_sub_refs(toks, 0, mode="body")
    assert subs == [SubRef(momentti=1), SubRef(momentti=2)]


def test_body_section_genitive_momentti() -> None:
    text = "104 §:n 2 momentissa"
    toks = tokenize(text)
    subs, _end = recognize_sub_refs(toks, _after_pykala(text), mode="body")
    assert subs == [SubRef(momentti=2)]


def test_body_momentti_with_period() -> None:
    # A clause-final momentti carries a glued period the amendment lexer leaves
    # out-of-vocab; body mode strips it for recognition.
    text = "7 §:n 2 momentissa."
    toks = tokenize(text)
    subs, _end = recognize_sub_refs(toks, _after_pykala(text), mode="body")
    assert subs == [SubRef(momentti=2)]


# ── amendment mode (default) is provably untouched ─────────────────────────


def test_amendment_mode_keeps_momentissa_lost() -> None:
    # In amendment mode the inessive ``momentissa`` is NOT promoted; the bare
    # WORD blocks the momentti recovery exactly as before (pinned by
    # test_fi_grammar_inessive_subref).
    text = "6 §:n 1 momentissa"
    toks = tokenize(text)
    subs, _end = recognize_sub_refs(toks, _after_pykala(text), mode="amendment")
    assert subs == []


def test_amendment_default_is_amendment_mode() -> None:
    text = "6 §:n 1 momentissa"
    toks = tokenize(text)
    assert recognize_sub_refs(toks, _after_pykala(text)) == (
        recognize_sub_refs(toks, _after_pykala(text), mode="amendment")
    )


def test_amendment_genitive_kohta_unchanged_in_body_mode() -> None:
    # A genitive amendment-style sub-ref still parses identically in body mode
    # (body mode only ADDS the inessive momentti promotion).
    text = "2 §:n 1 momentin 4 kohdan"
    toks = tokenize(text)
    start = _after_pykala(text)
    assert (
        recognize_sub_refs(toks, start, mode="body")[0]
        == recognize_sub_refs(toks, start, mode="amendment")[0]
    )


# ── SubRef.to_provision_ref ────────────────────────────────────────────────


def test_to_provision_ref_momentti_and_item() -> None:
    ref = SubRef(momentti=2, item="3").to_provision_ref("711/2022", "7")
    assert ref == ProvisionRef(
        statute_id="711/2022",
        provision_path="",
        section_label="7",
        subsection_num=2,
        item_label="3",
    )


def test_to_provision_ref_whole_section() -> None:
    # momentti=0 means no subsection named → subsection_num is None, not 0.
    ref = SubRef().to_provision_ref("424/2003", "6")
    assert ref.subsection_num is None
    assert ref.item_label is None
    assert ref.section_label == "6"


# ── parse_body_provision_tail: ranges, coordination, momentti precision ────


def test_tail_section_range() -> None:
    targets = parse_body_provision_tail("108—110 §:ää ei kuitenkaan sovelleta")
    assert targets == [
        BodyProvisionTarget(section_label="108"),
        BodyProvisionTarget(section_label="109"),
        BodyProvisionTarget(section_label="110"),
    ]


def test_tail_section_coordination() -> None:
    assert parse_body_provision_tail("6 ja 8 §") == [
        BodyProvisionTarget(section_label="6"),
        BodyProvisionTarget(section_label="8"),
    ]


def test_tail_section_momentti() -> None:
    assert parse_body_provision_tail("6 §:n 1 momentissa") == [
        BodyProvisionTarget(section_label="6", subsection_num=1)
    ]


def test_tail_momentti_kohta() -> None:
    assert parse_body_provision_tail("7 §:n 2 momentin 3 kohdan mukaan") == [
        BodyProvisionTarget(section_label="7", subsection_num=2, item_label="3")
    ]


def test_tail_no_section_is_empty() -> None:
    assert parse_body_provision_tail("ei lainkaan pykälää tässä") == []
