"""Tests for ``lawvm.core.write_receipt``'s prefix-equivalence rule (Wave N3a PR2).

PR2 of ``BOUND_TARGET_PATH_NORMALIZATION_DESIGN`` introduces the
receipt-prefix-equivalence rule (``receipt_prefix_equivalence``, family
``presentation_cleanup``): a ``WriteReceipt``'s boundlanded divergence
reconciles as benign-by-relation-shape when one path is a strict prefix of
the other (either direction). The relation is OWNED — the receipt arm emits
a non-blocking ``APPLY.RECEIPT_BOUND_PREFIX_OF_LANDED`` observation row
carrying the bound/landed pair as the audit witness (per AGENTS.md §0:
an *invisible* heuristic is forbidden) — and DEFERS to the undeclared-touch
cross-check (``no_boundary_violation`` from
:func:`aggregate_replay_authority`) as the load-bearing independent witness.

These pin:

* the typed helper :func:`_paths_consistent_under_prefix`:
    - Pattern A — landed-is-prefix-of-bound (71 cases on green corpus).
    - Pattern B — bound-is-prefix-of-landed (15 cases on green corpus).
    - NEGATIVE — neither is a prefix-of; refuses (the helper returns False).
    - EXACT_MATCH — equal paths return False (handled by divergence_explained,
      NOT by the prefix helper).
    - ``normalize_fn`` parameter re-normalizes raw paths before the check.
* the ``WriteReceipt.divergence_kind`` field, set at the receipt-construction
  site by ``_classify_receipt_divergence_kind`` in ``apply_resolved_op``;
* the ``APPLY.RECEIPT_BOUND_PREFIX_OF_LANDED`` observation row, registered in
  :data:`FINDING_REGISTRY` and constructed by
  :func:`make_bound_prefix_observation`;
* ``_receipt_boundary_authorized`` — the typed fast path (PREFIX_OF_LANDED
  → authorize) AND the legacy defense-in-depth recomputation for
  pre-PR2 constructed receipts;
* ``aggregate_replay_authority`` — the prefix relation authorizes the
  receipt arm BUT a dirty cross-check (an undeclared-touch finding) still
  rejects the aggregate (T-i threading-only: the cross-check is the
  load-bearing independent witness at the aggregate level);
* §2.9 production-liveness fire-drills: drive both prefix cases through
  the FULL production apply path (``_collect_op_write_receipt``), asserting
  the observation surfaces in the findings ledger AND the receipt carries
  ``divergence_kind=PREFIX_OF_LANDED`` — not just a hand-constructed
  receipt.

Scope: PR2 (71 + 15 = 86 prefix-count false-positives drop to zero blocking
residuals on the green corpus ``1997/1339``). Pattern C (29 kind-label
mismatch cases) was cleared by PR1's kind-alias canonicalization and stays
at zero here.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Any, List, cast

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.observation_registry import (
    RECEIPT_BOUND_PREFIX_OF_LANDED_KIND,
    get_finding_spec,
    make_bound_prefix_observation,
)
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.write_receipt import DivergenceKind, WriteReceipt, _paths_consistent_under_prefix
from lawvm.finland.apply_replay_authorization import (
    APPLY_BOUNDARY_VIOLATION_FINDING_CODE,
    _receipt_boundary_authorized,
    aggregate_replay_authority,
)
from lawvm.finland.apply_resolved_op import (
    ApplyResolvedOpSinks,
    _collect_op_write_receipt,
)
from lawvm.finland.ops import AmendmentOp, ResolvedOp
from lawvm.finland.statute import ReplayState

# ---------------------------------------------------------------------------
# helpers — IR fixtures
# ---------------------------------------------------------------------------


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _para(label: str, text: str, *, attrs: dict[str, str] | None = None) -> IRNode:
    """A kohta: an IRNodeKind.PARAGRAPH node carrying a content child."""
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(_content(text),),
        attrs=attrs if attrs is not None else {},
    )


def _sub(label: str, *children: IRNode, attrs: dict[str, str] | None = None) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=label,
        children=tuple(children),
        attrs=attrs if attrs is not None else {},
    )


def _sec(label: str, *children: IRNode, attrs: dict[str, str] | None = None) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=tuple(children),
        attrs=attrs if attrs is not None else {},
    )


def _chap(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(children))


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _resolved_op_with_bound(op_id: str, bound_path: tuple[tuple[str, str], ...]) -> ResolvedOp:
    """Construct a ResolvedOp whose resolved_target_address is forced to ``bound_path``.

    Mirrors the helper in ``tests/test_fi_receipt_path_norm.py``: the
    AmendmentOp + from_amendment_op factory produces a section-level
    ResolvedOp; we override the private ``_target_address_override`` field
    so the production receipt collector sees the synthesized bound target
    (the same field ``_rebind_resolved_target_address`` mutates).
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
# 1. _paths_consistent_under_prefix — the typed helper
# ---------------------------------------------------------------------------


