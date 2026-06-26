"""Core typed primitive for cross-statute citation references.

Promotes the Finland-frontend ``CrossRefEdge`` extraction to a stable typed
primitive that can be materialized as ``fi_refs.parquet`` and queried via
``lawvm refs`` and ``lawvm sql``.

Design principles (AGENTS.md §1.9, STRINGLY_TYPED_SURFACE_AUDIT.md):
  - Frozen dataclass with slots=True — no stringly-typed dicts crossing
    phase boundaries.
  - CiteKind and CiteConfidence are closed enums — not strings.
  - target_provision_ref is None only when cite_confidence is UNRESOLVED or
    BROKEN (typed absence, not missing key).
  - Rejected candidates emit RejectedRefCandidate — no silent drops
    (AGENTS.md §1.8).
  - Approximate/BROKEN confidence emits typed finding — no silent resolution
    (AGENTS.md §1.1).

This module has no Finland-specific imports. Finland extraction lives in
``lawvm.finland.cross_refs`` and ``lawvm.finland.references.ref_mention_extractor``.
This module only holds the shared typed primitive and observation types.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CiteKind(Enum):
    """What kind of statute/instrument is cited."""

    INTERNAL = "internal"
    """Same-statute cross-reference (e.g. '3 luvun 5 §:ssä säädetään')."""

    CROSS_STATUTE = "cross_statute"
    """Reference to another Finnish enacted statute."""

    EU = "eu"
    """EU directive, regulation, or treaty."""

    TREATY = "treaty"
    """Bilateral or multilateral treaty (not EU-specific)."""

    NON_STATUTORY_INSTRUMENT = "non_statutory_instrument"
    """Finnish asetus, määräys, ohje issued under statute authority."""


class CiteConfidence(Enum):
    """How confidently the target provision reference was resolved."""

    EXACT = "exact"
    """Target resolves unambiguously from the source text."""

    APPROXIMATE = "approximate"
    """Heuristic resolution is defensible (e.g. agency lifecycle rename)."""

    AMBIGUOUS = "ambiguous"
    """Multiple plausible targets; cannot pick one."""

    UNRESOLVED = "unresolved"
    """Target cannot be resolved (typo, future statute, no match)."""

    BROKEN = "broken"
    """Target was repealed/renumbered after the citation was written."""

    STATUTE_ONLY = "statute_only"
    """Act identity known, provision path / id pending.

    The citing text names an act (e.g. a by-name reference before the
    statute-name registry resolves it, or an explicit id followed by a bare
    ``§`` with no section number). The statute is fixed; the in-act provision
    target is deferred to a later resolution tier, not silently guessed.
    """

    OPEN = "open"
    """Vague catch-all reference by construction (e.g. ``muussa laissa
    säädetään``).

    These references are open-ended by design: the source text declines to
    name a specific act or provision. Per tag-don't-guess, the reference is
    typed OPEN and handed to the bounded residue overlay rather than resolved
    to a concrete target. OPEN is never assigned by a confidence threshold —
    only by closed-list vague-marker recognizers.
    """


# ---------------------------------------------------------------------------
# Source span
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Provenance back to a byte range in a source file.

    Used by ReferenceMention to anchor citations to the exact location in the
    source XML where the citation phrase was found.

    Attributes:
        source_file: Path or URI of the source document.
        byte_offset:  0-based byte offset of the start of the span.
        byte_len:     Length of the span in bytes.
    """

    source_file: str
    byte_offset: int
    byte_len: int

    def __post_init__(self) -> None:
        if self.byte_offset < 0:
            raise ValueError("SourceSpan.byte_offset must be >= 0")
        if self.byte_len < 0:
            raise ValueError("SourceSpan.byte_len must be >= 0")


