"""Failed-operation governance for ``process_muutoslaki``.

These checks do not change replay semantics. They move late apply-fold failures
behind stronger same-phase evidence when another surface already owns the
condition, preserving the signal as a non-blocking finding instead of exporting
it as an unresolved failed operation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from lawvm.core.compile_result import SourcePathology
from lawvm.core.recovery_kind import RecoveryKind
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import FailedOp, ResolvedOp
from lawvm.finland.statute import ReplayState


@dataclass(frozen=True, slots=True)
class _ParentSnapshotGovernedFailure:
    failed: FailedOp
    snapshot: _LegalOperation
    subsection: str
    item: str


@dataclass(frozen=True, slots=True)
class _SameWaveMigrationGovernedFailure:
    failed: FailedOp
    source_address: LegalAddress
    migrated_address: LegalAddress


@dataclass(frozen=True, slots=True)
class _RestructureDeferredTargetGovernedFailure:
    failed: FailedOp
    deferred_path: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _TimelineSnapshotGovernedFailure:
    failed: FailedOp
    snapshot: _LegalOperation


@dataclass(frozen=True, slots=True)
class _OccupancyPolicyGovernedFinding:
    finding: Finding
    resolved_op: ResolvedOp
    snapshot: _LegalOperation


RecordProcessFinding = Callable[..., Finding]


@dataclass(slots=True)
class ProcessFailedOpGovernance:
    amendment_id: str
    johto: str
    failed_ops: list[FailedOp]
    process_findings: list[Finding]
    source_pathologies: list[SourcePathology]
    lo_ops: Sequence[_LegalOperation]
    resolved_ops: Sequence[ResolvedOp]
    migration_ledger: MigrationLedger
    migration_ledger_initial_len: int
    record_finding: RecordProcessFinding

    def govern_all(self, output_state: ReplayState) -> None:
        self.govern_by_recodification_source_chain_gap()
        self.govern_by_same_wave_migration(output_state)
        self.govern_by_restructure_deferred_target()
        self.govern_by_timeline_snapshots()
        self.govern_item_failures_by_parent_subsection_snapshots()
        self.govern_repealed_section_insert_occupancy_by_timeline_snapshots()

    def govern_by_recodification_source_chain_gap(self) -> None:
        """Move apply failures already owned by recodification source gaps out of failed_ops."""
        if not self.failed_ops:
            return

        governed_targets: set[str] = set()
        governed_parts: set[str] = set()
        governed_chapters: set[str] = set()
        pathology_details: list[dict[str, object]] = [
            dict(pathology.as_detail())
            for pathology in self.source_pathologies
            if pathology.source_statute == self.amendment_id
        ]
        for finding in self.process_findings:
            if finding.kind != "ELAB.SOURCE_PATHOLOGY" or str(finding.source_statute or "") != self.amendment_id:
                continue
            pathology_details.append(dict(finding.detail))
        for detail in pathology_details:
            if detail.get("code") != "RECODIFICATION_SOURCE_CHAIN_GAP":
                continue
            target_label = str(detail.get("target_label") or "").strip()
            if target_label:
                governed_targets.add(target_label)
                normalized_target = _norm_num_token(target_label)
                if normalized_target.endswith("osa"):
                    governed_parts.add(normalized_target.removesuffix("osa"))
                if normalized_target.endswith("luku"):
                    governed_chapters.add(normalized_target.removesuffix("luku"))
        if not governed_targets and not governed_parts and not governed_chapters:
            return

        kept: list[FailedOp] = []
        governed: list[FailedOp] = []
        for failed in self.failed_ops:
            if failed.reason_code != "section_not_found":
                kept.append(failed)
                continue
            target_label = (
                f"{failed.target_chapter} luku {failed.target_section} §".strip()
                if failed.target_chapter
                else f"{failed.target_section} §"
            )
            if target_label in governed_targets:
                governed.append(failed)
            elif failed.target_part and _norm_num_token(failed.target_part) in governed_parts:
                governed.append(failed)
            elif (
                not failed.target_part
                and failed.target_chapter
                and _norm_num_token(failed.target_chapter) in governed_chapters
            ):
                governed.append(failed)
            else:
                kept.append(failed)

        if not governed:
            return
        self.failed_ops[:] = kept
        for failed in governed:
            self.record_finding(
                kind="APPLY.FAILED_OPERATION_GOVERNED_BY_SOURCE_CHAIN_GAP",
                message=(
                    "Apply failure is governed by a recodification source-chain gap "
                    "for the same target."
                ),
                source_statute=self.amendment_id,
                detail={
                    "failed_description": failed.description,
                    "target_unit_kind": failed.target_unit_kind,
                    "target_part": failed.target_part,
                    "target_chapter": failed.target_chapter,
                    "target_section": failed.target_section,
                    "failed_reason_code": failed.reason_code,
                    "source_pathology_code": "RECODIFICATION_SOURCE_CHAIN_GAP",
                },
                role="observation",
                blocking=False,
            )

    def govern_by_same_wave_migration(self, output_state: ReplayState) -> None:
        """Move transient old-frame failures behind exact same-wave lineage evidence."""
        if not self.failed_ops or len(self.migration_ledger) <= self.migration_ledger_initial_len:
            return

        kept: list[FailedOp] = []
        governed: list[_SameWaveMigrationGovernedFailure] = []
        for failed in self.failed_ops:
            if failed.reason_code != "section_not_found" or not failed.target_section:
                kept.append(failed)
                continue
            source_path: list[tuple[str, str]] = []
            if failed.target_part:
                source_path.append(("part", failed.target_part))
            if failed.target_chapter:
                source_path.append(("chapter", failed.target_chapter))
            source_path.append(("section", failed.target_section))
            source_address = LegalAddress(path=tuple(source_path))
            migrated = self.migration_ledger.current_address_with_prefix_migrations(source_address)
            if migrated == source_address:
                kept.append(failed)
                continue
            migrated_labels = {kind: label for kind, label in migrated.path}
            migrated_section = migrated_labels.get("section")
            if not migrated_section:
                kept.append(failed)
                continue
            migrated_path = output_state.find_section_path(
                migrated_section,
                migrated_labels.get("chapter"),
                migrated_labels.get("part"),
            )
            if migrated_path is None:
                kept.append(failed)
                continue
            governed.append(
                _SameWaveMigrationGovernedFailure(
                    failed=failed,
                    source_address=source_address,
                    migrated_address=migrated,
                )
            )

        if not governed:
            return
        self.failed_ops[:] = kept
        for governed_failure in governed:
            failed = governed_failure.failed
            self.record_finding(
                kind="APPLY.FAILED_OPERATION_GOVERNED_BY_SAME_WAVE_MIGRATION",
                message=(
                    "Apply failure is governed by an exact same-wave migration "
                    "from the old target frame to a live final target."
                ),
                source_statute=self.amendment_id,
                detail={
                    "failed_description": failed.description,
                    "target_unit_kind": failed.target_unit_kind,
                    "target_part": failed.target_part,
                    "target_chapter": failed.target_chapter,
                    "target_section": failed.target_section,
                    "failed_reason_code": failed.reason_code,
                    "source_address": str(governed_failure.source_address),
                    "migrated_address": str(governed_failure.migrated_address),
                },
                role="observation",
                blocking=False,
            )

    def govern_by_restructure_deferred_target(self) -> None:
        """Move old-frame misses behind exact deferred-restructure target findings."""
        if not self.failed_ops:
            return

        deferred_targets: set[tuple[tuple[str, str], ...]] = set()
        for finding in self.process_findings:
            if finding.kind != "APPLY.RESTRUCTURE_PLAN_OP_DEFERRED":
                continue
            detail = finding.detail
            if detail.get("reason_code") != "non_executable_deferred_to_leaf_replay":
                continue
            raw_target = str(detail.get("target") or "").strip()
            if not raw_target:
                continue
            target_path: list[tuple[str, str]] = []
            for part in raw_target.split("/"):
                if ":" not in part:
                    continue
                kind, label = part.split(":", 1)
                if kind and label:
                    target_path.append((kind, label))
            if target_path:
                deferred_targets.add(tuple(target_path))

        if not deferred_targets:
            return

        kept: list[FailedOp] = []
        governed: list[_RestructureDeferredTargetGovernedFailure] = []
        for failed in self.failed_ops:
            if failed.reason_code != "section_not_found" or not failed.target_section:
                kept.append(failed)
                continue
            failed_path: list[tuple[str, str]] = []
            if failed.target_part:
                failed_path.append(("part", failed.target_part))
            if failed.target_chapter:
                failed_path.append(("chapter", failed.target_chapter))
            failed_path.append(("section", failed.target_section))
            failed_path_tuple = tuple(failed_path)
            if failed_path_tuple not in deferred_targets:
                kept.append(failed)
                continue
            governed.append(
                _RestructureDeferredTargetGovernedFailure(
                    failed=failed,
                    deferred_path=failed_path_tuple,
                )
            )

        if not governed:
            return
        self.failed_ops[:] = kept
        for governed_failure in governed:
            failed = governed_failure.failed
            self.record_finding(
                kind="APPLY.FAILED_OPERATION_GOVERNED_BY_RESTRUCTURE_DEFERRED_TARGET",
                message=(
                    "Apply failure is governed by an exact restructure-plan deferred target "
                    "for the same old-frame address."
                ),
                source_statute=self.amendment_id,
                detail={
                    "failed_description": failed.description,
                    "target_unit_kind": failed.target_unit_kind,
                    "target_part": failed.target_part,
                    "target_chapter": failed.target_chapter,
                    "target_section": failed.target_section,
                    "failed_reason_code": failed.reason_code,
                    "deferred_target": "/".join(
                        f"{kind}:{label}" for kind, label in governed_failure.deferred_path
                    ),
                    "governance_basis": "exact_restructure_plan_deferred_target",
                },
                role="observation",
                blocking=False,
            )

    def govern_by_timeline_snapshots(self) -> None:
        """Move apply-fold failures behind exact same-source timeline snapshots."""
        if not self.failed_ops or not self.lo_ops:
            return

        def _snapshot_matches_failed(lo: _LegalOperation, failed: FailedOp) -> bool:
            if not lo.op_id.startswith("snapshot_section_"):
                return False
            if lo.action is not StructuralAction.REPLACE:
                return False
            if lo.payload is None or lo.source is None:
                return False
            if lo.source.statute_id != failed.amendment_id:
                return False
            if not lo.target.path or lo.target.path[-1][0] != "section":
                return False
            labels = {kind: label for kind, label in lo.target.path if label}
            if _norm_num_token(labels.get("section", "")) != _norm_num_token(failed.target_section or ""):
                return False
            if failed.target_chapter and labels.get("chapter") != failed.target_chapter:
                return False
            if failed.target_part and labels.get("part") != failed.target_part:
                return False
            return True

        kept: list[FailedOp] = []
        governed: list[_TimelineSnapshotGovernedFailure] = []
        for failed in self.failed_ops:
            if failed.reason_code != "section_not_found" or failed.target_unit_kind != "section":
                kept.append(failed)
                continue
            snapshot = next(
                (lo for lo in self.lo_ops if _snapshot_matches_failed(lo, failed)),
                None,
            )
            if snapshot is None:
                kept.append(failed)
                continue
            governed.append(_TimelineSnapshotGovernedFailure(failed=failed, snapshot=snapshot))

        if not governed:
            return
        self.failed_ops[:] = kept
        for governed_failure in governed:
            failed = governed_failure.failed
            snapshot = governed_failure.snapshot
            self.record_finding(
                kind="APPLY.FAILED_OPERATION_GOVERNED_BY_TIMELINE_SNAPSHOT",
                message=(
                    "Apply-fold failure is governed by an exact same-source "
                    "timeline snapshot for the target."
                ),
                source_statute=self.amendment_id,
                detail={
                    "failed_description": failed.description,
                    "target_unit_kind": failed.target_unit_kind,
                    "target_part": failed.target_part,
                    "target_chapter": failed.target_chapter,
                    "target_section": failed.target_section,
                    "failed_reason_code": failed.reason_code,
                    "snapshot_op_id": snapshot.op_id,
                    "snapshot_target": str(snapshot.target),
                    "snapshot_source_statute": snapshot.source.statute_id if snapshot.source else "",
                },
                role="observation",
                blocking=False,
            )

    def govern_item_failures_by_parent_subsection_snapshots(self) -> None:
        """Move redundant item failures behind same-source subsection snapshots."""
        if not self.failed_ops or not self.lo_ops:
            return

        item_desc_re = re.compile(
            r"^\s*(?:INSERT|REPLACE|REPEAL)\s+"
            r"(?P<section>\d+\s*[a-z]?)\s*§\s+"
            r"(?P<subsection>\d+)\s+mom\s+"
            r"(?P<item>\d+\s*[a-z]?)\s+kohta\b",
            flags=re.I,
        )

        def _payload_has_item(node: IRNode, item_label: str) -> bool:
            wanted = _norm_num_token(item_label)
            stack = [node]
            while stack:
                current = stack.pop()
                if current.kind is IRNodeKind.PARAGRAPH and _norm_num_token(current.label or "") == wanted:
                    return True
                stack.extend(reversed(current.children))
            return False

        def _snapshot_matches_failed(lo: _LegalOperation, failed: FailedOp, subsection: str, item: str) -> bool:
            if not lo.op_id.startswith("snapshot_subsection_"):
                return False
            if lo.action is not StructuralAction.REPLACE:
                return False
            if lo.payload is None or lo.source is None:
                return False
            if lo.source.statute_id != failed.amendment_id:
                return False
            if not lo.target.path or lo.target.path[-1][0] != "subsection":
                return False
            labels = {kind: label for kind, label in lo.target.path if label}
            if _norm_num_token(labels.get("section", "")) != _norm_num_token(failed.target_section or ""):
                return False
            if _norm_num_token(labels.get("subsection", "")) != _norm_num_token(subsection):
                return False
            if failed.target_chapter and labels.get("chapter") != failed.target_chapter:
                return False
            if failed.target_part and labels.get("part") != failed.target_part:
                return False
            return _payload_has_item(lo.payload, item)

        kept: list[FailedOp] = []
        governed: list[_ParentSnapshotGovernedFailure] = []
        for failed in self.failed_ops:
            match = item_desc_re.match(failed.description)
            if match is None:
                kept.append(failed)
                continue
            if _norm_num_token(match.group("section")) != _norm_num_token(failed.target_section or ""):
                kept.append(failed)
                continue
            subsection = match.group("subsection")
            item = match.group("item")
            snapshot = next(
                (lo for lo in self.lo_ops if _snapshot_matches_failed(lo, failed, subsection, item)),
                None,
            )
            if snapshot is None:
                kept.append(failed)
                continue
            governed.append(
                _ParentSnapshotGovernedFailure(
                    failed=failed,
                    snapshot=snapshot,
                    subsection=subsection,
                    item=item,
                )
            )

        if not governed:
            return
        self.failed_ops[:] = kept
        for governed_failure in governed:
            failed = governed_failure.failed
            snapshot = governed_failure.snapshot
            subsection = governed_failure.subsection
            item = governed_failure.item
            self.record_finding(
                kind="APPLY.FAILED_OPERATION_GOVERNED_BY_PARENT_SNAPSHOT",
                message=(
                    "Descendant item apply failure is governed by a same-source "
                    "subsection snapshot whose payload contains the item."
                ),
                source_statute=self.amendment_id,
                detail={
                    "failed_description": failed.description,
                    "target_unit_kind": failed.target_unit_kind,
                    "target_part": failed.target_part,
                    "target_chapter": failed.target_chapter,
                    "target_section": failed.target_section,
                    "target_subsection": subsection,
                    "target_item": item,
                    "failed_reason_code": failed.reason_code,
                    "governance_basis": "same_source_subsection_snapshot_payload_contains_item",
                    "snapshot_op_id": snapshot.op_id,
                    "snapshot_target": str(snapshot.target),
                    "snapshot_source_statute": snapshot.source.statute_id if snapshot.source else "",
                },
                role="observation",
                blocking=False,
            )

    def govern_repealed_section_insert_occupancy_by_timeline_snapshots(self) -> None:
        """Move stale insert-occupancy notes behind repealed-section snapshots."""
        if not self.process_findings or not self.lo_ops:
            return
        johto_text = self.johto.lower()
        if "kumotun" not in johto_text or "tilalle uusi" not in johto_text:
            return

        pathology_details: list[dict[str, object]] = [
            dict(pathology.as_detail())
            for pathology in self.source_pathologies
            if pathology.source_statute == self.amendment_id
        ]
        for finding in self.process_findings:
            if finding.kind != "ELAB.SOURCE_PATHOLOGY" or str(finding.source_statute or "") != self.amendment_id:
                continue
            pathology_details.append(dict(finding.detail))

        def _has_shape_loss_pathology(section_label: str) -> bool:
            for detail in pathology_details:
                if detail.get("code") != "DESTRUCTIVE_SHAPE_LOSS_RISK":
                    continue
                if detail.get("recovery_kind") != RecoveryKind.SECTION_INSERT_CHAPTER_MERGE_ABSORB:
                    continue
                if detail.get("target_unit_kind") != "section":
                    continue
                target_label = _norm_num_token(
                    re.sub(r"\s*§.*$", "", str(detail.get("target_label") or "")).strip()
                )
                if target_label == section_label:
                    return True
            return False

        def _resolved_op_for_finding(finding: Finding) -> ResolvedOp | None:
            detail = finding.detail
            op_id = str(detail.get("op_id") or "")
            if not op_id:
                return None
            for rop in self.resolved_ops:
                if rop.op_id == op_id:
                    return rop
            return None

        def _snapshot_matches_rop(lo: _LegalOperation, rop: ResolvedOp) -> bool:
            if not lo.op_id.startswith("snapshot_section_"):
                return False
            if lo.action is not StructuralAction.REPLACE:
                return False
            if lo.payload is None or lo.source is None:
                return False
            if lo.source.statute_id != self.amendment_id:
                return False
            if rop.target_unit_kind != "section":
                return False
            if not lo.target.path or lo.target.path[-1][0] != "section":
                return False
            labels = {kind: label for kind, label in lo.target.path if label}
            if _norm_num_token(labels.get("section", "")) != _norm_num_token(rop.resolved_target_label):
                return False
            rop_chapter = _norm_num_token(rop.resolved_target_scope_chapter_label or "")
            if rop_chapter and _norm_num_token(labels.get("chapter", "")) != rop_chapter:
                return False
            rop_part = _norm_num_token(rop.resolved_target_scope_part_label or "")
            if rop_part and _norm_num_token(labels.get("part", "")) != rop_part:
                return False
            return True

        kept: list[Finding] = []
        governed: list[_OccupancyPolicyGovernedFinding] = []
        for finding in self.process_findings:
            if finding.kind != "APPLY.OCCUPANCY_POLICY_VIOLATION":
                kept.append(finding)
                continue
            if str(finding.source_statute or "") != self.amendment_id:
                kept.append(finding)
                continue
            detail = finding.detail
            if detail.get("legacy_action") != "INSERT":
                kept.append(finding)
                continue
            if detail.get("current_occupancy") != "substantive":
                kept.append(finding)
                continue
            rop = _resolved_op_for_finding(finding)
            if rop is None:
                kept.append(finding)
                continue
            section_label = _norm_num_token(rop.resolved_target_label)
            if not _has_shape_loss_pathology(section_label):
                kept.append(finding)
                continue
            snapshot = next(
                (lo for lo in self.lo_ops if _snapshot_matches_rop(lo, rop)),
                None,
            )
            if snapshot is None:
                kept.append(finding)
                continue
            governed.append(
                _OccupancyPolicyGovernedFinding(
                    finding=finding,
                    resolved_op=rop,
                    snapshot=snapshot,
                )
            )

        if not governed:
            return
        self.process_findings[:] = kept
        for governed_finding in governed:
            finding = governed_finding.finding
            rop = governed_finding.resolved_op
            snapshot = governed_finding.snapshot
            self.record_finding(
                kind="APPLY.OCCUPANCY_POLICY_GOVERNED_BY_TIMELINE_SNAPSHOT",
                message=(
                    "Insert occupancy violation is governed by repealed-section "
                    "source text and an exact same-source timeline snapshot."
                ),
                source_statute=self.amendment_id,
                detail={
                    "governed_kind": finding.kind,
                    "ctx_label": str(finding.detail.get("ctx_label") or ""),
                    "op_id": str(finding.detail.get("op_id") or ""),
                    "legacy_action": str(finding.detail.get("legacy_action") or ""),
                    "current_occupancy": str(finding.detail.get("current_occupancy") or ""),
                    "target_part": rop.resolved_target_scope_part_label or "",
                    "target_chapter": rop.resolved_target_scope_chapter_label or "",
                    "target_section": rop.resolved_target_label,
                    "source_phrase_family": "kumotun_tilalle_uusi_section",
                    "source_pathology_code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                    "source_pathology_recovery_kind": "section_insert_chapter_merge_absorb",
                    "snapshot_op_id": snapshot.op_id,
                    "snapshot_target": str(snapshot.target),
                    "snapshot_source_statute": snapshot.source.statute_id if snapshot.source else "",
                },
                role="observation",
                blocking=False,
            )
