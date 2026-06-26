"""Core source-acquisition policy primitives.

Source-bundle admission is separate from replay authorization. These objects
classify whether a source artifact/lane may enter an evidence/source bundle and
which attestations are still missing. They do not authorize legal-state
mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.source_witness import SourceWitness


_SOURCE_BUNDLE_REQUIRED_PROOFS: tuple[str, ...] = (
    "phase_local_replay_authorization",
    "source_identity_proof",
    "target_identity_proof",
    "mutation_boundary_proof_before_replay_promotion",
)
_SOURCE_BUNDLE_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "source_bundle_admission_as_replay_authorization",
    "source_lane_admission_as_source_text_correction",
    "source_acquisition_attestation_as_semantic_compilation",
)


@dataclass(frozen=True, slots=True)
class SourceAcquisitionAssertion:
    """Claim that one source artifact belongs to a specific acquisition lane."""

    assertion_id: str
    jurisdiction: str
    artifact_id: str
    source_lane: str
    assertion_kind: str
    acquisition_status: str
    witness: SourceWitness | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "assertion_id", _required_string("assertion_id", self.assertion_id))
        object.__setattr__(self, "jurisdiction", _required_string("jurisdiction", self.jurisdiction))
        object.__setattr__(self, "artifact_id", _required_string("artifact_id", self.artifact_id))
        object.__setattr__(self, "source_lane", _required_string("source_lane", self.source_lane))
        object.__setattr__(self, "assertion_kind", _required_string("assertion_kind", self.assertion_kind))
        object.__setattr__(self, "acquisition_status", _required_string("acquisition_status", self.acquisition_status))
        if self.witness is not None and not isinstance(self.witness, SourceWitness):
            raise ValueError("SourceAcquisitionAssertion.witness must be a SourceWitness")
        if not isinstance(self.detail, Mapping):
            raise ValueError("SourceAcquisitionAssertion.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "assertion_id": self.assertion_id,
            "jurisdiction": self.jurisdiction,
            "artifact_id": self.artifact_id,
            "source_lane": self.source_lane,
            "assertion_kind": self.assertion_kind,
            "acquisition_status": self.acquisition_status,
            "detail": _plain_jsonable(self.detail),
        }
        if self.witness is not None:
            payload["source_witness"] = self.witness.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class SourceAcquisitionAttestation:
    """Evidence supporting or rejecting a source-acquisition assertion."""

    attestation_id: str
    assertion_id: str
    attestation_kind: str
    producer_id: str
    attestation_status: str
    witness: SourceWitness | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attestation_id", _required_string("attestation_id", self.attestation_id))
        object.__setattr__(self, "assertion_id", _required_string("assertion_id", self.assertion_id))
        object.__setattr__(self, "attestation_kind", _required_string("attestation_kind", self.attestation_kind))
        object.__setattr__(self, "producer_id", _required_string("producer_id", self.producer_id))
        object.__setattr__(self, "attestation_status", _required_string("attestation_status", self.attestation_status))
        if self.witness is not None and not isinstance(self.witness, SourceWitness):
            raise ValueError("SourceAcquisitionAttestation.witness must be a SourceWitness")
        if not isinstance(self.detail, Mapping):
            raise ValueError("SourceAcquisitionAttestation.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "attestation_id": self.attestation_id,
            "assertion_id": self.assertion_id,
            "attestation_kind": self.attestation_kind,
            "producer_id": self.producer_id,
            "attestation_status": self.attestation_status,
            "detail": _plain_jsonable(self.detail),
        }
        if self.witness is not None:
            payload["source_witness"] = self.witness.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class SourceBundleAdmission:
    """Result of applying a source-bundle policy to one acquisition assertion."""

    assertion_id: str
    admitted: bool
    admission_status: str
    policy_id: str
    source_lane: str
    missing_attestation_kinds: tuple[str, ...] = ()
    blocking_attestation_ids: tuple[str, ...] = ()
    safe_default: str = "exclude_source_from_bundle_until_policy_is_satisfied"
    forbidden_shortcuts: tuple[str, ...] = _SOURCE_BUNDLE_FORBIDDEN_SHORTCUTS
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "assertion_id", _required_string("assertion_id", self.assertion_id))
        object.__setattr__(self, "admission_status", _required_string("admission_status", self.admission_status))
        object.__setattr__(self, "policy_id", _required_string("policy_id", self.policy_id))
        object.__setattr__(self, "source_lane", _required_string("source_lane", self.source_lane))
        object.__setattr__(self, "missing_attestation_kinds", _string_tuple(self.missing_attestation_kinds))
        object.__setattr__(self, "blocking_attestation_ids", _string_tuple(self.blocking_attestation_ids))
        object.__setattr__(self, "forbidden_shortcuts", _string_tuple(self.forbidden_shortcuts))
        if not self.safe_default:
            raise ValueError("SourceBundleAdmission.safe_default is required")
        if not isinstance(self.detail, Mapping):
            raise ValueError("SourceBundleAdmission.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "admitted": self.admitted,
            "admission_status": self.admission_status,
            "policy_id": self.policy_id,
            "source_lane": self.source_lane,
            "missing_attestation_kinds": list(self.missing_attestation_kinds),
            "blocking_attestation_ids": list(self.blocking_attestation_ids),
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }

    def to_execution_authorization(self, *, owner_phase: str = "source_acquisition") -> ExecutionAuthorization:
        """Project bundle admission as non-executable replay authorization metadata."""

        return ExecutionAuthorization(
            executable=False,
            replay_authorized=False,
            authorization_status=(
                "source_bundle_admitted_not_replay_authority"
                if self.admitted
                else "source_bundle_policy_unsatisfied"
            ),
            authorization_rule_id=f"{self.policy_id}:{self.assertion_id}",
            owner_phase=owner_phase,
            strict_disposition="record" if self.admitted else "block",
            quirks_disposition="record",
            validator_status="source_bundle_admission_only",
            required_proofs=_SOURCE_BUNDLE_REQUIRED_PROOFS,
            safe_default=(
                "treat_source_bundle_admission_as_source_footing_not_replay_authorization"
                if self.admitted
                else self.safe_default
            ),
            forbidden_shortcuts=self.forbidden_shortcuts,
            detail={
                **dict(self.detail),
                "assertion_id": self.assertion_id,
                "policy_id": self.policy_id,
                "source_lane": self.source_lane,
                "admitted": self.admitted,
                "missing_attestation_kinds": self.missing_attestation_kinds,
                "blocking_attestation_ids": self.blocking_attestation_ids,
            },
        )


@dataclass(frozen=True, slots=True)
class SourceBundlePolicy:
    """Policy for admitting source lanes into a source/evidence bundle."""

    policy_id: str
    jurisdiction: str
    admitted_source_lanes: tuple[str, ...]
    blocked_source_lanes: tuple[str, ...] = ()
    required_attestation_kinds: tuple[str, ...] = ()
    safe_default: str = "exclude_source_from_bundle_until_policy_is_satisfied"
    forbidden_shortcuts: tuple[str, ...] = _SOURCE_BUNDLE_FORBIDDEN_SHORTCUTS
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _required_string("policy_id", self.policy_id))
        object.__setattr__(self, "jurisdiction", _required_string("jurisdiction", self.jurisdiction))
        object.__setattr__(self, "admitted_source_lanes", _string_tuple(self.admitted_source_lanes))
        object.__setattr__(self, "blocked_source_lanes", _string_tuple(self.blocked_source_lanes))
        object.__setattr__(self, "required_attestation_kinds", _string_tuple(self.required_attestation_kinds))
        object.__setattr__(self, "forbidden_shortcuts", _string_tuple(self.forbidden_shortcuts))
        if not self.admitted_source_lanes:
            raise ValueError("SourceBundlePolicy.admitted_source_lanes is required")
        if not self.safe_default:
            raise ValueError("SourceBundlePolicy.safe_default is required")
        if not isinstance(self.detail, Mapping):
            raise ValueError("SourceBundlePolicy.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def evaluate(
        self,
        assertion: SourceAcquisitionAssertion,
        *,
        attestations: tuple[SourceAcquisitionAttestation, ...] = (),
    ) -> SourceBundleAdmission:
        """Evaluate source-bundle admission for one assertion."""

        if assertion.jurisdiction != self.jurisdiction:
            return self._blocked(
                assertion,
                admission_status="jurisdiction_mismatch",
                detail={"assertion_jurisdiction": assertion.jurisdiction},
            )
        if assertion.source_lane in self.blocked_source_lanes:
            return self._blocked(assertion, admission_status="source_lane_blocked")
        if assertion.source_lane not in self.admitted_source_lanes:
            return self._blocked(assertion, admission_status="source_lane_not_admitted")

        relevant = tuple(attestation for attestation in attestations if attestation.assertion_id == assertion.assertion_id)
        blocking_ids = tuple(
            attestation.attestation_id
            for attestation in relevant
            if attestation.attestation_status in {"rejected", "retracted", "blocked"}
        )
        if blocking_ids:
            return self._blocked(
                assertion,
                admission_status="source_attestation_blocked",
                blocking_attestation_ids=blocking_ids,
            )

        attested_kinds = {
            attestation.attestation_kind
            for attestation in relevant
            if attestation.attestation_status in {"accepted", "verified"}
        }
        missing = tuple(kind for kind in self.required_attestation_kinds if kind not in attested_kinds)
        if missing:
            return self._blocked(
                assertion,
                admission_status="source_attestation_missing",
                missing_attestation_kinds=missing,
            )

        return SourceBundleAdmission(
            assertion_id=assertion.assertion_id,
            admitted=True,
            admission_status="source_bundle_admitted",
            policy_id=self.policy_id,
            source_lane=assertion.source_lane,
            safe_default="admit_source_to_bundle_without_authorizing_replay",
            forbidden_shortcuts=self.forbidden_shortcuts,
            detail={
                "jurisdiction": self.jurisdiction,
                "artifact_id": assertion.artifact_id,
                "assertion_kind": assertion.assertion_kind,
                "attestation_count": len(relevant),
            },
        )

    def _blocked(
        self,
        assertion: SourceAcquisitionAssertion,
        *,
        admission_status: str,
        missing_attestation_kinds: tuple[str, ...] = (),
        blocking_attestation_ids: tuple[str, ...] = (),
        detail: Mapping[str, Any] | None = None,
    ) -> SourceBundleAdmission:
        return SourceBundleAdmission(
            assertion_id=assertion.assertion_id,
            admitted=False,
            admission_status=admission_status,
            policy_id=self.policy_id,
            source_lane=assertion.source_lane,
            missing_attestation_kinds=missing_attestation_kinds,
            blocking_attestation_ids=blocking_attestation_ids,
            safe_default=self.safe_default,
            forbidden_shortcuts=self.forbidden_shortcuts,
            detail={
                "jurisdiction": self.jurisdiction,
                "artifact_id": assertion.artifact_id,
                "assertion_kind": assertion.assertion_kind,
                **dict(detail or {}),
            },
        )


def source_bundle_evidence_report(
    admissions: tuple[SourceBundleAdmission, ...],
    *,
    jurisdiction: str,
    assertions: tuple[SourceAcquisitionAssertion, ...] = (),
    attestations: tuple[SourceAcquisitionAttestation, ...] = (),
    report_kind: str = "source_bundle_admission",
    filters: Mapping[str, Any] | None = None,
    rows_truncated: bool = False,
) -> EvidenceSurfaceReport:
    """Project source-bundle policy results without granting replay authority."""

    admission_rows = tuple(
        _source_bundle_admission_row(admission)
        for admission in admissions
    )
    assertion_rows = tuple(
        _source_acquisition_assertion_row(assertion)
        for assertion in assertions
    )
    attestation_rows = tuple(
        _source_acquisition_attestation_row(attestation)
        for attestation in attestations
    )
    status_counts = _count_by(admission.admission_status for admission in admissions)
    lane_counts = _count_by(admission.source_lane for admission in admissions)
    admitted_count = sum(1 for admission in admissions if admission.admitted)
    summary = {
        "assertion_count": len(assertions),
        "attestation_count": len(attestations),
        "admission_count": len(admissions),
        "admitted_count": admitted_count,
        "blocked_count": len(admissions) - admitted_count,
        "status_counts": status_counts,
        "source_lane_counts": lane_counts,
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction,
        report_kind=report_kind,
        schema="lawvm.source_bundle_evidence_report.v1",
        truth_claim="source_bundle_admission_is_source_footing_not_replay_authority",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters=dict(filters or {}),
        filtered_summary=summary,
        rows=(*assertion_rows, *attestation_rows, *admission_rows),
        rows_truncated=rows_truncated,
        detail={
            "safe_default": "treat_source_bundle_admission_as_source_footing_not_replay_authorization",
            "forbidden_shortcuts": _SOURCE_BUNDLE_FORBIDDEN_SHORTCUTS,
            "included_surfaces": (
                "source_acquisition_assertion",
                "source_acquisition_attestation",
                "source_bundle_admission",
            ),
        },
    )


def _source_acquisition_assertion_row(
    assertion: SourceAcquisitionAssertion,
) -> dict[str, Any]:
    return {
        "surface": "source_acquisition_assertion",
        "row_id": assertion.assertion_id,
        "subject_id": assertion.artifact_id,
        "assertion_ref": assertion.assertion_id,
        **assertion.to_dict(),
    }


def _source_acquisition_attestation_row(
    attestation: SourceAcquisitionAttestation,
) -> dict[str, Any]:
    return {
        "surface": "source_acquisition_attestation",
        "row_id": attestation.attestation_id,
        "subject_id": attestation.assertion_id,
        "assertion_ref": attestation.assertion_id,
        **attestation.to_dict(),
    }


def _source_bundle_admission_row(admission: SourceBundleAdmission) -> dict[str, Any]:
    authorization = admission.to_execution_authorization().to_dict()
    authorization_ref = str(authorization.get("authorization_rule_id") or "")
    return {
        "surface": "source_bundle_admission",
        "row_id": authorization_ref,
        "subject_id": admission.assertion_id,
        "assertion_ref": admission.assertion_id,
        "authorization_ref": authorization_ref,
        "proof_ref": admission.policy_id,
        **admission.to_dict(),
        "execution_authorization": authorization,
    }


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _string_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError("expected a sequence of strings, got string")
    return tuple(str(value) for value in values if str(value))


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value


def _count_by(values: Iterable[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


__all__ = [
    "SourceAcquisitionAssertion",
    "SourceAcquisitionAttestation",
    "SourceBundleAdmission",
    "SourceBundlePolicy",
    "source_bundle_evidence_report",
]
