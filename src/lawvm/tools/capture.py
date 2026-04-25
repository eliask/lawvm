"""lawvm capture — amendment-level pipeline artifact capture.

Emits a compact, typed JSON bundle for one statute's Finland replay pipeline,
grouped by amendment source. This is intended as a first-step measurement tool
for decomposed-pipeline work: it captures what was extracted/compiled/applied
per amendment without refactoring the replay engine yet.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, cast

from lxml import etree

from lawvm.core.ir import IRNode, LegalOperation, TextPatchSpec
from lawvm.finland.source_adjudication import build_source_adjudication
from lawvm.finland.ops import FailedOp
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.tools.capture_models import (
    CaptureAmendmentView,
    CaptureBodyShapeView,
    CaptureCountsView,
    CapturePayload,
    CaptureReplayMetaView,
    CaptureSourceAdjudicationView,
    CaptureSourceCompletenessView,
    CaptureSourcePathologyView,
)


def _collapse_ws(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split())


def _excerpt(text: str, limit: int = 240) -> str:
    text = _collapse_ws(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _local_name(el: etree._Element) -> str:
    return etree.QName(el).localname


def _first(root: etree._Element, expr: str) -> Optional[etree._Element]:
    return root.find(expr)


def _body_shape(xml_bytes: bytes) -> CaptureBodyShapeView:
    tree = etree.fromstring(xml_bytes)
    body = _first(tree, ".//{*}body")
    if body is None:
        return CaptureBodyShapeView()

    intro_parts: list[str] = []
    if body.text and body.text.strip():
        intro_parts.append(body.text)
    for child in body:
        lname = _local_name(child)
        if lname in {"part", "chapter", "section", "article"}:
            break
        intro_parts.append(" ".join(str(_t) for _t in child.itertext()))
        if child.tail and child.tail.strip():
            intro_parts.append(child.tail)

    def _num_texts(tag: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for el in cast(list, body.xpath(f'.//*[local-name()="{tag}"]')):
            num = _first(el, "{*}num")
            txt = _collapse_ws(" ".join(str(_t) for _t in num.itertext()) if num is not None else "")
            if txt and txt not in seen:
                seen.add(txt)
                out.append(txt)
        return out

    chapters = _num_texts("chapter")
    sections = _num_texts("section")
    parts = _num_texts("part")
    return CaptureBodyShapeView(
        body_intro_excerpt=_excerpt(" ".join(intro_parts)),
        parts=tuple(parts),
        chapters=tuple(chapters),
        sections=tuple(sections),
        part_count=len(parts),
        chapter_count=len(chapters),
        section_count=len(sections),
    )


def _irnode_summary(node: Optional[IRNode]) -> Optional[dict[str, Any]]:
    if node is None:
        return None
    text = node.text or ""
    if not text:
        for child in node.children[:3]:
            if child.text:
                text += " " + child.text
    return {
        "kind": node.kind,
        "label": node.label,
        "text_excerpt": _excerpt(text),
        "child_count": len(node.children),
    }


def _serialize_source(source: Any) -> Optional[dict[str, Any]]:
    if source is None:
        return None
    return {
        "statute_id": source.statute_id,
        "title": source.title,
        "enacted": source.enacted,
        "effective": source.effective,
        "expires": source.expires,
        "corrected_by": source.corrected_by,
        "commencement_source": source.commencement_source,
        "commencement_title": source.commencement_title,
    }


def _serialize_legal_op(op: LegalOperation) -> dict[str, Any]:
    patch = op.text_patch
    return {
        "op_id": op.op_id,
        "sequence": op.sequence,
        "action": op.action,
        "target": str(op.target),
        "anchor": str(op.anchor) if op.anchor else None,
        "destination": str(op.destination) if op.destination else None,
        "group_id": op.group_id,
        "payload": _irnode_summary(op.payload),
        "text_patch": _serialize_text_patch(patch),
        "provenance_tags": list(op.provenance_tags),
        "source": _serialize_source(op.source),
    }


def _serialize_text_patch(patch: TextPatchSpec | None) -> dict[str, Any] | None:
    if patch is None:
        return None
    return {
        "kind": patch.kind.value,
        "selector": {
            "match_text": patch.selector.match_text,
            "occurrence": patch.selector.occurrence,
        },
        "replacement": patch.replacement,
    }


def _serialize_failure(failure: FailedOp) -> dict[str, Any]:
    scope = failure.scope_detail()
    return {
        "source_statute": getattr(failure, "source_statute", getattr(failure, "amendment_id", "")),
        "description": failure.description,
        "reason": failure.reason,
        "target_unit_kind": failure.target_unit_kind,
        "target_section": scope.get("target_section"),
        "target_chapter": scope.get("target_chapter"),
    }


def _iter_matching_ops(
    items: Iterable[Any],
    source_statute: str,
    *,
    source_attr: str = "source_statute",
) -> list[Any]:
    out: list[Any] = []
    for item in items:
        if getattr(item, source_attr, "") == source_statute:
            out.append(item)
    return out


def _iter_matching_dicts(
    items: Iterable[Any],
    source_statute: str,
    *,
    source_key: str = "source_statute",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get(source_key) or "") == source_statute:
            out.append(dict(item))
    return out


def _effective_source_adjudication(
    *,
    statute_id: str,
    replay_mode: str,
    replay_result: object | None,
    replay_meta: dict[str, Any],
) -> SourceAdjudication | None:
    typed = getattr(replay_result, "source_adjudication", None)
    if typed is not None:
        return cast(SourceAdjudication, typed)

    raw_lineage = replay_meta.get("lineage")
    lineage: tuple[dict[str, Any], ...] = ()
    if isinstance(raw_lineage, (list, tuple)):
        lineage = cast(
            tuple[dict[str, Any], ...],
            tuple(row for row in raw_lineage if isinstance(row, dict)),
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
        statute_id=statute_id,
        replay_mode=replay_mode,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id,
        oracle_suspect=oracle_suspect,
        html_noncommensurable_reason=html_noncommensurable_reason,
        lineage=lineage,
    )


def _bundle_for_amendment(
    *,
    compiled_ops: list[dict[str, Any]],
    canonical_ops: list[LegalOperation],
    failed_ops: list[FailedOp],
    projection_rows: list[dict[str, Any]],
    source_pathologies: list[dict[str, Any]],
    apply_mutation_invariant_reports: list[dict[str, Any]],
    lineage_row: dict[str, Any],
    source_xml: bytes | None,
) -> CaptureAmendmentView:
    mid = str(lineage_row.get("statute_id", ""))
    compiled_ops_for_source = [op for op in compiled_ops if op.get("source_statute") == mid]
    canonical_ops_for_source = [
        _serialize_legal_op(op)
        for op in canonical_ops
        if op.source is not None and op.source.statute_id == mid
    ]
    failed_ops_for_source = [
        _serialize_failure(f)
        for f in _iter_matching_ops(failed_ops, mid)
    ]
    projection_rows_for_source = [
        dict(row)
        for row in projection_rows
        if str(row.get("source") or "") == mid
    ]
    source_pathologies_for_source = _iter_matching_dicts(
        source_pathologies,
        mid,
        source_key="source_statute",
    )
    apply_mutation_invariant_reports_for_source = _iter_matching_dicts(
        apply_mutation_invariant_reports,
        mid,
        source_key="source_statute",
    )
    apply_mutation_invariant_result_code_counts: dict[str, int] = {}
    for item in apply_mutation_invariant_reports_for_source:
        results = item.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            code = str(result.get("code") or "").strip()
            if not code:
                continue
            apply_mutation_invariant_result_code_counts[code] = (
                apply_mutation_invariant_result_code_counts.get(code, 0) + 1
            )
    body_shape = _body_shape(source_xml) if source_xml is not None else None
    return CaptureAmendmentView(
        statute_id=mid,
        title=str(lineage_row.get("title", "")),
        issue_date=str(lineage_row.get("issue_date", "")),
        effective_date=str(lineage_row.get("effective_date", "")),
        included=bool(lineage_row.get("included")),
        source_available=source_xml is not None,
        body_shape=body_shape,
        counts=CaptureCountsView(
            compiled_ops=len(compiled_ops_for_source),
            canonical_ops=len(canonical_ops_for_source),
            failed_ops=len(failed_ops_for_source),
            projection_rows=len(projection_rows_for_source),
        ),
        compiled_ops=tuple(compiled_ops_for_source),
        canonical_ops=tuple(canonical_ops_for_source),
        failed_ops=tuple(failed_ops_for_source),
        projection_rows=tuple(projection_rows_for_source),
        source_pathologies=tuple(source_pathologies_for_source),
        apply_mutation_invariant_reports=tuple(apply_mutation_invariant_reports_for_source),
        apply_mutation_invariant_result_code_counts=apply_mutation_invariant_result_code_counts,
    )


def build_capture(
    statute_id: str,
    *,
    replay_mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle",
    source_filter: str = "",
) -> CapturePayload:
    from lawvm.finland.grafter import get_corpus, replay_xml

    with redirect_stdout(io.StringIO()):
        compiled_ops: list[dict[str, Any]] = []
        replay_meta: dict[str, Any] = {}
        canonical_ops: list[LegalOperation] = []
        failed_ops: list[FailedOp] = []
        master = replay_xml(
            statute_id,
            mode=replay_mode,
            compiled_ops_out=compiled_ops,
            replay_meta_out=replay_meta,
            lo_ops_out=canonical_ops,
            failed_ops_out=failed_ops,
        )
    projection_rows = [dict(row) for row in master.projection_rows()]
    cs = get_corpus()
    source_adjudication = _effective_source_adjudication(
        statute_id=statute_id,
        replay_mode=replay_mode,
        replay_result=master,
        replay_meta=replay_meta,
    )
    lineage = list(source_adjudication.lineage if source_adjudication is not None else ())
    if source_filter:
        lineage = [row for row in lineage if str(row.get("statute_id", "")) == source_filter]

    source_completeness = CaptureSourceCompletenessView(
        chain_length=len(lineage),
        source_available=sum(1 for r in lineage if r.get("included")),
        dates_available=sum(1 for r in lineage if r.get("effective_date")),
    )
    source_pathology_rows = getattr(master, "source_pathology_rows", None)
    if not callable(source_pathology_rows):
        raise TypeError("replay result must expose source_pathology_rows()")
    source_pathology_views: list[CaptureSourcePathologyView] = []
    for p in source_pathology_rows():
        if not isinstance(p, dict):
            continue
        source_target_unit_kind = str(p.get("target_unit_kind") or "")
        source_pathology_views.append(
            CaptureSourcePathologyView(
                code=str(p.get("code") or ""),
                message=str(p.get("message") or ""),
                source_statute=str(p.get("source_statute") or ""),
                target_unit_kind=source_target_unit_kind,
                target_label=str(p.get("target_label") or ""),
                detail=dict(p.get("detail") or {}) if isinstance(p.get("detail"), dict) else {},
            )
        )
    source_pathologies = tuple(source_pathology_views)

    top_level_projection_rows = [
        dict(row)
        for row in projection_rows
        if not str(row.get("source") or "")
    ]
    elaboration_observations = list(replay_meta.get("elaboration_observations", []))
    sparse_slot_bindings = list(replay_meta.get("sparse_slot_bindings", []))
    sparse_leftovers = list(replay_meta.get("sparse_leftovers", []))
    apply_mutation_events = list(replay_meta.get("apply_mutation_events", []))
    apply_mutation_invariant_reports = list(replay_meta.get("apply_mutation_invariant_reports", []))
    apply_mutation_invariant_result_code_counts: dict[str, int] = {}
    payload_completeness_rows = [
        item for item in elaboration_observations
        if str(item.get("kind") or "") == "ELAB.PAYLOAD_COMPLETENESS"
    ]
    payload_completeness_kind_counts: dict[str, int] = {}
    payload_completeness_tail_policy_counts: dict[str, int] = {}
    for item in payload_completeness_rows:
        kind = str(
            item.get("payload_completeness_kind")
            or (item.get("detail") or {}).get("payload_completeness_kind")
            or ""
        )
        if kind:
            payload_completeness_kind_counts[kind] = payload_completeness_kind_counts.get(kind, 0) + 1
        tail_policy = str(
            item.get("tail_policy")
            or (item.get("detail") or {}).get("tail_policy")
            or ""
        )
        if tail_policy:
            payload_completeness_tail_policy_counts[tail_policy] = (
                payload_completeness_tail_policy_counts.get(tail_policy, 0) + 1
            )
    for item in apply_mutation_invariant_reports:
        if not isinstance(item, dict):
            continue
        for result in item.get("results", []):
            if not isinstance(result, dict):
                continue
            code = str(result.get("code") or "")
            if code:
                apply_mutation_invariant_result_code_counts[code] = (
                    apply_mutation_invariant_result_code_counts.get(code, 0) + 1
                )
    amendments: list[CaptureAmendmentView] = []
    for row in lineage:
        mid = str(row.get("statute_id", ""))
        source_xml = cs.read_source(mid)
        amendments.append(
            _bundle_for_amendment(
                compiled_ops=compiled_ops,
                canonical_ops=canonical_ops,
                failed_ops=failed_ops,
                projection_rows=projection_rows,
                source_pathologies=[item.to_dict() for item in source_pathologies],
                apply_mutation_invariant_reports=[
                    dict(item) for item in apply_mutation_invariant_reports if isinstance(item, dict)
                ],
                lineage_row=dict(row),
                source_xml=source_xml,
            )
        )
    return CapturePayload(
        statute_id=statute_id,
        replay_mode=replay_mode,
        compile_mode="strict",
        profile="finland_ingestion_v1",
        source_completeness=source_completeness,
        source_adjudication=(
            CaptureSourceAdjudicationView(
                statute_id=source_adjudication.statute_id,
                replay_mode=source_adjudication.replay_mode,
                cutoff_date=source_adjudication.cutoff_date,
                oracle_version_amendment_id=source_adjudication.oracle_version_amendment_id,
                oracle_suspect=source_adjudication.oracle_suspect,
                html_noncommensurable_reason=str(
                    source_adjudication.html_noncommensurable_reason or ""
                ),
                source_pathologies=source_pathologies,
            )
            if source_adjudication is not None
            else None
        ),
        replay_meta=CaptureReplayMetaView(
            cutoff_date=str(replay_meta.get("cutoff_date", "")),
            oracle_version_amendment_id=str(replay_meta.get("oracle_version_amendment_id", "")),
            oracle_suspect=str(replay_meta.get("oracle_suspect", "")),
            elaboration_observations_count=len(elaboration_observations),
            payload_completeness_count=len(payload_completeness_rows),
            payload_completeness_kind_counts=payload_completeness_kind_counts,
            payload_completeness_tail_policy_counts=payload_completeness_tail_policy_counts,
            sparse_slot_bindings_count=len(sparse_slot_bindings),
            sparse_leftovers_count=len(sparse_leftovers),
            apply_mutation_events_count=len(apply_mutation_events),
            apply_mutation_invariant_reports_count=len(apply_mutation_invariant_reports),
            apply_mutation_invariant_result_code_counts=apply_mutation_invariant_result_code_counts,
            elaboration_observations=tuple(
                item for item in elaboration_observations if isinstance(item, dict)
            ),
            sparse_slot_bindings=tuple(item for item in sparse_slot_bindings if isinstance(item, dict)),
            sparse_leftovers=tuple(
                item for item in sparse_leftovers if isinstance(item, dict)
            ),
            apply_mutation_events=tuple(item for item in apply_mutation_events if isinstance(item, dict)),
            apply_mutation_invariant_reports=tuple(
                item for item in apply_mutation_invariant_reports if isinstance(item, dict)
            ),
        ),
        counts=CaptureCountsView(
            compiled_ops=len(compiled_ops),
            canonical_ops=len(canonical_ops),
            failed_ops=len(failed_ops),
            projection_rows=len(projection_rows),
            amendments=len(amendments),
        ),
        top_level_projection_rows=tuple(top_level_projection_rows),
        amendments=tuple(amendments),
    )


def main(args: Any) -> None:
    payload = build_capture(
        args.statute_id,
        replay_mode=args.mode,
        source_filter=getattr(args, "source", "") or "",
    )
    text = json.dumps(payload.to_dict(), indent=2, ensure_ascii=False)
    output = getattr(args, "output", "") or ""
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
