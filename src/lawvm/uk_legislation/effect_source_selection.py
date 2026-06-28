"""Affecting-source selection for UK effect replay."""

from __future__ import annotations

from lxml import etree as ET
import time
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional

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
    extracted_el: Optional[ET._Element]
    source_required_for_replay: bool


class ExtractedTagAndText(NamedTuple):
    tag: str | None
    text: str


_EFFECT_FEED_INDEX_CONTEXT_CACHE: dict[Any, UKAffectingSourceContext] = {}


def _effect_feed_index_source_context(
    provision_extractor=extract_provision_element_from_bytes,
) -> UKAffectingSourceContext:
    cached = _EFFECT_FEED_INDEX_CONTEXT_CACHE.get(provision_extractor)
    if cached is not None:
        return cached
    source_context, _parse_error = _build_affecting_source_context(
        xml_bytes=None,
        locator="",
        authority_layer="EFFECT_FEED_INDEX",
        provision_extractor=provision_extractor,
    )
    _EFFECT_FEED_INDEX_CONTEXT_CACHE[provision_extractor] = source_context
    return source_context


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
        return _effect_feed_index_source_context(provision_extractor)
    if not effect.affecting_class_is_recognized:
        # Affecting class has no document-type slug mapping AND no usable
        # AffectingURI; ``effect.affecting_act_id`` would raise
        # ``UnmappedAffectingClass`` (AGENTS.md §1.10). Surface the residual
        # loudly via the typed finding instead of producing an invalid
        # ``cls.lower()`` slug that 404s and reads to a human as a generic
        # missing-XML error.
        if effect_diagnostics_out is not None:
            from lawvm.uk_legislation.source_state import (
                uk_affecting_act_class_unmapped_rejection,
            )

            effect_diagnostics_out.append(
                uk_affecting_act_class_unmapped_rejection(
                    effect_id=str(effect.effect_id or ""),
                    affecting_act_id="",
                    locator=str(effect.affecting_uri or ""),
                    affecting_class=str(effect.affecting_class or ""),
                )
            )
        return _effect_feed_index_source_context(provision_extractor)
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
    source_phase_timings_out: Optional[dict[str, float]] = None,
) -> EffectSourceSelection:
    phase_t0 = time.perf_counter()

    def _mark_source_phase(name: str) -> None:
        nonlocal phase_t0
        now = time.perf_counter()
        if source_phase_timings_out is not None:
            source_phase_timings_out[name] = source_phase_timings_out.get(name, 0.0) + (
                now - phase_t0
            )
        phase_t0 = now

    source_required_for_replay = uk_effect_requires_affecting_source_for_replay(
        effect,
        applicability_mode=applicability_mode,
    )
    _mark_source_phase("compile_source_required_check")
    source_context = source_context_for_effect(
        effect=effect,
        source_required_for_replay=source_required_for_replay,
        archive=archive,
        extraction_cache=extraction_cache,
        effect_diagnostics_out=effect_diagnostics_out,
        current_xml_loader=current_xml_loader,
        provision_extractor=provision_extractor,
    )
    _mark_source_phase("compile_source_context")
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
    _mark_source_phase("compile_source_extract_current")
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
    _mark_source_phase("compile_source_select_enacted")
    if effect_diagnostics_out is not None:
        effect_diagnostics_out.extend(source_extraction_observations)
        effect_diagnostics_out.extend(source_lane_observations)
    return EffectSourceSelection(
        source_context=source_context,
        extracted_el=extracted_el,
        source_required_for_replay=source_required_for_replay,
    )


def extracted_tag_and_text(el: Optional[ET._Element]) -> ExtractedTagAndText:
    if el is None:
        return ExtractedTagAndText(None, "")
    return ExtractedTagAndText(
        tag=el.tag.rsplit("}", 1)[-1],
        text=" ".join(
            text.strip()
            for text in (str(part) for part in el.itertext())
            if text and text.strip()
        ),
    )
