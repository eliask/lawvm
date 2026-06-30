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

Every still-unhandled instruction shape (subparagraph edits, list edits inside an
article, renumber/move) remains a typed :class:`AmendmentGrammarDiagnostic`
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
from typing import Optional

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.provenance import OperationSource
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

# "Article 5 is replaced by the following" / "Article 5(2) is replaced ..."
_RE_ARTICLE_REPLACE = re.compile(
    r"\bArticle\s+(?P<num>\d+[a-z]?)\b.*?\bis\s+replaced\s+by\s+the\s+following\b",
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
# "Article 5 is deleted" / "is repealed"
_RE_ARTICLE_REPEAL = re.compile(
    r"\bArticle\s+(?P<num>\d+[a-z]?)\b.*?\bis\s+(?:deleted|repealed)\b",
    re.I | re.S,
)
# "in Article 12, point (b) is replaced by the following" / "... is replaced by '...'"
_RE_SUBART_POINT_REPLACE = re.compile(
    r"\bin\s+Article\s+(?P<art>\d+[a-z]?)\b.*?\bpoint\s+\((?P<point>[a-z0-9]+)\)"
    r".*?\bis\s+replaced\s+by\b",
    re.I | re.S,
)
# "in Article 12, point (b) is deleted"
_RE_SUBART_POINT_REPEAL = re.compile(
    r"\bin\s+Article\s+(?P<art>\d+[a-z]?)\b.*?\bpoint\s+\((?P<point>[a-z0-9]+)\)"
    r".*?\bis\s+(?:deleted|repealed)\b",
    re.I | re.S,
)
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
    # leading/trailing bare "Article N" heading of the quoted body is kept (it is
    # part of the replacement text), matching the fixture's QUOT-wrapper form.
    for node in el.iter():
        if _local(node.tag).upper() in ("QUOT", "QUOT.S"):
            txt = _all_text(node)
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


def _payload_node(kind: IRNodeKind, label: str, text: str) -> IRNode:
    """Build a replacement/insert payload IRNode from a captured quoted block."""
    return IRNode(kind=kind, label=label, text=text)


# ---------------------------------------------------------------------------
# Lowering entry point
# ---------------------------------------------------------------------------


def lower_amending_act(
    fmx4_bytes: bytes,
    amending_celex: str,
    *,
    base_celex: str = "",
    effective: str = "",
    enacted: str = "",
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
        result.instruction_count += 1
        instr = _instruction_text(article)
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
        )
        if op is not None:
            result.ops.append(op)

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
            target=LegalAddress(path=(("annex", annex_num),)),
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
) -> Optional[LegalOperation]:
    raw = " ".join(instr.split())[:400]
    src = _source(amending_celex, base_celex, effective, enacted, raw)

    # Order matters (most specific first). Point-level edits are checked before
    # paragraph- and whole-article rules so "in Article N, point (b) ..." is not
    # captured by the broader patterns.
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
        # Numbered ("Annex III …") or sole-annex ("The Annex … is replaced"). The
        # sole form targets the base's single annex with an empty number label.
        annex_num = (m.group("num") or "").upper()
        annex_payload = (
            " ".join(_all_text(a) for a in own_annexes) if own_annexes else ""
        )
        if not annex_payload:
            diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_annex_as_set_out_payload_separate",
                    reason=(
                        "indirect annex amendment ('as set out in the Annex to "
                        "this Regulation') names the target annex but its "
                        "replacement body ships as a separate ANNEX manifestation "
                        "absent from this main FMX4 — structural target lowered, "
                        "materialised payload is a recorded gap"
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
            target=LegalAddress(path=path),
            payload=_payload_node(IRNodeKind.SCHEDULE, annex_num, annex_payload),
            source=src,
            witness_rule_id="EU_FMX4.ANNEX_AMENDED_AS_SET_OUT",
            provenance_tags=(
                "ir_apply_class=whole_annex_replace",
                "annex_payload="
                + ("inline" if annex_payload else "separate_manifestation"),
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
            target=LegalAddress(path=path),
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
            payload=_payload_node(IRNodeKind.SECTION, m.group("num"), block),
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
            payload=_payload_node(IRNodeKind.SECTION, m.group("num"), block),
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
            action=StructuralAction.TEXT_REPLACE,
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
