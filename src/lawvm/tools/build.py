"""lawvm build — compile the Finnish (or Norwegian) legal graph to a persistent artifact.

Usage:
    # Finnish — from amendment corpus CSV (lightweight, no replay)
    lawvm build --corpus .tmp/batch_test_list.csv --output .tmp/corpus_graph/

    # Finnish — ALL 59K statutes from ZIP (lightweight, no replay, ~5 min)
    lawvm build --full --output .tmp/corpus_graph/

    # Finnish — amended subset WITH provision timelines (slow, uses replay)
    lawvm build --corpus .tmp/migration/expanded_batch_test_list.csv \\
                --with-timelines --output .tmp/corpus_graph_timelines/

    # Norwegian — from Lovdata bulk archive
    lawvm build --jurisdiction no --input .tmp/lovdata_lover.tar.bz2 \\
                --output .tmp/norway_graph/

Output directory:
    meta.json        BuildMeta (schema_version, lawvm_commit, built_at, corpus_size,
                     input_zip_sha256)
    statutes.json    statute_meta dict (id → {title, statute_type})
    amendments.json  amendment_index (parent → [amending act ids])
    citations.jsonl  CrossRefEdge per line (FI→FI + FI→EU)
    delegations.jsonl DelegationEdge per line
    stats.json       n_statutes, n_citation_edges, n_delegation_edges,
                     n_eu_ref_edges, n_amendment_links

    timelines/       (only with --with-timelines)
        YEAR_NUM.json  per-statute provision timelines
"""
from __future__ import annotations

import asyncio
import csv
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
from lawvm.tools._worker_pool import managed_executor
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterator, List, Optional

if TYPE_CHECKING:
    import argparse
    from lawvm.corpus_store import CorpusStore

_SCHEMA_VERSION = "11.0"
_BATCH_SIZE = 500   # statutes per asyncio.gather() call (replay/timelines path only)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return ""


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_corpus_csv(csv_path: Path) -> List[str]:
    ids = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and re.match(r'^\d{4}/\d+$', row[1]):
                ids.append(row[1])
    return ids


def _list_all_fi_statute_ids() -> List[str]:
    """Return all statute IDs present in the Finnish corpus (via farchive).

    Closes the farchive connection immediately after listing so that the file
    descriptor is not inherited by worker processes spawned via fork.
    """
    from lawvm.corpus_store import get_corpus_store
    cs = get_corpus_store()
    ids = sorted(cs.list_statute_ids())
    # Close the underlying farchive connection if accessible
    archive = getattr(cs, "_archive", None)
    if archive is not None:
        try:
            archive.close()
        except Exception:
            pass
    return ids


def _edge_to_dict(edge) -> dict:
    return dataclasses.asdict(edge)


# ---------------------------------------------------------------------------
# ProcessPoolExecutor workers — module-level for pickling
#
# The CorpusStore is process-local. Each worker process opens
# its own handles via _worker_init (called once per process by the pool).
# ---------------------------------------------------------------------------

# Per-process globals set by _worker_init
_w_corpus: "Optional[CorpusStore]" = None
_w_amendment_children: Optional[Dict[str, List[str]]] = None


def _worker_init() -> None:
    """Called once per worker process — create CorpusStore and build caches."""
    global _w_corpus, _w_amendment_children
    from lawvm.corpus_store import get_corpus_store
    _w_corpus = get_corpus_store()
    from lawvm.finland.amendment_index import get_amendment_children
    _w_amendment_children = dict(get_amendment_children())


