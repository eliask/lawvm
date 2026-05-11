"""lawvm evidence — live statute-level proof bundles for Finland deviations.

Builds auditable evidence bundles directly from current replay/oracle/html state,
without depending on historical divergences.db exports.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import inspect
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, cast

import yaml
from lxml import etree

from lawvm.core.compile_result import CompileFailure
from lawvm.core.target_scope import TargetUnitKind
from lawvm.finland.corrigendum_records import load_patch_records, load_source_records
from lawvm.finland.corpus import get_consolidated_oracle_suspect_cache_only
from lawvm.finland.compile import compile_fi_facade_from_replay
from lawvm.finland.source_adjudication import build_source_adjudication
from lawvm.finland.strict_profile import default_finland_strict_profile
from lawvm.finland.grafter import (
    get_ground_truth_tree,
    replay_xml,
)
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.tools._compile_report_record import report_record_from_facade
from lawvm.tools.audit import _audit_html_one, _finlex_html_url
from lawvm.tools.oracle_check import _classify_statute
from lawvm.finland.transparent_store import is_known_missing_source


from lawvm.tools._evidence_helpers import (  # noqa: E402
    _MANUAL_DATASET,
    _ORACLE_INCORRECT_DIAGNOSES,
    _PRIMARY_TIER_ORDER,
    _REPLAY_BUG_DIAGNOSES,
    _cross_chapter_same_label_oracle_matches,
    _cross_chapter_same_label_replay_matches,
    _diagnosis_counts,
    _normalize_observation_streams,
    _proof_contract,
    _run_quietly,
    _same_chapter_alternative_replay_matches,
    _same_chapter_oracle_range_matches,
    _section_label_from_key,
    _section_similarity,
)
from lawvm.tools.bisect_support import _section_bisect_support  # noqa: E402

_EVIDENCE_BUNDLE_CACHE_VERSION = "evidence-bundle-v53"
_DEFAULT_ORACLE_CORPUS_BUNDLE_CACHE_DIR = ".tmp/evidence_bundle_cache"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_EE_FARCHIVE = _REPO_ROOT / "data" / "ee_riigiteataja.farchive"
_DEFAULT_UK_FARCHIVE = _REPO_ROOT / "data" / "uk_legislation.farchive"
_ACTIONABLE_UNRESOLVED_SELECTED_KINDS = frozenset(
    {
        "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
        "replay_divergence",
    }
)
_MIXED_REPLAY_RISK_STRICT_FAIL_REASONS = frozenset(
    {
        "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
        "PARSE.EXTRACTION_FALLBACK",
        "APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
        "ELAB.OMISSION_EXPANSION",
        "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
        "APPLY.TREE_INVARIANT_VIOLATION",
        "APPLY.UNCOVERED_BODY_RECOVERY",
    }
)
_TARGET_SCOPED_PROVENANCE_KINDS = frozenset(
    {
        "PARSE.TARGET_GUESSING",
        "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
    }
)
_FRONTEND_ELABORATION_PROJECTION_KINDS = frozenset(
    {
        "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
        "ELAB.CONTAINER_PRUNED_SHADOWED",
        "ELAB.PAYLOAD_COMPLETENESS",
        "ELAB.DROP_ITEM_REPLACES_MISSING",
        "ELAB.UNASSIGNED_SPARSE_SLOTS",
        "ELAB.AMBIGUOUS_BINDING",
        "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH",
        "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
        "ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE",
        "ELAB.MISSING_PAYLOAD_SURFACE",
    }
)
_SPARSE_BLOCKER_KINDS = frozenset(
    {
        "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH",
        "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
    }
)
_ORACLE_REPEAL_SOURCE_RE = re.compile(
    r"on\s+kumottu\s+(?:\d{1,2}\.\d{1,2}\.\d{4}\s+)?(?:[LAP](?:\:ll[äa])?)\s+"
    r"((?:\d{1,2}\.\d{1,2}\.\d{4}/\d+)|(?:\d+/\d{4}))(?:\s+v\.\s+\d{4})?",
    re.IGNORECASE,
)
_ORACLE_TEMPORARY_SOURCE_RE = re.compile(
    r"oli\s+väliaikaisesti\s+voimassa\s+\d{1,2}\.\d{1,2}\.\d{4}\s*[–—\-]\s*"
    r"\d{1,2}\.\d{1,2}\.\d{4}\s+(?:[LAP](?:\:ll[äa])?)\s+"
    r"((?:\d{1,2}\.\d{1,2}\.\d{4}/\d+)|(?:\d+/\d{4}))(?:\s+v\.\s+\d{4})?",
    re.IGNORECASE,
)


def _evidence_context_degradation(rail: str, exc: Exception) -> Dict[str, str]:
    return {
        "kind": "evidence_context_degraded",
        "rail": rail,
        "exception_type": type(exc).__name__,
        "message": str(exc),
    }


def _evidence_context_degradation_rows(
    *,
    statute_id: str,
    mode: str,
    diagnostics: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    from lawvm.core.evidence_contracts import CorpusFindingEvidenceRow

    rows: List[Dict[str, Any]] = []
    for diagnostic in diagnostics:
        rail = str(diagnostic.get("rail") or "unknown")
        rule_id = f"evidence_context_degraded:{rail}"
        rows.append(
            CorpusFindingEvidenceRow(
                finding_id=f"finland:{statute_id}:{mode}:{rule_id}",
                frontend_id="finland",
                family="evidence_context_degraded",
                rule_id=rule_id,
                phase="evidence_context",
                message=str(diagnostic.get("message") or "evidence context rail degraded"),
                source_artifact_id=statute_id,
                blocking=True,
                strict_disposition="block",
                quirks_disposition="record_degraded",
                evidence=dict(diagnostic),
            ).to_dict()
        )
    return rows


def _witness_for_op(op: object) -> object | None:
    """Return the preferred witness payload for reporting.

    Prefer the typed payload-sidecar witness when present so EE-style lanes do
    not need to rely on shared source carriers. Payload-less legacy ops now
    return ``None`` here rather than carrying witness data through provenance.
    """
    payload = getattr(op, "payload", None)
    payload_attrs = getattr(payload, "attrs", None)
    if isinstance(payload_attrs, dict):
        witness = payload_attrs.get("rewrite_witness")
        if witness is not None:
            return witness
    return None


def _effective_source_adjudication(
    *,
    statute_id: str,
    replay_mode: str,
    replay_result: object | None,
    replay_meta: dict[str, object],
) -> SourceAdjudication | None:
    typed = getattr(replay_result, "source_adjudication", None)
    if typed is not None:
        return cast(SourceAdjudication, typed)

    raw_lineage = replay_meta.get("lineage")
    lineage: tuple[dict[str, Any], ...] = ()
    if isinstance(raw_lineage, (list, tuple)):
        lineage = cast(
            tuple[dict[str, Any], ...],
            tuple(row for row in raw_lineage if isinstance(row, dict)),
        )

    cutoff_date = str(replay_meta.get("cutoff_date") or "")
    oracle_version_amendment_id = str(replay_meta.get("oracle_version_amendment_id") or "")
    oracle_suspect = str(replay_meta.get("oracle_suspect") or "")
    html_noncommensurable_reason = str(replay_meta.get("html_noncommensurable_reason") or "")
    if not any(
        (
            cutoff_date,
            oracle_version_amendment_id,
            oracle_suspect,
            html_noncommensurable_reason,
            lineage,
        )
    ):
        return None
    return build_source_adjudication(
        statute_id=statute_id,
        replay_mode=replay_mode,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id,
        oracle_suspect=oracle_suspect,
        html_noncommensurable_reason=html_noncommensurable_reason,
        lineage=lineage,
    )


@contextlib.contextmanager
def _temporary_corpus_env(
    *,
    corpus_store_mode: str = "",
    cache_only: bool = False,
):
    requested_mode = str(corpus_store_mode or "").strip()
    prior_mode = os.environ.get("LAWVM_CORPUS_STORE")
    prior_cache_only = os.environ.get("LAWVM_TRANSPARENT_CACHE_ONLY")
    try:
        if requested_mode:
            os.environ["LAWVM_CORPUS_STORE"] = requested_mode
        if cache_only:
            os.environ["LAWVM_TRANSPARENT_CACHE_ONLY"] = "1"
        yield
    finally:
        if prior_mode is None:
            os.environ.pop("LAWVM_CORPUS_STORE", None)
        else:
            os.environ["LAWVM_CORPUS_STORE"] = prior_mode
        if prior_cache_only is None:
            os.environ.pop("LAWVM_TRANSPARENT_CACHE_ONLY", None)
        else:
            os.environ["LAWVM_TRANSPARENT_CACHE_ONLY"] = prior_cache_only


def _normalize_uk_applicability_mode(applicability_mode: Optional[str]) -> str:
    return str(applicability_mode or "effective_date_plus_feed_applied")


def _call_with_supported_kwargs(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    supported = {name: value for name, value in kwargs.items() if name in params}
    return fn(*args, **supported)


def _bundle_cache_path(
    cache_dir: str,
    statute_id: str,
    *,
    jurisdiction: str = "fi",
    mode: str,
    include_bisect: bool,
    corpus_store_mode: str = "",
    cache_only: bool = False,
    oracle_only: bool = False,
    allow_metadata_backfill: Optional[bool] = None,
    allow_oracle_alignment: Optional[bool] = None,
    applicability_mode: Optional[str] = None,
    authority_mode: Optional[str] = None,
) -> Path:
    safe_statute = re.sub(r"[^A-Za-z0-9._-]+", "_", str(statute_id or "").strip()) or "statute"
    effective_applicability_mode = _normalize_uk_applicability_mode(applicability_mode)
    key_payload = {
        "version": _EVIDENCE_BUNDLE_CACHE_VERSION,
        "jurisdiction": str(jurisdiction or "fi"),
        "statute_id": str(statute_id or ""),
        "mode": str(mode or ""),
        "include_bisect": bool(include_bisect),
        "corpus_store_mode": str(corpus_store_mode or ""),
        "cache_only": bool(cache_only),
        "oracle_only": bool(oracle_only),
        "allow_metadata_backfill": allow_metadata_backfill,
        "allow_oracle_alignment": allow_oracle_alignment,
        "applicability_mode": effective_applicability_mode,
        "authority_mode": authority_mode,
    }
    digest = hashlib.sha1(json.dumps(key_payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"{safe_statute}__{digest}.json"


def _effective_bundle_cache_dir(
    bundle_cache_dir: str = "",
    *,
    oracle_corpus: bool = False,
) -> str:
    requested = str(bundle_cache_dir or "").strip()
    if requested:
        return requested
    if oracle_corpus:
        return _DEFAULT_ORACLE_CORPUS_BUNDLE_CACHE_DIR
    return ""


def _read_cached_bundle(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # OSError: file not found/unreadable; ValueError: json.JSONDecodeError (subclass)
        return None


def _write_cached_bundle(path: Path, bundle: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load_cached_review_bundle(
    statute_id: str,
    *,
    jurisdiction: str = "fi",
    mode: str,
    include_bisect: bool,
    corpus_store_mode: str = "",
    cache_only: bool = False,
    bundle_cache_dir: str = "",
    oracle_only: bool = False,
    allow_metadata_backfill: Optional[bool] = None,
    allow_oracle_alignment: Optional[bool] = None,
    applicability_mode: Optional[str] = None,
    authority_mode: Optional[str] = None,
) -> tuple[bool, Optional[Dict], bool]:
    if not bundle_cache_dir:
        return False, None, False
    cache_path = _bundle_cache_path(
        bundle_cache_dir,
        statute_id,
        jurisdiction=jurisdiction,
        mode=mode,
        include_bisect=include_bisect,
        corpus_store_mode=corpus_store_mode,
        cache_only=cache_only,
        oracle_only=oracle_only,
        allow_metadata_backfill=allow_metadata_backfill,
        allow_oracle_alignment=allow_oracle_alignment,
        applicability_mode=applicability_mode,
        authority_mode=authority_mode,
    )
    if not cache_path.exists():
        return False, None, False
    cached_bundle = _read_cached_bundle(cache_path)
    if isinstance(cached_bundle, dict):
        return True, cached_bundle, False
    return False, None, True


def _normalize_compiler_observations(
    replay_meta: Dict[str, object] | None,
) -> List[Dict]:
    replay_meta = replay_meta or {}
    elaboration_observations = replay_meta.get("elaboration_observations")
    sparse_slot_bindings = replay_meta.get("sparse_slot_bindings")
    sparse_leftovers = replay_meta.get("sparse_leftovers")
    apply_mutation_events = replay_meta.get("apply_mutation_events")
    apply_mutation_invariant_reports = replay_meta.get("apply_mutation_invariant_reports")
    return _normalize_observation_streams(
        elaboration_observations=(
            cast(Iterable[object], elaboration_observations) if isinstance(elaboration_observations, list) else None
        ),
        sparse_slot_bindings=(
            cast(Iterable[object], sparse_slot_bindings) if isinstance(sparse_slot_bindings, list) else None
        ),
        sparse_leftovers=(
            cast(Iterable[object], sparse_leftovers) if isinstance(sparse_leftovers, list) else None
        ),
        apply_mutation_events=(
            cast(Iterable[object], apply_mutation_events) if isinstance(apply_mutation_events, list) else None
        ),
        apply_mutation_invariant_reports=(
            cast(Iterable[object], apply_mutation_invariant_reports)
            if isinstance(apply_mutation_invariant_reports, list)
            else None
        ),
    )


def _compiler_projection_rows(
    projection_rows: Iterable[Dict[str, Any]] | None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    malformed_indexes: list[int] = []
    for index, row in enumerate(projection_rows or ()):
        if isinstance(row, dict):
            rows.append(dict(row))
            continue
        malformed_indexes.append(index)
    if malformed_indexes:
        indexes = ", ".join(str(index) for index in malformed_indexes)
        raise ValueError(f"compiler projection rows contain non-object entries at indexes: {indexes}")
    return rows


def _compiler_observation_summary(
    *,
    replay_meta: Dict[str, object] | None,
    projection_rows: Iterable[Dict[str, Any]] | None,
    section_bisect: Optional[List[Dict]] = None,
) -> Dict:
    normalized_records = _normalize_compiler_observations(replay_meta)
    projection_rows = _compiler_projection_rows(projection_rows)
    provenance_projection_rows: list[dict[str, str]] = []
    seen_provenance_projection_rows: set[tuple[str, str, str, str, str, str]] = set()
    provenance_projection_kind_counts: Counter[str] = Counter()

    for item in projection_rows:
        kind = str(item.get("kind") or "").strip()
        if kind not in _TARGET_SCOPED_PROVENANCE_KINDS:
            continue
        detail = item.get("detail")
        if not isinstance(detail, dict):
            continue
        tag = str(detail.get("tag") or "").strip()
        target_unit_kind = str(detail.get("target_unit_kind") or "").strip()
        target_norm = str(detail.get("target_norm") or "").strip()
        target_chapter = str(detail.get("target_chapter") or "").strip()
        if not (tag or target_unit_kind or target_norm or target_chapter):
            continue
        source_statute = str(item.get("source") or "").strip()
        key = (kind, source_statute, tag, target_unit_kind, target_norm, target_chapter)
        if key in seen_provenance_projection_rows:
            continue
        seen_provenance_projection_rows.add(key)
        provenance_projection_kind_counts[kind] += 1
        provenance_projection_rows.append(
            {
                "kind": kind,
                "source_statute": source_statute,
                "tag": tag,
                "target_unit_kind": target_unit_kind,
                "target_norm": target_norm,
                "target_chapter": target_chapter,
            }
        )
    provenance_projection_rows.sort(
        key=lambda item: (
            item["kind"],
            item["source_statute"],
            item["target_unit_kind"],
            item["target_norm"],
            item["target_chapter"],
            item["tag"],
        )
    )

    elaboration_kind_counts: Counter[str] = Counter()
    elaboration_stage_counts: Counter[str] = Counter()
    payload_completeness_kind_counts: Counter[str] = Counter()
    payload_completeness_tail_policy_counts: Counter[str] = Counter()
    sparse_slot_binding_count = 0
    sparse_slot_binding_labels: list[str] | set[str] = set()
    sparse_leftover_count = 0
    sparse_leftover_slot_count = 0
    sparse_leftover_labels: list[str] | set[str] = set()
    normalized_family_counts: Counter[str] = Counter()
    for item in normalized_records:
        family = str(item.get("family") or "")
        if family:
            normalized_family_counts[family] += 1
        if family == "sparse_slot_binding":
            sparse_slot_binding_count += 1
            label = str(item.get("payload_slot_label") or "")
            if label:
                sparse_slot_binding_labels.add(label)
        if family == "sparse_leftover":
            sparse_leftover_count += 1
            sparse_leftover_slot_count += int(item.get("unassigned_slot_count") or 0)
            for label in item.get("unassigned_slots") or []:
                label_str = str(label or "")
                if label_str:
                    sparse_leftover_labels.add(label_str)
        if family != "elaboration":
            continue
        kind = str(item.get("kind") or "")
        stage = str(item.get("stage") or "")
        if kind:
            elaboration_kind_counts[kind] += 1
        if stage:
            elaboration_stage_counts[stage] += 1
        if kind == "ELAB.PAYLOAD_COMPLETENESS":
            detail = cast(dict[str, object], item.get("detail")) if isinstance(item.get("detail"), dict) else {}
            payload_kind = str(
                item.get("payload_completeness_kind")
                or detail.get("payload_completeness_kind")
                or ""
            )
            tail_policy = str(
                item.get("tail_policy")
                or detail.get("tail_policy")
                or ""
            )
            if payload_kind:
                payload_completeness_kind_counts[payload_kind] += 1
            if tail_policy:
                payload_completeness_tail_policy_counts[tail_policy] += 1

    unowned_normalized_records = [item for item in normalized_records if not str(item.get("section") or "")]
    unowned_normalized_family_counts = Counter(
        str(item.get("family") or "") for item in unowned_normalized_records if str(item.get("family") or "")
    )
    unowned_normalized_row_counts = Counter(
        (
            str(item.get("family") or ""),
            str(item.get("kind") or ""),
            tuple(str(code) for code in (item.get("result_codes") or []) if str(code)),
            str(item.get("stage") or ""),
            str(item.get("source_statute") or ""),
            str(item.get("target_unit_kind") or ""),
            str(item.get("target_kind") or ""),
            str(item.get("target_norm") or ""),
            str(item.get("target_chapter") or ""),
            str(item.get("target_path") or ""),
        )
        for item in unowned_normalized_records
        if str(item.get("family") or "")
        or str(item.get("kind") or "")
        or str(item.get("stage") or "")
        or str(item.get("source_statute") or "")
        or str(item.get("target_unit_kind") or "")
        or str(item.get("target_kind") or "")
        or str(item.get("target_norm") or "")
        or str(item.get("target_chapter") or "")
        or str(item.get("target_path") or "")
    )
    unowned_normalized_rows = [
        {
            "family": family,
            "kind": kind,
            "result_codes": list(result_codes),
            "stage": stage,
            "source_statute": source_statute,
            "target_unit_kind": target_unit_kind,
            "target_kind": target_kind,
            "target_norm": target_norm,
            "target_chapter": target_chapter,
            "target_path": target_path,
            "count": count,
        }
        for (
            family,
            kind,
            result_codes,
            stage,
            source_statute,
            target_unit_kind,
            target_kind,
            target_norm,
            target_chapter,
            target_path,
        ), count in sorted(unowned_normalized_row_counts.items())
    ]

    apply_observation_families = {"apply_mutation", "apply_mutation_invariant"}
    apply_helper_counts: Counter[str] = Counter()
    for item in normalized_records:
        if str(item.get("family") or "") not in apply_observation_families:
            continue
        helper = str(item.get("helper") or "")
        if helper:
            apply_helper_counts[helper] += 1

    elaboration_projection_row_count = sum(
        1
        for item in projection_rows
        if str(item.get("kind") or "") in _FRONTEND_ELABORATION_PROJECTION_KINDS
    )

    section_rows = []
    sparse_rows = []
    # Pre-index normalized_records by section key to avoid O(B*R) nested scan.
    # Each bisect item needs records matching (section_key, blame_source OR empty source).
    _records_by_section: Dict[str, List[Dict]] = defaultdict(list)
    for record in normalized_records:
        _rec_section = str(record.get("section") or "")
        if _rec_section:
            _records_by_section[_rec_section].append(record)
    for item in section_bisect or []:
        blame_source = str(item.get("blame_source") or "")
        section_key = str(item.get("section") or "")
        matching = [
            record
            for record in _records_by_section.get(section_key, [])
            if not str(record.get("source_statute") or "") or str(record.get("source_statute") or "") == blame_source
        ]
        elaboration_kinds = sorted(
            {
                str(record.get("kind") or "")
                for record in matching
                if str(record.get("family") or "") == "elaboration"
                and str(record.get("kind") or "")
                and str(record.get("kind") or "") != "ELAB.PAYLOAD_COMPLETENESS"
            }
        )
        payload_completeness_kinds = sorted(
            {
                str(
                    record.get("payload_completeness_kind")
                    or (record.get("detail") or {}).get("payload_completeness_kind")
                    or ""
                )
                for record in matching
                if str(record.get("family") or "") == "elaboration"
                and str(record.get("kind") or "") == "ELAB.PAYLOAD_COMPLETENESS"
                and str(
                    record.get("payload_completeness_kind")
                    or (record.get("detail") or {}).get("payload_completeness_kind")
                    or ""
                )
            }
        )
        payload_completeness_tail_policies = sorted(
            {
                str(record.get("tail_policy") or (record.get("detail") or {}).get("tail_policy") or "")
                for record in matching
                if str(record.get("family") or "") == "elaboration"
                and str(record.get("kind") or "") == "ELAB.PAYLOAD_COMPLETENESS"
                and str(record.get("tail_policy") or (record.get("detail") or {}).get("tail_policy") or "")
            }
        )
        apply_helpers = sorted(
            {
                str(record.get("helper") or "")
                for record in matching
                if str(record.get("family") or "") in apply_observation_families
                and str(record.get("helper") or "")
            }
        )
        section_sparse_slot_binding_count = sum(
            1 for record in matching if str(record.get("family") or "") == "sparse_slot_binding"
        )
        section_sparse_slot_binding_labels = sorted(
            {
                str(record.get("payload_slot_label") or "")
                for record in matching
                if str(record.get("family") or "") == "sparse_slot_binding"
                and str(record.get("payload_slot_label") or "")
            }
        )
        section_sparse_leftover_count = sum(
            1 for record in matching if str(record.get("family") or "") == "sparse_leftover"
        )
        section_sparse_leftover_slot_count = sum(
            int(record.get("unassigned_slot_count") or 0)
            for record in matching if str(record.get("family") or "") == "sparse_leftover"
        )
        section_sparse_leftover_labels = sorted(
            {
                str(label or "")
                for record in matching if str(record.get("family") or "") == "sparse_leftover"
                for label in (record.get("unassigned_slots") or [])
                if str(label or "")
            }
        )
        if (
            elaboration_kinds
            or payload_completeness_kinds
            or payload_completeness_tail_policies
            or section_sparse_slot_binding_count
            or section_sparse_leftover_slot_count
        ):
            row = {
                "section": section_key,
                "blame_source": blame_source,
                "elaboration_kinds": elaboration_kinds,
                "sparse_slot_binding_count": section_sparse_slot_binding_count,
                "sparse_slot_binding_labels": section_sparse_slot_binding_labels,
                "sparse_leftover_labels": section_sparse_leftover_labels,
                "payload_completeness_kinds": payload_completeness_kinds,
                "payload_completeness_tail_policies": payload_completeness_tail_policies,
                "sparse_leftover_count": section_sparse_leftover_count,
                "sparse_leftover_slot_count": section_sparse_leftover_slot_count,
                "apply_helpers": apply_helpers,
            }
            section_rows.append(row)
            is_sparse_blocker = (
                section_sparse_leftover_slot_count > 0
                or any(kind in _SPARSE_BLOCKER_KINDS for kind in elaboration_kinds)
            )
            if bool(item.get("blame_sparse_elaboration")) and is_sparse_blocker:
                sparse_rows.append(dict(row))

    return {
        "normalized_section_observation_count": sum(1 for item in normalized_records if str(item.get("section") or "")),
        "normalized_unowned_observation_count": len(unowned_normalized_records),
        "normalized_unowned_observation_family_counts": dict(sorted(unowned_normalized_family_counts.items())),
        "normalized_unowned_observation_rows": unowned_normalized_rows,
        "normalized_observation_family_counts": dict(sorted(normalized_family_counts.items())),
        "elaboration_observation_count": sum(
            1 for item in normalized_records if str(item.get("family") or "") == "elaboration"
        ),
        "elaboration_projection_count": elaboration_projection_row_count,
        "elaboration_kind_counts": dict(sorted(elaboration_kind_counts.items())),
        "elaboration_stage_counts": dict(sorted(elaboration_stage_counts.items())),
        "payload_completeness_kind_counts": dict(sorted(payload_completeness_kind_counts.items())),
        "payload_completeness_tail_policy_counts": dict(sorted(payload_completeness_tail_policy_counts.items())),
        "provenance_projection_count": len(provenance_projection_rows),
        "provenance_projection_kind_counts": dict(sorted(provenance_projection_kind_counts.items())),
        "provenance_projection_rows": provenance_projection_rows,
        "sparse_slot_binding_count": sparse_slot_binding_count,
        "sparse_slot_binding_labels": sorted(sparse_slot_binding_labels),
        "sparse_leftover_count": sparse_leftover_count,
        "sparse_leftover_slot_count": sparse_leftover_slot_count,
        "sparse_leftover_labels": sorted(sparse_leftover_labels),
        "apply_mutation_event_count": sum(
            1
            for item in normalized_records
            if str(item.get("family") or "") in apply_observation_families
        ),
        "apply_helper_counts": dict(sorted(apply_helper_counts.items())),
        "section_bisect_observation_row_count": len(section_rows),
        "section_bisect_sparse_blocker_row_count": len(sparse_rows),
        "section_bisect_rows_with_observation_support": section_rows,
        "section_bisect_rows_with_sparse_blocker": sparse_rows,
    }


from lawvm.tools.evidence_claims import (  # noqa: E402
    _build_section_claims,  # noqa: F401 — re-exported for test compatibility
    _build_proof_claims,  # noqa: F401 — re-exported for test compatibility; kept as legacy fallback
    _primary_proof_tier,
)
from lawvm.tools.evidence_render import (  # noqa: E402
    _render_markdown_bundle,
    _write_bundle_output,
    _write_markdown_output,
    _print_evidence_bundle,
    _print_oracle_proof_bundle,
    _print_review_summary,
)


def _load_bundle_artifacts(paths: Iterable[str]) -> List[Dict]:
    bundles: List[Dict] = []
    for raw_path in paths:
        path = Path(str(raw_path))
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        if path.suffix.lower() == ".jsonl":
            for line in text.splitlines():
                line = line.strip()
                if line:
                    bundles.append(json.loads(line))
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            parsed_any = False
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                bundles.append(json.loads(line))
                parsed_any = True
            if parsed_any:
                continue
            raise
        if isinstance(payload, list):
            bundles.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            bundles.append(payload)
    return bundles




def _display_primary_tier(primary: str, proof_kinds: set[str]) -> str:
    if primary == "UNRESOLVED" and proof_kinds == {"trivially_empty"}:
        return "BENIGN_TRIVIALLY_EMPTY"
    if primary == "UNRESOLVED" and proof_kinds == {"UNRESOLVED.preexisting.baseline_residue"}:
        return "BENIGN_PREEXISTING_BASELINE"
    if primary == "UNRESOLVED" and proof_kinds == {"UNRESOLVED.source_underdetermined.amendment_improves_section"}:
        return "BENIGN_SOURCE_UNDERDETERMINED"
    return primary


def _claim_trigger_pairs(claims: Iterable[Dict]) -> List[str]:
    pairs: set[str] = set()
    for claim in claims:
        for obs in claim.get("trigger_observations", []) or []:
            source = str(obs.get("source") or "").strip()
            field = str(obs.get("field") or "").strip()
            if source and field:
                pairs.add(f"{source}.{field}")
    return sorted(pairs)


def _bundle_dict_rows(bundle: Dict, field: str) -> List[Dict[str, Any]]:
    raw_rows = bundle.get(field, []) or []
    if not isinstance(raw_rows, list):
        raise ValueError(f"evidence bundle field {field} did not decode to a JSON array")
    rows: List[Dict[str, Any]] = []
    malformed_indexes: list[int] = []
    for index, item in enumerate(raw_rows):
        if isinstance(item, dict):
            rows.append(dict(item))
            continue
        malformed_indexes.append(index)
    if malformed_indexes:
        statute_id = str(bundle.get("statute_id") or "")
        indexes = ", ".join(str(index) for index in malformed_indexes)
        raise ValueError(
            f"evidence bundle {statute_id} field {field} contains non-object entries at indexes: {indexes}"
        )
    return rows


def _evidence_context_diagnostics(bundle: Dict) -> List[Dict[str, str]]:
    diagnostics: List[Dict[str, str]] = []
    for item in _bundle_dict_rows(bundle, "evidence_context_diagnostics"):
        diagnostics.append({
            "kind": str(item.get("kind") or ""),
            "rail": str(item.get("rail") or ""),
            "exception_type": str(item.get("exception_type") or ""),
            "message": str(item.get("message") or ""),
        })
    return diagnostics


def _bundle_matches_filters(
    bundle: Dict,
    *,
    primary_tier: str = "",
    tier: str = "",
    kind: str = "",
    section_kind: str = "",
    section_rule: str = "",
    trigger_source: str = "",
    trigger_field: str = "",
    strict_fail_reason: str = "",
    elaboration_observation_kind: str = "",
    sparse_leftovers_only: bool = False,
    sparse_blocker_source: str = "",
    sparse_blocker_section: str = "",
    payload_completeness_kind: str = "",
    payload_tail_policy: str = "",
    provenance_projection_kind: str = "",
    provenance_tag: str = "",
    provenance_source_statute: str = "",
    source_proof_kind: str = "",
    source_pathology_code: str = "",
    source_pathology_source: str = "",
    source_pathology_target_label: str = "",
    source_pathology_diagnostic_reason: str = "",
    alternative_replay_section: str = "",
    html_noncommensurable_reason: str = "",
    evidence_context_degraded_only: bool = False,
    evidence_context_rail: str = "",
) -> bool:
    if primary_tier and str(bundle.get("primary_proof_tier") or "") != primary_tier:
        return False
    claims = list(bundle.get("proof_claims", []) or [])
    if tier and tier not in {str(item.get("tier") or "") for item in claims}:
        return False
    if kind and kind not in {str(item.get("kind") or "") for item in claims}:
        return False
    section_claims = list(bundle.get("section_claims", []) or [])
    if section_kind and section_kind not in {str(item.get("selected_kind") or "") for item in section_claims}:
        return False
    if section_rule and section_rule not in {str(item.get("selected_inference_rule") or "") for item in section_claims}:
        return False
    if strict_fail_reason and strict_fail_reason not in {
        str(reason or "") for reason in (bundle.get("strict_fail_reasons", []) or []) if str(reason or "")
    }:
        return False
    if elaboration_observation_kind and elaboration_observation_kind not in {
        str(obs_kind or "")
        for obs_kind in (
            ((bundle.get("compiler_observations") or {}).get("elaboration_kind_counts") or {}).keys()
        )
        if str(obs_kind or "")
    }:
        return False
    if sparse_leftovers_only and int(
        ((bundle.get("compiler_observations") or {}).get("sparse_leftover_count") or 0)
    ) <= 0:
        return False
    if sparse_blocker_source:
        if sparse_blocker_source not in {
            str(item.get("blame_source") or "")
            for item in (
                (bundle.get("compiler_observations") or {}).get(
                    "section_bisect_rows_with_sparse_blocker",
                    [],
                )
                or []
            )
            if isinstance(item, dict)
        }:
            return False
    if sparse_blocker_section:
        if sparse_blocker_section not in {
            str(item.get("section") or "")
            for item in (
                (bundle.get("compiler_observations") or {}).get(
                    "section_bisect_rows_with_sparse_blocker",
                    [],
                )
                or []
            )
            if isinstance(item, dict)
        }:
            return False
    if payload_completeness_kind and payload_completeness_kind not in {
        str(obs_kind or "")
        for obs_kind in (
            ((bundle.get("compiler_observations") or {}).get("payload_completeness_kind_counts") or {}).keys()
        )
        if str(obs_kind or "")
    }:
        return False
    if payload_tail_policy and payload_tail_policy not in {
        str(obs_kind or "")
        for obs_kind in (
            ((bundle.get("compiler_observations") or {}).get("payload_completeness_tail_policy_counts") or {}).keys()
        )
        if str(obs_kind or "")
    }:
        return False
    if provenance_projection_kind and provenance_projection_kind not in {
        str(obs_kind or "")
        for obs_kind in (
            ((bundle.get("compiler_observations") or {}).get("provenance_projection_kind_counts") or {}).keys()
        )
        if str(obs_kind or "")
    }:
        return False
    if provenance_tag and provenance_tag not in {
        str(tag or "")
        for tag in (
            str(item.get("tag") or "")
            for item in ((bundle.get("compiler_observations") or {}).get("provenance_projection_rows") or [])
            if isinstance(item, dict)
        )
        if str(tag or "")
    }:
        return False
    if provenance_source_statute and provenance_source_statute not in {
        str(source or "")
        for source in (
            str(item.get("source_statute") or "")
            for item in ((bundle.get("compiler_observations") or {}).get("provenance_projection_rows") or [])
            if isinstance(item, dict)
        )
        if str(source or "")
    }:
        return False
    if source_proof_kind and source_proof_kind not in {
        str(kind_text or "")
        for kind_text in (
            str(item.get("kind") or "")
            for item in claims
            if str(item.get("tier") or "") == "PROVED_SOURCE_PATHOLOGY"
        )
        if str(kind_text or "")
    }:
        return False
    if source_pathology_code and source_pathology_code not in {
        str(code or "")
        for code in (
            str(item.get("code") or "")
            for item in (bundle.get("source_pathologies") or [])
            if isinstance(item, dict)
        )
        if str(code or "")
    }:
        return False
    if source_pathology_source and source_pathology_source not in {
        str(source or "")
        for source in (
            str(item.get("source_statute") or "")
            for item in (bundle.get("source_pathologies") or [])
            if isinstance(item, dict)
        )
        if str(source or "")
    }:
        return False
    if source_pathology_target_label and source_pathology_target_label not in {
        str(label or "")
        for label in (
            str(item.get("target_label") or "")
            for item in (bundle.get("source_pathologies") or [])
            if isinstance(item, dict)
        )
        if str(label or "")
    }:
        return False
    if source_pathology_diagnostic_reason and source_pathology_diagnostic_reason not in {
        str(reason or "")
        for reason in (
            str(((item.get("detail") or {}).get("diagnostic_reason") or ""))
            for item in (bundle.get("source_pathologies") or [])
            if isinstance(item, dict)
        )
        if str(reason or "")
    }:
        return False
    if alternative_replay_section and alternative_replay_section not in {
        str((claim.get("alternative_replay_match") or {}).get("best_replay_section") or "")
        for claim in section_claims
        if str((claim.get("alternative_replay_match") or {}).get("best_replay_section") or "")
    }:
        return False
    if html_noncommensurable_reason and str(
        ((bundle.get("html_topology") or {}).get("noncommensurable_reason") or "")
    ) != html_noncommensurable_reason:
        return False
    evidence_context_diagnostics = _evidence_context_diagnostics(bundle)
    if evidence_context_degraded_only and not evidence_context_diagnostics:
        return False
    if evidence_context_rail and evidence_context_rail not in {
        diagnostic["rail"] for diagnostic in evidence_context_diagnostics if diagnostic["rail"]
    }:
        return False
    if trigger_source or trigger_field:
        matched = False
        for claim in claims:
            for obs in claim.get("trigger_observations", []) or []:
                source = str(obs.get("source") or "")
                field = str(obs.get("field") or "")
                if trigger_source and source != trigger_source:
                    continue
                if trigger_field and field != trigger_field:
                    continue
                matched = True
                break
            if matched:
                break
        if not matched:
            return False
    return True


def _review_bundles(
    bundles: Iterable[Dict],
    *,
    primary_tier: str = "",
    tier: str = "",
    kind: str = "",
    section_kind: str = "",
    section_rule: str = "",
    trigger_source: str = "",
    trigger_field: str = "",
    strict_fail_reason: str = "",
    elaboration_observation_kind: str = "",
    sparse_leftovers_only: bool = False,
    sparse_blocker_source: str = "",
    sparse_blocker_section: str = "",
    payload_completeness_kind: str = "",
    payload_tail_policy: str = "",
    provenance_projection_kind: str = "",
    provenance_tag: str = "",
    provenance_source_statute: str = "",
    source_proof_kind: str = "",
    source_pathology_code: str = "",
    source_pathology_source: str = "",
    source_pathology_target_label: str = "",
    source_pathology_diagnostic_reason: str = "",
    alternative_replay_section: str = "",
    html_noncommensurable_reason: str = "",
    evidence_context_degraded_only: bool = False,
    evidence_context_rail: str = "",
    actionable_unresolved_only: bool = False,
    nontrivial_unresolved_only: bool = False,
    mixed_replay_risk_only: bool = False,
    ready_oracle_artifacts_only: bool = False,
    oracle_artifact_family: str = "",
    oracle_artifact_gap: str = "",
    limit: int = 20,
) -> Dict:
    bundle_list = [dict(bundle or {}) for bundle in bundles]
    error_rows = sorted(
        [
            {
                "statute_id": str(bundle.get("statute_id") or ""),
                "error": str(bundle.get("error") or ""),
            }
            for bundle in bundle_list
            if str(bundle.get("error") or "")
        ],
        key=lambda item: (item["statute_id"], item["error"]),
    )
    ok_bundles = [bundle for bundle in bundle_list if not str(bundle.get("error") or "")]
    by_primary_tier: Counter[str] = Counter()
    by_display_primary_tier: Counter[str] = Counter()
    by_claim_kind: Counter[str] = Counter()
    by_section_claim_kind: Counter[str] = Counter()
    by_section_claim_inference_rule: Counter[str] = Counter()
    by_defeated_section_claim_kind: Counter[str] = Counter()
    by_defeated_section_claim_inference_rule: Counter[str] = Counter()
    by_strict_fail_reason: Counter[str] = Counter()
    by_elaboration_observation_kind: Counter[str] = Counter()
    by_sparse_blocker_source: Counter[str] = Counter()
    by_sparse_blocker_section: Counter[str] = Counter()
    by_payload_completeness_kind: Counter[str] = Counter()
    by_payload_tail_policy: Counter[str] = Counter()
    by_provenance_projection_kind: Counter[str] = Counter()
    by_provenance_tag: Counter[str] = Counter()
    by_provenance_source_statute: Counter[str] = Counter()
    by_source_proof_kind: Counter[str] = Counter()
    by_source_pathology_code: Counter[str] = Counter()
    by_source_pathology_source: Counter[str] = Counter()
    by_source_pathology_target_label: Counter[str] = Counter()
    by_source_pathology_diagnostic_reason: Counter[str] = Counter()
    by_alternative_replay_section: Counter[str] = Counter()
    by_html_noncommensurable_reason: Counter[str] = Counter()
    by_evidence_context_degradation_rail: Counter[str] = Counter()
    by_evidence_context_degradation_exception: Counter[str] = Counter()
    by_unresolved_exclusion_reason: Counter[str] = Counter()
    by_mixed_replay_risk_reason: Counter[str] = Counter()
    by_trigger: Counter[str] = Counter()
    by_oracle_artifact_family: Counter[str] = Counter()
    by_oracle_artifact_complexity: Counter[str] = Counter()
    by_oracle_artifact_gap: Counter[str] = Counter()

    rows: List[Dict] = []
    for bundle in ok_bundles:
        primary = str(bundle.get("primary_proof_tier") or "UNRESOLVED")
        proof_kind_set = {
            str(item.get("kind") or "")
            for item in bundle.get("proof_claims", []) or []
            if str(item.get("kind") or "")
        }
        by_primary_tier[primary] += 1
        by_display_primary_tier[_display_primary_tier(primary, proof_kind_set)] += 1
        for reason in bundle.get("strict_fail_reasons", []) or []:
            reason_text = str(reason or "")
            if reason_text:
                by_strict_fail_reason[reason_text] += 1
        for obs_kind, count in (
            (bundle.get("compiler_observations") or {}).get("elaboration_kind_counts") or {}
        ).items():
            obs_kind_text = str(obs_kind or "")
            if obs_kind_text:
                by_elaboration_observation_kind[obs_kind_text] += int(count or 0)
        for blocker in (
            (bundle.get("compiler_observations") or {}).get(
                "section_bisect_rows_with_sparse_blocker",
                [],
            )
            or []
        ):
            if not isinstance(blocker, dict):
                continue
            source_text = str(blocker.get("blame_source") or "")
            if source_text:
                by_sparse_blocker_source[source_text] += 1
            section_text = str(blocker.get("section") or "")
            if section_text:
                by_sparse_blocker_section[section_text] += 1
        for payload_kind, count in (
            (bundle.get("compiler_observations") or {}).get("payload_completeness_kind_counts") or {}
        ).items():
            payload_kind_text = str(payload_kind or "")
            if payload_kind_text:
                by_payload_completeness_kind[payload_kind_text] += int(count or 0)
        for tail_policy, count in (
            (bundle.get("compiler_observations") or {}).get("payload_completeness_tail_policy_counts") or {}
        ).items():
            tail_policy_text = str(tail_policy or "")
            if tail_policy_text:
                by_payload_tail_policy[tail_policy_text] += int(count or 0)
        for provenance_kind, count in (
            ((bundle.get("compiler_observations") or {}).get("provenance_projection_kind_counts") or {}).items()
        ):
            provenance_kind_text = str(provenance_kind or "")
            if provenance_kind_text:
                by_provenance_projection_kind[provenance_kind_text] += int(count or 0)
        for pathology in ((bundle.get("compiler_observations") or {}).get("provenance_projection_rows") or []):
            if not isinstance(pathology, dict):
                continue
            tag_text = str(pathology.get("tag") or "")
            if tag_text:
                by_provenance_tag[tag_text] += 1
            source_statute_text = str(pathology.get("source_statute") or "")
            if source_statute_text:
                by_provenance_source_statute[source_statute_text] += 1
        for pathology in bundle.get("source_pathologies", []) or []:
            if not isinstance(pathology, dict):
                continue
            code_text = str(pathology.get("code") or "")
            if code_text:
                by_source_pathology_code[code_text] += 1
            source_text = str(pathology.get("source_statute") or "")
            if source_text:
                by_source_pathology_source[source_text] += 1
            target_label_text = str(pathology.get("target_label") or "")
            if target_label_text:
                by_source_pathology_target_label[target_label_text] += 1
            diagnostic_reason_text = str(((pathology.get("detail") or {}).get("diagnostic_reason") or ""))
            if diagnostic_reason_text:
                by_source_pathology_diagnostic_reason[diagnostic_reason_text] += 1
        for claim in bundle.get("proof_claims", []) or []:
            claim_kind = str(claim.get("kind") or "")
            if claim_kind:
                by_claim_kind[claim_kind] += 1
            if str(claim.get("tier") or "") == "PROVED_SOURCE_PATHOLOGY" and claim_kind:
                by_source_proof_kind[claim_kind] += 1
            for pair in _claim_trigger_pairs([claim]):
                by_trigger[pair] += 1
        for claim in bundle.get("section_claims", []) or []:
            claim_kind = str(claim.get("selected_kind") or "")
            if claim_kind:
                by_section_claim_kind[claim_kind] += 1
            claim_rule = str(claim.get("selected_inference_rule") or "")
            if claim_rule:
                by_section_claim_inference_rule[claim_rule] += 1
            alt_section = str(((claim.get("alternative_replay_match") or {}).get("best_replay_section") or ""))
            if alt_section:
                by_alternative_replay_section[alt_section] += 1
            for defeated_kind in claim.get("defeated_candidate_kinds", []) or []:
                defeated_text = str(defeated_kind or "")
                if defeated_text:
                    by_defeated_section_claim_kind[defeated_text] += 1
            for defeated in claim.get("defeated_candidates", []) or []:
                defeated_rule = str(defeated.get("inference_rule") or "")
                if defeated_rule:
                    by_defeated_section_claim_inference_rule[defeated_rule] += 1
        html_noncomm_reason = str(((bundle.get("html_topology") or {}).get("noncommensurable_reason") or ""))
        if html_noncomm_reason:
            by_html_noncommensurable_reason[html_noncomm_reason] += 1
        evidence_context_diagnostics = _evidence_context_diagnostics(bundle)
        for diagnostic in evidence_context_diagnostics:
            rail = diagnostic["rail"]
            if rail:
                by_evidence_context_degradation_rail[rail] += 1
            exception_type = diagnostic["exception_type"]
            if exception_type:
                by_evidence_context_degradation_exception[exception_type] += 1
        artifact_summary = dict(bundle.get("artifact_summary") or {})
        for family, count in (artifact_summary.get("by_family") or {}).items():
            family_text = str(family or "")
            if family_text:
                by_oracle_artifact_family[family_text] += int(count or 0)
        for complexity, count in (artifact_summary.get("by_complexity") or {}).items():
            complexity_text = str(complexity or "")
            if complexity_text:
                by_oracle_artifact_complexity[complexity_text] += int(count or 0)
        for gap, count in (artifact_summary.get("verification_gaps") or {}).items():
            gap_text = str(gap or "")
            if gap_text:
                by_oracle_artifact_gap[gap_text] += int(count or 0)

        if not _bundle_matches_filters(
            bundle,
            primary_tier=primary_tier,
            tier=tier,
            kind=kind,
            section_kind=section_kind,
            section_rule=section_rule,
            trigger_source=trigger_source,
            trigger_field=trigger_field,
            strict_fail_reason=strict_fail_reason,
            elaboration_observation_kind=elaboration_observation_kind,
            sparse_leftovers_only=sparse_leftovers_only,
            sparse_blocker_source=sparse_blocker_source,
            sparse_blocker_section=sparse_blocker_section,
            payload_completeness_kind=payload_completeness_kind,
            payload_tail_policy=payload_tail_policy,
            provenance_projection_kind=provenance_projection_kind,
            provenance_tag=provenance_tag,
            provenance_source_statute=provenance_source_statute,
            source_proof_kind=source_proof_kind,
            source_pathology_code=source_pathology_code,
            source_pathology_source=source_pathology_source,
            source_pathology_target_label=source_pathology_target_label,
            source_pathology_diagnostic_reason=source_pathology_diagnostic_reason,
            alternative_replay_section=alternative_replay_section,
            html_noncommensurable_reason=html_noncommensurable_reason,
            evidence_context_degraded_only=evidence_context_degraded_only,
            evidence_context_rail=evidence_context_rail,
        ):
            continue
        proof_kinds = sorted(
            {
                str(item.get("kind") or "")
                for item in bundle.get("proof_claims", []) or []
                if str(item.get("kind") or "")
            }
        )
        source_proof_kinds = sorted(
            {
                str(item.get("kind") or "")
                for item in bundle.get("proof_claims", []) or []
                if str(item.get("tier") or "") == "PROVED_SOURCE_PATHOLOGY"
                and str(item.get("kind") or "")
            }
        )
        selected_section_claim_kinds = sorted(
            {
                str(item.get("selected_kind") or "")
                for item in bundle.get("section_claims", []) or []
                if str(item.get("selected_kind") or "")
            }
        )
        source_pathologies = _bundle_dict_rows(bundle, "source_pathologies")
        evidence_context_diagnostics = _evidence_context_diagnostics(bundle)
        rows.append(
            {
                "statute_id": str(bundle.get("statute_id") or ""),
                "title": str(bundle.get("title") or ""),
                "primary_proof_tier": str(bundle.get("primary_proof_tier") or "UNRESOLVED"),
                "display_primary_tier": _display_primary_tier(
                    str(bundle.get("primary_proof_tier") or "UNRESOLVED"),
                    set(proof_kinds),
                ),
                "proof_tiers": list(bundle.get("proof_tiers", []) or []),
                "proof_kinds": proof_kinds,
                "trigger_pairs": _claim_trigger_pairs(bundle.get("proof_claims", []) or []),
                "strict_fail_reasons": list(bundle.get("strict_fail_reasons", []) or []),
                "overall_score": bundle.get("overall_score"),
                "section_score": bundle.get("section_score"),
                "elaboration_observation_count": int(
                    (bundle.get("compiler_observations") or {}).get(
                        "elaboration_observation_count",
                        0,
                    )
                    or 0
                ),
                "elaboration_observation_kinds": sorted(
                    {
                        str(kind_text or "")
                        for kind_text in (
                            (
                                (bundle.get("compiler_observations") or {}).get(
                                    "elaboration_kind_counts",
                                    {},
                                )
                                or {}
                            ).keys()
                        )
                        if str(kind_text or "") and str(kind_text or "") != "ELAB.PAYLOAD_COMPLETENESS"
                    }
                ),
                "payload_completeness_kinds": sorted(
                    {
                        str(kind_text or "")
                        for kind_text in (
                            (
                                (bundle.get("compiler_observations") or {}).get(
                                    "payload_completeness_kind_counts",
                                    {},
                                )
                                or {}
                            ).keys()
                        )
                        if str(kind_text or "")
                    }
                ),
                "payload_concern_kinds": sorted(
                    {
                        str(kind_text or "")
                        for kind_text in (
                            (
                                (bundle.get("compiler_observations") or {}).get(
                                    "payload_completeness_kind_counts",
                                    {},
                                )
                                or {}
                            ).keys()
                        )
                        if str(kind_text or "") and str(kind_text or "") != "complete"
                    }
                ),
                "payload_tail_policies": sorted(
                    {
                        str(kind_text or "")
                        for kind_text in (
                            (
                                (bundle.get("compiler_observations") or {}).get(
                                    "payload_completeness_tail_policy_counts",
                                    {},
                                )
                                or {}
                            ).keys()
                        )
                        if str(kind_text or "")
                    }
                ),
                "payload_concern_tail_policies": sorted(
                    {
                        str(kind_text or "")
                        for kind_text in (
                            (
                                (bundle.get("compiler_observations") or {}).get(
                                    "payload_completeness_tail_policy_counts",
                                    {},
                                )
                                or {}
                            ).keys()
                        )
                        if str(kind_text or "") and str(kind_text or "") != "replace_if_target_scope_requires"
                    }
                ),
                "provenance_projection_count": int(
                    (bundle.get("compiler_observations") or {}).get(
                        "provenance_projection_count",
                        0,
                    )
                    or 0
                ),
                "provenance_projection_kinds": sorted(
                    {
                        str(kind_text or "")
                        for kind_text in (
                            (
                                (bundle.get("compiler_observations") or {}).get(
                                    "provenance_projection_kind_counts",
                                    {},
                                )
                                or {}
                            ).keys()
                        )
                        if str(kind_text or "")
                    }
                ),
                "provenance_projection_rows": [
                    {
                        "kind": str(item.get("kind") or ""),
                        "source_statute": str(item.get("source_statute") or ""),
                        "tag": str(item.get("tag") or ""),
                        "target_unit_kind": str(item.get("target_unit_kind") or ""),
                        "target_norm": str(item.get("target_norm") or ""),
                        "target_chapter": str(item.get("target_chapter") or ""),
                    }
                    for item in (
                        (bundle.get("compiler_observations") or {}).get("provenance_projection_rows")
                        or []
                    )
                    if isinstance(item, dict)
                ],
                "source_proof_kinds": source_proof_kinds,
                "source_pathology_count": len(source_pathologies),
                "source_pathology_codes": sorted(
                    {
                        str(item.get("code") or "")
                        for item in source_pathologies
                        if str(item.get("code") or "")
                    }
                ),
                "source_pathology_sources": sorted(
                    {
                        str(item.get("source_statute") or "")
                        for item in source_pathologies
                        if str(item.get("source_statute") or "")
                    }
                ),
                "source_pathology_diagnostic_reasons": sorted(
                    {
                        str(((item.get("detail") or {}).get("diagnostic_reason") or ""))
                        for item in source_pathologies
                        if str(((item.get("detail") or {}).get("diagnostic_reason") or ""))
                    }
                ),
                "source_pathologies": [
                    {
                        "code": str(item.get("code") or ""),
                        "source_statute": str(item.get("source_statute") or ""),
                        "target_label": str(item.get("target_label") or ""),
                        "diagnostic_reason": str(((item.get("detail") or {}).get("diagnostic_reason") or "")),
                    }
                    for item in source_pathologies
                    if (
                        str(item.get("code") or "")
                        or str(item.get("source_statute") or "")
                        or str(item.get("target_label") or "")
                        or str(((item.get("detail") or {}).get("diagnostic_reason") or ""))
                    )
                ],
                "sparse_slot_binding_count": int(
                    (bundle.get("compiler_observations") or {}).get(
                        "sparse_slot_binding_count",
                        0,
                    )
                    or 0
                ),
                "sparse_leftover_count": int(
                    (
                        (bundle.get("compiler_observations") or {}).get("sparse_leftover_count")
                        if isinstance((bundle.get("compiler_observations") or {}).get("sparse_leftover_count"), (int, float, str))
                        else None
                    )
                    or (bundle.get("compiler_observations") or {}).get(
                        "sparse_leftover_count",
                        0,
                    )
                    or 0
                ),
                "sparse_leftover_labels": list(
                    (
                        (bundle.get("compiler_observations") or {}).get(
                            "sparse_leftover_labels",
                            [],
                        )
                    )
                    or []
                ),
                "sparse_blockers": [
                    {
                        "source_statute": str(item.get("blame_source") or ""),
                        "section": str(item.get("section") or ""),
                    }
                    for item in (
                        (bundle.get("compiler_observations") or {}).get(
                            "section_bisect_rows_with_sparse_blocker",
                            [],
                        )
                        or []
                    )
                    if isinstance(item, dict)
                    and (str(item.get("blame_source") or "") or str(item.get("section") or ""))
                ],
                "sparse_blocker_count": sum(
                    1
                    for item in (
                        (bundle.get("compiler_observations") or {}).get(
                            "section_bisect_rows_with_sparse_blocker",
                            [],
                        )
                        or []
                    )
                    if isinstance(item, dict)
                    and (str(item.get("blame_source") or "") or str(item.get("section") or ""))
                ),
                "sparse_blocker_sources": sorted(
                    {
                        str(item.get("blame_source") or "")
                        for item in (
                            (bundle.get("compiler_observations") or {}).get(
                                "section_bisect_rows_with_sparse_blocker",
                                [],
                            )
                            or []
                        )
                        if isinstance(item, dict) and str(item.get("blame_source") or "")
                    }
                ),
                "sparse_blocker_sections": sorted(
                    {
                        str(item.get("section") or "")
                        for item in (
                            (bundle.get("compiler_observations") or {}).get(
                                "section_bisect_rows_with_sparse_blocker",
                                [],
                            )
                            or []
                        )
                        if isinstance(item, dict) and str(item.get("section") or "")
                    }
                ),
                "apply_mutation_event_count": int(
                    (bundle.get("compiler_observations") or {}).get(
                        "apply_mutation_event_count",
                        0,
                    )
                    or 0
                ),
                "section_bisect_observation_row_count": int(
                    (bundle.get("compiler_observations") or {}).get(
                        "section_bisect_observation_row_count",
                        0,
                    )
                    or 0
                ),
                "section_claim_count": len(bundle.get("section_claims", []) or []),
                "selected_section_claim_count": sum(
                    1 for item in bundle.get("section_claims", []) or [] if str(item.get("selected_kind") or "")
                ),
                "selected_section_claim_kinds": selected_section_claim_kinds,
                "statute_only_proof_kinds": [
                    claim_kind for claim_kind in proof_kinds if claim_kind not in set(selected_section_claim_kinds)
                ],
                "selected_section_claim_rules": sorted(
                    {
                        str(item.get("selected_inference_rule") or "")
                        for item in bundle.get("section_claims", []) or []
                        if str(item.get("selected_inference_rule") or "")
                    }
                ),
                "defeated_section_claim_kinds": sorted(
                    {
                        str(kind or "")
                        for item in bundle.get("section_claims", []) or []
                        for kind in (item.get("defeated_candidate_kinds", []) or [])
                        if str(kind or "")
                    }
                ),
                "defeated_section_claim_rules": sorted(
                    {
                        str(defeated.get("inference_rule") or "")
                        for item in bundle.get("section_claims", []) or []
                        for defeated in (item.get("defeated_candidates", []) or [])
                        if str(defeated.get("inference_rule") or "")
                    }
                ),
                "alternative_replay_match_count": sum(
                    1
                    for item in bundle.get("section_claims", []) or []
                    if (item.get("alternative_replay_match") or {}).get("best_replay_section")
                ),
                "alternative_replay_sections": sorted(
                    {
                        str((item.get("alternative_replay_match") or {}).get("best_replay_section") or "")
                        for item in bundle.get("section_claims", []) or []
                        if str((item.get("alternative_replay_match") or {}).get("best_replay_section") or "")
                    }
                ),
                "html_noncommensurable_reason": str(
                    ((bundle.get("html_topology") or {}).get("noncommensurable_reason") or "")
                ),
                "evidence_context_degradation_count": len(evidence_context_diagnostics),
                "evidence_context_degradation_rails": sorted(
                    {diagnostic["rail"] for diagnostic in evidence_context_diagnostics if diagnostic["rail"]}
                ),
                "evidence_context_degradations": evidence_context_diagnostics,
                "oracle_artifact_count": int(
                    (bundle.get("artifact_summary") or {}).get("total_artifact_count", 0) or 0
                ),
                "ready_oracle_artifact_count": int(
                    (bundle.get("artifact_summary") or {}).get("ready_total_artifact_count", 0) or 0
                ),
                "oracle_artifact_families": sorted(
                    {
                        str(family or "")
                        for family in ((bundle.get("artifact_summary") or {}).get("by_family") or {}).keys()
                        if str(family or "")
                    }
                ),
                "oracle_artifact_complexities": sorted(
                    {
                        str(kind or "")
                        for kind in ((bundle.get("artifact_summary") or {}).get("by_complexity") or {}).keys()
                        if str(kind or "")
                    }
                ),
                "oracle_artifact_verification_gaps": sorted(
                    {
                        str(gap or "")
                        for gap in ((bundle.get("artifact_summary") or {}).get("verification_gaps") or {}).keys()
                        if str(gap or "")
                    }
                ),
            }
        )

    for row in rows:
        exclusion_reasons: List[str] = []
        mixed_replay_risk_reasons: List[str] = []
        primary = str(row.get("primary_proof_tier") or "UNRESOLVED")
        proof_kinds = {str(kind or "") for kind in (row.get("proof_kinds") or []) if str(kind or "")}
        selected_section_claim_kinds = {
            str(kind or "") for kind in (row.get("selected_section_claim_kinds") or []) if str(kind or "")
        }
        statute_only_proof_kinds = {
            str(kind or "") for kind in (row.get("statute_only_proof_kinds") or []) if str(kind or "")
        }
        strict_fail_reasons = {
            str(reason or "") for reason in (row.get("strict_fail_reasons") or []) if str(reason or "")
        }
        if primary == "UNRESOLVED":
            if "no_strong_claim" in proof_kinds:
                exclusion_reasons.append("no_strong_claim")
            if selected_section_claim_kinds and selected_section_claim_kinds <= {
                "preexisting_baseline_residue",
                "preexisting_elaboration_ambiguity",
                "preexisting_same_chapter_section_drift",
                "preexisting_same_section_structure_drift",
                # New prefixed kinds
                "UNRESOLVED.preexisting.baseline_residue",
                "UNRESOLVED.preexisting.elaboration_ambiguity",
                "UNRESOLVED.address_projection.same_chapter_section_drift",
                "UNRESOLVED.preexisting.same_section_structure_drift",
            }:
                exclusion_reasons.append("preexisting_only_selected_claims")
            if {
                "preexisting_baseline_residue",
                "preexisting_same_chapter_section_drift",
                "UNRESOLVED.preexisting.baseline_residue",
                "UNRESOLVED.address_projection.same_chapter_section_drift",
            } & proof_kinds:
                exclusion_reasons.append("preexisting_proof_signal")
            if statute_only_proof_kinds and not selected_section_claim_kinds:
                exclusion_reasons.append("statute_only_proof")
            if selected_section_claim_kinds and not (
                selected_section_claim_kinds & _ACTIONABLE_UNRESOLVED_SELECTED_KINDS
            ):
                exclusion_reasons.append("non_actionable_selected_claims")
            if strict_fail_reasons and strict_fail_reasons <= {
                "TIME.CONTINGENT_EFFECTIVE_DATE",
                "PARSE.EXTRACTION_FALLBACK",
                "APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
                "APPLY.SOURCE_INCOMPLETE",
                "APPLY.UNCOVERED_BODY_RECOVERY",
            }:
                exclusion_reasons.append("pathology_heavy_strict_fail")
        if primary != "PROVED_REPLAY_BUG" and "replay_divergence" in selected_section_claim_kinds:
            mixed_replay_risk_reasons.append("selected_replay_divergence")
            for reason in sorted(strict_fail_reasons & _MIXED_REPLAY_RISK_STRICT_FAIL_REASONS):
                mixed_replay_risk_reasons.append(f"strict_fail:{reason}")
        row["actionable_unresolved"] = primary == "UNRESOLVED" and not exclusion_reasons
        row["nontrivial_unresolved"] = not (primary == "UNRESOLVED" and proof_kinds == {"trivially_empty"})
        row["unresolved_exclusion_reasons"] = sorted(set(exclusion_reasons))
        row["mixed_replay_risk"] = len(mixed_replay_risk_reasons) > 1
        row["mixed_replay_risk_reasons"] = mixed_replay_risk_reasons
        for reason in row["unresolved_exclusion_reasons"]:
            by_unresolved_exclusion_reason[reason] += 1
        for reason in row["mixed_replay_risk_reasons"]:
            by_mixed_replay_risk_reason[reason] += 1

    if actionable_unresolved_only:
        rows = [row for row in rows if bool(row.get("actionable_unresolved"))]
    if nontrivial_unresolved_only:
        rows = [row for row in rows if bool(row.get("nontrivial_unresolved", True))]
    if mixed_replay_risk_only:
        rows = [row for row in rows if bool(row.get("mixed_replay_risk"))]
    if ready_oracle_artifacts_only:
        rows = [row for row in rows if int(row.get("ready_oracle_artifact_count") or 0) > 0]
    if oracle_artifact_family:
        wanted_family = str(oracle_artifact_family or "")
        rows = [row for row in rows if wanted_family in set(row.get("oracle_artifact_families") or [])]
    if oracle_artifact_gap:
        wanted_gap = str(oracle_artifact_gap or "")
        rows = [row for row in rows if wanted_gap in set(row.get("oracle_artifact_verification_gaps") or [])]

    rows.sort(
        key=lambda item: (
            _PRIMARY_TIER_ORDER.index(item["primary_proof_tier"])
            if item["primary_proof_tier"] in _PRIMARY_TIER_ORDER
            else len(_PRIMARY_TIER_ORDER),
            item["statute_id"],
        )
    )
    # Pro adversarial review #1: report 4 honest denominators
    _processable = [b for b in bundle_list if b not in error_rows]
    _classified = [
        r
        for r in rows
        if r.get("primary_proof_tier") != "UNRESOLVED"
        or r.get("proof_claims", [{}])[0].get("kind") != "no_strong_claim"
    ]
    _strict_clean = [r for r in rows if not r.get("strict_fail_reasons")]
    _chain_complete_count = sum(
        1 for b in ok_bundles
        if bool((b.get("chain_completeness") or {}).get("is_complete"))
    )
    return {
        "bundle_count": len(bundle_list),
        "error_count": len(error_rows),
        "processable_count": len(bundle_list) - len(error_rows),
        "classified_count": len(_classified),
        "strict_clean_count": len(_strict_clean),
        "chain_complete_count": _chain_complete_count,
        "by_error": dict(sorted(Counter(item["error"] for item in error_rows).items())),
        "selected_count": len(rows),
        "filters": {
            "primary_tier": primary_tier,
            "tier": tier,
            "kind": kind,
            "section_kind": section_kind,
            "section_rule": section_rule,
            "trigger_source": trigger_source,
            "trigger_field": trigger_field,
            "strict_fail_reason": strict_fail_reason,
            "elaboration_observation_kind": elaboration_observation_kind,
            "sparse_leftovers_only": sparse_leftovers_only,
            "sparse_blocker_source": sparse_blocker_source,
            "sparse_blocker_section": sparse_blocker_section,
            "payload_completeness_kind": payload_completeness_kind,
            "payload_tail_policy": payload_tail_policy,
            "provenance_projection_kind": provenance_projection_kind,
            "provenance_tag": provenance_tag,
            "provenance_source_statute": provenance_source_statute,
            "source_proof_kind": source_proof_kind,
            "source_pathology_code": source_pathology_code,
            "source_pathology_source": source_pathology_source,
            "source_pathology_target_label": source_pathology_target_label,
            "source_pathology_diagnostic_reason": source_pathology_diagnostic_reason,
            "alternative_replay_section": alternative_replay_section,
            "html_noncommensurable_reason": html_noncommensurable_reason,
            "evidence_context_degraded_only": evidence_context_degraded_only,
            "evidence_context_rail": evidence_context_rail,
            "actionable_unresolved_only": actionable_unresolved_only,
            "nontrivial_unresolved_only": nontrivial_unresolved_only,
            "mixed_replay_risk_only": mixed_replay_risk_only,
            "ready_oracle_artifacts_only": ready_oracle_artifacts_only,
            "oracle_artifact_family": oracle_artifact_family,
            "oracle_artifact_gap": oracle_artifact_gap,
            "limit": limit,
        },
        "by_primary_tier": dict(sorted(by_primary_tier.items())),
        "by_display_primary_tier": dict(sorted(by_display_primary_tier.items())),
        "by_claim_kind": dict(sorted(by_claim_kind.items())),
        "by_section_claim_kind": dict(sorted(by_section_claim_kind.items())),
        "by_section_claim_inference_rule": dict(sorted(by_section_claim_inference_rule.items())),
        "by_defeated_section_claim_kind": dict(sorted(by_defeated_section_claim_kind.items())),
        "by_defeated_section_claim_inference_rule": dict(sorted(by_defeated_section_claim_inference_rule.items())),
        "by_strict_fail_reason": dict(sorted(by_strict_fail_reason.items())),
        "by_elaboration_observation_kind": dict(sorted(by_elaboration_observation_kind.items())),
        "by_sparse_blocker_source": dict(sorted(by_sparse_blocker_source.items())),
        "by_sparse_blocker_section": dict(sorted(by_sparse_blocker_section.items())),
        "by_payload_completeness_kind": dict(sorted(by_payload_completeness_kind.items())),
        "by_payload_tail_policy": dict(sorted(by_payload_tail_policy.items())),
        "by_provenance_projection_kind": dict(sorted(by_provenance_projection_kind.items())),
        "by_provenance_tag": dict(sorted(by_provenance_tag.items())),
        "by_provenance_source_statute": dict(sorted(by_provenance_source_statute.items())),
        "by_source_proof_kind": dict(sorted(by_source_proof_kind.items())),
        "by_source_pathology_code": dict(sorted(by_source_pathology_code.items())),
        "by_source_pathology_source": dict(sorted(by_source_pathology_source.items())),
        "by_source_pathology_target_label": dict(sorted(by_source_pathology_target_label.items())),
        "by_source_pathology_diagnostic_reason": dict(sorted(by_source_pathology_diagnostic_reason.items())),
        "by_alternative_replay_section": dict(sorted(by_alternative_replay_section.items())),
        "by_html_noncommensurable_reason": dict(sorted(by_html_noncommensurable_reason.items())),
        "by_evidence_context_degradation_rail": dict(sorted(by_evidence_context_degradation_rail.items())),
        "by_evidence_context_degradation_exception": dict(sorted(by_evidence_context_degradation_exception.items())),
        "by_unresolved_exclusion_reason": dict(sorted(by_unresolved_exclusion_reason.items())),
        "by_mixed_replay_risk_reason": dict(sorted(by_mixed_replay_risk_reason.items())),
        "by_trigger": dict(sorted(by_trigger.items())),
        "by_oracle_artifact_family": dict(sorted(by_oracle_artifact_family.items())),
        "by_oracle_artifact_complexity": dict(sorted(by_oracle_artifact_complexity.items())),
        "by_oracle_artifact_gap": dict(sorted(by_oracle_artifact_gap.items())),
        "actionable_unresolved_count": sum(1 for row in rows if bool(row.get("actionable_unresolved"))),
        "nontrivial_unresolved_count": sum(1 for row in rows if bool(row.get("nontrivial_unresolved", True))),
        "mixed_replay_risk_count": sum(1 for row in rows if bool(row.get("mixed_replay_risk"))),
        "ready_oracle_artifact_count": sum(int(row.get("ready_oracle_artifact_count") or 0) for row in rows),
        "evidence_context_degraded_count": sum(
            1 for row in rows if int(row.get("evidence_context_degradation_count") or 0) > 0
        ),
        "error_rows": error_rows,
        "rows": rows[: max(limit, 0)],
    }


def _build_live_review_bundle_one(
    statute_id: str,
    *,
    jurisdiction: str = "fi",
    mode: str = "legal_pit",
    include_bisect: bool = False,
    corpus_store_mode: str = "",
    cache_only: bool = False,
    bundle_cache_dir: str = "",
    oracle_only: bool = False,
    allow_metadata_backfill: Optional[bool] = None,
    allow_oracle_alignment: Optional[bool] = None,
    applicability_mode: Optional[str] = None,
    authority_mode: Optional[str] = None,
) -> Dict:
    jurisdiction = _assert_live_evidence_review_supported(jurisdiction)
    cache_path: Optional[Path] = None
    if bundle_cache_dir:
        cache_path = _bundle_cache_path(
            bundle_cache_dir,
            statute_id,
            jurisdiction=jurisdiction,
            mode=mode,
            include_bisect=include_bisect,
            corpus_store_mode=corpus_store_mode,
            cache_only=cache_only,
            oracle_only=oracle_only,
            allow_metadata_backfill=allow_metadata_backfill,
            allow_oracle_alignment=allow_oracle_alignment,
            applicability_mode=applicability_mode,
            authority_mode=authority_mode,
        )
        if cache_path.exists():
            cached_bundle = _read_cached_bundle(cache_path)
            if isinstance(cached_bundle, dict):
                return cached_bundle
    try:
        if str(jurisdiction or "fi").lower() == "uk":
            bundle = _call_with_supported_kwargs(
                build_uk_evidence_bundle,
                statute_id,
                mode=mode,
                include_bisect=include_bisect,
                allow_metadata_backfill=allow_metadata_backfill,
                allow_oracle_alignment=allow_oracle_alignment,
                applicability_mode=applicability_mode,
                authority_mode=authority_mode,
            )
        elif str(jurisdiction or "fi").lower() == "ee":
            bundle = build_ee_evidence_bundle(
                statute_id,
                mode=mode,
                include_bisect=include_bisect,
            )
        else:
            with _temporary_corpus_env(
                corpus_store_mode=corpus_store_mode,
                cache_only=cache_only,
            ):
                if oracle_only:
                    bundle = build_oracle_proof_bundle(
                        statute_id,
                        mode=mode,
                        include_bisect=include_bisect,
                    )
                else:
                    bundle = build_evidence_bundle(
                        statute_id,
                        mode=mode,
                        include_bisect=include_bisect,
                    )
        if cache_path is not None:
            _write_cached_bundle(cache_path, bundle)
        return bundle
    except (NameError, TypeError, AttributeError) as exc:
        # Programming bugs — record with full traceback so the evidence run
        # continues and the bug is diagnosable (ProcessPoolExecutor pickles
        # exceptions across process boundaries, losing the original traceback).
        import traceback as _tb
        bundle = {
            "statute_id": str(statute_id),
            "mode": mode,
            "error": f"PROGRAMMING_BUG: {type(exc).__name__}: {exc}",
            "traceback": _tb.format_exc(),
        }
        print(
            f"[evidence] PROGRAMMING BUG processing {statute_id}: "
            f"{type(exc).__name__}: {exc}\n{''.join(_tb.format_exception(exc))}",
            file=sys.stderr,
        )
        if cache_path is not None:
            _write_cached_bundle(cache_path, bundle)
        return bundle
    except Exception as exc:
        bundle = {
            "statute_id": str(statute_id),
            "mode": mode,
            "error": str(exc),
        }
        if cache_path is not None:
            _write_cached_bundle(cache_path, bundle)
        return bundle


def _build_live_review_bundles(
    statute_ids: Iterable[str],
    *,
    jurisdiction: str = "fi",
    mode: str = "legal_pit",
    include_bisect: bool = False,
    workers: int = 1,
    corpus_store_mode: str = "",
    cache_only: bool = False,
    bundle_cache_dir: str = "",
    cache_stats_out: Optional[Dict[str, Any]] = None,
    oracle_only: bool = False,
    allow_metadata_backfill: Optional[bool] = None,
    allow_oracle_alignment: Optional[bool] = None,
    applicability_mode: Optional[str] = None,
    authority_mode: Optional[str] = None,
) -> List[Dict]:
    statute_id_list = [str(statute_id) for statute_id in statute_ids if str(statute_id)]
    if not statute_id_list:
        return []
    worker_count = max(1, int(workers or 1))

    bundles: List[Optional[Dict]] = [None] * len(statute_id_list)
    cache_hits = 0
    cache_misses = 0
    cache_errors = 0
    miss_items: List[tuple[int, str]] = []
    for idx, statute_id in enumerate(statute_id_list):
        hit, cached_bundle, cache_error = _load_cached_review_bundle(
            statute_id,
            jurisdiction=jurisdiction,
            mode=mode,
            include_bisect=include_bisect,
            corpus_store_mode=corpus_store_mode,
            cache_only=cache_only,
            bundle_cache_dir=bundle_cache_dir,
            oracle_only=oracle_only,
            allow_metadata_backfill=allow_metadata_backfill,
            allow_oracle_alignment=allow_oracle_alignment,
            applicability_mode=applicability_mode,
            authority_mode=authority_mode,
        )
        if hit and cached_bundle is not None:
            bundles[idx] = cached_bundle
            cache_hits += 1
            continue
        if cache_error:
            cache_errors += 1
        cache_misses += 1
        miss_items.append((idx, statute_id))

    if miss_items:
        if worker_count <= 1 or len(miss_items) <= 1:
            for idx, statute_id in miss_items:
                bundles[idx] = _build_live_review_bundle_one(
                    statute_id,
                    jurisdiction=jurisdiction,
                    mode=mode,
                    include_bisect=include_bisect,
                    corpus_store_mode=corpus_store_mode,
                    cache_only=cache_only,
                    bundle_cache_dir=bundle_cache_dir,
                    oracle_only=oracle_only,
                    allow_metadata_backfill=allow_metadata_backfill,
                    allow_oracle_alignment=allow_oracle_alignment,
                    applicability_mode=applicability_mode,
                    authority_mode=authority_mode,
                )
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(
                        _build_live_review_bundle_one,
                        statute_id,
                        jurisdiction=jurisdiction,
                        mode=mode,
                        include_bisect=include_bisect,
                        corpus_store_mode=corpus_store_mode,
                        cache_only=cache_only,
                        bundle_cache_dir=bundle_cache_dir,
                        oracle_only=oracle_only,
                        allow_metadata_backfill=allow_metadata_backfill,
                        allow_oracle_alignment=allow_oracle_alignment,
                        applicability_mode=applicability_mode,
                        authority_mode=authority_mode,
                    ): idx
                    for idx, statute_id in miss_items
                }
                for future in concurrent.futures.as_completed(future_map):
                    idx = future_map[future]
                    bundles[idx] = future.result()

    if cache_stats_out is not None:
        cache_stats_out["bundle_cache_hits"] = int(cache_stats_out.get("bundle_cache_hits", 0) or 0) + cache_hits
        cache_stats_out["bundle_cache_misses"] = int(cache_stats_out.get("bundle_cache_misses", 0) or 0) + cache_misses
        cache_stats_out["bundle_cache_errors"] = int(cache_stats_out.get("bundle_cache_errors", 0) or 0) + cache_errors

    return [bundle for bundle in bundles if bundle is not None]


_REVIEW_COUNT_FIELDS = (
    "by_primary_tier",
    "by_claim_kind",
    "by_section_claim_kind",
    "by_section_claim_inference_rule",
    "by_defeated_section_claim_kind",
    "by_defeated_section_claim_inference_rule",
    "by_strict_fail_reason",
    "by_elaboration_observation_kind",
    "by_payload_completeness_kind",
    "by_payload_tail_policy",
    "by_provenance_projection_kind",
    "by_provenance_tag",
    "by_provenance_source_statute",
    "by_source_proof_kind",
    "by_source_pathology_code",
    "by_source_pathology_source",
    "by_source_pathology_target_label",
    "by_source_pathology_diagnostic_reason",
    "by_alternative_replay_section",
    "by_html_noncommensurable_reason",
    "by_evidence_context_degradation_rail",
    "by_evidence_context_degradation_exception",
    "by_oracle_artifact_family",
    "by_oracle_artifact_complexity",
    "by_oracle_artifact_gap",
    "by_unresolved_exclusion_reason",
    "by_mixed_replay_risk_reason",
    "by_trigger",
    "by_error",
)


def _supports_live_evidence_review_jurisdiction(jurisdiction: str) -> bool:
    j = str(jurisdiction or "fi").lower()
    return j in {"fi", "ee", "uk"}


def _assert_live_evidence_review_supported(jurisdiction: str) -> str:
    j = str(jurisdiction or "fi").lower()
    if not _supports_live_evidence_review_jurisdiction(j):
        raise ValueError(
            f"unsupported jurisdiction {j!r} for live evidence-review "
            "(supported: fi, uk)"
        )
    return j


def _oracle_corpus_statute_ids(
    *,
    jurisdiction: str = "fi",
    corpus_store_mode: str = "",
    cache_only: bool = False,
) -> List[str]:
    normalized = str(jurisdiction or "fi").lower()
    if normalized == "uk":
        return _uk_oracle_corpus_statute_ids()
    if normalized == "ee":
        return _ee_oracle_corpus_statute_ids()
    from lawvm.corpus_store import get_corpus_store

    with _temporary_corpus_env(
        corpus_store_mode=corpus_store_mode,
        cache_only=cache_only,
    ):
        corpus = get_corpus_store()
        oracle_index_fn = getattr(corpus, "oracle_path_index")
        if cache_only:
            try:
                oracle_index = oracle_index_fn()
            except TypeError:
                oracle_index = oracle_index_fn()
        else:
            oracle_index = oracle_index_fn()
        return sorted(
            str(sid)
            for sid in oracle_index.keys()
            if not is_known_missing_source(str(sid))
        )


def _sort_by_chain_length_desc(sids: List[str]) -> List[str]:
    """Sort statute IDs longest-amendment-chain-first.

    Replay time scales with amendment count, so submitting the longest chains
    first to ProcessPoolExecutor prevents a few large statutes from stalling
    completion while workers sit idle (long-tail parallelism effect).
    Uses the cached amendment index — effectively free.
    """
    from lawvm.finland.grafter import _amendment_children_by_parent

    try:
        children = _amendment_children_by_parent()
    except (OSError, RuntimeError):
        return list(sids)
    return sorted(sids, key=lambda s: len(children.get(s, ())), reverse=True)


def _statute_year(statute_id: str, *, jurisdiction: str = "fi") -> int:
    text = str(statute_id or "").strip()
    normalized = str(jurisdiction or "fi").lower()
    if normalized == "uk":
        parts = text.split("/")
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1])
        return 0
    if normalized == "ee":
        if len(text) >= 9 and text[5:9].isdigit():
            return int(text[5:9])
        return 0
    prefix = text[:4]
    return int(prefix) if len(prefix) == 4 and prefix.isdigit() else 0


def _uk_archive_url_for_statute(statute_id: str, *, enacted: bool = False) -> str:
    base = f"https://www.legislation.gov.uk/{statute_id}"
    return f"{base}/enacted/data.xml" if enacted else f"{base}/data.xml"


def _extract_uk_statute_id(locator: str, suffix: str) -> str:
    text = str(locator or "")
    if not text.endswith(suffix):
        return ""
    prefix = "https://www.legislation.gov.uk/"
    if not text.startswith(prefix):
        return ""
    sid = text[len(prefix) : -len(suffix)].strip("/")
    parts = sid.split("/")
    if len(parts) != 3:
        return ""
    act_type, year, number = parts
    if not year.isdigit() or not number.isdigit():
        return ""
    return sid


def _uk_oracle_corpus_statute_ids() -> List[str]:
    from farchive import Farchive

    enacted: set[str] = set()
    current: set[str] = set()
    with Farchive(_DEFAULT_UK_FARCHIVE) as archive:
        for locator in archive.locators("%/enacted/data.xml"):
            sid = _extract_uk_statute_id(str(locator), "/enacted/data.xml")
            if sid:
                enacted.add(sid)
        for locator in archive.locators("%/data.xml"):
            text = str(locator)
            if "/enacted/" in text or "/changes/" in text:
                continue
            sid = _extract_uk_statute_id(text, "/data.xml")
            if sid:
                current.add(sid)
    return sorted(enacted & current)


def _ee_oracle_corpus_pairs() -> List[tuple[str, str]]:
    """Return (base_id, oracle_id) pairs for EE live review.

    Uses the current/latest EE replayable corpus used for publication and
    evidence review.
    """
    from lawvm.tools.ee_bench import _CORPUS_CSV, _load_corpus_csv

    if not _CORPUS_CSV.exists():
        raise RuntimeError(
            "EE evidence-review oracle corpus requires the current EE replayable corpus CSV"
        )
    pairs, _ = _load_corpus_csv(_CORPUS_CSV, include_decrees=True)
    return [(str(base_id), str(oracle_id)) for _, base_id, oracle_id in pairs]


def _ee_oracle_corpus_statute_ids() -> List[str]:
    return [oracle_id for _, oracle_id in _ee_oracle_corpus_pairs()]


def _resolve_ee_live_review_pair(statute_id: str) -> tuple[str, str] | None:
    text = str(statute_id or "").strip()
    if not text:
        return None
    for base_id, oracle_id in _ee_oracle_corpus_pairs():
        if text == oracle_id or text == base_id:
            return base_id, oracle_id
    return None


def _finlex_original_url(statute_id: str) -> str:
    text = str(statute_id or "").strip()
    if "/" not in text:
        return ""
    year, num = text.split("/", 1)
    if not (year.isdigit() and num.isdigit()):
        return ""
    return f"https://www.finlex.fi/fi/laki/alkup/{year}/{year}{int(num):04d}"


def _finlex_section_url(statute_id: str, section: str = "") -> str:
    base = _finlex_html_url(statute_id)
    label = _section_label_from_key(str(section or ""))
    if base and label and re.fullmatch(r"\d+[a-z]?", label, flags=re.I):
        return f"{base}#P{label}"
    return base


def _canonical_amendment_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    m = re.fullmatch(r"\d{1,2}\.\d{1,2}\.(\d{4})/(\d+)", text)
    if m:
        return f"{m.group(1)}/{int(m.group(2))}"
    m = re.fullmatch(r"(\d+)/(\d{4})", text)
    if m:
        return f"{m.group(2)}/{int(m.group(1))}"
    return text


def _oracle_text_repeal_source_id(text: str) -> str:
    raw = str(text or "")
    if "on kumottu" not in raw:
        return ""
    match = _ORACLE_REPEAL_SOURCE_RE.search(raw)
    if not match:
        return ""
    return _canonical_amendment_id(match.group(1))


def _oracle_text_temporary_source_id(text: str) -> str:
    raw = str(text or "")
    if "väliaikaisesti voimassa" not in raw:
        return ""
    match = _ORACLE_TEMPORARY_SOURCE_RE.search(raw)
    if not match:
        return ""
    return _canonical_amendment_id(match.group(1))


def _recover_oracle_artifact_blame_source(
    item: Dict[str, Any],
    *,
    bisect_row: Optional[Dict[str, Any]] = None,
) -> str:
    blame_source = str(item.get("blame_source") or "")
    if blame_source:
        return blame_source
    if bisect_row:
        blame_source = str(bisect_row.get("blame_source") or "")
        if blame_source:
            return blame_source
        first_drop = str(bisect_row.get("first_drop_source") or "")
        if first_drop:
            worst_sources = {
                str(drop.get("source_id") or "")
                for drop in list(bisect_row.get("worst_drops") or [])
                if str(drop.get("source_id") or "")
            }
            if not worst_sources or worst_sources == {first_drop}:
                return first_drop
    oracle_text = str(item.get("oracle_text") or "")
    return _oracle_text_repeal_source_id(oracle_text) or _oracle_text_temporary_source_id(oracle_text)


def _ordered_unique_strings(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _oracle_artifact_chain_sources(item: Dict[str, Any], claim: Dict[str, Any]) -> List[str]:
    if str(claim.get("selected_inference_rule") or "") != (
        "deterministic_sparse_same_section_drops_leave_oracle_stale"
    ):
        return []
    support = claim.get("support") or {}
    if not support:
        selected_kind = str(claim.get("selected_kind") or "")
        selected_tier = str(claim.get("selected_tier") or "")
        selected_rule = str(claim.get("selected_inference_rule") or "")
        for candidate in list(claim.get("candidates") or []):
            if (
                str(candidate.get("kind") or "") == selected_kind
                and str(candidate.get("tier") or "") == selected_tier
                and str(candidate.get("inference_rule") or "") == selected_rule
            ):
                support = candidate.get("support") or {}
                break
    return _ordered_unique_strings(
        [
            str(support.get("first_drop_source") or ""),
            *[str(source) for source in list(support.get("drop_sources") or [])],
            str(item.get("blame_source") or ""),
        ]
    )


def _oracle_artifact_profile_for_section(
    item: Dict[str, Any],
    claim: Dict[str, Any],
    *,
    bisect_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    selected_kind = str(claim.get("selected_kind") or "")
    drop_sources = [str(v) for v in (item.get("drop_sources") or []) if str(v)]
    chain_sources = _ordered_unique_strings(item.get("chain_sources") or [])
    if not drop_sources and len(chain_sources) > 1:
        drop_sources = list(chain_sources)
    if not drop_sources and bisect_row:
        first_drop = str(bisect_row.get("first_drop_source") or "")
        blame_source = _recover_oracle_artifact_blame_source(item, bisect_row=bisect_row)
        worst_drops = list(bisect_row.get("worst_drops") or [])
        if len(worst_drops) > 1:
            drop_sources = [str(d.get("source_id") or "") for d in worst_drops if str(d.get("source_id") or "")]
        elif first_drop:
            drop_sources = (
                [first_drop] if not blame_source or first_drop == blame_source else [first_drop, blame_source]
            )
    complexity = "mixed"
    family = selected_kind or str(item.get("diagnosis") or "")
    verification_gaps: List[str] = []

    if selected_kind == "oracle_section_stale":
        family = "oracle_section_stale"
        complexity = "single_step_same_section_stale" if len(drop_sources) <= 1 else "multi_step_same_section_stale"
        if len(drop_sources) > 1 and len(chain_sources) <= 1:
            verification_gaps.append("later_touch_chain_not_simplified")
    elif selected_kind in (
        "cross_chapter_oracle_section_drift",
        "UNRESOLVED.address_projection.cross_chapter_oracle_drift",
    ):
        family = "cross_chapter_oracle_section_drift"
        complexity = "structural_cross_chapter_drift"
    elif selected_kind == "same_chapter_oracle_range_drift":
        family = "same_chapter_oracle_range_drift"
        complexity = "structural_same_chapter_range_drift"
    elif selected_kind == "blamed_source_payload_prefers_replay":
        family = "blamed_source_payload_prefers_replay"
        complexity = "source_fragment_preference"
        verification_gaps.append("source_fragment_artifact_not_yet_rendered")
    else:
        verification_gaps.append("family_not_in_clean_v1_catalog")

    blame_required = family in {
        "oracle_section_stale",
        "blamed_source_payload_prefers_replay",
    }
    blame_source = _recover_oracle_artifact_blame_source(item, bisect_row=bisect_row)
    if blame_required:
        if not blame_source:
            verification_gaps.append("missing_blame_attribution")
        else:
            if not str(item.get("blame_source_url") or ""):
                verification_gaps.append("missing_blame_source_url")
            if not str(item.get("blame_source_johtolause") or ""):
                verification_gaps.append("missing_blame_source_johtolause")

    return {
        "family": family,
        "complexity": complexity,
        "verification_gaps": verification_gaps,
        "ready_for_clean_v1": not verification_gaps,
        "drop_source_count": len(drop_sources),
    }


def _oracle_artifact_summary(
    *,
    oracle_claims: List[Dict[str, Any]],
    section_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    by_family = Counter()
    by_complexity = Counter()
    verification_gaps = Counter()
    ready_section_count = 0
    for item in section_results:
        profile = item.get("artifact_profile") or {}
        family = str(profile.get("family") or "")
        complexity = str(profile.get("complexity") or "")
        if family:
            by_family[family] += 1
        if complexity:
            by_complexity[complexity] += 1
        if bool(profile.get("ready_for_clean_v1")):
            ready_section_count += 1
        for gap in profile.get("verification_gaps") or []:
            verification_gaps[str(gap)] += 1
    section_families = {
        str((item.get("artifact_profile") or {}).get("family") or "")
        for item in section_results
        if str((item.get("artifact_profile") or {}).get("family") or "")
    }
    statute_only_kinds = [
        str(claim.get("kind") or "")
        for claim in oracle_claims
        if not str(claim.get("section") or "") and str(claim.get("kind") or "") not in section_families
    ]
    statute_artifact_count = 0
    ready_statute_count = 0
    for kind in statute_only_kinds:
        if not kind:
            continue
        if kind in (
            "oracle_section_stale",
            "section_claims_unanimously_oracle_incorrect",
        ):
            continue
        statute_artifact_count += 1
        by_family[kind] += 1
        if kind in ("oracle_cutoff_version_drift", "oracle_metadata_inconsistency"):
            by_complexity["statute_cutoff_version_drift"] += 1
            ready_statute_count += 1
        elif kind == "xml_html_topology_drift":
            by_complexity["structural_html_xml_topology_drift"] += 1
            ready_statute_count += 1
        elif kind == "same_chapter_oracle_range_drift":
            by_complexity["structural_same_chapter_range_drift"] += 1
            ready_statute_count += 1
        else:
            by_complexity["statute_mixed"] += 1
            verification_gaps["family_not_in_clean_v1_catalog"] += 1
    return {
        "section_artifact_count": len(section_results),
        "ready_section_artifact_count": ready_section_count,
        "statute_artifact_count": statute_artifact_count,
        "ready_statute_artifact_count": ready_statute_count,
        "total_artifact_count": len(section_results) + statute_artifact_count,
        "ready_total_artifact_count": ready_section_count + ready_statute_count,
        "by_family": dict(by_family),
        "by_complexity": dict(by_complexity),
        "verification_gaps": dict(verification_gaps),
        "statute_only_proof_kinds": statute_only_kinds,
    }


def _load_amendment_source_context(amendment_id: str) -> Dict[str, str]:
    from lawvm.finland.corpus import get_corpus

    result = {
        "amendment_id": str(amendment_id or ""),
        "amendment_url": _finlex_original_url(amendment_id),
        "source_title": "",
        "johtolause": "",
    }
    if not str(amendment_id or "").strip():
        return result
    try:
        xml_bytes = get_corpus().read_source(str(amendment_id))
    except (KeyError, OSError):
        return result
    if not xml_bytes:
        return result
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return result
    title_el = root.find(".//{*}docTitle")
    if title_el is not None:
        result["source_title"] = " ".join(str(title_el.text or "").split())
    preamble = root.find(".//{*}preamble")
    if preamble is not None:
        result["johtolause"] = " ".join(etree.tostring(preamble, method="text", encoding="unicode").split())
    return result


def _merge_review_summary(acc: Dict, chunk: Dict) -> None:
    acc["bundle_count"] += int(chunk.get("bundle_count") or 0)
    acc["error_count"] += int(chunk.get("error_count") or 0)
    acc["processable_count"] += int(chunk.get("processable_count") or 0)
    acc["classified_count"] += int(chunk.get("classified_count") or 0)
    acc["strict_clean_count"] += int(chunk.get("strict_clean_count") or 0)
    acc["actionable_unresolved_count"] += int(chunk.get("actionable_unresolved_count") or 0)
    acc["mixed_replay_risk_count"] += int(chunk.get("mixed_replay_risk_count") or 0)
    acc["ready_oracle_artifact_count"] += int(chunk.get("ready_oracle_artifact_count") or 0)
    acc["evidence_context_degraded_count"] += int(chunk.get("evidence_context_degraded_count") or 0)
    acc["chain_complete_count"] += int(chunk.get("chain_complete_count") or 0)
    for field in _REVIEW_COUNT_FIELDS:
        dest = Counter(acc.get(field) or {})
        dest.update(chunk.get(field) or {})
        acc[field] = dict(dest)
    acc["error_rows"].extend(list(chunk.get("error_rows") or []))
    acc["rows"].extend(list(chunk.get("rows") or []))
    acc["selected_count"] += int(chunk.get("selected_count") or 0)


def _sorted_review_rows(rows: Iterable[Dict]) -> List[Dict]:
    items = [dict(item or {}) for item in rows]
    items.sort(
        key=lambda item: (
            _PRIMARY_TIER_ORDER.index(item["primary_proof_tier"])
            if item.get("primary_proof_tier") in _PRIMARY_TIER_ORDER
            else len(_PRIMARY_TIER_ORDER),
            str(item.get("statute_id") or ""),
        )
    )
    return items


def review_live_oracle_corpus(
    *,
    jurisdiction: str = "fi",
    mode: str = "legal_pit",
    include_bisect: bool = False,
    workers: int = 1,
    corpus_store_mode: str = "",
    cache_only: bool = False,
    bundle_cache_dir: str = "",
    primary_tier: str = "",
    tier: str = "",
    kind: str = "",
    section_kind: str = "",
    section_rule: str = "",
    trigger_source: str = "",
    trigger_field: str = "",
    strict_fail_reason: str = "",
    elaboration_observation_kind: str = "",
    sparse_leftovers_only: bool = False,
    sparse_blocker_source: str = "",
    sparse_blocker_section: str = "",
    payload_completeness_kind: str = "",
    payload_tail_policy: str = "",
    provenance_projection_kind: str = "",
    provenance_tag: str = "",
    provenance_source_statute: str = "",
    source_proof_kind: str = "",
    source_pathology_code: str = "",
    source_pathology_source: str = "",
    source_pathology_target_label: str = "",
    source_pathology_diagnostic_reason: str = "",
    alternative_replay_section: str = "",
    html_noncommensurable_reason: str = "",
    evidence_context_degraded_only: bool = False,
    evidence_context_rail: str = "",
    actionable_unresolved_only: bool = False,
    nontrivial_unresolved_only: bool = False,
    mixed_replay_risk_only: bool = False,
    ready_oracle_artifacts_only: bool = False,
    oracle_artifact_family: str = "",
    oracle_artifact_gap: str = "",
    limit: int = 20,
    chunk_size: int = 200,
    min_year: int = 0,
    max_year: int = 0,
    start_at: int = 0,
    max_statutes: int = 0,
    progress_path: str = "",
    output_path: str = "",
    resume: bool = False,
    allow_metadata_backfill: Optional[bool] = None,
    allow_oracle_alignment: Optional[bool] = None,
    applicability_mode: Optional[str] = None,
    authority_mode: Optional[str] = None,
) -> Dict:
    jurisdiction = _assert_live_evidence_review_supported(jurisdiction)
    oracle_only = bool(ready_oracle_artifacts_only)
    effective_bundle_cache_dir = _effective_bundle_cache_dir(
        bundle_cache_dir,
        oracle_corpus=True,
    )
    all_statute_ids = _oracle_corpus_statute_ids(
        jurisdiction=jurisdiction,
        corpus_store_mode=corpus_store_mode,
        cache_only=cache_only,
    )
    lower_year = int(min_year or 0)
    upper_year = int(max_year or 0)
    if lower_year > 0:
        all_statute_ids = [
            statute_id
            for statute_id in all_statute_ids
            if _statute_year(statute_id, jurisdiction=jurisdiction) >= lower_year
        ]
    if upper_year > 0:
        all_statute_ids = [
            statute_id
            for statute_id in all_statute_ids
            if _statute_year(statute_id, jurisdiction=jurisdiction) <= upper_year
        ]
    start_index = max(0, int(start_at or 0))
    statute_ids = all_statute_ids[start_index:]
    if int(max_statutes or 0) > 0:
        statute_ids = statute_ids[: int(max_statutes or 0)]
    if str(jurisdiction or "fi").lower() == "fi":
        # Sort longest-chain-first so ProcessPoolExecutor doesn't stall on
        # a few large statutes at the end while workers sit idle.
        statute_ids = _sort_by_chain_length_desc(statute_ids)
    worker_count = max(1, int(workers or 1))
    chunk_len = max(1, int(chunk_size or 1))
    progress_target = Path(progress_path) if progress_path else None
    output_target = Path(output_path) if output_path else None

    acc: Dict[str, Any] = {
        "bundle_count": 0,
        "error_count": 0,
        "processable_count": 0,
        "classified_count": 0,
        "strict_clean_count": 0,
        "selected_count": 0,
        "filters": {
            "primary_tier": primary_tier,
            "tier": tier,
            "kind": kind,
            "section_kind": section_kind,
            "section_rule": section_rule,
            "trigger_source": trigger_source,
            "trigger_field": trigger_field,
            "strict_fail_reason": strict_fail_reason,
            "elaboration_observation_kind": elaboration_observation_kind,
            "sparse_leftovers_only": sparse_leftovers_only,
            "sparse_blocker_source": sparse_blocker_source,
            "sparse_blocker_section": sparse_blocker_section,
            "payload_completeness_kind": payload_completeness_kind,
            "payload_tail_policy": payload_tail_policy,
            "provenance_projection_kind": provenance_projection_kind,
            "provenance_tag": provenance_tag,
            "provenance_source_statute": provenance_source_statute,
            "source_proof_kind": source_proof_kind,
            "source_pathology_code": source_pathology_code,
            "source_pathology_source": source_pathology_source,
            "source_pathology_target_label": source_pathology_target_label,
            "source_pathology_diagnostic_reason": source_pathology_diagnostic_reason,
            "alternative_replay_section": alternative_replay_section,
            "html_noncommensurable_reason": html_noncommensurable_reason,
            "evidence_context_degraded_only": evidence_context_degraded_only,
            "evidence_context_rail": evidence_context_rail,
            "actionable_unresolved_only": actionable_unresolved_only,
            "nontrivial_unresolved_only": nontrivial_unresolved_only,
            "mixed_replay_risk_only": mixed_replay_risk_only,
            "ready_oracle_artifacts_only": ready_oracle_artifacts_only,
            "oracle_artifact_family": oracle_artifact_family,
            "oracle_artifact_gap": oracle_artifact_gap,
            "limit": limit,
        },
        "by_primary_tier": {},
        "by_claim_kind": {},
        "by_section_claim_kind": {},
        "by_section_claim_inference_rule": {},
        "by_defeated_section_claim_kind": {},
        "by_defeated_section_claim_inference_rule": {},
        "by_strict_fail_reason": {},
        "by_elaboration_observation_kind": {},
        "by_payload_completeness_kind": {},
        "by_payload_tail_policy": {},
        "by_provenance_projection_kind": {},
        "by_provenance_tag": {},
        "by_provenance_source_statute": {},
        "by_source_proof_kind": {},
        "by_source_pathology_code": {},
        "by_source_pathology_source": {},
        "by_source_pathology_target_label": {},
        "by_source_pathology_diagnostic_reason": {},
        "by_alternative_replay_section": {},
        "by_html_noncommensurable_reason": {},
        "by_evidence_context_degradation_rail": {},
        "by_evidence_context_degradation_exception": {},
        "by_oracle_artifact_family": {},
        "by_oracle_artifact_complexity": {},
        "by_oracle_artifact_gap": {},
        "by_unresolved_exclusion_reason": {},
        "by_mixed_replay_risk_reason": {},
        "by_trigger": {},
        "by_error": {},
        "actionable_unresolved_count": 0,
        "mixed_replay_risk_count": 0,
        "ready_oracle_artifact_count": 0,
        "evidence_context_degraded_count": 0,
        "chain_complete_count": 0,
        "error_rows": [],
        "rows": [],
        "jurisdiction": str(jurisdiction or "fi"),
        "mode": mode,
        "with_bisect": include_bisect,
        "workers": worker_count,
        "chunk_size": chunk_len,
        "min_year": lower_year,
        "max_year": upper_year,
        "start_at": start_index,
        "max_statutes": int(max_statutes or 0),
        "cache_only": bool(cache_only),
        "bundle_cache_dir": effective_bundle_cache_dir,
        "bundle_cache_hits": 0,
        "bundle_cache_misses": 0,
        "bundle_cache_errors": 0,
        "corpus_store_mode": str(corpus_store_mode or ("auto" if cache_only else "")),
        "corpus_scope": "oracle",
        "statute_count": len(statute_ids),
        "processed": 0,
        "uk_metadata_backfill_enabled": allow_metadata_backfill,
        "uk_oracle_alignment_enabled": allow_oracle_alignment,
        "uk_applicability_mode": applicability_mode,
        "uk_authority_mode": authority_mode,
    }
    resume_index = 0
    if resume and output_target and output_target.exists():
        prior = json.loads(output_target.read_text(encoding="utf-8"))
        if (
            int(prior.get("statute_count") or 0) == len(statute_ids)
            and dict(prior.get("filters") or {}) == acc["filters"]
            and int(prior.get("min_year") or 0) == lower_year
            and int(prior.get("max_year") or 0) == upper_year
            and int(prior.get("start_at") or 0) == start_index
            and int(prior.get("max_statutes") or 0) == int(max_statutes or 0)
        ):
            for key in acc:
                if key in prior:
                    acc[key] = prior[key]
            resume_index = int(prior.get("processed") or 0)

    if progress_target and not resume:
        progress_target.parent.mkdir(parents=True, exist_ok=True)
        progress_target.write_text("", encoding="utf-8")
    if progress_target:
        with progress_target.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "event": "start",
                        "processed": resume_index,
                        "total": len(statute_ids),
                        "chunk_size": chunk_len,
                        "selected_count": acc["selected_count"],
                        "error_count": acc["error_count"],
                        "ready_oracle_artifact_count": acc["ready_oracle_artifact_count"],
                        "bundle_cache_hits": acc["bundle_cache_hits"],
                        "bundle_cache_misses": acc["bundle_cache_misses"],
                        "bundle_cache_errors": acc["bundle_cache_errors"],
                        "filters": acc["filters"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    # Fluid single-executor model: submit ALL cache-miss statutes at once so
    # workers stay busy across the full corpus rather than stalling per chunk.
    _review_kwargs: Dict[str, Any] = dict(
        primary_tier=primary_tier,
        tier=tier,
        kind=kind,
        section_kind=section_kind,
        section_rule=section_rule,
        trigger_source=trigger_source,
        trigger_field=trigger_field,
        strict_fail_reason=strict_fail_reason,
        elaboration_observation_kind=elaboration_observation_kind,
        sparse_leftovers_only=sparse_leftovers_only,
        sparse_blocker_source=sparse_blocker_source,
        sparse_blocker_section=sparse_blocker_section,
        payload_completeness_kind=payload_completeness_kind,
        payload_tail_policy=payload_tail_policy,
        provenance_projection_kind=provenance_projection_kind,
        provenance_tag=provenance_tag,
        provenance_source_statute=provenance_source_statute,
        source_proof_kind=source_proof_kind,
        source_pathology_code=source_pathology_code,
        source_pathology_source=source_pathology_source,
        source_pathology_target_label=source_pathology_target_label,
        source_pathology_diagnostic_reason=source_pathology_diagnostic_reason,
        alternative_replay_section=alternative_replay_section,
        html_noncommensurable_reason=html_noncommensurable_reason,
        evidence_context_degraded_only=evidence_context_degraded_only,
        evidence_context_rail=evidence_context_rail,
        actionable_unresolved_only=actionable_unresolved_only,
        nontrivial_unresolved_only=nontrivial_unresolved_only,
        mixed_replay_risk_only=mixed_replay_risk_only,
        ready_oracle_artifacts_only=ready_oracle_artifacts_only,
        oracle_artifact_family=oracle_artifact_family,
        oracle_artifact_gap=oracle_artifact_gap,
        limit=max(limit, 100000),
    )

    def _process_one_bundle(bundle: Dict) -> None:
        """Merge a single completed bundle into acc and write progress/checkpoints."""
        single_review = _review_bundles([bundle], **_review_kwargs)
        _merge_review_summary(acc, single_review)
        acc["processed"] += 1
        processed = acc["processed"]

        if progress_target and processed % 50 == 0:
            progress_target.parent.mkdir(parents=True, exist_ok=True)
            with progress_target.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "processed": processed,
                            "total": len(statute_ids),
                            "selected_count": acc["selected_count"],
                            "error_count": acc["error_count"],
                            "ready_oracle_artifact_count": acc["ready_oracle_artifact_count"],
                            "bundle_cache_hits": acc["bundle_cache_hits"],
                            "bundle_cache_misses": acc["bundle_cache_misses"],
                            "bundle_cache_errors": acc["bundle_cache_errors"],
                            "by_primary_tier": acc["by_primary_tier"],
                            "by_oracle_artifact_family": acc["by_oracle_artifact_family"],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        if output_target and processed % 100 == 0:
            output_target.parent.mkdir(parents=True, exist_ok=True)
            output_target.write_text(json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")

    # Serial cache-check pass; collect miss items for parallel execution.
    pending_ids: List[str] = []
    for statute_id in statute_ids[resume_index:]:
        hit, cached_bundle, cache_error = _load_cached_review_bundle(
            statute_id,
            jurisdiction=jurisdiction,
            mode=mode,
            include_bisect=include_bisect,
            corpus_store_mode=corpus_store_mode,
            cache_only=cache_only,
            bundle_cache_dir=effective_bundle_cache_dir,
            oracle_only=oracle_only,
            allow_metadata_backfill=allow_metadata_backfill,
            allow_oracle_alignment=allow_oracle_alignment,
            applicability_mode=applicability_mode,
            authority_mode=authority_mode,
        )
        if cache_error:
            acc["bundle_cache_errors"] += 1
        if hit and cached_bundle is not None:
            acc["bundle_cache_hits"] += 1
            _process_one_bundle(cached_bundle)
        else:
            acc["bundle_cache_misses"] += 1
            pending_ids.append(statute_id)

    # Fluid parallel pass over all cache misses.
    if pending_ids:
        if worker_count <= 1 or len(pending_ids) <= 1:
            for statute_id in pending_ids:
                bundle = _build_live_review_bundle_one(
                    statute_id,
                    jurisdiction=jurisdiction,
                    mode=mode,
                    include_bisect=include_bisect,
                    corpus_store_mode=corpus_store_mode,
                    cache_only=cache_only,
                    bundle_cache_dir=effective_bundle_cache_dir,
                    oracle_only=oracle_only,
                    allow_metadata_backfill=allow_metadata_backfill,
                    allow_oracle_alignment=allow_oracle_alignment,
                    applicability_mode=applicability_mode,
                    authority_mode=authority_mode,
                )
                _process_one_bundle(bundle)
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(
                        _build_live_review_bundle_one,
                        statute_id,
                        jurisdiction=jurisdiction,
                        mode=mode,
                        include_bisect=include_bisect,
                        corpus_store_mode=corpus_store_mode,
                        cache_only=cache_only,
                        bundle_cache_dir=effective_bundle_cache_dir,
                        oracle_only=oracle_only,
                        allow_metadata_backfill=allow_metadata_backfill,
                        allow_oracle_alignment=allow_oracle_alignment,
                        applicability_mode=applicability_mode,
                        authority_mode=authority_mode,
                    ): statute_id
                    for statute_id in pending_ids
                }
                for future in concurrent.futures.as_completed(future_map):
                    _process_one_bundle(future.result())

    acc["error_rows"] = sorted(
        [
            {
                "statute_id": str(item.get("statute_id") or ""),
                "error": str(item.get("error") or ""),
            }
            for item in acc.get("error_rows", [])
            if str(item.get("statute_id") or "") and str(item.get("error") or "")
        ],
        key=lambda item: (item["statute_id"], item["error"]),
    )
    acc["rows"] = _sorted_review_rows(acc.get("rows", []))
    if limit >= 0:
        acc["rows"] = acc["rows"][: max(limit, 0)]
    if output_target:
        output_target.parent.mkdir(parents=True, exist_ok=True)
        output_target.write_text(json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")
    return acc


def review_bundle_artifacts(
    paths: Iterable[str],
    *,
    primary_tier: str = "",
    tier: str = "",
    kind: str = "",
    section_kind: str = "",
    section_rule: str = "",
    trigger_source: str = "",
    trigger_field: str = "",
    strict_fail_reason: str = "",
    elaboration_observation_kind: str = "",
    sparse_leftovers_only: bool = False,
    sparse_blocker_source: str = "",
    sparse_blocker_section: str = "",
    payload_completeness_kind: str = "",
    payload_tail_policy: str = "",
    provenance_projection_kind: str = "",
    provenance_tag: str = "",
    provenance_source_statute: str = "",
    source_proof_kind: str = "",
    source_pathology_code: str = "",
    source_pathology_source: str = "",
    source_pathology_target_label: str = "",
    source_pathology_diagnostic_reason: str = "",
    alternative_replay_section: str = "",
    html_noncommensurable_reason: str = "",
    evidence_context_degraded_only: bool = False,
    evidence_context_rail: str = "",
    actionable_unresolved_only: bool = False,
    nontrivial_unresolved_only: bool = False,
    mixed_replay_risk_only: bool = False,
    ready_oracle_artifacts_only: bool = False,
    oracle_artifact_family: str = "",
    oracle_artifact_gap: str = "",
    limit: int = 20,
) -> Dict:
    path_list = [str(path) for path in paths if str(path)]
    review = _review_bundles(
        _load_bundle_artifacts(path_list),
        primary_tier=primary_tier,
        tier=tier,
        kind=kind,
        section_kind=section_kind,
        section_rule=section_rule,
        trigger_source=trigger_source,
        trigger_field=trigger_field,
        strict_fail_reason=strict_fail_reason,
        elaboration_observation_kind=elaboration_observation_kind,
        sparse_leftovers_only=sparse_leftovers_only,
        sparse_blocker_source=sparse_blocker_source,
        sparse_blocker_section=sparse_blocker_section,
        payload_completeness_kind=payload_completeness_kind,
        payload_tail_policy=payload_tail_policy,
        provenance_projection_kind=provenance_projection_kind,
        provenance_tag=provenance_tag,
        provenance_source_statute=provenance_source_statute,
        source_proof_kind=source_proof_kind,
        source_pathology_code=source_pathology_code,
        source_pathology_source=source_pathology_source,
        source_pathology_target_label=source_pathology_target_label,
        source_pathology_diagnostic_reason=source_pathology_diagnostic_reason,
        alternative_replay_section=alternative_replay_section,
        html_noncommensurable_reason=html_noncommensurable_reason,
        evidence_context_degraded_only=evidence_context_degraded_only,
        evidence_context_rail=evidence_context_rail,
        actionable_unresolved_only=actionable_unresolved_only,
        nontrivial_unresolved_only=nontrivial_unresolved_only,
        mixed_replay_risk_only=mixed_replay_risk_only,
        ready_oracle_artifacts_only=ready_oracle_artifacts_only,
        oracle_artifact_family=oracle_artifact_family,
        oracle_artifact_gap=oracle_artifact_gap,
        limit=limit,
    )
    review["artifact_count"] = len(path_list)
    return review


def _load_manual_override_counts(path: Optional[Path] = None) -> Dict[str, int]:
    target = Path(path) if path is not None else _MANUAL_DATASET
    if not target.exists():
        return {}
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or []
    counts: Dict[str, int] = defaultdict(int)
    if not isinstance(data, list):
        return {}
    for item in data:
        if not isinstance(item, dict):
            continue
        amendment_id = str(item.get("amendment_id") or "").strip()
        if amendment_id:
            counts[amendment_id] += 1
    return dict(counts)


def _corrigendum_support_for_amendments(
    amendment_ids: Iterable[str],
    *,
    patch_records: Optional[List[Dict]] = None,
    source_records: Optional[List[Dict]] = None,
    manual_override_counts: Optional[Dict[str, int]] = None,
) -> List[Dict]:
    wanted = {str(amendment_id) for amendment_id in amendment_ids if str(amendment_id)}
    if not wanted:
        return []
    patch_records = patch_records if patch_records is not None else load_patch_records()
    source_records = source_records if source_records is not None else load_source_records()
    manual_override_counts = (
        manual_override_counts if manual_override_counts is not None else _load_manual_override_counts()
    )

    per_amendment_id: Dict[str, Dict] = {}
    for amendment_id in wanted:
        per_amendment_id[amendment_id] = {
            "amendment_id": amendment_id,
            "official_item_count": 0,
            "verified_in_source_count": 0,
            "unverified_item_count": 0,
            "source_pdf_count": 0,
            "source_pdfs": [],
            "manual_override_count": int(manual_override_counts.get(amendment_id, 0) or 0),
        }

    for record in patch_records:
        amendment_id = str(record.get("amendment_id") or "").strip()
        if amendment_id not in per_amendment_id:
            continue
        entry = per_amendment_id[amendment_id]
        entry["official_item_count"] += 1
        if bool(record.get("verified_in_source")):
            entry["verified_in_source_count"] += 1
        else:
            entry["unverified_item_count"] += 1

    for record in source_records:
        amendment_id = str(record.get("amendment_id") or "").strip()
        if amendment_id not in per_amendment_id:
            continue
        pdf_name = str(record.get("pdf_name") or record.get("source_pdf") or "").strip()
        if pdf_name and pdf_name not in per_amendment_id[amendment_id]["source_pdfs"]:
            per_amendment_id[amendment_id]["source_pdfs"].append(pdf_name)

    for entry in per_amendment_id.values():
        entry["source_pdfs"].sort()
        entry["source_pdf_count"] = len(entry["source_pdfs"])

    return sorted(
        per_amendment_id.values(),
        key=lambda item: (
            -item["official_item_count"],
            -item["manual_override_count"],
            item["amendment_id"],
        ),
    )


def build_evidence_bundle(
    statute_id: str,
    *,
    mode: str = "legal_pit",
    include_bisect: bool = False,
    include_version_drift: bool = False,
) -> Dict:
    # Build shared context once — all sub-tools use this instead of
    # independently calling replay_xml / get_ground_truth_tree / _audit_html_one.
    from lawvm.tools.evidence_context import EvidenceContext

    ctx = EvidenceContext(
        statute_id=statute_id,
        mode=mode,
        html_audit=_run_quietly(_audit_html_one, statute_id),
        oracle_root=get_ground_truth_tree(statute_id),
    )
    oracle_suspect_detail, oracle_suspect_pending = get_consolidated_oracle_suspect_cache_only(statute_id)

    replay_compiled_ops: List[Dict[str, object]] = []
    replay_meta: Dict[str, object] = {}
    replay_canonical_ops: List[Any] = []
    replay_failed_ops: List[Any] = []
    replay_result = _run_quietly(
        replay_xml,
        statute_id,
        mode=mode,
        compiled_ops_out=replay_compiled_ops,
        replay_meta_out=replay_meta,
        lo_ops_out=replay_canonical_ops,
        failed_ops_out=replay_failed_ops,
    )

    oracle_result = _run_quietly(
        _classify_statute,
        statute_id,
        mode=mode,
        replay_result=replay_result,
        precomputed_compiled_ops=replay_compiled_ops,
        oracle_root=ctx.oracle_root,
        html_audit_result=ctx.html_audit,
    )
    if oracle_result is None:
        return {"statute_id": statute_id, "mode": mode, "error": "classification returned no result"}
    if oracle_result.error:
        return {"statute_id": statute_id, "mode": mode, "error": oracle_result.error}

    ctx.replay_result = oracle_result.replay_result
    ctx.compiled_ops = oracle_result.compiled_ops
    _typed_source_adjudication = _effective_source_adjudication(
        statute_id=statute_id,
        replay_mode=mode,
        replay_result=ctx.replay_result,
        replay_meta=replay_meta,
    )
    _typed_oracle_version = (
        str(_typed_source_adjudication.oracle_version_amendment_id or "")
        if _typed_source_adjudication is not None
        else ""
    )
    _typed_oracle_suspect = (
        str(_typed_source_adjudication.oracle_suspect or "")
        if _typed_source_adjudication is not None
        else ""
    )
    ctx.oracle_version_amendment_id = _typed_oracle_version or oracle_result.oracle_version_amendment_id

    compile_facade = _run_quietly(
        compile_fi_facade_from_replay,
        parent_id=statute_id,
        replay_result=ctx.replay_result,
        replay_mode=mode,
        compile_mode="strict",
        strict_profile=None,
        compiled_ops=replay_compiled_ops,
        replay_meta=replay_meta,
        canonical_ops=replay_canonical_ops,
        failed_ops=replay_failed_ops,
        extra_findings=[],
    )
    if compile_facade is None:
        return {"statute_id": statute_id, "mode": mode, "error": "compile facade returned no result"}

    _lineage_rows = []
    if _typed_source_adjudication is not None:
        _lineage_rows = list(_typed_source_adjudication.lineage or [])
    source_completeness = {
        "chain_length": len(_lineage_rows),
        "source_available": sum(1 for row in _lineage_rows if isinstance(row, dict) and row.get("included")),
        "dates_available": sum(1 for row in _lineage_rows if isinstance(row, dict) and row.get("effective_date")),
    }
    report_record = report_record_from_facade(
        statute_id=statute_id,
        facade=compile_facade,
        compiled_ops=replay_compiled_ops,
        failed_ops=replay_failed_ops,
        source_adjudication=_typed_source_adjudication,
    )
    projection_rows = _compiler_projection_rows(report_record.get("projection_rows"))
    _strict_reasons = list(report_record.get("strict_fail_reasons") or [])

    _evidence_context_diagnostics: List[Dict[str, str]] = []

    section_results = []
    blame_amendment_ids: List[str] = []
    for item in oracle_result.section_results:
        section_item = dict(item)
        section_item["similarity"] = round(
            _section_similarity(
                str(section_item.get("replay_text") or ""),
                str(section_item.get("oracle_text") or ""),
            ),
            6,
        )
        blame_source = str(section_item.get("blame_source") or "")
        if blame_source:
            blame_amendment_ids.append(blame_source)
        section_results.append(section_item)

    blame_amendment_ids = sorted(set(blame_amendment_ids))
    corrigendum_support = _corrigendum_support_for_amendments(blame_amendment_ids)
    html_topology = {
        "mismatch": bool(
            not ctx.html_audit.noncommensurable_reason
            and (ctx.html_audit.missing_from_xml or ctx.html_audit.extra_in_xml)
        ),
        "missing_from_xml": list(ctx.html_audit.missing_from_xml),
        "extra_in_xml": list(ctx.html_audit.extra_in_xml),
        "html_error": ctx.html_audit.html_error,
        "noncommensurable_reason": ctx.html_audit.noncommensurable_reason,
        "html_url": _finlex_html_url(statute_id),
    }

    source_pathologies = list(oracle_result.source_pathologies or [])
    contingent_effective_sources = [str(v) for v in oracle_result.contingent_effective_sources if str(v)]
    should_include_bisect = include_bisect or any(
        str(item.get("diagnosis") or "") in _REPLAY_BUG_DIAGNOSES for item in section_results
    )
    section_bisect = (
        _run_quietly(
            _section_bisect_support,
            statute_id,
            mode,
            section_results,
            oracle_root=ctx.oracle_root,
        )
        if should_include_bisect
        else []
    )
    alternative_replay_matches: Dict[str, Dict[str, object]] = {}
    oracle_range_matches: Dict[str, Dict[str, object]] = {}
    cross_chapter_oracle_matches: Dict[str, Dict[str, object]] = {}
    cross_chapter_replay_matches: Dict[str, Dict[str, object]] = {}
    if section_results:
        from lawvm.tools._section_debug import render_node_text
        from lawvm.tools.section_keys import extract_ir_sections

        # Reuse replay result from _classify_statute instead of replaying again
        replay_master = ctx.replay_result
        replay_sections = extract_ir_sections(replay_master.materialized_state.ir) if replay_master is not None else {}
        replay_section_texts = {key: render_node_text(node) for key, node in replay_sections.items()}
        alternative_replay_matches = _same_chapter_alternative_replay_matches(
            section_results,
            replay_section_texts,
        )
        oracle_sections = oracle_result.oracle_sections if oracle_result.oracle_sections is not None else {}
        oracle_range_matches = _same_chapter_oracle_range_matches(
            section_results,
            oracle_sections,
        )
        cross_chapter_oracle_matches = _cross_chapter_same_label_oracle_matches(
            section_results,
            oracle_sections,
        )
        cross_chapter_replay_matches = _cross_chapter_same_label_replay_matches(
            section_results,
            replay_section_texts,
        )
    # Collect timeline addresses for negative-proof classifier rule
    _timeline_addrs: set[str] | None = None
    _rr = ctx.replay_result
    if _rr is not None and hasattr(_rr, "timelines") and _rr.timelines:
        _timeline_addrs = {str(addr) for addr in _rr.timelines}
    # C1: Compute section-local strict verdicts via blame chain
    _section_strict_verdicts = None
    if section_bisect:
        try:
            from lawvm.core.compile_result import (
                compute_section_strict_verdicts as _compute_ssv,
            )

            _section_blame: dict[str, str] = {}
            for _bisect_row in section_bisect or []:
                _bs = str(_bisect_row.get("blame_source") or "")
                _bsec = str(_bisect_row.get("section") or "")
                if _bs and _bsec:
                    _section_blame[_bsec] = _bs
            if _section_blame:
                _profile = default_finland_strict_profile()
                _compile_failures = [
                    CompileFailure.from_scope(
                        source_statute=str(getattr(f, "amendment_id", "") or ""),
                        description=str(getattr(f, "description", "") or ""),
                        reason=str(getattr(f, "reason", "") or ""),
                        target_section=str(getattr(f, "target_section", "") or ""),
                        target_chapter=str(getattr(f, "target_chapter", "") or ""),
                        target_unit_kind=cast(TargetUnitKind, str(getattr(f, "target_unit_kind", "") or "")),
                        reason_code=str(getattr(f, "reason_code", "") or ""),
                    )
                    for f in replay_failed_ops
                ]
                _section_strict_verdicts = _compute_ssv(
                    _profile,
                    compiled_ops=list(replay_compiled_ops),
                    canonical_ops=list(replay_canonical_ops),
                    failed_ops=_compile_failures,
                    findings=list(compile_facade.finding_ledger),
                    section_blame=_section_blame,
                )
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception as exc:
            _evidence_context_diagnostics.append(
                _evidence_context_degradation("section_strict_verdicts", exc)
            )
    # C3: Compute section-local invariant violations from timelines
    _section_inv_violations: dict[str, list[dict]] | None = None
    _rr2 = ctx.replay_result
    if _rr2 is not None and hasattr(_rr2, "timelines") and _rr2.timelines and hasattr(_rr2, "ir") and _rr2.ir:
        try:
            from lawvm.core.timeline_invariants import check_all_timeline_invariants_typed

            _typed_violations = check_all_timeline_invariants_typed(
                _rr2.ir,
                _rr2.timelines,
                str(getattr(ctx, "cutoff_date", "") or ""),
            )
            if _typed_violations:
                _section_inv_violations = {}
                for _tv in _typed_violations:
                    _sl = _tv.section_label
                    if _sl:
                        _section_inv_violations.setdefault(_sl, []).append(
                            {
                                "kind": _tv.kind,
                                "section_label": _sl,
                                "address_path": _tv.address_path,
                                "message": _tv.message,
                            }
                        )
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception as exc:
            _evidence_context_diagnostics.append(
                _evidence_context_degradation("section_timeline_invariants", exc)
            )
    # Chain completeness: compute per-section chain completeness certificate
    # (attack #9 guard — missing compiler input can masquerade as oracle drift).
    _chain_completeness_by_section: dict[str, Any] | None = None
    _chain_completeness_summary: dict[str, Any] | None = None
    if section_results:
        try:
            from lawvm.core.chain_completeness import compute_chain_completeness

            _section_labels_for_cc = [
                str(item.get("section") or "")
                for item in section_results
                if str(item.get("section") or "")
            ]
            _cc_failed_ops_dicts = [
                {
                    "target_section": str(getattr(f, "target_section", "") or ""),
                    "target_unit_kind": str(getattr(f, "target_unit_kind", "") or ""),
                    "target_chapter": str(getattr(f, "target_chapter", "") or ""),
                    "source_statute": str(getattr(f, "amendment_id", "") or ""),
                }
                for f in replay_failed_ops
            ]
            _chain_completeness_by_section = compute_chain_completeness(
                section_labels=_section_labels_for_cc,
                strict_fail_reasons=_strict_reasons,
                failed_ops=_cc_failed_ops_dicts,
                compiled_ops=list(replay_compiled_ops),
            )
            _chain_completeness_summary = {
                "chain_length": int(source_completeness["chain_length"]),
                "source_available": int(source_completeness["source_available"]),
                "source_missing_count": max(
                    0,
                    int(source_completeness["chain_length"]) - int(source_completeness["source_available"]),
                ),
                "is_complete": (
                    int(source_completeness["source_available"]) == int(source_completeness["chain_length"])
                    and int(source_completeness["chain_length"]) > 0
                ),
                "section_complete_count": sum(
                    1 for s in _chain_completeness_by_section.values()
                    if s.is_complete
                ),
                "section_incomplete_count": sum(
                    1 for s in _chain_completeness_by_section.values()
                    if not s.is_complete
                ),
            }
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception as exc:
            _evidence_context_diagnostics.append(
                _evidence_context_degradation("chain_completeness", exc)
            )

    # A1 proof algebra: typed section claim path (primary, since session 9).
    from lawvm.tools.evidence_claims import build_section_claims_typed

    _typed_results = build_section_claims_typed(
        section_results=section_results,
        section_bisect=section_bisect,
        alternative_replay_matches=alternative_replay_matches,
        oracle_range_matches=oracle_range_matches,
        cross_chapter_oracle_matches=cross_chapter_oracle_matches,
        cross_chapter_replay_matches=cross_chapter_replay_matches,
        html_topology=html_topology,
        strict_fail_reasons=_strict_reasons,
        timeline_addresses=_timeline_addrs,
        oracle_suspect_detail=str(oracle_suspect_detail or ""),
        section_strict_verdicts=_section_strict_verdicts,
        section_invariant_violations=_section_inv_violations,
        chain_completeness_by_section=_chain_completeness_by_section,
    )
    section_claims = [r.to_legacy_row() for r in _typed_results]
    # Content-based version drift detection.
    # Only runs when explicitly requested (expensive: re-replays up to 3x).
    # Enable via include_version_drift param or LAWVM_VERSION_DRIFT=1 env var.
    _content_version_drift: Dict[str, Any] | None = None
    _full_score = float(oracle_result.overall_score or 0.0)
    _want_drift = include_version_drift or os.environ.get("LAWVM_VERSION_DRIFT") == "1"
    if _want_drift and _full_score < 0.9999:
        try:
            from lawvm.tools.version_drift import detect_content_version_drift

            _content_version_drift = _run_quietly(
                detect_content_version_drift,
                statute_id,
                _full_score,
            )
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception as exc:
            _evidence_context_diagnostics.append(
                _evidence_context_degradation("content_version_drift", exc)
            )

    # A2 typed statute-level proof algebra (primary path).
    # Produces identical output to _build_proof_claims(); keep legacy as fallback.
    from lawvm.tools.evidence_statute_rules import build_proof_claims_typed
    claims = build_proof_claims_typed(
        section_results=section_results,
        source_pathologies=source_pathologies,
        html_topology=html_topology,
        contingent_effective_sources=contingent_effective_sources,
        corrigendum_support=corrigendum_support,
        oracle_suspect_detail=oracle_suspect_detail,
        oracle_suspect_pending=oracle_suspect_pending,
        section_bisect=section_bisect,
        alternative_replay_matches=alternative_replay_matches,
        oracle_range_matches=oracle_range_matches,
        cross_chapter_oracle_matches=cross_chapter_oracle_matches,
        cross_chapter_replay_matches=cross_chapter_replay_matches,
        section_claims=section_claims,
        typed_section_results=_typed_results,
        content_version_drift=_content_version_drift,
    )
    compiler_observations = _compiler_observation_summary(
        replay_meta=replay_meta,
        projection_rows=projection_rows,
        section_bisect=section_bisect,
    )

    # Span-level anchor counts (L0 infrastructure proof)
    _span_anchor_counts: Dict[str, int] | None = None
    _rr_anchors = ctx.replay_result
    if (
        _rr_anchors is not None
        and hasattr(_rr_anchors, "materialized_state")
        and _rr_anchors.materialized_state is not None
        and hasattr(_rr_anchors.materialized_state, "ir")
        and _rr_anchors.materialized_state.ir is not None
    ):
        try:
            from lawvm.core.span_anchor import extract_all_anchors

            _all_anchors = extract_all_anchors(_rr_anchors.materialized_state.ir)
            _span_anchor_counts = {str(addr): len(sa.anchors) for addr, sa in _all_anchors.items()}
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs -- fail loud
        except Exception as exc:
            _evidence_context_diagnostics.append(
                _evidence_context_degradation("span_anchor_counts", exc)
            )

    # Body pairing analysis: detect foreign/unmatched/repeal-blocked body units
    # across the amendment chain.  Lightweight — reuses amendment XML from corpus.
    _pairing_findings: List[Dict[str, Any]] | None = None
    try:
        from lawvm.finland.body_pairing import audit_statute_body_pairing

        _bp_results = audit_statute_body_pairing(statute_id, mode=mode)
        if _bp_results:
            _pf_list: List[Dict[str, Any]] = []
            for _bpr in _bp_results:
                if _bpr.has_anomalies:
                    _pf_list.append(_bpr.to_dict())
            if _pf_list:
                _pairing_findings = _pf_list
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs -- fail loud
    except Exception as exc:
        _evidence_context_diagnostics.append(
            _evidence_context_degradation("body_pairing", exc)
        )

    return {
        "statute_id": statute_id,
        "title": str(oracle_result.title or ""),
        "mode": mode,
        "proof_contract": _proof_contract(),
        "oracle_version_amendment_id": str(ctx.oracle_version_amendment_id or ""),
        "oracle_suspect_detail": _typed_oracle_suspect or str(oracle_suspect_detail or ""),
        "oracle_suspect_pending": str(oracle_suspect_pending or ""),
        "oracle_present": bool(ctx.oracle_root is not None),
        "overall_score": float(oracle_result.overall_score or 0.0),
        "section_score": float(oracle_result.section_score or 0.0),
        "strict_fail_reasons": _strict_reasons,
        "projection_kinds": sorted(
            {
                str(row.get("kind") or "")
                for row in projection_rows
                if str(row.get("kind") or "")
            }
        ),
        "diagnosis_counts": _diagnosis_counts(section_results),
        "section_results": section_results,
        "section_claims": section_claims,
        "source_pathologies": source_pathologies,
        "html_topology": html_topology,
        "contingent_effective_sources": contingent_effective_sources,
        "supporting_amendments": [
            {
                "amendment_id": amendment_id,
                "blamed_sections": [
                    str(item.get("section") or "")
                    for item in section_results
                    if str(item.get("blame_source") or "") == amendment_id
                ],
                **next(
                    (support for support in corrigendum_support if support["amendment_id"] == amendment_id),
                    {
                        "official_item_count": 0,
                        "verified_in_source_count": 0,
                        "unverified_item_count": 0,
                        "source_pdf_count": 0,
                        "source_pdfs": [],
                        "manual_override_count": 0,
                    },
                ),
            }
            for amendment_id in blame_amendment_ids
        ],
        "compiler_observations": compiler_observations,
        "section_strict_verdicts": (
            {
                label: {
                    "section_label": getattr(v, "section_label", label),
                    "amendment_id": getattr(v, "amendment_id", ""),
                    "status": getattr(v, "status", ""),
                    "barrier_kinds": sorted(getattr(v, "barrier_kinds", set())),
                }
                for label, v in (_section_strict_verdicts or {}).items()
            }
            if _section_strict_verdicts
            else None
        ),
        "section_bisect": section_bisect,
        "proof_claims": claims,
        "proof_tiers": [
            tier for tier in _PRIMARY_TIER_ORDER if tier in {str(item.get("tier") or "") for item in claims}
        ],
        "primary_proof_tier": _primary_proof_tier(claims),
        "span_anchor_counts": _span_anchor_counts,
        "chain_completeness": _chain_completeness_summary,
        "pairing_findings": _pairing_findings,
        "evidence_context_diagnostics": _evidence_context_diagnostics,
        "evidence": {
            "finding_rows": _evidence_context_degradation_rows(
                statute_id=statute_id,
                mode=mode,
                diagnostics=_evidence_context_diagnostics,
            ),
        },
    }


def build_uk_evidence_bundle(
    statute_id: str,
    *,
    mode: str = "legal_pit",
    include_bisect: bool = False,
    allow_metadata_backfill: Optional[bool] = None,
    allow_oracle_alignment: Optional[bool] = None,
    applicability_mode: Optional[str] = None,
    authority_mode: Optional[str] = None,
) -> Dict:
    from farchive import Farchive
    from lawvm.tools.uk_replay import _get_all_eids
    from lawvm.uk_legislation.oracle_align import align_uk_replay_to_oracle
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_bench_comparison,
        classify_uk_replay_residual,
        is_core_uk_comparison,
        normalize_uk_replay_compare_eids,
    )
    from lawvm.uk_legislation.uk_amendment_replay import (
        UKReplayPipeline,
        load_effects_for_statute_from_archive,
    )
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )

    del include_bisect
    if allow_metadata_backfill is None:
        allow_metadata_backfill = True
    if allow_oracle_alignment is None:
        allow_oracle_alignment = True
    applicability_mode = _normalize_uk_applicability_mode(applicability_mode)
    if authority_mode is None:
        authority_mode = "current_mixed"

    if not _DEFAULT_UK_FARCHIVE.exists():
        return {"statute_id": statute_id, "mode": mode, "jurisdiction": "uk", "error": "UK archive not found"}

    with Farchive(_DEFAULT_UK_FARCHIVE) as archive:
        enacted_url = _uk_archive_url_for_statute(statute_id, enacted=True)
        current_url = _uk_archive_url_for_statute(statute_id, enacted=False)
        enacted_bytes = archive.get(enacted_url)
        if not enacted_bytes:
            return {"statute_id": statute_id, "mode": mode, "jurisdiction": "uk", "error": "NO_ENACTED"}
        current_bytes = archive.get(current_url)
        if not current_bytes:
            return {"statute_id": statute_id, "mode": mode, "jurisdiction": "uk", "error": "NO_ORACLE"}

        base_ir = parse_uk_statute_ir_bytes(
            enacted_bytes,
            statute_id=statute_id,
            version_label="enacted",
            source_path=enacted_url,
        )
        oracle_ir = parse_uk_statute_ir_bytes(
            current_bytes,
            statute_id=statute_id,
            version_label="oracle",
            source_path=current_url,
        )
        oracle_data = extract_eid_map_bytes(current_bytes)
        eid_map = dict(oracle_data.get("eid_map", {}) or {})
        text_map = dict(oracle_data.get("text_map", {}) or {})

        base_eids = _get_all_eids([base_ir.body])
        for schedule in base_ir.supplements:
            base_eids.update(_get_all_eids([schedule]))
        oracle_eids = set(eid_map.values())
        n_effects = len(load_effects_for_statute_from_archive(statute_id, archive))

        pipeline = UKReplayPipeline(_REPO_ROOT)
        authority_rejections: list[dict[str, Any]] = []
        compiled_ops = _call_with_supported_kwargs(
            pipeline.compile_ops_for_statute,
            statute_id,
            archive=archive,
            allow_metadata_backfill=allow_metadata_backfill,
            applicability_mode=applicability_mode,
            authority_mode=authority_mode,
            authority_rejections_out=authority_rejections,
        )
        replay_adjudications: List[Any] = []
        replayed_ir = _call_with_supported_kwargs(
            pipeline.apply_ops,
            base_ir,
            compiled_ops,
            eid_map=eid_map if allow_oracle_alignment else None,
            text_map=text_map if allow_oracle_alignment else None,
            adjudications_out=replay_adjudications,
            allow_oracle_alignment=allow_oracle_alignment,
        )
        if allow_oracle_alignment and hasattr(replayed_ir, "to_dict"):
            replayed_ir = align_uk_replay_to_oracle(
                replayed_ir,
                eid_map=eid_map,
                text_map=text_map,
            )

    replayed_eids = _get_all_eids([replayed_ir.body])
    for schedule in replayed_ir.supplements:
        replayed_eids.update(_get_all_eids([schedule]))

    compare_replayed, compare_oracle = normalize_uk_replay_compare_eids(replayed_eids, oracle_eids)
    common = compare_replayed & compare_oracle
    similarity = len(common) / max(len(compare_replayed), len(compare_oracle), 1)
    comparison_class = classify_uk_bench_comparison(
        n_enacted_eids=len(base_eids),
        n_oracle_eids=len(oracle_eids),
        n_effects=n_effects,
        raw_score=similarity,
    )
    only_in_replayed = sorted(compare_replayed - compare_oracle)
    only_in_oracle = sorted(compare_oracle - compare_replayed)
    core_comparison = is_core_uk_comparison(comparison_class)
    authority_counts: Dict[str, int] = {}
    metadata_backfill_op_count = 0
    text_rewrite_witness_op_count = 0
    fragment_substitution_runtime_note_fallback_count = 0
    fragment_substitution_note_only_count = 0
    insertion_anchor_note_only_count = 0
    applicability_effective_date_op_count = 0
    applicability_multi_in_force_date_op_count = 0
    applicability_requires_applied_op_count = 0
    applicability_unapplied_op_count = 0
    for op in compiled_ops:
        witness = _witness_for_op(op)
        if witness is None:
            authority_counts["UNSPECIFIED"] = authority_counts.get("UNSPECIFIED", 0) + 1
            continue
        effect_witness = getattr(witness, "effect_witness", None)
        extraction_witness = getattr(witness, "extraction_witness", None)
        text_rewrite_witness = getattr(witness, "text_rewrite_witness", None)
        applicability_witness = getattr(effect_witness, "applicability", None)
        authority = str(
            getattr(extraction_witness, "authority_layer", None)
            or getattr(effect_witness, "authority_layer", None)
            or "UNSPECIFIED"
        )
        authority_counts[authority] = authority_counts.get(authority, 0) + 1
        if bool(getattr(extraction_witness, "metadata_fallback_used", False)):
            metadata_backfill_op_count += 1
        if text_rewrite_witness is not None:
            text_rewrite_witness_op_count += 1
        op_notes = list(getattr(op, "notes", []) or [])
        if text_rewrite_witness is None and any(str(note or "").startswith("fragment_substitution:") for note in op_notes):
            fragment_substitution_note_only_count += 1
        if getattr(witness, "insertion_anchor_witness", None) is None and any(
            str(note or "").startswith("preceding_eid:") for note in op_notes
        ):
            insertion_anchor_note_only_count += 1
        if applicability_witness is not None:
            if bool(getattr(applicability_witness, "effective_date", None)):
                applicability_effective_date_op_count += 1
            if len(tuple(getattr(applicability_witness, "in_force_dates", ()) or ())) > 1:
                applicability_multi_in_force_date_op_count += 1
            if bool(getattr(applicability_witness, "requires_applied", False)):
                applicability_requires_applied_op_count += 1
            if not bool(getattr(applicability_witness, "applied", True)):
                applicability_unapplied_op_count += 1
    adjudication_kinds = sorted(
        {
            str(getattr(adj, "kind", "") or "")
            for adj in replay_adjudications
            if str(getattr(adj, "kind", "") or "")
        }
    )
    semantic_replay_lane = "metadata_backfilled_replay" if metadata_backfill_op_count else "effects_assisted_replay"
    oracle_alignment_lane = "oracle_alignment_adapter" if allow_oracle_alignment else "none"
    source_purity_lane = (
        "metadata_backfilled_with_oracle_adapter"
        if metadata_backfill_op_count and oracle_alignment_lane != "none"
        else "metadata_backfilled_source_semantics"
        if metadata_backfill_op_count
        else "source_backed_with_oracle_adapter"
        if oracle_alignment_lane != "none"
        else "source_backed_effects_assisted"
    )
    source_semantics_clean = bool(not metadata_backfill_op_count and oracle_alignment_lane == "none")
    source_first_candidate_reasons: list[str] = []
    if metadata_backfill_op_count:
        source_first_candidate_reasons.append("metadata_backfill_ops_present")
    if oracle_alignment_lane != "none":
        source_first_candidate_reasons.append("oracle_alignment_adapter_active")
    if applicability_mode != "effective_date_plus_feed_applied":
        source_first_candidate_reasons.append("applicability_selection_not_feed_applied")
    if authority_mode != "source_text_only":
        source_first_candidate_reasons.append("authority_mode_not_source_text_only")
    source_first_candidate = not source_first_candidate_reasons

    authority_rejection_reason_counts: dict[str, int] = {}
    for rejection in authority_rejections:
        reason_counts = rejection.get("rejected_reason_counts")
        if isinstance(reason_counts, dict) and reason_counts:
            for reason, count in reason_counts.items():
                reason_key = str(reason or "")
                if not reason_key:
                    continue
                authority_rejection_reason_counts[reason_key] = authority_rejection_reason_counts.get(reason_key, 0) + int(count or 0)
        else:
            for reason in rejection.get("rejected_reasons") or []:
                reason_key = str(reason or "")
                if not reason_key:
                    continue
                authority_rejection_reason_counts[reason_key] = authority_rejection_reason_counts.get(reason_key, 0) + 1

    trigger_observations = [
        {"source": "uk_oracle_comparison", "field": "comparison_class", "value": comparison_class},
        {"source": "uk_oracle_comparison", "field": "similarity", "value": round(similarity, 6)},
        {"source": "uk_oracle_comparison", "field": "only_in_replayed_count", "value": len(only_in_replayed)},
        {"source": "uk_oracle_comparison", "field": "only_in_oracle_count", "value": len(only_in_oracle)},
    ]
    proof_claims: List[Dict[str, Any]]
    section_claims: List[Dict[str, Any]] = []
    if not core_comparison:
        proof_claims = [
            {
                "tier": "UNRESOLVED",
                "kind": comparison_class,
                "trigger_observations": trigger_observations,
            }
        ]
    elif only_in_replayed or only_in_oracle or adjudication_kinds:
        residual_tier, residual_kind = classify_uk_replay_residual(
            only_in_replayed=only_in_replayed,
            only_in_oracle=only_in_oracle,
            adjudication_kinds=adjudication_kinds,
        )
        proof_claims = [
            {
                "tier": residual_tier,
                "kind": residual_kind,
                "trigger_observations": trigger_observations,
            }
        ]
        if residual_tier == "PROVED_REPLAY_BUG":
            section_claims = [
                {
                    "section": "statute",
                    "selected_tier": "PROVED_REPLAY_BUG",
                    "selected_kind": "replay_divergence",
                    "selected_inference_rule": residual_kind,
                    "defeated_candidate_kinds": [],
                    "defeated_candidates": [],
                }
            ]
    else:
        proof_claims = [
            {
                "tier": "UNRESOLVED",
                "kind": "no_strong_claim",
                "trigger_observations": trigger_observations,
            }
        ]

    return {
        "statute_id": statute_id,
        "title": str(getattr(oracle_ir, "title", "") or getattr(base_ir, "title", "") or ""),
        "mode": mode,
        "jurisdiction": "uk",
        "uk_applicability_mode": applicability_mode,
        "uk_respect_feed_applied": applicability_mode == "effective_date_plus_feed_applied",
        "uk_replay_regime": {
            "semantic_replay_lane": semantic_replay_lane,
            "oracle_alignment_lane": oracle_alignment_lane,
            "source_purity_lane": source_purity_lane,
            "source_semantics_clean": source_semantics_clean,
            "source_first_candidate": source_first_candidate,
            "source_first_candidate_reasons": source_first_candidate_reasons,
            "structural_canonicalization_lane": "pre_replay_target_address_normalization",
            "comparison_lane": "current_pair_benchmark",
            "oracle_alignment_stage": "post_replay_adapter" if allow_oracle_alignment else "none",
            "metadata_backfill_enabled": bool(allow_metadata_backfill),
            "oracle_alignment_enabled": bool(allow_oracle_alignment),
            "applicability_mode": applicability_mode,
            "authority_mode": authority_mode,
        },
        "uk_applicability_regime": {
            "ordering_model": "effective_date",
            "selection_model": applicability_mode,
            "raw_feed_applicability_retained": True,
            "first_class_commencement": False,
            "feed_applied_gate_enabled": applicability_mode == "effective_date_plus_feed_applied",
            "requires_applied_gate_enabled": applicability_mode == "effective_date_plus_requires_applied",
        },
        "proof_contract": _proof_contract(),
        "oracle_version_amendment_id": "current",
        "oracle_suspect_detail": "" if core_comparison else comparison_class,
        "oracle_suspect_pending": "",
        "oracle_present": True,
        "overall_score": float(similarity),
        "section_score": float(similarity),
        "strict_fail_reasons": [],
        "adjudication_kinds": adjudication_kinds,
        "diagnosis_counts": {},
        "section_results": [],
        "section_claims": section_claims,
        "source_pathologies": [],
        "html_topology": {},
        "contingent_effective_sources": [],
        "supporting_amendments": [],
        "compiler_observations": {
            "uk_source_authority_summary": {
                "authority_counts": authority_counts,
                "metadata_backfill_op_count": metadata_backfill_op_count,
                "authority_mode": authority_mode,
                "authority_rejection_count": len(authority_rejections),
                "authority_rejection_reason_counts": dict(sorted(authority_rejection_reason_counts.items())),
                "authority_rejections": authority_rejections,
            },
            "uk_witness_migration_summary": {
                "payload_sidecar_attached_op_count": sum(authority_counts.values()) - authority_counts.get("UNSPECIFIED", 0),
                "text_rewrite_witness_op_count": text_rewrite_witness_op_count,
                "fragment_substitution_runtime_note_fallback_count": fragment_substitution_runtime_note_fallback_count,
                "fragment_substitution_note_only_count": fragment_substitution_note_only_count,
                "insertion_anchor_note_only_count": insertion_anchor_note_only_count,
                "insertion_anchor_runtime_note_fallback_count": 0,
            },
            "uk_canonicalization_summary": {
                "schedule_address_normalization_active": True,
                "body_descendant_address_normalization_active": True,
                "transparent_wrapper_policy": "uk_canonicalize_api_v2",
                "transparent_recursive_descent_policy": "uk_canonicalize_api_v1",
                "kind_alias_matching_policy": "uk_canonicalize_api_v1",
                "schedule_lookup_policy": "uk_canonicalize_api_v1",
                "compound_subsection_alias_policy": "uk_canonicalize_api_v1",
                "recursive_lookup_policy": "uk_canonicalize_api_v1",
            },
            "uk_applicability_summary": {
                "effective_date_op_count": applicability_effective_date_op_count,
                "multi_in_force_date_op_count": applicability_multi_in_force_date_op_count,
                "requires_applied_op_count": applicability_requires_applied_op_count,
                "unapplied_op_count": applicability_unapplied_op_count,
            },
        },
        "section_strict_verdicts": None,
        "section_bisect": [],
        "proof_claims": proof_claims,
        "proof_tiers": [
            tier for tier in _PRIMARY_TIER_ORDER if tier in {str(item.get("tier") or "") for item in proof_claims}
        ],
        "primary_proof_tier": _primary_proof_tier(proof_claims),
        "span_anchor_counts": None,
        "chain_completeness": None,
        "uk_oracle_comparison": {
            "comparison_class": comparison_class,
            "core_comparison": core_comparison,
            "n_enacted_eids": len(base_eids),
            "n_oracle_eids": len(oracle_eids),
            "n_replayed_eids": len(replayed_eids),
            "n_effects": n_effects,
            "only_in_replayed": only_in_replayed,
            "only_in_oracle": only_in_oracle,
        },
    }


def build_ee_evidence_bundle(
    statute_id: str,
    *,
    mode: str = "legal_pit",
    include_bisect: bool = False,
) -> Dict:
    from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.tools.ee_reporting import (
        build_ee_benchmark_reporting_summary,
        build_ee_comparison_policy_summary,
    )

    pair = _resolve_ee_live_review_pair(statute_id)
    if pair is None:
        return {
            "statute_id": statute_id,
            "mode": mode,
            "jurisdiction": "ee",
            "error": "EE_NO_CURATED_OR_INDEXED_PAIR",
        }
    base_id, oracle_id = pair

    archive = open_rt_archive(_DEFAULT_EE_FARCHIVE)
    try:
        oracle_xml = fetch_rt_xml(oracle_id, archive=archive)
        as_of = extract_effective_date(oracle_xml) or "9999-12-31"
        result = replay_ee_to_pit(
            base_id=base_id,
            as_of=as_of,
            archive=archive,
            verbose=False,
            oracle_id=oracle_id,
        )
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()

    if result.error:
        return {
            "statute_id": oracle_id,
            "base_id": base_id,
            "oracle_id": oracle_id,
            "mode": mode,
            "jurisdiction": "ee",
            "error": result.error,
        }

    divergence_addresses = [
        "/".join(f"{kind}:{label}" for kind, label in getattr(d.address, "path", ()))
        for d in result.divergences
    ]
    residual_summary = build_ee_residual_summary(
        base_id=base_id,
        oracle_id=oracle_id,
        divergence_addresses=divergence_addresses,
    )
    reporting_summary = build_ee_benchmark_reporting_summary(
        getattr(result, "source_basis", ""),
        getattr(result, "comparison_class", ""),
    )
    comparison_policy = build_ee_comparison_policy_summary()

    section_claims: List[Dict[str, Any]] = []
    matched_bucket_counts: Counter[str] = Counter()
    unknown_divergence_count = 0
    unknown_divergence_type_counts: Counter[str] = Counter()
    for divergence, address in zip(result.divergences, divergence_addresses):
        bucket = ""
        divergence_type = str(getattr(divergence, "divergence_type", "") or "")
        if residual_summary is not None:
            record = residual_summary.record_by_address.get(address)
            if record is not None:
                bucket = str(record.bucket or "")
        if bucket == "source_oracle_drift":
            tier = "PROVED_ORACLE_INCORRECT"
            kind = "ee_residual_source_oracle_drift"
            inference_rule = "ee_residual_inventory_match"
            matched_bucket_counts[bucket] += 1
        elif bucket == "replay_bug":
            tier = "UNRESOLVED"
            kind = "ee_residual_replay_bug"
            inference_rule = "ee_residual_inventory_match"
            matched_bucket_counts[bucket] += 1
        elif bucket == "oracle_correction_notice":
            tier = "UNRESOLVED"
            kind = "ee_residual_oracle_correction_notice"
            inference_rule = "ee_residual_inventory_match"
            matched_bucket_counts[bucket] += 1
        elif bucket == "source_pathology":
            tier = "PROVED_SOURCE_PATHOLOGY"
            kind = "ee_residual_source_pathology"
            inference_rule = "ee_residual_inventory_match"
            matched_bucket_counts[bucket] += 1
        elif bucket == "appendix_display_pathology":
            tier = "PROVED_SOURCE_PATHOLOGY"
            kind = "ee_residual_appendix_display_pathology"
            inference_rule = "ee_residual_inventory_match"
            matched_bucket_counts[bucket] += 1
        elif bucket == "descendant_residual_mix":
            tier = "UNRESOLVED"
            kind = "ee_residual_descendant_residual_mix"
            inference_rule = "ee_residual_inventory_match"
            matched_bucket_counts[bucket] += 1
        else:
            tier = "UNRESOLVED"
            kind = _ee_unknown_divergence_section_kind(divergence_type)
            inference_rule = "ee_live_pair_divergence"
            unknown_divergence_count += 1
            unknown_divergence_type_counts[divergence_type] += 1
        section_claims.append(
            {
                "section": address,
                "selected_tier": tier,
                "selected_kind": kind,
                "selected_inference_rule": inference_rule,
                "defeated_candidate_kinds": [],
                "defeated_candidates": [],
                "divergence_type": divergence_type,
            }
        )

    trigger_observations = [
        {"source": "ee_oracle_comparison", "field": "source_basis", "value": getattr(result, "source_basis", "")},
        {"source": "ee_oracle_comparison", "field": "comparison_class", "value": getattr(result, "comparison_class", "")},
        {"source": "ee_oracle_comparison", "field": "divergence_count", "value": len(result.divergences)},
        {"source": "ee_oracle_comparison", "field": "unknown_current_divergence_count", "value": unknown_divergence_count},
        {
            "source": "ee_oracle_comparison",
            "field": "benchmark_reporting_stratum",
            "value": reporting_summary["benchmark_reporting_stratum"],
        },
    ]

    proof_claims: List[Dict[str, Any]] = []
    if not result.divergences:
        proof_claims = [
            {
                "tier": "UNRESOLVED",
                "kind": "no_strong_claim",
                "trigger_observations": trigger_observations,
            }
        ]
    else:
        if matched_bucket_counts.get("source_pathology", 0) > 0:
            proof_claims.append(
                {
                    "tier": "PROVED_SOURCE_PATHOLOGY",
                    "kind": "ee_residual_source_pathology",
                    "trigger_observations": trigger_observations,
                    "support": {"matched_bucket_count": matched_bucket_counts["source_pathology"]},
                }
            )
        if matched_bucket_counts.get("appendix_display_pathology", 0) > 0:
            proof_claims.append(
                {
                    "tier": "PROVED_SOURCE_PATHOLOGY",
                    "kind": "ee_residual_appendix_display_pathology",
                    "trigger_observations": trigger_observations,
                    "support": {"matched_bucket_count": matched_bucket_counts["appendix_display_pathology"]},
                }
            )
        if matched_bucket_counts.get("source_oracle_drift", 0) > 0:
            proof_claims.append(
                {
                    "tier": "PROVED_ORACLE_INCORRECT",
                    "kind": "ee_residual_source_oracle_drift",
                    "trigger_observations": trigger_observations,
                    "support": {"matched_bucket_count": matched_bucket_counts["source_oracle_drift"]},
                }
            )
        if not proof_claims or unknown_divergence_count > 0:
            proof_claims.append(
                {
                    "tier": "UNRESOLVED",
                    "kind": _ee_unknown_divergence_proof_kind(unknown_divergence_type_counts),
                    "trigger_observations": trigger_observations,
                    "support": {
                        "unknown_divergence_count": unknown_divergence_count,
                        "unknown_divergence_type_counts": dict(sorted(unknown_divergence_type_counts.items())),
                    },
                }
            )

    title = str(getattr(result.oracle, "title", "") or getattr(result.replayed, "title", "") or result.base_title or "")
    diagnosis_counts = dict(Counter(getattr(d, "divergence_type", "") for d in result.divergences if getattr(d, "divergence_type", "")))
    strict_fail_reasons = ["EE.AMENDMENT_FETCH_OR_PARSE_FAILURE"] if getattr(result, "amendments_failed", []) else []

    return {
        "statute_id": oracle_id,
        "base_id": base_id,
        "oracle_id": oracle_id,
        "title": title,
        "mode": mode,
        "jurisdiction": "ee",
        "proof_contract": _proof_contract(),
        "oracle_version_amendment_id": oracle_id,
        "oracle_suspect_detail": "" if getattr(result, "comparison_class", "") == "commensurable_delta" else getattr(result, "comparison_class", ""),
        "oracle_suspect_pending": "",
        "oracle_present": bool(getattr(result, "oracle", None) is not None),
        "overall_score": 1.0 if not result.divergences else 0.0,
        "section_score": 1.0 if not result.divergences else max(0.0, 1.0 - (len(result.divergences) / max(1, len(result.divergences) + 1))),
        "strict_fail_reasons": strict_fail_reasons,
        "adjudication_kinds": sorted({str(getattr(adj, "kind", "") or "") for adj in getattr(result, "adjudications", []) if str(getattr(adj, "kind", "") or "")}),
        "diagnosis_counts": diagnosis_counts,
        "section_results": [],
        "section_claims": section_claims,
        "source_pathologies": [],
        "html_topology": {},
        "contingent_effective_sources": [],
        "supporting_amendments": [],
        "compiler_observations": {
            "ee_pair_summary": {
                "source_basis": getattr(result, "source_basis", ""),
                "comparison_class": getattr(result, "comparison_class", ""),
                "ops_count": getattr(result, "n_ops", 0),
                "divergence_count": len(result.divergences),
                "mismatch_count": getattr(result, "n_mismatch", 0),
                "ops_missing_count": getattr(result, "n_ops_missing", 0),
                "consolidated_missing_count": getattr(result, "n_con_missing", 0),
                "applied_amendment_count": len(getattr(result, "amendments_applied", []) or []),
                "failed_amendment_count": len(getattr(result, "amendments_failed", []) or []),
            },
            "ee_residual_summary": (
                {
                    "residual_count": residual_summary.residual_count,
                    "bucket_counts": residual_summary.bucket_counts,
                    "matched_current_divergence_count": residual_summary.matched_current_divergence_count,
                    "matched_current_bucket_counts": residual_summary.matched_current_bucket_counts,
                    "unknown_current_divergence_count": residual_summary.unknown_current_divergence_count,
                }
                if residual_summary is not None
                else {}
            ),
            "ee_comparison_policy_summary": comparison_policy,
        },
        "section_strict_verdicts": None,
        "section_bisect": [],
        "proof_claims": proof_claims,
        "proof_tiers": [
            tier for tier in _PRIMARY_TIER_ORDER if tier in {str(item.get("tier") or "") for item in proof_claims}
        ],
        "primary_proof_tier": _primary_proof_tier(proof_claims),
        "span_anchor_counts": None,
        "chain_completeness": None,
        "source_basis": getattr(result, "source_basis", ""),
        "comparison_class": getattr(result, "comparison_class", ""),
        "benchmark_reporting_stratum": reporting_summary["benchmark_reporting_stratum"],
        "benchmark_reporting_headline_eligible": reporting_summary["benchmark_reporting_headline_eligible"],
        "comparison_policy": comparison_policy,
    }


def _ee_unknown_divergence_section_kind(divergence_type: str) -> str:
    dtype = str(divergence_type or "").strip().upper()
    if dtype == "MISMATCH":
        return "UNRESOLVED.ee_live_divergence.mismatch"
    if dtype == "OPS_MISSING":
        return "UNRESOLVED.ee_live_divergence.ops_missing"
    if dtype == "CONSOLIDATED_MISSING":
        return "UNRESOLVED.ee_live_divergence.consolidated_missing"
    return "UNRESOLVED.ee_live_divergence.other"


def _ee_unknown_divergence_proof_kind(
    divergence_type_counts: Counter[str],
) -> str:
    nonzero = {str(k): int(v) for k, v in divergence_type_counts.items() if int(v or 0) > 0}
    if not nonzero:
        return "UNRESOLVED.ee_live_divergence"
    if len(nonzero) > 1:
        return "UNRESOLVED.ee_live_divergence.mixed_types"
    only = next(iter(nonzero))
    if only == "MISMATCH":
        return "UNRESOLVED.ee_live_divergence.mismatch_only"
    if only == "OPS_MISSING":
        return "UNRESOLVED.ee_live_divergence.ops_missing_only"
    if only == "CONSOLIDATED_MISSING":
        return "UNRESOLVED.ee_live_divergence.consolidated_missing_only"
    return "UNRESOLVED.ee_live_divergence.other_only"


def build_oracle_proof_bundle(
    statute_id: str,
    *,
    mode: str = "legal_pit",
    include_bisect: bool = False,
) -> Dict:
    bundle = build_evidence_bundle(statute_id, mode=mode, include_bisect=include_bisect)
    if bundle.get("error"):
        return bundle
    oracle_claims = [
        claim for claim in bundle.get("proof_claims", []) if str(claim.get("tier") or "") == "PROVED_ORACLE_INCORRECT"
    ]
    oracle_section_claims = [
        item
        for item in bundle.get("section_claims", [])
        if str(item.get("selected_tier") or "") == "PROVED_ORACLE_INCORRECT"
    ]
    oracle_claim_sections = {
        str(item.get("section") or "") for item in oracle_section_claims if str(item.get("section") or "")
    }
    oracle_claim_by_section = {
        str(item.get("section") or ""): item for item in oracle_section_claims if str(item.get("section") or "")
    }
    bisect_by_section = {
        str(item.get("section") or ""): item
        for item in bundle.get("section_bisect", []) or []
        if str(item.get("section") or "")
    }
    amendment_context_cache: Dict[str, Dict[str, str]] = {}

    def _source_context(source_id: str) -> Dict[str, str]:
        key = str(source_id or "")
        if key not in amendment_context_cache:
            amendment_context_cache[key] = _load_amendment_source_context(key)
        return amendment_context_cache[key]

    support_by_amendment: Dict[str, Dict[str, Any]] = {}
    for item in bundle.get("supporting_amendments", []) or []:
        amendment_id = str(item.get("amendment_id") or "")
        if amendment_id:
            support_by_amendment[amendment_id] = dict(item)
    enriched_supporting_amendments = []

    enriched_section_results = []
    recovered_blame_sections: Dict[str, List[str]] = defaultdict(list)
    for item in bundle["section_results"]:
        section = str(item.get("section") or "")
        if not (str(item.get("diagnosis") or "") in _ORACLE_INCORRECT_DIAGNOSES or section in oracle_claim_sections):
            continue
        bisect_row = bisect_by_section.get(section) or {}
        blame_source = _recover_oracle_artifact_blame_source(item, bisect_row=bisect_row)
        claim = oracle_claim_by_section.get(section, {})
        chain_sources = _oracle_artifact_chain_sources(
            {**item, "blame_source": blame_source},
            claim,
        )
        if blame_source and section:
            recovered_blame_sections[blame_source].append(section)
        for amendment_id in chain_sources:
            if section:
                recovered_blame_sections[amendment_id].append(section)
        ctx = _source_context(blame_source) if blame_source else {}
        chain_amendments = []
        for amendment_id in chain_sources:
            amendment_ctx = _source_context(amendment_id) if amendment_id else {}
            chain_amendments.append(
                {
                    "amendment_id": amendment_id,
                    "amendment_url": amendment_ctx.get("amendment_url", ""),
                    "source_title": amendment_ctx.get("source_title", ""),
                    "johtolause": amendment_ctx.get("johtolause", ""),
                }
            )
        enriched_item = {
            **item,
            "blame_source": blame_source,
            "chain_sources": chain_sources,
            "chain_amendments": chain_amendments,
            "section_url": _finlex_section_url(statute_id, section),
            "blame_source_url": ctx.get("amendment_url", ""),
            "blame_source_title": ctx.get("source_title", ""),
            "blame_source_johtolause": ctx.get("johtolause", ""),
        }
        enriched_item["artifact_profile"] = _oracle_artifact_profile_for_section(
            enriched_item,
            claim,
            bisect_row=bisect_row,
        )
        enriched_section_results.append(enriched_item)

    for amendment_id in sorted(recovered_blame_sections):
        item: Dict[str, Any] = support_by_amendment.get(amendment_id) or {
            "amendment_id": amendment_id,
            "blamed_sections": [],
            "official_item_count": 0,
            "verified_in_source_count": 0,
            "unverified_item_count": 0,
            "source_pdf_count": 0,
            "source_pdfs": [],
            "manual_override_count": 0,
        }
        existing_sections = {str(section) for section in cast(list, item.get("blamed_sections", [])) if str(section)}
        item["blamed_sections"] = sorted(existing_sections | set(recovered_blame_sections.get(amendment_id, [])))
        ctx = _source_context(amendment_id) if amendment_id else {}
        enriched_supporting_amendments.append(
            {
                **item,
                "amendment_url": ctx.get("amendment_url", ""),
                "source_title": ctx.get("source_title", ""),
                "johtolause": ctx.get("johtolause", ""),
            }
        )

    artifact_summary = _oracle_artifact_summary(
        oracle_claims=oracle_claims,
        section_results=enriched_section_results,
    )
    oracle_version_amendment_id = str(
        bundle.get("oracle_version_amendment_id")
        or bundle.get("oracle_version_mid")
        or ""
    )

    return {
        "statute_id": bundle["statute_id"],
        "title": bundle["title"],
        "mode": bundle["mode"],
        "proof_contract": bundle.get("proof_contract") or _proof_contract(),
        "oracle_version_amendment_id": oracle_version_amendment_id,
        "oracle_version_mid": oracle_version_amendment_id,
        "oracle_suspect_detail": bundle.get("oracle_suspect_detail", ""),
        "oracle_suspect_pending": bundle.get("oracle_suspect_pending", ""),
        "verification_links": {
            "consolidated_url": _finlex_section_url(statute_id),
        },
        "artifact_summary": artifact_summary,
        "html_topology": bundle["html_topology"],
        "supporting_amendments": enriched_supporting_amendments,
        "section_bisect": bundle.get("section_bisect", []),
        "section_results": enriched_section_results,
        "section_claims": oracle_section_claims,
        "proof_claims": oracle_claims,
        "proved": bool(oracle_claims),
        "primary_proof_tier": "PROVED_ORACLE_INCORRECT" if oracle_claims else "UNRESOLVED",
        "alternative_tiers": [tier for tier in bundle.get("proof_tiers", []) if tier != "PROVED_ORACLE_INCORRECT"],
    }


def main(args) -> None:
    command = getattr(args, "command", "")
    jurisdiction = str(getattr(args, "jurisdiction", "fi") or "fi").lower()
    uk_allow_metadata_backfill = getattr(args, "uk_allow_metadata_backfill", None)
    uk_allow_oracle_alignment = getattr(args, "uk_allow_oracle_alignment", None)
    uk_respect_feed_applied = getattr(args, "uk_respect_feed_applied", None)
    uk_applicability_mode = getattr(args, "uk_applicability_mode", None)
    uk_source_first_candidate = bool(getattr(args, "uk_source_first_candidate", False))
    uk_authority_mode = getattr(args, "uk_authority_mode", None)
    if jurisdiction != "uk" and (
        uk_allow_metadata_backfill is not None
        or uk_allow_oracle_alignment is not None
        or uk_respect_feed_applied is not None
        or uk_applicability_mode is not None
        or uk_source_first_candidate
        or uk_authority_mode is not None
    ):
        print(
            "ERROR: UK replay regime flags are only supported with -j uk",
            file=sys.stderr,
        )
        sys.exit(1)
    if jurisdiction != "uk":
        uk_allow_metadata_backfill = None
        uk_allow_oracle_alignment = None
        uk_respect_feed_applied = None
        uk_applicability_mode = None
        uk_source_first_candidate = False
        uk_authority_mode = None
    if jurisdiction == "uk":
        if (
            uk_applicability_mode is not None
            and uk_respect_feed_applied is True
            and uk_applicability_mode != "effective_date_plus_feed_applied"
        ):
            print("ERROR: --applicability-mode conflicts with --respect-feed-applied", file=sys.stderr)
            sys.exit(1)
        if (
            uk_applicability_mode is not None
            and uk_respect_feed_applied is False
            and uk_applicability_mode != "effective_date_only"
        ):
            print("ERROR: --applicability-mode conflicts with --ignore-feed-applied", file=sys.stderr)
            sys.exit(1)
        if uk_applicability_mode is None:
            if uk_respect_feed_applied is True:
                uk_applicability_mode = "effective_date_plus_feed_applied"
            elif uk_respect_feed_applied is False:
                uk_applicability_mode = "effective_date_only"
            else:
                uk_applicability_mode = "effective_date_plus_feed_applied"
    if jurisdiction == "uk" and uk_source_first_candidate:
        if uk_allow_metadata_backfill is True:
            print("ERROR: --source-first-candidate conflicts with --metadata-backfill", file=sys.stderr)
            sys.exit(1)
        if uk_allow_oracle_alignment is True:
            print("ERROR: --source-first-candidate conflicts with --oracle-alignment", file=sys.stderr)
            sys.exit(1)
        if uk_applicability_mode != "effective_date_plus_feed_applied":
            print("ERROR: --source-first-candidate conflicts with --applicability-mode", file=sys.stderr)
            sys.exit(1)
        if uk_authority_mode == "current_mixed":
            print("ERROR: --source-first-candidate conflicts with --authority-mode current_mixed", file=sys.stderr)
            sys.exit(1)
        uk_allow_metadata_backfill = False
        uk_allow_oracle_alignment = False
        uk_respect_feed_applied = True
        uk_applicability_mode = "effective_date_plus_feed_applied"
        uk_authority_mode = "source_text_only"
    if command == "evidence-review":
        raw_statute_ids = getattr(args, "statute_id", None) or []
        if isinstance(raw_statute_ids, str):
            statute_ids = [raw_statute_ids]
        else:
            statute_ids = [str(item) for item in raw_statute_ids if str(item)]
        oracle_corpus = bool(getattr(args, "oracle_corpus", False))
        raw_paths = getattr(args, "artifact_path", [])
        artifact_paths = [str(item) for item in raw_paths if str(item)]
        selected_inputs = sum(
            1
            for present in (
                bool(artifact_paths),
                bool(statute_ids),
                bool(oracle_corpus),
            )
            if present
        )
        if selected_inputs > 1:
            print(
                "ERROR: provide artifact_path, --statute-id, or --oracle-corpus (choose one)",
                file=sys.stderr,
            )
            sys.exit(1)
        if selected_inputs == 0:
            print(
                "ERROR: provide at least one artifact_path, --statute-id, or --oracle-corpus",
                file=sys.stderr,
            )
            sys.exit(1)
        primary_tier = str(getattr(args, "primary_tier", "") or "")
        tier = str(getattr(args, "tier", "") or "")
        kind = str(getattr(args, "kind", "") or "")
        section_kind = str(getattr(args, "section_kind", "") or "")
        section_rule = str(getattr(args, "section_rule", "") or "")
        trigger_source = str(getattr(args, "trigger_source", "") or "")
        trigger_field = str(getattr(args, "trigger_field", "") or "")
        strict_fail_reason = str(getattr(args, "strict_fail_reason", "") or "")
        elaboration_observation_kind = str(getattr(args, "elaboration_observation_kind", "") or "")
        sparse_leftovers_only = bool(getattr(args, "sparse_leftovers_only", False))
        sparse_blocker_source = str(getattr(args, "sparse_blocker_source", "") or "")
        sparse_blocker_section = str(getattr(args, "sparse_blocker_section", "") or "")
        payload_completeness_kind = str(getattr(args, "payload_completeness_kind", "") or "")
        payload_tail_policy = str(getattr(args, "payload_tail_policy", "") or "")
        provenance_projection_kind = str(getattr(args, "provenance_projection_kind", "") or "")
        provenance_tag = str(getattr(args, "provenance_tag", "") or "")
        provenance_source_statute = str(getattr(args, "provenance_source_statute", "") or "")
        source_proof_kind = str(getattr(args, "source_proof_kind", "") or "")
        source_pathology_code = str(getattr(args, "source_pathology_code", "") or "")
        source_pathology_source = str(getattr(args, "source_pathology_source", "") or "")
        source_pathology_target_label = str(getattr(args, "source_pathology_target_label", "") or "")
        source_pathology_diagnostic_reason = str(getattr(args, "source_pathology_diagnostic_reason", "") or "")
        alternative_replay_section = str(getattr(args, "alternative_replay_section", "") or "")
        html_noncommensurable_reason = str(getattr(args, "html_noncommensurable_reason", "") or "")
        evidence_context_degraded_only = bool(getattr(args, "evidence_context_degraded", False))
        evidence_context_rail = str(getattr(args, "evidence_context_rail", "") or "")
        actionable_unresolved_only = bool(getattr(args, "actionable_unresolved_only", False))
        nontrivial_unresolved_only = bool(getattr(args, "nontrivial_unresolved_only", False))
        mixed_replay_risk_only = bool(getattr(args, "mixed_replay_risk_only", False))
        ready_oracle_artifacts_only = bool(getattr(args, "ready_oracle_artifacts_only", False))
        oracle_artifact_family = str(getattr(args, "oracle_artifact_family", "") or "")
        oracle_artifact_gap = str(getattr(args, "oracle_artifact_gap", "") or "")
        limit = int(getattr(args, "limit", 20) or 20)
        if artifact_paths:
            review = review_bundle_artifacts(
                artifact_paths,
                primary_tier=primary_tier,
                tier=tier,
                kind=kind,
                section_kind=section_kind,
                section_rule=section_rule,
                trigger_source=trigger_source,
                trigger_field=trigger_field,
                strict_fail_reason=strict_fail_reason,
                elaboration_observation_kind=elaboration_observation_kind,
                sparse_leftovers_only=sparse_leftovers_only,
                sparse_blocker_source=sparse_blocker_source,
                sparse_blocker_section=sparse_blocker_section,
                payload_completeness_kind=payload_completeness_kind,
                payload_tail_policy=payload_tail_policy,
                provenance_projection_kind=provenance_projection_kind,
                provenance_tag=provenance_tag,
                provenance_source_statute=provenance_source_statute,
                source_proof_kind=source_proof_kind,
                source_pathology_code=source_pathology_code,
                source_pathology_source=source_pathology_source,
                source_pathology_target_label=source_pathology_target_label,
                source_pathology_diagnostic_reason=source_pathology_diagnostic_reason,
                alternative_replay_section=alternative_replay_section,
                html_noncommensurable_reason=html_noncommensurable_reason,
                evidence_context_degraded_only=evidence_context_degraded_only,
                evidence_context_rail=evidence_context_rail,
                actionable_unresolved_only=actionable_unresolved_only,
                nontrivial_unresolved_only=nontrivial_unresolved_only,
                mixed_replay_risk_only=mixed_replay_risk_only,
                ready_oracle_artifacts_only=ready_oracle_artifacts_only,
                oracle_artifact_family=oracle_artifact_family,
                oracle_artifact_gap=oracle_artifact_gap,
                limit=limit,
            )
        else:
            mode = str(getattr(args, "mode", "legal_pit") or "legal_pit")
            include_bisect = bool(getattr(args, "with_bisect", False))
            workers = int(getattr(args, "workers", 1) or 1)
            corpus_store_mode = str(getattr(args, "corpus_store", "") or "")
            cache_only = bool(getattr(args, "cache_only", False))
            bundle_cache_dir = _effective_bundle_cache_dir(
                str(getattr(args, "bundle_cache_dir", "") or ""),
                oracle_corpus=bool(oracle_corpus),
            )
            if oracle_corpus:
                review = review_live_oracle_corpus(
                    jurisdiction=jurisdiction,
                    mode=mode,
                    include_bisect=include_bisect,
                    workers=workers,
                    corpus_store_mode=corpus_store_mode,
                    cache_only=cache_only,
                    bundle_cache_dir=bundle_cache_dir,
                    primary_tier=primary_tier,
                    tier=tier,
                    kind=kind,
                    section_kind=section_kind,
                    section_rule=section_rule,
                    trigger_source=trigger_source,
                    trigger_field=trigger_field,
                    strict_fail_reason=strict_fail_reason,
                    elaboration_observation_kind=elaboration_observation_kind,
                    sparse_leftovers_only=sparse_leftovers_only,
                    sparse_blocker_source=sparse_blocker_source,
                    sparse_blocker_section=sparse_blocker_section,
                    payload_completeness_kind=payload_completeness_kind,
                    payload_tail_policy=payload_tail_policy,
                    provenance_projection_kind=provenance_projection_kind,
                    provenance_tag=provenance_tag,
                    provenance_source_statute=provenance_source_statute,
                    source_proof_kind=source_proof_kind,
                    source_pathology_code=source_pathology_code,
                    source_pathology_source=source_pathology_source,
                    source_pathology_target_label=source_pathology_target_label,
                    source_pathology_diagnostic_reason=source_pathology_diagnostic_reason,
                    alternative_replay_section=alternative_replay_section,
                    html_noncommensurable_reason=html_noncommensurable_reason,
                    evidence_context_degraded_only=evidence_context_degraded_only,
                    evidence_context_rail=evidence_context_rail,
                    actionable_unresolved_only=actionable_unresolved_only,
                    nontrivial_unresolved_only=nontrivial_unresolved_only,
                    mixed_replay_risk_only=mixed_replay_risk_only,
                    ready_oracle_artifacts_only=ready_oracle_artifacts_only,
                    oracle_artifact_family=oracle_artifact_family,
                    oracle_artifact_gap=oracle_artifact_gap,
                    limit=limit,
                    chunk_size=int(getattr(args, "chunk_size", 200) or 200),
                    min_year=int(getattr(args, "min_year", 0) or 0),
                    max_year=int(getattr(args, "max_year", 0) or 0),
                    start_at=int(getattr(args, "start_at", 0) or 0),
                    max_statutes=int(getattr(args, "max_statutes", 0) or 0),
                    progress_path=str(getattr(args, "progress_path", "") or ""),
                    output_path=str(getattr(args, "output", "") or ""),
                    resume=bool(getattr(args, "resume", False)),
                    allow_metadata_backfill=uk_allow_metadata_backfill,
                    allow_oracle_alignment=uk_allow_oracle_alignment,
                    applicability_mode=uk_applicability_mode,
                    authority_mode=uk_authority_mode,
                )
            else:
                cache_stats = {
                    "bundle_cache_hits": 0,
                    "bundle_cache_misses": 0,
                    "bundle_cache_errors": 0,
                }
                bundles = _build_live_review_bundles(
                    statute_ids,
                    jurisdiction=jurisdiction,
                    mode=mode,
                    include_bisect=include_bisect,
                    workers=workers,
                    corpus_store_mode=corpus_store_mode,
                    cache_only=cache_only,
                    bundle_cache_dir=bundle_cache_dir,
                    cache_stats_out=cache_stats,
                    oracle_only=ready_oracle_artifacts_only,
                    allow_metadata_backfill=uk_allow_metadata_backfill,
                    allow_oracle_alignment=uk_allow_oracle_alignment,
                    applicability_mode=uk_applicability_mode,
                    authority_mode=uk_authority_mode,
                )
                review = _review_bundles(
                    bundles,
                    primary_tier=primary_tier,
                    tier=tier,
                    kind=kind,
                    section_kind=section_kind,
                    section_rule=section_rule,
                    trigger_source=trigger_source,
                    trigger_field=trigger_field,
                    strict_fail_reason=strict_fail_reason,
                    elaboration_observation_kind=elaboration_observation_kind,
                    sparse_leftovers_only=sparse_leftovers_only,
                    sparse_blocker_source=sparse_blocker_source,
                    sparse_blocker_section=sparse_blocker_section,
                    payload_completeness_kind=payload_completeness_kind,
                    payload_tail_policy=payload_tail_policy,
                    provenance_projection_kind=provenance_projection_kind,
                    provenance_tag=provenance_tag,
                    provenance_source_statute=provenance_source_statute,
                    source_proof_kind=source_proof_kind,
                    source_pathology_code=source_pathology_code,
                    source_pathology_source=source_pathology_source,
                    source_pathology_target_label=source_pathology_target_label,
                    source_pathology_diagnostic_reason=source_pathology_diagnostic_reason,
                    alternative_replay_section=alternative_replay_section,
                    html_noncommensurable_reason=html_noncommensurable_reason,
                    evidence_context_degraded_only=evidence_context_degraded_only,
                    evidence_context_rail=evidence_context_rail,
                    actionable_unresolved_only=actionable_unresolved_only,
                    nontrivial_unresolved_only=nontrivial_unresolved_only,
                    mixed_replay_risk_only=mixed_replay_risk_only,
                    ready_oracle_artifacts_only=ready_oracle_artifacts_only,
                    oracle_artifact_family=oracle_artifact_family,
                    oracle_artifact_gap=oracle_artifact_gap,
                    limit=limit,
                )
                review["statute_count"] = len(statute_ids)
                review["jurisdiction"] = jurisdiction
                review["mode"] = mode
                review["with_bisect"] = include_bisect
                review["workers"] = max(1, workers)
                review["cache_only"] = cache_only
                review["bundle_cache_dir"] = bundle_cache_dir
                review["bundle_cache_hits"] = cache_stats["bundle_cache_hits"]
                review["bundle_cache_misses"] = cache_stats["bundle_cache_misses"]
                review["bundle_cache_errors"] = cache_stats["bundle_cache_errors"]
                review["corpus_store_mode"] = corpus_store_mode
                review["uk_metadata_backfill_enabled"] = uk_allow_metadata_backfill
                review["uk_oracle_alignment_enabled"] = uk_allow_oracle_alignment
                review["uk_applicability_mode"] = uk_applicability_mode
                review["uk_authority_mode"] = uk_authority_mode
        if bool(getattr(args, "json", False) or getattr(args, "json_output", False)):
            print(json.dumps(review, ensure_ascii=False, indent=2))
        else:
            _print_review_summary(review)
        return

    raw_statute_ids = getattr(args, "statute_id", None) or []
    if isinstance(raw_statute_ids, str):
        statute_ids = [raw_statute_ids]
    else:
        statute_ids = [str(item) for item in raw_statute_ids if str(item)]
    mode = str(getattr(args, "mode", "legal_pit") or "legal_pit")
    json_output = bool(getattr(args, "json", False) or getattr(args, "json_output", False))
    markdown_output = bool(getattr(args, "markdown", False))
    output_path = str(getattr(args, "output", "") or "").strip()
    include_bisect = bool(getattr(args, "with_bisect", False))
    if not statute_ids:
        print("ERROR: provide at least one statute_id", file=sys.stderr)
        sys.exit(1)

    bundles: List[Dict] = []
    for statute_id in statute_ids:
        if command == "evidence":
            if jurisdiction == "uk":
                bundle = build_uk_evidence_bundle(
                    statute_id,
                    mode=mode,
                    include_bisect=include_bisect,
                    allow_metadata_backfill=uk_allow_metadata_backfill,
                    allow_oracle_alignment=uk_allow_oracle_alignment,
                    applicability_mode=uk_applicability_mode,
                    authority_mode=uk_authority_mode,
                )
            elif include_bisect:
                bundle = build_evidence_bundle(statute_id, mode=mode, include_bisect=True)
            else:
                bundle = build_evidence_bundle(statute_id, mode=mode)
        elif command == "prove-oracle":
            if jurisdiction == "uk":
                bundle = build_uk_evidence_bundle(
                    statute_id,
                    mode=mode,
                    include_bisect=include_bisect,
                    allow_metadata_backfill=uk_allow_metadata_backfill,
                    allow_oracle_alignment=uk_allow_oracle_alignment,
                    applicability_mode=uk_applicability_mode,
                    authority_mode=uk_authority_mode,
                )
            elif include_bisect:
                bundle = build_oracle_proof_bundle(statute_id, mode=mode, include_bisect=True)
            else:
                bundle = build_oracle_proof_bundle(statute_id, mode=mode)
        else:
            print(f"Unknown evidence command: {command}", file=sys.stderr)
            sys.exit(1)
        if bundle.get("error"):
            print(f"ERROR: {bundle['error']}", file=sys.stderr)
            sys.exit(1)
        bundles.append(bundle)

    if output_path:
        if markdown_output:
            saved = _write_markdown_output(output_path, bundles, oracle_only=(command == "prove-oracle"))
        else:
            saved = _write_bundle_output(output_path, bundles)
        if not json_output:
            print(f"Saved: {saved}")

    if json_output:
        payload: Dict | List[Dict]
        payload = bundles[0] if len(bundles) == 1 else bundles
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if markdown_output:
        for idx, bundle in enumerate(bundles):
            if idx:
                print("\n---\n")
            sys.stdout.write(_render_markdown_bundle(bundle, oracle_only=(command == "prove-oracle")))
        return

    for idx, bundle in enumerate(bundles):
        if idx:
            print("\n" + "=" * 72 + "\n")
        if command == "prove-oracle":
            _print_oracle_proof_bundle(bundle)
        else:
            _print_evidence_bundle(bundle)
