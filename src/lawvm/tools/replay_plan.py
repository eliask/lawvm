"""lawvm replay-plan -- inspect the prepared Finland replay plan."""

from __future__ import annotations

import json
from typing import Any

from lawvm.finland.corpus import get_corpus
from lawvm.finland.grafter import (
    _resolve_applicable_amendment_records,
    get_consolidated_oracle_suspect,
)
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.finland.replay_pipeline import prepare_replay_plan
from lawvm.finland.strict_profile import FINLAND_INGESTION_V1
from lawvm.finland.grafter import get_replay_profile
from lawvm.finland.corrigendum import extract_inline_corrections
from lawvm.finland.corpus import get_consolidated_oracle_inspection
from lawvm.tools.oracle_context import _selector_from_args


def _format_amendment_record(record: dict[str, Any]) -> str:
    statute_id = str(record.get("statute_id", ""))
    title = str(record.get("title", "")).strip()
    issue_date = str(record.get("issue_date", "")).strip()
    effective_date = str(record.get("effective_date", "")).strip()
    included = "yes" if record.get("included") else "no"
    parts = [
        f"  - {statute_id or '(unknown)'}",
        f"included={included}",
    ]
    if issue_date:
        parts.append(f"issue={issue_date}")
    if effective_date:
        parts.append(f"effective={effective_date}")
    if title:
        parts.append(f"title={title}")
    return " | ".join(parts)


def build_replay_plan_inspection(args: Any) -> dict[str, Any]:
    selector = _selector_from_args(args)
    corpus = get_corpus()
    oracle_inspection = get_consolidated_oracle_inspection(
        args.statute_id,
        corpus=corpus,
        selector=selector,
    )
    plan = prepare_replay_plan(
        args.statute_id,
        mode=getattr(args, "mode", "finlex_oracle"),
        strict_profile=FINLAND_INGESTION_V1 if getattr(args, "strict", False) else None,
        corpus=corpus,
        stop_before="",
        label_postprocessor=_fi_label_postprocessor,
        get_replay_profile=get_replay_profile,
        resolve_applicable_amendment_records=(
            lambda resolved_parent_id, resolved_mode, corpus=None: _resolve_applicable_amendment_records(
                resolved_parent_id,
                resolved_mode,
                corpus=corpus,
                selector=selector,
            )
        ),
        get_consolidated_oracle_suspect=(
            lambda resolved_parent_id, corpus=None: get_consolidated_oracle_suspect(
                resolved_parent_id,
                corpus=corpus,
                selector=selector,
            )
        ),
        extract_inline_corrections=extract_inline_corrections,
    )
    return {
        "statute_id": args.statute_id,
        "mode": getattr(args, "mode", "finlex_oracle"),
        "selector_mode": oracle_inspection.selector_mode,
        "oracle_context": {
            "locator": oracle_inspection.locator,
            "cutoff_date": oracle_inspection.cutoff_date.isoformat()
            if oracle_inspection.cutoff_date
            else "",
            "oracle_version_amendment_id": oracle_inspection.oracle_version_amendment_id,
        },
        "amendment_chain": list(plan.amendment_ids),
        "amendment_records": list(plan.amendment_records),
        "cutoff_date": plan.cutoff_date.isoformat() if plan.cutoff_date else "",
        "oracle_version_amendment_id": plan.oracle_version_amendment_id,
        "oracle_suspect": plan.oracle_suspect,
    }


def _format_text(bundle: dict[str, Any]) -> str:
    oracle_context = bundle.get("oracle_context") or {}
    amendment_chain = bundle.get("amendment_chain") or []
    records = bundle.get("amendment_records") or []
    lines = [
        f"Statute        : {bundle.get('statute_id') or '(none)'}",
        f"Mode           : {bundle.get('mode') or '(none)'}",
        f"Selector mode  : {bundle.get('selector_mode') or '(none)'}",
        f"Oracle locator : {oracle_context.get('locator') or '(none)'}",
        f"Oracle cutoff  : {oracle_context.get('cutoff_date') or '(none)'}",
        f"Oracle version : {oracle_context.get('oracle_version_amendment_id') or '(none)'}",
        f"Plan cutoff    : {bundle.get('cutoff_date') or '(none)'}",
        f"Plan version   : {bundle.get('oracle_version_amendment_id') or '(none)'}",
        f"Oracle suspect : {bundle.get('oracle_suspect') or '(none)'}",
        f"Chain length   : {len(amendment_chain)}",
        "",
        "Amendment chain:",
    ]
    if records:
        lines.extend(_format_amendment_record(record) for record in records)
    else:
        lines.append("  (empty)")
    return "\n".join(lines)


def main(args) -> None:
    bundle = build_replay_plan_inspection(args)
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    print(_format_text(bundle))


__all__ = ["build_replay_plan_inspection", "main"]
