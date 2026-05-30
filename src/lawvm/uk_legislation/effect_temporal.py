"""Source-backed temporal recovery for UK effects."""
from __future__ import annotations

from lxml import etree as ET
from typing import Any, NamedTuple, Optional, Sequence

from lawvm.core.temporal_resolution import (
    TEMPORAL_RECOVERY_FAMILY,
    TEMPORAL_SOURCE_BACKED_OVERRIDE,
    TEMPORAL_UNKNOWN_EFFECTIVE_DATE,
    TemporalResolutionEvidence,
)
from lawvm.uk_legislation.effects import UKEffectRecord


UK_UNDATED_APPLIED_SI_COMMENCEMENT_DATE_RULE_ID = (
    "uk_effect_undated_applied_si_commencement_date"
)
UK_UNDATED_APPLIED_SI_COMMENCEMENT_UNRESOLVED_RULE_ID = (
    "uk_effect_undated_applied_si_commencement_unresolved"
)

_LEG_BASE = "https://www.legislation.gov.uk"
_UKM_NS = "http://www.legislation.gov.uk/namespaces/metadata"


class UKAffectingInstrumentXML(NamedTuple):
    xml_bytes: bytes
    source_locator: str


class UKCommencementMetadata(NamedTuple):
    effective_date: str
    source_locator: str
    status: str
    dates: tuple[str, ...] = ()
    parse_error: str = ""


def _instrument_commencement_metadata(
    xml_bytes: bytes,
    *,
    source_locator: str,
) -> UKCommencementMetadata:
    if not xml_bytes:
        return UKCommencementMetadata(
            effective_date="",
            source_locator="",
            status="source_xml_unavailable",
        )
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return UKCommencementMetadata(
            effective_date="",
            source_locator=source_locator,
            status="source_xml_parse_error",
            parse_error=str(exc),
        )
    dates = {
        str(elem.attrib.get("Date") or "").strip()
        for elem in root.findall(f".//{{{_UKM_NS}}}ComingIntoForce/{{{_UKM_NS}}}DateTime")
        if str(elem.attrib.get("Date") or "").strip()
    }
    if len(dates) != 1:
        return UKCommencementMetadata(
            effective_date="",
            source_locator=source_locator,
            status="textual_or_missing_date" if not dates else "multiple_or_textual",
            dates=tuple(sorted(dates)),
        )
    date = next(iter(dates))
    return UKCommencementMetadata(
        effective_date=date,
        source_locator=source_locator,
        status="single_date",
        dates=(date,),
    )


def _load_affecting_instrument_xml(act_id: str, archive: Any) -> UKAffectingInstrumentXML:
    for suffix in ("data.xml", "enacted/data.xml"):
        locator = f"{_LEG_BASE}/{act_id}/{suffix}"
        data = archive.get(locator)
        if data:
            return UKAffectingInstrumentXML(xml_bytes=data, source_locator=locator)
    return UKAffectingInstrumentXML(xml_bytes=b"", source_locator="")


def _effect_allows_instrument_commencement_fallback(effect: UKEffectRecord) -> bool:
    if effect.effective_date:
        return False
    if not (effect.applied or effect.metadata_only):
        return False
    return effect.affecting_class == "UnitedKingdomStatutoryInstrument"


def _effect_detail(effect: UKEffectRecord) -> dict[str, Any]:
    return {
        "effect_id": effect.effect_id,
        "affecting_act_id": effect.affecting_act_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_provisions": effect.affecting_provisions,
        "effect_type": effect.effect_type,
    }


def _append_commencement_override_observation(
    diagnostics_out: list[dict[str, Any]],
    *,
    effect: UKEffectRecord,
    metadata: UKCommencementMetadata,
) -> None:
    diagnostics_out.append(
        TemporalResolutionEvidence(
            rule_id=UK_UNDATED_APPLIED_SI_COMMENCEMENT_DATE_RULE_ID,
            family=TEMPORAL_RECOVERY_FAMILY,
            phase="lowering",
            reason=(
                "UK effect feed marked this statutory-instrument effect as applied "
                "but omitted an effect-level in-force date; LawVM used the single "
                "official instrument commencement date from affecting-act metadata."
            ),
            status=TEMPORAL_SOURCE_BACKED_OVERRIDE,
            blocking=False,
            effective_date=metadata.effective_date,
            source_locator=metadata.source_locator,
            authority_layer="AFFECTING_ACT_METADATA",
            detail=_effect_detail(effect),
        ).to_diagnostic_detail()
    )


def _append_commencement_unresolved_observation(
    diagnostics_out: list[dict[str, Any]],
    *,
    effect: UKEffectRecord,
    metadata: UKCommencementMetadata,
) -> None:
    detail = {
        **_effect_detail(effect),
        "commencement_metadata_status": metadata.status,
        "commencement_metadata_dates": metadata.dates,
    }
    if metadata.parse_error:
        detail["parse_error"] = metadata.parse_error
    diagnostics_out.append(
        TemporalResolutionEvidence(
            rule_id=UK_UNDATED_APPLIED_SI_COMMENCEMENT_UNRESOLVED_RULE_ID,
            family=TEMPORAL_RECOVERY_FAMILY,
            phase="lowering",
            reason=(
                "UK effect feed marked this statutory-instrument effect as applied "
                "but omitted an effect-level in-force date; LawVM did not use an SI "
                "commencement fallback because the affecting instrument metadata "
                "does not expose exactly one commencement date."
            ),
            status=TEMPORAL_UNKNOWN_EFFECTIVE_DATE,
            blocking=False,
            source_locator=metadata.source_locator,
            authority_layer="AFFECTING_ACT_METADATA" if metadata.source_locator else "",
            detail=detail,
        ).to_diagnostic_detail()
    )


def resolve_uk_effective_date_overrides_for_replay(
    effects: Sequence[UKEffectRecord],
    archive: Any,
    *,
    diagnostics_out: Optional[list[dict[str, Any]]] = None,
) -> dict[str, str]:
    """Return source-backed replay dates for applied undated SI effects.

    The UK effect feed sometimes marks an effect as applied while omitting an
    effect-level ``InForce`` date. For statutory instruments with exactly one
    official instrument commencement date in metadata, that date is a source
    fact rather than an editorial modified timestamp. This resolver only
    supplies such dates; it does not use made dates or infer commencement from
    the instrument year.
    """
    by_act: dict[str, UKCommencementMetadata] = {}
    overrides: dict[str, str] = {}
    for effect in effects:
        if not _effect_allows_instrument_commencement_fallback(effect):
            continue
        act_id = effect.affecting_act_id
        if act_id not in by_act:
            source = _load_affecting_instrument_xml(act_id, archive)
            by_act[act_id] = _instrument_commencement_metadata(
                source.xml_bytes,
                source_locator=source.source_locator,
            )
        metadata = by_act[act_id]
        if not metadata.effective_date:
            if diagnostics_out is not None:
                _append_commencement_unresolved_observation(
                    diagnostics_out,
                    effect=effect,
                    metadata=metadata,
                )
            continue
        overrides[effect.effect_id] = metadata.effective_date
        if diagnostics_out is not None:
            _append_commencement_override_observation(
                diagnostics_out,
                effect=effect,
                metadata=metadata,
            )
    return overrides
