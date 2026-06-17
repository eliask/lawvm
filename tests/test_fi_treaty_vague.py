"""Gate for the treaty (SopS) + vague-OPEN reference recognizers (R7).

Covers FI_REFERENCE_CATALOGUE.md families ``treaty.sops`` (T1, EXACT) and
``vague.open_catchall`` (T3, OPEN — the tag-don't-guess boundary). The negative
case pins the boundary: a determinate self-ref must NOT be swept into OPEN.
"""
from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.references.treaty import recognize_treaty_refs
from lawvm.finland.references.vague import recognize_vague_refs


# --- treaty.sops (T1, EXACT) ------------------------------------------------


def test_parenthetical_sops_is_treaty_exact() -> None:
    mentions = recognize_treaty_refs("Yleissopimus (SopS 19/2020) tuli voimaan.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.TREATY
    assert m.cite_confidence is CiteConfidence.EXACT
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi:treaty:sops/2020/19"
    assert m.surface_text == "SopS 19/2020"


def test_bare_sops_form_matches() -> None:
    mentions = recognize_treaty_refs("ks. SopS 123/2003 sopimusta.")
    assert len(mentions) == 1
    assert mentions[0].target_provision_ref is not None
    assert mentions[0].target_provision_ref.statute_id == "fi:treaty:sops/2003/123"


def test_treaty_guard_returns_empty_without_sops() -> None:
    assert recognize_treaty_refs("Tässä laissa säädetään asiasta.") == []


def test_multiple_sops_in_document_order() -> None:
    mentions = recognize_treaty_refs("(SopS 19/2020) ja (SopS 20/2021)")
    assert [m.target_provision_ref.statute_id for m in mentions if m.target_provision_ref] == [
        "fi:treaty:sops/2020/19",
        "fi:treaty:sops/2021/20",
    ]


# --- vague.open_catchall (T3, OPEN) -----------------------------------------


def test_muussa_laissa_is_open_with_no_target() -> None:
    mentions = recognize_vague_refs("Asiasta säädetään muussa laissa säädetään mukaisesti.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_confidence is CiteConfidence.OPEN
    assert m.target_provision_ref is None
    assert m.surface_text == "muussa laissa säädetään"


def test_asianomaisessa_asetuksessa_is_open() -> None:
    mentions = recognize_vague_refs("noudatetaan asianomaisessa asetuksessa annettuja ohjeita")
    assert len(mentions) == 1
    assert mentions[0].cite_confidence is CiteConfidence.OPEN
    assert mentions[0].target_provision_ref is None


def test_erikseen_saadetaan_is_open() -> None:
    mentions = recognize_vague_refs("Tarkemmista seikoista erikseen säädetään valtioneuvoston päätöksellä.")
    assert len(mentions) == 1
    assert mentions[0].cite_confidence is CiteConfidence.OPEN


# --- NEGATIVE: determinate targets must NOT become OPEN ----------------------


def test_determinate_self_ref_does_not_match_vague() -> None:
    # "tämän lain 5 §:ssä" carries a determinate provision → self-ref lane,
    # NOT a vague OPEN. The closed list must leave it untouched.
    assert recognize_vague_refs("Kuten tämän lain 5 §:ssä säädetään, ...") == []


def test_determinate_by_name_does_not_match_vague() -> None:
    # A named act is the by-name lane, not OPEN.
    assert recognize_vague_refs("luonnonsuojelulaissa säädetään suojelusta") == []


def test_vague_guard_returns_empty_without_markers() -> None:
    assert recognize_vague_refs("Yleissopimus tuli voimaan vuonna 2020.") == []
