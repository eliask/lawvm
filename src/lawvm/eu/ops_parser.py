"""EU Amendment -> LawVM IR Operation parser.

The old Stanza-based parser has been removed. This module now provides only a
minimal compatibility parser so the EU replay scaffolding remains importable
until the EU frontend is rebuilt properly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import StructuralAction

VERB_MAPPING = {
    "replace": "replace",
    "insert": "insert",
    "add": "insert",
    "delete": "repeal",
    "repeal": "repeal",
    "amend": "replace",
}

UNSUPPORTED_ACTION_VERBS = frozenset(
    {
        "expire",
        "move",
        "renumber",
        "suspend",
        "transfer",
    }
)

UNKNOWN_OPERATIVE_ACTION_VERBS = frozenset(
    {
        "modified",
        "modifies",
        "modify",
        "substituted",
        "substitutes",
        "substitute",
        "varied",
        "varies",
        "vary",
    }
)

KIND_MAPPING = {
    "article": "article",
    "paragraph": "paragraph",
    "point": "point",
    "annex": "annex",
    "recital": "recital",
    "subparagraph": "subparagraph",
    "chapter": "chapter",
    "division": "division",
}

_CONTEXT_RE = re.compile(r"\b(in|to)\s+(Article|Chapter|Division)\s+([0-9A-Za-z]+)\b", re.I)
_TARGET_RE = re.compile(
    r"\b(Article|paragraph|point|annex|recital|subparagraph|chapter|division)\s+([0-9A-Za-z().-]+)\b",
    re.I,
)


@dataclass
class EUOpsParserDiagnostic:
    rule_id: str
    family: str
    phase: str
    reason: str
    source_excerpt: str
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: str = "record"

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "family": self.family,
            "phase": self.phase,
            "reason": self.reason,
            "source_excerpt": self.source_excerpt,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }


@dataclass
class EUOpsParser:
    """Minimal compatibility parser for EU Regulation amendments."""

    def __init__(self, model_dir: Optional[str] = None, cache_dir: Optional[str] = None):
        self.model_dir = model_dir
        self.cache_dir = cache_dir
        self.diagnostics: list[EUOpsParserDiagnostic] = []

    def extract_ops(self, text: str) -> List[LegalOperation]:
        """Extract LegalOperations from amendment text with shallow regexes."""
        self.diagnostics = []
        corrigenda_ops = self._extract_corrigenda_ops(text)
        corrigenda_spans = self._corrigenda_formula_spans(text)
        ordinary_text = self._mask_spans(text, corrigenda_spans)

        ops: List[LegalOperation] = list(corrigenda_ops)
        context_path: List[Tuple[str, str]] = []
        for segment in re.split(r"[;\n]+", ordinary_text):
            segment = segment.strip()
            if not segment:
                continue

            lowered = segment.lower()
            action = next(
                (mapped for verb, mapped in VERB_MAPPING.items() if re.search(rf"\b{verb}\w*\b", lowered)),
                None,
            )
            if action is None:
                unsupported_verb = next(
                    (
                        verb
                        for verb in UNSUPPORTED_ACTION_VERBS
                        if re.search(rf"\b{verb}\w*\b", lowered)
                    ),
                    None,
                )
                if unsupported_verb is not None:
                    self._record_diagnostic(
                        rule_id="eu_ops_parser_unsupported_action_segment",
                        reason=(
                            "EU parser saw an operative-looking amendment segment with an unsupported "
                            f"action verb: {unsupported_verb}"
                        ),
                        source_excerpt=segment,
                        family="unsupported_action",
                        blocking=True,
                        strict_disposition="block",
                    )
                else:
                    unknown_operative_verb = next(
                        (
                            verb
                            for verb in UNKNOWN_OPERATIVE_ACTION_VERBS
                            if re.search(rf"\b{verb}\w*\b", lowered)
                        ),
                        None,
                    )
                    if unknown_operative_verb is not None and _TARGET_RE.search(segment):
                        self._record_diagnostic(
                            rule_id="eu_ops_parser_unknown_operative_segment",
                            reason=(
                                "EU parser saw an operative-looking amendment segment with a target "
                                f"but no supported action mapping for verb: {unknown_operative_verb}"
                            ),
                            source_excerpt=segment,
                            family="unsupported_action",
                            blocking=True,
                            strict_disposition="block",
                        )
                continue
            action_kind = StructuralAction(action)
            segment_op_count = 0

            context_match = _CONTEXT_RE.search(segment)
            if context_match:
                context_kind = KIND_MAPPING[context_match.group(2).lower()]
                context_path = [(context_kind, context_match.group(3).strip("()."))]

            for index, match in enumerate(_TARGET_RE.finditer(segment), start=1):
                raw_kind = match.group(1).lower()
                raw_label = match.group(2).strip("().")
                if (
                    raw_kind in {"article", "chapter", "division"}
                    and context_match
                    and match.start() == context_match.start(2)
                ):
                    continue
                kind = KIND_MAPPING.get(raw_kind)
                if not kind or not raw_label:
                    continue
                path = tuple(context_path + [(kind, raw_label)])
                ops.append(
                    LegalOperation(
                        op_id=f"eu-compat-{len(ops) + 1}-{index}",
                        sequence=len(ops) + 1,
                        action=action_kind,
                        target=LegalAddress(path=path),
                        source=OperationSource(statute_id="unknown"),
                        provenance_tags=(f"ir_apply_class={self._apply_class(action_kind, path)}",),
                    )
                )
                segment_op_count += 1
            if segment_op_count == 0:
                self._record_diagnostic(
                    rule_id="eu_ops_parser_segment_unparsed",
                    reason="EU parser saw an amendment verb but could not lower any target from the segment",
                    source_excerpt=segment,
                )

        return ops

    def _corrigenda_formula_spans(self, text: str) -> List[Tuple[int, int]]:
        return [match.span() for match in re.finditer(r"for\s*:(.*?)read\s*:(.*?)(;|\.|\n|$)", text, re.S | re.I)]

    def _mask_spans(self, text: str, spans: List[Tuple[int, int]]) -> str:
        if not spans:
            return text
        chars = list(text)
        for start, end in spans:
            for index in range(start, end):
                chars[index] = "\n" if chars[index] in ";\n." else " "
        return "".join(chars)

    def _extract_corrigenda_ops(self, text: str) -> List[LegalOperation]:
        """Specific handling for EU Corrigenda 'for: ... read: ...' formulas."""
        ops: List[LegalOperation] = []

        for match in re.finditer(r"for\s*:(.*?)read\s*:(.*?)(;|\.|\n|$)", text, re.S | re.I):
            content_before = text[: match.start()]
            target_match = list(re.finditer(r"(Article|paragraph|point)\s+([0-9a-zA-Z\(\)\.]+)", content_before, re.I))
            if not target_match:
                self._record_diagnostic(
                    rule_id="eu_ops_parser_corrigendum_target_missing",
                    reason="EU corrigendum formula had no preceding Article, paragraph, or point target",
                    source_excerpt=match.group(0),
                )
                continue

            last_target = target_match[-1]
            kind = last_target.group(1).lower()
            num = last_target.group(2).strip("(). :")
            path = ((kind, num),)

            ops.append(
                LegalOperation(
                    op_id=f"corrigenda-{len(ops) + 1}",
                    sequence=len(ops) + 1,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=path),
                    source=OperationSource(statute_id="unknown"),
                    provenance_tags=(f"ir_apply_class={self._apply_class(StructuralAction.REPLACE, path)}",),
                )
            )

        return ops

    def _record_diagnostic(
        self,
        *,
        rule_id: str,
        reason: str,
        source_excerpt: str,
        family: str = "extraction_gap",
        blocking: bool = False,
        strict_disposition: str = "record",
    ) -> None:
        self.diagnostics.append(
            EUOpsParserDiagnostic(
                rule_id=rule_id,
                family=family,
                phase="extraction",
                reason=reason,
                source_excerpt=" ".join(source_excerpt.split())[:240],
                blocking=blocking,
                strict_disposition=strict_disposition,
            )
        )

    def _apply_class(self, action: StructuralAction, path: Tuple[Tuple[str, str], ...]) -> str:
        has_sub = len(path) > 1
        if action == StructuralAction.REPLACE:
            return "subsection_replace" if has_sub else "whole_section_replace"
        if action == StructuralAction.INSERT:
            return "subsection_insert" if has_sub else "whole_section_insert"
        if action == StructuralAction.REPEAL:
            return "subsection_repeal" if has_sub else "whole_section_repeal"
        return "unknown"
