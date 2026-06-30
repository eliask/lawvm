"""Per-op :class:`WriteReceipt` emission for the UK replay executor.

This is the UK counterpart of ``norway/grafter.py::_no_emit_one_op_receipt``
(NO) and ``sweden/grafter.py::_se_emit_one_op_receipt`` (SE) — the second step
of the AGENTS.md §2.3 receipt contract. Given the before/after body IR trees
that bracket one applied :class:`LegalOperation`, it synthesizes a typed
:class:`WriteReceipt` from the *landed* tree diff and the op's declared target.

Grounding-neutrality (the §2.7 byte-stable-bench invariant): receipts are
additive evidence. The UK replay path produces them ONLY when a caller opts in
by passing a ``write_receipts_out`` collection sink to :func:`replay_uk_ops`
(mirroring the existing ``mutation_events_out`` / ``lo_ops_out`` debug streams).
With the sink absent (the default), ``apply_op`` is byte-identical to its
pre-instrumentation behaviour — no snapshot is taken, no diff is run. The
receipt is the producer-side record; the independent observed before/after diff
(``core.observed_write_audit``) is the load-bearing cross-check.

A RENUMBER op's bound→landed divergence (source label → destination label) is
the typed named migration ``uk_section_renumber_relabel`` (registered in
``tools/spec_ledger_uk_catalog.py``), mirroring SE's ``se_renumber_relabel`` and
NO's ``no_section_renumber_relabel``. Without that named rule the RENUMBER
receipt is a §1.6 unstated-migration violation strict mode must reject.
"""

from __future__ import annotations

from lawvm.core import tree_ops
from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.mutation_boundary import (
    TreePath,
    TreePaths,
    diff_ir_paths_identity_pruned,
)
from lawvm.core.write_receipt import WriteReceipt, receipt_address_string
from lawvm.uk_legislation.addressing import _action_name

#: Named migration rule id owning the bound→landed divergence on a UK RENUMBER
#: op (source label → destination label). Mirrors SE's ``se_renumber_relabel``
#: and NO's ``no_section_renumber_relabel`` (one id per family). Registered in
#: ``tools/spec_ledger_uk_catalog.py`` so the divergence audits as ``qualified``
#: (named-rule-explained), not a ``violation``.
UK_SECTION_RENUMBER_RELABEL_RULE_ID = "uk_section_renumber_relabel"


def _uk_legal_path_to_tree_path(addr: LegalAddress) -> TreePath:
    """Coerce a :class:`LegalAddress` path into the core ``TreePath`` shape.

    ``LegalAddress.path`` is a tuple of ``(kind, label)`` pairs; the core
    ``TreePath`` requires ``str`` labels (empty string for an absent label).
    Mirrors ``norway/grafter.py::_no_legal_path_to_tree_path``.
    """
    return tuple((str(kind), str(label or "")) for kind, label in addr.path)


def _resolve_with_recursive_fallback(body: IRNode, path: TreePath) -> IRNode | None:
    """Resolve ``path`` against ``body``, falling back to a recursive find.

    UK sections live nested under schedules/parts/cross-headings, so a single
    coordinate from ``op.target`` may not resolve by a strict top-down walk.
    Mirrors NO's single-segment ``tree_ops.find`` fallback in
    ``_no_emit_one_op_receipt`` (the production-lane case where the target lives
    deeper than ``body``'s direct children).
    """
    node = tree_ops.resolve(body, list(path))
    if node is None and len(path) == 1:
        kind, label = path[0]
        if label:
            find_path = tree_ops.find(body, str(kind), str(label))
            if find_path is not None:
                node = tree_ops.resolve(body, list(find_path))
    return node


