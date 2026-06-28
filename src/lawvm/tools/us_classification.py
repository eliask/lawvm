"""``lawvm us-classification`` -- OLRC classification table builder.

Fetches the OLRC PL-section -> USC-section classification tables for
Congresses 108-118 via the Wayback Machine, parses them into typed
``ClassificationEntry`` carriers, and serializes the resulting index to
a JSON file for reuse by the amendatory lowerer without re-fetching.

Subcommands:
  fetch       fetch all available Congress/session tables and build
              the index JSON (network; intended to run under
              ``systemd-run --user --scope -p MemoryMax=16G``).
  lookup      load a saved index JSON and resolve sample statute IDs
              against it (offline; fast).
  show-stats  load a saved index JSON and print high-level counts.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from lawvm.us_federal.classification_tables import (
    ClassificationEntry,
    ClassificationIndex,
    fetch_classification_table,
    parse_classification_table,
)

# Default Congresses to fetch when ``--all`` is requested. 108th
# Congress started 2003-01-07; the 118th Congress is in session at the
# time of writing. The OLRC publishes one table per Congress * session.
DEFAULT_CONGRESSES: tuple[int, ...] = tuple(range(108, 119))
DEFAULT_SESSIONS: tuple[int, ...] = (1, 2)

DEFAULT_OUTPUT_PATH = Path("data/us_classification_index.json")


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


def _fetch_one(congress: int, session: int, *, verbose: bool) -> list[ClassificationEntry]:
    """Fetch and parse one Congress/session table.

    Failures are surfaced as printed warnings on stderr rather than
    silently swallowed (AGENTS.md sec. 1.10): a missing table for one
    Congress/session does not abort the whole build, but it is visible
    in the run transcript.
    """

    t0 = time.monotonic()
    try:
        html = fetch_classification_table(congress, session)
    except Exception as exc:  # noqa: BLE001 -- surface any fetch failure
        print(
            f"  tbl{congress}pl_{_session_token(session)}.htm: FETCH FAILED -- {exc}",
            file=sys.stderr,
        )
        return []
    entries = parse_classification_table(html, congress=congress)
    elapsed = time.monotonic() - t0
    if verbose:
        print(
            f"  tbl{congress}pl_{_session_token(session)}.htm: {len(entries):>6d} entries "
            f"({elapsed:.2f}s)"
        )
    return entries


def _session_token(session: int) -> str:
    # Mirror the OLRC URL token for log lines.
    if session == 1:
        return "1st"
    if session == 2:
        return "2nd"
    if session == 3:
        return "3rd"
    return f"{session}th"


def cmd_fetch(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    congresses: list[int] = list(args.congresses) if args.congresses else list(DEFAULT_CONGRESSES)
    sessions: list[int] = list(args.sessions) if args.sessions else list(DEFAULT_SESSIONS)

    print(f"Fetching classification tables for Congresses {min(congresses)}-{max(congresses)}")
    print(f"  sessions: {sessions}")
    print(f"  output: {output_path}")

    all_entries: list[ClassificationEntry] = []
    per_congress: dict[int, int] = {}

    for congress in congresses:
        for session in sessions:
            entries = _fetch_one(congress, session, verbose=args.verbose)
            all_entries.extend(entries)
            per_congress[congress] = per_congress.get(congress, 0) + len(entries)

    print()
    print("Entries per Congress:")
    for congress in sorted(per_congress):
        count = per_congress[congress]
        print(f"  {congress}th Congress: {count:>7d} entries")
    print(f"\nTotal entries: {len(all_entries)}")

    index = ClassificationIndex(all_entries)
    serialised = index.to_jsonable()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(serialised, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nWrote index to {output_path} ({output_path.stat().st_size:,} bytes)")

    # Print a few sample lookups against the freshly-built index. These
    # resolve theway the amendatory lowerer will: statute_id + pl_section
    # -> LegalAddress.
    _print_sample_lookups(index)
    return 0


def _print_sample_lookups(index: ClassificationIndex) -> None:
    sample_queries: list[tuple[str, str]] = [
        ("PL 118-31", "101"),
        ("PL 118-31", "101(a)"),
        ("PL 118-31", "101(a)(1)"),
        ("PL 117-328", "1"),
        ("PL 116-92", "122"),
        ("PL 116-92", "122(a)"),
    ]
    print("\nSample lookups:")
    for statute_id, pl_section in sample_queries:
        addr = index.resolve(statute_id, pl_section)
        if addr is None:
            print(f"  {statute_id} sec. {pl_section!r:<10} -> NOT FOUND")
        else:
            print(f"  {statute_id} sec. {pl_section!r:<10} -> {addr}")


# ---------------------------------------------------------------------------
# lookup / show-stats (offline, work from a saved JSON file)
# ---------------------------------------------------------------------------


def _load_index_from_path(path: Path) -> ClassificationIndex:
    if not path.exists():
        print(f"ERROR: index file does not exist: {path}", file=sys.stderr)
        print(
            "  run `uv run lawvm us-classification fetch` first to build the index.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return ClassificationIndex.from_jsonable(data)


def cmd_lookup(args: argparse.Namespace) -> int:
    index = _load_index_from_path(Path(args.index))
    queries = list(args.queries)
    # If no queries were supplied, run the built-in sample set.
    if not queries:
        queries = [
            "PL 118-31:101",
            "PL 118-31:101(a)",
            "PL 118-31:101(a)(1)",
            "PL 117-328:1",
            "PL 116-92:122",
            "PL 116-92:122(a)",
        ]
    for raw in queries:
        if ":" not in raw:
            print(f"  {raw}: malformed query -- expected STATUTE_ID:PL_SECTION", file=sys.stderr)
            continue
        statute_id, _, pl_section = raw.partition(":")
        addr = index.resolve(statute_id, pl_section)
        if addr is None:
            print(f"  {statute_id} sec. {pl_section!r} -> NOT FOUND")
            candidates = index.resolve_all(statute_id, pl_section)
            if candidates:
                print(f"    ambiguous candidates ({len(candidates)}):")
                for c in candidates:
                    print(f"      {c}")
        else:
            print(f"  {statute_id} sec. {pl_section!r} -> {addr}")
    return 0


def cmd_show_stats(args: argparse.Namespace) -> int:
    index = _load_index_from_path(Path(args.index))
    stats = index.stats()
    print(f"Index: {Path(args.index)}")
    for key in sorted(stats):
        print(f"  {key}: {stats[key]}")
    return 0


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


def _build_standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lawvm us-classification",
        description=(
            "Build the OLRC PL-section -> USC-section classification "
            "index from Wayback Machine classification tables."
        ),
    )
    sub = parser.add_subparsers(dest="us_classification_command", metavar="<subcommand>", required=True)

    fetch_p = sub.add_parser(
        "fetch",
        help="fetch classification tables from Wayback and build the JSON index",
        description=(
            "Fetch the OLRC classification tables for the given Congresses "
            "and sessions via the Wayback Machine, parse them into typed "
            "ClassificationEntry carriers, and serialize the resulting "
            "ClassificationIndex to a JSON file for reuse by the "
            "amendatory lowerer. Network-bound; intended to run under "
            "systemd-run MemoryMax=16G."
        ),
    )
    fetch_p.add_argument(
        "--output",
        "-o",
        default=str(DEFAULT_OUTPUT_PATH),
        metavar="PATH",
        help=f"output JSON path (default: {DEFAULT_OUTPUT_PATH})",
    )
    fetch_p.add_argument(
        "--congresses",
        nargs="*",
        type=int,
        metavar="N",
        help="space-separated Congress numbers (default: 108 through 118)",
    )
    fetch_p.add_argument(
        "--sessions",
        nargs="*",
        type=int,
        metavar="N",
        help="space-separated session numbers (default: 1 2)",
    )
    fetch_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print per-table fetch diagnostics",
    )
    fetch_p.set_defaults(func=cmd_fetch)

    lookup_p = sub.add_parser(
        "lookup",
        help="resolve one or more STATUTE_ID:PL_SECTION pairs against a saved index",
        description=(
            "Load a saved JSON index and resolve sample lookups through "
            "ClassificationIndex.resolve. With no explicit queries, runs "
            "the built-in sample set."
        ),
    )
    lookup_p.add_argument(
        "--index",
        "-i",
        default=str(DEFAULT_OUTPUT_PATH),
        metavar="PATH",
        help=f"index JSON path (default: {DEFAULT_OUTPUT_PATH})",
    )
    lookup_p.add_argument(
        "queries",
        nargs="*",
        metavar="STATUTE_ID:PL_SECTION",
        help="one or more queries like 'PL 118-31:101(a)'",
    )
    lookup_p.set_defaults(func=cmd_lookup)

    stats_p = sub.add_parser(
        "show-stats",
        help="print high-level counts from a saved index",
        description="Print high-level entry/PL/key counts from a saved index JSON.",
    )
    stats_p.add_argument(
        "--index",
        "-i",
        default=str(DEFAULT_OUTPUT_PATH),
        metavar="PATH",
        help=f"index JSON path (default: {DEFAULT_OUTPUT_PATH})",
    )
    stats_p.set_defaults(func=cmd_show_stats)

    return parser


def main(args: argparse.Namespace) -> int:
    """Dispatch the ``us-classification`` subcommand.

    The subparsers are registered inline in ``lawvm.tools.cli``; this
    entry point receives the parsed Namespace and routes to the
    appropriate ``cmd_*`` handler by ``us_classification_command``.
    """

    command = getattr(args, "us_classification_command", None)
    if command == "fetch":
        return cmd_fetch(args)
    if command == "lookup":
        return cmd_lookup(args)
    if command == "show-stats":
        return cmd_show_stats(args)
    print(f"ERROR: unknown us-classification subcommand: {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    # When run as a script (``python -m lawvm.tools.us_classification``),
    # rebuild the parser locally; the cli.py integration imports ``main``
    # above and feeds it the already-parsed Namespace.
    parser = _build_standalone_parser()
    raise SystemExit(main(parser.parse_args()))
