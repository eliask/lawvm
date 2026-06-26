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
from typing import Any, Dict, List, Literal, Mapping, NamedTuple, Sequence, TypedDict


from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.invariant_profiles import TimelineInvariantFamily
from lawvm.core.statute_facets import is_statute_title_address
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    OperationSource,
    ProvisionVersion,
)
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.mutation_boundary import TreePath
from lawvm.core.provenance import ExpiryOverride
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import (
    Timelines,
    VersionSelectionResult,
    select_active_version_ex,
)


class _ExpiryChainViolation(NamedTuple):
    index: int
    override: ExpiryOverride
    previous_expires: str


# ---------------------------------------------------------------------------
# 1. No overlapping permanent versions
# ---------------------------------------------------------------------------


def _permanent_version_identity_key(version: ProvisionVersion) -> tuple[str, str, str, str]:
    """Identity for duplicate-row detection at one timeline address."""
    source_id = version.source.statute_id if version.source is not None else ""
    if version.content_hash:
        content_key = version.content_hash
    elif version.content is not None:
        content_key = " ".join(irnode_to_text(version.content).split())
    else:
        content_key = ""
    return (version.effective, version.enacted, source_id, content_key)


def _collect_permanent_overlap_violations(timelines: Timelines) -> list[TimelineInvariantViolation]:
    """Classify same-effective permanent groups as duplicate rows vs true ambiguity."""
    violations: list[TimelineInvariantViolation] = []

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
                group = permanent[i:j]
                enacted_dates = {v.enacted for v in group}
                if len(enacted_dates) < count:
                    identity_keys = {_permanent_version_identity_key(v) for v in group}
                    if len(identity_keys) == 1:
                        violations.append(
                            _typed_violation_from_address(
                                kind="duplicate_permanent_version_row",
                                address=address,
                                message=(
                                    f"DUPLICATE_PERMANENT_VERSION_ROW: {address}: "
                                    f"{count} identical permanent version rows at "
                                    f"effective={permanent[i].effective!r}"
                                ),
                                detail={"duplicate_count": count},
                            )
                        )
                    else:
                        source_ids = sorted(
                            {
                                version.source.statute_id
                                for version in group
                                if version.source is not None and version.source.statute_id
                            }
                        )
                        semantic_keys = {
                            _permanent_version_identity_key(version)[3] for version in group
                        }
                        violations.append(
                            _typed_violation_from_address(
                                kind="overlapping_permanent",
                                address=address,
                                message=(
                                    f"{address}: {count} permanent versions with same "
                                    f"effective={permanent[i].effective!r} and overlapping "
                                    f"enacted dates (ambiguous precedence)"
                                ),
                                detail={
                                    "effective": permanent[i].effective,
                                    "duplicate_count": count,
                                    "enacted_dates": sorted(enacted_dates),
                                    "source_statute_ids": source_ids,
                                    "distinct_identity_count": len(identity_keys),
                                    "absent_content_count": sum(
                                        1 for version in group if version.content is None
                                    ),
                                    "semantic_text_equal": len(semantic_keys) == 1,
                                },
                            )
                        )
            i = j

    return violations


def check_no_overlapping_permanent_versions(timelines: Timelines) -> List[str]:
    """Check that no two permanent versions are active at the same date.

    Identical duplicate ledger rows are reported separately from true
    precedence ambiguity between competing permanent versions.
    """
    return [violation.message for violation in _collect_permanent_overlap_violations(timelines)]


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

            for violation in _expiry_chain_violations(source=v.source):
                if violation.previous_expires == "empty":
                    violations.append(
                        f"{address}: expiry_chain[{violation.index}] has empty new_expires "
                        f"(source={violation.override.source_statute_id})"
                    )
                    continue
                violations.append(
                    f"{address}: expiry_chain[{violation.index}] new_expires="
                    f"{violation.override.new_expires!r} <= previous "
                    f"{violation.previous_expires!r} (not monotonically increasing)"
                )

    return violations


def _expiry_chain_violations(
    *,
    source: OperationSource,
) -> list[_ExpiryChainViolation]:
    violations: list[_ExpiryChainViolation] = []
    prev_expires = source.expires_original or ""
    for index, override in enumerate(source.expiry_chain):
        new_expires = override.new_expires or ""
        if not new_expires:
            violations.append(_ExpiryChainViolation(index, override, "empty"))
            continue
        if prev_expires and new_expires <= prev_expires:
            violations.append(_ExpiryChainViolation(index, override, prev_expires))
        prev_expires = new_expires
    return violations