def test_paths_consistent_under_prefix_pattern_a_landed_is_prefix_of_bound() -> None:
    """Pattern A — landed is a strict prefix of bound (71 corpus cases).

    The op's bound path carries a descendant step (``subsection:1``) that the
    identity-pruned observed diff pruned away because the mutation only
    changed the parent's child shape (add/remove a subsection), not the
    descendant node itself. ``landed`` is a strict prefix of ``bound``.
    """
    bound = (("chapter", "4"), ("section", "5"), ("subsection", "1"))
    landed = (("chapter", "4"), ("section", "5"))
    assert _paths_consistent_under_prefix(bound, landed) is True


def test_paths_consistent_under_prefix_pattern_b_bound_is_prefix_of_landed() -> None:
    """Pattern B — bound is a strict prefix of landed (15 corpus cases).

    The bound address names the section root and the mutation touched a
    specific subsection; the diff descends to the subsection level, so
    ``landed`` is deeper than ``bound``. ``bound`` is a strict prefix of
    ``landed``.
    """
    bound = (("chapter", "4"), ("section", "5"))
    landed = (("chapter", "4"), ("section", "5"), ("subsection", "1"), ("item", "4"))
    assert _paths_consistent_under_prefix(bound, landed) is True


def test_paths_consistent_under_prefix_negative_true_divergence() -> None:
    """Negative — neither is a prefix of the other (true divergence).

    ``bound=chapter:4/section:5`` vs ``landed=chapter:4/section:7/subsection:1``:
    same root but diverge at index 1 (section:5 != section:7), so neither
    is a strict prefix of the other. The helper MUST return False so the
    receipt arm refuses authorization (the divergence is unexplained).
    """
    bound = (("chapter", "4"), ("section", "5"))
    landed = (("chapter", "4"), ("section", "7"), ("subsection", "1"))
    assert _paths_consistent_under_prefix(bound, landed) is False


def test_paths_consistent_under_prefix_equal_paths_return_false() -> None:
    """Equal paths return False — handled by EXACT_MATCH, NOT prefix-of.

    The receipt-boundary arm's existing ``bound == landed`` short-circuit
    classifies the exact-match case; the prefix helper classifies STRICT
    prefix-of only (a deliberate separation so ``divergence_kind`` does
    not double-count an exact match as a prefix relation).
    """
    bound = (("chapter", "4"), ("section", "5"))
    landed = (("chapter", "4"), ("section", "5"))
    assert _paths_consistent_under_prefix(bound, landed) is False


