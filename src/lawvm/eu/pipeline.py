"""EU Replay Pipeline for orchestrating statute replay using Cellar metadata."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.invariant_profiles import CORE_REPLAY_DELTA_MINIMAL_FAMILIES
from lawvm.core.ir import IRStatute, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.temporal import TemporalEvent
from lawvm.core.timeline import Timelines, compile_timelines, materialize_pit
from lawvm.core import tree_ops
from lawvm.core.phase_result import Finding
from lawvm.core.replay_lints import build_text_duplication_findings
from lawvm.eu.grafter import parse_eu_regulation_ir
from lawvm.eu.ops_parser import EUOpsParser, EUOpsParserDiagnostic
from lawvm.eu.cellar import NoticeRequest, _request_notice
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.core.quirks_disposition import QuirksDisposition, coerce_quirks_disposition


# ---------------------------------------------------------------------------
# Kind mapping: ops parser vocabulary → grafter IR vocabulary
# ---------------------------------------------------------------------------
# The ops parser (stanza NLP) emits EU-native kinds like "article".
# The grafter maps FMX4 tags to LawVM IR kinds: ARTICLE → "section", etc.
# This map bridges the two so tree_ops.find() can locate targets.

_EU_OPS_KIND_TO_IR: Dict[str, str] = {
    "article": "section",  # FMX4 ARTICLE → IR "section"
    "paragraph": "paragraph",  # same
    "point": "item",  # FMX4 ITEM → IR "item"
    "annex": "annex",  # same
    "recital": "recital",  # same
    "subparagraph": "subparagraph",  # same
    "chapter": "chapter",  # same
    "division": "division",  # same
}


@dataclass(frozen=True)
class EUPipelineDiagnostic:
    rule_id: str
    family: str
    phase: str
    reason: str
    celex: str
    exception_type: str
    blocking: bool = True
    strict_disposition: str = "block"
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD
    detail: dict[str, object] = field(default_factory=dict)

    def as_detail(self) -> dict[str, object]:
        payload = diagnostic_detail(
            rule_id=self.rule_id,
            family=self.family,
            phase=self.phase,
            reason=self.reason,
            blocking=self.blocking,
            strict_disposition=self.strict_disposition,
            quirks_disposition=self.quirks_disposition,
            celex=self.celex,
            exception_type=self.exception_type,
        )
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload


def _map_address(addr: LegalAddress) -> LegalAddress:
    """Translate ops-parser kinds in a LegalAddress to grafter IR kinds."""
    mapped = tuple((_EU_OPS_KIND_TO_IR.get(kind.lower(), kind.lower()), label) for kind, label in addr.path)
    return LegalAddress(path=mapped, special=addr.special)


def _append_eu_replay_adjudication(
    adjudications_out: Optional[List[CompileAdjudication]],
    *,
    kind: str,
    message: str,
    op: LegalOperation,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Append an EU replay adjudication when a sink list is available."""
    if adjudications_out is None:
        return
    detail_payload: dict[str, Any] = dict(detail or {})
    family = ""
    if kind in {
        "eu_replay_unsupported_action",
        "eu_replay_unknown_action",
        "eu_replay_text_payload_missing",
        "eu_replay_target_not_found",
        "eu_replay_parent_not_found",
        "eu_replay_insert_parent_scope_unresolved",
    }:
        family = "unsupported_or_unresolved_action"
    elif kind == "eu_replay_tree_invariant_violation":
        family = "tree_invariant_violation"
    detail_payload = diagnostic_detail(
        rule_id=kind,
        phase="replay",
        family=family,
        blocking=bool(detail_payload.get("blocking", True)),
        detail=detail_payload,
    )
    adjudications_out.append(
        CompileAdjudication(
            kind=kind,
            message=message,
            source_statute=op.source.statute_id if op.source else "",
            op_id=op.op_id,
            blocking=bool(detail_payload["blocking"]),
            phase=str(detail_payload["phase"]),
            detail=detail_payload,
        )
    )


def _eu_adjudication_from_finding(finding: Finding) -> CompileAdjudication:
    """Project replay-lint findings into the EU replay compatibility bag."""
    detail = dict(finding.detail)
    blocking = bool(finding.blocking)
    phase = str(detail.get("phase") or "")
    if not phase:
        raise ValueError(
            f"EU replay-lint finding kind={finding.kind!r} carries no phase; "
            "the finding emitter must set detail['phase']."
        )
    detail.setdefault("blocking", blocking)
    detail.setdefault("strict_disposition", "block" if blocking else "record")
    detail.setdefault("quirks_disposition", "record")
    message = str(detail.pop("message", "") or "")
    return CompileAdjudication(
        kind=str(finding.kind or ""),
        message=message,
        source_statute=str(finding.source_statute or ""),
        blocking=blocking,
        phase=phase,
        detail=detail,
    )


def _eu_adjudication_from_pipeline_diagnostic(
    diagnostic: EUPipelineDiagnostic,
) -> CompileAdjudication:
    detail = diagnostic.as_detail()
    return CompileAdjudication(
        kind=diagnostic.rule_id,
        message=diagnostic.reason,
        source_statute=diagnostic.celex,
        blocking=bool(diagnostic.blocking),
        phase=str(diagnostic.phase),
        detail=detail,
    )


