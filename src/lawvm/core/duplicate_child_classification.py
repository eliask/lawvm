"""Classify duplicate same-kind/same-label child families without mutating them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.provenance import MigrationEvent

DuplicateChildClassification = Literal[
    "valid_temporal_overlay",
    "migrated_native_identity_collision",
    "carried_tail_or_preserved_live_content",
    "stale_publisher_or_source_shadow",
    "unresolved_duplicate",
]

_VALID_DUPLICATE_CHILD_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        "valid_temporal_overlay",
        "migrated_native_identity_collision",
        "carried_tail_or_preserved_live_content",
        "stale_publisher_or_source_shadow",
        "unresolved_duplicate",
    }
)


@dataclass(frozen=True)
class DuplicateChildFinding:
    """One duplicate direct-child family under a legal parent."""

    parent_address: LegalAddress
    child_kind: str
    child_label: str
    child_count: int
    classification: DuplicateChildClassification
    reason: str

    def __post_init__(self) -> None:
        if not str(self.child_kind or "").strip():
            raise ValueError("DuplicateChildFinding.child_kind must be non-empty")
        if not str(self.child_label or "").strip():
            raise ValueError("DuplicateChildFinding.child_label must be non-empty")
        if self.child_count < 2:
            raise ValueError(
                f"DuplicateChildFinding.child_count must be >= 2; got {self.child_count}"
            )
        if self.classification not in _VALID_DUPLICATE_CHILD_CLASSIFICATIONS:
            raise ValueError(
                f"DuplicateChildFinding.classification must be one of "
                f"{sorted(_VALID_DUPLICATE_CHILD_CLASSIFICATIONS)}; "
                f"got {self.classification!r}"
            )
        if not str(self.reason or "").strip():
            raise ValueError("DuplicateChildFinding.reason must be non-empty")

    @property
    def child_address(self) -> LegalAddress:
        return LegalAddress(path=self.parent_address.path + ((self.child_kind, self.child_label),))


def timeline_issue_kind_for_duplicate_classification(classification: DuplicateChildClassification) -> str:
    """Map a duplicate classification to the public timeline issue vocabulary."""
    _map: dict[str, str] = {
        "valid_temporal_overlay": "duplicate_same_label_child_valid_temporal_overlay",
        "migrated_native_identity_collision": "duplicate_same_label_child_migration_collision",
        "carried_tail_or_preserved_live_content": "duplicate_same_label_child_carried_continuity",
        "stale_publisher_or_source_shadow": "duplicate_same_label_child_stale_source_shadow",
        "unresolved_duplicate": "duplicate_same_label_child_unresolved",
    }
    if classification not in _map:
        raise ValueError(
            f"unknown DuplicateChildClassification {classification!r}; "
            f"supported: {sorted(_VALID_DUPLICATE_CHILD_CLASSIFICATIONS)}"
        )
    return _map[classification]


def classify_duplicate_child_family(
    parent_address: LegalAddress,
    children: Sequence[IRNode],
    *,
    classification_hint: DuplicateChildClassification | None = None,
    migration_events: tuple[MigrationEvent, ...] = (),
) -> DuplicateChildFinding | None:
    """Classify one already-grouped same-kind/same-label child family.

    This function deliberately does not infer deletion authority from text
    equality. It only classifies the duplicate family for diagnostics. Replay or
    materialization callers may pass a hint when their phase-local context owns
    the reason duplicate content is being preserved.
    """
    if len(children) < 2:
        return None
    first = children[0]
    if first.label is None:
        return None
    child_kind = _kind_str(first.kind)
    child_label = first.label
    child_address = LegalAddress(path=parent_address.path + ((child_kind, child_label),))
    classification = classification_hint
    reason = ""

    if classification is None and any(
        event.to_address == child_address and event.from_address != event.to_address
        for event in migration_events
    ):
        classification = "migrated_native_identity_collision"
        reason = "a migration event projects another legal identity onto this visible child address"

    if classification is None and any(
        _node_marks_valid_temporal_overlay(child) for child in children
    ):
        classification = "valid_temporal_overlay"
        reason = "at least one child is explicitly marked as a temporal overlay"

    if classification is None and any(
        _node_marks_source_shadow(child) for child in children
    ):
        classification = "stale_publisher_or_source_shadow"
        reason = "at least one child carries an explicit source-shadow marker"

    if classification is None:
        classification = "unresolved_duplicate"
        reason = "no deletion, migration, temporal-overlay, or source-shadow authority proved"
    elif not reason:
        reason = _reason_for_hint(classification)

    return DuplicateChildFinding(
        parent_address=parent_address,
        child_kind=child_kind,
        child_label=child_label,
        child_count=len(children),
        classification=classification,
        reason=reason,
    )


def collect_duplicate_child_findings(
    node: IRNode,
    *,
    parent_address: LegalAddress,
    classification_hint: DuplicateChildClassification | None = None,
    migration_events: tuple[MigrationEvent, ...] = (),
) -> tuple[DuplicateChildFinding, ...]:
    """Return classified duplicate same-kind/same-label families below ``node``."""
    findings: list[DuplicateChildFinding] = []
    grouped: dict[tuple[str, str], list[IRNode]] = {}
    for child in node.children:
        if child.label is None:
            continue
        grouped.setdefault((_kind_str(child.kind), child.label), []).append(child)
    for children in grouped.values():
        finding = classify_duplicate_child_family(
            parent_address,
            children,
            classification_hint=classification_hint,
            migration_events=migration_events,
        )
        if finding is not None:
            findings.append(finding)
    for child in node.children:
        if child.label is None:
            continue
        findings.extend(
            collect_duplicate_child_findings(
                child,
                parent_address=LegalAddress(path=parent_address.path + ((_kind_str(child.kind), child.label),)),
                classification_hint=None,
                migration_events=migration_events,
            )
        )
    return tuple(findings)


def _node_marks_valid_temporal_overlay(node: IRNode) -> bool:
    return node.attrs.get("lawvm_temporal_overlay") == "1"


def _node_marks_source_shadow(node: IRNode) -> bool:
    shadow_keys = {
        "lawvm_source_shadow",
        "lawvm_original_version_shadow",
        "finlex_original_version_shadow",
    }
    return any(str(node.attrs.get(key) or "") == "1" for key in shadow_keys)


def _reason_for_hint(classification: DuplicateChildClassification) -> str:
    if classification == "carried_tail_or_preserved_live_content":
        return "materialization preserved duplicate-bearing content instead of applying an ambiguous child overlay"
    if classification == "valid_temporal_overlay":
        return "phase-local caller identified a valid temporal overlay"
    if classification == "migrated_native_identity_collision":
        return "phase-local caller identified a migrated/native identity collision"
    if classification == "stale_publisher_or_source_shadow":
        return "phase-local caller identified a publisher/source shadow"
    return "phase-local caller could not prove a more specific duplicate family"
