"""fmx4_amendment_grammar.py — REAL EU amendment-grammar lowering (Increment 0).

``ops_parser.py`` is a shallow regex placeholder that runs on tag-stripped XHTML
and has NO quoted-block capture (design §1.5). EU amending acts quote the new
text INLINE: *"Article 5 is replaced by the following: '…'"*. The replacement
payload is the load-bearing content; a parser that drops it cannot replay.

This module lowers amendment instructions from STRUCTURED Formex (FMX4), where
the quoted replacement block is marked up (``QUOT.START`` / ``QUOT.END``), into
typed :class:`LegalOperation`s with the captured payload as an :class:`IRNode`
and full :class:`OperationSource` provenance (statute_id + raw_text). It mirrors
how FI/UK lower from structured source rather than scraping flattened text.

Scope (Increment 0 + Increment 1)
---------------------------------
The grammar covers the WHOLE-ARTICLE instruction families that dominate EU
amending acts, with quoted-block payload capture:

1. REPLACE — *"Article N is replaced by the following: '<block>'"* → REPLACE op
   on ``(article, N)`` with the quoted block as payload IR.
2. INSERT — *"the following Article Na is inserted: '<block>'"* → INSERT op.
3. REPEAL — *"Article N is deleted"* / *"is repealed"* → REPEAL op (no payload).
4. Sub-article paragraph REPLACE — *"in Article N, paragraph M is replaced by the
   following: '<block>'"* → REPLACE on ``(article, N)/(paragraph, M)``.

Increment 1 ADDS (each a new ``witness_rule_id`` + typed diagnostic on the gap):

5. Sub-article POINT REPLACE — *"in Article N, point (b) is replaced by the
   following: '<block>'"* / *"… is replaced by '<inline>'"* → REPLACE on
   ``(article, N)/(point, b)`` (``EU_FMX4.SUBART_POINT_REPLACE``).
6. Sub-article POINT REPEAL — *"in Article N, point (b) is deleted"* → REPEAL on
   ``(article, N)/(point, b)`` (``EU_FMX4.SUBART_POINT_REPEAL``).
7. Corrigendum ``for:…read:…`` — *"on page P, … for: '<for>' read: '<read>'"* →
   a TEXT_REPLACE-shaped REPLACE carrying the read-payload, classified as a
   corrigendum (``EU_FMX4.CORRIGENDUM_FOR_READ``). Corrigenda apply on the
   corrected act's own timeline (design §3.5), not a fresh date.
8. ANNEX REPLACE — *"Annex N is replaced by the following: '<block>'"* and the
   ANNEX-root manifestation form (the real degree-57 amending acts —
   ``32016R0466`` etc. — are acquired as an ``ANNEX``-rooted new-annex body whose
   QUOT-START/END payload is the replacement annex) → REPLACE on
   ``(annex, N)`` (``EU_FMX4.WHOLE_ANNEX_REPLACE`` /
   ``EU_FMX4.ANNEX_ROOT_REPLACE``).

Root hardening (design §1.4, goal 4): the amending manifestation may be rooted at
``ACT`` (article-instruction form), ``DOC`` (a publication envelope — often the
metadata-only manifestation, no enacting terms), or ``ANNEX`` (the
new-annex-replacement form). ``lower_amending_act`` resolves all three: it digs
out an embedded ``ACT`` if present, lowers the ANNEX-root form structurally, and
emits a typed ``eu_fmx4_grammar_envelope_no_enacting_terms`` residual for a
genuinely instruction-free envelope — never a crash, never a silent zero.

Increment 3 ADDS the harder sub-article shapes Increment 2 left as typed
residuals (each a new ``witness_rule_id``):

9.  Sub-article POINT INSERT — *"in Article N, the following point (c) is
    inserted: '<block>'"* → INSERT on ``(article,N)/(point,c)``
    (``EU_FMX4.SUBART_POINT_INSERT``).
10. SUBPARAGRAPH REPLACE/REPEAL — *"in Article N, the second subparagraph of
    paragraph M is replaced by the following / is deleted"* → REPLACE/REPEAL on
    ``(article,N)/(paragraph,M)/(subparagraph,K)``, the ordinal normalised to a
    1-based index (``EU_FMX4.SUBART_SUBPARAGRAPH_REPLACE`` / ``…_REPEAL``).
11. INDENT (list-dash item) REPLACE/REPEAL — *"the second indent of Article N is
    replaced by the following / is deleted"* → REPLACE/REPEAL on
    ``(article,N)/(item,K)`` (``EU_FMX4.INDENT_REPLACE`` / ``EU_FMX4.INDENT_REPEAL``).
12. RENUMBER — *"Article N is renumbered as Article M"* → a RENUMBER op carrying
    the destination in a ``renumber_to=`` provenance tag
    (``EU_FMX4.ARTICLE_RENUMBER``). The EU apply seam OWNS renumber as a typed
    ``eu_replay_unsupported_action`` skip today — the move is visible to
    ordering/conflict detection and the destination is recorded, not dropped.

Increment 3 also threads SEPARATE-annex payloads (Goal 2): the indirect-annex
shape accepts an optional ``resolve_separate_annex`` resolver that materialises a
replacement annex shipped as a distinct manifestation; when it returns text the
payload is real (``annex_payload=separate_resolved``) rather than the
Increment-2 recorded gap.

Increment 4 ADDS the OMNIBUS MULTI-POINT (NP) instruction lane (#221 backlog #1):
the dominant real EU amender shape — *"Regulation (EU) No X is amended as
follows: (1) Article 3 is replaced …; (2) in Article 5, paragraph 2 is replaced
…"* — carries each sub-instruction in a numbered ``<NP>`` element (``NO.P``
marker + ``TXT`` verb clause + the QUOT payload). The pre-Increment-4 grammar
stripped ``NP`` wholesale as noise, so such an amender lowered to ZERO ops and
every anchor whose closure contained one was commensurability-suspect. Now an
"… is amended as follows:" instruction ARTICLE iterates its top-level NPs as
SUB-INSTRUCTIONS, each lowered against a CONTEXT path accumulated from nest
parents (*"(2) Article 3 is amended as follows: (a) paragraph 1 is replaced…"*
recurses with ``article:3`` as context). The leaf grammar reuses the existing
point-level rule vocabulary (``SUBART_POINT_REPLACE`` / ``SUBART_POINT_REPEAL``
/ ``SUBART_POINT_INSERT`` / ``SUBART_PARAGRAPH_REPLACE`` /
``SUBART_SUBPARAGRAPH_*`` / ``WHOLE_ARTICLE_*``) and adds the missing
paragraph/subparagraph INSERT + REPEAL family. Payload text for structural
quoted bodies is extracted GRAFTER-COMMENSURABLY (``TI.ART`` headings and
``NO.PARAG`` markers excluded — in the IR they are labels, not text), so a
replay-materialized unit renders exactly as a grafted consolidation does.

Instruction ARTICLEs that are NOT amendment instructions at all (a directive
amender's own substantive provisions, entry-into-force boilerplate) are typed
``eu_fmx4_grammar_non_amending_provision`` (family ``non_amending_provision``)
— visible, non-gap: they cannot touch the base act, so they must not
commensurability-poison closure scoring. Annex-lane gaps (embedded
annex-instruction indirection, annex-internal point edits) are typed with
annex-lane families for the same reason: the EU anchor compare surface is
article-only.

Every still-unhandled instruction shape (move-without-renumber, mixed multi-edit
prose) remains a typed :class:`AmendmentGrammarDiagnostic`
(``eu_fmx4_grammar_uncovered_instruction``) — counted, never silently dropped.
``lower_amending_act`` returns a :class:`LoweringResult` carrying ops, diagnostics,
and the coverage denominator so coverage % is measured, not asserted.

Each op carries ``witness_rule_id`` naming the grammar rule that produced it
(the falsifiable-hypothesis footing the other frontends carry).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Optional

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.provenance import OperationSource
from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.core.semantic_types import (
    IRNodeKind,
    StructuralAction,
    TextPatchKindEnum,
)

# ---------------------------------------------------------------------------
# Typed diagnostic + result carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AmendmentGrammarDiagnostic:
    """A typed record for an instruction the Increment-0 grammar did not lower."""

    rule_id: str
    reason: str
    source_excerpt: str
    family: str = "extraction_gap"

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "reason": self.reason,
            "source_excerpt": self.source_excerpt,
            "family": self.family,
        }


@dataclass
class LoweringResult:
    """Result of lowering one amending act's FMX4 enacting terms."""

    amending_celex: str
    ops: list[LegalOperation] = field(default_factory=list)
    diagnostics: list[AmendmentGrammarDiagnostic] = field(default_factory=list)
    instruction_count: int = 0  # the coverage denominator

    @property
    def covered_count(self) -> int:
        return len(self.ops)

    @property
    def coverage_fraction(self) -> float:
        if self.instruction_count == 0:
            return 0.0
        return self.covered_count / self.instruction_count


# ---------------------------------------------------------------------------
# Instruction classification (the grammar)
# ---------------------------------------------------------------------------

# "Article 5 is replaced by the following" / "Article 5 of Regulation (EU)
# 2016/44 is replaced ...". The gap between the target and the verb admits ONLY
# an instrument citation ("of Regulation …"), never an arbitrary tract: with a
# free ``.*?`` this rule SWALLOWED sub-article instructions whose own rule
# missed ("In Article 2 of Regulation 923/2012, point 104 is replaced …") and
# nuked the whole article down to the point payload — a mis-lowering CONVICTED
# by the #221 oracle-touch metric at 32012R0923@20150630.
_RE_ARTICLE_REPLACE = re.compile(
    r"\bArticle\s+(?P<num>\d+[a-z]?)\b(?:\s+of\s+[^,;:]{0,90}?)?\s*,?\s*"
    r"(?:is|shall\s+be)\s+replaced\s+by\s+the\s+following\b",
    re.I | re.S,
)
# "in Article 5, paragraph 2 is replaced by the following"
_RE_SUBART_REPLACE = re.compile(
    r"\bin\s+Article\s+(?P<art>\d+[a-z]?)\b.*?\bparagraph\s+(?P<par>\d+[a-z]?)\b"
    r".*?\bis\s+replaced\s+by\s+the\s+following\b",
    re.I | re.S,
)
# "the following Article 5a is inserted"
_RE_ARTICLE_INSERT = re.compile(
    r"\bthe\s+following\s+Article\s+(?P<num>\d+[a-z]?)\b.*?\bis\s+inserted\b",
    re.I | re.S,
)
# "Article 5 is deleted" / "is repealed" (same tight target-verb adjacency as
# _RE_ARTICLE_REPLACE — a free gap swallowed sub-article repeals whole).
_RE_ARTICLE_REPEAL = re.compile(
    r"\bArticle\s+(?P<num>\d+[a-z]?)\b(?:\s+of\s+[^,;:]{0,90}?)?\s*,?\s*"
    r"(?:is|shall\s+be)\s+(?:deleted|repealed)\b",
    re.I | re.S,
)
# "in Article 12, point (b) is replaced by the following" / "... is replaced by
# '...'" / "In Article 2 of Regulation X, point 104 is replaced by ..." — the
# point label may be parenthesized ("(b)"), bare ("104" — the real 32015R0340
# shape the whole-article rule used to swallow, nuking Article 2 of 32012R0923
# down to the single point payload: a mis-lowering CONVICTED by the #221
# oracle-touch metric at 32012R0923@20150630), or dotted ("3.1").
_RE_SUBART_POINT_REPLACE = re.compile(
    r"\bin\s+Article\s+(?P<art>\d+[a-z]?)\b.*?"
    r"\bpoint\s+\(?(?P<point>[a-z0-9]{1,4}(?:\.[a-z0-9]{1,3}){0,3})\)?"
    r"[\s,]*\b(?:is|shall\s+be)\s+replaced\s+by\b",
    re.I | re.S,
)
# "in Article 12, point (b) is deleted" (same label liberality as REPLACE).
_RE_SUBART_POINT_REPEAL = re.compile(
    r"\bin\s+Article\s+(?P<art>\d+[a-z]?)\b.*?"
    r"\bpoint\s+\(?(?P<point>[a-z0-9]{1,4}(?:\.[a-z0-9]{1,3}){0,3})\)?"
    r"[\s,]*\b(?:is|shall\s+be)\s+(?:deleted|repealed)\b",
    re.I | re.S,
)
# Increment 3 — harder sub-article shapes Increment 2 left as typed residuals.
#
# POINT INSERT — *"in Article N, the following point (c) is inserted/added"*. The
# new point body is the QUOT block (or inline). INSERT (not REPLACE), so the
# point is ADDED under the article, not overwritten.
_RE_SUBART_POINT_INSERT = re.compile(
    r"\bin\s+Article\s+(?P<art>\d+[a-z]?)\b.*?\bthe\s+following\s+point\s+"
    r"\((?P<point>[a-z0-9]+)\).*?\bis\s+(?:inserted|added)\b",
    re.I | re.S,
)
# SUBPARAGRAPH REPLACE — *"in Article N, the (first|second|…|Kth) subparagraph of
# paragraph M is replaced by the following: '<block>'"*. The ordinal word is
# normalised to a 1-based index label so the op targets
# ``(article,N)/(paragraph,M)/(subparagraph,K)``.
_RE_SUBART_SUBPARA_REPLACE = re.compile(
    r"\bin\s+Article\s+(?P<art>\d+[a-z]?)\b.*?\bthe\s+(?P<ord>first|second|third|"
    r"fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+"
    r"subparagraph\b(?:\s+of\s+paragraph\s+(?P<par>\d+[a-z]?))?"
    r".*?\bis\s+replaced\s+by\s+the\s+following\b",
    re.I | re.S,
)
# SUBPARAGRAPH REPEAL — *"… the (second) subparagraph of paragraph M is deleted"*.
_RE_SUBART_SUBPARA_REPEAL = re.compile(
    r"\bin\s+Article\s+(?P<art>\d+[a-z]?)\b.*?\bthe\s+(?P<ord>first|second|third|"
    r"fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+"
    r"subparagraph\b(?:\s+of\s+paragraph\s+(?P<par>\d+[a-z]?))?"
    r".*?\bis\s+(?:deleted|repealed)\b",
    re.I | re.S,
)
# INDENT (list dash item) REPLACE/REPEAL — *"the (first|second|…) indent of
# Article N(/paragraph M) is replaced by the following: '<block>'"* / *"… is
# deleted"*. FMX4 lists below a point are dash-INDENTs; the ordinal indexes them.
_RE_INDENT_REPLACE = re.compile(
    r"\bthe\s+(?P<ord>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|\d+(?:st|nd|rd|th)?)\s+indent\b.*?\bof\s+Article\s+(?P<art>\d+[a-z]?)\b"
    r".*?\bis\s+replaced\s+by\s+the\s+following\b",
    re.I | re.S,
)
_RE_INDENT_REPEAL = re.compile(
    r"\bthe\s+(?P<ord>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|\d+(?:st|nd|rd|th)?)\s+indent\b.*?\bof\s+Article\s+(?P<art>\d+[a-z]?)\b"
    r".*?\bis\s+(?:deleted|repealed)\b",
    re.I | re.S,
)
# RENUMBER — *"Article N is renumbered (as) Article M"* and the point/paragraph
# renumber *"points (a) to (c) … are renumbered …"*. The structural intent
# (move/relabel) is captured as a RENUMBER op; the EU apply seam currently owns
# RENUMBER as a typed ``eu_replay_unsupported_action`` skip (never silently lost).
_RE_ARTICLE_RENUMBER = re.compile(
    r"\bArticle\s+(?P<from>\d+[a-z]?)\b\s+(?:is|shall\s+be)\s+renumbered\b"
    r"(?:\s+(?:as|to)\s+Article\s+(?P<to>\d+[a-z]?))?",
    re.I | re.S,
)

