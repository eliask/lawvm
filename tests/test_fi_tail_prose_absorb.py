"""Tests for Step 8.6: tail_prose unnumbered peer absorption.

Covers the ``_absorb_tail_prose_peers`` pass in ``source_normalize.py``.

IMPORTANT: tests construct IR trees directly via ``IRNode(...)`` rather than
via ``fi_xml_to_ir_node(body)``.  The XML parse path's
``_merge_split_numbered_paragraph_continuations`` already merges tail-prose
peers into the preceding numbered kohta at parse time for the typical XML
shape, so the peer never reaches ``normalize_source_ir`` through that route.
T4b's pass catches the remaining cases where the upstream merge is
suppressed (preceding kohta ends with terminal punctuation, or the peer
arrives in a partially-constructed IR from another source).  To exercise
the pass deterministically we build IRNodes by hand.

Four fixture shapes:
- single tail-prose peer after a numbered list
- tail-prose peer after a three-item list
- multiple sequential tail-prose peers absorbed into the same wrapUp
- num_in_intro peer skipped (heuristic)

Plus a regression guard that T4a sub_clause_with_list handling is unchanged.
"""

from __future__ import annotations

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.tree_ops import check_invariants
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.source_normalization_kinds import (
    BASE_TAIL_PROSE_ABSORB,
    UNNUMBERED_PEER_REPARENT,
)
from lawvm.finland.source_normalize import normalize_source_ir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _build_body(inner_xml: str) -> etree._Element:
    """Wrap inner XML in a minimal AKN body element."""
    return etree.fromstring(f'<body xmlns="{AKN_NS}">{inner_xml}</body>')


def _num(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.NUM, text=text)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


def _numbered_paragraph(label: str, text: str) -> IRNode:
    """Construct a numbered PARAGRAPH IRNode with NUM and CONTENT children."""
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(_num(f"{label})"), _content(text)),
    )


def _unnumbered_paragraph(text: str) -> IRNode:
    """Construct an unnumbered PARAGRAPH IRNode with only CONTENT (tail-prose)."""
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=None,
        children=(_content(text),),
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
    """DFS search for a SECTION with the given label."""
    if node.kind == IRNodeKind.SECTION and node.label == label:
        return node
    for c in node.children:
        r = _find_section(c, label)
        if r:
            return r
    return None


def _collect_by_kind(node: IRNode, kind: IRNodeKind) -> list[IRNode]:
    """Collect all nodes of a given kind in DFS order."""
    result = []
    if node.kind == kind:
        result.append(node)
    for c in node.children:
        result.extend(_collect_by_kind(c, kind))
    return result


# ---------------------------------------------------------------------------
# Fixture 1: 1990s-style (1994/1080 pattern)
# Single tail-prose paragraph after a numbered list.
# ---------------------------------------------------------------------------

_FIXTURE_1990S_XML = """
<section>
  <num>17</num>
  <subsection>
    <num>1</num>
    <intro><p>Toimielimen jäsenyydestä voidaan eroottaa, jos jäsen:</p></intro>
    <paragraph>
      <num>1)</num>
      <content><p>ei enää täytä jäsenyysvaatimuksia</p></content>
    </paragraph>
    <paragraph>
      <num>2)</num>
      <content><p>on laiminlyönyt tehtävänsä toistuvasti</p></content>
    </paragraph>
    <paragraph>
      <content><p>Eroamispäätös on tehtävä kirjallisesti ja annettava tiedoksi asianomaiselle.</p></content>
    </paragraph>
  </subsection>
</section>
"""


