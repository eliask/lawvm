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

Scope (Increment 0, honestly partial)
--------------------------------------
The pilot grammar covers the WHOLE-ARTICLE instruction families that dominate EU
amending acts, with quoted-block payload capture:

1. REPLACE — *"Article N is replaced by the following: '<block>'"* → REPLACE op
   on ``(article, N)`` with the quoted block as payload IR.
2. INSERT — *"the following Article Na is inserted: '<block>'"* → INSERT op.
3. REPEAL — *"Article N is deleted"* / *"is repealed"* → REPEAL op (no payload).
4. Sub-article REPLACE — *"in Article N, paragraph M is replaced by the
   following: '<block>'"* → REPLACE on ``(article, N)/(paragraph, M)``.

Every other instruction shape (point/subparagraph edits, ``for:…read:…``
corrigenda formulas, list/annex edits, renumber) is OUT of scope for Increment 0
and is surfaced as a typed :class:`AmendmentGrammarDiagnostic`
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

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction

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


def _local(tag: object) -> str:
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag
    return str(tag)


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
    # Wrapper form: <QUOT>...</QUOT>
    for node in el.iter():
        if _local(node.tag).upper() in ("QUOT",):
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

    if _local(root.tag) != "ACT":
        act = root.find(".//ACT")
        if act is not None:
            root = act
        else:
            result.diagnostics.append(
                AmendmentGrammarDiagnostic(
                    rule_id="eu_fmx4_grammar_no_act_root",
                    reason=f"expected ACT root or descendant, got {_local(root.tag)!r}",
                    source_excerpt=_local(root.tag),
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

    seq = 0
    for article in enacting.iter("ARTICLE"):
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
) -> Optional[LegalOperation]:
    raw = " ".join(instr.split())[:400]
    src = _source(amending_celex, base_celex, effective, enacted, raw)

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

    diagnostics.append(
        AmendmentGrammarDiagnostic(
            rule_id="eu_fmx4_grammar_uncovered_instruction",
            reason=(
                "Increment-0 grammar covers whole/sub-article replace, article "
                "insert, article repeal; this instruction matched none"
            ),
            source_excerpt=raw or "(empty instruction text)",
        )
    )
    return None
