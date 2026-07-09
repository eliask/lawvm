"""Hermetic test: the fi-parse-compare Level-2 de-facsimile A/B acceptance metric.

The intelligent XML-vs-PDF adjudicator ``_chat`` is mocked to return canned
categorized diffs for the baseline (mechanical stitch) and the de-facsimiled
reconstruction; acceptance (spec §2) is EXTRA + STRUCTURE strictly DOWN, MISSING
not up, NUMERIC unchanged. No network / no model.
"""
from __future__ import annotations

from lawvm.tools.fi_parse_compare import (
    XmlPdfDiffAdjudicator,
    evaluate_defacsimile_ab,
)


class _ScriptedAdjudicator(XmlPdfDiffAdjudicator):
    """Returns a canned reply keyed by a substring of the PDF (TEXT B) content."""

    def __init__(self, replies: dict[str, str]) -> None:
        super().__init__()
        self._replies = replies

    def _chat(self, system: str, user: str) -> str:
        for marker, reply in self._replies.items():
            if marker in user:
                self._last_finish_reason = "stop"
                return reply
        raise AssertionError("no canned reply matched the user prompt")


_BASELINE_REPLY = (
    "EXTRA: running header 'HE 1/2015 vp' repeated\n"
    "EXTRA: page number '1'\n"
    "STRUCTURE: paragraph split across page break\n"
    "VERDICT: minor-issues — furniture + split paragraph\n"
)
_DEFAC_REPLY = "VERDICT: faithful — clean reconstruction\n"


def test_defacsimile_ab_accepts_when_extra_and_structure_down() -> None:
    adj = _ScriptedAdjudicator(
        {"BASELINE_MARK": _BASELINE_REPLY, "DEFAC_MARK": _DEFAC_REPLY}
    )
    report = evaluate_defacsimile_ab(
        xml_text="Authoritative body.",
        baseline_pdf_text="BASELINE_MARK body with furniture",
        defacsimiled_text="DEFAC_MARK clean body",
        adjudicator=adj,
    )
    assert report.extra_delta == -2       # two EXTRA findings resolved
    assert report.structure_delta == -1   # split paragraph rejoined
    assert report.missing_delta == 0
    assert report.numeric_delta == 0
    assert report.accepted


def test_defacsimile_ab_rejects_when_missing_goes_up() -> None:
    # Over-dedup: the de-facsimile dropped body content → a new MISSING finding.
    over_dedup = (
        "MISSING: section 3 body text absent\n"
        "VERDICT: material-issues — dropped a section\n"
    )
    adj = _ScriptedAdjudicator(
        {"BASELINE_MARK": _BASELINE_REPLY, "DEFAC_MARK": over_dedup}
    )
    report = evaluate_defacsimile_ab(
        xml_text="Authoritative body.",
        baseline_pdf_text="BASELINE_MARK body",
        defacsimiled_text="DEFAC_MARK truncated",
        adjudicator=adj,
    )
    assert report.missing_delta == 1
    assert not report.accepted


def test_defacsimile_ab_rejects_when_numeric_changes() -> None:
    numeric_corrupt = (
        "NUMERIC: euro amount 500 became 5000\n"
        "VERDICT: material-issues — corrupted amount\n"
    )
    adj = _ScriptedAdjudicator(
        {"BASELINE_MARK": _BASELINE_REPLY, "DEFAC_MARK": numeric_corrupt}
    )
    report = evaluate_defacsimile_ab(
        xml_text="Authoritative body 500 €.",
        baseline_pdf_text="BASELINE_MARK body 500",
        defacsimiled_text="DEFAC_MARK body 5000",
        adjudicator=adj,
    )
    assert report.numeric_delta == 1
    assert not report.accepted


def test_defacsimile_ab_rejects_when_no_improvement() -> None:
    # Same diffs on both sides → EXTRA+STRUCTURE not strictly down → not accepted.
    adj = _ScriptedAdjudicator(
        {"BASELINE_MARK": _BASELINE_REPLY, "DEFAC_MARK": _BASELINE_REPLY}
    )
    report = evaluate_defacsimile_ab(
        xml_text="Authoritative body.",
        baseline_pdf_text="BASELINE_MARK body",
        defacsimiled_text="DEFAC_MARK body",
        adjudicator=adj,
    )
    assert report.extra_delta == 0 and report.structure_delta == 0
    assert not report.accepted
