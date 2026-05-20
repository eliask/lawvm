"""Definition-anchor normalization helpers for UK replay."""
from __future__ import annotations


def _uk_definition_term_lexical_variants(term: str) -> tuple[str, ...]:
    """Return narrow UK definition-anchor lexical variants.

    This is target-resolution recovery, not fuzzy matching. Keep the family
    deliberately small: UK sources can use the adjectival form "educational"
    where the enacted definition label uses the noun "education".
    """
    cleaned = " ".join(str(term or "").split())
    if not cleaned:
        return ()
    variants: list[str] = []
    words = cleaned.split(" ")
    for i, word in enumerate(words):
        lower = word.lower()
        if lower == "educational":
            variant_words = list(words)
            variant_words[i] = "education"
            variants.append(" ".join(variant_words))
        elif lower == "education":
            variant_words = list(words)
            variant_words[i] = "educational"
            variants.append(" ".join(variant_words))
    return tuple(dict.fromkeys(variant for variant in variants if variant != cleaned))
