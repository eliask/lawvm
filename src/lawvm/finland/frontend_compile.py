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
from lawvm.core.regex_safety import compile_classifier_regex
from dataclasses import dataclass, replace as dc_replace
from datetime import date
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, FrozenSet, List, Optional, Sequence

import lxml.etree as etree

_SECTION_LABEL_ORDER_RE = re.compile(r"(\d+)([a-z]?)", flags=re.I)
_DIGITS_RE = re.compile(r"\d+")
_LETTER_SUFFIX_SECTION_RE = re.compile(r"(?P<stem>\d+)[a-z]", flags=re.I)
_LETTER_SUFFIX_CONTINUATION_PREVIOUS_RE = re.compile(r"(\d+)([a-z]?)", flags=re.I)
_LETTER_SUFFIX_CONTINUATION_CURRENT_RE = re.compile(r"(\d+)([a-z])", flags=re.I)

if TYPE_CHECKING:
    from lawvm.core.provenance import SourceAnchor
    from lawvm.finland.johtolause import ClauseParseResult
    from lawvm.finland.source_model import AmendmentSourceModel
    from lawvm.finland.statute import ReplayState

from lawvm.core.ir import IRNode, LegalOperation, OperationSource
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction
from lawvm.core.compile_result import StrictProfile
from lawvm.core.phase_result import Finding
from lawvm.core.statute_validity import expires_on_from_valid_until
from lawvm.core.temporal import ActivationRule, TemporalEvent, TemporalScope
from lawvm.finland.ops import AmendmentOp, OpType
from lawvm.finland.ops import FailedOp
from lawvm.finland.ops import ScopeConfidence
from lawvm.finland.ops import ScopeResolutionConfidence, ScopeResolutionSource
from lawvm.finland.ops import classify_legal_operation_conversion_skip
from lawvm.finland.ops import normalize_scope_confidence, projection_scope_confidence
from lawvm.finland.ops import _lo_with_path_update
from lawvm.finland.ops import _op_target_subsection_label
from lawvm.finland.normalize import (
    _sec1_fallback_peg_skip_required,
    _extract_root_replace_ops_from_body_fallback,
    _dedupe_fallback_ops_ir,
    parse_ops_fallback_heuristic_with_coverage,
    parse_ops_title_fallback,
)
from lawvm.core.clause_ast import ClauseAstLoweringDiagnostic
from lawvm.finland.johtolause import (
    extract_legal_ops_from_parse_result as extract_johtolause_legal_ops_from_parse_result,
    parse_clause as parse_johtolause_clause,
)
from lawvm.finland.johtolause_supplements import (
    _tag_explicit_item_shift_after_repeal_hints,
    _tag_numbered_table_target_clause_ops,
    _supplement_mixed_explicit_clause_ops,
    _supplement_item_and_moment_clause_ops,
    _supplement_jolloin_moment_renumber_ops,
    _supplement_missing_repeals_after_item_shift_clause,
    _supplement_named_table_row_mixed_clause_ops,
    _supplement_sparse_osalta_row_omission_repeals,
    _tag_named_table_row_single_clause_ops,
)
from lawvm.finland.scope import (
    _johtolause_explicitly_mentions_chaptered_section_target,
    _same_label_move_sections_for_chapter,
    _unique_section_chapter,
    infer_letter_suffix_section_chapter_from_stem_host,
    strip_unjustified_chapter_scope_from_unique_sections as _strip_unjustified_chapter_scope_from_unique_sections,
    assign_chapter_scope_from_johtolause as _assign_chapter_scope_from_johtolause,
    assign_scope_from_renumber_destinations as _assign_scope_from_renumber_destinations,
)
from lawvm.finland.scoped_section_resolver import section_paths_for_label
from lawvm.finland.metadata import (
    TemporaryProvisionExpiryOverride,
    _statute_issue_date,
    _amendment_effective_date,
    _amendment_expiry_date,
    _expiry_date_precedes_effective_date,
    _infer_expiry_date_from_temporary_payload_text,
    _temporary_provision_expiry_overrides,
    _temporary_section_expiry_overrides,
    _parse_section_list_labels,
    _normalize_fi_parse_text,
    get_johtolause,
)

from lawvm.finland.corpus import get_corpus
from lawvm.finland.fallback_op_ids import mint_fallback_op_id
from lawvm.finland.helpers import _normalize_source_part_num, _normalize_source_section_num, _norm_num_token
from lawvm.finland.frontend_observations import (
    _duplicate_frontend_target_observations,
    _destinationless_move_or_relabel_observations,
    _semantic_collapse_move_or_renumber_observations,
    _scope_anchor_dependence_observations,
)
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.finland.target_selector_facades import fi_section_target, replace_target

_WHITESPACE_RE = re.compile(r"\s+")
_TEMPORARY_SECTION_PREFIX_RE = re.compile(r"^\s*(?:uusi|uudet)\s*", flags=re.IGNORECASE)

logger = logging.getLogger(__name__)

# Stable witness rule id minted on structural ops recovered by the
# fallback-extraction lane (Heuristic #29, parse_ops_fallback_heuristic_with_coverage).
# These ops carry op.fallback_provenance=True and the
# "extraction_fallback_heuristic" extraction-provenance tag, but no parser-rule
# witness; this id makes the lane visible to the spec ledger.  Diagnostic-only
# metadata with zero replay semantics.
FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID = "fi.fallback_extraction_recovery"
FI_HISTORICAL_TOP_LEVEL_KOHTA_SUBSECTION_RULE_ID = (
    "fi.historical_top_level_kohta_as_subsection"
)
FI_ACT_WIDE_BODY_SECTION_REPLACE_RULE_ID = "fi.act_wide_body_section_replace"
FI_BODY_ROOT_REPLACE_FALLBACK_RULE_ID = "fi.body_root_replace_fallback"
FI_ENACTING_FORMULA_BODY_REPLACE_FALLBACK_RULE_ID = "fi.enacting_formula_body_replace_fallback"
FI_ENACTING_FORMULA_BODY_INSERT_FALLBACK_RULE_ID = "fi.enacting_formula_body_insert_fallback"
FI_TITLE_FALLBACK_RULE_ID = "fi.title_fallback"
_PARENTHESIZED_LEADING_LABEL_RE = re.compile(r"^\s*\((\d+(?:\s*[a-z])?)\)")
_POSTPOSED_KOHTA_LABELS_RE = re.compile(
    r"\bkohd(?:an|at|ien)\s+([0-9a-zA-Z\s,–—\-]+)",
    re.IGNORECASE,
)

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
        fallback_op.op_type != OpType.INSERT
        or fallback_op.target_cols.target_chapter is not None
        or fallback_op.target_cols.target_section is None
        or fallback_op.target_cols.target_paragraph is None
        or fallback_op.target_cols.target_item is None
        or fallback_op.target_cols.target_special is not None
        or "extraction_fallback_heuristic" not in fallback_op.extraction_provenance_tags
    ):
        return None

    candidate_chapters = sorted(
        {
            _norm_num_token(op.target_cols.target_chapter)
            for op in existing_ops
            if op.target_cols.target_section == fallback_op.target_cols.target_section and op.target_cols.target_chapter
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
            "target_section": fallback_op.target_cols.target_section,
            "target_paragraph": fallback_op.target_cols.target_paragraph,
            "target_item": fallback_op.target_cols.target_item,
            "candidate_chapters": candidate_chapters,
        },
        source_statute=amendment_id,
        blocking=False,
    )


def _direct_child_localname(el: "etree._Element") -> str:
    tag = el.tag
    return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _section_num_label(el: "etree._Element") -> str:
    num_el = el.find("{*}num")
    if num_el is None or not num_el.text:
        return ""
    return _normalize_source_section_num(num_el.text)


def _direct_subsection_num_label(el: "etree._Element") -> str | None:
    num_el = el.find("{*}num")
    if num_el is None or not num_el.text:
        return None
    label = _norm_num_token(re.sub(r"\s*(?:mom(?:entti)?|momentin).*$", "", num_el.text).strip())
    return label or None


def _single_body_section_subsection_labels(
    muutos_tree: "etree._Element",
    section_label: str,
) -> tuple[str | None, ...] | None:
    sections = [
        sec
        for sec in muutos_tree.findall(".//{*}section")
        if _section_num_label(sec) == _norm_num_token(section_label)
    ]
    if len(sections) != 1:
        return None
    return tuple(
        _direct_subsection_num_label(child)
        for child in sections[0]
        if _direct_child_localname(child) == "subsection"
    )


def _single_payload_already_owned_fallback_insert_observation(
    existing_ops: List[AmendmentOp],
    fallback_op: AmendmentOp,
    *,
    amendment_id: str,
    muutos_tree: "etree._Element",
) -> Finding | None:
    """Reject fallback subsection inserts that would reuse one unnumbered body payload.

    In mixed-section formulas the coarse fallback can see a later ``uusi 6
    momentti`` and bind it to the previous body section. If that body section
    has exactly one unnumbered subsection payload, and the PEG path already
    emitted a subsection insert for the same section, accepting the fallback
    would smuggle the same payload into a second target.
    """
    if (
        fallback_op.op_type != OpType.INSERT
        or fallback_op.target_cols.target_section is None
        or fallback_op.target_cols.target_paragraph is None
        or fallback_op.target_cols.target_item is not None
        or fallback_op.target_cols.target_special is not None
        or "extraction_fallback_heuristic" not in fallback_op.extraction_provenance_tags
    ):
        return None

    labels = _single_body_section_subsection_labels(muutos_tree, fallback_op.target_cols.target_section)
    if labels != (None,):
        return None

    owned_paragraphs = sorted(
        {
            int(op.target_cols.target_paragraph)
            for op in existing_ops
            if op.op_type == OpType.INSERT
            and op.target_cols.target_section == fallback_op.target_cols.target_section
            and op.target_cols.target_paragraph is not None
            and op.target_cols.target_paragraph != fallback_op.target_cols.target_paragraph
            and op.target_cols.target_item is None
            and op.target_cols.target_special is None
        }
    )
    if not owned_paragraphs:
        return None

    return Finding(
        kind="ELAB.REJECTED_OPERATION",
        role="observation",
        stage="frontend_compile",
        detail={
            "message": "Fallback subsection insert was rejected because the source body has one unnumbered subsection payload already owned by a parsed insert for this section.",
            "reason_code": "ELAB.FALLBACK_INSERT_SINGLE_PAYLOAD_ALREADY_OWNED",
            "description": fallback_op.description(),
            "target_section": fallback_op.target_cols.target_section,
            "target_paragraph": fallback_op.target_cols.target_paragraph,
            "owned_paragraphs": owned_paragraphs,
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
    deep_repeal_sections = {
        _norm_num_token(op.target_cols.target_section or "")
        for op in ops
        if op.op_type == OpType.REPEAL
        and op.target_cols.target_section
        and op.target_cols.target_item is not None
    }
    if not deep_repeal_sections:
        return ops, []

    kept: List[AmendmentOp] = []
    findings: List[Finding] = []
    for op in ops:
        if (
            op.op_type == OpType.REPEAL
            and op.target_cols.target_unit_kind == "section"
            and op.target_cols.target_paragraph is None
            and op.target_cols.target_item is None
            and op.target_cols.target_special is None
            and _norm_num_token(op.target_cols.target_section or "") in deep_repeal_sections
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
                        "target_section": op.target_cols.target_section or "",
                    },
                    source_statute=amendment_id,
                    blocking=False,
                )
            )
            continue
        kept.append(op)
    return kept, findings


def _parser_produced_structural_targets(ops: List[LegalOperation]) -> bool:
    """True when grammar lowering yielded executable structural target ops."""
    structural_actions = {
        StructuralAction.REPEAL,
        StructuralAction.REPLACE,
        StructuralAction.INSERT,
        StructuralAction.RENUMBER,
    }
    return any(op.action in structural_actions and bool(op.target.path) for op in ops)


def _parenthesized_payload_labels_for_section(
    muutos_tree: "etree._Element",
    *,
    section_label: str,
) -> set[str]:
    labels: set[str] = set()
    target = _norm_num_token(section_label)
    for section in muutos_tree.findall(".//{*}section"):
        if _section_num_label(section) != target:
            continue
        for paragraph in section.findall(".//{*}p"):
            text = etree.tostring(paragraph, method="text", encoding="unicode").strip()
            # lawvm-regex: prefilter leading (N) label lexer over the amendment's own body <p> text payload; pure label-token shape, mints no legal state
            match = _PARENTHESIZED_LEADING_LABEL_RE.match(text)
            if match is not None:
                labels.add(_norm_num_token(match.group(1)))
    return labels


def _live_direct_subsection_labels(master: "ReplayState | None", op: AmendmentOp) -> set[str]:
    if master is None or op.target_cols.target_unit_kind != "section" or not op.target_cols.target_section:
        return set()
    section = master.find_section(
        _norm_num_token(op.target_cols.target_section),
        op.target_cols.target_chapter,
        op.target_cols.target_part,
    )
    if section is None:
        return set()
    return {
        _norm_num_token(str(child.label or ""))
        for child in section.children
        if child.kind is IRNodeKind.SUBSECTION and child.label
    }


def _postposed_kohta_labels_for_section(johto: str, *, section_label: str) -> set[str]:
    normalized = _normalize_fi_parse_text(johto)
    lower = normalized.lower()
    needle = f"{_norm_num_token(section_label)} §:n"
    start = lower.find(needle.lower())
    if start < 0:
        return set()
    tail = normalized[start + len(needle) : start + len(needle) + 240]
    tail = re.split(r"\b(?:lisätään|kumotaan|siirretään)\b|[.;]", tail, maxsplit=1, flags=re.IGNORECASE)[0]
    labels: set[str] = set()
    # lawvm-regex: owning_parser postposed-kohta label recognizer over a bounded slice of the FI-owned normalized johto surface; not a cross-plane raw_text read
    for match in _POSTPOSED_KOHTA_LABELS_RE.finditer(tail):
        labels.update(_parse_section_list_labels(match.group(1)))
    return {_norm_num_token(label) for label in labels if _norm_num_token(label).isdigit()}


def _retarget_op_to_subsection(
    op: AmendmentOp,
    *,
    subsection_label: str,
    provenance_tag: str,
    op_id: str | None = None,
) -> AmendmentOp:
    lo = _lo_with_path_update(op.lo, subsection=subsection_label, item=None) if op.lo is not None else None
    return dc_replace(
        op,
        op_id=op_id if op_id is not None else op.op_id,
        **replace_target(
            op,
            target_paragraph=int(subsection_label),
            target_item=None,
            target_special=None,
        ),
        lo=lo,
        extraction_provenance_tags=tuple(
            dict.fromkeys((*op.extraction_provenance_tags, provenance_tag))
        ),
        witness_rule_id=provenance_tag,
    )


def _normalize_historical_top_level_kohta_subsection_ops(
    ops: List[AmendmentOp],
    *,
    johto: str,
    muutos_tree: "etree._Element",
    master: "ReplayState | None",
    amendment_id: str,
) -> tuple[List[AmendmentOp], List[Finding]]:
    """Map old top-level ``kohta`` wording onto sibling subsection targets.

    Some historical Finnish definition sections model parenthesized ``(1)``,
    ``(2)``, ... entries as direct ``subsection`` siblings in the source XML
    while the johtolause calls them ``kohdat``.  The modern parser otherwise
    treats ``N §:n kohdan 24`` as a broad section replace and ``uudet (29) ja
    (30) kohdat`` as item inserts under subsection 1.  Rewrite only when the
    source payload and live section both prove the top-level subsection lane.
    """
    if master is None or not ops:
        return ops, []

    findings: List[Finding] = []
    rewrite_sections: dict[str, set[str]] = {}
    broad_replaces: dict[str, AmendmentOp] = {}

    for op in ops:
        if (
            op.target_cols.target_unit_kind == "section"
            and op.op_type == OpType.REPLACE
            and op.target_cols.target_section
            and op.target_cols.target_paragraph is None
            and op.target_cols.target_item is None
            and op.target_cols.target_special is None
        ):
            section_label = _norm_num_token(op.target_cols.target_section)
            replace_labels = _postposed_kohta_labels_for_section(johto, section_label=section_label)
            payload_labels = _parenthesized_payload_labels_for_section(
                muutos_tree,
                section_label=section_label,
            )
            live_labels = _live_direct_subsection_labels(master, op)
            owned_replace_labels = replace_labels & payload_labels & live_labels
            if owned_replace_labels:
                rewrite_sections[section_label] = owned_replace_labels
                broad_replaces[section_label] = op
                continue

    if not rewrite_sections:
        return ops, []

    emitted_labels_by_section: dict[str, set[str]] = {section: set() for section in rewrite_sections}
    output: List[AmendmentOp] = []
    for op in ops:
        section_label = _norm_num_token(op.target_cols.target_section or "")
        if op is broad_replaces.get(section_label):
            for label in sorted(rewrite_sections[section_label], key=lambda value: int(value)):
                op_id = op.op_id if len(rewrite_sections[section_label]) == 1 else f"{op.op_id}__histkohta_{label}"
                output.append(
                    _retarget_op_to_subsection(
                        op,
                        subsection_label=label,
                        provenance_tag=FI_HISTORICAL_TOP_LEVEL_KOHTA_SUBSECTION_RULE_ID,
                        op_id=op_id,
                    )
                )
                emitted_labels_by_section.setdefault(section_label, set()).add(label)
            continue
        payload_labels = (
            _parenthesized_payload_labels_for_section(muutos_tree, section_label=section_label)
            if section_label in rewrite_sections
            else set()
        )
        if (
            section_label in rewrite_sections
            and op.target_cols.target_unit_kind == "section"
            and op.op_type in {OpType.INSERT, OpType.REPLACE}
            and op.target_cols.target_paragraph == 1
            and op.target_cols.target_item
            and op.target_cols.target_special is None
        ):
            item_label = _norm_num_token(op.target_cols.target_item)
            if item_label.isdigit() and item_label in payload_labels:
                retargeted = _retarget_op_to_subsection(
                    op,
                    subsection_label=item_label,
                    provenance_tag=FI_HISTORICAL_TOP_LEVEL_KOHTA_SUBSECTION_RULE_ID,
                )
                output.append(retargeted)
                emitted_labels_by_section.setdefault(section_label, set()).add(item_label)
                continue
        output.append(op)

    for section_label, labels in rewrite_sections.items():
        broad = broad_replaces[section_label]
        emitted = sorted(emitted_labels_by_section.get(section_label, ()), key=lambda value: int(value))
        findings.append(
            Finding(
                kind="ELAB.HISTORICAL_TOP_LEVEL_ITEM_AS_SUBSECTION",
                role="observation",
                stage="frontend_compile",
                detail={
                    "message": (
                        "Historical top-level kohta wording was mapped to direct subsection targets "
                        "because source payload and live section expose parenthesized subsection siblings."
                    ),
                    "rule_id": FI_HISTORICAL_TOP_LEVEL_KOHTA_SUBSECTION_RULE_ID,
                    "target_section": section_label,
                    "retargeted_labels": emitted,
                    "broad_replace_description": broad.description(),
                },
                source_statute=amendment_id,
                blocking=False,
            )
        )

    return output, findings


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
        if op.target_cols.target_unit_kind != "section" or not op.target_cols.target_section:
            patched.append(op)
            continue
        target_norm = _norm_num_token(op.target_cols.target_section)
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
                        "target_section": op.target_cols.target_section,
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
            str(op.target_cols.target_part or "").strip(),
            str(op.target_cols.target_section or "").strip(),
        )
        for op in ops
        if op.target_cols.target_unit_kind == "section"
        and str(op.target_cols.target_section or "").strip()
        and (op.target_cols.target_paragraph is not None or bool(op.target_cols.target_item))
    }

    for op in ops:
        key = (
            str(op.op_type or "").strip(),
            str(op.target_cols.target_part or "").strip(),
            str(op.target_cols.target_section or "").strip(),
        )
        descendant_scope_key = (
            str(op.target_cols.target_part or "").strip(),
            str(op.target_cols.target_section or "").strip(),
        )
        # Allow explicit heading ops (target_special == "otsikko") as well as
        # plain section replaces to receive the preserve flag when co-occurring
        # with a subsection op for the same section. An explicit "otsikko" op
        # must stay on the heading facet even when the shared XML payload
        # carries subsection children intended for the sibling subsection op.
        is_explicit_heading_op = op.target_cols.target_special == "otsikko"
        if (
            key not in candidate_keys
            or descendant_scope_key not in descendant_scope_present
            or op.target_cols.target_unit_kind != "section"
            or op.op_type != OpType.REPLACE
            or op.target_cols.target_paragraph is not None
            or bool(op.target_cols.target_item)
            or (op.target_cols.target_special is not None and not is_explicit_heading_op)
        ):
            continue
        op.preserve_explicit_heading_facet = True
    return ops, []


