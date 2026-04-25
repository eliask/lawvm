"""Step 7 of the Finnish amendment pipeline: post-process IR tree.

Extracted from grafter.py as a pure module with no grafter import cycle.
The only dependencies are tree_ops and IRNode — no lxml, no corpus access.
"""
from __future__ import annotations

import re
from typing import Optional

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.finland.helpers import _norm_num_token


_KUMOTTU_PLACEHOLDER_RE = re.compile(
    r'^(\S+ §) (\d+) momentti on kumottu(.*)$'
)
_SECTION_KUMOTTU_PLACEHOLDER_RE = re.compile(
    r'^(.*?) § on kumottu(.*)$'
)


def _is_omission_ir(node: IRNode) -> bool:
    """Return True for omission-marker nodes.

    Handles three encodings:
    - ``kind='omission'`` (standard AKN element)
    - ``hcontainer`` with ``name='omission'`` (named container form)
    - ``p`` with ``class='omission'`` (alternate form in older Finnish amendment XMLs)
    """
    if node.kind == IRNodeKind.OMISSION:
        return True
    if node.kind == IRNodeKind.HCONTAINER and node.attrs.get("name") == "omission":
        return True
    if node.kind == IRNodeKind.P and node.attrs.get("class") == "omission":
        return True
    return False


def post_process_tree(ir: IRNode, normalize_replay_text: bool = True) -> IRNode:
    """Step 7: strip boilerplate, normalize labels, hoist.

    Post-process on IRNode — no lxml mutations, no rebuild needed.

    Operations applied in order:
    1. Strip omission marker nodes (``kind='omission'`` or hcontainer name='omission').
    2. Strip conclusions hcontainers.
    3. Hoist trailing sections into the preceding chapter (skip voimaantulo).
    4. Hoist trailing chapters into the preceding part.
    5. Optionally normalize whitespace in text nodes.

    The function is idempotent: ``post_process_tree(post_process_tree(t)) == post_process_tree(t)``.
    """
    # Strip omission and conclusions nodes
    ir = _tops.strip_nodes(ir, _is_omission_ir)
    ir = _tops.strip_nodes(
        ir,
        lambda n: n.kind == IRNodeKind.HCONTAINER and n.attrs.get("name") == "conclusions",
    )
    # Hoist trailing sections/chapters.
    # skip_heading_prefixes=['voimaantulo'] prevents Finnish entry-into-force
    # sections (heading starts with "voimaantulo") from being hoisted into a
    # preceding chapter — they are statute-level, not chapter-level content.
    ir = _tops.hoist_trailing_into_container(
        ir,
        "chapter",
        "section",
        skip_heading_prefixes=["voimaantulo"],
    )
    ir = _tops.hoist_trailing_into_container(ir, "part", "chapter")
    # Normalize text
    if normalize_replay_text:
        ir = _tops.normalize_text(ir)
    return ir


