"""Lower U.S. Public Law USLM amendatory text into canonical LegalOperation candidates.

This is the first U.S. surface that compiles enacted amendatory instructions into
core ``LegalOperation`` envelopes. It does **not** apply them, materialize text, or
claim replay agreement: every op produced here is a *candidate* whose truth is only
established later by the dry-run against the USC oracle.

Source signal
-------------
govinfo PLAW USLM XML marks amendatory language structurally:

- ``<ref href="/us/usc/t11/s101/10A">`` carries the amendment target (and its
  prose form ``Section 101(10A) of title 11, United States Code``);
- ``<amendingAction type="amend|delete|insert|add|redesignate|repeal">`` tags the
  action verbs;
- ``<quotedText>`` carries inline old/new strings (strike/insert);
- ``<quotedContent>`` carries quoted block payloads (add-at-end / amend-to-read).

We lower the *common* forms the prompt enumerates. Anything we cannot lower is
NEVER silently skipped: it becomes a typed finding (``us_amendatory_unlowered``)
and the instruction is recorded with status ``unsupported``/``needs_review``.

Prime Directive (AGENTS.md §0/§1): no silent target hijacking. Unresolved targets
and unparsable payloads are preserved as typed findings, not guessed away.
"""

from __future__ import annotations

import calendar
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.parse_witness import ParseWitness
from lawvm.core.branch_authority import COMMENCED_STATUS, PENDING_CONDITION_STATUS
from lawvm.core.provenance import OperationSource
from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum

USLM_NS = "http://schemas.gpo.gov/xml/uslm"
_NS = {"u": USLM_NS}

# ---------------------------------------------------------------------------
# Witness rule ids (stable). Each lowered family carries its own id; the single
# finding id flags anything left unlowered.
# ---------------------------------------------------------------------------
RULE_STRIKE_INSERT = "us_amend_strike_insert"
RULE_STRIKE = "us_amend_strike"
RULE_INSERT_AFTER = "us_amend_insert_after_anchor"
RULE_INSERT_BEFORE = "us_amend_insert_before_anchor"
RULE_ADD_AT_END = "us_amend_add_at_end"
RULE_ADD_AT_END_NEW_SECTIONS = "us_amend_add_at_end_new_sections"
RULE_AMEND_TO_READ = "us_amend_to_read"
RULE_REPEAL = "us_amend_repeal"
RULE_REDESIGNATE = "us_amend_redesignate"
RULE_STRIKE_UNIT = "us_amend_strike_structural_unit"
RULE_STRIKE_UNIT_LIST = "us_amend_strike_structural_unit_list"
RULE_REDESIGNATE_RANGE = "us_amend_redesignate_range"
RULE_REDESIGNATE_PAIRS = "us_amend_redesignate_pairs"
RULE_INSERT_NODE_AFTER = "us_amend_insert_node_after_unit"
# Terminal punctuation edits where the struck anchor is described positionally:
# "striking the period at the end and inserting '; and'" / "inserting 'X' before
# the period at the end".  These are common list-conjunction amendments; they are
# lowered to a last-occurrence text_replace targeted at the target node rather than
# a first-occurrence string replace that could hit mid-text punctuation.
RULE_STRIKE_INSERT_END_PUNCT = "us_amend_strike_insert_end_punctuation"
RULE_INSERT_END_PUNCT = "us_amend_insert_end_punctuation"
# "striking '<old>' and inserting a semicolon/comma/period" — the insertion is
# given as a punctuation word, not a quoted literal.  Lowered to a last-occurrence
# text_replace of the old quoted text with the punctuation character.
RULE_STRIKE_INSERT_PUNCT_WORD = "us_amend_strike_insert_punctuation_word"
# An open-ended tail strike combined with an insertion: "striking 'X' and all that
# follows and inserting 'Y'".  The quoted match text is only the anchor; the
# materializer must delete from the anchor to the end of the target node.
RULE_STRIKE_INSERT_TAIL = "us_amend_strike_insert_tail"
# A BOUNDED tail strike: "striking 'OLD' and all that follows through 'END' and
# inserting 'NEW'". The first quoted text is the LEFT anchor; the second quoted
# text is the RIGHT bound (inclusive). The materializer deletes [OLD..END] then
# inserts NEW. Unlike RULE_STRIKE_INSERT_TAIL, the deletion stops at END instead
# of running to the end of the node, so the inventoried right-side text after END
# survives. Pure-strike form ("striking 'OLD' and all that follows through 'END'.")
# lowers to the same shape with replacement="".
RULE_STRIKE_INSERT_THROUGH_TAIL = "us_amend_strike_insert_through_tail"

UNLOWERED_FINDING_RULE_ID = "us_amendatory_unlowered"
TARGET_UNRESOLVED_FINDING_RULE_ID = "us_amendatory_target_unresolved"
NON_TITLE_TARGET_RULE_ID = "us_amendatory_target_non_us_code"
# A strike-and-insert that also splices a whole new structural node is a positional
# compound (multiple end-of-paragraph conjunction edits + a block add) that a single
# 2-operand text_replace cannot faithfully represent; held out as a typed residual.
COMPOUND_STRIKE_INSERT_FINDING_RULE_ID = "us_amendatory_compound_strike_insert_node"
# An "add at the end the following: <block>" whose block opens with a NEW section /
# chapter head ("§ 2328. …", "CHAPTER 37—…") is a whole-new-section CREATE, not an
# append to the inherited section's body. It does not materialize from any before-
# node (the section/chapter does not yet exist), so forcing it onto the inherited
# section corrupts that sibling's text. Held out as a typed residual (an honest
# new-section insert), never appended to the wrong before-node.
NEW_SECTION_INSERT_FINDING_RULE_ID = "us_amendatory_new_section_insert"
# Structural-strike forms that cannot be represented as section-text operations yet.
# Emitting a named finding keeps the uncertainty visible rather than hiding it in
# the generic unlowered bucket.
SENTENCE_STRIKE_FINDING_RULE_ID = "us_amendatory_sentence_strike_not_section_representable"
HEADING_STRIKE_FINDING_RULE_ID = "us_amendatory_heading_strike_not_section_representable"
TAIL_STRIKE_FINDING_RULE_ID = "us_amendatory_tail_strike_not_section_representable"
DESIGNATION_STRIKE_FINDING_RULE_ID = "us_amendatory_designation_strike_not_section_representable"
# A tail strike whose deletion is explicitly bounded by a second quoted anchor
# ("striking 'unless—' and all that follows through 'the holder'") is not the same
# as an open-ended tail deletion: it deletes a defined span. We cannot materialize it
# as a simple TEXT_REPLACE without losing the bounded range semantics, and we must not
# let the generic quote parser grab arbitrary quoted fragments from the instruction.
THROUGH_TAIL_STRIKE_FINDING_RULE_ID = "us_amendatory_through_tail_strike_not_section_representable"
# Amend-to-read units with future-effective / sunset language ("On the date that is 1
# year after ..., section ... is amended to read as follows") are delayed-effect
# instructions. Lowering them as immediate REPLACE ops would corrupt the in-force text
# for any after-edition before the effective date. The temporal layer owns them.
DEFERRED_AMEND_TO_READ_FINDING_RULE_ID = "us_amendatory_deferred_amend_to_read"

# A target whose title was inferred from the Act section's govinfo/OLRC classification
# refs (including sidenote refs) when the amendatory head omits "of title N". The
# section number is named in the head; the title comes from the publisher's own
# USC classification of that section. This is recorded, not guessed.
TARGET_TITLE_FROM_SECTION_CLASSIFICATION = "us_amend_target_title_from_section_classification"
# A target whose title was inferred from the Public Law's own short-title preamble
# (the dc:title metadata) when the instruction text names a section but omits the
# title. This is used only when the preamble names a SINGLE USC title and the unit
# otherwise has no resolved title; it is never allowed to override an explicit
# "of title N" reference or an inherited title.
TARGET_TITLE_FROM_PLAW_METADATA = "us_amend_target_title_from_plaw_metadata"
# Scope-conflict guard: when the PLAW preamble names one title but explicit refs in
# the act name a different title, the metadata fallback is withheld for the whole
# Public Law to avoid cross-title target hijacking.
PLAW_METADATA_SCOPE_CONFLICT_RULE_ID = "us_amend_plaw_metadata_scope_conflict"

# A text-replace instruction whose enacted text says "each place it appears" /
# "each place appearing" — a statutory instruction to apply the replacement to
# ALL occurrences, not just the first. This is the typed carrier for what was
# previously a raw substring check "each place" in raw_text (AGENTS.md §1.11:
# no surface predicate authorizes legal state — mutation scope is legal state).
# The regex is a named classifier compiled through compile_classifier_regex so the
# backtracking lint and required-literal prefilter are enforced (AGENTS.md §2.4).
EACH_PLACE_APPLIES_RULE_ID = "us_amend_text_replace_each_place"
_EACH_PLACE_RE = compile_classifier_regex(
    r"\beach\s+place\b",
    re.IGNORECASE,
    classifier_id="us_amendatory_each_place",
)


def _is_each_place_instruction(raw_text: str) -> bool:
    """True when the enacted instruction directs an all-occurrence replacement.

    ``"each place it appears"`` / ``"each place appearing"`` is a statutory
    instruction that the strike-and-insert applies to every occurrence of the
    match text, not just the first. This sets ``TextSelector.occurrence = -1``
    (all occurrences). Without this, a single-occurrence replacement leaves
    later occurrences unmodified — a mutation-scope error.
    """
    return _EACH_PLACE_RE.search(raw_text) is not None
