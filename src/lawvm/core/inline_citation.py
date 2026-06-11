"""Core typed primitive for inline body-prose citation references.

Promotes Finland body-prose citation patterns to a stable typed primitive
materialized as ``fi_inline_citations.parquet`` and queried via
``lawvm inline-citations``.

These are citations in plain ``<p>`` body prose that are NOT captured by:
  - Feature #1 (``<ref>`` AKN markup citations → fi_refs.parquet)
  - Feature #11 (``<hcontainer name="preliminaryWork">`` → fi_preparatory_refs.parquet)

Design principles (AGENTS.md §1.9, STRINGLY_TYPED_SURFACE_AUDIT.md):
  - Frozen dataclass with slots=True — no stringly-typed dicts crossing
    phase boundaries.
  - InlineCitationKind and InlineCitationContext are closed enums.
  - canonical_id is None only when kind is UNRESOLVED or OLD_COMMITTEE
    (typed absence, not missing key).
  - Only emit a row when at least one recognizer fires — body text is
    overwhelmingly plain prose; do NOT emit UNRESOLVED for every <p>
    (AGENTS.md §1.8 — inline body is NOT a typed block like preliminaryWork).

Composition with siblings:
  - #1 handles ``<ref>``-marked citations. InlineCitation extractor only runs
    over text *outside* ``<ref>`` markup.
  - #11 handles preliminaryWork blocks. InlineCitation extractor SKIPS
    preliminaryWork by default, EXCEPT for EK/old-committee patterns that
    #11 marks UNRESOLVED — closing the typing loop.

This module has no Finland-specific imports. Finland extraction lives in
``lawvm.finland.inline_citation_extractor``.
This module only holds the shared typed primitive and observation types.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InlineCitationKind(Enum):
    """What kind of instrument is cited in body prose."""

    COURT_KKO = "court_kko"
    """Korkein oikeus (Supreme Court) decision: KKO YYYY:N."""

    COURT_KHO = "court_kho"
    """Korkein hallinto-oikeus (Highest Administrative Court): KHO YYYY:N or KHO YYYY/N."""

    OMBUDSMAN_EOA = "ombudsman_eoa"
    """Eduskunnan oikeusasiamies (Parliamentary Ombudsman): EOAK/N/YYYY or EOA N/YYYY/N."""

    CHANCELLOR_OKA = "chancellor_oka"
    """Oikeuskansleri (Chancellor of Justice): OKV/N/YY/YYYY."""

    STATUTE_INLINE = "statute_inline"
    """Plain-text statute citation in body prose: 'lain (N/YYYY)' form.
    Only emitted when NO <ref> markup is present (deferred to #1 if markup exists)."""

    HE_INLINE = "he_inline"
    """HE->HE policy-coordination citation in HE prose: 'HE N/YYYY'.
    Only emitted when source_doc_kind='he' and outside preliminaryWork."""

    VTV_REPORT = "vtv_report"
    """Valtiontalouden tarkastusvirasto report: 'VTV N/YYYY' or 'VTV:n tarkastuskertomus N/Y'."""

    WORKING_GROUP_MEMO = "working_group_memo"
    """Working-group memorandum: 'Tyoryhmämuistio N/YYYY' or similar."""

    PARLIAMENT_KIRJELMA = "parliament_kirjelma"
    """Eduskunnan kirjelma (modern post-2019 EV replacement): 'EK N/YYYY'.
    Closes the #11 UNRESOLVED gap for EK citations."""

    OLD_COMMITTEE = "old_committee"
    """Pre-1991 committee abbreviation forms (lvk.miet., svk.miet. etc.).
    Emitted with raw_text only; canonical_id is None; mapping is out of scope."""

    UNRESOLVED = "unresolved"
    """Pattern matched a known citation shape but failed sanity/grammar checks.
    canonical_id is None for UNRESOLVED rows."""


class InlineCitationContext(Enum):
    """Structural context of the <p> element where the citation appears."""

    ENACTED_STATUTE_BODY = "enacted_statute_body"
    """In the body of a consolidated enacted statute (finlex.farchive source)."""

    HE_RATIONALE = "he_rationale"
    """In a 'perustelut' hcontainer of a government proposal (HE body)."""

    HE_INTRODUCTION = "he_introduction"
    """In an introductory hcontainer of a government proposal (non-perustelut)."""

    PRELIMINARY_WORK = "preliminary_work"
    """In a preliminaryWork hcontainer. Only EK/old-committee run here
    (other patterns in preliminaryWork belong to #11)."""

    OTHER = "other"
    """Structural context could not be determined from ancestor hcontainers."""


# ---------------------------------------------------------------------------
# Typed primitive
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InlineCitation:
    """A typed mention of one citation from body prose.

    This is the stable core typed primitive for inline body-prose citations.
    It does NOT interpret the legal force or precedential weight of the
    reference — that is downstream of LawVM.

    The primitive types:
      - which document this comes from (source_doc_id + source_doc_kind)
      - which provision context if available (source_provision_ref)
      - what kind of citation (kind)
      - the canonical id for joining (canonical_id)
      - the literal text span (raw_text)
      - parsed fields for court decisions (case_year, case_number)
      - structural context of the paragraph (context)
      - where in source text it was found (source_span_*)

    Per AGENTS.md §1.1: canonical_id is None only when kind is UNRESOLVED
    or OLD_COMMITTEE — never silently widened to a wrong target.
    Per AGENTS.md §1.8: only emit a row when a recognizer fires; body prose
    is NOT a typed block, so no UNRESOLVED rows for every unmatched <p>.

    Composition discipline:
      - <ref>-markup citations: deferred to #1 (fi_refs.parquet).
      - preliminaryWork citations: deferred to #11 (fi_preparatory_refs.parquet),
        EXCEPT for EK/old-committee patterns that #11 marks UNRESOLVED.
      - This primitive: all other plain <p> body-prose citations.

    Court citations note (AGENTS.md §1.1): canonical_id uses the published
    citation verbatim; never synthesized. Format: 'fi.court.kko.{year}.{number}'.
    """

    source_doc_id: str
    """Canonical document ID. Statute ID (e.g. '711/2022') or HE ID (e.g. '116/2024')."""

    source_doc_kind: str
    """'statute' for enacted-statute bodies; 'he' for government-proposal bodies."""

    source_provision_ref: str
    """Provision context if available (e.g. 'sec_7' from ancestor AKN eId).
    Empty string when no structured provision context is resolvable."""

    kind: InlineCitationKind
    """Kind of inline citation."""

    canonical_id: Optional[str]
    """Canonical identifier for joining.

    Format per kind:
      COURT_KKO:          "fi.court.kko.{year}.{number}"
      COURT_KHO:          "fi.court.kho.{year}.{number}"
      OMBUDSMAN_EOA:      "fi.eoa.{number}.{year}"
      CHANCELLOR_OKA:     "fi.oka.{number}.{year}"
      STATUTE_INLINE:     "{statute_number}/{statute_year}"  (e.g. "711/2022")
      HE_INLINE:          "he/{year}/{number}"  (matches fi_refs HE target_statute_id)
      VTV_REPORT:         "fi.vtv.{number}.{year}"
      WORKING_GROUP_MEMO: "fi.wgm.{number}.{year}"
      PARLIAMENT_KIRJELMA:"fi.ek.{number}.{year}"
      OLD_COMMITTEE:      None  (raw_text only; mapping deferred)
      UNRESOLVED:         None
    """

    raw_text: str
    """Literal citation text as found in source (verbatim, never synthesized)."""

    case_year: Optional[int]
    """Year component for court/ombudsman/chancellor/vtv/he/ek citations. None for statute_inline."""

    case_number: Optional[int]
    """Number component for court/ombudsman/chancellor/vtv/he/ek citations. None for statute_inline."""

    context: InlineCitationContext
    """Structural context of the paragraph where the citation appears."""

    source_span_file: Optional[str]
    """Source file path or farchive locator. None for in-memory extraction."""

    source_span_byte_offset: Optional[int]
    """Byte offset of the text span in the source file. None if unavailable."""

    source_span_byte_len: Optional[int]
    """Length of the text span in bytes. None if unavailable."""

    def __post_init__(self) -> None:
        _nullable_kinds = (InlineCitationKind.UNRESOLVED, InlineCitationKind.OLD_COMMITTEE)
        if self.kind not in _nullable_kinds:
            if self.canonical_id is None:
                raise ValueError(
                    f"InlineCitation.canonical_id may only be None "
                    f"when kind is UNRESOLVED or OLD_COMMITTEE; "
                    f"got kind={self.kind!r}"
                )
        if not self.source_doc_id:
            raise ValueError("InlineCitation.source_doc_id must be non-empty")
        if not self.raw_text:
            raise ValueError("InlineCitation.raw_text must be non-empty")
        if self.source_doc_kind not in ("statute", "he"):
            raise ValueError(
                f"InlineCitation.source_doc_kind must be 'statute' or 'he'; "
                f"got {self.source_doc_kind!r}"
            )


# ---------------------------------------------------------------------------
# Observation types (AGENTS.md §1.8 — no source lane disappears)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InlineCitationPatternMatch:
    """Observation emitted when a recognizer pattern fires but fails sanity checks.

    Distinct from UNRESOLVED InlineCitation: this records a raw match that
    passed substring guard + regex but was rejected by field validation
    (e.g. year out of range, number too large, malformed separator).

    Per AGENTS.md §1.8: no source-lane candidate disappears silently.

    Attributes:
        rule_id:           Stable rule identifier.
        phase:             Pipeline phase ("inline_citation_extraction").
        source_doc_id:     Document where candidate was found.
        reason:            Human-readable rejection reason.
        raw_text:          The text that triggered the candidate.
        kind_attempted:    Which InlineCitationKind was attempted (value string).
        blocking:          Whether this blocks compilation.
        strict_disposition: What strict mode does with this record.
    """

    rule_id: str
    phase: str
    source_doc_id: str
    reason: str
    raw_text: str
    kind_attempted: str
    blocking: bool = False
    strict_disposition: str = "record"


# ---------------------------------------------------------------------------
# Parquet row serialization helper
# ---------------------------------------------------------------------------


def inline_citation_to_row(citation: InlineCitation) -> dict[str, object]:
    """Serialize an InlineCitation to a flat dict for Parquet/JSONL output.

    Column names are stable per the feature brief's schema spec
    (INLINE_CITATION_SWEEP.md §Projection + CLI).
    Consumers must not depend on dict ordering; use column names.
    """
    return {
        "source_doc_id": citation.source_doc_id,
        "source_doc_kind": citation.source_doc_kind,
        "source_provision_ref": citation.source_provision_ref,
        "kind": citation.kind.value,
        "canonical_id": citation.canonical_id,
        "raw_text": citation.raw_text,
        "case_year": citation.case_year,
        "case_number": citation.case_number,
        "context": citation.context.value,
        "source_span_file": citation.source_span_file,
        "source_span_byte_offset": citation.source_span_byte_offset,
        "source_span_byte_len": citation.source_span_byte_len,
    }
