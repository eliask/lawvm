"""Unit tests for the shared Finnish date recognizer ``match_fi_date``.

The recognizer is the single owner of "what is a Finnish date in legislative
prose". It must return both the calendar VALUE and the morphological FORM
(essive / allative / dotted / year-end), because the lifecycle-minting callers
discriminate commencement vs expiry on the form.
"""
from __future__ import annotations

import datetime as dt

from lawvm.finland.fi_dates import FiDateForm, match_fi_date


def test_essive_commencement_form() -> None:
    m = match_fi_date("tulee voimaan 15 päivänä joulukuuta 1992")
    assert m is not None
    assert m.value == dt.date(1992, 12, 15)
    assert m.form is FiDateForm.ESSIVE


def test_allative_expiry_form() -> None:
    m = match_fi_date("on voimassa 31 päivään joulukuuta 2025")
    assert m is not None
    assert m.value == dt.date(2025, 12, 31)
    assert m.form is FiDateForm.ALLATIVE


def test_year_end_arm_resolves_to_dec_31() -> None:
    m = match_fi_date("on voimassa vuoden 2012 loppuun")
    assert m is not None
    assert m.value == dt.date(2012, 12, 31)
    assert m.form is FiDateForm.YEAR_END


def test_dotted_form() -> None:
    m = match_fi_date("tulee voimaan 1.3.2015")
    assert m is not None
    assert m.value == dt.date(2015, 3, 1)
    assert m.form is FiDateForm.DOTTED


def test_forms_filter_excludes_other_forms() -> None:
    # An allative expiry date is not picked up when only ESSIVE is eligible.
    assert match_fi_date(
        "on voimassa 31 päivään joulukuuta 2025", forms={FiDateForm.ESSIVE}
    ) is None


def test_earliest_eligible_form_wins() -> None:
    text = "tulee voimaan 1 päivänä tammikuuta 2020 ja on voimassa 31 päivään joulukuuta 2025"
    m = match_fi_date(text, forms={FiDateForm.ESSIVE, FiDateForm.ALLATIVE})
    assert m is not None
    assert m.value == dt.date(2020, 1, 1)
    assert m.form is FiDateForm.ESSIVE


def test_unknown_month_returns_none() -> None:
    assert match_fi_date("on voimassa 31 päivään ZZZkuuta 2025") is None


def test_dotted_day_tolerance_opt_in() -> None:
    # The dotted-ordinal day form ``15.päivänä`` is only accepted under the
    # opt-in flag (the fixed-term commencement path), not by default.
    assert match_fi_date("15.päivänä joulukuuta 1992", forms={FiDateForm.ESSIVE}) is None
    m = match_fi_date(
        "15.päivänä joulukuuta 1992",
        forms={FiDateForm.ESSIVE},
        tolerate_dotted_day=True,
    )
    assert m is not None
    assert m.value == dt.date(1992, 12, 15)


def test_no_date_returns_none() -> None:
    assert match_fi_date("tämä laki tulee voimaan myöhemmin säädettävänä ajankohtana") is None


def test_span_is_returned() -> None:
    m = match_fi_date("on voimassa 31 päivään joulukuuta 2025")
    assert m is not None
    assert m.start < m.end
    assert "31 päivään joulukuuta 2025" in "on voimassa 31 päivään joulukuuta 2025"[m.start : m.end]