#: Ordinal word → 1-based index. Covers the spelled forms EU drafters use for
#: subparagraph/indent ordinals; arabic ordinals ("2nd") are handled separately.
_ORDINAL_WORDS = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
}


def _ordinal_to_index(token: str) -> str:
    """Normalise a spelled or arabic ordinal ('second', '2nd', '2') to '2'."""
    t = token.strip().lower()
    if t in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[t]
    m = re.match(r"(\d+)(?:st|nd|rd|th)?$", t)
    if m:
        return m.group(1)
    return t
# Corrigendum formula: "... for: '<for>' read: '<read>'" (the classic OJ
# corrigendum shape). The replacement (read) value is the operative payload.
_RE_CORRIGENDUM_FOR_READ = re.compile(
    r"\bfor\s*:\s*['‘’“”\"]?(?P<for>.+?)['‘’“”\"]?\s*"
    r"\bread\s*:\s*['‘’“”\"]?(?P<read>.+?)['‘’“”\"]?\s*$",
    re.I | re.S,
)
# "the controller" style inline single-quoted replacement payload (no QUOT block)
_RE_INLINE_QUOTED = re.compile(
    r"\bis\s+replaced\s+by\s+['‘’“”](?P<inline>[^'‘’“”]+)"
    r"['‘’“”]",
    re.I | re.S,
)
# "Annex II is replaced by the following" / "Annex III is replaced by the text ..."
_RE_ANNEX_REPLACE = re.compile(
    r"\bAnnex\s+(?P<num>[IVXLCDM]+|\d+[a-z]?)\b.*?\bis\s+replaced\b",
    re.I | re.S,
)
# Increment 2 (real-bytes long-tail): the dominant real EU sanctions-amender shape
# is the INDIRECT annex amendment — *"Annex N to Regulation (EU) … is replaced by
# the list set out in the Annex to this Regulation"* and the multi-annex plural
# *"Annexes II and VI … are amended as set out in the Annex to this Regulation"*.
# The replacement payload is NOT a QUOT block in the instruction prose; it lives in
# the amending act's OWN ``<ANNEX>`` body (often a SEPARATE manifestation). The
# first named annex number is the structural target in the BASE coordinate system.
_RE_ANNEX_AS_SET_OUT = re.compile(
    r"\b(?:(?:the\s+)?Annex(?:es)?\s+(?P<num>[IVXLCDM]+|\d+[a-z]?)\b"
    r"|the\s+(?P<sole>Annex)\b)"
    r".*?\b(?:is|are)\s+(?:replaced|amended)\b"
    r".*?\bset\s+out\s+in\s+the\s+Annex\b",
    re.I | re.S,
)

# The instruments an amendment instruction can NAME as its target: "Regulation
# (EU) 2022/2309", "Council Regulation (EC) No 1210/2003", "Directive
# 2009/138/EC", "Decision (CFSP) 2022/2319" … Recognition is TWO-STEP (both
# classifiers FW-07 backtracking-bounded): find each instrument keyword, then
# scan a short bounded window after it for the ``A/B`` number pair — the
# decorations between them ((EU) / (EC) / (EU, Euratom) / (CFSP) / "No") vary
# freely and a one-shot pattern over them is exactly the nested-optional-
# quantifier shape the regex-safety lint forbids.
_RE_INSTRUMENT_KEYWORD = compile_classifier_regex(
    r"\b(?:Regulation|Directive|Decision)\b",
    re.I,
    classifier_id="eu_fmx4_instrument_keyword",
)
_RE_INSTRUMENT_NUMBER = compile_classifier_regex(
    r"(?<![\d/])(?P<num>\d{1,4}/\d{1,4})(?!\d)",
    classifier_id="eu_fmx4_instrument_number",
)
#: How far past the instrument keyword the number pair may sit ("Regulation
#: (EU, Euratom) No 1210/2003" needs ~26 chars of decoration).
_INSTRUMENT_NUMBER_WINDOW = 40


def _cited_instrument_numbers(instr: str) -> list[str]:
    """Every ``A/B`` instrument number cited in ``instr`` (document order)."""
    out: list[str] = []
    for kw in _RE_INSTRUMENT_KEYWORD.finditer(instr):
        window = instr[kw.end(): kw.end() + _INSTRUMENT_NUMBER_WINDOW]
        nm = _RE_INSTRUMENT_NUMBER.search(window)
        if nm:
            out.append(nm.group("num"))
    return out


def _base_number_forms(base_celex: str) -> frozenset[str]:
    """The ``A/B`` number forms under which ``base_celex`` is cited in prose.

    A sector-3 act CELEX ``3YYYY<L>NNNN`` is cited either ``No NNN/YYYY`` (the
    pre-2015 numbering, leading zeros dropped) or ``YYYY/NNN`` (the post-2015
    numbering). Both are returned so the foreign-target guard recognises the
    base under either convention. An unparseable ``base_celex`` yields the empty
    set (guard inactive — never a false skip on a malformed id).
    """
    m = re.match(r"^3(?P<year>\d{4})[A-Z](?P<num>\d+)$", base_celex)  # lawvm-regex: witness_only shape-parses a CELEX identifier (source-plane id census) into its citable number forms, not a post-parse semantic recognizer over statute text
    if not m:
        return frozenset()
    year = m.group("year")
    num = str(int(m.group("num")))  # drop leading zeros ("0692" → "692")
    return frozenset({f"{num}/{year}", f"{year}/{num}"})


def _foreign_target_instrument(instr: str, base_celex: str) -> str:
    """The instruction's named amended instrument iff it is NOT the base act.

    An omnibus amending act (e.g. the humanitarian-exemption regulation
    32023R0331) amends MANY regulations in one ENACTING.TERMS: each instruction
    article names ITS target ("In Council Regulation (EU) No 356/2010, Article 4
    is replaced …"). Lowering such an instruction against a DIFFERENT base and
    applying it there is cross-target misapplication — a corruption the
    EU consolidation oracle convicted on 32022R2309@20230216 (356/2010's
    Article 4 landed in 2309). Guard: if the instruction prose (QUOT payloads
    already excluded by ``_instruction_text``) names one or more instruments and
    NONE of them is the base act, the instruction belongs to another instrument
    — return the first foreign citation so the caller records a typed skip.
    Returns ``""`` (guard passes) when the base is named, when no instrument is
    named (single-target amenders elide the base after the opening clause), or
    when ``base_celex`` is absent/unparseable. A skip here is safe-by-default:
    the mistake it prevents (applying a foreign amendment) corrupts the body,
    while a false skip only surfaces as a typed, visible coverage residual.
    """
    base_forms = _base_number_forms(base_celex) if base_celex else frozenset()
    if not base_forms:
        return ""
    cited = _cited_instrument_numbers(instr)
    if not cited:
        return ""
    if any(c in base_forms for c in cited):
        return ""
    return cited[0]


def _local(tag: object) -> str:
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag
    return str(tag)


#: Quoted-block wrapper tags. An ``ARTICLE`` found INSIDE one of these is the
#: QUOTED REPLACEMENT BODY of an instruction, NOT a separate amendment
#: instruction — it must not be counted/iterated as its own instruction.
_QUOT_WRAPPER_TAGS = frozenset({"QUOT", "QUOT.S", "QUOT.START"})


def _top_level_amending_articles(enacting: ET.Element) -> list[ET.Element]:
    """Return the amendment-instruction ARTICLEs, EXCLUDING quoted replacement bodies.

    Increment 2 real-bytes fix: a whole-article REPLACE quotes the new article body
    as a nested ``<ARTICLE>`` inside a ``QUOT.S``/``QUOT.START`` wrapper (the real
    32017R0488 shape). ``enacting.iter("ARTICLE")`` walks that nested replacement
    ARTICLE as a SECOND, bogus instruction (double-count). We instead descend the
    ENACTING.TERMS tree but PRUNE any QUOT subtree, so only genuine amendment
    instructions (the amending act's own ARTICLEs, possibly nested in CHAPTER /
    DIVISION) are returned.
    """
    out: list[ET.Element] = []

    def _walk(node: ET.Element) -> None:
        for child in node:
            local = _local(child.tag).upper()
            if local in _QUOT_WRAPPER_TAGS:
                continue  # the quoted replacement body — not an instruction
            if local == "ARTICLE":
                out.append(child)
                # Do NOT recurse into an instruction ARTICLE: any ARTICLE nested
                # below it is a quoted body (already pruned) or sub-structure.
                continue
            _walk(child)

    _walk(enacting)
    return out


#: Elements whose text is the AMENDING act's own scaffolding (its own article
#: number / heading), NOT a target reference — excluded from instruction text so
#: e.g. ``<TI.ART>Article 1</TI.ART>`` (the amending act's Article 1) is not
#: mistaken for "Article 1" as a TARGET in the base act.
_INSTRUCTION_NOISE_TAGS = frozenset({"TI.ART", "STI.ART", "NO.ART", "NP"})


def _instruction_text(el: ET.Element) -> str:
    """Collect the instruction prose of an amending ARTICLE, EXCLUDING the
    quoted block AND the amending act's own ARTICLE heading.

    The quoted replacement payload lives inside ``QUOT.START``/``QUOT.END`` (or a
    ``QUOT`` wrapper); the amending act's own number lives in ``TI.ART``/``NO.ART``
    (the ``_INSTRUCTION_NOISE_TAGS``). Both are excluded so the classifier sees
    only the verb clause naming the TARGET in the base act.
    """
    parts: list[str] = []

    def _walk(node: ET.Element, inside_quote: bool) -> None:
        local = _local(node.tag).upper()
        if local in _INSTRUCTION_NOISE_TAGS:
            # Skip this subtree entirely, but keep its tail (the prose after the
            # heading element, which IS instruction text).
            return
        now_quote = inside_quote or local in ("QUOT.START", "QUOT", "QUOT.S")
        if not now_quote and node.text and node.text.strip():
            parts.append(node.text.strip())
        for child in node:
            _walk(child, now_quote)
            if not now_quote and child.tail and child.tail.strip():
                parts.append(child.tail.strip())

    _walk(el, inside_quote=False)
    return " ".join(parts)


def _quoted_block_text(el: ET.Element) -> Optional[str]:
    """Return the text of the FIRST quoted block in an amending ARTICLE, or None.

    Formex marks the inline new text with ``QUOT.START`` … ``QUOT.END`` siblings
    OR a ``QUOT`` wrapper element. We support both: a ``QUOT`` wrapper's inner
    text, else the text between a ``QUOT.START`` and the next ``QUOT.END`` among
    siblings.
    """
    # Wrapper form: <QUOT>…</QUOT> OR <QUOT.S>…</QUOT.S>. Increment 2 (real
    # bytes): the real whole-article replace (32017R0488) wraps the replacement
    # ARTICLE in a ``QUOT.S`` element whose inner ``QUOT.START``/``QUOT.END``
    # markers are NOT siblings (START sits in the nested ARTICLE's TI.ART, END
    # deep in the last PARAG), so the marker-pair logic below misses it. Treating
    # ``QUOT.S`` as a wrapper and taking its inner text captures the payload. The
    # quoted body's own leading "Article N" heading is returned here as-is; the
    # whole-article REPLACE/INSERT lowerers strip it via
    # ``_strip_quoted_article_heading`` (in the IR the heading is the node LABEL,
    # not text — see that helper).
    for node in el.iter():
        if _local(node.tag).upper() in ("QUOT", "QUOT.S"):
            txt = _wrapper_text_until_quot_end(node)
            if txt:
                return txt

    # Marker form: QUOT.START ... QUOT.END among a parent's children.
    for parent in el.iter():
        children = list(parent)
        start_idx = None
        for i, child in enumerate(children):
            lc = _local(child.tag).upper()
            if lc in ("QUOT.START", "QUOT.S"):
                start_idx = i
            elif lc in ("QUOT.END", "QUOT.E") and start_idx is not None:
                between: list[str] = []
                # tail of QUOT.START
                start_tail = children[start_idx].tail
                if start_tail and start_tail.strip():
                    between.append(start_tail.strip())
                for mid in children[start_idx + 1 : i]:
                    t = _all_text(mid)
                    if t:
                        between.append(t)
                    if mid.tail and mid.tail.strip():
                        between.append(mid.tail.strip())
                joined = " ".join(between).strip()
                if joined:
                    return joined
                start_idx = None
    return None


def _all_text(el: ET.Element) -> str:
    return " ".join(t.strip() for t in el.itertext() if t and t.strip())


