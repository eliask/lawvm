"""UK Amendment Replay Pipeline.

This module implements the acquisition and op-extraction layer for building
a PIT (Point-in-Time) legal graph from first principles for UK legislation —
analogous to lawvm.finland.grafter but without LLM dependency for the
amendment schedule, since UK effects feeds provide structured metadata.

Architecture:
  1. Effects feed  → ordered list of StructuredAmendmentOps
  2. For each op: fetch the affecting act's XML from legislation.gov.uk
  3. Extract the provision text referenced by the op
  4. Compile to IR ops against the base statute IR
  5. Replay enacted base + IR ops → PIT states
  6. Compare against official consolidated versions (oracle score)

Current status:
  - EffectsParser: reads all effects pages → list of UKEffectRecord
  - ManifestBuilder: generates acquisition manifests for affecting acts
  - AffectingActFetcher: downloads affecting act XML via legislation.gov.uk API
  - ProvisionExtractor: finds referenced provision text in affecting act XML
  - OpCompiler: converts provision text → IR op (IN PROGRESS)
  - Replayer: applies IR ops to base enacted IR (PLANNED)
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import Levenshtein
from dataclasses import dataclass, field, replace as dc_replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional, Sequence, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from lawvm.core import tree_ops
from lawvm.core.ir import (
    IRStatute,
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.core.phase_result import Finding
from lawvm.core.replay_lints import build_text_duplication_findings
from lawvm.uk_legislation.canonicalize import (
    canonicalize_uk_address,
    uk_compound_subsection_candidate,
    uk_addr_container,
    uk_find_body_predecessor_parent,
    uk_kind_matches,
    uk_is_transparent_wrapper_kind,
    uk_recursive_kind_match,
    uk_schedule_ordinal_paragraph_matches,
    uk_schedule_root_candidates,
    uk_semantic_path_key,
    uk_should_bubble_structural_commencement,
    uk_should_descend_transparently,
)
from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute

if TYPE_CHECKING:
    from lawvm.core.compile_result import TemporalEvent


# ---------------------------------------------------------------------------
# LegalAddress / LegalOperation helpers
# ---------------------------------------------------------------------------


def _make_address(
    container: str,
    section: Optional[str] = None,
    part: Optional[str] = None,
    chapter: Optional[str] = None,
    subsection: Optional[str] = None,
    item: Optional[str] = None,
    special: Optional[FacetKind] = None,
) -> LegalAddress:
    """Build a LegalAddress from the flat-field style used by the UK parser."""
    path: list[tuple[str, str]] = []
    if container == "schedule":
        if section is not None:
            path.append(("schedule", section))
        if part:
            path.append(("part", part))
        if chapter:
            path.append(("chapter", chapter))
        if subsection:
            path.append(("paragraph", subsection))
        if item:
            path.append(("paragraph", item))
    else:
        if part:
            path.append(("part", part))
        if chapter:
            path.append(("chapter", chapter))
        if section:
            path.append(("section", section))
        if subsection:
            path.append(("subsection", subsection))
        if item:
            path.append(("paragraph", item))
    return LegalAddress(path=tuple(path), special=special)


def _addr_container(addr: LegalAddress) -> str:
    """Return the top-level container kind of a LegalAddress."""
    return uk_addr_container(addr)


def _addr_field(addr: LegalAddress, kind: str) -> Optional[str]:
    """Return the label for the first path segment matching *kind*, or None."""
    for k, lbl in addr.path:
        if k == kind:
            return lbl
    return None


def _addr_leaf_label(addr: LegalAddress) -> Optional[str]:
    """Return the deepest meaningful label from a LegalAddress path."""
    for kind, lbl in reversed(addr.path):
        if lbl:
            return lbl
    return None


def _addr_leaf_kind(addr: LegalAddress) -> Optional[str]:
    """Return the deepest path kind from a LegalAddress, if any."""
    if not addr.path:
        return None
    return addr.path[-1][0]


def _schedule_target_levels(addr: LegalAddress) -> tuple[Optional[str], Optional[str], list[str]]:
    """Return typed schedule descendant labels as (paragraph, subparagraph, items)."""
    paragraph = None
    subparagraph = None
    items: list[str] = []
    for kind, lbl in addr.path:
        if not lbl:
            continue
        if kind == "paragraph":
            paragraph = lbl
        elif kind == "subparagraph":
            subparagraph = lbl
        elif kind in {"item", "point"}:
            items.append(lbl)
    return paragraph, subparagraph, items


def _looks_like_lettered_item_label(label: str) -> bool:
    return bool(re.fullmatch(r"[a-z]+", (label or "").strip(), re.I))


def _canonicalize_schedule_paragraph_eid_label(label: Optional[str]) -> str:
    """Canonicalize schedule paragraph labels for exact eId lookup.

    UK schedule paragraph ids can surface as lower-case aliases like ``9a`` or
    ``116a`` in affected-target text, while the parsed/oracle eId may retain an
    upper-case alpha suffix such as ``9A`` or ``116A``.

    We keep the normalization narrow: only the first alpha suffix immediately
    following leading digits is upper-cased, leaving any later nested item
    suffixes untouched (for example ``116a-a`` -> ``116A-a``).
    """

    cleaned = _clean_num(label or "")
    if not cleaned:
        return ""
    match = re.fullmatch(r"(\d+)([a-z])(?P<rest>.*)", cleaned)
    if match:
        return f"{match.group(1)}{match.group(2).upper()}{match.group('rest')}"
    return cleaned


def _order_schedule_materialization_ops(ops: list[LegalOperation]) -> list[LegalOperation]:
    """Prioritize materializing structural ops before dependent text edits within a source."""

    def _rank(op: LegalOperation) -> int:
        if _action_name(op.action) in {"insert", "replace", "repeal"}:
            return 0
        if _action_name(op.action) in {"text_replace", "text_repeal"}:
            return 1
        return 2

    return [
        op
        for _idx, op in sorted(
            enumerate(ops),
            key=lambda item: (
                str(getattr(item[1].source, "effective", "") or ""),
                str(getattr(item[1].source, "statute_id", "") or ""),
                _rank(item[1]),
                item[0],
            ),
        )
    ]


def _looks_like_roman_subitem_label(label: str) -> bool:
    cleaned = (label or "").strip().lower()
    return bool(cleaned) and bool(re.fullmatch(r"[ivx]+", cleaned))


def _action_name(action: StructuralAction | str) -> str:
    """Return the canonical lower-case action string for enum or legacy string values."""
    if isinstance(action, StructuralAction):
        return action.value
    return str(action)


def _direct_structural_num(el: ET.Element) -> str:
    """Return the node's own structural number, not a descendant's number."""
    num_el = el.find(f"./{{{_LEG_NS}}}Pnumber")
    if num_el is None:
        num_el = el.find(f"./{{{_LEG_NS}}}Number")
    if num_el is None and _tag(el) == "Schedule":
        num_el = el.find(f".//{{{_LEG_NS}}}Number")
    if num_el is None:
        return ""
    return _text_content(num_el)


def _is_heading_only_ref(ref: str) -> bool:
    ref_clean = ref.strip().lower()
    if "cross-heading" in ref_clean or "cross heading" in ref_clean or "crossheading" in ref_clean:
        return False
    return ref_clean.endswith(" heading") or ref_clean.endswith(" title") or ref_clean.endswith(" sidenote")


def _is_crossheading_ref(ref: str) -> bool:
    ref_clean = str(ref or "").strip().lower()
    return "cross-heading" in ref_clean or "cross heading" in ref_clean or "crossheading" in ref_clean


def _clone_element(el: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(el, encoding="unicode"))


def _retarget_instruction_element_to_target(
    extracted_el: ET.Element,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> Optional[ET.Element]:
    """Retarget instruction paragraphs like '27 Section 100 ...' to the real target.

    Some archive-backed affects extracts hand us the amending schedule paragraph
    itself (`P1`, `P2`, `P3`) rather than a nested replacement node. When the
    direct structural number on that element is the amending paragraph number,
    but the text explicitly introduces a different target provision, using the
    raw element as payload fabricates a subtree rooted at the amendment
    paragraph number. In those cases, clone the element and rewrite its direct
    number to the actual affected target label before parsing it as a payload.
    """
    target_kind = _addr_leaf_kind(target)
    target_label = _addr_leaf_label(target)
    if not target_kind or not target_label or not extracted_text:
        return None

    direct_num = _direct_structural_num(extracted_el)
    if not direct_num or _clean_num(direct_num) == _clean_num(target_label):
        return None

    text = " ".join(extracted_text.split())
    label_rx = re.escape(target_label)
    target_patterns = {
        "section": rf"\bsection\s+{label_rx}\b",
        "article": rf"\barticle\s+{label_rx}\b",
        "rule": rf"\brule\s+{label_rx}\b",
        "subsection": rf"\bsubsection\s*\({label_rx}\)\b",
        "paragraph": rf"\bparagraph\s*\({label_rx}\)\b",
        "subparagraph": rf"\bsub-?paragraph\s*\({label_rx}\)\b",
        "item": rf"\bitem\s*\({label_rx}\)\b",
    }
    pattern = target_patterns.get(target_kind)
    if pattern is None or not re.search(pattern, text, re.I):
        return None

    clone = _clone_element(extracted_el)
    num_el = clone.find(f"./{{{_LEG_NS}}}Pnumber")
    if num_el is None:
        num_el = clone.find(f"./{{{_LEG_NS}}}Number")
    if num_el is None:
        return None
    num_el.text = target_label
    for child in list(num_el):
        child.tail = ""
    return clone


# Note-key constants for structured data encoded in LegalOperation.provenance_tags
_NOTE_FRAGMENT_SUB = "fragment_substitution:"
_NOTE_EFFECT_TYPE = "uk_effect_type:"
_NOTE_ORIGINAL_REF = "original_ref:"
_NOTE_RAW_TEXT = "raw_text:"
_NOTE_PRECEDING_EID = "preceding_eid:"
_NOTE_METADATA_SOURCE_FALLBACK = "metadata_source_fallback:"


def _append_uk_replay_adjudication(
    adjudications_out: Optional[list[CompileAdjudication]],
    *,
    kind: str,
    message: str,
    op: LegalOperation,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Append a UK replay adjudication when a sink list is available."""
    if adjudications_out is None:
        return
    detail_payload: dict[str, Any] = dict(detail or {})
    detail_payload.setdefault("rule_id", str(kind))
    detail_payload.setdefault("phase", "replay")
    if kind == "uk_replay_unsupported_action":
        detail_payload.setdefault("family", "unsupported_or_unresolved_action")
        detail_payload.setdefault("blocking", True)
        detail_payload.setdefault("strict_disposition", "block")
        detail_payload.setdefault("quirks_disposition", "record")
    adjudications_out.append(
        CompileAdjudication(
            kind=str(kind),
            message=message,
            source_statute=op.source.statute_id if op.source else "",
            op_id=op.op_id,
            detail=detail_payload,
        )
    )


def _uk_adjudication_from_finding(finding: Finding) -> CompileAdjudication:
    """Project replay-lint findings into the UK replay compatibility bag."""
    detail = dict(finding.detail)
    message = str(detail.pop("message", "") or "")
    blocking = bool(finding.blocking)
    detail.setdefault("blocking", blocking)
    detail.setdefault("strict_disposition", "block" if blocking else "record")
    detail.setdefault("quirks_disposition", "record")
    return CompileAdjudication(
        kind=str(finding.kind or ""),
        message=message,
        source_statute=str(finding.source_statute or ""),
        detail=detail,
    )


def _to_mutable_node(node: Any) -> UKMutableNode:
    """Convert core payloads or dict-shaped payloads into a UK mutable node."""
    if isinstance(node, UKMutableNode):
        return node
    if isinstance(node, IRNode):
        return UKMutableNode.from_irnode(node)
    if isinstance(node, dict):
        return UKMutableNode.from_dict(node)
    raise TypeError(f"Unsupported payload type for UK mutable conversion: {type(node)!r}")


def _to_irnode(node: Any) -> IRNode:
    """Convert UK-local mutable payloads back into frozen core IR nodes."""
    if isinstance(node, IRNode):
        return node
    if isinstance(node, UKMutableNode):
        return node.to_irnode()
    if isinstance(node, dict):
        return UKMutableNode.from_dict(node).to_irnode()
    raise TypeError(f"Unsupported payload type for frozen IR conversion: {type(node)!r}")


def _lowered_witness_to_payload_data(witness: UKLoweredOperationWitness) -> dict[str, Any]:
    """Project a lowered witness into a JSON-safe payload sidecar."""
    target = witness.target
    effect_witness = witness.effect_witness
    applicability = effect_witness.applicability
    extraction_witness = witness.extraction_witness
    target_expansion_witness = witness.target_expansion_witness
    text_rewrite_witness = witness.text_rewrite_witness
    insertion_anchor_witness = witness.insertion_anchor_witness
    return {
        "op_id": witness.op_id,
        "sequence": witness.sequence,
        "action": witness.action.value,
        "target": {
            "path": [[kind, label] for kind, label in target.path],
            "special": target.special.value if target.special is not None else None,
        },
        "source": {
            "statute_id": witness.source.statute_id,
            "title": witness.source.title,
            "effective": witness.source.effective,
            "raw_text": witness.source.raw_text,
        },
        "effect_witness": {
            "effect_id": effect_witness.effect_id,
            "affected_provisions_raw": effect_witness.affected_provisions_raw,
            "affecting_provisions_raw": effect_witness.affecting_provisions_raw,
            "effect_type_raw": effect_witness.effect_type_raw,
            "comments_raw": effect_witness.comments_raw,
            "authority_layer": effect_witness.authority_layer,
            "applicability": {
                "effective_date": applicability.effective_date,
                "in_force_dates": list(applicability.in_force_dates),
                "requires_applied": applicability.requires_applied,
                "applied": applicability.applied,
                "effect_type_raw": applicability.effect_type_raw,
            },
        },
        "extraction_witness": {
            "effect_id": extraction_witness.effect_id,
            "authority_layer": extraction_witness.authority_layer,
            "extracted_tag": extraction_witness.extracted_tag,
            "extracted_text": extraction_witness.extracted_text,
            "extracted_source_present": extraction_witness.extracted_source_present,
            "metadata_fallback_used": extraction_witness.metadata_fallback_used,
            "extraction_failure_kind": extraction_witness.extraction_failure_kind,
        },
        "target_expansion_witness": {
            "original_ref": target_expansion_witness.original_ref,
            "expanded_refs": list(target_expansion_witness.expanded_refs),
            "expansion_source": target_expansion_witness.expansion_source,
        },
        "text_rewrite_witness": None
        if text_rewrite_witness is None
        else {
            "primary_match": text_rewrite_witness.primary_match,
            "primary_replacement": text_rewrite_witness.primary_replacement,
            "alternatives": [[original, replacement] for original, replacement in text_rewrite_witness.alternatives],
            "occurrence": text_rewrite_witness.occurrence,
            "rewrite_source": text_rewrite_witness.rewrite_source,
        },
        "insertion_anchor_witness": None
        if insertion_anchor_witness is None
        else {
            "preceding_eid": insertion_anchor_witness.preceding_eid,
            "anchor_source": insertion_anchor_witness.anchor_source,
        },
    }


def _lowered_witness_from_payload_data(data: dict[str, Any]) -> UKLoweredOperationWitness:
    """Rehydrate a lowered witness from the JSON-safe payload sidecar."""
    target_data = dict(data.get("target", {}) or {})
    source_data = dict(data.get("source", {}) or {})
    effect_data = dict(data.get("effect_witness", {}) or {})
    applicability_data = dict(effect_data.get("applicability", {}) or {})
    extraction_data = dict(data.get("extraction_witness", {}) or {})
    expansion_data = dict(data.get("target_expansion_witness", {}) or {})
    text_rewrite_data = data.get("text_rewrite_witness")
    anchor_data = data.get("insertion_anchor_witness")
    target_path = tuple(
        (str(kind), str(label))
        for kind, label in (target_data.get("path", []) or [])
        if str(kind)
    )
    special = target_data.get("special")
    return UKLoweredOperationWitness(
        op_id=str(data.get("op_id", "") or ""),
        sequence=int(data.get("sequence", 0) or 0),
        action=StructuralAction(str(data.get("action", StructuralAction.REPLACE.value) or StructuralAction.REPLACE.value)),
        target=LegalAddress(
            path=target_path,
            special=FacetKind(special) if special else None,
        ),
        payload=None,
        source=OperationSource(
            statute_id=str(source_data.get("statute_id", "") or ""),
            title=str(source_data.get("title", "") or ""),
            effective=str(source_data.get("effective", "") or ""),
            raw_text=str(source_data.get("raw_text", "") or ""),
        ),
        effect_witness=UKEffectWitness(
            effect_id=str(effect_data.get("effect_id", "") or ""),
            affected_provisions_raw=str(effect_data.get("affected_provisions_raw", "") or ""),
            affecting_provisions_raw=str(effect_data.get("affecting_provisions_raw", "") or ""),
            effect_type_raw=str(effect_data.get("effect_type_raw", "") or ""),
            comments_raw=str(effect_data.get("comments_raw", "") or ""),
            authority_layer=str(effect_data.get("authority_layer", "") or ""),
            applicability=UKApplicabilityWitness(
                effective_date=applicability_data.get("effective_date"),
                in_force_dates=tuple(str(item) for item in (applicability_data.get("in_force_dates", []) or [])),
                requires_applied=bool(applicability_data.get("requires_applied", False)),
                applied=bool(applicability_data.get("applied", False)),
                effect_type_raw=str(applicability_data.get("effect_type_raw", "") or ""),
            ),
        ),
        extraction_witness=UKProvisionExtractionWitness(
            effect_id=str(extraction_data.get("effect_id", "") or ""),
            authority_layer=str(extraction_data.get("authority_layer", "") or ""),
            extracted_tag=extraction_data.get("extracted_tag"),
            extracted_text=str(extraction_data.get("extracted_text", "") or ""),
            extracted_source_present=bool(extraction_data.get("extracted_source_present", False)),
            metadata_fallback_used=bool(extraction_data.get("metadata_fallback_used", False)),
            extraction_failure_kind=extraction_data.get("extraction_failure_kind"),
        ),
        target_expansion_witness=UKTargetExpansionWitness(
            original_ref=str(expansion_data.get("original_ref", "") or ""),
            expanded_refs=tuple(str(item) for item in (expansion_data.get("expanded_refs", []) or [])),
            expansion_source=str(expansion_data.get("expansion_source", "") or ""),
        ),
        text_rewrite_witness=None
        if text_rewrite_data is None
        else UKTextRewriteSpec(
            primary_match=text_rewrite_data.get("primary_match"),
            primary_replacement=text_rewrite_data.get("primary_replacement"),
            alternatives=tuple(
                (str(original), str(replacement))
                for original, replacement in (text_rewrite_data.get("alternatives", []) or [])
            ),
            occurrence=int(text_rewrite_data.get("occurrence", 0) or 0),
            rewrite_source=str(text_rewrite_data.get("rewrite_source", "") or ""),
        ),
        insertion_anchor_witness=None
        if anchor_data is None
        else UKInsertionAnchorWitness(
            preceding_eid=anchor_data.get("preceding_eid"),
            anchor_source=str(anchor_data.get("anchor_source", "") or ""),
        ),
    )


def _witness_for_op(op: LegalOperation) -> object | None:
    """Return the preferred witness payload for UK replay helpers.

    Prefer the typed payload-sidecar witness when present so sidecar-backed
    lanes can migrate away from the shared source witness carrier. Payload-
    less legacy ops now return ``None`` here.
    """
    payload = getattr(op, "payload", None)
    payload_attrs = getattr(payload, "attrs", None)
    if isinstance(payload_attrs, dict):
        witness = payload_attrs.get("rewrite_witness")
        if isinstance(witness, dict) and {"effect_witness", "extraction_witness", "target_expansion_witness"} <= set(witness):
            return _lowered_witness_from_payload_data(witness)
        if witness is not None:
            return witness
    return None


def _uk_temporal_group_id(effect: UKEffectRecord) -> str:
    """Return the stable temporal group key for one UK effect."""
    return effect.effect_id


def _uk_temporal_events_from_ops(
    ops: Sequence[LegalOperation],
    *,
    target_statute: str,
) -> tuple[TemporalEvent, ...]:
    """Project replay ops into explicit temporal authority for timeline mode.

    The UK replay path still reads source dates when no temporal events are
    present, but timeline mode should already carry explicit executable
    temporal authority so the core bridge can eventually be retired without
    changing the matcher again.
    """
    from lawvm.core.compile_result import TemporalEvent, TemporalScope  # noqa: PLC0415
    from lawvm.core.temporal import FIXED_DATE_KIND  # noqa: PLC0415
    from lawvm.core.compile_result import ActivationRule  # noqa: PLC0415

    events: list[TemporalEvent] = []
    seen_group_ids: set[str] = set()
    for op in ops:
        group_id = str(getattr(op, "group_id", "") or "")
        if not group_id or group_id in seen_group_ids:
            continue
        seen_group_ids.add(group_id)
        source = getattr(op, "source", None)
        if source is None:
            continue
        effective_from = str(getattr(source, "effective", "") or getattr(source, "enacted", "") or "")
        if not effective_from:
            continue
        events.append(
            TemporalEvent(
                event_id=f"uk-temporal:{group_id}",
                group_id=group_id,
                kind="commence",
                scope=TemporalScope(target_statute=target_statute),
                effective=effective_from,
                source=source,
                activation_rule=ActivationRule(
                    kind=FIXED_DATE_KIND,
                    effective_date=effective_from,
                    raw_text=str(getattr(source, "raw_text", "") or ""),
                ),
            )
        )
    return tuple(events)


def _payload_with_rewrite_witness(
    payload: Optional[IRNode],
    witness: UKLoweredOperationWitness,
) -> Optional[IRNode]:
    """Attach a sidecar witness to a payload node without creating a cycle."""
    if payload is None:
        return None
    payload_witness = _lowered_witness_to_payload_data(dc_replace(witness, payload=None))
    return dc_replace(payload, attrs={**dict(payload.attrs), "rewrite_witness": payload_witness})


def _fragment_substitution(op: LegalOperation) -> Optional[list]:
    """Return typed fragment-substitution data from the lowered witness."""
    witness = _witness_for_op(op)
    text_rewrite_witness = getattr(witness, "text_rewrite_witness", None)
    if text_rewrite_witness is not None and getattr(text_rewrite_witness, "alternatives", None):
        return [
            {"original": original, "replacement": replacement}
            for original, replacement in text_rewrite_witness.alternatives
            if original
        ]
    for note in getattr(op, "provenance_tags", ()) or ():
        if not str(note).startswith(_NOTE_FRAGMENT_SUB):
            continue
        try:
            payload = json.loads(str(note)[len(_NOTE_FRAGMENT_SUB) :])
        except Exception:
            return None
        if isinstance(payload, list):
            return [
                {
                    "original": str(item.get("original") or ""),
                    "replacement": str(item.get("replacement") or ""),
                }
                for item in payload
                if isinstance(item, dict) and str(item.get("original") or "")
            ]
    return None


def _uk_op_allowed_by_authority_mode(op: LegalOperation, authority_mode: str) -> tuple[bool, Optional[str]]:
    if authority_mode != "source_text_only":
        return True, None
    witness = _witness_for_op(op)
    extraction_witness = getattr(witness, "extraction_witness", None)
    target_expansion_witness = getattr(witness, "target_expansion_witness", None)
    if str(getattr(extraction_witness, "authority_layer", "") or "") != "AFFECTING_ACT_TEXT":
        return False, "extraction_authority"
    if str(getattr(target_expansion_witness, "expansion_source", "") or "") == "metadata_split":
        return False, "metadata_target_expansion"
    return True, None


def _preceding_eid(op: LegalOperation) -> Optional[str]:
    witness = _witness_for_op(op)
    insertion_anchor_witness = getattr(witness, "insertion_anchor_witness", None)
    if insertion_anchor_witness is not None and insertion_anchor_witness.preceding_eid:
        return insertion_anchor_witness.preceding_eid
    return None


def _uk_applicability_witness(effect: UKEffectRecord) -> UKApplicabilityWitness:
    return UKApplicabilityWitness(
        effective_date=effect.effective_date,
        in_force_dates=tuple(
            str(item.get("date") or "") for item in (effect.in_force_dates or []) if str(item.get("date") or "")
        ),
        requires_applied=bool(effect.requires_applied),
        applied=bool(effect.applied),
        effect_type_raw=effect.effect_type,
    )


def _uk_effect_witness(effect: UKEffectRecord, *, authority_layer: str) -> UKEffectWitness:
    return UKEffectWitness(
        effect_id=effect.effect_id,
        affected_provisions_raw=effect.affected_provisions,
        affecting_provisions_raw=effect.affecting_provisions,
        effect_type_raw=effect.effect_type,
        comments_raw=effect.comments,
        authority_layer=authority_layer,
        applicability=_uk_applicability_witness(effect),
    )


def _uk_extraction_witness(
    effect: UKEffectRecord,
    *,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    metadata_fallback_used: bool,
) -> UKProvisionExtractionWitness:
    extracted_source_present = extracted_el is not None
    if extracted_source_present:
        authority_layer = "AFFECTING_ACT_TEXT"
        extraction_failure_kind = None
    elif metadata_fallback_used:
        authority_layer = "CURRENT_XML_METADATA_BACKFILL"
        extraction_failure_kind = "missing_extracted_source"
    else:
        authority_layer = "EFFECT_FEED_INDEX"
        extraction_failure_kind = "missing_extracted_source"
    return UKProvisionExtractionWitness(
        effect_id=effect.effect_id,
        authority_layer=authority_layer,
        extracted_tag=_tag(extracted_el) if extracted_el is not None else None,
        extracted_text=extracted_text or "",
        extracted_source_present=extracted_source_present,
        metadata_fallback_used=metadata_fallback_used,
        extraction_failure_kind=extraction_failure_kind,
    )


def _uk_target_expansion_witness(
    original_ref: str,
    expanded_refs: list[str] | tuple[str, ...],
    *,
    original_targets_str: list[str] | tuple[str, ...] | None = None,
) -> UKTargetExpansionWitness:
    expanded_refs_list = list(expanded_refs)
    original_targets_list = list(original_targets_str) if original_targets_str is not None else expanded_refs_list
    if expanded_refs_list == [original_ref]:
        expansion_source = "none"
    elif expanded_refs_list == original_targets_list:
        expansion_source = "metadata_split"
    else:
        expansion_source = "extracted_or_text_expansion"
    return UKTargetExpansionWitness(
        original_ref=original_ref,
        expanded_refs=tuple(expanded_refs_list),
        expansion_source=expansion_source,
    )


def _uk_text_rewrite_spec(
    *,
    fragment_subs: Optional[list],
    text_patch: Optional[TextPatchSpec],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
) -> Optional[UKTextRewriteSpec]:
    if fragment_subs:
        primary = fragment_subs[0]
        alternatives = tuple(
            (str(item.get("original") or ""), str(item.get("replacement") or ""))
            for item in fragment_subs
            if str(item.get("original") or "")
        )
        return UKTextRewriteSpec(
            primary_match=str(primary.get("original") or "") or None,
            primary_replacement=str(primary.get("replacement") or ""),
            alternatives=alternatives,
            occurrence=op_text_occurrence,
            rewrite_source="fragment_substitution",
        )
    if text_patch is not None:
        primary_match = text_patch.selector.match_text
        primary_replacement = text_patch.replacement or ""
        if text_patch.kind == "delete":
            primary_replacement = ""
        return UKTextRewriteSpec(
            primary_match=primary_match,
            primary_replacement=primary_replacement,
            alternatives=((primary_match, primary_replacement),),
            occurrence=text_patch.selector.occurrence,
            rewrite_source="typed_text_patch",
        )
    if op_text_match is not None:
        return UKTextRewriteSpec(
            primary_match=op_text_match,
            primary_replacement=op_text_replacement,
            alternatives=((op_text_match, op_text_replacement or ""),),
            occurrence=op_text_occurrence,
            rewrite_source="regex_omission_fallback",
        )
    return None


def _uk_insertion_anchor_witness(preceding_eid: Optional[str]) -> Optional[UKInsertionAnchorWitness]:
    if not preceding_eid:
        return None
    return UKInsertionAnchorWitness(
        preceding_eid=preceding_eid,
        anchor_source="effect_comments_after_clause",
    )


def _uk_lowered_op_provenance_tags(witness: UKLoweredOperationWitness) -> tuple[str, ...]:
    import json as _json

    provenance_tags: list[str] = [
        f"{_NOTE_EFFECT_TYPE}{witness.effect_witness.effect_type_raw}",
        f"{_NOTE_ORIGINAL_REF}{witness.target_expansion_witness.original_ref}",
    ]
    if witness.extraction_witness.extracted_text:
        provenance_tags.append(f"{_NOTE_RAW_TEXT}{witness.extraction_witness.extracted_text}")
    if witness.text_rewrite_witness is not None and witness.text_rewrite_witness.alternatives:
        fragment_sub_payload = [
            {"original": original, "replacement": replacement}
            for original, replacement in witness.text_rewrite_witness.alternatives
        ]
        provenance_tags.append(f"{_NOTE_FRAGMENT_SUB}{_json.dumps(fragment_sub_payload, ensure_ascii=False)}")
    if witness.insertion_anchor_witness is not None and witness.insertion_anchor_witness.preceding_eid:
        provenance_tags.append(f"{_NOTE_PRECEDING_EID}{witness.insertion_anchor_witness.preceding_eid}")
    if witness.extraction_witness.metadata_fallback_used:
        provenance_tags.append(f"{_NOTE_METADATA_SOURCE_FALLBACK}{witness.effect_witness.effect_id}")
    return tuple(provenance_tags)


from lawvm.uk_legislation.uk_grafter import (
    _parse_part,
    _parse_chapter,
    _parse_section,
    _parse_p1group,
    _parse_p2,
    _parse_p3,
    _parse_p4,
    _clean_num,
    _semantic_hash,
    _LEG_NS,
    _extract_num,
    _parse_pblock,
    _parse_schedule_single,
)

