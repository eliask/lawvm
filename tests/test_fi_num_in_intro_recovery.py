"""Tests for Step 8.7: num_in_intro parse-phase num recovery.

Covers the ``_recover_num_in_intro_peers`` pass in ``source_normalize.py``.

IMPORTANT: tests construct IR trees directly via ``IRNode(...)`` to exercise
the normalization pass deterministically.  The XML parse path may already
strip or transform num_in_intro shapes upstream; direct IR construction
bypasses that and targets the normalization pass itself.

Fixtures:
1. Basic recovery — kohta 1, unnumbered "2) ...", kohta 3 → recovered to kohta 2.
2. Sequence mismatch — kohta 1, unnumbered "5) ...", kohta 2 → MISMATCH, no recovery.
3. Leading citation false positive — unnumbered "( 999 ) viittaus..." → no match.
4. Letter sequence — subitems a, b, unnumbered "c) ...", d → recovered to c.
5. T4a/T4b regression guards:
   - sub_clause_with_list fixture → UNNUMBERED_PEER_REPARENT, not RECOVERED.
   - tail_prose fixture → BASE_TAIL_PROSE_ABSORB, not RECOVERED.
"""

from __future__ import annotations

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.tree_ops import check_invariants
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.source_normalization_kinds import (
    BASE_NUM_IN_INTRO_MISMATCH,
    BASE_NUM_IN_INTRO_RECOVERED,
    BASE_TAIL_PROSE_ABSORB,
    UNNUMBERED_PEER_REPARENT,
)
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.source_normalize import normalize_source_ir


# ---------------------------------------------------------------------------
# Helpers (mirrors test_fi_tail_prose_absorb.py helpers)
# ---------------------------------------------------------------------------

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _build_body(inner_xml: str) -> etree._Element:
    return etree.fromstring(f'<body xmlns="{AKN_NS}">{inner_xml}</body>')


def _num(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.NUM, text=text)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


def _subparagraph(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBPARAGRAPH,
        label=label,
        children=(_num(f"{label})"), _content(text)),
    )


def _numbered_paragraph(label: str, text: str) -> IRNode:
    """Numbered PARAGRAPH with NUM and CONTENT children."""
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(_num(f"{label})"), _content(text)),
    )


def _unnumbered_paragraph(text: str) -> IRNode:
    """Unnumbered PARAGRAPH with only CONTENT child (tail-prose or num_in_intro)."""
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=None,
        children=(_content(text),),
    )


def _unnumbered_paragraph_with_subparas(intro: str, subparas: list[IRNode]) -> IRNode:
    """Unnumbered PARAGRAPH with INTRO and SUBPARAGRAPH children (sub_clause_with_list)."""
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=None,
        children=(_intro(intro), *subparas),
    )


def _subsection(label: str, intro_text: str, children: list[IRNode]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=label,
        children=(_intro(intro_text), *children),
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


def _collect_by_kind(node: IRNode, kind: IRNodeKind) -> list[IRNode]:
    result = []
    if node.kind == kind:
        result.append(node)
    for c in node.children:
        result.extend(_collect_by_kind(c, kind))
    return result


# ---------------------------------------------------------------------------
# Fixture 1: Basic recovery
# Subsection: kohta 1, unnumbered "2) ...", kohta 3 → recovered to kohta 2.
# ---------------------------------------------------------------------------


def test_basic_recovery_digit() -> None:
    """Unnumbered paragraph with '2) ...' between kohta 1 and kohta 3 is recovered.

    After recovery:
    - Three numbered paragraphs with labels 1, 2, 3.
    - Exactly one BASE_NUM_IN_INTRO_RECOVERED fact.
    - No BASE_NUM_IN_INTRO_MISMATCH facts.
    - Recovered kohta 2 has a NUM child and correct CONTENT.
    - Tree invariants hold.
    """
    sec = _section("5", [
        _subsection("1", "Luettelo:", [
            _numbered_paragraph("1", "ensimmäinen kohta."),
            _unnumbered_paragraph("2) tämä alkaa numerolla joten kohta puuttui"),
            _numbered_paragraph("3", "kolmas kohta."),
        ]),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "basic-recovery-fixture")

    sec = _find_section(base_ir, "5")
    assert sec is not None

    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    recovered_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_RECOVERED]
    assert len(recovered_facts) == 1, f"Expected 1 RECOVERED fact, got {len(recovered_facts)}"
    assert recovered_facts[0].statute_id == "basic-recovery-fixture"

    mismatch_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_MISMATCH]
    assert not mismatch_facts, f"Expected no MISMATCH facts, got {len(mismatch_facts)}"

    subsec = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 3, f"Expected 3 paragraphs after recovery, got {len(paragraphs)}"

    labels = [p.label for p in paragraphs]
    assert labels == ["1", "2", "3"], f"Unexpected labels: {labels}"

    # Recovered kohta 2 must have a NUM child
    kohta2 = next(p for p in paragraphs if p.label == "2")
    num_children = [c for c in kohta2.children if c.kind == IRNodeKind.NUM]
    assert len(num_children) == 1, f"Expected NUM child on recovered kohta 2, got {len(num_children)}"
    assert num_children[0].text in ("2)", "2."), (
        f"NUM text should use sibling separator, got {num_children[0].text!r}"
    )

    # Content child should have the remaining text (no leading "2) " prefix)
    content_children = [c for c in kohta2.children if c.kind == IRNodeKind.CONTENT]
    assert len(content_children) == 1, f"Expected CONTENT child, got {len(content_children)}"
    content_text = content_children[0].text or ""
    assert not content_text.strip().startswith("2)"), (
        f"Leading token must be stripped from content: {content_text!r}"
    )
    assert "tämä alkaa numerolla" in content_text, (
        f"Remaining text must be preserved: {content_text!r}"
    )

    # No BASE_TAIL_PROSE_ABSORB triggered
    absorb_facts = [f for f in facts if f.kind_value == BASE_TAIL_PROSE_ABSORB]
    assert not absorb_facts, f"BASE_TAIL_PROSE_ABSORB must not fire, got {len(absorb_facts)}"


