"""Branch-conflict diagnostics over draft-HE ProposalPackages (frontier #3).

Exercises the PURE checks (DUPLICATE_TARGET, TARGET_STATUTE_UNKNOWN) end to end
and pins the honest-gap contract: the existence / drift checks are reported as
``unchecked`` markers, never as a clean pass.
"""
from __future__ import annotations

from lawvm.core.source_document import (
    AssuranceTier,
    CandidateOperation,
    ConditionalBranch,
    ProposalAuthorityStatus,
    SourceAnchor,
)
from lawvm.finland.source_document.branch_conflicts import (
    BranchConflictFinding,
    BranchConflictKind,
    BranchConflictSeverity,
    diagnose_branch_conflicts,
)

_DIGEST = "sha256:" + "0" * 64


def _op(
    statute_id: str,
    provision_ref: str,
    *,
    action: str = "insert",
) -> CandidateOperation:
    return CandidateOperation(
        action=action,
        target_statute_id=statute_id,
        target_provision_ref=provision_ref,
        payload_text="Uusi momentti.",
        source_anchor=SourceAnchor(artifact_digest=_DIGEST, locator="//section[4]"),
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
    )


def _branch(branch_id: str, *ops: CandidateOperation) -> ConditionalBranch:
    return ConditionalBranch(
        branch_id=branch_id,
        condition=f"{branch_id} enacted as introduced",
        candidate_ops=tuple(ops),
        authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
    )


def _kinds(findings: tuple[BranchConflictFinding, ...]) -> set[BranchConflictKind]:
    return {f.kind for f in findings}


def _of_kind(
    findings: tuple[BranchConflictFinding, ...], kind: BranchConflictKind
) -> tuple[BranchConflictFinding, ...]:
    return tuple(f for f in findings if f.kind is kind)


def test_duplicate_target_within_a_single_branch() -> None:
    branch = _branch(
        "b1",
        _op("603/2006", "section:4/subsection:5"),
        _op("603/2006", "section:4/subsection:5", action="replace"),
    )
    findings = diagnose_branch_conflicts(branch)

    dups = _of_kind(findings, BranchConflictKind.DUPLICATE_TARGET)
    assert len(dups) == 1
    dup = dups[0]
    assert dup.severity is BranchConflictSeverity.BLOCKING
    assert dup.target == ("603/2006", "section:4/subsection:5")
    assert dup.branch_ids == ("b1", "b1")


def test_duplicate_target_across_branches_in_a_package() -> None:
    branches = [
        _branch("b1", _op("603/2006", "section:4")),
        _branch("b2", _op("603/2006", "section:4", action="repeal")),
    ]
    findings = diagnose_branch_conflicts(branches)

    dups = _of_kind(findings, BranchConflictKind.DUPLICATE_TARGET)
    assert len(dups) == 1
    assert set(dups[0].branch_ids) == {"b1", "b2"}
    assert dups[0].severity is BranchConflictSeverity.BLOCKING


def test_distinct_provisions_do_not_collide() -> None:
    branch = _branch(
        "b1",
        _op("603/2006", "section:4/subsection:5"),
        _op("603/2006", "section:7"),
    )
    findings = diagnose_branch_conflicts(branch)
    assert not _of_kind(findings, BranchConflictKind.DUPLICATE_TARGET)


def test_empty_statute_id_is_target_statute_unknown() -> None:
    branch = _branch("b1", _op("", "section:4"))
    findings = diagnose_branch_conflicts(branch)

    unknown = _of_kind(findings, BranchConflictKind.TARGET_STATUTE_UNKNOWN)
    assert len(unknown) == 1
    assert unknown[0].severity is BranchConflictSeverity.BLOCKING
    assert unknown[0].branch_ids == ("b1",)
    # An empty-statute op is NOT double-counted as a collision or a gap marker.
    assert not _of_kind(findings, BranchConflictKind.DUPLICATE_TARGET)
    assert not _of_kind(findings, BranchConflictKind.TARGET_EXISTENCE_UNCHECKED)


def test_malformed_statute_id_is_target_statute_unknown() -> None:
    branch = _branch("b1", _op("not-a-statute", "section:4"))
    findings = diagnose_branch_conflicts(branch)

    unknown = _of_kind(findings, BranchConflictKind.TARGET_STATUTE_UNKNOWN)
    assert len(unknown) == 1
    assert "not-a-statute" in unknown[0].detail
    assert unknown[0].severity is BranchConflictSeverity.BLOCKING


def test_engine_ordering_statute_id_is_well_formed() -> None:
    # Engine year/num ordering ('2006/603') is a well-formed säädös id too.
    branch = _branch("b1", _op("2006/603", "section:4"))
    findings = diagnose_branch_conflicts(branch)
    assert not _of_kind(findings, BranchConflictKind.TARGET_STATUTE_UNKNOWN)


def test_clean_single_op_has_no_blocking_findings() -> None:
    branch = _branch("b1", _op("603/2006", "section:4/subsection:5"))
    findings = diagnose_branch_conflicts(branch)

    blocking = tuple(
        f for f in findings if f.severity is BranchConflictSeverity.BLOCKING
    )
    assert blocking == ()


def test_clean_target_still_emits_honest_unchecked_gaps() -> None:
    # A clean, well-formed target is NOT silently blessed: existence + drift are
    # reported as explicit unchecked gaps naming the missing input.
    branch = _branch("b1", _op("603/2006", "section:4/subsection:5"))
    findings = diagnose_branch_conflicts(branch)

    kinds = _kinds(findings)
    assert BranchConflictKind.TARGET_EXISTENCE_UNCHECKED in kinds
    assert BranchConflictKind.TARGET_CHANGED_SINCE_DRAFT_BASE in kinds

    for kind in (
        BranchConflictKind.TARGET_EXISTENCE_UNCHECKED,
        BranchConflictKind.TARGET_CHANGED_SINCE_DRAFT_BASE,
    ):
        (finding,) = _of_kind(findings, kind)
        assert finding.severity is BranchConflictSeverity.UNCHECKED
        assert finding.target == ("603/2006", "section:4/subsection:5")


def test_malformed_target_gets_no_existence_gap_marker() -> None:
    # Existence/drift gaps are emitted only for well-formed ids; a malformed id
    # is convicted outright, not merely "unchecked".
    branch = _branch("b1", _op("junk", "section:4"))
    findings = diagnose_branch_conflicts(branch)
    assert not _of_kind(findings, BranchConflictKind.TARGET_EXISTENCE_UNCHECKED)
    assert not _of_kind(findings, BranchConflictKind.TARGET_CHANGED_SINCE_DRAFT_BASE)


def test_accepts_iterable_and_single_branch_forms() -> None:
    op = _op("603/2006", "section:4")
    single = diagnose_branch_conflicts(_branch("b1", op))
    as_list = diagnose_branch_conflicts([_branch("b1", op)])
    # Same finding kinds regardless of carrier form.
    assert _kinds(single) == _kinds(as_list)


def test_finding_rejects_bad_kind_and_severity() -> None:
    import pytest

    with pytest.raises(TypeError):
        BranchConflictFinding(
            kind="duplicate_target",  # type: ignore[arg-type]
            detail="x",
            severity=BranchConflictSeverity.INFO,
        )
    with pytest.raises(ValueError):
        BranchConflictFinding(
            kind=BranchConflictKind.DUPLICATE_TARGET,
            detail="",
            severity=BranchConflictSeverity.BLOCKING,
        )
