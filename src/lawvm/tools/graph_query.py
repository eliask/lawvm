"""lawvm graph — cross-statute graph queries using CorpusGraph.

Phase 9.4/9.5: Exercises the unified graph model built in Phase 9.1.

Subcommands (flags):
  --reverse-cites          What statutes cite sid?
  --affecting-acts         What acts have amended sid?
  --delegates              What authority does sid delegate?
  --silent-breakage        What provisions cite sid and may have been silently affected?
  --provision FRAG         Filter --silent-breakage to provisions citing this fragment
  --as-of DATE             Temporal filter for --silent-breakage (requires --with-timelines)
  --with-timelines         Load full provision timelines (slower, enables --as-of filtering)
  --corpus <csv>           Override corpus CSV (default: .tmp/batch_test_list.csv)
  --concurrency N          Build concurrency (default: 8 lightweight, 4 with-timelines)
"""
from __future__ import annotations

import asyncio
import csv
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

_DEFAULT_CORPUS = Path(".tmp/batch_test_list.csv")


def _read_corpus(csv_path: Path) -> list:
    """Read statute IDs from corpus CSV (format: N,YYYY/NNN)."""
    ids = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and re.match(r'^\d{4}/\d+$', row[1]):
                ids.append(row[1])
    return ids


async def _run(args: "argparse.Namespace") -> None:
    from lawvm.graph_build import build_corpus_graph

    corpus_path = Path(args.corpus) if args.corpus else _DEFAULT_CORPUS
    if not corpus_path.exists():
        print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    statute_ids = _read_corpus(corpus_path)
    with_timelines = getattr(args, "with_timelines", False)
    concurrency = args.concurrency

    mode_note = "with timelines" if with_timelines else "lightweight (no replay)"
    print(
        f"Building corpus graph for {len(statute_ids)} statutes "
        f"({mode_note}, concurrency={concurrency})...",
        file=sys.stderr,
    )

    cg = await build_corpus_graph(
        statute_ids, concurrency=concurrency, with_timelines=with_timelines
    )
    print(
        f"Graph: {len(cg.statute_meta)} statutes loaded, "
        f"{len(cg.delegations)} delegation edges, "
        f"{len(cg.citations)} citation edges.",
        file=sys.stderr,
    )
    if cg.build_meta:
        print(
            f"Build: {cg.build_meta.built_at}  commit={cg.build_meta.lawvm_commit}  "
            f"schema={cg.build_meta.schema_version}",
            file=sys.stderr,
        )
    print()

    sid = args.statute_id

    if args.reverse_cites:
        edges = cg.reverse_citations(sid)
        cites_only = [e for e in edges if e.edge_type == "CITES"]
        print(f"CITES → {sid}: {len(cites_only)} edge(s)")
        for e in sorted(cites_only, key=lambda e: (e.source_statute_id, e.source_section)):
            src = f"§{e.source_section} " if e.source_section else ""
            tgt = f"#{e.target_section}" if e.target_section else ""
            cnt = f" x{e.count}" if e.count > 1 else ""
            print(f"  {e.source_statute_id} {src}→ {sid}{tgt}{cnt}")
        other = [e for e in edges if e.edge_type != "CITES"]
        if other:
            print(f"\nOther edges (REPEALS/ISSUED_UNDER/ISSUES) targeting {sid}:")
            for e in other:
                print(f"  {e.source_statute_id}: {e.edge_type}")

    if args.affecting_acts:
        acts = cg.affecting_acts(sid)
        print(f"Acts amending {sid}: {len(acts)}")
        for a in sorted(acts):
            print(f"  {a}")

    if args.delegates:
        edges = cg.delegation_chain(sid)
        print(f"Delegation clauses in {sid}: {len(edges)}")
        for e in sorted(edges, key=lambda e: e.section):
            print(f"  §{e.section}  [{e.delegation_type}]  eid={e.eid}")

    if args.silent_breakage:
        provision_filter = getattr(args, "provision", "") or ""
        as_of = getattr(args, "as_of", "") or ""
        results = cg.silent_breakage(sid, target_section=provision_filter, as_of=as_of)
        filter_note = f" (targeting §{provision_filter})" if provision_filter else ""
        date_note = f" as-of {as_of}" if as_of else ""
        print(f"Silent breakage — provisions citing {sid}{filter_note}{date_note}: {len(results)}")
        if not with_timelines and as_of:
            print(
                "  NOTE: active_at_date is None for all (--with-timelines not set).",
                file=sys.stderr,
            )
        results_sorted = sorted(results, key=lambda r: (r["citing_statute"], r["citing_section"]))
        for r in results_sorted:
            src = f"§{r['citing_section']} " if r["citing_section"] else ""
            tgt = f"#{r['target_section']}" if r["target_section"] else ""
            cnt = f" x{r['count']}" if r["count"] > 1 else ""
            active = (
                "" if r["active_at_date"] is None
                else (" [active]" if r["active_at_date"] else " [REPEALED]")
            )
            print(f"  {r['citing_statute']} {src}→ {sid}{tgt}{cnt}{active}")


def main(args: "argparse.Namespace") -> None:
    asyncio.run(_run(args))