def test_paths_consistent_under_prefix_normalize_fn_applied_to_raw_paths() -> None:
    """The ``normalize_fn`` parameter lets the helper accept raw paths.

    When the caller has NOT pre-canonicalized the paths (e.g. a unit test
    constructing raw bound/landed formats), passing the canonicalization
    callable lets the helper normalize both sides BEFORE the prefix check
    — so the helper is reusable from contexts that don't share PR1's
    canonical pipeline.
    """

    def upper_kind(path: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        return tuple((kind.upper(), label) for kind, label in path)

    raw_bound = (("chapter", "4"), ("section", "5"), ("SUBSECTION", "1"))
    raw_landed = (("CHAPTER", "4"), ("SECTION", "5"))
    # Without normalize_fn: raw paths are unequal-as-is and the prefix match
    # fails because the kind labels differ in case.
    assert _paths_consistent_under_prefix(raw_bound, raw_landed) is False
    # With normalize_fn: both sides are uppercased, then bound is a strict
    # prefix of landed (the canonical-form prefix relation holds).
    assert _paths_consistent_under_prefix(raw_bound, raw_landed, normalize_fn=upper_kind) is True


# ---------------------------------------------------------------------------
# 2. observation registry entry + construction helper
# ---------------------------------------------------------------------------


def test_finding_registry_entry_for_receipt_bound_prefix() -> None:
    """The APPLY.RECEIPT_BOUND_PREFIX_OF_LANDED FindingSpec is registered."""
    spec = get_finding_spec(RECEIPT_BOUND_PREFIX_OF_LANDED_KIND)
    assert spec is not None
    assert spec.role == "observation"
    # Non-blocking by registry contract — validate_finding_projection rejects
    # a blocking observation; the §0 prime directive is satisfied by the
    # observation row existing (the audit witness), not by blocking strict
    # mode (the undeclared-touch cross-check is the load-bearing witness).
    assert spec.default_enforcement == "warn"
    assert spec.family == "audit"
    assert spec.role == "observation"


def test_make_bound_prefix_observation_carries_bound_landed_witness() -> None:
    """The observation row carries the bound/landed pair as the audit witness."""
    bound = (("chapter", "4"), ("section", "5"))
    landed = (("chapter", "4"), ("section", "5"), ("subsection", "1"))
    finding = make_bound_prefix_observation(
        op_id="op_a",
        bound_path=bound,
        landed_path=landed,
        rule_ids=("receipt_prefix_equivalence",),
        source_statute="2020/1",
    )
    assert finding.kind == RECEIPT_BOUND_PREFIX_OF_LANDED_KIND
    assert finding.role == "observation"
    assert finding.blocking is False
    assert finding.source_statute == "2020/1"
    # The bound/landed pair is preserved as the audit witness (§0 prime
    # directive: an *invisible* heuristic is forbidden; the pair is the
    # visible witness the §0 demand names).
    assert finding.detail.get("bound") == bound
    assert finding.detail.get("landed") == landed
    # The named rule family tag (presentation_cleanup) + rule id
    # (receipt_prefix_equivalence) are embedded so the audit can correlate.
    assert finding.detail.get("rule_family") == "presentation_cleanup"
    assert finding.detail.get("rule_id") == "receipt_prefix_equivalence"
    assert finding.detail.get("op_id") == "op_a"
    # The detail map is JSON-serializable (frozen but list-of-lists-friendly;
    # freeze_mapping converts lists to tuples for hashability).
    assert finding.detail.get("named_rule_ids") == ("receipt_prefix_equivalence",)


# ---------------------------------------------------------------------------
# 3. _receipt_boundary_authorized — typed fast path + legacy fallback
# ---------------------------------------------------------------------------


def _receipt(
    *,
    op_id: str = "op",
    bound_target_path: tuple[tuple[str, str], ...] | None = None,
    landed_primary_path: tuple[tuple[str, str], ...] | None = None,
    migration_rule_ids: tuple[str, ...] = (),
    divergence_kind: DivergenceKind | None = None,
) -> WriteReceipt:
    """Build a minimal receipt for _receipt_boundary_authorized tests."""
    return WriteReceipt(
        op_id=op_id,
        helper="test",
        action="replace",
        bound_target_path=bound_target_path,
        landed_primary_path=landed_primary_path,
        replaced_paths=((landed_primary_path,) if landed_primary_path else ()),
        migration_rule_ids=migration_rule_ids,
        divergence_kind=divergence_kind,
    )


def test_receipt_boundary_authorized_prefix_of_landed_typed_fast_path_pattern_a() -> None:
    """PR2 typed fast path — divergence_kind=PREFIX_OF_LANDED authorizes.

    Pattern A (landed=section:5 is a strict prefix of bound=subsection:1).
    The receipt's ``divergence_kind`` is set downstream by the receipt-
    construction site (``_classify_receipt_divergence_kind``); the receipt
    arm trusts the typed owner (§1.12 — no semantic reach-back) and
    authorizes.
    """
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5"), ("subsection", "1")),
        landed_primary_path=(("chapter", "4"), ("section", "5")),
        divergence_kind=DivergenceKind.PREFIX_OF_LANDED,
    )
    assert _receipt_boundary_authorized(receipt) is True


