"""Diagnostic audit of UK statutory-instrument commencement-metadata state.

Many UK statutory instruments carry incomplete or unresolved commencement
metadata: no made-date, several commencement dates, textual-only commencement,
or a single made-date that is only a *candidate* default and not a proven
commencement. This module classifies each affecting SI's commencement-metadata
state into a small typed taxonomy with reason tags.

It is a **sensor, not a gate**. It reads the same source facts the replay
temporal resolver reads — it reuses the replay-path ``UKCommencementMetadata``
carrier and ``_instrument_commencement_metadata`` extractor from
``effect_temporal`` verbatim — and it never changes commencement resolution,
in-force filtering, or any point-in-time selection. The UK Replay Living Spec
warns that a blanket "do not apply prospective" gate is wrong; this surface only
classifies, it does not block.

The classifier is total: every ``UKCommencementMetadata`` maps to exactly one
``UKSICommencementAuditState``.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, assert_never

from lxml import etree as ET

from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.uk_legislation.affecting_act_commencement import (
    affecting_provision_in_force,
    get_affecting_act_xml,
)
from lawvm.uk_legislation.effect_temporal import (
    UKCommencementMetadata,
    UKCommencementMetadataStatus,
    _instrument_commencement_metadata,
)
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    load_effects_for_statute_from_archive,
)
from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND

_SI_AFFECTING_CLASS = "UnitedKingdomStatutoryInstrument"

_UKM_NS = "http://www.legislation.gov.uk/namespaces/metadata"

# ── Audit-state taxonomy ─────────────────────────────────────────────────────
# A single proven official commencement date in metadata. Replay's temporal
# resolver uses exactly this date as a source fact.
UK_SI_COMMENCEMENT_RESOLVED_IN_FORCE = "resolved_in_force"
# More than one commencement date in metadata; replay has no single source date.
UK_SI_COMMENCEMENT_MULTIPLE_DATES = "multiple_commencement_dates"
# A ComingIntoForce element exists but exposes no parseable date — the
# commencement is stated only in prose.
UK_SI_COMMENCEMENT_TEXTUAL_ONLY = "textual_only_commencement"
# No commencement date and no made-date: nothing to anchor commencement on.
UK_SI_COMMENCEMENT_NO_MADE_DATE = "no_made_date"
# Exactly one made-date and no commencement date: the SI-Practice default
# (commences on the day it is made) is a *candidate*, not proven.
UK_SI_COMMENCEMENT_MADE_DATE_DEFAULT_CANDIDATE = "made_date_default_candidate_but_unproved"
# A prospective effect whose affecting provision could not be resolved to an
# in-force determination from the affecting act's RestrictStartDate metadata.
UK_SI_COMMENCEMENT_PROSPECTIVE_UNRESOLVED = "prospective_unresolved"
# The affecting SI source XML was not available in the archive.
UK_SI_COMMENCEMENT_SOURCE_UNAVAILABLE = "source_unavailable"
# The affecting SI source XML failed to parse.
UK_SI_COMMENCEMENT_SOURCE_PARSE_ERROR = "source_parse_error"

UK_SI_COMMENCEMENT_AUDIT_STATES: frozenset[str] = frozenset(
    {
        UK_SI_COMMENCEMENT_RESOLVED_IN_FORCE,
        UK_SI_COMMENCEMENT_MULTIPLE_DATES,
        UK_SI_COMMENCEMENT_TEXTUAL_ONLY,
        UK_SI_COMMENCEMENT_NO_MADE_DATE,
        UK_SI_COMMENCEMENT_MADE_DATE_DEFAULT_CANDIDATE,
        UK_SI_COMMENCEMENT_PROSPECTIVE_UNRESOLVED,
        UK_SI_COMMENCEMENT_SOURCE_UNAVAILABLE,
        UK_SI_COMMENCEMENT_SOURCE_PARSE_ERROR,
    }
)

# Reason tags. These annotate *why* a state was reached; they are not states.
REASON_SINGLE_COMMENCEMENT_DATE = "single_commencement_date"
REASON_MULTIPLE_COMMENCEMENT_DATES = "multiple_commencement_dates"
REASON_COMING_INTO_FORCE_ELEMENT_PRESENT = "coming_into_force_element_present"
REASON_NO_COMMENCEMENT_DATE = "no_commencement_date"
REASON_NO_MADE_DATE = "no_made_date"
REASON_SINGLE_MADE_DATE = "single_made_date"
REASON_MULTIPLE_MADE_DATES = "multiple_made_dates"
REASON_MADE_DATE_DEFAULT_UNPROVED = "made_date_default_candidate_but_unproved"
REASON_PROSPECTIVE_AFFECTING_PROVISION_UNRESOLVED = (
    "prospective_affecting_provision_unresolved"
)
REASON_SOURCE_XML_UNAVAILABLE = "source_xml_unavailable"
REASON_SOURCE_XML_PARSE_ERROR = "source_xml_parse_error"


@dataclass(frozen=True)
class UKSICommencementAuditState:
    """Typed commencement-metadata audit state for one affecting SI."""

    affecting_act_id: str
    state: str
    reason_tags: tuple[str, ...]
    commencement_dates: tuple[str, ...] = ()
    made_dates: tuple[str, ...] = ()
    coming_into_force_element_present: bool = False
    metadata_status: UKCommencementMetadataStatus | str = ""
    source_locator: str = ""
    parse_error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "affecting_act_id": self.affecting_act_id,
            "state": self.state,
            "reason_tags": self.reason_tags,
            "commencement_dates": self.commencement_dates,
            "made_dates": self.made_dates,
            "coming_into_force_element_present": self.coming_into_force_element_present,
            "metadata_status": str(self.metadata_status),
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }
        if self.source_locator:
            row["source_locator"] = self.source_locator
        if self.parse_error:
            row["parse_error"] = self.parse_error
        for key in sorted(self.detail):
            row.setdefault(key, self.detail[key])
        return row


def _has_coming_into_force_element(xml_bytes: bytes) -> bool:
    """Return True if the affecting SI carries a ComingIntoForce metadata element.

    Read-only; mirrors the ``ComingIntoForce`` scan in ``si_source_semantics``
    and ``effect_temporal`` without changing either.
    """
    if not xml_bytes:
        return False
    try:
        root = parse_corpus_xml(xml_bytes)
    except ET.ParseError:
        return False
    for _elem in root.findall(f".//{{{_UKM_NS}}}ComingIntoForce"):
        return True
    return False


def classify_si_commencement_metadata(
    affecting_act_id: str,
    metadata: UKCommencementMetadata,
    *,
    coming_into_force_element_present: bool = False,
    prospective_unresolved: bool = False,
) -> UKSICommencementAuditState:
    """Classify one affecting SI's commencement-metadata state.

    ``metadata`` is the verbatim replay-path carrier from ``effect_temporal``.
    ``prospective_unresolved`` is set by the caller when this SI carries a
    prospective effect whose affecting provision could not be resolved to an
    in-force determination (the ``None`` tri-state from
    ``affecting_act_commencement.affecting_provision_in_force``). When that holds
    AND the SI exposes no single official commencement date, the state is
    ``prospective_unresolved`` — the metadata cannot prove the effect is in
    force. A single proven commencement date always wins over this signal.

    The mapping is total over ``UKCommencementMetadata.commencement_status``.
    """
    status = metadata.commencement_status
    dates = tuple(metadata.dates)
    made_dates = tuple(metadata.made_dates)

    def _state(state: str, reason_tags: tuple[str, ...], *, parse_error: str = "") -> UKSICommencementAuditState:
        return UKSICommencementAuditState(
            affecting_act_id=affecting_act_id,
            state=state,
            reason_tags=reason_tags,
            commencement_dates=dates,
            made_dates=made_dates,
            coming_into_force_element_present=coming_into_force_element_present,
            metadata_status=status,
            source_locator=metadata.source_locator,
            parse_error=parse_error,
        )

    # Statuses whose audit state is fully determined by the metadata extraction,
    # independent of the prospective/coming-into-force guards below. The producer
    # only emits ``MULTIPLE_OR_TEXTUAL`` with at least one commencement date, so
    # the historical ``and dates`` guard is preserved defensively.
    match status:
        case UKCommencementMetadataStatus.SOURCE_XML_UNAVAILABLE:
            return _state(
                UK_SI_COMMENCEMENT_SOURCE_UNAVAILABLE,
                (REASON_SOURCE_XML_UNAVAILABLE,),
            )
        case UKCommencementMetadataStatus.SOURCE_XML_PARSE_ERROR:
            return _state(
                UK_SI_COMMENCEMENT_SOURCE_PARSE_ERROR,
                (REASON_SOURCE_XML_PARSE_ERROR,),
                parse_error=metadata.parse_error,
            )
        case UKCommencementMetadataStatus.SINGLE_DATE:
            # A proven single official commencement date always wins, even if the
            # effect feed flagged the effect prospective: the metadata resolves it.
            return _state(
                UK_SI_COMMENCEMENT_RESOLVED_IN_FORCE,
                (REASON_SINGLE_COMMENCEMENT_DATE,),
            )
        case UKCommencementMetadataStatus.MULTIPLE_OR_TEXTUAL if dates:
            return _state(
                UK_SI_COMMENCEMENT_MULTIPLE_DATES,
                (REASON_MULTIPLE_COMMENCEMENT_DATES,),
            )
        case (
            UKCommencementMetadataStatus.MULTIPLE_OR_TEXTUAL
            | UKCommencementMetadataStatus.TEXTUAL_OR_MISSING_DATE
            | UKCommencementMetadataStatus.DEFAULT_COMMENCEMENT_MADE_DATE_CANDIDATE
        ):
            # No parseable commencement date in metadata — fall through to the
            # prospective/made-date/textual classification below.
            pass
        case _:
            assert_never(status)

    # From here: no parseable commencement date in metadata, so a prospective
    # effect whose affecting provision is itself unresolved cannot be proven in
    # force from this SI's metadata. Surface that as a distinct state.
    made_reason = (
        REASON_NO_MADE_DATE
        if not made_dates
        else REASON_SINGLE_MADE_DATE
        if len(made_dates) == 1
        else REASON_MULTIPLE_MADE_DATES
    )

    if prospective_unresolved:
        return _state(
            UK_SI_COMMENCEMENT_PROSPECTIVE_UNRESOLVED,
            (
                REASON_PROSPECTIVE_AFFECTING_PROVISION_UNRESOLVED,
                REASON_NO_COMMENCEMENT_DATE,
                made_reason,
            ),
        )

    if status is UKCommencementMetadataStatus.DEFAULT_COMMENCEMENT_MADE_DATE_CANDIDATE:
        return _state(
            UK_SI_COMMENCEMENT_MADE_DATE_DEFAULT_CANDIDATE,
            (
                REASON_NO_COMMENCEMENT_DATE,
                REASON_SINGLE_MADE_DATE,
                REASON_MADE_DATE_DEFAULT_UNPROVED,
            ),
        )

    # status == "textual_or_missing_date" (or the never-emitted dateless
    # multiple_or_textual): no dates, and made_dates != 1.
    if coming_into_force_element_present:
        return _state(
            UK_SI_COMMENCEMENT_TEXTUAL_ONLY,
            (
                REASON_COMING_INTO_FORCE_ELEMENT_PRESENT,
                REASON_NO_COMMENCEMENT_DATE,
                made_reason,
            ),
        )
    return _state(
        UK_SI_COMMENCEMENT_NO_MADE_DATE,
        (REASON_NO_COMMENCEMENT_DATE, made_reason),
    )


def audit_affecting_si_commencement(
    affecting_act_id: str,
    xml_bytes: bytes,
    *,
    source_locator: str = "",
    prospective_unresolved: bool = False,
) -> UKSICommencementAuditState:
    """Classify one affecting SI from its raw source XML bytes.

    Reuses the replay-path ``_instrument_commencement_metadata`` extractor
    verbatim, then classifies the resulting carrier. Read-only.
    """
    metadata = _instrument_commencement_metadata(
        xml_bytes,
        source_locator=source_locator,
    )
    if metadata.commencement_status is UKCommencementMetadataStatus.SOURCE_XML_UNAVAILABLE:
        return classify_si_commencement_metadata(
            affecting_act_id,
            metadata,
            coming_into_force_element_present=False,
            prospective_unresolved=prospective_unresolved,
        )
    return classify_si_commencement_metadata(
        affecting_act_id,
        metadata,
        coming_into_force_element_present=_has_coming_into_force_element(xml_bytes),
        prospective_unresolved=prospective_unresolved,
    )


@dataclass(frozen=True)
class UKSICommencementAuditForStatute:
    """Per-affecting-SI audit states for one affected statute's effect chain."""

    statute_id: str
    as_of: str
    states: tuple[UKSICommencementAuditState, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "as_of": self.as_of,
            "n_affecting_si": len(self.states),
            "state_counts": si_commencement_state_counts(self.states),
            "states": tuple(state.to_dict() for state in self.states),
        }


