"""UK archive source-surface availability classification."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

MIN_UK_XML_SOURCE_BYTES = 100
UK_AFFECTING_ACT_XML_SOURCE_RULE_IDS = frozenset(
    {
        "uk_affecting_act_xml_missing_rejected",
        "uk_affecting_act_xml_too_small_rejected",
        "uk_affecting_act_xml_parse_rejected",
        "uk_affecting_act_xml_cached_recorded",
        "uk_affecting_act_current_shell_enacted_source_selected",
    }
)


class UKSourceStatus(StrEnum):
    ABSENT = "absent"
    TOO_SMALL = "too_small"
    AVAILABLE = "available"


@dataclass(frozen=True)
class UKSourceState:
    status: UKSourceStatus
    size: int

    @property
    def available(self) -> bool:
        return self.status is UKSourceStatus.AVAILABLE

    @property
    def missing(self) -> bool:
        return not self.available

    def as_legacy_tuple(self) -> tuple[str, int]:
        return self.status.value, self.size


def classify_uk_source_blob(blob: bytes | None) -> UKSourceState:
    if blob is None:
        return UKSourceState(status=UKSourceStatus.ABSENT, size=0)
    size = len(blob)
    if size < MIN_UK_XML_SOURCE_BYTES:
        return UKSourceState(status=UKSourceStatus.TOO_SMALL, size=size)
    return UKSourceState(status=UKSourceStatus.AVAILABLE, size=size)


def uk_source_state_wire_tuple(blob: bytes | None) -> tuple[str, int]:
    """Return the stable CLI/CSV wire shape for UK source availability."""
    return classify_uk_source_blob(blob).as_legacy_tuple()


def classify_uk_source_blob_legacy(blob: bytes | None) -> tuple[str, int]:
    return uk_source_state_wire_tuple(blob)


def uk_source_xml_parse_rejection(
    *,
    statute_id: str,
    side: str,
    source_url: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "rule_id": f"uk_{side}_xml_parse_rejected",
        "family": "source_pathology",
        "phase": "parse",
        "statute_id": statute_id,
        "side": side,
        "source_url": source_url,
        "reason": "UK source XML was available but could not be parsed.",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }


def uk_source_parse_observations_from_ir(ir: Any) -> list[dict[str, Any]]:
    """Return nonblocking source-parse observations carried by parsed UK IR."""
    metadata = getattr(ir, "metadata", {}) or {}
    rows = metadata.get("source_parse_observations", ())
    if not isinstance(rows, (list, tuple)):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def uk_affecting_act_xml_missing_rejection(
    *,
    effect_id: str,
    affecting_act_id: str,
    locator: str,
) -> dict[str, Any]:
    return {
        "rule_id": "uk_affecting_act_xml_missing_rejected",
        "family": "source_pathology",
        "phase": "acquisition",
        "effect_id": effect_id,
        "affecting_act_id": affecting_act_id,
        "locator": locator,
        "reason": "UK affecting act XML was missing from the archive, so the effect source fragment could not be extracted.",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }


def uk_affecting_act_xml_parse_rejection(
    *,
    effect_id: str,
    affecting_act_id: str,
    locator: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "rule_id": "uk_affecting_act_xml_parse_rejected",
        "family": "source_pathology",
        "phase": "parse",
        "effect_id": effect_id,
        "affecting_act_id": affecting_act_id,
        "locator": locator,
        "reason": "UK affecting act XML was available but could not be parsed, so the effect source fragment could not be extracted.",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }


def uk_affecting_act_xml_too_small_rejection(
    *,
    effect_id: str,
    affecting_act_id: str,
    locator: str,
    source_size: int,
) -> dict[str, Any]:
    return {
        "rule_id": "uk_affecting_act_xml_too_small_rejected",
        "family": "source_pathology",
        "phase": "acquisition",
        "effect_id": effect_id,
        "affecting_act_id": affecting_act_id,
        "locator": locator,
        "source_size": int(source_size),
        "reason": "UK affecting act XML was present but too small to trust, so the effect source fragment could not be extracted.",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }


def uk_affecting_act_current_shell_enacted_source_selected(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    current_locator: str,
    enacted_locator: str,
    current_source_size: int,
    enacted_source_size: int,
    current_text_preview: str,
    enacted_text_preview: str,
) -> dict[str, Any]:
    return {
        "rule_id": "uk_affecting_act_current_shell_enacted_source_selected",
        "family": "source_lane_selection",
        "phase": "acquisition",
        "effect_id": effect_id,
        "affecting_act_id": affecting_act_id,
        "affecting_provisions": affecting_provisions,
        "current_locator": current_locator,
        "enacted_locator": enacted_locator,
        "current_source_size": int(current_source_size),
        "enacted_source_size": int(enacted_source_size),
        "current_text_preview": current_text_preview,
        "enacted_text_preview": enacted_text_preview,
        "reason": (
            "UK current affecting-act XML extracted only a non-substantive dot-leader "
            "shell, while the official enacted XML contained substantive text for the "
            "same affecting provision."
        ),
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
    }


def is_uk_affecting_act_xml_source_observation(row: dict[str, Any]) -> bool:
    return str(row.get("rule_id") or "") in UK_AFFECTING_ACT_XML_SOURCE_RULE_IDS


def is_uk_affecting_act_xml_source_diagnostic(row: dict[str, Any]) -> bool:
    return is_uk_affecting_act_xml_source_observation(row)
