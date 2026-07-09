"""Hermetic tests for the draft-HE materialize + cross-bill conflict report.

The live report (``scripts/he_draft_materialize_report.py``) reads real corpus
PDFs and the replay corpus; these tests pin only its PURE, deterministic pieces —
the ``(statute, section)`` grouping that detects cross-bill collisions, the
section-label extraction, and the typed materialize-status skip decisions — so
the trustworthy-milestone contract cannot silently drift. No PDF, no network, no
data root: everything runs on constructed carriers.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from lawvm.core.source_document import (
    AssuranceTier,
    CandidateOperation,
    ConditionalBranch,
    ProposalAuthorityStatus,
    SourceAnchor,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.core.source_document.proposal import ProposalPackage

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "he_draft_materialize_report.py"
_spec = importlib.util.spec_from_file_location("he_draft_materialize_report", _SCRIPT)
assert _spec is not None and _spec.loader is not None
rep = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass(slots=True) can resolve the module dict.
sys.modules["he_draft_materialize_report"] = rep
_spec.loader.exec_module(rep)

_ANCHOR = SourceAnchor(artifact_digest="a" * 64, locator="//johtolause", page_num=1)


def _op(
    statute_id: str,
    provision_ref: str,
    *,
    action: str = "insert",
    tier: AssuranceTier = AssuranceTier.SINGLE_WITNESS,
) -> CandidateOperation:
    return CandidateOperation(
        action=action,
        target_statute_id=statute_id,
        target_provision_ref=provision_ref,
        payload_text="Uusi momentti.",
        source_anchor=_ANCHOR,
        assurance_tier=tier,
    )


def _branch(branch_id: str, *ops: CandidateOperation) -> ConditionalBranch:
    return ConditionalBranch(
        branch_id=branch_id,
        condition=f"{branch_id} enacted",
        candidate_ops=tuple(ops),
        authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
    )


def _package(proposal_id: str, *branches: ConditionalBranch) -> ProposalPackage:
    return ProposalPackage(
        proposal_id=proposal_id,
        source_manifestation_digests=("a" * 64,),
        branches=tuple(branches),
        reasoning_root=SourceDocumentNode(
            kind=SourceDocumentNodeKind.WORK_ROOT,
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
            anchor=_ANCHOR,
        ),
        authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
    )


# --------------------------------------------------------------------------- #
# Section-label extraction                                                     #
# --------------------------------------------------------------------------- #
def test_section_label_of_pulls_bare_section() -> None:
    assert rep._section_label_of("section:4/subsection:5") == "4"
    assert rep._section_label_of("section:30a") == "30a"


def test_section_label_of_empty_when_no_section() -> None:
    assert rep._section_label_of("subsection:5") == ""
    assert rep._section_label_of("") == ""


def test_dominant_section_is_first_resolvable() -> None:
    branch = _branch(
        "b1",
        _op("603/2006", "subsection:9"),  # no section → skipped
        _op("603/2006", "section:7/subsection:2"),
    )
    assert rep._dominant_section(branch) == "7"


# --------------------------------------------------------------------------- #
# Cross-bill collision grouping (the milestone's headline check)              #
# --------------------------------------------------------------------------- #
def test_two_bills_on_same_provision_collide() -> None:
    a = _package("A", _branch("A:draft", _op("703/2023", "section:4/subsection:5")))
    b = _package(
        "B", _branch("B:draft", _op("703/2023", "section:4", action="repeal"))
    )
    collisions = rep.cross_bill_collisions((("bill_a.pdf", a), ("bill_b.pdf", b)))
    assert len(collisions) == 1
    col = collisions[0]
    assert (col.statute_id, col.section_label) == ("703/2023", "4")
    assert set(col.bill_paths) == {"bill_a.pdf", "bill_b.pdf"}
    # Each proposing op is recorded with its action + assurance provenance.
    actions = {t[2] for t in col.touches}
    assert actions == {"insert", "repeal"}


def test_same_bill_touching_a_provision_twice_is_not_a_cross_bill_collision() -> None:
    # Two ops from ONE bill on one provision are an INTRA-bill duplicate
    # (diagnose_branch_conflicts owns that), NOT a cross-bill collision.
    a = _package(
        "A",
        _branch(
            "A:draft",
            _op("703/2023", "section:4"),
            _op("703/2023", "section:4", action="replace"),
        ),
    )
    collisions = rep.cross_bill_collisions((("bill_a.pdf", a),))
    assert collisions == ()


def test_different_sections_of_same_statute_do_not_collide() -> None:
    a = _package("A", _branch("A:draft", _op("703/2023", "section:4")))
    b = _package("B", _branch("B:draft", _op("703/2023", "section:7")))
    assert rep.cross_bill_collisions((("a.pdf", a), ("b.pdf", b))) == ()


def test_ops_without_section_or_statute_are_ignored_by_grouping() -> None:
    a = _package("A", _branch("A:draft", _op("", "section:4")))  # no statute
    b = _package("B", _branch("B:draft", _op("703/2023", "subsection:5")))  # no section
    assert rep.cross_bill_collisions((("a.pdf", a), ("b.pdf", b))) == ()


def test_collisions_are_deterministically_ordered() -> None:
    a = _package(
        "A",
        _branch(
            "A:draft",
            _op("703/2023", "section:7"),
            _op("111/2000", "section:1"),
        ),
    )
    b = _package(
        "B",
        _branch(
            "B:draft",
            _op("703/2023", "section:7"),
            _op("111/2000", "section:1"),
        ),
    )
    collisions = rep.cross_bill_collisions((("a.pdf", a), ("b.pdf", b)))
    keys = [(c.statute_id, c.section_label) for c in collisions]
    assert keys == sorted(keys)


# --------------------------------------------------------------------------- #
# Typed materialize-status skips (never a crash, never a fabricated diff)      #
# --------------------------------------------------------------------------- #
def _mat(branch: ConditionalBranch, *, have_data_root: bool) -> rep.BranchMaterialization:
    return asyncio.run(rep._materialize_branch(branch, have_data_root=have_data_root))


def test_no_statute_is_typed_skip() -> None:
    bm = _mat(_branch("b1", _op("", "section:4")), have_data_root=True)
    assert bm.status is rep.MaterializeStatus.SKIP_STATUTE_UNRESOLVED


def test_no_section_is_typed_skip() -> None:
    bm = _mat(_branch("b1", _op("603/2006", "subsection:5")), have_data_root=True)
    assert bm.status is rep.MaterializeStatus.SKIP_NO_SECTION


def test_no_data_root_is_typed_skip_not_a_crash() -> None:
    bm = _mat(_branch("b1", _op("603/2006", "section:4")), have_data_root=False)
    assert bm.status is rep.MaterializeStatus.SKIP_NO_DATA_ROOT
    # The branch's assurance profile is still published on the skip.
    assert bm.assurance_tiers == ("single_witness",)


def test_status_counts_bucket_every_branch() -> None:
    bill = rep.BillReport(
        rel_path="x/he/a.pdf",
        proposal_id="A",
        n_branches=2,
        lowering_findings=(),
        branch_mats=(
            rep.BranchMaterialization(
                branch_id="A:draft",
                target_statute_id="603/2006",
                section_label="4",
                n_ops=1,
                assurance_tiers=("single_witness",),
                status=rep.MaterializeStatus.MATERIALIZED,
                diff_lines=("gains unit 5",),
            ),
            rep.BranchMaterialization(
                branch_id="A:draft:law2",
                target_statute_id="",
                section_label="",
                n_ops=0,
                assurance_tiers=(),
                status=rep.MaterializeStatus.SKIP_STATUTE_UNRESOLVED,
            ),
        ),
    )
    report = rep.MaterializeReport(bills=(bill,))
    counts = report.status_counts
    assert counts["materialized"] == 1
    assert counts["skip_statute_unresolved"] == 1
