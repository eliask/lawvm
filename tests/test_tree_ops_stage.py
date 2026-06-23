"""WAIST #3 — the structural-mutation surface returns ``StageResult[IRNode]``.

These pin the conversion the structural waist makes (notes_internal/WAVE2_DESIGN.md
WAIST #3): the core tree ops gain ``*_staged`` wrappers over the existing
``*_witnessed`` variants that SURFACE the ``WriteReceipt`` footprint account as
the canonical ``StageResult[IRNode]`` (Pro §2 stage contract). What is pinned:

1.  Round-trip value identity — ``replace_at_staged(...).value`` etc. equal the
    bare ``replace_at`` output (0-delta: the staged form only wraps the write).
2.  Coverage — ``coverage.total == len(receipt.declared_footprint)``, ``unit ==
    "paths"``, every declared path ``owned``, ``residual``/``violation`` 0.
3.  Mutation-boundary residual — an unexplained bound→landed divergence
    (``WriteReceipt.divergence_explained is False``) maps to exactly one blocking
    ``unowned_violation`` residual; an explained / clean write maps to none.
4.  Authority firewall — the core op surface is non-authoritative (Pro §8): the
    staged result carries ``NEUTRAL_AUTHORITY`` (``replay_authorized is False``).
5.  Evidence — a receipt ``source_anchor`` projects into a ``DigestWitness``.
6.  Consumer fire-drill — the FI apply consumer (``_apply_container_op`` via
    ``_emit_container_insert_receipt``) RUNS the staged read on the live write
    path. On a well-formed container INSERT it produces a clean (non-blocking)
    account (0-delta on the green case). When an UNEXPLAINED mutation-boundary
    divergence is driven through the production consumer under a strict profile,
    the typed StageResult residual (``has_blocking_residual``) becomes a BLOCKING
    apply-boundary finding on the verdict surface (``findings_out``) — proving
    #3's incompleteness can block a clean claim through the typed account and
    is not severed into a log-only sink.

Run:
    uv run pytest tests/test_tree_ops_stage.py -v
"""

from __future__ import annotations

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.phase_result import Finding
from lawvm.core.provenance import compute_source_anchor
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.stage_result import StageResult
from lawvm.core.tree_ops import (
    insert_sorted_staged,
    remove_at_staged,
    replace_at_staged,
    structural_stage_result,
)
from lawvm.core.write_receipt import WriteReceipt


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _sec(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
    )


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _tree() -> IRNode:
    return _body(_sec("1", "old one"), _sec("3", "old three"))


# ---------------------------------------------------------------------------
# 1. round-trip value identity (0-delta)
# ---------------------------------------------------------------------------


def test_replace_at_staged_value_identity() -> None:
    tree = _tree()
    content = _sec("1", "new one")
    staged = replace_at_staged(tree, [("section", "1")], content)
    bare = _tops.replace_at(tree, [("section", "1")], content)

    assert isinstance(staged, StageResult)
    assert staged.value == bare


def test_remove_at_staged_value_identity() -> None:
    tree = _tree()
    staged = remove_at_staged(tree, [("section", "3")])
    bare = _tops.remove_at(tree, [("section", "3")])

    assert isinstance(staged, StageResult)
    assert staged.value == bare


def test_insert_sorted_staged_value_identity() -> None:
    tree = _tree()
    content = _sec("2", "new two")
    staged = insert_sorted_staged(tree, [], content)
    bare = _tops.insert_sorted(tree, [], content)

    assert isinstance(staged, StageResult)
    assert staged.value == bare


# ---------------------------------------------------------------------------
# 2. coverage: the footprint partition is RETURNED
# ---------------------------------------------------------------------------