# ---------------------------------------------------------------------------
# Typed provision reference for citations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvisionRef:
    """Typed reference to a provision within a statute.

    Differs from core LegalAddress in that it carries the statute_id as a
    first-class field (cross-statute citations need both).

    Attributes:
        statute_id:      Canonical statute ID, e.g. "711/2022".
                         Empty string for internal cross-references where
                         the statute is implicit from the source context.
        provision_path:  Raw AKN provision path fragment (e.g. "sec_7_sub_3")
                         or empty string if only statute-level is cited.
        section_label:   Human-readable section label, e.g. "7", "7a".
                         Empty string if not parsed.
        subsection_num:  Subsection (momentti) number, or None.
        item_label:      Item (kohta) label, or None.
        subitem_label:   Sub-item (alakohta) label, or None.
    """

    statute_id: str
    provision_path: str = ""
    section_label: str = ""
    subsection_num: Optional[int] = None
    item_label: Optional[str] = None
    subitem_label: Optional[str] = None

    def serialized(self) -> str:
        """Return a stable serialized form for parquet/JSONL output.

        Self-describing slash form
        ``statute_id[/chN]/section[/momentti][/kLABEL]``.

        Every non-statute-id, non-section segment is TYPED so the string is
        unambiguous and round-trippable regardless of which optional components
        are present:

          * ``ch{N}`` — chapter (luku). The chapter is carried in
            ``provision_path`` (AKN ``chp_N__sec_M…`` fragment), not in a
            first-class field, so it is parsed back out here. The ``ch`` prefix
            is uniform across chapter+section AND chapter-only refs, so a
            chapter can NEVER alias a section number:
            ``rikoslain 47 luvun 4 §`` → ``…/ch47/4`` ≠ bare ``rikoslain 4 §``
            → ``…/4`` (different §4s); chapter-only ``3 luvussa``
            (``provision_path="chp_3"``) → ``…/ch3`` ≠ bare section-3 ``…/3``.
          * bare integer — momentti (subsection). Momentti is the ONLY bare
            non-section segment; it is always a plain integer.
          * ``k{LABEL}`` — kohta (item). Typed so a section→kohta ref with NO
            momentti (``6 §:n 3 kohdassa`` → ``…/6/k3``) never aliases a
            section+momentti ref (``6 §:n 3 momentti`` → ``…/6/3``). Emitted
            whenever ``item_label`` is present, independent of
            ``subsection_num``.
          * ``s{LABEL}`` — alakohta (sub-item). Typed analogously to the kohta
            ``k{LABEL}`` segment so an item→sub-item ref (``1 kohdan a
            alakohta`` → ``…/k1/sa``) is unambiguous. Emitted whenever
            ``subitem_label`` is present (the alakohta always sits under a kohta,
            so ``item_label`` is normally present too).

        The statute id remains the leading segment, so ``LIKE 'statute_id%'``
        prefix queries are unaffected. Bare-section refs (no ``chp_`` in
        ``provision_path``, no kohta) serialize exactly as before.
        """
        parts = [self.statute_id]
        chapter = self._chapter_from_provision_path()
        if chapter is not None:
            parts.append(f"ch{chapter}")
        if self.section_label:
            parts.append(self.section_label)
            if self.subsection_num is not None:
                parts.append(str(self.subsection_num))
            if self.item_label:
                parts.append(f"k{self.item_label}")
            if self.subitem_label:
                parts.append(f"s{self.subitem_label}")
        return "/".join(p for p in parts if p)

    def _chapter_from_provision_path(self) -> Optional[str]:
        """Extract the chapter number from the AKN ``chp_N`` provision-path head.

        Returns the chapter label (e.g. ``"47"``, ``"9a"``) when
        ``provision_path`` begins with a ``chp_`` component, else None. Only the
        chapter head is consulted — the section/momentti/kohta are already
        carried by the human label fields.
        """
        if not self.provision_path.startswith("chp_"):
            return None
        head = self.provision_path.split("__", 1)[0]
        chapter = head[len("chp_") :]
        return chapter or None