def _consolidate_kumottu_range(ir: IRNode) -> IRNode:
    """Merge consecutive subsection kumottu placeholders into a range.

    Finlex uses "2-5 momentit on kumottu A:lla ..." for contiguous repeals.
    LawVM emits individual "§ N momentti on kumottu ..." per subsection.
    This post-pass merges contiguous runs sharing the same attribution.
    """
    def _display_section_label(label: str) -> str:
        return re.sub(r'^(\d+)([a-z])$', r'\1 \2', label, flags=re.I)

    def _section_placeholder_attr(node: IRNode) -> Optional[str]:
        subs = [c for c in node.children if c.kind == IRNodeKind.SUBSECTION]
        if len(subs) != 1:
            return None
        p_nodes = [
            c
            for gc in subs[0].children
            if gc.kind == IRNodeKind.CONTENT
            for c in gc.children
            if c.kind == IRNodeKind.P
        ]
        if len(p_nodes) != 1 or not p_nodes[0].text:
            return None
        m = _SECTION_KUMOTTU_PLACEHOLDER_RE.match(p_nodes[0].text)
        if not m:
            return None
        return m.group(2).rstrip('.')

    def _parse_section_label(label: str) -> Optional[tuple[int, str]]:
        m = re.fullmatch(r'(\d+)([a-z]?)', _norm_num_token(label), re.I)
        if not m:
            return None
        return int(m.group(1)), m.group(2).lower()

    def _contiguous_section_labels(prev_label: str, next_label: str) -> bool:
        prev = _parse_section_label(prev_label)
        nxt = _parse_section_label(next_label)
        if prev is None or nxt is None:
            return False
        prev_num, prev_suffix = prev
        next_num, next_suffix = nxt
        if next_num == prev_num + 1 and next_suffix == '':
            return prev_suffix == 'a'
        return False

    def _same_source_suffix_shadow(
        base_label: str,
        base_attr: str,
        candidate: IRNode,
    ) -> bool:
        if candidate.kind != 'section' or not candidate.label:
            return False
        cand_attr = _section_placeholder_attr(candidate)
        if cand_attr != base_attr:
            return False
        base = _parse_section_label(base_label)
        cand = _parse_section_label(candidate.label)
        if base is None or cand is None:
            return False
        base_num, base_suffix = base
        cand_num, cand_suffix = cand
        return not base_suffix and cand_num == base_num and bool(cand_suffix)

    def _merge_repealed_sections(children: list[IRNode]) -> list[IRNode]:
        merged: list[IRNode] = []
        run: list[tuple[str, str, IRNode]] = []  # (label, attr, node)

        def _flush_run() -> None:
            nonlocal run
            if len(run) <= 1:
                merged.extend(node for _, _, node in run)
                run = []
                return
            first_label = run[0][0]
            last_label = run[-1][0]
            attr = run[0][1]
            display_range = f"{_display_section_label(first_label)}\u2013{_display_section_label(last_label)}"
            merged.append(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label=f"{first_label}\u2013{last_label}",
                    attrs={"lawvm_repeal_placeholder": "1"},
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text=f"{display_range} §"),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            children=(
                                IRNode(
                                    kind=IRNodeKind.CONTENT,
                                    children=(
                                        IRNode(kind=IRNodeKind.P, text=f"{display_range} § on kumottu{attr}."),
                                    ),
                                ),
                            ),
                        ),
                    ),
                )
            )
            run = []

        i = 0
        while i < len(children):
            child = children[i]
            if child.kind != 'section' or not child.label:
                _flush_run()
                merged.append(child)
                i += 1
                continue
            attr = _section_placeholder_attr(child)
            if attr is None:
                _flush_run()
                merged.append(child)
                i += 1
                continue
            j = i + 1
            while j < len(children) and _same_source_suffix_shadow(child.label, attr, children[j]):
                j += 1
            if j > i + 1:
                _flush_run()
                merged.append(child)
                i = j
                continue
            if run:
                prev_label, prev_attr, _ = run[-1]
                if prev_attr == attr and _contiguous_section_labels(prev_label, child.label):
                    run.append((child.label, attr, child))
                    i += 1
                    continue
                _flush_run()
            run.append((child.label, attr, child))
            i += 1
        _flush_run()
        return merged

    def _rewrite(node: IRNode) -> IRNode:
        rewritten_children = [_rewrite(child) for child in node.children]
        node = IRNode(
            kind=node.kind,
            label=node.label,
            text=node.text,
            attrs=node.attrs,
            children=tuple(rewritten_children),
        )
        if node.kind in ('body', 'chapter'):
            merged_children = _merge_repealed_sections(list(node.children))
            if merged_children != list(node.children):
                return IRNode(
                    kind=node.kind,
                    label=node.label,
                    text=node.text,
                    attrs=node.attrs,
                    children=tuple(merged_children),
                )
            return node
        if node.kind != 'section':
            return node

        subs = [c for c in node.children if c.kind == 'subsection']
        non_subs = [c for c in node.children if c.kind != 'subsection']
        if len(subs) < 2:
            return node

        merged_subs: list[IRNode] = []
        run: list[tuple[int, str, str, IRNode]] = []  # (mom_num, sec_prefix, attribution, node)

        def _flush_run() -> None:
            if len(run) <= 1:
                for _, _, _, sub_node in run:
                    merged_subs.append(sub_node)
                return
            first_num = run[0][0]
            last_num = run[-1][0]
            attr = run[0][2]
            merged_text = f'{first_num}\u2013{last_num} momentit on kumottu{attr}.'
            merged_subs.append(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    children=(
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            children=(
                                IRNode(kind=IRNodeKind.P, text=merged_text),
                            ),
                        ),
                    ),
                )
            )

        for sub in subs:
            p_nodes = [c for gc in sub.children if gc.kind == 'content' for c in gc.children if c.kind == 'p']
            if len(p_nodes) == 1 and p_nodes[0].text:
                m = _KUMOTTU_PLACEHOLDER_RE.match(p_nodes[0].text)
                if m:
                    sec_prefix = m.group(1)
                    mom_num = int(m.group(2))
                    attr = m.group(3).rstrip('.')
                    if run and run[-1][1] == sec_prefix and run[-1][2] == attr and mom_num == run[-1][0] + 1:
                        run.append((mom_num, sec_prefix, attr, sub))
                        continue
                    _flush_run()
                    run = [(mom_num, sec_prefix, attr, sub)]
                    continue
            _flush_run()
            run = []
            merged_subs.append(sub)

        _flush_run()
        return IRNode(
            kind=node.kind,
            label=node.label,
            text=node.text,
            attrs=node.attrs,
            children=tuple(non_subs + merged_subs),
        )

    return _rewrite(ir)