def test_receipt_boundary_authorized_prefix_of_landed_typed_fast_path_pattern_b() -> None:
    """PR2 typed fast path — Pattern B (bound=section:5 is prefix of landed=subsection:1)."""
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5")),
        landed_primary_path=(("chapter", "4"), ("section", "5"), ("subsection", "1")),
        divergence_kind=DivergenceKind.PREFIX_OF_LANDED,
    )
    assert _receipt_boundary_authorized(receipt) is True


def test_receipt_boundary_authorized_legacy_fallback_recomputes_prefix_relation_pattern_a() -> None:
    """Legacy defense-in-depth — pre-PR2 receipts (divergence_kind=None) fall
    back to recomputing _paths_consistent_under_prefix directly.

    The receipt's bound/landed paths are ALREADY in canonical form (canonical
    at construction); the helper passes them through as-is. Constructed
    receipts from the existing unit test suite carry no ``divergence_kind``;
    this fallback preserves their authorization behavior under PR2.
    """
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5"), ("subsection", "1")),
        landed_primary_path=(("chapter", "4"), ("section", "5")),
        # divergence_kind defaults to None — the receipt-construction site
        # was never invoked on this constructed receipt.
    )
    assert _receipt_boundary_authorized(receipt) is True


def test_receipt_boundary_authorized_legacy_fallback_recomputes_prefix_relation_pattern_b() -> None:
    """Legacy defense-in-depth — Pattern B."""
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5")),
        landed_primary_path=(("chapter", "4"), ("section", "5"), ("subsection", "1")),
    )
    assert _receipt_boundary_authorized(receipt) is True


def test_receipt_boundary_authorized_unrelated_divergence_refuses() -> None:
    """Negative — true divergence (neither prefix-of) refuses authorization.

    bound=chapter:4/section:5 vs landed=chapter:4/section:7/subsection:1
    diverges at the section level (5 vs 7); no prefix relation, no named
    rule, no typed divergence_kind → the receipt arm refuses.
    """
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5")),
        landed_primary_path=(("chapter", "4"), ("section", "7"), ("subsection", "1")),
    )
    assert _receipt_boundary_authorized(receipt) is False


def test_receipt_boundary_authorized_unrelated_divergence_with_typed_kind_refuses() -> None:
    """Negative — UNEXPLAINED_DIVERGENCE typed kind still refuses."""
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5")),
        landed_primary_path=(("chapter", "4"), ("section", "7"), ("subsection", "1")),
        divergence_kind=DivergenceKind.UNEXPLAINED_DIVERGENCE,
    )
    assert _receipt_boundary_authorized(receipt) is False


def test_receipt_boundary_authorized_exact_match_typed_kind_authorizes() -> None:
    """EXACT_MATCH typed kind authorizes (the existing bound==landed short-circuit)."""
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5")),
        landed_primary_path=(("chapter", "4"), ("section", "5")),
        divergence_kind=DivergenceKind.EXACT_MATCH,
    )
    assert _receipt_boundary_authorized(receipt) is True


