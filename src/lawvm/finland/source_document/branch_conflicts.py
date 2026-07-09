"""Branch conflict diagnostics for draft-HE ProposalPackages (frontier #3).

A ``ProposalPackage`` is a bundle of ``ConditionalBranch`` overlays ("if
enacted, then …"), each carrying ``CandidateOperation`` targets. Before any of
that is materialized against the enacted timeline (a SEPARATE track — see the
proposal carrier's non-authorization invariant in
``lawvm.core.source_document.proposal``), we can already surface a class of
conflicts *statically*, from the carriers alone:

  * two proposed ops that hit the SAME (statute, provision) — a collision the
    reader must adjudicate (which one wins? did the drafter double-target?);
  * a target statute id that is empty or not a well-formed Finnish säädös id.

This module owns ONLY the checks that are honest at the carrier layer. It draws
a bright line between what it can *prove* and what it *cannot*:

  * DUPLICATE_TARGET / TARGET_STATUTE_UNKNOWN are PURE — no corpus, no data
    root, no materialization; a function of the package's own bytes.

  * Whether the amended statute/provision actually *exists* in the corpus, and
    whether it *changed since the draft's base*, cannot be answered without
    loading corpus data (existence needs the statute-name/lifecycle registry
    artifact under ``LAWVM_CANONICAL_DATA_ROOT``; provision existence needs the
    materialized statute tree; drift needs the draft's base date + the statute's
    amendment history). Rather than fake those, this module emits explicit,
    typed ``unchecked`` findings (``TARGET_EXISTENCE_UNCHECKED`` /
    ``TARGET_CHANGED_SINCE_DRAFT_BASE``) that name the missing input. An
    unchecked finding is an honest gap marker, never a clean bill of health.

Discipline (AGENTS.md §1.9, §1.10, §12): typed frozen carriers; Finnish
statute-id knowledge stays in ``finland/``; no silent degradation — a check we
cannot run is reported as unrun, not as passed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Union

from typing_extensions import override

from lawvm.core.source_document.proposal import (
    ConditionalBranch,
    ProposalPackage,
)
from lawvm.finland.statute_id import looks_like_statute_id


class BranchConflictSeverity(Enum):
    """How a finding should be treated by a downstream reader/gate.

    ``BLOCKING`` names a conflict a human must adjudicate before the proposal is
    trusted; ``INFO`` is advisory; ``UNCHECKED`` marks a check we could not run
    for lack of an input (an honest gap — NOT evidence of no conflict).
    """

    BLOCKING = "blocking"
    INFO = "info"
    UNCHECKED = "unchecked"

    @override
    def __str__(self) -> str:
        return self.value


class BranchConflictKind(Enum):
    """The taxonomy of branch-conflict findings this module can emit."""

    DUPLICATE_TARGET = "duplicate_target"
    """Two candidate ops target the same (statute_id, provision_ref). PURE."""
    TARGET_STATUTE_UNKNOWN = "target_statute_unknown"
    """Target statute id empty or not a well-formed Finnish säädös id. PURE."""
    TARGET_EXISTENCE_UNCHECKED = "target_existence_unchecked"
    """Statute/provision existence not verified (needs corpus data). GAP."""
    TARGET_CHANGED_SINCE_DRAFT_BASE = "target_changed_since_draft_base"
    """Target may have drifted since the draft's base (needs history). GAP."""

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BranchConflictFinding:
    """A single typed observation about a ProposalPackage's branch targets.

    ``kind`` is the taxonomy entry; ``detail`` is a human-readable explanation
    naming the offending target(s); ``severity`` is how a reader should treat
    it. ``branch_ids`` names the branch(es) the finding implicates (empty when
    package-wide), and ``target`` is the ``(statute_id, provision_ref)`` pair the
    finding is about (``("", "")`` when not target-specific).
    """

    kind: BranchConflictKind
    detail: str
    severity: BranchConflictSeverity
    branch_ids: tuple[str, ...] = ()
    target: tuple[str, str] = ("", "")

    def __post_init__(self) -> None:
        if not isinstance(self.kind, BranchConflictKind):
            raise TypeError("BranchConflictFinding.kind must be a BranchConflictKind")
        if not isinstance(self.severity, BranchConflictSeverity):
            raise TypeError(
                "BranchConflictFinding.severity must be a BranchConflictSeverity"
            )
        if not self.detail:
            raise ValueError("BranchConflictFinding.detail must be non-empty")


# A caller may hand us the whole package or just its branches (or one branch).
PackageOrBranches = Union[
    ProposalPackage,
    ConditionalBranch,
    Iterable[ConditionalBranch],
]


