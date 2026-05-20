"""Affected-target parsing for UK effects metadata."""
from __future__ import annotations

import re
from typing import Optional

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _looks_like_lettered_item_label,
    _make_address,
)
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.heading_facets import _is_direct_section_paragraph_ref
from lawvm.uk_legislation.provision_extractor import _parse_ref


def _schedule_part_context_removed_target(target: LegalAddress) -> Optional[LegalAddress]:
    """Drop a non-addressable schedule Part context from a paragraph target."""

    if _addr_container(target) != "schedule":
        return None
    if _addr_field(target, "part") is None or _addr_field(target, "paragraph") is None:
        return None
    stripped_path = tuple((kind, label) for kind, label in target.path if kind != "part")
    if stripped_path == target.path:
        return None
    return LegalAddress(path=stripped_path, special=target.special)


def _normalize_affected_target_ref(ref: str) -> str:
    """Insert missing separators in UK affected-target refs before parsing."""
    ref = ref.strip()
    if not ref:
        return ref
    return re.sub(
        r"(?<=\d)(?=(?:paragraph|subsection|sub-paragraph|subparagraph|item|point|section|article|rule)\b)",
        " ",
        ref,
        flags=re.I,
    )


def _parse_affected_target(ref: str) -> LegalAddress:
    """Parse 'Sch. 1 Pt. I Ch. 1 para. 1' into a LegalAddress."""
    ref = _normalize_affected_target_ref(ref)
    if ref.strip().lower() == "act":
        return _make_address(container="section", special=FacetKind.WHOLE_ACT)
    if re.fullmatch(r"sch(?:edule)?\.?", ref.strip(), re.I):
        return canonicalize_uk_address(LegalAddress(path=(("schedule", ""),)))

    path = _parse_ref(ref)
    schedule_idx = next((i for i, (kind, _num) in enumerate(path) if kind == "schedule"), None)
    if schedule_idx is not None:
        schedule_tokens = [path[schedule_idx], *path[schedule_idx + 1 :], *reversed(path[:schedule_idx])]
        schedule_path: list[tuple[str, str]] = []
        schedule_depth = 0
        for kind, num in schedule_tokens:
            if kind == "schedule":
                schedule_path.append(("schedule", num))
            elif kind == "part":
                schedule_path.append(("part", num))
            elif kind == "chapter":
                schedule_path.append(("chapter", num))
            elif kind in ("section", "article", "rule", "regulation"):
                schedule_path.append(("section", num))
            elif kind in ("paragraph", None):
                if schedule_depth == 0:
                    schedule_path.append(("paragraph", num))
                elif schedule_depth == 1:
                    if _looks_like_lettered_item_label(num):
                        schedule_path.append(("item", num))
                    else:
                        schedule_path.append(("subparagraph", num))
                else:
                    schedule_path.append(("item", num))
                schedule_depth += 1
        if schedule_path:
            return canonicalize_uk_address(LegalAddress(path=tuple(schedule_path)))

    body_descendant_tokens = [(kind, num) for kind, num in path if kind in ("paragraph", None)]
    if _is_direct_section_paragraph_ref(ref):
        body_path: list[tuple[str, str]] = []
        body_depth = 0
        for kind, num in path:
            if kind == "part":
                body_path.append(("part", num))
            elif kind == "chapter":
                body_path.append(("chapter", num))
            elif kind in ("section", "article", "rule", "regulation"):
                body_path.append(("section", num))
            elif kind in ("paragraph", None):
                if not body_path:
                    body_path.append(("section", num))
                    continue
                if body_depth == 0:
                    body_path.append(("paragraph", num))
                elif body_depth == 1:
                    body_path.append(("subparagraph", num))
                else:
                    body_path.append(("item", num))
                body_depth += 1
        if body_path:
            return LegalAddress(path=tuple(body_path))

    if len(body_descendant_tokens) > 2:
        body_path: list[tuple[str, str]] = []
        body_depth = 0
        for kind, num in path:
            if kind == "part":
                body_path.append(("part", num))
            elif kind == "chapter":
                body_path.append(("chapter", num))
            elif kind in ("section", "article", "rule", "regulation"):
                body_path.append(("section", num))
            elif kind in ("paragraph", None):
                if not body_path:
                    body_path.append(("section", num))
                    continue
                if body_depth == 0:
                    body_path.append(("subsection", num))
                elif body_depth == 1:
                    body_path.append(("paragraph", num))
                elif body_depth == 2:
                    body_path.append(("subparagraph", num))
                else:
                    body_path.append(("item", num))
                body_depth += 1
        if body_path:
            return canonicalize_uk_address(LegalAddress(path=tuple(body_path)))

    container: str = "section"
    section = None
    part = None
    chapter = None
    subsection = None
    item = None
    for kind, num in path:
        if kind == "schedule":
            container = "schedule"
            section = num
        elif kind == "part":
            part = num
        elif kind == "chapter":
            chapter = num
        elif kind in ("section", "article", "rule", "regulation"):
            section = num
        elif kind == "paragraph":
            if container == "schedule":
                if not subsection:
                    subsection = num
                else:
                    item = num
            else:
                if not section:
                    section = num
                elif not subsection:
                    subsection = num
                else:
                    item = num
        elif kind is None:
            if container == "schedule":
                if not subsection:
                    subsection = num
                else:
                    item = num
            else:
                if not section:
                    section = num
                elif not subsection:
                    subsection = num
                else:
                    item = num
    return canonicalize_uk_address(
        _make_address(
            container=container,
            section=section,
            part=part,
            chapter=chapter,
            subsection=subsection,
            item=item,
        )
    )