_cited_scope_cache: dict[tuple[str, str, int], dict[str, tuple[str | None, str | None]]] = {}
_cited_effective_date_cache: dict[str, str | None] = {}
_REINSTATEMENT_SECTION_LIST_FRAGMENT = (
    r"\d{1,4}(?:\s*[a-zäöå])?"
    r"(?:\s*(?:,|ja|sekä)\s*\d{1,4}(?:\s*[a-zäöå])?){0,60}"
)
_REINSTATEMENT_SECTION_LABEL_RE = re.compile(
    r"\A\d{1,4}(?:\s*[a-zäöå])?\Z",
    flags=re.I,
)
_REINSTATEMENT_SECTION_LIST_SEPARATOR_RE = re.compile(
    r"\s*(?:,|\bja\b|\bsekä\b)\s*",
    flags=re.I,
)
_REPEALED_SECTION_REPLACEMENT_LIST_RE = re.compile(
    rf"kumot[a-zäöå]{{0,20}}\s+(?P<old>{_REINSTATEMENT_SECTION_LIST_FRAGMENT})"
    rf"\s*§\s*:\s*n\s+tilalle\s+uusi\s+(?P<new>{_REINSTATEMENT_SECTION_LIST_FRAGMENT})\s*§",
    flags=re.I,
)
_LOCAL_CHAPTER_INSERT_SCOPE_BEFORE_REINSTATEMENT_RE = re.compile(
    r"(?:\A|[,;]|\bsekä\b|\bja\b)\s*"
    r"(?:lisätään\s+)?"
    r"(?:\d{1,4}(?:\s*[a-zäöå])?\s+)?lukuun\b[^,;]{0,160}\Z",
    flags=re.I,
)
_CITED_REPEALED_SECTION_REPLACEMENT_RE = re.compile(
    rf"(?:mainitulla\s+\w+\s+|(?:siitä\s+)?lailla\s+)"
    rf"(?P<num>\d{{1,5}})/(?P<year>\d{{4}})\s+"
    rf"kumot[a-zäöå]{{0,20}}\s+(?P<old>{_REINSTATEMENT_SECTION_LIST_FRAGMENT})"
    rf"\s*§\s*:\s*n\s+tilalle\s+uusi\s+(?P<new>{_REINSTATEMENT_SECTION_LIST_FRAGMENT})\s*§",
    flags=re.I,
)


def _normalized_reinstatement_section_list(text: str) -> tuple[str, ...]:
    labels: list[str] = []
    for part in _REINSTATEMENT_SECTION_LIST_SEPARATOR_RE.split(text or ""):
        if not part or _REINSTATEMENT_SECTION_LABEL_RE.fullmatch(part) is None:
            return ()
        labels.append(_norm_num_token(part))
    return tuple(labels)


def _matching_reinstatement_lists(old_text: str, new_text: str) -> tuple[str, ...]:
    old_labels = _normalized_reinstatement_section_list(old_text)
    new_labels = _normalized_reinstatement_section_list(new_text)
    if not old_labels or old_labels != new_labels:
        return ()
    return old_labels


def _reinstatement_match_has_local_chapter_insert_scope(
    normalized_johto: str,
    match_start: int,
) -> bool:
    prefix = normalized_johto[max(0, match_start - 180) : match_start]
    # lawvm-regex: prefilter bounded local-chapter-insert scope disambiguation on owned normalized johto prefix; mints no legal state
    return _LOCAL_CHAPTER_INSERT_SCOPE_BEFORE_REINSTATEMENT_RE.search(prefix) is not None


def _compiled_cited_section_scopes(
    *,
    cited_id: str,
    amendment_id: str,
    parent_id: str,
    master: "ReplayState",
) -> dict[str, tuple[str | None, str | None]]:
    if not cited_id or cited_id == amendment_id:
        return {}
    cache_key = (parent_id, cited_id, id(master.ir))
    if cache_key in _cited_scope_cache:
        return _cited_scope_cache[cache_key]

    cs = get_corpus()
    xml_bytes = cs.read_source(cited_id)
    if xml_bytes is None:
        _cited_scope_cache[cache_key] = {}
        return {}

    cited_tree = etree.fromstring(xml_bytes)
    cited_title = _tree_title(cited_tree)
    cited_johto = get_johtolause(xml_bytes)
    cited_phase = normalize_and_compile_ops(
        johto=cited_johto,
        muutos_tree=cited_tree,
        master=master,
        base_ir=None,
        amendment_id=cited_id,
        source_title=cited_title,
        used_preamble_body_fallback=False,
        parent_id=parent_id,
        strict_profile=None,
    )
    section_scopes: dict[str, tuple[str | None, str | None]] = {}
    for cited_op in cited_phase.output:
        if cited_op.target_cols.target_unit_kind != "section" or not cited_op.target_cols.target_section:
            continue
        if not cited_op.target_cols.target_chapter and not cited_op.target_cols.target_part:
            continue
        section_scopes.setdefault(
            _norm_num_token(cited_op.target_cols.target_section),
            (cited_op.target_cols.target_part, cited_op.target_cols.target_chapter),
        )
    _cited_scope_cache[cache_key] = section_scopes
    return section_scopes