# ---------------------------------------------------------------------------
# Fixture 2: Sequence mismatch
# Subsection: kohta 1, unnumbered "5) ...", kohta 2 → MISMATCH, no recovery.
# ---------------------------------------------------------------------------


def test_sequence_mismatch_no_recovery() -> None:
    """Candidate '5)' between kohta 1 and kohta 2 does not fit the sequence.

    Expected: one BASE_NUM_IN_INTRO_MISMATCH fact, zero RECOVERED facts,
    the unnumbered paragraph remains unchanged, still three children in the subsection.
    """
    sec = _section("6", [
        _subsection("1", "Luettelo:", [
            _numbered_paragraph("1", "ensimmäinen."),
            _unnumbered_paragraph("5) tämä ei sovi sekvenssiin"),
            _numbered_paragraph("2", "toinen."),
        ]),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "mismatch-fixture")

    sec = _find_section(base_ir, "6")
    assert sec is not None

    recovered_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_RECOVERED]
    assert not recovered_facts, f"Expected no RECOVERED facts, got {len(recovered_facts)}"

    mismatch_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_MISMATCH]
    assert len(mismatch_facts) == 1, f"Expected 1 MISMATCH fact, got {len(mismatch_facts)}"

    # The subsection children should be unchanged in count
    # (unnumbered paragraph is still present; step 9 may emit a gap warning,
    # but no paragraphs are removed by T4c)
    subsec = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    # All three paragraphs remain (2 numbered + 1 unnumbered)
    assert len(paragraphs) == 3, f"Expected 3 paragraphs (no absorption), got {len(paragraphs)}"

    # Unnumbered paragraph must still be unnumbered
    unnumbered = [p for p in paragraphs if p.label is None]
    assert len(unnumbered) == 1, "Unnumbered paragraph must remain unchanged"


# ---------------------------------------------------------------------------
# Fixture 3: Leading citation false positive
# Unnumbered "( 999 ) viittaus..." — pattern requires no space before the label
# so parens-wrapped citations must NOT match.
# ---------------------------------------------------------------------------


def test_leading_citation_not_matched() -> None:
    """Content starting with '( 999 )' is not a num_in_intro — must not be recovered."""
    sec = _section("7", [
        _subsection("1", "Luettelo:", [
            _numbered_paragraph("1", "ensimmäinen."),
            _unnumbered_paragraph("( 999 ) viittaus lakiin numero 999"),
            _numbered_paragraph("2", "toinen."),
        ]),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "citation-fixture")

    recovered_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_RECOVERED]
    assert not recovered_facts, f"Citation peer must not be recovered, got {len(recovered_facts)}"

    mismatch_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_MISMATCH]
    assert not mismatch_facts, (
        f"No MISMATCH expected for citation (regex does not match), got {len(mismatch_facts)}"
    )

    # The paragraph "( 999 ) viittaus..." does not start with digit/letter followed
    # immediately by )/. — the leading '(' means the regex won't match.


# ---------------------------------------------------------------------------
# Fixture 4: Letter sequence recovery
# Subitems a, b, unnumbered "c) ...", d → recovered to subitem c.
#
# Note: the pass runs at SUBSECTION level and targets PARAGRAPH children.
# Letter-labeled paragraphs (subparagraph/alakohta style) that are direct
# PARAGRAPH children of a subsection can also be recovered if the regex matches.
# This fixture tests the general letter-sequence path.
# ---------------------------------------------------------------------------


