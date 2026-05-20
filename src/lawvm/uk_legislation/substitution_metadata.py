"""UK effect-feed substitution metadata helpers.

These helpers interpret "substituted for ..." metadata as target evidence.
They do not inspect replay state or mutate payloads; callers still own lowering,
operation emission, and adjudication records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _addr_leaf_label,
)
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.target_parser import _parse_affected_target, _split_metadata_provisions
from lawvm.uk_legislation.uk_grafter import _clean_num


@dataclass(frozen=True)
class UKSourceLabelChangingSubstitution:
    source_ref: str
    source_target: LegalAddress
    replacement_ref: str
    replacement_target: LegalAddress


def _retarget_substituted_series_to_replaced_anchor(
    effect_type: str,
    target_refs: list[str],
) -> list[str]:
    """Retarget the first replacement when metadata names the replacement series."""
    raw = (effect_type or "").strip()
    if not raw.lower().startswith("substituted for "):
        return target_refs
    if not target_refs:
        return target_refs

    anchor_refs = _split_metadata_provisions(raw[len("substituted for ") :].strip())
    if not anchor_refs:
        return target_refs

    if len(target_refs) == 1 and len(anchor_refs) >= 2:
        try:
            anchor_target = _parse_affected_target(anchor_refs[0])
            replacement_target = _parse_affected_target(target_refs[0])
        except Exception:
            return target_refs

        anchor_section = _addr_field(anchor_target, "section") or _addr_field(anchor_target, "schedule")
        replacement_section = _addr_field(replacement_target, "section") or _addr_field(replacement_target, "schedule")
        anchor_leaf_kind = _addr_leaf_kind(anchor_target)
        replacement_leaf_kind = _addr_leaf_kind(replacement_target)
        anchor_leaf = _clean_num(_addr_leaf_label(anchor_target) or "")
        replacement_leaf = _clean_num(_addr_leaf_label(replacement_target) or "")
        if (
            anchor_section
            and anchor_section == replacement_section
            and anchor_leaf_kind
            and anchor_leaf_kind == replacement_leaf_kind
            and anchor_leaf
            and replacement_leaf
            and replacement_leaf != anchor_leaf
            and replacement_leaf.startswith(anchor_leaf)
        ):
            return [anchor_refs[0]]
        return target_refs

    if len(target_refs) < 2 or len(anchor_refs) != 1:
        return target_refs

    try:
        anchor_target = _parse_affected_target(anchor_refs[0])
        first_target = _parse_affected_target(target_refs[0])
        second_target = _parse_affected_target(target_refs[1])
    except Exception:
        return target_refs

    anchor_section = _addr_field(anchor_target, "section") or _addr_field(anchor_target, "schedule")
    first_section = _addr_field(first_target, "section") or _addr_field(first_target, "schedule")
    second_section = _addr_field(second_target, "section") or _addr_field(second_target, "schedule")
    anchor_sub = _clean_num(_addr_field(anchor_target, "subsection") or "")
    first_sub = _clean_num(_addr_field(first_target, "subsection") or "")
    second_sub = _clean_num(_addr_field(second_target, "subsection") or "")

    if not anchor_section or anchor_section != first_section or anchor_section != second_section:
        return target_refs
    if not anchor_sub or not first_sub or not second_sub:
        return target_refs
    if first_sub == anchor_sub or not first_sub.startswith(anchor_sub):
        return target_refs
    if not second_sub.startswith(anchor_sub):
        return target_refs

    retargeted = list(target_refs)
    retargeted[0] = anchor_refs[0]
    return retargeted


def _source_label_changing_substitution(
    effect_type: str,
    target_refs: list[str],
) -> Optional[UKSourceLabelChangingSubstitution]:
    """Return a source-owned old-label target for a labelled substitution.

    UK effects sometimes record a row as "substituted for <old sibling>" while
    affected_provisions names the new sibling label.  The executable replace
    target is the old sibling; the payload label remains the new sibling.
    """
    raw = (effect_type or "").strip()
    if not raw.lower().startswith("substituted for ") or raw.lower() == "substituted for words":
        return None
    if len(target_refs) != 1:
        return None
    anchor_refs = _split_metadata_provisions(raw[len("substituted for ") :].strip())
    if not anchor_refs:
        return None
    source_ref = anchor_refs[0]
    replacement_ref = target_refs[0]
    try:
        source_target = canonicalize_uk_address(_parse_affected_target(source_ref))
        replacement_target = canonicalize_uk_address(_parse_affected_target(replacement_ref))
    except Exception:
        return None
    if tuple(source_target.path) == tuple(replacement_target.path):
        return None
    if tuple(source_target.path[:-1]) != tuple(replacement_target.path[:-1]):
        return None
    if _addr_leaf_kind(source_target) != _addr_leaf_kind(replacement_target):
        return None
    source_label = _clean_num(_addr_leaf_label(source_target) or "")
    replacement_label = _clean_num(_addr_leaf_label(replacement_target) or "")
    if not source_label or not replacement_label or source_label == replacement_label:
        return None
    return UKSourceLabelChangingSubstitution(
        source_ref=source_ref,
        source_target=source_target,
        replacement_ref=replacement_ref,
        replacement_target=replacement_target,
    )


def _source_label_changing_substitution_series(
    effect_type: str,
    target_refs: list[str],
) -> tuple[UKSourceLabelChangingSubstitution, ...]:
    raw = (effect_type or "").strip()
    if not raw.lower().startswith("substituted for ") or raw.lower() == "substituted for words":
        return ()
    anchor_refs = _split_metadata_provisions(raw[len("substituted for ") :].strip())
    if not anchor_refs:
        return ()
    if len(target_refs) == 1:
        single = _source_label_changing_substitution(effect_type, target_refs)
        return (single,) if single is not None else ()
    if len(anchor_refs) != len(target_refs):
        return ()

    substitutions: list[UKSourceLabelChangingSubstitution] = []
    for source_ref, replacement_ref in zip(anchor_refs, target_refs):
        single = _source_label_changing_substitution(
            f"substituted for {source_ref}",
            [replacement_ref],
        )
        if single is None:
            return ()
        substitutions.append(single)
    return tuple(substitutions)


def _source_text_schedule_paragraph_target_override(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[LegalAddress]:
    """Return explicit ``paragraph X of schedule Y`` target from source text."""
    if not extracted_text or _addr_container(target) != "schedule":
        return None
    current_schedule = _clean_num(_addr_field(target, "schedule") or "")
    current_paragraph = _clean_num(_addr_field(target, "paragraph") or "")
    if not current_schedule or not current_paragraph:
        return None
    match = re.search(
        r"\bIn\s+paragraph\s+([0-9A-Za-z]+)\s+of\s+schedule\s+([0-9A-Za-z]+)\s+to\b",
        extracted_text,
        flags=re.I,
    )
    if match is None:
        return None
    source_paragraph = _clean_num(match.group(1))
    source_schedule = _clean_num(match.group(2))
    if not source_paragraph or source_schedule != current_schedule:
        return None
    if source_paragraph == current_paragraph:
        return None
    return LegalAddress(
        path=(("schedule", current_schedule), ("paragraph", source_paragraph)),
        special=target.special,
    )


def _repeal_tail_for_substituted_series_replacement(
    effect_type: str,
    original_target_refs: list[str],
) -> list[str]:
    """Return trailing replaced refs that should compile as repeals.

    Some UK effects are recorded as:
      effect_type="substituted for s. 3(5)(6)"
      affected_provisions="s. 3(5A)"

    Semantically this means the first replaced anchor becomes the new payload
    target and the remaining replaced anchors are repealed.

    The feed may also name the first replaced anchor itself:
      effect_type="substituted for s. 5(5)(6)"
      affected_provisions="s. 5(5)"

    That still authorizes only the trailing anchor repeal. It does not widen
    the replacement target beyond the explicitly named first anchor.
    """
    raw = (effect_type or "").strip()
    if not raw.lower().startswith("substituted for "):
        return []
    if len(original_target_refs) != 1:
        return []

    anchor_refs = _split_metadata_provisions(raw[len("substituted for ") :].strip())
    if len(anchor_refs) < 2:
        return []

    try:
        first_anchor = _parse_affected_target(anchor_refs[0])
        replacement_target = _parse_affected_target(original_target_refs[0])
    except Exception:
        return []

    anchor_section = _addr_field(first_anchor, "section") or _addr_field(first_anchor, "schedule")
    replacement_section = _addr_field(replacement_target, "section") or _addr_field(replacement_target, "schedule")
    anchor_leaf_kind = _addr_leaf_kind(first_anchor)
    replacement_leaf_kind = _addr_leaf_kind(replacement_target)
    anchor_leaf = _clean_num(_addr_leaf_label(first_anchor) or "")
    replacement_leaf = _clean_num(_addr_leaf_label(replacement_target) or "")
    if (
        not anchor_section
        or anchor_section != replacement_section
        or not anchor_leaf_kind
        or anchor_leaf_kind != replacement_leaf_kind
        or not anchor_leaf
        or not replacement_leaf
        or not replacement_leaf.startswith(anchor_leaf)
    ):
        return []

    return anchor_refs[1:]


def _source_replaced_sibling_count_from_substitution_text(
    *,
    extracted_text: Optional[str],
    target_refs: Sequence[str],
) -> Optional[int]:
    """Return how many source-named siblings are being replaced, if explicit."""
    if not extracted_text or len(target_refs) < 2:
        return None
    match = re.search(
        r"\bfor\s+(?:sub-?paragraphs?|paragraphs?|subsections?)\s+([^.;]+?)\s+substitute\b",
        extracted_text,
        flags=re.I,
    )
    if match is None:
        return None
    labels = re.findall(r"\(([0-9A-Za-z]+)\)", match.group(1))
    if len(labels) < 2 or len(labels) >= len(target_refs):
        return None
    target_labels: list[str] = []
    for target_ref in target_refs[: len(labels)]:
        try:
            target_labels.append(_clean_num(_addr_leaf_label(_parse_affected_target(target_ref)) or ""))
        except Exception:
            return None
    if [_clean_num(label) for label in labels] != target_labels:
        return None
    return len(labels)


def _expand_sibling_targets_from_text(
    prov_str: str,
    extracted_text: Optional[str],
) -> Optional[list[str]]:
    """Expand compressed sibling refs from plain-text omission/repeal wording."""
    if not extracted_text:
        return None

    sibling_text = None
    sibling_kind = None
    for pattern in (
        r"\bfor\s+((?:sub-)?paragraphs?|subsections?)\s+([^.;]+?)\s+substitute\b",
        r"\bomit\s+(subsections?|(?:sub-)?paragraphs?)\s+([^.;]+)",
        r"\b(subsections?|(?:sub-)?paragraphs?)\s+([^.;]+?)\s+(?:is|are)\s+repealed\b",
        r"\bin\s+((?:sub-)?paragraphs?|subsections?)\s+([^.;]+?)\s+(?:after|before|insert|substitute)\b",
    ):
        m = re.search(pattern, extracted_text, flags=re.I)
        if m:
            sibling_kind = m.group(1).lower()
            sibling_text = m.group(2)
            break
    if sibling_text is None or sibling_kind is None:
        return None

    sibling_parts = [part.strip() for part in re.split(r"\s*(?:,|and)\s*", sibling_text, flags=re.I) if part.strip()]
    if len(sibling_parts) < 2:
        return None
    if any(re.search(r"\b(?:beginning|end)\b", part, flags=re.I) for part in sibling_parts):
        return None
    sibling_kind_base = re.sub(r"[^a-z]+", "", sibling_kind.lower())
    if sibling_kind_base.endswith("s"):
        sibling_kind_base = sibling_kind_base[:-1]
    for part in sibling_parts[1:]:
        kind_match = re.match(r"^(sub-?paragraph|paragraph|subsection|item|point)\b", part, flags=re.I)
        if kind_match is None:
            continue
        part_kind_base = re.sub(r"[^a-z]+", "", kind_match.group(1).lower())
        if part_kind_base.endswith("s"):
            part_kind_base = part_kind_base[:-1]
        if part_kind_base != sibling_kind_base:
            return None
    if sibling_kind.startswith("subsection") and any(
        re.match(r"^(?:sub-?paragraph|paragraph|item|point)\b", part, flags=re.I) for part in sibling_parts
    ):
        return None

    flat_sibling_raw: list[str] = []
    for part in sibling_parts:
        part_groups = re.findall(r"\(([0-9A-Z]+)\)", part, re.I)
        if not part_groups:
            return None
        flat_sibling_raw.extend(part_groups)

    paren_groups = re.findall(r"\(([0-9A-Z]+)\)", prov_str, re.I)
    if len(paren_groups) < len(flat_sibling_raw):
        return None

    prov_is_schedule = bool(re.match(r"^\s*sch(?:edule)?\.?", prov_str, re.I))
    if sibling_kind.startswith("subsection") and prov_is_schedule:
        return None
    if ("paragraph" in sibling_kind) and not prov_is_schedule and not re.match(r"^\s*ss?\.\s*", prov_str, re.I):
        return None

    trailing_raw = paren_groups[-len(flat_sibling_raw) :]
    if [_clean_num(g) for g in trailing_raw] != [_clean_num(g) for g in flat_sibling_raw]:
        return None

    base = prov_str.rstrip()
    for _ in range(len(flat_sibling_raw)):
        base = re.sub(r"\([0-9A-Z]+\)\s*$", "", base, flags=re.I).rstrip()

    return [f"{base}{part}" for part in sibling_parts]
