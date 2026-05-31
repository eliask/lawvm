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

from collections.abc import Mapping
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


@dataclass(frozen=True, slots=True)
class FromChildEndSelector:
    """``from the end of child <kind> <label>`` to an anchor in the node's own text.

    Serializes to the ``US``-separated sentinel
    ``TEXT_FROM_CHILD_END\\x1f<kind>\\x1f<label>\\x1f<start>``.  ``start`` is held
    verbatim (the consumer strips it at use time) so the round-trip is exact.
    """

    child_kind: str
    child_label: str
    start: str


@dataclass(frozen=True, slots=True)
class ExceptPhraseSelector:
    """All occurrences of ``original`` except when inside ``excluded_phrase``."""

    original: str
    excluded_phrase: str


@dataclass(frozen=True, slots=True)
class RawSelector:
    """A not-yet-migrated ``TEXT_*`` sentinel string carried verbatim.

    The parser still emits sentinel families that have no typed selector yet
    (``TEXT_IN_DEFINITION_*``, ``TEXT_WORD_*``, ``TEXT_PROVISO_CHILD_*`` …).
    Wrapping the raw string keeps the typed surface *total* — every legacy
    ``original`` round-trips — without forcing every family to be migrated at
    once.  A shrinking count of ``RawSelector`` uses is the remaining migration
    debt; a consumer must never branch on a ``RawSelector``'s inner string (that
    would re-introduce the stringly-typed IR this module exists to remove).
    """

    original: str


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
    | FromChildEndSelector
    | ExceptPhraseSelector
    | RawSelector
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
    tail_connector: str = ""


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
    if isinstance(selector, FromChildEndSelector):
        return f"TEXT_FROM_CHILD_END{US}{selector.child_kind}{US}{selector.child_label}{US}{selector.start}"
    if isinstance(selector, ExceptPhraseSelector):
        return f"TEXT_EXCEPT_PHRASE{US}{selector.original}{US}{selector.excluded_phrase}"
    if isinstance(selector, RawSelector):
        return selector.original
    raise TypeError(f"unknown selector: {selector!r}")


def selector_from_legacy_original(original: str) -> UKTextSelector:
    """Parse a legacy ``original`` string back into a typed selector.

    Inverse of :func:`selector_to_legacy_original` for every form that function
    can emit: ``selector_to_legacy_original(selector_from_legacy_original(s)) == s``
    for all ``s``.  Recognized sentinels become their typed selector; an
    unrecognized ``TEXT_*`` sentinel becomes a :class:`RawSelector` (not yet
    migrated); anything else is a :class:`LiteralSelector`.

    This is the single typed boundary a consumer should cross instead of
    re-sniffing sentinel prefixes inline.
    """
    if original == "TEXT_OPENING_WORDS":
        return OpeningWordsSelector()
    if original == "TEXT_BEGINNING":
        return BeginningSelector()
    if original == "TEXT_END":
        return EndSelector()
    if original.startswith("TEXT_BEFORE_CHILD_"):
        kind, _, label = original[len("TEXT_BEFORE_CHILD_") :].partition("_")
        return BeforeChildSelector(kind, label)
    if original.startswith("TEXT_AFTER_CHILD_"):
        kind, _, label = original[len("TEXT_AFTER_CHILD_") :].partition("_")
        return AfterChildSelector(kind, label)
    if original.startswith("TEXT_BEFORE_DEFINITION_"):
        return DefinitionAnchorSelector(original[len("TEXT_BEFORE_DEFINITION_") :], "before")
    if original.startswith("TEXT_AFTER_DEFINITION_"):
        return DefinitionAnchorSelector(original[len("TEXT_AFTER_DEFINITION_") :], "after")
    if original.startswith(f"TEXT_FROM_CHILD_END{US}"):
        parts = original.split(US, 3)
        if len(parts) == 4:
            return FromChildEndSelector(parts[1], parts[2], parts[3])
        return RawSelector(original)
    if original.startswith(f"TEXT_EXCEPT_PHRASE{US}"):
        parts = original.split(US, 2)
        if len(parts) == 3:
            return ExceptPhraseSelector(parts[1], parts[2])
        return RawSelector(original)
    if original.startswith("TEXT_AFTER_") and original.endswith("_TO_END"):
        return AfterAnchorToEndSelector(original[len("TEXT_AFTER_") : -len("_TO_END")])
    if original.startswith("TEXT_FROM_") and original.endswith("_TO_END"):
        return RangeToEndSelector(original[len("TEXT_FROM_") : -len("_TO_END")])
    if original.startswith("TEXT_FROM_"):
        body = original[len("TEXT_FROM_") :]
        if "_TO_" in body:
            start, _, end = body.partition("_TO_")
            return RangeFromToSelector(start, end)
    if original.startswith("TEXT_"):
        return RawSelector(original)
    return LiteralSelector(original)


def fragment_to_legacy_dict(fragment: UKTextRewriteFragment) -> dict[str, str]:
    """Serialize a typed fragment to the legacy fragment dict.

    Only non-empty optional fields are emitted, matching how the parser
    productions build their dicts today (absent keys, not empty values).
    """
    out: dict[str, str] = {
        "original": selector_to_legacy_original(fragment.selector),
        "replacement": fragment.replacement,
    }
    # The parser omits rule_id on a couple of fragments (the reversed-order
    # substitution fallback, the in-definition substitute); an empty rule_id is
    # serialized as an absent key so the round-trip is byte-identical.
    if fragment.rule_id:
        out["rule_id"] = fragment.rule_id
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
    if fragment.tail_connector:
        out["tail_connector"] = fragment.tail_connector
    return out


def fragment_from_legacy_dict(legacy: Mapping[str, str]) -> UKTextRewriteFragment:
    """Parse a legacy fragment dict back into a typed fragment.

    Inverse of :func:`fragment_to_legacy_dict`:
    ``fragment_to_legacy_dict(fragment_from_legacy_dict(d)) == dict(d)`` for any
    dict the parser produces (absent optional keys stay absent because the
    reconstructed fragment leaves them empty and the serializer omits empties).
    """
    return UKTextRewriteFragment(
        selector=selector_from_legacy_original(legacy["original"]),
        replacement=legacy.get("replacement", ""),
        rule_id=legacy.get("rule_id", ""),
        occurrence=legacy.get("occurrence", ""),
        end_occurrence=legacy.get("end_occurrence", ""),
        source_child_kind=legacy.get("source_child_kind", ""),
        source_child_label=legacy.get("source_child_label", ""),
        target_suffix_kind=legacy.get("target_suffix_kind", ""),
        target_suffix_label=legacy.get("target_suffix_label", ""),
        tail_connector=legacy.get("tail_connector", ""),
    )
