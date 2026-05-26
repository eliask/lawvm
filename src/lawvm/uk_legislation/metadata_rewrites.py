"""UK effect-metadata rewrite helpers.

These helpers lower explicit UK effect-feed metadata such as "renumbered as"
without letting the replay layer infer a move from text coincidence.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _addr_leaf_label,
)
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.target_parser import _parse_affected_target
from lawvm.uk_legislation.uk_grafter import _LEG_NS, _clean_num, _extract_num
from lawvm.uk_legislation.xml_helpers import _tag


@dataclass(frozen=True)
class UKMetadataRenumberTargets:
    source_target: LegalAddress
    destination: LegalAddress
    rule_id: str
    reason_code: str
    reason: str
    metadata_destination: Optional[LegalAddress] = None


@dataclass(frozen=True)
class UKUnsupportedMetadataRenumber:
    source_target: LegalAddress
    destination: LegalAddress
    rule_id: str
    reason_code: str
    reason: str


def _renumbered_descendant_text(
    text: str,
    *,
    source_label: Optional[str],
    destination_label: Optional[str],
) -> str:
    source_clean = _clean_num(source_label or "")
    destination_clean = _clean_num(destination_label or "")
    if not text or not source_clean or not destination_clean:
        return text
    pattern = re.compile(rf"^\s*{re.escape(source_clean)}(?![0-9A-Za-z])[\s\u00a0]*")
    if pattern.search(text):
        return pattern.sub(destination_clean, text, count=1)
    return text


def _uk_metadata_renumber_targets(effect: UKEffectRecord) -> Optional[UKMetadataRenumberTargets]:
    """Return source/destination targets for an explicit UK metadata renumber row.

    This is deliberately narrow: supported shapes are a provision becoming its
    own immediate descendant, for example ``Sch. 9 para. 132`` to
    ``Sch. 9 para. 132(1)``, or a same-parent/same-kind sibling renumber such as
    ``s. 16(9)`` to ``s. 16(8)``. Broader moves/renumbers stay unsupported until
    they have their own lineage semantics.
    """

    effect_type = " ".join(str(effect.effect_type or "").replace("\u00a0", " ").split())
    match = re.fullmatch(r"(?P<source>.+?)\s+renumbered\s+as\s+(?P<dest>.+)", effect_type, flags=re.I)
    if match is None:
        return None
    source_ref = " ".join(match.group("source").split())
    words_in_match = re.fullmatch(r"words?\s+in\s+(?P<target>.+)", source_ref, flags=re.I)
    if words_in_match is not None:
        source_ref = words_in_match.group("target")
    source_target = canonicalize_uk_address(_parse_affected_target(source_ref))
    destination = canonicalize_uk_address(_parse_affected_target(match.group("dest")))
    if len(destination.path) == len(source_target.path) + 1 and destination.path[:-1] == source_target.path:
        return UKMetadataRenumberTargets(
            source_target=source_target,
            destination=destination,
            rule_id="uk_effect_metadata_renumber_lowered",
            reason_code="explicit_effect_metadata_descendant_renumber",
            reason=(
                "UK effect metadata explicitly says the source provision is "
                "renumbered as its own immediate descendant; lowering preserves "
                "that typed renumber instead of treating the row as nonstructural"
            ),
        )
    if (
        len(destination.path) == len(source_target.path)
        and destination.path[:-1] == source_target.path[:-1]
        and _addr_leaf_kind(destination) == _addr_leaf_kind(source_target)
        and _addr_leaf_label(destination) != _addr_leaf_label(source_target)
    ):
        return UKMetadataRenumberTargets(
            source_target=source_target,
            destination=destination,
            rule_id="uk_effect_metadata_sibling_renumber_lowered",
            reason_code="explicit_effect_metadata_same_parent_sibling_renumber",
            reason=(
                "UK effect metadata explicitly says a provision is renumbered "
                "as a same-parent sibling; lowering preserves a typed renumber "
                "instead of replaying the row as another repeal of the destination label"
            ),
        )
    return None


def _uk_unsupported_metadata_renumber_rejection(
    effect: UKEffectRecord,
) -> Optional[UKUnsupportedMetadataRenumber]:
    """Return a specific blocker for explicit metadata renumber rows outside replay support."""
    if _uk_metadata_renumber_targets(effect) is not None:
        return None
    effect_type = " ".join(str(effect.effect_type or "").replace("\u00a0", " ").split())
    match = re.fullmatch(r"(?P<source>.+?)\s+renumbered\s+as\s+(?P<dest>.+)", effect_type, flags=re.I)
    if match is None:
        return None
    source_ref = " ".join(match.group("source").split())
    words_in_match = re.fullmatch(r"words?\s+in\s+(?P<target>.+)", source_ref, flags=re.I)
    if words_in_match is not None:
        source_ref = words_in_match.group("target")
    source_target = canonicalize_uk_address(_parse_affected_target(source_ref))
    destination = canonicalize_uk_address(_parse_affected_target(match.group("dest")))
    if source_target.path[:1] != destination.path[:1]:
        return UKUnsupportedMetadataRenumber(
            source_target=source_target,
            destination=destination,
            rule_id="uk_effect_metadata_cross_container_renumber_rejected",
            reason_code="explicit_effect_metadata_cross_container_renumber",
            reason=(
                "UK effect metadata explicitly renumbers a provision into a "
                "different top-level container. This is a lineage/migration "
                "operation, not a same-parent relabel or same-provision "
                "descendant wrap, so replay blocks it until cross-container "
                "migration semantics are owned."
            ),
        )
    return UKUnsupportedMetadataRenumber(
        source_target=source_target,
        destination=destination,
        rule_id="uk_effect_metadata_unsupported_renumber_rejected",
        reason_code="explicit_effect_metadata_unsupported_renumber_shape",
        reason=(
            "UK effect metadata explicitly renumbers a provision, but the shape "
            "is outside the currently owned same-provision descendant and "
            "same-parent sibling replay semantics."
        ),
    )


def _uk_source_text_corrected_renumber_targets(
    metadata_targets: Optional[UKMetadataRenumberTargets],
    extracted_text: Optional[str],
) -> Optional[UKMetadataRenumberTargets]:
    if metadata_targets is None:
        return None
    text = " ".join(str(extracted_text or "").replace("\u00a0", " ").split())
    match = re.search(
        r"\bbecomes?\s+(?:paragraph|sub-?paragraph|subsection|section)\s+\(?(?P<label>[0-9A-Za-z]+)\)?",
        text,
        flags=re.I,
    )
    if match is None:
        return metadata_targets
    source_label = _clean_num(match.group("label"))
    if not source_label:
        return metadata_targets
    destination_leaf_kind, destination_leaf_label = metadata_targets.destination.path[-1]
    if _clean_num(destination_leaf_label) == source_label:
        return metadata_targets
    if metadata_targets.destination.path[:-1] != metadata_targets.source_target.path:
        return metadata_targets
    corrected_destination = LegalAddress(
        path=(
            *metadata_targets.source_target.path,
            (destination_leaf_kind, source_label),
        )
    )
    return UKMetadataRenumberTargets(
        source_target=metadata_targets.source_target,
        destination=corrected_destination,
        rule_id="uk_effect_source_text_renumber_destination_corrected",
        reason_code="source_text_destination_label_overrides_effect_metadata",
        reason=(
            "UK effect metadata supplies a descendant renumber destination, "
            "but the extracted operative source text states a different "
            "destination label; lowering preserves the source-stated label "
            "and records the metadata destination as a corrected witness."
        ),
        metadata_destination=metadata_targets.destination,
    )


def _uk_affected_target_corrected_renumber_targets(
    effect: UKEffectRecord,
    extracted_text: Optional[str],
) -> Optional[UKMetadataRenumberTargets]:
    """Correct apparent effect-type destination drift using source text plus affected target.

    Some UK effect rows display a renumber destination that disagrees with the
    operative source instruction and the affected target.  This correction is
    deliberately narrow: the source must say the existing provision becomes an
    immediate descendant, and the effect affected-provisions surface must name
    exactly that descendant under the parsed source provision.
    """
    unsupported = _uk_unsupported_metadata_renumber_rejection(effect)
    if unsupported is None:
        return None
    text = " ".join(str(extracted_text or "").replace("\u00a0", " ").split())
    match = re.search(
        r"\bbecomes?\s+(?:paragraph|sub-?paragraph|subsection|section)\s+\(?(?P<label>[0-9A-Za-z]+)\)?",
        text,
        flags=re.I,
    )
    if match is None:
        return None
    descendant_label = _clean_num(match.group("label"))
    if not descendant_label:
        return None
    affected_target = canonicalize_uk_address(_parse_affected_target(effect.affected_provisions))
    if affected_target.path[:-1] != unsupported.source_target.path:
        return None
    if _clean_num(_addr_leaf_label(affected_target)) != descendant_label:
        return None
    return UKMetadataRenumberTargets(
        source_target=unsupported.source_target,
        destination=affected_target,
        rule_id="uk_effect_source_text_and_affected_target_renumber_corrected",
        reason_code="source_text_and_affected_target_override_effect_metadata_destination",
        reason=(
            "UK effect-type metadata supplies a cross-container renumber "
            "destination, but the extracted source text and affected-provisions "
            "surface agree on a same-provision descendant renumber; lowering "
            "uses the source/affected target and records the metadata "
            "destination as a corrected witness."
        ),
        metadata_destination=unsupported.destination,
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
