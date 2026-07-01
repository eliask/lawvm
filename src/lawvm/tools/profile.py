"""lawvm profile — cProfile the single-statute compile + replay path.

AGENTS.md §2.7 mandate: "Profile (``cProfile``) the single-statute path before
reasoning about the cause; code-reading hypotheses about hot paths are usually
wrong." This wraps the same single-statute compile + replay entry point used by
``lawvm replay`` (FI default) in ``cProfile``, writes a pstats dump to ``--out``
when supplied, and prints a top-N cumtime summary to stdout so the user can
eyeball hotspots without loading the pstats file separately.

This command does NOT optimize findings, rejected ops, diagnostics, or
strict-mode behavior to improve wall time (AGENTS.md §2.7 / §1.8). It only
measures the production synchronous single-statute path — no parallelism is
introduced inside the profiler (§2.7 explicitly: synchronous single-statute is
correct).

Jurisdiction dispatch:

- ``-j fi`` (default): wraps the FI single-statute compile + replay path used by
  ``lawvm replay`` (``call_replay_xml`` over ``lawvm.finland.replay_entrypoint``).
  ``--as-of`` selects the target date.
- ``-j nz``: wraps the NZ per-work chain replay
  (:func:`lawvm.new_zealand.chain_replay.build_archived_work_chain_replay`).
  ``--as-of`` is IGNORED on this branch — the NZ chain replay runs ALL archived
  versions of ``work_id`` (the chain carries one evolving tree across every
  version date), there is no single-date PIT selection equivalent to FI's. The
  caller still passes ``--as-of`` because the shared CLI parser marks it
  required; a placeholder date is fine.
- ``-j us``: wraps the US per-window dry-run
  (:func:`lawvm.us_federal.bench.evaluate_window`). The canonical US replay unit
  is a *window* — an adjacent USC-edition pair ``(title, before, after)`` — so
  the positional id is the window key ``title<T>:<before>-><after>``
  (e.g. ``title11:2016->2018``). ``--as-of`` is IGNORED (the window pins the two
  edition years).
- ``-j uk``: wraps the UK single-statute compile + replay — resolve the affected
  act's enacted XML from the archive, parse to IR, compile archive-backed effect
  ops (:class:`lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline`), apply
  them (:func:`lawvm.uk_legislation.uk_amendment_replay.replay_uk_ops`). The
  positional id is the affected-act source id ``<type>/<year>/<number>``
  (e.g. ``ukpga/1998/42``). ``--as-of`` is IGNORED (the full compiled op stream
  is applied over the enacted tree; no single-date PIT selection).

Usage (via lawvm CLI):
    lawvm profile 2006/1299 --as-of 2024-01-01
    lawvm profile 2006/1299 --as-of 2024-01-01 --out statute.pstats
    lawvm profile 2006/1299 --as-of 2024-01-01 --out statute.pstats --top 50
    lawvm profile -j nz act_public_1992_122 --as-of 2025-03-30 --top 15
    lawvm profile -j us title11:2016->2018 --as-of 2000-01-01 --top 20
    lawvm profile -j uk ukpga/1998/42 --as-of 2000-01-01 --top 20
"""
from __future__ import annotations

import argparse
import cProfile
import pstats
import sys
from io import StringIO
from typing import Any, TextIO


