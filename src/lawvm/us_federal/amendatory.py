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

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.parse_witness import ParseWitness
from lawvm.core.provenance import OperationSource
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
RULE_ADD_AT_END = "us_amend_add_at_end"
RULE_AMEND_TO_READ = "us_amend_to_read"
RULE_REPEAL = "us_amend_repeal"
RULE_REDESIGNATE = "us_amend_redesignate"
RULE_STRIKE_UNIT = "us_amend_strike_structural_unit"
RULE_REDESIGNATE_RANGE = "us_amend_redesignate_range"
RULE_INSERT_NODE_AFTER = "us_amend_insert_node_after_unit"

UNLOWERED_FINDING_RULE_ID = "us_amendatory_unlowered"
TARGET_UNRESOLVED_FINDING_RULE_ID = "us_amendatory_target_unresolved"
NON_TITLE_TARGET_RULE_ID = "us_amendatory_target_non_us_code"

# USC nesting order (deepest-last). Used to type bare positional labels from a
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
_CANON_ROMAN_RE = re.compile(
    r"^m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$"
)
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


def _type_usc_segment_chain(
    labels: list[str], *, start_frontier: int = -1
) -> list[tuple[str, str]]:
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
        return (
            kind == "subsection"
            and _CANON_ROMAN_RE.match(label.strip().lower()) is not None
        )
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
    r"(?:\s+of\s+such\s+title\b|\s+is\s+amended\b|\s*,)"
)


# A leading sub-section anchor in an instruction unit: "(1) in subsection (a),
# by inserting …", "in paragraph (3), by striking …". This refines an inherited
# section/sub-section address by ONE more level: the edit applies inside the named
# sub-unit, not the whole inherited node. Anchored at the unit head so a mid-prose
# cross-reference ("in paragraph (1) of section 1322") is not mistaken for the
# edit's own scope. Only the first such anchor is consumed.
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
    r"\s*(?:,|[—–-])"
)


def _refine_with_leading_subunit_anchor(
    address: LegalAddress, raw_text: str
) -> LegalAddress:
    """Append a leading "in subsection (X)[(Y)...]" anchor to ``address``.

    Returns ``address`` unchanged when the unit has no leading sub-unit anchor (the
    edit applies directly to the inherited node). This is what disambiguates two
    sibling ops "(1) in subsection (a), by inserting …" / "(2) in subsection (b),
    …" that otherwise collapse to the same section address (and double-apply at the
    section-text surface). The named sub-unit's USC kind is taken from the prose
    verb ("subsection"/"paragraph"/...) — the enacted language is authoritative.
    """
    match = _LEADING_SUBUNIT_ANCHOR_RE.match(raw_text)
    if match is None:
        return address
    # The named sub-unit's USC kind comes from the prose verb ("subsection"/
    # "paragraph"/...) — the enacted language is authoritative for the first level.
    head_kind = match.group("kind")
    segments: list[tuple[str, str]] = [(head_kind, match.group("label"))]
    # Any further parenthesised tokens ("(a)(1)(A)") descend BELOW the prose verb's
    # level: thread the frontier from the named kind so they type by position.
    more = _SEGMENT_RE.findall(match.group("more") or "")
    if more:
        head_level = _USC_LEVELS.index(head_kind) if head_kind in _USC_LEVELS else 0
        segments.extend(_type_usc_segment_chain(more, start_frontier=head_level))
    return LegalAddress(path=(*address.path, *segments))


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
    segments = [
        seg
        for seg in (s for s in rest.split("/") if s)
        if seg not in ("note", "etseq", "et_seq")
    ]
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
_NON_TARGET_REF_CONTAINER_TAGS = frozenset({"quotedText", "quotedContent"})


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


def _quoted_texts(elem: ET.Element) -> list[str]:
    out: list[str] = []
    for q in elem.iter():
        if _localname(q.tag) == "quotedText":
            # Significant leading/trailing whitespace and punctuation INSIDE the
            # <quotedText> literal must survive (F1 leading space, F4 terminal
            # period). Collapse only internal formatting whitespace.
            out.append(_collapse_inner_ws("".join(q.itertext())))
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
    return ""


