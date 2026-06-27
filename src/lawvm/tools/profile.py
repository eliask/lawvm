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

Usage (via lawvm CLI):
    lawvm profile 2006/1299 --as-of 2024-01-01
    lawvm profile 2006/1299 --as-of 2024-01-01 --out statute.pstats
    lawvm profile 2006/1299 --as-of 2024-01-01 --out statute.pstats --top 50
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
    """
    j = getattr(args, "jurisdiction", "fi")
    if j != "fi":
        print(
            f"ERROR: lawvm profile does not yet support -j {j} (FI only — "
            f"replay XML for other jurisdictions uses a different entry point)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    prof = cProfile.Profile()
    _run_profiled_fi_replay(args, prof)

    out_path = getattr(args, "out", None)
    if out_path:
        pstats.Stats(prof).dump_stats(out_path)
        print(f"pstats dumped to: {out_path}")

    top = getattr(args, "top", 25)
    _print_top_summary(prof, top, sys.stdout)
