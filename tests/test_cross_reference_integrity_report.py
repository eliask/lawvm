"""Tests for the cross-reference-integrity Markdown renderer.

The renderer is a PURE function of a ``DanglingReferenceReport`` plus a scope
label and a display cap. These tests run on a SYNTHETIC report (corpus-free) and
pin the credibility-bearing properties:

* the methodology/limits section appears BEFORE the findings;
* a DANGLING row is rendered, an EXISTENCE_UNKNOWN is NEVER rendered as a finding
  (the no-false-positive discipline is inherited, not re-derived);
* a capped findings list states "showing top N of M" — no silent truncation;
* the render is deterministic (same report in, same Markdown out);
* the JSON round-trip re-asserts the report's totality/closed-status guards.
"""

from __future__ import annotations

import json

import pytest

from lawvm.tools import cross_reference_integrity_report as cri
from lawvm.tools.dangling_references import (
    REASON_DANGLING_ABSENT,
    REASON_UNKNOWN_ACT_ABSENT,
    STATUS_DANGLING,
    DanglingReferenceError,
    DanglingReferenceReport,
    DanglingReferenceRow,
)


def _dangling_row(
    *,
    source: str,
    source_ref: str,
    target: str,
    target_ref: str,
) -> DanglingReferenceRow:
    return DanglingReferenceRow(
        source_statute_id=source,
        source_provision_ref_str=source_ref,
        target_statute_id=target,
        target_provision_ref_str=target_ref,
        cite_confidence="exact",
        cite_kind="cross_statute",
        existence_status=STATUS_DANGLING,
        reason=REASON_DANGLING_ABSENT,
    )


def _report(rows: tuple[DanglingReferenceRow, ...], *, unknown: int = 0) -> DanglingReferenceReport:
    dangling = len(rows)
    present = 5
    checked = present + dangling + unknown
    return DanglingReferenceReport(
        total_rows=checked + 2,  # +2 non-resolved out-of-scope rows
        resolved_checked=checked,
        excluded_non_resolved={"statute_only": 1, "open": 1},
        present=present,
        dangling=dangling,
        existence_unknown=unknown,
        unknown_by_reason={REASON_UNKNOWN_ACT_ABSENT: unknown} if unknown else {},
        dangling_by_reason={REASON_DANGLING_ABSENT: dangling} if dangling else {},
        dangling_rows=rows,
    )


# ---------------------------------------------------------------------------
# Structure: methodology before findings; summary counts present.
# ---------------------------------------------------------------------------


def test_methodology_precedes_findings():
    rows = (
        _dangling_row(
            source="1994/750", source_ref="1994/750/12", target="1994/751",
            target_ref="1994/751/46",
        ),
    )
    md = cri.render_cross_reference_integrity_report(
        _report(rows), scope_label="unit-test slice"
    )
    assert "## Methodology & limits" in md
    assert "## Findings" in md
    assert md.index("## Methodology & limits") < md.index("## Findings")
    # The presented claim id is cited.
    assert cri.PRESENTED_CLAIM_ID in md
    # Scope label is rendered prominently.
    assert "unit-test slice" in md


def test_summary_counts_rendered():
    rows = (
        _dangling_row(
            source="2000/1", source_ref="2000/1/1", target="1999/9",
            target_ref="1999/9/5",
        ),
    )
    report = _report(rows, unknown=3)
    md = cri.render_cross_reference_integrity_report(report, scope_label="x")
    assert "PRESENT" in md and "DANGLING" in md and "EXISTENCE_UNKNOWN" in md
    # The three-way counts appear.
    assert "| PRESENT |" in md
    assert "| DANGLING |" in md
    assert "| EXISTENCE_UNKNOWN |" in md


# ---------------------------------------------------------------------------
# No-false-positive discipline: EXISTENCE_UNKNOWN never appears as a finding.
# ---------------------------------------------------------------------------


def test_existence_unknown_not_a_finding():
    # A report with ONLY unknowns and no dangling rows: findings section says none.
    report = _report(rows=(), unknown=7)
    md = cri.render_cross_reference_integrity_report(report, scope_label="x")
    assert "No dangling cross-references were found" in md
    # The unknown count is disclosed in the summary/methodology, but never as a
    # "Target act ... dangling reference(s) into it" finding heading.
    assert "dangling reference(s) into it" not in md


# ---------------------------------------------------------------------------
# No silent truncation: a capped list states "showing top N of M".
# ---------------------------------------------------------------------------


