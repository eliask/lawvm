"""UK source-text classifiers used during lowering.

These helpers identify when source wording contradicts or refines broad effect
metadata. They return evidence only; the lowering caller owns the typed record
and any action reclassification.
"""

from __future__ import annotations

import re
from typing import Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.uk_grafter import _clean_num


def _word_level_structural_subsection_omission(
    *,
    effect_type: str,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Identify word-level feed rows whose source explicitly repeals a subsection."""
    effect_type_norm = (effect_type or "").strip().lower()
    if effect_type_norm not in {"words omitted", "word omitted", "words repealed", "word repealed"}:
        return None
    if _addr_leaf_kind(target) != "subsection":
        return None
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    match = re.search(
        r"\bomit\s+(?:the\s+)?subsection\s*\(\s*(?P<label>[0-9A-Za-z]+)\s*\)(?=\W|$)",
        text,
        flags=re.I,
    )
    if match is None:
        match = re.search(
            r"\bsubsection\s*\(\s*(?P<label>[0-9A-Za-z]+)\s*\)\s+is\s+"
            r"(?:omitted|repealed)\b",
            text,
            flags=re.I,
        )
    if match is None:
        return None
    source_label = _clean_num(str(match.group("label") or ""))
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if not source_label or source_label != target_label:
        return None
    return {
        "source_target_kind": "subsection",
        "source_target_label": source_label,
        "matched_instruction": match.group(0),
    }


def _empty_effect_type_as_if_words_omitted(text: str) -> bool:
    norm = " ".join(str(text or "").split()).lower()
    if not norm:
        return False
    return (
        "shall have effect" in norm
        and "as if" in norm
        and re.search(r"\bwords?\b", norm) is not None
        and re.search(r"\b(?:were|was)\s+omitted\b", norm) is not None
    )


_DOUBLE_QUOTED_FRAGMENT_RE = re.compile(r'["\u201c]([^"\u201d]+)["\u201d]')


def _quote_only_omission_payload_match(extracted_text: str) -> Optional[str]:
    """Return a quoted deletion fragment when the payload contains no instruction text."""
    normalized = " ".join((extracted_text or "").split())
    if not normalized:
        return None
    matches = list(_DOUBLE_QUOTED_FRAGMENT_RE.finditer(normalized))
    if len(matches) != 1:
        return None
    match = matches[0]
    residue = (normalized[: match.start()] + normalized[match.end() :]).strip()
    residue = re.sub(r"^(?:[ivxlcdm]+|[a-z]|\d+)[.)]?\s*", "", residue, flags=re.I)
    residue = residue.strip(" \t\r\n,;.")
    if residue.lower() not in {"", "and", "or"}:
        return None
    fragment = " ".join(match.group(1).split()).strip()
    return fragment or None
