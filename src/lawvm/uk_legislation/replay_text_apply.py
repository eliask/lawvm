from __future__ import annotations

import re
from dataclasses import replace as dc_replace
from typing import Optional

from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.replay_text import _range_anchor_matches
from lawvm.uk_legislation.text_matching import (
    _numeric_list_trailing_comma_replacement_text,
    _numeric_list_trailing_comma_subtree_replacement,
    _text_patch_pattern,
)


class UKReplayTextApplyMixin:
    def _apply_numeric_list_trailing_comma_anchor_on_node_text_only(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int,
    ) -> tuple[UKMutableNode, bool, str | None]:
        """Recover a unique numeric list item whose source selector adds a comma."""

        text = node.text or ""
        new_text, anchor = _numeric_list_trailing_comma_replacement_text(
            text,
            match,
            replacement,
            occurrence,
            end_occurrence,
        )
        if new_text is None or anchor is None:
            return node, False, None
        rebuilt = dc_replace(node, text=new_text)
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True, anchor

    def _apply_numeric_list_trailing_comma_anchor_on_subtree(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int = 0,
    ) -> tuple[UKMutableNode, bool, str | None]:
        """Recover one unique numeric list item across a resolved target subtree."""

        subtree_replacement = _numeric_list_trailing_comma_subtree_replacement(
            node,
            match,
            replacement,
            occurrence,
            end_occurrence,
        )
        if subtree_replacement is None:
            return node, False, None
        path, new_text, anchor = subtree_replacement
        text_node = node
        for index in path:
            text_node = text_node.children[index]
        rebuilt = self._replace_descendant_at_path(
            node,
            path,
            dc_replace(
                text_node,
                text=new_text,
            ),
        )
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True, anchor

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
        if match == "TEXT_ALL":
            rebuilt = dc_replace(node, text=replacement)
            self._replace_node_in_statute(node, rebuilt)
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
            rebuilt = dc_replace(node, text=f"{text[: start_match.start()]}{replacement}")
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        if match.startswith("TEXT_FROM_") and "_TO_" in match:
            start_text, end_text = match.replace("TEXT_FROM_", "", 1).split("_TO_", 1)
            if not start_text or not end_text:
                return node, False
            start_ordinal = occurrence if occurrence > 0 else 1
            end_ordinal = end_occurrence if end_occurrence > 0 else 0
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
            if end_ordinal:
                end_matches, used_word_end = _range_anchor_matches(text, end_text)
                if len(end_matches) >= end_ordinal:
                    end_match = end_matches[end_ordinal - 1]
                    if end_match.start() < start_match.end():
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
                    if end_match.start() < start_match.end():
                        return node, False
                    end_end = end_match.end()
            else:
                end_idx = text.find(end_text, start_match.end())
                if end_idx == -1:
                    return node, False
                end_end = end_idx + len(end_text)
            rebuilt = dc_replace(node, text=f"{text[: start_match.start()]}{replacement}{text[end_end:]}")
            self._replace_node_in_statute(node, rebuilt)
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

        text_nodes: list[tuple[tuple[int, ...], UKMutableNode]] = []

        def _collect(n: UKMutableNode, path: tuple[int, ...] = ()) -> None:
            if n.text:
                text_nodes.append((path, n))
            for i, child in enumerate(n.children):
                _collect(child, path + (i,))

        _collect(node)
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
