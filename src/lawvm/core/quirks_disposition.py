"""Closed vocabulary for the cross-jurisdiction ``quirks_disposition`` field.

``QuirksDisposition`` is the single typed carrier for the ``quirks_disposition``
discriminant that records how a quirky/edge-case operation was handled by the
replay/apply pipeline (applied, blocked, recorded-only, recorded-and-degraded,
selected against an existing identical op, skipped with a finding, …). It is
shared across jurisdictions (core/*, estonia/*, eu/*, finland/*, nz/uk/no/se).

Authority firewall rationale (mirrors ``lawvm.core.recovery_kind``): the value
flows through serialized evidence/diagnostic dicts and is compared against string
literals at consumer sites. When it was a free-form ``str`` a typo on either the
producer or the consumer side silently failed to match. Promoting it to a closed
``StrEnum`` makes the producer set and the consumer set the same checkable object
and fails loud on an unregistered member (``coerce_quirks_disposition``).

This is a TYPE migration, not a value rename: every member value equals the exact
string previously stored/serialized, so persisted evidence and replay output stay
byte-compatible (``StrEnum`` members serialize and compare as their bare string).
"""

from __future__ import annotations

from enum import StrEnum


class QuirksDisposition(StrEnum):
    """Closed set of quirk-handling dispositions.

    Members are the verbatim strings that producers assign to the
    ``quirks_disposition`` field and write into serialized evidence/diagnostic
    dicts, and that consumers compare against. Adding a producer site that emits a
    new disposition requires adding a member here (the unregistered-member
    coercion fails loud), keeping producer-set == consumer-set.
    """

    # Sentinel "unset" default for evidence rows constructed before a disposition
    # is known (kept byte-identical with the prior ``str = ""`` default).
    UNSET = ""

    APPLY = "apply"
    BLOCK = "block"
    REJECT = "reject"
    SKIP = "skip"
    WARN = "warn"

    CANDIDATE_HANDLED_ELSEWHERE = "candidate_handled_elsewhere"
    CANDIDATE_ONLY = "candidate_only"
    CANDIDATE_ONLY_PREFLIGHT = "candidate_only_preflight"

    RECORD = "record"
    RECORD_RESIDUAL_WITHOUT_REPAIRING_TO_ORACLE = "record_residual_without_repairing_to_oracle"
    RECORD_FAILURE = "record_failure"
    RECORD_DIVERGENCE = "record_divergence"
    RECORD_BLOCKED_CANDIDATE = "record_blocked_candidate"
    RECORD_BLOCKED_PREFLIGHT = "record_blocked_preflight"
    RECORD_DEGRADED = "record_degraded"
    RECORD_INSTRUCTION_WORKQUEUE = "record_instruction_workqueue"
    RECORD_UNSUPPORTED = "record_unsupported"
    RECORD_WITNESS_ONLY = "record_witness_only"

    SELECT_EXISTING_IDENTICAL = "select_existing_identical"
    SELECT_FIRST_IDENTICAL = "select_first_identical"

    SKIP_WITH_FINDING = "skip_with_finding"
    SUPPRESS_DUPLICATE_APPLY = "suppress_duplicate_apply"


class UnregisteredQuirksDisposition(ValueError):
    """A ``quirks_disposition`` string is not a registered member.

    Raised instead of silently failing a consumer-side match. The fix is always
    "add the missing member to ``QuirksDisposition``" so producer-set ==
    consumer-set.
    """

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(
            f"unregistered quirks_disposition {value!r}; "
            f"add it to lawvm.core.quirks_disposition.QuirksDisposition so "
            f"producer-set == consumer-set"
        )


def coerce_quirks_disposition(value: object) -> QuirksDisposition:
    """Coerce a stored/loaded string to a ``QuirksDisposition``, failing loud.

    Used at consumer boundaries where the value re-enters from an untyped
    ``Mapping``. An unrecognized string is a registration gap, never a silent
    no-match: raise ``UnregisteredQuirksDisposition``.
    """
    if isinstance(value, QuirksDisposition):
        return value
    try:
        return QuirksDisposition(str(value))
    except ValueError as exc:
        raise UnregisteredQuirksDisposition(str(value)) from exc


__all__ = [
    "QuirksDisposition",
    "UnregisteredQuirksDisposition",
    "coerce_quirks_disposition",
]
