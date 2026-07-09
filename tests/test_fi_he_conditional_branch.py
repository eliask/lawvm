"""Draft-HE → ConditionalBranch ("if enacted, then …") extraction.

Hermetic tests drive a synthetic reading-order text that mimics a real HE
lakiehdotus (no external file, no PDF lib): the operative johtolause lowers to a
candidate INSERT op on a NON-authoritative branch; the perustelut become the
bound reasoning attachment; a non-HE document (määräys / muistio) yields zero ops
plus a finding, never a hallucinated op. One live test runs the real ``vm045``
draft PDF end to end (skipped if the corpus is absent).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.source_document import (
    AssuranceTier,
    ConditionalBranch,
    ProposalAuthorityStatus,
    SourceAnchor,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.finland.source_document import (
    HeDocKind,
    classify_he_document,
    extract_conditional_branch,
)
from lawvm.finland.source_document.he_draft import _operative_from_text

# A draft-HE PDF for the live e2e — supplied via env (never a committed abs path
# or a vendored blob). The real reproducible source is the lausuntopalvelu
# acquisition lane; set LAWVM_HE_SAMPLE_PDF to a local draft to run these.
_HE_PDF = Path(os.environ.get("LAWVM_HE_SAMPLE_PDF") or "/nonexistent/no-he-sample.pdf")

_DIGEST = "a" * 64


def _root(texts: tuple[str, ...]) -> SourceDocumentNode:
    anchor = SourceAnchor(artifact_digest=_DIGEST, locator="manifestation")
    children = tuple(
        SourceDocumentNode(
            kind=SourceDocumentNodeKind.PARAGRAPH,
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
            anchor=SourceAnchor(artifact_digest=_DIGEST, locator=f"p={i}", page_num=i),
            text=t,
        )
        for i, t in enumerate(texts)
    )
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.WORK_ROOT,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=anchor,
        children=children,
    )


# A synthetic reading-order text of a single-law HE (mirrors vm045's shape).
_HE_READING_ORDER = (
    "Hallituksen esitys eduskunnalle laiksi ... 4 §:n muuttamisesta\n\n"
    "ESITYKSEN PÄÄASIALLINEN SISÄLTÖ\n"
    "Esityksessä ehdotetaan muutettavaksi ... annettua lakia.\n\n"
    "PERUSTELUT\n1 Asian tausta ja valmistelu\n... reasoning ...\n\n"
    "Ponsi\n"
    "Edellä esitetyn perusteella annetaan eduskunnan hyväksyttäväksi seuraava lakiehdotus:\n\n"
    "Lakiehdotus\nLaki\n"
    "maataloudessa käytettyjen eräiden energiatuotteiden valmisteveron palautuksesta "
    "annetun lain 4 §:n muuttamisesta\n\n"
    "Eduskunnan päätöksen mukaisesti\n"
    "lisätään maataloudessa käytettyjen eräiden energiatuotteiden valmisteveron palautuksesta "
    "annetun lain (603/2006) 4 §:ään, sellaisena kuin se on laeissa 247/2018, 1227/2018, "
    "1217/2021 ja 370/2022, uusi 5 momentti seuraavasti:\n\n"
    "4 §\nVeronpalautuksen määrä\n"
    "Sen lisäksi, mitä 1 momentissa säädetään, hakijalle palautetaan valmisteveroa verovuoden "
    "2025 ja 2026 aikana maataloudessa käytetystä kevyestä polttoöljystä 4 senttiä litralta.\n\n"
    "Tämä laki tulee voimaan päivänä kuuta 20 .\n"
    "—————\nHelsingissä x.x.20xx\nPääministeri Petteri Orpo\n"
)


def test_operative_recovers_johtolause_statute_payload_commencement() -> None:
    op = _operative_from_text(_HE_READING_ORDER)
    assert op is not None
    assert "eduskunnan päätöksen mukaisesti" in op.clause.lower()
    assert op.clause.strip().endswith("seuraavasti:")
    assert op.target_statute_id == "603/2006"
    assert "hakijalle palautetaan" in op.payload_text
    assert op.commencement == "Tämä laki tulee voimaan päivänä kuuta 20 ."  # no signature lines


def test_extract_conditional_branch_insert_op() -> None:
    pkg = extract_conditional_branch(
        _root(("some ingested block",)),
        "fi:he:VM045:00/2026",
        reading_order_text=_HE_READING_ORDER,
    )
    assert pkg.findings == ()
    assert pkg.authority_status is ProposalAuthorityStatus.CONSULTATION_DRAFT
    assert pkg.replay_authorized is False
    assert len(pkg.branches) == 1  # a single-law bill is a 1-tuple
    branch = pkg.branches[0]
    assert branch.replay_authorized is False
    assert "VM045:00/2026 enacted" in branch.condition
    assert branch.commencement.startswith("Tämä laki tulee voimaan")
    assert len(branch.candidate_ops) == 1
    op = branch.candidate_ops[0]
    assert op.action == "insert"
    assert op.target_statute_id == "603/2006"
    assert "4" in op.target_provision_ref and "5" in op.target_provision_ref  # §4, momentti 5
    assert op.assurance_tier is AssuranceTier.SINGLE_WITNESS  # lone deterministic parse
    # The reasoning attachment is a bound, non-operative esityöt subtree.
    assert pkg.reasoning_root.attrs.get("role") == "esityot_reasoning"


# A synthetic reading-order text of a MULTI-law HE: a LAKIEHDOTUKSET section
# with TWO "Laki … muuttamisesta / Eduskunnan päätöksen mukaisesti …" blocks,
# each with its own target statute id and voimaantulo.
_HE_TWO_LAWS_READING_ORDER = (
    "Hallituksen esitys eduskunnalle laeiksi kahden lain muuttamisesta\n\n"
    "ESITYKSEN PÄÄASIALLINEN SISÄLTÖ\n"
    "Esityksessä ehdotetaan muutettavaksi kahta lakia.\n\n"
    "PERUSTELUT\n1 Asian tausta ja valmistelu\n... reasoning shared by both laws ...\n\n"
    "LAKIEHDOTUKSET\n\n"
    "1.\nLaki\nensimmäisen lain muuttamisesta\n\n"
    "Eduskunnan päätöksen mukaisesti\n"
    "lisätään ensimmäisestä laista (603/2006) annetun lain 4 §:ään uusi 5 momentti "
    "seuraavasti:\n\n"
    "4 §\nEnsimmäinen pykälä\n"
    "Ensimmäisen lain uusi momentti.\n\n"
    "Tämä laki tulee voimaan päivänä kuuta 20 .\n\n"
    "2.\nLaki\ntoisen lain muuttamisesta\n\n"
    "Eduskunnan päätöksen mukaisesti\n"
    "muutetaan toisesta laista (999/2015) annetun lain 7 § seuraavasti:\n\n"
    "7 §\nToinen pykälä\n"
    "Toisen lain uusi sanamuoto.\n\n"
    "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027.\n"
    "—————\nHelsingissä x.x.20xx\nPääministeri Petteri Orpo\n"
)


def test_operatives_from_text_splits_two_laws() -> None:
    from lawvm.finland.source_document.he_draft import _operatives_from_text

    ops = _operatives_from_text(_HE_TWO_LAWS_READING_ORDER)
    assert len(ops) == 2
    assert ops[0].target_statute_id == "603/2006"
    assert ops[1].target_statute_id == "999/2015"
    # Each law's region carries ITS OWN johtolause clause and voimaantulo.
    assert "603/2006" in ops[0].clause and "999/2015" not in ops[0].clause
    assert "999/2015" in ops[1].clause and "603/2006" not in ops[1].clause
    assert ops[0].commencement.startswith("Tämä laki tulee voimaan päivänä kuuta")
    assert ops[1].commencement == "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027."


def test_extract_conditional_branch_multi_law_two_branches() -> None:
    pkg = extract_conditional_branch(
        _root(("some ingested block",)),
        "fi:he:VM099:00/2026",
        reading_order_text=_HE_TWO_LAWS_READING_ORDER,
    )
    assert pkg.replay_authorized is False
    assert len(pkg.branches) == 2  # one ConditionalBranch per lakiehdotus law
    b0, b1 = pkg.branches
    assert b0.replay_authorized is False and b1.replay_authorized is False
    # Distinct per-law branch ids and distinct per-law targets.
    assert b0.branch_id != b1.branch_id
    assert {op.target_statute_id for op in b0.candidate_ops} == {"603/2006"}
    assert {op.target_statute_id for op in b1.candidate_ops} == {"999/2015"}
    assert b0.candidate_ops and b1.candidate_ops
    # Per-law commencement travels with its branch.
    assert b1.commencement == "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027."
    # One shared esityöt reasoning attachment for the whole proposal.
    assert pkg.reasoning_root.attrs.get("role") == "esityot_reasoning"


# A wholly new act: ``Eduskunnan päätöksen mukaisesti säädetään:`` (no
# ``seuraavasti:`` amendment clause). The ``(1535/1992)`` in the body is a
# cross-reference — NOT an amended target. (Corpus sweep CAT-3, vm082.)
_HE_NEW_ACT_READING_ORDER = (
    "Lakiehdotus\nLaki\nvetymarkkinoista\n\n"
    "Eduskunnan päätöksen mukaisesti säädetään:\n\n"
    "1 §\nSoveltamisala\n"
    "Tätä lakia sovelletaan tuloverolaissa (1535/1992) tarkoitettuun toimintaan.\n\n"
    "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027.\n"
)


def test_new_act_enactment_does_not_misattribute_a_body_cross_reference() -> None:
    op = _operative_from_text(_HE_NEW_ACT_READING_ORDER)
    assert op is not None
    assert op.is_new_act is True
    # The (1535/1992) in the body is a cross-reference, never the amended target.
    assert op.target_statute_id == ""


def test_new_act_yields_finding_not_misattributed_ops() -> None:
    pkg = extract_conditional_branch(
        _root(("some ingested block",)),
        "fi:he:VM082:00/2026",
        reading_order_text=_HE_NEW_ACT_READING_ORDER,
    )
    assert any("new-act" in f.lower() for f in pkg.findings)
    # A new act is not modelled as amendment ops — no misattributed target op.
    assert all(len(b.candidate_ops) == 0 for b in pkg.branches)


def test_producer_page_coverage_split_is_a_distinct_finding() -> None:
    # pdfplumber ingest (root) classified HE_BILL (it saw the johtolause) but the
    # reading-order text is truncated and carries none → a page-coverage split,
    # NOT "operative text unavailable". (Corpus sweep CAT-1, stm107 / tem038.)
    root = _root(("Eduskunnan päätöksen mukaisesti lisätään uusi 5 momentti ...",))
    pkg = extract_conditional_branch(
        root,
        "fi:he:STM107:00/2026",
        reading_order_text="Sisällys\nJohdanto, johtolause on vasta sivulla 301.\n",
    )
    assert pkg.branches == ()
    assert any("page-coverage split" in f for f in pkg.findings)


def test_a_draft_branch_can_never_be_replay_authorized() -> None:
    with pytest.raises(ValueError):
        ConditionalBranch(
            branch_id="fi:he:X:draft",
            condition="X enacted",
            candidate_ops=(),
            authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
            replay_authorized=True,  # forbidden
        )


def test_classify_he_vs_maarays_vs_muistio() -> None:
    assert classify_he_document(_root(("Eduskunnan päätöksen mukaisesti lisätään ...",))) is HeDocKind.HE_BILL
    assert classify_he_document(
        _root(("Määräys teletoiminnan häiriötilanteista", "säädetään ..."))
    ) is HeDocKind.MAARAYS
    assert classify_he_document(
        _root(("Esittelymuistio", "Taustaa ja nykytila ..."))
    ) is HeDocKind.MUISTIO


def test_non_he_document_yields_no_ops_but_a_finding() -> None:
    # A memo with no johtolause: reasoning-only, zero ops, honest finding.
    pkg = extract_conditional_branch(
        _root(("Esittelymuistio", "pelkkää taustaa, ei lakiehdotusta")),
        "fi:muistio:STM045:00/2026",
        reading_order_text="Esittelymuistio\nTaustaa ja nykytila, ei johtolausetta.\n",
    )
    assert pkg.branches == ()  # a non-HE document is the empty branch tuple
    assert any("muistio" in f.lower() or "no johtolause" in f.lower() for f in pkg.findings)
    assert pkg.replay_authorized is False


# --------------------------------------------------------------------------- #
# Live end-to-end on the real vm045 draft HE PDF (skipped if corpus absent)     #
# --------------------------------------------------------------------------- #



@pytest.mark.skipif(not _HE_PDF.exists(), reason="set LAWVM_HE_SAMPLE_PDF to a draft-HE PDF")
def test_live_vm045_pdf_to_conditional_branch() -> None:
    import hashlib
    from datetime import datetime

    from lawvm.core.source_document import SourceManifestation
    from lawvm.finland.source_document import (
        ingest_pdf_manifestation,
        reading_order_text_from_pdf,
    )

    b = _HE_PDF.read_bytes()
    m = SourceManifestation(
        artifact_digest=hashlib.sha256(b).hexdigest(),
        source_bytes=b,
        locator="vm045/he_luonnos.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 5, 20),
        media_type="application/pdf",
    )
    res = ingest_pdf_manifestation(m, max_pages=12)
    rot = reading_order_text_from_pdf(b, max_pages=12)
    pkg = extract_conditional_branch(
        res.root,
        "fi:he:VM045:00/2026",
        reading_order_text=rot,
        source_manifestation_digests=(m.artifact_digest,),
    )
    assert pkg.findings == ()
    assert len(pkg.branches) == 1
    assert len(pkg.branches[0].candidate_ops) == 1
    op = pkg.branches[0].candidate_ops[0]
    assert op.action == "insert"
    assert op.target_statute_id == "603/2006"
    assert pkg.branches[0].replay_authorized is False
    assert pkg.reasoning_root.attrs.get("role") == "esityot_reasoning"


@pytest.mark.network
@pytest.mark.skipif(not _HE_PDF.exists(), reason="set LAWVM_HE_SAMPLE_PDF to a draft-HE PDF")
def test_live_vm045_adjudicated_op_reaches_multi_witness() -> None:
    """pdfplumber (scrambled) + reading-order, adjudicated live → clean assurance.

    The reading-order-scramble on the bill page is a genuine two-producer case:
    when the local adjudicator confirms both independently carry the same
    johtolause, the candidate op is promoted to MULTI_WITNESS_ADJUDICATED.
    """
    import hashlib
    from datetime import datetime

    from lawvm.core.source_document import SourceManifestation
    from lawvm.finland.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator
    from lawvm.finland.source_document import he_pdf_to_proposal

    adjudicator = LlmWorkflowAdjudicator(verify_pass=False, max_tokens=700)
    if not adjudicator.is_available():
        pytest.skip("no llama.cpp server at :8080")
    b = _HE_PDF.read_bytes()
    m = SourceManifestation(
        artifact_digest=hashlib.sha256(b).hexdigest(),
        source_bytes=b,
        locator="vm045/he_luonnos.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 5, 20),
        media_type="application/pdf",
    )
    pkg = he_pdf_to_proposal(m, "fi:he:VM045:00/2026", adjudicator=adjudicator, max_pages=12)
    assert len(pkg.branches) == 1
    assert len(pkg.branches[0].candidate_ops) == 1
    op = pkg.branches[0].candidate_ops[0]
    assert op.action == "insert" and op.target_statute_id == "603/2006"
    # Two independent producers corroborated → clean, multi-witness assurance.
    assert op.assurance_tier is AssuranceTier.MULTI_WITNESS_ADJUDICATED
    assert op.assurance_tier.admits_clean_text_state
