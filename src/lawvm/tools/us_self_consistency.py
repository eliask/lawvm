"""U.S. federal amendatory self-consistency projector for ``self-consistency -j us``.

This mirrors the Finland / UK / EE self-consistency audits on the U.S. federal
frontend.  The audit is *oracle-independent* in exactly the sense UK's is: it
never consults the USC-edition oracle to derive a signal.  Where UK replays the
enacted base with ``allow_oracle_alignment=False``, the U.S. surface has no
replay step at all — its amendatory processing is a *lowering* stage that
compiles each enacted Public Law's USLM amendatory instructions into candidate
``LegalOperation``s (``lower_plaw_amendatory``).  A U.S. amendment chain is
*self-consistent* when every classified amendatory instruction lowers to an op
whose target resolves to a concrete, in-scope (Title-11) USC address.  Whenever
that breaks — the instruction's shape is not lowerable, its target cannot be
resolved, it resolves outside the US Code, or it lands on a non-positive-law /
uncodified holdout the govinfo-reachable channels cannot classify — the chain is
internally inconsistent, and the case is a typed, visible row (never guessed
away; the lowering's non-hijack discipline in ``amendatory._resolve_target`` and
``nonpositive.resolve_nonpositive_target`` is preserved verbatim).

The only oracle read anywhere in this tool is *corpus selection*: the default
sweep set is the bench-window public-law delta (``derive_window_law_locators``
reads the USC editions to decide WHICH laws first appear in a window).  That is a
bounding step, not a signal source — analogous to UK reading its enacted base and
EE deriving a PIT date.  Every projected signal is computed from the PLAW USLM
bytes alone; no ``WindowResult`` / dry-run / after-edition section text is ever
consulted to produce a row.

Signal taxonomy (mapped to the shared Finland schema where the US surfaces are
genuine):

  unhandled_op        ``us_amendatory_unlowered`` — a classified amendatory
                      instruction whose shape the lowerer does not support (left
                      unlowered rather than guessed).
  target_absent       the target could not be pinned to an in-scope codified USC
                      address: ``us_amendatory_target_unresolved`` (no prose/href
                      resolved), ``us_amendatory_target_non_us_code`` (resolved
                      but outside Title 11), and the non-positive-law uncodified
                      holdout (``us_nonpositive_target_unmapped`` /
                      ``us_nonpositive_target_note_only`` — no govinfo-reachable
                      classification channel yields a codified section).
  invariant_violation a PLAW that could not be parsed/lowered at all (the law's
                      whole instruction stream is unevaluable) — recorded both as
                      a structured row and an ``error_rows`` entry.

The remaining shared signal types have no honest U.S. lowering-stage surface and
are intentionally left EMPTY rather than faked (mirroring UK leaving
``coverage_gap`` empty):

  apply_failure       there is no apply step at the lowering stage — application
                      truth is established only later by the dry-run against the
                      USC oracle, which this oracle-independent audit excludes.
  source_pathology    the editorial-sidenote / page-stamp pruning the lowerer
                      does is silent normalisation, not a typed pathology lane.
  skipped_amendment   no amendment is dropped from a chain here — every
                      instruction unit is lowered or becomes a typed finding.

Rows use the shared dict schema (``statute_id, amendment_id, signal_type,
category, description, target_scope, reason``) so the shared report/JSON path in
``tools.self_consistency`` renders US identically to FI/UK/EE.
"""
from __future__ import annotations