def test_letter_sequence_recovery() -> None:
    """Unnumbered paragraph with 'c) ...' between paragraph b and paragraph d is recovered."""
    sec = _section("8", [
        _subsection("1", "Alakohdat:", [
            _numbered_paragraph("a", "alakohta a."),
            _numbered_paragraph("b", "alakohta b."),
            _unnumbered_paragraph("c) kolmas alakohta tässä"),
            _numbered_paragraph("d", "alakohta d."),
        ]),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "letter-recovery-fixture")

    sec = _find_section(base_ir, "8")
    assert sec is not None

    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    recovered_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_RECOVERED]
    assert len(recovered_facts) == 1, f"Expected 1 RECOVERED fact, got {len(recovered_facts)}"

    mismatch_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_MISMATCH]
    assert not mismatch_facts, f"Expected no MISMATCH facts, got {len(mismatch_facts)}"

    subsec = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 4, f"Expected 4 paragraphs after recovery, got {len(paragraphs)}"

    labels = [p.label for p in paragraphs]
    assert labels == ["a", "b", "c", "d"], f"Unexpected labels: {labels}"

    kohtac = next(p for p in paragraphs if p.label == "c")
    num_children = [c for c in kohtac.children if c.kind == IRNodeKind.NUM]
    assert len(num_children) == 1, f"Expected NUM child on recovered c, got {len(num_children)}"
    assert "kolmas alakohta" in (kohtac.children[1].text or ""), (
        "Remaining text must be in CONTENT child"
    )


# ---------------------------------------------------------------------------
# Fixture 5a: T4a regression guard
# sub_clause_with_list peer → UNNUMBERED_PEER_REPARENT, not RECOVERED.
# ---------------------------------------------------------------------------

_SUBCASE_B_XML = """
<section>
  <num>3</num>
  <subsection>
    <num>1</num>
    <intro><p>Tässä laissa tarkoitetaan:</p></intro>
    <paragraph>
      <num>1)</num>
      <intro><p>poikkeuksella tarkoitetaan:</p></intro>
      <subparagraph>
        <num>a)</num>
        <content><p>alakohta a ensimmäinen</p></content>
      </subparagraph>
      <subparagraph>
        <num>b)</num>
        <content><p>alakohta b ensimmäinen</p></content>
      </subparagraph>
    </paragraph>
    <paragraph>
      <intro><p>poikkeusta ei kuitenkaan sovelleta:</p></intro>
      <subparagraph>
        <num>a)</num>
        <content><p>alakohta a jatko</p></content>
      </subparagraph>
      <subparagraph>
        <num>b)</num>
        <content><p>alakohta b jatko</p></content>
      </subparagraph>
    </paragraph>
    <paragraph>
      <num>2)</num>
      <content><p>muulla säädöksellä tarkoitetaan lakia</p></content>
    </paragraph>
  </subsection>
</section>
"""


def test_t4a_regression_sub_clause_with_list() -> None:
    """T4a (sub_clause_with_list) produces UNNUMBERED_PEER_REPARENT, T4c does not fire."""
    body = _build_body(_SUBCASE_B_XML)
    raw_ir = fi_xml_to_ir_node(body)
    base_ir, facts = normalize_source_ir(raw_ir, "t4a-regression-fixture")

    reparent_facts = [f for f in facts if f.kind_value == UNNUMBERED_PEER_REPARENT]
    assert len(reparent_facts) == 1, (
        f"T4a must still emit 1 UNNUMBERED_PEER_REPARENT, got {len(reparent_facts)}"
    )

    recovered_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_RECOVERED]
    assert not recovered_facts, (
        f"T4c must not fire on sub_clause_with_list, got {len(recovered_facts)}"
    )

    mismatch_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_MISMATCH]
    assert not mismatch_facts, (
        f"T4c MISMATCH must not fire on sub_clause_with_list, got {len(mismatch_facts)}"
    )

    absorb_facts = [f for f in facts if f.kind_value == BASE_TAIL_PROSE_ABSORB]
    assert not absorb_facts, (
        f"T4b must not fire on sub_clause_with_list, got {len(absorb_facts)}"
    )


# ---------------------------------------------------------------------------
# Fixture 5b: T4b regression guard
# tail_prose peer → BASE_TAIL_PROSE_ABSORB, not RECOVERED.
# ---------------------------------------------------------------------------


def test_t4b_regression_tail_prose() -> None:
    """T4b (tail_prose) produces BASE_TAIL_PROSE_ABSORB, T4c does not fire."""
    sec = _section("17", [
        _subsection("1", "Toimielimen jäsenyydestä voidaan erottaa, jos jäsen:", [
            _numbered_paragraph("1", "ei enää täytä jäsenyysvaatimuksia."),
            _numbered_paragraph("2", "on laiminlyönyt tehtävänsä toistuvasti."),
            _unnumbered_paragraph(
                "Eroamispäätös on tehtävä kirjallisesti ja annettava tiedoksi asianomaiselle."
            ),
        ]),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "t4b-regression-fixture")

    absorb_facts = [f for f in facts if f.kind_value == BASE_TAIL_PROSE_ABSORB]
    assert len(absorb_facts) == 1, (
        f"T4b must still emit 1 BASE_TAIL_PROSE_ABSORB, got {len(absorb_facts)}"
    )

    recovered_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_RECOVERED]
    assert not recovered_facts, (
        f"T4c must not fire on tail_prose, got {len(recovered_facts)}"
    )

    mismatch_facts = [f for f in facts if f.kind_value == BASE_NUM_IN_INTRO_MISMATCH]
    assert not mismatch_facts, (
        f"T4c MISMATCH must not fire on tail_prose, got {len(mismatch_facts)}"
    )
