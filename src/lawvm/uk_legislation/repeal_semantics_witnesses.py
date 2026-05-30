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

from lxml import etree as ET

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
_SOURCE_PHRASE_TEXT_ELEMENTS = frozenset({"Text", "P"})


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


def scan_repeal_semantics_source_phrase_xml(
    statute_id: str,
    xml_bytes: bytes,
    *,
    source_locator: str = "",
) -> tuple[UKRepealSemanticsWitness, ...]:
    """Scan one source XML blob directly for repeal/revival semantic phrases.

    This is a fast corpus-mining lane. It does not prove that a phrase belongs to
    an executable effect row, but it can find candidate source documents before
    the slower effect-linked scan resolves every affecting-source context.
    """
    if not xml_bytes:
        return ()
    try:
        root = ET.fromstring(xml_bytes)
    except ET.XMLSyntaxError:
        return ()
    witnesses: list[UKRepealSemanticsWitness] = []
    seen: set[tuple[str, str]] = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local_name = ET.QName(el).localname
        if local_name not in _SOURCE_PHRASE_TEXT_ELEMENTS:
            continue
        text = _element_text(el)
        family = source_text_repeal_semantics_family(text)
        if not family:
            continue
        key = (family, normalize_repeal_semantics_text(text))
        if key in seen:
            continue
        seen.add(key)
        witnesses.append(
            UKRepealSemanticsWitness(
                family=family,
                statute_id=statute_id,
                effect_id="",
                effect_type="",
                affected_provisions="",
                affecting_act_id=statute_id,
                affecting_provisions="",
                rule_id=f"uk_repeal_semantics_source_phrase_{family}",
                source_status="source_phrase_scan",
                source_tag=local_name,
                source_text_preview=_preview(text),
                detail={"source_locator": source_locator} if source_locator else {},
            )
        )
    return tuple(witnesses)


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