# Effective-date phrase family (AGENTS.md §2.4: previously 7 overlapping
# regex variants of one phrase family; merged into 4 named patterns by
# unifying the "effective" / "take effect" trigger words, which are
# synonymous drafting verbs). These extract the date when the instruction
# is in force for the requested point-in-time.
_EFFECTIVE_OR_TAKE_EFFECT_AFTER_RE = re.compile(
    r"(?:effective|take\s+effect)\s+(?:on\s+)?(?:the\s+date\s+that\s+is\s+)?"
    r"(?P<n>\d+)\s+(?P<unit>year|month|day)s?\s+after\s+"
    r"(?:the\s+date\s+of\s+(?:the\s+)?enactment\s+of\s+this\s+Act|\b(?P<base_month>[A-Z][a-z]+)\s+"
    r"(?P<base_day>\d{1,2}),?\s+(?P<base_year>\d{4}))",
    re.IGNORECASE,
)
_EFFECTIVE_OR_TAKE_EFFECT_ON_RE = re.compile(
    r"(?:effective|take\s+effect)\s+(?:on\s+)?(?:the\s+)?date\s+of\s+(?:the\s+)?enactment\s+of\s+this\s+Act",
    re.IGNORECASE,
)
_EFFECTIVE_ABSOLUTE_RE = re.compile(
    r"(?:effective|take\s+effect)\s+(?:on\s+)?(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

# An ancestor "Effective Date; Sunset" paragraph may describe an expiry for a set
# of sibling amendatory subsections.  We capture the prose "effective on the date
# that is N year(s)/month(s)/day(s) after the date of enactment of this Act" as
# the *expires* value.
_SUNSET_AFTER_ENACTMENT_RE = re.compile(
    r"effective\s+on\s+(?:the\s+date\s+that\s+is\s+)?"
    r"(?P<n>\d+)\s+(?P<unit>year|month|day)s?\s+after\s+"
    r"(?:the\s+date\s+of\s+(?:the\s+)?enactment\s+of\s+this\s+Act)",
    re.IGNORECASE,
)
_SUNSET_ABSOLUTE_RE = re.compile(
    r"effective\s+(?:on\s+)?(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _add_calendar_delta(start: date, n: int, unit: str) -> date:
    """Add a calendar delta (year/month/day) without dateutil."""
    if unit == "day":
        return start + timedelta(days=n)
    if unit == "month":
        total_months = (start.month - 1) + n
        new_year = start.year + total_months // 12
        new_month = total_months % 12 + 1
        last_day = calendar.monthrange(new_year, new_month)[1]
        return date(new_year, new_month, min(start.day, last_day))
    if unit == "year":
        new_year = start.year + n
        try:
            return date(new_year, start.month, start.day)
        except ValueError:
            return date(new_year, start.month, start.day - 1)
    return start


def _build_date_or_none(year: int, month: int, day: int) -> date | None:
    """Build a ``date`` from year/month/day components or return ``None`` when invalid.

    Three effective/sunset parsers build a date from regex-captured year/month/day
    components that may over-match (Feb 30, day 0, month 13). Returning ``None`` is
    the parsers' "regex-over-matched, no real date" signal that callers translate
    into ``""`` themselves; this helper owns that single recovery shape instead of
    triplicating the ``try/except`` (AGENTS.md §2.6).
    """
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_iso_date_or_none(enacted: str) -> date | None:
    """Parse ``YYYY-MM-DD`` to ``date`` or return ``None`` when malformed.

    Same rule-of-three collapse as :func:`_build_date_or_none` for the enacted-
    date-anchored effective/sunset parsers (AGENTS.md §2.6).
    """
    try:
        return datetime.strptime(enacted, "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# PLAW short-title metadata scope
# ---------------------------------------------------------------------------

# "To amend title 11, United States Code, ...", "to amend titles 11, 13, and 15 ..."
_PLAW_TITLE_SCOPE_RE = re.compile(
    r"\btitles?\s+(?P<items>\d+(?:[,\s]+(?:and\s+)?\d+)*)\b"
    r"(?:\s*,?\s+United\s+States\s+Code)?",
    re.IGNORECASE,
)


def _plaw_usc_title_scope(root: ET.Element) -> str:
    """Return a single USC title number named in the PLAW's dc:title preamble.

    The enacted short-title preamble is an authoritative source lane: "To amend
    title 38, United States Code, ...".  When it names exactly one title, that title
    can complete a bare "Section N(...)" amendatory target that lacks an explicit
    "of title N" or sidenote classification ref.  Multiple titles or no title makes
    the scope ambiguous and returns "", refusing the fallback.

    Accepts the already-parsed PLAW root (``lower_plaw_amendatory`` parses the data
    once via ``ET.fromstring`` before this helper is reached).  Re-parsing ``data``
    here would re-raise the same ``ET.ParseError`` and mask it behind a silent ``""``
    return — an invariant-masking broad catch (AGENTS.md §1.10).  By taking the
    parsed root we never reach the catch path: malformed XML fails loudly at the
    caller's parse, propagating the typed ``ET.ParseError`` instead of silently
    disabling the metadata-title fallback behind an empty string.
    """
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    title_text = root.findtext(".//dc:title", namespaces=ns) or ""
    seen: set[int] = set()
    for m in _PLAW_TITLE_SCOPE_RE.finditer(title_text):
        items = re.split(r"[,\s]+(?:and\s+)?", m.group("items"))
        for token in items:
            token = token.strip()
            if token.isdigit():
                seen.add(int(token))
    if len(seen) == 1:
        return str(seen.pop())
    return ""


# ---------------------------------------------------------------------------
# Effective-date extraction
# ---------------------------------------------------------------------------


def _has_effective_date_phrase(text: str) -> bool:
    """True when ``text`` contains a recognizable effective-date drafting phrase."""
    lowered = text.lower()
    if "effective" not in lowered and "take effect" not in lowered:
        return False
    return (
        _EFFECTIVE_OR_TAKE_EFFECT_AFTER_RE.search(text) is not None
        or _EFFECTIVE_OR_TAKE_EFFECT_ON_RE.search(text) is not None
        or _EFFECTIVE_ABSOLUTE_RE.search(text) is not None
    )


def _parse_after_enactment_match(m: re.Match[str], enacted: str) -> str:
    """Return ISO date for a "N unit(s) after (base/enactment)" regex match."""
    n = int(m.group("n"))
    unit = m.group("unit").lower()
    if m.group("base_year") is not None:
        month = _MONTHS.get(m.group("base_month").lower())
        if month is None:
            return ""
        base_date = _build_date_or_none(
            int(m.group("base_year")), month, int(m.group("base_day"))
        )
        if base_date is None:
            return ""
        return _add_calendar_delta(base_date, n, unit).isoformat()
    if not enacted:
        return ""
    base_date = _parse_iso_date_or_none(enacted)
    if base_date is None:
        return ""
    return _add_calendar_delta(base_date, n, unit).isoformat()


def _parse_effective_date(text: str, enacted: str) -> str:
    """Return an ISO effective date if ``text`` carries a future-effective prefix.

    Recognises "Effective on the date that is N year(s)/month(s)/day(s) after the
    date of enactment of this Act" plus a fixed absolute date. Returns "" when no
    date phrase is found.
    """
    lowered = text.lower()
    if "effective" not in lowered and "take effect" not in lowered:
        return ""
    m = _EFFECTIVE_OR_TAKE_EFFECT_AFTER_RE.search(text)
    if m is not None:
        return _parse_after_enactment_match(m, enacted)
    if _EFFECTIVE_OR_TAKE_EFFECT_ON_RE.search(text) is not None:
        if not enacted:
            return ""
        parsed = _parse_iso_date_or_none(enacted)
        return parsed.isoformat() if parsed else ""
    m = _EFFECTIVE_ABSOLUTE_RE.search(text)
    if m is not None:
        month = _MONTHS.get(m.group("month").lower())
        if month is None:
            return ""
        parsed = _build_date_or_none(
            int(m.group("year")), month, int(m.group("day"))
        )
        return parsed.isoformat() if parsed else ""
    return ""


def _is_sunset_language(text: str) -> bool:
    """True when ``text`` resolves an expiry/sunset rather than a mere effective date."""
    lowered = text.lower()
    return any(
        term in lowered
        for term in (
            "sunset",
            "expir",
            "repeal",
            "cease",
            "terminate",
        )
    )


def _has_sunset_expiry_phrase(text: str) -> bool:
    """True when ``text`` contains a recognizable sunset/expiry date phrase."""
    lowered = text.lower()
    if "sunset" not in lowered and "effective" not in lowered:
        return False
    if not _is_sunset_language(text):
        return False
    return (
        _SUNSET_AFTER_ENACTMENT_RE.search(text) is not None
        or _SUNSET_ABSOLUTE_RE.search(text) is not None
    )


def _parse_sunset_expiry(text: str, enacted: str) -> str:
    """Return the sunset date prose describes, as ISO, or "".

    Recognises "effective on the date that is N year(s)/month(s)/day(s) after the
    date of enactment of this Act" and absolute forms.
    """
    if not _is_sunset_language(text):
        return ""
    m = _SUNSET_AFTER_ENACTMENT_RE.search(text)
    if m is not None:
        n = int(m.group("n"))
        unit = m.group("unit").lower()
        if not enacted:
            return ""
        base_date = _parse_iso_date_or_none(enacted)
        if base_date is None:
            return ""
        return _add_calendar_delta(base_date, n, unit).isoformat()
    m = _SUNSET_ABSOLUTE_RE.search(text)
    if m is not None:
        month = _MONTHS.get(m.group("month").lower())
        if month is None:
            return ""
        parsed = _build_date_or_none(
            int(m.group("year")), month, int(m.group("day"))
        )
        return parsed.isoformat() if parsed else ""
    return ""


def _paragraph_label(elem: ET.Element) -> str | None:
    """Return the printed num label of a paragraph/subsection (e.g. ``(a)``), or None."""
    num = elem.find("u:num", _NS)
    if num is not None and num.text:
        return num.text.strip()
    return None


def _ancestor_of_kind(
    elem: ET.Element, kind: str, xml_parent_of: dict[ET.Element, ET.Element | None]
) -> ET.Element | None:
    """Nearest ancestor of ``elem`` (including ``elem`` itself) whose tag is ``kind``."""
    current: ET.Element | None = elem
    while current is not None:
        if _localname(current.tag) == kind:
            return current
        current = xml_parent_of.get(current)
    return None


def _label_key(label: str) -> tuple[str, ...]:
    """Sortable token key for paragraph labels."""
    return tuple(label.lower())


def _label_token(label: str) -> str:
    """Strip surrounding parentheses/dashes from a printed label (e.g. ``(a)`` -> ``a``)."""
    return label.strip("().- \t")


# Module-scope regex for the "subsections (a) through (e)" range form used by
# sibling sunset/effective-scope collectors. Hoisted per AGENTS.md §2.4
# backtracking discipline — the two scope collectors are called per PLAW section
# and the pattern was duplicated at both call sites.
_SIBLING_SCOPE_RANGE_RE = re.compile(
    r"(?P<kind>subsections?|paragraphs?|subparagraphs?|clauses?)\s+"
    r"\((?P<start>[0-9A-Za-z]+)\)(?:\s+through\s+\((?P<end>[0-9A-Za-z]+)\))?",
    re.IGNORECASE,
)


def _collect_sibling_sunset_scopes(
    section: ET.Element,
    parent_of: dict[ET.Element, ET.Element | None],
    xml_parent_of: dict[ET.Element, ET.Element | None],
) -> dict[ET.Element, str]:
    """Map amendatory leaf units to any sibling 'Effective Date; Sunset' expiry text.

    Some PLAW sections contain one paragraph that says "the amendments made by
    subsections (a) through (e) ... sunset on <date>" while the actual amendatory
    leaves sit in subsections (a)-(e). This helper scans for such meta paragraphs,
    parses the expiry phrase, and stores the raw expiry text against the sibling
    leaf units that fall within the named range.

    The range is resolved at the named kind level: a temporal subparagraph that
    names "subsections (a) through (e)" covers subsections that are siblings of
    the nearest subsection ancestor, not every container labelled "a" elsewhere
    in the section (#83).
    """
    scopes: dict[ET.Element, str] = {}
    unit_tags = {"subsection", "paragraph", "subparagraph", "clause"}

    for sibling in section.iter():
        if _localname(sibling.tag) not in unit_tags:
            continue
        # Skip statutory text being inserted via <quotedContent>/<quotedText>:
        # those are the new law's text, not a PLAW provision (effective date,
        # sunset, application). Their use of "this section" / "effective date"
        # is statutory, not a temporal scope of the amending instruction.
        if _is_inside_quoted_content(sibling, xml_parent_of):
            continue
        shallow = _shallow_text(sibling, exclude=_amendatory_unit_children(sibling))
        if not _has_sunset_expiry_phrase(shallow):
            continue
        # Only consider units that themselves contain no amendingAction — true
        # temporal/meta paragraphs like "(f)(1) Effective date".
        if any(_localname(a.tag) == "amendingAction" for a in sibling.iter()):
            continue
        expiry_text = shallow
        range_match = _SIBLING_SCOPE_RANGE_RE.search(shallow)
        if range_match is None:
            continue
        kind = range_match.group("kind").rstrip("s").lower()
        start_label = range_match.group("start")
        end_label = range_match.group("end") or start_label
        start_key = _label_key(start_label)
        end_key = _label_key(end_label)
        ancestor = _ancestor_of_kind(sibling, kind, xml_parent_of)
        if ancestor is None:
            continue
        scope_parent = xml_parent_of.get(ancestor)

        for leaf, _ in list(parent_of.items()):
            leaf_ancestor = _ancestor_of_kind(leaf, kind, xml_parent_of)
            if leaf_ancestor is None or leaf_ancestor is ancestor:
                continue
            if xml_parent_of.get(leaf_ancestor) != scope_parent:
                continue
            label = _paragraph_label(leaf_ancestor)
            if label is None:
                continue
            clean = _label_token(label)
            if not clean:
                continue
            key = _label_key(clean)
            if start_key <= key <= end_key:
                scopes[leaf] = expiry_text
    return scopes


# Section-level effective-date scope ("The amendments made by this section ...") applies
# to every amendatory unit in the same PLAW section, even when no named kind/range is
# given. "This Act" scopes are treated the same way when the paragraph is a sibling of
# the amendatory leaves within one section.
_SECTION_EFFECTIVE_SCOPE_RE = re.compile(
    r"\bthis\s+(?:section|Act)\b",
    re.IGNORECASE,
)

# USLM tags that wrap statutory text being inserted/amended by an amendatory
# instruction.  A ``<subsection>`` inside one of these is the new law's text,
# not a PLAW-section provision (effective date, sunset, etc.); collecting it as
# an effective/sunset scope would mis-attribute the statutory text's "this
# section" / "effective date" phrases to the amendment instruction.
_QUOTED_TEXT_TAGS = frozenset({"quotedContent", "quotedText"})


def _is_inside_quoted_content(
    elem: ET.Element,
    xml_parent_of: dict[ET.Element, ET.Element | None],
) -> bool:
    """True when ``elem`` is a descendant of a ``quotedContent``/``quotedText`` element.

    Such elements contain the statutory text the amendment inserts — not the
    PLAW's own provisions (effective date, sunset, application). Their text may
    use "this section" and "effective date" in the *statutory* sense, which
    must not be read as the PLAW's temporal scope.
    """
    ancestor = xml_parent_of.get(elem)
    while ancestor is not None:
        if _localname(ancestor.tag) in _QUOTED_TEXT_TAGS:
            return True
        ancestor = xml_parent_of.get(ancestor)
    return False


def _collect_sibling_effective_scopes(
    section: ET.Element,
    parent_of: dict[ET.Element, ET.Element | None],
    xml_parent_of: dict[ET.Element, ET.Element | None],
) -> dict[ET.Element, str]:
    """Map amendatory leaf units to sibling 'Effective Date' scope text.

    Temporary acts often place one "Effective Date" paragraph next to the
    amendatory leaves (e.g. "The amendments made by subsections (a) through (e)
    shall take effect on the date on which ...").  When that paragraph does not
    reduce to a concrete ISO date, the leaves inherit the raw scope text so the
    temporal layer can mark the operation as pending a condition instead of
    guessing an immediate effective date.

    The range is resolved at the named kind level, so a temporal subparagraph that
    names "subsections (a) through (e)" covers subsection siblings of the nearest
    subsection ancestor rather than every container labelled "a" elsewhere in
    the section (#83).

    Falls back to section-level scopes: an "Effective Date" paragraph that says
    "The amendments made by this section ..." (or "... this Act ...") without a
    named range covers all amendatory leaf units in the same section.
    """
    scopes: dict[ET.Element, str] = {}
    unit_tags = {"subsection", "paragraph", "subparagraph", "clause"}

    for sibling in section.iter():
        if _localname(sibling.tag) not in unit_tags:
            continue
        # Skip statutory text being inserted via <quotedContent>/<quotedText>:
        # those are the new law's text, not a PLAW provision (effective date,
        # sunset, application). Their use of "this section" / "effective date"
        # is statutory, not a temporal scope of the amending instruction.
        if _is_inside_quoted_content(sibling, xml_parent_of):
            continue
        shallow = _shallow_text(sibling, exclude=_amendatory_unit_children(sibling))
        lowered = shallow.lower()
        if "effective date" not in lowered:
            continue
        # Only consider units that themselves contain no amendingAction — true
        # temporal/meta paragraphs like "(f)(1) Effective date".
        if any(_localname(a.tag) == "amendingAction" for a in sibling.iter()):
            continue
        range_match = _SIBLING_SCOPE_RANGE_RE.search(shallow)
        if range_match is not None:
            kind = range_match.group("kind").rstrip("s").lower()
            start_label = range_match.group("start")
            end_label = range_match.group("end") or start_label
            start_key = _label_key(start_label)
            end_key = _label_key(end_label)
            ancestor = _ancestor_of_kind(sibling, kind, xml_parent_of)
            if ancestor is None:
                continue
            scope_parent = xml_parent_of.get(ancestor)

            for leaf, _ in list(parent_of.items()):
                if leaf in scopes:
                    continue
                leaf_ancestor = _ancestor_of_kind(leaf, kind, xml_parent_of)
                if leaf_ancestor is None or leaf_ancestor is ancestor:
                    continue
                if xml_parent_of.get(leaf_ancestor) != scope_parent:
                    continue
                label = _paragraph_label(leaf_ancestor)
                if label is None:
                    continue
                clean = _label_token(label)
                if not clean:
                    continue
                key = _label_key(clean)
                if start_key <= key <= end_key:
                    scopes[leaf] = shallow
            continue

        # Section-level fallback: "amendments made by this section" or
        # "amendments made by this Act" covers every amendatory unit in this section.
        if _SECTION_EFFECTIVE_SCOPE_RE.search(shallow) is not None:
            for leaf, _ in list(parent_of.items()):
                if leaf not in scopes:
                    scopes[leaf] = shallow
    return scopes


# ref href / prose chain into the pinned LegalAddress segment kinds. This MUST stay
# aligned with ``source_tree._USC_LADDER`` (the split convention a target path is
# located against): subsection→paragraph→subparagraph→clause→subclause→item→sub-item.
_USC_LEVELS = (
    "subsection",
    "paragraph",
    "subparagraph",
    "clause",
    "subclause",
    "item",
    "sub-item",
)
_LEVEL_SUBSECTION = 0
_LEVEL_PARAGRAPH = 1
_LEVEL_SUBPARAGRAPH = 2
_LEVEL_CLAUSE = 3
_LEVEL_SUBCLAUSE = 4
_LEVEL_ITEM = 5
_LEVEL_SUBITEM = 6

# Strict canonical roman numeral (lowercase), used to tell an ambiguous single
# letter (``i``/``v``/``x``/``l``/``c``/``d``/``m`` are BOTH subsection letters and
# roman clause numerals) apart by position rather than by isolated token form. The
# round-trip canonicality (only ``i``/``ii``/``iv``... accepted, not ``iiii``) MUST
# match ``source_tree._ROMAN_RE`` so a target path types a token by the SAME roman
# convention the subsection split uses to type the node it locates against.
_CANON_ROMAN_RE = re.compile(r"^m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
_LOWER_ALPHA_RE = re.compile(r"^[a-z]+$")
_UPPER_ALPHA_RE = re.compile(r"^[A-Z]+$")


def _segment_level_candidates(label: str) -> tuple[int, ...]:
    """All USC ladder levels a bare positional ``label`` token can denote.

    Mirrors ``source_tree._marker_interpretations`` so a target-path segment is
    typed by the SAME convention the subsection split uses to type the node it is
    located against. A token is ambiguous when it can denote more than one level
    (``i`` = subsection-letter OR lowercase-roman clause); the descent walk in
    :func:`_type_usc_segment_chain` disambiguates by ladder position.
    """
    stripped = label.strip()
    # Digit-led (incl. compound "10A"/"51D") is always paragraph-level in USC.
    if stripped[:1].isdigit():
        return (_LEVEL_PARAGRAPH,)
    out: list[int] = []
    if _LOWER_ALPHA_RE.match(stripped) is not None:
        single = len(stripped) == 1
        doubled = len(stripped) == 2 and stripped[0] == stripped[1]
        is_roman = _CANON_ROMAN_RE.match(stripped) is not None
        if single:
            out.append(_LEVEL_SUBSECTION)
        if is_roman:
            out.append(_LEVEL_CLAUSE)
        if doubled:
            out.append(_LEVEL_ITEM)
    elif _UPPER_ALPHA_RE.match(stripped) is not None:
        single = len(stripped) == 1
        doubled = len(stripped) == 2 and stripped[0] == stripped[1]
        is_roman = _CANON_ROMAN_RE.match(stripped.lower()) is not None
        if single:
            out.append(_LEVEL_SUBPARAGRAPH)
        if is_roman:
            out.append(_LEVEL_SUBCLAUSE)
        if doubled:
            out.append(_LEVEL_SUBITEM)
    return tuple(out)


def _type_usc_segment_chain(labels: list[str], *, start_frontier: int = -1) -> list[tuple[str, str]]:
    """Type a run of bare positional ``labels`` as one strict USC ladder descent.

    A target address (and the ``in subsection (X)(Y)…`` anchor chain) is a single
    monotonic descent: each named sub-unit sits exactly one-or-more levels DEEPER
    than the one before it. So the level of each token is resolved against the
    running frontier (the deepest level placed so far), not by its isolated form —
    this is what fixes (1) leading single-roman letters (``983/i`` is subsection
    ``i``, NOT clause ``i``) and (2) out-of-ladder-order kinds (``i/2/D`` typed
    ``clause/paragraph/subparagraph`` instead of ``subsection/paragraph/subparagraph``).

    ``start_frontier`` is the deepest level already established by an inherited
    address prefix (``-1`` = only the section is fixed, so the first token may be a
    subsection). Tokens whose form does not name any known level are placed one
    level below the frontier WITHOUT inventing a label that is not present — the
    label text is preserved verbatim, only its (kind) is positional.
    """
    out: list[tuple[str, str]] = []
    frontier = start_frontier
    for label in labels:
        candidates = _segment_level_candidates(label)
        deeper = [lvl for lvl in candidates if lvl > frontier]
        if deeper:
            # Cleanest descent = the shallowest interpretation still below frontier.
            level = min(deeper)
        elif candidates:
            # No interpretation is below the frontier (the chain descended past this
            # token's natural level): keep descending by one rather than emit a path
            # that re-ascends, which could never match a split node.
            level = max(min(candidates), frontier + 1)
        else:
            # Unrecognised token form: one level deeper than the frontier.
            level = frontier + 1
        level = min(level, len(_USC_LEVELS) - 1)
        out.append((_USC_LEVELS[level], label.strip()))
        frontier = level
    return out


def _has_roman_ambiguous_subsection_head(address: LegalAddress) -> bool:
    """True when the address's FIRST sub-section segment is a roman-form letter.

    The source-tree subsection split flags a single roman-form subsection letter
    (``(i)``/``(v)``/``(x)``/...) as ambiguous between a new subsection and a clause
    and can mis-nest it, leaving a PHANTOM duplicate node at the same
    ``subsection:<roman>/...`` address (e.g. ``10 U.S.C. 284`` carries two
    ``subsection:i/...`` nodes after the split). Typing the target's leading ``(i)``
    as a subsection is correct for the real law, but a sub-section-scoped locate
    against the split would land on the phantom (first) node. This predicate lets a
    *precise-text* strike fall back to its match-text anchor (the strike's real
    locator) rather than risk that mislocation — it does NOT relax the path for
    whole-node ops, which genuinely need the located node.
    """
    for kind, label in address.path:
        if kind in ("title", "section"):
            continue
        # The first below-section segment decides the subsection identity.
        if kind != "subsection":
            return False
        clean = label.strip().lower()
        # Single letters that happen to be valid Roman numerals (c=100, d=500,
        # m=1000) are ordinary subsection labels in the USC; they are never the
        # small Roman clause numerals (i, v, x, ...) that trigger the phantom-
        # duplicate ambiguity. Keep the full ladder for d/c/m so the strike lands
        # in the right subsection instead of degrading to a whole-section match.
        if clean in ("c", "d", "m"):
            return False
        return _CANON_ROMAN_RE.match(clean) is not None
    return False


def _section_scoped(address: LegalAddress) -> LegalAddress:
    """Drop every below-section segment, leaving the bare ``title/section`` address."""
    head: list[tuple[str, str]] = []
    for kind, label in address.path:
        head.append((kind, label))
        if kind == "section":
            break
    return LegalAddress(path=tuple(head))


# ---------------------------------------------------------------------------
# Typed instruction + finding carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class USAmendatoryFinding:
    """Typed finding for an amendatory instruction we could not fully lower."""

    rule_id: str
    message: str
    statute_id: str
    instruction_id: str = ""
    target_phrase: str = ""
    target_href: str = ""
    raw_text: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "statute_id": self.statute_id,
            "instruction_id": self.instruction_id,
            "target_phrase": self.target_phrase,
            "target_href": self.target_href,
            "raw_text": self.raw_text,
        }


@dataclass(frozen=True)
class USAmendmentInstruction:
    """One lowered (or unlowered) amendatory instruction.

    ``status`` is ``accepted`` (op present and target resolved), ``unsupported``
    (form not lowerable; see ``finding``), or ``needs_review`` (lowered but the
    target or payload is partial / corroboration-only).
    """

    instruction_id: str
    status: str
    witness_rule_id: str
    action: str = ""
    target_phrase: str = ""
    target_href: str = ""
    target_address: LegalAddress | None = None
    operation: LegalOperation | None = None
    # Additional ops a single instruction lowers to (a range redesignation lowers
    # to one RENUMBER per member). ``operation`` is the first/primary op; these are
    # the rest, materialized in the same source order.
    extra_operations: tuple[LegalOperation, ...] = ()
    finding: USAmendatoryFinding | None = None
    parse_witness: ParseWitness | None = None
    raw_text: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "instruction_id": self.instruction_id,
            "status": self.status,
            "witness_rule_id": self.witness_rule_id,
            "action": self.action,
            "target_phrase": self.target_phrase,
            "target_href": self.target_href,
            "target_address": str(self.target_address) if self.target_address else "",
            "operation": _operation_jsonable(self.operation),
            "finding": self.finding.to_jsonable() if self.finding else None,
            "parse_witness_rule_id": self.parse_witness.rule_id if self.parse_witness else "",
            "raw_text": self.raw_text,
        }


@dataclass(frozen=True)
class USAmendatoryReport:
    """Lowered candidate ops + typed findings + witness-anchored coverage for one law."""

    statute_id: str
    enacted: str
    title_targets: tuple[str, ...]
    instructions: tuple[USAmendmentInstruction, ...]
    findings: tuple[USAmendatoryFinding, ...] = ()

    def operations(self) -> tuple[LegalOperation, ...]:
        out: list[LegalOperation] = []
        for i in self.instructions:
            if i.operation is not None:
                out.append(i.operation)
            out.extend(i.extra_operations)
        return tuple(out)

    def coverage(self) -> dict[str, Any]:
        total = len(self.instructions)
        lowered = sum(1 for i in self.instructions if i.operation is not None)
        accepted = sum(1 for i in self.instructions if i.status == "accepted")
        unsupported = sum(1 for i in self.instructions if i.status == "unsupported")
        needs_review = sum(1 for i in self.instructions if i.status == "needs_review")
        action_counts = Counter(i.action or "__none__" for i in self.instructions)
        witness_rule_counts = Counter(i.witness_rule_id for i in self.instructions)
        finding_rule_counts = Counter(f.rule_id for f in self.findings)
        return {
            "statute_id": self.statute_id,
            "enacted": self.enacted,
            "title_targets": sorted(self.title_targets),
            "instructions_total": total,
            "instructions_lowered": lowered,
            "instructions_accepted": accepted,
            "instructions_unsupported": unsupported,
            "instructions_needs_review": needs_review,
            "candidate_operations": lowered,
            "action_counts": dict(sorted(action_counts.items())),
            "witness_rule_counts": dict(sorted(witness_rule_counts.items())),
            "finding_rule_counts": dict(sorted(finding_rule_counts.items())),
            "findings_total": len(self.findings),
            "replay_claims": False,
            "candidate_claims": True,
        }

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "jurisdiction": "us_federal",
            "report_kind": "amendatory_candidates",
            "truth_claim": "candidate_legal_operations_not_replayed",
            "replay_claims": False,
            "candidate_claims": True,
            "coverage": self.coverage(),
            "instructions": [i.to_jsonable() for i in self.instructions],
            "findings": [f.to_jsonable() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Target address parsing (pinned USC LegalAddress convention)
# ---------------------------------------------------------------------------

# Container targets amend a whole chapter, subchapter or part (not an individual
# section). They are the parent chapeau for section-level structural inserts such
# as "inserting after section 7300 the following new section".
_CONTAINER_TARGET_RE = re.compile(
    r"(?:^|\b)(?P<kind>chapter|subchapter|part)\s+(?P<label>[0-9A-Za-z]+)"
    r"(?:\s+of\s+(?:chapter|subchapter|part)\s+[0-9A-Za-z]+)*"
    r"\s+of\s+title\s+(?P<title>\d+)",
    re.IGNORECASE,
)
# ref variants: /us/usc/t11/c6  /us/usc/t11/c6/schI  /us/usc/t11/ptI
_CONTAINER_HREF_RE = re.compile(
    r"^/us/usc/t(?P<title>\d+)(?:/(?P<container1>c[0-9A-Za-z]+|sch[0-9A-Za-z]+|pt[0-9A-Za-z]+))"
    r"(?:/(?P<container2>c[0-9A-Za-z]+|sch[0-9A-Za-z]+|pt[0-9A-Za-z]+))?$"
)


# "Section 362(c)(1) of title 11, United States Code" / "section 1325(b)(4) of
# title 11". Labels are bare tokens; segments after the section are parenthesized.
_PROSE_TARGET_RE = re.compile(
    r"(?:^|\b)[Ss]ection\s+"
    r"(?P<section>\d+[A-Za-z]*(?:[-‐‑‒–]\d+)?)"
    r"(?P<segments>(?:\s*\([0-9A-Za-z]+\))*)"
    r"\s+of\s+title\s+(?P<title>\d+)",
)
_SEGMENT_RE = re.compile(r"\(([0-9A-Za-z]+)\)")
# ref href: /us/usc/t11/s101/10A  or  /us/usc/t11/s362/c/1
_HREF_TARGET_RE = re.compile(
    r"^/us/usc/t(?P<title>\d+)/s(?P<section>\d+[A-Za-z]*(?:[-‐‑‒–]\d+)?)"
    r"(?P<rest>(?:/[^/]+)*)$"
)
# A RELATIVE prose target: "Section 3680(a)(3) of such title", "in section
# 3672(b)(2)(C)", "in subsection (d)". The title is NOT named — it is inherited
# from the enclosing instruction (a parent unit / the section ref). The leaf may
# be a bare "section X(...)" anchored mid-instruction ("in section X, by ...") or
# the head of the instruction ("Section X of such title is amended"). We capture
# the section number and any parenthesized sub-section segments. The match must be
# anchored at a word boundary so a stray "section 116 of title 18" cross-reference
# inside the inserted text is never mistaken for the amendment target — those
# carry the explicit "of title N" form handled by the absolute parser.
_RELATIVE_PROSE_TARGET_RE = re.compile(
    r"(?:^|\b)(?:in\s+)?[Ss]ection\s+"
    r"(?P<section>\d+[A-Za-z]*(?:[-‐‑‒–]\d+)?)"
    r"(?P<segments>(?:\s*\([0-9A-Za-z]+\))*)"
    r"(?:\s+of\s+such\s+title\b|\s+is\s+amended\b|\s*,|\s*[—–-])"
)


# A leading sub-section anchor in an instruction unit: "(1) in subsection (a),
# by inserting …", "in paragraph (3), by striking …". This refines an inherited
# section/sub-section address by ONE more level: the edit applies inside the named
# sub-unit, not the whole inherited node. Anchored at the unit head so a mid-prose
# cross-reference ("in paragraph (1) of section 1322") is not mistaken for the
# edit's own scope. Only the first such anchor is consumed.
#
# Case-insensitive because USLM chapeaux after an enumerator can be title-case
# ("(2) In subsection (b)—"); the captured kind is normalised to lowercase.
_LEADING_SUBUNIT_ANCHOR_RE = re.compile(
    r"^\s*(?:\([0-9A-Za-z]+\)\s*)?"
    r"in\s+(?P<kind>subsection|paragraph|subparagraph|clause|subclause)\s+"
    r"\((?P<label>[0-9A-Za-z]+)\)"
    r"(?P<more>(?:\s*\([0-9A-Za-z]+\))*)"
    # The anchor is terminated by a comma ("(i) in clause (ii), by striking …") OR a
    # list dash ("(A) in paragraph (1)(A)—(i) …" — an intermediate scope ancestor
    # whose own anchor heads a nested sub-instruction list). Both forms scope the
    # edit; the dash terminator is required so an intermediate ancestor's anchor is
    # accumulated, not dropped. A trailing terminator (not just "of …") still
    # prevents a mid-prose cross-reference ("in paragraph (1) of section 1322") from
    # being mistaken for the edit's own scope.
    r"\s*(?:,|[—–-])",
    re.IGNORECASE,
)


# Same shape as ``_LEADING_SUBUNIT_ANCHOR_RE`` but not anchored to the start of
# the string, used when an ancestor unit's chapeau carries the section target and
# a scope anchor later in the same prose ("Section X ... is amended, in subsection
# (a)—"). The terminator guard still keeps the match specific; we intentionally
# do not require a leading word boundary because the match is applied to the
# ancestor's own chapeau text, not arbitrary mid-prose.
_ANY_SUBUNIT_ANCHOR_RE = re.compile(
    r"in\s+(?P<kind>subsection|paragraph|subparagraph|clause|subclause)\s+"
    r"\((?P<label>[0-9A-Za-z]+)\)"
    r"(?P<more>(?:\s*\([0-9A-Za-z]+\))*)"
    r"\s*(?:,|[—–-])",
    re.IGNORECASE,
)


# A positional tail anchor: "in paragraph (9), in the matter following
# subparagraph (B), by striking 'or'".  The edit applies to the text after the
# named sub-unit inside the inherited container.  When that sub-unit is the last
# child, the target is effectively that sub-unit's trailing conjunction.
_MATTER_FOLLOWING_ANCHOR_RE = re.compile(
    r"in\s+the\s+matter\s+following\s+"
    r"(?P<kind>subsection|paragraph|subparagraph|clause|subclause)\s+"
    r"\((?P<label>[0-9A-Za-z]+)\)"
    r"\s*(?:,|—)",
    re.IGNORECASE,
)


def _apply_subunit_anchor_match(
    address: LegalAddress, match: re.Match[str]
) -> LegalAddress:
    """Apply a parsed ``in <kind> (label)[(Y)...]`` anchor to ``address``."""
    head_kind = match.group("kind").lower()
    head_label = match.group("label")
    # If the inherited address already terminates at this exact node, the anchor
    # is a restatement, not a descent. Skip it so the edit targets the right node.
    if address.path and address.path[-1] == (head_kind, head_label):
        return address
    segments: list[tuple[str, str]] = [(head_kind, head_label)]
    # Any further parenthesised tokens ("(a)(1)(A)") descend BELOW the prose verb's
    # level: thread the frontier from the named kind so they type by position.
    more = _SEGMENT_RE.findall(match.group("more") or "")
    if more:
        head_level = _USC_LEVELS.index(head_kind) if head_kind in _USC_LEVELS else 0
        segments.extend(_type_usc_segment_chain(more, start_frontier=head_level))
    refined = LegalAddress(path=(*address.path, *segments))

    # Handle positional tail anchor: "in the matter following subparagraph (B)".
    matter = _MATTER_FOLLOWING_ANCHOR_RE.search(match.string)
    if matter is not None:
        kind = matter.group("kind").lower()
        label = matter.group("label")
        if refined.path and refined.path[-1] != (kind, label):
            refined = LegalAddress(path=(*refined.path, (kind, label)))

    return refined


def _refine_with_leading_subunit_anchor(address: LegalAddress, raw_text: str) -> LegalAddress:
    """Append a leading "in subsection (X)[(Y)...]" anchor to ``address``.

    Returns ``address`` unchanged when the unit has no leading sub-unit anchor (the
    edit applies directly to the inherited node). This is what disambiguates two
    sibling ops "(1) in subsection (a), by inserting …" / "(2) in subsection (b),
    …" that otherwise collapse to the same section address (and double-apply at the
    section-text surface). The named sub-unit's USC kind is taken from the prose
    verb ("subsection"/"paragraph"/...) — the enacted language is authoritative.

    Also handles "in the matter following subparagraph (B)" positional tail
    scopes, refining the target to that sub-unit so a token-strike hits the right
    location instead of the first match in the parent container.

    When the address already ends at the anchor's level and label (a parent unit's
    prose resolved to that same node, and the intermediate ancestor merely restates
    it, e.g. "(A) in subsection (a), by inserting ..." under an inherited
    ``section:322/subsection:a``), the anchor does not descend further. This avoids
    the phantom path ``.../subsection:a/subsection:a``.
    """
    match = _LEADING_SUBUNIT_ANCHOR_RE.match(raw_text)
    if match is None:
        return address
    return _apply_subunit_anchor_match(address, match)


def _refine_with_any_subunit_anchor(address: LegalAddress, raw_text: str) -> LegalAddress:
    """Append the last "in subsection (X)[(Y)...]" anchor found anywhere in text.

    This is for ancestor chapeaux that carry the section target before the scope
    anchor ("Section X ... is amended, in subsection (a)—"). The last match is used
    so any earlier cross-reference ("except as provided in paragraph (1), ...")
    does not take precedence over the final list-introducing anchor.
    """
    last_match: re.Match[str] | None = None
    for match in _ANY_SUBUNIT_ANCHOR_RE.finditer(raw_text):
        last_match = match
    if last_match is None:
        return address
    return _apply_subunit_anchor_match(address, last_match)


def _label_level(label: str, index: int) -> str:
    """Infer the USC segment kind for a positional label at ladder position ``index``.

    USC labels are positional (subsection (a), paragraph (1), subparagraph (A),
    clause (i), subclause (I)). The label *form* alone is ambiguous — a single
    letter ``i``/``l``/``v``/``x`` is both a subsection letter AND a roman clause
    numeral — so the kind is resolved by ladder POSITION: a token at ``index`` sits
    one level below ``index - 1`` (the frontier). This is the single-token entry
    point onto the same descent typer the multi-segment parsers use, so a bare
    redesignation/anchor label types identically to a full target chain.
    """
    return _type_usc_segment_chain([label], start_frontier=index - 1)[0][0]


def parse_usc_container_target(phrase: str, href: str = "") -> LegalAddress | None:
    """Parse a container-level target phrase or href into a USC LegalAddress.

    Handles "Chapter 6 of title 11, United States Code",
    "Subchapter I of chapter 74 of such title", and href forms such as
    ``/us/usc/t11/c6`` or ``/us/usc/t11/c6/schI``. Returns ``None`` for
    section-level or unrecognized targets.
    """
    if phrase:
        m = _CONTAINER_TARGET_RE.search(phrase)
        if m is not None:
            title = m.group("title") or ""
            kind = m.group("kind").lower()
            if title:
                return LegalAddress(path=(("title", title), (kind, m.group("label"))))
    if href:
        m = _CONTAINER_HREF_RE.match(href.strip())
        if m is not None:
            title = m.group("title")
            path: list[tuple[str, str]] = [("title", title)]
            for raw in (m.group("container1"), m.group("container2")):
                if not raw:
                    continue
                if raw.startswith("c"):
                    path.append(("chapter", raw[1:]))
                elif raw.startswith("sch"):
                    path.append(("subchapter", raw[3:]))
                elif raw.startswith("pt"):
                    path.append(("part", raw[2:]))
            return LegalAddress(path=tuple(path))
    return None


def parse_usc_target_phrase(phrase: str) -> LegalAddress | None:
    """Parse a prose amendment target phrase into the pinned USC LegalAddress.

    Returns ``None`` when the phrase is not a "Section X(...) of title N" form.
    """
    match = _PROSE_TARGET_RE.search(phrase)
    if match is None:
        return None
    title = match.group("title")
    section = match.group("section")
    path: list[tuple[str, str]] = [("title", title), ("section", section)]
    segments = _SEGMENT_RE.findall(match.group("segments") or "")
    path.extend(_type_usc_segment_chain(segments))
    return LegalAddress(path=tuple(path))


def parse_relative_usc_target(phrase: str, *, inherited_title: str) -> LegalAddress | None:
    """Parse a relative target ("section X(...) of such title") under ``inherited_title``.

    Returns ``None`` when the phrase carries no bare "section X" head and no
    inherited title is known. Used for the nested-instruction-list threading: a
    leaf unit ("(B) in section 3675(b)(3), by striking ...") names its USC section
    in prose but inherits the title from the enclosing instruction. Never invents a
    title — if ``inherited_title`` is empty the relative target is unresolved.
    """
    if not inherited_title:
        return None
    match = _RELATIVE_PROSE_TARGET_RE.search(phrase)
    if match is None:
        return None
    section = match.group("section")
    path: list[tuple[str, str]] = [("title", inherited_title), ("section", section)]
    segments = _SEGMENT_RE.findall(match.group("segments") or "")
    path.extend(_type_usc_segment_chain(segments))
    return LegalAddress(path=tuple(path))


def _address_title(address: LegalAddress | None) -> str:
    if address is None:
        return ""
    for kind, label in address.path:
        if kind == "title":
            return label
    return ""


def parse_usc_target_href(href: str) -> LegalAddress | None:
    """Parse a ``/us/usc/t{N}/s{section}/...`` ref href into a USC LegalAddress.

    Trailing ``/note``, ``/etseq`` and similar non-structural carriers are dropped
    (they are citation facets, not addressable sub-structure).
    """
    match = _HREF_TARGET_RE.match(href.strip())
    if match is None:
        return None
    path: list[tuple[str, str]] = [
        ("title", match.group("title")),
        ("section", match.group("section")),
    ]
    rest = match.group("rest") or ""
    segments = [seg for seg in (s for s in rest.split("/") if s) if seg not in ("note", "etseq", "et_seq")]
    # The href path order IS the USC ladder order: build the FULL chain (never drop
    # an intervening level) and type each segment by its descent position, not by
    # the isolated token form (so ``/s2261A/b/1/A/ii`` keeps every level and a
    # leading ``/s983/i`` is subsection ``i``, not a roman clause).
    path.extend(_type_usc_segment_chain(segments))
    return LegalAddress(path=tuple(path))


# ---------------------------------------------------------------------------
# Lowering an instruction section
# ---------------------------------------------------------------------------


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# USLM elements that carry an inline AMENDATORY OPERAND literal — the struck /
# inserted / replacement string — not the instruction's own target locator. A
# ``<ref>`` or "section X of title N" prose that lives INSIDE one of these is part
# of the quoted operand (a cross-reference being struck or inserted as text), NOT
# the amendment target. Resolving the target off such a buried ref silently
# hijacks the unit onto the wrong section (the operand's cited section instead of
# the section actually being amended): e.g. ``inserting "...section 2313(a)(2) of
# title 10..." before "..."`` is an edit to a *free-standing Act* whose inserted
# literal merely cites title-10 §2313 — lowering it as a title-10 §2313 edit is a
# misextraction. Target scanning must skip these subtrees.
_NON_TARGET_REF_CONTAINER_TAGS = frozenset({"quotedText", "quotedContent", "sidenote"})

# Parenthetical sidenote refs (e.g. "<sidenote><p><ref href="/us/usc/t38/s117">38 USC
# 117</ref>.</p></sidenote>") are publisher cross-references, not amendatory targets.
# Treating them as the unit's own USC target would hijack "Section 117(c) is amended"
# onto the bare section 117 and drop the subsection/paragraph scope named in the text.
# They are excluded from ``_first_usc_ref`` but remain available to
# ``_section_classification_pairs`` for title inference.


def _text_of(elem: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(elem.itertext())).strip()


# Curly/straight quote marks the USLM wraps around an inline literal in the
# *prose* (siblings of <quotedText>, never inside it). Only an enclosing matched
# pair is peeled — never edge punctuation that is part of the literal.
_ENCLOSING_QUOTE_PAIRS = (("“", "”"), ('"', '"'))


def _collapse_inner_ws(text: str) -> str:
    """Collapse runs of insignificant formatting whitespace WITHOUT touching edges.

    USLM serializes block payloads with newline/indentation whitespace between
    child elements. We collapse internal runs to a single space so the materialized
    literal reads as one line, but we never strip the literal's own leading/trailing
    characters — those are whitespace- and punctuation-significant (F1/F4).
    """
    # Preserve a single leading / trailing whitespace char (a significant space),
    # collapse everything internal. Edge whitespace beyond one char is XML
    # serialization noise (e.g. trailing "\n\n" after a block) and is trimmed.
    lead = " " if text[:1].isspace() else ""
    trail = " " if text[-1:].isspace() and len(text) > 1 else ""
    return lead + re.sub(r"\s+", " ", text.strip()) + trail


def _peel_enclosing_quotes(text: str) -> str:
    """Remove a single matched enclosing quote pair, preserving inner edge chars.

    ``“(d) … becomes due.”`` -> ``(d) … becomes due.`` (the terminal period stays
    INSIDE; only the wrapping curly quotes are peeled). A literal with no enclosing
    pair is returned unchanged.
    """
    for open_q, close_q in _ENCLOSING_QUOTE_PAIRS:
        if text.startswith(open_q) and text.endswith(close_q) and len(text) >= len(open_q) + len(close_q):
            return text[len(open_q) : len(text) - len(close_q)]
    return text


# USC structural units whose leading token is meaningful at the law-text surface.
# When a whole-node REPLACE payload omits the token of the unit it targets, we
# reconstitute it from the address. The token is not "guessed": the source itself
# named the unit (e.g. "subparagraph (B)"), and the quotedContent merely supplies
# the body without repeating the label. This is the SBRA §101(51D)(B) form, and
# keeping the label preserves address continuity with the live tree.
_STRUCTURAL_UNIT_KINDS = frozenset(
    {"subsection", "paragraph", "subparagraph", "clause", "subclause", "item", "sub-item"}
)
RULE_RECONSTITUTED_TARGET_LABEL = "us_amend_reconstituted_target_label"


def _target_unit_label(address: LegalAddress) -> str | None:
    """Return the label of the deepest structural unit in ``address``.

    Returns ``None`` for a section-level or unresolved address.
    """
    for kind, label in reversed(address.path):
        if kind in _STRUCTURAL_UNIT_KINDS:
            return label
    return None


def _reconstitute_target_label(
    payload_text: str | None,
    target_address: LegalAddress,
) -> tuple[str, bool]:
    """Ensure a whole-node REPLACE payload carries the target unit's leading token.

    Some structural strike-insert instructions frame the quotedContent as the body
    of the target unit only, omitting the repeated ``(B)``/``(i)`` token (e.g. SBRA
    §101(51D)(B): "does not include— (i) any member"). The unit's identity is fixed
    by the source target address, so we prepend the token when it is missing. The
    original payload text is returned unchanged when it already opens with the token
    or when the target is not a structural unit below the section.

    Returns ``(normalized_text, normalized)``.
    """
    if not payload_text:
        return payload_text or "", False
    label = _target_unit_label(target_address)
    if not label:
        return payload_text, False
    stripped = payload_text.lstrip()
    body = _peel_enclosing_quotes(stripped)
    expected = f"({label})"
    if body.lstrip().startswith(expected):
        return payload_text, False
    return f"{expected} {stripped.lstrip()}", True


# Characters that BEGIN a fresh token when they open an inserted phrase. Inserting
# a phrase "after" a word splices it into the running prose, so a new token must be
# whitespace-separated from the anchor word: a letter, digit, or an opening bracket
# all start a new word/clause.
_INSERT_TOKEN_START = "([{"
# Characters that END a fresh token at the boundary when the inserted phrase is
# placed BEFORE an anchor word. A closing parenthesis/bracket/brace (as in a label
# like "(i)") separates from the following word just as a word character does.
_INSERT_TOKEN_END = ")]}"
# Terminal punctuation on the LEFT side of an "insert X after Y" boundary also
# ends a fresh element: e.g. "...Service," followed by "Members..." materializes as
# "...Service, Members..." because the comma terminates the anchor clause and the
# inserted clause begins a new element.
_INSERT_TOKEN_TERMINAL = ",.;:!?)]}"


def _join_insert_after(anchor: str, insert: str) -> str:
    """Assemble the "insert ``insert`` after ``anchor``" replacement faithfully.

    Legislative "inserting 'X' after 'Y'" places X immediately after the anchor word
    Y in the consolidated body. When Y ends in a word character and X opens a fresh
    token (letter/digit/opening bracket), the OLRC body renders a single separating
    space at the junction — ``President`` + ``or the Secretary…`` materializes as
    ``President or the Secretary…`` (the enacted result, not an invented space). The
    space is added ONLY at a genuine word↔word (or word↔open-bracket) junction:

    - if either operand already supplies edge whitespace, nothing is added (the
      enacted text already carries the boundary — never double it);
    - if the insert opens with attaching punctuation (``,`` ``.`` ``)`` …) it binds
      to the anchor word with NO space (``trade or business`` + ``, and`` →
      ``…business, and``);
    - if the anchor ends in a non-word char (e.g. ``(``) no space is forced either.

    This reflects what the slip-law instruction means for the consolidated surface;
    it never inserts a separator the enacted text would not contain.
    """
    if not anchor or not insert:
        return anchor + insert
    a, b = anchor[-1], insert[0]
    if a.isspace() or b.isspace():
        return anchor + insert
    left_ends_token = a.isalnum() or a in _INSERT_TOKEN_TERMINAL
    right_starts_token = b.isalnum() or b in _INSERT_TOKEN_START
    if left_ends_token and right_starts_token:
        return anchor + " " + insert
    return anchor + insert


_PUNCTUATION_WORD_MAP: dict[str, str] = {
    "semicolon": ";",
    "comma": ",",
    "period": ".",
}


_INSERT_WORD_ANCHOR_DIRECTION_RE = re.compile(
    r"inserting\s+["
    "\"\u201c\u201d"
    r"][^\"\u201d]{0,400}[\"\u201d]\s+"
    r"(?P<where>before|after)\s+"
    r"[\"\u201c][^\"\u201d]{0,400}[\"\u201d]",
    re.IGNORECASE,
)


def _insert_word_anchor_direction(raw_text: str) -> str:
    """Return ``before`` or ``after`` for an "inserting 'X' before/after 'Y'" formula."""
    m = _INSERT_WORD_ANCHOR_DIRECTION_RE.search(raw_text)
    if m is None:
        return "after"
    return m.group("where").lower().strip()


def _join_insert_before(anchor: str, insert: str) -> str:
    """Assemble the "insert ``insert`` before ``anchor``" replacement faithfully.

    Legislative "inserting 'X' before 'Y'" places X immediately before the anchor word
    Y.  The boundary between X and Y needs a separating space when the inserted phrase
    ends in a word/label token and the anchor begins a fresh token; attaching
    punctuation and pre-supplied edge whitespace take none.
    """
    if not anchor or not insert:
        return insert + anchor
    a, b = insert[-1], anchor[0]
    if a.isspace() or b.isspace():
        return insert + anchor
    if (a.isalnum() or a in _INSERT_TOKEN_END) and (b.isalnum() or b in _INSERT_TOKEN_START):
        return insert + " " + anchor
    return insert + anchor


def _punctuation_word_to_char(word: str | None) -> str | None:
    """Map the prose punctuation word to its character, or None."""
    if word is None:
        return None
    return _PUNCTUATION_WORD_MAP.get(word.strip().lower())


def _end_punctuation_char(name: str | None) -> str | None:
    """Map a positional punctuation name ('period', 'semicolon', 'comma') to char."""
    if name is None:
        return None
    return _PUNCTUATION_WORD_MAP.get(name.strip().lower())


def _quoted_texts(elem: ET.Element) -> list[str]:
    out: list[str] = []
    for q in elem.iter():
        if _localname(q.tag) == "quotedText":
            # Significant leading/trailing whitespace and punctuation INSIDE the
            # <quotedText> literal must survive (F1 leading space, F4 terminal
            # period). Editorial page-break/sidenote stamps embedded inside the
            # quoted literal are not enacted text and are pruned.
            text = _itertext_excluding_sidenotes(q)
            out.append(_collapse_inner_ws(text))
    return out


def _amending_actions(elem: ET.Element) -> list[str]:
    out: list[str] = []
    for a in elem.iter():
        if _localname(a.tag) == "amendingAction":
            out.append((a.get("type") or "").strip())
    return out


# govinfo PLAW USLM interleaves the legislative-counsel marginal sidenotes
# (the topical/effective-date markers "Time period.", "Definitions.", "Deadline.",
# "Effective date.", page-break "134 STAT. ..." stamps) as small-font ``<p>``
# elements inside ``<quotedContent>``. These are EDITORIAL marginalia, not enacted
# statutory text — the OLRC consolidated USC body never renders them. They are
# distinguished by their ``fontsize8`` paragraph class (the small marginal-note
# font; real enacted body is ``fontsize10``/``fontsize12``). Excluding them from
# the materialized payload is a faithfulness fix, not a comparison hack: a quoted
# block that pulls "(2) Time period.A plan ..." into the body is materializing
# sidenote text the statute does not contain.
_EDITORIAL_SIDENOTE_CLASS = "fontsize8"
# USLM ``<page>`` elements are the Statutes-at-Large page-break stamps
# ("134 STAT. 3219") govinfo injects between body runs. Like the sidenotes they
# are editorial pagination, never enacted statutory text, and are pruned.
_EDITORIAL_PRUNE_TAGS = frozenset({"page"})


def _is_editorial_sidenote(elem: ET.Element) -> bool:
    if _localname(elem.tag) in _EDITORIAL_PRUNE_TAGS:
        return True
    cls = elem.get("class", "")
    return _EDITORIAL_SIDENOTE_CLASS in cls.split()


def _itertext_excluding_sidenotes(elem: ET.Element) -> str:
    """Concatenated descendant text of ``elem`` with editorial sidenotes pruned.

    Mirrors :meth:`Element.itertext` but skips the subtree of any element that is a
    legislative-counsel marginal sidenote (``fontsize8`` ``<p>``), and skips that
    element's *text* while keeping its *tail* (the tail belongs to the parent's
    text flow, not the sidenote). The statutory body text is preserved verbatim.
    """
    parts: list[str] = []

    def _walk(node: ET.Element, *, emit_own_text: bool) -> None:
        if emit_own_text and node.text:
            parts.append(node.text)
        for child in node:
            if _is_editorial_sidenote(child):
                # Drop the sidenote subtree entirely, but keep its tail text (which
                # is the surrounding statutory flow that follows the marginal note).
                if child.tail:
                    parts.append(child.tail)
                continue
            _walk(child, emit_own_text=True)
            if child.tail:
                parts.append(child.tail)

    _walk(elem, emit_own_text=True)
    return "".join(parts)


def _quoted_content_node(elem: ET.Element) -> IRNode | None:
    """Build an IRNode payload from the first ``<quotedContent>`` block, if any."""
    for q in elem.iter():
        if _localname(q.tag) == "quotedContent":
            # Collapse internal formatting whitespace and trim the block's outer
            # serialization whitespace (newlines/indent around <quotedContent> are
            # NOT significant), then peel ONLY the enclosing curly-quote pair. The
            # terminal punctuation (period) lives INSIDE the quote and must survive
            # (F4: "…becomes due." not "…becomes due"). Editorial marginal sidenotes
            # (fontsize8 ``<p>``: "Time period.", "Definitions.", page stamps) are
            # pruned — they are not enacted statutory text.
            collapsed = re.sub(r"\s+", " ", _itertext_excluding_sidenotes(q)).strip()
            text = _peel_enclosing_quotes(collapsed)
            # We carry the quoted block verbatim as a single content node; the
            # dry-run stage re-parses the USLM sub-tree into structured law.
            return IRNode(kind=IRNodeKind.CONTENT, text=text)
    return None


def _direct_target_title(target_phrase: str, target_href: str) -> str:
    """The title the unit's OWN absolute prose / href would resolve to, or "".

    Used to decide whether the unit's own direct target lands on a non-positive
    title — in which case resolution is routed through the act-section→USC
    non-positive resolver (which enforces the uncodified/note-only holdout and the
    pinned IRC sub-segment typing). The inherited / relative-prose channels are NOT
    consulted here: those thread a title a parent already resolved and are handled
    by the direct positive-law path unchanged.
    """
    prose_addr = parse_usc_target_phrase(target_phrase) if target_phrase else None
    if prose_addr is not None:
        return _address_title(prose_addr)
    href_addr = parse_usc_target_href(target_href) if target_href else None
    if href_addr is not None:
        return _address_title(href_addr)
    container_addr = (
        parse_usc_container_target(target_phrase, target_href or "") if (target_phrase or target_href) else None
    )
    if container_addr is not None:
        return _address_title(container_addr)
    return ""


def _resolve_target(
    target_phrase: str,
    target_href: str,
    *,
    raw_text: str = "",
    inherited_address: LegalAddress | None = None,
    plaw_title_scope: str = "",
) -> tuple[LegalAddress | None, str]:
    """Resolve the instruction target; prose is canonical, href corroborates.

    Returns ``(address, resolution_status)`` where status is one of
    ``prose``, ``href``, ``prose_href_agree``, ``nonpositive_<status>``,
    ``relative_prose``, ``inherited``, or ``unresolved``.

    Resolution order (each strictly more specific than the next):

    0. NON-POSITIVE TITLE ROUTING. When the unit's own absolute prose / href lands
       on one of the 24 non-positive-law USC titles (Title 7, 15, 20, 26, 42, …),
       the enacted target names a free-standing Act ("Section 5 of the Securities
       Act of 1933 (15 U.S.C. 77e)") and the codified address comes from the
       govinfo USLM classification carried in the inline ``(N U.S.C. M)``
       parenthetical and the structural ``<ref>`` href. We route through
       :func:`lawvm.us_federal.nonpositive.resolve_nonpositive_target`, which
       enforces the Prime Directive at the lowering boundary: a ``note``-only / et
       seq. target (an UNCODIFIED Statutes-at-Large note) is held OUT (resolves to
       ``unresolved``, never guessed onto a codified section), and the IRC
       single-letter subsection (``(l)``) is typed by nesting position rather than
       as a roman-numeral clause. Only the unit's OWN target_phrase / target_href
       are consulted (NOT the raw_text), so a stray ``(N U.S.C. M)`` cross-citation
       inside the instruction body can never hijack the target.
    1. The unit's own absolute prose / href ("Section X(...) of title N").
    2. The unit's own RELATIVE prose ("section X(...) of such title" / "in section
       X, by ...") combined with the title inherited from the enclosing
       instruction — this threads the nested-instruction-list form where the leaf
       names its USC section in prose but inherits the title from a parent unit.
    3. The inherited target itself ("(1) by striking ..." with no ref of its own
       inherits the parent unit's resolved section address verbatim).
    4. The PLAW's own short-title preamble (the ``dc:title`` metadata) when it
       names exactly one USC title and the instruction names a section but no
       title.

    The relative/inherited/metadata steps NEVER invent a title; they only carry
    one that an enclosing instruction or the enacted preamble already resolved
    (no silent target hijack).
    """
    # Local import: ``nonpositive`` imports lowering primitives from this module at
    # its top level, so a module-level import here would be circular. The resolver
    # is pure and cheap to reach lazily.
    from lawvm.us_federal import nonpositive

    # (0) Non-positive title: route the unit's own direct target through the
    # act-section→USC resolver. Only fires when the unit's own prose/href lands on
    # a non-positive title; inherited / relative-prose resolutions are left to the
    # direct path below (a leaf with no own ref keeps inheriting its parent's
    # already-resolved address). A non-positive unit whose only codified channel is
    # a note cross-ref resolves to ``unresolved`` here (a typed holdout finding
    # downstream), never a guessed codified section.
    direct_title = _direct_target_title(target_phrase, target_href)
    if direct_title and not nonpositive.is_positive_law_title(int(direct_title)):
        witness = nonpositive.resolve_nonpositive_target(
            target_phrase=target_phrase,
            target_href=target_href,
        )
        if witness.address is not None:
            return witness.address, f"nonpositive_{witness.status}"
        # No codified channel for this non-positive target (note-only / unmapped):
        # held out as the uncodified residual, never guessed onto a section or
        # container through the positive-law target parser.
        return None, "unresolved"

    prose_addr = parse_usc_target_phrase(target_phrase) if target_phrase else None
    href_addr = parse_usc_target_href(target_href) if target_href else None
    container_addr = (
        parse_usc_container_target(target_phrase, target_href or "") if (target_phrase or target_href) else None
    )
    # Section target always beats container target (more specific). If both prose
    # and href give the same path, report agreement.
    if prose_addr is not None and href_addr is not None:
        if prose_addr.path == href_addr.path:
            return prose_addr, "prose_href_agree"
        # Prose is canonical (the enacted language); href is a converter artifact.
        return prose_addr, "prose"
    if prose_addr is not None:
        return prose_addr, "prose"
    if href_addr is not None:
        return href_addr, "href"
    if container_addr is not None:
        return container_addr, "container"

    # Strip the quoted amendment operands before inferring a target from the prose;
    # a USC cross-reference inside the text being struck or inserted is part of the
    # payload, not the target address. The unquoted prefix still carries "Section X",
    # "in section X", and "in subsection (X)" target/anchor cues.
    raw_prefix = re.split(r'["“]', raw_text, maxsplit=1)[0] if raw_text else ""

    # (2) Relative prose under the inherited title. The leaf names a different
    # section than the inherited address (a conforming amendment to a sibling
    # section), so the section comes from the leaf's prose, the title from the
    # inherited address.
    inherited_title = _address_title(inherited_address)
    if raw_prefix and inherited_title:
        rel = parse_relative_usc_target(raw_prefix, inherited_title=inherited_title)
        if rel is not None:
            return _refine_with_leading_subunit_anchor(rel, raw_prefix), "relative_prose"

    # (3) Pure inheritance: the leaf carries no section of its own; it amends the
    # same node the enclosing instruction resolved — refined by any leading
    # "in subsection (X)" anchor so sibling sub-unit edits do not collapse onto the
    # same address (and double-apply at the section-text surface).
    if inherited_address is not None:
        return (
            _refine_with_leading_subunit_anchor(inherited_address, raw_prefix),
            "inherited",
        )

    # (3.5) Plain-prose target stated in the instruction text when no <ref>/phrase
    # was extracted (converter-flattened sections may still carry "Section X of title N"
    # as ordinary text). Non-positive titles are routed through the act-section
    # resolver rather than treated as direct USC addresses.
    if raw_prefix:
        direct_from_raw = parse_usc_target_phrase(raw_prefix)
        if direct_from_raw is not None:
            raw_title = _address_title(direct_from_raw)
            if raw_title and not nonpositive.is_positive_law_title(int(raw_title)):
                witness = nonpositive.resolve_nonpositive_target(
                    target_phrase=raw_prefix,
                    target_href="",
                )
                if witness.address is not None:
                    return witness.address, f"nonpositive_{witness.status}"
                return None, "unresolved"
            return _refine_with_leading_subunit_anchor(direct_from_raw, raw_prefix), "prose"

    # (4) PLAW short-title metadata scope: the enacted preamble names exactly one
    # USC title, and the instruction itself names a section but no title. This is an
    # authoritative source lane, used only when no explicit title or inherited title
    # was available.
    if plaw_title_scope and raw_prefix:
        meta_addr = parse_relative_usc_target(raw_prefix, inherited_title=plaw_title_scope)
        if meta_addr is not None:
            return _refine_with_leading_subunit_anchor(meta_addr, raw_prefix), "metadata_title"

    return None, "unresolved"


# Surface-prose fallback for the repeal action family. The typed
# <amendingAction type="repeal"> element is the primary authority; this regex
# is a prefilter fallback when the typed element is absent (AGENTS.md §1.11).
# Hoisted per AGENTS.md §2.4 backtracking discipline and routed through
# compile_classifier_regex (AGENTS.md §2.4 safety lint + prefilter).
_IS_REPEALED_PROSE_RE = compile_classifier_regex(
    r"\bis\s+repealed\b",
    re.IGNORECASE,
    classifier_id="us_amendatory_is_repealed_prose",
)


def _classify_action(actions: list[str], raw_text: str) -> str:
    """Map the amendingAction verb sequence / prose to a canonical family token."""
    has = set(actions)
    lowered = raw_text.lower()
    if "repeal" in has or _IS_REPEALED_PROSE_RE.search(lowered) is not None:
        return "repeal"
    if "redesignate" in has or "redesignat" in lowered:
        return "redesignate"
    if ("amend" in has and "to read" in lowered) or "to read as follows" in lowered:
        return "amend_to_read"
    has_strike = "delete" in has or "striking" in lowered
    has_insert = "insert" in has or "inserting" in lowered
    has_anchor = " after " in lowered or " before " in lowered
    # Terminal punctuation edits where the anchor is positional ("the period at the
    # end") or the replacement is a punctuation word ("inserting a semicolon") need
    # last-occurrence / punctuation mapping. Detect them before the generic strike-
    # insert branch so the strike_insert branch can apply the correct rule.
    if has_strike and has_insert:
        if _END_PUNCT_STRIKE_INSERT_RE.search(raw_text) is not None:
            return "strike_insert_end_punct"
        if _PUNCT_WORD_RE.search(raw_text) is not None:
            return "strike_insert_punct_word"
    if has_insert and has_anchor and not has_strike:
        if _END_PUNCT_INSERT_RE.search(raw_text) is not None:
            return "insert_end_punct"
    # "inserting 'X' after/before 'Y'" with NO striking is an anchored insert, not
    # a strike-and-insert. Classify it as insert_after BEFORE the strike_insert and
    # add_at_end branches so the anchor (not a struck phrase) drives the operand
    # assignment (F5: PL 116-54 §547(b) was mis-read as strike_insert with inverted
    # operands because a sibling subsection's "striking" bled into the raw text).
    if has_insert and has_anchor and not has_strike:
        return "insert_after"
    # Genuine strike-and-insert: an explicit strike verb paired with an insert.
    if has_strike and has_insert:
        return "strike_insert"
    if "add" in has and "at the end" in lowered:
        return "add_at_end"
    if has_strike:
        return "strike"
    if has_insert and has_anchor:
        return "insert_after"
    if "add" in has or "insert" in has:
        return "add_at_end"
    return "unknown"


def _redesignate_destination(raw_text: str, target: LegalAddress) -> tuple[LegalAddress, LegalAddress] | None:
    """Parse ``redesignating X as Y`` into ``(from, to)`` addresses (single-unit form)."""
    m = re.search(
        r"redesignating\s+(?:subsection|paragraph|subparagraph|clause|subclause)\s+"
        r"\(([0-9A-Za-z]+)\)\s+as\s+"
        r"(?:subsection|paragraph|subparagraph|clause|subclause)\s+\(([0-9A-Za-z]+)\)",
        raw_text,
        re.IGNORECASE,
    )
    if m is None:
        return None
    from_label, to_label = m.group(1), m.group(2)
    parent = target  # target already resolves to the enclosing section/subsection
    leaf_index = max(parent.depth() - 2, 0)
    from_kind = _label_level(from_label, leaf_index)
    from_addr = LegalAddress(path=(*parent.path, (from_kind, from_label)))
    to_addr = LegalAddress(path=(*parent.path, (from_kind, to_label)))
    return from_addr, to_addr


_KIND_WORDS = "subsection|paragraph|subparagraph|clause|subclause"
# Structural-action trailing punctuation in nested conforming-amendment lists:
# a unit's raw text is often "(A) by striking subsection (X); and" or "(B) by
# redesignating paragraphs ...;".  The recognizers below must consume the
# trailing list conjunction/terminator, not require the raw text to end exactly
# after the label.
_STRUCTURAL_ACTION_TRAIL = r"(?:\s*[.,;])?(?:\s*and\b\s*)?$"
_STRIKE_UNIT_RE = re.compile(
    rf"by\s+striking\s+(?P<kind>{_KIND_WORDS})\s+\((?P<label>[0-9A-Za-z]+)\)" + _STRUCTURAL_ACTION_TRAIL,
    re.IGNORECASE,
)
# A strike-subsection instruction with FUTURE-effective language ("Effective on
# the date that is N ... after …", "On the date that is ...", "Effective <date>,
# …", "shall take effect …") is a SUNSET / deferred repeal, not an in-window
# amendment. The temporal layer owns it; lowering it to an immediate REPEAL would
# (wrongly) delete a node that is still in force in the window's after edition.
# We refuse to lower these as immediate ops.
_FUTURE_EFFECTIVE_RE = re.compile(
    r"effective\s+(?:on\s+the\s+date|[A-Z][a-z]+\s+\d|\w+\s+\d{1,2},\s*\d{4})"
    r"|on\s+the\s+date\s+that\s+is\b"
    r"|(?:^|\W)(?:sunset|expires?|terminates?)\s+(?:on|after)\b"
    r"|shall\s+take\s+effect\b",
    re.IGNORECASE,
)
# Terminal punctuation edits where the struck anchor is described positionally.
# Pattern A: "striking the period [at the end [of paragraph (2)]] and inserting
# '<replacement>'" — the anchor is positional, the replacement is the quoted
# insertion. The optional "of (paragraph|subparagraph|...)(label)" clause names
# the sub-unit whose trailing punctuation is edited; when captured, the lowerer
# drills the target one level deeper (e.g. resolved `subsection (b)` +
# `of paragraph (2)` → op target `paragraph:2`) so the op anchors on the named
# child's terminal period, NOT the parent's. The nested chain form — `(1)(A)`,
# `(3)(B)(ii)` — is captured too but left for the handler to refuse (multi-level
# drilling cannot be safely resolved from prose alone). Quoted replacement is
# bounded to 300 chars (the enacted literal is often a one-clause insert, but a
# section reference like ", and (G) any assessments required under section 505B."
# runs ~60 chars — the original 20-char cap silently blocked those).
# Pattern B: "striking '<old>' at the end ... and inserting '<replacement>'".
_END_PUNCT_STRIKE_INSERT_RE = re.compile(
    r"(?:"
    r"striking\s+(?:the\s+)?(?P<struck_punct>period|semicolon|comma)"
    r"(?:\s+at\s+the\s+end)?"
    r"(?:\s+of\s+(?P<punct_subunit_kind>section|subsection|paragraph|subparagraph|clause|subclause)"
    r"\s+\((?P<punct_subunit_label>[0-9A-Za-z]+)\)(?P<punct_subunit_extra>(?:\([0-9A-Za-z]+\))*))?"
    r"\s+and\s+inserting\s+"
    r"(?:[\"“”'](?P<quoted_ins>[^\"“”']{0,300})[\"“”']|(?P<word_ins>a\s+(?:semicolon|comma|period)))"
    r"|striking\s+[\"“”'](?P<quoted_old_end>[^\"“”']{0,300})[\"“”']\s+at\s+the\s+end[^\"“”']{0,400}?and\s+inserting\s+[\"“”'](?P<quoted_new_end>[^\"“”']{0,300})[\"“”']"
    r")"
    + _STRUCTURAL_ACTION_TRAIL,
    re.IGNORECASE,
)
# Terminal punctuation insert: "inserting '<X>' before/after the period at the end".
_END_PUNCT_INSERT_RE = re.compile(
    r"inserting\s+[\"“”'](?P<ins>[^\"“”']{0,20})[\"“”']\s+(?P<where>before|after)\s+the\s+(?P<punct>period|semicolon|comma)\s+at\s+the\s+end"
    + _STRUCTURAL_ACTION_TRAIL,
    re.IGNORECASE,
)
# Punctuation word replacement: "striking '<old>' and inserting a semicolon".
_PUNCT_WORD_RE = re.compile(
    r"striking\s+[\"“”'](?P<old>[^\"“”']{0,200})[\"“”']\s+and\s+inserting\s+(?:a\s+)?(?P<ins_word>semicolon|comma|period)\b"
    + _STRUCTURAL_ACTION_TRAIL,
    re.IGNORECASE,
)
# Structural-strike forms that cannot be represented as section-text operations yet.
# Each becomes a typed finding rather than falling into the generic unlowered bucket.
_SENTENCE_STRIKE_RE = re.compile(
    r"striking\s+(?:the\s+)?(?:(?:first|second|third|fourth|fifth|last)\s+)?sentence\b",
    re.IGNORECASE,
)
_HEADING_STRIKE_RE = re.compile(
    r"striking\s+(?:the\s+)?(?:section|subsection|paragraph|subparagraph|clause|subclause)\s+(?:heading|(?:designation\s+and\s+)?heading)\b",
    re.IGNORECASE,
)
_DESIGNATION_STRIKE_RE = re.compile(
    r"striking\s+(?:the\s+)?(?:section|subsection|paragraph|subparagraph|clause|subclause)\s+designation\b",
    re.IGNORECASE,
)
# Open-ended tail strike: deletes from an anchor to the end of the node.
_TAIL_STRIKE_RE = re.compile(
    r"striking\s+[\"“”'](?P<old>[^\"“”']{0,500})[\"“”']\s+and\s+all\s+that\s+follows",
    re.IGNORECASE,
)
# Bounded tail strike: deletes from anchor through a second anchor.
_THROUGH_TAIL_STRIKE_RE = re.compile(
    r"striking\s+[\"“”'](?P<old>[^\"“”']{0,500})[\"“”']\s+"
    r"and\s+all\s+that\s+follows\s+through\s+[\"“”'](?P<end>[^\"“”']{0,500})[\"“”']",
    re.IGNORECASE,
)
# "redesignating paragraphs (3) through (7) as paragraphs (4) through (8)" — a
# contiguous range relabel. The two endpoints define the shift; each member is
# relabelled by the same offset (the USC labels in a numeric range are
# consecutive). Only the digit-numbered (paragraph) range is materializable as a
# pure relabel without knowing the alphabet sequence, so we keep both endpoints
# and let the dry-run relabel the members it can enumerate.
_REDESIGNATE_RANGE_RE = re.compile(
    rf"redesignating\s+(?P<from_kind>{_KIND_WORDS})s?\s+"
    r"\((?P<from_lo>[0-9A-Za-z]+)\)\s+through\s+\((?P<from_hi>[0-9A-Za-z]+)\)\s+as\s+"
    rf"(?P<to_kind>{_KIND_WORDS})s?\s+"
    r"\((?P<to_lo>[0-9A-Za-z]+)\)\s+through\s+\((?P<to_hi>[0-9A-Za-z]+)\)"
    r"(?:,\s*respectively)?" + _STRUCTURAL_ACTION_TRAIL,
    re.IGNORECASE,
)
# "redesignating paragraphs (2) and (4) as paragraphs (4) and (5), respectively" —
# a non-contiguous paired relabel.  Each from-label maps in source order to the
# corresponding to-label (the enacted order is authoritative).  The source and
# destination kinds are taken from the prose; when they differ the operation is
# still emitted as a RENUMBER whose target/destination kinds differ, leaving the
# materialization layer to validate.
_REDESIGNATE_PAIRS_RE = re.compile(
    rf"redesignating\s+(?P<from_kind>{_KIND_WORDS})s?\s+"
    r"(?P<from_labels>\((?:[0-9A-Za-z]+)\)(?:\s*(?:,\s*and\s+|,\s*|\s+and\s+)\((?:[0-9A-Za-z]+)\))+)\s+"
    r"as\s+"
    rf"(?P<to_kind>{_KIND_WORDS})s?\s+"
    r"(?P<to_labels>\((?:[0-9A-Za-z]+)\)(?:\s*(?:,\s*and\s+|,\s*|\s+and\s+)\((?:[0-9A-Za-z]+)\))+)"
    r"(?:,\s*respectively)?" + _STRUCTURAL_ACTION_TRAIL,
    re.IGNORECASE,
)
# "inserting after section (N) / paragraph (N) / subsection (N) the following[ new
# <kind>]: <block>" — splice the quoted block as a NEW node positioned after the
# named anchor unit. Section anchors appear when the resolved target is a chapter/
# subchapter/part container.
_INSERT_NODE_AFTER_RE = re.compile(
    rf"inserting\s+after\s+(?P<kind>section|{_KIND_WORDS})\s+"
    r"(?:\((?P<label_p>[0-9A-Za-z]+)\)|(?P<label_s>[0-9A-Za-z]+))"
    r"(?:\s*\([0-9A-Za-z]+\))*\s+(?:\(as\s+so\s+redesignated\)\s+)?the\s+following",
    re.IGNORECASE,
)
# A comma/"and"-separated list of parenthesised labels: "(a), (c), (f), and (g)" or
# "(B) and (C)". One label is the single-unit form (handled by ``_STRIKE_UNIT_RE``);
# the LIST form (>=2 members) is what these recognizers add.
_LABEL_LIST = r"\((?:[0-9A-Za-z]+)\)(?:\s*(?:,\s*and\s+|,\s*|\s+and\s+)\([0-9A-Za-z]+\))+"
# "by striking subsections (a), (c), (f), and (g)" — a multi-unit STRUCTURAL strike
# (each named sub-unit is removed). The plural kind word ("subsections") + a 2+
# member list distinguishes it from the single ``by striking subsection (X)`` form
# and from a quoted-phrase strike (which carries a <quotedText>, never reaching the
# structural branch). Each member lowers to one REPEAL of the named node.
_STRIKE_UNIT_LIST_RE = re.compile(
    rf"by\s+striking\s+(?P<kind>{_KIND_WORDS})s\s+(?P<labels>{_LABEL_LIST})" + _STRUCTURAL_ACTION_TRAIL,
    re.IGNORECASE | re.UNICODE,
)
# "striking subparagraph (I) and inserting the following new subparagraphs (I) and
# (J): <block>" — a structural NODE-RESTRUCTURE: a named sub-unit is struck and one
# OR MORE new sub-units are spliced in its place. This is NOT a whole-node REPLACE of
# the resolved address (which would substitute the WHOLE enclosing node with just the
# new block, dropping its siblings) and NOT a flat phrase swap. We cannot faithfully
# represent the node-level restructure as a single op, so we hold it out as a typed
# residual (the same discipline as the ``inserting after … the following`` compound).
_STRIKE_UNIT_INSERT_NODE_RE = re.compile(
    rf"striking\s+(?P<kind>{_KIND_WORDS})\s+\([0-9A-Za-z]+\)"
    r"(?:\s*\([0-9A-Za-z]+\))*\s+and\s+inserting\s+the\s+following"
    rf"(?:\s+new)?\s+(?:{_KIND_WORDS})s?\b",
    re.IGNORECASE,
)
# "striking paragraph (3) and inserting the following: '(3) ...'" — a single named
# structural sub-unit is removed and one new unit spliced in its place.  The new unit
# is not separately named in the prose (it is the quotedContent payload), so this is
# a whole-node REPLACE of the struck sub-unit, not of the enclosing section/subsection
# and not a flat phrase swap.
_STRIKE_INSERT_UNIT_RE = re.compile(
    rf"striking\s+(?P<kind>{_KIND_WORDS})\s+\((?P<label>[0-9A-Za-z]+)\)"
    r"(?:\s*\([0-9A-Za-z]+\))*\s+and\s+inserting\s+the\s+following"
    # Block the compound "inserting the following new subparagraph(s) ..." form that
    # the rule above holds out as a node-restructure.
    rf"(?!\s+new\s+(?:{_KIND_WORDS})s?\b)",
    re.IGNORECASE,
)


def _strike_structural_unit(raw_text: str, target: LegalAddress) -> LegalAddress | None:
    """Parse ``by striking subsection (X)`` into the struck node's address.

    The struck node hangs off ``target`` (the section/sub-section the instruction
    resolved to). Returns ``None`` when the instruction is not a bare structural
    strike (e.g. it strikes a quoted phrase, handled by the text path).
    """
    if _FUTURE_EFFECTIVE_RE.search(raw_text):
        # Deferred / sunset repeal — the temporal layer owns the reversion; never
        # lower it to an immediate REPEAL (it would delete an in-force node).
        return None
    m = _STRIKE_UNIT_RE.search(raw_text)
    if m is None:
        return None
    label = m.group("label")
    # The struck unit hangs ONE level below the resolved target. Index from the
    # target's own depth below the section so "subsection (g)" off a section types
    # as a subsection, not floored to a deeper level by a stale leaf index.
    base_index = max(target.depth() - 2, 0)
    kind = _label_level(label, base_index)
    return LegalAddress(path=(*target.path, (kind, label)))


def _strike_insert_unit_target(raw_text: str, target: LegalAddress) -> LegalAddress | None:
    """Parse ``striking <kind> (X) and inserting the following: ...`` into the
    address of the unit being replaced.

    The resolved ``target`` is typically the enclosing section/subsection named in the
    unit's leading target phrase (e.g. ``section 1102(a)``). The struck unit named in
    the instruction body hangs one level below that.  Returns ``None`` when the form is
    not a single-unit structural strike-insert (e.g. quoted-phrase swaps, compound
    node restructures, or future-effective language).
    """
    if _FUTURE_EFFECTIVE_RE.search(raw_text):
        return None
    m = _STRIKE_INSERT_UNIT_RE.search(raw_text)
    if m is None:
        return None
    kind = m.group("kind").lower()
    label = m.group("label")
    return LegalAddress(path=(*target.path, (kind, label)))


def _redesignate_range(raw_text: str, target: LegalAddress) -> tuple[tuple[LegalAddress, LegalAddress], ...] | None:
    """Parse a ``redesignating (a) through (b) as (c) through (d)`` range.

    Returns a tuple of ``(from_addr, to_addr)`` pairs, one per member of the
    contiguous range. Numeric ranges are enumerated directly; alphabetic single-letter
    ranges (a-z, i-l, etc.) are also enumerable by character offset. Non-single-letter
    or non-contiguous forms are left as ``None`` so a more specific finding can be
    emitted rather than guessed.

    The USC segment kind is taken from the prose verb (``subsections``, ``paragraphs``,
    etc.) — the enacted text is authoritative, so a single-letter roman label is not
    mis-typed as a clause when the source explicitly says ``subsection``.
    """
    m = _REDESIGNATE_RANGE_RE.search(raw_text)
    if m is None:
        return None
    lo, hi = m.group("from_lo"), m.group("from_hi")
    to_lo, to_hi = m.group("to_lo"), m.group("to_hi")
    from_kind = m.group("from_kind").lower()
    to_kind = m.group("to_kind").lower()
    pairs: list[tuple[LegalAddress, LegalAddress]] = []

    if lo.isdigit() and hi.isdigit() and to_lo.isdigit() and to_hi.isdigit():
        span = int(hi) - int(lo)
        if span < 0 or (int(to_hi) - int(to_lo)) != span:
            return None
        offset = int(to_lo) - int(lo)
        # Relabel from the HIGH end down so an intermediate relabel never collides with
        # a member not yet moved (e.g. (3)->(4),(4)->(5) must move (4) first).
        for n in range(int(hi), int(lo) - 1, -1):
            from_label = str(n)
            to_label = str(n + offset)
            pairs.append(
                (
                    LegalAddress(path=(*target.path, (from_kind, from_label))),
                    LegalAddress(path=(*target.path, (to_kind, to_label))),
                )
            )
        return tuple(pairs)

    # Alphabetic single-letter ranges: e.g. (i) through (k) as (j) through (l).
    if (
        len(lo) == 1
        and lo.isalpha()
        and len(hi) == 1
        and hi.isalpha()
        and len(to_lo) == 1
        and to_lo.isalpha()
        and len(to_hi) == 1
        and to_hi.isalpha()
    ):
        span = ord(hi.lower()) - ord(lo.lower())
        if span < 0 or (ord(to_hi.lower()) - ord(to_lo.lower())) != span:
            return None
        offset = ord(to_lo.lower()) - ord(lo.lower())
        for ch_ord in range(ord(hi.lower()), ord(lo.lower()) - 1, -1):
            from_label = chr(ch_ord)
            to_label = chr(ch_ord + offset)
            pairs.append(
                (
                    LegalAddress(path=(*target.path, (from_kind, from_label))),
                    LegalAddress(path=(*target.path, (to_kind, to_label))),
                )
            )
        return tuple(pairs)

    return None


def _strike_structural_unit_list(raw_text: str, target: LegalAddress) -> tuple[LegalAddress, ...] | None:
    """Parse ``by striking subsections (a), (c), and (g)`` into struck nodes.

    Returns one address per named member (each hangs one level below ``target``),
    or ``None`` when the instruction is not a multi-unit structural strike (a single
    unit is handled by :func:`_strike_structural_unit`; a quoted-phrase strike never
    reaches the structural branch). Future-effective / sunset strikes are refused
    (the temporal layer owns the deferred reversion), exactly as the single-unit
    path does — an immediate REPEAL would delete a node still in force in the window.
    """
    if _FUTURE_EFFECTIVE_RE.search(raw_text):
        return None
    m = _STRIKE_UNIT_LIST_RE.search(raw_text)
    if m is None:
        return None
    labels = _SEGMENT_RE.findall(m.group("labels"))
    if len(labels) < 2:
        return None
    # The enacted prose names the members' level explicitly ("subsections (a), (c)")
    # — use that kind for ALL members rather than typing each label positionally,
    # which would mis-type a roman-ambiguous letter ("(d)" -> clause) among siblings
    # that are all the same kind. The verb is authoritative for the first level.
    kind = m.group("kind").lower()
    return tuple(LegalAddress(path=(*target.path, (kind, label))) for label in labels)


def _redesignate_pairs(raw_text: str, target: LegalAddress) -> tuple[tuple[LegalAddress, LegalAddress], ...] | None:
    """Parse ``redesignating (a) and (b) as (c) and (d), respectively`` relabel.

    Handles any equal-length list of source and destination labels (two or more),
    including Oxford/comma forms such as ``(1), (2), and (3) as (a), (b), and (c)``.
    Returns a tuple of ``(from_addr, to_addr)`` pairs in source order, or ``None``
    when the form is not a listed non-contiguous redesignation. The source and
    destination kinds come from the prose (e.g. ``paragraphs`` -> ``paragraph``);
    mismatched kinds are honoured because the enacted text may change nesting level.
    """
    m = _REDESIGNATE_PAIRS_RE.search(raw_text)
    if m is None:
        return None
    from_kind = m.group("from_kind").lower()
    to_kind = m.group("to_kind").lower()
    from_labels = _SEGMENT_RE.findall(m.group("from_labels"))
    to_labels = _SEGMENT_RE.findall(m.group("to_labels"))
    if not from_labels or len(from_labels) != len(to_labels):
        return None
    pairs: list[tuple[LegalAddress, LegalAddress]] = []
    for from_label, to_label in zip(from_labels, to_labels, strict=True):
        pairs.append(
            (
                LegalAddress(path=(*target.path, (from_kind, from_label))),
                LegalAddress(path=(*target.path, (to_kind, to_label))),
            )
        )
    return tuple(pairs)


# A quoted "add at the end" payload that OPENS with a new section / chapter head —
# "§ 2328. Mandatory forfeiture…", "CHAPTER 37—NONPOSTAL SERVICES", "SUBCHAPTER V…"
# — is a whole-new-unit CREATE, not an append to the inherited section's body. The
# leading curly/straight quote the USLM wraps the block in is allowed before the
# head. A bare "(a)"/"(1)" enumerator (a real subsection/paragraph append) does NOT
# match, so a legitimate add-at-end of section content is never mis-held-out.
_NEW_SECTION_PAYLOAD_HEAD_RE = re.compile(
    r'^\s*(?:["“]\s*)?(?:§+\s*\d|CHAPTER\s+\d|SUBCHAPTER\s+[IVXLC]|PART\s+[A-Z]\b)',
    re.IGNORECASE,
)

# Capture the leading "§ <num>. " of a replacement/insert payload so we can address a
# newly inserted section by its enacted section number rather than its parent
# container.  The number pattern mirrors the oracle-side catchline parser in
# :mod:`lawvm.us_federal.source_tree`; the optional leading quote mirrors the USLM
# quotedContent wrapper.
_SECTION_CATCHLINE_PREFIX_RE = re.compile(
    r'^\s*(?:["“]\s*)?\[?\s*§+\s*(?P<num>[0-9]+[A-Za-z]*(?:[-‐‑–][0-9]+[A-Za-z]*)?)\.\s*'
)


# Find each enacted section head inside a multi-section add-at-end payload so the
# single instruction can lower to one INSERT per new section.
_MULTI_SECTION_HEAD_RE = re.compile(
    r"§+\s*(?P<num>[0-9]+[A-Za-z]*(?:[-‐‑–][0-9]+[A-Za-z]*)?)\.\s*",
    re.IGNORECASE,
)


def _split_new_section_payload(payload_text: str) -> list[tuple[str, str]]:
    """Split a block like ``"§ 1181. ... § 1182. ..."`` into per-section slices.

    Returns ``[]`` when no ``§ <num>.`` heads are found, so the caller falls back to
    the existing residual/finding path. The returned slices preserve their leading
    catchline; the dry-run materializer strips the catchline by section number.
    """
    starts: list[tuple[int, str]] = []
    for m in _MULTI_SECTION_HEAD_RE.finditer(payload_text):
        starts.append((m.start(), m.group("num")))
    if not starts:
        return []
    slices: list[tuple[str, str]] = []
    end = len(payload_text)
    for idx, (start, num) in enumerate(starts):
        next_start = starts[idx + 1][0] if idx + 1 < len(starts) else end
        section_payload = payload_text[start:next_start].strip()
        # Drop a trailing sentence-final quote that wraps the whole quotedContent.
        if section_payload and section_payload[-1] in '"”':
            section_payload = section_payload[:-1].rstrip()
        # Discard a leading wrapper quote if the body is independently quoted.
        if section_payload and section_payload[0] in '"“':
            rest = section_payload[1:].lstrip()
            if rest.startswith("§"):
                section_payload = rest
        slices.append((num, section_payload))
    return slices


def _payload_opens_new_section(payload_text: str) -> bool:
    """True when an add-at-end payload opens with a new section/chapter/part head."""
    return _NEW_SECTION_PAYLOAD_HEAD_RE.match(payload_text or "") is not None


def _new_section_number_from_payload(payload_text: str) -> str | None:
    """Return the new section number from a payload catchline (``§ 111. ...``), or None."""
    m = _SECTION_CATCHLINE_PREFIX_RE.match(payload_text or "")
    return m.group("num") if m is not None else None


def _lower_instruction(
    *,
    statute_id: str,
    enacted: str,
    instruction_id: str,
    sequence: int,
    target_phrase: str,
    target_href: str,
    raw_text: str,
    effective_text: str = "",
    expires_text: str = "",
    quoted: list[str],
    actions: list[str],
    payload_node: IRNode | None,
    inherited_address: LegalAddress | None = None,
    inherited_via_classification: bool = False,
    plaw_title_scope: str = "",
    proof_title: str = "11",
) -> USAmendmentInstruction:
    effective = _parse_effective_date(effective_text or raw_text, enacted)
    expires = _parse_sunset_expiry(expires_text or raw_text, enacted)
    # An effective-date scope was recognized (ancestor or sibling) but could not be
    # reduced to a concrete ISO date.  Mark the operation as pending a condition
    # rather than defaulting to the enactment date, which would make a future-
    # conditional amendment look immediately in force.
    legal_status = (
        PENDING_CONDITION_STATUS
        if effective_text and not effective
        else COMMENCED_STATUS
    )
    source = OperationSource(
        statute_id=statute_id,
        enacted=enacted,
        effective=effective,
        expires=expires,
        raw_text=raw_text,
        legal_status=legal_status,
    )
    address, resolution_status = _resolve_target(
        target_phrase,
        target_href,
        raw_text=raw_text,
        inherited_address=inherited_address,
        plaw_title_scope=plaw_title_scope,
    )
    family = _classify_action(actions, raw_text)

    def _finding(rule_id: str, message: str) -> USAmendatoryFinding:
        return USAmendatoryFinding(
            rule_id=rule_id,
            message=message,
            statute_id=statute_id,
            instruction_id=instruction_id,
            target_phrase=target_phrase,
            target_href=target_href,
            raw_text=raw_text,
        )

    # Target gate: never hijack. Unresolved target → unsupported finding.
    if address is None:
        finding = _finding(
            TARGET_UNRESOLVED_FINDING_RULE_ID,
            f"could not resolve amendment target for {family!r} instruction "
            f"(phrase={target_phrase!r}, href={target_href!r})",
        )
        return USAmendmentInstruction(
            instruction_id=instruction_id,
            status="unsupported",
            witness_rule_id=UNLOWERED_FINDING_RULE_ID,
            action=family,
            target_phrase=target_phrase,
            target_href=target_href,
            finding=finding,
            parse_witness=ParseWitness(rule_id=UNLOWERED_FINDING_RULE_ID),
            raw_text=raw_text,
        )

    # Off-proof-title targets are resolvable but out of this surface's scope; record
    # them as needs_review rather than emit a candidate into the wrong corpus. The
    # proof title is threaded by the caller (default "11" preserves the original
    # Title-11-only surface; non-Title-11 benchmarks like the Title 42 ACA window
    # pass their actual proof title so on-title ops are not mis-flagged as
    # out-of-scope and produce misleading finding noise).
    if address.path and address.path[0] == ("title", proof_title):
        on_title_11 = True
    else:
        on_title_11 = False

    op: LegalOperation | None = None
    extra_ops: list[LegalOperation] = []
    witness_rule_id = UNLOWERED_FINDING_RULE_ID
    status = "unsupported"
    finding: USAmendatoryFinding | None = None

    # Tagged provenance suffix for target resolutions that used a non-local source
    # lane (currently the PLAW metadata title fallback). Appended to all operations
    # built by this instruction so the title-supply mechanism is visible.
    _metadata_provenance = (
        (TARGET_TITLE_FROM_PLAW_METADATA,) if resolution_status == "metadata_title" else ()
    )

    def _make_op(
        action: StructuralAction,
        *,
        rule_id: str,
        payload: IRNode | None = None,
        anchor: LegalAddress | None = None,
        destination: LegalAddress | None = None,
        text_patch: TextPatchSpec | None = None,
        target: LegalAddress | None = None,
        extra_provenance_tags: tuple[str, ...] = (),
    ) -> LegalOperation:
        provenance: list[str] = [
            "us_amendatory",
            f"target_resolution:{resolution_status}",
        ]
        if inherited_via_classification:
            provenance.append(TARGET_TITLE_FROM_SECTION_CLASSIFICATION)
        if resolution_status == "metadata_title":
            provenance.append(TARGET_TITLE_FROM_PLAW_METADATA)
        return LegalOperation(
            op_id=instruction_id,
            sequence=sequence,
            action=action,
            target=target if target is not None else address,
            payload=payload,
            anchor=anchor,
            destination=destination,
            source=source,
            text_patch=text_patch,
            witness_rule_id=rule_id,
            provenance_tags=(*provenance, *extra_provenance_tags),
        )

    # A PRECISE-text strike (a quoted match_text, not a whole-node operand) is
    # located by its match_text, not by its sub-section node. When the target's
    # leading sub-section letter is a roman-form letter the source-tree split flags
    # as ambiguous (and may duplicate, e.g. ``10 U.S.C. 284(i)(3)``), scope the
    # strike to the section so the dry-run anchors on the unique match_text instead
    # of risking a locate onto the phantom duplicate node. Whole-node ops keep the
    # full ladder path (they genuinely need the located node). This trades nothing:
    # the precise quoted string is the strike's real, unambiguous anchor.
    _text_strike_target = _section_scoped(address) if _has_roman_ambiguous_subsection_head(address) else None

    if family == "strike_insert":
        # A strike-and-insert unit that ALSO splices a whole new structural node
        # ("striking 'and' at the end of paragraph (1), … and by inserting after
        # paragraph (2) the following new paragraph: <block>") is a positional
        # COMPOUND, not a single phrase swap. The 2-operand text_replace below would
        # grab an arbitrary quotedText pair (e.g. strike 'and' / insert ', and') and
        # silently drop the structural block insert AND mis-apply the conjunction
        # edits at the wrong positions (26:6050I: ', and' spliced after an existing
        # comma → 'business,, and'). We cannot represent the compound as one op, so
        # we refuse it as a typed residual rather than emit a corrupt patch.
        #
        # For a named structural unit strike-insert, the target is the unit named in
        # the body (e.g. "striking paragraph (3)"), not the enclosing node the unit's
        # leading target phrase resolved to. The helper returns ``None`` for quoted-
        # phrase swaps, which stay anchored on their match_text.
        structural_target = _strike_insert_unit_target(raw_text, address)
        if _INSERT_NODE_AFTER_RE.search(raw_text) is not None:
            finding = _finding(
                COMPOUND_STRIKE_INSERT_FINDING_RULE_ID,
                "strike-and-insert is a positional compound that also splices a new "
                "structural node ('inserting after … the following'); not lowerable "
                "to a single text_replace",
            )
        elif _STRIKE_UNIT_INSERT_NODE_RE.search(raw_text) is not None:
            # "striking subparagraph (I) and inserting the following new
            # subparagraphs (I) and (J): <block>" — a node-level restructure. A
            # whole-node REPLACE of the resolved address would drop the struck node's
            # siblings (materializing only the new block); held out as a residual.
            finding = _finding(
                COMPOUND_STRIKE_INSERT_FINDING_RULE_ID,
                "strike-and-insert replaces a named structural sub-unit with new "
                "sub-unit(s) ('striking <unit> and inserting the following … <unit>'); "
                "a node-level restructure, not a whole-node text replace",
            )
        elif _TAIL_STRIKE_RE.search(raw_text) is not None:
            # "striking 'X' and all that follows ... [through 'Y'] and inserting 'Z'":
            # the quoted anchor is only the start of the deletion; the materializer
            # must remove from the anchor EITHER to the end of the target node
            # (open-ended tail) OR THROUGH the second quoted anchor (bounded).
            through_match = _THROUGH_TAIL_STRIKE_RE.search(raw_text)
            if through_match is not None and len(quoted) >= 3:
                # BOUNDED through-tail strike-insert: "striking OLD and all that
                # follows through END and inserting NEW". Delete [OLD..END]
                # inclusive, then insert NEW. The right-side text after END
                # survives (the op is a bounded deletion, not a to-end cut).
                old, end, new = quoted[0], quoted[1], quoted[2]
                op = _make_op(
                    StructuralAction.TEXT_REPLACE,
                    rule_id=RULE_STRIKE_INSERT_THROUGH_TAIL,
                    text_patch=TextPatchSpec(
                        kind=TextPatchKindEnum.REPLACE,
                        selector=TextSelector(
                            match_text=old,
                            occurrence=-1 if _is_each_place_instruction(raw_text) else 0,
                            end_match_text=end,
                        ),
                        replacement=new,
                    ),
                    target=_text_strike_target,
                    extra_provenance_tags=(RULE_STRIKE_INSERT_THROUGH_TAIL,),
                )
                witness_rule_id = RULE_STRIKE_INSERT_THROUGH_TAIL
            elif through_match is None and len(quoted) >= 2:
                old, new = quoted[0], quoted[1]
                op = _make_op(
                    StructuralAction.TEXT_REPLACE,
                    rule_id=RULE_STRIKE_INSERT_TAIL,
                    text_patch=TextPatchSpec(
                        kind=TextPatchKindEnum.REPLACE,
                        selector=TextSelector(
                            match_text=old,
                            occurrence=-1 if _is_each_place_instruction(raw_text) else 0,
                        ),
                        replacement=new,
                    ),
                    target=_text_strike_target,
                    extra_provenance_tags=(RULE_STRIKE_INSERT_TAIL,),
                )
                witness_rule_id = RULE_STRIKE_INSERT_TAIL
            else:
                finding = _finding(
                    UNLOWERED_FINDING_RULE_ID,
                    "open-ended tail strike-insert not lowerable without matched old/new quotes",
                )
        elif len(quoted) >= 2:
            old, new = quoted[0], quoted[1]
            op = _make_op(
                StructuralAction.TEXT_REPLACE,
                rule_id=RULE_STRIKE_INSERT,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(
                        match_text=old,
                        occurrence=-1 if _is_each_place_instruction(raw_text) else 0,
                    ),
                    replacement=new,
                ),
                target=_text_strike_target,
            )
            witness_rule_id = RULE_STRIKE_INSERT
        elif payload_node is not None and quoted:
            # strike <label> and insert <block> -> whole-node REPLACE of the struck unit.
            replace_target = structural_target if structural_target is not None else address
            normalized_payload_text, reconstituted = _reconstitute_target_label(
                payload_node.text, replace_target
            )
            if reconstituted:
                payload_node = IRNode(kind=payload_node.kind, text=normalized_payload_text)
                finding = _finding(
                    RULE_RECONSTITUTED_TARGET_LABEL,
                    f"payload for {replace_target} omitted its leading label; "
                    f"reconstituted ({_target_unit_label(replace_target)}) from the source target",
                )
            op = _make_op(
                StructuralAction.REPLACE,
                rule_id=RULE_STRIKE_INSERT,
                payload=payload_node,
                target=replace_target,
                extra_provenance_tags=(RULE_RECONSTITUTED_TARGET_LABEL,) if reconstituted else (),
            )
            witness_rule_id = RULE_STRIKE_INSERT
        elif payload_node is not None:
            # Whole-node REPLACE of the struck unit (no separate quoted old text).
            replace_target = structural_target if structural_target is not None else address
            normalized_payload_text, reconstituted = _reconstitute_target_label(
                payload_node.text, replace_target
            )
            if reconstituted:
                payload_node = IRNode(kind=payload_node.kind, text=normalized_payload_text)
                finding = _finding(
                    RULE_RECONSTITUTED_TARGET_LABEL,
                    f"payload for {replace_target} omitted its leading label; "
                    f"reconstituted ({_target_unit_label(replace_target)}) from the source target",
                )
            op = _make_op(
                StructuralAction.REPLACE,
                rule_id=RULE_STRIKE_INSERT,
                payload=payload_node,
                target=replace_target,
                extra_provenance_tags=(RULE_RECONSTITUTED_TARGET_LABEL,) if reconstituted else (),
            )
            witness_rule_id = RULE_STRIKE_INSERT
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "strike-and-insert without two quoted strings or a quoted block payload",
            )
    elif family == "strike_insert_end_punct":
        # Terminal punctuation edit: "striking the period at the end and inserting
        # '; and'" / "striking '; and' at the end ... and inserting ';'".  Anchor on
        # the terminal punctuation of the target node, not the first occurrence.
        m = _END_PUNCT_STRIKE_INSERT_RE.search(raw_text)
        if m is not None:
            old = m.group("quoted_old_end")
            replacement = m.group("quoted_new_end") or m.group("quoted_ins")
            if replacement is None:
                word_ins = (m.group("word_ins") or "").lstrip("a").strip()
                replacement = _punctuation_word_to_char(word_ins) or ""
            if old is None:
                old = _end_punctuation_char(m.group("struck_punct")) or "."
            # The optional "of (paragraph|subparagraph|...)(label)" clause names
            # the sub-unit whose terminal punctuation is edited. When the resolved
            # `address` is shallower than the named sub-unit (typical: prose names
            # `of paragraph (2)` while the resolved target is `subsection (b)` —
            # the parent of paragraph (2)), drilling the target one level deeper
            # to the named child anchors the op on the child's terminal period
            # rather than the parent's. Skip drilling when the resolved target's
            # last segment is already at the SAME kind (e.g. prose header resolved
            # to paragraph:2 and prose body says `of paragraph (2)` — drilling
            # would duplicate the unit at a deeper level). Holding out the
            # nested-chain form (`(1)(A)`, `(3)(B)(ii)`) — multi-level drilling
            # from prose alone is ambiguous and risks the wrong-node invariant
            # (§1.3).
            strike_target = _text_strike_target
            sub_kind = m.group("punct_subunit_kind")
            sub_label = m.group("punct_subunit_label")
            sub_extra = m.group("punct_subunit_extra")
            if (
                sub_kind is not None
                and sub_label is not None
                and (sub_extra is None or not sub_extra)
                and _text_strike_target is None
                and address.path
                and address.path[-1][0] != sub_kind.lower()
            ):
                strike_target = LegalAddress(
                    path=(*address.path, (sub_kind.lower(), sub_label))
                )
            op = _make_op(
                StructuralAction.TEXT_REPLACE,
                rule_id=RULE_STRIKE_INSERT_END_PUNCT,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=old, occurrence=-1),
                    replacement=replacement or "",
                ),
                target=strike_target,
            )
            witness_rule_id = RULE_STRIKE_INSERT_END_PUNCT
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "end-punctuation strike-insert matched classify but not regex",
            )
    elif family == "strike_insert_punct_word":
        # "striking '<old>' and inserting a semicolon/comma/period" — map the word to
        # the punctuation character.
        m = _PUNCT_WORD_RE.search(raw_text)
        if m is not None:
            old = m.group("old")
            new_char = _punctuation_word_to_char(m.group("ins_word"))
            if new_char is not None:
                op = _make_op(
                    StructuralAction.TEXT_REPLACE,
                    rule_id=RULE_STRIKE_INSERT_PUNCT_WORD,
                    text_patch=TextPatchSpec(
                        kind=TextPatchKindEnum.REPLACE,
                        selector=TextSelector(match_text=old, occurrence=-1),
                        replacement=new_char,
                    ),
                    target=_text_strike_target,
                )
                witness_rule_id = RULE_STRIKE_INSERT_PUNCT_WORD
            else:
                finding = _finding(
                    UNLOWERED_FINDING_RULE_ID,
                    f"unrecognized punctuation word: {m.group('ins_word')!r}",
                )
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "punctuation-word strike-insert matched classify but not regex",
            )
    elif family == "insert_end_punct":
        # "inserting '<X>' before/after the period at the end" — the anchor is the
        # described terminal punctuation of the target node.
        m = _END_PUNCT_INSERT_RE.search(raw_text)
        if m is not None:
            ins = m.group("ins")
            punct = _end_punctuation_char(m.group("punct")) or "."
            where = m.group("where")
            replacement = ins + punct if where == "before" else punct + ins
            op = _make_op(
                StructuralAction.TEXT_REPLACE,
                rule_id=RULE_INSERT_END_PUNCT,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=punct, occurrence=-1),
                    replacement=replacement,
                ),
                target=_text_strike_target,
            )
            witness_rule_id = RULE_INSERT_END_PUNCT
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "end-punctuation insert matched classify but not regex",
            )
    elif family == "strike":
        # Tail / through-tail strikes name a quoted anchor but DELETE MORE than that
        # exact literal (everything after, or a bounded span). The BOUNDED "through"
        # form ("striking OLD and all that follows through END") lowers to a
        # bounded deletion [OLD..END] with empty replacement; the open-ended
        # tail form ("... and all that follows") — which would delete to the END
        # of the host node — is still held out as not section-representable.
        if _THROUGH_TAIL_STRIKE_RE.search(raw_text) and len(quoted) >= 2:
            old, end = quoted[0], quoted[1]
            op = _make_op(
                StructuralAction.TEXT_REPEAL,
                rule_id=RULE_STRIKE_INSERT_THROUGH_TAIL,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.DELETE,
                    selector=TextSelector(
                        match_text=old,
                        occurrence=-1 if _is_each_place_instruction(raw_text) else 0,
                        end_match_text=end,
                    ),
                ),
                target=_text_strike_target,
                extra_provenance_tags=(RULE_STRIKE_INSERT_THROUGH_TAIL,),
            )
            witness_rule_id = RULE_STRIKE_INSERT_THROUGH_TAIL
        elif _TAIL_STRIKE_RE.search(raw_text):
            finding = _finding(
                TAIL_STRIKE_FINDING_RULE_ID,
                "open-ended tail strike ('... and all that follows') not section-representable",
            )
        elif quoted:
            op = _make_op(
                StructuralAction.TEXT_REPEAL,
                rule_id=RULE_STRIKE,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.DELETE,
                    selector=TextSelector(
                        match_text=quoted[0],
                        occurrence=-1 if _is_each_place_instruction(raw_text) else 0,
                    ),
                ),
                target=_text_strike_target,
            )
            witness_rule_id = RULE_STRIKE
        else:
            # Named non-materializable structural strikes are classified before the
            # bare structural-unit path so they produce typed findings, not generic
            # unlowered records.
            if _HEADING_STRIKE_RE.search(raw_text):
                finding = _finding(
                    HEADING_STRIKE_FINDING_RULE_ID,
                    "heading strike not section-representable",
                )
            elif _DESIGNATION_STRIKE_RE.search(raw_text):
                finding = _finding(
                    DESIGNATION_STRIKE_FINDING_RULE_ID,
                    "designation strike not section-representable",
                )
            elif _SENTENCE_STRIKE_RE.search(raw_text):
                finding = _finding(
                    SENTENCE_STRIKE_FINDING_RULE_ID,
                    "sentence strike not section-representable",
                )
            else:
                # "is amended by striking subsection (X)" — a structural-unit strike (a
                # sub-section REPEAL), no quoted phrase. Lower to a REPEAL of the named
                # node so the dry-run can remove it at sub-section granularity.
                struck = _strike_structural_unit(raw_text, address)
                struck_list = None if struck is not None else _strike_structural_unit_list(raw_text, address)
                if struck is not None:
                    op = LegalOperation(
                        op_id=instruction_id,
                        sequence=sequence,
                        action=StructuralAction.REPEAL,
                        target=struck,
                        source=source,
                        witness_rule_id=RULE_STRIKE_UNIT,
                        provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}", *_metadata_provenance),
                    )
                    address = struck
                    witness_rule_id = RULE_STRIKE_UNIT
                elif struck_list is not None:
                    # "by striking subsections (a), (c), and (g)" — one REPEAL per named
                    # member. The struck spans are distinct sibling nodes, so the order
                    # is immaterial at the section-text surface (each removes its own
                    # located span); emit in source order.
                    for idx, struck_addr in enumerate(struck_list):
                        node_op = LegalOperation(
                            op_id=f"{instruction_id}#s{idx}",
                            sequence=sequence,
                            action=StructuralAction.REPEAL,
                            target=struck_addr,
                            source=source,
                            witness_rule_id=RULE_STRIKE_UNIT_LIST,
                            provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}", *_metadata_provenance),
                        )
                        if op is None:
                            op = node_op
                        else:
                            extra_ops.append(node_op)
                    address = struck_list[0]
                    witness_rule_id = RULE_STRIKE_UNIT_LIST
                else:
                    finding = _finding(
                        UNLOWERED_FINDING_RULE_ID,
                        "strike with no quoted string and no recognizable structural unit",
                    )
    elif family == "insert_after":
        node_anchor = _INSERT_NODE_AFTER_RE.search(raw_text)
        if len(quoted) >= 2 and node_anchor is None:
            new_text, anchor_text = quoted[0], quoted[1]
            if _insert_word_anchor_direction(raw_text) == "before":
                replacement = _join_insert_before(anchor_text, new_text)
                rule_id = RULE_INSERT_BEFORE
            else:
                replacement = _join_insert_after(anchor_text, new_text)
                rule_id = RULE_INSERT_AFTER
            op = _make_op(
                StructuralAction.TEXT_REPLACE,
                rule_id=rule_id,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=anchor_text),
                    replacement=replacement,
                ),
            )
            witness_rule_id = rule_id
        elif node_anchor is not None and payload_node is not None:
            # "inserting after paragraph/section (N) the following: <block>" — splice
            # the quoted block as a NEW node positioned AFTER the named anchor unit.
            # The anchor node hangs off the resolved target; the dry-run inserts the
            # payload immediately after that node's span.
            anchor_label = node_anchor.group("label_p") or node_anchor.group("label_s")
            anchor_kind = node_anchor.group("kind").lower()
            if anchor_kind == "section":
                # Section labels ("7300") are not on the subsection ladder; the
                # prose verb itself names the anchor level.
                anchor_addr = LegalAddress(path=(*address.path, ("section", anchor_label)))
            elif anchor_kind in _USC_LEVELS:
                # The enacted prose explicitly names the anchor level ("paragraph",
                # "subparagraph", ...). Trust it rather than re-typing by ladder
                # position, which can over-descend a digit label under an already-deep
                # target (e.g. "inserting after paragraph (10)" under §541(b) must
                # anchor on paragraph:10, not subparagraph:10).
                anchor_addr = LegalAddress(path=(*address.path, (anchor_kind, anchor_label)))
            else:
                typed_kind = _label_level(anchor_label, max(address.depth() - 1, 0))
                anchor_addr = LegalAddress(path=(*address.path, (typed_kind, anchor_label)))
            op = _make_op(
                StructuralAction.INSERT,
                rule_id=RULE_INSERT_NODE_AFTER,
                payload=payload_node,
                anchor=anchor_addr,
            )
            # A section-level insert creates a new section node. Address the operation
            # by the enacted section number in the payload so downstream phases
            # (including the dry-run oracle comparison) can compare the new section
            # directly against its after-edition witness.
            if anchor_kind == "section":
                new_section_number = _new_section_number_from_payload(payload_node.text or "")
                if new_section_number is not None:
                    insert_title = _address_title(address) or _address_title(anchor_addr)
                    op = _make_op(
                        StructuralAction.INSERT,
                        rule_id=RULE_INSERT_NODE_AFTER,
                        payload=payload_node,
                        anchor=anchor_addr,
                        target=LegalAddress(path=(("title", insert_title), ("section", new_section_number))),
                    )
            witness_rule_id = RULE_INSERT_NODE_AFTER
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "insert-after without both inserted text and anchor text",
            )
    elif family == "add_at_end":
        add_payload_text = payload_node.text if payload_node is not None else (quoted[0] if quoted else "")
        if _payload_opens_new_section(add_payload_text):
            # "by adding at the end the following: '§ 1181. ... § 1182. ...'" — a
            # container-level (usually chapter) insertion of one or more new sections.
            # Split the block and emit one INSERT per enacted section number so the
            # dry-run can materialize each new section against its after-edition oracle.
            section_slices = _split_new_section_payload(add_payload_text or "")
            if section_slices:
                insert_title = _address_title(address)
                if not insert_title:
                    insert_title = _address_title(inherited_address)
                for idx, (section_number, section_payload) in enumerate(section_slices):
                    section_node = IRNode(kind=IRNodeKind.CONTENT, text=section_payload)
                    insert_op = _make_op(
                        StructuralAction.INSERT,
                        rule_id=RULE_ADD_AT_END_NEW_SECTIONS,
                        payload=section_node,
                        target=LegalAddress(
                            path=(
                                (("title", insert_title), ("section", section_number))
                                if insert_title
                                else (("section", section_number),)
                            )
                        ),
                    )
                    if idx == 0:
                        op = insert_op
                        witness_rule_id = RULE_ADD_AT_END_NEW_SECTIONS
                    else:
                        # Each additional section is emitted as part of the same
                        # source instruction but as its own LegalOperation.
                        extra_ops.append(insert_op)
            else:
                finding = _finding(
                    NEW_SECTION_INSERT_FINDING_RULE_ID,
                    "add-at-end payload opens with a new section/chapter head "
                    "but the block could not be split into per-section slices",
                )
        elif payload_node is not None:
            op = _make_op(
                StructuralAction.INSERT,
                rule_id=RULE_ADD_AT_END,
                payload=payload_node,
                anchor=address,
            )
            witness_rule_id = RULE_ADD_AT_END
        elif quoted:
            op = _make_op(
                StructuralAction.INSERT,
                rule_id=RULE_ADD_AT_END,
                payload=IRNode(kind=IRNodeKind.CONTENT, text=quoted[0]),
                anchor=address,
            )
            witness_rule_id = RULE_ADD_AT_END
        else:
            finding = _finding(UNLOWERED_FINDING_RULE_ID, "add-at-end without a quoted payload")
    elif family == "amend_to_read":
        if _FUTURE_EFFECTIVE_RE.search(effective_text or raw_text):
            finding = _finding(
                DEFERRED_AMEND_TO_READ_FINDING_RULE_ID,
                "future-effective / sunset amend-to-read not lowered as immediate amendment",
            )
        elif payload_node is not None:
            normalized_payload_text, reconstituted = _reconstitute_target_label(
                payload_node.text, address
            )
            if reconstituted:
                payload_node = IRNode(kind=payload_node.kind, text=normalized_payload_text)
                finding = _finding(
                    RULE_RECONSTITUTED_TARGET_LABEL,
                    f"payload for {address} omitted its leading label; "
                    f"reconstituted ({_target_unit_label(address)}) from the source target",
                )
            op = _make_op(
                StructuralAction.REPLACE,
                rule_id=RULE_AMEND_TO_READ,
                payload=payload_node,
                extra_provenance_tags=(RULE_RECONSTITUTED_TARGET_LABEL,) if reconstituted else (),
            )
            witness_rule_id = RULE_AMEND_TO_READ
        else:
            finding = _finding(UNLOWERED_FINDING_RULE_ID, "amend-to-read without a quoted replacement block")
    elif family == "repeal":
        op = _make_op(StructuralAction.REPEAL, rule_id=RULE_REPEAL)
        witness_rule_id = RULE_REPEAL
    elif family == "redesignate":
        pair = _redesignate_destination(raw_text, address)
        range_pairs = None if pair is not None else _redesignate_range(raw_text, address)
        pair_list = None if (pair is not None or range_pairs is not None) else _redesignate_pairs(raw_text, address)
        if pair is not None:
            from_addr, to_addr = pair
            op = LegalOperation(
                op_id=instruction_id,
                sequence=sequence,
                action=StructuralAction.RENUMBER,
                target=from_addr,
                destination=to_addr,
                source=source,
                witness_rule_id=RULE_REDESIGNATE,
                provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}", *_metadata_provenance),
            )
            witness_rule_id = RULE_REDESIGNATE
        elif range_pairs:
            # "redesignating paragraphs (3) through (7) as (4) through (8)" — one
            # RENUMBER per member (high-end first so relabels never collide).
            for idx, (from_addr, to_addr) in enumerate(range_pairs):
                node_op = LegalOperation(
                    op_id=f"{instruction_id}#r{idx}",
                    sequence=sequence,
                    action=StructuralAction.RENUMBER,
                    target=from_addr,
                    destination=to_addr,
                    source=source,
                    witness_rule_id=RULE_REDESIGNATE_RANGE,
                    provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}", *_metadata_provenance),
                )
                if op is None:
                    op = node_op
                else:
                    extra_ops.append(node_op)
            witness_rule_id = RULE_REDESIGNATE_RANGE
        elif pair_list is not None:
            # "redesignating paragraphs (2) and (4) as paragraphs (4) and (5),
            # respectively" — one RENUMBER per pair in source order.
            for idx, (from_addr, to_addr) in enumerate(pair_list):
                node_op = LegalOperation(
                    op_id=f"{instruction_id}#p{idx}",
                    sequence=sequence,
                    action=StructuralAction.RENUMBER,
                    target=from_addr,
                    destination=to_addr,
                    source=source,
                    witness_rule_id=RULE_REDESIGNATE_PAIRS,
                    provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}", *_metadata_provenance),
                )
                if op is None:
                    op = node_op
                else:
                    extra_ops.append(node_op)
            witness_rule_id = RULE_REDESIGNATE_PAIRS
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "redesignation is multi-unit or non-numeric range form (not lowered to RENUMBER)",
            )
    else:
        finding = _finding(
            UNLOWERED_FINDING_RULE_ID,
            f"amendatory form not recognized (actions={actions!r})",
        )

    if op is not None:
        if resolution_status == "metadata_title":
            finding = _finding(
                TARGET_TITLE_FROM_PLAW_METADATA,
                "target title supplied from PLAW short-title preamble "
                f"({plaw_title_scope}); section had no explicit 'of title N' or sidenote ref",
            )
        status = "accepted" if on_title_11 else "needs_review"
        if not on_title_11:
            non_title_finding = _finding(
                NON_TITLE_TARGET_RULE_ID,
                f"resolved target is outside Title 11 ({address.path[0] if address.path else ()}); "
                "candidate withheld from Title 11 scope",
            )
            # The metadata-title observation is a coverage explanation, not a strict
            # barrier. Keep the non-title-scope finding dominant but do not erase it.
            finding = non_title_finding

    return USAmendmentInstruction(  # noqa: B035
        instruction_id=instruction_id,
        status=status,
        witness_rule_id=witness_rule_id,
        action=family,
        target_phrase=target_phrase,
        target_href=target_href,
        target_address=address,
        operation=op,
        extra_operations=tuple(extra_ops),
        finding=finding,
        parse_witness=ParseWitness(rule_id=witness_rule_id),
        raw_text=raw_text,
    )