def _cited_repealed_section_scope_for_replacement(
    *,
    johto: str,
    section_norm: str,
    amendment_id: str,
    parent_id: str,
    master: "ReplayState",
) -> tuple[str | None, str | None] | None:
    normalized = _normalize_fi_parse_text(johto)
    if not normalized or "kumot" not in normalized or "tilalle" not in normalized:
        return None
    # lawvm-regex: owning_parser substring-guarded (kumot/tilalle) single-pass reinstatement recognizer over the FI-owned normalized johto; not a cross-plane raw_text read
    for match in _CITED_REPEALED_SECTION_REPLACEMENT_RE.finditer(normalized):
        if _reinstatement_match_has_local_chapter_insert_scope(normalized, match.start()):
            continue
        reinstated_sections = _matching_reinstatement_lists(
            match.group("old"),
            match.group("new"),
        )
        if section_norm not in reinstated_sections:
            continue
        cited_id = f"{match.group('year')}/{int(match.group('num'))}"
        scoped_target = _compiled_cited_section_scopes(
            cited_id=cited_id,
            amendment_id=amendment_id,
            parent_id=parent_id,
            master=master,
        ).get(section_norm)
        if scoped_target is not None and scoped_target[1] is not None:
            return scoped_target
    return None


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
            if op.target_version_statute_id and op.target_cols.target_unit_kind == "section" and op.target_cols.target_section and not op.target_cols.target_chapter
        }
    )
    if not relevant_cited_ids:
        return ops

    cited_scope_map: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for cited_id in relevant_cited_ids:
        cited_scopes = _compiled_cited_section_scopes(
            cited_id=cited_id,
            amendment_id=amendment_id,
            parent_id=parent_id,
            master=master,
        )
        if cited_scopes:
            cited_scope_map[cited_id] = cited_scopes

    if not cited_scope_map:
        return ops

    patched: List[AmendmentOp] = []
    for op in ops:
        cited_id = str(op.target_version_statute_id or "")
        target_norm = _norm_num_token(op.target_cols.target_section or "")
        scoped_target = cited_scope_map.get(cited_id, {}).get(target_norm)
        if (
            not cited_id
            or not target_norm
            or op.target_cols.target_unit_kind != "section"
            or op.target_cols.target_chapter is not None
            or scoped_target is None
        ):
            patched.append(op)
            continue
        target_part, target_chapter = scoped_target
        if target_chapter is not None and master.find_section_path(
            target_norm,
            target_chapter,
            target_part,
        ) is None and master.find_section_path(target_norm, None, op.target_cols.target_part) is not None:
            patched.append(op)
            continue
        patched.append(
            dc_replace(
                op,
                **replace_target(
                    op,
                    target_part=target_part,
                    target_chapter=target_chapter,
                ),
                scope_confidence=ScopeConfidence(
                    tag="chapter_scope_from_cited_version_binding",
                    source=ScopeResolutionSource.EXPLICIT_CHUNK,
                    confidence=ScopeResolutionConfidence.EXPLICIT,
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
    johto: str = "",
    source_model: "AmendmentSourceModel | None" = None,
) -> str | None:
    """Infer a body chapter for a chapterless section op when the chapter already exists.

    This is the narrow compile-time bridge for amendments like 2013/393 where
    the amendment body places a new section inside an explicit chapter that is
    already part of the master statute. We only attach a chapter when the body
    chapter is unique for the section label and already exists in the master.
    """
    if op.target_cols.target_unit_kind != "section" or not op.target_cols.target_section:
        return None
    scope_witness = projection_scope_confidence(
        scope_confidence=op.scope_confidence,
        scope_provenance_tags=op.scope_provenance_tags,
        resolved_chapter=op.target_cols.target_chapter,
    )
    if op.target_cols.target_chapter:
        if not (
            op.op_type == OpType.INSERT
            and op.target_cols.target_paragraph is None
            and not op.target_cols.target_item
            and not op.target_cols.target_special
            and scope_witness is not None
            and scope_witness.source is ScopeResolutionSource.CARRY_FORWARD
        ):
            return None

    section_label = _norm_num_token(op.target_cols.target_section)
    if source_model is not None:
        chapter_label = source_model.unique_body_section_chapter(
            section_label,
            target_part=op.target_cols.target_part,
        )
        if chapter_label is None:
            return None
    else:
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
                    return _normalize_source_part_num(part_num.text) or None
                parent = parent.getparent()
            return None

        candidate_chapters: dict[str, etree._Element] = {}
        for sec in body.findall(".//{*}section"):
            num_el = sec.find("{*}num")
            if num_el is None or not num_el.text:
                continue
            sec_label = _normalize_source_section_num(num_el.text)
            if sec_label != section_label:
                continue
            if op.target_cols.target_part:
                body_part = _part_label_for_element(sec)
                if body_part != op.target_cols.target_part:
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

        chapter_label = next(iter(candidate_chapters))

    if op.target_cols.target_chapter and chapter_label == op.target_cols.target_chapter:
        return None
    if _body_chapter_scope_conflicts_with_unchaptered_live_target(
        op=op,
        master=master,
        source_model=source_model,
        chapter_label=chapter_label,
        johto=johto,
    ):
        return None
    if master.find_chapter(chapter_label) is None:
        if not (
            op.target_cols.target_chapter
            and scope_witness is not None
            and scope_witness.source is ScopeResolutionSource.CARRY_FORWARD
            and _source_declares_chapter_heading_wave(muutos_tree=muutos_tree, johto=johto)
        ):
            return None

    return chapter_label


def _live_section_path_is_unchaptered(
    *,
    master: "ReplayState",
    section_norm: str,
    target_part: str | None,
) -> bool:
    live_path = master.find_section_path(section_norm, None, target_part)
    if live_path is None and target_part is not None:
        live_path = master.find_section_path(section_norm, None, None)
    if live_path is None or section_norm in master.duplicate_section_labels:
        return False
    return not any(kind == "chapter" for kind, _label in live_path)


def _body_chapter_scope_conflicts_with_unchaptered_live_target(
    *,
    op: AmendmentOp,
    master: "ReplayState",
    source_model: "AmendmentSourceModel | None",
    chapter_label: str,
    johto: str,
) -> bool:
    """Reject stale source-body chapter wrappers over root-level live sections."""
    section_norm = _norm_num_token(op.target_cols.target_section or "")
    if (
        op.target_cols.target_paragraph is not None
        or op.target_cols.target_item is not None
        or op.target_cols.target_special is not None
        or _johtolause_explicitly_mentions_chaptered_section_target(
            johto,
            chapter_label,
            section_norm,
        )
    ):
        return False
    if (
        op.op_type in {OpType.REPLACE, OpType.REPEAL}
        and _live_section_path_is_unchaptered(
            master=master,
            section_norm=section_norm,
            target_part=op.target_cols.target_part,
        )
    ):
        return True
    if source_model is None or not _is_whole_section_insert(op):
        return False
    suffix_match = _LETTER_SUFFIX_SECTION_RE.fullmatch(section_norm)
    if suffix_match is None:
        return False
    stem_norm = suffix_match.group("stem")
    if not _live_section_path_is_unchaptered(
        master=master,
        section_norm=stem_norm,
        target_part=op.target_cols.target_part,
    ):
        return False
    stem_body_scope = source_model.body_section_scope(stem_norm)
    if stem_body_scope is None:
        return False
    _stem_body_part, stem_body_chapter = stem_body_scope
    return stem_body_chapter == chapter_label


def _is_whole_section_insert(op: AmendmentOp) -> bool:
    return (
        op.op_type == OpType.INSERT
        and op.target_cols.target_unit_kind == "section"
        and bool(op.target_cols.target_section)
        and op.target_cols.target_paragraph is None
        and op.target_cols.target_item is None
        and op.target_cols.target_special is None
    )


def _part_label_for_source_element(el: etree._Element) -> str | None:
    parent = el.getparent()
    while parent is not None:
        if str(parent.tag).rsplit("}", 1)[-1] == "part":
            part_num = parent.find("{*}num")
            if part_num is None or not part_num.text:
                return None
            return _normalize_source_part_num(part_num.text) or None
        parent = parent.getparent()
    return None


def _source_body_carries_whole_section(
    *,
    muutos_tree: etree._Element,
    section_norm: str,
    target_part: str | None,
    source_model: "AmendmentSourceModel | None" = None,
) -> bool:
    """True when the amendment body carries the whole section as new text.

    Unlike :func:`_source_body_has_flat_whole_section`, the section may sit
    under a chapter container (not only directly under body/hcontainer); the
    amendment supplies the full section payload either way.
    """
    if source_model is not None:
        return source_model.body_carries_whole_section(
            section_norm,
            target_part=target_part,
        )

    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return False
    for sec in body.findall(".//{*}section"):
        num_el = sec.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        if _normalize_source_section_num(num_el.text) != section_norm:
            continue
        if _part_label_for_source_element(sec) != target_part:
            continue
        return True
    return False


def _is_identity_whole_section_renumber(op: AmendmentOp) -> bool:
    """True when a whole-section RENUMBER renames a section to its own label.

    An identity renumber (destination leaf label == target leaf label, with no
    container relabel) is semantically a no-op rename. It arises when a move
    verb group (``... johon samalla siirretään ... §, sekä N-M §``) over-extends
    across a ``sekä`` coordination and sweeps trailing new-section targets into
    the SIIRTAA verb group. Such targets carry no real relabel — their true verb
    is the outer ``lisätään`` (insert).
    """
    if op.op_type != OpType.RENUMBER or op.target_cols.target_unit_kind != "section":
        return False
    if not op.target_cols.target_section:
        return False
    if op.target_cols.target_paragraph is not None or op.target_cols.target_item or op.target_cols.target_special:
        return False
    lo = op.lo
    if lo is None or lo.target is None or lo.destination is None:
        return False
    target_leaf = lo.target.path[-1] if lo.target.path else None
    dest_leaf = lo.destination.path[-1] if lo.destination.path else None
    if target_leaf is None or dest_leaf is None:
        return False
    if target_leaf != dest_leaf:
        return False
    # A genuine container move encodes on the destination a chapter/part that
    # differs from the target's. The destination is frequently un-enriched
    # (carries no chapter/part) while the target picked up a body-inferred
    # chapter scope; that is still an identity rename, not a move. Reject only
    # when the destination explicitly names a container that conflicts with the
    # target's same-kind container.
    target_by_kind = {kind: label for kind, label in lo.target.path[:-1]}
    for kind, label in lo.destination.path[:-1]:
        if target_by_kind.get(kind, label) != label:
            return False
    return True


def _lo_to_whole_section_insert(lo: "LegalOperation") -> "LegalOperation":
    """Rewrite an identity-renumber LegalOperation into a whole-section INSERT.

    ``destination`` must be cleared because LegalOperation only permits it on
    RENUMBER actions.
    """
    return dc_replace(
        lo,
        action=StructuralAction.INSERT,
        destination=None,
        provenance_tags=tuple(lo.provenance_tags)
        + ("identity_renumber_absent_target_to_insert",),
    )


def _lo_to_declared_move_replace(lo: "LegalOperation") -> "LegalOperation":
    """Rewrite an identity-renumber residue into a source-declared move replace."""
    return dc_replace(
        lo,
        action=StructuralAction.REPLACE,
        destination=None,
        move_clause_target_unit_kind="chapter",
        provenance_tags=tuple(lo.provenance_tags)
        + ("identity_renumber_declared_move_to_replace",),
    )


def _source_body_has_flat_whole_section(
    *,
    muutos_tree: etree._Element,
    section_norm: str,
    target_part: str | None,
) -> bool:
    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return False
    for sec in body.findall(".//{*}section"):
        num_el = sec.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        sec_label = _normalize_source_section_num(num_el.text)
        if sec_label != section_norm:
            continue
        if _part_label_for_source_element(sec) != target_part:
            continue
        parent = sec.getparent()
        if parent is None:
            continue
        if etree.QName(parent.tag).localname in {"body", "hcontainer"}:
            return True
    return False


def _infer_flat_body_insert_chapter_from_bracketing_live_siblings(
    *,
    op: AmendmentOp,
    muutos_tree: etree._Element,
    master: "ReplayState",
) -> str | None:
    """Infer a flat source-body section insert's chapter from bracketing live siblings."""
    if not _is_whole_section_insert(op) or op.target_cols.target_chapter is not None:
        return None
    section_norm = _norm_num_token(op.target_cols.target_section)
    if _DIGITS_RE.fullmatch(section_norm) is None:
        return None
    if not _source_body_has_flat_whole_section(
        muutos_tree=muutos_tree,
        section_norm=section_norm,
        target_part=op.target_cols.target_part,
    ):
        return None

    target_num = int(section_norm)
    lower_by_distance: dict[int, set[tuple[str | None, str]]] = {}
    upper_by_distance: dict[int, set[tuple[str | None, str]]] = {}

    def _walk(node: object, current_part: str | None, current_chapter: str | None) -> None:
        kind = getattr(node, "kind", None)
        label = getattr(node, "label", None)
        next_part = current_part
        next_chapter = current_chapter
        if kind is IRNodeKind.PART:
            next_part = _norm_num_token(str(label or ""))
        elif kind is IRNodeKind.CHAPTER:
            next_chapter = _norm_num_token(str(label or ""))
        elif kind is IRNodeKind.SECTION and label and next_chapter:
            sibling_norm = _norm_num_token(str(label))
            if _DIGITS_RE.fullmatch(sibling_norm) is not None and (
                op.target_cols.target_part is None or next_part == op.target_cols.target_part
            ):
                sibling_num = int(sibling_norm)
                distance = abs(sibling_num - target_num)
                if 0 < distance <= 2:
                    bucket = lower_by_distance if sibling_num < target_num else upper_by_distance
                    bucket.setdefault(distance, set()).add((next_part, next_chapter))
        for child in getattr(node, "children", ()):
            _walk(child, next_part, next_chapter)

    _walk(master.ir, None, None)
    if not lower_by_distance or not upper_by_distance:
        return None
    lower_scopes = lower_by_distance[min(lower_by_distance)]
    upper_scopes = upper_by_distance[min(upper_by_distance)]
    if len(lower_scopes) != 1 or lower_scopes != upper_scopes:
        return None
    _part, chapter = next(iter(lower_scopes))
    return chapter


def _infer_flat_body_replace_scope_from_bracketing_live_siblings(
    *,
    op: AmendmentOp,
    muutos_tree: etree._Element,
    master: "ReplayState",
) -> tuple[str | None, str] | None:
    """Infer scope for a flat source-body REPLACE from neighboring live sections.

    This covers source bodies that serialize a chapter-scoped run as flat
    sibling sections while the johtolause names one descendant target without
    repeating the chapter, e.g. ``78 §, 79 §:n 1 momentti, 80 §``.  The exact
    target label may be duplicated by temporary provisions, so the witness is
    the closest lower and upper source-neighbor labels that already agree on a
    live chapter.
    """
    if (
        op.op_type != OpType.REPLACE
        or op.target_cols.target_unit_kind != "section"
        or not op.target_cols.target_section
        or op.target_cols.target_chapter is not None
        or op.target_cols.target_special is not None
        or op.target_cols.target_item
    ):
        return None
    section_norm = _norm_num_token(op.target_cols.target_section)
    target_order = _section_label_order_key(section_norm)
    if target_order is None:
        return None
    if not _source_body_has_flat_whole_section(
        muutos_tree=muutos_tree,
        section_norm=section_norm,
        target_part=op.target_cols.target_part,
    ):
        return None

    target_num, target_suffix_rank = target_order
    lower_by_distance: dict[tuple[int, int], set[tuple[str | None, str]]] = {}
    upper_by_distance: dict[tuple[int, int], set[tuple[str | None, str]]] = {}

    def _walk(node: IRNode, current_part: str | None, current_chapter: str | None) -> None:
        next_part = current_part
        next_chapter = current_chapter
        if node.kind is IRNodeKind.PART:
            next_part = _norm_num_token(str(node.label or "")) or None
        elif node.kind is IRNodeKind.CHAPTER:
            next_chapter = _norm_num_token(str(node.label or "")) or None
        elif node.kind is IRNodeKind.SECTION and node.label and next_chapter:
            sibling_norm = _norm_num_token(str(node.label))
            if sibling_norm == section_norm:
                return
            sibling_order = _section_label_order_key(sibling_norm)
            if sibling_order is not None and (op.target_cols.target_part is None or next_part == op.target_cols.target_part):
                sibling_num, sibling_suffix_rank = sibling_order
                numeric_distance = abs(sibling_num - target_num)
                if numeric_distance <= 2:
                    bucket = lower_by_distance if sibling_order < target_order else upper_by_distance
                    rank_distance = abs(sibling_suffix_rank - target_suffix_rank)
                    bucket.setdefault((numeric_distance, rank_distance), set()).add((next_part, next_chapter))
            return
        for child in node.children:
            _walk(child, next_part, next_chapter)

    _walk(master.ir, None, None)
    if not lower_by_distance or not upper_by_distance:
        return None
    lower_scopes = lower_by_distance[min(lower_by_distance)]
    upper_scopes = upper_by_distance[min(upper_by_distance)]
    if len(lower_scopes) != 1 or lower_scopes != upper_scopes:
        return None
    part, chapter = next(iter(lower_scopes))
    if master.find_section_path(section_norm, chapter, part) is None:
        return None
    return part, chapter


def _section_label_order_key(section_norm: str) -> tuple[int, int] | None:
    match = _SECTION_LABEL_ORDER_RE.fullmatch(section_norm)
    if match is None:
        return None
    suffix = match.group(2).lower()
    return int(match.group(1)), (ord(suffix) - ord("a") + 1) if suffix else 0


def _flat_source_body_section_nums(
    *,
    muutos_tree: etree._Element,
    target_part: str | None,
) -> set[int]:
    """Return integer section labels carried as flat whole sections in source body."""
    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return set()
    labels: set[int] = set()
    for sec in body.findall(".//{*}section"):
        parent = sec.getparent()
        if parent is None or etree.QName(parent.tag).localname not in {"body", "hcontainer"}:
            continue
        if _part_label_for_source_element(sec) != target_part:
            continue
        num_el = sec.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        sec_label = _normalize_source_section_num(num_el.text)
        if _DIGITS_RE.fullmatch(sec_label) is None:
            continue
        labels.add(int(sec_label))
    return labels


def _infer_flat_body_replace_chapter_from_live_section_gap(
    *,
    op: AmendmentOp,
    muutos_tree: etree._Element,
    master: "ReplayState",
) -> str | None:
    """Infer chapter scope for flat section REPLACE payloads missing from base XML.

    Some historical Finnish base XML omits sections that later amendments
    replace. The source body carries the full section payload, but the
    johtolause names only ``N §`` without a chapter. When the live chapter
    sequence itself proves a single gap, compile the op to that chapter rather
    than letting apply materialize a root-level section.

    Boundary gaps are intentionally conservative: a singleton gap between two
    chapters can belong to either side (for example 48 / 49 / 50), so it stays
    unresolved unless the target is the first section before chapter 1's first
    known section or part of a multi-section source-body tail prefix.
    """
    if (
        op.op_type != OpType.REPLACE
        or op.target_cols.target_unit_kind != "section"
        or not op.target_cols.target_section
        or op.target_cols.target_chapter is not None
        or op.target_cols.target_paragraph is not None
        or op.target_cols.target_item
        or op.target_cols.target_special
    ):
        return None
    section_norm = _norm_num_token(op.target_cols.target_section)
    if _DIGITS_RE.fullmatch(section_norm) is None:
        return None
    if not _source_body_has_flat_whole_section(
        muutos_tree=muutos_tree,
        section_norm=section_norm,
        target_part=op.target_cols.target_part,
    ):
        return None
    if master.find_section_path(section_norm, None, op.target_cols.target_part) is not None:
        return None

    target_num = int(section_norm)
    source_nums = _flat_source_body_section_nums(
        muutos_tree=muutos_tree,
        target_part=op.target_cols.target_part,
    )
    chapters: list[tuple[str | None, str, tuple[int, ...]]] = []

    def _walk(node: IRNode, current_part: str | None) -> None:
        next_part = current_part
        if node.kind is IRNodeKind.PART:
            next_part = _norm_num_token(str(node.label or "")) or None
        if node.kind is IRNodeKind.CHAPTER and node.label:
            chapter = _norm_num_token(str(node.label))
            nums: list[int] = []
            for child in node.children:
                if child.kind is not IRNodeKind.SECTION or not child.label:
                    continue
                child_norm = _norm_num_token(str(child.label))
                if _DIGITS_RE.fullmatch(child_norm) is not None:
                    nums.append(int(child_norm))
            if nums and (op.target_cols.target_part is None or next_part == op.target_cols.target_part):
                chapters.append((next_part, chapter, tuple(sorted(set(nums)))))
        for child in node.children:
            _walk(child, next_part)

    _walk(master.ir, None)

    candidates: set[str] = set()
    for idx, (_part, chapter, nums) in enumerate(chapters):
        chapter_min = nums[0]
        chapter_max = nums[-1]
        previous_max = chapters[idx - 1][2][-1] if idx > 0 and chapters[idx - 1][0] == _part else None
        next_min = chapters[idx + 1][2][0] if idx + 1 < len(chapters) and chapters[idx + 1][0] == _part else None

        if chapter_min < target_num < chapter_max and target_num not in nums:
            candidates.add(chapter)
            continue

        if previous_max is None and target_num == chapter_min - 1:
            candidates.add(chapter)
            continue

        if target_num > chapter_max and next_min is not None and target_num < next_min:
            gap = tuple(range(chapter_max + 1, next_min))
            source_gap = tuple(n for n in gap if n in source_nums)
            expected_prefix = tuple(range(chapter_max + 1, chapter_max + 1 + len(source_gap)))
            if len(source_gap) >= 2 and source_gap == expected_prefix and target_num in source_gap:
                candidates.add(chapter)

    if len(candidates) != 1:
        return None
    return next(iter(candidates))


def _johto_says_repealed_section_replaced_by_new_section(johto: str, section_norm: str) -> bool:
    """Narrow witness for ``kumotun N §:n tilalle uusi N §`` reinsertion clauses."""
    normalized = _normalize_fi_parse_text(johto)
    if not normalized or "kumot" not in normalized or "tilalle" not in normalized or "uusi" not in normalized:
        return False
    return any(
        section_norm
        in _matching_reinstatement_lists(
            match.group("old"),
            match.group("new"),
        )
        # lawvm-regex: owning_parser reinstatement-list recognizer over the FI-owned normalized johto; not a cross-plane raw_text read
        for match in _REPEALED_SECTION_REPLACEMENT_LIST_RE.finditer(normalized)
    )


def _johto_says_statute_level_repealed_section_replaced_by_new_section(
    johto: str,
    section_norm: str,
) -> bool:
    normalized = _normalize_fi_parse_text(johto)
    if not normalized or "kumot" not in normalized or "tilalle" not in normalized:
        return False
    # lawvm-regex: owning_parser reinstatement-list recognizer over the FI-owned normalized johto; not a cross-plane raw_text read
    for match in _REPEALED_SECTION_REPLACEMENT_LIST_RE.finditer(normalized):
        if _reinstatement_match_has_local_chapter_insert_scope(normalized, match.start()):
            continue
        if section_norm in _matching_reinstatement_lists(match.group("old"), match.group("new")):
            return True
    return False


def _base_section_scope_for_unique_section(
    *,
    base_ir: IRNode,
    section_norm: str,
) -> tuple[str | None, str | None] | None:
    """Return the unique original (part, chapter) scope for a section label."""
    scopes: set[tuple[str | None, str | None]] = set()

    def _walk(node: IRNode, current_part: str | None, current_chapter: str | None) -> None:
        next_part = current_part
        next_chapter = current_chapter
        if node.kind is IRNodeKind.PART:
            next_part = _norm_num_token(str(node.label or "")) or None
        elif node.kind is IRNodeKind.CHAPTER:
            next_chapter = _norm_num_token(str(node.label or "")) or None
        elif node.kind is IRNodeKind.SECTION and _norm_num_token(str(node.label or "")) == section_norm:
            scopes.add((next_part, next_chapter))
        for child in node.children:
            _walk(child, next_part, next_chapter)

    _walk(base_ir, None, None)
    if len(scopes) != 1:
        return None
    return next(iter(scopes))


def _container_path_exists_in_master(
    *,
    master: "ReplayState",
    part: str | None,
    chapter: str | None,
) -> bool:
    if chapter is None:
        return part is None or master.find_part(part) is not None

    found = False

    def _walk(node: object, current_part: str | None) -> None:
        nonlocal found
        if found:
            return
        kind = getattr(node, "kind", None)
        label = getattr(node, "label", None)
        next_part = current_part
        if kind is IRNodeKind.PART:
            next_part = _norm_num_token(str(label or "")) or None
        elif kind is IRNodeKind.CHAPTER and _norm_num_token(str(label or "")) == chapter:
            if part is None or next_part == part:
                found = True
                return
        for child in getattr(node, "children", ()):
            _walk(child, next_part)

    _walk(master.ir, None)
    return found


def _infer_flat_reinstated_section_scope_from_base(
    *,
    op: AmendmentOp,
    muutos_tree: etree._Element,
    master: "ReplayState",
    base_ir: IRNode | None,
    johto: str,
    amendment_id: str,
    parent_id: str,
    source_model: "AmendmentSourceModel | None" = None,
) -> tuple[str | None, str] | None:
    """Infer scope for source-body insertion of a new section replacing a repealed one.

    Finnish amendment XML sometimes serializes ``lisätään ... kumotun 114 §:n
    tilalle uusi 114 §`` as a flat body-level section even though the repealed
    section had an owned original chapter address, and sometimes leaves a later
    reinstatement list under a stale chapter wrapper.  The source phrase supplies
    the rebirth semantics; the base tree supplies the prior address.  This rule
    only fires when that prior address is unique and its container still exists.
    """
    if not _is_whole_section_insert(op):
        return None
    section_norm = _norm_num_token(op.target_cols.target_section)
    scope_witness = projection_scope_confidence(
        scope_confidence=op.scope_confidence,
        scope_provenance_tags=op.scope_provenance_tags,
        resolved_chapter=op.target_cols.target_chapter,
    )
    unwitnessed_absent_statute_level_reinstatement = (
        op.target_cols.target_chapter is not None
        and scope_witness is None
        and master.find_section_path(section_norm, op.target_cols.target_chapter, op.target_cols.target_part) is None
        and _johto_says_statute_level_repealed_section_replaced_by_new_section(
            johto,
            section_norm,
        )
    )
    explicit_absent_statute_level_reinstatement = (
        op.target_cols.target_chapter is not None
        and scope_witness is not None
        and scope_witness.source is ScopeResolutionSource.EXPLICIT_CHUNK
        and master.find_section_path(section_norm, op.target_cols.target_chapter, op.target_cols.target_part) is None
        and _johto_says_statute_level_repealed_section_replaced_by_new_section(
            johto,
            section_norm,
        )
    )
    if op.target_cols.target_chapter is not None and (
        scope_witness is None
        or scope_witness.source not in {ScopeResolutionSource.CARRY_FORWARD, ScopeResolutionSource.EXPLICIT_CHUNK}
    ):
        if not unwitnessed_absent_statute_level_reinstatement:
            return None
    if op.target_cols.target_chapter is not None and master.find_section_path(
        section_norm,
        op.target_cols.target_chapter,
        op.target_cols.target_part,
    ) is not None:
        return None
    if not _source_body_carries_whole_section(
        muutos_tree=muutos_tree,
        section_norm=section_norm,
        target_part=op.target_cols.target_part,
        source_model=source_model,
    ):
        return None
    if not _johto_says_repealed_section_replaced_by_new_section(johto, section_norm):
        return None
    cited_scope = _cited_repealed_section_scope_for_replacement(
        johto=johto,
        section_norm=section_norm,
        amendment_id=amendment_id,
        parent_id=parent_id,
        master=master,
    )
    if cited_scope is not None:
        cited_part, cited_chapter = cited_scope
        if op.target_cols.target_part is not None and cited_part != op.target_cols.target_part:
            return None
        if cited_chapter is not None and _container_path_exists_in_master(
            master=master,
            part=cited_part,
            chapter=cited_chapter,
        ):
            source_chunk_target_absent = (
                op.target_cols.target_chapter is not None
                and scope_witness is not None
                and scope_witness.source is ScopeResolutionSource.EXPLICIT_CHUNK
                and master.find_section_path(section_norm, op.target_cols.target_chapter, op.target_cols.target_part)
                is None
            )
            cited_repeal_target_present = (
                master.find_section_path(section_norm, cited_chapter, cited_part) is not None
            )
            if (
                op.target_cols.target_chapter is None
                or cited_chapter == op.target_cols.target_chapter
                or (
                    scope_witness is not None
                    and scope_witness.source is ScopeResolutionSource.CARRY_FORWARD
                )
                or (source_chunk_target_absent and cited_repeal_target_present)
            ):
                return (cited_part, cited_chapter)
    if base_ir is None:
        return None
    base_scope = _base_section_scope_for_unique_section(base_ir=base_ir, section_norm=section_norm)
    if base_scope is None:
        return None
    base_part, base_chapter = base_scope
    if base_chapter is None:
        return None
    if base_chapter == op.target_cols.target_chapter and base_part == op.target_cols.target_part:
        return None
    if op.target_cols.target_part is not None and base_part != op.target_cols.target_part:
        return None
    if not _container_path_exists_in_master(master=master, part=base_part, chapter=base_chapter):
        return None
    if (
        unwitnessed_absent_statute_level_reinstatement
        or explicit_absent_statute_level_reinstatement
        or op.target_cols.target_chapter is None
        or (scope_witness is not None and scope_witness.source is ScopeResolutionSource.CARRY_FORWARD)
    ):
        return (base_part, base_chapter)
    if not _source_body_has_flat_whole_section(
        muutos_tree=muutos_tree,
        section_norm=section_norm,
        target_part=op.target_cols.target_part,
    ):
        return None
    return (base_part, base_chapter)


def _is_letter_suffix_section_family_continuation(previous_label: str | None, current_label: str) -> bool:
    if not previous_label:
        return False
    previous_match = _LETTER_SUFFIX_CONTINUATION_PREVIOUS_RE.fullmatch(_norm_num_token(previous_label))
    current_match = _LETTER_SUFFIX_CONTINUATION_CURRENT_RE.fullmatch(_norm_num_token(current_label))
    if previous_match is None or current_match is None:
        return False
    previous_stem, previous_suffix = previous_match.groups()
    current_stem, current_suffix = current_match.groups()
    if previous_stem != current_stem:
        return False
    if not previous_suffix:
        return current_suffix.lower() == "a"
    return ord(current_suffix.lower()) == ord(previous_suffix.lower()) + 1


def _infer_unique_live_section_chapter_scope(
    *,
    op: AmendmentOp,
    master: "ReplayState",
) -> str | None:
    """Bind chapter scope when johto cites only §N and live tree has one host."""
    if op.target_cols.target_unit_kind != "section" or not op.target_cols.target_section:
        return None
    has_child_target = (
        op.target_cols.target_paragraph is not None
        or bool(op.target_cols.target_item)
        or bool(op.target_cols.target_special)
    )
    section_label = _norm_num_token(op.target_cols.target_section)
    if op.op_type in {OpType.REPLACE, OpType.REPEAL}:
        if op.target_cols.target_chapter is not None or has_child_target:
            return None
    elif op.op_type == OpType.INSERT and has_child_target:
        if op.target_cols.target_chapter is not None and master.find_section_path(
            section_label,
            op.target_cols.target_chapter,
            op.target_cols.target_part,
        ) is not None:
            return None
    else:
        return None
    unique_chapter = _unique_section_chapter(
        master,
        section_label,
        part_label=op.target_cols.target_part,
    )
    if unique_chapter is not None and master.find_section_path(
        section_label,
        unique_chapter,
        op.target_cols.target_part,
    ) is not None:
        return unique_chapter
    if op.op_type == OpType.INSERT and has_child_target:
        return None
    return infer_letter_suffix_section_chapter_from_stem_host(
        master,
        section_label,
        part_label=op.target_cols.target_part,
    )


def _direct_heading_text(node: IRNode | None) -> str:
    if node is None:
        return ""
    for child in node.children:
        if child.kind is IRNodeKind.HEADING:
            return " ".join(irnode_to_text(child).split())
    return ""


def _infer_duplicate_section_scope_from_source_heading(
    *,
    op: AmendmentOp,
    master: "ReplayState",
    source_model: "AmendmentSourceModel | None",
) -> tuple[str | None, str] | None:
    """Bind an unscoped duplicate section label by unique source/live heading fit."""
    if source_model is None:
        return None
    if (
        op.target_cols.target_unit_kind != "section"
        or op.op_type not in {OpType.REPLACE, OpType.REPEAL}
        or not op.target_cols.target_section
        or op.target_cols.target_chapter is not None
        or op.target_cols.target_paragraph is not None
        or op.target_cols.target_item is not None
        or op.target_cols.target_special is not None
    ):
        return None

    section_norm = _norm_num_token(op.target_cols.target_section)
    source_payload = source_model.lookup_payload_ir(
        "section",
        section_norm,
        target_part=op.target_cols.target_part,
    )
    source_heading = _direct_heading_text(source_payload.payload_ir).casefold()
    if not source_heading:
        return None

    candidates: list[tuple[float, str | None, str]] = []
    for path in section_paths_for_label(
        master.provision_index,
        section_norm,
        target_part=op.target_cols.target_part,
    ):
        node = master.resolve(path)
        if node is None:
            continue
        live_heading = _direct_heading_text(node).casefold()
        if not live_heading:
            continue
        chapter = next((label for kind, label in path if kind == "chapter"), None)
        if chapter is None:
            continue
        part = next((label for kind, label in path if kind == "part"), None)
        candidates.append(
            (SequenceMatcher(None, source_heading, live_heading).ratio(), part, chapter)
        )

    if len(candidates) < 2:
        return None
    ranked = sorted(candidates, key=lambda row: row[0], reverse=True)
    best_score, best_part, best_chapter = ranked[0]
    second_score = ranked[1][0]
    if best_score < 0.60 or best_score - second_score < 0.15:
        return None
    return (best_part, best_chapter)


def _renumbers_same_section_label_away(
    op: AmendmentOp,
    ops: List[AmendmentOp],
) -> bool:
    """Return True when this amendment explicitly vacates ``op``'s label."""
    section_label = _norm_num_token(op.target_cols.target_section or "")
    if not section_label:
        return False
    for candidate in ops:
        if (
            candidate is op
            or candidate.op_type != "RENUMBER"
            or candidate.target_cols.target_unit_kind != "section"
            or _norm_num_token(candidate.target_cols.target_section or "") != section_label
            or candidate.lo is None
            or candidate.lo.destination is None
        ):
            continue
        destination_label = candidate.lo.destination.leaf_label()
        if _norm_num_token(destination_label) != section_label:
            return True
    return False


def _infer_recodification_vacated_insert_scope(
    *,
    op: AmendmentOp,
    ops: List[AmendmentOp],
    master: "ReplayState",
    source_model: "AmendmentSourceModel | None",
) -> tuple[str | None, str | None] | None:
    """Bind a same-wave new section insert to the label vacated by a relabel.

    Historical source XML can keep later provisions inside one overbroad
    chapter wrapper.  If the same amendment also renumbers the exact live
    section label away and then inserts a new whole section with that label, the
    live pre-wave address of the vacated section is the owned target scope for
    the insert.
    """
    if source_model is None or not _is_whole_section_insert(op):
        return None
    section_label = _norm_num_token(op.target_cols.target_section or "")
    if not _renumbers_same_section_label_away(op, ops):
        return None
    body_scope = source_model.body_section_scope(section_label)
    if body_scope is None:
        return None
    _body_part, body_chapter = body_scope
    if not body_chapter:
        return None
    if not source_model.body_chapter_is_single_mixed_wrapper(body_chapter, master):
        return None
    live_path = master.find_section_path(section_label, None, op.target_cols.target_part)
    if live_path is None:
        return None
    live_part = next((label for kind, label in live_path if kind == "part"), None)
    live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
    if not live_chapter or _norm_num_token(live_chapter) == _norm_num_token(body_chapter):
        return None
    return live_part, live_chapter


def _infer_letter_suffix_insert_chapter_from_stem_host(
    *,
    op: AmendmentOp,
    muutos_tree: etree._Element,
    master: "ReplayState",
    source_model: "AmendmentSourceModel | None" = None,
) -> str | None:
    """Bind a new letter-suffix section insert to its live stem section host.

    Finnish source XML can over-wrap trailing new sections in a newly inserted
    chapter container while the johtolause also lists those sections as separate
    ``uusi N a §`` inserts. If a previous chapter scope was carried forward
    from an unrelated group, or mis-tagged as explicit while the amendment body
    places the section under a different not-yet-live container, the live stem
    section (``16`` for ``16a``) is stronger ownership evidence than the stale
    chapter.
    """
    if not _is_whole_section_insert(op):
        return None
    section_label = _norm_num_token(op.target_cols.target_section)
    if _LETTER_SUFFIX_SECTION_RE.fullmatch(section_label) is None:
        return None
    scope_witness = projection_scope_confidence(
        scope_confidence=op.scope_confidence,
        scope_provenance_tags=op.scope_provenance_tags,
        resolved_chapter=op.target_cols.target_chapter,
    )
    body_scope = _body_scope_for_section_label(
        muutos_tree=muutos_tree,
        section_label=section_label,
        source_model=source_model,
    )
    if op.target_cols.target_chapter is not None:
        if scope_witness is None:
            return None
        if scope_witness.source is ScopeResolutionSource.CARRY_FORWARD:
            pass
        elif scope_witness.source is ScopeResolutionSource.EXPLICIT_CHUNK:
            if body_scope is None:
                return None
            body_part, body_chapter = body_scope
            if body_part != op.target_cols.target_part:
                return None
        else:
            return None
    if not _source_body_carries_whole_section(
        muutos_tree=muutos_tree,
        section_norm=section_label,
        target_part=op.target_cols.target_part,
        source_model=source_model,
    ):
        return None
    if body_scope is not None:
        body_part, body_chapter = body_scope
        if body_part == op.target_cols.target_part and _container_path_exists_in_master(
            master=master,
            part=body_part,
            chapter=body_chapter,
        ):
            return None
    if op.target_cols.target_chapter is not None and master.find_section_path(
        section_label,
        op.target_cols.target_chapter,
        op.target_cols.target_part,
    ) is not None:
        return None
    inferred_chapter = infer_letter_suffix_section_chapter_from_stem_host(
        master,
        section_label,
        part_label=op.target_cols.target_part,
    )
    if inferred_chapter is None or inferred_chapter == op.target_cols.target_chapter:
        return None
    return inferred_chapter


def _infer_corroborated_body_scope_for_live_stem_insert(
    *,
    op: AmendmentOp,
    ops: List[AmendmentOp],
    master: "ReplayState",
    source_model: "AmendmentSourceModel | None",
) -> tuple[str | None, str] | None:
    """Prefer a witnessed source-body chapter over a live-stem guess.

    A bare ``130 a §`` insert can be initially scoped to the live host of
    ``130 §``.  When the amendment body itself places the new section under an
    existing chapter and the same amendment also edits existing sections in
    that chapter, the source-body chapter is stronger evidence than the stem.
    """
    if source_model is None or not _is_whole_section_insert(op):
        return None
    scope_witness = projection_scope_confidence(
        scope_confidence=op.scope_confidence,
        scope_provenance_tags=op.scope_provenance_tags,
        resolved_chapter=op.target_cols.target_chapter,
    )
    if scope_witness is None or scope_witness.source is not ScopeResolutionSource.LIVE_STEM_HOST:
        return None
    section_label = _norm_num_token(op.target_cols.target_section or "")
    body_scope = source_model.body_section_scope(section_label)
    if body_scope is None:
        return None
    body_part, body_chapter = body_scope
    if not body_chapter or (body_part == op.target_cols.target_part and body_chapter == op.target_cols.target_chapter):
        return None
    if not source_model.body_has_section(
        section_label,
        target_chapter=body_chapter,
        target_part=body_part,
    ):
        return None
    if not _container_path_exists_in_master(
        master=master,
        part=body_part,
        chapter=body_chapter,
    ):
        return None

    def _section_stem(label: str) -> int | None:
        match = _SECTION_LABEL_ORDER_RE.fullmatch(_norm_num_token(label))
        if match is None:
            return None
        return int(match.group(1))

    def _payload_text_mentions_section(text: str, label: str) -> bool:
        if "§" not in text and "pykäl" not in text.lower():
            return False
        from lawvm.finland.references.freetext_addresses import scan_legal_addresses

        label_norm = _norm_num_token(label)
        return any(_norm_num_token(address.section) == label_norm for address in scan_legal_addresses(text))

    target_stem = _section_stem(section_label)
    if target_stem is None:
        return None

    corroborating_labels: set[str] = set()
    for other in ops:
        if other is op or other.target_cols.target_unit_kind != "section" or not other.target_cols.target_section:
            continue
        if other.op_type == OpType.INSERT and _is_whole_section_insert(other):
            continue
        other_chapter = _norm_num_token(other.target_cols.target_chapter or "")
        if other_chapter != _norm_num_token(body_chapter):
            continue
        other_part = _norm_num_token(other.target_cols.target_part or "") if other.target_cols.target_part else None
        body_part_norm = _norm_num_token(body_part or "") if body_part else None
        if other_part != body_part_norm:
            continue
        other_label = _norm_num_token(other.target_cols.target_section)
        if other_label == section_label:
            continue
        if source_model.body_has_section(
            other_label,
            target_chapter=body_chapter,
            target_part=body_part,
        ):
            other_stem = _section_stem(other_label)
            if other_stem is not None and abs(other_stem - target_stem) <= 1:
                corroborating_labels.add(other_label)

    if not corroborating_labels:
        return None

    has_internal_reference_witness = False
    for other in ops:
        if other is op or other.target_cols.target_unit_kind != "section" or not other.target_cols.target_section:
            continue
        other_label = _norm_num_token(other.target_cols.target_section)
        if other_label == section_label:
            continue
        if not source_model.body_has_section(
            other_label,
            target_chapter=body_chapter,
            target_part=body_part,
        ):
            continue
        payload_text = source_model.lookup_section_payload_text(
            other_label,
            target_chapter=body_chapter,
            target_part=body_part,
        ).text
        if payload_text and _payload_text_mentions_section(payload_text, section_label):
            has_internal_reference_witness = True
            break
    if not has_internal_reference_witness:
        return None

    source_labels = [
        _norm_num_token(label)
        for label in source_model.body_real_chapter_section_labels(body_chapter)
    ]
    try:
        idx = source_labels.index(section_label)
    except ValueError:
        return None
    adjacent = {
        source_labels[pos]
        for pos in (idx - 1, idx + 1)
        if 0 <= pos < len(source_labels)
    }
    if adjacent & corroborating_labels:
        return body_part, body_chapter
    return None


def _add_inferred_section_chapter_scope(
    op: AmendmentOp,
    *,
    part: str | None = None,
    chapter: str,
    rule_id: str,
) -> AmendmentOp:
    tags = tuple(dict.fromkeys((*op.scope_provenance_tags, "chapter_scope_carry_forward")))
    scoped_lo = _lo_with_path_update(op.lo, part=part, chapter=chapter) if op.lo is not None else op.lo
    if scoped_lo is not None:
        scoped_lo = dc_replace(
            scoped_lo,
            provenance_tags=tuple(dict.fromkeys((*scoped_lo.provenance_tags, "chapter_scope_carry_forward"))),
            witness_rule_id=rule_id,
        )
    return dc_replace(
        op,
        **replace_target(
            op,
            target_part=part if part is not None else op.target_cols.target_part,
            target_chapter=chapter,
        ),
        scope_provenance_tags=tags,
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter=chapter,
        ),
        lo=scoped_lo,
        witness_rule_id=rule_id,
    )


def _retarget_letter_suffix_inserts_from_same_amendment_stem_scope(
    ops: list[AmendmentOp],
    *,
    source_model: "AmendmentSourceModel | None",
    master: "ReplayState | None" = None,
) -> list[AmendmentOp]:
    if source_model is None:
        return ops

    stem_scopes: dict[str, tuple[str | None, str, ScopeConfidence]] = {}
    conflicted_stems: set[str] = set()
    for op in ops:
        if op.target_cols.target_unit_kind != "section" or not op.target_cols.target_section or not op.target_cols.target_chapter:
            continue
        section_label = _norm_num_token(op.target_cols.target_section)
        if not section_label.isdigit():
            continue
        witness = projection_scope_confidence(
            scope_confidence=op.scope_confidence,
            scope_provenance_tags=op.scope_provenance_tags,
            resolved_chapter=op.target_cols.target_chapter,
        )
        if witness is None or witness.source in {
            ScopeResolutionSource.LIVE_STEM_HOST,
            ScopeResolutionSource.CARRY_FORWARD,
        }:
            continue
        candidate = (op.target_cols.target_part, op.target_cols.target_chapter, witness)
        existing = stem_scopes.get(section_label)
        if existing is not None and existing[:2] != candidate[:2]:
            conflicted_stems.add(section_label)
            stem_scopes.pop(section_label, None)
            continue
        if section_label not in conflicted_stems:
            stem_scopes[section_label] = candidate

    if not stem_scopes:
        return ops

    retargeted: list[AmendmentOp] = []
    for op in ops:
        section_label = _norm_num_token(op.target_cols.target_section or "")
        match = _LETTER_SUFFIX_SECTION_RE.fullmatch(section_label)
        if (
            match is None
            or not _is_whole_section_insert(op)
            or not source_model.body_has_section(match.group("stem"))
            or not source_model.body_has_section(section_label)
        ):
            retargeted.append(op)
            continue
        stem_scope = stem_scopes.get(match.group("stem"))
        if stem_scope is None:
            retargeted.append(op)
            continue
        stem_part, stem_chapter, _stem_witness = stem_scope
        if master is not None:
            find_section_path = getattr(master, "find_section_path", None)
            stem_live_path = (
                find_section_path(match.group("stem"), None, stem_part)
                if callable(find_section_path)
                else None
            )
            if stem_live_path is None and stem_part is not None and callable(find_section_path):
                stem_live_path = find_section_path(match.group("stem"), None, None)
            stem_live_is_unchaptered = stem_live_path is not None and not any(
                kind == "chapter" for kind, _label in stem_live_path
            )
            if stem_live_is_unchaptered and stem_chapter is not None:
                retargeted.append(op)
                continue
        current_witness = projection_scope_confidence(
            scope_confidence=op.scope_confidence,
            scope_provenance_tags=op.scope_provenance_tags,
            resolved_chapter=op.target_cols.target_chapter,
        )
        if current_witness is None and op.target_cols.target_chapter is not None:
            retargeted.append(op)
            continue
        if current_witness is not None and current_witness.source not in {
            ScopeResolutionSource.LIVE_STEM_HOST,
            ScopeResolutionSource.CARRY_FORWARD,
        }:
            retargeted.append(op)
            continue
        if (
            op.target_cols.target_part == stem_part
            and op.target_cols.target_chapter == stem_chapter
            and (
                current_witness is None
                or current_witness.source is not ScopeResolutionSource.LIVE_STEM_HOST
            )
        ):
            retargeted.append(op)
            continue
        scoped_lo = _lo_with_path_update(op.lo, part=stem_part, chapter=stem_chapter) if op.lo is not None else op.lo
        if scoped_lo is not None:
            scoped_lo = dc_replace(
                scoped_lo,
                provenance_tags=tuple(
                    dict.fromkeys(
                        (
                            *scoped_lo.provenance_tags,
                            "chapter_scope_from_same_amendment_stem",
                        )
                    )
                ),
                witness_rule_id="fi_same_amendment_stem_scope_for_letter_suffix_insert",
            )
        tags = tuple(
            dict.fromkeys(
                (
                    *op.scope_provenance_tags,
                    "chapter_scope_from_same_amendment_stem",
                )
            )
        )
        retargeted.append(
            dc_replace(
                op,
                **replace_target(
                    op,
                    target_part=stem_part,
                    target_chapter=stem_chapter,
                ),
                scope_provenance_tags=tags,
                scope_confidence=ScopeConfidence(
                    tag="chapter_scope_from_same_amendment_stem",
                    source=ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
                    confidence=ScopeResolutionConfidence.INFERRED,
                    resolved_chapter=stem_chapter,
                ),
                lo=scoped_lo,
            )
        )
    return retargeted


def _body_scope_for_section_label(
    *,
    muutos_tree: "etree._Element",
    section_label: str,
    source_model: "AmendmentSourceModel | None" = None,
) -> tuple[str | None, str | None] | None:
    """Return the unique body-backed (part, chapter) scope for one section label."""
    if source_model is not None:
        return source_model.body_section_wrapper_scope(section_label)

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
                return _normalize_source_part_num(part_num.text) or None
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
        sec_norm = _normalize_source_section_num(num_el.text)
        if sec_norm != target_norm:
            continue
        scopes.add((_part_label_for_element(sec), _chapter_label_for_element(sec)))
    if len(scopes) != 1:
        return None
    return next(iter(scopes))


def _unborn_source_chapter_labels(
    *,
    muutos_tree: etree._Element,
    master: "ReplayState",
) -> set[str]:
    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return set()
    labels: set[str] = set()
    for chapter in body.findall(".//{*}chapter"):
        num_el = chapter.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        chapter_label = _norm_num_token(num_el.text).removesuffix("luku")
        if chapter_label and master.find_chapter(chapter_label) is None:
            labels.add(chapter_label)
    return labels


def _source_chapter_direct_section_count(
    *,
    muutos_tree: etree._Element,
    chapter_label: str,
) -> int:
    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return 0
    for chapter in body.findall(".//{*}chapter"):
        num_el = chapter.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        label = _norm_num_token(num_el.text).removesuffix("luku")
        if label == chapter_label:
            return sum(1 for child in chapter if _direct_child_localname(child) == "section")
    return 0


def _source_body_direct_chapter_count(muutos_tree: etree._Element) -> int:
    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return 0
    return sum(1 for chapter in body.findall(".//{*}chapter") if chapter.find("{*}num") is not None)


def _source_declares_chapter_heading_wave(
    *,
    muutos_tree: etree._Element,
    johto: str,
) -> bool:
    if "luvun otsikko" not in johto or "edelle uusi" not in johto:
        return False
    return _source_body_direct_chapter_count(muutos_tree) >= 2


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
    sibling_ops: Sequence[AmendmentOp] = (),
    muutos_tree: "etree._Element",
    master: "ReplayState",
    johto: str = "",
    source_model: "AmendmentSourceModel | None" = None,
) -> AmendmentOp | None:
    """Clear chapter scope when the live statute proves a bare section target.

    This guards against parent-title leakage like ``rikoslain 1 luvun 7 §`` being
    misread as chapter scope for the amended statute itself, and stale source
    body wrappers that place an already-live root section under an unrelated
    chapter. We only clear the chapter when the amendment body or live target
    path proves a bare top-level section.
    """
    if op.target_cols.target_unit_kind != "section" or not op.target_cols.target_section or not op.target_cols.target_chapter:
        return None
    body_scope = _body_scope_for_section_label(
        muutos_tree=muutos_tree,
        section_label=op.target_cols.target_section,
        source_model=source_model,
    )
    section_norm = _norm_num_token(op.target_cols.target_section)
    has_descendant_target = (
        op.target_cols.target_paragraph is not None
        or op.target_cols.target_item is not None
        or op.target_cols.target_special is not None
    )
    scoped_path = master.find_section_path(section_norm, op.target_cols.target_chapter, op.target_cols.target_part)
    live_path = master.find_section_path(section_norm, None, op.target_cols.target_part)
    if live_path is None and op.target_cols.target_part is not None:
        live_path = master.find_section_path(section_norm, None, None)
    live_chapter = (
        next((label for kind, label in live_path if kind == "chapter"), None)
        if live_path is not None
        else None
    )
    live_path_proves_unchaptered = (
        live_path is not None
        and live_chapter is None
        and scoped_path is None
        and section_norm not in master.duplicate_section_labels
        and not has_descendant_target
        and op.op_type in {OpType.REPLACE, OpType.REPEAL}
        and not any(
            sibling.target_cols.target_unit_kind == "chapter"
            and _norm_num_token(sibling.target_cols.target_section or "") == _norm_num_token(op.target_cols.target_chapter)
            for sibling in sibling_ops
        )
    )
    body_proves_unchaptered = not _master_has_any_chapter(master) and body_scope == (None, None)
    if not body_proves_unchaptered and not (
        live_path_proves_unchaptered
        and not _johtolause_explicitly_mentions_chaptered_section_target(
            johto,
            op.target_cols.target_chapter,
            op.target_cols.target_section,
        )
    ):
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
        **replace_target(op, target_chapter=None),
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
    source_model: "AmendmentSourceModel | None" = None,
) -> tuple[str | None, str | None] | None:
    """Retarget stale body-derived scope to the unique live section path.

    Some amendment XML wraps section payloads in an outdated chapter container
    even though the live statute has already rehomed those sections elsewhere.
    Only retarget when:
    - the op already carries chapter scope,
    - the scope came from a bounded explicit source, rewrite lane, or body-scope
      carry-forward lane,
    - the scoped live section does not exist, and
    - the amendment body uniquely places the section under a different part /
      chapter family that resolves to one live section path.
    """
    scope_witness = projection_scope_confidence(
        scope_confidence=op.scope_confidence,
        scope_provenance_tags=op.scope_provenance_tags,
        resolved_chapter=op.target_cols.target_chapter,
    )
    if (
        op.target_cols.target_unit_kind != "section"
        or not op.target_cols.target_section
        or not op.target_cols.target_chapter
        or (
            scope_witness is not None
            and scope_witness.source
            not in {
                ScopeResolutionSource.CARRY_FORWARD,
                ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
                ScopeResolutionSource.EXPLICIT_CHUNK,
            }
        )
    ):
        return None

    section_label = _norm_num_token(op.target_cols.target_section)
    scoped_path = master.find_section_path(section_label, op.target_cols.target_chapter, op.target_cols.target_part)
    if scoped_path is not None:
        return None
    if op.target_cols.target_chapter and section_label in _same_label_move_sections_for_chapter(johto, op.target_cols.target_chapter):
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
        source_model=source_model,
    )
    if body_scope is None:
        return None

    body_part, body_chapter = body_scope
    if (
        scope_witness is not None
        and scope_witness.source is ScopeResolutionSource.EXPLICIT_CHUNK
        and op.op_type == OpType.INSERT
        and op.target_cols.target_paragraph is None
        and not op.target_cols.target_item
        and not op.target_cols.target_special
    ):
        # Explicit chunk scope is source-owned. Do not rehome a whole-section
        # insert merely because the body wrapper resembles an existing live
        # section's chapter.
        return None
    if (
        op.op_type == OpType.INSERT
        and op.target_cols.target_paragraph is None
        and not op.target_cols.target_item
        and not op.target_cols.target_special
        and body_chapter == op.target_cols.target_chapter
        and body_part == op.target_cols.target_part
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
    if not live_chapter or (live_chapter == op.target_cols.target_chapter and live_part == op.target_cols.target_part):
        return None
    return live_part, live_chapter


# ---------------------------------------------------------------------------
# _enrich_ops_from_amendment_tree
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _AmendmentTreeMetadata:
    source_issue_date: date | None
    source_title: str
    effective_date: date | None
    expiry_date: date | None
    provision_expiry_overrides: tuple[TemporaryProvisionExpiryOverride, ...]
    section_expiry_overrides: tuple[tuple[str, set[str], date], ...]
    # Byte-level anchor of the source clause in the raw amendment bytes, when a
    # verbatim contiguous span exists; None (fail-loud) otherwise. Stamped by
    # the acquisition stage, which owns the raw bytes + chosen operative text.
    source_anchor: "SourceAnchor | None" = None


def _amendment_tree_metadata(
    *,
    amendment_id: str,
    muutos_tree: "etree._Element",
) -> _AmendmentTreeMetadata:
    raw_text = etree.tostring(muutos_tree, method="text", encoding="unicode")
    return _AmendmentTreeMetadata(
        source_issue_date=_statute_issue_date(muutos_tree),
        source_title=_tree_title(muutos_tree),
        effective_date=_amendment_effective_date(muutos_tree),
        expiry_date=_amendment_expiry_date(muutos_tree, raw_text=raw_text),
        provision_expiry_overrides=_temporary_provision_expiry_overrides(
            muutos_tree,
            amendment_id,
            raw_text=raw_text,
        ),
        section_expiry_overrides=_temporary_section_expiry_overrides(
            muutos_tree,
            amendment_id,
            raw_text=raw_text,
        ),
    )


def _enrich_ops_from_amendment_tree(
    ops: List[AmendmentOp],
    amendment_id: str,
    muutos_tree: "etree._Element",
    master: "ReplayState | None" = None,
    johto: str = "",
    base_ir: IRNode | None = None,
    parent_id: str = "",
    metadata: _AmendmentTreeMetadata | None = None,
    source_model: "AmendmentSourceModel | None" = None,
) -> List[AmendmentOp]:
    """Stamp source-statute metadata (date, title, expiry) onto every op.

    Pure ``(ops, amendment_id, tree) -> ops`` transform.  The lxml tree is read-only.
    """
    metadata = metadata or _amendment_tree_metadata(
        amendment_id=amendment_id,
        muutos_tree=muutos_tree,
    )
    source_issue_date = metadata.source_issue_date
    source_title = metadata.source_title
    eff_date = metadata.effective_date
    expiry_date = metadata.expiry_date
    provision_expiry_overrides = metadata.provision_expiry_overrides
    section_expiry_overrides = metadata.section_expiry_overrides
    # Only stamp the expiry on op_source when the amendment has WHOLE-ACT expiry
    # ("Tämä laki on voimassa N päivään ...").  When the expiry is section-scoped
    # ("Lain 43 a—43 c § ovat voimassa ..."), op_source.expires must remain empty
    # so that permanently-modified sections (e.g. 16 §, 18 §, 20 §, 21 § in
    # 2012/991) do not get an erroneous expires stamp.  The section-scoped expiry
    # is applied per-section via the section_expiry_override block below.
    _section_scoped_expiry = any(
        target_mid == amendment_id for target_mid, _labels, _expiry in section_expiry_overrides
    ) or any(target.target_mid == amendment_id for target in provision_expiry_overrides)
    op_source = OperationSource(
        statute_id=amendment_id,
        title=source_title,
        enacted=source_issue_date.isoformat() if source_issue_date else "",
        effective=eff_date.isoformat() if eff_date else "",
        raw_text=johto.strip(),
        source_anchor=metadata.source_anchor,
        # _amendment_expiry_date returns the prose-inclusive last in-force day;
        # the kernel `expires` field is an exclusive cutoff, so convert here.
        expires=(
            ""
            if _section_scoped_expiry
            else (expires_on_from_valid_until(expiry_date).isoformat() if expiry_date else "")
        ),
    )
    enriched = []
    last_inferred_section_norm: str | None = None
    last_inferred_section_chapter: str | None = None
    last_inferred_section_part: str | None = None
    heading_scope_source_model = source_model
    for op in ops:
        scoped_op = op
        body_scoped = False
        if master is not None:
            stripped_op = _strip_impossible_chapter_scope_for_bare_body_section_op(
                op=scoped_op,
                sibling_ops=ops,
                muutos_tree=muutos_tree,
                master=master,
                johto=johto,
                source_model=source_model,
            )
            if stripped_op is not None:
                scoped_op = stripped_op
            scope_witness = projection_scope_confidence(
                scope_confidence=scoped_op.scope_confidence,
                scope_provenance_tags=scoped_op.scope_provenance_tags,
                resolved_chapter=scoped_op.target_cols.target_chapter,
            )
            if (
                scoped_op.op_type == OpType.INSERT
                and scoped_op.target_cols.target_unit_kind == "section"
                and scoped_op.target_cols.target_chapter is not None
                and (
                    scoped_op.target_cols.target_paragraph is not None
                    or scoped_op.target_cols.target_item is not None
                    or scoped_op.target_cols.target_special is not None
                )
                and scope_witness is not None
                and scope_witness.source is ScopeResolutionSource.CARRY_FORWARD
            ):
                carry_forward_host = master.find_section_path(
                    _norm_num_token(scoped_op.target_cols.target_section or ""),
                    scoped_op.target_cols.target_chapter,
                    scoped_op.target_cols.target_part,
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
                        **replace_target(scoped_op, target_chapter=None),
                        scope_confidence=normalize_scope_confidence(None, resolved_chapter=None),
                        scope_provenance_tags=retained_scope_tags,
                        lo=retained_lo,
                    )
                    scope_witness = projection_scope_confidence(
                        scope_confidence=scoped_op.scope_confidence,
                        scope_provenance_tags=scoped_op.scope_provenance_tags,
                        resolved_chapter=scoped_op.target_cols.target_chapter,
                    )
            inferred_part = None
            inferred_chapter = None
            inferred_rule_id = "fi_body_chapter_scope_from_source_body"
            if scoped_op.target_cols.target_chapter is None or (
                scoped_op.op_type == OpType.INSERT
                and scoped_op.target_cols.target_unit_kind == "section"
                and scoped_op.target_cols.target_paragraph is None
                and scoped_op.target_cols.target_item is None
                and scoped_op.target_cols.target_special is None
                and scope_witness is not None
                and scope_witness.source
                in {
                    ScopeResolutionSource.CARRY_FORWARD,
                    ScopeResolutionSource.EXPLICIT_CHUNK,
                    ScopeResolutionSource.LIVE_STEM_HOST,
                }
            ):
                reinstated_scope = _infer_flat_reinstated_section_scope_from_base(
                    op=scoped_op,
                    muutos_tree=muutos_tree,
                    master=master,
                    base_ir=base_ir,
                    johto=johto,
                    amendment_id=amendment_id,
                    parent_id=parent_id,
                    source_model=source_model,
                )
                if reinstated_scope is not None:
                    inferred_part, inferred_chapter = reinstated_scope
                    inferred_rule_id = "fi_reinstated_section_scope_from_prior_repeal_address"
                if inferred_chapter is None:
                    corroborated_body_scope = _infer_corroborated_body_scope_for_live_stem_insert(
                        op=scoped_op,
                        ops=ops,
                        master=master,
                        source_model=source_model,
                    )
                    if corroborated_body_scope is not None:
                        inferred_part, inferred_chapter = corroborated_body_scope
                        inferred_rule_id = "fi_live_stem_scope_overridden_by_corroborated_source_body"
                if inferred_chapter is None:
                    inferred_chapter = _infer_letter_suffix_insert_chapter_from_stem_host(
                        op=scoped_op,
                        muutos_tree=muutos_tree,
                        master=master,
                        source_model=source_model,
                    )
                    inferred_rule_id = "fi_letter_suffix_insert_scope_from_stem_host"
                if inferred_chapter is None and not _is_whole_section_insert(scoped_op):
                    inferred_chapter = _infer_unique_live_section_chapter_scope(
                        op=scoped_op,
                        master=master,
                    )
                    if inferred_chapter is not None:
                        inferred_rule_id = (
                            "fi_unique_live_section_chapter_scope"
                            if master.find_section_path(
                                _norm_num_token(scoped_op.target_cols.target_section or ""),
                                inferred_chapter,
                                scoped_op.target_cols.target_part,
                            )
                            is not None
                            else "fi_letter_suffix_stem_host_chapter_scope"
                        )
                if inferred_chapter is None:
                    if heading_scope_source_model is None:
                        from lawvm.finland.source_model import AmendmentSourceModel

                        heading_scope_source_model = AmendmentSourceModel.from_tree(muutos_tree)
                    heading_scope = _infer_duplicate_section_scope_from_source_heading(
                        op=scoped_op,
                        master=master,
                        source_model=heading_scope_source_model,
                    )
                    if heading_scope is not None:
                        inferred_part, inferred_chapter = heading_scope
                        inferred_rule_id = "fi_duplicate_section_scope_from_source_heading"
                if inferred_chapter is None:
                    recodification_vacated_scope = _infer_recodification_vacated_insert_scope(
                        op=scoped_op,
                        ops=ops,
                        master=master,
                        source_model=source_model,
                    )
                    if recodification_vacated_scope is not None:
                        inferred_part, inferred_chapter = recodification_vacated_scope
                        inferred_rule_id = "fi_recodification_vacated_insert_scope"
                    else:
                        inferred_chapter = _body_chapter_scope_for_section_op(
                            op=scoped_op,
                            muutos_tree=muutos_tree,
                            master=master,
                            johto=johto,
                            source_model=source_model,
                        )
                        inferred_rule_id = "fi_body_chapter_scope_from_source_body"
                if inferred_chapter is None:
                    inferred_chapter = _infer_flat_body_insert_chapter_from_bracketing_live_siblings(
                        op=scoped_op,
                        muutos_tree=muutos_tree,
                        master=master,
                    )
                    inferred_rule_id = "fi_flat_body_insert_scope_from_bracketing_live_siblings"
                if inferred_chapter is None:
                    inferred_chapter = _infer_flat_body_replace_chapter_from_live_section_gap(
                        op=scoped_op,
                        muutos_tree=muutos_tree,
                        master=master,
                    )
                    inferred_rule_id = "fi_flat_body_replace_scope_from_live_section_gap"
                if inferred_chapter is None:
                    sibling_scope = _infer_flat_body_replace_scope_from_bracketing_live_siblings(
                        op=scoped_op,
                        muutos_tree=muutos_tree,
                        master=master,
                    )
                    if sibling_scope is not None:
                        inferred_part, inferred_chapter = sibling_scope
                        inferred_rule_id = "fi_flat_body_replace_scope_from_bracketing_live_siblings"
                if (
                    inferred_chapter is None
                    and _is_whole_section_insert(scoped_op)
                    and scoped_op.target_cols.target_chapter is None
                    and scoped_op.target_cols.target_part == last_inferred_section_part
                    and last_inferred_section_chapter is not None
                    and _source_body_has_flat_whole_section(
                        muutos_tree=muutos_tree,
                        section_norm=_norm_num_token(scoped_op.target_cols.target_section),
                        target_part=scoped_op.target_cols.target_part,
                    )
                    and _is_letter_suffix_section_family_continuation(
                        last_inferred_section_norm,
                        _norm_num_token(scoped_op.target_cols.target_section),
                    )
                ):
                    inferred_chapter = last_inferred_section_chapter
                    inferred_rule_id = "fi_flat_body_insert_scope_from_base_family_continuation"
            if inferred_chapter is not None:
                body_scoped = True
                scoped_op = _add_inferred_section_chapter_scope(
                    scoped_op,
                    part=inferred_part,
                    chapter=inferred_chapter,
                    rule_id=inferred_rule_id,
                )
            elif scope_witness is not None and scope_witness.source in {
                ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
                ScopeResolutionSource.EXPLICIT_CHUNK,
            }:
                body_scoped = True
            if body_scoped and inferred_rule_id != "fi_reinstated_section_scope_from_prior_repeal_address":
                retargeted_scope = _retarget_stale_body_scope_for_section_op(
                    op=scoped_op,
                    muutos_tree=muutos_tree,
                    master=master,
                    johto=johto,
                    source_model=source_model,
                )
                if retargeted_scope is not None:
                    retargeted_part, retargeted_chapter = retargeted_scope
                    stale_body_part = scoped_op.target_cols.target_part
                    stale_body_chapter = scoped_op.target_cols.target_chapter
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
                        **replace_target(
                            scoped_op,
                            target_part=retargeted_part,
                            target_chapter=retargeted_chapter,
                        ),
                        scope_confidence=(
                            ScopeConfidence(
                                tag="body_container_membership_rewrite",
                                source=ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
                                confidence=ScopeResolutionConfidence.REWRITTEN,
                                resolved_chapter=retargeted_chapter,
                            )
                            if scope_witness is not None
                            and scope_witness.source is ScopeResolutionSource.EXPLICIT_CHUNK
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
        # Identity-renumber-of-absent-section -> whole-section INSERT.
        # A move verb group (``... siirretään ... §, sekä N-M § seuraavasti``)
        # can over-extend across the ``sekä`` coordination and emit identity
        # RENUMBER ops (dest leaf == target leaf) for the trailing new sections.
        # When the section is absent from the live parent it cannot be a rename
        # of an existing node; with the source body carrying the whole section
        # (body_scoped from ``fi_body_chapter_scope_from_source_body``) it is a
        # new INSERT. Replaying it as a same-label relabel otherwise raises
        # ``RELABEL target not found`` and silently drops the section body.
        if (
            master is not None
            and _is_identity_whole_section_renumber(scoped_op)
            and master.find_section_path(
                _norm_num_token(scoped_op.target_cols.target_section),
                scoped_op.target_cols.target_chapter,
                scoped_op.target_cols.target_part,
            )
            is None
            and _source_body_carries_whole_section(
                muutos_tree=muutos_tree,
                section_norm=_norm_num_token(scoped_op.target_cols.target_section),
                target_part=scoped_op.target_cols.target_part,
                source_model=source_model,
            )
        ):
            declared_move_destination = (
                scoped_op.target_cols.target_chapter is not None
                and _norm_num_token(scoped_op.target_cols.target_section)
                in _same_label_move_sections_for_chapter(johto, scoped_op.target_cols.target_chapter)
            )
            rewritten_lo = scoped_op.lo
            if scoped_op.lo is not None:
                rewritten_lo = (
                    _lo_to_declared_move_replace(scoped_op.lo)
                    if declared_move_destination
                    else _lo_to_whole_section_insert(scoped_op.lo)
                )
            scoped_op = dc_replace(
                scoped_op,
                op_type=OpType.REPLACE if declared_move_destination else OpType.INSERT,
                move_clause_target_unit_kind=(
                    "chapter" if declared_move_destination else scoped_op.move_clause_target_unit_kind
                ),
                scope_provenance_tags=tuple(scoped_op.scope_provenance_tags)
                + (
                    (
                        "identity_renumber_declared_move_to_replace"
                        if declared_move_destination
                        else "identity_renumber_absent_target_to_insert"
                    ),
                ),
                lo=rewritten_lo,
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
        if _is_whole_section_insert(enriched[-1]) and enriched[-1].target_chapter:
            last_inferred_section_norm = _norm_num_token(enriched[-1].target_section)
            last_inferred_section_chapter = enriched[-1].target_chapter
            last_inferred_section_part = enriched[-1].target_part
        elif _is_whole_section_insert(enriched[-1]):
            last_inferred_section_norm = None
            last_inferred_section_chapter = None
            last_inferred_section_part = None
    patched = enriched
    for target in provision_expiry_overrides:
        if target.target_mid != amendment_id:
            continue
        expiry_iso = expires_on_from_valid_until(target.expiry).isoformat()
        next_patched = []
        for op in patched:
            if (
                _norm_num_token(op.target_cols.target_section or "") == target.section
                and op.target_cols.target_paragraph == target.subsection
                and op.target_cols.target_special == target.special
                and op.lo is not None
                and op.lo.source is not None
            ):
                next_patched.append(
                    dc_replace(
                        op,
                        lo=dc_replace(
                            op.lo,
                            source=dc_replace(
                                op.lo.source,
                                expires=expiry_iso,
                            ),
                        ),
                    )
                )
            else:
                next_patched.append(op)
        patched = next_patched
    for _target_mid, labels, section_expiry in section_expiry_overrides:
        if _target_mid != amendment_id:
            continue
        next_patched: List[AmendmentOp] = []
        for op in patched:
            if (
                op.target_cols.target_unit_kind == "section"
                # Both `labels` and the target_section must pass through the
                # SAME canonical token normalizer.  `labels` were built by
                # `_parse_section_list_labels` (internal whitespace stripped,
                # lowercased → e.g. "21b"); a raw ".lower()" on a spaced path
                # label like "21 b" would miss "21b".  Normalize both sides.
                and _norm_num_token(op.target_cols.target_section or "")
                in {_norm_num_token(label) for label in labels}
                and op.lo is not None
                and op.lo.source is not None
            ):
                next_patched.append(
                    dc_replace(
                        op,
                        lo=dc_replace(
                            op.lo,
                            # section_expiry is the prose-inclusive last in-force
                            # day; kernel `expires` is exclusive — convert here.
                            source=dc_replace(
                                op.lo.source,
                                expires=expires_on_from_valid_until(section_expiry).isoformat(),
                            ),
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
    event_key = op.op_id or op.target_cols.target_section or "op"
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
    muutos_tree: "etree._Element | None" = None,
    source_model: "AmendmentSourceModel | None" = None,
) -> str:
    """Return amendment-body text for a section-targeted temporary op."""
    if op.target_cols.target_unit_kind != "section" or not op.target_cols.target_section:
        return ""

    target_label = _norm_num_token(op.target_cols.target_section)
    if not target_label:
        return ""

    if source_model is not None:
        result = source_model.lookup_section_payload_text(target_label)
        return result.text if result.status == "unique" else ""

    if muutos_tree is None:
        return ""

    for section in muutos_tree.findall(".//{*}section"):
        num_el = section.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        section_label = _normalize_source_section_num(num_el.text)
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
    muutos_tree: "etree._Element | None" = None,
    source_model: "AmendmentSourceModel | None" = None,
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
        tagged_op = _apply_inferred_payload_expiry_to_temporary_ops(
            [tagged_op],
            muutos_tree=muutos_tree,
            source_model=source_model,
        )[0]
        temporal_events.extend(_temporary_events_for_op(tagged_op, amendment_id))
        tagged.append(tagged_op)
    return tagged, temporal_events


def _apply_inferred_payload_expiry_to_temporary_ops(
    ops: List[AmendmentOp],
    *,
    muutos_tree: "etree._Element | None" = None,
    source_model: "AmendmentSourceModel | None" = None,
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
                _body_text_for_temporary_op(
                    op,
                    muutos_tree=muutos_tree,
                    source_model=source_model,
                )
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
                            # inferred tax-year sunset is the inclusive Dec 31 of
                            # the latest named year; kernel `expires` is exclusive.
                            source=dc_replace(
                                source,
                                expires=expires_on_from_valid_until(inferred).isoformat(),
                            ),
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
_VAALIAIKAISESTI_RE = compile_classifier_regex(r'\bväliaikaisesti\b', re.IGNORECASE, classifier_id="fi.frontend_compile.vaaliaikaisesti_re")
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
    # lawvm-regex: prefilter bounded `uusi N moment...` modifier-shape gate over owned johto lookahead; mints no legal state
    if _TEMPORARY_MOMENT_SCOPE_RE.match(lookahead) is None:
        return frozenset()

    # lawvm-regex: owning_parser host-section label scan over the owned johto preceding väliaikaisesti; not a cross-plane raw_text read
    preceding_matches = list(_SECTION_REF_RE.finditer(johto[:vaali_start]))
    if not preceding_matches:
        return frozenset()

    candidate = _norm_num_token(preceding_matches[-1].group(1))
    # lawvm-regex: prefilter valid section-label shape gate on a normalized token; mints no legal state
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
    # lawvm-regex: prefilter väliaikaisesti adverb presence guard over owned johto; mints no legal state
    if _VAALIAIKAISESTI_RE.search(johto) is None:
        return None  # caller already checked, but guard anyway

    all_valid_labels: set[str] = set()
    # lawvm-regex: prefilter per-occurrence väliaikaisesti adverb scan over owned johto; mints no legal state
    for m_vaali in _VAALIAIKAISESTI_RE.finditer(johto):
        after_vaali = johto[m_vaali.end():]

        valid_labels: FrozenSet[str] = frozenset()

        # Find the first "§" after this "väliaikaisesti"
        pykala_pos = after_vaali.find('§')
        if pykala_pos >= 0:
            section_fragment = after_vaali[:pykala_pos]
            # Strip a leading "uusi" / "uudet" word (insertion clauses)
            section_fragment = _TEMPORARY_SECTION_PREFIX_RE.sub("", section_fragment)
            raw_labels = _parse_section_list_labels(section_fragment)

            # Filter: keep only labels that look like valid Finnish section identifiers.
            # "testilain5", "xlain5", etc. are statute-name artifacts → discard.
            valid_labels = frozenset(
                # lawvm-regex: prefilter valid section-label shape filter on normalized tokens; mints no legal state
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
_OPERATIVE_VERB_RE = compile_classifier_regex(r"\b(?:kumotaan|muutetaan|lisätään|poistetaan|siirretään)\b", re.IGNORECASE, classifier_id="fi.frontend_compile.operative_verb_re")
_BODY_ONLY_ITEM_LABEL_RE = re.compile(r"^\s*(\d+[a-z]?)\)")
# Structural-target marker in a johtolause (hoisted per §1.11 from the act-wide
# body-recovery fallback guard); presence means the johto already names a target.
_JOHTO_STRUCTURAL_TARGET_MARKER_RE = compile_classifier_regex(r"\b(?:§|luku|luvun|osa|osan|liite|liitteen)\b", classifier_id="fi.frontend_compile.johto_structural_target_marker_re")


def _body_direct_sections(muutos_tree: "etree._Element") -> "list[etree._Element]":
    body = muutos_tree.find(".//{*}body")
    if body is None:
        return []
    direct_sections = list(body.findall("./{*}section"))
    if direct_sections:
        return direct_sections
    sections: list[etree._Element] = []
    for container in body.findall("./{*}hcontainer"):
        name = (container.get("name") or "").strip()
        if name in {"entryIntoForce", "conclusions", "signatures"}:
            continue
        sections.extend(container.findall("./{*}section"))
    return sections


def _body_section_groups(muutos_tree: "etree._Element") -> "list[tuple[etree._Element, tuple[etree._Element, ...]]]":
    body = muutos_tree.find(".//{*}body")
    if body is None:
        return []
    groups: list[tuple[etree._Element, list[etree._Element]]] = []

    containers = list(body.findall("./{*}hcontainer"))
    if not containers:
        return [(section, ()) for section in body.findall("./{*}section")]

    for container in containers:
        name = (container.get("name") or "").strip()
        if name in {"entryIntoForce", "conclusions", "signatures"}:
            continue
        for child in container:
            child_tag = _direct_child_localname(child)
            if child_tag == "section":
                groups.append((child, []))
            elif child_tag == "subsection" and groups:
                groups[-1][1].append(child)

    return [(section, tuple(orphans)) for section, orphans in groups]


def _is_body_only_amendment_surface(johto: str, source_title: str) -> bool:
    cleaned_johto = _WHITESPACE_RE.sub(" ", johto or "").strip().lower()
    cleaned_title = _WHITESPACE_RE.sub(" ", source_title or "").strip().lower()
    # lawvm-regex: prefilter operative-verb presence guard over owned johto (any verb -> not a body-only surface); mints no legal state
    if not cleaned_johto or _OPERATIVE_VERB_RE.search(cleaned_johto):
        return False
    if "muuttamisesta" not in cleaned_title or "kumoamisesta" in cleaned_title:
        return False
    return "päätöksen mukaisesti" in cleaned_johto or cleaned_johto.endswith("esittelystä")


def _section_label_from_xml(sec: "etree._Element") -> str:
    num_el = sec.find("{*}num")
    if num_el is None:
        return ""
    return _norm_num_token(num_el.text or "")


def _live_subsection_label_for_item(section: IRNode, item_label: str) -> int | None:
    hits: set[int] = set()
    for child in section.children:
        if child.kind is not IRNodeKind.SUBSECTION or child.label is None:
            continue
        for grandchild in child.children:
            if grandchild.kind is IRNodeKind.PARAGRAPH and _norm_num_token(str(grandchild.label or "")) == item_label:
                try:
                    hits.add(int(str(child.label)))
                except ValueError:
                    continue
    if len(hits) != 1:
        return None
    return next(iter(hits))


def _body_section_item_labels(
    sec: "etree._Element",
    orphan_subsections: "tuple[etree._Element, ...]" = (),
) -> list[str]:
    labels: list[str] = []
    p_elements = list(sec.findall(".//{*}p"))
    for orphan in orphan_subsections:
        p_elements.extend(orphan.findall(".//{*}p"))
    for p_el in p_elements:
        text = _WHITESPACE_RE.sub(" ", etree.tostring(p_el, method="text", encoding="unicode")).strip()
        # lawvm-regex: prefilter leading item-label lexer over the amendment's own body <p> text payload; pure label-token shape, mints no legal state
        match = _BODY_ONLY_ITEM_LABEL_RE.match(text)
        if match:
            labels.append(_norm_num_token(match.group(1)))
    return [label for label in labels if label]


def _section_has_direct_omission(sec: "etree._Element") -> bool:
    for child in sec:
        tag = _direct_child_localname(child)
        if tag == "omission":
            return True
        if tag == "p" and (child.get("class") or "").strip() == "omission":
            return True
        if tag == "hcontainer" and (child.get("name") or "").strip() == "omission":
            return True
    return False


def _section_direct_payload_paragraph_count(
    sec: "etree._Element",
    orphan_subsections: "tuple[etree._Element, ...]" = (),
) -> int:
    count = 0
    for child in sec:
        if _direct_child_localname(child) != "p":
            continue
        if (child.get("class") or "").strip() == "omission":
            continue
        text = _WHITESPACE_RE.sub(" ", etree.tostring(child, method="text", encoding="unicode")).strip()
        if text:
            count += 1
    for orphan in orphan_subsections:
        text = _WHITESPACE_RE.sub(" ", etree.tostring(orphan, method="text", encoding="unicode")).strip()
        if text:
            count += 1
    for child in sec:
        if _direct_child_localname(child) != "subsection":
            continue
        text = _WHITESPACE_RE.sub(" ", etree.tostring(child, method="text", encoding="unicode")).strip()
        # lawvm-regex: prefilter item-label lexer over the amendment's own body subsection text payload (excludes item-labelled subsections from the count); mints no legal state
        if text and not _BODY_ONLY_ITEM_LABEL_RE.match(text):
            count += 1
    return count


def _xml_text(element: "etree._Element") -> str:
    return _WHITESPACE_RE.sub(" ", etree.tostring(element, method="text", encoding="unicode")).strip()


def _act_wide_sparse_subsection_text_blocks(sec: "etree._Element") -> tuple[str, ...]:
    """Return sparse unnumbered subsection blocks carried by one body section.

    This recognizes a narrow AKN shape used by act-wide ``muutetaan ...
    seuraavasti`` amendments: the body section has a direct leading omission and
    one unnumbered subsection whose intro/paragraph children carry consecutive
    changed live moments, with an omission marker preserving the tail.
    """
    if not _section_has_direct_omission(sec):
        return ()
    subsections = [child for child in sec if _direct_child_localname(child) == "subsection"]
    if len(subsections) != 1:
        return ()
    subsection = subsections[0]
    blocks: list[str] = []
    saw_omission = False
    for child in subsection:
        tag = _direct_child_localname(child)
        if tag == "hcontainer" and (child.get("name") or "").strip() == "omission":
            saw_omission = True
            continue
        if tag in {"intro", "paragraph", "content", "p"}:
            text = _xml_text(child)
            if text:
                blocks.append(text)
    if not saw_omission or len(blocks) < 2:
        return ()
    return tuple(blocks)


def _unique_live_subsection_window_for_text_blocks(
    live_section: IRNode,
    blocks: tuple[str, ...],
) -> tuple[int, ...]:
    live_subsections = [
        child
        for child in live_section.children
        if child.kind is IRNodeKind.SUBSECTION and str(child.label or "").isdigit()
    ]
    if not blocks or len(blocks) > len(live_subsections):
        return ()

    matches: list[tuple[float, tuple[int, ...]]] = []
    for start in range(0, len(live_subsections) - len(blocks) + 1):
        window = live_subsections[start : start + len(blocks)]
        scores = [
            SequenceMatcher(
                None,
                block,
                _WHITESPACE_RE.sub(" ", irnode_to_text(live_subsection)).strip(),
            ).ratio()
            for block, live_subsection in zip(blocks, window, strict=True)
        ]
        if all(score >= 0.70 for score in scores):
            labels = tuple(int(str(live_subsection.label)) for live_subsection in window)
            matches.append((sum(scores) / len(scores), labels))

    if len(matches) != 1:
        return ()
    return matches[0][1]


def _next_integer_subsection_label(section: IRNode) -> int | None:
    labels: list[int] = []
    for child in section.children:
        if child.kind is not IRNodeKind.SUBSECTION or child.label is None:
            continue
        try:
            labels.append(int(str(child.label)))
        except ValueError:
            return None
    if not labels:
        return 1
    return max(labels) + 1


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
    cleaned = _WHITESPACE_RE.sub(" ", johto).strip().lower()
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
        # lawvm-regex: prefilter letter-suffix num-shape lexer over the amendment's own body <num> payload (gates INSERT fallback); pure label-token shape, mints no legal state
        if not _LETTER_SUFFIX_NUM_RE.match(num_text):
            continue  # plain-number sections handled elsewhere
        label = _norm_num_token(num_text)
        if not label:
            continue
        if master.find_section(label) is not None:
            continue  # already exists — not a new INSERT
        ops.append(AmendmentOp(op_id="", op_type=OpType.INSERT, **fi_section_target(label)))
    return ops


def _enacting_formula_body_insert_unowned_section_findings(
    johto: str,
    muutos_tree: "etree._Element",
    master: "ReplayState",
    *,
    accepted_ops: "list[AmendmentOp]",
    amendment_id: str,
) -> "list[Finding]":
    """Own sibling body sections skipped by the enacting-formula insert fallback."""
    cleaned = _WHITESPACE_RE.sub(" ", johto).strip().lower()
    if cleaned != _ENACTING_FORMULA_EXACT:
        return []

    accepted_targets = {
        op.target_cols.target_section
        for op in accepted_ops
        if op.op_type == OpType.INSERT and op.target_cols.target_unit_kind == "section" and op.target_cols.target_section
    }
    if not accepted_targets:
        return []

    body = muutos_tree.find(".//{*}body")
    if body is None:
        return []
    sections_no_eid = [s for s in body.findall(".//{*}section") if not s.get("eId")]
    findings: list[Finding] = []
    for index, sec in enumerate(sections_no_eid):
        num_el = sec.find("{*}num")
        if num_el is None:
            num_text = ""
            label = ""
            reason_code = "missing_num"
        else:
            num_text = (num_el.text or "").strip()
            label = _norm_num_token(num_text)
            if label in accepted_targets:
                continue
            # lawvm-regex: prefilter letter-suffix num-shape lexer over the amendment's own body <num> payload (diagnostic reason_code); pure label-token shape, mints no legal state
            if not _LETTER_SUFFIX_NUM_RE.match(num_text):
                # lawvm-regex: prefilter plain-num shape classification over the amendment's own body <num> payload (diagnostic reason_code); mints no legal state
                if _PLAIN_SECTION_NUM_RE.match(num_text):
                    reason_code = "plain_number_not_owned_by_insert_fallback"
                else:
                    reason_code = "unsupported_num_shape"
            elif not label:
                reason_code = "unparseable_section_label"
            elif master.find_section(label) is not None:
                reason_code = "existing_letter_section_not_inserted"
            else:
                reason_code = "letter_section_not_selected"
        findings.append(
            Finding(
                kind="PARSE.UNOWNED_BODY_SECTION",
                role="observation",
                stage="frontend_compile",
                detail={
                    "message": "Enacting-formula body insert fallback left a sibling body section unowned.",
                    "fallback_rule": "_extract_enacting_formula_body_insert_ops_fallback",
                    "reason_code": reason_code,
                    "section_index": index,
                    "num_text": num_text,
                    "target_section": label,
                    "accepted_insert_targets": sorted(accepted_targets),
                },
                source_statute=amendment_id,
                blocking=False,
            )
        )
    return findings


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
    cleaned = _WHITESPACE_RE.sub(" ", johto).strip().lower()
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
    # lawvm-regex: prefilter plain-num shape gate over the amendment's own body <num> payload (ceremonial-formula REPLACE fallback); pure label-token shape, mints no legal state
    if not _PLAIN_SECTION_NUM_RE.match(num_text):
        return []
    label = _norm_num_token(num_text)
    if not label or master.find_section(label) is None:
        return []
    return [AmendmentOp(op_id="", op_type=OpType.REPLACE, **fi_section_target(label))]


def _extract_ceremonial_body_only_ops_fallback(
    johto: str,
    source_title: str,
    muutos_tree: "etree._Element",
    master: "ReplayState",
) -> "list[AmendmentOp]":
    """Recover sparse body-only amendments with no operative preamble.

    Some decree amendments publish only a ceremonial preamble and encode the
    operative surface as body sections. The title supplies amendment character
    (``... muuttamisesta``); the body supplies explicit provision labels. This
    fallback owns only labels visible in the body and only against matching live
    state:

    - body item labels become item-level REPLACE/INSERT ops under the unique
      live subsection that owns the item list;
    - a direct omission followed by one body paragraph appends a new subsection;
    - one direct body paragraph with no omission replaces subsection 1, keeping
      the existing section heading outside the mutation boundary.
    """
    if not _is_body_only_amendment_surface(johto, source_title):
        return []
    if muutos_tree.find(".//{*}chapter") is not None or muutos_tree.find(".//{*}part") is not None:
        return []

    ops: list[AmendmentOp] = []
    for sec, orphan_subsections in _body_section_groups(muutos_tree):
        section_label = _section_label_from_xml(sec)
        if not section_label:
            continue
        live_section = master.find_section(section_label)
        if live_section is None:
            continue

        item_labels = _body_section_item_labels(sec, orphan_subsections)
        if item_labels:
            existing_owner_labels = {
                owner_label
                for item_label in item_labels
                for owner_label in [_live_subsection_label_for_item(live_section, item_label)]
                if owner_label is not None
            }
            if len(existing_owner_labels) != 1:
                continue
            [owner_label] = list(existing_owner_labels)
            for item_label in item_labels:
                op_type = OpType.REPLACE if _live_subsection_label_for_item(live_section, item_label) is not None else OpType.INSERT
                ops.append(
                    AmendmentOp(
                        op_id="",
                        op_type=op_type,
                        **fi_section_target(
                            section_label,
                            subsection=owner_label,
                            item=item_label,
                        ),
                    )
                )
            continue

        direct_payload_count = _section_direct_payload_paragraph_count(sec, orphan_subsections)
        if direct_payload_count != 1:
            continue
        if _section_has_direct_omission(sec):
            next_label = _next_integer_subsection_label(live_section)
            if next_label is None:
                continue
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type=OpType.INSERT,
                    **fi_section_target(section_label, subsection=next_label),
                )
            )
            continue
        if _live_subsection_label_for_item(live_section, "1") is None and live_section.children:
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type=OpType.REPLACE,
                    **fi_section_target(section_label, subsection=1),
                )
            )

    return _dedupe_fallback_ops_ir(ops)


def _extract_act_wide_body_section_replace_ops_fallback(
    johto: str,
    source_title: str,
    muutos_tree: "etree._Element",
    master: "ReplayState",
) -> "list[AmendmentOp]":
    """Recover body-labelled section replacements from act-wide change formulas.

    Some Finnish amendment regulations say only that the named act/regulation is
    changed ``seuraavasti`` and leave the concrete provision labels to the
    body.  In that bounded shape the body section labels are source-owned target
    evidence.  This recovery deliberately refuses formulas that already name a
    section, chapter, part, or appendix target in the preamble.
    """
    cleaned_johto = _WHITESPACE_RE.sub(" ", johto or "").strip().lower()
    cleaned_title = _WHITESPACE_RE.sub(" ", source_title or "").strip().lower()
    if "muutetaan" not in cleaned_johto or "seuraavasti" not in cleaned_johto:
        return []
    if "muuttamisesta" not in cleaned_title or "kumoamisesta" in cleaned_title:
        return []
    # lawvm-regex: prefilter structural-target marker guard over owned johto (refuses act-wide body recovery when the johto already names a structural target); mints no legal state
    if _JOHTO_STRUCTURAL_TARGET_MARKER_RE.search(cleaned_johto):
        return []
    if muutos_tree.find(".//{*}chapter") is not None or muutos_tree.find(".//{*}part") is not None:
        return []

    ops: list[AmendmentOp] = []
    seen: set[str] = set()
    for sec, _orphan_subsections in _body_section_groups(muutos_tree):
        section_label = _section_label_from_xml(sec)
        if not section_label or section_label in seen:
            return []
        live_section = master.find_section(section_label)
        if live_section is None:
            return []
        seen.add(section_label)
        sparse_blocks = _act_wide_sparse_subsection_text_blocks(sec)
        sparse_targets = _unique_live_subsection_window_for_text_blocks(live_section, sparse_blocks)
        if sparse_blocks and not sparse_targets:
            return []
        if sparse_targets:
            for target_paragraph in sparse_targets:
                ops.append(
                    AmendmentOp(
                        op_id="",
                        op_type=OpType.REPLACE,
                        **fi_section_target(section_label, subsection=target_paragraph),
                    )
                )
            continue
        ops.append(
            AmendmentOp(
                op_id="",
                op_type=OpType.REPLACE,
                **fi_section_target(section_label),
            )
        )
    return _dedupe_fallback_ops_ir(ops)


def _accepted_fallback_op_findings(
    ops: "list[AmendmentOp]",
    *,
    amendment_id: str,
    source: str,
    rule_id: str,
    johto: str,
) -> "list[Finding]":
    """Emit one witnessed observation per executable op minted from raw text.

    A heuristic fallback recognizer minted these ops from raw johtolause because
    no typed parser owned the clause. Each minted op carries ``witness_rule_id``
    and ``fallback_provenance``; this records the matching finding so the
    raw-text mint is a first-class evidence object, never a silent legal-state
    move (no-representation-regression witness exception).
    """
    findings: "list[Finding]" = []
    johto_preview = _WHITESPACE_RE.sub(" ", johto or "").strip()[:240]
    for op in ops:
        findings.append(
            Finding(
                kind="PARSE.FALLBACK_OP_FROM_RAW_TEXT",
                role="observation",
                stage="frontend_compile",
                detail={
                    "message": (
                        "Executable op minted from raw johtolause by a heuristic "
                        "fallback recognizer; no typed parser owned the clause."
                    ),
                    "fallback_source": source,
                    "rule_id": rule_id,
                    "op_type": op.op_type,
                    "description": op.description(),
                    "target_section": op.target_cols.target_section or "",
                    "target_unit_kind": op.target_cols.target_unit_kind,
                    "target_paragraph": op.target_cols.target_paragraph,
                    "target_item": op.target_cols.target_item or "",
                    "target_special": op.target_cols.target_special or "",
                    "johto_preview": johto_preview,
                },
                source_statute=amendment_id,
                blocking=False,
            )
        )
    return findings


def _act_wide_body_section_replace_findings(
    ops: "list[AmendmentOp]",
    *,
    amendment_id: str,
    johto: str,
) -> "list[Finding]":
    findings: list[Finding] = []
    if not ops:
        return findings
    for op in ops:
        findings.append(
            Finding(
                kind="PARSE.BODY_SECTION_REPLACE_FROM_ACT_WIDE_FORMULA",
                role="observation",
                stage="frontend_compile",
                detail={
                    "message": (
                        "Act-wide muutetaan formula supplied no provision target; "
                        "a labelled body section already present in live state was "
                        "used as the source-owned replacement target."
                    ),
                    "rule_id": FI_ACT_WIDE_BODY_SECTION_REPLACE_RULE_ID,
                    "description": op.description(),
                    "target_section": op.target_cols.target_section or "",
                    "target_paragraph": op.target_cols.target_paragraph,
                    "johto_preview": _WHITESPACE_RE.sub(" ", johto or "").strip()[:240],
                },
                source_statute=amendment_id,
                blocking=False,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# normalize_and_compile_ops — the main frontend extraction orchestrator
# ---------------------------------------------------------------------------


def normalize_and_compile_ops(
    johto: str,
    muutos_tree: "etree._Element",
    master: "ReplayState",
    amendment_id: str,
    source_title: str,
    used_preamble_body_fallback: bool,
    parent_id: str = "",
    strict_profile: Optional[StrictProfile] = None,
    parse_result: "ClauseParseResult | None" = None,
    regex_recognition_coverage_out: Optional[List[RegexRecognitionCoverage]] = None,
    base_ir: IRNode | None = None,
    amendment_metadata: _AmendmentTreeMetadata | None = None,
    source_model: "AmendmentSourceModel | None" = None,
) -> "PhaseResult[List[AmendmentOp]]":
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
        base_ir:            Original parent statute IR, used only as a read-only prior-address witness.
        amendment_id:                Amendment statute id (for enrichment + logging).
        source_title:       Amendment title (for title-fallback path).
        used_preamble_body_fallback: True when ``johto`` came from sec_1 body text.
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
                target_section=op.target_cols.target_section or "",
                target_unit_kind=op.target_cols.target_unit_kind,
                target_chapter=op.target_cols.target_chapter,
                target_subsection=_op_target_subsection_label(op),
                target_item=op.target_cols.target_item,
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

    parse_result_local = parse_result
    prechecked_legal_ops: List[LegalOperation] | None = None
    parser_has_structural_targets = False
    # Rank-17: collect typed receipts for clause nodes the ingress seam cannot
    # lower (MetaClause/ItemShiftClause/NamedRowClause) so they stop being a
    # silent drop. Filled by whichever extraction call actually runs.
    _ingress_lowering_diagnostics: List[ClauseAstLoweringDiagnostic] = []
    if used_preamble_body_fallback:
        if parse_result_local is None:
            parse_result_local = parse_johtolause_clause(johto, statute_id=parent_id or amendment_id)
        prechecked_legal_ops = extract_johtolause_legal_ops_from_parse_result(
            parse_result_local, diagnostics_out=_ingress_lowering_diagnostics
        )
        parser_has_structural_targets = _parser_produced_structural_targets(prechecked_legal_ops)

    peg_skip_for_sec1_repeal_list = used_preamble_body_fallback and _sec1_fallback_peg_skip_required(
        johto,
        parent_id,
        parser_has_structural_targets=parser_has_structural_targets,
    )
    legal_ops: List[LegalOperation] = []
    if not peg_skip_for_sec1_repeal_list:
        if parse_result_local is None:
            parse_result_local = parse_johtolause_clause(johto, statute_id=parent_id or amendment_id)
        if prechecked_legal_ops is not None:
            legal_ops = prechecked_legal_ops
        else:
            legal_ops = extract_johtolause_legal_ops_from_parse_result(
                parse_result_local, diagnostics_out=_ingress_lowering_diagnostics
            )
    for _ld in _ingress_lowering_diagnostics:
        frontend_findings_out.append(
            Finding(
                kind=_ld.kind,
                role="observation",
                stage="frontend_compile",
                detail={
                    "message": _ld.reason,
                    "node_kind": _ld.node_kind,
                    "rule_id": _ld.rule_id,
                    "sequence": _ld.sequence,
                    "scope": str(_ld.scope) if _ld.scope is not None else "",
                    "detail": _ld.detail or "",
                    "source_statute": amendment_id,
                },
                source_statute=amendment_id,
                blocking=False,
            )
        )
    if parse_result_local is not None:
        # Conservation across the frontend boundary: parse-layer findings are
        # part of this phase's output. In particular a blocking parse-layer
        # violation must not vanish while a fallback-recovered op replays in
        # its place.
        frontend_findings_out.extend(parse_result_local.findings)
    if peg_skip_for_sec1_repeal_list:
        frontend_findings_out.append(
            Finding(
                kind="PARSE.GRAMMAR_SKIP_PREAMBLE_REPEAL_LIST",
                role="observation",
                stage="frontend_compile",
                detail={
                    "message": "PEG extraction skipped for sec1 repeal-list fallback pattern",
                    "source_statute": amendment_id,
                    "parent_statute": parent_id,
                    "used_preamble_body_fallback": True,
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
        legal_ops = _strip_unjustified_chapter_scope_from_unique_sections(
            legal_ops,
            johto,
            master,
            source_model=source_model,
        )
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
        ops = _tag_numbered_table_target_clause_ops(ops, johto)
        ops = _supplement_item_and_moment_clause_ops(ops, johto)
        ops = _supplement_mixed_explicit_clause_ops(ops, johto)
        ops = _supplement_jolloin_moment_renumber_ops(ops, johto)
    else:
        ops = []
    ops, osalta_findings = _supplement_sparse_osalta_row_omission_repeals(
        ops,
        johto,
        amendment_id=amendment_id,
    )
    frontend_findings_out.extend(osalta_findings)
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
    tree_metadata = amendment_metadata or _amendment_tree_metadata(
        amendment_id=amendment_id,
        muutos_tree=muutos_tree,
    )
    ops = _enrich_ops_from_amendment_tree(
        ops,
        amendment_id,
        muutos_tree,
        master,
        johto=johto,
        base_ir=base_ir,
        parent_id=parent_id or "",
        metadata=tree_metadata,
        source_model=source_model,
    )
    ops = _retarget_letter_suffix_inserts_from_same_amendment_stem_scope(
        ops,
        source_model=source_model,
        master=master,
    )
    ops, historical_kohta_findings = _normalize_historical_top_level_kohta_subsection_ops(
        ops,
        johto=johto,
        muutos_tree=muutos_tree,
        master=master,
        amendment_id=amendment_id,
    )
    frontend_findings_out.extend(historical_kohta_findings)
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
            ops, temp_events = _tag_temporary_ops(
                ops,
                amendment_id=amendment_id,
                muutos_tree=muutos_tree,
                source_model=source_model,
            )
            temporary_temporal_events.extend(temp_events)
        elif _temporary_targets is not None:
            # Section-scoped: only tag ops whose target_section is in the set
            tagged_ops: List[AmendmentOp] = []
            for op in ops:
                # Same root-cause asymmetry as the section_expiry_overrides
                # stamp site above: `_temporary_targets` were normalized by
                # `_parse_section_list_labels` (→ "21b"), so a raw ".lower()"
                # of a spaced path label "21 b" would miss the temporary scope.
                if _norm_num_token(op.target_cols.target_section or "") in {
                    _norm_num_token(label) for label in _temporary_targets
                }:
                    temp_tagged, temp_events = _tag_temporary_ops(
                        [op],
                        amendment_id=amendment_id,
                        muutos_tree=muutos_tree,
                        source_model=source_model,
                    )
                    tagged_ops.extend(temp_tagged)
                    temporary_temporal_events.extend(temp_events)
                else:
                    tagged_ops.append(op)
            ops = tagged_ops
    if ops:
        ops = _apply_inferred_payload_expiry_to_temporary_ops(
            ops,
            muutos_tree=muutos_tree,
            source_model=source_model,
        )
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
                            "target_section": op.target_cols.target_section or "",
                            "target_chapter": op.target_cols.target_chapter or "",
                        },
                        source_statute=amendment_id,
                        blocking=False,
                    )
                )
            patched_ops.append(op)
        ops = patched_ops

    # Fallback paths (still AmendmentOp-based, skips LO normalization chain)
    # Heuristic #29: parse_ops_fallback_heuristic — gated by allows_target_guessing.
    # Retained rank-3 fallback (load-bearing residual, proven by
    # normalize_fallback_heuristic_census); only fires when the typed parse
    # yields no ops.
    _allows_fallback = strict_profile is None or strict_profile.allows_target_guessing
    fallback_result = parse_ops_fallback_heuristic_with_coverage(
        johto,
        source_artifact_id=amendment_id,
    )
    fallback_ops = fallback_result.ops
    if regex_recognition_coverage_out is not None:
        regex_recognition_coverage_out.extend(fallback_result.regex_recognition_coverage)
    if fallback_ops and _allows_fallback:
        logger.debug("  %s fallback_ops: %s", amendment_id, [op.description() for op in fallback_ops])
        enriched_fallback_ops = _enrich_ops_from_amendment_tree(
            fallback_ops,
            amendment_id,
            muutos_tree,
            master,
            johto=johto,
            base_ir=base_ir,
            parent_id=parent_id or "",
            metadata=tree_metadata,
            source_model=source_model,
        )
        fallback_plain_insert_count = sum(
            1
            for op in enriched_fallback_ops
            if op.op_type == OpType.INSERT
            and op.target_cols.target_special is None
        )
        for op in enriched_fallback_ops:
            op.fallback_provenance = True
            op.extraction_provenance_tags = tuple(
                dict.fromkeys((*op.extraction_provenance_tags, "extraction_fallback_heuristic"))
            )
            # Tag the fallback-extraction lane so the spec ledger can see it.
            # Diagnostic-only metadata (zero replay semantics); never overwrite a
            # real parser-rule id already carried by an upstream rule.
            if not op.witness_rule_id:
                op.witness_rule_id = FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID
        enriched_fallback_ops, rejected_overbroad_fallback_repeals = _reject_overbroad_section_repeals_for_deep_targets(
            enriched_fallback_ops,
            johto=johto,
            amendment_id=amendment_id,
        )
        frontend_findings_out.extend(rejected_overbroad_fallback_repeals)
        if not ops:
            frontend_findings_out.extend(
                _accepted_fallback_op_findings(
                    enriched_fallback_ops,
                    amendment_id=amendment_id,
                    source="parse_ops_fallback_heuristic",
                    rule_id=FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID,
                    johto=johto,
                )
            )
            ops = enriched_fallback_ops
        elif _allows_additive_subsection_fallback and fallback_plain_insert_count > 0:
            existing_keys = {
                (
                    op.op_type,
                    op.target_cols.target_section,
                    op.target_cols.target_paragraph,
                    op.target_cols.target_item,
                    op.target_cols.target_special,
                )
                for op in ops
            }
            for op in enriched_fallback_ops:
                key = (
                    op.op_type,
                    op.target_cols.target_section,
                    op.target_cols.target_paragraph,
                    op.target_cols.target_item,
                    op.target_cols.target_special,
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
                single_payload_already_owned = _single_payload_already_owned_fallback_insert_observation(
                    ops,
                    op,
                    amendment_id=amendment_id,
                    muutos_tree=muutos_tree,
                )
                if single_payload_already_owned is not None:
                    frontend_findings_out.append(single_payload_already_owned)
                    continue
                if op.op_type != OpType.INSERT:
                    continue
                if op.target_cols.target_special is not None:
                    continue
                frontend_findings_out.extend(
                    _accepted_fallback_op_findings(
                        [op],
                        amendment_id=amendment_id,
                        source="parse_ops_fallback_heuristic_additive_insert",
                        rule_id=FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID,
                        johto=johto,
                    )
                )
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
                    base_ir=base_ir,
                    parent_id=parent_id or "",
                    metadata=tree_metadata,
                    source_model=source_model,
                )
                for op in ops:
                    op.body_root_replace_fallback = True
                    op.fallback_provenance = True
                    if not op.witness_rule_id:
                        op.witness_rule_id = FI_BODY_ROOT_REPLACE_FALLBACK_RULE_ID
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_body_root_replace"))
                    )
                frontend_findings_out.extend(
                    _accepted_fallback_op_findings(
                        ops,
                        amendment_id=amendment_id,
                        source="_extract_root_replace_ops_from_body_fallback",
                        rule_id=FI_BODY_ROOT_REPLACE_FALLBACK_RULE_ID,
                        johto=johto,
                    )
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
                    base_ir=base_ir,
                    parent_id=parent_id or "",
                    metadata=tree_metadata,
                    source_model=source_model,
                )
                for op in ops:
                    op.fallback_provenance = True
                    if not op.witness_rule_id:
                        op.witness_rule_id = FI_ENACTING_FORMULA_BODY_REPLACE_FALLBACK_RULE_ID
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_enacting_formula_body_replace"))
                    )
                frontend_findings_out.extend(
                    _accepted_fallback_op_findings(
                        ops,
                        amendment_id=amendment_id,
                        source="_extract_enacting_formula_body_replace_ops_fallback",
                        rule_id=FI_ENACTING_FORMULA_BODY_REPLACE_FALLBACK_RULE_ID,
                        johto=johto,
                    )
                )
            else:
                frontend_findings_out.extend(
                    _strict_rejected_op_findings(
                        ef_replace_ops,
                        source="_extract_enacting_formula_body_replace_ops_fallback",
                    )
                )

    if not ops:
        ceremonial_body_ops = _extract_ceremonial_body_only_ops_fallback(johto, source_title, muutos_tree, master)
        if ceremonial_body_ops:
            if _allows_fallback:
                logger.debug(
                    "  %s ceremonial_body_only_ops: %s",
                    amendment_id,
                    [op.description() for op in ceremonial_body_ops],
                )
                ops = _enrich_ops_from_amendment_tree(
                    ceremonial_body_ops,
                    amendment_id,
                    muutos_tree,
                    master,
                    johto=johto,
                    base_ir=base_ir,
                    parent_id=parent_id or "",
                    metadata=tree_metadata,
                    source_model=source_model,
                )
                for op in ops:
                    op.fallback_provenance = True
                    op.witness_rule_id = FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_ceremonial_body_only"))
                    )
                frontend_findings_out.extend(
                    _accepted_fallback_op_findings(
                        ops,
                        amendment_id=amendment_id,
                        source="_extract_ceremonial_body_only_ops_fallback",
                        rule_id=FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID,
                        johto=johto,
                    )
                )
            else:
                frontend_findings_out.extend(
                    _strict_rejected_op_findings(
                        ceremonial_body_ops,
                        source="_extract_ceremonial_body_only_ops_fallback",
                    )
                )

    if not ops:
        act_wide_body_replace_ops = _extract_act_wide_body_section_replace_ops_fallback(
            johto,
            source_title,
            muutos_tree,
            master,
        )
        if act_wide_body_replace_ops:
            if _allows_fallback:
                logger.debug(
                    "  %s act_wide_body_section_replace_ops: %s",
                    amendment_id,
                    [op.description() for op in act_wide_body_replace_ops],
                )
                ops = _enrich_ops_from_amendment_tree(
                    act_wide_body_replace_ops,
                    amendment_id,
                    muutos_tree,
                    master,
                    johto=johto,
                    base_ir=base_ir,
                    parent_id=parent_id or "",
                    metadata=tree_metadata,
                    source_model=source_model,
                )
                for op in ops:
                    op.fallback_provenance = True
                    op.witness_rule_id = FI_ACT_WIDE_BODY_SECTION_REPLACE_RULE_ID
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_act_wide_body_section_replace"))
                    )
                frontend_findings_out.extend(
                    _act_wide_body_section_replace_findings(
                        ops,
                        amendment_id=amendment_id,
                        johto=johto,
                    )
                )
            else:
                frontend_findings_out.extend(
                    _strict_rejected_op_findings(
                        act_wide_body_replace_ops,
                        source="_extract_act_wide_body_section_replace_ops_fallback",
                    )
                )

    if not ops:
        # Retained rank-3 title-fallback lane (load-bearing residual, proven by
        # normalize_fallback_heuristic_census); fires only when the body yields
        # no ops.
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
                    base_ir=base_ir,
                    parent_id=parent_id or "",
                    metadata=tree_metadata,
                    source_model=source_model,
                )
                for op in ops:
                    op.fallback_provenance = True
                    if not op.witness_rule_id:
                        op.witness_rule_id = FI_TITLE_FALLBACK_RULE_ID
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_title_fallback"))
                    )
                frontend_findings_out.extend(
                    _accepted_fallback_op_findings(
                        ops,
                        amendment_id=amendment_id,
                        source="parse_ops_title_fallback",
                        rule_id=FI_TITLE_FALLBACK_RULE_ID,
                        johto=johto,
                    )
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
            frontend_findings_out.extend(
                _enacting_formula_body_insert_unowned_section_findings(
                    johto,
                    muutos_tree,
                    master,
                    accepted_ops=ef_insert_ops,
                    amendment_id=amendment_id,
                )
            )
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
                    base_ir=base_ir,
                    parent_id=parent_id or "",
                    metadata=tree_metadata,
                    source_model=source_model,
                )
                for op in ops:
                    op.fallback_provenance = True
                    if not op.witness_rule_id:
                        op.witness_rule_id = FI_ENACTING_FORMULA_BODY_INSERT_FALLBACK_RULE_ID
                    op.extraction_provenance_tags = tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, "extraction_enacting_formula_body_insert"))
                    )
                frontend_findings_out.extend(
                    _accepted_fallback_op_findings(
                        ops,
                        amendment_id=amendment_id,
                        source="_extract_enacting_formula_body_insert_ops_fallback",
                        rule_id=FI_ENACTING_FORMULA_BODY_INSERT_FALLBACK_RULE_ID,
                        johto=johto,
                    )
                )
            else:
                frontend_findings_out.extend(
                    _strict_rejected_op_findings(
                        ef_insert_ops,
                        source="_extract_enacting_formula_body_insert_ops_fallback",
                    )
                )

    # Tag sec1 body-text fallback on all ops from this amendment
    if used_preamble_body_fallback and ops:
        for op in ops:
            op.sec1_body_johto_fallback = True
            op.extraction_provenance_tags = tuple(
                dict.fromkeys((*op.extraction_provenance_tags, "extraction_preamble_body"))
            )
    if ops:
        reinstated_scope_ops: list[AmendmentOp] = []
        for op in ops:
            reinstated_scope = _infer_flat_reinstated_section_scope_from_base(
                op=op,
                muutos_tree=muutos_tree,
                master=master,
                base_ir=base_ir,
                johto=johto,
                amendment_id=amendment_id,
                parent_id=parent_id,
                source_model=source_model,
            )
            if reinstated_scope is None:
                reinstated_scope_ops.append(op)
                continue
            reinstated_part, reinstated_chapter = reinstated_scope
            if reinstated_chapter is None or (
                reinstated_part == op.target_cols.target_part and reinstated_chapter == op.target_cols.target_chapter
            ):
                reinstated_scope_ops.append(op)
                continue
            reinstated_scope_ops.append(
                _add_inferred_section_chapter_scope(
                    op,
                    part=reinstated_part,
                    chapter=reinstated_chapter,
                    rule_id="fi_reinstated_section_scope_from_prior_repeal_address",
                )
            )
        ops = reinstated_scope_ops
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
                    "used_preamble_body_fallback": used_preamble_body_fallback,
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


