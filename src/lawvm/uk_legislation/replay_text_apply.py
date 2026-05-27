from __future__ import annotations

import re
from dataclasses import replace as dc_replace
from typing import Any, Callable, NamedTuple, Optional, TypeAlias

from lawvm.uk_legislation.definition_anchors import _uk_definition_term_lexical_variants
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.nlp_parser import US
from lawvm.uk_legislation.replay_text import _append_definition_child_suffix_text, _range_anchor_matches
from lawvm.uk_legislation.text_matching import (
    _numeric_list_trailing_comma_replacement_text,
    _numeric_list_trailing_comma_subtree_replacement,
    _normalize_text,
    _text_patch_pattern,
)
from lawvm.uk_legislation.uk_grafter import _clean_num


TextNodePath: TypeAlias = tuple[int, ...]
DocumentTextNode: TypeAlias = tuple[TextNodePath, UKMutableNode]
TextNodeRewriteCandidate: TypeAlias = tuple[TextNodePath, UKMutableNode, str]
TextNodeRewriteMetadataCandidate: TypeAlias = tuple[TextNodePath, UKMutableNode, str, Any]
TextNodeRegexMatch: TypeAlias = tuple[TextNodePath, UKMutableNode, re.Match[str]]
TextNodeExactMatch: TypeAlias = tuple[TextNodePath, UKMutableNode, int]
TextNodeRegexMatchesByPath: TypeAlias = dict[
    TextNodePath,
    tuple[UKMutableNode, list[re.Match[str]]],
]


class DefinitionTextRewriteResult(NamedTuple):
    text: str
    applied: bool
    recovery_rule_ids: tuple[str, ...]


class NumericListTrailingCommaApplyResult(NamedTuple):
    node: UKMutableNode
    applied: bool
    anchor: str | None


class DefinitionChildTailSelectorParts(NamedTuple):
    term: str
    child_label: str
    anchor: str


class TextRangeStartIndex(NamedTuple):
    start_index: int
    recovery_rule_ids: tuple[str, ...]


_UK_DEFINITION_PREDICATE_PATTERN = r"""
means
|have\s+the\s+same\s+meaning\s+as
|has\s+the\s+same\s+meaning\s+as
|have\s+the\s+meaning
|has\s+the\s+meaning
|are\s+to\s+be\s+construed
|is\s+to\s+be\s+construed
|shall\s+be\s+construed
|includes
"""

_UK_DEFINITION_PREDICATE_PATTERN_WITHOUT_SHALL = r"""
means
|have\s+the\s+same\s+meaning\s+as
|has\s+the\s+same\s+meaning\s+as
|have\s+the\s+meaning
|has\s+the\s+meaning
|are\s+to\s+be\s+construed
|is\s+to\s+be\s+construed
|includes
"""

_UK_NEXT_DEFINITION_PATTERN = re.compile(
    rf"""
    [;\.,]\s*
    [“"'\u2018][^”"'\u2019;]{{1,160}}[”"'\u2019]
    (?:\s*\([^;]*?\))*
    (?:\s+(?:and|or)\s+[“"'\u2018][^”"'\u2019;]{{1,160}}[”"'\u2019])*
    \s+
    (?:{_UK_DEFINITION_PREDICATE_PATTERN})\b
    """,
    flags=re.I | re.S | re.X,
)

_UK_NEXT_DEFINITION_PATTERN_WITHOUT_SHALL = re.compile(
    rf"""
    [;\.]\s*
    [“"'\u2018][^”"'\u2019;]{{1,160}}[”"'\u2019]
    (?:\s+(?:and|or)\s+[“"'\u2018][^”"'\u2019;]{{1,160}}[”"'\u2019])*
    \s+
    (?:{_UK_DEFINITION_PREDICATE_PATTERN_WITHOUT_SHALL})\b
    """,
    flags=re.I | re.S | re.X,
)

_UK_FEE_SUM_PATTERN = re.compile(
    r"£\d+(?:,\d{3})*(?:[\.·]\d{2})?\b"
    r"|\bNIL\b"
    r"|\bsixpence\b"
    r"|\btwo\s+pence\b"
    r"|\bone\s+shilling\b"
    r"|\bthree\s+pounds\b"
    r"|\bfive\s+shillings\b"
    r"|\btwenty\s+shillings\b"
    r"|\btwo\s+shillings\s+and\s+sixpence\b"
    r"|\bten\s+shillings\b",
    flags=re.I,
)
_UK_AMENDMENT_PROGRAM_NEXT_LINE_LABEL_PATTERN = re.compile(
    r"(?m)(?:^|\n)[ \t]*(?:[0-9]+[A-Za-z]?|[A-Za-z]{1,4})[ \t]*(?=\n|$)"
)
_UK_AMENDMENT_PROGRAM_INSERTED_PARENT_PATTERN = re.compile(
    r"TEXT_AMENDMENT_PROGRAM_INSERTED_PARENT_([0-9A-Za-z]+)_(BEFORE|AFTER)_([0-9A-Za-z]+)"
)
_UK_BRACKETED_TEXT_PATTERN = re.compile(r"\([^()]*\)")


def _uk_amendment_program_line_label_matches(text: str, label: str) -> list[re.Match[str]]:
    label_pattern = re.escape(str(label or "").strip().strip("()").lower().strip("."))
    if not label_pattern:
        return []
    return list(
        re.finditer(
            rf"(?m)(?:^|\n)(?P<prefix>[ \t]*){label_pattern}[ \t]*(?=\n|$)",
            text,
            flags=re.I,
        )
    )


def _uk_amendment_program_next_line_label_match(
    text: str,
    *,
    start: int,
) -> re.Match[str] | None:
    for match in _UK_AMENDMENT_PROGRAM_NEXT_LINE_LABEL_PATTERN.finditer(text, pos=start):
        return match
    return None


def _uk_apply_amendment_program_inserted_parent_child_insert(
    text: str,
    *,
    inserted_parent_label: str,
    direction: str,
    anchor_label: str,
    replacement: str,
) -> str | None:
    parent_matches = _uk_amendment_program_line_label_matches(text, inserted_parent_label)
    if len(parent_matches) != 1:
        return None
    anchor_matches = [
        match
        for match in _uk_amendment_program_line_label_matches(text, anchor_label)
        if match.start() > parent_matches[0].end()
    ]
    if len(anchor_matches) != 1:
        return None
    anchor_match = anchor_matches[0]
    if direction == "before":
        insert_at = anchor_match.start()
    elif direction == "after":
        next_label = _uk_amendment_program_next_line_label_match(
            text,
            start=anchor_match.end(),
        )
        insert_at = next_label.start() if next_label is not None else len(text)
    else:
        return None
    insertion = "\n\n" + " ".join(replacement.split()).strip() + "\n\n"
    return f"{text[:insert_at].rstrip()}{insertion}{text[insert_at:].lstrip()}"


def _definition_term_pattern(
    term: str,
    *,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> str:
    return _text_patch_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )


def _compile_definition_entry_start_pattern(
    term: str,
    *,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
    prefix_pattern: str = r"(?:^|[;\.,\u2014\u2013-]\s*)",
    predicate_pattern: str = _UK_DEFINITION_PREDICATE_PATTERN,
) -> re.Pattern[str]:
    term_pattern = _definition_term_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    return re.compile(
        rf"""
        (?P<prefix>{prefix_pattern})
        [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
        (?:\s*\([^;]*?\))*
        (?:\s+(?:and|or)\s+[“"'\u2018][^”"'\u2019;]{{1,160}}[”"'\u2019])*
        \s+
        (?:{predicate_pattern})\b
        """,
        flags=re.I | re.S | re.X,
    )


def _compile_definition_entry_range_pattern(
    term: str,
    *,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
    prefix_pattern: str = r"(?:^|[;\.]\s*)",
    predicate_pattern: str = _UK_DEFINITION_PREDICATE_PATTERN,
) -> re.Pattern[str]:
    term_pattern = _definition_term_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    return re.compile(
        rf"""
        (?P<prefix>{prefix_pattern})
        [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
        (?:\s+(?:and|or)\s+[“"'\u2018][^”"'\u2019;]{{1,160}}[”"'\u2019])*
        \s+
        (?:{predicate_pattern})\b
        .*?
        (?P<terminator>;|$)
        """,
        flags=re.I | re.S | re.X,
    )


def _flat_definition_child_bounds(
    text: str,
    *,
    term: str,
    child_label: str,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[int, int, int, list[re.Match[str]]] | None:
    ordinal = _child_ordinal(child_label)
    if ordinal is None or ordinal < 1:
        return None
    definition_start_pattern = _compile_definition_entry_start_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
        prefix_pattern=r"(?:^|[;\.]\s*)",
        predicate_pattern=_UK_DEFINITION_PREDICATE_PATTERN_WITHOUT_SHALL,
    )
    definition_starts = list(definition_start_pattern.finditer(text))
    if len(definition_starts) != 1:
        return None
    definition_start = definition_starts[0]
    next_definition = _UK_NEXT_DEFINITION_PATTERN_WITHOUT_SHALL.search(
        text,
        definition_start.end(),
    )
    entry_end = next_definition.start() + 1 if next_definition is not None else len(text)
    body_start = definition_start.end()
    entry_body = text[body_start:entry_end]
    semicolons = list(re.finditer(r";", entry_body))
    if len(semicolons) < ordinal:
        return None
    return ordinal, body_start, entry_end, semicolons


def _definition_child_insert_payload(
    raw_replacement: str,
    *,
    term: str,
) -> tuple[str, list[UKMutableNode]]:
    text = " ".join(raw_replacement.split()).strip(" ,.")
    if not text:
        return "", []
    anchor_suffix = ""
    suffix_match = re.match(r"^(?P<suffix>;\s+(?:or|and))\s+(?P<body>.+)$", text, flags=re.I | re.S)
    if suffix_match is not None:
        anchor_suffix = " ".join(suffix_match.group("suffix").split())
        text = suffix_match.group("body").strip()
    item_matches = list(
        re.finditer(
            r"(?:(?<=^)|(?<=;\s))(?P<label>[0-9A-Za-z]+)\s+(?P<body>[^;]+;)",
            text,
            flags=re.S,
        )
    )
    items: list[UKMutableNode] = []
    if item_matches and item_matches[0].start() == 0:
        for item_match in item_matches:
            label = item_match.group("label").strip()
            item_text = " ".join(item_match.group("body").split()).strip()
            if not label or not item_text:
                return "", []
            items.append(
                UKMutableNode(
                    kind=IRNodeKind.ITEM,
                    label=None,
                    text=item_text,
                    attrs={
                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                        "definition_term": term,
                        "definition_child_label": label,
                        "source_rule_detail": "uk_effect_source_carried_definition_child_insert_text_patch",
                    },
                )
            )
    return anchor_suffix, items