def test_receipt_boundary_authorized_explained_by_rule_typed_kind_authorizes() -> None:
    """EXPLAINED_BY_RULE typed kind authorizes via existing named-rule arm."""
    receipt = _receipt(
        bound_target_path=(("section", "5"),),
        landed_primary_path=(("section", "5a"),),
        migration_rule_ids=("section_relabel_renumber",),
        divergence_kind=DivergenceKind.EXPLAINED_BY_RULE,
    )
    assert _receipt_boundary_authorized(receipt) is True


# ---------------------------------------------------------------------------
# 4. aggregate_replay_authority — prefix authorizes, dirty cross-check refuses
# ---------------------------------------------------------------------------


def test_aggregate_replay_authority_authorizes_on_prefix_relation_pattern_a() -> None:
    """The receipt arm authorizes on the prefix relation AND no_boundary_violation
    is clean → aggregate authorizes (Pattern A: landed-is-prefix-of-bound)."""
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5"), ("subsection", "1")),
        landed_primary_path=(("chapter", "4"), ("section", "5")),
        divergence_kind=DivergenceKind.PREFIX_OF_LANDED,
    )
    surface = aggregate_replay_authority(write_receipts=(receipt,), findings=())
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is True
    assert surface.authorization.executable is True


def test_aggregate_replay_authority_authorizes_on_prefix_relation_pattern_b() -> None:
    """Same as Pattern A but for Pattern B (bound-is-prefix-of-landed)."""
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5")),
        landed_primary_path=(("chapter", "4"), ("section", "5"), ("subsection", "1")),
        divergence_kind=DivergenceKind.PREFIX_OF_LANDED,
    )
    surface = aggregate_replay_authority(write_receipts=(receipt,), findings=())
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is True


def test_aggregate_replay_authority_refuses_when_undeclared_touch_dirty_despite_prefix() -> None:
    """PR2 design contract: the dirty cross-check refuses authorization
    DESPITE the prefix relation. The undeclared-touch cross-check
    (``no_boundary_violation``) is the load-bearing independent witness —
    the prefix relation is benign-by-relation-shape ONLY when the
    cross-check is clean (the declared mutation events cover the deeper
    side's descendant keys).

    FD-PR2-3 from BOUND_TARGET_PATH_NORMALIZATION_DESIGN §5.2: a clean prefix
    relation but a dirty undeclared-touch cross-check still refuses.
    """
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5"), ("subsection", "1")),
        landed_primary_path=(("chapter", "4"), ("section", "5")),
        divergence_kind=DivergenceKind.PREFIX_OF_LANDED,
    )
    # A blocking REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET finding — the
    # undeclared-touch cross-check caught a tree path the op's declared
    # mutation events do not explain. The receipt arm's prefix relation
    # does NOT mask this: aggregate refuses authorization.
    finding = Finding(
        kind=APPLY_BOUNDARY_VIOLATION_FINDING_CODE,
        role="violation",
        stage="apply",
        blocking=True,
        source_statute="2020/1",
        detail={"op_id": "op", "message": "boundary touch for tests"},
    )
    surface = aggregate_replay_authority(
        write_receipts=(receipt,), findings=(finding,)
    )
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is False
    assert surface.authorization.executable is False
    assert surface.authorization.strict_disposition == "block"


def test_aggregate_replay_authority_refuses_on_unrelated_divergence_receipt() -> None:
    """The unrelated-divergence receipt (no prefix relation, no named rule)
    still un-authorizes the aggregate (existing behavior preserved)."""
    receipt = _receipt(
        bound_target_path=(("chapter", "4"), ("section", "5")),
        landed_primary_path=(("chapter", "4"), ("section", "7"), ("subsection", "1")),
        divergence_kind=DivergenceKind.UNEXPLAINED_DIVERGENCE,
    )
    surface = aggregate_replay_authority(write_receipts=(receipt,), findings=())
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is False