def _wrapper_text_until_quot_end(wrapper: ET.Element) -> str:
    """Text of a QUOT/QUOT.S wrapper UP TO its ``QUOT.END`` marker.

    The real Formex shape closes the quoted body with an inline
    ``<QUOT.END/>`` whose TAIL is the surrounding INSTRUCTION's own closing
    punctuation (``…Committee.<QUOT.END/>.`` — the final ``.`` belongs to the
    amending sentence, not the payload). ``_all_text`` swallowed that tail into
    the payload, leaving a spurious trailing period on every replay-
    materialized article (convicted by the consolidation oracle at
    32022R2309@20230216 Art 5). Collect document-order text and STOP at the
    first ``QUOT.END``/``QUOT.E`` — its tail and everything after are the
    instruction's, not the quote's.
    """
    parts: list[str] = []
    stopped = False
    outer_start_id: str | None = None
    outer_end_id: str | None = None
    for node in wrapper.iter():
        if _local(node.tag).upper() == "QUOT.START":
            outer_start_id = node.attrib.get("ID") or None
            outer_end_id = node.attrib.get("REF.END") or None
            break

    def _is_outer_end(node: ET.Element) -> bool:
        if _local(node.tag).upper() not in ("QUOT.END", "QUOT.E"):
            return False
        if outer_end_id is None and outer_start_id is None:
            return True
        return (
            (outer_end_id is not None and node.attrib.get("ID") == outer_end_id)
            or (
                outer_start_id is not None
                and node.attrib.get("REF.START") == outer_start_id
            )
        )

    def _walk(node: ET.Element) -> None:
        nonlocal stopped
        if stopped:
            return
        if _is_outer_end(node):
            stopped = True
            return
        if node.text and node.text.strip():
            parts.append(node.text.strip())
        for child in node:
            _walk(child)
            if stopped:
                return
            if child.tail and child.tail.strip():
                parts.append(child.tail.strip())

    # The wrapper's own text, then children in document order (the wrapper's
    # tail is outside the quote by construction).
    if wrapper.text and wrapper.text.strip():
        parts.append(wrapper.text.strip())
    for child in wrapper:
        _walk(child)
        if stopped:
            break
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return " ".join(parts)


def _payload_node(kind: IRNodeKind, label: str, text: str) -> IRNode:
    """Build a replacement/insert payload IRNode from a captured quoted block."""
    return IRNode(kind=kind, label=label, text=text)


def _strip_quoted_article_heading(block: str, num: str) -> str:
    """Drop the quoted body's own leading ``Article <num>`` heading from a
    whole-article payload.

    The quoted replacement body of a whole-article REPLACE/INSERT opens with the
    article's OWN heading (``<TI.ART>Article 5</TI.ART>`` → the flattened block
    starts ``Article 5 …``). In the IR coordinate system that heading is the
    node's LABEL, not its text: the grafter parses both an enacted act and a
    consolidated FMX4 into articles whose text EXCLUDES the ``Article N`` line.
    Keeping it in the payload made every replay-materialized article carry a
    spurious ``Article N`` text prefix that no grafted rendering has (convicted
    by the consolidation oracle on 32022R2309@20230216 Art 5). Only the exact
    heading of THIS payload's own number is stripped — never any other leading
    text — so the transform is label/text normalization, not payload rewriting.
    """
    m = _RE_QUOTED_ARTICLE_HEADING.match(block.lstrip())
    if m and m.group("num").casefold() == num.casefold():
        rest = block.lstrip()[m.end():]
        # The heading's trailing punctuation/space is trimmed in CODE (a
        # variable-repeat regex tail is the lint-forbidden shape).
        return rest.lstrip(" \t\r\n\u00a0.:—–-")
    return block


#: A quoted whole-article body's own leading heading ("Article 5", "Article 5a").
#: Precompiled once (no per-op f-string regex, FW-08) via the FW-07
#: backtracking-bounded wrap; the caller verifies the captured number equals the
#: payload's target label before stripping, so only the payload's OWN heading is
#: ever removed.
_RE_QUOTED_ARTICLE_HEADING = compile_classifier_regex(
    r"Article\s+(?P<num>\d{1,4}[a-z]?)(?!\w)",
    re.I,
    classifier_id="eu_fmx4_quoted_article_heading",
)


# ---------------------------------------------------------------------------
# Increment 4 — OMNIBUS MULTI-POINT (NP) INSTRUCTION LOWERING (#221 backlog #1)
#
# Formex marks each numbered sub-instruction of an "… is amended as follows:"
# omnibus article as an <NP> (NO.P marker "(1)" + TXT verb clause + the QUOT
# payload). NPs NEST — "(2) Article 3 is amended as follows: (a) paragraph 1 is
# replaced …" — and the SAME NP element also carries ordinary (non-instruction)
# numbered prose lists inside substantive provisions AND inside quoted payload
# bodies, so three disciplines apply:
#
#   1. only NPs of an instruction ARTICLE whose opening prose matches
#      "is amended as follows" are iterated as sub-instructions;
#   2. NPs inside QUOT payloads are NEVER instructions (quote-pruned walk);
#   3. a nested "X is amended as follows:" NP contributes a CONTEXT path step
#      and recurses; its children are lowered against that context.
# ---------------------------------------------------------------------------

# The omnibus head verb clause routinely carries an intervening ADVERB — real
# CELLAR bytes say "is HEREBY amended as follows:" (32012R0630, 32011R0269,
# 32013R0049, 32011R1106) and "is FURTHER amended as follows:" — between the
# "is"/"are" copula and "amended". The pre-widening pattern required the two to
# be adjacent, so every adverb-carrying omnibus head FAILED to match: its NPs
# (already correctly discovered by ``_top_level_nps``, which recurses through the
# ALINEA/LIST/ITEM wrappers) were never iterated, the whole multi-point
# instruction lowered to ZERO ops, and the head fell through to
# ``eu_fmx4_grammar_uncovered_instruction`` — a FALSE lowering-gap masking real,
# fully-recognisable sub-instructions. Allowing an optional "hereby"/"further"
# adverb is strictly ADDITIVE: every head the narrow pattern
# matched still matches (the adverb group is optional), and the newly-matched
# heads route through the UNCHANGED ``_lower_np_instructions`` machinery (the
# foreign-target guard inside the omnibus branch still suppresses a head whose
# named instrument is not the base act). No new lowering logic is introduced.
# The adverb group is a single OPTIONAL non-nested clause with a LITERAL trailing
# space (not ``\s+``) — the regex-safety linter rejects a variable ``\s+`` inside
# an optional group adjacent to the following ``\s``/token (nested backtracking).
# The match runs on ``_instruction_text`` output, which is NBSP-normalised and
# single-space-joined (``" ".join(instr.split())``), so a literal space is the
# correct separator and cannot mis-fire on tab/NBSP runs. Real bytes never stack
# two of these adverbs, so a single optional adverb suffices.
_RE_AMENDED_AS_FOLLOWS = compile_classifier_regex(
    r"\b(?:is|are)\s+(?:hereby |further )?amended\s+as\s+follows\b",
    re.I,
    classifier_id="eu_fmx4_amended_as_follows",
)

#: Amendment-instruction verb census for the non-amending-provision classifier.
#: An instruction ARTICLE whose quote-free prose carries NONE of these (and no
#: for:/read: corrigendum formula) is a substantive/final provision, not an
#: amendment instruction.
_RE_AMEND_VERB = compile_classifier_regex(
    r"\b(?:replaced|inserted|added|deleted|repealed|amended|renumbered|"
    r"substituted)\b|\bshall\s+read\b",
    re.I,
    classifier_id="eu_fmx4_amendment_verb",
)

#: Grammar tokens for the NP leaf/context grammar (bounded, lint-safe shapes).
_ORD_TOKEN = (
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"\d{1,2}(?:st|nd|rd|th)"
)
_ART_NUM = r"\d{1,4}[a-z]{0,3}"
_PAR_NUM = r"\d{1,3}[a-z]{0,2}"
_PT_LAB = r"[a-z0-9]{1,4}"
_AX_NUM = r"[IVXLCDM]{1,7}[a-zA-Z]?|\d{1,3}[a-z]?"
#: A bare label list: "6", "6 and 7", "1, 2 and 3", "15 to 18", "5a".
_LAB_LIST = r"[0-9a-z]{1,6}(?:\s*(?:,|and|to)\s+[0-9a-z]{1,6}){0,12}"
#: A parenthesized label list: "(a)", "(a), (b) and (c)", "(23)".
_PLAB_LIST = r"\([0-9a-z]{1,4}\)(?:\s*(?:,|and|to)\s*\([0-9a-z]{1,4}\)){0,12}"

# CONTEXT clauses — leading "in …," scopes an NP instruction names before its
# verb clause. Most specific first ("in point (2) of Article 4," before the
# bare point clause). The bare paragraph/point clauses REQUIRE the trailing
# comma so "point (x) is replaced …" is never consumed as context.
_RE_CTX_SUBPARA_OF_ART = re.compile(
    r"^in\s+the\s+(?P<ord>" + _ORD_TOKEN + r")\s+subparagraph\s+of\s+Article\s+"
    r"(?P<art>" + _ART_NUM + r")\s*(?:\((?P<par>" + _PAR_NUM + r")\))?"
    r"(?:\s+of\s+[^,:;]{0,90}?)?\s*,\s*",
    re.I,
)
_RE_CTX_POINT_OF_ART = re.compile(
    r"^in\s+point\s+\((?P<pt>" + _PT_LAB + r")\)\s+of\s+(?:Article\s+"
    r"(?P<art>" + _ART_NUM + r")\s*(?:\((?P<par>" + _PAR_NUM + r")\))?"
    r"(?:\s+of\s+[^,:;]{0,90}?)?|Annex\s+(?P<ax>" + _AX_NUM + r"))"
    r"\s*,?\s*",
    re.I,
)
_RE_CTX_ART = re.compile(
    r"^in\s+Article\s+(?P<art>" + _ART_NUM + r")\s*(?:\((?P<par>" + _PAR_NUM + r")\))?"
    r"(?:\s+of\s+[^,:;]{0,90}?)?\s*,\s*",
    re.I,
)
_RE_CTX_PAR = re.compile(
    r"^in\s+paragraph\s+(?P<par>" + _PAR_NUM + r")\s*,?\s+",
    re.I,
)
_RE_CTX_PT = re.compile(
    r"^in\s+point\s+\((?P<pt>" + _PT_LAB + r")\)\s*,?\s+",
    re.I,
)
_RE_CTX_ANNEX = re.compile(
    r"^in\s+Annex\s+(?P<ax>" + _AX_NUM + r")\s*,\s*",
    re.I,
)
_RE_CTX_BARE_PAR = re.compile(
    r"^paragraph(?!s)\s+(?P<par>" + _PAR_NUM + r")\s*,\s*(?=the\s|shall\s|point\s)",
    re.I,
)
_RE_CTX_BARE_PT = re.compile(
    r"^point(?!s)\s+\((?P<pt>" + _PT_LAB + r")\)\s*,\s*(?=the\s|shall\s)",
    re.I,
)

# NEST targets — "X is amended as follows:" contributes a context step and the
# nested NPs are lowered against it.
_RE_NEST_ART = re.compile(
    r"^Article\s+(?P<art>" + _ART_NUM + r")\s*(?:\((?P<par>" + _PAR_NUM + r")\))?"
    r"(?:\s+of\s+[^,:;]{0,90}?)?\s+is\s+amended\s+as\s+follows",
    re.I,
)
_RE_NEST_PAR = re.compile(
    r"^paragraph\s+(?P<par>" + _PAR_NUM + r")\s+is\s+amended\s+as\s+follows", re.I
)
_RE_NEST_PT = re.compile(
    r"^point\s+\((?P<pt>" + _PT_LAB + r")\)\s+is\s+amended\s+as\s+follows", re.I
)
_RE_NEST_ANNEX = re.compile(
    r"^Annex\s+(?P<ax>" + _AX_NUM + r")\s+is\s+amended\s+as\s+follows", re.I
)
_RE_NEST_SUBPARA = re.compile(
    r"^the\s+(?P<ord>" + _ORD_TOKEN + r")\s+subparagraph\s+is\s+amended\s+as\s+follows",
    re.I,
)

