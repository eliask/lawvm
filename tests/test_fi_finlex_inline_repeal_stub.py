"""Tests for Finlex paragraph-level inline repeal stub detection and projection stripping.

The Finlex consolidated oracle XML uses an italic-paragraph convention to record
that a kohta has been repealed:

    <paragraph eId="chp_1__sec_3__subsec_1__para_2v20211030">
      <content><p><i>2 kohta on kumottu A:lla 25.11.2021/1030.</i></p></content>
    </paragraph>

These stubs are editorial metadata, not law.  LawVM must strip them from the
oracle semantic tree so they do not pollute diff scores with spurious
``unit_added_right`` events.

See notes/FINLAND_PROFILE_ONTOLOGY_GAPS_2026-04-15.md §1.9 for the fix recipe.
See notes/2013_331_UNNUMBERED_PEER_CASE_STUDY.md §4 for the stub anatomy.
"""
from __future__ import annotations

import pytest
from lxml import etree

from lawvm.finland.inline_repeal_stub import (
    _is_paragraph_level_kumottu_stub,
    extract_paragraph_stub_amendment_id,
    extract_paragraph_stub_target_range,
)
from lawvm.semantic.projection import semantic_structure_from_oracle


# ---------------------------------------------------------------------------
# Helper: build a minimal <paragraph> XML element for testing
# ---------------------------------------------------------------------------

def _make_repeal_stub(
    eid: str = "chp_1__sec_3__subsec_1__para_2v20211030",
    text: str = "2 kohta on kumottu A:lla 25.11.2021/1030.",
    include_italic: bool = True,
    include_num: bool = False,
    num_text: str = "2)",
) -> etree._Element:
    """Build a <paragraph> element matching the Finlex repeal stub convention."""
    para = etree.Element("paragraph")
    para.set("eId", eid)
    if include_num:
        num_el = etree.SubElement(para, "num")
        num_el.text = num_text
    content = etree.SubElement(para, "content")
    p = etree.SubElement(content, "p")
    if include_italic:
        i = etree.SubElement(p, "i")
        i.text = text
    else:
        p.text = text
    return para


# ---------------------------------------------------------------------------
# Detector: _is_paragraph_level_kumottu_stub
# ---------------------------------------------------------------------------

class TestIsParagraphLevelKumottuStub:
    def test_single_kohta_match(self) -> None:
        """'2 kohta on kumottu A:lla 25.11.2021/1030.' → match."""
        para = _make_repeal_stub(
            text="2 kohta on kumottu A:lla 25.11.2021/1030.",
        )
        assert _is_paragraph_level_kumottu_stub(para) is True

    def test_range_kohta_match(self) -> None:
        """'1–2 kohta on kumottu A:lla 25.11.2021/1030.' → match (range form)."""
        para = _make_repeal_stub(
            eid="chp_1__sec_3__subsec_1__para_1v20211030",
            text="1\u20132 kohta on kumottu A:lla 25.11.2021/1030.",
        )
        assert _is_paragraph_level_kumottu_stub(para) is True

    def test_hyphen_range_match(self) -> None:
        """ASCII hyphen range '1-2 kohta ...' also matches."""
        para = _make_repeal_stub(
            eid="chp_1__sec_3__subsec_1__para_1v20211030",
            text="1-2 kohta on kumottu A:lla 25.11.2021/1030.",
        )
        assert _is_paragraph_level_kumottu_stub(para) is True

    def test_muutettu_no_match(self) -> None:
        """'muutettu' (amended) is NOT a repeal stub."""
        para = _make_repeal_stub(
            text="2 kohta on muutettu A:lla 25.11.2021/1030.",
        )
        assert _is_paragraph_level_kumottu_stub(para) is False

    def test_numbered_paragraph_no_match(self) -> None:
        """A <paragraph> with a <num> child is NOT a repeal stub."""
        para = _make_repeal_stub(
            text="tavanomaisella jätteellä jätettä, joka ei ole vaarallista jätettä;",
            include_italic=False,
            include_num=True,
            num_text="2)",
        )
        assert _is_paragraph_level_kumottu_stub(para) is False

    def test_no_versioned_eid_no_match(self) -> None:
        """A paragraph with a plain (non-versioned) eId is not flagged."""
        para = _make_repeal_stub(
            eid="chp_1__sec_3__subsec_1__para_2",  # no vYYYYMMDD suffix
            text="2 kohta on kumottu A:lla 25.11.2021/1030.",
        )
        assert _is_paragraph_level_kumottu_stub(para) is False

    def test_no_italic_no_match(self) -> None:
        """The repeal text without <i> wrapper is not matched."""
        para = _make_repeal_stub(
            text="2 kohta on kumottu A:lla 25.11.2021/1030.",
            include_italic=False,
        )
        assert _is_paragraph_level_kumottu_stub(para) is False

    def test_non_breaking_space_tolerated(self) -> None:
        """Non-breaking spaces inside the text do not break detection."""
        text_with_nbsp = "2\u00a0kohta\u00a0on\u00a0kumottu\u00a0A:lla\u00a025.11.2021/1030."
        para = _make_repeal_stub(text=text_with_nbsp)
        assert _is_paragraph_level_kumottu_stub(para) is True

    def test_momentti_form_matches(self) -> None:
        """'2 momentti on kumottu A:lla ...' is also a valid repeal stub."""
        para = _make_repeal_stub(
            eid="chp_1__sec_3__subsec_1__para_2v20211030",
            text="2 momentti on kumottu A:lla 25.11.2021/1030.",
        )
        assert _is_paragraph_level_kumottu_stub(para) is True


