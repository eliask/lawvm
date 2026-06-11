"""Core typed primitive for legislative preparation references.

Promotes the Finland-frontend `preliminaryWork` hcontainer content to a stable
typed primitive materialized as ``fi_preparatory_refs.parquet`` and queried via
``lawvm preparatory-refs``.

Design principles (AGENTS.md §1.9, STRINGLY_TYPED_SURFACE_AUDIT.md):
  - Frozen dataclass with slots=True — no stringly-typed dicts crossing
    phase boundaries.
  - PreparatoryReferenceKind and PreparatoryReferenceConfidence are closed
    enums — not strings.
  - canonical_id is None only when confidence is UNRESOLVED (typed absence,
    not missing key).
  - Rejected candidates emit RejectedPreparatoryCandidate — no silent drops
    (AGENTS.md §1.8).
  - CommitteeLifecycleObservation emitted for renamed/merged committees
    (AGENTS.md §1.6).

HE canonical_id join note:
  PreparatoryReference rows with kind=HE use canonical_id="he/YEAR/NUMBER",
  matching the cross_refs.py target_statute_id format ("he/YEAR/NUMBER") used
  by feature #1 (commit 77bf0c7e). The brief suggests "fi.he.YEAR.NUMBER" but
  the live codebase uses "he/YEAR/NUMBER" — we use the live format so that
  preparatory-refs HE rows are directly joinable to fi_refs.parquet HE-CITES
  edges.

This module has no Finland-specific imports. Finland extraction lives in
``lawvm.finland.preparatory_reference_extractor``.
This module only holds the shared typed primitive and observation types.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PreparatoryReferenceKind(Enum):
    """What kind of preparatory instrument is referenced."""

    HE = "he"
    """Hallituksen esitys — government proposal."""

    COMMITTEE_REPORT = "committee_report"
    """Committee mietintö: *VM (e.g. HaVM, LaVM, SiVM, StVM, MmVM, YmVM...)."""

    COMMITTEE_OPINION = "committee_opinion"
    """Committee lausunto: *VL (e.g. PeVL = perustuslakivaliokunnan lausunto)."""

    PARLIAMENT_RESPONSE = "parliament_response"
    """Eduskunnan vastaus — parliament response: EV N/YYYY."""

    PARLIAMENT_RESPONSE_COMM = "parliament_response_comm"
    """Supplementary parliament response: EVK N/YYYY (rare)."""

    LAW_INITIATIVE = "law_initiative"
    """Lakialoite — member of parliament law initiative: LA N/YYYY."""

    EU_REGULATION = "eu_regulation"
    """EU regulation: (EU|EY|EEY|ETY) [N:o] N/YYYY."""

    EU_DIRECTIVE = "eu_directive"
    """EU directive: distinguished from regulation by CELEX type character 'L'
    or keyword 'direktiivi' in text."""

    EU_DECISION = "eu_decision"
    """EU decision: CELEX type character 'D'."""

    OJ_REFERENCE = "oj_reference"
    """Official Journal reference: EUVL [L/C/S] N, DATE, s. N.
    May be standalone or co-emitted with an EU act row from the same <p>."""

    UNRESOLVED = "unresolved"
    """Text is in preliminaryWork block but pattern not classified.
    Emits RejectedPreparatoryCandidate per AGENTS.md §1.8."""


class PreparatoryReferenceConfidence(Enum):
    """How confidently the preparatory reference was classified and resolved."""

    EXACT = "exact"
    """Pattern matches strict grammar; canonical_id fully populated."""

    APPROXIMATE = "approximate"
    """Heuristic pattern matched an uncommon variant; may have partial fields."""

    UNRESOLVED = "unresolved"
    """Text in block but no pattern matched; canonical_id is None."""


# ---------------------------------------------------------------------------
# Typed primitive
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreparatoryReference:
    """A typed mention of one preparatory instrument from a statute's
    preliminaryWork hcontainer.

    This is the stable core typed primitive for legislative preparation lineage.
    It does NOT interpret the legal force or content of the reference — that is
    downstream of LawVM.

    The primitive types:
      - which statute this comes from (source_statute_id)
      - what kind of instrument (kind)
      - the canonical id for joining (canonical_id)
      - the literal text span (raw_text)
      - parsed fields for each kind (committee_abbrev, he_year, he_number,
        eu_form, eu_number, eu_year, celex, oj_series, oj_number, oj_date, oj_page)
      - how confident the classification is (confidence)
      - where in source text it was found (source_span_*)
      - when this reference state holds (valid_at_interval)

    HE join note: canonical_id for kind=HE uses "he/YEAR/NUMBER" to match
    feature #1's fi_refs.parquet target_statute_id format (cross_refs.py).

    Per AGENTS.md §1.1: canonical_id is None only when confidence is UNRESOLVED.
    Per AGENTS.md §1.8: rejected candidates produce RejectedPreparatoryCandidate.
    """

    source_statute_id: str
    """Canonical statute ID of the statute whose preliminaryWork contains this ref."""

    kind: PreparatoryReferenceKind
    """Kind of preparatory instrument."""

    canonical_id: Optional[str]
    """Canonical identifier for joining.

    Format per kind:
      HE:                       "he/YEAR/NUMBER"  (matches fi_refs HE target_statute_id)
      COMMITTEE_REPORT:         "fi.committee.{abbr_lower}.{n}.{y}"
      COMMITTEE_OPINION:        "fi.committee_opinion.{abbr_lower}.{n}.{y}"
      PARLIAMENT_RESPONSE:      "fi.ev.{n}.{y}"
      PARLIAMENT_RESPONSE_COMM: "fi.evk.{n}.{y}"
      LAW_INITIATIVE:           "fi.la.{n}.{y}"
      EU_REGULATION:            "eu.celex.{celex}" if CELEX available, else
                                "eu.reg.{eu_form}.{eu_number}.{eu_year}"
      EU_DIRECTIVE:             "eu.celex.{celex}" if CELEX available, else
                                "eu.dir.{eu_form}.{eu_number}.{eu_year}"
      EU_DECISION:              "eu.celex.{celex}" if CELEX available, else
                                "eu.dec.{eu_form}.{eu_number}.{eu_year}"
      OJ_REFERENCE:             "eu.oj.{series}.{oj_number}.{oj_year}"
      UNRESOLVED:               None
    """

    raw_text: str
    """Literal text of the preparatory reference as found in source."""

    committee_abbrev: Optional[str]
    """Committee abbreviation for COMMITTEE_REPORT/COMMITTEE_OPINION kinds.
    E.g. "HaVM", "LaVM", "PeVL". None for other kinds."""

    he_year: Optional[int]
    """Year component of HE (YYYY in HE N/YYYY). Populated for kind=HE."""

    he_number: Optional[int]
    """Number component of HE (N in HE N/YYYY). Populated for kind=HE."""

    eu_form: Optional[str]
    """EU form string: "EU", "EY", "EEY", or "ETY". Populated for EU kinds."""

    eu_number: Optional[int]
    """EU act number (N in (EU) N/YYYY). Populated for EU kinds."""

    eu_year: Optional[int]
    """EU act year (YYYY in (EU) N/YYYY). Populated for EU kinds."""

    celex: Optional[str]
    """CELEX identifier, e.g. "32017R2226". Populated when CELEX text present."""

    oj_series: Optional[str]
    """OJ series letter: "L", "C", or "S". Populated for OJ_REFERENCE."""

    oj_number: Optional[int]
    """OJ issue number. Populated for OJ_REFERENCE."""

    oj_date: Optional[date]
    """OJ publication date. Populated for OJ_REFERENCE."""

    oj_page: Optional[int]
    """OJ starting page. Populated for OJ_REFERENCE."""

    confidence: PreparatoryReferenceConfidence
    """How confidently the text was classified."""

    source_span_file: Optional[str]
    """Source XML file path, or None for in-memory extraction."""

    source_span_byte_offset: Optional[int]
    """Byte offset of text span in source file, or None."""

    source_span_byte_len: Optional[int]
    """Length of text span in bytes, or None."""

    valid_at_interval: Tuple[Optional[date], Optional[date]]
    """(start, end) when this reference state holds; end=None = currently valid."""

    def __post_init__(self) -> None:
        if self.confidence != PreparatoryReferenceConfidence.UNRESOLVED:
            if self.canonical_id is None:
                raise ValueError(
                    "PreparatoryReference.canonical_id may only be None "
                    "when confidence is UNRESOLVED; "
                    f"got {self.confidence!r}"
                )
        if not self.source_statute_id:
            raise ValueError("PreparatoryReference.source_statute_id must be non-empty")
        if not self.raw_text:
            raise ValueError("PreparatoryReference.raw_text must be non-empty")


# ---------------------------------------------------------------------------
# Observation types (AGENTS.md §1.8 — no source lane disappears)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RejectedPreparatoryCandidate:
    """A preparatory reference candidate that was recognized but not classified.

    Emitted when the extractor finds text in a preliminaryWork block that LOOKS
    like a preparatory reference but fails grammar or sanity checks, OR when
    the text does not match any known pattern (UNRESOLVED case).

    Per AGENTS.md §1.8: no source-lane candidate disappears silently.

    Attributes:
        rule_id:           Stable rule identifier for the rejection reason.
        phase:             Pipeline phase ("preparatory_ref_extraction").
        source_statute_id: Statute the candidate was found in.
        reason:            Human-readable rejection reason.
        raw_text:          The text that triggered the candidate.
        blocking:          Whether this rejection blocks compilation.
        strict_disposition: What strict mode does with this record.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    reason: str
    raw_text: str
    blocking: bool = False
    strict_disposition: str = "record"


