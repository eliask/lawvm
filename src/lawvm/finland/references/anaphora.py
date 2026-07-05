"""Anaphoric statute/provision reference resolution (the first DISCOURSE tier).

This lane owns the closed family of *anaphoric* references: a determiner +
reference head that points BACK at an antecedent named earlier in the same text,
rather than naming a target by id, by title, or by a self-anchored ``§`` path.

  - ``mainitun lain``          ("of the said act")
  - ``sanotun lain``           ("of the aforesaid act")
  - ``kyseisen lain``          ("of the act in question")
  - ``edellä mainitun lain``   ("of the aforementioned act")
  - ``mainitussa pykälässä``   ("in the said section")
  - ``sanotun pykälän``        ("of the said section")
  - ``tämän lain``             ("of THIS act" — the self-referential variant)

These carry no determinate target on their own surface. Their target is the
ANTECEDENT — the most recent concrete statute/provision reference that appears
EARLIER in the same text. That makes this a DISCOURSE-level resolution, unlike
the other recognizers (by-name, internal, vague), each of which decides a single
mention locally from its own surface.

Resolution rule (deterministic, left-to-right)
-----------------------------------------------
We scan the text once, left to right, interleaving two event kinds in document
order:

  1. CONCRETE antecedents — every reference produced by the by-name lane
     (:func:`recognize_by_name_refs`, a NAMED act) and by the internal lane
     (:func:`recognize_internal_refs`, a same-statute ``§`` / chapter path). As
     we pass each, we remember it as the most-recent antecedent OF ITS KIND
     (act-level vs section/provision-level).

  2. ANAPHORS — every determiner+head match from the closed list below. When an
     anaphor fires, we bind it to the most-recent antecedent whose KIND matches
     the anaphor head:
       * an ACT head (``lain``/``laissa``/``asetuksen`` …) binds to the most
         recent ACT-level antecedent (a by-name named act, or an act-scoped
         internal ref);
       * a PROVISION head (``pykälässä``/``pykälän``/``momentissa``/``kohdassa``
         …) binds to the most recent antecedent that carries a concrete
         provision path (a section / momentti / kohta).

Statuses (tag-don't-guess; never fabricate a target)
-----------------------------------------------------
  * ``resolved``  — exactly one most-recent antecedent of the matching kind was
    in scope. The bound target is THAT antecedent's target (its
    ``ProvisionRef``); confidence mirrors the antecedent
    (``EXACT`` for an internal path, ``STATUTE_ONLY`` for a by-name act).
  * ``ambiguous`` — two or more antecedents of the matching kind are EQUALLY the
    nearest (they sit at the same document position, i.e. the prior reference
    expanded to a coordination / range, so several candidate targets co-occur as
    "the most recent"). We list every candidate and pick NONE. The
    :class:`AnaphoricRef` is typed ``status=AMBIGUOUS`` and carries an
    :class:`AmbiguousReferenceFinding` naming every candidate. Because we pick no
    target, the emitted ``ReferenceMention`` carries
    ``target_provision_ref=None``; the ``ReferenceMention`` type only permits a
    None target under ``UNRESOLVED`` / ``BROKEN`` / ``OPEN`` confidence, so the
    mention itself is typed ``UNRESOLVED`` (target genuinely not resolved) while
    the discourse-level AMBIGUOUS verdict + candidate list live on the
    :class:`AnaphoricRef` and its finding (the catalogue convention for
    ambiguity-with-a-representative-target does not apply here — there is no
    single representative to carry).
  * ``open``      — no antecedent of the matching kind precedes the anaphor in
    this text. We do NOT guess: ``cite_confidence=OPEN``,
    ``target_provision_ref=None``. (Mirrors the vague-OPEN lane's discipline:
    an unresolvable discourse reference is tagged, never resolved.)

The single self-referential exception: ``tämän lain`` / ``tässä laissa`` ("THIS
act") binds to the CITING statute itself (``cite_kind=INTERNAL``,
``statute_id=<citing>``), independent of any preceding antecedent — exactly as
the internal lane treats the ``tämän lain`` demonstrative. When the citing
statute id is unknown (``statute_id=""``) we still resolve it as INTERNAL with an
empty-id self ProvisionRef (the integration step supplies the id), never OPEN —
"this act" is determinate by construction.

§1.11 hot-path regex discipline: the anaphor pattern is one alternation compiled
at module scope over a closed determiner+head set, bounded quantifiers, with a
cheap substring pre-guard before the scan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from lawvm.core.reference_mention import (
    AmbiguousReferenceFinding,
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)
from lawvm.finland.references.by_name import recognize_by_name_refs
from lawvm.finland.references.internal_refs import recognize_internal_refs


# ---------------------------------------------------------------------------
# Status + result types
# ---------------------------------------------------------------------------


class AnaphorStatus(Enum):
    """Resolution outcome for one anaphoric reference."""

    RESOLVED = "resolved"
    """Exactly one most-recent antecedent of the matching kind; bound to it."""

    AMBIGUOUS = "ambiguous"
    """Several equally-recent candidate antecedents; listed, none picked."""

    OPEN = "open"
    """No antecedent of the matching kind precedes the anaphor; tagged, unbound."""


@dataclass(frozen=True, slots=True)
class AnaphoricRef:
    """One recognized anaphoric reference and its resolution.

    Attributes:
        surface_text:  The matched anaphor surface (``mainitun lain``).
        char_offset:   0-based char offset of the anaphor in the input text.
        head_kind:     ANTECEDENT kind the head selects (ACT vs PROVISION).
        anaphor_status: RESOLVED / AMBIGUOUS / OPEN.
        mention:       The emitted :class:`ReferenceMention`. For RESOLVED it
                       carries the bound antecedent target; for AMBIGUOUS / OPEN
                       it carries ``target_provision_ref=None``.
        candidates:    For AMBIGUOUS, the tie-ing candidate targets; else empty.
        finding:       For AMBIGUOUS, the audit finding; else None.
    """

    surface_text: str
    char_offset: int
    head_kind: "_HeadKind"
    anaphor_status: AnaphorStatus
    mention: ReferenceMention
    candidates: Tuple[ProvisionRef, ...] = ()
    finding: Optional[AmbiguousReferenceFinding] = field(default=None)


# ---------------------------------------------------------------------------
# The closed anaphor list (NORMATIVE — determiner + reference head)
# ---------------------------------------------------------------------------
#
# An anaphor = an anaphoric DETERMINER (it points back, carries no target of its
# own) + a reference HEAD naming what kind of thing is referred back to.
#
# Determiners (closed):
#   mainitun / mainitussa / mainitulla / mainitusta / mainittuun  ("the said")
#   sanotun  / sanotussa  / sanotulla  / sanotusta                ("the aforesaid")
#   kyseisen / kyseisessä / kyseisellä / kyseisestä               ("in question")
#   edellä mainitun / edellä mainitussa …  ("the aforementioned" — fronted cue)
#   tämän / tässä / tällä / tästä / tähän  ("this" — the self-ref variant only)
#
# Heads (closed) split into two KINDS:
#   ACT       — lain / laissa / lakia / laista / lakiin / asetuksen / asetuksessa …
#   PROVISION — pykälän / pykälässä / momentin / momentissa / kohdan / kohdassa …


class _HeadKind(Enum):
    """Which kind of antecedent a reference head selects."""

    ACT = "act"
    PROVISION = "provision"


# Anaphoric determiners. ``tämän``-family ("this") is the SELF-referential variant
# (handled specially: binds to the citing statute, not a discourse antecedent).
_DETERMINER_ANAPHORIC: tuple[str, ...] = (
    "mainitun",
    "mainitussa",
    "mainitulla",
    "mainitusta",
    "mainittuun",
    "mainitut",
    "sanotun",
    "sanotussa",
    "sanotulla",
    "sanotusta",
    "kyseisen",
    "kyseisessä",
    "kyseisellä",
    "kyseisestä",
)
# Self-referential demonstratives ("this act") — same set the internal lane uses.
_DETERMINER_SELF: tuple[str, ...] = (
    "tämän",
    "tämä",
    "tässä",
    "tästä",
    "tähän",
    "tätä",
    "tällä",
)

# ACT-kind heads (a head that names an act/decree).
_HEAD_ACT: tuple[str, ...] = (
    "lain",
    "laissa",
    "lakia",
    "laista",
    "lakiin",
    "laiksi",
    "laille",
    "lailla",
    "lailta",
    "asetuksen",
    "asetuksessa",
    "asetusta",
    "asetuksesta",
    "asetukseen",
    "asetukseksi",
    "asetuksella",
    "asetukselle",
    "asetukselta",
)
# PROVISION-kind heads (a head that names a section / subsection / item).
_HEAD_PROVISION: tuple[str, ...] = (
    "pykälän",
    "pykälässä",
    "pykälää",
    "pykälästä",
    "pykälään",
    "momentin",
    "momentissa",
    "momenttia",
    "momentista",
    "momenttiin",
    "kohdan",
    "kohdassa",
    "kohtaa",
    "kohdasta",
    "kohtaan",
    "luvun",
    "luvussa",
    "luvusta",
    "lukuun",
)

_HEAD_KIND_BY_SURFACE: dict[str, _HeadKind] = {
    **{h: _HeadKind.ACT for h in _HEAD_ACT},
    **{h: _HeadKind.PROVISION for h in _HEAD_PROVISION},
}

# ---------------------------------------------------------------------------
# Compiled pattern (module scope — §1.11)
# ---------------------------------------------------------------------------
#
# An optional fronted ``edellä`` cue ("above/aforementioned"), then a determiner
# (anaphoric OR self), then the reference head. The cue is captured into the
# surface so ``edellä mainitun lain`` is reported whole. Whitespace between the
# parts may vary. The determiner and head alternations are longest-first only
# incidentally (fixed closed sets); the (?<![...]) / (?![...]) guards pin word
# boundaries on the Finnish letter class.
_LETTER = r"A-Za-zÅÄÖåäö"
_all_determiners = sorted(
    _DETERMINER_ANAPHORIC + _DETERMINER_SELF, key=len, reverse=True
)
_all_heads = sorted(_HEAD_ACT + _HEAD_PROVISION, key=len, reverse=True)
_DET_ALT = "|".join(re.escape(d) for d in _all_determiners)
_HEAD_ALT = "|".join(re.escape(h) for h in _all_heads)
_ANAPHOR_RE = re.compile(
    rf"(?<![{_LETTER}])"
    rf"(?P<front>edellä\s+)?"
    rf"(?P<det>{_DET_ALT})\s+"
    rf"(?P<head>{_HEAD_ALT})"
    rf"(?![{_LETTER}])",
    re.IGNORECASE,
)

#: Cheap substring pre-guards; if no determiner stem appears, no anaphor matches.
_ANAPHOR_GUARDS: tuple[str, ...] = (
    "mainit",
    "sanot",
    "kyseise",
    "tämä",
    "tässä",
    "tällä",
    "tästä",
    "tähän",
)


# ---------------------------------------------------------------------------
# Antecedent scan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Antecedent:
    """A concrete reference observed earlier in the text (a candidate target).

    Attributes:
        offset:    char offset of the antecedent surface in the text.
        kind:      whether it is an ACT-level or PROVISION-level reference.
        mention:   the concrete antecedent ReferenceMention (its target is the
                   bind target).
        offset_located: whether ``offset`` is a REAL located surface offset
                   (``True``) or the cursor fallback used when the surface could
                   not be found in the text (``False``). A fallback offset makes
                   document ordering (and therefore the bound antecedent) uncertain,
                   so a bind to such an antecedent is downgraded to APPROXIMATE
                   rather than inheriting the antecedent's EXACT confidence.
    """

    offset: int
    kind: _HeadKind
    mention: ReferenceMention
    offset_located: bool = True


def _antecedent_kind(mention: ReferenceMention) -> _HeadKind:
    """Classify a concrete reference as ACT-level or PROVISION-level.

    A reference that carries a concrete in-act provision path — a section label,
    a subsection (momentti), or an item (kohta) — is PROVISION-level. A reference
    that names only the act (no section path) is ACT-level. This mirrors the head
    KINDS: ``mainitussa pykälässä`` (PROVISION) binds to the nearest provision,
    ``mainitun lain`` (ACT) to the nearest act.
    """
    tgt = mention.target_provision_ref
    if tgt is not None and (
        tgt.section_label or tgt.subsection_num is not None or tgt.item_label is not None
    ):
        return _HeadKind.PROVISION
    return _HeadKind.ACT


def _locate_offset(text: str, surface: str, search_from: int) -> tuple[int, bool]:
    """Find the char offset of ``surface`` in ``text`` at or after ``search_from``.

    The by-name / internal lanes report ``source_span=None`` (the integration
    step re-anchors), so we recover document order by locating each antecedent's
    surface text. We search left-to-right from a running cursor so repeated
    surfaces map to successive occurrences in order.

    Returns ``(offset, located)``: ``located`` is ``True`` when the surface was
    really found (at/after the cursor, or on a whole-text re-search) and ``False``
    when it could not be located and ``search_from`` is returned as the cursor
    FALLBACK (keeps ordering monotone; never raises). A fallback offset makes
    document ordering unreliable, so the caller marks the antecedent as
    ``offset_located=False`` and any anaphor that binds to it is downgraded to
    APPROXIMATE (the wrong antecedent may have been picked — tag-don't-guess).
    """
    if not surface:
        return search_from, False
    idx = text.find(surface, search_from)
    if idx < 0:
        # Fall back to a fresh search from the start (the lanes may normalize
        # whitespace in the surface); if still not found, keep the cursor.
        idx = text.find(surface)
    if idx >= 0:
        return idx, True
    return search_from, False


def _collect_antecedents(text: str, statute_id: str) -> List[_Antecedent]:
    """Collect concrete by-name + internal references as ordered antecedents.

    Each concrete reference becomes an antecedent positioned at the char offset
    of its surface text, classified ACT vs PROVISION by :func:`_antecedent_kind`.
    Sorted by offset so the left-to-right scan sees them in document order.
    """
    antecedents: List[_Antecedent] = []

    cursor = 0
    for mention in recognize_by_name_refs(text):
        off, located = _locate_offset(text, mention.surface_text, cursor)
        cursor = max(cursor, off)
        antecedents.append(
            _Antecedent(off, _antecedent_kind(mention), mention, located)
        )

    cursor = 0
    for mention in recognize_internal_refs(text, statute_id):
        off, located = _locate_offset(text, mention.surface_text, cursor)
        cursor = max(cursor, off)
        antecedents.append(
            _Antecedent(off, _antecedent_kind(mention), mention, located)
        )

    antecedents.sort(key=lambda a: a.offset)
    return antecedents


def _nearest_of_kind(
    antecedents: List[_Antecedent], before_offset: int, kind: _HeadKind
) -> List[_Antecedent]:
    """Return the most-recent antecedent(s) of ``kind`` strictly before ``before_offset``.

    A PROVISION anaphor matches PROVISION-level antecedents only; an ACT anaphor
    matches ACT-level antecedents (and also accepts a PROVISION-level antecedent's
    act identity — see :func:`_act_target_of`). Returns ALL antecedents that tie
    at the single most-recent offset (a prior coordinated/ranged reference
    expands to several targets at one position → genuine ambiguity). Empty list
    when none precede.
    """
    candidates: List[_Antecedent] = []
    if kind is _HeadKind.PROVISION:
        candidates = [
            a for a in antecedents
            if a.offset < before_offset and a.kind is _HeadKind.PROVISION
        ]
    else:  # ACT — any preceding concrete reference fixes an act identity.
        candidates = [a for a in antecedents if a.offset < before_offset]
    if not candidates:
        return []
    best = max(a.offset for a in candidates)
    return [a for a in candidates if a.offset == best]


def _act_target_of(mention: ReferenceMention) -> ProvisionRef:
    """Project an antecedent's target onto the ACT level (drop the provision path).

    For ``mainitun lain`` the bind target is the antecedent ACT, even when the
    antecedent itself carried a section path (``ympäristönsuojelulain 5 §:ssä`` →
    ``mainitun lain`` = ymp.suojelulaki, act-level). Keeps the statute id, clears
    the in-act path.
    """
    tgt = mention.target_provision_ref
    statute_id = tgt.statute_id if tgt is not None else mention.source_provision_ref.statute_id
    return ProvisionRef(statute_id=statute_id)


# ---------------------------------------------------------------------------
# Mention builders
# ---------------------------------------------------------------------------


def _self_mention(statute_id: str, surface: str, offset: int) -> ReferenceMention:
    """Build the INTERNAL self-reference mention for ``tämän lain`` ("this act")."""
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id=statute_id),
        target_provision_ref=ProvisionRef(statute_id=statute_id),
        cite_kind=CiteKind.INTERNAL,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="anaphor_self_ref",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


def _downgrade_for_approximate(cite_confidence: CiteConfidence) -> CiteConfidence:
    """Lower an inherited antecedent confidence to APPROXIMATE, floor-preserving.

    Used when the bound antecedent's document position was a CURSOR FALLBACK
    (its surface could not be located), so the ordering — and therefore WHICH
    antecedent was bound — is uncertain. An EXACT (internal-path) inheritance is
    lowered to APPROXIMATE. A confidence already AT OR BELOW APPROXIMATE
    (STATUTE_ONLY for a by-name act, AMBIGUOUS, …) already flags the target as
    not-parsed-exact, so it is left as-is (never RAISED to APPROXIMATE).
    """
    if cite_confidence is CiteConfidence.EXACT:
        return CiteConfidence.APPROXIMATE
    return cite_confidence


def _resolved_mention(
    antecedent: ReferenceMention,
    target: ProvisionRef,
    surface: str,
    *,
    offset_located: bool = True,
) -> ReferenceMention:
    """Build a RESOLVED anaphor mention bound to an antecedent target.

    The mention inherits the antecedent's ``cite_kind`` and ``cite_confidence``
    (a by-name antecedent stays ``CROSS_STATUTE`` / ``STATUTE_ONLY``; an internal
    antecedent stays ``INTERNAL`` / ``EXACT``). The anaphor adds no new
    resolution confidence of its own — it merely co-refers.

    When ``offset_located`` is ``False`` the bound antecedent's document position
    was a cursor FALLBACK (its surface could not be located), so the ordering that
    selected it is unreliable; an EXACT inheritance is then downgraded to
    APPROXIMATE (:func:`_downgrade_for_approximate`) so a possibly-wrong bind is
    not laundered into the graph as an exact co-reference.
    """
    confidence = antecedent.cite_confidence
    if not offset_located:
        confidence = _downgrade_for_approximate(confidence)
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id=""),
        target_provision_ref=target,
        cite_kind=antecedent.cite_kind,
        cite_confidence=confidence,
        phrase_lemma="anaphor_ref",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


def _unbound_mention(surface: str, confidence: CiteConfidence) -> ReferenceMention:
    """Build an unbound anaphor mention (no target picked).

    Used for OPEN (no antecedent in scope → ``CiteConfidence.OPEN``) and for the
    AMBIGUOUS verdict (several equally-recent candidates → the mention is
    ``CiteConfidence.UNRESOLVED`` since a None target is type-legal only under
    UNRESOLVED / BROKEN / OPEN; the AMBIGUOUS verdict + candidates ride the
    :class:`AnaphoricRef`).
    """
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id=""),
        target_provision_ref=None,
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=confidence,
        phrase_lemma="anaphor_ref",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


# ---------------------------------------------------------------------------
# Public recognizer
# ---------------------------------------------------------------------------


def recognize_anaphoric_refs(
    text: str,
    statute_id: str = "",
) -> List[AnaphoricRef]:
    """Recognize and resolve closed-list anaphoric references in ``text``.

    For each anaphor (determiner + reference head from the closed list), resolve
    it to its ANTECEDENT — the nearest preceding concrete reference of the
    matching KIND — by a deterministic left-to-right scan. ``statute_id`` is the
    CITING statute (used to resolve the self-referential ``tämän lain`` to itself
    and to key internal-lane antecedents); it may be empty if unknown.

    Returns one :class:`AnaphoricRef` per anaphor in document order. Statuses:
      * RESOLVED   — one most-recent antecedent of the matching kind.
      * AMBIGUOUS  — several equally-recent candidate antecedents (listed,
                     none picked; ``cite_confidence=AMBIGUOUS``).
      * OPEN       — no antecedent of the matching kind in scope
                     (``cite_confidence=OPEN``; never fabricated).

    The self-referential ``tämän lain`` / ``tässä laissa`` ("this act") is always
    RESOLVED to the citing statute (INTERNAL), independent of any antecedent.
    """
    if not any(g in text.lower() for g in _ANAPHOR_GUARDS):
        return []

    antecedents = _collect_antecedents(text, statute_id)

    out: List[AnaphoricRef] = []
    for m in _ANAPHOR_RE.finditer(text):
        surface = m.group(0)
        offset = m.start()
        det = m.group("det").lower()
        head = m.group("head").lower()
        head_kind = _HEAD_KIND_BY_SURFACE.get(head, _HeadKind.ACT)

        # ── Self-referential "this act/section" → bind to the citing statute ──
        if det in _DETERMINER_SELF:
            mention = _self_mention(statute_id, surface, offset)
            out.append(
                AnaphoricRef(
                    surface_text=surface,
                    char_offset=offset,
                    head_kind=head_kind,
                    anaphor_status=AnaphorStatus.RESOLVED,
                    mention=mention,
                )
            )
            continue

        # ── Anaphoric determiner → bind to the nearest matching antecedent ──
        nearest = _nearest_of_kind(antecedents, offset, head_kind)

        if not nearest:
            out.append(
                AnaphoricRef(
                    surface_text=surface,
                    char_offset=offset,
                    head_kind=head_kind,
                    anaphor_status=AnaphorStatus.OPEN,
                    mention=_unbound_mention(surface, CiteConfidence.OPEN),
                )
            )
            continue

        # Build the candidate bind target(s). An ACT head projects each
        # candidate to its act identity; a PROVISION head keeps the full target.
        if head_kind is _HeadKind.ACT:
            targets = [_act_target_of(a.mention) for a in nearest]
        else:
            targets = [
                a.mention.target_provision_ref
                for a in nearest
                if a.mention.target_provision_ref is not None
            ]

        # Distinct candidate targets (a coordinated antecedent may expand to
        # several at the same offset). One distinct target → RESOLVED; more than
        # one → AMBIGUOUS (list, never pick).
        distinct: List[ProvisionRef] = []
        seen: set[str] = set()
        for t in targets:
            key = t.serialized()
            if key in seen:
                continue
            seen.add(key)
            distinct.append(t)

        if len(distinct) == 1:
            out.append(
                AnaphoricRef(
                    surface_text=surface,
                    char_offset=offset,
                    head_kind=head_kind,
                    anaphor_status=AnaphorStatus.RESOLVED,
                    mention=_resolved_mention(
                        nearest[0].mention,
                        distinct[0],
                        surface,
                        # A cursor-fallback offset on the bound antecedent makes the
                        # ordering (and the bind) uncertain -> downgrade EXACT to
                        # APPROXIMATE rather than laundering a possibly-wrong bind.
                        offset_located=all(a.offset_located for a in nearest),
                    ),
                )
            )
        else:
            finding = AmbiguousReferenceFinding(
                rule_id="fi.refs.anaphora.ambiguous_antecedent",
                phase="anaphora_resolution",
                source_statute_id=statute_id,
                source_provision_ref_str="",
                candidate_target_ids=tuple(t.serialized() for t in distinct),
                reason=(
                    f"anaphor {surface!r} has {len(distinct)} equally-recent "
                    "candidate antecedents; declining to pick (tag-don't-guess)"
                ),
            )
            out.append(
                AnaphoricRef(
                    surface_text=surface,
                    char_offset=offset,
                    head_kind=head_kind,
                    anaphor_status=AnaphorStatus.AMBIGUOUS,
                    # The ReferenceMention type forbids a None target under
                    # AMBIGUOUS confidence; the verdict + candidate list live on
                    # the AnaphoricRef/finding, so the mention itself is the
                    # genuinely-unresolved-because-multiple state (UNRESOLVED).
                    mention=_unbound_mention(surface, CiteConfidence.UNRESOLVED),
                    candidates=tuple(distinct),
                    finding=finding,
                )
            )

    return out


__all__ = [
    "AnaphorStatus",
    "AnaphoricRef",
    "recognize_anaphoric_refs",
]
