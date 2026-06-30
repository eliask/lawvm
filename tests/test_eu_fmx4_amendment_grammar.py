"""Offline tests for the REAL FMX4 amendment-grammar lowering (Increment 0).

No network: a pinned FMX4 amending-act excerpt fixture. Verifies quoted-block
payload CAPTURE, TARGET resolution into the BASE act's coordinate system (NOT the
amending act's own article number), typed provenance (statute_id + raw_text +
witness_rule_id), and HONEST coverage (the out-of-scope point edit + the
entry-into-force boilerplate are diagnosed, not silently dropped).
"""

from __future__ import annotations

from pathlib import Path

from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.eu.fmx4_amendment_grammar import lower_amending_act

FIXTURES = Path(__file__).parent / "eu" / "fixtures"
AMENDING_CELEX = "32017R0488"


def _load() -> bytes:
    return (FIXTURES / "amending_act_excerpt.fmx4.xml").read_bytes()


def test_lowers_four_families_with_payload_capture() -> None:
    r = lower_amending_act(
        _load(), AMENDING_CELEX, base_celex="32016R0679", effective="2017-03-23"
    )
    by_target = {str(op.target): op for op in r.ops}

    # whole-article replace: targets BASE article 5, captures the QUOT block
    rep = by_target["article:5"]
    assert rep.action == StructuralAction.REPLACE
    assert rep.witness_rule_id == "EU_FMX4.WHOLE_ARTICLE_REPLACE"
    assert rep.payload is not None
    assert rep.payload.kind == IRNodeKind.SECTION
    assert "lawful only where consent" in rep.payload.text

    # whole-article repeal: targets BASE article 7, no payload
    rep7 = by_target["article:7"]
    assert rep7.action == StructuralAction.REPEAL
    assert rep7.payload is None

    # whole-article insert: targets NEW article 5a, captures the QUOT wrapper
    ins = by_target["article:5a"]
    assert ins.action == StructuralAction.INSERT
    assert ins.payload is not None
    assert "Records of processing" in ins.payload.text

    # sub-article paragraph replace: BASE article 9 / paragraph 2
    sub = by_target["article:9/paragraph:2"]
    assert sub.action == StructuralAction.REPLACE
    assert sub.witness_rule_id == "EU_FMX4.SUBART_PARAGRAPH_REPLACE"
    assert sub.payload is not None
    assert "explicit consent" in sub.payload.text


def test_target_is_base_article_not_amending_article_number() -> None:
    """The amending act's own Article 1..6 numbering must NOT leak as targets."""
    r = lower_amending_act(_load(), AMENDING_CELEX, effective="2017-03-23")
    targets = {str(op.target) for op in r.ops}
    # The 5 covered ops target base articles 5, 7, 5a, 9/2, 12/point(b) — never
    # the amending act's own 1..6 scaffolding numbers.
    assert targets == {
        "article:5",
        "article:7",
        "article:5a",
        "article:9/paragraph:2",
        "article:12/point:b",
    }


def test_provenance_footing_statute_id_and_raw_text() -> None:
    r = lower_amending_act(_load(), AMENDING_CELEX, effective="2017-03-23")
    for op in r.ops:
        assert op.source is not None
        assert op.source.statute_id == AMENDING_CELEX
        assert op.source.effective == "2017-03-23"
        assert op.source.raw_text  # the source clause is carried verbatim
        assert op.witness_rule_id  # every op names its grammar rule


def test_coverage_measured_honestly() -> None:
    r = lower_amending_act(_load(), AMENDING_CELEX, effective="2017-03-23")
    # 6 instructions: 5 lowered (Increment 1 added the point-(b) edit via
    # EU_FMX4.SUBART_POINT_REPLACE); only the entry-into-force boilerplate is
    # OUT OF SCOPE and diagnosed.
    assert r.instruction_count == 6
    assert r.covered_count == 5
    assert abs(r.coverage_fraction - (5 / 6)) < 1e-9
    uncovered = [d.rule_id for d in r.diagnostics]
    assert uncovered.count("eu_fmx4_grammar_uncovered_instruction") == 1