# ---------------------------------------------------------------------------
# ReferenceMention (the core typed primitive)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReferenceMention:
    """A typed mention of one statute or provision from within another provision.

    This is the stable typed primitive promoted from Finland's CrossRefEdge.
    It does NOT interpret the legal force of the reference (incorporation /
    delegation / constraint / authority-transfer) — that is interpretation,
    downstream of LawVM.

    The primitive types:
      - where the citation is (source_provision_ref)
      - where it points (target_provision_ref)
      - what kind of target it is (cite_kind)
      - how confidently the target was resolved (cite_confidence)
      - what syntactic class it belongs to (phrase_lemma)
      - where in the source text it lives (source_span)
      - when this reference state holds (valid_at_interval)
      - back-compat link to CrossRefEdge edge types (edge_subtype)

    Per AGENTS.md §1.1: target_provision_ref is None only when confidence
    is UNRESOLVED or BROKEN — never silently widened to a wrong target.
    Per AGENTS.md §1.8: rejected candidates produce RejectedRefCandidate.
    """

    source_provision_ref: ProvisionRef
    """Where the citation text lives."""

    target_provision_ref: Optional[ProvisionRef]
    """Where the citation points; None iff cite_confidence is UNRESOLVED,
    BROKEN, or OPEN (the vague catch-all that names no target by construction)."""

    cite_kind: CiteKind
    """What kind of instrument is cited."""

    cite_confidence: CiteConfidence
    """How confidently the target was resolved."""

    phrase_lemma: str
    """Syntactic class of the citation phrase.

    Values used by the Finland extractor:
      'ref_element'      — inline AKN <ref> element (most common)
      'REPEALS'          — metadata-level repeals edge
      'ISSUED_UNDER'     — metadata issuedUnderActs edge
      'ISSUES'           — metadata issuedUnderThisAct edge
      'in_prose_fi'      — in-prose Finnish citation pattern (future)
      'eu_text_pattern'  — EU citation from text scan
    """

    source_span: Optional[SourceSpan]
    """Provenance back to the source text; None for metadata-derived edges."""

    valid_at_interval: Tuple[Optional[date], Optional[date]]
    """(start, end) when this reference state holds; end=None = currently valid."""

    edge_subtype: Optional[str]
    """Back-compat with CrossRefEdge.edge_type: CITES / REPEALS / ISSUED_UNDER /
    ISSUES. None for in-prose citations extracted from body text."""

    target_stat_hash: Optional[str] = None
    """SHA256[:16] of the target statute's consolidated XML at projection time.
    Populated by the projection layer; None during extraction."""

    surface_text: str = ""
    """Literal source text for the citation surface when the extractor owns it.
    This is intentionally not part of the stable fi_refs row schema; neutral
    interlink projections use it for viewer overlays."""

    #: Confidence states for which a None target is the typed-correct outcome.
    #: UNRESOLVED/BROKEN: target gone or never resolvable. OPEN: vague catch-all
    #: by construction (the closed-list T3 marker lane declines to name a target
    #: per tag-don't-guess); OPEN is targetless BY DESIGN, see CiteConfidence.OPEN.
    _NONE_TARGET_OK = (
        CiteConfidence.UNRESOLVED,
        CiteConfidence.BROKEN,
        CiteConfidence.OPEN,
    )

    def __post_init__(self) -> None:
        if self.cite_confidence not in self._NONE_TARGET_OK:
            if self.target_provision_ref is None:
                raise ValueError(
                    "ReferenceMention.target_provision_ref may only be None "
                    "when cite_confidence is UNRESOLVED, BROKEN, or OPEN; "
                    f"got {self.cite_confidence!r}"
                )
        if not self.phrase_lemma:
            raise ValueError("ReferenceMention.phrase_lemma must be non-empty")


