"""Typed text-selector algebra for UK amendment instructions.

Background:
    UK source-carried rewrite instructions name *what region of a provision* to
    rewrite.  Historically that selection has been encoded two ways inside a
    single ``original`` string field on the legacy fragment dict:

    - ordinary quoted text, e.g. ``original="the words"`` (a literal selector);
    - a hidden sentinel language, e.g. ``original="TEXT_FROM_X_TO_END"``,
      ``"TEXT_AFTER_<anchor>_TO_END"``, ``"TEXT_OPENING_WORDS"`` — symbolic
      strings constructed in ``nlp_parser.py``, carried through lowering, and
      re-parsed in replay.

    That is a stringly-typed IR: the selector *semantics* exist only as the
    shape of a string.  This module makes the selection a typed object so the
    type checker, not a downstream regex, enforces its structure.  Even an
    ordinary quoted selector is a ``LiteralSelector`` here — there is one notion
    of "selector", not "typed for the weird cases, raw strings for the common
    case".

Migration policy (staged; see notes/REGEX_TO_GRAMMAR_MIGRATION.md):
    Parser productions construct ``UKTextRewriteFragment`` objects and serialize
    them to the existing legacy dict via ``fragment_to_legacy_dict`` at the
    boundary.  Lowering and replay keep consuming the legacy dict unchanged, so
    a production migration is byte-identical and has no behavioral blast radius.
    ``rule_id`` stays *provenance* (how a fragment was recognized); the selector
    object carries *meaning* (what it selects).  Consumers should branch on the
    selector type, not on ``rule_id``.

Extraction-readiness:
    Pure dataclasses.  No replay imports, no XML, no live-state resolution
    (same in-tree, standalone-ready discipline as ``core/regex_safety.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ASCII Unit Separator — matches nlp_parser.US, used inside some sentinel forms.
US = "\x1f"


# ---------------------------------------------------------------------------
# Selector algebra
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiteralSelector:
    """The selected region is the literal quoted text itself."""

    text: str


@dataclass(frozen=True, slots=True)
class RangeFromToSelector:
    """``from Start to End`` — a bounded span between two anchors.

    ``start`` may be empty for the ``from the beginning to End`` shape, which
    serializes to ``TEXT_FROM__TO_<end>``.
    """

    start: str
    end: str


@dataclass(frozen=True, slots=True)
class RangeToEndSelector:
    """``from Anchor to the end`` — a span starting at (and including) Anchor."""

    start: str


@dataclass(frozen=True, slots=True)
class AfterAnchorToEndSelector:
    """``after Anchor ... to the end`` — a span starting after Anchor."""

    anchor: str


@dataclass(frozen=True, slots=True)
class OpeningWordsSelector:
    """The opening words of the provision."""


@dataclass(frozen=True, slots=True)
class BeginningSelector:
    """The beginning of the provision (insertion anchor)."""


@dataclass(frozen=True, slots=True)
class EndSelector:
    """The end of the provision (insertion anchor)."""


@dataclass(frozen=True, slots=True)
class BeforeChildSelector:
    """The position before a labeled child (``before paragraph (a)``)."""

    child_kind: str
    child_label: str


@dataclass(frozen=True, slots=True)
class AfterChildSelector:
    """The position after a labeled child (``after paragraph (a)``)."""

    child_kind: str
    child_label: str


@dataclass(frozen=True, slots=True)
class DefinitionAnchorSelector:
    """``before/after the definition of "Term"`` — a definition-list anchor."""

    term: str
    direction: Literal["before", "after"]


UKTextSelector = (
    LiteralSelector
    | RangeFromToSelector
    | RangeToEndSelector
    | AfterAnchorToEndSelector
    | OpeningWordsSelector
    | BeginningSelector
    | EndSelector
    | BeforeChildSelector
    | AfterChildSelector
    | DefinitionAnchorSelector
)


# ---------------------------------------------------------------------------
# Fragment object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UKTextRewriteFragment:
    """A typed source-carried text rewrite.

    ``occurrence`` mirrors the legacy string field: ``""`` (omitted / single),
    ``"-1"`` (all occurrences), or an ordinal ``"1"``..``"5"``.  It is kept as a
    string so serialization to the legacy dict is byte-identical.
    """

    selector: UKTextSelector
    replacement: str
    rule_id: str
    occurrence: str = ""
    end_occurrence: str = ""
    source_child_kind: str = ""
    source_child_label: str = ""
    target_suffix_kind: str = ""
    target_suffix_label: str = ""


# ---------------------------------------------------------------------------
# Legacy serialization (compatibility boundary)
# ---------------------------------------------------------------------------


def selector_to_legacy_original(selector: UKTextSelector) -> str:
    """Serialize a selector to the legacy ``original`` string.

    The exact strings match the sentinels constructed in ``nlp_parser.py`` so a
    migrated production produces byte-identical fragment dicts.
    """
    if isinstance(selector, LiteralSelector):
        return selector.text
    if isinstance(selector, RangeFromToSelector):
        return f"TEXT_FROM_{selector.start}_TO_{selector.end}"
    if isinstance(selector, RangeToEndSelector):
        return f"TEXT_FROM_{selector.start}_TO_END"
    if isinstance(selector, AfterAnchorToEndSelector):
        return f"TEXT_AFTER_{selector.anchor}_TO_END"
    if isinstance(selector, OpeningWordsSelector):
        return "TEXT_OPENING_WORDS"
    if isinstance(selector, BeginningSelector):
        return "TEXT_BEGINNING"
    if isinstance(selector, EndSelector):
        return "TEXT_END"
    if isinstance(selector, BeforeChildSelector):
        return f"TEXT_BEFORE_CHILD_{selector.child_kind}_{selector.child_label}"
    if isinstance(selector, AfterChildSelector):
        return f"TEXT_AFTER_CHILD_{selector.child_kind}_{selector.child_label}"
    if isinstance(selector, DefinitionAnchorSelector):
        prefix = "TEXT_BEFORE_DEFINITION" if selector.direction == "before" else "TEXT_AFTER_DEFINITION"
        return f"{prefix}_{selector.term}"
    raise TypeError(f"unknown selector: {selector!r}")


def fragment_to_legacy_dict(fragment: UKTextRewriteFragment) -> dict[str, str]:
    """Serialize a typed fragment to the legacy fragment dict.

    Only non-empty optional fields are emitted, matching how the parser
    productions build their dicts today (absent keys, not empty values).
    """
    out: dict[str, str] = {
        "original": selector_to_legacy_original(fragment.selector),
        "replacement": fragment.replacement,
        "rule_id": fragment.rule_id,
    }
    if fragment.occurrence:
        out["occurrence"] = fragment.occurrence
    if fragment.end_occurrence:
        out["end_occurrence"] = fragment.end_occurrence
    if fragment.source_child_kind:
        out["source_child_kind"] = fragment.source_child_kind
    if fragment.source_child_label:
        out["source_child_label"] = fragment.source_child_label
    if fragment.target_suffix_kind:
        out["target_suffix_kind"] = fragment.target_suffix_kind
    if fragment.target_suffix_label:
        out["target_suffix_label"] = fragment.target_suffix_label
    return out
