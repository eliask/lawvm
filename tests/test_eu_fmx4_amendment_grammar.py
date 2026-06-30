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
    # The 4 covered ops target base articles 5, 7, 5a, 9/2 — never the amending
    # act's own 1/2/3/4 scaffolding numbers.
    assert targets == {"article:5", "article:7", "article:5a", "article:9/paragraph:2"}


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
    # 6 instructions: 4 lowered; the point (b) edit + the entry-into-force
    # boilerplate are OUT OF SCOPE and diagnosed.
    assert r.instruction_count == 6
    assert r.covered_count == 4
    assert abs(r.coverage_fraction - (4 / 6)) < 1e-9
    uncovered = [d.rule_id for d in r.diagnostics]
    assert uncovered.count("eu_fmx4_grammar_uncovered_instruction") == 2


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


def test_doc_root_variance_diagnosed() -> None:
    doc_root = b"""<?xml version="1.0"?><DOC><BODY/></DOC>"""
    r = lower_amending_act(doc_root, AMENDING_CELEX)
    assert r.diagnostics[0].rule_id == "eu_fmx4_grammar_no_act_root"
