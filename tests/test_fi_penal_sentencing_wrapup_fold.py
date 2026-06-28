"""Regression tests for the penal sentencing-clause wrapUp fold.

Covers ``_fold_penal_sentencing_wrapup_subsection`` in ``source_normalize.py``.

Finnish rangaistussäännös provisions are drafted as one momentti::

    Joka ... [1) ... 7)] on tuomittava ... sakkoon.

Some Finlex section payloads encode the offence frame (offender formula plus
the numbered kohta list) and its sentencing clause ("on tuomittava ...
sakkoon/vankeuteen") as two separate ``<subsection>`` siblings.  The sentencing
clause cannot stand as an independent momentti, so it must be folded back as the
offence frame's loppukappale wrapUp and the following momentit renumbered.

These tests construct IR trees directly via ``IRNode(...)`` to exercise the
section-level fold deterministically.

Both directions are covered:
- under-absorption: a stray penal sentencing subsection IS folded
  (Joka-prefix and embedded named-subject culpability forms);
- over-absorption guard: a genuinely new normative momentti subsection is
  NOT folded.
"""

from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.tree_ops import check_invariants
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.source_normalization_kinds import (
    BASE_PENAL_SENTENCING_WRAPUP_FOLD,
)
from lawvm.finland.source_normalize import normalize_source_ir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _num(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.NUM, text=text)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


def _numbered_paragraph(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(_num(f"{label})"), _content(text)),
    )


def _offence_frame(label: str, intro_text: str, kohdat: list[IRNode]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=label,
        children=(_intro(intro_text), *kohdat),
    )


def _content_subsection(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=label,
        children=(_content(text),),
    )


def _section(label: str, subsections: list[IRNode]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(_num(label), *subsections),
    )


def _body_with_section(section: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=(section,))


def _find_section(node: IRNode, label: str) -> IRNode | None:
    if node.kind == IRNodeKind.SECTION and node.label == label:
        return node
    for c in node.children:
        r = _find_section(c, label)
        if r:
            return r
    return None


# ---------------------------------------------------------------------------
# Under-absorption: stray penal sentencing subsection IS folded (Joka-prefix)
# ---------------------------------------------------------------------------


