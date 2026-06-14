"""parse-bench — corpus-wide grammar-coverage benchmark for the FI johtolause parser.

This is the grammar counterpart to ``lawvm bench``.  Where ``bench`` measures
replay-vs-oracle TEXT agreement over the full pipeline (parse -> elaborate ->
apply -> materialize -> diff), ``parse-bench`` measures only whether the parser
CONSUMED every operative token of each amendment johtolause into a produced op.

Why a separate bench:
  * It is parse-only — no farchive replay, no oracle, no materialization — so it
    runs over the ENTIRE statute corpus (~59k, incl. the ~15k non-amendment
    enactments) in minutes, not the ~3.5k replayable subset in ~10 min.
  * It is grammar-SENSITIVE where the replay bench is blind: a johtolause can
    silently drop half its targets (a real grammar bug) and barely move the
    replay bench, because the dropped sections often are not in the oracle diff
    or the statute is not replayable at all.  A dropped target IS, by
    construction, an uncovered token span here.

The metric is the corpus-wide fraction of amendment johtolauses with NO interior
or trailing silent drop (clean parse), plus the ranked inventory of the
remaining uncovered-span shapes — the grammar worklist.

Non-amendment enactments (``... säädetään``, ministry decrees) produce zero ops
by design; they are reported separately, not counted as drops.
"""

from __future__ import annotations

import collections
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from lawvm.finland.johtolause.coverage_audit import classify_uncovered_spans


_AMENDMENT_VERB_PREFIXES = ("muute", "kumot", "lisät", "siirre", "korva")


@dataclass(frozen=True)
class _StatuteResult:
    sid: str
    is_amendment: bool
    n_ops: int
    n_drop_spans: int  # interior/trailing high-signal uncovered spans
    drop_shapes: tuple[tuple[str, ...], ...]
    drop_tiers: tuple[str, ...]


def _archive_path() -> str:
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "finlex.farchive")


def _scan_one(sid: str) -> _StatuteResult | None:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.finland.metadata import get_johtolause
    from lawvm.finland.johtolause.api import parse_clause

    store = TransparentCorpusStore(Farchive(_archive_path()))
    xb = store.read_source(sid) or store.read_amendment(sid)
    if not xb:
        return None
    try:
        johto = get_johtolause(xb) or ""
    except Exception:
        return None
    if not johto or "§" not in johto:
        return None

    head = " ".join(johto.split())[:24].lower()
    is_amendment = head.startswith(_AMENDMENT_VERB_PREFIXES)

    try:
        parsed = parse_clause(johto, statute_id=sid)
        n_ops = len(parsed.parsed_ops or [])
        spans = [
            c
            for c in classify_uncovered_spans(johto)
            if c.position in ("interior", "trailing")
            and c.tier in ("verb_no_op", "unmatched_section")
        ]
    except Exception:
        return _StatuteResult(sid, is_amendment, 0, 0, (), ())

    return _StatuteResult(
        sid=sid,
        is_amendment=is_amendment,
        n_ops=n_ops,
        n_drop_spans=len(spans),
        drop_shapes=tuple(tuple(c.span.token_cats[:8]) for c in spans),
        drop_tiers=tuple(c.tier for c in spans),
    )


def _statute_ids(limit: int) -> list[str]:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore

    store = TransparentCorpusStore(Farchive(_archive_path()))
    ids = store.list_statute_ids()
    return ids[:limit] if limit else ids


def main(args) -> None:
    limit = getattr(args, "limit", 0) or 0
    workers = getattr(args, "workers", 0) or 8
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20

    ids = _statute_ids(limit)
    print(f"parse-bench: scanning {len(ids)} statutes (parse-only)...", file=sys.stderr)

    results: list[_StatuteResult] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_scan_one, ids, chunksize=50)):
            if r is not None:
                results.append(r)
            if i and i % 10000 == 0:
                print(f"  {i}/{len(ids)}", file=sys.stderr)

    amendments = [r for r in results if r.is_amendment]
    clean = [r for r in amendments if r.n_drop_spans == 0]
    dropped = [r for r in amendments if r.n_drop_spans > 0]
    coverage = (len(clean) / len(amendments) * 100.0) if amendments else 0.0

    shape_ct: collections.Counter[tuple[str, ...]] = collections.Counter()
    tier_ct: collections.Counter[str] = collections.Counter()
    for r in dropped:
        shape_ct.update(r.drop_shapes)
        tier_ct.update(r.drop_tiers)

    if as_json:
        json.dump(
            {
                "scanned": len(results),
                "amendments": len(amendments),
                "clean": len(clean),
                "dropped": len(dropped),
                "grammar_coverage_pct": round(coverage, 3),
                "tier_counts": dict(tier_ct),
                "top_shapes": [
                    {"shape": list(sh), "count": n} for sh, n in shape_ct.most_common(top)
                ],
                "dropped_statutes": [
                    {"sid": r.sid, "n_ops": r.n_ops, "n_drop_spans": r.n_drop_spans}
                    for r in sorted(dropped, key=lambda r: -r.n_drop_spans)
                ],
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return

    print("\n=== parse-bench (grammar coverage) ===")
    print(f"  statutes scanned          : {len(results)}")
    print(f"  amendment johtolauses     : {len(amendments)}")
    print(f"  non-amendment enactments  : {len(results) - len(amendments)}")
    print(f"  clean (no silent drop)    : {len(clean)}")
    print(f"  with silent drop          : {len(dropped)}")
    print(f"  GRAMMAR COVERAGE          : {coverage:.3f}%")
    print(f"\n  drop tiers: {dict(tier_ct)}")
    print(f"\n  top {top} uncovered-span shapes (grammar worklist):")
    for sh, n in shape_ct.most_common(top):
        print(f"    {n:5}  {sh}")
    print(f"\n  top {top} statutes by drop count:")
    for r in sorted(dropped, key=lambda r: -r.n_drop_spans)[:top]:
        print(f"    {r.sid}: {r.n_drop_spans} drops (ops={r.n_ops})")
