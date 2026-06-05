#!/usr/bin/env python3
"""Review applied UK effect rows against archived current XML.

This is an evidence-only scanner.  It looks for applied effect-feed rows where
an affecting-source fragment is available but the archived current XML lacks
simple corroborating markers.  It does not authorize replay and does not claim
the official consolidation is wrong.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from lxml import etree as ET

from lawvm.core.agreement_residual import AgreementResidual
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.uk_legislation.effect_source_selection import (
    extracted_tag_and_text,
    select_source_for_effect,
)
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    load_effects_for_statute_from_archive,
)


_DEFAULT_ARCHIVE = Path("data/uk_legislation.farchive")
_LEG_BASE = "https://www.legislation.gov.uk"
_FORBIDDEN_SHORTCUTS = (
    "applied_effect_as_official_error",
    "source_fragment_as_payload_authority",
    "current_xml_absence_as_replay_authorization",
    "review_lead_as_automatic_consolidation_change",
)
_SIMPLE_EFFECT_WORDS = (
    "insert",
    "substitut",
    "omit",
    "repeal",
    "revoke",
)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_ADDRESS_WITH_PARENS_RE = re.compile(
    r"\b(?P<kind>s|section|art|article|reg|regulation|rule)\.?\s+"
    r"(?P<number>\d+[A-Za-z]{0,3})"
    r"(?P<suffix>(?:\s*\([^)()]{1,12}\)){1,6})",
    re.IGNORECASE,
)
_SCHEDULE_PARAGRAPH_WITH_PARENS_RE = re.compile(
    r"\b(?:sch|schedule)\.?\s+(?P<schedule>\d+[A-Za-z]{0,3})"
    r".{0,100}?\b(?:para|paragraph)\.?\s+"
    r"(?P<paragraph>\d+[A-Za-z]{0,3})"
    r"(?P<suffix>(?:\s*\([^)()]{1,12}\)){1,6})?",
    re.IGNORECASE,
)
_OMIT_ENTRY_UNDER_HEADING_RE = re.compile(
    r"\bunder\s+the\s+heading\s+[“\"][^”\"]{3,180}[”\"]"
    r".{0,120}?\bomit\s+the\s+entry\s+for\s+(?P<entry>[^.;:\n]{3,180})",
    re.IGNORECASE,
)
_IN_DEFINITION_OMIT_QUOTED_RE = re.compile(
    r"\bin\s+the\s+definition\s+of\s+[“\"][^”\"]{3,180}[”\"]"
    r".{0,180}?\bomit\s+[“\"](?P<phrase>[^”\"]{3,180})[”\"]",
    re.IGNORECASE,
)
_OMIT_DEFINITION_RE = re.compile(
    r"\bomit\s+the\s+definition\s+of\s+[“\"](?P<phrase>[^”\"]{3,180})[”\"]",
    re.IGNORECASE,
)
_UNREVIEWABLE_REMOVAL_LABEL_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"under\s+the\s+heading\s+[“\"][^”\"]{3,180}[”\"].{0,120}?\bomit\s+entry\s+\d+|"
    r"omit\s+paragraph\s+\([^)()]{1,12}\)|"
    r"omit\s+the\s+words\s+following\s+the\s+definition\s+of|"
    r"the\s+word\s+[“\"][^”\"]{1,12}[”\"].{0,120}?\bdefinition\s+of"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AppliedEffectCurrentReviewRow:
    statute_id: str
    effect_id: str
    review_status: str
    effect_type: str
    affected_provisions: str
    affecting_source_id: str
    affecting_provisions: str
    effective_date: str
    current_source_url: str
    current_source_status: str
    current_review_surface: str
    current_review_surface_locator: str
    source_fragment_locator: str
    source_fragment_sha256: str
    source_fragment_tag: str
    source_preview: str
    expected_phrase: str
    expected_phrase_role: str
    current_surface_preview: str
    current_expected_phrase_context: str
    current_xml_has_effect_id: bool
    current_xml_has_expected_phrase: bool
    current_xml_has_any_commentary_marker: bool
    current_xml_has_repeal_marker: bool
    public_current_urls: tuple[str, ...]
    public_source_urls: tuple[str, ...]
    simplest_public_check: tuple[str, ...]
    remaining_question: str
    agreement_residual: Mapping[str, Any]


def build_review_rows(
    statute_ids: Sequence[str],
    *,
    archive: Any,
    today: date | None = None,
    include_statuses: Sequence[str] = (),
    limit: int = 0,
) -> list[AppliedEffectCurrentReviewRow]:
    review_date = today or date.today()
    allowed_statuses = set(include_statuses)
    rows: list[AppliedEffectCurrentReviewRow] = []
    extraction_cache: dict[str, Any] = {}
    enacted_extraction_cache: dict[str, Any] = {}
    for statute_id in statute_ids:
        effects = load_effects_for_statute_from_archive(statute_id, archive)
        current_xml = _archive_get_optional(
            archive, f"{_LEG_BASE}/{statute_id}/data.xml"
        )
        current_status = _current_source_status(current_xml)
        current_context = _current_xml_context(
            current_xml=current_xml,
            current_status=current_status,
        )
        for effect in effects:
            if not _is_reviewable_effect(effect, today=review_date):
                continue
            current_surface = _current_review_surface(
                current_context=current_context,
                affected_provisions=effect.affected_provisions,
            )
            if not _effect_can_emit_requested_statuses(
                effect=effect,
                requested_statuses=allowed_statuses,
                current_status=current_status,
                current_surface=current_surface,
            ):
                continue
            row = _review_effect(
                statute_id=statute_id,
                effect=effect,
                archive=archive,
                current_surface=current_surface,
                current_status=current_status,
                extraction_cache=extraction_cache,
                enacted_extraction_cache=enacted_extraction_cache,
            )
            if row is None:
                continue
            if allowed_statuses and row.review_status not in allowed_statuses:
                continue
            rows.append(row)
            if limit > 0 and len(rows) >= limit:
                return rows
    return rows


def _review_effect(
    *,
    statute_id: str,
    effect: UKEffectRecord,
    archive: Any,
    current_surface: "_CurrentReviewSurface",
    current_status: str,
    extraction_cache: dict[str, Any],
    enacted_extraction_cache: dict[str, Any],
) -> AppliedEffectCurrentReviewRow | None:
    selection = select_source_for_effect(
        effect=effect,
        archive=archive,
        applicability_mode="effective_date_plus_feed_applied",
        extraction_cache=extraction_cache,
        enacted_extraction_cache=enacted_extraction_cache,
        effect_diagnostics_out=[],
    )
    tag_text = extracted_tag_and_text(selection.extracted_el)
    source_preview = _squash(tag_text.text)[:900]
    if not source_preview:
        return None
    expected_phrase, expected_phrase_role = _expected_phrase(
        source_preview,
        effect_type=effect.effect_type,
    )
    if not expected_phrase:
        return None
    current_surface_preview = _plain_text_preview(current_surface.xml_or_text)
    current_expected_phrase_context = _phrase_context(
        current_surface.xml_or_text,
        expected_phrase,
    )
    current_has_effect_id = bool(
        effect.effect_id and effect.effect_id in current_surface.xml_or_text
    )
    current_has_expected_phrase = _contains_phrase(
        current_surface.xml_or_text,
        expected_phrase,
    )
    current_has_any_commentary_marker = current_has_effect_id
    current_has_repeal_marker = bool(
        re.search(r"<Repeal\b", current_surface.xml_or_text)
    )
    review_status = _review_status(
        current_status=current_status,
        current_has_effect_id=current_has_effect_id,
        current_has_expected_phrase=current_has_expected_phrase,
        current_has_any_commentary_marker=current_has_any_commentary_marker,
        current_has_repeal_marker=current_has_repeal_marker,
        effect_type=effect.effect_type,
        expected_phrase_role=expected_phrase_role,
    )
    public_current_urls = _public_current_urls(statute_id, effect.affected_provisions)
    public_source_urls = _public_source_urls(effect.affecting_act_id, effect.affecting_provisions)
    residual = _agreement_residual(
        statute_id=statute_id,
        effect=effect,
        review_status=review_status,
        current_status=current_status,
        current_review_surface=current_surface,
        current_has_effect_id=current_has_effect_id,
        current_has_expected_phrase=current_has_expected_phrase,
        current_has_any_commentary_marker=current_has_any_commentary_marker,
        current_has_repeal_marker=current_has_repeal_marker,
    )
    source_bytes = selection.source_context.xml_bytes or b""
    return AppliedEffectCurrentReviewRow(
        statute_id=statute_id,
        effect_id=effect.effect_id,
        review_status=review_status,
        effect_type=effect.effect_type,
        affected_provisions=effect.affected_provisions,
        affecting_source_id=effect.affecting_act_id,
        affecting_provisions=effect.affecting_provisions,
        effective_date=effect.effective_date,
        current_source_url=f"{_LEG_BASE}/{statute_id}/data.xml",
        current_source_status=current_status,
        current_review_surface=current_surface.surface,
        current_review_surface_locator=current_surface.locator,
        source_fragment_locator=selection.source_context.locator,
        source_fragment_sha256=hashlib.sha256(source_bytes).hexdigest()
        if source_bytes
        else "",
        source_fragment_tag=tag_text.tag or "",
        source_preview=source_preview,
        expected_phrase=expected_phrase,
        expected_phrase_role=expected_phrase_role,
        current_surface_preview=current_surface_preview,
        current_expected_phrase_context=current_expected_phrase_context,
        current_xml_has_effect_id=current_has_effect_id,
        current_xml_has_expected_phrase=current_has_expected_phrase,
        current_xml_has_any_commentary_marker=current_has_any_commentary_marker,
        current_xml_has_repeal_marker=current_has_repeal_marker,
        public_current_urls=public_current_urls,
        public_source_urls=public_source_urls,
        simplest_public_check=_public_check_steps(
            public_current_urls=public_current_urls,
            public_source_urls=public_source_urls,
            expected_phrase=expected_phrase,
        ),
        remaining_question=_remaining_question(review_status),
        agreement_residual=residual.to_dict(),
    )


def _is_reviewable_effect(effect: UKEffectRecord, *, today: date) -> bool:
    if not effect.applied:
        return False
    if effect.is_prospective_only:
        return False
    if effect.effective_date and not _is_current_or_past_iso_date(
        effect.effective_date,
        today=today,
    ):
        return False
    lower_type = effect.effect_type.lower()
    return any(word in lower_type for word in _SIMPLE_EFFECT_WORDS)


def _effect_can_emit_requested_statuses(
    *,
    effect: UKEffectRecord,
    requested_statuses: set[str],
    current_status: str,
    current_surface: "_CurrentReviewSurface",
) -> bool:
    if not requested_statuses:
        return True
    if (
        current_status != "available"
        and "current_xml_unavailable_frontier" not in requested_statuses
    ):
        return False
    lower_type = effect.effect_type.lower()
    removal_effect = _is_removal_effect_type(lower_type)
    if requested_statuses == {"needs_public_review_removed_phrase_still_present"}:
        if not removal_effect:
            return False
        if effect.effect_id and effect.effect_id in current_surface.xml_or_text:
            return False
        return re.search(r"<Repeal\b", current_surface.xml_or_text) is None
    if requested_statuses == {"needs_public_review_no_obvious_current_marker"}:
        if removal_effect:
            return False
        if effect.effect_id and effect.effect_id in current_surface.xml_or_text:
            return False
    return True


def _is_removal_effect_type(lower_effect_type: str) -> bool:
    return (
        "omit" in lower_effect_type
        or "repeal" in lower_effect_type
        or "revoke" in lower_effect_type
    )


def _is_current_or_past_iso_date(value: str, *, today: date) -> bool:
    if _ISO_DATE_RE.fullmatch(value) is None:
        return False
    return date.fromisoformat(value) <= today


@dataclass(frozen=True)
class _CurrentReviewSurface:
    surface: str
    locator: str
    xml_or_text: str


@dataclass(frozen=True)
class _CurrentXmlContext:
    status: str
    whole_xml: str
    root: ET._Element | None


def _current_xml_context(
    *,
    current_xml: bytes | None,
    current_status: str,
) -> _CurrentXmlContext:
    if current_status != "available" or current_xml is None:
        return _CurrentXmlContext(status=current_status, whole_xml="", root=None)
    return _CurrentXmlContext(
        status=current_status,
        whole_xml=current_xml.decode("utf-8", errors="replace"),
        root=ET.fromstring(current_xml),
    )


def _current_review_surface(
    *,
    current_context: _CurrentXmlContext,
    affected_provisions: str,
) -> _CurrentReviewSurface:
    if current_context.status != "available" or current_context.root is None:
        return _CurrentReviewSurface(
            surface="current_xml_unavailable",
            locator="",
            xml_or_text="",
        )
    matched: list[tuple[str, ET._Element]] = []
    for target_eid in _affected_provision_eid_candidates(affected_provisions):
        target = current_context.root.find(f".//*[@eId='{target_eid}']")
        if target is None:
            target = current_context.root.find(f".//*[@id='{target_eid}']")
        if target is None:
            continue
        matched.append((target_eid, target))
    if matched:
        max_depth = max(_eid_depth(target_eid) for target_eid, _target in matched)
        best = tuple(
            (target_eid, target)
            for target_eid, target in matched
            if _eid_depth(target_eid) == max_depth
        )
        return _CurrentReviewSurface(
            surface="affected_provision",
            locator=" ".join(target_eid for target_eid, _target in best),
            xml_or_text=" ".join(
                ET.tostring(target, encoding="unicode") for _target_eid, target in best
            ),
        )
    return _CurrentReviewSurface(
        surface="whole_current_xml",
        locator="",
        xml_or_text=current_context.whole_xml,
    )


def _eid_depth(eid: str) -> int:
    return len([part for part in eid.split("-") if part])


def _affected_provision_eid_candidates(affected_provisions: str) -> tuple[str, ...]:
    candidates: list[str] = []
    candidates.extend(_deep_affected_provision_eid_candidates(affected_provisions))
    for kind, prefix in (
        ("s", "section"),
        ("section", "section"),
        ("art", "article"),
        ("article", "article"),
        ("reg", "regulation"),
        ("regulation", "regulation"),
        ("rule", "rule"),
        ("sch", "schedule"),
        ("schedule", "schedule"),
        ("annex", "annex"),
    ):
        for number in _numbers_after_token(affected_provisions, kind):
            candidates.append(f"{prefix}-{number}")
    return _unique(candidates)


def _deep_affected_provision_eid_candidates(affected_provisions: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for match in _ADDRESS_WITH_PARENS_RE.finditer(affected_provisions):
        prefix = _address_kind_eid_prefix(match.group("kind"))
        if not prefix:
            continue
        base = f"{prefix}-{match.group('number')}"
        for suffix in _parenthetical_eid_suffixes(match.group("suffix")):
            candidates.append(f"{base}-{suffix}")
    for match in _SCHEDULE_PARAGRAPH_WITH_PARENS_RE.finditer(affected_provisions):
        base = f"schedule-{match.group('schedule')}-paragraph-{match.group('paragraph')}"
        suffix_text = match.group("suffix") or ""
        if suffix_text:
            for suffix in _parenthetical_eid_suffixes(suffix_text):
                candidates.append(f"{base}-{suffix}")
        candidates.append(base)
    return _unique(candidates)


def _address_kind_eid_prefix(kind: str) -> str:
    normalized = kind.lower()
    if normalized in {"s", "section"}:
        return "section"
    if normalized in {"art", "article"}:
        return "article"
    if normalized in {"reg", "regulation"}:
        return "regulation"
    if normalized == "rule":
        return "rule"
    return ""


def _parenthetical_eid_suffixes(value: str) -> tuple[str, ...]:
    labels = tuple(
        _clean_eid_label(match.group(1))
        for match in re.finditer(r"\(([^)()]{1,12})\)", value)
    )
    labels = tuple(label for label in labels if label)
    if not labels:
        return ()
    suffixes: list[str] = []
    suffixes.append("-".join(labels))
    for end in range(len(labels) - 1, 0, -1):
        suffixes.append("-".join(labels[:end]))
    if len(labels) >= 3:
        suffixes.extend(f"{labels[0]}-{label}" for label in labels[1:])
    suffixes.extend(labels)
    return _unique(suffixes)


def _clean_eid_label(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "", value)
    return cleaned[:12]


def _review_status(
    *,
    current_status: str,
    current_has_effect_id: bool,
    current_has_expected_phrase: bool,
    current_has_any_commentary_marker: bool,
    current_has_repeal_marker: bool,
    effect_type: str,
    expected_phrase_role: str,
) -> str:
    if current_status != "available":
        return "current_xml_unavailable_frontier"
    lower_type = effect_type.lower()
    if expected_phrase_role == "removed_preimage":
        if current_has_expected_phrase and not (
            current_has_effect_id or current_has_repeal_marker
        ):
            return "needs_public_review_removed_phrase_still_present"
        return "current_xml_lacks_removed_phrase"
    if current_has_expected_phrase or current_has_effect_id:
        return "current_xml_has_expected_marker"
    if ("repeal" in lower_type or "omit" in lower_type or "revoke" in lower_type) and (
        current_has_repeal_marker or current_has_any_commentary_marker
    ):
        return "current_xml_has_editorial_marker"
    if current_has_any_commentary_marker:
        return "current_xml_has_other_commentary_marker"
    return "needs_public_review_no_obvious_current_marker"


def _agreement_residual(
    *,
    statute_id: str,
    effect: UKEffectRecord,
    review_status: str,
    current_status: str,
    current_review_surface: "_CurrentReviewSurface",
    current_has_effect_id: bool,
    current_has_expected_phrase: bool,
    current_has_any_commentary_marker: bool,
    current_has_repeal_marker: bool,
) -> AgreementResidual:
    return AgreementResidual(
        residual_id=f"uk-applied-effect-current:{statute_id}:{effect.effect_id}",
        jurisdiction="uk",
        agreement_surface="applied_effect_source_fragment_vs_archived_current_xml",
        family=_residual_family(review_status),
        status=_residual_status(review_status),
        owner_phase="compare_oracle_classification",
        rule_id=f"uk_applied_effect_current_{review_status}",
        source_artifact_id=statute_id,
        replay_count=1,
        oracle_count=1 if current_status == "available" else 0,
        missing_proofs=_missing_proofs(review_status),
        safe_default="review_without_replay_or_official_error_promotion",
        forbidden_shortcuts=_FORBIDDEN_SHORTCUTS,
        detail={
            "effect_id": effect.effect_id,
            "effect_type": effect.effect_type,
            "affected_provisions": effect.affected_provisions,
            "affecting_act_id": effect.affecting_act_id,
            "affecting_provisions": effect.affecting_provisions,
            "current_source_status": current_status,
            "current_review_surface": current_review_surface.surface,
            "current_review_surface_locator": current_review_surface.locator,
            "current_xml_has_effect_id": current_has_effect_id,
            "current_xml_has_expected_phrase": current_has_expected_phrase,
            "current_xml_has_any_commentary_marker": current_has_any_commentary_marker,
            "current_xml_has_repeal_marker": current_has_repeal_marker,
        },
    )


def _residual_family(review_status: str) -> str:
    if review_status in {
        "needs_public_review_no_obvious_current_marker",
        "needs_public_review_removed_phrase_still_present",
    }:
        return "oracle_editorial_pathology"
    if review_status == "current_xml_unavailable_frontier":
        return "source_footing_gap"
    return "non_commensurable_surface"


def _residual_status(review_status: str) -> str:
    if review_status in {
        "needs_public_review_no_obvious_current_marker",
        "needs_public_review_removed_phrase_still_present",
    }:
        return "residual"
    return "frontier"


def _missing_proofs(review_status: str) -> tuple[str, ...]:
    if review_status in {
        "needs_public_review_no_obvious_current_marker",
        "needs_public_review_removed_phrase_still_present",
    }:
        return (
            "public_page_review",
            "page_declared_current_timeline_xml",
            "savings_extent_or_editorial_policy_review",
        )
    if review_status == "current_xml_unavailable_frontier":
        return ("current_xml_source_witness",)
    return ()


def _remaining_question(review_status: str) -> str:
    if review_status in {
        "needs_public_review_no_obvious_current_marker",
        "needs_public_review_removed_phrase_still_present",
    }:
        return (
            "Check the public current page and page-declared dated XML for a "
            "missed effect, savings/extent limitation, or editorial display policy."
        )
    if review_status == "current_xml_unavailable_frontier":
        return "Acquire or verify the current XML before reviewing this effect."
    return "No official-error review claim; the current XML has an obvious marker."


def _expected_phrase(source_preview: str, *, effect_type: str) -> tuple[str, str]:
    lower_type = effect_type.lower()
    quoted = _quoted_phrases(source_preview)
    if "omit" in lower_type or "repeal" in lower_type or "revoke" in lower_type:
        return _removed_expected_phrase(source_preview, quoted)
    if "substitut" in lower_type and len(quoted) >= 2:
        cleaned = _squash(quoted[-1])
        if 8 <= len(cleaned) <= 180 and _has_letters(cleaned):
            return cleaned, "postimage"
    if "substitut" in lower_type:
        after_substitute_dash = re.search(
            r"substitute(?:\s+\w+)?\s*[—-]\s*(.{12,180})",
            source_preview,
            re.IGNORECASE,
        )
        if after_substitute_dash:
            return (
                _squash(after_substitute_dash.group(1).strip(" .;:"))[:140],
                "postimage",
            )
        return "", ""
    for phrase in quoted:
        cleaned = _squash(phrase)
        if 12 <= len(cleaned) <= 140 and _has_letters(cleaned):
            return cleaned, "postimage"
    after_dash = re.search(
        r"(?:insert|substitute|substituted)\s*[—-]\s*(.{12,180})",
        source_preview,
        re.IGNORECASE,
    )
    if after_dash:
        return _squash(after_dash.group(1).strip(" .;:"))[:140], "postimage"
    return "", ""


def _removed_expected_phrase(
    source_preview: str,
    quoted: tuple[str, ...],
) -> tuple[str, str]:
    entry_match = _OMIT_ENTRY_UNDER_HEADING_RE.search(source_preview)
    if entry_match:
        return _clean_removed_phrase(entry_match.group("entry"))
    definition_omit_match = _IN_DEFINITION_OMIT_QUOTED_RE.search(source_preview)
    if definition_omit_match:
        return _clean_removed_phrase(definition_omit_match.group("phrase"))
    omitted_definition_match = _OMIT_DEFINITION_RE.search(source_preview)
    if omitted_definition_match:
        return _clean_removed_phrase(omitted_definition_match.group("phrase"))
    if _UNREVIEWABLE_REMOVAL_LABEL_CONTEXT_RE.search(source_preview):
        return "", ""
    for phrase in quoted:
        cleaned = _squash(phrase)
        if 8 <= len(cleaned) <= 180 and _has_letters(cleaned):
            return cleaned, "removed_preimage"
    return "", ""


def _clean_removed_phrase(value: str) -> tuple[str, str]:
    cleaned = _squash(value.strip(" .;:,"))
    if 8 <= len(cleaned) <= 180 and _has_letters(cleaned):
        return cleaned, "removed_preimage"
    return "", ""


def _quoted_phrases(text: str) -> tuple[str, ...]:
    return tuple(
        match.group(1)
        for match in re.finditer(r"[“\"]([^”\"]{3,180})[”\"]", text)
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    if not text or not phrase:
        return False
    return _normalize_for_search(phrase) in _normalize_for_search(text)


def _plain_text_preview(xml_or_text: str) -> str:
    return _squash(_strip_xml_markup(xml_or_text))[:900]


def _phrase_context(xml_or_text: str, phrase: str) -> str:
    if not xml_or_text or not phrase:
        return ""
    text = _squash(_strip_xml_markup(xml_or_text))
    normalized_text = text.casefold()
    normalized_phrase = _squash(phrase).casefold()
    index = normalized_text.find(normalized_phrase)
    if index < 0:
        index = _first_keyword_index(normalized_text, normalized_phrase)
    if index < 0:
        return text[:500]
    start = max(0, index - 240)
    end = min(len(text), index + len(phrase) + 360)
    return text[start:end]


def _first_keyword_index(normalized_text: str, normalized_phrase: str) -> int:
    for word in re.findall(r"[a-z0-9]{4,}", normalized_phrase):
        index = normalized_text.find(word)
        if index >= 0:
            return index
    return -1


def _normalize_for_search(value: str) -> str:
    return _squash(_strip_xml_markup(value)).casefold()


def _strip_xml_markup(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return value.replace("&quot;", '"').replace("&amp;", "&")


def _public_check_steps(
    *,
    public_current_urls: Sequence[str],
    public_source_urls: Sequence[str],
    expected_phrase: str,
) -> tuple[str, ...]:
    steps: list[str] = []
    if public_current_urls:
        steps.append(f"Open current provision page: {public_current_urls[0]}")
    if public_source_urls:
        steps.append(f"Open affecting source: {public_source_urls[0]}")
    if expected_phrase:
        steps.append(f"Search current page/XML for expected phrase: {expected_phrase}")
    return tuple(steps)


def _public_current_urls(statute_id: str, affected_provisions: str) -> tuple[str, ...]:
    urls: list[str] = []
    for kind, path in (
        ("s", "section"),
        ("section", "section"),
        ("art", "article"),
        ("article", "article"),
        ("sch", "schedule"),
        ("schedule", "schedule"),
    ):
        for number in _numbers_after_token(affected_provisions, kind):
            urls.append(f"{_LEG_BASE}/{statute_id}/{path}/{number}")
    if not urls:
        urls.append(f"{_LEG_BASE}/{statute_id}")
    return _unique(urls)


def _public_source_urls(source_id: str, affecting_provisions: str) -> tuple[str, ...]:
    if not source_id:
        return ()
    urls = [f"{_LEG_BASE}/{source_id}"]
    for kind, path in (
        ("s", "section"),
        ("ss", "section"),
        ("art", "article"),
        ("arts", "article"),
        ("reg", "regulation"),
        ("regs", "regulation"),
        ("sch", "schedule"),
        ("schs", "schedule"),
    ):
        for number in _numbers_after_token(affecting_provisions, kind):
            urls.append(f"{_LEG_BASE}/{source_id}/{path}/{number}")
    return _unique(urls)


def _numbers_after_token(text: str, token: str) -> tuple[str, ...]:
    values: list[str] = []
    pattern = rf"\b{re.escape(token)}\.?\s+([0-9][0-9A-Za-z(),.\-/\u2013\s]{{0,80}})"
    for match in re.finditer(pattern, text, re.IGNORECASE):
        for chunk in re.split(r"\s*,\s*", match.group(1)):
            for number in _labels_from_number_chunk(chunk):
                if number not in values:
                    values.append(number)
    return tuple(values)


def _labels_from_number_chunk(chunk: str) -> tuple[str, ...]:
    chunk = chunk.strip()
    range_match = re.match(
        r"(\d+)([A-Za-z])\s*(?:-|--|\u2013|to)\s*(?:(\d+))?([A-Za-z])\b",
        chunk,
        re.IGNORECASE,
    )
    if range_match is not None:
        start_num, start_letter, end_num, end_letter = range_match.groups()
        if end_num not in (None, start_num):
            return ()
        start_ord = ord(start_letter.upper())
        end_ord = ord(end_letter.upper())
        if start_ord <= end_ord and end_ord - start_ord <= 12:
            return tuple(f"{start_num}{chr(letter)}" for letter in range(start_ord, end_ord + 1))
    number_match = re.match(r"\s*(\d+[A-Za-z]{0,3})", chunk)
    if number_match is None:
        return ()
    number = number_match.group(1)
    return (number,)


def _archive_get_optional(archive: Any, locator: str) -> bytes | None:
    data = archive.get(locator)
    return data if isinstance(data, bytes) and data else None


def _current_source_status(data: bytes | None) -> str:
    if data is None:
        return "missing"
    if len(data) < 200:
        return "too_small"
    return "available"


def _has_letters(value: str) -> bool:
    return any(ch.isalpha() for ch in value)


def _squash(value: str) -> str:
    return " ".join(value.split())


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _emit_json(rows: Sequence[AppliedEffectCurrentReviewRow]) -> str:
    status_counts: dict[str, int] = {}
    residual_family_counts: dict[str, int] = {}
    residual_status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row.review_status] = status_counts.get(row.review_status, 0) + 1
        residual = row.agreement_residual
        family = str(residual.get("family") or "")
        status = str(residual.get("status") or "")
        residual_family_counts[family] = residual_family_counts.get(family, 0) + 1
        residual_status_counts[status] = residual_status_counts.get(status, 0) + 1
    summary = {
        "row_count": len(rows),
        "review_status_counts": status_counts,
        "candidate_public_review_count": status_counts.get(
            "needs_public_review_no_obvious_current_marker", 0
        )
        + status_counts.get("needs_public_review_removed_phrase_still_present", 0),
        "agreement_residual_family_counts": residual_family_counts,
        "agreement_residual_status_counts": residual_status_counts,
        "forbidden_shortcuts": list(_FORBIDDEN_SHORTCUTS),
    }
    report = EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_applied_effect_current_review",
        schema="lawvm.uk_applied_effect_current_review.v1",
        truth_claim="applied_effect_current_review_not_replay_authority",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary,
        filtered_summary=summary,
        rows=tuple(asdict(row) for row in rows),
        detail={
            "source_truth_claims": False,
            "official_error_claims": False,
            "forbidden_shortcuts": list(_FORBIDDEN_SHORTCUTS),
        },
    )
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def _filter_rows(
    rows: Sequence[AppliedEffectCurrentReviewRow],
    *,
    statuses: Sequence[str],
    limit: int,
) -> tuple[AppliedEffectCurrentReviewRow, ...]:
    allowed = set(statuses)
    filtered = [
        row for row in rows if not allowed or row.review_status in allowed
    ]
    if limit > 0:
        filtered = filtered[:limit]
    return tuple(filtered)


def _load_statute_ids(path: Path | None, inline: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    if path is not None:
        values.extend(line.strip() for line in path.read_text().splitlines())
    values.extend(inline)
    return _unique(value for value in values if value and not value.startswith("#"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review applied UK effect rows against archived current XML."
    )
    parser.add_argument("--db", type=Path, default=_DEFAULT_ARCHIVE)
    parser.add_argument("--ids-file", type=Path)
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--status",
        action="append",
        default=[],
        help="Emit only rows with this review_status; repeatable.",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    statute_ids = _load_statute_ids(args.ids_file, args.id)
    if not statute_ids:
        raise SystemExit("provide --id or --ids-file")

    from farchive import Farchive

    archive = Farchive(args.db)
    try:
        rows = build_review_rows(
            statute_ids,
            archive=archive,
            include_statuses=args.status,
            limit=args.limit,
        )
    finally:
        archive.close()
    filtered_rows = _filter_rows(rows, statuses=(), limit=0)
    payload = _emit_json(filtered_rows)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
