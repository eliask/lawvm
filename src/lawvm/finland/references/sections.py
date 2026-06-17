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


def _normalize_disjunctive_joiner(tail_text: str) -> str:
    """Rewrite a numeric-coordination ``tai`` to ``ja`` for the body tail parse.

    ``"114, 115 tai 155 §:n"`` → ``"114, 115 ja 155 §:n"``. Only a ``tai`` flanked
    by digits (the section-number coordination position) is rewritten; a ``tai``
    elsewhere in the tail (e.g. inside the trailing prose the parser discards) is
    left untouched. The replacement preserves the surrounding whitespace so the
    consumed-slice / char-offset accounting downstream is unaffected.
    """
    if "tai" not in tail_text.lower():
        return tail_text
    return _TAI_NUMERIC_JOINER_RE.sub(
        lambda m: f"{m.group('lsuf') or ''}{m.group('pre')}ja{m.group('post')}",
        tail_text,
    )


@dataclass(frozen=True)
class BodyProvisionTarget:
    """One expanded provision target from a body reference structural tail.

    Attributes:
        section_label:  Section label, e.g. "7", "7a", "108".
        subsection_num: Momentti number, or None for a bare § (section-level).
        item_label:     Kohta label, or None.
    """

    section_label: str
    subsection_num: Optional[int] = None
    item_label: Optional[str] = None


def _subref_to_target(section_label: str, sub: SubRef) -> BodyProvisionTarget:
    return BodyProvisionTarget(
        section_label=section_label,
        subsection_num=sub.momentti if sub.momentti else None,
        item_label=sub.item or None,
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
    normalized = _normalize_disjunctive_joiner(_WS_RE.sub(" ", tail_text).strip())
    toks = _reclassify_body_tokens(tokenize(normalized))
    if not toks:
        return BodyTailParse(targets=[], consumed_text="")
    scan = _sections._Scan(Cursor(toks, 0))

    targets: List[BodyProvisionTarget] = []
    consumed_end = 0  # char offset into ``normalized`` of the last consumed token
    while scan.pos < len(toks):
        parsed = _sections.recognize_section_ref(scan)
        if parsed is None:
            break
        # The body lane models only the suffix form (section + sub-refs);
        # renumber / pykälä-prefix are amendment shapes that do not occur in a
        # body citation tail. Emit one target per expanded (section, sub-ref).
        subs = list(parsed.subs) or [SubRef()]
        for num, suffix in parsed.nums:
            for expanded in _sections._expand_range_single(num):
                label = expanded + (
                    suffix if len(_sections._expand_range_single(num)) == 1 else ""
                )
                for sub in subs:
                    targets.append(_subref_to_target(label, sub))
        # Record the consumed boundary as the furthest char_end among the tokens
        # consumed so far (tokens carry char offsets into ``normalized``).
        for i in range(min(scan.pos, len(toks))):
            ce = toks[i].char_end
            if ce > consumed_end:
                consumed_end = ce
        # Consume a list separator between coordinated section-reference runs
        # (``6 §:n 1 momentissa ja 8 §:n 2 momentissa``); stop when none.
        saved = scan.pos
        if _sections._sep(scan) is None:
            break
        # Guard against a separator that does not introduce another section ref.
        if scan.pos == saved:
            break

    consumed_text = normalized[:consumed_end].rstrip() if consumed_end > 0 else ""
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
