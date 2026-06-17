"""Internal (same-statute) bare section-reference recognition.

This lane owns the single largest reference-recall gap: BARE / INTERNAL section
references in body prose that point at a provision of the SAME statute, with no
statute identity of their own. These are the references the recall bench reports
under ``[SECTION]`` — tens of thousands of misses no other lane emits:

  - ``tämän lain 5 §:ssä``                 ("in § 5 of this act")
  - ``Edellä 1 ja 2 momentissa``           ("in subsections 1 and 2 above")
  - ``104 §:n 2 momentissa säädetään``     ("as provided in § 104(2)")
  - ``3 §:n 1 momentin 4 kohdassa``        ("in § 3(1)(4)")
  - ``108—110 §``                          (en-dash section range)
  - ``6 ja 8 §:ssä``                       (section coordination)

The structural tail (§ / momentti / kohta path, ranges, coordination) is parsed
by the SHARED body sub-ref grammar via
:func:`lawvm.finland.references.sections.parse_body_provision_tail` (§-anchored
shapes) and :func:`...grammar.subref.recognize_sub_refs` in ``body`` mode
(the bare-momentti/kohta shapes that carry no §). This lane adds ONLY the
recognizer that finds the citation site in prose, decides it is INTERNAL (not a
cross-statute case some other lane owns), and lifts each resolved provision to a
``ReferenceMention`` targeting the same statute.

LANE BOUNDARY (no double-emission):
  A bare § reference is INTERNAL unless it is owned by another lane:
    - preceded by a statute id ``(NNN/YYYY)``   → ``extract_plain_text_statute_mentions``
    - preceded by an inflected statute-NAME head → the cross-statute by-name lane
        (``…lain`` / ``…laissa`` / ``…asetuksen`` …)
  Those cases are EXCLUDED here. The one subtlety: a name-suffix word preceded by
  a self-referential demonstrative (``tämän lain`` / ``tässä laissa``) means
  "this act" → INTERNAL → ours. The vague catch-all (``muussa laissa``) is owned
  by the vague-OPEN lane and never produces a § path, so it never reaches here.

FAIL-LOUD (AGENTS.md §1.1): a bare reference is NEVER silently widened to the
whole statute. A trigger that fires but parses no concrete provision path is
dropped (we prefer not-emitting over guessing); only the rare case where a § is
present but no tail parses yields a STATUTE_ONLY section-less self-reference.
A cross-statute case is never emitted from this lane.

§1.11 hot-path regex discipline: patterns compiled at module scope, bounded
quantifiers, substring guards before the scan.
"""
from __future__ import annotations

import re
from typing import List, Optional

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)
from lawvm.finland.johtolause.grammar import sections as _sections  # noqa: F401  (load order: break subref circular import)
from lawvm.finland.johtolause.grammar.subref import recognize_sub_refs
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.references.sections import (
    BodyProvisionTarget,
    parse_body_provision_tail,
)

# ---------------------------------------------------------------------------
# Substring guards (fast path — eliminate non-matching paragraphs cheaply)
# ---------------------------------------------------------------------------
_GUARD_SECTION = "\xa7"  # §
_GUARD_MOMENTTI = "moment"  # momentissa / momentin / momenteissa cue
# Chapter-cue guard: the inflected ``luku`` stem appears as ``luvu…`` (genitive
# luvun, inessive luvussa, plural luvut/luvuissa) or as ``luku…`` (nominative
# luku, illative lukuun). Either stem is a cheap presence check for the chapter
# passes; both are substrings of ``lu``, so the guard is just that prefix.
_GUARD_CHAPTER = "lu"

