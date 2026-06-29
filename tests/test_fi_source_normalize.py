"""Tests for the explicit source normalization phase.

Verifies that normalize_source_ir:
  1. Detects and corrects item-style subsection pathology (TAG_RECLASSIFY),
     emitting a SourceNormalizationFact witness.
  2. Detects and removes editorial block children (EDITORIAL_STRIP),
     emitting a SourceNormalizationFact witness.
  3. Normalizes whitespace in text content (WHITESPACE).
  4. Detects numbering anomalies -- gaps (NUMBERING_REPAIR) and
     duplicates (DUPLICATE_DROP) -- among sibling items.
  5. Supports shape-driven sparse payload repairs.
  6. Leaves unaffected nodes untouched (no facts emitted, same node returned).
  7. Handles nested pathologies by returning one fact per corrected node.
"""

from __future__ import annotations

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.tree_ops import check_invariants
from lawvm.core.semantic_types import (
    IRNodeKind,
    SourceNormalizationBasis,
    SourceNormalizationKind,
)
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.helpers import _fi_label_postprocessor, _norm_num_token
from lawvm.finland.source_normalize import (
    normalize_source_ir,
    source_normalization_fact_finding_kind,
)
from lawvm.finland.source_normalization_kinds import (
    BASE_DIGIT_RESET_SPLIT,
    BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION,
    BASE_INTRO_LIST_RESTART_SPLIT,
    BASE_DUPLICATE_SIBLING_DROP,
    BASE_DUPLICATE_TAIL_SPLIT,
    BASE_HEADING_BODY_SUBSECTION_SPLIT,
    BASE_INTRO_LIST_TAIL_MOMENT_SPLIT,
    BASE_TREATY_PROTOCOL_MOMENT_SPLIT,
    BASE_SECTION_ITEM_SUBSECTION_FOLD,
    BASE_TABLE_NOTE_SUBSECTION_FOLD,
    BASE_TABLE_CONTINUATION_SUBSECTION_MERGE,
    BASE_TABLE_CONTINUATION_HEADER_REPAIR,
    BASE_TAIL_PROSE_ABSORB,
    BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT,
    TRAILING_CHAPTER_REPARENT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_source_normalization_fact_finding_kind_resolves_registered_base_codes() -> None:
    assert source_normalization_fact_finding_kind("tail_prose_absorb") == "BASE_TAIL_PROSE_ABSORB"
    assert source_normalization_fact_finding_kind("base_tail_prose_absorb") == "BASE_TAIL_PROSE_ABSORB"
    assert (
        source_normalization_fact_finding_kind("base_dotted_paragraph_subsection_promotion")
        == "BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION"
    )
    assert (
        source_normalization_fact_finding_kind("base_intro_list_tail_moment_split")
        == "BASE_INTRO_LIST_TAIL_MOMENT_SPLIT"
    )
    assert (
        source_normalization_fact_finding_kind("base_treaty_protocol_moment_split")
        == "BASE_TREATY_PROTOCOL_MOMENT_SPLIT"
    )
    assert (
        source_normalization_fact_finding_kind("base_table_continuation_subsection_merge")
        == "BASE_TABLE_CONTINUATION_SUBSECTION_MERGE"
    )
    assert (
        source_normalization_fact_finding_kind("base_table_continuation_header_repair")
        == "BASE_TABLE_CONTINUATION_HEADER_REPAIR"
    )
    assert source_normalization_fact_finding_kind("") is None
    assert source_normalization_fact_finding_kind("not_registered") is None


def _subsection_xml_with_item_num(label: str = "9", letter_children: int = 3) -> etree._Element:
    """Build a <subsection> element with item-style num and letter-labeled paragraphs."""
    letters = "abcdefghij"[:letter_children]
    para_xml = "\n".join(
        f"""<paragraph><num>{ch})</num><content><p>text {ch}</p></content></paragraph>"""
        for ch in letters
    )
    return etree.fromstring(
        f"""
        <subsection>
          <num>{label})</num>
          <intro><p>Definition list:</p></intro>
          {para_xml}
        </subsection>
        """
    )


def _content_node_with_image_block() -> IRNode:
    """Build an IRNode content with an image-block child (as xml_ir.py produces it)."""
    return IRNode(
        kind=IRNodeKind.CONTENT,
        text="some legal text",
        children=(
            IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "image"}),
        ),
    )


def _plain_subsection_node() -> IRNode:
    """Build a plain, non-pathological subsection IRNode."""
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.CONTENT, text="Normaali momentti."),
        ),
    )


# ---------------------------------------------------------------------------
# TAG_RECLASSIFY: item-style subsection -> paragraph
# ---------------------------------------------------------------------------


