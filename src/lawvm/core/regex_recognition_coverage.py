"""Typed coverage rows for regex-backed recognizers.

These rows answer a narrow question: which source spans did a regex recognizer
claim, and did it skip any intervening text that is not owned by the rule?
They are passive evidence. They do not authorize replay.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, cast

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


RegexRecognitionCoverageStatus = Literal[
    "fully_classified",
    "unclassified_gap",
    "no_match",
]

REGEX_RECOGNITION_FULLY_CLASSIFIED: RegexRecognitionCoverageStatus = "fully_classified"
REGEX_RECOGNITION_UNCLASSIFIED_GAP: RegexRecognitionCoverageStatus = "unclassified_gap"
REGEX_RECOGNITION_NO_MATCH: RegexRecognitionCoverageStatus = "no_match"

_VALID_STATUSES = frozenset(
    {
        REGEX_RECOGNITION_FULLY_CLASSIFIED,
        REGEX_RECOGNITION_UNCLASSIFIED_GAP,
        REGEX_RECOGNITION_NO_MATCH,
    }
)
_REGEX_RECOGNITION_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "regex_match_as_complete_parse",
    "bounded_wildcard_as_semantic_proof",
    "regex_coverage_as_replay_authorization",
)


@dataclass(frozen=True, slots=True)
class RegexRecognitionCoverage:
    """Passive coverage certificate for one regex recognizer match."""

    coverage_id: str
    jurisdiction: str
    recognizer_id: str
    owner_phase: str
    source_artifact_id: str
    source_text_hash: str
    matched_span: tuple[int, int]
    coverage_status: RegexRecognitionCoverageStatus
    semantic_slots: Mapping[str, Any] = field(default_factory=dict)
    ignored_spans: tuple[Mapping[str, Any], ...] = ()
    required_proofs: tuple[str, ...] = ()
    safe_default: str = "treat_regex_recognition_as_parse_evidence_not_replay_authority"
    forbidden_shortcuts: tuple[str, ...] = _REGEX_RECOGNITION_FORBIDDEN_SHORTCUTS
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name, value in (
            ("coverage_id", self.coverage_id),
            ("jurisdiction", self.jurisdiction),
            ("recognizer_id", self.recognizer_id),
            ("owner_phase", self.owner_phase),
            ("source_text_hash", self.source_text_hash),
            ("coverage_status", self.coverage_status),
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(field_name, value),
            )
        object.__setattr__(self, "source_artifact_id", str(self.source_artifact_id or ""))
        if self.coverage_status not in _VALID_STATUSES:
            raise ValueError(
                "RegexRecognitionCoverage.coverage_status must be one of "
                f"{sorted(_VALID_STATUSES)}"
            )
        matched_span = _ordered_span("matched_span", self.matched_span)
        object.__setattr__(self, "matched_span", matched_span)
        if not isinstance(self.semantic_slots, Mapping):
            raise ValueError("RegexRecognitionCoverage.semantic_slots must be a mapping")
        object.__setattr__(self, "semantic_slots", freeze_mapping(self.semantic_slots))
        ignored_spans = _validated_ignored_span_rows(self.ignored_spans, matched_span)
        object.__setattr__(
            self,
            "ignored_spans",
            tuple(freeze_mapping(row) for row in ignored_spans),
        )
        required_proofs = _string_tuple("required_proofs", self.required_proofs)
        _validate_gap_status(
            coverage_status=self.coverage_status,
            ignored_spans=ignored_spans,
            required_proofs=required_proofs,
        )
        object.__setattr__(self, "required_proofs", required_proofs)
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple("forbidden_shortcuts", self.forbidden_shortcuts),
        )
        if not self.safe_default:
            raise ValueError("RegexRecognitionCoverage.safe_default is required")
        if not self.forbidden_shortcuts:
            raise ValueError("RegexRecognitionCoverage.forbidden_shortcuts is required")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_id": self.coverage_id,
            "jurisdiction": self.jurisdiction,
            "recognizer_id": self.recognizer_id,
            "owner_phase": self.owner_phase,
            "source_artifact_id": self.source_artifact_id,
            "source_text_hash": self.source_text_hash,
            "matched_span": list(self.matched_span),
            "coverage_status": self.coverage_status,
            "semantic_slots": _plain_jsonable(self.semantic_slots),
            "ignored_spans": [_plain_jsonable(row) for row in self.ignored_spans],
            "required_proofs": list(self.required_proofs),
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def regex_recognition_coverage_evidence_report(
    coverage_rows: (
        RegexRecognitionCoverage
        | Mapping[str, Any]
        | tuple[RegexRecognitionCoverage | Mapping[str, Any], ...]
    ),
    *,
    jurisdiction: str,
    report_kind: str = "regex_recognition_coverage",
) -> EvidenceSurfaceReport:
    rows = tuple(_coverage_mapping(row) for row in _coverage_sequence(coverage_rows))
    status_counts = _counts(str(row.get("coverage_status") or "") for row in rows)
    recognizer_counts = _counts(str(row.get("recognizer_id") or "") for row in rows)
    unclassified_gap_count = status_counts.get(REGEX_RECOGNITION_UNCLASSIFIED_GAP, 0)
    summary = {
        "regex_recognition_coverage_count": len(rows),
        "coverage_status_counts": status_counts,
        "recognizer_counts": recognizer_counts,
        "unclassified_gap_count": unclassified_gap_count,
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
        schema="lawvm.regex_recognition_coverage.v1",
        truth_claim="regex recognizer span coverage rows",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=tuple(_coverage_report_row(row) for row in rows),
        detail={
            "safe_default": "treat_regex_coverage_as_parse_diagnostics_not_replay_authority",
            "forbidden_shortcuts": _REGEX_RECOGNITION_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("regex_recognition_coverage",),
        },
    )


def regex_source_text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _coverage_sequence(
    value: (
        RegexRecognitionCoverage
        | Mapping[str, Any]
        | tuple[RegexRecognitionCoverage | Mapping[str, Any], ...]
    ),
) -> tuple[RegexRecognitionCoverage | Mapping[str, Any], ...]:
    if isinstance(value, RegexRecognitionCoverage):
        return (value,)
    if isinstance(value, Mapping):
        return (cast(Mapping[str, Any], value),)
    return tuple(value)


def _coverage_mapping(value: RegexRecognitionCoverage | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value, RegexRecognitionCoverage):
        return value.to_dict()
    row = dict(value)
    return RegexRecognitionCoverage(
        coverage_id=str(row.get("coverage_id") or ""),
        jurisdiction=str(row.get("jurisdiction") or ""),
        recognizer_id=str(row.get("recognizer_id") or ""),
        owner_phase=str(row.get("owner_phase") or ""),
        source_artifact_id=str(row.get("source_artifact_id") or ""),
        source_text_hash=str(row.get("source_text_hash") or ""),
        matched_span=_span_tuple(row.get("matched_span")),
        coverage_status=_coverage_status(row.get("coverage_status")),
        semantic_slots=_mapping(row.get("semantic_slots")),
        ignored_spans=tuple(_mapping(item) for item in _sequence(row.get("ignored_spans"))),
        required_proofs=_string_tuple("required_proofs", _sequence(row.get("required_proofs"))),
        safe_default=str(row.get("safe_default") or ""),
        forbidden_shortcuts=_string_tuple("forbidden_shortcuts", _sequence(row.get("forbidden_shortcuts"))),
        detail=_mapping(row.get("detail")),
    ).to_dict()


def _coverage_status(value: Any) -> RegexRecognitionCoverageStatus:
    text = str(value or "")
    if text not in _VALID_STATUSES:
        raise ValueError(
            "RegexRecognitionCoverage.coverage_status must be one of "
            f"{sorted(_VALID_STATUSES)}"
        )
    return cast(RegexRecognitionCoverageStatus, text)


def _coverage_report_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "surface": "regex_recognition_coverage",
        "row_id": str(row.get("coverage_id") or ""),
        "coverage_ref": str(row.get("coverage_id") or ""),
        "recognizer_id": str(row.get("recognizer_id") or ""),
        "row_status": str(row.get("coverage_status") or ""),
        "coverage_status": str(row.get("coverage_status") or ""),
        "owner_phase": str(row.get("owner_phase") or ""),
        "source_artifact_id": str(row.get("source_artifact_id") or ""),
        "matched_span": _ordered_span("matched_span", row.get("matched_span")),
        "source_text_hash": str(row.get("source_text_hash") or ""),
        "semantic_slots": _mapping(row.get("semantic_slots")),
        "ignored_spans": tuple(_mapping(item) for item in _sequence(row.get("ignored_spans"))),
        "required_proofs": tuple(str(item) for item in _sequence(row.get("required_proofs"))),
        "safe_default": str(row.get("safe_default") or ""),
        "forbidden_shortcuts": tuple(
            dict.fromkeys(
                (
                    *tuple(str(item) for item in _sequence(row.get("forbidden_shortcuts"))),
                    *_REGEX_RECOGNITION_FORBIDDEN_SHORTCUTS,
                )
            )
        ),
        "detail": _mapping(row.get("detail")),
    }


def _span_tuple(value: Any) -> tuple[int, int]:
    return _ordered_span("matched_span", value)


def _ordered_span(field_name: str, value: Any) -> tuple[int, int]:
    seq = _sequence(value)
    if len(seq) != 2:
        raise ValueError(f"RegexRecognitionCoverage.{field_name} must contain exactly two offsets")
    start = _offset(f"{field_name}[0]", seq[0])
    end = _offset(f"{field_name}[1]", seq[1])
    if end < start:
        raise ValueError(
            f"RegexRecognitionCoverage.{field_name} must be ordered non-negative offsets"
        )
    return (start, end)


def _offset(field_name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"RegexRecognitionCoverage.{field_name} must be an integer offset")
    if value < 0:
        raise ValueError(f"RegexRecognitionCoverage.{field_name} must be non-negative")
    return value


def _validated_ignored_span_rows(
    rows: Any,
    matched_span: tuple[int, int],
) -> tuple[Mapping[str, Any], ...]:
    validated: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("RegexRecognitionCoverage.ignored_spans rows must be mappings")
        normalized = dict(row)
        if "span" in normalized and normalized["span"] is not None:
            ignored_span = _ordered_span("ignored_spans[].span", normalized["span"])
            if ignored_span[0] < matched_span[0] or ignored_span[1] > matched_span[1]:
                raise ValueError(
                    "RegexRecognitionCoverage.ignored_spans must stay within matched_span"
                )
            normalized["span"] = ignored_span
        validated.append(normalized)
    return tuple(validated)


def _validate_gap_status(
    *,
    coverage_status: RegexRecognitionCoverageStatus,
    ignored_spans: tuple[Mapping[str, Any], ...],
    required_proofs: tuple[str, ...],
) -> None:
    meaning_gap_count = sum(1 for row in ignored_spans if _ignored_span_needs_proof(row))
    if coverage_status == REGEX_RECOGNITION_FULLY_CLASSIFIED and meaning_gap_count:
        raise ValueError(
            "RegexRecognitionCoverage.coverage_status cannot be fully_classified "
            "when ignored_spans contain unclassified or meaning-altering text"
        )
    if coverage_status == REGEX_RECOGNITION_UNCLASSIFIED_GAP and not meaning_gap_count:
        raise ValueError(
            "RegexRecognitionCoverage.coverage_status unclassified_gap requires "
            "an unclassified or meaning-altering ignored span"
        )
    if (
        coverage_status == REGEX_RECOGNITION_UNCLASSIFIED_GAP
        and "regex_skipped_span_classification" not in required_proofs
    ):
        raise ValueError(
            "RegexRecognitionCoverage.required_proofs must include "
            "regex_skipped_span_classification for unclassified_gap rows"
        )


def _ignored_span_needs_proof(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("classification") or "") == "unclassified"
        or row.get("could_alter_meaning") is True
    )


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, list | tuple):
        return tuple(value)
    return ()


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"RegexRecognitionCoverage.{field_name} is required")
    return text


def _string_tuple(field_name: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"RegexRecognitionCoverage.{field_name} must be a sequence")
    return tuple(str(item) for item in value if str(item))


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
