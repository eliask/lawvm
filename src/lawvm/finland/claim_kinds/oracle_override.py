"""Finland oracle-override claim kind.

Registers ``fi.v1.ORACLE_OVERRIDE`` — a typed manual claim that marks a
Finlex consolidated oracle row as wrong in any way (not just
LawVM-replay-is-right-and-oracle-is-stale; the oracle can be wrong for
many reasons: transcription error, wrong section, missing amendment
effect, omitted repeal, editorial convention vs legal truth, etc.).

This claim lives in the **adjudication layer** (ClaimLayer.ADJUDICATION)
and is intentionally **not a semantic compilation claim** — it does not
authorise replay mutation. Per AGENTS.md §2.10 (planes stay
type-distinct), an oracle-override mutates the *projection* /
comparison-surface plane, never the source XML. The law is whatever the
source XML yields after replay; the override just declares "this
consolidated-oracle row does not represent canonical truth and we have
proof."

The Promotion chain (AGENTS.md §0): source_witness → claim → adjudication
override → projection filter. The validator here is intentionally
conservative: it checks the carrier-shape requirements (rule_id,
statute_id, target_address, non-empty source_witness) and that the
evidence is non-empty — it does NOT authenticate the witness (that is a
human-review step in the promotion boundary, outside this module).

See ``data/finland/oracle_overrides_fi.yaml`` for the canonical carrier
file and ``notes/MANUAL_COMPILATION_CLAIMS.md`` §2.2 (semantic
compilation claim) for the broader claim discipline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from lawvm.core.manual_claims.kind_registry import (
    ClaimKindSpec,
    ValidationResult,
    register_claim_kind,
)


_REQUIRED_TARGET_FIELDS = ("rule_id", "statute_id", "target_address")
_REQUIRED_VALUE_FIELDS = ("override_kind", "source_witness", "evidence_summary")


@dataclass(frozen=True, slots=True)
class _OracleOverrideSpec:
    claim_kind: str
    layer: str
    description: str
    target_fields: tuple[str, ...]
    value_fields: tuple[str, ...]


_ORACLE_OVERRIDE = _OracleOverrideSpec(
    claim_kind="fi.v1.ORACLE_OVERRIDE",
    layer="adjudication",
    description=(
        "Reviewed oracle-override claim: the Finlex consolidated oracle row "
        "is wrong in some way and the proof is in the cited source_witness. "
        "Mutates the comparison/projection plane, NOT source XML — replay "
        "is unaffected. The witness standard is the strictest of the (a)/(c) "
        "surfaces: original_promulgation, later_corrigendum, "
        "explicit_editorial_marker, multi_acquisition_corroboration, "
        "intrinsic_legal_reason. LawVM-replay-is-right is only one of "
        "several oracle-wrong shapes; do not pre-narrow."
    ),
    target_fields=_REQUIRED_TARGET_FIELDS,
    value_fields=_REQUIRED_VALUE_FIELDS,
)


def _validate_span(target: object, value: object) -> ValidationResult:
    """Conservative shape validator for oracle-override claims.

    Oracle-override claims carry no canonical source span (they are
    projection-plane, not source-plane); the "span" here is a target
    address — a legal-address surface on the oracle side. The validator
    checks the carrier shape only (rule_id, statute_id, target_address
    all non-empty); witness authentication is a human-review promotion
    step outside this module.
    """
    if not isinstance(target, dict):
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason="target must be a dict",
            details=None,
        )
    if not isinstance(value, dict):
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason="value must be a dict",
            details=None,
        )
    target_d = cast("dict[str, object]", target)
    value_d = cast("dict[str, object]", value)
    for field in _REQUIRED_TARGET_FIELDS:
        if not str(target_d.get(field) or "").strip():
            return ValidationResult(
                passed=False,
                validator_name="span_verified",
                reason=f"missing required target field: {field!r}",
                details=None,
            )
    witness = value_d.get("source_witness")
    if not isinstance(witness, dict) or not str(
        cast("dict[str, object]", witness).get("kind") or ""
    ).strip():
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason="source_witness.kind must be a non-empty string",
            details=None,
        )
    if not str(value_d.get("evidence_summary") or "").strip():
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason="evidence_summary must be a non-empty string",
            details=None,
        )
    return ValidationResult(
        passed=True,
        validator_name="span_verified",
        reason="ok",
        details=None,
    )


def _validate_entailment(target: object, value: object) -> ValidationResult:
    """Entailment validator: oracle-override claims make no entailment
    claim against replay — they are projection-plane only.

    Returns ``passed=True`` unconditionally on shape-passed claims; the
    real entailment check is performed at oracle-comparison time when
    the override is applied to a disagreement row. Keeping this
    permissive preserves the plane-distinction (adjudication layer does
    not gate replay authority).
    """
    return ValidationResult(
        passed=True,
        validator_name="entailment_verified",
        reason="oracle_override_no_replay_authority",
        details=None,
    )


def _register(spec: _OracleOverrideSpec) -> ClaimKindSpec:
    registered = ClaimKindSpec(
        claim_kind=spec.claim_kind,
        jurisdiction="fi",
        layer=spec.layer,
        description=spec.description,
        target_fields=spec.target_fields,
        value_fields=spec.value_fields,
        span_validator=_validate_span,
        entailment_validator=_validate_entailment,
        # Oracle-override is NOT a semantic compilation claim — it authorises
        # no replay mutation. The composer must keep replay authority blocked
        # for this kind (AGENTS.md §2.10 / §0).
        is_semantic_compilation_claim=False,
    )
    register_claim_kind(registered)
    return registered


REGISTERED = _register(_ORACLE_OVERRIDE)


def get_spec() -> ClaimKindSpec:
    return REGISTERED