def _eu_adjudication_from_parser_diagnostic(
    act_celex: str,
    diagnostic: EUOpsParserDiagnostic,
) -> CompileAdjudication:
    detail = diagnostic.as_detail()
    detail["celex"] = act_celex
    return CompileAdjudication(
        kind=diagnostic.rule_id,
        message=diagnostic.reason,
        source_statute=act_celex,
        blocking=bool(diagnostic.blocking),
        phase=str(diagnostic.phase),
        detail=detail,
    )


# ---------------------------------------------------------------------------
# apply_eu_ops: wire LegalOperations to tree_ops
# ---------------------------------------------------------------------------


def apply_eu_ops(
    base: IRStatute,
    ops: List[LegalOperation],
    adjudications_out: Optional[List[CompileAdjudication]] = None,
) -> IRStatute:
    """Apply compiled LegalOperations to an EU baseline IRStatute.

    For each operation:
      - replace: find target node via tree_ops.find(), replace with payload
      - repeal:  find target node, remove it
      - insert:  find parent container, insert payload at sorted position

    Operations that cannot be resolved (target not found, missing payload,
    unsupported action) are skipped and emitted as compile adjudications when
    ``adjudications_out`` is provided. Returns a new IRStatute with replay stats
    in metadata; the original is not mutated.

    Args:
        base: The parsed baseline IRStatute (from parse_eu_regulation_ir).
        ops:  List of LegalOperations (from EUOpsParser.extract_ops).

    Returns:
        A new IRStatute with all applicable operations applied.
    """
    # The shared IR is frozen; build a new tree only when applying ops.
    body = base.body
    applied = 0
    skipped = 0
    seen_invariant_violations: set[str] = set()
    seen_duplication_warnings: set[tuple[tuple[str, object], ...]] = set()
    replay_tree_invariant_families = CORE_REPLAY_DELTA_MINIMAL_FAMILIES

    def _duplication_warning_key(warning: dict[str, object]) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(warning.items()))

    def _record_invariant_violations(op: LegalOperation, target: LegalAddress) -> None:
        for violation in tree_ops.iter_tree_invariant_violations(body, families=replay_tree_invariant_families):
            if violation.message in seen_invariant_violations:
                continue
            violation_detail = violation.to_dict()
            _append_eu_replay_adjudication(
                adjudications_out,
                kind="eu_replay_tree_invariant_violation",
                message="EU replay violated order/duplication tree invariant after applying an op.",
                op=op,
                detail={
                    "action": str(action),
                    "target": str(target),
                    "violation": violation.message,
                    "invariant_kind": violation_detail["kind"],
                    "invariant_path": violation_detail["path"],
                    "invariant": violation_detail,
                },
            )
            seen_invariant_violations.add(violation.message)

    def _record_new_duplication_warnings(
        before_body,
        op: LegalOperation,
        target: LegalAddress,
    ) -> None:
        if adjudications_out is None:
            return
        before_warnings = {
            _duplication_warning_key(warning) for warning in tree_ops.find_text_duplication_warnings(before_body)
        }
        for warning in tree_ops.find_text_duplication_warnings(body):
            warning_key = _duplication_warning_key(warning)
            if warning_key in before_warnings or warning_key in seen_duplication_warnings:
                continue
            adjudications_out.append(
                CompileAdjudication(
                    kind="text_duplication_warning",
                    message="Replay output contains a suspicious duplicated text tract.",
                    source_statute=op.source.statute_id if op.source else "",
                    op_id=op.op_id,
                    blocking=False,
                    phase="apply_op",
                    detail=diagnostic_detail(
                        rule_id="text_duplication_warning",
                        phase="apply_op",
                        blocking=False,
                        action=str(action),
                        target=str(target),
                        detail=warning,
                    ),
                )
            )
            seen_duplication_warnings.add(warning_key)

    for op in ops:
        target = _map_address(op.target)
        path_steps = list(target.path)
        action = op.action.value if hasattr(op.action, "value") else op.action

        if action == StructuralAction.REPLACE.value:
            if op.payload is None:
                _append_eu_replay_adjudication(
                    adjudications_out,
                    kind="eu_replay_text_payload_missing",
                    message="EU replay skipped replace: payload missing.",
                    op=op,
                    detail={"action": "replace", "target": str(target)},
                )
                skipped += 1
                continue
            # Find the target node
            found = tree_ops.find(
                body,
                kind=path_steps[-1][0],
                label=path_steps[-1][1],
                scope_kind=path_steps[0][0] if len(path_steps) > 1 else None,
                scope_label=path_steps[0][1] if len(path_steps) > 1 else None,
            )
            if found is None:
                _append_eu_replay_adjudication(
                    adjudications_out,
                    kind="eu_replay_target_not_found",
                    message="EU replay skipped replace: target not found.",
                    op=op,
                    detail={"action": "replace", "target": str(target)},
                )
                skipped += 1
                continue
            before_body = body
            body = tree_ops.replace_at(body, found, op.payload)
            applied += 1
            _record_invariant_violations(op, target)
            _record_new_duplication_warnings(before_body, op, target)

        elif action == StructuralAction.REPEAL.value:
            found = tree_ops.find(
                body,
                kind=path_steps[-1][0],
                label=path_steps[-1][1],
                scope_kind=path_steps[0][0] if len(path_steps) > 1 else None,
                scope_label=path_steps[0][1] if len(path_steps) > 1 else None,
            )
            if found is None:
                _append_eu_replay_adjudication(
                    adjudications_out,
                    kind="eu_replay_target_not_found",
                    message="EU replay skipped repeal: target not found.",
                    op=op,
                    detail={"action": "repeal", "target": str(target)},
                )
                skipped += 1
                continue
            before_body = body
            body = tree_ops.remove_at(body, found)
            applied += 1
            _record_invariant_violations(op, target)
            _record_new_duplication_warnings(before_body, op, target)

        elif action == StructuralAction.INSERT.value:
            if op.payload is None:
                _append_eu_replay_adjudication(
                    adjudications_out,
                    kind="eu_replay_text_payload_missing",
                    message="EU replay skipped insert: payload missing.",
                    op=op,
                    detail={"action": "insert", "target": str(target)},
                )
                skipped += 1
                continue
            # For insert, the target address specifies where to insert.
            # The parent is target minus the last path element.
            if len(path_steps) > 1:
                parent_path = path_steps[:-1]
                parent_kind = parent_path[-1][0]
                parent_label = parent_path[-1][1]
                if tree_ops.resolve(body, parent_path) is None:
                    unscoped_parent_candidates = tree_ops.find_all(body, parent_kind, parent_label)
                    if unscoped_parent_candidates and len(parent_path) > 1:
                        _append_eu_replay_adjudication(
                            adjudications_out,
                            kind="eu_replay_insert_parent_scope_unresolved",
                            message=(
                                "EU replay skipped insert: scoped parent path not found; "
                                "unscoped lookalike parent candidates were ignored."
                            ),
                            op=op,
                            detail={
                                "action": "insert",
                                "target": str(target),
                                "parent_kind": parent_kind,
                                "parent_label": parent_label,
                                "parent_path": [f"{kind}:{label}" for kind, label in parent_path],
                                "unscoped_parent_candidates": [
                                    [f"{kind}:{label}" for kind, label in candidate_path]
                                    for candidate_path in unscoped_parent_candidates
                                ],
                            },
                        )
                        skipped += 1
                        continue
                    _append_eu_replay_adjudication(
                        adjudications_out,
                        kind="eu_replay_parent_not_found",
                        message="EU replay skipped insert: parent target not found.",
                        op=op,
                        detail={
                            "action": "insert",
                            "target": str(target),
                            "parent_kind": parent_kind,
                            "parent_label": parent_label,
                            "parent_path": [f"{kind}:{label}" for kind, label in parent_path],
                        },
                    )
                    skipped += 1
                    continue
            else:
                parent_path = []  # insert at body level
            before_body = body
            body = tree_ops.insert_sorted(body, parent_path, op.payload)
            applied += 1
            _record_invariant_violations(op, target)
            _record_new_duplication_warnings(before_body, op, target)

        elif action in ("text_replace", "text_repeal", "renumber"):
            # Not yet supported for EU pipeline
            _append_eu_replay_adjudication(
                adjudications_out,
                kind="eu_replay_unsupported_action",
                message="EU replay skipped unsupported action.",
                op=op,
                detail={"action": action, "target": str(target)},
            )
            skipped += 1
            continue

        elif action == "unknown":
            _append_eu_replay_adjudication(
                adjudications_out,
                kind="eu_replay_unknown_action",
                message="EU replay skipped unknown action.",
                op=op,
                detail={"target": str(target)},
            )
            skipped += 1
            continue

        else:
            _append_eu_replay_adjudication(
                adjudications_out,
                kind="eu_replay_unknown_action",
                message="EU replay skipped unknown action.",
                op=op,
                detail={"action": str(action), "target": str(target)},
            )
            skipped += 1

    metadata = dict(base.metadata)
    metadata["eu_replay_applied_op_count"] = applied
    metadata["eu_replay_skipped_op_count"] = skipped

    return IRStatute(
        statute_id=base.statute_id,
        title=base.title,
        body=body,
        supplements=list(base.supplements),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Typed apply-result carrier (AGENTS.md §1.8 — replay conservation contract).
#
# The classic ``apply_eu_ops`` returns only the mutated :class:`IRStatute` and
# shuttles skipped-op evidence through an ``adjudications_out`` out-parameter.
# The AGENTS.md §1.8 contract requires the apply path to return accepted AND
# rejected carriers (``FilterResult`` shape) so a downstream consumer cannot
# silently lose track of filtered ops. ``apply_eu_ops_conserved`` is the typed
# wrapper that mirrors ``apply_eu_ops``'s behaviour and surfaces both lanes
# via the contract-shape FilterResult[LegalOperation]. Production routing to
# the conserved wrapper is a separate per-frontend decision (AGENTS.md §1.8);
# this wrapper is added WITHOUT touching callers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EUApplyResult:
    """Typed apply-result conservation carrier (AGENTS.md §1.8).

    Mirrors the FilterResult contract shape: every op in the input set is
    either in ``applied_ops`` (its binding landed in the output statute) or
    surfaces as a :class:`RejectedItem[LegalOperation]` witness in
    ``skipped_items`` with a ``reason`` / ``reason_code`` and ``blocking``
    disposition. The mutation footprint (the IRStatute returned by
    :func:`apply_eu_ops`) is the ``statute`` field.

    The ``filter_result`` field is the canonical ``FilterResult`` projection
    of the same accepted/rejected partition, so callers that already consume
    the shared core type can reuse it without unpacking ``applied_ops`` /
    ``skipped_items`` separately.

    Note: this is distinct from :class:`EUReplayResult`, which carries the
    full replay pipeline outputs (baseline, timelines, temporal_events).
    ``EUApplyResult`` is the per-apply-fold receipt only.

    Post-apply adjudications (``eu_replay_tree_invariant_violation`` and
    ``text_duplication_warning``) are emitted by the bare variant AFTER an op
    was applied — they record findings about the resulting body, not a skip
    of the op. They are intentionally NOT in
    :data:`_EU_SKIP_ADJUDICATION_KINDS`; only the genuine per-op skip kinds
    (``eu_replay_text_payload_missing`` / ``eu_replay_target_not_found`` /
    ``eu_replay_parent_not_found`` / ``eu_replay_insert_parent_scope_unresolved``
    / ``eu_replay_unsupported_action`` / ``eu_replay_unknown_action``) mark an
    op as rejected.
    """

    statute: IRStatute
    filter_result: "FilterResult[LegalOperation]"

    @property
    def applied_ops(self) -> tuple["LegalOperation", ...]:
        return self.filter_result.accepted_items

    @property
    def skipped_items(self) -> tuple["RejectedItem[LegalOperation]", ...]:
        return self.filter_result.rejected_items


# Per-op skip adjudication kinds emitted by :func:`apply_eu_ops`. Each is
# emitted ONLY at a per-op skip path that emits an adjudication and then
# ``continue``s (the op is NOT applied). Post-apply findings
# (``eu_replay_tree_invariant_violation``, ``text_duplication_warning``) are
# emitted AFTER an op was applied — they are observations about the resulting
# body, not skips — and are intentionally excluded from this set.
_EU_SKIP_ADJUDICATION_KINDS = frozenset(
    {
        "eu_replay_text_payload_missing",
        "eu_replay_target_not_found",
        "eu_replay_parent_not_found",
        "eu_replay_insert_parent_scope_unresolved",
        "eu_replay_unsupported_action",
        "eu_replay_unknown_action",
    }
)


def apply_eu_ops_conserved(
    base: IRStatute,
    ops: List[LegalOperation] | tuple["LegalOperation", ...],
    *,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
) -> EUApplyResult:
    """Apply an EU op set with a typed conservation receipt (§1.8).

    Mirrors :func:`apply_eu_ops` exactly (same replay semantics, same
    ``adjudications_out`` side channel — when the caller passes one, both the
    conserved typed result AND the existing descriptive adjudications are
    populated). Returns a :class:`EUApplyResult` whose ``filter_result``
    partitions every input op into accepted (its replay applied) or rejected
    (its replay skipped, with a witness adjudication carrying the reason).
    The contract is monotone: every input op ends up either accepted or
    rejected, never silently dropped.

    The partition keys on ``op_id`` (the EU bare variant emits one
    ``CompileAdjudication`` per skipped op carrying that op's ``op_id``). An
    op is rejected iff its ``op_id`` appears in a per-op SKIP adjudication
    (``eu_replay_text_payload_missing`` / ``eu_replay_target_not_found`` /
    ``eu_replay_parent_not_found`` /
    ``eu_replay_insert_parent_scope_unresolved`` /
    ``eu_replay_unsupported_action`` / ``eu_replay_unknown_action``).
    Post-apply findings (``eu_replay_tree_invariant_violation``,
    ``text_duplication_warning``) record observations after an op WAS applied
    and must NOT mark their op as rejected. Empty or duplicate ``op_id``
    values would mis-partition the per-op lane and are rejected with a
    ``ValueError`` rather than silently dropping or mis-bucketing an op.
    """
    ops_list = list(ops)
    # Conservation requires a robust op IDENTITY for the accepted/rejected
    # partition. The op_id string is NOT a safe identity key: it defaults to
    # "" (a SKIPPED op with an empty op_id would be filtered out of the
    # skipped set and silently land in the accepted lane — a §1.8
    # "never silently dropped" violation) and it is not guaranteed unique
    # (a duplicate/shared op_id mis-partitions both ops). Fail loud on either
    # degenerate case so the op_id-keyed partition below is provably bijective.
    op_ids = [op.op_id for op in ops_list]
    if any(not op_id for op_id in op_ids):
        empty_positions = [i for i, op_id in enumerate(op_ids) if not op_id]
        raise ValueError(
            "apply_eu_ops_conserved requires every op to carry a non-empty op_id "
            "(the conservation partition keys on op_id and an empty op_id would be "
            f"silently dropped from the skipped lane). Empty op_id at positions {empty_positions}."
        )
    if len(set(op_ids)) != len(op_ids):
        counts = Counter(op_ids)
        duplicates = sorted(op_id for op_id, n in counts.items() if n > 1)
        raise ValueError(
            "apply_eu_ops_conserved requires op_ids to be unique (the conservation "
            "partition keys on op_id and duplicate op_ids would mis-partition). "
            f"Duplicate op_ids: {duplicates}."
        )
    # Trust the bare-apply contract: ``apply_eu_ops`` appends each per-op
    # adjudication to ``adjudications_out`` in place. Routing the caller's
    # list directly through bare apply means a mid-apply raise (any §1.10
    # fail-loud path) preserves the witnesses emitted BEFORE the raise on the
    # caller's accumulator — the caller can then diagnose via the partial
    # adjudications (AGENTS.md §1.0 evidence is not silently destroyed).
    # When the caller did not pass an ``adjudications_out``, use a throwaway
    # local buffer so bare-apply's mutations stay scoped and the partition
    # below still has a source to read from. Mirrors the EE/NO/SE wrappers
    # (conserved-wrapper consistency fix).
    adjudications: List[CompileAdjudication] = (
        adjudications_out if adjudications_out is not None else []
    )
    applied_statute = apply_eu_ops(
        base,
        ops_list,
        adjudications_out=adjudications,
    )
    # Partition: an op is REJECTED iff its op_id appears on a per-op SKIP
    # adjudication. Post-apply findings (eu_replay_tree_invariant_violation,
    # text_duplication_warning) record observations after an op WAS applied
    # and must NOT mark their op as rejected. See
    # ``_EU_SKIP_ADJUDICATION_KINDS`` above.
    skipped_op_ids = {
        a.op_id
        for a in adjudications
        if a.op_id and a.kind in _EU_SKIP_ADJUDICATION_KINDS
    }
    accepted: list[LegalOperation] = []
    rejected: list[RejectedItem[LegalOperation]] = []
    for op in ops_list:
        if op.op_id in skipped_op_ids:
            matching = [a for a in adjudications if a.op_id == op.op_id and a.kind in _EU_SKIP_ADJUDICATION_KINDS]
            reason = matching[0].message if matching else "EU replay op skipped without a typed reason."
            reason_code = matching[0].kind if matching else "eu_replay_skipped_unspecified"
            rejected.append(
                RejectedItem(
                    item=op,
                    reason=reason,
                    reason_code=reason_code,
                    blocking=False,
                )
            )
        else:
            accepted.append(op)
    # Propagation: bare apply already mutated ``adjudications_out`` in place
    # (the caller's list when one was provided) — no local-copy / clear /
    # extend round-trip needed. The previous local-copy-then-extend pattern
    # silently dropped bare-apply's partial adjudication witness when bare
    # apply raised mid-fold (the §1.0 evidence-loss failure); routing the
    # caller's list directly closes that hole.
    return EUApplyResult(
        statute=applied_statute,
        filter_result=FilterResult(
            accepted_items=tuple(accepted),
            rejected_items=tuple(rejected),
        ),
    )


@dataclass
class EUReplayResult:
    """Result of an EU statute replay."""

    celex: str
    baseline: IRStatute
    replayed: Optional[IRStatute] = None
    ops: List[LegalOperation] = field(default_factory=list)
    timelines: Optional[Timelines] = None
    cutoff_date: Optional[str] = None
    adjudications: List[CompileAdjudication] = field(default_factory=list)
    temporal_events: tuple[TemporalEvent, ...] = ()
    # Typed apply-result conservation receipt (AGENTS.md §1.8 + §2.9
    # guard-liveness). Populated when the production apply lane routes
    # through ``apply_eu_ops_conserved``: partitions every input op into
    # ``accepted_items`` (its binding landed in ``replayed``) or
    # ``rejected_items`` (its replay skipped, with a typed ``RejectedItem``
    # witness carrying ``reason`` / ``reason_code`` / ``blocking``). Without
    # this field the conserved wrapper exists but is unreachable from
    # production — the §2.9 worst-class silent failure (a guard that exists
    # but is unreachable from the production lane).
    apply_filter_result: Optional[FilterResult[LegalOperation]] = None
    # Apply-fold raise signal (silent-failure review HIGH #2). Mirrors the
    # EE/NO production-caller contract: when ``apply_eu_ops_conserved`` raises
    # mid-fold, the partial witnesses emitted BEFORE the raise (pipeline
    # diagnostics + parser diagnostics + bare-apply's per-op skips) ARE
    # preserved on ``adjudications`` (the conserved wrapper routes
    # ``adjudications_out`` directly through bare apply), and a typed
    # ``eu_replay_apply_raise`` orchestration adjudication is appended per
    # §1.10 with the exception embedded as a ~400 char ``clause_text`` snippet
    # so a downstream consumer can diagnose the raise without re-running
    # extraction. ``error`` carries the same exception summary as a string for
    # parity with the EE/NO ``result.error = f"Failed to apply ops: {e}"``
    # pattern (mirrors ``EEPitResult.error`` / ``NOReplayResult.error``).
    error: Optional[str] = None


def _eu_pipeline_diagnostic_from_cellar_row(celex: str, row: dict[str, Any]) -> EUPipelineDiagnostic:
    detail = row.get("detail", {})
    if not isinstance(detail, dict):
        detail = {}
    return EUPipelineDiagnostic(
        rule_id=str(row.get("rule_id") or "eu_cellar_manifestation_option_skipped"),
        family=str(row.get("family") or "source_pathology"),
        phase=str(row.get("phase") or "acquisition"),
        reason=str(row.get("reason") or "EU Cellar acquisition diagnostic"),
        celex=celex,
        exception_type=str(detail.get("reason_code") or row.get("kind") or "cellar_diagnostic"),
        blocking=bool(row.get("blocking", True)),
        strict_disposition=str(row.get("strict_disposition") or "block"),
        quirks_disposition=coerce_quirks_disposition(row.get("quirks_disposition") or QuirksDisposition.RECORD),
        detail={
            "cellar_source": str(row.get("source") or ""),
            "cellar_detail": dict(detail),
        },
    )


class EUReplayPipeline:
    def __init__(self, cache_dir: Path = Path(".cache/eu_replay")):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        stanza_cache = self.cache_dir / "stanza_cache"
        self.parser = EUOpsParser(cache_dir=str(stanza_cache))
        self.diagnostics: list[EUPipelineDiagnostic] = []
        self.parser_diagnostics: list[tuple[str, EUOpsParserDiagnostic]] = []

    def discover_affecting_acts(self, celex: str) -> List[str]:
        """Query Cellar for acts that modify or amend the given CELEX."""
        notice = NoticeRequest(celex=celex, notice_format="xml", notice_type="tree", decode_language="eng")
        try:
            data, meta = _request_notice(notice)
            root = ET.fromstring(data)

            affecting_celexes = []
            for child in root.iter():
                tag = child.tag
                if any(
                    kw in tag
                    for kw in (
                        "MODIFIED_BY_WORK",
                        "AMENDED_BY_WORK",
                        "HAS_CORRIGENDUM_WORK",
                        "WORK_HAS_MODIFICATION",
                        "CORRECTED_BY",
                    )
                ):
                    uri_el = child.find(".//URI")
                    if uri_el is not None:
                        candidate_celex = None
                        ident = uri_el.find("IDENTIFIER")
                        if ident is not None and ident.text:
                            candidate_celex = ident.text
                        else:
                            value = uri_el.find("VALUE")
                            if value is not None and value.text and "/celex/" in value.text:
                                candidate_celex = value.text.split("/celex/")[-1].split("?")[0]

                        if candidate_celex:
                            if candidate_celex.startswith("0") or not candidate_celex[0].isdigit():
                                self.diagnostics.append(
                                    EUPipelineDiagnostic(
                                        rule_id="eu_affecting_candidate_celex_rejected",
                                        family="source_pathology",
                                        phase="acquisition",
                                        reason=(
                                            "EU Cellar affecting-act candidate was rejected because its CELEX "
                                            "identifier was not a usable affecting act ID."
                                        ),
                                        celex=celex,
                                        exception_type="invalid_candidate_celex",
                                        detail={
                                            "candidate_celex": candidate_celex,
                                            "relation_tag": tag,
                                            "reason_code": "invalid_candidate_celex",
                                        },
                                    )
                                )
                                continue
                            if candidate_celex == celex:
                                self.diagnostics.append(
                                    EUPipelineDiagnostic(
                                        rule_id="eu_affecting_candidate_celex_rejected",
                                        family="source_pathology",
                                        phase="acquisition",
                                        reason=(
                                            "EU Cellar affecting-act candidate was rejected because it points "
                                            "back to the affected act itself."
                                        ),
                                        celex=celex,
                                        exception_type="self_reference_candidate",
                                        detail={
                                            "candidate_celex": candidate_celex,
                                            "relation_tag": tag,
                                            "reason_code": "self_reference_candidate",
                                        },
                                    )
                                )
                                continue
                            affecting_celexes.append(candidate_celex)

            return sorted(list(set(affecting_celexes)))
        except Exception as e:
            self._record_diagnostic(
                rule_id="eu_affecting_discovery_failed",
                celex=celex,
                phase="acquisition",
                reason="EU Cellar affecting-act discovery failed; replay cannot distinguish this from no affecting acts without the diagnostic",
                exc=e,
            )
            print(f"Error discovering affecting acts for {celex}: {e}")
            return []

    def fetch_amendment_text(self, celex: str) -> str:
        """Fetch the XHTML manifestation of an amending act and extract its amendment text."""
        from lawvm.eu.cellar import select_manifestation_option, _request_url

        notice_path = self.cache_dir / f"{celex.replace('/', '_')}_tree.xml"
        if not notice_path.exists():
            notice = NoticeRequest(celex=celex, notice_format="xml", notice_type="tree", decode_language="eng")
            data, _ = _request_notice(notice)
            notice_path.write_bytes(data)

        try:
            amendment_path = self.cache_dir / f"{celex.replace('/', '_')}_amendment.xhtml"
            manifestation_diagnostics: list[dict[str, Any]] = []
            option = select_manifestation_option(
                notice_path,
                language="ENG",
                manifestation_type="xhtml",
                diagnostics_out=manifestation_diagnostics,
            )
            for diagnostic in manifestation_diagnostics:
                self.diagnostics.append(_eu_pipeline_diagnostic_from_cellar_row(celex, diagnostic))
            item_uri = option["items"][0]["uri"]["value"] if option["items"] else option["manifestation_uri"]["value"]
            if not item_uri:
                raise ValueError(f"No URI found for amendment {celex}")

            if "/cellar/" in item_uri and "." not in item_uri.split("/")[-1]:
                item_uri += "?format=xhtml"

            print(f"DEBUG: Fetching amendment from {item_uri}")
            data, _ = _request_url(item_uri, accept="application/xhtml+xml,text/html")
            amendment_path.write_bytes(data)
            text = data.decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", " ", text)
            text = text.replace("&lsquo;", "'").replace("&rsquo;", "'").replace("&nbsp;", " ")
            return text
        except Exception as e:
            self._record_diagnostic(
                rule_id="eu_amendment_text_fetch_failed",
                celex=celex,
                phase="acquisition",
                reason="EU amendment text fetch failed; replay cannot distinguish this from an empty amendment text without the diagnostic",
                exc=e,
            )
            print(f"Error fetching amendment {celex}: {e}")
            return ""

    def _record_diagnostic(
        self,
        *,
        rule_id: str,
        celex: str,
        phase: str,
        reason: str,
        exc: Exception,
    ) -> None:
        self.diagnostics.append(
            EUPipelineDiagnostic(
                rule_id=rule_id,
                family="source_pathology",
                phase=phase,
                reason=reason,
                celex=celex,
                exception_type=type(exc).__name__,
            )
        )

    def compile_ops_for_statute(self, celex: str) -> List[LegalOperation]:
        """Fetch affecting acts and compile their amendment text into LegalOperations."""
        affecting_acts = self.discover_affecting_acts(celex)
        all_ops: List[LegalOperation] = []
        self.parser_diagnostics = []

        for act_celex in affecting_acts:
            diagnostics_before_fetch = len(self.diagnostics)
            text = self.fetch_amendment_text(act_celex)
            if text:
                print(f"DEBUG: Processing act {act_celex}, text length={len(text)}")
                ops = self.parser.extract_ops(text)
                self.parser_diagnostics.extend(
                    (act_celex, diagnostic)
                    for diagnostic in getattr(self.parser, "diagnostics", ())
                )
                print(f"DEBUG: Extracted {len(ops)} ops for {act_celex}")
                for op in ops:
                    all_ops.append(replace(op, source=OperationSource(statute_id=act_celex)))
            else:
                if len(self.diagnostics) == diagnostics_before_fetch:
                    self.diagnostics.append(
                        EUPipelineDiagnostic(
                            rule_id="eu_amendment_text_empty",
                            family="source_pathology",
                            phase="acquisition",
                            reason=(
                                "EU affecting act produced empty amendment text; replay cannot treat the "
                                "discovered source lane as if it had no operative content."
                            ),
                            celex=act_celex,
                            exception_type="not_applicable",
                        )
                    )
                print(f"DEBUG: No text fetched for act {act_celex}")

        return all_ops

    def replay_statute(
        self,
        celex: str,
        cutoff_date: Optional[str] = None,
        temporal_events: tuple[TemporalEvent, ...] = (),
    ) -> EUReplayResult:
        """Parse baseline, compile ops, apply them, and build timelines.

        Returns an EUReplayResult with:
          - baseline: the parsed base statute (unamended)
          - ops: the compiled LegalOperations from affecting acts
          - replayed: the statute after applying ops via tree_ops
          - timelines: compiled ProvisionTimelines for PIT queries
        """
        self.diagnostics = []
        self.parser_diagnostics = []
        baseline_path = self.cache_dir / f"{celex.replace('/', '_')}_baseline.xhtml"
        if not baseline_path.exists():
            baseline_text = self.fetch_amendment_text(celex)
            baseline_path.write_text(baseline_text)

        baseline = parse_eu_regulation_ir(baseline_path, celex=celex)

        ops = self.compile_ops_for_statute(celex)
        adjudications: List[CompileAdjudication] = []
        adjudications.extend(
            _eu_adjudication_from_pipeline_diagnostic(diagnostic)
            for diagnostic in self.diagnostics
        )
        adjudications.extend(
            _eu_adjudication_from_parser_diagnostic(act_celex, diagnostic)
            for act_celex, diagnostic in self.parser_diagnostics
        )

        # Apply ops to produce replayed statute.
        # Route production through the conserved wrapper (AGENTS.md §1.8 +
        # §2.9 guard-liveness). The bare ``apply_eu_ops`` returned only the
        # IRStatute and shuttled skip-evidence through ``adjudications_out``
        # side-effect; the conserved wrapper returns a typed ``EUApplyResult``
        # whose ``filter_result`` partitions ops into accepted /
        # RejectedItem witnesses (the §1.8 "no unsupported lane disappears"
        # contract). Without routing production here, the conserved wrapper
        # would be exercised only by tests — a §2.9 worst-class silent
        # failure (a guard that exists but is unreachable from production).
        # ``adjudications`` (the local list pre-populated with pipeline +
        # parser diagnostics above) IS routed directly through bare apply by
        # the conserved wrapper; on a mid-apply raise, bare-apply's partial
        # witnesses emitted BEFORE the raise persist on this accumulator —
        # the §1.0 evidence-not-silently-destroyed contract. Mirrors the
        # NO/EE production caller pattern (silent-failure review HIGH #2 —
        # iter2 W2 closed the conserved-wrapper propagate-on-raise; this wrap
        # surfaces the partial list on the production result instead of
        # letting the raw exception discard it).
        try:
            apply_result = apply_eu_ops_conserved(
                baseline,
                ops,
                adjudications_out=adjudications,
            )
            replayed = apply_result.statute
            apply_filter_result = apply_result.filter_result
        except Exception as e:
            # ``adjudications`` already carries pipeline + parser diagnostics
            # AND bare-apply's partial witnesses (appended in place before
            # the raise; the conserved wrapper routes ``adjudications_out``
            # directly through bare apply so partials persist — iter2 W2).
            # Emit a typed ``eu_replay_apply_raise`` orchestration adjudication
            # per §1.10 (embedding the exception truncated to a ~400 char
            # ``clause_text`` snippet via ``diagnostic_detail``) so a
            # downstream consumer can diagnose the apply raise without
            # re-running extraction. Return a typed ``EUReplayResult``
            # mirroring the EE/NO on-raise shape: ``replayed``/``timelines``
            # stay ``None`` (apply did not produce a tree), ``error`` carries
            # the exception summary (the blocking gate — the EE/NO convention
            # for apply-fold failure), and ``adjudications`` carries the
            # preserved partial ledger. The adjudication is non-blocking — it
            # is a WITNESS, not the gate; ``error`` IS the gate.
            adjudications.append(
                CompileAdjudication(
                    kind="eu_replay_apply_raise",
                    message=f"EU replay apply raised {type(e).__name__}: {e}",
                    source_statute=celex,
                    blocking=False,
                    phase="replay",
                    detail=diagnostic_detail(
                        rule_id="eu_replay_apply_raise",
                        phase="replay",
                        blocking=False,
                        family="orchestration_failure",
                        reason=(
                            "apply_eu_ops_conserved raised mid-fold; partial "
                            "witnesses preserved on adjudications"
                        ),
                        exception_type=type(e).__name__,
                        exception=str(e),
                        # iter4 W1 M2 (silent-failure review M2): ``clause_text``
                        # currently carries the apply-raise exception string
                        # (``str(e)[:400]``), NOT the §1.10 source-text snippet.
                        # The per-op source_clause_extract extraction is deferred
                        # per task #50 — at the apply_raise catch site bare-apply
                        # has raised mid-fold without exposing the failing op's
                        # source span, so the only immediately-available diagnostic
                        # snippet IS the exception string. Once task #50 lands
                        # the per-op source_anchor sink through bare-apply's raise
                        # path, this will carry source text per §1.10 embed-snippet
                        # contract (mirrors the NO precedent at
                        # norway/replay.py no_replay_apply_raise and the EE
                        # precedent at estonia/replay.py ee_replay_apply_raise).
                        clause_text=str(e)[:400],
                    ),
                )
            )
            return EUReplayResult(
                celex=celex,
                baseline=baseline,
                replayed=None,
                ops=ops,
                timelines=None,
                adjudications=adjudications,
                cutoff_date=cutoff_date,
                temporal_events=temporal_events,
                apply_filter_result=None,
                error=f"Failed to apply ops: {e}",
            )
        replay_text_duplication_findings = build_text_duplication_findings(
            replayed.body,
            phase="replay_fold",
            source_statute=celex,
        )
        adjudications.extend(_eu_adjudication_from_finding(finding) for finding in replay_text_duplication_findings)

        # Compile timelines and materialize PIT
        timelines = compile_timelines(
            baseline,
            ops,
            temporal_events=temporal_events,
        )
        as_of = cutoff_date or "9999-12-31"
        pit = materialize_pit(timelines, as_of=as_of, base=baseline)

        # Use PIT body as the canonical replayed body (timeline-primary,
        # same pattern as Finnish pipeline)
        replayed = IRStatute(
            statute_id=pit.statute_id,
            title=pit.title,
            body=pit.body,
            supplements=pit.supplements,
            metadata=pit.metadata,
        )
        materialized_text_duplication_findings = build_text_duplication_findings(
            replayed.body,
            phase="materialized",
            source_statute=celex,
        )
        adjudications.extend(_eu_adjudication_from_finding(finding) for finding in materialized_text_duplication_findings)

        return EUReplayResult(
            celex=celex,
            baseline=baseline,
            replayed=replayed,
            ops=ops,
            timelines=timelines,
            adjudications=adjudications,
            cutoff_date=cutoff_date,
            temporal_events=temporal_events,
            apply_filter_result=apply_filter_result,
        )
