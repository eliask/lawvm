"""Tests for the D12 ``EVID.ATTESTATION_POLICY_GAP_TOTALITY`` audit.

Per :file:`notes_internal/audit_impl_D12.md`: an attestation policy id cited by a
proof-carrying output but absent from the loaded
:class:`EvidencePolicyRegistry` is a FORGED policy cite — never a soft
mismatch. The audit lives in :mod:`lawvm.core.evidence_policy`:

* :func:`known_predicate_ids` returns the set of ``predicate_id`` strings the
  registry admits (single authority surface, no escape hatch for dynamically-
  constructed ids).
* :func:`collect_cited_attestation_policy_ids` extracts every policy id
  cited across the proof/certification surface via ``authorization_rule_id``
  or the nested ``detail.evidence_kernel.policy_id`` path.
* :func:`audit_attestation_policy_gap` returns one :class:`AttestationPolicyGap`
  per cited-by-unknown id. Empty tuple is the success witness.
* :func:`attestation_policy_gap_findings` projects each gap to one
  ``EVID.UNKNOWN_ATTESTATION_POLICY`` violation Finding.

Honest scope: the wire into ``tools/certificate_bundle.py:~2404`` (the existing
policy-hash commit block) is staged as a follow-up commit; until the wire,
this audit runs from the unit/helper lane only.
"""

from __future__ import annotations

from lawvm.core.evidence_policy import (
    AttestationPolicyGap,
    EVID_ATTESTATION_POLICY_GAP_TOTALITY_RULE_ID,
    EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE,
    EvidenceGraphPredicate,
    EvidencePolicyRegistry,
    attestation_policy_gap_findings,
    audit_attestation_policy_gap,
    collect_cited_attestation_policy_ids,
    known_predicate_ids,
)
from lawvm.core.observation_registry import FINDING_REGISTRY
from lawvm.core.phase_result import Finding, VIOLATION_ROLE


_KNOWN_PID = "fi.v1.INLINE_STATUTE_RESOLUTION.strict"
_OTHER_KNOWN_PID = "fi.v1.AMENDMENT_COMMENCEMENT.strict"


def _registry(*predicate_ids: str) -> EvidencePolicyRegistry:
    """Build a minimal registry admitting the given predicate ids."""
    predicates = tuple(
        EvidenceGraphPredicate(
            predicate_id=pid,
            claim_kind=f"claim:{pid}",
            required=(),
        )
        for pid in predicate_ids
    )
    return EvidencePolicyRegistry.build(
        registry_id="test.v1",
        registry_version="v1",
        predicates=predicates,
    )


# --------------------------------------------------------------------------- #
# Firing case — known registry, cited-by-unknown fires one gap.               #
# --------------------------------------------------------------------------- #


def test_cited_unknown_policy_id_in_authorization_rule_yields_one_gap() -> None:
    """Per audit_impl_D12 §6 positive: an
    :class:`ExecutionAuthorization` whose ``authorization_rule_id`` cites an
    unknown predicate_id yields exactly one :class:`AttestationPolicyGap` whose
    ``cited_policy_id`` is the unknown id.

    Drives the registry + proof_rows through the audit directly. The cited id
    ``policy_v999.unknown`` is NOT registered; ``fi.v1.INLINE_STATUTE_
    RESOLUTION.strict`` IS registered. Only the unknown cite fires.
    """
    registry = _registry(_KNOWN_PID)
    proof_rows = (
        {
            "authorization_rule_id": _KNOWN_PID,
            "row_id": "auth-row-1",
        },
        {
            "authorization_rule_id": "policy_v999.unknown",
            "row_id": "auth-row-2",
        },
    )
    gaps = audit_attestation_policy_gap(registry, proof_rows)
    assert len(gaps) == 1, (
        f"only the unknown cite MUST fire; got {len(gaps)} gaps"
    )
    gap = gaps[0]
    assert isinstance(gap, AttestationPolicyGap)
    assert gap.cited_policy_id == "policy_v999.unknown"
    assert gap.cite_source == "authorization_rule_id"
    assert gap.cite_location == "auth-row-2"
    assert gap.rule_id == EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE


def test_unknown_nested_evidence_kernel_policy_id_yields_one_gap() -> None:
    """A cited policy id nested under ``detail.evidence_kernel.policy_id`` is
    also collected and audited.

    Per audit_impl_D12 §2 the nested ``detail.evidence_kernel.policy_id`` cite
    path is the second collector surface. An unknown id on this path must fire
    the same way as the ``authorization_rule_id`` path.
    """
    registry = _registry(_KNOWN_PID)
    proof_rows = (
        {
            "authorization_rule_id": _KNOWN_PID,
            "row_id": "auth-row-1",
            "detail": {
                "evidence_kernel": {
                    "policy_id": "unknown.kernel.pid"
                }
            },
        },
    )
    gaps = audit_attestation_policy_gap(registry, proof_rows)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.cited_policy_id == "unknown.kernel.pid"
    assert gap.cite_source == "evidence_kernel_policy_id"


# --------------------------------------------------------------------------- #
# Negative — known cite doesn't fire.                                          #
# --------------------------------------------------------------------------- #


def test_all_known_cites_yields_empty_gap_tuple() -> None:
    """Per audit_impl_D12 §6 negative: all cited policy ids are registered → empty
    tuple (success witness). ``audit_attestation_policy_gap`` never returns None
    (§1.10 fail-loud; the absence of a gap is a valid result, but None would be
    silent folklore).
    """
    registry = _registry(_KNOWN_PID, _OTHER_KNOWN_PID)
    proof_rows = (
        {"authorization_rule_id": _KNOWN_PID, "row_id": "row-1"},
        {"authorization_rule_id": _OTHER_KNOWN_PID, "row_id": "row-2"},
        {
            "authorization_rule_id": _KNOWN_PID,
            "row_id": "row-3",
            "detail": {"evidence_kernel": {"policy_id": _KNOWN_PID}},
        },
    )
    gaps = audit_attestation_policy_gap(registry, proof_rows)
    assert gaps == ()


# --------------------------------------------------------------------------- #
# Discriminators — dedup, empty, multi-registry.                             #
# --------------------------------------------------------------------------- #


def test_same_cite_on_both_paths_does_not_double_fire() -> None:
    """When the same cited policy id appears on BOTH the
    ``authorization_rule_id`` AND the nested ``detail.evidence_kernel.policy_id``
    path of the same row, the audit does NOT double-fire.

    The ``authorization_rule_id`` gap is the canonical witness (§1.8 receipt
    accounting: the same cite is owned ONCE, not silently duplicated across
    cite-paths).
    """
    registry = _registry(_KNOWN_PID)
    proof_rows = (
        {
            "authorization_rule_id": "shared.unknown.pid",
            "row_id": "auth-row-dup",
            "detail": {"evidence_kernel": {"policy_id": "shared.unknown.pid"}},
        },
    )
    gaps = audit_attestation_policy_gap(registry, proof_rows)
    assert len(gaps) == 1
    assert gaps[0].cited_policy_id == "shared.unknown.pid"
    assert gaps[0].cite_source == "authorization_rule_id"


def test_empty_registry_and_empty_proof_rows_yields_empty_tuple() -> None:
    """Empty registry (no predicates) and empty proof_rows are both the
    clean-state edge case.

    An empty registry with citations fires one gap per citation; an empty
    proof_rows with predicates fires no gaps. Disciplined both-ways handling —
    the audit does not silently swallow inputs and does not raise.
    """
    empty_registry = _registry()
    # No predicates → all cites are unknown. Empty registry + no rows → no gaps.
    assert audit_attestation_policy_gap(empty_registry, ()) == ()
    # Empty registry + cites with all unknown ids → fires each.
    proof_rows = (
        {"authorization_rule_id": "totally.unknown", "row_id": "row-1"},
        {"authorization_rule_id": "also.unknown", "row_id": "row-2"},
    )
    gaps = audit_attestation_policy_gap(empty_registry, proof_rows)
    assert len(gaps) == 2


def test_known_predicate_ids_returns_complete_registry_set() -> None:
    """``known_predicate_ids`` returns EVERY predicate in the registry, frozenset.

    Closed-set surface (AGENTS.md §1.9): a finite, deduplicated frozenset of
    registered ``predicate_id`` strings. The audit's gap-detection is a set
    difference against this frozenset — so an unknown id always fails to match.
    """
    registry = _registry(_KNOWN_PID, _OTHER_KNOWN_PID)
    known = known_predicate_ids(registry)
    assert known == frozenset({_KNOWN_PID, _OTHER_KNOWN_PID})