def test_replace_at_staged_coverage_matches_declared_footprint() -> None:
    tree = _tree()
    staged = replace_at_staged(tree, [("section", "1")], _sec("1", "new one"))
    outcome = _tops.replace_at_witnessed(tree, [("section", "1")], _sec("1", "new one"))

    declared = len(outcome.receipt.declared_footprint)
    assert declared > 0
    assert staged.coverage.unit == "paths"
    assert staged.coverage.total == declared
    assert staged.coverage.owned == declared
    assert staged.coverage.residual == 0
    assert staged.coverage.violation == 0
    assert staged.coverage.is_partition()
    assert staged.coverage.is_clean


def test_insert_sorted_staged_coverage_matches_declared_footprint() -> None:
    tree = _tree()
    content = _sec("2", "new two")
    staged = insert_sorted_staged(tree, [], content)
    outcome = _tops.insert_sorted_witnessed(tree, [], content)

    assert staged.coverage.total == len(outcome.receipt.declared_footprint)
    assert staged.coverage.owned == staged.coverage.total


# ---------------------------------------------------------------------------
# 3. mutation-boundary residual
# ---------------------------------------------------------------------------


def test_bare_witnessed_op_reports_landed_leaf_divergence() -> None:
    # A BARE witnessed op has no resolver binding aligning bound→landed: the
    # bound target is the nominal path while the landed primary is the diff-
    # observed leaf, so the receipt records an unexplained divergence and the
    # staged form faithfully surfaces the blocking residual (per WAIST #3 step 2:
    # residual fires iff receipt.divergence_explained is False). The bare
    # wrappers have NO production consumer (only the apply consumer, which builds
    # an aligned + named-rule receipt, consumes the account) so this is 0-delta.
    tree = _tree()
    staged = replace_at_staged(tree, [("section", "1")], _sec("1", "new one"))
    assert staged.has_blocking_residual
    assert staged.residuals[0].kind == "unowned_violation"


def test_aligned_clean_receipt_has_no_blocking_residual() -> None:
    # When a write IS resolver-aligned (bound == landed), the clean account
    # carries no residual — the green-path shape the apply consumer produces.
    receipt = WriteReceipt(
        op_id="op",
        helper="test",
        action="replace",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "1"),),
        replaced_paths=((("section", "1"),),),
    )
    assert receipt.divergence_explained is True
    staged = structural_stage_result(_tree(), receipt)
    assert staged.residuals == ()
    assert not staged.has_blocking_residual


def test_unexplained_divergence_maps_to_blocking_unowned_violation() -> None:
    # A hand-built receipt where the bound target differs from the landed
    # primary path with NO named recovery/migration/fallback rule — the
    # unexplained mutation-boundary divergence the §4 contract forbids.
    bound = (("section", "1"),)
    landed = (("section", "2"),)
    receipt = WriteReceipt(
        op_id="op",
        helper="test",
        action="replace",
        bound_target_path=bound,
        landed_primary_path=landed,
        replaced_paths=(landed,),
    )
    assert receipt.divergence_explained is False

    staged = structural_stage_result(_tree(), receipt)
    assert staged.has_blocking_residual
    assert len(staged.residuals) == 1
    residual = staged.residuals[0]
    assert residual.kind == "unowned_violation"
    assert residual.reason == "unexplained_mutation_boundary_divergence"
    assert residual.blocking is True
    assert residual.scope == "section:1"


def test_explained_divergence_via_named_rule_has_no_residual() -> None:
    receipt = WriteReceipt(
        op_id="op",
        helper="test",
        action="replace",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "2"),),
        replaced_paths=((("section", "2"),),),
        recovery_rule_ids=("named_recovery_rule",),
    )
    assert receipt.divergence_explained is True

    staged = structural_stage_result(_tree(), receipt)
    assert staged.residuals == ()
    assert not staged.has_blocking_residual


# ---------------------------------------------------------------------------
# 4. authority firewall (Pro §8) — the core op surface is non-authoritative
# ---------------------------------------------------------------------------


