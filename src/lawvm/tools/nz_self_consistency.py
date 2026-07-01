"""NZ self-consistency projector for ``lawvm self-consistency -j nz``.

This mirrors the Finland (``tools.self_consistency``) and UK
(``tools.uk_self_consistency``) self-consistency audits on the New Zealand
frontend.  The audit is *oracle-independent*: it replays each archived work's
amendment chain through the strict actual-replay executor
(:func:`lawvm.new_zealand.actual_replay.build_archived_work_actual_replay`) and
harvests every channel where the chain fails to promote a declared op to a
materialized mutation, refuses a whole transition, or sets an op aside.

The actual-replay executor is *fail-closed*: it never partially materializes a
declared transition and emits a distinct named ``NZActualReplayRefusal`` for
every op/transition it declines.  Those refusal rule_ids — not the archived
consolidation oracle — are the self-consistency signal.  The one refusal family
that IS oracle-derived (``*_materialized_target_slice_diverges_from_oracle`` /
``*_dry_run_oracle_residual_not_agreement``) is a genuine replay-direction
divergence; it is surfaced as ``apply_failure`` because the executor determined
the op's effect would not reproduce the archived state.  In the dominant case
(``*_dry_run_oracle_residual_not_agreement``) that determination is made at the
strict *dry-run gate* — the executor refuses the op *before* materializing it,
because the dry-run proof's residual disagrees with the oracle; the rarer
``*_materialized_target_slice_diverges_from_oracle`` catches the same divergence
after a materialized slice.  Either way it is the "the chain's op does not
reproduce the archived result" shape Finland/UK call an apply failure.
Everything else is a pre-materialization, source-internal decision.

NZ exposes the shared taxonomy through the ``NZActualReplayRefusal.rule_id``
fail-closed vocabulary (``actual_replay.py``):

  apply_failure       a declared op materialized (or its dry-run proof residual)
                      but the result diverges from the archived state
                      (``*_dry_run_oracle_residual_not_agreement``,
                      ``*_materialized_target_slice_diverges_from_oracle``,
                      ``*_mutation_perturbed_neighbours``)
  target_absent       a structural op whose payload/anchor could not be
                      re-materialized in the tree
                      (``*_structural_payload_not_re_materializable``)
  unhandled_op        a declared op the executor could not even attempt because
                      the driving surface was absent
                      (``*_operation_surface_missing_for_structural_family``)
  source_pathology    the before/on-or-after/amending XML footing the replay
                      reads from was unreadable
                      (``*_before_version_xml_unreadable``,
                      ``*_on_or_after_version_xml_unreadable``)
  skipped_amendment   a declared op that was not dry-run-verified / not in the
                      promotable set / was blocked by a sibling op in the same
                      fail-closed transition — the source did not license a
                      clean exact replay (``*_not_dry_run_verified``,
                      ``*_family_not_in_promotable_set``)
  invariant_violation the amendment window for a transition's date is missing —
                      the temporal footing is internally inconsistent
                      (``*_missing_before_after_version_window``)

  occupancy_violation / coverage_gap / duplicate_label / mixed_hierarchy /
  elaboration_finding: not currently surfaced by the NZ actual-replay executor
  (NZ has no johtolause coverage count nor a sparse-slot elaboration lane), so
  they are left empty rather than faked — matching the UK harness's stance on
  coverage_gap.

The projector returns rows in the same dict schema Finland/UK use
(``statute_id, amendment_id, signal_type, category, description, target_scope,
reason``) so the shared report/JSON path in ``tools.self_consistency`` renders
NZ identically to the other jurisdictions.  The ``statute_id`` column carries the
NZ work id; ``amendment_id`` carries the amendment date (NZ refusals are dated,
not id-keyed).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[3]  # LawVM/


# ---------------------------------------------------------------------------
# Signal classification: NZActualReplayRefusal.rule_id -> shared signal type
# ---------------------------------------------------------------------------
#
# The rule_id constants live in ``new_zealand.actual_replay``; imported lazily in
# the classifier so this module stays importable without the NZ frontend loaded.


def _refusal_signal_type(rule_id: str) -> str | None:
    """Map an NZ actual-replay refusal rule_id to a self-consistency signal type.

    Returns ``None`` for rule_ids that are NOT fail-closed op/transition blocks
    (e.g. the family-level "family declared nothing" receipt), which are honest
    residue rather than inconsistencies.
    """
    from lawvm.new_zealand.actual_replay import (
        NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_FAMILY_NOT_PROMOTABLE_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_STRUCTURAL_MATERIALIZATION_FAILED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
    )

    # A mutation was materialized (or its dry-run proof residual survived) and
    # the result diverged from the archived state: a genuine apply failure.
    if rule_id in {
        NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
    }:
        return "apply_failure"

    # A structural op whose payload/anchor could not be re-materialized into the
    # tree — the target unit the op names is not locatable/derivable.
    if rule_id == NZ_ACTUAL_REPLAY_REFUSED_STRUCTURAL_MATERIALIZATION_FAILED_RULE_ID:
        return "target_absent"

    # A declared structural op the executor could not even attempt because its
    # driving operation surface was absent: an op with no handler path.
    if rule_id == NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID:
        return "unhandled_op"

    # The before / on-or-after XML footing the replay reads from was unreadable:
    # a source-side pathology (present in principle, footing missing).
    if rule_id in {
        NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
    }:
        return "source_pathology"

    # The before/on-or-after version window for the transition's date is missing:
    # the temporal footing of the chain is internally inconsistent.
    if rule_id == NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID:
        return "invariant_violation"

    # The op was declined before any mutation because the source did not license
    # a clean exact replay (not dry-run-verified / family not promotable / a
    # sibling op in the same fail-closed transition blocked it): the NZ analogue
    # of Finland's set-aside amendment.
    if rule_id in {
        NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_FAMILY_NOT_PROMOTABLE_RULE_ID,
    }:
        return "skipped_amendment"

    # Family-level "this family declared nothing to replay" receipt: honest
    # residue, not an inconsistency.
    if rule_id == NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID:
        return None

    # An unrecognised fail-closed refusal is itself a finding — surface it as a
    # skipped amendment rather than dropping it silently.
    return "skipped_amendment"


# ---------------------------------------------------------------------------
# Store factory (module-level so it is picklable for the worker pool)
# ---------------------------------------------------------------------------

def build_nz_store() -> str:
    """Resolve the read-only NZ Farchive path once per worker process.

    Returns the archive path as a plain string (the store the projector needs is
    the db path — the NZ actual-replay builder opens the Farchive read-only
    itself, via the corpus cache when active).  This NEVER touches the live NZ
    API / NZ_API_KEY: the audit operates archive-only.

    The path is resolved through the shared ``corpus_store.resolve_farchive_path``
    chokepoint (precedence: ``LAWVM_NZ_LEGISLATION_FARCHIVE_DB`` explicit override
    → ``$LAWVM_CANONICAL_DATA_ROOT/data/nz_legislation.farchive`` →
    ``<repo_root>/data/nz_legislation.farchive``), exactly like every other
    corpus consumer (FI/UK/US), so a git worktree finds the archive via the
    canonical data root with no manual symlink.
    """
    from lawvm.corpus_store import resolve_farchive_path

    db_path, _rule = resolve_farchive_path(
        "nz_legislation.farchive", explicit_env="LAWVM_NZ_LEGISLATION_FARCHIVE_DB"
    )
    if not db_path.exists():
        raise FileNotFoundError(f"NZ archive not found: {db_path}")
    return str(db_path)


# ---------------------------------------------------------------------------
# Per-work projector (module-level: picklable for the process pool)
# ---------------------------------------------------------------------------

def project_nz_self_consistency(
    work_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Replay one NZ work and project every self-consistency signal as rows.

    ``store`` is the read-only NZ Farchive path (from :func:`build_nz_store`).
    The actual-replay executor loads the archived before-document, dry-run
    verifies each declared op, and materializes only the verified ops fail-closed
    — capturing a distinct named refusal for every op/transition it declines.
    The audit is oracle-independent: the harvested signal comes from the
    fail-closed refusal vocabulary, not from a hand-picked consolidation XML.

    Returns ``(signal_rows, error_rows)``; ``error_rows`` carries replay crashes
    so one bad work never aborts the sweep.
    """
    from lawvm.new_zealand.actual_replay import build_archived_work_actual_replay

    db_path = Path(str(store))
    try:
        report = build_archived_work_actual_replay(db_path, work_id)
    except Exception as exc:  # a crashing replay is itself a finding
        return [], [{
            "statute_id": work_id,
            "error": f"{type(exc).__name__}: {exc}",
        }]

    return _project_report(work_id, report), []


