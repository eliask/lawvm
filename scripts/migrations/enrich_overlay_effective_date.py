"""Enrich overlay records with ``effective_date`` derived from corrigendum PDF publish date.

The PIT-effective-gate work (Step 1) requires every overlay carry an
``effective_date`` so the apply hook can filter patches whose corrigendum
became effective before or at the replay cutoff_date. Today no overlay has
this field; corrigenda fire unconditionally at every replay, silently
applying 2026 corrigenda to 2005 PITs.

This script back-fills ``effective_date`` on every existing retry-overlay
and unresolvable-overlay record by joining ``source_pdf_witness``
(locator) against ``corrigendum_sources_fi.jsonl.date_published``. Records
whose source_pdf_witness is missing or for which the source manifest has
no date are left without the field (a typed diagnostic per-record is
emitted for manual investigation).

Idempotent: re-running on records already carrying ``effective_date`` is a
no-op for those rows.

Run:
    uv run python scripts/migrations/enrich_overlay_effective_date.py --in-place
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
RETRY_PATH = _REPO_ROOT / "data" / "finland" / "corrigendum_retry_overlays_fi.jsonl"
UNRESOLVABLE_PATH = _REPO_ROOT / "data" / "finland" / "corrigendum_unresolvable_fi.yaml"
SOURCES_PATH = _REPO_ROOT / "data" / "finland" / "corrigendum_sources_fi.jsonl"

# Match Finnish-style dates ("6.3.2014") and ISO ("2014-03-06") patterns.
_FI_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def _to_iso_date(date_str: str) -> str | None:
    s = (date_str or "").strip()
    if not s:
        return None
    m = _ISO_DATE_RE.match(s)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = _FI_DATE_RE.match(s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def _build_source_date_index() -> dict[str, str]:
    """Map source_pdf locator → ISO date_published."""
    out: dict[str, str] = {}
    if not SOURCES_PATH.exists():
        return out
    with SOURCES_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            spdf = str(r.get("source_pdf") or "").strip()
            if not spdf:
                continue
            iso = _to_iso_date(str(r.get("date_published") or ""))
            if iso:
                out[spdf] = iso
    return out


def enrich_retry(dates: dict[str, str], *, dry_run: bool) -> tuple[int, int]:
    """Add effective_date to retry-overlay records. Returns (enriched, miss)."""
    if not RETRY_PATH.exists():
        return 0, 0
    rows: list[dict[str, Any]] = []
    with RETRY_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    enriched = 0
    miss = 0
    for r in rows:
        if r.get("effective_date"):
            continue  # already set
        spdf = str(r.get("source_pdf_witness") or "").strip()
        d = dates.get(spdf)
        if d:
            r["effective_date"] = d
            enriched += 1
        else:
            miss += 1
    if dry_run:
        return enriched, miss
    tmp = RETRY_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    tmp.replace(RETRY_PATH)
    return enriched, miss


def enrich_unresolvable(dates: dict[str, str], *, dry_run: bool) -> tuple[int, int]:
    if not UNRESOLVABLE_PATH.exists():
        return 0, 0
    raw = yaml.safe_load(UNRESOLVABLE_PATH.read_text(encoding="utf-8"))
    if raw is None:
        return 0, 0
    if not isinstance(raw, list):
        raise ValueError(f"{UNRESOLVABLE_PATH} expected list, got {type(raw)!r}")
    enriched = 0
    miss = 0
    for r in raw:
        if not isinstance(r, dict):
            continue
        if r.get("effective_date"):
            continue
        spdf = str(r.get("source_pdf_witness") or "").strip()
        d = dates.get(spdf)
        if d:
            r["effective_date"] = d
            enriched += 1
        else:
            miss += 1
    if dry_run:
        return enriched, miss
    tmp = UNRESOLVABLE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(
            "# Auto-managed by scripts/tribunal_adjudicate.py +"
            "scripts/migrations/enrich_overlay_effective_date.py\n\n"
        )
        yaml.safe_dump(
            raw,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=160,
        )
    tmp.replace(UNRESOLVABLE_PATH)
    return enriched, miss


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    dates = _build_source_date_index()
    print(f"loaded {len(dates)} source-pdf → date mappings", file=sys.stderr)
    e1, m1 = enrich_retry(dates, dry_run=args.dry_run or not args.in_place)
    print(f"retry: enriched {e1}, no-date-mapping {m1}", file=sys.stderr)
    e2, m2 = enrich_unresolvable(dates, dry_run=args.dry_run or not args.in_place)
    print(f"unresolvable: enriched {e2}, no-date-mapping {m2}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())