# ---------------------------------------------------------------------------
# Instruction extraction from a USLM section
# ---------------------------------------------------------------------------


def _first_usc_ref(
    content: ET.Element,
    *,
    exclude: ET.Element | set[ET.Element] | frozenset[ET.Element] | None = None,
) -> tuple[str, str]:
    """Return ``(prose_phrase, href)`` for the first USC structural ref in content.

    Refs that live inside a ``<quotedText>`` / ``<quotedContent>`` operand subtree
    are SKIPPED: such a ref is part of the struck/inserted literal (a cross-citation
    being edited as text), never the instruction's own amendment target. Scanning
    them would hijack the target onto the operand's cited section instead of the
    section actually being amended (no silent target hijack, Prime Directive).

    ``exclude`` drops additional sub-trees (e.g. sibling amendatory units when
    resolving an intermediate ancestor's own target) so their refs do not leak into
    the ancestor's target scan.
    """
    if exclude is None:
        exclude_set: set[ET.Element] = set()
    elif isinstance(exclude, ET.Element):
        exclude_set = {exclude}
    else:
        exclude_set = set(exclude)

    for ref, in_non_target in _iter_with_non_target_depth(content, exclude=exclude_set):
        if in_non_target or _localname(ref.tag) != "ref":
            continue
        href = ref.get("href", "")
        if "/usc/" not in href:
            continue
        phrase = "".join(ref.itertext()).strip()
        # Skip pure "note" citations (editorial cross-refs), not amendment targets.
        if phrase.lower().endswith("note"):
            continue
        return phrase, href
    return "", ""


