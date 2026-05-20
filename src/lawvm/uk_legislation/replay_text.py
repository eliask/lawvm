"""UK replay text normalization and selector checks."""
from __future__ import annotations

import re
from typing import Any

from lawvm.core.ir import IRNode
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.uk_grafter import _clean_num


def _normalize_text_for_grounding(text: str) -> str:
    """Normalize text for grounding similarity checks."""
    text = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(text.split())


def _normalized_replay_subtree_text(node: IRNode | UKMutableNode) -> str:
    parts: list[str] = []
    if node.text:
        parts.append(str(node.text).strip())
    for child in node.children:
        child_text = _normalized_replay_subtree_text(child)
        if child_text:
            parts.append(child_text)
    return _normalize_text_for_grounding(" ".join(parts))


def _compact_normalized_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _compact_schedule_entry_anchor_without_article(text: str) -> str:
    stripped = re.sub(
        r"^(?:the|a|an)\s+",
        "",
        " ".join(str(text or "").split()),
        flags=re.I,
    )
    return _compact_normalized_text(stripped)


def _compact_schedule_entry_anchor_with_citation_short_title(text: str) -> str:
    """Normalize long UK Act title references to same-context year Act aliases."""
    stripped = " ".join(str(text or "").split())
    shortened = re.sub(
        r"\b(of|under|by|in|for)\s+the\s+[A-Z][A-Za-z0-9&(),.\-'’ ]{3,}?\s+Act\s+(\d{4})\b",
        r"\1 the \2 Act",
        stripped,
    )
    if shortened == stripped:
        return ""
    return _compact_normalized_text(shortened)


def _compact_numbered_schedule_entry_text(text: str) -> str:
    stripped = re.sub(
        r"^\s*\(?[0-9]+[A-Za-z]?\)?\.?\s+",
        "",
        " ".join(str(text or "").split()),
        count=1,
    )
    return _compact_normalized_text(stripped)


def _compact_numbered_schedule_entry_text_without_article(text: str) -> str:
    stripped = re.sub(
        r"^\s*\(?[0-9]+[A-Za-z]?\)?\.?\s+",
        "",
        " ".join(str(text or "").split()),
        count=1,
    )
    return _compact_schedule_entry_anchor_without_article(stripped)


def _schedule_entry_parenthetical_paragraph_anchor(anchor: str) -> tuple[str, str] | None:
    match = re.fullmatch(
        r"\s*(?P<entry>.+?)\s*\(\s*paragraph\s+(?P<label>[0-9A-Za-z]+)\s*\)\s*",
        str(anchor or ""),
        flags=re.I,
    )
    if match is None:
        return None
    entry = " ".join(match.group("entry").split())
    label = _clean_num(match.group("label"))
    if not entry or not label:
        return None
    return entry, label


def _append_definition_child_suffix_text(child_text: str, suffix: str) -> str:
    base = " ".join((child_text or "").split()).strip()
    addition = " ".join((suffix or "").split()).strip()
    if not base:
        return addition
    if not addition:
        return base
    if base.rstrip().endswith(";") and addition.startswith(";"):
        addition = addition[1:].lstrip()
    return f"{base.rstrip()} {addition}".strip()


