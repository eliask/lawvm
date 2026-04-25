"""Shared helpers for evidence tools.

Pure functions and constants used by evidence.py and bisect_support.py.
Extracted to avoid circular imports.
"""
from __future__ import annotations

import contextlib
import io
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from lawvm.core.evidence_support import (
    ORACLE_INCORRECT_DIAGNOSES as _ORACLE_INCORRECT_DIAGNOSES,
    REPLAY_BUG_DIAGNOSES as _REPLAY_BUG_DIAGNOSES,
    has_negligible_blame_drop_on_preexisting_residue as _has_negligible_blame_drop_on_preexisting_residue,
    section_similarity as _section_similarity,
)

# Primary tier precedence is intentionally conservative: if current evidence
# also proves source/oracle-side causes, do not headline the statute as a
# replay bug even when replay-side residuals are still present.
_PRIMARY_TIER_ORDER = [
    "PROVED_HTML_XML_NONCOMMENSURABLE",
    "PROVED_SOURCE_PATHOLOGY",
    "PROVED_ORACLE_INCORRECT",
    "PROVED_REPLAY_BUG",
    "UNRESOLVED",
]
_MANUAL_DATASET = Path(__file__).resolve().parent.parent.parent.parent / "data" / "finland" / "corrigendum_manual.yaml"
_PROOF_CONTRACT_VERSION = "lawvm-proof-v1"
_PROOF_STATUS = "defeasible_current_system"

__all__ = [
    "_MANUAL_DATASET",
    "_ORACLE_INCORRECT_DIAGNOSES",
    "_PRIMARY_TIER_ORDER",
    "_PROOF_CONTRACT_VERSION",
    "_PROOF_STATUS",
    "_REPLAY_BUG_DIAGNOSES",
    "_build_support_lookup_maps",
    "_chapter_label_from_key",
    "_cross_chapter_same_label_oracle_matches",
    "_cross_chapter_same_label_replay_matches",
    "_diagnosis_counts",
    "_has_negligible_blame_drop_on_preexisting_residue",
    "_lookup_support_row",
    "_normalize_observation_streams",
    "_obs",
    "_payload_materially_prefers_replay",
    "_section_key_from_labels",
    "_section_label_from_key",
    "_section_similarity",
]


def _payload_materially_prefers_replay(
    payload_vs_replay_score: Optional[float],
    payload_vs_oracle_score: Optional[float],
) -> bool:
    if payload_vs_replay_score is None or payload_vs_oracle_score is None:
        return False
    if (
        payload_vs_replay_score >= 0.85
        and payload_vs_replay_score >= (payload_vs_oracle_score + 0.05)
    ):
        return True
    # Allow a weaker but still meaningful source-side signal when the published
    # payload is only moderately close to replay yet still clearly farther from
    # the oracle. This catches narrow section-fragment payloads that can still
    # worsen total section similarity while materially supporting replay.
    return (
        payload_vs_replay_score >= 0.70
        and payload_vs_replay_score >= (payload_vs_oracle_score + 0.15)
    )


def _section_label_from_key(section_key: str) -> str:
    marker = "section:"
    if marker not in section_key:
        return section_key.strip()
    return section_key.split(marker, 1)[1].split("/", 1)[0].strip()


def _chapter_label_from_key(section_key: str) -> str:
    marker = "chapter:"
    if marker not in section_key:
        return ""
    return section_key.split(marker, 1)[1].split("/", 1)[0].strip()


def _section_key_from_labels(section_label: str, chapter_label: str = "") -> str:
    section_label = str(section_label or "").strip()
    chapter_label = str(chapter_label or "").strip()
    if not section_label:
        return ""
    if chapter_label:
        return f"chapter:{chapter_label}/section:{section_label}"
    return f"section:{section_label}"


