"""Tiny Estonia act-identity registry helpers.

This is the first evidence-only slice of the future registry-backed identity
layer. It is intentionally small and lookup-oriented.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class EEActIdentityRecord:
    akt_viide: str
    grupi_id: str = ""
    canonical_title: str = ""
    title_variants: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    source_family: str = ""
    effective_from: str = ""
    effective_to: str = ""
    wrapper_parent_akt_viide: str = ""
    notes: tuple[str, ...] = ()


_EE_ACT_IDENTITY_REGISTRY: tuple[EEActIdentityRecord, ...] = (
    EEActIdentityRecord(
        akt_viide="ee/104072013003",
        canonical_title="Ehitusseaduse muutmise seadus",
        title_variants=("Ehitusseaduse muutmise seadus",),
        aliases=("Ehitusseadus",),
        source_family="single_target_preambul",
        notes=("seed registry entry for the first EE act-identity slice",),
    ),
    EEActIdentityRecord(
        akt_viide="ee/129102025003",
        grupi_id="1039676",
        canonical_title="Konsulaarametniku ametitoimingute ja diplomaatiliste passide andmekogu põhimäärus",
        title_variants=(
            "Konsulaarametniku ametitoimingute ja diplomaatiliste passide andmekogu põhimäärus",
        ),
        aliases=(
            "Konsulaarametniku ametitoimingute ja diplomaatiliste passide andmekogu pidamise kord",
        ),
        source_family="title_relabel_alias",
        effective_from="2025-11-01",
        notes=(
            "Source act 114012025005, effective 2025-12-09, still targets the pre-rename title "
            "while consolidated bases from 129102025003 onward expose the renamed põhimäärus title.",
        ),
    ),
)


def _normalize_identity_text(text: str) -> str:
    return " ".join((text or "").split()).strip().casefold()


def _normalize_identity_surface(text: str) -> str:
    """Normalize a statute title surface, including common clause suffixes."""
    normalized = _normalize_identity_text(text)
    if not normalized:
        return ""
    normalized = re.sub(r'\s+§.*$', '', normalized).strip()
    normalized = re.sub(r'seaduse\b', 'seadus', normalized)
    normalized = re.sub(r'seadust\b', 'seadus', normalized)
    normalized = re.sub(r'seadustiku\b', 'seadustik', normalized)
    normalized = re.sub(r'seadustikku\b', 'seadustik', normalized)
    normalized = re.sub(r'koodeksi\b', 'koodeks', normalized)
    normalized = re.sub(r'koodeksit\b', 'koodeks', normalized)
    normalized = re.sub(r'seaduste\b', 'seadus', normalized)
    normalized = re.sub(r'määruse\b', 'määrus', normalized)
    normalized = re.sub(r'määrust\b', 'määrus', normalized)
    return normalized


def _record_titles(record: EEActIdentityRecord) -> tuple[str, ...]:
    return (
        record.canonical_title,
        *record.title_variants,
        *record.aliases,
    )


def act_identity_matches_title(record: EEActIdentityRecord, title: str) -> bool:
    """Return True if the registry record supports the given title string."""
    normalized_title = _normalize_identity_surface(title)
    if not normalized_title:
        return False
    return any(
        _normalize_identity_surface(candidate) == normalized_title
        for candidate in _record_titles(record)
        if candidate
    )


def lookup_ee_act_identity(
    *,
    akt_viide: str = "",
    title: str = "",
    alias: str = "",
    registry: tuple[EEActIdentityRecord, ...] = _EE_ACT_IDENTITY_REGISTRY,
) -> EEActIdentityRecord | None:
    """Look up a registry record by exact aktViide, then title/alias evidence."""
    normalized_akt_viide = _normalize_identity_text(akt_viide)
    if normalized_akt_viide:
        for record in registry:
            if _normalize_identity_text(record.akt_viide) == normalized_akt_viide:
                return record

    normalized_candidates = [
        _normalize_identity_text(value)
        for value in (title, alias)
        if _normalize_identity_text(value)
    ]
    if not normalized_candidates:
        return None

    for record in registry:
        record_candidates = {
            _normalize_identity_text(candidate)
            for candidate in _record_titles(record)
            if candidate
        }
        if any(candidate in record_candidates for candidate in normalized_candidates):
            return record
    return None


__all__ = [
    "EEActIdentityRecord",
    "act_identity_matches_title",
    "lookup_ee_act_identity",
]
