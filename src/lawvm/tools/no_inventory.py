"""lawvm no-inventory -- Norway replayability inventory from local Lovdata archives."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.inventory import build_no_inventory
    from lawvm.norway.index import load_no_amendment_index

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    index = load_no_amendment_index(index_path) if index_path else None
    commencement_arg = getattr(args, "commencement", None)
    commencement_path = Path(commencement_arg) if commencement_arg else None
    inventory = build_no_inventory(
        data_dir,
        index=index,
        index_path=index_path,
        commencement_path=commencement_path,
    )
    data = inventory.to_dict()
    if index is not None:
        data.update(index.staleness_report(data_dir))

    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Inventory ===")
    print(f"  data dir                     : {data['data_dir']}")
    print(f"  current laws                 : {data['current_laws']}")
    print(f"  with local base source       : {data['current_laws_with_local_base_source']}")
    print(f"  without local base source    : {data['current_laws_without_local_base_source']}")
    print(f"  amendment documents          : {data['amendment_documents']}")
    print(f"  current laws with amendments : {data['current_laws_with_amendments']}")
    print(f"  current laws without changes : {data['current_laws_without_amendments']}")
    print(f"  amended laws missing base    : {data['current_laws_with_amendments_missing_base_source']}")
    print(
        "  amended laws replayable     : "
        f"{data['current_laws_with_amendments_fully_replayable_executable']}"
    )
    print(
        "  amended laws blocked cont.  : "
        f"{data['current_laws_with_amendments_blocked_contingent_executable']}"
    )
    print(
        "  amended laws blocked unk.   : "
        f"{data['current_laws_with_amendments_blocked_unknown_executable']}"
    )
    if data.get("index_stale"):
        print("  index stale                  : yes")

    status_counts = data["amendment_documents_by_status"]
    if isinstance(status_counts, dict):
        print("  amendment status counts:")
        for key in sorted(status_counts):
            print(f"    {key}: {status_counts[key]}")

    top_blocked = data["top_executable_blocked_current_laws"]
    if top_blocked:
        print("  top executable blocked current laws:")
        for item in top_blocked:
            print(f"    {item['base_id']} ({item['amendments']} amendments)")

    top_replayable = data["top_executable_fully_replayable_current_laws"]
    if top_replayable:
        print("  top executable replayable current laws:")
        for item in top_replayable:
            print(f"    {item['base_id']} ({item['amendments']} amendments)")

    top_missing_base = data["top_missing_base_source_current_laws"]
    if top_missing_base:
        print("  top amended current laws missing local base:")
        for item in top_missing_base:
            print(f"    {item['base_id']} ({item['amendments']} amendments)")
