from __future__ import annotations

import re
from typing import cast

from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic


_ROMAN_CHILD_LABEL_RE = re.compile(
    r"(?P<prefix>^|(?:[;]\s*(?:and|or)?\s+)|(?:\b(?:and|or)\s+))"
    r"(?P<label>viii|vii|vi|iv|iii|ii|ix|x|v|i)\s+",
    flags=re.I,
)


def _source_carried_labeled_child_replacement_parts(
    replacement: str,
    *,
    parent_kind: str,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Split a source-carried flat child run like ``i ...; or ii ...``.

    This is intentionally narrow: it only recognizes a consecutive roman
    child run starting at ``i`` under a paragraph-like parent.  The source
    witness is the visible child labels in the amendment payload, not oracle
    shape or live-state guessing.
    """
    parent_kind_norm = str(parent_kind or "").lower()
    if parent_kind_norm not in {"paragraph", "subparagraph", "item"}:
        return "", ()
    text = " ".join(str(replacement or "").split()).strip()
    if not text:
        return "", ()
    matches = list(_ROMAN_CHILD_LABEL_RE.finditer(text))
    if len(matches) < 2 or matches[0].start() != 0:
        return "", ()
    labels = tuple(match.group("label").lower() for match in matches)
    label_ordinals = tuple(_shared_roman_to_arabic(label) for label in labels)
    if any(value is None for value in label_ordinals):
        return "", ()
    ordinals = tuple(cast(int, value) for value in label_ordinals)
    if ordinals != tuple(range(1, len(ordinals) + 1)):
        return "", ()

    if parent_kind_norm == "paragraph":
        child_kind = "subparagraph"
    elif parent_kind_norm == "subparagraph":
        child_kind = "item"
    else:
        child_kind = "point"

    parts: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start("label") if index + 1 < len(matches) else len(text)
        body = " ".join(text[start:end].split()).strip()
        if not body:
            return "", ()
        parts.append((labels[index], body))
    return child_kind, tuple(parts)
