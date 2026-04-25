"""Frontend finding emitters for Finnish amendment replay.

Pure analysis functions that inspect a list of AmendmentOps and a johtolause
and return direct Finding records.  They have no side effects and do not read
or mutate replay state.

All emitted Finding kinds are registered in
``lawvm.core.observation_registry.FINDING_REGISTRY``.
"""

import re
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, List, Optional, Set, Tuple

from lawvm.core.phase_result import Finding
from lawvm.finland.ops import AmendmentOp, projection_scope_confidence
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.johtolause import parse_clause, derive_features

if TYPE_CHECKING:
    from lawvm.finland.johtolause import ClauseParseResult

_SAME_LABEL_MOVE_CLAUSE_RE = re.compile(
    r"joista\s+([^§]{0,120})\s*§\s+(?:samalla\s+)?siirretään\s+(\d+\s*[a-z]?)\s+lukuun",
    flags=re.I,
)

def _target_detail_for_unit_kind(
    target_unit_kind: str,
    *,
    target_norm: str = "",
    target_chapter: str = "",
) -> dict[str, object]:
    return {
        "target_unit_kind": target_unit_kind,
        "target_norm": target_norm,
        "target_chapter": target_chapter,
    }


def _target_detail_for_op(
    op: AmendmentOp,
    *,
    target_norm: Optional[str] = None,
    target_chapter: Optional[str] = None,
) -> dict[str, object]:
    target_unit_kind = op.target_unit_kind
    return _target_detail_for_unit_kind(
        target_unit_kind,
        target_norm=(
            target_norm
            if target_norm is not None
            else (_norm_num_token(op.target_section) if op.target_section else "")
        ),
        target_chapter=(
            target_chapter
            if target_chapter is not None
            else (_norm_num_token(op.target_chapter) if op.target_chapter else "")
        ),
    )


def _same_label_move_clause_targets(johto: str) -> List[Tuple[str, str]]:
    """Return inline same-label move clause targets as (labels_text, dest_chapter)."""
    cleaned = re.sub(r"\s+", " ", johto or "").strip().lower()
    return _SAME_LABEL_MOVE_CLAUSE_RE.findall(cleaned)


def _duplicate_frontend_target_observations(
    ops: List[AmendmentOp],
    source_statute: str,
    *,
    stage: str = "frontend_ops",
) -> List[Finding]:
    """Emit frontend findings for exact duplicate extracted targets.

    These are not strict failures by themselves, but they are a strong signal
    that frontend extraction/supplement logic flattened the same target more
    than once. Keep the key exact: same action, address, and special target.
    """
    grouped: dict[
        Tuple[str, str, str, Optional[int], Optional[str], Optional[str], Optional[str]],
        List[AmendmentOp],
    ] = {}
    for op in ops:
        target_unit_kind = op.target_unit_kind
        key = (
            op.op_type,
            target_unit_kind,
            _norm_num_token(op.target_section) if op.target_section else "",
            op.target_paragraph,
            op.target_item,
            op.target_special,
            _norm_num_token(op.target_chapter) if op.target_chapter else None,
        )
        grouped.setdefault(key, []).append(op)

    findings: List[Finding] = []
    for key, dupes in grouped.items():
        if len(dupes) < 2:
            continue
        op_type, target_kind, target_norm, paragraph, item, special, chapter = key
        target_detail = _target_detail_for_op(
            dupes[0],
            target_norm=target_norm,
            target_chapter=chapter or "",
        )
        findings.append(
            Finding(
                kind="PARSE.DUPLICATE_TARGET_OP",
                role="observation",
                stage=stage,
                source_statute=source_statute,
                detail={
                    **target_detail,
                    "op_type": op_type,
                    "target_paragraph": paragraph,
                    "target_item": item or "",
                    "target_special": special or "",
                    "duplicate_count": len(dupes),
                },
                blocking=False,
            )
        )
    return findings