def _insert_after_definition_text(
    text: str,
    *,
    term: str,
    replacement: str,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> DefinitionTextRewriteResult:
    definition_start: re.Match[str] | None = None
    recovered_anchor = False
    recovered_parenthetical_translation = False
    recovered_qualifier_phrase = False
    recovered_conjoined_term = False
    for candidate_term in (term, *_uk_definition_term_lexical_variants(term)):
        term_pattern = _definition_term_pattern(
            candidate_term,
            allow_punctuation_spacing=allow_punctuation_spacing,
            allow_word_punctuation_elision=allow_word_punctuation_elision,
        )
        definition_start_pattern = re.compile(
            rf"""
            (?P<prefix>(?:^|[;\.,\u2014\u2013-]\s*|(?:\band\b|\bor\b)\s+))
            [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
            (?P<parenthetical_translation>(?:\s*\([^;]*?\))*)
            (?P<qualifier>\s*,\s*[^;]{{1,240}}?\s*,)?
            \s+
            (?:{_UK_DEFINITION_PREDICATE_PATTERN})\b
            """,
            flags=re.I | re.S | re.X,
        )
        definition_starts = list(definition_start_pattern.finditer(text))
        if len(definition_starts) != 1:
            continue
        definition_start = definition_starts[0]
        recovered_anchor = candidate_term != term
        recovered_parenthetical_translation = bool(
            str(definition_start.group("parenthetical_translation") or "").strip()
        )
        recovered_qualifier_phrase = bool(str(definition_start.group("qualifier") or "").strip())
        recovered_conjoined_term = bool(
            re.fullmatch(
                r"\s*(?:and|or)\s+",
                str(definition_start.group("prefix") or ""),
                flags=re.I,
            )
        )
        break
    if definition_start is None:
        return DefinitionTextRewriteResult(text, False, ())
    next_definition = _UK_NEXT_DEFINITION_PATTERN.search(text, definition_start.end())
    if next_definition is not None:
        insert_at = next_definition.start() + 1
    else:
        insert_at = len(text)
    joiner = "" if replacement.startswith((" ", ",", ".", ";", ":", ")")) else " "
    new_text = f"{text[:insert_at]}{joiner}{replacement}{text[insert_at:]}"
    recovery_rule_ids = []
    if recovered_anchor:
        recovery_rule_ids.append("uk_replay_definition_anchor_lexical_variant_recovered")
    if recovered_parenthetical_translation:
        recovery_rule_ids.append("uk_replay_definition_anchor_parenthetical_translation_normalized")
    if recovered_qualifier_phrase:
        recovery_rule_ids.append("uk_replay_definition_anchor_qualifier_phrase_normalized")
    if recovered_conjoined_term:
        recovery_rule_ids.append("uk_replay_definition_anchor_conjoined_term_normalized")
    recovery_rule_ids.append("uk_replay_after_definition_text_insert_applied")
    return DefinitionTextRewriteResult(
        " ".join(new_text.split()).strip(),
        True,
        tuple(recovery_rule_ids),
    )


def _rewrite_flat_definition_child_ordinal_text(
    text: str,
    *,
    term: str,
    child_label: str,
    replacement: str,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[str, bool]:
    bounds = _flat_definition_child_bounds(
        text,
        term=term,
        child_label=child_label,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    if bounds is None:
        return text, False
    ordinal, body_start, _entry_end, semicolons = bounds
    segment_start = body_start
    if ordinal > 1:
        segment_start = body_start + semicolons[ordinal - 2].end()
    segment_end = body_start + semicolons[ordinal - 1].end()
    before = text[:segment_start].rstrip()
    after = text[segment_end:].lstrip()
    if replacement:
        old_segment = text[segment_start:segment_end]
        terminator = (
            ";" if old_segment.rstrip().endswith(";") and not replacement.rstrip().endswith(";") else ""
        )
        new_segment = f"{replacement.strip()}{terminator}"
        new_text = f"{before} {new_segment} {after}".strip()
    else:
        new_text = f"{before} {after}".strip()
    return " ".join(new_text.split()).strip(), True


def _rewrite_flat_definition_child_inner_text(
    text: str,
    *,
    term: str,
    child_label: str,
    pattern: str,
    replacement_text: str,
    child_after_anchor: bool,
    child_at_end: bool,
    occurrence: int,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[str, bool]:
    bounds = _flat_definition_child_bounds(
        text,
        term=term,
        child_label=child_label,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    if bounds is None:
        return text, False
    ordinal, body_start, entry_end, semicolons = bounds
    segment_start = body_start
    if ordinal > 1:
        segment_start = body_start + semicolons[ordinal - 2].end()
    search_end = (
        body_start + semicolons[ordinal - 1].end()
        if child_after_anchor
        else body_start + semicolons[ordinal].end()
        if len(semicolons) > ordinal
        else entry_end
    )
    segment = text[segment_start:search_end]
    if child_at_end:
        new_segment = _append_definition_child_suffix_text(segment, replacement_text)
        new_text = f"{text[:segment_start]}{new_segment}{text[search_end:]}"
        return " ".join(new_text.split()).strip(), True
    matches = list(re.finditer(pattern, segment, flags=re.I | re.S))
    if child_after_anchor:
        required_occurrence = occurrence if occurrence > 0 else 1
        if len(matches) < required_occurrence:
            return text, False
        match_obj = matches[required_occurrence - 1]
    else:
        if len(matches) != 1:
            return text, False
        match_obj = matches[0]
    absolute_start = segment_start + match_obj.start()
    absolute_end = segment_start + match_obj.end()
    new_text = f"{text[:absolute_start]}{replacement_text}{text[absolute_end:]}"
    return " ".join(new_text.split()).strip(), True


def _node_at_path(n: UKMutableNode, path: TextNodePath) -> UKMutableNode:
    current = n
    for index in path:
        current = current.children[index]
    return current


def _find_descendant_path_by_kind_label(
    node: UKMutableNode,
    *,
    kind: str,
    label: str,
) -> TextNodePath | None:
    stack: list[DocumentTextNode] = [((), node)]
    while stack:
        path, current = stack.pop()
        kind_value = current.kind.value if isinstance(current.kind, IRNodeKind) else str(current.kind)
        if kind_value == kind and _clean_num(current.label or "") == _clean_num(label):
            return path
        for index in range(len(current.children) - 1, -1, -1):
            stack.append((path + (index,), current.children[index]))
    return None


def _collect_descendant_paths_by_label_and_kinds(
    node: UKMutableNode,
    *,
    label: str,
    allowed_kinds: set[str],
) -> list[TextNodePath]:
    matches: list[TextNodePath] = []
    stack: list[DocumentTextNode] = [((), node)]
    while stack:
        path, current = stack.pop()
        kind_value = current.kind.value if isinstance(current.kind, IRNodeKind) else str(current.kind)
        if kind_value in allowed_kinds and _clean_num(current.label or "") == _clean_num(label):
            matches.append(path)
        for index in range(len(current.children) - 1, -1, -1):
            stack.append((path + (index,), current.children[index]))
    return matches


def _definition_child_nodes(
    n: UKMutableNode,
    *,
    term: str,
    child_label: str,
    path: TextNodePath = (),
) -> list[DocumentTextNode]:
    matches: list[DocumentTextNode] = []
    normalized_term = _normalize_text(term)
    normalized_label = child_label.lower()
    for index, child in enumerate(n.children):
        child_path = path + (index,)
        if (
            child.kind is IRNodeKind.ITEM
            and str(child.attrs.get("definition_child_label") or "").lower() == normalized_label
            and _normalize_text(str(child.attrs.get("definition_term") or "")) == normalized_term
        ):
            matches.append((child_path, child))
        matches.extend(
            _definition_child_nodes(
                child,
                term=term,
                child_label=child_label,
                path=child_path,
            )
        )
    return matches


def _child_ordinal(label: str) -> Optional[int]:
    if len(label) == 1 and label.isalpha():
        return ord(label.lower()) - ord("a") + 1
    if label.isdigit():
        return int(label)
    return None


def _text_nodes_in_document_order(
    n: UKMutableNode,
    path: TextNodePath = (),
) -> list[DocumentTextNode]:
    text_nodes: list[DocumentTextNode] = []
    if n.text:
        text_nodes.append((path, n))
    for index, child in enumerate(n.children):
        text_nodes.extend(_text_nodes_in_document_order(child, path + (index,)))
    return text_nodes


def _rewrite_definition_entry_text(
    text: str,
    *,
    term: str,
    replacement: str,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> DefinitionTextRewriteResult:
    term_pattern = _text_patch_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    definition_pattern = re.compile(
        rf"""
        (?P<prefix>(?:^|[;\.:\u2014]\s*,?\s*))
        \s*
        [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
        (?:\s*\([^;]*?\))*
        (?:\s+(?:and|or)\s+[“"'\u2018][^”"'\u2019;]{{1,160}}[”"'\u2019])*
        (?P<qualifier>\s*,\s*[^;]{{1,240}}?\s*,)?
        \s+
        (?P<predicate>
            means
            |have\s+the\s+same\s+meaning\s+as
            |has\s+the\s+same\s+meaning\s+as
            |have\s+the\s+meaning
            |has\s+the\s+meaning
            |are\s+to\s+be\s+construed
            |is\s+to\s+be\s+construed
            |shall\s+be\s+construed
            |includes
        )\b
        .*?
        (?P<terminator>;|$)
        """,
        flags=re.I | re.S | re.X,
    )
    matches = list(definition_pattern.finditer(text))
    if len(matches) != 1:
        return DefinitionTextRewriteResult(text, False, ())
    match = matches[0]
    predicate = " ".join(str(match.group("predicate") or "").lower().split())
    used_shall_construed = predicate == "shall be construed"
    used_qualifier = bool(str(match.group("qualifier") or "").strip())
    raw_prefix = match.group("prefix")
    used_orphan_separator = bool(re.search(r"[;\.:\u2014]\s*,\s*$", raw_prefix))
    prefix = re.sub(r"\s*,\s*$", " ", raw_prefix) if used_orphan_separator else raw_prefix
    if replacement:
        replacement_prefix = "" if match.start() == 0 else prefix
        joiner = (
            ""
            if not replacement_prefix or replacement.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        new_text = (
            f"{text[: match.start()]}{replacement_prefix}{joiner}"
            f"{replacement}{text[match.end():]}"
        )
    else:
        replacement_prefix = "" if match.start() == 0 or prefix.strip() == "." else prefix
        new_text = f"{text[: match.start()]}{replacement_prefix}{text[match.end():]}"
    recovery_rule_ids = []
    if used_shall_construed:
        recovery_rule_ids.append("uk_replay_definition_predicate_shall_construed_normalized")
    if used_qualifier:
        recovery_rule_ids.append("uk_replay_definition_entry_qualifier_phrase_normalized")
    if used_orphan_separator:
        recovery_rule_ids.append("uk_replay_definition_entry_orphan_separator_normalized")
    return DefinitionTextRewriteResult(
        " ".join(new_text.split()).strip(),
        True,
        tuple(recovery_rule_ids),
    )


def _remove_trailing_context_word(text: str, needle: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"(?P<prefix>.*?)(?P<sep>\s*,?\s*){re.escape(needle)}(?P<suffix>\s*[,;:]?\s*)$",
        re.I | re.S,
    )
    match = pattern.fullmatch(text)
    if not match:
        return text, False
    return (match.group("prefix").rstrip() + match.group("suffix").rstrip()).rstrip(), True


def _delete_ordinal_sentence(text: str, ordinal: int) -> tuple[str, bool]:
    if ordinal < 1:
        return text, False
    spans = list(
        re.finditer(
            r"(?P<body>.*?(?:[.!?]+(?:[\"”’')\]]+)?))(?=\s+|$)",
            text,
            flags=re.S,
        )
    )
    if len(spans) < ordinal:
        return text, False
    span = spans[ordinal - 1].span()
    new_text = f"{text[: span[0]]}{text[span[1] :]}"
    return " ".join(new_text.split()).strip(), True


def _delete_ordinal_sentence_beginning(
    text: str,
    *,
    ordinal: int,
    beginning: str,
) -> tuple[str, bool]:
    if ordinal < 1 or not beginning:
        return text, False
    spans = list(
        re.finditer(
            r"(?P<body>.*?(?:[.!?]+(?:[\"”’')\]]+)?))(?=\s+|$)",
            text,
            flags=re.S,
        )
    )
    if len(spans) < ordinal:
        return text, False
    sentence = spans[ordinal - 1].group("body")
    if not _normalize_text(sentence).startswith(_normalize_text(beginning)):
        return text, False
    span = spans[ordinal - 1].span()
    new_text = f"{text[: span[0]]}{text[span[1] :]}"
    return " ".join(new_text.split()).strip(), True


def _delete_source_carried_child_text(
    text: str,
    *,
    original: str,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[str, bool]:
    if original in text:
        return text.replace(original, ""), True
    pattern = _text_patch_pattern(
        original,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    new_text, count = re.subn(pattern, "", text, flags=re.I | re.S)
    return new_text, count > 0


def _insert_at_end_of_definition_text(
    text: str,
    *,
    term: str,
    replacement: str,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[str, bool]:
    definition_start_pattern = _compile_definition_entry_start_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
        prefix_pattern=r"(?:^|[;\.,\u2014\u2013-]\s*)",
    )
    starts = list(definition_start_pattern.finditer(text))
    if len(starts) != 1:
        return text, False
    definition_start = starts[0]
    next_definition = _UK_NEXT_DEFINITION_PATTERN.search(text, definition_start.end())
    if next_definition is not None:
        insert_at = next_definition.start()
    else:
        terminal = re.search(r"\s*[;,.]\s*$", text)
        insert_at = terminal.start() if terminal is not None else len(text)
    joiner = (
        ""
        if insert_at == 0
        or text[:insert_at].endswith((" ", "\t", "\n", "\r"))
        or replacement.startswith((" ", ",", ".", ";", ":", ")"))
        else " "
    )
    new_text = f"{text[:insert_at]}{joiner}{replacement}{text[insert_at:]}"
    return " ".join(new_text.split()).strip(), True


def _rewrite_definition_range_to_end_text(
    text: str,
    *,
    term: str,
    start_anchor: str,
    replacement: str,
    occurrence: int,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[str, bool]:
    definition_pattern = _compile_definition_entry_range_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    definition_matches = list(definition_pattern.finditer(text))
    if len(definition_matches) != 1:
        return text, False
    definition_match = definition_matches[0]
    entry_text = definition_match.group(0)
    start_pattern = _text_patch_pattern(
        start_anchor,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    start_matches = list(re.finditer(start_pattern, entry_text, flags=re.I | re.S))
    start_ordinal = occurrence if occurrence > 0 else 1
    if len(start_matches) < start_ordinal:
        return text, False
    if occurrence <= 0 and len(start_matches) != 1:
        return text, False
    start_match = start_matches[start_ordinal - 1]
    terminator = str(definition_match.group("terminator") or "")
    replacement_text = replacement.strip()
    if terminator and not replacement_text.endswith(terminator):
        replacement_text = f"{replacement_text}{terminator}"
    rewritten_entry = entry_text[: start_match.start()].rstrip()
    joiner = (
        ""
        if not rewritten_entry
        or rewritten_entry.endswith((" ", "\t", "\n", "\r"))
        or replacement_text.startswith((" ", ",", ".", ";", ":", ")"))
        else " "
    )
    rewritten_entry = f"{rewritten_entry}{joiner}{replacement_text}"
    new_text = text[: definition_match.start()] + rewritten_entry + text[definition_match.end() :]
    return " ".join(new_text.split()).strip(), True


def _rewrite_definition_range_text(
    text: str,
    *,
    term: str,
    start_anchor: str,
    end_anchor: str,
    replacement: str,
    occurrence: int,
    end_occurrence: int,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[str, bool]:
    definition_pattern = _compile_definition_entry_range_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    definition_matches = list(definition_pattern.finditer(text))
    if len(definition_matches) != 1:
        return text, False
    definition_match = definition_matches[0]
    entry_text = definition_match.group(0)
    start_pattern = _text_patch_pattern(
        start_anchor,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    start_ordinal = occurrence if occurrence > 0 else 1
    start_matches = list(re.finditer(start_pattern, entry_text, flags=re.I | re.S))
    if len(start_matches) < start_ordinal:
        return text, False
    start_match = start_matches[start_ordinal - 1]
    end_pattern = _text_patch_pattern(
        end_anchor,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    end_matches = [
        candidate
        for candidate in re.finditer(end_pattern, entry_text, flags=re.I | re.S)
        if candidate.start() >= start_match.end()
    ]
    end_ordinal = end_occurrence if end_occurrence > 0 else 1
    if len(end_matches) < end_ordinal:
        return text, False
    end_match = end_matches[end_ordinal - 1]
    rewritten_entry = (
        entry_text[: start_match.start()] + replacement + entry_text[end_match.end() :]
    )
    new_text = text[: definition_match.start()] + rewritten_entry + text[definition_match.end() :]
    return " ".join(new_text.split()).strip(), True


def _rewrite_each_anchor_in_definition_entry_text(
    text: str,
    *,
    term: str,
    anchor: str,
    replacement: str,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[str, bool]:
    definition_pattern = _compile_definition_entry_range_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
        predicate_pattern=_UK_DEFINITION_PREDICATE_PATTERN_WITHOUT_SHALL,
    )
    definition_matches = list(definition_pattern.finditer(text))
    if len(definition_matches) != 1:
        return text, False
    definition_match = definition_matches[0]
    entry_text = definition_match.group(0)
    anchor_pattern = _text_patch_pattern(
        anchor,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    anchor_matches = list(re.finditer(anchor_pattern, entry_text, flags=re.I | re.S))
    if not anchor_matches:
        return text, False
    rewritten_entry = re.sub(anchor_pattern, replacement, entry_text, flags=re.I | re.S)
    new_text = f"{text[:definition_match.start()]}{rewritten_entry}{text[definition_match.end():]}"
    return " ".join(new_text.split()).strip(), True


def _rewrite_anchor_in_definition_entry_text(
    text: str,
    *,
    term: str,
    anchor: str,
    replacement: str,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[str, bool]:
    definition_pattern = _compile_definition_entry_range_pattern(
        term,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
        predicate_pattern=_UK_DEFINITION_PREDICATE_PATTERN_WITHOUT_SHALL,
    )
    definition_matches = list(definition_pattern.finditer(text))
    if len(definition_matches) != 1:
        return text, False
    definition_match = definition_matches[0]
    entry_text = definition_match.group(0)
    anchor_pattern = _text_patch_pattern(
        anchor,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    anchor_matches = list(re.finditer(anchor_pattern, entry_text, flags=re.I | re.S))
    if len(anchor_matches) != 1:
        return text, False
    anchor_match = anchor_matches[0]
    rewritten_entry = (
        entry_text[: anchor_match.start()] + replacement + entry_text[anchor_match.end() :]
    )
    new_text = f"{text[:definition_match.start()]}{rewritten_entry}{text[definition_match.end():]}"
    return " ".join(new_text.split()).strip(), True


def _rewrite_after_anchor_to_end_text(
    text: str,
    *,
    anchor: str,
    replacement: str,
    occurrence: int,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> tuple[str, bool]:
    ordinal = occurrence if occurrence > 0 else 1
    start = 0
    for _ in range(ordinal):
        idx = text.find(anchor, start)
        if idx == -1:
            break
        start = idx + len(anchor)
    else:
        anchor_end = idx + len(anchor)
        joiner = (
            ""
            if text[:anchor_end].endswith((" ", "\t", "\n", "\r"))
            or replacement.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        return " ".join(f"{text[:anchor_end]}{joiner}{replacement}".split()).strip(), True

    pattern = _text_patch_pattern(
        anchor,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    matches = list(re.finditer(pattern, text, flags=re.I | re.S))
    if len(matches) < ordinal:
        return text, False
    anchor_match = matches[ordinal - 1]
    joiner = (
        ""
        if text[: anchor_match.end()].endswith((" ", "\t", "\n", "\r"))
        or replacement.startswith((" ", ",", ".", ";", ":", ")"))
        else " "
    )
    return " ".join(f"{text[: anchor_match.end()]}{joiner}{replacement}".split()).strip(), True


def _rewrite_definition_child_tail_after_anchor_to_end_text(
    text: str,
    *,
    term: str,
    child_label: str,
    anchor: str,
    replacement: str,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> DefinitionTextRewriteResult:
    bounds = _flat_definition_child_bounds(
        text,
        term=term,
        child_label=child_label,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    fallback_recovery_rule_ids: tuple[str, ...] = ()
    if bounds is None:
        definition_pattern = _compile_definition_entry_range_pattern(
            term,
            allow_punctuation_spacing=allow_punctuation_spacing,
            allow_word_punctuation_elision=allow_word_punctuation_elision,
            prefix_pattern=r"(?:^|[;\.]\s*)",
            predicate_pattern=_UK_DEFINITION_PREDICATE_PATTERN_WITHOUT_SHALL,
        )
        definition_matches = list(definition_pattern.finditer(text))
        if len(definition_matches) != 1:
            return DefinitionTextRewriteResult(text, False, ())
        definition_match = definition_matches[0]
        tail_start = definition_match.end(0) - len(definition_match.group(0))
        entry_end = definition_match.end()
        fallback_recovery_rule_ids = (
            "uk_replay_definition_child_tail_flat_child_boundary_unavailable_anchor_unique",
        )
    else:
        ordinal, body_start, entry_end, semicolons = bounds
        tail_start = body_start + semicolons[ordinal - 1].end()
    tail_text = text[tail_start:entry_end]

    literal_matches = list(re.finditer(re.escape(anchor), tail_text))
    if len(literal_matches) == 1:
        anchor_match = literal_matches[0]
    else:
        pattern = _text_patch_pattern(
            anchor,
            allow_punctuation_spacing=allow_punctuation_spacing,
            allow_word_punctuation_elision=allow_word_punctuation_elision,
        )
        matches = list(re.finditer(pattern, tail_text, flags=re.I | re.S))
        if len(matches) != 1:
            return DefinitionTextRewriteResult(text, False, ())
        anchor_match = matches[0]

    anchor_end = tail_start + anchor_match.end()
    terminator = ""
    if entry_end > anchor_end and text[entry_end - 1] in ";,.":
        terminator = text[entry_end - 1]
    replacement_text = replacement.strip()
    if terminator and not replacement_text.endswith(terminator):
        replacement_text = f"{replacement_text} {terminator}".strip()
    joiner = (
        ""
        if text[:anchor_end].endswith((" ", "\t", "\n", "\r"))
        or replacement_text.startswith((" ", ",", ".", ";", ":", ")"))
        else " "
    )
    rewritten = " ".join(f"{text[:anchor_end]}{joiner}{replacement_text}{text[entry_end:]}".split()).strip()
    return DefinitionTextRewriteResult(rewritten, True, fallback_recovery_rule_ids)


def _remove_final_definition_child_connector(text: str, connector: str) -> tuple[str, bool]:
    connector = connector.strip().lower()
    if connector not in {"and", "or"}:
        return text, False
    match = re.search(
        rf"(?P<separator>[;,:]?)\s*\b{re.escape(connector)}\b\s*$",
        text,
        flags=re.I,
    )
    if match is None:
        return text, False
    rewritten = text[: match.start()].rstrip()
    separator = match.group("separator")
    if separator and not rewritten.endswith(separator):
        rewritten = f"{rewritten}{separator}"
    return " ".join(rewritten.split()).strip(), True


def _definition_child_tail_after_anchor_selector_parts(
    match: str,
) -> DefinitionChildTailSelectorParts | None:
    if not match.startswith(f"TEXT_IN_DEFINITION_CHILD_TAIL{US}"):
        return None
    parts = match.split(US)
    if len(parts) != 7 or parts[4] != "AFTER" or parts[6] != "TO_END":
        return None
    _, term, child_kind, child_label, _, anchor, _ = parts
    if child_kind not in {"paragraph", "subparagraph", "subsection"}:
        return None
    term = term.strip()
    child_label = child_label.strip()
    anchor = anchor.strip()
    if not term or not child_label or not anchor:
        return None
    return DefinitionChildTailSelectorParts(term=term, child_label=child_label, anchor=anchor)


def _find_text_range_start_index(
    full_text: str,
    start_text: str,
    *,
    occurrence: int,
    allow_punctuation_spacing: bool,
    allow_word_punctuation_elision: bool,
) -> TextRangeStartIndex:
    ordinal = occurrence if occurrence > 0 else 1
    if occurrence > 0:
        range_matches, used_word_anchor = _range_anchor_matches(full_text, start_text)
    else:
        range_matches = list(re.finditer(re.escape(start_text), full_text))
        used_word_anchor = False
    if len(range_matches) >= ordinal:
        recovery_rule_ids = (
            ("uk_replay_text_range_anchor_word_boundary_normalized",)
            if used_word_anchor
            else ()
        )
        return TextRangeStartIndex(range_matches[ordinal - 1].start(), recovery_rule_ids)
    pattern = _text_patch_pattern(
        start_text,
        allow_punctuation_spacing=allow_punctuation_spacing,
        allow_word_punctuation_elision=allow_word_punctuation_elision,
    )
    matches = list(re.finditer(pattern, full_text, flags=re.I | re.S))
    if len(matches) < ordinal:
        return TextRangeStartIndex(-1, ())
    return TextRangeStartIndex(matches[ordinal - 1].start(), ())


class UKReplayTextApplyMixin:
    def _apply_numeric_list_trailing_comma_anchor_on_node_text_only(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int,
    ) -> NumericListTrailingCommaApplyResult:
        """Recover a unique numeric list item whose source selector adds a comma."""

        text = node.text or ""
        text_replacement = _numeric_list_trailing_comma_replacement_text(
            text,
            match,
            replacement,
            occurrence,
            end_occurrence,
        )
        if text_replacement is None:
            return NumericListTrailingCommaApplyResult(node=node, applied=False, anchor=None)
        rebuilt = dc_replace(node, text=text_replacement.new_text)
        self._replace_node_in_statute(node, rebuilt)
        return NumericListTrailingCommaApplyResult(
            node=rebuilt,
            applied=True,
            anchor=text_replacement.anchor,
        )

    def _apply_numeric_list_trailing_comma_anchor_on_subtree(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int = 0,
    ) -> NumericListTrailingCommaApplyResult:
        """Recover one unique numeric list item across a resolved target subtree."""

        subtree_replacement = _numeric_list_trailing_comma_subtree_replacement(
            node,
            match,
            replacement,
            occurrence,
            end_occurrence,
        )
        if subtree_replacement is None:
            return NumericListTrailingCommaApplyResult(node=node, applied=False, anchor=None)
        text_node = node
        for index in subtree_replacement.path:
            text_node = text_node.children[index]
        rebuilt = self._replace_descendant_at_path(
            node,
            subtree_replacement.path,
            dc_replace(
                text_node,
                text=subtree_replacement.new_text,
            ),
        )
        self._replace_node_in_statute(node, rebuilt)
        return NumericListTrailingCommaApplyResult(
            node=rebuilt,
            applied=True,
            anchor=subtree_replacement.anchor,
        )

    def _apply_text_replace_on_node_text_only(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int = 0,
        *,
        allow_punctuation_spacing: bool = False,
        allow_word_punctuation_elision: bool = False,
        recovery_rule_ids_out: Optional[list[str]] = None,
    ) -> tuple[UKMutableNode, bool]:
        """Apply a text patch only to one node's text, never to descendants."""
        text = node.text or ""
        if not text:
            return node, False
        if match.startswith("TEXT_FEE_SUM_"):
            old_fee = match[len("TEXT_FEE_SUM_"):]
            found_matches = []
            if old_fee and old_fee != "ANY":
                pattern = _text_patch_pattern(
                    old_fee,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                found_matches = list(re.finditer(pattern, text, flags=re.I))
            if not found_matches:
                found_matches = list(_UK_FEE_SUM_PATTERN.finditer(text))
            if not found_matches:
                return node, False

            if occurrence == 0:
                new_text = text
                for m in reversed(found_matches):
                    new_text = new_text[:m.start()] + replacement + new_text[m.end():]
                rebuilt = dc_replace(node, text=new_text)
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            elif occurrence == -1:
                m = found_matches[-1]
                rebuilt = dc_replace(node, text=text[:m.start()] + replacement + text[m.end():])
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            else:
                if occurrence <= len(found_matches):
                    m = found_matches[occurrence - 1]
                    rebuilt = dc_replace(node, text=text[:m.start()] + replacement + text[m.end():])
                    self._replace_node_in_statute(node, rebuilt)
                    return rebuilt, True
                return node, False

        if match == "TEXT_ALL":
            rebuilt = dc_replace(node, text=replacement)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        if match == "TEXT_IN_BRACKETS":
            bracket_matches = list(_UK_BRACKETED_TEXT_PATTERN.finditer(text))
            if occurrence > 0:
                if occurrence > len(bracket_matches):
                    return node, False
                bracket_match = bracket_matches[occurrence - 1]
            else:
                if len(bracket_matches) != 1:
                    return node, False
                bracket_match = bracket_matches[0]
            rebuilt = dc_replace(
                node,
                text=f"{text[: bracket_match.start()]}{replacement}{text[bracket_match.end() :]}",
            )
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_words_in_brackets_text_rewrite_applied")
            return rebuilt, True
        if match.startswith("TEXT_AFTER_EACH_OTHER_OCCURRENCE\x1f"):
            anchor = match.split("\x1f", 1)[1].strip()
            if not anchor:
                return node, False
            matches = list(re.finditer(re.escape(anchor), text))
            if not matches:
                pattern = _text_patch_pattern(
                    anchor,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, text, flags=re.I))
            if len(matches) <= 1:
                return node, False
            new_text = text
            for anchor_match in reversed(matches[1:]):
                joiner = (
                    ""
                    if new_text[: anchor_match.end()].endswith((" ", "\t", "\n", "\r"))
                    or replacement.startswith((" ", ",", ".", ";", ":", ")"))
                    else " "
                )
                new_text = (
                    new_text[: anchor_match.end()]
                    + joiner
                    + replacement
                    + new_text[anchor_match.end() :]
                )
            rebuilt = dc_replace(node, text=new_text)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_each_other_place_after_anchor_insert_applied")
            return rebuilt, True
        if match.startswith("TEXT_EACH_OTHER_OCCURRENCE_AFTER_FIRST_SIBLING\x1f"):
            parts = match.split("\x1f", 2)
            if len(parts) != 3:
                return node, False
            sibling_replacement = parts[1].strip()
            anchor = parts[2].strip()
            if not anchor:
                return node, False
            matches = list(re.finditer(re.escape(anchor), text))
            if not matches:
                pattern = _text_patch_pattern(
                    anchor,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, text, flags=re.I))
            if not matches:
                return node, False
            prefix = text[: matches[0].start()]
            sibling_seen = bool(sibling_replacement and re.search(re.escape(sibling_replacement), prefix, flags=re.I))
            if not sibling_seen and sibling_replacement:
                sibling_pattern = _text_patch_pattern(
                    sibling_replacement,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                sibling_seen = bool(re.search(sibling_pattern, prefix, flags=re.I))
            selected_matches = matches if sibling_seen else matches[1:]
            if not selected_matches:
                return node, False
            new_text = text
            for anchor_match in reversed(selected_matches):
                new_text = (
                    new_text[: anchor_match.start()]
                    + replacement
                    + new_text[anchor_match.end() :]
                )
            rebuilt = dc_replace(node, text=new_text)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append(
                    "uk_replay_each_other_place_substitution_applied"
                )
            return rebuilt, True
        if match.startswith(f"TEXT_IN_DEFINITION_CHILD_TAIL{US}"):
            selector_parts = _definition_child_tail_after_anchor_selector_parts(match)
            if selector_parts is None:
                return node, False
            new_text, changed, fallback_recovery_rule_ids = _rewrite_definition_child_tail_after_anchor_to_end_text(
                text,
                term=selector_parts.term,
                child_label=selector_parts.child_label,
                anchor=selector_parts.anchor,
                replacement=replacement,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            if not changed:
                return node, False
            rebuilt = dc_replace(node, text=new_text)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.extend(fallback_recovery_rule_ids)
                recovery_rule_ids_out.append(
                    "uk_replay_definition_child_tail_after_anchor_to_end_text_rewrite_applied"
                )
            return rebuilt, True

        if match.startswith("TEXT_AFTER_") and match.endswith("_TO_END"):
            anchor = match[len("TEXT_AFTER_") : -len("_TO_END")]
            if not anchor:
                return node, False
            ordinal = occurrence if occurrence > 0 else 1
            literal_matches = list(re.finditer(re.escape(anchor), text))
            if len(literal_matches) >= ordinal:
                anchor_match = literal_matches[ordinal - 1]
            else:
                pattern = _text_patch_pattern(
                    anchor,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, text, flags=re.I | re.S))
                if len(matches) < ordinal:
                    return node, False
                anchor_match = matches[ordinal - 1]
            joiner = (
                ""
                if text[: anchor_match.end()].endswith((" ", "\t", "\n", "\r"))
                or replacement.startswith((" ", ",", ".", ";", ":", ")"))
                else " "
            )
            rebuilt = dc_replace(node, text=f"{text[: anchor_match.end()]}{joiner}{replacement}")
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_after_anchor_to_end_text_rewrite_applied")
            return rebuilt, True
        if match.startswith("TEXT_FROM_") and match.endswith("_TO_END"):
            start_text = match[len("TEXT_FROM_") : -len("_TO_END")]
            if not start_text:
                return node, False
            ordinal = occurrence if occurrence > 0 else 1
            literal_matches = list(re.finditer(re.escape(start_text), text))
            if len(literal_matches) >= ordinal:
                start_match = literal_matches[ordinal - 1]
            else:
                pattern = _text_patch_pattern(
                    start_text,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, text, flags=re.I | re.S))
                if len(matches) < ordinal:
                    return node, False
                start_match = matches[ordinal - 1]
            rebuilt = dc_replace(node, text=f"{text[: start_match.start()]}{replacement}".strip())
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_node_local_range_to_end_text_rewrite_applied")
            return rebuilt, True
        if match.startswith("TEXT_FROM_") and "_TO_" in match:
            start_text, end_text = match.replace("TEXT_FROM_", "", 1).split("_TO_", 1)
            if not end_text:
                return node, False
            if start_text:
                start_ordinal = occurrence if occurrence > 0 else 1
                if occurrence > 0:
                    start_matches, used_word_start = _range_anchor_matches(text, start_text)
                else:
                    start_matches = list(re.finditer(re.escape(start_text), text))
                    used_word_start = False
                if len(start_matches) >= start_ordinal:
                    start_match = start_matches[start_ordinal - 1]
                    if used_word_start and recovery_rule_ids_out is not None:
                        recovery_rule_ids_out.append("uk_replay_text_range_anchor_word_boundary_normalized")
                else:
                    start_pattern = _text_patch_pattern(
                        start_text,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    start_matches = list(re.finditer(start_pattern, text, flags=re.I | re.S))
                    if len(start_matches) < start_ordinal:
                        return node, False
                    start_match = start_matches[start_ordinal - 1]
                start_start = start_match.start()
                start_end = start_match.end()
            else:
                start_start = 0
                start_end = 0

            end_ordinal = end_occurrence if end_occurrence > 0 else 0
            if end_ordinal:
                end_matches, used_word_end = _range_anchor_matches(text, end_text)
                if len(end_matches) >= end_ordinal:
                    end_match = end_matches[end_ordinal - 1]
                    if end_match.start() < start_end:
                        return node, False
                    end_end = end_match.end()
                    if used_word_end and recovery_rule_ids_out is not None:
                        recovery_rule_ids_out.append("uk_replay_text_range_anchor_word_boundary_normalized")
                else:
                    end_pattern = _text_patch_pattern(
                        end_text,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    end_matches = list(re.finditer(end_pattern, text, flags=re.I | re.S))
                    if len(end_matches) < end_ordinal:
                        return node, False
                    end_match = end_matches[end_ordinal - 1]
                    if end_match.start() < start_end:
                        return node, False
                    end_end = end_match.end()
            else:
                end_idx = text.find(end_text, start_end)
                if end_idx == -1:
                    return node, False
                end_end = end_idx + len(end_text)
            rebuilt = dc_replace(node, text=f"{text[:start_start]}{replacement}{text[end_end:]}")
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_node_local_range_text_rewrite_applied")
            return rebuilt, True
        if occurrence == -1:
            pos = text.rfind(match)
            if pos != -1:
                rebuilt = dc_replace(node, text=text[:pos] + replacement + text[pos + len(match) :])
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            pattern = _text_patch_pattern(
                match,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            matches = list(re.finditer(pattern, text, flags=re.I))
            if not matches:
                return node, False
            last = matches[-1]
            rebuilt = dc_replace(node, text=text[: last.start()] + replacement + text[last.end() :])
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        if occurrence == 0:
            if match in text:
                rebuilt = dc_replace(node, text=text.replace(match, replacement))
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            pattern = _text_patch_pattern(
                match,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            new_text, count = re.subn(pattern, replacement, text, flags=re.I)
            if count == 0:
                return node, False
            rebuilt = dc_replace(node, text=new_text)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        start = 0
        seen = 0
        while True:
            pos = text.find(match, start)
            if pos == -1:
                break
            seen += 1
            if seen == occurrence:
                rebuilt = dc_replace(node, text=text[:pos] + replacement + text[pos + len(match) :])
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            start = pos + len(match)
        pattern = _text_patch_pattern(
            match,
            allow_punctuation_spacing=allow_punctuation_spacing,
            allow_word_punctuation_elision=allow_word_punctuation_elision,
        )
        for idx, normalized_match in enumerate(re.finditer(pattern, text, flags=re.I), start=1):
            if idx == occurrence:
                rebuilt = dc_replace(
                    node,
                    text=text[: normalized_match.start()] + replacement + text[normalized_match.end() :],
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
        return node, False

    def _apply_text_replace_on_marked_post_child_tail(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        *,
        allow_punctuation_spacing: bool = False,
        allow_word_punctuation_elision: bool = False,
        recovery_rule_ids_out: Optional[list[str]] = None,
    ) -> tuple[UKMutableNode, bool]:
        """Apply a range-to-end rewrite to parser-marked post-child local text."""
        text = node.text or ""
        post_child_tail = str(node.attrs.get("uk_post_child_text_tail") or "")
        if not text or not post_child_tail or not match.startswith("TEXT_FROM_") or not match.endswith("_TO_END"):
            return node, False
        start_text = match[len("TEXT_FROM_") : -len("_TO_END")]
        if not start_text:
            return node, False
        tail_start = _find_text_range_start_index(
            post_child_tail,
            start_text,
            occurrence=occurrence,
            allow_punctuation_spacing=allow_punctuation_spacing,
            allow_word_punctuation_elision=allow_word_punctuation_elision,
        )
        if tail_start.start_index == -1:
            return node, False
        tail_offset = text.rfind(post_child_tail)
        if tail_offset == -1:
            return node, False
        if recovery_rule_ids_out is not None and tail_start.recovery_rule_ids:
            recovery_rule_ids_out.extend(tail_start.recovery_rule_ids)
        rewrite_start = tail_offset + tail_start.start_index
        rebuilt = dc_replace(node, text=f"{text[:rewrite_start]}{replacement}".strip())
        self._replace_node_in_statute(node, rebuilt)
        if recovery_rule_ids_out is not None:
            recovery_rule_ids_out.append("uk_replay_node_local_range_to_end_text_rewrite_applied")
        return rebuilt, True

    def _apply_text_append_on_node_text_only(
        self,
        node: UKMutableNode,
        insertion: str,
    ) -> tuple[UKMutableNode, bool]:
        """Append text only to one node's text, never to descendants."""
        text = node.text or ""
        if not insertion:
            return node, False
        joiner = (
            ""
            if not text
            or text.endswith((" ", "\t", "\n", "\r"))
            or insertion.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        rebuilt = dc_replace(node, text=f"{text}{joiner}{insertion}")
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True

    def _apply_text_append_on_subtree_text_end(
        self,
        node: UKMutableNode,
        insertion: str,
    ) -> tuple[UKMutableNode, bool]:
        """Append text at the target subtree end without flattening children."""
        if not insertion:
            return node, False
        if node.text or not node.children:
            return self._apply_text_append_on_node_text_only(node, insertion)

        text_nodes = _text_nodes_in_document_order(node)
        if not text_nodes:
            return node, False
        text_path, text_node = text_nodes[-1]
        text = text_node.text or ""
        joiner = (
            ""
            if not text
            or text.endswith((" ", "\t", "\n", "\r"))
            or insertion.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        replacement_node = dc_replace(text_node, text=f"{text}{joiner}{insertion}")
        if not text_path:
            self._replace_node_in_statute(text_node, replacement_node)
            return replacement_node, True
        rebuilt = self._replace_descendant_at_path(node, text_path, replacement_node)
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True

    def _apply_text_substitution_on_node(
        self,
        node: UKMutableNode,
        subs: list[dict],
    ) -> tuple[UKMutableNode, tuple[dict[str, Any], ...]]:
        text = node.text or ""
        children = list(node.children)
        observations: list[dict[str, Any]] = []
        for s in subs:
            old, new = s["original"], s["replacement"]
            if old.startswith("FROM_") and "_TO_" in old:
                parts = old.replace("FROM_", "").split("_TO_")
                if len(parts) == 2:
                    start_label, end_label = parts[0].strip("()"), parts[1].strip("()")
                    start_idx = end_idx = -1
                    for i, child in enumerate(children):
                        if _clean_num(child.label or "") == _clean_num(start_label):
                            start_idx = i
                        if _clean_num(child.label or "") == _clean_num(end_label):
                            end_idx = i
                    if start_idx != -1 and end_idx != -1 and start_idx <= end_idx:
                        self._log(
                            f"  EXECUTOR: deleting children from '{start_label}' to '{end_label}' in {node.kind} {node.label}"
                        )
                        removed_labels = tuple(str(child.label or "") for child in children[start_idx : end_idx + 1])
                        for i in range(end_idx, start_idx - 1, -1):
                            children.pop(i)
                        observations.append(
                            {
                                "source_shape": "fragment_substitution_child_range_selector",
                                "start_label": start_label,
                                "end_label": end_label,
                                "removed_labels": removed_labels,
                                "removed_count": len(removed_labels),
                            }
                        )
                continue
            if old in text:
                text = text.replace(old, new)
            else:
                pattern = re.escape(old).replace(r"\ ", r"\s+")
                new_text, count = re.subn(pattern, new, text, flags=re.I)
                if count > 0:
                    text = new_text
        rebuilt = dc_replace(node, text=text, children=list(children))
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, tuple(observations)

    def _apply_unique_text_node_rewrite(
        self,
        node: UKMutableNode,
        text_nodes: list[DocumentTextNode],
        rewrite: Callable[[str], tuple[str, bool]],
    ) -> tuple[UKMutableNode, bool]:
        """Apply a text rewrite to root text or one unique descendant text node."""

        if node.text:
            new_text, changed = rewrite(node.text)
            if changed:
                rebuilt = dc_replace(node, text=new_text)
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

        candidate_paths: list[TextNodeRewriteCandidate] = []
        for path, text_node in text_nodes:
            if not text_node.text:
                continue
            new_text, changed = rewrite(text_node.text)
            if changed:
                candidate_paths.append((path, text_node, new_text))
        if len(candidate_paths) != 1:
            return node, False
        path, text_node, new_text = candidate_paths[0]
        rebuilt = self._replace_descendant_at_path(
            node,
            path,
            dc_replace(text_node, text=new_text),
        )
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True

    def _apply_unique_text_node_rewrite_with_metadata(
        self,
        node: UKMutableNode,
        text_nodes: list[DocumentTextNode],
        rewrite: Callable[[str], tuple[str, bool, Any]],
    ) -> tuple[UKMutableNode, bool, Any]:
        """Apply a unique text rewrite and return the rewrite's metadata."""

        if node.text:
            new_text, changed, metadata = rewrite(node.text)
            if changed:
                rebuilt = dc_replace(node, text=new_text)
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True, metadata

        candidate_paths: list[TextNodeRewriteMetadataCandidate] = []
        for path, text_node in text_nodes:
            if not text_node.text:
                continue
            new_text, changed, metadata = rewrite(text_node.text)
            if changed:
                candidate_paths.append((path, text_node, new_text, metadata))
        if len(candidate_paths) != 1:
            return node, False, None
        path, text_node, new_text, metadata = candidate_paths[0]
        rebuilt = self._replace_descendant_at_path(
            node,
            path,
            dc_replace(text_node, text=new_text),
        )
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True, metadata

    def _apply_text_replace_on_subtree(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int = 0,
        *,
        allow_punctuation_spacing: bool = False,
        allow_word_punctuation_elision: bool = False,
        recovery_rule_ids_out: Optional[list[str]] = None,
    ) -> tuple[UKMutableNode, bool]:
        """Walk the subtree rooted at *node*, find *match* in text fields, and substitute.

        Args:
            node:        Root of the IR subtree to search.
            match:       Exact string to find (case-sensitive first, then whitespace-
                         normalized fallback, consistent with _apply_text_substitution_on_node).
            replacement: String to substitute in place of *match*.
            occurrence:  0 = replace all occurrences across the subtree.
                         N > 0 = replace only the Nth occurrence (1-based, document order).
                         -1 = replace only the last occurrence in document order.

        Returns:
            True if at least one substitution was made; False otherwise.
        """
        text_nodes = _text_nodes_in_document_order(node)

        if match.startswith("TEXT_AMENDMENT_PROGRAM_INSERTED_PARENT_"):
            program_match = _UK_AMENDMENT_PROGRAM_INSERTED_PARENT_PATTERN.fullmatch(match)
            if program_match is None or not replacement:
                return node, False
            candidate_replacements: list[TextNodeRewriteCandidate] = []
            for path, text_node in text_nodes:
                new_text = _uk_apply_amendment_program_inserted_parent_child_insert(
                    text_node.text or "",
                    inserted_parent_label=program_match.group(1),
                    direction=program_match.group(2).lower(),
                    anchor_label=program_match.group(3),
                    replacement=replacement,
                )
                if new_text is not None:
                    candidate_replacements.append((path, text_node, new_text))
            if len(candidate_replacements) != 1:
                return node, False
            path, text_node, new_text = candidate_replacements[0]
            rebuilt = self._replace_descendant_at_path(
                node,
                path,
                dc_replace(text_node, text=new_text),
            )
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append(
                    "uk_replay_amendment_program_inserted_parent_child_insert_applied"
                )
            return rebuilt, True

        if match.startswith("TEXT_FEE_SUM_"):
            old_fee = match[len("TEXT_FEE_SUM_"):]
            all_matches = []
            for path, tn in text_nodes:
                text = tn.text or ""
                found_matches = []
                if old_fee and old_fee != "ANY":
                    pattern = _text_patch_pattern(
                        old_fee,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    found_matches = list(re.finditer(pattern, text, flags=re.I))
                if not found_matches:
                    found_matches = list(_UK_FEE_SUM_PATTERN.finditer(text))
                for m in found_matches:
                    all_matches.append((path, tn, m))

            if not all_matches:
                return node, False

            if occurrence == 0:
                from collections import defaultdict
                by_path = defaultdict(list)
                for path, tn, m in all_matches:
                    by_path[path].append((tn, m))
                rebuilt = node
                for path, matches_in_node in by_path.items():
                    tn = matches_in_node[0][0]
                    new_text = tn.text
                    sorted_matches = sorted(matches_in_node, key=lambda x: x[1].start(), reverse=True)
                    for _, m in sorted_matches:
                        new_text = new_text[:m.start()] + replacement + new_text[m.end():]
                    rebuilt = self._replace_descendant_at_path(
                        rebuilt,
                        path,
                        dc_replace(tn, text=new_text),
                    )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            elif occurrence == -1:
                path, tn, m = all_matches[-1]
                rebuilt = self._replace_descendant_at_path(
                    node,
                    path,
                    dc_replace(tn, text=tn.text[:m.start()] + replacement + tn.text[m.end():]),
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            else:
                if occurrence <= len(all_matches):
                    path, tn, m = all_matches[occurrence - 1]
                    rebuilt = self._replace_descendant_at_path(
                        node,
                        path,
                        dc_replace(tn, text=tn.text[:m.start()] + replacement + tn.text[m.end():]),
                    )
                    self._replace_node_in_statute(node, rebuilt)
                    return rebuilt, True
                return node, False

        if match == "TEXT_IN_BRACKETS":
            all_matches: list[TextNodeRegexMatch] = []
            for path, text_node in text_nodes:
                for bracket_match in _UK_BRACKETED_TEXT_PATTERN.finditer(text_node.text or ""):
                    all_matches.append((path, text_node, bracket_match))
            if occurrence > 0:
                if occurrence > len(all_matches):
                    return node, False
                path, text_node, bracket_match = all_matches[occurrence - 1]
            else:
                if len(all_matches) != 1:
                    return node, False
                path, text_node, bracket_match = all_matches[0]
            old_text = text_node.text or ""
            replacement_node = dc_replace(
                text_node,
                text=f"{old_text[: bracket_match.start()]}{replacement}{old_text[bracket_match.end() :]}",
            )
            if not path:
                self._replace_node_in_statute(node, replacement_node)
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append("uk_replay_words_in_brackets_text_rewrite_applied")
                return replacement_node, True
            rebuilt = self._replace_descendant_at_path(node, path, replacement_node)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_words_in_brackets_text_rewrite_applied")
            return rebuilt, True
        if match.startswith("TEXT_AFTER_EACH_OTHER_OCCURRENCE\x1f"):
            anchor = match.split("\x1f", 1)[1].strip()
            if not anchor:
                return node, False
            all_matches: list[TextNodeRegexMatch] = []
            for path, text_node in text_nodes:
                text = text_node.text or ""
                node_matches = list(re.finditer(re.escape(anchor), text))
                if not node_matches:
                    pattern = _text_patch_pattern(
                        anchor,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    node_matches = list(re.finditer(pattern, text, flags=re.I))
                for anchor_match in node_matches:
                    all_matches.append((path, text_node, anchor_match))
            if len(all_matches) <= 1:
                return node, False
            rebuilt = node
            by_path: TextNodeRegexMatchesByPath = {}
            for path, text_node, anchor_match in all_matches[1:]:
                existing = by_path.get(path)
                if existing is None:
                    by_path[path] = (text_node, [anchor_match])
                else:
                    existing[1].append(anchor_match)
            for path, (text_node, path_matches) in by_path.items():
                new_text = text_node.text or ""
                for anchor_match in reversed(path_matches):
                    joiner = (
                        ""
                        if new_text[: anchor_match.end()].endswith((" ", "\t", "\n", "\r"))
                        or replacement.startswith((" ", ",", ".", ";", ":", ")"))
                        else " "
                    )
                    new_text = (
                        new_text[: anchor_match.end()]
                        + joiner
                        + replacement
                        + new_text[anchor_match.end() :]
                    )
                rebuilt = self._replace_descendant_at_path(
                    rebuilt,
                    path,
                    dc_replace(text_node, text=new_text),
                )
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_each_other_place_after_anchor_insert_applied")
            return rebuilt, True
        if match.startswith("TEXT_EACH_OTHER_OCCURRENCE_AFTER_FIRST_SIBLING\x1f"):
            parts = match.split("\x1f", 2)
            if len(parts) != 3:
                return node, False
            sibling_replacement = parts[1].strip()
            anchor = parts[2].strip()
            if not anchor:
                return node, False
            all_matches: list[TextNodeRegexMatch] = []
            prefix_parts: list[str] = []
            prefix_before_first_match = ""
            for path, text_node in text_nodes:
                text = text_node.text or ""
                node_matches = list(re.finditer(re.escape(anchor), text))
                if not node_matches:
                    pattern = _text_patch_pattern(
                        anchor,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    node_matches = list(re.finditer(pattern, text, flags=re.I))
                if node_matches and not all_matches:
                    prefix_before_first_match = " ".join(
                        part for part in (*prefix_parts, text[: node_matches[0].start()]) if part
                    )
                for anchor_match in node_matches:
                    all_matches.append((path, text_node, anchor_match))
                if not all_matches:
                    prefix_parts.append(text)
            if not all_matches:
                return node, False
            sibling_seen = bool(
                sibling_replacement
                and re.search(re.escape(sibling_replacement), prefix_before_first_match, flags=re.I)
            )
            if not sibling_seen and sibling_replacement:
                sibling_pattern = _text_patch_pattern(
                    sibling_replacement,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                sibling_seen = bool(re.search(sibling_pattern, prefix_before_first_match, flags=re.I))
            selected_matches = all_matches if sibling_seen else all_matches[1:]
            if not selected_matches:
                return node, False
            rebuilt = node
            by_path: TextNodeRegexMatchesByPath = {}
            for path, text_node, anchor_match in selected_matches:
                existing = by_path.get(path)
                if existing is None:
                    by_path[path] = (text_node, [anchor_match])
                else:
                    existing[1].append(anchor_match)
            for path, (text_node, path_matches) in by_path.items():
                new_text = text_node.text or ""
                for anchor_match in reversed(path_matches):
                    new_text = (
                        new_text[: anchor_match.start()]
                        + replacement
                        + new_text[anchor_match.end() :]
                    )
                rebuilt = self._replace_descendant_at_path(
                    rebuilt,
                    path,
                    dc_replace(text_node, text=new_text),
                )
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append(
                    "uk_replay_each_other_place_substitution_applied"
                )
            return rebuilt, True

        if match == "TEXT_OPENING_WORDS":
            if not node.text:
                return node, False
            rebuilt = dc_replace(node, text=replacement)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match == "TEXT_BEGINNING":
            if not node.text:
                return node, False
            joiner = "" if replacement.endswith((" ", "(", "/", "-")) else " "
            rebuilt = dc_replace(node, text=f"{replacement}{joiner}{node.text}")
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith(f"TEXT_FROM_CHILD_END{US}"):
            parts = match.split(US, 3)
            if len(parts) != 4:
                return node, False
            child_kind = parts[1]
            child_label = parts[2]
            start_text = parts[3].strip()
            if not child_kind or not child_label or not start_text:
                return node, False
            direct_child_matches = [
                (index, child)
                for index, child in enumerate(node.children)
                if (child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind))
                == child_kind
                and _clean_num(child.label or "") == _clean_num(child_label)
            ]
            if len(direct_child_matches) != 1:
                return node, False
            child_index, _child = direct_child_matches[0]
            text = node.text or ""
            if not text:
                return node, False
            ordinal = occurrence if occurrence > 0 else 1
            literal_matches = list(re.finditer(re.escape(start_text), text))
            if len(literal_matches) >= ordinal:
                start_match = literal_matches[ordinal - 1]
            else:
                pattern = _text_patch_pattern(
                    start_text,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, text, flags=re.I | re.S))
                if len(matches) < ordinal:
                    return node, False
                start_match = matches[ordinal - 1]
            separators = list(re.finditer(r"[—–-]", text[start_match.end() :]))
            if not separators:
                return node, False
            separator = separators[-1]
            tail_start = start_match.end() + separator.end()
            prefix = text[: start_match.start()].rstrip()
            tail = text[tail_start:].strip()
            replacement_text = replacement.strip()
            if not replacement_text:
                new_text = f"{prefix} {tail}".strip()
            else:
                joiner_before = "" if not prefix or replacement_text.startswith((" ", ",", ".", ";", ":", ")")) else " "
                joiner_after = "" if not tail or replacement_text.endswith((" ", "(", "/", "-")) else " "
                new_text = f"{prefix}{joiner_before}{replacement_text}{joiner_after}{tail}".strip()
            rebuilt = dc_replace(
                node,
                text=" ".join(new_text.split()).strip(),
                children=tuple(node.children[child_index + 1 :]),
            )
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_labeled_child_end_range_applied")
            return rebuilt, True

        if match.startswith("TEXT_BEFORE_CHILD_"):
            child_match = re.fullmatch(
                r"TEXT_BEFORE_CHILD_([A-Za-z]+)_([0-9A-Za-z]+)",
                match,
            )
            if child_match is None:
                return node, False
            child_kind = child_match.group(1)
            child_label = child_match.group(2)
            if not node.text:
                return node, False

            direct_child_matches = [
                child
                for child in node.children
                if (child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind))
                == child_kind
                and _clean_num(child.label or "") == _clean_num(child_label)
            ]
            if len(direct_child_matches) != 1:
                return node, False
            rebuilt = dc_replace(node, text=replacement)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_source_carried_before_child_text_rewrite_applied")
            return rebuilt, True

        if match == "TEXT_AFTER_AMENDMENT_INSERT_TO_END":
            text = node.text or ""
            insert_matches = list(re.finditer(r"\binsert\s*[—–-]", text, flags=re.I))
            if not insert_matches or not replacement:
                return node, False
            insert_match = insert_matches[-1]
            rebuilt = dc_replace(
                node,
                text=f"{text[: insert_match.end()].rstrip()} {replacement.strip()}",
            )
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_amendment_insert_tail_text_rewrite_applied")
            return rebuilt, True

        if match.startswith("TEXT_AMENDMENT_PROGRAM_INSERTED_PARENT_"):
            program_match = _UK_AMENDMENT_PROGRAM_INSERTED_PARENT_PATTERN.fullmatch(match)
            if program_match is None or not replacement:
                return node, False
            text = node.text or ""
            new_text = _uk_apply_amendment_program_inserted_parent_child_insert(
                text,
                inserted_parent_label=program_match.group(1),
                direction=program_match.group(2).lower(),
                anchor_label=program_match.group(3),
                replacement=replacement,
            )
            if new_text is None:
                return node, False
            rebuilt = dc_replace(node, text=new_text)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append(
                    "uk_replay_amendment_program_inserted_parent_child_insert_applied"
                )
            return rebuilt, True

        if match.startswith("TEXT_IN_CHILDREN_"):
            child_match = re.fullmatch(
                rf"TEXT_IN_CHILDREN_([A-Za-z]+)_([0-9A-Za-z_]+){re.escape(US)}(.+)",
                match,
                flags=re.S,
            )
            if child_match is None or replacement:
                return node, False
            child_kind = child_match.group(1)
            child_labels = tuple(label for label in child_match.group(2).split("_") if label)
            original = child_match.group(3).strip()
            if not child_labels or not original:
                return node, False
            direct_matches: dict[str, tuple[int, UKMutableNode]] = {}
            for index, child in enumerate(node.children):
                kind_value = child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind)
                label_key = _clean_num(child.label or "")
                if kind_value == child_kind and label_key in child_labels and label_key not in direct_matches:
                    direct_matches[label_key] = (index, child)
                elif kind_value == child_kind and label_key in child_labels:
                    return node, False
            if set(direct_matches) != set(child_labels):
                return node, False

            new_children = list(node.children)
            for label in child_labels:
                index, child = direct_matches[label]
                new_text, changed = _delete_source_carried_child_text(
                    child.text or "",
                    original=original,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                if not changed:
                    return node, False
                new_children[index] = dc_replace(child, text=new_text)
            rebuilt = dc_replace(node, children=new_children)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_source_carried_multi_child_text_rewrite_applied")
            return rebuilt, True

        if match.startswith("TEXT_PROVISO_CHILD_"):
            child_label = match[len("TEXT_PROVISO_CHILD_") :].strip()
            structured_child_matches = [
                ( (index,), child )
                for index, child in enumerate(node.children)
                if _clean_num(child.label or "") == _clean_num(child_label)
                and (child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind)) in ("subparagraph", "paragraph")
            ]
            if len(structured_child_matches) == 1:
                child_path, child_node = structured_child_matches[0]
                if replacement:
                    rebuilt_child = dc_replace(child_node, text=replacement.strip())
                    rebuilt = self._replace_descendant_at_path(node, child_path, rebuilt_child)
                    self._replace_node_in_statute(node, rebuilt)
                    if recovery_rule_ids_out is not None:
                        recovery_rule_ids_out.append(
                            "uk_replay_proviso_child_structured_text_rewrite_applied"
                        )
                    return rebuilt, True

        if match.startswith("TEXT_REPLACE_CHILDREN_"):
            parts = match[len("TEXT_REPLACE_CHILDREN_") :].split("_")
            if len(parts) >= 3:
                child_kind = parts[0].lower()
                labels_to_remove = set(parts[1:])
                matching_children = [
                    (index, child)
                    for index, child in enumerate(node.children)
                    if (child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind)) == child_kind
                    and _clean_num(child.label or "") in labels_to_remove
                ]
                if len(matching_children) == len(labels_to_remove):
                    indices_to_remove = {idx for idx, _ in matching_children}
                    new_children = [
                        child for idx, child in enumerate(node.children)
                        if idx not in indices_to_remove
                    ]
                    current_text = node.text or ""
                    if replacement.strip().startswith(",") or replacement.strip().startswith(";"):
                        current_text = current_text.rstrip("—: ")
                    new_text = f"{current_text} {replacement.strip()}".strip()
                    rebuilt = dc_replace(node, text=new_text, children=tuple(new_children))
                    self._replace_node_in_statute(node, rebuilt)
                    if recovery_rule_ids_out is not None:
                        recovery_rule_ids_out.append(
                            "uk_replay_children_range_replaced_with_text_applied"
                        )
                    return rebuilt, True

        if match.startswith("TEXT_AFTER_CHILD_TAIL_"):
            child_match = re.fullmatch(
                r"TEXT_AFTER_CHILD_TAIL_([A-Za-z]+)_([0-9A-Za-z]+)",
                match,
            )
            if child_match is None:
                return node, False
            child_kind = child_match.group(1)
            child_label = child_match.group(2)
            direct_child_matches = [
                (index, child)
                for index, child in enumerate(node.children)
                if (child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind))
                == child_kind
                and _clean_num(child.label or "") == _clean_num(child_label)
            ]
            if len(direct_child_matches) != 1:
                return node, False
            child_index, _ = direct_child_matches[0]
            if child_index != len(node.children) - 1:
                return node, False
            text = node.text or ""
            if not text:
                return node, False
            separator_matches = list(re.finditer(r"[—–-]", text))
            if not separator_matches:
                return node, False
            separator = separator_matches[-1]
            tail = text[separator.end() :].strip()
            replacement_text = str(replacement or "").strip()
            if not tail:
                return node, False
            if not replacement_text and not re.match(r"(?:,?\s*)?(?:and|or)\b|[“\"'‘]", tail, flags=re.I):
                return node, False
            if replacement_text:
                joiner = "" if replacement_text.startswith((" ", ",", ".", ";", ":", ")")) else " "
                rebuilt_text = f"{text[: separator.end()].rstrip()}{joiner}{replacement_text}".rstrip()
            else:
                rebuilt_text = text[: separator.end()].rstrip()
            rebuilt = dc_replace(node, text=rebuilt_text)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_source_carried_child_tail_text_rewrite_applied")
            return rebuilt, True

        if match.startswith("TEXT_AFTER_CHILD_LIST_TAIL_"):
            child_match = re.fullmatch(
                r"TEXT_AFTER_CHILD_LIST_TAIL_([A-Za-z]+)",
                match,
            )
            if child_match is None:
                return node, False
            child_kind = child_match.group(1)
            direct_child_matches = [
                (index, child)
                for index, child in enumerate(node.children)
                if (child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind))
                == child_kind
            ]
            if len(direct_child_matches) < 2:
                return node, False
            child_index, _ = direct_child_matches[-1]
            if child_index != len(node.children) - 1:
                return node, False
            text = node.text or ""
            if not text:
                return node, False
            separator_matches = list(re.finditer(r"[—–]", text))
            if not separator_matches:
                return node, False
            separator = separator_matches[-1]
            tail = text[separator.end() :].strip()
            if not tail:
                return node, False
            rebuilt = dc_replace(node, text=text[: separator.end()].rstrip())
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append(
                    "uk_replay_source_carried_child_list_tail_text_rewrite_applied"
                )
            return rebuilt, True

        if match.startswith("TEXT_AFTER_CHILD_"):
            child_match = re.fullmatch(
                r"TEXT_AFTER_CHILD_([A-Za-z]+)_([0-9A-Za-z]+)",
                match,
            )
            if child_match is None:
                return node, False
            child_kind = child_match.group(1)
            child_label = child_match.group(2)
            anchor_paths = _collect_descendant_paths_by_label_and_kinds(
                node,
                label=child_label,
                allowed_kinds={child_kind},
            )
            if len(anchor_paths) != 1:
                return node, False
            anchor_path = anchor_paths[0]
            target_node = _node_at_path(node, anchor_path)
            joiner = "" if replacement.startswith((" ", ",", ".", ";", ":", ")")) else " "
            new_text = f"{(target_node.text or '').rstrip()}{joiner}{replacement}".rstrip()
            rebuilt = self._replace_descendant_at_path(
                node,
                anchor_path,
                dc_replace(target_node, text=new_text),
            )
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_source_carried_after_child_text_rewrite_applied")
            return rebuilt, True

        if match.startswith("TEXT_BEFORE_DEFINITION_"):
            term = match[len("TEXT_BEFORE_DEFINITION_") :].strip()
            if not term:
                return node, False
            if node.children:
                return node, False
            full_text = " ".join(tn.text.strip() for _, tn in text_nodes if tn.text).strip()
            if not full_text:
                return node, False
            term_pattern = _text_patch_pattern(
                term,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            definition_pattern = re.compile(
                rf"(?P<prefix>^|[;\.—–-]\s*)"
                rf"(?P<body>[“\"'‘]?\s*{term_pattern}\s*[”\"'’]?(?:\s*[,;:])?\s+)",
                re.I | re.S,
            )
            definition_match = definition_pattern.search(full_text)
            if definition_match is None:
                return node, False
            insert_at = definition_match.start("body")
            joiner = "" if replacement.endswith(" ") else " "
            new_text = f"{full_text[:insert_at]}{replacement}{joiner}{full_text[insert_at:]}"
            rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_before_definition_text_rewrite_applied")
            return rebuilt, True

        if match.startswith("TEXT_IN_DEFINITION_") and not match.startswith("TEXT_IN_DEFINITION_CHILD_"):
            parts = match[len("TEXT_IN_DEFINITION_") :].split(US)
            if len(parts) == 2 and parts[1] == "AT_END":
                term = parts[0].strip()
                if not term:
                    return node, False

                rebuilt, applied = self._apply_unique_text_node_rewrite(
                    node,
                    text_nodes,
                    lambda text: _insert_at_end_of_definition_text(
                        text,
                        term=term,
                        replacement=replacement,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    ),
                )
                if not applied:
                    return node, False
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append("uk_replay_in_definition_at_end_text_rewrite_applied")
                return rebuilt, True

            if len(parts) == 3 and parts[1] == "DELETE":
                term = parts[0].strip()
                anchor = parts[2].strip()
                if not term or not anchor:
                    return node, False

                rebuilt, applied = self._apply_unique_text_node_rewrite(
                    node,
                    text_nodes,
                    lambda text: _rewrite_anchor_in_definition_entry_text(
                        text,
                        term=term,
                        anchor=anchor,
                        replacement="",
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    ),
                )
                if not applied:
                    return node, False
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_in_definition_quoted_word_delete_applied"
                    )
                return rebuilt, True

            if len(parts) == 4 and parts[1] == "FROM" and parts[3] == "TO_END":
                term = parts[0].strip()
                start_anchor = parts[2].strip()
                if not term or not start_anchor:
                    return node, False

                rebuilt, applied = self._apply_unique_text_node_rewrite(
                    node,
                    text_nodes,
                    lambda text: _rewrite_definition_range_to_end_text(
                        text,
                        term=term,
                        start_anchor=start_anchor,
                        replacement=replacement,
                        occurrence=occurrence,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    ),
                )
                if not applied:
                    return node, False
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_in_definition_range_to_end_text_rewrite_applied"
                    )
                return rebuilt, True

            if len(parts) == 5 and parts[1] == "FROM" and parts[3] == "TO":
                term = parts[0].strip()
                start_anchor = parts[2].strip()
                end_anchor = parts[4].strip()
                if not term or not start_anchor or not end_anchor:
                    return node, False

                rebuilt, applied = self._apply_unique_text_node_rewrite(
                    node,
                    text_nodes,
                    lambda text: _rewrite_definition_range_text(
                        text,
                        term=term,
                        start_anchor=start_anchor,
                        end_anchor=end_anchor,
                        replacement=replacement,
                        occurrence=occurrence,
                        end_occurrence=end_occurrence,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    ),
                )
                if not applied:
                    return node, False
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_in_definition_range_text_rewrite_applied"
                    )
                return rebuilt, True

            if len(parts) == 3 and parts[1] == "AFTER_EACH":
                term = parts[0].strip()
                anchor = parts[2].strip()
                if not term or not anchor:
                    return node, False

                rebuilt, applied = self._apply_unique_text_node_rewrite(
                    node,
                    text_nodes,
                    lambda text: _rewrite_each_anchor_in_definition_entry_text(
                        text,
                        term=term,
                        anchor=anchor,
                        replacement=replacement,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    ),
                )
                if not applied:
                    return node, False
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_in_definition_after_each_text_rewrite_applied"
                    )
                return rebuilt, True

            if len(parts) != 3 or parts[1] != "AFTER":
                return node, False
            term = parts[0].strip()
            anchor = parts[2].strip()
            if not term or not anchor:
                return node, False

            rebuilt, applied = self._apply_unique_text_node_rewrite(
                node,
                text_nodes,
                lambda text: _rewrite_anchor_in_definition_entry_text(
                    text,
                    term=term,
                    anchor=anchor,
                    replacement=replacement,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                ),
            )
            if not applied:
                return node, False
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append(
                    "uk_replay_in_definition_after_anchor_text_rewrite_applied"
                )
            return rebuilt, True

        if match.startswith("TEXT_DEFINITION_CHILD_"):
            child_selector = match[len("TEXT_DEFINITION_CHILD_") :]
            child_parts = child_selector.split(US)
            if len(child_parts) != 2:
                return node, False
            kind_and_term, child_label = child_parts
            child_match = re.fullmatch(r"([A-Z]+)_(.+)", kind_and_term)
            if child_match is None:
                return node, False
            child_kind = child_match.group(1).lower()
            term = child_match.group(2).strip()
            child_label = child_label.strip()
            if child_kind != "paragraph" or not term or not child_label:
                return node, False

            structured_child_matches = _definition_child_nodes(
                node,
                term=term,
                child_label=child_label,
            )
            if len(structured_child_matches) == 1:
                child_path, child_node = structured_child_matches[0]

                if replacement:
                    rebuilt_child = dc_replace(child_node, text=replacement.strip())
                    rebuilt = self._replace_descendant_at_path(node, child_path, rebuilt_child)
                    self._replace_node_in_statute(node, rebuilt)
                    if recovery_rule_ids_out is not None:
                        recovery_rule_ids_out.append(
                            "uk_replay_definition_child_structured_text_rewrite_applied"
                        )
                    return rebuilt, True
                parent_path = child_path[:-1]
                child_index = child_path[-1]
                parent_node = _node_at_path(node, parent_path)
                new_children = list(parent_node.children)
                new_children.pop(child_index)
                rebuilt_parent = dc_replace(parent_node, children=new_children)
                rebuilt = (
                    rebuilt_parent
                    if not parent_path
                    else self._replace_descendant_at_path(node, parent_path, rebuilt_parent)
                )
                self._replace_node_in_statute(node, rebuilt)
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_definition_child_structured_text_rewrite_applied"
                    )
                return rebuilt, True

            if len(text_nodes) != 1:
                return node, False

            text_path, text_node = text_nodes[0]
            new_text, changed = _rewrite_flat_definition_child_ordinal_text(
                text_node.text or "",
                term=term,
                child_label=child_label,
                replacement=replacement,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            if not changed:
                return node, False
            replacement_node = dc_replace(text_node, text=new_text)
            if not text_path:
                self._replace_node_in_statute(text_node, replacement_node)
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_definition_child_flat_ordinal_text_rewrite_applied"
                    )
                return replacement_node, True
            rebuilt = self._replace_descendant_at_path(node, text_path, replacement_node)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append(
                    "uk_replay_definition_child_flat_ordinal_text_rewrite_applied"
                )
            return rebuilt, True

        if match.startswith(f"TEXT_IN_DEFINITION_CHILD_TAIL{US}"):
            selector_parts = _definition_child_tail_after_anchor_selector_parts(match)
            if selector_parts is None:
                return node, False

            rebuilt, applied, fallback_recovery_rule_ids = self._apply_unique_text_node_rewrite_with_metadata(
                node,
                text_nodes,
                lambda text: _rewrite_definition_child_tail_after_anchor_to_end_text(
                    text,
                    term=selector_parts.term,
                    child_label=selector_parts.child_label,
                    anchor=selector_parts.anchor,
                    replacement=replacement,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                ),
            )
            if not applied:
                return node, False
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.extend(fallback_recovery_rule_ids or ())
                recovery_rule_ids_out.append(
                    "uk_replay_definition_child_tail_after_anchor_to_end_text_rewrite_applied"
                )
            return rebuilt, True

        if match.startswith("TEXT_IN_DEFINITION_CHILD_"):
            child_selector = match[len("TEXT_IN_DEFINITION_CHILD_") :]
            child_parts = child_selector.split(US)
            child_after_anchor = ""
            child_final_connector = ""
            child_at_end = False
            if len(child_parts) == 4 and child_parts[2] == "AFTER":
                kind_and_term, child_label, _, child_after_anchor = child_parts
                original = ""
            elif len(child_parts) == 4 and child_parts[2] == "FINAL":
                kind_and_term, child_label, _, child_final_connector = child_parts
                original = ""
            elif len(child_parts) == 3:
                kind_and_term, child_label, original = child_parts
                child_at_end = original == "AT_END"
            else:
                return node, False
            child_match = re.fullmatch(r"([A-Z]+)_(.+)", kind_and_term)
            if child_match is None:
                return node, False
            child_kind = child_match.group(1).lower()
            term = child_match.group(2).strip()
            child_label = child_label.strip()
            original = original.strip()
            child_after_anchor = child_after_anchor.strip()
            child_final_connector = child_final_connector.strip().lower()
            if child_kind != "paragraph" or not term or not child_label:
                return node, False
            if (
                (child_after_anchor and original)
                or (child_final_connector and original)
                or (
                    not child_after_anchor
                    and not child_final_connector
                    and not original
                    and not child_at_end
                )
            ):
                return node, False

            structured_child_matches = _definition_child_nodes(
                node,
                term=term,
                child_label=child_label,
            )
            if child_final_connector:
                pattern = ""
            elif child_after_anchor:
                pattern = _text_patch_pattern(
                    child_after_anchor,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
            elif re.fullmatch(r"[A-Za-z0-9]+", original):
                pattern = rf"(?<![A-Za-z0-9]){re.escape(original)}(?![A-Za-z0-9])"
            else:
                pattern = _text_patch_pattern(
                    original,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
            replacement_text = replacement or ""
            if len(structured_child_matches) == 1:
                child_path, child_node = structured_child_matches[0]
                child_text = child_node.text or ""
                if not child_text:
                    return node, False
                if child_final_connector:
                    new_text, changed = _remove_final_definition_child_connector(
                        child_text,
                        child_final_connector,
                    )
                    if not changed:
                        return node, False
                elif child_at_end:
                    new_text = _append_definition_child_suffix_text(child_text, replacement_text)
                elif child_after_anchor:
                    required_occurrence = occurrence if occurrence > 0 else 1
                    matches = list(re.finditer(pattern, child_text, flags=re.I | re.S))
                    if len(matches) < required_occurrence:
                        return node, False
                    match_obj = matches[required_occurrence - 1]
                    new_text = (
                        child_text[: match_obj.start()]
                        + replacement_text
                        + child_text[match_obj.end() :]
                    )
                else:
                    new_text, count = re.subn(pattern, replacement_text, child_text, count=1, flags=re.I | re.S)
                    if count != 1:
                        return node, False
                rebuilt_child = dc_replace(child_node, text=" ".join(new_text.split()).strip())
                rebuilt = self._replace_descendant_at_path(node, child_path, rebuilt_child)
                self._replace_node_in_statute(node, rebuilt)
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_definition_child_final_connector_rewrite_applied"
                        if child_final_connector
                        else "uk_replay_in_definition_child_structured_text_rewrite_applied"
                    )
                return rebuilt, True

            if child_final_connector:
                return node, False

            candidate_rewrites: list[TextNodeRewriteCandidate] = []
            for text_path, text_node in text_nodes:
                if not text_node.text:
                    continue
                new_text, changed = _rewrite_flat_definition_child_inner_text(
                    text_node.text,
                    term=term,
                    child_label=child_label,
                    pattern=pattern,
                    replacement_text=replacement_text,
                    child_after_anchor=bool(child_after_anchor),
                    child_at_end=child_at_end,
                    occurrence=occurrence,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                if changed:
                    candidate_rewrites.append((text_path, text_node, new_text))
            if len(candidate_rewrites) != 1:
                return node, False
            text_path, text_node, new_text = candidate_rewrites[0]
            replacement_node = dc_replace(text_node, text=new_text)
            if not text_path:
                self._replace_node_in_statute(text_node, replacement_node)
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_in_definition_child_flat_ordinal_text_rewrite_applied"
                    )
                return replacement_node, True
            rebuilt = self._replace_descendant_at_path(node, text_path, replacement_node)
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append(
                    "uk_replay_in_definition_child_flat_ordinal_text_rewrite_applied"
                )
            return rebuilt, True

        if match.startswith("TEXT_AFTER_DEFINITION_"):
            definition_child_match = re.fullmatch(
                r"TEXT_AFTER_DEFINITION_([A-Z]+)_(.*)_AFTER_([0-9A-Za-z]+)",
                match,
            )
            if definition_child_match is not None:
                child_kind = definition_child_match.group(1).lower()
                term = definition_child_match.group(2).strip()
                child_label = definition_child_match.group(3).strip()
                if child_kind != "paragraph" or not term or not child_label:
                    return node, False

                structured_child_matches = _definition_child_nodes(
                    node,
                    term=term,
                    child_label=child_label,
                )
                if len(structured_child_matches) == 1:
                    child_path, child_node = structured_child_matches[0]
                    parent_path = child_path[:-1]
                    child_index = child_path[-1]

                    anchor_suffix, inserted_children = _definition_child_insert_payload(
                        replacement,
                        term=term,
                    )
                    if inserted_children:
                        parent_node = _node_at_path(node, parent_path)
                        new_children = list(parent_node.children)
                        if anchor_suffix:
                            anchor_text = " ".join(f"{child_node.text.rstrip()} {anchor_suffix}".split()).strip()
                            new_children[child_index] = dc_replace(child_node, text=anchor_text)
                        new_children[child_index + 1 : child_index + 1] = inserted_children
                        rebuilt_parent = dc_replace(parent_node, children=new_children)
                        rebuilt = (
                            rebuilt_parent
                            if not parent_path
                            else self._replace_descendant_at_path(node, parent_path, rebuilt_parent)
                        )
                        self._replace_node_in_statute(node, rebuilt)
                        if recovery_rule_ids_out is not None:
                            recovery_rule_ids_out.append(
                                "uk_replay_after_definition_child_structured_insert_applied"
                            )
                        return rebuilt, True

                full_text = " ".join(tn.text.strip() for _, tn in text_nodes if tn.text).strip()
                if not full_text:
                    return node, False
                term_pattern = re.escape(term).replace(r"\ ", r"\s+")
                definition_match = re.search(
                    rf"[“\"'‘]?\s*{term_pattern}\s*[”\"'’]?.*?\bmeans\b",
                    full_text,
                    flags=re.I | re.S,
                )
                if definition_match is None:
                    return node, False
                if len(child_label) == 1 and child_label.isalpha():
                    semicolon_ordinal = ord(child_label.lower()) - ord("a") + 1
                elif child_label.isdigit():
                    semicolon_ordinal = int(child_label)
                else:
                    return node, False
                tail = full_text[definition_match.end() :]
                semicolons = list(re.finditer(r";", tail))
                if len(semicolons) < semicolon_ordinal:
                    return node, False
                insert_at = definition_match.end() + semicolons[semicolon_ordinal - 1].end()
                joiner = "" if replacement.startswith((" ", ",", ".", ";", ":", ")")) else " "
                new_text = f"{full_text[:insert_at]}{joiner}{replacement}{full_text[insert_at:]}"
                rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
                self._replace_node_in_statute(node, rebuilt)
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_after_definition_child_flat_ordinal_insert_applied"
                    )
                return rebuilt, True

            term = match[len("TEXT_AFTER_DEFINITION_") :].strip()
            if not term:
                return node, False

            rebuilt, applied, recovery_rule_ids = self._apply_unique_text_node_rewrite_with_metadata(
                node,
                text_nodes,
                lambda text: _insert_after_definition_text(
                    text,
                    term=term,
                    replacement=replacement,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                ),
            )
            if not applied:
                return node, False
            if recovery_rule_ids and recovery_rule_ids_out is not None:
                recovery_rule_ids_out.extend(recovery_rule_ids)
            return rebuilt, True

        if match.startswith("TEXT_DEFINITION_ENTRY_"):
            term = match[len("TEXT_DEFINITION_ENTRY_") :].strip()
            if not term:
                return node, False

            rebuilt, applied, definition_recovery_rule_ids = (
                self._apply_unique_text_node_rewrite_with_metadata(
                    node,
                    text_nodes,
                    lambda text: _rewrite_definition_entry_text(
                        text,
                        term=term,
                        replacement=replacement,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    ),
                )
            )
            if not applied:
                return node, False
            if definition_recovery_rule_ids and recovery_rule_ids_out is not None:
                recovery_rule_ids_out.extend(definition_recovery_rule_ids)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_definition_entry_text_rewrite_applied")
            return rebuilt, True

        if match.startswith("TEXT_SENTENCE_"):
            sentence_ordinal_text = match[len("TEXT_SENTENCE_") :].strip()
            sentence_beginning = ""
            if US in sentence_ordinal_text:
                sentence_parts = sentence_ordinal_text.split(US)
                if len(sentence_parts) != 3 or sentence_parts[1] != "BEGINNING":
                    return node, False
                sentence_ordinal_text = sentence_parts[0]
                sentence_beginning = sentence_parts[2]
            if not sentence_ordinal_text.isdigit():
                return node, False
            sentence_ordinal = int(sentence_ordinal_text)
            if sentence_beginning:
                rebuilt, applied = self._apply_unique_text_node_rewrite(
                    node,
                    text_nodes,
                    lambda text: _delete_ordinal_sentence_beginning(
                        text,
                        ordinal=sentence_ordinal,
                        beginning=sentence_beginning,
                    ),
                )
                if not applied:
                    return node, False
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_ordinal_sentence_beginning_text_rewrite_applied"
                    )
                return rebuilt, True
            rebuilt, applied = self._apply_unique_text_node_rewrite(
                node,
                text_nodes,
                lambda text: _delete_ordinal_sentence(text, sentence_ordinal),
            )
            if not applied:
                return node, False
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_ordinal_sentence_text_rewrite_applied")
            return rebuilt, True

        if match.startswith("TEXT_WORD_"):
            target_contextual_match = re.fullmatch(
                r"TEXT_WORD_(.*?)_IMMEDIATELY_FOLLOWING_TARGET",
                match,
            )
            if target_contextual_match is not None:
                word = target_contextual_match.group(1)
                new_text, changed = _remove_trailing_context_word(node.text or "", word)
                if not changed:
                    return node, False
                rebuilt = dc_replace(node, text=new_text)
                self._replace_node_in_statute(node, rebuilt)
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append("uk_replay_contextual_word_text_rewrite_applied")
                return rebuilt, True

            contextual_match = re.fullmatch(
                r"TEXT_WORD_(.*?)_IMMEDIATELY_(PRECEDING|FOLLOWING)_([A-Za-z]+)_([0-9A-Za-z]+)",
                match,
            )
            if contextual_match is None:
                return node, False
            word = contextual_match.group(1)
            relation = contextual_match.group(2)
            anchor_kind = contextual_match.group(3)
            anchor_label = contextual_match.group(4)
            anchor_path = _find_descendant_path_by_kind_label(
                node,
                kind=anchor_kind,
                label=anchor_label,
            )
            recovered_anchor_kind = False
            if anchor_path is None:
                allowed_anchor_kinds = {
                    "paragraph",
                    "subparagraph",
                    "item",
                    "point",
                }
                if anchor_kind.lower() not in allowed_anchor_kinds:
                    return node, False

                candidate_paths = _collect_descendant_paths_by_label_and_kinds(
                    node,
                    label=anchor_label,
                    allowed_kinds=allowed_anchor_kinds,
                )
                if len(candidate_paths) != 1:
                    return node, False
                anchor_path = candidate_paths[0]
                recovered_anchor_kind = True
            target_path = anchor_path
            if relation == "PRECEDING":
                if not anchor_path:
                    return node, False
                sibling_idx = anchor_path[-1] - 1
                if sibling_idx < 0:
                    return node, False
                target_path = anchor_path[:-1] + (sibling_idx,)
            target_node = _node_at_path(node, target_path)
            new_text, changed = _remove_trailing_context_word(target_node.text or "", word)
            if not changed:
                return node, False
            if recovered_anchor_kind and recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_contextual_word_anchor_kind_normalized")
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_contextual_word_text_rewrite_applied")
            rebuilt = self._replace_descendant_at_path(
                node,
                target_path,
                dc_replace(target_node, text=new_text),
            )
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_AFTER_") and match.endswith("_TO_END"):
            anchor = match[len("TEXT_AFTER_") : -len("_TO_END")]
            if not anchor:
                return node, False

            rebuilt, applied = self._apply_unique_text_node_rewrite(
                node,
                text_nodes,
                lambda text: _rewrite_after_anchor_to_end_text(
                    text,
                    anchor=anchor,
                    replacement=replacement,
                    occurrence=occurrence,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                ),
            )
            if not applied:
                return node, False
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_after_anchor_to_end_text_rewrite_applied")
            return rebuilt, True

        if match.startswith("TEXT_FROM_"):
            if node.text and ("_TO_" in match and not match.endswith("_TO_END") or not node.children):
                rebuilt, applied = self._apply_text_replace_on_node_text_only(
                    node,
                    match,
                    replacement,
                    occurrence,
                    end_occurrence,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                    recovery_rule_ids_out=recovery_rule_ids_out,
                )
                if applied:
                    return rebuilt, True

            if node.text and node.children and match.endswith("_TO_END"):
                rebuilt, applied = self._apply_text_replace_on_marked_post_child_tail(
                    node,
                    match,
                    replacement,
                    occurrence,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                    recovery_rule_ids_out=recovery_rule_ids_out,
                )
                if applied:
                    return rebuilt, True

            full_text = " ".join(tn.text.strip() for _, tn in text_nodes if tn.text).strip()
            if not full_text:
                return node, False

            if match.endswith("_TO_END"):
                start_text = match[len("TEXT_FROM_") : -len("_TO_END")]
                start_result = _find_text_range_start_index(
                    full_text,
                    start_text,
                    occurrence=occurrence,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                if start_result.recovery_rule_ids and recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.extend(start_result.recovery_rule_ids)
                if start_result.start_index == -1:
                    return node, False
                new_text = full_text[: start_result.start_index] + replacement
                rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
                self._replace_node_in_statute(node, rebuilt)
                if recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append("uk_replay_subtree_range_to_end_text_rewrite_flattened")
                return rebuilt, True

            if "_TO_" in match:
                parts = match.replace("TEXT_FROM_", "", 1).split("_TO_", 1)
                if len(parts) == 2:
                    start_text, end_text = parts[0], parts[1]
                    if start_text:
                        start_result = _find_text_range_start_index(
                            full_text,
                            start_text,
                            occurrence=occurrence,
                            allow_punctuation_spacing=allow_punctuation_spacing,
                            allow_word_punctuation_elision=allow_word_punctuation_elision,
                        )
                        start_idx = start_result.start_index
                        if start_result.recovery_rule_ids and recovery_rule_ids_out is not None:
                            recovery_rule_ids_out.extend(start_result.recovery_rule_ids)
                        start_len = len(start_text)
                    else:
                        start_idx = 0
                        start_len = 0
                    end_idx = -1
                    if start_idx != -1:
                        if end_occurrence > 0:
                            end_matches, used_word_end = _range_anchor_matches(full_text, end_text)
                            if len(end_matches) >= end_occurrence:
                                end_match = end_matches[end_occurrence - 1]
                                if end_match.start() >= start_idx + start_len:
                                    end_idx = end_match.start()
                                    end_end = end_match.end()
                                    if used_word_end and recovery_rule_ids_out is not None:
                                        recovery_rule_ids_out.append(
                                            "uk_replay_text_range_anchor_word_boundary_normalized"
                                        )
                        else:
                            end_idx = full_text.find(end_text, start_idx + start_len)
                            end_end = end_idx + len(end_text)
                    if start_idx == -1 or end_idx == -1:
                        if start_text:
                            start_pattern = _text_patch_pattern(
                                start_text,
                                allow_punctuation_spacing=allow_punctuation_spacing,
                                allow_word_punctuation_elision=allow_word_punctuation_elision,
                            )
                            start_matches = list(re.finditer(start_pattern, full_text, flags=re.I | re.S))
                            ordinal = occurrence if occurrence > 0 else 1
                            if len(start_matches) < ordinal:
                                return node, False
                            start_match = start_matches[ordinal - 1]
                            start_start = start_match.start()
                            start_end = start_match.end()
                        else:
                            start_pattern = ""
                            start_start = 0
                            start_end = 0
                        if end_occurrence > 0:
                            end_pattern = _text_patch_pattern(
                                end_text,
                                allow_punctuation_spacing=allow_punctuation_spacing,
                                allow_word_punctuation_elision=allow_word_punctuation_elision,
                            )
                            end_matches = list(re.finditer(end_pattern, full_text, flags=re.I | re.S))
                            if len(end_matches) < end_occurrence:
                                return node, False
                            end_match = end_matches[end_occurrence - 1]
                            if end_match.start() < start_end:
                                return node, False
                            new_text = full_text[:start_start] + replacement + full_text[end_match.end() :]
                        else:
                            if start_text:
                                pattern = (
                                    start_pattern
                                    + r".*?"
                                    + _text_patch_pattern(
                                        end_text,
                                        allow_punctuation_spacing=allow_punctuation_spacing,
                                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                                    )
                                )
                                m = re.search(pattern, full_text, flags=re.I | re.S)
                                if not m:
                                    return node, False
                                new_text = full_text[: m.start()] + replacement + full_text[m.end() :]
                            else:
                                end_pattern = _text_patch_pattern(
                                    end_text,
                                    allow_punctuation_spacing=allow_punctuation_spacing,
                                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                                )
                                m = re.search(end_pattern, full_text, flags=re.I | re.S)
                                if not m:
                                    return node, False
                                new_text = replacement + full_text[m.end() :]
                    else:
                        new_text = full_text[:start_idx] + replacement + full_text[end_end:]
                    rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
                    self._replace_node_in_statute(node, rebuilt)
                    if recovery_rule_ids_out is not None:
                        recovery_rule_ids_out.append("uk_replay_subtree_range_text_rewrite_flattened")
                    return rebuilt, True

        if occurrence == -1:
            last_exact_match: Optional[TextNodeExactMatch] = None
            for path, tn in text_nodes:
                start = 0
                while True:
                    pos = tn.text.find(match, start)
                    if pos == -1:
                        break
                    last_exact_match = (path, tn, pos)
                    start = pos + len(match)
            if last_exact_match is not None:
                path, tn, pos = last_exact_match
                rebuilt = self._replace_descendant_at_path(
                    node,
                    path,
                    dc_replace(tn, text=tn.text[:pos] + replacement + tn.text[pos + len(match) :]),
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            pattern = _text_patch_pattern(
                match,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            last_normalized_match: Optional[TextNodeRegexMatch] = None
            for path, tn in text_nodes:
                for m in re.finditer(pattern, tn.text, flags=re.I):
                    last_normalized_match = (path, tn, m)
            if last_normalized_match is not None:
                path, tn, m = last_normalized_match
                rebuilt = self._replace_descendant_at_path(
                    node,
                    path,
                    dc_replace(tn, text=tn.text[: m.start()] + replacement + tn.text[m.end() :]),
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            return node, False

        if occurrence == 0:
            # Replace all occurrences across all text nodes
            made_any = False
            rebuilt = node
            for path, tn in text_nodes:
                text = tn.text
                if match in text:
                    rebuilt = self._replace_descendant_at_path(
                        rebuilt,
                        path,
                        dc_replace(tn, text=text.replace(match, replacement)),
                    )
                    made_any = True
                else:
                    # Whitespace-normalized fallback (same as _apply_text_substitution_on_node)
                    pattern = _text_patch_pattern(
                        match,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    new_text, count = re.subn(pattern, replacement, text, flags=re.I)
                    if count > 0:
                        rebuilt = self._replace_descendant_at_path(
                            rebuilt,
                            path,
                            dc_replace(tn, text=new_text),
                        )
                        made_any = True
            if made_any:
                self._replace_node_in_statute(node, rebuilt)
            return rebuilt, made_any
        else:
            # Replace only the Nth occurrence (1-based) — count across all text nodes in order
            global_count = 0
            for path, tn in text_nodes:
                text = tn.text
                # Count occurrences in this node's text
                start = 0
                while True:
                    pos = text.find(match, start)
                    if pos == -1:
                        break
                    global_count += 1
                    if global_count == occurrence:
                        rebuilt = self._replace_descendant_at_path(
                            node,
                            path,
                            dc_replace(tn, text=text[:pos] + replacement + text[pos + len(match) :]),
                        )
                        self._replace_node_in_statute(node, rebuilt)
                        return rebuilt, True
                    start = pos + len(match)
            # Whitespace-normalized fallback if exact search found nothing
            if global_count == 0:
                pattern = _text_patch_pattern(
                    match,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                nth_seen = 0
                for path, tn in text_nodes:
                    for m in re.finditer(pattern, tn.text, flags=re.I):
                        nth_seen += 1
                        if nth_seen == occurrence:
                            rebuilt = self._replace_descendant_at_path(
                                node,
                                path,
                                dc_replace(tn, text=tn.text[: m.start()] + replacement + tn.text[m.end() :]),
                            )
                            self._replace_node_in_statute(node, rebuilt)
                            return rebuilt, True
            return node, False