def _project_report(work_id: str, report: Any) -> List[Dict[str, Any]]:
    """Project one ``NZActualReplayReport`` into shared self-consistency rows.

    Only the fail-closed per-op/per-transition refusals in ``report.refusals``
    are inconsistencies (a declared op the chain could not cleanly replay).  The
    ``families_not_attempted`` and ``family_level_dry_run_refusals`` lanes are
    deliberate "this family declared nothing" receipts (honest residue, per
    AGENTS §1.8), so they are excluded — the same stance the UK harness takes on
    its benign frontier statuses.
    """
    rows: List[Dict[str, Any]] = []
    for refusal in getattr(report, "refusals", ()):
        rule_id = str(getattr(refusal, "rule_id", "") or "")
        signal = _refusal_signal_type(rule_id)
        if signal is None:
            continue
        detail = getattr(refusal, "detail", {}) or {}
        if not isinstance(detail, dict):
            detail = {}
        op_ids = tuple(getattr(refusal, "op_ids", ()) or ())
        target_scope = str(
            detail.get("target_address")
            or detail.get("target")
            or detail.get("family")
            or (op_ids[0] if op_ids else "")
        )
        rows.append({
            "statute_id": work_id,
            "amendment_id": str(getattr(refusal, "amendment_date_iso", "") or ""),
            "signal_type": signal,
            "category": rule_id,
            "description": str(getattr(refusal, "message", "") or rule_id),
            "target_scope": target_scope,
            "reason": str(detail)[:300],
        })
    return rows


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------