def _worker_fn(sid: str) -> Optional[dict]:
    """Build lightweight data for one statute — runs in a worker process.

    Returns a dict with statute_id, title, statute_type, citations (list[dict]),
    delegations (list[dict]), amendment_chain.  Returns None on any error.
    """
    from lawvm.finland.cross_refs import extract_cross_refs, extract_eu_refs
    from lawvm.finland.delegation import extract_delegations

    assert _w_corpus is not None
    base_xml = _w_corpus.read_source(sid)
    if base_xml is None:
        return None

    # title
    title = ""
    m = re.search(rb'<docTitle[^>]*>(.*?)</docTitle>', base_xml, re.DOTALL)
    if m:
        title = re.sub(r'<[^>]+>', '', m.group(1).decode("utf-8", errors="replace")).strip()
        title = re.sub(r'\s+', ' ', title)

    # statute_type
    statute_type = "statute"
    m2 = re.search(rb'typeStatute[^>]+refersTo="#([^"]+)"', base_xml)
    if m2:
        statute_type = m2.group(1).decode("utf-8", errors="replace")

    # delegations + citations from consolidated ZIP
    delegations: list = []
    citations: list = []
    con_xml = _w_corpus.read_oracle(sid)
    if con_xml:
        try:
            delegations = [dataclasses.asdict(e) for e in extract_delegations(con_xml, sid)]
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
        try:
            citations = [dataclasses.asdict(e) for e in extract_cross_refs(con_xml, sid)]
            citations += [dataclasses.asdict(e) for e in extract_eu_refs(base_xml, sid)]
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass

    # Phase 8.4: section-level ISSUED_UNDER — parse preamble for "N §:n nojalla" patterns.
    # Populates target_section on existing ISSUED_UNDER edges; adds edges missing from metadata.
    try:
        from lawvm.finland.delegation import extract_asetus_authority
        auth_edges = extract_asetus_authority(base_xml, sid)
        if auth_edges:
            from collections import defaultdict as _dd
            auth_map: Dict[str, list] = _dd(list)
            for ae in auth_edges:
                if ae.parent_section:
                    auth_map[ae.parent_statute_id].append(ae.parent_section)
            existing_targets: set = set()
            for e in citations:
                if e.get("edge_type") == "ISSUED_UNDER":
                    existing_targets.add(e["target_statute_id"])
                    if e["target_statute_id"] in auth_map:
                        secs = auth_map[e["target_statute_id"]]
                        e["target_section"] = ",".join(dict.fromkeys(secs))
            for parent_id, secs in auth_map.items():
                if parent_id not in existing_targets:
                    citations.append({
                        "source_statute_id": sid,
                        "target_statute_id": parent_id,
                        "edge_type": "ISSUED_UNDER",
                        "source_section": "",
                        "target_section": ",".join(dict.fromkeys(secs)),
                        "count": 1,
                        "target_stat_hash": "",
                    })
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        pass

    # Stamp each citation edge with target's current consolidated XML hash (D2).
    # Compute once per unique target to avoid redundant reads.
    target_hashes: dict = {}
    if _w_corpus and citations:
        unique_targets = {e['target_statute_id'] for e in citations
                         if not e.get('target_stat_hash') and e.get('target_statute_id')}
        for tgt in unique_targets:
            try:
                tgt_xml = _w_corpus.read_oracle(tgt)
                if tgt_xml:
                    target_hashes[tgt] = hashlib.sha256(tgt_xml).hexdigest()[:16]
            except (NameError, TypeError, AttributeError):
                raise  # programming bugs — fail loud
            except Exception:
                pass
        for e in citations:
            tgt = e.get('target_statute_id', '')
            if tgt in target_hashes:
                e['target_stat_hash'] = target_hashes[tgt]

    amendment_chain = (_w_amendment_children or {}).get(sid, [])

    return {
        "statute_id": sid,
        "title": title,
        "statute_type": statute_type,
        "citations": citations,
        "delegations": delegations,
        "amendment_chain": amendment_chain,
    }


# ---------------------------------------------------------------------------
# Finnish build — lightweight path (ProcessPoolExecutor)
# ---------------------------------------------------------------------------

def _build_fi_lightweight_parallel(
    statute_ids: List[str],
    n_workers: int,
    verbose: bool,
) -> Iterator[Optional[dict]]:
    """Yield per-statute result dicts using ProcessPoolExecutor."""
    n_total = len(statute_ids)
    with managed_executor(
        n_workers,
        initializer=_worker_init,
        initargs=(),
    ) as pool:
        try:
            for i, result in enumerate(pool.map(_worker_fn, statute_ids, chunksize=50)):
                if verbose and (i + 1) % 500 == 0:
                    print(f"  {i + 1}/{n_total}...", file=sys.stderr, end="\r")
                yield result
        except KeyboardInterrupt:
            print("\nInterrupted — cancelling workers...", file=sys.stderr, flush=True)
            raise


# ---------------------------------------------------------------------------
# Finnish build — timelines path (asyncio + Semaphore, replay)
# ---------------------------------------------------------------------------

