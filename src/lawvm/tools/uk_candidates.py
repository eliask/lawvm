"""lawvm uk-candidates -- candidate-aware UK frontier triage from a saved bench run."""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence, cast

from lawvm.core.compile_records import is_blocking_compile_record

if TYPE_CHECKING:
    import argparse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_RESIDUAL_CANDIDATE_SAMPLE_LIMIT = 5
_RESIDUAL_CANDIDATE_ROOT_SAMPLE_LIMIT = 10
_DEFAULT_MANUAL_COMPILE_EVIDENCE_STATUSES = ("manual_compile_candidate",)
_ACTIONABLE_MANUAL_COMPILE_EVIDENCE_STATUSES = (
    "manual_compile_candidate",
    "deterministic_frontend_candidate",
)
_ACTIONABLE_MANUAL_COMPILE_EVIDENCE_ALIASES = frozenset(
    {
        "actionable",
        "all_actionable",
    }
)
_REPLAY_ADJUDICATION_KIND_ALIASES: dict[str, tuple[str, ...]] = {
    "uk_text_match_already_rewritten": ("uk_replay_text_match_already_rewritten",),
    "uk_text_match_already_rewritten_mixed_residual_eids": (
        "uk_replay_text_match_already_rewritten",
    ),
}


def _primary_frontier_score(result, *, score_mode: str = "auto") -> float:  # noqa: ANN001
    if score_mode == "replay":
        if result.replay_score >= 0.0:
            return result.replay_score
        if result.replay_commencement_score >= 0.0:
            return result.replay_commencement_score
        if result.commencement_score >= 0.0:
            return result.commencement_score
        return result.score
    if score_mode == "replay_commencement":
        if result.replay_commencement_score >= 0.0:
            return result.replay_commencement_score
        if result.replay_score >= 0.0:
            return result.replay_score
        if result.commencement_score >= 0.0:
            return result.commencement_score
        return result.score
    if (
        result.replay_commencement_score >= 0.0
        and int(getattr(result, "n_commenced_eids", 0) or 0) > 0
    ):
        return result.replay_commencement_score
    if result.replay_score >= 0.0:
        return result.replay_score
    if result.commencement_score >= 0.0:
        return result.commencement_score
    return result.score


def _effective_comparison_class(result) -> str:  # noqa: ANN001
    from lawvm.uk_legislation.source_adjudication import classify_uk_bench_comparison

    comparison_class = str(getattr(result, "comparison_class", "") or "").strip()
    if comparison_class:
        return comparison_class
    return classify_uk_bench_comparison(
        n_enacted_eids=int(getattr(result, "n_enacted_eids", 0) or 0),
        n_oracle_eids=int(getattr(result, "n_oracle_eids", 0) or 0),
        n_effects=int(getattr(result, "n_effect_rows", getattr(result, "n_effects", 0)) or 0),
        raw_score=float(getattr(result, "score", 0.0) or 0.0),
    )


def _effective_core_benchmark(result) -> bool:  # noqa: ANN001
    from lawvm.uk_legislation.source_adjudication import is_core_uk_comparison

    return is_core_uk_comparison(_effective_comparison_class(result))


def _bool_bench_field(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _uk_replay_regime_kwargs_from_bench_row(result) -> dict[str, object]:  # noqa: ANN001
    """Recover the replay regime that produced a saved UK bench row."""
    return {
        "allow_metadata_backfill": _bool_bench_field(
            getattr(result, "uk_metadata_backfill_enabled", None),
            default=True,
        ),
        "allow_oracle_alignment": _bool_bench_field(
            getattr(result, "uk_oracle_alignment_enabled", None),
            default=True,
        ),
        "allow_metadata_only_effects": _bool_bench_field(
            getattr(result, "uk_metadata_only_effects_enabled", None),
            default=True,
        ),
        "applicability_mode": str(
            getattr(result, "uk_applicability_mode", "") or "effective_date_plus_feed_applied"
        ),
        "authority_mode": str(getattr(result, "uk_authority_mode", "") or "current_mixed"),
    }


def _uk_replay_regime_summary_key(value: object) -> str:
    if not isinstance(value, Mapping):
        return "unknown"
    regime = cast(Mapping[str, object], value)
    return (
        f"metadata_backfill={int(bool(regime.get('allow_metadata_backfill')))}"
        f";oracle_alignment={int(bool(regime.get('allow_oracle_alignment')))}"
        f";metadata_only_effects={int(_bool_bench_field(regime.get('allow_metadata_only_effects'), default=True))}"
        f";applicability={regime.get('applicability_mode') or 'unknown'}"
        f";authority={regime.get('authority_mode') or 'unknown'}"
    )


def _string_tuple_from_object(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return (stripped,)
        if isinstance(parsed, list):
            return tuple(str(item) for item in parsed)
        return (stripped,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _uk_replay_regime_claim_from_bench_row(result) -> dict[str, object]:  # noqa: ANN001
    """Read persisted UK source-first regime evidence from a saved bench row."""
    return {
        "source_purity_lane": str(getattr(result, "uk_source_purity_lane", "") or "unknown"),
        "source_semantics_clean": _bool_bench_field(
            getattr(result, "uk_source_semantics_clean", None),
            default=False,
        ),
        "source_first_candidate": _bool_bench_field(
            getattr(result, "uk_source_first_candidate", None),
            default=False,
        ),
        "source_first_candidate_reasons": list(
            _string_tuple_from_object(
                getattr(result, "uk_source_first_candidate_reasons", ())
            )
        ),
    }


def _uk_residual_claim_from_bench_row(result) -> dict[str, object]:  # noqa: ANN001
    """Read persisted replay residual proof-claim evidence from a saved bench row."""
    tier = str(getattr(result, "uk_residual_claim_tier", "") or "UNRESOLVED")
    kind = str(getattr(result, "uk_residual_claim_kind", "") or "unknown_legacy_missing")
    return {
        "selected_tier": tier,
        "selected_kind": kind,
        "comparison_class": str(
            getattr(result, "uk_residual_claim_comparison_class", "") or ""
        ),
        "core_comparison": _bool_bench_field(
            getattr(result, "uk_residual_claim_core_comparison", None),
            default=False,
        ),
        "only_in_replayed_count": int(
            getattr(result, "uk_residual_only_in_replayed_count", 0) or 0
        ),
        "only_in_oracle_count": int(
            getattr(result, "uk_residual_only_in_oracle_count", 0) or 0
        ),
        "section_claim_count": int(
            getattr(result, "uk_residual_section_claim_count", 0) or 0
        ),
        "section_claim_emitted": _bool_bench_field(
            getattr(result, "uk_residual_section_claim_emitted", None),
            default=False,
        ),
    }


def _uk_residual_claim_has_reviewable_evidence(claim: Mapping[str, object]) -> bool:
    kind = str(claim.get("selected_kind") or "")
    if not kind or kind in {"no_strong_claim", "unknown_legacy_missing"}:
        return False
    return (
        int(claim.get("only_in_replayed_count") or 0) > 0
        or int(claim.get("only_in_oracle_count") or 0) > 0
        or int(claim.get("section_claim_count") or 0) > 0
        or _bool_bench_field(claim.get("section_claim_emitted"), default=False)
    )


def _uk_residual_claim_work_item_id(
    *,
    label: str,
    statute_id: str,
    claim: Mapping[str, object],
) -> str:
    parts = (
        label,
        statute_id,
        str(claim.get("selected_tier") or ""),
        str(claim.get("selected_kind") or ""),
        str(claim.get("comparison_class") or ""),
        str(claim.get("only_in_replayed_count") or 0),
        str(claim.get("only_in_oracle_count") or 0),
        str(claim.get("section_claim_count") or 0),
    )
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"uk-residual-claim-{digest}"


def _uk_residual_claim_evidence_row_from_candidate_row(
    row: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any] | None:
    """Build a residual-claim review item from an analyzed candidate row.

    Saved bench rows only persist residual counts. Archive-backed candidate
    rows additionally know the concrete residual roots and candidate overlap,
    so evidence exports must prefer this richer phase output when available.
    """
    claim_obj = row.get("uk_residual_claim")
    if not isinstance(claim_obj, Mapping):
        return None
    claim = cast(Mapping[str, object], claim_obj)
    if not _uk_residual_claim_has_reviewable_evidence(claim):
        return None
    statute_id = str(row.get("statute_id") or "")
    return {
        "schema": "lawvm.uk_residual_claim_frontier.v1",
        "rule_id": "uk_residual_claim_frontier_workqueue",
        "family": "residual_claim_frontier",
        "phase": "oracle_adjudication",
        "jurisdiction": "uk",
        "work_item_kind": "residual_claim_review",
        "claim_kind": str(claim.get("selected_kind") or "unknown"),
        "claim_status": str(claim.get("selected_tier") or "UNRESOLVED"),
        "validator_status": "not_validated",
        "work_item_id": _uk_residual_claim_work_item_id(
            label=label,
            statute_id=statute_id,
            claim=claim,
        ),
        "bench_label": label,
        "statute_id": statute_id,
        "score_mode": str(row.get("score_mode") or ""),
        "frontier_score": float(row.get("frontier_score") or -1.0),
        "raw_score": float(row.get("raw_score") or -1.0),
        "replay_score": float(row.get("replay_score") or -1.0),
        "commencement_score": float(row.get("commencement_score") or -1.0),
        "replay_commencement_score": float(
            row.get("replay_commencement_score") or -1.0
        ),
        "comparison_class": str(row.get("comparison_class") or ""),
        "core_benchmark": bool(row.get("core_benchmark")),
        "uk_replay_regime": dict(
            cast(Mapping[str, object], row.get("uk_replay_regime") or {})
        ),
        "uk_replay_regime_claim": dict(
            cast(Mapping[str, object], row.get("uk_replay_regime_claim") or {})
        ),
        "uk_residual_claim": dict(claim),
        "residual_evidence": {
            "status": str(row.get("status") or ""),
            "triage_rule_id": str(row.get("triage_rule_id") or ""),
            "residual_roots": list(row.get("residual_roots") or ()),
            "replayed_residual_roots": list(row.get("replayed_residual_roots") or ()),
            "oracle_residual_roots": list(row.get("oracle_residual_roots") or ()),
            "malformed_residual_roots": list(row.get("malformed_residual_roots") or ()),
            "backed_residual_roots": list(row.get("backed_residual_roots") or ()),
            "defeated_residual_roots": list(row.get("defeated_residual_roots") or ()),
            "residual_candidate_effect_count": int(
                row.get("residual_candidate_effect_count") or 0
            ),
            "residual_candidate_op_count": int(
                row.get("residual_candidate_op_count") or 0
            ),
            "residual_candidate_root_hit_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("residual_candidate_root_hit_counts") or {},
                )
            ),
            "residual_candidate_root_side_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("residual_candidate_root_side_counts") or {},
                )
            ),
            "residual_candidate_source_pathology_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("residual_candidate_source_pathology_counts") or {},
                )
            ),
            "residual_candidate_compare_shape_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("residual_candidate_compare_shape_counts") or {},
                )
            ),
            "residual_candidate_structural_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("residual_candidate_structural_counts") or {},
                )
            ),
            "residual_candidate_action_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("residual_candidate_action_counts") or {},
                )
            ),
            "residual_candidate_target_presence_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("residual_candidate_target_presence_counts") or {},
                )
            ),
            "residual_candidate_target_presence_action_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("residual_candidate_target_presence_action_counts")
                    or {},
                )
            ),
            "residual_candidate_manual_compile_rule_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("residual_candidate_manual_compile_rule_counts") or {},
                )
            ),
            "residual_candidate_root_samples": list(
                row.get("residual_candidate_root_samples") or ()
            ),
            "residual_candidate_root_samples_omitted": int(
                row.get("residual_candidate_root_samples_omitted") or 0
            ),
            "residual_candidate_samples": list(
                row.get("residual_candidate_samples") or ()
            ),
            "residual_candidate_samples_omitted": int(
                row.get("residual_candidate_samples_omitted") or 0
            ),
            "replay_adjudication_kind_counts": dict(
                cast(Mapping[str, int], row.get("replay_adjudication_kind_counts") or {})
            ),
            "replay_adjudication_bucket_counts": dict(
                cast(
                    Mapping[str, int],
                    row.get("replay_adjudication_bucket_counts") or {},
                )
            ),
            "replay_adjudication_samples": list(
                row.get("replay_adjudication_samples") or ()
            ),
            "replay_adjudication_samples_omitted": int(
                row.get("replay_adjudication_samples_omitted") or 0
            ),
        },
        "enacted_source": {
            "status": str(row.get("enacted_source_status") or "unknown"),
            "size": int(row.get("enacted_source_size") or 0),
            "sha256": str(row.get("enacted_source_sha256") or ""),
            "url": str(row.get("enacted_source_url") or ""),
        },
        "oracle_source": {
            "status": str(row.get("oracle_source_status") or "unknown"),
            "size": int(row.get("oracle_source_size") or 0),
            "sha256": str(row.get("oracle_source_sha256") or ""),
            "url": str(row.get("oracle_source_url") or ""),
        },
    }


def _uk_residual_claim_evidence_rows_from_candidate_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
) -> tuple[dict[str, Any], ...]:
    evidence_rows: list[dict[str, Any]] = []
    for row in rows:
        evidence_row = _uk_residual_claim_evidence_row_from_candidate_row(
            row,
            label=label,
        )
        if evidence_row is not None:
            evidence_rows.append(evidence_row)
    return tuple(evidence_rows)


def _write_residual_claim_evidence_report_from_candidate_rows(
    path: Path | None,
    rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
) -> dict[str, Any] | None:
    if path is None:
        return None
    count = _write_jsonl_rows(
        path,
        _uk_residual_claim_evidence_rows_from_candidate_rows(rows, label=label),
    )
    return {
        "path": str(path),
        "rows": count,
    }


def _matches_filters(  # noqa: ANN001
    result,
    *,
    min_year: int | None,
    max_year: int | None,
    types: set[str] | None,
) -> bool:
    year = int(getattr(result, "year", 0) or 0)
    act_type = str(getattr(result, "act_type", "") or "")
    if min_year is not None and year < min_year:
        return False
    if max_year is not None and year > max_year:
        return False
    if types is not None and act_type not in types:
        return False
    return True


def _row_matches_claim_template_status(row: Mapping[str, Any], status: str) -> bool:
    if not status:
        return True
    counts = row.get("suggested_claim_template_status_counts") or {}
    if not isinstance(counts, Mapping):
        return False
    return int(counts.get(status) or 0) > 0


def _matching_frontier(  # noqa: ANN001
    results,
    *,
    score_mode: str,
    min_year: int | None,
    max_year: int | None,
    types: set[str] | None,
):
    frontier = [
        r for r in results
        if r.status == "OK"
        and _effective_core_benchmark(r)
        and _primary_frontier_score(r, score_mode=score_mode) < 1.0
        and _matches_filters(r, min_year=min_year, max_year=max_year, types=types)
    ]
    frontier.sort(key=lambda r: _primary_frontier_score(r, score_mode=score_mode))
    return frontier


def _filtered_frontier(  # noqa: ANN001
    results,
    *,
    top: int,
    score_mode: str,
    min_year: int | None,
    max_year: int | None,
    types: set[str] | None,
):
    frontier = _matching_frontier(
        results,
        score_mode=score_mode,
        min_year=min_year,
        max_year=max_year,
        types=types,
    )
    return frontier[:top]


def _effect_overlaps_residual(
    resolver_eids: tuple[str, ...],
    *,
    only_in_replayed: set[str],
    only_in_oracle: set[str],
) -> bool:
    residual = {eid.lower() for eid in only_in_replayed} | {
        eid.lower() for eid in only_in_oracle
    }
    for resolver_eid in resolver_eids:
        norm = resolver_eid.lower()
        if not norm:
            continue
        if norm in residual:
            return True
        candidate_prefix = norm + "-"
        if any(other.startswith(candidate_prefix) for other in residual):
            return True
        if any(norm.startswith(other + "-") for other in residual):
            return True
    return False


def _eid_branch_root(eid: str) -> str:
    norm = str(eid or "").strip().lower()
    if not norm:
        return ""
    if norm == "schedule":
        return norm
    if norm.startswith("schedule-"):
        parts = norm.split("-")
        if len(parts) >= 2 and parts[1] not in {
            "part",
            "chapter",
            "crossheading",
            "paragraph",
            "subparagraph",
            "item",
            "point",
        }:
            return "-".join(parts[:2])
        return "schedule"
    for prefix in (
        "section-",
        "article-",
        "rule-",
        "regulation-",
        "chapter-",
        "part-",
        "division-",
        "recital-",
    ):
        if norm.startswith(prefix):
            parts = norm.split("-")
            if len(parts) >= 2:
                return "-".join(parts[:2])
    if norm.startswith(("crossheading-", "p1group")):
        return norm
    return norm


def _is_malformed_residual_root(root: str) -> bool:
    norm = str(root or "").strip().lower()
    return bool(norm) and norm[-1] in {".", ",", ";", ":"}


def _collect_residual_roots(
    *,
    only_in_replayed: set[str],
    only_in_oracle: set[str],
) -> set[str]:
    roots: set[str] = set()
    for eid in only_in_replayed | only_in_oracle:
        root = _eid_branch_root(eid)
        if root:
            roots.add(root)
    return roots


def _collect_malformed_residual_roots(
    *,
    only_in_replayed: set[str],
    only_in_oracle: set[str],
) -> set[str]:
    return {
        root
        for root in _collect_residual_roots(
            only_in_replayed=only_in_replayed,
            only_in_oracle=only_in_oracle,
        )
        if _is_malformed_residual_root(root)
    }


def _collect_residual_root_sides(
    *,
    only_in_replayed: set[str],
    only_in_oracle: set[str],
) -> tuple[set[str], set[str]]:
    replayed_roots = {_eid_branch_root(eid) for eid in only_in_replayed}
    oracle_roots = {_eid_branch_root(eid) for eid in only_in_oracle}
    replayed_roots.discard("")
    oracle_roots.discard("")
    return replayed_roots, oracle_roots


def _candidate_root_hits(
    resolver_eids: tuple[str, ...],
    *,
    residual_roots: set[str],
) -> set[str]:
    hits: set[str] = set()
    for resolver_eid in resolver_eids:
        root = _eid_branch_root(resolver_eid)
        if root and root in residual_roots:
            hits.add(root)
    return hits


def _candidate_overlapping_root_hits(
    resolver_eids: tuple[str, ...],
    *,
    residual_roots: set[str],
    only_in_replayed: set[str],
    only_in_oracle: set[str],
) -> set[str]:
    hits: set[str] = set()
    for resolver_eid in resolver_eids:
        if not _effect_overlaps_residual(
            (resolver_eid,),
            only_in_replayed=only_in_replayed,
            only_in_oracle=only_in_oracle,
        ):
            continue
        hits.update(_candidate_root_hits((resolver_eid,), residual_roots=residual_roots))
    return hits


def _frontier_status(
    *,
    candidate_count: int,
    residual_candidate_count: int,
    residual_root_count: int = 0,
    defeated_residual_root_count: int = 0,
    malformed_residual_root_count: int = 0,
) -> str:
    if residual_candidate_count > 0:
        return "real residual frontier"
    if residual_root_count > 0 and malformed_residual_root_count == residual_root_count:
        return "malformed residual roots deferred"
    if (
        residual_root_count > 0
        and malformed_residual_root_count > 0
        and defeated_residual_root_count + malformed_residual_root_count == residual_root_count
    ):
        return "residual branches include malformed roots"
    if residual_root_count > 0 and defeated_residual_root_count == residual_root_count:
        return "residual branches defeated by no candidate overlap"
    if candidate_count == 0:
        return "classification-heavy"
    return "candidate-clean after residual overlap"


def _budget_aware_frontier_status(
    *,
    candidate_count: int,
    residual_candidate_count: int,
    effect_inspection_truncated: bool,
    residual_root_count: int = 0,
    defeated_residual_root_count: int = 0,
    malformed_residual_root_count: int = 0,
) -> str:
    if effect_inspection_truncated and residual_candidate_count == 0:
        return "effect inspection budget truncated"
    return _frontier_status(
        candidate_count=candidate_count,
        residual_candidate_count=residual_candidate_count,
        residual_root_count=residual_root_count,
        defeated_residual_root_count=defeated_residual_root_count,
        malformed_residual_root_count=malformed_residual_root_count,
    )


def _count_map_jsonable(counts: Mapping[str, int]) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counts.items())}


def _limited_count_map_jsonable(
    counts: Mapping[str, int],
    *,
    limit: int | None,
) -> tuple[dict[str, int], int]:
    if limit is None:
        return _count_map_jsonable(counts), 0
    items = sorted(
        ((str(key), int(value)) for key, value in counts.items()),
        key=lambda item: (-item[1], item[0]),
    )
    emitted = items[:limit]
    return dict(emitted), max(0, len(items) - len(emitted))


def _limit_summary_count_maps(
    summary: dict[str, Any],
    *,
    limit: int | None,
) -> dict[str, Any]:
    if limit is None:
        return summary
    omissions: dict[str, int] = {}
    limited = dict(summary)
    for key, value in tuple(summary.items()):
        if not key.endswith("_counts") or not isinstance(value, Mapping):
            continue
        limited_map, omitted = _limited_count_map_jsonable(value, limit=limit)
        limited[key] = limited_map
        if omitted:
            omissions[key] = omitted
    if omissions:
        limited["summary_count_map_omissions"] = dict(sorted(omissions.items()))
    return limited


def _limit_row_count_maps(
    row: Mapping[str, Any],
    *,
    limit: int | None,
) -> dict[str, Any]:
    if limit is None:
        return dict(row)
    omissions: dict[str, int] = {}
    limited = dict(row)
    for key, value in tuple(row.items()):
        if not str(key).endswith("_counts") or not isinstance(value, Mapping):
            continue
        limited_map, omitted = _limited_count_map_jsonable(value, limit=limit)
        limited[str(key)] = limited_map
        if omitted:
            omissions[str(key)] = omitted
    if omissions:
        limited["row_count_map_omissions"] = dict(sorted(omissions.items()))
    return limited


def _count_map_from_object(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): count for key, count in sorted(value.items()) if isinstance(count, int)}


def _load_saved_bench_run(
    load_run: object,
    label: str,
    *,
    include_diagnostics: bool,
) -> list[Any]:
    signature = inspect.signature(load_run)
    supports_include_diagnostics = (
        "include_diagnostics" in signature.parameters
        or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
    )
    if supports_include_diagnostics:
        return cast(Any, load_run)(label, include_diagnostics=include_diagnostics)
    return cast(Any, load_run)(label)


def _short_replay_adjudication_sample_value(value: object, *, limit: int = 120) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _observation_rows_from_object(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, tuple | list):
        return []
    rows: list[dict[str, Any]] = []
    for row in value:
        if isinstance(row, Mapping):
            rows.append(dict(cast(Mapping[str, Any], row)))
    return rows