# ---------------------------------------------------------------------------
# Amendment id and target range extraction helpers
# ---------------------------------------------------------------------------

class TestExtractHelpers:
    def test_amendment_id_from_ref(self) -> None:
        """Amendment id extracted from <ref> text."""
        para = etree.fromstring(
            '<paragraph eId="chp_1__sec_3__subsec_1__para_2v20211030">'
            '<content><p><i>2 kohta on kumottu A:lla '
            '<ref href="#entryIntoForce_20211030">25.11.2021/1030</ref>.</i></p></content>'
            '</paragraph>'
        )
        assert extract_paragraph_stub_amendment_id(para) == "2021/1030"

    def test_amendment_id_from_plain_text_fallback(self) -> None:
        """Amendment id extracted from plain text when no <ref>."""
        para = _make_repeal_stub(text="2 kohta on kumottu A:lla 25.11.2021/1030.")
        assert extract_paragraph_stub_amendment_id(para) == "2021/1030"

    def test_target_range_single(self) -> None:
        """Single kohta target → list of one."""
        para = _make_repeal_stub(text="2 kohta on kumottu A:lla 25.11.2021/1030.")
        assert extract_paragraph_stub_target_range(para) == [2]

    def test_target_range_range(self) -> None:
        """Range kohta target → list covering range."""
        para = _make_repeal_stub(
            eid="chp_1__sec_3__subsec_1__para_1v20211030",
            text="1\u20132 kohta on kumottu A:lla 25.11.2021/1030.",
        )
        assert extract_paragraph_stub_target_range(para) == [1, 2]


# ---------------------------------------------------------------------------
# Projection stripping
# ---------------------------------------------------------------------------

def _make_subsection_with_stub() -> etree._Element:
    """Build a <subsection> containing one real item and one repeal stub."""
    subsec = etree.fromstring("""
    <subsection eId="chp_1__sec_3__subsec_1">
      <paragraph eId="chp_1__sec_3__subsec_1__para_1">
        <num>1)</num>
        <content><p>kaatopaikalla jätteiden loppukäsittelypaikkaa</p></content>
      </paragraph>
      <paragraph eId="chp_1__sec_3__subsec_1__para_2v20211030">
        <content><p><i>2 kohta on kumottu A:lla 25.11.2021/1030.</i></p></content>
      </paragraph>
      <paragraph eId="chp_1__sec_3__subsec_1__para_3">
        <num>3)</num>
        <content><p>pysyvällä jätteellä jätettä joka ei hajoa</p></content>
      </paragraph>
    </subsection>
    """)
    return subsec


class TestProjectionStripping:
    def test_stub_absent_from_children(self) -> None:
        """The repeal stub paragraph must not appear in the projected children."""
        subsec = _make_subsection_with_stub()
        result = semantic_structure_from_oracle(subsec)
        assert result is not None
        child_labels = [c.label for c in result.children]
        # Items 1 and 3 present; no spurious ordinal-fallback "2" from the stub
        assert "1" in child_labels
        assert "3" in child_labels
        # The stub must not produce a child node
        assert len(result.children) == 2, (
            f"Expected 2 children (items 1 and 3), got {len(result.children)}: "
            f"{[(c.label, c.label_basis) for c in result.children]}"
        )

    def test_stub_emits_observation(self) -> None:
        """When _observations_out is supplied, a FINLEX_INLINE_REPEAL_STUB obs is emitted."""
        subsec = _make_subsection_with_stub()
        observations: list[dict] = []
        semantic_structure_from_oracle(subsec, _observations_out=observations)
        assert len(observations) == 1
        obs = observations[0]
        assert obs["kind"] == "FINLEX_INLINE_REPEAL_STUB"
        assert obs["target_range"] == [2]
        assert obs["amendment_id"] == "2021/1030"

    def test_no_observations_without_out_param(self) -> None:
        """Without _observations_out, projection still works (stubs silently stripped)."""
        subsec = _make_subsection_with_stub()
        result = semantic_structure_from_oracle(subsec)
        assert result is not None
        assert len(result.children) == 2

    def test_clean_subsection_unaffected(self) -> None:
        """A subsection with no stubs is projected normally."""
        subsec = etree.fromstring("""
        <subsection eId="sec_1__subsec_1">
          <paragraph eId="sec_1__subsec_1__para_1">
            <num>1)</num>
            <content><p>ensimmäinen kohta</p></content>
          </paragraph>
          <paragraph eId="sec_1__subsec_1__para_2">
            <num>2)</num>
            <content><p>toinen kohta</p></content>
          </paragraph>
        </subsection>
        """)
        observations: list[dict] = []
        result = semantic_structure_from_oracle(subsec, _observations_out=observations)
        assert result is not None
        assert len(result.children) == 2
        assert observations == []


