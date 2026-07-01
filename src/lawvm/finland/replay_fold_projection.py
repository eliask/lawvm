"""Replay-fold normalization and diagnostic projection for Finland replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional

from lawvm.core import tree_ops as _tops
from lawvm.core.invariant_profiles import collect_tree_invariant_violations
from lawvm.core.invariant_profiles import project_tree_invariant_dicts
from lawvm.core.invariant_surface_matrix import (
    FI_REPLAY_FOLD_SURFACE,
    project_replay_warning_findings,
    record_replay_profile,
)
from lawvm.core.phase_result import Finding
from lawvm.finland.apply_ir_ops import (
    _strip_redundant_paragraph_label_prefixes_ir,
    _strip_standalone_subsection_item_prefixes_ir,
)
from lawvm.finland.definition_introducer import fi_definition_list_introducer_predicate
from lawvm.finland.replay_findings import _emit_structural_dedup_warning
from lawvm.finland.replay_pipeline import build_tree_invariant_finding
from lawvm.finland.replay_tree_normalize import hoist_trailing_wrapup_ir
from lawvm.finland.statute import ReplayState
from lawvm.finland.tree_invariant_allowances import (
    is_base_authored_final_provisions_section_violation,
    is_terminal_fi_commencement_section_violation,
)

_FI_REPLAY_FOLD_TREE_PROFILE = FI_REPLAY_FOLD_SURFACE.tree_profile
_FI_REPLAY_FOLD_INVARIANT_PROFILE = FI_REPLAY_FOLD_SURFACE.replay_profile


@dataclass(frozen=True, slots=True)
class ReplayFoldProjectionRequest:
    """Inputs for replay-fold normalization and replay-fold diagnostics."""

    state: ReplayState
    parent_id: str
    replay_findings: list[Finding]
    replay_meta_out: Optional[Dict[str, object]]
    replay_print: Callable[[str], None]
    # Normalized labels of base-authored final-provisions sections (from
    # ``base_final_provisions_section_labels`` over the base IR).  Used only to
    # suppress benign mixed_hierarchy diagnostics for base-authored bare blocks;
    # does not participate in any tree normalization above.
    base_final_provisions_labels: frozenset[str] = frozenset()


def project_replay_fold(request: ReplayFoldProjectionRequest) -> ReplayState:
    """Normalize replay-fold IR and project invariant/lint diagnostics."""
    if request.replay_meta_out is not None:
        record_replay_profile(request.replay_meta_out, FI_REPLAY_FOLD_SURFACE)

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

    project_replay_warning_findings(
        tree=replay_fold_state.ir,
        phase="replay_fold",
        source_statute=request.parent_id,
        warnings=_FI_REPLAY_FOLD_INVARIANT_PROFILE.warnings,
        replay_findings=request.replay_findings,
        replay_meta_out=request.replay_meta_out,
        replay_print=request.replay_print,
        definition_introducer_predicate=fi_definition_list_introducer_predicate,
    )

    replay_fold_state = replay_fold_state.with_ir(_tops.resort_children(replay_fold_state.ir))

    typed_invariant_violations = collect_tree_invariant_violations(
        replay_fold_state.ir,
        _FI_REPLAY_FOLD_TREE_PROFILE,
    )
    typed_invariant_violations = tuple(
        violation
        for violation in typed_invariant_violations
        if not is_terminal_fi_commencement_section_violation(replay_fold_state.ir, violation)
        and not is_base_authored_final_provisions_section_violation(
            violation, request.base_final_provisions_labels
        )
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

    return replay_fold_state
