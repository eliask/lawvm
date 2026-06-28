from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from lawvm.core.ir import IRNode, IRStatute
from lawvm.uk_legislation.grounding_classification import (
    GROUNDING_CLASSIFICATIONS,
    classify_suppression_mechanism,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor
from lawvm.core.quirks_disposition import QuirksDisposition

UK_ORACLE_ALIGNMENT_RULE_ID = "uk_oracle_eid_alignment_adapter"
_TRANSPARENT_WRAPPER_KINDS = frozenset({"p1group", "pblock", "crossheading"})


@dataclass(frozen=True)
class UKOracleAlignmentChange:
    path: str
    kind: str
    label: str | None
    before_eid: str | None
    after_eid: str | None
    match_method: str | None = None
    match_key: str | None = None
    # Set iff the node was left unmatched (after_eid is None). Exactly one of
    # the four GROUNDING_CLASSIFICATIONS; ``None`` for matched (oracle-assigned)
    # changes.
    grounding_classification: str | None = None
    rule_id: str = UK_ORACLE_ALIGNMENT_RULE_ID


@dataclass(frozen=True)
class UKOracleAlignmentReport:
    enabled: bool
    stage: str
    rule_id: str
    phase: str
    family: str
    input_oracle_key_count: int
    input_oracle_eid_count: int
    before_node_count: int
    after_node_count: int
    node_count_mismatch: bool
    changed_count: int
    cleared_count: int
    oracle_assigned_count: int
    local_fallback_count: int
    local_fallback_suppressed_count: int
    transparent_wrapper_cleared_count: int
    match_method_counts: dict[str, int]
    # Total over unmatched (after_eid=None) changes: each contributes to exactly
    # one of the four GROUNDING_CLASSIFICATIONS buckets. ``unclassified_count``
    # counts unmatched changes carrying no classification — a contract
    # violation that must always be zero.
    grounding_classification_counts: dict[str, int]
    unclassified_count: int
    strict_disposition: str
    quirks_disposition: QuirksDisposition
    changes: tuple[UKOracleAlignmentChange, ...] = ()

    def to_jsonable_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "stage": self.stage,
            "rule_id": self.rule_id,
            "phase": self.phase,
            "family": self.family,
            "input_oracle_key_count": self.input_oracle_key_count,
            "input_oracle_eid_count": self.input_oracle_eid_count,
            "before_node_count": self.before_node_count,
            "after_node_count": self.after_node_count,
            "node_count_mismatch": self.node_count_mismatch,
            "changed_count": self.changed_count,
            "cleared_count": self.cleared_count,
            "oracle_assigned_count": self.oracle_assigned_count,
            "local_fallback_count": self.local_fallback_count,
            "local_fallback_suppressed_count": self.local_fallback_suppressed_count,
            "transparent_wrapper_cleared_count": self.transparent_wrapper_cleared_count,
            "match_method_counts": dict(sorted(self.match_method_counts.items())),
            "grounding_classification_counts": dict(
                sorted(self.grounding_classification_counts.items())
            ),
            "unclassified_count": self.unclassified_count,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
            "changes": [change.__dict__ for change in self.changes],
        }


@dataclass(frozen=True)
class UKOracleAlignmentResult:
    statute: IRStatute
    report: UKOracleAlignmentReport


def _empty_alignment_report(*, enabled: bool, eid_map: Optional[dict[str, str]]) -> UKOracleAlignmentReport:
    oracle_values = set((eid_map or {}).values())
    return UKOracleAlignmentReport(
        enabled=enabled,
        stage="post_replay_adapter" if enabled else "none",
        rule_id=UK_ORACLE_ALIGNMENT_RULE_ID,
        phase="oracle_alignment",
        family="oracle_alignment_adapter",
        input_oracle_key_count=len(eid_map or {}),
        input_oracle_eid_count=len(oracle_values),
        before_node_count=0,
        after_node_count=0,
        node_count_mismatch=False,
        changed_count=0,
        cleared_count=0,
        oracle_assigned_count=0,
        local_fallback_count=0,
        local_fallback_suppressed_count=0,
        transparent_wrapper_cleared_count=0,
        match_method_counts={},
        grounding_classification_counts={},
        unclassified_count=0,
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
    )


