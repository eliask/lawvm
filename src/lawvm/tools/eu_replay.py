"""lawvm eu-replay -- EU CELEX replay with adjudication summary."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import argparse
    from lawvm.replay_adjudication import CompileAdjudication


class _AdjudicationLike(Protocol):
    kind: str
    message: str
    source_statute: str
    op_id: str
    detail: object


def _serialize_adjudication(adj: "CompileAdjudication | _AdjudicationLike") -> dict[str, Any]:
    detail = adj.detail
    if isinstance(detail, dict):
        detail_payload = dict(detail)
    elif detail is None:
        detail_payload = {}
    else:
        detail_payload = {"value": str(detail)}

    return {
        "kind": str(adj.kind),
        "message": str(adj.message),
        "source_statute": str(adj.source_statute),
        "op_id": str(adj.op_id),
        "detail": detail_payload,
    }


def _markdown_text(payload: dict[str, Any]) -> str:
    lines = [
        "# EU Replay Report",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| CELEX | {payload['celex']} |",
        f"| Ops | {payload['ops']} |",
        f"| Adjudications | {payload['adjudications']} |",
    ]

    timeline_count = payload.get("timelines")
    if timeline_count is not None:
        lines.append(f"| Timelines provisions | {timeline_count} |")

    if payload["text_duplication_phases"]:
        lines.append(f"| Text duplication phases | {', '.join(payload['text_duplication_phases'])} |")
    else:
        lines.append("| Text duplication phases | None |")

    lines.extend(["", "## Adjudication Kinds"])
    lines.extend(["", "| Kind | Count |", "| --- | ---: |"])
    for kind, count in payload["adjudication_kinds"].items():
        lines.append(f"| {kind} | {count} |")

    lines.extend(["", "## Adjudications"])
    lines.extend(
        [
            "| # | Kind | Source | Op | Detail |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for index, adjudication in enumerate(payload["adjudications_data"], start=1):
        detail = adjudication["detail"]
        detail_text = str(detail)
        lines.append(
            f"| {index} | {adjudication['kind']} | {adjudication['source_statute']} "
            f"| {adjudication['op_id']} | {detail_text} |"
        )

    return "\n".join(lines) + "\n"


def main(args: "argparse.Namespace") -> None:
    from lawvm.eu.pipeline import EUReplayPipeline

    celex: str = args.celex
    pit_date = getattr(args, "pit_date", None)
    output_format = str(getattr(args, "format", "text")).lower()
    if getattr(args, "json", False):
        output_format = "json"
    cache_dir = Path(getattr(args, "cache_dir", ".cache/eu_replay"))

    pipeline = EUReplayPipeline(cache_dir=cache_dir)
    result = pipeline.replay_statute(celex, cutoff_date=pit_date)
    if not result.replayed:
        raise RuntimeError("EU replay produced no replayed statute")

    adjudication_kinds = dict(sorted(Counter(str(adj.kind) for adj in result.adjudications).items()))
    text_duplication_phases = sorted(
        {
            str(adj.detail.get("phase"))
            for adj in result.adjudications
            if adj.kind == "text_duplication_warning"
            and isinstance(adj.detail, dict)
            and adj.detail.get("phase")
        }
    )
    payload = {
        "celex": result.celex,
        "ops": len(result.ops),
        "adjudications": len(result.adjudications),
        "adjudication_kinds": dict(adjudication_kinds),
        "text_duplication_phases": text_duplication_phases,
        "adjudications_data": [
            _serialize_adjudication(adj) for adj in result.adjudications
        ],
        "timelines": len(result.timelines) if result.timelines is not None else None,
    }

    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False))
        return
    elif output_format == "markdown":
        print(_markdown_text(payload))
        return

    print("EU Replay")
    print(f"CELEX: {payload['celex']}")
    print(f"Ops: {payload['ops']}")
    print(f"Adjudications: {payload['adjudications']}")
    if payload["timelines"] is not None:
        print(f"Timelines provisions: {payload['timelines']}")
    if payload["text_duplication_phases"]:
        print("Text duplication phases: " + ", ".join(payload["text_duplication_phases"]))
    if payload["adjudication_kinds"]:
        print("Kinds:")
        for kind in sorted(payload["adjudication_kinds"]):
            print(f"  {kind}: {payload['adjudication_kinds'][kind]}")
    if result.adjudications:
        print("Sample adjudications:")
        for idx, adj in enumerate(result.adjudications[:10], 1):
            line = (
                f"  [{idx}] {adj.kind}: source={adj.source_statute}"
                f" op={adj.op_id or '-'}"
            )
            detail = getattr(adj, "detail", None)
            if isinstance(detail, dict):
                phase = detail.get("phase")
                if phase:
                    line += f" phase={phase}"
            print(line)
