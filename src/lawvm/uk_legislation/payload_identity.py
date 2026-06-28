from __future__ import annotations

import re
from dataclasses import replace as dc_replace
from typing import Any, Optional

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.addressing import _addr_container, _addr_field, _canonicalize_eid_tail_label
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.target_anchors import _fallback_target_eid
from lawvm.uk_legislation.uk_grafter import _clean_num


UK_WHOLE_SCHEDULE_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID = (
    "uk_whole_schedule_payload_descendant_eid_synthesis"
)
UK_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID = "uk_payload_descendant_eid_synthesis"
UK_PAYLOAD_FOREIGN_SOURCE_ID_RETARGETED_RULE_ID = (
    "uk_payload_foreign_source_id_retargeted"
)

# legislation.gov.uk physical source ids are ``pNNNN`` (and descendant variants
# like ``pNNNN-1``). When an inserted provision payload is carried from the
# AFFECTING act, its root node retains that act's physical ``id`` rather than an
# eId in the affected act's numbering scheme. That foreign id is meaningless in
# the affected act and must NOT seed descendant identity synthesis.
_UK_FOREIGN_PHYSICAL_SOURCE_ID_RE = re.compile(r"^p\d{3,}(?:-.*)?$")


def _is_foreign_physical_source_id(identity: str) -> bool:
    """Return True when *identity* is a legislation.gov.uk physical source id.

    A real eId in the affected act's numbering scheme (``section-138A``,
    ``schedule-1-paragraph-3``) is authoritative and preserved. A bare physical
    id (``p02828``) carried from the affecting act's source XML is foreign and
    cannot anchor descendant eIds in the affected act.
    """

    return bool(_UK_FOREIGN_PHYSICAL_SOURCE_ID_RE.match(identity))


def _payload_identity_diagnostic(
    *,
    rule_id: str,
    reason: str,
    blocking: bool,
    **detail: Any,
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id=rule_id,
        family="payload_identity_normalization",
        phase="payload_normalization",
        reason=reason,
        blocking=blocking,
        detail=detail,
    )


def _whole_schedule_target_root_eid(target: LegalAddress) -> str:
    if _addr_container(target) != "schedule" or len(target.path) != 1:
        return ""
    schedule_label = _addr_field(target, "schedule")
    if not schedule_label:
        return ""
    return f"schedule-{_clean_num(schedule_label)}"


def _whole_schedule_payload_local_suffix(parent_eid: str, child: IRNode) -> str:
    kind_name = str(child.kind or "").lower()
    raw_label = str(child.label or "").strip()
    clean_label = _clean_num(raw_label).strip("().")
    if kind_name == "crossheading":
        heading_text = " ".join(str(child.text or raw_label).split()).strip()
        heading_slug = re.sub(r"[^a-zA-Z0-9]+", "-", heading_text.lower()).strip("-")
        if heading_slug:
            return f"crossheading-{heading_slug}"
        return ""
    if (
        raw_label
        and kind_name in {"subparagraph", "item", "point"}
        and re.fullmatch(r"[ivxlcdm]+", raw_label, re.IGNORECASE)
    ):
        clean_label = raw_label.lower().strip(".")
    if not clean_label:
        return ""
    if kind_name in {"paragraph", "subparagraph", "subsection", "item", "point", "p2", "p3"}:
        if re.search(r"(?:^|-)paragraph-[^-]+(?:-|$)", parent_eid):
            return clean_label
        return f"paragraph-{clean_label}"
    return f"{kind_name}-{clean_label}"


def _payload_local_suffix(child: IRNode) -> str:
    raw_label = str(child.label or "").strip()
    return _canonicalize_eid_tail_label(raw_label)


# Body section-like provisions always carry a flat root eId (``section-289A``,
# ``article-4``) even when nested inside a part or chapter. When a whole-chapter
# or whole-part payload is inserted, its section children must therefore reset
# the descendant-eId namespace to that flat root rather than inherit the
# container's hierarchical eId.
_UK_BODY_SECTION_LIKE_KINDS = {"section", "article", "rule", "regulation"}