def _run_profiled_fi_replay(
    args: argparse.Namespace,
    prof: cProfile.Profile,
) -> Any:
    """Run the FI single-statute compile + replay path under ``prof``.

    Mirrors the FI branch of ``lawvm replay`` (``call_replay_xml`` over
    ``lawvm.finland.replay_entrypoint.replay_xml``) so the profiled work is
    byte-identical to the production path the user is reasoning about.
    The profiler is enabled only around the replay invocation; the surrounding
    CLI plumbing (pstats dump, stdout summary) stays outside the profiled
    region (§2.7: profile the compile + replay path, not the CLI plumbing).

    ``quiet=True`` is a real boundary contract: tools that request a profile
    must not leak raw replay chatter into stdout alongside the cumtime summary.
    """
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import (
        ReplayXmlRequest,
        ReplayXmlSinks,
        call_replay_xml,
    )
    from lawvm.finland.strict_profile import FINLAND_INGESTION_V1

    use_strict = getattr(args, "strict", False)
    strict_profile = FINLAND_INGESTION_V1 if use_strict else None
    as_of = getattr(args, "as_of", "")
    mode = getattr(args, "mode", "legal_pit")

    replay_meta: dict[str, object] = {}
    prof.enable()
    try:
        return call_replay_xml(
            replay_xml,
            request=ReplayXmlRequest(
                parent_id=args.statute_id,
                mode=mode,
                as_of=as_of,
                quiet=True,
                strict_profile=strict_profile,
            ),
            sinks=ReplayXmlSinks(replay_meta_out=replay_meta),
        )
    finally:
        prof.disable()


def _run_profiled_nz_chain_replay(
    args: argparse.Namespace,
    prof: cProfile.Profile,
) -> Any:
    """Run the NZ per-work chain replay under ``prof``.

    Wraps :func:`lawvm.new_zealand.chain_replay.build_archived_work_chain_replay`
    so the profiled work is the same per-work chain-replay path the corpus
    aggregator runs. The NZ frontend does not expose a single-statute
    single-date replay entry point comparable to FI's ``replay_xml``: the
    canonical NZ replay unit is the chain across ALL archived versions of the
    work (one evolving tree across every version date), and
    ``build_archived_work_chain_replay`` is the binding entry point for that
    unit.

    ``args.as_of`` is accepted for CLI parser symmetry with the FI branch and
    IGNORED on this branch — the chain itself decides which version dates to
    carry (i.e. all of them). Callers pass a placeholder date.

    The current ``work_id`` is read from ``args.statute_id`` (NZ work-id form
    ``act_public_<year>_<number>``); the ``nz_legislation.farchive`` path is
    resolved through the shared ``corpus_store.resolve_farchive_path`` chokepoint
    so the profiler works in a git worktree under ``LAWVM_CANONICAL_DATA_ROOT``.
    ``--mode`` and ``--strict`` are FI-only flags and silently ignored here.
    """
    from lawvm.corpus_store import resolve_farchive_path
    from lawvm.new_zealand.chain_replay_corpus import build_archived_work_chain_replay

    work_id = args.statute_id  # caller passes act_public_<year>_<number>
    families = "all"
    # Resolve the NZ farchive through the shared corpus-store chokepoint so the
    # profiler works in a git worktree (LAWVM_CANONICAL_DATA_ROOT) exactly like
    # every other corpus consumer — not the historical cwd-relative literal.
    db_path, _rule = resolve_farchive_path(
        "nz_legislation.farchive", explicit_env="LAWVM_NZ_LEGISLATION_FARCHIVE_DB"
    )
    prof.enable()
    try:
        return build_archived_work_chain_replay(db_path, work_id, families=families)
    finally:
        prof.disable()


def _run_profiled_us_window_replay(
    args: argparse.Namespace,
    prof: cProfile.Profile,
) -> Any:
    """Run the US per-window dry-run (compile + replay) under ``prof``.

    The canonical US replay unit is a *window* — an adjacent USC-edition pair
    ``(title, before_year, after_year)`` — not a single Public Law. The window
    laws are DERIVED from the witness delta between the two editions, then every
    derived Public Law is compiled and applied over the before-edition tree and
    scored against the after edition. :func:`lawvm.us_federal.bench.evaluate_window`
    is the binding per-window entry point the US corpus aggregator runs, so the
    profiled work is byte-identical to the production single-window path.

    ``args.statute_id`` carries the window key in the form
    ``title<T>:<before>-><after>`` (e.g. ``title11:2016->2018``) — the same key
    :class:`lawvm.us_federal.bench.BenchWindow.key` prints. ``--as-of``,
    ``--mode`` and ``--strict`` are FI-only flags and IGNORED here (the window
    itself pins the two edition years; there is no single-date PIT selection).
    """
    from lawvm.us_federal.bench import evaluate_window
    from lawvm.us_federal.sources import open_us_federal_farchive

    window = _parse_us_window_key(args.statute_id)
    archive = open_us_federal_farchive(readonly=True)
    prof.enable()
    try:
        return evaluate_window(archive, window)
    finally:
        prof.disable()
        close = getattr(archive, "close", None)
        if callable(close):
            close()


