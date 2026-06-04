from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
import sys
import types
from typing import Any

from lawvm.tools import dump
from lawvm.tools import source_dump


def test_dump_apply_replays_quietly(monkeypatch, capsys) -> None:
    called: dict[str, object] = {}

    def fake_replay_xml(statute_id: str, *, stop_before: str = "", quiet: bool = False):
        called["statute_id"] = statute_id
        called["stop_before"] = stop_before
        called["quiet"] = quiet
        return SimpleNamespace(
            serialize_text=lambda: "quiet dump text",
        )

    monkeypatch.setattr("lawvm.tools.dump.replay_xml", fake_replay_xml)

    dump.main(
        Namespace(
            statute_id="1991/1",
            after="apply",
            source=None,
            address=None,
            before="1992/1",
        )
    )

    assert called == {"statute_id": "1991/1", "stop_before": "1992/1", "quiet": True}
    out = capsys.readouterr().out
    assert "quiet dump text" in out


def test_dump_parse_routes_uk_statute_id_to_farchive(monkeypatch, tmp_path, capsys) -> None:
    xml = b"""<Legislation xmlns='http://www.legislation.gov.uk/namespaces/legislation'>
  <Primary>
    <Body>
      <P1 id='section-10'>
        <Pnumber>10</Pnumber>
        <P1para><Text>UK section ten text.</Text></P1para>
      </P1>
    </Body>
  </Primary>
</Legislation>
"""
    db_path = tmp_path / "uk_legislation.farchive"
    db_path.write_bytes(b"")
    seen: dict[str, Any] = {}

    class DummyArchive:
        def __init__(self, path):
            seen["path"] = path

        def get(self, locator: str) -> bytes | None:
            seen["locator"] = locator
            return xml

        def close(self) -> None:
            seen["closed"] = True

    fake_farchive = types.ModuleType("farchive")
    fake_farchive.Farchive = DummyArchive
    monkeypatch.setitem(sys.modules, "farchive", fake_farchive)

    dump.main(
        Namespace(
            statute_id="ukpga/2002/30",
            after="parse",
            source=None,
            address="section:10",
            before="",
            jurisdiction="fi",
            db=str(db_path),
        )
    )

    out = capsys.readouterr().out
    assert seen["path"] == db_path
    assert seen["locator"] == "https://www.legislation.gov.uk/ukpga/2002/30/enacted/data.xml"
    assert seen["closed"] is True
    assert "Stage    : PARSE (UK enacted source XML from farchive, no replay)" in out
    assert "Kind     : P1" in out
    assert "Label    : 10" in out
    assert "UK section ten text." in out


def test_source_dump_parse_routes_j_uk_to_farchive(monkeypatch, tmp_path) -> None:
    xml = b"""<Legislation xmlns='http://www.legislation.gov.uk/namespaces/legislation'>
  <Primary><Body><P1><Pnumber>10</Pnumber><P1para><Text>Native source.</Text></P1para></P1></Body></Primary>
</Legislation>
"""
    db_path = tmp_path / "uk_legislation.farchive"
    db_path.write_bytes(b"")

    class DummyArchive:
        def __init__(self, path):
            self.path = path

        def get(self, locator: str) -> bytes | None:
            return xml if locator.endswith("/ukpga/2002/30/enacted/data.xml") else None

        def close(self) -> None:
            return None

    fake_farchive = types.ModuleType("farchive")
    fake_farchive.Farchive = DummyArchive
    monkeypatch.setitem(sys.modules, "farchive", fake_farchive)

    bundle = source_dump.build_uk_source_dump(
        "ukpga/2002/30",
        "section:10",
        db_path=db_path,
    )

    assert bundle["jurisdiction"] == "uk"
    assert bundle["selected_kind"] == "P1"
    assert bundle["selected_label"] == "10"
    assert "Native source." in bundle["xml"]
