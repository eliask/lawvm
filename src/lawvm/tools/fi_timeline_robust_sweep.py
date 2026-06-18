"""Bounded corpus sweep for robust timeline invariant hits."""

from __future__ import annotations

import csv
import json
import sys
from argparse import Namespace
from collections import Counter
from pathlib import Path
from typing import Any, Literal, cast

from lawvm.core.invariant_profiles import core_replay_strict_profile
from lawvm.core.timeline_invariants import (
    check_all_timeline_invariants_typed,
    filter_promotable_timeline_invariant_rows,
    is_promotable_timeline_invariant_row,
    timeline_invariant_violation_row,
)
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml


def _load_corpus(
    path: Path,
    *,
    limit: int | None,
    tail: int | None,
) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    with path.open(newline="") as handle:
        sample = handle.read(2048)
        handle.seek(0)
        has_header = "statute_id" in sample.splitlines()[0]
        if has_header:
            for row in csv.DictReader(handle):
                amend = int(row.get("amendments") or row.get("amendment_count") or 0)
                sid = str(row.get("statute_id") or row.get("id") or "").strip()
                if sid:
                    rows.append((amend, sid))
        else:
            for line in handle:
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                parts = text.split(",", 1)
                if len(parts) != 2:
                    continue
                amend = int(parts[0].strip())
                sid = parts[1].strip()
                if sid:
                    rows.append((amend, sid))
    rows.sort(key=lambda item: item[0])
    if tail:
        rows = rows[-tail:]
    elif limit:
        rows = rows[:limit]
    return rows


def _decile_bucket(amend_count: int, *, max_amend: int) -> str:
    if max_amend <= 0:
        return "d0"
    bucket = min(9, (amend_count * 10) // max(max_amend, 1))
    return f"d{bucket}"


def sweep_robust_timeline_invariants(
    corpus: list[tuple[int, str]],
    *,
    mode: str = "legal_pit",
) -> dict[str, Any]:
    """Replay corpus slice and count robust-tier timeline violations by decile."""
    families = core_replay_strict_profile("timeline_sweep").timeline_invariants
    max_amend = max((count for count, _sid in corpus), default=0)
    kind_counts: Counter[str] = Counter()
    promotable_kind_counts: Counter[str] = Counter()
    observation_only_kind_counts: Counter[str] = Counter()
    decile_kind_counts: dict[str, Counter[str]] = {}
    statutes_with_hits = 0
    statutes_with_promotable_hits = 0
    per_statute: list[dict[str, Any]] = []

    for amend_count, sid in corpus:
        request = ReplayXmlRequest(
            parent_id=sid,
            mode=cast(Literal["official_consolidation", "legal_pit"], mode),
            quiet=True,
            build_full_products=True,
        )
        master = call_replay_xml(replay_xml, request=request, sinks=ReplayXmlSinks(replay_meta_out={}))
        products = master.products
        if products.timelines is None or products.materialization_spec is None:
            continue
        violations = check_all_timeline_invariants_typed(
            products.materialized_state.ir,
            products.timelines,
            str(products.materialization_spec.as_of),
            families=families,
        )
        robust = [v for v in violations if v.detail.get("tier") == "robust"]
        if not robust:
            continue
        statutes_with_hits += 1
        decile = _decile_bucket(amend_count, max_amend=max_amend)
        decile_kind_counts.setdefault(decile, Counter())
        statute_kinds: Counter[str] = Counter()
        statute_promotable_kinds: Counter[str] = Counter()
        robust_rows = [timeline_invariant_violation_row(violation) for violation in robust]
        promotable_rows = filter_promotable_timeline_invariant_rows(robust_rows)
        if promotable_rows:
            statutes_with_promotable_hits += 1
        for violation in robust:
            kind_counts[violation.kind] += 1
            decile_kind_counts[decile][violation.kind] += 1
            statute_kinds[violation.kind] += 1
            row = timeline_invariant_violation_row(violation)
            if is_promotable_timeline_invariant_row(row):
                promotable_kind_counts[violation.kind] += 1
                statute_promotable_kinds[violation.kind] += 1
            else:
                observation_only_kind_counts[violation.kind] += 1
        per_statute.append(
            {
                "statute_id": sid,
                "amendment_count": amend_count,
                "decile": decile,
                "robust_count": len(robust),
                "promotable_count": len(promotable_rows),
                "kinds": dict(statute_kinds),
                "promotable_kinds": dict(statute_promotable_kinds),
            }
        )

    return {
        "statutes_scanned": len(corpus),
        "statutes_with_robust_hits": statutes_with_hits,
        "statutes_with_promotable_hits": statutes_with_promotable_hits,
        "robust_kind_counts": dict(kind_counts),
        "promotable_kind_counts": dict(promotable_kind_counts),
        "observation_only_kind_counts": dict(observation_only_kind_counts),
        "decile_kind_counts": {
            decile: dict(counter) for decile, counter in sorted(decile_kind_counts.items())
        },
        "per_statute": per_statute,
    }


def main(args: Namespace) -> None:
    corpus_path = Path(args.corpus)
    corpus = _load_corpus(corpus_path, limit=args.limit, tail=args.tail)
    report = sweep_robust_timeline_invariants(corpus, mode=args.mode)
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        sys.stdout.write("\n")
        return
    print(f"Scanned {report['statutes_scanned']} statutes")
    print(f"Statutes with robust timeline hits: {report['statutes_with_robust_hits']}")
    print(f"Statutes with evidence-promotable hits: {report['statutes_with_promotable_hits']}")
    print(f"Robust kind counts: {report['robust_kind_counts']}")
    print(f"Promotable kind counts: {report['promotable_kind_counts']}")
    print(f"Observation-only robust kinds: {report['observation_only_kind_counts']}")
    print("By amend decile:")
    for decile, kinds in report["decile_kind_counts"].items():
        print(f"  {decile}: {kinds}")
