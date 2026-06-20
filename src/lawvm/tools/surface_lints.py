"""surface-lints — corpus-wide, parse-only Legal Surface Graph lint report.

This is the analyzer OUTPUT of the Legal Surface Graph spine: it sweeps the FI
corpus, builds the graph for each statute, derives the surface lints, and tallies
them into a single worklist report. It is the lint-layer counterpart to
``refs-bench`` (reference coverage) and ``parse-bench`` (grammar coverage).

It is parse-only and replay-free — like the other benches it just reads each
statute's cached body XML from the farchive and runs
``build_legal_surface_graph`` -> ``lint_surface_graph`` READ-ONLY. No oracle
replay, no apply, no materialize, no writes to the corpus.

What it tallies, over the scanned statutes:

* per-``lint_kind`` counts (the lint worklist, sorted by frequency).
* per-``severity`` counts (info / warning / blocker / bug).
* a graph node-kind CENSUS (count of each ``node_kind`` minted across the
  corpus) — so the report doubles as a graph-coverage census.
* the top-N statutes by lint count, each with a few example lint messages.
* an "errored" bucket: any statute whose graph build / lint pass raises is
  recorded by id with its error — never silently skipped (fail-loud).

The statute-name registry and the EU-nickname registry are loaded ONCE per
worker process and passed into ``build_legal_surface_graph`` so the
resolution-dependent reference lints (broken / open / statute-only / ambiguous)
actually fire. If the registry artifact is absent the sweep runs WITHOUT
registries and the report notes it.
"""

from __future__ import annotations

import collections
import json
import os
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Per-worker corpus + registry state (loaded once per process).
# ---------------------------------------------------------------------------
#
# The statute-name registry is large (~60k entries) and expensive to rebuild;
# rather than pickle it across the ProcessPoolExecutor boundary we load it ONCE
# per worker process into these module-level caches, lazily on first scan. This
# is the standard "load once per worker" pattern these benches use for the store.
_STORE = None
_STATUTE_REGISTRY: object | None = None
_EU_REGISTRY: object | None = None
_REGISTRIES_LOADED = False
# True once we have determined whether the registry artifact exists. The parent
# process probes this up front (see ``registries_available``) so the report can
# state plainly whether resolution-dependent lints were enabled.
_REGISTRY_ARTIFACT_MISSING = False


def _archive_path() -> str:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "finlex.farchive")


def _get_store():
    global _STORE
    if _STORE is None:
        from farchive import Farchive
        from lawvm.finland.transparent_store import TransparentCorpusStore

        _STORE = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    return _STORE


def registries_available() -> bool:
    """True iff the statute-name registry artifact exists on disk.

    Probed once in the parent process so the report can state whether the
    resolution-dependent reference lints were enabled.
    """
    from lawvm.finland.references.registries.statute_name import (
        default_artifact_path,
    )

    return default_artifact_path().exists()


def _load_registries() -> tuple[object | None, object | None]:
    """Load (statute_name_registry, eu_nickname_registry) once per worker.

    Returns ``(None, None)`` if the statute-name artifact is absent — the sweep
    then runs without registries (resolution-dependent reference lints stay
    inert) and the parent reports the degraded mode.
    """
    global _STATUTE_REGISTRY, _EU_REGISTRY, _REGISTRIES_LOADED
    global _REGISTRY_ARTIFACT_MISSING
    if _REGISTRIES_LOADED:
        return _STATUTE_REGISTRY, _EU_REGISTRY
    _REGISTRIES_LOADED = True
    from lawvm.finland.references.registries import eu_nickname
    from lawvm.finland.references.registries.statute_name import (
        default_artifact_path,
        load_statute_name_registry,
    )

    path = default_artifact_path()
    if not path.exists():
        _REGISTRY_ARTIFACT_MISSING = True
        _STATUTE_REGISTRY = None
        _EU_REGISTRY = None
        return None, None
    _STATUTE_REGISTRY = load_statute_name_registry(path)
    _EU_REGISTRY = eu_nickname
    return _STATUTE_REGISTRY, _EU_REGISTRY


def _read_body(store, sid: str) -> bytes | None:
    """Best available body text for surface-graph building.

    Delegates to :func:`read_reference_body`: the graph (definitions, references,
    temporal, ...) lives in the consolidated body, so prefer the oracle; fall
    back to enacted source / amendment when the oracle is absent OR a
    ``contentAbsent`` stub (repealed/expired statutes), so those statutes still
    contribute their full surface. All archive-only reads — no replay.
    """
    from lawvm.finland.legal_surface.body_source import read_reference_body

    return read_reference_body(store, sid)


