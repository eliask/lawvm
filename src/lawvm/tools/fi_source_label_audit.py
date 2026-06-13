"""CLI for non-mutating Finland source XML label policy audit."""

from __future__ import annotations

import json
from typing import Any

from lawvm.corpus_store import get_corpus_store
from lawvm.finland.source_xml_label_policy_audit import (
    audit_source_xml_label_policies,
    summarize_label_policy_rows,
)


def main(args: Any) -> None:
    corpus = get_corpus_store(readonly=True)
    try:
        statute_ids = _selected_statute_ids(args, corpus.list_statute_ids())
        rows = []
        missing: list[str] = []
        for sid in statute_ids:
            xml_bytes = corpus.read_source(sid)
            if xml_bytes is None:
                missing.append(sid)
                continue
            rows.extend(
                audit_source_xml_label_policies(
                    sid,
                    xml_bytes,
                    include_agreeing=bool(getattr(args, "include_agreeing", False)),
                )
            )
    finally:
        corpus.close()

    summary = summarize_label_policy_rows(rows)
    payload = {
        "kind": "lawvm.fi.source_xml_label_policy_audit.v1",
        "statutes_scanned": len(statute_ids),
        "missing_sources": missing,
        "summary": summary,
        "rows": [row.to_jsonable() for row in rows],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    print("FI source XML label policy audit")
    print(f"  statutes scanned: {payload['statutes_scanned']}")
    print(f"  missing sources:   {len(missing)}")
    print(f"  rows:              {summary['rows']}")
    print(f"  divergent rows:    {summary['divergent_rows']}")
    print(f"  by kind:           {summary['by_kind']}")
    print(f"  divergent by kind: {summary['divergent_by_kind']}")
    examples = summary.get("examples")
    if isinstance(examples, list) and examples:
        print("  examples:")
        for example in examples[: int(getattr(args, "examples", 10))]:
            policies = example.get("policies", {})
            print(
                "   - "
                f"{example.get('statute_id')}:{example.get('sourceline')} "
                f"{example.get('element_kind')} raw={example.get('raw_num')!r} "
                f"policies={policies}"
            )


def _selected_statute_ids(args: Any, all_ids: list[str]) -> list[str]:
    statute_id = str(getattr(args, "statute_id", "") or "").strip()
    if statute_id:
        return [statute_id]
    limit = int(getattr(args, "limit", 50) or 0)
    ids = sorted(all_ids)
    if limit > 0:
        return ids[:limit]
    return ids