def _saved_bench_diagnostic_rows_from_result(result) -> tuple[dict[str, Any], ...]:  # noqa: ANN001
    def _effect_diagnostic_lane(record: dict[str, Any]) -> str:
        from lawvm.uk_legislation.source_state import is_uk_affecting_act_xml_source_observation

        rule_id = str(record.get("rule_id") or "")
        if is_uk_affecting_act_xml_source_observation(record):
            return "source_acquisition"
        if rule_id == "uk_effect_source_pathology_classified":
            return "effect_source_pathology"
        if rule_id == "uk_manual_compile_frontier_classified":
            return "manual_compile_frontier"
        return "effect_diagnostic"

    def _row_blocking(lane: str, record: dict[str, Any]) -> bool:
        if (
            lane in {"effect_feed", "source_acquisition", "effect_source_pathology", "manual_compile_frontier"}
            or "blocking" in record
            or record.get("strict_disposition")
        ):
            return is_blocking_compile_record(record)
        return False

    leading_lane_attrs: tuple[tuple[str, str], ...] = (
        ("source_parse", "source_parse_observations"),
        ("effect_feed", "effect_feed_observations"),
    )
    trailing_lane_attrs: tuple[tuple[str, str], ...] = (
        ("authority", "uk_authority_observations"),
        ("lowering", "lowering_rejections"),
        ("replay_adjudication", "replay_adjudications"),
        ("bench_exception", "bench_exception_observations"),
    )
    rows: list[dict[str, Any]] = []
    for lane, attr in leading_lane_attrs:
        records = _observation_rows_from_object(getattr(result, attr, ()))
        for index, record in enumerate(records):
            rule_id = str(record.get("rule_id") or record.get("kind") or "")
            rows.append(
                {
                    "diagnostic_lane": lane,
                    "index": index,
                    "rule_id": rule_id,
                    "blocking": _row_blocking(lane, record),
                    "record": record,
                }
            )
    effect_lane_indexes: Counter[str] = Counter()
    for record in _observation_rows_from_object(getattr(result, "effect_diagnostics", ())):
        lane = _effect_diagnostic_lane(record)
        index = effect_lane_indexes[lane]
        effect_lane_indexes[lane] += 1
        rule_id = str(record.get("rule_id") or record.get("kind") or "")
        rows.append(
            {
                "diagnostic_lane": lane,
                "index": index,
                "rule_id": rule_id,
                "blocking": _row_blocking(lane, record),
                "record": record,
            }
        )
    for lane, attr in trailing_lane_attrs:
        records = _observation_rows_from_object(getattr(result, attr, ()))
        for index, record in enumerate(records):
            rule_id = str(record.get("rule_id") or record.get("kind") or "")
            rows.append(
                {
                    "diagnostic_lane": lane,
                    "index": index,
                    "rule_id": rule_id,
                    "blocking": _row_blocking(lane, record),
                    "record": record,
                }
            )
    return tuple(rows)


def _replay_adjudication_records_from_result(result) -> tuple[dict[str, Any], ...]:  # noqa: ANN001
    records: list[dict[str, Any]] = []
    for record in _observation_rows_from_object(getattr(result, "replay_adjudications", ())):
        records.append(record)
    return tuple(records)


def _replay_adjudication_bucket_counts_from_kind_counts(
    kind_counts: Mapping[str, int],
) -> dict[str, int]:
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_replay_adjudication_bucket,
    )

    bucket_counts: Counter[str] = Counter()
    for kind, count in kind_counts.items():
        bucket_counts[classify_uk_replay_adjudication_bucket(str(kind))] += int(count)
    return _count_map_jsonable(bucket_counts)


def _result_has_replay_adjudication_kind(
    result,  # noqa: ANN001
    *,
    kinds: set[str],
) -> bool:
    if not kinds:
        return True
    for record in _replay_adjudication_records_from_result(result):
        if str(record.get("kind") or "") in kinds:
            return True
    kind_counts = _count_map_from_object(getattr(result, "replay_adjudication_kind_counts", {}))
    return any(kind in kind_counts for kind in kinds)


def _replay_adjudication_kinds_from_args(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        raw_items = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw_items = tuple(str(item) for item in value)
    else:
        raw_items = (str(value),)
    kinds: set[str] = set()
    for item in raw_items:
        kind = item.strip()
        if not kind:
            continue
        kinds.update(_REPLAY_ADJUDICATION_KIND_ALIASES.get(kind, (kind,)))
    return kinds


def _replay_adjudication_sample_rows(
    result,  # noqa: ANN001
    *,
    kinds: set[str],
    limit: int,
) -> tuple[dict[str, Any], ...]:
    if limit <= 0:
        return ()
    rows: list[dict[str, Any]] = []
    for record in _replay_adjudication_records_from_result(result):
        kind = str(record.get("kind") or "unknown")
        if kinds and kind not in kinds:
            continue
        detail = record.get("detail")
        if not isinstance(detail, Mapping):
            detail = {}
        sample = {
            "kind": kind,
            "message": _short_replay_adjudication_sample_value(record.get("message")),
            "source_statute": str(record.get("source_statute") or ""),
            "op_id": str(record.get("op_id") or ""),
            "target": _short_replay_adjudication_sample_value(detail.get("target")),
            "target_granularity": str(detail.get("target_granularity") or ""),
            "text_match": _short_replay_adjudication_sample_value(detail.get("text_match")),
            "replacement_text": _short_replay_adjudication_sample_value(
                detail.get("replacement_text")
            ),
            "source_shape": _short_replay_adjudication_sample_value(
                detail.get("source_shape")
            ),
        }
        for source_key, sample_key in (
            ("kind", "duplicate_kind"),
            ("path", "path"),
            ("root", "root"),
            ("left", "left"),
            ("right", "right"),
            ("excerpt", "excerpt"),
            ("shared_token_count", "shared_token_count"),
        ):
            if detail.get(source_key) not in (None, ""):
                sample[sample_key] = _short_replay_adjudication_sample_value(
                    detail.get(source_key)
                )
        if not sample.get("root"):
            path = str(sample.get("path") or "")
            if path == "body" or path.startswith("body/"):
                sample["root"] = "body"
        rows.append(sample)
        if len(rows) >= limit:
            break
    return tuple(rows)


def _replay_adjudication_sample_omitted(
    result,  # noqa: ANN001
    *,
    kinds: set[str],
    sampled_count: int,
) -> int:
    counts = _count_map_from_object(getattr(result, "replay_adjudication_kind_counts", {}))
    if counts:
        if kinds:
            total = sum(int(counts.get(kind, 0)) for kind in kinds)
        else:
            total = sum(counts.values())
        return max(0, total - sampled_count)
    total = 0
    for record in _replay_adjudication_records_from_result(result):
        if not kinds or str(record.get("kind") or "") in kinds:
            total += 1
    return max(0, total - sampled_count)


def _replay_adjudication_work_item_id(
    *,
    label: str,
    statute_id: str,
    record: Mapping[str, Any],
) -> str:
    detail = record.get("detail")
    detail_payload = detail if isinstance(detail, Mapping) else {}
    parts = (
        label,
        statute_id,
        str(record.get("kind") or ""),
        str(record.get("source_statute") or ""),
        str(record.get("op_id") or ""),
        str(record.get("message") or ""),
        json.dumps(dict(detail_payload), ensure_ascii=False, sort_keys=True),
    )
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"uk-replay-adjudication-{digest}"


def _replay_adjudication_evidence_row_jsonable(
    result,  # noqa: ANN001
    *,
    label: str,
    record: Mapping[str, Any],
    score_mode: str,
) -> dict[str, Any]:
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_replay_adjudication_bucket,
    )

    kind = str(record.get("kind") or "unknown")
    detail = record.get("detail")
    detail_payload = dict(detail) if isinstance(detail, Mapping) else {}
    return {
        "schema": "lawvm.uk_replay_adjudication_frontier.v1",
        "rule_id": "uk_replay_adjudication_frontier_workqueue",
        "family": "replay_adjudication_frontier",
        "phase": "replay_adjudication",
        "jurisdiction": "uk",
        "work_item_kind": "replay_adjudication_review",
        "claim_kind": "replay_adjudication",
        "claim_status": "unresolved_work_item",
        "validator_status": "not_validated",
        "work_item_id": _replay_adjudication_work_item_id(
            label=label,
            statute_id=str(result.statute_id),
            record=record,
        ),
        "bench_label": label,
        "statute_id": str(result.statute_id),
        "score_mode": score_mode,
        "frontier_score": _primary_frontier_score(result, score_mode=score_mode),
        "raw_score": float(getattr(result, "score", -1.0)),
        "replay_score": float(getattr(result, "replay_score", -1.0)),
        "commencement_score": float(getattr(result, "commencement_score", -1.0)),
        "replay_commencement_score": float(
            getattr(result, "replay_commencement_score", -1.0)
        ),
        "comparison_class": _effective_comparison_class(result),
        "core_benchmark": _effective_core_benchmark(result),
        "adjudication_kind": kind,
        "adjudication_bucket": classify_uk_replay_adjudication_bucket(kind),
        "message": _short_replay_adjudication_sample_value(record.get("message"), limit=500),
        "source_statute": str(record.get("source_statute") or ""),
        "op_id": str(record.get("op_id") or ""),
        "detail": detail_payload,
        "blocking": is_blocking_compile_record(
            {"rule_id": kind, **detail_payload, "kind": kind}
        ),
        "strict_disposition": str(detail_payload.get("strict_disposition") or "record"),
        "quirks_disposition": str(detail_payload.get("quirks_disposition") or "record"),
        "uk_replay_regime": _uk_replay_regime_kwargs_from_bench_row(result),
        "uk_replay_regime_claim": _uk_replay_regime_claim_from_bench_row(result),
        "uk_residual_claim": _uk_residual_claim_from_bench_row(result),
        "enacted_source": {
            "status": str(getattr(result, "enacted_source_status", "") or "unknown"),
            "size": int(getattr(result, "enacted_source_size", 0) or 0),
            "sha256": str(getattr(result, "enacted_source_sha256", "") or ""),
            "url": str(getattr(result, "enacted_source_url", "") or ""),
        },
        "oracle_source": {
            "status": str(getattr(result, "oracle_source_status", "") or "unknown"),
            "size": int(getattr(result, "oracle_source_size", 0) or 0),
            "sha256": str(getattr(result, "oracle_source_sha256", "") or ""),
            "url": str(getattr(result, "oracle_source_url", "") or ""),
        },
    }


def _replay_adjudication_evidence_rows(
    results: Sequence[object],
    *,
    label: str,
    kinds: set[str],
    score_mode: str,
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for result in results:
        for record in _replay_adjudication_records_from_result(result):
            if kinds and str(record.get("kind") or "") not in kinds:
                continue
            rows.append(
                _replay_adjudication_evidence_row_jsonable(
                    result,
                    label=label,
                    record=record,
                    score_mode=score_mode,
                )
            )
    return tuple(rows)


def _replay_adjudication_fields_from_result(
    result,  # noqa: ANN001
    *,
    kinds: set[str],
    sample_limit: int,
) -> dict[str, Any]:
    samples = _replay_adjudication_sample_rows(
        result,
        kinds=kinds,
        limit=sample_limit,
    )
    kind_counts = _count_map_from_object(
        getattr(result, "replay_adjudication_kind_counts", {})
    )
    return {
        "replay_adjudication_count": int(
            getattr(result, "replay_adjudication_count", 0) or 0
        ),
        "replay_adjudication_kind_counts": kind_counts,
        "replay_adjudication_bucket_counts": _replay_adjudication_bucket_counts_from_kind_counts(
            kind_counts
        ),
        "replay_adjudication_samples": list(samples),
        "replay_adjudication_samples_omitted": _replay_adjudication_sample_omitted(
            result,
            kinds=kinds,
            sampled_count=len(samples),
        ),
    }


def _saved_effect_feed_observation_rows_from_result(result) -> tuple[dict[str, Any], ...]:  # noqa: ANN001
    return tuple(
        dict(row)
        for row in _observation_rows_from_object(getattr(result, "effect_feed_observations", ()))
    )


def _format_count_map(value: object) -> str:
    if not isinstance(value, Mapping):
        return "{}"
    counts = {str(key): count for key, count in value.items()}
    if not counts:
        return "{}"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))


