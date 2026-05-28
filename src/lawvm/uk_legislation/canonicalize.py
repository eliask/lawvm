from __future__ import annotations

import re
from typing import Any, NamedTuple, Optional

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.roman import roman_to_arabic

UK_TRANSPARENT_WRAPPER_KINDS = frozenset(
    {
        "p1group",
        "pblock",
        "crossheading",
        "body",
        "content",
    }
)
_UK_SECTIONLIKE_KINDS = frozenset({"p1group", "section", "article", "rule", "regulation"})
_UK_SUBSECTIONLIKE_KINDS = frozenset({"p2", "subsection", "point"})
_UK_PARAGRAPHLIKE_KINDS = frozenset({"p3", "paragraph", "point"})
_UK_ITEMLIKE_KINDS = frozenset({"p4", "item", "point"})
_UK_CHAPTERLIKE_KINDS = frozenset({"pblock", "chapter", "crossheading", "division"})

_UK_SCHEDULE_CONTAINER_KINDS = frozenset({"part", "chapter", "crossheading", "division"})
_UK_SCHEDULE_SECTIONLIKE_KINDS = frozenset({"section", "article", "rule", "regulation"})
_UK_SCHEDULE_DESCENDANT_KINDS = frozenset(
    {"paragraph", "subsection", "subparagraph", "item", "point", "p1", "p2", "p3", "p4"}
)
_UK_BODY_CONTAINER_KINDS = frozenset({"part", "chapter", "crossheading", "division"})
_UK_BODY_SECTIONLIKE_KINDS = frozenset({"section", "article", "rule", "regulation", "p1group", "p1"})
_UK_BODY_DESCENDANT_KINDS = frozenset(
    {"paragraph", "subsection", "subparagraph", "item", "point", "p2", "p3", "p4"}
)


class UKCanonicalNodeMatch(NamedTuple):
    node: Optional[IRNode]
    parent: Optional[IRNode]
    index: Optional[int]


class UKBodyPredecessorParent(NamedTuple):
    parent: Optional[IRNode]
    predecessor_index: Optional[int]
    predecessor_label: Optional[str]


class UKInsertionParentResolution(NamedTuple):
    parent: Optional[IRNode]
    insert_index: Optional[int]


class _UKBodyPredecessorCandidate(NamedTuple):
    sort_key: tuple[tuple[int, object], ...]
    parent: IRNode
    index: int
    label: str


def _clean_num(s: str) -> str:
    return str(s or "").strip().strip("()").replace("\u00a0", " ").lower()


def uk_addr_container(addr: LegalAddress) -> str:
    if addr.path and addr.path[0][0] == "schedule":
        return "schedule"
    return "section"


def uk_is_transparent_wrapper_kind(kind: str) -> bool:
    return str(kind or "").lower() in UK_TRANSPARENT_WRAPPER_KINDS


def uk_semantic_path_key(
    parent_path_key: str,
    *,
    kind: str,
    clean_label: str,
) -> str:
    node_key_part = f"{kind}-{clean_label}" if clean_label else kind
    if uk_is_transparent_wrapper_kind(kind):
        return parent_path_key
    return f"{parent_path_key}:{node_key_part}" if parent_path_key else node_key_part


def uk_should_descend_transparently(node: IRNode) -> bool:
    return uk_is_transparent_wrapper_kind(str(node.kind))


def uk_should_bubble_structural_commencement(node: IRNode) -> bool:
    return uk_is_transparent_wrapper_kind(str(node.kind)) or str(node.kind or "") in {
        "part",
        "chapter",
        "schedule",
        "section",
        "article",
        "rule",
        "regulation",
        "subsection",
        "paragraph",
        "subparagraph",
        "item",
        "wrapper",
        "hcontainer",
    }


def uk_schedule_root_candidates(
    schedules: list[IRNode],
    *,
    sched_label: Optional[str],
    remaining_path: tuple[tuple[str, str], ...],
    match_kind_label,
) -> list[UKCanonicalNodeMatch]:
    roots: list[UKCanonicalNodeMatch] = []
    if sched_label:
        for i, sch in enumerate(schedules):
            if match_kind_label(sch, "schedule", sched_label):
                roots.append(UKCanonicalNodeMatch(sch, None, i))
                break
        if not roots and len(schedules) == 1:
            sch = schedules[0]
            if not _clean_num(str(sch.label or "")):
                roots.append(UKCanonicalNodeMatch(sch, None, 0))
        return roots

    unlabeled: list[UKCanonicalNodeMatch] = [
        UKCanonicalNodeMatch(sch, None, i)
        for i, sch in enumerate(schedules)
        if not _clean_num(str(sch.label or ""))
    ]
    if len(unlabeled) == 1:
        sch, _, idx = unlabeled[0]
        return [UKCanonicalNodeMatch(sch, None, idx)]
    if remaining_path:
        return [UKCanonicalNodeMatch(sch, None, i) for i, sch in enumerate(schedules)]
    return []


