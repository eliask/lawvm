"""Witness-required-for-downgrade invariant.

A recurring silent-failure class: an adjudication that *downgrades* a finding
from a blocking bug-kind into a non-blocking observation — quietly, with no
recorded reason. The motivating case is a ``sort_order`` tree-invariant
violation reclassified as "spurious" (dropped from the blocking set) without a
recorded witness for WHY it was spurious; another is a UK compile rejection
reclassified ``blocking=False`` without a ``reclassification_reason``.

The general law (the discipline lifted to a machine-checked invariant):

    Any adjudication that places a finding reachable from a (blocking) bug-kind
    into a non-blocking bucket MUST carry a non-empty witness/provenance —
    a *rule id* that names the reclassification AND a *reason* that justifies it.

A downgrade with no witness is indistinguishable from a silent suppression: it
hides a real bug behind an unexplained "observation". Requiring a witness makes
every downgrade an auditable, attributable claim.

This module is the jurisdiction-agnostic primitive. A frontend constructs a
:class:`DowngradeRecord` per reclassification it performs; the invariant checks
it carries both a non-empty ``reclassification_rule_id`` (the type) and a
non-empty ``reclassification_reason`` (the justification). A *witnessed*
downgrade passes; a *witnessless* one fires.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class DowngradeWitnessError(ValueError):
    """A bug-kind was downgraded to a non-blocking observation without a witness."""


@dataclass(frozen=True, slots=True)
class DowngradeRecord:
    """One reclassification of a finding from blocking to non-blocking.

    Parameters
    ----------
    finding_id:
        Stable identifier for the reclassified finding (for error attribution).
    bug_kind:
        The original (blocking) bug-kind the finding was raised under
        (e.g. ``"sort_order"``, ``"replay_tree_invariant_violation"``,
        a UK lowering rejection reason code). This is what makes the downgrade
        *load-bearing*: a non-bug observation downgraded to non-blocking is fine;
        downgrading a *bug-kind* is the dangerous move that needs a witness.
    downgraded_to_nonblocking:
        Whether this record actually downgrades to non-blocking. ``False``
        records (the finding stayed blocking) need no witness and always pass.
    reclassification_rule_id:
        The witness TYPE — a named rule id for the reclassification. Required
        non-empty when ``downgraded_to_nonblocking`` and ``bug_kind`` is set.
    reclassification_reason:
        The witness JUSTIFICATION — a human-readable reason the downgrade is
        sound. Required non-empty when ``downgraded_to_nonblocking`` and
        ``bug_kind`` is set.
    """

    finding_id: str
    bug_kind: str
    downgraded_to_nonblocking: bool
    reclassification_rule_id: str = ""
    reclassification_reason: str = ""

    @property
    def requires_witness(self) -> bool:
        """A downgrade of a (non-empty) bug-kind to non-blocking needs a witness."""
        return self.downgraded_to_nonblocking and bool(self.bug_kind)

    @property
    def is_witnessed(self) -> bool:
        return bool(self.reclassification_rule_id) and bool(self.reclassification_reason)


def downgrade_witness_violation(record: DowngradeRecord) -> str | None:
    """Return a violation message if *record* is a witnessless downgrade, else None."""
    if not record.requires_witness:
        return None
    if record.is_witnessed:
        return None
    missing: list[str] = []
    if not record.reclassification_rule_id:
        missing.append("reclassification_rule_id")
    if not record.reclassification_reason:
        missing.append("reclassification_reason")
    return (
        f"finding {record.finding_id!r} (bug_kind {record.bug_kind!r}) was downgraded "
        f"to a non-blocking observation without {', '.join(missing)} — a witnessless "
        "downgrade is indistinguishable from a silent suppression of a real bug"
    )


def check_downgrade_witness(record: DowngradeRecord) -> None:
    """Raise :class:`DowngradeWitnessError` if *record* is a witnessless downgrade."""
    msg = downgrade_witness_violation(record)
    if msg is not None:
        raise DowngradeWitnessError(msg)


def downgrade_witness_violations(records: Iterable[DowngradeRecord]) -> list[str]:
    """Return every witnessless-downgrade message across *records* (empty == clean)."""
    out: list[str] = []
    for r in records:
        msg = downgrade_witness_violation(r)
        if msg is not None:
            out.append(msg)
    return out
