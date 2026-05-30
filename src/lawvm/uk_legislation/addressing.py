"""Internal UK address and label helpers.

These helpers are pure normalization/order utilities used by the UK replay
compiler. They deliberately do not resolve targets against live state.
"""
from __future__ import annotations

from functools import lru_cache
import re
from typing import Any, NamedTuple, Optional

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.mutation_boundary import TreePathStep
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction
from lawvm.uk_legislation.canonicalize import uk_addr_container
from lawvm.uk_legislation.uk_grafter import _clean_num


@lru_cache(maxsize=128)
def _uk_kind_value(kind: IRNodeKind | str) -> str:
    if isinstance(kind, IRNodeKind):
        return kind.value
    return str(kind or "")


def _uk_eid_value(eid: Any) -> str | None:
    if eid is None:
        return None
    if isinstance(eid, IRNodeKind):
        return eid.value
    return str(eid)


def _make_address(
    container: str,
    section: Optional[str] = None,
    part: Optional[str] = None,
    chapter: Optional[str] = None,
    subsection: Optional[str] = None,
    item: Optional[str] = None,
    special: Optional[FacetKind] = None,
) -> LegalAddress:
    """Build a LegalAddress from the flat-field style used by the UK parser."""
    path: list[TreePathStep] = []
    if container == "schedule":
        if section is not None:
            path.append(("schedule", section))
        if part:
            path.append(("part", part))
        if chapter:
            path.append(("chapter", chapter))
        if subsection:
            path.append(("paragraph", subsection))
        if item:
            path.append(("paragraph", item))
    else:
        if part:
            path.append(("part", part))
        if chapter:
            path.append(("chapter", chapter))
        if section:
            path.append(("section", section))
        if subsection:
            path.append(("subsection", subsection))
        if item:
            path.append(("paragraph", item))
    return LegalAddress(path=tuple(path), special=special)


def _addr_container(addr: LegalAddress) -> str:
    """Return the top-level container kind of a LegalAddress."""
    return uk_addr_container(addr)


def _addr_field(addr: LegalAddress, kind: str) -> Optional[str]:
    """Return the label for the first path segment matching *kind*, or None."""
    for k, lbl in addr.path:
        if k == kind:
            return lbl
    return None


class UKScheduleTargetLevels(NamedTuple):
    paragraph: Optional[str]
    subparagraph: Optional[str]
    item_labels: list[str]


def _addr_leaf_label(addr: LegalAddress) -> Optional[str]:
    """Return the deepest meaningful label from a LegalAddress path."""
    for _kind, lbl in reversed(addr.path):
        if lbl:
            return lbl
    return None


def _addr_leaf_kind(addr: LegalAddress) -> Optional[str]:
    """Return the deepest path kind from a LegalAddress, if any."""
    if not addr.path:
        return None
    return addr.path[-1][0]


def _schedule_target_levels(addr: LegalAddress) -> UKScheduleTargetLevels:
    """Return typed schedule descendant labels as (paragraph, subparagraph, items)."""
    paragraph = None
    subparagraph = None
    items: list[str] = []
    for kind, lbl in addr.path:
        if not lbl:
            continue
        if kind == "paragraph":
            paragraph = lbl
        elif kind == "subparagraph":
            subparagraph = lbl
        elif kind in {"item", "point"}:
            items.append(lbl)
    return UKScheduleTargetLevels(
        paragraph=paragraph,
        subparagraph=subparagraph,
        item_labels=items,
    )


def _looks_like_lettered_item_label(label: str) -> bool:
    return bool(re.fullmatch(r"[a-z]+", (label or "").strip(), re.I))


def _canonicalize_schedule_paragraph_eid_label(label: Optional[str]) -> str:
    """Canonicalize schedule paragraph labels for exact eId lookup.

    UK schedule paragraph ids can surface as lower-case aliases like ``9a`` or
    ``116a`` in affected-target text, while the parsed/oracle eId may retain an
    upper-case alpha suffix such as ``9A`` or ``116A``.

    We keep the normalization narrow: only the first alpha suffix immediately
    following leading digits is upper-cased, leaving any later nested item
    suffixes untouched (for example ``116a-a`` -> ``116A-a``).
    """

    cleaned = _clean_num(label or "")
    if not cleaned:
        return ""
    match = re.fullmatch(r"(\d+)([a-z])(?P<rest>.*)", cleaned)
    if match:
        return f"{match.group(1)}{match.group(2).upper()}{match.group('rest')}"
    return cleaned


def _canonicalize_eid_tail_label(label: Optional[str]) -> str:
    """Canonicalize descendant eId suffixes without Romanizing letter labels."""
    raw = str(label or "").strip().replace("\u00a0", " ")
    if not raw:
        return ""
    stripped = raw.strip("().").lower()
    if re.fullmatch(r"[a-z]+", stripped):
        return stripped
    return _clean_num(raw)


