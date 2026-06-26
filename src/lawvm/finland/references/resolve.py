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
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional

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
        status: The resolution outcome (:class:`ResolutionStatus`).
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
    status: ResolutionStatus
    work_id: Optional[str]
    candidates: tuple[str, ...]
    rejected_candidates: tuple[str, ...]
    finding: Optional[AmbiguousReferenceFinding]


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
            morphology_ok=(b.status == STATUS_OK),
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
) -> ReferenceMention:
    """Return a NEW mention with the target's statute_id rewritten to ``work_id``.

    The input mention is never mutated (frozen dataclasses, ``replace``). The
    cite_confidence is promoted to EXACT — the identity is now resolved to a
    single real id. ``phrase_lemma`` overrides the syntactic-class label on the
    rewritten mention when provided (used to record local-binding provenance).
    """
    target = mention.target_provision_ref
    assert target is not None  # guarded by caller
    new_target = dataclasses.replace(target, statute_id=work_id)
    changes: dict[str, object] = {
        "target_provision_ref": new_target,
        "cite_confidence": CiteConfidence.EXACT,
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
                status=ResolutionStatus.RESOLVED,
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
                status=ResolutionStatus.RESOLVED,
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
    if as_of is not None and result.registry_status == "none":
        unfiltered = statute_registry.lookup(name, None)
        if unfiltered.registry_status != "none":
            result = unfiltered

    candidate_ids = tuple(c.statute_id for c in result.candidates)

    if result.registry_status == "single":
        work_id = candidate_ids[0]
        return ResolvedReference(
            mention=_rewrite_target_id(mention, work_id),
            status=ResolutionStatus.RESOLVED,
            work_id=work_id,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=None,
        )
    if result.registry_status == "multiple":
        return ResolvedReference(
            mention=dataclasses.replace(
                mention, cite_confidence=CiteConfidence.AMBIGUOUS
            ),
            status=ResolutionStatus.AMBIGUOUS,
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
            return ResolvedReference(
                mention=_rewrite_target_id(
                    mention, cws_ids[0], phrase_lemma=_CWS_FALLBACK_PHRASE_LEMMA
                ),
                status=ResolutionStatus.RESOLVED,
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
            status=ResolutionStatus.AMBIGUOUS,
            work_id=None,
            candidates=cws_ids,
            rejected_candidates=(),
            finding=_ambiguity_finding(mention, cws_ids),
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
        status=ResolutionStatus.STATUTE_ONLY,
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
        return ResolvedReference(
            mention=_rewrite_target_id(
                mention, work_id, phrase_lemma=_EU_FALLBACK_PHRASE_LEMMA
            ),
            status=ResolutionStatus.RESOLVED,
            work_id=work_id,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=None,
        )
    # MULTIPLE — a genuinely ambiguous EU nickname: list all, never pick.
    return ResolvedReference(
        mention=dataclasses.replace(mention, cite_confidence=CiteConfidence.AMBIGUOUS),
        status=ResolutionStatus.AMBIGUOUS,
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
            status=ResolutionStatus.RESOLVED,
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
            status=ResolutionStatus.AMBIGUOUS,
            work_id=None,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=_ambiguity_finding(mention, candidate_ids),
        )
    # NONE — nickname-shaped but unknown to the registry: id pending.
    return ResolvedReference(
        mention=mention,
        status=ResolutionStatus.STATUTE_ONLY,
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
            status=ResolutionStatus.OPEN,
            work_id=None,
            candidates=(),
            rejected_candidates=(),
            finding=None,
        )
    if conf is CiteConfidence.BROKEN:
        return ResolvedReference(
            mention=mention,
            status=ResolutionStatus.BROKEN,
            work_id=None,
            candidates=(),
            rejected_candidates=(),
            finding=None,
        )
    target = mention.target_provision_ref
    work_id = target.statute_id if target is not None else None
    return ResolvedReference(
        mention=mention,
        status=ResolutionStatus.UNCHANGED,
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
    "build_default_registries",
    "build_defined_term_table",
    "build_name_id_anaphora_table",
    "resolve_mention",
    "resolve_mentions",
]