def uk_schedule_ordinal_paragraph_matches(
    curr_cands: list[UKCanonicalNodeMatch],
    *,
    p_kind: Optional[str],
    p_label: Optional[str],
) -> list[UKCanonicalNodeMatch]:
    if p_kind is None or str(p_kind).lower() not in {"paragraph", "p1"} or not p_label:
        return []
    clean = _clean_num(str(p_label))
    if not clean.isdigit():
        return []
    ordinal = int(clean)
    ordinal_matches: list[UKCanonicalNodeMatch] = []
    for curr_node, _, _ in curr_cands:
        if curr_node is None:
            continue
        paragraph_children = [
            UKCanonicalNodeMatch(child, curr_node, i)
            for i, child in enumerate(curr_node.children)
            if str(child.kind) == "p1group" and not _clean_num(str(child.label or ""))
        ]
        if 1 <= ordinal <= len(paragraph_children):
            wrapper, wrapper_parent, wrapper_idx = paragraph_children[ordinal - 1]
            if wrapper is None:
                continue
            wrapper_paragraph_children = [
                UKCanonicalNodeMatch(child, wrapper, i)
                for i, child in enumerate(wrapper.children)
                if str(child.kind) == "paragraph"
            ]
            exact_children = [
                row
                for row in wrapper_paragraph_children
                if row.node is not None and _clean_num(str(row.node.label or "")) == clean
            ]
            if len(wrapper_paragraph_children) == 1 and len(exact_children) == 1:
                ordinal_matches.append(exact_children[0])
            else:
                ordinal_matches.append(UKCanonicalNodeMatch(wrapper, wrapper_parent, wrapper_idx))
    return ordinal_matches


def uk_compound_subsection_candidate(
    curr_node: IRNode,
    label: str,
    *,
    match_kind_label,
) -> UKCanonicalNodeMatch:
    clean = _clean_num(label)
    suffix_start = -1
    for idx, ch in enumerate(clean):
        if ch.isalpha():
            suffix_start = idx
            break
    if suffix_start <= 0:
        return UKCanonicalNodeMatch(None, None, None)
    base, suffix = clean[:suffix_start], clean[suffix_start:]
    for i, child in enumerate(curr_node.children):
        if not match_kind_label(child, "subsection", base):
            continue
        for j, grandchild in enumerate(child.children):
            if (
                match_kind_label(grandchild, "paragraph", suffix)
                or match_kind_label(grandchild, "item", suffix)
                or match_kind_label(grandchild, "point", suffix)
            ):
                return UKCanonicalNodeMatch(grandchild, child, j)
    return UKCanonicalNodeMatch(None, None, None)


def uk_recursive_kind_match(
    node: IRNode,
    *,
    kind: str,
    label: str,
    match_kind_label,
) -> UKCanonicalNodeMatch:
    for i, child in enumerate(node.children):
        if match_kind_label(child, kind, label):
            return UKCanonicalNodeMatch(child, node, i)
        if not child.children:
            continue
        res_n, res_p, res_i = uk_recursive_kind_match(
            child,
            kind=kind,
            label=label,
            match_kind_label=match_kind_label,
        )
        if res_n is not None:
            return UKCanonicalNodeMatch(res_n, res_p, res_i)
    return UKCanonicalNodeMatch(None, None, None)


def _roman_suffix_sort_key(text: str) -> Optional[tuple[int, str]]:
    raw = str(text or "").strip().lower()
    if not raw or not re.fullmatch(r"[a-z]+", raw):
        return None
    for split_at in range(len(raw), 0, -1):
        prefix = raw[:split_at]
        suffix = raw[split_at:]
        roman = roman_to_arabic(prefix)
        if roman is not None:
            return (roman, suffix)
    return None