# ---------------------------------------------------------------------------
# 5. §2.9 fire-drill — production-liveness through _collect_op_write_receipt
# ---------------------------------------------------------------------------


def _tree_with_section_attrs_change() -> tuple[IRNode, IRNode]:
    """A before/after IR pair whose diff lands AT the section:5 level.

    The before/after section:5 carries the SAME children (subsection:1)
    but different ``attrs`` (``amended: 1``). The identity-pruned diff
    reports the change at the section:5 path (children identical, attrs
    differ — section-level change) — mirroring the Pattern A witness in
    BOUND_TARGET_PATH_NORMALIZATION_DESIGN §1.1 (the op bounded subsection:1
    but the section's structural shape changed at the section level).
    """
    sub = _sub("1", _para("1", "body of subsection one"))
    before_sec = _sec("5", sub)
    after_sec = _sec("5", sub, attrs={"amended": "1"})
    before = _body(_chap("4", before_sec))
    after = _body(_chap("4", after_sec))
    return before, after


def _tree_with_subsection_attrs_change() -> tuple[IRNode, IRNode]:
    """A before/after IR pair whose diff lands AT the subsection:1 level.

    The before/after subsection:1 carries the SAME content child but
    different ``attrs``. The identity-pruned diff reports the change at the
    subsection:1 path (children identical, attrs differ — subsection-level
    change). Pattern B witness: the op bounded section:5 (the section root)
    but the diff descended into the subsection level.
    """
    content = _content("body of subsection one")
    before_sub = _sub("1", _para("1", "x"), attrs={})
    after_sub = _sub("1", _para("1", "x"), attrs={"amended": "1"})
    # Force the children tuple to be distinct instances but structurally
    # identical so identity-pruning stops at the subsection level (the
    # attrs differ; the inner content is preserved). Rebuild sec with the
    # same para reference identity? — IRNode is immutable; just rebuild
    # a structurally identical para each side so the structural hash of the
    # children tuples is equal.
    before_para = IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(content,))
    after_para = IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(content,))
    before_sub = _sub("1", before_para)
    after_sub = _sub("1", after_para, attrs={"amended": "1"})
    before_sec = _sec("5", before_sub)
    after_sec = _sec("5", after_sub)
    before = _body(_chap("4", before_sec))
    after = _body(_chap("4", after_sec))
    return before, after


