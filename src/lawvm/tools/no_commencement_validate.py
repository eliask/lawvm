"""lawvm no-commencement-validate -- validate a Norway commencement override file."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import (
        load_no_commencement_overrides,
        validate_no_commencement_overrides,
    )
    from lawvm.norway.index import build_no_amendment_index, load_no_amendment_index

    path = Path(args.commencement)
    overrides = load_no_commencement_overrides(path)

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    if index_path:
        index = load_no_amendment_index(index_path)
    else:
        index = build_no_amendment_index(data_dir)
    staleness = index.staleness_report(data_dir) if index_path else {"index_stale": False}

    report = validate_no_commencement_overrides(index, overrides)
    report.update(staleness)

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Commencement Validation ===")
    print(f"  overrides           : {report['override_count']}")
    print(f"  resolvable          : {len(report['resolvable_sources'])}")
    print(f"  unknown source ids  : {len(report['unknown_source_ids'])}")
    print(f"  blank effective     : {len(report['blank_effective_date'])}")
    print(f"  invalid date format : {len(report['invalid_date_format'])}")
    print(f"  redundant sources   : {len(report['redundant_sources'])}")
    print(f"  with evidence       : {len(report['resolved_with_evidence'])}")
    print(f"  missing evidence    : {len(report['resolved_missing_evidence'])}")
    print(f"  missing contingent  : {len(report['missing_contingent_sources'])}")
    if report.get("index_stale"):
        print("  index stale         : yes")