# ---------------------------------------------------------------------------
# 4. Replay-timeline consistency
# ---------------------------------------------------------------------------


def _collect_addressed_nodes(
    node: IRNode,
    current_path: TreePath = (),
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


TimelineReplayConsistencyMode = Literal["full", "robust"]
TimelineInvariantTier = Literal["robust", "materialization_variant"]

_ROBUST_TIMELINE_VIOLATION_KINDS = frozenset(
    {
        "overlapping_permanent",
        "temporary_missing_expiry",
        "temporary_bad_interval",
        "temporary_overlap",
        "expiry_chain_non_monotone",
        "content_mismatch",
        "same_source_descendant_shadow",
        "ir_without_timeline",
        "timeline_without_ir",
        "active_descendant_not_materialized",
    }
)

# Robust timeline hits that prove replay/materialization drift — not temporal-ledger ambiguity.
# Apply-phase same-source conflicts are owned by REPLAY.TRANSITION_DETECTOR; these are
# post-materialization consistency witnesses suitable for PROVED_REPLAY_BUG promotion.
_EVIDENCE_PROMOTABLE_TIMELINE_KINDS = frozenset(
    {
        "content_mismatch",
        "same_source_descendant_shadow",
    }
)

_LEGACY_ALL_TIMELINE_INVARIANT_FAMILIES: tuple[TimelineInvariantFamily, ...] = (
    "temporal_overlap",
    "temporary_overlay",
    "expiry_chain",
    "replay_timeline",
)


def _materialization_roots(ir_node: IRNode | IRStatute) -> tuple[IRNode, ...]:
    if isinstance(ir_node, IRStatute):
        roots = [ir_node.body, *ir_node.supplements]
        return tuple(roots)
    if ir_node.kind == IRNodeKind.BODY:
        return (ir_node,)
    return (ir_node,)


def _resolve_nodes_at_path(
    ir_node: IRNode | IRStatute,
    path: tuple[tuple[str, str], ...],
) -> tuple[IRNode, ...]:
    """Resolve materialized nodes by kind/label path, not only flat addressed indexes."""
    if not path:
        return _materialization_roots(ir_node)
    frontier = list(_materialization_roots(ir_node))
    for kind, label in path:
        next_frontier: list[IRNode] = []
        for node in frontier:
            for child in node.children:
                if _kind_str(child.kind) == kind and (child.label or "") == label:
                    next_frontier.append(child)
        frontier = next_frontier
        if not frontier:
            return ()
    return tuple(frontier)


def _normalized_ir_text(node: IRNode) -> str:
    return " ".join(irnode_to_text(node).split())


def _is_facet_timeline_address(address: LegalAddress) -> bool:
    """Timeline addresses for facets/title carriers outside the addressed-node walk."""
    return is_statute_title_address(address) or address.special is not None


def _address_present_in_materialized_tree(
    ir_node: IRNode | IRStatute,
    address: LegalAddress,
    ir_nodes: Mapping[LegalAddress, IRNode],
) -> bool:
    if address in ir_nodes:
        return True
    return bool(_resolve_nodes_at_path(ir_node, address.path))


def _materialized_node_for_address(
    ir_node: IRNode | IRStatute,
    address: LegalAddress,
    ir_nodes: Mapping[LegalAddress, IRNode],
) -> IRNode | None:
    if address in ir_nodes:
        return ir_nodes[address]
    resolved = _resolve_nodes_at_path(ir_node, address.path)
    if len(resolved) == 1:
        return resolved[0]
    return None


def _materialized_text_witness(
    *,
    ir_node: IRNode | IRStatute,
    address: LegalAddress,
    timeline_text: str,
    ir_nodes: Mapping[LegalAddress, IRNode],
) -> bool:
    """Return True when timeline text is already represented in the materialized tree."""
    norm_tl = " ".join(timeline_text.split())
    if not norm_tl:
        return True

    seen: set[int] = set()
    candidates: list[IRNode] = []
    if address in ir_nodes:
        candidates.append(ir_nodes[address])
    candidates.extend(_resolve_nodes_at_path(ir_node, address.path))
    for node in candidates:
        node_id = id(node)
        if node_id in seen:
            continue
        seen.add(node_id)
        if norm_tl in _normalized_ir_text(node):
            return True

    for depth in range(len(address.path) - 1, 0, -1):
        prefix = LegalAddress(path=address.path[:depth])
        prefix_nodes: list[IRNode] = []
        if prefix in ir_nodes:
            prefix_nodes.append(ir_nodes[prefix])
        prefix_nodes.extend(_resolve_nodes_at_path(ir_node, prefix.path))
        for node in prefix_nodes:
            node_id = id(node)
            if node_id in seen:
                continue
            seen.add(node_id)
            if norm_tl in _normalized_ir_text(node):
                return True
    return False


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
            if _is_facet_timeline_address(address):
                continue
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

        if _is_facet_timeline_address(address):
            continue
        if _address_present_in_materialized_tree(ir_node, address, ir_nodes):
            continue
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
        materialized_node = _materialized_node_for_address(ir_node, address, ir_nodes)
        if materialized_node is None:
            continue

        timeline_text = irnode_to_text(version.content).strip()
        ir_text = _normalized_ir_text(materialized_node)

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

    for violation in _same_source_descendant_shadow_violations(
        ir_nodes=ir_nodes,
        active_versions=active_versions,
        pit_date=pit_date,
    ):
        violations.append(
            _same_source_descendant_shadow_message(
                address=violation.address,
                ancestor_address=violation.ancestor_address,
                descendant_version=violation.descendant_version,
                timeline_text=violation.timeline_text,
                materialized_text=violation.materialized_text,
                pit_date=pit_date,
            )
        )

    for violation in _active_descendant_materialization_violations(
        ir_node=ir_node,
        ir_nodes=ir_nodes,
        active_versions=active_versions,
    ):
        violations.append(
            _active_descendant_materialization_message(
                violation=violation,
                pit_date=pit_date,
            )
        )

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
    "duplicate_permanent_version_row",
    "temporary_missing_expiry",
    "temporary_bad_interval",
    "temporary_overlap",
    "expiry_chain_non_monotone",
    "ir_without_timeline",
    "timeline_without_ir",
    "content_mismatch",
    "same_source_descendant_shadow",
    "active_descendant_not_materialized",
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", freeze_mapping(self.detail))


def _timeline_violation_tier(kind: InvariantKind, *, mode: TimelineReplayConsistencyMode) -> TimelineInvariantTier:
    if kind not in _ROBUST_TIMELINE_VIOLATION_KINDS:
        return "materialization_variant"
    if mode == "full":
        return "robust"
    if kind in {
        "ir_without_timeline",
        "timeline_without_ir",
        "active_descendant_not_materialized",
    }:
        return "materialization_variant"
    return "robust"


def _with_timeline_tier(
    violation: TimelineInvariantViolation,
    *,
    mode: TimelineReplayConsistencyMode,
) -> TimelineInvariantViolation:
    tier = _timeline_violation_tier(violation.kind, mode=mode)
    detail = dict(violation.detail)
    detail["tier"] = tier
    return TimelineInvariantViolation(
        kind=violation.kind,
        section_label=violation.section_label,
        address_path=violation.address_path,
        message=violation.message,
        detail=detail,
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
        section_label=_section_label_from_address(address),
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


def _section_path_depth(address: LegalAddress) -> int | None:
    for index, (kind, _label) in enumerate(address.path, start=1):
        if kind == "section":
            return index
    return None


def _is_section_descendant_address(address: LegalAddress) -> bool:
    section_depth = _section_path_depth(address)
    return section_depth is not None and len(address.path) > section_depth


def _source_statute_id(version: ProvisionVersion) -> str:
    return version.source.statute_id if version.source is not None else ""


@dataclass(frozen=True, slots=True)
class _SameSourceDescendantShadowViolation:
    address: LegalAddress
    ancestor_address: LegalAddress
    descendant_version: ProvisionVersion
    ancestor_version: ProvisionVersion
    timeline_text: str
    materialized_text: str


@dataclass(frozen=True, slots=True)
class _ActiveDescendantMaterializationViolation:
    address: LegalAddress
    ancestor_address: LegalAddress | None
    descendant_version: ProvisionVersion
    timeline_text: str
    ancestor_materialized_text: str


def _nearest_same_source_ancestor_version(
    address: LegalAddress,
    version: ProvisionVersion,
    active_versions: Mapping[LegalAddress, ProvisionVersion],
) -> tuple[LegalAddress, ProvisionVersion] | None:
    source_statute = _source_statute_id(version)
    if not source_statute:
        return None
    for depth in range(len(address.path) - 1, 0, -1):
        ancestor_address = LegalAddress(path=address.path[:depth])
        ancestor_version = active_versions.get(ancestor_address)
        if ancestor_version is None or ancestor_version.content is None:
            continue
        if _source_statute_id(ancestor_version) == source_statute:
            return ancestor_address, ancestor_version
    return None


def _same_source_descendant_shadow_violations(
    *,
    ir_nodes: Mapping[LegalAddress, IRNode],
    active_versions: Mapping[LegalAddress, ProvisionVersion],
    pit_date: str,
) -> list[_SameSourceDescendantShadowViolation]:
    violations: list[_SameSourceDescendantShadowViolation] = []
    for address, version in active_versions.items():
        if len(address.path) <= 1:
            continue
        if version.content is None:
            continue
        ancestor_pair = _nearest_same_source_ancestor_version(address, version, active_versions)
        if ancestor_pair is None:
            continue
        ancestor_address, ancestor_version = ancestor_pair
        timeline_text = " ".join(irnode_to_text(version.content).split())
        if not timeline_text:
            continue
        materialized_node = ir_nodes.get(address)
        materialized_text = (
            " ".join(irnode_to_text(materialized_node).split())
            if materialized_node is not None
            else ""
        )
        if materialized_text == timeline_text:
            continue
        violations.append(
            _SameSourceDescendantShadowViolation(
                address=address,
                ancestor_address=ancestor_address,
                descendant_version=version,
                ancestor_version=ancestor_version,
                timeline_text=timeline_text,
                materialized_text=materialized_text,
            )
        )
    return violations


def _same_source_descendant_shadow_message(
    *,
    address: LegalAddress,
    ancestor_address: LegalAddress,
    descendant_version: ProvisionVersion,
    timeline_text: str,
    materialized_text: str,
    pit_date: str,
) -> str:
    source_statute = _source_statute_id(descendant_version)
    materialized_preview = materialized_text[:80] if materialized_text else "<missing>"
    return (
        f"SAME_SOURCE_DESCENDANT_SHADOW: {address} selected descendant from "
        f"{source_statute} at {pit_date} is not materialized under same-source "
        f"ancestor {ancestor_address}; timeline={timeline_text[:80]!r}... vs "
        f"ir={materialized_preview!r}..."
    )


def _nearest_materialized_ancestor(
    address: LegalAddress,
    ir_nodes: Mapping[LegalAddress, IRNode],
) -> tuple[LegalAddress, IRNode] | None:
    for depth in range(len(address.path) - 1, 0, -1):
        ancestor_address = LegalAddress(path=address.path[:depth])
        ancestor_node = ir_nodes.get(ancestor_address)
        if ancestor_node is not None:
            return ancestor_address, ancestor_node
    return None


def _normalized_ir_text(node: IRNode) -> str:
    return " ".join(irnode_to_text(node).split())


def _active_descendant_materialization_violations(
    *,
    ir_node: IRNode | IRStatute,
    ir_nodes: Mapping[LegalAddress, IRNode],
    active_versions: Mapping[LegalAddress, ProvisionVersion],
) -> list[_ActiveDescendantMaterializationViolation]:
    violations: list[_ActiveDescendantMaterializationViolation] = []
    for address, version in active_versions.items():
        if not _is_section_descendant_address(address):
            continue
        if version.content is None:
            continue
        if address in ir_nodes or _resolve_nodes_at_path(ir_node, address.path):
            continue
        timeline_text = _normalized_ir_text(version.content)
        if not timeline_text:
            continue
        if _materialized_text_witness(
            ir_node=ir_node,
            address=address,
            timeline_text=timeline_text,
            ir_nodes=ir_nodes,
        ):
            continue
        ancestor_pair = _nearest_materialized_ancestor(address, ir_nodes)
        if ancestor_pair is None:
            for depth in range(len(address.path) - 1, 0, -1):
                prefix = LegalAddress(path=address.path[:depth])
                resolved = _resolve_nodes_at_path(ir_node, prefix.path)
                if resolved:
                    ancestor_pair = (prefix, resolved[0])
                    break
        if ancestor_pair is None:
            violations.append(
                _ActiveDescendantMaterializationViolation(
                    address=address,
                    ancestor_address=None,
                    descendant_version=version,
                    timeline_text=timeline_text,
                    ancestor_materialized_text="",
                )
            )
            continue
        ancestor_address, ancestor_node = ancestor_pair
        ancestor_text = _normalized_ir_text(ancestor_node)
        violations.append(
            _ActiveDescendantMaterializationViolation(
                address=address,
                ancestor_address=ancestor_address,
                descendant_version=version,
                timeline_text=timeline_text,
                ancestor_materialized_text=ancestor_text,
            )
        )
    return violations


def _active_descendant_materialization_message(
    *,
    violation: _ActiveDescendantMaterializationViolation,
    pit_date: str,
) -> str:
    source_statute = _source_statute_id(violation.descendant_version)
    source_text = f" from {source_statute}" if source_statute else ""
    ancestor_address = (
        str(violation.ancestor_address)
        if violation.ancestor_address is not None
        else "<missing>"
    )
    ancestor_text = violation.ancestor_materialized_text
    ancestor_preview = ancestor_text[:80] if ancestor_text else "<missing>"
    return (
        f"ACTIVE_DESCENDANT_NOT_MATERIALIZED: {violation.address} has active "
        f"descendant timeline content{source_text} at {pit_date}, but no "
        f"materialized address and no text witness under ancestor "
        f"{ancestor_address}; timeline={violation.timeline_text[:80]!r}... vs "
        f"ancestor_ir={ancestor_preview!r}..."
    )


def _selection_detail(selection: VersionSelectionResult) -> SelectionDetail:
    """Extract ambiguity-preserving metadata from a selection result."""
    certificate = selection.certificate
    return {
        "selection_status": selection.selection_status,
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
        if selection.selection_status == "selected" and selection.version is not None:
            active_versions[address] = selection.version
        elif selection.selection_status == "ambiguous_missing_scope":
            selection_notes[address] = _selection_detail(selection)
    return active_versions, selection_notes


def check_all_timeline_invariants_typed(
    ir_node: IRNode | IRStatute,
    timelines: Timelines,
    pit_date: str,
    families: Sequence[TimelineInvariantFamily] | None = None,
) -> List[TimelineInvariantViolation]:
    """Typed version of check_all_timeline_invariants for C3 evidence wiring.

    Returns structured violations with section attribution instead of
    plain strings. Evidence layer consumes these for per-section
    PROVED_REPLAY_BUG promotion.
    """
    selected = tuple(families) if families is not None else _LEGACY_ALL_TIMELINE_INVARIANT_FAMILIES
    replay_mode: TimelineReplayConsistencyMode = (
        "full" if "replay_timeline" in selected else "robust"
    )
    typed_violations: List[TimelineInvariantViolation] = []

    if "temporal_overlap" not in selected and "temporary_overlay" not in selected and "expiry_chain" not in selected and "replay_timeline" not in selected and "replay_timeline_robust" not in selected:
        return []

    run_replay_checks = "replay_timeline" in selected or "replay_timeline_robust" in selected

    if "temporal_overlap" in selected:
        typed_violations.extend(_collect_permanent_overlap_violations(timelines))

    for address, tl in timelines.items():
        if "temporary_overlay" in selected:
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

        if "expiry_chain" in selected:
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

    if run_replay_checks:
        ir_nodes = (
            _collect_statute_addressed_nodes(ir_node)
            if isinstance(ir_node, IRStatute)
            else _collect_addressed_nodes(ir_node)
        )
        active_versions, selection_notes = _active_versions_with_selection_notes(timelines, pit_date)

        for address in ir_nodes:
            if address in active_versions or _is_facet_timeline_address(address):
                continue
            if len(address.path) <= 2:
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
            if _is_facet_timeline_address(address):
                continue
            materialized_node = _materialized_node_for_address(ir_node, address, ir_nodes)
            if materialized_node is None:
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
            ir_text = _normalized_ir_text(materialized_node)
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

        for violation in _same_source_descendant_shadow_violations(
            ir_nodes=ir_nodes,
            active_versions=active_versions,
            pit_date=pit_date,
        ):
            typed_violations.append(
                _typed_violation_from_address(
                    kind="same_source_descendant_shadow",
                    address=violation.address,
                    message=_same_source_descendant_shadow_message(
                        address=violation.address,
                        ancestor_address=violation.ancestor_address,
                        descendant_version=violation.descendant_version,
                        timeline_text=violation.timeline_text,
                        materialized_text=violation.materialized_text,
                        pit_date=pit_date,
                    ),
                    detail={
                        "ancestor_address": str(violation.ancestor_address),
                        "source_statute": _source_statute_id(violation.descendant_version),
                        "descendant_effective": violation.descendant_version.effective,
                        "descendant_enacted": violation.descendant_version.enacted,
                        "ancestor_effective": violation.ancestor_version.effective,
                        "ancestor_enacted": violation.ancestor_version.enacted,
                        "timeline_preview": violation.timeline_text[:120],
                        "materialized_preview": violation.materialized_text[:120],
                    },
                )
            )

        for violation in _active_descendant_materialization_violations(
            ir_node=ir_node,
            ir_nodes=ir_nodes,
            active_versions=active_versions,
        ):
            typed_violations.append(
                _typed_violation_from_address(
                    kind="active_descendant_not_materialized",
                    address=violation.address,
                    message=_active_descendant_materialization_message(
                        violation=violation,
                        pit_date=pit_date,
                    ),
                    detail={
                        "ancestor_address": (
                            str(violation.ancestor_address)
                            if violation.ancestor_address is not None
                            else ""
                        ),
                        "source_statute": _source_statute_id(violation.descendant_version),
                        "descendant_effective": violation.descendant_version.effective,
                        "descendant_enacted": violation.descendant_version.enacted,
                        "timeline_preview": violation.timeline_text[:120],
                        "ancestor_materialized_preview": violation.ancestor_materialized_text[:120],
                    },
                )
            )

    include_variants = replay_mode == "full" or "replay_timeline" in selected
    filtered: list[TimelineInvariantViolation] = []
    for violation in typed_violations:
        tiered = _with_timeline_tier(violation, mode=replay_mode)
        if include_variants or tiered.detail.get("tier") == "robust":
            filtered.append(tiered)
    return filtered


def timeline_invariant_violation_row(violation: TimelineInvariantViolation) -> dict[str, Any]:
    """Serialize a typed violation for evidence and bench diagnostic surfaces."""
    row = {
        "kind": violation.kind,
        "section_label": violation.section_label,
        "address_path": violation.address_path,
        "message": violation.message,
    }
    row.update(dict(violation.detail))
    return row


def collect_apply_phase_shadow_paths(
    findings: Sequence[Any],
) -> frozenset[str]:
    """Collect descendant paths already witnessed by apply-phase transition detectors."""
    paths: set[str] = set()
    for finding in findings:
        if isinstance(finding, Mapping):
            kind = str(finding.get("kind") or "")
            detail = finding.get("detail") or {}
        else:
            kind = str(getattr(finding, "kind", "") or "")
            detail = getattr(finding, "detail", None) or {}
        if kind != "REPLAY.TRANSITION_DETECTOR":
            continue
        if not isinstance(detail, Mapping):
            continue
        if str(detail.get("detector") or "") != "same_source_descendant_snapshot_shadow":
            continue
        path = str(detail.get("path") or "").strip()
        if path:
            paths.add(path)
    return frozenset(paths)


def is_promotable_timeline_invariant_row(
    row: Mapping[str, Any],
    *,
    apply_phase_shadow_paths: frozenset[str] | None = None,
) -> bool:
    """Return True when a violation row may promote to PROVED_REPLAY_BUG."""
    tier = str(row.get("tier") or "").strip()
    if tier and tier != "robust":
        return False
    kind = str(row.get("kind") or "").strip()
    if kind not in _EVIDENCE_PROMOTABLE_TIMELINE_KINDS:
        return False
    if (
        kind == "same_source_descendant_shadow"
        and apply_phase_shadow_paths
        and str(row.get("address_path") or "").strip() in apply_phase_shadow_paths
    ):
        return False
    if tier:
        return True
    return kind in _EVIDENCE_PROMOTABLE_TIMELINE_KINDS


def filter_promotable_timeline_invariant_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    apply_phase_shadow_paths: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Keep only robust-tier timeline violations for evidence promotion."""
    return [
        dict(row)
        for row in rows
        if is_promotable_timeline_invariant_row(
            row,
            apply_phase_shadow_paths=apply_phase_shadow_paths,
        )
    ]
