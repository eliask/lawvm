"""lawvm audit-trail — per-amendment decision chain for one statute.

Shows the pipeline decisions made for each amendment in human-readable
format: johtolause text, citation routing outcome, PEG extraction
result, and body content summary.

Requires captures in .cache/pipeline_gold.db.  If none are found,
prints a clear message instructing the user to run capture_gold.py.
"""
from __future__ import annotations

import sys
from typing import Any

from lawvm.core.pipeline_capture import AmendmentCapture, CaptureStore


def _format_ops(ops: list[dict]) -> list[str]:
    """Format a list of op dicts as indented lines."""
    lines = []
    for op in ops:
        action = op.get("action", op.get("op_type", "?")).upper()
        target = op.get("target", op.get("target_ref", "?"))
        lines.append(f"      {action:<8s} {target}")
    return lines


def _extraction_summary(cap: AmendmentCapture) -> str:
    """One-line summary of the extraction path and op count."""
    path = cap.extraction_path or "unknown"
    # Normalise display name
    display = {
        "peg": "PEG",
        "fallback_heuristic": "fallback/heuristic",
        "title_fallback": "title-fallback",
        "sec1": "sec1-fallback",
    }.get(path, path)
    n = len(cap.peg_ops)
    return f"{display} \u2192 {n} op{'s' if n != 1 else ''}"


def _body_summary(cap: AmendmentCapture) -> str:
    """Compact summary of body sections and their omission status."""
    if not cap.body_section_labels:
        return "(no body sections)"
    parts = []
    for label in cap.body_section_labels:
        has_omission = cap.body_has_omissions.get(label, False)
        suffix = "omission" if has_omission else "no omission"
        parts.append(f"\u00a7{label} ({suffix})")
    return ", ".join(parts)


def _citation_label(cap: AmendmentCapture) -> str:
    action = cap.citation_action
    if action == "pass" or action == "":
        return "PASS"
    if action == "skip_num_collision":
        return "SKIP  [num-collision]"
    if action == "skip_citation_mismatch":
        return "SKIP  [citation mismatch]"
    return action.upper()


def _truncate(text: str, max_len: int = 90) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "\u2026"


def _format_capture(index: int, cap: AmendmentCapture) -> str:
    lines: list[str] = []

    eff = cap.effective_date or "unknown"
    lines.append(f"[{index}] {cap.amendment_id}  (eff: {eff})")

    johtolause = cap.preamble_normalized or cap.preamble_raw or "(none)"
    lines.append(f"    Johtolause: \"{_truncate(johtolause)}\"")

    lines.append(f"    Citation:   {_citation_label(cap)}")

    lines.append(f"    Extraction: {_extraction_summary(cap)}")
    for op_line in _format_ops(cap.peg_ops):
        lines.append(op_line)

    lines.append(f"    Body: {_body_summary(cap)}")

    return "\n".join(lines)


def _format_trail(statute_id: str, captures: list[AmendmentCapture]) -> str:
    lines: list[str] = []
    lines.append(f"Statute: {statute_id}")
    lines.append(f"Amendments: {len(captures)}")
    for i, cap in enumerate(captures, 1):
        lines.append("")
        lines.append(_format_capture(i, cap))
    return "\n".join(lines)


def main(args: Any) -> None:
    statute_id: str = args.statute_id
    db_path: str = getattr(args, "db", None) or ".cache/pipeline_gold.db"

    store = CaptureStore(db_path=db_path)
    captures = store.load(statute_id)

    if not captures:
        print(
            f"No captures found for {statute_id} in {db_path}.\n"
            "Run capture_gold.py first to populate the capture store:\n"
            "  uv run python scripts/capture_gold.py <statute_id>",
            file=sys.stderr,
        )
        sys.exit(1)

    print(_format_trail(statute_id, captures))
