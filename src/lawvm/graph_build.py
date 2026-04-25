"""Graph-build orchestration outside the core kernel types.

The graph data and query surface live in ``lawvm.core.graph``. This module owns
the jurisdiction dispatch, async orchestration, partial-failure policy, and
reproducibility metadata gathering needed to build those graph objects.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from typing import List, Optional

from lawvm.contracts import ProcessingStatus
from lawvm.core.graph import BuildMeta, CorpusGraph, StatuteGraph


async def build_statute_graph(statute_id: str, jurisdiction: str = "fi") -> StatuteGraph:
    """Build a StatuteGraph for a single statute."""
    if jurisdiction == "fi":
        from lawvm.finland.graph import build_statute_graph_fi

        return await build_statute_graph_fi(statute_id)
    raise NotImplementedError(
        f"build_statute_graph: jurisdiction {jurisdiction!r} not yet supported"
    )


def build_statute_graph_sync(statute_id: str, jurisdiction: str = "fi") -> StatuteGraph:
    """Synchronous wrapper for build_statute_graph()."""
    return asyncio.run(build_statute_graph(statute_id, jurisdiction=jurisdiction))


async def build_corpus_graph(
    statute_ids: List[str],
    jurisdiction: str = "fi",
    concurrency: int = 4,
    with_timelines: bool = True,
) -> CorpusGraph:
    """Build a CorpusGraph for a set of statutes."""
    if jurisdiction != "fi":
        raise NotImplementedError(
            f"build_corpus_graph: jurisdiction {jurisdiction!r} not yet supported"
        )

    from lawvm.finland.amendment_index import get_amendment_children
    from lawvm.finland.graph import (
        build_statute_graph_fi,
        build_statute_graph_fi_lightweight,
    )

    semaphore = asyncio.Semaphore(concurrency)

    async def _build_one(sid: str) -> tuple[str, Optional[StatuteGraph], Optional[str]]:
        async with semaphore:
            try:
                if with_timelines:
                    return sid, await build_statute_graph_fi(sid), None
                return sid, await build_statute_graph_fi_lightweight(sid), None
            except (NameError, TypeError, AttributeError, SyntaxError):
                raise
            except Exception as exc:
                print(f"[graph] {sid}: skipped — {exc}", file=sys.stderr)
                return sid, None, str(exc)

    results = await asyncio.gather(*[_build_one(sid) for sid in statute_ids])

    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        commit = ""
    meta = BuildMeta(
        built_at=datetime.now(timezone.utc).isoformat(),
        lawvm_commit=commit,
        corpus_size=len(statute_ids),
        failed_statutes=sum(1 for _sid, sg, _err in results if sg is None),
    )

    failure_rows = sorted(
        (
            {"statute_id": sid, "error": error or ""}
            for sid, sg, error in results
            if sg is None
        ),
        key=lambda row: (row["statute_id"], row["error"]),
    )
    status = ProcessingStatus(
        kind="partial" if failure_rows else "complete",
        blockers=tuple(
            f"graph_build_failed:{row['statute_id']}" for row in failure_rows
        ),
    )

    graph = CorpusGraph(
        amendment_index=dict(get_amendment_children()),
        build_meta=meta,
        build_failures=failure_rows,
        processing_status=status,
    )
    for sid, sg, error in results:
        if sg is None:
            continue
        graph.statute_meta[sg.statute_id] = {
            "title": sg.title,
            "statute_type": sg.statute_type,
        }
        if sg.timelines:
            graph.timelines[sg.statute_id] = sg.timelines
        graph.delegations.extend(sg.delegations)
        graph.citations.extend(sg.citations)

    return graph


def build_corpus_graph_sync(
    statute_ids: List[str],
    jurisdiction: str = "fi",
    concurrency: int = 4,
    with_timelines: bool = True,
) -> CorpusGraph:
    """Synchronous wrapper for build_corpus_graph()."""
    return asyncio.run(
        build_corpus_graph(
            statute_ids,
            jurisdiction=jurisdiction,
            concurrency=concurrency,
            with_timelines=with_timelines,
        )
    )
