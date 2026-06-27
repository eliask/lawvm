"""Temporal/source rewrite helpers for Finland replay compilation.

These helpers adjust source effective/expiry metadata after Finland
commencement and validity clauses are parsed. They mutate the caller-provided
operation/report lists, but do not apply legal tree mutations themselves.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Set as AbstractSet
from dataclasses import replace as dc_replace
from typing import Dict, List, Literal, Optional, Set

from lawvm.core import tree_ops as _tops
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.core.statute_validity import expires_on_from_valid_until
from lawvm.finland.apply_runtime_support import _timeline_target_exists
from lawvm.finland.metadata import _expiry_date_precedes_effective_date


def _rewrite_lo_op_source_expiry(
    lo_ops_out: Optional[List[_LegalOperation]],
    target_source_statute: str,
    section_labels: Optional[AbstractSet[str]],
    expiry_date: dt.date,
    parent_statute_id: Optional[str] = None,
    replay_mode: str = "legal_pit",
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
    fallback_effective: Optional[dt.date] = None,
    *,
    expiry_convention: Literal["inclusive_prose", "exclusive_cutoff"],
    touched_addresses_out: Optional[List[LegalAddress]] = None,
) -> bool:
    """Update expires on lo_ops whose source matches ``target_source_statute``.

    When ``target_source_statute`` is the same as ``parent_statute_id`` (i.e. the
    override targets the master statute directly, not a specific amendment), the
    function falls back to extending ALL ops that already carry a finite expiry
    date earlier than the new one.  This handles the common Finnish pattern where
    an amendment amends only the *voimaantulosäännös* of the parent statute to
    extend all temporary sections' validity.

    In ``official_consolidation`` mode the parent-statute fallback clears the expires field
    entirely (rather than updating it to the new date) so that oracle materialization
    stays anchored at the consolidation cutoff instead of reviving future text.

    ``expiry_convention`` declares what ``expiry_date`` means so each caller
    must audit its source. ``"inclusive_prose"``: the prose-inclusive last
    in-force day ("on voimassa N päivään ...") — converted here to the kernel's
    exclusive cutoff before stamping. ``"exclusive_cutoff"``: already the first
    day NOT in force (e.g. a kumotaan repeal's effective date) — stamped as is.
    The born-expired guard always runs in the caller-supplied domain.
    """
    if lo_ops_out is None:
        return False
    expiry_iso = (
        expires_on_from_valid_until(expiry_date).isoformat()
        if expiry_convention == "inclusive_prose"
        else expiry_date.isoformat()
    )
    updated = False
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        if _expiry_date_precedes_effective_date(expiry_date, src.effective):
            continue
        target_path = list(lo.target.path)
        sec_label = next((v for k, v in reversed(target_path) if k == "section"), "")
        if section_labels is not None and sec_label.lower() not in section_labels:
            continue
        # Chapter-scoped guard: when chapter_section_map is provided, only expire
        # ops whose (chapter, section) pair is covered by the map.  This prevents
        # cross-chapter contamination when the same section number is fully repealed
        # in one chapter but only partially repealed in another.
        if chapter_section_map is not None:
            chap_label = next((v for k, v in reversed(target_path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label.lower() not in (global_secs | chap_secs):
                continue
        lo_ops_out[i] = dc_replace(lo, source=dc_replace(src, expires=expiry_iso))
        if touched_addresses_out is not None:
            touched_addresses_out.append(lo.target)
        updated = True
    if updated and not (
        parent_statute_id is not None and target_source_statute == parent_statute_id
    ):
        return True
    # Fallback: when the override targets the parent statute (voimaantulosäännös
    # amending the whole regulation), extend every op that has a finite expiry
    # earlier than the new one.  This covers the case where all lo_ops carry
    # amendment IDs (not the parent statute ID) as their source.
    if parent_statute_id is not None and target_source_statute == parent_statute_id:
        # In official_consolidation mode clear the expires field so sections with extended
        # validity appear at the 9999-12-31 materialization horizon.  In legal_pit
        # mode keep the real expiry date so point-in-time queries remain accurate.
        new_expires = "" if replay_mode == "official_consolidation" else expiry_iso
        for i, lo in enumerate(lo_ops_out):
            src = lo.source
            if src is None or not src.expires:
                continue
            if fallback_effective is not None and src.effective != fallback_effective.isoformat():
                continue
            if _expiry_date_precedes_effective_date(expiry_date, src.effective):
                continue
            if replay_mode != "official_consolidation" and src.expires >= expiry_iso:
                continue
            if _later_same_target_op_before_expiry(
                lo_ops_out,
                current_index=i,
                target=lo.target,
                current_effective=src.effective,
                expiry_iso=expiry_iso,
            ):
                continue
            target_path = list(lo.target.path)
            sec_label = next((v for k, v in reversed(target_path) if k == "section"), "")
            if section_labels is not None and sec_label.lower() not in section_labels:
                continue
            lo_ops_out[i] = dc_replace(lo, source=dc_replace(src, expires=new_expires))
            if touched_addresses_out is not None:
                touched_addresses_out.append(lo.target)
            updated = True
    return updated


def _later_same_target_op_before_expiry(
    lo_ops: List[_LegalOperation],
    *,
    current_index: int,
    target: LegalAddress,
    current_effective: str,
    expiry_iso: str,
) -> bool:
    """Return True when a later op supersedes this target before the extension horizon."""
    for later in lo_ops[current_index + 1 :]:
        if later.target != target:
            continue
        later_src = later.source
        if later_src is None or not later_src.effective:
            continue
        if current_effective and later_src.effective <= current_effective:
            continue
        if later_src.effective < expiry_iso:
            return True
    return False


def _rewrite_temporal_event_expiry_for_addresses(
    temporal_events: List[TemporalEvent],
    target_statute: str,
    addresses: tuple[LegalAddress, ...],
    expiry_date: dt.date,
    *,
    replay_mode: str,
    expiry_convention: Literal["inclusive_prose", "exclusive_cutoff"],
) -> int:
    """Mirror source-expiry overrides onto executable expiry TemporalEvents."""
    if not temporal_events or not addresses:
        return 0
    address_set = set(addresses)
    expiry_iso = (
        expires_on_from_valid_until(expiry_date).isoformat()
        if expiry_convention == "inclusive_prose"
        else expiry_date.isoformat()
    )
    new_expires = "" if replay_mode == "official_consolidation" else expiry_iso
    rewritten = 0
    kept: list[TemporalEvent] = []
    for event in temporal_events:
        if (
            event.kind not in {"expire", "suspend"}
            or event.scope.target_statute != target_statute
            or not any(address in address_set for address in event.scope.exact_addresses)
        ):
            kept.append(event)
            continue
        if replay_mode != "official_consolidation" and event.expires >= expiry_iso:
            kept.append(event)
            continue
        if not new_expires:
            rewritten += 1
            continue
        kept.append(
            dc_replace(
                event,
                expires=new_expires,
                source=(
                    dc_replace(event.source, expires=new_expires)
                    if event.source is not None
                    else None
                ),
            )
        )
        rewritten += 1
    if rewritten:
        temporal_events[:] = kept
    return rewritten


def reconcile_temporal_event_expiry_with_op_sources(
    temporal_events: List[TemporalEvent],
    lo_ops: Optional[List[_LegalOperation]],
    *,
    target_statute: str,
) -> int:
    """Align executable expiry events with rewritten operation source expiry.

    Late commencement-clause overrides rewrite ``LegalOperation.source.expires``
    after earlier amendment events have already been accumulated at replay
    scope. For replay/direct carriers minted from those operations, the
    operation source is the typed authority for the exact group and target
    address; stale matching expiry events must be updated or removed. Do not
    apply this to relation-backed lifecycle events: those may intentionally add
    an expiry that is not present on the operation source.
    """
    if not temporal_events or not lo_ops:
        return 0
    source_expiry_by_group_address: dict[tuple[str, LegalAddress], str] = {}
    for lo in lo_ops:
        if not lo.group_id or lo.source is None:
            continue
        source_expiry_by_group_address[(lo.group_id, lo.target)] = lo.source.expires or ""

    changed = 0
    kept: list[TemporalEvent] = []
    for event in temporal_events:
        if (
            event.kind not in {"expire", "suspend"}
            or not event.event_id.startswith(("fi-temporary:", "fi-temporal:"))
            or event.scope.target_statute != target_statute
            or not event.group_id
            or not event.scope.exact_addresses
        ):
            kept.append(event)
            continue
        replacements = {
            source_expiry_by_group_address[(event.group_id, address)]
            for address in event.scope.exact_addresses
            if (event.group_id, address) in source_expiry_by_group_address
        }
        if len(replacements) != 1:
            kept.append(event)
            continue
        replacement = next(iter(replacements))
        if replacement == event.expires:
            kept.append(event)
            continue
        changed += 1
        if not replacement:
            continue
        kept.append(
            dc_replace(
                event,
                expires=replacement,
                source=(
                    dc_replace(event.source, expires=replacement)
                    if event.source is not None
                    else None
                ),
            )
        )
    if changed:
        temporal_events[:] = kept
    return changed


def _rewrite_lo_op_source_effective(
    lo_ops_out: Optional[List[_LegalOperation]],
    target_source_statute: str,
    effective_date: dt.date,
    *,
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
    base_ir: Optional[IRNode] = None,
) -> bool:
    """Update effective on lo_ops whose source matches the scoped override."""
    if lo_ops_out is None:
        return False
    effective_iso = effective_date.isoformat()
    updated = False
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        if chapter_section_map is not None:
            target_path = list(lo.target.path)
            sec_label = next((v for k, v in reversed(target_path) if k == "section"), "")
            chap_label = next((v for k, v in reversed(target_path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label.lower() not in (global_secs | chap_secs):
                continue
        updated_lo = dc_replace(lo, source=dc_replace(src, effective=effective_iso))
        if (
            updated_lo.action is StructuralAction.REPLACE
            and updated_lo.target.path
            and (base_ir is None or _tops.resolve(base_ir, updated_lo.target.path) is None)
            and _timeline_target_exists(
                updated_lo.target.path,
                replay_history_ops=lo_ops_out[:i],
                base_ir=base_ir,
                before_effective=effective_iso,
            )
        ):
            updated_lo = dc_replace(updated_lo, action=StructuralAction.INSERT)
        lo_ops_out[i] = updated_lo
        updated = True
    return updated


def _rewrite_lo_op_group_id(
    lo_ops_out: Optional[List[_LegalOperation]],
    target_source_statute: str,
    new_group_id: str,
    *,
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
) -> tuple[LegalAddress, ...]:
    """Retarget matching lo_ops to a scoped temporal group and return addresses."""
    if lo_ops_out is None:
        return ()
    touched: list[LegalAddress] = []
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        if chapter_section_map is not None:
            target_path = list(lo.target.path)
            sec_label = next((v for k, v in reversed(target_path) if k == "section"), "")
            chap_label = next((v for k, v in reversed(target_path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label.lower() not in (global_secs | chap_secs):
                continue
        lo_ops_out[i] = dc_replace(lo, group_id=new_group_id)
        if lo.target not in touched:
            touched.append(lo.target)
    return tuple(touched)


def _rewrite_lo_op_source_effective_for_chapters(
    lo_ops_out: Optional[List[_LegalOperation]],
    target_source_statute: str,
    effective_date: dt.date,
    *,
    chapter_labels: frozenset[str],
    new_group_id: str,
    base_ir: Optional[IRNode] = None,
) -> tuple[LegalAddress, ...]:
    """Update effective dates for ops targeting the named chapters."""
    if lo_ops_out is None or not chapter_labels:
        return ()
    effective_iso = effective_date.isoformat()
    touched: list[LegalAddress] = []
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        if not _address_targets_any_chapter(lo.target, chapter_labels):
            continue
        updated_lo = dc_replace(
            lo,
            source=dc_replace(src, effective=effective_iso),
            group_id=new_group_id,
        )
        if (
            updated_lo.action is StructuralAction.REPLACE
            and updated_lo.target.path
            and (base_ir is None or _tops.resolve(base_ir, updated_lo.target.path) is None)
            and _timeline_target_exists(
                updated_lo.target.path,
                replay_history_ops=lo_ops_out[:i],
                base_ir=base_ir,
                before_effective=effective_iso,
            )
        ):
            updated_lo = dc_replace(updated_lo, action=StructuralAction.INSERT)
        lo_ops_out[i] = updated_lo
        if lo.target not in touched:
            touched.append(lo.target)
    return tuple(touched)


def _address_targets_any_chapter(address: LegalAddress, chapter_labels: frozenset[str]) -> bool:
    return any(
        kind == "chapter" and value.lower() in chapter_labels
        for kind, value in address.path
    )


def _rewrite_lo_op_source_effective_for_address_suffixes(
    lo_ops_out: Optional[List[_LegalOperation]],
    target_source_statute: str,
    effective_date: dt.date,
    *,
    address_suffixes: tuple[LegalAddress, ...],
    new_group_id: str,
    include_payload_carriers: bool = False,
) -> tuple[LegalAddress, ...]:
    """Update exact child-address effective dates from scoped commencement text."""
    if lo_ops_out is None or not address_suffixes:
        return ()
    effective_iso = effective_date.isoformat()
    touched: list[LegalAddress] = []
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        if not any(
            _op_carries_commencement_suffix(
                lo,
                suffix,
                include_payload_carriers=include_payload_carriers,
            )
            for suffix in address_suffixes
        ):
            continue
        lo_ops_out[i] = dc_replace(
            lo,
            source=dc_replace(src, effective=effective_iso),
            group_id=new_group_id,
        )
        if lo.target not in touched:
            touched.append(lo.target)
    return tuple(touched)


def _op_carries_commencement_suffix(
    op: _LegalOperation,
    suffix: LegalAddress,
    *,
    include_payload_carriers: bool,
) -> bool:
    """Return whether an op would materialize the delayed commencement suffix."""
    if _address_matches_commencement_suffix(op.target, suffix):
        return True
    if not include_payload_carriers:
        return False
    if suffix.special is not None:
        return False
    target_path = tuple(op.target.path)
    suffix_path = tuple(suffix.path)
    if not target_path or len(target_path) >= len(suffix_path):
        return False
    if suffix_path[: len(target_path)] != target_path:
        return False
    if op.payload is None:
        return False
    return _tops.resolve(op.payload, suffix_path[len(target_path):]) is not None


def _rewrite_compiled_op_activation_rule_effective_for_address_suffixes(
    compiled_ops_out: Optional[List[Dict[str, object]]],
    target_source_statute: str,
    effective_date: dt.date,
    *,
    address_suffixes: tuple[LegalAddress, ...],
) -> bool:
    """Update compiled activation rules for subsection-scoped commencement."""
    if compiled_ops_out is None or not address_suffixes:
        return False
    effective_iso = effective_date.isoformat()
    updated = False
    for op in compiled_ops_out:
        if op.get("source_statute") != target_source_statute:
            continue
        target_address = _compiled_op_address_suffix(op)
        if target_address is None:
            continue
        if not any(_address_matches_commencement_suffix(target_address, suffix) for suffix in address_suffixes):
            continue
        op["activation_rule"] = {
            "kind": "fixed_date",
            "effective_date": effective_iso,
            "condition_ref": "",
        }
        op["is_contingent"] = False
        updated = True
    return updated


def _compiled_op_address_suffix(op: Dict[str, object]) -> Optional[LegalAddress]:
    target_norm = str(op.get("target_norm") or "").strip()
    if not target_norm:
        return None
    path: list[tuple[str, str]] = [("section", target_norm)]
    target_paragraph = str(op.get("target_paragraph") or "").strip()
    if target_paragraph:
        path.append(("subsection", target_paragraph))
    target_item = str(op.get("target_item") or "").strip()
    if target_item:
        path.append(("paragraph", target_item))
    return LegalAddress(path=tuple(path))


def _address_has_suffix(address: LegalAddress, suffix: LegalAddress) -> bool:
    if len(suffix.path) > len(address.path):
        return False
    return address.path[-len(suffix.path):] == suffix.path


def _address_matches_commencement_suffix(address: LegalAddress, suffix: LegalAddress) -> bool:
    if suffix.special is not None and address.special != suffix.special:
        return False
    if _address_has_suffix(address, suffix):
        return True
    sparse = _sparse_item_suffix(suffix)
    if sparse is None:
        return False
    section, item, subitem = sparse
    address_section = next((value for kind, value in address.path if kind == "section"), "")
    if address_section != section:
        return False
    address_item = next(
        (value for kind, value in address.path if kind in {"item", "paragraph"}),
        "",
    )
    if address_item != item:
        return False
    if subitem is None:
        return True
    address_subitem = next((value for kind, value in address.path if kind == "subitem"), "")
    return address_subitem == subitem


def _sparse_item_suffix(suffix: LegalAddress) -> tuple[str, str, str | None] | None:
    section = ""
    subsection_seen = False
    item = ""
    subitem: str | None = None
    for kind, value in suffix.path:
        if kind == "section":
            section = value
        elif kind == "subsection":
            subsection_seen = True
        elif kind in {"item", "paragraph"}:
            item = value
        elif kind == "subitem":
            subitem = value
    if not section or not item or subsection_seen:
        return None
    return section, item, subitem


def _rewrite_compiled_op_activation_rule_effective(
    compiled_ops_out: Optional[List[Dict[str, object]]],
    target_source_statute: str,
    effective_date: dt.date,
    *,
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
) -> bool:
    """Update compiled activation rules for scoped commencement overrides."""
    if compiled_ops_out is None:
        return False
    effective_iso = effective_date.isoformat()
    updated = False
    for op in compiled_ops_out:
        if op.get("source_statute") != target_source_statute:
            continue
        if chapter_section_map is not None:
            sec_label = str(op.get("target_norm") or "").lower()
            chap_label_raw = str(op.get("target_chapter") or "").strip().lower() or None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_raw, set()) if chap_label_raw else set()
            if sec_label not in (global_secs | chap_secs):
                continue
        op["activation_rule"] = {
            "kind": "fixed_date",
            "effective_date": effective_iso,
            "condition_ref": "",
        }
        op["is_contingent"] = False
        updated = True
    return updated


def _rewrite_compiled_op_activation_rule_effective_for_chapters(
    compiled_ops_out: Optional[List[Dict[str, object]]],
    target_source_statute: str,
    effective_date: dt.date,
    *,
    chapter_labels: frozenset[str],
) -> bool:
    """Update compiled activation rules for chapter-scoped commencement."""
    if compiled_ops_out is None or not chapter_labels:
        return False
    effective_iso = effective_date.isoformat()
    updated = False
    for op in compiled_ops_out:
        if op.get("source_statute") != target_source_statute:
            continue
        if _compiled_op_chapter_label(op) not in chapter_labels:
            continue
        op["activation_rule"] = {
            "kind": "fixed_date",
            "effective_date": effective_iso,
            "condition_ref": "",
        }
        op["is_contingent"] = False
        updated = True
    return updated


def _compiled_op_chapter_label(op: Dict[str, object]) -> str:
    target_chapter = str(op.get("target_chapter") or "").strip().lower()
    if target_chapter:
        return target_chapter
    if str(op.get("target_unit_kind") or "").strip().lower() == "chapter":
        return str(op.get("target_norm") or "").strip().lower()
    return ""


def _rewrite_later_effective_lo_groups(
    lo_ops_out: Optional[List[_LegalOperation]],
    *,
    target_source_statute: str,
    amendment_effective_date: dt.date,
) -> dict[str, tuple[LegalAddress, ...]]:
    """Scope later-effective ops away from the amendment-wide temporal group.

    Finland cited-version-bound ops can legitimately carry a later executable
    effective date than the amendment's own commencement date. If those ops
    keep the canonical ``finland-johto:<amendment>`` group id, core temporal
    matching will still activate them at the amendment-wide date. Rewrite only
    the later-effective ops into per-date scoped groups so replay emits an
    explicit, auditable temporal carrier for the deferred subset.
    """
    if lo_ops_out is None:
        return {}

    amendment_effective_iso = amendment_effective_date.isoformat()
    canonical_group_id = f"finland-johto:{target_source_statute}"
    touched_by_effective: dict[str, list[LegalAddress]] = {}

    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if (
            src is None
            or src.statute_id != target_source_statute
            or not src.effective
            or src.effective <= amendment_effective_iso
            or lo.group_id != canonical_group_id
        ):
            continue
        scoped_group_id = f"{canonical_group_id}:effective:{src.effective}"
        lo_ops_out[i] = dc_replace(lo, group_id=scoped_group_id)
        touched_by_effective.setdefault(src.effective, [])
        if lo.target not in touched_by_effective[src.effective]:
            touched_by_effective[src.effective].append(lo.target)

    return {
        effective_iso: tuple(addresses)
        for effective_iso, addresses in touched_by_effective.items()
        if addresses
    }


def _rewrite_compiled_op_activation_rule_effective_for_addresses(
    compiled_ops_out: Optional[List[Dict[str, object]]],
    *,
    target_source_statute: str,
    effective_date: dt.date,
    exact_addresses: tuple[LegalAddress, ...],
) -> bool:
    """Update compiled activation rules for one exact-address effective override."""
    if compiled_ops_out is None or not exact_addresses:
        return False
    effective_iso = effective_date.isoformat()
    address_keys = {tuple(address.path) for address in exact_addresses}
    updated = False
    for op in compiled_ops_out:
        if op.get("source_statute") != target_source_statute:
            continue
        target_path: list[tuple[str, str]] = []
        target_part = str(op.get("target_part") or "").strip()
        target_chapter = str(op.get("target_chapter") or "").strip()
        target_norm = str(op.get("target_norm") or "").strip()
        target_unit_kind = str(op.get("target_unit_kind") or "").strip()
        if target_part:
            target_path.append(("part", target_part))
        if target_chapter:
            target_path.append(("chapter", target_chapter))
        if target_unit_kind and target_norm:
            target_path.append((target_unit_kind, target_norm))
        if tuple(target_path) not in address_keys:
            continue
        op["activation_rule"] = {
            "kind": "fixed_date",
            "effective_date": effective_iso,
            "condition_ref": "",
        }
        op["is_contingent"] = False
        updated = True
    return updated


def _event_scope_section_and_chapter(
    event: TemporalEvent,
) -> tuple[str, Optional[str]]:
    """Extract section/chapter labels from a TemporalEvent scope when present."""
    addresses = tuple(event.scope.exact_addresses or ()) or tuple(event.scope.address_prefixes or ())
    if not addresses:
        return "", None
    address = addresses[0]
    path = list(address.path)
    section = next((value for kind, value in reversed(path) if kind == "section"), "")
    chapter = next((value for kind, value in reversed(path) if kind == "chapter"), None)
    return section.lower(), (chapter.lower() if chapter else None)


def _rewrite_temporal_event_expiry(
    temporal_events: list[TemporalEvent],
    *,
    target_source_statute: str,
    section_labels: Optional[Set[str]],
    expiry_date: dt.date,
    parent_statute_id: Optional[str] = None,
    replay_mode: str = "legal_pit",
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
    expiry_convention: Literal["inclusive_prose", "exclusive_cutoff"],
) -> bool:
    """Rewrite emitted TemporalEvents when Finland expiry overrides retarget time.

    ``expiry_convention`` has the same meaning as on
    ``_rewrite_lo_op_source_expiry``: prose-inclusive dates are converted to the
    kernel's exclusive ``expires`` cutoff before stamping.
    """
    expiry_iso = (
        expires_on_from_valid_until(expiry_date).isoformat()
        if expiry_convention == "inclusive_prose"
        else expiry_date.isoformat()
    )
    updated = False

    def _scope_matches(event: TemporalEvent) -> bool:
        section_label, chapter_label = _event_scope_section_and_chapter(event)
        if section_labels is not None and section_label not in section_labels:
            return False
        if chapter_section_map is None:
            return True
        global_secs = chapter_section_map.get(None, set())
        chapter_secs = chapter_section_map.get(chapter_label, set()) if chapter_label else set()
        return section_label in (global_secs | chapter_secs)

    for i, event in enumerate(temporal_events):
        source = event.source
        if source is None or source.statute_id != target_source_statute:
            continue
        if not _scope_matches(event):
            continue
        temporal_events[i] = dc_replace(
            event,
            expires=expiry_iso,
            source=dc_replace(source, expires=expiry_iso),
        )
        updated = True
    if updated:
        return True

    if parent_statute_id is None or target_source_statute != parent_statute_id:
        return False

    new_expires = "" if replay_mode == "official_consolidation" else expiry_iso
    for i, event in enumerate(temporal_events):
        source = event.source
        if source is None:
            continue
        event_expires = event.expires or source.expires
        if not event_expires:
            continue
        if replay_mode != "official_consolidation" and event_expires >= expiry_iso:
            continue
        if not _scope_matches(event):
            continue
        temporal_events[i] = dc_replace(
            event,
            expires=new_expires,
            source=dc_replace(source, expires=new_expires),
        )
        updated = True
    return updated


def _clear_temporal_event_expiry(
    temporal_events: list[TemporalEvent],
    *,
    target_source_statute: str,
    section_labels: Set[str],
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
) -> bool:
    """Clear expiry payload on matched TemporalEvents after permanent repeal rewrites."""
    updated = False
    for i, event in enumerate(temporal_events):
        source = event.source
        if source is None or source.statute_id != target_source_statute:
            continue
        section_label, chapter_label = _event_scope_section_and_chapter(event)
        if section_label not in section_labels:
            continue
        if chapter_section_map is not None:
            global_secs = chapter_section_map.get(None, set())
            chapter_secs = chapter_section_map.get(chapter_label, set()) if chapter_label else set()
            if section_label not in (global_secs | chapter_secs):
                continue
        if not event.expires and not source.expires:
            continue
        temporal_events[i] = dc_replace(
            event,
            expires="",
            source=dc_replace(source, expires=""),
        )
        updated = True
    return updated


def _normalize_frontend_temporal_events(
    temporal_events: tuple[TemporalEvent, ...],
    *,
    amendment_id: str,
    target_statute: str,
) -> tuple[TemporalEvent, ...]:
    """Normalize frontend-emitted temporal carriers onto Finland replay batch ids."""
    if not temporal_events:
        return ()
    normalized_events: list[TemporalEvent] = []
    canonical_group_id = f"finland-johto:{amendment_id}"
    for event in temporal_events:
        normalized_scope = event.scope
        if normalized_scope.target_statute != target_statute:
            normalized_scope = TemporalScope(
                target_statute=target_statute,
                exact_addresses=normalized_scope.exact_addresses,
                address_prefixes=normalized_scope.address_prefixes,
                predicates=normalized_scope.predicates,
                include_future_descendants=normalized_scope.include_future_descendants,
            )
        normalized_group_id = (
            canonical_group_id
            if event.group_id in {None, "", amendment_id}
            else event.group_id
        )
        if normalized_scope is event.scope and normalized_group_id == event.group_id:
            normalized_events.append(event)
            continue
        normalized_events.append(
            dc_replace(
                event,
                scope=normalized_scope,
                group_id=normalized_group_id,
            )
        )
    return tuple(normalized_events)


def _base_chapter_expiry_temporal_events(
    *,
    target_statute: str,
    chapter_expiries: Optional[Dict[str, str]],
) -> tuple[TemporalEvent, ...]:
    """Project base-statute chapter expiry facts into explicit TemporalEvents."""
    if not chapter_expiries:
        return ()
    events: list[TemporalEvent] = []
    for chapter_label, expiry_iso in sorted(chapter_expiries.items()):
        chapter_address = LegalAddress(path=(("chapter", chapter_label),))
        expires = expires_on_from_valid_until(dt.date.fromisoformat(expiry_iso)).isoformat()
        events.append(
            TemporalEvent(
                event_id=f"fi-base-chapter-expiry:{target_statute}:chapter:{chapter_label}",
                kind="expire",
                scope=TemporalScope(
                    target_statute=target_statute,
                    address_prefixes=(chapter_address,),
                ),
                expires=expires,
                source=OperationSource(
                    statute_id=target_statute,
                    expires=expires,
                ),
            )
        )
    return tuple(events)
