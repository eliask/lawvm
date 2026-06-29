"""Reclassify ``byte_anchor_absent`` verdicts to ``already_applied_in_source``
where the evidence detail indicates the corrigendum's correct_text is already
present in source XML (acquired post-corrigendum consolidation).

Scans ``corrigendum_unresolvable_fi.yaml`` for records where:
  evidence.kind == "byte_anchor_absent"
  AND evidence.detail (case-insensitive) contains "already applied" or
    "already_applied_in_source"

Re-writes those records with evidence.kind = "already_applied_in_source".
Retired records are preserved in the audit log.
"""
import json
import re
import sys
from pathlib import Path

import yaml

UNRESOLVABLE_PATH = Path("data/finland/corrigendum_unresolvable_fi.yaml")

_ALREADY_APPLIED_PATTERNS = [
    r"already[_ ]applied",
    r"correct_text.*(?:present|in source|in.*xml)",
    r"source.*already.*correct",
    r"corrigendum.*effect.*already",
    r"post-corrigendum",
    r"post_corrigendum",
    r"already.*corrected",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _ALREADY_APPLIED_PATTERNS]


def _is_already_applied(detail: str) -> bool:
    return any(p.search(detail) for p in _COMPILED)


def main() -> int:
    raw = yaml.safe_load(UNRESOLVABLE_PATH.read_text(encoding="utf-8"))
    if raw is None:
        print("empty file", file=sys.stderr)
        return 0
    if not isinstance(raw, list):
        print(f"unexpected type {type(raw)!r}", file=sys.stderr)
        return 2

    reclassified = 0
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        evidence = rec.get("evidence") or {}
        kind = str(evidence.get("kind") or "")
        detail = str(evidence.get("detail") or "")
        if kind == "byte_anchor_absent" and _is_already_applied(detail):
            evidence["kind"] = "already_applied_in_source"
            rec["rule_id"] = rec.get("rule_id", "").replace(
                "UNRESOLVABLE.BYTE_ANCHOR_ABSENT",
                "UNRESOLVABLE.ALREADY_APPLIED_IN_SOURCE",
            )
            reclassified += 1

    UNRESOLVABLE_PATH.write_text(
        "# Auto-managed by scripts/tribunal_adjudicate.py + migrations\n\n"
        + yaml.safe_dump(
            raw,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=160,
        ),
        encoding="utf-8",
    )
    print(f"reclassified {reclassified} records from byte_anchor_absent → already_applied_in_source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())