def _resolve_target(
    target_phrase: str,
    target_href: str,
    *,
    raw_text: str = "",
    inherited_address: LegalAddress | None = None,
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

    The relative/inherited steps NEVER invent a title; they only carry one that an
    enclosing instruction already resolved (no silent target hijack).
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
        # held out as the uncodified residual, never guessed onto a section.
        return None, "unresolved"

    prose_addr = parse_usc_target_phrase(target_phrase) if target_phrase else None
    href_addr = parse_usc_target_href(target_href) if target_href else None
    if prose_addr is not None and href_addr is not None:
        if prose_addr.path == href_addr.path:
            return prose_addr, "prose_href_agree"
        # Prose is canonical (the enacted language); href is a converter artifact.
        return prose_addr, "prose"
    if prose_addr is not None:
        return prose_addr, "prose"
    if href_addr is not None:
        return href_addr, "href"

    # (2) Relative prose under the inherited title. The leaf names a different
    # section than the inherited address (a conforming amendment to a sibling
    # section), so the section comes from the leaf's prose, the title from the
    # inherited address.
    inherited_title = _address_title(inherited_address)
    if raw_text and inherited_title:
        rel = parse_relative_usc_target(raw_text, inherited_title=inherited_title)
        if rel is not None:
            return _refine_with_leading_subunit_anchor(rel, raw_text), "relative_prose"

    # (3) Pure inheritance: the leaf carries no section of its own; it amends the
    # same node the enclosing instruction resolved — refined by any leading
    # "in subsection (X)" anchor so sibling sub-unit edits do not collapse onto the
    # same address (and double-apply at the section-text surface).
    if inherited_address is not None:
        return (
            _refine_with_leading_subunit_anchor(inherited_address, raw_text),
            "inherited",
        )

    return None, "unresolved"


def _classify_action(actions: list[str], raw_text: str) -> str:
    """Map the amendingAction verb sequence / prose to a canonical family token."""
    has = set(actions)
    lowered = raw_text.lower()
    if "repeal" in has or re.search(r"\bis repealed\b", lowered):
        return "repeal"
    if "redesignate" in has or "redesignat" in lowered:
        return "redesignate"
    if ("amend" in has and "to read" in lowered) or "to read as follows" in lowered:
        return "amend_to_read"
    has_strike = "delete" in has or "striking" in lowered
    has_insert = "insert" in has or "inserting" in lowered
    has_anchor = " after " in lowered or " before " in lowered
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


def _redesignate_destination(
    raw_text: str, target: LegalAddress
) -> tuple[LegalAddress, LegalAddress] | None:
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
_STRIKE_UNIT_RE = re.compile(
    rf"by\s+striking\s+(?P<kind>{_KIND_WORDS})\s+\((?P<label>[0-9A-Za-z]+)\)\s*\.?\s*$",
    re.IGNORECASE,
)
# A strike-subsection instruction with FUTURE-effective language ("Effective on the
# date that is N ... after …", "Effective <date>, …", "shall take effect …") is a
# SUNSET / deferred repeal, not an in-window amendment. The temporal layer owns it;
# lowering it to an immediate REPEAL would (wrongly) delete a node that is still in
# force in the window's after edition. We refuse to lower these as immediate ops.
_FUTURE_EFFECTIVE_RE = re.compile(
    r"effective\s+(?:on\s+the\s+date|[A-Z][a-z]+\s+\d|\w+\s+\d{1,2},\s*\d{4})"
    r"|shall\s+take\s+effect\b",
    re.IGNORECASE,
)
# "redesignating paragraphs (3) through (7) as paragraphs (4) through (8)" — a
# contiguous range relabel. The two endpoints define the shift; each member is
# relabelled by the same offset (the USC labels in a numeric range are
# consecutive). Only the digit-numbered (paragraph) range is materializable as a
# pure relabel without knowing the alphabet sequence, so we keep both endpoints
# and let the dry-run relabel the members it can enumerate.
_REDESIGNATE_RANGE_RE = re.compile(
    rf"redesignating\s+(?:{_KIND_WORDS})s?\s+"
    r"\((?P<from_lo>[0-9A-Za-z]+)\)\s+through\s+\((?P<from_hi>[0-9A-Za-z]+)\)\s+as\s+"
    rf"(?:{_KIND_WORDS})s?\s+"
    r"\((?P<to_lo>[0-9A-Za-z]+)\)\s+through\s+\((?P<to_hi>[0-9A-Za-z]+)\)",
    re.IGNORECASE,
)
# "inserting after paragraph (N) the following[ new <kind>]: <block>" — splice the
# quoted block as a NEW node positioned after the named anchor unit.
_INSERT_NODE_AFTER_RE = re.compile(
    rf"inserting\s+after\s+(?P<kind>{_KIND_WORDS})\s+\((?P<label>[0-9A-Za-z]+)\)"
    r"(?:\s*\([0-9A-Za-z]+\))*\s+(?:\(as\s+so\s+redesignated\)\s+)?the\s+following",
    re.IGNORECASE,
)


def _strike_structural_unit(
    raw_text: str, target: LegalAddress
) -> LegalAddress | None:
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


def _redesignate_range(
    raw_text: str, target: LegalAddress
) -> tuple[tuple[LegalAddress, LegalAddress], ...] | None:
    """Parse a ``redesignating (a) through (b) as (c) through (d)`` range.

    Returns a tuple of ``(from_addr, to_addr)`` pairs, one per member of the
    digit-numbered range, or ``None`` when the form is not a numeric range (an
    alphabetic range cannot be enumerated without the label alphabet, so it is left
    as a typed finding rather than guessed).
    """
    m = _REDESIGNATE_RANGE_RE.search(raw_text)
    if m is None:
        return None
    lo, hi = m.group("from_lo"), m.group("from_hi")
    to_lo, to_hi = m.group("to_lo"), m.group("to_hi")
    if not (lo.isdigit() and hi.isdigit() and to_lo.isdigit() and to_hi.isdigit()):
        return None
    span = int(hi) - int(lo)
    if span < 0 or (int(to_hi) - int(to_lo)) != span:
        return None
    offset = int(to_lo) - int(lo)
    leaf_index = max(target.depth() - 1, 0)
    pairs: list[tuple[LegalAddress, LegalAddress]] = []
    # Relabel from the HIGH end down so an intermediate relabel never collides with
    # a member not yet moved (e.g. (3)->(4),(4)->(5) must move (4) first).
    for n in range(int(hi), int(lo) - 1, -1):
        from_label = str(n)
        to_label = str(n + offset)
        kind = _label_level(from_label, leaf_index)
        pairs.append(
            (
                LegalAddress(path=(*target.path, (kind, from_label))),
                LegalAddress(path=(*target.path, (kind, to_label))),
            )
        )
    return tuple(pairs)


def _lower_instruction(
    *,
    statute_id: str,
    enacted: str,
    instruction_id: str,
    sequence: int,
    target_phrase: str,
    target_href: str,
    raw_text: str,
    quoted: list[str],
    actions: list[str],
    payload_node: IRNode | None,
    inherited_address: LegalAddress | None = None,
) -> USAmendmentInstruction:
    source = OperationSource(statute_id=statute_id, enacted=enacted, raw_text=raw_text)
    address, resolution_status = _resolve_target(
        target_phrase,
        target_href,
        raw_text=raw_text,
        inherited_address=inherited_address,
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

    # Off-Title-11 targets are resolvable but out of this surface's scope; record
    # them as needs_review rather than emit a candidate into the wrong corpus.
    if address.path and address.path[0] == ("title", "11"):
        on_title_11 = True
    else:
        on_title_11 = False

    op: LegalOperation | None = None
    extra_ops: list[LegalOperation] = []
    witness_rule_id = UNLOWERED_FINDING_RULE_ID
    status = "unsupported"
    finding: USAmendatoryFinding | None = None

    def _make_op(
        action: StructuralAction,
        *,
        rule_id: str,
        payload: IRNode | None = None,
        anchor: LegalAddress | None = None,
        destination: LegalAddress | None = None,
        text_patch: TextPatchSpec | None = None,
        target: LegalAddress | None = None,
    ) -> LegalOperation:
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
            provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}"),
        )

    # A PRECISE-text strike (a quoted match_text, not a whole-node operand) is
    # located by its match_text, not by its sub-section node. When the target's
    # leading sub-section letter is a roman-form letter the source-tree split flags
    # as ambiguous (and may duplicate, e.g. ``10 U.S.C. 284(i)(3)``), scope the
    # strike to the section so the dry-run anchors on the unique match_text instead
    # of risking a locate onto the phantom duplicate node. Whole-node ops keep the
    # full ladder path (they genuinely need the located node). This trades nothing:
    # the precise quoted string is the strike's real, unambiguous anchor.
    _text_strike_target = (
        _section_scoped(address)
        if _has_roman_ambiguous_subsection_head(address)
        else None
    )

    if family == "strike_insert":
        if len(quoted) >= 2:
            old, new = quoted[0], quoted[1]
            op = _make_op(
                StructuralAction.TEXT_REPLACE,
                rule_id=RULE_STRIKE_INSERT,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(
                        match_text=old,
                        occurrence=-1 if "each place" in raw_text.lower() else 0,
                    ),
                    replacement=new,
                ),
                target=_text_strike_target,
            )
            witness_rule_id = RULE_STRIKE_INSERT
        elif payload_node is not None and quoted:
            # strike <label> and insert <block> -> whole-node REPLACE of the struck unit.
            op = _make_op(
                StructuralAction.REPLACE,
                rule_id=RULE_STRIKE_INSERT,
                payload=payload_node,
            )
            witness_rule_id = RULE_STRIKE_INSERT
        elif payload_node is not None:
            op = _make_op(
                StructuralAction.REPLACE, rule_id=RULE_STRIKE_INSERT, payload=payload_node
            )
            witness_rule_id = RULE_STRIKE_INSERT
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "strike-and-insert without two quoted strings or a quoted block payload",
            )
    elif family == "strike":
        if quoted:
            op = _make_op(
                StructuralAction.TEXT_REPEAL,
                rule_id=RULE_STRIKE,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.DELETE,
                    selector=TextSelector(
                        match_text=quoted[0],
                        occurrence=-1 if "each place" in raw_text.lower() else 0,
                    ),
                ),
                target=_text_strike_target,
            )
            witness_rule_id = RULE_STRIKE
        else:
            # "is amended by striking subsection (X)" — a structural-unit strike (a
            # sub-section REPEAL), no quoted phrase. Lower to a REPEAL of the named
            # node so the dry-run can remove it at sub-section granularity.
            struck = _strike_structural_unit(raw_text, address)
            if struck is not None:
                op = LegalOperation(
                    op_id=instruction_id,
                    sequence=sequence,
                    action=StructuralAction.REPEAL,
                    target=struck,
                    source=source,
                    witness_rule_id=RULE_STRIKE_UNIT,
                    provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}"),
                )
                address = struck
                witness_rule_id = RULE_STRIKE_UNIT
            else:
                finding = _finding(
                    UNLOWERED_FINDING_RULE_ID,
                    "strike with no quoted string and no recognizable structural unit",
                )
    elif family == "insert_after":
        node_anchor = _INSERT_NODE_AFTER_RE.search(raw_text)
        if len(quoted) >= 2 and node_anchor is None:
            new_text, anchor_text = quoted[0], quoted[1]
            op = _make_op(
                StructuralAction.TEXT_REPLACE,
                rule_id=RULE_INSERT_AFTER,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=anchor_text),
                    replacement=anchor_text + new_text,
                ),
            )
            witness_rule_id = RULE_INSERT_AFTER
        elif node_anchor is not None and payload_node is not None:
            # "inserting after paragraph (N) the following: <block>" — splice the
            # quoted block as a NEW node positioned AFTER the named anchor unit. The
            # anchor node hangs off the resolved target; the dry-run inserts the
            # payload immediately after that node's span.
            anchor_label = node_anchor.group("label")
            anchor_kind = _label_level(anchor_label, max(address.depth() - 1, 0))
            anchor_addr = LegalAddress(path=(*address.path, (anchor_kind, anchor_label)))
            op = _make_op(
                StructuralAction.INSERT,
                rule_id=RULE_INSERT_NODE_AFTER,
                payload=payload_node,
                anchor=anchor_addr,
            )
            witness_rule_id = RULE_INSERT_NODE_AFTER
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID,
                "insert-after without both inserted text and anchor text",
            )
    elif family == "add_at_end":
        if payload_node is not None:
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
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID, "add-at-end without a quoted payload"
            )
    elif family == "amend_to_read":
        if payload_node is not None:
            op = _make_op(
                StructuralAction.REPLACE, rule_id=RULE_AMEND_TO_READ, payload=payload_node
            )
            witness_rule_id = RULE_AMEND_TO_READ
        else:
            finding = _finding(
                UNLOWERED_FINDING_RULE_ID, "amend-to-read without a quoted replacement block"
            )
    elif family == "repeal":
        op = _make_op(StructuralAction.REPEAL, rule_id=RULE_REPEAL)
        witness_rule_id = RULE_REPEAL
    elif family == "redesignate":
        pair = _redesignate_destination(raw_text, address)
        range_pairs = None if pair is not None else _redesignate_range(raw_text, address)
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
                provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}"),
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
                    provenance_tags=("us_amendatory", f"target_resolution:{resolution_status}"),
                )
                if op is None:
                    op = node_op
                else:
                    extra_ops.append(node_op)
            witness_rule_id = RULE_REDESIGNATE_RANGE
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
        status = "accepted" if on_title_11 else "needs_review"
        if not on_title_11:
            finding = _finding(
                NON_TITLE_TARGET_RULE_ID,
                f"resolved target is outside Title 11 ({address.path[0] if address.path else ()}); "
                "candidate withheld from Title 11 scope",
            )

    return USAmendmentInstruction(
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


def _first_usc_ref(content: ET.Element) -> tuple[str, str]:
    """Return ``(prose_phrase, href)`` for the first USC structural ref in content.

    Refs that live inside a ``<quotedText>`` / ``<quotedContent>`` operand subtree
    are SKIPPED: such a ref is part of the struck/inserted literal (a cross-citation
    being edited as text), never the instruction's own amendment target. Scanning
    them would hijack the target onto the operand's cited section instead of the
    section actually being amended (no silent target hijack, Prime Directive).
    """
    for ref, in_non_target in _iter_with_non_target_depth(content):
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
) -> Iterable[tuple[ET.Element, bool]]:
    """Pre-order walk yielding ``(element, inside_non_target_container)`` per node.

    ``inside_non_target_container`` is ``True`` once the walk has descended into (or
    onto) a quoted-operand subtree (see ``_NON_TARGET_REF_CONTAINER_TAGS``) — the
    region whose refs are struck/inserted operand literals, never the instruction's
    own amendment target. ``root`` itself is yielded with its own state so a scan
    rooted inside such a container is handled too.
    """

    def _walk(node: ET.Element, inside: bool) -> Iterable[tuple[ET.Element, bool]]:
        here = inside or _localname(node.tag) in _NON_TARGET_REF_CONTAINER_TAGS
        yield node, here
        for child in node:
            yield from _walk(child, here)

    yield from _walk(root, False)