def test_point_b_edit_lowered_increment1() -> None:
    """Increment 1: the sub-article point-(b) edit is now a typed REPLACE op."""
    r = lower_amending_act(
        _load(), AMENDING_CELEX, base_celex="32016R0679", effective="2017-03-23"
    )
    by_target = {str(op.target): op for op in r.ops}
    pt = by_target["article:12/point:b"]
    assert pt.action == StructuralAction.REPLACE
    assert pt.witness_rule_id == "EU_FMX4.SUBART_POINT_REPLACE"
    assert pt.payload is not None
    # The inline single-quoted replacement payload ("the controller") is captured.
    assert "the controller" in pt.payload.text


def test_replace_without_quoted_block_diagnosed_not_dropped() -> None:
    no_block = b"""<?xml version="1.0"?>
<ACT><ENACTING.TERMS>
  <ARTICLE><TI.ART>Article 1</TI.ART>
    <PARAG><P>Article 5 is replaced by the following:</P></PARAG></ARTICLE>
</ENACTING.TERMS></ACT>"""
    r = lower_amending_act(no_block, AMENDING_CELEX)
    assert r.covered_count == 0
    assert any(
        d.rule_id == "eu_fmx4_grammar_replace_missing_quoted_block"
        for d in r.diagnostics
    )


def test_non_xml_diagnosed() -> None:
    r = lower_amending_act(b"not xml at all", AMENDING_CELEX)
    assert r.ops == []
    assert r.diagnostics[0].rule_id == "eu_fmx4_grammar_not_xml"


def test_doc_root_envelope_diagnosed_not_crashed() -> None:
    """Increment 1 goal 4: a DOC-root publication envelope with no ACT/ANNEX and
    no enacting terms (the real 32016R0690 shape) is a typed residual, not a
    crash and not a silent zero."""
    doc_root = b"""<?xml version="1.0"?><DOC><BODY/></DOC>"""
    r = lower_amending_act(doc_root, AMENDING_CELEX)
    assert r.ops == []
    assert r.diagnostics[0].rule_id == "eu_fmx4_grammar_envelope_no_enacting_terms"


def test_doc_root_with_embedded_act_is_lowered() -> None:
    """A DOC envelope that WRAPS an ACT is dug out and lowered (root hardening)."""
    doc_with_act = b"""<?xml version="1.0"?>
<DOC><FMX><ACT><ENACTING.TERMS>
  <ARTICLE><TI.ART>Article 1</TI.ART>
    <PARAG><P>Article 5 is deleted.</P></PARAG></ARTICLE>
</ENACTING.TERMS></ACT></FMX></DOC>"""
    r = lower_amending_act(doc_with_act, AMENDING_CELEX, base_celex="32016R0044")
    assert r.covered_count == 1
    assert r.ops[0].witness_rule_id == "EU_FMX4.WHOLE_ARTICLE_REPEAL"
    assert str(r.ops[0].target) == "article:5"


def test_annex_root_lowered_as_whole_annex_replace() -> None:
    """Increment 1 goal 4: the ANNEX-rooted new-annex manifestation (the real
    degree-57 amending-act shape — 32016R0466 etc.) lowers to a WHOLE-ANNEX
    REPLACE targeting the base act's annex, with the new annex body as payload."""
    annex_root = b"""<?xml version="1.0"?>
<ANNEX>
  <TITLE><TI><P>ANNEX III</P></TI></TITLE>
  <CONTENTS><GR.SEQ>
    <P><QUOT.START/>New listing: Person A; Person B.<QUOT.END/></P>
  </GR.SEQ></CONTENTS>
</ANNEX>"""
    r = lower_amending_act(
        annex_root, "32016R0466", base_celex="32016R0044", effective="2016-04-01"
    )
    assert r.covered_count == 1
    op = r.ops[0]
    assert op.action == StructuralAction.REPLACE
    assert op.witness_rule_id == "EU_FMX4.ANNEX_ROOT_REPLACE"
    assert str(op.target) == "annex:III"
    assert op.payload is not None
    assert "New listing" in op.payload.text
    assert op.source is not None and op.source.effective == "2016-04-01"


