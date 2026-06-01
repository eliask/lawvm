"""Shared JSON payload builders for replay-oriented CLI commands."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from lawvm.core.agreement_residual import AgreementResidual
from lawvm.core.compile_records import is_blocking_compile_record
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.adjudication_evidence import (
    adjudication_finding_evidence_rows,
    adjudication_kind_counts,
    text_or_none,
)


def _address_to_str(address: Any) -> str:
    path = getattr(address, "path", None)
    if not path:
        return str(address)
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _text_or_none(value: Any) -> str | None:
    return text_or_none(value)


def _text_field(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _adjudication_to_dict(adjudication: Any) -> dict[str, Any]:
    return {
        "kind": _text_field(getattr(adjudication, "kind", None), default="compile_adjudication"),
        "message": _text_field(getattr(adjudication, "message", None)),
        "source_statute": _text_field(getattr(adjudication, "source_statute", None)),
        "op_id": _text_field(getattr(adjudication, "op_id", None)),
        "detail": dict(getattr(adjudication, "detail", {}) or {}),
    }


def _rejection_rule_counts(rejections: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rejection in rejections:
        rule_id = _text_field(rejection.get("rule_id"), default="unknown")
        counts[rule_id] = counts.get(rule_id, 0) + 1
    return dict(sorted(counts.items()))


def _record_field_counts(
    records: Iterable[dict[str, Any]],
    field: str,
    *,
    default: str = "__none__",
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        key = _text_field(record.get(field), default=default) or default
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _record_nested_field_counts(
    records: Iterable[dict[str, Any]],
    mapping_field: str,
    field: str,
    *,
    default: str = "__none__",
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        mapping = record.get(mapping_field)
        if not isinstance(mapping, Mapping):
            continue
        key = _text_field(mapping.get(field), default=default) or default
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _record_required_proof_counts(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if record.get("replay_authorized") is True:
            continue
        proofs = record.get("required_proofs") or ()
        if not isinstance(proofs, list | tuple):
            counts["invalid_required_proofs_shape"] = (
                counts.get("invalid_required_proofs_shape", 0) + 1
            )
            continue
        for proof in proofs:
            key = _text_field(proof, default="unknown") or "unknown"
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _record_agreement_residual_field_counts(
    records: Iterable[Mapping[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        residual = record.get("agreement_residual")
        if not isinstance(residual, Mapping):
            continue
        key = _text_field(residual.get(field), default="unknown") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _uk_replay_adjudication_bucket_counts(adjudications: Iterable[Any]) -> dict[str, int]:
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_replay_adjudication_bucket,
    )

    counts: dict[str, int] = {}
    for adjudication in adjudications:
        kind = _text_field(getattr(adjudication, "kind", None), default="unknown")
        bucket = classify_uk_replay_adjudication_bucket(kind)
        counts[bucket] = counts.get(bucket, 0) + 1
    return dict(sorted(counts.items()))


def _uk_replay_adjudication_owner_phase_counts(
    adjudications: Iterable[Any],
) -> dict[str, int]:
    from lawvm.uk_legislation.phase_discipline import (
        uk_phase_owner_counts_for_replay_adjudications,
    )

    return uk_phase_owner_counts_for_replay_adjudications(adjudications)


def _uk_replay_adjudication_to_dict(adjudication: Any) -> dict[str, Any]:
    from lawvm.uk_legislation.execution_authorization import (
        uk_execution_authorization_from_replay_adjudication,
    )
    from lawvm.uk_legislation.phase_discipline import (
        uk_phase_owner_for_replay_adjudication,
    )
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_replay_adjudication_bucket,
    )

    payload = _adjudication_to_dict(adjudication)
    owner_phase = uk_phase_owner_for_replay_adjudication(adjudication)
    bucket = classify_uk_replay_adjudication_bucket(payload["kind"])
    authorization = uk_execution_authorization_from_replay_adjudication(
        adjudication=adjudication,
        owner_phase=owner_phase,
        bucket=bucket,
    ).to_dict()
    payload["owner_phase"] = owner_phase
    payload["execution_authorization"] = authorization
    payload["executable"] = authorization["executable"]
    payload["replay_authorized"] = authorization["replay_authorized"]
    payload["authorization_status"] = authorization["authorization_status"]
    payload["authorization_rule_id"] = authorization["authorization_rule_id"]
    payload["required_proofs"] = authorization["required_proofs"]
    payload["safe_default"] = authorization["safe_default"]
    payload["forbidden_shortcuts"] = authorization["forbidden_shortcuts"]
    payload["agreement_residual"] = _uk_replay_adjudication_agreement_residual(
        payload=payload,
        bucket=bucket,
        owner_phase=owner_phase,
        authorization=authorization,
    ).to_dict()
    return payload


def _uk_replay_adjudication_agreement_residual(
    *,
    payload: Mapping[str, Any],
    bucket: str,
    owner_phase: str,
    authorization: Mapping[str, Any],
) -> AgreementResidual:
    kind = _text_field(payload.get("kind"), default="unknown")
    op_id = _text_field(payload.get("op_id"))
    source_statute = _text_field(payload.get("source_statute"))
    suffix = op_id or kind
    return AgreementResidual(
        residual_id=f"uk-replay-adjudication:{source_statute}:{suffix}",
        jurisdiction="uk",
        agreement_surface="replay_adjudication",
        family=_uk_replay_adjudication_residual_family(bucket),
        status=_uk_replay_adjudication_residual_status(bucket),
        owner_phase=owner_phase,
        rule_id=f"uk_replay_adjudication_{bucket or 'unknown'}",
        source_artifact_id=source_statute,
        replay_count=0,
        oracle_count=0,
        missing_proofs=tuple(str(proof) for proof in authorization.get("required_proofs") or ()),
        safe_default="treat_adjudication_as_residual_not_replay_authority",
        forbidden_shortcuts=(
            "adjudication_as_replay_authority",
            "oracle_backed_mutation",
            "residual_over_promotion",
        ),
        detail={
            "kind": kind,
            "bucket": bucket,
            "op_id": op_id,
            "authorization_status": authorization.get("authorization_status"),
        },
    )


def _uk_replay_adjudication_residual_family(bucket: str) -> str:
    if bucket == "replay_bug":
        return "replay_bug"
    if bucket == "source_shape":
        return "source_pathology"
    if bucket == "text_surface":
        return "topology_granularity_mismatch"
    if bucket == "nonblocking_observation":
        return "accepted_non_executable_frontier"
    return "unknown"


def _uk_replay_adjudication_residual_status(bucket: str) -> str:
    if bucket == "nonblocking_observation":
        return "frontier"
    return "residual"


def _blocking_rejections(rejections: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [rejection for rejection in rejections if is_blocking_compile_record(rejection)]


def _with_uk_compile_authorization(
    records: Iterable[Mapping[str, Any]],
    *,
    lane: str,
    source_artifact_id: str = "",
) -> list[dict[str, Any]]:
    from lawvm.uk_legislation.execution_authorization import (
        uk_execution_authorization_from_compile_record,
    )
    from lawvm.uk_legislation.phase_discipline import uk_phase_owner_for_diagnostic

    rows: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        if source_artifact_id and not row.get("source_artifact_id"):
            row["source_artifact_id"] = source_artifact_id
        owner_phase = uk_phase_owner_for_diagnostic(row)
        row["owner_phase"] = owner_phase
        authorization = uk_execution_authorization_from_compile_record(
            record=row,
            lane=lane,
            owner_phase=owner_phase,
        ).to_dict()
        row["execution_authorization"] = authorization
        row["executable"] = authorization["executable"]
        row["replay_authorized"] = authorization["replay_authorized"]
        row["authorization_status"] = authorization["authorization_status"]
        row["authorization_rule_id"] = authorization["authorization_rule_id"]
        row["required_proofs"] = authorization["required_proofs"]
        row["safe_default"] = authorization["safe_default"]
        row["forbidden_shortcuts"] = authorization["forbidden_shortcuts"]
        if lane == "manual_compile_frontier" and row["replay_authorized"] is False:
            from lawvm.uk_legislation.frontier_work_items import (
                uk_frontier_work_item_from_manual_frontier_row,
            )

            row["frontier_work_item"] = (
                uk_frontier_work_item_from_manual_frontier_row(row).to_dict()
            )
        rows.append(row)
    return rows


def _uk_diagnostic_owner_phase_counts(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    from lawvm.uk_legislation.phase_discipline import uk_phase_owner_counts_for_diagnostics

    return uk_phase_owner_counts_for_diagnostics(records)


def build_no_replay_payload(
    result: Any,
    *,
    archive_path: str | None = None,
    index_path: str | None = None,
    commencement_path: str | None = None,
    index_stale: bool = False,
    replayed_text: str | None = None,
) -> dict[str, Any]:
    adjudications = list(getattr(result, "adjudications", []) or [])
    finding_rows = adjudication_finding_evidence_rows(
        adjudications,
        frontend_id="norway",
        base_id=result.base_id,
        as_of=result.as_of,
    )
    return {
        "jurisdiction": "no",
        "base_id": result.base_id,
        "as_of": result.as_of,
        "title": _text_or_none(getattr(result, "base_title", None)),
        "error": _text_or_none(getattr(result, "error", None)),
        "mode": "replay",
        "ops_count": int(getattr(result, "n_ops", 0) or 0),
        "adjudications_count": len(adjudications),
        "adjudication_kind_counts": adjudication_kind_counts(adjudications),
        "adjudications": [_adjudication_to_dict(adjudication) for adjudication in adjudications],
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
        },
        "source": {
            "archive": archive_path,
            "index": index_path,
            "commencement": commencement_path,
            "index_stale": index_stale,
        },
        "amendment_counts": {
            "total": len(getattr(result, "amendments_scanned", []) or []),
            "matched": len(getattr(result, "amendments_scanned", []) or []),
            "applied": len(getattr(result, "amendments_applied", []) or []),
            "failed": 0,
            "future": len(getattr(result, "amendments_skipped_future", []) or []),
            "contingent": len(getattr(result, "amendments_skipped_contingent", []) or []),
            "unknown_effective": len(getattr(result, "amendments_skipped_unknown_effective", []) or []),
            "missing_source": len(getattr(result, "amendments_skipped_missing_source", []) or []),
        },
        "applied_amendments": list(getattr(result, "amendments_applied", []) or []),
        "failed_amendments": [],
        "skipped_amendments": {
            "future": list(getattr(result, "amendments_skipped_future", []) or []),
            "contingent": list(getattr(result, "amendments_skipped_contingent", []) or []),
            "unknown_effective": list(getattr(result, "amendments_skipped_unknown_effective", []) or []),
            "missing_source": list(getattr(result, "amendments_skipped_missing_source", []) or []),
        },
        "oracle": {
            "available": False,
            "id": None,
            "comparison_class": None,
            "eid_similarity": None,
        },
        "consistency": {
            "consistent": None,
            "divergence_count": None,
            "mismatch_count": None,
            "ops_missing_count": None,
            "consolidated_missing_count": None,
        },
        "timeline": {
            "mode": None,
            "provisions": None,
            "versions": None,
            "pit_materialized_eids": None,
        },
        "divergences": [],
        "replayed_text": replayed_text,
    }


def build_ee_replay_payload(
    result: Any,
    *,
    archive_path: str | None = None,
    replayed_text: str | None = None,
    residual_summary: Any = None,
) -> dict[str, Any]:
    residual_by_address = {}
    if residual_summary is not None:
        residual_by_address = getattr(residual_summary, "record_by_address", {}) or {}
    adjudications = list(getattr(result, "adjudications", []) or [])
    finding_rows = adjudication_finding_evidence_rows(
        adjudications,
        frontend_id="estonia",
        base_id=result.base_id,
        as_of=result.as_of,
    )
    divergences = []
    for divergence in list(getattr(result, "divergences", []) or []):
        address = _address_to_str(getattr(divergence, "address", ""))
        record = residual_by_address.get(address)
        divergences.append(
            {
                "address": address,
                "divergence_type": _text_or_none(getattr(divergence, "divergence_type", None)),
                "replay_text": _text_or_none(getattr(divergence, "ops_text", None)),
                "oracle_text": _text_or_none(getattr(divergence, "consolidated_text", None)),
                "residual_bucket": _text_or_none(getattr(record, "bucket", None)) if record else None,
                "residual_evidence": _text_or_none(getattr(record, "evidence", None)) if record else None,
            }
        )
    return {
        "jurisdiction": "ee",
        "base_id": result.base_id,
        "as_of": result.as_of,
        "title": _text_or_none(getattr(result, "base_title", None)),
        "error": _text_or_none(getattr(result, "error", None)),
        "mode": "replay",
        "ops_count": int(getattr(result, "n_ops", 0) or 0),
        "adjudications_count": len(adjudications),
        "adjudication_kind_counts": adjudication_kind_counts(adjudications),
        "adjudications": [_adjudication_to_dict(adjudication) for adjudication in adjudications],
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
        },
        "source": {
            "archive": archive_path,
            "index": None,
            "commencement": None,
            "index_stale": None,
        },
        "amendment_counts": {
            "total": len(getattr(result, "amendments_total", []) or []),
            "matched": len(getattr(result, "amendments_total", []) or []),
            "applied": len(getattr(result, "amendments_applied", []) or []),
            "failed": len(getattr(result, "amendments_failed", []) or []),
            "future": None,
            "contingent": None,
            "unknown_effective": None,
        },
        "applied_amendments": list(getattr(result, "amendments_applied", []) or []),
        "failed_amendments": list(getattr(result, "amendments_failed", []) or []),
        "skipped_amendments": {
            "other": list(getattr(result, "amendments_skipped", []) or []),
        },
        "oracle": {
            "available": getattr(result, "oracle", None) is not None,
            "id": _text_or_none(getattr(result, "oracle_id", None)),
            "comparison_class": _text_or_none(getattr(result, "comparison_class", None)),
            "eid_similarity": None,
        },
        "consistency": {
            "consistent": None if getattr(result, "oracle", None) is None else len(getattr(result, "divergences", []) or []) == 0,
            "divergence_count": len(getattr(result, "divergences", []) or []),
            "mismatch_count": int(getattr(result, "n_mismatch", 0) or 0),
            "ops_missing_count": int(getattr(result, "n_ops_missing", 0) or 0),
            "consolidated_missing_count": int(getattr(result, "n_con_missing", 0) or 0),
        },
        "timeline": {
            "mode": None,
            "provisions": len(getattr(result, "timelines", {}) or {}),
            "versions": None,
            "pit_materialized_eids": None,
        },
        "divergences": divergences,
        "replayed_text": replayed_text,
    }


def build_uk_replay_payload(
    *,
    statute_id: str,
    pit_date: str | None,
    enacted_only: bool,
    db_path: str,
    n_effects: int | None,
    n_ops: int,
    similarity: float | None,
    comparison_class: str | None,
    oracle_available: bool,
    n_provisions: int,
    n_versions: int | None,
    pit_materialized_eids: int | None,
    timeline_mode: str,
    enacted_url: str | None = None,
    oracle_url: str | None = None,
    enacted_source_status: str | None = None,
    oracle_source_status: str | None = None,
    enacted_source_size: int | None = None,
    oracle_source_size: int | None = None,
    enacted_source_sha256: str | None = None,
    oracle_source_sha256: str | None = None,
    base_eid_count: int | None = None,
    replayed_eid_count: int | None = None,
    oracle_eid_count: int | None = None,
    replay_compare_eid_count: int | None = None,
    oracle_compare_eid_count: int | None = None,
    common_eid_count: int | None = None,
    only_in_replayed_count: int | None = None,
    only_in_oracle_count: int | None = None,
    only_in_replayed_sample: Iterable[str] = (),
    only_in_oracle_sample: Iterable[str] = (),
    core_benchmark: bool | None = None,
    replayed_text: str | None = None,
    adjudications: Iterable[Any] = (),
    effect_feed_parse_rejections: Iterable[dict[str, Any]] = (),
    lowering_rejections: Iterable[dict[str, Any]] = (),
    authority_rejections: Iterable[dict[str, Any]] = (),
    source_parse_rejections: Iterable[dict[str, Any]] = (),
    effect_source_pathology_observations: Iterable[dict[str, Any]] = (),
    manual_compile_frontier_observations: Iterable[dict[str, Any]] = (),
    source_acquisition_rejections: Iterable[dict[str, Any]] = (),
    uk_replay_regime: dict[str, Any] | None = None,
    uk_oracle_alignment_summary: dict[str, Any] | None = None,
    uk_commencement_summary: dict[str, Any] | None = None,
    uk_prefetch_report: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    replay_adjudications = list(adjudications)
    replay_adjudication_rows = [
        _uk_replay_adjudication_to_dict(adjudication)
        for adjudication in replay_adjudications
    ]
    source_parse_rejection_rows = _with_uk_compile_authorization(
        source_parse_rejections,
        lane="source_parse",
    )
    effect_feed_parse_rejection_rows = _with_uk_compile_authorization(
        effect_feed_parse_rejections,
        lane="effect_feed_parse",
    )
    effect_source_pathology_rows = _with_uk_compile_authorization(
        effect_source_pathology_observations,
        lane="effect_source_pathology",
    )
    manual_compile_frontier_rows = _with_uk_compile_authorization(
        manual_compile_frontier_observations,
        lane="manual_compile_frontier",
        source_artifact_id=statute_id,
    )
    source_acquisition_rejection_rows = _with_uk_compile_authorization(
        source_acquisition_rejections,
        lane="source_acquisition",
    )
    lowering_rejection_rows = _with_uk_compile_authorization(
        lowering_rejections,
        lane="lowering",
    )
    authority_rejection_rows = _with_uk_compile_authorization(
        authority_rejections,
        lane="authority",
    )
    compile_observations = [
        *source_parse_rejection_rows,
        *effect_feed_parse_rejection_rows,
        *effect_source_pathology_rows,
        *manual_compile_frontier_rows,
        *source_acquisition_rejection_rows,
        *lowering_rejection_rows,
        *authority_rejection_rows,
    ]
    blocking_source_parse_rejections = _blocking_rejections(source_parse_rejection_rows)
    blocking_effect_feed_parse_rejections = _blocking_rejections(effect_feed_parse_rejection_rows)
    blocking_effect_source_pathology_rejections = _blocking_rejections(
        effect_source_pathology_rows
    )
    blocking_manual_compile_frontier_rejections = _blocking_rejections(
        manual_compile_frontier_rows
    )
    blocking_source_acquisition_rejections = _blocking_rejections(
        source_acquisition_rejection_rows
    )
    blocking_lowering_rejections = _blocking_rejections(lowering_rejection_rows)
    blocking_authority_rejections = _blocking_rejections(authority_rejection_rows)
    blocking_compile_rejections = _blocking_rejections(compile_observations)
    source_parse_failed_sides = {
        _text_field(row.get("side"))
        for row in blocking_source_parse_rejections
        if _text_field(row.get("side"))
    }
    finding_rows = adjudication_finding_evidence_rows(
        replay_adjudications,
        frontend_id="uk",
        base_id=statute_id,
        as_of=pit_date or "latest",
    )
    legacy_payload = {
        "jurisdiction": "uk",
        "base_id": statute_id,
        "as_of": pit_date,
        "title": None,
        "error": error,
        "mode": "enacted_only" if enacted_only else "replay",
        "uk_replay_regime": dict(uk_replay_regime or {}),
        "uk_oracle_alignment_summary": dict(uk_oracle_alignment_summary or {}),
        "uk_commencement_summary": dict(uk_commencement_summary or {}),
        "uk_prefetch_report": dict(uk_prefetch_report or {}),
        "ops_count": int(n_ops),
        "adjudications_count": len(replay_adjudications),
        "adjudication_kind_counts": adjudication_kind_counts(replay_adjudications),
        "replay_adjudication_bucket_counts": _uk_replay_adjudication_bucket_counts(
            replay_adjudications
        ),
        "replay_adjudication_owner_phase_counts": (
            _uk_replay_adjudication_owner_phase_counts(replay_adjudications)
        ),
        "replay_adjudication_authorization_status_counts": _record_field_counts(
            replay_adjudication_rows,
            "authorization_status",
        ),
        "replay_adjudication_missing_proof_counts": _record_required_proof_counts(
            replay_adjudication_rows
        ),
        "replay_adjudication_agreement_residual_family_counts": (
            _record_agreement_residual_field_counts(
                replay_adjudication_rows,
                "family",
            )
        ),
        "replay_adjudication_agreement_residual_status_counts": (
            _record_agreement_residual_field_counts(
                replay_adjudication_rows,
                "status",
            )
        ),
        "replay_adjudication_agreement_residual_owner_phase_counts": (
            _record_agreement_residual_field_counts(
                replay_adjudication_rows,
                "owner_phase",
            )
        ),
        "adjudications": replay_adjudication_rows,
        "compile_observation_count": len(compile_observations),
        "compile_observation_rule_counts": _rejection_rule_counts(compile_observations),
        "compile_observation_owner_phase_counts": _uk_diagnostic_owner_phase_counts(
            compile_observations
        ),
        "compile_observation_authorization_status_counts": _record_field_counts(
            compile_observations,
            "authorization_status",
        ),
        "compile_observation_missing_proof_counts": _record_required_proof_counts(
            compile_observations
        ),
        "manual_compile_status_counts": _record_field_counts(
            manual_compile_frontier_rows,
            "manual_compile_status",
        ),
        "manual_compile_rule_counts": _record_field_counts(
            manual_compile_frontier_rows,
            "manual_compile_rule_id",
        ),
        "manual_frontier_work_item_family_counts": _record_nested_field_counts(
            manual_compile_frontier_rows,
            "frontier_work_item",
            "frontier_family",
        ),
        "manual_frontier_work_item_authorization_status_counts": (
            _record_nested_field_counts(
                manual_compile_frontier_rows,
                "frontier_work_item",
                "authorization_status",
            )
        ),
        "compile_observation_lane_counts": {
            "source_parse": len(source_parse_rejection_rows),
            "effect_feed_parse": len(effect_feed_parse_rejection_rows),
            "effect_source_pathology": len(effect_source_pathology_rows),
            "manual_compile_frontier": len(manual_compile_frontier_rows),
            "source_acquisition": len(source_acquisition_rejection_rows),
            "lowering": len(lowering_rejection_rows),
            "authority": len(authority_rejection_rows),
        },
        "compile_rejection_count": len(blocking_compile_rejections),
        "compile_rejection_rule_counts": _rejection_rule_counts(blocking_compile_rejections),
        "compile_rejection_owner_phase_counts": _uk_diagnostic_owner_phase_counts(
            blocking_compile_rejections
        ),
        "compile_rejection_authorization_status_counts": _record_field_counts(
            blocking_compile_rejections,
            "authorization_status",
        ),
        "compile_rejection_missing_proof_counts": _record_required_proof_counts(
            blocking_compile_rejections
        ),
        "blocking_compile_rejection_count": len(blocking_compile_rejections),
        "blocking_compile_rejection_rule_counts": _rejection_rule_counts(
            blocking_compile_rejections
        ),
        "blocking_compile_rejection_owner_phase_counts": _uk_diagnostic_owner_phase_counts(
            blocking_compile_rejections
        ),
        "blocking_compile_rejection_lane_counts": {
            "source_parse": len(blocking_source_parse_rejections),
            "effect_feed_parse": len(blocking_effect_feed_parse_rejections),
            "effect_source_pathology": len(blocking_effect_source_pathology_rejections),
            "manual_compile_frontier": len(blocking_manual_compile_frontier_rejections),
            "source_acquisition": len(blocking_source_acquisition_rejections),
            "lowering": len(blocking_lowering_rejections),
            "authority": len(blocking_authority_rejections),
        },
        "blocking_compile_rejection_rule_counts_by_lane": {
            "source_parse": _rejection_rule_counts(blocking_source_parse_rejections),
            "effect_feed_parse": _rejection_rule_counts(blocking_effect_feed_parse_rejections),
            "effect_source_pathology": _rejection_rule_counts(
                blocking_effect_source_pathology_rejections
            ),
            "manual_compile_frontier": _rejection_rule_counts(
                blocking_manual_compile_frontier_rejections
            ),
            "source_acquisition": _rejection_rule_counts(
                blocking_source_acquisition_rejections
            ),
            "lowering": _rejection_rule_counts(blocking_lowering_rejections),
            "authority": _rejection_rule_counts(blocking_authority_rejections),
        },
        "compile_observations": {
            "source_parse": source_parse_rejection_rows,
            "effect_feed_parse": effect_feed_parse_rejection_rows,
            "effect_source_pathology": effect_source_pathology_rows,
            "manual_compile_frontier": manual_compile_frontier_rows,
            "source_acquisition": source_acquisition_rejection_rows,
            "lowering": lowering_rejection_rows,
            "authority": authority_rejection_rows,
        },
        "compile_rejections": {
            "source_parse": blocking_source_parse_rejections,
            "effect_feed_parse": blocking_effect_feed_parse_rejections,
            "effect_source_pathology": blocking_effect_source_pathology_rejections,
            "manual_compile_frontier": blocking_manual_compile_frontier_rejections,
            "source_acquisition": blocking_source_acquisition_rejections,
            "lowering": blocking_lowering_rejections,
            "authority": blocking_authority_rejections,
        },
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
        },
        "source": {
            "archive": db_path,
            "enacted_url": enacted_url,
            "oracle_url": oracle_url,
            "enacted_missing": (
                enacted_source_status != "available" or "enacted" in source_parse_failed_sides
            ),
            "oracle_missing": (
                oracle_source_status != "available" or "oracle" in source_parse_failed_sides
            ),
            "enacted_source_status": enacted_source_status,
            "oracle_source_status": oracle_source_status,
            "enacted_source_size": enacted_source_size,
            "oracle_source_size": oracle_source_size,
            "enacted_source_sha256": enacted_source_sha256,
            "oracle_source_sha256": oracle_source_sha256,
            "enacted_source_parse_failed": "enacted" in source_parse_failed_sides,
            "oracle_source_parse_failed": "oracle" in source_parse_failed_sides,
            "index": None,
            "commencement": None,
            "index_stale": None,
        },
        "amendment_counts": {
            "total": n_effects,
            "matched": n_effects,
            "applied": None,
            "failed": None,
            "future": None,
            "contingent": None,
            "unknown_effective": None,
        },
        "applied_amendments": [],
        "failed_amendments": [],
        "skipped_amendments": {},
        "oracle": {
            "available": oracle_available,
            "id": None,
            "comparison_class": comparison_class or None,
            "eid_similarity": similarity,
            "core_benchmark": core_benchmark,
            "base_eid_count": base_eid_count,
            "replayed_eid_count": replayed_eid_count,
            "oracle_eid_count": oracle_eid_count,
            "replay_compare_eid_count": replay_compare_eid_count,
            "oracle_compare_eid_count": oracle_compare_eid_count,
            "common_eid_count": common_eid_count,
            "only_in_replayed_count": only_in_replayed_count,
            "only_in_oracle_count": only_in_oracle_count,
            "only_in_replayed_sample": list(only_in_replayed_sample),
            "only_in_oracle_sample": list(only_in_oracle_sample),
        },
        "consistency": {
            "consistent": None,
            "divergence_count": None,
            "mismatch_count": None,
            "ops_missing_count": None,
            "consolidated_missing_count": None,
        },
        "timeline": {
            "mode": timeline_mode,
            "provisions": n_provisions,
            "versions": n_versions,
            "pit_materialized_eids": pit_materialized_eids,
        },
        "divergences": [],
        "replayed_text": replayed_text,
    }
    summary = {
        "statute_id": statute_id,
        "pit_date": pit_date,
        "mode": legacy_payload["mode"],
        "error": error,
        "ops_count": int(n_ops),
        "effect_count": n_effects,
        "adjudications_count": len(replay_adjudications),
        "compile_observation_count": len(compile_observations),
        "compile_rejection_count": len(blocking_compile_rejections),
        "blocking_compile_rejection_count": len(blocking_compile_rejections),
        "replay_adjudication_agreement_residual_family_counts": legacy_payload[
            "replay_adjudication_agreement_residual_family_counts"
        ],
        "replay_adjudication_agreement_residual_status_counts": legacy_payload[
            "replay_adjudication_agreement_residual_status_counts"
        ],
        "replay_adjudication_agreement_residual_owner_phase_counts": legacy_payload[
            "replay_adjudication_agreement_residual_owner_phase_counts"
        ],
        "similarity": similarity,
        "comparison_class": comparison_class or None,
        "oracle_available": oracle_available,
        "timeline_mode": timeline_mode,
        "n_provisions": n_provisions,
        "n_versions": n_versions,
        "pit_materialized_eids": pit_materialized_eids,
        "source_enacted_status": enacted_source_status,
        "source_oracle_status": oracle_source_status,
        "source_enacted_missing": legacy_payload["source"]["enacted_missing"],
        "source_oracle_missing": legacy_payload["source"]["oracle_missing"],
    }
    return EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_replay_report",
        schema="lawvm.uk_replay_report.v1",
        truth_claim="uk_replay_materialization_and_declared_oracle_agreement_report",
        replay_claims=True,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=similarity is not None,
        summary=summary,
        filters={
            "statute_id": statute_id,
            "pit_date": pit_date,
            "enacted_only": enacted_only,
            "db_path": db_path,
            "comparison_class": comparison_class or None,
            "timeline_mode": timeline_mode,
        },
        filtered_summary=summary,
        rows=(),
        rows_truncated=False,
        detail={
            **legacy_payload,
            "safe_default": "use_replay_payload_as_materialization_diagnostics_not_source_truth",
            "forbidden_shortcuts": (
                "oracle_agreement_as_source_truth",
                "compile_observation_as_replay_authorization",
                "manual_frontier_as_executable_claim",
                "metadata_backfill_as_source_only_semantics",
            ),
            "next_promotion_requires": (
                "source_identity",
                "target_identity",
                "payload_identity",
                "temporal_extent_applicability",
                "mutation_boundary_proof",
            ),
        },
    ).to_dict()


def replay_text_from_ir(body: Any, *, irnode_to_text: Any) -> str | None:
    if body is None:
        return None
    return irnode_to_text(body)


def replay_text_from_nodes(nodes: Iterable[Any], *, irnode_to_text: Any) -> str | None:
    parts = [irnode_to_text(node) for node in nodes]
    parts = [part for part in parts if part]
    return "\n".join(parts) if parts else None
