"""lawvm self-consistency — enumerate amendment-chain self-consistency violations.

A statute's amendment chain is *self-consistent* when every amendment operates
on a structural unit (section / momentti / chapter / kohta) the replay can
actually locate, every compiled op is applied, and every amendment that cites
the statute is folded into the chain.  Whenever that breaks — an amendment
targets a unit that does not exist, an op is dropped or left unhandled, an
amendment is silently excluded, the johtolause claims more targets than the
body covers — the chain is internally inconsistent.  Almost every such case is
a real defect (a johtolause mis-parse, a target mis-resolution, or a genuine
coverage gap), so the population is a high-signal triage queue, not noise.

This tool replays every curated statute in parallel (reusing the bench corpus
list and the deterministic per-statute process pool) and harvests *every*
self-consistency channel it can reach, both the typed/structured surfaces and
the per-op replay log:

  apply_failure       typed ``FailedOp`` records (reason / reason_code / scope)
  target_absent       repeal/replace whose target unit does not exist — these
                      are SILENTLY swallowed in cumulative consolidation replay
                      (idempotent-repeal-of-absent) and never reach failed_ops,
                      so they are recovered from the per-op replay log
  unhandled_op        ops the apply layer could not classify or rejected
  source_pathology    typed ``SourcePathology`` records
  skipped_amendment   amendments excluded from the chain (not in corpus, title
                      targets a different statute, dropped from lineage)
  coverage_gap        per-amendment coverage mismatch (uncovered units, or the
                      johtolause claiming more/fewer targets than the body)
  invariant_violation post-replay structural tree invariant violations
  elaboration_finding governed ``ELAB.*`` rejection/observation findings

The proof case is ``1958/370`` (Rakennuslaki): amendment ``1968/493`` drops its
§111 replace (surfacing as a coverage gap), so §111 never gains its 2nd
momentti; later ``1977/604`` legitimately repeals "111 §:n 2 momentti", which
the replay reports as ``REPEAL 111 § 2 mom → FAILED (momentti 2 not found)`` —
a ``target_absent`` finding that is invisible to ``failed_ops`` alone.

The same audit runs on the UK frontend via ``-j uk`` (see
``tools.uk_self_consistency``): it replays the UK enacted base, applies the
compiled amendment ops with oracle alignment disabled, and harvests the replay
adjudications + compile rejections into the same row schema / signal taxonomy.
UK exposes ``apply_failure / target_absent / unhandled_op / source_pathology /
skipped_amendment / invariant_violation`` (no ``coverage_gap`` — UK has no
johtolause coverage count — and no FI-specific ``elaboration_finding``).

Usage:
    lawvm self-consistency                       # FI full corpus, all signal types
    lawvm self-consistency --statutes 1958/370
    lawvm self-consistency --signal-types target_absent,apply_failure
    lawvm self-consistency --limit 200 --workers 8
    lawvm self-consistency --json
    lawvm self-consistency -j uk --limit 50      # UK audit (curated subset)
    lawvm self-consistency -j uk --statutes ukpga/1961/33
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# Signal taxonomy
# ---------------------------------------------------------------------------

ALL_SIGNAL_TYPES = (
    "apply_failure",
    "target_absent",
    "unhandled_op",
    "source_pathology",
    "skipped_amendment",
    "coverage_gap",
    "invariant_violation",
    "elaboration_finding",
)

# ELAB finding kinds that denote a dropped / rejected / unhandled operation
# (an internal-inconsistency signal) rather than benign book-keeping.
_ELAB_FINDING_KINDS = {
    "ELAB.REJECTED_OPERATION",
    "ELAB.UNASSIGNED_SPARSE_SLOTS",
    "ELAB.AMBIGUOUS_BINDING",
    "ELAB.CONTAINER_PRUNED_SHADOWED",
    "ELAB.CHAPTER_SEED_SOURCE_PATHOLOGY",
}


# ---------------------------------------------------------------------------
# Reason categorisation
# ---------------------------------------------------------------------------

def _category(reason: str, reason_code: str = "") -> str:
    """Collapse a free-text reason to a stable category key.

    Prefer the typed ``reason_code`` when present; otherwise normalise the free
    text by replacing concrete numbers and ``kind:label`` tokens with
    placeholders so that, e.g., "momentti 2 not found" and "momentti 5 not
    found" share one bucket.
    """
    if reason_code:
        return reason_code
    # Number token, optionally with an immediately-attached section letter
    # suffix (e.g. "10a"); do NOT consume a following word like "not".
    cat = re.sub(r"\d+[a-z]?", "N", reason)
    cat = re.sub(r":[^\s)]+", ":X", cat)
    cat = re.sub(r"\[[^\]]*\]", "[...]", cat)
    cat = re.sub(r"'[^']*'", "'X'", cat)
    cat = re.sub(r"\s+", " ", cat)
    return cat.strip()


# ---------------------------------------------------------------------------
# Per-op replay-log parsing (recovers signals that never reach a typed sink)
# ---------------------------------------------------------------------------

# A failed per-op apply line, e.g.
#   "  [1977/604] REPEAL 111 § 2 mom → FAILED (momentti 2 not found)"
# The amendment id (when present) is a leading "[YYYY/NNN]" token.
_FAILED_LINE = re.compile(
    r"→ FAILED \((?P<reason>[^)]*)\)|-> FAILED \((?P<reason2>[^)]*)\)"
)
_AMENDMENT_TOKEN = re.compile(r"\[(\d{4}/\d+[A-Za-z]?)\]")
# Coverage line, e.g. "[1968/493] Coverage: 34 units, 32 claimed, 16 uncovered"
_COVERAGE_LINE = re.compile(
    r"Coverage:\s*(?P<units>\d+)\s*units,\s*(?P<claimed>\d+)\s*claimed,\s*"
    r"(?P<uncovered>\d+)\s*uncovered"
)
_SKIPPED_LINE = re.compile(
    r"\[(?P<aid>\d{4}/\d+[A-Za-z]?)\]\s*(?P<msg>(?:SKIPPED|not found in corpus)[^\n]*)"
)


def _classify_failed_reason(reason: str) -> str:
    """Map a ``→ FAILED (...)`` reason to a self-consistency signal type."""
    low = reason.lower()
    if "not found" in low:
        return "target_absent"
    if "unhandled" in low:
        return "unhandled_op"
    return "unhandled_op"


def _parse_replay_log(statute_id: str, log: str) -> List[Dict[str, Any]]:
    """Recover signals from the per-op replay log that no typed sink carries.

    The cumulative ``official_consolidation`` replay silently swallows
    target-absent repeals/replaces (idempotent-repeal-of-absent), so the only
    place they surface is this log.  We also pick up unhandled ops, skipped
    amendments, and per-amendment coverage gaps.
    """
    rows: List[Dict[str, Any]] = []
    for raw in log.splitlines():
        line = raw.rstrip()
        if not line:
            continue

        m = _FAILED_LINE.search(line)
        if m is not None:
            reason = (m.group("reason") or m.group("reason2") or "").strip()
            aid_m = _AMENDMENT_TOKEN.search(line)
            # The op description is the text before the arrow (minus the [aid]).
            desc = re.split(r"→ FAILED|-> FAILED", line, maxsplit=1)[0].strip()
            desc = _AMENDMENT_TOKEN.sub("", desc).strip()
            signal = _classify_failed_reason(reason)
            rows.append({
                "statute_id": statute_id,
                "amendment_id": aid_m.group(1) if aid_m else "",
                "signal_type": signal,
                "category": _category(reason),
                "description": desc,
                "target_scope": "",
                "reason": reason,
            })
            continue

        cov = _COVERAGE_LINE.search(line)
        if cov is not None:
            units = int(cov.group("units"))
            claimed = int(cov.group("claimed"))
            uncovered = int(cov.group("uncovered"))
            # A coverage gap is self-consistency signal when body units are left
            # uncovered, or the johtolause claims a different count than the
            # body actually carries (claimed != units) — a dropped/extra op.
            if uncovered > 0 or claimed != units:
                aid_m = _AMENDMENT_TOKEN.search(line)
                if claimed < units:
                    cat = "claimed<units (dropped op?)"
                elif claimed > units:
                    cat = "claimed>units (extra claim?)"
                else:
                    cat = "uncovered units"
                rows.append({
                    "statute_id": statute_id,
                    "amendment_id": aid_m.group(1) if aid_m else "",
                    "signal_type": "coverage_gap",
                    "category": cat,
                    "description": (
                        f"{units} units, {claimed} claimed, {uncovered} uncovered"
                    ),
                    "target_scope": "",
                    "reason": line.strip(),
                })
            continue

        sk = _SKIPPED_LINE.search(line)
        if sk is not None:
            msg = sk.group("msg").strip()
            rows.append({
                "statute_id": statute_id,
                "amendment_id": sk.group("aid"),
                "signal_type": "skipped_amendment",
                "category": _category(msg),
                "description": msg,
                "target_scope": "",
                "reason": msg,
            })
            continue

    return rows


# ---------------------------------------------------------------------------
# Structured-surface projection
# ---------------------------------------------------------------------------

def _scope_str(unit_kind: Any, chapter: Any, section: Any, part: Any = "") -> str:
    parts = []
    if part:
        parts.append(f"osa {part}")
    if chapter:
        parts.append(f"luku {chapter}")
    if section:
        parts.append(f"{section} §")
    if unit_kind and unit_kind not in {"section", ""}:
        parts.append(f"({unit_kind})")
    return " ".join(parts)


def _project_structured(
    statute_id: str,
    failed: List[Any],
    pathologies: List[Any],
    meta: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Project the typed replay sinks into self-consistency rows."""
    rows: List[Dict[str, Any]] = []

    # 1. Apply failures (typed FailedOp records).
    for fo in failed:
        reason = getattr(fo, "reason", "") or ""
        reason_code = getattr(fo, "reason_code", "") or ""
        rows.append({
            "statute_id": statute_id,
            "amendment_id": getattr(fo, "amendment_id", "") or "",
            "signal_type": "apply_failure",
            "category": _category(reason, reason_code),
            "description": getattr(fo, "description", "") or "",
            "target_scope": _scope_str(
                getattr(fo, "target_unit_kind", ""),
                getattr(fo, "target_chapter", ""),
                getattr(fo, "target_section", ""),
                getattr(fo, "target_part", ""),
            ),
            "reason": reason,
        })

    # 2. Source pathologies (typed SourcePathology records).
    for sp in pathologies:
        code = getattr(sp, "code", "") or ""
        msg = getattr(sp, "message", "") or ""
        rows.append({
            "statute_id": statute_id,
            "amendment_id": getattr(sp, "source_statute", "") or "",
            "signal_type": "source_pathology",
            "category": code or _category(msg),
            "description": msg,
            "target_scope": _scope_str(
                getattr(sp, "target_unit_kind", ""),
                "",
                getattr(sp, "target_label", ""),
            ),
            "reason": msg,
        })

    # 3. Skipped amendments — lineage records explicitly marked not-included.
    lineage = meta.get("lineage") or []
    for rec in lineage:
        if isinstance(rec, dict) and rec.get("included") is False:
            rows.append({
                "statute_id": statute_id,
                "amendment_id": str(rec.get("statute_id", "")),
                "signal_type": "skipped_amendment",
                "category": str(rec.get("selection_basis", "") or "excluded from lineage"),
                "description": str(rec.get("title", "") or ""),
                "target_scope": "",
                "reason": f"selection_basis={rec.get('selection_basis')}",
            })

    # 4. Post-replay structural tree invariant violations.
    for v in meta.get("invariant_violations") or []:
        text = str(v)
        rows.append({
            "statute_id": statute_id,
            "amendment_id": "",
            "signal_type": "invariant_violation",
            "category": _category(text),
            "description": text,
            "target_scope": "",
            "reason": text,
        })

    # 5. Governed ELAB findings denoting a dropped / rejected / unhandled op.
    for obs in meta.get("elaboration_observations") or []:
        if not isinstance(obs, dict):
            continue
        kind = str(obs.get("kind") or "")
        if kind not in _ELAB_FINDING_KINDS:
            continue
        detail = obs.get("detail") or {}
        rows.append({
            "statute_id": statute_id,
            "amendment_id": str(obs.get("source_statute", "") or ""),
            "signal_type": "elaboration_finding",
            "category": kind,
            "description": str(detail.get("message") or detail.get("explanation") or kind),
            "target_scope": _scope_str(
                obs.get("target_unit_kind", ""),
                obs.get("target_chapter", ""),
                obs.get("target_norm", ""),
            ),
            "reason": str(detail)[:240],
        })

    return rows