def _build_support_lookup_maps(
    items: List[Dict[str, Any]],
) -> tuple[dict[tuple[str, str, str], Dict[str, Any]], dict[tuple[str, str], Dict[str, Any]]]:
    by_source_chapter_label: dict[tuple[str, str, str], Dict[str, Any]] = {}
    by_source_label_buckets: dict[tuple[str, str], list[Dict[str, Any]]] = defaultdict(list)
    for item in items:
        section_key = str(item.get("section") or "")
        blame_source = str(item.get("blame_source") or "")
        section_label = _section_label_from_key(section_key)
        if not section_label:
            continue
        chapter_label = _chapter_label_from_key(section_key)
        by_source_chapter_label[(blame_source, chapter_label, section_label)] = item
        if not chapter_label:
            by_source_label_buckets[(blame_source, section_label)].append(item)
    unique_by_source_label = {
        key: rows[0]
        for key, rows in by_source_label_buckets.items()
        if len(rows) == 1
    }
    return by_source_chapter_label, unique_by_source_label


def _lookup_support_row(
    sec: Dict[str, Any],
    exact_map: Dict[str, Dict[str, Any]],
    by_source_chapter_label: Dict[tuple[str, str, str], Dict[str, Any]],
    unique_by_source_label: Dict[tuple[str, str], Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    exact = exact_map.get(str(sec.get("section") or ""))
    if exact is not None:
        return exact
    blame_source = str(sec.get("blame_source") or "")
    section_key = str(sec.get("section") or "")
    section_label = _section_label_from_key(section_key)
    if not section_label:
        return None
    chapter_label = _chapter_label_from_key(section_key)
    chapter_exact = by_source_chapter_label.get((blame_source, chapter_label, section_label))
    if chapter_exact is not None:
        return chapter_exact
    return unique_by_source_label.get((blame_source, section_label))


def _subsection_texts(node) -> List[str]:
    from lawvm.tools._section_debug import render_node_text

    texts: List[str] = []
    if node is None:
        return texts
    children = getattr(node, "children", None)
    if children is not None:
        for child in children or []:
            if str(getattr(child, "kind", "") or "") != "subsection":
                continue
            text = " ".join(render_node_text(child).split())
            if text:
                texts.append(text)
        return texts
    for child in list(node):
        tag = str(getattr(child, "tag", "") or "")
        tag = tag.split("}")[-1] if "}" in tag else tag
        if tag != "subsection":
            continue
        text = " ".join(render_node_text(child).split())
        if text:
            texts.append(text)
    return texts


def _same_section_unmatched_oracle_subsections(
    replay_section_node,
    oracle_section_node,
) -> Dict[str, Any]:
    replay_subsections = _subsection_texts(replay_section_node)
    oracle_subsections = _subsection_texts(oracle_section_node)
    if not replay_subsections or not oracle_subsections:
        return {}
    unmatched: List[Dict[str, Any]] = []
    for oracle_text in oracle_subsections:
        best_score = max(
            (_section_similarity(oracle_text, replay_text) for replay_text in replay_subsections),
            default=0.0,
        )
        if best_score >= 0.55:
            continue
        excerpt = " ".join(oracle_text.split())
        if len(excerpt) > 180:
            excerpt = excerpt[:177] + "..."
        unmatched.append(
            {
                "oracle_text_excerpt": excerpt,
                "best_replay_score": round(best_score, 6),
            }
        )
    if not unmatched:
        return {}
    return {
        "count": len(unmatched),
        "max_best_replay_score": max(
            float(item.get("best_replay_score") or 0.0)
            for item in unmatched
        ),
        "oracle_text_excerpts": [
            str(item.get("oracle_text_excerpt") or "")
            for item in unmatched
            if str(item.get("oracle_text_excerpt") or "")
        ],
    }


def _diagnosis_counts(section_results: Iterable[Dict]) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for item in section_results:
        diag = str(item.get("diagnosis") or "")
        if diag:
            counts[diag] += 1
    return dict(sorted(counts.items()))


def _same_chapter_alternative_replay_matches(
    section_results: Iterable[Dict],
    replay_section_texts: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:
    matches: Dict[str, Dict[str, Any]] = {}
    for item in section_results:
        section = str(item.get("section") or "")
        oracle_text = str(item.get("oracle_text") or "")
        replay_text = str(item.get("replay_text") or "")
        if not section or not oracle_text:
            continue
        chapter = _chapter_label_from_key(section)
        # Use pre-computed similarity when available to avoid redundant Levenshtein.
        _precomputed = item.get("similarity")
        same_score = float(_precomputed) if _precomputed is not None else _section_similarity(replay_text, oracle_text)
        best_key = ""
        best_score = 0.0
        for key, candidate_text in replay_section_texts.items():
            if key == section or not candidate_text:
                continue
            if _chapter_label_from_key(key) != chapter:
                continue
            score = _section_similarity(candidate_text, oracle_text)
            if score > best_score:
                best_score = score
                best_key = key
        if (
            best_key
            and best_score >= 0.70
            and best_score >= (same_score + 0.20)
        ):
            matches[section] = {
                "best_replay_section": best_key,
                "best_replay_score": round(best_score, 6),
                "same_section_score": round(same_score, 6),
            }
    return matches


def _same_chapter_oracle_range_matches(
    section_results: Iterable[Dict],
    oracle_sections: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    matches: Dict[str, Dict[str, Any]] = {}
    for item in section_results:
        section = str(item.get("section") or "")
        if not section or section in oracle_sections:
            continue
        chapter = _chapter_label_from_key(section)
        section_label = _section_label_from_key(section)
        if not chapter or not section_label:
            continue
        for key in oracle_sections:
            if _chapter_label_from_key(key) != chapter:
                continue
            oracle_label = _section_label_from_key(key)
            if "–" not in oracle_label and "-" not in oracle_label:
                continue
            parts = [
                str(part or "").strip()
                for part in re.split(r"[–-]", oracle_label)
                if str(part or "").strip()
            ]
            if section_label not in parts:
                continue
            matches[section] = {
                "oracle_range_section": key,
                "oracle_range_label": oracle_label,
            }
            break
    return matches


def _cross_chapter_same_label_oracle_matches(
    section_results: Iterable[Dict],
    oracle_sections: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    from lawvm.tools._section_debug import render_node_text

    oracle_section_texts = {
        key: render_node_text(node)
        for key, node in oracle_sections.items()
    }
    matches: Dict[str, Dict[str, Any]] = {}
    for item in section_results:
        section = str(item.get("section") or "")
        replay_text = str(item.get("replay_text") or "")
        oracle_text = str(item.get("oracle_text") or "")
        if not section or not replay_text or oracle_text.strip():
            continue
        section_label = _section_label_from_key(section)
        chapter_label = _chapter_label_from_key(section)
        # Use pre-computed similarity when available to avoid redundant Levenshtein.
        _precomputed = item.get("similarity")
        same_score = float(_precomputed) if _precomputed is not None else _section_similarity(replay_text, oracle_text)
        best_key = ""
        best_score = 0.0
        runner_up_key = ""
        runner_up_score = 0.0
        for key, candidate_text in oracle_section_texts.items():
            if key == section or not candidate_text:
                continue
            if _section_label_from_key(key) != section_label:
                continue
            if _chapter_label_from_key(key) == chapter_label:
                continue
            score = _section_similarity(replay_text, candidate_text)
            if score > best_score:
                runner_up_key = best_key
                runner_up_score = best_score
                best_score = score
                best_key = key
            elif score > runner_up_score:
                runner_up_key = key
                runner_up_score = score
        if (
            best_key
            and best_score >= 0.70
            and best_score >= (same_score + 0.20)
        ):
            row = {
                "oracle_section": best_key,
                "oracle_section_score": round(best_score, 6),
                "same_section_score": round(same_score, 6),
            }
            if runner_up_key:
                row["runner_up_oracle_section"] = runner_up_key
                row["runner_up_oracle_section_score"] = round(runner_up_score, 6)
            matches[section] = row
    return matches


def _cross_chapter_same_label_replay_matches(
    section_results: Iterable[Dict],
    replay_section_texts: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:
    matches: Dict[str, Dict[str, Any]] = {}
    for item in section_results:
        section = str(item.get("section") or "")
        replay_text = str(item.get("replay_text") or "")
        oracle_text = str(item.get("oracle_text") or "")
        if not section or replay_text.strip() or not oracle_text:
            continue
        section_label = _section_label_from_key(section)
        chapter_label = _chapter_label_from_key(section)
        _precomputed = item.get("similarity")
        same_score = float(_precomputed) if _precomputed is not None else _section_similarity(replay_text, oracle_text)
        best_key = ""
        best_score = 0.0
        runner_up_key = ""
        runner_up_score = 0.0
        for key, candidate_text in replay_section_texts.items():
            if key == section or not candidate_text:
                continue
            if _section_label_from_key(key) != section_label:
                continue
            if _chapter_label_from_key(key) == chapter_label:
                continue
            score = _section_similarity(candidate_text, oracle_text)
            if score > best_score:
                runner_up_key = best_key
                runner_up_score = best_score
                best_score = score
                best_key = key
            elif score > runner_up_score:
                runner_up_key = key
                runner_up_score = score
        if (
            best_key
            and best_score >= 0.70
            and best_score >= (same_score + 0.20)
        ):
            row = {
                "replay_section": best_key,
                "replay_section_score": round(best_score, 6),
                "same_section_score": round(same_score, 6),
            }
            if runner_up_key:
                row["runner_up_replay_section"] = runner_up_key
                row["runner_up_replay_section_score"] = round(runner_up_score, 6)
            matches[section] = row
    return matches


def _run_quietly(fn, *args, **kwargs):
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        return fn(*args, **kwargs)



def _proof_contract() -> Dict:
    return {
        "version": _PROOF_CONTRACT_VERSION,
        "status": _PROOF_STATUS,
        "meaning": (
            "LawVM proof tiers are current-system evidence claims, not formal mathematical proofs. "
            "They are generated from current replay semantics, current oracle-check diagnoses, "
            "current HTML/XML audit logic, and current source/corrigendum classification."
        ),
        "depends_on": [
            "current_replay_semantics",
            "current_oracle_check_diagnosis_rules",
            "current_html_xml_audit_rules",
            "current_source_pathology_detectors",
            "current_corrigendum_records",
        ],
    }


def _obs(source: str, field: str, value, *, scope: str = "") -> Dict:
    item = {"source": source, "field": field, "value": value}
    if scope:
        item["scope"] = scope
    return item


def _observation_value(item, key: str):
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _section_key_from_path(path) -> str:
    if not isinstance(path, (list, tuple)):
        return ""
    chapter_label = ""
    section_label = ""
    for item in path:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            continue
        kind, label = item
        if not isinstance(kind, str) or not isinstance(label, str):
            continue
        if kind == "chapter":
            chapter_label = label
        elif kind == "section":
            section_label = label
    return _section_key_from_labels(section_label, chapter_label)


def _normalize_observation_streams(
    *,
    elaboration_observations: Optional[Iterable] = None,
    sparse_slot_bindings: Optional[Iterable] = None,
    sparse_leftovers: Optional[Iterable] = None,
    apply_mutation_events: Optional[Iterable] = None,
    apply_mutation_invariant_reports: Optional[Iterable] = None,
) -> List[Dict]:
    apply_mutation_events = tuple(apply_mutation_events or ())
    apply_mutation_invariant_reports = tuple(apply_mutation_invariant_reports or ())
    normalized: List[Dict] = []

    def _observation_target_unit_kind(item: object) -> str:
        target_unit_kind = str(_observation_value(item, "target_unit_kind") or "").strip()
        if target_unit_kind:
            return target_unit_kind
        target_kind = str(_observation_value(item, "target_kind") or "").strip().upper()
        if target_kind == "P":
            return "section"
        if target_kind == "L":
            return "chapter"
        if target_kind == "O":
            return "part"
        return ""

    for item in elaboration_observations or []:
        target_unit_kind = _observation_target_unit_kind(item)
        section_label = ""
        chapter_label = ""
        target_kind = str(_observation_value(item, "target_kind") or "").strip()
        target_norm = str(_observation_value(item, "target_norm") or "").strip()
        target_chapter = str(_observation_value(item, "target_chapter") or "").strip()
        if target_unit_kind == "section":
            section_label = target_norm
            chapter_label = target_chapter
        detail = _observation_value(item, "detail")
        normalized.append(
            {
                "family": "elaboration",
                "kind": str(_observation_value(item, "kind") or ""),
                "stage": str(_observation_value(item, "stage") or ""),
                "source_statute": str(_observation_value(item, "source_statute") or ""),
                "target_unit_kind": target_unit_kind,
                "target_kind": target_kind,
                "target_norm": target_norm,
                "target_chapter": target_chapter,
                "section": _section_key_from_labels(section_label, chapter_label),
                "section_label": section_label,
                "chapter_label": chapter_label,
                "detail": dict(detail) if isinstance(detail, dict) else {},
                "payload_completeness_kind": str(_observation_value(item, "payload_completeness_kind") or ""),
                "tail_policy": str(_observation_value(item, "tail_policy") or ""),
            }
        )

    for item in sparse_leftovers or []:
        target_unit_kind = _observation_target_unit_kind(item)
        section_label = ""
        chapter_label = ""
        target_kind = str(_observation_value(item, "target_kind") or "").strip()
        target_norm = str(_observation_value(item, "target_norm") or "").strip()
        target_chapter = str(_observation_value(item, "target_chapter") or "").strip()
        if target_unit_kind == "section":
            section_label = target_norm
            chapter_label = target_chapter
        normalized.append(
            {
                "family": "sparse_leftover",
                "kind": "ELAB.SPARSE_PAYLOAD_LEFTOVER",
                "stage": "sparse_subsection_elaboration",
                "source_statute": str(_observation_value(item, "source_statute") or ""),
                "target_unit_kind": target_unit_kind,
                "target_kind": target_kind,
                "target_norm": target_norm,
                "target_chapter": target_chapter,
                "section": _section_key_from_labels(section_label, chapter_label),
                "section_label": section_label,
                "chapter_label": chapter_label,
                "unassigned_slot_count": len(_observation_value(item, "unassigned_slots") or []),
                "unassigned_slots": [
                    str(label)
                    for label in (_observation_value(item, "unassigned_slots") or [])
                    if str(label)
                ],
            }
        )

    for item in sparse_slot_bindings or []:
        target_unit_kind = _observation_target_unit_kind(item)
        section_label = ""
        chapter_label = ""
        target_kind = str(_observation_value(item, "target_kind") or "").strip()
        target_norm = str(_observation_value(item, "target_norm") or "").strip()
        target_chapter = str(_observation_value(item, "target_chapter") or "").strip()
        if target_unit_kind == "section":
            section_label = target_norm
            chapter_label = target_chapter
        normalized.append(
            {
                "family": "sparse_slot_binding",
                "kind": "ELAB.SPARSE_SLOT_BINDING",
                "stage": "sparse_subsection_elaboration",
                "source_statute": str(_observation_value(item, "source_statute") or ""),
                "target_unit_kind": target_unit_kind,
                "target_kind": target_kind,
                "target_norm": target_norm,
                "target_chapter": target_chapter,
                "section": _section_key_from_labels(section_label, chapter_label),
                "section_label": section_label,
                "chapter_label": chapter_label,
                "payload_slot_index": int(_observation_value(item, "payload_slot_index") or 0),
                "payload_slot_label": str(_observation_value(item, "payload_slot_label") or ""),
            }
        )

    if not apply_mutation_invariant_reports:
        for item in apply_mutation_events:
            resolved_target_path = _observation_value(item, "resolved_target_path")
            parent_path = _observation_value(item, "parent_path")
            section_key = _section_key_from_path(resolved_target_path)
            normalized.append(
                {
                    "family": "apply_mutation",
                    "kind": str(_observation_value(item, "outcome") or ""),
                    "stage": "apply",
                    "source_statute": str(_observation_value(item, "source_statute") or ""),
                    "helper": str(_observation_value(item, "helper") or ""),
                    "target_unit_kind": str(
                        _observation_value(item, "target_unit_kind")
                        or _observation_value(item, "target_kind")
                        or ""
                    ),
                    "target_kind": str(_observation_value(item, "target_kind") or ""),
                    "target_norm": str(_observation_value(item, "target_norm") or ""),
                    "target_chapter": str(_observation_value(item, "target_chapter") or ""),
                    "target_path": resolved_target_path or parent_path or (),
                    "section": section_key,
                    "section_label": _section_label_from_key(section_key),
                    "chapter_label": _chapter_label_from_key(section_key),
                }
            )

    for item in apply_mutation_invariant_reports:
        allowed_effect_region_paths = _observation_value(item, "allowed_effect_region_paths")
        touched_paths = _observation_value(item, "touched_paths")
        permitted_paths = _observation_value(item, "permitted_paths")
        target_path = ()
        if isinstance(allowed_effect_region_paths, (list, tuple)) and allowed_effect_region_paths:
            target_path = allowed_effect_region_paths[0]
        elif isinstance(touched_paths, (list, tuple)) and touched_paths:
            target_path = touched_paths[0]
        section_key = _section_key_from_path(target_path)
        normalized.append(
            {
                "family": "apply_mutation_invariant",
                "kind": (
                    "PATH_SET_INVARIANT_HOLDS"
                    if bool(_observation_value(item, "path_set_invariant_holds"))
                    else "PATH_SET_INVARIANT_BROKEN"
                ),
                "stage": "apply",
                "source_statute": str(_observation_value(item, "source_statute") or ""),
                "helper": str(_observation_value(item, "helper") or ""),
                "target_unit_kind": "",
                "target_kind": "",
                "target_norm": "",
                "target_chapter": "",
                "target_path": target_path or permitted_paths or (),
                "section": section_key,
                "section_label": _section_label_from_key(section_key),
                "chapter_label": _chapter_label_from_key(section_key),
                "path_set_invariant_holds": bool(_observation_value(item, "path_set_invariant_holds")),
                "covered_changed_count": len(_observation_value(item, "covered_changed_paths") or ()),
                "unexplained_changed_count": len(_observation_value(item, "unexplained_changed_paths") or ()),
                "declared_recovery_rule_ids": [
                    str(rule_id)
                    for rule_id in (_observation_value(item, "declared_recovery_rule_ids") or [])
                    if str(rule_id)
                ],
                "declared_migration_rule_ids": [
                    str(rule_id)
                    for rule_id in (_observation_value(item, "declared_migration_rule_ids") or [])
                    if str(rule_id)
                ],
                "result_codes": [
                    str(_observation_value(result, "code") or "")
                    for result in (_observation_value(item, "results") or [])
                    if str(_observation_value(result, "code") or "")
                ],
                "matched_allowance_rule_ids": [
                    str(rule_id)
                    for rule_id in (_observation_value(item, "matched_allowance_rule_ids") or [])
                    if str(rule_id)
                ],
            }
        )

    return normalized