def test_staged_ops_are_non_authoritative() -> None:
    tree = _tree()
    for staged in (
        replace_at_staged(tree, [("section", "1")], _sec("1", "x")),
        remove_at_staged(tree, [("section", "3")]),
        insert_sorted_staged(tree, [], _sec("2", "y")),
    ):
        assert staged.authority.is_neutral
        assert staged.authority.replay_authorized is False


# ---------------------------------------------------------------------------
# 5. evidence — a source_anchor projects into a DigestWitness
# ---------------------------------------------------------------------------


def test_source_anchor_projects_into_digest_witness() -> None:
    raw = b"prefix amend clause needle suffix"
    anchor = compute_source_anchor(
        source_artifact_id="2020/1",
        raw_bytes=raw,
        clause_text="amend clause needle",
    )
    assert anchor is not None
    receipt = WriteReceipt(
        op_id="op",
        helper="test",
        action="replace",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "1"),),
        replaced_paths=((("section", "1"),),),
        source_anchor=anchor,
    )

    staged = structural_stage_result(_tree(), receipt)
    assert not staged.evidence.is_empty
    witness = staged.evidence.witnesses[0]
    assert isinstance(witness, SourceWitness)
    assert witness.source_role == "amendment_source_clause"
    assert witness.artifact_id == "2020/1"
    assert isinstance(witness.digest, DigestWitness)
    algorithm, _, digest = anchor.quote_hash.partition(":")
    assert witness.digest.digest_algorithm == algorithm
    assert witness.digest.digest == digest


def test_no_source_anchor_yields_empty_evidence() -> None:
    staged = replace_at_staged(_tree(), [("section", "1")], _sec("1", "x"))
    assert staged.evidence.is_empty


# ---------------------------------------------------------------------------
# 6. consumer fire-drill — the FI apply path runs the staged read live
# ---------------------------------------------------------------------------


def test_apply_container_consumer_runs_staged_read_clean_on_well_formed_insert() -> None:
    # Drive the real FI apply consumer (_apply_container_op) through a
    # well-formed container INSERT. The container INSERT path emits its receipt
    # via _emit_container_insert_receipt, which RUNS structural_stage_result and
    # reads .has_blocking_residual. A clean container INSERT carries a named
    # recovery rule, so the staged account is clean: no blocking residual, no
    # decline — proving the staged read is on the live write path AND that the
    # green case is 0-delta.
    from lawvm.finland.apply_structure_ops import _apply_container_op
    from lawvm.finland.ops import AmendmentOp, get_replay_profile
    from lawvm.finland.statute import ReplayState
    from lawvm.core.write_receipt import WriteReceipt as _WR

    state = ReplayState(
        ir=_body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                    _sec("1", "chapter 1"),
                ),
            ),
        )
    )
    op = AmendmentOp(
        op_id="insert_chapter_2",
        op_type="INSERT",
        target_unit_kind="chapter",
        target_section="2",
        source_statute="2020/1",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 luku"),
            _sec("1", "chapter 2"),
        ),
    )

    receipts: list[_WR] = []
    result = _apply_container_op(
        state,
        op,
        muutos_ir,
        get_replay_profile("legal_pit"),
        "[2020/1] INSERT 2 luku",
        write_receipts_out=receipts,
    )

    assert result is not None and result is not state
    # The container INSERT emitted exactly one receipt on the live path...
    assert len(receipts) == 1
    receipt = receipts[0]
    # ...and the staged account the consumer reads is clean (explained write):
    staged = structural_stage_result(result.ir, receipt)
    assert not staged.has_blocking_residual
    assert receipt.divergence_explained is True


