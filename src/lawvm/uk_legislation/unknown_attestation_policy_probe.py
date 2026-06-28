"""§2.9 production-lane guard-liveness for the UK unknown-attestation-policy probe (D12).

CONTEXT
``lawvm.core.evidence_policy.audit_attestation_policy_gap`` (registry row
**D12** / ``EVID.UNKNOWN_ATTESTATION_POLICY`` — AGENTS.md §0/§2.10: a
cited-by-unknown ``predicate_id`` is a FORGED policy cite, not a soft
mismatch. The audit returns one ``AttestationPolicyGap`` per cited policy
id NOT in the loaded registry's known set; the empty tuple is the success
witness — had ZERO UK production call sites (the §2.9 worst failure class).
Per memory ``uk_d1_d7_childtail_findings.md``: *"the per-op projection
needs a multi-session build-out of tools/certificate_bundle.py"* — and
*"the wire into ``tools/certificate_bundle.py:~2404`` ... is
medium-complexity; needs the existing emission site to obtain the loaded
registry + projection rows then call audit_attestation_policy_gap"*.

This module wires the audit at the UK replay fold-exit as a v0
OBSERVATION-ONLY, env-gated probe — emitting a
:class:`~lawvm.replay_adjudication.CompileAdjudication` per
``AttestationPolicyGap`` carrier the audit returns. UK has no loaded
EvidencePolicyRegistry or collected proof_rows surface today, so the probe
runs the audit with an empty registry + empty proof_rows and emits nothing
(clean no-op cycle — showcasing the firewall hook is explicit per
audit_impl_D12 spec intent, mirroring D11's discipline). As UK grows a
registry + proof-row surface (a future wire), the probe will receive those
inputs so the audit can fire on forged-policy-cite breaches.

§2.9 GUARD-LIVENESS DISCIPLINE: the v0 emit-nothing case is the textbook
§2.9 worst failure class. The §2.9 fire-drill at
:func:`tests.test_uk_unknown_attestation_policy_probe.test_probe_fires_
on_known_violating_proof_row_with_unknown_policy_id` drives a known-
violating input directly through the probe (a proof_row carrying a forged
``authorization_rule_id`` not in the empty known set) and asserts the
corresponding ``uk_replay_unknown_attestation_policy_observed``
adjudication fires — the §2.9 guard-liveness rule proving the wire is
reachable from production.

OPT-IN ENV
``LAWVM_UK_UNKNOWN_ATTESTATION_POLICY_PROBE=1``. Default-off preserves
byte-stable bench replay output. The probe never raises — non-blocking
``uk_replay_*_observed`` adjudications.

WHAT THIS DOES NOT PROMISE (honesty boundary):
* Empty registry + empty proof_rows at v0 → audit emits nothing in
  production. The probe is FORWARD-COMPATIBLE NO-OP AUDIT per audit_impl_D12
  spec intent — it makes the firewall hook explicit, surfaces the
  requirement that UK grow a registry + proof-row surface, and exercises
  the audit's pathway so the audit cannot become dead code if a future wire
  forgets to populate inputs.
* It does NOT block replay — observation-only (per AGENTS.md §0 over-
  retention-safe direction). Strict enforcement stays multi-session pending
  a UK strict_profile lane.
* It does NOT compute registry hashes — the empty registry's hash is
  computed by ``EvidencePolicyRegistry.build()`` (deterministic per the
  ``_compute_registry_hash`` helper) so a future wire can transition to a
  populated registry cleanly.

§1.12 RE-DERIVATION RISK: NONE. The audit is a pure read over
``registry.predicates[].predicate_id`` + ``proof_rows[].authorization_rule_id``
(+ the ``detail.evidence_kernel.policy_id`` nested cite path). All inputs
are source-side typed carriers; no re-parsing of source text or rendered/
oracle text.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional, Sequence

from lawvm.core.evidence_policy import (
    EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE,
    AttestationPolicyGap,
    EvidencePolicyRegistry,
    audit_attestation_policy_gap,
    known_predicate_ids,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication

# UK-scoped adjudication kind emitted for an EVID.UNKNOWN_ATTESTATION_POLICY
# shortfall. Mirrored after the existing ``uk_replay_*_observed`` adjudication
# kind vocabulary; the underlying audit registry row (D12) is preserved as
# ``core_registry_finding_code`` in the detail payload.
UK_UNKNOWN_ATTESTATION_POLICY_KIND = (
    "uk_replay_unknown_attestation_policy_observed"
)

# Opt-in env flag — default-off preserves byte-stable bench replay output.
_PROBE_ENV_FLAG = "LAWVM_UK_UNKNOWN_ATTESTATION_POLICY_PROBE"


def _probe_enabled() -> bool:
    return os.environ.get(_PROBE_ENV_FLAG, "") == "1"


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
    if not _probe_enabled():
        return ()
    statute_id = str(source_statute or "")
    reg = registry if registry is not None else _empty_registry()
    rows = list(proof_rows) if proof_rows else []
    try:
        gaps = audit_attestation_policy_gap(reg, rows)
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        if adjudications_out is not None:
            adjudications_out.append(
                _build_probe_skip_adjudication(
                    statute_id=statute_id,
                    reason=(
                        f"probe_unexpected_error: {exc.__class__.__name__}: {exc!r}"
                    ),
                )
            )
        return ()
    if not gaps:
        return ()
    known = known_predicate_ids(reg)
    for gap in gaps:
        adjudication = _build_adjudication(
            statute_id=statute_id,
            gap=gap,
            known_predicate_ids=frozenset(known),
            proof_row_count=len(rows),
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return gaps


def _build_adjudication(
    *,
    statute_id: str,
    gap: AttestationPolicyGap,
    known_predicate_ids: frozenset[str],
    proof_row_count: int,
) -> CompileAdjudication:
    """Render one audit gap carrier as a UK CompileAdjudication."""
    return CompileAdjudication(
        kind=UK_UNKNOWN_ATTESTATION_POLICY_KIND,
        message=(
            "UK replay fold exit: a proof row cites an attestation policy "
            "id NOT in the loaded registry's known-predicate set — the cite "
            "is a FORGED policy cite, not a soft mismatch (AGENTS.md §0/§2.10). "
            "Emitted observably; strict enforcement stays multi-session "
            "pending a UK strict_profile lane."
        ),
        source_statute=statute_id,
        op_id="",
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": UK_UNKNOWN_ATTESTATION_POLICY_KIND,
            "family": "unknown_attestation_policy",
            "reason_code": "forged_policy_cite_observed",
            "cited_policy_id": str(gap.cited_policy_id),
            "cite_source": str(gap.cite_source),
            "cite_location": str(gap.cite_location),
            "audit_rule_id": str(gap.rule_id),
            "known_predicate_ids_sorted": sorted(known_predicate_ids),
            "proof_row_count": int(proof_row_count),
            "core_registry_finding_kind": (
                EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE
            ),
            "probe_mode": "observation_only",
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": (
                "core.evidence_policy.audit_attestation_policy_gap"
            ),
            # The canonical prior-art witness is the D12 registry row at
            # core/observation_registry.py + the spec's
            # core.evidence_policy EvidencePolicyRegistry shape. The probe
            # is the discipline-disclosing first step toward a UK
            # certificate_bundle wire (Tier B PR-multisession per memory
            # uk_d1_d7_childtail_findings.md) that loads a populated
            # registry + emits per-op proof_rows.
            "witness_prior_art": "d12_evid_unknown_attestation_policy_evidence_policy_registry_shape",
        },
    )


def _build_probe_skip_adjudication(
    *,
    statute_id: str,
    reason: str,
) -> CompileAdjudication:
    """Diagnostic record when the probe could not run — never silent."""
    return CompileAdjudication(
        kind="uk_replay_unknown_attestation_policy_probe_skipped",
        message=(
            "UK unknown-attestation-policy probe could not run the audit. "
            "Recorded as a named diagnostic so the silence is itself audible."
        ),
        source_statute=statute_id,
        op_id="",
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": "uk_replay_unknown_attestation_policy_probe_skipped",
            "family": "unknown_attestation_policy",
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


__all__ = [
    "UK_UNKNOWN_ATTESTATION_POLICY_KIND",
    "probe_uk_unknown_attestation_policy",
]
