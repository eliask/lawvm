"""Scan the UK corpus for REAL conditional-temporal-repeal effects.

Runs the archive-backed compile loop per statute and captures any effect whose
EXTRACTED source carries the conditional-temporal-repeal shape, i.e. the source
pathology classification ``conditional_temporal_repeal_unsupported`` (driven by
``_looks_like_conditional_temporal_repeal_source``). This is the same recognizer
the M1 contingent-commencement claim validator binds against, so a hit here is a
genuine claimable candidate.

Usage:
    LAWVM_CANONICAL_DATA_ROOT=... uv run python \
        scripts/uk_scan_conditional_temporal_repeals.py [--limit N] [--corpus FILE]

With no corpus file it scans every ``.../enacted/data.xml`` locator in the
archive (ukpga/uksi/asp/...). Prints one JSON line per genuine candidate plus a
final summary count.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"

_ENACTED_RE = re.compile(r"legislation\.gov\.uk/(.+?)/enacted/data\.xml$")


def _archive_path() -> Path:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if root:
        cand = Path(root) / "data" / "uk_legislation.farchive"
        if cand.exists():
            return cand
    return _DEFAULT_DB


def _statute_ids_from_archive(archive) -> list[str]:
    out: list[str] = []
    for loc in archive.locators():
        m = _ENACTED_RE.search(loc)
        if m:
            out.append(m.group(1))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="cap statutes scanned")
    parser.add_argument("--corpus", type=str, default="", help="newline-separated statute ids")
    parser.add_argument("--db", type=str, default="", help="archive path override")
    args = parser.parse_args(argv)

    import farchive
    from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

    db_path = Path(args.db) if args.db else _archive_path()
    if not db_path.exists():
        raise SystemExit(f"archive not found: {db_path}")
    archive = farchive.Farchive(str(db_path), readonly=True)

    if args.corpus:
        statute_ids = [
            ln.strip()
            for ln in Path(args.corpus).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
    else:
        statute_ids = _statute_ids_from_archive(archive)
    if args.limit:
        statute_ids = statute_ids[: args.limit]

    print(f"# scanning {len(statute_ids)} statutes from {db_path}", file=sys.stderr)

    pipeline = UKReplayPipeline(_REPO_ROOT)
    candidates: list[dict] = []
    scanned = 0
    errors = 0
    for sid in statute_ids:
        scanned += 1
        if scanned % 250 == 0:
            print(
                f"# ...{scanned}/{len(statute_ids)} scanned, "
                f"{len(candidates)} candidates, {errors} errors",
                file=sys.stderr,
            )
        diags: list[dict] = []
        try:
            pipeline.compile_ops_for_statute(
                sid,
                pit_date=None,
                archive=archive,
                effect_diagnostics_out=diags,
            )
        except Exception:  # noqa: BLE001
            errors += 1
            continue
        for row in diags:
            if str(row.get("rule_id") or "") != "uk_effect_source_pathology_classified":
                continue
            classification = str(row.get("source_pathology") or "")
            if classification != "conditional_temporal_repeal_unsupported":
                continue
            hit = {
                "statute_id": sid,
                "effect_id": row.get("effect_id"),
                "classification": classification,
                "effect_type": row.get("effect_type"),
                "affecting_act_id": row.get("affecting_act_id"),
                "affected_provisions": row.get("affected_provisions"),
                "affecting_provisions": row.get("affecting_provisions"),
            }
            candidates.append(hit)
            print(json.dumps(hit, ensure_ascii=False))

    summary = {
        "scanned": scanned,
        "errors": errors,
        "candidates": len(candidates),
        "candidate_statutes": sorted({c["statute_id"] for c in candidates}),
    }
    print("# SUMMARY " + json.dumps(summary, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