def _payload_flat_section_root_eid(child: IRNode) -> str:
    """Return the flat root eId for a body section-like payload child, or ``''``."""
    kind_name = str(child.kind or "").lower()
    if kind_name not in _UK_BODY_SECTION_LIKE_KINDS:
        return ""
    clean_label = _clean_num(str(child.label or "")).strip()
    if not clean_label:
        return ""
    return f"{kind_name}-{clean_label}"


def _synthesize_whole_schedule_payload_descendant_eids(
    payload_node: IRNode,
    *,
    target: LegalAddress,
    effect: UKEffectRecord,
    lowering_records_out: Optional[list[dict[str, Any]]],
    allow_payload_identity_synthesis: bool,
) -> IRNode:
    """Own local descendant IDs for whole-schedule payloads before replay.

    This is source-local identity normalization, not oracle alignment: it only
    runs for an explicit single-schedule target and derives descendants from
    the target-owned root EID plus parsed labels.
    """
    if payload_node.kind != IRNodeKind.SCHEDULE and str(payload_node.kind) != "schedule":
        return payload_node
    root_eid = _whole_schedule_target_root_eid(target)
    if not root_eid:
        return payload_node
    existing_root_eid = str(payload_node.attrs.get("eId") or payload_node.attrs.get("id") or "")
    if not existing_root_eid:
        # PR2 (audit XJUR-02 / AGENTS.md §2.3): rebuild a fresh IRNode
        # rather than mutating ``attrs`` in place.
        payload_node = dc_replace(
            payload_node, attrs={**dict(payload_node.attrs), "eId": root_eid}
        )
    else:
        root_eid = existing_root_eid

    if not allow_payload_identity_synthesis:
        if lowering_records_out is not None:
            lowering_records_out.append(
                _payload_identity_diagnostic(
                    rule_id=UK_WHOLE_SCHEDULE_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID,
                    reason=(
                        "Whole-schedule payload has descendants without source EIDs; "
                        "strict lowering did not synthesize local descendant identity"
                    ),
                    blocking=True,
                    effect_id=effect.effect_id,
                    affecting_act_id=effect.affecting_act_id,
                    affected_provisions=effect.affected_provisions,
                    affecting_provisions=effect.affecting_provisions,
                    effect_type=effect.effect_type,
                    target=str(target),
                )
            )
        return payload_node

    synthesized: list[dict[str, Any]] = []
    used_eids: set[str] = {root_eid}
    skipped_ambiguous = 0
    skipped_duplicate = 0

    def _walk(parent_eid: str, current: IRNode) -> IRNode:
        """Return a new IRNode with synthesized descendant eIds.

        PR2 (audit XJUR-02 / AGENTS.md §2.3): no in-place mutation of the
        parsed payload node. Each level rebuilds its children via
        ``dataclasses.replace`` and returns a fresh ``IRNode``;
        ``child.attrs["eId"] = ...`` becomes ``dc_replace(child, attrs=...)``.
        """
        nonlocal skipped_ambiguous, skipped_duplicate
        new_children: list[IRNode] = []
        for child in current.children:
            child_kind_name = str(child.kind or "").lower()
            existing_eid = str(child.attrs.get("eId") or child.attrs.get("id") or "")
            child_parent_eid = existing_eid or parent_eid
            if existing_eid:
                used_eids.add(existing_eid)
            else:
                suffix = _whole_schedule_payload_local_suffix(parent_eid, child)
                if suffix:
                    child_parent_eid = f"{parent_eid}{'' if parent_eid.endswith('-') else '-'}{suffix}"
                    if child_parent_eid in used_eids:
                        skipped_duplicate += 1
                        child_parent_eid = parent_eid
                        if child.children:
                            skipped_ambiguous += 1
                        new_children.append(_walk(child_parent_eid, child))
                        continue
                    used_eids.add(child_parent_eid)
                    child = dc_replace(
                        child, attrs={**dict(child.attrs), "eId": child_parent_eid}
                    )
                    if child_kind_name == "crossheading":
                        child_parent_eid = parent_eid
                    synthesized.append(
                        {
                            "kind": str(child.kind),
                            "label": child.label,
                            "parent_eid": parent_eid,
                            "after_eid": child_parent_eid,
                        }
                    )
                elif child.children:
                    skipped_ambiguous += 1
            new_children.append(_walk(child_parent_eid, child))
        return dc_replace(current, children=new_children)

    payload_node = _walk(root_eid, payload_node)
    if synthesized and lowering_records_out is not None:
        lowering_records_out.append(
            _payload_identity_diagnostic(
                rule_id=UK_WHOLE_SCHEDULE_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID,
                reason=(
                    "Whole-schedule payload descendants lacked source EIDs; "
                    "lowering synthesized deterministic local IDs from the explicit schedule target"
                ),
                blocking=False,
                effect_id=effect.effect_id,
                affecting_act_id=effect.affecting_act_id,
                affected_provisions=effect.affected_provisions,
                affecting_provisions=effect.affecting_provisions,
                effect_type=effect.effect_type,
                target=str(target),
                root_eid=root_eid,
                synthesized_count=len(synthesized),
                skipped_ambiguous_count=skipped_ambiguous,
                skipped_duplicate_count=skipped_duplicate,
                sample=synthesized[:8],
            )
        )
    return payload_node


