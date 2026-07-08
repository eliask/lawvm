"""Resolution PROJECTION: unresolved-by-identity mentions -> ResolvedReference.

The recognizer lanes (``references/by_name.py``, ``references/eu_directive.py``)
deliberately emit references whose target carries an UNRESOLVED-by-identity
placeholder rather than a fabricated statute id:

* by-name cross-statute refs carry
  ``target_provision_ref.statute_id = "fi-name:<normalized_name>"`` with
  ``cite_confidence = STATUTE_ONLY``;
* EU-by-nickname directive refs carry
  ``target_provision_ref.statute_id = "eu-nickname:<surface>"`` with
  ``cite_confidence`` in {AMBIGUOUS, STATUTE_ONLY, EXACT}.

This module is the downstream PROJECTION that resolves those placeholders against
the registries that already exist (``registries/statute_name.py``,
``registries/eu_nickname.py``). It is the point where the two-stage
``ReferenceExpr -> ResolvedReference`` model
(``notes_internal/FI_PARSE_OVERLAY_IR_MODEL.md``) materializes: the placeholder
mention is the ``ReferenceExpr`` (what the text SAYS), and a
:class:`ResolvedReference` is what it POINTS TO (status + work_id|None +
candidates + rejected_candidates + finding).

Discipline (fail-loud / tag-don't-guess, §0.3):

* A single registry candidate -> ``status=resolved`` and the placeholder is
  rewritten to the real statute/CELEX id in a NEW mention
  (``dataclasses.replace`` — the input mention is NEVER mutated).
* More than one candidate -> ``status=ambiguous``: ALL candidates are listed, a
  finding is emitted, and ``work_id`` stays ``None``. The registry/projection
  NEVER picks one.
* A registry MISS -> ``status=statute_only``: the act identity is textual but the
  id is pending. This is a coverage gap recorded as such, NOT a silent
  ``resolved`` to nothing.
* Already-resolved mentions (explicit ``NNN/YYYY`` id, internal self-reference,
  treaty with a SopS id) pass through ``status=unchanged`` with no registry call.
* OPEN (vague catch-all) mentions pass through ``status=open``.

This is a PURE downstream projection: it does not edit any recognizer or
registry module.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import warnings
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Mapping, Optional, Sequence

from lawvm.core.reference_mention import (
    AmbiguousReferenceFinding,
    CiteConfidence,
    ReferenceMention,
)
from lawvm.finland.references.defined_terms import (
    STATUS_OK,
    DefinedTermBinding,
)
from lawvm.finland.references.registries import eu_nickname
from lawvm.finland.references.registries.statute_name import (
    Candidate,
    StatuteNameRegistry,
    _normalize_key,
    build_registry,
    default_artifact_path,
    load_statute_name_registry,
    sample_entries_from_farchive,
)

# Placeholder id prefixes emitted by the recognizer lanes (UNRESOLVED-by-identity).
_FI_NAME_PREFIX = "fi-name:"
_EU_NICKNAME_PREFIX = "eu-nickname:"

# Stable rule id + phase for the ambiguity finding (mirrors the existing
# cross_ref_extraction finding conventions in core.reference_mention).
_AMBIGUOUS_RULE_ID = "fi_ref_resolve_ambiguous_name"
_RESOLVE_PHASE = "reference_resolution"


# ---------------------------------------------------------------------------
# Resolution status + output record
# ---------------------------------------------------------------------------


class ResolutionStatus(Enum):
    """Outcome of resolving one placeholder mention against the registries.

    The fail-loud control signal of the projection stage (mirrors
    ``CiteConfidence`` but at the resolution layer, per
    ``FI_PARSE_OVERLAY_IR_MODEL.md`` ``ResolvedReference.status``).
    """

    RESOLVED = "resolved"
    """Exactly one registry candidate — placeholder rewritten to the real id."""

    AMBIGUOUS = "ambiguous"
    """>1 candidate — all listed, a finding emitted, never picked."""

    STATUTE_ONLY = "statute_only"
    """Registry miss — act identity textual, id pending (a coverage gap)."""

    OPEN = "open"
    """Vague catch-all reference — names no target by construction."""

    BROKEN = "broken"
    """Target was repealed/renumbered after the citation was written."""

    UNCHANGED = "unchanged"
    """Already resolved upstream (explicit id / internal / treaty) — pass-through."""


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    """What a placeholder mention POINTS TO after registry resolution.

    The second stage of the ``ReferenceExpr -> ResolvedReference`` model: the
    ``mention`` is the (possibly rewritten) typed reference, ``status`` is the
    fail-loud resolution outcome, ``work_id`` is the resolved statute/CELEX id
    (``None`` unless ``status`` is RESOLVED or UNCHANGED), ``candidates`` lists
    every candidate the registry returned (the full set when AMBIGUOUS), and
    ``finding`` carries the audit record for an ambiguous resolution (``None``
    otherwise).

    Attributes:
        mention: The typed reference. For a RESOLVED placeholder this is a NEW
            mention (via ``dataclasses.replace``) whose target id is the real
            statute/CELEX id; in every other case it is the input mention,
            unmutated.
        resolution_status: The resolution outcome (:class:`ResolutionStatus`).
        work_id: The resolved statute/CELEX id, or ``None`` when not a single
            unambiguous resolution.
        candidates: All candidate ids the registry returned for this mention
            (empty on a miss / pass-through). For AMBIGUOUS, all are listed and
            none is chosen.
        rejected_candidates: Candidate ids considered but not selected. The
            projection never picks among multiple candidates, so this is empty
            here (reserved for downstream tier resolution); included for parity
            with the ``ResolvedReference`` model.
        finding: An :class:`AmbiguousReferenceFinding` when ``status`` is
            AMBIGUOUS, else ``None``.
    """

    mention: ReferenceMention
    resolution_status: ResolutionStatus
    work_id: Optional[str]
    candidates: tuple[str, ...]
    rejected_candidates: tuple[str, ...]
    finding: Optional[AmbiguousReferenceFinding]


class SuccessorReferenceStatus(Enum):
    """Outcome of dated successor resolution for an already-typed reference.

    This is intentionally separate from :class:`ResolutionStatus`: ordinary
    reference resolution owns the literal cited endpoint, while successor
    resolution owns only a dated operative-endpoint claim. A citation to
    ``592/1991`` therefore remains a citation to ``592/1991`` even when an
    as-of-2026 successor projection can prove an operative endpoint.
    """

    RESOLVED = "resolved"
    """Exactly one witnessed successor chain reaches one operative work id."""

    NO_APPLICABLE_SUCCESSOR = "no_applicable_successor"
    """No witnessed successor edge applies as of the requested date."""

    MISSING_AS_OF = "missing_as_of"
    """No dated query instant was supplied, so no successor can be selected."""

    AMBIGUOUS = "ambiguous"
    """More than one successor candidate applies; no operative id is asserted."""

    UNRESOLVED_LITERAL = "unresolved_literal"
    """The input reference has no literal work id to resolve from."""


class SuccessorReferenceReasonCode(StrEnum):
    """Closed reason-code set for successor-reference resolution.

    ``StrEnum`` preserves the projection boundary's string values while keeping
    the semantic waist closed and testable inside the resolver. Add a member
    here before introducing a new successor-resolution reason.
    """

    LITERAL_WORK_ID_UNRESOLVED = "literal_work_id_unresolved"
    AS_OF_REQUIRED = "as_of_required_for_successor_resolution"
    MULTIPLE_APPLICABLE_SUCCESSORS = "multiple_applicable_successors"
    UNIQUE_WITNESSED_SUCCESSOR_CHAIN = "unique_witnessed_successor_chain"
    NO_SUCCESSOR_WITNESS_APPLICABLE_AS_OF = (
        "no_successor_witness_applicable_as_of"
    )
    SUCCESSOR_CYCLE_DETECTED = "successor_cycle_detected"


class SuccessorReferenceResolutionBasis(StrEnum):
    """Closed basis set for successor-reference operative endpoint claims."""

    SUCCESSOR_CHAIN = "successor_chain"


@dataclass(frozen=True, slots=True)
class StatuteSuccessorEdge:
    """A witnessed act-level successor/substitution edge.

    Attributes:
        predecessor_work_id: Literal work id the source text may cite.
        successor_work_id: Work id claimed as successor from ``effective_from``.
        effective_from: First date on which the edge applies.
        witness_id: Source witness identifier (Finlex lifecycle/substitution row,
            manual claim id, or another evidence handle). Required: a lifecycle
            gap alone is not a successor proof.
        witness_text: Short source excerpt / statement carried for triage.
        rule_id: Stable rule id authorising this edge family.
    """

    predecessor_work_id: str
    successor_work_id: str
    effective_from: dt.date
    witness_id: str
    witness_text: str
    rule_id: str = "fi.reference_successor.witnessed_edge"


@dataclass(frozen=True, slots=True)
class SuccessorReferenceResolution:
    """Dated operative-endpoint projection for a literal reference.

    ``literal_work_id`` is never rewritten. ``operative_work_id`` is populated
    only when the successor chain is uniquely witnessed as of ``as_of``. When the
    witness is absent or ambiguous, candidates/rejections are carried but no
    operative endpoint is asserted.
    """

    literal_work_id: Optional[str]
    operative_work_id: Optional[str]
    as_of: Optional[dt.date]
    successor_status: SuccessorReferenceStatus
    resolution_basis: SuccessorReferenceResolutionBasis
    successor_chain: tuple[StatuteSuccessorEdge, ...]
    candidates: tuple[str, ...]
    rejected_candidates: tuple[str, ...]
    reason_code: SuccessorReferenceReasonCode
    rule_id: str = "fi.reference_successor.dated_resolution"


def _literal_work_id(resolved: ResolvedReference) -> Optional[str]:
    """Return the literal resolved work id, without successor rewriting."""
    if resolved.work_id:
        return resolved.work_id
    target = resolved.mention.target_provision_ref
    if target is None:
        return None
    statute_id = target.statute_id
    if statute_id.startswith((_FI_NAME_PREFIX, _EU_NICKNAME_PREFIX)):
        return None
    return statute_id or None


def _successor_edges_by_predecessor(
    successor_edges: Sequence[StatuteSuccessorEdge],
) -> dict[str, tuple[StatuteSuccessorEdge, ...]]:
    by_pred: dict[str, list[StatuteSuccessorEdge]] = {}
    for edge in successor_edges:
        by_pred.setdefault(edge.predecessor_work_id, []).append(edge)
    return {
        pred: tuple(sorted(edges, key=lambda e: (e.effective_from, e.successor_work_id)))
        for pred, edges in by_pred.items()
    }


def resolve_successor_reference(
    resolved: ResolvedReference,
    *,
    as_of: Optional[dt.date],
    successor_edges: Sequence[StatuteSuccessorEdge],
) -> SuccessorReferenceResolution:
    """Resolve a literal reference through witnessed successor edges, if any.

    This is B5's first executable waist: the ordinary reference stays literal,
    and this projection may additionally say "as of date D, a witnessed successor
    chain makes work B the operative endpoint." It never infers a successor from
    a broken/lifecycle gap alone, never picks among multiple candidates, and never
    applies an edge whose effective date is after ``as_of``.
    """
    literal = _literal_work_id(resolved)
    if literal is None:
        return SuccessorReferenceResolution(
            literal_work_id=None,
            operative_work_id=None,
            as_of=as_of,
            successor_status=SuccessorReferenceStatus.UNRESOLVED_LITERAL,
            resolution_basis=SuccessorReferenceResolutionBasis.SUCCESSOR_CHAIN,
            successor_chain=(),
            candidates=(),
            rejected_candidates=(),
            reason_code=SuccessorReferenceReasonCode.LITERAL_WORK_ID_UNRESOLVED,
        )
    if as_of is None:
        return SuccessorReferenceResolution(
            literal_work_id=literal,
            operative_work_id=None,
            as_of=None,
            successor_status=SuccessorReferenceStatus.MISSING_AS_OF,
            resolution_basis=SuccessorReferenceResolutionBasis.SUCCESSOR_CHAIN,
            successor_chain=(),
            candidates=(),
            rejected_candidates=(),
            reason_code=SuccessorReferenceReasonCode.AS_OF_REQUIRED,
        )

    by_pred = _successor_edges_by_predecessor(successor_edges)
    current = literal
    chain: list[StatuteSuccessorEdge] = []
    rejected: list[str] = []
    seen = {literal}

    while True:
        applicable = tuple(
            edge
            for edge in by_pred.get(current, ())
            if edge.effective_from <= as_of
        )
        future = tuple(
            edge
            for edge in by_pred.get(current, ())
            if edge.effective_from > as_of
        )
        rejected.extend(edge.successor_work_id for edge in future)

        candidate_ids = tuple(dict.fromkeys(edge.successor_work_id for edge in applicable))
        if len(candidate_ids) > 1:
            return SuccessorReferenceResolution(
                literal_work_id=literal,
                operative_work_id=None,
                as_of=as_of,
                successor_status=SuccessorReferenceStatus.AMBIGUOUS,
                resolution_basis=SuccessorReferenceResolutionBasis.SUCCESSOR_CHAIN,
                successor_chain=tuple(chain),
                candidates=candidate_ids,
                rejected_candidates=tuple(dict.fromkeys(rejected)),
                reason_code=(
                    SuccessorReferenceReasonCode.MULTIPLE_APPLICABLE_SUCCESSORS
                ),
            )
        if len(candidate_ids) == 0:
            if chain:
                return SuccessorReferenceResolution(
                    literal_work_id=literal,
                    operative_work_id=current,
                    as_of=as_of,
                    successor_status=SuccessorReferenceStatus.RESOLVED,
                    resolution_basis=SuccessorReferenceResolutionBasis.SUCCESSOR_CHAIN,
                    successor_chain=tuple(chain),
                    candidates=(current,),
                    rejected_candidates=tuple(dict.fromkeys(rejected)),
                    reason_code=(
                        SuccessorReferenceReasonCode.UNIQUE_WITNESSED_SUCCESSOR_CHAIN
                    ),
                )
            return SuccessorReferenceResolution(
                literal_work_id=literal,
                operative_work_id=None,
                as_of=as_of,
                successor_status=SuccessorReferenceStatus.NO_APPLICABLE_SUCCESSOR,
                resolution_basis=SuccessorReferenceResolutionBasis.SUCCESSOR_CHAIN,
                successor_chain=(),
                candidates=(),
                rejected_candidates=tuple(dict.fromkeys(rejected)),
                reason_code=(
                    SuccessorReferenceReasonCode.NO_SUCCESSOR_WITNESS_APPLICABLE_AS_OF
                ),
            )

        edge = next(edge for edge in applicable if edge.successor_work_id == candidate_ids[0])
        if edge.successor_work_id in seen:
            return SuccessorReferenceResolution(
                literal_work_id=literal,
                operative_work_id=None,
                as_of=as_of,
                successor_status=SuccessorReferenceStatus.AMBIGUOUS,
                resolution_basis=SuccessorReferenceResolutionBasis.SUCCESSOR_CHAIN,
                successor_chain=tuple(chain),
                candidates=(edge.successor_work_id,),
                rejected_candidates=tuple(dict.fromkeys(rejected)),
                reason_code=SuccessorReferenceReasonCode.SUCCESSOR_CYCLE_DETECTED,
            )
        chain.append(edge)
        current = edge.successor_work_id
        seen.add(current)


# ---------------------------------------------------------------------------
# Local defined-term / alias bindings (in-statute scope)
# ---------------------------------------------------------------------------
#
# A statute introduces a SHORT local name and then uses it (inflected) throughout
# the rest of the document (``… asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus)
# …`` then later ``sivutuoteasetuksen 3 artiklassa``). The defined-term
# recognizer (``references/defined_terms.py``, READ-ONLY here) recognizes the
# BINDING SITE and emits :class:`DefinedTermBinding` records tying a term surface
# to a canonical ``target_ref``. This module CONSUMES those bindings as a
# per-statute table so a later inflected USE of the alias resolves EXACT/resolved
# instead of falling to ``open`` / ``statute_only``.
#
# The match is on the SAME normalized-head key the statute-name registry uses
# (``registries.statute_name._normalize_key``): the ``fi-name:<name>`` placeholder
# the by-name recognizer emits already reattaches the NOMINATIVE head to the
# invariant modifier (``sivutuoteasetuksen`` -> key ``sivutuoteasetus``), and the
# binding term is itself recorded in the nominative; both fold to the same key, so
# an inflected use matches WITHOUT a second/ad-hoc normalizer.
#
# Fail-loud / tag-don't-guess discipline (mirrors the registry projection):
#   * a term used BEFORE its binding site (the use's byte offset precedes the
#     binding's ``source_span``) does NOT resolve via the binding — that ordering
#     case is left as today (open / statute_only);
#   * a binding with ``status=unsupported_morphology`` resolves ONLY on an exact
#     surface match (no inflection guessing);
#   * >1 DISTINCT target for the same term key is ambiguous and NEVER picked — the
#     key is dropped from the resolving table entirely.


@dataclass(frozen=True, slots=True)
class _DefinedTermEntry:
    """One resolvable local binding behind a normalized term key.

    Attributes:
        target_ref: The canonical act id the term denotes (FI canonical
            ``YEAR/NUMBER`` or EU source surface). Always present (bindings with no
            ``target_ref`` carry no resolvable identity and are excluded from the
            table).
        binding_offset: Byte offset of the binding SITE in the source text — a use
            is only resolved by this binding when the use's byte offset is at or
            after this (binding precedes use). ``None`` when the binding has no
            span (ordering then cannot be verified and the binding does not apply).
        term_surface: The term surface as written at the binding site (nominative),
            folded to a normalized key for the exact-surface requirement on
            morphologically-unsupported bindings.
        morphology_ok: Whether the binding's term morphology is supported. When
            ``False`` the binding resolves a use only on an exact surface match.
    """

    target_ref: str
    binding_offset: Optional[int]
    term_surface: str
    morphology_ok: bool


@dataclass(frozen=True, slots=True)
class DefinedTermTable:
    """Per-statute defined-term / alias table consumed by resolution.

    Maps a normalized term key (``_normalize_key`` of the binding term) to the
    single resolvable :class:`_DefinedTermEntry` for that key. A key whose
    bindings name MORE THAN ONE distinct target is omitted (ambiguous — never
    picked); a key with no act-tied binding is omitted (no resolvable identity).

    Use :func:`build_defined_term_table`; do not construct directly.
    """

    _by_key: Mapping[str, _DefinedTermEntry]

    def resolve(
        self,
        name_key: str,
        *,
        use_offset: Optional[int],
        use_surface: str,
    ) -> Optional[str]:
        """Return the bound ``target_ref`` for ``name_key``, or ``None``.

        ``name_key`` is the already-normalized head key carried by the placeholder
        (``fi-name:<name>``). Returns ``None`` (no local resolution; fall through
        to the registry) when:

        * the key is unknown / ambiguous (not in the table);
        * the binding site does not precede the use (``use_offset`` is ``None``,
          or earlier than the binding's offset, or the binding has no offset) —
          the use-before-binding ordering case;
        * the binding's morphology is unsupported and ``use_surface`` is not an
          exact (normalized) match of the binding term surface.
        """
        entry = self._by_key.get(name_key)
        if entry is None:
            return None
        # Ordering: a binding applies only to a use AT OR AFTER its site. Without a
        # verifiable use offset, or a binding offset, we cannot establish that the
        # binding precedes the use — leave it to the registry (tag-don't-guess).
        if entry.binding_offset is None or use_offset is None:
            return None
        if use_offset < entry.binding_offset:
            return None
        # Morphologically-unsupported bindings resolve only on an exact surface
        # match (no inflection guessing).
        if not entry.morphology_ok:
            if _normalize_key(use_surface) != entry.term_surface:
                return None
        return entry.target_ref


def build_defined_term_table(
    bindings: list[DefinedTermBinding],
) -> DefinedTermTable:
    """Build a :class:`DefinedTermTable` from a statute's defined-term bindings.

    Bindings with no ``target_ref`` (a definitional expansion that ties the term
    to text, not an act) carry no resolvable identity and are skipped. The EARLIEST
    binding site per term is kept (a use must follow the first introduction). A
    term key bound to MORE THAN ONE distinct target is dropped entirely (ambiguous,
    never picked). The key is the registry's ``_normalize_key`` of the term — the
    SAME normalization the statute-name registry uses, so an inflected use (whose
    ``fi-name:`` placeholder reattaches the nominative head) matches.
    """
    # term key -> {target_ref -> earliest entry seen for that target}
    by_key: dict[str, dict[str, _DefinedTermEntry]] = {}
    for b in bindings:
        if not b.target_ref:
            continue
        key = _normalize_key(b.term)
        if not key:
            continue
        offset = b.source_span.byte_offset if b.source_span is not None else None
        entry = _DefinedTermEntry(
            target_ref=b.target_ref,
            binding_offset=offset,
            term_surface=key,
            morphology_ok=(b.binding_status == STATUS_OK),
        )
        targets = by_key.setdefault(key, {})
        prior = targets.get(b.target_ref)
        if prior is None:
            targets[b.target_ref] = entry
        else:
            # Same target re-bound: keep the EARLIEST site (a use must follow the
            # first introduction). A None offset never displaces a real one.
            if prior.binding_offset is None or (
                offset is not None and offset < prior.binding_offset
            ):
                targets[b.target_ref] = entry

    resolved: dict[str, _DefinedTermEntry] = {}
    for key, targets in by_key.items():
        if len(targets) == 1:
            # Exactly one distinct target — resolvable.
            resolved[key] = next(iter(targets.values()))
        # >1 distinct target → ambiguous: drop the key (never pick).
    return DefinedTermTable(_by_key=resolved)


# ---------------------------------------------------------------------------
# In-statute name->id anaphora (repeated by-name citation)
# ---------------------------------------------------------------------------
#
# A statute commonly NAMES an act once with its explicit id —
# ``yhteistoimintalain (1333/2021) 5 luvussa`` — then re-cites the SAME act by
# bare title later — ``yhteistoimintalain 5 §:ssä``. The bare repeat is a
# by-name placeholder (``fi-name:yhteistoimintalaki``); its id was established
# earlier in the same text by the id-anchored occurrence of the SAME name. This
# is name-level anaphora: the bare repeat co-refers with the earlier id-anchored
# citation of the identical normalized name.
#
# We build the binding table from the SAME mention batch resolve_mentions
# already holds: every id-anchored citation (a CROSS_STATUTE mention whose
# target is a concrete ``NUMBER/YEAR`` id) whose surface carries a distinctive
# statute-NAME head binds that name -> that id at its byte offset. The name is
# recovered by re-running the by-name name recognizer on the surface's name part
# (left of the ``(id)``): a bare ``lain (335/2007)`` ("the act (id)") carries NO
# distinctive name head and establishes NO binding (fail-loud: a generic head is
# not an antecedent for later bare uses). A name bound to >1 distinct id in the
# same statute is AMBIGUOUS and dropped (never picked).


# Concrete Finnish statute id ``NUMBER/YEAR`` (EU/celex/he/fi-name ids never
# match — they carry a non-numeric prefix or extra path segments).
_FI_STATUTE_ID_RE = re.compile(r"^[0-9]+/[0-9]{4}$")


@dataclass(frozen=True)
class _NameIdEntry:
    target_ref: str
    binding_offset: int


@dataclass(frozen=True)
class NameIdAnaphoraTable:
    """In-statute name->id bindings established by id-anchored citations.

    Keyed by the registry-normalized statute-name head (the same key a
    ``fi-name:`` placeholder carries). A bare repeat of a name resolves to the
    bound id only when an id-anchored occurrence of that name PRECEDES the use
    (byte-offset ordering) — never a use before its first id-anchored mention.
    """

    _by_key: Mapping[str, _NameIdEntry]

    def resolve(self, name_key: str, *, use_offset: Optional[int]) -> Optional[str]:
        entry = self._by_key.get(name_key)
        if entry is None:
            return None
        # The binding must precede the use (anaphora points BACKWARD). Without a
        # verifiable use offset we cannot establish the ordering — decline.
        if use_offset is None or use_offset < entry.binding_offset:
            return None
        return entry.target_ref


def _recover_name_key(surface: str) -> Optional[str]:
    """Recover the normalized name key from an id-anchored citation surface.

    The surface of an id-anchored named citation is ``<name-inflected> (id) …``
    (``yhteistoimintalain (1333/2021) 5 luvussa``). The by-name name recognizer
    declines an id-anchored surface (that case belongs to the plain-text lane),
    so we feed it the NAME part alone — the text left of the first ``(`` — and
    read back the ``fi-name:`` key it derives. A surface whose head is a bare
    generic ``lain`` / ``asetuksen`` (no distinctive title) yields no by-name
    mention and therefore no key (None): a generic head is not a name antecedent.

    FAIL-LOUD on a dropped left modifier. The by-name head regex captures only
    the last conjunct of a SPACE-separated multi-word name —
    ``maatalousyrittäjien tapaturmavakuutuslain`` yields the key
    ``tapaturmavakuutuslaki`` (the ``maatalousyrittäjien`` modifier is dropped),
    which is a DIFFERENT act (1026/1981) from the plain ``tapaturmavakuutuslaki``
    (1948/608). Binding the truncated key would conflate the two acts and
    mis-resolve a later bare ``tapaturmavakuutuslain`` repeat. We therefore accept
    the key ONLY when the recognized surface covers the WHOLE name part (the
    recognizer dropped nothing). A hyphen-coordinated compound
    (``perintö- ja lahjaverolain``) IS captured whole, so it passes; a separate
    leading word-modifier is rejected (return ``None`` — no binding).
    """
    if not surface:
        return None
    name_part = surface.split("(", 1)[0].strip()
    if not name_part:
        return None
    # Local import: by_name is the recognizer that mints fi-name keys; importing
    # it at module scope would couple this resolution module to the recognizer
    # package's import graph (resolve.py is imported by the recognizer lane).
    from lawvm.finland.references.by_name import recognize_by_name_refs

    normalized_name_part = " ".join(name_part.split())
    for m in recognize_by_name_refs(name_part):
        tgt = m.target_provision_ref
        if tgt is None or not tgt.statute_id.startswith(_FI_NAME_PREFIX):
            continue
        # Reject a truncated capture: the recognized surface must span the whole
        # name part, else a dropped leading word-modifier would conflate two
        # distinct compound act names under one key (fail-loud, no binding).
        recognized_surface = " ".join((m.surface_text or "").split())
        if recognized_surface != normalized_name_part:
            return None
        return tgt.statute_id[len(_FI_NAME_PREFIX) :]
    return None


def build_name_id_anaphora_table(
    mentions: list[ReferenceMention],
) -> NameIdAnaphoraTable:
    """Build the in-statute name->id table from a statute's mention batch.

    Scans the id-anchored citations (CROSS_STATUTE mentions whose target is a
    concrete ``NUMBER/YEAR`` id with a locatable byte span) and records, per
    recovered statute name, the EARLIEST binding offset. A name bound to MORE
    THAN ONE distinct id in the same statute is AMBIGUOUS and dropped (never
    picked). Mentions without a span, without a concrete id, or whose surface
    carries no distinctive name head contribute no binding (fail-loud).
    """
    # name key -> {target id -> earliest byte offset for that id}
    by_key: dict[str, dict[str, int]] = {}
    for m in mentions:
        tgt = m.target_provision_ref
        if tgt is None or not _FI_STATUTE_ID_RE.match(tgt.statute_id):
            continue
        if m.source_span is None:
            continue
        key = _recover_name_key(m.surface_text or "")
        if not key:
            continue
        offset = m.source_span.byte_offset
        by_id = by_key.setdefault(key, {})
        prior = by_id.get(tgt.statute_id)
        if prior is None or offset < prior:
            by_id[tgt.statute_id] = offset

    resolved: dict[str, _NameIdEntry] = {}
    for key, by_id in by_key.items():
        if len(by_id) != 1:
            # No id-anchored binding, or >1 distinct id (ambiguous) — drop.
            continue
        target_id, offset = next(iter(by_id.items()))
        resolved[key] = _NameIdEntry(target_ref=target_id, binding_offset=offset)
    return NameIdAnaphoraTable(_by_key=resolved)


# Provenance tag recorded on a mention resolved via in-statute name anaphora
# (a bare repeat of an earlier id-anchored citation of the same name).
_NAME_ANAPHORA_PHRASE_LEMMA = "name_id_anaphora_local_binding"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _placeholder_kind(mention: ReferenceMention) -> Optional[str]:
    """Return the placeholder prefix carried by ``mention``, or ``None``.

    A mention is an UNRESOLVED-by-identity placeholder iff its target id starts
    with ``fi-name:`` or ``eu-nickname:``. Returns the matched prefix so the
    caller routes to the right registry.
    """
    target = mention.target_provision_ref
    if target is None:
        return None
    sid = target.statute_id
    if sid.startswith(_FI_NAME_PREFIX):
        return _FI_NAME_PREFIX
    if sid.startswith(_EU_NICKNAME_PREFIX):
        return _EU_NICKNAME_PREFIX
    return None


def _rewrite_target_id(
    mention: ReferenceMention,
    work_id: str,
    *,
    phrase_lemma: Optional[str] = None,
    cite_confidence: CiteConfidence = CiteConfidence.EXACT,
) -> ReferenceMention:
    """Return a NEW mention with the target's statute_id rewritten to ``work_id``.

    The input mention is never mutated (frozen dataclasses, ``replace``).
    ``cite_confidence`` is the cite_confidence stamped on the rewritten mention:

    * ``EXACT`` (the default) — the identity is resolved to a single real id by an
      EXACT registry key match or an in-statute binding that names it verbatim.
    * ``APPROXIMATE`` — a BEST-EFFORT resolution: a heuristic pick among genuinely
      multiple candidates (as-of-live version preference), an inflection-robust
      content-word-set match, a jurisdiction-flipped EU-nickname fallback, or an
      as-of re-widened out-of-window version. These are defensible but NOT parsed
      exact, so they carry the lower APPROXIMATE confidence to keep the guess
      distinguishable downstream (honesty over recall — §0.3 tag-don't-guess).

    ``phrase_lemma`` overrides the syntactic-class label on the rewritten mention
    when provided (used to record the resolution-path provenance).
    """
    target = mention.target_provision_ref
    assert target is not None  # guarded by caller
    new_target = dataclasses.replace(target, statute_id=work_id)
    changes: dict[str, object] = {
        "target_provision_ref": new_target,
        "cite_confidence": cite_confidence,
    }
    if phrase_lemma is not None:
        changes["phrase_lemma"] = phrase_lemma
    return dataclasses.replace(mention, **changes)


# Provenance tag recorded on the rewritten mention's ``phrase_lemma`` when a
# placeholder resolves via an in-statute defined-term binding rather than the
# statute-name registry.
_LOCAL_BINDING_PHRASE_LEMMA = "defined_term_local_binding"

# Provenance tag recorded when a ``fi-name:`` placeholder resolves via the FP-gated
# content-word-set fallback (a head-first descriptive cite whose complement differs
# from the official title only by premodifier inflection), after the exact-surface
# registry lookup missed.
_CWS_FALLBACK_PHRASE_LEMMA = "statute_name_content_word_set_fallback"

# Provenance tag recorded when a ``fi-name:`` descriptive placeholder resolves via
# the TRAILING-VOWEL-FOLDED content-word-set lane (the last content-lane recall
# step), after BOTH the exact-surface lookup and the plain whole-set content match
# missed. A near-match on the folded stems -> APPROXIMATE, never EXACT.
_CWS_FOLDED_FALLBACK_PHRASE_LEMMA = "statute_name_content_word_set_folded_fallback"

# Provenance tag recorded when a MULTIPLE (multi-version) registry result is
# collapsed to one candidate by the as-of-live-version preference (exactly one
# candidate is still in force). This is a HEURISTIC pick among genuinely multiple
# candidates, so the rewritten mention carries APPROXIMATE confidence, never EXACT.
_LIVE_VERSION_PHRASE_LEMMA = "statute_name_as_of_live_version_pick"

# Provenance tag recorded when an as-of window that excluded every version is
# re-widened to the whole timeline and yields a single (out-of-window) candidate.
# The candidate's window did NOT cover the mention's as-of instant, so the pick is
# a best-effort (APPROXIMATE), not an exact in-window resolution.
_REWIDENED_PHRASE_LEMMA = "statute_name_as_of_rewidened_out_of_window"

# Provenance tag recorded when a multi-version MULTIPLE result is collapsed to one
# candidate by the CITED-HEAD law/decree filter (a ``laki`` cite dropped the
# ``asetus`` homonym). A best-effort pick -> APPROXIMATE.
_HEAD_FILTER_PHRASE_LEMMA = "statute_name_law_decree_head_pick"

# Provenance tag recorded when a ``fi-name:`` placeholder resolves via the CURATED
# colloquial-nickname alias table (``registries/statute_name_aliases.py``): the cite
# used a nickname whose official title does not contain it (``julkisuuslaki`` ->
# 1999/621), so no generated surface could ever match and the identity comes from a
# human-verified 1:1 alias, NOT a parsed-exact surface. A defensible best-effort
# resolution -> APPROXIMATE (never EXACT), so a curated-alias id stays distinguishable
# from a generation-exact one downstream (honesty over recall — §0.3 tag-don't-guess).
_CURATED_ALIAS_PHRASE_LEMMA = "statute_name_curated_alias"


# ---------------------------------------------------------------------------
# Multi-version disambiguation (recall lever, honesty-preserving)
# ---------------------------------------------------------------------------
#
# A statute NAME commonly maps to several ids over time (an act repealed and
# re-enacted under the same name; a chain of amending acts sharing a title). The
# registry lands ``multiple`` and NEVER picks — the correct fail-loud default. But
# two PRINCIPLED signals often single out one candidate WITHOUT guessing an
# arbitrary "newest":
#
#   (1) LAW-vs-DECREE by the CITED HEAD. The by-name key carries the head the text
#       used (``laki`` vs ``asetus``). A ``laki`` cite can never denote an
#       ``asetus`` act of the same subject (a law and a decree are different
#       statutes), so a head mismatch drops that candidate. When exactly one
#       candidate's official-title head matches the cited head, it is the target.
#   (2) AS-OF-LIVE VERSION. When exactly one candidate is STILL IN FORCE
#       (``valid_to is None``) and every other candidate has a CLOSED window (it
#       was repealed/superseded), the live version is the defensible referent of a
#       bare name read in the consolidated present. This is NOT "pick the newest"
#       (a re-enacted-then-also-repealed pair leaves zero or several live and is
#       left ambiguous); it is "the one act of this name that is still the law".
#
# BOTH are best-effort disambiguations of a genuinely-multiple result, so the
# resolution is stamped APPROXIMATE (not EXACT): downstream can tell a parsed-exact
# id from a heuristically-disambiguated one. When neither signal singles out one
# candidate the result stays AMBIGUOUS (fail-loud, no pick). Default-ON
# (``disambiguate_multi_version=True``): the reference subsystem is read/publish
# (citation graph / viewer / analysis), never a replay input, and the pick is
# always stamped APPROXIMATE so a strict consumer can filter it. Pass ``False`` to
# recover the byte-unchanged (fail-loud AMBIGUOUS) behavior for a specific caller.


def _title_head(canonical_title: str) -> Optional[str]:
    """Return the closed statute head (``laki``/``asetus``/…) a title ends in.

    The head is read from the title's LAST word (``Ympäristönsuojelulaki`` ->
    ``laki``; ``Valtioneuvoston asetus …`` is head-first so its first word
    ``asetus`` is the head). We check both the last-word suffix (compound titles)
    and the first word (head-first descriptive titles). Returns ``None`` when no
    known head is found (the title carries no law/decree distinction we can use).
    """
    low = canonical_title.strip().rstrip(".").lower()
    if not low:
        return None
    words = low.split()
    # Head-first descriptive title (``Laki …`` / ``Asetus …`` / ``Valtioneuvoston
    # asetus …``): the head is a standalone leading word.
    for w in words[:2]:
        if w in ("laki", "asetus"):
            return w
    # Compound title: the trailing head rides the last word (``…laki`` / ``…asetus``).
    last = words[-1]
    for head in ("asetus", "laki"):
        if last.endswith(head):
            return head
    return None


def _cited_head(name_key: str) -> Optional[str]:
    """Return the statute head the CITED ``fi-name:`` key carries (``laki``/``asetus``).

    The by-name key is either head-first (``laki <body>``) or a trailing-head
    compound (``ympäristönsuojelulaki``). ``None`` when neither shape names a
    law/decree head (no head signal to filter on).
    """
    low = name_key.strip().lower()
    if not low:
        return None
    first = low.split(" ", 1)[0]
    if first in ("laki", "asetus"):
        return first
    for head in ("asetus", "laki"):
        if low.endswith(head):
            return head
    return None


def _disambiguate_multi_version(
    name_key: str,
    candidates: "tuple[Candidate, ...]",
) -> Optional[tuple[str, str]]:
    """Try to single out ONE candidate from a MULTIPLE result, honestly.

    Applies, in order: (1) the cited-head law/decree filter, then (2) the
    as-of-live-version preference (exactly one candidate still in force). Returns
    ``(work_id, provenance_lemma)`` when exactly one candidate survives the
    filters, else ``None`` (leave AMBIGUOUS — no guess). The returned resolution is
    a best-effort pick and is stamped APPROXIMATE by the caller.
    """
    survivors = list(candidates)

    # (1) LAW-vs-DECREE by cited head. Drop candidates whose title head disagrees
    # with the head the citation used. Only apply when the cite HAS a head signal
    # AND every candidate exposes a head (else a headless title would be dropped
    # for lacking a signal, which is not a real mismatch).
    cited = _cited_head(name_key)
    if cited is not None:
        # ``hasattr`` guard: a duck-typed candidate stub without a title exposes
        # no head signal, so the head filter is skipped (never a false drop).
        heads = [
            _title_head(c.canonical_title) if hasattr(c, "canonical_title") else None
            for c in survivors
        ]
        if all(h is not None for h in heads):
            head_matched = [
                c for c, h in zip(survivors, heads, strict=True) if h == cited
            ]
            if head_matched and len(head_matched) < len(survivors):
                survivors = head_matched
                if len({c.statute_id for c in survivors}) == 1:
                    # The head filter alone singled out one act (a law/decree pick).
                    return survivors[0].statute_id, _HEAD_FILTER_PHRASE_LEMMA

    # (2) AS-OF-LIVE VERSION. Exactly one candidate is still in force -> it is the
    # referent of the bare name in the consolidated present. Zero or several live
    # candidates -> genuinely ambiguous, leave it.
    # ``hasattr`` guard: a candidate whose lifecycle window is unknown (a stub
    # without ``valid_to``) is not treated as live — no unconfirmed pick.
    live = [c for c in survivors if hasattr(c, "valid_to") and c.valid_to is None]
    if len({c.statute_id for c in live}) == 1:
        return live[0].statute_id, _LIVE_VERSION_PHRASE_LEMMA

    return None


def _ambiguity_finding(
    mention: ReferenceMention,
    candidates: tuple[str, ...],
) -> AmbiguousReferenceFinding:
    """Build the audit finding for an ambiguous placeholder resolution."""
    src = mention.source_provision_ref
    target = mention.target_provision_ref
    surface = mention.surface_text or (target.statute_id if target else "")
    return AmbiguousReferenceFinding(
        rule_id=_AMBIGUOUS_RULE_ID,
        phase=_RESOLVE_PHASE,
        source_statute_id=src.statute_id,
        source_provision_ref_str=src.serialized(),
        candidate_target_ids=candidates,
        reason=(
            f"Reference surface {surface!r} resolves to "
            f"{len(candidates)} candidates; the registry refuses to pick one."
        ),
    )


def _mention_validity_as_of(mention: ReferenceMention) -> Optional[dt.date]:
    """Derive the per-mention validity instant to resolve an act-name against.

    Returns the START of the mention's ``valid_at_interval`` — the instant the
    citing reference state began holding — so an act name is resolved to the
    version in force WHILE the citing text was valid (static-as-of-citing at the
    mention's own granularity). When the interval start is ``None`` (open / unknown
    on the left), no instant can be established and ``None`` is returned: the
    registry then resolves against the whole timeline and a multi-version name
    stays AMBIGUOUS (fail-loud, no guess).

    NOTE: the interval START is used deliberately, NOT the citing statute's
    enactment year. Bodies are read in CONSOLIDATED (current) form, so a statute
    enacted in year Y may legitimately cite a post-Y version; the enactment year
    would mis-resolve such citations. The mention's own ``valid_at_interval`` is
    the only safe per-mention instant.
    """
    start, _end = mention.valid_at_interval
    return start


def _resolve_fi_name(
    mention: ReferenceMention,
    statute_registry: StatuteNameRegistry,
    as_of: Optional[dt.date],
    defined_terms: Optional[DefinedTermTable],
    name_id_anaphora: Optional[NameIdAnaphoraTable] = None,
    disambiguate_multi_version: bool = True,
) -> ResolvedReference:
    """Resolve a ``fi-name:<name>`` placeholder.

    A local in-statute defined-term binding is consulted FIRST: when the
    placeholder name matches a binding (on the registry's normalized-head key) and
    the binding precedes the use, the placeholder resolves EXACT/resolved to the
    binding's ``target_ref`` (provenance recorded on ``phrase_lemma``). Failing
    that, an in-statute name->id anaphora binding (a bare repeat of an earlier
    id-anchored citation of the SAME name in this statute) is consulted next —
    the same name with the same single id established earlier resolves the bare
    repeat to that id. Otherwise the placeholder falls through to the statute-name
    registry exactly as before.
    """
    target = mention.target_provision_ref
    assert target is not None
    name = target.statute_id[len(_FI_NAME_PREFIX) :]

    use_offset = (
        mention.source_span.byte_offset if mention.source_span is not None else None
    )

    if defined_terms is not None:
        bound = defined_terms.resolve(
            name,
            use_offset=use_offset,
            use_surface=mention.surface_text or name,
        )
        if bound is not None:
            return ResolvedReference(
                mention=_rewrite_target_id(
                    mention, bound, phrase_lemma=_LOCAL_BINDING_PHRASE_LEMMA
                ),
                resolution_status=ResolutionStatus.RESOLVED,
                work_id=bound,
                candidates=(bound,),
                rejected_candidates=(),
                finding=None,
            )

    if name_id_anaphora is not None:
        bound = name_id_anaphora.resolve(name, use_offset=use_offset)
        if bound is not None:
            return ResolvedReference(
                mention=_rewrite_target_id(
                    mention, bound, phrase_lemma=_NAME_ANAPHORA_PHRASE_LEMMA
                ),
                resolution_status=ResolutionStatus.RESOLVED,
                work_id=bound,
                candidates=(bound,),
                rejected_candidates=(),
                finding=None,
            )

    result = statute_registry.lookup(name, as_of)

    # An as-of filter that excludes EVERY version is NOT a registry miss: the act
    # name IS known, the instant simply falls before any registered version's
    # window. Downgrading to STATUTE_ONLY here would erase a known identity on a
    # guessed instant. Re-check unfiltered: if the whole-timeline lookup still
    # yields candidates, the name stays AMBIGUOUS over those candidates (no pick,
    # fail-loud) instead of falsely reporting a coverage gap.
    #
    # HONESTY: a re-widened result is OUT-OF-WINDOW — the surviving candidate's
    # window did not cover the mention's as-of instant. If the whole-timeline
    # lookup is ``single`` we still resolve it (the name IS that act), but as a
    # best-effort (APPROXIMATE), never a laundered EXACT: the as-of that would have
    # confirmed the version was excluded.
    rewidened = False
    if as_of is not None and result.registry_status == "none":
        unfiltered = statute_registry.lookup(name, None)
        if unfiltered.registry_status != "none":
            result = unfiltered
            rewidened = True

    candidate_ids = tuple(c.statute_id for c in result.candidates)

    if result.registry_status == "single":
        work_id = candidate_ids[0]
        # An in-window single is EXACT; a re-widened (out-of-window) single is a
        # best-effort APPROXIMATE pick with the rewiden provenance recorded.
        if rewidened:
            return ResolvedReference(
                mention=_rewrite_target_id(
                    mention,
                    work_id,
                    phrase_lemma=_REWIDENED_PHRASE_LEMMA,
                    cite_confidence=CiteConfidence.APPROXIMATE,
                ),
                resolution_status=ResolutionStatus.RESOLVED,
                work_id=work_id,
                candidates=candidate_ids,
                rejected_candidates=(),
                finding=None,
            )
        # A curated-alias-only single hit is a best-effort nickname resolution, not a
        # parsed-exact surface: stamp APPROXIMATE with the curated-alias provenance so
        # the human-verified 1:1 nickname pick stays distinguishable from an EXACT one.
        # ``getattr`` default: a duck-typed registry result (test stubs, alternate
        # substrates) that predates the ``via_alias`` provenance field degrades to a
        # plain EXACT hit — the field is an optional best-effort signal, never required.
        if getattr(result, "via_alias", False):
            return ResolvedReference(
                mention=_rewrite_target_id(
                    mention,
                    work_id,
                    phrase_lemma=_CURATED_ALIAS_PHRASE_LEMMA,
                    cite_confidence=CiteConfidence.APPROXIMATE,
                ),
                resolution_status=ResolutionStatus.RESOLVED,
                work_id=work_id,
                candidates=candidate_ids,
                rejected_candidates=(),
                finding=None,
            )
        return ResolvedReference(
            mention=_rewrite_target_id(mention, work_id),
            resolution_status=ResolutionStatus.RESOLVED,
            work_id=work_id,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=None,
        )
    if result.registry_status == "multiple":
        # A genuinely multi-version name. When enabled, try a PRINCIPLED honest
        # disambiguation (cited-head law/decree filter, then as-of-live version);
        # a single survivor resolves APPROXIMATE (a best-effort pick among
        # multiple, never laundered EXACT), the others stay listed as rejected.
        if disambiguate_multi_version:
            picked = _disambiguate_multi_version(name, result.candidates)
            if picked is not None:
                work_id, provenance = picked
                rejected = tuple(cid for cid in candidate_ids if cid != work_id)
                return ResolvedReference(
                    mention=_rewrite_target_id(
                        mention,
                        work_id,
                        phrase_lemma=provenance,
                        cite_confidence=CiteConfidence.APPROXIMATE,
                    ),
                    resolution_status=ResolutionStatus.RESOLVED,
                    work_id=work_id,
                    candidates=candidate_ids,
                    rejected_candidates=rejected,
                    finding=None,
                )
        return ResolvedReference(
            mention=dataclasses.replace(
                mention, cite_confidence=CiteConfidence.AMBIGUOUS
            ),
            resolution_status=ResolutionStatus.AMBIGUOUS,
            work_id=None,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=_ambiguity_finding(mention, candidate_ids),
        )
    # "none" on the EXACT-surface index — before declaring a coverage gap, try the
    # FP-gated content-word-set fallback: a head-first descriptive cite whose
    # complement differs from the official title only by a premodifier INFLECTION
    # (singular ``viranomaisen`` vs official plural ``viranomaisten``) misses the
    # exact key but hits the base-act content-word-set index. The fallback is
    # strict (clean head-first ``Laki/Asetus <body>`` only, head must match, >=2
    # distinctive content stems, WHOLE-set match, no subset) and stays fail-loud:
    # single → resolved, multiple → ambiguous (never picked), none → fall through.
    cws_result = statute_registry.lookup_content_word_set(name, as_of)
    if as_of is not None and cws_result.registry_status == "none":
        # Same as-of-vs-known reconciliation as the exact lane: a window that
        # excludes every version is not a content miss if the whole timeline has
        # candidates — re-check unfiltered so a known-but-out-of-window name stays
        # AMBIGUOUS rather than falsely a coverage gap.
        unfiltered_cws = statute_registry.lookup_content_word_set(name, None)
        if unfiltered_cws.registry_status != "none":
            cws_result = unfiltered_cws
    if cws_result.registry_status != "none":
        cws_ids = tuple(c.statute_id for c in cws_result.candidates)
        if cws_result.registry_status == "single":
            # The content-word-set match is inflection-robust but NOT an exact
            # surface hit — the cite's premodifier inflection differed from the
            # official title, so the identity is inferred from a stem-set match, a
            # best-effort resolution. Stamp APPROXIMATE (not EXACT) so a guessed
            # descriptive-title id stays distinguishable from a parsed-exact one.
            return ResolvedReference(
                mention=_rewrite_target_id(
                    mention,
                    cws_ids[0],
                    phrase_lemma=_CWS_FALLBACK_PHRASE_LEMMA,
                    cite_confidence=CiteConfidence.APPROXIMATE,
                ),
                resolution_status=ResolutionStatus.RESOLVED,
                work_id=cws_ids[0],
                candidates=cws_ids,
                rejected_candidates=(),
                finding=None,
            )
        # multiple — genuinely ambiguous content set: list all, never pick.
        return ResolvedReference(
            mention=dataclasses.replace(
                mention, cite_confidence=CiteConfidence.AMBIGUOUS
            ),
            resolution_status=ResolutionStatus.AMBIGUOUS,
            work_id=None,
            candidates=cws_ids,
            rejected_candidates=(),
            finding=_ambiguity_finding(mention, cws_ids),
        )

    # Plain whole-set content match also missed. LAST content-lane recall step: the
    # TRAILING-VOWEL-FOLDED set, which collapses the residual singular/plural stem
    # artifact (cited ``viranomaisen`` sg vs official ``viranomaisten`` pl) the plain
    # whole-set match cannot. Same fail-loud shape: single → RESOLVED APPROXIMATE (a
    # near-match, not exact), multiple → AMBIGUOUS (never picked), none → fall
    # through to the EU / STATUTE_ONLY tail. The fold is bounded (one vowel/stem) and
    # verified to add zero cross-id collisions, so it never merges two distinct acts.
    folded_result = statute_registry.lookup_content_word_set_folded(name, as_of)
    if as_of is not None and folded_result.registry_status == "none":
        unfiltered_folded = statute_registry.lookup_content_word_set_folded(name, None)
        if unfiltered_folded.registry_status != "none":
            folded_result = unfiltered_folded
    if folded_result.registry_status != "none":
        folded_ids = tuple(c.statute_id for c in folded_result.candidates)
        if folded_result.registry_status == "single":
            return ResolvedReference(
                mention=_rewrite_target_id(
                    mention,
                    folded_ids[0],
                    phrase_lemma=_CWS_FOLDED_FALLBACK_PHRASE_LEMMA,
                    cite_confidence=CiteConfidence.APPROXIMATE,
                ),
                resolution_status=ResolutionStatus.RESOLVED,
                work_id=folded_ids[0],
                candidates=folded_ids,
                rejected_candidates=(),
                finding=None,
            )
        return ResolvedReference(
            mention=dataclasses.replace(
                mention, cite_confidence=CiteConfidence.AMBIGUOUS
            ),
            resolution_status=ResolutionStatus.AMBIGUOUS,
            work_id=None,
            candidates=folded_ids,
            rejected_candidates=(),
            finding=_ambiguity_finding(mention, folded_ids),
        )
    # still "none" — the act name is NOT a Finnish statute the registry knows.
    # Before declaring a genuine coverage gap, try the EU-nickname registry: a by-name
    # citation of an EU regulation carries a Finnish-shaped ``-asetus`` head
    # (``sivutuoteasetuksen``, ``vakavaraisuusasetuksen``), so the by-name lane
    # types it ``fi-name:`` even though it denotes an EU instrument. This fallback
    # fires ONLY after the statute registry has missed, so a real Finnish act is
    # never shadowed by an EU nickname (statute-first; the EU table is consulted
    # only on a Finnish miss). It is the same fail-loud projection as the explicit
    # ``eu-nickname:`` lane (single → resolved ``celex:``, multiple → ambiguous,
    # none → the STATUTE_ONLY coverage gap below).
    eu_fallback = _resolve_fi_name_via_eu(mention, name)
    if eu_fallback is not None:
        return eu_fallback
    # A genuine registry miss: the act is textual, the id is pending.
    return ResolvedReference(
        mention=mention,
        resolution_status=ResolutionStatus.STATUTE_ONLY,
        work_id=None,
        candidates=(),
        rejected_candidates=(),
        finding=None,
    )


# Provenance tag recorded on a mention resolved via the EU-nickname fallback (a
# Finnish-shaped ``fi-name:`` placeholder that missed the statute registry but
# names an EU instrument known to the EU-nickname registry).
_EU_FALLBACK_PHRASE_LEMMA = "eu_nickname_fallback_from_fi_name"


def _resolve_fi_name_via_eu(
    mention: ReferenceMention,
    name: str,
) -> Optional[ResolvedReference]:
    """Try resolving a STATUTE-missed ``fi-name:`` placeholder as an EU nickname.

    ``name`` is the normalized statute-name key (the ``fi-name:`` payload). It is
    looked up in the EU-nickname registry on the SAME normalized-head key the by-
    name lane mints (``sivutuoteasetus`` etc.). Returns:

    * a RESOLVED reference (target rewritten to ``celex:<CELEX>``) on a single EU
      candidate;
    * an AMBIGUOUS reference (a finding, no pick) on multiple EU candidates;
    * ``None`` when the name is unknown to the EU registry too — the caller then
      records the genuine STATUTE_ONLY coverage gap.

    Fail-loud: never invents a CELEX; a multi-CELEX nickname is always ambiguous.
    """
    result = eu_nickname.lookup(name)
    if result.registry_status is eu_nickname.RegistryStatus.NONE:
        return None
    candidate_ids = tuple(f"celex:{celex}" for celex in result.candidates)
    if result.registry_status is eu_nickname.RegistryStatus.SINGLE:
        work_id = candidate_ids[0]
        # A JURISDICTION FLIP: the by-name lane typed this ``fi-name:`` (a
        # Finnish-shaped ``-asetus`` head), and only the EU-nickname registry
        # recognizes it. Resolving it to an EU CELEX is a defensible best-effort
        # (the statute registry missed, the EU one hit), but it re-classifies the
        # instrument's jurisdiction on a nickname match — stamp APPROXIMATE, not
        # EXACT, so the flip is not laundered into the graph as a parsed-exact cite.
        return ResolvedReference(
            mention=_rewrite_target_id(
                mention,
                work_id,
                phrase_lemma=_EU_FALLBACK_PHRASE_LEMMA,
                cite_confidence=CiteConfidence.APPROXIMATE,
            ),
            resolution_status=ResolutionStatus.RESOLVED,
            work_id=work_id,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=None,
        )
    # MULTIPLE — a genuinely ambiguous EU nickname: list all, never pick.
    return ResolvedReference(
        mention=dataclasses.replace(mention, cite_confidence=CiteConfidence.AMBIGUOUS),
        resolution_status=ResolutionStatus.AMBIGUOUS,
        work_id=None,
        candidates=candidate_ids,
        rejected_candidates=(),
        finding=_ambiguity_finding(mention, candidate_ids),
    )


def _resolve_eu_nickname(mention: ReferenceMention) -> ResolvedReference:
    """Resolve an ``eu-nickname:<surface>`` placeholder against the EU registry."""
    target = mention.target_provision_ref
    assert target is not None
    surface = target.statute_id[len(_EU_NICKNAME_PREFIX) :]
    result = eu_nickname.lookup(surface)
    candidate_ids = tuple(f"celex:{celex}" for celex in result.candidates)

    if result.registry_status is eu_nickname.RegistryStatus.SINGLE:
        work_id = candidate_ids[0]
        return ResolvedReference(
            mention=_rewrite_target_id(mention, work_id),
            resolution_status=ResolutionStatus.RESOLVED,
            work_id=work_id,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=None,
        )
    if result.registry_status is eu_nickname.RegistryStatus.MULTIPLE:
        return ResolvedReference(
            mention=dataclasses.replace(
                mention, cite_confidence=CiteConfidence.AMBIGUOUS
            ),
            resolution_status=ResolutionStatus.AMBIGUOUS,
            work_id=None,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=_ambiguity_finding(mention, candidate_ids),
        )
    # NONE — nickname-shaped but unknown to the registry: id pending.
    return ResolvedReference(
        mention=mention,
        resolution_status=ResolutionStatus.STATUTE_ONLY,
        work_id=None,
        candidates=(),
        rejected_candidates=(),
        finding=None,
    )


def _passthrough(mention: ReferenceMention) -> ResolvedReference:
    """Project a non-placeholder mention with no registry call.

    OPEN (vague catch-all) -> ``status=open`` (targetless by construction).
    BROKEN -> ``status=broken``. Everything else (explicit id, internal,
    treaty) is already resolved upstream -> ``status=unchanged`` with the
    existing target id as ``work_id``.
    """
    conf = mention.cite_confidence
    if conf is CiteConfidence.OPEN:
        return ResolvedReference(
            mention=mention,
            resolution_status=ResolutionStatus.OPEN,
            work_id=None,
            candidates=(),
            rejected_candidates=(),
            finding=None,
        )
    if conf is CiteConfidence.BROKEN:
        return ResolvedReference(
            mention=mention,
            resolution_status=ResolutionStatus.BROKEN,
            work_id=None,
            candidates=(),
            rejected_candidates=(),
            finding=None,
        )
    target = mention.target_provision_ref
    work_id = target.statute_id if target is not None else None
    return ResolvedReference(
        mention=mention,
        resolution_status=ResolutionStatus.UNCHANGED,
        work_id=work_id,
        candidates=(work_id,) if work_id else (),
        rejected_candidates=(),
        finding=None,
    )


def resolve_mention(
    mention: ReferenceMention,
    *,
    statute_registry: StatuteNameRegistry,
    eu_registry: object = eu_nickname,
    as_of: Optional[dt.date] = None,
    defined_terms: Optional[DefinedTermTable] = None,
    name_id_anaphora: Optional[NameIdAnaphoraTable] = None,
    use_mention_validity: bool = False,
    disambiguate_multi_version: bool = True,
) -> ResolvedReference:
    """Resolve a single mention's placeholder identity against the registries.

    See :func:`resolve_mentions` for the routing contract. ``eu_registry`` is
    accepted for interface symmetry with the statute registry; the EU lookup is
    a module-level pure function (``eu_nickname.lookup``), so the default is the
    module itself and no per-call state is threaded. ``defined_terms`` (optional,
    default ``None``) is the per-statute local alias table consulted before the
    statute-name registry for ``fi-name:`` placeholders. ``name_id_anaphora``
    (optional, default ``None``) is the per-statute name->id anaphora table
    consulted after defined terms and before the registry (a bare repeat of an
    earlier id-anchored citation of the same name resolves to that id).

    ``use_mention_validity`` (default ``False``) selects the per-mention validity
    instant (this mention's ``valid_at_interval`` START) as the as-of filter when
    no explicit ``as_of`` is supplied; see :func:`resolve_mentions`.
    """
    del eu_registry  # the eu_nickname module's lookup is a pure function
    kind = _placeholder_kind(mention)
    if kind == _FI_NAME_PREFIX:
        effective_as_of = as_of
        if effective_as_of is None and use_mention_validity:
            effective_as_of = _mention_validity_as_of(mention)
        return _resolve_fi_name(
            mention,
            statute_registry,
            effective_as_of,
            defined_terms,
            name_id_anaphora,
            disambiguate_multi_version=disambiguate_multi_version,
        )
    if kind == _EU_NICKNAME_PREFIX:
        return _resolve_eu_nickname(mention)
    return _passthrough(mention)


def resolve_mentions(
    mentions: list[ReferenceMention],
    *,
    statute_registry: StatuteNameRegistry,
    eu_registry: object = eu_nickname,
    as_of: Optional[dt.date] = None,
    defined_terms: Optional[DefinedTermTable] = None,
    resolve_name_id_anaphora: bool = True,
    use_mention_validity: bool = False,
    disambiguate_multi_version: bool = True,
) -> list[ResolvedReference]:
    """Project placeholder mentions to :class:`ResolvedReference` records.

    Routing (per mention):

    * ``fi-name:<name>`` target -> look up in ``statute_registry``:
      single -> RESOLVED (placeholder rewritten to the real id in a NEW
      mention), multiple -> AMBIGUOUS (all candidates, a finding, no pick),
      none -> STATUTE_ONLY (registry miss = coverage gap, not a silent resolve).
    * ``eu-nickname:<surface>`` target -> same against the EU nickname registry
      (resolved id is ``celex:<CELEX>``).
    * already-resolved (explicit id / internal / treaty with a SopS id) ->
      UNCHANGED pass-through (no registry call), ``work_id`` = the existing id.
    * OPEN (vague) -> OPEN pass-through; BROKEN -> BROKEN pass-through.

    Fail-loud: never invents an id; >1 candidate is always AMBIGUOUS with every
    candidate listed; a registry miss is STATUTE_ONLY, never a silent RESOLVED.

    Args:
        mentions: The recognizer-emitted typed references to resolve.
        statute_registry: The built statute-name registry (Index B).
        eu_registry: The EU nickname registry module (default: the module).
        as_of: A SINGLE explicit validity instant applied to EVERY mention
            (static-as-of-citing). ``None`` resolves against the whole timeline
            (and is allowed to be AMBIGUOUS) unless ``use_mention_validity`` is
            set. An explicit ``as_of`` always overrides per-mention validity.
        defined_terms: Optional per-statute local alias table (built from the
            statute's :class:`DefinedTermBinding` records via
            :func:`build_defined_term_table`). When supplied, a ``fi-name:``
            placeholder that matches a local binding preceding the use resolves
            EXACT to the binding's target BEFORE the registry is consulted. Default
            ``None`` leaves every existing caller unaffected.
        resolve_name_id_anaphora: When ``True`` (default), an in-statute name->id
            anaphora table is built ONCE from this batch — every id-anchored
            citation (a concrete ``NUMBER/YEAR`` id with a distinctive statute-name
            head) binds that name -> that id at its byte offset. A bare ``fi-name:``
            repeat of the same name appearing AFTER the binding (and with no
            defined-term match) resolves to that id. A name bound to >1 distinct id
            stays AMBIGUOUS (dropped, never picked). Set ``False`` to disable.
        disambiguate_multi_version: When ``True``, a genuinely multi-version name
            (registry ``multiple``) that the as-of filter did NOT narrow is given a
            PRINCIPLED honest disambiguation before being reported AMBIGUOUS: the
            cited-head law/decree filter (a ``laki`` cite never resolves to an
            ``asetus`` act), then the as-of-live-version preference (exactly one
            candidate still in force). A single survivor RESOLVES with APPROXIMATE
            confidence (a best-effort pick among multiple, never a laundered EXACT)
            and the dropped candidates are listed in ``rejected_candidates``; when
            neither signal singles out one candidate the result stays AMBIGUOUS
            (fail-loud). Default ``False`` preserves the never-pick behaviour for
            every existing caller. This is a READ/PUBLISH recall lever (the citation
            graph / viewer), not a replay input.
        use_mention_validity: When ``True`` and no explicit ``as_of`` is given,
            resolve EACH mention against the START of its OWN ``valid_at_interval``
            — the version of the cited act in force WHILE that citing reference
            state held. A multi-version act name whose mention interval selects
            exactly one version then RESOLVES; a name whose interval still leaves
            >1 version, or whose interval start is ``None`` (open/unknown), stays
            AMBIGUOUS (fail-loud, no guess). Default ``False`` preserves the prior
            whole-timeline behaviour for every existing caller. The enactment year
            of the citing statute is intentionally NOT used (consolidated bodies
            legitimately cite post-enactment versions).

    Returns:
        One :class:`ResolvedReference` per input mention, in input order.
    """
    name_id_anaphora = (
        build_name_id_anaphora_table(mentions) if resolve_name_id_anaphora else None
    )
    return [
        resolve_mention(
            m,
            statute_registry=statute_registry,
            eu_registry=eu_registry,
            as_of=as_of,
            defined_terms=defined_terms,
            name_id_anaphora=name_id_anaphora,
            use_mention_validity=use_mention_validity,
            disambiguate_multi_version=disambiguate_multi_version,
        )
        for m in mentions
    ]


def build_default_registries(
    *,
    statute_sample_limit: int = 500,
    artifact_path: "str | Path | None" = None,
) -> tuple[StatuteNameRegistry, object]:
    """Build the default (statute_name, eu_nickname) registry pair.

    Prefers the PERSISTED FULL-CORPUS registry artifact (``artifact_path`` or
    :func:`default_artifact_path`): a jsonl of all ~59k titles, built offline by
    ``lawvm build-statute-name-registry``. Loading it is a cheap file read (no
    farchive walk at startup) and is what gives by-name resolution its real
    recall (full vs the 500-title sample is ~35% vs ~92% statute_only-miss).

    Fallback (artifact absent) is the SMALL sample of ``statute_sample_limit``
    titles — but the fallback is announced via :mod:`warnings`, never silent: a
    sample registry resolves a tiny fraction of by-name citations, so a caller
    must know it is running degraded rather than mistaking sample misses for
    genuine coverage gaps.

    Returns ``(statute_registry, eu_nickname_module)``.
    """
    path = Path(artifact_path) if artifact_path is not None else default_artifact_path()
    if path.exists():
        return load_statute_name_registry(path), eu_nickname
    warnings.warn(
        f"statute-name registry artifact not found at {path!s}; falling back to a "
        f"{statute_sample_limit}-title SAMPLE registry — by-name resolution recall "
        f"will be severely degraded. Build the full artifact with "
        f"`lawvm build-statute-name-registry`.",
        RuntimeWarning,
        stacklevel=2,
    )
    entries = sample_entries_from_farchive(limit=statute_sample_limit)
    statute_registry = build_registry(entries)
    return statute_registry, eu_nickname


__all__ = [
    "DefinedTermTable",
    "NameIdAnaphoraTable",
    "ResolutionStatus",
    "ResolvedReference",
    "StatuteSuccessorEdge",
    "SuccessorReferenceResolution",
    "SuccessorReferenceStatus",
    "build_default_registries",
    "build_defined_term_table",
    "build_name_id_anaphora_table",
    "resolve_mention",
    "resolve_mentions",
    "resolve_successor_reference",
]