# ---------------------------------------------------------------------------
# normalize_and_compile_ops_staged — WAIST #6 canonical-op StageResult adapter
# ---------------------------------------------------------------------------


def phase_result_to_canonical_op_stage(
    phase_result: "PhaseResult[List[AmendmentOp]]",
) -> "StageResult[List[AmendmentOp]]":
    """Adapt the canonical-op ``PhaseResult`` onto the typed ``StageResult`` account.

    WAIST #6 (canonical-operation / normalize / effect-lowering). The existing
    ``normalize_and_compile_ops`` producer already returns the rich typed
    ``PhaseResult`` carrier; this is an ADAPTER, not a from-scratch carrier — it
    PROJECTS that carrier onto the canonical ``StageResult[list[AmendmentOp]]``:

      * ``value``     — ``phase_result.output`` (the emitted ops, unchanged).
      * ``findings``  — the OBSERVATION-role findings (informational; they do not
        block). The blocking OBLIGATION/VIOLATION findings become typed
        ``Residual`` records instead, so blocking lives in exactly one typed
        account (the §LEDGER's "incompleteness can block a clean claim" home).
      * ``residuals`` — one ``Residual(kind="unowned_violation", blocking=True)``
        per blocking obligation/violation finding (a strict-rejected candidate op
        / source pathology that must block). The reason/scope are sourced verbatim
        from the finding so the residual is self-evidencing.
      * ``coverage``  — ESCALATE-3D RESOLVED: ``total = #emitted ops + #rejected
        candidate ops`` where the rejected lane is the producer's own typed
        rejection findings (each blocking obligation = one rejected candidate op),
        i.e. reuse the existing typed partition rather than a synthetic source
        recount. ``owned`` = emitted ops; ``violation`` = rejected (blocking)
        candidates. ``is_partition()`` holds.
      * ``evidence``  — ``EMPTY_EVIDENCE`` (ops cite source downstream via the
        apply ``WriteReceipt.source_anchor``, not here).
      * ``authority`` — ``NEUTRAL_AUTHORITY`` (Pro §8): a canonical op is NOT yet
        execution-authorized; authorization attaches at the apply waist (#7).
    """
    from lawvm.core.stage_result import (
        EMPTY_EVIDENCE,
        NEUTRAL_AUTHORITY,
        CoverageCertificate,
        Residual,
        StageResult,
    )

    ops = phase_result.output
    observations: List[Finding] = []
    residuals: List[Residual] = []
    for finding in phase_result.findings():
        if finding.role == "observation":
            observations.append(finding)
            continue
        # obligation / violation — the blocking decline channel. Project each
        # onto a typed blocking residual; the reason/scope are self-evidencing.
        detail = finding.detail
        message = str(detail.get("message", "") or "")
        target = str(
            detail.get("target_section", "")
            or detail.get("reason", "")
            or ""
        )
        residuals.append(
            Residual(
                kind="unowned_violation",
                reason=(
                    message
                    or f"{finding.kind}: strict-rejected canonical operation"
                ),
                scope=finding.kind,
                source_unit_id=finding.source_statute,
                text=target,
                blocking=bool(finding.blocking),
            )
        )

    emitted = len(ops)
    rejected = len(residuals)
    coverage = CoverageCertificate(
        unit="candidate_ops",
        total=emitted + rejected,
        owned=emitted,
        violation=rejected,
        totality_claimed=True,
    )
    return StageResult(
        value=ops,
        evidence=EMPTY_EVIDENCE,
        residuals=tuple(residuals),
        findings=tuple(observations),
        coverage=coverage,
        authority=NEUTRAL_AUTHORITY,
    )