def _synthesize_payload_descendant_eids(
    payload_node: IRNode,
    *,
    target: LegalAddress,
    effect: UKEffectRecord,
    lowering_records_out: Optional[list[dict[str, Any]]],
    allow_payload_identity_synthesis: bool,
) -> IRNode:
    """Own local descendant IDs for non-schedule source-backed payload trees."""
    if str(payload_node.kind).lower() == "schedule":
        return payload_node
    explicit_eid = str(payload_node.attrs.get("eId") or "")
    foreign_id = str(payload_node.attrs.get("id") or "")
    root_eid = explicit_eid or foreign_id
    retargeted_from: Optional[str] = None
    # An inserted provision whose only identity is a foreign physical source id
    # (carried from the affecting act, e.g. ``p02828``) must be re-anchored to
    # the affected act's target-derived eId; otherwise the whole inserted
    # provision and all its descendants land under a foreign id namespace
    # (``p02828-1`` …) that can never match the affected act's eId scheme.
    if not explicit_eid and foreign_id and _is_foreign_physical_source_id(foreign_id):
        derived = _fallback_target_eid(target)
        if derived:
            retargeted_from = foreign_id
            root_eid = derived
            # PR2 (audit XJUR-02 / AGENTS.md §2.3): rebuild attrs via a fresh
            # dict and publish a new IRNode via ``dataclasses.replace``,
            # instead of mutating ``payload_node.attrs`` in place (set ``eId``
            # to the derived id AND drop the foreign physical ``id``).
            new_attrs = {**dict(payload_node.attrs), "eId": derived}
            new_attrs.pop("id", None)
            payload_node = dc_replace(payload_node, attrs=new_attrs)
    if not root_eid:
        root_eid = _fallback_target_eid(target)
        if root_eid:
            payload_node = dc_replace(
                payload_node, attrs={**dict(payload_node.attrs), "eId": root_eid}
            )
    if retargeted_from and lowering_records_out is not None:
        lowering_records_out.append(
            _payload_identity_diagnostic(
                rule_id=UK_PAYLOAD_FOREIGN_SOURCE_ID_RETARGETED_RULE_ID,
                reason=(
                    "Inserted provision payload root carried a foreign physical "
                    "source id from the affecting act; lowering re-anchored it to "
                    "the affected act's target-derived eId."
                ),
                blocking=False,
                effect_id=effect.effect_id,
                affecting_act_id=effect.affecting_act_id,
                affected_provisions=effect.affected_provisions,
                affecting_provisions=effect.affecting_provisions,
                effect_type=effect.effect_type,
                target=str(target),
                foreign_source_id=retargeted_from,
                root_eid=root_eid,
            )
        )
    if not root_eid or not payload_node.children:
        return payload_node

    if not allow_payload_identity_synthesis:
        if lowering_records_out is not None:
            lowering_records_out.append(
                _payload_identity_diagnostic(
                    rule_id=UK_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID,
                    reason=(
                        "Source-backed payload has descendants without source EIDs; "
                        "strict lowering did not synthesize local descendant identity"
                    ),
                    blocking=True,
                    effect_id=effect.effect_id,
                    affecting_act_id=effect.affecting_act_id,
                    affected_provisions=effect.affected_provisions,
                    affecting_provisions=effect.affecting_provisions,
                    effect_type=effect.effect_type,
                    target=str(target),
                )
            )
        return payload_node

    synthesized: list[dict[str, Any]] = []
    used_eids: set[str] = {root_eid}
    skipped_duplicate = 0

    def _walk(parent_eid: str, current: IRNode) -> IRNode:
        """Return a new IRNode with synthesized descendant eIds.

        PR2 (audit XJUR-02 / AGENTS.md §2.3): no in-place mutation of the
        parsed payload node. Each level rebuilds its children via
        ``dataclasses.replace`` and returns a fresh ``IRNode``;
        ``child.attrs["eId"] = ...`` becomes ``dc_replace(child, attrs=...)``.
        """
        nonlocal skipped_duplicate
        new_children: list[IRNode] = []
        for child in current.children:
            child_eid = str(child.attrs.get("eId") or child.attrs.get("id") or "")
            child_parent_eid = child_eid or parent_eid
            if child_eid:
                used_eids.add(child_eid)
            else:
                # A section-like provision nested inside a part/chapter payload
                # carries a FLAT root eId (``section-289A``) and re-roots the
                # descendant namespace, rather than inheriting the container eId.
                flat_root = _payload_flat_section_root_eid(child)
                suffix = "" if flat_root else _payload_local_suffix(child)
                if flat_root:
                    if flat_root in used_eids:
                        skipped_duplicate += 1
                        child_parent_eid = parent_eid
                    else:
                        used_eids.add(flat_root)
                        child = dc_replace(
                            child, attrs={**dict(child.attrs), "eId": flat_root}
                        )
                        child_parent_eid = flat_root
                        synthesized.append(
                            {
                                "kind": str(child.kind),
                                "label": child.label,
                                "parent_eid": parent_eid,
                                "after_eid": flat_root,
                            }
                        )
                elif suffix:
                    child_parent_eid = f"{parent_eid}{'' if parent_eid.endswith('-') else '-'}{suffix}"
                    if child_parent_eid in used_eids:
                        skipped_duplicate += 1
                        child_parent_eid = parent_eid
                    else:
                        used_eids.add(child_parent_eid)
                        child = dc_replace(
                            child,
                            attrs={**dict(child.attrs), "eId": child_parent_eid},
                        )
                        synthesized.append(
                            {
                                "kind": str(child.kind),
                                "label": child.label,
                                "parent_eid": parent_eid,
                                "after_eid": child_parent_eid,
                            }
                        )
            new_children.append(_walk(child_parent_eid, child))
        return dc_replace(current, children=new_children)

    payload_node = _walk(root_eid, payload_node)
    if synthesized and lowering_records_out is not None:
        lowering_records_out.append(
            _payload_identity_diagnostic(
                rule_id=UK_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID,
                reason=(
                    "Source-backed payload descendants lacked source EIDs; "
                    "lowering synthesized deterministic local IDs from the explicit target"
                ),
                blocking=False,
                effect_id=effect.effect_id,
                affecting_act_id=effect.affecting_act_id,
                affected_provisions=effect.affected_provisions,
                affecting_provisions=effect.affecting_provisions,
                effect_type=effect.effect_type,
                target=str(target),
                root_eid=root_eid,
                synthesized_count=len(synthesized),
                skipped_duplicate_count=skipped_duplicate,
                sample=synthesized[:8],
            )
        )
    return payload_node