import json
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from lawvm.tools.self_consistency import _category
from lawvm.us_federal.amendatory import (
    ADD_AT_END_MISSING_PAYLOAD_FINDING_RULE_ID,
    AMEND_TO_READ_MISSING_PAYLOAD_FINDING_RULE_ID,
    DEFERRED_AMEND_TO_READ_FINDING_RULE_ID,
    END_PUNCT_INSERT_NO_QUOTED_CAPTURE_FINDING_RULE_ID,
    END_PUNCT_STRIKE_INSERT_REGEX_MISS_FINDING_RULE_ID,
    FORMATTING_ONLY_FINDING_RULE_ID,
    INSERT_AFTER_MISSING_OPERANDS_FINDING_RULE_ID,
    NON_TITLE_TARGET_RULE_ID,
    PUNCT_WORD_UNRECOGNIZED_FINDING_RULE_ID,
    STRIKE_INSERT_MISSING_OPERANDS_FINDING_RULE_ID,
    STRIKE_NO_QUOTED_ANCHOR_FINDING_RULE_ID,
    TABLE_REDESIGNATE_AMBIGUOUS_TITLE_FINDING_RULE_ID,
    TAIL_STRIKE_INSERT_MISSING_OPERANDS_FINDING_RULE_ID,
    TARGET_UNRESOLVED_FINDING_RULE_ID,
    UNLOWERED_FINDING_RULE_ID,
    UNRECOGNIZED_AMENDATORY_FORM_FINDING_RULE_ID,
    UNRECOGNIZED_REDESIGNATE_FINDING_RULE_ID,
)
from lawvm.us_federal.nonpositive import (
    NOTE_ONLY_FINDING_RULE_ID as NONPOSITIVE_NOTE_ONLY_RULE_ID,
)
from lawvm.us_federal.nonpositive import (
    UNMAPPED_FINDING_RULE_ID as NONPOSITIVE_UNMAPPED_RULE_ID,
)

# ---------------------------------------------------------------------------
# Signal taxonomy
# ---------------------------------------------------------------------------

US_SIGNAL_TYPES = (
    "apply_failure",
    "target_absent",
    "unhandled_op",
    "source_pathology",
    "skipped_amendment",
    "invariant_violation",
)

# Amendatory finding rule_id -> self-consistency signal type. These are the
# typed findings ``lower_plaw_amendatory`` emits for an instruction it could not
# fully lower; every other instruction produced a candidate op (not a defect).
_AMENDATORY_FINDING_SIGNAL: Dict[str, str] = {
    UNLOWERED_FINDING_RULE_ID: "unhandled_op",
    UNRECOGNIZED_REDESIGNATE_FINDING_RULE_ID: "unhandled_op",
    UNRECOGNIZED_AMENDATORY_FORM_FINDING_RULE_ID: "unhandled_op",
    INSERT_AFTER_MISSING_OPERANDS_FINDING_RULE_ID: "unhandled_op",
    STRIKE_NO_QUOTED_ANCHOR_FINDING_RULE_ID: "unhandled_op",
    STRIKE_INSERT_MISSING_OPERANDS_FINDING_RULE_ID: "unhandled_op",
    ADD_AT_END_MISSING_PAYLOAD_FINDING_RULE_ID: "unhandled_op",
    AMEND_TO_READ_MISSING_PAYLOAD_FINDING_RULE_ID: "unhandled_op",
    TAIL_STRIKE_INSERT_MISSING_OPERANDS_FINDING_RULE_ID: "unhandled_op",
    END_PUNCT_INSERT_NO_QUOTED_CAPTURE_FINDING_RULE_ID: "unhandled_op",
    END_PUNCT_STRIKE_INSERT_REGEX_MISS_FINDING_RULE_ID: "unhandled_op",
    PUNCT_WORD_UNRECOGNIZED_FINDING_RULE_ID: "unhandled_op",
    TABLE_REDESIGNATE_AMBIGUOUS_TITLE_FINDING_RULE_ID: "unhandled_op",
    FORMATTING_ONLY_FINDING_RULE_ID: "unhandled_op",
    DEFERRED_AMEND_TO_READ_FINDING_RULE_ID: "unhandled_op",
    TARGET_UNRESOLVED_FINDING_RULE_ID: "target_absent",
    NON_TITLE_TARGET_RULE_ID: "target_absent",
}


def _get_classification_index_for_self_consistency() -> Any:
    """Lazily load the PL-section→USC-section classification index."""
    import os
    path = os.environ.get("LAWVM_US_CLASSIFICATION_INDEX")
    if not path or not os.path.exists(path):
        return None
    import json
    from lawvm.us_federal.classification_tables import (
        ClassificationEntry, ClassificationIndex,
    )
    with open(path) as f:
        data = json.load(f)
    entries = [ClassificationEntry(**e) for e in data["entries"]]
    return ClassificationIndex(entries)

# Non-positive-law resolution statuses that denote an UNMAPPED holdout (no
# govinfo-reachable channel yields a codified section). Only these two are
# self-consistency signals; the resolved statuses (paren/href/agree) are correct
# classifications, not defects.
_NONPOSITIVE_HOLDOUT_RULE_IDS = frozenset(
    {NONPOSITIVE_UNMAPPED_RULE_ID, NONPOSITIVE_NOTE_ONLY_RULE_ID}
)


