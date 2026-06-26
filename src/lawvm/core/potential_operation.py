"""Shared potential-operation coverage surface.

``PotentialOperation`` is an accounting row for a source-derived operation cue
or a visible compiled/failed operation candidate. It is passive evidence: it
does not authorize replay and does not prove source-text cue exhaustiveness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, cast

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


PotentialOperationClassification = Literal[
    "compiled",
    "failed",
    "suppressed",
    "unclassified",
    "blocked",
]

POTENTIAL_OPERATION_COMPILED: PotentialOperationClassification = "compiled"
POTENTIAL_OPERATION_FAILED: PotentialOperationClassification = "failed"
POTENTIAL_OPERATION_SUPPRESSED: PotentialOperationClassification = "suppressed"
POTENTIAL_OPERATION_UNCLASSIFIED: PotentialOperationClassification = "unclassified"
POTENTIAL_OPERATION_BLOCKED: PotentialOperationClassification = "blocked"

_VALID_CLASSIFICATIONS = frozenset(
    {
        POTENTIAL_OPERATION_COMPILED,
        POTENTIAL_OPERATION_FAILED,
        POTENTIAL_OPERATION_SUPPRESSED,
        POTENTIAL_OPERATION_UNCLASSIFIED,
        POTENTIAL_OPERATION_BLOCKED,
    }
)
_POTENTIAL_OPERATION_REPORT_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "potential_operation_as_replay_authorization",
    "potential_operation_as_canonical_operation",
    "visible_potential_operations_as_source_cue_exhaustiveness_proof",
)


@dataclass(frozen=True, slots=True)
class PotentialOperation:
    """Passive row for an operation cue/candidate in a declared coverage scope."""

    potential_operation_id: str
    jurisdiction: str
    source_artifact_id: str
    source_unit_id: str
    owner_phase: str
    classification: PotentialOperationClassification
    operation_family: str
    action: str = ""
    target: str = ""
    source_anchor: Mapping[str, Any] = field(default_factory=dict)
    refs: tuple[str, ...] = ()
    required_proofs: tuple[str, ...] = ()
    safe_default: str = ""
    forbidden_shortcuts: tuple[str, ...] = _POTENTIAL_OPERATION_REPORT_FORBIDDEN_SHORTCUTS
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name, value in (
            ("potential_operation_id", self.potential_operation_id),
            ("jurisdiction", self.jurisdiction),
            ("source_artifact_id", self.source_artifact_id),
            ("source_unit_id", self.source_unit_id),
            ("owner_phase", self.owner_phase),
            ("classification", self.classification),
            ("operation_family", self.operation_family),
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(field_name, value),
            )
        if self.classification not in _VALID_CLASSIFICATIONS:
            raise ValueError(
                "PotentialOperation.classification must be one of "
                f"{sorted(_VALID_CLASSIFICATIONS)}"
            )
        object.__setattr__(self, "action", str(self.action or ""))
        object.__setattr__(self, "target", str(self.target or ""))
        object.__setattr__(self, "refs", _string_tuple("refs", self.refs))
        object.__setattr__(
            self,
            "required_proofs",
            _string_tuple("required_proofs", self.required_proofs),
        )
        if not self.safe_default:
            raise ValueError("PotentialOperation.safe_default is required")
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple("forbidden_shortcuts", self.forbidden_shortcuts),
        )
        if not self.forbidden_shortcuts:
            raise ValueError("PotentialOperation.forbidden_shortcuts is required")
        if not isinstance(self.source_anchor, Mapping):
            raise ValueError("PotentialOperation.source_anchor must be a mapping")
        if not isinstance(self.detail, Mapping):
            raise ValueError("PotentialOperation.detail must be a mapping")
        object.__setattr__(self, "source_anchor", freeze_mapping(self.source_anchor))
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "potential_operation_id": self.potential_operation_id,
            "jurisdiction": self.jurisdiction,
            "source_artifact_id": self.source_artifact_id,
            "source_unit_id": self.source_unit_id,
            "source_anchor": _plain_jsonable(self.source_anchor),
            "owner_phase": self.owner_phase,
            "classification": self.classification,
            "operation_family": self.operation_family,
            "action": self.action,
            "target": self.target,
            "refs": list(self.refs),
            "required_proofs": list(self.required_proofs),
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def potential_operation_evidence_report(
    operations: (
        PotentialOperation
        | Mapping[str, Any]
        | tuple[PotentialOperation | Mapping[str, Any], ...]
    ),
    *,
    jurisdiction: str,
    report_kind: str = "potential_operation_coverage",
) -> EvidenceSurfaceReport:
    """Project potential-operation rows into a passive evidence report."""

    rows = tuple(_potential_operation_mapping(row) for row in _operation_sequence(operations))
    class_counts = _counts(str(row.get("classification") or "") for row in rows)
    family_counts = _counts(str(row.get("operation_family") or "") for row in rows)
    phase_counts = _counts(str(row.get("owner_phase") or "") for row in rows)
    summary = {
        "potential_operation_count": len(rows),
        "classification_counts": class_counts,
        "operation_family_counts": family_counts,
        "owner_phase_counts": phase_counts,
        "compiled_count": class_counts.get(POTENTIAL_OPERATION_COMPILED, 0),
        "failed_count": class_counts.get(POTENTIAL_OPERATION_FAILED, 0),
        "unclassified_count": class_counts.get(POTENTIAL_OPERATION_UNCLASSIFIED, 0),
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction,
        report_kind=report_kind,
        schema="lawvm.potential_operation_coverage.v1",
        truth_claim="declared potential-operation coverage rows",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=tuple(_potential_operation_report_row(row) for row in rows),
        detail={
            "safe_default": "treat_potential_operations_as_coverage_evidence_not_replay_authority",
            "forbidden_shortcuts": _POTENTIAL_OPERATION_REPORT_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("potential_operation",),
        },
    )


def _operation_sequence(
    value: (
        PotentialOperation
        | Mapping[str, Any]
        | tuple[PotentialOperation | Mapping[str, Any], ...]
    ),
) -> tuple[PotentialOperation | Mapping[str, Any], ...]:
    if isinstance(value, PotentialOperation) or isinstance(value, Mapping):
        return (cast(PotentialOperation | Mapping[str, Any], value),)
    return tuple(value)


def _potential_operation_mapping(
    value: PotentialOperation | Mapping[str, Any],
) -> Mapping[str, Any]:
    if isinstance(value, PotentialOperation):
        return value.to_dict()
    return PotentialOperation(
        potential_operation_id=str(value.get("potential_operation_id") or ""),
        jurisdiction=str(value.get("jurisdiction") or ""),
        source_artifact_id=str(value.get("source_artifact_id") or ""),
        source_unit_id=str(value.get("source_unit_id") or ""),
        source_anchor=_mapping_or_empty(value.get("source_anchor")),
        owner_phase=str(value.get("owner_phase") or ""),
        classification=cast(PotentialOperationClassification, str(value.get("classification") or "")),
        operation_family=str(value.get("operation_family") or ""),
        action=str(value.get("action") or ""),
        target=str(value.get("target") or ""),
        refs=_string_tuple("refs", value.get("refs", ())),
        required_proofs=_string_tuple("required_proofs", value.get("required_proofs", ())),
        safe_default=str(value.get("safe_default") or ""),
        forbidden_shortcuts=_string_tuple(
            "forbidden_shortcuts",
            value.get("forbidden_shortcuts", _POTENTIAL_OPERATION_REPORT_FORBIDDEN_SHORTCUTS),
        ),
        detail=_mapping_or_empty(value.get("detail")),
    ).to_dict()


def _potential_operation_report_row(row: Mapping[str, Any]) -> dict[str, Any]:
    operation_id = str(row.get("potential_operation_id") or "")
    return {
        **dict(row),
        "surface": "potential_operation",
        "row_id": operation_id,
        "subject_id": str(row.get("source_unit_id") or operation_id),
        "row_status": str(row.get("classification") or ""),
        "forbidden_shortcuts": list(row.get("forbidden_shortcuts") or ()),
    }


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"PotentialOperation.{field_name} is required")
    return text


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError(f"PotentialOperation.{field_name} must be a sequence")
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"PotentialOperation.{field_name} must be a sequence")
    return tuple(str(value) for value in values if str(value))


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    raise ValueError("PotentialOperation mapping fields must be mappings")


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        if key:
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
