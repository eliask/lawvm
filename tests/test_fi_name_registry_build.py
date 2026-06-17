"""Gate for the full-corpus statute-name registry builder + artifact loader.

Exercises serialize -> load round-trip and the builder's title/date extraction on
a SMALL hand-built input (NOT the 56k corpus — the full build is a CLI data step,
proven separately at scale). Asserts an inflected name resolves after a
build -> serialize -> load cycle.
"""

from __future__ import annotations

import datetime as dt

from lawvm.finland.references.registries.statute_name import (
    StatuteNameEntry,
    load_statute_name_entries,
    load_statute_name_registry,
    serialize_entries,
)
from lawvm.tools.build_statute_name_registry import _extract_title_and_date


# A minimal AKN source XML with a docTitle and a FRBRWork dateIssued.
def _akn_xml(title: str, *, date: str | None) -> bytes:
    frbr_date = (
        f'<FRBRdate date="{date}" name="dateIssued"/>' if date is not None else ""
    )
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><meta><identification><FRBRWork>"
        f"{frbr_date}"
        "</FRBRWork></identification></meta>"
        f"<preface><longTitle><docTitle>{title}</docTitle></longTitle></preface>"
        "</act></akomaNtoso>"
    ).encode("utf-8")


def test_extract_title_and_date_reads_frbr_dateissued() -> None:
    out = _extract_title_and_date(_akn_xml("Holhouslaki", date="1898-12-19"))
    assert out is not None
    title, valid_from = out
    assert title == "Holhouslaki"
    assert valid_from == dt.date(1898, 12, 19)


def test_extract_title_no_date_leaves_window_open() -> None:
    """Fail-loud: a corpus without dateIssued yields an OPEN valid_from, never guessed."""
    out = _extract_title_and_date(_akn_xml("Ulosottolaki", date=None))
    assert out is not None
    title, valid_from = out
    assert title == "Ulosottolaki"
    assert valid_from is None


def test_extract_no_doctitle_returns_none() -> None:
    xb = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><meta/></act></akomaNtoso>"
    ).encode("utf-8")
    assert _extract_title_and_date(xb) is None


def test_build_serialize_load_roundtrip_resolves_inflected(tmp_path) -> None:
    """build -> serialize -> load -> lookup resolves a GENERATED inflected surface."""
    entries = [
        StatuteNameEntry("1898/34-001", "Holhouslaki", dt.date(1898, 1, 1), None),
        StatuteNameEntry("1895/37-001", "Ulosottolaki", None, None),
        # ONE name, TWO acts over time (closed + open windows).
        StatuteNameEntry(
            "1995/365", "Kuntalaki", dt.date(1995, 7, 1), dt.date(2015, 5, 1)
        ),
        StatuteNameEntry("2015/410", "Kuntalaki", dt.date(2015, 5, 1), None),
    ]
    path = tmp_path / "statute_name_registry.jsonl"
    n = serialize_entries(entries, path, meta={"titles_indexed": len(entries)})
    assert n == len(entries)

    # Round-trip the raw entries (dates survive ISO serialization).
    loaded = load_statute_name_entries(path)
    assert {e.statute_id for e in loaded} == {
        "1898/34-001",
        "1895/37-001",
        "1995/365",
        "2015/410",
    }
    holhous = next(e for e in loaded if e.statute_id == "1898/34-001")
    assert holhous.valid_from == dt.date(1898, 1, 1)
    ulos = next(e for e in loaded if e.statute_id == "1895/37-001")
    assert ulos.valid_from is None  # open window preserved, not fabricated

    # The built registry resolves a GENERATED inflected surface (the genitive of
    # the head was never stored literally).
    reg = load_statute_name_registry(path)
    res = reg.lookup("Holhouslain")
    assert res.status == "single"
    assert res.candidates[0].statute_id == "1898/34-001"

    # Temporal disambiguation survives the round-trip.
    assert reg.lookup("Kuntalaki").status == "multiple"
    assert (
        reg.lookup("Kuntalaki", as_of=dt.date(2000, 1, 1)).candidates[0].statute_id
        == "1995/365"
    )


def test_load_rejects_wrong_artifact_kind(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"_meta": {"kind": "something_else"}}\n', encoding="utf-8")
    try:
        load_statute_name_entries(path)
    except ValueError as exc:
        assert "unexpected kind" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected ValueError on wrong artifact kind")