# LEAF verb clauses (anchored at the remainder start, after context stripping).
_RE_NPL_INSERT = re.compile(
    r"^(?:the\s+following|new)\s+"
    r"(?:(?:" + _ORD_TOKEN + r")\s+(?:to\s+(?:" + _ORD_TOKEN + r")\s+)?)?"
    r"(?P<kind>articles?|paragraphs?|points?|subparagraphs?|indents?|sub-points?)"
    r"\s*(?P<list>" + _PLAB_LIST + r"|" + _LAB_LIST + r")?\s*"
    r"(?:is|are)\s+(?:inserted|added)\b",
    re.I,
)
_RE_NPL_KIND_REPLACE = re.compile(
    r"^(?P<kind>articles?|paragraphs?|points?)\s+"
    r"(?P<list>" + _PLAB_LIST + r"|" + _LAB_LIST + r")"
    r"(?:\s+of\s+[^,;:]{0,90}?)?\s*,?\s+"
    r"(?:is|are|shall\s+be)\s+replaced\s+by\s+the\s+following(?:\s+text)?",
    re.I,
)
_RE_NPL_KIND_REPEAL = re.compile(
    r"^(?P<kind>articles?|paragraphs?|points?)\s+"
    r"(?P<list>" + _PLAB_LIST + r"|" + _LAB_LIST + r")"
    r"(?:\s+of\s+[^,;:]{0,90}?)?\s*,?\s+"
    r"(?:is|are|shall\s+be)\s+(?:deleted|repealed)\b",
    re.I,
)
_RE_NPL_SUBPARA_REPLACE = re.compile(
    r"^the\s+(?P<ord>" + _ORD_TOKEN + r")\s+subparagraph\s+"
    r"(?:is|are|shall\s+be)\s+replaced\s+by\s+the\s+following",
    re.I,
)
_RE_NPL_SUBPARA_REPEAL = re.compile(
    r"^the\s+(?P<ord>" + _ORD_TOKEN + r")\s+subparagraph\s+is\s+(?:deleted|repealed)\b",
    re.I,
)
_RE_NPL_BARE_REPLACE = re.compile(
    r"^(?:is|are|shall\s+be)\s+replaced\s+by\s+the\s+following(?:\s+text)?", re.I
)
_RE_NPL_BARE_REPEAL = re.compile(
    r"^(?:is|are|shall\s+be)\s+(?:deleted|repealed)\b", re.I
)
#: Recognized-but-unaddressable targets (title/heading/introductory wording have
#: no coordinate in the IR system) — typed, never silently dropped.
_RE_NPL_UNADDRESSABLE = re.compile(
    r"^the\s+(?:title|heading|introductory\s+(?:wording|part|phrase|sentence))\s+"
    r"is\s+replaced\b",
    re.I,
)
# Annex leaf forms. "replaced by the text (set out) in Annex Y to this
# Regulation" is a true whole-annex REPLACE with the amender's OWN annex as
# payload; "amended in accordance with / as set out in Annex Y" means annex Y
# carries EMBEDDED amendment instructions (an indirection the grammar does not
# execute — typed annex-lane gap); "inserted as Annex N" / "added as laid down
# in Annex Y" are whole-annex INSERTs.
_RE_NPL_ANNEX_REPLACED_BY_TEXT = re.compile(
    r"^Annex\s+(?P<num>" + _AX_NUM + r")(?:\s*\([^)]{0,40}\))?"
    r"(?:\s+to\s+[^,;:]{0,90}?)?\s+is\s+replaced\s+by\s+the\s+text\s+"
    r"(?:set\s+out\s+)?in\s+(?:the\s+Annex|Annex\s+(?P<own>" + _AX_NUM + r"))\s+"
    r"to\s+this\s+Regulation",
    re.I,
)
_RE_NPL_ANNEX_TEXT_INSERTED_AS = re.compile(
    r"^the\s+text\s+set\s+out\s+in\s+the\s+Annex\s+to\s+this\s+Regulation\s+is\s+"
    r"inserted\s+as\s+Annex\s+(?P<num>" + _AX_NUM + r")",
    re.I,
)
_RE_NPL_ANNEX_ADDED_LAID_DOWN = re.compile(
    r"^Annex\s+(?P<num>" + _AX_NUM + r")(?:\s+to\s+[^,;:]{0,90}?)?\s+is\s+added\s+as\s+"
    r"laid\s+down\s+in\s+Annex\s+(?P<own>" + _AX_NUM + r")\s+to\s+this\s+Regulation",
    re.I,
)
_RE_NPL_ANNEX_ADDED_AS_SET_OUT = re.compile(
    r"^Annex\s+(?P<num>" + _AX_NUM + r")\s*,\s*as\s+set\s+out\s+in\s+Annex\s+"
    r"(?P<own>" + _AX_NUM + r")\s+to\s+this\s+Regulation\s*,\s*is\s+added",
    re.I,
)
_RE_NPL_ANNEX_INDIRECT = re.compile(
    r"^(?:the\s+Annex|Annex(?:es)?\s+[IVXLCDM0-9][^,;:]{0,60}?)\s+"
    r"(?:is|are)\s+(?:amended|replaced)\s+"
    r"(?:in\s+accordance\s+with|as\s+set\s+out\s+in)\b",
    re.I,
)
#: ARTICLE-level variant of the annex indirection (search, not anchored): the
#: real 32022R2309 sanctions-amender shape "Annex I to Regulation (EU)
#: 2022/2309 is amended in accordance with the Annex to this Regulation."
_RE_ANNEX_IN_ACCORDANCE = re.compile(
    r"\bAnnex(?:es)?\b[^;:]{0,90}?\b(?:is|are)\s+amended\s+in\s+accordance\s+"
    r"with\b[^;:]{0,60}?\bAnnex\b",
    re.I | re.S,
)
#: Act-TITLE replace ("The title of Regulation (EU) No 1284/2009 is replaced by
#: the following:") — the act title has no unit coordinate on the article-only
#: compare surface; typed, never silently dropped.
_RE_ACT_TITLE_REPLACE = re.compile(
    r"\bthe\s+title\s+of\s+[^;:]{0,90}?\bis\s+replaced\s+by\s+the\s+following\b",
    re.I | re.S,
)
#: Numberless whole-article INSERT ("The following article is inserted (in
#: Regulation (EU) No 1284/2009):") — the new article's number lives on the
#: quoted body's own TI.ART heading, not in the instruction prose.
_RE_ARTICLE_INSERT_NUMBERLESS = re.compile(
    r"\bthe\s+following\s+articles?\s+(?:is|are)\s+inserted\b",
    re.I | re.S,
)

#: Payload marker tags excluded from GRAFTER-COMMENSURABLE structural payload
#: text: in the IR coordinate system the article heading (TI.ART) and the
#: paragraph number marker (NO.PARAG) are the node's LABEL, not text — the EU
#: grafter renders neither into a consolidated article's text, so neither may a
#: replay-materialized payload. Point markers (NO.P) STAY: the grafter renders
#: them inline (ALINEA itertext), so the payload must too.
_PAYLOAD_MARKER_TAGS = frozenset({"TI.ART", "NO.PARAG"})


def _top_level_nps(el: ET.Element) -> list[ET.Element]:
    """The instruction NPs of ``el``, EXCLUDING quoted payloads and nested NPs.

    Prunes QUOT wrapper subtrees AND tracks the sibling ``QUOT.START`` /
    ``QUOT.END`` marker form (content between the markers is quoted payload,
    not instruction structure). Does not recurse INTO an NP — a nested NP is
    the recursion payload of :func:`_lower_np_instructions`, not a sibling.
    """
    out: list[ET.Element] = []

    def _walk(node: ET.Element) -> None:
        depth = 0
        for child in node:
            local = _local(child.tag).upper()
            if local == "QUOT.START":
                depth += 1
                continue
            if local in ("QUOT.END", "QUOT.E"):
                depth = max(0, depth - 1)
                continue
            if depth > 0:
                continue
            if local in ("QUOT", "QUOT.S"):
                continue
            if local == "NP":
                out.append(child)
                continue
            _walk(child)

    _walk(el)
    return out


def _np_prose(el: ET.Element, *, keep_child_nps: bool) -> str:
    """Instruction prose of ``el``: quote-free, marker-free, NBSP-normalized.

    ``keep_child_nps=False`` prunes NP subtrees below ``el`` (they are their
    own sub-instructions); ``True`` keeps them (the whole-article amendment-verb
    census needs the NP clauses). The element's own NO.P marker and the
    amending act's heading tags are always excluded; quoted payloads (wrapper
    AND sibling-marker form) are always excluded.
    """
    noise = frozenset({"NO.P", "TI.ART", "STI.ART", "NO.ART"})
    parts: list[str] = []

    def _walk(node: ET.Element, inside_quote: bool) -> None:
        local = _local(node.tag).upper()
        if local in noise:
            return
        if not keep_child_nps and local == "NP" and node is not el:
            return
        now_quote = inside_quote or local in ("QUOT", "QUOT.S")
        if not now_quote and node.text and node.text.strip():
            parts.append(node.text.strip())
        marker_quote = False
        for child in node:
            cl = _local(child.tag).upper()
            if cl == "QUOT.START":
                marker_quote = True
                continue
            if cl in ("QUOT.END", "QUOT.E"):
                marker_quote = False
                if not now_quote and child.tail and child.tail.strip():
                    parts.append(child.tail.strip())
                continue
            if marker_quote:
                continue
            _walk(child, now_quote)
            if not now_quote and child.tail and child.tail.strip():
                parts.append(child.tail.strip())

    _walk(el, inside_quote=False)
    return " ".join(parts).replace(" ", " ")


def _quoted_struct_payload_text(el: ET.Element, *, drop_own_no_p: bool = False) -> str:
    """GRAFTER-COMMENSURABLE text of a quoted structural payload element.

    Mirrors how the EU grafter renders a consolidated unit: TI.ART headings and
    NO.PARAG markers are labels (excluded); point markers and body text are
    kept; collection STOPS at the first ``QUOT.END`` (its tail belongs to the
    instruction, not the payload — the 32022R2309@20230216 boundary conviction).
    ``drop_own_no_p`` additionally excludes the element's own DIRECT ``NO.P``
    marker — the case of a numbered paragraph quoted in NP form, whose marker
    is the node LABEL (nested point markers stay, as the grafter keeps them).
    """
    parts: list[str] = []
    stopped = False

    def _walk(node: ET.Element) -> None:
        nonlocal stopped
        if stopped:
            return
        local = _local(node.tag).upper()
        if local in ("QUOT.END", "QUOT.E"):
            stopped = True
            return
        if local in _PAYLOAD_MARKER_TAGS:
            return
        if node.text and node.text.strip():
            parts.append(node.text.strip())
        for child in node:
            _walk(child)
            if stopped:
                return
            if child.tail and child.tail.strip():
                parts.append(child.tail.strip())

    if el.text and el.text.strip():
        parts.append(el.text.strip())
    for child in el:
        if drop_own_no_p and _local(child.tag).upper() == "NO.P":
            if child.tail and child.tail.strip():
                parts.append(child.tail.strip())
            continue
        _walk(child)
        if stopped:
            break
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return " ".join(parts)


def _contains_quot_start(el: ET.Element) -> bool:
    return any(_local(n.tag).upper() == "QUOT.START" for n in el.iter())


def _quoted_struct_elements(np: ET.Element, tag: str) -> list[ET.Element]:
    """TOPMOST elements with local ``tag`` inside the NP's QUOT payload(s).

    Wrapper form first (QUOT / QUOT.S subtree); marker-form fallback: a payload
    element whose subtree CONTAINS the inline ``QUOT.START`` marker (the real
    32021R1096 shape — the marker opens inside the quoted unit's own number
    marker, so no wrapper subtree exists).
    """
    out: list[ET.Element] = []

    def _collect(node: ET.Element, inside_quote: bool) -> None:
        local = _local(node.tag).upper()
        now_quote = inside_quote or local in ("QUOT", "QUOT.S")
        if now_quote and local == tag:
            out.append(node)
            return  # topmost only — a nested same-tag is its sub-structure
        for child in node:
            _collect(child, now_quote)

    for child in np:
        _collect(child, False)
    if out:
        return out

    def _collect_marker(node: ET.Element) -> None:
        local = _local(node.tag).upper()
        if local == tag and _contains_quot_start(node):
            out.append(node)
            return
        for child in node:
            _collect_marker(child)

    for child in np:
        _collect_marker(child)
    return out


def _quoted_article_label(art_el: ET.Element) -> str:
    ti = art_el.find("TI.ART")
    if ti is None:
        return ""
    txt = " ".join(t.strip() for t in ti.itertext() if t and t.strip())
    m = re.search(r"Article\s+(\d{1,4}\w{0,3})", txt.replace(" ", " "), re.I)  # lawvm-regex: witness_only reads the quoted payload's own heading label for zip-matching, not a semantic recognizer
    return m.group(1) if m else ""


def _quoted_parag_label(parag_el: ET.Element) -> str:
    no = parag_el.find("NO.PARAG")
    if no is None:
        return ""
    txt = "".join(no.itertext()).strip().replace(" ", " ")
    m = re.match(r"\(?(\d{1,3}[a-z]{0,2})[).]?", txt)  # lawvm-regex: witness_only reads the quoted payload's own paragraph marker for zip-matching, not a semantic recognizer
    return m.group(1) if m else ""


def _quoted_point_label(np_el: ET.Element) -> str:
    no = np_el.find("NO.P")
    if no is None:
        return ""
    txt = "".join(no.itertext()).strip().replace(" ", " ")
    m = re.match(r"\(?([0-9a-z]{1,4})\)?", txt, re.I)  # lawvm-regex: witness_only reads the quoted payload's own point marker for zip-matching, not a semantic recognizer
    return m.group(1) if m else ""


def _parse_label_list(raw: str) -> Optional[list[str]]:
    """Parse '6 and 7' / '1, 2 and 3' / '15 to 18' / '(a), (b)' into labels."""
    s = raw.replace("(", " ").replace(")", " ").replace(" ", " ").strip()
    if re.search(r"\bto\b", s):  # lawvm-regex: witness_only detects the range form inside an already-matched label-list capture, not a semantic recognizer over statute text
        m = re.match(r"^(\d{1,4})\s+to\s+(\d{1,4})$", s)  # lawvm-regex: witness_only expands a numeric label range token already isolated by the leaf grammar
        if not m:
            return None  # non-numeric range ("(a) to (c)") — typed residual
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi < lo or hi - lo > 50:
            return None
        return [str(i) for i in range(lo, hi + 1)]
    toks = [t.strip() for t in re.split(r",|\band\b", s) if t.strip()]
    if not toks or any(not re.match(r"^[0-9a-z]{1,6}$", t, re.I) for t in toks):  # lawvm-regex: witness_only validates label-token shape inside an already-matched list capture
        return None
    return toks


def _parse_points_of_point_replace(rem: str) -> Optional[tuple[list[str], str]]:
    """Parse "points (a), (b) and (c) of point 90 are replaced ..."."""
    text = rem.strip()
    folded = text.casefold()
    if folded.startswith("points "):
        label_start = len("points ")
    elif folded.startswith("point "):
        label_start = len("point ")
    else:
        return None
    marker = " of point "
    marker_at = folded.find(marker, label_start)
    if marker_at < 0:
        return None
    labels = _parse_label_list(text[label_start:marker_at])
    if labels is None:
        return None
    after_marker = text[marker_at + len(marker) :].lstrip()
    parent_chars: list[str] = []
    for ch in after_marker:
        if ch.isalnum():
            parent_chars.append(ch)
            continue
        break
    parent = "".join(parent_chars)
    if not parent or len(parent) > 6:
        return None
    tail = after_marker[len(parent) :].lstrip().casefold()
    if not (
        tail.startswith("is replaced by the following")
        or tail.startswith("are replaced by the following")
        or tail.startswith("shall be replaced by the following")
    ):
        return None
    return labels, parent


def _parse_point_of_article_repeal(instr: str) -> Optional[tuple[str, str]]:
    """Parse "Point (h) of Article 1 ... is deleted" as a point repeal."""
    text = instr.strip()
    folded = text.casefold()
    if not folded.startswith("point "):
        return None
    marker = " of article "
    marker_at = folded.find(marker)
    if marker_at < 0:
        return None
    point_raw = text[len("point ") : marker_at].strip()
    labels = _parse_label_list(point_raw)
    if labels is None or len(labels) != 1:
        return None
    after_marker = text[marker_at + len(marker) :].lstrip()
    article_chars: list[str] = []
    for ch in after_marker:
        if ch.isalnum():
            article_chars.append(ch)
            continue
        break
    article = "".join(article_chars)
    if not article or len(article) > 6:
        return None
    tail = after_marker[len(article) :].casefold()
    if not any(
        phrase in tail
        for phrase in (" is deleted", " is repealed", " shall be deleted", " shall be repealed")
    ):
        return None
    return article, labels[0]


