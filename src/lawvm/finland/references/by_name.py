"""Recognizer for cross-statute references made by inflected statute NAME.

Closes the ``[STATUTE_NAME_HEAD]`` recall family: cross-statute references that
name the target by its inflected *title* with **no** ``(NNN/YYYY)`` parenthetical
id, e.g.::

    luonnonsuojelulaissa säädetään ...
    ympäristönsuojelulain 5 §:ssä
    maankäyttö- ja rakennuslain 132 §:ssä

No existing lane emits these: the plain-text lane
(``ref_mention_extractor.PlainTextStatuteCitationRecognizer``) *requires* a
``(NNN/YYYY)`` id anchor, and the ``<ref>`` lane needs explicit markup. This
module recognises the *inflected name head* alone.

Design discipline
-----------------
* **M1-derived head detection.** A statute name is a compound whose trailing
  *head* (``laki`` / ``asetus`` / ``päätös`` ...) carries the inflection while
  the modifier prefix rides invariant. We ask the merged M1 morphology engine
  (``generate_forms``, READ-ONLY) for the oblique case surfaces of each closed
  statute head (``laissa``, ``lain``, ``asetuksen`` ...) and match a token that
  ENDS in one of them (longest-first, so ``asetuksessa`` is split on the whole
  inflected head and never on a coincidental shorter suffix). The nominative
  surface (``laki``) is deliberately NOT a trigger — an uninflected bare head is
  not a by-name *citation*.

* **Tag, don't guess (fail-loud id).** The recognizer's job is to TYPE the
  reference as an unresolved-by-name cross-statute ref. The act id is NOT
  resolved here (only the name surface); resolution to a real ``NNN/YYYY`` id is
  a later PROJECTION step against the statute-name registry (M2). We therefore
  emit ``cite_confidence=STATUTE_ONLY`` and carry the name in the target ref as
  ``statute_id="fi-name:<normalized_name>"`` — never a fabricated id.

* **Name normalization.** The normalized key reattaches the *nominative* head to
  the invariant modifier (``luonnonsuojelu`` + ``laki`` ->
  ``luonnonsuojelulaki``), folded to lower case. When the modifier cannot be
  recovered confidently the raw inflected surface key is carried instead — never
  an invented base.

* **No double-emission / no lane theft.** A name head immediately followed by a
  ``(NNN/YYYY)`` id is the id-anchored case owned by the plain-text lane —
  excluded here. A bare ``§`` tail with no name head (``5 §:ssä``) is an internal
  / other-lane reference — never emitted here (we only fire on a name head).

* **Structural tail reuse.** The ``§`` / momentti / kohta path after the name is
  parsed by the SHARED ``parse_body_provision_tail`` (body mode), so section
  ranges / coordination / momentti precision expand with the same expressiveness
  as the amendment grammar. One mention per expanded provision; one statute-level
  mention when there is no tail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)
from lawvm.finland.morphology import (
    MorphCase,
    MorphNumber,
    generate_forms,
    head_entry,
)
from lawvm.finland.references.registries.statute_name import _HEADS_BY_LEN
from lawvm.finland.references.sections import (
    BodyProvisionTarget,
    parse_body_provision_tail,
)


@dataclass(frozen=True, slots=True)
class _HeadForm:
    """One inflected statute-head surface and the data to normalize it.

    Attributes:
        oblique:    The inflected head surface, lower case (``laissa``).
        head_lemma: The closed-class head lemma (``laki``).
        nominative: The nominative head surface to reattach (``laki``).
    """

    oblique: str
    head_lemma: str
    nominative: str


def _build_head_forms() -> tuple[_HeadForm, ...]:
    """Derive the closed-class inflected-head trigger set from the M1 engine.

    For every closed statute head, generate its SG case forms and register every
    *oblique* surface (all cases except the nominative) as a trigger. The
    nominative surface is excluded: an uninflected bare head is not a by-name
    citation and would mis-fire on ordinary running text. The result is sorted
    longest-first so the modifier/head split is unambiguous (a token ending in
    ``asetuksessa`` splits on the whole inflected head, never a shorter
    coincidental suffix).
    """
    forms: list[_HeadForm] = []
    for head in _HEADS_BY_LEN:
        entry = head_entry(head)
        nom = ""
        obliques: list[str] = []
        for form in generate_forms(entry, numbers=(MorphNumber.SG,)):
            if form.certainty != "deterministic" or not form.surface:
                continue
            if form.case is MorphCase.NOM:
                nom = form.surface.lower()
                continue
            obliques.append(form.surface.lower())
        if not nom:
            continue
        for obl in obliques:
            forms.append(_HeadForm(oblique=obl, head_lemma=head, nominative=nom))
    # Dedup (distinct heads cannot share an oblique surface, but be safe) and
    # sort longest-first for unambiguous longest-match.
    seen: set[str] = set()
    uniq: list[_HeadForm] = []
    for f in sorted(forms, key=lambda f: len(f.oblique), reverse=True):
        if f.oblique in seen:
            continue
        seen.add(f.oblique)
        uniq.append(f)
    return tuple(uniq)


# Closed trigger set, built once at import time from the M1 engine.
_HEAD_FORMS: tuple[_HeadForm, ...] = _build_head_forms()
_HEAD_FORM_BY_OBLIQUE: dict[str, _HeadForm] = {f.oblique: f for f in _HEAD_FORMS}

# A name-head token: a run of name characters ending in a known oblique head
# surface. The leading part is the (possibly compound / coordinated) modifier;
# the trailing alternation is the closed inflected-head set (longest-first so the
# regex prefers the longest head surface). Bounded quantifier on the modifier
# (§1.11). The character class admits the coordinated-modifier hyphen
# (``maankäyttö-``) but the FULL coordinated phrase (``maankäyttö- ja
# rakennuslain``) is recovered by a separate left-extension scan below.
_NAME_CHAR = r"[A-Za-zÅÄÖåäö0-9-]"
_OBLIQUE_ALT = "|".join(re.escape(f.oblique) for f in _HEAD_FORMS)
_NAME_HEAD_RE = re.compile(
    rf"(?<![A-Za-zÅÄÖåäö0-9-])"  # word start (no preceding name char)
    rf"(?P<modifier>{_NAME_CHAR}{{0,80}}?)"
    rf"(?P<oblique>{_OBLIQUE_ALT})"
    rf"(?![A-Za-zÅÄÖåäö0-9])",  # word end (allow trailing hyphen? no)
    re.IGNORECASE,
)

# An id-anchored parenthetical ``(NNN/YYYY)`` immediately after the name head:
# that is the plain-text lane's case — exclude it here (no double-emission).
_ID_PAREN_RE = re.compile(r"\s{0,5}\(\s{0,3}\d{1,6}/\d{4}\s{0,3}\)")

# A coordinated left modifier fragment that elides its own head:
# ``maankäyttö- ja `` before ``rakennuslain``. We extend the matched modifier
# leftward over ``<word>- ja `` (and ``<word>- sekä ``) groups so the full name
# surface is reported. Bounded.
_COORD_LEFT_RE = re.compile(
    rf"(?:{_NAME_CHAR}{{1,80}}-\s+(?:ja|sekä|tai)\s+)+$",
    re.IGNORECASE,
)

# The window after the name head in which to look for a ``§`` / momentti tail.
# A citation tail is short; a bounded slice keeps the shared tail parser from
# scanning the rest of the paragraph.
_TAIL_WINDOW = 120

# ---------------------------------------------------------------------------
# Precision gate for weak (common-noun) heads and the ``laki`` elative.
# ---------------------------------------------------------------------------
#
# The bare name-head trigger fires on ANY token ending in an oblique statute-head
# surface. For the STRONG heads (``laki`` / ``asetus`` / ``direktiivi`` on a real
# capitalized/known modifier) this is mostly genuine. But the WEAK heads are
# productive ordinary common nouns whose oblique forms saturate running prose:
# ``vuokrasopimuksen`` (lease agreement), ``lupapäätöksen`` (permit decision),
# ``veroilmoituksen`` (tax return) — not act titles. A corpus diagnostic
# (``tools.resolution_miss_analysis``) attributes ~42% of by-name misses to these
# weak-head false positives plus the ``-alainen``/``-nainen`` adjective family.
#
# So weak heads (and the one ``laki`` form that collides with an adjective) only
# emit a cross-statute mention when there is POSITIVE EVIDENCE it is a real act
# reference:
#   * a following provision tail (``§`` / momentti) — a citation shape; or
#   * a capitalized modifier mid-sentence (a proper-name-ish title).
# Without either signal a weak-head common noun is not emitted (it is not a
# resolvable named act — emitting it is pure garbage, not a fail-loud residue).
_WEAK_HEADS: frozenset[str] = frozenset(
    {"sopimus", "päätös", "ilmoitus", "määräys", "ohje", "säädös"}
)

# The single ``laki`` oblique surface (elative ``laista``) that is orthographically
# identical to the partitive of the highly productive ``-lainen``/``-nainen``
# adjective family: ``sellaista`` ("such"), ``samanlaista`` ("similar"),
# ``veronalaista`` ("subject to tax"), ``alaista`` ("subordinate"). Every OTHER
# adjective form (``-laisessa``, ``-laisen``, ``-laisesta`` …) differs from every
# ``laki`` oblique and never fires; only ``laista`` collides. We therefore gate
# ``laista`` with the same positive-evidence requirement as the weak heads, AND
# reject outright any token that is an unambiguous ``-alainen``/``-nainen``
# adjective inflection (which is NEVER a ``laki``).
_LAKI_ADJ_COLLISION_OBLIQUE = "laista"

# Unambiguous ``-alainen``/``-nainen`` adjective inflections that the ``laista``
# trigger mis-segments as a ``laki`` elative. These are pure garbage: an
# ``-alainen`` adjective in the partitive (``veronalaista``, ``työnalaista``,
# ``valvonnanalaista``) is not a statute. The ``-lai`` digraph stem before the
# case ending (``…alaista``, ``…llaista``, ``…nlaista``) marks the derivational
# adjective suffix rather than the ``laki`` head. Matched against the WHOLE token
# (modifier + oblique), case-folded.
_ADJ_NOT_LAKI_RE = re.compile(
    r"(?:"
    r"[aeiouyäö]llaista"  # demonstrative -llainen: sellaista, tällaista, tuollaista
    r"|alaista"           # -alainen partitive: veronalaista, valvonnanalaista, alaista
    r"|nlaista"           # -nlainen partitive: samanlaista, vastaavanlaista
    r"|rilaista"          # erilaista, monenkirjavan- … (eri-/-ri stems)
    r"|kalaista"          # paikallaista etc. (rare; -kkalainen)
    r")$",
    re.IGNORECASE,
)

# A capitalized-modifier signal: the modifier's first character is an uppercase
# letter. Combined with a mid-sentence check (the match does not begin the text
# nor follow sentence-terminating punctuation), this is the proper-name-ish
# positive evidence for an otherwise-weak head.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?:;]\s*$")
_UPPER_FIRST_RE = re.compile(r"^[A-ZÅÄÖ]")


def _modifier_is_capitalized_midsentence(
    text: str, match_start: int, modifier: str
) -> bool:
    """True when ``modifier`` begins with a capital mid-sentence (proper-name-ish).

    A capitalized modifier that is NOT at the start of the text and NOT directly
    after sentence-terminating punctuation is positive evidence the token is a
    proper act title (``Kuntalain``, ``Hallintolain``) rather than a sentence-
    initial common noun. Sentence-initial capitalization is orthographic, not
    a title signal, so it does not count.
    """
    if not _UPPER_FIRST_RE.match(modifier):
        return False
    preceding = text[:match_start]
    if not preceding.strip():
        return False  # start of text — capitalization is positional, not a title
    if _SENTENCE_BOUNDARY_RE.search(preceding):
        return False  # sentence-initial — same
    return True


def _normalize_name(modifier: str, head_form: _HeadForm) -> str:
    """Build the normalized name key by reattaching the nominative head.

    ``modifier`` is the invariant prefix as matched (original casing); the
    nominative head surface is reattached and the whole folded to lower case
    (``luonnonsuojelu`` + ``laki`` -> ``luonnonsuojelulaki``). When the modifier
    is empty (a bare inflected head, e.g. ``lain``), the nominative head alone is
    returned (key ``laki``).
    """
    mod = " ".join(modifier.split())
    return (mod + head_form.nominative).lower()


def _extend_coordinated_modifier(text: str, match_start: int, modifier: str) -> str:
    """Reattach an elided-head coordinated left modifier to the name surface.

    Finnish coordinates statute names by eliding the shared head on the first
    conjunct: ``maankäyttö- ja rakennuslaki`` = ``maankäyttölaki`` +
    ``rakennuslaki``. The name-head regex only captures from the last conjunct
    (``rakennus`` + ``lain``); this scans the text immediately to the LEFT of the
    match for a ``<word>- ja `` chain and prepends it so the reported surface is
    the full coordinated name. The normalized key still reattaches the head to
    the LAST conjunct only (the head the inflection actually rides), per
    tag-don't-guess — we do not synthesize per-conjunct ids.
    """
    left = text[:match_start]
    m = _COORD_LEFT_RE.search(left)
    if m is None:
        return modifier
    return m.group(0) + modifier


def recognize_by_name_refs(text: str) -> list[ReferenceMention]:
    """Recognise inflected-statute-name cross-references in ``text``.

    For each inflected statute-name head NOT immediately followed by a
    ``(NNN/YYYY)`` id (that id-anchored case belongs to the plain-text lane),
    emit one :class:`ReferenceMention` per provision in the optional ``§`` tail
    (via :func:`parse_body_provision_tail`), or a single statute-level mention
    when there is no tail.

    Every mention is ``cite_kind=CROSS_STATUTE`` /
    ``cite_confidence=STATUTE_ONLY``: the act is named only by (inflected) title,
    so the concrete ``NNN/YYYY`` id is deferred to a later registry-resolution
    step. The name is carried, never an invented id, as
    ``target_provision_ref.statute_id = "fi-name:<normalized_name>"``.

    ``source_provision_ref`` is an empty placeholder; ``source_span`` is None
    (the integration step re-anchors the surface to a byte span, like the other
    surface-grammar lanes). ``surface_text`` carries the full matched name + tail.

    A bare ``§`` reference with no name head (``5 §:ssä``) is NOT emitted here —
    that is an internal / other-lane reference; this lane only fires on a name
    head.

    A BARE inflected head with no attached compound modifier (``tämän lain``,
    ``valtioneuvoston asetuksessa``) is NOT emitted: it is either an internal
    self-reference (``tämän lain``) or a generic governed instrument, not a
    resolvable named title. This lane requires a genuine compound title — a
    non-empty modifier glued to the head (``luonnonsuojelu`` + ``laissa``).
    """
    out: list[ReferenceMention] = []
    source_ref = ProvisionRef(statute_id="")

    for m in _NAME_HEAD_RE.finditer(text):
        oblique = m.group("oblique").lower()
        head_form = _HEAD_FORM_BY_OBLIQUE.get(oblique)
        if head_form is None:  # pragma: no cover - regex alt mirrors the dict
            continue

        # Require a genuine compound title: a non-empty modifier glued directly
        # to the inflected head. A bare head (``lain``, ``asetuksessa``) is the
        # internal self-reference (``tämän lain``) or a generic governed
        # instrument — not a resolvable named title; do not emit (tag-don't-guess
        # excludes the internal lane). Coordinated elided-head left conjuncts are
        # recovered separately and still attach to THIS head's modifier.
        if not m.group("modifier"):
            continue

        # Exclusion: an id-anchored ``(NNN/YYYY)`` right after the head is the
        # plain-text lane's case. Skip — no double-emission.
        if _ID_PAREN_RE.match(text, m.end()):
            continue

        # Hard reject the ``-alainen``/``-nainen`` adjective family. The partitive
        # ``-laista`` is orthographically identical to the ``laki`` elative
        # ``laista`` and the trigger mis-segments it, but an adjective in the
        # partitive (``sellaista``, ``veronalaista``) is NEVER a ``laki``. This is
        # not a fail-loud residue; it is a non-reference and must not be emitted.
        whole_token = m.group("modifier") + m.group("oblique")
        if _ADJ_NOT_LAKI_RE.search(whole_token):
            continue

        # Parse the optional structural tail (everything after the head) through
        # the shared body-mode section/sub-ref recognizers, bounded to a short
        # window so it does not scan the rest of the paragraph.
        tail_text = text[m.end() : m.end() + _TAIL_WINDOW]
        targets = parse_body_provision_tail(tail_text)
        has_provision_tail = bool(targets)

        # Precision gate for WEAK (common-noun) heads and the ``laki`` elative
        # that collides with the adjective partitive. These trigger on ordinary
        # compound nouns (``vuokrasopimuksen``, ``lupapäätöksen``), so require
        # POSITIVE EVIDENCE that the token is a real act reference: either a
        # following provision tail (a citation shape) or a capitalized modifier
        # mid-sentence (a proper-name-ish title). Strong heads (``laki`` in its
        # other forms, ``asetus``, ``direktiivi``) keep the looser behavior — the
        # false positives concentrate in the weak heads.
        needs_evidence = (
            head_form.head_lemma in _WEAK_HEADS
            or oblique == _LAKI_ADJ_COLLISION_OBLIQUE
        )
        if needs_evidence and not has_provision_tail:
            if not _modifier_is_capitalized_midsentence(
                text, m.start("modifier"), m.group("modifier")
            ):
                continue

        modifier = _extend_coordinated_modifier(text, m.start("modifier"), m.group("modifier"))
        normalized = _normalize_name(m.group("modifier"), head_form)

        if not targets:
            # No parsable § tail — a statute-level by-name reference.
            targets = [BodyProvisionTarget(section_label="")]

        # The reported surface spans the name head and (when present) its tail.
        name_surface = modifier + m.group("oblique")

        for tgt in targets:
            target_ref = ProvisionRef(
                statute_id=f"fi-name:{normalized}",
                section_label=tgt.section_label,
                subsection_num=tgt.subsection_num,
                item_label=tgt.item_label,
            )
            # Surface = name + the consumed tail slice (best-effort, for overlay
            # display). For the statute-level fallback it is just the name.
            if tgt.section_label:
                surface = (name_surface + " " + tail_text.strip()).strip()
            else:
                surface = name_surface
            out.append(
                ReferenceMention(
                    source_provision_ref=source_ref,
                    target_provision_ref=target_ref,
                    cite_kind=CiteKind.CROSS_STATUTE,
                    cite_confidence=CiteConfidence.STATUTE_ONLY,
                    phrase_lemma="statute_name_head",
                    source_span=None,
                    valid_at_interval=(None, None),
                    edge_subtype=None,
                    surface_text=surface,
                )
            )
    return out


__all__ = ["recognize_by_name_refs"]
