"""Finland XML-backed manual-compilation frontier claim kinds.

These claim kinds cover source-present but proof-incomplete Finnish XML cases:
the XML/source artifact exists, but deterministic compilation cannot prove a
payload boundary, target choice, source-chain base, temporal base, or mutation
boundary without an explicit reviewed claim.

The validators here are intentionally conservative.  They verify only:

- the cited source span hash;
- required typed target/value fields;
- allowed source-pathology family for the claim kind;
- a bounded source quote that appears in the cited source span.

They do not authorize replay and do not lower claims into operations.
Semantic/replay-adjacent kinds are registered as semantic compilation claims so
the existing composer keeps replay authorization blocked until a separate
phase-local replay gate exists.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from lawvm.core.manual_claims.kind_registry import (
    ClaimKindSpec,
    ValidationResult,
    register_claim_kind,
)
from lawvm.core.manual_claims.primitive import ManualCompilationClaim


@dataclass(frozen=True, slots=True)
class _XmlClaimSpec:
    claim_kind: str
    layer: str
    description: str
    target_fields: tuple[str, ...]
    value_fields: tuple[str, ...]
    allowed_pathology_codes: tuple[str, ...]
    semantic: bool


_SOURCE_CORRECTION = _XmlClaimSpec(
    claim_kind="fi.v1.CORRIGENDUM_SOURCE_CORRECTION",
    layer="correction",
    description=(
        "Reviewed source-correction claim for XML/extracted source atoms where "
        "a corrigendum or equivalent source witness supports a correction. The "
        "claim can feed deterministic extraction later but is not replay authority."
    ),
    target_fields=("source_statute", "affected_target", "source_locator"),
    value_fields=(
        "source_quote",
        "correction_kind",
        "original_text",
        "corrected_text",
        "correction_witness_digest",
    ),
    allowed_pathology_codes=(),
    semantic=False,
)

_PAYLOAD_COMPLETENESS = _XmlClaimSpec(
    claim_kind="fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
    layer="adjudication",
    description=(
        "Manual proof boundary for XML-present payloads whose broad target or "
        "body shape is too partial for deterministic whole-target replay."
    ),
    target_fields=("source_statute", "affected_target", "source_pathology_code"),
    value_fields=(
        "source_quote",
        "payload_boundary",
        "retained_live_paths",
        "mutation_boundary_proof_ref",
    ),
    allowed_pathology_codes=(
        "PARTIAL_WHOLE_SECTION_PAYLOAD",
        "MALFORMED_BROAD_REPLACE_BODY",
    ),
    semantic=True,
)

_SPARSE_SLOT = _XmlClaimSpec(
    claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
    layer="adjudication",
    description=(
        "Manual proof boundary for sparse item/subsection payloads where the XML "
        "source exists but deterministic slot binding is not unique enough."
    ),
    target_fields=("source_statute", "affected_target", "source_pathology_code"),
    value_fields=(
        "source_quote",
        "candidate_slots",
        "selected_slot",
        "old_text_precondition",
    ),
    allowed_pathology_codes=(
        "SPARSE_ITEM_BODY_MISSING",
        "ITEM_TARGET_STRUCTURE_ABSENT",
        "ITEM_TARGET_SLOT_OCCUPIED",
        "ITEM_TARGET_ANCHOR_ABSENT",
        "SUBSECTION_TARGET_ABSENT",
    ),
    semantic=True,
)

_CONTAINER_MEMBERSHIP = _XmlClaimSpec(
    claim_kind="fi.v1.CONTAINER_MEMBERSHIP_RESOLUTION",
    layer="adjudication",
    description=(
        "Manual proof boundary for container-wrapped XML payloads where bundled "
        "children must be classified as operative payload or carried context."
    ),
    target_fields=("source_statute", "affected_target", "source_pathology_code"),
    value_fields=(
        "source_quote",
        "payload_children",
        "carried_context_children",
        "standalone_target_children",
    ),
    allowed_pathology_codes=("CONTAINER_MEMBERSHIP_MISMATCH",),
    semantic=True,
)

_SOURCE_CHAIN = _XmlClaimSpec(
    claim_kind="fi.v1.SOURCE_CHAIN_RESOLUTION",
    layer="adjudication",
    description=(
        "Manual proof boundary for XML-present recodification/source-chain gaps "
        "where deterministic replay cannot prove the correct base or lineage."
    ),
    target_fields=("source_statute", "affected_target", "source_pathology_code"),
    value_fields=(
        "source_quote",
        "base_source_id",
        "lineage_event_kind",
        "source_chain_basis",
    ),
    allowed_pathology_codes=(
        "BASE_MISSING_CHAPTER_SPAN",
        "RECODIFICATION_SOURCE_CHAIN_GAP",
    ),
    semantic=True,
)

_TEMPORAL_BASE = _XmlClaimSpec(
    claim_kind="fi.v1.TEMPORAL_BASE_SELECTION_RESOLUTION",
    layer="adjudication",
    description=(
        "Manual proof boundary for XML-present temporal base-selection cases, "
        "including temporary or expired source snapshots."
    ),
    target_fields=("source_statute", "affected_target", "source_pathology_code"),
    value_fields=(
        "source_quote",
        "effective_date",
        "base_snapshot_id",
        "temporal_basis",
    ),
    allowed_pathology_codes=("TEMPORARY_SECTION_REBASE",),
    semantic=True,
)

_MUTATION_BOUNDARY = _XmlClaimSpec(
    claim_kind="fi.v1.MUTATION_BOUNDARY_RESOLUTION",
    layer="adjudication",
    description=(
        "Manual proof boundary for XML-present cases where literal replay would "
        "cause destructive shape loss outside the source-owned target region."
    ),
    target_fields=("source_statute", "affected_target", "source_pathology_code"),
    value_fields=(
        "source_quote",
        "changed_paths",
        "target_region",
        "recovery_rule_id",
    ),
    allowed_pathology_codes=("DESTRUCTIVE_SHAPE_LOSS_RISK",),
    semantic=True,
)

_FAILED_OPERATION = _XmlClaimSpec(
    claim_kind="fi.v1.FAILED_OPERATION_RESOLUTION",
    layer="adjudication",
    description=(
        "Manual or deterministic proof boundary for visible failed operation "
        "rows. The claim identifies why an attempted operation remained "
        "non-executable, but does not authorize replay by itself."
    ),
    target_fields=("source_statute", "affected_target", "failure_reason_code"),
    value_fields=(
        "source_quote",
        "resolution_kind",
        "resolution_basis",
        "mutation_boundary_proof_ref",
    ),
    allowed_pathology_codes=(),
    semantic=True,
)

_UNSUPPORTED_CORRIGENDUM_PATCH = _XmlClaimSpec(
    claim_kind="fi.v1.CORRIGENDUM_UNSUPPORTED_PATCH_RESOLUTION",
    layer="adjudication",
    description=(
        "Manual proof boundary for XML/source-backed corrigendum patches whose "
        "patch shape is visible but not deterministically compilable by the "
        "current corrigendum parser."
    ),
    target_fields=("source_statute", "affected_target", "unsupported_reason_code"),
    value_fields=(
        "source_quote",
        "correction_kind",
        "resolution_kind",
        "resolution_basis",
        "mutation_boundary_proof_ref",
    ),
    allowed_pathology_codes=(),
    semantic=True,
)

_XML_FRONTIER_SPECS: tuple[_XmlClaimSpec, ...] = (
    _SOURCE_CORRECTION,
    _PAYLOAD_COMPLETENESS,
    _SPARSE_SLOT,
    _CONTAINER_MEMBERSHIP,
    _SOURCE_CHAIN,
    _TEMPORAL_BASE,
    _MUTATION_BOUNDARY,
    _FAILED_OPERATION,
    _UNSUPPORTED_CORRIGENDUM_PATCH,
)

_SPECS_BY_KIND: dict[str, _XmlClaimSpec] = {
    spec.claim_kind: spec for spec in _XML_FRONTIER_SPECS
}


def _claim_mapping(claim: ManualCompilationClaim, field_name: str) -> dict[str, object]:
    if field_name == "target":
        return dict(claim.target)
    if field_name == "value":
        return dict(claim.value)
    return {}


def _missing_fields(mapping: dict[str, object], fields: tuple[str, ...]) -> tuple[str, ...]:
    missing: list[str] = []
    for field in fields:
        value = mapping.get(field)
        if value is None:
            missing.append(field)
        elif isinstance(value, str) and not value.strip():
            missing.append(field)
        elif isinstance(value, tuple) and not value:
            missing.append(field)
        elif isinstance(value, list) and not value:
            missing.append(field)
    return tuple(missing)


def _span_bytes(
    claim: ManualCompilationClaim,
    source_bytes: bytes,
) -> tuple[bytes, str]:
    start, end = claim.cited_source_span
    if start < 0 or end > len(source_bytes) or start >= end:
        return b"", f"span ({start}, {end}) out of range for source of length {len(source_bytes)}"
    return source_bytes[start:end], ""


def _validate_span(claim: object, source_bytes: object) -> ValidationResult:
    if not isinstance(claim, ManualCompilationClaim):
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason="claim is not a ManualCompilationClaim",
            details=None,
        )
    if not isinstance(source_bytes, bytes):
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason="source_bytes is not bytes",
            details=None,
        )
    span_bytes, span_error = _span_bytes(claim, source_bytes)
    if span_error:
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason=span_error,
            details=None,
        )
    actual = hashlib.sha256(span_bytes).hexdigest()
    if actual != claim.cited_source_hash:
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason=f"hash mismatch at cited span: claimed {claim.cited_source_hash!r}, actual {actual!r}",
            details=None,
        )
    return ValidationResult(
        passed=True,
        validator_name="span_verified",
        reason="ok",
        details=None,
    )


def _validate_entailment(claim: object, source_bytes: object) -> ValidationResult:
    if not isinstance(claim, ManualCompilationClaim):
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason="claim is not a ManualCompilationClaim",
            details=None,
        )
    if not isinstance(source_bytes, bytes):
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason="source_bytes is not bytes",
            details=None,
        )
    spec = _SPECS_BY_KIND.get(claim.claim_kind)
    if spec is None:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=f"unsupported XML manual frontier claim kind {claim.claim_kind!r}",
            details=None,
        )

    target = _claim_mapping(claim, "target")
    value = _claim_mapping(claim, "value")
    missing = _missing_fields(target, spec.target_fields) + _missing_fields(value, spec.value_fields)
    if missing:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=f"missing required fields: {', '.join(missing)}",
            details="missing_required_fields",
        )

    pathology_code = str(target.get("source_pathology_code") or "")
    if spec.allowed_pathology_codes and pathology_code not in spec.allowed_pathology_codes:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=(
                f"source_pathology_code {pathology_code!r} is not valid for "
                f"{spec.claim_kind}; expected one of {spec.allowed_pathology_codes}"
            ),
            details="source_pathology_code_mismatch",
        )

    source_quote = str(value.get("source_quote") or "").strip()
    span_bytes, span_error = _span_bytes(claim, source_bytes)
    if span_error:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=span_error,
            details=None,
        )
    span_text = span_bytes.decode("utf-8", errors="replace")
    if source_quote not in span_text:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason="source_quote not found in cited source span",
            details="source_quote_absent",
        )
    return ValidationResult(
        passed=True,
        validator_name="entailment_verified",
        reason="ok",
        details=None,
    )


def _register(spec: _XmlClaimSpec) -> ClaimKindSpec:
    registered = ClaimKindSpec(
        claim_kind=spec.claim_kind,
        jurisdiction="fi",
        layer=spec.layer,
        description=spec.description,
        target_fields=spec.target_fields,
        value_fields=spec.value_fields,
        span_validator=_validate_span,
        entailment_validator=_validate_entailment,
        is_semantic_compilation_claim=spec.semantic,
    )
    register_claim_kind(registered)
    return registered


_REGISTERED_SPECS: dict[str, ClaimKindSpec] = {
    spec.claim_kind: _register(spec) for spec in _XML_FRONTIER_SPECS
}


def get_spec(claim_kind: str) -> ClaimKindSpec:
    """Return one registered Finland XML manual-frontier claim spec."""

    return _REGISTERED_SPECS[claim_kind]


def list_specs() -> tuple[ClaimKindSpec, ...]:
    """Return all registered Finland XML manual-frontier claim specs."""

    return tuple(_REGISTERED_SPECS[kind] for kind in sorted(_REGISTERED_SPECS))
