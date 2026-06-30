"""US same-moment cross-act conflict detection + ordering parity (task #105).

US joined the apply seam at char-span (task #86) but ``order_ops`` / same-moment
detection was DEFERRED: ``us_federal/dry_run.py`` sorted lowered Public-Law
reports by ``(enacted_date, statute_id)`` and applied them in that list order, so
two acts amending the SAME section at the SAME applied moment with incompatible
whole-target payloads were silently materialized by iteration order with ZERO
finding. This module pins the task #105 wiring:

  1. ORDERING PARITY — ``order_us_ops`` returns the lowered op stream in EXACTLY
     the prior enactment-sorted-report × ``operations()`` application order
     (byte-identical for non-colliding ops). The "old path" (the verbatim
     ``lowered_reports.sort`` + per-report ``operations()`` iteration) is
     reconstructed here and asserted identical to the kernel's ordered op list.

  2. SAME-MOMENT DETECTION — a genuine same-moment cross-act incompatible-payload
     collision now emits a BLOCKING ``us``-prefixed finding. Covers the dominant
     US case (undated-effective amendments that apply AT ENACTMENT — same enacted
     date, empty ``effective``), the explicit-effective-date case, and the
     no-false-positive cases (compatible payloads / distinct dates / one act).

Guard-liveness (AGENTS.md §2.9): drives synthesized ops through the production
``order_us_ops`` lane, not just the detector unit.
"""
from __future__ import annotations

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.us_federal.us_ordering import (
    order_us_ops,
    us_ordering_profile,
    us_same_moment_effective_date,
    us_temporal_key,
)

US_SAME_MOMENT_KIND = "us_same_moment_cross_act_incompatible_payload_ambiguous"


# ── op builders (mirror the US lowered op shape) ─────────────────────────────


def _addr(section: str) -> LegalAddress:
    return LegalAddress(path=(("title", "11"), ("section", section)))


def _replace(
    op_id: str,
    sequence: int,
    section: str,
    statute_id: str,
    enacted: str,
    *,
    effective: str = "",
    text: str = "new text",
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_addr(section),
        payload=IRNode(kind=IRNodeKind.SECTION, label=section, text=text),
        source=OperationSource(
            statute_id=statute_id, enacted=enacted, effective=effective
        ),
    )


def _repeal(
    op_id: str,
    sequence: int,
    section: str,
    statute_id: str,
    enacted: str,
    *,
    effective: str = "",
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=_addr(section),
        source=OperationSource(
            statute_id=statute_id, enacted=enacted, effective=effective
        ),
    )


# ── 1. Ordering parity (byte-identical application order) ────────────────────


def _old_path_order(
    reports: list[tuple[str, list[LegalOperation]]],
) -> list[LegalOperation]:
    """Reconstruct the prior ``dry_run.py`` enactment-order application stream.

    Verbatim of the old ``lowered_reports.sort(key=lambda x: (x[0] or x[1], x[1]))``
    over ``(report_enacted_or_statute_id, statute_id)`` followed by per-report
    ``operations()`` iteration — the order the kernel must reproduce.
    """
    sortable = []
    for statute_id, ops in reports:
        report_enacted = ops[0].source.enacted if ops and ops[0].source else ""
        primary = report_enacted or statute_id
        sortable.append((primary, statute_id, ops))
    sortable.sort(key=lambda x: (x[0] or x[1], x[1]))
    flat: list[LegalOperation] = []
    for _primary, _statute_id, ops in sortable:
        flat.extend(ops)
    return flat


def test_ordering_parity_preserves_old_enactment_order():
    # Three reports out of enactment order; each carries multiple ops, including
    # an instruction whose primary + extra ops SHARE a sequence (the stable-tie
    # case the kernel must preserve as input order).
    pl_b = [
        _replace("b#0", 1, "100", "PL 118-50", "2024-03-01"),
        _repeal("b#1", 2, "101", "PL 118-50", "2024-03-01"),
        _repeal("b#1#s0", 2, "102", "PL 118-50", "2024-03-01"),  # extra op, same seq
    ]
    pl_a = [
        _replace("a#0", 1, "200", "PL 118-10", "2024-01-15"),
        _replace("a#1", 2, "201", "PL 118-10", "2024-01-15"),
    ]
    pl_c = [
        _replace("c#0", 1, "300", "PL 118-99", "2024-06-20"),
    ]
    # Discovery order is arbitrary (dict iteration order of plaw_blobs).
    reports = [("PL 118-50", pl_b), ("PL 118-10", pl_a), ("PL 118-99", pl_c)]

    flat_discovery_order: list[LegalOperation] = []
    # The dry-run builds the flat stream from the OLD sorted reports; feed the
    # kernel that exact stream so stable-tie ordering is preserved.
    for op in _old_path_order(reports):
        flat_discovery_order.append(op)

    result = order_us_ops(flat_discovery_order)
    assert [op.op_id for op in result.ops] == [
        op.op_id for op in _old_path_order(reports)
    ]
    # No collision in this corpus → no findings (purely additive).
    assert result.findings == ()