# ---------------------------------------------------------------------------
# Per-statute projector (module-level: picklable for the process pool)
# ---------------------------------------------------------------------------

def _project_self_consistency(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Replay one statute and project every self-consistency signal as rows.

    Returns ``(signal_rows, error_rows)``.  ``error_rows`` carries replay
    crashes (kept separate so one bad statute never aborts the sweep).

    The replay is run once with ``quiet=False`` under a captured stdout so we
    get both the typed sinks (``failed_ops``, ``source_pathologies``,
    ``replay_meta``) AND the per-op log that carries the silently-swallowed
    target-absent / coverage-gap / skipped-amendment signals.
    """
    from lawvm.finland.grafter import replay_xml

    failed: List[Any] = []
    pathologies: List[Any] = []
    meta: Dict[str, Any] = {}
    log_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(log_buf):
            replay_xml(
                statute_id,
                mode="official_consolidation",
                failed_ops_out=failed,
                source_pathologies_out=pathologies,
                replay_meta_out=meta,
                corpus=store,
                quiet=False,
            )
    except Exception as exc:  # a crashing replay is itself a finding
        return [], [{"statute_id": statute_id, "error": f"{type(exc).__name__}: {exc}"}]

    rows = _project_structured(statute_id, failed, pathologies, meta)
    rows.extend(_parse_replay_log(statute_id, log_buf.getvalue()))
    return rows, []


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------

def _resolve_statute_ids(args) -> List[str]:
    explicit = getattr(args, "statutes", None)
    if explicit:
        return [s.strip() for s in explicit.split(",") if s.strip()]
    from pathlib import Path

    from lawvm.tools.bench import _default_corpus_path, _load_corpus

    if getattr(args, "full", False):
        # The full ~3545-statute curated corpus (coverage-wide confidence),
        # rather than the ~690 bench_core representative subset.
        full = Path(_default_corpus_path()).parent / "bench_corpus.csv"
        corpus_path = str(full) if full.exists() else _default_corpus_path()
    else:
        corpus_path = _default_corpus_path()

    ids = [sid for _, sid in _load_corpus(corpus_path)]
    limit = getattr(args, "limit", None)
    if limit:
        ids = ids[:limit]
    return ids


def _resolve_signal_filter(args) -> set[str]:
    raw = getattr(args, "signal_types", None)
    if not raw:
        return set(ALL_SIGNAL_TYPES)
    requested = {s.strip() for s in raw.split(",") if s.strip()}
    unknown = requested - set(ALL_SIGNAL_TYPES)
    if unknown:
        raise SystemExit(
            f"unknown --signal-types {sorted(unknown)}; "
            f"choose from {list(ALL_SIGNAL_TYPES)}"
        )
    return requested


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    # Jurisdiction dispatch: EE/UK frontends expose their own replay harness and
    # self-consistency surfaces, so route to the jurisdiction-specific module
    # rather than the Finland projector.  FI remains the default fast path.
    jurisdiction = getattr(args, "jurisdiction", "fi") or "fi"
    if jurisdiction == "uk":
        _main_uk(args)
        return
    if jurisdiction == "ee":
        from lawvm.tools.ee_self_consistency import main as ee_main

        ee_main(args)
        return
    _main_fi(args)


def _main_fi(args) -> None:
    from lawvm.finland.corpus import get_corpus_store
    from lawvm.tools._parallel_corpus import project_corpus_parallel

    statute_ids = _resolve_statute_ids(args)
    signal_filter = _resolve_signal_filter(args)
    store = get_corpus_store()

    t0 = time.monotonic()
    rows, error_rows = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=(__name__, "_project_self_consistency"),
        serial_projector=_project_self_consistency,
        store=store,
        workers=getattr(args, "workers", 0) or 0,
    )
    elapsed = time.monotonic() - t0

    rows = [r for r in rows if r["signal_type"] in signal_filter]

    if getattr(args, "json", False):
        json.dump(
            {
                "elapsed_s": round(elapsed, 2),
                "statutes_swept": len(statute_ids),
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

    _print_report(rows, error_rows, statute_ids, elapsed)


def _main_uk(args) -> None:
    """UK self-consistency sweep.

    Replays the UK corpus in parallel through the shared jurisdiction-neutral
    harness, building one open Farchive per worker (via the UK store factory) and
    harvesting replay adjudications + compile rejections as self-consistency rows.
    """
    from lawvm.tools._parallel_corpus import project_corpus_parallel
    from lawvm.tools.uk_self_consistency import (
        build_uk_store,
        project_uk_self_consistency,
        resolve_uk_statute_ids,
    )

    statute_ids = resolve_uk_statute_ids(args)
    signal_filter = _resolve_signal_filter(args)
    store = build_uk_store()

    t0 = time.monotonic()
    try:
        rows, error_rows = project_corpus_parallel(
            statute_ids=statute_ids,
            projector_ref=("lawvm.tools.uk_self_consistency", "project_uk_self_consistency"),
            serial_projector=project_uk_self_consistency,
            store=store,
            workers=getattr(args, "workers", 0) or 0,
            store_factory_ref=("lawvm.tools.uk_self_consistency", "build_uk_store"),
        )
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    elapsed = time.monotonic() - t0

    rows = [r for r in rows if r["signal_type"] in signal_filter]

    if getattr(args, "json", False):
        json.dump(
            {
                "jurisdiction": "uk",
                "elapsed_s": round(elapsed, 2),
                "statutes_swept": len(statute_ids),
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

    _print_report(rows, error_rows, statute_ids, elapsed)


def _print_report(
    rows: List[Dict[str, Any]],
    error_rows: List[Dict[str, Any]],
    statute_ids: List[str],
    elapsed: float,
) -> None:
    by_type = Counter(r["signal_type"] for r in rows)
    affected = len({r["statute_id"] for r in rows})
    rate = len(statute_ids) / elapsed if elapsed > 0 else 0.0

    print(
        f"Swept {len(statute_ids):,} statutes in {elapsed:.1f}s "
        f"({rate:.0f}/s); {len(error_rows)} replay error(s)"
    )
    print(
        f"{len(rows):,} self-consistency signal(s) across {affected:,} statutes\n"
    )

    print("=== signals by type ===")
    for sig, n in by_type.most_common():
        statutes = len({r["statute_id"] for r in rows if r["signal_type"] == sig})
        print(f"{n:7d}  [{statutes:5d} statutes]  {sig}")
    print()

    # Group by signal_type then category, with per-category statute counts +
    # a few examples.
    for sig in [s for s in ALL_SIGNAL_TYPES if by_type.get(s)]:
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
            print(f"  {n:6d}  [{len(cat_statutes[cat]):4d} statutes]  {cat}")
            for r in samples[cat]:
                scope = f" {{{r['target_scope']}}}" if r["target_scope"] else ""
                print(
                    f"            {r['statute_id']} <- {r['amendment_id'] or '?'}:"
                    f" {r['description']}{scope}"
                )
        print()

    if error_rows:
        print(f"=== replay errors ({len(error_rows)}) ===")
        for er in error_rows[:20]:
            print(f"  {er['statute_id']}: {er['error']}")
