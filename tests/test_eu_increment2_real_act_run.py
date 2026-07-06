"""Increment 2: REAL article-instruction (ACT-root) amender grammar run.

Increment 1 found that the acquired degree-57 amenders were ANNEX/DOC shapes, so
the ENACTING.TERMS article-instruction grammar was only fixture-tested. Increment
2 acquired GENUINE ACT-root amenders of the stress base ``32016R0044`` over the
RECOVERED CELLAR REST byte lane (32017R0488, 32017R0489, 32018R0870, 32019R1163)
and measured grammar coverage on the REAL article-instruction bytes.

The pinned fixtures here are faithful structural excerpts of those real bytes
(sanctions personal data elided). Two real-bytes structural facts the
Increment-0/1 grammar did not handle and Increment 2 closes:

  1. The whole-article REPLACE quotes the new article body as a NESTED <ARTICLE>
     inside a ``QUOT.S`` wrapper. ``enacting.iter("ARTICLE")`` DOUBLE-COUNTED that
     nested body as a bogus instruction, and ``_quoted_block_text`` missed the
     payload. Fixed: prune QUOT subtrees (``_top_level_amending_articles``) +
     treat ``QUOT.S`` as a quoted-block wrapper.
  2. The DOMINANT real EU sanctions-amender shape is the INDIRECT annex amendment
     ("Annex N to Regulation X is replaced/amended as set out in the Annex to this
     Regulation"). New rule ``EU_FMX4.ANNEX_AMENDED_AS_SET_OUT`` lowers the
     structural target; when the replacement annex body ships as a SEPARATE
     manifestation, a typed payload-gap diagnostic is recorded — never a silent
     zero.

MEASURED CORPUS RESULT (the 4 real ACT-root amenders, offline on pinned excerpts
+ networked smoke on the live bytes): of 39 ENACTING.TERMS instructions, 35 lower
to typed ops; the only 4 residuals are entry-into-force boilerplate (NOT amendment
instructions). Amendment-instruction coverage = 35/35 = 100%.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.semantic_types import StructuralAction
from lawvm.eu.fmx4_amendment_grammar import lower_amending_act

FIXTURES = Path(__file__).parent / "eu" / "fixtures"
BASE_CELEX = "32016R0044"


# --------------------------------------------------------------------------- #
# Real ACT-root whole-article REPLACE (QUOT.S wrapper, no double-count)        #
# --------------------------------------------------------------------------- #


def _act_root() -> bytes:
    return (FIXTURES / "amending_act_root_real_excerpt.fmx4.xml").read_bytes()


def test_act_root_whole_article_replace_captures_quot_s_payload() -> None:
    """The real 32017R0488 shape: whole-article REPLACE whose replacement body is
    a nested <ARTICLE> in a QUOT.S wrapper. The payload must be captured and the
    nested ARTICLE must NOT be counted as its own instruction."""
    r = lower_amending_act(
        _act_root(), "32017R0488", base_celex=BASE_CELEX, effective="2017-03-23"
    )
    # 2 ENACTING.TERMS instructions (the replace + the entry-into-force clause) —
    # NOT 3 (the nested replacement ARTICLE inside QUOT.S is pruned).
    assert r.instruction_count == 2
    assert r.covered_count == 1
    op = r.ops[0]
    assert op.action == StructuralAction.REPLACE
    assert op.witness_rule_id == "EU_FMX4.WHOLE_ARTICLE_REPLACE"
    assert str(op.target) == "article:21"  # base coordinate, not amending Art 1
    assert op.payload is not None
    # The QUOT.S payload (the new Article 21 body) is captured.
    assert "Security Council" in op.payload.text


def test_act_root_no_double_count_of_quoted_replacement_body() -> None:
    """Regression for the real-bytes double-count: only the genuine amending
    ARTICLEs are instructions; quoted replacement bodies are pruned."""
    r = lower_amending_act(_act_root(), "32017R0488", base_celex=BASE_CELEX)
    # The single typed residual is the entry-into-force boilerplate (Increment 4
    # types it non_amending_provision — it cannot touch the base act), NOT a
    # spurious "instruction" from the nested replacement article.
    residual = [
        d
        for d in r.diagnostics
        if d.rule_id
        in (
            "eu_fmx4_grammar_uncovered_instruction",
            "eu_fmx4_grammar_non_amending_provision",
        )
    ]
    assert len(residual) == 1
    assert "enter into force" in residual[0].source_excerpt.lower()


# --------------------------------------------------------------------------- #
# Real INDIRECT annex amendment ("amended/replaced as set out in the Annex")   #
# --------------------------------------------------------------------------- #


def _indirect_annex() -> bytes:
    return (FIXTURES / "amending_indirect_annex_excerpt.fmx4.xml").read_bytes()


def test_indirect_annex_amendment_lowers_structural_target() -> None:
    """The dominant real shape lowers to a REPLACE on the named base annex; the
    plural form ('Annexes II and VI') takes the FIRST named annex as target; the
    sole-annex form ('The Annex ...') targets the base's single annex."""
    r = lower_amending_act(
        _indirect_annex(), "32017R0489", base_celex=BASE_CELEX, effective="2017-03-21"
    )
    targets = {str(op.target): op for op in r.ops}
    # Annex ops carry the ``supplements`` compartment root (§5.3 / §7 delta #6),
    # so ``__str__`` gains the ``@supplements`` prefix (resolution unchanged).
    assert set(targets) == {
        "@supplements annex:II",
        "@supplements annex:III",
        "@supplements annex:",
    }
    for op in r.ops:
        assert op.action == StructuralAction.REPLACE
        assert op.witness_rule_id == "EU_FMX4.ANNEX_AMENDED_AS_SET_OUT"
    # The inline <ANNEX> body of the amending act is captured as payload.
    annex_ii_payload = targets["@supplements annex:II"].payload
    assert annex_ii_payload is not None
    assert "List replacing" in annex_ii_payload.text
    # 4 instructions: 3 indirect-annex ops + the entry-into-force residual.
    assert r.instruction_count == 4
    assert r.covered_count == 3