def _iter_with_non_target_depth(
    root: ET.Element,
    *,
    exclude: ET.Element | set[ET.Element] | frozenset[ET.Element] | None = None,
) -> Iterable[tuple[ET.Element, bool]]:
    """Pre-order walk yielding ``(element, inside_non_target_container)`` per node.

    ``inside_non_target_container`` is ``True`` once the walk has descended into (or
    onto) a quoted-operand subtree (see ``_NON_TARGET_REF_CONTAINER_TAGS``) — the
    region whose refs are struck/inserted operand literals, never the instruction's
    own amendment target.

    ``exclude`` drops additional sub-trees entirely (e.g. sibling amendatory units when
    resolving an intermediate ancestor's own target).
    """
    if exclude is None:
        exclude_set: set[ET.Element] = set()
    elif isinstance(exclude, ET.Element):
        exclude_set = {exclude}
    else:
        exclude_set = set(exclude)

    def _walk(node: ET.Element, inside: bool) -> Iterable[tuple[ET.Element, bool]]:
        if node in exclude_set:
            return
        here = inside or _localname(node.tag) in _NON_TARGET_REF_CONTAINER_TAGS
        yield node, here
        for child in node:
            yield from _walk(child, here)

    yield from _walk(root, False)


def _unit_own_target(
    unit: ET.Element,
    *,
    exclude: ET.Element | set[ET.Element] | frozenset[ET.Element] | None = None,
) -> LegalAddress | None:
    """Resolve a unit's OWN absolute USC target from its direct prose/ref, if any.

    Only the unit's own ``<content>`` text (not its nested amendatory sub-units) is
    consulted, so a parent's "Section X of title N is amended—" resolves to X
    without bleeding a child's "in section Y" into the parent's target. ``exclude``
    drops sub-trees (the descendant leaf units and/or sibling amendatory units) from
    the prose/ref scan.
    """
    phrase, href = _first_usc_ref(unit, exclude=exclude)
    addr, status = _resolve_target(phrase, href)
    if addr is not None:
        return addr
    # No ref: try the unit's own direct prose head ("Section X of title N ...").
    # Scan only text outside the excluded descendant sub-units.
    own_text = _shallow_text(unit, exclude=exclude)
    return parse_usc_target_phrase(own_text)


