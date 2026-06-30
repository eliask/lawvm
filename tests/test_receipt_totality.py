"""B-enforcement increment 5: the universal receipt-totality CONTRACT (observe).

Design reference: ``notes/B_ENFORCEMENT_STATUS.md`` §6 (the FI-battery →
seam-status table; the ``receipt-totality`` row) + §6.2 (second bullet:
"Receipt-totality → contract-checked seam output") + §3(c). FI reference:
``finland/apply_resolved_op._collect_op_write_receipt`` (the per-op
``WriteReceipt`` producer the seam's ``emit_receipts`` generalizes).

WHAT THIS CONTRACT IS. The seam already SYNTHESIZES the per-op
:class:`~lawvm.core.write_receipt.WriteReceipt` (``apply_seam._synthesize_receipt``,
gated on ``profile.emit_receipts``). What was MISSING is the TOTALITY contract —
the receipt analogue of coverage-totality:

    every landed write ⇒ exactly one receipt; no receipt without a landed write
    (i.e. landed-writes ↔ receipts is a bijection).

``core/receipt_totality.check_receipt_totality`` is the pure, dependency-light
contract over an accumulated per-op ledger. The seam runs it over the per-op slice
(a one-entry ledger of THIS op's (landed, receipt) outcome) and routes a broken-arm
witness — one non-blocking ``APPLY.RECEIPT_TOTALITY_OBSERVED`` observation — to the
SEPARATE :attr:`AppliedOp.observations` lane, NEVER to :attr:`AppliedOp.findings`.
That separation is the byte-identity mechanism (the six byte-identity gates read
``findings``/receipts, not ``observations``).

THE DEFAULT IS 0-DELTA. The receipt-emitting tree profiles synthesize exactly one
receipt per landed write on the same ``landed`` seam branch, so the contract is
silent for them; the non-emitting profiles set ``emit_receipts=False``
(``receipts_expected=False``), so a landed write with no receipt is a declared
no-receipt fold, not a broken bijection. This test asserts the CONTRACT MECHANISM
on a synthetic profile — receipt present → quiet; receipt missing for a landed
write under emit_receipts → exactly one observation; spurious receipt → exactly
one observation — NOT any frontend's transient receipt count (EE, which a sibling
lane is concurrently making emit receipts, would change).
"""
from __future__ import annotations

from lawvm.core import tree_ops
from lawvm.core.apply_seam import (
    RECEIPT_TOTALITY_OBSERVED_FINDING_CODE,
    ApplyProfile,
    AppliedOp,
    MaterializeResult,
    apply_op,
)
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import Finding
from lawvm.core.receipt_totality import (
    RECEIPT_TOTALITY_REQUIRED_FINDING_CODE,
    ReceiptLedgerEntry,
    check_receipt_totality,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.write_receipt import WriteReceipt


# ── A small tree materializer + op corpus shared across the cases ─────────────


def _addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _op(op_id: str, label: str, action: StructuralAction) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=action,
        target=_addr(label),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=f"new {label}"),
        source=OperationSource(statute_id="act/2025", effective="2026-01-01"),
    )


def _body() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=tuple(
            IRNode(kind=IRNodeKind.SECTION, label=str(n), text=f"Original {n}")
            for n in (1, 2, 3)
        ),
    )


def _tree_materializer(before: IRNode, op: LegalOperation) -> MaterializeResult[IRNode]:
    """A minimal section-patch materializer that lands a CoW write on the target."""
    label = op.target.leaf_label()
    path = tree_ops.find(before, "section", label) if label else None
    if path is None:
        return MaterializeResult(new_state=before, applied=False)
    node = tree_ops.resolve(before, list(path))
    if node is None:
        return MaterializeResult(new_state=before, applied=False)
    new_node = IRNode(kind=node.kind, label=node.label, text="patched")
    return MaterializeResult(new_state=tree_ops.replace_at(before, path, new_node))


def _profile(
    jurisdiction: str = "syn",
    *,
    emit_receipts: bool = True,
) -> ApplyProfile[IRNode]:
    """A representative ``boundary_mode="off"`` tree profile (the production shape)."""
    return ApplyProfile(
        jurisdiction=jurisdiction,
        materializer=_tree_materializer,
        boundary_mode="off",
        emit_receipts=emit_receipts,
        emit_coverage=False,
    )


def _wr(op_id: str = "r") -> WriteReceipt:
    return WriteReceipt(
        op_id=op_id,
        helper="syn::apply_op::replace::section",
        action="replace",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "1"),),
    )