def test_1990s_tail_prose_absorbed() -> None:
    """Single tail-prose peer absorbed into preceding kohta's wrapUp.

    Built via direct IRNode construction to bypass the XML parse path's
    upstream merge of split numbered paragraphs.
    """
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
    base_ir, facts = normalize_source_ir(raw_ir, "1994/1080-fixture")

    sec = _find_section(base_ir, "17")
    assert sec is not None, "§17 not found"

    # Tree invariants
    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    # Exactly one BASE_TAIL_PROSE_ABSORB fact
    absorb_facts = [f for f in facts if f.kind_value == BASE_TAIL_PROSE_ABSORB]
    assert len(absorb_facts) == 1, f"Expected 1 BASE_TAIL_PROSE_ABSORB fact, got {len(absorb_facts)}"
    assert absorb_facts[0].statute_id == "1994/1080-fixture"

    # No UNNUMBERED_PEER_REPARENT (that is T4a only)
    reparent_facts = [f for f in facts if f.kind_value == UNNUMBERED_PEER_REPARENT]
    assert not reparent_facts, f"Expected no UNNUMBERED_PEER_REPARENT facts, got {len(reparent_facts)}"

    # Subsection should now have 2 paragraphs (kohta 1 and 2), not 3
    subsec = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 2, f"Expected 2 paragraphs after absorption, got {len(paragraphs)}"

    # Kohta 2 (last numbered) must have a tail_prose WRAP_UP
    kohta2 = next(p for p in paragraphs if p.label == "2")
    wrapups = [c for c in kohta2.children if c.kind == IRNodeKind.WRAP_UP]
    assert len(wrapups) == 1, f"Expected 1 WRAP_UP on kohta 2, got {len(wrapups)}"
    wu = wrapups[0]
    assert wu.attrs.get("__tail_prose__") == "1", (
        f"WRAP_UP must carry __tail_prose__='1', got {dict(wu.attrs)}"
    )
    assert "Eroamispäätös" in (wu.text or ""), (
        f"WRAP_UP text must contain absorbed prose, got: {wu.text!r}"
    )

    # No __continuation__ WRAP_UP (that is T4a sub_case_B only)
    continuation_wus = [wu for wu in _collect_by_kind(sec, IRNodeKind.WRAP_UP)
                        if wu.attrs.get("__continuation__")]
    assert not continuation_wus, "Must not produce __continuation__ WRAP_UP for tail_prose"

    # No synthetic labels
    for sp in _collect_by_kind(sec, IRNodeKind.SUBPARAGRAPH):
        lbl = sp.label or ""
        assert "'" not in lbl and "_exc" not in lbl, f"Synthetic label found: {lbl!r}"


# ---------------------------------------------------------------------------
# Fixture 2: 2010s-style (2015/1745 pattern)
# Tail-prose peer after a numbered list in a simple statute.
# ---------------------------------------------------------------------------

_FIXTURE_2010S_XML = """
<section>
  <num>2</num>
  <subsection>
    <num>1</num>
    <intro><p>Tässä asetuksessa tarkoitetaan:</p></intro>
    <paragraph>
      <num>1)</num>
      <content><p>hakijalla luonnollista henkilöä tai oikeushenkilöä</p></content>
    </paragraph>
    <paragraph>
      <num>2)</num>
      <content><p>toimivaltaisella viranomaisella asianomaista ministeriötä</p></content>
    </paragraph>
    <paragraph>
      <content><p>Mitä tässä pykälässä säädetään, koskee soveltuvin osin myös yhteisöjä.</p></content>
    </paragraph>
  </subsection>
</section>
"""


