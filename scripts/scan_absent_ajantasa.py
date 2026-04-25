"""scan_absent_ajantasa.py — Find in-force statutes with contentAbsent ajantasa.

Scans the statute-consolidated ZIP for statutes where:
  - oracle XML exists but body is <hcontainer name="contentAbsent"/>
  - finlex:isInForce value="true"

These are laws that ARE in force but have no readable consolidated text
on Finlex. Citizens and lawyers cannot read these laws online.

Outputs: CSV and optional SQLite table in the publication DB.

Run from LawVM/ dir:
    uv run python scripts/scan_absent_ajantasa.py
    uv run python scripts/scan_absent_ajantasa.py --output-csv .tmp/absent_ajantasa.csv
    uv run python scripts/scan_absent_ajantasa.py --publication-db .tmp/finlex_errors_publication.db
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


_PUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS absent_ajantasa (
    statute_id      TEXT PRIMARY KEY,
    title           TEXT,
    year            INTEGER,
    type_statute    TEXT,
    is_amended      INTEGER,
    amendment_count INTEGER,
    latest_amendment TEXT,
    issued_date     TEXT,
    finlex_url      TEXT,
    alkup_url       TEXT,
    stale_known     INTEGER DEFAULT 0,
    stale_status    TEXT,
    stale_status_fi TEXT,
    stale_confidence TEXT,
    stale_confidence_fi TEXT,
    stale_mechanism  TEXT,
    stale_mechanism_fi TEXT,
    stale_notes      TEXT,
    stale_summary_fi TEXT
);

CREATE TABLE IF NOT EXISTS absent_ajantasa_stats (
    total_in_force_absent   INTEGER,
    total_acts              INTEGER,
    total_decrees           INTEGER,
    total_decisions         INTEGER,
    total_announcements     INTEGER,
    total_other             INTEGER,
    total_amended           INTEGER,
    total_modern_amended    INTEGER,
    total_stale_known       INTEGER,
    total_metadata_corrections INTEGER,
    scan_date               TEXT
);

CREATE TABLE IF NOT EXISTS metadata_corrections (
    statute_id      TEXT PRIMARY KEY,
    title           TEXT,
    scope           TEXT,
    status          TEXT,
    status_fi       TEXT,
    confidence      TEXT,
    confidence_fi   TEXT,
    mechanism       TEXT,
    evidence_statute TEXT,
    evidence_quote  TEXT,
    expired_date    TEXT,
    reasoning_evidence TEXT,
    reasoning       TEXT,
    validity        TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS absent_ajantasa_year_amendment_idx ON absent_ajantasa(year DESC, amendment_count DESC);
"""


