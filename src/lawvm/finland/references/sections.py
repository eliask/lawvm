"""Body / cross-statute reference structural-tail parser.

The plain-text statute citation lane (``ref_mention_extractor``) anchors on a
statute-name head + ``(NUMBER/YEAR)`` parenthetical id, then must parse the
STRUCTURAL TAIL — the section / momentti / kohta path that follows the ``§``.
The bespoke single-match regex used to capture exactly one section + one
momentti + one kohta. That left it unable to model the expressiveness the
johtolause amendment grammar already has: en-dash section RANGES
(``108—110 §``), section COORDINATION (``6 ja 8 §``), and momentti
coordination (``1 ja 2 momentissa``, ``104 §:n 2 momentissa``).

This module routes the structural tail through the SHARED section/sub-ref
recognizers (``grammar.sections`` / ``grammar.subref``) in ``body`` mode, so the
body lane gets the same range/coordination/momentti precision as the amendment
lane — without perturbing amendment parsing (the body mode reclassification is
local and never touches the shared lexicon).

The output is architecture-neutral: a list of ``BodyProvisionTarget`` rows, one
per expanded section, each carrying its (optional) momentti / kohta. The caller
lifts each to a ``ReferenceMention`` with a full ``ProvisionRef``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from lawvm.finland.johtolause.grammar import sections as _sections
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.subref import (
    SubRef,
    _reclassify_body_tokens,
)
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.references.lemma_gate import chapter_head_alternation

# Whitespace normalization applied by ``tokenize`` before it assigns char
# offsets. ``parse_body_provision_tail_spanned`` re-applies it so the consumed
# slice it returns is sliced from the SAME (normalized) string the token
# ``char_end`` offsets index into.
_WS_RE = re.compile(r"\s+")

# Body references coordinate sections with the disjunctive ``tai`` ("or")
# (``114, 115 tai 155 §:n``) as routinely as with ``ja``/``sekä``. The shared
# johtolause lexicon deliberately classifies only ``ja``/``sekä``/``ynnä`` as
# CONJ — ``tai`` is left as a plain WORD because it carries a different meaning
# inside an AMENDMENT johtolause. In a BODY citation tail, however, a ``tai``
# between two section numbers is a pure coordination joiner with the same
# enumeration semantics as ``ja``, so we rewrite it to ``ja`` ONLY when it sits
# between two numeric tokens before handing the tail to the shared recognizer.
# The rewrite is local to this body lane (it never reaches the amendment
# parser) and is scoped to the numeric-coordination position so a ``tai`` in the
# trailing prose the parser ignores is never touched.
#
# The left operand may end in a section number (``115``) OR a number + letter
# suffix (``52 d``); the right operand opens with a number. The lookbehind
# tolerates a single suffix letter (and its space) after the digits so a
# suffixed list member (``52 d tai 52 e``) coordinates too. Operates on the
# WHITESPACE-NORMALIZED tail (single ASCII spaces only), so every gap is a bare
# literal space — no ``\s*`` repeats (keeps the regex backtracking-safe).
_TAI_NUMERIC_JOINER_RE = re.compile(
    r"(?<=\d)(?P<lsuf> [a-zA-Z])?(?P<pre> ?[–—-]? ?)tai(?P<post> ?)(?=\d)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Chapter prefix (``N luvun M §``) on a CROSS-STATUTE / body citation tail
# ---------------------------------------------------------------------------
#
# A body / cross-statute citation tail can open with a chapter qualifier before
# the section: ``9 luvun 9 b §`` (chapter 9, section 9b), ``13 luvun 3 §:ssä``,
# coordinated ``3 ja 4 luvun 5 §``, or a chapter with NO section at all
# (``5 luvussa``, ``20 luvussa``). The grammar's ``recognize_section_ref`` only
# consumes a GENITIVE ``N luvun`` head (and discards it); the inessive / plural
# forms (``luvussa``, ``luvut``) tokenize as plain WORDs, so neither form reaches
# a chapter-qualified target without help here.
#
# This lane therefore peels a leading ``N luvun`` chapter run off the tail BEFORE
# parsing the section path, mirroring the INTERNAL lane's chapter handling
# (``references.internal_refs._CHAPTER_PREFIX_RE`` / ``_strip_chapter_prefix``).
# The chapter run is carried onto every expanded target so the caller can build
# a chapter-qualified AKN path (``chp_9__sec_9b``) — never silently dropped. When
# the chapter is followed by NO section the tail yields one chapter-only target
# (``section_label=""``), so the caller can emit a chapter-scoped reference
# instead of dropping it. The chapter ``luku`` head carries any Finnish case
# suffix (genitive ``luvun``, inessive ``luvussa``, nominative ``luku`` …),
# matching the internal lane's ``_CHAPTER_HEAD``.
#
# The head surfaces are M1-GENERATED from the closed ``luku`` head (shared with
# the internal-ref lane via ``lemma_gate.chapter_head_alternation``), not a
# hand-typed paradigm table duplicated across lanes. Sound paradigm inversion,
# strict-equal superset of the old table; see ``lemma_gate``.
_CHAPTER_TAIL_HEAD = rf"(?:{chapter_head_alternation()})"
# The chapter number run reuses a number-list shape (coordination + ranges),
# kept local so this module needs no grammar import for the prefix scan. Bounded
# quantifiers only (§1.11): a single suffix letter, joiners are bounded literals.
_CH_LABEL = r"\d{1,4}(?:\s*[a-zA-Z])?"
_CH_SEP = r"(?:,|ja|sek\xe4|tai|[–—-])"
_CH_NUM_RUN = rf"{_CH_LABEL}(?:\s*{_CH_SEP}\s*{_CH_LABEL})*"
# Leading chapter prefix: ``9 luvun`` / ``5 luvussa`` / ``3 ja 4 luvun`` at the
# START of the (whitespace-normalized) tail, with the trailing whitespace that
# separates it from the section run (if any) consumed.
_CHAPTER_TAIL_PREFIX_RE = re.compile(
    rf"^(?P<chnums>{_CH_NUM_RUN})\s+{_CHAPTER_TAIL_HEAD}\b\s*",
    re.IGNORECASE,
)
# A clause separator between successive chapter clauses under one statute head
# (``… 18 §:ssä, 20 luvussa``): a comma and/or a coordinating joiner. Anchored
# with ``match`` at the offset just past the consumed text, so only a separator
# that DIRECTLY follows the consumed run is treated as a clause boundary; the
# outer loop's chapter-prefix requirement then decides whether a real chapter
# clause follows. Bounded literals only (§1.11).
_CLAUSE_SEP_RE = re.compile(
    r"\s*(?:,\s*)?(?:(?:ja|sek\xe4|tai)\s+)?",
    re.IGNORECASE,
)
# Chapter-run splitter / range / spaced-letter-suffix patterns (module scope per
# §1.11; bounded quantifiers only).
_CH_COORD_SPLIT_RE = re.compile(r"\s*(?:,|\bja\b|\bsek\xe4\b|\btai\b)\s*", re.IGNORECASE)
_CH_RANGE_RE = re.compile(r"^(\d{1,4})\s*[–—-]\s*(\d{1,4})$")
_CH_SPACED_SUFFIX_RE = re.compile(r"^(\d{1,4})\s+([a-zA-Z])$")


def _split_leading_chapter(tail_text: str) -> tuple[str, Optional[str]]:
    """Peel a leading ``N luvun`` chapter prefix off a body/cross-statute tail.

    Returns ``(tail_without_chapter, chapter_run)`` where ``chapter_run`` is the
    raw chapter number run (``"9"``, ``"3 ja 4"``), or ``(tail_text, None)`` when
    the tail does not open with a chapter prefix. Operates on the
    whitespace-normalized tail (single ASCII spaces). Mirrors the INTERNAL lane's
    ``_strip_chapter_prefix`` (which peels from the PRECEDING context) — here the
    chapter sits at the HEAD of the tail because the statute-name / id anchor was
    already consumed by the caller.
    """
    cm = _CHAPTER_TAIL_PREFIX_RE.match(tail_text)
    if cm is None:
        return tail_text, None
    return tail_text[cm.end():], cm.group("chnums")


def _expand_chapter_run(chnums: str) -> List[str]:
    """Expand a chapter number run into individual chapter labels.

    ``"9"`` → ``["9"]``; ``"3 ja 4"`` → ``["3", "4"]``; ``"3-5"`` →
    ``["3","4","5"]``. Bare numeric chapters expand inclusively across a range;
    a chapter with a letter suffix (``9a``) is kept verbatim. Mirrors the
    INTERNAL lane's ``_expand_chapter_run`` so both lanes enumerate identically.
    Returns an empty list if nothing parses (fail-loud — caller declines).
    """
    out: List[str] = []
    for piece in _CH_COORD_SPLIT_RE.split(chnums.strip()):
        piece = piece.strip()
        if not piece:
            continue
        rm = _CH_RANGE_RE.match(piece)
        if rm is not None:
            lo, hi = int(rm.group(1)), int(rm.group(2))
            if lo <= hi and hi - lo < 100:
                out.extend(str(n) for n in range(lo, hi + 1))
                continue
        # Normalize a spaced letter suffix to the glued AKN form (``9 a`` → ``9a``).
        out.append(_CH_SPACED_SUFFIX_RE.sub(r"\1\2", piece).replace(" ", ""))
    return out


def chapter_akn_path(chapter: str, section_label: str = "") -> str:
    """Build the chapter-qualified AKN provision-path fragment.

    Uses the SAME ``chp_N__sec_M`` AKN eId form the rest of the codebase parses
    (``core.locator``, ``finland.section_resolver``, ``references.interlinks``)
    and the SAME shape the INTERNAL lane emits
    (``references.internal_refs._chapter_akn_path``), so the graph-writer and the
    extractor stay consistent. A chapter-only reference (no section) yields just
    ``chp_N``.
    """
    parts = [f"chp_{chapter}"]
    if section_label:
        parts.append(f"sec_{section_label}")
    return "__".join(parts)


def _normalize_disjunctive_joiner(tail_text: str) -> str:
    """Rewrite a numeric-coordination ``tai`` to ``ja`` for the body tail parse.

    ``"114, 115 tai 155 §:n"`` → ``"114, 115 ja 155 §:n"``. Only a ``tai`` flanked
    by digits (the section-number coordination position) is rewritten; a ``tai``
    elsewhere in the tail (e.g. inside the trailing prose the parser discards) is
    left untouched. The shared section recognizer then enumerates the disjunctive
    list the same way as a ``ja`` list. The recorded surface, however, must keep
    the AUTHOR's original ``tai`` (see :func:`_unnormalize_consumed`), so this
    rewrite is for the PARSE only.
    """
    return _normalize_disjunctive_joiner_spanned(tail_text)[0]


def _normalize_disjunctive_joiner_spanned(tail_text: str) -> tuple[str, list[int]]:
    """As :func:`_normalize_disjunctive_joiner`, also reporting the shrink offsets.

    Returns ``(normalized, shrink_positions)`` where ``shrink_positions`` lists,
    in the NORMALIZED string's coordinate system, the char offset just AFTER each
    ``tai`` → ``ja`` rewrite (each rewrite is one char shorter). The list lets
    :func:`_unnormalize_consumed` map a consumed slice of the normalized tail back
    onto the whitespace-normalized-but-joiner-PRESERVED tail, so the recorded
    surface keeps the author's ``tai`` while the parse keeps the ``ja`` semantics.
    """
    if "tai" not in tail_text.lower():
        return tail_text, []
    out: list[str] = []
    shrinks: list[int] = []
    last = 0
    norm_len = 0
    for m in _TAI_NUMERIC_JOINER_RE.finditer(tail_text):
        out.append(tail_text[last : m.start()])
        norm_len += m.start() - last
        repl = f"{m.group('lsuf') or ''}{m.group('pre')}ja{m.group('post')}"
        out.append(repl)
        norm_len += len(repl)
        # ``tai`` (3) → ``ja`` (2): one char shorter; record the post-rewrite
        # boundary in the normalized coordinate system.
        shrinks.append(norm_len)
        last = m.end()
    out.append(tail_text[last:])
    return "".join(out), shrinks


def _unnormalize_consumed(
    consumed_text: str, ws_norm: str, shrinks: list[int]
) -> str:
    """Recover the author's original joiner in a consumed surface slice.

    ``consumed_text`` is a leading slice of the joiner-NORMALIZED tail (``tai`` →
    ``ja``). ``ws_norm`` is the whitespace-normalized tail with the joiner
    PRESERVED. ``shrinks`` are the per-rewrite shrink offsets (normalized
    coordinate system). The pre-image length is ``len(consumed_text)`` plus the
    number of rewrites that fall within it (each was one char shorter), so the
    matching slice of ``ws_norm`` restores the original ``tai``. Cosmetic only —
    target enumeration is unchanged.
    """
    if not consumed_text or not shrinks:
        return consumed_text
    extra = sum(1 for s in shrinks if s <= len(consumed_text))
    return ws_norm[: len(consumed_text) + extra].rstrip()


@dataclass(frozen=True)
class BodyProvisionTarget:
    """One expanded provision target from a body reference structural tail.

    Attributes:
        section_label:  Section label, e.g. "7", "7a", "108". Empty for a
                        chapter-only reference (``5 luvussa`` — a chapter is
                        named but no §; the in-chapter provision is deferred).
        subsection_num: Momentti number, or None for a bare § (section-level).
        item_label:     Kohta label, or None.
        chapter:        Chapter label (``"9"``, ``"9a"``) when the reference is
                        chapter-qualified (``9 luvun 9 b §``), or None. Carried so
                        the caller can build a chapter-qualified AKN target path
                        (``chp_9__sec_9b``) — the SAME modeling the internal lane
                        uses (``references.internal_refs._chapter_akn_path``) —
                        rather than silently dropping the chapter.
    """

    section_label: str
    subsection_num: Optional[int] = None
    item_label: Optional[str] = None
    chapter: Optional[str] = None


def _subref_to_target(
    section_label: str, sub: SubRef, chapter: Optional[str] = None
) -> BodyProvisionTarget:
    return BodyProvisionTarget(
        section_label=section_label,
        subsection_num=sub.momentti if sub.momentti else None,
        item_label=sub.item or None,
        chapter=chapter,
    )


@dataclass(frozen=True)
class BodyTailParse:
    """The result of parsing a body-reference structural tail.

    Attributes:
        targets:       One expanded provision target per (section, sub-ref).
        consumed_text: The leading slice of the (whitespace-normalized) tail the
                       section-reference run actually consumed — e.g. ``"5 a §:ssä"``
                       for input ``" 5 a §:ssä tarkoitetun luontovahingon, …"``.
                       Empty string when no section reference was recognized.
    """

    targets: List[BodyProvisionTarget]
    consumed_text: str


def parse_body_provision_tail_spanned(tail_text: str) -> BodyTailParse:
    """Parse the section/momentti/kohta path AND report the consumed slice.

    Identical recognition to :func:`parse_body_provision_tail`, but additionally
    returns the leading slice of the *whitespace-normalized* tail that the
    section-reference run consumed. The slice is taken from the normalized form
    because the token ``char_end`` offsets index into the normalized string (the
    one ``tokenize`` builds after collapsing whitespace), so callers that want a
    trimmed surface (instead of an arbitrary fixed window) get exactly the bytes
    the grammar recognized.
    """
    # Collapse whitespace first, THEN rewrite a numeric-coordination ``tai``
    # ("or") to ``ja`` so the shared recognizer enumerates disjunctive section
    # lists (``114, 115 tai 155 §:n``) the same way it enumerates ``ja``/``sekä``
    # lists. The rewrite runs on the single-space-normalized form (so its regex
    # stays backtracking-safe) and the SAME string feeds both tokenization and
    # the consumed-slice accounting.
    ws_norm = _WS_RE.sub(" ", tail_text).strip()
    # The joiner-rewritten string (``tai`` → ``ja``) feeds the PARSE; ``ws_norm``
    # (joiner preserved) backs the recorded surface so it keeps the author's
    # original ``tai`` (N7). ``shrinks`` maps a consumed slice back onto ws_norm.
    normalized, shrinks = _normalize_disjunctive_joiner_spanned(ws_norm)

    # A cross-statute / body tail under ONE statute head can span MULTIPLE chapter
    # clauses (``rikoslain 17 luvun 18 §:ssä, 20 luvussa, 21 luvun 1—3 §:ssä``):
    # after the first chapter clause's section run is consumed, a list separator
    # introduces the NEXT chapter clause (``, 20 luvussa``). Each clause has its
    # own chapter prefix (``N luvun`` / ``N luvussa``) and its own section run, and
    # the chapter prefix is what re-opens the clause boundary — a plain
    # coordinated section run with no new chapter prefix stays inside the current
    # clause (handled by the inner section loop). We therefore drive an OUTER loop
    # over chapter clauses keyed off a leading-chapter-prefix at the clause head,
    # and ONLY treat a separator as a clause boundary when a fresh chapter prefix
    # follows it. A single-clause tail (the common case) makes exactly one pass,
    # so the prior behavior is preserved byte-for-byte.
    targets: List[BodyProvisionTarget] = []
    consumed_end = 0  # furthest consumed char offset into ``normalized``
    clause_base = 0  # offset into ``normalized`` of the current clause head
    first_clause = True
    while clause_base < len(normalized):
        clause_text = normalized[clause_base:]
        # Peel a leading ``N luvun`` chapter prefix off THIS clause (``9 luvun
        # 9 b §`` → chapter 9, section run ``9 b §``). For the first clause the
        # chapter is optional (a bare ``108—110 §`` tail has none); for a
        # SUBSEQUENT clause a chapter prefix is REQUIRED — without one there is no
        # new clause to open, so the loop stops (the trailing prose is not a
        # citation clause).
        section_text, chnums = _split_leading_chapter(clause_text)
        if chnums is None and not first_clause:
            break
        ch_prefix_len = len(clause_text) - len(section_text)
        chapters = _expand_chapter_run(chnums) if chnums else []
        chapter_choices: List[Optional[str]] = list(chapters) or [None]
        # The global offset (into ``normalized``) of the section run for this
        # clause, used to lift the per-clause token offsets to global offsets.
        section_base = clause_base + ch_prefix_len

        toks = _reclassify_body_tokens(tokenize(section_text))
        clause_targets: List[BodyProvisionTarget] = []
        clause_consumed_local = 0  # furthest token char_end within ``section_text``
        if toks:
            scan = _sections._Scan(Cursor(toks, 0))
            while scan.pos < len(toks):
                parsed = _sections.recognize_section_ref(scan)
                if parsed is None:
                    break
                # The body lane models only the suffix form (section + sub-refs);
                # renumber / pykälä-prefix are amendment shapes that do not occur
                # in a body citation tail. Emit one target per (chapter, expanded
                # section, sub-ref). When no chapter prefix was peeled,
                # ``chapter_choices`` is ``[None]`` so the section-only behavior is
                # unchanged.
                subs = list(parsed.subs) or [SubRef()]
                for chapter in chapter_choices:
                    for num, suffix in parsed.nums:
                        for expanded in _sections._expand_range_single(num):
                            label = expanded + (
                                suffix
                                if len(_sections._expand_range_single(num)) == 1
                                else ""
                            )
                            for sub in subs:
                                clause_targets.append(
                                    _subref_to_target(label, sub, chapter)
                                )
                # Record the consumed boundary as the furthest char_end among the
                # tokens consumed so far (tokens carry offsets into section_text).
                for i in range(min(scan.pos, len(toks))):
                    ce = toks[i].char_end
                    if ce > clause_consumed_local:
                        clause_consumed_local = ce
                # Consume a list separator between coordinated section-reference
                # runs INSIDE this clause (``6 §:n 1 mom ja 8 §:n 2 mom``). A
                # separator that introduces a new chapter prefix is the next
                # clause's boundary, not an in-clause section join: stop here and
                # let the outer loop pick the next clause up (it re-peels the
                # chapter prefix from the right offset).
                saved = scan.pos
                if _sections._sep(scan) is None:
                    break
                if scan.pos == saved:
                    break
                # Peek: if what follows the separator is a chapter prefix, this is
                # a clause boundary — stop the in-clause section loop and defer to
                # the outer loop, which re-peels the chapter prefix from the
                # recorded consumed offset (the separator is re-matched there by
                # ``_CLAUSE_SEP_RE``). ``section_text`` from the next unconsumed
                # token onward is the remaining text; a leading chapter prefix
                # there means a new clause.
                rest_local = (
                    section_text[toks[scan.pos].char_start :]
                    if scan.pos < len(toks)
                    else ""
                )
                if _CHAPTER_TAIL_PREFIX_RE.match(rest_local):
                    break

        if clause_targets:
            targets.extend(clause_targets)
        elif chapters:
            # A chapter prefix was peeled but no section run parsed after it
            # (``20 luvussa`` / ``5 luvussa tai …``): the clause is chapter-only.
            # Emit one chapter-only target per chapter (section deferred) so it is
            # never silently dropped.
            for ch in chapters:
                targets.append(BodyProvisionTarget(section_label="", chapter=ch))

        # Compute this clause's furthest global consumed offset. With a section
        # run, it is the section run's furthest token end lifted by section_base;
        # for a chapter-only clause, the chapter prefix itself is consumed.
        if clause_consumed_local > 0:
            consumed_end = section_base + clause_consumed_local
        elif chapters:
            consumed_end = section_base  # chapter prefix consumed, no section
        elif first_clause:
            # First clause, no chapter and no parsable section: fail-loud (no ref).
            return BodyTailParse(targets=[], consumed_text="")
        else:
            break

        # Advance to the next clause: skip a separator (``, `` / ``ja`` / ``sekä``
        # / ``tai``) after the consumed text, then loop. The outer loop's chapter-
        # prefix requirement (for non-first clauses) stops the scan when the
        # separator does not introduce a fresh chapter clause.
        first_clause = False
        next_pos = consumed_end
        sep_m = _CLAUSE_SEP_RE.match(normalized, next_pos)
        if sep_m is None:
            break
        clause_base = sep_m.end()

    if not targets:
        return BodyTailParse(targets=[], consumed_text="")

    # The consumed slice is reported against the FULL tail. Recover the author's
    # original disjunctive joiner (``tai``) from ws_norm so the recorded surface
    # is faithful (N7) while target enumeration stays the ``ja`` semantics. The
    # consumed offset already indexes the full ``normalized`` string (chapter
    # prefixes included), so by_name's span alignment stays correct.
    consumed_text = (
        _unnormalize_consumed(normalized[:consumed_end], ws_norm, shrinks).rstrip()
        if consumed_end > 0
        else ""
    )
    return BodyTailParse(targets=targets, consumed_text=consumed_text)


# An inline ``<ref>`` element's surface text opens with the statute-id
# parenthetical (``(360/1968) 18 a ja 18 b §:ssä``). The href anchors only the
# FIRST coordinated section, so to enumerate every coordinated member we strip
# the leading ``(NUMBER/YEAR)`` paren and parse the remaining structural tail
# through the same body recognizer the plain-text by-id lane uses. The paren may
# carry the statute name BEFORE it too (``… lain (360/1968) 6 a ja 6 d §:ssä``);
# the slice starts after the LAST ``)`` that precedes the section run so any
# leading name/paren is dropped and only the section tail remains.
_REF_ID_PAREN_RE = re.compile(r"\([^)]*\d+\s*/\s*\d{2,4}[^)]*\)")


def _ref_section_label_to_akn_path(section_label: str) -> str:
    """``"18b"`` → ``"sec_18b"``; section-level AKN provision path."""
    return f"sec_{section_label}"


def coordinated_member_paths_from_ref_surface(
    surface_text: str, anchored_path: str
) -> List[str]:
    """Enumerate the coordinated section members a ``<ref>`` surface names.

    A Finlex inline ``<ref>`` element's ``href`` anchors only the FIRST member of
    a coordinated section list (``(360/1968) 18 a ja 18 b §:ssä`` → ``sec_18a``).
    The LawVM convention elsewhere is to enumerate EVERY coordinated member, so
    this re-parses the ref's own surface text — through the SAME body recognizer
    the plain-text by-id lane uses (:func:`parse_body_provision_tail`) — and
    returns the AKN section paths of the members BEYOND the anchored first.

    ``surface_text``  : the ``<ref>`` element's collapsed text, e.g.
                        ``"(360/1968) 18 a ja 18 b §:ssä"``.
    ``anchored_path`` : the href-anchored provision path, e.g. ``"sec_18a"`` (its
                        member is already emitted; it is excluded from the
                        returned additions).

    Returns the additional members' section-level AKN paths (``["sec_18b"]``), in
    surface order, deduplicated. Empty when the surface names no further
    coordinated section (a bare single-section ref, a sub-provision-only tail, or
    an unparsable surface) — so this only ever ADDS the dropped siblings and never
    removes or rewrites the anchored member.
    """
    if not surface_text:
        return []
    # Drop everything up to and including the statute-id parenthetical so the
    # remaining text is the structural tail (``18 a ja 18 b §:ssä``). Match the
    # LAST id-paren so a leading statute name carrying its own paren is skipped.
    m = None
    for m in _REF_ID_PAREN_RE.finditer(surface_text):
        pass
    tail = surface_text[m.end():] if m is not None else surface_text
    targets = parse_body_provision_tail(tail)
    if not targets:
        return []
    # The anchored section label, derived from the href path, is already emitted;
    # collect the labels of the OTHER coordinated section members. Only distinct
    # section labels matter here (the anchored member's sub-precision is owned by
    # its own edge); members are emitted at section level, faithful to the
    # plain-text by-id lane which the href-less form already resolves this way.
    anchored_section = ""
    a = re.search(r"sec_([0-9]{1,6}[a-z]?)", anchored_path or "")
    if a:
        anchored_section = a.group(1)
    additions: List[str] = []
    seen = {anchored_section} if anchored_section else set()
    for tgt in targets:
        label = tgt.section_label
        if not label or label in seen:
            continue
        seen.add(label)
        # A chapter-qualified coordinated tail (``(NNN/YYYY) 9 luvun 9 a ja
        # 9 b §``) carries the chapter onto each sibling so the addition path is
        # ``chp_9__sec_9b``, matching the AKN form the anchored member's href and
        # the internal lane both use; bare (chapter-less) tails keep ``sec_N``.
        if tgt.chapter is not None:
            additions.append(chapter_akn_path(tgt.chapter, label))
        else:
            additions.append(_ref_section_label_to_akn_path(label))
    return additions


def parse_body_provision_tail(tail_text: str) -> List[BodyProvisionTarget]:
    """Parse the section/momentti/kohta path of a body reference.

    ``tail_text`` is the text from the section number onward (everything after
    the statute-name head + ``(id)`` anchor), e.g. ``"108—110 §:ää ei …"`` or
    ``"6 §:n 1 momentissa säädetään"``. Tokenizes it, runs the shared section
    recognizer in body mode (so the inessive ``momentissa`` reads as MOMENTTI),
    and expands ranges / coordination / momentti precision into one
    ``BodyProvisionTarget`` per section.

    Returns an empty list when the tail does not begin with a recognizable
    section reference (the anchor matched a statute id but no parsable § tail —
    a bare statute-level citation; the caller emits the STATUTE_ONLY fallback).
    Only the LEADING section-reference run is consumed; trailing prose is
    ignored.

    Thin wrapper over :func:`parse_body_provision_tail_spanned` keeping the
    historical list-only return shape its other callers rely on.
    """
    return parse_body_provision_tail_spanned(tail_text).targets