# ── The registry contract for the new codes (fresh observation + STAGED twin) ──


def test_observation_code_registered_as_observation_role() -> None:
    """The new code is a fresh observation-role twin of the strict block."""
    spec = get_finding_spec(RECEIPT_TOTALITY_OBSERVED_FINDING_CODE)
    assert spec is not None
    assert spec.role == "observation"
    assert spec.default_enforcement == "warn"


def test_required_code_registered_as_staged_violation_twin() -> None:
    """The STAGED strict-blocking twin is a DISTINCT violation-role code."""
    required = get_finding_spec(RECEIPT_TOTALITY_REQUIRED_FINDING_CODE)
    assert required is not None
    assert required.role == "violation"
    assert required.default_enforcement == "hard_fail"
    assert (
        RECEIPT_TOTALITY_OBSERVED_FINDING_CODE != RECEIPT_TOTALITY_REQUIRED_FINDING_CODE
    )


# ── The contract function: the bijection arms over an accumulated ledger ───────


def test_clean_one_to_one_ledger_is_total_and_silent() -> None:
    """A landed write WITH exactly one receipt: bijection holds, no witness."""
    ledger = (
        ReceiptLedgerEntry(op_id="a", landed=True, receipt=_wr("a")),
        ReceiptLedgerEntry(op_id="b", landed=True, receipt=_wr("b")),
        # A skipped op with no receipt is fine (it landed no write).
        ReceiptLedgerEntry(op_id="c", landed=False, receipt=None),
    )
    report = check_receipt_totality(ledger, receipts_expected=True)
    assert report.is_total
    assert report.findings == ()
    assert report.landed_writes == 2
    assert report.receipts == 2


def test_missing_receipt_for_landed_write_fires_one_observation() -> None:
    """A landed write with NO receipt (receipts expected) → exactly one witness."""
    ledger = (
        ReceiptLedgerEntry(op_id="a", landed=True, receipt=_wr("a")),
        ReceiptLedgerEntry(op_id="gap", landed=True, receipt=None),  # the break
    )
    report = check_receipt_totality(
        ledger, receipts_expected=True, source_statute="act/2025", jurisdiction="syn"
    )
    assert not report.is_total
    assert report.missing_receipt_op_ids == ("gap",)
    assert report.spurious_receipt_op_ids == ()
    assert len(report.findings) == 1
    obs = report.findings[0]
    assert isinstance(obs, Finding)
    assert obs.kind == RECEIPT_TOTALITY_OBSERVED_FINDING_CODE
    assert obs.role == "observation"
    assert obs.blocking is False
    assert obs.detail["arm"] == "missing_receipt"
    assert obs.detail["op_id"] == "gap"
    assert obs.detail["owner"] == "apply_seam_receipt_totality"


def test_spurious_receipt_without_landed_write_fires_one_observation() -> None:
    """A receipt whose op did NOT land a write → exactly one witness (any setting)."""
    ledger = (ReceiptLedgerEntry(op_id="ghost", landed=False, receipt=_wr("ghost")),)
    # Spurious is a violation even when receipts are NOT expected (a receipt is the
    # record of a LANDED write; one with no landed write is a lie the audit sees).
    report = check_receipt_totality(ledger, receipts_expected=False)
    assert not report.is_total
    assert report.spurious_receipt_op_ids == ("ghost",)
    assert report.missing_receipt_op_ids == ()
    assert len(report.findings) == 1
    assert report.findings[0].detail["arm"] == "spurious_receipt"


def test_missing_receipt_is_silent_when_receipts_not_expected() -> None:
    """``emit_receipts=False`` lands writes with no receipt: a declared no-receipt fold."""
    ledger = (ReceiptLedgerEntry(op_id="a", landed=True, receipt=None),)
    report = check_receipt_totality(ledger, receipts_expected=False)
    assert report.is_total
    assert report.findings == ()


# ── The seam runs the contract per-op and routes the witness to observations ───


def test_seam_landed_write_with_receipt_is_quiet() -> None:
    """A receipt-emitting profile: the landed write carries one receipt → no witness."""
    op = _op("a", "1", StructuralAction.REPLACE)
    profile = _profile(emit_receipts=True)
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    assert applied.write_receipt is not None
    assert not [
        f
        for f in applied.observations
        if f.kind == RECEIPT_TOTALITY_OBSERVED_FINDING_CODE
    ]


