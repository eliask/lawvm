"""lawvm no-statsrad -- fetch Norway Offisielt fra statsråd into Farchive in one go."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.statsrad import (
        build_no_statsrad_extract_report,
        build_no_statsrad_fetch_report,
        build_no_statsrad_index_report,
    )

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    db_arg = getattr(args, "db", None)
    db_path = Path(db_arg) if db_arg else None
    start_page = int(getattr(args, "start_page", 1) or 1)
    limit = getattr(args, "limit", None)
    bulletin_ids = getattr(args, "bulletin_id", None)
    skip_existing = bool(getattr(args, "skip_existing", False))

    index_report = build_no_statsrad_index_report(
        data_dir=data_dir,
        db_path=db_path,
        start_page=start_page,
        article_limit=limit if bulletin_ids is None else None,
        max_age_hours=float(getattr(args, "max_age_hours", 24.0) or 24.0),
        skip_existing=skip_existing,
    )

    fetch_report = build_no_statsrad_fetch_report(
        data_dir=data_dir,
        db_path=db_path,
        bulletin_ids=bulletin_ids,
        max_articles=limit,
        max_age_hours=float("inf"),
        skip_existing=skip_existing,
    )

    fetched_ids = list(fetch_report.get("bulletin_ids", []))
    article_ids = bulletin_ids or fetched_ids or None
    extract_report = build_no_statsrad_extract_report(
        data_dir=data_dir,
        db_path=db_path,
        article_ids=article_ids,
        limit=limit if bulletin_ids is not None else None,
    )

    report = {
        "index": index_report,
        "fetch": fetch_report,
        "extract": extract_report,
    }

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    page_count = index_report.get("page_count", index_report.get("discovered_page_count", 0))
    article_count = index_report.get("article_count", index_report.get("discovered_article_count", 0))
    requested = fetch_report.get("requested_articles", fetch_report.get("selected_article_count", 0))
    stored = fetch_report.get("stored_articles", fetch_report.get("stored_raw_count", 0))
    extracted = extract_report.get("article_count", extract_report.get("processed_article_count", 0))
    event_count = extract_report.get("event_count", 0)

    print()
    print("=== Norway Statsråd ===")
    print(f"  start page         : {start_page}")
    print(f"  page count         : {page_count}")
    print(f"  article count      : {article_count}")
    print(f"  requested articles : {requested}")
    print(f"  stored articles    : {stored}")
    print(f"  extracted articles : {extracted}")
    print(f"  event count        : {event_count}")
