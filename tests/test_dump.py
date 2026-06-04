from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
import sys
import types
from typing import Any

import pytest

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


def test_dump_default_routes_uk_statute_id_to_farchive(monkeypatch, tmp_path, capsys) -> None:
    xml = b"""<Legislation xmlns='http://www.legislation.gov.uk/namespaces/legislation'>
  <Primary>
    <Body>
      <P1 id='section-10'>
        <Pnumber>10</Pnumber>
        <P1para><Text>Default UK dump reads archive source.</Text></P1para>
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
            after=None,
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
    assert "Default UK dump reads archive source." in out


def test_source_dump_parse_routes_j_uk_to_farchive(monkeypatch, tmp_path) -> None:
    xml = b"""<Legislation xmlns='http://www.legislation.gov.uk/namespaces/legislation'>
  <Primary><Body><P1><Pnumber>II</Pnumber><P1para><Text>Native source.</Text></P1para></P1></Body></Primary>
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
        "section:2",
        db_path=db_path,
    )

    assert bundle["jurisdiction"] == "uk"
    assert bundle["selected_kind"] == "P1"
    assert bundle["selected_label"] == "II"
    assert "Native source." in bundle["xml"]


def test_source_dump_uk_parse_finds_metadata_matched_archived_leaf_without_direct_locator(
    monkeypatch,
    tmp_path,
) -> None:
    leaf_locator = "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml"
    leaf_xml = b"""<Legislation xmlns='http://www.legislation.gov.uk/namespaces/legislation'
    xmlns:ukm='http://www.legislation.gov.uk/namespaces/metadata'>
  <ukm:Year Value='2002'/>
  <ukm:Number Value='30'/>
  <Primary><Body><P1><Pnumber>1</Pnumber><P1para><Text>Leaf-only source.</Text></P1para></P1></Body></Primary>
</Legislation>
"""
    db_path = tmp_path / "uk_legislation.farchive"
    db_path.write_bytes(b"")

    class DummyArchive:
        def __init__(self, path):
            self.path = path

        def get(self, locator: str) -> bytes | None:
            return leaf_xml if locator == leaf_locator else None

        def locators(self, pattern: str) -> list[str]:
            assert pattern == "%/enacted/data.xml"
            return [leaf_locator]

        def close(self) -> None:
            return None

    fake_farchive = types.ModuleType("farchive")
    fake_farchive.Farchive = DummyArchive
    monkeypatch.setitem(sys.modules, "farchive", fake_farchive)

    bundle = source_dump.build_uk_source_dump("ukpga/2002/30", db_path=db_path)

    assert bundle["source_url"] == leaf_locator
    assert bundle["source_resolution"] == "resolved_archived_enacted_candidate"
    assert "Leaf-only source." in bundle["xml"]


def test_dump_uk_parse_resolves_archived_multiple_choice_leaf_source(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    requested_locator = "https://www.legislation.gov.uk/ukpga/2002/30/enacted/data.xml"
    leaf_locator = "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml"
    multiple_choices = b"""Multiple Choices
The link that you've followed could mean either of the following:
<a href="/ukpga/Eliz2/3-4/18">Candidate leaf</a>
"""
    leaf_xml = b"""<Legislation xmlns='http://www.legislation.gov.uk/namespaces/legislation'
    xmlns:ukm='http://www.legislation.gov.uk/namespaces/metadata'>
  <ukm:Year Value='2002'/>
  <ukm:Number Value='30'/>
  <Primary>
    <Body>
      <P1><Pnumber>10</Pnumber><P1para><Text>Resolved leaf source.</Text></P1para></P1>
    </Body>
  </Primary>
</Legislation>
"""
    db_path = tmp_path / "uk_legislation.farchive"
    db_path.write_bytes(b"")
    seen: dict[str, Any] = {"get": []}

    class DummyArchive:
        def __init__(self, path):
            seen["path"] = path

        def get(self, locator: str) -> bytes | None:
            seen["get"].append(locator)
            return {
                requested_locator: multiple_choices,
                leaf_locator: leaf_xml,
            }.get(locator)

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
    assert seen["get"] == [requested_locator, leaf_locator]
    assert seen["closed"] is True
    assert f"Source   : {leaf_locator}" in out
    assert "Resolve  : resolved_archived_enacted_candidate" in out
    assert f"Requested: {requested_locator}" in out
    assert "Resolved leaf source." in out


def test_source_dump_uk_parse_rejects_ambiguous_archived_leaf_sources(
    monkeypatch,
    tmp_path,
) -> None:
    requested_locator = "https://www.legislation.gov.uk/ukpga/2002/30/enacted/data.xml"
    leaf_a = "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml"
    leaf_b = "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/19/enacted/data.xml"
    multiple_choices = b"""Multiple Choices
The link that you've followed could mean either of the following:
<a href="/ukpga/Eliz2/3-4/18">Candidate A</a>
<a href="/ukpga/Eliz2/4-5/19">Candidate B</a>
"""
    leaf_xml = b"""<Legislation xmlns:ukm='http://www.legislation.gov.uk/namespaces/metadata'>
  <ukm:Year Value='2002'/>
  <ukm:Number Value='30'/>
  <Primary><Body><P1><Pnumber>1</Pnumber><P1para><Text>Candidate.</Text></P1para></P1></Body></Primary>
</Legislation>
"""
    db_path = tmp_path / "uk_legislation.farchive"
    db_path.write_bytes(b"")

    class DummyArchive:
        def __init__(self, path):
            self.path = path

        def get(self, locator: str) -> bytes | None:
            return {
                requested_locator: multiple_choices,
                leaf_a: leaf_xml,
                leaf_b: leaf_xml,
            }.get(locator)

        def close(self) -> None:
            return None

    fake_farchive = types.ModuleType("farchive")
    fake_farchive.Farchive = DummyArchive
    monkeypatch.setitem(sys.modules, "farchive", fake_farchive)

    with pytest.raises(SystemExit) as exc_info:
        source_dump.build_uk_source_dump("ukpga/2002/30", db_path=db_path)

    message = str(exc_info.value)
    assert "ambiguous UK enacted XML candidates" in message
    assert leaf_a in message
    assert leaf_b in message


def test_source_dump_main_routes_j_uk_to_farchive(monkeypatch, tmp_path, capsys) -> None:
    xml = b"""<Legislation xmlns='http://www.legislation.gov.uk/namespaces/legislation'>
  <Primary>
    <Body>
      <P1 id='section-10'>
        <Pnumber>10</Pnumber>
        <P1para><Text>Main source dump reads farchive.</Text></P1para>
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

    source_dump.main(
        Namespace(
            statute_id="ukpga/2002/30",
            address="section:10",
            json=False,
            jurisdiction="uk",
            db=str(db_path),
        )
    )

    out = capsys.readouterr().out
    assert seen["path"] == db_path
    assert seen["locator"] == "https://www.legislation.gov.uk/ukpga/2002/30/enacted/data.xml"
    assert seen["closed"] is True
    assert "Stage    : PARSE (UK enacted source XML from farchive, no replay)" in out
    assert f"Archive  : {db_path}" in out
    assert "Main source dump reads farchive." in out
