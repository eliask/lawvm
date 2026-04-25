"""lawvm sync-finlex-latest — cache the Finnish PIT XMLs.

This command is intentionally explicit: it enumerates known Finnish statute IDs
from the archive (or an optional corpus CSV), discovers the latest PIT version
for each statute via the Finlex OpenAPI collection endpoint, and stores every
discovered PIT XML version in the farchive database.

Existing exact PIT XML locators are skipped, so reruns are cheap.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from farchive import Farchive

from lawvm.finland.finlex_api import sync_latest_pits


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_db_path() -> Path:
    return _repo_root() / "data" / "finlex.farchive"


def _archive_source_sids(archive: Farchive) -> list[str]:
    sids: list[str] = []
    for locator in archive.locators("finlex://sd/%/fin/main.xml"):
        m = re.match(r"finlex://sd/(\d{4}/[^/]+)/fin/main\.xml$", locator)
        if m:
            sids.append(m.group(1))
    return list(dict.fromkeys(sids))


def _load_corpus_sids(path: Path) -> list[str]:
    sids: list[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if "/" in line and "," not in line and "\t" not in line:
                sids.append(line)
                continue
            row = next(csv.reader([line]))
            if not row:
                continue
            if len(row) == 1:
                sid = row[0].strip()
                if sid:
                    sids.append(sid)
                continue
            first = row[0].strip()
            try:
                int(first)
            except ValueError:
                # Accept simple two-column CSVs where the SID is in the first column.
                sid = first or row[1].strip()
            else:
                sid = row[1].strip()
            if sid:
                sids.append(sid)
    return list(dict.fromkeys(sids))


def main(args) -> None:
    db_path = Path(getattr(args, "db", None) or _default_db_path())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sid_args = list(getattr(args, "sid", None) or [])
    corpus_arg = getattr(args, "corpus", None)
    delay = float(getattr(args, "delay", 1.0) or 1.0)
    verbose = bool(getattr(args, "verbose", False))

    archive = Farchive(db_path)
    try:
        if sid_args:
            sids = list(dict.fromkeys(sid_args))
            source = "explicit --sid arguments"
        elif corpus_arg:
            corpus_path = Path(corpus_arg)
            sids = _load_corpus_sids(corpus_path)
            if not sids:
                raise SystemExit(f"ERROR: empty or unreadable corpus: {corpus_path}")
            source = str(corpus_path)
        else:
            sids = _archive_source_sids(archive)
            source = f"{db_path} (archive source locators)"
            if not sids:
                raise SystemExit(
                    "ERROR: no statute IDs found in archive; pass --corpus "
                    "or populate source XML locators first"
                )

        print(f"Syncing latest Finnish PIT XMLs from {source}: {len(sids)} statutes")
        stats = sync_latest_pits(archive, sids, delay=delay, verbose=verbose)
    finally:
        archive.close()

    print(
        f"statutes={stats['statutes']}  fetched={stats['fetched']}  cached={stats['cached']}  "
        f"skipped={stats['skipped']}  errors={stats['errors']}"
    )
    if stats["errors"]:
        raise SystemExit(1)