from lawvm.uk_legislation.nlp_parser import is_whole_node_replacement, parse_fragment_substitution
from lawvm.uk_legislation.witnesses import (
    UKApplicabilityWitness,
    UKEffectWitness,
    UKInsertionAnchorWitness,
    UKLoweredOperationWitness,
    UKProvisionExtractionWitness,
    UKTargetExpansionWitness,
    UKTextRewriteSpec,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LEG_BASE = "https://www.legislation.gov.uk"
_USER_AGENT = "LawVM UK replay/0.1 (+https://github.com/lawvm)"

# Effect types that directly imply textual changes we can extract
STRUCTURAL_EFFECT_TYPES = frozenset(
    {
        "inserted",
        "words inserted",
        "word inserted",
        "words substituted",
        "substituted for words",
        "word substituted",
        "substituted",
        "words repealed",
        "word repealed",
        "repealed",
        "repealed in part",
        "words omitted",
        "word omitted",
        "omitted",
    }
)


def _label_sort_key(label: Optional[str]) -> tuple[Any, ...]:
    """Return a deterministic natural sort key for UK structural labels."""
    clean = _clean_num(label or "")
    if not clean:
        # Bare structural labels such as "CHAPTER" or "PART" should sort
        # before numbered siblings like "CHAPTER 3" or "PART 6".  The replay
        # insertion path uses this key to preserve child order when inserting a
        # heading-only container under an existing parent that already has
        # numbered siblings.
        return ((-1, ""),)
    parts = re.findall(r"\d+|[a-z]+", clean)
    key: list[Any] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


# ---------------------------------------------------------------------------


def _to_structural_action(action: str) -> StructuralAction:
    """Map string action to StructuralAction, preserving text-level variants."""
    if action == "replace":
        return StructuralAction.REPLACE
    if action == "text_replace":
        return StructuralAction.TEXT_REPLACE
    if action == "repeal":
        return StructuralAction.REPEAL
    if action == "text_repeal":
        return StructuralAction.TEXT_REPEAL
    if action == "insert":
        return StructuralAction.INSERT
    if action == "renumber":
        return StructuralAction.RENUMBER
    # Fallback for unknown actions - should not happen in normal operation
    return StructuralAction.META


# Data classes
# ---------------------------------------------------------------------------


@dataclass
class UKEffectRecord:
    """A single structured effect entry from the effects feed."""

    effect_id: str
    effect_type: str
    applied: bool
    requires_applied: bool
    modified: str  # ISO date of last editorial modification

    # Affected (the statute being changed)
    affected_uri: str
    affected_class: str
    affected_year: str
    affected_number: str
    affected_provisions: str  # e.g. "s. 21", "Sch. 1"

    # Affecting (the act making the change)
    affecting_uri: str
    affecting_class: str
    affecting_year: str
    affecting_number: str
    affecting_provisions: str  # e.g. "Sch. 2 para. 2(2)"
    affecting_title: str

    in_force_dates: list[dict[str, Any]] = field(default_factory=list)
    metadata_only: bool = False  # True if this effect was only found in XML metadata, not the Atom feed
    comments: str = ""

    @property
    def affecting_act_id(self) -> str:
        """Canonical web path for the affecting act, e.g. 'ukpga/2023/28'."""
        cls = self.affecting_class
        # Map class name to URL segment
        cls_map = {
            "UnitedKingdomPublicGeneralAct": "ukpga",
            "UnitedKingdomStatutoryInstrument": "uksi",
            "WelshParliamentAct": "asc",
            "WelshStatutoryInstrument": "wsi",
            "ScottishAct": "asp",
            "ScottishStatutoryInstrument": "ssi",
            "NorthernIrelandAssemblyMeasure": "mnia",
            "NorthernIrelandParliamentAct": "apni",
            "NorthernIrelandStatutoryRule": "nisr",
            "UnitedKingdomChurchInstrument": "ukci",
            "UnitedKingdomMinisterialOrder": "ukmo",
            "EuropeanUnionRegulation": "eur",
            "EuropeanUnionDecision": "eudn",
            "EuropeanUnionDirective": "eudr",
        }
        slug = cls_map.get(cls, cls.lower())
        return f"{slug}/{self.affecting_year}/{self.affecting_number}"

    @property
    def effective_date(self) -> str:
        """Return the best non-empty, non-prospective in-force date, or '' if none.

        The effects feed sometimes has a first InForce entry with an empty date
        and prospective=true (the 'applied' marker) followed by the real effective
        date in a later entry.  Using in_force_dates[0]["date"] blindly sorts
        those effects to position 0 (before all real dates) and applies them
        unconditionally.  This helper returns the first real (non-prospective,
        non-empty) date, falling back to any non-empty date, then ''.
        """
        real: str = ""
        any_date: str = ""
        for d in self.in_force_dates:
            dt = d.get("date", "")
            if not dt:
                continue
            if not any_date:
                any_date = dt
            if d.get("prospective", "false").lower() != "true":
                real = dt
                break
        return real or any_date

    @property
    def is_structural(self) -> bool:
        # If it's from metadata, we likely want it for the current PIT state reconstruction
        # even if the Atom feed hasn't 'applied' it yet.
        return (self.applied or self.metadata_only) and (
            self.effect_type in STRUCTURAL_EFFECT_TYPES or self.effect_type == ""
        )

    def is_applicable_for_replay(
        self,
        *,
        applicability_mode: str = "effective_date_plus_feed_applied",
    ) -> bool:
        mode = str(applicability_mode or "effective_date_plus_feed_applied")
        if mode == "effective_date_only":
            return True
        if mode == "effective_date_plus_requires_applied":
            return bool(self.applied) or not bool(self.requires_applied) or bool(self.metadata_only)
        return bool(self.applied) or bool(self.metadata_only)

    def is_structural_for_replay(
        self,
        *,
        applicability_mode: str = "effective_date_plus_feed_applied",
    ) -> bool:
        if self.effect_type not in STRUCTURAL_EFFECT_TYPES and self.effect_type != "":
            return False
        return self.is_applicable_for_replay(applicability_mode=applicability_mode)

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "effect_type": self.effect_type,
            "applied": self.applied,
            "affected_provisions": self.affected_provisions,
            "affecting_act_id": self.affecting_act_id,
            "affecting_provisions": self.affecting_provisions,
            "affecting_title": self.affecting_title,
            "modified": self.modified,
            "in_force_date": self.in_force_dates[0]["date"] if self.in_force_dates else "",
        }


# ---------------------------------------------------------------------------
# Effects Parser
# ---------------------------------------------------------------------------


def parse_effects_from_feeds(feed_files: list[Path]) -> list[UKEffectRecord]:
    """Parse all effect feed pages into a list of UKEffectRecord."""
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
    }
    records = []
    for ff in feed_files:
        root = ET.parse(ff).getroot()
        for entry in root.findall("atom:entry", ns):
            effect = entry.find(".//ukm:Effect", ns)
            if effect is None:
                continue
            # Parse in-force dates
            in_force_dates = []
            for inf in effect.findall(".//ukm:InForceDates/ukm:InForce", ns):
                in_force_dates.append(
                    {
                        "date": inf.get("Date", ""),
                        "applied": inf.get("Applied", ""),
                        "prospective": inf.get("Prospective", "false"),
                    }
                )
            rec = UKEffectRecord(
                effect_id=effect.get("EffectId", ""),
                effect_type=effect.get("Type", ""),
                applied=(effect.get("Applied", "false").lower() == "true"),
                requires_applied=(effect.get("RequiresApplied", "false").lower() == "true"),
                modified=effect.get("Modified", "")[:10],
                affected_uri=effect.get("AffectedURI", ""),
                affected_class=effect.get("AffectedClass", ""),
                affected_year=effect.get("AffectedYear", ""),
                affected_number=effect.get("AffectedNumber", ""),
                affected_provisions=effect.get("AffectedProvisions", ""),
                affecting_uri=effect.get("AffectingURI", ""),
                affecting_class=effect.get("AffectingClass", ""),
                affecting_year=effect.get("AffectingYear", ""),
                affecting_number=effect.get("AffectingNumber", ""),
                affecting_provisions=effect.get("AffectingProvisions", ""),
                affecting_title=effect.findtext("ukm:AffectingTitle", default="", namespaces=ns),
                in_force_dates=in_force_dates,
            )
            records.append(rec)
    return records