async def _build_fi_timelines(
    statute_ids: List[str],
    output_dir: Path,
    concurrency: int,
    verbose: bool,
) -> None:
    """Build Finnish corpus with provision timelines (replay path)."""
    from lawvm.finland.graph import build_statute_graph_fi as _build_statute_graph_fi

    output_dir.mkdir(parents=True, exist_ok=True)
    timelines_dir = output_dir / "timelines"
    timelines_dir.mkdir(exist_ok=True)

    n_total = len(statute_ids)
    print(f"Building Finnish graph (with timelines): {n_total} statutes", file=sys.stderr)

    n_ok = n_skip = n_cites = n_eu_cites = n_delegs = 0
    statutes_meta: dict = {}
    sem = asyncio.Semaphore(concurrency)

    async def _one(sid: str):
        async with sem:
            try:
                return await _build_statute_graph_fi(sid)
            except Exception as exc:
                print(f"\n  [skip] {sid}: {exc}", file=sys.stderr)
                return None

    with (
        open(output_dir / "citations.jsonl", "w", encoding="utf-8") as cite_f,
        open(output_dir / "delegations.jsonl", "w", encoding="utf-8") as delg_f,
    ):
        for batch_start in range(0, n_total, _BATCH_SIZE):
            batch = statute_ids[batch_start:batch_start + _BATCH_SIZE]
            batch_end = min(batch_start + _BATCH_SIZE, n_total)
            if verbose:
                print(f"  [{batch_start+1}–{batch_end}/{n_total}]", file=sys.stderr, end="\r")
            results = await asyncio.gather(*[_one(sid) for sid in batch])

            for sg in results:
                if sg is None:
                    n_skip += 1
                    continue
                n_ok += 1
                statutes_meta[sg.statute_id] = {
                    "title": sg.title,
                    "statute_type": sg.statute_type,
                }
                for edge in sg.citations:
                    d = _edge_to_dict(edge)
                    cite_f.write(json.dumps(d, ensure_ascii=False) + "\n")
                    if d.get("target_statute_id", "").startswith("eu/"):
                        n_eu_cites += 1
                    else:
                        n_cites += 1
                for edge in sg.delegations:
                    delg_f.write(json.dumps(_edge_to_dict(edge), ensure_ascii=False) + "\n")
                    n_delegs += 1
                if sg.timelines:
                    tl_key = sg.statute_id.replace("/", "_")
                    tl_data: dict = {}
                    for addr, tl in sg.timelines.items():
                        addr_str = "/".join(f"{k}:{v}" for k, v in addr.path)
                        tl_data[addr_str] = [
                            {
                                "effective": v.effective,
                                "enacted": v.enacted,
                                "expires": v.expires,
                                "source": v.source.statute_id if v.source else None,
                                "content_hash": v.content_hash,
                                "content_kind": v.content.kind if v.content else None,
                            }
                            for v in tl.versions
                        ]
                    with open(timelines_dir / f"{tl_key}.json", "w", encoding="utf-8") as tf:
                        json.dump(tl_data, tf, ensure_ascii=False)

    if verbose:
        print(file=sys.stderr)

    _write_fi_artifact(
        output_dir, statutes_meta, n_ok, n_skip, n_cites, n_eu_cites, n_delegs,
        with_timelines=True,
    )


# ---------------------------------------------------------------------------
# Shared artifact writer
# ---------------------------------------------------------------------------

