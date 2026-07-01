"""Independent observed-vs-declared audit for landed write receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lawvm.core.ir import IRNode
from lawvm.core.mutation_boundary import (
    TreePath,
    TreePaths,
    dedupe_tree_paths,
    diff_ir_paths_identity_pruned,
    paths_related,
    validate_tree_path,
)
from lawvm.core.write_receipt import WriteReceipt

ObservedWriteAuditStatus = Literal["clean", "qualified", "violation"]


@dataclass(frozen=True, slots=True)
class ObservedWriteAudit:
    """Compare actual before/after tree changes against one WriteReceipt.

    This is the independent side of the apply receipt contract: the receipt is
    what the helper claims it wrote; the audit reads the tree diff and checks
    whether that claim covers the observed mutation footprint.
    """

    op_id: str
    observed_changed_paths: TreePaths
    receipt_declared_paths: TreePaths
    undeclared_paths: TreePaths
    unobserved_declared_paths: TreePaths
    audit_status: ObservedWriteAuditStatus
    matched_rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_paths(self.observed_changed_paths, field_name="observed changed path")
        _validate_paths(self.receipt_declared_paths, field_name="receipt declared path")
        _validate_paths(self.undeclared_paths, field_name="undeclared path")
        _validate_paths(self.unobserved_declared_paths, field_name="unobserved declared path")
        if self.audit_status not in {"clean", "qualified", "violation"}:
            raise ValueError("ObservedWriteAudit.audit_status must be clean, qualified, or violation")
        if self.audit_status == "qualified" and not self.matched_rule_ids:
            raise ValueError("ObservedWriteAudit.qualified requires matched_rule_ids")


def build_observed_write_audit(
    before: IRNode,
    after: IRNode,
    receipt: WriteReceipt,
    *,
    observed_paths: TreePaths | None = None,
) -> ObservedWriteAudit:
    """Build a passive receipt audit from an actual before/after IR diff.

    ``observed_paths`` lets a caller reuse a diff already computed for this
    before/after pair while preserving the same audit checks.
    """

    observed = (
        observed_paths
        if observed_paths is not None
        else diff_ir_paths_identity_pruned(before, after)
    )
    declared = receipt.declared_footprint
    undeclared = _unrelated_observed_paths(observed, declared)
    unobserved_declared = _unrelated_declared_paths(declared, observed)
    exact_clean = dedupe_tree_paths(observed) == dedupe_tree_paths(declared)
    if exact_clean:
        audit_status: ObservedWriteAuditStatus = "clean"
        matched_rule_ids: tuple[str, ...] = ()
    elif not undeclared and not unobserved_declared and receipt.named_rule_ids:
        audit_status = "qualified"
        matched_rule_ids = receipt.named_rule_ids
    else:
        audit_status = "violation"
        matched_rule_ids = ()
    return ObservedWriteAudit(
        op_id=receipt.op_id,
        observed_changed_paths=observed,
        receipt_declared_paths=declared,
        undeclared_paths=undeclared,
        unobserved_declared_paths=unobserved_declared,
        audit_status=audit_status,
        matched_rule_ids=matched_rule_ids,
    )


def _unrelated_observed_paths(observed: TreePaths, declared: TreePaths) -> TreePaths:
    return tuple(path for path in observed if not _path_related_to_any(path, declared))


def _unrelated_declared_paths(declared: TreePaths, observed: TreePaths) -> TreePaths:
    return tuple(path for path in declared if not _path_related_to_any(path, observed))


def _path_related_to_any(path: TreePath, candidates: TreePaths) -> bool:
    return any(paths_related(path, candidate) for candidate in candidates)


def _validate_paths(paths: TreePaths, *, field_name: str) -> None:
    issues = tuple(
        issue
        for path in paths
        for issue in validate_tree_path(path, field_name=field_name)
    )
    if issues:
        raise ValueError("; ".join(issues))
