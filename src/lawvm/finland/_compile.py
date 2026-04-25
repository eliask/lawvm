"""Finland compile dossier assembly.

Preferred public APIs:
- ``compile_fi_facade(...)`` / ``compile_fi_facade_from_replay(...)``

The only grafter dependency is ``replay_xml``, imported lazily to avoid a
circular import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Sequence, Set, cast

from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.compile_result import (
    CanonicalBundle,
    CompileFailure,
    SourcePathology,
    StrictProfile,
    CompileVerdict,
    compute_verdict_from_registry,
    _profile_allows,
    strict_fail_reasons_from_finding_ledger,
    _compiled_op_scope_witness,
)
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.core.phase_result import Finding
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.target_scope import NeutralTargetUnitKind, resolve_internal_target_scope
from lawvm.finland.strict_profile import default_finland_strict_profile
from lawvm.finland.source_adjudication import build_source_adjudication

if TYPE_CHECKING:
    from lawvm.finland.ops import FailedOp
    from lawvm.finland.statute import ReplayResult

__all__ = [
    "compile_fi_facade",
    "compile_fi_facade_from_replay",
]


_FI_JOHTO_GROUP_PREFIX = "finland-johto:"


@dataclass(frozen=True)
class _ReplayCompileArtifacts:
    compiled_ops: tuple[dict[str, object], ...]
    canonical_ops: tuple[_LegalOperation, ...]
    compile_failures: tuple[CompileFailure, ...]
    findings: tuple[Finding, ...]
    source_adjudication: SourceAdjudication | None
    verdict: CompileVerdict | None
    replay_meta: dict[str, object]


@dataclass(frozen=True)
class _CompiledOpTargetScope:
    target_unit_kind: NeutralTargetUnitKind
    target_norm: str
    target_chapter: str
    target_part: str
    target_paragraph: str
    target_item: str
    target_special: str

    @classmethod
    def from_row(cls, row: dict[str, object]) -> "_CompiledOpTargetScope":
        scope = resolve_internal_target_scope(row)
        target_unit_kind = scope.target_unit_kind
        target_norm = scope.target_norm
        target_chapter = scope.target_chapter
        target_paragraph = str(row.get("target_paragraph") or "")
        target_item = str(row.get("target_item") or "")
        target_special = str(row.get("target_special") or "")

        return cls(
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=str(row.get("target_part") or ""),
            target_paragraph=target_paragraph,
            target_item=target_item,
            target_special=target_special,
        )


@dataclass(frozen=True)
class _ReplayObservationScope:
    target_unit_kind: str
    target_norm: str
    target_chapter: str

    @classmethod
    def from_row(cls, row: dict[str, object]) -> "_ReplayObservationScope":
        return cls(
            target_unit_kind=str(row.get("target_unit_kind") or ""),
            target_norm=str(row.get("target_norm") or ""),
            target_chapter=str(row.get("target_chapter") or ""),
        )


@dataclass(frozen=True)
class _ReplayElaborationObservation:
    kind: str
    stage: str
    source_statute: str
    scope: _ReplayObservationScope
    detail: dict[str, object]

    @classmethod
    def from_row(cls, row: dict[str, object]) -> "_ReplayElaborationObservation":
        return cls(
            kind=str(row.get("kind") or ""),
            stage=str(row.get("stage") or ""),
            source_statute=str(row.get("source_statute") or ""),
            scope=_ReplayObservationScope.from_row(row),
            detail=cast(dict[str, object], row.get("detail")) if isinstance(row.get("detail"), dict) else {},
        )

    @property
    def reasons(self) -> list[object]:
        raw_reasons = self.detail.get("reasons")
        if isinstance(raw_reasons, list):
            return cast(list[object], raw_reasons)
        return []

    @property
    def payload_completeness_kind(self) -> str:
        return str(self.detail.get("payload_completeness_kind") or "")

    @property
    def tail_policy(self) -> str:
        return str(self.detail.get("tail_policy") or "")


@dataclass(frozen=True)
class _ReplaySparsePayloadLeftover:
    source_statute: str
    scope: _ReplayObservationScope
    unassigned_slots: tuple[str, ...]

    @classmethod
    def from_row(cls, row: dict[str, object]) -> "_ReplaySparsePayloadLeftover":
        raw_slots = row.get("unassigned_slots")
        return cls(
            source_statute=str(row.get("source_statute") or ""),
            scope=_ReplayObservationScope.from_row(row),
            unassigned_slots=tuple(
                str(slot or "") for slot in (raw_slots if isinstance(raw_slots, list) else []) if str(slot or "")
            ),
        )


@dataclass(frozen=True)
class _ReplaySparseSlotBinding:
    source_statute: str
    scope: _ReplayObservationScope
    op_description: str
    op_type: str
    target_paragraph: object
    target_item: str
    target_special: str
    payload_slot_index: int
    payload_slot_label: str

    @classmethod
    def from_row(cls, row: dict[str, object]) -> "_ReplaySparseSlotBinding":
        slot_raw = row.get("payload_slot_index")
        return cls(
            source_statute=str(row.get("source_statute") or ""),
            scope=_ReplayObservationScope.from_row(row),
            op_description=str(row.get("op_description") or ""),
            op_type=str(row.get("op_type") or ""),
            target_paragraph=row.get("target_paragraph"),
            target_item=str(row.get("target_item") or ""),
            target_special=str(row.get("target_special") or ""),
            payload_slot_index=int(slot_raw) if isinstance(slot_raw, int) else 0,
            payload_slot_label=str(row.get("payload_slot_label") or ""),
        )


def _failed_op_to_compile_failure(f: FailedOp) -> CompileFailure:
    return CompileFailure.from_scope(
        source_statute=f.amendment_id,
        description=f.description,
        reason=f.reason,
        target_section=f.target_section,
        target_chapter=f.target_chapter or "",
        target_unit_kind=f.target_unit_kind or "",
        reason_code=f.reason_code,
    )


def _maybe_with_target_unit_kind(
    detail: dict[str, object],
    target_unit_kind: NeutralTargetUnitKind | str | object,
) -> dict[str, object]:
    if target_unit_kind:
        detail["target_unit_kind"] = str(target_unit_kind)
    return detail


def _detail_with_target_scope(
    detail: dict[str, object],
    *,
    target_unit_kind: NeutralTargetUnitKind | str | object,
) -> dict[str, object]:
    return _maybe_with_target_unit_kind(detail, target_unit_kind)


def _detail_with_internal_replay_scope(
    detail: dict[str, object],
    *,
    scope: _ReplayObservationScope,
) -> dict[str, object]:
    return _detail_with_target_scope(
        detail,
        target_unit_kind=scope.target_unit_kind,
    )


def _effective_source_adjudication(
    *,
    parent_id: str,
    replay_mode: Literal["finlex_oracle", "legal_pit"],
    replay_result: ReplayResult,
    replay_meta: Dict[str, object],
) -> SourceAdjudication | None:
    source_adjudication = replay_result.source_adjudication
    if source_adjudication is not None:
        return source_adjudication

    lineage_raw = replay_meta.get("lineage")
    lineage: tuple[dict[str, object], ...] = ()
    if isinstance(lineage_raw, list):
        lineage = cast(
            tuple[dict[str, object], ...],
            tuple(row for row in lineage_raw if isinstance(row, dict)),
        )
    cutoff_date = str(replay_meta.get("cutoff_date") or "")
    oracle_version_amendment_id = str(replay_meta.get("oracle_version_amendment_id") or "")
    oracle_suspect = str(replay_meta.get("oracle_suspect") or "")
    html_noncommensurable_reason = str(replay_meta.get("html_noncommensurable_reason") or "")
    if not any(
        (
            cutoff_date,
            oracle_version_amendment_id,
            oracle_suspect,
            html_noncommensurable_reason,
            lineage,
        )
    ):
        return None
    return build_source_adjudication(
        parent_id,
        replay_mode,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id,
        oracle_suspect=oracle_suspect,
        html_noncommensurable_reason=html_noncommensurable_reason,
        lineage=lineage,
    )


def _finding_from_compile_signal(
    *,
    kind: str,
    message: str,
    source_statute: str = "",
    detail: Optional[dict[str, object]] = None,
    op_id: str = "",
) -> Finding | None:
    resolved_detail: Dict[str, object] = dict(detail or {})
    resolved_detail.setdefault("message", str(message))
    if op_id:
        resolved_detail.setdefault("op_id", str(op_id))
    if source_statute:
        resolved_detail.setdefault("source_statute", str(source_statute))

    resolved_kind = str(kind or "").strip()
    resolved_source_statute = str(source_statute or "")
    if not resolved_kind:
        return None

    spec = get_finding_spec(resolved_kind)
    if spec is None:
        return Finding(
            kind=resolved_kind,
            role="observation",
            stage="compile",
            detail=resolved_detail,
            source_statute=resolved_source_statute,
            blocking=False,
        )

    if spec.role == "observation":
        return Finding(
            kind=resolved_kind,
            role="observation",
            stage=spec.phase,
            detail=resolved_detail,
            source_statute=resolved_source_statute,
            blocking=False,
        )
    if spec.role == "barrier":
        return Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage=spec.phase,
            detail={
                **resolved_detail,
                "barrier_code": resolved_kind,
            },
            source_statute=resolved_source_statute,
            blocking=True,
        )
    return Finding(
        kind=resolved_kind,
        role="obligation",
        stage=spec.phase,
        detail=resolved_detail,
        source_statute=resolved_source_statute,
        blocking=spec.default_enforcement in ("strict_fail", "hard_fail"),
    )


def _compile_findings(
    *,
    compiled_ops: List[Dict[str, object]],
    canonical_ops: List[_LegalOperation],
    replay_meta: Dict[str, object],
    source_adjudication: SourceAdjudication | None = None,
    existing_findings: Sequence[Finding] = (),
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    seen: set[tuple[str, str, str, str]] = set()
    existing_scope_finding_keys: set[tuple[str, str, str, str, str]] = set()

    for finding in existing_findings:
        kind = str(finding.kind or "")
        if kind not in {
            "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
            "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED",
            "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED",
        }:
            continue
        detail = finding.detail
        existing_scope_finding_keys.add(
            (
                kind,
                str(finding.source_statute or ""),
                str(detail.get("target_unit_kind") or ""),
                str(detail.get("target_norm") or ""),
                str(detail.get("target_chapter") or ""),
            )
        )

    def _record_finding(
        *,
        kind: str,
        message: str,
        source_statute: str = "",
        detail: Optional[dict[str, object]] = None,
        op_id: str = "",
    ) -> None:
        finding = _finding_from_compile_signal(
            kind=kind,
            message=message,
            source_statute=source_statute,
            detail=detail,
            op_id=op_id,
        )
        if finding is None:
            return
        key = (
            str(finding.kind or ""),
            str(finding.source_statute or ""),
            str(finding.detail.get("op_id") or ""),
            repr(finding.detail),
        )
        if key in seen:
            return
        seen.add(key)
        findings.append(finding)

    for op in canonical_ops:
        if op.source and op.source.corrected_by:
            _record_finding(
                kind="APPLY.SOURCE_CORRECTED_BY_PATCH",
                message="A corrigendum patch influenced compilation.",
                source_statute=op.source.statute_id,
                op_id=op.op_id,
                detail={"corrected_by": op.source.corrected_by},
            )

    target_guessing_hint_to_kind = {
        "normalize_item_like_target": "PARSE.TARGET_GUESSING",
    }
    seen_hints: Set[tuple[str, str, NeutralTargetUnitKind | str | object, str, str]] = set()
    for row in compiled_ops:
        source_statute = str(row.get("source_statute") or "")
        target_scope = _CompiledOpTargetScope.from_row(row)
        target_guessing_tags = row.get("target_guessing_provenance_tags")
        if isinstance(target_guessing_tags, list):
            for tag in (str(part).strip() for part in target_guessing_tags if str(part).strip()):
                kind = target_guessing_hint_to_kind.get(tag)
                if kind is None:
                    continue
                key = (kind, tag, target_scope.target_unit_kind, target_scope.target_norm, target_scope.target_chapter)
                if key in seen_hints:
                    continue
                _record_finding(
                    kind=kind,
                    message="Compilation required typed target-guessing provenance.",
                    source_statute=source_statute,
                    detail=_detail_with_target_scope(
                        {
                            "tag": tag,
                            "target_norm": target_scope.target_norm,
                            "target_chapter": target_scope.target_chapter,
                        },
                        target_unit_kind=target_scope.target_unit_kind,
                    ),
                )
                seen_hints.add(key)

        witness = _compiled_op_scope_witness(row)
        if witness is not None:
            existing_scope_key = (
                witness.kind,
                source_statute,
                str(target_scope.target_unit_kind or ""),
                target_scope.target_norm,
                target_scope.target_chapter,
            )
            if existing_scope_key in existing_scope_finding_keys:
                continue
            key = (witness.kind, witness.tag, target_scope.target_unit_kind, target_scope.target_norm, target_scope.target_chapter)
            if key not in seen_hints:
                if witness.kind == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION":
                    message = "Compilation required context-dependent anchor resolution."
                elif witness.kind == "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED":
                    message = "Compilation required explicit source-chunk scope carry."
                else:
                    message = "Compilation rewrote explicit source scope using live-tree fallback."
                detail: dict[str, object] = {
                    "tag": witness.tag,
                    "target_norm": target_scope.target_norm,
                    "target_chapter": target_scope.target_chapter,
                    "scope_source": witness.source,
                    "scope_confidence": witness.confidence,
                }
                if witness.used_legacy_tag_fallback:
                    detail["scope_transport_mode"] = "legacy_scope_tag_fallback"
                _record_finding(
                    kind=witness.kind,
                    message=message,
                    source_statute=source_statute,
                    detail=_detail_with_target_scope(
                        detail,
                        target_unit_kind=target_scope.target_unit_kind,
                    ),
                )
                seen_hints.add(key)

    oracle_suspect = (
        str(source_adjudication.oracle_suspect or "")
        if source_adjudication is not None
        else str(replay_meta.get("oracle_suspect") or "")
    )
    if oracle_suspect:
        _record_finding(
            kind="APPLY.SOURCE_INCOMPLETE",
            message="Oracle/source lineage appears incomplete or suspect.",
            detail={"oracle_suspect": oracle_suspect},
        )

    source_pathologies = replay_meta.get("source_pathologies")
    if isinstance(source_pathologies, list):
        for _p in source_pathologies:
            if not isinstance(_p, dict):
                continue
            # Cast narrows dict[Never, Never] (ty's isinstance narrowing) to dict[str, object].
            p: dict[str, object] = cast(dict[str, object], _p)
            if "target_kind" in p:
                p = dict(p)
                p.pop("target_kind", None)
            pathology = SourcePathology.from_internal_detail(
                source_statute=str(p.get("source_statute") or ""),
                detail=p,
            )
            scope = _ReplayObservationScope(
                target_unit_kind=pathology.target_unit_kind,
                target_norm="",
                target_chapter="",
            )
            _record_finding(
                kind="APPLY.SOURCE_PATHOLOGY_DETECTED",
                message=pathology.message or "Replay encountered a source pathology.",
                source_statute=pathology.source_statute,
                detail=_detail_with_internal_replay_scope(
                    {
                        "code": pathology.code,
                        "target_label": pathology.target_label,
                        "detail": dict(pathology.detail),
                    },
                    scope=scope,
                ),
            )

    elaboration_observations = replay_meta.get("elaboration_observations")
    seen_elaboration: Set[tuple[str, str, str, str, str, str]] = set()
    seen_payload_completeness: Set[tuple[str, str, str, str, str, str]] = set()
    if isinstance(elaboration_observations, list):
        for _obs in elaboration_observations:
            if not isinstance(_obs, dict):
                continue
            # Cast narrows dict[Never, Never] (ty's isinstance narrowing) to dict[str, object].
            obs = _ReplayElaborationObservation.from_row(cast(dict[str, object], _obs))
            obs_kind = obs.kind
            stage = obs.stage
            source_statute = obs.source_statute
            scope = obs.scope
            key = (
                obs_kind,
                stage,
                source_statute,
                scope.target_unit_kind,
                scope.target_norm,
                scope.target_chapter,
            )
            detail = dict(obs.detail)
            reasons = obs.reasons
            if obs_kind == "ELAB.PAYLOAD_COMPLETENESS" and key not in seen_payload_completeness:
                _record_finding(
                    kind="ELAB.PAYLOAD_COMPLETENESS",
                    message="Payload completeness witness emitted before apply.",
                    source_statute=source_statute,
                    detail=_detail_with_internal_replay_scope(
                            {
                                "stage": stage,
                                "target_norm": scope.target_norm,
                                "target_chapter": scope.target_chapter,
                                "payload_completeness_kind": obs.payload_completeness_kind,
                                "reasons": list(reasons),
                                "tail_policy": obs.tail_policy,
                                "detail": detail,
                            },
                            scope=scope,
                        ),
                )
                seen_payload_completeness.add(key)
                # Payload completeness is now a first-class adjudication; do
                # not also duplicate it through the generic frontend wrapper.
                continue
            if key in seen_elaboration:
                continue
            if not obs_kind:
                continue
            _record_finding(
                kind=obs_kind,
                message=f"Frontend elaboration recorded observation: {obs_kind or 'unknown'}",
                source_statute=source_statute,
                detail=_detail_with_internal_replay_scope(
                        {
                            "stage": stage,
                            "observation_kind": obs_kind,
                            "target_norm": scope.target_norm,
                            "target_chapter": scope.target_chapter,
                            "detail": detail,
                        },
                        scope=scope,
                    ),
            )
            seen_elaboration.add(key)

    sparse_leftovers = replay_meta.get("sparse_leftovers")
    seen_sparse_leftovers: Set[tuple[str, str, str, str, tuple[str, ...]]] = set()
    if isinstance(sparse_leftovers, list):
        for _lo in sparse_leftovers:
            if not isinstance(_lo, dict):
                continue
            # Cast narrows dict[Never, Never] (ty's isinstance narrowing) to dict[str, object].
            leftover = _ReplaySparsePayloadLeftover.from_row(cast(dict[str, object], _lo))
            source_statute = leftover.source_statute
            scope = leftover.scope
            slots = leftover.unassigned_slots
            key = (source_statute, scope.target_unit_kind, scope.target_norm, scope.target_chapter, slots)
            if key in seen_sparse_leftovers:
                continue
            _record_finding(
                kind="ELAB.SPARSE_PAYLOAD_LEFTOVER",
                message="Frontend elaboration preserved unassigned sparse payload slots.",
                source_statute=source_statute,
                detail=_detail_with_internal_replay_scope(
                        {
                            "target_norm": scope.target_norm,
                            "target_chapter": scope.target_chapter,
                            "unassigned_slots": list(slots),
                        },
                        scope=scope,
                    ),
            )
            seen_sparse_leftovers.add(key)

    sparse_slot_bindings = replay_meta.get("sparse_slot_bindings")
    seen_sparse_bindings: Set[tuple[str, str, str, str, str, int, str]] = set()
    if isinstance(sparse_slot_bindings, list):
        for _bi in sparse_slot_bindings:
            if not isinstance(_bi, dict):
                continue
            # Cast narrows dict[Never, Never] (ty's isinstance narrowing) to dict[str, object].
            binding = _ReplaySparseSlotBinding.from_row(cast(dict[str, object], _bi))
            source_statute = binding.source_statute
            scope = binding.scope
            op_description = binding.op_description
            slot_index = binding.payload_slot_index
            slot_label = binding.payload_slot_label
            key = (
                source_statute,
                scope.target_unit_kind,
                scope.target_norm,
                scope.target_chapter,
                op_description,
                slot_index,
                slot_label,
            )
            if key in seen_sparse_bindings:
                continue
            _record_finding(
                kind="ELAB.SPARSE_SLOT_BINDING",
                message="Frontend elaboration recorded sparse slot ownership.",
                source_statute=source_statute,
                detail=_detail_with_internal_replay_scope(
                        {
                            "target_norm": scope.target_norm,
                            "target_chapter": scope.target_chapter,
                            "op_description": op_description,
                        "op_type": binding.op_type,
                        "target_paragraph": binding.target_paragraph,
                        "target_item": binding.target_item,
                        "target_special": binding.target_special,
                            "payload_slot_index": slot_index,
                            "payload_slot_label": slot_label,
                        },
                        scope=scope,
                    ),
            )
            seen_sparse_bindings.add(key)

    return tuple(findings)


def _collect_fi_temporal_coverage_findings(
    *,
    canonical_ops: Sequence[_LegalOperation],
    temporal_events: Sequence[object],
    source_statute: str,
    compile_mode: Literal["strict", "quirks"],
) -> tuple[Finding, ...]:
    structural_groups: set[str] = {
        str(op.group_id)
        for op in canonical_ops
        if str(op.group_id or "").startswith(_FI_JOHTO_GROUP_PREFIX)
    }
    temporal_groups: set[str] = set()
    for event in temporal_events:
        event_group_id = getattr(event, "group_id", None)
        if isinstance(event_group_id, str) and event_group_id.strip():
            temporal_groups.add(event_group_id.strip())
    missing_groups = tuple(sorted(structural_groups - temporal_groups))

    if not missing_groups:
        return ()

    if compile_mode == "strict":
        raise RuntimeError(
            "Finland temporal-coverage migration check failed: missing temporal events for finland-johto groups="
            f"{', '.join(missing_groups)} in {source_statute}"
        )

    finding = _finding_from_compile_signal(
        kind="TIME.TRIGGER_COVERAGE_INCOMPLETE",
        message=(
            "Temporal authority is missing for one or more Finland johto-grouped structural operations "
            "and will remain a migration fallback for this compile path."
        ),
        source_statute=source_statute,
        detail={
            "coverage_prefix": _FI_JOHTO_GROUP_PREFIX,
            "missing_group_ids": list(missing_groups),
            "structural_group_count": len(structural_groups),
            "temporal_group_count": len(temporal_groups),
        },
    )
    if finding is None:
        return ()
    return (finding,)


def _compile_artifacts_from_replay(
    *,
    parent_id: str,
    replay_result: ReplayResult,
    replay_mode: Literal["finlex_oracle", "legal_pit"] = "legal_pit",
    compile_mode: Literal["strict", "quirks"] = "strict",
    strict_profile: Optional[StrictProfile] = None,
    compiled_ops: Optional[List[Dict[str, object]]] = None,
    replay_meta: Optional[Dict[str, object]] = None,
    canonical_ops: Optional[Sequence[_LegalOperation]] = None,
    failed_ops: Optional[Sequence[FailedOp]] = None,
    extra_findings: Optional[Sequence[Finding]] = None,
) -> _ReplayCompileArtifacts:
    """Derive Finland compile dossier artifacts from replay-owned state."""
    profile = strict_profile or default_finland_strict_profile()
    compiled_ops = list(compiled_ops or [])
    replay_meta = dict(replay_meta or {})
    canonical_ops = list(canonical_ops or [])
    failed_ops = list(failed_ops or [])
    source_adjudication = _effective_source_adjudication(
        parent_id=parent_id,
        replay_mode=replay_mode,
        replay_result=replay_result,
        replay_meta=replay_meta,
    )
    if extra_findings is None:
        extra_findings = tuple(replay_result.findings)
    extra_findings = list(extra_findings)

    compile_failures = [_failed_op_to_compile_failure(f) for f in failed_ops]

    findings = list(_compile_findings(
        compiled_ops=compiled_ops,
        canonical_ops=canonical_ops,
        replay_meta=replay_meta,
        source_adjudication=source_adjudication,
        existing_findings=tuple(extra_findings),
    ))
    seen_findings: set[tuple[str, str, str, str]] = {
        (
            str(finding.kind or ""),
            str(finding.source_statute or ""),
            str(finding.detail.get("op_id") or ""),
            repr(finding.detail),
        )
        for finding in findings
    }
    for finding in extra_findings:
        key = (
            str(finding.kind or ""),
            str(finding.source_statute or ""),
            str(finding.detail.get("op_id") or ""),
            repr(finding.detail),
        )
        if key in seen_findings:
            continue
        seen_findings.add(key)
        findings.append(finding)
    findings_tuple = tuple(findings)
    temporal_coverage_findings = _collect_fi_temporal_coverage_findings(
        canonical_ops=canonical_ops,
        temporal_events=replay_result.temporal_events,
        source_statute=parent_id,
        compile_mode=compile_mode,
    )
    findings_tuple = tuple((*findings_tuple, *temporal_coverage_findings))

    registry_codes_from_runtime_violations: set[str] = set()
    for finding in findings_tuple:
        if str(finding.kind or "") != "RUNTIME.VIOLATION":
            continue
        barrier_code = str(finding.detail.get("barrier_code") or "").strip()
        if not barrier_code:
            continue
        barrier_spec = get_finding_spec(barrier_code)
        if barrier_spec is not None and barrier_spec.role in ("barrier", "violation", "obligation"):
            registry_codes_from_runtime_violations.add(barrier_code)

    strict_fail_reasons = strict_fail_reasons_from_finding_ledger(
        profile,
        compiled_ops=compiled_ops,
        canonical_ops=canonical_ops,
        failures=compile_failures,
        findings=tuple(findings_tuple),
    )
    if registry_codes_from_runtime_violations:
        gated_barrier_codes = {
            code for code in registry_codes_from_runtime_violations if not _profile_allows(profile, code)
        }
        if gated_barrier_codes:
            strict_fail_reasons = sorted(set(strict_fail_reasons).union(gated_barrier_codes))
    verdict = compute_verdict_from_registry(
        profile,
        strict_fail_reasons,
        has_internal_failure=any(
            (
                str(finding.kind or "") in (
                    "APPLY.TREE_INVARIANT_VIOLATION",
                    "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
                )
            )
            or (
                finding.kind == "RUNTIME.VIOLATION"
                and str(finding.detail.get("barrier_code") or "") in (
                    "APPLY.TREE_INVARIANT_VIOLATION",
                    "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
                )
            )
            for finding in findings_tuple
        ),
    )

    return _ReplayCompileArtifacts(
        compiled_ops=tuple(compiled_ops),
        canonical_ops=tuple(canonical_ops),
        compile_failures=tuple(compile_failures),
        findings=findings_tuple,
        source_adjudication=source_adjudication,
        verdict=verdict,
        replay_meta=replay_meta,
    )

def compile_fi_facade_from_replay(
    *,
    parent_id: str,
    replay_result: ReplayResult,
    replay_mode: Literal["finlex_oracle", "legal_pit"],
    compile_mode: Literal["strict", "quirks"] = "strict",
    strict_profile: Optional[StrictProfile] = None,
    compiled_ops: List[Dict[str, object]],
    replay_meta: Dict[str, object],
    canonical_ops: List[_LegalOperation],
    failed_ops: List[FailedOp],
    extra_findings: Optional[List[Finding]] = None,
):
    """Build Finland's native CompileFacade from an already-executed replay."""
    from lawvm.core.compile_facade import CompileFacade  # noqa: PLC0415
    from lawvm.core.phase_result import PhaseBuilder  # noqa: PLC0415

    profile = strict_profile or default_finland_strict_profile()
    artifacts = _compile_artifacts_from_replay(
        parent_id=parent_id,
        replay_result=replay_result,
        replay_mode=replay_mode,
        compile_mode=compile_mode,
        strict_profile=profile,
        compiled_ops=compiled_ops,
        replay_meta=replay_meta,
        canonical_ops=canonical_ops,
        failed_ops=failed_ops,
        extra_findings=extra_findings,
    )

    builder = PhaseBuilder()
    artifact_findings = tuple(artifacts.findings)
    builder.add_findings(artifact_findings)
    resolved_temporal_events = tuple(replay_result.temporal_events)
    bundle = CanonicalBundle(
        target_statute=parent_id,
        structural_ops=tuple(canonical_ops),
        temporal_events=resolved_temporal_events,
        migration_events=tuple(replay_result.migration_events),
    )
    pr = builder.finish(bundle)
    facade = CompileFacade.from_phase_result(
        pr,
        replay_mode=replay_mode,
        strict_profile_name=profile.name,
        verdict=artifacts.verdict,
    )
    replay_result.compile_facade = facade
    return facade