def test_temporal_key_matches_old_sort_key():
    op = _replace("x", 7, "100", "PL 118-42", "2024-02-29")
    assert us_temporal_key(op) == ("2024-02-29", "PL 118-42", 7)
    # Empty enacted falls back to statute id (old ``x[0] or x[1]``).
    op2 = _replace("y", 3, "100", "PL 118-42", "")
    assert us_temporal_key(op2) == ("PL 118-42", "PL 118-42", 3)


def test_same_moment_effective_date_prefers_effective_then_enacted():
    enacted_only = _replace("e", 1, "100", "PL 1", "2024-01-01")
    assert us_same_moment_effective_date(enacted_only) == "2024-01-01"
    future_eff = _replace("f", 1, "100", "PL 1", "2024-01-01", effective="2025-07-01")
    assert us_same_moment_effective_date(future_eff) == "2025-07-01"


# ── 2. Same-moment cross-act detection ───────────────────────────────────────


def test_same_enacted_date_incompatible_replace_emits_blocking_finding():
    # Two DISTINCT acts both REPLACE section 100 with no explicit effective date —
    # they both apply AT ENACTMENT on the same date. The old order-dependent path
    # silently let the later-sequenced one win; the kernel now flags it.
    ops = [
        _replace("a", 1, "100", "PL 118-10", "2024-01-15", text="alpha"),
        _replace("b", 1, "100", "PL 118-20", "2024-01-15", text="beta"),
    ]
    result = order_us_ops(ops)
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.kind == US_SAME_MOMENT_KIND
    assert finding.blocking is True
    assert finding.op_id == ""  # cross-act evidence row, not a per-op skip
    assert finding.detail["effective_date"] == "2024-01-15"
    assert set(finding.detail["conflicting_affecting_acts"]) == {
        "PL 118-10",
        "PL 118-20",
    }


def test_repeal_vs_replace_same_enacted_date_emits_finding():
    ops = [
        _repeal("a", 1, "100", "PL 118-10", "2024-01-15"),
        _replace("b", 1, "100", "PL 118-20", "2024-01-15", text="beta"),
    ]
    result = order_us_ops(ops)
    assert len(result.findings) == 1
    assert result.findings[0].kind == US_SAME_MOMENT_KIND


def test_explicit_same_effective_date_emits_finding():
    # Different enacted dates but SAME parsed effective date → still a collision.
    ops = [
        _replace("a", 1, "100", "PL 118-10", "2023-12-01", effective="2025-01-01"),
        _replace("b", 1, "100", "PL 118-20", "2024-06-01", effective="2025-01-01"),
    ]
    result = order_us_ops(ops)
    assert len(result.findings) == 1
    assert result.findings[0].detail["effective_date"] == "2025-01-01"


def test_two_repeals_same_moment_not_flagged():
    # Two REPEALs are redundant destructive effects (same outcome) — no
    # order-decided winner to dispute, so NOT flagged.
    ops = [
        _repeal("a", 1, "100", "PL 118-10", "2024-01-15"),
        _repeal("b", 1, "100", "PL 118-20", "2024-01-15"),
    ]
    assert order_us_ops(ops).findings == ()


def test_distinct_enacted_dates_no_finding():
    # Same section, incompatible payloads, but DIFFERENT applied moments → no
    # same-moment collision (they are sequenced in time, not simultaneous).
    ops = [
        _replace("a", 1, "100", "PL 118-10", "2024-01-15", text="alpha"),
        _replace("b", 1, "100", "PL 118-20", "2024-02-20", text="beta"),
    ]
    assert order_us_ops(ops).findings == ()


def test_distinct_sections_no_finding():
    ops = [
        _replace("a", 1, "100", "PL 118-10", "2024-01-15", text="alpha"),
        _replace("b", 1, "101", "PL 118-20", "2024-01-15", text="beta"),
    ]
    assert order_us_ops(ops).findings == ()


def test_same_act_same_section_not_cross_act():
    # Two REPLACEs of the same section from the SAME act are intra-act ordering,
    # not a cross-act collision — the detector requires ≥2 distinct affecting acts.
    ops = [
        _replace("a", 1, "100", "PL 118-10", "2024-01-15", text="alpha"),
        _replace("b", 2, "100", "PL 118-10", "2024-01-15", text="beta"),
    ]
    assert order_us_ops(ops).findings == ()


def test_profile_shape():
    profile = us_ordering_profile()
    assert profile.finder_kind_prefix == "us"
    assert profile.temporal_key is us_temporal_key
    assert profile.same_moment_effective_date_of is us_same_moment_effective_date
    # No US-specific predicate / claim registry / lex tiebreak yet.
    assert profile.incompatible_payload_predicate is None
    assert profile.precedence_claims == ()
    assert profile.lex_posterior is False