def scan_repeal_semantics_affecting_act_phrase_candidates_for_statute(
    statute_id: str,
    archive: Any,
    *,
    phrase_witnesses_by_act: dict[str, tuple[UKRepealSemanticsWitness, ...]],
    audit_selected_source: bool = False,
    applicability_mode: str = "effective_date_plus_feed_applied",
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> tuple[UKRepealSemanticsWitness, ...]:
    """Link repeal effects to phrase-bearing affecting Acts without source extraction.

    This is a bounded candidate lane between the cheap source-phrase scan and the
    expensive selected-source scan. It proves that a repeal-family effect is made
    by an affecting Act whose official XML contains a repeal/revival semantic
    phrase; it does not prove that the phrase is in the selected source provision.
    """
    effect_parse_diagnostics: list[dict[str, Any]] = []
    effects = load_effects_for_statute_from_archive(
        statute_id,
        archive,
        parse_rejections_out=effect_parse_diagnostics,
    )
    if diagnostics_out is not None:
        diagnostics_out.extend(effect_parse_diagnostics)
    repeal_effects = tuple(effect for effect in effects if is_repeal_semantics_effect(effect))
    return _affecting_act_phrase_effect_witnesses(
        statute_id,
        repeal_effects,
        phrase_witnesses_by_act,
        archive=archive if audit_selected_source else None,
        applicability_mode=applicability_mode,
        diagnostics_out=diagnostics_out,
    )


def scan_repeal_semantics_affecting_act_phrase_candidates(
    statute_ids: Iterable[str],
    archive: Any,
    *,
    phrase_witnesses_by_act: dict[str, tuple[UKRepealSemanticsWitness, ...]],
    audit_selected_source: bool = False,
    applicability_mode: str = "effective_date_plus_feed_applied",
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> tuple[UKRepealSemanticsWitness, ...]:
    witnesses: list[UKRepealSemanticsWitness] = []
    for statute_id in statute_ids:
        witnesses.extend(
            scan_repeal_semantics_affecting_act_phrase_candidates_for_statute(
                statute_id,
                archive,
                phrase_witnesses_by_act=phrase_witnesses_by_act,
                audit_selected_source=audit_selected_source,
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


def _affecting_act_phrase_effect_witnesses(
    statute_id: str,
    repeal_effects: tuple[UKEffectRecord, ...],
    phrase_witnesses_by_act: dict[str, tuple[UKRepealSemanticsWitness, ...]],
    *,
    archive: Any | None = None,
    applicability_mode: str = "effective_date_plus_feed_applied",
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> tuple[UKRepealSemanticsWitness, ...]:
    witnesses: list[UKRepealSemanticsWitness] = []
    extraction_cache: dict[str, UKAffectingSourceContext] = {}
    enacted_extraction_cache: dict[str, UKAffectingSourceContext] = {}
    for effect in repeal_effects:
        if not is_repeal_semantics_effect(effect):
            continue
        phrase_witnesses = phrase_witnesses_by_act.get(effect.affecting_act_id, ())
        if not phrase_witnesses:
            continue
        by_family: dict[str, list[UKRepealSemanticsWitness]] = defaultdict(list)
        for phrase_witness in phrase_witnesses:
            by_family[phrase_witness.family].append(phrase_witness)
        for phrase_family, family_witnesses in sorted(by_family.items()):
            first_phrase = family_witnesses[0]
            detail: dict[str, Any] = {
                "candidate_reason": (
                    "repeal-family effect is made by an affecting Act whose "
                    "official source contains a repeal/revival semantic phrase; "
                    "selected source provision is not yet proved"
                ),
                "source_phrase_family": phrase_family,
                "source_phrase_rule_id": first_phrase.rule_id,
                "source_phrase_count": len(family_witnesses),
                "source_locator": first_phrase.detail.get("source_locator", ""),
            }
            if archive is not None:
                detail.update(
                    _selected_source_phrase_audit_detail(
                        effect,
                        archive,
                        applicability_mode=applicability_mode,
                        extraction_cache=extraction_cache,
                        enacted_extraction_cache=enacted_extraction_cache,
                        diagnostics_out=diagnostics_out,
                    )
                )
            witnesses.append(
                UKRepealSemanticsWitness(
                    family=f"affecting_act_{phrase_family}_candidate",
                    statute_id=statute_id,
                    effect_id=str(effect.effect_id or ""),
                    effect_type=effect.effect_type,
                    affected_provisions=effect.affected_provisions,
                    affecting_act_id=effect.affecting_act_id,
                    affecting_provisions=effect.affecting_provisions,
                    rule_id=f"uk_repeal_semantics_affecting_act_{phrase_family}_candidate",
                    source_status="affecting_act_source_phrase_candidate",
                    source_tag=first_phrase.source_tag,
                    source_text_preview=first_phrase.source_text_preview,
                    detail=detail,
                )
            )
    return tuple(witnesses)


def _selected_source_phrase_audit_detail(
    effect: UKEffectRecord,
    archive: Any,
    *,
    applicability_mode: str,
    extraction_cache: dict[str, UKAffectingSourceContext],
    enacted_extraction_cache: dict[str, UKAffectingSourceContext],
    diagnostics_out: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    selection = select_source_for_effect(
        effect=effect,
        archive=archive,
        applicability_mode=applicability_mode,
        extraction_cache=extraction_cache,
        enacted_extraction_cache=enacted_extraction_cache,
        effect_diagnostics_out=diagnostics_out,
    )
    tag_and_text = extracted_tag_and_text(selection.extracted_el)
    selected_family = source_text_repeal_semantics_family(tag_and_text.text)
    return {
        "selected_source_status": selection.source_context.source_status,
        "selected_source_tag": tag_and_text.tag or "",
        "selected_source_text_preview": _preview(tag_and_text.text),
        "selected_source_phrase_family": selected_family,
        "selected_source_matches_phrase": bool(selected_family),
    }


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


def _element_text(el: ET._Element) -> str:
    return _WHITESPACE_RE.sub(" ", " ".join(part for part in el.itertext() if part)).strip()
