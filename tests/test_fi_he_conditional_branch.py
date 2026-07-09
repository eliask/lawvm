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
_HE_PDF = Path(os.environ["LAWVM_HE_SAMPLE_PDF"]) if os.environ.get("LAWVM_HE_SAMPLE_PDF") else None

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
    branch = pkg.branch
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
    assert pkg.branch.candidate_ops == ()
    assert any("muistio" in f.lower() or "no johtolause" in f.lower() for f in pkg.findings)
    assert pkg.replay_authorized is False


# --------------------------------------------------------------------------- #
# Live end-to-end on the real vm045 draft HE PDF (skipped if corpus absent)     #
# --------------------------------------------------------------------------- #



@pytest.mark.skipif(not (_HE_PDF is not None and _HE_PDF.exists()), reason="set LAWVM_HE_SAMPLE_PDF to a draft-HE PDF")
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
    assert len(pkg.branch.candidate_ops) == 1
    op = pkg.branch.candidate_ops[0]
    assert op.action == "insert"
    assert op.target_statute_id == "603/2006"
    assert pkg.branch.replay_authorized is False
    assert pkg.reasoning_root.attrs.get("role") == "esityot_reasoning"


@pytest.mark.network
@pytest.mark.skipif(not (_HE_PDF is not None and _HE_PDF.exists()), reason="set LAWVM_HE_SAMPLE_PDF to a draft-HE PDF")
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
    assert len(pkg.branch.candidate_ops) == 1
    op = pkg.branch.candidate_ops[0]
    assert op.action == "insert" and op.target_statute_id == "603/2006"
    # Two independent producers corroborated → clean, multi-witness assurance.
    assert op.assurance_tier is AssuranceTier.MULTI_WITNESS_ADJUDICATED
    assert op.assurance_tier.admits_clean_text_state
