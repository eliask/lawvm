"""lawvm no-ingest -- hydrate norway.farchive from local Lovdata public tarballs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.sources import DEFAULT_NORWAY_DB, ingest_no_public_archives

    data_dir_arg = getattr(args, "data_dir", None)
    if not data_dir_arg:
        raise SystemExit("error: --data-dir is required")
    data_dir = Path(data_dir_arg)
    db_arg = getattr(args, "db", None)
    db_path = Path(db_arg) if db_arg else DEFAULT_NORWAY_DB
    report = ingest_no_public_archives(
        data_dir,
        db_path,
        skip_existing=bool(getattr(args, "skip_existing", False)),
    )

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Ingest ===")
    print(f"  source dir        : {report['source_dir']}")
    print(f"  db path           : {report['db_path']}")
    print(f"  current stored    : {report['current_locators_stored']}")
    print(f"  originals stored  : {report['original_locators_stored']}")
    print(f"  amendments stored : {report['amendment_locators_stored']}")
    print(f"  skipped existing  : {report['skipped_existing']}")