# A title-only enclosing scope: a parent chapeau that amends a WHOLE title with no
# section of its own — "Part I of title 18, United States Code, is amended—" (or the
# bare ``<ref href="/us/usc/t18">`` the converter emits for it). It names the title
# the enacted language operates under but no codified section, so it does not resolve
# to a target address. We thread the TITLE alone so a leaf that names its own section
# in relative prose ("(A) in section 1583(a), by striking …") can resolve under it —
# the title is the OLRC's own (enacted) scope, never invented. The prose form must
# carry "United States Code" so a stray "title 18 of the report" is not mistaken for
# a USC title.
_TITLE_ONLY_HREF_RE = re.compile(r"^/us/usc/t(?P<title>\d+)/?$")
_TITLE_ONLY_PROSE_RE = re.compile(r"\btitle\s+(?P<title>\d+),?\s+United\s+States\s+Code\b", re.IGNORECASE)


def _unit_title_only_scope(
    unit: ET.Element,
    *,
    exclude: ET.Element | set[ET.Element] | frozenset[ET.Element] | None = None,
) -> str:
    """The bare USC title a unit's title-only chapeau scopes, or "".

    Returns the title number when the unit's own ref/prose names a whole USC title
    with NO section ("Part I of title 18, United States Code" / ``/us/usc/t18``).
    Returns "" when the unit names a section (that is a full target, handled by
    :func:`_unit_own_target`) or names no USC title at all. Never invents a title.
    """
    phrase, href = _first_usc_ref(unit, exclude=exclude)
    # A section-bearing href/prose is a full target, not a title-only scope.
    if href:
        m = _TITLE_ONLY_HREF_RE.match(href.strip())
        if m is not None:
            return m.group("title")
    own_text = _shallow_text(unit, exclude=exclude)
    # If the unit's own prose already names a "section X of title N", that is a full
    # target (or a relative one); do not treat it as a bare title-only scope.
    if parse_usc_target_phrase(own_text) is not None:
        return ""
    m = _TITLE_ONLY_PROSE_RE.search(own_text)
    if m is not None:
        return m.group("title")
    return ""