# ---------------------------------------------------------------------------
# Observation types (AGENTS.md §1.8 — no source lane disappears)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RejectedRefCandidate:
    """A citation candidate that was pattern-matched but rejected.

    Emitted when the extractor identifies text that LOOKS like a citation but
    fails grammar or sanity checks. Per AGENTS.md §1.8: no parse candidate
    disappears silently.

    Attributes:
        rule_id:          Stable rule identifier for the rejection reason.
        phase:            Pipeline phase ("cross_ref_extraction").
        source_statute_id: Statute the candidate was found in.
        reason:           Human-readable rejection reason.
        matched_text:     The text that triggered the candidate.
        source_span:      Location of the candidate in source, or None.
        blocking:         Whether this rejection blocks compilation.
        strict_disposition: What strict mode does with this record.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    reason: str
    matched_text: str
    source_span: Optional[SourceSpan]
    blocking: bool = False
    strict_disposition: str = "record"


@dataclass(frozen=True, slots=True)
class BrokenReferenceFinding:
    """Finding emitted when a citation target was repealed after the citation.

    Per AGENTS.md §7: heuristics that affect legal text must have a stable
    rule ID, source witness, and finding emission. Broken references are not
    silently discarded — they remain as ReferenceMention with
    confidence=BROKEN and this finding in the audit lane.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    target_statute_id: str
    source_provision_ref_str: str
    target_provision_ref_str: str
    reason: str
    blocking: bool = False
    strict_disposition: str = "record"


@dataclass(frozen=True, slots=True)
class AmbiguousReferenceFinding:
    """Finding emitted when a citation maps to multiple plausible targets.

    Per AGENTS.md §1.1: ambiguity must remain visible. The ReferenceMention
    is emitted with confidence=AMBIGUOUS; this finding names each candidate.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    source_provision_ref_str: str
    candidate_target_ids: Tuple[str, ...]
    reason: str
    blocking: bool = False
    strict_disposition: str = "record"

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_target_ids", tuple(self.candidate_target_ids))


@dataclass(frozen=True, slots=True)
class ApproximateReferenceFinding:
    """Finding emitted when target resolved via lifecycle or renumbering heuristic.

    Per AGENTS.md §7: approximate recoveries must be witnessed.
    confidence=APPROXIMATE ReferenceMention always pairs with this finding.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    source_provision_ref_str: str
    target_provision_ref_str: str
    heuristic_applied: str
    """Description of the lifecycle/renumbering heuristic used."""
    blocking: bool = False
    strict_disposition: str = "record"


# ---------------------------------------------------------------------------
# Parquet row serialization helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Reference SET model (one source expression → one target SET with semantics)
# ---------------------------------------------------------------------------
#
# The flattened ``ReferenceMention`` relation emits one row per expanded target.
# A written RANGE ("33—35 artiklassa") or coordination ("1 ja 2 kohdassa")
# therefore lands as N rows that share only their ``surface_text`` — and at that
# point a range is indistinguishable from a candidate ambiguity (alternatives,
# pick-one) or an open vague reference. That is a type error: the source
# expression denotes a *set* of targets, and the SET SEMANTICS (every member is
# denoted, vs one-of-N is meant) is load-bearing and must be carried explicitly.
#
# These additive types restore that distinction WITHOUT changing the flattened
# ``ReferenceMention`` projection (``fi_refs.parquet`` / ``lawvm refs`` / viewer
# overlays keep working as a projection of the same facts):
#   * ``ReferenceExpression``  — the immutable surface fact (one per source span).
#   * ``ReferenceResolution``  — one expression → ONE resolution carrying the
#     whole target SET plus its ``ReferenceTargetSetSemantics``.