# ---------------------------------------------------------------------------
# Section-citation surface recognizer (§-anchored shapes)
# ---------------------------------------------------------------------------
#
# Captures the FULL citation surface from the leading section number run through
# the § and its momentti/kohta tail, so the captured text can be fed verbatim to
# the shared body tail parser (no trailing prose). Bounded quantifiers only.
#
#   section label:  \d{1,6}[a-z]?      (e.g. 7, 7a, 104)
#   number run:     label (sep label)* with ,/ja/sekä/tai/en-dash joiners
#   §:             § optionally with an inflection suffix (§:ssä, §:n, §:ää)
#   tail step:      <number run> momentti|momentin|kohta|kohdassa   (repeatable)
#
_SEC_LABEL = r"\d{1,6}[a-zA-Z]?"
_SEP = r"(?:,|ja|sekä|tai|[–—-])"
_NUM_RUN = rf"{_SEC_LABEL}(?:\s*{_SEP}\s*{_SEC_LABEL})*"
_TAIL_NOUN = r"(?:moment\w+|kohda\w+|kohta)"

_SECTION_SURFACE_RE = re.compile(
    rf"""
    (?P<surf>
        {_NUM_RUN}
        \s*§(?::[a-zäöå]+)?
        (?:\s+{_NUM_RUN}\s+{_TAIL_NOUN})*
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Chapter prefix (``N luvun`` / ``N luku`` / ``N luvussa`` …) that qualifies a
# following section reference: ``3 luvun 5 §``, ``2 luvun 4 §:n 1 momentti``,
# coordinated ``3 ja 4 luvun 5 §``. The chapter number run reuses ``_NUM_RUN``
# (so ``ja`` / ``sekä`` / ranges coordinate). The ``luku`` head carries any
# Finnish case suffix (``luvun`` genitive, ``luvussa`` inessive, ``luku`` nom,
# ``luvut`` plural, ``lukuun`` …). Matched as a trailing prefix on the context
# BEFORE a section surface; the captured chapter run is applied to every target
# the following §-tail expands to.
_CHAPTER_HEAD = r"(?:luvun|luvussa|luvusta|lukuun|luvut|luvuissa|luku)"
_CHAPTER_PREFIX_RE = re.compile(
    rf"(?P<chnums>{_NUM_RUN})\s+{_CHAPTER_HEAD}\s*$",
    re.IGNORECASE,
)

# Chapter-only reference with NO following section: ``2 luvun säännöksiä``,
# ``3 luvussa tarkoitettu``. The chapter is concrete (a number run) but no §
# follows — emit a chapter-scoped internal mention (STATUTE_ONLY confidence:
# act is fixed = this statute, chapter is known, section deferred — never
# widened to the whole statute). A bare ``luku`` head with no number is NOT
# matched (fail-loud: no chapter to record → no emission).
_CHAPTER_ONLY_RE = re.compile(
    rf"\b(?P<chnums>{_NUM_RUN})\s+{_CHAPTER_HEAD}\b",
    re.IGNORECASE,
)

# Bare-momentti / bare-kohta surface (NO §), only after an internal lead-in cue
# (``Edellä`` / ``jäljempänä``). ``Edellä 1 ja 2 momentissa`` names subsections
# of the section currently being read — an internal self-reference with no § of
# its own. Without the cue, a bare ``N momentissa`` is too ambiguous to claim.
#
#   lead-in:   Edellä | Jäljempänä (case-insensitive, word-boundary)
#   body:      <number run> momentissa|momenteissa|kohdassa
_BARE_LEADIN = r"(?:edell\xe4|j\xe4ljemp\xe4n\xe4)"
_BARE_NOUN = r"(?:moment(?:issa|eissa|in)|kohda(?:ssa|n)|kohta)"
_BARE_SUBREF_RE = re.compile(
    rf"""
    \b(?P<leadin>{_BARE_LEADIN})\s+
    (?P<surf>{_NUM_RUN}\s+{_BARE_NOUN})
    """,
    re.VERBOSE | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Exclusion recognizers on the PRECEDING context (lane boundary)
# ---------------------------------------------------------------------------
#
# A statute-id parenthetical immediately before the citation → plain-text by-id
# lane owns it.  e.g. ``(123/2020) 5 §``.
_PRECEDING_STATUTE_ID_RE = re.compile(
    r"\(\s*\d{1,6}/\d{4}\s*\)\s*$",
)

# An inflected statute-NAME head immediately before the citation → cross-statute
# by-name lane owns it.  The trailing word ends in a Finnish law/decree case
# suffix (``…lain``, ``…laissa``, ``…asetuksen``, …). Captures the (optional)
# word before it so a self-referential demonstrative can be detected.
_NAME_SUFFIX = (
    r"(?:lain|lakia|laissa|laista|laiksi|laille|lailla|lailta|laki"
    r"|asetuksen|asetusta|asetuksessa|asetuksesta|asetukseksi"
    r"|asetuksella|asetukselle|asetukselta|asetus)"
)
_PRECEDING_NAME_HEAD_RE = re.compile(
    rf"(?P<prev>[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5]+)?\s*"
    rf"(?P<head>[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5\-]*{_NAME_SUFFIX})\s*$",
    re.IGNORECASE,
)

# A self-referential demonstrative makes ``… lain / … laissa`` mean "THIS act" →
# INTERNAL → ours (overrides the name-head exclusion). ``tämän lain``,
# ``tässä laissa``, ``tähän lakiin`` …
_SELF_DEMONSTRATIVES = frozenset(
    {
        "t\xe4m\xe4n",
        "t\xe4m\xe4",
        "t\xe4ss\xe4",
        "t\xe4st\xe4",
        "t\xe4h\xe4n",
        "t\xe4t\xe4",
        "t\xe4ll\xe4",
    }
)

# How far back to look for a preceding name head / statute id.
_LOOKBACK = 80


def _strip_chapter_prefix(before: str) -> tuple[str, Optional[str]]:
    """Split a trailing ``N luvun`` chapter prefix off the preceding context.

    Returns ``(before_without_chapter, chapter_run)`` where ``chapter_run`` is
    the raw number run of the chapter (e.g. ``"3"`` or ``"3 ja 4"``), or
    ``(before, None)`` when no chapter prefix is present. Used both to recover
    the chapter context for an internal ref and so the exclusion check can see
    PAST the chapter prefix to a statute-name head (``jätelain 3 luvun 5 §`` is a
    cross-statute case — the name head is one ``luvun`` token further back).
    """
    cm = _CHAPTER_PREFIX_RE.search(before)
    if cm is None:
        return before, None
    return before[: cm.start()], cm.group("chnums")


def _preceding_chapter_match(text: str, start: int) -> Optional[re.Match[str]]:
    """Return the ``N luvun`` chapter-prefix match ending just before ``start``.

    Looks at the bounded window before the section surface at ``start``. The
    chapter prefix must be ADJACENT to the section (only whitespace between the
    ``luvun`` head and the section number) — a chapter named further away is not
    a qualifier for this section. ``None`` when no chapter prefix abuts.
    """
    before = text[max(0, start - _LOOKBACK) : start]
    cm = _CHAPTER_PREFIX_RE.search(before)
    return cm


def _is_excluded(text: str, start: int) -> bool:
    """True iff the citation at ``start`` is owned by another (cross-statute) lane.

    Excludes a citation preceded by a statute id ``(NNN/YYYY)`` (plain-text
    by-id lane) or by an inflected statute-NAME head (cross-statute by-name
    lane). A name head preceded by a self-referential demonstrative
    (``tämän lain`` / ``tässä laissa`` = "this act") is NOT excluded — it is an
    internal self-reference and therefore ours.

    An intervening ``N luvun`` chapter prefix is transparent to this check: a
    statute-name head one ``luvun`` token further back (``jätelain 3 luvun 5 §``)
    still owns the citation via the cross-statute by-name lane.
    """
    before = text[max(0, start - _LOOKBACK) : start]
    # Look PAST a chapter prefix so a name head before it still excludes
    # (``jätelain 3 luvun 5 §`` is cross-statute, not internal).
    before, _chapter = _strip_chapter_prefix(before)
    if _PRECEDING_STATUTE_ID_RE.search(before):
        return True
    m = _PRECEDING_NAME_HEAD_RE.search(before)
    if m is not None:
        # A name head (``…lain`` / ``…laissa`` / ``…asetuksen`` …) immediately
        # before the citation is a cross-statute by-name case — EXCLUDED —
        # UNLESS a self-referential demonstrative makes it "THIS act"
        # (``tämän lain`` / ``tässä laissa``), which is an internal self-ref.
        prev = (m.group("prev") or "").lower()
        if prev in _SELF_DEMONSTRATIVES:
            return False
        return True
    return False


# ---------------------------------------------------------------------------
# Mention construction
# ---------------------------------------------------------------------------


# Split a chapter number run on COORDINATION joiners only (``ja`` / ``sekä`` /
# ``tai`` / comma) — NOT on dashes, which denote an inclusive range handled
# per-piece by ``_CHAPTER_RANGE_RE`` below.
_CHAPTER_COORD_SEP = r"(?:,|\bja\b|\bsek\xe4\b|\btai\b)"
_CHAPTER_SPLIT_RE = re.compile(rf"\s*{_CHAPTER_COORD_SEP}\s*", re.IGNORECASE)
# A single chapter range like ``3–5`` (en-dash / hyphen between two numbers).
_CHAPTER_RANGE_RE = re.compile(
    rf"^(?P<lo>{_SEC_LABEL})\s*[–—-]\s*(?P<hi>{_SEC_LABEL})$",
)


def _expand_chapter_run(chnums: str) -> List[str]:
    """Expand a chapter number run into individual chapter labels.

    ``"3"`` → ``["3"]``; ``"3 ja 4"`` → ``["3", "4"]``; ``"3–5"`` → ``["3","4","5"]``.
    Bare numeric chapters expand inclusively across a range; non-numeric labels
    (rare for chapters) are kept verbatim. Returns an empty list if nothing
    numeric parses (fail-loud — the caller then declines).
    """
    out: List[str] = []
    for piece in _CHAPTER_SPLIT_RE.split(chnums.strip()):
        piece = piece.strip()
        if not piece:
            continue
        rm = _CHAPTER_RANGE_RE.match(piece)
        if rm is not None and rm.group("lo").isdigit() and rm.group("hi").isdigit():
            lo, hi = int(rm.group("lo")), int(rm.group("hi"))
            if lo <= hi:
                out.extend(str(n) for n in range(lo, hi + 1))
                continue
        out.append(piece)
    return out


def _chapter_akn_path(
    chapter: str,
    *,
    section_label: str = "",
    subsection_num: Optional[int] = None,
    item_label: Optional[str] = None,
) -> str:
    """Build the AKN provision-path fragment carrying the chapter context.

    ProvisionRef has no first-class chapter field, so the chapter is encoded in
    ``provision_path`` using the SAME ``chp_N__sec_M__subsec_K__para_L`` AKN eId
    form the rest of the codebase uses (``core.locator``,
    ``finland.section_resolver``, ``references.interlinks`` all parse this with
    the ``__`` separator). The deeper components are appended so the interlink
    adapter — which prefers ``provision_path`` when it contains ``__`` — keeps
    the section/subsection/item rather than collapsing to chapter-only.
    """
    parts = [f"chp_{chapter}"]
    if section_label:
        parts.append(f"sec_{section_label}")
        if subsection_num is not None:
            parts.append(f"subsec_{subsection_num}")
            if item_label:
                parts.append(f"para_{item_label}")
    return "__".join(parts)


def _chapter_head_is_excluded(text: str, start: int) -> bool:
    """True iff a chapter-only ref at ``start`` is a cross-statute case.

    ``start`` is the offset of the chapter NUMBER run. A statute id ``(NNN/YYYY)``
    or an inflected statute-NAME head immediately before it (``jätelain 3
    luvussa`` / ``(123/2020) 3 luvussa``) means the chapter belongs to another
    act — owned by the cross-statute lanes — UNLESS a self-referential
    demonstrative makes the name head "this act". Reuses the same boundary
    logic as :func:`_is_excluded` (the section number plays the role the §
    surface plays there).
    """
    return _is_excluded(text, start)


def _target_to_ref(
    statute_id: str,
    tgt: BodyProvisionTarget,
    chapter: Optional[str] = None,
) -> ProvisionRef:
    provision_path = (
        _chapter_akn_path(
            chapter,
            section_label=tgt.section_label,
            subsection_num=tgt.subsection_num,
            item_label=tgt.item_label,
        )
        if chapter is not None
        else ""
    )
    return ProvisionRef(
        statute_id=statute_id,
        provision_path=provision_path,
        section_label=tgt.section_label,
        subsection_num=tgt.subsection_num,
        item_label=tgt.item_label,
    )


def _make_mention(
    statute_id: str,
    surface: str,
    target_ref: ProvisionRef,
    confidence: CiteConfidence,
) -> ReferenceMention:
    """Build one INTERNAL ReferenceMention (span re-anchored downstream)."""
    src_ref = ProvisionRef(statute_id=statute_id, provision_path="", section_label="")
    return ReferenceMention(
        source_provision_ref=src_ref,
        target_provision_ref=target_ref,
        cite_kind=CiteKind.INTERNAL,
        cite_confidence=confidence,
        phrase_lemma="internal_section_ref",
        source_span=None,  # the document-level integration re-anchors the span
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


def recognize_internal_refs(
    text: str,
    statute_id: str,
) -> List[ReferenceMention]:
    """Recognize bare / internal same-statute section references in ``text``.

    Emits one :class:`ReferenceMention` per resolved provision, all targeting
    ``statute_id`` itself (``cite_kind=INTERNAL``).

    DOES emit: ``tämän lain N §…``, ``Edellä N momentissa``, bare ``N §:ssä`` /
    ``N §:n M momentissa`` / ``N §:n M momentin K kohdassa``, en-dash ranges and
    coordination — one mention per expanded provision. Chapter-qualified shapes
    (``3 luvun 5 §``, ``2 luvun 4 §:n 1 momentti``, coordinated
    ``3 ja 4 luvun 5 §``) carry the chapter in ``provision_path`` as the AKN
    ``chp_N__sec_M…`` eId form. A chapter with no section (``3 luvussa``,
    ``2 luvun säännöksiä``) emits a chapter-scoped STATUTE_ONLY mention
    (``provision_path="chp_N"``, section deferred — never widened).

    Does NOT emit: any reference preceded by a statute id ``(NNN/YYYY)`` or an
    inflected statute-NAME head (those are owned by the plain-text-by-id and
    by-name cross-statute lanes). Never widens a bare reference to "whole
    statute"; never emits a cross-statute case.

    ``cite_confidence`` is EXACT when a concrete provision path parses;
    STATUTE_ONLY only in the rare case where the § trigger fired but no path
    parsed. A trigger that yields no provision at all is dropped (prefer
    not-emitting over guessing).
    """
    if not text:
        return []
    lower = text.lower()
    has_section = _GUARD_SECTION in text
    has_momentti = _GUARD_MOMENTTI in lower
    has_chapter = _GUARD_CHAPTER in lower
    if not has_section and not has_momentti and not has_chapter:
        return []

    mentions: List[ReferenceMention] = []
    # Track byte/char spans already consumed by the §-anchored pass so the
    # bare-momentti pass does not re-emit a momentti that belongs to a § cite.
    consumed: List[tuple[int, int]] = []
    # Chapter-prefix spans already attached to a § citation; the chapter-only
    # pass must not re-emit these as standalone chapter references.
    consumed_chapters: List[tuple[int, int]] = []

    # ── §-anchored shapes (the common case) ────────────────────────────────
    if has_section:
        for m in _SECTION_SURFACE_RE.finditer(text):
            if _is_excluded(text, m.start()):
                continue
            surface = m.group("surf")
            # A ``N luvun`` chapter prefix immediately before the section surface
            # qualifies it (``3 luvun 5 §`` → chapter 3, section 5). Recover the
            # chapter run and consume its span so the chapter-only pass does not
            # re-emit it as a bare chapter reference.
            win_start = max(0, m.start() - _LOOKBACK)
            cm = _preceding_chapter_match(text, m.start())
            chapters = _expand_chapter_run(cm.group("chnums")) if cm is not None else []
            if cm is not None:
                consumed_chapters.append(
                    (win_start + cm.start(), win_start + cm.end())
                )
            targets = parse_body_provision_tail(surface)
            if not targets:
                # § present but no parsable path: STATUTE_ONLY section-less
                # self-reference (act is fixed = this statute; provision
                # deferred, never widened). Rare; only when the recognizer
                # captured a § with no resolvable section label.
                mentions.append(
                    _make_mention(
                        statute_id,
                        surface,
                        ProvisionRef(statute_id=statute_id),
                        CiteConfidence.STATUTE_ONLY,
                    )
                )
                consumed.append((m.start(), m.end()))
                continue
            chapter_choices: List[Optional[str]] = list(chapters) or [None]
            for chapter in chapter_choices:
                for tgt in targets:
                    mentions.append(
                        _make_mention(
                            statute_id,
                            surface,
                            _target_to_ref(statute_id, tgt, chapter),
                            CiteConfidence.EXACT,
                        )
                    )
            consumed.append((m.start(), m.end()))

    # ── bare-momentti / bare-kohta shapes (no §; internal lead-in only) ─────
    if has_momentti:
        for m in _BARE_SUBREF_RE.finditer(text):
            if _is_excluded(text, m.start("leadin")):
                continue
            # Skip if this momentti run sits inside a § citation already taken.
            if any(s <= m.start("surf") < e for s, e in consumed):
                continue
            surface = m.group("surf")
            subs = _bare_subref_targets(surface)
            if not subs:
                continue
            for sub in subs:
                mentions.append(
                    _make_mention(
                        statute_id,
                        surface,
                        _internal_bare_target(statute_id, sub),
                        CiteConfidence.EXACT,
                    )
                )

    # ── chapter-only shapes (``2 luvun säännöksiä``, ``3 luvussa``) ─────────
    # A concrete chapter with NO following section. The act is fixed (= this
    # statute), the chapter is known, the in-chapter provision is deferred →
    # STATUTE_ONLY (never widened to the whole statute). Skip any chapter
    # prefix already attached to a § citation above, and any cross-statute
    # case (``jätelain 3 luvussa``).
    if has_chapter:
        for m in _CHAPTER_ONLY_RE.finditer(text):
            if any(s <= m.start() < e for s, e in consumed_chapters):
                continue
            if _chapter_head_is_excluded(text, m.start()):
                continue
            for chapter in _expand_chapter_run(m.group("chnums")):
                mentions.append(
                    _make_mention(
                        statute_id,
                        m.group(0),
                        ProvisionRef(
                            statute_id=statute_id,
                            provision_path=_chapter_akn_path(chapter),
                        ),
                        CiteConfidence.STATUTE_ONLY,
                    )
                )

    return mentions


def _bare_subref_targets(surface: str) -> List[ProvisionRef]:
    """Parse a bare ``N momentissa`` / ``N kohdassa`` run (no §) to ProvisionRefs.

    Routes the surface through the shared body sub-ref recognizer in ``body``
    mode (which promotes the inessive ``momentissa`` to MOMENTTI). The section is
    NOT named by the surface — a bare momentti reference names a subsection of
    the section currently being read — so ``section_label`` is left empty and the
    momentti/kohta carries the precision. Returns an empty list when nothing
    parses (fail-loud: no guessed widening).
    """
    toks = tokenize(surface)
    subs, _end = recognize_sub_refs(toks, 0, mode="body")
    refs: List[ProvisionRef] = []
    for sub in subs:
        sub_ref = sub.to_provision_ref(statute_id="", section_label="")
        # Only emit when the sub-ref actually carries a subsection or item; a
        # facet-only / empty sub-ref names no concrete provision path.
        if sub_ref.subsection_num is None and sub_ref.item_label is None:
            continue
        # statute_id is empty here; the caller re-keys onto the internal statute
        # via _internal_bare_target before building the mention.
        refs.append(sub_ref)
    return refs


def _internal_bare_target(statute_id: str, ref: ProvisionRef) -> ProvisionRef:
    """Re-key a bare sub-ref ProvisionRef onto the internal statute id."""
    return ProvisionRef(
        statute_id=statute_id,
        provision_path="",
        section_label=ref.section_label,
        subsection_num=ref.subsection_num,
        item_label=ref.item_label,
    )
