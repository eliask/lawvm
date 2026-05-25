"""Affected-target parsing for UK effects metadata."""
from __future__ import annotations

import re
from typing import Optional

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.roman import (
    arabic_to_roman as _shared_arabic_to_roman,
    roman_to_arabic as _shared_roman_to_arabic,
)
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _looks_like_lettered_item_label,
    _looks_like_roman_subitem_label,
    _make_address,
)
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.heading_facets import (
    _expand_heading_facet_section_range_ref,
    _is_direct_section_paragraph_ref,
    _is_heading_only_ref,
)
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


def _split_stemmed_alnum(group: str) -> Optional[tuple[str, str]]:
    match = re.fullmatch(r"((?:\d+[A-Z]*|[A-Z]+\d+[A-Z]*))([A-Z])", group, re.I)
    if match is None:
        return None
    return match.group(1).upper(), match.group(2).upper()


def _is_sibling_group_family(groups: list[str]) -> bool:
    if all(group.isdigit() for group in groups):
        return True
    roman_values = [_shared_roman_to_arabic(group) for group in groups]
    if (
        len(groups) >= 2
        and all(_looks_like_roman_subitem_label(group) for group in groups)
        and all(value is not None for value in roman_values)
    ):
        return True
    if all(re.fullmatch(r"\d+[A-Z]*", group, re.I) for group in groups):
        return True
    if all(re.fullmatch(r"[A-Z]+", group, re.I) for group in groups):
        alpha_lengths = {len(group) for group in groups}
        if alpha_lengths <= {1}:
            return True
        if alpha_lengths <= {1, 2}:
            single_stems = {group.upper() for group in groups if len(group) == 1}
            if single_stems:
                return all(
                    len(group) == 1 or any(group.upper().startswith(stem) for stem in single_stems)
                    for group in groups
                )
            return len(alpha_lengths) == 1
        return len(alpha_lengths) == 1
    alnum = [re.fullmatch(r"(\d+)([A-Z])", group, re.I) for group in groups]
    if (
        bool(alnum)
        and all(match is not None for match in alnum)
        and len({match.group(1) for match in alnum if match is not None}) == 1
    ):
        return True
    stemmed = [_split_stemmed_alnum(group) for group in groups]
    return (
        bool(stemmed)
        and all(pair is not None for pair in stemmed)
        and len({pair[0] for pair in stemmed if pair is not None}) == 1
    )


def _expand_parenthesized_range(prefix: str, start_str: str, end_str: str) -> Optional[list[str]]:
    raw_start_str = start_str
    raw_end_str = end_str
    start_str = start_str.upper()
    end_str = end_str.upper()
    roman_start = _shared_roman_to_arabic(start_str)
    roman_end = _shared_roman_to_arabic(end_str)
    if (
        roman_start is not None
        and roman_end is not None
        and roman_end > roman_start
        and roman_end - roman_start < 50
    ):
        if raw_start_str.islower():
            return [
                f"{prefix}({_shared_arabic_to_roman(value).lower()})"
                for value in range(roman_start, roman_end + 1)
            ]
        return [f"{prefix}({_shared_arabic_to_roman(value)})" for value in range(roman_start, roman_end + 1)]

    if len(start_str) == 1 and len(end_str) == 1 and start_str.isalpha() and end_str.isalpha():
        return [f"{prefix}({chr(c)})" for c in range(ord(start_str), ord(end_str) + 1)]

    if (
        len(start_str) == len(end_str)
        and len(start_str) > 1
        and start_str.isalpha()
        and end_str.isalpha()
        and start_str[:-1] == end_str[:-1]
        and ord(start_str[-1]) <= ord(end_str[-1])
    ):
        stem = raw_start_str[:-1]
        return [f"{prefix}({stem}{chr(c)})" for c in range(ord(raw_start_str[-1]), ord(raw_end_str[-1]) + 1)]

    if (
        len(start_str) == 1
        and len(end_str) == 2
        and start_str.isalpha()
        and end_str.isalpha()
        and end_str.startswith(start_str)
    ):
        end_letter = raw_end_str[-1].lower()
        return [f"{prefix}({raw_start_str})"] + [
            f"{prefix}({raw_start_str}{chr(c)})" for c in range(ord("a"), ord(end_letter) + 1)
        ]

    stemmed_start = _split_stemmed_alnum(start_str)
    stemmed_end = _split_stemmed_alnum(end_str)
    if stemmed_start is not None and stemmed_end is not None and stemmed_start[0] == stemmed_end[0]:
        return [
            f"{prefix}({stemmed_start[0]}{chr(c)})" for c in range(ord(stemmed_start[1]), ord(stemmed_end[1]) + 1)
        ]

    ms = re.match(r"^(\d+)([A-Z])$", start_str)
    me = re.match(r"^(\d+)([A-Z])$", end_str)
    if ms and me and ms.group(1) == me.group(1):
        base_n = ms.group(1)
        return [f"{prefix}({base_n}{chr(c)})" for c in range(ord(ms.group(2)), ord(me.group(2)) + 1)]

    if start_str.isdigit() and me and me.group(1) == start_str:
        return [f"{prefix}({start_str})"] + [
            f"{prefix}({start_str}{chr(c)})" for c in range(ord("A"), ord(me.group(2)) + 1)
        ]

    if start_str.isdigit() and me is not None:
        start = int(start_str)
        end = int(me.group(1))
        suffix = me.group(2).upper()
        if suffix and end > start and end - start < 100:
            return [f"{prefix}({n})" for n in range(start, end + 1)] + [
                f"{prefix}({end}{chr(c)})" for c in range(ord("A"), ord(suffix) + 1)
            ]

    if start_str.isdigit() and end_str.isdigit():
        start = int(start_str)
        end = int(end_str)
        if end > start and end - start < 100:
            return [f"{prefix}({n})" for n in range(start, end + 1)]

    return None


