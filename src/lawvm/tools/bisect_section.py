from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Literal, Optional

from lawvm.finland.grafter import (
    _resolve_applicable_amendment_records,
    get_corpus,
    get_ground_truth_tree,
    process_muutoslaki,
)
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.finland.statute import ReplayState, StatuteContext
from lawvm.tools._section_debug import render_node_text, resolve_section_key, score_text_pair
from lawvm.tools.section_keys import extract_ir_sections, extract_oracle_sections


def _section_text_from_state(state: ReplayState, target_key: str) -> str:
    sections = extract_ir_sections(state.ir)
    try:
        actual_key = resolve_section_key(sections, target_key)
    except ValueError:
        return ""
    return render_node_text(sections.get(actual_key))


def build_bisect_bundle(
    statute_id: str,
    section: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    threshold: float,
    top: int,
    *,
    oracle_root: Optional[Any] = None,
    corpus: Optional[Any] = None,
) -> Dict[str, Any]:
    if oracle_root is None:
        oracle_root = get_ground_truth_tree(statute_id)
    if oracle_root is None:
        raise SystemExit(f"no oracle for {statute_id}")
    oracle_sections = extract_oracle_sections(oracle_root)
    target_key = resolve_section_key(oracle_sections, section)
    oracle_text = render_node_text(oracle_sections[target_key])

    cs = corpus if corpus is not None else get_corpus()
    xml_bytes = cs.read_source(statute_id)
    if xml_bytes is None:
        raise SystemExit(f"statute not found in corpus: {statute_id}")

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    state = ReplayState(ir=ctx.base_ir)
    records, _, _ = _resolve_applicable_amendment_records(statute_id, mode)

    baseline_text = _section_text_from_state(state, target_key)
    baseline_score = score_text_pair(baseline_text, oracle_text)
    prev_score = baseline_score
    steps: List[Dict[str, Any]] = []

    for index, record in enumerate(records, start=1):
        source_id = str(record["statute_id"])
        state = process_muutoslaki(source_id, state, ctx, replay_mode=mode, parent_id=statute_id).output
        current_text = _section_text_from_state(state, target_key)
        current_score = score_text_pair(current_text, oracle_text)
        steps.append(
            {
                "index": index,
                "source_id": source_id,
                "score_before": prev_score,
                "score_after": current_score,
                "delta": current_score - prev_score,
                "changed": current_text != baseline_text if index == 1 else current_text != steps[-1]["text_after"],
                "text_after": current_text,
            }
        )
        prev_score = current_score

    first_bad = next((step for step in steps if step["score_after"] < threshold), None)
    first_drop = next((step for step in steps if step["delta"] < 0), None)
    worst_drops = sorted((step for step in steps if step["delta"] < 0), key=lambda step: step["delta"])[:top]
    return {
        "statute_id": statute_id,
        "mode": mode,
        "section": target_key,
        "oracle_text": oracle_text,
        "baseline_score": baseline_score,
        "threshold": threshold,
        "first_bad_source": first_bad["source_id"] if first_bad else "",
        "first_drop_source": first_drop["source_id"] if first_drop else "",
        "worst_drops": [
            {
                "index": step["index"],
                "source_id": step["source_id"],
                "score_before": step["score_before"],
                "score_after": step["score_after"],
                "delta": step["delta"],
            }
            for step in worst_drops
        ],
        "steps": [
            {
                "index": step["index"],
                "source_id": step["source_id"],
                "score_before": step["score_before"],
                "score_after": step["score_after"],
                "delta": step["delta"],
            }
            for step in steps
        ],
    }


