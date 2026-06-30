"""eu_ordering.py — EU date-of-application ordering profile.

Today EU ordering is LEXICAL-by-CELEX (design §1.6): ``discover_affecting_acts``
returns ``sorted(set(celexes))`` and ops carry no effective date, so
``op_sort_date`` collapses to the empty string for every op. That makes
same-moment detection vacuous and chronology unenforced.

This module supplies the EU :class:`OrderingProfile` whose ``temporal_key`` keys
on ``OperationSource.effective`` — the amending act's DATE-OF-APPLICATION
(threaded by ``fmx4_amendment_grammar.lower_amending_act``). With that key,
``order_ops`` (the shared core algebra) sorts amending acts in legal-chronological
order and the shared cross-act same-moment detector fires whenever two amending
acts share a date-of-application touching the same provision.

No core change is required: ``core.op_ordering.OrderingProfile`` already accepts
a ``temporal_key`` callable (NO/UK use the same seam). EU keeps its ordering
policy frontend-local, mirroring ``estonia/ordering.py`` and the NO profile.
"""

from __future__ import annotations

from lawvm.core.ir import LegalOperation
from lawvm.core.op_ordering import OrderingProfile, OrderedOps, order_ops

__all__ = ["eu_temporal_key", "eu_ordering_profile", "order_eu_ops"]


def eu_temporal_key(op: LegalOperation) -> tuple[str, str, int]:
    """EU temporal sort key: ``(date_of_application, amending_celex, sequence)``.

    ``OperationSource.effective`` carries the amending act's date-of-application
    (the legal instant its provisions apply — design §3.5). ``statute_id`` (the
    amending CELEX) is a deterministic secondary so two acts sharing a date sort
    by a stable id, and ``sequence`` is the universal stable tertiary. Ops with
    no date (honest gap) sort first under the empty string — they are NOT
    silently dropped, and the gap is visible to the same-moment detector.
    """
    effective = op.source.effective if op.source and op.source.effective else ""
    source_id = op.source.statute_id if op.source and op.source.statute_id else ""
    return (effective, source_id, op.sequence)


def eu_ordering_profile() -> OrderingProfile:
    """The EU ordering profile: date-of-application temporal key + EU finder prefix.

    ``finder_kind_prefix="eu"`` keeps EU same-moment findings frontend-distinct.
    No precedence-claim registry yet (EU has no validated precedence rules in
    Increment 0), no lexical-posterior tiebreak, no renumber-vacate stage.
    """
    return OrderingProfile(
        finder_kind_prefix="eu",
        temporal_key=eu_temporal_key,
    )


def order_eu_ops(ops: list[LegalOperation]) -> OrderedOps:
    """Order EU ops by date-of-application via the shared core algebra."""
    return order_ops(ops, eu_ordering_profile())