def test_fire_drill_pattern_a_full_apply_path_emits_observation_and_authorizes() -> None:
    """§2.9 fire-drill (Pattern A — landed-is-prefix-of-bound): a synthesized
    FI op whose ``resolved_target_address.path`` is
    ``[chapter:4, section:5, subsection:1]`` driven through the production
    ``_collect_op_write_receipt`` apply path lands a receipt whose:

      (a) ``bound_target_path`` is the canonical-form of the bound path
          (no leading hcontainer wrapper; section labels preserved);
      (b) ``landed_primary_path`` is the section-level path
          ``[chapter:4, section:5]`` (the section-attrs change → diff
          descends to section level, IGNORING the bound's subsection:1
          step);
      (c) ``divergence_kind == PREFIX_OF_LANDED`` (the typed witness);
      (d) the named ``APPLY.RECEIPT_BOUND_PREFIX_OF_LANDED`` observation
          row fires in the production findings ledger (the §0 audit
          witness); AND
      (e) ``aggregate_replay_authority`` authorizes the replay (clean
          cross-check, prefix relation holds).
    """
    before, after = _tree_with_section_attrs_change()
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    bound_path = (
        ("chapter", "4"),
        ("section", "5"),
        ("subsection", "1"),
    )
    rop = _resolved_op_with_bound("prefix_a_op", bound_path)
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

    # Production-liveness: exactly one receipt was produced.
    assert len(sinks.write_receipts_out) == 1
    receipt = sinks.write_receipts_out[0]

    # (a) bound_target_path is set + canonical (no leading hcontainer, no kind
    # rewrite needed — section/subsection already in IR vocabulary).
    assert receipt.bound_target_path == (
        ("chapter", "4"),
        ("section", "5"),
        ("subsection", "1"),
    )

    # (b) landed_primary_path is the section-level diff path (the diff
    # descended to section:5 because the section's attrs changed and its
    # children tuple is structurally identical).
    assert receipt.landed_primary_path == (("chapter", "4"), ("section", "5"))

    # (c) divergence_kind is the typed witness.
    assert receipt.divergence_kind is DivergenceKind.PREFIX_OF_LANDED

    # (d) the named observation fires in the production findings ledger.
    prefix_findings = [f for f in findings if f.kind == RECEIPT_BOUND_PREFIX_OF_LANDED_KIND]
    assert len(prefix_findings) == 1, (
        f"expected one RECEIPT_BOUND_PREFIX_OF_LANDED observation, got "
        f"{len(prefix_findings)} (findings: {[f.kind for f in findings]})"
    )
    obs = prefix_findings[0]
    assert obs.role == "observation"
    assert obs.blocking is False
    # The observation carries the bound/landed pair as the audit witness.
    assert obs.detail.get("bound") == receipt.bound_target_path
    assert obs.detail.get("landed") == receipt.landed_primary_path
    assert obs.detail.get("op_id") == "prefix_a_op"
    assert obs.detail.get("rule_family") == "presentation_cleanup"
    assert obs.detail.get("rule_id") == "receipt_prefix_equivalence"

    # (e) aggregate_replay_authority authorizes (clean cross-check, prefix
    # relation holds). NOTE: _collect_op_write_receipt does NOT emit an
    # undeclared-touch finding on this synthetic op (no mutation_events_out
    # wired) so no_boundary_violation is True.
    surface = aggregate_replay_authority(
        write_receipts=sinks.write_receipts_out,
        findings=tuple(findings),
    )
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is True


def test_fire_drill_pattern_b_full_apply_path_emits_observation_and_authorizes() -> None:
    """§2.9 fire-drill (Pattern B — bound-is-prefix-of-landed): a synthesized
    FI op whose ``resolved_target_address.path`` is ``[chapter:4, section:5]``
    driven through the production apply path lands a receipt whose:

      (a) ``bound_target_path`` is the canonical section-level path;
      (b) ``landed_primary_path`` is the subsection-level path
          ``[chapter:4, section:5, subsection:1]`` (the subsection attrs
          change → diff descends to subsection:1);
      (c) ``divergence_kind == PREFIX_OF_LANDED``;
      (d) the named ``APPLY.RECEIPT_BOUND_PREFIX_OF_LANDED`` observation
          row fires in the production findings ledger; AND
      (e) ``aggregate_replay_authority`` authorizes (clean cross-check).
    """
    before, after = _tree_with_subsection_attrs_change()
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    bound_path = (
        ("chapter", "4"),
        ("section", "5"),
    )
    rop = _resolved_op_with_bound("prefix_b_op", bound_path)
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

    # (a) bound at section level.
    assert receipt.bound_target_path == (("chapter", "4"), ("section", "5"))

    # (b) landed at subsection level — bound is a strict prefix of landed.
    assert receipt.landed_primary_path == (
        ("chapter", "4"),
        ("section", "5"),
        ("subsection", "1"),
    )

    # (c) divergence_kind is the typed witness.
    assert receipt.divergence_kind is DivergenceKind.PREFIX_OF_LANDED

    # (d) the observation fires.
    prefix_findings = [f for f in findings if f.kind == RECEIPT_BOUND_PREFIX_OF_LANDED_KIND]
    assert len(prefix_findings) == 1
    obs = prefix_findings[0]
    assert obs.role == "observation"
    assert obs.blocking is False
    assert obs.detail.get("bound") == receipt.bound_target_path
    assert obs.detail.get("landed") == receipt.landed_primary_path
    assert obs.detail.get("op_id") == "prefix_b_op"

    # (e) aggregate authorizes.
    surface = aggregate_replay_authority(
        write_receipts=sinks.write_receipts_out,
        findings=tuple(findings),
    )
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is True