def _unit_own_target(unit: ET.Element, *, exclude: ET.Element | None = None) -> LegalAddress | None:
    """Resolve a unit's OWN absolute USC target from its direct prose/ref, if any.

    Only the unit's own ``<content>`` text (not its nested amendatory sub-units) is
    consulted, so a parent's "Section X of title N is amended—" resolves to X
    without bleeding a child's "in section Y" into the parent's target. ``exclude``
    drops a sub-tree (the descendant leaf units) from the prose scan.
    """
    phrase, href = _first_usc_ref(unit)
    addr, status = _resolve_target(phrase, href)
    if addr is not None:
        return addr
    # No ref: try the unit's own direct prose head ("Section X of title N ...").
    # Scan only text outside the excluded descendant sub-units.
    own_text = _shallow_text(unit, exclude=exclude)
    return parse_usc_target_phrase(own_text)


def _shallow_text(elem: ET.Element, *, exclude: ET.Element | None = None) -> str:
    """Concatenated text of ``elem`` excluding the ``exclude`` sub-tree's text.

    Text inside a quoted-operand container (see ``_NON_TARGET_REF_CONTAINER_TAGS``)
    is dropped (its *tail* — the surrounding instruction prose — is kept). This
    keeps the prose head scan (``_unit_own_target``) from parsing a quoted operand
    literal such as ``"section 7222 of title 10, United States Code"`` as the
    unit's own amendment target: that string is the struck/inserted phrase, not
    the section being amended. Mirrors the same no-hijack discipline as
    :func:`_first_usc_ref`.
    """
    parts: list[str] = []

    def _is_dropped(node: ET.Element) -> bool:
        # ``exclude`` drops the descendant leaf sub-unit; quoted operands drop the
        # struck/inserted literal. In both cases the node's OWN text is suppressed
        # but its TAIL (the surrounding instruction prose) is kept by the caller.
        return (
            node is exclude
            or _localname(node.tag) in _NON_TARGET_REF_CONTAINER_TAGS
        )

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