def _si_affecting_effects(effects: Iterable[UKEffectRecord]) -> list[UKEffectRecord]:
    return [
        effect
        for effect in effects
        if str(effect.affecting_class or "") == _SI_AFFECTING_CLASS
        and str(effect.affecting_act_id or "")
    ]


def _affecting_si_prospective_unresolved(
    effects: Iterable[UKEffectRecord],
    affecting_act_xml: Optional[bytes],
    *,
    as_of: str,
) -> bool:
    """True if any structural prospective effect's affecting provision is unresolved.

    Mirrors the prospective-commencement witness tri-state: ``None`` (unknown,
    do not guess) for at least one structural prospective effect is the
    ``prospective_unresolved`` signal. Read-only; no replay gating.
    """
    for effect in effects:
        if not effect.is_prospective_only or not effect.is_structural:
            continue
        in_force = affecting_provision_in_force(
            effect.affecting_provisions,
            affecting_act_xml,
            as_of=as_of,
        )
        if in_force is None:
            return True
    return False


def audit_si_commencement_for_statute(
    statute_id: str,
    archive: Any,
    *,
    as_of: str,
    diagnostics_out: Optional[list[dict[str, Any]]] = None,
) -> UKSICommencementAuditForStatute:
    """Classify the commencement-metadata state of each affecting SI of a statute.

    Deterministic: affecting SIs are processed in sorted order and each affecting
    act is classified once. Read-only — it never changes commencement
    resolution, in-force filtering, or PIT selection.
    """
    effects = load_effects_for_statute_from_archive(
        statute_id,
        archive,
        parse_rejections_out=diagnostics_out,
    )
    si_effects = _si_affecting_effects(effects)
    effects_by_act: dict[str, list[UKEffectRecord]] = {}
    for effect in si_effects:
        effects_by_act.setdefault(str(effect.affecting_act_id), []).append(effect)

    states: list[UKSICommencementAuditState] = []
    for affecting_act_id in sorted(effects_by_act):
        xml_bytes = get_affecting_act_xml(affecting_act_id, archive)
        prospective_unresolved = _affecting_si_prospective_unresolved(
            effects_by_act[affecting_act_id],
            xml_bytes,
            as_of=as_of,
        )
        source_locator = (
            f"https://www.legislation.gov.uk/{affecting_act_id}/data.xml"
            if xml_bytes
            else ""
        )
        states.append(
            audit_affecting_si_commencement(
                affecting_act_id,
                xml_bytes or b"",
                source_locator=source_locator,
                prospective_unresolved=prospective_unresolved,
            )
        )
    return UKSICommencementAuditForStatute(
        statute_id=statute_id,
        as_of=as_of,
        states=tuple(states),
    )


def si_commencement_state_counts(
    states: Iterable[UKSICommencementAuditState],
) -> dict[str, int]:
    """Return stable (sorted) per-state counts."""
    counts = Counter(state.state for state in states)
    return dict(sorted(counts.items()))