def _split_metadata_provisions(prov_str: str) -> list[str]:
    if not prov_str:
        return []

    # Split by comma first
    parts = [p.strip() for p in prov_str.split(",") if p.strip()]

    # Expand space-separated section lists: "s. 3A 3B" -> ["s. 3A", "s. 3B"]
    # Pattern: kind-abbreviation followed by two or more bare alphanumeric IDs (no parentheses).
    # This is distinct from "s. 3A(1)" (subsection ref, parentheses present).
    expanded_parts = []
    for p in parts:
        heading_facet_range_refs = _expand_heading_facet_section_range_ref(p)
        if heading_facet_range_refs:
            expanded_parts.extend(heading_facet_range_refs)
            continue
        if _is_heading_only_ref(p):
            expanded_parts.append(p)
            continue
        p_for_space_list = re.sub(r"\s+and\s+cross[-\s]?headings?\b.*$", "", p, flags=re.I).strip()
        m = re.match(r"^(s\.|ss\.|para\.|art\.|ch\.)\s+([0-9A-Z]+)(\s+[0-9A-Z]+)+$", p_for_space_list, re.I)
        if m:
            kind_abbr = m.group(1)
            nums = re.findall(r"[0-9A-Z]+", p_for_space_list[len(kind_abbr) :], re.I)
            if any(num.lower() == "table" for num in nums):
                expanded_parts.append(p)
                continue
            for n in nums:
                expanded_parts.append(f"{kind_abbr} {n}")
            continue
        m = re.match(
            r"^(.*?\b(?:para\.|paragraph|s\.|ss\.|section|art\.|article|ch\.|chapter)\s+)([0-9A-Z]+)(\s+[0-9A-Z]+)+$",
            p_for_space_list,
            re.I,
        )
        if m:
            prefix = m.group(1)
            nums = re.findall(r"[0-9A-Z]+", p_for_space_list[len(prefix) :], re.I)
            if any(num.lower() == "table" for num in nums):
                expanded_parts.append(p)
                continue
            for n in nums:
                expanded_parts.append(f"{prefix}{n}".strip())
        else:
            expanded_parts.append(p)
    parts = expanded_parts

    # Handle ranges like "ss. 10A-10C".
    # Range endpoints must contain at least one digit: pure-word compounds like
    # "cross-heading" must not be expanded.
    all_parts = []
    for p in parts:
        repeated_anchor_m = re.match(
            r"^(.*?\b(?:para\.|paragraph|s\.|ss\.|section|art\.|article)\s+)(\d+(?:\([0-9A-Z]+\))+)\s+and\s+(\d+(?:\([0-9A-Z]+\))+)$",
            p,
            re.I,
        )
        if repeated_anchor_m:
            prefix = repeated_anchor_m.group(1)
            all_parts.append(f"{prefix}{repeated_anchor_m.group(2)}")
            all_parts.append(f"{prefix}{repeated_anchor_m.group(3)}")
            continue

        range_plus_ws_group_m = re.match(
            r"^(.*?)\(([0-9A-Z]+)\)-\(([0-9A-Z]+)\)((?:\s+\([0-9A-Z]+\))+)$",
            p,
            re.I,
        )
        if range_plus_ws_group_m:
            prefix = range_plus_ws_group_m.group(1).rstrip()
            expanded_range = _expand_parenthesized_range(
                prefix,
                range_plus_ws_group_m.group(2),
                range_plus_ws_group_m.group(3),
            )
            trailing_raw = re.findall(r"\(([0-9A-Z]+)\)", range_plus_ws_group_m.group(4), re.I)
            if expanded_range and trailing_raw:
                all_parts.extend(expanded_range)
                for group in trailing_raw:
                    all_parts.append(f"{prefix}({group})")
                continue

        adjacent_group_m = re.match(
            r"^(.*?)((?:\([0-9A-Z]+\)){2,})$",
            p,
            re.I,
        )
        if adjacent_group_m:
            prefix = adjacent_group_m.group(1)
            all_groups = re.findall(r"\(([0-9A-Z]+)\)", adjacent_group_m.group(2), re.I)
            if len(all_groups) >= 2:
                if _is_sibling_group_family(all_groups) and not (
                    len(all_groups) == 2
                    and _looks_like_lettered_item_label(all_groups[0])
                    and _looks_like_roman_subitem_label(all_groups[1])
                ):
                    for group in all_groups:
                        all_parts.append(f"{prefix}({group})")
                    continue
                if (
                    len(all_groups) == 3
                    and _is_sibling_group_family([all_groups[0], all_groups[2]])
                    and _looks_like_lettered_item_label(all_groups[1])
                ):
                    all_parts.append(f"{prefix}({all_groups[0]})({all_groups[1]})")
                    all_parts.append(f"{prefix}({all_groups[2]})")
                    continue
                # Fixed-prefix sibling suffixes: ``s. 54(8)(b)(c)`` means
                # paragraph siblings (b) and (c) under subsection (8), not
                # nested ``(8)(b)(c)``. Likewise ``Sch. 1 para. 1(1)(b)(c)``
                # means item siblings under ``(1)(1)``.
                for split_at in range(1, len(all_groups) - 1):
                    fixed_groups = all_groups[:split_at]
                    sibling_groups = all_groups[split_at:]
                    if (
                        _looks_like_lettered_item_label(sibling_groups[0])
                        and not _looks_like_roman_subitem_label(sibling_groups[0])
                        and any(_looks_like_roman_subitem_label(group) for group in sibling_groups[1:])
                    ):
                        continue
                    if not _is_sibling_group_family(sibling_groups):
                        continue
                    fixed_prefix = prefix + "".join(f"({group})" for group in fixed_groups)
                    for group in sibling_groups:
                        all_parts.append(f"{fixed_prefix}({group})")
                    break
                if all_parts and all_parts[-1].startswith(prefix):
                    continue

        # Whitespace-compressed sibling refs: "s. 62(7) (8)" means sibling
        # subsections (7) and (8), not a nested paragraph 8 under subsection 7.
        ws_group_m = re.match(
            r"^(.*?)(\(([0-9A-Z]+)\))((?:\s+\([0-9A-Z]+\))+)$",
            p,
            re.I,
        )
        if ws_group_m:
            prefix = ws_group_m.group(1)
            first_raw = ws_group_m.group(3)
            trailing_raw = re.findall(r"\(([0-9A-Z]+)\)", ws_group_m.group(4), re.I)
            if trailing_raw:
                all_groups = [first_raw, *trailing_raw]
                if _is_sibling_group_family(all_groups):
                    for group in all_groups:
                        all_parts.append(f"{prefix}({group})")
                    continue

        # Whitespace-compressed sibling subsection ranges:
        # ``s. 13(1A) (9)-(12)`` means subsection (1A) plus sibling
        # subsections (9) to (12), not paragraphs (9)-(12) under subsection
        # (1A). Keep this narrower than general nested parenthesized ranges:
        # only a single numeric/alphanumeric carried group may introduce the
        # sibling range.
        ws_group_range_m = re.match(
            r"^(.*?\(([0-9]+[A-Z]*)\))\s+\(([0-9]+[A-Z]*)\)-\(([0-9]+[A-Z]*)\)$",
            p,
            re.I,
        )
        if ws_group_range_m:
            base_ref = ws_group_range_m.group(1)
            base_prefix = re.sub(r"\([0-9]+[A-Z]*\)$", "", base_ref, flags=re.I).rstrip()
            expanded_range = _expand_parenthesized_range(
                base_prefix,
                ws_group_range_m.group(3),
                ws_group_range_m.group(4),
            )
            if (
                expanded_range
                and len(re.findall(r"\(([0-9A-Z]+)\)", base_ref, re.I)) == 1
                and _is_sibling_group_family(
                    [
                        ws_group_range_m.group(2),
                        ws_group_range_m.group(3),
                        ws_group_range_m.group(4),
                    ]
                )
            ):
                all_parts.append(base_ref)
                all_parts.extend(expanded_range)
                continue

        # Strip "and cross-heading(s)" / "and cross heading(s)" qualifier
        # suffix so ranges like "s. 9-12 and cross-heading" and
        # "Sch. 6 para. 45-48 and cross-headings" expand correctly.
        p_for_range = re.sub(r"\s+and\s+cross[-\s]?headings?\b.*$", "", p, flags=re.I).strip()

        # Parenthesized subsection range: "s. 18(7A)-(7D)" -> "s. 18(7A)", ...
        paren_range_m = re.match(r"^(.*?)\(([0-9A-Z]+)\)-\(([0-9A-Z]+)\)$", p_for_range, re.I)
        if paren_range_m:
            prefix = paren_range_m.group(1).rstrip()
            expanded_range = _expand_parenthesized_range(
                prefix,
                paren_range_m.group(2),
                paren_range_m.group(3),
            )
            if expanded_range:
                all_parts.extend(expanded_range)
                continue

        range_m = re.search(
            r"^(.*?)\s?([0-9A-Z]+)\s*[-–—]\s*([0-9A-Z]+)$",
            p_for_range,
            re.I,
        )
        if range_m:
            prefix = range_m.group(1).strip()
            start_str = range_m.group(2)
            end_str = range_m.group(3)
            if not any(c.isdigit() for c in start_str) and not any(c.isdigit() for c in end_str):
                all_parts.append(p)
                continue

            if start_str.isdigit() and end_str.isdigit():
                start = int(start_str)
                end = int(end_str)
                if end > start and end - start < 100:
                    for n in range(start, end + 1):
                        all_parts.append(f"{prefix} {n}".strip())
                    continue

            m_start = re.match(r"^(\d+)([A-Z]*)$", start_str, re.I)
            m_end = re.match(r"^(\d+)([A-Z]*)$", end_str, re.I)
            if m_start and m_end and m_start.group(1) == m_end.group(1):
                base = m_start.group(1)
                s_let = m_start.group(2).upper()
                e_let = m_end.group(2).upper()
                if s_let and e_let and len(s_let) == 1 and len(e_let) == 1:
                    for c in range(ord(s_let), ord(e_let) + 1):
                        all_parts.append(f"{prefix} {base}{chr(c)}".strip())
                    continue

            # Mixed numeric -> alphanumeric range: "s. 60-61A" expands to
            # "s. 60", "s. 61", "s. 61A".
            if m_start and m_end and not m_start.group(2):
                start_base = int(m_start.group(1))
                end_base = int(m_end.group(1))
                end_suffix = m_end.group(2).upper()
                if end_suffix and len(end_suffix) == 1 and end_base >= start_base and end_base - start_base < 100:
                    for n in range(start_base, end_base + 1):
                        all_parts.append(f"{prefix} {n}".strip())
                    for c in range(ord("A"), ord(end_suffix) + 1):
                        all_parts.append(f"{prefix} {end_base}{chr(c)}".strip())
                    continue

            all_parts.append(f"{prefix} {start_str}".strip())
            all_parts.append(f"{prefix} {end_str}".strip())
        else:
            all_parts.append(p)

    carried_parts: list[str] = []
    active_prefix: Optional[str] = None
    subordinate_prefix_re = re.compile(
        r"^(?:para(?:graph)?|sub-?paragraph|item|point)\b",
        re.I,
    )
    for p in all_parts:
        part = p.strip()
        if not part:
            continue
        if _is_heading_only_ref(part):
            carried_parts.append(part)
            continue

        groups = re.findall(r"\(([0-9A-Z]+)\)", part, re.I)
        if active_prefix and groups and subordinate_prefix_re.match(part):
            carried_parts.append(f"{active_prefix}{''.join(f'({group})' for group in groups)}")
            continue

        carried_parts.append(part)
        if re.match(r"^(?:s\.|ss\.|section|sch\.?|schedule|art\.|article)(?:\s|\(|$)", part, re.I):
            active_prefix = part.rstrip(" ,;")

    return carried_parts
