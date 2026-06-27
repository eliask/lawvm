"""parse-bench — corpus-wide grammar-coverage benchmark (fi + ee free-text grammars).

This is the grammar counterpart to ``lawvm bench``.  Where ``bench`` measures
replay-vs-oracle TEXT agreement over the full pipeline (parse -> elaborate ->
apply -> materialize -> diff), ``parse-bench`` measures only whether the parser
CONSUMED every operative target of each amendment instruction into a produced op.

It dispatches on the global ``-j/--jurisdiction`` flag.  Two distinct metrics:

* ``fi``/``ee`` — GRAMMAR coverage (free-text amendment grammars).  ``fi`` runs the
  original token-witness coverage over the johtolause parser (``run_fi``); ``ee``
  runs LABEL coverage over the Estonian amendment parser (``run_ee`` /
  ``estonia.coverage_audit`` — the EE analog of FI's high-signal
  ``unmatched_section`` tier, since the EE parser is regex/char based with no token
  stream).
* ``us``/``nz``/``uk`` — LOWERING coverage (a DIFFERENT metric, NOT grammar
  coverage).  These jurisdictions consume pre-typed/structured amendment data, so
  there is no free-text instruction to silently drop tokens from.  Instead we
  measure: of all pre-typed amendment instructions/effects already in the corpus,
  what fraction LOWERED into produced ops (``run_us``/``run_nz``/``run_uk``), plus
  the ranked worklist of unhandled/rejected instruction shapes.  These adapters
  reuse each frontend's own existing instruments read-only and are replay-free.

``no``/``se`` have neither a free-text grammar nor a lowering adapter yet and print
a pointer to their own coverage report.

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

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
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

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
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


def _ee_all_akt_locators(limit: int) -> list[str]:
    """Every akt-document locator in the EE farchive (full corpus, parse-only).

    parse-bench scans the ENTIRE corpus cheaply (no replay), so it enumerates
    every stored akt rather than the ~2k replayable-pair subset — the silent-drop
    signal is most valuable on the non-replayable tail, where grammar gaps hide
    and the replay bench can never reach.  Non-amendment akts contribute zero
    verb-bearing op-items and drop out of the coverage metric.
    """
    from farchive import Farchive

    archive = Farchive(_ee_archive_path(), readonly=True)
    locs = sorted(
        loc
        for loc in archive.locators("%/akt/%")
        if "/akt/" in loc and loc.endswith(".xml")
    )
    return locs[:limit] if limit else locs


def _ee_scan_one(locator: str) -> _EeResult | None:
    from farchive import Farchive
    from lawvm.estonia.coverage_audit import audit_amendment_xml

    aid = locator.rsplit("/akt/", 1)[-1]
    if aid.endswith(".xml"):
        aid = aid[:-4]
    archive = Farchive(_ee_archive_path(), readonly=True)
    try:
        xb = archive.get(locator)
    except Exception:
        return None
    if not xb:
        return None
    try:
        cov = audit_amendment_xml(xb, sid=aid)
    except Exception:
        return None

    examples = tuple(
        (d.tier, d.label, d.item_text[:200]) for d in cov.drops[:3]
    )
    return _EeResult(
        oid=aid,
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

    ids = _ee_all_akt_locators(limit)
    print(
        f"parse-bench: scanning {len(ids)} EE akt documents (parse-only, full corpus)...",
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


# ===========================================================================
# LOWERING COVERAGE (structured-amendment jurisdictions: us / nz / uk)
# ===========================================================================
#
# A DIFFERENT metric from FI/EE grammar coverage.  These jurisdictions consume
# pre-typed/structured amendment data (no free-text instruction to silently drop
# tokens from), so "grammar coverage" is undefined.  Instead we measure LOWERING
# coverage: of all pre-typed amendment instructions/effects already present in
# the corpus, what fraction LOWERED into produced ops — plus a ranked worklist of
# the unhandled/rejected instruction shapes (the lowering worklist).
#
# These adapters are read-only and replay-free: they reuse each frontend's own
# existing instruments (lower_plaw_amendatory / effect_readiness surface /
# compile_effect_to_ir_ops) and never apply, materialize, or diff.


def _canonical_data_path(*parts: str) -> str:
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", *parts)


# ---------------------------------------------------------------------------
# US — PLAW amendatory lowering coverage (pure parse+lower, no replay)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _UsResult:
    sid: str
    instructions_total: int
    instructions_lowered: int
    finding_rule_counts: tuple[tuple[str, int], ...]
    action_counts: tuple[tuple[str, int], ...]
    # (rule_id, raw_text) self-evidencing examples of UNLOWERED instructions.
    examples: tuple[tuple[str, str], ...]


def _us_plaw_locators(limit: int) -> list[str]:
    from lawvm.us_federal.sources import open_us_federal_farchive, list_plaw_identities

    archive = open_us_federal_farchive(readonly=True)
    try:
        identities = list_plaw_identities(archive)
    finally:
        archive.close()
    # Most-recent-Congress-first: the earliest congresses are dominated by
    # non-amendatory enactments (appropriations, short-title laws), so a bounded
    # --limit sample taken from the natural (congress, number) order would be all
    # zero-instruction laws.  Reversing makes a bounded sample representative;
    # full-corpus runs (no limit) aggregate identically regardless of order.
    locs = [i.locator for i in reversed(identities) if i.is_public]
    return locs[:limit] if limit else locs


def _us_scan_one(locator: str) -> _UsResult | None:
    from lawvm.us_federal.sources import open_us_federal_farchive
    from lawvm.us_federal.amendatory import lower_plaw_amendatory

    archive = open_us_federal_farchive(readonly=True)
    try:
        data = archive.get(locator)
    finally:
        archive.close()
    if not data:
        return None
    try:
        report = lower_plaw_amendatory(data, statute_id="")
    except Exception:
        return None
    cov = report.coverage()
    total = int(cov.get("instructions_total", 0) or 0)
    if total == 0:
        return None
    # Self-evidencing examples of instructions that did NOT lower into an op.
    examples: list[tuple[str, str]] = []
    for instr in report.instructions:
        if instr.operation is None and len(examples) < 3:
            rule = instr.witness_rule_id or instr.instruction_status or "__none__"
            examples.append((rule, (instr.raw_text or "")[:200]))
    return _UsResult(
        sid=cov.get("statute_id", "") or report.statute_id,
        instructions_total=total,
        instructions_lowered=int(cov.get("instructions_lowered", 0) or 0),
        finding_rule_counts=tuple(sorted((cov.get("finding_rule_counts") or {}).items())),
        action_counts=tuple(sorted((cov.get("action_counts") or {}).items())),
        examples=tuple(examples),
    )


def run_us(args) -> None:
    """US lowering coverage: fraction of pre-typed PLAW amendatory instructions
    that lowered into candidate ops, + the ranked finding-rule worklist."""
    limit = getattr(args, "limit", 0) or 0
    workers = getattr(args, "workers", 0) or 8
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20

    locs = _us_plaw_locators(limit)
    print(
        f"parse-bench: scanning {len(locs)} US public laws (parse+lower, no replay)...",
        file=sys.stderr,
    )

    results: list[_UsResult] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_us_scan_one, locs, chunksize=20)):
            if r is not None:
                results.append(r)
            if i and i % 500 == 0:
                print(f"  {i}/{len(locs)}", file=sys.stderr)

    total = sum(r.instructions_total for r in results)
    lowered = sum(r.instructions_lowered for r in results)
    coverage = (lowered / total * 100.0) if total else 0.0

    rule_ct: collections.Counter[str] = collections.Counter()
    action_ct: collections.Counter[str] = collections.Counter()
    for r in results:
        for rule, n in r.finding_rule_counts:
            rule_ct[rule] += n
        for act, n in r.action_counts:
            action_ct[act] += n
    worst = sorted(
        (r for r in results if r.instructions_lowered < r.instructions_total),
        key=lambda r: (r.instructions_lowered - r.instructions_total),
    )

    if as_json:
        json.dump(
            {
                "jurisdiction": "us",
                "metric": "lowering_coverage",
                "unit": "amendatory_instruction",
                "scanned": len(results),
                "instructions_total": total,
                "instructions_lowered": lowered,
                "instructions_unlowered": total - lowered,
                "lowering_coverage_pct": round(coverage, 3),
                "finding_rule_counts": dict(rule_ct),
                "action_counts": dict(action_ct),
                "top_shapes": [
                    {"rule_id": rule, "count": n} for rule, n in rule_ct.most_common(top)
                ],
                "worst_statutes": [
                    {
                        "sid": r.sid,
                        "instructions_total": r.instructions_total,
                        "instructions_lowered": r.instructions_lowered,
                    }
                    for r in worst[:top]
                ],
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return

    print("\n=== parse-bench: LOWERING COVERAGE (us) ===")
    print(f"  public laws scanned       : {len(results)}")
    print(f"  amendatory instructions   : {total}")
    print(f"  lowered into ops          : {lowered}")
    print(f"  unlowered                 : {total - lowered}")
    print(f"  LOWERING COVERAGE         : {coverage:.3f}%")
    print(f"\n  action counts: {dict(action_ct)}")
    print(f"\n  top {top} finding-rule shapes (lowering worklist):")
    for rule, n in rule_ct.most_common(top):
        print(f"    {n:5}  {rule}")
    print(f"\n  top {top} public laws by unlowered count:")
    for r in worst[:top]:
        gap = r.instructions_total - r.instructions_lowered
        print(f"    {r.sid}: {gap} unlowered ({r.instructions_lowered}/{r.instructions_total})")
    print("\n  sample unlowered instructions (self-evidencing):")
    shown = 0
    for r in worst:
        for rule, text in r.examples:
            print(f"    [{rule}] {r.sid}: {text}")
            shown += 1
            if shown >= top:
                break
        if shown >= top:
            break


# ---------------------------------------------------------------------------
# NZ — canonical-effect-lowering readiness coverage (pre-lowering surface)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _NzResult:
    work_id: str
    rows: int
    ready: int
    status_counts: tuple[tuple[str, int], ...]


def _nz_db_path() -> str:
    return _canonical_data_path("nz_legislation.farchive")


def _nz_work_ids(limit: int) -> list[str]:
    from pathlib import Path
    from lawvm.new_zealand.acquisition import open_farchive
    from lawvm.new_zealand.benchmark import _archived_work_ids

    archive = open_farchive(Path(_nz_db_path()))
    try:
        ids = list(_archived_work_ids(archive))
    finally:
        archive.close()
    return ids[:limit] if limit else ids


def _nz_scan_one(work_id: str) -> _NzResult | None:
    from pathlib import Path
    from lawvm.new_zealand.effect_readiness import (
        build_archived_work_effect_readiness_surface,
    )

    try:
        report = build_archived_work_effect_readiness_surface(
            Path(_nz_db_path()), work_id
        )
    except Exception:
        return None
    summary = report.summary()
    rows = int(summary.get("rows", 0) or 0)
    if rows == 0:
        return None
    return _NzResult(
        work_id=work_id,
        rows=rows,
        ready=int(summary.get("ready_for_canonical_effect_lowering", 0) or 0),
        status_counts=tuple(
            sorted((summary.get("effect_readiness_status_counts") or {}).items())
        ),
    )


def run_nz(args) -> None:
    """NZ lowering coverage: fraction of pre-typed effect rows READY for canonical
    effect lowering, + the ranked readiness-status worklist (blocked shapes)."""
    limit = getattr(args, "limit", 0) or 0
    workers = getattr(args, "workers", 0) or 8
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20

    work_ids = _nz_work_ids(limit)
    print(
        f"parse-bench: scanning {len(work_ids)} NZ works (effect-readiness, no replay)...",
        file=sys.stderr,
    )

    results: list[_NzResult] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_nz_scan_one, work_ids, chunksize=10)):
            if r is not None:
                results.append(r)
            if i and i % 200 == 0:
                print(f"  {i}/{len(work_ids)}", file=sys.stderr)

    total = sum(r.rows for r in results)
    ready = sum(r.ready for r in results)
    coverage = (ready / total * 100.0) if total else 0.0

    status_ct: collections.Counter[str] = collections.Counter()
    for r in results:
        for status, n in r.status_counts:
            status_ct[status] += n
    # The worklist of UNREADY (blocked) shapes excludes the ready bucket.
    blocked_ct = collections.Counter(
        {s: n for s, n in status_ct.items() if s != "ready_for_canonical_effect_lowering"}
    )
    worst = sorted(
        (r for r in results if r.ready < r.rows),
        key=lambda r: (r.ready - r.rows),
    )

    if as_json:
        json.dump(
            {
                "jurisdiction": "nz",
                "metric": "lowering_coverage",
                "unit": "effect_row",
                "scanned": len(results),
                "effect_rows_total": total,
                "ready_for_canonical_effect_lowering": ready,
                "blocked": total - ready,
                "lowering_coverage_pct": round(coverage, 3),
                "effect_readiness_status_counts": dict(status_ct),
                "top_shapes": [
                    {"effect_readiness_status": s, "count": n} for s, n in blocked_ct.most_common(top)
                ],
                "worst_statutes": [
                    {"sid": r.work_id, "rows": r.rows, "ready": r.ready}
                    for r in worst[:top]
                ],
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return

    print("\n=== parse-bench: LOWERING COVERAGE (nz) ===")
    print(f"  works scanned             : {len(results)}")
    print(f"  effect rows               : {total}")
    print(f"  ready for canonical lower : {ready}")
    print(f"  blocked                   : {total - ready}")
    print(f"  LOWERING COVERAGE         : {coverage:.3f}%")
    print(f"\n  readiness status counts: {dict(status_ct)}")
    print(f"\n  top {top} blocked-status shapes (lowering worklist):")
    for status, n in blocked_ct.most_common(top):
        print(f"    {n:5}  {status}")
    print(f"\n  top {top} works by blocked count:")
    for r in worst[:top]:
        gap = r.rows - r.ready
        print(f"    {r.work_id}: {gap} blocked ({r.ready}/{r.rows})")


# ---------------------------------------------------------------------------
# UK — per-effect lowering coverage (HEAVIER: bounded/sampled run)
# ---------------------------------------------------------------------------
#
# Per-effect lowering ordinarily needs the affecting-act source extraction, which
# is heavy.  This adapter stays replay-free by lowering with extracted_el=None +
# fallback_for_missing_extracted_source=True — reduced fidelity, but it answers
# the lowering-coverage question (did the typed effect produce any ops) without a
# PIT replay.  It DEFAULTS to a bounded run honoring --limit.

_UK_COST_NOTE = (
    "note: UK lowering coverage needs per-effect source extraction; this is a "
    "bounded/sampled run of {n} statutes (reduced fidelity, extracted_el=None) — "
    "the full corpus is slower."
)


@dataclass(frozen=True)
class _UkResult:
    sid: str
    effects_total: int
    effects_lowered: int
    rejection_rule_counts: tuple[tuple[str, int], ...]
    # (rule_id, affected_provisions, effect_type) self-evidencing examples.
    examples: tuple[tuple[str, str, str], ...]


def _uk_db_path() -> str:
    return _canonical_data_path("uk_legislation.farchive")


def _uk_statute_ids(limit: int) -> list[str]:
    import re
    from farchive import Farchive

    archive = Farchive(_uk_db_path(), readonly=True)
    pat = re.compile(r"/changes/affected/([^/]+)/(\d+)/(\d+)/data\.feed")
    seen: dict[str, None] = {}
    try:
        rows = archive._conn.execute(
            "SELECT DISTINCT locator FROM locator_span "
            "WHERE locator LIKE '%/changes/affected/%/data.feed%'"
        ).fetchall()
    finally:
        archive.close()
    for (url,) in rows:
        m = pat.search(url)
        if m:
            seen.setdefault(f"{m.group(1)}/{m.group(2)}/{m.group(3)}", None)
    ids = list(seen)
    return ids[:limit] if limit else ids


def _uk_scan_one(statute_id: str) -> _UkResult | None:
    from farchive import Farchive
    from lawvm.uk_legislation.effects import load_effects_for_statute_from_archive
    from lawvm.uk_legislation.effect_compiler import compile_effect_to_ir_ops

    archive = Farchive(_uk_db_path(), readonly=True)
    try:
        effects = load_effects_for_statute_from_archive(statute_id, archive)
    except Exception:
        archive.close()
        return None
    finally:
        archive.close()
    if not effects:
        return None

    total = 0
    lowered = 0
    rule_ct: collections.Counter[str] = collections.Counter()
    examples: list[tuple[str, str, str]] = []
    for seq, effect in enumerate(effects):
        total += 1
        rejections: list[dict] = []
        try:
            ops = compile_effect_to_ir_ops(
                effect,
                None,
                sequence=seq,
                fallback_for_missing_extracted_source=True,
                lowering_rejections_out=rejections,
            )
        except Exception:
            ops = []
            rejections.append({"rule_id": "uk_lowering_raised_exception"})
        if ops:
            lowered += 1
        else:
            for rej in rejections:
                rule = str(rej.get("rule_id") or "__none__")
                rule_ct[rule] += 1
                if len(examples) < 3:
                    examples.append(
                        (
                            rule,
                            (effect.affected_provisions or "")[:120],
                            (effect.effect_type or "")[:80],
                        )
                    )
            if not rejections:
                rule_ct["uk_lowering_no_ops_no_rejection"] += 1
    return _UkResult(
        sid=statute_id,
        effects_total=total,
        effects_lowered=lowered,
        rejection_rule_counts=tuple(sorted(rule_ct.items())),
        examples=tuple(examples),
    )


def run_uk(args) -> None:
    """UK lowering coverage: fraction of pre-typed effect records that lowered
    into ops (bounded/sampled, replay-free), + the ranked rejection worklist."""
    limit = getattr(args, "limit", 0) or 0
    workers = getattr(args, "workers", 0) or 8
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20

    sids = _uk_statute_ids(limit)
    cost_note = _UK_COST_NOTE.format(n=len(sids))
    print(f"parse-bench: scanning {len(sids)} UK statutes (per-effect lower)...", file=sys.stderr)
    print(cost_note, file=sys.stderr)

    results: list[_UkResult] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_uk_scan_one, sids, chunksize=4)):
            if r is not None:
                results.append(r)
            if i and i % 50 == 0:
                print(f"  {i}/{len(sids)}", file=sys.stderr)

    total = sum(r.effects_total for r in results)
    lowered = sum(r.effects_lowered for r in results)
    coverage = (lowered / total * 100.0) if total else 0.0

    rule_ct: collections.Counter[str] = collections.Counter()
    for r in results:
        for rule, n in r.rejection_rule_counts:
            rule_ct[rule] += n
    worst = sorted(
        (r for r in results if r.effects_lowered < r.effects_total),
        key=lambda r: (r.effects_lowered - r.effects_total),
    )

    if as_json:
        json.dump(
            {
                "jurisdiction": "uk",
                "metric": "lowering_coverage",
                "unit": "effect_record",
                "bounded_sample": True,
                "cost_note": cost_note,
                "scanned": len(results),
                "effects_total": total,
                "effects_lowered": lowered,
                "effects_unlowered": total - lowered,
                "lowering_coverage_pct": round(coverage, 3),
                "rejection_rule_counts": dict(rule_ct),
                "top_shapes": [
                    {"rule_id": rule, "count": n} for rule, n in rule_ct.most_common(top)
                ],
                "worst_statutes": [
                    {
                        "sid": r.sid,
                        "effects_total": r.effects_total,
                        "effects_lowered": r.effects_lowered,
                    }
                    for r in worst[:top]
                ],
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return

    print("\n=== parse-bench: LOWERING COVERAGE (uk) ===")
    print(f"  {cost_note}")
    print(f"  statutes scanned          : {len(results)}")
    print(f"  effect records            : {total}")
    print(f"  lowered into ops          : {lowered}")
    print(f"  unlowered                 : {total - lowered}")
    print(f"  LOWERING COVERAGE         : {coverage:.3f}%")
    print(f"\n  top {top} rejection-rule shapes (lowering worklist):")
    for rule, n in rule_ct.most_common(top):
        print(f"    {n:5}  {rule}")
    print(f"\n  top {top} statutes by unlowered count:")
    for r in worst[:top]:
        gap = r.effects_total - r.effects_lowered
        print(f"    {r.sid}: {gap} unlowered ({r.effects_lowered}/{r.effects_total})")
    print("\n  sample unlowered effects (self-evidencing):")
    shown = 0
    for r in worst:
        for rule, prov, etype in r.examples:
            print(f"    [{rule}] {r.sid}: {etype} -> {prov}")
            shown += 1
            if shown >= top:
                break
        if shown >= top:
            break


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

# fi/ee compute GRAMMAR coverage (free-text amendment grammars).
# us/nz/uk compute LOWERING coverage (structured amendment data — a DIFFERENT
# metric: fraction of pre-typed instructions/effects that lowered into ops).
# no/se have no free-text grammar and no lowering adapter yet; they keep the
# structured-jurisdiction pointer guard.
_STRUCTURED_POINTERS = {
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
    if jur == "us":
        run_us(args)
        return
    if jur == "nz":
        run_nz(args)
        return
    if jur == "uk":
        run_uk(args)
        return
    if jur in _STRUCTURED_POINTERS:
        print(
            "parse-bench: grammar coverage (fi, ee) and lowering coverage "
            "(us, nz, uk) are the defined metrics.  "
            f"{jur} has neither a free-text grammar nor a lowering adapter yet — "
            f"use its own coverage report.  {_STRUCTURED_POINTERS[jur]}."
        )
        return
    print(
        f"parse-bench: unknown jurisdiction {jur!r}; defined metrics are grammar "
        "coverage (fi, ee) and lowering coverage (us, nz, uk)."
    )
