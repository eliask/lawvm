from __future__ import annotations

import sys
from types import SimpleNamespace

import scripts.uk_broad_baseline as uk_broad_baseline


class _FakeNode:
    def __init__(self, *, eid: str = "", children: tuple["_FakeNode", ...] = ()) -> None:
        self.attrs = {"eId": eid} if eid else {}
        self.text = ""
        self.children = children


def test_score_one_reports_too_small_current_as_source_frontier(monkeypatch) -> None:
    class FakeFarchive:
        def __init__(self, _path):
            pass

        def get(self, locator: str) -> bytes | None:
            if locator.endswith("/enacted/data.xml"):
                return b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""
            if locator.endswith("/data.xml"):
                return b"HTTP 300 Multiple Choices"
            return None

        def close(self) -> None:
            pass

    monkeypatch.setitem(
        sys.modules,
        "farchive",
        SimpleNamespace(Farchive=FakeFarchive),
    )

    row = uk_broad_baseline.score_one("ukpga/1945/9")

    assert row["score_status"] == "source_frontier"
    assert row["source_frontier_reason"] == "oracle_too_small"
    assert row["base_source_status"] == "available"
    assert row["oracle_source_status"] == "too_small"
    assert "error" not in row


def test_score_one_reports_empty_oracle_eids_as_source_frontier(monkeypatch) -> None:
    xml = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""

    class FakeFarchive:
        def __init__(self, _path):
            pass

        def get(self, locator: str) -> bytes | None:
            if locator.endswith("/enacted/data.xml") or locator.endswith("/data.xml"):
                return xml
            return None

        def close(self) -> None:
            pass

    def fake_parse(_xml: bytes, *, statute_id: str):
        return SimpleNamespace(body=_FakeNode(), supplements=())

    monkeypatch.setitem(sys.modules, "farchive", SimpleNamespace(Farchive=FakeFarchive))
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes",
        fake_parse,
    )

    row = uk_broad_baseline.score_one("ukpga/1938/22")

    assert row["score_status"] == "source_frontier"
    assert row["source_frontier_reason"] == "oracle_eids_empty"
    assert row["n_oracle"] == 0
    assert "error" not in row