@dataclass(frozen=True, slots=True)
class CommitteeLifecycleObservation:
    """Observation emitted when a committee abbreviation maps to a lifecycle event.

    Per AGENTS.md §1.6: unstated migration must emit lineage evidence.
    If a committee abbreviation corresponds to a committee that has since been
    renamed, merged, or dissolved, this observation records that fact.

    This defers to the canonical-actor-registry pattern from feature #2.

    Attributes:
        rule_id:           Stable rule identifier.
        phase:             Pipeline phase ("preparatory_ref_extraction").
        source_statute_id: Statute where the abbreviation was found.
        committee_abbrev:  The historical abbreviation found in source.
        canonical_id:      Current canonical committee id if resolvable, else None.
        lifecycle_event:   Human-readable description of the lifecycle event.
        blocking:          Whether this observation blocks compilation.
        strict_disposition: What strict mode does with this record.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    committee_abbrev: str
    canonical_id: Optional[str]
    lifecycle_event: str
    blocking: bool = False
    strict_disposition: str = "record"


# ---------------------------------------------------------------------------
# Parquet row serialization helpers
# ---------------------------------------------------------------------------


def preparatory_reference_to_row(ref: PreparatoryReference) -> dict[str, object]:
    """Serialize a PreparatoryReference to a flat dict for Parquet/JSONL output.

    Column names are stable per the feature brief's schema spec
    (PREPARATORY_REFERENCE_SWEEP.md §Projection export).
    Consumers must not depend on dict ordering; use column names.
    """
    valid_start, valid_end = ref.valid_at_interval
    return {
        "source_statute_id": ref.source_statute_id,
        "kind": ref.kind.value,
        "canonical_id": ref.canonical_id,
        "raw_text": ref.raw_text,
        "committee_abbrev": ref.committee_abbrev,
        "he_year": ref.he_year,
        "he_number": ref.he_number,
        "eu_form": ref.eu_form,
        "eu_number": ref.eu_number,
        "eu_year": ref.eu_year,
        "celex": ref.celex,
        "oj_series": ref.oj_series,
        "oj_number": ref.oj_number,
        "oj_date": ref.oj_date.isoformat() if ref.oj_date else None,
        "oj_page": ref.oj_page,
        "confidence": ref.confidence.value,
        "source_span_file": ref.source_span_file,
        "source_span_byte_offset": ref.source_span_byte_offset,
        "source_span_byte_len": ref.source_span_byte_len,
        "valid_at_start": valid_start.isoformat() if valid_start else None,
        "valid_at_end": valid_end.isoformat() if valid_end else None,
    }
