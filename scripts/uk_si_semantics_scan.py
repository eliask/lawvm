#!/usr/bin/env python3
"""Scan UK statutory-instrument XML for source-semantics surfaces.

This is a diagnostic inventory for roadmap item 7. It records SI commencement,
vires, extent/application, revocation/lapse, correction-slip, and structure
surfaces without changing replay.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "uk_legislation.farchive"
LEG_BASE = "https://www.legislation.gov.uk"
SI_CLASSES = ("uksi", "ssi", "wsi", "nisr", "ukci", "ukmo")


def statute_ids_from_archive(db_path: Path, *, classes: list[str] | None = None) -> list[str]:
    from farchive import Farchive

    allowed = tuple(classes) if classes else SI_CLASSES
    with Farchive(db_path) as archive:
        ids: set[str] = set()
        for doc_class in allowed:
            for loc in archive.locators(f"{LEG_BASE}/{doc_class}/%/%/data.xml"):
                if loc.endswith("/enacted/data.xml"):
                    continue
                sid = loc[len(LEG_BASE) + 1 : -len("/data.xml")]
                if sid.count("/") == 2:
                    ids.add(sid)
    return sorted(ids)


def ids_from_file(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def run_scan(args: argparse.Namespace) -> dict[str, Any]:
    from farchive import Farchive
    from lawvm.uk_legislation.si_source_semantics import (
        is_uk_si_document_id,
        scan_si_source_semantics_bytes,
    )

    ids: list[str] = []
    if args.ids:
        ids.extend(args.ids)
    if args.ids_file:
        ids.extend(ids_from_file(args.ids_file))
    if args.all:
        ids.extend(statute_ids_from_archive(args.db, classes=args.classes))
    if args.sample:
        pool = statute_ids_from_archive(args.db, classes=args.classes)
        rng = random.Random(args.seed)
        rng.shuffle(pool)
        ids.extend(pool[: args.sample])
    ids = [sid for sid in dict.fromkeys(ids) if is_uk_si_document_id(sid)]
    if not ids:
        raise SystemExit("pass SI ids via --ids/--ids-file, or use --sample/--all")

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    with Farchive(args.db) as archive:
        for sid in ids:
            source_path = f"{LEG_BASE}/{sid}/data.xml"
            xml_bytes = archive.get(source_path)
            if xml_bytes is None:
                missing.append(sid)
                continue
            for record in scan_si_source_semantics_bytes(sid, xml_bytes, source_path=source_path):
                rows.append(record.to_dict())

    family_counts = Counter(str(row["family"]) for row in rows)
    status_counts = Counter(str(row["status"]) for row in rows)
    document_minor_type_counts = Counter(
        str(row["document_minor_type"])
        for row in rows
        if row.get("family") == "si_structure_vocabulary"
    )
    expected_body_unit_counts = Counter(
        str(row["expected_body_unit_kind"])
        for row in rows
        if row.get("family") == "si_structure_vocabulary"
        and row.get("expected_body_unit_kind")
    )
    commencement_default_status_counts = Counter(
        str(row["status"])
        for row in rows
        if row.get("family") == "si_commencement_default_surface"
    )
    source_role_counts = Counter(
        str(row["source_role"]) for row in rows if row.get("source_role")
    )
    geographic_term_counts: Counter[str] = Counter()
    for row in rows:
        for term in row.get("geographic_terms") or ():
            geographic_term_counts[str(term)] += 1
    extent_application_relation_counts = Counter(
        str(row["extent_application_relation"])
        for row in rows
        if row.get("extent_application_relation")
    )
    revocation_lapse_kind_counts: Counter[str] = Counter()
    for row in rows:
        for kind in row.get("revocation_lapse_kinds") or ():
            revocation_lapse_kind_counts[str(kind)] += 1
    vires_marker_counts: Counter[str] = Counter()
    for row in rows:
        for marker in row.get("vires_markers") or ():
            vires_marker_counts[str(marker)] += 1
    correction_marker_counts: Counter[str] = Counter()
    for row in rows:
        for marker in row.get("correction_marker_kinds") or ():
            correction_marker_counts[str(marker)] += 1
    output_rows = rows[: args.limit] if args.limit is not None else rows
    return {
        "n_statutes_scanned": len(ids),
        "n_missing_xml": len(missing),
        "n_records": len(rows),
        "families": dict(family_counts),
        "statuses": dict(status_counts),
        "document_minor_types": dict(document_minor_type_counts),
        "expected_body_unit_kinds": dict(expected_body_unit_counts),
        "commencement_default_statuses": dict(commencement_default_status_counts),
        "source_roles": dict(source_role_counts),
        "geographic_terms": dict(geographic_term_counts),
        "extent_application_relations": dict(extent_application_relation_counts),
        "revocation_lapse_kinds": dict(revocation_lapse_kind_counts),
        "vires_markers": dict(vires_marker_counts),
        "correction_markers": dict(correction_marker_counts),
        "missing_xml": missing,
        "records": output_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--ids", nargs="+", help="explicit SI IDs")
    parser.add_argument("--ids-file", type=Path, help="newline-separated IDs; non-SI IDs are ignored")
    parser.add_argument("--sample", type=int, help="sample N SI documents from archive")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--classes", nargs="+", help="restrict --sample/--all to SI classes")
    parser.add_argument("--all", action="store_true", help="scan all cached SI-like current XML")
    parser.add_argument("--limit", type=int, help="limit emitted record rows")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    payload = run_scan(args)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT / "src"))
    raise SystemExit(main())
