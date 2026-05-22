"""Internal UK address and label helpers.

These helpers are pure normalization/order utilities used by the UK replay
compiler. They deliberately do not resolve targets against live state.
"""
from __future__ import annotations

from functools import lru_cache
import re
from typing import Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation
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
    path: list[tuple[str, str]] = []
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


def _schedule_target_levels(addr: LegalAddress) -> tuple[Optional[str], Optional[str], list[str]]:
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
    return paragraph, subparagraph, items


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

    return [
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


def _looks_like_roman_subitem_label(label: str) -> bool:
    cleaned = (label or "").strip().lower()
    return bool(cleaned) and bool(re.fullmatch(r"[ivx]+", cleaned))
