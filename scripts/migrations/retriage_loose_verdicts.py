"""Identify loose unresolvable verdicts for re-triage.

Reads ``corrigendum_unresolvable_fi.yaml`` and groups amendments that have
``semantic_only``, ``ambiguous_anchor_unresolvable`` or
``byte_anchor_absent`` verdicts. Outputs a stable list per evidence kind
to drive re-triage subagents that should attempt retry-overlays with
multi-patch byte sequences or careful disambiguation rather than give
up — per AGENTS.md §0: never silently abandon a fixable surface.

Output: ``/tmp/retriage_loose_verdicts.json`` with keys:
  ``by_kind[<evidence_kind>]``: list of amendments that have ≥1 verdict of this kind
  ``records[<evidence_kind>][<amendment_id>]``: list of stable_ids (for re-triage)

Run:
    uv run python scripts/migrations/retriage_loose_verdicts.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

UNRESOLVABLE_PATH = Path("data/finland/corrigendum_unresolvable_fi.yaml")
OUT_PATH = Path("/tmp/retriage_loose_verdicts.json")

LOOSE_KINDS = (
    "semantic_only",
    "ambiguous_anchor_unresolvable",
    "byte_anchor_absent",  # many of these are "source already corrected" — need reclassification
)


def main() -> int:
    if not UNRESOLVABLE_PATH.exists():
        print(f"missing {UNRESOLVABLE_PATH}", file=sys.stderr)
        return 2
    raw = yaml.safe_load(UNRESOLVABLE_PATH.read_text(encoding="utf-8"))
    if raw is None:
        print("file is empty", file=sys.stderr)
        return 0
    if not isinstance(raw, list):
        print(f"unexpected YAML type {type(raw)!r}", file=sys.stderr)
        return 2

    by_kind: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for record in raw:
        if not isinstance(record, dict):
            continue
        evidence = record.get("evidence") or {}
        kind = str(evidence.get("kind") or "").strip()
        if kind not in LOOSE_KINDS:
            continue
        aid = str(record.get("amendment_id") or "").strip()
        sid = str(record.get("stable_id") or "").strip()
        if aid and sid:
            by_kind[kind][aid].append(sid)

    out = {
        "summary": {
            kind: sum(len(sids) for sids in aids.values())
            for kind, aids in by_kind.items()
        },
        "by_kind_amendments": {
            kind: sorted(aids.keys()) for kind, aids in by_kind.items()
        },
        "records": {
            kind: {aid: sids for aid, sids in sorted(aids.items())}
            for kind, aids in by_kind.items()
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"summary: {out['summary']}", file=sys.stderr)
    print(f"total amendments needing re-triage:", file=sys.stderr)
    for kind in LOOSE_KINDS:
        if kind in by_kind:
            print(f"  {kind}: {len(by_kind[kind])} amendments ({len(out['records'][kind])} records)", file=sys.stderr)
    print(f"wrote {OUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())