# ---------------------------------------------------------------------------
# Integration: 2013/331 § 3 against fin@20211030
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not __import__("pathlib").Path("data/finlex.farchive").exists(),
    reason="Requires data/finlex.farchive",
)
class TestIntegration2013331:
    def test_stub_stripped_from_oracle_tree(self) -> None:
        """para_2v20211030 must be stripped from the projected children.

        The @20211030 oracle for 2013/331 § 3 / subsection 1 contains:
        - para_2v20211030: the inline repeal stub for '2 kohta' (kumottu)
        - para_2: the unnumbered exclusion-list peer (a separate known defect, T1/T7)

        T6's job is exclusively to strip the repeal stub.  The unnumbered peer
        (para_2) still produces an ordinal_fallback '2' child — that is a known
        issue tracked separately in FINLAND_PROFILE_ONTOLOGY_GAPS_2026-04-15.md
        §1.1 and is NOT addressed by this task.

        This test confirms:
        1. The raw XML contains para_2v20211030 (our target).
        2. After projection with _observations_out, a FINLEX_INLINE_REPEAL_STUB
           observation is emitted for amendment 2021/1030, target kohta 2.
        3. No child with label_basis='editorial_repeal_notice' appears (i.e. the
           stub did not leak through as a semantic item bearing that label basis).
        """
        from lawvm.corpus_store import get_corpus_store
        from lawvm.finland.corpus import get_ground_truth_tree
        from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
        from lawvm.tools.section_keys import extract_oracle_sections

        cs = get_corpus_store()
        selector = ConsolidatedArtifactSelector.latest_cached_editorial()
        root = get_ground_truth_tree("2013/331", corpus=cs, selector=selector)
        assert root is not None, "ground truth tree not found for 2013/331"
        sections = extract_oracle_sections(root)
        sec3 = sections.get("chapter:1/section:3")
        assert sec3 is not None, "section:3 not found in oracle"

        # Find subsection 1
        subsec1 = None
        for child in sec3:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "subsection":
                subsec1 = child
                break
        assert subsec1 is not None, "subsection 1 not found in section 3"

        # Confirm the raw XML has the target stub
        raw_eids = [child.get("eId", "") for child in list(subsec1)]
        assert any("para_2v20211030" in e for e in raw_eids), (
            "para_2v20211030 not found in raw oracle XML — test data may have changed"
        )

        observations: list[dict] = []
        projected = semantic_structure_from_oracle(subsec1, _observations_out=observations)
        assert projected is not None

        # At least one FINLEX_INLINE_REPEAL_STUB observation emitted for 2021/1030
        stub_obs = [o for o in observations if o["kind"] == "FINLEX_INLINE_REPEAL_STUB"]
        assert len(stub_obs) >= 1, (
            "Expected at least one FINLEX_INLINE_REPEAL_STUB observation, got none"
        )
        assert stub_obs[0]["amendment_id"] == "2021/1030"
        assert 2 in stub_obs[0]["target_range"]

        # The stub must not have leaked through as an editorial_repeal_notice child
        # (that would mean we projected it rather than stripped it)
        leaked = [
            c for c in projected.children
            if c.label_basis == "editorial_repeal_notice"
        ]
        assert leaked == [], (
            f"Repeal stub leaked into projection as editorial_repeal_notice: "
            f"{[(c.label, c.label_basis) for c in leaked]}"
        )

        # The stub eId text must not appear in any projected child's text
        for c in projected.children:
            assert "on kumottu" not in (c.text or ""), (
                f"Repeal stub text found in projected child {c.label!r}: {c.text!r}"
            )
