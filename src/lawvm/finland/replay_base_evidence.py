"""Seed replay-run evidence streams from base-statute witnesses."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from lawvm.finland.replay_findings import xml_ingest_observation_finding
from lawvm.finland.replay_pipeline import ReplaySignalBuffers
from lawvm.finland.source_normalize import source_normalization_fact_finding_kind


@dataclass(frozen=True, slots=True)
class ReplayBaseEvidenceSeedRequest:
    """Immutable inputs for base evidence signal seeding."""

    parent_id: str
    ctx: object


def seed_replay_base_evidence_signals(
    request: ReplayBaseEvidenceSeedRequest,
    *,
    signals: ReplaySignalBuffers,
) -> None:
    """Append base-statute observations to replay evidence buffers."""
    parent_id = request.parent_id
    ctx = request.ctx

    # Project the base statute's witnessed XML->IR ingest observations into the
    # production findings ledger. StatuteContext.from_xml threads an _IngestSink
    # through the real FI parse and folds the witnesses into
    # ingest_metadata["xml_ingest_observations"], but that channel is only read
    # by tests -- so a dropped/positional/repaired source child reached no
    # certificate or findings surface. Append each as a governed
    # SCAN.XML_INGEST_* observation finding so the ingest-boundary event reaches
    # the same ledger (signals.findings -> ReplayResult.findings -> certificate /
    # oracle_check consumers) every other base-statute witness flows through.
    ingest_metadata = getattr(ctx, "ingest_metadata", None) or {}
    raw_ingest_observations = ingest_metadata.get("xml_ingest_observations", ()) or ()
    for obs in raw_ingest_observations:
        if not isinstance(obs, dict):
            continue
        finding = xml_ingest_observation_finding(
            source_statute=parent_id,
            obs_dict=obs,
        )
        if finding is not None:
            signals.findings.append(finding)

    for base_obs in (getattr(ctx, "base_observations", ()) or ()):
        signals.elaboration_observations.append({
            "kind": str(base_obs.kind or ""),
            "stage": str(base_obs.stage or ""),
            "source_statute": parent_id,
            "target_unit_kind": "statute",
            "target_norm": parent_id,
            "target_chapter": "",
            "detail": dict(base_obs.detail or {}),
        })

    source_normalization_facts = cast(
        tuple[Any, ...],
        getattr(ctx, "source_normalization_facts", ()) or (),
    )
    for norm_fact in source_normalization_facts:
        finding_kind = source_normalization_fact_finding_kind(str(norm_fact.kind_value or ""))
        if finding_kind is None:
            continue
        signals.elaboration_observations.append({
            "kind": finding_kind,
            "stage": "source_normalize",
            "source_statute": parent_id,
            "target_unit_kind": "statute",
            "target_norm": parent_id,
            "target_chapter": "",
            "detail": _source_normalization_fact_detail(norm_fact),
        })


def _source_normalization_fact_detail(norm_fact: Any) -> dict[str, object]:
    return {
        "path": list(norm_fact.path),
        "before": norm_fact.before,
        "after": norm_fact.after,
        "basis": norm_fact.basis_value,
        "confidence": norm_fact.confidence,
        "explanation": norm_fact.explanation,
    }
