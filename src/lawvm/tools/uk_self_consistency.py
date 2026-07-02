"""UK self-consistency projector for ``lawvm self-consistency -j uk``.

This mirrors the Finland self-consistency audit (``tools.self_consistency``) on
the UK frontend.  The audit is *oracle-independent*: it replays an enacted base
and applies the compiled amendment ops, then harvests every channel where the
amendment chain fails to operate on a unit the replay can locate, drops/rejects
an op, or leaves an op unresolved.  None of these signals need the consolidated
oracle XML — they are emitted by the replay executor and the op compiler from
the source feeds alone, so we run with ``allow_oracle_alignment=False`` to keep
the signal purely internal to the chain.

UK exposes the same shape as Finland through different surfaces:

  apply_failure       replay-bug adjudications that are genuine apply failures
                      (``uk_replay_payload_mismatch / _missing``,
                      ``uk_replay_text_match_missing`` text-surface misses)
  target_absent       replay-bug / source-shape adjudications whose target unit
                      does not exist in the tree
                      (``uk_replay_target_not_found`` and the ``*_gap`` /
                      ``*_unresolved`` source-shape kinds)
  unhandled_op        ``uk_replay_unsupported_action`` and blocking lowering /
                      authority / feed-parse compile rejections
  source_pathology    classified effect source pathologies and source-parse
                      observations
  skipped_amendment   manual-compile-frontier rows the compiler set aside
                      (out-of-scope / non-textual / unresolved effects)
  invariant_violation ``uk_replay_tree_invariant_violation`` adjudications
  coverage_gap        (not currently surfaced by the UK replay — UK has no
                      johtolause coverage count; left empty rather than faked)

The projector returns rows in the same dict schema the Finland projector uses
(``statute_id, amendment_id, signal_type, category, description, target_scope,
reason``) so the shared report/JSON path in ``tools.self_consistency`` renders
both jurisdictions identically.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from lawvm.uk_legislation.source_adjudication import (
    classify_uk_replay_adjudication_bucket,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]  # LawVM/ (git-tracked resources)

# Replay-bug adjudication kinds that denote a missing/absent target rather than a
# payload/application failure.  Everything else in the replay-bug bucket is an
# apply failure; the tree-invariant kind is split out separately.
_TARGET_ABSENT_BUG_KINDS = frozenset({"uk_replay_target_not_found"})
_INVARIANT_BUG_KINDS = frozenset({"uk_replay_tree_invariant_violation"})
_UNSUPPORTED_BUG_KINDS = frozenset({"uk_replay_unsupported_action"})


# ---------------------------------------------------------------------------
# Store factory (one open Farchive per worker; module-level so it is picklable)
# ---------------------------------------------------------------------------

def build_uk_store() -> Any:
    """Open the UK Farchive once per worker process.

    Returns an open ``Farchive`` handle used read-only by the projector.  The
    parallel harness builds one of these per worker (not per statute), matching
    the Finland corpus-store-per-worker contract.

    The path is resolved through the shared ``corpus_store.resolve_farchive_path``
    chokepoint (precedence: ``LAWVM_UK_LEGISLATION_FARCHIVE_DB`` explicit override
    → ``$LAWVM_CANONICAL_DATA_ROOT/data/uk_legislation.farchive`` →
    ``<repo_root>/data/uk_legislation.farchive``), exactly like every other corpus
    consumer (FI/NZ/US), so a git worktree finds the archive via the canonical
    data root with no manual symlink (mirrors the NZ #157 fix).
    """
    from farchive import Farchive

    from lawvm.corpus_store import resolve_farchive_path

    db_path, _rule = resolve_farchive_path(
        "uk_legislation.farchive", explicit_env="LAWVM_UK_LEGISLATION_FARCHIVE_DB"
    )
    if not db_path.exists():
        raise FileNotFoundError(f"UK archive not found: {db_path}")
    return Farchive(db_path, readonly=True)


# ---------------------------------------------------------------------------
# Signal classification
# ---------------------------------------------------------------------------

def _adjudication_signal_type(kind: str) -> str | None:
    """Map a UK replay adjudication kind to a self-consistency signal type.

    Returns ``None`` for kinds that denote a *successful* resolution / applied
    rewrite (the non-blocking-observation bucket and the ``*_resolved`` /
    ``*_applied`` / ``*_recovered`` outcomes) — those are not inconsistencies.
    """
    if kind in _INVARIANT_BUG_KINDS:
        return "invariant_violation"
    if kind in _UNSUPPORTED_BUG_KINDS:
        return "unhandled_op"
    if kind in _TARGET_ABSENT_BUG_KINDS:
        return "target_absent"
    bucket = classify_uk_replay_adjudication_bucket(kind)
    if bucket == "replay_bug":
        # payload_mismatch / payload_missing / text_patch_missing_structured_payload
        return "apply_failure"
    if bucket == "source_shape":
        # The source-shape gaps are all "target unit not locatable in tree".
        return "target_absent"
    if bucket == "text_surface":
        # Text preimage / match misses are apply failures of a text patch.
        return "apply_failure"
    # nonblocking_observation and unknown/applied/resolved kinds are not defects.
    return None


def _project_adjudications(
    statute_id: str,
    adjudications: List[Any],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for adj in adjudications:
        kind = str(getattr(adj, "kind", "") or "")
        signal = _adjudication_signal_type(kind)
        if signal is None:
            continue
        detail = getattr(adj, "detail", {}) or {}
        if not isinstance(detail, dict):
            detail = {}
        target = str(detail.get("target") or "")
        rows.append({
            "statute_id": statute_id,
            "amendment_id": str(getattr(adj, "source_statute", "") or ""),
            "signal_type": signal,
            "category": kind,
            "description": str(getattr(adj, "message", "") or kind),
            "target_scope": target,
            "reason": str(detail)[:240],
        })
    return rows


# Source-pathology classifications that are benign / out-of-scope rather than a
# chain inconsistency.  ``nonstructural_root_gap`` is the expected "this effect
# is a non-structural application-by-reference, not a text/tree mutation" case
# (and the empty value is its unclassified twin); replaying it is *correct*
# non-action, so it is not a self-consistency defect.  Everything else (missing
# source, misselected target, instruction-text-as-payload, unsupported
# modification) is a genuine compile-side pathology.
_BENIGN_SOURCE_PATHOLOGIES = frozenset({"nonstructural_root_gap", ""})

# Manual-compile-frontier statuses that denote a deliberate, correct decision not
# to replay (non-textual / out-of-scope effects, or rows the deterministic
# frontend fully handled).  Only the "could not classify / source insufficient"
# statuses are skipped-amendment inconsistencies.
_BENIGN_FRONTIER_STATUSES = frozenset(
    {"non_textual_or_out_of_scope", "deterministic_frontend_supported"}
)


def _provision_scope(rec: Dict[str, Any]) -> str:
    affected = str(rec.get("affected_provisions") or "")
    affecting = str(rec.get("affecting_provisions") or "")
    if affected and affecting:
        return f"{affected} <- {affecting}"
    return affected or affecting


def _project_compile_rejections(
    statute_id: str,
    *,
    lowering_rejections: List[Dict[str, Any]],
    authority_rejections: List[Dict[str, Any]],
    effect_feed_parse_rejections: List[Dict[str, Any]],
    source_parse_rejections: List[Dict[str, Any]],
    effect_source_pathology_observations: List[Dict[str, Any]],
    manual_compile_frontier_observations: List[Dict[str, Any]],
    source_acquisition_rejections: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Project the typed compile-side rejection lanes into self-consistency rows.

    Only *blocking* lowering/authority/feed/source-parse/acquisition rejections
    are inconsistencies (a dropped or unparseable op).  The source-pathology and
    manual-compile-frontier lanes are mostly *deliberate* non-replay decisions
    (non-textual / application-by-reference effects the UK replay correctly does
    not apply); those benign classes are filtered out so only the genuinely
    pathological rows (missing source, misselected target, unsupported
    modification, unclassified frontier) remain — the analogue of Finland's typed
    ``SourcePathology`` / excluded-from-lineage harvest.
    """
    from lawvm.core.compile_records import CompileRecord, is_blocking_compile_record

    rows: List[Dict[str, Any]] = []

    def _row(
        rec: Dict[str, Any],
        signal: str,
        *,
        category: str,
        description: str,
    ) -> Dict[str, Any]:
        return {
            "statute_id": statute_id,
            "amendment_id": str(
                rec.get("affecting_act_id")
                or rec.get("source_statute")
                or rec.get("amending_act_id")
                or ""
            ),
            "signal_type": signal,
            "category": category,
            "description": description,
            "target_scope": _provision_scope(rec)
            or str(rec.get("target") or rec.get("target_eid") or ""),
            "reason": str({
                k: rec.get(k)
                for k in (
                    "rule_id",
                    "source_pathology",
                    "manual_compile_status",
                    "manual_compile_reason",
                    "effect_type",
                )
                if rec.get(k) is not None
            })[:300],
        }

    # unhandled_op: a compiled op that could not be lowered / authorised / parsed.
    for lane in (
        lowering_rejections,
        authority_rejections,
        effect_feed_parse_rejections,
        source_acquisition_rejections,
    ):
        for rec in lane:
            if is_blocking_compile_record(CompileRecord.from_mapping(rec)):
                rule = str(rec.get("rule_id") or "unknown")
                rows.append(_row(
                    rec,
                    "unhandled_op",
                    category=rule,
                    description=str(
                        rec.get("message") or rec.get("explanation") or rule
                    ),
                ))

    # source_pathology: typed source-shape pathologies (benign classes filtered)
    # + blocking source-parse observations.
    for rec in effect_source_pathology_observations:
        pathology = str(rec.get("source_pathology") or "")
        if pathology in _BENIGN_SOURCE_PATHOLOGIES:
            continue
        rows.append(_row(
            rec,
            "source_pathology",
            category=pathology or "unclassified_source_pathology",
            description=(
                f"{pathology}: {str(rec.get('effect_type') or 'effect')} "
                f"{_provision_scope(rec)}".strip()
            ),
        ))
    for rec in source_parse_rejections:
        if is_blocking_compile_record(CompileRecord.from_mapping(rec)):
            rule = str(rec.get("rule_id") or "unknown")
            rows.append(_row(
                rec,
                "source_pathology",
                category=rule,
                description=str(rec.get("message") or rec.get("explanation") or rule),
            ))

    # skipped_amendment: effects the manual-compile frontier set aside because it
    # could not classify / lower them (benign deliberate non-replay filtered).
    for rec in manual_compile_frontier_observations:
        status = str(rec.get("manual_compile_status") or "")
        if status in _BENIGN_FRONTIER_STATUSES:
            continue
        rule = str(rec.get("manual_compile_rule_id") or rec.get("rule_id") or "unknown")
        rows.append(_row(
            rec,
            "skipped_amendment",
            category=status or rule,
            description=(
                f"{status}: {_provision_scope(rec)} "
                f"({str(rec.get('manual_compile_reason') or '')})".strip()
            ),
        ))

    return rows


# ---------------------------------------------------------------------------
# Per-statute projector (module-level: picklable for the process pool)
# ---------------------------------------------------------------------------

def project_uk_self_consistency(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Replay one UK statute and project every self-consistency signal as rows.

    ``store`` is an open ``Farchive`` handle (from :func:`build_uk_store`).  The
    replay loads the enacted base, compiles amendment ops from the archive feeds,
    and applies them while capturing the adjudication + compile-rejection lanes.
    Oracle alignment is disabled so the harvested signal is purely internal to
    the amendment chain (no consolidated-XML dependency).

    Returns ``(signal_rows, error_rows)``; ``error_rows`` carries replay crashes
    so one bad statute never aborts the sweep.
    """
    from lawvm.uk_legislation.source_state import (
        is_uk_affecting_act_xml_source_observation,
        uk_enacted_blob_replay_base_usability,
        uk_source_parse_observations_from_ir,
        uk_source_xml_parse_rejection,
        uk_source_state_wire_tuple,
    )
    from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline
    from lawvm.uk_legislation.uk_grafter import parse_uk_statute_ir_bytes

    archive = store
    enacted_url = (
        f"https://www.legislation.gov.uk/{statute_id}/enacted/data.xml"
    )

    try:
        enacted_bytes = archive.get(enacted_url)
        status, _size = uk_source_state_wire_tuple(enacted_bytes)
        if status != "available" or enacted_bytes is None:
            return [], [{
                "statute_id": statute_id,
                "error": f"enacted_xml_unavailable ({status})",
            }]

        # A metadata-only enacted envelope (NumberOfProvisions="0", no Body /
        # Schedule payload — common for pre-digitisation UK acts) passes the
        # size gate above but parses into an EMPTY IR base.  Replaying the
        # amendment chain against it manufactures target-absent / missing-branch
        # / missing-payload signals that are artefacts of the thin source, not
        # chain inconsistencies.  The oracle-bench paths already exclude such
        # bases via ``usable_as_replay_base``; do the same here so the
        # oracle-independent audit is not dominated by un-digitised enacted
        # stubs.  Genuine digitised bases (has_body / has_schedules) are
        # unaffected.
        usable, content_status = uk_enacted_blob_replay_base_usability(enacted_bytes)
        if not usable:
            return [], [{
                "statute_id": statute_id,
                "error": f"enacted_not_replayable ({content_status})",
            }]

        source_parse_rejections: List[Dict[str, Any]] = []
        try:
            base_ir = parse_uk_statute_ir_bytes(
                enacted_bytes,
                statute_id=statute_id,
                version_label="enacted",
                source_path=enacted_url,
            )
            source_parse_rejections.extend(
                dict(r) for r in uk_source_parse_observations_from_ir(base_ir)
            )
        except Exception as exc:
            source_parse_rejections.append(dict(uk_source_xml_parse_rejection(
                statute_id=statute_id,
                side="enacted",
                source_url=enacted_url,
                exc=exc,
            )))
            return [], [{
                "statute_id": statute_id,
                "error": f"enacted_xml_parse_rejected: {type(exc).__name__}",
            }]

        effect_feed_parse_rejections: List[Dict[str, Any]] = []
        effect_diagnostics: List[Dict[str, Any]] = []
        lowering_rejections: List[Dict[str, Any]] = []
        authority_rejections: List[Dict[str, Any]] = []

        pipeline = UKReplayPipeline(_REPO_ROOT)
        ops = pipeline.compile_ops_for_statute(
            statute_id,
            archive=archive,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            effect_diagnostics_out=effect_diagnostics,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
        )

        effect_source_pathology_observations = [
            dict(row)
            for row in effect_diagnostics
            if str(row.get("rule_id") or "") == "uk_effect_source_pathology_classified"
        ]
        manual_compile_frontier_observations = [
            dict(row)
            for row in effect_diagnostics
            if str(row.get("rule_id") or "") == "uk_manual_compile_frontier_classified"
        ]
        source_acquisition_rejections = [
            dict(row)
            for row in effect_diagnostics
            if is_uk_affecting_act_xml_source_observation(row)
        ]

        adjudications: List[Any] = []
        pipeline.apply_ops(
            base_ir,
            list(ops),
            allow_oracle_alignment=False,
            adjudications_out=adjudications,
        )
    except Exception as exc:  # a crashing replay is itself a finding
        return [], [{
            "statute_id": statute_id,
            "error": f"{type(exc).__name__}: {exc}",
        }]

    rows = _project_adjudications(statute_id, adjudications)
    rows.extend(_project_compile_rejections(
        statute_id,
        lowering_rejections=lowering_rejections,
        authority_rejections=authority_rejections,
        effect_feed_parse_rejections=effect_feed_parse_rejections,
        source_parse_rejections=source_parse_rejections,
        effect_source_pathology_observations=effect_source_pathology_observations,
        manual_compile_frontier_observations=manual_compile_frontier_observations,
        source_acquisition_rejections=source_acquisition_rejections,
    ))
    return rows, []


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------

def _uk_corpus_path(full: bool) -> Path:
    """Locate the UK corpus CSV (statute_id column)."""
    base = _REPO_ROOT / "data" / "uk"
    if full:
        return base / "bench_corpus.csv"
    # Default to the curated "tight" subset for a fast representative sweep,
    # falling back to the full corpus if the subset is absent.
    tight = base / "bench_corpus_tight.csv"
    return tight if tight.exists() else base / "bench_corpus.csv"


def resolve_uk_statute_ids(args) -> List[str]:
    explicit = getattr(args, "statutes", None)
    if explicit:
        return [s.strip() for s in explicit.split(",") if s.strip()]

    import csv

    corpus_path = _uk_corpus_path(getattr(args, "full", False))
    ids: List[str] = []
    if corpus_path.exists():
        with open(corpus_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sid = str(row.get("statute_id") or "").strip()
                if sid:
                    ids.append(sid)
    limit = getattr(args, "limit", None)
    if limit:
        ids = ids[:limit]
    return ids
