"""Replay-fold normalization and diagnostic projection for Finland replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, cast

from lawvm.core import tree_ops as _tops
from lawvm.core.invariant_profiles import collect_tree_invariant_violations
from lawvm.core.invariant_profiles import core_replay_strict_profile
from lawvm.core.invariant_profiles import project_tree_invariant_dicts
from lawvm.core.invariant_profiles import structural_tree_all_profile
from lawvm.core.phase_result import Finding
from lawvm.core.replay_lints import build_label_sequence_gap_findings, build_text_duplication_findings
from lawvm.finland.apply_ir_ops import (
    _strip_redundant_paragraph_label_prefixes_ir,
    _strip_standalone_subsection_item_prefixes_ir,
)
from lawvm.finland.replay_findings import _emit_structural_dedup_warning
from lawvm.finland.replay_pipeline import build_tree_invariant_finding
from lawvm.finland.replay_tree_normalize import hoist_trailing_wrapup_ir
from lawvm.finland.statute import ReplayState

_FI_REPLAY_FOLD_TREE_PROFILE = structural_tree_all_profile("replay_fold_tree")
_FI_REPLAY_FOLD_INVARIANT_PROFILE = core_replay_strict_profile("replay_fold_tree")


@dataclass(frozen=True, slots=True)
class ReplayFoldProjectionRequest:
    """Inputs for replay-fold normalization and replay-fold diagnostics."""

    state: ReplayState
    parent_id: str
    replay_findings: list[Finding]
    replay_meta_out: Optional[Dict[str, object]]
    replay_print: Callable[[str], None]


def _record_replay_invariant_profile(replay_meta_out: Dict[str, object]) -> None:
    profiles = replay_meta_out.setdefault("replay_invariant_profiles", [])
    rows = cast(list[dict[str, object]], profiles)
    profile_row = _FI_REPLAY_FOLD_INVARIANT_PROFILE.to_dict()
    if profile_row not in rows:
        rows.append(profile_row)


def project_replay_fold(request: ReplayFoldProjectionRequest) -> ReplayState:
    """Normalize replay-fold IR and project invariant/lint diagnostics."""
    if request.replay_meta_out is not None:
        _record_replay_invariant_profile(request.replay_meta_out)

    replay_fold_state = request.state.with_ir(
        _strip_redundant_paragraph_label_prefixes_ir(
            _strip_standalone_subsection_item_prefixes_ir(request.state.ir)
        )
    )
    replay_fold_state = replay_fold_state.with_ir(hoist_trailing_wrapup_ir(replay_fold_state.ir))

    deduped_replay_fold_ir = _tops.dedup_children_by_label(replay_fold_state.ir)
    deduped_replay_fold_ir = _emit_structural_dedup_warning(
        phase="replay_fold",
        before_ir=replay_fold_state.ir,
        after_ir=deduped_replay_fold_ir,
        source_statute=request.parent_id,
        replay_findings=request.replay_findings,
        replay_meta_out=request.replay_meta_out,
    )
    replay_fold_state = replay_fold_state.with_ir(deduped_replay_fold_ir)
    replay_fold_state = replay_fold_state.with_ir(_tops.resort_children(replay_fold_state.ir))

    typed_invariant_violations = collect_tree_invariant_violations(
        replay_fold_state.ir,
        _FI_REPLAY_FOLD_TREE_PROFILE,
    )
    invariant_violations = [violation.message for violation in typed_invariant_violations]
    if request.replay_meta_out is not None and invariant_violations:
        request.replay_meta_out["invariant_violations"] = list(invariant_violations)
        request.replay_meta_out["typed_invariant_violations"] = list(
            project_tree_invariant_dicts(
                typed_invariant_violations,
                _FI_REPLAY_FOLD_TREE_PROFILE,
            )
        )
    if invariant_violations:
        for violation in invariant_violations:
            request.replay_findings.append(
                build_tree_invariant_finding(
                    violation=violation,
                    source_statute="",
                    phase="replay_fold",
                    message="Replay tree invariant violated.",
                )
            )
        seen_tree_invariants = {
            (
                finding.kind,
                str(finding.detail.get("violation") or ""),
                str(finding.detail.get("phase") or ""),
                str(finding.source_statute or ""),
            )
            for finding in request.replay_findings
            if finding.kind == "APPLY.TREE_INVARIANT_VIOLATION"
        }
        for finding in request.replay_findings:
            if finding.kind != "APPLY.TREE_INVARIANT_VIOLATION":
                continue
            violation = str(finding.detail.get("violation") or "")
            phase = str(finding.detail.get("phase") or "")
            request.replay_print(f"WARNING tree invariant: {violation}")
            seen_tree_invariants.add(
                ("APPLY.TREE_INVARIANT_VIOLATION", violation, phase, str(finding.source_statute or ""))
            )

    replay_text_duplication_findings = build_text_duplication_findings(
        replay_fold_state.ir,
        phase="replay_fold",
        source_statute=request.parent_id,
    )
    if request.replay_meta_out is not None and replay_text_duplication_findings:
        request.replay_meta_out["text_duplication_warnings"] = [
            {
                key: value
                for key, value in finding.detail.items()
                if key != "message"
            }
            for finding in replay_text_duplication_findings
        ]
    if replay_text_duplication_findings:
        seen_text_warnings = {
            (
                finding.kind,
                str(finding.detail.get("phase") or ""),
                str(finding.detail.get("kind") or ""),
                str(finding.detail.get("left") or ""),
                str(finding.detail.get("right") or ""),
            )
            for finding in request.replay_findings
            if finding.kind == "text_duplication_warning"
        }
        for finding in replay_text_duplication_findings:
            warning = {
                key: value
                for key, value in finding.detail.items()
                if key != "message"
            }
            request.replay_print(
                f"WARNING text duplication: {warning['kind']} {warning['left']} <-> {warning['right']}"
            )
            key = (
                "text_duplication_warning",
                "replay_fold",
                str(warning.get("kind") or ""),
                str(warning.get("left") or ""),
                str(warning.get("right") or ""),
            )
            if key not in seen_text_warnings:
                request.replay_findings.append(finding)
                seen_text_warnings.add(key)

    replay_label_gap_findings = build_label_sequence_gap_findings(
        replay_fold_state.ir,
        phase="replay_fold",
        source_statute=request.parent_id,
    )
    if request.replay_meta_out is not None and replay_label_gap_findings:
        request.replay_meta_out["label_sequence_gap_warnings"] = [
            {
                key: value
                for key, value in finding.detail.items()
                if key != "message"
            }
            for finding in replay_label_gap_findings
        ]
    if replay_label_gap_findings:
        seen_label_gap_warnings = {
            (
                finding.kind,
                str(finding.detail.get("phase") or ""),
                str(finding.detail.get("kind") or ""),
                str(finding.detail.get("path") or ""),
                str(finding.detail.get("node_kind") or ""),
                str(finding.detail.get("next_label") or ""),
            )
            for finding in request.replay_findings
            if finding.kind == "label_sequence_gap_warning"
        }
        for finding in replay_label_gap_findings:
            warning = {
                key: value
                for key, value in finding.detail.items()
                if key != "message"
            }
            missing = ", ".join(str(item) for item in warning.get("missing_labels", [])[:8])
            request.replay_print(
                f"WARNING label sequence gap: {warning['path']} {warning['node_kind']} missing {missing}"
            )
            key = (
                "label_sequence_gap_warning",
                "replay_fold",
                str(warning.get("kind") or ""),
                str(warning.get("path") or ""),
                str(warning.get("node_kind") or ""),
                str(warning.get("next_label") or ""),
            )
            if key not in seen_label_gap_warnings:
                request.replay_findings.append(finding)
                seen_label_gap_warnings.add(key)

    return replay_fold_state
