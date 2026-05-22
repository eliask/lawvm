"""Affecting-source selection for UK effect replay."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    get_affecting_act_enacted_xml_from_archive,
    get_affecting_act_xml_from_archive,
    uk_effect_requires_affecting_source_for_replay,
)
from lawvm.uk_legislation.provision_extractor import (
    extract_provision_element_from_bytes,
)
from lawvm.uk_legislation.source_context import (
    UKAffectingSourceContext,
    _append_affecting_source_context_diagnostic,
    _build_affecting_source_context,
    _extract_from_affecting_source_context_with_observations,
    _select_enacted_source_for_current_shell,
)


@dataclass(frozen=True)
class EffectSourceSelection:
    source_context: UKAffectingSourceContext
    extracted_el: Optional[ET.Element]
    source_required_for_replay: bool


def source_context_for_effect(
    *,
    effect: UKEffectRecord,
    source_required_for_replay: bool,
    archive: Any,
    extraction_cache: dict[str, UKAffectingSourceContext],
    effect_diagnostics_out: Optional[list[dict[str, Any]]],
    current_xml_loader=get_affecting_act_xml_from_archive,
    provision_extractor=extract_provision_element_from_bytes,
) -> UKAffectingSourceContext:
    """Return the current affecting-source context for one UK effect row."""
    if not source_required_for_replay:
        source_context, _parse_error = _build_affecting_source_context(
            xml_bytes=None,
            locator="",
            authority_layer="EFFECT_FEED_INDEX",
            provision_extractor=provision_extractor,
        )
        return source_context
    if effect.affecting_act_id in extraction_cache:
        return extraction_cache[effect.affecting_act_id]

    current_locator = f"https://www.legislation.gov.uk/{effect.affecting_act_id}/data.xml"
    source_context, parse_error = _build_affecting_source_context(
        xml_bytes=current_xml_loader(effect.affecting_act_id, archive),
        locator=current_locator,
        authority_layer="AFFECTING_ACT_TEXT",
        provision_extractor=provision_extractor,
    )
    _append_affecting_source_context_diagnostic(
        effect_diagnostics_out,
        effect=effect,
        source_context=source_context,
        parse_error=parse_error,
    )
    extraction_cache[effect.affecting_act_id] = source_context
    return source_context


def select_source_for_effect(
    *,
    effect: UKEffectRecord,
    archive: Any,
    applicability_mode: str,
    extraction_cache: dict[str, UKAffectingSourceContext],
    enacted_extraction_cache: dict[str, UKAffectingSourceContext],
    effect_diagnostics_out: Optional[list[dict[str, Any]]],
    current_xml_loader=get_affecting_act_xml_from_archive,
    enacted_xml_loader=get_affecting_act_enacted_xml_from_archive,
    provision_extractor=extract_provision_element_from_bytes,
) -> EffectSourceSelection:
    source_required_for_replay = uk_effect_requires_affecting_source_for_replay(
        effect,
        applicability_mode=applicability_mode,
    )
    source_context = source_context_for_effect(
        effect=effect,
        source_required_for_replay=source_required_for_replay,
        archive=archive,
        extraction_cache=extraction_cache,
        effect_diagnostics_out=effect_diagnostics_out,
        current_xml_loader=current_xml_loader,
        provision_extractor=provision_extractor,
    )
    if not source_required_for_replay:
        return EffectSourceSelection(
            source_context=source_context,
            extracted_el=None,
            source_required_for_replay=source_required_for_replay,
        )
    extracted_el, source_extraction_observations = (
        _extract_from_affecting_source_context_with_observations(
            source_context,
            effect,
        )
    )
    source_context, extracted_el, source_lane_observations = (
        _select_enacted_source_for_current_shell(
            effect=effect,
            archive=archive,
            current_context=source_context,
            current_el=extracted_el,
            enacted_context_cache=enacted_extraction_cache,
            enacted_xml_loader=enacted_xml_loader,
        )
    )
    if effect_diagnostics_out is not None:
        effect_diagnostics_out.extend(source_extraction_observations)
        effect_diagnostics_out.extend(source_lane_observations)
    return EffectSourceSelection(
        source_context=source_context,
        extracted_el=extracted_el,
        source_required_for_replay=source_required_for_replay,
    )


def extracted_tag_and_text(el: Optional[ET.Element]) -> tuple[Optional[str], str]:
    if el is None:
        return None, ""
    return (
        el.tag.rsplit("}", 1)[-1],
        " ".join(t.strip() for t in el.itertext() if t and t.strip()),
    )