def _lowering_record_rule_ids(rows: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    """Return stable non-empty lowering rule ids for evidence rows."""
    return tuple(sorted({str(row.get("rule_id") or "unknown") for row in rows}))


def _text_patch_replacement_preserves_anchor(match_text: str, replacement: str | None) -> bool:
    match_norm = _compact_normalized_text(match_text)
    replacement_norm = _compact_normalized_text(replacement or "")
    if len(match_norm) < 3 or not replacement_norm:
        return False
    return replacement_norm.startswith(match_norm) or replacement_norm.endswith(match_norm)


def _monetary_amount_text_selector(text: str) -> bool:
    return bool(re.search(r"(?:\u00a3\s*\d|\b\d[\d,]*(?:\.\d+)?\s*(?:pounds?|sterling)\b)", text or "", re.I))


def _parenthetical_omission_text_selector(text: str) -> bool:
    stripped = (text or "").strip()
    if not (stripped.startswith("(") and stripped.endswith(")")):
        return False
    return bool(re.search(r"[A-Za-z]{3,}", stripped))


def _citation_connector_elided_text_match_present(match_text: str, node: IRNode | UKMutableNode) -> bool:
    text = match_text or ""
    if not re.search(r"\bor\b", text, re.I):
        return False
    citation_refs = re.findall(r"\b\d+[A-Za-z]?\s*\(", text)
    if len(citation_refs) < 2:
        return False
    without_connectors = re.sub(r"\bor\b", "", text, flags=re.I)
    selector_norm = _compact_normalized_text(without_connectors)
    if len(selector_norm) < 6 or selector_norm == _compact_normalized_text(text):
        return False
    target_norm = _compact_normalized_text(_normalized_replay_subtree_text(node))
    return bool(target_norm) and selector_norm in target_norm


def _article_phrase_content_word_present(match_text: str, node: IRNode | UKMutableNode) -> bool:
    stripped = (match_text or "").strip()
    article_match = re.fullmatch(r"(?:a|an|the)\s+([A-Za-z][A-Za-z-]{3,})", stripped, re.I)
    if article_match is None:
        return False
    content_norm = _compact_normalized_text(article_match.group(1))
    target_norm = _compact_normalized_text(_normalized_replay_subtree_text(node))
    return bool(content_norm) and content_norm in target_norm


def _normalized_text_match_present(match_text: str, node: IRNode | UKMutableNode) -> bool:
    match_norm = _compact_normalized_text(match_text)
    if len(match_norm) < 3:
        return False
    target_norm = _compact_normalized_text(_normalized_replay_subtree_text(node))
    return bool(target_norm) and match_norm in target_norm


def _definition_entry_term_absent(match_text: str, node: IRNode | UKMutableNode) -> bool:
    if not match_text.startswith("TEXT_DEFINITION_ENTRY_"):
        return False
    term = match_text[len("TEXT_DEFINITION_ENTRY_") :].strip()
    term_norm = _compact_normalized_text(term)
    if len(term_norm) < 3:
        return False
    target_norm = _compact_normalized_text(_normalized_replay_subtree_text(node))
    return bool(target_norm) and term_norm not in target_norm


def _normalized_replacement_text_present(replacement_text: str, node: IRNode | UKMutableNode) -> bool:
    replacement_norm = _compact_normalized_text(replacement_text)
    if len(replacement_norm) < 3:
        return False
    target_norm = _compact_normalized_text(_normalized_replay_subtree_text(node))
    return bool(target_norm) and replacement_norm in target_norm


def _citation_stripped_text_match_present(match_text: str, node: IRNode | UKMutableNode) -> bool:
    text = match_text or ""
    if not re.search(r"\b(?:Act|Measure|Order|Regulations)\s+\d{4}\b|\(\s*c\.\s*\d+", text, re.I):
        return False
    stripped = re.sub(r"\(\s*c\.\s*\d+[a-z]?\s*\)", "", text, flags=re.I)
    stripped = re.sub(r"\b(Act|Measure|Order|Regulations)\s+\d{4}\b", r"\1", stripped, flags=re.I)
    stripped_norm = _compact_normalized_text(stripped)
    if len(stripped_norm) < 12 or stripped_norm == _compact_normalized_text(text):
        return False
    target_norm = _compact_normalized_text(_normalized_replay_subtree_text(node))
    return bool(target_norm) and stripped_norm in target_norm


def _non_substantive_text_selector(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if re.fullmatch(r"[\W_]+", stripped):
        return True
    compact = _compact_normalized_text(stripped)
    return compact in {"", "none", "nil"}


def _multi_fragment_text_selector(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    quote = r"[\"'“”‘’]"
    return bool(
        re.search(rf"{quote}\s*,\s*{quote}", stripped)
        or re.search(rf"{quote}\s+(?:and|or)\s+{quote}", stripped, re.I)
        or re.search(rf",\s*{quote}", stripped)
    )


def _synthetic_text_selector(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped.startswith("TEXT_") or bool(re.match(r"^FROM_.+_TO_", stripped))


def _replay_subtree_text_preview(node: IRNode | UKMutableNode, *, limit: int = 240) -> str:
    parts: list[str] = []
    if node.text:
        parts.append(str(node.text).strip())
    for child in node.children:
        child_text = _replay_subtree_text_preview(child, limit=limit)
        if child_text:
            parts.append(child_text)
        if sum(len(part) for part in parts) >= limit:
            break
    return " ".join(" ".join(parts).split())[:limit]
