"""Timeline overlay invariant validators (Phase 7).

Validates temporal-layer invariants on compiled timelines. These checks
catch the "replay drift" class of bugs where the materialized PIT tree
diverges from what the timeline data predicts.

Invariants checked:
  1. No overlapping permanent versions at the same address
  2. Temporary overlay consistency (expires present, non-overlapping)
  3. Expiry chain monotonicity for extended temporary versions
  4. Replay-timeline consistency (IR tree matches timeline predictions)
  5. Aggregate check running all four above

Usage:
    from lawvm.core.timeline_invariants import check_all_timeline_invariants
    violations = check_all_timeline_invariants(ir_node, timelines, pit_date)

API tier
--------
Internal validator surface for kernel timeline correctness. Important for
tests/diagnostics, but not a primary public product API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Dict, List, Literal, Mapping, TypedDict

import re

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    OperationSource,
    ProvisionVersion,
)
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.provenance import ExpiryOverride
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import (
    Timelines,
    VersionSelectionResult,
    select_active_version_ex,
)


# ---------------------------------------------------------------------------
# 1. No overlapping permanent versions
# ---------------------------------------------------------------------------


def check_no_overlapping_permanent_versions(timelines: Timelines) -> List[str]:
    """Check that no two permanent versions are active at the same date.

    For each address, iterates permanent versions sorted by effective date
    and verifies that each version's effective range does not overlap with
    the next. A permanent version's active range is [effective, next_effective)
    where next_effective is the effective date of the next permanent version
    (or infinity if it's the last one). Two permanent versions overlap if
    they share the same effective date (since only one can be authoritative).
    """
    violations: List[str] = []

    for address, tl in timelines.items():
        permanent = [v for v in tl.versions if v.variant_kind == "permanent"]
        # Sort by effective date (they should already be sorted, but be safe)
        permanent.sort(key=lambda v: (v.effective, v.enacted))

        # Check for same-effective-date duplicates among permanent versions
        i = 0
        while i < len(permanent):
            j = i + 1
            while j < len(permanent) and permanent[j].effective == permanent[i].effective:
                j += 1
            count = j - i
            if count > 1:
                # Multiple permanent versions at the same effective date.
                # This is a potential ambiguity but is handled by tie-breaking
                # (enacted date, substantive-vs-placeholder). Only flag if
                # they also share the same enacted date, making tie-breaking
                # rely solely on position — which is fragile.
                enacted_dates = {v.enacted for v in permanent[i:j]}
                if len(enacted_dates) < count:
                    violations.append(
                        f"{address}: {count} permanent versions with same "
                        f"effective={permanent[i].effective!r} and overlapping "
                        f"enacted dates (ambiguous precedence)"
                    )
            i = j

    return violations


# ---------------------------------------------------------------------------
# 2. Temporary overlay consistency
# ---------------------------------------------------------------------------


def check_temporary_overlay_consistency(timelines: Timelines) -> List[str]:
    """Check temporary version consistency at each address.

    For each address with temporary versions:
      - Each temporary version should have a non-empty expires date
      - If expires < effective, flag as violation (would never be active)
      - If two temporary versions overlap in time, flag as ambiguity
    """
    violations: List[str] = []

    for address, tl in timelines.items():
        temporaries = [v for v in tl.versions if v.variant_kind == "temporary"]
        if not temporaries:
            continue

        # Check each temporary has expires and expires >= effective
        for v in temporaries:
            if not v.expires:
                source_info = ""
                if v.source:
                    source_info = f" (source={v.source.statute_id})"
                violations.append(
                    f"{address}: temporary version effective={v.effective!r} has no expires date{source_info}"
                )
                continue

            if v.expires < v.effective:
                source_info = ""
                if v.source:
                    source_info = f" (source={v.source.statute_id})"
                violations.append(
                    f"{address}: temporary version has expires={v.expires!r} < effective={v.effective!r}{source_info}"
                )

        # Check for overlapping temporaries
        # Sort by effective date
        sorted_temps = sorted(temporaries, key=lambda v: (v.effective, v.enacted))
        for a, b in pairwise(sorted_temps):
            # a is active in [a.effective, a.expires)
            # b is active in [b.effective, b.expires)
            # They overlap if a.expires > b.effective (and both have expires)
            if a.expires and b.effective < a.expires:
                violations.append(
                    f"{address}: overlapping temporary versions — "
                    f"v1=[{a.effective}, {a.expires}) vs "
                    f"v2=[{b.effective}, {b.expires or '...'})"
                )

    return violations


# ---------------------------------------------------------------------------
# 3. Expiry chain preserved
# ---------------------------------------------------------------------------


def check_expiry_chain_preserved(timelines: Timelines) -> List[str]:
    """Check that expiry extension chains are monotonically increasing.

    For addresses with temporary versions whose OperationSource has a
    non-empty expiry_chain:
      - Each successive extension should have a later new_expires than
        the previous one

    """
    violations: List[str] = []

    for address, tl in timelines.items():
        for v in tl.versions:
            if v.variant_kind != "temporary":
                continue
            if v.source is None:
                continue
            if not v.source.expiry_chain:
                continue

            for i, override, previous in _expiry_chain_violations(source=v.source):
                if previous == "empty":
                    violations.append(
                        f"{address}: expiry_chain[{i}] has empty new_expires "
                        f"(source={override.source_statute_id})"
                    )
                    continue
                violations.append(
                    f"{address}: expiry_chain[{i}] new_expires="
                    f"{override.new_expires!r} <= previous "
                    f"{previous!r} (not monotonically increasing)"
                )

    return violations


def _expiry_chain_violations(
    *,
    source: OperationSource,
) -> list[tuple[int, ExpiryOverride, str]]:
    violations: list[tuple[int, ExpiryOverride, str]] = []
    prev_expires = source.expires_original or ""
    for index, override in enumerate(source.expiry_chain):
        new_expires = override.new_expires or ""
        if not new_expires:
            violations.append((index, override, "empty"))
            continue
        if prev_expires and new_expires <= prev_expires:
            violations.append((index, override, prev_expires))
        prev_expires = new_expires
    return violations


# ---------------------------------------------------------------------------
# 4. Replay-timeline consistency
# ---------------------------------------------------------------------------


def _collect_addressed_nodes(
    node: IRNode,
    current_path: tuple[tuple[str, str], ...] = (),
) -> Dict[LegalAddress, IRNode]:
    """Collect all addressable nodes from an IR tree with their addresses."""
    result: Dict[LegalAddress, IRNode] = {}

    if node.kind == IRNodeKind.BODY:
        for child in node.children:
            result.update(_collect_addressed_nodes(child, current_path))
        return result

    if node.label is not None:
        addr_path = current_path + ((_kind_str(node.kind), node.label),)
        address = LegalAddress(path=addr_path)
        result[address] = node
        for child in node.children:
            result.update(_collect_addressed_nodes(child, addr_path))
    else:
        # Unlabelled node — not addressable; recurse under same path
        for child in node.children:
            result.update(_collect_addressed_nodes(child, current_path))

    return result


def _collect_statute_addressed_nodes(statute: IRStatute) -> Dict[LegalAddress, IRNode]:
    """Collect all addressed nodes from both statute body and supplements."""
    result = _collect_addressed_nodes(statute.body)
    for supplement in statute.supplements:
        result.update(_collect_addressed_nodes(supplement))
    return result


def check_replay_timeline_consistency(
    ir_node: IRNode | IRStatute,
    timelines: Timelines,
    pit_date: str,
) -> List[str]:
    """Compare materialized PIT IR tree with timeline predictions.

    This is the most important validator — catches "replay drift" bugs where
    the materialized tree diverges from what select_active_version predicts.

    Checks:
      - Every section-level node in the IR has a corresponding active
        timeline version
      - Every timeline with an active version at pit_date has content
        in the IR (or is a known tombstone)
      - Content text matches between timeline version and IR node
    """
    violations: List[str] = []

    # Collect all addressed nodes from the IR tree
    ir_nodes = (
        _collect_statute_addressed_nodes(ir_node)
        if isinstance(ir_node, IRStatute)
        else _collect_addressed_nodes(ir_node)
    )

    # Collect all active timeline versions at pit_date
    active_versions, selection_notes = _active_versions_with_selection_notes(timelines, pit_date)

    # Check 1: IR nodes without corresponding active timeline version
    for address in ir_nodes:
        if address not in active_versions:
            # Not necessarily a bug — the IR may contain base content that
            # predates the timeline system (e.g., unlabeled structural nodes).
            # Only flag section-level addresses (depth 1-2) which should
            # always have timeline entries.
            if len(address.path) <= 2:
                note = selection_notes.get(address)
                note_text = f" ({_selection_note_from_detail(note)})" if note else ""
                violations.append(
                    f"IR_WITHOUT_TIMELINE: {address} present in IR "
                    f"but has no active timeline version at {pit_date}{note_text}"
                )

    # Check 2: Active timeline versions without corresponding IR nodes
    for address, version in active_versions.items():
        if version is None:
            continue
        is_tombstone = version.content is None
        if is_tombstone:
            # Tombstone — IR should NOT have this address (it's repealed).
            # But some implementations materialize tombstones as placeholder
            # text (e.g., "§ X on kumottu"). Check for presence but don't
            # flag as error — the placeholder pattern is valid.
            continue

        if address not in ir_nodes:
            # Only flag section-level addresses (depth 1-2) where absence
            # is more likely a real issue. Deeper addresses may be composed
            # into their parent during materialization.
            if len(address.path) <= 2:
                violations.append(
                    f"TIMELINE_WITHOUT_IR: {address} has active timeline version at {pit_date} but is missing from IR"
                )

    # Check 3: Content text mismatch between timeline and IR
    for address, version in active_versions.items():
        if version is None:
            continue
        if version.content is None:
            continue  # tombstone
        if address not in ir_nodes:
            continue  # already flagged above

        timeline_text = irnode_to_text(version.content).strip()
        ir_text = irnode_to_text(ir_nodes[address]).strip()

        # Normalize whitespace for comparison
        timeline_norm = " ".join(timeline_text.split())
        ir_norm = " ".join(ir_text.split())

        if timeline_norm and ir_norm and timeline_norm != ir_norm:
            # Only flag section-node mismatches. Deeper descendants may be
            # intentionally composed into their parent during materialization.
            if _is_section_address(address):
                tl_preview = timeline_norm[:80]
                ir_preview = ir_norm[:80]
                violations.append(f"CONTENT_MISMATCH: {address} timeline={tl_preview!r}... vs ir={ir_preview!r}...")

    return violations


# ---------------------------------------------------------------------------
# 5. Aggregate check
# ---------------------------------------------------------------------------


def check_all_timeline_invariants(
    ir_node: IRNode | IRStatute,
    timelines: Timelines,
    pit_date: str,
) -> List[str]:
    """Run all timeline invariant checks and return combined violations.

    Args:
        ir_node:    The materialized PIT IR body node.
        timelines:  Compiled timelines dict (from compile_timelines or ingest_*).
        pit_date:   The point-in-time date used for materialization.

    Returns:
        List of violation description strings. Empty = all invariants hold.
    """
    violations: List[str] = []
    violations.extend(check_no_overlapping_permanent_versions(timelines))
    violations.extend(check_temporary_overlay_consistency(timelines))
    violations.extend(check_expiry_chain_preserved(timelines))
    violations.extend(check_replay_timeline_consistency(ir_node, timelines, pit_date))
    return violations


# ---------------------------------------------------------------------------
# C3: Typed section-local invariant violations
# ---------------------------------------------------------------------------

InvariantKind = Literal[
    "overlapping_permanent",
    "temporary_missing_expiry",
    "temporary_bad_interval",
    "temporary_overlap",
    "expiry_chain_non_monotone",
    "ir_without_timeline",
    "timeline_without_ir",
    "content_mismatch",
]


class SelectionDetail(TypedDict):
    selection_status: str
    required_dimensions: tuple[str, ...]
    candidate_count: int


@dataclass(frozen=True)
class TimelineInvariantViolation:
    """Typed timeline invariant violation with section attribution (C3).

    Carries the section label affected and enough detail for evidence
    to promote to PROVED_REPLAY_BUG.
    """

    kind: InvariantKind
    section_label: str  # extracted from LegalAddress (e.g., "12")
    address_path: str  # full address string for diagnostics
    message: str
    detail: Mapping[str, Any] = field(default_factory=dict)


def _section_label_from_address_text(address_text: str) -> str:
    """Extract the first section label from a rendered LegalAddress string."""
    match = re.search(r"(?:^|/)section:([^/]+)", address_text)
    if match is not None:
        return match.group(1)
    return ""


def _typed_violation(
    *,
    kind: InvariantKind,
    message: str,
) -> TimelineInvariantViolation:
    """Build a typed violation from a rendered invariant message."""
    address_text, sep, _rest = message.partition(": ")
    if not sep:
        address_text = ""
    return TimelineInvariantViolation(
        kind=kind,
        section_label=_section_label_from_address_text(address_text),
        address_path=address_text,
        message=message,
    )


def _typed_violation_from_address(
    *,
    kind: InvariantKind,
    address: LegalAddress,
    message: str,
    detail: Mapping[str, Any] | None = None,
) -> TimelineInvariantViolation:
    """Build a typed violation from a concrete LegalAddress."""
    return TimelineInvariantViolation(
        kind=kind,
        section_label=_section_label_from_address_text(str(address)),
        address_path=str(address),
        message=message,
        detail=dict(detail or {}),
    )


def _section_label_from_address(address: LegalAddress) -> str:
    """Extract section-level label from a LegalAddress.

    Returns the label of the first 'section'-kind path element,
    or the leaf label if no explicit section kind exists.
    """
    for kind, label in address.path:
        if kind == "section":
            return label
    # Fallback: use leaf label
    return address.path[-1][1] if address.path else ""


def _is_section_address(address: LegalAddress) -> bool:
    """Return True when the address points at a section node."""
    return bool(address.path) and address.path[-1][0] == "section"


def _selection_detail(selection: VersionSelectionResult) -> SelectionDetail:
    """Extract ambiguity-preserving metadata from a selection result."""
    certificate = selection.certificate
    return {
        "selection_status": selection.status,
        "required_dimensions": tuple(selection.required_dimensions),
        "candidate_count": certificate.candidate_count if certificate is not None else 0,
    }


def _selection_note(selection: VersionSelectionResult) -> str:
    detail = _selection_detail(selection)
    return (
        f"selection_status={detail['selection_status']}; "
        f"required_dimensions={detail['required_dimensions']!r}; "
        f"candidate_count={detail['candidate_count']}"
    )


def _selection_note_from_detail(detail: SelectionDetail) -> str:
    return (
        f"selection_status={detail['selection_status']}; "
        f"required_dimensions={detail['required_dimensions']!r}; "
        f"candidate_count={detail['candidate_count']}"
    )


def _active_versions_with_selection_notes(
    timelines: Timelines,
    pit_date: str,
    ) -> tuple[Dict[LegalAddress, ProvisionVersion], Dict[LegalAddress, SelectionDetail]]:
    """Collect active versions while preserving ambiguous-scope notes."""
    active_versions: Dict[LegalAddress, ProvisionVersion] = {}
    selection_notes: Dict[LegalAddress, SelectionDetail] = {}
    for address, tl in timelines.items():
        selection = select_active_version_ex(tl, pit_date)
        if selection.status == "selected" and selection.version is not None:
            active_versions[address] = selection.version
        elif selection.status == "ambiguous_missing_scope":
            selection_notes[address] = _selection_detail(selection)
    return active_versions, selection_notes


def check_all_timeline_invariants_typed(
    ir_node: IRNode | IRStatute,
    timelines: Timelines,
    pit_date: str,
) -> List[TimelineInvariantViolation]:
    """Typed version of check_all_timeline_invariants for C3 evidence wiring.

    Returns structured violations with section attribution instead of
    plain strings. Evidence layer consumes these for per-section
    PROVED_REPLAY_BUG promotion.
    """
    typed_violations: List[TimelineInvariantViolation] = []

    for address, tl in timelines.items():
        permanent = [v for v in tl.versions if v.variant_kind == "permanent"]
        permanent.sort(key=lambda v: (v.effective, v.enacted))
        i = 0
        while i < len(permanent):
            j = i + 1
            while j < len(permanent) and permanent[j].effective == permanent[i].effective:
                j += 1
            count = j - i
            if count > 1:
                enacted_dates = {v.enacted for v in permanent[i:j]}
                if len(enacted_dates) < count:
                    typed_violations.append(
                        _typed_violation_from_address(
                            kind="overlapping_permanent",
                            address=address,
                            message=(
                                f"{address}: {count} permanent versions with same "
                                f"effective={permanent[i].effective!r} and overlapping "
                                f"enacted dates (ambiguous precedence)"
                            ),
                        )
                    )
            i = j

        temporaries = [v for v in tl.versions if v.variant_kind == "temporary"]
        for v in temporaries:
            if not v.expires:
                source_info = f" (source={v.source.statute_id})" if v.source else ""
                typed_violations.append(
                    _typed_violation_from_address(
                        kind="temporary_missing_expiry",
                        address=address,
                        message=(
                            f"{address}: temporary version effective={v.effective!r} has no expires date{source_info}"
                        ),
                    )
                )
                continue

            if v.expires < v.effective:
                source_info = f" (source={v.source.statute_id})" if v.source else ""
                typed_violations.append(
                    _typed_violation_from_address(
                        kind="temporary_bad_interval",
                        address=address,
                        message=(
                            f"{address}: temporary version has expires="
                            f"{v.expires!r} < effective={v.effective!r}{source_info}"
                        ),
                    )
                )

        sorted_temps = sorted(temporaries, key=lambda v: (v.effective, v.enacted))
        for a, b in pairwise(sorted_temps):
            if a.expires and b.effective < a.expires:
                typed_violations.append(
                    _typed_violation_from_address(
                        kind="temporary_overlap",
                        address=address,
                        message=(
                            f"{address}: overlapping temporary versions — "
                            f"v1=[{a.effective}, {a.expires}) vs "
                            f"v2=[{b.effective}, {b.expires or '...'})"
                        ),
                    )
                )

        for v in tl.versions:
            if v.variant_kind != "temporary":
                continue
            if v.source is None:
                continue
            if not v.source.expiry_chain:
                continue

            for i, override, previous in _expiry_chain_violations(source=v.source):
                if previous == "empty":
                    typed_violations.append(
                        _typed_violation_from_address(
                            kind="expiry_chain_non_monotone",
                            address=address,
                            message=(
                                f"{address}: expiry_chain[{i}] has empty new_expires "
                                f"(source={override.source_statute_id})"
                            ),
                        )
                    )
                    continue
                typed_violations.append(
                    _typed_violation_from_address(
                        kind="expiry_chain_non_monotone",
                        address=address,
                        message=(
                            f"{address}: expiry_chain[{i}] new_expires="
                            f"{override.new_expires!r} <= previous "
                            f"{previous!r} (not monotonically increasing)"
                        ),
                    )
                )

    ir_nodes = (
        _collect_statute_addressed_nodes(ir_node)
        if isinstance(ir_node, IRStatute)
        else _collect_addressed_nodes(ir_node)
    )
    active_versions, selection_notes = _active_versions_with_selection_notes(timelines, pit_date)

    for address in ir_nodes:
        if address not in active_versions and len(address.path) <= 2:
            note = selection_notes.get(address)
            note_text = f" ({_selection_note_from_detail(note)})" if note else ""
            typed_violations.append(
                _typed_violation_from_address(
                    kind="ir_without_timeline",
                    address=address,
                    message=(
                        f"IR_WITHOUT_TIMELINE: {address} present in IR "
                        f"but has no active timeline version at {pit_date}{note_text}"
                    ),
                    detail=note or {},
                )
            )

    for address, version in active_versions.items():
        if version is None or version.content is None:
            continue
        if address not in ir_nodes:
            if len(address.path) <= 2:
                typed_violations.append(
                    _typed_violation_from_address(
                        kind="timeline_without_ir",
                        address=address,
                        message=(
                            f"TIMELINE_WITHOUT_IR: {address} has active "
                            f"timeline version at {pit_date} but is missing from IR"
                        ),
                    )
                )
            continue

        timeline_text = irnode_to_text(version.content).strip()
        ir_text = irnode_to_text(ir_nodes[address]).strip()
        timeline_norm = " ".join(timeline_text.split())
        ir_norm = " ".join(ir_text.split())
        if timeline_norm and ir_norm and timeline_norm != ir_norm and _is_section_address(address):
            typed_violations.append(
                _typed_violation_from_address(
                    kind="content_mismatch",
                    address=address,
                    message=(
                        f"CONTENT_MISMATCH: {address} timeline={timeline_norm[:80]!r}... vs ir={ir_norm[:80]!r}..."
                    ),
                )
            )

    return typed_violations