def test_2010s_tail_prose_absorbed() -> None:
    """Tail-prose peer after definition list is absorbed (direct IR construction)."""
    sec = _section("2", [
        _subsection("1", "Tässä asetuksessa tarkoitetaan:", [
            _numbered_paragraph("1", "hakijalla luonnollista henkilöä tai oikeushenkilöä."),
            _numbered_paragraph("2", "toimivaltaisella viranomaisella asianomaista ministeriötä."),
            _unnumbered_paragraph(
                "Mitä tässä pykälässä säädetään, koskee soveltuvin osin myös yhteisöjä."
            ),
        ]),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "2015/1745-fixture")

    sec = _find_section(base_ir, "2")
    assert sec is not None, "§2 not found"

    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    absorb_facts = [f for f in facts if f.kind_value == BASE_TAIL_PROSE_ABSORB]
    assert len(absorb_facts) == 1, f"Expected 1 BASE_TAIL_PROSE_ABSORB, got {len(absorb_facts)}"

    subsec = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 2, f"Expected 2 paragraphs, got {len(paragraphs)}"

    # kohta 2 absorbs the tail
    kohta2 = next(p for p in paragraphs if p.label == "2")
    wrapups = [c for c in kohta2.children if c.kind == IRNodeKind.WRAP_UP]
    assert len(wrapups) == 1, f"Expected 1 WRAP_UP, got {len(wrapups)}"
    wu = wrapups[0]
    assert wu.attrs.get("__tail_prose__") == "1"
    assert "yhteisöjä" in (wu.text or ""), f"Text not preserved: {wu.text!r}"


# ---------------------------------------------------------------------------
# Fixture 3: 2020s-style (2021/946 pattern)
# Tail-prose peer after a longer numbered list (3 kohdat).
# ---------------------------------------------------------------------------

_FIXTURE_2020S_XML = """
<section>
  <num>1</num>
  <subsection>
    <num>1</num>
    <intro><p>Hakemuksessa on ilmoitettava:</p></intro>
    <paragraph>
      <num>1)</num>
      <content><p>hakijan nimi ja yhteystiedot</p></content>
    </paragraph>
    <paragraph>
      <num>2)</num>
      <content><p>haettavan luvantyypin kuvaus</p></content>
    </paragraph>
    <paragraph>
      <num>3)</num>
      <content><p>toiminnan aloittamisajankohta</p></content>
    </paragraph>
    <paragraph>
      <content><p>Hakemus on toimitettava viranomaiselle viimeistään kolme kuukautta ennen toiminnan suunniteltua aloittamista.</p></content>
    </paragraph>
  </subsection>
</section>
"""


def test_2020s_tail_prose_absorbed() -> None:
    """Tail-prose peer after three-item list is absorbed (direct IR construction)."""
    sec = _section("1", [
        _subsection("1", "Hakemuksessa on ilmoitettava:", [
            _numbered_paragraph("1", "hakijan nimi ja yhteystiedot."),
            _numbered_paragraph("2", "haettavan luvantyypin kuvaus."),
            _numbered_paragraph("3", "toiminnan aloittamisajankohta."),
            _unnumbered_paragraph(
                "Hakemus on toimitettava viranomaiselle viimeistään kolme kuukautta ennen toiminnan suunniteltua aloittamista."
            ),
        ]),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "2021/946-fixture")

    sec = _find_section(base_ir, "1")
    assert sec is not None, "§1 not found"

    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    absorb_facts = [f for f in facts if f.kind_value == BASE_TAIL_PROSE_ABSORB]
    assert len(absorb_facts) == 1, f"Expected 1 BASE_TAIL_PROSE_ABSORB, got {len(absorb_facts)}"

    subsec = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 3, f"Expected 3 paragraphs after absorption, got {len(paragraphs)}"

    # kohta 3 is the last numbered — it absorbs the tail
    kohta3 = next(p for p in paragraphs if p.label == "3")
    wrapups = [c for c in kohta3.children if c.kind == IRNodeKind.WRAP_UP]
    assert len(wrapups) == 1, f"Expected 1 WRAP_UP, got {len(wrapups)}"
    wu = wrapups[0]
    assert wu.attrs.get("__tail_prose__") == "1"
    assert "kolme kuukautta" in (wu.text or ""), f"Text not preserved: {wu.text!r}"


# ---------------------------------------------------------------------------
# Fixture 4: multiple tail-prose peers appended to the same wrapUp
# ---------------------------------------------------------------------------

_MULTI_TAIL_XML = """
<section>
  <num>27</num>
  <subsection>
    <num>1</num>
    <intro><p>Luettelo:</p></intro>
    <paragraph>
      <num>5)</num>
      <content><p>viides kohta</p></content>
    </paragraph>
    <paragraph>
      <content><p>Ensimmäinen yhteinen lause.</p></content>
    </paragraph>
    <paragraph>
      <content><p>Toinen yhteinen lause.</p></content>
    </paragraph>
  </subsection>
</section>
"""


def test_multiple_tail_prose_peers_merged_into_wrapup() -> None:
    """Multiple sequential tail-prose peers merged into the same wrapUp."""
    sec = _section("27", [
        _subsection("1", "Luettelo:", [
            _numbered_paragraph("5", "viides kohta."),
            _unnumbered_paragraph("Ensimmäinen yhteinen lause."),
            _unnumbered_paragraph("Toinen yhteinen lause."),
        ]),
    ])
    raw_ir = _body_with_section(sec)
    base_ir, facts = normalize_source_ir(raw_ir, "2022/1381-fixture")

    sec = _find_section(base_ir, "27")
    assert sec is not None, "§27 not found"

    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    # Two absorb facts (one per peer)
    absorb_facts = [f for f in facts if f.kind_value == BASE_TAIL_PROSE_ABSORB]
    assert len(absorb_facts) == 2, f"Expected 2 BASE_TAIL_PROSE_ABSORB facts, got {len(absorb_facts)}"

    subsec = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 1, f"Expected 1 paragraph after absorption, got {len(paragraphs)}"

    kohta5 = paragraphs[0]
    assert kohta5.label == "5"
    wrapups = [c for c in kohta5.children if c.kind == IRNodeKind.WRAP_UP]
    assert len(wrapups) == 1, f"Expected 1 WRAP_UP, got {len(wrapups)}"
    wu = wrapups[0]
    assert wu.attrs.get("__tail_prose__") == "1"
    # Both texts should be in the combined wrapUp
    assert "Ensimmäinen" in (wu.text or ""), f"First text not found: {wu.text!r}"
    assert "Toinen" in (wu.text or ""), f"Second text not found: {wu.text!r}"


# ---------------------------------------------------------------------------
# Fixture 5: num_in_intro peer is NOT absorbed (skipped by heuristic)
# ---------------------------------------------------------------------------

_NUM_IN_INTRO_XML = """
<section>
  <num>5</num>
  <subsection>
    <num>1</num>
    <paragraph>
      <num>1)</num>
      <content><p>ensimmäinen kohta</p></content>
    </paragraph>
    <paragraph>
      <content><p>2) tämä alkaa numerolla joten se on num_in_intro</p></content>
    </paragraph>
    <paragraph>
      <num>3)</num>
      <content><p>kolmas kohta</p></content>
    </paragraph>
  </subsection>
</section>
"""


def test_num_in_intro_peer_not_absorbed() -> None:
    """Peers whose content starts with N) are skipped (num_in_intro, handled by T4c)."""
    body = _build_body(_NUM_IN_INTRO_XML)
    raw_ir = fi_xml_to_ir_node(body)
    base_ir, facts = normalize_source_ir(raw_ir, "num-in-intro-fixture")

    absorb_facts = [f for f in facts if f.kind_value == BASE_TAIL_PROSE_ABSORB]
    assert not absorb_facts, (
        f"num_in_intro peer must not be absorbed, got {len(absorb_facts)} facts"
    )

    sec = _find_section(base_ir, "5")
    assert sec is not None

    # The subsection should still have 3 child paragraphs (no absorption happened)
    subsec = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    # Note: after step 9 the duplicate-label / numbering logic may fire, but
    # no absorption should have happened.  The key invariant is zero absorb facts.


# ---------------------------------------------------------------------------
# Fixture 6: T4a sub_clause_with_list behavior unchanged after T4b is added
# (regression guard — 2013/331-style)
# ---------------------------------------------------------------------------

_SUBCASE_B_UNCHANGED_XML = """
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


def test_sub_clause_with_list_unchanged_by_t4b() -> None:
    """T4a behavior (sub_clause_with_list reparenting) is unchanged after T4b lands.

    The 2013/331-style pattern must still produce:
    - exactly 1 UNNUMBERED_PEER_REPARENT fact (from T4a)
    - 0 BASE_TAIL_PROSE_ABSORB facts (T4b must NOT trigger on sub_clause_with_list)
    - a WRAP_UP with __continuation__=1 on kohta 1 (T4a sub_case_B)
    """
    body = _build_body(_SUBCASE_B_UNCHANGED_XML)
    raw_ir = fi_xml_to_ir_node(body)
    base_ir, facts = normalize_source_ir(raw_ir, "2013/331-regression")

    sec = _find_section(base_ir, "3")
    assert sec is not None

    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    reparent_facts = [f for f in facts if f.kind_value == UNNUMBERED_PEER_REPARENT]
    assert len(reparent_facts) == 1, (
        f"T4a must still emit 1 UNNUMBERED_PEER_REPARENT, got {len(reparent_facts)}"
    )

    absorb_facts = [f for f in facts if f.kind_value == BASE_TAIL_PROSE_ABSORB]
    assert not absorb_facts, (
        f"T4b must not trigger on sub_clause_with_list, got {len(absorb_facts)} facts"
    )

    subsec = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 2, f"Expected 2 paragraphs after T4a, got {len(paragraphs)}"

    kohta1 = next(p for p in paragraphs if p.label == "1")
    continuation_wus = [c for c in kohta1.children
                        if c.kind == IRNodeKind.WRAP_UP and c.attrs.get("__continuation__")]
    assert len(continuation_wus) == 1, (
        f"T4a sub_case_B must still produce __continuation__ WRAP_UP, got {len(continuation_wus)}"
    )
    tail_wus = [c for c in kohta1.children
                if c.kind == IRNodeKind.WRAP_UP and c.attrs.get("__tail_prose__")]
    assert not tail_wus, (
        f"T4b must not add __tail_prose__ WRAP_UP for sub_clause_with_list, got {len(tail_wus)}"
    )
