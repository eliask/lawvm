"""UK replay text matching and patch-splice helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

from lawvm.uk_legislation.mutable_ir import UKMutableNode


_WORD_PUNCTUATION_ELISION_CANDIDATE_RE = re.compile(
    r"(?<=[A-Za-z0-9_])['’‘\-‐‑‒–—](?=[A-Za-z0-9_])"
)
_ROTATED_TRAILING_COMMA_BODY_RE = re.compile(r"[A-Za-z][A-Za-z0-9 ]{1,80}")
_NUMERIC_LIST_TRAILING_COMMA_ANCHOR_RE = re.compile(r"\s*(?P<anchor>\d+[A-Za-z]?)\s*,\s*")


@dataclass(frozen=True, slots=True)
class UKNumericListTrailingCommaAnchorPattern:
    anchor: str
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class UKNumericListTrailingCommaTextReplacement:
    new_text: str
    anchor: str


@dataclass(frozen=True, slots=True)
class UKNumericListTrailingCommaSubtreeReplacement:
    path: tuple[int, ...]
    new_text: str
    anchor: str


@dataclass(frozen=True, slots=True)
class _UKTextNodeWalkEntry:
    path: tuple[int, ...]
    node: UKMutableNode


@dataclass(frozen=True, slots=True)
class _UKTextMatchCandidate:
    path: tuple[int, ...]
    node: UKMutableNode
    match: re.Match[str]


def _normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching (squash whitespace)."""
    if not text:
        return ""
    return " ".join(text.replace("\u00a0", " ").split()).lower()


def _text_patch_pattern(
    match: str,
    *,
    allow_punctuation_spacing: bool = False,
    allow_word_punctuation_elision: bool = False,
) -> str:
    """Build a conservative text-patch regex from an effect-feed match string."""
    pattern = re.escape(match).replace(r"\ ", r"\s+")
    if allow_punctuation_spacing:
        # UK effects sometimes omit a space in citation forms like "c.14" while
        # the source text has "c. 14". Keep this narrow: only allow space after
        # an escaped full stop when a word character precedes and a digit follows.
        pattern = re.sub(
            r"(?<=[A-Za-z0-9_])\\\.(?:\\s\+)?(?=\d)",
            lambda _match: r"\.\s*",
            pattern,
        )
        # Effect-feed selectors can carry a trailing space before punctuation
        # that belongs to the host provision, not the selected source phrase.
        pattern = re.sub(
            r"\\s\+$",
            lambda _match: r"\s*(?=[\.,;:]|\s|$)",
            pattern,
        )
        # Some UK XML text surfaces elide the space before an inline quoted
        # term, e.g. "the“nominated..." while the effect source quotes
        # "the “nominated...".
        pattern = re.sub(
            r"\\s\+(?=[“\"'‘])",
            lambda _match: r"\s*",
            pattern,
        )
        # Parsed UK XML can also surface compact subsection citations as
        # "2 (1)" while the effect feed quotes "2(1)". Keep this bounded to
        # word/digit citation tokens and only as an explicit replay recovery.
        pattern = re.sub(
            r"(?<=[A-Za-z0-9_])\\\((?=[A-Za-z0-9_])",
            lambda _match: r"\s*\(",
            pattern,
        )
    if allow_word_punctuation_elision:
        # Some UK source/XML text surfaces lose word-internal punctuation while
        # effect text retains it, e.g. "tenant's son-in-law" vs
        # "tenants soninlaw". Keep this bounded to apostrophe/hyphen-like marks
        # between word characters; it must not match whitespace or arbitrary
        # punctuation differences.
        pattern = re.sub(
            r"(?<=[A-Za-z0-9_])(?:'|\\'|’|‘)(?=[A-Za-z0-9_])",
            lambda _match: r"['’‘]?",
            pattern,
        )
        pattern = re.sub(
            r"(?<=[A-Za-z0-9_])(?:\\-|‐|‑|‒|–|—)(?=[A-Za-z0-9_])",
            lambda _match: r"[-‐‑‒–—]?",
            pattern,
        )
    return pattern


def _text_match_has_word_punctuation_elision_candidate(match: str) -> bool:
    """Return whether a match string contains recoverable word-internal punctuation."""
    if not match:
        return False
    return bool(_WORD_PUNCTUATION_ELISION_CANDIDATE_RE.search(match))


def _walk_text_nodes(node: UKMutableNode) -> list[_UKTextNodeWalkEntry]:
    text_nodes: list[_UKTextNodeWalkEntry] = []
    stack: list[_UKTextNodeWalkEntry] = [_UKTextNodeWalkEntry((), node)]
    while stack:
        walk_entry = stack.pop()
        path = walk_entry.path
        current = walk_entry.node
        if current.text:
            text_nodes.append(_UKTextNodeWalkEntry(path, current))
        for index in range(len(current.children) - 1, -1, -1):
            stack.append(_UKTextNodeWalkEntry(path + (index,), current.children[index]))
    return text_nodes


