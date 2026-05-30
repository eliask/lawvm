"""Diagnostic witness search for UK repeal semantics.

This module does not change replay. It inventories source-backed candidates for
future rules such as "repeal of a repeal does not revive" and "do not double-enter
the same repeal from both body text and a repeal schedule".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from lawvm.uk_legislation.effect_source_selection import (
    extracted_tag_and_text,
    select_source_for_effect,
)
from lawvm.uk_legislation.effects import UKEffectRecord, load_effects_for_statute_from_archive
from lawvm.uk_legislation.source_context import UKAffectingSourceContext


_REPEAL_EFFECT_WORDS = ("repeal", "revoke", "omit", "cease")
_WHITESPACE_RE = re.compile(r"\s+")
_REPEAL_OF_REPEAL_RE = re.compile(
    r"\brepeal(?:ed|ing)?\s+of\s+(?:a|an|the)?\s{0,8}repeal(?:ed|ing)?\b",
    re.I,
)
_NO_REVIVE_RE = re.compile(r"\b(?:does\s+not|shall\s+not|is\s+not\s+to)\s+revive\b", re.I)
_REVIVAL_RE = re.compile(r"\b(?:revive|revives|revived|revival)\b", re.I)


@dataclass(frozen=True)
class UKRepealSemanticsWitness:
    """One diagnostic candidate for UK repeal-semantics follow-up."""

    family: str
    statute_id: str
    effect_id: str
    effect_type: str
    affected_provisions: str
    affecting_act_id: str
    affecting_provisions: str
    rule_id: str
    source_status: str = ""
    source_tag: str = ""
    source_text_preview: str = ""
    duplicate_count: int = 0
    related_effect_ids: tuple[str, ...] = ()
    related_affecting_provisions: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "family": self.family,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "effect_type": self.effect_type,
            "affected_provisions": self.affected_provisions,
            "affecting_act_id": self.affecting_act_id,
            "affecting_provisions": self.affecting_provisions,
            "rule_id": self.rule_id,
        }
        if self.source_status:
            row["source_status"] = self.source_status
        if self.source_tag:
            row["source_tag"] = self.source_tag
        if self.source_text_preview:
            row["source_text_preview"] = self.source_text_preview
        if self.duplicate_count:
            row["duplicate_count"] = self.duplicate_count
        if self.related_effect_ids:
            row["related_effect_ids"] = self.related_effect_ids
        if self.related_affecting_provisions:
            row["related_affecting_provisions"] = self.related_affecting_provisions
        row.update(self.detail)
        return row


def is_repeal_semantics_effect(effect: UKEffectRecord) -> bool:
    """Return whether an effect belongs to the repeal/revocation/omission family."""
    normalized = normalize_repeal_semantics_text(effect.effect_type)
    return any(word in normalized for word in _REPEAL_EFFECT_WORDS)


def normalize_repeal_semantics_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", str(text or "").strip().lower())


def source_text_repeal_semantics_family(source_text: str) -> str:
    """Return a source-text witness family, or ``""`` when no phrase matched."""
    normalized = normalize_repeal_semantics_text(source_text)
    if "repeal" not in normalized and "reviv" not in normalized:
        return ""
    if _NO_REVIVE_RE.search(normalized):
        return "repeal_of_repeal_no_revive_phrase"
    if _REPEAL_OF_REPEAL_RE.search(normalized):
        return "repeal_of_repeal_phrase"
    if "repeal" in normalized and _REVIVAL_RE.search(normalized):
        return "repeal_revival_phrase"
    return ""


def scan_repeal_semantics_witnesses_for_statute(
    statute_id: str,
    archive: Any,
    *,
    applicability_mode: str = "effective_date_plus_feed_applied",
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> tuple[UKRepealSemanticsWitness, ...]:
    """Scan one affected statute for diagnostic repeal-semantics candidates."""
    effect_parse_diagnostics: list[dict[str, Any]] = []
    effects = load_effects_for_statute_from_archive(
        statute_id,
        archive,
        parse_rejections_out=effect_parse_diagnostics,
    )
    if diagnostics_out is not None:
        diagnostics_out.extend(effect_parse_diagnostics)
    repeal_effects = tuple(effect for effect in effects if is_repeal_semantics_effect(effect))
    witnesses: list[UKRepealSemanticsWitness] = []
    witnesses.extend(_duplicate_repeal_target_witnesses(statute_id, repeal_effects))
    witnesses.extend(
        _source_phrase_witnesses(
            statute_id,
            repeal_effects,
            archive,
            applicability_mode=applicability_mode,
            diagnostics_out=diagnostics_out,
        )
    )
    return tuple(witnesses)


def scan_repeal_semantics_witnesses(
    statute_ids: Iterable[str],
    archive: Any,
    *,
    applicability_mode: str = "effective_date_plus_feed_applied",
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> tuple[UKRepealSemanticsWitness, ...]:
    witnesses: list[UKRepealSemanticsWitness] = []
    for statute_id in statute_ids:
        witnesses.extend(
            scan_repeal_semantics_witnesses_for_statute(
                statute_id,
                archive,
                applicability_mode=applicability_mode,
                diagnostics_out=diagnostics_out,
            )
        )
    return tuple(witnesses)


def _duplicate_repeal_target_witnesses(
    statute_id: str,
    repeal_effects: tuple[UKEffectRecord, ...],
) -> tuple[UKRepealSemanticsWitness, ...]:
    groups: dict[tuple[str, str, str], list[UKEffectRecord]] = defaultdict(list)
    for effect in repeal_effects:
        affected_key = normalize_repeal_semantics_text(effect.affected_provisions)
        if not affected_key:
            continue
        key = (
            effect.affecting_act_id,
            affected_key,
            _repeal_effect_family_key(effect.effect_type),
        )
        groups[key].append(effect)

    witnesses: list[UKRepealSemanticsWitness] = []
    for group in groups.values():
        affecting_refs = tuple(
            sorted({str(effect.affecting_provisions or "") for effect in group if effect.affecting_provisions})
        )
        if len(group) < 2 or len(affecting_refs) < 2:
            continue
        family = _duplicate_repeal_target_family(affecting_refs)
        rule_id = "uk_repeal_semantics_duplicate_target_candidate"
        if family == "body_schedule_repeal_double_entry_candidate":
            rule_id = "uk_repeal_semantics_body_schedule_double_entry_candidate"
        first = group[0]
        witnesses.append(
            UKRepealSemanticsWitness(
                family=family,
                statute_id=statute_id,
                effect_id=str(first.effect_id or ""),
                effect_type=first.effect_type,
                affected_provisions=first.affected_provisions,
                affecting_act_id=first.affecting_act_id,
                affecting_provisions=first.affecting_provisions,
                rule_id=rule_id,
                duplicate_count=len(group),
                related_effect_ids=tuple(str(effect.effect_id or "") for effect in group),
                related_affecting_provisions=affecting_refs,
                detail={"candidate_reason": "same affected repeal target appears under multiple affecting provisions"},
            )
        )
    return tuple(witnesses)


def _duplicate_repeal_target_family(affecting_refs: tuple[str, ...]) -> str:
    normalized = tuple(_normalize_source_ref(ref) for ref in affecting_refs)
    for shorter in normalized:
        if not shorter.startswith("sch."):
            continue
        for longer in normalized:
            if longer == shorter:
                continue
            if longer.endswith(shorter):
                return "body_schedule_repeal_double_entry_candidate"
    return "duplicate_repeal_target_candidate"


def _source_phrase_witnesses(
    statute_id: str,
    repeal_effects: tuple[UKEffectRecord, ...],
    archive: Any,
    *,
    applicability_mode: str,
    diagnostics_out: list[dict[str, Any]] | None,
) -> tuple[UKRepealSemanticsWitness, ...]:
    witnesses: list[UKRepealSemanticsWitness] = []
    extraction_cache: dict[str, UKAffectingSourceContext] = {}
    enacted_extraction_cache: dict[str, UKAffectingSourceContext] = {}
    for effect in repeal_effects:
        selection = select_source_for_effect(
            effect=effect,
            archive=archive,
            applicability_mode=applicability_mode,
            extraction_cache=extraction_cache,
            enacted_extraction_cache=enacted_extraction_cache,
            effect_diagnostics_out=diagnostics_out,
        )
        tag_and_text = extracted_tag_and_text(selection.extracted_el)
        family = source_text_repeal_semantics_family(tag_and_text.text)
        if not family:
            continue
        witnesses.append(
            UKRepealSemanticsWitness(
                family=family,
                statute_id=statute_id,
                effect_id=str(effect.effect_id or ""),
                effect_type=effect.effect_type,
                affected_provisions=effect.affected_provisions,
                affecting_act_id=effect.affecting_act_id,
                affecting_provisions=effect.affecting_provisions,
                rule_id=f"uk_repeal_semantics_{family}",
                source_status=selection.source_context.source_status,
                source_tag=tag_and_text.tag or "",
                source_text_preview=_preview(tag_and_text.text),
            )
        )
    return tuple(witnesses)


def _repeal_effect_family_key(effect_type: str) -> str:
    normalized = normalize_repeal_semantics_text(effect_type)
    if "revoke" in normalized:
        return "revocation"
    if "omit" in normalized:
        return "omission"
    if "cease" in normalized:
        return "cease_effect"
    return "repeal"


def _normalize_source_ref(text: str) -> str:
    return normalize_repeal_semantics_text(text).strip(" .,;")


def _preview(text: str, *, limit: int = 240) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."