def test_seam_landed_write_missing_receipt_fires_observation() -> None:
    """A profile that claims to emit receipts but whose synthesis yielded none.

    Driven by a materializer that lands a write whose before/after IR diff is
    EMPTY at the receipt-synthesis pass (a structurally-identical replacement),
    so ``_synthesize_receipt`` returns ``None`` while the op DID land — exactly
    the missing-receipt arm the contract catches. Routed to ``observations``.
    """
    op = _op("noisediff", "1", StructuralAction.REPLACE)

    def _identity_landing_materializer(
        before: IRNode, _op: LegalOperation
    ) -> MaterializeResult[IRNode]:
        # ``applied=True`` but ``new_state`` is a fresh-but-structurally-identical
        # body: ``landed`` (new_state is not base_state) holds, yet the IR-path
        # diff is empty so ``_synthesize_receipt`` returns None — a landed write
        # with no receipt while ``emit_receipts`` is on.
        return MaterializeResult(
            new_state=IRNode(kind=before.kind, label=before.label, children=before.children),
            applied=True,
        )

    profile = ApplyProfile(
        jurisdiction="syn",
        materializer=_identity_landing_materializer,
        boundary_mode="off",
        emit_receipts=True,
        emit_coverage=False,
    )
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    assert applied.write_receipt is None  # synthesis found no diff
    obs = [
        f
        for f in applied.observations
        if f.kind == RECEIPT_TOTALITY_OBSERVED_FINDING_CODE
    ]
    assert len(obs) == 1
    assert obs[0].detail["arm"] == "missing_receipt"
    assert obs[0].detail["op_id"] == "noisediff"


def test_seam_non_emitting_profile_is_zero_delta() -> None:
    """A ``emit_receipts=False`` profile lands a write with no receipt: no witness."""
    op = _op("a", "1", StructuralAction.REPLACE)
    profile = _profile(emit_receipts=False)
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    assert applied.write_receipt is None
    assert not [
        f
        for f in applied.observations
        if f.kind == RECEIPT_TOTALITY_OBSERVED_FINDING_CODE
    ]


def test_seam_skipped_op_is_silent() -> None:
    """An op that lands no write: no receipt expected, no witness (either arm)."""
    op = _op("missing", "99", StructuralAction.REPLACE)  # label not in body
    profile = _profile(emit_receipts=True)
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert not applied.applied
    assert applied.write_receipt is None
    assert not [
        f
        for f in applied.observations
        if f.kind == RECEIPT_TOTALITY_OBSERVED_FINDING_CODE
    ]


# ── The witness NEVER leaks into the production findings lane ──────────────────


def test_observation_never_leaks_into_findings() -> None:
    """The missing-receipt witness lives on ``observations``, never on ``findings``."""
    op = _op("noisediff", "1", StructuralAction.REPLACE)

    def _identity_landing_materializer(
        before: IRNode, _op: LegalOperation
    ) -> MaterializeResult[IRNode]:
        return MaterializeResult(
            new_state=IRNode(kind=before.kind, label=before.label, children=before.children),
            applied=True,
        )

    profile = ApplyProfile(
        jurisdiction="syn",
        materializer=_identity_landing_materializer,
        boundary_mode="off",
        emit_receipts=True,
        emit_coverage=False,
    )
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert any(
        f.kind == RECEIPT_TOTALITY_OBSERVED_FINDING_CODE for f in applied.observations
    )
    assert not any(
        getattr(f, "kind", None) == RECEIPT_TOTALITY_OBSERVED_FINDING_CODE
        for f in applied.findings
    )


# ── 0-delta: the contract perturbs nothing on the clean default seam path ──────


def test_contract_is_zero_delta_on_clean_seam_path() -> None:
    """With no perturbation, a normal receipt-emitting apply emits no receipt witness.

    The default seam path (a real materializer that lands a real diff under
    ``emit_receipts=True``) synthesizes exactly one receipt per landed write, so
    the contract is silent: the byte-identity guarantee the production profiles
    inherit.
    """
    profile = _profile(emit_receipts=True)
    for label in ("1", "2", "3"):
        op = _op(f"op{label}", label, StructuralAction.REPLACE)
        applied: AppliedOp[IRNode] = apply_op(
            _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
        )
        assert applied.applied
        assert applied.write_receipt is not None
        assert not [
            f
            for f in applied.observations
            if f.kind == RECEIPT_TOTALITY_OBSERVED_FINDING_CODE
        ]
