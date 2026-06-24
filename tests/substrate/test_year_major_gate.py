"""Tests for the strict year-major statute-id gate at the pack CLI boundary.

The ruling: ``pack-work``/``pack-corpus`` accept ONLY year-major ``year/num``
ids (e.g. ``2004/301``, ``1889/39``). The Finnish num/year citation form
(``301/2004``) and bare numbers are rejected with a clear NAMED diagnostic
(``FI_STATUTE_ID_NOT_YEAR_MAJOR``) — never silently swapped. Sub-numbered tails
(``1889/39-001``) are allowed as long as the year is still first.
"""
from __future__ import annotations

import pytest

from lawvm.finland.statute_id import StatuteIdError, require_year_major


@pytest.mark.parametrize(
    "good",
    [
        "2004/301",
        "1889/39",
        "1889/39-001",  # sub-numbered tail, year still first
        "1734/1",
        "2200/9999",
        "  2004/301  ",  # surrounding whitespace tolerated
    ],
)
def test_year_major_accepted(good: str) -> None:
    assert require_year_major(good) == good.strip()


@pytest.mark.parametrize(
    "bad",
    [
        "301/2004",  # num/year citation form — the core hazard
        "39/1889",  # num/year, must NOT be swapped
        "301",  # bare number
        "301/12",  # neither component a plausible year
        "0301/2004",  # first component not a 4-digit year value in range
        "1700/39",  # year below 1734 window
        "2300/39",  # year above 2200 window
        "",  # empty
    ],
)
def test_non_year_major_rejected(bad: str) -> None:
    with pytest.raises(StatuteIdError) as exc:
        require_year_major(bad)
    assert "FI_STATUTE_ID_NOT_YEAR_MAJOR" in str(exc.value)


def test_rejection_never_swaps_silently() -> None:
    """The num/year form must raise, not return the swapped year-major id."""
    with pytest.raises(StatuteIdError):
        require_year_major("301/2004")


def test_cli_pack_work_rejects_num_year(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI pack-work dispatch applies the gate before any replay runs."""
    from lawvm.tools import cli as cli_mod

    # If the gate fired, export_work_pack must never be reached.
    def _boom(*_a: object, **_k: object) -> object:  # pragma: no cover - guard
        raise AssertionError("export_work_pack ran despite num/year id")

    monkeypatch.setattr("lawvm.substrate.exporter.export_work_pack", _boom)
    monkeypatch.setattr(
        "sys.argv",
        ["lawvm", "-j", "fi", "pack-work", "301/2004", "--out", "/tmp/should-not-be-written"],
    )
    with pytest.raises(StatuteIdError) as exc:
        cli_mod._main_impl()
    assert "FI_STATUTE_ID_NOT_YEAR_MAJOR" in str(exc.value)


def test_cli_pack_work_accepts_year_major(monkeypatch: pytest.MonkeyPatch) -> None:
    """A year-major id passes the gate and reaches export_work_pack."""
    from lawvm.tools import cli as cli_mod

    called: dict[str, object] = {}

    def _capture(work_id: str, out: object, **_k: object) -> object:
        called["work_id"] = work_id
        raise SystemExit(0)  # short-circuit before real replay

    monkeypatch.setattr("lawvm.substrate.exporter.export_work_pack", _capture)
    monkeypatch.setattr(
        "sys.argv",
        ["lawvm", "-j", "fi", "pack-work", "1889/39", "--out", "/tmp/x"],
    )
    with pytest.raises(SystemExit):
        cli_mod._main_impl()
    assert called["work_id"] == "1889/39"
