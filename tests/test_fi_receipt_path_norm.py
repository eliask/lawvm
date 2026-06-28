"""Tests for ``lawvm.finland._receipt_path_norm`` (Wave N3a PR1).

PR1 of ``BOUND_TARGET_PATH_NORMALIZATION_DESIGN`` introduces two pure-form
canonicalizations on the op-level ``WriteReceipt``'s bound/landed paths so
the receipt's tuple-equality ``divergence_explained`` check compares
canonical-form paths:

  1. Wrapper-strip — drop a single leading ``("hcontainer", "")`` body-root
     wrapper step (corollary of the FI replay IR's rooted-under-hcontainer
     shape).
  2. Kind-alias rewrite — convert legal-address-vocabulary ``item`` /
     ``subitem`` (kohta / alakohta) to the IR-kind vocabulary
     ``paragraph`` / ``subparagraph`` the diff emits.

These pin:

* the alias map mirrors ``payload_realization_audit._TARGET_NODE_KINDS``
  (single source of truth — the rule-of-three extraction host);
* the helpers behave on synthetic unit inputs (wrapper-strip path, kind
  rewrite);
* the production-path fire-drill: a synthesized FI op with a paragraph:7
  kohta target driven through ``apply_resolved_op._collect_op_write_receipt``
  lands a receipt whose ``bound_target_path`` is populated non-None AND is
  reconciled to the IR-vocabulary ``paragraph:7`` form so the divergence check
  does not flag a false positive (AGENTS.md §2.9 production-liveness);
* the negative: a synthesized op whose bound truly diverges from the landed
  path (an out-of-scope target rewrite — bound=section:5 but the diff lands on
  section:1's paragraph:7) still carries ``divergence_explained is False`` —
  the normalization does not mask real divergences.

Scope (per ``BOUND_TARGET_PATH_NORMALIZATION_DESIGN`` §3 PR1): the 29
Pattern-C kind-label-mismatch cases clear; the 71+15 Pattern-A/B prefix-count
cases remain surfaced as false-positives pending PR2's prefix-equivalence
rule. The fire-drills below exercise only Pattern-C and the negative
unrelated-divergence case.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Any, List, cast

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland._receipt_path_norm import (
    _FI_KIND_ALIAS_TO_IR,
    _normalize_receipt_path_for_comparison,
    _reconcile_kind_alias,
    _strip_wrapper_root,
)
from lawvm.finland.apply_resolved_op import (
    ApplyResolvedOpSinks,
    _collect_op_write_receipt,
)
from lawvm.finland.ops import AmendmentOp, ResolvedOp
from lawvm.finland.payload_realization_audit import _TARGET_NODE_KINDS
from lawvm.finland.statute import ReplayState


# ---------------------------------------------------------------------------
# helpers — IR fixtures
# ---------------------------------------------------------------------------


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _para(label: str, text: str) -> IRNode:
    """A kohta: an IRNodeKind.PARAGRAPH node carrying a content child."""
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(_content(text),),
    )


def _sub(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


def _sec(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _chap(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(children))


def _hcontainer(*children: IRNode) -> IRNode:
    """An unlabeled body-root wrapper (mirrors replay_products._ensure_body_hcontainer)."""
    return IRNode(kind=IRNodeKind.HCONTAINER, label="", children=tuple(children))


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _resolved_op_with_bound(op_id: str, bound_path: tuple[tuple[str, str], ...]) -> ResolvedOp:
    """Construct a ResolvedOp whose resolved_target_address is forced to ``bound_path``.

    The AmendmentOp + from_amendment_op factory produces a section-level
    ResolvedOp; we then override the private ``_target_address_override`` field
    (the same field ``_rebind_resolved_target_address`` mutates) so the
    production receipt collector sees the synthesized bound target.
    """
    op = AmendmentOp(
        op_id=op_id,
        op_type=cast("Any", "REPLACE"),
        target_section="1",
        target_unit_kind="section",
        source_statute="2020/1",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )
    return dc_replace(rop, _target_address_override=LegalAddress(path=bound_path))


# ---------------------------------------------------------------------------
# 1. alias map — single source of truth mirror of _TARGET_NODE_KINDS
# ---------------------------------------------------------------------------


def test_alias_map_mirrors_target_node_kinds() -> None:
    """The alias map mirrors ``payload_realization_audit._TARGET_NODE_KINDS``.

    Single source of truth: the rule-of-three extraction host. PR1 lands this
    fact in ``_receipt_path_norm``; ``payload_realization_audit`` imports from
    here (not the other way around) so the alias is owned in exactly one place
    for the three consumers — payload-realization audit, receipt-construction,
    and the future PR2 prefix-equivalence helper.
    """
    # Alias structure — kohta and alakohta have multi-kind / single-alias sets.
    assert _FI_KIND_ALIAS_TO_IR["item"] == frozenset({IRNodeKind.ITEM, IRNodeKind.PARAGRAPH})
    assert _FI_KIND_ALIAS_TO_IR["subitem"] == frozenset({IRNodeKind.SUBPARAGRAPH})
    # Simple kinds map to themselves only (the legal-address name matches the
    # IR kind name).
    assert _FI_KIND_ALIAS_TO_IR["part"] == frozenset({IRNodeKind.PART})
    assert _FI_KIND_ALIAS_TO_IR["chapter"] == frozenset({IRNodeKind.CHAPTER})
    assert _FI_KIND_ALIAS_TO_IR["section"] == frozenset({IRNodeKind.SECTION})
    assert _FI_KIND_ALIAS_TO_IR["subsection"] == frozenset({IRNodeKind.SUBSECTION})
    # The payload_realization_audit consumer sees the SAME object (no copy).
    assert _TARGET_NODE_KINDS is _FI_KIND_ALIAS_TO_IR


def test_reconcile_kind_alias_returns_equivalence_set() -> None:
    """_reconcile_kind_alias returns the equivalence frozenset for a kind."""
    assert _reconcile_kind_alias("item") == frozenset({IRNodeKind.ITEM, IRNodeKind.PARAGRAPH})
    assert _reconcile_kind_alias("subitem") == frozenset({IRNodeKind.SUBPARAGRAPH})
    assert _reconcile_kind_alias("section") == frozenset({IRNodeKind.SECTION})
    # Kinds not in the alias map (already IR-vocabulary): empty frozenset.
    assert _reconcile_kind_alias("paragraph") == frozenset()
    assert _reconcile_kind_alias("hcontainer") == frozenset()


# ---------------------------------------------------------------------------
# 2. wrapper-strip — drop leading ("hcontainer", "")
# ---------------------------------------------------------------------------


def test_strip_wrapper_root_drops_unlabeled_body_root() -> None:
    """A single leading ("hcontainer", "") step is dropped (mirrors core)."""
    path = (("hcontainer", ""), ("chapter", "3"), ("section", "1"))
    assert _strip_wrapper_root(path) == (("chapter", "3"), ("section", "1"))


def test_strip_wrapper_root_preserves_named_hcontainer() -> None:
    """A NAMED hcontainer step (e.g. provisions-wrapper) is preserved unchanged.

    Only the unlabeled body-root wrapper is stripped; legitimate hcontainer
    address steps the FI IR encodes elsewhere stay intact.
    """
    path = (("hcontainer", "statuteProvisionsWrapper"), ("section", "1"))
    assert _strip_wrapper_root(path) == path


def test_strip_wrapper_root_noop_when_absent() -> None:
    """A path with no leading hcontainer wrapper step is returned unchanged."""
    path = (("chapter", "3"), ("section", "1"))
    assert _strip_wrapper_root(path) == path


def test_normalize_path_for_comparison_combines_strip_and_kind_alias() -> None:
    """The full canonicalization: wrapper-strip + kind-alias rewrite together.

    The bound path (from the rop's legal-address vocabulary) is rewritten into
    the IR-kind vocabulary so the tuple-equality divergence check matches the
    IR diff's landed path.

    Mirrors Pattern C witness: bound ``item:7`` reconciles to landed
    ``paragraph:7`` once the wrapper strip + kind-alias canonicalization
    is applied.
    """
    bound_path = (
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("item", "7"),
    )
    landed_path = (
        ("hcontainer", ""),
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "7"),
    )
    # Both sides normalize to the same canonical form.
    assert _normalize_receipt_path_for_comparison(bound_path) == (
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "7"),
    )
    assert _normalize_receipt_path_for_comparison(landed_path) == (
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "7"),
    )
    # And they match each other — Pattern C cleared.
    assert _normalize_receipt_path_for_comparison(bound_path) == (
        _normalize_receipt_path_for_comparison(landed_path)
    )


# ---------------------------------------------------------------------------
# 3. §2.9 fire-drill — production-liveness through _collect_op_write_receipt
# ---------------------------------------------------------------------------


def _tree_with_para7(body_kind: str) -> tuple[IRNode, IRNode]:
    """Construct a before/after IR pair whose diff lands on paragraph:7.

    The before tree carries paragraph:7 with content "old"; the after tree
    changes paragraph:7's attrs only (a node-level change so the diff reports
    the change AT the paragraph:7 path, not deeper at content). The body root
    is an hcontainer wrapper matching the production replay-IR shape (mirrors
    ``replay_products._ensure_body_hcontainer``).
    """
    content = _content("body of seven")
    before_para = IRNode(
        kind=IRNodeKind.PARAGRAPH, label="7", children=(content,)
    )
    after_para = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="7",
        children=(content,),
        attrs={"amended": "1"},
    )
    before_sub = _sub("1", before_para)
    after_sub = _sub("1", after_para)
    before_sec = _sec("1", before_sub)
    after_sec = _sec("1", after_sub)
    before_chap = _chap("3", before_sec)
    after_chap = _chap("3", after_sec)
    if body_kind == "hcontainer":
        before = _body(_hcontainer(before_chap))
        after = _body(_hcontainer(after_chap))
    else:
        before = _body(before_chap)
        after = _body(after_chap)
    return before, after


def test_fire_drill_bound_target_path_threaded_and_canonical_under_kind_alias() -> None:
    """§2.9 fire-drill: a synthesized FI op with item-targeted kohta payload
    driven through the production apply path lands a receipt whose
    ``bound_target_path`` is populated non-None AND is reconciled to the IR's
    canonical ``paragraph:7`` form so the divergence check does not trip.

    The op's resolved_target_address.path uses the legal-address vocabulary
    ``item:7``; the IR's diff path uses the IR-kind vocabulary
    ``paragraph:7`` (per ``apply_ir_ops._relabel_item_ir``). Without the
    kind-alias canonicalization the receipt's bound != landed would
    un-authorize the aggregate. Pattern C clears.

    Mirrors the §2.9 guard-liveness test plan from
    ``BOUND_TARGET_PATH_NORMALIZATION_DESIGN`` §5.1 FD-PR1-2: the guard
    (canonicalization) fires through the FULL production lane, not just a
    unit test of the helper.
    """
    before, after = _tree_with_para7("hcontainer")
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    bound_path = (
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("item", "7"),
    )
    rop = _resolved_op_with_bound("kind_alias_op", bound_path)
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=None,
        source_statute="2020/1",
        sinks=sinks,
    )

    # Production-liveness: the receipt is produced and carries a non-None
    # bound_target_path (was None before the PR1 threading).
    assert len(sinks.write_receipts_out) == 1
    receipt = sinks.write_receipts_out[0]
    assert receipt.bound_target_path is not None
    # The bound path is in CANONICAL IR-form (``paragraph:7``, not ``item:7``).
    assert receipt.bound_target_path == (
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "7"),
    )
    # The landed path on the receipt is normalized too (wrapper-stripped —
    # no leading ("hcontainer", "")):
    assert receipt.landed_primary_path is not None
    assert receipt.landed_primary_path == (
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "7"),
    )
    # And — the crux — the divergence is explained (Pattern C cleared):
    # no false-positive unexplained-divergence residual fires from the
    # receipt-boundary arm.
    assert receipt.divergence_explained is True


def test_fire_drill_wrapper_strip_alone_without_kind_alias() -> None:
    """§2.9 fire-drill: when the bound path carries only the wrapper-root
    prefix (no kind-alias mismatch), the wrapper-strip canonicalization
    alone aligns the bound with the landed path.

    Mirrors FD-PR1-1 from ``BOUND_TARGET_PATH_NORMALIZATION_DESIGN`` §5.1: a
    pathological rop whose resolved_target_address.path carries a leading
    ``("hcontainer", "")`` prefix (the wrapper step normally only present on
    the IR-diff side). Drives the production apply path.
    """
    before, after = _tree_with_para7("hcontainer")
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    bound_path = (
        ("hcontainer", ""),
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "7"),
    )
    rop = _resolved_op_with_bound("wrapper_op", bound_path)
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=None,
        source_statute="2020/1",
        sinks=sinks,
    )

    assert len(sinks.write_receipts_out) == 1
    receipt = sinks.write_receipts_out[0]
    assert receipt.bound_target_path is not None
    # Both sides normalize to the same canonical form — wrapper-stripped on
    # both sides; no leading ("hcontainer", "") in either.
    assert receipt.bound_target_path == (
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "7"),
    )
    assert receipt.landed_primary_path == receipt.bound_target_path
    assert receipt.divergence_explained is True


# ---------------------------------------------------------------------------
# 4. negative — a true bound→landed divergence is NOT masked
# ---------------------------------------------------------------------------


def test_negative_true_bound_to_landed_divergence_still_unexplained() -> None:
    """A real divergence (bound=section:5 but landed=section:1's paragraph:7)
    still emits an UNEXPLAINED divergence — the normalization does not mask
    real divergences.

    The op's target claimed section:5; the apply landed on section:1's
    paragraph:7 (an out-of-scope rewrite). No named rule explains this. The
    receipt's ``divergence_explained`` MUST stay False so the strict-mode arm
    refuses authorization — confirming the canonicalization doesn't widen the
    boundary.
    """
    before, after = _tree_with_para7("hcontainer")
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    # Bound to section:5 (a section NOT touched by the mutation — the diff
    # lands on section:1's paragraph:7).
    bound_path = (("chapter", "3"), ("section", "5"))
    rop = _resolved_op_with_bound("divergent_op", bound_path)
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=None,
        source_statute="2020/1",
        sinks=sinks,
    )

    assert len(sinks.write_receipts_out) == 1
    receipt = sinks.write_receipts_out[0]
    assert receipt.bound_target_path is not None
    # The bound path is rewritten via canonical-form, but it does NOT match the
    # landed path (which is at chapter:3/section:1/subsection:1/paragraph:7).
    assert receipt.bound_target_path == (("chapter", "3"), ("section", "5"))
    assert receipt.landed_primary_path == (
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "7"),
    )
    # And — the crux — the divergence is UNEXPLAINED: the receipt's
    # divergence_explained property returns False. No false-positive masking.
    assert receipt.divergence_explained is False


# ---------------------------------------------------------------------------
# 5. regression — op with no resolved_target_address still threads None
# ---------------------------------------------------------------------------


def test_collect_op_write_receipt_no_resolved_address_threads_none() -> None:
    """When the rop has no resolved_target_address, bound_target_path stays
    None (the legacy production behavior). The canonicalization is a no-op
    on a None bound; the receipt's divergence check authorizes by absence.
    """
    before, after = _tree_with_para7("body")
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    # Use the standard AmendmentOp construction (no override) —
    # resolved_target_address is a section:1 path, NOT None here.
    rop = _resolved_op_with_bound(
        "plain_op",
        (("section", "1"),),
    )
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=None,
        source_statute="2020/1",
        sinks=sinks,
    )

    assert len(sinks.write_receipts_out) == 1
    receipt = sinks.write_receipts_out[0]
    # The bound is the canonical-form of section:1 — no hcontainer, no kind
    # rewrite needed (section already in IR vocabulary).
    assert receipt.bound_target_path == (("section", "1"),)


def test_no_leak_synthetic_markers_do_not_reach_user_output() -> None:
    """§2.9 clause 6: synthetic markers from these fire-drills (chapter:3 in
    the synthetic fixtures) do not leak into user-visible surfaces.

    The synthetic bound/landed paths use ``chapter:3`` / ``section:5`` /
    ``paragraph:7`` purely for the kind-alias + wrapper-strip test; they
    must not appear in any source_statute field, op_id-encoded statistics, or
    other persisted user-visible artifacts.
    """
    before, after = _tree_with_para7("hcontainer")
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    bound_path = (
        ("chapter", "3"),
        ("section", "1"),
        ("subsection", "1"),
        ("item", "7"),
    )
    rop = _resolved_op_with_bound("leak_check_op", bound_path)
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=None,
        source_statute="2020/1",
        sinks=sinks,
    )

    receipt = sinks.write_receipts_out[0]
    # The op_id is the bare "leak_check_op" identifier (no synthetic markers
    # embedded) and source_statute is the test source "2020/1".
    assert receipt.op_id == "leak_check_op"
    # The action is the rop's resolved_action_type ("replace" lowercased).
    assert receipt.action == "replace"
    # The synthetic markers are confined to the bound/landed paths which are
    # the explicit test subjects — none leak into the audit fields or the
    # findings ledger. The findings list stays empty (clean apply).
    assert findings == []
    # Helper identifier is the production FI helper (no synthetic markers).
    assert receipt.helper == "fi.apply.resolved_op_write"