def _write_fi_artifact(
    output_dir: Path,
    statutes_meta: dict,
    n_ok: int,
    n_skip: int,
    n_cites: int,
    n_eu_cites: int,
    n_delegs: int,
    with_timelines: bool,
) -> None:
    from lawvm.finland.amendment_index import get_amendment_children

    with open(output_dir / "statutes.json", "w", encoding="utf-8") as f:
        json.dump(statutes_meta, f, ensure_ascii=False, indent=2)

    amendment_index = dict(get_amendment_children())
    with open(output_dir / "amendments.json", "w", encoding="utf-8") as f:
        json.dump(amendment_index, f, ensure_ascii=False, indent=2)

    meta = {
        "schema_version": _SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "lawvm_commit": _git_commit(),
        "corpus_size": n_ok,
        "jurisdiction": "fi",
        "with_timelines": with_timelines,
    }
    with open(output_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    n_amendment_links = sum(len(v) for v in amendment_index.values())
    stats = {
        "n_statutes": n_ok,
        "n_skipped": n_skip,
        "n_citation_edges": n_cites,
        "n_eu_ref_edges": n_eu_cites,
        "n_delegation_edges": n_delegs,
        "n_amendment_links": n_amendment_links,
    }
    with open(output_dir / "stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(f"Build complete → {output_dir}", file=sys.stderr)
    print(f"  statutes:      {n_ok:>7}", file=sys.stderr)
    print(f"  citations:     {n_cites:>7}  (FI→FI)", file=sys.stderr)
    print(f"  eu_refs:       {n_eu_cites:>7}  (FI→EU)", file=sys.stderr)
    print(f"  delegations:   {n_delegs:>7}", file=sys.stderr)
    print(f"  amend_links:   {n_amendment_links:>7}", file=sys.stderr)
    if n_skip:
        print(f"  skipped:       {n_skip:>7}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Norwegian build
# ---------------------------------------------------------------------------

async def _build_no(
    input_path: Path,
    output_dir: Path,
    verbose: bool,
    amendment_archives: Optional[List[Path]] = None,
) -> None:
    """Build Norwegian corpus graph from Lovdata tar.bz2 and write to output_dir."""
    from lawvm.core.timeline import compile_timelines
    from lawvm.norway.grafter import (
        iter_no_document_change_ops,
        open_lovdata_amendment_archive,
        open_lovdata_archive,
        parse_no_statute,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Building Norwegian graph from {input_path} ...", file=sys.stderr)

    statutes_meta: dict = {}
    amendment_index: dict[str, list[str]] = {}
    n_ok = 0
    n_skip = 0
    n_provisions = 0
    n_amendment_links = 0
    skipped_statutes: list[dict[str, str]] = []

    with (
        open(output_dir / "citations.jsonl", "w", encoding="utf-8") as _cite_f,
        open(output_dir / "delegations.jsonl", "w", encoding="utf-8") as _delg_f,
    ):
        _delg_f.write("")  # no delegation extraction for Norway yet
        for sid, html_bytes in open_lovdata_archive(str(input_path)):
            try:
                statute = parse_no_statute(html_bytes, sid)
                tl = compile_timelines(statute, [])
                n_provisions += len(tl)
                statutes_meta[sid] = {
                    "title": statute.title,
                    "statute_type": "lov",
                    "n_provisions": len(tl),
                }
                n_ok += 1
                if verbose and n_ok % 50 == 0:
                    print(f"  {n_ok} statutes parsed...", file=sys.stderr, end="\r")
            except Exception as exc:
                n_skip += 1
                skipped_statutes.append(
                    {
                        "rule_id": "no_build_statute_parse_skipped",
                        "phase": "build",
                        "family": "source_pathology",
                        "reason": "Norway build skipped statute after parse or timeline compilation failure",
                        "statute_id": sid,
                        "error": str(exc),
                    }
                )
                if verbose:
                    print(f"\n  [skip] {sid}: {exc}", file=sys.stderr)

        for archive_path in amendment_archives or []:
            if verbose:
                print(f"\n  indexing amendments from {archive_path} ...", file=sys.stderr)
            for source_id, html_bytes in open_lovdata_amendment_archive(str(archive_path)):
                try:
                    for base_id, ops in iter_no_document_change_ops(html_bytes, source_id):
                        if not ops:
                            continue
                        bucket = amendment_index.setdefault(base_id, [])
                        if source_id not in bucket:
                            bucket.append(source_id)
                            n_amendment_links += 1
                except Exception as exc:
                    if verbose:
                        print(f"\n  [skip amendment] {source_id}: {exc}", file=sys.stderr)

    with open(output_dir / "statutes.json", "w", encoding="utf-8") as f:
        json.dump(statutes_meta, f, ensure_ascii=False, indent=2)

    with open(output_dir / "amendments.json", "w", encoding="utf-8") as f:
        json.dump(amendment_index, f, ensure_ascii=False, indent=2)

    meta = {
        "schema_version": _SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "lawvm_commit": _git_commit(),
        "corpus_size": n_ok,
        "jurisdiction": "no",
        "with_timelines": False,
        "input_zip_sha256": _sha256_file(str(input_path)),
    }
    with open(output_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    stats = {
        "n_statutes": n_ok,
        "n_skipped": n_skip,
        "n_provisions": n_provisions,
        "n_citation_edges": 0,
        "n_eu_ref_edges": 0,
        "n_delegation_edges": 0,
        "n_amendment_links": n_amendment_links,
        "skipped_statutes": skipped_statutes,
    }
    with open(output_dir / "stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    if verbose:
        print(file=sys.stderr)
    print(f"Build complete → {output_dir}", file=sys.stderr)
    print(f"  statutes:      {n_ok:>7}", file=sys.stderr)
    print(f"  provisions:    {n_provisions:>7}", file=sys.stderr)
    if n_skip:
        print(f"  skipped:       {n_skip:>7}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args: "argparse.Namespace") -> None:
    jurisdiction = getattr(args, "jurisdiction", "fi") or "fi"
    output_dir = Path(args.output)
    verbose = getattr(args, "verbose", False)

    if jurisdiction == "no":
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
            sys.exit(1)
        amendment_archives = [Path(p) for p in (getattr(args, "amendment_archives", None) or [])]
        for archive_path in amendment_archives:
            if not archive_path.exists():
                print(f"ERROR: amendment archive not found: {archive_path}", file=sys.stderr)
                sys.exit(1)
        asyncio.run(_build_no(input_path, output_dir, verbose, amendment_archives))
        return

    # Finnish build
    with_timelines = getattr(args, "with_timelines", False)

    if getattr(args, "full", False):
        print("Enumerating all statutes from farchive...", file=sys.stderr)
        statute_ids = _list_all_fi_statute_ids()
        print(f"Found {len(statute_ids)} statutes.", file=sys.stderr)
    elif getattr(args, "corpus", None):
        corpus_path = Path(args.corpus)
        if not corpus_path.exists():
            print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
            sys.exit(1)
        statute_ids = _read_corpus_csv(corpus_path)
        print(f"Corpus: {len(statute_ids)} statutes from {corpus_path}", file=sys.stderr)
    else:
        print("ERROR: specify --corpus CSV or --full", file=sys.stderr)
        sys.exit(1)

    if with_timelines:
        # Replay path — asyncio + Semaphore (Stanza/LLM can't fork)
        concurrency = getattr(args, "concurrency", 4) or 4
        asyncio.run(_build_fi_timelines(
            statute_ids=statute_ids,
            output_dir=output_dir,
            concurrency=concurrency,
            verbose=verbose,
        ))
        return

    # Lightweight path — ProcessPoolExecutor (pure CPU, no I/O to await)
    n_workers = getattr(args, "concurrency", None) or max(8, os.cpu_count() or 4)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"Building Finnish graph (lightweight): {len(statute_ids)} statutes, "
        f"{n_workers} workers",
        file=sys.stderr,
    )

    n_ok = n_skip = n_cites = n_eu_cites = n_delegs = 0
    statutes_meta: dict = {}

    with (
        open(output_dir / "citations.jsonl", "w", encoding="utf-8") as cite_f,
        open(output_dir / "delegations.jsonl", "w", encoding="utf-8") as delg_f,
    ):
        for result in _build_fi_lightweight_parallel(
            statute_ids, n_workers, verbose
        ):
            if result is None:
                n_skip += 1
                continue
            n_ok += 1
            sid = result["statute_id"]
            statutes_meta[sid] = {
                "title": result["title"],
                "statute_type": result["statute_type"],
            }
            for edge_dict in result["citations"]:
                cite_f.write(json.dumps(edge_dict, ensure_ascii=False) + "\n")
                if edge_dict.get("target_statute_id", "").startswith("eu/"):
                    n_eu_cites += 1
                else:
                    n_cites += 1
            for edge_dict in result["delegations"]:
                delg_f.write(json.dumps(edge_dict, ensure_ascii=False) + "\n")
                n_delegs += 1

    if verbose:
        print(file=sys.stderr)

    _write_fi_artifact(
        output_dir, statutes_meta, n_ok, n_skip, n_cites, n_eu_cites, n_delegs,
        with_timelines=False,
    )