def _effective_insert_label_key(label: Optional[str], *, peers: list[str], label_sort_key) -> tuple[int, Any]:
    raw = str(label or "").strip().lower()
    peer_raw = [str(peer or "").strip().lower() for peer in peers if str(peer or "").strip()]
    peer_alpha = bool(peer_raw) and all(re.fullmatch(r"[a-z]+", peer) for peer in peer_raw)
    peer_roman_scheme = peer_alpha and all(_roman_suffix_sort_key(peer) is not None for peer in peer_raw)
    raw_roman_key = _roman_suffix_sort_key(raw)
    if raw_roman_key is not None and peer_roman_scheme:
        roman, suffix = raw_roman_key
        return (0, ((0, roman), (1, suffix)))

    peer_single = {peer for peer in peer_raw if re.fullmatch(r"[a-z]", peer)}
    peer_has_alpha_suffix = any(len(peer) > 1 and peer[0] in peer_single for peer in peer_raw)
    peer_has_nonroman_alpha = any(
        re.fullmatch(r"[a-z]+", peer)
        and not re.fullmatch(r"[ivxlcdm]+", peer)
        and _roman_suffix_sort_key(peer) is None
        for peer in peer_raw
    )
    raw_is_alpha_suffix = len(raw) > 1 and raw[0] in peer_single
    alphabetic_suffix_scheme = (
        peer_alpha
        and (
            all(re.fullmatch(r"[a-z]", peer) for peer in peer_raw)
            or (peer_has_alpha_suffix and peer_has_nonroman_alpha)
            or (raw_is_alpha_suffix and peer_has_nonroman_alpha)
            or (bool(re.fullmatch(r"[a-z]", raw)) and peer_has_nonroman_alpha)
        )
    )
    if raw and re.fullmatch(r"[a-z]+", raw) and alphabetic_suffix_scheme:
        return (0, ((1, raw),))
    return (1, label_sort_key(label))


def uk_insert_into_children(
    children: list[IRNode],
    node_to_insert: IRNode,
    *,
    label_sort_key,
) -> None:
    if node_to_insert.label:
        insert_kind = str(node_to_insert.kind)
        insert_label = _clean_num(node_to_insert.label or "")
        for i, child in enumerate(children):
            if str(child.kind) != insert_kind:
                continue
            if _clean_num(child.label or "") != insert_label:
                continue
            existing_eid = child.attrs.get("eId")
            if existing_eid and not node_to_insert.attrs.get("eId"):
                new_attrs = dict(node_to_insert.attrs)
                new_attrs["eId"] = existing_eid
                node_to_insert = IRNode(
                    kind=node_to_insert.kind,
                    label=node_to_insert.label,
                    text=node_to_insert.text,
                    attrs=new_attrs,
                    children=node_to_insert.children,
                )
            children[i] = node_to_insert
            return
    if not node_to_insert.label:
        children.append(node_to_insert)
        return
    same_kind = [
        (i, child) for i, child in enumerate(children) if child.kind == node_to_insert.kind and child.label
    ]
    if not same_kind:
        children.append(node_to_insert)
        return

    new_label = node_to_insert.label or ""
    new_is_bare = not re.search(r"\d", new_label)
    if new_is_bare:
        for i, child in same_kind:
            child_label = child.label or ""
            if re.search(r"\d", child_label):
                children.insert(i, node_to_insert)
                return

    same_kind_labels = [child.label or "" for _, child in same_kind]
    new_key = _effective_insert_label_key(node_to_insert.label, peers=same_kind_labels, label_sort_key=label_sort_key)
    for i, child in same_kind:
        if _effective_insert_label_key(child.label, peers=same_kind_labels, label_sort_key=label_sort_key) > new_key:
            children.insert(i, node_to_insert)
            return
    children.insert(same_kind[-1][0] + 1, node_to_insert)


def uk_find_body_predecessor_parent(
    body_root: IRNode,
    node_kind: str,
    node_label: Optional[str],
    *,
    label_sort_key,
) -> UKBodyPredecessorParent:
    if not node_label:
        return UKBodyPredecessorParent(None, None, None)

    want_key = label_sort_key(node_label)
    best: _UKBodyPredecessorCandidate | None = None

    def _walk(parent: IRNode) -> None:
        nonlocal best
        for i, child in enumerate(parent.children):
            child_label = child.label or ""
            if str(child.kind).lower() == str(node_kind or "").lower() and child_label:
                child_key = label_sort_key(child_label)
                if child_key < want_key and (best is None or child_key > best.sort_key):
                    best = _UKBodyPredecessorCandidate(child_key, parent, i, child_label)
            _walk(child)

    _walk(body_root)
    if best is None:
        return UKBodyPredecessorParent(None, None, None)
    return UKBodyPredecessorParent(best.parent, best.index, best.label)


