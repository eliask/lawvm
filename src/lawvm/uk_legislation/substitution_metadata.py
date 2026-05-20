"""UK effect-feed substitution metadata helpers.

These helpers interpret "substituted for ..." metadata as target evidence.
They do not inspect replay state or mutate payloads; callers still own lowering,
operation emission, and adjudication records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

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