def normalize_and_compile_ops_staged(
    johto: str,
    muutos_tree: "etree._Element",
    master: "ReplayState",
    amendment_id: str,
    source_title: str,
    used_preamble_body_fallback: bool,
    parent_id: str = "",
    strict_profile: Optional[StrictProfile] = None,
    parse_result: "ClauseParseResult | None" = None,
    regex_recognition_coverage_out: Optional[List[RegexRecognitionCoverage]] = None,
    base_ir: IRNode | None = None,
    amendment_metadata: "_AmendmentTreeMetadata | None" = None,
    source_model: "AmendmentSourceModel | None" = None,
) -> "StageResult[List[AmendmentOp]]":
    """StageResult-carried form of :func:`normalize_and_compile_ops` (WAIST #6).

    Calls the existing producer and adapts its ``PhaseResult`` onto the typed
    ``StageResult`` account via :func:`phase_result_to_canonical_op_stage`. The
    ops + observation findings are byte-identical; the blocking decline becomes a
    typed ``Residual`` (the single load-bearing blocking channel).
    """
    phase_result = normalize_and_compile_ops(
        johto,
        muutos_tree,
        master,
        amendment_id,
        source_title,
        used_preamble_body_fallback,
        parent_id=parent_id,
        strict_profile=strict_profile,
        parse_result=parse_result,
        regex_recognition_coverage_out=regex_recognition_coverage_out,
        base_ir=base_ir,
        amendment_metadata=amendment_metadata,
        source_model=source_model,
    )
    return phase_result_to_canonical_op_stage(phase_result)


if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState
    from lawvm.core.phase_result import PhaseResult
    from lawvm.core.stage_result import StageResult