def _branches_of(package_or_branches: PackageOrBranches) -> tuple[ConditionalBranch, ...]:
    """Normalize the flexible input to a tuple of ConditionalBranch."""
    if isinstance(package_or_branches, ProposalPackage):
        return package_or_branches.branches
    if isinstance(package_or_branches, ConditionalBranch):
        return (package_or_branches,)
    branches = tuple(package_or_branches)
    if not all(isinstance(b, ConditionalBranch) for b in branches):
        raise TypeError(
            "diagnose_branch_conflicts expects a ProposalPackage, a ConditionalBranch, "
            "or an iterable of ConditionalBranch"
        )
    return branches


def _duplicate_target_findings(
    branches: tuple[ConditionalBranch, ...],
) -> tuple[BranchConflictFinding, ...]:
    """DUPLICATE_TARGET — same (statute_id, provision_ref) hit more than once.

    PURE. Collisions are detected across the WHOLE package (within a branch AND
    across branches): two branches that both amend the same provision are a
    conflict the reader must resolve. Empty-statute ops are skipped here — they
    are already reported by TARGET_STATUTE_UNKNOWN, and a pair of
    empty-statute ops is not a meaningful collision.
    """
    # Map target -> ordered list of branch_ids that carry an op against it.
    seen: dict[tuple[str, str], list[str]] = {}
    for branch in branches:
        for op in branch.candidate_ops:
            if not op.target_statute_id:
                continue
            key = (op.target_statute_id, op.target_provision_ref)
            seen.setdefault(key, []).append(branch.branch_id)

    findings: list[BranchConflictFinding] = []
    for (statute_id, provision_ref), branch_ids in seen.items():
        if len(branch_ids) < 2:
            continue
        provision_desc = provision_ref or "(statute root)"
        findings.append(
            BranchConflictFinding(
                kind=BranchConflictKind.DUPLICATE_TARGET,
                detail=(
                    f"{len(branch_ids)} candidate ops target the same provision "
                    f"{statute_id} / {provision_desc} "
                    f"(branch ops: {', '.join(branch_ids)}); a reader must "
                    "adjudicate which proposed change governs."
                ),
                severity=BranchConflictSeverity.BLOCKING,
                branch_ids=tuple(branch_ids),
                target=(statute_id, provision_ref),
            )
        )
    return tuple(findings)


def _statute_unknown_findings(
    branches: tuple[ConditionalBranch, ...],
) -> tuple[BranchConflictFinding, ...]:
    """TARGET_STATUTE_UNKNOWN — empty or malformed Finnish statute id. PURE.

    A Finnish säädös id is a two-component ``num/year`` (or engine ``year/num``)
    id; :func:`looks_like_statute_id` accepts both orderings. Anything else
    (empty, bare number, junk) cannot name a real amended statute.
    """
    findings: list[BranchConflictFinding] = []
    for branch in branches:
        for op in branch.candidate_ops:
            statute_id = op.target_statute_id
            if not statute_id:
                findings.append(
                    BranchConflictFinding(
                        kind=BranchConflictKind.TARGET_STATUTE_UNKNOWN,
                        detail=(
                            f"branch {branch.branch_id!r}: a candidate "
                            f"{op.action!r} op names no target statute "
                            "(target_statute_id is empty / unresolved)."
                        ),
                        severity=BranchConflictSeverity.BLOCKING,
                        branch_ids=(branch.branch_id,),
                        target=("", op.target_provision_ref),
                    )
                )
            elif not looks_like_statute_id(statute_id):
                findings.append(
                    BranchConflictFinding(
                        kind=BranchConflictKind.TARGET_STATUTE_UNKNOWN,
                        detail=(
                            f"branch {branch.branch_id!r}: target statute id "
                            f"{statute_id!r} is not a well-formed Finnish säädös "
                            "id (expected num/year or year/num, e.g. '603/2006')."
                        ),
                        severity=BranchConflictSeverity.BLOCKING,
                        branch_ids=(branch.branch_id,),
                        target=(statute_id, op.target_provision_ref),
                    )
                )
    return tuple(findings)