def _node_text_patch_preimage_present(
    node: UKMutableNode,
    match: str,
    occurrence: int,
    end_occurrence: int = 0,
) -> bool:
    """Preflight simple node-local text patches used for multi-cell table edits."""
    if occurrence != 0 or end_occurrence != 0:
        return False
    text = node.text or ""
    if not text or not match:
        return False
    if match in text:
        return True
    pattern = _text_patch_pattern(match)
    return re.search(pattern, text, flags=re.I) is not None


def _rotated_trailing_comma_omission_match(match: str, node: UKMutableNode) -> Optional[str]:
    """Return a unique `X` preimage for a quoted omission selector shaped as `X,`.

    Some UK effect/source surfaces quote the logical omitted phrase with a
    trailing comma even when the replay text carries that comma before the
    phrase, e.g. omit "Part 4," against "... offences), Part 4 is amended".
    This is not a general normalized-text recovery: only simple alphanumeric
    phrases are eligible, and the rotated preimage must occur exactly once in
    the explicit target subtree. The host comma is preserved.
    """

    normalized_match = " ".join((match or "").split())
    if not normalized_match.endswith(","):
        return None
    body = normalized_match[:-1].strip()
    if _ROTATED_TRAILING_COMMA_BODY_RE.fullmatch(body) is None:
        return None

    body_pattern = re.escape(body).replace(r"\ ", r"\s+")
    rotated_pattern = re.compile(
        rf",\s+(?P<delete>{body_pattern}(?![A-Za-z0-9])\s*)",
        flags=re.I,
    )
    body_pattern_re = re.compile(rf"(?<![A-Za-z0-9]){body_pattern}(?![A-Za-z0-9])", flags=re.I)
    rotated_matches: list[str] = []
    body_matches = 0
    for walk_entry in _walk_text_nodes(node):
        text = walk_entry.node.text or ""
        rotated_matches.extend(match_obj.group("delete") for match_obj in rotated_pattern.finditer(text))
        body_matches += len(tuple(body_pattern_re.finditer(text)))

    if len(rotated_matches) != 1 or body_matches != 1:
        return None
    return rotated_matches[0]


def _numeric_list_trailing_comma_anchor_pattern(
    match: str,
    replacement: str | None,
) -> UKNumericListTrailingCommaAnchorPattern | None:
    """Return a bounded pattern for insertion anchors quoted as `28,`."""

    match_obj = _NUMERIC_LIST_TRAILING_COMMA_ANCHOR_RE.fullmatch(match or "")
    if match_obj is None:
        return None
    anchor = match_obj.group("anchor")
    replacement_text = (replacement or "").lstrip()
    if re.match(rf"{re.escape(anchor)}\s*,", replacement_text, flags=re.I) is None:
        return None
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(anchor)}(?![A-Za-z0-9])(?=\s+(?:and|or)\b)",
        flags=re.I,
    )
    return UKNumericListTrailingCommaAnchorPattern(anchor=anchor, pattern=pattern)


def _numeric_list_trailing_comma_replacement_text(
    text: str,
    match: str,
    replacement: str,
    occurrence: int,
    end_occurrence: int,
) -> UKNumericListTrailingCommaTextReplacement | None:
    if occurrence not in (0, 1) or end_occurrence:
        return None
    anchor_pattern = _numeric_list_trailing_comma_anchor_pattern(match, replacement)
    if anchor_pattern is None:
        return None
    if not text or match in text:
        return None
    matches = list(anchor_pattern.pattern.finditer(text))
    if len(matches) != 1:
        return None
    return UKNumericListTrailingCommaTextReplacement(
        new_text=_splice_text_match_replacement(text, matches[0], replacement),
        anchor=anchor_pattern.anchor,
    )


def _numeric_list_trailing_comma_subtree_replacement(
    node: UKMutableNode,
    match: str,
    replacement: str,
    occurrence: int,
    end_occurrence: int,
) -> UKNumericListTrailingCommaSubtreeReplacement | None:
    if occurrence not in (0, 1) or end_occurrence:
        return None
    anchor_pattern = _numeric_list_trailing_comma_anchor_pattern(match, replacement)
    if anchor_pattern is None:
        return None
    text_nodes = _walk_text_nodes(node)
    if any(match in text_node.node.text for text_node in text_nodes):
        return None
    matches: list[_UKTextMatchCandidate] = []
    for text_node in text_nodes:
        matches.extend(
            _UKTextMatchCandidate(text_node.path, text_node.node, match_obj)
            for match_obj in anchor_pattern.pattern.finditer(text_node.node.text)
        )
    if len(matches) != 1:
        return None
    match_candidate = matches[0]
    return UKNumericListTrailingCommaSubtreeReplacement(
        path=match_candidate.path,
        new_text=_splice_text_match_replacement(match_candidate.node.text, match_candidate.match, replacement),
        anchor=anchor_pattern.anchor,
    )


def _splice_text_match_replacement(text: str, match_obj: re.Match[str], replacement: str) -> str:
    """Splice replacement while avoiding duplicate whitespace at the join."""

    applied_replacement = replacement
    if applied_replacement and applied_replacement[-1].isspace() and text[match_obj.end() :].startswith(
        (" ", "\t", "\n", "\r")
    ):
        applied_replacement = applied_replacement.rstrip()
    return f"{text[: match_obj.start()]}{applied_replacement}{text[match_obj.end() :]}"
