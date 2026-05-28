"""lawvm ee-bench — Estonia replay benchmark.

Indexes all tervikteksts in the RT archive, selects (base, oracle) pairs
where both have actual body content, runs replay, and reports section-level
accuracy. Results are saved for regression tracking.

Usage:
    lawvm ee-bench --label v1
    lawvm ee-bench --label v2 --laws-only
    lawvm ee-bench --show v1
    lawvm ee-bench --compare v1 v2
"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, cast

from lawvm.estonia.compare import irnode_to_ee_comparison_text
from lawvm.estonia.compare import normalize_ee_comparison_text
from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive
from lawvm.estonia.replay import replay_ee_to_pit
from lawvm.estonia.residual_reporting import (
    build_ee_punctuation_whitespace_record,
    build_ee_residual_summary,
    is_ee_punctuation_whitespace_only_difference,
)
from lawvm.tools.ee_reporting import build_ee_benchmark_reporting_summary
from lawvm.tools.section_keys import extract_ir_sections

_DEFAULT_DB = Path(__file__).parent.parent.parent.parent / "data" / "ee_riigiteataja.farchive"
_BENCH_DIR = Path(__file__).parent.parent.parent.parent / "data" / "ee_bench_runs"
_HISTORY_CSV = Path(__file__).parent.parent.parent.parent / "data" / "ee_benchmark_history.csv"
_CORPUS_CSV = (
    Path(__file__).parent.parent.parent.parent
    / "data"
    / "estonia"
    / "current_replayable_corpus.csv"
)

# Module-level state for worker processes (set before spawning ProcessPoolExecutor).
_WORKER_DB_PATH: str = ""
_WORKER_META: dict = {}

# Schemas considered "law" (Riigikogu acts)
_LAW_SCHEMAS = frozenset(["tyviseadus", "muutmisseadus"])
# Schemas considered "decree" (VV/ministerial)
_DECREE_SCHEMAS = frozenset(["maarus", "muutmismaarus", "juurakt"])


# ---------------------------------------------------------------------------
# Corpus CSV loading
# ---------------------------------------------------------------------------


def _load_corpus_csv(
    csv_path: Path,
    include_decrees: bool = True,
) -> tuple[list[tuple[str, str, str]], dict[str, tuple[int, str]]]:
    """Load (grupi_id, base_id, oracle_id) pairs and meta from a corpus CSV.

    CSV format:
      grupi_id, base_id, oracle_id, n_amendments, schema

    Returns same types as _index_corpus:
      pairs: list of (grupi_id, base_id, oracle_id)
      meta:  dict of grupi_id -> (n_amendments, title="")
    """
    _LAW = frozenset(["tyviseadus", "muutmisseadus"])
    _DECREE = frozenset(["maarus", "muutmismaarus", "juurakt"])
    allowed = _LAW | (_DECREE if include_decrees else frozenset())

    pairs: list[tuple[str, str, str]] = []
    meta: dict[str, tuple[int, str]] = {}

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            schema = row.get("schema", "").strip()
            if schema not in allowed:
                continue
            gid = row["grupi_id"].strip()
            bid = row["base_id"].strip()
            oid = row["oracle_id"].strip()
            try:
                na = int(row.get("n_amendments", 0))
            except ValueError:
                na = 0
            pairs.append((gid, bid, oid))
            meta[gid] = (na, "")

    return pairs, meta


# ---------------------------------------------------------------------------
# Corpus index
# ---------------------------------------------------------------------------


@dataclass
class _GroupInfo:
    grupi_id: str
    terviktekst_with_body: list  # [(aktViide, size), ...]
    n_amendments: int = 0
    schemas: set = field(default_factory=set)
    title: str = ""


def _index_corpus(archive: Any, include_decrees: bool = False) -> tuple[list[tuple[str, str, str]], dict[str, tuple[int, str]]]:
    """Index RT archive → list of (grupiId, base_id, oracle_id) pairs.

    Only includes groups with 2+ non-stub tervikteksts.
    Returns sorted by amendment count ascending.
    """
    conn = archive._conn  # type: ignore[union-attr]
    rows = conn.execute("SELECT DISTINCT locator FROM locator WHERE locator LIKE '%riigiteataja.ee/akt/%.xml'").fetchall()

    groups: dict[str, _GroupInfo] = {}

    for i, (url,) in enumerate(rows):
        aid = url.split("/akt/")[-1].replace(".xml", "")
        data = archive.get(url)  # type: ignore[union-attr]
        if not data or len(data) < 100:
            continue

        prefix = data[:20000]

        m_g = re.search(rb"<[^>]*terviktekstiGrupiID[^>]*>([^<]+)<", prefix)
        grupi_id = m_g.group(1).decode().strip() if m_g else None
        if not grupi_id:
            continue

        m_t = re.search(rb"<[^>]*tekstiliik[^>]*>([^<]+)<", prefix)
        tl = m_t.group(1).decode().strip() if m_t else ""

        m_ns = re.search(rb'xmlns\s*=\s*["\x27]([^"\x27]+)', prefix)
        ns = m_ns.group(1).decode() if m_ns else ""

        if grupi_id not in groups:
            groups[grupi_id] = _GroupInfo(grupi_id=grupi_id, terviktekst_with_body=[])

        g = groups[grupi_id]

        # Classify schema
        for sn in ("muutmisseadus", "muutmismaarus", "tyviseadus", "maarus", "juurakt"):
            if sn in ns:
                g.schemas.add(sn)
                break

        # Check terviktekst with actual body
        if tl == "terviktekst":
            has_body = b"<peatykk" in data or b"<paragrahv" in data
            if has_body:
                # Extract kehtivuseAlgus for chronological sorting
                m_algus = re.search(rb"<[^>]*kehtivuseAlgus[^>]*>([^<]+)<", prefix)
                algus = m_algus.group(1).decode().strip()[:10] if m_algus else "9999-99-99"
                g.terviktekst_with_body.append((aid, len(data), algus))

        # Amendment count
        n_amend = len(re.findall(rb"<[^>]*muutmismarge[^>]*>", data[:200000]))
        g.n_amendments = max(g.n_amendments, n_amend)

        # Title (from first encounter)
        if not g.title:
            m_title = re.search(rb"<[^>]*pealkiri[^>]*>([^<]+)<", prefix)
            if m_title:
                g.title = m_title.group(1).decode("utf-8", errors="replace").strip()

        if (i + 1) % 10000 == 0:
            print(f"  indexing {i + 1}/{len(rows)}...", file=sys.stderr)

    # Select pairs
    allowed_schemas = _LAW_SCHEMAS
    if include_decrees:
        allowed_schemas = allowed_schemas | _DECREE_SCHEMAS

    pairs = []
    for gid, g in groups.items():
        if not g.schemas & allowed_schemas:
            continue
        # Sort tervikteksts chronologically by kehtivuseAlgus date (not by aktViide
        # number, which is NOT chronological and leads to wrong base/oracle pairing).
        tvs = sorted(g.terviktekst_with_body, key=lambda x: x[2])  # x[2] = algus date
        if len(tvs) >= 2:
            base_id = tvs[-2][0]  # second-to-last chronologically
            oracle_id = tvs[-1][0]  # latest chronologically
            pairs.append((gid, base_id, oracle_id, g.n_amendments, g.title))

    pairs.sort(key=lambda x: x[3])
    return [(gid, bid, oid) for gid, bid, oid, _, _ in pairs], {gid: (na, title) for gid, _, _, na, title in pairs}


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------


def _get_sections(body) -> dict[str, str]:
    """Extract {address: comparison text} for all sections in a body IRNode."""
    if body is None:
        return {}
    return {
        key: normalize_ee_comparison_text(irnode_to_ee_comparison_text(section))
        for key, section in extract_ir_sections(body).items()
    }


# ---------------------------------------------------------------------------
# Run bench
# ---------------------------------------------------------------------------


@dataclass
class _BenchResult:
    grupi_id: str
    base_id: str
    oracle_id: str
    title: str
    n_ops: int
    n_divs: int
    sec_match: float
    r_secs: int
    o_secs: int
    status: str
    source_basis: str = ""
    comparison_class: str = ""
    core_benchmark: bool = True
    benchmark_reporting_stratum: str = ""
    benchmark_reporting_headline_eligible: bool = False
    adjudicated_residual_count: int = 0
    matched_current_residual_count: int = 0
    adjudicated_bucket_counts: str = ""
    unknown_current_residual_count: int = 0
    open_current_divergence_count: int = 0


def _score_one_pair(gid: str, base_id: str, oracle_id: str, title: str, archive: Any) -> _BenchResult:
    """Score a single (base, oracle) pair using the provided archive connection."""
    try:
        oracle_xml = fetch_rt_xml(oracle_id, archive=archive)
        as_of = extract_effective_date(oracle_xml) or "2026-03-24"
        r = replay_ee_to_pit(
            base_id,
            as_of=as_of,
            archive=archive,
            verbose=False,
            oracle_id=oracle_id,
        )

        r_secs = _get_sections(r.replayed.body) if r.replayed else {}
        o_secs = _get_sections(r.oracle.body) if r.oracle else {}

        if not o_secs:
            status = "EMPTY_ORACLE"
            sec_match = 0.0
        else:
            matching = sum(
                1
                for key, oracle_text in o_secs.items()
                if (key in r_secs and r_secs[key] == oracle_text)
                or (key not in r_secs and oracle_text == "")
            )
            sec_match = matching / len(o_secs)
            status = "OK" if not r.error else "ERR"
        reporting_summary = build_ee_benchmark_reporting_summary(r.source_basis, r.comparison_class)

        divergence_addresses = tuple(
            "/".join(f"{kind}:{label}" for kind, label in d.address.path)
            for d in r.divergences
        )
        residual_summary = build_ee_residual_summary(
            base_id=base_id,
            oracle_id=oracle_id,
            divergence_addresses=divergence_addresses,
        )
        residual_count = 0
        matched_current = 0
        matched_bucket_counts: Counter[str] = Counter()
        unknown_current = len(r.divergences)
        matched_addresses: set[str] = set()
        if residual_summary is not None:
            matched_current = residual_summary.matched_current_divergence_count
            residual_count = matched_current
            matched_bucket_counts.update(residual_summary.matched_current_bucket_counts)
            unknown_current = residual_summary.unknown_current_divergence_count
            matched_addresses = {
                address
                for address in divergence_addresses
                if address in residual_summary.record_by_address
            }
        punctuation_records = [
            build_ee_punctuation_whitespace_record(address)
            for address, divergence in zip(divergence_addresses, r.divergences, strict=True)
            if address not in matched_addresses
            and is_ee_punctuation_whitespace_only_difference(
                divergence.ops_text,
                divergence.consolidated_text,
            )
        ]
        if punctuation_records:
            matched_current += len(punctuation_records)
            residual_count += len(punctuation_records)
            matched_bucket_counts.update(record.bucket for record in punctuation_records)
            unknown_current = max(0, unknown_current - len(punctuation_records))
        bucket_counts = ",".join(
            f"{bucket}={count}" for bucket, count in sorted(matched_bucket_counts.items())
        )

        core_benchmark = r.source_adjudication is not None and not r.source_adjudication.oracle_suspect
        open_current = max(0, len(r.divergences) - matched_current)
        if r.comparison_class == "cross_statute_oracle_mismatch":
            unknown_current = 0
            open_current = 0

        return _BenchResult(
            grupi_id=gid,
            base_id=base_id,
            oracle_id=oracle_id,
            title=title[:60],
            n_ops=r.n_ops,
            n_divs=len(r.divergences),
            sec_match=sec_match,
            r_secs=len(r_secs),
            o_secs=len(o_secs),
            status=status,
            source_basis=r.source_basis,
            comparison_class=r.comparison_class,
            core_benchmark=core_benchmark,
            benchmark_reporting_stratum=reporting_summary["benchmark_reporting_stratum"],
            benchmark_reporting_headline_eligible=reporting_summary["benchmark_reporting_headline_eligible"],
            adjudicated_residual_count=residual_count,
            matched_current_residual_count=matched_current,
            adjudicated_bucket_counts=bucket_counts,
            unknown_current_residual_count=unknown_current,
            open_current_divergence_count=open_current,
        )
    except Exception as e:
        return _BenchResult(
            grupi_id=gid,
            base_id=base_id,
            oracle_id=oracle_id,
            title=title[:60],
            n_ops=0,
            n_divs=0,
            sec_match=0.0,
            r_secs=0,
            o_secs=0,
            status=f"EXC:{str(e)[:60]}",
            source_basis="",
            comparison_class="exception",
            core_benchmark=False,
            benchmark_reporting_stratum="EE_NONCORE_SOURCE_GAP",
            benchmark_reporting_headline_eligible=False,
            adjudicated_residual_count=0,
            matched_current_residual_count=0,
            adjudicated_bucket_counts="",
            unknown_current_residual_count=0,
            open_current_divergence_count=0,
        )


def _score_one_pair_worker(item: tuple) -> _BenchResult:
    """Top-level picklable wrapper for parallel execution.

    Opens its own Farchive per worker process using module-level globals
    set before the ProcessPoolExecutor is spawned.
    Item: (gid, base_id, oracle_id)
    """
    gid, base_id, oracle_id = item
    _, title = _WORKER_META.get(gid, (0, ""))
    archive = open_rt_archive(Path(_WORKER_DB_PATH))
    try:
        return _score_one_pair(gid, base_id, oracle_id, title, archive)
    finally:
        archive.close()


def _run_bench(
    pairs: list[tuple[str, str, str]],
    meta: dict,
    archive: Any,
    workers: int = 1,
) -> list[_BenchResult]:
    total = len(pairs)
    t0 = time.time()

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        # Communicate config to worker processes via module globals.
        global _WORKER_DB_PATH, _WORKER_META
        _WORKER_DB_PATH = str(archive._db_path)
        _WORKER_META = meta

        results: list[Optional[_BenchResult]] = [None] * total
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(_score_one_pair_worker, (gid, base_id, oracle_id)): i
                for i, (gid, base_id, oracle_id) in enumerate(pairs)
            }
            done = 0
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                results[idx] = future.result()
                done += 1
                if done % 50 == 0 or done == total:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    print(
                        f"  [{done}/{total}] {elapsed:.0f}s  {rate:.1f}/s",
                        file=sys.stderr,
                    )
        return cast(List[_BenchResult], results)

    # Sequential fallback.
    results_seq = []
    for i, (gid, base_id, oracle_id) in enumerate(pairs):
        na, title = meta.get(gid, (0, ""))
        r = _score_one_pair(gid, base_id, oracle_id, title, archive)
        results_seq.append(r)

        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            print(f"  [{i + 1}/{total}] {elapsed:.0f}s", file=sys.stderr)

    return results_seq


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_report(results: list[_BenchResult], label: str) -> None:
    ok = [r for r in results if r.status == "OK" and r.o_secs > 0]
    core = [r for r in ok if r.core_benchmark]
    noncore = [r for r in ok if not r.core_benchmark]
    empty = [r for r in results if r.status == "EMPTY_ORACLE"]
    errs = [r for r in results if r.status.startswith("E")]

    print(f"\n=== EE Bench: {label} ===")
    print(f"Total: {len(results)}, OK: {len(ok)}, Empty oracle: {len(empty)}, Errors: {len(errs)}")

    if not ok:
        print("No valid results to report.")
        return

    avg = sum(r.sec_match for r in ok) / len(ok)
    perfect = sum(1 for r in ok if r.sec_match == 1.0)
    ge90 = sum(1 for r in ok if r.sec_match >= 0.9)
    ge80 = sum(1 for r in ok if r.sec_match >= 0.8)
    with_ops = sum(1 for r in ok if r.n_ops > 0)
    avg_ops = sum(r.n_ops for r in ok) / len(ok)

    print(f"\nSection-level accuracy (N={len(ok)}):")
    print(f"  Average:     {avg:.1%}")
    print(f"  Perfect:     {perfect} ({100 * perfect / len(ok):.0f}%)")
    print(f"  >=90%%:       {ge90} ({100 * ge90 / len(ok):.0f}%)")
    print(f"  >=80%%:       {ge80} ({100 * ge80 / len(ok):.0f}%)")
    print(f"  With ops>0:  {with_ops}")
    print(f"  Avg ops:     {avg_ops:.0f}")
    if core:
        avg_core = sum(r.sec_match for r in core) / len(core)
        print(f"  Core pairs:  {len(core)}  avg={avg_core:.1%}")
    if noncore:
        counts = Counter(r.comparison_class for r in noncore)
        print("  Non-core classes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    adjudicated_rows = [r for r in ok if r.matched_current_residual_count > 0 and r.open_current_divergence_count == 0]
    open_rows = [r for r in ok if r.open_current_divergence_count > 0]
    if adjudicated_rows:
        bucket_counts = Counter()
        for row in adjudicated_rows:
            for piece in row.adjudicated_bucket_counts.split(","):
                if not piece:
                    continue
                bucket, _, count = piece.partition("=")
                try:
                    bucket_counts[bucket] += int(count)
                except ValueError:
                    continue
        counts = ", ".join(f"{bucket}={count}" for bucket, count in sorted(bucket_counts.items()))
        print(f"  Fully adjudicated residual rows: {len(adjudicated_rows)}" + (f"  buckets={counts}" if counts else ""))
    if open_rows:
        print(f"  Open unexplained residual rows: {len(open_rows)}")

    # Worst 15
    worst_core = sorted(
        [r for r in core if r.sec_match < 1.0],
        key=lambda r: (-r.open_current_divergence_count, r.sec_match, -r.n_divs),
    )
    if worst_core:
        print(f"\nWorst {min(15, len(worst_core))} core rows (by section match):")
        for r in worst_core[:15]:
            residual_tail = ""
            if r.matched_current_residual_count or r.open_current_divergence_count:
                residual_tail = f" matched={r.matched_current_residual_count} open={r.open_current_divergence_count}"
            print(
                f"  {r.base_id} {r.title[:35]:35s} sec={r.sec_match:.1%} "
                f"ops={r.n_ops:4d} rsec={r.r_secs:3d} osec={r.o_secs:3d} "
                f"class={r.comparison_class}{residual_tail}"
            )
    worst_noncore = sorted(
        noncore,
        key=lambda r: (-r.open_current_divergence_count, r.sec_match, -r.n_divs),
    )
    if worst_noncore:
        print(f"\nWorst {min(10, len(worst_noncore))} non-core rows:")
        for r in worst_noncore[:10]:
            residual_tail = ""
            if r.matched_current_residual_count or r.open_current_divergence_count:
                residual_tail = f" matched={r.matched_current_residual_count} open={r.open_current_divergence_count}"
            print(
                f"  {r.base_id} {r.title[:35]:35s} sec={r.sec_match:.1%} "
                f"ops={r.n_ops:4d} class={r.comparison_class}{residual_tail}"
            )

    # Best with ops
    best = sorted([r for r in ok if r.n_ops > 0], key=lambda r: -r.sec_match)
    if best:
        print(f"\nBest {min(10, len(best))} (with ops>0):")
        for r in best[:10]:
            print(f"  {r.base_id} {r.title[:35]:35s} sec={r.sec_match:.1%} ops={r.n_ops:4d} osec={r.o_secs:3d}")


def _save_results(results: list[_BenchResult], label: str) -> None:
    _BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _BENCH_DIR / f"{label}.csv"

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "grupi_id",
                "base_id",
                "oracle_id",
                "title",
                "n_ops",
                "n_divs",
                "sec_match",
                "r_secs",
                "o_secs",
                "status",
                "source_basis",
                "comparison_class",
                "benchmark_reporting_stratum",
                "benchmark_reporting_headline_eligible",
                "core_benchmark",
                "adjudicated_residual_count",
                "matched_current_residual_count",
                "adjudicated_bucket_counts",
                "unknown_current_residual_count",
                "open_current_divergence_count",
            ]
        )
        for r in results:
            w.writerow(
                [
                    r.grupi_id,
                    r.base_id,
                    r.oracle_id,
                    r.title,
                    r.n_ops,
                    r.n_divs,
                    f"{r.sec_match:.4f}",
                    r.r_secs,
                    r.o_secs,
                    r.status,
                    r.source_basis,
                    r.comparison_class,
                    r.benchmark_reporting_stratum,
                    "1" if r.benchmark_reporting_headline_eligible else "0",
                    "1" if r.core_benchmark else "0",
                    r.adjudicated_residual_count,
                    r.matched_current_residual_count,
                    r.adjudicated_bucket_counts,
                    r.unknown_current_residual_count,
                    r.open_current_divergence_count,
                ]
            )

    print(f"\nResults saved: {out_path}")

    # Append to history
    ok = [r for r in results if r.status == "OK" and r.o_secs > 0]
    if ok:
        avg = sum(r.sec_match for r in ok) / len(ok)
        perfect = sum(1 for r in ok if r.sec_match == 1.0)
        write_header = not _HISTORY_CSV.exists()
        with open(_HISTORY_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["label", "n_total", "n_ok", "avg_sec_match", "n_perfect", "timestamp"])
            w.writerow(
                [
                    label,
                    len(results),
                    len(ok),
                    f"{avg:.4f}",
                    perfect,
                    time.strftime("%Y-%m-%d %H:%M"),
                ]
            )


def _show_run(label: str) -> None:
    path = _BENCH_DIR / f"{label}.csv"
    if not path.exists():
        print(f"No saved run with label '{label}'. Available:", file=sys.stderr)
        for p in sorted(_BENCH_DIR.glob("*.csv")):
            print(f"  {p.stem}", file=sys.stderr)
        sys.exit(1)

    results = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            results.append(
                _BenchResult(
                    grupi_id=row["grupi_id"],
                    base_id=row["base_id"],
                    oracle_id=row["oracle_id"],
                    title=row["title"],
                    n_ops=int(row["n_ops"]),
                    n_divs=int(row["n_divs"]),
                    sec_match=float(row["sec_match"]),
                    r_secs=int(row["r_secs"]),
                    o_secs=int(row["o_secs"]),
                    status=row["status"],
                    source_basis=row.get("source_basis", ""),
                    comparison_class=row.get("comparison_class", ""),
                    benchmark_reporting_stratum=row.get("benchmark_reporting_stratum", ""),
                    benchmark_reporting_headline_eligible=row.get(
                        "benchmark_reporting_headline_eligible", "0"
                    ) in ("1", "True", "true"),
                    core_benchmark=row.get("core_benchmark", "1") in ("1", "True", "true"),
                    adjudicated_residual_count=int(row.get("adjudicated_residual_count", 0) or 0),
                    matched_current_residual_count=int(row.get("matched_current_residual_count", 0) or 0),
                    adjudicated_bucket_counts=row.get("adjudicated_bucket_counts", ""),
                    unknown_current_residual_count=int(row.get("unknown_current_residual_count", 0) or 0),
                    open_current_divergence_count=int(row.get("open_current_divergence_count", 0) or 0),
                )
            )

    _print_report(results, label)


def _compare_runs(label_a: str, label_b: str) -> None:
    def _load(label):
        path = _BENCH_DIR / f"{label}.csv"
        if not path.exists():
            print(f"No run '{label}'", file=sys.stderr)
            sys.exit(1)
        data = {}
        with open(path) as f:
            for row in csv.DictReader(f):
                data[row["grupi_id"]] = float(row["sec_match"])
        return data

        a = _load(label_a)
        b = _load(label_b)
        common = sorted(set(a) & set(b))
        diffs = [(k, a[k], b[k], b[k] - a[k]) for k in common]
        regressions = [d for d in diffs if d[3] < -0.005]
        improvements = [d for d in diffs if d[3] > 0.005]
        print(f"Comparing {label_a} vs {label_b}: {len(common)} common pairs")
        print(f"  Regressions: {len(regressions)}")
        for gid, old, new, delta in sorted(regressions, key=lambda d: d[3])[:10]:
            print(f"    {gid}: {old:.4f} → {new:.4f} ({delta:+.4f})")
        print(f"  Improvements: {len(improvements)}")
        for gid, old, new, delta in sorted(improvements, key=lambda d: -d[3])[:10]:
            print(f"    {gid}: {old:.4f} → {new:.4f} ({delta:+.4f})")


# ---------------------------------------------------------------------------
# Single-statute bench mode
# ---------------------------------------------------------------------------


def _run_single_statute(statute_id: str, args) -> None:
    """Run bench for a single statute identified by grupi_id or base_id."""
    db_path = Path(args.db) if args.db else _DEFAULT_DB
    if not db_path.exists():
        print(f"Archive not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    # Look up the pair from the corpus CSV
    corpus_csv_path = Path(args.ee_corpus) if getattr(args, "ee_corpus", None) else _CORPUS_CSV
    pair = None
    if corpus_csv_path.exists():
        with open(corpus_csv_path, newline="") as f:
            for row in csv.DictReader(f):
                gid = row["grupi_id"].strip()
                bid = row["base_id"].strip()
                oid = row["oracle_id"].strip()
                if gid == statute_id or bid == statute_id or oid == statute_id:
                    pair = (gid, bid, oid)
                    break

    if pair is None:
        print(f"Statute '{statute_id}' not found in corpus CSV ({corpus_csv_path})", file=sys.stderr)
        print("Usage: lawvm bench -j ee --statute <grupi_id|base_id|oracle_id>", file=sys.stderr)
        sys.exit(1)

    gid, base_id, oracle_id = pair
    print(f"Running single-statute bench: {gid} ({base_id} → {oracle_id})")

    try:
        archive = open_rt_archive(db_path, readonly=True)
    except Exception:
        archive = None
    try:
        result = _score_one_pair(gid, base_id, oracle_id, "", archive)
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()

    print()
    print(f"=== EE Bench: {gid} ===")
    print(f"  base   : {base_id}")
    print(f"  oracle : {oracle_id}")
    print(f"  status : {result.status}")
    print(f"  compare: {result.comparison_class}")
    print(f"  core   : {'yes' if result.core_benchmark else 'no'}")
    print(f"  ops    : {result.n_ops}")
    print(f"  divs   : {result.n_divs}")
    print(f"  sec    : {result.sec_match:.1%}")
    print(f"  r_secs : {result.r_secs}")
    print(f"  o_secs : {result.o_secs}")
    if result.adjudicated_bucket_counts:
        print(f"  buckets: {result.adjudicated_bucket_counts}")
    print(f"  open   : {result.open_current_divergence_count}")


def main(args) -> None:
    if args.show:
        _show_run(args.show)
        return

    if args.compare:
        _compare_runs(args.compare[0], args.compare[1])
        return

    if args.history:
        if not _HISTORY_CSV.exists():
            print("No history yet. Run a bench first.")
            return
        with open(_HISTORY_CSV) as f:
            print(f.read())
        return

    # Single-statute mode
    statute_id = getattr(args, "statute", None)
    if statute_id:
        _run_single_statute(statute_id, args)
        return

    # Run bench
    db_path = Path(args.db) if args.db else _DEFAULT_DB
    if not db_path.exists():
        print(f"Archive not found: {db_path}", file=sys.stderr)
        print("Run: uv run python scripts/acquire_ee_corpus.py", file=sys.stderr)
        sys.exit(1)

    label = args.label or time.strftime("ee_%Y%m%d_%H%M")

    # Corpus source: prefer curated CSV (reproducible), fall back to live index.
    corpus_csv_path = Path(args.ee_corpus) if getattr(args, "ee_corpus", None) else _CORPUS_CSV
    use_csv = corpus_csv_path.exists() and not getattr(args, "reindex", False)

    archive = open_rt_archive(db_path)
    if use_csv:
        print(f"Loading corpus from CSV: {corpus_csv_path}")
        pairs, meta = _load_corpus_csv(corpus_csv_path, include_decrees=args.include_decrees)
        print(f"Found {len(pairs)} pairs from corpus CSV")
    else:
        print(
            f"Corpus CSV not found ({corpus_csv_path}); falling back to live archive index.",
            file=sys.stderr,
        )
        print(f"Indexing RT archive ({db_path})...")
        pairs, meta = _index_corpus(archive, include_decrees=args.include_decrees)
        print(f"Found {len(pairs)} replayable groups")

    if not pairs:
        print("No replayable groups found.", file=sys.stderr)
        archive.close()
        sys.exit(1)

    # Parallelism: None means --parallel was not passed → default to cpu_count.
    # Pass --parallel 1 explicitly to force sequential (useful for debugging).
    _par = getattr(args, "parallel", None)
    workers = _par if _par is not None else max(8, os.cpu_count() or 4)
    print(f"Running bench (workers={workers})...")
    results = _run_bench(pairs, meta, archive, workers=workers)
    archive.close()

    _print_report(results, label)
    _save_results(results, label)