def _ancestor_title_only_scope(
    section: ET.Element,
    leaf: ET.Element,
    parent_of: Mapping[ET.Element, ET.Element | None],
) -> str:
    """The nearest ancestor (or section chapeau) that scopes a bare title.

    A conforming-amendment list is often headed "(a) Title 11.—" with descendant
    units that name their own section ("(2) in section 1102(g)..."). The title is
    threaded from this ancestor so the relative section reference resolves. Sibling
    amendatory units are excluded from the ancestor's scan so their refs do not leak.
    """
    ancestor = parent_of.get(leaf)
    while ancestor is not None:
        scope = _unit_title_only_scope(ancestor, exclude=_amendatory_unit_children(ancestor))
        if scope:
            return scope
        ancestor = parent_of.get(ancestor)
    # The title-only scope may live directly on the section's chapeau/content.
    for tag in ("u:chapeau", "u:content"):
        head_el = section.find(tag, _NS)
        if head_el is not None:
            scope = _unit_title_only_scope(head_el)
            if scope:
                return scope
    return ""


_UNIT_TAGS = ("subsection", "paragraph", "subparagraph", "clause", "subclause")


def _amendatory_unit_children(parent: ET.Element) -> set[ET.Element]:
    """Direct amendatory-unit children of ``parent`` (for sibling exclusion)."""
    out: set[ET.Element] = set()
    for child in parent:
        if _localname(child.tag) not in _UNIT_TAGS:
            continue
        if any(_localname(a.tag) == "amendingAction" for a in child.iter()):
            out.add(child)
    return out


