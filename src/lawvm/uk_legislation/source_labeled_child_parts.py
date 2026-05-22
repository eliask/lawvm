from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic


_ROMAN_CHILD_LABEL_RE = re.compile(
    r"(?P<prefix>^|(?:[;]\s*(?:and|or)?\s+)|(?:\b(?:and|or)\s+)|(?:[-–—]\s*))"
    r"(?P<label>viii|vii|vi|iv|iii|ii|ix|x|v|i)\s+",
    flags=re.I,
)


@dataclass(frozen=True)
class SourceCarriedLabeledChildReplacement:
    child_kind: str
    parent_prefix: str
    parts: tuple[tuple[str, str], ...]


def _source_carried_labeled_child_replacement_shape(
    replacement: str,
    *,
    parent_kind: str,
) -> SourceCarriedLabeledChildReplacement:
    """Split a source-carried flat child run like ``i ...; or ii ...``.

    This is intentionally narrow: it only recognizes a consecutive roman
    child run starting at ``i`` under a paragraph-like parent, optionally after
    a source-carried parent prefix ending in a dash.  The source witness is the
    visible child labels in the amendment payload, not oracle shape or
    live-state guessing.
    """
    parent_kind_norm = str(parent_kind or "").lower()
    if parent_kind_norm not in {"paragraph", "subparagraph", "item"}:
        return SourceCarriedLabeledChildReplacement("", "", ())
    text = " ".join(str(replacement or "").split()).strip()
    if not text:
        return SourceCarriedLabeledChildReplacement("", "", ())
    matches = list(_ROMAN_CHILD_LABEL_RE.finditer(text))
    if len(matches) < 2:
        return SourceCarriedLabeledChildReplacement("", "", ())
    parent_prefix = ""
    first_label_start = matches[0].start("label")
    if first_label_start != 0:
        prefix = text[:first_label_start].strip()
        if not re.search(r"(?:-|--|–|—)\s*$", prefix):
            return SourceCarriedLabeledChildReplacement("", "", ())
        parent_prefix = re.sub(r"\s*(?:-|--|–|—)\s*$", "", prefix).strip()
        if not parent_prefix:
            return SourceCarriedLabeledChildReplacement("", "", ())
    labels = tuple(match.group("label").lower() for match in matches)
    label_ordinals = tuple(_shared_roman_to_arabic(label) for label in labels)
    if any(value is None for value in label_ordinals):
        return SourceCarriedLabeledChildReplacement("", "", ())
    ordinals = tuple(cast(int, value) for value in label_ordinals)
    if ordinals != tuple(range(1, len(ordinals) + 1)):
        return SourceCarriedLabeledChildReplacement("", "", ())

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
            return SourceCarriedLabeledChildReplacement("", "", ())
        parts.append((labels[index], body))
    return SourceCarriedLabeledChildReplacement(child_kind, parent_prefix, tuple(parts))


def _source_carried_labeled_child_replacement_parts(
    replacement: str,
    *,
    parent_kind: str,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    shape = _source_carried_labeled_child_replacement_shape(
        replacement,
        parent_kind=parent_kind,
    )
    return shape.child_kind, shape.parts
