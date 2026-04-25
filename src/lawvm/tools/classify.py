from __future__ import annotations

import json
import sys
from collections import defaultdict
from typing import Any

from lawvm.tools.oracle_check import _classify_statute


def _format_text(result: Any) -> str:
    if result.error:
        return f"{result.sid}: ERROR {result.error}"

    lines = [
        f"Statute      : {result.sid}",
        f"Mode         : {result.mode}",
        f"Overall      : {result.overall_score:.1%}",
        f"Section score: {result.section_score:.1%}",
    ]
    if result.source_pathologies:
        codes = ", ".join(
            sorted({entry["code"] for entry in result.source_pathologies if entry.get("code")})
        )
        lines.append(f"Pathologies  : {codes}")
    if result.contingent_effective_sources:
        lines.append(
            "Contingent   : "
            + ", ".join(result.contingent_effective_sources)
        )

    sections = result.section_results
    if not sections:
        lines.append("Divergences  : none")
        return "\n".join(lines)

    counts = defaultdict(int)
    for section in sections:
        counts[section["diagnosis"]] += 1
    lines.append(
        "Counts       : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )
    lines.append("")
    lines.append("Sections:")
    for section in sections:
        blame = f"  [{section['blame_source']}]" if section.get("blame_source") else ""
        lines.append(
            f"  {section['section']}: {section['diagnosis']}{blame}"
        )
    return "\n".join(lines)


def main(args) -> None:
    result = _classify_statute(args.statute_id, getattr(args, "mode", "finlex_oracle"))
    if result is None:
        print("classification failed", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    print(_format_text(result))
