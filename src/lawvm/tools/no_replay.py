"""lawvm replay -j no -- Norway point-in-time replay from local Lovdata archives."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.replay import replay_no_to_pit
    from lawvm.core.ir_helpers import irnode_to_text
    from lawvm.norway.index import load_no_amendment_index
    from lawvm.tools.replay_payloads import build_no_replay_payload, replay_text_from_ir

    archive_arg = getattr(args, "archive", None)
    data_dir = Path(archive_arg) if archive_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    index = load_no_amendment_index(index_path) if index_path else None
    commencement_arg = getattr(args, "commencement", None)
    commencement_path = Path(commencement_arg) if commencement_arg else None
    result = replay_no_to_pit(
        base_id=args.base_id,
        as_of=args.as_of,
        data_dir=data_dir,
        index=index,
        index_path=index_path,
        commencement_path=commencement_path,
        verbose=getattr(args, "verbose", False),
    )
    staleness = index.staleness_report(data_dir) if index is not None else {"index_stale": False}
    replayed_text = None
    if getattr(args, "show_text", False) and result.replayed is not None:
        replayed_text = replay_text_from_ir(result.replayed.body, irnode_to_text=irnode_to_text)
    payload = build_no_replay_payload(
        result,
        archive_path=str(data_dir) if data_dir is not None else None,
        index_path=str(index_path) if index_path is not None else None,
        commencement_path=str(commencement_path) if commencement_path is not None else None,
        index_stale=bool(staleness.get("index_stale")),
        replayed_text=replayed_text,
    )

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if result.error:
            sys.exit(1)
        return

    print()
    print(f"=== NO PIT Replay: {result.base_id}  as-of: {result.as_of} ===")
    if result.error:
        print(f"  ERROR     : {result.error}")
        sys.exit(1)

    print(f"  title     : {result.base_title[:90]}")
    print(f"  base      : {result.base_source_id}")
    if staleness.get("index_stale"):
        print("  index     : stale")
    print(f"  amendments: {len(result.amendments_scanned)} matched | "
          f"{len(result.amendments_applied)} applied | "
          f"{len(result.amendments_skipped_future)} future | "
          f"{len(result.amendments_skipped_contingent)} contingent | "
          f"{len(result.amendments_skipped_unknown_effective)} unknown-effective")
    print(f"  ops       : {result.n_ops}")

    if result.amendments_applied:
        print("  applied:")
        for source_id in result.amendments_applied:
            print(f"    {source_id}")
    if result.amendments_skipped_contingent:
        print("  skipped (contingent commencement):")
        for source_id in result.amendments_skipped_contingent:
            print(f"    {source_id}")
    if result.amendments_skipped_unknown_effective:
        print("  skipped (unknown effective date):")
        for source_id in result.amendments_skipped_unknown_effective:
            print(f"    {source_id}")

    if getattr(args, "show_text", False) and result.replayed is not None:
        print()
        print("=== Replayed text ===")
        print(replayed_text or "")