# ---------------------------------------------------------------------------
# Store factory (one open Farchive per worker; module-level so it is picklable)
# ---------------------------------------------------------------------------

def build_us_store() -> Any:
    """Open the U.S. federal Farchive once per worker process (read-only).

    Returns an open ``Farchive`` handle used read-only by the projector. The
    canonical-data-root / worktree precedence is resolved by
    :func:`lawvm.us_federal.sources.open_us_federal_farchive`.
    """
    from lawvm.us_federal.sources import open_us_federal_farchive

    return open_us_federal_farchive(readonly=True)


# ---------------------------------------------------------------------------
# Per-unit non-positive holdout scan (oracle-free; PLAW bytes only)
# ---------------------------------------------------------------------------

def _nonpositive_holdouts(data: bytes) -> List[Tuple[str, str, str]]:
    """Yield ``(rule_id, target_phrase, note_href)`` for unmapped non-positive units.

    Iterates the same amendatory instruction units as ``lower_plaw_amendatory``
    and resolves each via the non-positive act-section → USC resolver, keeping
    ONLY the genuinely-unmapped holdouts (no codified channel) — the residual
    classification-table gap. Resolved targets (paren/href/agree) are not defects
    and are dropped. This reads no USC edition: the classification signal lives
    inside the PLAW USLM itself (the parenthetical cite and the converter href).
    """
    from lawvm.us_federal.amendatory import (
        _amending_actions,
        _first_usc_ref,
        _iter_instruction_units,
        _localname,
        _text_of,
    )
    from lawvm.us_federal.nonpositive import (
        _USLM_NS,
        _structural_target_href,
        resolve_nonpositive_target,
    )

    out: List[Tuple[str, str, str]] = []
    root = ET.fromstring(data)
    main = root.find(".//u:main", _USLM_NS)
    if main is None:
        return out
    for section in main.iter():
        if _localname(section.tag) != "section":
            continue
        if not any(_localname(a.tag) == "amendingAction" for a in section.iter()):
            continue
        section_content = section.find("u:content", _USLM_NS)
        sec_phrase, sec_href = ("", "")
        if section_content is not None:
            sec_phrase, sec_href = _first_usc_ref(section_content)
        for _uid, unit, _inherited, _effective, _expires, _via_class in _iter_instruction_units(
            section
        ):
            if not _amending_actions(unit):
                continue
            unit_phrase, _unit_href = _first_usc_ref(unit)
            target_phrase = unit_phrase or sec_phrase
            target_href = _structural_target_href(unit, sec_href)
            raw_text = _text_of(unit)
            witness = resolve_nonpositive_target(
                target_phrase=target_phrase,
                target_href=target_href,
                raw_text=raw_text,
            )
            if witness.rule_id in _NONPOSITIVE_HOLDOUT_RULE_IDS:
                out.append((witness.rule_id, witness.target_phrase, witness.note_href))
    return out


# ---------------------------------------------------------------------------
# Per-law projector (module-level: picklable for the process pool)
# ---------------------------------------------------------------------------