def test_collected_cites_include_both_paths_in_a_frozenset() -> None:
    """``collect_cited_attestation_policy_ids`` aggregates both cite paths into a
    deduplicated frozenset (§1.9 typed carrier, no positional escape hatch).

    The collector returns a frozenset[str]; duplicate cites across rows or
    paths collapse. The collector does NOT raise on unknown key shapes — it
    silently skips paths that don't match the closed-set cite-path vocabulary
    (the fail-loud discipline lives in :func:`audit_attestation_policy_gap`).
    """
    proof_rows = (
        {"authorization_rule_id": _KNOWN_PID, "row_id": "row-1"},
        {
            "authorization_rule_id": "pid.X",
            "row_id": "row-2",
            "detail": {"evidence_kernel": {"policy_id": "pid.Y"}},
        },
        # Row with missing detail and a nested non-Mapping — the collector
        # must not raise.
        {"authorization_rule_id": "pid.Z", "row_id": "row-3", "detail": None},
    )
    cited = collect_cited_attestation_policy_ids(proof_rows)
    assert cited == frozenset({_KNOWN_PID, "pid.X", "pid.Y", "pid.Z"})


# --------------------------------------------------------------------------- #
# Projection to violation findings + registry-row presence.                   #
# --------------------------------------------------------------------------- #


def test_gaps_project_to_violation_findings_with_cite_provenance() -> None:
    """Each gap projects to a ``EVID.UNKNOWN_ATTESTATION_POLICY`` violation Finding.

    Per audit_impl_D12 §5: the Finding MUST be ``role="violation"`` and
    ``blocking=True`` (a forged policy id is a contract break). Per §1.10 the
    detail carries the offending ``cited_policy_id`` + ``cite_source`` +
    ``cite_location`` so a triager can answer the §3.2 evidence path without
    re-running extraction.
    """
    registry = _registry(_KNOWN_PID)
    proof_rows = (
        {
            "authorization_rule_id": "policy_v999.unknown",
            "row_id": "auth-row-1",
        },
    )
    gaps = audit_attestation_policy_gap(registry, proof_rows)
    findings = attestation_policy_gap_findings(gaps)
    assert len(findings) == 1
    finding = findings[0]
    assert isinstance(finding, Finding)
    assert finding.kind == EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE
    assert finding.role == VIOLATION_ROLE
    assert finding.blocking is True
    assert finding.stage == "evidence_kernel"
    detail = finding.detail
    assert detail["cited_policy_id"] == "policy_v999.unknown"
    assert detail["cite_source"] == "authorization_rule_id"
    assert detail["cite_location"] == "auth-row-1"
    assert detail["rule_id"] == EVID_ATTESTATION_POLICY_GAP_TOTALITY_RULE_ID
    assert "AGENTS.md §0" in detail["reason"]


def test_registry_row_registered_for_violation_finding() -> None:
    """The FindingSpec row is registered so the violation Finding validates.

    Sanity for the validate_finding_projection carrier contract: a
    role=``"violation"`` Finding requires blocking=True AND a registered
    barrier code (hard_fail enforcement).

    NOTE: ``EVID.UNKNOWN_ATTESTATION_POLICY`` is registered by the EV-06
    apply-authority closure wave at ``apply_op_closure_sweeps.py`` (phase=apply,
    owner=apply_op_closure_sweeps, family=violation, hard_fail). The D12
    bundle-emission sweep at ``evidence_policy.audit_attestation_policy_gap``
    EMITS THE SAME code (no separate registry row) — the single canonical
    FindingSpec row's metadata governs every emission path that cites an
    unknown attestation-policy id. audit_impl_D12 spec literal wanted a
    separate ``evidence_kernel`` row but that would silently override the
    existing emitter's metadata (a contract break), so we share.
    """
    spec = FINDING_REGISTRY.get(EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE)
    assert spec is not None
    assert spec.role == "violation"
    assert spec.family == "violation"
    assert spec.default_enforcement == "hard_fail"
    assert spec.owner == "apply_op_closure_sweeps"
    assert "safety_invariant" in spec.proof_categories
    assert "provenance" in spec.proof_categories