def _parse_us_window_key(key: str) -> Any:
    """Parse a US window key ``title<T>:<before>-><after>`` into a ``BenchWindow``.

    Mirrors :attr:`lawvm.us_federal.bench.BenchWindow.key`. ``prior_edition_years``
    is left empty (the profiler measures the base window compile + replay; the F2
    prior-edition reversion channel only strengthens channel (a) and is not part
    of the single-window hot path we are targeting). Raises ``SystemExit(2)`` on a
    malformed key so the CLI fails loud rather than profiling an empty window.
    """
    import re

    from lawvm.us_federal.bench import BenchWindow

    m = re.fullmatch(r"title(\d+):(\d+)->(\d+)", key.strip())
    if m is None:
        print(
            f"ERROR: US window key {key!r} is malformed; expected "
            f"'title<T>:<before>-><after>' (e.g. title11:2016->2018)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    title, before_year, after_year = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return BenchWindow(
        title=title,
        before_year=before_year,
        after_year=after_year,
        include=True,
        window_law_count=0,
        prior_edition_years=(),
        note="lawvm-profile",
    )


def _run_profiled_uk_statute_replay(
    args: argparse.Namespace,
    prof: cProfile.Profile,
) -> Any:
    """Run the UK single-statute compile + replay under ``prof``.

    Mirrors the per-statute compile + replay path ``lawvm uk-bench`` runs:
    resolve the affected act's enacted XML from the archive, parse it to IR,
    compile the archive-backed effect ops
    (:meth:`lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline.compile_ops_for_statute`),
    and apply them (:func:`lawvm.uk_legislation.uk_amendment_replay.replay_uk_ops`).
    UK replay is archive-backed; effects feeds and affecting-act XMLs are loaded
    from the Farchive DB (no on-disk XML fallback).

    ``args.statute_id`` carries the UK affected-act id in the source-id form
    ``<type>/<year>/<number>`` (e.g. ``ukpga/1998/42``). ``--as-of``, ``--mode``
    and ``--strict`` are FI-only flags and IGNORED here — UK replay applies the
    full compiled op stream over the enacted tree, there is no single-date PIT
    selection equivalent to FI's on this branch.
    """
    from pathlib import Path

    from farchive import Farchive

    from lawvm.corpus_store import resolve_farchive_path
    from lawvm.uk_legislation.uk_amendment_replay import (
        UKReplayPipeline,
        replay_uk_ops,
    )
    from lawvm.uk_legislation.uk_grafter import parse_uk_statute_ir_bytes

    sid = args.statute_id
    db_path, _rule = resolve_farchive_path(
        "uk_legislation.farchive", explicit_env="LAWVM_UK_LEGISLATION_FARCHIVE_DB"
    )
    archive = Farchive(db_path)
    try:
        enacted_url = _resolve_uk_enacted_url(archive, sid)
        enacted_bytes = archive.get(enacted_url)
        if not enacted_bytes:
            print(
                f"ERROR: UK enacted XML for {sid!r} is empty at {enacted_url}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        pipeline = UKReplayPipeline(Path("."))
        prof.enable()
        try:
            enacted_ir = parse_uk_statute_ir_bytes(
                enacted_bytes,
                statute_id=sid,
                version_label="enacted",
                source_path=enacted_url,
            )
            ops = pipeline.compile_ops_for_statute(sid, archive=archive)
            return replay_uk_ops(enacted_ir, ops)
        finally:
            prof.disable()
    finally:
        archive.close()


def _resolve_uk_enacted_url(archive: Any, sid: str) -> str:
    """Resolve the enacted-XML locator for UK affected-act id ``sid``.

    Mirrors the ``locator_span`` lookup ``lawvm uk-bench`` uses to enumerate the
    corpus (``…/<type>/<year>/<number>/enacted/data.xml``). Fails loud with
    ``SystemExit(2)`` when the act has no enacted XML in the archive so the
    profiler never silently profiles a statute the archive cannot supply.
    """
    rows = archive._conn.execute(
        "SELECT DISTINCT locator FROM locator_span "
        "WHERE locator LIKE ?",
        (f"%/{sid}/enacted/data.xml",),
    ).fetchall()
    if not rows:
        print(
            f"ERROR: UK act {sid!r} has no enacted XML in the archive "
            f"(expected a '…/{sid}/enacted/data.xml' locator)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return str(rows[0][0])


def _print_top_summary(
    prof: cProfile.Profile,
    top: int,
    stream: TextIO,
) -> None:
    """Print the standard pstats cumtime summary to ``stream``.

    ``strip_dirs`` keeps the table scan-friendly; ``sort_stats('cumulative')``
    surfaces the hot outer frames (pipeline phases) before recursive leaves,
    which is what a developer reasoning about §2.7 hot paths wants to see first.
    """
    if top <= 0:
        return
    buf = StringIO()
    stats = pstats.Stats(prof, stream=buf)
    stats.strip_dirs().sort_stats("cumulative").print_stats(top)
    stream.write(buf.getvalue())


def main(args: argparse.Namespace) -> None:
    """Profile the single-statute compile + replay path.

    Args mirror the ``lawvm replay`` FI path (``statute_id``, ``--as-of``,
    ``--mode``, ``--strict``) plus ``--out`` (pstats dump path) and ``--top``
    (cumtime summary row count).

    For NZ (``-j nz``) the entry point is the per-work chain replay
    (:func:`lawvm.new_zealand.chain_replay.build_archived_work_chain_replay`)
    over the archived NZ farchive. For US (``-j us``) it is the per-window
    dry-run (:func:`lawvm.us_federal.bench.evaluate_window`) over the archived
    ``us_federal.farchive`` (positional id is the window key
    ``title<T>:<before>-><after>``). For UK (``-j uk``) it is the single-statute
    compile + replay over the archived ``uk_legislation.farchive`` (positional id
    is the affected-act source id ``<type>/<year>/<number>``). ``--as-of`` is
    IGNORED on the NZ/US/UK branches (no single-date PIT selection); callers
    still pass ``--as-of`` because the shared CLI parser marks it required.
    """
    j = getattr(args, "jurisdiction", "fi")
    prof = cProfile.Profile()
    if j == "fi":
        _run_profiled_fi_replay(args, prof)
    elif j == "nz":
        _run_profiled_nz_chain_replay(args, prof)
    elif j == "us":
        _run_profiled_us_window_replay(args, prof)
    elif j == "uk":
        _run_profiled_uk_statute_replay(args, prof)
    else:
        print(
            f"ERROR: lawvm profile does not yet support -j {j} (FI replay XML, "
            f"NZ per-work chain replay, US per-window dry-run, and UK "
            f"single-statute compile + replay are supported; other jurisdictions "
            f"use a different entry point)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    out_path = getattr(args, "out", None)
    if out_path:
        pstats.Stats(prof).dump_stats(out_path)
        print(f"pstats dumped to: {out_path}")

    top = getattr(args, "top", 25)
    _print_top_summary(prof, top, sys.stdout)