def _semantic_collapse_move_or_renumber_observations(
    ops: List[AmendmentOp],
    johto: str,
    source_statute: str,
    *,
    parse_result: "ClauseParseResult | None" = None,
) -> List[Finding]:
    """Emit findings when move/renumber syntax collapses into plain duplicate replaces.

    Keep this intentionally narrow: only warn when the clause explicitly says a
    section list is moved into a chapter and raw frontend extraction still emits
    duplicate whole-section `REPLACE` targets for those same labels.
    """
    cleaned = re.sub(r"\s+", " ", johto or "").strip().lower()

    duplicate_replace_counts: dict[str, int] = {}
    for obs in _duplicate_frontend_target_observations(
        ops,
        source_statute,
        stage="frontend_extraction",
    ):
        d = obs.detail
        if (
            d.get("target_unit_kind") == "section"
            and d.get("op_type") == "REPLACE"
            and not d.get("target_chapter")
            and d.get("target_paragraph") is None
            and not d.get("target_item")
            and not d.get("target_special")
        ):
            target_norm = str(d.get("target_norm") or "")
            duplicate_replace_counts[target_norm] = int(d.get("duplicate_count") or 0)

    findings: List[Finding] = []
    if duplicate_replace_counts and "siirretään" in cleaned and "lukuun" in cleaned:
        for labels_text, dest_chapter in _same_label_move_clause_targets(cleaned):
            labels = {
                _norm_num_token(token)
                for token in re.split(r"\s*(?:,|ja)\s*", labels_text)
                if re.fullmatch(r"\d+\s*[a-z]?", token.strip(), flags=re.I)
            }
            dest_norm = _norm_num_token(str(dest_chapter or ""))
            for label in sorted(labels):
                dup_count = duplicate_replace_counts.get(label, 0)
                if dup_count < 2:
                    continue
                findings.append(
                    Finding(
                        kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                        role="observation",
                        stage="frontend_extraction",
                        source_statute=source_statute,
                        detail={
                            **_target_detail_for_unit_kind(
                                "section",
                                target_norm=label,
                                target_chapter="",
                            ),
                            "collapse_kind": "move_to_chapter_clause",
                            "destination_chapter": dest_norm,
                            "duplicate_replace_count": dup_count,
                        },
                        blocking=False,
                    )
                )

    if "numero" not in cleaned or "mainit" not in cleaned:
        return findings
    try:
        result = parse_result if parse_result is not None else parse_clause(johto)
        parsed_ops = result.parsed_ops
        johto_features = derive_features(johto, parsed_ops)
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        return findings
    if "renumber" not in johto_features or not ({"backref_singular", "backref_plural"} & set(johto_features)):
        return findings

    parsed_groups: dict[Tuple[str, str, str], dict[str, int]] = {}
    for parsed in parsed_ops:
        if parsed.kind != "P" or not parsed.number:
            continue
        key = (
            _norm_num_token(parsed.part) if parsed.part else "",
            _norm_num_token(parsed.chapter) if parsed.chapter else "",
            _norm_num_token(parsed.number),
        )
        bucket = parsed_groups.setdefault(key, {"whole_count": 0, "scoped_count": 0})
        if parsed.momentti or parsed.item or parsed.special:
            bucket["scoped_count"] += 1
        else:
            bucket["whole_count"] += 1

    section_replace_ops: dict[Tuple[str, str], List[AmendmentOp]] = {}
    for op in ops:
        if op.op_type != "REPLACE" or op.target_unit_kind != "section" or not op.target_section:
            continue
        key = (
            _norm_num_token(op.target_chapter) if op.target_chapter else "",
            _norm_num_token(op.target_section),
        )
        section_replace_ops.setdefault(key, []).append(op)

    for (_part_norm, chapter_norm, section_norm), bucket in sorted(parsed_groups.items()):
        whole_count = int(bucket.get("whole_count", 0))
        scoped_count = int(bucket.get("scoped_count", 0))
        if whole_count == 0 or (scoped_count == 0 and whole_count < 2):
            continue
        resolved_chapter = chapter_norm
        matching_ops = section_replace_ops.get((chapter_norm, section_norm), [])
        if not matching_ops:
            matching_ops = section_replace_ops.get(("", section_norm), [])
        if not matching_ops and not chapter_norm:
            # When the parsed clause omitted an explicit chapter for a section
            # (e.g. "3 §:n" after a section scoped in a PART block), fall back
            # to any op matching the section under any chapter.  Use the chapter
            # from the matched op as the resolved chapter for the observation.
            for (op_ch, op_sec), op_list in section_replace_ops.items():
                if op_sec == section_norm and op_list:
                    matching_ops = op_list
                    resolved_chapter = op_ch
                    break
        if not matching_ops:
            continue
        findings.append(
            Finding(
                kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                role="observation",
                stage="frontend_extraction",
                source_statute=source_statute,
                detail={
                    **_target_detail_for_unit_kind(
                        "section",
                        target_norm=section_norm,
                        target_chapter=resolved_chapter,
                    ),
                    "collapse_kind": "renumber_backref_clause",
                    "whole_section_replace_count": whole_count,
                    "scoped_replace_count": scoped_count,
                },
                blocking=False,
            )
        )
    return findings