def _node_eid(node: IRNode) -> str | None:
    return node.attrs.get("eId") or node.attrs.get("id")


def _walk_nodes(node: IRNode, path: str) -> list[tuple[str, IRNode]]:
    rows = [(path, node)]
    for index, child in enumerate(node.children):
        label = str(child.label or "")
        label_part = f"[{label}]" if label else ""
        rows.extend(_walk_nodes(child, f"{path}/{index}:{child.kind}{label_part}"))
    return rows


def _statute_nodes(statute: IRStatute) -> list[tuple[str, IRNode]]:
    rows = _walk_nodes(statute.body, "body")
    for index, supplement in enumerate(statute.supplements):
        label = str(supplement.label or "")
        label_part = f"[{label}]" if label else ""
        rows.extend(_walk_nodes(supplement, f"supplements/{index}:{supplement.kind}{label_part}"))
    return rows


def _alignment_report(
    before: IRStatute,
    after: IRStatute,
    *,
    eid_map: dict[str, str],
    match_events: tuple[dict[str, Any], ...] = (),
) -> UKOracleAlignmentReport:
    oracle_values = set(eid_map.values())
    before_rows = _statute_nodes(before)
    after_rows = _statute_nodes(after)
    node_count_mismatch = len(before_rows) != len(after_rows)
    event_by_after: dict[str, dict[str, Any]] = {
        str(event["after_eid"]): event
        for event in match_events
        if event.get("after_eid") is not None
    }
    cleared_events: list[dict[str, Any]] = [
        event
        for event in match_events
        if event.get("after_eid") is None
    ]
    used_cleared_event_indexes: set[int] = set()
    changes: list[UKOracleAlignmentChange] = []
    for (before_path, before_node), (after_path, after_node) in zip(before_rows, after_rows, strict=False):
        if str(after_node.kind).lower() == "body":
            continue
        before_eid = _node_eid(before_node)
        after_eid = _node_eid(after_node)
        if before_eid == after_eid:
            continue
        event = event_by_after.get(after_eid or "")
        if event is None and after_eid is None:
            for event_index, candidate in enumerate(cleared_events):
                if event_index in used_cleared_event_indexes:
                    continue
                if str(candidate.get("kind") or "") != str(after_node.kind):
                    continue
                if candidate.get("label") != after_node.label:
                    continue
                event = candidate
                used_cleared_event_indexes.add(event_index)
                break
        path = before_path if before_path == after_path else f"{before_path} -> {after_path}"
        match_method = (
            str(event.get("match_method")) if event and event.get("match_method") else None
        )
        changes.append(
            UKOracleAlignmentChange(
                path=path,
                kind=str(after_node.kind),
                label=after_node.label,
                before_eid=before_eid,
                after_eid=after_eid,
                match_method=match_method,
                match_key=str(event.get("match_key")) if event and event.get("match_key") else None,
                grounding_classification=(
                    classify_suppression_mechanism(match_method)
                    if after_eid is None
                    else None
                ),
            )
        )
    for event_index, event in enumerate(cleared_events):
        if event_index in used_cleared_event_indexes:
            continue
        label_value = event.get("label")
        label = (
            label_value
            if label_value is None or isinstance(label_value, str)
            else str(label_value)
        )
        event_match_method = (
            str(event.get("match_method")) if event.get("match_method") else None
        )
        changes.append(
            UKOracleAlignmentChange(
                path="oracle_alignment/event",
                kind=str(event.get("kind") or ""),
                label=label,
                before_eid=str(event.get("before_eid")) if event.get("before_eid") else None,
                after_eid=None,
                match_method=event_match_method,
                match_key=str(event.get("match_key")) if event.get("match_key") else None,
                grounding_classification=classify_suppression_mechanism(event_match_method),
            )
        )

    cleared_count = sum(1 for change in changes if change.before_eid and not change.after_eid)
    oracle_assigned_count = sum(
        1
        for change in changes
        if change.after_eid is not None and change.after_eid in oracle_values
    )
    local_fallback_count = sum(
        1
        for change in changes
        if change.after_eid is not None and change.after_eid not in oracle_values
    )
    local_fallback_suppressed_count = sum(
        1 for change in changes if change.match_method == "local_fallback_suppressed"
    )
    transparent_wrapper_cleared_count = sum(
        1
        for change in changes
        if change.before_eid and not change.after_eid and change.kind.lower() in _TRANSPARENT_WRAPPER_KINDS
    )
    match_method_counts: dict[str, int] = {}
    for change in changes:
        if change.match_method:
            match_method_counts[change.match_method] = match_method_counts.get(change.match_method, 0) + 1
    # Totality over the negative space: every unmatched (after_eid=None) change
    # is tallied into exactly one of the four GROUNDING_CLASSIFICATIONS buckets.
    # An unmatched change with no classification — or one carrying a value
    # outside the contract set — is counted as ``unclassified`` and must be 0.
    grounding_classification_counts: dict[str, int] = {
        value: 0 for value in GROUNDING_CLASSIFICATIONS
    }
    unclassified_count = 0
    for change in changes:
        if change.after_eid is not None:
            continue
        classification = change.grounding_classification
        if classification in grounding_classification_counts:
            grounding_classification_counts[classification] += 1
        else:
            unclassified_count += 1
    return UKOracleAlignmentReport(
        enabled=True,
        stage="post_replay_adapter",
        rule_id=UK_ORACLE_ALIGNMENT_RULE_ID,
        phase="oracle_alignment",
        family="oracle_alignment_adapter",
        input_oracle_key_count=len(eid_map),
        input_oracle_eid_count=len(oracle_values),
        before_node_count=len(before_rows),
        after_node_count=len(after_rows),
        node_count_mismatch=node_count_mismatch,
        changed_count=len(changes),
        cleared_count=cleared_count,
        oracle_assigned_count=oracle_assigned_count,
        local_fallback_count=local_fallback_count,
        local_fallback_suppressed_count=local_fallback_suppressed_count,
        transparent_wrapper_cleared_count=transparent_wrapper_cleared_count,
        match_method_counts=match_method_counts,
        grounding_classification_counts=grounding_classification_counts,
        unclassified_count=unclassified_count,
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        changes=tuple(changes),
    )


