"""Tests for Step 8.5: unnumbered paragraph peer reparenting.

Covers the ``_reparent_sub_clause_with_list_peers`` pass in
``source_normalize.py``.

Two fixtures:
- **Sub-case A** (1988/161 §24): unnumbered peer has subparagraphs, but the
  preceding numbered kohta has NO own subparagraphs.  Subparagraphs are
  reparented directly as kohta children.
- **Sub-case B** (2013/331 §3): unnumbered peer has subparagraphs, and the
  preceding numbered kohta ALREADY has own subparagraphs.  A WRAP_UP
  continuation facet with opaque ``__continuation__`` marker is appended.

Both cases must:
- Emit exactly one UNNUMBERED_PEER_REPARENT SourceNormalizationFact.
- Not produce any synthetic public labels.
- Pass tree invariant checks.
"""

from __future__ import annotations

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.tree_ops import check_invariants
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.source_normalization_kinds import UNNUMBERED_PEER_REPARENT
from lawvm.finland.source_normalize import normalize_source_ir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _build_body(inner_xml: str) -> etree._Element:
    """Wrap inner XML in a minimal AKN body element."""
    return etree.fromstring(
        f'<body xmlns="{AKN_NS}">{inner_xml}</body>'
    )


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
# Sub-case A fixture: unnumbered peer, preceding kohta has no subparagraphs
# (matches 1988/161 §24 kohta 6 pattern)
# ---------------------------------------------------------------------------

_SUBCASE_A_XML = """
<section>
  <num>24</num>
  <subsection>
    <num>1</num>
    <intro><p>Hakemukseen on liitettävä seuraavat selvitykset:</p></intro>
    <paragraph>
      <num>5)</num>
      <content><p>muu selvitys</p></content>
    </paragraph>
    <paragraph>
      <num>6)</num>
      <intro><p>ydinlaitoshankkeen selvitys; sekä</p></intro>
    </paragraph>
    <paragraph>
      <subparagraph>
        <num>a)</num>
        <content><p>selvitys sijaintipaikan sopivuudesta</p></content>
      </subparagraph>
      <subparagraph>
        <num>b)</num>
        <content><p>selvitys turvallisuustekijöistä</p></content>
      </subparagraph>
      <subparagraph>
        <num>c)</num>
        <content><p>selvitys ympäristövaikutuksista</p></content>
      </subparagraph>
    </paragraph>
  </subsection>
</section>
"""


def test_subcase_a_reparents_subparagraphs_directly() -> None:
    """Sub-case A: subparagraphs are attached directly under the preceding kohta."""
    body = _build_body(_SUBCASE_A_XML)
    raw_ir = fi_xml_to_ir_node(body)
    base_ir, facts = normalize_source_ir(raw_ir, "1988/161-fixture")

    sec = _find_section(base_ir, "24")
    assert sec is not None, "§24 not found in normalized IR"

    # Tree invariants must pass
    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    # Exactly one UNNUMBERED_PEER_REPARENT fact
    reparent_facts = [
        f for f in facts
        if f.kind_value == UNNUMBERED_PEER_REPARENT
    ]
    assert len(reparent_facts) == 1, (
        f"Expected 1 UNNUMBERED_PEER_REPARENT fact, got {len(reparent_facts)}"
    )
    assert reparent_facts[0].statute_id == "1988/161-fixture"

    # The subsection should have 2 paragraphs (5 and 6), not 3
    subsec = next(
        c for c in sec.children if c.kind == IRNodeKind.SUBSECTION
    )
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 2, (
        f"Expected 2 paragraphs after reparenting, got {len(paragraphs)}"
    )

    # kohta '6' should now have 3 SUBPARAGRAPH children directly
    kohta6 = next(p for p in paragraphs if p.label == "6")
    subparas = [c for c in kohta6.children if c.kind == IRNodeKind.SUBPARAGRAPH]
    assert len(subparas) == 3, (
        f"Expected 3 subparagraphs under kohta 6, got {len(subparas)}"
    )
    labels = [sp.label for sp in subparas]
    assert labels == ["a", "b", "c"], f"Expected a/b/c labels, got {labels}"

    # No WRAP_UP node anywhere
    wrapups = _collect_by_kind(sec, IRNodeKind.WRAP_UP)
    assert not wrapups, "Sub-case A must NOT produce WRAP_UP nodes"

    # No synthetic labels (no digits with prime/underscore suffix patterns)
    all_nodes = _collect_by_kind(sec, IRNodeKind.SUBPARAGRAPH)
    for sp in all_nodes:
        lbl = sp.label or ""
        assert "'" not in lbl and "_" not in lbl, (
            f"Synthetic suffix in label: {lbl!r}"
        )


