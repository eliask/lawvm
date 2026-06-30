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

Usage (via lawvm CLI):
    lawvm profile 2006/1299 --as-of 2024-01-01
    lawvm profile 2006/1299 --as-of 2024-01-01 --out statute.pstats
    lawvm profile 2006/1299 --as-of 2024-01-01 --out statute.pstats --top 50
    lawvm profile -j nz act_public_1992_122 --as-of 2025-03-30 --top 15
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
    ``act_public_<year>_<number>``); the farchive path is the conventional
    ``data/nz_legislation.farchive``. ``--mode`` and ``--strict`` are FI-only
    flags and silently ignored here.
    """
    from pathlib import Path

    from lawvm.new_zealand.chain_replay_corpus import build_archived_work_chain_replay

    work_id = args.statute_id  # caller passes act_public_<year>_<number>
    families = "all"
    db_path = Path("data/nz_legislation.farchive")
    prof.enable()
    try:
        return build_archived_work_chain_replay(db_path, work_id, families=families)
    finally:
        prof.disable()


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
    over the archived NZ farchive. ``--as-of`` is IGNORED on this branch —
    the NZ chain replay runs ALL archived versions of the work (no single-date
    PIT selection); callers still pass ``--as-of`` because the shared CLI
    parser marks it required.
    """
    j = getattr(args, "jurisdiction", "fi")
    prof = cProfile.Profile()
    if j == "fi":
        _run_profiled_fi_replay(args, prof)
    elif j == "nz":
        _run_profiled_nz_chain_replay(args, prof)
    else:
        print(
            f"ERROR: lawvm profile does not yet support -j {j} (FI replay XML "
            f"and NZ per-work chain replay are supported; other jurisdictions "
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