def _existence_unchecked_findings(
    branches: tuple[ConditionalBranch, ...],
) -> tuple[BranchConflictFinding, ...]:
    """TARGET_EXISTENCE_UNCHECKED — documented gap, per well-formed target.

    Statute-level existence would need the statute-name / lifecycle registry
    artifact (``LAWVM_CANONICAL_DATA_ROOT``); provision-level existence would
    need the statute materialized as-of the base date (the separate track). No
    PURE, static-registry check for either exists at this layer, so we do NOT
    fabricate a verdict — we emit an ``unchecked`` marker naming exactly what is
    missing. Emitted only for targets that ARE well-formed (a malformed id is
    already convicted by TARGET_STATUTE_UNKNOWN; re-flagging it as merely
    "unchecked" would understate the problem).
    """
    findings: list[BranchConflictFinding] = []
    for branch in branches:
        for op in branch.candidate_ops:
            statute_id = op.target_statute_id
            if not statute_id or not looks_like_statute_id(statute_id):
                continue
            provision_desc = op.target_provision_ref or "(statute root)"
            findings.append(
                BranchConflictFinding(
                    kind=BranchConflictKind.TARGET_EXISTENCE_UNCHECKED,
                    detail=(
                        f"branch {branch.branch_id!r}: existence of amended target "
                        f"{statute_id} / {provision_desc} was NOT verified. "
                        "Statute-level existence needs the statute-name/lifecycle "
                        "registry artifact (LAWVM_CANONICAL_DATA_ROOT); "
                        "provision-level existence needs the statute materialized "
                        "as-of the draft base (a separate track). No pure "
                        "static-registry check exists at the carrier layer."
                    ),
                    severity=BranchConflictSeverity.UNCHECKED,
                    branch_ids=(branch.branch_id,),
                    target=(statute_id, op.target_provision_ref),
                )
            )
    return tuple(findings)


def _drift_unchecked_findings(
    branches: tuple[ConditionalBranch, ...],
) -> tuple[BranchConflictFinding, ...]:
    """TARGET_CHANGED_SINCE_DRAFT_BASE — documented gap, per well-formed target.

    Whether the target drifted since the draft was written needs two inputs this
    carrier does not hold: the draft's BASE DATE (the säädös version the drafter
    read — proposals carry no committed base date here) and the statute's
    AMENDMENT HISTORY between that date and now. Both require corpus data and the
    amendment index. We emit an honest ``unchecked`` placeholder rather than
    guessing.
    """
    findings: list[BranchConflictFinding] = []
    for branch in branches:
        for op in branch.candidate_ops:
            statute_id = op.target_statute_id
            if not statute_id or not looks_like_statute_id(statute_id):
                continue
            provision_desc = op.target_provision_ref or "(statute root)"
            findings.append(
                BranchConflictFinding(
                    kind=BranchConflictKind.TARGET_CHANGED_SINCE_DRAFT_BASE,
                    detail=(
                        f"branch {branch.branch_id!r}: drift of amended target "
                        f"{statute_id} / {provision_desc} since the draft base was "
                        "NOT checked. This needs the draft's base date (not carried "
                        "by the proposal) and the statute's amendment history "
                        "between that date and now (the amendment index over "
                        "corpus data)."
                    ),
                    severity=BranchConflictSeverity.UNCHECKED,
                    branch_ids=(branch.branch_id,),
                    target=(statute_id, op.target_provision_ref),
                )
            )
    return tuple(findings)


def diagnose_branch_conflicts(
    package_or_branches: PackageOrBranches,
) -> tuple[BranchConflictFinding, ...]:
    """Inspect a ProposalPackage / its branches and emit typed conflict findings.

    Accepts a whole ``ProposalPackage``, a single ``ConditionalBranch``, or an
    iterable of branches. Emits, in a stable order:

      1. PURE blocking findings that a reader must adjudicate
         (TARGET_STATUTE_UNKNOWN, then DUPLICATE_TARGET); and
      2. one ``unchecked`` gap marker PER well-formed target for each check this
         layer cannot run (TARGET_EXISTENCE_UNCHECKED,
         TARGET_CHANGED_SINCE_DRAFT_BASE).

    The unchecked markers are deliberate honesty: their absence would falsely
    imply the target was verified. Callers gate on ``severity``:
    ``BLOCKING`` findings convict the proposal; ``UNCHECKED`` findings tell the
    caller which stronger checks still need corpus data / materialization.
    """
    branches = _branches_of(package_or_branches)
    findings: list[BranchConflictFinding] = []
    findings.extend(_statute_unknown_findings(branches))
    findings.extend(_duplicate_target_findings(branches))
    findings.extend(_existence_unchecked_findings(branches))
    findings.extend(_drift_unchecked_findings(branches))
    return tuple(findings)


__all__ = [
    "BranchConflictFinding",
    "BranchConflictKind",
    "BranchConflictSeverity",
    "diagnose_branch_conflicts",
]