# ---------------------------------------------------------------------------
# Sub-case B fixture: unnumbered peer, preceding kohta already has subparagraphs
# (matches 2013/331 §3 kohta 1 pattern)
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
      <subparagraph>
        <num>c)</num>
        <content><p>alakohta c ensimmäinen</p></content>
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
      <subparagraph>
        <num>c)</num>
        <content><p>alakohta c jatko</p></content>
      </subparagraph>
    </paragraph>
    <paragraph>
      <num>2)</num>
      <content><p>muulla säädöksellä tarkoitetaan lakia</p></content>
    </paragraph>
  </subsection>
</section>
"""


def test_subcase_b_reparents_as_wrapup() -> None:
    """Sub-case B: a WRAP_UP continuation facet is appended to the preceding kohta."""
    body = _build_body(_SUBCASE_B_XML)
    raw_ir = fi_xml_to_ir_node(body)
    base_ir, facts = normalize_source_ir(raw_ir, "2013/331-fixture")

    sec = _find_section(base_ir, "3")
    assert sec is not None, "§3 not found in normalized IR"

    # Tree invariants must pass
    violations = check_invariants(sec)
    assert not violations, f"Tree violations: {violations}"

    # Exactly one UNNUMBERED_PEER_REPARENT fact
    reparent_facts = [
        f for f in facts
        if f.kind_value == UNNUMBERED_PEER_REPARENT
    ]
    assert len(reparent_facts) == 1, (
        f"Expected 1 UNNUMBERED_PEER_REPARENT fact, got {len(reparent_facts)}"
    )
    assert reparent_facts[0].statute_id == "2013/331-fixture"

    # The subsection should have 2 paragraphs (kohta 1 and kohta 2), not 3
    subsec = next(
        c for c in sec.children if c.kind == IRNodeKind.SUBSECTION
    )
    paragraphs = [c for c in subsec.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 2, (
        f"Expected 2 paragraphs after reparenting, got {len(paragraphs)}"
    )

    # kohta '1' must contain a WRAP_UP node with __continuation__ marker
    kohta1 = next(p for p in paragraphs if p.label == "1")
    wrapups = [c for c in kohta1.children if c.kind == IRNodeKind.WRAP_UP]
    assert len(wrapups) == 1, (
        f"Expected 1 WRAP_UP child on kohta 1, got {len(wrapups)}"
    )
    wu = wrapups[0]
    assert wu.attrs.get("__continuation__") == "1", (
        f"WRAP_UP must carry __continuation__='1', got attrs={dict(wu.attrs)}"
    )

    # WRAP_UP must contain the 3 continuation subparagraphs
    wu_subparas = [c for c in wu.children if c.kind == IRNodeKind.SUBPARAGRAPH]
    assert len(wu_subparas) == 3, (
        f"Expected 3 subparagraphs inside WRAP_UP, got {len(wu_subparas)}"
    )

    # kohta 1 original subparagraphs are still directly present
    direct_subparas = [c for c in kohta1.children if c.kind == IRNodeKind.SUBPARAGRAPH]
    assert len(direct_subparas) == 3, (
        f"Expected 3 original subparagraphs to remain on kohta 1, got {len(direct_subparas)}"
    )

    # No synthetic labels anywhere in the section
    all_subparas = _collect_by_kind(sec, IRNodeKind.SUBPARAGRAPH)
    for sp in all_subparas:
        lbl = sp.label or ""
        assert "'" not in lbl and "_" not in lbl, (
            f"Synthetic suffix in label: {lbl!r}"
        )


# ---------------------------------------------------------------------------
# No-op: unnumbered peer without subparagraphs is left untouched
# ---------------------------------------------------------------------------

_NOOP_XML = """
<section>
  <num>5</num>
  <subsection>
    <num>1</num>
    <paragraph>
      <num>1)</num>
      <content><p>ensimmäinen kohta</p></content>
    </paragraph>
    <paragraph>
      <content><p>tämä on pelkkä tekstikappale ilman alakohtia</p></content>
    </paragraph>
    <paragraph>
      <num>2)</num>
      <content><p>toinen kohta</p></content>
    </paragraph>
  </subsection>
</section>
"""


def test_unnumbered_peer_without_subparagraphs_left_untouched() -> None:
    """Unnumbered paragraphs without subparagraph children are not reparented."""
    body = _build_body(_NOOP_XML)
    raw_ir = fi_xml_to_ir_node(body)
    base_ir, facts = normalize_source_ir(raw_ir, "noop-fixture")

    reparent_facts = [
        f for f in facts
        if f.kind_value == UNNUMBERED_PEER_REPARENT
    ]
    assert not reparent_facts, (
        f"Expected no UNNUMBERED_PEER_REPARENT facts for plain unnumbered peer, got {len(reparent_facts)}"
    )

    # No WRAP_UP node should be present
    sec = _find_section(base_ir, "5")
    assert sec is not None
    wrapups = _collect_by_kind(sec, IRNodeKind.WRAP_UP)
    # WRAP_UP from __continuation__ marker must not be present
    continuation_wrapups = [wu for wu in wrapups if wu.attrs.get("__continuation__")]
    assert not continuation_wrapups, (
        f"Expected no continuation WRAP_UP for plain unnumbered peer, got {len(continuation_wrapups)}"
    )