def test_annex_root_without_number_diagnosed() -> None:
    annex_root = b"""<?xml version="1.0"?>
<ANNEX><CONTENTS><P>some body without an ANNEX N title</P></CONTENTS></ANNEX>"""
    r = lower_amending_act(annex_root, "32016R0466", base_celex="32016R0044")
    assert r.covered_count == 0
    assert r.diagnostics[0].rule_id == "eu_fmx4_grammar_annex_root_no_number"


def test_indirect_annex_as_set_out_lowered_increment2() -> None:
    """Increment 2 real long-tail: 'Annex N to Regulation X is replaced/amended as
    set out in the Annex to this Regulation' → REPLACE on the named base annex,
    payload from the amending act's own <ANNEX>."""
    fmx = b"""<?xml version="1.0"?>
<ACT><ENACTING.TERMS>
  <ARTICLE><TI.ART>Article 1</TI.ART>
    <ALINEA>Annex II to Regulation (EU) 2016/44 is amended as set out in the Annex to this Regulation.</ALINEA>
  </ARTICLE>
</ENACTING.TERMS>
<ANNEX><TITLE><TI><P>ANNEX</P></TI></TITLE><CONTENTS><P>New list.</P></CONTENTS></ANNEX>
</ACT>"""
    r = lower_amending_act(fmx, "32017R0489", base_celex="32016R0044")
    assert r.covered_count == 1
    op = r.ops[0]
    assert op.witness_rule_id == "EU_FMX4.ANNEX_AMENDED_AS_SET_OUT"
    assert str(op.target) == "annex:II"
    assert op.payload is not None and "New list" in op.payload.text
    assert "annex_payload=inline" in op.provenance_tags


def test_quot_s_wrapper_payload_captured_and_no_double_count_increment2() -> None:
    """Increment 2 real-bytes fix: a whole-article REPLACE quotes the new body as a
    nested <ARTICLE> in a QUOT.S wrapper. The payload is captured and the nested
    ARTICLE is NOT counted as a second instruction."""
    fmx = b"""<?xml version="1.0"?>
<ACT><ENACTING.TERMS>
  <ARTICLE IDENTIFIER="001"><TI.ART>Article 1</TI.ART>
    <ALINEA><P>Article 21 of Regulation (EU) 2016/44 is replaced by the following:</P>
      <QUOT.S><ARTICLE IDENTIFIER="021"><TI.ART><QUOT.START/>Article 21</TI.ART>
        <PARAG><NO.PARAG>1.</NO.PARAG><ALINEA>The Council shall act.<QUOT.END/></ALINEA></PARAG>
      </ARTICLE></QUOT.S>
    </ALINEA>
  </ARTICLE>
</ENACTING.TERMS></ACT>"""
    r = lower_amending_act(fmx, "32017R0488", base_celex="32016R0044")
    assert r.instruction_count == 1  # NOT 2 (the nested replacement is pruned)
    assert r.covered_count == 1
    op = r.ops[0]
    assert op.witness_rule_id == "EU_FMX4.WHOLE_ARTICLE_REPLACE"
    assert str(op.target) == "article:21"
    assert op.payload is not None and "Council shall act" in op.payload.text


def test_corrigendum_for_read_lowered() -> None:
    """Increment 1: the corrigendum 'for: X read: Y' formula lowers to a typed
    TEXT_REPLACE on the named Article."""
    fmx = b"""<?xml version="1.0"?>
<ACT><ENACTING.TERMS>
  <ARTICLE><TI.ART>Article 1</TI.ART>
    <PARAG><P>In Article 6, for: 'the Council' read: 'the Commission'</P></PARAG>
  </ARTICLE>
</ENACTING.TERMS></ACT>"""
    r = lower_amending_act(fmx, "32016R0044R(01)", base_celex="32016R0044")
    assert r.covered_count == 1
    op = r.ops[0]
    assert op.witness_rule_id == "EU_FMX4.CORRIGENDUM_FOR_READ"
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "the Council"
    assert op.text_patch.replacement == "the Commission"
    assert str(op.target) == "article:6"
