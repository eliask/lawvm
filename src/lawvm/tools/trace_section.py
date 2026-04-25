from __future__ import annotations

import json
import sys
from typing import Any, Dict, Literal, Optional

from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.corpus import get_consolidated_oracle_context
from lawvm.finland.grafter import (
    _resolve_applicable_amendment_records,
    get_ground_truth_tree,
    replay_xml,
)
from lawvm.tools._section_debug import (
    render_node_text,
    resolve_section_key,
    score_text_pair,
)
from lawvm.tools.section_keys import extract_ir_sections, extract_oracle_sections


def _resolve_side_section_key(section_filter: str, *candidate_sections: Dict[str, Any]) -> str:
    last_error: ValueError | None = None
    for sections in candidate_sections:
        if not sections:
            continue
        try:
            return resolve_section_key(sections, section_filter)
        except ValueError as exc:
            last_error = exc
            if ":" not in section_filter:
                continue
            leaf = section_filter.rsplit("/", 1)[-1]
            if leaf.startswith("section:"):
                leaf = leaf[len("section:") :]
            if not leaf:
                continue
            try:
                return resolve_section_key(sections, leaf)
            except ValueError as leaf_exc:
                last_error = leaf_exc
                continue
    if last_error is not None:
        raise last_error
    raise ValueError("no sections available")


def _next_amendment_id(statute_id: str, source_id: str, mode: Literal["finlex_oracle", "legal_pit"]) -> Optional[str]:
    records, _, _ = _resolve_applicable_amendment_records(statute_id, mode)
    ids = [str(record["statute_id"]) for record in records]
    try:
        idx = ids.index(source_id)
    except ValueError as exc:
        raise SystemExit(f"amendment not in replay chain: {source_id}") from exc
    if idx + 1 < len(ids):
        return ids[idx + 1]
    return None


def build_trace_bundle(
    statute_id: str,
    source_id: str,
    section: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    *,
    oracle_root: Optional[Any] = None,
) -> Dict[str, Any]:
    next_source = _next_amendment_id(statute_id, source_id, mode)
    before_master = replay_xml(statute_id, mode=mode, stop_before=source_id, quiet=True)
    after_master = replay_xml(statute_id, mode=mode, stop_before=next_source or "", quiet=True)

    before_sections = extract_ir_sections(before_master.materialized_state.ir)
    after_sections = extract_ir_sections(after_master.materialized_state.ir)

    if oracle_root is None:
        oracle_root = get_ground_truth_tree(statute_id)
    oracle_sections = extract_oracle_sections(oracle_root) if oracle_root is not None else {}

    oracle_ctx = get_consolidated_oracle_context(
        statute_id,
        selector=ConsolidatedArtifactSelector.latest_cached_editorial(),
    )

    replay_key = _resolve_side_section_key(section, before_sections, after_sections, oracle_sections)
    oracle_key = _resolve_side_section_key(section, oracle_sections, before_sections, after_sections)
    before_node = before_sections.get(replay_key)
    after_node = after_sections.get(replay_key)
    oracle_node = oracle_sections.get(oracle_key)

    before_text = render_node_text(before_node)
    after_text = render_node_text(after_node)
    oracle_text = render_node_text(oracle_node)

    return {
        "statute_id": statute_id,
        "source_id": source_id,
        "next_source_id": next_source or "",
        "mode": mode,
        "requested_section": section,
        "section": replay_key,
        "replay_path": replay_key,
        "oracle_path": oracle_key if oracle_node is not None else "",
        "oracle_context": {
            "locator": oracle_ctx.locator,
            "cutoff_date": oracle_ctx.cutoff_date.isoformat() if oracle_ctx.cutoff_date else "",
            "oracle_version_amendment_id": oracle_ctx.oracle_version_amendment_id,
        },
        "before_text": before_text,
        "after_text": after_text,
        "oracle_text": oracle_text,
        "before_vs_oracle": score_text_pair(before_text, oracle_text) if oracle_text else None,
        "after_vs_oracle": score_text_pair(after_text, oracle_text) if oracle_text else None,
        "changed": before_text != after_text,
    }


def _format_score(score: Optional[float]) -> str:
    if score is None:
        return "-"
    return f"{score:.1%}"


def _format_text(bundle: Dict[str, Any]) -> str:
    oracle_context = bundle.get("oracle_context") or {}
    return "\n".join(
        [
            f"Statute        : {bundle['statute_id']}",
            f"Amendment      : {bundle['source_id']}",
            f"Next amendment : {bundle.get('next_source_id') or '(none)'}",
            f"Mode           : {bundle['mode']}",
            f"Requested      : {bundle.get('requested_section') or '(none)'}",
            f"Replay path    : {bundle.get('replay_path') or '(none)'}",
            f"Oracle path    : {bundle.get('oracle_path') or '(none)'}",
            f"Oracle locator : {oracle_context.get('locator') or '(none)'}",
            f"Oracle cutoff  : {oracle_context.get('cutoff_date') or '(none)'}",
            f"Oracle version : {oracle_context.get('oracle_version_amendment_id') or '(none)'}",
            f"Changed        : {'yes' if bundle['changed'] else 'no'}",
            f"Before score   : {_format_score(bundle.get('before_vs_oracle'))}",
            f"After score    : {_format_score(bundle.get('after_vs_oracle'))}",
            "",
            "Before:",
            bundle.get("before_text", ""),
            "",
            "After:",
            bundle.get("after_text", ""),
            "",
            "Oracle:",
            bundle.get("oracle_text", ""),
        ]
    )


def main(args) -> None:
    try:
        bundle = build_trace_bundle(
            statute_id=args.statute_id,
            source_id=args.source,
            section=args.section,
            mode=getattr(args, "mode", "legal_pit"),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    print(_format_text(bundle))
