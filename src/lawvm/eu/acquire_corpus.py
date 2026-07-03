"""acquire_corpus.py — EU full-corpus acquisition CLI driver (task #101, Inc 3).

A thin, robust CLI that COMPOSES the existing EU acquisition functions into a
resumable, owned-truncation, typed-failure-witnessed full-corpus download:

    python -m lawvm.eu.acquire_corpus --full --language eng

It does NOT reinvent acquisition. It orchestrates:

* :func:`lawvm.eu.eu_enumerate.enumerate_snapshot` — the closed-world enumeration
  off the Cellar SPARQL registry (regulations in force). The SPARQL enumerate is
  the completeness claim; if it fails the run is FATAL (we cannot bound the
  corpus) but exits with a clear typed error, not a traceback.
* :func:`lawvm.eu.eu_enumerate.store_snapshot` + ``snapshot_universe`` — freeze
  the dated registry snapshot as a witness and build the closed-world universe
  wired into every per-CELEX acquisition.
* :func:`lawvm.eu.eu_acquire.acquire_celex` — fetch + verify-before-store one
  CELEX manifestation (notice + selected Formex item) into the content-addressed
  ``data/eu_cellar.farchive``. A per-CELEX/per-language fetch failure is CAUGHT,
  recorded as a typed gap witness, and the loop CONTINUES (one bad act never
  aborts the corpus).
* :func:`lawvm.eu.eu_acquire_closure.acquire_amendment_closure` — when
  ``--with-closure`` is set, also acquire each acquired act's base + ``amended_by``
  DAG closure.

Owned discipline preserved end-to-end:

* Closed-world enumeration claim: every enumerated id is accounted in the final
  summary — ``acquired ∪ skipped-non-act ∪ failed == enumerated``.
* Owned truncation: ``--limit N`` records ``acquisition_sampled=True`` and the
  cap; ``--full`` maps to ``sample_limit=None`` (the whole in-force universe).
  Never a silent truncation.
* Typed acquisition-failure witnesses: a REST 502 (or any per-CELEX fetch error)
  is a recorded :class:`CorpusGap`, never a silent zero / crash.
* Resumable: ``--resume`` skips a ``(celex, language)`` whose item locator is
  already content-addressed in the farchive, so an interrupted run continues
  without refetching.

Determinism: no ``datetime.now()`` in pure logic — the fetch timestamp is
supplied (UTC now is read ONCE at the CLI boundary, mirroring the sibling lanes).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lawvm.eu import eu_acquire, eu_enumerate
from lawvm.eu.eu_acquire_closure import acquire_amendment_closure
from lawvm.eu.eu_enumerate import (
    SPARQL_ENDPOINT,
    EnumerationError,
    EnumerationSnapshot,
    is_well_formed_celex,
    regulations_in_force_query,
    snapshot_universe,
    store_snapshot,
)

# --------------------------------------------------------------------------- #
# Typed run record
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CorpusGap:
    """A typed acquisition-failure / gap witness for one (celex, language).

    Never a silent zero or crash: a REST 502, a missing manifestation in one
    language, or an unexpected per-CELEX exception is recorded here and the
    corpus loop continues.
    """

    celex: str
    language: str
    rule_id: str
    reason: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "celex": self.celex,
            "language": self.language,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class CorpusAcquireRun:
    """Owned account of one full-corpus acquisition run."""

    snapshot_id: str
    snapshot_locator: str
    enumerated_count: int
    acquirable_count: int
    languages: tuple[str, ...]
    fmt: str
    with_closure: bool
    dry_run: bool
    acquisition_sampled: bool
    sample_limit: int | None
    #: CELEXes selected into the acquisition window (acts the run targets).
    window_celexes: tuple[str, ...] = ()
    #: per-language acquired CELEX counts (a stored/observed item locator).
    acquired_per_language: dict[str, int] = field(default_factory=dict)
    #: per-language skipped-because-already-present counts (``--resume``).
    resumed_skipped_per_language: dict[str, int] = field(default_factory=dict)
    #: enumerated ids that are not well-formed ACT CELEX (corrigenda etc.).
    non_act_skipped: tuple[str, ...] = ()
    #: closure acts acquired (base + amended_by), de-duplicated.
    closure_acts_acquired: tuple[str, ...] = ()
    #: typed gap witnesses (REST 502s, missing manifestations, errors).
    gaps: list[CorpusGap] = field(default_factory=list)

    @property
    def acquired_total(self) -> int:
        return sum(self.acquired_per_language.values())

    @property
    def failed_celexes(self) -> tuple[str, ...]:
        return tuple(sorted({g.celex for g in self.gaps}))

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "schema": "lawvm.eu_acquire_corpus_run.v0",
            "snapshot_id": self.snapshot_id,
            "snapshot_locator": self.snapshot_locator,
            "enumerated_count": self.enumerated_count,
            "acquirable_count": self.acquirable_count,
            "languages": list(self.languages),
            "format": self.fmt,
            "with_closure": self.with_closure,
            "dry_run": self.dry_run,
            "acquisition_sampled": self.acquisition_sampled,
            "sample_limit": self.sample_limit,
            "window_count": len(self.window_celexes),
            "acquired_per_language": dict(self.acquired_per_language),
            "acquired_total": self.acquired_total,
            "resumed_skipped_per_language": dict(self.resumed_skipped_per_language),
            "non_act_skipped_count": len(self.non_act_skipped),
            "closure_acts_acquired_count": len(self.closure_acts_acquired),
            "gap_count": len(self.gaps),
            "failed_celex_count": len(self.failed_celexes),
            "gaps": [g.to_dict() for g in self.gaps],
        }


class CorpusAcquisitionError(RuntimeError):
    """Fatal corpus-acquisition error (e.g. the SPARQL enumerate failed)."""


# --------------------------------------------------------------------------- #
# Resume probe
# --------------------------------------------------------------------------- #


def _already_present(farchive: Any, locator: str) -> bool:
    """True iff the locator is already content-addressed in the farchive.

    Reuses the same ``farchive.history`` no-op channel as
    ``eu_acquire._store_if_new``: a non-empty history means the item witness was
    acquired before, so ``--resume`` can skip the refetch.
    """
    try:
        spans = farchive.history(locator)
    except Exception:  # pragma: no cover - farchive errors are non-fatal for resume
        return False
    return bool(spans)


# --------------------------------------------------------------------------- #
# Orchestration (composes enumerate + acquire_celex + closure)
# --------------------------------------------------------------------------- #


def run_corpus_acquisition(
    *,
    farchive: Any,
    snapshot: EnumerationSnapshot,
    fetched_at: datetime,
    languages: tuple[str, ...],
    fmt: str,
    with_closure: bool,
    sample_limit: int | None,
    resume: bool,
    dry_run: bool,
    universe_kind: str = "static_manifest",
    progress: Callable[[str], None] | None = None,
    _acquire_celex: Callable[..., eu_acquire.CelexIngestRun] | None = None,
    _acquire_closure: Callable[..., Any] | None = None,
) -> CorpusAcquireRun:
    """Acquire the enumerated corpus into ``farchive`` (or report a dry run).

    Composes the existing seams; the loop is the only new logic. Every enumerated
    id is accounted: ``acquired ∪ skipped-non-act ∪ failed == enumerated``.

    ``_acquire_celex`` / ``_acquire_closure`` are test seams mirroring
    :func:`eu_acquire.acquire_celex` / :func:`acquire_amendment_closure`.
    """
    log = progress or (lambda _msg: None)
    acquire_celex = _acquire_celex or eu_acquire.acquire_celex
    acquire_closure = _acquire_closure or acquire_amendment_closure

    # Store the snapshot witness + build the closed-world universe. The snapshot
    # IS the completeness artifact; persist it even on a dry run.
    snapshot_locator = store_snapshot(farchive, snapshot, observed_at=fetched_at)
    universe = snapshot_universe(snapshot, universe_kind=universe_kind)

    enumerated = snapshot.celexes
    acquirable = tuple(c for c in enumerated if is_well_formed_celex(c))
    non_act = tuple(c for c in enumerated if not is_well_formed_celex(c))

    if sample_limit is None:
        window = acquirable
        sampled = False
    else:
        window = acquirable[:sample_limit]
        sampled = len(acquirable) > sample_limit

    run = CorpusAcquireRun(
        snapshot_id=snapshot.snapshot_id,
        snapshot_locator=snapshot_locator,
        enumerated_count=len(enumerated),
        acquirable_count=len(acquirable),
        languages=languages,
        fmt=fmt,
        with_closure=with_closure,
        dry_run=dry_run,
        acquisition_sampled=sampled,
        sample_limit=sample_limit,
        window_celexes=window,
        non_act_skipped=non_act,
        acquired_per_language={lang: 0 for lang in languages},
        resumed_skipped_per_language={lang: 0 for lang in languages},
    )

    log(
        f"enumerated={len(enumerated)} acquirable={len(acquirable)} "
        f"non_act={len(non_act)} window={len(window)} "
        f"languages={','.join(languages)} sampled={sampled}"
    )

    if dry_run:
        log("dry-run: no payloads fetched")
        return run

    closure_acquired: set[str] = set()
    acquired_count = 0
    for index, celex in enumerate(window, start=1):
        for language in languages:
            item_locator = eu_acquire.celex_locator(celex, "enacted", language, fmt)
            if resume and _already_present(farchive, item_locator):
                run.resumed_skipped_per_language[language] += 1
                log(f"[{index}/{len(window)}] {celex} {language}: resume-skip (present)")
                continue
            try:
                ingest = acquire_celex(
                    celex,
                    fetched_at=fetched_at,
                    language=language,
                    fmt=fmt,
                    farchive=farchive,
                    universe=universe,
                )
            except Exception as exc:  # one bad act never aborts the corpus
                run.gaps.append(
                    CorpusGap(
                        celex=celex,
                        language=language,
                        rule_id="EU_CORPUS.ACQUIRE_RAISED",
                        reason="acquire_celex raised; recorded as a gap, loop continues",
                        detail=f"{exc.__class__.__name__}: {exc}",
                    )
                )
                log(f"[{index}/{len(window)}] {celex} {language}: GAP ({exc.__class__.__name__})")
                continue

            # A typed acquisition failure inside the run (REST 502, missing
            # manifestation in THIS language, not-XML) is a per-(celex,language)
            # gap, not a whole-act failure.
            if ingest.failures:
                for fail in ingest.failures:
                    run.gaps.append(
                        CorpusGap(
                            celex=celex,
                            language=language,
                            rule_id=fail.rule_id,
                            reason=fail.reason,
                            detail=fail.detail,
                        )
                    )
            if ingest.added or ingest.skipped:
                run.acquired_per_language[language] += 1
                acquired_count += 1
                log(
                    f"[{index}/{len(window)}] {celex} {language}: "
                    f"acquired (added={ingest.added} skipped={ingest.skipped}) "
                    f"running_total={acquired_count}"
                )
            else:
                log(
                    f"[{index}/{len(window)}] {celex} {language}: "
                    f"no witness stored ({len(ingest.failures)} gap(s))"
                )

        if with_closure:
            try:
                closure = acquire_closure(
                    celex,
                    fetched_at=fetched_at,
                    language=languages[0],
                    fmt=fmt,
                    farchive=farchive,
                )
            except Exception as exc:  # closure failure is a recorded gap, not abort
                run.gaps.append(
                    CorpusGap(
                        celex=celex,
                        language=languages[0],
                        rule_id="EU_CORPUS.CLOSURE_RAISED",
                        reason="acquire_amendment_closure raised; recorded gap, continue",
                        detail=f"{exc.__class__.__name__}: {exc}",
                    )
                )
            else:
                for acquired_celex in getattr(closure, "acquired_celexes", ()):  # noqa: B009
                    closure_acquired.add(acquired_celex)
                for failed_celex in getattr(closure, "failed_celexes", ()):  # noqa: B009
                    run.gaps.append(
                        CorpusGap(
                            celex=failed_celex,
                            language=languages[0],
                            rule_id="EU_CORPUS.CLOSURE_ACT_FAILED",
                            reason="closure amender acquisition recorded a failure",
                            detail=f"base={celex}",
                        )
                    )

    run.closure_acts_acquired = tuple(sorted(closure_acquired))
    return run


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_languages(raw: list[str] | None) -> tuple[str, ...]:
    """Flatten REPEATABLE + comma-list ``--language`` into an ordered tuple.

    ``--language eng --language fin`` and ``--language eng,fin`` both yield
    ``('eng', 'fin')``. Order is preserved, duplicates de-duplicated.
    """
    raw = raw or []
    out: list[str] = []
    for chunk in raw:
        for piece in chunk.split(","):
            lang = piece.strip()
            if lang and lang not in out:
                out.append(lang)
    return tuple(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lawvm.eu.acquire_corpus",
        description=(
            "Acquire the EU regulations-in-force corpus into the content-addressed "
            "eu_cellar.farchive (resumable, owned-truncation, typed-gap-witnessed)."
        ),
    )
    parser.add_argument(
        "--farchive",
        default="data/eu_cellar.farchive",
        help=(
            "Farchive path or name (resolved under LAWVM_CANONICAL_DATA_ROOT). "
            "Default: data/eu_cellar.farchive."
        ),
    )
    parser.add_argument(
        "--language",
        action="append",
        metavar="LANG",
        help=(
            "Expression language, ISO 639-3 (eng/fin). REPEATABLE and/or comma-list; "
            "MULTI supported, e.g. --language eng --language fin. Default: eng."
        ),
    )
    parser.add_argument("--format", default="fmx4", help="Manifestation format slug (default fmx4).")
    parser.add_argument(
        "--with-closure",
        action="store_true",
        help="For each acquired act, also acquire its base + amended_by DAG closure.",
    )
    universe = parser.add_mutually_exclusive_group(required=True)
    universe.add_argument(
        "--limit",
        type=int,
        help="Acquire a BOUNDED sample of N acts (owned: acquisition_sampled=True).",
    )
    universe.add_argument(
        "--full",
        action="store_true",
        help="Acquire the WHOLE regulations-in-force universe (sample_limit=None).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip (celex, language) items already present in the farchive.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Enumerate + report what WOULD be fetched, fetching no payloads.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=eu_enumerate.DEFAULT_TIMEOUT_S,
        help=f"SPARQL enumerate timeout seconds (default {eu_enumerate.DEFAULT_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--endpoint",
        default=SPARQL_ENDPOINT,
        help=f"SPARQL endpoint (default {SPARQL_ENDPOINT}).",
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="ISO 'YYYY-MM-DD' snapshot date (default: today, UTC).",
    )
    parser.add_argument(
        "--report",
        default=None,
        metavar="PATH",
        help=(
            "Write the full structured account JSON (indented, embedding the "
            "gaps/failed_celexes lists) to this file. Default: a timestamped "
            "file under .tmp/eu-corpus-reports/. stdout carries only the human "
            "header + a one-line pointer to this file."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Also emit the full account as a single machine-readable JSON line "
            "on stdout (legacy back-compat for a stdout-parsing consumer). Off "
            "by default so ingest never prints an unbounded one-liner."
        ),
    )
    return parser


def _resolve_farchive_path(raw: str) -> Path:
    """Resolve ``--farchive`` through the corpus_store precedence chokepoint."""
    from lawvm.corpus_store import resolve_farchive_path

    name = Path(raw).name  # accept either a bare name or a data/<name> path
    dest_path, _rule = resolve_farchive_path(name)
    return dest_path


def _default_report_path(run: CorpusAcquireRun, fetched_at: datetime) -> Path:
    """Default report path under ``.tmp/eu-corpus-reports/`` (timestamped)."""
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    snap = run.snapshot_id or "snapshot"
    return Path(".tmp") / "eu-corpus-reports" / f"corpus_account_{snap}_{stamp}.json"


def _write_report(run: CorpusAcquireRun, report_path: Path) -> Path:
    """Write the full indented account JSON to ``report_path``; return the path.

    The full account (embedding the gaps/failed_celexes lists) must live
    SOMEWHERE for total-accounting; this file is that home so stdout need not
    carry an unbounded blob.
    """
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(run.to_summary_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report_path


def _print_summary(
    run: CorpusAcquireRun,
    *,
    out: Any,
    report_path: Path | None = None,
    emit_json: bool = False,
) -> None:
    """Print the bounded owned-account summary (human header + report pointer).

    The full structured account is written to ``report_path`` (indented). stdout
    carries the human-readable header and a one-line pointer + the typed gap
    COUNT — never the unbounded gaps/failed_celexes lists. ``emit_json`` opts
    back into the legacy single-line stdout JSON for a stdout-parsing consumer.
    """
    print("=== EU corpus acquisition summary ===", file=out)
    summary = run.to_summary_dict()
    accounted = (
        run.acquired_total
        + sum(run.resumed_skipped_per_language.values())
        + len(run.non_act_skipped)
        + len(run.failed_celexes)
    )
    print(
        f"enumerated={run.enumerated_count} acquirable={run.acquirable_count} "
        f"window={len(run.window_celexes)} sampled={run.acquisition_sampled} "
        f"(limit={run.sample_limit})",
        file=out,
    )
    print(f"acquired_per_language={summary['acquired_per_language']}", file=out)
    print(f"resumed_skipped_per_language={summary['resumed_skipped_per_language']}", file=out)
    print(f"closure_acts_acquired={summary['closure_acts_acquired_count']}", file=out)
    print(f"non_act_skipped={summary['non_act_skipped_count']}", file=out)
    print(f"gaps(typed)={summary['gap_count']} failed_celexes={summary['failed_celex_count']}", file=out)
    print(
        f"account: acquired+resumed+non_act+failed = {accounted} "
        f"(enumerated={run.enumerated_count})",
        file=out,
    )
    if report_path is not None:
        print(f"full account written to: {report_path}", file=out)
    if emit_json:
        # Legacy stdout-parsing back-compat: the full account as one JSON line.
        print(json.dumps(summary, ensure_ascii=False), file=out)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    languages = _parse_languages(args.language) or ("eng",)
    sample_limit = None if args.full else args.limit
    if sample_limit is not None and sample_limit <= 0:
        parser.error("--limit must be a positive integer")

    snapshot_date = args.snapshot_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched_at = datetime.now(timezone.utc)

    def progress(msg: str) -> None:
        print(msg, file=sys.stderr)

    # --- 1. Enumerate (FATAL on failure — cannot bound the corpus) ----------
    progress(f"enumerating regulations-in-force via SPARQL ({args.endpoint}) ...")
    try:
        snapshot = eu_enumerate.enumerate_snapshot(
            snapshot_date=snapshot_date,
            query=regulations_in_force_query(),
            endpoint=args.endpoint,
            timeout_s=args.timeout,
        )
    except EnumerationError as exc:
        print(
            f"FATAL: SPARQL enumeration returned a non-results body: {exc}",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(
            f"FATAL: SPARQL enumeration transport failed ({exc.__class__.__name__}): "
            f"{exc}; cannot bound the corpus — aborting (no partial closed-world claim).",
            file=sys.stderr,
        )
        return 2

    progress(
        f"enumerated snapshot {snapshot.snapshot_id} date={snapshot.snapshot_date} "
        f"count={snapshot.count}"
    )

    # --- 2. Open the farchive + run the corpus loop -------------------------
    dest_path = _resolve_farchive_path(args.farchive)
    if args.dry_run:
        # A dry run still stores the snapshot witness, so an in-memory archive is
        # not honest here; open the real archive (create dirs if needed) but fetch
        # no payloads. If the archive cannot be opened we still report the count.
        progress(f"dry-run against farchive {dest_path}")

    from farchive import Farchive

    from lawvm.corpus_store import validate_farchive_create_path

    validate_farchive_create_path(dest_path, explicit_env=None)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    farchive = Farchive(str(dest_path))
    try:
        run = run_corpus_acquisition(
            farchive=farchive,
            snapshot=snapshot,
            fetched_at=fetched_at,
            languages=languages,
            fmt=args.format,
            with_closure=args.with_closure,
            sample_limit=sample_limit,
            resume=args.resume,
            dry_run=args.dry_run,
            progress=progress,
        )
    finally:
        farchive.close()

    report_path = (
        Path(args.report)
        if args.report
        else _default_report_path(run, fetched_at)
    )
    _write_report(run, report_path)
    _print_summary(
        run, out=sys.stdout, report_path=report_path, emit_json=args.json
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
