"""lawvm no-verify -- compare Norway replay against current consolidated law."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING, cast

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.core.adjudication_evidence import (
        adjudication_finding_evidence_rows,
        adjudication_kind_counts,
    )
    from lawvm.norway.verify import verify_no_against_current

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    commencement_arg = getattr(args, "commencement", None)
    commencement_path = Path(commencement_arg) if commencement_arg else None

    result = verify_no_against_current(
        getattr(args, "base_id"),
        as_of=getattr(args, "as_of"),
        data_dir=data_dir,
        index_path=index_path,
        commencement_path=commencement_path,
    )
    replay = result.replay
    adjudications = list(getattr(replay, "adjudications", []) or [])
    finding_rows = adjudication_finding_evidence_rows(
        adjudications,
        frontend_id="norway",
        base_id=result.base_id,
        as_of=result.as_of,
    )

    payload: dict[str, object] = {
        "base_id": result.base_id,
        "as_of": result.as_of,
        "current_title": result.current_title,
        "replay_status": result.replay_status,
        "consistent": result.consistent,
        "divergence_count": result.divergence_count,
        "divergence_counts": dict(result.divergence_counts or {}),
        "raw_divergence_count": result.raw_divergence_count,
        "raw_divergence_counts": dict(result.raw_divergence_counts or {}),
        "filtered_divergence_count": int(getattr(result, "filtered_divergence_count", 0) or 0),
        "filtered_divergence_rule_counts": dict(getattr(result, "filtered_divergence_rule_counts", None) or {}),
        "compare_projection_count": int(getattr(result, "compare_projection_count", 0) or 0),
        "compare_projection_rule_counts": dict(getattr(result, "compare_projection_rule_counts", None) or {}),
        "indexed_amendment_count": result.indexed_amendment_count,
        "applied_amendment_count": result.applied_amendment_count,
        "replay_op_count": result.replay_op_count,
        "source_signal": result.source_signal or "",
        "replay_adjudication_count": len(adjudications),
        "replay_adjudication_kind_counts": adjudication_kind_counts(adjudications),
        "replay_adjudications": [
            {
                "kind": adjudication.kind,
                "message": adjudication.message,
                "source_statute": adjudication.source_statute,
                "op_id": adjudication.op_id,
                "detail": dict(adjudication.detail),
            }
            for adjudication in adjudications
        ],
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
        },
        "error": result.error or "",
    }
    if getattr(args, "verbose", False) and result.divergences is not None:
        max_divergences = getattr(args, "max_divergences", None)
        divergences = result.divergences
        if isinstance(max_divergences, int) and max_divergences >= 0:
            divergences = divergences[:max_divergences]
        payload["divergences"] = [
            {
                "address": list(divergence.address.path),
                "divergence_type": divergence.divergence_type,
                "ops_text": divergence.ops_text,
                "consolidated_text": divergence.consolidated_text,
            }
            for divergence in divergences
        ]
        filtered_divergences = getattr(result, "filtered_divergences", None) or []
        if isinstance(max_divergences, int) and max_divergences >= 0:
            filtered_divergences = filtered_divergences[:max_divergences]
        payload["filtered_divergences"] = [
            {
                "rule_id": filtered.rule_id,
                "reason": filtered.reason,
                "address": list(filtered.divergence.address.path),
                "divergence_type": filtered.divergence.divergence_type,
                "ops_text": filtered.divergence.ops_text,
                "consolidated_text": filtered.divergence.consolidated_text,
            }
            for filtered in filtered_divergences
        ]
        compare_projections = getattr(result, "compare_projections", None) or []
        if isinstance(max_divergences, int) and max_divergences >= 0:
            compare_projections = compare_projections[:max_divergences]
        payload["compare_projections"] = [
            projection.to_dict() for projection in compare_projections if hasattr(projection, "to_dict")
        ]

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Verify ===")
    print(f"  base id         : {payload['base_id']}")
    print(f"  as of           : {payload['as_of']}")
    print(f"  replay status   : {payload['replay_status']}")
    if payload["current_title"]:
        print(f"  current title   : {payload['current_title']}")
    if payload["error"]:
        print(f"  error           : {payload['error']}")
        return
    print(f"  consistent      : {'yes' if payload['consistent'] else 'no'}")
    print(f"  divergence count: {payload['divergence_count']}")
    print(
        "  source coverage : "
        f"indexed={payload['indexed_amendment_count']} | "
        f"applied={payload['applied_amendment_count']} | "
        f"ops={payload['replay_op_count']}"
    )
    if payload["source_signal"]:
        print(f"  source signal   : {payload['source_signal']}")
    if payload["replay_adjudication_count"]:
        print(f"  adjudications   : {payload['replay_adjudication_count']}")
    if payload["divergence_counts"]:
        print(
            "  by type         : "
            + ", ".join(f"{k}={v}" for k, v in sorted(payload["divergence_counts"].items()))
        )
    if payload["raw_divergence_count"] != payload["divergence_count"]:
        print(
            "  raw divergences : "
            f"{payload['raw_divergence_count']}"
        )
    if payload["filtered_divergence_count"]:
        print(f"  filtered divs   : {payload['filtered_divergence_count']}")
    if payload["compare_projection_count"]:
        print(f"  projections     : {payload['compare_projection_count']}")
    if getattr(args, "verbose", False):
        for divergence in cast(list[dict[str, Any]], payload.get("divergences", [])):
            address = "/".join(f"{kind}:{label}" for kind, label in divergence["address"])
            print(f"    [{divergence['divergence_type']}] {address}")