def _parse_paragraph_of_article_replace(instr: str) -> Optional[tuple[str, str]]:
    """Parse "Paragraph (1) of Article 4 ... is replaced" as paragraph replace."""
    text = instr.strip()
    folded = text.casefold()
    if not folded.startswith("paragraph "):
        return None
    marker = " of article "
    marker_at = folded.find(marker)
    if marker_at < 0:
        return None
    paragraph_raw = text[len("paragraph ") : marker_at].strip()
    labels = _parse_label_list(paragraph_raw)
    if labels is None or len(labels) != 1:
        return None
    after_marker = text[marker_at + len(marker) :].lstrip()
    article_chars: list[str] = []
    for ch in after_marker:
        if ch.isalnum():
            article_chars.append(ch)
            continue
        break
    article = "".join(article_chars)
    if not article or len(article) > 6:
        return None
    tail = after_marker[len(article) :].casefold()
    if " replaced by the following" not in tail and " replaced by:" not in tail:
        return None
    return article, labels[0]


def _extract_np_context(
    text: str, context: tuple[tuple[str, str], ...]
) -> tuple[tuple[tuple[str, str], ...], str]:
    """Strip leading context clauses ('in Article 5(2),' …) off an NP clause.

    Returns the extended context path steps plus the remaining verb clause.
    """
    rem = text.lstrip()
    steps = list(context)
    while True:
        m = _RE_CTX_SUBPARA_OF_ART.match(rem)
        if m:
            steps.append(("article", m.group("art")))
            if m.group("par"):
                steps.append(("paragraph", m.group("par")))
            steps.append(("subparagraph", _ordinal_to_index(m.group("ord"))))
            rem = rem[m.end():]
            continue
        m = _RE_CTX_POINT_OF_ART.match(rem)
        if m:
            if m.group("ax"):
                steps.append(("annex", m.group("ax").upper()))
            else:
                steps.append(("article", m.group("art")))
                if m.group("par"):
                    steps.append(("paragraph", m.group("par")))
            steps.append(("point", m.group("pt")))
            rem = rem[m.end():]
            continue
        m = _RE_CTX_ART.match(rem)
        if m:
            steps.append(("article", m.group("art")))
            if m.group("par"):
                steps.append(("paragraph", m.group("par")))
            rem = rem[m.end():]
            continue
        m = _RE_CTX_PAR.match(rem) or _RE_CTX_BARE_PAR.match(rem)
        if m:
            steps.append(("paragraph", m.group("par")))
            rem = rem[m.end():]
            continue
        m = _RE_CTX_PT.match(rem) or _RE_CTX_BARE_PT.match(rem)
        if m:
            steps.append(("point", m.group("pt")))
            rem = rem[m.end():]
            continue
        m = _RE_CTX_ANNEX.match(rem)
        if m:
            steps.append(("annex", m.group("ax").upper()))
            rem = rem[m.end():]
            continue
        break
    return tuple(steps), rem


def _parse_nest_target(
    rem: str, context: tuple[tuple[str, str], ...]
) -> Optional[tuple[tuple[str, str], ...]]:
    """The extended context of a NEST NP ('X is amended as follows:'), or None."""
    m = _RE_NEST_ART.match(rem)
    if m:
        steps = context + (("article", m.group("art")),)
        if m.group("par"):
            steps += (("paragraph", m.group("par")),)
        return steps
    m = _RE_NEST_PAR.match(rem)
    if m:
        return context + (("paragraph", m.group("par")),)
    m = _RE_NEST_PT.match(rem)
    if m:
        return context + (("point", m.group("pt")),)
    m = _RE_NEST_ANNEX.match(rem)
    if m:
        return context + (("annex", m.group("ax").upper()),)
    m = _RE_NEST_SUBPARA.match(rem)
    if m:
        return context + (("subparagraph", _ordinal_to_index(m.group("ord"))),)
    return None


def _ctx_is_annex_lane(context: tuple[tuple[str, str], ...]) -> bool:
    return bool(context) and context[0][0] == "annex"


#: Instruction kind token → (path kind, payload IRNodeKind, witness stem).
_NP_KIND_TABLE: dict[str, tuple[str, IRNodeKind, str]] = {
    "article": ("article", IRNodeKind.SECTION, "WHOLE_ARTICLE"),
    "paragraph": ("paragraph", IRNodeKind.PARAGRAPH, "SUBART_PARAGRAPH"),
    "point": ("point", IRNodeKind.ITEM, "SUBART_POINT"),
    "subparagraph": ("subparagraph", IRNodeKind.SUBPARAGRAPH, "SUBART_SUBPARAGRAPH"),
    "indent": ("item", IRNodeKind.ITEM, "INDENT"),
    "sub-point": ("point", IRNodeKind.ITEM, "SUBART_POINT"),
}

#: Quoted payload structural tag per instruction kind (for per-label zip).
_NP_PAYLOAD_TAG = {"article": "ARTICLE", "paragraph": "PARAG", "point": "NP"}
_NP_PAYLOAD_LABEL = {
    "article": _quoted_article_label,
    "paragraph": _quoted_parag_label,
    "point": _quoted_point_label,
}


def _np_payloads_for_labels(
    np: ET.Element, kind: str, labels: list[str]
) -> Optional[list[tuple[str, str]]]:
    """Match target ``labels`` to quoted payload bodies → [(label, text)].

    Per-label structural zip first (the payload's own heading/marker labels are
    matched case-insensitively to the instruction's target labels); positional
    zip when counts agree but the payload exposes no labels; single-target flat
    fallback via :func:`_quoted_block_text`. ``None`` = no payload resolvable
    (the caller records the typed missing-payload diagnostic).
    """
    tag = _NP_PAYLOAD_TAG.get(kind)
    elems = _quoted_struct_elements(np, tag) if tag else []
    label_fn = _NP_PAYLOAD_LABEL.get(kind, _quoted_point_label)
    drop_own_no_p = False
    if not elems and kind == "paragraph":
        # A numbered paragraph quoted in NP form (marker inside its own NO.P —
        # the real 32021R1096 shape). The NO.P carries the paragraph number
        # (the node label), so it is dropped from the payload text.
        elems = _quoted_struct_elements(np, "NP")
        label_fn = _quoted_point_label
        drop_own_no_p = True

    def _text(e: ET.Element) -> str:
        return _quoted_struct_payload_text(e, drop_own_no_p=drop_own_no_p)

    if elems:
        by_label = {
            label_fn(e).casefold(): e for e in elems if label_fn(e)
        }
        if labels and all(l.casefold() in by_label for l in labels):
            return [(l, _text(by_label[l.casefold()])) for l in labels]
        if labels and len(labels) == len(elems):
            return [(l, _text(e)) for l, e in zip(labels, elems, strict=True)]
        if not labels:
            # Labels come FROM the payload ("the following Article is inserted:").
            derived = [(label_fn(e), _text(e)) for e in elems]
            if all(lbl for lbl, _ in derived):
                return derived
        if len(labels) == 1 and len(elems) == 1:
            return [(labels[0], _text(elems[0]))]
    block = _quoted_block_text(np)
    if block is None:
        mi = _RE_INLINE_QUOTED.search(_np_prose(np, keep_child_nps=True))
        block = mi.group("inline").strip() if mi else None
    if block is None:
        return None
    if len(labels) <= 1:
        label = labels[0] if labels else ""
        return [(label, block)]
    return None  # plural target with an unsplittable payload — typed residual


def _own_annex_payload(
    own_annexes: Optional[list[ET.Element]], label: str
) -> str:
    """The amender's OWN annex body named ``label`` ('' = the sole annex)."""
    annexes = list(own_annexes or ())
    if label:
        for a in annexes:
            if (_annex_number_from_title(a) or "").casefold() == label.casefold():
                return _quoted_block_text(a) or _all_text(a)
    if len(annexes) == 1:
        return _quoted_block_text(annexes[0]) or _all_text(annexes[0])
    return ""


def _lower_np_leaf(
    rem: str,
    ctx: tuple[tuple[str, str], ...],
    np: ET.Element,
    raw: str,
    *,
    op_ids: Callable[[], tuple[str, int]],
    src: OperationSource,
    diagnostics: list[AmendmentGrammarDiagnostic],
    own_annexes: Optional[list[ET.Element]],
) -> Optional[list[LegalOperation]]:
    """Lower ONE leaf NP verb clause against its context path.

    Returns the lowered op list ([] = handled, a typed diagnostic already
    recorded), or ``None`` = the clause matched no leaf rule (the caller
    records the uncovered-instruction diagnostic).
    """

    def _mk(
        action: StructuralAction,
        path: tuple[tuple[str, str], ...],
        payload: Optional[IRNode],
        witness: str,
        apply_class: str,
        *,
        root: Optional[str] = None,
        extra_tags: tuple[str, ...] = (),
    ) -> LegalOperation:
        op_id, sequence = op_ids()
        target_root = root
        if target_root is None and path and path[0][0] == "annex":
            target_root = "supplements"
        return LegalOperation(
            op_id=op_id,
            sequence=sequence,
            action=action,
            target=LegalAddress(path=path, root=target_root),
            payload=payload,
            source=src,
            witness_rule_id=witness,
            provenance_tags=(f"ir_apply_class={apply_class}",) + extra_tags,
        )

    def _missing_payload(kind: str) -> list[LegalOperation]:
        diagnostics.append(
            AmendmentGrammarDiagnostic(
                rule_id="eu_fmx4_grammar_np_payload_unresolved",
                reason=(
                    f"NP {kind} instruction had no resolvable quoted payload "
                    "(no QUOT block / per-label structural zip failed)"
                ),
                source_excerpt=raw,
                family=(
                    "annex_extraction_gap"
                    if _ctx_is_annex_lane(ctx)
                    else "extraction_gap"
                ),
            )
        )
        return []

    _STEM = {
        "article": "WHOLE_ARTICLE",
        "paragraph": "SUBART_PARAGRAPH",
        "point": "SUBART_POINT",
        "subparagraph": "SUBART_SUBPARAGRAPH",
    }
    _NODE_KIND = {
        "article": IRNodeKind.SECTION,
        "paragraph": IRNodeKind.PARAGRAPH,
        "point": IRNodeKind.ITEM,
        "subparagraph": IRNodeKind.SUBPARAGRAPH,
    }

    def _apply_class(path_kind: str, verb: str) -> str:
        if path_kind == "article":
            return f"whole_section_{verb}"
        if path_kind == "point" and verb in ("replace", "repeal"):
            return f"point_{verb}"
        return {
            "replace": "subsection_replace",
            "repeal": "subsection_repeal",
            "insert": "subsection_insert",
        }[verb]

    # ---- INSERT family ("the following … is inserted/added") ---------------
    m = _RE_NPL_INSERT.match(rem)
    if m:
        kind_tok = m.group("kind").lower()
        kind = "sub-point" if kind_tok.startswith("sub-point") else kind_tok.rstrip("s")
        path_kind, node_kind, stem = _NP_KIND_TABLE[kind]
        labels = _parse_label_list(m.group("list") or "") or []
        if kind in ("subparagraph", "indent", "sub-point"):
            # Unlabeled positional units: ONE op carrying the whole block (the
            # article-text compare surface is insensitive to their split).
            block = _quoted_block_text(np)
            if block is None:
                return _missing_payload(kind)
            if not ctx:
                return None  # dangling positional insert without any scope
            return [
                _mk(
                    StructuralAction.INSERT,
                    ctx + ((path_kind, ""),),
                    _payload_node(node_kind, "", block),
                    f"EU_FMX4.{stem}_INSERT",
                    "subsection_insert",
                )
            ]
        pairs = _np_payloads_for_labels(np, kind, labels)
        if pairs is None:
            return _missing_payload(kind)
        if kind == "article":
            ops: list[LegalOperation] = []
            for label, text in pairs:
                if not label:
                    return _missing_payload(kind)
                ops.append(
                    _mk(
                        StructuralAction.INSERT,
                        (("article", label),),
                        _payload_node(
                            node_kind,
                            label,
                            _strip_quoted_article_heading(text, label),
                        ),
                        "EU_FMX4.WHOLE_ARTICLE_INSERT",
                        "whole_section_insert",
                    )
                )
            return ops
        if not ctx:
            return None  # dangling paragraph/point insert without any scope
        return [
            _mk(
                StructuralAction.INSERT,
                ctx + ((path_kind, label),),
                _payload_node(node_kind, label, text),
                f"EU_FMX4.{stem}_INSERT",
                "subsection_insert",
            )
            for label, text in pairs
        ]

    # ---- REPLACE family -----------------------------------------------------
    points_of_point = _parse_points_of_point_replace(rem)
    if points_of_point is not None:
        if not ctx:
            return None
        labels, parent = points_of_point
        pairs = _np_payloads_for_labels(np, "point", labels)
        if pairs is None:
            return _missing_payload("point")
        return [
            _mk(
                StructuralAction.REPLACE,
                ctx + (("point", parent), ("point", label)),
                _payload_node(IRNodeKind.ITEM, label, text),
                "EU_FMX4.SUBART_POINT_REPLACE",
                "point_replace",
            )
            for label, text in pairs
        ]

    m = _RE_NPL_KIND_REPLACE.match(rem)
    if m:
        kind = m.group("kind").lower().rstrip("s")
        path_kind, node_kind, stem = _NP_KIND_TABLE[kind]
        labels = _parse_label_list(m.group("list"))
        if labels is None:
            return None
        if kind != "article" and not ctx:
            return None  # dangling sub-article replace without scope
        pairs = _np_payloads_for_labels(np, kind, labels)
        if pairs is None:
            return _missing_payload(kind)
        ops = []
        for label, text in pairs:
            if kind == "article":
                payload = _payload_node(
                    node_kind, label, _strip_quoted_article_heading(text, label)
                )
                path: tuple[tuple[str, str], ...] = (("article", label),)
            else:
                payload = _payload_node(node_kind, label, text)
                path = ctx + ((path_kind, label),)
            ops.append(
                _mk(
                    StructuralAction.REPLACE,
                    path,
                    payload,
                    f"EU_FMX4.{stem}_REPLACE",
                    _apply_class(path_kind, "replace"),
                )
            )
        return ops

    m = _RE_NPL_SUBPARA_REPLACE.match(rem)
    if m:
        if not ctx:
            return None
        block = _quoted_block_text(np)
        if block is None:
            return _missing_payload("subparagraph")
        idx = _ordinal_to_index(m.group("ord"))
        return [
            _mk(
                StructuralAction.REPLACE,
                ctx + (("subparagraph", idx),),
                _payload_node(IRNodeKind.SUBPARAGRAPH, idx, block),
                "EU_FMX4.SUBART_SUBPARAGRAPH_REPLACE",
                "subsection_replace",
            )
        ]

    # ---- REPEAL family ------------------------------------------------------
    m = _RE_NPL_KIND_REPEAL.match(rem)
    if m:
        kind = m.group("kind").lower().rstrip("s")
        path_kind, _node_kind, stem = _NP_KIND_TABLE[kind]
        labels = _parse_label_list(m.group("list"))
        if labels is None:
            return None
        if kind != "article" and not ctx:
            return None
        return [
            _mk(
                StructuralAction.REPEAL,
                (
                    (("article", label),)
                    if kind == "article"
                    else ctx + ((path_kind, label),)
                ),
                None,
                f"EU_FMX4.{stem}_REPEAL",
                _apply_class("article" if kind == "article" else path_kind, "repeal"),
            )
            for label in labels
        ]

    m = _RE_NPL_SUBPARA_REPEAL.match(rem)
    if m:
        if not ctx:
            return None
        idx = _ordinal_to_index(m.group("ord"))
        return [
            _mk(
                StructuralAction.REPEAL,
                ctx + (("subparagraph", idx),),
                None,
                "EU_FMX4.SUBART_SUBPARAGRAPH_REPEAL",
                "subsection_repeal",
            )
        ]

    # ---- context-carried bare verb ("In Article 9, paragraph 7, shall be
    # replaced by the following text:") — the target IS the context's deepest
    # step. --------------------------------------------------------------------
    if ctx and _RE_NPL_BARE_REPLACE.match(rem):
        path_kind, label = ctx[-1]
        if path_kind not in _STEM:
            return None
        pairs = _np_payloads_for_labels(
            np, path_kind if path_kind in _NP_PAYLOAD_TAG else "paragraph", [label]
        )
        if pairs is None:
            return _missing_payload(path_kind)
        text = pairs[0][1]
        if path_kind == "article":
            text = _strip_quoted_article_heading(text, label)
        return [
            _mk(
                StructuralAction.REPLACE,
                ctx,
                _payload_node(_NODE_KIND[path_kind], label, text),
                f"EU_FMX4.{_STEM[path_kind]}_REPLACE",
                _apply_class(path_kind, "replace"),
            )
        ]
    if ctx and _RE_NPL_BARE_REPEAL.match(rem):
        path_kind, _label = ctx[-1]
        if path_kind not in _STEM:
            return None
        return [
            _mk(
                StructuralAction.REPEAL,
                ctx,
                None,
                f"EU_FMX4.{_STEM[path_kind]}_REPEAL",
                _apply_class(path_kind, "repeal"),
            )
        ]

    # ---- annex leaf forms ----------------------------------------------------
    m = _RE_NPL_ANNEX_REPLACED_BY_TEXT.match(rem)
    if m:
        num = m.group("num").upper()
        payload_text = _own_annex_payload(own_annexes, m.group("own"))
        if not payload_text:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_annex_as_set_out_payload_separate",
                    reason=(
                        "NP annex replace names its payload annex but the body "
                        "is not in this manifestation — structural target "
                        "lowered, materialised payload is a recorded gap"
                    ),
                    source_excerpt=raw,
                    family="annex_payload_gap",
                )
            )
        return [
            _mk(
                StructuralAction.REPLACE,
                (("annex", num),),
                _payload_node(IRNodeKind.SCHEDULE, num, payload_text),
                "EU_FMX4.ANNEX_AMENDED_AS_SET_OUT",
                "whole_annex_replace",
                root="supplements",
                extra_tags=(
                    "annex_payload=inline"
                    if payload_text
                    else "annex_payload=separate_manifestation",
                ),
            )
        ]
    m = (
        _RE_NPL_ANNEX_TEXT_INSERTED_AS.match(rem)
        or _RE_NPL_ANNEX_ADDED_LAID_DOWN.match(rem)
        or _RE_NPL_ANNEX_ADDED_AS_SET_OUT.match(rem)
    )
    if m:
        num = m.group("num").upper()
        own = m.groupdict().get("own") or ""
        payload_text = _own_annex_payload(own_annexes, own)
        if not payload_text:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_np_annex_insert_payload_unresolved",
                    reason=(
                        "NP annex insert names a payload annex the manifestation "
                        "does not carry — typed annex-lane gap"
                    ),
                    source_excerpt=raw,
                    family="annex_payload_gap",
                )
            )
            return []
        return [
            _mk(
                StructuralAction.INSERT,
                (("annex", num),),
                _payload_node(IRNodeKind.SCHEDULE, num, payload_text),
                "EU_FMX4.ANNEX_INSERTED_AS_SET_OUT",
                "whole_annex_insert",
                root="supplements",
            )
        ]
    if _RE_NPL_ANNEX_INDIRECT.match(rem):
        # "Annex X is amended in accordance with Annex Y to this Regulation" —
        # annex Y carries EMBEDDED amendment instructions the grammar does not
        # execute. A typed ANNEX-LANE gap: the EU anchor compare surface is
        # article-only, so this cannot poison article scoring, but the gap is
        # recorded (never buried). Backlog: parse the own-annex instruction
        # sequence as a sub-grammar.
        diagnostics.append(
            AmendmentGrammarDiagnostic(
                rule_id="eu_fmx4_grammar_np_annex_indirect_instructions",
                reason=(
                    "annex amended 'in accordance with / as set out in' the "
                    "amender's own annex: the own annex carries an embedded "
                    "amendment-instruction sequence this grammar does not yet "
                    "execute (annex-lane capability gap)"
                ),
                source_excerpt=raw,
                family="annex_extraction_gap",
            )
        )
        return []

    # ---- recognized-but-unaddressable targets --------------------------------
    if _RE_NPL_UNADDRESSABLE.match(rem):
        diagnostics.append(
            AmendmentGrammarDiagnostic(
                rule_id="eu_fmx4_grammar_np_unaddressable_target",
                reason=(
                    "title/heading/introductory-wording targets have no "
                    "coordinate in the IR system — typed residual"
                ),
                source_excerpt=raw,
                family=(
                    "annex_extraction_gap"
                    if _ctx_is_annex_lane(ctx)
                    else "extraction_gap"
                ),
            )
        )
        return []

    return None