def _uk_canonicalize_eid_letter_case(eid: str) -> str:
    """Uppercase the letter suffix of digit-led provision-number eId segments.

    UK eId convention: an inserted-provision number's letter suffix is uppercase
    (``section-20A``, ``section-24-3A``, ``section-23ZA``, ``section-23C-1A``),
    while pure-letter labels (``paragraph-a``, lettered ``za``, roman ``ia``) and
    kind names (``section``, ``p1group``) stay lowercase. Operates per ``-``
    segment and only rewrites segments that begin with a digit and contain a
    letter, so kind names and lettered/roman labels are untouched.

    This is for EMITTED node eId attributes only. Matching keys (oracle
    ``eid_map`` keys, grounding flat candidates) stay lowercase and must NOT be
    routed through this helper \u2014 they are compared case-insensitively.
    """
    if not eid:
        return eid
    out: list[str] = []
    for seg in eid.split("-"):
        if seg and seg[0].isdigit() and any(c.isalpha() for c in seg):
            out.append(seg.upper())
        else:
            out.append(seg)
    return "-".join(out)


def _action_name(action: StructuralAction | str) -> str:
    if isinstance(action, StructuralAction):
        return action.value
    return str(action)


def _order_schedule_materialization_ops(ops: list[LegalOperation]) -> list[LegalOperation]:
    """Prioritize materializing structural ops before dependent text edits within a source."""
    structural_materialization_targets = {
        (
            str(getattr(op.source, "effective", "") or ""),
            str(getattr(op.source, "statute_id", "") or ""),
            tuple(op.target.path or ()),
        )
        for op in ops
        if _action_name(op.action) in {"insert", "replace"}
    }

    def _rank(op: LegalOperation) -> int:
        if op.target.special is FacetKind.HEADING and _action_name(op.action) in {"text_replace", "text_repeal"}:
            structural_key = (
                str(getattr(op.source, "effective", "") or ""),
                str(getattr(op.source, "statute_id", "") or ""),
                tuple(op.target.path or ()),
            )
            if structural_key in structural_materialization_targets:
                return 1
            return -1
        if _action_name(op.action) in {"insert", "replace", "repeal", "renumber"}:
            return 0
        if _action_name(op.action) in {"text_replace", "text_repeal"}:
            return 1
        return 2

    sorted_ops = [
        op
        for _idx, op in sorted(
            enumerate(ops),
            key=lambda item: (
                str(getattr(item[1].source, "effective", "") or ""),
                str(getattr(item[1].source, "statute_id", "") or ""),
                _rank(item[1]),
                item[0],
            ),
        )
    ]

    from collections import defaultdict
    groups = defaultdict(list)
    for op in sorted_ops:
        eff = str(getattr(op.source, "effective", "") or "")
        stat_id = str(getattr(op.source, "statute_id", "") or "")
        groups[(eff, stat_id)].append(op)

    final_ops = []
    for key in sorted(groups.keys()):
        group_ops = groups[key]
        final_ops.extend(_dependency_sort_ops(group_ops))
    return final_ops


def _op_depends_on(op1: LegalOperation, op2: LegalOperation) -> bool:
    # If op2 is a renumbering operation, and its destination is a prefix of op1's target path
    if _action_name(op2.action) == "renumber" and op2.destination is not None:
        dest_path = tuple(op2.destination.path or ())
        target_path = tuple(op1.target.path or ())
        if len(target_path) >= len(dest_path) and target_path[:len(dest_path)] == dest_path:
            return True
    return False


def _dependency_sort_ops(ops: list[LegalOperation]) -> list[LegalOperation]:
    n = len(ops)
    adj = {i: set() for i in range(n)}
    in_degree = [0] * n
    for i in range(n):
        for j in range(n):
            if i != j:
                if _op_depends_on(ops[j], ops[i]):
                    if j not in adj[i]:
                        adj[i].add(j)
                        in_degree[j] += 1
    from heapq import heappush, heappop, heapify
    queue = [i for i in range(n) if in_degree[i] == 0]
    heapify(queue)
    result = []
    while queue:
        u = heappop(queue)
        result.append(ops[u])
        for v in adj[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                heappush(queue, v)
    if len(result) < n:
        seen = set(result)
        for op in ops:
            if op not in seen:
                result.append(op)
    return result


def _looks_like_roman_subitem_label(label: str) -> bool:
    cleaned = (label or "").strip().lower()
    return bool(cleaned) and bool(re.fullmatch(r"[ivx]+", cleaned))
