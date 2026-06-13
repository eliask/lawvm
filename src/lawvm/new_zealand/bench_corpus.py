"""Deterministic NZ bench-corpus generator + curated-corpus reader.

The addressable NZ replay set is the works that carry at least one amendment
operation witness (``build_operation_surface`` rows; equivalently the parsed
source ``history_witnesses`` count). Most ``act_public`` works are
enacted-but-unamended and carry zero witnesses; they are excluded.

This module scans the farchive, keeps only works with ``>0`` amendment
operations, and writes two curated CSVs that mirror the per-jurisdiction bench
corpora shipped for UK (``data/uk/bench_corpus*.csv``) and Estonia
(``data/estonia/*.csv``):

- a LARGE/MAXIMAL corpus — every scanned work with ``>0`` amendment operations;
- a SMALL curated smoke slice deliberately spanning operation families and
  pinning the known dry-run canaries + divergence repros.

Both writers are deterministic: works are scanned in lexicographic order, the
``>0`` filter is exact, and there is no clock and no randomness. Truncation is
never silent — the smoke slice states its requested size and the works it
actually kept, and the large writer states the full count.

The CSV column schema (header on row 0):

    work_id,work_type,year,n_amendment_operations,n_history_witnesses,
    operation_families,latest_version_id

``work_id`` is the only column the bench tooling needs (see
:func:`read_corpus_work_ids`); the remaining columns are deterministic
provenance/diversity metadata, analogous to UK's ``type,year,n_effects`` and
EE's ``n_amendments,schema``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.benchmark import _archived_work_max_version_year
from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface

# Default population prefix. ``act_public`` is the amendment-bearing public-act
# class the NZ dry-run/coverage surfaces target. Scanning the full archive
# (bills, amendment-papers, secondary legislation, local/private/imperial acts)
# is supported by passing ``work_id_prefix=""``, but the shipped corpora are
# scoped to ``act_public`` — the class whose amendment witnesses the dry-run
# repeal surface and coverage scoreboard actually consume.
DEFAULT_WORK_ID_PREFIX = "act_public_"

# Canonical CSV columns. ``work_id`` first so the bench reader is trivial.
CORPUS_CSV_FIELDS = (
    "work_id",
    "work_type",
    "year",
    "n_amendment_operations",
    "n_history_witnesses",
    "operation_families",
    "latest_version_id",
)

# Known dry-run canaries + divergence repros that MUST appear in the smoke
# slice (so the smoke corpus always exercises the hand-picked surfaces).
SMOKE_PINNED_WORK_IDS = (
    "act_public_2005_87",
    "act_public_2009_38",
    "act_public_1993_110",
    "act_public_1992_122",
    "act_public_1955_37",
    "act_public_1981_23",
)

# Target smoke-slice size (pinned works + diversity picks).
DEFAULT_SMOKE_SIZE = 30


class NZBenchCorpusError(RuntimeError):
    """Raised when corpus generation cannot proceed deterministically."""


@dataclass(frozen=True)
class NZBenchCorpusRow:
    """One curated-corpus row: a work with ``>0`` amendment operations."""

    work_id: str
    work_type: str
    year: int | None
    n_amendment_operations: int
    n_history_witnesses: int
    operation_families: dict[str, int]
    latest_version_id: str

    def to_csv_row(self) -> dict[str, str]:
        families = ";".join(f"{family}:{count}" for family, count in sorted(self.operation_families.items()))
        return {
            "work_id": self.work_id,
            "work_type": self.work_type,
            "year": "" if self.year is None else str(self.year),
            "n_amendment_operations": str(self.n_amendment_operations),
            "n_history_witnesses": str(self.n_history_witnesses),
            "operation_families": families,
            "latest_version_id": self.latest_version_id,
        }


def _work_type(work_id: str) -> str:
    # work ids are ``<type>_<year>_<number>`` where ``<type>`` may contain a
    # single underscore-free prefix and a kind (e.g. ``act_public``,
    # ``secondary-legislation_pco-drafted``).
    parts = work_id.split("_")
    if len(parts) >= 2 and parts[-1].isdigit() and parts[-2].isdigit():
        return "_".join(parts[:-2])
    return "_".join(parts[:-1]) if len(parts) > 1 else work_id


def scan_amendment_bearing_works(
    db_path: Path,
    *,
    work_id_prefix: str = DEFAULT_WORK_ID_PREFIX,
    on_progress: Any = None,
) -> tuple[tuple[NZBenchCorpusRow, ...], dict[str, int]]:
    """Scan the archive and return rows for works with ``>0`` amendment ops.

    Deterministic: works are scanned in lexicographic order and only works
    whose ``build_operation_surface`` row count is strictly positive are kept.
    Returns the kept rows (in scan order) plus a stats dict that accounts for
    every scanned work (kept / zero-op / parse-failed) so no work is silently
    dropped.
    """

    archive = open_farchive(db_path)
    try:
        max_year = _archived_work_max_version_year(archive)
    finally:
        archive.close()

    population = tuple(
        work_id for work_id in max_year if not work_id_prefix or work_id.startswith(work_id_prefix)
    )

    rows: list[NZBenchCorpusRow] = []
    scanned = 0
    zero_op = 0
    parse_failed = 0
    for work_id in population:
        scanned += 1
        if on_progress is not None and scanned % 500 == 0:
            on_progress(scanned, len(population), len(rows))
        try:
            surface = build_archived_work_operation_surface(db_path, work_id)
        except Exception:  # noqa: BLE001 - any parse/IO failure is a non-amendment skip, counted
            parse_failed += 1
            continue
        n_ops = len(surface.rows)
        if n_ops <= 0:
            zero_op += 1
            continue
        families: dict[str, int] = {}
        for row in surface.rows:
            families[row.operation_family] = families.get(row.operation_family, 0) + 1
        rows.append(
            NZBenchCorpusRow(
                work_id=work_id,
                work_type=_work_type(work_id),
                year=max_year.get(work_id),
                n_amendment_operations=n_ops,
                n_history_witnesses=n_ops,
                operation_families=families,
                latest_version_id=surface.version_id,
            )
        )

    stats = {
        "population": len(population),
        "scanned": scanned,
        "kept": len(rows),
        "zero_op": zero_op,
        "parse_failed": parse_failed,
    }
    return tuple(rows), stats


def select_smoke_rows(
    rows: tuple[NZBenchCorpusRow, ...],
    *,
    smoke_size: int = DEFAULT_SMOKE_SIZE,
    pinned_work_ids: tuple[str, ...] = SMOKE_PINNED_WORK_IDS,
) -> tuple[NZBenchCorpusRow, ...]:
    """Curate a small smoke slice: pinned canaries + family-diverse picks.

    Deterministic. Pinned canaries are always included (and must be present in
    the scanned ``>0``-op rows, else :class:`NZBenchCorpusError`). The
    remaining budget is filled by a deterministic greedy pass that prefers
    works introducing operation families not yet covered, breaking ties by
    work_id, so the slice spans repeal / definition-repeal / amended /
    substituted / inserted families for development.
    """

    by_id = {row.work_id: row for row in rows}
    missing = [work_id for work_id in pinned_work_ids if work_id not in by_id]
    if missing:
        raise NZBenchCorpusError(
            "smoke-corpus pinned canaries are not amendment-bearing in the scanned population "
            f"(missing/zero-op: {', '.join(missing)}); refusing to emit a smoke corpus that "
            "silently drops a canary"
        )

    selected: list[NZBenchCorpusRow] = [by_id[work_id] for work_id in pinned_work_ids]
    selected_ids = {row.work_id for row in selected}
    covered_families: set[str] = set()
    for row in selected:
        covered_families.update(row.operation_families)

    # Candidates sorted deterministically by work_id.
    candidates = sorted((row for row in rows if row.work_id not in selected_ids), key=lambda r: r.work_id)

    # Greedy family-diversity fill: repeatedly take the candidate that adds the
    # most new families (ties broken by work_id), until the budget is full or
    # no candidate adds a new family; then top up by work_id order.
    while len(selected) < smoke_size and candidates:
        best_index = -1
        best_new = 0
        for index, row in enumerate(candidates):
            new_families = len(set(row.operation_families) - covered_families)
            if new_families > best_new:
                best_new = new_families
                best_index = index
        if best_index < 0:
            break
        chosen = candidates.pop(best_index)
        selected.append(chosen)
        covered_families.update(chosen.operation_families)

    # Top up deterministically by work_id if family diversity is exhausted.
    for row in candidates:
        if len(selected) >= smoke_size:
            break
        selected.append(row)

    # Emit in deterministic work_id order (stable across runs).
    return tuple(sorted(selected, key=lambda r: r.work_id))


def write_corpus_csv(path: Path, rows: tuple[NZBenchCorpusRow, ...]) -> None:
    """Write rows to ``path`` as a curated-corpus CSV (deterministic order)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CORPUS_CSV_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_row())