def _lower_np_instructions(
    nps: list[ET.Element],
    context: tuple[tuple[str, str], ...],
    *,
    op_ids: Callable[[], tuple[str, int]],
    amending_celex: str,
    base_celex: str,
    effective: str,
    enacted: str,
    result: LoweringResult,
    own_annexes: Optional[list[ET.Element]],
    depth: int = 0,
) -> None:
    """Lower each NP sub-instruction of an omnibus article (recursing nests)."""
    for np in nps:
        text = _np_prose(np, keep_child_nps=False)
        raw = " ".join(text.split())[:400]
        foreign = _foreign_target_instrument(text, base_celex)
        if foreign:
            result.instruction_count += 1
            result.diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_foreign_target_instruction",
                    reason=(
                        f"NP instruction names instrument {foreign!r} as its "
                        f"amendment target, which is not the base act "
                        f"{base_celex}; omnibus cross-target sub-instruction "
                        "suppressed"
                    ),
                    source_excerpt=raw,
                    family="foreign_target",
                )
            )
            continue
        ctx, rem = _extract_np_context(text, context)
        nested = _top_level_nps(np)
        if nested and depth < 6 and _RE_AMENDED_AS_FOLLOWS.search(rem):
            nest_ctx = _parse_nest_target(rem, ctx)
            if nest_ctx is not None:
                _lower_np_instructions(
                    nested,
                    nest_ctx,
                    op_ids=op_ids,
                    amending_celex=amending_celex,
                    base_celex=base_celex,
                    effective=effective,
                    enacted=enacted,
                    result=result,
                    own_annexes=own_annexes,
                    depth=depth + 1,
                )
                continue
            # An unparseable nest target must NOT recurse (its children would
            # mis-target) — one typed residual covering the whole nest.
            result.instruction_count += 1
            result.diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_np_nest_target_unresolved",
                    reason=(
                        "'… is amended as follows:' nest whose target the NP "
                        "grammar could not resolve; its sub-instructions are "
                        "one typed residual (never mis-targeted)"
                    ),
                    source_excerpt=raw,
                    family=(
                        "annex_extraction_gap"
                        if _ctx_is_annex_lane(ctx)
                        else "extraction_gap"
                    ),
                )
            )
            continue
        result.instruction_count += 1
        src = _source(amending_celex, base_celex, effective, enacted, raw)
        ops = _lower_np_leaf(
            rem,
            ctx,
            np,
            raw,
            op_ids=op_ids,
            src=src,
            diagnostics=result.diagnostics,
            own_annexes=own_annexes,
        )
        if ops is None:
            result.diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_uncovered_instruction",
                    reason=(
                        "NP sub-instruction matched no leaf rule (context "
                        f"{'/'.join(f'{k}:{v}' for k, v in ctx) or '-'})"
                    ),
                    source_excerpt=raw or "(empty NP instruction text)",
                    family=(
                        "annex_extraction_gap"
                        if _ctx_is_annex_lane(ctx)
                        else "extraction_gap"
                    ),
                )
            )
        else:
            result.ops.extend(ops)


