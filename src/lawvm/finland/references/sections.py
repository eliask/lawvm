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
    normalized = _WS_RE.sub(" ", tail_text).strip()
    toks = _reclassify_body_tokens(tokenize(tail_text))
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
