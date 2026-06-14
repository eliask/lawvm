"""parse-bench — corpus-wide grammar-coverage benchmark (fi + ee free-text grammars).

This is the grammar counterpart to ``lawvm bench``.  Where ``bench`` measures
replay-vs-oracle TEXT agreement over the full pipeline (parse -> elaborate ->
apply -> materialize -> diff), ``parse-bench`` measures only whether the parser
CONSUMED every operative target of each amendment instruction into a produced op.

It dispatches on the global ``-j/--jurisdiction`` flag.  ``fi`` runs the original
token-witness coverage over the johtolause parser (this module's ``run_fi``);
``ee`` runs LABEL coverage over the Estonian amendment parser (``run_ee`` /
``estonia.coverage_audit`` — the EE analog of FI's high-signal ``unmatched_section``
tier, since the EE parser is regex/char based with no token stream).  Other
(structured-amendment) jurisdictions print a pointer to their own coverage report
and exit 0; grammar coverage is only defined where the frontend parses FREE-TEXT
amendment instructions.

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


def run_fi(args) -> None:
    """FI grammar coverage: token-witness silent-drop audit over the johtolause parser."""
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


# ---------------------------------------------------------------------------
# EE — label coverage over the Estonian amendment parser
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _EeResult:
    oid: str
    n_ops: int
    n_verb_items: int
    n_clean_items: int
    drop_shapes: tuple[tuple[str, ...], ...]
    drop_tiers: tuple[str, ...]
    # A few self-evidencing examples (item text + label) for the worklist.
    examples: tuple[tuple[str, str, str], ...]  # (tier, label, item_text)


def _ee_archive_path() -> str:
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "ee_riigiteataja.farchive")


def _ee_corpus_path() -> str:
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "estonia", "current_replayable_corpus.csv")


def _ee_oracle_ids(limit: int) -> list[str]:
    """Distinct amendment (oracle) ids from the EE replayable corpus CSV."""
    import csv

    out: list[str] = []
    seen: set[str] = set()
    with open(_ee_corpus_path(), encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            oid = (row.get("oracle_id") or "").strip()
            if oid and oid not in seen:
                seen.add(oid)
                out.append(oid)
    return out[:limit] if limit else out


def _ee_scan_one(oid: str) -> _EeResult | None:
    from farchive import Farchive
    from lawvm.estonia.fetch import fetch_rt_xml
    from lawvm.estonia.coverage_audit import audit_amendment_xml

    archive = Farchive(_ee_archive_path(), readonly=True)
    try:
        xb = fetch_rt_xml(oid, archive)
    except Exception:
        return None
    if not xb:
        return None
    try:
        cov = audit_amendment_xml(xb, sid=oid)
    except Exception:
        return None

    examples = tuple(
        (d.tier, d.label, d.item_text[:200]) for d in cov.drops[:3]
    )
    return _EeResult(
        oid=oid,
        n_ops=cov.n_ops,
        n_verb_items=cov.n_verb_items,
        n_clean_items=cov.n_clean_items,
        drop_shapes=tuple(d.shape for d in cov.drops),
        drop_tiers=tuple(d.tier for d in cov.drops),
        examples=examples,
    )


def run_ee(args) -> None:
    """EE grammar coverage: label silent-drop audit over the amendment parser.

    Unit of the coverage metric is the verb-bearing op-item.  An item is CLEAN
    when its most-specific mentioned target label matches a produced op; a DROP is
    a mentioned label no produced op covers (the EE analog of FI's silent token
    drop).  See ``estonia.coverage_audit`` — a triage worklist, not an oracle.
    """
    limit = getattr(args, "limit", 0) or 0
    workers = getattr(args, "workers", 0) or 8
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20

    ids = _ee_oracle_ids(limit)
    print(
        f"parse-bench: scanning {len(ids)} EE amendments (parse-only)...",
        file=sys.stderr,
    )

    results: list[_EeResult] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_ee_scan_one, ids, chunksize=20)):
            if r is not None:
                results.append(r)
            if i and i % 500 == 0:
                print(f"  {i}/{len(ids)}", file=sys.stderr)

    # Amendments that actually carry verb-bearing op-items.
    amendments = [r for r in results if r.n_verb_items > 0]
    total_items = sum(r.n_verb_items for r in amendments)
    clean_items = sum(r.n_clean_items for r in amendments)
    dropped = [r for r in amendments if r.drop_tiers]
    coverage = (clean_items / total_items * 100.0) if total_items else 0.0

    shape_ct: collections.Counter[tuple[str, ...]] = collections.Counter()
    tier_ct: collections.Counter[str] = collections.Counter()
    for r in dropped:
        shape_ct.update(r.drop_shapes)
        tier_ct.update(r.drop_tiers)

    if as_json:
        json.dump(
            {
                "jurisdiction": "ee",
                "unit": "verb_bearing_op_item",
                "scanned": len(results),
                "amendments": len(amendments),
                "verb_items": total_items,
                "clean": clean_items,
                "dropped_items": total_items - clean_items,
                "grammar_coverage_pct": round(coverage, 3),
                "tier_counts": dict(tier_ct),
                "top_shapes": [
                    {"shape": list(sh), "count": n} for sh, n in shape_ct.most_common(top)
                ],
                "dropped_statutes": [
                    {
                        "sid": r.oid,
                        "n_ops": r.n_ops,
                        "n_drop_spans": len(r.drop_tiers),
                    }
                    for r in sorted(dropped, key=lambda r: -len(r.drop_tiers))
                ],
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return

    print("\n=== parse-bench (grammar coverage, ee) ===")
    print(f"  amendments scanned        : {len(results)}")
    print(f"  amendments w/ verb-items  : {len(amendments)}")
    print(f"  verb-bearing op-items     : {total_items}")
    print(f"  clean (no label drop)     : {clean_items}")
    print(f"  with silent label drop    : {total_items - clean_items}")
    print(f"  GRAMMAR COVERAGE          : {coverage:.3f}%")
    print(f"\n  drop tiers: {dict(tier_ct)}")
    print(f"\n  top {top} uncovered-label shapes (grammar worklist):")
    for sh, n in shape_ct.most_common(top):
        print(f"    {n:5}  {sh}")
    print(f"\n  top {top} amendments by drop count:")
    for r in sorted(dropped, key=lambda r: -len(r.drop_tiers))[:top]:
        print(f"    {r.oid}: {len(r.drop_tiers)} drops (ops={r.n_ops})")
    print("\n  sample silent drops (self-evidencing):")
    shown = 0
    for r in sorted(dropped, key=lambda r: -len(r.drop_tiers)):
        for tier, label, item in r.examples:
            print(f"    [{tier} {label}] {r.oid}: {item}")
            shown += 1
            if shown >= top:
                break
        if shown >= top:
            break


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

# Structured-amendment jurisdictions: their amendment data is already STRUCTURED
# (no free-text instruction to silently drop tokens from), so grammar coverage in
# this silent-drop sense is undefined.  Point to each one's own coverage report.
_STRUCTURED_POINTERS = {
    "uk": "uk: use its own coverage report (uk-bench / structured-amendment adapter)",
    "us": "us: us-bench / USAmendatoryReport",
    "nz": "nz: effect-readiness report",
    "no": "no: structured-amendment adapter (no free-text grammar)",
    "se": "se: structured-amendment adapter (no free-text grammar)",
}


def main(args) -> None:
    """Dispatch parse-bench on the global -j/--jurisdiction flag."""
    jur = (getattr(args, "jurisdiction", None) or "fi").lower()
    if jur == "fi":
        run_fi(args)
        return
    if jur == "ee":
        run_ee(args)
        return
    if jur in _STRUCTURED_POINTERS:
        print(
            "parse-bench: grammar-coverage is defined only for free-text-grammar "
            "jurisdictions (fi, ee).  "
            f"{jur} consumes STRUCTURED amendment data — use its own coverage "
            f"report.  {_STRUCTURED_POINTERS[jur]}."
        )
        return
    print(
        f"parse-bench: unknown jurisdiction {jur!r}; grammar coverage is defined "
        "only for fi and ee (free-text amendment grammars)."
    )
