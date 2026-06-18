from __future__ import annotations

import argparse
import sys
import types
from dataclasses import dataclass

from lawvm.tools.delegate import _parse_type_filter, main


@dataclass(frozen=True)
class _Edge:
    section: str
    delegation_type: str
    match_text: str
    quote: str = ""


class _Corpus:
    def read_oracle(self, statute_id: str) -> bytes | None:
        if statute_id == "missing":
            return None
        return b"<akomaNtoso/>"


def _install_delegate_fakes(monkeypatch, edges: list[_Edge]) -> None:
    def extract_delegations(xml_bytes: bytes, statute_id: str) -> list[_Edge]:
        assert xml_bytes == b"<akomaNtoso/>"
        assert statute_id == "2009/953"
        return edges

    fake_delegation = types.SimpleNamespace(
        extract_delegations=extract_delegations,
        extract_asetus_authority=lambda xml_bytes, statute_id: [],
    )
    fake_corpus = types.SimpleNamespace(get_corpus=lambda: _Corpus())
    monkeypatch.setitem(sys.modules, "lawvm.finland.delegation", fake_delegation)
    monkeypatch.setitem(sys.modules, "lawvm.finland.corpus", fake_corpus)


def test_parse_type_filter_trims_whitespace_and_ignores_empty_parts() -> None:
    assert _parse_type_filter("vn_asetus, min_asetus,, agency ") == {
        "VN_ASETUS",
        "MIN_ASETUS",
        "AGENCY",
    }


def test_delegate_type_filter_accepts_spaces_after_commas(monkeypatch, capsys) -> None:
    _install_delegate_fakes(
        monkeypatch,
        [
            _Edge(section="1", delegation_type="VN_ASETUS", match_text="vn"),
            _Edge(section="2", delegation_type="MIN_ASETUS", match_text="min"),
            _Edge(section="3", delegation_type="AGENCY", match_text="agency"),
        ],
    )

    main(
        argparse.Namespace(
            statute_id="2009/953",
            type="vn_asetus, min_asetus",
            reverse=False,
            verbose=False,
        )
    )

    out = capsys.readouterr().out
    assert "2009/953: 2 delegation clause(s)" in out
    assert "§1 [VN_ASETUS]: vn" in out
    assert "§2 [MIN_ASETUS]: min" in out
    assert "§3 [AGENCY]: agency" not in out


def test_delegate_type_filter_reports_empty_filtered_result(monkeypatch, capsys) -> None:
    _install_delegate_fakes(
        monkeypatch,
        [_Edge(section="1", delegation_type="VN_ASETUS", match_text="vn")],
    )

    main(
        argparse.Namespace(
            statute_id="2009/953",
            type="agency",
            reverse=False,
            verbose=False,
        )
    )

    assert (
        capsys.readouterr().out
        == "2009/953: no delegation clauses found for type filter AGENCY.\n"
    )
