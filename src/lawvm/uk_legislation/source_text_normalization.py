"""UK source-text normalization surfaces.

These helpers build parser/comparison views over source text. They must not be
used to mutate stored source witnesses or replay payload text silently.
"""

from __future__ import annotations

from lawvm.core.comparison_normalization import (
    DASH_CANONICAL_EM,
    DASH_VARIANT_CHARS,
    ComparisonNormalizationRule,
    normalize_comparison_text,
)

# Dash-variant fold is the jurisdiction-agnostic ``dash_variant_canonicalization``
# primitive, now owned by the shared comparison-normalization registry
# (``DASH_VARIANT_CANONICALIZATION_RULE``). The UK parser view applies it OUTSIDE
# quoted legal text only (so ``"A-B"`` payloads are preserved), so it consumes the
# shared variant set + canonical target as DATA rather than re-executing the flat
# pipeline over the whole string.
_DASH_VARIANTS = DASH_VARIANT_CHARS

_OPEN_TO_CLOSE_QUOTE = {
    "\u201c": "\u201d",
    "\u2018": "\u2019",
    '"': '"',
    "'": "'",
}

# UK-specific OCR/transport token-join repairs, expressed as typed
# comparison-normalization rules and executed via the shared registry mechanism
# (``normalize_comparison_text``) instead of an open-coded replace loop. Each base
# pair contributes its lowercase form and its ``str.capitalize()`` form, applied in
# the same order as the previous loop so the output is byte-identical.
_OUTSIDE_QUOTE_TOKEN_JOIN_PAIRS = (
    ("thereshall", "there shall"),
    ("thereis", "there is"),
    ("thereare", "there are"),
    ("beomitted", "be omitted"),
    ("berepealed", "be repealed"),
)


def _build_outside_quote_token_join_rules(
    pairs: tuple[tuple[str, str], ...],
) -> tuple[ComparisonNormalizationRule, ...]:
    rules: list[ComparisonNormalizationRule] = []
    for joined, replacement in pairs:
        rules.append(
            ComparisonNormalizationRule(
                name=f"uk_outside_quote_token_join_{joined}",
                rule_class="ocr_token_join",
                kind="literal",
                description=f"Repair the transport-glued token {joined!r} -> {replacement!r}.",
                old_text=joined,
                new_text=replacement,
            )
        )
        rules.append(
            ComparisonNormalizationRule(
                name=f"uk_outside_quote_token_join_{joined.capitalize()}",
                rule_class="ocr_token_join",
                kind="literal",
                description=(
                    f"Repair the capitalized transport-glued token "
                    f"{joined.capitalize()!r} -> {replacement.capitalize()!r}."
                ),
                old_text=joined.capitalize(),
                new_text=replacement.capitalize(),
            )
        )
    return tuple(rules)


_OUTSIDE_QUOTE_TOKEN_JOIN_RULES = _build_outside_quote_token_join_rules(
    _OUTSIDE_QUOTE_TOKEN_JOIN_PAIRS
)


def _is_word_apostrophe(text: str, index: int) -> bool:
    if text[index] != "'":
        return False
    previous_char = text[index - 1] if index > 0 else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""
    return previous_char.isalnum() and next_char.isalnum()


def _repair_outside_quote_token_joins(segment: str) -> str:
    return normalize_comparison_text(segment, _OUTSIDE_QUOTE_TOKEN_JOIN_RULES).text


def normalize_uk_parser_text(text: str) -> str:
    """Return a parser-only view of UK source text.

    The view collapses transport whitespace and canonicalizes dash-like
    instruction punctuation outside quoted legal text. Quoted payload/preimage
    text is preserved after the existing whitespace collapse so parser matching
    does not rewrite legal fragments such as ``"A-B"`` or ``"A-B-C"``.
    """
    collapsed = " ".join(str(text or "").split())
    out: list[str] = []
    outside_segment: list[str] = []
    quote_stack: list[str] = []
    def flush_outside_segment() -> None:
        if outside_segment:
            out.append(_repair_outside_quote_token_joins("".join(outside_segment)))
            outside_segment.clear()

    for index, char in enumerate(collapsed):
        if quote_stack:
            flush_outside_segment()
            out.append(char)
            if char == quote_stack[-1] and not _is_word_apostrophe(collapsed, index):
                quote_stack.pop()
            continue
        if _is_word_apostrophe(collapsed, index):
            outside_segment.append(char)
            continue
        closing_quote = _OPEN_TO_CLOSE_QUOTE.get(char)
        if closing_quote is not None:
            flush_outside_segment()
            quote_stack.append(closing_quote)
            out.append(char)
        elif char in _DASH_VARIANTS:
            outside_segment.append(DASH_CANONICAL_EM)
        else:
            outside_segment.append(char)
    flush_outside_segment()
    return "".join(out).strip()