def _strict_container_insert(
    findings_out: list,
    *,
    force_unexplained_divergence: bool,
    monkeypatch,
):
    """Drive the production container-INSERT consumer under a strict profile.

    Runs the real ``_apply_container_op`` live write path (the same path the
    apply boundary verdict uses). When ``force_unexplained_divergence`` is set,
    the landed ``WriteReceipt.divergence_explained`` is forced ``False`` —
    simulating the genuine production bug the WAIST #3 contract forbids (a
    bound→landed mutation-boundary divergence with no named rule). The wiring
    under test is: the typed StageResult residual (``has_blocking_residual``)
    must, under a strict profile, become a blocking Finding on ``findings_out``.
    """
    from lawvm.finland.apply_structure_ops import _apply_container_op
    from lawvm.finland.ops import AmendmentOp as _AmendmentOp, get_replay_profile
    from lawvm.finland.statute import ReplayState as _ReplayState
    from lawvm.core.compile_result import StrictProfile

    if force_unexplained_divergence:
        # The genuine failure mode: a landed container write whose declared
        # bound target diverges from the landed primary path with NO named rule
        # explaining it. Production always carries a recovery rule on this path
        # (the 0-delta guarantee), so we force the receipt's divergence verdict
        # to the unexplained state to exercise the strict apply boundary.
        monkeypatch.setattr(
            WriteReceipt, "divergence_explained", property(lambda self: False)
        )

    state = _ReplayState(
        ir=_body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                    _sec("1", "chapter 1"),
                ),
            ),
        )
    )
    op = _AmendmentOp(
        op_id="insert_chapter_2",
        op_type="INSERT",
        target_unit_kind="chapter",
        target_section="2",
        source_statute="2020/1",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 luku"),
            _sec("1", "chapter 2"),
        ),
    )

    receipts: list[WriteReceipt] = []
    result = _apply_container_op(
        state,
        op,
        muutos_ir,
        get_replay_profile("legal_pit"),
        "[2020/1] INSERT 2 luku",
        write_receipts_out=receipts,
        findings_out=findings_out,
        strict_profile=StrictProfile(name="test_strict", allows_target_guessing=False),
    )
    assert result is not None and result is not state
    assert len(receipts) == 1
    return result, receipts[0]


def test_apply_consumer_unexplained_divergence_blocks_via_typed_residual(monkeypatch) -> None:
    # FIRE DRILL (WAVE2 WAIST #3 §6(d)): an unexplained mutation-boundary
    # divergence driven through the PRODUCTION apply consumer must produce a
    # BLOCKING finding on the verdict surface, derived from the typed StageResult
    # residual — not merely a log line. This is what makes #3's incompleteness
    # able to block a clean claim THROUGH THE TYPED ACCOUNT.
    findings: list[Finding] = []
    _strict_container_insert(
        findings, force_unexplained_divergence=True, monkeypatch=monkeypatch
    )

    # The typed residual reached the verdict surface as a blocking finding.
    boundary = [
        f
        for f in findings
        if f.kind == "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"
    ]
    assert len(boundary) == 1, (
        "an unexplained mutation-boundary divergence must surface a blocking "
        "apply-boundary finding on the verdict surface (the typed residual must "
        "not be severed into a log-only sink)"
    )
    finding = boundary[0]
    assert finding.role == "violation"
    assert finding.blocking is True
    assert finding.stage == "apply"
    assert finding.source_statute == "2020/1"
    # The finding is DERIVED FROM the typed residual, not the undeclared-touch
    # cross-check: it carries the structural residual reason/scope.
    assert (
        finding.detail["residual_reason"]
        == "unexplained_mutation_boundary_divergence"
    )


def test_apply_consumer_explained_divergence_is_clean_no_finding(monkeypatch) -> None:
    # 0-delta guard: the SAME production consumer on the SAME well-formed
    # container INSERT (divergence explained by a named recovery rule, as on the
    # green corpus) emits NO blocking finding — the residual is empty and the
    # strict apply boundary stays clean. If this fired, routing the residual
    # would have moved bench.
    findings: list[Finding] = []
    _result, receipt = _strict_container_insert(
        findings, force_unexplained_divergence=False, monkeypatch=monkeypatch
    )
    assert receipt.divergence_explained is True
    assert findings == []