def read_corpus_work_ids(path: Path) -> tuple[str, ...]:
    """Read the ``work_id`` column from a curated-corpus CSV, in file order.

    Deduplicates while preserving first-seen order. Raises
    :class:`NZBenchCorpusError` if the file has no ``work_id`` column or no
    rows, so a malformed corpus never silently degrades to the sampler.
    """

    if not path.exists():
        raise NZBenchCorpusError(f"corpus CSV not found: {path}")
    work_ids: list[str] = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "work_id" not in reader.fieldnames:
            raise NZBenchCorpusError(
                f"corpus CSV {path} has no 'work_id' column (header={reader.fieldnames!r})"
            )
        for row in reader:
            work_id = (row.get("work_id") or "").strip()
            if not work_id or work_id in seen:
                continue
            seen.add(work_id)
            work_ids.append(work_id)
    if not work_ids:
        raise NZBenchCorpusError(f"corpus CSV {path} contained no work_id rows")
    return tuple(work_ids)


def build_corpora(
    db_path: Path,
    *,
    work_id_prefix: str = DEFAULT_WORK_ID_PREFIX,
    smoke_size: int = DEFAULT_SMOKE_SIZE,
    on_progress: Any = None,
) -> tuple[tuple[NZBenchCorpusRow, ...], tuple[NZBenchCorpusRow, ...], dict[str, int]]:
    """Scan + curate both corpora. Returns ``(large_rows, smoke_rows, stats)``."""

    large_rows, stats = scan_amendment_bearing_works(
        db_path, work_id_prefix=work_id_prefix, on_progress=on_progress
    )
    if not large_rows:
        raise NZBenchCorpusError(
            f"no amendment-bearing works found under prefix {work_id_prefix!r} "
            f"(scanned={stats['scanned']}); refusing to write an empty corpus"
        )
    smoke_rows = select_smoke_rows(large_rows, smoke_size=smoke_size)
    return large_rows, smoke_rows, stats