class ReferenceTargetSetSemantics(Enum):
    """How the target SET denoted by one source expression is to be read.

    A range/coordination is NOT an ambiguity: every listed target is denoted
    simultaneously (``ALL_VALID``). An ambiguity is a disjunction: one of the
    listed candidates is meant but the source does not say which
    (``CANDIDATE_AMBIGUITY``). The two collapse to identical flattened rows, so
    the distinction must live here, not be inferred from row count.
    """

    SINGLE = "single"
    """Exactly one target. The set has one member."""

    ALL_VALID = "all_valid"
    """A range or coordination: every listed target is denoted (conjunction).
    ``33—35 artiklassa`` denotes articles 33 AND 34 AND 35; ``1 ja 2 kohdassa``
    denotes kohta 1 AND 2."""

    CANDIDATE_AMBIGUITY = "candidate_ambiguity"
    """Alternatives (disjunction): exactly one of the listed candidates is the
    intended target, but the source does not disambiguate. Pick-one-unknown."""

    OPEN = "open"
    """Referent-bearing but not enumerable: the expression names a referent
    (e.g. a vague ``muussa laissa``) without a closed target list. The set is
    not enumerated, but the expression is NOT targetless garbage."""

    NO_ENUMERABLE_EXTENSION = "no_enumerable_extension"
    """The expression has no enumerable target extension at all (e.g. a broken
    or unresolved reference whose target set is empty and cannot be widened).
    Distinct from OPEN: OPEN has a (vague) referent, this has none to enumerate.
    """


class ReferenceResolutionStatus(Enum):
    """Closed status for a :class:`ReferenceResolution`.

    Mirrors the resolution outcome at the SET level (vs ``CiteConfidence`` which
    is per flattened member). Kept small and closed — never a free string.
    """

    RESOLVED = "resolved"
    """Every member of ``target_set`` resolved to a concrete target."""

    PARTIAL = "partial"
    """Some members resolved, some did not (mixed member confidences)."""

    UNRESOLVED = "unresolved"
    """No member resolved to a concrete target (but the expression is real)."""


@dataclass(frozen=True, slots=True)
class ReferenceExpression:
    """The IMMUTABLE surface fact: one written citation expression.

    A ``ReferenceExpression`` is the source-keyed identity of a single citation
    surface — the thing a reader points a finger at. It carries no resolved
    targets (those live in :class:`ReferenceResolution`); it is purely the
    surface and its provenance. One expression maps to one resolution per
    resolution scope.

    Attributes:
        surface_text:    The literal source text of the citation surface.
        source_span:     Provenance back to the source text, or None when the
                         expression is metadata-derived (no byte span).
        expression_kind: Coarse syntactic class of the surface: ``"single"`` /
                         ``"range"`` / ``"coordination"`` / ``"open"``. This is
                         the SURFACE shape, independent of resolution.
        surface_expr_id: Content-addressed stable identity of this expression
                         (``sha256:`` + hex digest of the canonical source-keyed
                         tuple). Stable across runs given the same source facts.
    """

    surface_text: str
    source_span: Optional[SourceSpan]
    expression_kind: str
    surface_expr_id: str

    def __post_init__(self) -> None:
        if not self.surface_text:
            raise ValueError("ReferenceExpression.surface_text must be non-empty")
        if not self.expression_kind:
            raise ValueError("ReferenceExpression.expression_kind must be non-empty")
        expected = compute_surface_expr_id(
            self.surface_text, self.source_span, self.expression_kind
        )
        if self.surface_expr_id != expected:
            raise ValueError(
                "ReferenceExpression.surface_expr_id is not the content address "
                f"of its fields: got {self.surface_expr_id!r}, "
                f"expected {expected!r}. Use ReferenceExpression.create()."
            )

    @classmethod
    def create(
        cls,
        surface_text: str,
        source_span: Optional[SourceSpan],
        expression_kind: str,
    ) -> "ReferenceExpression":
        """Build a ``ReferenceExpression`` with the content-addressed id filled in."""
        return cls(
            surface_text=surface_text,
            source_span=source_span,
            expression_kind=expression_kind,
            surface_expr_id=compute_surface_expr_id(
                surface_text, source_span, expression_kind
            ),
        )