def uk_resolve_insertion_parent(
    *,
    target: LegalAddress,
    body_root: IRNode,
    node_kind: str,
    node_label: Optional[str],
    preceding_eid: Optional[str],
    following_eid: Optional[str],
    find_node_by_target,
    find_node_and_parent_statute,
    label_sort_key,
) -> UKInsertionParentResolution:
    if preceding_eid:
        _, sib_p, sib_idx = find_node_and_parent_statute(preceding_eid)
        if sib_p and sib_idx is not None:
            return UKInsertionParentResolution(sib_p, sib_idx + 1)
    if following_eid:
        _, sib_p, sib_idx = find_node_and_parent_statute(following_eid)
        if sib_p and sib_idx is not None:
            return UKInsertionParentResolution(sib_p, sib_idx)

    parent_addr = target.parent() if len(target.path) > 1 else None
    if parent_addr is not None:
        p_node, _, _ = find_node_by_target(parent_addr)
        if p_node:
            return UKInsertionParentResolution(p_node, None)

    if uk_addr_container(target) == "schedule":
        schedule_node, _, _ = find_node_by_target(target)
        if schedule_node and str(node_kind or "").lower() in {"part", "chapter", "section"}:
            return UKInsertionParentResolution(schedule_node, None)

    if node_label and len(target.path) == 1 and uk_addr_container(target) != "schedule":
        p_node, p_idx, _ = uk_find_body_predecessor_parent(
            body_root,
            node_kind,
            node_label,
            label_sort_key=label_sort_key,
        )
        if p_node and p_idx is not None:
            return UKInsertionParentResolution(p_node, p_idx + 1)

    return UKInsertionParentResolution(None, None)


def uk_kind_matches(
    *,
    node_kind: str,
    target_kind: str,
    node_label: str = "",
    target_label: str = "",
) -> bool:
    nk = str(node_kind or "").lower()
    tk = str(target_kind or "").lower()
    if nk == tk:
        return True
    if nk in _UK_SECTIONLIKE_KINDS and tk in _UK_SECTIONLIKE_KINDS:
        return True
    if nk == "p1group" and tk in {"paragraph", "p1"}:
        return True
    if nk in _UK_SUBSECTIONLIKE_KINDS and tk in _UK_SUBSECTIONLIKE_KINDS:
        return True
    if nk in _UK_PARAGRAPHLIKE_KINDS and tk in _UK_PARAGRAPHLIKE_KINDS:
        return True
    if (
        nk in {"p3", "paragraph"}
        and tk == "subsection"
        and node_label
        and target_label
    ):
        return True
    if nk in {"item", "point"} and tk == "subparagraph" and node_label and target_label:
        return True
    if nk in _UK_ITEMLIKE_KINDS and tk in _UK_ITEMLIKE_KINDS:
        return True
    if nk == "table" and tk in {"paragraph", "table"}:
        return True
    if nk in _UK_CHAPTERLIKE_KINDS and tk in _UK_CHAPTERLIKE_KINDS:
        return True
    if nk == "recital" and tk == "division":
        return True
    if nk == "division" and tk == "recital":
        return True
    return False


def canonicalize_uk_address(addr: LegalAddress) -> LegalAddress:
    path = tuple(addr.path or ())
    if not path:
        return addr
    normalized: list[tuple[str, str]] = []
    if uk_addr_container(addr) == "schedule":
        schedule_depth = 0
        for idx, (kind, label) in enumerate(path):
            if idx == 0 and kind == "schedule":
                normalized.append((kind, label))
                continue
            if kind in _UK_SCHEDULE_CONTAINER_KINDS:
                normalized.append((kind, label))
                continue
            if kind in _UK_SCHEDULE_SECTIONLIKE_KINDS:
                normalized.append(("section", label))
                continue
            if kind in _UK_SCHEDULE_DESCENDANT_KINDS:
                if schedule_depth == 0:
                    normalized.append(("paragraph", label))
                elif schedule_depth == 1:
                    if kind in {"item", "point"}:
                        normalized.append(("item", label))
                    else:
                        normalized.append(("subparagraph", label))
                else:
                    normalized.append(("item", label))
                schedule_depth += 1
                continue
            normalized.append((kind, label))
    else:
        body_depth = 0
        seen_section = False
        for kind, label in path:
            if kind in _UK_BODY_CONTAINER_KINDS:
                normalized.append((kind, label))
                continue
            if kind in _UK_BODY_SECTIONLIKE_KINDS:
                normalized.append(("section", label))
                seen_section = True
                continue
            if kind in _UK_BODY_DESCENDANT_KINDS:
                if not seen_section:
                    normalized.append(("section", label))
                    seen_section = True
                    continue
                if body_depth == 0:
                    normalized.append(("subsection", label))
                elif body_depth == 1:
                    normalized.append(("paragraph", label))
                elif body_depth == 2:
                    normalized.append(("subparagraph", label))
                else:
                    normalized.append(("item", label))
                body_depth += 1
                continue
            normalized.append((kind, label))

    normalized_path = tuple(normalized)
    if normalized_path == path:
        return addr
    return LegalAddress(path=normalized_path, special=addr.special)