def test_fire_drill_negative_unrelated_divergence_does_not_emit_observation() -> None:
    """§2.9 fire-drill NEGATIVE: an op whose bound/landed paths share no
    prefix relation does NOT emit the RECEIPT_BOUND_PREFIX_OF_LANDED
    observation AND the receipt carries ``divergence_kind ==
    UNEXPLAINED_DIVERGENCE`` (no false-positive authorization).

    The diff lands on paragraph:7 under section:1 (a different section
    than the bound's section:5), so neither is a prefix of the other.
    """
    # Re-use the existing _tree_with_para7-style fixture: a tree whose diff
    # lands at chapter:4/section:1/subsection:1/paragraph:7. The op
    # nominally targets chapter:4/section:5 — a different section entirely.
    content = _content("body of seven")
    before_para = IRNode(kind=IRNodeKind.PARAGRAPH, label="7", children=(content,))
    after_para = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="7",
        children=(content,),
        attrs={"amended": "1"},
    )
    before = _body(_chap("4", _sec("1", _sub("1", before_para))))
    after = _body(_chap("4", _sec("1", _sub("1", after_para))))
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)

    bound_path = (("chapter", "4"), ("section", "5"))
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
    # bound stays at section:5, landed is the diff path under section:1.
    assert receipt.bound_target_path == (("chapter", "4"), ("section", "5"))
    assert receipt.landed_primary_path == (
        ("chapter", "4"),
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "7"),
    )
    # The divergence is UNEXPLAINED — typed witness.
    assert receipt.divergence_kind is DivergenceKind.UNEXPLAINED_DIVERGENCE
    # No RECEIPT_BOUND_PREFIX_OF_LANDED observation fired.
    prefix_findings = [f for f in findings if f.kind == RECEIPT_BOUND_PREFIX_OF_LANDED_KIND]
    assert prefix_findings == []
    # Refuses authorization.
    assert _receipt_boundary_authorized(receipt) is False


# ---------------------------------------------------------------------------
# 6. no-leak assertions — synthetic markers user-visible surfaces (§2.9 cl.6)
# ---------------------------------------------------------------------------


def test_no_leak_synthetic_markers_confined_to_bound_landed_paths() -> None:
    """§2.9 clause 6: synthetic markers (chapter:4, section:5, subsection:1
    in these fire-drills) do not leak into user-visible surfaces outside
    the explicit test subjects (bound/landed_path).

    The fire-drills use ``chapter:4`` / ``section:5`` purely as the
    synthesised bound/landed values; they must NOT appear in op_id-encoded
    statistics, source_statute, or the receipt's audit fields. The findings
    ledger — the audit witness the prefix observation carries — is
    permitted to embed them in the bound/landed detail (that is its job).
    """
    before, after = _tree_with_section_attrs_change()
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    bound_path = (("chapter", "4"), ("section", "5"), ("subsection", "1"))
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
    # op_id is the bare synthesised identifier — no path markers encoded.
    assert receipt.op_id == "leak_check_op"
    # source_statute is the test statute identifier, not a bound/landed fragment.
    assert receipt.source_anchor is None or "chapter" not in repr(receipt.source_anchor)
    # action is the rop's resolved_action_type ("replace" lowercased).
    assert receipt.action == "replace"
    # The helper is the production FI helper (no synthetic markers).
    assert receipt.helper == "fi.apply.resolved_op_write"
    # The findings ledger: exactly one observation row fired (prefix), no
    # boundary violation, no extra diagnostics.
    assert len(findings) == 1
    assert findings[0].kind == RECEIPT_BOUND_PREFIX_OF_LANDED_KIND
    # The observation's source_statute field is the test statute (no leak).
    assert findings[0].source_statute == "2020/1"