def lower_amending_act(
    fmx4_bytes: bytes,
    amending_celex: str,
    *,
    base_celex: str = "",
    effective: str = "",
    enacted: str = "",
    resolve_separate_annex: Optional[Callable[[str, str], Optional[str]]] = None,
) -> LoweringResult:
    """Lower one amending act's FMX4 enacting terms into typed LegalOperations.

    Parameters
    ----------
    fmx4_bytes:
        The amending act's Formex (FMX4) XML bytes (the ACT root, or an envelope
        containing it). Each ENACTING.TERMS ARTICLE is one amendment instruction.
    amending_celex:
        CELEX of the amending act (the op source statute_id).
    effective / enacted:
        Date-of-application / entry-into-force of the amending act, threaded onto
        ``OperationSource`` so ``order_ops``' temporal key sorts these ops in
        legal-chronological order.
    resolve_separate_annex:
        Increment 3 (Goal 2 — separate-annex payloads). Optional resolver
        ``resolve_separate_annex(amending_celex, annex_label) -> annex_text``
        invoked for the indirect-annex shape when the replacement annex ships as a
        SEPARATE manifestation (absent from this main FMX4). When it returns text,
        that materialised payload is threaded into the op (provenance
        ``annex_payload=separate_resolved``) instead of leaving a recorded gap.
        Returning ``None`` (resolver can't materialise it either) preserves the
        Increment-2 typed gap. NEVER fetches on its own — the caller owns the lane.
    """
    result = LoweringResult(amending_celex=amending_celex)
    try:
        root = ET.fromstring(fmx4_bytes)
    except ET.ParseError as exc:
        result.diagnostics.append(
            AmendmentGrammarDiagnostic(
                rule_id="eu_fmx4_grammar_not_xml",
                reason=f"amending act bytes are not parseable XML: {exc}",
                source_excerpt=repr(fmx4_bytes[:80]),
                family="source_pathology",
            )
        )
        return result

    root_tag = _local(root.tag)
    if root_tag != "ACT":
        act = root.find(".//ACT")
        if act is not None:
            root = act
            root_tag = "ACT"
        elif root_tag == "ANNEX" or root.find(".//ANNEX") is not None:
            # Root hardening (goal 4): the real degree-57 amending acts are
            # acquired as an ANNEX-rooted new-annex body (the replacement annex
            # content, QUOT-delimited). Lower it as a WHOLE-ANNEX REPLACE rather
            # than rejecting it as "no ACT root".
            annex_el = root if root_tag == "ANNEX" else root.find(".//ANNEX")
            assert annex_el is not None  # guarded by the elif condition above
            _lower_annex_root(
                annex_el,
                amending_celex=amending_celex,
                base_celex=base_celex,
                effective=effective,
                enacted=enacted,
                result=result,
            )
            return result
        else:
            # DOC / other envelope with no ACT and no ANNEX: a metadata-only
            # publication manifestation (the real 32016R0690 shape). This is an
            # instruction-FREE envelope — a typed residual, not a crash and not a
            # silent zero.
            result.diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_envelope_no_enacting_terms",
                    reason=(
                        f"manifestation root {root_tag!r} carries no ACT, no "
                        "ANNEX and no enacting terms (publication envelope / "
                        "metadata-only manifestation); no instructions to lower"
                    ),
                    source_excerpt=root_tag,
                    family="source_pathology",
                )
            )
            return result

    enacting = root.find("ENACTING.TERMS")
    if enacting is None:
        result.diagnostics.append(
            AmendmentGrammarDiagnostic(
                rule_id="eu_fmx4_grammar_no_enacting_terms",
                reason="amending act has no ENACTING.TERMS",
                source_excerpt="",
                family="source_pathology",
            )
        )
        return result

    # The amending act's OWN <ANNEX> bodies (if any) are the payload for the
    # "amended/replaced as set out in the Annex to this Regulation" indirect form.
    own_annexes = root.findall("ANNEX")

    seq = 0
    for article in _top_level_amending_articles(enacting):
        seq += 1
        instr = _instruction_text(article)
        nps = _top_level_nps(article)
        if nps and _RE_AMENDED_AS_FOLLOWS.search(instr):
            # Increment 4 — OMNIBUS MULTI-POINT instruction: the opening clause
            # names the amended instrument; each NP is one sub-instruction.
            foreign = _foreign_target_instrument(instr, base_celex)
            if foreign:
                result.instruction_count += 1
                result.diagnostics.append(
                    AmendmentGrammarDiagnostic(
                        rule_id="eu_fmx4_grammar_foreign_target_instruction",
                        reason=(
                            f"omnibus instruction names instrument {foreign!r} "
                            f"as its amendment target, which is not the base "
                            f"act {base_celex}; the whole multi-point "
                            "instruction is suppressed (applying it here would "
                            "be misapplication, not coverage)"
                        ),
                        source_excerpt=" ".join(instr.split())[:400],
                        family="foreign_target",
                    )
                )
                continue
            # Per-op id/sequence allocator: doc-ordered, unique per op even when
            # one NP lowers several ops (a plural target), never colliding with
            # the whole-article lane's ``{celex}-{seq}`` ids.
            np_op_index = [0]
            article_seq = seq

            def _next_op_id(
                _idx: list[int] = np_op_index, _seq: int = article_seq
            ) -> tuple[str, int]:
                _idx[0] += 1
                return (
                    f"{amending_celex}-{_seq}.{_idx[0]}",
                    _seq * 1000 + _idx[0],
                )

            _lower_np_instructions(
                nps,
                (),
                op_ids=_next_op_id,
                amending_celex=amending_celex,
                base_celex=base_celex,
                effective=effective,
                enacted=enacted,
                result=result,
                own_annexes=own_annexes,
            )
            continue
        # Numberless whole-article INSERT ("The following article is inserted
        # (in Regulation (EU) No 1284/2009):") — the new article's number lives
        # on the quoted body's own TI.ART heading. Multi-op capable ("the
        # following Articles are inserted:" quotes several ARTICLEs).
        if _RE_ARTICLE_INSERT_NUMBERLESS.search(instr) and not _RE_ARTICLE_INSERT.search(
            instr
        ):
            result.instruction_count += 1
            foreign = _foreign_target_instrument(instr, base_celex)
            if foreign:
                result.diagnostics.append(
                    AmendmentGrammarDiagnostic(
                        rule_id="eu_fmx4_grammar_foreign_target_instruction",
                        reason=(
                            f"instruction names instrument {foreign!r} as its "
                            f"amendment target, which is not the base act "
                            f"{base_celex}; cross-target instruction suppressed"
                        ),
                        source_excerpt=" ".join(instr.split())[:400],
                        family="foreign_target",
                    )
                )
                continue
            pairs = _np_payloads_for_labels(article, "article", [])
            if pairs is None or any(not lbl for lbl, _ in pairs):
                result.diagnostics.append(
                    AmendmentGrammarDiagnostic(
                        rule_id="eu_fmx4_grammar_insert_missing_quoted_block",
                        reason=(
                            "numberless article insert exposed no quoted "
                            "ARTICLE body with its own heading number"
                        ),
                        source_excerpt=" ".join(instr.split())[:400],
                    )
                )
                continue
            src = _source(
                amending_celex,
                base_celex,
                effective,
                enacted,
                " ".join(instr.split())[:400],
            )
            for k, (label, text) in enumerate(pairs, 1):
                result.ops.append(
                    LegalOperation(
                        op_id=f"{amending_celex}-{seq}.{k}",
                        sequence=seq * 1000 + k,
                        action=StructuralAction.INSERT,
                        target=LegalAddress(path=(("article", label),)),
                        payload=_payload_node(
                            IRNodeKind.SECTION,
                            label,
                            _strip_quoted_article_heading(text, label),
                        ),
                        source=src,
                        witness_rule_id="EU_FMX4.WHOLE_ARTICLE_INSERT",
                        provenance_tags=("ir_apply_class=whole_section_insert",),
                    )
                )
            continue
        # NON-AMENDING provision census (typed, non-gap): an instruction
        # ARTICLE whose quote-free prose (NP clauses included) carries no
        # amendment verb and no for:/read: corrigendum formula is the amending
        # act's OWN substantive/final provision — it cannot touch the base act,
        # so it is typed ``non_amending_provision`` rather than an extraction
        # gap. A misclassification here cannot hide silently: an unexecuted
        # real amendment surfaces as a divergence at a fully-covered anchor,
        # which the oracle-touch metric convicts as billable.
        prose_with_nps = _np_prose(article, keep_child_nps=True)
        if (
            not _RE_AMEND_VERB.search(prose_with_nps)
            and not _RE_CORRIGENDUM_FOR_READ.search(prose_with_nps)
        ):
            result.instruction_count += 1
            result.diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_non_amending_provision",
                    reason=(
                        "instruction ARTICLE carries no amendment verb and no "
                        "quoted payload: the amending act's own substantive or "
                        "final provision, not an amendment instruction"
                    ),
                    source_excerpt=" ".join(prose_with_nps.split())[:400],
                    family="non_amending_provision",
                )
            )
            continue
        result.instruction_count += 1
        pre_diag_count = len(result.diagnostics)
        op = _lower_one_instruction(
            instr,
            article,
            seq=seq,
            amending_celex=amending_celex,
            base_celex=base_celex,
            effective=effective,
            enacted=enacted,
            diagnostics=result.diagnostics,
            own_annexes=own_annexes,
            resolve_separate_annex=resolve_separate_annex,
        )
        if op is not None:
            result.ops.append(op)
            continue
        # NP-LEAF FALLBACK (Increment 4): an article-level instruction the
        # classic rules missed may still be covered by the stricter,
        # context-anchored NP leaf grammar ("In Article 1 of Regulation (EC)
        # No 754/2009, the following points are added:" — a multi-op point
        # insert the single-op classic lane cannot express). Only attempted
        # when the classic lane's sole finding was the generic uncovered
        # diagnostic; a leaf hit replaces that diagnostic with the lowered ops
        # (or with the leaf's own more precise typed diagnostics).
        new_diags = result.diagnostics[pre_diag_count:]
        if not (
            len(new_diags) == 1
            and new_diags[0].rule_id == "eu_fmx4_grammar_uncovered_instruction"
        ):
            continue
        flat = " ".join(instr.replace("\xa0", " ").split())
        ctx, rem = _extract_np_context(flat, ())
        fallback_idx = [0]
        article_seq = seq

        def _fallback_op_id(
            _idx: list[int] = fallback_idx, _seq: int = article_seq
        ) -> tuple[str, int]:
            _idx[0] += 1
            return (f"{amending_celex}-{_seq}.{_idx[0]}", _seq * 1000 + _idx[0])

        leaf_diags: list[AmendmentGrammarDiagnostic] = []
        leaf_ops = _lower_np_leaf(
            rem,
            ctx,
            article,
            flat[:400],
            op_ids=_fallback_op_id,
            src=_source(amending_celex, base_celex, effective, enacted, flat[:400]),
            diagnostics=leaf_diags,
            own_annexes=own_annexes,
        )
        if leaf_ops is None:
            continue  # the classic uncovered diagnostic stands
        result.diagnostics.pop()  # replaced by the leaf outcome
        result.diagnostics.extend(leaf_diags)
        result.ops.extend(leaf_ops)

    return result


def _source(
    amending_celex: str, base_celex: str, effective: str, enacted: str, raw_text: str
) -> OperationSource:
    return OperationSource(
        statute_id=amending_celex,
        effective=effective,
        enacted=enacted,
        raw_text=raw_text,
    )


def _annex_number_from_title(annex_el: ET.Element) -> Optional[str]:
    """Extract the annex roman/arabic number from an ANNEX-root manifestation.

    The real new-annex form titles itself ``ANNEX III`` (the annex of the BASE
    act it replaces) in the leading ``TI``/``P``. Return the bare number (``III``)
    so the op targets ``(annex, III)`` in the base coordinate system.
    """
    for node in annex_el.iter():
        if _local(node.tag).upper() in ("TI", "P"):
            txt = _all_text(node)
            m = re.match(r"\s*ANNEX\s+([IVXLCDM]+|\d+[a-z]?)\b", txt, re.I)
            if m:
                return m.group(1).upper()
    return None


def _lower_annex_root(
    annex_el: ET.Element,
    *,
    amending_celex: str,
    base_celex: str,
    effective: str,
    enacted: str,
    result: LoweringResult,
) -> None:
    """Lower an ANNEX-rooted amending manifestation as a WHOLE-ANNEX REPLACE.

    The acquired bytes ARE the replacement annex body (the Office ships the new
    annex content, QUOT-delimited, under an ``ANNEX`` root). One instruction: the
    base act's annex N is replaced by this content.
    """
    result.instruction_count += 1
    annex_num = _annex_number_from_title(annex_el)
    block = _quoted_block_text(annex_el) or _all_text(annex_el)
    raw = " ".join(_all_text(annex_el).split())[:400]
    if annex_num is None:
        # An ANNEX-root body that IS a base-annex instruction sequence ("The
        # Annexes to Regulation (EC) No 692/2008 are amended as follows: …",
        # the real 32012R0630 shape) is annex-SCOPED by its own words: a typed
        # ANNEX-LANE gap (the article-only compare surface is untouched by it).
        # Any other numberless ANNEX-root manifestation stays a plain
        # extraction gap — it may be the AMENDER's own annex stored in lieu of
        # its act (the 32014L0059 acquisition shape), whose body amendments to
        # the base are then unknowable, so anchor suspicion must persist.
        if re.search(  # lawvm-regex: witness_only routes a numberless annex-root manifestation between the annex-lane and acquisition-gap diagnostic families
            r"\bAnnex(?:es)?\s+(?:[IVXLCDM0-9][A-Za-z0-9]{0,3}\s+)?to\s+"
            r"[^,:;]{0,90}?\b(?:is|are)\s+amended\s+as\s+follows\b",
            raw,
            re.I,
        ):
            result.diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_annex_root_instruction_sequence",
                    reason=(
                        "numberless ANNEX-root manifestation is itself a "
                        "base-annex amendment-instruction sequence this "
                        "grammar does not yet execute (annex-lane capability "
                        "gap)"
                    ),
                    source_excerpt=raw,
                    family="annex_extraction_gap",
                )
            )
            return
        result.diagnostics.append(
            AmendmentGrammarDiagnostic(
                rule_id="eu_fmx4_grammar_annex_root_no_number",
                reason=(
                    "ANNEX-root manifestation exposed no 'ANNEX <N>' title to "
                    "resolve the target annex number"
                ),
                source_excerpt=raw or "(empty annex body)",
                family="extraction_gap",
            )
        )
        return
    src = _source(amending_celex, base_celex, effective, enacted, raw)
    result.ops.append(
        LegalOperation(
            op_id=f"{amending_celex}-annex-{annex_num}",
            sequence=1,
            action=StructuralAction.REPLACE,
            # The EU annex lives in the ``supplements`` compartment root, not the
            # statute ``body`` (§5.3 / §7 delta #6, mirroring the SE bilaga mint).
            # Naming the root on the ADDRESS makes annex REPLACE/REPEAL/INSERT
            # ordinary root-selected resolution: the EU materializer dispatches to
            # the ``supplements`` lane off ``root_kind()`` instead of the retired
            # inline ``path_steps[0][0] == "annex"`` sniff.
            target=LegalAddress(path=(("annex", annex_num),), root="supplements"),
            payload=_payload_node(IRNodeKind.SCHEDULE, annex_num, block),
            source=src,
            witness_rule_id="EU_FMX4.ANNEX_ROOT_REPLACE",
            provenance_tags=("ir_apply_class=whole_annex_replace",),
        )
    )