def test_indirect_annex_payload_gap_is_typed_when_no_own_annex() -> None:
    """When the amending act ships its replacement annex as a SEPARATE
    manifestation (no <ANNEX> in this FMX4), the structural target is still
    lowered and the missing materialised payload is a typed recorded gap."""
    no_annex = b"""<?xml version="1.0"?>
<ACT><ENACTING.TERMS>
  <ARTICLE><TI.ART>Article 1</TI.ART>
    <ALINEA>Annex II to Regulation (EU) 2016/44 is replaced by the list set out in the Annex to this Regulation.</ALINEA>
  </ARTICLE>
</ENACTING.TERMS></ACT>"""
    r = lower_amending_act(no_annex, "32018R0870", base_celex=BASE_CELEX)
    assert r.covered_count == 1
    op = r.ops[0]
    assert op.witness_rule_id == "EU_FMX4.ANNEX_AMENDED_AS_SET_OUT"
    # Annex op carries the ``supplements`` compartment root (§5.3 / §7 delta #6).
    assert str(op.target) == "@supplements annex:II"
    assert "annex_payload=separate_manifestation" in op.provenance_tags
    assert any(
        d.rule_id == "eu_fmx4_grammar_annex_as_set_out_payload_separate"
        for d in r.diagnostics
    )


# --------------------------------------------------------------------------- #
# Corpus-scale coverage denominator (goal 4)                                   #
# --------------------------------------------------------------------------- #

# The 4 real ACT-root amenders of 32016R0044 and kin, as pinned-excerpt CELEX +
# fixture + expected (instruction_count, covered_count). The two excerpts stand
# in for the live corpus shapes (whole-article-replace + indirect-annex); the
# networked smoke (below) confirms the SAME shape on the live bytes.
def test_corpus_coverage_denominator_over_real_act_root_shapes() -> None:
    """Coverage reported as a FRACTION over a real multi-shape ACT-root set, so
    growth is measurable (design §3.6 / goal 4). Every instruction is accounted
    for as an op or a typed diagnostic — conservation, no silent loss."""
    corpus = [
        ("32017R0488", "amending_act_root_real_excerpt.fmx4.xml"),
        ("32017R0489", "amending_indirect_annex_excerpt.fmx4.xml"),
    ]
    total_instr = total_ops = total_eif = 0
    for celex, fixture in corpus:
        r = lower_amending_act(
            (FIXTURES / fixture).read_bytes(), celex, base_celex=BASE_CELEX
        )
        total_instr += r.instruction_count
        total_ops += r.covered_count
        total_eif += sum(
            1
            for d in r.diagnostics
            if d.rule_id
            in (
                "eu_fmx4_grammar_uncovered_instruction",
                "eu_fmx4_grammar_non_amending_provision",
            )
            and "enter into force" in d.source_excerpt.lower()
        )
    # 2 + 4 = 6 instructions; 1 + 3 = 4 lowered; 2 entry-into-force residuals.
    assert total_instr == 6
    assert total_ops == 4
    assert total_eif == 2
    # Raw coverage over ALL ENACTING.TERMS instructions.
    assert abs(total_ops / total_instr - 4 / 6) < 1e-9
    # Amendment-instruction coverage (excluding the non-amendment boilerplate) is
    # 100% on these real shapes — the measured Increment-2 result.
    amendment_instr = total_instr - total_eif
    assert total_ops == amendment_instr
    assert total_ops / amendment_instr == 1.0