def _nz_corpus_path(full: bool) -> Path:
    """Locate the NZ corpus CSV (``work_id`` column).

    Defaults to the curated smoke subset for a fast representative sweep; ``full``
    selects the larger committed bench corpus.  Both are committed CSVs, so the
    default sweep never enumerates the whole 40k-work archive.
    """
    base = _REPO_ROOT / "data" / "nz"
    if full:
        return base / "bench_corpus.csv"
    smoke = base / "bench_corpus_smoke.csv"
    return smoke if smoke.exists() else base / "bench_corpus.csv"


def resolve_nz_work_ids(args) -> List[str]:
    """Resolve the NZ work-id population for the sweep, in deterministic order.

    Precedence: an explicit ``--statutes`` list wins; else an explicit
    ``--corpus`` CSV/text file; else the curated smoke subset (or the full bench
    corpus with ``--full``).  ``--limit`` caps the population from the front.
    """
    explicit = getattr(args, "statutes", None)
    if explicit:
        ids = [s.strip() for s in str(explicit).split(",") if s.strip()]
        limit = getattr(args, "limit", None)
        return ids[:limit] if limit else ids

    corpus_arg = str(getattr(args, "corpus", "") or "").strip()
    corpus_path = Path(corpus_arg) if corpus_arg else _nz_corpus_path(
        bool(getattr(args, "full", False))
    )

    ids: List[str] = []
    if corpus_path.exists():
        lines = corpus_path.read_text(encoding="utf-8").splitlines()
        if lines and "work_id" in lines[0]:
            reader = csv.DictReader(lines)
            for row in reader:
                wid = str(row.get("work_id") or "").strip()
                if wid:
                    ids.append(wid)
        else:
            # Plain-text one-work-id-per-line list.
            for line in lines:
                wid = line.strip()
                if wid and not wid.startswith("#"):
                    ids.append(wid)

    limit = getattr(args, "limit", None)
    if limit:
        ids = ids[:limit]
    return ids
