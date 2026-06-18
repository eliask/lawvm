"""Post-apply fold normalization for one ``process_muutoslaki`` invocation."""

from __future__ import annotations

from lawvm.core import tree_ops as _tops
from lawvm.core.phase_result import Finding
from lawvm.finland.replay_findings import (
    _emit_structural_dedup_warning,
    _pre_dedup_duplicate_details,
)
from lawvm.finland.statute import ReplayState

FI_PROCESS_POST_APPLY_LABEL_DEDUP_RULE_ID = "fi.process.post_apply_label_dedup"


def normalize_process_apply_fold(
    state: ReplayState,
    *,
    amendment_id: str,
    process_findings: list[Finding],
) -> ReplayState:
    """Apply the replay-fold label dedup backstop before handing state forward.

    Large restructure waves can leave transient same-kind+label siblings during
    one amendment's apply loop. The replay-fold projection already owns the
    global cleanup at statute end; mirroring that bounded backstop here keeps
    per-amendment ``process_muutoslaki`` output aligned with the fold state
    later amendments actually consume.
    """
    if not _pre_dedup_duplicate_details(state.ir):
        return state

    deduped_ir = _tops.dedup_children_by_label(state.ir)
    deduped_ir = _tops.resort_children(deduped_ir)
    deduped_ir = _emit_structural_dedup_warning(
        phase="process_muutoslaki.post_apply",
        before_ir=state.ir,
        after_ir=deduped_ir,
        source_statute=amendment_id,
        replay_findings=process_findings,
        replay_meta_out=None,
    )
    return state.with_ir(deduped_ir)