def _rejection_rule_counts(rows: tuple[dict[str, Any], ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts[str(row.get("rule_id") or "unknown")] += 1
    return _count_map_jsonable(counts)


def _blocking_rows(rows: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(row for row in rows if is_blocking_compile_record(row))


def _effect_source_pathology_rows(
    rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        dict(row)
        for row in rows
        if str(row.get("rule_id") or "") == "uk_effect_source_pathology_classified"
    )


def _source_acquisition_rows(
    rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    from lawvm.uk_legislation.source_state import is_uk_affecting_act_xml_source_observation

    return tuple(
        dict(row)
        for row in rows
        if is_uk_affecting_act_xml_source_observation(row)
    )


def _merged_rejection_rule_counts(*row_groups: tuple[dict[str, Any], ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for rows in row_groups:
        counts.update(_rejection_rule_counts(rows))
    return _count_map_jsonable(counts)


def _residual_execution_exception_observation(
    *,
    statute_id: str,
    phase: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "rule_id": f"uk_residual_{phase}_exception_recorded",
        "family": "tooling_diagnostic",
        "phase": phase,
        "statute_id": statute_id,
        "reason": f"UK candidate residual analysis {phase} failed; row retained as typed evidence.",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }


def _uk_candidates_filters_jsonable(
    *,
    top: int,
    score_mode: str,
    residual_only: bool,
    fast: bool,
    effect_budget: int | None,
    residual_budget: int | None,
    min_year: int | None,
    max_year: int | None,
    types: set[str] | None,
    replay_adjudication_kinds: set[str] | None = None,
    replay_adjudication_sample_limit: int = 5,
    manual_compile_evidence_statuses: set[str] | None = None,
    claim_template_status: str = "",
    compact_json: bool = False,
    summary_count_limit: int | None = None,
    row_count_limit: int | None = None,
) -> dict[str, Any]:
    return {
        "top": top,
        "score_mode": score_mode,
        "residual_only": residual_only,
        "fast": fast,
        "effect_budget": effect_budget,
        "residual_budget": residual_budget,
        "min_year": min_year,
        "max_year": max_year,
        "types": sorted(types) if types is not None else None,
        "replay_adjudication_kinds": (
            sorted(replay_adjudication_kinds) if replay_adjudication_kinds else []
        ),
        "replay_adjudication_sample_limit": replay_adjudication_sample_limit,
        "manual_compile_evidence_statuses": (
            sorted(manual_compile_evidence_statuses)
            if manual_compile_evidence_statuses is not None
            else list(_DEFAULT_MANUAL_COMPILE_EVIDENCE_STATUSES)
        ),
        "claim_template_status": claim_template_status,
        "compact_json": compact_json,
        "summary_count_limit": summary_count_limit,
        "row_count_limit": row_count_limit,
    }


def _manual_compile_evidence_statuses_from_args(value: object) -> set[str]:
    if value is None:
        return set(_DEFAULT_MANUAL_COMPILE_EVIDENCE_STATUSES)
    if isinstance(value, str):
        raw_items = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw_items = tuple(str(item) for item in value)
    else:
        raw_items = (str(value),)
    statuses = {item.strip() for item in raw_items if item.strip()}
    if not statuses:
        return set(_DEFAULT_MANUAL_COMPILE_EVIDENCE_STATUSES)
    expanded: set[str] = set()
    for status in statuses:
        if status in _ACTIONABLE_MANUAL_COMPILE_EVIDENCE_ALIASES:
            expanded.update(_ACTIONABLE_MANUAL_COMPILE_EVIDENCE_STATUSES)
        else:
            expanded.add(status)
    statuses = expanded
    return statuses


_COMPACT_ROW_OMIT_KEYS = frozenset(
    {
        "bench_exception_observations",
        "saved_bench_diagnostics",
        "effect_feed_parse_rejections",
        "effect_feed_observations",
        "effect_selection_observations",
        "effect_selection_rejections",
        "residual_compile_observations",
        "residual_compile_rejections",
    }
)


def _compact_uk_candidate_row_jsonable(row: Mapping[str, Any]) -> dict[str, Any]:
    """Drop bulky diagnostic arrays while preserving row-level counts and samples."""
    return {
        str(key): value
        for key, value in row.items()
        if str(key) not in _COMPACT_ROW_OMIT_KEYS
    }


def _uk_candidate_row_jsonable(  # noqa: PLR0913
    result,  # noqa: ANN001
    *,
    score_mode: str,
    source_counts: Mapping[str, int],
    compare_counts: Mapping[str, int],
    candidate_source_counts: Mapping[str, int],
    candidate_compare_counts: Mapping[str, int],
    non_candidate_source_counts: Mapping[str, int],
    non_candidate_compare_counts: Mapping[str, int],
    lowering_rejection_rule_counts: Mapping[str, int],
    blocking_lowering_rejection_rule_counts: Mapping[str, int],
    source_acquisition_observation_rule_counts: Mapping[str, int],
    rows_with_source_acquisition_observations: int,
    source_acquisition_rejection_rule_counts: Mapping[str, int],
    rows_with_source_acquisition_rejections: int,
    rows_with_blocking_lowering_rejections: int,
    inspected_effect_count: int,
    available_replay_applicable_effect_count: int,
    available_applied_effect_count: int,
    effect_inspection_truncated: bool,
    residual_analysis_skipped: bool,
    residual_analysis_unavailable: bool = False,
    residual_analysis_unavailable_reason: str = "",
    residual_analysis_enacted_missing: bool = False,
    residual_analysis_oracle_missing: bool = False,
    candidate_count: int,
    candidate_ops: int,
    residual_candidate_count: int,
    residual_candidate_ops: int,
    residual_roots: set[str],
    replayed_residual_roots: set[str],
    oracle_residual_roots: set[str],
    malformed_residual_roots: set[str],
    residual_root_hits: set[str],
    residual_root_hit_counts: Mapping[str, int] | None = None,
    residual_root_side_counts: Mapping[str, int] | None = None,
    residual_candidate_source_pathology_counts: Mapping[str, int] | None = None,
    residual_candidate_compare_shape_counts: Mapping[str, int] | None = None,
    residual_candidate_structural_counts: Mapping[str, int] | None = None,
    residual_candidate_action_counts: Mapping[str, int] | None = None,
    residual_candidate_target_presence_counts: Mapping[str, int] | None = None,
    residual_candidate_target_presence_action_counts: Mapping[str, int] | None = None,
    residual_candidate_manual_compile_rule_counts: Mapping[str, int] | None = None,
    residual_candidate_root_samples: tuple[dict[str, Any], ...] = (),
    residual_candidate_root_samples_omitted: int = 0,
    defeated_residual_roots: set[str],
    status: str,
    effect_feed_parse_rejections: tuple[dict[str, Any], ...] = (),
    effect_selection_observations: tuple[dict[str, Any], ...] = (),
    residual_effect_feed_parse_rejections: tuple[dict[str, Any], ...] = (),
    residual_effect_source_pathology_observations: tuple[dict[str, Any], ...] = (),
    residual_source_acquisition_rejections: tuple[dict[str, Any], ...] = (),
    residual_lowering_rejections: tuple[dict[str, Any], ...] = (),
    residual_authority_rejections: tuple[dict[str, Any], ...] = (),
    residual_execution_observations: tuple[dict[str, Any], ...] = (),
    residual_candidate_samples: tuple[dict[str, Any], ...] = (),
    residual_candidate_samples_omitted: int = 0,
    manual_compile_status_counts: Mapping[str, int] | None = None,
    manual_compile_rule_counts: Mapping[str, int] | None = None,
    suggested_claim_template_status_counts: Mapping[str, int] | None = None,
    lowering_observation_rule_counts: Mapping[str, int] | None = None,
    rows_with_lowering_observations: int = 0,
    replay_adjudication_kinds: set[str] | None = None,
    replay_adjudication_sample_limit: int = 5,
) -> dict[str, Any]:
    score = _primary_frontier_score(result, score_mode=score_mode)
    parse_observation_rows = tuple(dict(item) for item in effect_feed_parse_rejections)
    parse_rejection_rows = _blocking_rows(parse_observation_rows)
    selection_observation_rows = tuple(dict(item) for item in effect_selection_observations)
    selection_rejection_rows = _blocking_rows(selection_observation_rows)
    residual_feed_rows = tuple(dict(item) for item in residual_effect_feed_parse_rejections)
    residual_source_pathology_rows = tuple(
        dict(item) for item in residual_effect_source_pathology_observations
    )
    residual_source_acquisition_rows = tuple(
        dict(item) for item in residual_source_acquisition_rejections
    )
    residual_lowering_rows = tuple(dict(item) for item in residual_lowering_rejections)
    residual_authority_rows = tuple(dict(item) for item in residual_authority_rejections)
    residual_execution_rows = tuple(dict(item) for item in residual_execution_observations)
    residual_observation_rows = (
        residual_feed_rows
        + residual_source_pathology_rows
        + residual_source_acquisition_rows
        + residual_lowering_rows
        + residual_authority_rows
        + residual_execution_rows
    )
    residual_feed_rejection_rows = _blocking_rows(residual_feed_rows)
    residual_source_pathology_rejection_rows = _blocking_rows(residual_source_pathology_rows)
    residual_source_acquisition_rejection_rows = _blocking_rows(residual_source_acquisition_rows)
    residual_lowering_rejection_rows = _blocking_rows(residual_lowering_rows)
    residual_authority_rejection_rows = _blocking_rows(residual_authority_rows)
    residual_execution_rejection_rows = _blocking_rows(residual_execution_rows)
    sample_rows = tuple(dict(item) for item in residual_candidate_samples)
    residual_compile_rejection_count = (
        len(residual_feed_rejection_rows)
        + len(residual_source_pathology_rejection_rows)
        + len(residual_source_acquisition_rejection_rows)
        + len(residual_lowering_rejection_rows)
        + len(residual_authority_rejection_rows)
        + len(residual_execution_rejection_rows)
    )
    saved_bench_diagnostic_rows = _saved_bench_diagnostic_rows_from_result(result)
    saved_bench_diagnostic_lane_counts = _count_map_jsonable(
        Counter(str(row.get("diagnostic_lane") or "unknown") for row in saved_bench_diagnostic_rows)
    )
    replay_adjudication_fields = _replay_adjudication_fields_from_result(
        result,
        kinds=replay_adjudication_kinds or set(),
        sample_limit=replay_adjudication_sample_limit,
    )
    return {
        "statute_id": result.statute_id,
        "score_mode": score_mode,
        "frontier_score": score,
        "raw_score": float(getattr(result, "score", -1.0)),
        "replay_score": float(getattr(result, "replay_score", -1.0)),
        "commencement_score": float(getattr(result, "commencement_score", -1.0)),
        "replay_commencement_score": float(getattr(result, "replay_commencement_score", -1.0)),
        "effect_count": int(getattr(result, "n_effects", 0) or 0),
        "effect_row_count": int(getattr(result, "n_effect_rows", getattr(result, "n_effects", 0)) or 0),
        "effect_feed_page_count": int(
            getattr(
                result,
                "n_effect_feed_pages",
                getattr(result, "n_effects", 0),
            )
            or 0
        ),
        "effect_feed_count_error": str(getattr(result, "effect_feed_count_error", "") or ""),
        "enacted_source_status": str(getattr(result, "enacted_source_status", "") or "unknown"),
        "oracle_source_status": str(getattr(result, "oracle_source_status", "") or "unknown"),
        "enacted_source_size": int(getattr(result, "enacted_source_size", 0) or 0),
        "oracle_source_size": int(getattr(result, "oracle_source_size", 0) or 0),
        "enacted_source_sha256": str(getattr(result, "enacted_source_sha256", "") or ""),
        "oracle_source_sha256": str(getattr(result, "oracle_source_sha256", "") or ""),
        "enacted_source_url": str(getattr(result, "enacted_source_url", "") or ""),
        "oracle_source_url": str(getattr(result, "oracle_source_url", "") or ""),
        "source_parse_rejection_count": int(
            getattr(result, "source_parse_rejection_count", 0) or 0
        ),
        "source_parse_rejection_rule_counts": _count_map_from_object(
            getattr(result, "source_parse_rejection_rule_counts", {})
        ),
        "source_parse_observation_count": int(
            getattr(result, "source_parse_observation_count", 0) or 0
        ),
        "source_parse_observation_rule_counts": _count_map_from_object(
            getattr(result, "source_parse_observation_rule_counts", {})
        ),
        "bench_exception_count": int(getattr(result, "bench_exception_count", 0) or 0),
        "bench_exception_rule_counts": _count_map_from_object(
            getattr(result, "bench_exception_rule_counts", {})
        ),
        "bench_exception_observations": _observation_rows_from_object(
            getattr(result, "bench_exception_observations", ())
        ),
        "saved_bench_diagnostic_count": len(saved_bench_diagnostic_rows),
        "saved_bench_diagnostic_rule_counts": _rejection_rule_counts(
            saved_bench_diagnostic_rows
        ),
        "saved_bench_diagnostic_lane_counts": saved_bench_diagnostic_lane_counts,
        "saved_bench_diagnostics": list(saved_bench_diagnostic_rows),
        **replay_adjudication_fields,
        "uk_replay_regime": _uk_replay_regime_kwargs_from_bench_row(result),
        "uk_replay_regime_claim": _uk_replay_regime_claim_from_bench_row(result),
        "uk_residual_claim": _uk_residual_claim_from_bench_row(result),
        "bench_authority_observation_count": int(
            getattr(result, "uk_authority_observation_count", 0) or 0
        ),
        "bench_authority_observation_rule_counts": _count_map_from_object(
            getattr(result, "uk_authority_observation_rule_counts", {})
        ),
        "bench_authority_rejection_count": int(
            getattr(result, "uk_authority_rejection_count", 0) or 0
        ),
        "bench_authority_rejection_rule_counts": _count_map_from_object(
            getattr(result, "uk_authority_rejection_rule_counts", {})
        ),
        "bench_effect_source_pathology_counts": _count_map_from_object(
            getattr(result, "effect_source_pathology_counts", {})
        ),
        "bench_manual_compile_status_counts": _count_map_from_object(
            getattr(result, "manual_compile_status_counts", {})
        ),
        "bench_manual_compile_rule_counts": _count_map_from_object(
            getattr(result, "manual_compile_rule_counts", {})
        ),
        "bench_source_acquisition_rejection_count": int(
            getattr(result, "source_acquisition_rejection_count", 0) or 0
        ),
        "bench_source_acquisition_rejection_rule_counts": _count_map_from_object(
            getattr(result, "source_acquisition_rejection_rule_counts", {})
        ),
        "inspected_effect_count": inspected_effect_count,
        "inspected_replay_applicable_effect_count": inspected_effect_count,
        "available_replay_applicable_effect_count": available_replay_applicable_effect_count,
        "available_applied_effect_count": available_applied_effect_count,
        "effect_inspection_truncated": effect_inspection_truncated,
        "residual_analysis_skipped": residual_analysis_skipped,
        "residual_analysis_unavailable": residual_analysis_unavailable,
        "residual_analysis_unavailable_reason": residual_analysis_unavailable_reason,
        "residual_analysis_enacted_missing": residual_analysis_enacted_missing,
        "residual_analysis_oracle_missing": residual_analysis_oracle_missing,
        "comparison_class": _effective_comparison_class(result),
        "core_benchmark": _effective_core_benchmark(result),
        "source_counts": _count_map_jsonable(source_counts),
        "compare_counts": _count_map_jsonable(compare_counts),
        "candidate_source_counts": _count_map_jsonable(candidate_source_counts),
        "candidate_compare_counts": _count_map_jsonable(candidate_compare_counts),
        "non_candidate_source_counts": _count_map_jsonable(non_candidate_source_counts),
        "non_candidate_compare_counts": _count_map_jsonable(non_candidate_compare_counts),
        "manual_compile_status_counts": _count_map_jsonable(manual_compile_status_counts or {}),
        "manual_compile_rule_counts": _count_map_jsonable(manual_compile_rule_counts or {}),
        "suggested_claim_template_status_counts": _count_map_jsonable(
            suggested_claim_template_status_counts or {}
        ),
        "lowering_observation_rule_counts": _count_map_jsonable(
            lowering_observation_rule_counts
            if lowering_observation_rule_counts is not None
            else lowering_rejection_rule_counts
        ),
        "rows_with_lowering_observations": rows_with_lowering_observations,
        "lowering_rejection_rule_counts": _count_map_jsonable(lowering_rejection_rule_counts),
        "blocking_lowering_rejection_rule_counts": _count_map_jsonable(blocking_lowering_rejection_rule_counts),
        "source_acquisition_observation_rule_counts": _count_map_jsonable(
            source_acquisition_observation_rule_counts
        ),
        "rows_with_source_acquisition_observations": rows_with_source_acquisition_observations,
        "source_acquisition_rejection_rule_counts": _count_map_jsonable(source_acquisition_rejection_rule_counts),
        "rows_with_source_acquisition_rejections": rows_with_source_acquisition_rejections,
        "rows_with_blocking_lowering_rejections": rows_with_blocking_lowering_rejections,
        "effect_feed_parse_rejection_count": len(parse_rejection_rows),
        "effect_feed_parse_rejection_rule_counts": _rejection_rule_counts(parse_rejection_rows),
        "effect_feed_parse_rejections": list(parse_rejection_rows),
        "effect_feed_observation_count": len(parse_observation_rows),
        "effect_feed_observation_rule_counts": _rejection_rule_counts(parse_observation_rows),
        "effect_feed_observations": list(parse_observation_rows),
        "effect_selection_observation_count": len(selection_observation_rows),
        "effect_selection_observation_rule_counts": _rejection_rule_counts(selection_observation_rows),
        "effect_selection_observations": list(selection_observation_rows),
        "effect_selection_rejection_count": len(selection_rejection_rows),
        "effect_selection_rejection_rule_counts": _rejection_rule_counts(selection_rejection_rows),
        "effect_selection_rejections": list(selection_rejection_rows),
        "residual_compile_observation_count": len(residual_observation_rows),
        "residual_compile_observation_rule_counts": _merged_rejection_rule_counts(
            residual_feed_rows,
            residual_source_pathology_rows,
            residual_source_acquisition_rows,
            residual_lowering_rows,
            residual_authority_rows,
            residual_execution_rows,
        ),
        "residual_compile_observations": {
            "effect_feed_parse": list(residual_feed_rows),
            "effect_source_pathology": list(residual_source_pathology_rows),
            "source_acquisition": list(residual_source_acquisition_rows),
            "lowering": list(residual_lowering_rows),
            "authority": list(residual_authority_rows),
            "execution": list(residual_execution_rows),
        },
        "residual_compile_rejection_count": residual_compile_rejection_count,
        "residual_compile_rejection_rule_counts": _merged_rejection_rule_counts(
            residual_feed_rejection_rows,
            residual_source_pathology_rejection_rows,
            residual_source_acquisition_rejection_rows,
            residual_lowering_rejection_rows,
            residual_authority_rejection_rows,
            residual_execution_rejection_rows,
        ),
        "residual_compile_rejections": {
            "effect_feed_parse": list(residual_feed_rejection_rows),
            "effect_source_pathology": list(residual_source_pathology_rejection_rows),
            "source_acquisition": list(residual_source_acquisition_rejection_rows),
            "lowering": list(residual_lowering_rejection_rows),
            "authority": list(residual_authority_rejection_rows),
            "execution": list(residual_execution_rejection_rows),
        },
        "candidate_effect_count": candidate_count,
        "candidate_op_count": candidate_ops,
        "residual_candidate_effect_count": residual_candidate_count,
        "residual_candidate_op_count": residual_candidate_ops,
        "residual_candidate_root_hit_counts": _count_map_jsonable(
            residual_root_hit_counts or {}
        ),
        "residual_candidate_root_side_counts": _count_map_jsonable(
            residual_root_side_counts or {}
        ),
        "residual_candidate_source_pathology_counts": _count_map_jsonable(
            residual_candidate_source_pathology_counts or {}
        ),
        "residual_candidate_compare_shape_counts": _count_map_jsonable(
            residual_candidate_compare_shape_counts or {}
        ),
        "residual_candidate_structural_counts": _count_map_jsonable(
            residual_candidate_structural_counts or {}
        ),
        "residual_candidate_action_counts": _count_map_jsonable(
            residual_candidate_action_counts or {}
        ),
        "residual_candidate_target_presence_counts": _count_map_jsonable(
            residual_candidate_target_presence_counts or {}
        ),
        "residual_candidate_target_presence_action_counts": _count_map_jsonable(
            residual_candidate_target_presence_action_counts or {}
        ),
        "residual_candidate_manual_compile_rule_counts": _count_map_jsonable(
            residual_candidate_manual_compile_rule_counts or {}
        ),
        "residual_candidate_root_samples": [
            dict(item) for item in residual_candidate_root_samples
        ],
        "residual_candidate_root_samples_omitted": int(
            residual_candidate_root_samples_omitted
        ),
        "residual_candidate_samples": list(sample_rows),
        "residual_candidate_samples_omitted": residual_candidate_samples_omitted,
        "residual_roots": sorted(residual_roots),
        "replayed_residual_roots": sorted(replayed_residual_roots),
        "oracle_residual_roots": sorted(oracle_residual_roots),
        "malformed_residual_roots": sorted(malformed_residual_roots),
        "backed_residual_roots": sorted(residual_root_hits),
        "defeated_residual_roots": sorted(defeated_residual_roots),
        "status": status,
        "triage_rule_id": _triage_rule_id(status),
    }


def _triage_rule_id(status: str) -> str:
    if status == "real residual frontier":
        return "uk_residual_claim_backed_by_candidate_overlap"
    if status == "residual branches defeated by no candidate overlap":
        return "uk_residual_claim_defeated_no_candidate_overlap"
    if status == "classification-heavy":
        return "uk_frontier_classification_heavy_no_candidate_effects"
    if status == "candidate-clean after residual overlap":
        return "uk_frontier_candidate_clean_after_residual_overlap"
    if status == "frontier prefilter only":
        return "uk_frontier_prefilter_only"
    if status == "residual analysis budget skipped":
        return "uk_residual_analysis_budget_skipped"
    if status == "residual comparison source unavailable":
        return "uk_residual_analysis_source_unavailable"
    if status == "residual comparison execution unavailable":
        return "uk_residual_analysis_execution_unavailable"
    if status == "effect inspection budget truncated":
        return "uk_effect_inspection_budget_truncated"
    if status == "malformed residual roots deferred":
        return "uk_residual_claim_deferred_malformed_eid_root"
    if status == "residual branches include malformed roots":
        return "uk_residual_claim_partially_deferred_malformed_eid_root"
    return "uk_frontier_status_unclassified"


def _saved_bench_prefilter_candidate_row_jsonable(
    result: object,
    *,
    score_mode: str,
    replay_adjudication_kinds: set[str] | None = None,
    replay_adjudication_sample_limit: int = 5,
) -> dict[str, Any]:
    """Build a saved-run-only candidate row for summary aggregation."""
    return _uk_candidate_row_jsonable(
        result,
        score_mode=score_mode,
        source_counts={},
        compare_counts={},
        candidate_source_counts={},
        candidate_compare_counts={},
        non_candidate_source_counts={},
        non_candidate_compare_counts={},
        lowering_rejection_rule_counts=_count_map_from_object(
            getattr(result, "lowering_rejection_rule_counts", {})
        ),
        blocking_lowering_rejection_rule_counts=_count_map_from_object(
            getattr(result, "blocking_lowering_rejection_rule_counts", {})
        ),
        source_acquisition_observation_rule_counts=_count_map_from_object(
            getattr(result, "source_acquisition_observation_rule_counts", {})
        ),
        rows_with_source_acquisition_observations=(
            1 if int(getattr(result, "source_acquisition_observation_count", 0) or 0) else 0
        ),
        source_acquisition_rejection_rule_counts=_count_map_from_object(
            getattr(result, "source_acquisition_rejection_rule_counts", {})
        ),
        rows_with_source_acquisition_rejections=(
            1 if int(getattr(result, "source_acquisition_rejection_count", 0) or 0) else 0
        ),
        rows_with_blocking_lowering_rejections=(
            1 if int(getattr(result, "blocking_lowering_rejection_count", 0) or 0) else 0
        ),
        inspected_effect_count=0,
        available_replay_applicable_effect_count=0,
        available_applied_effect_count=0,
        effect_inspection_truncated=False,
        residual_analysis_skipped=True,
        candidate_count=0,
        candidate_ops=0,
        residual_candidate_count=0,
        residual_candidate_ops=0,
        residual_roots=set(),
        replayed_residual_roots=set(),
        oracle_residual_roots=set(),
        malformed_residual_roots=set(),
        residual_root_hits=set(),
        defeated_residual_roots=set(),
        status="frontier prefilter only",
        manual_compile_status_counts=_count_map_from_object(
            getattr(result, "manual_compile_status_counts", {})
        ),
        manual_compile_rule_counts=_count_map_from_object(
            getattr(result, "manual_compile_rule_counts", {})
        ),
        suggested_claim_template_status_counts={},
        lowering_observation_rule_counts=_count_map_from_object(
            getattr(
                result,
                "lowering_observation_rule_counts",
                getattr(result, "lowering_rejection_rule_counts", {}),
            )
        ),
        rows_with_lowering_observations=(
            1 if int(getattr(result, "lowering_observation_count", 0) or 0) else 0
        ),
        replay_adjudication_kinds=replay_adjudication_kinds,
        replay_adjudication_sample_limit=replay_adjudication_sample_limit,
    )


def _uk_candidates_report_jsonable(
    *,
    label: str,
    rows: list[dict[str, Any]],
    filters: dict[str, Any],
    inspected_count: int,
    matched_frontier_count: int | None = None,
    replay_adjudication_prefilter_count: int | None = None,
    summary_only: bool = False,
    compact_rows: bool = False,
    emitted_row_count: int | None = None,
    summary_count_limit: int | None = None,
    row_count_limit: int | None = None,
) -> dict[str, Any]:
    matched_count = inspected_count if matched_frontier_count is None else matched_frontier_count
    replay_prefilter_count = (
        matched_count
        if replay_adjudication_prefilter_count is None
        else replay_adjudication_prefilter_count
    )
    status_counts: Counter[str] = Counter(str(row.get("status") or "") for row in rows)
    source_counts: Counter[str] = Counter()
    compare_counts: Counter[str] = Counter()
    candidate_source_counts: Counter[str] = Counter()
    candidate_compare_counts: Counter[str] = Counter()
    non_candidate_source_counts: Counter[str] = Counter()
    non_candidate_compare_counts: Counter[str] = Counter()
    enacted_source_status_counts: Counter[str] = Counter()
    oracle_source_status_counts: Counter[str] = Counter()
    uk_replay_regime_counts: Counter[str] = Counter()
    uk_source_purity_lane_counts: Counter[str] = Counter()
    uk_source_first_candidate_reason_counts: Counter[str] = Counter()
    rows_with_source_semantics_clean = 0
    rows_with_source_first_candidate = 0
    comparison_class_counts: Counter[str] = Counter()
    core_benchmark_counts: Counter[str] = Counter()
    lowering_rejection_rule_counts: Counter[str] = Counter()
    blocking_lowering_rejection_rule_counts: Counter[str] = Counter()
    source_acquisition_observation_rule_counts: Counter[str] = Counter()
    source_acquisition_rejection_rule_counts: Counter[str] = Counter()
    manual_compile_status_counts: Counter[str] = Counter()
    manual_compile_rule_counts: Counter[str] = Counter()
    suggested_claim_template_status_counts: Counter[str] = Counter()
    lowering_observation_rule_counts: Counter[str] = Counter()
    bench_authority_observation_count = 0
    bench_authority_observation_rule_counts: Counter[str] = Counter()
    bench_authority_rejection_count = 0
    bench_authority_rejection_rule_counts: Counter[str] = Counter()
    bench_effect_source_pathology_counts: Counter[str] = Counter()
    bench_manual_compile_status_counts: Counter[str] = Counter()
    bench_manual_compile_rule_counts: Counter[str] = Counter()
    bench_source_acquisition_rejection_count = 0
    rows_with_bench_source_acquisition_rejections = 0
    bench_source_acquisition_rejection_rule_counts: Counter[str] = Counter()
    candidate_effect_count = 0
    candidate_op_count = 0
    residual_candidate_effect_count = 0
    residual_candidate_op_count = 0
    residual_candidate_source_pathology_counts: Counter[str] = Counter()
    residual_candidate_compare_shape_counts: Counter[str] = Counter()
    residual_candidate_structural_counts: Counter[str] = Counter()
    residual_candidate_action_counts: Counter[str] = Counter()
    residual_candidate_target_presence_counts: Counter[str] = Counter()
    residual_candidate_target_presence_action_counts: Counter[str] = Counter()
    residual_candidate_manual_compile_rule_counts: Counter[str] = Counter()
    residual_root_count = 0
    replayed_residual_root_count = 0
    oracle_residual_root_count = 0
    malformed_residual_root_count = 0
    backed_residual_root_count = 0
    defeated_residual_root_count = 0
    rows_with_source_acquisition_observations = 0
    rows_with_source_acquisition_rejections = 0
    rows_with_lowering_observations = 0
    rows_with_blocking_lowering_rejections = 0
    effect_feed_parse_rejection_count = 0
    rows_with_effect_feed_parse_rejections = 0
    effect_feed_parse_rejection_rule_counts: Counter[str] = Counter()
    rows_with_effect_feed_count_errors = 0
    effect_feed_observation_count = 0
    rows_with_effect_feed_observations = 0
    effect_feed_observation_rule_counts: Counter[str] = Counter()
    effect_selection_observation_count = 0
    rows_with_effect_selection_observations = 0
    effect_selection_observation_rule_counts: Counter[str] = Counter()
    effect_selection_rejection_count = 0
    rows_with_effect_selection_rejections = 0
    effect_selection_rejection_rule_counts: Counter[str] = Counter()
    source_parse_rejection_count = 0
    rows_with_source_parse_rejections = 0
    source_parse_rejection_rule_counts: Counter[str] = Counter()
    source_parse_observation_count = 0
    rows_with_source_parse_observations = 0
    source_parse_observation_rule_counts: Counter[str] = Counter()
    bench_exception_count = 0
    rows_with_bench_exceptions = 0
    bench_exception_rule_counts: Counter[str] = Counter()
    saved_bench_diagnostic_count = 0
    rows_with_saved_bench_diagnostics = 0
    saved_bench_diagnostic_rule_counts: Counter[str] = Counter()
    saved_bench_diagnostic_lane_counts: Counter[str] = Counter()
    replay_adjudication_count = 0
    rows_with_replay_adjudications = 0
    replay_adjudication_kind_counts: Counter[str] = Counter()
    replay_adjudication_bucket_counts: Counter[str] = Counter()
    replay_adjudication_sample_count = 0
    replay_adjudication_samples_omitted = 0
    uk_residual_claim_tier_counts: Counter[str] = Counter()
    uk_residual_claim_kind_counts: Counter[str] = Counter()
    rows_with_residual_section_claims = 0
    residual_claim_only_in_replayed_count = 0
    residual_claim_only_in_oracle_count = 0
    residual_compile_rejection_count = 0
    rows_with_residual_compile_rejections = 0
    residual_compile_rejection_rule_counts: Counter[str] = Counter()
    residual_compile_observation_count = 0
    rows_with_residual_compile_observations = 0
    residual_compile_observation_rule_counts: Counter[str] = Counter()
    saved_legacy_effect_count = 0
    saved_effect_row_count = 0
    saved_effect_feed_page_count = 0
    inspected_effect_count = 0
    available_replay_applicable_effect_count = 0
    available_applied_effect_count = 0
    rows_with_effect_inspection_truncated = 0
    rows_with_residual_analysis_skipped = 0
    rows_with_residual_analysis_unavailable = 0
    rows_with_candidate_analysis_skipped = 0
    for row in rows:
        candidate_effect_count += int(row.get("candidate_effect_count") or 0)
        candidate_op_count += int(row.get("candidate_op_count") or 0)
        residual_candidate_effect_count += int(row.get("residual_candidate_effect_count") or 0)
        residual_candidate_op_count += int(row.get("residual_candidate_op_count") or 0)
        for key, count in dict(row.get("residual_candidate_source_pathology_counts") or {}).items():
            residual_candidate_source_pathology_counts[str(key)] += int(count)
        for key, count in dict(row.get("residual_candidate_compare_shape_counts") or {}).items():
            residual_candidate_compare_shape_counts[str(key)] += int(count)
        for key, count in dict(row.get("residual_candidate_structural_counts") or {}).items():
            residual_candidate_structural_counts[str(key)] += int(count)
        for key, count in dict(row.get("residual_candidate_action_counts") or {}).items():
            residual_candidate_action_counts[str(key)] += int(count)
        for key, count in dict(row.get("residual_candidate_target_presence_counts") or {}).items():
            residual_candidate_target_presence_counts[str(key)] += int(count)
        for key, count in dict(row.get("residual_candidate_target_presence_action_counts") or {}).items():
            residual_candidate_target_presence_action_counts[str(key)] += int(count)
        for key, count in dict(row.get("residual_candidate_manual_compile_rule_counts") or {}).items():
            residual_candidate_manual_compile_rule_counts[str(key)] += int(count)
        residual_root_count += len(tuple(row.get("residual_roots") or ()))
        replayed_residual_root_count += len(tuple(row.get("replayed_residual_roots") or ()))
        oracle_residual_root_count += len(tuple(row.get("oracle_residual_roots") or ()))
        malformed_residual_root_count += len(tuple(row.get("malformed_residual_roots") or ()))
        backed_residual_root_count += len(tuple(row.get("backed_residual_roots") or ()))
        defeated_residual_root_count += len(tuple(row.get("defeated_residual_roots") or ()))
        rows_with_blocking_lowering_rejections += int(
            row.get("rows_with_blocking_lowering_rejections") or 0
        )
        row_parse_rejection_count = int(row.get("effect_feed_parse_rejection_count") or 0)
        effect_feed_parse_rejection_count += row_parse_rejection_count
        if row_parse_rejection_count:
            rows_with_effect_feed_parse_rejections += 1
        for rule_id, count in dict(row.get("effect_feed_parse_rejection_rule_counts") or {}).items():
            effect_feed_parse_rejection_rule_counts[str(rule_id)] += int(count)
        if str(row.get("effect_feed_count_error") or ""):
            rows_with_effect_feed_count_errors += 1
        row_feed_observation_count = int(row.get("effect_feed_observation_count") or 0)
        effect_feed_observation_count += row_feed_observation_count
        if row_feed_observation_count:
            rows_with_effect_feed_observations += 1
        for rule_id, count in dict(row.get("effect_feed_observation_rule_counts") or {}).items():
            effect_feed_observation_rule_counts[str(rule_id)] += int(count)
        row_selection_observation_count = int(row.get("effect_selection_observation_count") or 0)
        effect_selection_observation_count += row_selection_observation_count
        if row_selection_observation_count:
            rows_with_effect_selection_observations += 1
        for rule_id, count in dict(row.get("effect_selection_observation_rule_counts") or {}).items():
            effect_selection_observation_rule_counts[str(rule_id)] += int(count)
        row_selection_rejection_count = int(row.get("effect_selection_rejection_count") or 0)
        effect_selection_rejection_count += row_selection_rejection_count
        if row_selection_rejection_count:
            rows_with_effect_selection_rejections += 1
        for rule_id, count in dict(row.get("effect_selection_rejection_rule_counts") or {}).items():
            effect_selection_rejection_rule_counts[str(rule_id)] += int(count)
        row_source_parse_rejection_count = int(row.get("source_parse_rejection_count") or 0)
        source_parse_rejection_count += row_source_parse_rejection_count
        if row_source_parse_rejection_count:
            rows_with_source_parse_rejections += 1
        for rule_id, count in dict(row.get("source_parse_rejection_rule_counts") or {}).items():
            source_parse_rejection_rule_counts[str(rule_id)] += int(count)
        row_source_parse_observation_count = int(row.get("source_parse_observation_count") or 0)
        source_parse_observation_count += row_source_parse_observation_count
        if row_source_parse_observation_count:
            rows_with_source_parse_observations += 1
        for rule_id, count in dict(row.get("source_parse_observation_rule_counts") or {}).items():
            source_parse_observation_rule_counts[str(rule_id)] += int(count)
        row_bench_exception_count = int(row.get("bench_exception_count") or 0)
        bench_exception_count += row_bench_exception_count
        if row_bench_exception_count:
            rows_with_bench_exceptions += 1
        for rule_id, count in dict(row.get("bench_exception_rule_counts") or {}).items():
            bench_exception_rule_counts[str(rule_id)] += int(count)
        row_saved_bench_diagnostic_count = int(row.get("saved_bench_diagnostic_count") or 0)
        saved_bench_diagnostic_count += row_saved_bench_diagnostic_count
        if row_saved_bench_diagnostic_count:
            rows_with_saved_bench_diagnostics += 1
        for rule_id, count in dict(row.get("saved_bench_diagnostic_rule_counts") or {}).items():
            saved_bench_diagnostic_rule_counts[str(rule_id)] += int(count)
        for lane, count in dict(row.get("saved_bench_diagnostic_lane_counts") or {}).items():
            saved_bench_diagnostic_lane_counts[str(lane)] += int(count)
        regime_claim = row.get("uk_replay_regime_claim")
        if isinstance(regime_claim, Mapping):
            uk_source_purity_lane_counts[
                str(regime_claim.get("source_purity_lane") or "unknown")
            ] += 1
            if _bool_bench_field(regime_claim.get("source_semantics_clean"), default=False):
                rows_with_source_semantics_clean += 1
            if _bool_bench_field(regime_claim.get("source_first_candidate"), default=False):
                rows_with_source_first_candidate += 1
            for reason in _string_tuple_from_object(
                regime_claim.get("source_first_candidate_reasons")
            ):
                uk_source_first_candidate_reason_counts[reason] += 1
        row_replay_adjudication_count = int(row.get("replay_adjudication_count") or 0)
        replay_adjudication_count += row_replay_adjudication_count
        if row_replay_adjudication_count:
            rows_with_replay_adjudications += 1
        for kind, count in dict(row.get("replay_adjudication_kind_counts") or {}).items():
            replay_adjudication_kind_counts[str(kind)] += int(count)
        for bucket, count in dict(row.get("replay_adjudication_bucket_counts") or {}).items():
            replay_adjudication_bucket_counts[str(bucket)] += int(count)
        replay_adjudication_sample_count += len(tuple(row.get("replay_adjudication_samples") or ()))
        replay_adjudication_samples_omitted += int(
            row.get("replay_adjudication_samples_omitted") or 0
        )
        residual_claim = row.get("uk_residual_claim")
        if isinstance(residual_claim, Mapping):
            uk_residual_claim_tier_counts[
                str(residual_claim.get("selected_tier") or "UNRESOLVED")
            ] += 1
            uk_residual_claim_kind_counts[
                str(residual_claim.get("selected_kind") or "unknown")
            ] += 1
            residual_claim_only_in_replayed_count += int(
                residual_claim.get("only_in_replayed_count") or 0
            )
            residual_claim_only_in_oracle_count += int(
                residual_claim.get("only_in_oracle_count") or 0
            )
            if _bool_bench_field(
                residual_claim.get("section_claim_emitted"),
                default=False,
            ):
                rows_with_residual_section_claims += 1
        row_residual_compile_rejection_count = int(row.get("residual_compile_rejection_count") or 0)
        residual_compile_rejection_count += row_residual_compile_rejection_count
        if row_residual_compile_rejection_count:
            rows_with_residual_compile_rejections += 1
        for rule_id, count in dict(row.get("residual_compile_rejection_rule_counts") or {}).items():
            residual_compile_rejection_rule_counts[str(rule_id)] += int(count)
        row_residual_compile_observation_count = int(row.get("residual_compile_observation_count") or 0)
        residual_compile_observation_count += row_residual_compile_observation_count
        if row_residual_compile_observation_count:
            rows_with_residual_compile_observations += 1
        for rule_id, count in dict(row.get("residual_compile_observation_rule_counts") or {}).items():
            residual_compile_observation_rule_counts[str(rule_id)] += int(count)
        row_source_acquisition_observation_count = int(
            row.get("rows_with_source_acquisition_observations") or 0
        )
        rows_with_source_acquisition_observations += row_source_acquisition_observation_count
        for rule_id, count in dict(row.get("source_acquisition_observation_rule_counts") or {}).items():
            source_acquisition_observation_rule_counts[str(rule_id)] += int(count)
        row_source_acquisition_rejection_count = int(
            row.get("rows_with_source_acquisition_rejections") or 0
        )
        rows_with_source_acquisition_rejections += row_source_acquisition_rejection_count
        for rule_id, count in dict(row.get("source_acquisition_rejection_rule_counts") or {}).items():
            source_acquisition_rejection_rule_counts[str(rule_id)] += int(count)
        bench_authority_observation_count += int(row.get("bench_authority_observation_count") or 0)
        for rule_id, count in dict(row.get("bench_authority_observation_rule_counts") or {}).items():
            bench_authority_observation_rule_counts[str(rule_id)] += int(count)
        bench_authority_rejection_count += int(row.get("bench_authority_rejection_count") or 0)
        for rule_id, count in dict(row.get("bench_authority_rejection_rule_counts") or {}).items():
            bench_authority_rejection_rule_counts[str(rule_id)] += int(count)
        for pathology, count in dict(row.get("bench_effect_source_pathology_counts") or {}).items():
            bench_effect_source_pathology_counts[str(pathology)] += int(count)
        for status, count in dict(row.get("bench_manual_compile_status_counts") or {}).items():
            bench_manual_compile_status_counts[str(status)] += int(count)
        for rule_id, count in dict(row.get("bench_manual_compile_rule_counts") or {}).items():
            bench_manual_compile_rule_counts[str(rule_id)] += int(count)
        row_bench_source_acquisition_count = int(row.get("bench_source_acquisition_rejection_count") or 0)
        bench_source_acquisition_rejection_count += row_bench_source_acquisition_count
        if row_bench_source_acquisition_count:
            rows_with_bench_source_acquisition_rejections += 1
        for rule_id, count in dict(row.get("bench_source_acquisition_rejection_rule_counts") or {}).items():
            bench_source_acquisition_rejection_rule_counts[str(rule_id)] += int(count)
        saved_legacy_effect_count += int(row.get("effect_count") or 0)
        saved_effect_row_count += int(row.get("effect_row_count") or 0)
        saved_effect_feed_page_count += int(row.get("effect_feed_page_count") or 0)
        inspected_effect_count += int(row.get("inspected_effect_count") or 0)
        available_replay_applicable_effect_count += int(
            row.get("available_replay_applicable_effect_count") or 0
        )
        available_applied_effect_count += int(row.get("available_applied_effect_count") or 0)
        if bool(row.get("effect_inspection_truncated", False)):
            rows_with_effect_inspection_truncated += 1
        if bool(row.get("residual_analysis_skipped", False)):
            rows_with_residual_analysis_skipped += 1
        if bool(row.get("residual_analysis_unavailable", False)):
            rows_with_residual_analysis_unavailable += 1
        if str(row.get("triage_rule_id") or "") == "uk_frontier_prefilter_only":
            rows_with_candidate_analysis_skipped += 1
        enacted_source_status_counts[str(row.get("enacted_source_status") or "unknown")] += 1
        oracle_source_status_counts[str(row.get("oracle_source_status") or "unknown")] += 1
        uk_replay_regime_counts[_uk_replay_regime_summary_key(row.get("uk_replay_regime"))] += 1
        comparison_class_counts[str(row.get("comparison_class") or "unknown")] += 1
        core_benchmark_counts["core" if bool(row.get("core_benchmark", False)) else "non_core"] += 1
        for key, count in dict(row.get("source_counts") or {}).items():
            source_counts[str(key)] += int(count)
        for key, count in dict(row.get("compare_counts") or {}).items():
            compare_counts[str(key)] += int(count)
        for key, count in dict(row.get("candidate_source_counts") or {}).items():
            candidate_source_counts[str(key)] += int(count)
        for key, count in dict(row.get("candidate_compare_counts") or {}).items():
            candidate_compare_counts[str(key)] += int(count)
        for key, count in dict(row.get("non_candidate_source_counts") or {}).items():
            non_candidate_source_counts[str(key)] += int(count)
        for key, count in dict(row.get("non_candidate_compare_counts") or {}).items():
            non_candidate_compare_counts[str(key)] += int(count)
        for key, count in dict(row.get("manual_compile_status_counts") or {}).items():
            manual_compile_status_counts[str(key)] += int(count)
        for key, count in dict(row.get("manual_compile_rule_counts") or {}).items():
            manual_compile_rule_counts[str(key)] += int(count)
        for key, count in dict(row.get("suggested_claim_template_status_counts") or {}).items():
            suggested_claim_template_status_counts[str(key)] += int(count)
        row_lowering_observation_rule_counts = dict(
            row.get(
                "lowering_observation_rule_counts",
                row.get("lowering_rejection_rule_counts", {}),
            )
            or {}
        )
        if "rows_with_lowering_observations" in row:
            rows_with_lowering_observations += int(
                row.get("rows_with_lowering_observations") or 0
            )
        elif row_lowering_observation_rule_counts:
            rows_with_lowering_observations += 1
        for rule_id, count in row_lowering_observation_rule_counts.items():
            lowering_observation_rule_counts[str(rule_id)] += int(count)
        for rule_id, count in dict(row.get("lowering_rejection_rule_counts") or {}).items():
            lowering_rejection_rule_counts[str(rule_id)] += int(count)
        for rule_id, count in dict(row.get("blocking_lowering_rejection_rule_counts") or {}).items():
            blocking_lowering_rejection_rule_counts[str(rule_id)] += int(count)
    summary_payload = {
        "configured_top": filters.get("top"),
        "configured_score_mode": filters.get("score_mode"),
        "configured_effect_budget": filters.get("effect_budget"),
        "configured_residual_budget": filters.get("residual_budget"),
        "pre_replay_adjudication_filter_frontier_count": replay_prefilter_count,
        "replay_adjudication_filter_excluded_count": max(
            0,
            replay_prefilter_count - matched_count,
        ),
        "matched_frontier_count": matched_count,
        "inspected_frontier_count": inspected_count,
        "frontier_truncated": inspected_count < matched_count,
        "emitted_row_count": len(rows) if emitted_row_count is None else emitted_row_count,
        "status_counts": _count_map_jsonable(status_counts),
        "inspected_effect_count": inspected_effect_count,
        "inspected_replay_applicable_effect_count": inspected_effect_count,
        "candidate_effect_count": candidate_effect_count,
        "candidate_op_count": candidate_op_count,
        "residual_candidate_effect_count": residual_candidate_effect_count,
        "residual_candidate_op_count": residual_candidate_op_count,
        "residual_candidate_source_pathology_counts": _count_map_jsonable(
            residual_candidate_source_pathology_counts
        ),
        "residual_candidate_compare_shape_counts": _count_map_jsonable(
            residual_candidate_compare_shape_counts
        ),
        "residual_candidate_structural_counts": _count_map_jsonable(
            residual_candidate_structural_counts
        ),
        "residual_candidate_action_counts": _count_map_jsonable(
            residual_candidate_action_counts
        ),
        "residual_candidate_target_presence_counts": _count_map_jsonable(
            residual_candidate_target_presence_counts
        ),
        "residual_candidate_target_presence_action_counts": _count_map_jsonable(
            residual_candidate_target_presence_action_counts
        ),
        "residual_candidate_manual_compile_rule_counts": _count_map_jsonable(
            residual_candidate_manual_compile_rule_counts
        ),
        "residual_root_count": residual_root_count,
        "replayed_residual_root_count": replayed_residual_root_count,
        "oracle_residual_root_count": oracle_residual_root_count,
        "malformed_residual_root_count": malformed_residual_root_count,
        "backed_residual_root_count": backed_residual_root_count,
        "defeated_residual_root_count": defeated_residual_root_count,
        "rows_with_source_acquisition_rejections": rows_with_source_acquisition_rejections,
        "source_acquisition_rejection_rule_counts": _count_map_jsonable(
            source_acquisition_rejection_rule_counts
        ),
        "rows_with_source_acquisition_observations": rows_with_source_acquisition_observations,
        "source_acquisition_observation_rule_counts": _count_map_jsonable(
            source_acquisition_observation_rule_counts
        ),
        "bench_authority_observation_count": bench_authority_observation_count,
        "bench_authority_observation_rule_counts": _count_map_jsonable(
            bench_authority_observation_rule_counts
        ),
        "bench_authority_rejection_count": bench_authority_rejection_count,
        "bench_authority_rejection_rule_counts": _count_map_jsonable(
            bench_authority_rejection_rule_counts
        ),
        "bench_effect_source_pathology_counts": _count_map_jsonable(
            bench_effect_source_pathology_counts
        ),
        "bench_manual_compile_status_counts": _count_map_jsonable(
            bench_manual_compile_status_counts
        ),
        "bench_manual_compile_rule_counts": _count_map_jsonable(
            bench_manual_compile_rule_counts
        ),
        "bench_source_acquisition_rejection_count": (
            bench_source_acquisition_rejection_count
        ),
        "rows_with_bench_source_acquisition_rejections": (
            rows_with_bench_source_acquisition_rejections
        ),
        "bench_source_acquisition_rejection_rule_counts": _count_map_jsonable(
            bench_source_acquisition_rejection_rule_counts
        ),
        "rows_with_lowering_observations": rows_with_lowering_observations,
        "rows_with_blocking_lowering_rejections": rows_with_blocking_lowering_rejections,
        "effect_feed_parse_rejection_count": effect_feed_parse_rejection_count,
        "rows_with_effect_feed_parse_rejections": rows_with_effect_feed_parse_rejections,
        "effect_feed_parse_rejection_rule_counts": _count_map_jsonable(
            effect_feed_parse_rejection_rule_counts
        ),
        "rows_with_effect_feed_count_errors": rows_with_effect_feed_count_errors,
        "effect_feed_observation_count": effect_feed_observation_count,
        "rows_with_effect_feed_observations": rows_with_effect_feed_observations,
        "effect_feed_observation_rule_counts": _count_map_jsonable(
            effect_feed_observation_rule_counts
        ),
        "effect_selection_observation_count": effect_selection_observation_count,
        "rows_with_effect_selection_observations": rows_with_effect_selection_observations,
        "effect_selection_observation_rule_counts": _count_map_jsonable(
            effect_selection_observation_rule_counts
        ),
        "effect_selection_rejection_count": effect_selection_rejection_count,
        "rows_with_effect_selection_rejections": rows_with_effect_selection_rejections,
        "effect_selection_rejection_rule_counts": _count_map_jsonable(
            effect_selection_rejection_rule_counts
        ),
        "source_parse_rejection_count": source_parse_rejection_count,
        "rows_with_source_parse_rejections": rows_with_source_parse_rejections,
        "source_parse_rejection_rule_counts": _count_map_jsonable(
            source_parse_rejection_rule_counts
        ),
        "source_parse_observation_count": source_parse_observation_count,
        "rows_with_source_parse_observations": rows_with_source_parse_observations,
        "source_parse_observation_rule_counts": _count_map_jsonable(
            source_parse_observation_rule_counts
        ),
        "bench_exception_count": bench_exception_count,
        "rows_with_bench_exceptions": rows_with_bench_exceptions,
        "bench_exception_rule_counts": _count_map_jsonable(bench_exception_rule_counts),
        "saved_bench_diagnostic_count": saved_bench_diagnostic_count,
        "rows_with_saved_bench_diagnostics": rows_with_saved_bench_diagnostics,
        "saved_bench_diagnostic_rule_counts": _count_map_jsonable(
            saved_bench_diagnostic_rule_counts
        ),
        "saved_bench_diagnostic_lane_counts": _count_map_jsonable(
            saved_bench_diagnostic_lane_counts
        ),
        "replay_adjudication_count": replay_adjudication_count,
        "rows_with_replay_adjudications": rows_with_replay_adjudications,
        "replay_adjudication_kind_counts": _count_map_jsonable(
            replay_adjudication_kind_counts
        ),
        "replay_adjudication_bucket_counts": _count_map_jsonable(
            replay_adjudication_bucket_counts
        ),
        "replay_adjudication_sample_count": replay_adjudication_sample_count,
        "replay_adjudication_samples_omitted": replay_adjudication_samples_omitted,
        "residual_compile_observation_count": residual_compile_observation_count,
        "rows_with_residual_compile_observations": (
            rows_with_residual_compile_observations
        ),
        "residual_compile_observation_rule_counts": _count_map_jsonable(
            residual_compile_observation_rule_counts
        ),
        "residual_compile_rejection_count": residual_compile_rejection_count,
        "rows_with_residual_compile_rejections": rows_with_residual_compile_rejections,
        "residual_compile_rejection_rule_counts": _count_map_jsonable(
            residual_compile_rejection_rule_counts
        ),
        "source_counts": _count_map_jsonable(source_counts),
        "compare_counts": _count_map_jsonable(compare_counts),
        "enacted_source_status_counts": _count_map_jsonable(enacted_source_status_counts),
        "oracle_source_status_counts": _count_map_jsonable(oracle_source_status_counts),
        "uk_replay_regime_counts": _count_map_jsonable(uk_replay_regime_counts),
        "uk_source_purity_lane_counts": _count_map_jsonable(uk_source_purity_lane_counts),
        "rows_with_source_semantics_clean": rows_with_source_semantics_clean,
        "rows_with_source_first_candidate": rows_with_source_first_candidate,
        "uk_source_first_candidate_reason_counts": _count_map_jsonable(
            uk_source_first_candidate_reason_counts
        ),
        "uk_residual_claim_tier_counts": _count_map_jsonable(
            uk_residual_claim_tier_counts
        ),
        "uk_residual_claim_kind_counts": _count_map_jsonable(
            uk_residual_claim_kind_counts
        ),
        "rows_with_residual_section_claims": rows_with_residual_section_claims,
        "residual_claim_only_in_replayed_count": residual_claim_only_in_replayed_count,
        "residual_claim_only_in_oracle_count": residual_claim_only_in_oracle_count,
        "comparison_class_counts": _count_map_jsonable(comparison_class_counts),
        "core_benchmark_counts": _count_map_jsonable(core_benchmark_counts),
        "candidate_source_counts": _count_map_jsonable(candidate_source_counts),
        "candidate_compare_counts": _count_map_jsonable(candidate_compare_counts),
        "non_candidate_source_counts": _count_map_jsonable(non_candidate_source_counts),
        "non_candidate_compare_counts": _count_map_jsonable(non_candidate_compare_counts),
        "manual_compile_status_counts": _count_map_jsonable(manual_compile_status_counts),
        "manual_compile_rule_counts": _count_map_jsonable(manual_compile_rule_counts),
        "suggested_claim_template_status_counts": _count_map_jsonable(
            suggested_claim_template_status_counts
        ),
        "lowering_observation_rule_counts": _count_map_jsonable(
            lowering_observation_rule_counts
        ),
        "lowering_rejection_rule_counts": _count_map_jsonable(
            lowering_rejection_rule_counts
        ),
        "blocking_lowering_rejection_rule_counts": _count_map_jsonable(
            blocking_lowering_rejection_rule_counts
        ),
        "saved_legacy_effect_count": saved_legacy_effect_count,
        "saved_effect_row_count": saved_effect_row_count,
        "saved_effect_feed_page_count": saved_effect_feed_page_count,
        "available_replay_applicable_effect_count": (
            available_replay_applicable_effect_count
        ),
        "available_applied_effect_count": available_applied_effect_count,
        "rows_with_effect_inspection_truncated": rows_with_effect_inspection_truncated,
        "rows_with_residual_analysis_skipped": rows_with_residual_analysis_skipped,
        "rows_with_residual_analysis_unavailable": rows_with_residual_analysis_unavailable,
        "rows_with_candidate_analysis_skipped": rows_with_candidate_analysis_skipped,
    }
    payload: dict[str, Any] = {
        "report_kind": "uk_candidates_frontier_report",
        "label": label,
        "filters": filters,
        "summary": _limit_summary_count_maps(
            summary_payload,
            limit=summary_count_limit,
        ),
    }
    if not summary_only:
        emitted_rows = (
            [_compact_uk_candidate_row_jsonable(row) for row in rows]
            if compact_rows
            else rows
        )
        payload["rows"] = [
            _limit_row_count_maps(row, limit=row_count_limit)
            for row in emitted_rows
        ]
    return payload


def _print_uk_candidates_text_summary(report: Mapping[str, Any]) -> None:
    summary = report["summary"]

    def _format_counts(value: object) -> str:
        return _format_count_map(value)

    print("Summary:")
    print(
        "  frontier: "
        f"matched={summary['matched_frontier_count']} "
        f"inspected={summary['inspected_frontier_count']} "
        f"emitted={summary['emitted_row_count']} "
        f"truncated={str(summary['frontier_truncated']).lower()}"
    )
    print(
        "  configured: "
        f"top={summary.get('configured_top')} "
        f"score_mode={summary.get('configured_score_mode')} "
        f"effect_budget={summary.get('configured_effect_budget')} "
        f"residual_budget={summary.get('configured_residual_budget')}"
    )
    if summary.get("replay_adjudication_filter_excluded_count"):
        print(
            "  replay_adjudication_filter: "
            f"pre_filter={summary.get('pre_replay_adjudication_filter_frontier_count')} "
            f"excluded={summary.get('replay_adjudication_filter_excluded_count')}"
        )
    print(
        "  candidates: "
        f"effects={summary['candidate_effect_count']} "
        f"ops={summary['candidate_op_count']} "
        f"residual_effects={summary['residual_candidate_effect_count']} "
        f"residual_ops={summary['residual_candidate_op_count']}"
    )
    print(
        "  saved_effect_inventory: "
        f"legacy={summary.get('saved_legacy_effect_count', 0)} "
        f"rows={summary.get('saved_effect_row_count', 0)} "
        f"pages={summary.get('saved_effect_feed_page_count', 0)}"
    )
    print(
        "  inspected_effects: "
        f"inspected={summary['inspected_effect_count']} "
        f"available_replay_applicable={summary['available_replay_applicable_effect_count']} "
        f"available_applied={summary['available_applied_effect_count']}"
    )
    print(
        "  budgets: "
        f"effect_truncated_rows={summary['rows_with_effect_inspection_truncated']} "
        f"residual_skipped_rows={summary['rows_with_residual_analysis_skipped']} "
        f"residual_unavailable_rows={summary['rows_with_residual_analysis_unavailable']} "
        f"candidate_analysis_skipped_rows={summary['rows_with_candidate_analysis_skipped']} "
        f"feed_parse_observation_rows={summary['rows_with_effect_feed_observations']} "
        f"feed_parse_observations={summary['effect_feed_observation_count']} "
        f"feed_parse_rejection_rows={summary['rows_with_effect_feed_parse_rejections']} "
        f"feed_parse_rejections={summary['effect_feed_parse_rejection_count']} "
        f"effect_selection_observation_rows={summary.get('rows_with_effect_selection_observations', 0)} "
        f"effect_selection_observations={summary.get('effect_selection_observation_count', 0)} "
        f"effect_selection_rejection_rows={summary.get('rows_with_effect_selection_rejections', 0)} "
        f"effect_selection_rejections={summary.get('effect_selection_rejection_count', 0)} "
        f"feed_count_error_rows={summary['rows_with_effect_feed_count_errors']} "
        f"source_parse_observation_rows={summary['rows_with_source_parse_observations']} "
        f"source_parse_observations={summary['source_parse_observation_count']} "
        f"source_parse_rejection_rows={summary['rows_with_source_parse_rejections']} "
        f"source_parse_rejections={summary['source_parse_rejection_count']} "
        f"bench_exception_rows={summary['rows_with_bench_exceptions']} "
        f"bench_exceptions={summary['bench_exception_count']} "
        f"saved_bench_diagnostic_rows={summary.get('rows_with_saved_bench_diagnostics', 0)} "
        f"saved_bench_diagnostics={summary.get('saved_bench_diagnostic_count', 0)} "
        f"replay_adjudication_rows={summary.get('rows_with_replay_adjudications', 0)} "
        f"replay_adjudications={summary.get('replay_adjudication_count', 0)} "
        f"residual_compile_observation_rows={summary['rows_with_residual_compile_observations']} "
        f"residual_compile_observations={summary['residual_compile_observation_count']} "
        f"residual_compile_rejection_rows={summary['rows_with_residual_compile_rejections']} "
        f"residual_compile_rejections={summary['residual_compile_rejection_count']} "
        f"bench_authority_observations={summary.get('bench_authority_observation_count', 0)} "
        f"bench_authority_rejections={summary['bench_authority_rejection_count']} "
        f"bench_source_acquisition_rejection_rows="
        f"{summary.get('rows_with_bench_source_acquisition_rejections', 0)} "
        f"bench_source_acquisition_rejections="
        f"{summary.get('bench_source_acquisition_rejection_count', 0)} "
        f"lowering_observation_rows="
        f"{summary.get('rows_with_lowering_observations', 0)} "
        f"source_acquisition_observation_rows="
        f"{summary.get('rows_with_source_acquisition_observations', 0)} "
        f"source_acquisition_rejection_rows={summary['rows_with_source_acquisition_rejections']}"
    )
    print(
        "  residual_roots: "
        f"total={summary['residual_root_count']} "
        f"backed={summary['backed_residual_root_count']} "
        f"defeated={summary['defeated_residual_root_count']} "
        f"malformed={summary['malformed_residual_root_count']}"
    )
    print(
        "  source_status: "
        f"enacted={_format_counts(summary.get('enacted_source_status_counts'))} "
        f"oracle={_format_counts(summary.get('oracle_source_status_counts'))}"
    )
    print(
        "  classes: "
        f"comparison={_format_counts(summary.get('comparison_class_counts'))} "
        f"core={_format_counts(summary.get('core_benchmark_counts'))}"
    )
    print("  replay_regimes: " + _format_counts(summary.get("uk_replay_regime_counts")))
    if (
        summary.get("uk_source_purity_lane_counts")
        or summary.get("rows_with_source_semantics_clean")
        or summary.get("rows_with_source_first_candidate")
        or summary.get("uk_source_first_candidate_reason_counts")
    ):
        print(
            "  source_first_regime: "
            f"purity={_format_counts(summary.get('uk_source_purity_lane_counts'))} "
            f"clean_rows={summary.get('rows_with_source_semantics_clean', 0)} "
            f"candidate_rows={summary.get('rows_with_source_first_candidate', 0)} "
            f"reasons={_format_counts(summary.get('uk_source_first_candidate_reason_counts'))}"
        )
    if summary.get("uk_residual_claim_tier_counts") or summary.get("uk_residual_claim_kind_counts"):
        print(
            "  residual_claims: "
            f"tier={_format_counts(summary.get('uk_residual_claim_tier_counts'))} "
            f"kind={_format_counts(summary.get('uk_residual_claim_kind_counts'))} "
            f"section_claim_rows={summary.get('rows_with_residual_section_claims', 0)} "
            f"only_in_replayed={summary.get('residual_claim_only_in_replayed_count', 0)} "
            f"only_in_oracle={summary.get('residual_claim_only_in_oracle_count', 0)}"
        )
    if (
        summary.get("source_counts")
        or summary.get("candidate_source_counts")
        or summary.get("non_candidate_source_counts")
    ):
        print(
            "  source_evidence: "
            f"all={_format_counts(summary.get('source_counts'))} "
            f"candidate={_format_counts(summary.get('candidate_source_counts'))} "
            f"non_candidate={_format_counts(summary.get('non_candidate_source_counts'))}"
        )
    if (
        summary.get("compare_counts")
        or summary.get("candidate_compare_counts")
        or summary.get("non_candidate_compare_counts")
    ):
        print(
            "  compare_evidence: "
            f"all={_format_counts(summary.get('compare_counts'))} "
            f"candidate={_format_counts(summary.get('candidate_compare_counts'))} "
            f"non_candidate={_format_counts(summary.get('non_candidate_compare_counts'))}"
        )
    if (
        summary.get("manual_compile_status_counts")
        or summary.get("manual_compile_rule_counts")
        or summary.get("suggested_claim_template_status_counts")
    ):
        print(
            "  manual_compile_frontier: "
            f"status={_format_counts(summary.get('manual_compile_status_counts'))} "
            f"rules={_format_counts(summary.get('manual_compile_rule_counts'))} "
            "claim_templates="
            f"{_format_counts(summary.get('suggested_claim_template_status_counts'))}"
        )
    if (
        summary.get("effect_feed_parse_rejection_rule_counts")
        or summary.get("effect_feed_observation_rule_counts")
        or summary.get("effect_selection_observation_rule_counts")
        or summary.get("effect_selection_rejection_rule_counts")
        or summary.get("source_parse_observation_rule_counts")
        or summary.get("source_parse_rejection_rule_counts")
        or summary.get("bench_exception_rule_counts")
        or summary.get("saved_bench_diagnostic_rule_counts")
        or summary.get("saved_bench_diagnostic_lane_counts")
        or summary.get("replay_adjudication_kind_counts")
        or summary.get("replay_adjudication_bucket_counts")
        or summary.get("residual_compile_observation_rule_counts")
        or summary.get("residual_compile_rejection_rule_counts")
        or summary.get("source_acquisition_observation_rule_counts")
        or summary.get("source_acquisition_rejection_rule_counts")
        or summary.get("bench_authority_observation_rule_counts")
        or summary.get("bench_authority_rejection_rule_counts")
        or summary.get("bench_effect_source_pathology_counts")
        or summary.get("bench_manual_compile_status_counts")
        or summary.get("bench_manual_compile_rule_counts")
        or summary.get("bench_source_acquisition_rejection_rule_counts")
        or summary.get("lowering_observation_rule_counts")
        or summary.get("lowering_rejection_rule_counts")
        or summary.get("blocking_lowering_rejection_rule_counts")
    ):
        print("  rejection_rules:")
        print(
            "    feed_parse: "
            + _format_counts(summary.get("effect_feed_parse_rejection_rule_counts"))
        )
        print(
            "    feed_observation: "
            + _format_counts(summary.get("effect_feed_observation_rule_counts"))
        )
        print(
            "    effect_selection_observation: "
            + _format_counts(summary.get("effect_selection_observation_rule_counts"))
        )
        print(
            "    effect_selection_rejection: "
            + _format_counts(summary.get("effect_selection_rejection_rule_counts"))
        )
        print(
            "    source_parse_observation: "
            + _format_counts(summary.get("source_parse_observation_rule_counts"))
        )
        print(
            "    source_parse: "
            + _format_counts(summary.get("source_parse_rejection_rule_counts"))
        )
        print(
            "    bench_exception: "
            + _format_counts(summary.get("bench_exception_rule_counts"))
        )
        print(
            "    saved_bench_diagnostic_rules: "
            + _format_counts(summary.get("saved_bench_diagnostic_rule_counts"))
        )
        print(
            "    saved_bench_diagnostic_lanes: "
            + _format_counts(summary.get("saved_bench_diagnostic_lane_counts"))
        )
        print(
            "    replay_adjudication: "
            + _format_counts(summary.get("replay_adjudication_kind_counts"))
        )
        print(
            "    replay_adjudication_buckets: "
            + _format_counts(summary.get("replay_adjudication_bucket_counts"))
        )
        print(
            "    residual_compile_observation: "
            + _format_counts(summary.get("residual_compile_observation_rule_counts"))
        )
        print(
            "    residual_compile: "
            + _format_counts(summary.get("residual_compile_rejection_rule_counts"))
        )
        print(
            "    source_acquisition_observation: "
            + _format_counts(summary.get("source_acquisition_observation_rule_counts"))
        )
        print(
            "    source_acquisition: "
            + _format_counts(summary.get("source_acquisition_rejection_rule_counts"))
        )
        print(
            "    bench_authority_observation: "
            + _format_counts(summary.get("bench_authority_observation_rule_counts"))
        )
        print(
            "    bench_authority: "
            + _format_counts(summary.get("bench_authority_rejection_rule_counts"))
        )
        print(
            "    bench_effect_source_pathology: "
            + _format_counts(summary.get("bench_effect_source_pathology_counts"))
        )
        print(
            "    bench_manual_compile_status: "
            + _format_counts(summary.get("bench_manual_compile_status_counts"))
        )
        print(
            "    bench_manual_compile_rule: "
            + _format_counts(summary.get("bench_manual_compile_rule_counts"))
        )
        print(
            "    bench_source_acquisition: "
            + _format_counts(summary.get("bench_source_acquisition_rejection_rule_counts"))
        )
        print(
            "    lowering_observation: "
            + _format_counts(summary.get("lowering_observation_rule_counts"))
        )
        print("    lowering: " + _format_counts(summary.get("lowering_rejection_rule_counts")))
        print(
            "    blocking_lowering: "
            + _format_counts(summary.get("blocking_lowering_rejection_rule_counts"))
        )


def _format_candidate_source_status(row: object) -> str:
    enacted_status = str(getattr(row, "enacted_source_status", "") or "unknown")
    oracle_status = str(getattr(row, "oracle_source_status", "") or "unknown")
    enacted_size = int(getattr(row, "enacted_source_size", 0) or 0)
    oracle_size = int(getattr(row, "oracle_source_size", 0) or 0)
    text = (
        f"enacted={enacted_status} ({enacted_size} bytes) "
        f"oracle={oracle_status} ({oracle_size} bytes)"
    )
    enacted_url = str(getattr(row, "enacted_source_url", "") or "")
    oracle_url = str(getattr(row, "oracle_source_url", "") or "")
    if enacted_url or oracle_url:
        text += f" enacted_url={enacted_url or '(none)'} oracle_url={oracle_url or '(none)'}"
    enacted_sha = str(getattr(row, "enacted_source_sha256", "") or "")
    oracle_sha = str(getattr(row, "oracle_source_sha256", "") or "")
    if enacted_sha or oracle_sha:
        text += f" enacted_sha256={enacted_sha or '(none)'} oracle_sha256={oracle_sha or '(none)'}"
    return text


def _format_candidate_effect_inventory(row: object) -> str:
    effect_rows = int(getattr(row, "n_effect_rows", getattr(row, "n_effects", 0)) or 0)
    effect_pages = int(getattr(row, "n_effect_feed_pages", getattr(row, "n_effects", 0)) or 0)
    return f"effect_rows={effect_rows:4d} effect_pages={effect_pages:4d}"


def _format_saved_bench_rejection_rules(row: object) -> str:
    feed_rules = _count_map_from_object(getattr(row, "effect_feed_rejection_rule_counts", {}))
    feed_observation_rules = _count_map_from_object(
        getattr(row, "effect_feed_observation_rule_counts", {})
    )
    source_parse_rules = _count_map_from_object(
        getattr(row, "source_parse_rejection_rule_counts", {})
    )
    source_parse_observation_rules = _count_map_from_object(
        getattr(row, "source_parse_observation_rule_counts", {})
    )
    bench_exception_rules = _count_map_from_object(
        getattr(row, "bench_exception_rule_counts", {})
    )
    effect_source_pathology_counts = _count_map_from_object(
        getattr(row, "effect_source_pathology_counts", {})
    )
    manual_compile_status_counts = _count_map_from_object(
        getattr(row, "manual_compile_status_counts", {})
    )
    manual_compile_rule_counts = _count_map_from_object(
        getattr(row, "manual_compile_rule_counts", {})
    )
    source_acquisition_rules = _count_map_from_object(
        getattr(row, "source_acquisition_rejection_rule_counts", {})
    )
    authority_observation_rules = _count_map_from_object(
        getattr(row, "uk_authority_observation_rule_counts", {})
    )
    authority_rules = _count_map_from_object(
        getattr(row, "uk_authority_rejection_rule_counts", {})
    )
    lowering_observation_rules = _count_map_from_object(
        getattr(
            row,
            "lowering_observation_rule_counts",
            getattr(row, "lowering_rejection_rule_counts", {}),
        )
    )
    lowering_rules = _count_map_from_object(getattr(row, "lowering_rejection_rule_counts", {}))
    blocking_rules = _count_map_from_object(
        getattr(row, "blocking_lowering_rejection_rule_counts", {})
    )
    saved_bench_diagnostic_rows = _saved_bench_diagnostic_rows_from_result(row)
    saved_bench_diagnostic_rules = _rejection_rule_counts(saved_bench_diagnostic_rows)
    saved_bench_diagnostic_lanes = _count_map_jsonable(
        Counter(
            str(diagnostic_row.get("diagnostic_lane") or "unknown")
            for diagnostic_row in saved_bench_diagnostic_rows
        )
    )
    if not (
        feed_rules
        or feed_observation_rules
        or source_parse_rules
        or source_parse_observation_rules
        or bench_exception_rules
        or effect_source_pathology_counts
        or manual_compile_status_counts
        or manual_compile_rule_counts
        or source_acquisition_rules
        or authority_observation_rules
        or authority_rules
        or lowering_observation_rules
        or lowering_rules
        or blocking_rules
        or saved_bench_diagnostic_rules
        or saved_bench_diagnostic_lanes
    ):
        return ""
    text = (
        "rejection_rules: "
        f"feed_parse={_format_count_map(feed_rules)} "
        f"feed_observation={_format_count_map(feed_observation_rules)} "
        f"source_parse={_format_count_map(source_parse_rules)} "
        f"source_parse_observation={_format_count_map(source_parse_observation_rules)} "
        f"bench_exception={_format_count_map(bench_exception_rules)} "
        f"effect_source_pathology={_format_count_map(effect_source_pathology_counts)} "
        f"manual_compile_status={_format_count_map(manual_compile_status_counts)} "
        f"manual_compile_rule={_format_count_map(manual_compile_rule_counts)} "
        f"source_acquisition={_format_count_map(source_acquisition_rules)} "
        f"bench_authority={_format_count_map(authority_rules)} "
        f"lowering_observation={_format_count_map(lowering_observation_rules)} "
        f"lowering={_format_count_map(lowering_rules)} "
        f"blocking_lowering={_format_count_map(blocking_rules)}"
    )
    if authority_observation_rules:
        text += (
            f" bench_authority_observation={_format_count_map(authority_observation_rules)}"
        )
    if saved_bench_diagnostic_rules or saved_bench_diagnostic_lanes:
        text += (
            f" saved_bench_diagnostic_rules={_format_count_map(saved_bench_diagnostic_rules)}"
            f" saved_bench_diagnostic_lanes={_format_count_map(saved_bench_diagnostic_lanes)}"
        )
    return text


def _format_saved_bench_feed_count_error(row: object) -> str:
    error = str(getattr(row, "effect_feed_count_error", "") or "").strip()
    if not error:
        return ""
    return f"saved_bench_feed_count_error: {error}"


def _format_replay_adjudication_sample(row: Mapping[str, Any]) -> str:
    parts = [f"kind={row.get('kind') or 'unknown'}"]
    for key in (
        "source_statute",
        "op_id",
        "target",
        "text_match",
        "replacement_text",
        "duplicate_kind",
        "path",
        "root",
        "left",
        "right",
        "shared_token_count",
        "excerpt",
    ):
        value = str(row.get(key) or "")
        if value:
            parts.append(f"{key}={value}")
    return " ".join(parts)


def _print_replay_adjudication_samples_for_row(row: Mapping[str, Any]) -> None:
    samples = tuple(row.get("replay_adjudication_samples") or ())
    if not samples:
        return
    print(
        "  replay_adjudications: "
        f"{_format_count_map(row.get('replay_adjudication_kind_counts'))} "
        f"samples={len(samples)} "
        f"omitted={int(row.get('replay_adjudication_samples_omitted') or 0)}"
    )
    for sample in samples:
        if isinstance(sample, Mapping):
            print(f"    sample: {_format_replay_adjudication_sample(sample)}")


def _write_jsonl_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return len(rows)


def _attach_replay_adjudication_evidence_report(
    report: dict[str, Any],
    evidence_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if evidence_report is not None:
        report["replay_adjudication_evidence_jsonl"] = dict(evidence_report)
    return report


def _attach_residual_claim_evidence_report(
    report: dict[str, Any],
    evidence_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if evidence_report is not None:
        report["residual_claim_evidence_jsonl"] = dict(evidence_report)
    return report


def _format_replay_adjudication_evidence_report(
    evidence_report: Mapping[str, Any],
) -> str:
    kinds = ",".join(str(kind) for kind in evidence_report.get("kinds") or [])
    return (
        "Replay adjudication evidence JSONL: "
        f"{evidence_report.get('path')} rows={evidence_report.get('rows')} "
        f"kinds={kinds}"
    )


def _format_residual_claim_evidence_report(
    evidence_report: Mapping[str, Any],
) -> str:
    return (
        "Residual claim evidence JSONL: "
        f"{evidence_report.get('path')} rows={evidence_report.get('rows')}"
    )


def _replay_applicable_effects_with_budget(
    effects,  # noqa: ANN001
    *,
    effect_budget: int | None,
    applicability_mode: str = "effective_date_plus_feed_applied",
    allow_metadata_only_effects: bool = True,
    selection_observations_out: list[dict[str, Any]] | None = None,
) -> tuple[list[Any], int, int, bool]:
    replay_applicable_effects = []
    for effect in effects:
        effect_id = str(getattr(effect, "effect_id", "") or "")
        if bool(getattr(effect, "metadata_only", False)) and not allow_metadata_only_effects:
            if selection_observations_out is not None:
                selection_observations_out.append(
                    {
                        "rule_id": "uk_effect_metadata_only_selection_rejected",
                        "family": "applicability_filter",
                        "phase": "candidate_selection",
                        "effect_id": effect_id,
                        "effect_type": str(getattr(effect, "effect_type", "") or ""),
                        "affected_provisions": str(getattr(effect, "affected_provisions", "") or ""),
                        "affecting_act_id": str(getattr(effect, "affecting_act_id", "") or ""),
                        "affecting_provisions": str(getattr(effect, "affecting_provisions", "") or ""),
                        "applicability_mode": applicability_mode,
                        "allow_metadata_only_effects": allow_metadata_only_effects,
                        "reason": "UK candidate effect inspection excluded metadata-only effect rows for this replay regime.",
                        "blocking": False,
                        "strict_disposition": "record",
                        "quirks_disposition": "record",
                    }
                )
            continue
        replay_applicable = effect.is_applicable_for_replay(applicability_mode=applicability_mode)
        if not replay_applicable:
            if selection_observations_out is not None:
                selection_observations_out.append(
                    {
                        "rule_id": "uk_effect_replay_applicability_selection_rejected",
                        "family": "applicability_filter",
                        "phase": "candidate_selection",
                        "effect_id": effect_id,
                        "effect_type": str(getattr(effect, "effect_type", "") or ""),
                        "affected_provisions": str(getattr(effect, "affected_provisions", "") or ""),
                        "affecting_act_id": str(getattr(effect, "affecting_act_id", "") or ""),
                        "affecting_provisions": str(getattr(effect, "affecting_provisions", "") or ""),
                        "applicability_mode": applicability_mode,
                        "allow_metadata_only_effects": allow_metadata_only_effects,
                        "reason": "UK candidate effect inspection excluded an effect row that is outside the replay applicability regime.",
                        "blocking": False,
                        "strict_disposition": "record",
                        "quirks_disposition": "record",
                    }
                )
            continue
        replay_applicable_effects.append(effect)
    applied_effects = [effect for effect in effects if effect.applied]
    if effect_budget is None:
        return (
            replay_applicable_effects,
            len(replay_applicable_effects),
            len(applied_effects),
            False,
        )
    if len(replay_applicable_effects) > effect_budget and selection_observations_out is not None:
        skipped = replay_applicable_effects[effect_budget:]
        selection_observations_out.append(
            {
                "rule_id": "uk_effect_inspection_budget_excluded",
                "family": "budget_filter",
                "phase": "candidate_selection",
                "effect_budget": effect_budget,
                "available_replay_applicable_effect_count": len(replay_applicable_effects),
                "skipped_effect_count": len(skipped),
                "skipped_effect_ids_sample": [
                    str(getattr(effect, "effect_id", "") or "") for effect in skipped[:20]
                ],
                "applicability_mode": applicability_mode,
                "allow_metadata_only_effects": allow_metadata_only_effects,
                "reason": "UK candidate effect inspection stopped at the configured effect budget.",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            }
        )
    return (
        replay_applicable_effects[:effect_budget],
        len(replay_applicable_effects),
        len(applied_effects),
        len(replay_applicable_effects) > effect_budget,
    )


def _include_candidate_row(
    *,
    residual_only: bool,
    residual_candidate_count: int,
    residual_analysis_skipped: bool,
    effect_inspection_truncated: bool,
    residual_analysis_unavailable: bool = False,
) -> bool:
    if not residual_only:
        return True
    return (
        residual_candidate_count > 0
        or residual_analysis_skipped
        or residual_analysis_unavailable
        or effect_inspection_truncated
    )


def _residual_analysis_unavailable_reason(context) -> str:  # noqa: ANN001
    if bool(getattr(context, "enacted_missing", False)) or getattr(context, "enacted_ir", None) is None:
        return "enacted_missing"
    if bool(getattr(context, "oracle_missing", False)) or not bool(getattr(context, "oracle_eids", set())):
        return "oracle_missing_or_empty"
    return ""


def _summarize_effect_inventory(
    effect_summaries,
    *,
    effect_report_rows=(),
    statute_id: str = "",
):
    source_counts: Counter[str] = Counter()
    compare_counts: Counter[str] = Counter()
    candidate_source_counts: Counter[str] = Counter()
    candidate_compare_counts: Counter[str] = Counter()
    non_candidate_source_counts: Counter[str] = Counter()
    non_candidate_compare_counts: Counter[str] = Counter()
    lowering_observation_rule_counts: Counter[str] = Counter()
    lowering_rejection_rule_counts: Counter[str] = Counter()
    blocking_lowering_rejection_rule_counts: Counter[str] = Counter()
    source_acquisition_observation_rule_counts: Counter[str] = Counter()
    source_acquisition_rejection_rule_counts: Counter[str] = Counter()
    manual_compile_status_counts: Counter[str] = Counter()
    manual_compile_rule_counts: Counter[str] = Counter()
    suggested_claim_template_status_counts: Counter[str] = Counter()
    rows_with_lowering_observations = 0
    rows_with_blocking_lowering_rejections = 0
    rows_with_source_acquisition_observations = 0
    rows_with_source_acquisition_rejections = 0
    inspected_effect_count = 0
    candidate_count = 0
    candidate_ops = 0
    candidate_summaries = []
    for summary in effect_summaries:
        inspected_effect_count += 1
        if summary.source_pathology:
            source_counts[summary.source_pathology] += 1
        if summary.compare_shape:
            compare_counts[summary.compare_shape] += 1
        if getattr(summary, "manual_compile_status", ""):
            manual_compile_status_counts[summary.manual_compile_status] += 1
        if getattr(summary, "manual_compile_rule_id", ""):
            manual_compile_rule_counts[summary.manual_compile_rule_id] += 1
        lowering_observations = tuple(summary.lowering_rejections)
        lowering_rejections = _blocking_rows(lowering_observations)
        if lowering_observations:
            rows_with_lowering_observations += 1
        for observation in lowering_observations:
            rule_id = str(observation.get("rule_id") or "unknown")
            lowering_observation_rule_counts[rule_id] += 1
        if lowering_rejections:
            rows_with_blocking_lowering_rejections += 1
        for rejection in lowering_rejections:
            rule_id = str(rejection.get("rule_id") or "unknown")
            lowering_rejection_rule_counts[rule_id] += 1
            blocking_lowering_rejection_rule_counts[rule_id] += 1
        source_acquisition_observations = tuple(summary.source_acquisition_rejections)
        source_acquisition_rejections = _blocking_rows(source_acquisition_observations)
        if source_acquisition_observations:
            rows_with_source_acquisition_observations += 1
        for observation in source_acquisition_observations:
            rule_id = str(observation.get("rule_id") or "unknown")
            source_acquisition_observation_rule_counts[rule_id] += 1
        if source_acquisition_rejections:
            rows_with_source_acquisition_rejections += 1
        for rejection in source_acquisition_rejections:
            rule_id = str(rejection.get("rule_id") or "unknown")
            source_acquisition_rejection_rule_counts[rule_id] += 1
        if summary.candidate:
            candidate_count += 1
            candidate_ops += summary.n_ops
            candidate_summaries.append(summary)
            if summary.source_pathology:
                candidate_source_counts[summary.source_pathology] += 1
            if summary.compare_shape:
                candidate_compare_counts[summary.compare_shape] += 1
        else:
            if summary.source_pathology:
                non_candidate_source_counts[summary.source_pathology] += 1
            if summary.compare_shape:
                non_candidate_compare_counts[summary.compare_shape] += 1
    if effect_report_rows and statute_id:
        from lawvm.tools.uk_effects import _actionable_claim_template_status

        for row in effect_report_rows:
            template_status = _actionable_claim_template_status(
                statute_id=statute_id,
                row=row,
            )
            if template_status != "__not_actionable__":
                suggested_claim_template_status_counts[template_status] += 1
    return {
        "source_counts": source_counts,
        "compare_counts": compare_counts,
        "candidate_source_counts": candidate_source_counts,
        "candidate_compare_counts": candidate_compare_counts,
        "non_candidate_source_counts": non_candidate_source_counts,
        "non_candidate_compare_counts": non_candidate_compare_counts,
        "manual_compile_status_counts": manual_compile_status_counts,
        "manual_compile_rule_counts": manual_compile_rule_counts,
        "suggested_claim_template_status_counts": suggested_claim_template_status_counts,
        "lowering_observation_rule_counts": lowering_observation_rule_counts,
        "rows_with_lowering_observations": rows_with_lowering_observations,
        "lowering_rejection_rule_counts": lowering_rejection_rule_counts,
        "blocking_lowering_rejection_rule_counts": blocking_lowering_rejection_rule_counts,
        "source_acquisition_observation_rule_counts": (
            source_acquisition_observation_rule_counts
        ),
        "rows_with_source_acquisition_observations": (
            rows_with_source_acquisition_observations
        ),
        "source_acquisition_rejection_rule_counts": source_acquisition_rejection_rule_counts,
        "rows_with_source_acquisition_rejections": rows_with_source_acquisition_rejections,
        "rows_with_blocking_lowering_rejections": rows_with_blocking_lowering_rejections,
        "inspected_effect_count": inspected_effect_count,
        "candidate_count": candidate_count,
        "candidate_ops": candidate_ops,
        "candidate_summaries": candidate_summaries,
    }


def _residual_candidate_inventory(
    candidate_summaries,
    *,
    residual_roots: set[str],
    only_in_replayed: set[str],
    only_in_oracle: set[str],
) -> dict[str, Any]:
    def _sample_priority(summary) -> tuple[int, int, int, int]:  # noqa: ANN001
        return (
            1 if bool(getattr(summary, "structural_for_replay", False)) else 0,
            1 if bool(getattr(summary, "replay_applicable", False)) else 0,
            1 if not str(getattr(summary, "source_pathology", "") or "") else 0,
            int(getattr(summary, "n_ops", 0) or 0),
        )

    def _target_presence_bucket(summary) -> str:  # noqa: ANN001
        resolver_count = len(tuple(getattr(summary, "resolver_eids", ())))
        if resolver_count <= 0:
            return "no_resolver_eids"
        oracle_hit_count = sum(
            1 for hit in tuple(getattr(summary, "oracle_target_hits", ())) if hit
        )
        if oracle_hit_count <= 0:
            return "oracle_targets_absent"
        if oracle_hit_count >= resolver_count:
            return "oracle_targets_all_present"
        return "oracle_targets_partly_present"

    residual_candidate_count = 0
    residual_candidate_ops = 0
    residual_root_hits: set[str] = set()
    residual_root_hit_counts: Counter[str] = Counter()
    residual_root_side_counts: Counter[str] = Counter()
    residual_root_structural_counts: dict[str, Counter[str]] = {}
    source_pathology_counts: Counter[str] = Counter()
    compare_shape_counts: Counter[str] = Counter()
    structural_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    target_presence_counts: Counter[str] = Counter()
    target_presence_action_counts: Counter[str] = Counter()
    manual_compile_rule_counts: Counter[str] = Counter()
    residual_candidate_samples: list[dict[str, Any]] = []
    residual_candidate_root_samples_by_root: dict[str, dict[str, Any]] = {}
    residual_candidate_root_sample_priorities: dict[str, tuple[int, int, int, int]] = {}
    replayed_roots, oracle_roots = _collect_residual_root_sides(
        only_in_replayed=only_in_replayed,
        only_in_oracle=only_in_oracle,
    )
    for summary in candidate_summaries:
        if _effect_overlaps_residual(
            summary.resolver_eids,
            only_in_replayed=only_in_replayed,
            only_in_oracle=only_in_oracle,
        ):
            residual_candidate_count += 1
            residual_candidate_ops += summary.n_ops
            overlapping_roots = _candidate_overlapping_root_hits(
                summary.resolver_eids,
                residual_roots=residual_roots,
                only_in_replayed=only_in_replayed,
                only_in_oracle=only_in_oracle,
            )
            residual_root_hit_counts.update(overlapping_roots)
            for root in overlapping_roots:
                if root in replayed_roots and root in oracle_roots:
                    residual_root_side_counts["both"] += 1
                elif root in replayed_roots:
                    residual_root_side_counts["replayed_only"] += 1
                elif root in oracle_roots:
                    residual_root_side_counts["oracle_only"] += 1
                else:
                    residual_root_side_counts["unknown"] += 1
            source_pathology_counts[
                str(getattr(summary, "source_pathology", "") or "__none__")
            ] += 1
            compare_shape_counts[
                str(getattr(summary, "compare_shape", "") or "__none__")
            ] += 1
            structural_counts[
                "structural_for_replay"
                if bool(getattr(summary, "structural_for_replay", False))
                else "non_structural_for_replay"
            ] += 1
            op_actions = tuple(str(action) for action in getattr(summary, "op_actions", ()))
            if op_actions:
                action_family = "+".join(sorted(set(op_actions)))
            else:
                action_family = "__none__"
            action_counts[action_family] += 1
            target_presence_bucket = _target_presence_bucket(summary)
            target_presence_counts[target_presence_bucket] += 1
            target_presence_action_counts[f"{target_presence_bucket}:{action_family}"] += 1
            structural_key = (
                "structural_for_replay"
                if bool(getattr(summary, "structural_for_replay", False))
                else "non_structural_for_replay"
            )
            for root in overlapping_roots:
                residual_root_structural_counts.setdefault(root, Counter())[structural_key] += 1
            manual_rule_id = str(getattr(summary, "manual_compile_rule_id", "") or "")
            if manual_rule_id:
                manual_compile_rule_counts[manual_rule_id] += 1
            sample = {
                "effect_id": str(getattr(summary, "effect_id", "") or ""),
                "effect_type": str(getattr(summary, "effect_type", "") or ""),
                "affected_provisions": str(getattr(summary, "affected_provisions", "") or ""),
                "affecting_act_id": str(getattr(summary, "affecting_act_id", "") or ""),
                "affecting_provisions": str(getattr(summary, "affecting_provisions", "") or ""),
                "effective_date": str(getattr(summary, "effective_date", "") or ""),
                "resolver_eids": list(summary.resolver_eids),
                "overlapping_residual_roots": sorted(overlapping_roots),
                "source_pathology": str(getattr(summary, "source_pathology", "") or ""),
                "compare_shape": str(getattr(summary, "compare_shape", "") or ""),
                "compiled_op_count": int(getattr(summary, "n_ops", 0) or 0),
                "op_actions": list(op_actions),
                "replay_applicable": bool(getattr(summary, "replay_applicable", False)),
                "structural_for_replay": bool(getattr(summary, "structural_for_replay", False)),
                "manual_compile_status": str(
                    getattr(summary, "manual_compile_status", "") or ""
                ),
                "manual_compile_rule_id": str(
                    getattr(summary, "manual_compile_rule_id", "") or ""
                ),
                "target_presence": {
                    "resolver_count": len(tuple(getattr(summary, "resolver_eids", ()))),
                    "base_target_hit_count": sum(
                        1 for hit in tuple(getattr(summary, "base_target_hits", ())) if hit
                    ),
                    "oracle_target_hit_count": sum(
                        1 for hit in tuple(getattr(summary, "oracle_target_hits", ())) if hit
                    ),
                    "base_descendant_hit_count": sum(
                        1
                        for hit in tuple(getattr(summary, "base_descendant_hits", ()))
                        if hit
                    ),
                    "oracle_descendant_hit_count": sum(
                        1
                        for hit in tuple(getattr(summary, "oracle_descendant_hits", ()))
                        if hit
                    ),
                },
            }
            sample_priority = _sample_priority(summary)
            for root in sorted(overlapping_roots):
                existing_priority = residual_candidate_root_sample_priorities.get(root)
                if existing_priority is None or sample_priority > existing_priority:
                    residual_candidate_root_sample_priorities[root] = sample_priority
                    residual_candidate_root_samples_by_root[root] = sample
            if len(residual_candidate_samples) < _RESIDUAL_CANDIDATE_SAMPLE_LIMIT:
                residual_candidate_samples.append(sample)
            residual_root_hits.update(overlapping_roots)
    root_sample_rows = [
        {
            "root": root,
            "candidate_count": int(count),
            "structural_counts": _count_map_jsonable(
                residual_root_structural_counts.get(root, {})
            ),
            "sample": residual_candidate_root_samples_by_root[root],
        }
        for root, count in sorted(
            residual_root_hit_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )[:_RESIDUAL_CANDIDATE_ROOT_SAMPLE_LIMIT]
        if root in residual_candidate_root_samples_by_root
    ]
    return {
        "residual_candidate_count": residual_candidate_count,
        "residual_candidate_ops": residual_candidate_ops,
        "residual_root_hits": residual_root_hits,
        "residual_root_hit_counts": _count_map_jsonable(residual_root_hit_counts),
        "residual_root_side_counts": _count_map_jsonable(residual_root_side_counts),
        "residual_candidate_source_pathology_counts": _count_map_jsonable(
            source_pathology_counts
        ),
        "residual_candidate_compare_shape_counts": _count_map_jsonable(
            compare_shape_counts
        ),
        "residual_candidate_structural_counts": _count_map_jsonable(structural_counts),
        "residual_candidate_action_counts": _count_map_jsonable(action_counts),
        "residual_candidate_target_presence_counts": _count_map_jsonable(
            target_presence_counts
        ),
        "residual_candidate_target_presence_action_counts": _count_map_jsonable(
            target_presence_action_counts
        ),
        "residual_candidate_manual_compile_rule_counts": _count_map_jsonable(
            manual_compile_rule_counts
        ),
        "residual_candidate_root_samples": root_sample_rows,
        "residual_candidate_root_samples_omitted": max(
            0,
            len(residual_root_hit_counts) - len(root_sample_rows),
        ),
        "residual_candidate_samples": residual_candidate_samples,
        "residual_candidate_samples_omitted": max(
            0,
            residual_candidate_count - len(residual_candidate_samples),
        ),
    }


def main(args: "argparse.Namespace") -> None:
    from lawvm.tools.uk_bench import _load_run

    label: str = args.label
    top: int = getattr(args, "top", 15)
    fast: bool = bool(getattr(args, "fast", False))
    effect_budget: int | None = getattr(args, "effect_budget", None)
    residual_budget: int | None = getattr(args, "residual_budget", None)
    score_mode: str = str(getattr(args, "score_mode", "auto") or "auto")
    residual_only: bool = bool(getattr(args, "residual_only", False))
    json_output: bool = bool(getattr(args, "json", False))
    summary_only: bool = bool(getattr(args, "summary_only", False))
    compact_json: bool = bool(getattr(args, "compact_json", False))
    summary_count_limit: int | None = getattr(args, "summary_count_limit", None)
    row_count_limit: int | None = getattr(args, "row_count_limit", None)
    claim_template_status: str = str(getattr(args, "claim_template_status", "") or "")
    min_year: int | None = getattr(args, "min_year", None)
    max_year: int | None = getattr(args, "max_year", None)
    type_args = getattr(args, "types", None)
    types = {str(t).strip() for t in type_args} if type_args else None
    replay_adjudication_kinds = _replay_adjudication_kinds_from_args(
        getattr(args, "replay_adjudication_kind", None)
    )
    replay_adjudication_sample_limit = int(
        getattr(args, "replay_adjudication_sample_limit", 5) or 0
    )
    db_arg = getattr(args, "db", None)
    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    manual_compile_evidence_jsonl_arg = (
        getattr(args, "manual_compile_evidence_jsonl", "") or ""
    )
    manual_compile_evidence_jsonl_path = (
        Path(manual_compile_evidence_jsonl_arg)
        if manual_compile_evidence_jsonl_arg
        else None
    )
    replay_adjudication_evidence_jsonl_arg = (
        getattr(args, "replay_adjudication_evidence_jsonl", "") or ""
    )
    replay_adjudication_evidence_jsonl_path = (
        Path(replay_adjudication_evidence_jsonl_arg)
        if replay_adjudication_evidence_jsonl_arg
        else None
    )
    residual_claim_evidence_jsonl_arg = (
        getattr(args, "residual_claim_evidence_jsonl", "") or ""
    )
    residual_claim_evidence_jsonl_path = (
        Path(residual_claim_evidence_jsonl_arg)
        if residual_claim_evidence_jsonl_arg
        else None
    )
    manual_compile_evidence_statuses = _manual_compile_evidence_statuses_from_args(
        getattr(args, "manual_compile_evidence_status", None)
    )

    if summary_only and not json_output:
        print("error: --summary-only requires --json for uk-candidates", file=sys.stderr)
        sys.exit(2)
    if compact_json and not json_output:
        print("error: --compact-json requires --json for uk-candidates", file=sys.stderr)
        sys.exit(2)
    if summary_count_limit is not None and not json_output:
        print("error: --summary-count-limit requires --json for uk-candidates", file=sys.stderr)
        sys.exit(2)
    if summary_count_limit is not None and summary_count_limit < 1:
        print("error: --summary-count-limit must be a positive integer", file=sys.stderr)
        sys.exit(2)
    if row_count_limit is not None and not json_output:
        print("error: --row-count-limit requires --json for uk-candidates", file=sys.stderr)
        sys.exit(2)
    if row_count_limit is not None and row_count_limit < 1:
        print("error: --row-count-limit must be a positive integer", file=sys.stderr)
        sys.exit(2)
    if top < 0:
        print("error: --top must be zero or a positive integer", file=sys.stderr)
        sys.exit(2)
    if effect_budget is not None and effect_budget < 1:
        print("error: --effect-budget must be a positive integer", file=sys.stderr)
        sys.exit(2)
    if residual_budget is not None and residual_budget < 0:
        print("error: --residual-budget must be zero or a positive integer", file=sys.stderr)
        sys.exit(2)
    if claim_template_status and not db_path.exists():
        print(
            "error: --claim-template-status requires an archive DB for per-effect inspection",
            file=sys.stderr,
        )
        sys.exit(2)
    if claim_template_status and fast and not residual_only:
        print(
            "error: --claim-template-status requires archive-backed mode; omit --fast or use --residual-only",
            file=sys.stderr,
        )
        sys.exit(2)
    if replay_adjudication_sample_limit < 0:
        print(
            "error: --replay-adjudication-sample-limit must be zero or a positive integer",
            file=sys.stderr,
        )
        sys.exit(2)
    if fast and manual_compile_evidence_jsonl_path is not None:
        print(
            "error: --manual-compile-evidence-jsonl requires archive-backed mode; omit --fast",
            file=sys.stderr,
        )
        sys.exit(2)
    if not fast and not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)
    if fast and residual_only and not db_path.exists():
        print(
            "error: --fast --residual-only requires an archive DB for residual analysis",
            file=sys.stderr,
        )
        sys.exit(2)

    results = _load_saved_bench_run(
        _load_run,
        label,
        include_diagnostics=not summary_only,
    )
    matching_frontier = _matching_frontier(
        results,
        score_mode=score_mode,
        min_year=min_year,
        max_year=max_year,
        types=types,
    )
    replay_adjudication_prefilter_count = len(matching_frontier)
    if replay_adjudication_kinds:
        matching_frontier = [
            result
            for result in matching_frontier
            if _result_has_replay_adjudication_kind(
                result,
                kinds=replay_adjudication_kinds,
            )
        ]
    frontier = matching_frontier[:top]
    replay_adjudication_evidence_jsonl_count = 0
    if replay_adjudication_evidence_jsonl_path is not None:
        replay_adjudication_evidence_jsonl_count = _write_jsonl_rows(
            replay_adjudication_evidence_jsonl_path,
            _replay_adjudication_evidence_rows(
                frontier,
                label=label,
                kinds=replay_adjudication_kinds,
                score_mode=score_mode,
            ),
        )
    replay_adjudication_evidence_jsonl_report = (
        {
            "path": str(replay_adjudication_evidence_jsonl_path),
            "rows": replay_adjudication_evidence_jsonl_count,
            "kinds": sorted(replay_adjudication_kinds),
        }
        if replay_adjudication_evidence_jsonl_path is not None
        else None
    )
    residual_claim_evidence_jsonl_report = (
        {
            "path": str(residual_claim_evidence_jsonl_path),
            "rows": 0,
        }
        if residual_claim_evidence_jsonl_path is not None
        else None
    )
    filters_json = _uk_candidates_filters_jsonable(
        top=top,
        score_mode=score_mode,
        residual_only=residual_only,
        fast=fast,
        effect_budget=effect_budget,
        residual_budget=residual_budget,
        min_year=min_year,
        max_year=max_year,
        types=types,
        replay_adjudication_kinds=replay_adjudication_kinds,
        replay_adjudication_sample_limit=replay_adjudication_sample_limit,
        manual_compile_evidence_statuses=(
            manual_compile_evidence_statuses
            if manual_compile_evidence_jsonl_path is not None
            else None
        ),
        claim_template_status=claim_template_status,
        compact_json=compact_json,
        summary_count_limit=summary_count_limit,
        row_count_limit=row_count_limit,
    )

    if not json_output:
        print(f"=== UK Candidates: {label} ===")
        print(f"Top rows inspected: {len(frontier)}")
        if len(frontier) < len(matching_frontier):
            print(f"Matching frontier rows before --top: {len(matching_frontier)}")
    if not json_output and (
        min_year is not None
        or max_year is not None
        or types is not None
        or replay_adjudication_kinds
    ):
        parts: list[str] = []
        if min_year is not None:
            parts.append(f"min_year={min_year}")
        if max_year is not None:
            parts.append(f"max_year={max_year}")
        if types is not None:
            parts.append(f"types={','.join(sorted(types))}")
        if replay_adjudication_kinds:
            parts.append(
                "replay_adjudication_kind="
                + ",".join(sorted(replay_adjudication_kinds))
            )
        parts.append(f"score_mode={score_mode}")
        print(f"Filters: {' '.join(parts)}")
    elif not json_output:
        print(f"Score mode: {score_mode}")
    if not frontier:
        summary_rows = (
            [
                _saved_bench_prefilter_candidate_row_jsonable(
                    result,
                    score_mode=score_mode,
                    replay_adjudication_kinds=replay_adjudication_kinds,
                    replay_adjudication_sample_limit=replay_adjudication_sample_limit,
                )
                for result in matching_frontier
            ]
            if summary_only
            else []
        )
        manual_compile_evidence_jsonl_count = 0
        if manual_compile_evidence_jsonl_path is not None:
            manual_compile_evidence_jsonl_count = _write_jsonl_rows(
                manual_compile_evidence_jsonl_path,
                [],
            )
        residual_claim_evidence_jsonl_report = (
            _write_residual_claim_evidence_report_from_candidate_rows(
                residual_claim_evidence_jsonl_path,
                [],
                label=label,
            )
        )
        if json_output:
            report = _uk_candidates_report_jsonable(
                label=label,
                rows=summary_rows,
                filters=filters_json,
                inspected_count=0,
                matched_frontier_count=len(matching_frontier),
                replay_adjudication_prefilter_count=replay_adjudication_prefilter_count,
                summary_only=summary_only,
                compact_rows=compact_json,
                emitted_row_count=0,
                summary_count_limit=summary_count_limit,
                row_count_limit=row_count_limit,
            )
            if manual_compile_evidence_jsonl_path is not None:
                report["manual_compile_evidence_jsonl"] = {
                    "path": str(manual_compile_evidence_jsonl_path),
                    "rows": manual_compile_evidence_jsonl_count,
                    "statuses": sorted(manual_compile_evidence_statuses),
                }
            _attach_replay_adjudication_evidence_report(
                report,
                replay_adjudication_evidence_jsonl_report,
            )
            _attach_residual_claim_evidence_report(
                report,
                residual_claim_evidence_jsonl_report,
            )
            print(json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ))
        elif manual_compile_evidence_jsonl_path is not None:
            print(
                "Manual compile evidence JSONL: "
                f"{manual_compile_evidence_jsonl_path} "
                f"rows={manual_compile_evidence_jsonl_count} "
                f"statuses={','.join(sorted(manual_compile_evidence_statuses))}"
            )
        if (
            not json_output
            and replay_adjudication_evidence_jsonl_report is not None
        ):
            print(
                _format_replay_adjudication_evidence_report(
                    replay_adjudication_evidence_jsonl_report
                )
            )
        if (
            not json_output
            and residual_claim_evidence_jsonl_report is not None
        ):
            print(
                _format_residual_claim_evidence_report(
                    residual_claim_evidence_jsonl_report
                )
            )
        return
    if not json_output:
        print()

    if fast:
        if residual_only and db_path.exists():
            from farchive import Farchive
            from lawvm.tools.uk_effects import (
                _EffectReportRow,
                build_uk_effect_summary_context,
                summarize_uk_effect,
            )
            from lawvm.tools.uk_effect import _collect_statute_eids
            from lawvm.uk_legislation.source_adjudication import (
                normalize_uk_replay_compare_eids,
            )
            from lawvm.uk_legislation.effects import load_effects_for_statute_from_archive
            from lawvm.uk_legislation.uk_amendment_replay import (
                UKReplayPipeline,
            )

            report_rows: list[dict[str, Any]] = []
            residual_rows_analyzed = 0
            with Farchive(db_path) as archive:
                for r in frontier:
                    parse_rejections: list[dict[str, Any]] = []
                    effect_selection_observations: list[dict[str, Any]] = []
                    effects = load_effects_for_statute_from_archive(
                        r.statute_id,
                        archive,
                        parse_rejections_out=parse_rejections,
                    )
                    replay_regime = _uk_replay_regime_kwargs_from_bench_row(r)
                    (
                        replay_applicable_effects,
                        available_replay_applicable_effect_count,
                        available_applied_effect_count,
                        effect_inspection_truncated,
                    ) = (
                        _replay_applicable_effects_with_budget(
                            effects,
                            effect_budget=effect_budget,
                            applicability_mode=str(replay_regime["applicability_mode"]),
                            allow_metadata_only_effects=bool(
                                replay_regime["allow_metadata_only_effects"]
                            ),
                            selection_observations_out=effect_selection_observations,
                        )
                    )
                    context = build_uk_effect_summary_context(r.statute_id, archive=archive)
                    effect_report_rows = [
                        _EffectReportRow(
                            effect=effect,
                            summary=summarize_uk_effect(
                                effect,
                                archive=archive,
                                context=context,
                                applicability_mode=str(replay_regime["applicability_mode"]),
                            ),
                        )
                        for effect in replay_applicable_effects
                    ]
                    effect_summaries = [row.summary for row in effect_report_rows]
                    inventory = _summarize_effect_inventory(
                        effect_summaries,
                        effect_report_rows=effect_report_rows,
                        statute_id=r.statute_id,
                    )
                    source_counts = inventory["source_counts"]
                    compare_counts = inventory["compare_counts"]
                    candidate_count = int(inventory["candidate_count"])
                    candidate_ops = int(inventory["candidate_ops"])
                    candidate_summaries = list(inventory["candidate_summaries"])
                    replay_only: set[str] = set()
                    oracle_only: set[str] = set()
                    residual_analysis_skipped = (
                        residual_budget is not None and residual_rows_analyzed >= residual_budget
                    )
                    residual_analysis_unavailable_reason = ""
                    residual_effect_feed_parse_rejections: list[dict[str, Any]] = []
                    residual_effect_diagnostics: list[dict[str, Any]] = []
                    residual_lowering_rejections: list[dict[str, Any]] = []
                    residual_authority_rejections: list[dict[str, Any]] = []
                    residual_execution_observations: list[dict[str, Any]] = []
                    if residual_analysis_skipped:
                        status = "residual analysis budget skipped"
                    elif context.enacted_ir is not None and context.oracle_eids:
                        residual_rows_analyzed += 1
                        pipeline = UKReplayPipeline(_REPO_ROOT)
                        try:
                            ops = pipeline.compile_ops_for_statute(
                                r.statute_id,
                                archive=archive,
                                allow_metadata_backfill=bool(
                                    replay_regime["allow_metadata_backfill"]
                                ),
                                applicability_mode=str(replay_regime["applicability_mode"]),
                                authority_mode=str(replay_regime["authority_mode"]),
                                allow_metadata_only_effects=bool(
                                    replay_regime["allow_metadata_only_effects"]
                                ),
                                effect_feed_parse_rejections_out=residual_effect_feed_parse_rejections,
                                effect_diagnostics_out=residual_effect_diagnostics,
                                lowering_rejections_out=residual_lowering_rejections,
                                authority_rejections_out=residual_authority_rejections,
                            )
                        except Exception as exc:
                            residual_analysis_unavailable_reason = (
                                f"compile_exception:{type(exc).__name__}"
                            )
                            residual_execution_observations.append(
                                _residual_execution_exception_observation(
                                    statute_id=r.statute_id,
                                    phase="compile",
                                    exc=exc,
                                )
                            )
                            status = "residual comparison execution unavailable"
                        else:
                            try:
                                with contextlib.redirect_stdout(io.StringIO()):
                                    replayed_ir = pipeline.apply_ops(
                                        context.enacted_ir,
                                        ops,
                                        eid_map=context.oracle_eid_map,
                                        text_map=context.oracle_text_map,
                                        allow_oracle_alignment=bool(
                                            replay_regime["allow_oracle_alignment"]
                                        ),
                                    )
                            except Exception as exc:
                                residual_analysis_unavailable_reason = (
                                    f"apply_exception:{type(exc).__name__}"
                                )
                                residual_execution_observations.append(
                                    _residual_execution_exception_observation(
                                        statute_id=r.statute_id,
                                        phase="apply",
                                        exc=exc,
                                    )
                                )
                                status = "residual comparison execution unavailable"
                            else:
                                replayed_eids = _collect_statute_eids(replayed_ir)
                                replay_compare_eids, oracle_compare_eids = (
                                    normalize_uk_replay_compare_eids(
                                        replayed_eids,
                                        context.oracle_eids,
                                    )
                                )
                                replay_only = replay_compare_eids - oracle_compare_eids
                                oracle_only = oracle_compare_eids - replay_compare_eids
                    else:
                        residual_analysis_unavailable_reason = _residual_analysis_unavailable_reason(context)
                        status = "residual comparison source unavailable"
                    residual_analysis_unavailable = bool(residual_analysis_unavailable_reason)
                    if (
                        candidate_count <= 0
                        and not effect_inspection_truncated
                        and not residual_analysis_skipped
                        and not residual_analysis_unavailable
                    ):
                        continue
                    residual_roots = _collect_residual_roots(
                        only_in_replayed=replay_only,
                        only_in_oracle=oracle_only,
                    )
                    replayed_residual_roots, oracle_residual_roots = _collect_residual_root_sides(
                        only_in_replayed=replay_only,
                        only_in_oracle=oracle_only,
                    )
                    residual_inventory = _residual_candidate_inventory(
                        candidate_summaries,
                        residual_roots=residual_roots,
                        only_in_replayed=replay_only,
                        only_in_oracle=oracle_only,
                    )
                    residual_candidate_count = int(residual_inventory["residual_candidate_count"])
                    residual_candidate_ops = int(residual_inventory["residual_candidate_ops"])
                    residual_root_hits = set(residual_inventory["residual_root_hits"])
                    malformed_residual_roots = _collect_malformed_residual_roots(
                        only_in_replayed=replay_only,
                        only_in_oracle=oracle_only,
                    )
                    defeated_residual_roots = (
                        residual_roots - residual_root_hits - malformed_residual_roots
                    )

                    if not _include_candidate_row(
                        residual_only=True,
                        residual_candidate_count=residual_candidate_count,
                        residual_analysis_skipped=residual_analysis_skipped,
                        residual_analysis_unavailable=residual_analysis_unavailable,
                        effect_inspection_truncated=effect_inspection_truncated,
                    ):
                        continue

                    score = _primary_frontier_score(r, score_mode=score_mode)
                    comparison_class = _effective_comparison_class(r)
                    if not residual_analysis_skipped and not residual_analysis_unavailable:
                        status = _budget_aware_frontier_status(
                            candidate_count=candidate_count,
                            residual_candidate_count=residual_candidate_count,
                            effect_inspection_truncated=effect_inspection_truncated,
                            residual_root_count=len(residual_roots),
                            defeated_residual_root_count=len(defeated_residual_roots),
                            malformed_residual_root_count=len(malformed_residual_roots),
                        )
                    candidate_row = _uk_candidate_row_jsonable(
                        r,
                        score_mode=score_mode,
                        source_counts=source_counts,
                        compare_counts=compare_counts,
                        candidate_source_counts=inventory["candidate_source_counts"],
                        candidate_compare_counts=inventory["candidate_compare_counts"],
                        non_candidate_source_counts=inventory["non_candidate_source_counts"],
                        non_candidate_compare_counts=inventory["non_candidate_compare_counts"],
                        manual_compile_status_counts=inventory.get("manual_compile_status_counts", {}),
                        manual_compile_rule_counts=inventory.get("manual_compile_rule_counts", {}),
                        suggested_claim_template_status_counts=inventory.get(
                            "suggested_claim_template_status_counts",
                            {},
                        ),
                        lowering_observation_rule_counts=inventory.get(
                            "lowering_observation_rule_counts",
                            inventory["lowering_rejection_rule_counts"],
                        ),
                        rows_with_lowering_observations=int(
                            inventory.get("rows_with_lowering_observations", 0)
                        ),
                        lowering_rejection_rule_counts=inventory["lowering_rejection_rule_counts"],
                        blocking_lowering_rejection_rule_counts=inventory[
                            "blocking_lowering_rejection_rule_counts"
                        ],
                        source_acquisition_observation_rule_counts=inventory.get(
                            "source_acquisition_observation_rule_counts",
                            {},
                        ),
                        rows_with_source_acquisition_observations=int(
                            inventory.get("rows_with_source_acquisition_observations", 0)
                        ),
                        source_acquisition_rejection_rule_counts=inventory[
                            "source_acquisition_rejection_rule_counts"
                        ],
                        rows_with_source_acquisition_rejections=int(
                            inventory["rows_with_source_acquisition_rejections"]
                        ),
                        rows_with_blocking_lowering_rejections=int(
                            inventory["rows_with_blocking_lowering_rejections"]
                        ),
                        inspected_effect_count=int(inventory["inspected_effect_count"]),
                        available_replay_applicable_effect_count=(
                            available_replay_applicable_effect_count
                        ),
                        available_applied_effect_count=available_applied_effect_count,
                        effect_inspection_truncated=effect_inspection_truncated,
                        residual_analysis_skipped=residual_analysis_skipped,
                        residual_analysis_unavailable=residual_analysis_unavailable,
                        residual_analysis_unavailable_reason=residual_analysis_unavailable_reason,
                        residual_analysis_enacted_missing=bool(getattr(context, "enacted_missing", False)),
                        residual_analysis_oracle_missing=bool(getattr(context, "oracle_missing", False)),
                        candidate_count=candidate_count,
                        candidate_ops=candidate_ops,
                        residual_candidate_count=residual_candidate_count,
                        residual_candidate_ops=residual_candidate_ops,
                        residual_roots=residual_roots,
                        replayed_residual_roots=replayed_residual_roots,
                        oracle_residual_roots=oracle_residual_roots,
                        malformed_residual_roots=malformed_residual_roots,
                        residual_root_hits=residual_root_hits,
                        residual_root_hit_counts=residual_inventory[
                            "residual_root_hit_counts"
                        ],
                        residual_root_side_counts=residual_inventory[
                            "residual_root_side_counts"
                        ],
                        residual_candidate_source_pathology_counts=residual_inventory[
                            "residual_candidate_source_pathology_counts"
                        ],
                        residual_candidate_compare_shape_counts=residual_inventory[
                            "residual_candidate_compare_shape_counts"
                        ],
                        residual_candidate_structural_counts=residual_inventory[
                            "residual_candidate_structural_counts"
                        ],
                        residual_candidate_action_counts=residual_inventory[
                            "residual_candidate_action_counts"
                        ],
                        residual_candidate_target_presence_counts=residual_inventory[
                            "residual_candidate_target_presence_counts"
                        ],
                        residual_candidate_target_presence_action_counts=residual_inventory[
                            "residual_candidate_target_presence_action_counts"
                        ],
                        residual_candidate_manual_compile_rule_counts=residual_inventory[
                            "residual_candidate_manual_compile_rule_counts"
                        ],
                        residual_candidate_root_samples=tuple(
                            residual_inventory["residual_candidate_root_samples"]
                        ),
                        residual_candidate_root_samples_omitted=int(
                            residual_inventory["residual_candidate_root_samples_omitted"]
                        ),
                        defeated_residual_roots=defeated_residual_roots,
                        status=status,
                        effect_feed_parse_rejections=tuple(parse_rejections),
                        effect_selection_observations=tuple(effect_selection_observations),
                        residual_effect_feed_parse_rejections=tuple(residual_effect_feed_parse_rejections),
                        residual_effect_source_pathology_observations=(
                            _effect_source_pathology_rows(residual_effect_diagnostics)
                        ),
                        residual_source_acquisition_rejections=(
                            _source_acquisition_rows(residual_effect_diagnostics)
                        ),
                        residual_lowering_rejections=tuple(residual_lowering_rejections),
                        residual_authority_rejections=tuple(residual_authority_rejections),
                        residual_execution_observations=tuple(residual_execution_observations),
                        residual_candidate_samples=tuple(residual_inventory["residual_candidate_samples"]),
                        residual_candidate_samples_omitted=int(
                            residual_inventory["residual_candidate_samples_omitted"]
                        ),
                        replay_adjudication_kinds=replay_adjudication_kinds,
                        replay_adjudication_sample_limit=replay_adjudication_sample_limit,
                    )
                    if not _row_matches_claim_template_status(
                        candidate_row,
                        claim_template_status,
                    ):
                        continue
                    report_rows.append(candidate_row)
                    if json_output:
                        continue
                    print(
                        f"{r.statute_id:<30} frontier={score:.1%} "
                        f"raw={r.score:.1%} replay={r.replay_score:.1%} "
                        f"{_format_candidate_effect_inventory(r)} "
                        f"candidate_effects={candidate_count:3d} "
                        f"candidate_ops={candidate_ops:4d} "
                        f"residual_candidate_effects={residual_candidate_count:3d} "
                        f"residual_candidate_ops={residual_candidate_ops:4d}"
                    )
                    if effect_inspection_truncated:
                        print(
                            f"  budget:   inspected_replay_applicable="
                            f"{inventory['inspected_effect_count']} "
                            f"available_replay_applicable="
                            f"{available_replay_applicable_effect_count} "
                            f"available_applied={available_applied_effect_count}"
                    )
                    print(f"  class:    {comparison_class}")
                    print(f"  sources:  {_format_candidate_source_status(r)}")
                    saved_rule_line = _format_saved_bench_rejection_rules(r)
                    if saved_rule_line:
                        print(f"  saved_bench_{saved_rule_line}")
                    saved_feed_error_line = _format_saved_bench_feed_count_error(r)
                    if saved_feed_error_line:
                        print(f"  {saved_feed_error_line}")
                    _print_replay_adjudication_samples_for_row(report_rows[-1])
                    if residual_analysis_skipped:
                        print("  residual: skipped by --residual-budget")
                    elif residual_analysis_unavailable:
                        print(f"  residual: unavailable ({residual_analysis_unavailable_reason})")
                    if parse_rejections:
                        top_rejections = ", ".join(
                            f"{k}={v}" for k, v in _rejection_rule_counts(tuple(parse_rejections)).items()
                        )
                        print(f"  feed:     {top_rejections}")
                    residual_compile_observation_rule_counts = _merged_rejection_rule_counts(
                        tuple(residual_effect_feed_parse_rejections),
                        _effect_source_pathology_rows(residual_effect_diagnostics),
                        _source_acquisition_rows(residual_effect_diagnostics),
                        tuple(residual_lowering_rejections),
                        tuple(residual_authority_rejections),
                        tuple(residual_execution_observations),
                    )
                    residual_compile_rule_counts = _merged_rejection_rule_counts(
                        _blocking_rows(tuple(residual_effect_feed_parse_rejections)),
                        _blocking_rows(_effect_source_pathology_rows(residual_effect_diagnostics)),
                        _blocking_rows(_source_acquisition_rows(residual_effect_diagnostics)),
                        _blocking_rows(tuple(residual_lowering_rejections)),
                        _blocking_rows(tuple(residual_authority_rejections)),
                        _blocking_rows(tuple(residual_execution_observations)),
                    )
                    if residual_compile_observation_rule_counts:
                        top_residual_observations = ", ".join(
                            f"{k}={v}" for k, v in residual_compile_observation_rule_counts.items()
                        )
                        print(f"  residual_compile_observation: {top_residual_observations}")
                    if residual_compile_rule_counts:
                        top_residual_rejections = ", ".join(
                            f"{k}={v}" for k, v in residual_compile_rule_counts.items()
                        )
                        print(f"  residual_compile: {top_residual_rejections}")
                    if source_counts:
                        top_source = ", ".join(
                            f"{k}={v}" for k, v in source_counts.most_common(3)
                        )
                        print(f"  source:   {top_source}")
                    else:
                        print("  source:   (none)")
                    if compare_counts:
                        top_compare = ", ".join(
                            f"{k}={v}" for k, v in compare_counts.most_common(3)
                        )
                        print(f"  compare:  {top_compare}")
                    else:
                        print("  compare:  (none)")
                    if residual_roots:
                        print(
                            f"  claims:   residual_roots={len(residual_roots):3d} "
                            f"backed_roots={len(residual_root_hits):3d} "
                            f"malformed_roots={len(malformed_residual_roots):3d} "
                            f"defeated_roots={len(defeated_residual_roots):3d}"
                        )
                    print(f"  status:   {status}")
                    print()
            residual_claim_evidence_jsonl_report = (
                _write_residual_claim_evidence_report_from_candidate_rows(
                    residual_claim_evidence_jsonl_path,
                    report_rows,
                    label=label,
                )
            )
            if json_output:
                report = _uk_candidates_report_jsonable(
                        label=label,
                        rows=report_rows,
                        filters=filters_json,
                        inspected_count=len(frontier),
                        matched_frontier_count=len(matching_frontier),
                        replay_adjudication_prefilter_count=replay_adjudication_prefilter_count,
                        summary_only=summary_only,
                        compact_rows=compact_json,
                        summary_count_limit=summary_count_limit,
                        row_count_limit=row_count_limit,
                    )
                _attach_replay_adjudication_evidence_report(
                    report,
                    replay_adjudication_evidence_jsonl_report,
                )
                _attach_residual_claim_evidence_report(
                    report,
                    residual_claim_evidence_jsonl_report,
                )
                print(json.dumps(
                    report,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ))
            elif replay_adjudication_evidence_jsonl_report is not None:
                print(
                    _format_replay_adjudication_evidence_report(
                        replay_adjudication_evidence_jsonl_report
                    )
                )
            if (
                not json_output
                and residual_claim_evidence_jsonl_report is not None
            ):
                print(
                    _format_residual_claim_evidence_report(
                        residual_claim_evidence_jsonl_report
                    )
                )
            return

        report_rows = []
        for r in frontier:
            score = _primary_frontier_score(r, score_mode=score_mode)
            comparison_class = _effective_comparison_class(r)
            status = "frontier prefilter only"
            saved_bench_diagnostic_rows = _saved_bench_diagnostic_rows_from_result(r)
            effect_feed_observation_rows = _saved_effect_feed_observation_rows_from_result(r)
            effect_feed_rejection_rows = _blocking_rows(effect_feed_observation_rows)
            report_rows.append({
                "statute_id": r.statute_id,
                "score_mode": score_mode,
                "frontier_score": score,
                "raw_score": float(getattr(r, "score", -1.0)),
                "replay_score": float(getattr(r, "replay_score", -1.0)),
                "commencement_score": float(getattr(r, "commencement_score", -1.0)),
                "replay_commencement_score": float(getattr(r, "replay_commencement_score", -1.0)),
                "effect_count": int(getattr(r, "n_effects", 0) or 0),
                "effect_row_count": int(getattr(r, "n_effect_rows", getattr(r, "n_effects", 0)) or 0),
                "effect_feed_page_count": int(
                    getattr(r, "n_effect_feed_pages", getattr(r, "n_effects", 0)) or 0
                ),
                "effect_feed_count_error": str(getattr(r, "effect_feed_count_error", "") or ""),
                "inspected_effect_count": 0,
                "inspected_replay_applicable_effect_count": 0,
                "available_replay_applicable_effect_count": 0,
                "available_applied_effect_count": 0,
                "effect_inspection_truncated": False,
                "residual_analysis_skipped": False,
                "residual_analysis_unavailable": False,
                "residual_analysis_unavailable_reason": "",
                "residual_analysis_enacted_missing": False,
                "residual_analysis_oracle_missing": False,
                "comparison_class": comparison_class,
                "core_benchmark": _effective_core_benchmark(r),
                "enacted_source_status": str(getattr(r, "enacted_source_status", "") or "unknown"),
                "oracle_source_status": str(getattr(r, "oracle_source_status", "") or "unknown"),
                "enacted_source_size": int(getattr(r, "enacted_source_size", 0) or 0),
                "oracle_source_size": int(getattr(r, "oracle_source_size", 0) or 0),
                "enacted_source_sha256": str(getattr(r, "enacted_source_sha256", "") or ""),
                "oracle_source_sha256": str(getattr(r, "oracle_source_sha256", "") or ""),
                "enacted_source_url": str(getattr(r, "enacted_source_url", "") or ""),
                "oracle_source_url": str(getattr(r, "oracle_source_url", "") or ""),
                "source_parse_rejection_count": int(
                    getattr(r, "source_parse_rejection_count", 0) or 0
                ),
                "source_parse_rejection_rule_counts": _count_map_from_object(
                    getattr(r, "source_parse_rejection_rule_counts", {})
                ),
                "source_parse_observation_count": int(
                    getattr(r, "source_parse_observation_count", 0) or 0
                ),
                "source_parse_observation_rule_counts": _count_map_from_object(
                    getattr(r, "source_parse_observation_rule_counts", {})
                ),
                "bench_exception_count": int(getattr(r, "bench_exception_count", 0) or 0),
                "bench_exception_rule_counts": _count_map_from_object(
                    getattr(r, "bench_exception_rule_counts", {})
                ),
                "bench_exception_observations": _observation_rows_from_object(
                    getattr(r, "bench_exception_observations", ())
                ),
                "saved_bench_diagnostic_count": len(saved_bench_diagnostic_rows),
                "saved_bench_diagnostic_rule_counts": _rejection_rule_counts(
                    saved_bench_diagnostic_rows
                ),
                "saved_bench_diagnostic_lane_counts": _count_map_jsonable(
                    Counter(
                        str(row.get("diagnostic_lane") or "unknown")
                        for row in saved_bench_diagnostic_rows
                    )
                ),
                "saved_bench_diagnostics": list(saved_bench_diagnostic_rows),
                **_replay_adjudication_fields_from_result(
                    r,
                    kinds=replay_adjudication_kinds,
                    sample_limit=replay_adjudication_sample_limit,
                ),
                "uk_replay_regime": _uk_replay_regime_kwargs_from_bench_row(r),
                "uk_replay_regime_claim": _uk_replay_regime_claim_from_bench_row(r),
                "uk_residual_claim": _uk_residual_claim_from_bench_row(r),
                "bench_authority_observation_count": int(
                    getattr(r, "uk_authority_observation_count", 0) or 0
                ),
                "bench_authority_observation_rule_counts": _count_map_from_object(
                    getattr(r, "uk_authority_observation_rule_counts", {})
                ),
                "bench_authority_rejection_count": int(
                    getattr(r, "uk_authority_rejection_count", 0) or 0
                ),
                "bench_authority_rejection_rule_counts": _count_map_from_object(
                    getattr(r, "uk_authority_rejection_rule_counts", {})
                ),
                "bench_effect_source_pathology_counts": _count_map_from_object(
                    getattr(r, "effect_source_pathology_counts", {})
                ),
                "bench_manual_compile_status_counts": _count_map_from_object(
                    getattr(r, "manual_compile_status_counts", {})
                ),
                "bench_manual_compile_rule_counts": _count_map_from_object(
                    getattr(r, "manual_compile_rule_counts", {})
                ),
                "bench_source_acquisition_rejection_count": int(
                    getattr(r, "source_acquisition_rejection_count", 0) or 0
                ),
                "bench_source_acquisition_rejection_rule_counts": _count_map_from_object(
                    getattr(r, "source_acquisition_rejection_rule_counts", {})
                ),
                "effect_feed_parse_rejection_count": int(
                    getattr(r, "effect_feed_rejection_count", 0) or 0
                ),
                "effect_feed_parse_rejection_rule_counts": _count_map_from_object(
                    getattr(r, "effect_feed_rejection_rule_counts", {})
                ),
                "effect_feed_parse_rejections": list(effect_feed_rejection_rows),
                "effect_feed_observation_count": int(
                    getattr(r, "effect_feed_observation_count", 0) or 0
                ),
                "effect_feed_observation_rule_counts": _count_map_from_object(
                    getattr(r, "effect_feed_observation_rule_counts", {})
                ),
                "effect_feed_observations": list(effect_feed_observation_rows),
                "residual_compile_rejection_count": 0,
                "residual_compile_rejection_rule_counts": {},
                "residual_compile_rejections": {
                    "effect_feed_parse": [],
                    "lowering": [],
                    "authority": [],
                },
                "source_counts": {},
                "compare_counts": {},
                "candidate_source_counts": {},
                "candidate_compare_counts": {},
                "non_candidate_source_counts": {},
                "non_candidate_compare_counts": {},
                "lowering_rejection_count": int(getattr(r, "lowering_rejection_count", 0) or 0),
                "lowering_observation_count": int(
                    getattr(
                        r,
                        "lowering_observation_count",
                        getattr(r, "lowering_rejection_count", 0),
                    )
                    or 0
                ),
                "lowering_observation_rule_counts": _count_map_from_object(
                    getattr(
                        r,
                        "lowering_observation_rule_counts",
                        getattr(r, "lowering_rejection_rule_counts", {}),
                    )
                ),
                "rows_with_lowering_observations": int(
                    bool(
                        getattr(
                            r,
                            "lowering_observation_count",
                            getattr(r, "lowering_rejection_count", 0),
                        )
                        or 0
                    )
                ),
                "lowering_rejection_rule_counts": _count_map_from_object(
                    getattr(r, "lowering_rejection_rule_counts", {})
                ),
                "blocking_lowering_rejection_count": int(
                    getattr(r, "blocking_lowering_rejection_count", 0) or 0
                ),
                "blocking_lowering_rejection_rule_counts": _count_map_from_object(
                    getattr(r, "blocking_lowering_rejection_rule_counts", {})
                ),
                "rows_with_blocking_lowering_rejections": int(
                    bool(getattr(r, "blocking_lowering_rejection_count", 0) or 0)
                ),
                "candidate_effect_count": 0,
                "candidate_op_count": 0,
                "residual_candidate_effect_count": 0,
                "residual_candidate_op_count": 0,
                "residual_roots": [],
                "replayed_residual_roots": [],
                "oracle_residual_roots": [],
                "malformed_residual_roots": [],
                "backed_residual_roots": [],
                "defeated_residual_roots": [],
                "status": status,
                "triage_rule_id": _triage_rule_id(status),
            })
            if json_output:
                continue
            print(
                f"{r.statute_id:<30} frontier={score:.1%} "
                f"raw={r.score:.1%} replay={r.replay_score:.1%} "
                f"{_format_candidate_effect_inventory(r)}"
            )
            print(f"  class:    {comparison_class}")
            print(f"  sources:  {_format_candidate_source_status(r)}")
            print("  source:   (skipped --fast)")
            print("  compare:  (skipped --fast)")
            saved_rule_line = _format_saved_bench_rejection_rules(r)
            if saved_rule_line:
                print(f"  {saved_rule_line}")
            saved_feed_error_line = _format_saved_bench_feed_count_error(r)
            if saved_feed_error_line:
                print(f"  {saved_feed_error_line}")
            _print_replay_adjudication_samples_for_row(report_rows[-1])
            print("  status:   frontier prefilter only")
            print()
        residual_claim_evidence_jsonl_report = (
            _write_residual_claim_evidence_report_from_candidate_rows(
                residual_claim_evidence_jsonl_path,
                report_rows,
                label=label,
            )
        )
        if json_output:
            report = _uk_candidates_report_jsonable(
                    label=label,
                    rows=report_rows,
                    filters=filters_json,
                    inspected_count=len(frontier),
                    matched_frontier_count=len(matching_frontier),
                    replay_adjudication_prefilter_count=replay_adjudication_prefilter_count,
                    summary_only=summary_only,
                    compact_rows=compact_json,
                    summary_count_limit=summary_count_limit,
                    row_count_limit=row_count_limit,
                )
            _attach_replay_adjudication_evidence_report(
                report,
                replay_adjudication_evidence_jsonl_report,
            )
            _attach_residual_claim_evidence_report(
                report,
                residual_claim_evidence_jsonl_report,
            )
            print(json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ))
        elif replay_adjudication_evidence_jsonl_report is not None:
            print()
            print(
                _format_replay_adjudication_evidence_report(
                    replay_adjudication_evidence_jsonl_report
                )
            )
        if (
            not json_output
            and residual_claim_evidence_jsonl_report is not None
        ):
            print(
                _format_residual_claim_evidence_report(
                    residual_claim_evidence_jsonl_report
                )
            )
        return

    from farchive import Farchive
    from lawvm.tools.uk_effects import (
        _EffectReportRow,
        _manual_compile_evidence_row_jsonable,
        build_uk_effect_summary_context,
        summarize_uk_effect,
    )
    from lawvm.tools.uk_effect import _collect_statute_eids
    from lawvm.uk_legislation.source_adjudication import normalize_uk_replay_compare_eids
    from lawvm.uk_legislation.effects import load_effects_for_statute_from_archive
    from lawvm.uk_legislation.uk_amendment_replay import (
        UKReplayPipeline,
    )

    report_rows: list[dict[str, Any]] = []
    manual_compile_evidence_rows: list[dict[str, Any]] = []
    residual_rows_analyzed = 0
    with Farchive(db_path) as archive:
        for r in frontier:
            parse_rejections = []
            effect_selection_observations: list[dict[str, Any]] = []
            effects = load_effects_for_statute_from_archive(
                r.statute_id,
                archive,
                parse_rejections_out=parse_rejections,
            )
            replay_regime = _uk_replay_regime_kwargs_from_bench_row(r)
            (
                replay_applicable_effects,
                available_replay_applicable_effect_count,
                available_applied_effect_count,
                effect_inspection_truncated,
            ) = (
                _replay_applicable_effects_with_budget(
                    effects,
                    effect_budget=effect_budget,
                    applicability_mode=str(replay_regime["applicability_mode"]),
                    allow_metadata_only_effects=bool(replay_regime["allow_metadata_only_effects"]),
                    selection_observations_out=effect_selection_observations,
                )
            )
            context = build_uk_effect_summary_context(r.statute_id, archive=archive)
            effect_report_rows = [
                _EffectReportRow(
                    effect=effect,
                    summary=summarize_uk_effect(
                        effect,
                        archive=archive,
                        context=context,
                        applicability_mode=str(replay_regime["applicability_mode"]),
                    ),
                )
                for effect in replay_applicable_effects
            ]
            effect_summaries = [row.summary for row in effect_report_rows]
            if manual_compile_evidence_jsonl_path is not None:
                manual_compile_evidence_rows.extend(
                    _manual_compile_evidence_row_jsonable(
                        statute_id=r.statute_id,
                        row=effect_report_row,
                        context=context,
                        replay_regime=replay_regime,
                    )
                    for effect_report_row in effect_report_rows
                    if effect_report_row.summary.manual_compile_status
                    in manual_compile_evidence_statuses
                )
            inventory = _summarize_effect_inventory(
                effect_summaries,
                effect_report_rows=effect_report_rows,
                statute_id=r.statute_id,
            )
            replay_only: set[str] = set()
            oracle_only: set[str] = set()
            residual_analysis_skipped = (
                residual_budget is not None and residual_rows_analyzed >= residual_budget
            )
            residual_analysis_unavailable_reason = ""
            residual_effect_feed_parse_rejections: list[dict[str, Any]] = []
            residual_effect_diagnostics: list[dict[str, Any]] = []
            residual_lowering_rejections: list[dict[str, Any]] = []
            residual_authority_rejections: list[dict[str, Any]] = []
            residual_execution_observations: list[dict[str, Any]] = []
            if context.enacted_ir is not None and context.oracle_eids and not residual_analysis_skipped:
                residual_rows_analyzed += 1
                pipeline = UKReplayPipeline(_REPO_ROOT)
                try:
                    ops = pipeline.compile_ops_for_statute(
                        r.statute_id,
                        archive=archive,
                        allow_metadata_backfill=bool(replay_regime["allow_metadata_backfill"]),
                        applicability_mode=str(replay_regime["applicability_mode"]),
                        authority_mode=str(replay_regime["authority_mode"]),
                        allow_metadata_only_effects=bool(replay_regime["allow_metadata_only_effects"]),
                        effect_feed_parse_rejections_out=residual_effect_feed_parse_rejections,
                        effect_diagnostics_out=residual_effect_diagnostics,
                        lowering_rejections_out=residual_lowering_rejections,
                        authority_rejections_out=residual_authority_rejections,
                    )
                except Exception as exc:
                    residual_analysis_unavailable_reason = f"compile_exception:{type(exc).__name__}"
                    residual_execution_observations.append(
                        _residual_execution_exception_observation(
                            statute_id=r.statute_id,
                            phase="compile",
                            exc=exc,
                        )
                    )
                else:
                    try:
                        with contextlib.redirect_stdout(io.StringIO()):
                            replayed_ir = pipeline.apply_ops(
                                context.enacted_ir,
                                ops,
                                eid_map=context.oracle_eid_map,
                                text_map=context.oracle_text_map,
                                allow_oracle_alignment=bool(replay_regime["allow_oracle_alignment"]),
                            )
                    except Exception as exc:
                        residual_analysis_unavailable_reason = f"apply_exception:{type(exc).__name__}"
                        residual_execution_observations.append(
                            _residual_execution_exception_observation(
                                statute_id=r.statute_id,
                                phase="apply",
                                exc=exc,
                            )
                        )
                    else:
                        replayed_eids = _collect_statute_eids(replayed_ir)
                        replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
                            replayed_eids,
                            context.oracle_eids,
                        )
                        replay_only = replay_compare_eids - oracle_compare_eids
                        oracle_only = oracle_compare_eids - replay_compare_eids
            elif not residual_analysis_skipped:
                residual_analysis_unavailable_reason = _residual_analysis_unavailable_reason(context)
            residual_analysis_unavailable = bool(residual_analysis_unavailable_reason)
            residual_roots = _collect_residual_roots(
                only_in_replayed=replay_only,
                only_in_oracle=oracle_only,
            )
            replayed_residual_roots, oracle_residual_roots = _collect_residual_root_sides(
                only_in_replayed=replay_only,
                only_in_oracle=oracle_only,
            )
            source_counts = inventory["source_counts"]
            compare_counts = inventory["compare_counts"]
            candidate_source_counts = inventory["candidate_source_counts"]
            candidate_compare_counts = inventory["candidate_compare_counts"]
            non_candidate_source_counts = inventory["non_candidate_source_counts"]
            non_candidate_compare_counts = inventory["non_candidate_compare_counts"]
            lowering_rejection_rule_counts = inventory["lowering_rejection_rule_counts"]
            lowering_observation_rule_counts = inventory.get(
                "lowering_observation_rule_counts",
                lowering_rejection_rule_counts,
            )
            rows_with_lowering_observations = int(
                inventory.get("rows_with_lowering_observations", 0)
            )
            blocking_lowering_rejection_rule_counts = inventory["blocking_lowering_rejection_rule_counts"]
            source_acquisition_observation_rule_counts = inventory.get(
                "source_acquisition_observation_rule_counts",
                {},
            )
            rows_with_source_acquisition_observations = int(
                inventory.get("rows_with_source_acquisition_observations", 0)
            )
            source_acquisition_rejection_rule_counts = inventory["source_acquisition_rejection_rule_counts"]
            rows_with_source_acquisition_rejections = int(
                inventory["rows_with_source_acquisition_rejections"]
            )
            rows_with_blocking_lowering_rejections = int(
                inventory["rows_with_blocking_lowering_rejections"]
            )
            inspected_effect_count = int(inventory["inspected_effect_count"])
            candidate_count = int(inventory["candidate_count"])
            candidate_ops = int(inventory["candidate_ops"])
            residual_inventory = _residual_candidate_inventory(
                inventory["candidate_summaries"],
                residual_roots=residual_roots,
                only_in_replayed=replay_only,
                only_in_oracle=oracle_only,
            )
            residual_candidate_count = int(residual_inventory["residual_candidate_count"])
            residual_candidate_ops = int(residual_inventory["residual_candidate_ops"])
            residual_root_hits = set(residual_inventory["residual_root_hits"])
            malformed_residual_roots = _collect_malformed_residual_roots(
                only_in_replayed=replay_only,
                only_in_oracle=oracle_only,
            )
            defeated_residual_roots = residual_roots - residual_root_hits - malformed_residual_roots

            score = _primary_frontier_score(r, score_mode=score_mode)
            comparison_class = _effective_comparison_class(r)
            if residual_analysis_skipped:
                status = "residual analysis budget skipped"
            elif residual_analysis_unavailable:
                if residual_analysis_unavailable_reason.startswith(("compile_exception:", "apply_exception:")):
                    status = "residual comparison execution unavailable"
                else:
                    status = "residual comparison source unavailable"
            else:
                status = _budget_aware_frontier_status(
                    candidate_count=candidate_count,
                    residual_candidate_count=residual_candidate_count,
                    effect_inspection_truncated=effect_inspection_truncated,
                    residual_root_count=len(residual_roots),
                    defeated_residual_root_count=len(defeated_residual_roots),
                    malformed_residual_root_count=len(malformed_residual_roots),
                )
            if not _include_candidate_row(
                residual_only=residual_only,
                residual_candidate_count=residual_candidate_count,
                residual_analysis_skipped=residual_analysis_skipped,
                residual_analysis_unavailable=residual_analysis_unavailable,
                effect_inspection_truncated=effect_inspection_truncated,
            ):
                continue
            candidate_row = _uk_candidate_row_jsonable(
                r,
                score_mode=score_mode,
                source_counts=source_counts,
                compare_counts=compare_counts,
                candidate_source_counts=candidate_source_counts,
                candidate_compare_counts=candidate_compare_counts,
                non_candidate_source_counts=non_candidate_source_counts,
                non_candidate_compare_counts=non_candidate_compare_counts,
                manual_compile_status_counts=inventory.get("manual_compile_status_counts", {}),
                manual_compile_rule_counts=inventory.get("manual_compile_rule_counts", {}),
                suggested_claim_template_status_counts=inventory.get(
                    "suggested_claim_template_status_counts",
                    {},
                ),
                lowering_observation_rule_counts=lowering_observation_rule_counts,
                rows_with_lowering_observations=rows_with_lowering_observations,
                lowering_rejection_rule_counts=lowering_rejection_rule_counts,
                blocking_lowering_rejection_rule_counts=blocking_lowering_rejection_rule_counts,
                source_acquisition_observation_rule_counts=source_acquisition_observation_rule_counts,
                rows_with_source_acquisition_observations=rows_with_source_acquisition_observations,
                source_acquisition_rejection_rule_counts=source_acquisition_rejection_rule_counts,
                rows_with_source_acquisition_rejections=rows_with_source_acquisition_rejections,
                rows_with_blocking_lowering_rejections=rows_with_blocking_lowering_rejections,
                inspected_effect_count=inspected_effect_count,
                available_replay_applicable_effect_count=available_replay_applicable_effect_count,
                available_applied_effect_count=available_applied_effect_count,
                effect_inspection_truncated=effect_inspection_truncated,
                residual_analysis_skipped=residual_analysis_skipped,
                residual_analysis_unavailable=residual_analysis_unavailable,
                residual_analysis_unavailable_reason=residual_analysis_unavailable_reason,
                residual_analysis_enacted_missing=bool(getattr(context, "enacted_missing", False)),
                residual_analysis_oracle_missing=bool(getattr(context, "oracle_missing", False)),
                candidate_count=candidate_count,
                candidate_ops=candidate_ops,
                residual_candidate_count=residual_candidate_count,
                residual_candidate_ops=residual_candidate_ops,
                residual_roots=residual_roots,
                replayed_residual_roots=replayed_residual_roots,
                oracle_residual_roots=oracle_residual_roots,
                malformed_residual_roots=malformed_residual_roots,
                residual_root_hits=residual_root_hits,
                residual_root_hit_counts=residual_inventory[
                    "residual_root_hit_counts"
                ],
                residual_root_side_counts=residual_inventory[
                    "residual_root_side_counts"
                ],
                residual_candidate_source_pathology_counts=residual_inventory[
                    "residual_candidate_source_pathology_counts"
                ],
                residual_candidate_compare_shape_counts=residual_inventory[
                    "residual_candidate_compare_shape_counts"
                ],
                residual_candidate_structural_counts=residual_inventory[
                    "residual_candidate_structural_counts"
                ],
                residual_candidate_action_counts=residual_inventory[
                    "residual_candidate_action_counts"
                ],
                residual_candidate_target_presence_counts=residual_inventory[
                    "residual_candidate_target_presence_counts"
                ],
                residual_candidate_target_presence_action_counts=residual_inventory[
                    "residual_candidate_target_presence_action_counts"
                ],
                residual_candidate_manual_compile_rule_counts=residual_inventory[
                    "residual_candidate_manual_compile_rule_counts"
                ],
                residual_candidate_root_samples=tuple(
                    residual_inventory["residual_candidate_root_samples"]
                ),
                residual_candidate_root_samples_omitted=int(
                    residual_inventory["residual_candidate_root_samples_omitted"]
                ),
                defeated_residual_roots=defeated_residual_roots,
                status=status,
                effect_feed_parse_rejections=tuple(parse_rejections),
                effect_selection_observations=tuple(effect_selection_observations),
                residual_effect_feed_parse_rejections=tuple(residual_effect_feed_parse_rejections),
                residual_effect_source_pathology_observations=(
                    _effect_source_pathology_rows(residual_effect_diagnostics)
                ),
                residual_source_acquisition_rejections=(
                    _source_acquisition_rows(residual_effect_diagnostics)
                ),
                residual_lowering_rejections=tuple(residual_lowering_rejections),
                residual_authority_rejections=tuple(residual_authority_rejections),
                residual_execution_observations=tuple(residual_execution_observations),
                residual_candidate_samples=tuple(residual_inventory["residual_candidate_samples"]),
                residual_candidate_samples_omitted=int(
                    residual_inventory["residual_candidate_samples_omitted"]
                ),
                replay_adjudication_kinds=replay_adjudication_kinds,
                replay_adjudication_sample_limit=replay_adjudication_sample_limit,
            )
            if not _row_matches_claim_template_status(
                candidate_row,
                claim_template_status,
            ):
                continue
            report_rows.append(candidate_row)
            if json_output:
                continue
            print(
                f"{r.statute_id:<30} frontier={score:.1%} "
                f"raw={r.score:.1%} replay={r.replay_score:.1%} "
                f"{_format_candidate_effect_inventory(r)} "
                f"candidate_effects={candidate_count:3d} "
                f"candidate_ops={candidate_ops:4d} "
                f"residual_candidate_effects={residual_candidate_count:3d} "
                f"residual_candidate_ops={residual_candidate_ops:4d}"
            )
            if effect_inspection_truncated:
                print(
                    f"  budget:   inspected_replay_applicable={inspected_effect_count} "
                    f"available_replay_applicable={available_replay_applicable_effect_count} "
                    f"available_applied={available_applied_effect_count}"
            )
            print(f"  class:    {comparison_class}")
            print(f"  sources:  {_format_candidate_source_status(r)}")
            saved_rule_line = _format_saved_bench_rejection_rules(r)
            if saved_rule_line:
                print(f"  saved_bench_{saved_rule_line}")
            saved_feed_error_line = _format_saved_bench_feed_count_error(r)
            if saved_feed_error_line:
                print(f"  {saved_feed_error_line}")
            _print_replay_adjudication_samples_for_row(report_rows[-1])
            if residual_analysis_skipped:
                print("  residual: skipped by --residual-budget")
            elif residual_analysis_unavailable:
                print(f"  residual: unavailable ({residual_analysis_unavailable_reason})")
            if parse_rejections:
                top_rejections = ", ".join(
                    f"{k}={v}" for k, v in _rejection_rule_counts(tuple(parse_rejections)).items()
                )
                print(f"  feed:     {top_rejections}")
            residual_compile_observation_rule_counts = _merged_rejection_rule_counts(
                tuple(residual_effect_feed_parse_rejections),
                _effect_source_pathology_rows(residual_effect_diagnostics),
                _source_acquisition_rows(residual_effect_diagnostics),
                tuple(residual_lowering_rejections),
                tuple(residual_authority_rejections),
                tuple(residual_execution_observations),
            )
            residual_compile_rule_counts = _merged_rejection_rule_counts(
                _blocking_rows(tuple(residual_effect_feed_parse_rejections)),
                _blocking_rows(_effect_source_pathology_rows(residual_effect_diagnostics)),
                _blocking_rows(_source_acquisition_rows(residual_effect_diagnostics)),
                _blocking_rows(tuple(residual_lowering_rejections)),
                _blocking_rows(tuple(residual_authority_rejections)),
                _blocking_rows(tuple(residual_execution_observations)),
            )
            if residual_compile_observation_rule_counts:
                top_residual_observations = ", ".join(
                    f"{k}={v}" for k, v in residual_compile_observation_rule_counts.items()
                )
                print(f"  residual_compile_observation: {top_residual_observations}")
            if residual_compile_rule_counts:
                top_residual_rejections = ", ".join(
                    f"{k}={v}" for k, v in residual_compile_rule_counts.items()
                )
                print(f"  residual_compile: {top_residual_rejections}")
            if source_counts:
                top_source = ", ".join(
                    f"{k}={v}" for k, v in source_counts.most_common(3)
                )
                print(f"  source:   {top_source}")
            if compare_counts:
                top_compare = ", ".join(
                    f"{k}={v}" for k, v in compare_counts.most_common(3)
                )
                print(f"  compare:  {top_compare}")
            if residual_roots:
                print(
                    f"  claims:   residual_roots={len(residual_roots):3d} "
                    f"backed_roots={len(residual_root_hits):3d} "
                    f"malformed_roots={len(malformed_residual_roots):3d} "
                    f"defeated_roots={len(defeated_residual_roots):3d}"
                )
            if not source_counts and not compare_counts:
                print("  source:   (none)")
                print("  compare:  (none)")
            print(f"  status:   {status}")
            print()
    manual_compile_evidence_jsonl_count = 0
    if manual_compile_evidence_jsonl_path is not None:
        manual_compile_evidence_jsonl_count = _write_jsonl_rows(
            manual_compile_evidence_jsonl_path,
            manual_compile_evidence_rows,
        )
    residual_claim_evidence_jsonl_report = (
        _write_residual_claim_evidence_report_from_candidate_rows(
            residual_claim_evidence_jsonl_path,
            report_rows,
            label=label,
        )
    )
    if json_output:
        report = _uk_candidates_report_jsonable(
            label=label,
            rows=report_rows,
            filters=filters_json,
            inspected_count=len(frontier),
            matched_frontier_count=len(matching_frontier),
            replay_adjudication_prefilter_count=replay_adjudication_prefilter_count,
            summary_only=summary_only,
            compact_rows=compact_json,
            summary_count_limit=summary_count_limit,
            row_count_limit=row_count_limit,
        )
        if manual_compile_evidence_jsonl_path is not None:
            report["manual_compile_evidence_jsonl"] = {
                "path": str(manual_compile_evidence_jsonl_path),
                "rows": manual_compile_evidence_jsonl_count,
                "statuses": sorted(manual_compile_evidence_statuses),
            }
        _attach_replay_adjudication_evidence_report(
            report,
            replay_adjudication_evidence_jsonl_report,
        )
        _attach_residual_claim_evidence_report(
            report,
            residual_claim_evidence_jsonl_report,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print()
        if manual_compile_evidence_jsonl_path is not None:
            print(
                "Manual compile evidence JSONL: "
                f"{manual_compile_evidence_jsonl_path} "
                f"rows={manual_compile_evidence_jsonl_count} "
                f"statuses={','.join(sorted(manual_compile_evidence_statuses))}"
            )
        if replay_adjudication_evidence_jsonl_report is not None:
            print(
                _format_replay_adjudication_evidence_report(
                    replay_adjudication_evidence_jsonl_report
                )
            )
        if residual_claim_evidence_jsonl_report is not None:
            print(
                _format_residual_claim_evidence_report(
                    residual_claim_evidence_jsonl_report
                )
            )
        _print_uk_candidates_text_summary(
            _uk_candidates_report_jsonable(
                label=label,
                rows=report_rows,
                filters=filters_json,
                inspected_count=len(frontier),
                matched_frontier_count=len(matching_frontier),
                replay_adjudication_prefilter_count=replay_adjudication_prefilter_count,
                summary_only=True,
                summary_count_limit=summary_count_limit,
                row_count_limit=row_count_limit,
            )
        )