def test_cap_states_top_n_of_m():
    rows = tuple(
        _dangling_row(
            source=f"2000/{i}", source_ref=f"2000/{i}/1", target="1999/9",
            target_ref=f"1999/9/{i}",
        )
        for i in range(10)
    )
    md = cri.render_cross_reference_integrity_report(
        _report(rows), scope_label="x", top=3
    )
    assert "Showing top 3 of 10" in md
    # The shortfall is explicitly disclosed, not silently dropped.
    assert "7 further dangling reference(s) are not shown" in md


def test_uncapped_shows_all():
    rows = tuple(
        _dangling_row(
            source=f"2000/{i}", source_ref=f"2000/{i}/1", target="1999/9",
            target_ref=f"1999/9/{i}",
        )
        for i in range(4)
    )
    md = cri.render_cross_reference_integrity_report(
        _report(rows), scope_label="x", top=100
    )
    assert "Showing top 4 of 4" in md
    assert "further dangling reference(s) are not shown" not in md


# ---------------------------------------------------------------------------
# Determinism: same report in, same Markdown out.
# ---------------------------------------------------------------------------


def test_render_is_deterministic():
    rows = tuple(
        _dangling_row(
            source=f"2000/{i}", source_ref=f"2000/{i}/1", target=f"199{i % 3}/9",
            target_ref=f"199{i % 3}/9/{i}",
        )
        for i in range(8)
    )
    report = _report(rows)
    a = cri.render_cross_reference_integrity_report(report, scope_label="s", top=5)
    b = cri.render_cross_reference_integrity_report(report, scope_label="s", top=5)
    assert a == b


def test_negative_top_rejected():
    with pytest.raises(ValueError):
        cri.render_cross_reference_integrity_report(
            _report(rows=()), scope_label="x", top=-1
        )


# ---------------------------------------------------------------------------
# Grouping by target: most-cited absent target surfaces first.
# ---------------------------------------------------------------------------


def test_group_by_target_orders_by_count_then_id():
    rows = (
        _dangling_row(source="a/1", source_ref="a/1/1", target="1999/2", target_ref="1999/2/1"),
        _dangling_row(source="a/2", source_ref="a/2/1", target="1999/1", target_ref="1999/1/1"),
        _dangling_row(source="a/3", source_ref="a/3/1", target="1999/1", target_ref="1999/1/2"),
    )
    groups = cri._group_dangling_by_target(rows)
    # target 1999/1 has 2 rows -> first; 1999/2 has 1 -> second.
    assert [g[0] for g in groups] == ["1999/1", "1999/2"]


# ---------------------------------------------------------------------------
# JSON round-trip re-asserts the underlying report guards.
# ---------------------------------------------------------------------------


def test_load_report_from_json_round_trip(tmp_path):
    rows = (
        _dangling_row(
            source="1994/750", source_ref="1994/750/12", target="1994/751",
            target_ref="1994/751/46",
        ),
    )
    report = _report(rows, unknown=2)
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(report.to_canonical_dict(), ensure_ascii=False), encoding="utf-8"
    )
    loaded = cri._load_report_from_json(str(path))
    assert loaded.dangling == report.dangling
    assert loaded.present == report.present
    assert loaded.existence_unknown == report.existence_unknown
    assert loaded.dangling_rows[0].target_provision_ref_str == "1994/751/46"


def test_load_report_from_json_refuses_non_summing_partition(tmp_path):
    # A corrupt JSON whose counts do not sum must be refused by the report guard.
    bad = {
        "total_rows": 10,
        "resolved_checked": 10,
        "excluded_non_resolved": {},
        "present": 5,
        "dangling": 2,
        "existence_unknown": 2,  # sums to 9, not 10
        "unknown_by_reason": {},
        "dangling_by_reason": {},
        "dangling_rows": [],
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(DanglingReferenceError):
        cri._load_report_from_json(str(path))


# ---------------------------------------------------------------------------
# Honesty boundary: the module docstring declares it adds no new authority.
# ---------------------------------------------------------------------------


def test_module_docstring_declares_presentation_only():
    doc = cri.__doc__ or ""
    flat = " ".join(doc.lower().split())  # collapse line-wraps before substring check
    assert "PRESENTATION" in doc
    assert "no new authority" in flat and "no new computation" in flat
    assert "EXISTENCE_UNKNOWN" in doc
    assert "not a legal conclusion" in doc.lower() or "NOT a ruling" in doc