def _load_corrections(corrections_path: Path) -> dict[str, dict]:
    """Load metadata corrections YAML. Returns {statute_id: entry}."""
    if not corrections_path.exists():
        return {}
    if yaml is None:
        # Fallback: simple regex parse for statute_id lines
        text = corrections_path.read_text(encoding="utf-8")
        ids = re.findall(r'statute_id:\s*"([^"]+)"', text)
        return {sid: {"statute_id": sid} for sid in ids}
    with open(corrections_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entries = data.get("validity_judgements")
    if entries is None:
        entries = data.get("stale_in_force", [])

    def _entry_scope(entry: dict) -> str:
        scope = str(entry.get("scope") or "").strip()
        if scope:
            return scope
        if any(entry.get(k) for k in ("mechanism", "evidence_statute", "expired_date", "notes")):
            return "stale_in_force"
        return "review_ledger"

    stale_entries = []
    for entry in entries:
        if not isinstance(entry, dict) or "statute_id" not in entry:
            continue
        if _entry_scope(entry) == "stale_in_force":
            stale_entries.append(entry)
    return {e["statute_id"]: e for e in stale_entries}


def _status_label_fi(status: str) -> str:
    raw = str(status or "").strip()
    if raw == "valid":
        return "voimassa"
    if raw == "not valid":
        return "ei voimassa"
    return raw


def _confidence_label_fi(confidence: str) -> str:
    raw = str(confidence or "").strip()
    return {
        "confirmed": "vahvistettu",
        "high": "korkea",
        "medium": "keskitaso",
    }.get(raw, raw)


def _mechanism_label_fi(mechanism: str) -> str:
    raw = str(mechanism or "").strip()
    return {
        "sunset_clause": "määräaikainen raukeaminen",
        "explicitly_repealed": "nimenomaisesti kumottu",
        "formally_repealed": "muodollisesti kumottu",
        "eu_accession_superseded": "EU-jäsenyyden myötä korvautunut",
        "likely_superseded": "todennäköisesti korvautunut",
        "wartime_completed": "sotasäädös täyttynyt",
        "temporary_tax_exception": "tilapäinen verohelpotus",
        "one_time_completed": "kertasäädös täyttynyt",
        "annual_tax_law": "vuotuinen verolaki",
        "price_control_wartime": "sodan aikainen hintasäätely",
        "temporary_subsidy": "tilapäinen tukijärjestely",
        "excise_tax_abolished": "valmistevero poistettu",
        "temporary_experiment": "tilapäinen kokeilu",
        "currency_control_abolished": "valuuttasäätely poistettu",
    }.get(raw, raw)


def _summary_fi(status: str, confidence: str, mechanism: str) -> str:
    parts = [
        _status_label_fi(status),
        _confidence_label_fi(confidence),
    ]
    mech = _mechanism_label_fi(mechanism)
    if mech:
        parts.append(mech)
    return " · ".join(part for part in parts if part)


def _finlex_lainsaadanto_url(year: str, num: str) -> str:
    return f"https://www.finlex.fi/fi/lainsaadanto/{year}/{num}"


def _finlex_alkup_url(year: str, num: str) -> str:
    base_num = num.split("-")[0]
    try:
        return f"https://finlex.fi/fi/laki/alkup/{year}/{year}{int(base_num):04d}"
    except ValueError:
        return f"https://finlex.fi/fi/laki/alkup/{year}/{year}{base_num}"


def scan(zip_path: Path, min_year: int = 0,
         corrections: dict[str, dict] | None = None) -> list[dict]:
    """Scan ZIP for in-force contentAbsent statutes. Returns list of dicts."""
    corrections = corrections or {}
    zf = zipfile.ZipFile(zip_path)

    # Build index: year/num -> shortest fin@ main.xml path (= unversioned or latest)
    name_idx: dict[str, str] = {}
    for n in zf.namelist():
        if "/fin@" in n and n.endswith("/main.xml"):
            parts = n.split("/")
            if len(parts) >= 7:
                key = f"{parts[4]}/{parts[5]}"
                if key not in name_idx or len(n) < len(name_idx[key]):
                    name_idx[key] = n

    print(f"Indexed {len(name_idx)} oracle paths from ZIP", file=sys.stderr)

    # First pass: find all contentAbsent statutes
    absent_keys: list[str] = []
    for key, path in name_idx.items():
        data = zf.read(path)
        if b"contentAbsent" in data:
            year_s = key.split("/")[0]
            if year_s.isdigit() and int(year_s) >= min_year:
                absent_keys.append(key)

    print(f"contentAbsent statutes (>={min_year}): {len(absent_keys)}", file=sys.stderr)

    # Second pass: parse metadata for absent statutes
    results: list[dict] = []
    for i, key in enumerate(sorted(absent_keys)):
        path = name_idx[key]
        data = zf.read(path)
        if b'isInForce value="true"' not in data:
            continue
        year_s, num_s = key.split("/", 1)
        record = _parse_statute_record(year_s, num_s, data, corrections)
        if record is not None:
            results.append(record)
        if (i + 1) % 5000 == 0:
            print(f"  {i+1}/{len(absent_keys)} checked, {len(results)} in-force...",
                  file=sys.stderr)

    zf.close()
    return results


def _amendment_sort_key(a: str) -> tuple[int, int]:
    y, n = a.split("/")
    return (int(y), int(n))


def _parse_statute_record(
    year_s: str,
    num_s: str,
    data: bytes,
    corrections: dict[str, dict],
) -> dict | None:
    """Parse one oracle XML payload into an absent_ajantasa row dict."""
    text = data.decode("utf-8", errors="replace")
    sid = f"{year_s}/{num_s}"

    type_statute = "other"
    if "type-statute.act" in text:
        type_statute = "act"
    elif "type-statute.decree" in text:
        type_statute = "decree"
    elif "type-statute.announcement" in text:
        type_statute = "announcement"
    elif "type-statute.decision" in text:
        type_statute = "decision"

    title_m = re.search(r"<docTitle>([^<]+)", text)
    title = title_m.group(1) if title_m else ""
    title = re.sub(r"&#\d+;", " ", title)
    title = re.sub(r"\s+", " ", title).strip()

    amendments = re.findall(r'<finlex:ref href="/akn/fi/act/statute/(\d+/\d+)"', text)
    amendment_count = len(amendments)
    latest_amendment = max(amendments, key=_amendment_sort_key) if amendments else ""

    issued_m = re.search(r'FRBRdate date="([^"]+)" name="dateIssued"', text)
    issued_date = issued_m.group(1) if issued_m else ""

    corr = corrections.get(sid, {})
    return {
        "statute_id": sid,
        "title": title,
        "year": int(year_s),
        "type_statute": type_statute,
        "is_amended": 1 if amendment_count > 0 else 0,
        "amendment_count": amendment_count,
        "latest_amendment": latest_amendment,
        "issued_date": issued_date,
        "finlex_url": _finlex_lainsaadanto_url(year_s, num_s),
        "alkup_url": _finlex_alkup_url(year_s, num_s),
        "stale_known": 1 if corr else 0,
        "stale_status": str(corr.get("status") or corr.get("validity") or "not valid").strip() if corr else "",
        "stale_status_fi": _status_label_fi(corr.get("status") or corr.get("validity") or "not valid") if corr else "",
        "stale_confidence": corr.get("confidence", ""),
        "stale_confidence_fi": _confidence_label_fi(corr.get("confidence", "")) if corr else "",
        "stale_mechanism": corr.get("mechanism", ""),
        "stale_mechanism_fi": _mechanism_label_fi(corr.get("mechanism", "")) if corr else "",
        "stale_notes": corr.get("notes", ""),
        "stale_summary_fi": _summary_fi(
            corr.get("status") or corr.get("validity") or "not valid",
            corr.get("confidence", ""),
            corr.get("mechanism", ""),
        ) if corr else "",
    }


def scan_farchive(
    farchive_path: Path,
    min_year: int = 0,
    corrections: dict[str, dict] | None = None,
) -> list[dict]:
    """Scan farchive for in-force contentAbsent statutes.

    Drop-in replacement for scan() that reads from data/finlex.farchive
    instead of the statute-consolidated ZIP.
    """
    import farchive as fa
    from lawvm.finland.consolidated_artifacts import (
        build_versioned_consolidated_main_glob,
        parse_versioned_consolidated_main_locator,
    )

    corrections = corrections or {}
    archive = fa.Farchive(str(farchive_path))
    try:
        locators = archive.locators(build_versioned_consolidated_main_glob())
        print(f"Indexed {len(locators)} oracle locators from farchive", file=sys.stderr)

        # Build index: sid -> locator with the highest version string
        sid_best: dict[str, tuple[str, str]] = {}  # sid -> (version, locator)
        for loc in locators:
            lparts = parse_versioned_consolidated_main_locator(loc)
            if lparts is None:
                continue
            sid = lparts.sid
            ver = lparts.version
            if sid not in sid_best or ver > sid_best[sid][0]:
                sid_best[sid] = (ver, loc)

        # First pass: find contentAbsent
        absent_sids: list[str] = []
        for sid, (_ver, loc) in sid_best.items():
            year_s = sid.split("/")[0]
            if not year_s.isdigit() or int(year_s) < min_year:
                continue
            data = archive.get(loc)
            if data and b"contentAbsent" in data:
                absent_sids.append(sid)

        print(f"contentAbsent statutes (>={min_year}): {len(absent_sids)}", file=sys.stderr)

        # Second pass: in-force check and metadata extraction
        results: list[dict] = []
        for i, sid in enumerate(sorted(absent_sids)):
            _ver, loc = sid_best[sid]
            data = archive.get(loc)
            if not data or b'isInForce value="true"' not in data:
                continue
            year_s, num_s = sid.split("/", 1)
            record = _parse_statute_record(year_s, num_s, data, corrections)
            if record is not None:
                results.append(record)
            if (i + 1) % 5000 == 0:
                print(f"  {i+1}/{len(absent_sids)} checked, {len(results)} in-force...",
                      file=sys.stderr)

        return results
    finally:
        archive.close()


def write_csv(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"Wrote {len(results)} rows to {path}")


def write_publication_db(results: list[dict], db_path: Path,
                         corrections: dict[str, dict] | None = None) -> None:
    corrections = corrections or {}
    con = sqlite3.connect(str(db_path))

    # Drop and recreate
    con.execute("DROP TABLE IF EXISTS absent_ajantasa")
    con.execute("DROP TABLE IF EXISTS absent_ajantasa_stats")
    con.execute("DROP TABLE IF EXISTS metadata_corrections")
    con.executescript(_PUB_SCHEMA)

    for r in results:
        con.execute(
            "INSERT OR REPLACE INTO absent_ajantasa "
            "(statute_id, title, year, type_statute, is_amended, amendment_count, "
            "latest_amendment, issued_date, finlex_url, alkup_url, "
            "stale_known, stale_status, stale_status_fi, stale_confidence, "
            "stale_confidence_fi, stale_mechanism, stale_mechanism_fi, "
            "stale_notes, stale_summary_fi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (r["statute_id"], r["title"], r["year"], r["type_statute"],
             r["is_amended"], r["amendment_count"], r["latest_amendment"],
             r["issued_date"], r["finlex_url"], r["alkup_url"],
             r.get("stale_known", 0), r.get("stale_status", ""),
             r.get("stale_status_fi", ""), r.get("stale_confidence", ""),
             r.get("stale_confidence_fi", ""), r.get("stale_mechanism", ""),
             r.get("stale_mechanism_fi", ""), r.get("stale_notes", ""),
             r.get("stale_summary_fi", "")),
        )

    # Write metadata_corrections table
    for sid, corr in corrections.items():
        con.execute(
            "INSERT OR REPLACE INTO metadata_corrections "
            "(statute_id, title, scope, status, status_fi, confidence, "
            "confidence_fi, mechanism, evidence_statute, evidence_quote, "
            "expired_date, reasoning_evidence, reasoning, validity, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
             sid,
             corr.get("title", ""),
             corr.get("scope", "stale_in_force"),
             corr.get("status", corr.get("validity", "")),
             corr.get("status_fi", _status_label_fi(corr.get("status", corr.get("validity", "")))),
             corr.get("confidence", ""),
             corr.get("confidence_fi", _confidence_label_fi(corr.get("confidence", ""))),
             corr.get("mechanism", ""),
             corr.get("evidence_statute", ""),
             corr.get("evidence_quote", ""),
             corr.get("expired_date", ""),
             corr.get("reasoning_evidence", ""),
             corr.get("reasoning", ""),
             corr.get("validity", ""),
             corr.get("notes", ""),
            ),
        )

    acts = sum(1 for r in results if r["type_statute"] == "act")
    decrees = sum(1 for r in results if r["type_statute"] == "decree")
    decisions = sum(1 for r in results if r["type_statute"] == "decision")
    announcements = sum(1 for r in results if r["type_statute"] == "announcement")
    other = sum(1 for r in results if r["type_statute"] not in {"act", "decree", "decision", "announcement"})
    amended = sum(1 for r in results if r["is_amended"])
    modern_amended = sum(1 for r in results if r["is_amended"] and r["type_statute"] == "act" and r["year"] >= 1995)
    stale = sum(1 for r in results if r.get("stale_known"))

    con.execute(
        "INSERT INTO absent_ajantasa_stats "
        "(total_in_force_absent, total_acts, total_decrees, total_decisions, "
        "total_announcements, total_other, total_amended, total_modern_amended, "
        "total_stale_known, total_metadata_corrections, scan_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (len(results), acts, decrees, decisions, announcements, other, amended, modern_amended, stale, len(corrections),
         datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()
    print(f"Added absent_ajantasa ({len(results)} rows) + metadata_corrections ({len(corrections)} rows) to {db_path}")


def print_summary(results: list[dict]) -> None:
    by_type = Counter(r["type_statute"] for r in results)
    by_decade = Counter((r["year"] // 10) * 10 for r in results)
    amended = sum(1 for r in results if r["is_amended"])

    print("\n=== In-force statutes with no readable ajantasa ===")
    print(f"  Total: {len(results)}")
    print(f"  Amended (need consolidation): {amended}")
    print()
    print("  By type:")
    for t, c in by_type.most_common():
        print(f"    {t}: {c}")
    print()
    print("  By decade:")
    for d in sorted(by_decade.keys()):
        print(f"    {d}s: {by_decade[d]}")
    print()
    # Top amended acts
    amended_acts = sorted(
        [r for r in results if r["type_statute"] == "act" and r["is_amended"]],
        key=lambda r: r["amendment_count"],
        reverse=True,
    )
    if amended_acts:
        print(f"  Top amended acts without ajantasa ({len(amended_acts)} total):")
        for r in amended_acts[:15]:
            print(f"    {r['statute_id']} ({r['amendment_count']} muutosta): {r['title'][:60]}")
        if len(amended_acts) > 15:
            print(f"    ... and {len(amended_acts) - 15} more")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find in-force statutes with contentAbsent ajantasa."
    )
    parser.add_argument(
        "--farchive", default="data/finlex.farchive",
        help="Path to finlex farchive (default: data/finlex.farchive)",
    )
    parser.add_argument("--min-year", type=int, default=0, help="Min year filter")
    parser.add_argument("--output-csv", help="Output CSV path")
    parser.add_argument("--publication-db", help="Publication DB to add table to")
    parser.add_argument(
        "--corrections", default="data/finlex_metadata_corrections.yaml",
        help="Path to metadata corrections YAML",
    )
    args = parser.parse_args()

    farchive_path = Path(args.farchive)
    if not farchive_path.exists():
        print(f"ERROR: farchive not found: {farchive_path}", file=sys.stderr)
        sys.exit(1)

    corrections = _load_corrections(Path(args.corrections))
    if corrections:
        print(f"Loaded {len(corrections)} metadata corrections", file=sys.stderr)

    results = scan_farchive(farchive_path, min_year=args.min_year, corrections=corrections)

    if not results:
        print("No in-force contentAbsent statutes found.")
        return

    print_summary(results)

    if args.output_csv:
        write_csv(results, Path(args.output_csv))

    if args.publication_db:
        write_publication_db(results, Path(args.publication_db), corrections)


if __name__ == "__main__":
    main()
