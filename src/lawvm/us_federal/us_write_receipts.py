"""Per-op :class:`WriteReceipt` emission for the US federal dry-run kernel.

US federal is the "algorithmic frontier": its replay is a witness-anchored,
TEXT-LEVEL dry-run (``us_federal/dry_run.py``), NOT a structural IR-tree fold
like NO/SE/UK. There is no per-op before/after ``IRNode`` pair — the composition
loop applies one :class:`LegalOperation` at a time to a section's *text*
(``running`` -> ``materialized`` strings, via :func:`_materialize_one`) and
compares the composed result against the OLRC oracle once per section.

The per-op apply seam therefore exists (one ``LegalOperation`` per
``_materialize_one`` call), but at SECTION-TEXT granularity, not subtree
granularity. This module emits a faithful :class:`WriteReceipt` at that seam and
DOCUMENTS the limitation in the receipt itself:

  * ``bound_target_path`` / ``landed_primary_path`` are the op's declared target
    address coerced to the core ``TreePath`` shape (a real address).
  * ``pre_hashes`` / ``post_hashes`` are the canonical
    :func:`structural_subtree_hash` of a SYNTHETIC single ``IRNode`` wrapping the
    section's before/after TEXT (``us_section_text`` kind). This keeps the hash
    comparable and contract-shaped, while signalling — via the synthetic
    ``us_section_text`` kind — that US's granularity is section-text, not the
    subtree granularity NO/SE/UK record.

Grounding-neutrality (the §2.7 byte-stable-bench invariant): receipts are
produced ONLY when a caller passes a ``write_receipts_out`` sink to
:func:`build_us_dry_run`. With the sink absent (the default), the dry-run is
byte-identical — the report rows, refusals, coverage, and boundary proof are
unchanged. The receipt is additive evidence; it never feeds the dry-run gate
(``replay_authorized`` stays ``False`` always).
"""

from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.mutation_boundary import TreePath, TreePaths
from lawvm.core.semantic_types import IRNodeKind, legacy_text_action_value
from lawvm.core.write_receipt import WriteReceipt, receipt_address_string

#: IRNode kind used for the synthetic section-text wrapper whose
#: :func:`structural_subtree_hash` becomes the receipt's pre/post hash. US's
#: dry-run carries section TEXT (a string), not a subtree, at the per-op seam,
#: so the wrapper is a single ``SECTION`` node holding that text — the receipt's
#: section-TEXT granularity (the algorithmic-frontier limitation, vs the subtree
#: granularity NO/SE/UK record) is documented on the ``helper`` field
#: (``us_dry_run.materialize_one::...``) rather than via a novel node kind, since
#: ``IRNode.kind`` is the typed :class:`IRNodeKind` enum.
US_SECTION_TEXT_KIND = IRNodeKind.SECTION

#: Named migration rule id owning the bound→landed divergence on a US RENUMBER
#: (redesignation) op. Registered in ``tools/spec_ledger_us_catalog.py``; mirrors
#: NO's ``no_section_renumber_relabel`` and SE's ``se_renumber_relabel``.
US_SECTION_REDESIGNATE_RELABEL_RULE_ID = "us_section_redesignate_relabel"


def _us_legal_path_to_tree_path(addr: LegalAddress) -> TreePath:
    """Coerce a :class:`LegalAddress` path into the core ``TreePath`` shape."""
    return tuple((str(kind), str(label or "")) for kind, label in addr.path)


def _section_text_hash(text: str) -> str:
    """Canonical structural hash of a section's text via a synthetic wrapper node.

    US's dry-run carries section TEXT (a string), not a subtree, at the per-op
    seam. Wrapping it in a single :class:`IRNode` keeps the receipt hash on the
    frozen :func:`structural_subtree_hash` recipe (so consumers compare it like
    any other receipt hash) while the ``us_section_text`` kind documents the
    granularity limitation. An empty string hashes to ``""`` (an absent
    subtree), matching the receipt contract's "" convention.
    """
    if not text:
        return ""
    return structural_subtree_hash(IRNode(kind=US_SECTION_TEXT_KIND, label="", text=text))


def emit_us_op_receipt(
    op: LegalOperation,
    *,
    before_text: str,
    after_text: str,
) -> WriteReceipt:
    """Emit a section-text-granularity :class:`WriteReceipt` for one applied US op.

    ``before_text`` is the section's running text BEFORE this op materialized;
    ``after_text`` is the text :func:`_materialize_one` produced. The mutation
    footprint is categorized by the op's action at the declared target address
    (US dry-run does not claim cross-address migrations except RENUMBER).
    """
    action_value = legacy_text_action_value(op)
    leaf_kind = op.target.leaf_kind() or "section"
    helper = f"us_dry_run.materialize_one::{action_value}::{leaf_kind}"
    bound_target_path: TreePath = _us_legal_path_to_tree_path(op.target)

    created_paths: TreePaths = ()
    replaced_paths: TreePaths = ()
    removed_paths: TreePaths = ()
    renumbered_paths: tuple[tuple[TreePath, TreePath], ...] = ()
    migration_rule_ids: tuple[str, ...] = ()

    if action_value in {"replace", "text_replace", "heading_replace"}:
        landed_primary_path: TreePath | None = bound_target_path or None
        replaced_paths = (bound_target_path,) if bound_target_path else ()
    elif action_value == "insert":
        landed_primary_path = bound_target_path or None
        created_paths = (bound_target_path,) if bound_target_path else ()
    elif action_value in {"repeal", "text_repeal"}:
        landed_primary_path = bound_target_path or None
        removed_paths = (bound_target_path,) if bound_target_path else ()
    elif action_value == "renumber":
        destination_path = (
            _us_legal_path_to_tree_path(op.destination) if op.destination is not None else None
        )
        landed_primary_path = destination_path or bound_target_path or None
        if destination_path is not None:
            renumbered_paths = ((bound_target_path, destination_path),)
            migration_rule_ids = (US_SECTION_REDESIGNATE_RELABEL_RULE_ID,)
    else:
        landed_primary_path = bound_target_path or None

    pre_hashes: dict[str, str] = {}
    post_hashes: dict[str, str] = {}
    if landed_primary_path:
        key = receipt_address_string(landed_primary_path)
        pre_hashes[key] = _section_text_hash(before_text)
        post_hashes[key] = _section_text_hash(after_text)

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
