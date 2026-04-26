"""Pure PIT/body materialization helpers for timeline consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional, Sequence

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, ProvisionVersion
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_addresses import (
    _address_prefix_matches,
    _retarget_version_content,
    _sort_label_key,
)
from lawvm.core.timeline_lineage import current_address_from_migration_events


@dataclass(frozen=True)
class MaterializationSelectionState:
    address: LegalAddress
    status: Literal["selected", "inactive", "ambiguous_missing_scope"]
    version: Optional[ProvisionVersion] = None


def is_strip_node(node: IRNode) -> bool:
    """Nodes that the replay strips during post-processing."""
    if node.kind == IRNodeKind.OMISSION:
        return True
    if node.kind == IRNodeKind.HCONTAINER and node.attrs.get("name") in ("omission", "conclusions"):
        return True
    return False


def clean_timeline_content(content: Optional[IRNode]) -> Optional[IRNode]:
    """Clean timeline content before materialization."""
    if content is None or not content.children:
        return content
    filtered = []
    for c in content.children:
        if is_strip_node(c):
            continue
        cleaned = clean_timeline_content(c)
        filtered.append(cleaned if cleaned is not None else c)
    if len(filtered) == len(content.children) and all(f is o for f, o in zip(filtered, content.children)):
        return content
    return IRNode(
        kind=content.kind,
        label=content.label,
        text=content.text,
        attrs=dict(content.attrs),
        children=tuple(filtered),
    )


def normalize_base_node(node: IRNode) -> IRNode:
    """Apply text normalization and omission stripping to base content."""
    from lawvm.core.tree_ops import normalize_text

    normalized = normalize_text(node)
    if normalized.children:
        filtered = []
        for c in normalized.children:
            if is_strip_node(c):
                continue
            filtered.append(normalize_base_node(c))
        if len(filtered) != len(normalized.children):
            return IRNode(
                kind=normalized.kind,
                label=normalized.label,
                text=normalized.text,
                attrs=dict(normalized.attrs),
                children=tuple(filtered),
            )
    return normalized


def _node_has_duplicate_raw_children(node: IRNode) -> bool:
    """Return whether ``node`` has duplicate labeled direct children."""
    counts: dict[tuple[str, str], int] = {}
    for child in node.children:
        if child.label is None:
            continue
        key = (_kind_str(child.kind), child.label)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > 1:
            return True
    return False


def _node_has_relative_address(
    node: IRNode,
    relative_path: tuple[tuple[str, str], ...],
) -> bool:
    """Return whether ``node`` contains the given labeled descendant path."""
    if not relative_path:
        return True
    kind, label = relative_path[0]
    for child in node.children:
        if child.label is None:
            if _node_has_relative_address(child, relative_path):
                return True
            continue
        if _kind_str(child.kind) != kind or child.label != label:
            continue
        if _node_has_relative_address(child, relative_path[1:]):
            return True
    return False


def _base_child_matches_active_descendant(
    child: IRNode,
    child_path: tuple[tuple[str, str], ...],
    active: dict[LegalAddress, Optional[IRNode]],
    active_prefixes: Optional[set[tuple[tuple[str, str], ...]]],
) -> bool:
    """Return whether active descendant overlays structurally belong under ``child``."""
    candidate_paths: set[tuple[tuple[str, str], ...]] = set()
    if active_prefixes is not None:
        candidate_paths.update(active_prefixes)
    candidate_paths.update(addr.path for addr in active)
    prefix_len = len(child_path)
    for candidate in candidate_paths:
        if len(candidate) <= prefix_len:
            continue
        if candidate[:prefix_len] != child_path:
            continue
        if _node_has_relative_address(child, candidate[prefix_len:]):
            return True
    return False


def _filtered_active_for_base_child(
    child: IRNode,
    child_path: tuple[tuple[str, str], ...],
    active: dict[LegalAddress, Optional[IRNode]],
) -> tuple[dict[LegalAddress, Optional[IRNode]], set[tuple[tuple[str, str], ...]]]:
    """Return active entries whose descendant paths structurally belong under ``child``."""
    filtered_active: dict[LegalAddress, Optional[IRNode]] = {}
    filtered_prefixes: set[tuple[tuple[str, str], ...]] = set()
    prefix_len = len(child_path)
    for address, content in active.items():
        if len(address.path) <= prefix_len:
            continue
        if address.path[:prefix_len] != child_path:
            continue
        if not _node_has_relative_address(child, address.path[prefix_len:]):
            continue
        filtered_active[address] = content
        for depth in range(1, len(address.path)):
            filtered_prefixes.add(address.path[:depth])
    return filtered_active, filtered_prefixes


def apply_overlays(
    content: IRNode,
    parent_address: LegalAddress,
    active: dict[LegalAddress, Optional[IRNode]],
    label_norm: Optional[Callable[[str], str]] = None,
    active_prefixes: Optional[set[tuple[tuple[str, str], ...]]] = None,
    issue_sink: Any = None,
    emit_warnings: bool = True,
    *,
    record_issue: Callable[..., None],
) -> IRNode:
    """Apply child-level active version overrides to content (overlay semantics)."""
    parent_len = len(parent_address.path)
    child_overrides: dict[tuple[str, str], Optional[IRNode]] = {}
    for addr, child_content in active.items():
        if len(addr.path) == parent_len + 1 and addr.path[:parent_len] == parent_address.path:
            child_overrides[addr.path[-1]] = child_content

    if not child_overrides and active_prefixes is None:
        return content

    new_children: list[IRNode] = []
    seen_overrides: set[tuple[str, str]] = set()
    dup_norm_keys: set[tuple[str, str]] = set()
    duplicate_raw_counts: dict[tuple[str, str], int] = {}
    for child in content.children:
        if child.label is None:
            continue
        raw_key = (_kind_str(child.kind), child.label)
        duplicate_raw_counts[raw_key] = duplicate_raw_counts.get(raw_key, 0) + 1
    if label_norm:
        norm_count: dict[tuple[str, str], int] = {}
        for child in content.children:
            if child.label is None:
                continue
            k = (_kind_str(child.kind), label_norm(child.label))
            norm_count[k] = norm_count.get(k, 0) + 1
        dup_norm_keys = {k for k, cnt in norm_count.items() if cnt > 1}

    for child in content.children:
        if child.label is None:
            new_children.append(child)
            continue
        norm_label = (
            label_norm(child.label)
            if (label_norm and (_kind_str(child.kind), label_norm(child.label)) not in dup_norm_keys)
            else child.label
        )
        key = (_kind_str(child.kind), norm_label)
        child_addr = LegalAddress(path=parent_address.path + (key,))
        exact_key = (_kind_str(child.kind), child.label)
        raw_addr = LegalAddress(path=parent_address.path + (exact_key,))
        if key not in child_overrides and exact_key in child_overrides and exact_key[1] != key[1]:
            key = exact_key
            child_addr = LegalAddress(path=parent_address.path + (key,))

        if duplicate_raw_counts.get(exact_key, 0) > 1 and (
            key in child_overrides or exact_key in child_overrides
        ):
            seen_overrides.add(key)
            seen_overrides.add(exact_key)
            record_issue(
                issue_sink,
                kind="duplicate_selected_address_descendant_overlay",
                message=(
                    "apply_overlays: preserved duplicate-labeled selected content "
                    f"at {raw_addr} and ignored ambiguous direct child override"
                ),
                address=raw_addr,
                emit_warnings=emit_warnings,
            )
            normalized = normalize_base_node(child)
            child_active, child_active_prefixes = _filtered_active_for_base_child(
                normalized,
                raw_addr.path,
                active,
            )
            if child_active:
                new_children.append(
                    apply_overlays(
                        normalized,
                        raw_addr,
                        child_active,
                        label_norm,
                        child_active_prefixes,
                        issue_sink=issue_sink,
                        emit_warnings=emit_warnings,
                        record_issue=record_issue,
                    )
                )
            else:
                new_children.append(normalized)
            continue

        if key in child_overrides:
            seen_overrides.add(key)
            override = child_overrides[key]
            if override is not None:
                new_children.append(
                    apply_overlays(
                        override,
                        child_addr,
                        active,
                        label_norm,
                        active_prefixes,
                        issue_sink=issue_sink,
                        emit_warnings=emit_warnings,
                        record_issue=record_issue,
                    )
                )
        elif active_prefixes is not None and child_addr.path in active_prefixes:
            normalized = normalize_base_node(child)
            new_children.append(
                apply_overlays(
                    normalized,
                    child_addr,
                    active,
                    label_norm,
                    active_prefixes,
                    issue_sink=issue_sink,
                    emit_warnings=emit_warnings,
                    record_issue=record_issue,
                )
            )
        else:
            new_children.append(child)

    for key, override in child_overrides.items():
        if key not in seen_overrides and override is not None and key in dup_norm_keys:
            record_issue(
                issue_sink,
                kind="duplicate_normalized_sibling_override",
                message=(
                    f"_apply_overlays: override {key!r} could not be matched to any "
                    f"sibling under {parent_address} (normalized-duplicate siblings "
                    f"exist, exact-label fallback did not match)"
                ),
                address=parent_address,
                emit_warnings=emit_warnings,
            )
    for key, override in child_overrides.items():
        if key not in seen_overrides and override is not None and key not in dup_norm_keys:
            child_addr = LegalAddress(path=parent_address.path + (key,))
            node = apply_overlays(
                override,
                child_addr,
                active,
                label_norm,
                active_prefixes,
                issue_sink=issue_sink,
                emit_warnings=emit_warnings,
                record_issue=record_issue,
            )
            insert_key = _sort_label_key(key[1])
            insert_idx = len(new_children)
            for idx, existing in enumerate(new_children):
                if _kind_str(existing.kind) == key[0] and existing.label is not None:
                    if _sort_label_key(existing.label) > insert_key:
                        insert_idx = idx
                        break
            new_children.insert(insert_idx, node)

    return IRNode(
        kind=content.kind,
        label=content.label,
        text=content.text,
        attrs=dict(content.attrs),
        children=tuple(new_children),
    )


def overlay_on_container(
    base_children: Sequence[IRNode],
    active: dict[LegalAddress, Optional[IRNode]],
    top_keys: set[LegalAddress],
    parent_path: tuple[tuple[str, str], ...],
    active_prefixes: Optional[set[tuple[tuple[str, str], ...]]] = None,
    seen_keys: Optional[set[tuple[tuple[str, str], ...]]] = None,
    label_norm: Optional[Callable[[str], str]] = None,
    issue_sink: Any = None,
    emit_warnings: bool = True,
    *,
    record_issue: Callable[..., None],
) -> tuple[IRNode, ...]:
    """Walk base container children, replacing labeled nodes with timeline versions."""
    seen: set[tuple[tuple[str, str], ...]] = seen_keys if seen_keys is not None else set()
    children: list[IRNode] = []
    duplicate_raw_counts: dict[tuple[str, str], int] = {}
    for child in base_children:
        if child.label is None:
            continue
        raw_key = (_kind_str(child.kind), child.label)
        duplicate_raw_counts[raw_key] = duplicate_raw_counts.get(raw_key, 0) + 1

    for c in base_children:
        if is_strip_node(c):
            continue

        if c.label is not None:
            norm_label = label_norm(c.label) if label_norm else c.label
            key = (_kind_str(c.kind), norm_label)
            addr = LegalAddress(path=parent_path + (key,))
            raw_key = (_kind_str(c.kind), c.label)
            raw_addr = LegalAddress(path=parent_path + (raw_key,))
            preserve_base_structure = False
            if duplicate_raw_counts.get(raw_key, 0) > 1:
                preserve_base_structure = True
            elif raw_addr in active and _node_has_duplicate_raw_children(c):
                preserve_base_structure = True
            if preserve_base_structure:
                seen.add(raw_addr.path)
                if raw_addr in active and active.get(raw_addr) is not None:
                    record_issue(
                        issue_sink,
                        kind="duplicate_base_address_descendant_overlay",
                        message=(
                            "overlay_on_container: preserved duplicate-labeled base content "
                            f"at {raw_addr} and applied overlays against base structure"
                        ),
                        address=raw_addr,
                        emit_warnings=emit_warnings,
                    )
                normalized = normalize_base_node(c)
                child_active, child_active_prefixes = _filtered_active_for_base_child(
                    normalized,
                    raw_addr.path,
                    active,
                )
                children.append(
                    IRNode(
                        kind=normalized.kind,
                        label=normalized.label,
                        text=normalized.text,
                        attrs=dict(normalized.attrs),
                        children=overlay_on_container(
                            normalized.children,
                            child_active,
                            top_keys,
                            raw_addr.path,
                            active_prefixes=child_active_prefixes,
                            seen_keys=seen,
                            label_norm=label_norm,
                            issue_sink=issue_sink,
                            emit_warnings=emit_warnings,
                            record_issue=record_issue,
                        ),
                    )
                )
                continue
            seen.add(addr.path)
            if addr in active:
                content = active[addr]
                if content is None:
                    continue
                children.append(
                    apply_overlays(
                        content,
                        addr,
                        active,
                        label_norm,
                        active_prefixes,
                        issue_sink=issue_sink,
                        emit_warnings=emit_warnings,
                        record_issue=record_issue,
                    )
                )
            elif active_prefixes is not None and addr.path in active_prefixes:
                normalized = normalize_base_node(c)
                children.append(
                    IRNode(
                        kind=normalized.kind,
                        label=normalized.label,
                        text=normalized.text,
                        attrs=dict(normalized.attrs),
                        children=overlay_on_container(
                            normalized.children,
                            active,
                            top_keys,
                            addr.path,
                            active_prefixes=active_prefixes,
                            label_norm=label_norm,
                            issue_sink=issue_sink,
                            emit_warnings=emit_warnings,
                            record_issue=record_issue,
                        ),
                    )
                )
            else:
                children.append(normalize_base_node(c))
        elif c.kind == IRNodeKind.HCONTAINER and c.children:
            has_labeled = any(gc.label is not None for gc in c.children)
            if has_labeled:
                inner = overlay_on_container(
                    c.children,
                    active,
                    top_keys,
                    parent_path,
                    active_prefixes=active_prefixes,
                    seen_keys=seen,
                    label_norm=label_norm,
                    issue_sink=issue_sink,
                    emit_warnings=emit_warnings,
                    record_issue=record_issue,
                )
                children.extend(inner)
            else:
                children.append(normalize_base_node(c))
        else:
            children.append(normalize_base_node(c))

    for addr in sorted(active.keys(), key=lambda a: _sort_label_key(a.path[-1][1])):
        if len(addr.path) != len(parent_path) + 1:
            continue
        if addr.path[: len(parent_path)] != parent_path:
            continue
        if addr.path in seen:
            continue
        content = active.get(addr)
        if content is None:
            continue
        seen.add(addr.path)
        node = apply_overlays(
            content,
            addr,
            active,
            label_norm,
            active_prefixes=active_prefixes,
            issue_sink=issue_sink,
            emit_warnings=emit_warnings,
            record_issue=record_issue,
        )
        insert_key = _sort_label_key(addr.path[-1][1])
        insert_idx = len(children)
        for idx, existing in enumerate(children):
            if existing.kind == addr.path[-1][0] and existing.label is not None:
                if _sort_label_key(existing.label) > insert_key:
                    insert_idx = idx
                    break
        children.insert(insert_idx, node)

    return tuple(children)


def materialize_body(
    active: dict[LegalAddress, Optional[IRNode]],
    active_versions: dict[LegalAddress, ProvisionVersion],
    base: Optional[IRStatute],
    *,
    label_norm: Optional[Callable[[str], str]] = None,
    issue_sink: Any = None,
    emit_warnings: bool = True,
    record_issue: Callable[..., None],
) -> IRNode:
    """Build body from timeline entries, preserving unlabeled base content."""
    top_keys: set[LegalAddress] = {addr for addr in active if len(addr.path) == 1}
    for addr in list(active):
        if active.get(addr) is None:
            continue
        for depth in range(1, len(addr.path)):
            parent = LegalAddress(path=addr.path[:depth])
            if parent in active and active[parent] is not None:
                continue
            kind, label = addr.path[depth - 1]
            if kind in {"section", "subsection", "paragraph", "item"}:
                continue
            parent_version = active_versions.get(parent)
            descendant_version = active_versions.get(addr)
            if (
                parent in active
                and active[parent] is None
                and parent_version is not None
                and descendant_version is not None
                and (descendant_version.effective, descendant_version.enacted)
                <= (parent_version.effective, parent_version.enacted)
            ):
                continue
            active[parent] = IRNode(
                kind=IRNodeKind(kind),
                label=label,
                attrs={"lawvm_synthesized_container": "active_descendant"},
                children=(IRNode(kind=IRNodeKind.NUM, text=label),),
            )
            if depth == 1:
                top_keys.add(parent)

    if base is None:
        top_level = sorted(top_keys, key=lambda a: _sort_label_key(a.path[0][1]))
        children: list[IRNode] = []
        for addr in top_level:
            content = active[addr]
            if content is None:
                continue
            children.append(
                apply_overlays(
                    content,
                    addr,
                    active,
                    label_norm,
                    active_prefixes=None,
                    issue_sink=issue_sink,
                    emit_warnings=emit_warnings,
                    record_issue=record_issue,
                )
            )
        return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(children))

    active_prefixes: set[tuple[tuple[str, str], ...]] = set()
    for addr in active:
        for depth in range(1, len(addr.path)):
            active_prefixes.add(addr.path[:depth])

    children = overlay_on_container(
        base.body.children,
        active,
        top_keys,
        (),
        active_prefixes=active_prefixes,
        label_norm=label_norm,
        issue_sink=issue_sink,
        emit_warnings=emit_warnings,
        record_issue=record_issue,
    )
    return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(children))


def materialize_root_nodes(
    base_nodes: Sequence[IRNode],
    active: dict[LegalAddress, Optional[IRNode]],
    *,
    label_norm: Optional[Callable[[str], str]] = None,
    issue_sink: Any = None,
    emit_warnings: bool = True,
    record_issue: Callable[..., None],
) -> tuple[IRNode, ...]:
    """Materialize a top-level IRNode list with timeline overlays applied."""
    top_keys: set[LegalAddress] = {addr for addr in active if len(addr.path) == 1}
    if not base_nodes:
        materialized: list[IRNode] = []
        for addr in sorted(top_keys, key=lambda a: _sort_label_key(a.path[0][1])):
            content = active.get(addr)
            if content is None:
                continue
            materialized.append(
                apply_overlays(
                    content,
                    addr,
                    active,
                    label_norm,
                    active_prefixes=None,
                    issue_sink=issue_sink,
                    emit_warnings=emit_warnings,
                    record_issue=record_issue,
                )
            )
        return tuple(materialized)

    active_prefixes: set[tuple[tuple[str, str], ...]] = set()
    for addr in active:
        for depth in range(1, len(addr.path)):
            active_prefixes.add(addr.path[:depth])

    return overlay_on_container(
        base_nodes,
        active,
        top_keys,
        (),
        active_prefixes=active_prefixes,
        label_norm=label_norm,
        issue_sink=issue_sink,
        emit_warnings=emit_warnings,
        record_issue=record_issue,
    )


def top_level_supplement_active(
    active: dict[LegalAddress, Optional[IRNode]],
    *,
    base: Optional[IRStatute],
    body_top_level_kinds: frozenset[str],
) -> dict[LegalAddress, Optional[IRNode]]:
    """Return the active top-level roots that belong in statute supplements."""
    supplement_root_kinds = {str(node.kind) for node in (base.supplements if base else ())}
    if not supplement_root_kinds:
        supplement_root_kinds = {
            addr.path[0][0] for addr in active if addr.path and addr.path[0][0] not in body_top_level_kinds
        }
    return {addr: content for addr, content in active.items() if addr.path and addr.path[0][0] in supplement_root_kinds}


def project_materialization_selection_states(
    selection_states: Sequence[MaterializationSelectionState],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
) -> tuple[
    dict[LegalAddress, Optional[IRNode]],
    dict[LegalAddress, ProvisionVersion],
    tuple[LegalAddress, ...],
]:
    """Project materialization selections onto the address visible at ``as_of``."""
    if not migration_events:
        active: dict[LegalAddress, Optional[IRNode]] = {}
        active_versions: dict[LegalAddress, ProvisionVersion] = {}
        ambiguous_addresses: list[LegalAddress] = []
        for state in selection_states:
            if state.status == "ambiguous_missing_scope":
                active[state.address] = None
                ambiguous_addresses.append(state.address)
                continue
            if state.status == "inactive":
                active[state.address] = None
                continue
            if state.version is None:
                continue
            active[state.address] = clean_timeline_content(state.version.content)
            active_versions[state.address] = state.version
        return (
            active,
            active_versions,
            tuple(sorted(ambiguous_addresses, key=lambda address: address.path)),
        )

    @dataclass
    class _ProjectedBucket:
        ambiguous: bool = False
        inactive: bool = False
        selected_version: Optional[ProvisionVersion] = None
        selected_is_native: bool = False

    projected: dict[LegalAddress, _ProjectedBucket] = {}
    suppressed_source_addresses: set[LegalAddress] = set()
    for state in sorted(selection_states, key=lambda item: item.address.path):
        migrated_address = current_address_from_migration_events(
            state.address,
            migration_events,
            as_of_date=as_of,
            address_prefix_matches=_address_prefix_matches,
        )
        if migrated_address != state.address:
            suppressed_source_addresses.add(state.address)
        bucket = projected.setdefault(migrated_address, _ProjectedBucket())
        if state.status == "ambiguous_missing_scope":
            bucket.ambiguous = True
            bucket.inactive = False
            bucket.selected_version = None
            bucket.selected_is_native = False
            continue
        if state.status == "inactive":
            if not bucket.ambiguous and bucket.selected_version is None:
                bucket.inactive = True
            continue
        if state.version is None:
            continue
        version = state.version
        is_native = migrated_address == state.address
        if not is_native and version.content is not None:
            version = _retarget_version_content(version, migrated_address)
        if bucket.ambiguous:
            continue
        current_version = bucket.selected_version
        if current_version is None:
            bucket.selected_version = version
            bucket.selected_is_native = is_native
            bucket.inactive = False
            continue
        current_rank = (
            current_version.effective,
            current_version.enacted,
            1 if bucket.selected_is_native else 0,
        )
        incoming_rank = (
            version.effective,
            version.enacted,
            1 if is_native else 0,
        )
        current_source_statute = (
            current_version.source.statute_id
            if current_version.source is not None
            else ""
        )
        incoming_source_statute = (
            version.source.statute_id
            if version.source is not None
            else ""
        )
        source_leaf_changed = (
            bool(state.address.path)
            and bool(migrated_address.path)
            and state.address.path[-1] != migrated_address.path[-1]
        )
        if (
            bucket.selected_is_native
            and not is_native
            and incoming_rank > current_rank
            and current_version.content is not None
            and version.content is not None
            and source_leaf_changed
            and current_source_statute
            and incoming_source_statute
            and current_source_statute != incoming_source_statute
        ):
            bucket.ambiguous = True
            bucket.inactive = False
            bucket.selected_version = None
            bucket.selected_is_native = False
            continue
        if incoming_rank > current_rank:
            bucket.selected_version = version
            bucket.selected_is_native = is_native
            bucket.inactive = False
            continue
        if (
            incoming_rank == current_rank
            and current_version != version
            and current_source_statute != incoming_source_statute
        ):
            bucket.ambiguous = True
            bucket.inactive = False
            bucket.selected_version = None
            bucket.selected_is_native = False

    active = {}
    active_versions = {}
    ambiguous_addresses: list[LegalAddress] = []
    for address, bucket in sorted(projected.items(), key=lambda item: item[0].path):
        if bucket.ambiguous:
            active[address] = None
            ambiguous_addresses.append(address)
            continue
        if bucket.selected_version is not None:
            active[address] = clean_timeline_content(bucket.selected_version.content)
            active_versions[address] = bucket.selected_version
            continue
        if bucket.inactive:
            active[address] = None
    for event in migration_events:
        if as_of and event.effective and event.effective > as_of:
            continue
        if event.from_address != event.to_address and event.from_address not in active_versions:
            suppressed_source_addresses.add(event.from_address)
    for address in sorted(suppressed_source_addresses, key=lambda item: item.path):
        if address not in active_versions:
            active[address] = None
    return active, active_versions, tuple(ambiguous_addresses)