def main(args: Any) -> None:
    db_path = Path(args.db)
    out_dir = Path(getattr(args, "out_dir", None) or "data/nz")
    large_path = Path(getattr(args, "large_out", None) or (out_dir / "bench_corpus.csv"))
    smoke_path = Path(getattr(args, "smoke_out", None) or (out_dir / "bench_corpus_smoke.csv"))
    prefix = getattr(args, "work_id_prefix", DEFAULT_WORK_ID_PREFIX)
    if prefix is None:
        prefix = DEFAULT_WORK_ID_PREFIX
    smoke_size = int(getattr(args, "smoke_size", DEFAULT_SMOKE_SIZE) or DEFAULT_SMOKE_SIZE)
    quiet = bool(getattr(args, "quiet", False))

    def _progress(scanned: int, total: int, kept: int) -> None:
        if not quiet:
            print(f"... scanned {scanned}/{total} works, kept {kept} amendment-bearing", flush=True)

    try:
        large_rows, smoke_rows, stats = build_corpora(
            db_path,
            work_id_prefix=prefix,
            smoke_size=smoke_size,
            on_progress=_progress,
        )
    except NZBenchCorpusError as exc:
        raise SystemExit(f"nz-corpus build-corpus: {exc}") from exc

    write_corpus_csv(large_path, large_rows)
    write_corpus_csv(smoke_path, smoke_rows)

    print(
        f"work_id_prefix={prefix!r} population={stats['population']} scanned={stats['scanned']} "
        f"parse_failed={stats['parse_failed']} zero_op={stats['zero_op']}"
    )
    print(f"LARGE corpus: {large_path} -> {len(large_rows)} works with >0 amendment operations")
    print(f"SMOKE corpus: {smoke_path} -> {len(smoke_rows)} works (pinned canaries + family-diverse picks)")