def _shallow_text(
    elem: ET.Element,
    *,
    exclude: ET.Element | set[ET.Element] | frozenset[ET.Element] | None = None,
) -> str:
    """Concatenated text of ``elem`` excluding the ``exclude`` sub-tree(s).

    Text inside a quoted-operand container (see ``_NON_TARGET_REF_CONTAINER_TAGS``)
    is dropped (its *tail* — the surrounding instruction prose — is kept). This
    keeps the prose head scan (``_unit_own_target``) from parsing a quoted operand
    literal such as ``"section 7222 of title 10, United States Code"`` as the
    unit's own amendment target: that string is the struck/inserted phrase, not
    the section being amended. Mirrors the same no-hijack discipline as
    :func:`_first_usc_ref`.

    ``exclude`` may be a single element, a set/frozenset of elements, or ``None``.
    Multiple exclusions are used when resolving an intermediate ancestor's own
    target, so sibling amendatory units do not leak their anchors into the
    ancestor's target scan.
    """
    if exclude is None:
        exclude_set: set[ET.Element] = set()
    elif isinstance(exclude, ET.Element):
        exclude_set = {exclude}
    else:
        exclude_set = set(exclude)

    parts: list[str] = []

    def _is_dropped(node: ET.Element) -> bool:
        # Exclude set drops the descendant leaf / sibling sub-units; quoted operands
        # drop the struck/inserted literal. In both cases the node's OWN text is
        # suppressed but its TAIL (the surrounding instruction prose) is kept.
        return node in exclude_set or _localname(node.tag) in _NON_TARGET_REF_CONTAINER_TAGS

    def _walk(node: ET.Element) -> None:
        if _is_dropped(node):
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            _walk(child)
            if child.tail:
                parts.append(child.tail)

    if elem.text:
        parts.append(elem.text)
    for child in elem:
        _walk(child)
        if child.tail:
            parts.append(child.tail)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


