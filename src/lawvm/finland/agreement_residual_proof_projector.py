"""Project Finland oracle/adjudication rows into agreement residual evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from lawvm.core.agreement_residual import (
    AgreementResidual,
    agreement_surface_evidence_report,
    agreement_surface_from_residuals,
)
from lawvm.finland.proof_surface_row_helpers import mapping_sequence

FINLEX_RESIDUAL_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "finlex_oracle_as_source_truth",
    "editorial_witness_as_replay_authorization",
    "agreement_residual_as_mutation_instruction",
)


def finlex_editorial_witness_agreement_residual(
    row: Mapping[str, Any],
    *,
    statute_id: str = "",
) -> AgreementResidual:
    """Project a Finlex editorial witness row into an agreement residual."""

    kind = str(row.get("kind") or "")
    slot_address = str(row.get("slot_address") or "")
    amendment_id = str(row.get("amendment_id") or "")
    timeline_terminator = str(row.get("timeline_terminator") or "")
    residual_id = _stable_residual_id(
        "fi-finlex-editorial-witness",
        statute_id,
        kind,
        slot_address,
        amendment_id,
        timeline_terminator,
    )
    if kind == "editorial_witness_confirmed":
        family = "agreement"
        status = "agrees"
        rule_id = "fi_finlex_inline_repeal_stub_confirmed"
        missing_proofs: tuple[str, ...] = ()
    elif kind == "editorial_witness_disagrees":
        family = "unknown"
        status = "residual"
        rule_id = "fi_finlex_inline_repeal_stub_disagrees"
        missing_proofs = (
            "manual_editorial_witness_triage",
            "timeline_terminator_source_review",
        )
    else:
        family = "source_footing_gap"
        status = "residual"
        rule_id = "fi_finlex_inline_repeal_stub_unresolved"
        missing_proofs = (
            "timeline_terminator_proof",
            "source_lineage_review",
        )
    return AgreementResidual(
        residual_id=residual_id,
        jurisdiction="fi",
        agreement_surface="finlex_inline_repeal_stub",
        family=family,
        status=status,
        owner_phase="oracle_adjudication",
        rule_id=rule_id,
        source_artifact_id=amendment_id or statute_id,
        replay_count=1 if kind == "editorial_witness_confirmed" or timeline_terminator else 0,
        oracle_count=1,
        missing_proofs=missing_proofs,
        safe_default="classify_finlex_editorial_witness_without_authorizing_replay",
        forbidden_shortcuts=FINLEX_RESIDUAL_FORBIDDEN_SHORTCUTS,
        detail={
            "statute_id": statute_id,
            "witness_kind": kind,
            "slot_address": slot_address,
            "amendment_id": amendment_id,
            "timeline_terminator": timeline_terminator,
            "severity": str(row.get("severity") or ""),
        },
    )


def finlex_editorial_witness_agreement_residual_rows(
    rows: tuple[Mapping[str, Any], ...],
    *,
    statute_id: str = "",
) -> list[dict[str, Any]]:
    """Return shared residual rows for Finlex editorial witness records."""

    return [
        finlex_editorial_witness_agreement_residual(row, statute_id=statute_id).to_dict()
        for row in rows
        if str(row.get("kind") or "").startswith("editorial_witness_")
    ]


def source_adjudication_agreement_residual_rows(
    source_adjudication: Any,
    *,
    statute_id: str = "",
) -> list[dict[str, Any]]:
    """Project Finland source/oracle adjudication into agreement residual rows."""

    if source_adjudication is None:
        return []
    adjudication_statute = str(_field(source_adjudication, "statute_id", "") or statute_id or "")
    replay_mode = str(_field(source_adjudication, "replay_mode", "") or "")
    reason = str(_field(source_adjudication, "html_noncommensurable_reason", "") or "").strip()
    if not reason:
        return []
    residual = AgreementResidual(
        residual_id=_stable_residual_id(
            "fi-finlex-source-adjudication",
            adjudication_statute,
            replay_mode,
            reason,
        ),
        jurisdiction="fi",
        agreement_surface="finlex_html_oracle_compare",
        family="non_commensurable_surface",
        status="residual",
        owner_phase="oracle_adjudication",
        rule_id="fi_finlex_html_non_commensurable_surface",
        source_artifact_id=adjudication_statute,
        replay_count=0,
        oracle_count=0,
        missing_proofs=("compare_projection_review",),
        safe_default="classify_non_commensurable_finlex_surface_without_rewriting_replay",
        forbidden_shortcuts=FINLEX_RESIDUAL_FORBIDDEN_SHORTCUTS,
        detail={
            "statute_id": adjudication_statute,
            "replay_mode": replay_mode,
            "html_noncommensurable_reason": reason,
            "cutoff_date": str(_field(source_adjudication, "cutoff_date", "") or ""),
            "oracle_version_amendment_id": str(
                _field(source_adjudication, "oracle_version_amendment_id", "") or ""
            ),
            "oracle_suspect": str(_field(source_adjudication, "oracle_suspect", "") or ""),
        },
    )
    return [residual.to_dict()]


def strict_report_agreement_surface_rows(
    agreement_residuals: tuple[Mapping[str, Any], ...],
    *,
    payload: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Any]]:
    if not agreement_residuals:
        return (), {}
    statute_id = str(payload.get("statute_id") or "unknown")
    surface = agreement_surface_from_residuals(
        agreement_residuals,
        jurisdiction="fi",
        agreement_surface="finlex_oracle_compare",
        materialization_id=f"fi:{statute_id}:materialization",
        comparison_target_id=f"finlex:{statute_id}",
        comparison_kind="residual_classification",
        materialization_kind="legal_text_state",
        comparison_materialization_kind="official_consolidation_view",
        profile_id=str(payload.get("profile") or ""),
    )
    report = agreement_surface_evidence_report(
        surface,
        report_kind="finland_agreement_surface",
    ).to_dict()
    return mapping_sequence(report.get("rows")), dict(report.get("summary") or {})


def _field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _stable_residual_id(*parts: str) -> str:
    normalized = tuple(str(part or "") for part in parts)
    digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=True).encode("utf-8")).hexdigest()[:16]
    prefix = normalized[0] if normalized else "fi-residual"
    return f"{prefix}:{digest}"


__all__ = [
    "FINLEX_RESIDUAL_FORBIDDEN_SHORTCUTS",
    "finlex_editorial_witness_agreement_residual",
    "finlex_editorial_witness_agreement_residual_rows",
    "source_adjudication_agreement_residual_rows",
    "strict_report_agreement_surface_rows",
]