def _destinationless_move_or_relabel_observations(
    ops: List[AmendmentOp],
    johto: str,
    source_statute: str,
) -> List[Finding]:
    """Emit findings when move/relabel semantics still leave bare renumber ops.

    This is the residual producer-side gap the constitution wants surfaced
    explicitly: the clause speaks in move/relabel terms, but frontend repairs
    still leave a destination-less RENUMBER instead of typed destination data.
    """
    cleaned = re.sub(r"\s+", " ", johto or "").strip().lower()
    if "siirretään" not in cleaned and "siirret" not in cleaned:
        return []

    findings: List[Finding] = []
    seen: Set[Tuple[str, str, str, Optional[int], Optional[str], Optional[str]]] = set()
    for op in ops:
        if op.op_type != "RENUMBER":
            continue
        if op.lo is None or op.lo.destination is not None:
            continue
        if not op.target_section:
            continue
        target_unit_kind = op.target_unit_kind
        key = (
            target_unit_kind,
            _norm_num_token(op.target_section),
            _norm_num_token(op.target_chapter) if op.target_chapter else "",
            op.target_paragraph,
            op.target_item,
            op.target_special,
        )
        if key in seen:
            continue
        findings.append(
            Finding(
                kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                role="observation",
                stage="frontend_extraction",
                source_statute=source_statute,
                detail={
                    **_target_detail_for_op(op),
                    "collapse_kind": "destinationless_move_relabel",
                    "op_type": op.op_type,
                    "target_paragraph": op.target_paragraph,
                    "target_item": op.target_item or "",
                    "target_special": op.target_special or "",
                    "destination_missing": True,
                },
                blocking=False,
            )
        )
        seen.add(key)
    return findings


def _scope_anchor_dependence_observations(
    ops: List[AmendmentOp],
    source_statute: str,
) -> List[Finding]:
    """Emit frontend findings for scope/anchor-dependent target recovery.

    The typed Finland-local scope carrier is primary. Raw scope-provenance tags
    remain debug/compat evidence and are only used as a fallback witness source.
    """
    seen: Set[Tuple[str, str, str, str]] = set()
    findings: List[Finding] = []
    for op in ops:
        target_norm = _norm_num_token(op.target_section) if op.target_section else ""
        target_chapter = _norm_num_token(op.target_chapter) if op.target_chapter else ""
        witness = projection_scope_confidence(
            scope_confidence=op.scope_confidence,
            scope_provenance_tags=op.scope_provenance_tags,
            resolved_chapter=target_chapter or None,
        )
        if witness is None:
            continue
        if not witness.tag and op.scope_provenance_tags:
            witness = dc_replace(witness, tag=op.scope_provenance_tags[0])
        if witness.source == "carry_forward":
            obs_kind = "LOWER.SCOPE_CARRY_FORWARD"
        elif witness.source in {"johtolause", "grouped_part", "grouped_chapter"}:
            obs_kind = "LOWER.CONTEXT_DEPENDENT_ANCHOR"
        elif witness.source == "explicit_chunk":
            obs_kind = "LOWER.EXPLICIT_CHUNK_SCOPE"
        elif witness.source == "explicit_scope_rewrite":
            obs_kind = "LOWER.EXPLICIT_SCOPE_REWRITE"
        else:
            continue
        target_unit_kind = op.target_unit_kind
        key = (obs_kind, target_unit_kind, target_norm, target_chapter)
        if key in seen:
            continue
        findings.append(
            Finding(
                kind=obs_kind,
                role="observation",
                stage="frontend_scope",
                source_statute=source_statute,
                detail={
                    **_target_detail_for_op(
                        op,
                        target_norm=target_norm,
                        target_chapter=target_chapter,
                    ),
                    "tag": witness.tag,
                    "scope_source": witness.source,
                    "scope_confidence": witness.confidence,
                    "op_type": op.op_type,
                    "target_paragraph": op.target_paragraph,
                    "target_item": op.target_item or "",
                    "target_special": op.target_special or "",
                },
                blocking=False,
            )
        )
        seen.add(key)
    return findings