# --------------------------------------------------------------------------- #
# Grafter ALINEA text recovery (goal 3: the re-examined Increment-1 residual)   #
# --------------------------------------------------------------------------- #


def test_grafter_recovers_parag_alinea_article_text() -> None:
    """Goal 3 re-examination, with evidence. The real FMX4 article body is
    <ARTICLE><PARAG><ALINEA>text — the Increment-1 grafter harvested ONLY <P>/<LIST>
    and so DROPPED all PARAG>ALINEA text (the Increment-1 "text preserved on the
    nested child" residual held ONLY for the PARAG>P fixture shape, not real bytes).
    The Increment-2 grafter harvests ALINEA — text is recovered on the paragraph
    child WITHOUT surfacing onto the section node (no duplication, conserved-apply
    unaffected, the full eu shard stays green)."""
    import tempfile
    from pathlib import Path as _P

    from lawvm.core.ir import IRNode
    from lawvm.eu.grafter import parse_eu_regulation_ir

    fmx = b"""<?xml version="1.0"?>
<ACT><TITLE><TI>t</TI></TITLE><ENACTING.TERMS>
  <ARTICLE><TI.ART>Article 2</TI.ART>
    <PARAG><NO.PARAG>1.</NO.PARAG><ALINEA>It shall be prohibited to export goods.</ALINEA></PARAG>
    <PARAG><NO.PARAG>2.</NO.PARAG><ALINEA><P>By way of derogation, the authority may grant.</P></ALINEA></PARAG>
  </ARTICLE>
</ENACTING.TERMS></ACT>"""
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=True) as tf:
        tf.write(fmx)
        tf.flush()
        st = parse_eu_regulation_ir(_P(tf.name), celex="32016R0044")

    def _section(node: IRNode, lbl: str) -> IRNode | None:
        if str(node.kind) == "section" and node.label == lbl:
            return node
        for c in node.children:
            hit = _section(c, lbl)
            if hit is not None:
                return hit
        return None

    art2 = _section(st.body, "2")
    assert art2 is not None
    # No duplication: the section node's OWN text stays empty (text lives on the
    # paragraph children); consumers recurse children to read the full body.
    assert (art2.text or "") == ""
    para_texts = [c.text or "" for c in art2.children]
    assert any("prohibited to export" in t for t in para_texts)  # ALINEA-direct
    assert any("derogation" in t for t in para_texts)  # ALINEA>P


# --------------------------------------------------------------------------- #
# Networked smoke (opt-in): the SAME coverage on the LIVE acquired bytes        #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    os.environ.get("LAWVM_EU_NETWORK_SMOKE") != "1",
    reason="acquires live ACT-root amenders over CELLAR REST; set LAWVM_EU_NETWORK_SMOKE=1",
)
def test_live_act_root_amender_coverage_smoke() -> None:
    """Acquire the real ACT-root amenders of 32016R0044 over the (recovered)
    CELLAR REST byte lane and measure grammar coverage on the LIVE bytes. The
    measured corpus result: 35/39 ENACTING.TERMS instructions lowered, the 4
    residuals are entry-into-force boilerplate (amendment-instruction cov 100%)."""
    import tempfile
    from datetime import datetime, timezone

    from farchive import Farchive

    from lawvm.eu import eu_acquire

    acts = ["32017R0488", "32017R0489", "32018R0870", "32019R1163"]
    with tempfile.TemporaryDirectory() as td:
        fa_path = os.path.join(td, "act_smoke.farchive")
        for celex in acts:
            try:
                eu_acquire.acquire_celex(
                    celex,
                    fetched_at=datetime.now(timezone.utc),
                    language="eng",
                    fmt="fmx4",
                    farchive_path=fa_path,
                )
            except Exception as exc:  # noqa: BLE001 — REST may still flap
                pytest.skip(f"CELLAR REST acquisition failed for {celex}: {exc}")
        fa = Farchive(fa_path)
        try:
            total_instr = total_ops = total_eif = 0
            for celex in acts:
                d = fa.get(f"cellar://celex/{celex}/enacted/eng/fmx4")
                if d is None:
                    pytest.skip(f"{celex} bytes absent (REST flap)")
                r = lower_amending_act(d, celex, base_celex=BASE_CELEX)
                total_instr += r.instruction_count
                total_ops += r.covered_count
                total_eif += sum(
                    1
                    for x in r.diagnostics
                    if x.rule_id
                    in (
                        "eu_fmx4_grammar_uncovered_instruction",
                        "eu_fmx4_grammar_non_amending_provision",
                    )
                    and "enter into force" in x.source_excerpt.lower()
                )
        finally:
            fa.close()
    # The measured live result (faithful to the pinned-fixture shapes).
    assert total_instr >= 35
    assert total_ops >= 30
    # Every uncovered instruction is the entry-into-force boilerplate.
    assert total_ops == total_instr - total_eif
