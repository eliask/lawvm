"""Receipt-totality contract for the universal apply seam (B-enforcement inc 5).

Design reference: ``notes/B_ENFORCEMENT_STATUS.md`` §6 (the FI-battery →
seam-status table; the ``receipt-totality`` row, previously **STAGED**), §6.2
(second bullet: "Receipt-totality → contract-checked seam output"), and §3(c).
FI reference: ``finland/apply_resolved_op._collect_op_write_receipt`` (the per-op
``WriteReceipt`` producer the seam's ``emit_receipts`` generalizes).

WHAT THIS IS. The seam already SYNTHESIZES the per-op
:class:`~lawvm.core.write_receipt.WriteReceipt` (``apply_seam._synthesize_receipt``,
gated on ``profile.emit_receipts``). What was MISSING is the TOTALITY CONTRACT —
the receipt analogue of coverage-totality
(``core/coverage_totality.assert_coverage_totality``):

    every landed write ⇒ exactly one receipt;
    no landed write without a receipt;
    no receipt without a landed write.

i.e. *landed-writes ↔ receipts is a bijection*. This module is the pure,
dependency-light contract over an accumulated per-op ledger. It is OBSERVE-FIRST
(design §5): a violation is surfaced as ONE non-blocking
``APPLY.RECEIPT_TOTALITY_OBSERVED`` observation routed to the seam's SEPARATE
``AppliedOp.observations`` lane — NEVER to production ``findings`` — so the six
byte-identity gates stay green. Its strict-blocking twin
``APPLY.RECEIPT_TOTALITY_REQUIRED`` is registered but no profile routes to it yet
(the staged block-promotion path).

WHY A LEDGER, NOT A PER-OP CHECK. A receipt is the OUTPUT of one ``apply_op``
call, but TOTALITY is a multi-op property: the bijection between the set of
landed writes and the set of receipts emitted across an apply run. So the
contract is a pure function over an accumulated ledger of per-op outcomes
(:class:`ReceiptLedgerEntry`), exactly as ``assert_coverage_totality`` runs over
the accumulated ``CoverageClaim`` ledger. The seam accumulates one entry per
``apply_op`` call (landed flag + the synthesized receipt) and runs this contract
over the running ledger; the per-op slice of the contract (a landed write under
``emit_receipts`` that produced no receipt, or a receipt with no landed write)
is the witness the seam emits.

THE EMIT-RECEIPTS GATE. A profile with ``emit_receipts=False`` (the cheaper fold)
INTENTIONALLY lands writes with no receipt — that is not a totality violation, it
is the profile declaring it produces no receipts. The contract is therefore
parameterized by ``receipts_expected``: only when the profile emits receipts is a
landed write REQUIRED to carry exactly one. A spurious receipt (a receipt entry
whose op did not land) is a violation under ANY setting (a receipt is a record of
a LANDED write; one with no landed write is a lie the audit must see — write_receipt
§4). This keeps all 6 production profiles 0-delta: the receipt-emitting tree
profiles already emit exactly one receipt per landed write (the seam synthesizes
it on the same ``landed`` branch), and the non-emitting profiles set
``receipts_expected=False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lawvm.core.phase_result import Finding
from lawvm.core.write_receipt import WriteReceipt

__all__ = [
    "RECEIPT_TOTALITY_OBSERVED_FINDING_CODE",
    "RECEIPT_TOTALITY_REQUIRED_FINDING_CODE",
    "ReceiptLedgerEntry",
    "ReceiptTotalityReport",
    "check_receipt_totality",
]


#: The non-blocking observation code the contract emits when the landed-writes ↔
#: receipts bijection is broken (a landed write with no receipt, or a receipt with
#: no landed write). Routed to the seam's SEPARATE ``AppliedOp.observations`` lane,
#: never to production ``findings``. Its strict-blocking twin is
#: ``APPLY.RECEIPT_TOTALITY_REQUIRED`` (registered, not yet routed to — the staged
#: block-promotion path; see ``notes/B_ENFORCEMENT_STATUS.md``).
RECEIPT_TOTALITY_OBSERVED_FINDING_CODE = "APPLY.RECEIPT_TOTALITY_OBSERVED"

#: The strict-blocking violation twin of the observe code above. Registered so the
#: per-profile block-promotion path has a destination, but NO profile routes to it
#: yet (observe-first; design §5). When a profile's apply run is MEASURED
#: receipt-total (zero observations over its corpus), promoting that profile's
#: contract from the observe code to this block is the EV-04 promote-after-clean-
#: bench landing — exactly EE's LS-03 occupancy flip template (§7.2).
RECEIPT_TOTALITY_REQUIRED_FINDING_CODE = "APPLY.RECEIPT_TOTALITY_REQUIRED"


@dataclass(frozen=True, slots=True)
class ReceiptLedgerEntry:
    """One per-op outcome in the accumulated receipt ledger.

    A thin, dependency-free record of what one ``apply_op`` call produced, so the
    totality contract can be run over the running ledger without reaching back
    into the full :class:`~lawvm.core.apply_seam.AppliedOp`:

    * ``op_id`` — the op's identity (for the witness detail; ``""`` when the op
      carried no id).
    * ``landed`` — whether the op landed a write (``AppliedOp.applied``). A landed
      write is the LHS of the bijection.
    * ``receipt`` — the synthesized :class:`WriteReceipt`, or ``None`` when the op
      landed no write OR ``profile.emit_receipts`` is ``False``. A receipt is the
      RHS of the bijection.
    """

    op_id: str
    landed: bool
    receipt: Optional[WriteReceipt] = None


@dataclass(frozen=True, slots=True)
class ReceiptTotalityReport:
    """The result of running the receipt-totality contract over a ledger.

    * ``landed_writes`` — count of ledger entries that landed a write.
    * ``receipts`` — count of ledger entries carrying a receipt.
    * ``missing_receipt_op_ids`` — landed writes that carried NO receipt while
      ``receipts_expected`` (the bijection's missing-RHS arm).
    * ``spurious_receipt_op_ids`` — receipts whose op did NOT land a write (the
      bijection's spurious-RHS arm; a receipt with no landed write is a lie under
      ANY setting).
    * ``findings`` — one non-blocking ``APPLY.RECEIPT_TOTALITY_OBSERVED``
      observation per broken arm (the seam routes these to ``observations``).
    """

    landed_writes: int
    receipts: int
    missing_receipt_op_ids: tuple[str, ...]
    spurious_receipt_op_ids: tuple[str, ...]
    findings: tuple[Finding, ...]

    @property
    def is_total(self) -> bool:
        """True when the landed-writes ↔ receipts bijection holds (no witness)."""
        return not self.missing_receipt_op_ids and not self.spurious_receipt_op_ids


def _totality_finding(
    *,
    arm: str,
    op_id: str,
    source_statute: str,
    jurisdiction: str,
) -> Finding:
    """One non-blocking receipt-totality witness Finding for a broken bijection arm."""
    if arm == "missing_receipt":
        message = (
            "A state-mutating op landed a write through the universal apply seam "
            "but produced NO WriteReceipt while the profile emits receipts: a "
            "landed write with no receipt breaks the receipt-totality bijection "
            "(every landed write ⇒ exactly one receipt). Surfaced as a non-blocking "
            "receipt-totality witness; not promoted to authority."
        )
    else:  # spurious_receipt
        message = (
            "A WriteReceipt was synthesized for an op that landed NO write through "
            "the universal apply seam: a receipt with no landed write breaks the "
            "receipt-totality bijection (no receipt without a landed write). "
            "Surfaced as a non-blocking receipt-totality witness; not promoted to "
            "authority."
        )
    return Finding(
        kind=RECEIPT_TOTALITY_OBSERVED_FINDING_CODE,
        role="observation",
        stage="apply",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": message,
            "arm": arm,
            "op_id": op_id,
            "jurisdiction": jurisdiction,
            "owner": "apply_seam_receipt_totality",
        },
    )


def check_receipt_totality(
    ledger: tuple[ReceiptLedgerEntry, ...],
    *,
    receipts_expected: bool,
    source_statute: str = "",
    jurisdiction: str = "",
) -> ReceiptTotalityReport:
    """Run the receipt-totality contract over an accumulated per-op ledger.

    The contract asserts *landed-writes ↔ receipts is a bijection* over the
    ledger — the receipt analogue of coverage-totality:

    * **missing receipt** (the missing-RHS arm) — a ledger entry that ``landed`` a
      write but carries no ``receipt``. This is a violation ONLY when
      ``receipts_expected`` (the profile emits receipts): a profile with
      ``emit_receipts=False`` intentionally lands writes with no receipt, which is
      a declared no-receipt fold, not a broken bijection.
    * **spurious receipt** (the spurious-RHS arm) — a ledger entry carrying a
      ``receipt`` whose op did NOT ``land`` a write. A receipt is the record of a
      LANDED write (write_receipt §4); one with no landed write is a lie the audit
      must see, so it is a violation under ANY ``receipts_expected`` setting.

    Returns a :class:`ReceiptTotalityReport` carrying the per-arm op-id witnesses
    and one non-blocking ``APPLY.RECEIPT_TOTALITY_OBSERVED`` observation per broken
    arm. A receipt-total ledger yields an empty ``findings`` tuple (observe-first:
    only a broken bijection is the witness). The function is PURE and
    dependency-light — it never mutates the ledger and never imports a frontend.
    """
    landed_writes = sum(1 for e in ledger if e.landed)
    receipts = sum(1 for e in ledger if e.receipt is not None)

    missing_receipt_op_ids: list[str] = []
    spurious_receipt_op_ids: list[str] = []
    for entry in ledger:
        has_receipt = entry.receipt is not None
        if entry.landed and not has_receipt and receipts_expected:
            missing_receipt_op_ids.append(entry.op_id)
        elif has_receipt and not entry.landed:
            spurious_receipt_op_ids.append(entry.op_id)

    findings: list[Finding] = []
    for op_id in missing_receipt_op_ids:
        findings.append(
            _totality_finding(
                arm="missing_receipt",
                op_id=op_id,
                source_statute=source_statute,
                jurisdiction=jurisdiction,
            )
        )
    for op_id in spurious_receipt_op_ids:
        findings.append(
            _totality_finding(
                arm="spurious_receipt",
                op_id=op_id,
                source_statute=source_statute,
                jurisdiction=jurisdiction,
            )
        )

    return ReceiptTotalityReport(
        landed_writes=landed_writes,
        receipts=receipts,
        missing_receipt_op_ids=tuple(missing_receipt_op_ids),
        spurious_receipt_op_ids=tuple(spurious_receipt_op_ids),
        findings=tuple(findings),
    )