def _iter_instruction_units(
    section: ET.Element,
) -> Iterable[tuple[str, ET.Element, LegalAddress | None]]:
    """Yield ``(unit_id, element, inherited_address)`` for each amendatory unit.

    A unit is either the section's own direct ``<content>`` (flat instruction) or
    each nested ``<paragraph>/<subparagraph>`` that carries its own amendingAction
    ("(1) in subsection (b)— (A) by striking…"). The third element is the USC
    address resolved by the nearest ENCLOSING instruction (a parent unit's
    "Section X of title N is amended—"), threaded down so a leaf that names no
    title of its own ("(1) by striking …") or only a relative section ("(B) in
    section Y, …") can be resolved (no silent target hijack — the inherited title
    is one a parent already resolved, never invented).
    """
    unit_tags = ("subsection", "paragraph", "subparagraph", "clause")

    def _is_unit(elem: ET.Element) -> bool:
        return _localname(elem.tag) in unit_tags and any(
            _localname(a.tag) == "amendingAction" for a in elem.iter()
        )

    nested = [elem for elem in section.iter() if _is_unit(elem)]

    # Map each unit to its nearest amendatory-unit ancestor (within the section),
    # so we can thread the parent instruction's resolved target into leaf units.
    parent_of: dict[ET.Element, ET.Element | None] = {}
    stack: list[ET.Element] = []

    def _descend(node: ET.Element) -> None:
        pushed = False
        if node is not section and _is_unit(node):
            parent_of[node] = stack[-1] if stack else None
            stack.append(node)
            pushed = True
        for child in node:
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

    if leaf_units:
        for elem in leaf_units:
            uid = elem.get("identifier") or elem.get("id") or ""
            # Inherited target = the nearest ancestor instruction whose OWN prose/ref
            # resolves to a USC address (excluding this leaf's sub-tree from that
            # ancestor's prose so the leaf's own "in section Y" never leaks up).
            inherited: LegalAddress | None = None
            ancestor = parent_of.get(elem)
            child_for_exclude = elem
            # The ancestors BETWEEN the leaf and the section-resolving ancestor each
            # carry a leading sub-unit anchor ("(A) in paragraph (1)(A)—") that scopes
            # the edit one ladder rung deeper. Collect them leaf→up; they are applied
            # top→down onto the inherited section so the leaf's own "(i) in clause
            # (ii)" lands on the FULL ladder, not a truncated section/clause path.
            intermediate_anchors: list[str] = []
            while ancestor is not None and inherited is None:
                inherited = _unit_own_target(ancestor, exclude=child_for_exclude)
                if inherited is None:
                    intermediate_anchors.append(_text_of(ancestor))
                child_for_exclude = ancestor
                ancestor = parent_of.get(ancestor)
            if inherited is None:
                # Fall back to the section's own content ref ("Section X ... — (1)...").
                section_content = section.find("u:content", _NS)
                if section_content is not None:
                    sp, sh = _first_usc_ref(section_content)
                    inherited, _ = _resolve_target(sp, sh)
            if inherited is not None and intermediate_anchors:
                # Apply outermost intermediate anchor first (it is the shallowest
                # scope); each refinement descends from the prior frontier.
                for anchor_text in reversed(intermediate_anchors):
                    inherited = _refine_with_leading_subunit_anchor(
                        inherited, anchor_text
                    )
            yield uid, elem, inherited
        return
    # Flat instruction: the section's own content blocks.
    yield (section.get("identifier") or section.get("id") or ""), section, None


def lower_plaw_amendatory(data: bytes, *, statute_id: str = "", enacted: str = "") -> USAmendatoryReport:
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

    for section in main.iter():
        if _localname(section.tag) != "section":
            continue
        # Section-level target ref (carried into sub-units without their own ref).
        section_content = section.find("u:content", _NS)
        sec_phrase, sec_href = ("", "")
        if section_content is not None:
            sec_phrase, sec_href = _first_usc_ref(section_content)
        # Skip pure short-title / non-amendatory sections.
        if not any(_localname(a.tag) == "amendingAction" for a in section.iter()):
            continue

        for unit_id, unit, inherited_address in _iter_instruction_units(section):
            actions = _amending_actions(unit)
            if not actions:
                continue
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
                instruction_id=unit_id or f"{statute_id}#instr{sequence}",
                sequence=sequence,
                target_phrase=target_phrase,
                target_href=target_href,
                raw_text=raw_text,
                quoted=quoted,
                actions=actions,
                payload_node=payload_node,
                inherited_address=inherited_address,
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
    }
