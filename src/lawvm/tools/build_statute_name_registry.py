"""build-statute-name-registry — materialize the FULL statute-name -> id registry.

The resolution projection (``finland/references/resolve.py``) needs a complete
statute-name registry (Index B) to resolve named-statute citations
(``Holhouslaki``, ``Vaalilain`` ...) that carry no ``(NNN/YYYY)`` anchor. Until
now only a 500-title SAMPLE existed
(``statute_name.sample_entries_from_farchive``). This tool enumerates ALL
statutes in the farchive corpus, reads each ``docTitle`` and its real enactment
date, and serializes a persisted, reproducible registry artifact that
``load_statute_name_registry`` reads back for the projection.

Memory discipline: the corpus is ~56k statutes (the ``.farchive`` is multi-GB).
The builder STREAMS the enumeration — for each statute it reads the source XML,
extracts only ``(statute_id, title, valid_from)`` and discards the XML — it never
holds all corpus XML in memory.

Temporal windows (fail-loud, never fabricated):
  * ``valid_from`` = the source XML's ``FRBRWork/FRBRdate[@name='dateIssued']``
    (the real enactment date). If the corpus lacks it, the window is left OPEN
    (``None``) and counted in the report.
  * ``valid_to`` = always OPEN. A title's end date (an act renamed/repealed) is
    NOT derivable from the source title alone; we do not invent it. The
    consolidation timeline is the place that would close windows, out of scope
    for a title index.

Inflection (fail-loud): a title ending in a known statute head (``laki`` /
``asetus`` ...) is expanded into its inflected surface variants by the M1
morphology engine; a title with NO known head is indexed by its nominative only
(no guessed inflection) and counted in the report as ``no_known_head``.

The artifact is a JSON-lines file (``_meta`` header + one entry/line), the same
convention as other Finland derived working files. It is a pure function of the
corpus, regenerable, and large — so it is GITIGNORED, not committed (like the
``.farchive`` it derives from). Regenerate on demand:

    lawvm build-statute-name-registry [--out PATH] [--limit N]

Reports: total statutes enumerated, titles indexed, generated surface variants,
no-known-head titles (nominative-only), no-date titles (open window), and
collisions (one normalized surface -> several distinct statute ids = the
ambiguous/temporal cases the registry refuses to silently pick among).
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass

from lawvm.finland.references.registries.statute_name import (
    StatuteNameEntry,
    _inflected_surfaces,
    _split_head,
    default_artifact_path,
    serialize_entries,
)


def _archive_path() -> str:
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "finlex.farchive")


@dataclass
class BuildReport:
    """Counts from a full-corpus registry build."""

    total_statutes: int = 0
    titles_indexed: int = 0
    no_title: int = 0
    no_known_head: int = 0  # title indexed nominative-only (no inflection)
    no_date: int = 0  # title with an OPEN valid_from (corpus lacked dateIssued)
    surface_variants: int = 0  # total generated surface keys across all titles
    collisions: int = 0  # surfaces mapping to >1 distinct statute id

    def print_summary(self, file=None) -> None:
        if file is None:
            file = sys.stderr
        print("statute-name registry build report:", file=file)
        print(f"  total statutes enumerated : {self.total_statutes}", file=file)
        print(f"  titles indexed            : {self.titles_indexed}", file=file)
        print(f"  no title (skipped)        : {self.no_title}", file=file)
        print(f"  no known head (nom-only)  : {self.no_known_head}", file=file)
        print(f"  no date (open valid_from) : {self.no_date}", file=file)
        print(f"  generated surface variants: {self.surface_variants}", file=file)
        print(f"  surface collisions        : {self.collisions}", file=file)


def _extract_title_and_date(
    xb: bytes,
) -> tuple[str, dt.date | None] | None:
    """Extract ``(title, enactment_date|None)`` from a statute XML blob.

    Returns ``None`` when no ``docTitle`` is present. ``enactment_date`` is the
    ``FRBRWork`` ``dateIssued`` parsed as a date, or ``None`` if absent/unparseable
    (never fabricated).
    """
    from lxml import etree

    try:
        tree = etree.fromstring(xb)
    except etree.XMLSyntaxError:
        return None

    title_el = tree.find(".//{*}docTitle")
    if title_el is None:
        return None
    title = " ".join(
        etree.tostring(title_el, method="text", encoding="unicode").split()
    )
    if not title:
        return None

    valid_from: dt.date | None = None
    work = tree.find(".//{*}FRBRWork")
    if work is not None:
        for d in work.findall("{*}FRBRdate"):
            if d.get("name") == "dateIssued":
                raw = d.get("date")
                if raw:
                    try:
                        valid_from = dt.date.fromisoformat(raw)
                    except ValueError:
                        valid_from = None
                break
    return title, valid_from


def _iter_entries(limit: int) -> "tuple[list[StatuteNameEntry], BuildReport]":
    """Stream the corpus, yielding (entries, report).

    Memory-careful: reads one statute XML at a time, extracts only title + date,
    and discards the XML before moving on.
    """
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore

    store = TransparentCorpusStore(Farchive(_archive_path()))
    ids = store.list_statute_ids()
    if limit:
        ids = ids[:limit]

    report = BuildReport(total_statutes=len(ids))
    entries: list[StatuteNameEntry] = []
    # surface key -> set of distinct statute ids (collision detection)
    surface_to_ids: dict[str, set[str]] = {}

    print(
        f"build-statute-name-registry: enumerating {len(ids)} statutes...",
        file=sys.stderr,
    )
    for i, sid in enumerate(ids):
        if i and i % 10000 == 0:
            print(f"  {i}/{len(ids)}", file=sys.stderr)
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            report.no_title += 1
            continue
        extracted = _extract_title_and_date(xb)
        del xb  # release the XML blob immediately
        if extracted is None:
            report.no_title += 1
            continue
        title, valid_from = extracted

        entry = StatuteNameEntry(
            statute_id=sid,
            canonical_title=title,
            valid_from=valid_from,
            valid_to=None,
        )
        entries.append(entry)
        report.titles_indexed += 1
        if valid_from is None:
            report.no_date += 1
        if _split_head(title) is None:
            report.no_known_head += 1

        for key in _inflected_surfaces(title):
            surface_to_ids.setdefault(key, set()).add(sid)

    report.surface_variants = len(surface_to_ids)
    report.collisions = sum(1 for ids_ in surface_to_ids.values() if len(ids_) > 1)
    return entries, report


def main(args) -> None:
    limit = getattr(args, "limit", 0) or 0
    out = getattr(args, "out", "") or str(default_artifact_path())

    entries, report = _iter_entries(limit)

    n = serialize_entries(
        entries,
        out,
        meta={
            "total_statutes": report.total_statutes,
            "titles_indexed": report.titles_indexed,
            "no_title": report.no_title,
            "no_known_head": report.no_known_head,
            "no_date": report.no_date,
            "surface_variants": report.surface_variants,
            "collisions": report.collisions,
        },
    )
    report.print_summary()
    print(f"wrote {n} entries to {out}", file=sys.stderr)