def _lower_one_instruction(
    instr: str,
    article: ET.Element,
    *,
    seq: int,
    amending_celex: str,
    base_celex: str,
    effective: str,
    enacted: str,
    diagnostics: list[AmendmentGrammarDiagnostic],
    own_annexes: Optional[list[ET.Element]] = None,
    resolve_separate_annex: Optional[Callable[[str, str], Optional[str]]] = None,
) -> Optional[LegalOperation]:
    raw = " ".join(instr.split())[:400]
    src = _source(amending_celex, base_celex, effective, enacted, raw)

    # Cross-target guard FIRST (before any pattern can match): an omnibus
    # instruction naming a different instrument than the base must never lower
    # into the base's coordinate system (see :func:`_foreign_target_instrument`).
    foreign = _foreign_target_instrument(instr, base_celex)
    if foreign:
        diagnostics.append(
            AmendmentGrammarDiagnostic(
                rule_id="eu_fmx4_grammar_foreign_target_instruction",
                reason=(
                    f"instruction names instrument {foreign!r} as its amendment "
                    f"target, which is not the base act {base_celex}; omnibus "
                    "cross-target instruction suppressed (applying it to this "
                    "base would be misapplication, not coverage)"
                ),
                source_excerpt=raw,
                family="foreign_target",
            )
        )
        return None

    # Order matters (most specific first). Point-level edits are checked before
    # paragraph- and whole-article rules so "in Article N, point (b) ..." is not
    # captured by the broader patterns.
    paragraph_of_article_replace = _parse_paragraph_of_article_replace(instr)
    if paragraph_of_article_replace is not None:
        art, paragraph = paragraph_of_article_replace
        block = _quoted_block_text(article)
        if block is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_paragraph_of_article_replace_missing_payload",
                    reason="paragraph-of-article replace had no QUOT block payload",
                    source_excerpt=raw,
                )
            )
            return None
        path = (("article", art), ("paragraph", paragraph))
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=path),
            payload=_payload_node(IRNodeKind.PARAGRAPH, paragraph, block),
            source=src,
            witness_rule_id="EU_FMX4.SUBART_PARAGRAPH_REPLACE",
            provenance_tags=("ir_apply_class=subsection_replace",),
        )

    point_of_article_repeal = _parse_point_of_article_repeal(instr)
    if point_of_article_repeal is not None:
        art, point = point_of_article_repeal
        path = (("article", art), ("point", point))
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=path),
            source=src,
            witness_rule_id="EU_FMX4.SUBART_POINT_REPEAL",
            provenance_tags=("ir_apply_class=point_repeal",),
        )

    m = _RE_SUBART_POINT_REPEAL.search(instr)
    if m:
        path = (("article", m.group("art")), ("point", m.group("point")))
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=path),
            source=src,
            witness_rule_id="EU_FMX4.SUBART_POINT_REPEAL",
            provenance_tags=("ir_apply_class=point_repeal",),
        )

    m = _RE_SUBART_POINT_REPLACE.search(instr)
    if m:
        # Point edits carry their payload EITHER as a QUOT block (the "replaced by
        # the following: '<block>'" form) OR inline ("replaced by '<text>'").
        block = _quoted_block_text(article)
        if block is None:
            mi = _RE_INLINE_QUOTED.search(instr)
            block = mi.group("inline").strip() if mi else None
        if block is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_point_replace_missing_payload",
                    reason="point replace had neither a QUOT block nor inline quoted text",
                    source_excerpt=raw,
                )
            )
            return None
        path = (("article", m.group("art")), ("point", m.group("point")))
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=path),
            payload=_payload_node(IRNodeKind.ITEM, m.group("point"), block),
            source=src,
            witness_rule_id="EU_FMX4.SUBART_POINT_REPLACE",
            provenance_tags=("ir_apply_class=point_replace",),
        )

    # ---- Increment 3 harder sub-article shapes (most specific first) ----------
    #
    # POINT INSERT ("in Article N, the following point (c) is inserted: '<block>'").
    # Checked before point REPLACE so the "inserted" verb is not mis-read; the new
    # point is ADDED under the article (INSERT), not overwriting an existing one.
    m = _RE_SUBART_POINT_INSERT.search(instr)
    if m:
        block = _quoted_block_text(article)
        if block is None:
            mi = _RE_INLINE_QUOTED.search(instr)
            block = mi.group("inline").strip() if mi else None
        if block is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_point_insert_missing_payload",
                    reason="point insert had neither a QUOT block nor inline quoted text",
                    source_excerpt=raw,
                )
            )
            return None
        path = (("article", m.group("art")), ("point", m.group("point")))
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=path),
            payload=_payload_node(IRNodeKind.ITEM, m.group("point"), block),
            source=src,
            witness_rule_id="EU_FMX4.SUBART_POINT_INSERT",
            provenance_tags=("ir_apply_class=subsection_insert",),
        )

    # SUBPARAGRAPH REPEAL / REPLACE ("the second subparagraph of paragraph M …").
    # The ordinal is normalised to a 1-based index label. Repeal is checked first
    # (it has no payload, so a payload miss can't shadow it).
    m = _RE_SUBART_SUBPARA_REPEAL.search(instr)
    if m:
        idx = _ordinal_to_index(m.group("ord"))
        path = (("article", m.group("art")),)
        if m.group("par"):
            path += (("paragraph", m.group("par")),)
        path += (("subparagraph", idx),)
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=path),
            source=src,
            witness_rule_id="EU_FMX4.SUBART_SUBPARAGRAPH_REPEAL",
            provenance_tags=("ir_apply_class=subsection_repeal",),
        )

    m = _RE_SUBART_SUBPARA_REPLACE.search(instr)
    if m:
        block = _quoted_block_text(article)
        if block is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_subparagraph_replace_missing_quoted_block",
                    reason="subparagraph replace had no QUOT block payload",
                    source_excerpt=raw,
                )
            )
            return None
        idx = _ordinal_to_index(m.group("ord"))
        path = (("article", m.group("art")),)
        if m.group("par"):
            path += (("paragraph", m.group("par")),)
        path += (("subparagraph", idx),)
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=path),
            payload=_payload_node(IRNodeKind.SUBPARAGRAPH, idx, block),
            source=src,
            witness_rule_id="EU_FMX4.SUBART_SUBPARAGRAPH_REPLACE",
            provenance_tags=("ir_apply_class=subsection_replace",),
        )

    # INDENT (list-dash item) REPEAL / REPLACE ("the second indent of Article N …").
    m = _RE_INDENT_REPEAL.search(instr)
    if m:
        idx = _ordinal_to_index(m.group("ord"))
        path = (("article", m.group("art")), ("item", idx))
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=path),
            source=src,
            witness_rule_id="EU_FMX4.INDENT_REPEAL",
            provenance_tags=("ir_apply_class=subsection_repeal",),
        )

    m = _RE_INDENT_REPLACE.search(instr)
    if m:
        block = _quoted_block_text(article)
        if block is None:
            mi = _RE_INLINE_QUOTED.search(instr)
            block = mi.group("inline").strip() if mi else None
        if block is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_indent_replace_missing_payload",
                    reason="indent replace had neither a QUOT block nor inline quoted text",
                    source_excerpt=raw,
                )
            )
            return None
        idx = _ordinal_to_index(m.group("ord"))
        path = (("article", m.group("art")), ("item", idx))
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=path),
            payload=_payload_node(IRNodeKind.ITEM, idx, block),
            source=src,
            witness_rule_id="EU_FMX4.INDENT_REPLACE",
            provenance_tags=("ir_apply_class=subsection_replace",),
        )

    # RENUMBER ("Article N is renumbered as Article M"). The structural intent is
    # captured as a RENUMBER op carrying the destination label in a provenance tag;
    # the EU apply seam currently OWNS renumber as a typed
    # ``eu_replay_unsupported_action`` skip (never a silent drop). Lowering it (vs
    # leaving it an uncovered_instruction residual) makes the move VISIBLE to
    # ordering/conflict detection and records the destination for a future
    # increment that materialises the relabel.
    m = _RE_ARTICLE_RENUMBER.search(instr)
    if m:
        from_num = m.group("from")
        to_num = m.group("to") or ""
        path = (("article", from_num),)
        tags = ("ir_apply_class=renumber",)
        if to_num:
            tags += (f"renumber_to=article:{to_num}",)
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=path),
            source=src,
            witness_rule_id="EU_FMX4.ARTICLE_RENUMBER",
            provenance_tags=tags,
        )

    # Indirect annex amendment ("Annex N to Regulation X is replaced/amended as set
    # out in the Annex to this Regulation") — the DOMINANT real EU sanctions-amender
    # shape (32017R0489, 32018R0870, and 31/33 instructions of 32019R1163). Checked
    # BEFORE the direct _RE_ANNEX_REPLACE, which would otherwise partial-match the
    # "is replaced" verb and look for a (non-existent) inline QUOT block. The payload
    # is the amending act's OWN <ANNEX> body; when that annex ships as a SEPARATE
    # manifestation (not in this main FMX4), the op is still lowered with a typed
    # payload-gap note — the STRUCTURAL effect (which base annex is replaced) is
    # recoverable; only the materialised replacement text is the recorded gap.
    m = _RE_ANNEX_AS_SET_OUT.search(instr)
    if m:
        # Numbered ("Annex III …") has an executable base-annex coordinate. The
        # sole form ("The Annex …") does not: resolving a bare annex to "the
        # base's single annex" would be target invention at lowering time, so it
        # remains an annex-lane extraction frontier instead of minting
        # ``@supplements annex:``.
        annex_num = (m.group("num") or "").upper()
        if not annex_num:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_annex_as_set_out_target_unresolved",
                    reason=(
                        "indirect annex amendment uses the sole-annex form "
                        "('the Annex ... as set out in the Annex to this "
                        "Regulation') without a numbered base-annex coordinate; "
                        "not lowered to a bare annex target"
                    ),
                    source_excerpt=raw,
                    family="annex_extraction_gap",
                )
            )
            return None
        annex_payload = (
            " ".join(_all_text(a) for a in own_annexes) if own_annexes else ""
        )
        payload_origin = "inline" if annex_payload else ""
        # Increment 3 (Goal 2): when the replacement annex ships as a SEPARATE
        # manifestation (no inline <ANNEX>), try the caller-supplied resolver to
        # MATERIALISE that separate payload before falling back to the typed gap.
        if not annex_payload and resolve_separate_annex is not None:
            resolved = resolve_separate_annex(amending_celex, annex_num)
            if resolved:
                annex_payload = " ".join(resolved.split())
                payload_origin = "separate_resolved"
        if not annex_payload:
            payload_origin = "separate_manifestation"
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_annex_as_set_out_payload_separate",
                    reason=(
                        "indirect annex amendment ('as set out in the Annex to "
                        "this Regulation') names the target annex but its "
                        "replacement body ships as a separate ANNEX manifestation "
                        "absent from this main FMX4 and no resolver materialised "
                        "it — structural target lowered, materialised payload is a "
                        "recorded gap"
                    ),
                    source_excerpt=raw,
                    family="annex_payload_gap",
                )
            )
        path = (("annex", annex_num),)
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPLACE,
            # ``root="supplements"`` — the annex compartment root (§5.3 / §7
            # delta #6); see the ANNEX_ROOT_REPLACE mint above.
            target=LegalAddress(path=path, root="supplements"),
            payload=_payload_node(IRNodeKind.SCHEDULE, annex_num, annex_payload),
            source=src,
            witness_rule_id="EU_FMX4.ANNEX_AMENDED_AS_SET_OUT",
            provenance_tags=(
                "ir_apply_class=whole_annex_replace",
                "annex_payload=" + payload_origin,
            ),
        )

    # Annex replace ("Annex II is replaced by the following: '<block>'").
    m = _RE_ANNEX_REPLACE.search(instr)
    if m:
        block = _quoted_block_text(article)
        if block is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_annex_replace_missing_quoted_block",
                    reason="annex replace had no QUOT block payload",
                    source_excerpt=raw,
                )
            )
            return None
        path = (("annex", m.group("num").upper()),)
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPLACE,
            # ``root="supplements"`` — the annex compartment root (§5.3 / §7
            # delta #6); see the ANNEX_ROOT_REPLACE mint above.
            target=LegalAddress(path=path, root="supplements"),
            payload=_payload_node(IRNodeKind.SCHEDULE, m.group("num").upper(), block),
            source=src,
            witness_rule_id="EU_FMX4.WHOLE_ANNEX_REPLACE",
            provenance_tags=("ir_apply_class=whole_annex_replace",),
        )

    # Order matters: sub-article replace before whole-article replace (the
    # whole-article pattern would otherwise also match "in Article N ...").
    m = _RE_SUBART_REPLACE.search(instr)
    if m:
        block = _quoted_block_text(article)
        if block is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_replace_missing_quoted_block",
                    reason="sub-article replace had no QUOT block payload",
                    source_excerpt=raw,
                )
            )
            return None
        path = (("article", m.group("art")), ("paragraph", m.group("par")))
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=path),
            payload=_payload_node(IRNodeKind.PARAGRAPH, m.group("par"), block),
            source=src,
            witness_rule_id="EU_FMX4.SUBART_PARAGRAPH_REPLACE",
            provenance_tags=("ir_apply_class=subsection_replace",),
        )

    m = _RE_ARTICLE_INSERT.search(instr)
    if m:
        block = _quoted_block_text(article)
        if block is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_insert_missing_quoted_block",
                    reason="article insert had no QUOT block payload",
                    source_excerpt=raw,
                )
            )
            return None
        path = (("article", m.group("num")),)
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=path),
            payload=_payload_node(
                IRNodeKind.SECTION,
                m.group("num"),
                _strip_quoted_article_heading(block, m.group("num")),
            ),
            source=src,
            witness_rule_id="EU_FMX4.WHOLE_ARTICLE_INSERT",
            provenance_tags=("ir_apply_class=whole_section_insert",),
        )

    m = _RE_ARTICLE_REPLACE.search(instr)
    if m:
        block = _quoted_block_text(article)
        if block is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_replace_missing_quoted_block",
                    reason="article replace had no QUOT block payload",
                    source_excerpt=raw,
                )
            )
            return None
        path = (("article", m.group("num")),)
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=path),
            payload=_payload_node(
                IRNodeKind.SECTION,
                m.group("num"),
                _strip_quoted_article_heading(block, m.group("num")),
            ),
            source=src,
            witness_rule_id="EU_FMX4.WHOLE_ARTICLE_REPLACE",
            provenance_tags=("ir_apply_class=whole_section_replace",),
        )

    m = _RE_ARTICLE_REPEAL.search(instr)
    if m:
        path = (("article", m.group("num")),)
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=path),
            source=src,
            witness_rule_id="EU_FMX4.WHOLE_ARTICLE_REPEAL",
            provenance_tags=("ir_apply_class=whole_section_repeal",),
        )

    # Corrigendum "for: '<for>' read: '<read>'": a TEXT_REPLACE of the erroneous
    # substring with the corrected one. Corrigenda apply on the corrected act's
    # OWN timeline (design §3.5). The target is the Article named in the same
    # instruction if present, else the act-wide context (no structural address).
    m = _RE_CORRIGENDUM_FOR_READ.search(instr)
    if m:
        for_text = " ".join(m.group("for").split()).strip(" '‘’\"“”")
        read_text = " ".join(m.group("read").split()).strip(" '‘’\"“”")
        if not for_text or not read_text:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_corrigendum_empty_for_read",
                    reason="corrigendum for/read formula resolved to empty text",
                    source_excerpt=raw,
                    family="corrigendum",
                )
            )
            return None
        art_m = re.search(r"\bArticle\s+(\d+[a-z]?)\b", instr, re.I)
        if art_m is None:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_corrigendum_no_structural_target",
                    reason=(
                        "corrigendum for/read formula names no Article target; "
                        "an act-wide text patch is not addressable in the IR "
                        "coordinate system — recorded as a typed residual"
                    ),
                    source_excerpt=raw,
                    family="corrigendum",
                )
            )
            return None
        return LegalOperation(
            op_id=f"{amending_celex}-{seq}",
            sequence=seq,
            action=StructuralAction.TEXT_PATCH,
            target=LegalAddress(path=(("article", art_m.group(1)),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text=for_text),
                replacement=read_text,
            ),
            source=src,
            witness_rule_id="EU_FMX4.CORRIGENDUM_FOR_READ",
            provenance_tags=("ir_apply_class=corrigendum_text_replace",),
        )

    # Annex indirection ("Annex I … is amended in accordance with the Annex to
    # this Regulation") — the amender's own annex carries an EMBEDDED
    # amendment-instruction sequence this grammar does not execute. A typed
    # ANNEX-LANE gap: the EU anchor compare surface is article-only, so the gap
    # is recorded (never buried) without poisoning article scoring.
    if _RE_ANNEX_IN_ACCORDANCE.search(instr):
        diagnostics.append(
            AmendmentGrammarDiagnostic(
                rule_id="eu_fmx4_grammar_annex_indirect_instructions",
                reason=(
                    "annex amended 'in accordance with' the amender's own "
                    "annex: the own annex carries an embedded amendment-"
                    "instruction sequence this grammar does not yet execute "
                    "(annex-lane capability gap)"
                ),
                source_excerpt=raw,
                family="annex_extraction_gap",
            )
        )
        return None

    # Act-TITLE replace — the act title has no unit coordinate on the
    # article-only compare surface; typed metadata-lane residual.
    if _RE_ACT_TITLE_REPLACE.search(instr):
        diagnostics.append(
            AmendmentGrammarDiagnostic(
                rule_id="eu_fmx4_grammar_act_title_replace",
                reason=(
                    "the act's own TITLE is replaced — no unit coordinate on "
                    "the article-only compare surface (typed metadata-lane "
                    "residual)"
                ),
                source_excerpt=raw,
                family="act_metadata_gap",
            )
        )
        return None

    diagnostics.append(
        AmendmentGrammarDiagnostic(
            rule_id="eu_fmx4_grammar_uncovered_instruction",
            reason=(
                "grammar covers whole/sub-article replace (paragraph/point), "
                "article insert/repeal, point repeal, annex replace, and "
                "for/read corrigenda; this instruction matched none"
            ),
            source_excerpt=raw or "(empty instruction text)",
        )
    )
    return None