def compile_fi_facade(
    parent_id: str,
    *,
    replay_mode: Literal["finlex_oracle", "legal_pit"] = "legal_pit",
    compile_mode: Literal["strict", "quirks"] = "strict",
    strict_profile: Optional[StrictProfile] = None,
):
    """Compile one Finnish statute into the newer CompileFacade surface.

    This is the native Finland top-level compile path. It reuses the replay
    execution directly and returns the shared `CompileFacade` without
    reconstructing a second Finland-specific dossier carrier.
    """
    from lawvm.finland.grafter import replay_xml  # noqa: PLC0415

    compiled_ops: List[Dict[str, object]] = []
    replay_meta: Dict[str, object] = {}
    canonical_ops: List[_LegalOperation] = []
    failed_ops_list: List[FailedOp] = []
    master = replay_xml(
        parent_id,
        mode=replay_mode,
        strict_johto_temporal=(compile_mode == "strict"),
        compiled_ops_out=compiled_ops,
        replay_meta_out=replay_meta,
        lo_ops_out=canonical_ops,
        failed_ops_out=failed_ops_list,
        strict_profile=None,
    )

    return compile_fi_facade_from_replay(
        parent_id=parent_id,
        replay_result=master,
        replay_mode=replay_mode,
        compile_mode=compile_mode,
        strict_profile=strict_profile,
        compiled_ops=compiled_ops,
        replay_meta=replay_meta,
        canonical_ops=canonical_ops,
        failed_ops=failed_ops_list,
    )
