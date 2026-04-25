"""Norway replay-vs-current consistency checks."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops
from lawvm.core.timeline_consistency import ConsistencyDivergence, ingest_consolidated, verify_consistency
from lawvm.norway.grafter import parse_no_statute
from lawvm.norway.index import NOAmendmentIndex, build_no_amendment_index, load_no_amendment_index
from lawvm.norway.inventory import build_no_inventory
from lawvm.norway.replay import NOReplayResult, replay_no_to_pit
from lawvm.norway.sources import load_no_current_bytes, resolve_no_source_path

_NO_VERIFY_WS_RE = re.compile(r"\s+")
_NO_VERIFY_PUNCT_RE = re.compile(r"\s+([,.;:])")
_NO_VERIFY_PAREN_OPEN_RE = re.compile(r"\(\s+")
_NO_VERIFY_REPEALED_RE = re.compile(r"^(?:§\s*[0-9A-Za-z-]+\.\s*)?\(Opphevet\)$", re.IGNORECASE)
_NO_VERIFY_OTHER_LAWS_PLACEHOLDER_RE = re.compile(
    r"((?:gjøres følgende endringer(?: i andre lover)?|gjerast i andre lover|skal desse endringane gjerast i andre lover):)\s*(?:[-–—]\s*){2,}$",
    re.IGNORECASE,
)
_NO_VERIFY_TRAILING_FOOTNOTE_RE = re.compile(r"([.!?])\s+\d+$")
_NO_VERIFY_STANDALONE_FOOTNOTE_RE = re.compile(r"([.!?])\s+\d+\s+(?=[A-ZÆØÅ])")
_NO_VERIFY_CONTINGENT_OTHER_LAWS_RE = re.compile(
    r"^(?:Fra|Frå|Med virkning fra den tid)\b.*?(?:Kongen fastsetter|Kongen bestemmer).*?(?:gjøres følgende endringer|gjerast i andre lover|skal desse endringane gjerast i andre lover)",
    re.IGNORECASE,
)
_NO_VERIFY_SECTION_SHELL_RE = re.compile(
    r"^I §\s*(?P<label>[0-9A-Za-z-]+)(?:\s+nr\.\s*\d+)?\b",
    re.IGNORECASE,
)


@dataclass
class NOVerifyResult:
    base_id: str
    as_of: str
    current_title: str = ""
    replay_status: str = ""
    consistent: bool = False
    divergence_count: int = 0
    divergence_counts: dict[str, int] | None = None
    raw_divergence_count: int = 0
    raw_divergence_counts: dict[str, int] | None = None
    divergences: list[ConsistencyDivergence] | None = None
    indexed_amendment_count: int = 0
    applied_amendment_count: int = 0
    replay_op_count: int = 0
    source_signal: str | None = None
    replay: Optional[NOReplayResult] = None
    error: str | None = None


_NO_RELATION_CONTAINER_KINDS = {"part", "chapter"}
_NO_RELATION_SPECIAL_LABELS = {"last", "first"}


def normalize_no_comparison_text(text: str) -> str:
    """Normalize bounded Norway editorial spacing noise for compare-only use."""
    text = text.replace("\xa0", " ")
    text = _NO_VERIFY_WS_RE.sub(" ", text).strip()
    text = _NO_VERIFY_PUNCT_RE.sub(r"\1", text)
    text = _NO_VERIFY_PAREN_OPEN_RE.sub("(", text)
    text = re.sub(r"(?<=[a-zæøå])\s+\d+\s+(?=[A-ZÆØÅ])", " ", text)
    text = _NO_VERIFY_STANDALONE_FOOTNOTE_RE.sub(r"\1 ", text)
    text = re.sub(r"(\d)\s+-", r"\1-", text)
    text = _NO_VERIFY_OTHER_LAWS_PLACEHOLDER_RE.sub(r"\1", text)
    text = _NO_VERIFY_TRAILING_FOOTNOTE_RE.sub(r"\1", text)
    if _NO_VERIFY_REPEALED_RE.fullmatch(text):
        return ""
    return text


def _has_no_other_laws_marker(text: str) -> bool:
    lowered = normalize_no_comparison_text(text).lower()
    return (
        "endringer i andre lover" in lowered
        or "endringar i andre lover" in lowered
        or "gjøres følgende endringer" in lowered
        or "gjerast i andre lover" in lowered
        or "desse endringane gjerast i andre lover" in lowered
    )


def _is_no_contingent_other_laws_placeholder(text: str) -> bool:
    lowered = normalize_no_comparison_text(text).lower()
    return bool(_NO_VERIFY_CONTINGENT_OTHER_LAWS_RE.search(lowered))


def _is_no_self_section_lead_shell(section_label: str | None, text: str) -> bool:
    normalized = normalize_no_comparison_text(text)
    if not normalized:
        return False
    match = _NO_VERIFY_SECTION_SHELL_RE.match(normalized)
    if not match:
        return False
    if section_label and match.group("label") != section_label:
        return False
    lowered = normalized.lower()
    return (
        "endringer i " in lowered
        or "endringar i " in lowered
        or " om endringer i " in lowered
        or " om endringar i " in lowered
        or "skal ny endring lyde:" in lowered
        or "skal nye endringer lyde:" in lowered
        or "skal ny endring lyde :" in lowered
        or "skal nye endringer lyde :" in lowered
    )


def _infer_no_source_signal(
    *,
    divergence_count: int,
    indexed_amendment_count: int,
    replay_op_count: int,
    base_year: int,
) -> str | None:
    if (
        divergence_count >= 50
        and indexed_amendment_count <= 1
        and replay_op_count <= 5
        and base_year
        and base_year <= 2020
    ):
        return "sparse_indexed_history"
    if (
        divergence_count >= 15
        and indexed_amendment_count <= 1
        and replay_op_count <= 2
        and base_year
        and base_year <= 2025
    ):
        return "sparse_indexed_history"
    return None


def normalize_no_relation_path(path: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple(step for step in path if step[0] not in _NO_RELATION_CONTAINER_KINDS)


def no_paths_related(
    left: tuple[tuple[str, str], ...],
    right: tuple[tuple[str, str], ...],
) -> bool:
    left = normalize_no_relation_path(left)
    right = normalize_no_relation_path(right)
    if left == right:
        return True
    if len(left) <= len(right) and right[: len(left)] == left:
        return True
    if len(right) <= len(left) and left[: len(right)] == right:
        return True
    if not left or not right:
        return False
    left_kind, left_label = left[-1]
    right_kind, right_label = right[-1]
    if left_kind != right_kind:
        return False
    if left[:-1] != right[:-1]:
        return False
    return left_label in _NO_RELATION_SPECIAL_LABELS or right_label in _NO_RELATION_SPECIAL_LABELS


def _concretize_no_relation_path(
    body: IRNode,
    path: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    concrete: list[tuple[str, str]] = []
    for kind, label in path:
        if label not in _NO_RELATION_SPECIAL_LABELS:
            concrete.append((kind, label))
            continue
        parent = tree_ops.resolve(body, concrete) if concrete else body
        if parent is None:
            return path
        candidates = [child for child in parent.children if child.kind == kind and child.label]
        if not candidates:
            return path
        chosen = candidates[-1] if label == "last" else candidates[0]
        concrete.append((kind, chosen.label or label))
    return tuple(concrete)


def collect_no_touched_path_counts(
    *,
    base_id: str,
    index: NOAmendmentIndex,
    data_dir: Optional[Path] = None,
    replayed_body: Optional[IRNode] = None,
) -> tuple[Counter[tuple[tuple[str, str], ...]], int, int]:
    from lawvm.norway.grafter import iter_no_document_change_ops
    from lawvm.norway.sources import load_no_amendment_bytes

    source_path = resolve_no_source_path(Path(index.data_dir) if getattr(index, "data_dir", None) else data_dir)
    norm_base_id = base_id if base_id.startswith("no/") else f"no/{base_id.removeprefix('lov/')}"
    touched_path_counts: Counter[tuple[tuple[str, str], ...]] = Counter()
    touched_source_count = 0
    touched_op_count = 0

    for entry in index.entries_for_base(norm_base_id):
        html_bytes = load_no_amendment_bytes(entry.source_id, source_path)
        if html_bytes is None:
            continue
        source_touched = False
        for group_base, ops in iter_no_document_change_ops(html_bytes, entry.source_id):
            if group_base != norm_base_id:
                continue
            for op in ops:
                touched_op_count += 1
                op_paths = {tuple(op.target.path)}
                for candidate in getattr(op, "targets", []) or []:
                    op_paths.add(tuple(candidate.path))
                anchor = getattr(op, "anchor", None)
                if anchor is not None:
                    op_paths.add(tuple(anchor.path))
                destination = getattr(op, "destination", None)
                if destination is not None:
                    op_paths.add(tuple(destination.path))
                if op_paths:
                    source_touched = True
                for path in op_paths:
                    if replayed_body is not None:
                        path = _concretize_no_relation_path(replayed_body, path)
                    touched_path_counts[path] += 1
        if source_touched:
            touched_source_count += 1

    return touched_path_counts, touched_source_count, touched_op_count


def build_no_verify_coverage_summary(
    *,
    verify_result: NOVerifyResult,
    index: NOAmendmentIndex,
    data_dir: Optional[Path] = None,
) -> dict[str, Any]:
    replay = getattr(verify_result, "replay", None)
    replayed_body = replay.replayed.body if replay is not None and getattr(replay, "replayed", None) is not None else None
    touched_path_counts, touched_source_count, touched_op_count = collect_no_touched_path_counts(
        base_id=verify_result.base_id,
        index=index,
        data_dir=data_dir,
        replayed_body=replayed_body,
    )
    touched_path_set = set(touched_path_counts)
    divergences = list(verify_result.divergences or [])
    touched_divergence_count = 0
    untouched_divergence_count = 0
    for divergence in divergences:
        divergence_path = tuple(divergence.address.path)
        if any(no_paths_related(path, divergence_path) for path in touched_path_set):
            touched_divergence_count += 1
        else:
            untouched_divergence_count += 1
    return {
        "touched_path_count": len(touched_path_counts),
        "touched_source_count": touched_source_count,
        "touched_op_count": touched_op_count,
        "touched_divergence_count": touched_divergence_count,
        "untouched_divergence_count": untouched_divergence_count,
    }


def irnode_to_no_comparison_text(node: IRNode) -> str:
    """Norway compare-only materialization.

    Current Lovdata consolidated texts sometimes omit section-title headings that
    appear in amendment-side future payloads. Ignore direct section heading
    children so verify focuses on operative text and structure rather than
    heading-only editorial drift.
    """
    if node.kind in {IRNodeKind.SUBSECTION, IRNodeKind.ITEM} and node.children:
        parts = [node.text or ""]
        parts.extend(irnode_to_no_comparison_text(child) for child in node.children)
        return " ".join(part for part in parts if part)
    if node.text:
        return node.text
    children = node.children
    if node.kind is IRNodeKind.SECTION:
        children = [
            child
            for child in children
            if child.kind is not IRNodeKind.HEADING
        ]
    parts = [irnode_to_no_comparison_text(child) for child in children]
    return " ".join(part for part in parts if part)


def _normalize_no_compare_tree(node: IRNode) -> IRNode:
    """Collapse sentence-only Norway containers for compare-only verification."""
    text = node.text
    if text and normalize_no_comparison_text(text) == "":
        text = ""
    normalized_children = [_normalize_no_compare_tree(child) for child in node.children]
    if node.kind in {IRNodeKind.SUBSECTION, IRNodeKind.ITEM}:
        sentence_children = [child for child in normalized_children if child.kind is IRNodeKind.SENTENCE]
        other_children = [child for child in normalized_children if child.kind is not IRNodeKind.SENTENCE]
        if sentence_children:
            text = " ".join(child.text for child in sentence_children if child.text).strip()
            if text:
                text = " ".join(part for part in [normalize_no_comparison_text(node.text or ""), text] if part).strip()
            if not other_children:
                return IRNode(
                    kind=node.kind,
                    label=node.label,
                    text=text,
                    attrs=dict(node.attrs),
                    children=(),
                )
            return IRNode(
                kind=node.kind,
                label=node.label,
                text=text,
                attrs=dict(node.attrs),
                children=tuple(other_children),
            )
        nested_item_children = [child for child in other_children if child.kind is IRNodeKind.ITEM]
        if node.kind is IRNodeKind.ITEM and nested_item_children and text:
            normalized_parent = normalize_no_comparison_text(text)
            cut_points = [
                normalized_parent.find(child_text)
                for child in nested_item_children
                if (child_text := normalize_no_comparison_text(child.text or ""))
            ]
            cut_points = [idx for idx in cut_points if idx > 0]
            if cut_points:
                text = normalized_parent[: min(cut_points)].rstrip(" ,;")
    if node.kind is IRNodeKind.SECTION:
        heading_children = [child for child in normalized_children if child.kind is IRNodeKind.HEADING]
        non_heading_children = [child for child in normalized_children if child.kind is not IRNodeKind.HEADING]
        if (
            not non_heading_children
            and _is_no_self_section_lead_shell(node.label, node.text or "")
        ):
            return IRNode(
                kind=node.kind,
                label=node.label,
                text="",
                attrs=dict(node.attrs),
                children=tuple(heading_children),
            )
        heading_texts = [
            normalize_no_comparison_text(child.text or "").lower()
            for child in normalized_children
            if child.kind is IRNodeKind.HEADING and child.text
        ]
        subsection_children = [child for child in normalized_children if child.kind is IRNodeKind.SUBSECTION]
        if subsection_children and any(
            _is_no_contingent_other_laws_placeholder(child.text or "") for child in subsection_children
        ):
            return IRNode(
                kind=node.kind,
                label=node.label,
                text="",
                attrs=dict(node.attrs),
                children=(),
            )
        if subsection_children:
            first = subsection_children[0]
            intro_text = normalize_no_comparison_text(first.text or "")
            if intro_text.lower().endswith("forstås med:"):
                rebuilt_items: list[IRNode] = []
                if first.children and all(child.kind is IRNodeKind.ITEM for child in first.children):
                    rebuilt_items = [
                        IRNode(
                            kind=IRNodeKind.ITEM,
                            label=child.label,
                            text=normalize_no_comparison_text(child.text or ""),
                            attrs=dict(child.attrs),
                            children=child.children,
                        )
                        for child in first.children
                    ]
                elif (
                    len(subsection_children) >= 3
                    and len(subsection_children[1:]) % 2 == 0
                    and all(not child.children for child in subsection_children[1:])
                ):
                    pairs = list(zip(subsection_children[1::2], subsection_children[2::2]))
                    if all(
                        normalize_no_comparison_text(left.text or "").endswith(":")
                        and normalize_no_comparison_text(right.text or "")
                        for left, right in pairs
                    ):
                        rebuilt_items = [
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label=str(idx),
                                text=normalize_no_comparison_text(
                                    " ".join(
                                        part
                                        for part in [
                                            normalize_no_comparison_text(left.text or ""),
                                            normalize_no_comparison_text(right.text or ""),
                                        ]
                                        if part
                                    )
                                ),
                            )
                            for idx, (left, right) in enumerate(pairs, start=1)
                        ]
                if rebuilt_items:
                    rebuilt_first = IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label=first.label,
                        text=intro_text,
                        attrs=dict(first.attrs),
                        children=tuple(rebuilt_items),
                    )
                    kept_children: list[IRNode] = []
                    replaced = False
                    for child in normalized_children:
                        if child.kind is IRNodeKind.SUBSECTION:
                            if not replaced:
                                kept_children.append(rebuilt_first)
                                replaced = True
                            continue
                        kept_children.append(child)
                    return IRNode(
                        kind=node.kind,
                        label=node.label,
                        text=text,
                        attrs=dict(node.attrs),
                        children=tuple(kept_children),
                    )
        subsection_texts = [
            normalize_no_comparison_text(child.text or "").lower()
            for child in subsection_children
        ]
        has_other_laws_marker = any(_has_no_other_laws_marker(heading) for heading in heading_texts) or any(
            _has_no_other_laws_marker(subsection_text) for subsection_text in subsection_texts
        )
        if (
            has_other_laws_marker
            and subsection_children
        ):
            first_detail_index = next(
                (
                    idx
                    for idx, child in enumerate(subsection_children)
                    if _has_no_other_laws_marker(child.text or "")
                ),
                None,
            )
            if first_detail_index is None:
                first_detail_index = 0
            kept_children: list[IRNode] = []
            detail_seen = 0
            for child in normalized_children:
                if child.kind is not IRNodeKind.SUBSECTION:
                    kept_children.append(child)
                    continue
                if detail_seen == first_detail_index:
                    kept_children.append(
                        IRNode(
                            kind=child.kind,
                            label=child.label,
                            text=normalize_no_comparison_text(child.text or ""),
                            attrs=dict(child.attrs),
                            children=child.children,
                        )
                    )
                detail_seen += 1
            return IRNode(
                kind=node.kind,
                label=node.label,
                text=text,
                attrs=dict(node.attrs),
                children=tuple(kept_children),
            )
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=text,
        attrs=dict(node.attrs),
        children=tuple(normalized_children),
    )


def _is_prefix_address(prefix: tuple[tuple[str, str], ...], full: tuple[tuple[str, str], ...]) -> bool:
    return len(prefix) < len(full) and full[: len(prefix)] == prefix


def _non_container_path(path: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple(step for step in path if step[0] not in {"part", "chapter"})


def _is_chapter_relocation_pair(
    left: ConsistencyDivergence,
    right: ConsistencyDivergence,
) -> bool:
    kinds = {left.divergence_type, right.divergence_type}
    if kinds != {"OPS_MISSING", "CONSOLIDATED_MISSING"}:
        return False
    left_text = normalize_no_comparison_text(left.ops_text or left.consolidated_text or "")
    right_text = normalize_no_comparison_text(right.ops_text or right.consolidated_text or "")
    if not left_text or left_text != right_text:
        return False
    left_path = tuple(left.address.path)
    right_path = tuple(right.address.path)
    return left_path != right_path and _non_container_path(left_path) == _non_container_path(right_path)


def _primary_divergences(divergences: list[ConsistencyDivergence]) -> list[ConsistencyDivergence]:
    primary_candidates: list[ConsistencyDivergence] = []
    paths = [tuple(div.address.path) for div in divergences]
    for idx, divergence in enumerate(divergences):
        path = paths[idx]
        if any(_is_prefix_address(path, other_path) for j, other_path in enumerate(paths) if j != idx):
            continue
        primary_candidates.append(divergence)

    primary: list[ConsistencyDivergence] = []
    paired: set[int] = set()
    for idx, divergence in enumerate(primary_candidates):
        if idx in paired:
            continue
        partner_idx = next(
            (
                j
                for j in range(idx + 1, len(primary_candidates))
                if j not in paired and _is_chapter_relocation_pair(divergence, primary_candidates[j])
            ),
            None,
        )
        if partner_idx is not None:
            paired.add(partner_idx)
            continue
        primary.append(divergence)
    return primary


def load_no_current_statute(base_id: str, data_dir: Optional[Path] = None) -> IRStatute:
    data_dir = resolve_no_source_path(data_dir)
    current_bytes = load_no_current_bytes(base_id, data_dir)
    if current_bytes is None:
        raise FileNotFoundError(f"current Norway act not found: {base_id}")
    return parse_no_statute(current_bytes, base_id)


def verify_no_against_current(
    base_id: str,
    *,
    as_of: str,
    data_dir: Optional[Path] = None,
    index: Optional[NOAmendmentIndex] = None,
    index_path: Optional[Path] = None,
    commencement_path: Optional[Path] = None,
) -> NOVerifyResult:
    data_dir = resolve_no_source_path(data_dir)
    if index is None and index_path is not None:
        index = load_no_amendment_index(index_path)
    if index is None:
        index = build_no_amendment_index(data_dir)

    indexed_entries = index.entries_for_base(base_id)

    replay = replay_no_to_pit(
        base_id,
        as_of=as_of,
        data_dir=data_dir,
        index=index,
        commencement_path=commencement_path,
    )
    result = NOVerifyResult(
        base_id=replay.base_id or base_id,
        as_of=as_of,
        replay=replay,
        indexed_amendment_count=len(indexed_entries),
        applied_amendment_count=len(replay.amendments_applied),
        replay_op_count=replay.n_ops,
        replay_status=(
            "error"
            if replay.error
            else (
                "blocked_contingent"
                if replay.amendments_skipped_contingent
                else (
                    "blocked_unknown"
                    if replay.amendments_skipped_unknown_effective
                    else "replayed"
                )
            )
        ),
    )
    if replay.error:
        result.error = replay.error
        return result
    if replay.replayed is None:
        result.error = "replay produced no statute"
        return result

    try:
        current = load_no_current_statute(result.base_id, data_dir)
    except FileNotFoundError as exc:
        result.error = str(exc)
        return result
    result.current_title = current.title

    replay_compare = IRStatute(
        statute_id=replay.replayed.statute_id,
        title=replay.replayed.title,
        body=_normalize_no_compare_tree(replay.replayed.body),
        supplements=replay.replayed.supplements,
        metadata=dict(replay.replayed.metadata),
    )
    current_compare = IRStatute(
        statute_id=current.statute_id,
        title=current.title,
        body=_normalize_no_compare_tree(current.body),
        supplements=current.supplements,
        metadata=dict(current.metadata),
    )

    replay_tl = ingest_consolidated(replay_compare, as_of=as_of)
    current_tl = ingest_consolidated(current_compare, as_of=as_of)
    divergences = verify_consistency(
        replay_tl,
        current_tl,
        as_of=as_of,
        irnode_to_text=irnode_to_no_comparison_text,
        text_normalizer=normalize_no_comparison_text,
        missing_equals_empty=True,
    )
    primary = _primary_divergences(divergences)
    counts: dict[str, int] = {}
    raw_counts: dict[str, int] = {}
    for divergence in primary:
        counts[divergence.divergence_type] = counts.get(divergence.divergence_type, 0) + 1
    for divergence in divergences:
        raw_counts[divergence.divergence_type] = raw_counts.get(divergence.divergence_type, 0) + 1

    result.consistent = not primary
    result.divergence_count = len(primary)
    result.divergence_counts = counts
    result.raw_divergence_count = len(divergences)
    result.raw_divergence_counts = raw_counts
    result.divergences = primary
    base_year = 0
    try:
        base_year = int(result.base_id.split("/")[2][:4])
    except (IndexError, ValueError):
        base_year = 0
    result.source_signal = _infer_no_source_signal(
        divergence_count=result.divergence_count,
        indexed_amendment_count=result.indexed_amendment_count,
        replay_op_count=result.replay_op_count,
        base_year=base_year,
    )
    return result


def build_no_verify_scan(
    *,
    as_of: str,
    data_dir: Optional[Path] = None,
    index: Optional[NOAmendmentIndex] = None,
    index_path: Optional[Path] = None,
    commencement_path: Optional[Path] = None,
    limit: int = 10,
    base_ids: Optional[list[str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    data_dir = resolve_no_source_path(data_dir)
    if index is None and index_path is not None:
        index = load_no_amendment_index(index_path)
    if index is None:
        index = build_no_amendment_index(data_dir)

    inventory = build_no_inventory(
        data_dir,
        index=index,
        index_path=index_path,
        commencement_path=commencement_path,
    )
    executable_status_map = inventory.amended_executable_law_status_map()
    candidates = sorted(
        (
            base_id
            for base_id, status in executable_status_map.items()
            if status == "fully_replayable"
        ),
        key=lambda base_id: (-len(inventory.base_to_sources.get(base_id, [])), base_id),
    )
    if base_ids:
        wanted = set(base_ids)
        candidates = [base_id for base_id in candidates if base_id in wanted]

    results = []
    summary = {
        "consistent": 0,
        "divergent": 0,
        "error": 0,
    }
    source_signal_counts: dict[str, int] = {}
    selected = candidates[:limit]
    for idx, base_id in enumerate(selected, start=1):
        if progress_callback is not None:
            progress_callback(f"[{idx}/{len(selected)}] {base_id}")
        verify_result = verify_no_against_current(
            base_id,
            as_of=as_of,
            data_dir=data_dir,
            index=index,
            commencement_path=commencement_path,
        )
        entry = {
            "base_id": verify_result.base_id,
            "current_title": verify_result.current_title,
            "replay_status": verify_result.replay_status,
            "consistent": verify_result.consistent,
            "divergence_count": verify_result.divergence_count,
            "divergence_counts": dict(verify_result.divergence_counts or {}),
            "amendment_count": len(inventory.base_to_sources.get(base_id, [])),
            "indexed_amendment_count": verify_result.indexed_amendment_count,
            "applied_amendment_count": verify_result.applied_amendment_count,
            "replay_op_count": verify_result.replay_op_count,
            "source_signal": verify_result.source_signal or "",
            "error": verify_result.error or "",
        }
        if verify_result.error:
            summary["error"] += 1
        elif verify_result.consistent:
            summary["consistent"] += 1
        else:
            summary["divergent"] += 1
        if verify_result.source_signal:
            source_signal_counts[verify_result.source_signal] = (
                source_signal_counts.get(verify_result.source_signal, 0) + 1
            )
        results.append(entry)

    return {
        "data_dir": str(data_dir),
        "as_of": as_of,
        "candidate_count": len(candidates),
        "scanned_count": len(results),
        "summary": summary,
        "source_signal_counts": source_signal_counts,
        "results": results,
    }


def build_no_verify_partition(
    *,
    as_of: str,
    data_dir: Optional[Path] = None,
    index: Optional[NOAmendmentIndex] = None,
    index_path: Optional[Path] = None,
    commencement_path: Optional[Path] = None,
    limit: int = 10,
    base_ids: Optional[list[str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    loaded_index = index if index is not None else _load_no_index(index_path=index_path, data_dir=data_dir)
    scan = build_no_verify_scan(
        as_of=as_of,
        data_dir=data_dir,
        index=loaded_index,
        index_path=index_path,
        commencement_path=commencement_path,
        limit=limit,
        base_ids=base_ids,
        progress_callback=progress_callback,
    )
    replay_defects: list[dict[str, Any]] = []
    untouched_drift: list[dict[str, Any]] = []
    source_sparse: list[dict[str, Any]] = []
    consistent: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for item in scan["results"]:
        if item["error"]:
            errors.append(item)
        elif item["consistent"]:
            consistent.append(item)
        elif item["source_signal"]:
            source_sparse.append(item)
        else:
            verify_result = verify_no_against_current(
                item["base_id"],
                as_of=as_of,
                data_dir=data_dir,
                index=loaded_index,
                index_path=index_path,
                commencement_path=commencement_path,
            )
            coverage = build_no_verify_coverage_summary(
                verify_result=verify_result,
                index=loaded_index,
                data_dir=data_dir,
            )
            item_with_coverage = dict(item)
            item_with_coverage.update(coverage)
            if coverage["touched_divergence_count"] > 0:
                replay_defects.append(item_with_coverage)
            else:
                untouched_drift.append(item_with_coverage)

    def _sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
        return (
            -int(item.get("divergence_count", 0) or 0),
            -int(item.get("replay_op_count", 0) or 0),
            str(item.get("base_id", "")),
        )

    replay_defects.sort(key=_sort_key)
    untouched_drift.sort(key=_sort_key)
    source_sparse.sort(key=_sort_key)
    consistent.sort(key=_sort_key)
    errors.sort(key=lambda item: str(item.get("base_id", "")))

    return {
        "data_dir": scan["data_dir"],
        "as_of": scan["as_of"],
        "candidate_count": scan["candidate_count"],
        "scanned_count": scan["scanned_count"],
        "summary": dict(scan["summary"]),
        "source_signal_counts": dict(scan.get("source_signal_counts", {})),
        "partitions": {
            "replay_defect": replay_defects,
            "untouched_drift": untouched_drift,
            "source_sparse": source_sparse,
            "consistent": consistent,
            "error": errors,
        },
    }


def _load_no_index(
    *,
    index_path: Optional[Path],
    data_dir: Optional[Path],
) -> NOAmendmentIndex:
    if index_path is not None:
        return load_no_amendment_index(index_path)
    return build_no_amendment_index(data_dir)
