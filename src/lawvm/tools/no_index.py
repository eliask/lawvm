"""lawvm no-index -- build or inspect a Norway amendment index."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import (
        apply_no_commencement_overrides,
        load_no_commencement_overrides,
    )
    from lawvm.norway.index import build_no_amendment_index, save_no_amendment_index

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index = build_no_amendment_index(data_dir)
    commencement_arg = getattr(args, "commencement", None)
    if commencement_arg:
        overrides = load_no_commencement_overrides(Path(commencement_arg))
        index = apply_no_commencement_overrides(index, overrides)
    data: dict[str, Any] = index.to_dict()

    output_arg = getattr(args, "output", None)
    if output_arg:
        save_no_amendment_index(index, Path(output_arg))

    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Amendment Index ===")
    print(f"  source path     : {data['data_dir']}")
    print(f"  source kind     : {data.get('source_kind', 'dir')}")
    print(f"  archives        : {len(data['archive_names'])}")
    print(f"  indexed entries : {len(data['entries'])}")
    status_counts: dict[str, int] = {}
    for entry in data["entries"]:
        status = entry["effective_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    print("  status counts:")
    for key in sorted(status_counts):
        print(f"    {key}: {status_counts[key]}")
    if output_arg:
        print(f"  wrote           : {output_arg}")
