"""Pure EE text morphology helpers.

This module holds sentence-level text helpers extracted from the larger Estonia
frontend so Phase 4 file decomposition can start without changing behavior.
"""
from __future__ import annotations

import re


def split_ee_sentences(text: str) -> list[str]:
    """Split EE prose into sentences without breaking on ordinal/date markers."""
    stripped = (text or "").strip()
    if not stripped:
        return []
    parts: list[str] = []
    start = 0
    for match in re.finditer(r'\.\s+', stripped):
        dot_idx = match.start()
        prev_char = stripped[dot_idx - 1] if dot_idx > 0 else ""
        next_char = stripped[match.end()] if match.end() < len(stripped) else ""
        if prev_char.isdigit() and next_char.islower():
            continue
        part = stripped[start: dot_idx + 1].strip()
        if part:
            parts.append(part)
        start = match.end()
    tail = stripped[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def replace_first_sentence(text: str, replacement: str) -> str:
    """Replace the first sentence in text, preserving the rest."""
    stripped = (text or "").strip()
    repl = (replacement or "").strip()
    if not stripped:
        return repl
    sentences = split_ee_sentences(stripped)
    if not sentences:
        return repl
    if len(sentences) == 1:
        return repl
    if not repl:
        return " ".join(sentences[1:]).strip()
    sentences[0] = repl
    return " ".join(sentences).strip()


def replace_sentence(text: str, replacement: str, sentence_index: int) -> str:
    """Replace one sentence in text, preserving the rest."""
    stripped = (text or "").strip()
    repl = (replacement or "").strip()
    if not stripped:
        return repl
    if sentence_index == 0:
        return replace_first_sentence(stripped, repl)
    sentences = split_ee_sentences(stripped)
    if not sentences:
        return repl
    if sentence_index >= 1_000_000:
        sentence_index = len(sentences) - 1
    if sentence_index >= len(sentences):
        return stripped
    if not repl:
        kept = [sentence for idx, sentence in enumerate(sentences) if idx != sentence_index]
        return " ".join(kept).strip()
    sentences[sentence_index] = repl
    return " ".join(sentences).strip()


def insert_sentence_after(text: str, inserted: str, sentence_index: int) -> str:
    """Insert one sentence after the targeted sentence index in EE prose."""
    stripped = (text or "").strip()
    ins = (inserted or "").strip()
    if not stripped:
        return ins
    if not ins:
        return stripped
    sentences = split_ee_sentences(stripped)
    if not sentences:
        return f"{stripped} {ins}".strip()
    insert_at = min(sentence_index + 1, len(sentences))
    sentences.insert(insert_at, ins)
    return " ".join(sentences).strip()


def insert_sentence_before(text: str, inserted: str, sentence_index: int) -> str:
    """Insert one sentence before the targeted sentence index in EE prose."""
    stripped = (text or "").strip()
    ins = (inserted or "").strip()
    if not stripped:
        return ins
    if not ins:
        return stripped
    sentences = split_ee_sentences(stripped)
    if not sentences:
        return f"{ins} {stripped}".strip()
    insert_at = max(0, min(sentence_index, len(sentences)))
    sentences.insert(insert_at, ins)
    return " ".join(sentences).strip()


def surface_pattern(text: str) -> str:
    """Build a bounded regex that tolerates RT spacing around hyphens."""
    parts: list[str] = []
    for char in text:
        if char.isspace():
            parts.append(r"\s+")
        elif char in "-–‒−":
            parts.append(r"\s*[–‒−-]\s*")
        else:
            parts.append(re.escape(char))
    return "".join(parts)


def wrap_word_boundaries(pattern: str, text: str) -> str:
    """Avoid matching bare-word replacements inside larger compounds."""
    starts_with_word = bool(re.match(r"[A-Za-zÄÖÕÜäöõüŠŽšž]", text))
    ends_with_word = bool(re.search(r"[A-Za-zÄÖÕÜäöõüŠŽšž]$", text))
    if not starts_with_word and not ends_with_word:
        return pattern
    wrapped = pattern
    if starts_with_word:
        wrapped = r"(?<![A-Za-zÄÖÕÜäöõüŠŽšž-])" + wrapped
    if ends_with_word:
        wrapped = wrapped + r"(?![A-Za-zÄÖÕÜäöõüŠŽšž-])"
    return wrapped


def case_preserved_replacement(
    match: re.Match[str],
    new: str,
    *,
    capitalize_sentence_start: bool = True,
    preserve_match_capital: bool = False,
) -> str:
    """Compute one case-preserved replacement string for a regex match."""
    matched = match.group(0)
    if matched.isupper() and new:
        return new.upper()
    if matched and matched[0].isupper() and new:
        if preserve_match_capital:
            return new[0].upper() + new[1:]
        if new[0].isupper():
            return new
        prefix = match.string[:match.start()].rstrip()
        if capitalize_sentence_start and (not prefix or prefix[-1] in '.!?("«'):
            return new[0].upper() + new[1:]
        return new
    return new


def replace_case_preserving(
    text: str,
    old: str,
    new: str,
    *,
    capitalize_sentence_start: bool = True,
    preserve_match_capital: bool = False,
) -> str:
    """Replace all occurrences of old with new, preserving sentence-case starts."""
    pattern = re.compile(
        wrap_word_boundaries(surface_pattern(old), old),
        re.IGNORECASE,
    )

    def _repl(match: re.Match[str]) -> str:
        return case_preserved_replacement(
            match,
            new,
            capitalize_sentence_start=capitalize_sentence_start,
            preserve_match_capital=preserve_match_capital,
        )

    return pattern.sub(_repl, text)


def sentence_indexes_from_notes(note_text: str) -> list[int]:
    """Extract targeted EE sentence indexes from amendment prose notes."""
    indexes: list[int] = []
    ordinal_patterns = [
        (r"esime(?:ne|se|ses|st|sest)", 0),
        (r"tei(?:ne|se|ses|st|sest)", 1),
        (r"kolma(?:s|st|ndat|ndas|ndast)", 2),
        (r"nelja(?:s|nda|ndat|ndas|ndast)", 3),
        (r"vii(?:es|enda|endat|endas|endast)", 4),
        (r"kuu(?:es|enda|endat|endas|endast)", 5),
        (r"seitsme(?:s|nda|ndat|ndas|ndast)", 6),
        (r"kaheks(?:as|anda|andat|andas|andast)", 7),
        (r"viima(?:ne|se|st|ses|sest)", 1_000_000),
    ]
    for (left_pat, left_idx), (right_pat, right_idx) in zip(ordinal_patterns, ordinal_patterns[1:]):
        if re.search(
            rf"\b{left_pat}\s+ja\s+{right_pat}\s+lause(?:t|s|st|ga)?\b",
            note_text,
        ):
            indexes.extend([left_idx, right_idx])
    for pattern, idx in ordinal_patterns:
        if re.search(rf"\b{pattern}\s+lause(?:t|s|st|ga)?\b", note_text):
            indexes.append(idx)
    return sorted(set(indexes))


def sentence_index_from_notes(note_text: str) -> int | None:
    """Extract the first targeted EE sentence index from amendment prose notes."""
    indexes = sentence_indexes_from_notes(note_text)
    return indexes[0] if indexes else None


__all__ = [
    "case_preserved_replacement",
    "insert_sentence_after",
    "insert_sentence_before",
    "replace_first_sentence",
    "replace_case_preserving",
    "replace_sentence",
    "sentence_index_from_notes",
    "sentence_indexes_from_notes",
    "surface_pattern",
    "split_ee_sentences",
    "wrap_word_boundaries",
]
