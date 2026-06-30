"""§2.9 production-lane guard-liveness for the UK unknown-attestation-policy probe (D12).

CONTEXT
``lawvm.core.evidence_policy.audit_attestation_policy_gap`` (registry row
**D12** / ``EVID.UNKNOWN_ATTESTATION_POLICY`` — AGENTS.md §0/§2.10: a
cited-by-unknown ``predicate_id`` is a FORGED policy cite, not a soft
mismatch) had ZERO UK production call sites (the §2.9 worst failure class).
Per memory ``uk_d1_d7_childtail_findings.md``: D12 wire was classified
multi-session blocked via tools/certificate_bundle.py build-out (required
a per-op projection carrying authorization_rule_id +
detail.evidence_kernel.policy_id + row_id).

WIRED at commit ``c8adf388`` as a v0 forward-compat no-op probe + §2.9
fire-drill. Per audit_impl_D12 spec intent: *"today this is a forward-
compatible no-op audit call ... the hook makes the firewall explicit"*.

This module migrated to the shared ``lawvm.uk_legislation.probe_base``
harness per §2.6 rule-of-three (the env-gated observation-only probe
pattern shipped 9 times before this extraction landed). The migration
IS the precedent for the other 8 probes — reverse-applied in cohesive
later commits per §2.6 incremental migration discipline.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from lawvm.core.evidence_policy import (
    EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE,
    AttestationPolicyGap,
    EvidencePolicyRegistry,
    audit_attestation_policy_gap,
    known_predicate_ids,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)

# Public symbol kept (backward-compat with the fold-exit caller +
# the test suite). Mirrors the convention of the other 8 (yet-
# unmigrated) probe modules.
UK_UNKNOWN_ATTESTATION_POLICY_KIND = (
    "uk_replay_unknown_attestation_policy_observed"
)

# Module-scope ProbeSpec — the immutable shape consumed by the probe-base
# helpers. Replaces the per-probe _PROBE_ENV_FLAG + per-probe _build_*_
# adjudication boilerplate.
_PROBE_SPEC = ProbeSpec(
    env_flag="LAWVM_UK_UNKNOWN_ATTESTATION_POLICY_PROBE",
    kind=UK_UNKNOWN_ATTESTATION_POLICY_KIND,
    skipped_kind="uk_replay_unknown_attestation_policy_probe_skipped",
    family="unknown_attestation_policy",
    audit_module_path="core.evidence_policy.audit_attestation_policy_gap",
    witness_prior_art=(
        "d12_evid_unknown_attestation_policy_evidence_policy_registry_shape"
    ),
    core_registry_finding_kind=EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE,
)


def _empty_registry() -> EvidencePolicyRegistry:
    """A v0 sentinel registry with zero predicates.

    ``known_predicate_ids`` returns an empty frozenset, so any cited
    policy_id from ``proof_rows`` is registered as a gap (FORGED) and the
    audit emits one ``AttestationPolicyGap`` per cite. With empty
    ``proof_rows`` (the v0 default), the audit emits nothing.
    """
    return EvidencePolicyRegistry.build(
        registry_id="uk_unknown_attestation_policy_probe:v0",
        registry_version="0",
        predicates=(),
    )


def probe_uk_unknown_attestation_policy(
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    registry: Optional[EvidencePolicyRegistry] = None,
    proof_rows: Optional[Sequence[Mapping[str, object]]] = None,
    source_statute: str = "",
) -> tuple[AttestationPolicyGap, ...]:
    """Run the unknown-attestation-policy audit, appending a non-blocking
    ``CompileAdjudication`` per gap carrier.

    The probe consumes a loaded ``EvidencePolicyRegistry`` (or the empty v0
    sentinel) and a sequence of ``proof_rows`` (or empty at v0), runs
    :func:`audit_attestation_policy_gap` (D12), and emits one
    ``uk_replay_unknown_attestation_policy_observed`` adjudication per cited
    policy_id NOT in the registry's known set.

    UK has no loaded registry or collected proof_rows surface today, so the
    defaults produce zero findings in production. The fire-drill bypasses
    production by passing a known-violating proof_row + empty registry
    directly.

    Returns the audit's gap carriers (also surfaced as CompileAdjudications
    on ``adjudications_out`` when non-empty). Emits nothing when the audit
    yields no gaps (the v0 production default — clean no-op cycle).
    """
    if not probe_env_enabled(_PROBE_SPEC.env_flag):
        return ()
    statute_id = str(source_statute or "")
    reg = registry if registry is not None else _empty_registry()
    rows = list(proof_rows) if proof_rows else []
    try:
        gaps = audit_attestation_policy_gap(reg, rows)
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        if adjudications_out is not None:
            adjudications_out.append(
                make_probe_skip_adjudication(
                    _PROBE_SPEC,
                    statute_id=statute_id,
                    reason=(
                        f"probe_unexpected_error: "
                        f"{exc.__class__.__name__}: {exc!r}"
                    ),
                )
            )
        return ()
    if not gaps:
        return ()
    known = known_predicate_ids(reg)
    for gap in gaps:
        adjudication = make_probe_observed_adjudication(
            _PROBE_SPEC,
            statute_id=statute_id,
            message=(
                "UK replay fold exit: a proof row cites an attestation "
                "policy id NOT in the loaded registry's known-predicate "
                "set — the cite is a FORGED policy cite, not a soft "
                "mismatch (AGENTS.md §0/§2.10). Emitted observably; strict "
                "enforcement stays multi-session pending a UK "
                "strict_profile lane."
            ),
            extra_detail={
                "reason_code": "forged_policy_cite_observed",
                "cited_policy_id": str(gap.cited_policy_id),
                "cite_source": str(gap.cite_source),
                "cite_location": str(gap.cite_location),
                "audit_rule_id": str(gap.rule_id),
                "known_predicate_ids_sorted": sorted(known),
                "proof_row_count": int(len(rows)),
            },
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return gaps


__all__ = [
    "UK_UNKNOWN_ATTESTATION_POLICY_KIND",
    "probe_uk_unknown_attestation_policy",
]