@dataclass(frozen=True)
class _LintResult:
    sid: str
    # lint_kind -> count over this statute
    lint_kind_counts: tuple[tuple[str, int], ...]
    # severity -> count over this statute
    severity_counts: tuple[tuple[str, int], ...]
    # node_kind -> count over this statute's graph
    node_kind_counts: tuple[tuple[str, int], ...]
    n_lints: int
    n_nodes: int
    # up to a few (lint_kind, severity, message) examples for the worklist
    examples: tuple[tuple[str, str, str], ...]
    # populated only when the build/lint raised (fail-loud bucket)
    error: str | None = None


def _scan_one(sid: str) -> _LintResult:
    """Build the graph + derive lints for one statute. Fail-loud on errors.

    Returns a ``_LintResult`` always (even on error — the ``error`` field is set
    and the statute is counted in the errored bucket, never silently skipped).
    """
    from lawvm.finland.legal_surface.graph_build import (
        build_legal_surface_graph,
        lint_surface_graph,
    )

    store = _get_store()
    try:
        xb = _read_body(store, sid)
    except Exception:
        return _LintResult(
            sid=sid,
            lint_kind_counts=(),
            severity_counts=(),
            node_kind_counts=(),
            n_lints=0,
            n_nodes=0,
            examples=(),
            error="read_body: " + traceback.format_exc(limit=2).strip(),
        )
    if not xb:
        # No body available — not an error, just nothing to analyze.
        return _LintResult(
            sid=sid,
            lint_kind_counts=(),
            severity_counts=(),
            node_kind_counts=(),
            n_lints=0,
            n_nodes=0,
            examples=(),
            error=None,
        )

    statute_registry, eu_registry = _load_registries()

    try:
        graph = build_legal_surface_graph(
            xb,
            sid,
            statute_registry=statute_registry,
            eu_registry=eu_registry,
        )
        report = lint_surface_graph(graph)
    except Exception:
        return _LintResult(
            sid=sid,
            lint_kind_counts=(),
            severity_counts=(),
            node_kind_counts=(),
            n_lints=0,
            n_nodes=0,
            examples=(),
            error=traceback.format_exc(limit=4).strip(),
        )

    node_kind_ct: collections.Counter[str] = collections.Counter()
    for node in graph.nodes.values():
        node_kind_ct[node.node_kind] += 1

    lint_kind_ct: collections.Counter[str] = collections.Counter()
    sev_ct: collections.Counter[str] = collections.Counter()
    examples: list[tuple[str, str, str]] = []
    for lint in report.lints:
        lint_kind_ct[lint.lint_kind] += 1
        sev_ct[lint.severity] += 1
        if len(examples) < 3:
            examples.append((lint.lint_kind, lint.severity, lint.message[:200]))

    return _LintResult(
        sid=sid,
        lint_kind_counts=tuple(sorted(lint_kind_ct.items())),
        severity_counts=tuple(sorted(sev_ct.items())),
        node_kind_counts=tuple(sorted(node_kind_ct.items())),
        n_lints=len(report.lints),
        n_nodes=len(graph.nodes),
        examples=tuple(examples),
        error=None,
    )


def _statute_ids(limit: int) -> list[str]:
    store = _get_store()
    ids = store.list_statute_ids()
    return ids[:limit] if limit else ids


# Stable display order for the four severities (unknown severities appended).
_SEVERITY_ORDER = ("bug", "blocker", "warning", "info")


def _ordered_severity_items(
    sev_ct: collections.Counter[str],
) -> list[tuple[str, int]]:
    out = [(s, sev_ct.get(s, 0)) for s in _SEVERITY_ORDER if s in sev_ct]
    extra = sorted(k for k in sev_ct if k not in _SEVERITY_ORDER)
    out.extend((k, sev_ct[k]) for k in extra)
    return out


@dataclass
class _Aggregate:
    statutes_scanned: int
    statutes_with_lints: int
    total_lints: int
    total_nodes: int
    lint_kind_ct: collections.Counter[str]
    severity_ct: collections.Counter[str]
    node_kind_ct: collections.Counter[str]
    errored: list[tuple[str, str]]  # (sid, error)
    results: list[_LintResult]


def _aggregate(results: list[_LintResult]) -> _Aggregate:
    lint_kind_ct: collections.Counter[str] = collections.Counter()
    severity_ct: collections.Counter[str] = collections.Counter()
    node_kind_ct: collections.Counter[str] = collections.Counter()
    errored: list[tuple[str, str]] = []
    total_lints = 0
    total_nodes = 0
    statutes_with_lints = 0
    for r in results:
        if r.error is not None:
            errored.append((r.sid, r.error))
            continue
        for k, n in r.lint_kind_counts:
            lint_kind_ct[k] += n
        for s, n in r.severity_counts:
            severity_ct[s] += n
        for k, n in r.node_kind_counts:
            node_kind_ct[k] += n
        total_lints += r.n_lints
        total_nodes += r.n_nodes
        if r.n_lints:
            statutes_with_lints += 1
    return _Aggregate(
        statutes_scanned=len(results),
        statutes_with_lints=statutes_with_lints,
        total_lints=total_lints,
        total_nodes=total_nodes,
        lint_kind_ct=lint_kind_ct,
        severity_ct=severity_ct,
        node_kind_ct=node_kind_ct,
        errored=errored,
        results=results,
    )