def project_us_self_consistency(
    locator: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Lower one Public Law and project every self-consistency signal as rows.

    ``locator`` is a canonical ``us://plaw/{congress}/publ{N}.xml`` locator;
    ``store`` is an open ``Farchive`` handle (from :func:`build_us_store`). The
    projector reads ONLY the PLAW USLM bytes — no USC edition, no dry-run, no
    after-edition oracle — so every harvested signal is internal to the
    amendatory lowering stage.

    Returns ``(signal_rows, error_rows)``. A PLAW that is absent or unparseable is
    recorded both as a structured ``invariant_violation`` row AND an
    ``error_rows`` entry so one bad law never aborts the sweep.
    """
    from lawvm.us_federal.amendatory import lower_plaw_amendatory
    from lawvm.us_federal.sources import read_plaw_locator

    data = read_plaw_locator(store, locator)
    if data is None:
        return [], [{"statute_id": locator, "error": "plaw_source_absent"}]

    try:
        report = lower_plaw_amendatory(data, statute_id=locator,
                                       classification_index=_get_classification_index_for_self_consistency())
    except Exception as exc:  # an unparseable law is itself a finding
        err = f"{type(exc).__name__}: {exc}"
        row = {
            "statute_id": locator,
            "amendment_id": locator,
            "signal_type": "invariant_violation",
            "category": _category(err),
            "description": f"PLAW USLM could not be lowered: {err}",
            "target_scope": "",
            "reason": err,
        }
        return [row], [{"statute_id": locator, "error": err}]

    rows: List[Dict[str, Any]] = []

    # Amendatory findings: an instruction the lowerer could not handle / resolve.
    for instr in report.instructions:
        finding = instr.finding
        if finding is None:
            continue
        signal = _AMENDATORY_FINDING_SIGNAL.get(finding.rule_id)
        if signal is None:
            continue
        target_scope = ""
        if instr.target_address is not None:
            target_scope = str(instr.target_address)
        elif finding.target_phrase:
            target_scope = finding.target_phrase
        rows.append({
            "statute_id": locator,
            "amendment_id": locator,
            "signal_type": signal,
            "category": finding.rule_id,
            "description": f"{instr.action or 'amend'}: {finding.message}",
            "target_scope": target_scope[:200],
            "reason": str(
                {
                    "instruction_id": finding.instruction_id,
                    "target_phrase": finding.target_phrase,
                    "target_href": finding.target_href,
                }
            )[:300],
        })

    # Non-positive-law uncodified holdouts: an act-section amendment no reachable
    # govinfo channel can classify to a codified USC section.
    try:
        for rule_id, target_phrase, note_href in _nonpositive_holdouts(data):
            rows.append({
                "statute_id": locator,
                "amendment_id": locator,
                "signal_type": "target_absent",
                "category": rule_id,
                "description": (
                    "non-positive-law / uncodified target unmapped to a codified "
                    "USC section by any govinfo-reachable channel"
                ),
                "target_scope": (target_phrase or note_href)[:200],
                "reason": str({"target_phrase": target_phrase, "note_href": note_href})[:300],
            })
    except Exception:
        # The holdout scan is a best-effort secondary surface; its failure must
        # never lose the primary amendatory findings already harvested above.
        pass

    return rows, []


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CORPUS = _ROOT / "us" / "bench" / "us_bench_corpus.csv"


def resolve_us_locators(args, store: Any) -> List[str]:
    """Resolve the ``us://plaw/...`` locators to sweep.

    ``--statutes`` accepts explicit comma-separated PLAW locators (or canonical
    ``PL {congress}-{number}`` labels). Otherwise the default sweep set is the
    bench-window public-law delta: the laws whose source-credit first appears in
    each included window (``derive_window_law_locators``). This is the only step
    that reads the USC editions, and only to BOUND the corpus — never to produce
    a signal.
    """
    from lawvm.us_federal.sources import parse_plaw_locator, plaw_locator

    explicit = (getattr(args, "statutes", "") or "").strip()
    if explicit:
        locs: List[str] = []
        for token in (t.strip() for t in explicit.split(",") if t.strip()):
            if token.startswith("us://plaw/"):
                locs.append(token)
                continue
            # "PL 116-54" / "116-54" -> canonical locator.
            label = token.replace("PL", "").strip()
            if "-" in label:
                congress, number = label.split("-", 1)
                try:
                    locs.append(plaw_locator(int(congress), int(number)))
                    continue
                except ValueError:
                    pass
            ident = parse_plaw_locator(token)
            if ident is not None:
                locs.append(ident.locator)
        return locs

    from lawvm.us_federal.bench import derive_window_law_locators, load_corpus

    corpus_path = Path(getattr(args, "us_corpus", "") or _DEFAULT_CORPUS)
    if not corpus_path.exists():
        raise SystemExit(f"US bench corpus CSV not found: {corpus_path}")

    windows = load_corpus(corpus_path)
    seen: Dict[str, None] = {}
    for window in windows:
        if not window.include:
            continue
        derived = derive_window_law_locators(
            store,
            title=window.title,
            before_year=window.before_year,
            after_year=window.after_year,
        )
        if not derived:
            continue
        for loc in derived.values():
            seen.setdefault(loc, None)

    locators = sorted(seen)
    limit = getattr(args, "limit", 0) or 0
    if limit:
        locators = locators[:limit]
    return locators


def _resolve_signal_filter(args) -> set[str]:
    raw = getattr(args, "signal_types", "") or ""
    requested = {s.strip() for s in raw.split(",") if s.strip()}
    if not requested:
        return set(US_SIGNAL_TYPES)
    unknown = requested - set(US_SIGNAL_TYPES)
    if unknown:
        raise SystemExit(
            f"unknown --signal-types {sorted(unknown)}; choose from {list(US_SIGNAL_TYPES)}"
        )
    return requested


# ---------------------------------------------------------------------------
# Parallel sweep (per-worker archive open, mirroring us-bench discipline)
# ---------------------------------------------------------------------------

def _sweep(
    locators: Sequence[str],
    workers: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    from lawvm.tools._parallel_corpus import project_corpus_parallel

    store = build_us_store()
    try:
        rows, errs = project_corpus_parallel(
            statute_ids=list(locators),
            projector_ref=("lawvm.tools.us_self_consistency", "project_us_self_consistency"),
            serial_projector=project_us_self_consistency,
            store=store,
            workers=workers,
            store_factory_ref=("lawvm.tools.us_self_consistency", "build_us_store"),
        )
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    return rows, errs


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    store = build_us_store()
    try:
        locators = resolve_us_locators(args, store)
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()

    signal_filter = _resolve_signal_filter(args)
    if not locators:
        print("No US public laws selected.", file=sys.stderr)
        raise SystemExit(1)

    t0 = time.monotonic()
    rows, error_rows = _sweep(locators, getattr(args, "workers", 0) or 0)
    elapsed = time.monotonic() - t0

    rows = [r for r in rows if r["signal_type"] in signal_filter]

    if getattr(args, "json", False):
        json.dump(
            {
                "jurisdiction": "us",
                "elapsed_s": round(elapsed, 2),
                "statutes_swept": len(locators),
                "signal_types": sorted(signal_filter),
                "replay_errors": error_rows,
                "signals": len(rows),
                "rows": rows,
            },
            sys.stdout,
            ensure_ascii=False,
            indent=1,
            default=str,
        )
        sys.stdout.write("\n")
        return

    _print_report(rows, error_rows, locators, elapsed)


def _print_report(
    rows: List[Dict[str, Any]],
    error_rows: List[Dict[str, Any]],
    locators: Sequence[str],
    elapsed: float,
) -> None:
    by_type = Counter(r["signal_type"] for r in rows)
    affected = len({r["statute_id"] for r in rows})
    rate = len(locators) / elapsed if elapsed > 0 else 0.0

    print(
        f"Swept {len(locators):,} US public laws in {elapsed:.1f}s "
        f"({rate:.0f}/s); {len(error_rows)} lowering error(s)"
    )
    print(f"{len(rows):,} self-consistency signal(s) across {affected:,} laws\n")

    print("=== signals by type ===")
    for sig, n in by_type.most_common():
        statutes = len({r["statute_id"] for r in rows if r["signal_type"] == sig})
        print(f"{n:7d}  [{statutes:5d} laws]  {sig}")
    print()

    for sig in [s for s in US_SIGNAL_TYPES if by_type.get(s)]:
        sig_rows = [r for r in rows if r["signal_type"] == sig]
        print(f"=== {sig} ({len(sig_rows):,}) ===")
        by_cat = Counter(r["category"] for r in sig_rows)
        cat_statutes: Dict[str, set] = defaultdict(set)
        samples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in sig_rows:
            cat_statutes[r["category"]].add(r["statute_id"])
            if len(samples[r["category"]]) < 3:
                samples[r["category"]].append(r)
        for cat, n in by_cat.most_common():
            print(f"  {n:6d}  [{len(cat_statutes[cat]):4d} laws]  {cat}")
            for r in samples[cat]:
                scope = f" {{{r['target_scope']}}}" if r["target_scope"] else ""
                print(
                    f"            {r['statute_id']} <- {r['amendment_id'] or '?'}:"
                    f" {r['description']}{scope}"
                )
        print()

    if error_rows:
        print(f"=== lowering errors ({len(error_rows)}) ===")
        for er in error_rows[:20]:
            print(f"  {er.get('statute_id')}: {er.get('error')}")


__all__ = [
    "US_SIGNAL_TYPES",
    "build_us_store",
    "project_us_self_consistency",
    "resolve_us_locators",
    "main",
]
