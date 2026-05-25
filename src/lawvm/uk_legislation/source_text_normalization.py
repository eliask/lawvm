"""UK source-text normalization surfaces.

These helpers build parser/comparison views over source text. They must not be
used to mutate stored source witnesses or replay payload text silently.
"""

from __future__ import annotations


_DASH_VARIANTS = {
    "\u2010",
    "\u2011",
    "\u2012",
    "\u2013",
    "\u2014",
    "\u2015",
    "\u2212",
}

_OPEN_TO_CLOSE_QUOTE = {
    "\u201c": "\u201d",
    "\u2018": "\u2019",
    '"': '"',
    "'": "'",
}


def _is_word_apostrophe(text: str, index: int) -> bool:
    if text[index] != "'":
        return False
    previous_char = text[index - 1] if index > 0 else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""
    return previous_char.isalnum() and next_char.isalnum()


def normalize_uk_parser_text(text: str) -> str:
    """Return a parser-only view of UK source text.

    The view collapses transport whitespace and canonicalizes dash-like
    instruction punctuation outside quoted legal text. Quoted payload/preimage
    text is preserved after the existing whitespace collapse so parser matching
    does not rewrite legal fragments such as ``"A-B"`` or ``"A-B-C"``.
    """
    collapsed = " ".join(str(text or "").split())
    out: list[str] = []
    quote_stack: list[str] = []
    for index, char in enumerate(collapsed):
        if quote_stack:
            out.append(char)
            if char == quote_stack[-1] and not _is_word_apostrophe(collapsed, index):
                quote_stack.pop()
            continue
        if _is_word_apostrophe(collapsed, index):
            out.append(char)
            continue
        closing_quote = _OPEN_TO_CLOSE_QUOTE.get(char)
        if closing_quote is not None:
            quote_stack.append(closing_quote)
            out.append(char)
        elif char in _DASH_VARIANTS:
            out.append("\u2014")
        else:
            out.append(char)
    return "".join(out).strip()