def parse_effects_from_bytes(
    feed_bytes_list: list[bytes],
    *,
    parse_rejections_out: Optional[list[dict[str, Any]]] = None,
    feed_locators: Optional[list[str]] = None,
) -> list[UKEffectRecord]:
    """Parse effect feed pages from raw bytes into a list of UKEffectRecord.

    Archive-backed alternative to parse_effects_from_feeds() — accepts bytes
    directly so no temp files are needed.
    """
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
    }
    records = []
    for feed_index, raw in enumerate(feed_bytes_list):
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            if parse_rejections_out is not None:
                rejection: dict[str, Any] = {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                    "family": "source_pathology",
                    "phase": "parse",
                    "feed_index": feed_index,
                    "reason": "UK effect feed page is not well-formed XML.",
                    "parse_error": str(exc),
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
                if feed_locators is not None and feed_index < len(feed_locators):
                    rejection["feed_locator"] = feed_locators[feed_index]
                parse_rejections_out.append(rejection)
            continue
        for entry_index, entry in enumerate(root.findall("atom:entry", ns)):
            effect = entry.find(".//ukm:Effect", ns)
            if effect is None:
                if parse_rejections_out is not None:
                    rejection = {
                        "rule_id": "uk_effect_feed_entry_missing_effect_rejected",
                        "family": "source_pathology",
                        "phase": "parse",
                        "feed_index": feed_index,
                        "entry_index": entry_index,
                        "entry_id": entry.findtext("atom:id", default="", namespaces=ns),
                        "entry_title": entry.findtext("atom:title", default="", namespaces=ns),
                        "reason": "UK effect feed entry did not contain a ukm:Effect payload.",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    }
                    if feed_locators is not None and feed_index < len(feed_locators):
                        rejection["feed_locator"] = feed_locators[feed_index]
                    parse_rejections_out.append(rejection)
                continue
            in_force_dates = []
            for inf in effect.findall(".//ukm:InForceDates/ukm:InForce", ns):
                in_force_dates.append(
                    {
                        "date": inf.get("Date", ""),
                        "applied": inf.get("Applied", ""),
                        "prospective": inf.get("Prospective", "false"),
                    }
                )
            rec = UKEffectRecord(
                effect_id=effect.get("EffectId", ""),
                effect_type=effect.get("Type", ""),
                applied=(effect.get("Applied", "false").lower() == "true"),
                requires_applied=(effect.get("RequiresApplied", "false").lower() == "true"),
                modified=effect.get("Modified", "")[:10],
                affected_uri=effect.get("AffectedURI", ""),
                affected_class=effect.get("AffectedClass", ""),
                affected_year=effect.get("AffectedYear", ""),
                affected_number=effect.get("AffectedNumber", ""),
                affected_provisions=effect.get("AffectedProvisions", ""),
                affecting_uri=effect.get("AffectingURI", ""),
                affecting_class=effect.get("AffectingClass", ""),
                affecting_year=effect.get("AffectingYear", ""),
                affecting_number=effect.get("AffectingNumber", ""),
                affecting_provisions=effect.get("AffectingProvisions", ""),
                affecting_title=effect.findtext("ukm:AffectingTitle", default="", namespaces=ns),
                in_force_dates=in_force_dates,
            )
            records.append(rec)
    return records


def load_effects_for_statute_from_archive(
    statute_id: str,
    archive: Any,
    *,
    parse_rejections_out: Optional[list[dict[str, Any]]] = None,
) -> list[UKEffectRecord]:
    """Load effects for a statute from a Farchive.

    Queries the archive for all effects feed pages matching the statute's
    /changes/affected/{statute_id}/data.feed URL pattern, fetches their
    bytes, and parses them via parse_effects_from_bytes().

    Returns an empty list if no feed pages are found in the archive.
    """
    pattern = f"%/changes/affected/{statute_id}/%"
    rows = archive._conn.execute(
        "SELECT DISTINCT locator FROM locator_span WHERE locator LIKE ?",
        (pattern,),
    ).fetchall()

    feed_bytes_list: list[bytes] = []
    feed_locators: list[str] = []
    for (url,) in rows:
        data = archive.get(url)
        if data:
            feed_bytes_list.append(data)
            feed_locators.append(url)
            continue
        if parse_rejections_out is not None:
            parse_rejections_out.append(
                {
                    "rule_id": "uk_effect_feed_locator_payload_missing_rejected",
                    "family": "source_pathology",
                    "phase": "acquisition",
                    "statute_id": statute_id,
                    "feed_locator": url,
                    "reason": "UK effect feed locator was indexed but payload bytes were missing from the archive.",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
            )

    return parse_effects_from_bytes(
        feed_bytes_list,
        parse_rejections_out=parse_rejections_out,
        feed_locators=feed_locators,
    )


def get_affecting_act_xml_from_archive(
    act_id: str,
    archive: Any,
) -> Optional[bytes]:
    """Fetch affecting act XML bytes from archive.

    act_id is the web-path form, e.g. 'ukpga/2023/28' or 'uksi/2023/723'.
    Returns None if not in the archive.
    """
    url = f"{_LEG_BASE}/{act_id}/data.xml"
    return archive.get(url)


def parse_effects_from_metadata(xml_path: Path) -> list[UKEffectRecord]:
    """Parse effects from the <ukm:UnappliedEffects> section of a legislation XML file."""
    records = []
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return []

    # Use lxml xpath if possible or simple findall
    for effect in root.findall(".//{*}UnappliedEffect"):
        # Parse in-force dates
        in_force_dates = []
        for inf in effect.findall(".//{*}InForce"):
            in_force_dates.append(
                {
                    "date": inf.get("Date", ""),
                    "applied": inf.get("Applied", "false").lower() == "true",
                    "prospective": inf.get("Prospective", "false").lower() == "true",
                }
            )

        # Extract affected provisions as a string (simplified for now)
        prov_parts = []
        for prov in effect.findall(".//{*}AffectedProvisions/{*}Section"):
            prov_parts.append(prov.get("Ref", ""))
        prov_str = ", ".join(prov_parts)

        rec = UKEffectRecord(
            effect_id=effect.get("EffectId", ""),
            effect_type=effect.get("Type", ""),
            applied=(effect.get("Applied", "false").lower() == "true"),
            requires_applied=(effect.get("RequiresApplied", "false").lower() == "true"),
            modified=effect.get("Modified", "")[:10],
            affected_uri=effect.get("AffectedURI", ""),
            affected_class=effect.get("AffectedClass", ""),
            affected_year=effect.get("AffectedYear", ""),
            affected_number=effect.get("AffectedNumber", ""),
            affected_provisions=prov_str,
            affecting_uri=effect.get("AffectingURI", ""),
            affecting_class=effect.get("AffectingClass", ""),
            affecting_year=effect.get("AffectingYear", ""),
            affecting_number=effect.get("AffectingNumber", ""),
            affecting_provisions="",  # Often missing in metadata summary
            affecting_title=effect.findtext("{*}AffectingTitle") or "",
            in_force_dates=in_force_dates,
            metadata_only=True,
            comments=effect.get("Comments", ""),
        )
        records.append(rec)
    return records


def fetch_effects_for_statute(statute_id: str, dest_dir: Path) -> int:
    """Fetch all pages of an effects feed for a given statute."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = dest_dir / "pages"
    pages_dir.mkdir(exist_ok=True)

    base_url = f"{_LEG_BASE}/changes/affected/{statute_id}/data.feed?results-count=50&sort=modified"
    p1_file = dest_dir / "data.feed"

    print(f"Fetching page 1: {base_url}")
    _download_file(base_url, p1_file)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "leg": "http://www.legislation.gov.uk/namespaces/legislation",
    }
    try:
        root = ET.parse(p1_file).getroot()
    except ET.ParseError as e:
        print(f"Warning: could not parse effects feed XML: {e}")
        return 1
    try:
        total_pages_el = root.find(".//leg:totalPages", ns)
        if total_pages_el is None or not total_pages_el.text:
            return 1
        total_pages = int(total_pages_el.text)
    except (ValueError, TypeError) as e:
        print(f"Warning: could not parse total pages: {e}")
        return 1

    if total_pages <= 1:
        return 1

    print(f"Found {total_pages} pages in total.")
    for p in range(2, total_pages + 1):
        url = f"{base_url}&page={p}"
        dest = pages_dir / f"page-{p}.feed"
        print(f"Fetching page {p}/{total_pages}: {url}")
        _download_file(url, dest)

    return total_pages


def fetch_metadata_for_statute(statute_id: str, dest_file: Path):
    """Fetch the 'current' XML for a statute to acquire its UnappliedEffects metadata."""
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    url = f"{_LEG_BASE}/{statute_id}/data.xml"
    print(f"Fetching metadata XML: {url}")
    _download_file(url, dest_file)


def load_effects_for_statute(statute_id: str, base_dir: Path) -> list[UKEffectRecord]:
    """Load effects from both Atom feed and XML metadata, then merge them."""
    stat_dir = base_dir / statute_id
    pages_dir = stat_dir / "pages"

    feed_files = list(pages_dir.glob("*.feed"))
    if (stat_dir / "data.feed").exists():
        feed_files.append(stat_dir / "data.feed")

    atom_effects = parse_effects_from_feeds(feed_files)

    meta_file = stat_dir / "metadata.xml"
    if not meta_file.exists():
        alt_meta = stat_dir / "current" / "data.xml"
        if alt_meta.exists():
            meta_file = alt_meta

    meta_effects = []
    if meta_file.exists():
        meta_effects = parse_effects_from_metadata(meta_file)

    seen_ids = {e.effect_id for e in atom_effects if e.effect_id}
    merged = list(atom_effects)

    backfilled = 0
    for me in meta_effects:
        if me.effect_id not in seen_ids:
            merged.append(me)
            backfilled += 1

    if backfilled > 0:
        print(f"Backfilled {backfilled} effects from XML metadata for {statute_id}.")

    return merged


def _download_file(url: str, dest: Path):
    """Helper to download a file with User-Agent header."""
    req = Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    try:
        with urlopen(req) as response:
            with open(dest, "wb") as f:
                f.write(response.read())
    except HTTPError as e:
        print(f"HTTP Error {e.code}: {url}")
        raise
    except URLError as e:
        print(f"URL Error: {e.reason}")
        raise


def load_effects_for_statute_from_raw(raw_dir: Path) -> list[UKEffectRecord]:
    """Load all effects for a statute from its effects data directory."""
    feed_files = [raw_dir / "data.feed"]
    pages_dir = raw_dir / "pages"
    if pages_dir.exists():
        feed_files += sorted(pages_dir.glob("*.feed"))
    return parse_effects_from_feeds([f for f in feed_files if f.exists()])


# ---------------------------------------------------------------------------
# Acquisition Manifest Builder
# ---------------------------------------------------------------------------


def build_acquisition_manifest(
    effects: list[UKEffectRecord],
    repo_root: Path,
) -> dict[str, Any]:
    """Build a JSON manifest of affecting act URLs to fetch for replay."""
    structural = [e for e in effects if e.is_structural]

    acts_seen: dict[str, dict[str, Any]] = {}
    for e in structural:
        act_id = e.affecting_act_id
        if act_id not in acts_seen:
            acts_seen[act_id] = {
                "act_id": act_id,
                "class": e.affecting_class,
                "year": e.affecting_year,
                "number": e.affecting_number,
                "title": e.affecting_title,
                "effect_count": 0,
                "effects": [],
            }
        acts_seen[act_id]["effect_count"] += 1
        acts_seen[act_id]["effects"].append(e.to_dict())

    sources = []
    for act_id, info in sorted(acts_seen.items()):
        rel_path = f"uk/data/raw/affecting_acts/{act_id.replace('/', '_')}/data.xml"
        dest = repo_root / rel_path
        url = f"{_LEG_BASE}/{act_id}/data.xml"
        sources.append(
            {
                "label": info["title"] or act_id,
                "act_id": act_id,
                "effect_count": info["effect_count"],
                "effects": info["effects"],
                "artifacts": [
                    {
                        "url": url,
                        "path": rel_path,
                    }
                ],
                "already_fetched": dest.exists(),
            }
        )

    manifest = {
        "kind": "uk_affecting_acts_manifest",
        "total_structural_effects": len(structural),
        "affecting_acts": len(sources),
        "sources": [s for s in sources if not s["already_fetched"]],
        "_all_sources": sources,
    }
    return manifest


# ---------------------------------------------------------------------------
# Provision Text Extractor
# ---------------------------------------------------------------------------


def _tag(el: ET.Element) -> str:
    t = el.tag
    return t.split("}", 1)[1] if "}" in t else t


def get_all_eids(nodes: Sequence[IRNode]) -> list[str]:
    """Recursively gather all eIds from an IR tree fragment."""
    eids = []
    for n in nodes:
        eid = n.attrs.get("id") or n.attrs.get("eId")
        if eid:
            eids.append(eid)
        if n.children:
            eids.extend(get_all_eids(n.children))
    return eids


def _text_content(el: ET.Element) -> str:
    """Recursively collect normalised text."""
    parts: list[str] = []
    for node in el.iter():
        if node.text:
            parts.append(node.text)
        if node.tail and node is not el:
            parts.append(node.tail)
    return " ".join(" ".join(parts).split())


def _direct_payload_text(el: ET.Element) -> str:
    """Collect direct/local text for extracted payload compilation only."""
    structural_tags = {
        "part",
        "chapter",
        "euchapter",
        "p1group",
        "section",
        "p1",
        "article",
        "eusection",
        "conventionrights",
        "pblock",
        "p2",
        "p3",
        "p4",
        "subsection",
        "paragraph",
        "schedule",
    }
    transparent_tags = {
        "pnumber",
        "number",
        "title",
        "commentaryref",
        "blockamendment",
        "inlineamendment",
    }
    editorial_tags = {"commentary", "citation", "citationsubref"}

    def _collect_local(node: ET.Element) -> list[str]:
        parts: list[str] = []
        if node.text:
            parts.append(node.text)
        for child in node:
            ct = _tag(child).lower()
            if ct in editorial_tags:
                pass
            elif ct in structural_tags or ct in transparent_tags:
                pass
            else:
                parts.extend(_collect_local(child))
            if child.tail:
                parts.append(child.tail)
        return parts

    return " ".join(" ".join(_collect_local(el)).split())


def _normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching (squash whitespace)."""
    if not text:
        return ""
    return " ".join(text.replace("\u00a0", " ").split()).lower()


def _norm_prov_ref(ref: str) -> str:
    """Normalise a provision reference for comparison."""
    return "".join(re.findall(r"[0-9a-zA-Z]", ref)).lower()


_NUM_ALPHA_RE = re.compile(r"(\d+)([a-z]+)", flags=re.I)
_DIGITS_RE = re.compile(r"^\d+$")
_ALPHA_RE = re.compile(r"^[a-z]+$")
_REF_SPLIT_RE = re.compile(r"[\s.()]+")
_EID_SPLIT_RE = re.compile(r"[-_]")


@lru_cache(maxsize=131072)
def _sequence_tokens_cached(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize ID/reference parts while preserving token boundaries."""
    kinds = {
        "schedule",
        "part",
        "chapter",
        "section",
        "paragraph",
        "subparagraph",
        "p1",
        "p2",
        "p3",
        "pblock",
        "wrapper",
        "article",
        "rule",
    }
    roman_map = {
        "i": "1",
        "ii": "2",
        "iii": "3",
        "iv": "4",
        "v": "5",
        "vi": "6",
        "vii": "7",
        "viii": "8",
        "ix": "9",
        "x": "10",
    }
    seq_parts: list[str] = []
    for p in parts:
        p_low = p.lower()
        if p_low in kinds:
            seq_parts.append(p_low)
        elif p_low in roman_map:
            seq_parts.append(roman_map[p_low])
        elif match := _NUM_ALPHA_RE.fullmatch(p_low):
            seq_parts.extend([match.group(1), match.group(2)])
        elif _DIGITS_RE.match(p_low):
            seq_parts.append(p_low)
        elif _ALPHA_RE.match(p_low):
            seq_parts.append(p_low)
    return tuple(seq_parts)


def _sequence_tokens(parts: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return _sequence_tokens_cached(tuple(parts))


@lru_cache(maxsize=131072)
def _get_id_sequence(eid: str) -> tuple[str, ...]:
    """Extract semantic components with boundary preservation."""
    return _sequence_tokens_cached(tuple(_EID_SPLIT_RE.split(eid)))


@lru_cache(maxsize=131072)
def _get_ref_sequence_cached(path: tuple[tuple[Optional[str], str], ...]) -> tuple[str, ...]:
    parts: list[str] = []
    for kind, label in path:
        if kind:
            parts.append(kind)
        if label:
            parts.append(label)
    return _sequence_tokens_cached(tuple(parts))


def _get_ref_sequence(path: list[tuple[Optional[str], str]] | tuple[tuple[Optional[str], str], ...]) -> tuple[str, ...]:
    return _get_ref_sequence_cached(tuple(path))


def _build_extraction_context(
    root: ET.Element,
) -> tuple[dict[ET.Element, ET.Element], dict[str, ET.Element], dict[tuple[str, ...], ET.Element]]:
    parent_map = {child: parent for parent in root.iter() for child in parent}
    exact_id_map: dict[str, ET.Element] = {}
    sequence_map: dict[tuple[str, ...], ET.Element] = {}
    for el in root.iter():
        el_id = el.get("id") or el.get("Id")
        if not el_id:
            continue
        norm_el_id = _norm_prov_ref(el_id)
        if norm_el_id and norm_el_id not in exact_id_map:
            exact_id_map[norm_el_id] = el
        seq = _get_id_sequence(el_id)
        if seq and seq not in sequence_map:
            sequence_map[seq] = el
    return parent_map, exact_id_map, sequence_map


@lru_cache(maxsize=65536)
def _parse_ref(ref: str) -> tuple[tuple[Optional[str], str], ...]:
    """Parse 'Sch. 2 para. 2(2)' into [('schedule', '2'), ('paragraph', '2'), (None, '2')]."""
    # Normalize common abbreviations using regex for case insensitivity
    r = ref
    r = re.sub(r"\bSch\.", "schedule", r, flags=re.I)
    r = re.sub(r"\bSch\b", "schedule", r, flags=re.I)
    r = re.sub(r"\bpara\.", "paragraph", r, flags=re.I)
    r = re.sub(r"\bparas\.", "paragraph", r, flags=re.I)
    r = re.sub(r"\bs\.", "section", r, flags=re.I)
    r = re.sub(r"\bss\.", "section", r, flags=re.I)
    r = re.sub(r"\bPt\.", "part", r, flags=re.I)
    r = re.sub(r"\bCh\.", "chapter", r, flags=re.I)
    r = re.sub(r"\barts\.", "article", r, flags=re.I)
    r = re.sub(r"\bart\.", "article", r, flags=re.I)
    r = re.sub(r"\bregs\.", "section", r, flags=re.I)
    r = re.sub(r"\breg\.", "section", r, flags=re.I)
    r = re.sub(r"\bannex\b", "schedule", r, flags=re.I)
    r = re.sub(r"\bpoints?\b", "paragraph", r, flags=re.I)

    # Just in case there are full words without dot but wrong casing
    r = re.sub(r"\bArticle\b", "article", r, flags=re.I)
    r = re.sub(r"\bRule\b", "rule", r, flags=re.I)

    # Split by whitespace, dots, brackets, BUT keep hyphens for ranges (e.g. 10A-10C)
    raw_tokens = _REF_SPLIT_RE.split(r)
    raw_tokens = [t.lower() for t in raw_tokens if t]

    kinds = {"schedule", "paragraph", "section", "part", "chapter", "article", "rule"}
    # Conjunctions and structural modifiers that are not provision identifiers.
    # "word" / "words" appear as qualifier suffixes in affected_provisions strings like
    # "sch. 6 para. 6(2)(a)(ii) and word" — they must not be parsed as provision labels.
    _stop = {
        "and",
        "or",
        "of",
        "cross",
        "heading",
        "crossheading",
        "cross-heading",
        "word",
        "words",
    }
    res = []
    i = 0
    while i < len(raw_tokens):
        t = raw_tokens[i]
        if t in _stop:
            i += 1  # skip conjunction / decorative token
        elif t in kinds and i + 1 < len(raw_tokens):
            if t == "schedule" and raw_tokens[i + 1] in kinds | _stop:
                res.append((t, ""))
                i += 1
                continue
            res.append((t, raw_tokens[i + 1]))
            i += 2
        elif t in kinds:
            res.append((t, ""))
            i += 1
        else:
            res.append((None, t))
            i += 1
    return tuple(res)


def _match_node(el: ET.Element, kind: Optional[str], num: str) -> bool:
    """Check if an element matches a provision kind and/or number."""
    tag = _tag(el).lower()
    if kind:
        synonyms = {
            "schedule": ("schedule", "sched", "schedules"),
            "paragraph": ("p3", "p2", "p1", "paragraph", "para", "p", "listitem"),
            "section": ("section", "p1", "p1group"),
            "part": ("part",),
            "chapter": ("pblock", "chapter"),
        }.get(kind, (kind,))
        if tag not in synonyms:
            return False

    if not num:
        return True

    found_raw_nums = []
    if el.get("Number"):
        found_raw_nums.append(el.get("Number"))
    for child in el:
        ctag = _tag(child).lower()
        if ctag in ("pnumber", "number", "num"):
            # Collect text directly on the element AND tails of its children —
            # legislation.gov.uk often wraps the number text in a CommentaryRef/Repeal
            # element, so the actual digit lives in that child's .tail rather than
            # in Pnumber.text.  Example:
            #   <Pnumber><CommentaryRef Ref="key-xxx"/>2</Pnumber>
            # In that case child.text is None but child.tail is "2".
            raw_text = child.text or ""
            for grandchild in child:
                if grandchild.tail:
                    raw_text += grandchild.tail
            if raw_text.strip():
                found_raw_nums.append(raw_text.strip())
            elif child.text is not None:
                found_raw_nums.append(child.text)

    target_num = re.sub(r"[^0-9a-zA-Z]", "", num).lower()
    roman_map = {
        "i": "1",
        "ii": "2",
        "iii": "3",
        "iv": "4",
        "v": "5",
        "vi": "6",
        "vii": "7",
        "viii": "8",
        "ix": "9",
        "x": "10",
    }
    if target_num in roman_map:
        target_num = roman_map[target_num]

    for raw in found_raw_nums:
        norm_raw = re.sub(r"[^0-9a-zA-Z]", "", raw).lower()
        if norm_raw in roman_map:
            norm_raw = roman_map[norm_raw]
        if norm_raw == target_num:
            return True
    return False


def _find_provision_greedy(
    el: ET.Element, path: list[tuple[Optional[str], str]], depth: int = 0
) -> tuple[Optional[ET.Element], int]:
    """Recursively find a provision."""
    best_node = el if depth > 0 else None
    best_depth = depth
    if depth >= len(path):
        return el, depth
    target_kind, target_num = path[depth]
    for child in el:
        if _match_node(child, target_kind, target_num):
            res_node, res_depth = _find_provision_greedy(child, path, depth + 1)
            if res_depth > best_depth:
                best_node = res_node
                best_depth = res_depth
        else:
            res_node, res_depth = _find_provision_greedy(child, path, depth)
            if res_depth > best_depth:
                best_node = res_node
                best_depth = res_depth
    return best_node, best_depth


def _select_extracted_match(
    el: ET.Element,
    parent_map: Optional[dict[ET.Element, ET.Element]] = None,
) -> ET.Element:
    """Prefer structural amendment containers, not naked inline quote nodes."""
    if _tag(el) in ("BlockAmendment", "InlineAmendment"):
        return el

    if parent_map is not None:
        parent = parent_map.get(el)
        if parent is not None:
            local_text = _text_content(el).strip().lower()
            if re.search(r"\b(?:insert|substitute)\s*[—-]?\s*$", local_text):
                siblings = list(parent)
                try:
                    idx = siblings.index(el)
                except ValueError:
                    idx = -1
                if idx >= 0:
                    for sibling in siblings[idx + 1 :]:
                        sibling_tag = _tag(sibling)
                        if sibling_tag in ("BlockAmendment", "InlineAmendment"):
                            return sibling
                        if sibling_tag in {
                            "P1",
                            "P2",
                            "P3",
                            "P4",
                            "P1group",
                            "Pblock",
                            "Section",
                            "Schedule",
                            "Part",
                            "Chapter",
                            "Article",
                            "Rule",
                            "Subsection",
                        }:
                            break

    for child in el.iter():
        if child is el:
            continue
        if _tag(child) == "BlockAmendment":
            return child

    # Some rows match a full P1/P2/P3 provision whose text contains an
    # InlineAmendment quote fragment. Returning the naked InlineAmendment loses
    # the surrounding instruction context needed for word-level compilation, so
    # in the Inline-only case keep the enclosing provision node.
    return el


def extract_provision_element(
    affecting_act_xml: Path,
    provision_ref: str,
) -> Optional[ET.Element]:
    """Extract the provision element from an affecting act's XML."""
    if not affecting_act_xml.exists():
        return None
    try:
        root = ET.parse(affecting_act_xml).getroot()
    except ET.ParseError as exc:
        print(f"  WARN: XML parse error for {affecting_act_xml}: {exc}")
        return None
    parent_map, exact_id_map, sequence_map = _build_extraction_context(root)

    return _extract_provision_element_from_root(
        root,
        provision_ref,
        parent_map=parent_map,
        exact_id_map=exact_id_map,
        sequence_map=sequence_map,
    )


def _extract_provision_element_from_root(
    root: ET.Element,
    provision_ref: str,
    *,
    parent_map: Optional[dict[ET.Element, ET.Element]] = None,
    exact_id_map: Optional[dict[str, ET.Element]] = None,
    sequence_map: Optional[dict[tuple[str, ...], ET.Element]] = None,
) -> Optional[ET.Element]:
    if parent_map is None or exact_id_map is None or sequence_map is None:
        parent_map, exact_id_map, sequence_map = _build_extraction_context(root)

    norm_full = _norm_prov_ref(provision_ref)
    path = _parse_ref(provision_ref)
    if not path:
        return None
    target_sequence = _get_ref_sequence(path)

    exact_match = exact_id_map.get(norm_full)
    if exact_match is not None:
        return _select_extracted_match(exact_match, parent_map)
    if target_sequence:
        seq_match = sequence_map.get(target_sequence)
        if seq_match is not None:
            return _select_extracted_match(seq_match, parent_map)

    body = None
    for el in root.iter():
        if _tag(el).lower() == "body":
            body = el
            break
    target_node, depth_reached = _find_provision_greedy(body or root, list(path))
    if target_node is not None:
        rem_tokens = [tn for tk, tn in path[depth_reached:] if tn]
        for child in target_node.iter():
            if _tag(child) in ("BlockAmendment", "InlineAmendment"):
                if rem_tokens:
                    inner_text = _text_content(child)
                    if all(t.lower() in inner_text.lower() for t in rem_tokens):
                        return child
                else:
                    return child
        return _select_extracted_match(target_node, parent_map)
    return None


def extract_provision_element_from_bytes(
    xml_bytes: bytes,
    provision_ref: str,
    *,
    root: Optional[ET.Element] = None,
    parent_map: Optional[dict[ET.Element, ET.Element]] = None,
    exact_id_map: Optional[dict[str, ET.Element]] = None,
    sequence_map: Optional[dict[tuple[str, ...], ET.Element]] = None,
) -> Optional[ET.Element]:
    """Extract a provision element from affecting act XML bytes.

    Archive-backed alternative to extract_provision_element() — accepts bytes
    directly so no temp files are needed.  Delegates to the same matching
    logic once the root is parsed.
    """
    if root is None:
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            print(f"  WARN: XML parse error in extract_provision_element_from_bytes: {exc}")
            return None
    return _extract_provision_element_from_root(
        root,
        provision_ref,
        parent_map=parent_map,
        exact_id_map=exact_id_map,
        sequence_map=sequence_map,
    )


def _parse_affected_target(ref: str) -> LegalAddress:
    """Parse 'Sch. 1 Pt. I Ch. 1 para. 1' into a LegalAddress."""
    ref = _normalize_affected_target_ref(ref)
    if ref.strip().lower() == "act":
        return _make_address(container="section", special=FacetKind.WHOLE_ACT)
    if re.fullmatch(r"sch(?:edule)?\.?", ref.strip(), re.I):
        return canonicalize_uk_address(LegalAddress(path=(("schedule", ""),)))

    path = _parse_ref(ref)
    schedule_idx = next((i for i, (kind, _num) in enumerate(path) if kind == "schedule"), None)
    if schedule_idx is not None:
        schedule_tokens = [path[schedule_idx], *path[schedule_idx + 1 :], *reversed(path[:schedule_idx])]
        schedule_path: list[tuple[str, str]] = []
        schedule_depth = 0
        for kind, num in schedule_tokens:
            if kind == "schedule":
                schedule_path.append(("schedule", num))
            elif kind == "part":
                schedule_path.append(("part", num))
            elif kind == "chapter":
                schedule_path.append(("chapter", num))
            elif kind in ("section", "article", "rule", "regulation"):
                schedule_path.append(("section", num))
            elif kind in ("paragraph", None):
                if schedule_depth == 0:
                    schedule_path.append(("paragraph", num))
                elif schedule_depth == 1:
                    if _looks_like_lettered_item_label(num):
                        schedule_path.append(("item", num))
                    else:
                        schedule_path.append(("subparagraph", num))
                else:
                    schedule_path.append(("item", num))
                schedule_depth += 1
        if schedule_path:
            return canonicalize_uk_address(LegalAddress(path=tuple(schedule_path)))

    body_descendant_tokens = [(kind, num) for kind, num in path if kind in ("paragraph", None)]
    if len(body_descendant_tokens) > 2:
        body_path: list[tuple[str, str]] = []
        body_depth = 0
        for kind, num in path:
            if kind == "part":
                body_path.append(("part", num))
            elif kind == "chapter":
                body_path.append(("chapter", num))
            elif kind in ("section", "article", "rule", "regulation"):
                body_path.append(("section", num))
            elif kind in ("paragraph", None):
                if not body_path:
                    body_path.append(("section", num))
                    continue
                if body_depth == 0:
                    body_path.append(("subsection", num))
                elif body_depth == 1:
                    body_path.append(("paragraph", num))
                elif body_depth == 2:
                    body_path.append(("subparagraph", num))
                else:
                    body_path.append(("item", num))
                body_depth += 1
        if body_path:
            return canonicalize_uk_address(LegalAddress(path=tuple(body_path)))

    container: str = "section"
    section = None
    part = None
    chapter = None
    subsection = None
    item = None
    for kind, num in path:
        if kind == "schedule":
            container = "schedule"
            section = num
        elif kind == "part":
            part = num
        elif kind == "chapter":
            chapter = num
        elif kind in ("section", "article", "rule", "regulation"):
            section = num
        elif kind == "paragraph":
            if container == "schedule":
                if not subsection:
                    subsection = num
                else:
                    item = num
            else:
                if not section:
                    section = num
                elif not subsection:
                    subsection = num
                else:
                    item = num
        elif kind is None:
            if container == "schedule":
                if not subsection:
                    subsection = num
                else:
                    item = num
            else:
                if not section:
                    section = num
                elif not subsection:
                    subsection = num
                else:
                    item = num
    return canonicalize_uk_address(
        _make_address(
            container=container,
            section=section,
            part=part,
            chapter=chapter,
            subsection=subsection,
            item=item,
        )
    )


def _select_whole_schedule_element(
    extracted_el: Optional[ET.Element],
    target: LegalAddress,
) -> Optional[ET.Element]:
    """Return the whole Schedule node for a schedule-level target when present."""
    if extracted_el is None:
        return None
    if _addr_container(target) != "schedule" or len(target.path) != 1:
        return None
    schedule_label = _addr_field(target, "schedule")
    if not schedule_label:
        schedules = [child for child in extracted_el.iter() if _tag(child) == "Schedule"]
        if len(schedules) == 1:
            return schedules[0]
        return None
    for child in extracted_el.iter():
        if _tag(child) != "Schedule":
            continue
        num_el = child.find(f".//{{{_LEG_NS}}}Number")
        c_num = _extract_num(num_el)
        if _clean_num(c_num) == _clean_num(schedule_label):
            return child
    return None


def _normalize_affected_target_ref(ref: str) -> str:
    """Insert missing separators in UK affected-target refs before parsing."""
    ref = ref.strip()
    if not ref:
        return ref
    return re.sub(
        r"(?<=\d)(?=(?:paragraph|subsection|sub-paragraph|subparagraph|item|point|section|article|rule)\b)",
        " ",
        ref,
        flags=re.I,
    )


def _split_metadata_provisions(prov_str: str) -> list[str]:
    if not prov_str:
        return []

    def _split_stemmed_alnum(group: str) -> Optional[tuple[str, str]]:
        match = re.fullmatch(r"((?:\d+[A-Z]*|[A-Z]+\d+[A-Z]*))([A-Z])", group, re.I)
        if match is None:
            return None
        return match.group(1).upper(), match.group(2).upper()

    def _is_sibling_group_family(groups: list[str]) -> bool:
        if all(group.isdigit() for group in groups):
            return True
        if all(re.fullmatch(r"\d+[A-Z]*", group, re.I) for group in groups):
            return True
        if all(re.fullmatch(r"[A-Z]+", group, re.I) for group in groups):
            alpha_lengths = {len(group) for group in groups}
            if alpha_lengths <= {1, 2}:
                return True
            return len(alpha_lengths) == 1
        alnum = [re.fullmatch(r"(\d+)([A-Z])", group, re.I) for group in groups]
        if (
            bool(alnum)
            and all(match is not None for match in alnum)
            and len({match.group(1) for match in alnum if match is not None}) == 1
        ):
            return True
        stemmed = [_split_stemmed_alnum(group) for group in groups]
        return (
            bool(stemmed)
            and all(pair is not None for pair in stemmed)
            and len({pair[0] for pair in stemmed if pair is not None}) == 1
        )

    def _expand_parenthesized_range(prefix: str, start_str: str, end_str: str) -> Optional[list[str]]:
        start_str = start_str.upper()
        end_str = end_str.upper()
        if len(start_str) == 1 and len(end_str) == 1 and start_str.isalpha() and end_str.isalpha():
            return [f"{prefix}({chr(c)})" for c in range(ord(start_str), ord(end_str) + 1)]

        stemmed_start = _split_stemmed_alnum(start_str)
        stemmed_end = _split_stemmed_alnum(end_str)
        if stemmed_start is not None and stemmed_end is not None and stemmed_start[0] == stemmed_end[0]:
            return [
                f"{prefix}({stemmed_start[0]}{chr(c)})" for c in range(ord(stemmed_start[1]), ord(stemmed_end[1]) + 1)
            ]

        ms = re.match(r"^(\d+)([A-Z])$", start_str)
        me = re.match(r"^(\d+)([A-Z])$", end_str)
        if ms and me and ms.group(1) == me.group(1):
            base_n = ms.group(1)
            return [f"{prefix}({base_n}{chr(c)})" for c in range(ord(ms.group(2)), ord(me.group(2)) + 1)]

        if start_str.isdigit() and me and me.group(1) == start_str:
            return [f"{prefix}({start_str})"] + [
                f"{prefix}({start_str}{chr(c)})" for c in range(ord("A"), ord(me.group(2)) + 1)
            ]

        if start_str.isdigit() and end_str.isdigit():
            start = int(start_str)
            end = int(end_str)
            if end > start and end - start < 100:
                return [f"{prefix}({n})" for n in range(start, end + 1)]

        return None

    # Split by comma first
    parts = [p.strip() for p in prov_str.split(",") if p.strip()]

    # Expand space-separated section lists: "s. 3A 3B" → ["s. 3A", "s. 3B"]
    # Pattern: kind-abbreviation followed by two or more bare alphanumeric IDs (no parentheses)
    # This is distinct from "s. 3A(1)" (subsection ref, parentheses present).
    expanded_parts = []
    for p in parts:
        if _is_heading_only_ref(p):
            expanded_parts.append(p)
            continue
        p_for_space_list = re.sub(r"\s+and\s+cross[-\s]?headings?\b.*$", "", p, flags=re.I).strip()
        m = re.match(r"^(s\.|ss\.|para\.|art\.)\s+([0-9A-Z]+)(\s+[0-9A-Z]+)+$", p_for_space_list, re.I)
        if m:
            kind_abbr = m.group(1)
            nums = re.findall(r"[0-9A-Z]+", p_for_space_list[len(kind_abbr) :], re.I)
            for n in nums:
                expanded_parts.append(f"{kind_abbr} {n}")
            continue
        m = re.match(
            r"^(.*?\b(?:para\.|paragraph|s\.|ss\.|section|art\.|article)\s+)([0-9A-Z]+)(\s+[0-9A-Z]+)+$",
            p_for_space_list,
            re.I,
        )
        if m:
            prefix = m.group(1)
            nums = re.findall(r"[0-9A-Z]+", p_for_space_list[len(prefix) :], re.I)
            for n in nums:
                expanded_parts.append(f"{prefix}{n}".strip())
        else:
            expanded_parts.append(p)
    parts = expanded_parts

    # Handle ranges like "ss. 10A-10C"
    # Note: range endpoints must contain at least one digit — pure-word compounds
    # like "cross-heading" must NOT be expanded (they are not ranges).
    all_parts = []
    for p in parts:
        repeated_anchor_m = re.match(
            r"^(.*?\b(?:para\.|paragraph|s\.|ss\.|section|art\.|article)\s+)(\d+(?:\([0-9A-Z]+\))+)\s+and\s+(\d+(?:\([0-9A-Z]+\))+)$",
            p,
            re.I,
        )
        if repeated_anchor_m:
            prefix = repeated_anchor_m.group(1)
            all_parts.append(f"{prefix}{repeated_anchor_m.group(2)}")
            all_parts.append(f"{prefix}{repeated_anchor_m.group(3)}")
            continue

        range_plus_ws_group_m = re.match(
            r"^(.*?)\(([0-9A-Z]+)\)-\(([0-9A-Z]+)\)((?:\s+\([0-9A-Z]+\))+)$",
            p,
            re.I,
        )
        if range_plus_ws_group_m:
            prefix = range_plus_ws_group_m.group(1).rstrip()
            expanded_range = _expand_parenthesized_range(
                prefix,
                range_plus_ws_group_m.group(2),
                range_plus_ws_group_m.group(3),
            )
            trailing_raw = re.findall(r"\(([0-9A-Z]+)\)", range_plus_ws_group_m.group(4), re.I)
            if expanded_range and trailing_raw:
                all_parts.extend(expanded_range)
                for group in trailing_raw:
                    all_parts.append(f"{prefix}({group})")
                continue

        adjacent_group_m = re.match(
            r"^(.*?)((?:\([0-9A-Z]+\)){2,})$",
            p,
            re.I,
        )
        if adjacent_group_m:
            prefix = adjacent_group_m.group(1)
            all_groups = re.findall(r"\(([0-9A-Z]+)\)", adjacent_group_m.group(2), re.I)
            if len(all_groups) >= 2:
                if _is_sibling_group_family(all_groups) and not (
                    len(all_groups) == 2
                    and _looks_like_lettered_item_label(all_groups[0])
                    and _looks_like_roman_subitem_label(all_groups[1])
                ):
                    for group in all_groups:
                        all_parts.append(f"{prefix}({group})")
                    continue
                if (
                    len(all_groups) == 3
                    and _is_sibling_group_family([all_groups[0], all_groups[2]])
                    and _looks_like_lettered_item_label(all_groups[1])
                ):
                    all_parts.append(f"{prefix}({all_groups[0]})({all_groups[1]})")
                    all_parts.append(f"{prefix}({all_groups[2]})")
                    continue
                # Fixed-prefix sibling suffixes: ``s. 54(8)(b)(c)`` means
                # paragraph siblings (b) and (c) under subsection (8), not
                # nested ``(8)(b)(c)``. Likewise ``Sch. 1 para. 1(1)(b)(c)``
                # means item siblings under ``(1)(1)``.
                for split_at in range(1, len(all_groups) - 1):
                    fixed_groups = all_groups[:split_at]
                    sibling_groups = all_groups[split_at:]
                    if _looks_like_lettered_item_label(sibling_groups[0]) and any(
                        _looks_like_roman_subitem_label(group) for group in sibling_groups[1:]
                    ):
                        continue
                    if not _is_sibling_group_family(sibling_groups):
                        continue
                    fixed_prefix = prefix + "".join(f"({group})" for group in fixed_groups)
                    for group in sibling_groups:
                        all_parts.append(f"{fixed_prefix}({group})")
                    break
                if all_parts and all_parts[-1].startswith(prefix):
                    continue

        # Whitespace-compressed sibling refs: "s. 62(7) (8)" means sibling
        # subsections (7) and (8), not a nested paragraph 8 under subsection 7.
        ws_group_m = re.match(
            r"^(.*?)(\(([0-9A-Z]+)\))((?:\s+\([0-9A-Z]+\))+)$",
            p,
            re.I,
        )
        if ws_group_m:
            prefix = ws_group_m.group(1)
            first_raw = ws_group_m.group(3)
            trailing_raw = re.findall(r"\(([0-9A-Z]+)\)", ws_group_m.group(4), re.I)
            if trailing_raw:
                all_groups = [first_raw, *trailing_raw]
                if _is_sibling_group_family(all_groups):
                    for group in all_groups:
                        all_parts.append(f"{prefix}({group})")
                    continue

        # Strip "and cross-heading" / "and cross heading" qualifier suffix so that
        # ranges like "s. 9-12 and cross-heading" expand correctly.
        p_for_range = re.sub(r"\s+and\s+cross[-\s]?heading\b.*$", "", p, flags=re.I).strip()

        # Parenthesized subsection range: "s. 18(7A)-(7D)" → "s. 18(7A)", "s. 18(7B)", ...
        paren_range_m = re.match(r"^(.*?)\(([0-9A-Z]+)\)-\(([0-9A-Z]+)\)$", p_for_range, re.I)
        if paren_range_m:
            prefix = paren_range_m.group(1).rstrip()
            expanded_range = _expand_parenthesized_range(
                prefix,
                paren_range_m.group(2),
                paren_range_m.group(3),
            )
            if expanded_range:
                all_parts.extend(expanded_range)
                continue

        range_m = re.search(r"^(.*?)\s?([0-9A-Z]+)-([0-9A-Z]+)$", p_for_range, re.I)
        if range_m:
            prefix = range_m.group(1).strip()
            start_str = range_m.group(2)
            end_str = range_m.group(3)
            # Skip if neither endpoint has a digit — e.g. "cross-heading"
            if not any(c.isdigit() for c in start_str) and not any(c.isdigit() for c in end_str):
                all_parts.append(p)
                continue

            # Simple numeric range
            if start_str.isdigit() and end_str.isdigit():
                start = int(start_str)
                end = int(end_str)
                if end > start and end - start < 100:
                    for n in range(start, end + 1):
                        all_parts.append(f"{prefix} {n}".strip())
                    continue

            # Alphanumeric range: 10A-10C
            m_start = re.match(r"^(\d+)([A-Z]*)$", start_str, re.I)
            m_end = re.match(r"^(\d+)([A-Z]*)$", end_str, re.I)
            if m_start and m_end and m_start.group(1) == m_end.group(1):
                base = m_start.group(1)
                s_let = m_start.group(2).upper()
                e_let = m_end.group(2).upper()
                if s_let and e_let and len(s_let) == 1 and len(e_let) == 1:
                    for c in range(ord(s_let), ord(e_let) + 1):
                        all_parts.append(f"{prefix} {base}{chr(c)}".strip())
                    continue

            # Mixed numeric -> alphanumeric range: "s. 60-61A" should expand to
            # "s. 60", "s. 61", "s. 61A" rather than dropping the intermediate
            # whole-number section.
            if m_start and m_end and not m_start.group(2):
                start_base = int(m_start.group(1))
                end_base = int(m_end.group(1))
                end_suffix = m_end.group(2).upper()
                if end_suffix and len(end_suffix) == 1 and end_base >= start_base and end_base - start_base < 100:
                    for n in range(start_base, end_base + 1):
                        all_parts.append(f"{prefix} {n}".strip())
                    for c in range(ord("A"), ord(end_suffix) + 1):
                        all_parts.append(f"{prefix} {end_base}{chr(c)}".strip())
                    continue

            # Fallback
            all_parts.append(f"{prefix} {start_str}".strip())
            all_parts.append(f"{prefix} {end_str}".strip())
        else:
            all_parts.append(p)

    carried_parts: list[str] = []
    active_prefix: Optional[str] = None
    subordinate_prefix_re = re.compile(
        r"^(?:para(?:graph)?|sub-?paragraph|item|point)\b",
        re.I,
    )
    for p in all_parts:
        part = p.strip()
        if not part:
            continue
        if _is_heading_only_ref(part):
            carried_parts.append(part)
            continue

        groups = re.findall(r"\(([0-9A-Z]+)\)", part, re.I)
        if active_prefix and groups and subordinate_prefix_re.match(part):
            carried_parts.append(f"{active_prefix}{''.join(f'({group})' for group in groups)}")
            continue

        carried_parts.append(part)
        if re.match(r"^(?:s\.|ss\.|section|sch\.?|schedule|art\.|article)(?:\s|\(|$)", part, re.I):
            active_prefix = part.rstrip(" ,;")

    return carried_parts


def _expand_sibling_targets_from_extracted(
    prov_str: str,
    extracted_el: Optional[ET.Element],
) -> Optional[list[str]]:
    """Expand refs like 'Sch. para. 5(7)(8)' when the payload has sibling nodes.

    Some legislation.gov.uk effects feeds compress multiple inserted sibling
    subparagraphs/items into one metadata ref while the BlockAmendment carries
    separate direct children for each inserted sibling.  In that case we expand
    the ref into one target per direct child so replay does not silently keep
    only the first or last sibling.
    """
    if extracted_el is None or _tag(extracted_el) not in ("BlockAmendment", "InlineAmendment"):
        return None

    structural_tags = {
        "Part",
        "Chapter",
        "EUChapter",
        "Pblock",
        "P1group",
        "Section",
        "P1",
        "Article",
        "Rule",
        "Subsection",
        "P2",
        "P3",
        "Schedule",
    }
    child_nums: list[str] = []
    child_raw_nums: list[str] = []
    for child in list(extracted_el):
        if _tag(child) not in structural_tags:
            continue
        num_el = child.find(f"./{{{_LEG_NS}}}Pnumber")
        if num_el is None:
            num_el = child.find(f"./{{{_LEG_NS}}}Number")
        raw_num = _extract_num(num_el)
        clean_num = _clean_num(raw_num)
        if clean_num:
            child_nums.append(clean_num)
            child_raw_nums.append(raw_num)

    if len(child_nums) < 2:
        return None

    range_groups = re.match(
        r"^(.*?)\(([0-9A-Z]+)\)-\(([0-9A-Z]+)\)$",
        prov_str.strip(),
        re.I,
    )
    if range_groups:
        prefix = range_groups.group(1).rstrip()
        start_group = _clean_num(range_groups.group(2))
        end_group = _clean_num(range_groups.group(3))
        if child_nums[0] == start_group and child_nums[-1] == end_group:
            return [f"{prefix}({raw_num})" for raw_num in child_raw_nums]

    paren_groups = re.findall(r"\(([0-9A-Z]+)\)", prov_str, re.I)
    if len(paren_groups) < 2:
        return None

    trailing_raw = paren_groups[-len(child_nums) :]
    trailing_clean = [_clean_num(group) for group in trailing_raw]
    if trailing_clean != child_nums:
        return None

    base = prov_str.rstrip()
    for _ in range(len(child_nums)):
        base = re.sub(r"\([0-9A-Z]+\)\s*$", "", base, flags=re.I).rstrip()

    return [f"{base}({raw_num})" for raw_num in child_raw_nums]


def _retarget_substituted_series_to_replaced_anchor(
    effect_type: str,
    target_refs: list[str],
) -> list[str]:
    """Retarget the first replacement when metadata names the replacement series."""
    raw = (effect_type or "").strip()
    if not raw.lower().startswith("substituted for "):
        return target_refs
    if not target_refs:
        return target_refs

    anchor_refs = _split_metadata_provisions(raw[len("substituted for ") :].strip())
    if not anchor_refs:
        return target_refs

    if len(target_refs) == 1 and len(anchor_refs) >= 2:
        try:
            anchor_target = _parse_affected_target(anchor_refs[0])
            replacement_target = _parse_affected_target(target_refs[0])
        except Exception:
            return target_refs

        anchor_section = _addr_field(anchor_target, "section") or _addr_field(anchor_target, "schedule")
        replacement_section = _addr_field(replacement_target, "section") or _addr_field(replacement_target, "schedule")
        anchor_leaf_kind = _addr_leaf_kind(anchor_target)
        replacement_leaf_kind = _addr_leaf_kind(replacement_target)
        anchor_leaf = _clean_num(_addr_leaf_label(anchor_target) or "")
        replacement_leaf = _clean_num(_addr_leaf_label(replacement_target) or "")
        if (
            anchor_section
            and anchor_section == replacement_section
            and anchor_leaf_kind
            and anchor_leaf_kind == replacement_leaf_kind
            and anchor_leaf
            and replacement_leaf
            and replacement_leaf != anchor_leaf
            and replacement_leaf.startswith(anchor_leaf)
        ):
            return [anchor_refs[0]]
        return target_refs

    if len(target_refs) < 2 or len(anchor_refs) != 1:
        return target_refs

    try:
        anchor_target = _parse_affected_target(anchor_refs[0])
        first_target = _parse_affected_target(target_refs[0])
        second_target = _parse_affected_target(target_refs[1])
    except Exception:
        return target_refs

    anchor_section = _addr_field(anchor_target, "section") or _addr_field(anchor_target, "schedule")
    first_section = _addr_field(first_target, "section") or _addr_field(first_target, "schedule")
    second_section = _addr_field(second_target, "section") or _addr_field(second_target, "schedule")
    anchor_sub = _clean_num(_addr_field(anchor_target, "subsection") or "")
    first_sub = _clean_num(_addr_field(first_target, "subsection") or "")
    second_sub = _clean_num(_addr_field(second_target, "subsection") or "")

    if not anchor_section or anchor_section != first_section or anchor_section != second_section:
        return target_refs
    if not anchor_sub or not first_sub or not second_sub:
        return target_refs
    if first_sub == anchor_sub or not first_sub.startswith(anchor_sub):
        return target_refs
    if not second_sub.startswith(anchor_sub):
        return target_refs

    retargeted = list(target_refs)
    retargeted[0] = anchor_refs[0]
    return retargeted


def _repeal_tail_for_substituted_series_replacement(
    effect_type: str,
    original_target_refs: list[str],
) -> list[str]:
    """Return trailing replaced refs that should compile as repeals.

    Some UK effects are recorded as:
      effect_type="substituted for s. 3(5)(6)"
      affected_provisions="s. 3(5A)"

    Semantically this means the first replaced anchor becomes the new payload
    target and the remaining replaced anchors are repealed.
    """
    raw = (effect_type or "").strip()
    if not raw.lower().startswith("substituted for "):
        return []
    if len(original_target_refs) != 1:
        return []

    anchor_refs = _split_metadata_provisions(raw[len("substituted for ") :].strip())
    if len(anchor_refs) < 2:
        return []

    try:
        first_anchor = _parse_affected_target(anchor_refs[0])
        replacement_target = _parse_affected_target(original_target_refs[0])
    except Exception:
        return []

    anchor_section = _addr_field(first_anchor, "section") or _addr_field(first_anchor, "schedule")
    replacement_section = _addr_field(replacement_target, "section") or _addr_field(replacement_target, "schedule")
    anchor_leaf_kind = _addr_leaf_kind(first_anchor)
    replacement_leaf_kind = _addr_leaf_kind(replacement_target)
    anchor_leaf = _clean_num(_addr_leaf_label(first_anchor) or "")
    replacement_leaf = _clean_num(_addr_leaf_label(replacement_target) or "")
    if (
        not anchor_section
        or anchor_section != replacement_section
        or not anchor_leaf_kind
        or anchor_leaf_kind != replacement_leaf_kind
        or not anchor_leaf
        or not replacement_leaf
        or replacement_leaf == anchor_leaf
        or not replacement_leaf.startswith(anchor_leaf)
    ):
        return []

    return anchor_refs[1:]


def _expand_sibling_targets_from_text(
    prov_str: str,
    extracted_text: Optional[str],
) -> Optional[list[str]]:
    """Expand compressed sibling refs from plain-text omission/repeal wording."""
    if not extracted_text:
        return None

    sibling_text = None
    sibling_kind = None
    for pattern in (
        r"\bfor\s+((?:sub-)?paragraphs?|subsections?)\s+([^.;]+?)\s+substitute\b",
        r"\bomit\s+(subsections?|(?:sub-)?paragraphs?)\s+([^.;]+)",
        r"\b(subsections?|(?:sub-)?paragraphs?)\s+([^.;]+?)\s+(?:is|are)\s+repealed\b",
        r"\bin\s+((?:sub-)?paragraphs?|subsections?)\s+([^.;]+?)\s+(?:after|before|insert|substitute)\b",
    ):
        m = re.search(pattern, extracted_text, flags=re.I)
        if m:
            sibling_kind = m.group(1).lower()
            sibling_text = m.group(2)
            break
    if sibling_text is None or sibling_kind is None:
        return None

    sibling_parts = [part.strip() for part in re.split(r"\s*(?:,|and)\s*", sibling_text, flags=re.I) if part.strip()]
    if len(sibling_parts) < 2:
        return None
    if sibling_kind.startswith("subsection") and any(
        re.match(r"^(?:sub-?paragraph|paragraph|item|point)\b", part, flags=re.I) for part in sibling_parts
    ):
        return None

    flat_sibling_raw: list[str] = []
    for part in sibling_parts:
        part_groups = re.findall(r"\(([0-9A-Z]+)\)", part, re.I)
        if not part_groups:
            return None
        flat_sibling_raw.extend(part_groups)

    paren_groups = re.findall(r"\(([0-9A-Z]+)\)", prov_str, re.I)
    if len(paren_groups) < len(flat_sibling_raw):
        return None

    prov_is_schedule = bool(re.match(r"^\s*sch(?:edule)?\.?", prov_str, re.I))
    if sibling_kind.startswith("subsection") and prov_is_schedule:
        return None
    if ("paragraph" in sibling_kind) and not prov_is_schedule and not re.match(r"^\s*ss?\.\s*", prov_str, re.I):
        return None

    trailing_raw = paren_groups[-len(flat_sibling_raw) :]
    if [_clean_num(g) for g in trailing_raw] != [_clean_num(g) for g in flat_sibling_raw]:
        return None

    base = prov_str.rstrip()
    for _ in range(len(flat_sibling_raw)):
        base = re.sub(r"\([0-9A-Z]+\)\s*$", "", base, flags=re.I).rstrip()

    return [f"{base}{part}" for part in sibling_parts]


def _extract_crossheading_payload_from_extracted(
    affected_provisions: str,
    extracted_el: Optional[ET.Element],
) -> Optional[IRNode]:
    """Return a standalone crossheading payload for refs ending in 'and cross-heading'."""
    if extracted_el is None or "cross-heading" not in affected_provisions.lower():
        return None

    candidate = extracted_el
    if _tag(candidate) in ("BlockAmendment", "InlineAmendment") and len(list(candidate)) == 1:
        candidate = list(candidate)[0]
    if _tag(candidate) != "Pblock":
        return None

    title_el = candidate.find(f"./{{{_LEG_NS}}}Title")
    if title_el is None:
        return None
    title = _text_content(title_el)
    if not title:
        return None
    return IRNode(kind=IRNodeKind.CROSSHEADING, label=None, text=title, children=())


def _with_trailing_subordinate_siblings(
    actual_el: ET.Element,
    amendment_container: Optional[ET.Element],
) -> ET.Element:
    """Attach direct trailing subordinate siblings for block-amendment payloads.

    Some affecting-act extracts encode a replaced paragraph as a `P3` followed by
    sibling `P4` nodes rather than nested children. For payload compilation we
    want those subordinate rows preserved under the selected parent payload.
    """
    if amendment_container is None or _tag(amendment_container) not in {"BlockAmendment", "InlineAmendment"}:
        return actual_el
    tag = _tag(actual_el)
    subordinate_tag = {"P2": "P3", "P3": "P4"}.get(tag)
    if subordinate_tag is None:
        return actual_el
    children = list(amendment_container)
    try:
        idx = children.index(actual_el)
    except ValueError:
        return actual_el
    trailing = []
    for sibling in children[idx + 1 :]:
        sibling_tag = _tag(sibling)
        if sibling_tag == subordinate_tag:
            trailing.append(_clone_element(sibling))
            continue
        if sibling_tag in {"CommentaryRef", "Commentary"}:
            continue
        break
    if not trailing:
        return actual_el
    clone = _clone_element(actual_el)
    for sibling in trailing:
        clone.append(sibling)
    return clone


def _is_non_substantive_structural_payload(node: Optional[UKMutableNode]) -> bool:
    """Return True for placeholder structural payloads like '77 . . . .'.

    These appear in some affecting-act extracts as numbering plus dot leaders
    with no operative content. Replaying them as real inserts creates bogus
    wrapper nodes that are later hard to distinguish from genuine structure.
    """
    if node is None:
        return False
    if str(node.kind).lower() not in {
        "section",
        "subsection",
        "paragraph",
        "subparagraph",
        "item",
        "article",
        "rule",
        "content",
    }:
        return False
    text = (node.text or "").strip()
    if node.children:
        if text and re.sub(r"[.\s]+", "", re.sub(r"^\s*[0-9A-Z]+\s*", "", text, flags=re.I)) != "":
            return False
        return all(_is_non_substantive_structural_payload(child) for child in node.children)
    if not text:
        return True
    stripped = re.sub(r"^\s*[0-9A-Z]+\s*", "", text, flags=re.I)
    stripped = re.sub(r"[.\s]+", "", stripped)
    return stripped == ""


def compile_effect_to_ir_ops(
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    sequence: int = 0,
    fallback_for_missing_extracted_source: bool = False,
) -> list[LegalOperation]:
    """Compile a UKEffectRecord + XML element into LawVM LegalOperations.

    Word-level effects ("words substituted", "words repealed", "words omitted",
    "words inserted") compile to text_replace / text_repeal actions with a
    typed ``text_patch`` as the authoritative text-level payload. Legacy
    ``text_match`` / ``text_replacement`` are compatibility only when they
    still appear at older boundaries. Structural effects ("substituted",
    "repealed", "inserted") compile to replace / repeal / insert as before.

    Effects with an empty effect_type (typically from XML metadata) are inferred
    from the provision text when possible; if no verb can be found they are skipped
    rather than guessing a structural action.
    """
    # Determine whether this is a word-level (intra-node text) effect.
    effect_type = (effect.effect_type or "").strip().lower()

    # Commencement rows affect in-force status, not structural text/state.
    if effect_type in _COMMENCEMENT_EFFECT_TYPES:
        return []

    _word_level_types = frozenset(
        {
            "words substituted",
            "word substituted",
            "substituted for words",
            "words repealed",
            "word repealed",
            "words omitted",
            "word omitted",
            "words inserted",
            "word inserted",
        }
    )
    is_word_level = effect_type in _word_level_types

    # Map effect_type to a base action.  Word-level effects start as "replace"
    # but may be promoted to text_replace / text_repeal after fragment extraction.
    action_map = {
        "inserted": "insert",
        "word inserted": "insert",
        "words inserted": "insert",
        "repealed": "repeal",
        "repealed in part": "replace",
        "words repealed": "replace",
        "word repealed": "replace",
        "substituted": "replace",
        "words substituted": "replace",
        "substituted for words": "replace",
        "word substituted": "replace",
        "replaced": "replace",
        "words omitted": "replace",
        "word omitted": "replace",
        "omitted": "repeal",
        "ceases to have effect": "repeal",
    }
    action = action_map.get(effect_type)
    if not action and effect_type.startswith("substituted for"):
        action = "replace"
    extracted_text = _text_content(extracted_el) if extracted_el is not None else None

    # Infer missing action from text heuristics if metadata is empty.
    # For empty effect_type we require a clear structural verb — if none is found
    # we skip (return []) rather than guessing "modified" or a structural replace.
    if not action and extracted_el is not None:
        text_lower = (extracted_text or "").lower()
        if "repeal" in text_lower or "omit" in text_lower:
            action = "repeal"
        elif "substitute" in text_lower or "replace" in text_lower:
            action = "replace"
        elif "insert" in text_lower:
            action = "insert"
        elif re.search(r"\bfrom\b.*\bto\b", text_lower, re.I | re.S):
            action = "replace"
        # No else — leave action=None so we fall through to the early return below.

    if not action:
        return []

    use_metadata_fallback = fallback_for_missing_extracted_source and extracted_el is None and action == "insert"
    extraction_witness = _uk_extraction_witness(
        effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        metadata_fallback_used=use_metadata_fallback,
    )
    effect_witness = _uk_effect_witness(
        effect,
        authority_layer=extraction_witness.authority_layer,
    )

    # ALWAYS split metadata provisions to handle ranges and lists
    targets_str = _split_metadata_provisions(effect.affected_provisions)
    original_targets_str = list(targets_str)
    trailing_repeal_refs: list[str] = []
    replacement_leaf_override: Optional[str] = None
    replacement_leaf_kind: Optional[str] = None
    if action == "replace":
        # Keep the replacement target labels authoritative. The older anchor-
        # retarget heuristic rewrites live replacement labels back to the
        # legacy anchor series, which is exactly the kind of compatibility
        # slop we do not want to keep around.
        trailing_repeal_refs = _repeal_tail_for_substituted_series_replacement(
            effect.effect_type,
            original_targets_str,
        )
        if trailing_repeal_refs and original_targets_str:
            try:
                replacement_target = _parse_affected_target(original_targets_str[0])
            except Exception:
                replacement_target = None
            if replacement_target is not None:
                replacement_leaf_override = _addr_leaf_label(replacement_target)
                replacement_leaf_kind = _addr_leaf_kind(replacement_target)
    if len(targets_str) == 1:
        expanded_targets = _expand_sibling_targets_from_extracted(targets_str[0], extracted_el)
        if not expanded_targets:
            expanded_targets = _expand_sibling_targets_from_text(targets_str[0], extracted_text)
        if expanded_targets:
            targets_str = expanded_targets
    if not targets_str:
        return []

    ops = []
    if action == "insert":
        crossheading_payload = _extract_crossheading_payload_from_extracted(
            effect.affected_provisions,
            extracted_el,
        )
        if crossheading_payload is not None:
            crossheading_target = canonicalize_uk_address(LegalAddress(path=(("crossheading", ""),)))
            crossheading_target_witness = _uk_target_expansion_witness(
                "cross-heading",
                ["cross-heading"],
            )
            crossheading_lowered_witness = UKLoweredOperationWitness(
                op_id=f"{effect.effect_id}_crossheading",
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=crossheading_target,
                payload=crossheading_payload,
                source=OperationSource(
                    statute_id=effect.affecting_act_id,
                    title=effect.affecting_title,
                    effective=effect_witness.applicability.effective_date or "",
                    raw_text=extraction_witness.extracted_text,
                ),
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=crossheading_target_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=crossheading_lowered_witness.op_id,
                    sequence=sequence,
                    action=StructuralAction.INSERT,
                    target=crossheading_target,
                    payload=_payload_with_rewrite_witness(crossheading_payload, crossheading_lowered_witness),
                    source=crossheading_lowered_witness.source,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=_uk_lowered_op_provenance_tags(crossheading_lowered_witness),
                )
            )
    for t_str in targets_str:
        if _is_heading_only_ref(t_str):
            continue
        if action == "replace" and _is_crossheading_ref(t_str):
            # Cross-heading replacements are not yet compiled onto an explicit
            # crossheading target surface. Emitting them as structural replace
            # ops against the numbered section target is destructive and can
            # erase the real section subtree. Skip until the frontend has a
            # proper crossheading replacement lane.
            continue

        target = canonicalize_uk_address(_parse_affected_target(t_str))
        parse_context = "schedule" if _addr_container(target) == "schedule" else ""
        content_ir = None
        if extracted_el is not None:
            actual_el = _select_whole_schedule_element(extracted_el, target)
            # Find any BlockAmendment or InlineAmendment in the subtree
            if actual_el is None:
                for am in extracted_el.iter():
                    if _tag(am) in ("BlockAmendment", "InlineAmendment"):
                        # Find the first structural node whose numbering matches the
                        # target provision. Whole-schedule targets are handled above
                        # so a paragraph "2" does not hijack "Sch. 2".
                        for child in am.iter():
                            ct = _tag(child)
                            if ct in (
                                "Part",
                                "Chapter",
                                "EUChapter",
                                "Pblock",
                                "P1group",
                                "Section",
                                "P1",
                                "Article",
                                "Rule",
                                "Subsection",
                                "P2",
                                "P3",
                                "P4",
                                "Schedule",
                            ):
                                c_num = _direct_structural_num(child)
                                target_num = _addr_leaf_label(target)
                                if not target_num or _clean_num(c_num) == _clean_num(target_num):
                                    actual_el = child
                                    break
                        if actual_el is not None:
                            actual_el = _with_trailing_subordinate_siblings(actual_el, am)
                            break

            if actual_el is None:
                # Fallback: maybe the extracted element ITSELF is the node
                if _tag(extracted_el) in (
                    "Part",
                    "Chapter",
                    "EUChapter",
                    "Pblock",
                    "P1group",
                    "Section",
                    "P1",
                    "Article",
                    "Rule",
                    "Subsection",
                    "P2",
                    "P3",
                    "P4",
                    "Schedule",
                ):
                    target_num = _addr_leaf_label(target)
                    extracted_num = _direct_structural_num(extracted_el)
                    if not target_num or _clean_num(extracted_num) == _clean_num(target_num):
                        actual_el = extracted_el
                    else:
                        actual_el = _retarget_instruction_element_to_target(
                            extracted_el,
                            target,
                            extracted_text,
                        )
            elif actual_el is not extracted_el:
                actual_el = _with_trailing_subordinate_siblings(actual_el, extracted_el)

            if actual_el is not None:
                tag = _tag(actual_el)
                if tag == "Part":
                    content_ir = _parse_part(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag in ("Chapter", "EUChapter"):
                    content_ir = _parse_chapter(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "Pblock":
                    content_ir = _parse_pblock(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "P1group":
                    content_ir = _parse_p1group(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag in ("Section", "P1", "Article", "Rule", "ConventionRights", "EUSection"):
                    content_ir = _parse_section(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag in ("Subsection", "P2"):
                    content_ir = _parse_p2(
                        actual_el, parse_context or "body", force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "P3":
                    content_ir = _parse_p3(
                        actual_el, parse_context or "body", force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "P4":
                    content_ir = _parse_p4(
                        actual_el, parse_context or "body", force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "Schedule":
                    content_ir = _parse_schedule_single(
                        actual_el, "schedule", force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                if content_ir is not None:
                    direct_text = _direct_payload_text(actual_el)
                    if direct_text:
                        content_ir["text"] = direct_text

        if content_ir is None:
            # Infer kind and label from target if metadata points to a specific provision
            inferred_kind = "content"
            inferred_label = None
            _container = _addr_container(target)
            _t_section = _addr_field(target, "section") or _addr_field(target, "schedule")
            _t_part = _addr_field(target, "part")
            _t_chapter = _addr_field(target, "chapter")
            _schedule_paragraph = None
            _schedule_subparagraph = None
            _schedule_items: list[str] = []
            if _container == "schedule":
                _schedule_paragraph, _schedule_subparagraph, _schedule_items = _schedule_target_levels(target)
                _t_subsection = _schedule_subparagraph
                _t_item = _schedule_items[-1] if _schedule_items else None
            else:
                _paras2 = [lbl for k, lbl in target.path if k == "paragraph"]
                _subsec_field2 = _addr_field(target, "subsection")
                if _subsec_field2:
                    _t_subsection = _subsec_field2
                    _t_item = _paras2[0] if _paras2 else None
                else:
                    _t_subsection = _paras2[0] if _paras2 else None
                    _t_item = _paras2[1] if len(_paras2) >= 2 else None
            if _container == "schedule" and not _t_subsection and not _t_item:
                if _schedule_paragraph:
                    inferred_kind = "paragraph"
                    inferred_label = _schedule_paragraph
                else:
                    inferred_kind = "schedule"
                    inferred_label = _t_section
            elif _container == "schedule" and _t_item:
                inferred_kind = "item"
                inferred_label = _t_item
            elif _container == "schedule" and _t_subsection:
                inferred_kind = "subparagraph"
                inferred_label = _t_subsection
            elif _t_item:
                inferred_kind = "paragraph"
                inferred_label = _t_item
            elif _t_subsection:
                inferred_kind = "subsection"
                inferred_label = _t_subsection
            elif _t_section:
                inferred_kind = "section"
                inferred_label = _t_section
            elif _t_chapter:
                inferred_kind = "chapter"
                inferred_label = _t_chapter
            elif _t_part:
                inferred_kind = "part"
                inferred_label = _t_part

            inferred_text = extracted_text or ""
            if use_metadata_fallback and not inferred_text and not _is_heading_only_ref(t_str):
                inferred_text = f"[inserted by metadata source only: {effect.effect_id}]"
            content_ir = {
                "kind": inferred_kind,
                "label": inferred_label,
                "text": inferred_text,
                "children": [],
            }

        # Safety guard: if extraction failed (extracted_el is None) and the action is a
        # structural replace or insert, we have no payload text.  Applying a replace with an
        # empty-text node would silently erase real content, which is worse than a no-op.
        # Repeal is fine (no payload needed).  Word-level effects (text_replace/text_repeal)
        # are handled via fragment_subs and don't reach here with a structural payload.
        if (
            extracted_el is None
            and action in ("replace", "insert")
            and not extracted_text
            and not use_metadata_fallback
        ):
            continue

        curr_action = action
        fragment_subs: Optional[list] = None
        # Text-level fields (populated for text_replace / text_repeal ops)
        op_text_match: Optional[str] = None
        op_text_replacement: Optional[str] = None
        op_text_occurrence: int = 0
        text_patch: Optional[TextPatchSpec] = None

        # Grounding 2.0: Fragment substitutions
        if (curr_action == "replace" or is_word_level) and extracted_text:
            if not is_whole_node_replacement(extracted_text, effect.effect_type):
                subs = parse_fragment_substitution(extracted_text)
                if subs:
                    fragment_subs = subs
                    content_ir = None
                    # Promote to text_replace / text_repeal with fields populated.
                    # Use the first pair as the primary; additional pairs stay in notes.
                    primary = subs[0]
                    op_text_match = primary["original"]
                    op_text_replacement = primary["replacement"]
                    # Word-level fragment edits are replayed as text_replace/text_repeal
                    # regardless of whether the metadata verb was "replace" or "insert".
                    if is_word_level and op_text_replacement == "":
                        curr_action = "text_repeal"
                    else:
                        curr_action = "text_replace"
                else:
                    # Fallback regex for simple omissions not caught by NLP
                    _OPEN_Q = "\"\u201c\u2018'"
                    _CLOSE_Q = "\"\u201d\u2019'"
                    m_omit = re.search("(?:omit|repeal) [" + _OPEN_Q + "](.*?)[" + _CLOSE_Q + "]", extracted_text, re.I)
                    if not m_omit:
                        m_omit = re.search(
                            "[" + _OPEN_Q + "](.*?)[" + _CLOSE_Q + "] is (?:omitted|repealed)", extracted_text, re.I
                        )
                    if m_omit:
                        fragment_subs = [{"original": m_omit.group(1), "replacement": ""}]
                        content_ir = None
                        op_text_match = m_omit.group(1)
                        op_text_replacement = ""
                        curr_action = "text_repeal" if is_word_level else "text_replace"
                    elif (
                        is_word_level
                        and effect.effect_type == "substituted for words"
                        and content_ir is not None
                        and content_ir.get("kind") == _addr_leaf_kind(target)
                        and _clean_num(str(content_ir.get("label") or "")) == _clean_num(_addr_leaf_label(target) or "")
                    ):
                        # Some archive-backed UK effects are labeled as word-level
                        # substitutions even though the affecting source provides
                        # the fully substituted structural node text. When we
                        # already extracted a typed payload and no quoted fragment
                        # can be recovered, treat this as a structural replace
                        # rather than silently dropping the effect.
                        curr_action = "replace"
                    elif is_word_level:
                        # We couldn't extract the fragment for a word-level effect.
                        # Do NOT replace the whole node text with the amendment instruction!
                        curr_action = None

        if curr_action:
            preceding_eid = None
            if "after " in effect.comments.lower():
                rel_m = re.search(r"after (?:paragraph|section|ss\.|s\.)\s?\(?([0-9a-zA-Z]+)\)?", effect.comments, re.I)
                if rel_m:
                    num = rel_m.group(1)
                    preceding_eid = f"p1-{num}" if "paragraph" in effect.comments.lower() else f"section-{num}"

            # Build payload IRNode (None when fragment substitution handles content)
            payload_node_mut: Optional[UKMutableNode] = _to_mutable_node(content_ir) if content_ir else None
            if (
                payload_node_mut is not None
                and replacement_leaf_override
                and replacement_leaf_kind
                and payload_node_mut.kind == replacement_leaf_kind
            ):
                payload_node_mut.label = replacement_leaf_override
            if payload_node_mut is not None and curr_action == "insert":
                leaf_kind = _addr_leaf_kind(target) or ""
                leaf_label = _addr_leaf_label(target) or ""
                if (
                    leaf_kind
                    and leaf_label
                    and payload_node_mut.kind == leaf_kind
                    and not _clean_num(payload_node_mut.label or "")
                ):
                    payload_node_mut.label = leaf_label
                leafish_kinds = {"subsection", "paragraph", "subparagraph", "item", "point"}
                if (
                    leaf_kind in leafish_kinds
                    and payload_node_mut.kind in leafish_kinds
                    and payload_node_mut.kind != leaf_kind
                    and _clean_num(payload_node_mut.label or "") == _clean_num(leaf_label)
                ):
                    payload_node_mut.kind = cast(IRNodeKind, leaf_kind)

            if curr_action in ("insert", "replace") and _is_non_substantive_structural_payload(payload_node_mut):
                continue
            payload_node = payload_node_mut.to_irnode() if payload_node_mut is not None else None
            if curr_action == "text_repeal" and op_text_match:
                text_patch = TextPatchSpec(
                    kind=TextPatchKindEnum.DELETE,
                    selector=TextSelector(
                        match_text=op_text_match,
                        occurrence=op_text_occurrence,
                    ),
                )
            elif curr_action == "text_replace" and op_text_match and op_text_replacement is not None:
                text_patch = TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(
                        match_text=op_text_match,
                        occurrence=op_text_occurrence,
                    ),
                    replacement=op_text_replacement,
                )

            # Build source
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )

            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            text_rewrite_witness = _uk_text_rewrite_spec(
                fragment_subs=fragment_subs,
                text_patch=text_patch,
                op_text_match=op_text_match,
                op_text_replacement=op_text_replacement,
                op_text_occurrence=op_text_occurrence,
            )
            insertion_anchor_witness = _uk_insertion_anchor_witness(preceding_eid)
            lowered_witness = UKLoweredOperationWitness(
                op_id=f"{effect.effect_id}_{len(ops)}" if len(targets_str) > 1 else effect.effect_id,
                sequence=sequence,
                action=_to_structural_action(curr_action),
                target=target,
                payload=payload_node,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=text_rewrite_witness,
                insertion_anchor_witness=insertion_anchor_witness,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=lowered_witness.action,
                    target=lowered_witness.target,
                    payload=_payload_with_rewrite_witness(lowered_witness.payload, lowered_witness),
                    source=lowered_witness.source,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                    text_patch=text_patch,
                )
            )
    if action == "replace" and trailing_repeal_refs:
        src = OperationSource(
            statute_id=effect.affecting_act_id,
            title=effect.affecting_title,
            effective=effect_witness.applicability.effective_date or "",
            raw_text=extraction_witness.extracted_text,
        )
        for repeal_idx, repeal_ref in enumerate(trailing_repeal_refs):
            repeal_target = _parse_affected_target(repeal_ref)
            target_expansion_witness = _uk_target_expansion_witness(
                repeal_ref,
                [repeal_ref],
                original_targets_str=original_targets_str,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=f"{effect.effect_id}_repeal_{repeal_idx}",
                sequence=sequence,
                action=StructuralAction.REPEAL,
                target=repeal_target,
                payload=None,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=lowered_witness.action,
                    target=lowered_witness.target,
                    payload=None,
                    source=lowered_witness.source,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                )
            )
    return ops


# ---------------------------------------------------------------------------
# Replay Pipeline
# ---------------------------------------------------------------------------


class UKReplayPipeline:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    @staticmethod
    def _should_replay_nonstructural_ops(
        effect: UKEffectRecord,
        compiled_ops: list[LegalOperation],
        *,
        applicability_mode: str = "effective_date_plus_feed_applied",
    ) -> bool:
        """Admit a narrow false-negative effects-feed class into replay.

        Some UK effects rows that are marked non-structural in the feed are
        actually structural:

        - sibling-range substitutions whose extracted source compiles to
          multiple structural replace ops
        - revoked rows whose extracted source compiles to one or more
          structural repeal ops

        Replaying these narrow classes is lower regret than trusting the feed
        flag blindly.
        """
        if not effect.is_applicable_for_replay(applicability_mode=applicability_mode):
            return False
        effect_type = (effect.effect_type or "").strip().lower()
        if effect_type.startswith("substituted for"):
            if not compiled_ops:
                return False
            head, *tail = compiled_ops
            if _action_name(head.action) != "replace" or head.payload is None:
                return False
            if all(_action_name(op.action) == "replace" and op.payload is not None for op in compiled_ops):
                return True
            return all(_action_name(op.action) == "repeal" and op.target.path for op in tail)
        if effect_type.startswith("revoked"):
            return bool(compiled_ops) and all(_action_name(op.action) == "repeal" and op.target.path for op in compiled_ops)
        if effect_type.startswith("ceases to have effect"):
            return bool(compiled_ops) and all(_action_name(op.action) == "repeal" and op.target.path for op in compiled_ops)
        return False

    @staticmethod
    def _nonstructural_replay_candidate_family(
        effect: UKEffectRecord,
        *,
        applicability_mode: str = "effective_date_plus_feed_applied",
    ) -> str:
        """Return the nonstructural effect row family that may still replay."""
        if not effect.is_applicable_for_replay(applicability_mode=applicability_mode):
            return ""
        effect_type = (effect.effect_type or "").strip().lower()
        if effect_type.startswith("substituted for"):
            return "substituted_for_series"
        if effect_type.startswith("revoked"):
            return "revoked_repeal"
        if effect_type.startswith("ceases to have effect"):
            return "ceases_to_have_effect_repeal"
        return ""

    def compile_ops_for_statute(
        self,
        affected_act_id: str,
        pit_date: Optional[str] = None,
        archive: Optional[Any] = None,
        allow_metadata_backfill: bool = True,
        applicability_mode: str = "effective_date_plus_feed_applied",
        authority_mode: str = "current_mixed",
        authority_rejections_out: Optional[list[dict[str, Any]]] = None,
        lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
        effect_feed_parse_rejections_out: Optional[list[dict[str, Any]]] = None,
    ) -> list[LegalOperation]:
        """Compile IR ops for *affected_act_id*.

        UK replay is archive-backed. Effects feeds and affecting act XMLs are
        loaded from the Farchive DB; deprecated on-disk XML fallbacks are
        intentionally not used.
        """
        if archive is None:
            raise ValueError(
                "UKReplayPipeline.compile_ops_for_statute requires archive-backed "
                "effects/XML; deprecated on-disk XML inputs have been removed"
            )

        # ── Load effects ────────────────────────────────────────────────────
        if effect_feed_parse_rejections_out is None:
            effects = load_effects_for_statute_from_archive(affected_act_id, archive)
        else:
            effects = load_effects_for_statute_from_archive(
                affected_act_id,
                archive,
                parse_rejections_out=effect_feed_parse_rejections_out,
            )

        replayable = list(effects)
        if pit_date:
            replayable = [e for e in replayable if (e.effective_date or "9999-99-99") <= pit_date]

        def _sort_key(e: UKEffectRecord) -> tuple:
            return (
                e.effective_date or "9999-99-99",
                e.modified,
                e.effect_id,
            )

        replayable.sort(key=_sort_key)

        ops = []
        extraction_cache: dict[
            str,
            tuple[
                Optional[bytes],
                Optional[ET.Element],
                Optional[dict[ET.Element, ET.Element]],
                dict[str, ET.Element],
                dict[tuple[str, ...], ET.Element],
            ],
        ] = {}
        for i, e in enumerate(replayable):
            el: Optional[ET.Element] = None
            xml_bytes: Optional[bytes]
            root: Optional[ET.Element]
            parent_map: Optional[dict[ET.Element, ET.Element]]
            exact_id_map: dict[str, ET.Element]
            sequence_map: dict[tuple[str, ...], ET.Element]

            if e.affecting_act_id in extraction_cache:
                xml_bytes, root, parent_map, exact_id_map, sequence_map = extraction_cache[e.affecting_act_id]
            else:
                xml_bytes = get_affecting_act_xml_from_archive(e.affecting_act_id, archive)
                root = None
                parent_map = None
                exact_id_map = {}
                sequence_map = {}
                if xml_bytes:
                    try:
                        root = ET.fromstring(xml_bytes)
                    except ET.ParseError:
                        root = None
                    if root is not None:
                        parent_map, exact_id_map, sequence_map = _build_extraction_context(root)
                extraction_cache[e.affecting_act_id] = (xml_bytes, root, parent_map, exact_id_map, sequence_map)
            if xml_bytes and root is not None:
                el = extract_provision_element_from_bytes(
                    xml_bytes,
                    e.affecting_provisions,
                    root=root,
                    parent_map=parent_map,
                    exact_id_map=exact_id_map,
                    sequence_map=sequence_map,
                )

            structural_for_replay = e.is_structural_for_replay(applicability_mode=applicability_mode)
            compiled = compile_effect_to_ir_ops(
                e,
                el,
                sequence=i,
                fallback_for_missing_extracted_source=(xml_bytes is None and allow_metadata_backfill),
            )
            if not compiled:
                if structural_for_replay and lowering_rejections_out is not None:
                    lowering_rejections_out.append(
                        {
                            "rule_id": "uk_effect_lowering_no_ops_rejected",
                            "family": "lowering_filter",
                            "phase": "lowering",
                            "effect_id": e.effect_id,
                            "affecting_act_id": e.affecting_act_id,
                            "affected_provisions": e.affected_provisions,
                            "affecting_provisions": e.affecting_provisions,
                            "effect_type": e.effect_type,
                            "reason": "UK structural effect lowered to no replay operations",
                            "blocking": True,
                            "strict_disposition": "block",
                            "quirks_disposition": "record",
                        }
                    )
                if not structural_for_replay and lowering_rejections_out is not None:
                    nonstructural_candidate_family = self._nonstructural_replay_candidate_family(
                        e,
                        applicability_mode=applicability_mode,
                    )
                    if nonstructural_candidate_family:
                        lowering_rejections_out.append(
                            {
                                "rule_id": "uk_effect_nonstructural_lowering_no_ops_rejected",
                                "family": "lowering_filter",
                                "phase": "lowering",
                                "effect_id": e.effect_id,
                                "affecting_act_id": e.affecting_act_id,
                                "affected_provisions": e.affected_provisions,
                                "affecting_provisions": e.affecting_provisions,
                                "effect_type": e.effect_type,
                                "reason": "UK nonstructural effect row may be replayable but lowered to no replay operations",
                                "blocking": True,
                                "strict_disposition": "block",
                                "quirks_disposition": "record",
                                "nonstructural_replay_candidate_family": nonstructural_candidate_family,
                            }
                        )
                continue
            if authority_mode == "source_text_only":
                rejected_ops: list[LegalOperation] = []
                rejected_reason_counts: dict[str, int] = {}
                for op in compiled:
                    allowed, rejection_reason = _uk_op_allowed_by_authority_mode(op, authority_mode)
                    if allowed:
                        continue
                    rejected_ops.append(op)
                    if rejection_reason:
                        rejected_reason_counts[rejection_reason] = rejected_reason_counts.get(rejection_reason, 0) + 1
                if rejected_ops and authority_rejections_out is not None:
                    authority_rejections_out.append(
                        {
                            "rule_id": "uk_effect_authority_filter_rejected",
                            "family": "authority_filter",
                            "phase": "lowering",
                            "effect_id": e.effect_id,
                            "affecting_act_id": e.affecting_act_id,
                            "affected_provisions": e.affected_provisions,
                            "affecting_provisions": e.affecting_provisions,
                            "authority_mode": authority_mode,
                            "rejected_op_count": len(rejected_ops),
                            "kept_op_count": len(compiled) - len(rejected_ops),
                            "rejected_authority_layers": sorted(
                                {
                                    str(
                                        getattr(
                                            getattr(_witness_for_op(op), "extraction_witness", None),
                                            "authority_layer",
                                            "",
                                        )
                                        or ""
                                    )
                                    for op in rejected_ops
                                    if str(
                                        getattr(
                                            getattr(_witness_for_op(op), "extraction_witness", None),
                                            "authority_layer",
                                            "",
                                        )
                                        or ""
                                    )
                                }
                            ),
                            "rejected_reasons": sorted(rejected_reason_counts),
                            "rejected_reason_counts": rejected_reason_counts,
                            "reason": "UK source-text-only authority mode rejected non-source-text replay operations",
                            "blocking": True,
                            "strict_disposition": "block",
                            "quirks_disposition": "record",
                        }
                    )
                compiled = [op for op in compiled if _uk_op_allowed_by_authority_mode(op, authority_mode)[0]]
                if not compiled:
                    continue
            if structural_for_replay:
                from lawvm.uk_legislation.source_adjudication import (
                    classify_uk_effect_source_pathology,
                )

                extracted_tag = el.tag.rsplit("}", 1)[-1] if el is not None else None
                extracted_text = " ".join(t.strip() for t in el.itertext() if t and t.strip()) if el is not None else ""
                source_pathology = classify_uk_effect_source_pathology(
                    extracted_tag=extracted_tag,
                    extracted_text=extracted_text,
                    op_actions=[_action_name(op.action) for op in compiled],
                    payload_kinds=[str(op.payload.kind) for op in compiled if op.payload is not None],
                    payload_texts=[op.payload.text or "" for op in compiled if op.payload is not None],
                    target_paths=["/".join(f"{kind}:{label}" for kind, label in op.target.path) for op in compiled],
                    effect_type=e.effect_type,
                    is_structural=structural_for_replay,
                )
                if source_pathology == "instruction_text_reused_as_payload" and any(
                    _action_name(op.action) in {"insert", "replace"} for op in compiled
                ):
                    if lowering_rejections_out is not None:
                        lowering_rejections_out.append(
                            {
                                "rule_id": "uk_effect_instruction_text_payload_rejected",
                                "family": "source_pathology_filter",
                                "phase": "lowering",
                                "effect_id": e.effect_id,
                                "affecting_act_id": e.affecting_act_id,
                                "affected_provisions": e.affected_provisions,
                                "affecting_provisions": e.affecting_provisions,
                                "effect_type": e.effect_type,
                                "reason": "UK effect payload reused instruction text rather than source legal payload",
                                "blocking": True,
                                "strict_disposition": "block",
                                "quirks_disposition": "record",
                                "source_pathology": source_pathology,
                            }
                        )
                    continue
            if structural_for_replay or self._should_replay_nonstructural_ops(
                e,
                compiled,
                applicability_mode=applicability_mode,
            ):
                ops.extend(compiled)

        return _order_schedule_materialization_ops(ops)

    def apply_ops(
        self,
        base_ir: IRStatute,
        ops: list[LegalOperation],
        eid_map: Optional[dict[str, str]] = None,
        text_map: Optional[dict[str, str]] = None,
        allow_oracle_alignment: bool = True,
        verbose: bool = False,
        lo_ops_out: Optional[List[LegalOperation]] = None,
        adjudications_out: Optional[List[CompileAdjudication]] = None,
    ) -> IRStatute:
        executor = UKReplayExecutor(
            base_ir,
            eid_map=eid_map if allow_oracle_alignment else None,
            text_map=text_map if allow_oracle_alignment else None,
            verbose=verbose,
            lo_ops_out=lo_ops_out,
            adjudications_out=adjudications_out,
        )
        for op in _prepare_replay_uk_ops(
            ops,
            verbose=verbose,
            adjudications_out=adjudications_out,
        ):
            executor.apply_op(op)
        return executor.statute.to_irstatute()


# ---------------------------------------------------------------------------
# Replay Executor
# ---------------------------------------------------------------------------


def _normalize_text_for_grounding(text: str) -> str:
    """Normalize text for grounding similarity checks."""
    # Strip punctuation and normalize whitespace
    text = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(text.split())


class UKReplayExecutor:
    def __init__(
        self,
        statute: IRStatute,
        eid_map: Optional[dict[str, str]] = None,
        text_map: Optional[dict[str, str]] = None,
        verbose: bool = False,
        lo_ops_out: Optional[List[LegalOperation]] = None,
        adjudications_out: Optional[List[CompileAdjudication]] = None,
    ):
        self.statute = UKMutableStatute.from_irstatute(statute)
        self.eid_map = eid_map or {}
        self.text_map = text_map or {}
        self.verbose = bool(verbose)
        self.lo_ops_out = lo_ops_out  # None = don't collect snapshots
        self.adjudications_out = adjudications_out
        self._seen_invariant_violations = self._collect_invariant_violations()
        self._repealed_target_prefixes: set[str] = set()

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def _replace_statute(
        self,
        *,
        body: Optional[UKMutableNode] = None,
        supplements: Optional[list[UKMutableNode]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Replace the UK-local mutable runtime state."""
        if body is not None:
            self.statute.body = body
        if supplements is not None:
            self.statute.supplements = list(supplements)
        if metadata is not None:
            self.statute.metadata = dict(metadata)

    def _find_path_to_node(
        self,
        root: UKMutableNode,
        target_node: UKMutableNode,
        path: tuple[int, ...] = (),
    ) -> Optional[tuple[int, ...]]:
        if root is target_node:
            return path
        for i, child in enumerate(root.children):
            found = self._find_path_to_node(child, target_node, path + (i,))
            if found is not None:
                return found
        return None

    def _replace_descendant_at_path(
        self,
        root: UKMutableNode,
        path: tuple[int, ...],
        new_node: UKMutableNode,
    ) -> UKMutableNode:
        if not path:
            return new_node
        idx = path[0]
        root.children[idx] = self._replace_descendant_at_path(root.children[idx], path[1:], new_node)
        return root

    def _replace_node_in_statute(self, old_node: UKMutableNode, new_node: UKMutableNode) -> bool:
        if self.statute.body is old_node:
            self.statute.body = new_node
            return True
        body_path = self._find_path_to_node(self.statute.body, old_node)
        if body_path is not None:
            self._replace_descendant_at_path(self.statute.body, body_path, new_node)
            return True
        for idx, root in enumerate(self.statute.supplements):
            if root is old_node:
                self.statute.supplements[idx] = new_node
                return True
            sub_path = self._find_path_to_node(root, old_node)
            if sub_path is not None:
                self._replace_descendant_at_path(root, sub_path, new_node)
                return True
        return False

    def _replace_children(self, node: UKMutableNode, new_children: list[UKMutableNode]) -> bool:
        node.children = list(new_children)
        return True

    def _replace_text(self, node: UKMutableNode, new_text: str) -> bool:
        node.text = new_text
        return True

    def _replace_text_and_children(
        self,
        node: UKMutableNode,
        *,
        text: str,
        children: list[UKMutableNode],
    ) -> bool:
        node.text = text
        node.children = list(children)
        return True

    def _replace_attrs(self, node: UKMutableNode, attrs: dict[str, Any]) -> bool:
        node.attrs = dict(attrs)
        return True

    def _remove_node(self, node: UKMutableNode, parent: Optional[UKMutableNode], idx: Optional[int]) -> bool:
        if parent is not None and idx is not None:
            parent.children.pop(idx)
            return True
        for s_idx, root in enumerate(self.statute.supplements):
            if root is node:
                self.statute.supplements.pop(s_idx)
                return True
        return False

    def _insert_child_sorted(self, parent: UKMutableNode, new_node: UKMutableNode) -> bool:
        from lawvm.uk_legislation.canonicalize import uk_insert_into_children

        uk_insert_into_children(
            cast(list[IRNode], parent.children),
            cast(IRNode, new_node),
            label_sort_key=_label_sort_key,
        )
        return True

    def _insert_supplement_sorted(self, new_node: UKMutableNode) -> bool:
        from lawvm.uk_legislation.canonicalize import uk_insert_into_children

        uk_insert_into_children(
            cast(list[IRNode], self.statute.supplements),
            cast(IRNode, new_node),
            label_sort_key=_label_sort_key,
        )
        return True

    def _collect_invariant_violations(self) -> set[str]:
        violations: set[str] = set()
        targets: list[tuple[str, UKMutableNode]] = [("body", self.statute.body)]
        targets.extend((f"schedule:{schedule.label or '?'}", schedule) for schedule in self.statute.supplements)
        for root_name, node in targets:
            for violation in tree_ops.check_invariants(cast(IRNode, node)):
                if "duplicate " not in violation and " out of order:" not in violation:
                    continue
                violations.add(f"{root_name}:{violation}")
        return violations

    def _payload_shape_invariant_violations(self, op: LegalOperation) -> list[str]:
        payload = getattr(op, "payload", None)
        if payload is None or _action_name(op.action) not in {"insert", "replace"}:
            return []
        violations: list[str] = []
        for violation in tree_ops.check_invariants(payload):
            if "duplicate " not in violation and " out of order:" not in violation:
                continue
            violations.append(violation)
        return violations

    def _payload_container_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "duplicate part:" not in scoped_violation.lower():
            return False
        payload = getattr(op, "payload", None)
        if payload is None or _action_name(op.action) != "replace":
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() != "part":
            return False
        payload_kind = str(getattr(payload, "kind", "") or "").lower()
        payload_label = _clean_num(str(getattr(payload, "label", "") or ""))
        return payload_kind == "part" and payload_label in {"", "part"}

    def _part_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "part out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path:
            return False
        part_labels = [str(label or "") for kind, label in target_path if str(kind or "").lower() == "part"]
        if not part_labels:
            return False
        leaf_kind = "part"
        leaf_text = _clean_num(part_labels[-1])
        violation = str(scoped_violation or "")
        match = re.search(r"part out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)

        def normalize(text: str) -> str:
            return re.sub(r"^(?:part)\s*", "", text.strip(), flags=re.I)

        def numeric(text: str) -> bool:
            return bool(re.fullmatch(r"\d+", normalize(text)))

        def roman(text: str) -> bool:
            return bool(re.fullmatch(r"(?:part)?[ivxlcdm]+", text, re.I))

        schedule_labels = [
            _clean_num(str(label or "")) for kind, label in target_path if str(kind or "").lower() == "schedule"
        ]
        if (
            str(leaf_kind or "").lower() == "part"
            and schedule_labels
            and any(re.fullmatch(r"\d+[a-z]+", label, re.I) for label in schedule_labels if label)
        ):
            return True
        if re.fullmatch(r"(?:[a-z]+\d+[a-z0-9]*|\d+[a-z][a-z0-9]*)", leaf_text):
            return True
        if match is None:
            return False
        left = _clean_num(normalize(match.group(1)))
        right = _clean_num(normalize(match.group(2)))
        return (numeric(left) and roman(right)) or (roman(left) and numeric(right))

    def _chapter_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "chapter out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() != "chapter":
            return False
        violation = str(scoped_violation or "")
        match = re.search(r"chapter out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False

        def normalize(text: str) -> str:
            return re.sub(r"^(?:chapter)\s*", "", text.strip(), flags=re.I)

        left = _clean_num(normalize(match.group(1)))
        right = _clean_num(normalize(match.group(2)))

        def mixed(text: str) -> bool:
            return bool(re.fullmatch(r"(?:[a-z]+\d+[a-z0-9]*|\d+[a-z][a-z0-9]*)", text, re.I))

        def numeric(text: str) -> bool:
            return bool(re.fullmatch(r"\d+", text))

        return (
            (numeric(left) and mixed(right))
            or (mixed(left) and numeric(right))
            or (mixed(left) and mixed(right))
            or left == right
        )

    def _section_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "section out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() != "section":
            return False
        leaf_text = _clean_num(str(target_path[-1][1] or ""))
        violation = str(scoped_violation or "")

        def mixed(text: str) -> bool:
            return bool(
                re.fullmatch(
                    r"(?:\d+[a-z]+\d+[a-z0-9]*|\d+[a-z]{2,}|\d+[a-z]\d[a-z0-9]*|[a-z]+\d+[a-z0-9]*)", text, re.I
                )
            )

        if mixed(leaf_text):
            return True
        if leaf_text and not re.fullmatch(r"\d+[a-z]*", leaf_text, re.I):
            return True
        match = re.search(r"section out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False
        left = _clean_num(match.group(1))
        right = _clean_num(match.group(2))

        def numeric(text: str) -> bool:
            return bool(re.fullmatch(r"\d+", text))

        return (numeric(left) and mixed(right)) or (mixed(left) and numeric(right)) or (mixed(left) and mixed(right))

    def _paragraph_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "paragraph out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path:
            return False
        paragraph_labels = [str(label or "") for kind, label in target_path if str(kind or "").lower() == "paragraph"]
        if not paragraph_labels:
            return False
        leaf_text = _clean_num(paragraph_labels[-1])

        def mixed(text: str) -> bool:
            return bool(re.fullmatch(r"(?:\d+[a-z][a-z0-9]*|[a-z]+\d+[a-z0-9]*)", text, re.I))

        def pure_alpha(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]+", text, re.I))

        def pure_num(text: str) -> bool:
            return bool(re.fullmatch(r"\d+", text))

        def pure_roman(text: str) -> bool:
            return bool(re.fullmatch(r"[ivxlcdm]+", text, re.I))

        def alpha_suffix(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]{2,}", text, re.I))

        if mixed(leaf_text) or alpha_suffix(leaf_text):
            return True
        violation = str(scoped_violation or "")
        match = re.search(r"paragraph out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False
        left = _clean_num(match.group(1))
        right = _clean_num(match.group(2))
        return (
            (mixed(left) and pure_alpha(right))
            or (pure_alpha(left) and mixed(right))
            or (mixed(left) and pure_num(right))
            or (pure_num(left) and mixed(right))
            or (mixed(left) and mixed(right))
            or (pure_num(left) and pure_alpha(right))
            or (pure_alpha(left) and pure_num(right))
            or (alpha_suffix(left) and pure_alpha(right))
            or (pure_alpha(left) and alpha_suffix(right))
            or (pure_roman(left) and pure_alpha(right))
            or (pure_alpha(left) and pure_roman(right))
            or (alpha_suffix(left) and pure_roman(right))
            or (pure_roman(left) and alpha_suffix(right))
        )

    def _subparagraph_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "subparagraph out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() != "subparagraph":
            return False
        leaf_text = _clean_num(str(target_path[-1][1] or ""))

        def pure_roman(text: str) -> bool:
            return bool(re.fullmatch(r"[ivxlcdm]+", text, re.I))

        def alpha_suffix(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]{2,}", text, re.I))

        def mixed(text: str) -> bool:
            return bool(re.fullmatch(r"(?:\d+[a-z][a-z0-9]*|[a-z]+\d+[a-z0-9]*|[ivxlcdm]+[a-z]+)", text, re.I))

        if mixed(leaf_text) or alpha_suffix(leaf_text):
            return True
        violation = str(scoped_violation or "")
        match = re.search(r"subparagraph out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False
        left = _clean_num(match.group(1))
        right = _clean_num(match.group(2))
        return bool(
            (mixed(left) and pure_roman(right))
            or (pure_roman(left) and mixed(right))
            or (re.fullmatch(r"\d+", left) and mixed(right))
            or (mixed(left) and re.fullmatch(r"\d+", right))
            or (mixed(left) and mixed(right))
            or (alpha_suffix(left) and pure_roman(right))
            or (pure_roman(left) and alpha_suffix(right))
            or (alpha_suffix(left) and alpha_suffix(right))
            or (re.fullmatch(r"\d+", left) and alpha_suffix(right))
            or (alpha_suffix(left) and re.fullmatch(r"\d+", right))
        )

    def _item_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "item out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() not in {"subparagraph", "item", "point"}:
            return False
        in_schedule = any(str(kind or "").lower() == "schedule" for kind, _ in target_path)
        raw_leaf_text = str(target_path[-1][1] or "").strip().lower()
        leaf_text = _clean_num(raw_leaf_text)

        def pure_roman(text: str) -> bool:
            return bool(re.fullmatch(r"[ivxlcdm]+", text, re.I))

        def pure_alpha(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]+", text, re.I))

        def pure_alpha_single(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]", text, re.I))

        def alpha_suffix(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]{2,}", text, re.I))

        def mixed(text: str) -> bool:
            return bool(re.fullmatch(r"(?:\d+[a-z][a-z0-9]*|[a-z]+\d+[a-z0-9]*|[ivxlcdm]+[a-z]+)", text, re.I))

        if mixed(leaf_text) or alpha_suffix(leaf_text):
            return True
        violation = str(scoped_violation or "")
        match = re.search(r"item out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False
        raw_left = str(match.group(1) or "").strip().lower()
        raw_right = str(match.group(2) or "").strip().lower()
        left = _clean_num(match.group(1))
        right = _clean_num(match.group(2))
        return bool(
            (mixed(left) and pure_roman(right))
            or (pure_roman(left) and mixed(right))
            or (re.fullmatch(r"\d+", left) and mixed(right))
            or (mixed(left) and re.fullmatch(r"\d+", right))
            or (mixed(left) and mixed(right))
            or (alpha_suffix(left) and pure_alpha(right))
            or (pure_alpha(left) and alpha_suffix(right))
            or (alpha_suffix(left) and pure_roman(right))
            or (pure_roman(left) and alpha_suffix(right))
            or (alpha_suffix(left) and alpha_suffix(right))
            or (
                in_schedule
                and pure_alpha_single(raw_leaf_text)
                and pure_alpha_single(raw_left)
                and pure_alpha_single(raw_right)
            )
        )

    def _replace_payload_kind_mismatch_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if _action_name(op.action) != "replace" or op.payload is None:
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path:
            return False
        target_kind = str(target_path[-1][0] or "").lower()
        payload_kind = str(getattr(op.payload, "kind", "") or "").lower()
        if payload_kind == target_kind:
            return False
        return (
            (
                target_kind == "subsection"
                and payload_kind == "paragraph"
                and "paragraph out of order:" in scoped_violation.lower()
            )
            or (
                target_kind == "paragraph"
                and payload_kind == "subparagraph"
                and "subparagraph out of order:" in scoped_violation.lower()
            )
            or (
                target_kind in {"subparagraph", "item", "point"}
                and payload_kind in {"item", "point"}
                and "duplicate " in scoped_violation.lower()
            )
        )

    def _record_invariant_violations(self, op: LegalOperation) -> None:
        current_violations = self._collect_invariant_violations()
        payload_shape_violations = self._payload_shape_invariant_violations(op)
        for scoped_violation in sorted(current_violations - self._seen_invariant_violations):
            if payload_shape_violations or self._payload_container_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_payload_shape_gap",
                    message="UK replay applied a payload that already violated order/duplication tree invariants.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                        "payload_violations": "; ".join(payload_shape_violations),
                    },
                )
            elif self._replace_payload_kind_mismatch_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_malformed_target_gap",
                    message="UK replay hit an invariant because the replace payload kind does not match the lowered target leaf.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                        "payload_kind": str(getattr(op.payload, "kind", "")) if op.payload is not None else "",
                    },
                )
            elif self._part_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_part_order_shape_gap",
                    message="UK replay hit a mixed-label part ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._chapter_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_chapter_order_shape_gap",
                    message="UK replay hit a mixed-label chapter ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._section_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_section_order_shape_gap",
                    message="UK replay hit an alphanumeric section ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._paragraph_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_paragraph_order_shape_gap",
                    message="UK replay hit a mixed-label paragraph ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._subparagraph_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_subparagraph_order_shape_gap",
                    message="UK replay hit a mixed-label subparagraph ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._item_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_item_order_shape_gap",
                    message="UK replay hit a mixed-label item ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_tree_invariant_violation",
                    message="UK replay violated order/duplication tree invariant after applying an op.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
        self._seen_invariant_violations = current_violations

    def _record_repealed_target(self, target: LegalAddress) -> None:
        target_text = str(target or "").strip()
        if target_text:
            self._repealed_target_prefixes.add(target_text)

    def _target_under_repealed_prefix(self, target: LegalAddress) -> bool:
        target_text = str(target or "").strip()
        if not target_text:
            return False
        for prefix in self._repealed_target_prefixes:
            if target_text == prefix or target_text.startswith(prefix + "/"):
                return True
        return False

    def _table_target_shape_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if not path:
            return False
        return any(_clean_num(label or "") == "table" for _, label in path)

    def _schedule_unlabeled_paragraph_target_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or len(path) < 3:
            return False
        root_kind, root_label = path[0]
        if str(root_kind or "").lower() != "schedule":
            return False
        paragraph_segments = [
            re.sub(r"[^0-9a-z]+", "", str(label or "").lower())
            for kind, label in path
            if str(kind or "").lower() == "paragraph"
        ]
        if not paragraph_segments or not any(label.isdigit() for label in paragraph_segments if label):
            return False
        want = _clean_num(root_label or "")
        root_node = None
        for schedule in getattr(self.statute, "supplements", []) or []:
            if str(getattr(schedule, "kind", "") or "").lower() != "schedule":
                continue
            have = _clean_num(getattr(schedule, "label", "") or "")
            if have == want or have.endswith(want):
                root_node = schedule
                break
        if root_node is None:
            return False
        paragraph_labels: list[str] = []
        subparagraph_labels: list[str] = []
        stack = list(getattr(root_node, "children", []) or [])
        while stack:
            curr = stack.pop()
            curr_kind = str(getattr(curr, "kind", "") or "").lower()
            if curr_kind == "paragraph":
                paragraph_labels.append(re.sub(r"[^0-9a-z]+", "", str(getattr(curr, "label", "") or "").lower()))
            elif curr_kind == "subparagraph":
                subparagraph_labels.append(re.sub(r"[^0-9a-z]+", "", str(getattr(curr, "label", "") or "").lower()))
            stack.extend(list(getattr(curr, "children", []) or []))
        leaf_kind = str(path[-1][0] or "").lower()
        return (
            bool(paragraph_labels)
            and not any(paragraph_labels)
            and bool(subparagraph_labels)
            and leaf_kind
            in {
                "subparagraph",
                "item",
                "point",
            }
        )

    def _malformed_target_gap(self, target: LegalAddress) -> bool:
        def _descendant_labels(node: UKMutableNode, *, kinds: set[str]) -> list[str]:
            out: list[str] = []
            stack = list(getattr(node, "children", []) or [])
            while stack:
                curr = stack.pop()
                curr_kind = str(getattr(curr, "kind", "") or "").lower()
                if curr_kind in kinds:
                    out.append(re.sub(r"[^0-9a-z]+", "", str(getattr(curr, "label", "") or "").lower()))
                stack.extend(list(getattr(curr, "children", []) or []))
            return out

        path = tuple(getattr(target, "path", ()) or ())
        if not path:
            return False
        if any(
            str(kind or "").lower() in {"item", "point", "paragraph", "subparagraph"}
            and bool(re.fullmatch(r"\[[^\]]+\]", str(label or "").strip()))
            for kind, label in path
        ):
            return True
        if any(_clean_num(label or "").lower() == "note" for _, label in path):
            return True
        if any(
            re.sub(r"[^0-9a-z]+", "", _clean_num(label or "").lower()) in {"crossheading", "crossheadings"}
            for _, label in path
        ):
            return True
        if (
            len(path) == 1
            and str(path[0][0] or "").lower() in {"section", "article", "rule", "regulation"}
            and not re.fullmatch(r"\d+[a-z]?", str(path[0][1] or "").strip().lower())
        ):
            return True
        if len(path) == 1 and str(path[0][0] or "").lower() in {"section", "article", "rule", "regulation"}:
            body_child_kinds = {
                str(getattr(child, "kind", "") or "").lower()
                for child in getattr(self.statute.body, "children", []) or []
            }
            if body_child_kinds and body_child_kinds <= {"part", "chapter", "division", "crossheading", "pblock"}:
                return True
        if _addr_container(target) == "schedule":
            first_kind, first_label = path[0]
            if first_kind == "schedule" and not _clean_num(first_label or ""):
                return True
        if len(path) >= 2:
            parent_target = LegalAddress(path=path[:-1], special=None)
            parent_node, _, _ = self._find_node_by_target(parent_target)
            leaf_kind, leaf_label = path[-1]
            textual_leaf = re.sub(r"[^0-9a-z]+", "", str(leaf_label or "").lower())
            is_roman = bool(re.fullmatch(r"[ivxlcdm]+", textual_leaf))
            is_alpha = bool(re.fullmatch(r"[a-z]+", textual_leaf))
            if (
                len(path) >= 2
                and str(path[-2][0] or "").lower() == "subsection"
                and re.fullmatch(r"[a-z]+", str(path[-2][1] or "").strip().lower())
                and str(path[-1][0] or "").lower() == "paragraph"
                and is_roman
            ):
                return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "paragraph"
                and is_roman
                and str(getattr(parent_node, "kind", "") or "").lower() == "subsection"
            ):
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "paragraph":
                        continue
                    for grandchild in getattr(child, "children", []) or []:
                        if str(getattr(grandchild, "kind", "") or "").lower() not in {"subparagraph", "item", "point"}:
                            continue
                        grandchild_label = re.sub(
                            r"[^0-9a-z]+",
                            "",
                            str(getattr(grandchild, "label", "") or "").lower(),
                        )
                        if grandchild_label == textual_leaf:
                            return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subparagraph"
                and is_alpha
                and str(getattr(parent_node, "kind", "") or "").lower() == "paragraph"
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() in {"subparagraph", "item", "point"}
                ]
                if child_labels and all(re.fullmatch(r"[ivxlcdm]+", label) for label in child_labels if label):
                    return True
                if child_labels and all(re.fullmatch(r"\d+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subparagraph"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() == "paragraph"
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"item", "point"}:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() in {"item", "point"}
                and str(getattr(parent_node, "kind", "") or "").lower() in {"item", "point", "subparagraph"}
                and textual_leaf.isdigit()
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() in {"item", "point"}
                ]
                if child_labels and all(re.fullmatch(r"[ivxlcdm]+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() in {"item", "point"}
                and str(getattr(parent_node, "kind", "") or "").lower() in {"item", "point", "subparagraph"}
                and is_alpha
                and len(textual_leaf) > 1
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() in {"item", "point"}
                ]
                if child_labels and all(re.fullmatch(r"[a-z]", label) for label in child_labels if label):
                    return True
                if textual_leaf[:1] in child_labels:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() in {"item", "point"}
                and str(getattr(parent_node, "kind", "") or "").lower() == "paragraph"
                and is_alpha
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subparagraph"
                ]
                if (
                    child_kinds
                    and child_kinds <= {"subparagraph"}
                    and child_labels
                    and all(re.fullmatch(r"\d+[a-z]?", label) for label in child_labels if label)
                ):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "paragraph"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() == "subsection"
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "paragraph"
                ]
                if child_labels and all(re.fullmatch(r"[a-z]+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "paragraph"
                and is_alpha
                and len(textual_leaf) > 1
                and str(getattr(parent_node, "kind", "") or "").lower() == "subsection"
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "paragraph"
                ]
                if child_labels and all(re.fullmatch(r"[a-z]", label) for label in child_labels if label):
                    return True
                first = textual_leaf[:1]
                rest = textual_leaf[1:]
                if rest and first in child_labels:
                    return True
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "paragraph":
                        continue
                    child_label = re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    if child_label != first:
                        continue
                    descendant_labels = [
                        re.sub(r"[^0-9a-z]+", "", str(getattr(grandchild, "label", "") or "").lower())
                        for grandchild in getattr(child, "children", []) or []
                        if str(getattr(grandchild, "kind", "") or "").lower() in {"subparagraph", "item", "point"}
                    ]
                    if rest and rest in descendant_labels:
                        return True
                last = textual_leaf[-1:]
                prefix = textual_leaf[:-1]
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "paragraph":
                        continue
                    child_label = re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    if child_label != last:
                        continue
                    descendant_labels = [
                        re.sub(r"[^0-9a-z]+", "", str(getattr(grandchild, "label", "") or "").lower())
                        for grandchild in getattr(child, "children", []) or []
                        if str(getattr(grandchild, "kind", "") or "").lower() in {"subparagraph", "item", "point"}
                    ]
                    if prefix and prefix in descendant_labels:
                        return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subsection"
                ]
                if child_labels and any(label == "" for label in child_labels):
                    return True
                if any(re.fullmatch(rf"{re.escape(textual_leaf)}[a-z]+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and _addr_container(target) == "schedule"
                and len(path) == 2
                and str(leaf_kind or "").lower() == "paragraph"
                and str(getattr(parent_node, "kind", "") or "").lower() == "schedule"
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if "part" in child_kinds:
                    return True
                if re.fullmatch(r"[a-z]+\d+", textual_leaf):
                    paragraph_labels = [
                        label for label in _descendant_labels(parent_node, kinds={"paragraph"}) if label
                    ]
                    if paragraph_labels and all(re.fullmatch(r"\d+[a-z]?", label) for label in paragraph_labels):
                        return True
            if self._schedule_unlabeled_paragraph_target_gap(target):
                return True
            if (
                parent_node is not None
                and _addr_container(target) == "schedule"
                and len(path) == 2
                and str(leaf_kind or "").lower() in {"part", "chapter", "division"}
                and str(getattr(parent_node, "kind", "") or "").lower() == "schedule"
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"crossheading", "pblock"}:
                    return True
            if (
                parent_node is not None
                and _addr_container(target) == "schedule"
                and str(leaf_kind or "").lower() == "paragraph"
                and str(getattr(parent_node, "kind", "") or "").lower() in {"part", "chapter", "division"}
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"crossheading", "pblock"}:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_kinds = [
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                ]
                if child_kinds and "subsection" not in child_kinds and "paragraph" in child_kinds:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and is_alpha
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_kinds = [
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                ]
                if child_kinds and "subsection" not in child_kinds and "paragraph" in child_kinds:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and re.fullmatch(r"\d+[a-z]{2,}", textual_leaf)
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subsection"
                ]
                if child_labels and all(re.fullmatch(r"\d+[a-z]?", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and len(path) == 2
                and _addr_container(target) == "schedule"
                and str(leaf_kind or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"part", "chapter", "division", "crossheading", "pblock"}:
                    return True
        return any(_clean_num(label or "") == "and" for _, label in path)

    def _missing_source_target_gap(self, op: LegalOperation) -> bool:
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        authority_layer = str(getattr(extraction, "authority_layer", "") or "")
        extraction_failure_kind = str(getattr(extraction, "extraction_failure_kind", "") or "")
        extracted_source_present = bool(getattr(extraction, "extracted_source_present", False))
        return (
            authority_layer == "EFFECT_FEED_INDEX"
            and not extracted_source_present
            and extraction_failure_kind == "missing_extracted_source"
        )

    def _empty_descendant_shape_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        return not bool(getattr(parent_node, "children", []) or [])

    def _annex_schedule_mismatch_gap(self, op: LegalOperation) -> bool:
        target = getattr(op, "target", None)
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 1 or str(path[0][0] or "").lower() != "schedule":
            return False
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        original_ref = str(getattr(extraction, "original_ref", "") or "")
        if "annex" not in original_ref.lower():
            for note in getattr(op, "provenance_tags", []) or []:
                if str(note or "").startswith("original_ref:") and "annex" in str(note or "").lower():
                    original_ref = str(note or "")
                    break
        if "annex" not in original_ref.lower():
            return False
        if target is None:
            return False
        node, _, _ = self._find_node_by_target(cast(LegalAddress, target))
        return node is None

    def _missing_parent_shape_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        return parent_node is None

    def _schedule_paragraph_carrier_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or len(path) < 3:
            return False
        if not any(str(kind or "").lower() == "paragraph" for kind, _ in path):
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        if leaf_kind not in {"subparagraph", "item", "point"}:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is not None and str(getattr(parent_node, "kind", "") or "").lower() == "p1group":
            return True
        grandparent_target = LegalAddress(path=path[:-2], special=None)
        grandparent_node, _, _ = self._find_node_by_target(grandparent_target)
        return grandparent_node is not None and parent_node is None

    def _leading_blank_subparagraph_gap(self, target: LegalAddress) -> bool:
        def _local_alnum_suffix_key(text: str) -> tuple[int, int] | None:
            m = re.fullmatch(r"(\d+)([a-z])", text.strip().lower())
            if not m:
                return None
            return (int(m.group(1)), ord(m.group(2)) - ord("a") + 1)

        path = tuple(getattr(target, "path", ()) or ())
        if not path:
            return False
        leaf_kind, leaf_label = path[-1]
        if str(leaf_kind or "").lower() != "subparagraph":
            return False
        text = str(leaf_label or "").strip().lower()
        want_pair = None
        if text.isdigit():
            want_num = int(text)
        elif re.fullmatch(r"\d+[a-z]", text):
            want_pair = _local_alnum_suffix_key(text)
            if want_pair is None:
                return False
            want_num = want_pair[0]
        else:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None or str(getattr(parent_node, "kind", "") or "").lower() != "paragraph":
            return False
        blank_present = False
        numeric_labels: list[int] = []
        numeric_pairs: list[tuple[int, int]] = []
        for child in getattr(parent_node, "children", []) or []:
            if str(getattr(child, "kind", "") or "").lower() != "subparagraph":
                continue
            raw = str(getattr(child, "label", "") or "").strip().lower()
            if not raw:
                blank_present = True
                continue
            if raw.isdigit():
                numeric_labels.append(int(raw))
                continue
            pair = _local_alnum_suffix_key(raw)
            if pair is not None:
                numeric_pairs.append(pair)
        if not blank_present:
            return False
        if want_pair is not None:
            if any(pair[0] == want_pair[0] and pair[1] > want_pair[1] for pair in numeric_pairs):
                return True
        if numeric_labels and want_num < min(numeric_labels):
            return True
        if numeric_pairs and want_num < min(pair[0] for pair in numeric_pairs):
            return True
        return False

    def _missing_schedule_branch_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2 or str(path[0][0] or "").lower() != "schedule":
            return False
        schedule_target = LegalAddress(path=path[:1], special=None)
        schedule_node, _, _ = self._find_node_by_target(schedule_target)
        return schedule_node is None

    def _prior_same_target_gap_kind(self, target: LegalAddress) -> str | None:
        want = str(target)
        prior = getattr(self, "adjudications_out", None) or []
        preferred = {
            "uk_replay_empty_descendant_shape_gap",
            "uk_replay_missing_parent_shape_gap",
            "uk_replay_malformed_target_gap",
            "uk_replay_repealed_target_gap",
            "uk_replay_table_shape_gap",
            "uk_replay_missing_source_target_gap",
        }
        for adjudication in reversed(prior):
            kind = str(getattr(adjudication, "kind", "") or "")
            if kind not in preferred:
                continue
            detail = getattr(adjudication, "detail", {}) or {}
            if str(detail.get("target", "") or "") == want:
                return kind
        return None

    def _missing_sibling_range_gap(self, target: LegalAddress) -> bool:
        # Roman numeral parser: shared implementation in lawvm.roman
        # rejects non-canonical spellings like "IIII" via round-trip
        # canonicalization.  The previous nested implementation had a
        # latent bug where ``prev`` only updated in the additive branch.
        _roman_to_int = _shared_roman_to_arabic

        def _alnum_suffix_key(text: str) -> tuple[int, int] | None:
            m = re.fullmatch(r"(\d+)([a-z])", text.lower())
            if not m:
                return None
            return (int(m.group(1)), ord(m.group(2)) - ord("a") + 1)

        def _alnum_multi_suffix_key(text: str) -> tuple[int, str] | None:
            m = re.fullmatch(r"(\d+)([a-z]{2,})", text.lower())
            if not m:
                return None
            return (int(m.group(1)), m.group(2))

        def _alpha_num_suffix_key(text: str) -> tuple[str, int] | None:
            m = re.fullmatch(r"([a-z]+)(\d+)", text.lower())
            if not m:
                return None
            return (m.group(1), int(m.group(2)))

        def _part_numeric_value(raw: str) -> int | None:
            text = str(raw or "").strip()
            if not text:
                return None
            text = re.sub(r"^(?:part)\s+", "", text, flags=re.I).strip()
            if text.isdigit():
                return int(text)
            roman = _roman_to_int(text)
            if roman is not None:
                return roman
            return None

        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind, leaf_label = path[-1]
        text = str(leaf_label or "").strip().lower()
        mode: str | None = None
        want: int
        want_pair: tuple[int, int] | None = None
        want_multi_pair: tuple[int, str] | None = None
        want_alpha_num_pair: tuple[str, int] | None = None
        if text.isdigit():
            mode = "numeric"
            want = int(text)
        elif re.fullmatch(r"[a-z]", text):
            mode = "alpha"
            want = ord(text) - ord("a") + 1
        elif re.fullmatch(r"[a-z]{2,}", text):
            mode = "alpha_suffix"
            want = ord(text[0]) - ord("a") + 1
        elif re.fullmatch(r"[ivxlcdm]+", text):
            roman = _roman_to_int(text)
            if roman is None:
                return False
            mode = "roman"
            want = roman
        elif re.fullmatch(r"\d+[a-z]", text):
            pair = _alnum_suffix_key(text)
            if pair is None:
                return False
            mode = "alnum_suffix"
            want = pair[0]
            want_pair = pair
        elif re.fullmatch(r"\d+[a-z]{2,}", text):
            pair = _alnum_multi_suffix_key(text)
            if pair is None:
                return False
            mode = "alnum_multi_suffix"
            want = pair[0]
            want_multi_pair = pair
        elif re.fullmatch(r"[a-z]+\d+", text):
            pair = _alpha_num_suffix_key(text)
            if pair is None:
                return False
            mode = "alpha_num_suffix"
            want = pair[1]
            want_alpha_num_pair = pair
        else:
            return False
        if len(path) == 1:
            parent_node = self.statute.body
        else:
            parent_target = LegalAddress(path=path[:-1], special=None)
            parent_node, _, _ = self._find_node_by_target(parent_target)
            if parent_node is None:
                return False
        if str(leaf_kind or "").lower() == "part" and text.isdigit():
            part_nums: list[int] = []
            for child in getattr(parent_node, "children", []) or []:
                if str(getattr(child, "kind", "") or "").lower() != "part":
                    continue
                num = _part_numeric_value(str(getattr(child, "label", "") or ""))
                if num is not None:
                    part_nums.append(num)
            if part_nums:
                part_nums = sorted(set(part_nums))
                want_num = int(text)
                lower = max((n for n in part_nums if n < want_num), default=None)
                upper = min((n for n in part_nums if n > want_num), default=None)
                if lower is not None and upper is not None and lower < want_num < upper:
                    return True
                if lower is None and part_nums and want_num < part_nums[0]:
                    return True
                if upper is None and part_nums and want_num > part_nums[-1]:
                    return True
        if str(leaf_kind or "").lower() == "part" and re.fullmatch(r"\d+[a-z]+", text):
            base_match = re.fullmatch(r"(\d+)[a-z]+", text)
            if base_match is not None:
                want_num = int(base_match.group(1))
                part_nums: list[int] = []
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "part":
                        continue
                    raw = str(getattr(child, "label", "") or "").strip()
                    base_num = _part_numeric_value(raw)
                    if base_num is not None:
                        part_nums.append(base_num)
                        continue
                    m = re.fullmatch(r"part\s+(\d+)[a-z]+", raw, re.I)
                    if m is not None:
                        part_nums.append(int(m.group(1)))
                if part_nums:
                    part_nums = sorted(set(part_nums))
                    lower = max((n for n in part_nums if n < want_num), default=None)
                    upper = min((n for n in part_nums if n > want_num), default=None)
                    if lower is not None and upper is not None and lower < want_num < upper:
                        return True
                    if any(n == want_num for n in part_nums):
                        return True
        sibling_labels: list[int] = []
        sibling_pairs: list[tuple[int, int]] = []
        sibling_multi_pairs: list[tuple[int, str]] = []
        sibling_alpha_num_pairs: list[tuple[str, int]] = []
        alpha_raw_labels: list[str] = []
        numeric_suffix_labels: list[int] = []
        alpha_suffix_labels: list[str] = []
        blank_same_kind_present = False
        for child in getattr(parent_node, "children", []) or []:
            child_kind = str(getattr(child, "kind", "") or "").lower()
            if child_kind == str(leaf_kind or "").lower():
                label_text = str(getattr(child, "label", "") or "").strip()
                if not label_text:
                    blank_same_kind_present = True
                if mode == "numeric" and label_text.isdigit():
                    sibling_labels.append(int(label_text))
                elif mode == "numeric" and (pair := _alnum_suffix_key(label_text)) is not None:
                    numeric_suffix_labels.append(int(pair[0]))
                elif mode == "alpha" and re.fullmatch(r"[a-z]", label_text.lower()):
                    sibling_labels.append(ord(label_text.lower()) - ord("a") + 1)
                elif mode == "alpha":
                    alpha_raw_labels.append(label_text.lower())
                elif mode == "alpha_suffix":
                    lowered = label_text.lower()
                    if re.fullmatch(r"[a-z]", lowered):
                        sibling_labels.append(ord(lowered) - ord("a") + 1)
                    else:
                        alpha_suffix_labels.append(lowered)
                elif mode == "roman" and re.fullmatch(r"[ivxlcdm]+", label_text.lower()):
                    roman = _roman_to_int(label_text)
                    if roman is not None:
                        sibling_labels.append(roman)
                elif mode == "alnum_suffix":
                    pair = _alnum_suffix_key(label_text)
                    if pair is not None:
                        sibling_pairs.append(pair)
                    elif label_text.isdigit():
                        numeric_suffix_labels.append(int(label_text))
                elif mode == "alnum_multi_suffix":
                    pair = _alnum_multi_suffix_key(label_text)
                    if pair is not None:
                        sibling_multi_pairs.append(pair)
                    elif (pair1 := _alnum_suffix_key(label_text)) is not None:
                        sibling_multi_pairs.append((pair1[0], chr(ord("a") + pair1[1] - 1)))
                    elif label_text.isdigit():
                        numeric_suffix_labels.append(int(label_text))
                elif mode == "alpha_num_suffix":
                    pair = _alpha_num_suffix_key(label_text)
                    if pair is not None:
                        sibling_alpha_num_pairs.append(pair)
                    elif re.fullmatch(r"[a-z]+", label_text.lower()):
                        alpha_raw_labels.append(label_text.lower())
                continue
            if uk_is_transparent_wrapper_kind(child_kind):
                for grandchild in getattr(child, "children", []) or []:
                    if str(getattr(grandchild, "kind", "") or "").lower() != str(leaf_kind or "").lower():
                        continue
                    label_text = str(getattr(grandchild, "label", "") or "").strip()
                    if not label_text:
                        blank_same_kind_present = True
                    if mode == "numeric" and label_text.isdigit():
                        sibling_labels.append(int(label_text))
                    elif mode == "numeric" and (pair := _alnum_suffix_key(label_text)) is not None:
                        numeric_suffix_labels.append(int(pair[0]))
                    elif mode == "alpha" and re.fullmatch(r"[a-z]", label_text.lower()):
                        sibling_labels.append(ord(label_text.lower()) - ord("a") + 1)
                    elif mode == "alpha":
                        alpha_raw_labels.append(label_text.lower())
                    elif mode == "alpha_suffix":
                        lowered = label_text.lower()
                        if re.fullmatch(r"[a-z]", lowered):
                            sibling_labels.append(ord(lowered) - ord("a") + 1)
                        else:
                            alpha_suffix_labels.append(lowered)
                    elif mode == "roman" and re.fullmatch(r"[ivxlcdm]+", label_text.lower()):
                        roman = _roman_to_int(label_text)
                        if roman is not None:
                            sibling_labels.append(roman)
                    elif mode == "alnum_suffix":
                        pair = _alnum_suffix_key(label_text)
                        if pair is not None:
                            sibling_pairs.append(pair)
                        elif label_text.isdigit():
                            numeric_suffix_labels.append(int(label_text))
                    elif mode == "alnum_multi_suffix":
                        pair = _alnum_multi_suffix_key(label_text)
                        if pair is not None:
                            sibling_multi_pairs.append(pair)
                        elif (pair1 := _alnum_suffix_key(label_text)) is not None:
                            sibling_multi_pairs.append((pair1[0], chr(ord("a") + pair1[1] - 1)))
                        elif label_text.isdigit():
                            numeric_suffix_labels.append(int(label_text))
                    elif mode == "alpha_num_suffix":
                        pair = _alpha_num_suffix_key(label_text)
                        if pair is not None:
                            sibling_alpha_num_pairs.append(pair)
                        elif re.fullmatch(r"[a-z]+", label_text.lower()):
                            alpha_raw_labels.append(label_text.lower())
        if mode == "alnum_multi_suffix":
            if want_multi_pair is None:
                return False
            if sibling_multi_pairs:
                sibling_multi_pairs = sorted(set(sibling_multi_pairs))
                lower = max((pair for pair in sibling_multi_pairs if pair < want_multi_pair), default=None)
                upper = min((pair for pair in sibling_multi_pairs if pair > want_multi_pair), default=None)
                if lower is not None or upper is not None:
                    return True
                if any(pair[0] == want_multi_pair[0] for pair in sibling_multi_pairs):
                    return True
            numeric_base_present = any(
                str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
                and str(getattr(child, "label", "") or "").strip().lower() == str(want_multi_pair[0])
                for child in getattr(parent_node, "children", []) or []
            )
            if numeric_base_present:
                return True
            if numeric_suffix_labels and want_multi_pair[0] in set(numeric_suffix_labels):
                return True
            return False
        if mode == "alpha_num_suffix":
            if want_alpha_num_pair is None:
                return False
            if sibling_alpha_num_pairs:
                sibling_alpha_num_pairs = sorted(set(sibling_alpha_num_pairs))
                same_prefix = [pair for pair in sibling_alpha_num_pairs if pair[0] == want_alpha_num_pair[0]]
                if same_prefix:
                    lower = max((pair for pair in same_prefix if pair[1] < want_alpha_num_pair[1]), default=None)
                    upper = min((pair for pair in same_prefix if pair[1] > want_alpha_num_pair[1]), default=None)
                    if lower is not None or upper is not None:
                        return True
            if any(label == want_alpha_num_pair[0] for label in alpha_raw_labels):
                return True
            return False
        if mode == "alnum_suffix":
            if not sibling_pairs or want_pair is None:
                # If the section still has the numeric base subsection (e.g. "6")
                # but the alpha extension (e.g. "6A") is absent, treat this as the
                # same stale/shape family as other missing sibling gaps.
                want_pair_base = want_pair[0] if want_pair is not None else None
                want_num = str(want_pair_base) if want_pair_base is not None else ""
                numeric_base_present = any(
                    str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
                    and str(getattr(child, "label", "") or "").strip().lower() == want_num
                    for child in getattr(parent_node, "children", []) or []
                )
                if numeric_suffix_labels and want_pair_base is not None:
                    nums = sorted(set(numeric_suffix_labels))
                    lower_num = max((n for n in nums if n < want_pair_base), default=None)
                    upper_num = min((n for n in nums if n > want_pair_base), default=None)
                    if lower_num is not None and upper_num is not None and lower_num < want_pair_base < upper_num:
                        return True
                    if lower_num is None and nums and want_pair_base < nums[0]:
                        return True
                    if upper_num is None and nums and want_pair_base > nums[-1]:
                        return True
                return numeric_base_present
            sibling_pairs = sorted(set(sibling_pairs))
            lower = max((pair for pair in sibling_pairs if pair < want_pair), default=None)
            upper = min((pair for pair in sibling_pairs if pair > want_pair), default=None)
            if lower is not None and upper is not None and lower < want_pair < upper:
                return True
            if lower is None and sibling_pairs and want_pair < sibling_pairs[0]:
                return True
            if upper is None and sibling_pairs and want_pair > sibling_pairs[-1]:
                return True
            same_num = [pair for pair in sibling_pairs if pair[0] == want_pair[0]]
            if same_num:
                lower_same = max((pair for pair in same_num if pair[1] < want_pair[1]), default=None)
                upper_same = min((pair for pair in same_num if pair[1] > want_pair[1]), default=None)
                if lower_same is not None or upper_same is not None:
                    return True
            numeric_base_present = any(
                str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
                and str(getattr(child, "label", "") or "").strip().lower() == str(want_pair[0])
                for child in getattr(parent_node, "children", []) or []
            )
            if numeric_base_present:
                return True
            if numeric_suffix_labels:
                nums = sorted(set(numeric_suffix_labels))
                lower_num = max((n for n in nums if n < want_pair[0]), default=None)
                upper_num = min((n for n in nums if n > want_pair[0]), default=None)
                if lower_num is not None and upper_num is not None and lower_num < want_pair[0] < upper_num:
                    return True
                if lower_num is None and nums and want_pair[0] < nums[0]:
                    return True
                if upper_num is None and nums and want_pair[0] > nums[-1]:
                    return True
            return False
        if mode == "alpha_suffix":
            if any(label.startswith(text) and len(label) > len(text) for label in alpha_suffix_labels):
                return True
            first = text[:1]
            if any(label == first for label in alpha_raw_labels):
                return True
            lower = max((n for n in sibling_labels if n < want), default=None)
            upper = min((n for n in sibling_labels if n > want), default=None)
            if lower is not None and upper is not None and lower < want < upper:
                return True
            if any(label.startswith(first) and len(label) > 1 for label in alpha_suffix_labels):
                return True
            return False
        if not sibling_labels:
            if mode == "numeric" and numeric_suffix_labels:
                nums = sorted(set(numeric_suffix_labels))
                lower_num = max((n for n in nums if n < want), default=None)
                upper_num = min((n for n in nums if n > want), default=None)
                if lower_num is not None and upper_num is not None and lower_num < want < upper_num:
                    return True
                if lower_num is None and nums and want < nums[0]:
                    return True
                if upper_num is None and nums and want > nums[-1]:
                    return True
            if mode == "alpha" and any(label.startswith(text) and len(label) > 1 for label in alpha_raw_labels):
                return True
            if mode == "alpha":
                repeated = sorted(label for label in alpha_raw_labels if re.fullmatch(r"([a-z])\1+", label))
                if repeated and any(rep < text for rep in repeated) and any(rep > text for rep in repeated):
                    return True
            return False
        if mode == "alpha":
            repeated = sorted(label for label in alpha_raw_labels if re.fullmatch(r"([a-z])\1+", label))
            if repeated and any(rep < text for rep in repeated) and any(rep > text for rep in repeated):
                return True
        sibling_labels = sorted(set(sibling_labels))
        if mode == "numeric" and blank_same_kind_present and sibling_labels and want < sibling_labels[0]:
            return True
        lower = max((label for label in sibling_labels if label < want), default=None)
        upper = min((label for label in sibling_labels if label > want), default=None)
        if lower is not None and upper is not None and lower < want < upper:
            return True
        if lower is None and sibling_labels and want < sibling_labels[0]:
            return True
        if upper is None and sibling_labels and want > sibling_labels[-1]:
            return True
        return False

    def _container_text_target_gap(self, op: LegalOperation) -> bool:
        target = getattr(op, "target", None)
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 2:
            return False
        if _addr_container(cast(LegalAddress, target)) != "schedule":
            return False
        leaf_kind, _ = path[-1]
        if str(leaf_kind or "").lower() not in {"part", "chapter"}:
            return False
        schedule_node, _, _ = self._find_node_by_target(LegalAddress(path=path[:1], special=None))
        if schedule_node is None:
            return False
        if any(
            str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
            for child in getattr(schedule_node, "children", []) or []
        ):
            return False
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        raw_text = str(getattr(extraction, "raw_text", "") or "")
        original_ref = str(getattr(extraction, "original_ref", "") or "")
        if not raw_text or not original_ref:
            for note in getattr(op, "provenance_tags", []) or []:
                note_text = str(note or "")
                if not raw_text and note_text.startswith("raw_text:"):
                    raw_text = note_text.partition(":")[2]
                elif not original_ref and note_text.startswith("original_ref:"):
                    original_ref = note_text.partition(":")[2]
        combined = f"{original_ref} {raw_text}".lower()
        return any(token in combined for token in ("paragraph", "sub-paragraph", "subparagraph", "item"))

    def _subsection_alpha_text_target_gap(self, op: LegalOperation) -> bool:
        target = getattr(op, "target", None)
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 2:
            return False
        if str(path[0][0] or "").lower() not in {"section", "article", "rule", "regulation"}:
            return False
        if str(path[1][0] or "").lower() != "subsection":
            return False
        leaf_label = str(path[1][1] or "").strip().lower()
        if not re.fullmatch(r"[a-z]+", leaf_label):
            return False
        parent_node, _, _ = self._find_node_by_target(LegalAddress(path=path[:1], special=None))
        if parent_node is None:
            return False
        subsection_labels = [
            str(getattr(child, "label", "") or "").strip().lower()
            for child in getattr(parent_node, "children", []) or []
            if str(getattr(child, "kind", "") or "").lower() == "subsection"
        ]
        if not subsection_labels or not all(re.fullmatch(r"\d+[a-z]?", label) for label in subsection_labels if label):
            return False
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        raw_text = str(getattr(extraction, "raw_text", "") or "")
        original_ref = str(getattr(extraction, "original_ref", "") or "")
        if not raw_text or not original_ref:
            for note in getattr(op, "provenance_tags", []) or []:
                note_text = str(note or "")
                if not raw_text and note_text.startswith("raw_text:"):
                    raw_text = note_text.partition(":")[2]
                elif not original_ref and note_text.startswith("original_ref:"):
                    original_ref = note_text.partition(":")[2]
        combined = f"{original_ref} {raw_text}".lower()
        return bool(re.search(r"subsection\s*\(\d+[a-z]?\)\s*\([a-z]+\)", combined))

    def _missing_sectionlike_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 1:
            return False
        leaf_kind, leaf_label = path[0]
        if str(leaf_kind or "").lower() not in {"section", "article", "rule", "regulation"}:
            return False
        want_label = str(leaf_label or "").strip()
        if not want_label:
            return False
        want_key = _label_sort_key(want_label)
        labels: list[str] = []

        def _walk(node: UKMutableNode) -> None:
            for child in getattr(node, "children", []) or []:
                if str(getattr(child, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}:
                    label = str(getattr(child, "label", "") or "").strip()
                    if label:
                        labels.append(label)
                _walk(child)

        _walk(self.statute.body)
        if not labels:
            return False
        existing = sorted({_label_sort_key(label): label for label in labels}.keys())
        if want_key in existing:
            return False
        lower = max((key for key in existing if key < want_key), default=None)
        upper = min((key for key in existing if key > want_key), default=None)
        return lower is not None and upper is not None

    def _doubled_alpha_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind, leaf_label = path[-1]
        text = str(leaf_label or "").strip().lower()
        if not re.fullmatch(r"([a-z])\1+", text):
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        labels = [
            str(getattr(child, "label", "") or "").strip().lower()
            for child in getattr(parent_node, "children", []) or []
            if str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
        ]
        repeated = sorted(label for label in labels if re.fullmatch(r"([a-z])\1+", label))
        if not repeated:
            return False
        return any(rep < text for rep in repeated) and any(rep > text for rep in repeated)

    def _missing_schedule_root_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 1 or str(path[0][0] or "").lower() != "schedule":
            return False
        want_label = str(path[0][1] or "").strip()
        if not want_label:
            return False
        want_key = _label_sort_key(want_label)
        labels = [str(getattr(sched, "label", "") or "").strip() for sched in self.statute.supplements]
        labels = [label for label in labels if label]
        if not labels:
            return False
        existing = sorted({_label_sort_key(label): label for label in labels}.keys())
        if want_key in existing:
            return False
        lower = max((key for key in existing if key < want_key), default=None)
        upper = min((key for key in existing if key > want_key), default=None)
        if lower is not None and upper is not None:
            return True
        if lower is None and existing and want_key < existing[0]:
            return True
        if upper is None and existing and want_key > existing[-1]:
            return True
        return False

    def _existing_target_insert_gap(
        self,
        target: LegalAddress,
        node: Optional[UKMutableNode],
        op: LegalOperation,
    ) -> bool:
        if _action_name(op.action) != "insert" or node is None:
            return False
        payload = getattr(op, "payload", None)
        if payload is None:
            return True
        payload_kind = str(getattr(payload, "kind", "") or "")
        payload_label = _clean_num(str(getattr(payload, "label", "") or ""))
        target_kind = _addr_leaf_kind(target) or ""
        target_label = _addr_leaf_label(target) or ""
        if not (
            uk_kind_matches(
                node_kind=payload_kind,
                target_kind=target_kind,
                node_label=payload_label,
                target_label=_clean_num(target_label),
            )
            and payload_label == _clean_num(target_label)
        ):
            return False
        return uk_kind_matches(
            node_kind=str(getattr(node, "kind", "") or ""),
            target_kind=target_kind,
            node_label=_clean_num(str(getattr(node, "label", "") or "")),
            target_label=_clean_num(target_label),
        ) and _clean_num(str(getattr(node, "label", "") or "")) == _clean_num(target_label)

    def _match_kind_label(self, node: Any, kind: str, label: Optional[str]) -> bool:
        """Shared matching logic for UK IR nodes."""
        nk = str(node.kind)
        tk = kind.lower()
        node_label = _clean_num(node.label or "")
        want_label = _clean_num(label or "") if label else ""

        if not uk_kind_matches(
            node_kind=nk,
            target_kind=tk,
            node_label=node_label,
            target_label=want_label,
        ):
            return False

        if not label:
            return True
        return node_label == want_label

    def _find_compound_subsection_candidate(
        self,
        curr_node: UKMutableNode,
        label: str,
    ) -> tuple[Optional[IRNode], Optional[IRNode], Optional[int]]:
        """Match malformed UK shapes like legal subsection 8A stored as 8 -> a."""
        return uk_compound_subsection_candidate(
            cast(IRNode, curr_node),
            label,
            match_kind_label=self._match_kind_label,
        )

    def _find_node_by_target(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        """Find a node and its parent by LegalAddress path."""
        target = canonicalize_uk_address(target)
        path = list(target.path)
        container = _addr_container(target)

        # 1. Resolve top-level container
        roots: list[tuple[IRNode, Optional[IRNode], Optional[int]]] = []
        if container == "schedule":
            # First path segment is ("schedule", label)
            sched_label = path[0][1] if path else None
            remaining = path[1:]
            roots = uk_schedule_root_candidates(
                cast(list[IRNode], self.statute.supplements),
                sched_label=sched_label,
                remaining_path=tuple(remaining),
                match_kind_label=self._match_kind_label,
            )
            if sched_label and roots and not remaining:
                sch, _, idx = roots[0]
                return cast(UKMutableNode, sch), None, idx
            if not sched_label and len(roots) == 1 and not remaining:
                sch, _, idx = roots[0]
                return cast(UKMutableNode, sch), None, idx
            path = remaining
        else:
            roots = [(cast(IRNode, self.statute.body), None, None)]
        if not roots:
            return None, None, None

        curr_cands = roots
        for p_kind, p_label in path:
            next_cands: list[tuple[IRNode, Optional[IRNode], Optional[int]]] = []
            for curr_node, _, _ in curr_cands:
                for i, child in enumerate(curr_node.children):
                    if self._match_kind_label(child, p_kind, p_label):
                        next_cands.append((child, curr_node, i))
                if not next_cands and allow_compound_subsection_alias and p_kind.lower() == "subsection" and p_label:
                    compound = self._find_compound_subsection_candidate(cast(UKMutableNode, curr_node), p_label)
                    if compound[0] is not None:
                        next_cands.append(cast(tuple[IRNode, Optional[IRNode], Optional[int]], compound))
            if not next_cands:
                if container == "schedule":
                    ordinal_matches = uk_schedule_ordinal_paragraph_matches(
                        curr_cands,
                        p_kind=p_kind,
                        p_label=p_label,
                    )
                    if ordinal_matches:
                        next_cands = ordinal_matches
                if not next_cands:
                    for curr_node, _, _ in curr_cands:
                        for i, child in enumerate(curr_node.children):
                            res_node, res_p, res_i = self._find_recursive_match(
                                cast(UKMutableNode, child), p_kind, p_label
                            )
                            if res_node:
                                next_cands.append((res_node, res_p, res_i))
            if not next_cands:
                return None, None, None
            curr_cands = next_cands
        return (
            cast(tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]], curr_cands[0])
            if curr_cands
            else (None, None, None)
        )

    def _find_recursive_match(
        self, node: UKMutableNode, kind: str, label: str
    ) -> tuple[Optional[IRNode], Optional[IRNode], Optional[int]]:
        return uk_recursive_kind_match(
            cast(IRNode, node),
            kind=str(kind),
            label=label,
            match_kind_label=self._match_kind_label,
        )

    def _empty_schedule_root_shape_gap(self, target: LegalAddress) -> bool:
        """Return True when a descendant target lands under an empty schedule root."""
        if _addr_container(target) != "schedule" or len(target.path) <= 1:
            return False
        sched_label = target.path[0][1] if target.path else None
        if not sched_label:
            return False
        for sch in self.statute.supplements:
            if self._match_kind_label(sch, "schedule", sched_label):
                return len(sch.children) == 0
        return False

    def _emit_top_section_snapshot(self, op: LegalOperation) -> None:
        """Emit a top-level section/schedule snapshot to lo_ops_out after an op is applied.

        Finds the top-level node (first path segment) affected by *op* in the
        current statute state and appends a LegalOperation snapshot to lo_ops_out.
        This gives compile_timelines() section-level content for overlay
        materialization, mirroring the Finland lo_ops_out pattern.

        For repeal ops the tombstone is recorded (payload=None, action="repeal").
        For all other structural ops the current node content is snapshotted
        (action="replace" / "insert" depending on whether the node was already in
        the base, but "replace" is used as the conservative choice since
        compile_timelines handles both identically for existing addresses).
        """
        if self.lo_ops_out is None:
            return
        target = op.target
        if not target.path:
            return
        # Derive the canonical address for the top-level container.
        # For body ops this is the first path segment (e.g. section:1 or part:I).
        # For schedule ops it is the schedule element itself.
        top_kind, top_label = target.path[0]
        top_addr = LegalAddress(path=((top_kind, top_label),))

        # Find the top-level node in the current (post-op) statute state.
        # We look in body children and schedules.
        top_node: Optional[UKMutableNode] = None
        for child in self.statute.body.children:
            if str(child.kind) == top_kind and (child.label is not None and child.label == top_label):
                top_node = child
                break
        if top_node is None:
            for sch in self.statute.supplements:
                if str(sch.kind) == top_kind and sch.label == top_label:
                    top_node = sch
                    break

        if _action_name(op.action) == "repeal" and top_node is None:
            # Node was removed — emit tombstone
            self.lo_ops_out.append(
                LegalOperation(
                    op_id=f"uk_snapshot_repeal_{top_kind}_{top_label}_{op.op_id}",
                    sequence=op.sequence,
                    action=StructuralAction.REPEAL,
                    target=top_addr,
                    payload=None,
                    source=op.source,
                    group_id=op.group_id,
                )
            )
        elif top_node is not None:
            # Snapshot the current state of the top-level node after op applied.
            self.lo_ops_out.append(
                LegalOperation(
                    op_id=f"uk_snapshot_{top_kind}_{top_label}_{op.op_id}",
                    sequence=op.sequence,
                    action=StructuralAction.REPLACE,
                    target=top_addr,
                    payload=top_node.to_irnode(),
                    source=op.source,
                    group_id=op.group_id,
                )
            )

    def apply_op(self, op: LegalOperation):
        target = op.target
        # Keep legacy warnings visible during replay runs while also recording
        # structured adjudications for downstream analyses.

        if str(target.special or "") == "whole_act":
            if _action_name(op.action) == "repeal":
                self._log("  EXECUTOR: repealing WHOLE ACT")
                self.statute.body.children = []
                self.statute.supplements = []
                self._record_invariant_violations(op)
            else:
                self._log(
                    f"  EXECUTOR: WARN whole_act target with unhandled action {op.action!r} — skipping {op.op_id}"
                )
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_unsupported_action",
                    message="UK replay skipped unsupported whole-act action.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            return

        allow_compound_subsection_alias = _action_name(op.action) in ("text_replace", "text_repeal")
        node, parent, idx = self._find_node_by_target(
            target,
            allow_compound_subsection_alias=allow_compound_subsection_alias,
        )

        if not node:
            target_eid = self._derive_target_eid(target)
            node, parent, idx = self._find_node_and_parent_statute(
                target_eid,
                allow_sequence_match=False,
            )
        target_found = node is not None
        if not target_found and self._empty_schedule_root_shape_gap(target):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_empty_schedule_shape_gap",
                message="UK replay skipped text-based op: empty schedule root has no descendant target shape.",
                op=op,
                detail={
                    "action": _action_name(op.action),
                    "target": str(target),
                    "source_shape": "empty_schedule_root",
                },
            )
            return

        if _action_name(op.action) == "repeal":
            if node is None:
                if self._target_under_repealed_prefix(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped repeal: target path was already repealed earlier in the chain.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._doubled_alpha_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped repeal: target falls inside an already absent doubled-alpha sibling range under the parent path.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._malformed_target_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_malformed_target_gap",
                        message="UK replay skipped repeal: lowered target path is malformed.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_source_target_gap(op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_source_target_gap",
                        message="UK replay skipped repeal: target comes from index-only effect row without extracted source text.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_sibling_range_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped repeal: target falls inside an already absent sibling range under the parent path.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._empty_descendant_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_empty_descendant_shape_gap",
                        message="UK replay skipped repeal: parent target exists but has no descendant structural shape.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_sectionlike_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped repeal: target falls inside an already absent sectionlike gap.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_schedule_branch_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped repeal: schedule root branch is already absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_schedule_root_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped repeal: target falls inside an already absent alphanumeric schedule gap.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_schedule_branch_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped repeal: schedule root branch is already absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_parent_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_parent_shape_gap",
                        message="UK replay skipped repeal: immediate parent target path is structurally absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._schedule_paragraph_carrier_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_parent_shape_gap",
                        message="UK replay skipped repeal: schedule paragraph carrier is structurally absent or wrapped.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._leading_blank_subparagraph_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped repeal: target falls inside an already absent leading numeric subparagraph gap under blank schedule placeholders.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                else:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_target_not_found",
                        message="UK replay skipped repeal: target not found.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                return
            if parent and idx is not None:
                self._log(f"  EXECUTOR: repealing {node.kind} {node.label} from parent {parent.kind} {parent.label}")
                self._remove_node(node, parent, idx)
                self._record_repealed_target(target)
            elif node in self.statute.supplements:
                self._log(f"  EXECUTOR: repealing schedule {node.label}")
                self._remove_node(node, None, None)
                self._record_repealed_target(target)
            self._record_invariant_violations(op)
            self._emit_top_section_snapshot(op)
        elif _action_name(op.action) == "replace":
            frag_subs = _fragment_substitution(op)
            if frag_subs is not None:
                if node:
                    self._log(f"  EXECUTOR: substituting text in {node.kind} {node.label}")
                    self._apply_text_substitution_on_node(node, frag_subs)
                    self._record_invariant_violations(op)
                else:
                    if self._malformed_target_gap(target):
                        kind = "uk_replay_malformed_target_gap"
                        message = "UK replay skipped replace: lowered target path is malformed."
                    elif self._missing_parent_shape_gap(target):
                        kind = "uk_replay_missing_parent_shape_gap"
                        message = "UK replay skipped replace: immediate parent target path is structurally absent."
                    elif self._missing_sectionlike_gap(target):
                        kind = "uk_replay_repealed_target_gap"
                        message = "UK replay skipped replace: target falls inside an already absent sectionlike gap."
                    else:
                        kind = "uk_replay_target_not_found"
                        message = "UK replay skipped replace: target not found."
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=str(kind),
                        message=message,
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
            elif op.payload is not None:
                # Clone payload so repeated ops don't share state
                new_node = UKMutableNode.from_dict(op.payload.to_jsonable_dict())
                if node is None:
                    witness = _witness_for_op(op)
                    effect_witness = getattr(witness, "effect_witness", None)
                    target_expansion_witness = getattr(witness, "target_expansion_witness", None)
                    if effect_witness is not None and target_expansion_witness is not None:
                        retargeted_refs = _retarget_substituted_series_to_replaced_anchor(
                            str(getattr(effect_witness, "effect_type_raw", "") or ""),
                            [str(getattr(target_expansion_witness, "original_ref", "") or "")],
                        )
                        if retargeted_refs:
                            try:
                                retargeted_target = _parse_affected_target(retargeted_refs[0])
                            except Exception:
                                retargeted_target = None
                            if retargeted_target is not None:
                                retargeted_node, retargeted_parent, retargeted_idx = self._find_node_by_target(
                                    retargeted_target,
                                    allow_compound_subsection_alias=True,
                                )
                                if retargeted_node is not None:
                                    retargeted_leaf = _addr_leaf_label(op.target) or ""
                                    if retargeted_leaf:
                                        new_node.label = retargeted_leaf
                                    node = retargeted_node
                                    parent = retargeted_parent
                                    idx = retargeted_idx
                if node:
                    node_kind = str(node.kind).lower()
                    new_kind = str(new_node.kind).lower()
                    if node_kind != "content" and new_kind != "content":
                        existing_eid = str(node.attrs.get("eId") or "")
                        if existing_eid:
                            new_node.attrs["eId"] = existing_eid
                        if parent and idx is not None:
                            self._replace_node_in_statute(node, new_node)
                            self._record_invariant_violations(op)
                        elif idx is not None and node in self.statute.supplements:
                            self._replace_node_in_statute(node, new_node)
                            self._record_invariant_violations(op)
                    elif node_kind != "content" and new_kind == "content":
                        self._replace_text(node, new_node.text)
                    else:
                        existing_eid = str(node.attrs.get("eId") or "")
                        if existing_eid:
                            new_node.attrs["eId"] = existing_eid
                        if parent and idx is not None:
                            self._replace_node_in_statute(node, new_node)
                            self._record_invariant_violations(op)
                elif uk_kind_matches(
                    node_kind=str(new_node.kind),
                    target_kind=_addr_leaf_kind(op.target) or "",
                    node_label=_clean_num(new_node.label or ""),
                    target_label=_clean_num(_addr_leaf_label(op.target) or ""),
                ) and _clean_num(new_node.label or "") == _clean_num(_addr_leaf_label(op.target) or ""):
                    # Some UK replace ops target a node that is missing from the
                    # base shape but present in the commensurable oracle shape
                    # (for example a collapsed section lead becoming an explicit
                    # subsection 1). If the replacement payload already matches
                    # the missing target leaf exactly, materialize it under the
                    # parent instead of silently dropping the replace.
                    leaf_kind = str(_addr_leaf_kind(op.target) or "").lower()
                    parent_target = LegalAddress(path=target.path[:-1], special=None)
                    parent_node, _, _ = self._find_node_by_target(parent_target)
                    inserted = False
                    if parent_node is not None and leaf_kind not in {"subparagraph", "item", "point"}:
                        inserted = self._insert_node_v2(op.target, new_node, op)
                    if inserted:
                        self._record_invariant_violations(op)
                        self._emit_top_section_snapshot(op)
                    else:
                        if self._malformed_target_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_malformed_target_gap",
                                message="UK replay skipped replace: lowered target path is malformed.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._missing_parent_shape_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_missing_parent_shape_gap",
                                message="UK replay skipped replace: immediate parent target path is structurally absent.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._schedule_paragraph_carrier_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_missing_parent_shape_gap",
                                message="UK replay skipped replace: schedule paragraph carrier is structurally absent or wrapped.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._leading_blank_subparagraph_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_repealed_target_gap",
                                message="UK replay skipped replace: target falls inside an already absent leading numeric subparagraph gap under blank schedule placeholders.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._missing_sibling_range_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_repealed_target_gap",
                                message="UK replay skipped replace: target falls inside an already absent sibling range under the parent path.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._empty_descendant_shape_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_empty_descendant_shape_gap",
                                message="UK replay skipped replace: parent target exists but has no descendant structural shape.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_payload_mismatch",
                            message="UK replay skipped replace: payload could not be inserted by target path.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(new_node.kind),
                                "payload_label": new_node.label or "",
                            },
                        )
                else:
                    if _addr_leaf_kind(op.target) and (
                        str(new_node.kind or "").lower() != str(_addr_leaf_kind(op.target) or "").lower()
                        or _clean_num(new_node.label or "") != _clean_num(_addr_leaf_label(op.target) or "")
                    ):
                        kind = "uk_replay_malformed_target_gap"
                        message = "UK replay skipped replace: payload does not match lowered target leaf."
                    elif self._malformed_target_gap(target):
                        kind = "uk_replay_malformed_target_gap"
                        message = "UK replay skipped replace: lowered target path is malformed."
                    elif self._missing_parent_shape_gap(target):
                        kind = "uk_replay_missing_parent_shape_gap"
                        message = "UK replay skipped replace: immediate parent target path is structurally absent."
                    elif self._missing_sectionlike_gap(target):
                        kind = "uk_replay_repealed_target_gap"
                        message = "UK replay skipped replace: target falls inside an already absent sectionlike gap."
                    else:
                        kind = "uk_replay_target_not_found"
                        message = "UK replay skipped replace: target not found."
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=str(kind),
                        message=message,
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_payload_missing",
                    message="UK replay skipped replace: payload missing.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            if target_found or node is not None:
                self._emit_top_section_snapshot(op)
        elif _action_name(op.action) in ("text_replace", "text_repeal"):
            text_patch = op.text_patch
            if text_patch is None:
                self._log(
                    f"  EXECUTOR: WARN text_replace/text_repeal op has no structured text patch — skipping {op.op_id}"
                )
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_text_match_missing",
                    message="UK replay skipped text-based op: text_match missing.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(target),
                    },
                )
                return
            if node:
                replacement = (
                    text_patch.replacement
                    if text_patch.kind is TextPatchKindEnum.REPLACE and text_patch.replacement is not None
                    else ""
                )
                node, applied = self._apply_text_replace_on_subtree(
                    node,
                    text_patch.selector.match_text,
                    replacement,
                    text_patch.selector.occurrence,
                )
                applied_match = text_patch.selector.match_text
                applied_replacement = replacement
                if not applied:
                    for frag_sub in _fragment_substitution(op) or []:
                        alt_match = str(frag_sub.get("original") or "").strip()
                        alt_replacement = str(frag_sub.get("replacement") or "")
                        if not alt_match or (
                            alt_match == text_patch.selector.match_text and alt_replacement == replacement
                        ):
                            continue
                        node, alt_applied = self._apply_text_replace_on_subtree(
                            node,
                            alt_match,
                            alt_replacement,
                            text_patch.selector.occurrence,
                        )
                        if alt_applied:
                            applied = True
                            applied_match = alt_match
                            applied_replacement = alt_replacement
                            self._log(
                                f"  EXECUTOR: text_replace fallback in {node.kind} {node.label}: {alt_match!r} -> {alt_replacement!r}"
                            )
                            break
                if applied:
                    self._log(
                        f"  EXECUTOR: text_replace in {node.kind} {node.label}: {applied_match!r} -> {applied_replacement!r}"
                    )
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                else:
                    self._log(
                        f"  EXECUTOR: WARN text_replace target found but text_match not in subtree: {text_patch.selector.match_text!r} in {node.kind} {node.label}"
                    )
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_text_match_missing",
                        message="UK replay skipped text-based op: text_match not found in target subtree.",
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                        },
                    )
            else:
                self._log(f"  EXECUTOR: WARN text_replace target not found: {op.target}")
                if self._table_target_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_table_shape_gap",
                        message="UK replay skipped text-based op: table target has no structural table node.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._empty_descendant_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_empty_descendant_shape_gap",
                        message="UK replay skipped text-based op: parent target exists but has no descendant structural shape.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._target_under_repealed_prefix(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped text-based op: target path was already repealed earlier in the chain.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._doubled_alpha_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped text-based op: target falls inside an already absent doubled-alpha sibling range under the parent path.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_sibling_range_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped text-based op: target falls inside an already absent sibling range under the parent path.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._annex_schedule_mismatch_gap(op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_malformed_target_gap",
                        message="UK replay skipped text-based op: Annex reference was lowered to a missing schedule root target.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._container_text_target_gap(op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_malformed_target_gap",
                        message="UK replay skipped text-based op: lowered target points at a missing schedule container instead of the textual descendant.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._subsection_alpha_text_target_gap(op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_malformed_target_gap",
                        message="UK replay skipped text-based op: lowered target collapsed a numeric subsection and alphabetic descendant into one subsection label.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._malformed_target_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_malformed_target_gap",
                        message="UK replay skipped text-based op: lowered target path is malformed.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_schedule_branch_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped text-based op: schedule root branch is already absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_parent_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_parent_shape_gap",
                        message="UK replay skipped text-based op: immediate parent target path is structurally absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._schedule_paragraph_carrier_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_parent_shape_gap",
                        message="UK replay skipped text-based op: schedule paragraph carrier is structurally absent or wrapped.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._leading_blank_subparagraph_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped text-based op: target falls inside an already absent leading numeric subparagraph gap under blank schedule placeholders.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_sectionlike_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped text-based op: target falls inside an already absent sectionlike gap.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif prior_kind := self._prior_same_target_gap_kind(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=prior_kind,
                        message="UK replay skipped text-based op: target already exhibited the same structural gap earlier in the chain.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                else:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_target_not_found",
                        message="UK replay skipped text-based op: target not found.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
        elif _action_name(op.action) == "insert":
            if op.payload is not None:
                if self._existing_target_insert_gap(target, node, op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_existing_target_gap",
                        message="UK replay skipped insert: target path already exists before applying the op.",
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "payload_kind": str(op.payload.kind),
                            "payload_label": op.payload.label or "",
                        },
                    )
                    return
                # Clone payload so repeated ops (same source for multiple targets) don't share nodes
                inserted = self._insert_node_v2(
                    target,
                    UKMutableNode.from_dict(op.payload.to_jsonable_dict()),
                    op,
                )
                if inserted:
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                else:
                    if self._malformed_target_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_malformed_target_gap",
                            message="UK replay skipped insert: lowered target path is malformed.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._missing_parent_shape_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_missing_parent_shape_gap",
                            message="UK replay skipped insert: immediate parent target path is structurally absent.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._schedule_paragraph_carrier_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_missing_parent_shape_gap",
                            message="UK replay skipped insert: schedule target expects a paragraph carrier that is absent or wrapped by legacy p1group structure.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._leading_blank_subparagraph_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_repealed_target_gap",
                            message="UK replay skipped insert: target falls inside an already absent leading numeric subparagraph gap under blank schedule placeholders.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._missing_sibling_range_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_repealed_target_gap",
                            message="UK replay skipped insert: target falls inside an already absent sibling range under the parent path.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._empty_descendant_shape_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_empty_descendant_shape_gap",
                            message="UK replay skipped insert: parent target exists but has no descendant structural shape.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_payload_mismatch",
                        message="UK replay skipped insert: payload could not be inserted by target path.",
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "payload_kind": str(op.payload.kind),
                            "payload_label": op.payload.label or "",
                        },
                    )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_payload_missing",
                    message="UK replay skipped insert: payload missing.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
        elif _action_name(op.action) == "renumber":
            self._log(f"  EXECUTOR: renumber op not yet implemented — skipping {op.op_id}")
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_unsupported_action",
                message="UK replay skipped unsupported action.",
                op=op,
                detail={"action": _action_name(op.action), "target": str(target)},
            )
        elif _action_name(op.action) == "unknown":
            self._log(f"  EXECUTOR: unknown action — skipping {op.op_id}")
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_unsupported_action",
                message="UK replay skipped unsupported action.",
                op=op,
                detail={"action": _action_name(op.action), "target": str(target)},
            )
        else:
            raise ValueError(
                f"UKReplayExecutor.apply_op: unhandled action {op.action!r} "
                f"on op {op.op_id}. This is a programming error — every action "
                f"type must be explicitly handled (even if only to skip+warn)."
            )

    def _apply_text_replace_on_subtree(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
    ) -> tuple[UKMutableNode, bool]:
        """Walk the subtree rooted at *node*, find *match* in text fields, and substitute.

        Args:
            node:        Root of the IR subtree to search.
            match:       Exact string to find (case-sensitive first, then whitespace-
                         normalized fallback, consistent with _apply_text_substitution_on_node).
            replacement: String to substitute in place of *match*.
            occurrence:  0 = replace all occurrences across the subtree.
                         N > 0 = replace only the Nth occurrence (1-based, document order).

        Returns:
            True if at least one substitution was made; False otherwise.
        """
        # Collect all nodes with text in document order (pre-order traversal)
        text_nodes: list[tuple[tuple[int, ...], UKMutableNode]] = []

        def _collect(n: UKMutableNode, path: tuple[int, ...] = ()) -> None:
            if n.text:
                text_nodes.append((path, n))
            for i, child in enumerate(n.children):
                _collect(child, path + (i,))

        _collect(node)

        if match.startswith("TEXT_FROM_"):
            full_text = " ".join(tn.text.strip() for _, tn in text_nodes if tn.text).strip()
            if not full_text:
                return node, False

            if match.endswith("_TO_END"):
                start_text = match[len("TEXT_FROM_") : -len("_TO_END")]
                start_idx = full_text.find(start_text)
                if start_idx == -1:
                    pattern = re.escape(start_text).replace(r"\ ", r"\s+") + r".*$"
                    m = re.search(pattern, full_text, flags=re.I | re.S)
                    if not m:
                        return node, False
                    new_text = full_text[: m.start()] + replacement
                else:
                    new_text = full_text[:start_idx] + replacement
                rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            if "_TO_" in match:
                parts = match.replace("TEXT_FROM_", "", 1).split("_TO_", 1)
                if len(parts) == 2:
                    start_text, end_text = parts[0], parts[1]
                    start_idx = full_text.find(start_text)
                    end_idx = -1
                    if start_idx != -1:
                        end_idx = full_text.find(end_text, start_idx + len(start_text))
                    if start_idx == -1 or end_idx == -1:
                        pattern = (
                            re.escape(start_text).replace(r"\ ", r"\s+")
                            + r".*?"
                            + re.escape(end_text).replace(r"\ ", r"\s+")
                        )
                        m = re.search(pattern, full_text, flags=re.I | re.S)
                        if not m:
                            return node, False
                        new_text = full_text[: m.start()] + replacement + full_text[m.end() :]
                    else:
                        new_text = full_text[:start_idx] + replacement + full_text[end_idx + len(end_text) :]
                    rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
                    self._replace_node_in_statute(node, rebuilt)
                    return rebuilt, True

        if occurrence == 0:
            # Replace all occurrences across all text nodes
            made_any = False
            rebuilt = node
            for path, tn in text_nodes:
                text = tn.text
                if match in text:
                    rebuilt = self._replace_descendant_at_path(
                        rebuilt,
                        path,
                        dc_replace(tn, text=text.replace(match, replacement)),
                    )
                    made_any = True
                else:
                    # Whitespace-normalized fallback (same as _apply_text_substitution_on_node)
                    pattern = re.escape(match).replace(r"\ ", r"\s+")
                    new_text, count = re.subn(pattern, replacement, text, flags=re.I)
                    if count > 0:
                        rebuilt = self._replace_descendant_at_path(
                            rebuilt,
                            path,
                            dc_replace(tn, text=new_text),
                        )
                        made_any = True
            if made_any:
                self._replace_node_in_statute(node, rebuilt)
            return rebuilt, made_any
        else:
            # Replace only the Nth occurrence (1-based) — count across all text nodes in order
            global_count = 0
            for path, tn in text_nodes:
                text = tn.text
                # Count occurrences in this node's text
                start = 0
                while True:
                    pos = text.find(match, start)
                    if pos == -1:
                        break
                    global_count += 1
                    if global_count == occurrence:
                        rebuilt = self._replace_descendant_at_path(
                            node,
                            path,
                            dc_replace(tn, text=text[:pos] + replacement + text[pos + len(match) :]),
                        )
                        self._replace_node_in_statute(node, rebuilt)
                        return rebuilt, True
                    start = pos + len(match)
            # Whitespace-normalized fallback if exact search found nothing
            if global_count == 0:
                pattern = re.escape(match).replace(r"\ ", r"\s+")
                nth_seen = 0
                for path, tn in text_nodes:
                    for m in re.finditer(pattern, tn.text, flags=re.I):
                        nth_seen += 1
                        if nth_seen == occurrence:
                            rebuilt = self._replace_descendant_at_path(
                                node,
                                path,
                                dc_replace(tn, text=tn.text[: m.start()] + replacement + tn.text[m.end() :]),
                            )
                            self._replace_node_in_statute(node, rebuilt)
                            return rebuilt, True
            return node, False

    def _apply_text_substitution_on_node(self, node: UKMutableNode, subs: list[dict]) -> UKMutableNode:
        text = node.text or ""
        children = list(node.children)
        for s in subs:
            old, new = s["original"], s["replacement"]
            if old.startswith("FROM_") and "_TO_" in old:
                parts = old.replace("FROM_", "").split("_TO_")
                if len(parts) == 2:
                    start_label, end_label = parts[0].strip("()"), parts[1].strip("()")
                    start_idx = end_idx = -1
                    for i, child in enumerate(children):
                        if _clean_num(child.label or "") == _clean_num(start_label):
                            start_idx = i
                        if _clean_num(child.label or "") == _clean_num(end_label):
                            end_idx = i
                    if start_idx != -1 and end_idx != -1 and start_idx <= end_idx:
                        self._log(
                            f"  EXECUTOR: deleting children from '{start_label}' to '{end_label}' in {node.kind} {node.label}"
                        )
                        for i in range(end_idx, start_idx - 1, -1):
                            children.pop(i)
                continue
            if old in text:
                text = text.replace(old, new)
            else:
                pattern = re.escape(old).replace(r"\ ", r"\s+")
                new_text, count = re.subn(pattern, new, text, flags=re.I)
                if count > 0:
                    text = new_text
        rebuilt = dc_replace(node, text=text, children=list(children))
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt

    def _insert_node_v2(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
    ) -> bool:
        from lawvm.uk_legislation.canonicalize import (
            uk_insert_into_children,
            uk_resolve_insertion_parent,
        )

        prec_eid = _preceding_eid(op)
        parent_node, insert_idx = uk_resolve_insertion_parent(
            target=target,
            body_root=cast(IRNode, self.statute.body),
            node_kind=str(new_node.kind),
            node_label=new_node.label,
            preceding_eid=prec_eid,
            find_node_by_target=self._find_node_by_target,
            find_node_and_parent_statute=self._find_node_and_parent_statute,
            label_sort_key=_label_sort_key,
        )
        parent_node = cast(Optional[UKMutableNode], parent_node)
        target_eid = self._derive_target_eid(target)
        if target_eid and "eId" not in new_node.attrs and "id" not in new_node.attrs:
            new_node.attrs["eId"] = target_eid

        def _inherit_parent_local_eid(parent_node: UKMutableNode, candidate: UKMutableNode) -> UKMutableNode:
            parent_eid = str(parent_node.attrs.get("eId") or parent_node.attrs.get("id") or "")
            current_eid = str(candidate.attrs.get("eId") or candidate.attrs.get("id") or "")
            label = str(candidate.label or _addr_leaf_label(target) or "").strip()
            if not parent_eid or not label:
                return candidate
            if current_eid and current_eid in self.eid_map.values():
                return candidate
            candidate.attrs["eId"] = f"{parent_eid}-{label}"
            return candidate

        if parent_node and insert_idx is not None:
            new_node = _inherit_parent_local_eid(parent_node, new_node)
            self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} at routed index {insert_idx}")
            children = list(parent_node.children)
            children.insert(insert_idx, new_node)
            self._replace_children(parent_node, children)
            return True
        if parent_node:
            new_node = _inherit_parent_local_eid(parent_node, new_node)
            self._log(
                f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into {parent_node.kind} {parent_node.label}"
            )
            return self._insert_child_sorted(parent_node, new_node)

        # Build parent address by dropping the last path segment.
        # Single-segment paths (e.g. section:2a) get parent = body/schedules directly,
        # matching the old IRTargetRef behaviour where parent_target.section=None caused
        # _find_node_by_target to return the body node for non-schedule containers.
        container = _addr_container(target)
        parent_addr = target.parent() if len(target.path) > 1 else None

        if parent_addr is not None:
            p_node, _, _ = self._find_node_by_target(parent_addr)
            if p_node:
                new_node = _inherit_parent_local_eid(p_node, new_node)
                self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into {p_node.kind} {p_node.label}")
                return self._insert_child_sorted(p_node, new_node)
        elif container == "schedule":
            # Single-segment schedule target: the target IS the schedule — insert payload into it,
            # but only when the payload is a part, chapter, or section (structural containers
            # that appear as direct children of schedules).  Paragraph/subsection payloads
            # targeted at a whole schedule are likely table-row inserts (e.g. concordat
            # schedules) whose EIDs don't match oracle EIDs — fall through to the EID-derived
            # logic in those cases.
            #
            # A schedule payload targeted at a whole schedule path (for example
            # ``schedule:7a`` with payload kind ``schedule``) is a top-level
            # schedule insertion and must be added to ``statute.supplements``.
            # Falling through to the EID-derived parent lookup turns
            # ``schedule-7a`` into parent ``schedule`` and can incorrectly nest
            # the new schedule under an existing schedule branch like
            # ``schedule-7``.
            _sch_structural = {"part", "chapter", "section", "article", "p1group", "crossheading"}
            new_kind = str(new_node.kind).lower()
            if new_kind == "schedule":
                self._log(f"  EXECUTOR: inserting schedule {new_node.label} at top-level")
                return self._insert_supplement_sorted(new_node)
            if new_kind in _sch_structural:
                sch_node, _, _ = self._find_node_by_target(target)
                if sch_node:
                    sch_node = cast(UKMutableNode, sch_node)
                    new_node = _inherit_parent_local_eid(sch_node, new_node)
                    self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into schedule {sch_node.label}")
                    return self._insert_child_sorted(sch_node, new_node)
                return False
        else:
            # Single-segment non-schedule target: prefer inserting after the
            # nearest existing same-kind predecessor in its actual parent,
            # because UK body sections/articles often live under wrappers like
            # crossheading -> p1group rather than directly under body.
            pred_parent, pred_idx, pred_label = uk_find_body_predecessor_parent(
                cast(IRNode, self.statute.body),
                str(new_node.kind),
                new_node.label,
                label_sort_key=_label_sort_key,
            )
            if pred_parent is not None and pred_idx is not None:
                pred_parent = cast(UKMutableNode, pred_parent)
                self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} after body predecessor {pred_label}")
                children: list[UKMutableNode] = list(pred_parent.children)
                children.insert(pred_idx + 1, new_node)
                self._replace_children(pred_parent, children)
                return True

            # No suitable predecessor exists in the body tree: fall back to a
            # true body-root insertion.
            self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into body (top-level)")
            body_children: list[UKMutableNode] = list(self.statute.body.children)
            uk_insert_into_children(
                cast(list[IRNode], body_children),
                cast(IRNode, new_node),
                label_sort_key=_label_sort_key,
            )
            self.statute.body.children = body_children
            return True

        if "-" in target_eid:
            parent_eid = "-".join(target_eid.split("-")[:-1])
            p_node, _, _ = self._find_node_and_parent_statute(parent_eid)
            if p_node:
                new_node = _inherit_parent_local_eid(p_node, new_node)
                self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into parent {parent_eid}")
                return self._insert_child_sorted(cast(UKMutableNode, p_node), new_node)

        body_root_kinds = {
            "part",
            "chapter",
            "crossheading",
            "pblock",
            "division",
            "section",
            "article",
            "rule",
            "regulation",
            "p1group",
            "schedule",
        }
        new_kind = str(new_node.kind).lower()
        if new_kind not in body_root_kinds:
            self._log(
                "  EXECUTOR: WARN refusing impossible body-root fallback for "
                f"{new_node.kind} {new_node.label} target {target}"
            )
            return False
        self._log(f"  EXECUTOR: fallback inserting {new_node.kind} {new_node.label} into body")
        if new_kind == "schedule":
            supplements = list(self.statute.supplements)
            supplements.append(new_node)
            self._replace_statute(supplements=supplements)
            return True
        else:
            body_children: list[UKMutableNode] = list(self.statute.body.children)
            uk_insert_into_children(
                cast(list[IRNode], body_children),
                cast(IRNode, new_node),
                label_sort_key=_label_sort_key,
            )
            self.statute.body.children = body_children
            return True

    def _derive_target_eid(self, addr: LegalAddress) -> str:
        is_eur = self.statute.metadata.get("is_eur", False)
        container = _addr_container(addr)
        section = _addr_field(addr, "schedule") or _addr_field(addr, "section")
        part = _addr_field(addr, "part")
        chapter = _addr_field(addr, "chapter")
        # subsection is in path as ("subsection", lbl); item is ("paragraph", lbl).
        # For schedule paths there is no "subsection" kind — both levels use "paragraph".
        if container == "schedule":
            paragraph, subsection, item_labels = _schedule_target_levels(addr)
        else:
            paragraph = None
            item_labels = []
            paras = [lbl for k, lbl in addr.path if k == "paragraph"]
            _subsec_direct = _addr_field(addr, "subsection")
            if _subsec_direct:
                subsection = _subsec_direct
                item_labels = paras[:1]
            else:
                subsection = paras[0] if paras else None
                item_labels = paras[1:2] if len(paras) > 1 else []
        item = item_labels[0] if item_labels else None

        def _get_candidates():
            parts: list[str] = []
            if container == "schedule":
                sch_prefix = "annex" if is_eur else "schedule"
                if section:
                    parts.append(f"{sch_prefix}-{_clean_num(section)}")
                else:
                    parts.append(sch_prefix)

                # EU specific: very flat scheme for Annexes
                if is_eur:
                    eu_parts = list(parts)
                    if paragraph:
                        eu_parts.append(f"paragraph-{_clean_num(paragraph)}")
                    if subsection:
                        eu_parts.append(_clean_num(subsection))
                    for item_label in item_labels:
                        eu_parts.append(_clean_num(item_label))
                    yield "-".join(eu_parts)
                    # Reset parts for hierarchical try
                    parts = [f"{sch_prefix}-{_clean_num(section)}"] if section else [sch_prefix]

                if part:
                    parts.append(f"part-{_clean_num(part)}")
                if chapter:
                    parts.append(f"chapter-{_clean_num(chapter)}")
                if paragraph:
                    if is_eur:
                        parts.append(f"paragraph-{_clean_num(paragraph)}")
                    else:
                        parts.append(f"paragraph-{_canonicalize_schedule_paragraph_eid_label(paragraph)}")
                if subsection:
                    parts.append(_clean_num(subsection))
                for item_label in item_labels:
                    parts.append(_clean_num(item_label))
                yield "-".join(parts)
            else:
                # Try section and article prefixes
                for prefix in ["article", "section"] if is_eur else ["section", "article"]:
                    parts = []
                    if section:
                        parts.append(f"{prefix}-{_clean_num(section)}")
                        if subsection:
                            parts.append(_clean_num(subsection))
                        if item:
                            parts.append(_clean_num(item))
                    yield "-".join(parts)

        for full_key in _get_candidates():
            if not full_key:
                continue
            if full_key.lower() in self.eid_map:
                return self.eid_map[full_key.lower()]

        # Fallback to the first best guess
        return next(_get_candidates(), "")

    def _find_node_and_parent_statute(
        self,
        eid: str,
        *,
        allow_sequence_match: bool = True,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        node, parent, idx = self._find_node_and_parent(
            self.statute.body,
            eid,
            allow_sequence_match=allow_sequence_match,
        )
        if node:
            return node, parent, idx
        for sched in self.statute.supplements:
            if sched.attrs.get("eId") == eid:
                return sched, None, None
            node, parent, idx = self._find_node_and_parent(
                sched,
                eid,
                allow_sequence_match=allow_sequence_match,
            )
            if node:
                return node, parent, idx
        return None, None, None

    def _find_node_and_parent(
        self,
        node: UKMutableNode,
        eid: str,
        *,
        allow_sequence_match: bool = True,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        target_seq = _get_id_sequence(eid)
        for i, child in enumerate(node.children):
            c_eid = child.attrs.get("eId") or child.attrs.get("id")
            if c_eid:
                if c_eid == eid:
                    return child, node, i
                if c_eid.endswith("-" + eid) or c_eid.endswith("_" + eid):
                    return child, node, i
                if allow_sequence_match and _get_id_sequence(c_eid) == target_seq:
                    return child, node, i
            res_node, res_parent, res_idx = self._find_node_and_parent(
                child,
                eid,
                allow_sequence_match=allow_sequence_match,
            )
            if res_node:
                return res_node, res_parent, res_idx
        return None, None, None

    def ground_ids(self):
        """Walks the entire statute and updates EIDs to match the Oracle map."""
        if not self.eid_map:
            return

        # Collect the full set of oracle EID values (the canonical IDs we want to
        # assign).  Used both for pre-seeding and in the main matching loop.
        oracle_id_values: set = set(self.eid_map.values())

        # Pre-seed seen_oracle_ids with EIDs that are already correct.
        # These nodes already carry an oracle-canonical EID and must NOT be
        # cleared — they would otherwise be reset to generic local IDs and
        # potentially mis-re-grounded to a different oracle EID.
        seen_oracle_ids: set = set()

        def _get_eid(node: UKMutableNode) -> Optional[str]:
            """Return the EID/id from a node's attrs (handles both 'eId' and 'id' keys)."""
            return node.attrs.get("eId") or node.attrs.get("id")

        def _set_eid(node: UKMutableNode, eid: str) -> None:
            """Set an EID on a node, using whichever key the node already uses."""
            if "eId" in node.attrs:
                node.attrs["eId"] = eid
            else:
                # Node uses 'id' key (UK legislation XML) or has no EID attr yet.
                # Use 'eId' as the canonical key going forward.
                node.attrs["eId"] = eid

        def _preseed_correct_eids(node: UKMutableNode) -> None:
            eid = _get_eid(node)
            if eid and eid in oracle_id_values:
                seen_oracle_ids.add(eid)
            for c in node.children:
                _preseed_correct_eids(c)

        if getattr(self.statute, "body", None):
            _preseed_correct_eids(self.statute.body)
        for sch in self.statute.supplements:
            _preseed_correct_eids(sch)

        def _clear_eids(node: UKMutableNode) -> None:
            """Clear EIDs that are NOT already in oracle (those stay for matching)."""
            eid = _get_eid(node)
            if eid and eid not in oracle_id_values:
                # Non-canonical EID — clear it so the grounding pass can assign
                # the correct oracle ID.
                for key in ("eId", "id"):
                    if key in node.attrs:
                        del node.attrs[key]
            # Children may need grounding even if the parent is already correct.
            for c in node.children:
                _clear_eids(c)

        if getattr(self.statute, "body", None):
            _clear_eids(self.statute.body)
        for sch in self.statute.supplements:
            _clear_eids(sch)

        # Pre-pass: ensure every node has a reasonable local eId.
        # Skip nodes that already have an oracle-canonical EID (under either
        # 'eId' or 'id' key) — those were preserved by _clear_eids and must
        # not be overwritten with a generic local label.
        def _ensure_local_eid(node: UKMutableNode) -> None:
            if "eId" not in node.attrs and "id" not in node.attrs and node.kind != "body":
                clean_label = _clean_num(node.label) if node.label else ""
                if clean_label:
                    node.attrs["eId"] = f"{node.kind}-{clean_label}"
                else:
                    node.attrs["eId"] = node.kind
            for c in node.children:
                _ensure_local_eid(c)

        if getattr(self.statute, "body", None):
            _ensure_local_eid(self.statute.body)
        for sch in self.statute.supplements:
            _ensure_local_eid(sch)

        def _slugify(text: str) -> str:
            if not text:
                return ""
            return re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")

        def _node_full_text(node: UKMutableNode) -> str:
            """Collect normalized full-subtree text for a node (matches oracle text_map)."""
            parts = []
            if node.text:
                parts.append(node.text.strip())
            for child in node.children:
                t = _node_full_text(child)
                if t:
                    parts.append(t)
            raw = " ".join(parts)
            return _normalize_text_for_grounding(raw)

        def _ground_node(node: UKMutableNode, parent_path_key, parent_eid=None, ordinal=1, context="body"):
            nonlocal seen_oracle_ids
            # Fast path: if this node already has a correct oracle EID (preserved
            # from the pre-seed pass), skip the multi-pass matching for this node
            # and recurse into children with updated context.  The EID is already
            # registered in seen_oracle_ids from the pre-seed pass.
            existing_eid = node.attrs.get("eId") or node.attrs.get("id")
            if existing_eid and existing_eid in oracle_id_values and existing_eid in seen_oracle_ids:
                kind = node.kind
                kind_name = str(kind).lower()
                clean_label = _clean_num(node.label) if node.label else ""
                next_path_key = uk_semantic_path_key(
                    parent_path_key,
                    kind=kind_name,
                    clean_label=clean_label,
                )
                new_context = context
                if kind_name == "schedule" and clean_label:
                    new_context = f"schedule-{clean_label}"
                elif kind_name == "body":
                    new_context = "body"
                kind_counts: dict = {}
                for child in node.children:
                    kind_counts[child.kind] = kind_counts.get(child.kind, 0) + 1
                    _ground_node(
                        child, next_path_key, existing_eid, ordinal=kind_counts[child.kind], context=new_context
                    )
                return

            clean_label = _clean_num(node.label) if node.label else ""
            kind = node.kind
            kind_name = str(kind).lower()
            raw_label = str(node.label or "").strip()
            heading = node.attrs.get("heading") or ""
            if (
                not heading
                and kind_name in ("p1group", "pblock", "crossheading", "chapter", "part")
                and node.text
                and len(node.text) < 200
            ):
                heading = node.text
            slug = _slugify(heading)

            node_key_part = f"{kind_name}-{clean_label}" if clean_label else (f"{kind_name}-{slug}" if slug else kind_name)

            # Use : as separator for semantic path matching against eid_map
            if not parent_path_key:
                hierarchical_path_key = str(node_key_part)
            else:
                hierarchical_path_key = f"{parent_path_key}:{node_key_part}"

            next_path_key = uk_semantic_path_key(
                parent_path_key,
                kind=kind_name,
                clean_label=clean_label or slug,
            )

            oracle_id = None
            matched_cand = None

            # Pass 0: Exact Hash Matching (NEW - Grounding 2.0)
            # ONLY match meaningful text to avoid dot-shell collisions.
            # Skip for: (a) structural containers (part/chapter/schedule) — heading text
            # can collide with inline term definitions, (b) nodes whose exact hierarchical
            # path exists in oracle eid_map — flat matching will succeed and is more precise
            # (prevents section-1 enacted text matching oracle's subsection-1-1 with same text).
            _structural_kinds = {"part", "chapter", "schedule", "annex"}
            # Kinds that may legitimately match oracle term-* EIDs (definition nodes).
            # All other structural kinds (section, paragraph, subsection …) must NOT be
            # grounded to a term-* oracle EID via hash — the hash collision is accidental
            # (e.g. paragraph-a whose text begins with a term name).
            _term_eid_kinds = {"p1group", "crossheading", "section", "article"}
            is_dots = bool(node.text and re.match(r"^[.\s]+$", node.text))
            _has_structural_path = str(hierarchical_path_key).lower() in self.eid_map
            if (
                not oracle_id
                and node.text
                and not is_dots
                and not _has_structural_path
                and kind_name not in _structural_kinds
            ):
                h = _semantic_hash(node.text)
                hash_key = f"hash:{h}"
                if hash_key in self.eid_map:
                    candidate_id = self.eid_map[hash_key]
                    if candidate_id not in seen_oracle_ids:
                        # Guard: reject a term-* oracle EID for non-term node kinds.
                        # Prevents paragraph-a (e.g. "(a) chief constable means…") from
                        # hash-colliding with the oracle's term-chief-constable definition.
                        _is_term_eid = candidate_id.startswith("term-")
                        if not _is_term_eid or kind_name in _term_eid_kinds:
                            oracle_id = candidate_id
                            matched_cand = f"hash:{h}"

            # Pass 0.5: Fuzzy Text Matching (NEW - Grounding 2.1)
            # Use node.text (direct text only) for the length/Levenshtein comparison.
            # Transparent wrapper nodes (p1group, crossheading) are excluded from fuzzy
            # matching because:
            #   (a) p1group direct text is typically empty — fuzzy wouldn't fire anyway
            #       but using full-subtree text would steal oracle EIDs from child sections.
            #   (b) crossheading direct text is the heading — it can fuzzy-match oracle
            #       term-* EIDs whose text equals the heading name.  Instead, a separate
            #       guard (below) blocks crossheading → term-* matches explicitly.
            # Non-transparent nodes (section, paragraph, subsection…) use direct text and
            # additionally must not fuzzy-match term-* oracle EIDs (same guard as hash pass).
            _fuzzy_skip_kinds = {"p1group", "pblock"}  # transparent wrappers whose children own the EIDs
            if (
                not oracle_id
                and node.text
                and not is_dots
                and not _has_structural_path
                and kind_name not in _structural_kinds
                and kind_name not in _fuzzy_skip_kinds
            ):
                node_norm = _normalize_text_for_grounding(node.text)
                if len(node_norm) > 30:
                    best_score = 0
                    best_id = None
                    for oid, otext in self.text_map.items():
                        if oid in seen_oracle_ids:
                            continue
                        if abs(len(otext) - len(node_norm)) > 0.1 * len(node_norm):
                            continue
                        score = Levenshtein.ratio(node_norm, otext)
                        if score > 0.92 and score > best_score:
                            best_score = score
                            best_id = oid
                    if best_id:
                        # Guard: crossheadings must not fuzzy-match term-* oracle EIDs.
                        # A crossheading "domestic abuse protection notices" should match
                        # oracle's crossheading EID (not term-domestic-abuse-protection-notice)
                        # even if the heading text and term text are nearly identical.
                        # When a crossheading matches a term-* EID the bench penalises the
                        # match because the crossheading's full subtree (all its sections) is
                        # compared to the oracle term's short text → very low text similarity.
                        _is_term_eid = best_id.startswith("term-")
                        if not _is_term_eid or kind_name not in ("crossheading", "pblock", "chapter"):
                            oracle_id = best_id
                            matched_cand = f"fuzzy:{best_score:.3f}"

            kind_syns: list[str] = [kind_name]
            if kind_name == "pblock":
                kind_syns.extend(["chapter", "crossheading", "eusection", "division"])
            elif kind_name == "chapter":
                kind_syns.extend(["pblock", "crossheading", "euchapter", "division"])
            elif kind_name == "crossheading":
                kind_syns.extend(["pblock", "chapter", "eusection", "division"])
            elif kind_name == "p1group":
                kind_syns.extend(["section", "crossheading", "paragraph", "article"])
            elif kind_name == "schedule":
                kind_syns.extend(["annex"])
            elif kind_name in ("section", "p1", "article"):
                kind_syns = ["section", "p1", "article"]
            elif kind_name in ("paragraph", "subsection", "p2", "p3", "subparagraph", "item", "point"):
                kind_syns = ["paragraph", "subsection", "p2", "p3", "subparagraph", "item", "point"]

            # Pass 1: Local & Flat Matching (High Priority for top-level nodes)
            if not oracle_id:
                flat_cands = []
                # Check hierarchical keys with synonyms
                for k in kind_syns:
                    parts = str(hierarchical_path_key).split(":")
                    last = parts[-1]
                    if "-" in last:
                        parts[-1] = f"{k}-{last.split('-', 1)[1]}"
                    else:
                        parts[-1] = k
                    flat_cands.append(":".join(parts).lower())

                # Check flat/suffix keys
                # crossheading/pblock are included so that ECHR-article Pblocks in
                # Schedule 1 can match oracle chapter-N EIDs via the suffix slug key.
                #
                # IMPORTANT: Suppress the short context:kind-label flat candidates for
                # sub-section-level nodes (paragraph, subsection, subparagraph, item)
                # that are deeply nested *inside a section* (parent_path_key contains
                # a "section-N" or "article-N" segment).  Without this guard a paragraph
                # node inside section-1-7 matches oracle's section-25-1-b via the shared
                # key "body:paragraph-b", stealing the oracle EID from section-25.
                # Structural containers (section, chapter, part, schedule) are NOT
                # restricted — their flat keys are the primary lookup path and they do
                # not collide across sections.
                _sub_kinds = {"paragraph", "subsection", "subparagraph", "item", "point", "p2", "p3"}
                _is_inside_section = bool(
                    kind_name in _sub_kinds and re.search(r":(section|article|rule|regulation)-", parent_path_key or "")
                )
                # Suppress flat matching for paragraph/subparagraph/item nodes inside
                # schedule chapters/parts. Without this guard, "paragraph 2" under
                # chapter-1 matches oracle's chapter-10-paragraph-2 via the shared
                # key "schedule-1:paragraph-2". Schedule descendant nodes must match
                # via hierarchical paths or hash/fuzzy, not flat context:kind-label keys.
                _is_inside_schedule_chapter = bool(
                    kind_name in _sub_kinds
                    and context.startswith("schedule")
                    and re.search(r":(chapter|part)-", parent_path_key or "")
                )
                _schedule_structural_flat = bool(
                    context.startswith("schedule") and kind_name in {"part", "chapter", "crossheading", "pblock", "division"}
                )
                if kind_name in (
                    "section",
                    "article",
                    "schedule",
                    "annex",
                    "part",
                    "chapter",
                    "paragraph",
                    "crossheading",
                    "pblock",
                    "division",
                ):
                    for k in kind_syns:
                        if clean_label:
                            if not _is_inside_section and not _is_inside_schedule_chapter:
                                flat_cands.append(f"{context}:{k}-{clean_label}")
                                flat_cands.append(f"{context}:suffix:{k}-{clean_label}")
                            if not _schedule_structural_flat:
                                flat_cands.append(f"{k}-{clean_label}")
                        elif slug:
                            if not _is_inside_section and not _is_inside_schedule_chapter:
                                flat_cands.append(f"{context}:suffix:{k}-{slug}")
                            if not _schedule_structural_flat:
                                flat_cands.append(f"{k}-{slug}")

                if kind_name == "subsection" and clean_label and parent_eid:
                    parent_match = re.match(
                        r"^(section|article|rule|regulation)-(.+)$",
                        parent_eid,
                        re.I,
                    )
                    if parent_match:
                        parent_suffix = _clean_num(parent_match.group(2))
                        if parent_suffix:
                            flat_cands.append(f"{context}:subsection-{parent_suffix}-{clean_label}")
                            flat_cands.append(f"{context}:suffix:subsection-{parent_suffix}-{clean_label}")
                            flat_cands.append(f"{parent_path_key}:subsection-{parent_suffix}-{clean_label}")

                for cand in flat_cands:
                    if cand.lower() in self.eid_map:
                        candidate_id = self.eid_map[cand.lower()]
                        if candidate_id not in seen_oracle_ids:
                            oracle_id = candidate_id
                            matched_cand = f"flat:{cand.lower()}"
                            break

            # Pass 3: Ordinal Matching (Fallback for non-semantic IDs)
            # Guard: before accepting an ordinal match, verify text similarity when the
            # oracle text_map has content for the candidate.  This prevents a case where
            # enacted section[1] inside part-1 matches oracle section[1]-inside-part-1
            # (which is section-21, a definitions section) purely by position even though
            # the content is completely different — e.g. enacted Part 1 had sections 1-20
            # but after amendments only section-21 (definitions) remains in oracle Part 1.
            #
            # Two-factor rejection:
            #   (a) length ratio: if max/min > 3.0, texts are too different in size.
            #   (b) Levenshtein ratio < 0.50: text content does not match well enough.
            # Either condition alone rejects the candidate.  Both must pass to accept.
            # Threshold 0.50 is intentionally strict because legitimate ordinal matches
            # (same provision at same structural position) will score 0.80+ while wrong
            # ordinal matches (different section at same ordinal slot after amendments)
            # typically score 0.30-0.55 even for similar legal vocabulary.
            _ORDINAL_LEN_RATIO_MAX = 3.0
            _ORDINAL_TEXT_THRESHOLD = 0.50
            if not oracle_id:
                ord_key = f"{parent_path_key}:{kind}[{ordinal}]".lower()
                if ord_key in self.eid_map:
                    candidate_id = self.eid_map[ord_key]
                    if candidate_id not in seen_oracle_ids:
                        # Text guard: if oracle has text for the candidate, require
                        # the node full text to be sufficiently similar to oracle text.
                        oracle_text = self.text_map.get(candidate_id, "")
                        accept = True
                        if oracle_text:
                            node_full = _node_full_text(node)
                            if node_full and len(node_full) > 20 and len(oracle_text) > 20:
                                max_len = max(len(node_full), len(oracle_text))
                                min_len = min(len(node_full), len(oracle_text))
                                if max_len / min_len > _ORDINAL_LEN_RATIO_MAX:
                                    accept = False
                                else:
                                    ratio = Levenshtein.ratio(node_full, oracle_text)
                                    if ratio < _ORDINAL_TEXT_THRESHOLD:
                                        accept = False
                        if accept:
                            oracle_id = candidate_id
                            matched_cand = f"ordinal:{ord_key}"

            if oracle_id:
                node.attrs["eId"] = oracle_id
                seen_oracle_ids.add(oracle_id)
                if matched_cand:
                    self._log(f"  Matched {node.kind} {node.label or ''} to {oracle_id} via {matched_cand}")
            else:
                if uk_is_transparent_wrapper_kind(kind_name):
                    if "eId" in node.attrs:
                        del node.attrs["eId"]
                elif parent_eid:
                    local_label = clean_label
                    if (
                        raw_label
                        and kind_name in {"subparagraph", "item", "point"}
                        and re.fullmatch(
                            r"[ivxlcdm]+",
                            raw_label,
                            re.IGNORECASE,
                        )
                    ):
                        local_label = raw_label.lower().strip(".")
                    part = local_label if local_label else kind_name
                    if context.startswith("schedule") and clean_label:
                        if kind_name in {"paragraph", "subparagraph", "subsection", "item", "point", "p2", "p3"}:
                            # UK schedule descendant IDs flatten nested paragraph/item levels
                            # to bare suffixes once the first schedule paragraph is established.
                            if re.search(r"(?:^|-)paragraph-[^-]+(?:-|$)", parent_eid):
                                part = local_label
                            else:
                                part = f"paragraph-{local_label}"
                        else:
                            part = f"{kind_name}-{clean_label}"
                    node.attrs["eId"] = f"{parent_eid}{'' if parent_eid.endswith('-') else '-'}{part}"

            kind_counts = {}
            new_context = context
            if kind_name == "schedule" and clean_label:
                new_context = f"schedule-{clean_label}"
            elif kind_name == "body":
                new_context = "body"

            actual_eid = node.attrs.get("eId", parent_eid)
            for child in node.children:
                kind_counts[child.kind] = kind_counts.get(child.kind, 0) + 1
                _ground_node(child, next_path_key, actual_eid, ordinal=kind_counts[child.kind], context=new_context)

        grounded_count = 0

        def _visit_count(n):
            nonlocal grounded_count
            eid = n.attrs.get("eId")
            if eid and eid in self.eid_map.values():
                grounded_count += 1
            for c in n.children:
                _visit_count(c)

        body_node = getattr(self.statute, "body", None)
        if body_node:
            kind_counts = {}
            for node in body_node.children:
                kind_counts[node.kind] = kind_counts.get(node.kind, 0) + 1
                _ground_node(node, "body", None, ordinal=kind_counts[node.kind], context="body")
            _visit_count(body_node)

        for i, sch in enumerate(self.statute.supplements):
            _ground_node(sch, "", None, ordinal=i + 1, context="schedule")
            _visit_count(sch)

        self._log(f"  EXECUTOR: grounded {grounded_count} nodes against Oracle map")


# ---------------------------------------------------------------------------
# Commencement-aware EID filtering
# ---------------------------------------------------------------------------

_COMMENCEMENT_EFFECT_TYPES = frozenset(
    {
        "coming into force",
        "commencement order",
    }
)

# Kind aliases used in LegalAddress paths that map to IR node kinds
_ADDR_KIND_ALIASES: dict[str, set[str]] = {
    "section": {"section", "article", "rule", "regulation", "p1group"},
    "schedule": {"schedule"},
    "paragraph": {"paragraph", "p1", "p2", "p3", "subparagraph"},
    "subsection": {"subsection", "paragraph"},
    "part": {"part"},
    "chapter": {"chapter"},
}


def _collect_all_eids(node: "IRNode") -> set[str]:
    """Recursively collect all eId/id attrs from a node and its descendants."""
    result: set[str] = set()
    eid = node.attrs.get("eId") or node.attrs.get("id")
    if eid:
        result.add(eid)
    for child in node.children:
        result.update(_collect_all_eids(child))
    return result


def _nodes_matching_address(
    nodes: Sequence["IRNode"],
    path: tuple[tuple[str, str], ...],
    depth: int = 0,
) -> list["IRNode"]:
    """Walk an IR node list and return nodes that match the LegalAddress path.

    The path is a sequence of (kind, label) pairs from LegalAddress.path.
    Matching is hierarchical: each step drills into children of matched nodes.
    An empty path means "match all nodes at this level."

    Transparent kinds (part, chapter, crossheading, p1group, etc.) are
    descended into without consuming a path component, so "s. 1" matches
    a section nested arbitrarily deep under structural containers.
    """
    if depth >= len(path):
        # Consumed the whole path — return all nodes at this level
        return list(nodes)

    addr_kind, addr_label = path[depth]
    accepted_ir_kinds = _ADDR_KIND_ALIASES.get(addr_kind, {addr_kind})
    # Remove transparent kinds from accepted set: a p1group with label=None
    # should never match an addr_label like '1'.
    non_transparent_accepted = {
        kind
        for kind in accepted_ir_kinds
        if kind not in {"part", "chapter", "wrapper", "hcontainer"} and not uk_is_transparent_wrapper_kind(kind)
    }

    matched: list["IRNode"] = []
    for node in nodes:
        # Always descend transparently into structural containers
        if uk_should_descend_transparently(node):
            matched.extend(_nodes_matching_address(node.children, path, depth))
            continue

        if node.kind not in non_transparent_accepted:
            continue

        # Normalise label for comparison: strip leading zeros, lowercase
        node_label = (node.label or "").strip().lower().lstrip("0") or (node.label or "").strip().lower()
        # Also normalise addr_label the same way
        addr_lbl_norm = addr_label.strip().lower().lstrip("0") or addr_label.strip().lower()

        if node_label != addr_lbl_norm:
            continue

        # This node matches this path component — descend for the rest
        if depth + 1 >= len(path):
            matched.append(node)
        else:
            sub = _nodes_matching_address(node.children, path, depth + 1)
            if sub:
                matched.extend(sub)
            else:
                # Path extends beyond what this node has — still include the
                # parent node (commencing s. 1(2) when only s. 1 exists is fine)
                matched.append(node)

    return matched


def commencement_eid_set(
    effects: list["UKEffectRecord"],
    statute_ir: "IRStatute",
) -> set[str]:
    """Return the set of EIDs that have been brought into force.

    Parses "coming into force" effects, maps their provision references to
    nodes in *statute_ir*, and returns the union of EIDs for those nodes,
    their descendants, AND any structural ancestor nodes (part, chapter,
    crossheading) that contain at least one commenced provision.

    An EID is "commenced" when at least one "coming into force" effect with
    a non-empty effective date covers the provision (or any ancestor).

    If no commencement effects are found at all, returns the full set of EIDs
    from the statute (treat all provisions as in force — self-commencement).
    """
    comm_effects = [
        e
        for e in effects
        if e.effect_type.lower() in _COMMENCEMENT_EFFECT_TYPES and e.effective_date  # must have a real date
    ]

    all_ir_nodes: list["IRNode"] = list(statute_ir.body.children)
    for sched in statute_ir.supplements:
        all_ir_nodes.append(sched)

    if not comm_effects:
        # No commencement orders found → treat all provisions as in force.
        all_eids: set[str] = set()
        for node in all_ir_nodes:
            all_eids.update(_collect_all_eids(node))
        return all_eids

    # Collect directly-commenced EIDs (section/schedule/paragraph nodes and descendants)
    commenced: set[str] = set()

    for effect in comm_effects:
        prov_str = effect.affected_provisions.strip()
        if not prov_str:
            continue

        # Split compound provision strings ("s. 1, s. 2-5") and parse each
        prov_parts = _split_metadata_provisions(prov_str)
        for part in prov_parts:
            part = part.strip()
            if not part:
                continue

            addr = _parse_affected_target(part)

            # whole_act special: everything is commenced
            if str(addr.special or "") == "whole_act":
                for node in all_ir_nodes:
                    commenced.update(_collect_all_eids(node))
                return commenced

            if not addr.path:
                continue

            matching = _nodes_matching_address(all_ir_nodes, addr.path)
            for node in matching:
                commenced.update(_collect_all_eids(node))

    # Bubble-up pass: add structural ancestors (part, chapter, crossheading, pblock,
    # p1group, wrapper) whose subtrees contain at least one commenced EID.
    # These structural EIDs appear in the oracle but are never named in commencement
    # orders (you commence provisions, not the containers holding them).
    def _add_structural_ancestors(nodes: Sequence["IRNode"]) -> bool:
        """Return True if any descendant is in commenced.  Side-effect: adds structural EIDs."""
        any_child_commenced = False
        for node in nodes:
            eid = node.attrs.get("eId") or node.attrs.get("id")
            if eid and eid in commenced:
                any_child_commenced = True
                continue
            # Recurse into all nodes to find committed descendants
            sub_commenced = _add_structural_ancestors(node.children)
            if sub_commenced:
                any_child_commenced = True
                if uk_should_bubble_structural_commencement(node) and eid:
                    commenced.add(eid)
        return any_child_commenced

    _add_structural_ancestors(all_ir_nodes)

    return commenced


# ---------------------------------------------------------------------------
# Public replay API
# ---------------------------------------------------------------------------


def _prepare_replay_uk_ops(
    ops: list[LegalOperation],
    *,
    verbose: bool = False,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
) -> list[LegalOperation]:
    """Normalize replay ops so every entry point applies the same semantics."""
    filtered_ops: list[LegalOperation] = []
    for op in ops:
        if str(op.target.special or "") == "whole_act":
            if _action_name(op.action) == "repeal":
                filtered_ops.append(op)
                continue
            if verbose:
                print("  replay_uk_ops: skipping unsupported whole_act op")
            _append_uk_replay_adjudication(
                adjudications_out,
                kind="uk_replay_unsupported_action",
                message="UK replay prepare step skipped unsupported whole-act target before replay apply.",
                op=op,
                detail={
                    "action": _action_name(op.action),
                    "target": str(op.target),
                    "reason": "whole_act_prepare_filter",
                },
            )
            continue
        filtered_ops.append(op)
    return filtered_ops


def replay_uk_ops(
    base: IRStatute,
    ops: list[LegalOperation],
    *,
    eid_map: Optional[dict[str, str]] = None,
    text_map: Optional[dict[str, str]] = None,
    allow_oracle_alignment: bool = True,
    verbose: bool = False,
    lo_ops_out: Optional[List[LegalOperation]] = None,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
) -> IRStatute:
    """Apply compiled UK legal operations to enacted base, return amended statute.

    This is the primary public entry point for the UK replay engine.  It wraps
    UKReplayExecutor with a clean function signature so callers do not need to
    instantiate the executor directly.

    Args:
        base:       Enacted (base) IRStatute produced by parse_uk_statute_ir().
        ops:        Compiled LegalOperation list from compile_effect_to_ir_ops()
                    or UKReplayPipeline.compile_ops_for_statute().
        eid_map:    Optional oracle EID map for grounding (key → oracle EID).
        text_map:   Optional oracle text map for fuzzy-text grounding.
        allow_oracle_alignment:
                    When True, replay-time oracle adapter behavior is enabled:
                    oracle-zombie collapse preparation plus post-apply EID grounding.
                    When False, replay runs without ORACLE_ALIGNMENT_ONLY mutation help.
        verbose:    If True, executor prints each applied op to stdout.
        lo_ops_out: Optional list to collect top-section snapshots after each
                    structural op.  Pass an empty list; it will be populated with
                    legal operations suitable for replay timelines.
        adjudications_out: Optional list to collect replay skip/no-op adjudications.
                    Entries are `CompileAdjudication` with one of the `uk_replay_*`
                    kinds defined by this executor.

    Returns:
        A new IRStatute with all ops applied (deep copy — base is not mutated).

    Op ordering:
        Ops are applied in the order supplied.  Callers should pre-sort by
        (effective_date, sequence) before passing.  UKReplayPipeline already
        does this in compile_ops_for_statute().
    """
    if verbose:
        print(f"  replay_uk_ops: applying {len(ops)} ops to {base.statute_id}")
    _filtered_ops = _prepare_replay_uk_ops(
        ops,
        verbose=verbose,
        adjudications_out=adjudications_out,
    )

    executor = UKReplayExecutor(
        base,
        eid_map=(eid_map or {}) if allow_oracle_alignment else {},
        text_map=(text_map or {}) if allow_oracle_alignment else {},
        verbose=verbose,
        lo_ops_out=lo_ops_out,
        adjudications_out=adjudications_out,
    )
    for op in _filtered_ops:
        executor.apply_op(op)

    if adjudications_out is not None:
        frozen_statute = executor.statute.to_irstatute()
        duplicate_findings = build_text_duplication_findings(
            frozen_statute.body,
            phase="replay_fold",
            source_statute=base.statute_id,
        )
        for schedule in frozen_statute.supplements:
            schedule_findings = build_text_duplication_findings(
                schedule,
                phase="replay_fold",
                source_statute=base.statute_id,
            )
            patched_schedule_findings = [
                dc_replace(finding, detail={"root": f"schedule:{schedule.label or '?'}", **finding.detail})
                for finding in schedule_findings
            ]
            duplicate_findings.extend(patched_schedule_findings)

        seen_duplicate_keys = {
            (
                adjudication.kind,
                adjudication.message,
                adjudication.source_statute,
                json.dumps(adjudication.detail, sort_keys=True, ensure_ascii=False),
            )
            for adjudication in adjudications_out
        }
        for finding in duplicate_findings:
            adjudication = _uk_adjudication_from_finding(finding)
            key = (
                adjudication.kind,
                adjudication.message,
                adjudication.source_statute,
                json.dumps(adjudication.detail, sort_keys=True, ensure_ascii=False),
            )
            if key in seen_duplicate_keys:
                continue
            adjudications_out.append(adjudication)
            seen_duplicate_keys.add(key)

    return executor.statute.to_irstatute()


# ---------------------------------------------------------------------------
# Affecting Act Fetcher
# ---------------------------------------------------------------------------


def fetch_affecting_act(act_id: str, out_path: Path, dry_run: bool = False) -> bool:
    url = f"{_LEG_BASE}/{act_id}/data.xml"
    print(f"  fetch {url} -> {out_path}")
    if dry_run:
        return True
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
        out_path.write_bytes(data)
        meta = {"url": url, "bytes": len(data)}
        out_path.with_suffix(".xml.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        return True
    except (HTTPError, URLError, OSError) as exc:
        print(f"  ERROR fetching {url}: {exc}")
        return False


def fetch_affecting_acts_from_manifest(
    manifest: dict[str, Any], repo_root: Path, dry_run: bool = False, limit: Optional[int] = None
) -> tuple[int, int]:
    sources = manifest.get("sources", [])
    if limit:
        sources = sources[:limit]
    ok = fail = 0
    for src in sources:
        for artifact in src.get("artifacts", []):
            out = repo_root / artifact["path"]
            if out.exists():
                ok += 1
                continue
            success = fetch_affecting_act(src["act_id"], out, dry_run=dry_run)
            if success:
                ok += 1
            else:
                fail += 1
    return ok, fail