def build_bisect_bundles_batch(
    statute_id: str,
    sections: List[str],
    mode: Literal["finlex_oracle", "legal_pit"],
    threshold: float,
    top: int,
    *,
    oracle_root: Optional[Any] = None,
    corpus: Optional[Any] = None,
) -> Dict[str, Dict[str, Any]]:
    """Bisect multiple sections with a single replay pass.

    Returns {section_key: bisect_bundle} for each requested section.
    O(A) instead of O(S × A) where S=sections, A=amendments.
    """
    if not sections:
        return {}
    if oracle_root is None:
        oracle_root = get_ground_truth_tree(statute_id)
    if oracle_root is None:
        raise SystemExit(f"no oracle for {statute_id}")
    oracle_sections = extract_oracle_sections(oracle_root)

    # Resolve all target keys and oracle texts upfront
    targets: Dict[str, str] = {}  # key -> oracle_text
    for section in sections:
        try:
            target_key = resolve_section_key(oracle_sections, section)
        except (ValueError, KeyError):
            continue
        targets[target_key] = render_node_text(oracle_sections[target_key])

    if not targets:
        return {}

    cs = corpus if corpus is not None else get_corpus()
    xml_bytes = cs.read_source(statute_id)
    if xml_bytes is None:
        raise SystemExit(f"statute not found in corpus: {statute_id}")

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    state = ReplayState(ir=ctx.base_ir)
    records, _, _ = _resolve_applicable_amendment_records(statute_id, mode)

    def _section_text_from_ir_sections(ir_sections: Dict[str, Any], target_key: str) -> str:
        """Look up section text from a pre-extracted IR sections dict.

        Uses direct key lookup (O(1)) instead of calling extract_ir_sections
        and resolve_section_key on every invocation.  Callers must pass the
        already-resolved target key (as stored in ``targets``).
        """
        node = ir_sections.get(target_key)
        return render_node_text(node) if node is not None else ""

    # Initialize per-section tracking — extract IR sections once for baseline
    baseline_ir_sections = extract_ir_sections(state.ir)
    per_section: Dict[str, Dict[str, Any]] = {}
    for key, oracle_text in targets.items():
        baseline_text = _section_text_from_ir_sections(baseline_ir_sections, key)
        baseline_score = score_text_pair(baseline_text, oracle_text)
        per_section[key] = {
            "oracle_text": oracle_text,
            "baseline_score": baseline_score,
            "prev_score": baseline_score,
            "prev_text": baseline_text,
            "steps": [],
        }

    # Single replay pass — extract IR sections once per amendment, score all sections
    for index, record in enumerate(records, start=1):
        source_id = str(record["statute_id"])
        state = process_muutoslaki(source_id, state, ctx, replay_mode=mode, parent_id=statute_id).output
        # Extract once per amendment step instead of once per (amendment, section) pair.
        # This avoids O(S × A) calls to extract_ir_sections (which traverses the full IR
        # tree each time) — critical for statutes with many sections and many amendments.
        step_ir_sections = extract_ir_sections(state.ir)
        for key, tracker in per_section.items():
            current_text = _section_text_from_ir_sections(step_ir_sections, key)
            current_score = score_text_pair(current_text, tracker["oracle_text"])
            tracker["steps"].append({
                "index": index,
                "source_id": source_id,
                "score_before": tracker["prev_score"],
                "score_after": current_score,
                "delta": current_score - tracker["prev_score"],
                "changed": current_text != tracker["prev_text"],
            })
            tracker["prev_score"] = current_score
            tracker["prev_text"] = current_text

    # Build output bundles
    result: Dict[str, Dict[str, Any]] = {}
    for key, tracker in per_section.items():
        steps = tracker["steps"]
        first_bad = next((s for s in steps if s["score_after"] < threshold), None)
        first_drop = next((s for s in steps if s["delta"] < 0), None)
        worst_drops = sorted((s for s in steps if s["delta"] < 0), key=lambda s: s["delta"])[:top]
        result[key] = {
            "statute_id": statute_id,
            "mode": mode,
            "section": key,
            "oracle_text": tracker["oracle_text"],
            "baseline_score": tracker["baseline_score"],
            "threshold": threshold,
            "first_bad_source": first_bad["source_id"] if first_bad else "",
            "first_drop_source": first_drop["source_id"] if first_drop else "",
            "worst_drops": [
                {
                    "index": s["index"],
                    "source_id": s["source_id"],
                    "score_before": s["score_before"],
                    "score_after": s["score_after"],
                    "delta": s["delta"],
                }
                for s in worst_drops
            ],
            "steps": [
                {
                    "index": s["index"],
                    "source_id": s["source_id"],
                    "score_before": s["score_before"],
                    "score_after": s["score_after"],
                    "delta": s["delta"],
                }
                for s in steps
            ],
        }
    return result


def _format_text(bundle: Dict[str, Any], verbose: bool) -> str:
    lines = [
        f"Statute       : {bundle['statute_id']}",
        f"Mode          : {bundle['mode']}",
        f"Section       : {bundle['section']}",
        f"Baseline score: {bundle['baseline_score']:.1%}",
        f"Threshold     : {bundle['threshold']:.1%}",
        f"First drop    : {bundle.get('first_drop_source') or '(none)'}",
        f"First bad     : {bundle['first_bad_source'] or '(none)'}",
        "",
    ]
    if bundle.get("worst_drops"):
        lines.append("Worst drops:")
        for step in bundle["worst_drops"]:
            lines.append(
                f"  [{step['index']}] {step['source_id']}: "
                f"{step['score_before']:.1%} -> {step['score_after']:.1%} ({step['delta']:+.1%})"
            )
        lines.append("")
    if verbose:
        lines.append("All steps:")
        for step in bundle["steps"]:
            lines.append(
                f"  [{step['index']}] {step['source_id']}: "
                f"{step['score_before']:.1%} -> {step['score_after']:.1%} ({step['delta']:+.1%})"
            )
    return "\n".join(lines).rstrip()


def main(args) -> None:
    try:
        _mode: Literal["finlex_oracle", "legal_pit"] = getattr(args, "mode", "legal_pit")
        bundle = build_bisect_bundle(
            statute_id=args.statute_id,
            section=args.section,
            mode=_mode,
            threshold=getattr(args, "threshold", 0.9999),
            top=getattr(args, "top", 5),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    print(_format_text(bundle, verbose=getattr(args, "verbose", False)))
