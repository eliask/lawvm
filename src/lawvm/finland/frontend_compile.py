"""Frontend extraction pipeline for Finnish amendment replay.

This module contains ``normalize_and_compile_ops`` — the orchestrator for the
entire PEG-to-AmendmentOp pipeline — along with the helpers that exist solely
to serve it.

Extracted from ``grafter.py`` (Tier-3 extraction per GRAFTER_DECOMPOSITION_ANALYSIS.md,
Phase N).  ``grafter.py`` re-exports all public names from this module for
backward compatibility.

Pipeline stages executed here:
1. PEG extraction (``extract_johtolause_legal_ops``)
2. LO normalization chain (chapter-scope strips/assignments)
3. ``AmendmentOp`` conversion via ``AmendmentOp.from_lo``
4. Johtolause supplement passes (item-shift, named table row)
5. Metadata enrichment (source statute/date/title on every op)
6. Fallback parsers (heuristic, body-root-replace, title)
7. Frontend observation emission (deduplication signals, scope-anchor analysis)
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, FrozenSet, List, Optional

import lxml.etree as etree

if TYPE_CHECKING:
    from lawvm.finland.johtolause import ClauseParseResult

from lawvm.core.ir import LegalOperation, OperationSource
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.core.compile_result import StrictProfile
from lawvm.core.phase_result import Finding
from lawvm.core.temporal import ActivationRule, TemporalEvent, TemporalScope
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.ops import FailedOp
from lawvm.finland.ops import ScopeConfidence
from lawvm.finland.ops import classify_legal_operation_conversion_skip
from lawvm.finland.ops import normalize_scope_confidence, projection_scope_confidence
from lawvm.finland.ops import _lo_with_path_update
from lawvm.finland.normalize import (
    _sec1_fallback_peg_skip_required,
    _extract_root_replace_ops_from_body_fallback,
    _dedupe_fallback_ops_ir,
    parse_ops_fallback_heuristic,
    parse_ops_title_fallback,
)
from lawvm.finland.johtolause import (
    extract_legal_ops_from_parse_result as extract_johtolause_legal_ops_from_parse_result,
    parse_clause as parse_johtolause_clause,
)
from lawvm.finland.johtolause_supplements import (
    _tag_explicit_item_shift_after_repeal_hints,
    _supplement_missing_repeals_after_item_shift_clause,
    _supplement_named_table_row_mixed_clause_ops,
    _tag_named_table_row_single_clause_ops,
)
from lawvm.finland.scope import (
    _same_label_move_sections_for_chapter,
    strip_unjustified_chapter_scope_from_unique_sections as _strip_unjustified_chapter_scope_from_unique_sections,
    assign_chapter_scope_from_johtolause as _assign_chapter_scope_from_johtolause,
    assign_scope_from_renumber_destinations as _assign_scope_from_renumber_destinations,
)
from lawvm.finland.metadata import (
    _statute_issue_date,
    _amendment_effective_date,
    _amendment_expiry_date,
    _expiry_date_precedes_effective_date,
    _infer_expiry_date_from_temporary_payload_text,
    _temporary_section_expiry_overrides,
    _parse_section_list_labels,
    _normalize_fi_parse_text,
    get_johtolause,
)
from lawvm.finland.corpus import get_corpus
from lawvm.finland.fallback_op_ids import mint_fallback_op_id
from lawvm.finland.helpers import _norm_num_token, _roman_label_to_arabic
from lawvm.finland.frontend_observations import (
    _duplicate_frontend_target_observations,
    _destinationless_move_or_relabel_observations,
    _semantic_collapse_move_or_renumber_observations,
    _scope_anchor_dependence_observations,
)
from lawvm.finland.replay_notices import replay_print as _replay_print

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# _tree_title — tiny lxml helper, lives here because it was first introduced
# to serve _enrich_ops_from_amendment_tree.  Re-exported from grafter.py.
# ---------------------------------------------------------------------------


def _tree_title(tree: "etree._Element") -> str:
    """Extract the document title text from an AKN lxml tree."""
    title_el = tree.find(".//{*}docTitle")
    return etree.tostring(title_el, method="text", encoding="unicode").strip() if title_el is not None else "Unknown"


def _ambiguous_unscoped_additive_fallback_insert_observation(
    existing_ops: List[AmendmentOp],
    fallback_op: AmendmentOp,
    *,
    amendment_id: str,
) -> Finding | None:
    """Reject unscoped additive fallback item inserts when section ownership is multi-scoped."""
    if (
        fallback_op.op_type != "INSERT"
        or fallback_op.target_chapter is not None
        or fallback_op.target_section is None
        or fallback_op.target_paragraph is None
        or fallback_op.target_item is None
        or fallback_op.target_special is not None
        or "extraction_fallback_heuristic" not in fallback_op.extraction_provenance_tags
    ):
        return None

    candidate_chapters = sorted(
        {
            _norm_num_token(op.target_chapter)
            for op in existing_ops
            if op.target_section == fallback_op.target_section and op.target_chapter
        }
    )
    if len(candidate_chapters) <= 1:
        return None

    return Finding(
        kind="ELAB.REJECTED_OPERATION",
        role="observation",
        stage="frontend_compile",
        detail={
            "message": "Unscoped additive fallback insert was rejected because the amendment carries multiple explicit chapter-scoped owners for that section.",
            "reason_code": "ELAB.AMBIGUOUS_UNSCOPED_FALLBACK_INSERT_MULTI_SCOPE",
            "description": fallback_op.description(),
            "target_section": fallback_op.target_section,
            "target_paragraph": fallback_op.target_paragraph,
            "target_item": fallback_op.target_item,
            "candidate_chapters": candidate_chapters,
        },
        source_statute=amendment_id,
        blocking=False,
    )


def _reject_overbroad_section_repeals_for_deep_targets(
    ops: List[AmendmentOp],
    *,
    johto: str,
    amendment_id: str,
) -> tuple[List[AmendmentOp], List[Finding]]:
    """Reject whole-section repeals when the clause explicitly targets a deeper unit.

    PEG/sec1-fallback and coarse fallback can both under-specify dotted
    ``kohta``/``alakohta`` repeal depth as a whole-section ``REPEAL <section>``.
    That violates the mutation-boundary contract by widening a child repeal
    into a parent deletion. Keep the unsupported overbroad repeal visible
    instead of mutating the parent.
    """
    cleaned = re.sub(r"\s+", " ", johto or "").strip().lower()
    mentions_deep_repeal = bool(
        re.search(r"§\s*:n\s+.+\b(?:kohta|kohdan|alakohta|alakohdan)\b", cleaned, flags=re.I)
    )
    if not mentions_deep_repeal:
        return ops, []

    kept: List[AmendmentOp] = []
    findings: List[Finding] = []
    for op in ops:
        if (
            op.op_type == "REPEAL"
            and op.target_unit_kind == "section"
            and op.target_paragraph is None
            and op.target_item is None
            and op.target_special is None
        ):
            findings.append(
                Finding(
                    kind="ELAB.REJECTED_OPERATION",
                    role="observation",
                    stage="frontend_compile",
                    detail={
                        "message": "Whole-section repeal was rejected because the clause explicitly targets deeper kohta/alakohta scope.",
                        "reason_code": "ELAB.OVERBROAD_SECTION_REPEAL_FOR_DEEP_TARGET",
                        "description": op.description(),
                        "target_section": op.target_section or "",
                    },
                    source_statute=amendment_id,
                    blocking=False,
                )
            )
            continue
        kept.append(op)
    return kept, findings


def _attach_target_version_selectors(
    ops: List[AmendmentOp],
    *,
    parse_result: "ClauseParseResult | None",
    amendment_id: str,
) -> tuple[List[AmendmentOp], List[Finding]]:
    """Attach explicit cited-version selectors from the parsed johtolause to ops.

    Finland clauses like ``23 § laissa 195/2015 sekä 24 c, 30 b ja 34 a § laissa
    575/2018`` carry per-target cited-version ownership. Preserve that ownership
    on the emitted section ops before supplements/fallbacks can blur it.
    """
    if parse_result is None or not getattr(parse_result, "target_version_bindings", ()):
        return ops, []

    label_to_cited_ids: dict[str, set[str]] = {}
    for binding in parse_result.target_version_bindings:
        cited_id = str(getattr(binding, "cited_statute_id", "") or "").strip()
        if not cited_id:
            continue
        for label in getattr(binding, "target_labels", ()) or ():
            norm = _norm_num_token(str(label or ""))
            if not norm:
                continue
            label_to_cited_ids.setdefault(norm, set()).add(cited_id)

    findings: List[Finding] = []
    patched: List[AmendmentOp] = []
    for op in ops:
        if op.target_unit_kind != "section" or not op.target_section:
            patched.append(op)
            continue
        target_norm = _norm_num_token(op.target_section)
        cited_ids = sorted(label_to_cited_ids.get(target_norm, ()))
        if not cited_ids:
            patched.append(op)
            continue
        if len(cited_ids) > 1:
            findings.append(
                Finding(
                    kind="ELAB.REJECTED_OPERATION",
                    role="observation",
                    stage="frontend_compile",
                    detail={
                        "message": "Multiple cited-version selectors matched the same section target; selector ownership was left unresolved.",
                        "reason_code": "ELAB.AMBIGUOUS_TARGET_VERSION_SELECTOR",
                        "description": op.description(),
                        "target_section": op.target_section,
                        "candidate_statute_ids": cited_ids,
                    },
                    source_statute=amendment_id,
                    blocking=False,
                )
            )
            patched.append(op)
            continue
        patched.append(dc_replace(op, target_version_statute_id=cited_ids[0]))
    return patched, findings


def _restore_heading_facet_for_mixed_scope_section_replaces(
    ops: List[AmendmentOp],
    *,
    parse_result: "ClauseParseResult | None",
    amendment_id: str,
) -> tuple[List[AmendmentOp], List[Finding]]:
    """Restore heading-scoped replaces when the clause explicitly narrows scope."""
    if parse_result is None:
        return ops, []

    verb_to_op_type = {
        "M": "REPLACE",
        "L": "INSERT",
        "K": "REPEAL",
        "N": "RENUMBER",
    }
    # Use (op_type, part, section) 3-tuples WITHOUT chapter. Parsed ops never
    # carry chapter context here, but compiled ops may have chapter from
    # carry-forward scope — the two would never match if chapter were included.
    heading_keys: set[tuple[str, str, str]] = set()
    descendant_keys: set[tuple[str, str, str]] = set()
    whole_section_keys: set[tuple[str, str, str]] = set()
    for parsed in getattr(parse_result, "parsed_ops", ()):
        section = str(getattr(parsed, "number", "") or "").strip()
        if not section or str(getattr(parsed, "kind", "") or "").strip() != "P":
            continue
        op_type = verb_to_op_type.get(str(getattr(parsed, "verb", "") or "").strip(), "")
        if not op_type:
            continue
        key = (
            op_type,
            str(getattr(parsed, "part", "") or "").strip(),
            section,
        )
        momentti = int(getattr(parsed, "momentti", 0) or 0)
        item = str(getattr(parsed, "item", "") or "").strip()
        facet = getattr(parsed, "facet", None)
        if facet == FacetKind.HEADING:
            heading_keys.add(key)
        elif momentti > 0 or item:
            descendant_keys.add(key)
        else:
            whole_section_keys.add(key)

    candidate_keys = (heading_keys & descendant_keys) - whole_section_keys
    if not candidate_keys:
        return ops, []

    # descendant_scope_present: sections that have ANY descendant-level op
    # (INSERT or REPLACE targeting paragraph/item). Keyed on (part, section)
    # only — op_type is intentionally excluded because an INSERT subsection
    # and a REPLACE section-container are different ops for the same section.
    descendant_scope_present: set[tuple[str, str]] = {
        (
            str(op.target_part or "").strip(),
            str(op.target_section or "").strip(),
        )
        for op in ops
        if op.target_unit_kind == "section"
        and str(op.target_section or "").strip()
        and (op.target_paragraph is not None or bool(op.target_item))
    }

    for op in ops:
        key = (
            str(op.op_type or "").strip(),
            str(op.target_part or "").strip(),
            str(op.target_section or "").strip(),
        )
        descendant_scope_key = (
            str(op.target_part or "").strip(),
            str(op.target_section or "").strip(),
        )
        # Allow explicit heading ops (target_special == "otsikko") as well as
        # plain section replaces to receive the preserve flag when co-occurring
        # with a subsection op for the same section. An explicit "otsikko" op
        # must stay on the heading facet even when the shared XML payload
        # carries subsection children intended for the sibling subsection op.
        is_explicit_heading_op = op.target_special == "otsikko"
        if (
            key not in candidate_keys
            or descendant_scope_key not in descendant_scope_present
            or op.target_unit_kind != "section"
            or op.op_type != "REPLACE"
            or op.target_paragraph is not None
            or bool(op.target_item)
            or (op.target_special is not None and not is_explicit_heading_op)
        ):
            continue
        op.preserve_explicit_heading_facet = True
    return ops, []


_cited_scope_cache: dict[str, dict[str, tuple[str | None, str | None]]] = {}
_cited_effective_date_cache: dict[str, str | None] = {}


def _lift_explicit_scopes_from_cited_version_ops(
    ops: List[AmendmentOp],
    *,
    master: "ReplayState",
    amendment_id: str,
    parent_id: str,
) -> List[AmendmentOp]:
    """Lift explicit chapter/part scope from cited amendment-owned section ops.

    This is the narrow Finland-local bridge for clauses like
    ``30 b § laissa 575/2018``: if the cited amendment itself compiles a unique
    explicit chapter/part scope for that section, carry the same scope onto the
    current root-only op instead of resolving against the stale root lineage.
    """
    relevant_cited_ids = sorted(
        {
            str(op.target_version_statute_id or "")
            for op in ops
            if op.target_version_statute_id and op.target_unit_kind == "section" and op.target_section and not op.target_chapter
        }
    )
    if not relevant_cited_ids:
        return ops

    cs = get_corpus()
    cited_scope_map: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for cited_id in relevant_cited_ids:
        if not cited_id or cited_id == amendment_id:
            continue
        # Cache: avoid recompiling cited amendments across replay calls
        if cited_id in _cited_scope_cache:
            cached = _cited_scope_cache[cited_id]
            if cached:
                cited_scope_map[cited_id] = cached
            continue
        xml_bytes = cs.read_source(cited_id)
        if xml_bytes is None:
            _cited_scope_cache[cited_id] = {}
            continue
        cited_tree = etree.fromstring(xml_bytes)
        cited_title = _tree_title(cited_tree)
        cited_johto = get_johtolause(xml_bytes)
        cited_phase = normalize_and_compile_ops(
            johto=cited_johto,
            muutos_tree=cited_tree,
            master=master,
            amendment_id=cited_id,
            source_title=cited_title,
            used_sec1_fallback=False,
            parent_id=parent_id,
            strict_profile=None,
        )
        section_scopes: dict[str, tuple[str | None, str | None]] = {}
        for cited_op in cited_phase.output:
            if cited_op.target_unit_kind != "section" or not cited_op.target_section:
                continue
            if not cited_op.target_chapter and not cited_op.target_part:
                continue
            section_scopes.setdefault(
                _norm_num_token(cited_op.target_section),
                (cited_op.target_part, cited_op.target_chapter),
            )
        _cited_scope_cache[cited_id] = section_scopes
        if section_scopes:
            cited_scope_map[cited_id] = section_scopes

    if not cited_scope_map:
        return ops

    patched: List[AmendmentOp] = []
    for op in ops:
        cited_id = str(op.target_version_statute_id or "")
        target_norm = _norm_num_token(op.target_section or "")
        scoped_target = cited_scope_map.get(cited_id, {}).get(target_norm)
        if (
            not cited_id
            or not target_norm
            or op.target_unit_kind != "section"
            or op.target_chapter is not None
            or scoped_target is None
        ):
            patched.append(op)
            continue
        target_part, target_chapter = scoped_target
        patched.append(
            dc_replace(
                op,
                target_part=target_part,
                target_chapter=target_chapter,
                scope_confidence=ScopeConfidence(
                    tag="chapter_scope_from_cited_version_binding",
                    source="explicit_chunk",
                    confidence="explicit",
                    resolved_chapter=target_chapter,
                ),
                lo=_lo_with_path_update(op.lo, part=target_part, chapter=target_chapter) if op.lo is not None else op.lo,
            )
        )
    return patched


def _retime_ops_from_cited_version_effective_dates(
    ops: List[AmendmentOp],
) -> List[AmendmentOp]:
    """Defer cited-version-targeted ops to the cited amendment's effective date.

    When a clause explicitly says a target is amended "sellaisena kuin ... laissa
    X/YYYY", the op belongs to that cited pending version family. If the cited
    amendment takes effect later than the current op's own effective date, defer
    the op to the cited amendment's effective date so the later version is not
    overwritten by an older phase ordering.
    """
    relevant_cited_ids = sorted(
        {str(op.target_version_statute_id or "") for op in ops if op.target_version_statute_id}
    )
    if not relevant_cited_ids:
        return ops

    cs = get_corpus()
    cited_effective_dates: dict[str, str] = {}
    for cited_id in relevant_cited_ids:
        if not cited_id:
            continue
        # Cache: avoid re-parsing cited amendment XML for effective dates
        if cited_id in _cited_effective_date_cache:
            cached_date = _cited_effective_date_cache[cited_id]
            if cached_date is not None:
                cited_effective_dates[cited_id] = cached_date
            continue
        xml_bytes = cs.read_source(cited_id)
        if xml_bytes is None:
            _cited_effective_date_cache[cited_id] = None
            continue
        cited_tree = etree.fromstring(xml_bytes)
        cited_effective = _amendment_effective_date(cited_tree)
        if cited_effective is not None:
            cited_effective_dates[cited_id] = cited_effective.isoformat()
            _cited_effective_date_cache[cited_id] = cited_effective.isoformat()
        else:
            _cited_effective_date_cache[cited_id] = None

    if not cited_effective_dates:
        return ops

    patched: List[AmendmentOp] = []
    for op in ops:
        cited_id = str(op.target_version_statute_id or "")
        lo = op.lo
        source = lo.source if (lo is not None and lo.source is not None) else None
        cited_effective_iso = cited_effective_dates.get(cited_id)
        if (
            lo is None
            or source is None
            or not cited_effective_iso
            or not source.effective
            or cited_effective_iso <= source.effective
        ):
            patched.append(op)
            continue
        patched.append(
            dc_replace(
                op,
                lo=dc_replace(
                    lo,
                    source=dc_replace(source, effective=cited_effective_iso),
                    provenance_tags=tuple(lo.provenance_tags) + (f"target_version_effective_from:{cited_id}",),
                ),
            )
        )
    return patched


def _body_chapter_scope_for_section_op(
    *,
    op: AmendmentOp,
    muutos_tree: "etree._Element",
    master: "ReplayState",
) -> str | None:
    """Infer a body chapter for a chapterless section op when the chapter already exists.

    This is the narrow compile-time bridge for amendments like 2013/393 where
    the amendment body places a new section inside an explicit chapter that is
    already part of the master statute. We only attach a chapter when the body
    chapter is unique for the section label and already exists in the master.
    """
    if op.target_unit_kind != "section" or not op.target_section:
        return None
    scope_witness = projection_scope_confidence(
        scope_confidence=op.scope_confidence,
        scope_provenance_tags=op.scope_provenance_tags,
        resolved_chapter=op.target_chapter,
    )
    if op.target_chapter:
        if not (
            op.op_type == "INSERT"
            and op.target_paragraph is None
            and not op.target_item
            and not op.target_special
            and scope_witness is not None
            and scope_witness.source == "carry_forward"
        ):
            return None

    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return None

    def _part_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if str(parent.tag).rsplit("}", 1)[-1] == "part":
                part_num = parent.find("{*}num")
                if part_num is None or not part_num.text:
                    return None
                raw = _norm_num_token(part_num.text).removesuffix("osa")
                arabic = _roman_label_to_arabic(raw.lower()) if raw else None
                return str(arabic) if arabic is not None else (raw or None)
            parent = parent.getparent()
        return None

    section_label = _norm_num_token(op.target_section)
    candidate_chapters: dict[str, etree._Element] = {}
    for sec in body.findall(".//{*}section"):
        num_el = sec.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        sec_label = _norm_num_token(re.sub(r"\s*§.*$", "", num_el.text).strip())
        if sec_label != section_label:
            continue
        if op.target_part:
            body_part = _part_label_for_element(sec)
            if body_part != op.target_part:
                continue
        parent = sec.getparent()
        if parent is None or str(parent.tag).rsplit("}", 1)[-1] != "chapter":
            continue
        chapter_num = parent.find("{*}num")
        if chapter_num is None or not chapter_num.text:
            continue
        chapter_label = _norm_num_token(chapter_num.text).removesuffix("luku")
        if chapter_label:
            candidate_chapters.setdefault(chapter_label, parent)

    if len(candidate_chapters) != 1:
        return None

    chapter_label, chapter_node = next(iter(candidate_chapters.items()))

    if master.find_chapter(chapter_label) is None:
        return None
    if op.target_chapter and chapter_label == op.target_chapter:
        return None

    return chapter_label


def _body_scope_for_section_label(
    *,
    muutos_tree: "etree._Element",
    section_label: str,
) -> tuple[str | None, str | None] | None:
    """Return the unique body-backed (part, chapter) scope for one section label."""
    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return None

    def _part_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if str(parent.tag).rsplit("}", 1)[-1] == "part":
                part_num = parent.find("{*}num")
                if part_num is None or not part_num.text:
                    return None
                raw = _norm_num_token(part_num.text).removesuffix("osa")
                arabic = _roman_label_to_arabic(raw.lower()) if raw else None
                return str(arabic) if arabic is not None else (raw or None)
            parent = parent.getparent()
        return None

    def _chapter_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if str(parent.tag).rsplit("}", 1)[-1] == "chapter":
                chapter_num = parent.find("{*}num")
                if chapter_num is None or not chapter_num.text:
                    return None
                return _norm_num_token(chapter_num.text).removesuffix("luku") or None
            parent = parent.getparent()
        return None

    scopes: set[tuple[str | None, str | None]] = set()
    target_norm = _norm_num_token(section_label)
    for sec in body.findall(".//{*}section"):
        num_el = sec.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        sec_norm = _norm_num_token(re.sub(r"\s*§.*$", "", num_el.text).strip())
        if sec_norm != target_norm:
            continue
        scopes.add((_part_label_for_element(sec), _chapter_label_for_element(sec)))
    if len(scopes) != 1:
        return None
    return next(iter(scopes))


def _master_has_any_chapter(master: "ReplayState") -> bool:
    stack = [master.ir]
    while stack:
        node = stack.pop()
        if node.kind == IRNodeKind.CHAPTER:
            return True
        stack.extend(reversed(node.children))
    return False


def _strip_impossible_chapter_scope_for_bare_body_section_op(
    *,
    op: AmendmentOp,
    muutos_tree: "etree._Element",
    master: "ReplayState",
) -> AmendmentOp | None:
    """Clear chapter scope when a no-chapter statute body proves a bare section target.

    This guards against parent-title leakage like ``rikoslain 1 luvun 7 §`` being
    misread as chapter scope for the amended statute itself. We only clear the
    chapter when the live parent statute has no chapters at all and the
    amendment body uniquely places the target section as a bare top-level
    section.
    """
    if op.target_unit_kind != "section" or not op.target_section or not op.target_chapter:
        return None
    if _master_has_any_chapter(master):
        return None
    body_scope = _body_scope_for_section_label(
        muutos_tree=muutos_tree,
        section_label=op.target_section,
    )
    if body_scope != (None, None):
        return None
    retained_scope_tags = tuple(
        tag for tag in op.scope_provenance_tags if tag != "chapter_scope_carry_forward"
    )
    retained_lo = op.lo
    if retained_lo is not None:
        retained_lo = dc_replace(
            _lo_with_path_update(retained_lo, chapter=None),
            provenance_tags=tuple(
                tag for tag in retained_lo.provenance_tags if tag != "chapter_scope_carry_forward"
            ),
        )
    return dc_replace(
        op,
        target_chapter=None,
        scope_confidence=normalize_scope_confidence(None, resolved_chapter=None),
        scope_provenance_tags=retained_scope_tags,
        lo=retained_lo,
    )


def _retarget_stale_body_scope_for_section_op(
    *,
    op: AmendmentOp,
    muutos_tree: "etree._Element",
    master: "ReplayState",
    johto: str = "",
) -> tuple[str | None, str | None] | None:
    """Retarget stale body-derived scope to the unique live section path.

    Some amendment XML wraps section payloads in an outdated chapter container
    even though the live statute has already rehomed those sections elsewhere.
    Only retarget when:
    - the op already carries chapter scope,
    - the scope came from a bounded explicit source or rewrite lane,
    - the scoped live section does not exist, and
    - the amendment body uniquely places the section under a different part /
      chapter family that resolves to one live section path.
    """
    scope_witness = projection_scope_confidence(
        scope_confidence=op.scope_confidence,
        scope_provenance_tags=op.scope_provenance_tags,
        resolved_chapter=op.target_chapter,
    )
    if (
        op.target_unit_kind != "section"
        or not op.target_section
        or not op.target_chapter
        or (
            scope_witness is not None
            and scope_witness.source not in {"explicit_scope_rewrite", "explicit_chunk"}
        )
    ):
        return None

    section_label = _norm_num_token(op.target_section)
    scoped_path = master.find_section_path(section_label, op.target_chapter, op.target_part)
    if scoped_path is not None:
        return None
    if op.target_chapter and section_label in _same_label_move_sections_for_chapter(johto, op.target_chapter):
        # PEG/clause-surface already owns explicit same-label move destinations
        # like "29 e §, joka samalla siirretään 5 b lukuun". If the live tree
        # still has the old same-labeled section under another chapter, that is
        # evidence of a pending move, not license to rewrite the target back to
        # the old host.
        return None
    if section_label in master.duplicate_section_labels:
        return None

    body_scope = _body_scope_for_section_label(
        muutos_tree=muutos_tree,
        section_label=section_label,
    )
    if body_scope is None:
        return None

    body_part, body_chapter = body_scope
    if (
        op.op_type == "INSERT"
        and op.target_paragraph is None
        and not op.target_item
        and not op.target_special
        and body_chapter == op.target_chapter
        and body_part == op.target_part
    ):
        # A whole-section INSERT whose amendment body already agrees with the
        # explicit source scope is creating a new section there. A same-labeled
        # section elsewhere in the old live tree is not license to hijack the
        # insert into that existing chapter.
        return None

    live_path = master.find_section_path(section_label, body_chapter, body_part)
    if live_path is None:
        live_path = master.find_section_path(section_label, None, body_part)
    if live_path is None:
        return None

    live_part = next((label for kind, label in live_path if kind == "part"), None)
    live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
    if not live_chapter or (live_chapter == op.target_chapter and live_part == op.target_part):
        return None
    return live_part, live_chapter


# ---------------------------------------------------------------------------
# _enrich_ops_from_amendment_tree
# ---------------------------------------------------------------------------


def _enrich_ops_from_amendment_tree(
    ops: List[AmendmentOp],
    amendment_id: str,
    muutos_tree: "etree._Element",
    master: "ReplayState | None" = None,
    johto: str = "",
) -> List[AmendmentOp]:
    """Stamp source-statute metadata (date, title, expiry) onto every op.

    Pure ``(ops, amendment_id, tree) -> ops`` transform.  The lxml tree is read-only.
    """
    source_issue_date = _statute_issue_date(muutos_tree)
    source_title = _tree_title(muutos_tree)
    eff_date = _amendment_effective_date(muutos_tree)
    expiry_date = _amendment_expiry_date(muutos_tree)
    section_expiry_overrides = _temporary_section_expiry_overrides(muutos_tree, amendment_id)
    # Only stamp the expiry on op_source when the amendment has WHOLE-ACT expiry
    # ("Tämä laki on voimassa N päivään ...").  When the expiry is section-scoped
    # ("Lain 43 a—43 c § ovat voimassa ..."), op_source.expires must remain empty
    # so that permanently-modified sections (e.g. 16 §, 18 §, 20 §, 21 § in
    # 2012/991) do not get an erroneous expires stamp.  The section-scoped expiry
    # is applied per-section via the section_expiry_override block below.
    _section_scoped_expiry = any(
        target_mid == amendment_id for target_mid, _labels, _expiry in section_expiry_overrides
    )
    op_source = OperationSource(
        statute_id=amendment_id,
        title=source_title,
        enacted=source_issue_date.isoformat() if source_issue_date else "",
        effective=eff_date.isoformat() if eff_date else "",
        expires="" if _section_scoped_expiry else (expiry_date.isoformat() if expiry_date else ""),
    )
    enriched = []
    for op in ops:
        scoped_op = op
        body_scoped = False
        if master is not None:
            stripped_op = _strip_impossible_chapter_scope_for_bare_body_section_op(
                op=scoped_op,
                muutos_tree=muutos_tree,
                master=master,
            )
            if stripped_op is not None:
                scoped_op = stripped_op
            scope_witness = projection_scope_confidence(
                scope_confidence=scoped_op.scope_confidence,
                scope_provenance_tags=scoped_op.scope_provenance_tags,
                resolved_chapter=scoped_op.target_chapter,
            )
            if (
                scoped_op.op_type == "INSERT"
                and scoped_op.target_unit_kind == "section"
                and scoped_op.target_chapter is not None
                and (
                    scoped_op.target_paragraph is not None
                    or scoped_op.target_item is not None
                    or scoped_op.target_special is not None
                )
                and scope_witness is not None
                and scope_witness.source == "carry_forward"
            ):
                carry_forward_host = master.find_section_path(
                    _norm_num_token(scoped_op.target_section or ""),
                    scoped_op.target_chapter,
                    scoped_op.target_part,
                )
                if carry_forward_host is None:
                    retained_scope_tags = tuple(
                        tag for tag in scoped_op.scope_provenance_tags if tag != "chapter_scope_carry_forward"
                    )
                    retained_lo = scoped_op.lo
                    if retained_lo is not None:
                        retained_lo = dc_replace(
                            _lo_with_path_update(retained_lo, chapter=None),
                            provenance_tags=tuple(
                                tag for tag in retained_lo.provenance_tags if tag != "chapter_scope_carry_forward"
                            ),
                        )
                    scoped_op = dc_replace(
                        scoped_op,
                        target_chapter=None,
                        scope_confidence=normalize_scope_confidence(None, resolved_chapter=None),
                        scope_provenance_tags=retained_scope_tags,
                        lo=retained_lo,
                    )
                    scope_witness = projection_scope_confidence(
                        scope_confidence=scoped_op.scope_confidence,
                        scope_provenance_tags=scoped_op.scope_provenance_tags,
                        resolved_chapter=scoped_op.target_chapter,
                    )
            inferred_chapter = None
            if scoped_op.target_chapter is None or (
                scoped_op.op_type == "INSERT"
                and scoped_op.target_unit_kind == "section"
                and scoped_op.target_paragraph is None
                and scoped_op.target_item is None
                and scoped_op.target_special is None
                and scope_witness is not None
                and scope_witness.source == "carry_forward"
            ):
                inferred_chapter = _body_chapter_scope_for_section_op(
                    op=scoped_op,
                    muutos_tree=muutos_tree,
                    master=master,
                )
            if inferred_chapter is not None:
                body_scoped = True
                scoped_op = dc_replace(
                    scoped_op,
                    target_chapter=inferred_chapter,
                    scope_confidence=normalize_scope_confidence(
                        projection_scope_confidence(
                            scope_confidence=scoped_op.scope_confidence,
                            scope_provenance_tags=scoped_op.scope_provenance_tags,
                            resolved_chapter=inferred_chapter,
                        ),
                        resolved_chapter=inferred_chapter,
                    ),
                    lo=_lo_with_path_update(op.lo, chapter=inferred_chapter) if op.lo is not None else op.lo,
                )
            elif scope_witness is not None and scope_witness.source in {"explicit_scope_rewrite", "explicit_chunk"}:
                body_scoped = True
            if body_scoped:
                retargeted_scope = _retarget_stale_body_scope_for_section_op(
                    op=scoped_op,
                    muutos_tree=muutos_tree,
                    master=master,
                    johto=johto,
                )
                if retargeted_scope is not None:
                    retargeted_part, retargeted_chapter = retargeted_scope
                    stale_body_part = scoped_op.target_part
                    stale_body_chapter = scoped_op.target_chapter
                    retargeted_lo = (
                        _lo_with_path_update(
                            scoped_op.lo,
                            part=retargeted_part,
                            chapter=retargeted_chapter,
                        )
                        if scoped_op.lo is not None
                        else scoped_op.lo
                    )
                    if retargeted_lo is not None:
                        retargeted_lo = dc_replace(
                            retargeted_lo,
                            provenance_tags=tuple(retargeted_lo.provenance_tags)
                            + tuple(
                                tag
                                for tag in (
                                    f"body_part_retargeted_from:{stale_body_part}" if stale_body_part else "",
                                    f"body_chapter_retargeted_from:{stale_body_chapter}" if stale_body_chapter else "",
                                )
                                if tag
                            ),
                        )
                    scoped_op = dc_replace(
                        scoped_op,
                        target_part=retargeted_part,
                        target_chapter=retargeted_chapter,
                        scope_confidence=(
                            ScopeConfidence(
                                tag="body_container_membership_rewrite",
                                source="explicit_scope_rewrite",
                                confidence="rewritten",
                                resolved_chapter=retargeted_chapter,
                            )
                            if scope_witness is not None and scope_witness.source == "explicit_chunk"
                            else normalize_scope_confidence(
                                projection_scope_confidence(
                                    scope_confidence=scoped_op.scope_confidence,
                                    scope_provenance_tags=scoped_op.scope_provenance_tags,
                                    resolved_chapter=retargeted_chapter,
                                ),
                                resolved_chapter=retargeted_chapter,
                            )
                        ),
                        lo=retargeted_lo,
                    )
        enriched.append(
            dc_replace(
                scoped_op,
                source_statute=amendment_id,
                source_issue_date=source_issue_date,
                source_title=source_title,
                lo=dc_replace(scoped_op.lo, source=op_source) if scoped_op.lo is not None else scoped_op.lo,
            )
        )
        if enriched[-1].op_id == "":
            enriched[-1] = dc_replace(enriched[-1], op_id=mint_fallback_op_id(amendment_id, enriched[-1]))
    patched = enriched
    for _target_mid, labels, section_expiry in section_expiry_overrides:
        if _target_mid != amendment_id:
            continue
        next_patched: List[AmendmentOp] = []
        for op in patched:
            if (
                op.target_unit_kind == "section"
                and (op.target_section or "").lower() in labels
                and op.lo is not None
                and op.lo.source is not None
            ):
                next_patched.append(
                    dc_replace(
                        op,
                        lo=dc_replace(
                            op.lo,
                            source=dc_replace(op.lo.source, expires=section_expiry.isoformat()),
                        ),
                    )
                )
            else:
                next_patched.append(op)
        patched = next_patched
    return patched


# ---------------------------------------------------------------------------
# _tag_temporary_ops — tag ops from temporary amendments
# ---------------------------------------------------------------------------


def _temporary_events_for_op(op: AmendmentOp, amendment_id: str) -> tuple[TemporalEvent, ...]:
    """Build executable temporal carriers for one temporary amendment op."""
    source = op.lo.source if (op.lo is not None and op.lo.source is not None) else None
    start_date = (source.effective if source is not None else "") or ""
    end_date = (source.expires if source is not None else "") or ""
    activation_rule = (
        ActivationRule(
            kind="fixed_date",
            effective_date=start_date,
            raw_text=str(getattr(source, "raw_text", "") or ""),
        )
        if start_date
        else ActivationRule(kind="immediate", raw_text=str(getattr(source, "raw_text", "") or ""))
    )
    scope = TemporalScope(
        target_statute=op.source_statute or amendment_id,
        exact_addresses=(op.lo.target,) if op.lo is not None else (),
    )
    event_key = op.op_id or op.target_section or "op"
    events = [
        TemporalEvent(
            event_id=f"fi-temporary:{amendment_id}:{event_key}:commence",
            kind="commence",
            scope=scope,
            effective=start_date,
            source=source,
            activation_rule=activation_rule,
            group_id=amendment_id,
        )
    ]
    if end_date:
        events.append(
            TemporalEvent(
                event_id=f"fi-temporary:{amendment_id}:{event_key}:expire",
                kind="expire",
                scope=scope,
                expires=end_date,
                source=source,
                group_id=amendment_id,
            )
        )
    return tuple(events)


def _body_text_for_temporary_op(
    op: AmendmentOp,
    *,
    muutos_tree: "etree._Element",
) -> str:
    """Return amendment-body text for a section-targeted temporary op."""
    if op.target_unit_kind != "section" or not op.target_section:
        return ""

    target_label = _norm_num_token(op.target_section)
    if not target_label:
        return ""

    for section in muutos_tree.findall(".//{*}section"):
        num_el = section.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        section_label = _norm_num_token(re.sub(r"\s*§.*$", "", num_el.text).strip())
        if section_label != target_label:
            continue
        content_nodes = section.findall(".//{*}content")
        if content_nodes:
            return " ".join(
                etree.tostring(node, method="text", encoding="unicode")
                for node in content_nodes
            )
        return etree.tostring(section, method="text", encoding="unicode")
    return ""


def _tag_temporary_ops(
    ops: List[AmendmentOp],
    *,
    amendment_id: str,
    muutos_tree: "etree._Element",
) -> tuple[List[AmendmentOp], List[TemporalEvent]]:
    """Return a new list with ``is_temporary=True`` on every op.

    Called when the johtolause contains "väliaikaisesti" (or the source title
    contains "väliaikais").  Emits live ``TemporalEvent`` carriers for the
    temporary window instead of the retired activation-shell helper.

    When the op has already been enriched by ``_enrich_ops_from_amendment_tree``
    (i.e. ``op.lo.source`` is set), the live temporal carrier is populated from
    the OperationSource dates:

    - ``effective`` ← ``op.lo.source.effective`` (effective entry-into-force date)
    - ``expires``   ← ``op.lo.source.expires``   (expiry date, if present)

    When no source dates are available, the event still exists as explicit
    temporal authority, but its begin/end payload remains empty rather than
    being fabricated from provenance.

    This is intentionally conservative: the temporal carrier can always be
    narrowed later by commencement/expiry sentence parsing.
    """
    from lawvm.finland.ops import temporary_signal_for_op

    tagged: List[AmendmentOp] = []
    temporal_events: List[TemporalEvent] = []
    for op in ops:
        if temporary_signal_for_op(op):
            tagged.append(op)
            continue
        tagged_op = dc_replace(op, is_temporary=True)
        tagged_op = _apply_inferred_payload_expiry_to_temporary_ops([tagged_op], muutos_tree=muutos_tree)[0]
        temporal_events.extend(_temporary_events_for_op(tagged_op, amendment_id))
        tagged.append(tagged_op)
    return tagged, temporal_events


def _apply_inferred_payload_expiry_to_temporary_ops(
    ops: List[AmendmentOp],
    *,
    muutos_tree: "etree._Element",
) -> List[AmendmentOp]:
    """Stamp inferred expiry on temporary ops when payload text names tax years.

    This is a bounded Finland-local recovery for older temporary tax provisions
    that never include an explicit ``on voimassa`` sunset clause. We only infer
    expiry when the op is already temporary and the amendment body itself names a
    closed tax-year window.
    """
    from lawvm.finland.ops import temporary_signal_for_op

    patched: List[AmendmentOp] = []
    for op in ops:
        lo = op.lo
        source = lo.source if (lo is not None and lo.source is not None) else None
        if (
            lo is not None
            and
            source is not None
            and temporary_signal_for_op(op)
            and not source.expires
        ):
            inferred = _infer_expiry_date_from_temporary_payload_text(
                _body_text_for_temporary_op(op, muutos_tree=muutos_tree)
            )
            if (
                inferred is not None
                and not _expiry_date_precedes_effective_date(inferred, source.effective)
            ):
                patched.append(
                    dc_replace(
                        op,
                        lo=dc_replace(
                            lo,
                            source=dc_replace(source, expires=inferred.isoformat()),
                        ),
                    )
                )
                continue
        patched.append(op)
    return patched


# ---------------------------------------------------------------------------
# _extract_temporary_targets_from_johtolause — per-op temporary scoping
# ---------------------------------------------------------------------------

# "väliaikaisesti" token
_VAALIAIKAISESTI_RE = re.compile(r'\bväliaikaisesti\b', re.IGNORECASE)
# Valid Finnish section label: one or more digits followed by optional letter suffix
# e.g. "5", "21b", "16g", "87a"
_VALID_SECTION_LABEL_RE = re.compile(r'^\d+[a-z]*$', re.IGNORECASE)
_SECTION_REF_RE = re.compile(r'(\d+\s*[a-z]*)\s*§', re.IGNORECASE)
_TEMPORARY_MOMENT_SCOPE_RE = re.compile(
    r'^\s*(?:uusi|uudet)\s+\d+(?:\s*(?:,|ja|sekä|\-|–)\s*\d+)*\s+moment',
    re.IGNORECASE,
)


def _infer_temporary_targets_from_preceding_section_context(
    johto: str,
    *,
    vaali_start: int,
    after_vaali: str,
) -> FrozenSet[str]:
    """Recover section-scoped temporariness for ``uusi N momentti`` clauses.

    Some mixed amendments scope ``väliaikaisesti`` only to a new subsection
    under an already named section, for example:

      ``... lisätään 51 §:ään ... väliaikaisesti uusi 5 momentti ...``

    In that shape there is no section label after ``väliaikaisesti`` to parse,
    but the host section is still explicit in the immediately preceding clause.
    """
    lookahead = after_vaali[:80]
    if _TEMPORARY_MOMENT_SCOPE_RE.match(lookahead) is None:
        return frozenset()

    preceding_matches = list(_SECTION_REF_RE.finditer(johto[:vaali_start]))
    if not preceding_matches:
        return frozenset()

    candidate = _norm_num_token(preceding_matches[-1].group(1))
    if not candidate or _VALID_SECTION_LABEL_RE.match(candidate) is None:
        return frozenset()

    return frozenset({candidate})


def _extract_temporary_targets_from_johtolause(
    johto: str,
) -> Optional[FrozenSet[str]]:
    """Determine which section labels are in the ``väliaikaisesti`` scope.

    Returns:
    - ``None``          — whole-amendment is temporary (tag ALL ops).  Returned
                          when the section labels immediately following
                          ``väliaikaisesti`` (before the first ``§``) cannot
                          be parsed as valid Finnish section identifiers, which
                          happens when a statute name appears between the
                          ``väliaikaisesti`` adverb and the section numbers.
    - ``frozenset``     — only ops whose ``target_section`` (lowercased)
                          matches one of these labels should be tagged.

    Algorithm:
    1. Find the first ``väliaikaisesti`` in the johtolause.
    2. Collect the text fragment between ``väliaikaisesti`` and the first
       ``§`` that follows it (the natural Finnish section-group terminator).
    3. Strip a leading ``uusi``/``uudet`` word (common before section numbers
       in insertion clauses).
    4. Parse and filter to *valid* section labels (digits + optional suffix).
    5. If valid labels are found, return them (section-scoped).
    6. Otherwise return ``None`` (whole-amendment fallback).

    Examples::

        # Whole-amendment: statute name gets in the way → no valid labels
        "muutetaan väliaikaisesti testilain 5 §"
        # fragment = "testilain 5" → "testilain5" not valid → None (whole)

        # Section-scoped: clean section number follows väliaikaisesti
        "lisätään lakiin uusi 4 a §, väliaikaisesti uusi 21 b § sekä ..."
        # fragment = "21 b" → {"21b"} valid → frozenset({"21b"})

        # Section-scoped in multi-verb clause
        "muutetaan X lain 5 § ja lisätään väliaikaisesti uusi 6 §"
        # fragment = "6" → {"6"} valid → frozenset({"6"})
    """
    if _VAALIAIKAISESTI_RE.search(johto) is None:
        return None  # caller already checked, but guard anyway

    all_valid_labels: set[str] = set()
    for m_vaali in _VAALIAIKAISESTI_RE.finditer(johto):
        after_vaali = johto[m_vaali.end():]

        valid_labels: FrozenSet[str] = frozenset()

        # Find the first "§" after this "väliaikaisesti"
        pykala_pos = after_vaali.find('§')
        if pykala_pos >= 0:
            section_fragment = after_vaali[:pykala_pos]
            # Strip a leading "uusi" / "uudet" word (insertion clauses)
            section_fragment = re.sub(r'^\s*(?:uusi|uudet)\s*', '', section_fragment, flags=re.IGNORECASE)
            raw_labels = _parse_section_list_labels(section_fragment)

            # Filter: keep only labels that look like valid Finnish section identifiers.
            # "testilain5", "xlain5", etc. are statute-name artifacts → discard.
            valid_labels = frozenset(
                lbl for lbl in raw_labels if _VALID_SECTION_LABEL_RE.match(lbl)
            )

        if not valid_labels:
            valid_labels = _infer_temporary_targets_from_preceding_section_context(
                johto,
                vaali_start=m_vaali.start(),
                after_vaali=after_vaali,
            )

        all_valid_labels.update(valid_labels)

    if not all_valid_labels:
        # No valid section labels found from any occurrence → whole-amendment fallback
        return None

    return frozenset(all_valid_labels)


# ---------------------------------------------------------------------------
# Enacting-formula body INSERT fallback
# ---------------------------------------------------------------------------

_ENACTING_FORMULA_EXACT = "eduskunnan päätöksen mukaisesti"
_LETTER_SUFFIX_NUM_RE = re.compile(r"^\d+\s+[a-z]\s*§", re.IGNORECASE)
_PLAIN_SECTION_NUM_RE = re.compile(r"^\d+\s*§", re.IGNORECASE)


def _extract_enacting_formula_body_insert_ops_fallback(
    johto: str,
    muutos_tree: "etree._Element",
    master: "ReplayState",
) -> "list[AmendmentOp]":
    """Recover INSERT ops from amendments that encode only the enacting formula.

    Some older amendments (e.g. 1997/147) have only "Eduskunnan päätöksen
    mukaisesti" as their enacting clause, body sections without eId attributes,
    and no block-level amendment instructions.  The johtolause extraction paths
    all return empty, causing the amendment to be silently skipped.

    For these amendments, body sections with letter suffixes (e.g. "26 a §")
    that do not yet exist in the master statute are genuinely new insertions.
    Plain-number sections are ignored — they are presumed replacements handled
    by later amendments with proper johtolause.

    Conditions for triggering:
    - johto (after normalization) matches exactly the enacting formula
    - body has at least one section without an eId attribute
    - at least one such section has a letter-suffix label absent from master
    """
    cleaned = re.sub(r"\s+", " ", johto).strip().lower()
    if cleaned != _ENACTING_FORMULA_EXACT:
        return []
    body = muutos_tree.find(".//{*}body")
    if body is None:
        return []
    sections_no_eid = [s for s in body.findall(".//{*}section") if not s.get("eId")]
    if not sections_no_eid:
        return []
    ops: list[AmendmentOp] = []
    for sec in sections_no_eid:
        num_el = sec.find("{*}num")
        if num_el is None:
            continue
        num_text = (num_el.text or "").strip()
        if not _LETTER_SUFFIX_NUM_RE.match(num_text):
            continue  # plain-number sections handled elsewhere
        label = _norm_num_token(num_text)
        if not label:
            continue
        if master.find_section(label) is not None:
            continue  # already exists — not a new INSERT
        ops.append(AmendmentOp(op_id="", op_type="INSERT", target_section=label, target_unit_kind="section"))
    return ops


def _extract_enacting_formula_body_replace_ops_fallback(
    johto: str,
    muutos_tree: "etree._Element",
    master: "ReplayState",
) -> "list[AmendmentOp]":
    """Recover one direct section REPLACE from enacting-formula-only amendments.

    Some amendments carry only the ceremonial formula ``Eduskunnan päätöksen
    mukaisesti`` in the preamble and encode the operative change directly as one
    numbered section under ``statuteProvisionsWrapper``. In that bounded shape,
    a lone plain-number section that already exists in the master statute is an
    explicit whole-section replacement, not a new insert.

    Guardrails:
    - johto must match the exact ceremonial formula
    - body must not contain parts/chapters
    - there must be exactly one section without an eId
    - the section label must be a plain-number section already present in master
    """
    cleaned = re.sub(r"\s+", " ", johto).strip().lower()
    if cleaned != _ENACTING_FORMULA_EXACT:
        return []
    body = muutos_tree.find(".//{*}body")
    if body is None:
        return []
    if body.find(".//{*}chapter") is not None or body.find(".//{*}part") is not None:
        return []
    sections_no_eid = [s for s in body.findall(".//{*}section") if not s.get("eId")]
    if len(sections_no_eid) != 1:
        return []
    sec = sections_no_eid[0]
    num_el = sec.find("{*}num")
    if num_el is None:
        return []
    num_text = (num_el.text or "").strip()
    if not _PLAIN_SECTION_NUM_RE.match(num_text):
        return []
    label = _norm_num_token(num_text)
    if not label or master.find_section(label) is None:
        return []
    return [AmendmentOp(op_id="", op_type="REPLACE", target_section=label, target_unit_kind="section")]


# ---------------------------------------------------------------------------
# normalize_and_compile_ops — the main frontend extraction orchestrator
# ---------------------------------------------------------------------------


def normalize_and_compile_ops(
    johto: str,
    muutos_tree: "etree._Element",
    master: "ReplayState",
    amendment_id: str,
    source_title: str,
    used_sec1_fallback: bool,
    parent_id: str = "",
    strict_profile: Optional[StrictProfile] = None,
    parse_result: "ClauseParseResult | None" = None,
) -> "PhaseResult":
    """Normalize PEG output and compile to AmendmentOps.

    Extracted from the ``if not _vts_ops_enrich_done:`` block in
    ``process_muutoslaki``.  Pure refactoring — behaviour is identical.

    Takes ``johto`` (already normalized by ``_normalize_johtolause_verbs``),
    runs the PEG extractor, applies the LO normalization chain, compiles to
    ``AmendmentOp`` objects, and runs all fallback recovery paths.

    Args:
        johto:              Normalized johtolause text.
        muutos_tree:        Parsed amendment lxml tree (read-only).
        master:             Master statute being replayed (read for chapter structure).
        amendment_id:                Amendment statute id (for enrichment + logging).
        source_title:       Amendment title (for title-fallback path).
        used_sec1_fallback: True when ``johto`` came from sec_1 body text.
        parent_id:          Parent statute id (for peg-skip check).
        strict_profile:     Optional strictness gate; None uses the caller-provided default behavior.
        parse_result:       Optional precomputed Finland ClauseParseResult for this johtolause.

    Returns:
        PhaseResult where:
        - ``output``         — ``List[AmendmentOp]``
        - ``finding_ledger`` — frontend extraction findings; wrapper
                               observations/obligations remain compatibility
                               projections over this ledger
    """
    from lawvm.core.phase_result import Finding, PhaseResult

    frontend_findings_out: List[Finding] = []

    def _strict_rejected_op_findings(candidate_ops: List[AmendmentOp], *, source: str) -> List[Finding]:
        findings: List[Finding] = []
        for op in candidate_ops:
            failed = FailedOp.from_scope(
                amendment_id=amendment_id,
                description=op.description(),
                reason=f"{source} rejected by strict profile (allows_target_guessing=False)",
                target_section=op.target_section or "",
                target_unit_kind=op.target_unit_kind,
                target_chapter=op.target_chapter,
            )
            detail = {
                **failed.as_detail(),
                "message": failed.reason,
            }
            findings.append(
                Finding(
                    kind="ELAB.REJECTED_OPERATION",
                    role="observation",
                    stage="frontend_compile",
                    detail=detail,
                    source_statute=amendment_id,
                    blocking=False,
                )
            )
            findings.append(
                Finding(
                    kind="ELAB.STRICT_REJECTED_OPERATION",
                    role="obligation",
                    stage="frontend_compile",
                    detail=detail,
                    source_statute=amendment_id,
                    blocking=True,
                )
            )
        return findings

    def _legal_operation_conversion_skip_findings(lo: LegalOperation) -> List[Finding]:
        skip = classify_legal_operation_conversion_skip(lo)
        if skip is None:
            return []
        detail = {
            **skip.as_detail(),
            "source": "AmendmentOp.from_lo",
        }
        findings = [
            Finding(
                kind=skip.finding_kind,
                role="observation",
                stage="frontend_compile",
                detail=detail,
                source_statute=amendment_id,
                blocking=False,
            )
        ]
        if not skip.blocking:
            return findings
        findings.append(Finding(
            kind="ELAB.STRICT_REJECTED_OPERATION",
            role="obligation",
            stage="frontend_compile",
            detail=detail,
            source_statute=amendment_id,
            blocking=True,
        ))
        return findings

    # Normalize typography before any structural parsing: em-dash → en-dash,
    # horizontal space variants (NBSP, thin space, etc.) → plain space.
    johto = _normalize_fi_parse_text(johto)
    _allows_additive_subsection_fallback = "sellaisena kuin se on" in johto.lower()

    peg_skip_for_sec1_repeal_list = used_sec1_fallback and _sec1_fallback_peg_skip_required(johto, parent_id)
    parse_result_local = parse_result
    legal_ops = []
    if not peg_skip_for_sec1_repeal_list:
        if parse_result_local is None:
            parse_result_local = parse_johtolause_clause(johto, statute_id=parent_id or amendment_id)
        legal_ops = extract_johtolause_legal_ops_from_parse_result(parse_result_local)
    if peg_skip_for_sec1_repeal_list:
        frontend_findings_out.append(
            Finding(
                kind="PARSE.PEG_SKIP_SEC1_REPEAL_LIST",
                role="observation",
                stage="frontend_compile",
                detail={
                    "message": "PEG extraction skipped for sec1 repeal-list fallback pattern",
                    "source_statute": amendment_id,
                    "parent_statute": parent_id,
                    "used_sec1_fallback": True,
                    "johto_excerpt": johto[:200],
                },
                source_statute=amendment_id,
                blocking=False,
            )
        )

    # Detect väliaikainen (temporary) amendments.
    # _temporary_targets: frozenset of section labels that are temporary, or
    #   None when the whole amendment is temporary (or when no temporariness).
    # _is_temporary_whole: True when the entire amendment is temporary
    #   (väliaikaisesti modifies the verb or the source title carries the flag).
    _temporary_targets: Optional[FrozenSet[str]] = None
    _is_temporary_whole: bool = False

    if "väliaikais" in johto.lower():
        _targets = _extract_temporary_targets_from_johtolause(johto)
        if _targets is None:
            # "väliaikaisesti" present but not section-scoped → whole amendment
            _is_temporary_whole = True
        else:
            # section-scoped: only specific labels
            _temporary_targets = _targets
        _replay_print(
            f"  [{amendment_id}] VÄLIAIKAINEN — temporary amendment"
            f" (scope={'whole' if _is_temporary_whole else repr(_temporary_targets)},"
            f" title: {source_title[:60]})"
        )
    elif "väliaikais" in source_title.lower():
        # Title-only signal: whole amendment temporary
        _is_temporary_whole = True
        _replay_print(f"  [{amendment_id}] VÄLIAIKAINEN — temporary amendment (title: {source_title[:80]})")

    if legal_ops:
        # LO normalization operates on LegalOperation (Phase 4.5 step 4)
        legal_ops = _strip_unjustified_chapter_scope_from_unique_sections(legal_ops, johto, master)
        legal_ops = _assign_chapter_scope_from_johtolause(legal_ops, johto, master)
        legal_ops = _assign_scope_from_renumber_destinations(legal_ops)
        ops: List[AmendmentOp] = []
        for i, lo in enumerate(legal_ops):
            converted_ops = AmendmentOp.from_lo(lo, i)
            if not converted_ops:
                frontend_findings_out.extend(_legal_operation_conversion_skip_findings(lo))
            ops.extend(converted_ops)
        ops, target_version_findings = _attach_target_version_selectors(
            ops,
            parse_result=parse_result_local,
            amendment_id=amendment_id,
        )
        frontend_findings_out.extend(target_version_findings)
        ops, heading_scope_findings = _restore_heading_facet_for_mixed_scope_section_replaces(
            ops,
            parse_result=parse_result_local,
            amendment_id=amendment_id,
        )
        frontend_findings_out.extend(heading_scope_findings)
        ops = _lift_explicit_scopes_from_cited_version_ops(
            ops,
            master=master,
            amendment_id=amendment_id,
            parent_id=parent_id,
        )
        ops = _supplement_missing_repeals_after_item_shift_clause(ops, johto)
        ops = _supplement_named_table_row_mixed_clause_ops(ops, johto)
        ops = _tag_named_table_row_single_clause_ops(ops, johto)
    else:
        ops = []
    if ops:
        logger.debug("  %s legal_ops → ops: %s", amendment_id, [op.description() for op in ops])
    if ops:
        frontend_findings_out.extend(
            _duplicate_frontend_target_observations(
                ops,
                amendment_id,
                stage="frontend_extraction",
            )
        )
        frontend_findings_out.extend(
            _semantic_collapse_move_or_renumber_observations(
                ops,
                johto,
                amendment_id,
                parse_result=parse_result_local,
            )
        )
        unrecovered_move_relabel_obs = _destinationless_move_or_relabel_observations(ops, johto, amendment_id)
        if unrecovered_move_relabel_obs:
            frontend_findings_out.extend(unrecovered_move_relabel_obs)
            for obs in unrecovered_move_relabel_obs:
                frontend_findings_out.append(
                Finding(
                    kind=obs.kind,
                    role="observation",
                    stage="frontend_compile",
                    detail={
                        **dict(obs.detail),
                        "message": "Move/relabel clause left a destination-less renumber after frontend repairs.",
                        "source_statute": amendment_id,
                    },
                    blocking=False,
                )
            )
        frontend_findings_out.extend(
            _scope_anchor_dependence_observations(
                ops,
                amendment_id,
            )
        )

    # Metadata enrichment (source statute/date/title) on all AmendmentOps
    ops = _enrich_ops_from_amendment_tree(ops, amendment_id, muutos_tree, master, johto=johto)
    ops = _retime_ops_from_cited_version_effective_dates(ops)
    ops = _dedupe_fallback_ops_ir(ops)
    ops = _tag_explicit_item_shift_after_repeal_hints(ops, johto)
    ops, rejected_overbroad_section_repeals = _reject_overbroad_section_repeals_for_deep_targets(
        ops,
        johto=johto,
        amendment_id=amendment_id,
    )
    frontend_findings_out.extend(rejected_overbroad_section_repeals)

    # Tag temporary ops (väliaikaisesti).  This is a coarse signal; the live
    # TemporalEvent carrier still prefers commencement/expiry sentences when
    # they are available separately from the amendment body.
    temporary_temporal_events: List[TemporalEvent] = []
    if ops:
        if _is_temporary_whole:
            ops, temp_events = _tag_temporary_ops(ops, amendment_id=amendment_id, muutos_tree=muutos_tree)
            temporary_temporal_events.extend(temp_events)
        elif _temporary_targets is not None:
            # Section-scoped: only tag ops whose target_section is in the set
            tagged_ops: List[AmendmentOp] = []
            for op in ops:
                if (op.target_section or "").lower() in _temporary_targets:
                    temp_tagged, temp_events = _tag_temporary_ops(
                        [op],
                        amendment_id=amendment_id,
                        muutos_tree=muutos_tree,
                    )
                    tagged_ops.extend(temp_tagged)
                    temporary_temporal_events.extend(temp_events)
                else:
                    tagged_ops.append(op)
            ops = tagged_ops
    if ops:
        ops = _apply_inferred_payload_expiry_to_temporary_ops(ops, muutos_tree=muutos_tree)
    # After tagging, detect ops that are temporary but have no parseable
    # expiry date.  These should produce an explicit degradation observation;
    # the temporal sidecar already carries the real temporary signal.
    if ops:
        from lawvm.finland.ops import temporary_signal_for_op

        patched_ops: List[AmendmentOp] = []
        for op in ops:
            if (
                temporary_signal_for_op(op)
                and op.lo is not None
                and op.lo.source is not None
                and not op.lo.source.expires
            ):
                frontend_findings_out.append(
                    Finding(
                        kind="TIME.UNRESOLVED_TEMPORARY_EXPIRY",
                        role="observation",
                        stage="frontend_compile",
                        detail={
                            "amendment_id": amendment_id,
                            "target_section": op.target_section or "",
                            "target_chapter": op.target_chapter or "",
                        },
                        source_statute=amendment_id,
                        blocking=False,
                    )
                )
            patched_ops.append(op)
        ops = patched_ops

    # Fallback paths (still AmendmentOp-based, skips LO normalization chain)
    # Heuristic #29: parse_ops_fallback_heuristic — gated by allows_target_guessing
    _allows_fallback = strict_profile is None or strict_profile.allows_target_guessing
    fallback_ops = parse_ops_fallback_heuristic(johto)
    if fallback_ops and _allows_fallback:
        logger.debug("  %s fallback_ops: %s", amendment_id, [op.description() for op in fallback_ops])
        enriched_fallback_ops = _enrich_ops_from_amendment_tree(
            fallback_ops,
            amendment_id,
            muutos_tree,
            master,
            johto=johto,
        )
        fallback_plain_insert_count = sum(
            1
            for op in enriched_fallback_ops
            if op.op_type == "INSERT"
            and op.target_special is None
        )
        for op in enriched_fallback_ops:
            op.fallback_provenance = True
            op.extraction_provenance_tags = tuple(
                dict.fromkeys((*op.extraction_provenance_tags, "extraction_fallback_heuristic"))
            )
        enriched_fallback_ops, rejected_overbroad_fallback_repeals = _reject_overbroad_section_repeals_for_deep_targets(
            enriched_fallback_ops,
            johto=johto,
            amendment_id=amendment_id,
        )
        frontend_findings_out.extend(rejected_overbroad_fallback_repeals)
        if not ops:
            ops = enriched_fallback_ops
        elif _allows_additive_subsection_fallback and fallback_plain_insert_count > 0:
            existing_keys = {
                (
                    op.op_type,
                    op.target_section,
                    op.target_paragraph,
                    op.target_item,
                    op.target_special,
                )
                for op in ops
            }
            for op in enriched_fallback_ops:
                key = (
                    op.op_type,
                    op.target_section,
                    op.target_paragraph,
                    op.target_item,
                    op.target_special,
                )
                if key in existing_keys:
                    continue
                ambiguous_unscoped_insert = _ambiguous_unscoped_additive_fallback_insert_observation(
                    ops,
                    op,
                    amendment_id=amendment_id,
                )
                if ambiguous_unscoped_insert is not None:
                    frontend_findings_out.append(ambiguous_unscoped_insert)
                    continue
                if op.op_type != "INSERT":
                    continue
                if op.target_special is not None:
                    continue
                ops.append(op)
                existing_keys.add(key)
    elif fallback_ops:
        frontend_findings_out.extend(
            _strict_rejected_op_findings(
                fallback_ops,
                source="parse_ops_fallback_heuristic",
            )
        )
    if not ops:
        body_replace_ops = _extract_root_replace_ops_from_body_fallback(johto, muutos_tree)
        if body_replace_ops:
            if _allows_fallback:
                logger.debug(
                    "  %s body_replace_ops: %s",
                    amendment_id,
                    [op.description() for op in body_replace_ops],
                )
                ops = _enrich_ops_from_amendment_tree(
                    body_replace_ops,
                    amendment_id,
                    muutos_tree,
                    master,
                    johto=johto,
                )
                for op in ops:
                    op.body_root_replace_fallback = True
                    op.fallback_provenance = True
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_body_root_replace"))
                    )
            else:
                frontend_findings_out.extend(
                    _strict_rejected_op_findings(
                        body_replace_ops,
                        source="_extract_root_replace_ops_from_body_fallback",
                    )
                )
    if not ops:
        ef_replace_ops = _extract_enacting_formula_body_replace_ops_fallback(johto, muutos_tree, master)
        if ef_replace_ops:
            if _allows_fallback:
                logger.debug(
                    "  %s enacting_formula_body_replace_ops: %s",
                    amendment_id,
                    [op.description() for op in ef_replace_ops],
                )
                ops = _enrich_ops_from_amendment_tree(
                    ef_replace_ops,
                    amendment_id,
                    muutos_tree,
                    master,
                    johto=johto,
                )
                for op in ops:
                    op.fallback_provenance = True
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_enacting_formula_body_replace"))
                    )
            else:
                frontend_findings_out.extend(
                    _strict_rejected_op_findings(
                        ef_replace_ops,
                        source="_extract_enacting_formula_body_replace_ops_fallback",
                    )
                )

    if not ops:
        title_fallback_ops = parse_ops_title_fallback(source_title)
        if title_fallback_ops:
            if _allows_fallback:
                logger.debug(
                    "  %s title_fallback_ops: %s",
                    amendment_id,
                    [op.description() for op in title_fallback_ops],
                )
                ops = _enrich_ops_from_amendment_tree(
                    title_fallback_ops,
                    amendment_id,
                    muutos_tree,
                    master,
                    johto=johto,
                )
                for op in ops:
                    op.fallback_provenance = True
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_title_fallback"))
                    )
            else:
                frontend_findings_out.extend(
                    _strict_rejected_op_findings(
                        title_fallback_ops,
                        source="parse_ops_title_fallback",
                    )
                )

    if not ops:
        ef_insert_ops = _extract_enacting_formula_body_insert_ops_fallback(johto, muutos_tree, master)
        if ef_insert_ops:
            if _allows_fallback:
                logger.debug(
                    "  %s enacting_formula_body_insert_ops: %s",
                    amendment_id,
                    [op.description() for op in ef_insert_ops],
                )
                ops = _enrich_ops_from_amendment_tree(
                    ef_insert_ops,
                    amendment_id,
                    muutos_tree,
                    master,
                    johto=johto,
                )
                for op in ops:
                    op.fallback_provenance = True
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_enacting_formula_body_insert"))
                    )
            else:
                frontend_findings_out.extend(
                    _strict_rejected_op_findings(
                        ef_insert_ops,
                        source="_extract_enacting_formula_body_insert_ops_fallback",
                    )
                )

    # Tag sec1 body-text fallback on all ops from this amendment
    if used_sec1_fallback and ops:
        for op in ops:
            op.sec1_body_johto_fallback = True
            op.extraction_provenance_tags = tuple(
                dict.fromkeys((*op.extraction_provenance_tags, "extraction_sec1_body_johto"))
            )
    if not ops:
        frontend_findings_out.append(
            Finding(
                kind="PARSE.EXTRACTION_EMPTY",
                role="observation",
                stage="frontend_compile",
                detail={
                    "message": "PEG and fallback extraction produced no legal operations",
                    "source_statute": amendment_id,
                    "parent_statute": parent_id,
                    "used_sec1_fallback": used_sec1_fallback,
                    "peg_skip_for_sec1_repeal_list": peg_skip_for_sec1_repeal_list,
                },
                source_statute=amendment_id,
                blocking=False,
            )
        )
    if ops:
        frontend_findings_out.extend(
            _duplicate_frontend_target_observations(
                ops,
                amendment_id,
                stage="frontend_ops",
            )
        )

    return PhaseResult(
        output=ops,
        findings=tuple(frontend_findings_out),
        temporal_events=tuple(temporary_temporal_events),
    )


if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState  # noqa: F401
    from lawvm.core.phase_result import PhaseResult