def align_uk_replay_to_oracle_with_report(
    replayed_ir: IRStatute,
    *,
    eid_map: Optional[dict[str, str]] = None,
    text_map: Optional[dict[str, str]] = None,
    verbose: bool = False,
) -> UKOracleAlignmentResult:
    if not eid_map:
        return UKOracleAlignmentResult(
            statute=replayed_ir,
            report=_empty_alignment_report(enabled=False, eid_map=eid_map),
        )
    executor = UKReplayExecutor(
        replayed_ir,
        eid_map=eid_map,
        text_map=text_map or {},
        verbose=verbose,
    )
    executor.ground_ids()
    # Sub-PR C+D: ``executor.statute`` IS the immutable ``IRStatute`` — no
    # ``to_irstatute()`` boundary call left.
    aligned = executor.statute
    return UKOracleAlignmentResult(
        statute=aligned,
        report=_alignment_report(
            replayed_ir,
            aligned,
            eid_map=eid_map,
            match_events=tuple(executor.oracle_alignment_events),
        ),
    )


def align_uk_replay_to_oracle(
    replayed_ir: IRStatute,
    *,
    eid_map: Optional[dict[str, str]] = None,
    text_map: Optional[dict[str, str]] = None,
    verbose: bool = False,
) -> IRStatute:
    return align_uk_replay_to_oracle_with_report(
        replayed_ir,
        eid_map=eid_map,
        text_map=text_map or {},
        verbose=verbose,
    ).statute