def _worst_statutes(results: list[_LintResult], top: int) -> list[_LintResult]:
    scored = [r for r in results if r.error is None and r.n_lints]
    return sorted(scored, key=lambda r: -r.n_lints)[:top]


def run(args) -> None:
    """Corpus-wide Legal Surface Graph lint report (fi)."""
    limit = getattr(args, "limit", 0) or 0
    workers = getattr(args, "workers", 0) or 0
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20

    if not workers:
        workers = min(8, max(1, (os.cpu_count() or 2) - 2))

    have_registries = registries_available()

    ids = _statute_ids(limit)
    print(
        f"surface-lints: scanning {len(ids)} statutes "
        f"(parse-only, no replay) with {workers} workers; "
        f"registries={'on' if have_registries else 'ABSENT (degraded)'}...",
        file=sys.stderr,
    )

    results: list[_LintResult] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_scan_one, ids, chunksize=25)):
            results.append(r)
            if i and i % 5000 == 0:
                print(f"  {i}/{len(ids)}", file=sys.stderr)

    agg = _aggregate(results)
    worst = _worst_statutes(results, top)

    if as_json:
        payload: dict = {
            "jurisdiction": "fi",
            "metric": "surface_graph_lints",
            "registries_enabled": have_registries,
            "statutes_scanned": agg.statutes_scanned,
            "statutes_with_lints": agg.statutes_with_lints,
            "statutes_errored": len(agg.errored),
            "total_lints": agg.total_lints,
            "total_graph_nodes": agg.total_nodes,
            "lint_kind_counts": dict(agg.lint_kind_ct.most_common()),
            "severity_counts": dict(_ordered_severity_items(agg.severity_ct)),
            "node_kind_census": dict(agg.node_kind_ct.most_common()),
            "top_statutes": [
                {
                    "sid": r.sid,
                    "lints": r.n_lints,
                    "nodes": r.n_nodes,
                    "examples": [
                        {"lint_kind": lk, "severity": sv, "message": msg}
                        for lk, sv, msg in r.examples
                    ],
                }
                for r in worst
            ],
            "errored": [
                {"sid": sid, "error": err} for sid, err in agg.errored[:top]
            ],
        }
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
        return

    print("\n=== surface-lints (Legal Surface Graph lint report, fi) ===")
    if not have_registries:
        print(
            "  ! registry artifact ABSENT — ran WITHOUT statute-name / EU "
            "registries; resolution-dependent reference lints did NOT fire."
        )
    print(f"  statutes scanned          : {agg.statutes_scanned}")
    print(f"  statutes with lints       : {agg.statutes_with_lints}")
    print(f"  statutes errored          : {len(agg.errored)}")
    print(f"  total lints               : {agg.total_lints}")
    print(f"  total graph nodes         : {agg.total_nodes}")

    print("\n  per-lint_kind counts (the lint worklist):")
    if agg.lint_kind_ct:
        for kind, n in agg.lint_kind_ct.most_common():
            print(f"    {n:8}  {kind}")
    else:
        print("    (no lints fired)")

    print("\n  per-severity counts:")
    if agg.severity_ct:
        for sev, n in _ordered_severity_items(agg.severity_ct):
            print(f"    {n:8}  {sev}")
    else:
        print("    (no lints fired)")

    print("\n  node-kind census (graph coverage):")
    if agg.node_kind_ct:
        for kind, n in agg.node_kind_ct.most_common():
            print(f"    {n:8}  {kind}")
    else:
        print("    (no nodes minted)")

    print(f"\n  top {top} statutes by lint count:")
    if worst:
        for r in worst:
            print(f"    {r.sid}: {r.n_lints} lints ({r.n_nodes} nodes)")
            for lk, sv, msg in r.examples:
                print(f"        [{sv} / {lk}] {msg}")
    else:
        print("    (none)")

    if agg.errored:
        print(f"\n  ERRORED statutes ({len(agg.errored)}; showing up to {top}):")
        for sid, err in agg.errored[:top]:
            first_line = err.splitlines()[-1] if err else err
            print(f"    {sid}: {first_line}")


def main(args) -> None:
    """Dispatch on the global -j/--jurisdiction flag (only fi has a graph today)."""
    jur = (getattr(args, "jurisdiction", None) or "fi").lower()
    if jur == "fi":
        run(args)
        return
    print(
        f"surface-lints: the Legal Surface Graph is defined for fi only; "
        f"{jur!r} has no surface graph."
    )