# A unit head "Section X(...) is amended" / "Paragraph (N) of section X(...) is
# amended" with NO "of title M" names its USC section in prose but inherits the
# title from the enclosing Act's context. We thread that title ONLY from the
# section's OWN govinfo classification refs (``<ref href="/us/usc/tM/sX">``, incl.
# the editorial ``note`` sidenote refs the OLRC stamps), and ONLY when those refs
# pin the SAME section number the head names to exactly ONE title. This is the
# OLRC's authoritative classification of the very section being amended — not a
# guess: if the head's section is unclassified, multi-classified, or the law spans
# several titles for that number, the unit stays unresolved (no silent hijack).
_USC_CLASSIFY_REF_RE = re.compile(r"^/us/usc/t(?P<title>\d+)/s(?P<section>\d+[A-Za-z]*)")
# The section a unit head names, with no "of title": "Section 24(d) is amended",
# "Paragraph (5) of section 48(a) is amended", "Subclause (II) of section
# 48(a)(2)(A)(i) of the Internal Revenue Code of 1986 is amended". The section
# token may be followed by its own "(...)" segments and then the amend verb
# (optionally via an intervening "...,..." clause that carries no period).
_RELATIVE_HEAD_SECTION_RE = re.compile(
    r"(?:^|\b)(?:in\s+)?[Ss]ection\s+(?P<section>\d+[A-Za-z]*)"
    r"(?:\s*\([0-9A-Za-z]+\))*"
    r"(?:,[^.]*?)?(?:\s+is\s+amended\b|\s*[—–-])",
)


def _section_classification_pairs(section: ET.Element) -> set[tuple[str, str]]:
    """All ``(title, section)`` USC pairs the section's own refs classify it under.

    Scans every ``<ref href="/us/usc/tM/sX...">`` in the section subtree (including
    the editorial ``note`` sidenote refs), keeping only the leading ``title/section``
    of each. This is the govinfo/OLRC structural classification of the provisions the
    section amends — the authoritative signal for the title a relative head omits.
    """
    pairs: set[tuple[str, str]] = set()
    for e in section.iter():
        if _localname(e.tag) != "ref":
            continue
        m = _USC_CLASSIFY_REF_RE.match((e.get("href") or "").strip())
        if m is not None:
            pairs.add((m.group("title"), m.group("section")))
    return pairs


def _inherited_title_from_section_classification(section: ET.Element, raw_text: str) -> str:
    """The title to thread onto a relative section head, or "".

    Matches both "Section X(...) is amended" and "(N) in section X(...)—" forms
    that omit the title. Returns a title ONLY when (1) the head names a bare
    ``section X`` (no "of title M"), and (2) the section's own classification
    refs pin THAT section number ``X`` to exactly one title. Otherwise "" (the
    head stays unresolved). This never invents a title: it threads the OLRC's
    own classification of the named section.
    """
    if "of title" in raw_text.lower():
        return ""
    head = _RELATIVE_HEAD_SECTION_RE.search(raw_text)
    if head is None:
        return ""
    named_section = head.group("section")
    titles = {title for (title, sec) in _section_classification_pairs(section) if sec == named_section}
    if len(titles) == 1:
        return next(iter(titles))
    return ""


def _iter_instruction_units(
    section: ET.Element,
) -> Iterable[tuple[str, ET.Element, LegalAddress | None, str, str, bool]]:
    """Yield ``(unit_id, element, inherited_address, effective_text, expires_text, inherited_via_classification)`` for each amendatory unit.

    A unit is either the section's own direct ``<content>`` (flat instruction) or
    each nested ``<paragraph>/<subparagraph>`` that carries its own amendingAction
    ("(1) in subsection (b)— (A) by striking…"). ``inherited_address`` is the USC
    address resolved by the nearest ENCLOSING instruction, threaded down so a leaf
    that names no title of its own can be resolved.

    ``effective_text`` is the nearest ancestor chapeau (including the section's own
    head) that carries an "Effective …" date phrase, or the leaf's own text if no
    ancestor carries one.

    ``expires_text`` is captured from a sibling "Effective Date; Sunset" paragraph
    whose named range covers the leaf's parent paragraph/subsection. This handles
    temporary amendments where one paragraph declares both the effective trigger and
    the sunset date for a group of sibling amendments.
    """
    unit_tags = ("subsection", "paragraph", "subparagraph", "clause", "subclause")

    def _is_unit(elem: ET.Element) -> bool:
        return _localname(elem.tag) in unit_tags and any(_localname(a.tag) == "amendingAction" for a in elem.iter())

    nested = [elem for elem in section.iter() if _is_unit(elem)]

    # Map each unit to its nearest amendatory-unit ancestor (within the section),
    # so we can thread the parent instruction's resolved target into leaf units.
    parent_of: dict[ET.Element, ET.Element | None] = {}
    # Full XML parent map, needed by sibling scope collectors to compare structural
    # position independently of amendatory-unit nesting.
    xml_parent_of: dict[ET.Element, ET.Element | None] = {section: None}
    stack: list[ET.Element] = []

    def _descend(node: ET.Element) -> None:
        pushed = False
        if node is not section and _is_unit(node):
            parent_of[node] = stack[-1] if stack else None
            stack.append(node)
            pushed = True
        for child in node:
            xml_parent_of[child] = node
            _descend(child)
        if pushed:
            stack.pop()

    _descend(section)

    leaf_units = []
    for elem in nested:
        has_deeper = any(
            child is not elem
            and _localname(child.tag) in unit_tags
            and any(_localname(a.tag) == "amendingAction" for a in child.iter())
            for child in elem.iter()
        )
        if not has_deeper:
            leaf_units.append(elem)

    section_chapeau = _shallow_text(
        section, exclude=_amendatory_unit_children(section)
    )

    if leaf_units:
        sibling_sunset_scopes = _collect_sibling_sunset_scopes(section, parent_of, xml_parent_of)
        sibling_effective_scopes = _collect_sibling_effective_scopes(section, parent_of, xml_parent_of)
        for elem in leaf_units:
            # Effective-date prefixes live on the enclosing section/subsection chapeau
            # far more often than on the leaf action text. Gather the ancestor chapeaux
            # from outermost to innermost; the first one containing a recognizable
            # "Effective …" phrase becomes the unit's effective text.
            ancestor_chain: list[ET.Element] = []
            ancestor = parent_of.get(elem)
            while ancestor is not None:
                ancestor_chain.append(ancestor)
                ancestor = parent_of.get(ancestor)
            effective_text = ""
            for ancestor in reversed(ancestor_chain):
                chapeau = _shallow_text(
                    ancestor, exclude=_amendatory_unit_children(ancestor)
                )
                if _has_effective_date_phrase(chapeau):
                    effective_text = chapeau
                    break
            if not effective_text and _has_effective_date_phrase(section_chapeau):
                effective_text = section_chapeau
            # Sibling effective-date scopes (e.g. "The amendments made by subsections
            # (a) through (e) shall take effect ...") apply when no ancestor carries a
            # concrete effective date phrase.
            if not effective_text:
                effective_text = sibling_effective_scopes.get(elem, "")

            # Sibling sunset scope: paragraphs like "(f) Effective Date; Sunset" that
            # name a range of subsections/paragraphs and an expiry date.
            expires_text = sibling_sunset_scopes.get(elem, "")

            uid = elem.get("identifier") or elem.get("id") or ""
            # Inherited target = the nearest ancestor instruction whose OWN prose/ref
            # resolves to a USC address. All direct amendatory-unit children of an
            # ancestor are excluded from that ancestor's scan so sibling anchors/refs
            # never leak into the parent's target resolution.
            inherited: LegalAddress | None = None
            ancestor = parent_of.get(elem)
            # The ancestors BETWEEN the leaf and the section-resolving ancestor each
            # carry a leading sub-unit anchor ("(A) in paragraph (1)(A)—") that scopes
            # the edit one ladder rung deeper. Collect them leaf→up; they are applied
            # top→down onto the inherited section so the leaf's own "(i) in clause
            # (ii)" lands on the FULL ladder, not a truncated section/clause path.
            intermediate_anchors: list[str] = []
            # Title-only chapeau ("(a) Title 11.—") gives the enacted title for any
            # nested relative "in section X" reference that appears on an ancestor.
            scope_title = _ancestor_title_only_scope(section, elem, parent_of)
            while ancestor is not None and inherited is None:
                exclusions = _amendatory_unit_children(ancestor)
                inherited = _unit_own_target(ancestor, exclude=exclusions)
                if inherited is None and scope_title:
                    # Ancestor prose may itself name a target section without saying
                    # "is amended" ("(2) in section 1102(g), by redesignating ...").
                    # Thread that section down so the descendant leaf inherits it.
                    inherited = parse_relative_usc_target(
                        _shallow_text(ancestor, exclude=exclusions),
                        inherited_title=scope_title,
                    )
                if inherited is None:
                    intermediate_anchors.append(_text_of(ancestor))
                else:
                    # The ancestor that furnishes the inherited address may itself
                    # introduce a sub-unit anchor in its chapeau (e.g. "Section X ...
                    # is amended, in subsection (a)—"). Thread that anchor so the leaf
                    # lands inside the named sub-unit instead of on the inherited
                    # section.
                    inherited = _refine_with_any_subunit_anchor(
                        inherited, _shallow_text(ancestor, exclude=exclusions)
                    )
                ancestor = parent_of.get(ancestor)
            if inherited is None:
                # Fall back to the section's own content ref ("Section X ... — (1)...").
                section_content = section.find("u:content", _NS)
                if section_content is not None:
                    sp, sh = _first_usc_ref(section_content)
                    inherited, _ = _resolve_target(sp, sh)
            if inherited is None:
                # Last resort: a parent "in section X(...)—" or "Section X(...) is
                # amended" head with no "of title" whose section the OLRC classifies
                # under exactly one title. Resolve the head FULLY here (title from
                # classification, section + segments from the head prose) so a complete
                # address is threaded — never a bare title that step-3 inheritance
                # could mis-apply.
                ancestor = parent_of.get(elem)
                while ancestor is not None and inherited is None:
                    exclusions = _amendatory_unit_children(ancestor)
                    head_text = _shallow_text(ancestor, exclude=exclusions)
                    title = _inherited_title_from_section_classification(section, head_text)
                    if title:
                        inherited = parse_relative_usc_target(head_text, inherited_title=title)
                    ancestor = parent_of.get(ancestor)
            title_only_scope = scope_title
            inherited_is_bare_title = False
            if inherited is None and scope_title:
                # Title-only enclosing scope: a parent chapeau amends a WHOLE title
                # ("(a) Title 11.—") with no section, so no inherited ADDRESS resolves.
                # Thread the bare TITLE so a leaf that names its own section in relative
                # prose ("(A) in section 1583(a), by striking …") resolves under it.
                title_only_scope = scope_title
                inherited = LegalAddress(path=(("title", title_only_scope),))
                inherited_is_bare_title = True
            inherited_via_classification = False
            if inherited is None:
                # Last resort: the leaf unit itself names the target section without a
                # title ("Section 117(c) is amended ..."), and the section's publisher
                # classification refs pin that section to exactly one title. Use the
                # leaf's own head as the relative head and the classified title as the
                # inherited title. This is recorded in operation provenance; it is not a
                # guess because the title comes from the same act's USC classification.
                leaf_head_text = _shallow_text(
                    elem, exclude=_amendatory_unit_children(elem)
                )
                title = _inherited_title_from_section_classification(
                    section, leaf_head_text
                )
                if title:
                    inherited = parse_relative_usc_target(
                        leaf_head_text, inherited_title=title
                    )
                    inherited_via_classification = True
            title_only_scope = scope_title
            inherited_is_bare_title = False
            if inherited is None and scope_title:
                # Title-only enclosing scope: a parent chapeau amends a WHOLE title
                # ("(a) Title 11.—") with no section, so no inherited ADDRESS resolves.
                # Thread the bare TITLE so a leaf that names its own section in relative
                # prose ("(A) in section 1583(a), by striking …") resolves under it.
                title_only_scope = scope_title
                inherited = LegalAddress(path=(("title", title_only_scope),))
                inherited_is_bare_title = True
            if inherited is not None and intermediate_anchors and not inherited_is_bare_title:
                # Apply outermost intermediate anchor first (it is the shallowest
                # scope); each refinement descends from the prior frontier.
                for anchor_text in reversed(intermediate_anchors):
                    inherited = _refine_with_leading_subunit_anchor(inherited, anchor_text)
            yield uid, elem, inherited, effective_text, expires_text, inherited_via_classification
        return
    # Flat instruction: the section's own content blocks. A flat head "Section X(...)
    # is amended" with no "of title" inherits the title from the section's own OLRC
    # classification of X (resolved fully here so a complete address is threaded; the
    # unit's own absolute prose/ref, when present, still takes precedence downstream).
    flat_inherited: LegalAddress | None = None
    flat_text = _shallow_text(section, exclude=None)
    flat_title = _inherited_title_from_section_classification(section, flat_text)
    if flat_title:
        flat_inherited = parse_relative_usc_target(flat_text, inherited_title=flat_title)
    flat_effective = (
        section_chapeau
        if _has_effective_date_phrase(section_chapeau)
        else ""
    )
    yield (
        section.get("identifier") or section.get("id") or "",
        section,
        flat_inherited,
        flat_effective,
        "",
        False,
    )


def lower_plaw_amendatory(
    data: bytes, *, statute_id: str = "", enacted: str = "", proof_title: str = "11"
) -> USAmendatoryReport:
    """Lower one Public Law's USLM amendatory text into candidate operations."""
    root = ET.fromstring(data)
    congress = (root.findtext(".//u:meta/u:congress", namespaces=_NS) or "").strip()
    docnum = (root.findtext(".//u:meta/u:docNumber", namespaces=_NS) or "").strip()
    approved = (root.findtext(".//u:meta/u:approvedDate", namespaces=_NS) or "").strip()
    if not statute_id:
        statute_id = f"PL {congress}-{docnum}" if congress and docnum else "PL ?-?"
    if not enacted:
        enacted = approved

    title_targets: set[str] = set()
    instructions: list[USAmendmentInstruction] = []
    findings: list[USAmendatoryFinding] = []
    sequence = 0

    main = root.find(".//u:main", _NS)
    if main is None:
        return USAmendatoryReport(statute_id=statute_id, enacted=enacted, title_targets=(), instructions=())

    plaw_title_scope = _plaw_usc_title_scope(root)

    # First pass: collect all amendatory units and any explicit title references.
    # If the PLAW's preamble names one title but the body also contains explicit
    # references to a *different* title, the preamble is not a safe fallback for
    # bare "Section N(...)" targets — using it would silently hijack cross-title
    # instructions onto the preamble's title. In that case the metadata scope is
    # discarded for the whole PLAW and those bare targets remain unresolved
    # (typed residual) instead of guessed.
    explicit_titles: set[str] = set()
    unit_records: list[
        tuple[
            str,
            ET.Element,
            LegalAddress | None,
            str,
            str,
            bool,
            str,
            str,
        ]
    ] = []
    for section in main.iter():
        if _localname(section.tag) != "section":
            continue
        section_content = section.find("u:content", _NS)
        sec_phrase, sec_href = ("", "")
        if section_content is not None:
            sec_phrase, sec_href = _first_usc_ref(section_content)
        # Skip pure short-title / non-amendatory sections.
        if not any(_localname(a.tag) == "amendingAction" for a in section.iter()):
            continue

        for (
            unit_id,
            unit,
            inherited_address,
            effective_text,
            expires_text,
            inherited_via_classification,
        ) in _iter_instruction_units(section):
            actions = _amending_actions(unit)
            if not actions:
                continue
            unit_phrase, unit_href = _first_usc_ref(unit)
            # The leaf's OWN ref/prose is canonical; the section-level ref is only a
            # last resort (it would mis-target a leaf that amends a sibling section).
            # The inherited ancestor address threads the title for relative prose.
            target_phrase = unit_phrase or sec_phrase
            target_href = unit_href or sec_href
            explicit_title = _direct_target_title(target_phrase, target_href) or _address_title(
                inherited_address
            )
            # Plain-prose targets (converter-flattened refs) may not surface in
            # target_phrase; an explicit "Section X of title N" in the unquoted prose
            # is still an explicit title that prevents preamble fallback.
            if not explicit_title:
                raw_prefix = re.split(r'["“]', _text_of(unit), maxsplit=1)[0]
                if raw_prefix:
                    raw_addr = parse_usc_target_phrase(raw_prefix)
                    explicit_title = _address_title(raw_addr)
            if explicit_title:
                explicit_titles.add(explicit_title)
            unit_records.append(
                (
                    unit_id,
                    unit,
                    inherited_address,
                    effective_text,
                    expires_text,
                    inherited_via_classification,
                    sec_phrase,
                    sec_href,
                )
            )

    if plaw_title_scope and explicit_titles and any(
        t != plaw_title_scope for t in explicit_titles
    ):
        findings.append(
            USAmendatoryFinding(
                rule_id=PLAW_METADATA_SCOPE_CONFLICT_RULE_ID,
                message=(
                    f"PLAW preamble names title {plaw_title_scope} but explicit references "
                    f"name {sorted(explicit_titles)}; metadata title fallback withheld to avoid "
                    "cross-title target hijacking."
                ),
                statute_id=statute_id,
            )
        )
        plaw_title_scope = ""

    for unit_id, unit, inherited_address, effective_text, expires_text, inherited_via_classification, sec_phrase, sec_href in unit_records:
        actions = _amending_actions(unit)
        unit_phrase, unit_href = _first_usc_ref(unit)
        # The leaf's OWN ref/prose is canonical; the section-level ref is only a
        # last resort (it would mis-target a leaf that amends a sibling section).
        # The inherited ancestor address threads the title for relative prose.
        target_phrase = unit_phrase or sec_phrase
        target_href = unit_href or sec_href
        raw_text = _text_of(unit)
        quoted = _quoted_texts(unit)
        payload_node = _quoted_content_node(unit)
        sequence += 1
        instr = _lower_instruction(
            statute_id=statute_id,
            enacted=enacted,
            instruction_id=unit_id or (statute_id + "#instr" + str(sequence)),
            sequence=sequence,
            target_phrase=target_phrase,
            target_href=target_href,
            raw_text=raw_text,
            effective_text=effective_text,
            expires_text=expires_text,
            quoted=quoted,
            actions=actions,
            payload_node=payload_node,
            inherited_address=inherited_address,
            inherited_via_classification=inherited_via_classification,
            plaw_title_scope=plaw_title_scope,
            proof_title=proof_title,
        )
        instructions.append(instr)
        if instr.finding is not None:
            findings.append(instr.finding)
        if instr.target_address is not None and instr.target_address.path:
            title_targets.add(f"title {instr.target_address.path[0][1]}")

    return USAmendatoryReport(
        statute_id=statute_id,
        enacted=enacted,
        title_targets=tuple(sorted(title_targets)),
        instructions=tuple(instructions),
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# JSON projection helpers
# ---------------------------------------------------------------------------


def _operation_jsonable(op: LegalOperation | None) -> dict[str, Any] | None:
    if op is None:
        return None
    patch: dict[str, Any] | None = None
    if op.text_patch is not None:
        patch = {
            "kind": op.text_patch.kind.value,
            "match_text": op.text_patch.selector.match_text,
            "occurrence": op.text_patch.selector.occurrence,
            "replacement": op.text_patch.replacement,
        }
    return {
        "op_id": op.op_id,
        "sequence": op.sequence,
        "action": str(op.action),
        "target": str(op.target),
        "destination": str(op.destination) if op.destination else "",
        "anchor": str(op.anchor) if op.anchor else "",
        "witness_rule_id": op.witness_rule_id,
        "text_patch": patch,
        "payload_text": op.payload.text if op.payload is not None else "",
        "provenance_tags": list(op.provenance_tags),
        "statute_id": op.source.statute_id if op.source else "",
        "enacted": op.source.enacted if op.source else "",
        "effective": op.source.effective if op.source else "",
    }