def test_joka_offence_frame_sentencing_clause_folded() -> None:
    """A Joka-prefixed offence frame absorbs its stray sentencing subsection.

    Mirrors 2023/1270 §97: penal frame (subsec 1) + content-only sentencing
    clause (subsec 2) + two genuine following momentit (subsec 3, 4).  The
    sentencing clause folds as wrapUp on momentti 1; subsec 3 -> 2, subsec 4 -> 3.
    """
    sec = _section("97", [
        _offence_frame("1", "Joka tahallaan tai törkeästä huolimattomuudesta", [
            _numbered_paragraph("1", "laiminlyö hakea päästölupaa,"),
            _numbered_paragraph("2", "laiminlyö ilmoituksen tekemisen,"),
        ]),
        _content_subsection(
            "2",
            "on tuomittava, jollei teosta muualla laissa säädetä ankarampaa "
            "rangaistusta, päästökaupparikkomuksesta sakkoon.",
        ),
        _content_subsection("3", "MRV-asetuksen seuraamuksista säädetään muualla."),
        _content_subsection("4", "Energiaviraston tulee tehdä ilmoitus esitutkintaa varten."),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "2023/1270-fixture")

    sec = _find_section(base_ir, "97")
    assert sec is not None

    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    fold_facts = [f for f in facts if f.kind_value == BASE_PENAL_SENTENCING_WRAPUP_FOLD]
    assert len(fold_facts) == 1, f"Expected 1 fold fact, got {len(fold_facts)}"

    subsections = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    # Four source subsections collapse to three momentit.
    assert [s.label for s in subsections] == ["1", "2", "3"], (
        f"Expected relabelled momentit [1,2,3], got {[s.label for s in subsections]}"
    )

    # Momentti 1 now carries the sentencing clause as a trailing wrapUp.
    mom1 = subsections[0]
    wrapups = [c for c in mom1.children if c.kind == IRNodeKind.WRAP_UP]
    assert len(wrapups) == 1, f"Expected 1 wrapUp on momentti 1, got {len(wrapups)}"
    assert "on tuomittava" in (wrapups[0].text or "")
    assert "sakkoon" in (wrapups[0].text or "")

    # The two genuine following momentit are preserved (renumbered down by one).
    assert "MRV-asetuksen" in "".join(
        gc.text or "" for gc in subsections[1].children
    )
    assert "Energiaviraston" in "".join(
        gc.text or "" for gc in subsections[2].children
    )


# ---------------------------------------------------------------------------
# Under-absorption: named-subject culpability frame + interjected sentencing
# ---------------------------------------------------------------------------


def test_named_subject_culpability_frame_folded() -> None:
    """A named-subject offence frame (", joka tahallaan ...") absorbs its clause.

    Mirrors 2016/549 §114: the offender is a named subject qualified by
    ", joka tahallaan tai törkeästä huolimattomuudesta", and the sentencing
    clause interjects a condition between "on" and "tuomittava".
    """
    sec = _section("114", [
        _offence_frame(
            "1",
            "Yleisen kulkuneuvon haltija tai hänen edustajansa, joka tahallaan "
            "tai törkeästä huolimattomuudesta",
            [
                _numbered_paragraph("1", "sallii tupakoinnin sisätilassa,"),
                _numbered_paragraph("2", "sallii työskentelyn tupakointitilassa,"),
            ],
        ),
        _content_subsection(
            "2",
            "on, jollei laiminlyöntiä voida pitää vähäisenä ja jollei teosta "
            "muualla laissa säädetä ankarampaa rangaistusta, tuomittava "
            "tupakansavulta suojaavien toimenpiteiden laiminlyönnistä sakkoon.",
        ),
        _content_subsection("3", "Rangaistus terveysrikoksesta säädetään rikoslaissa."),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "2016/549-fixture")

    sec = _find_section(base_ir, "114")
    assert sec is not None
    assert not check_invariants(sec)

    fold_facts = [f for f in facts if f.kind_value == BASE_PENAL_SENTENCING_WRAPUP_FOLD]
    assert len(fold_facts) == 1, f"Expected 1 fold fact, got {len(fold_facts)}"

    subsections = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    assert [s.label for s in subsections] == ["1", "2"], (
        f"Expected [1,2] after fold, got {[s.label for s in subsections]}"
    )
    mom1 = subsections[0]
    wrapups = [c for c in mom1.children if c.kind == IRNodeKind.WRAP_UP]
    assert len(wrapups) == 1
    assert "tuomittava" in (wrapups[0].text or "")


# ---------------------------------------------------------------------------
# Over-absorption guard: a genuine new momentti subsection is NOT folded
# ---------------------------------------------------------------------------


def test_new_normative_momentti_not_folded() -> None:
    """A following subsection that begins a new normative rule is NOT folded.

    The offence frame is penal, but the following content-only subsection does
    not carry the sentencing predicate — it is a genuine separate momentti.
    Folding it would swallow a real legal unit.
    """
    sec = _section("9", [
        _offence_frame("1", "Joka tahallaan tai törkeästä huolimattomuudesta", [
            _numbered_paragraph("1", "rikkoo velvoitetta,"),
            _numbered_paragraph("2", "laiminlyö ilmoituksen,"),
        ]),
        # No sentencing predicate — a genuinely new momentti, must stay a peer.
        _content_subsection(
            "2",
            "Laiminlyöntimaksun suuruus on 300 euroa. Julkista osakeyhtiötä "
            "koskeva laiminlyöntimaksu on 600 euroa.",
        ),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "guard-fixture")

    sec = _find_section(base_ir, "9")
    assert sec is not None

    fold_facts = [f for f in facts if f.kind_value == BASE_PENAL_SENTENCING_WRAPUP_FOLD]
    assert not fold_facts, (
        f"Genuine momentti must not be folded, got {len(fold_facts)} fold facts"
    )

    subsections = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    assert [s.label for s in subsections] == ["1", "2"], (
        f"Both momentit must be preserved, got {[s.label for s in subsections]}"
    )
    # Momentti 1 must NOT have acquired a wrapUp.
    mom1 = subsections[0]
    assert not [c for c in mom1.children if c.kind == IRNodeKind.WRAP_UP]


def test_non_penal_frame_with_sentencing_like_tail_not_folded() -> None:
    """A non-penal list frame is not folded even if a tail mentions a penalty.

    Without the offence-formula / culpability signal on the frame, the penal
    fold must not fire.
    """
    sec = _section("5", [
        _offence_frame("1", "Hakemuksessa on ilmoitettava", [
            _numbered_paragraph("1", "hakijan nimi,"),
            _numbered_paragraph("2", "yhteystiedot,"),
        ]),
        _content_subsection(
            "2",
            "on tuomittava sakkoon se, joka antaa vääriä tietoja.",
        ),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "nonpenal-fixture")

    fold_facts = [f for f in facts if f.kind_value == BASE_PENAL_SENTENCING_WRAPUP_FOLD]
    assert not fold_facts, (
        f"Non-penal frame must not trigger penal fold, got {len(fold_facts)}"
    )