def compute_surface_expr_id(
    surface_text: str,
    source_span: Optional[SourceSpan],
    expression_kind: str,
) -> str:
    """Compute the content-addressed identity of a reference expression.

    The identity is a ``sha256:``-prefixed hex digest (the codebase's
    content-address convention, see ``core.compile_metadata``) over a canonical
    JSON tuple of the source-keyed identity fields. Including the source span
    keys the id to the exact source location, so two distinct occurrences of the
    same literal text get distinct ids while a re-run over the same source is
    stable.
    """
    span_key: Optional[list[object]]
    if source_span is None:
        span_key = None
    else:
        span_key = [
            source_span.source_file,
            source_span.byte_offset,
            source_span.byte_len,
        ]
    payload = json.dumps(
        [surface_text, expression_kind, span_key],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    """The resolution of ONE :class:`ReferenceExpression` to a target SET.

    One expression → one resolution. The whole target set lives here as an
    ordered tuple, and ``target_set_semantics`` says how to read it (every member
    denoted vs one-of-N vs open). This is what the flattened ``ReferenceMention``
    rows are a projection OF: re-flattening a ``ReferenceResolution`` reproduces
    the per-target rows, but the set identity and semantics are no longer lost.

    Attributes:
        surface_expr_id:     Links back to the ``ReferenceExpression`` it resolves
                             (its content-addressed id).
        target_set:          Ordered tuple of resolved provision targets. May be
                             empty for ``OPEN`` / ``NO_ENUMERABLE_EXTENSION`` (the
                             referent is named but not enumerated).
        target_set_semantics: How the set is read (range/coordination vs
                             ambiguity vs open).
        reference_status:    Set-level resolution status.
        corpus_version:      Resolution scope key — the corpus version the
                             targets resolve under.
        branch:              Resolution scope key — the branch/line the targets
                             resolve under.
    """

    surface_expr_id: str
    target_set: Tuple[ProvisionRef, ...]
    target_set_semantics: ReferenceTargetSetSemantics
    reference_status: ReferenceResolutionStatus
    corpus_version: str = ""
    branch: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_set", tuple(self.target_set))
        if not self.surface_expr_id:
            raise ValueError("ReferenceResolution.surface_expr_id must be non-empty")
        # An enumerable-semantics resolution must actually enumerate at least one
        # target; a non-enumerable one (OPEN / NO_ENUMERABLE_EXTENSION) carries an
        # empty set BY DESIGN. This keeps the set-vs-open distinction fail-loud.
        enumerable = self.target_set_semantics in (
            ReferenceTargetSetSemantics.SINGLE,
            ReferenceTargetSetSemantics.ALL_VALID,
            ReferenceTargetSetSemantics.CANDIDATE_AMBIGUITY,
        )
        if enumerable and not self.target_set:
            raise ValueError(
                "ReferenceResolution with enumerable semantics "
                f"{self.target_set_semantics!r} must carry a non-empty target_set"
            )
        if (
            self.target_set_semantics is ReferenceTargetSetSemantics.SINGLE
            and len(self.target_set) != 1
        ):
            raise ValueError(
                "ReferenceResolution.SINGLE semantics must carry exactly one "
                f"target; got {len(self.target_set)}"
            )


def reference_mention_to_row(mention: ReferenceMention) -> dict[str, object]:
    """Serialize a ReferenceMention to a flat dict for Parquet/JSONL output.

    Column names are stable per the brief's schema spec (REFERENCE_MENTION_EXTRACTION.md).
    Consumers must not depend on dict ordering; use column names.
    """
    src = mention.source_provision_ref
    tgt = mention.target_provision_ref

    valid_start, valid_end = mention.valid_at_interval
    span = mention.source_span

    return {
        "source_statute_id": src.statute_id,
        "source_provision_ref_str": src.serialized(),
        "target_statute_id": tgt.statute_id if tgt else None,
        "target_provision_ref_str": tgt.serialized() if tgt else None,
        "cite_kind": mention.cite_kind.value,
        "cite_confidence": mention.cite_confidence.value,
        "edge_subtype": mention.edge_subtype,
        "phrase_lemma": mention.phrase_lemma,
        "source_span_file": span.source_file if span else None,
        "source_span_byte_offset": span.byte_offset if span else None,
        "source_span_len": span.byte_len if span else None,
        "valid_at_start": valid_start.isoformat() if valid_start else None,
        "valid_at_end": valid_end.isoformat() if valid_end else None,
        "target_stat_hash": mention.target_stat_hash,
    }