def emit_uk_op_receipt(
    before_body: IRNode,
    after_body: IRNode,
    op: LegalOperation,
) -> WriteReceipt | None:
    """Emit a :class:`WriteReceipt` for one applied UK op, or ``None`` when skipped.

    Mirrors ``norway/grafter.py::_no_emit_one_op_receipt``. The receipt
    synthesizes the §2.3 contract fields from the actual before/after IR tree
    diff (core's identity-pruned diff) and the op's declared target. When the
    diff is empty the op was filtered/skipped (the replay path recorded an
    adjudication); no receipt is emitted — the adjudication carries the witness.

    The mutation footprint is categorized by the op's action:
    REPLACE/text-replace → ``replaced_paths``; INSERT → ``created_paths``;
    REPEAL/text-repeal → ``removed_paths``; RENUMBER →
    ``renumbered_paths`` ``(from, to)`` sourced from ``op.target`` and
    ``op.destination`` with ``migration_rule_ids=(uk_section_renumber_relabel,)``.

    Pre/post structural subtree hashes are taken at the landed primary path's
    covering region via :func:`structural_subtree_hash` (the canonical recipe
    from CERTIFIED_TREE_TRANSITION_TRACE_V0.md §2.2). For REPEAL the post hash
    is ``""`` (the hash of an absent subtree).
    """
    changed = diff_ir_paths_identity_pruned(before_body, after_body)
    if not changed:
        # Op filtered/skipped — the replay adjudication ledger carries the
        # witness instead; no receipt for a no-op apply.
        return None

    action_value = _action_name(op.action)
    leaf_kind = op.target.leaf_kind() or "unknown"
    helper = f"UKReplayExecutor.apply_op::{action_value}::{leaf_kind}"
    bound_target_path: TreePath = _uk_legal_path_to_tree_path(op.target)

    created_paths: TreePaths = ()
    replaced_paths: TreePaths = ()
    removed_paths: TreePaths = ()
    renumbered_paths: tuple[tuple[TreePath, TreePath], ...] = ()
    migration_rule_ids: tuple[str, ...] = ()

    # Landed primary path + footprint categorization. For INSERT/REPEAL/REPLACE
    # the targeted legal address IS the landed address (bound == landed
    # semantically; the deep ``changed[0]`` is a tree-nesting artifact, not a
    # semantic divergence). RENUMBER lands at the destination — bound != landed
    # by construction, explained by the named migration rule. Mirrors NO's
    # action-keyed landed/footprint resolution.
    if action_value in {"replace", "text_replace"}:
        landed_primary_path: TreePath | None = bound_target_path or None
        replaced_paths = changed
    elif action_value == "insert":
        landed_primary_path = bound_target_path or None
        created_paths = (bound_target_path,) if bound_target_path else ()
    elif action_value in {"repeal", "text_repeal"}:
        landed_primary_path = bound_target_path or None
        removed_paths = (bound_target_path,) if bound_target_path else ()
    elif action_value == "renumber":
        destination_path = (
            _uk_legal_path_to_tree_path(op.destination) if op.destination is not None else None
        )
        landed_primary_path = destination_path or None
        if destination_path is not None:
            renumbered_paths = ((bound_target_path, destination_path),)
            migration_rule_ids = (UK_SECTION_RENUMBER_RELABEL_RULE_ID,)
    else:
        landed_primary_path = changed[0] if changed else None

    # pre/post hashes at the covering region of the landed primary path.
    pre_hashes: dict[str, str] = {}
    post_hashes: dict[str, str] = {}
    if landed_primary_path:
        key = receipt_address_string(landed_primary_path)
        before_node = _resolve_with_recursive_fallback(before_body, landed_primary_path)
        after_node = _resolve_with_recursive_fallback(after_body, landed_primary_path)
        pre_hashes[key] = structural_subtree_hash(before_node) if before_node is not None else ""
        post_hashes[key] = structural_subtree_hash(after_node) if after_node is not None else ""

    return WriteReceipt(
        op_id=op.op_id or "",
        helper=helper,
        action=action_value,
        bound_target_path=bound_target_path,
        landed_primary_path=landed_primary_path,
        created_paths=created_paths,
        replaced_paths=replaced_paths,
        removed_paths=removed_paths,
        renumbered_paths=renumbered_paths,
        migration_rule_ids=migration_rule_ids,
        pre_hashes=pre_hashes,
        post_hashes=post_hashes,
    )