class TestTagReclassify:
    def test_splits_treaty_protocol_paragraph_with_explicit_second_moment_reference(self) -> None:
        """A treaty protocol source paragraph may encode two self-referenced momentit."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="1",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="1 §"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text=(
                                "Portossa 2 päivänä toukokuuta 1992 Suomen tasavallan, Islannin "
                                "tasavallan, Itävallan tasavallan, Liechtensteinin ruhtinaskunnan, "
                                "Norjan kuningaskunnan, Ruotsin kuningaskunnan ja Sveitsin "
                                "valaliiton välillä valvontaviranomaisen ja tuomioistuimen "
                                "perustamisesta tehdyn EFTA-valtioiden sopimuksen (sopimus) sekä "
                                "sen pöytäkirjojen määräykset ovat, mikäli ne kuuluvat "
                                "lainsäädännön alaan, voimassa siten tarkistettuina kuin siitä on "
                                "sovittu 2 momentissa tarkoitetulla tarkistuspöytäkirjalla. "
                                "Brysselissä 17 päivänä maaliskuuta 1993 Suomen tasavallan, Islannin "
                                "tasavallan, Itävallan tasavallan, Liechtensteinin ruhtinaskunnan, "
                                "Norjan kuningaskunnan ja Ruotsin kuningaskunnan välisen "
                                "valvontaviranomaisen ja tuomioistuimen perustamisesta tehdyn "
                                "EFTA-valtioiden sopimuksen tarkistamista koskevan pöytäkirjan "
                                "määräykset ovat, mikäli ne kuuluvat lainsäädännön alaan, voimassa "
                                "niin kuin siitä on sovittu."
                            ),
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "1993/1508")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2"]
        assert "2 momentissa tarkoitetulla tarkistuspöytäkirjalla." in irnode_to_text(subsections[0])
        assert irnode_to_text(subsections[1]).startswith("Brysselissä 17 päivänä maaliskuuta 1993")
        assert check_invariants(normalized) == []

        split_facts = [
            fact for fact in facts if fact.kind_value == BASE_TREATY_PROTOCOL_MOMENT_SPLIT
        ]
        assert len(split_facts) == 1
        assert split_facts[0].basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "subsection:1" in split_facts[0].path

    def test_treaty_protocol_split_requires_explicit_second_moment_reference(self) -> None:
        """Ordinary two-sentence treaty prose is not split without the typed cue."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="1",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text=(
                                "Sopimuksen pöytäkirjojen määräykset ovat voimassa. "
                                "Brysselissä allekirjoitetun pöytäkirjan määräykset ovat voimassa."
                            ),
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "2020/1")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1"]
        assert not any(fact.kind_value == BASE_TREATY_PROTOCOL_MOMENT_SPLIT for fact in facts)

    def test_splits_intro_list_tail_moments_into_peer_subsections(self) -> None:
        """Multiple explicit first-moment tail prose children are later momentit."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="11",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="11 §"),
                IRNode(kind=IRNodeKind.HEADING, text="Luoton enimmäismäärä"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="Luoton määrä saa olla:"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="ensimmäinen kohta;"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="toinen kohta."),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="Edellä 1 momentissa tarkoitetun luoton määrä saa olla enintään 90 prosenttia.",
                        ),
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="Finanssivalvonta voi alentaa 2 momentissa säädettyjä enimmäismääriä.",
                        ),
                        IRNode(
                            kind=IRNodeKind.WRAP_UP,
                            text="Päätös on voimassa enintään vuoden kerrallaan.",
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "2014/610")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2", "3", "4"]
        assert [child.kind for child in subsections[0].children] == [
            IRNodeKind.INTRO,
            IRNodeKind.PARAGRAPH,
            IRNodeKind.PARAGRAPH,
        ]
        assert "tarkoitetun luoton määrä" in irnode_to_text(subsections[1])
        assert "alentaa 2 momentissa" in irnode_to_text(subsections[2])
        assert check_invariants(normalized) == []

        split_facts = [
            fact for fact in facts if fact.kind_value == BASE_INTRO_LIST_TAIL_MOMENT_SPLIT
        ]
        assert len(split_facts) == 1
        assert split_facts[0].basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "subsection:1" in split_facts[0].path

    def test_splits_conditional_intro_list_tail_moments_into_peer_subsections(self) -> None:
        """A conditional first tail plus another prose tail is peer momentti content."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="10",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.INTRO,
                            text="Viranomainen valvoo velvollisuutta. Tässä tarkoituksessa se:",
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="tarkastaa ilmoitukset;"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="pyytää selvitykset."),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="Jos ilmoitusta ei tehdä, viranomainen voi asettaa uhkasakon.",
                        ),
                        IRNode(
                            kind=IRNodeKind.WRAP_UP,
                            text="Viranomaisen valvonta päättyy vuoden kuluttua.",
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "2009/273")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2", "3"]
        assert "Jos ilmoitusta ei tehdä" in irnode_to_text(subsections[1])
        assert "valvonta päättyy" in irnode_to_text(subsections[2])
        assert check_invariants(normalized) == []

        split_facts = [
            fact for fact in facts if fact.kind_value == BASE_INTRO_LIST_TAIL_MOMENT_SPLIT
        ]
        assert len(split_facts) == 1
        assert split_facts[0].basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "peer momentti subsections" in split_facts[0].explanation

    def test_keeps_single_generic_first_moment_tail_inside_intro_list(self) -> None:
        """A lone generic first-moment tail remains ordinary wrap-up prose."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="1",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="Verovapaita ovat:"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="vuokra-asunnot;"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="asumisoikeusasunnot."),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.WRAP_UP,
                            text="Edellä 1 momentissa tarkoitettu verovapaus koskee myös osakkeita.",
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "2000/1")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        assert not any(fact.kind_value == BASE_INTRO_LIST_TAIL_MOMENT_SPLIT for fact in facts)

    def test_reclassifies_item_style_subsection(self) -> None:
        """normalize_source_ir corrects <subsection num='9)'> with letter paragraphs."""
        raw = fi_xml_to_ir_node(_subsection_xml_with_item_num("9", 3), _fi_label_postprocessor)
        assert raw.kind == IRNodeKind.SUBSECTION, "fi_xml_to_ir_node must produce raw SUBSECTION"

        normalized, facts = normalize_source_ir(raw, "2002/672")

        assert normalized.kind == IRNodeKind.PARAGRAPH
        assert normalized.label == "9"

    def test_emits_tag_reclassify_fact(self) -> None:
        """A TAG_RECLASSIFY SourceNormalizationFact is emitted for each corrected node."""
        raw = fi_xml_to_ir_node(_subsection_xml_with_item_num("9", 2), _fi_label_postprocessor)
        _, facts = normalize_source_ir(raw, "1999/123")

        tag_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.TAG_RECLASSIFY.value]
        assert len(tag_facts) == 1

        fact = tag_facts[0]
        assert fact.statute_id == "1999/123"
        assert "9)" in fact.before
        assert "paragraph" in fact.after

    def test_reclassifies_letter_paragraph_children_to_subparagraph(self) -> None:
        """Letter-labeled paragraph children become subparagraph (alakohta)."""
        raw = fi_xml_to_ir_node(_subsection_xml_with_item_num("5", 3), _fi_label_postprocessor)
        normalized, _ = normalize_source_ir(raw, "2020/1")

        subparagraphs = [c for c in normalized.children if c.kind == IRNodeKind.SUBPARAGRAPH]
        assert [sp.label for sp in subparagraphs] == ["a", "b", "c"]

        paragraphs = [c for c in normalized.children if c.kind == IRNodeKind.PARAGRAPH]
        assert paragraphs == []

    def test_preserves_intro_node(self) -> None:
        """The intro child is preserved after reclassification."""
        raw = fi_xml_to_ir_node(_subsection_xml_with_item_num("3", 2), _fi_label_postprocessor)
        normalized, _ = normalize_source_ir(raw, "2020/1")

        intros = [c for c in normalized.children if c.kind == IRNodeKind.INTRO]
        assert len(intros) == 1
        assert "Definition list" in (intros[0].text or "")

    def test_no_reclassify_for_plain_subsection(self) -> None:
        """A normal subsection without item-style num produces no TAG_RECLASSIFY facts."""
        node = _plain_subsection_node()
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.kind == IRNodeKind.SUBSECTION
        tag_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.TAG_RECLASSIFY.value]
        assert tag_facts == []

    def test_no_reclassify_without_letter_paragraphs(self) -> None:
        """A subsection with item-style num but only digit-labeled paragraphs stays as subsection."""
        xml = etree.fromstring(
            """
            <subsection>
              <num>2)</num>
              <paragraph><num>1)</num><content><p>first</p></content></paragraph>
              <paragraph><num>2)</num><content><p>second</p></content></paragraph>
            </subsection>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)
        normalized, facts = normalize_source_ir(raw, "2020/1")

        assert normalized.kind == IRNodeKind.SUBSECTION
        tag_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.TAG_RECLASSIFY.value]
        assert tag_facts == []

    def test_keeps_section_scoped_item_style_subsection_as_subsection(self) -> None:
        """A standalone section-scoped item-style subsection remains a visible suspicious shape."""
        xml = etree.fromstring(
            """
            <section>
              <num>1 §</num>
              <subsection>
                <num>9)</num>
                <intro><p>Tässä momentissa tarkoitetaan:</p></intro>
                <paragraph><num>a)</num><content><p>ensimmäinen</p></content></paragraph>
                <paragraph><num>b)</num><content><p>toinen</p></content></paragraph>
              </subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)
        normalized, facts = normalize_source_ir(raw, "2002/672")

        assert normalized.kind == IRNodeKind.SECTION
        subsections = [c for c in normalized.children if c.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        assert subsections[0].label == "9"
        assert [c.label for c in normalized.children if c.kind == IRNodeKind.PARAGRAPH] == []
        assert check_invariants(normalized) == []

        tag_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.TAG_RECLASSIFY.value]
        assert tag_facts == []
        suspicious_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.SUSPICIOUS_SHAPE.value]
        assert len(suspicious_facts) == 1
        fact = suspicious_facts[0]
        assert fact.basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "section-scoped subsection" in fact.before
        assert "illegal section -> paragraph edge" in fact.after

    def test_folds_section_scoped_item_style_continuation_into_previous_subsection(self) -> None:
        """A malformed sibling subsection that continues kohdat is folded into the prior momentti."""
        xml = etree.fromstring(
            """
            <section>
              <num>107 §</num>
              <subsection>
                <intro><p>Lain 108-110 §:ää ei kuitenkaan sovelleta:</p></intro>
                <paragraph><num>1)</num><content><p>ensimmäinen kohta;</p></content></paragraph>
              </subsection>
              <subsection>
                <num>2)</num>
                <intro><p>laitokseen, jossa poltetaan seuraavia jätteitä:</p></intro>
                <paragraph><num>a)</num><content><p>maa- ja metsätalousjäte;</p></content></paragraph>
                <paragraph><num>b)</num><content><p>elintarviketeollisuuden jäte;</p></content></paragraph>
                <paragraph><num>3)</num><content><p>koelaitos.</p></content></paragraph>
              </subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2014/527")

        assert normalized.kind == IRNodeKind.SECTION
        subsections = [c for c in normalized.children if c.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        paragraphs = [c for c in subsections[0].children if c.kind == IRNodeKind.PARAGRAPH]
        assert [p.label for p in paragraphs] == ["1", "2", "3"]
        para2 = paragraphs[1]
        assert [c.label for c in para2.children if c.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b"]
        assert check_invariants(normalized) == []

        tag_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.TAG_RECLASSIFY.value]
        assert len(tag_facts) == 1
        assert tag_facts[0].basis_value == SourceNormalizationBasis.IMPOSSIBLE_NUMBERING.value
        assert "section-scoped subsection continuation" in tag_facts[0].before
        assert "folded into previous subsection" in tag_facts[0].after

    def test_folds_multi_subsection_item_run_and_relabels_true_moment(self) -> None:
        """A first-moment item list split across subsection siblings is one momentti."""
        xml = etree.fromstring(
            """
            <section>
              <num>2 §</num>
              <subsection>
                <intro><p>Eläkeajaksi luetaan:</p></intro>
                <paragraph><num>1)</num><content><p>ensimmäinen kohta.</p></content></paragraph>
              </subsection>
              <subsection>
                <num>2)</num>
                <intro><p>toinen kohta alkaa,</p></intro>
                <paragraph><content><p>toisen kohdan jatko;</p></content></paragraph>
                <paragraph><num>3)</num><content><p>kolmas kohta alkaa.</p></content></paragraph>
              </subsection>
              <subsection>
                <intro><p>kolmannen kohdan jatko, sekä</p></intro>
                <paragraph><content><p>lisäjatko.</p></content></paragraph>
                <paragraph><num>4)</num><content><p>neljäs kohta.</p></content></paragraph>
                <paragraph><num>5)</num><content><p>viides kohta.</p></content></paragraph>
              </subsection>
              <subsection>
                <content><p>Todellinen toinen momentti.</p></content>
              </subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1966/612")

        subsections = [c for c in normalized.children if c.kind == IRNodeKind.SUBSECTION]
        assert [sub.label for sub in subsections] == ["1", "2"]
        paragraphs = [c for c in subsections[0].children if c.kind == IRNodeKind.PARAGRAPH]
        assert [para.label for para in paragraphs] == ["1", "2", "3", "4", "5"]
        assert "kolmannen kohdan jatko" in " ".join(
            gc.text or ""
            for gc in paragraphs[2].children
            if gc.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT)
        )
        assert check_invariants(normalized) == []

        fold_facts = [f for f in facts if f.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD]
        assert len(fold_facts) == 1
        assert fold_facts[0].basis_value == SourceNormalizationBasis.IMPOSSIBLE_NUMBERING.value
        assert "subsection:2" in fold_facts[0].before
        assert "3->2" in fold_facts[0].after

    def test_folds_dash_bullet_definition_continuation_subsection(self) -> None:
        """Dash-list definition continuations stay under the preceding numbered kohta."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="2",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="2 §"),
                IRNode(kind=IRNodeKind.HEADING, text="Määritelmät"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="Tässä päätöksessä tarkoitetaan:"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="PCB:llä"),
                                IRNode(kind=IRNodeKind.CONTENT, text="- polykloorattuja bifenyylejä;"),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="- polykloorattuja terfenyylejä;"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="- monometyylitetraklooridifenyylimetaania; sekä"),),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="- seosta, jossa jotakin edellä mainittua ainetta on yli 0,005 prosenttia;"),),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="PCB-laitteistolla muuntajaa;"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="3",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="3)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="PCB-jätteellä jätettä;"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="4",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="4)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="käsittelyllä hyödyntämistä."),
                            ),
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "1998/711")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3", "4"]
        assert "monometyylitetraklooridifenyylimetaania" in irnode_to_text(paragraphs[0])
        assert "PCB-jätteellä" in irnode_to_text(paragraphs[2])
        assert check_invariants(normalized) == []

        fold_facts = [fact for fact in facts if fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD]
        assert len(fold_facts) == 1
        assert fold_facts[0].basis_value == SourceNormalizationBasis.IMPOSSIBLE_NUMBERING.value

    def test_folds_connector_wrapper_before_next_section_item_carrier(self) -> None:
        """Connector-only wrappers may belong to a split section-level item run."""
        xml = etree.fromstring(
            """
            <section>
              <num>2 §</num>
              <subsection>
                <intro><p>Luettelo:</p></intro>
                <paragraph><num>1)</num><content><p>ensimmäinen kohta;</p></content></paragraph>
                <paragraph><num>2)</num><content><p>toinen kohta;</p></content></paragraph>
              </subsection>
              <subsection><content><p>sekä</p></content></subsection>
              <subsection>
                <paragraph><num>3)</num><content><p>kolmas kohta.</p></content></paragraph>
              </subsection>
              <subsection><content><p>Todellinen toinen momentti.</p></content></subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1978/380")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3"]
        assert "toinen kohta; sekä" in irnode_to_text(paragraphs[1])
        assert "Todellinen toinen momentti" in irnode_to_text(subsections[1])
        assert check_invariants(normalized) == []

        fold_facts = [fact for fact in facts if fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD]
        assert len(fold_facts) == 1
        assert "subsection:2" in fold_facts[0].before
        assert "subsection:3" in fold_facts[0].before

    def test_splits_glued_coordinator_next_item_inside_paragraph_content(self) -> None:
        """A source typo like ``; seka5)`` starts the next numbered item."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="51",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="51 §"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="Hakemukseen on liitettävä:"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="4",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="4)"),
                                IRNode(
                                    kind=IRNodeKind.CONTENT,
                                    text="pääpiirustukset; seka5) rakentamista koskevat tiedot.",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "1980/687")

        subsection = next(child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION)
        paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["4", "5"]
        assert irnode_to_text(paragraphs[0]).endswith("pääpiirustukset;")
        assert irnode_to_text(paragraphs[1]) == "5) rakentamista koskevat tiedot."
        split_facts = [fact for fact in facts if fact.kind_value == BASE_DIGIT_RESET_SPLIT]
        assert len(split_facts) == 1
        assert split_facts[0].basis_value == SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR.value

    def test_real_1980_687_section_51_splits_glued_seka5_item(self) -> None:
        """Regression: 1980/687 section 51 glues item 5 into item 4 text."""
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("1980/687")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        body = root.find(".//{*}body")
        raw = fi_xml_to_ir_node(body if body is not None else root, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1980/687")

        section_51: IRNode | None = None
        pending = list(normalized.children)
        while pending:
            candidate = pending.pop()
            if candidate.kind == IRNodeKind.SECTION and candidate.label == "51":
                section_51 = candidate
                break
            pending.extend(candidate.children)
        assert section_51 is not None
        subsection = next(child for child in section_51.children if child.kind == IRNodeKind.SUBSECTION)
        paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["4", "5"]
        assert "sadevesiensekä salaojavesien pois johtamisesta;" in irnode_to_text(paragraphs[0])
        assert "rakentamista, rakennuksia ja huoneistoja koskevat tiedot" in irnode_to_text(paragraphs[1])
        assert any(fact.kind_value == BASE_DIGIT_RESET_SPLIT for fact in facts)

    def test_preserves_connector_wrapper_without_next_section_item_carrier(self) -> None:
        """A connector word alone is not enough to collapse a peer moment."""
        xml = etree.fromstring(
            """
            <section>
              <num>2 §</num>
              <subsection>
                <intro><p>Luettelo:</p></intro>
                <paragraph><num>1)</num><content><p>ensimmäinen kohta;</p></content></paragraph>
                <paragraph><num>2)</num><content><p>toinen kohta;</p></content></paragraph>
              </subsection>
              <subsection><content><p>sekä</p></content></subsection>
              <subsection><content><p>Todellinen kolmas momentti.</p></content></subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1978/380")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2", "3"]
        assert not any(fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD for fact in facts)

    def test_real_1978_380_section_2_folds_connector_split_item_tail(self) -> None:
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("1978/380")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "2"
        )
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1978/380")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3"]
        section_text = irnode_to_text(subsections[0])
        assert "ristin sakaran leveys 3 mittayksikköä; sekä" in section_text
        assert "kenttien korkeus 4" in section_text
        assert any(fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD for fact in facts)

    def test_folds_content_item_run_with_intervening_continuation_subsection(self) -> None:
        """A content-only continuation wrapper can belong to the previous kohta."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="3",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="3 §"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="ensimmäinen määritelmä."),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="Ensimmäisen kohdan jatkolause."),),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="2) toinen määritelmä."),),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="3) kolmas määritelmä."),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "content-item-continuation-fixture")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3"]
        assert "Ensimmäisen kohdan jatkolause" in irnode_to_text(paragraphs[0])
        assert check_invariants(normalized) == []
        fold_facts = [fact for fact in facts if fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD]
        assert len(fold_facts) == 1
        assert "subsection:2" in fold_facts[0].before
        assert "subsection:3" in fold_facts[0].before

    def test_real_1994_1505_section_3_folds_definition_item_wrappers(self) -> None:
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("1994/1505")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "3"
        )
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1994/1505")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3", "4", "5", "6"]
        section_text = irnode_to_text(normalized)
        assert "Edellä 1 momentissa tarkoitetun laitteen" in section_text
        assert "6) Käyttöönottamisella" in section_text
        assert check_invariants(normalized) == []
        assert any(fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD for fact in facts)

    def test_real_2000_345_section_3_folds_sparse_definition_item_payload(self) -> None:
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("2000/345")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "3"
        )
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2000/345")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1a", "5", "6", "7"]
        section_text = irnode_to_text(normalized)
        assert "In vitro -diagnostiikkaan tarkoitetulla" in section_text
        assert "Valtuutetulla edustajalla tarkoitetaan" in section_text
        assert check_invariants(normalized) == []
        assert any(
            fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD
            and "sparse section-level item carriers" in fact.before
            for fact in facts
        )

    def test_folds_intro_only_subsection_followed_by_paragraph_item_wrapper(self) -> None:
        """Intro-only first moment plus sibling 1..N paragraph wrapper is one momentti."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="4",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="4 §"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="Rahoitustuen edellytyksenä on,"),),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="ensimmäinen kohta,"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="toinen kohta."),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="Todellinen toinen momentti."),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "intro-item-wrapper-fixture")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2"]
        assert "Rahoitustuen edellytyksenä on" in irnode_to_text(subsections[0])
        assert "Todellinen toinen momentti" in irnode_to_text(subsections[1])
        assert check_invariants(normalized) == []

        fold_facts = [fact for fact in facts if fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD]
        assert len(fold_facts) == 1
        assert "intro-only subsection" in fold_facts[0].before
        assert "3->2" in fold_facts[0].after

    def test_real_1974_1086_section_4_folds_intro_and_item_wrapper(self) -> None:
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("1974/1086")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "4"
        )
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1974/1086")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3"]
        assert "Rahoitus tuen myöntämisen edellytyksenä on" in irnode_to_text(subsections[0])
        assert "Asetuksella annetaan tarkempia säännöksiä" in irnode_to_text(subsections[1])
        assert any(
            fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD
            and "intro-only subsection" in fact.before
            for fact in facts
        )

    def test_real_2001_189_section_3_folds_chaptered_intro_item_wrapper(self) -> None:
        """A chaptered section can still carry a transport-split moment item list.

        Provenance: 2001/189 §3. The source XML encodes the second moment lead
        sentence as one subsection and its 1..3 item list as the following
        subsection. If left as peer moments, later replacements of the second
        moment cannot retire the old item list.
        """
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("2001/189")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "3"
        )
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2001/189")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2", "3", "4"]
        paragraphs = [child for child in subsections[1].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3"]
        assert "työmarkkinatukea vähintään 500 päivältä" in irnode_to_text(subsections[1])
        assert "Tämän lain soveltamisesta" in irnode_to_text(subsections[2])
        assert check_invariants(normalized) == []
        assert any(
            fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD
            and "paragraph item wrapper subsection:3" in fact.before
            for fact in facts
        )

    def test_real_2005_1266_section_8_folds_duplicate_one_item_wrapper(self) -> None:
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("2005/1266")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "8"
        )
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2005/1266")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3", "4", "5"]
        assert "musiikkialan perustutkintoon" in irnode_to_text(paragraphs[0])
        assert "kaikilla aloilla erityisopetuksessa" in irnode_to_text(paragraphs[4])
        assert any(child.kind == IRNodeKind.OMISSION for child in normalized.children)
        assert check_invariants(normalized) == []
        assert any(
            fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD
            and "paragraph item wrapper subsection:1" in fact.before
            for fact in facts
        )

    def test_folds_unlabelled_paragraph_list_wrapper_into_comma_ended_subsection(self) -> None:
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="2",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="2 §"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    children=(
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="Eläkeajaksi luetaan siltä osin kuin sitä ei ole luettava hyväksi muuta varten,",
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    children=(
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="a",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="a)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="ensimmäinen kohta; ja"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="b",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="b)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="toinen kohta."),
                            ),
                        ),
                        IRNode(kind=IRNodeKind.OMISSION),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "1981/68-fixture")

        assert [child.kind for child in normalized.children] == [
            IRNodeKind.NUM,
            IRNodeKind.SUBSECTION,
            IRNodeKind.OMISSION,
        ]
        subsection = normalized.children[1]
        paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["a", "b"]
        assert "ensimmäinen kohta" in irnode_to_text(subsection)
        assert check_invariants(normalized) == []
        fold_facts = [fact for fact in facts if fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD]
        assert len(fold_facts) == 1
        assert fold_facts[0].basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "unlabelled paragraph-list wrapper" in fold_facts[0].before

    def test_does_not_fold_unlabelled_paragraph_list_after_closed_subsection(self) -> None:
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="2",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="Ensimmäinen momentti päättyy."),),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    children=(
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="a",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="a)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="itsenäinen listakohta;"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="b",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="b)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="toinen listakohta."),
                            ),
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "closed-subsection-fixture")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 2
        assert not any(fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD for fact in facts)

    def test_real_1981_68_section_2_folds_split_first_moment_item_list(self) -> None:
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("1981/68")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "2"
        )
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1981/68")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["a", "b"]
        assert "puoleksi heinäkuun 1 päivään 1962" in irnode_to_text(subsections[0])
        assert any(
            fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD
            and "unlabelled paragraph-list wrapper" in fact.before
            for fact in facts
        )

    def test_real_1995_361_section_4_preserves_nested_lettered_definition_order(self) -> None:
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("1995/361")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "4"
        )
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1995/361")

        text = irnode_to_text(normalized)
        assert text.index("1) elintarvikemääräyksillä") < text.index(
            "(a) elintarviketta, jonka tiedetään"
        )
        assert text.index("(a) elintarviketta, jonka tiedetään") < text.index(
            "(b) elintarviketta, joka pilaantumisen"
        )
        assert text.index("(b) elintarviketta, joka pilaantumisen") < text.index(
            "4) kuluttajalla"
        )
        assert not any(fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD for fact in facts)
        assert not any(fact.kind_value == BASE_TAIL_PROSE_ABSORB for fact in facts)

    def test_real_1998_711_dash_definition_list_preserves_all_items(self) -> None:
        """Regression: 1998/711 section 2 is a single definition-list moment."""
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("1998/711")
        assert xml is not None
        xml_bytes: bytes = xml if isinstance(xml, bytes) else xml.encode("utf-8")
        root = etree.fromstring(xml_bytes)
        body = root.find(".//{*}body")
        raw = fi_xml_to_ir_node(body if body is not None else root, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1998/711")

        section_2: IRNode | None = None
        pending = list(normalized.children)
        while pending:
            candidate = pending.pop()
            if candidate.kind == IRNodeKind.SECTION and candidate.label == "2":
                section_2 = candidate
                break
            pending.extend(candidate.children)
        assert section_2 is not None
        subsections = [child for child in section_2.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3", "4"]
        section_text = irnode_to_text(section_2)
        assert "polykloorattuja terfenyylejä" in section_text
        assert "PCB-jätteellä PCB:tä" in section_text
        assert "käsittelyllä jäteasetuksen" in section_text

        assert any(fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD for fact in facts)
        assert not any(
            fact.kind_value == BASE_DUPLICATE_SIBLING_DROP
            and fact.path[-1] in {"subsection:1", "subsection:2"}
            for fact in facts
        )

    def test_folds_synthetic_table_note_subsections_into_table_moment(self) -> None:
        """EId-less table-note wrappers continue the table-bearing moment."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="3",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="3 §"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="Moment begins:"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            children=(
                                IRNode(
                                    kind=IRNodeKind.CONTENT,
                                    children=(IRNode(kind=IRNodeKind.TABLE, text="table body"),),
                                ),
                            ),
                        ),
                        IRNode(kind=IRNodeKind.OMISSION),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="(*) first table note."),),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="3",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="(**) listed note:"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="- first bullet"),),
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "table-note-fixture")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        assert "(*) first table note." in irnode_to_text(subsections[0])
        assert "- first bullet" in irnode_to_text(subsections[0])
        assert any(fact.kind_value == BASE_TABLE_NOTE_SUBSECTION_FOLD for fact in facts)

    def test_table_note_fold_preserves_real_eid_subsection(self) -> None:
        """A real following moment with source eId is not a table-note wrapper."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="3",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            children=(
                                IRNode(
                                    kind=IRNodeKind.CONTENT,
                                    children=(IRNode(kind=IRNodeKind.TABLE, text="table body"),),
                                ),
                            ),
                        ),
                        IRNode(kind=IRNodeKind.OMISSION),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    attrs={"eId": "sec_3__subsec_2"},
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="Real second moment."),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "real-moment-fixture")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2"]
        assert not any(fact.kind_value == BASE_TABLE_NOTE_SUBSECTION_FOLD for fact in facts)

    def test_folds_numeric_table_note_subsections_with_source_eids(self) -> None:
        """Numeric note wrappers with source eIds may still be table footnotes."""
        table = IRNode(
            kind=IRNodeKind.TABLE,
            children=(
                IRNode(
                    kind=IRNodeKind.ROW,
                    children=(
                        IRNode(kind=IRNodeKind.CELL, text="Aine"),
                        IRNode(kind=IRNodeKind.CELL, text="50 1)"),
                        IRNode(kind=IRNodeKind.CELL, text="8 tuntia 2)"),
                    ),
                ),
            ),
        )
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="3",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    attrs={"eId": "sec_3__subsec_1"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="Raja-arvot ovat seuraavat:",
                            children=(table,),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    attrs={"eId": "sec_3__subsec_2"},
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="1) Tulokset ilmaistaan lämpötilassa."),),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="3",
                    attrs={"eId": "sec_3__subsec_3"},
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="2) Kahdeksan tunnin keskiarvo."),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "numeric-table-note-fixture")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        pending = [subsections[0]]
        has_table = False
        while pending:
            candidate = pending.pop()
            if candidate.kind == IRNodeKind.TABLE:
                has_table = True
                break
            pending.extend(candidate.children)
        assert has_table
        assert "1) Tulokset ilmaistaan" in irnode_to_text(subsections[0])
        assert "2) Kahdeksan tunnin" in irnode_to_text(subsections[0])
        assert any(fact.kind_value == BASE_TABLE_NOTE_SUBSECTION_FOLD for fact in facts)
        assert not any(fact.kind_value == BASE_SECTION_ITEM_SUBSECTION_FOLD for fact in facts)

    def test_numeric_table_note_fold_requires_marker_in_table(self) -> None:
        """A numbered following moment is not a table note without a table marker."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="3",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="Raja-arvot ovat seuraavat:",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.TABLE,
                                    children=(IRNode(kind=IRNodeKind.ROW, children=(IRNode(kind=IRNodeKind.CELL, text="Aine"),)),),
                                ),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="1) Todellinen kohta."),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "numeric-table-note-negative")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2"]
        assert not any(fact.kind_value == BASE_TABLE_NOTE_SUBSECTION_FOLD for fact in facts)

    def test_table_note_fold_preserves_unmarked_following_prose_moment(self) -> None:
        """EId-less following prose is not a table-note run without a marker."""
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="6",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="Table-bearing moment:"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            children=(
                                IRNode(
                                    kind=IRNodeKind.CONTENT,
                                    children=(IRNode(kind=IRNodeKind.TABLE, text="table body"),),
                                ),
                            ),
                        ),
                        IRNode(kind=IRNodeKind.OMISSION),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="Tämän pykälän mukainen maksu koskee myös muita."),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "table-prose-fixture")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2"]
        assert "Tämän pykälän mukainen" in irnode_to_text(subsections[1])
        assert not any(fact.kind_value == BASE_TABLE_NOTE_SUBSECTION_FOLD for fact in facts)

    def test_real_2006_953_table_notes_stay_with_replaced_first_moment(self) -> None:
        """Regression: 2006/953 section 3 publishes table notes inside 3 § 1 mom."""
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("2006/953")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        body = root.find(".//{*}body")
        raw = fi_xml_to_ir_node(body if body is not None else root, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2006/953")

        section_3: IRNode | None = None
        pending = list(normalized.children)
        while pending:
            candidate = pending.pop()
            if candidate.kind == IRNodeKind.SECTION and candidate.label == "3":
                section_3 = candidate
                break
            pending.extend(candidate.children)
        assert section_3 is not None
        subsections = [child for child in section_3.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        section_text = irnode_to_text(section_3)
        assert "P el hitsausgeneraattoreilla" in section_text
        assert "Yksimoottorisiin ajoneuvonostureihin" in section_text
        assert "Sallittu äänitehotaso pyöristetään" in section_text
        assert any(fact.kind_value == BASE_TABLE_NOTE_SUBSECTION_FOLD for fact in facts)
        assert not any(fact.kind_value == BASE_DUPLICATE_TAIL_SPLIT for fact in facts)

    def test_real_2013_255_unmarked_table_following_moments_are_preserved(self) -> None:
        """Regression: 2013/255 section 6 has real prose moments after a table."""
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("2013/255")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        body = root.find(".//{*}body")
        raw = fi_xml_to_ir_node(body if body is not None else root, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2013/255")

        section_6: IRNode | None = None
        pending = list(normalized.children)
        while pending:
            candidate = pending.pop()
            if candidate.kind == IRNodeKind.SECTION and candidate.label == "6":
                section_6 = candidate
                break
            pending.extend(candidate.children)
        assert section_6 is not None
        subsections = [child for child in section_6.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) >= 3
        assert "Tämän pykälän mukainen" in irnode_to_text(subsections[1])
        assert "Markkinarakennetoimija-asetuksen" in irnode_to_text(subsections[2])
        assert not any(fact.kind_value == BASE_TABLE_NOTE_SUBSECTION_FOLD for fact in facts)

    def test_promotes_dotted_paragraph_rows_to_peer_subsections(self) -> None:
        """Old decision-style dotted rows are momentit, not kohdat."""
        xml = etree.fromstring(
            """
            <section eId="chp_3__sec_4">
              <num>4 §</num>
              <heading>Vaatimukset</heading>
              <subsection eId="chp_3__sec_4__subsec_1">
                <paragraph><num>1.</num><content><p>Ensimmäinen momentti.</p></content></paragraph>
                <paragraph><num>2.</num><content><p>Toinen momentti.</p></content></paragraph>
                <paragraph><num>3.</num><content><p>Kolmas momentti.</p></content></paragraph>
              </subsection>
              <subsection eId="chp_3__sec_4__subsec_2">
                <intro><p>4. Neljäs momentti sisältää luettelon:</p></intro>
                <paragraph><num>a)</num><content><p>ensimmäinen kohta;</p></content></paragraph>
                <paragraph><num>b)</num><content><p>toinen kohta.</p></content></paragraph>
              </subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "1990/1207")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2", "3", "4"]
        assert [irnode_to_text(subsection) for subsection in subsections[:3]] == [
            "1. Ensimmäinen momentti.",
            "2. Toinen momentti.",
            "3. Kolmas momentti.",
        ]
        assert [child.label for child in subsections[3].children if child.kind == IRNodeKind.PARAGRAPH] == [
            "a",
            "b",
        ]
        assert subsections[3].attrs["lawvm_source_subsection_eid"] == "chp_3__sec_4__subsec_2"
        assert check_invariants(normalized) == []

        dotted_facts = [
            fact for fact in facts if fact.kind_value == BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION
        ]
        assert len(dotted_facts) == 2
        assert dotted_facts[0].basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "dotted paragraph rows" in dotted_facts[0].before
        assert "peer subsections" in dotted_facts[0].after
        assert "dotted intro moment" in dotted_facts[1].before
        assert "subsection:4" in dotted_facts[1].after

    def test_dotted_paragraph_promotion_ignores_parenthesized_items(self) -> None:
        """Normal Finnish kohdat use parenthesized labels and must not be promoted."""
        xml = etree.fromstring(
            """
            <section eId="chp_1__sec_2">
              <num>2 §</num>
              <subsection eId="chp_1__sec_2__subsec_1">
                <intro><p>Momentissa säädetään:</p></intro>
                <paragraph><num>1)</num><content><p>ensimmäinen kohta;</p></content></paragraph>
                <paragraph><num>2)</num><content><p>toinen kohta.</p></content></paragraph>
              </subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2020/1")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2"]
        assert [
            fact for fact in facts if fact.kind_value == BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION
        ] == []

    def test_dotted_paragraph_promotion_can_be_disabled_for_amendment_payloads(self) -> None:
        """Amendment payload normalization must not infer base-only moment promotion."""
        xml = etree.fromstring(
            """
            <section eId="chp_3__sec_4">
              <num>4 §</num>
              <subsection eId="chp_3__sec_4__subsec_1">
                <paragraph><num>1.</num><content><p>Ensimmäinen rivi.</p></content></paragraph>
                <paragraph><num>2.</num><content><p>Toinen rivi.</p></content></paragraph>
              </subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(
            raw,
            "2005/354",
            allow_dotted_paragraph_subsection_promotion=False,
        )

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1"]
        paragraphs = [child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1.", "2."]
        assert [
            fact for fact in facts if fact.kind_value == BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION
        ] == []


class TestUnnumberedSubparagraphMomentSplit:
    def test_splits_closed_item_unnumbered_subparagraph_payload_into_peer_moment(self) -> None:
        """A closed item cannot own an unnumbered peer-moment payload."""
        xml = etree.fromstring(
            """
            <section>
              <num>1 §</num>
              <subsection>
                <intro><p>Lakia sovelletaan:</p></intro>
                <paragraph><num>1)</num><content><p>ensimmäinen kohta;</p></content></paragraph>
                <paragraph>
                  <num>2)</num>
                  <intro><p>toinen kohta päättyy.</p></intro>
                  <subparagraph><content><p>Uusi momentti sisältää luettelon:</p></content></subparagraph>
                  <subparagraph><num>1)</num><content><p>ensimmäinen uuden momentin kohta;</p></content></subparagraph>
                  <subparagraph><num>2)</num><content><p>toinen uuden momentin kohta; tai</p></content></subparagraph>
                </paragraph>
                <paragraph><num>3)</num><content><p>kolmas uuden momentin kohta.</p></content></paragraph>
              </subsection>
              <subsection><content><p>Vanha kolmas momentti.</p></content></subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2020/1")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2", "3"]
        assert [child.label for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH] == ["1", "2"]
        second_children = subsections[1].children
        assert second_children[0].kind == IRNodeKind.INTRO
        assert [child.label for child in second_children if child.kind == IRNodeKind.PARAGRAPH] == ["1", "2", "3"]
        assert "Vanha kolmas momentti" in irnode_to_text(subsections[2])

        split_facts = [
            fact for fact in facts if fact.kind_value == BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT
        ]
        assert len(split_facts) == 1
        assert split_facts[0].basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "peer subsection:2" in split_facts[0].after

    def test_preserves_open_item_subparagraph_intro_list(self) -> None:
        """A colon-introduced item may own its subparagraph list."""
        xml = etree.fromstring(
            """
            <section>
              <num>1 §</num>
              <subsection>
                <paragraph>
                  <num>2)</num>
                  <intro><p>kohta sisältää seuraavat alakohdat:</p></intro>
                  <subparagraph><content><p>Alakohtien johdanto:</p></content></subparagraph>
                  <subparagraph><num>1)</num><content><p>ensimmäinen alakohta;</p></content></subparagraph>
                  <subparagraph><num>2)</num><content><p>toinen alakohta.</p></content></subparagraph>
                </paragraph>
              </subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2020/1")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        para = next(child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH)
        assert len([child for child in para.children if child.kind == IRNodeKind.SUBPARAGRAPH]) == 3
        assert not any(fact.kind_value == BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT for fact in facts)

    def test_real_2019_1567_section_1_restores_misnested_etuyhteys_moment(self) -> None:
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("2019/1567")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "1"
        )
        assert section is not None
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2019/1567")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2", "3", "4", "5"]
        second_items = [child.label for child in subsections[1].children if child.kind == IRNodeKind.PARAGRAPH]
        assert second_items == ["1", "2"]
        third_items = [child.label for child in subsections[2].children if child.kind == IRNodeKind.PARAGRAPH]
        assert third_items == ["1", "2", "3", "4"]
        assert "Toinen henkilö on etuyhteydessä" in irnode_to_text(subsections[2])
        assert any(fact.kind_value == BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT for fact in facts)

    def test_real_2021_1177_section_8a_splits_tail_moment_from_item_3(self) -> None:
        from lawvm.corpus_store import get_corpus_store

        xml = get_corpus_store().read_source("2021/1177")
        assert xml is not None
        root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        section = next(
            candidate
            for candidate in root.findall(".//{*}section")
            if _norm_num_token("".join(candidate.findtext("{*}num") or "")) == "8a"
        )
        raw = fi_xml_to_ir_node(section, _fi_label_postprocessor)

        normalized, facts = normalize_source_ir(raw, "2021/1177")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["1", "2", "3", "4", "5", "6", "7"]
        fourth_item_3 = next(
            child for child in subsections[3].children if child.kind == IRNodeKind.PARAGRAPH and child.label == "3"
        )
        assert not any(child.kind == IRNodeKind.SUBPARAGRAPH for child in fourth_item_3.children)
        assert "Sovellettaessa 4 momenttia" in irnode_to_text(subsections[4])
        assert "Poiketen siitä" in irnode_to_text(subsections[5])
        assert any(fact.kind_value == BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT for fact in facts)


# ---------------------------------------------------------------------------
# EDITORIAL_STRIP: image blocks
# ---------------------------------------------------------------------------


class TestEditorialStrip:
    def test_strips_image_block_child(self) -> None:
        """normalize_source_ir removes BLOCK(name=image) children."""
        node = _content_node_with_image_block()
        normalized, facts = normalize_source_ir(node, "2020/1262")

        image_children = [c for c in normalized.children if c.kind == IRNodeKind.BLOCK and c.attrs.get("name") == "image"]
        assert image_children == []

    def test_emits_editorial_strip_fact(self) -> None:
        """An EDITORIAL_STRIP SourceNormalizationFact is emitted for each image block removed."""
        node = _content_node_with_image_block()
        _, facts = normalize_source_ir(node, "2020/1262")

        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1

        fact = strip_facts[0]
        assert fact.statute_id == "2020/1262"
        assert "image" in fact.before
        assert "(removed)" in fact.after

    def test_preserves_non_image_children(self) -> None:
        """Non-image children are preserved when an image block is stripped."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="text",
            children=(
                IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "image"}),
                IRNode(kind=IRNodeKind.P, text="legal text"),
            ),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 1
        assert normalized.children[0].kind == IRNodeKind.P
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1

    def test_no_strip_for_non_image_block(self) -> None:
        """A block without name='image' is not stripped."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="text",
            children=(
                IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "other"}),
            ),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 1
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert strip_facts == []


# ---------------------------------------------------------------------------
# CROSS_HEADING_HOIST: standalone sibling heading → structural heading facet
# ---------------------------------------------------------------------------


class TestCrossHeadingHoist:
    def test_hoists_cross_heading_sibling_into_following_section(self) -> None:
        node = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CROSS_HEADING, text="Kustannusten ja toiminnan seuraaminen"),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="4 §"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="T"),)),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(node, "1994/951")

        assert all(c.kind != IRNodeKind.CROSS_HEADING for c in normalized.children)
        sec = next(c for c in normalized.children if c.kind == IRNodeKind.SECTION and c.label == "4")
        headings = [c for c in sec.children if c.kind == IRNodeKind.HEADING]
        assert len(headings) == 1
        assert headings[0].text == "Kustannusten ja toiminnan seuraaminen"

        hoist_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.CROSS_HEADING_HOIST.value]
        assert len(hoist_facts) == 1
        assert hoist_facts[0].statute_id == "1994/951"


class TestTrailingChapterReparent:
    def test_reparents_trailing_root_chapter_under_preceding_part(self) -> None:
        node = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.CHAPTER, label="18"),),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="19"),
            ),
        )

        normalized, facts = normalize_source_ir(node, "2012/746")

        assert len(normalized.children) == 1
        assert normalized.children[0].kind == IRNodeKind.PART
        chapter_labels = [c.label for c in normalized.children[0].children if c.kind == IRNodeKind.CHAPTER]
        assert chapter_labels == ["18", "19"]

        reparent_facts = [f for f in facts if f.kind_value == TRAILING_CHAPTER_REPARENT]
        assert len(reparent_facts) == 1
        assert reparent_facts[0].basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "top-level chapter '19'" in reparent_facts[0].before
        assert "reparented under part 6" in reparent_facts[0].after

    def test_does_not_reparent_chapter_before_first_part(self) -> None:
        node = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="1"),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.CHAPTER, label="2"),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(node, "2020/1")

        assert [child.kind for child in normalized.children] == [IRNodeKind.CHAPTER, IRNodeKind.PART]
        assert not any(f.kind_value == TRAILING_CHAPTER_REPARENT for f in facts)


# ---------------------------------------------------------------------------
# Identity: unmodified nodes
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_plain_node_unchanged(self) -> None:
        """A node with no pathologies passes through unchanged; no facts emitted."""
        node = _plain_subsection_node()
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized is node  # unchanged -> same object
        assert facts == []

    def test_empty_body_node(self) -> None:
        """An empty body node passes through unchanged."""
        node = IRNode(kind=IRNodeKind.BODY, children=())
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized is node
        assert facts == []


# ---------------------------------------------------------------------------
# WHITESPACE normalization
# ---------------------------------------------------------------------------


class TestWhitespace:
    def test_collapses_multiple_spaces(self) -> None:
        """Multiple spaces in text are collapsed to a single space."""
        node = IRNode(kind=IRNodeKind.CONTENT, text="hello   world")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.text == "hello world"
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert len(ws_facts) == 1

    def test_collapses_newlines_and_tabs(self) -> None:
        """Newlines and tabs are collapsed to single space."""
        node = IRNode(kind=IRNodeKind.P, text="line one\n\n  line two\ttab")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.text == "line one line two tab"
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert len(ws_facts) == 1

    def test_strips_leading_trailing_whitespace(self) -> None:
        """Leading and trailing whitespace is stripped."""
        node = IRNode(kind=IRNodeKind.CONTENT, text="  leading and trailing  ")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.text == "leading and trailing"
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert len(ws_facts) == 1

    def test_no_fact_when_already_clean(self) -> None:
        """No whitespace fact is emitted when text is already normalized."""
        node = IRNode(kind=IRNodeKind.CONTENT, text="already clean text")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized is node
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert ws_facts == []

    def test_no_fact_for_empty_text(self) -> None:
        """No whitespace fact for nodes with empty text."""
        node = IRNode(kind=IRNodeKind.CONTENT, text="")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized is node
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert ws_facts == []

    def test_does_not_modify_labels(self) -> None:
        """Whitespace normalization does not modify node labels."""
        node = IRNode(kind=IRNodeKind.PARAGRAPH, label="3 a", text="clean text")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.label == "3 a"
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert ws_facts == []


# ---------------------------------------------------------------------------
# NUMBERING anomaly detection: gaps and duplicates
# ---------------------------------------------------------------------------


class TestNumberingAnomalies:
    def test_detects_gap_in_sibling_numbering(self) -> None:
        """A gap (1, 2, 4, 5) emits a NUMBERING_REPAIR fact."""
        children = tuple(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label=str(n),
                children=(
                    IRNode(kind=IRNodeKind.NUM, text=f"{n})"),
                    IRNode(kind=IRNodeKind.CONTENT, text=f"text {n}"),
                ),
            )
            for n in [1, 2, 4, 5]
        )
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        normalized, facts = normalize_source_ir(parent, "2020/1")

        gap_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value]
        assert len(gap_facts) == 1
        assert "3" in gap_facts[0].before  # expected 3
        # All children preserved (gap doesn't remove anything)
        para_children = [c for c in normalized.children if c.kind == IRNodeKind.PARAGRAPH]
        assert len(para_children) == 4

    def test_detects_duplicate_and_drops_second(self) -> None:
        """Duplicate labels (1, 2, 2, 3) emit DUPLICATE_DROP and keep first occurrence."""
        children = tuple(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label=str(n),
                children=(
                    IRNode(kind=IRNodeKind.NUM, text=f"{n})"),
                    IRNode(kind=IRNodeKind.CONTENT, text=f"text {n} v{i}"),
                ),
            )
            for i, n in enumerate([1, 2, 2, 3])
        )
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        normalized, facts = normalize_source_ir(parent, "2020/1")

        dup_facts = [f for f in facts if f.kind_value == BASE_DUPLICATE_SIBLING_DROP]
        assert len(dup_facts) == 1
        assert "2" in dup_facts[0].before

        # Only 3 children remain (duplicate dropped)
        para_children = [c for c in normalized.children if c.kind == IRNodeKind.PARAGRAPH]
        assert len(para_children) == 3
        # First occurrence of label 2 is kept
        assert irnode_to_text(para_children[1]) == "2) text 2 v1"

    def test_unnumbered_intro_paragraph_reclassified_before_item_run(self) -> None:
        """A label inferred for intro prose is not a numbered item witness."""
        parent = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.CONTENT, text="Introductory lead-in:"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="1)"),
                        IRNode(kind=IRNodeKind.CONTENT, text="first numbered item;"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="2",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="2)"),
                        IRNode(kind=IRNodeKind.CONTENT, text="second numbered item."),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(parent, "2012/179")

        assert normalized.children[0].kind == IRNodeKind.INTRO
        assert irnode_to_text(normalized.children[0]) == "Introductory lead-in:"
        paragraphs = [child for child in normalized.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [irnode_to_text(paragraph) for paragraph in paragraphs] == [
            "1) first numbered item;",
            "2) second numbered item.",
        ]
        assert any(
            fact.kind == SourceNormalizationKind.TAG_RECLASSIFY
            and "leading unnumbered paragraph" in fact.before
            for fact in facts
        )
        assert not any(fact.kind_value == BASE_DUPLICATE_SIBLING_DROP for fact in facts)

    def test_repairs_terminal_duplicate_item_after_open_coordinator(self) -> None:
        """A terminal ``1), 2), 2)`` after ``ja`` is a local item-label typo."""
        parent = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="1)"),
                        IRNode(kind=IRNodeKind.CONTENT, text="ensimmäinen kohta,"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="2",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="2)"),
                        IRNode(kind=IRNodeKind.CONTENT, text="toinen kohta ja"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="2",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="2)"),
                        IRNode(kind=IRNodeKind.CONTENT, text="kolmas kohta."),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(parent, "1996/1117")

        paragraphs = [child for child in normalized.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3"]
        assert irnode_to_text(paragraphs[2]).startswith("3) kolmas kohta")
        repair_facts = [
            fact
            for fact in facts
            if fact.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value
        ]
        assert len(repair_facts) == 1
        assert "terminal duplicate paragraph label 2" in repair_facts[0].before
        assert not any(fact.kind_value == BASE_DUPLICATE_SIBLING_DROP for fact in facts)

    def test_terminal_duplicate_without_open_coordinator_is_not_relabelled(self) -> None:
        """The duplicate-item repair needs a local open-list witness."""
        children = tuple(
            IRNode(kind=IRNodeKind.PARAGRAPH, label=str(n), text=f"text {n} v{i}.")
            for i, n in enumerate([1, 2, 2])
        )
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)

        normalized, facts = normalize_source_ir(parent, "2020/1")

        paragraphs = [child for child in normalized.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "2"]
        assert not any(
            fact.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value
            and "terminal duplicate paragraph label" in fact.before
            for fact in facts
        )

    def test_no_anomaly_for_monotonic_sequence(self) -> None:
        """A clean 1, 2, 3 sequence produces no numbering facts."""
        children = tuple(
            IRNode(kind=IRNodeKind.PARAGRAPH, label=str(n), text=f"text {n}")
            for n in [1, 2, 3]
        )
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        _, facts = normalize_source_ir(parent, "2020/1")

        numbering_facts = [
            f for f in facts
            if f.kind_value in (
                SourceNormalizationKind.NUMBERING_REPAIR.value,
                BASE_DUPLICATE_SIBLING_DROP,
            )
        ]
        assert numbering_facts == []


class TestIntroListRestartSplit:
    def test_splits_standalone_intro_then_numbered_list_subsection(self) -> None:
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="4",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="4 §"),
                IRNode(kind=IRNodeKind.HEADING, text="Heading"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.INTRO,
                            text="Standalone earlier moment.",
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="2",
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="The authority records the following:"),),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="item one;"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="item two."),
                            ),
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "2017/367-fixture")

        subsections = [c for c in normalized.children if c.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 2
        assert subsections[0].label == "2"
        assert subsections[0].children == (IRNode(kind=IRNodeKind.CONTENT, text="Standalone earlier moment."),)
        assert subsections[1].children[0] == IRNode(
            kind=IRNodeKind.INTRO,
            text="The authority records the following:",
        )
        assert subsections[1].label == "3"
        assert [c.label for c in subsections[1].children[1:]] == ["1", "2"]

        split_facts = [f for f in facts if f.kind_value == BASE_INTRO_LIST_RESTART_SPLIT]
        assert len(split_facts) == 1

    def test_split_shifts_later_colliding_subsection_labels(self) -> None:
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="4",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="Standalone earlier moment."),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="The authority records the following:"),),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="item one."),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="Later moment."),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "2017/367-fixture")

        subsections = [c for c in normalized.children if c.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["2", "3", "4"]
        assert any(f.kind_value == BASE_INTRO_LIST_RESTART_SPLIT for f in facts)

    def test_no_anomaly_for_single_child(self) -> None:
        """A single numbered child produces no numbering facts."""
        children = (IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="only one"),)
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        _, facts = normalize_source_ir(parent, "2020/1")

        numbering_facts = [
            f for f in facts
            if f.kind_value in (
                SourceNormalizationKind.NUMBERING_REPAIR.value,
                BASE_DUPLICATE_SIBLING_DROP,
            )
        ]
        assert numbering_facts == []

    def test_ignores_non_numbered_kinds(self) -> None:
        """Non-numbered node kinds (CONTENT, P, etc.) are not checked for numbering."""
        children = (
            IRNode(kind=IRNodeKind.CONTENT, label="1", text="a"),
            IRNode(kind=IRNodeKind.CONTENT, label="1", text="b"),
        )
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        _, facts = normalize_source_ir(parent, "2020/1")

        numbering_facts = [
            f for f in facts
            if f.kind_value in (
                SourceNormalizationKind.NUMBERING_REPAIR.value,
                BASE_DUPLICATE_SIBLING_DROP,
            )
        ]
        assert numbering_facts == []


# ---------------------------------------------------------------------------
# EDITORIAL_STRIP: note, footnote, authorialNote blocks
# ---------------------------------------------------------------------------


class TestEditorialStripExtended:
    def test_strips_note_block(self) -> None:
        """BLOCK(name=note) is stripped as editorial."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="legal text",
            children=(IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "note"}),),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 0
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1
        assert "note" in strip_facts[0].before

    def test_strips_footnote_block(self) -> None:
        """BLOCK(name=footnote) is stripped as editorial."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="legal text",
            children=(IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "footnote"}),),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 0
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1
        assert "footnote" in strip_facts[0].before

    def test_strips_authorial_note(self) -> None:
        """HCONTAINER(name=authorialNote) is stripped as editorial."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="legal text",
            children=(
                IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "authorialNote"}),
            ),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 0
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1
        assert "authorialNote" in strip_facts[0].before

    def test_preserves_non_editorial_hcontainer(self) -> None:
        """HCONTAINER with non-editorial name is preserved."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="text",
            children=(
                IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "omission"}),
            ),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 1
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert strip_facts == []


# ---------------------------------------------------------------------------
# Disabled sparse-tail repairs
# ---------------------------------------------------------------------------


class TestSparsePayloadRepairs:
    def test_non_matching_sparse_repairs_are_noop(self) -> None:
        """Disabled sparse-tail repairs should leave unrelated nodes untouched."""
        node = IRNode(kind=IRNodeKind.SECTION, label="1", text="text")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized == node
        repair_facts = [f for f in facts if "sparse" in f.kind_value]
        assert repair_facts == []

    def test_1977_18_section_2_sparse_repair_is_not_applied(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>2§</num>
                  <subsection>
                    <intro><p>Eläkkeen saamisen edellytyksenä on:</p></intro>
                    <paragraph><num>1)</num><content><p>että luopuja ... kaksi hehtaaria;</p></content></paragraph>
                    <paragraph><num>2)</num><content><p>että luopujan ...</p></content></paragraph>
                    <paragraph><num>3)</num><content><p>että luopuja ... 45 vuotta; ja</p></content></paragraph>
                    <paragraph><num>4)</num><content><p>että luopuminen ...</p><p class="omission"/></content></paragraph>
                  </subsection>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "1977/18")

        subsection = next(child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION)
        assert [child.kind for child in subsection.children] == [
            IRNodeKind.INTRO,
            IRNodeKind.PARAGRAPH,
            IRNodeKind.PARAGRAPH,
            IRNodeKind.PARAGRAPH,
            IRNodeKind.PARAGRAPH,
            IRNodeKind.OMISSION,
        ]
        assert [child.label for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH] == ["1", "2", "3", "4"]
        repair_facts = [f for f in facts if "sparse" in f.kind_value]
        assert repair_facts == []

    def test_2008_342_section_21_sparse_repair_is_not_applied(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>21 §</num>
                  <heading>Muu ydinenergian käyttö</heading>
                  <subsection>
                    <intro>
                      <p>Lupa 2 §:n 1 momentin 2―6 kohdassa ja 2 §:n 2 momentin 1 kohdassa tarkoitettuun toimintaan voidaan myöntää, milloin toiminta sitä edellyttää jos:</p>
                    </intro>
                    <paragraph>
                      <num>1)</num>
                      <content><p>ydinenergian käyttö täyttää tämän lain mukaiset turvallisuutta koskevat vaatimukset;</p></content>
                    </paragraph>
                    <hcontainer name="omission"/>
                    <paragraph>
                      <num>7)</num>
                      <content><p>niiden vieraiden valtioiden suostumukset ...; ja</p></content>
                    </paragraph>
                  </subsection>
                  <subsection>
                    <content>
                      <p>ydinenergian käyttö muutoinkin täyttää 5―7 §:ssä säädetyt periaatteet eikä ole ristiriidassa Euratom-sopimuksen velvoitteiden kanssa.</p>
                    </content>
                  </subsection>
                  <hcontainer name="omission"/>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2008/342")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 2
        assert any(child.kind == IRNodeKind.OMISSION for child in normalized.children)

        seventh_para = next(
            child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH and child.label == "7"
        )
        subparagraphs = [child for child in seventh_para.children if child.kind == IRNodeKind.SUBPARAGRAPH]
        assert subparagraphs == []
        assert len([child for child in seventh_para.children if child.kind == IRNodeKind.CONTENT]) == 1

        repair_facts = [f for f in facts if "sparse" in f.kind_value]
        assert repair_facts == []

    def test_1994_1420_section_21_sparse_repair_is_not_applied(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>21 §</num>
                  <heading>Muu ydinenergian käyttö</heading>
                  <subsection>
                    <intro>
                      <p>Lupa 2 §:n 1 momentin 2―5 kohdassa tarkoitettuun toimintaan voidaan myöntää, jos, milloin toiminta sitä edellyttää:</p>
                    </intro>
                    <hcontainer name="omission"/>
                    <paragraph>
                      <num>5)</num>
                      <content><p>hakijalla on käytettävänään tarpeellinen asiantuntemus ...</p></content>
                    </paragraph>
                    <paragraph>
                      <num>6)</num>
                      <content><p>hakijalla harkitaan olevan taloudelliset ja muut tarpeelliset edellytykset ...</p></content>
                    </paragraph>
                    <paragraph>
                      <num>7)</num>
                      <content><p>niiden vieraiden valtioiden suostumukset ...; ja</p></content>
                      <content><p>ydinenergian käyttö muutoinkin täyttää 5―7 §:ssä säädetyt periaatteet eikä ole ristiriidassa Euratom-sopimuksen velvoitteiden kanssa.</p></content>
                    </paragraph>
                  </subsection>
                  <hcontainer name="omission"/>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "1994/1420")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        seventh_para = next(
            child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH and child.label == "7"
        )
        content_children = [child for child in seventh_para.children if child.kind == IRNodeKind.CONTENT]
        assert len(content_children) == 2
        subparagraphs = [child for child in seventh_para.children if child.kind == IRNodeKind.SUBPARAGRAPH]
        assert subparagraphs == []

        repair_facts = [f for f in facts if "sparse" in f.kind_value]
        assert repair_facts == []

    def test_2008_342_section_3_keeps_lettered_items_5a_and_5b(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>3 §</num>
                  <heading>Määritelmät</heading>
                  <subsection>
                    <intro><p>Tässä laissa tarkoitetaan:</p></intro>
                    <paragraph><num>1)</num><content><p>ydinenergian käytöllä ...</p></content></paragraph>
                    <hcontainer name="omission"/>
                    <paragraph><num>4)</num><content><p>ydinjätehuollolla ...</p></content></paragraph>
                    <hcontainer name="omission"/>
                    <paragraph><num>5 a)</num><content><p>ydinvoimalaitoksella ...</p></content></paragraph>
                    <paragraph><num>5 b)</num><content><p>käytöstä poistamisella ...</p></content></paragraph>
                    <hcontainer name="omission"/>
                    <paragraph><num>7)</num><content><p>valmiusjärjestelyillä ...</p></content></paragraph>
                  </subsection>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2008/342")

        subsection = next(child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION)
        paragraph_labels = [child.label for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
        assert paragraph_labels == ["1", "4", "5a", "5b", "7"]

        duplicate_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.DUPLICATE_DROP.value]
        assert duplicate_facts == []

        gap_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value]
        assert len(gap_facts) == 3

    def test_1981_555_section_11_splits_terminal_proportionality_sentence(self) -> None:
        """Maa-aineslaki 11 § keeps the proportionality sentence as a separate 4 mom."""
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>11 §</num>
                  <heading>Lupamääräykset</heading>
                  <subsection>
                    <content>
                      <p>Ainesten ottamista koskevaan lupaan on liitettävä määräykset siitä, mitä hakijan on noudatettava hankkeesta aiheutuvien haittojen välttämiseksi tai rajoittamiseksi.</p>
                    </content>
                  </subsection>
                  <subsection>
                    <intro><p>Lupamääräykset on annettava:</p></intro>
                    <paragraph><num>1)</num><content><p>ottamisalueen rajauksesta, kaivausten ja leikkausten syvyydestä ja muodosta sekä ottamistoiminnan etenemissuunnista;</p></content></paragraph>
                    <paragraph><num>2)</num><content><p>alueen suojaamisesta ja siistimisestä ottamisen aikana ja sen jälkeen; sekä</p></content></paragraph>
                    <paragraph><num>3)</num><content><p>puuston ja muun kasvillisuuden säilyttämisestä, uusimisesta ja uusista istutuksista ottamisen aikana ja sen jälkeen.</p></content></paragraph>
                  </subsection>
                  <subsection>
                    <intro><p>Lupamääräyksiä voidaan lisäksi antaa:</p></intro>
                    <paragraph><num>1)</num><content><p>ottamiseen liittyvistä laitteista ja liikenteen järjestämisestä erityisesti pohjaveden suojelemiseksi; sekä</p></content></paragraph>
                    <paragraph><num>2)</num><content><p>ajasta, jonka kuluessa tämän pykälän nojalla määrätyt toimenpiteet on suoritettava.</p></content></paragraph>
                    <paragraph><num>2)</num><content><p>Määräykset eivät saa aiheuttaa luvan saajalle sellaista vahinkoa ja haittaa, jota on pidettävä hankkeen laajuuteen ja hänen saamaansa hyötyyn nähden kohtuuttomana.</p></content></paragraph>
                  </subsection>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "1981/555")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [child.label for child in subsections] == ["1", "2", "3", "4"]

        third = subsections[2]
        assert [child.label for child in third.children if child.kind == IRNodeKind.PARAGRAPH] == ["1", "2"]

        fourth = subsections[3]
        assert fourth.kind == IRNodeKind.SUBSECTION
        assert fourth.label == "4"
        assert "Määräykset eivät saa aiheuttaa" in (next(
            child for child in fourth.children if child.kind == IRNodeKind.CONTENT
        ).text or "")

        repair_facts = [f for f in facts if f.kind_value == BASE_DUPLICATE_TAIL_SPLIT]
        assert len(repair_facts) == 1
        assert repair_facts[0].basis_value == SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR.value
        assert not any(f.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value for f in facts)

    def test_duplicate_tail_split_ignores_unlabelled_paragraph_rows(self) -> None:
        """Unlabelled prose/list rows are not duplicate-labelled paragraphs."""
        node = IRNode(
            kind=IRNodeKind.SECTION,
            label="3",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.INTRO, text="(**) note begins:"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="- first row"),),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="- terminal row."),),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="Following real prose."),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(node, "unlabelled-tail-fixture")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [subsection.label for subsection in subsections] == ["4", "5"]
        assert "- terminal row." in irnode_to_text(subsections[0])
        assert "Following real prose." in irnode_to_text(subsections[1])
        assert not any(fact.kind_value == BASE_DUPLICATE_TAIL_SPLIT for fact in facts)


class TestDigitResetSubparagraphSplit:
    def test_splits_digit_reset_buried_inside_subparagraph_run(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>1 §</num>
                  <subsection>
                    <num>1 mom.</num>
                    <paragraph>
                      <num>4)</num>
                      <intro><p>naudanliha-alan yhteisestä markkinajärjestelystä;</p></intro>
                      <subparagraph><num>a)</num><content><p>sonnipalkkio;</p></content></subparagraph>
                      <subparagraph><num>b)</num><content><p>härkäpalkkio;</p></content></subparagraph>
                      <subparagraph><num>5)</num><content><p>lampaan- ja vuohenliha-alan yhteisestä markkinajärjestelystä;</p></content></subparagraph>
                      <subparagraph><num>a)</num><content><p>uuhipalkkio;</p></content></subparagraph>
                      <subparagraph><num>b)</num><content><p>lisäpalkkio;</p></content></subparagraph>
                    </paragraph>
                  </subsection>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2000/154")

        subsection = next(child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION)
        paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [child.label for child in paragraphs] == ["4", "5"]

        para4_subs = [child.label for child in paragraphs[0].children if child.kind == IRNodeKind.SUBPARAGRAPH]
        para5_subs = [child.label for child in paragraphs[1].children if child.kind == IRNodeKind.SUBPARAGRAPH]
        assert para4_subs == ["a", "b"]
        assert para5_subs == ["a", "b"]
        assert check_invariants(normalized) == []

        repair_facts = [f for f in facts if f.kind_value == BASE_DIGIT_RESET_SPLIT]
        assert any("digit-labelled subparagraph 5" in f.before for f in repair_facts)
        assert not any(f.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value for f in facts)

    def test_splits_terminal_digit_subparagraph_when_peer_sequence_witnesses_item(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <subsection>
                  <intro><p>Momentissa tarkoitetaan:</p></intro>
                  <paragraph>
                    <num>2)</num>
                    <intro><p>hyödykettä koskevia sopimuksia, joita ovat:</p></intro>
                    <subparagraph><num>a)</num><content><p>ensimmäinen alakohta;</p></content></subparagraph>
                    <subparagraph><num>b)</num><content><p>toinen alakohta; sekä</p></content></subparagraph>
                    <subparagraph><num>3)</num><content><p>kolmas kohta.</p></content></subparagraph>
                  </paragraph>
                  <paragraph><num>4)</num><content><p>neljäs kohta.</p></content></paragraph>
                </subsection>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2014/1194")

        paragraphs = [child for child in normalized.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [child.label for child in paragraphs] == ["2", "3", "4"]
        assert [child.label for child in paragraphs[0].children if child.kind == IRNodeKind.SUBPARAGRAPH] == [
            "a",
            "b",
        ]
        assert [child.label for child in paragraphs[1].children if child.kind == IRNodeKind.SUBPARAGRAPH] == []
        assert "kolmas kohta" in irnode_to_text(paragraphs[1])
        assert check_invariants(normalized) == []

        repair_facts = [f for f in facts if f.kind_value == BASE_DIGIT_RESET_SPLIT]
        assert any("digit-labelled subparagraph 3" in f.before for f in repair_facts)

    def test_does_not_split_terminal_digit_subparagraph_without_peer_sequence_witness(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <subsection>
                  <intro><p>Momentissa tarkoitetaan:</p></intro>
                  <paragraph>
                    <num>2)</num>
                    <intro><p>hyödykettä koskevia sopimuksia, joita ovat:</p></intro>
                    <subparagraph><num>a)</num><content><p>ensimmäinen alakohta;</p></content></subparagraph>
                    <subparagraph><num>5)</num><content><p>ei seuraa peer-sarjaa.</p></content></subparagraph>
                  </paragraph>
                  <paragraph><num>4)</num><content><p>neljäs kohta.</p></content></paragraph>
                </subsection>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2020/1")

        paragraphs = [child for child in normalized.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [child.label for child in paragraphs] == ["2", "4"]
        assert [child.label for child in paragraphs[0].children if child.kind == IRNodeKind.SUBPARAGRAPH] == [
            "a",
            "5",
        ]
        assert not any("digit-labelled subparagraph 5" in f.before for f in facts)

    def test_does_not_split_plain_lettered_subparagraph_run(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <paragraph>
                  <num>4)</num>
                  <content><p>otsikko:</p></content>
                  <subparagraph><num>a)</num><content><p>ensimmäinen</p></content></subparagraph>
                  <subparagraph><num>b)</num><content><p>toinen</p></content></subparagraph>
                </paragraph>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2020/1")

        assert normalized.kind == IRNodeKind.PARAGRAPH
        assert normalized.label == "4"
        assert [child.label for child in normalized.children if child.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b"]
        assert not any("digit-labelled subparagraph" in f.before for f in facts)


def test_heading_body_subsection_split_rehomes_body_heading_as_first_moment() -> None:
    raw = fi_xml_to_ir_node(
        etree.fromstring(
            """
            <section>
              <num>51 §</num>
              <heading>Nopeuskilpailuja henkilöautoille ja moottoripyörille saa järjestää vain suljetulla tiellä. Tien sulkemiseen tarvitaan lupa, jonka myöntää kunnanhallitus tai lääninhallitus. Lääninhallituksen on kuultava tienpitäjää.</heading>
              <subsection><content><p>Muille moottoriajoneuvoille ei nopeuskilpailuja saa järjestää.</p></content></subsection>
              <subsection><content><p>Poliisilla on tienpitäjää kuultuaan oikeus tien tilapäiseen sulkemiseen.</p></content></subsection>
            </section>
            """
        ),
        _fi_label_postprocessor,
    )

    normalized, facts = normalize_source_ir(raw, "1994/328")

    assert [child.kind for child in normalized.children] == [
        IRNodeKind.NUM,
        IRNodeKind.SUBSECTION,
        IRNodeKind.SUBSECTION,
        IRNodeKind.SUBSECTION,
    ]
    subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3"]
    assert "Nopeuskilpailuja henkilöautoille" in irnode_to_text(subsections[0])
    assert subsections[0].attrs["lawvm_source_normalization_rule"] == "fi_heading_body_subsection_split_v1"
    assert subsections[1].attrs["lawvm_source_normalization_original_label"] == "1"

    repair_facts = [fact for fact in facts if fact.kind_value == BASE_HEADING_BODY_SUBSECTION_SPLIT]
    assert len(repair_facts) == 1
    assert repair_facts[0].basis == SourceNormalizationBasis.PROFILE_INVALID
    assert "heading converted to subsection:1" in repair_facts[0].after
    assert check_invariants(normalized) == []


def test_heading_body_subsection_split_does_not_rehome_real_section_title() -> None:
    raw = fi_xml_to_ir_node(
        etree.fromstring(
            """
            <section>
              <num>1 §</num>
              <heading>Pakkaamattomista elintarvikkeista annettavien tietojen ilmoittamistapa</heading>
              <subsection><content><p>Elintarvikkeesta on ilmoitettava tarpeelliset tiedot.</p></content></subsection>
            </section>
            """
        ),
        _fi_label_postprocessor,
    )

    normalized, facts = normalize_source_ir(raw, "2020/1")

    assert [child.kind for child in normalized.children] == [
        IRNodeKind.NUM,
        IRNodeKind.HEADING,
        IRNodeKind.SUBSECTION,
    ]
    assert not any(fact.kind_value == BASE_HEADING_BODY_SUBSECTION_SPLIT for fact in facts)


def test_table_continuation_subsection_merge_joins_interrupted_first_moment() -> None:
    raw = fi_xml_to_ir_node(
        etree.fromstring(
            """
            <section>
              <num>21 §</num>
              <subsection><content><p>Ensimmäisessä momentissa säädetty määrä koskee seuraavia</p></content></subsection>
              <subsection>
                <content>
                  <p>hyväksyttäviä ryhmiä:</p>
                  <table>
                    <tr><td><p>Alue</p></td><td><p>Mänty</p></td><td><p>Pääpuulaji Kuusi kpl/hehtaari</p></td><td><p>Muu puulaji</p></td></tr>
                    <tr><td><p>Pohjoinen</p></td><td><p>1 000</p></td><td><p>1 000</p></td><td><p>1000</p></td></tr>
                  </table>
                </content>
              </subsection>
              <subsection eId="sec_21__subsec_3"><content><p>Edellä 1 momentissa tarkoitettua määrää voidaan alentaa.</p></content></subsection>
              <subsection eId="sec_21__subsec_4"><content><p>Päätös tehdään hakemuksesta.</p></content></subsection>
            </section>
            """
        ),
        _fi_label_postprocessor,
    )

    normalized, facts = normalize_source_ir(raw, "1991/1208")

    subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
    assert [subsection.label for subsection in subsections] == ["1", "2", "3"]
    assert "koskee seuraavia hyväksyttäviä ryhmiä" in irnode_to_text(subsections[0])
    table = next(child for child in subsections[0].children[1].children if child.kind == IRNodeKind.TABLE)
    assert [irnode_to_text(row) for row in table.children[:3]] == [
        "Pääpuulaji",
        "Alue Mänty Kuusi Muu puulaji",
        "kpl/hehtaari",
    ]
    assert "Pohjoinen 1 000 1 000 1 000" in irnode_to_text(table)
    assert subsections[0].attrs["lawvm_source_normalization_rule"] == (
        "fi_table_continuation_subsection_merge_v1"
    )
    assert subsections[0].attrs["lawvm_source_normalization_merged_label"] == "2"
    assert subsections[1].attrs["lawvm_source_normalization_original_label"] == "3"
    assert "eId" not in subsections[1].attrs
    assert subsections[1].attrs["lawvm_source_subsection_eid"] == "sec_21__subsec_3"

    repair_facts = [
        fact for fact in facts if fact.kind_value == BASE_TABLE_CONTINUATION_SUBSECTION_MERGE
    ]
    assert len(repair_facts) == 1
    assert repair_facts[0].basis == SourceNormalizationBasis.PROFILE_INVALID
    assert "table continuation" in repair_facts[0].before
    header_facts = [
        fact for fact in facts if fact.kind_value == BASE_TABLE_CONTINUATION_HEADER_REPAIR
    ]
    assert len(header_facts) == 1
    assert check_invariants(normalized) == []


def test_table_continuation_subsection_merge_keeps_real_table_moment() -> None:
    raw = fi_xml_to_ir_node(
        etree.fromstring(
            """
            <section>
              <num>2 §</num>
              <subsection><content><p>Ensimmäinen momentti on kokonainen.</p></content></subsection>
              <subsection>
                <content>
                  <p>Toisessa momentissa säädetään taulukosta:</p>
                  <table>
                    <tr><td><p>Alue</p></td><td><p>Määrä</p></td></tr>
                  </table>
                </content>
              </subsection>
              <subsection><content><p>Kolmas momentti säilyy erillisenä.</p></content></subsection>
            </section>
            """
        ),
        _fi_label_postprocessor,
    )

    normalized, facts = normalize_source_ir(raw, "2020/1")

    subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
    assert [subsection.label for subsection in subsections] == ["1", "2", "3"]
    assert not any(
        fact.kind_value == BASE_TABLE_CONTINUATION_SUBSECTION_MERGE for fact in facts
    )
