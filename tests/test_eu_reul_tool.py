from __future__ import annotations

import json
import textwrap

import pytest

from argparse import Namespace

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import cli
from lawvm.tools import eu_reul


def test_cli_parser_accepts_eu_reul_subcommands() -> None:
    parser = cli._build_parser()

    map_args = parser.parse_args(["eu-reul", "map", "32016R0679", "art/1/para/2"])
    assert map_args.command == "eu-reul"
    assert map_args.eu_reul_command == "map"
    assert map_args.celex == "32016R0679"
    assert map_args.eu_path == "art/1/para/2"

    resolve_args = parser.parse_args(
        [
            "eu-reul",
            "resolve",
            "retained-law://celex/32016R0679/article/1",
            "sample.xml",
        ]
    )
    assert resolve_args.command == "eu-reul"
    assert resolve_args.eu_reul_command == "resolve"
    assert resolve_args.uri == "retained-law://celex/32016R0679/article/1"


def test_eu_reul_map_command_prints_eid(capsys) -> None:
    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="map",
            celex="32016R0679",
            eu_path="art/1/para/2",
            json=False,
        )
    )

    assert capsys.readouterr().out.strip() == "eur_2016_679_article_1_paragraph_2"


def test_eu_reul_map_command_supports_json(capsys) -> None:
    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="map",
            celex="32016R0679",
            eu_path="article/1",
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["uk_eid"] == "eur_2016_679_article_1"


def test_eu_reul_map_command_trims_and_normalizes_path(capsys) -> None:
    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="map",
            celex=" 32016R0679 ",
            eu_path="  sec/ 1 /PAR/2  ",
            json=False,
        )
    )

    assert capsys.readouterr().out.strip() == "eur_2016_679_section_1_paragraph_2"


def test_eu_reul_rejects_invalid_uri(capsys) -> None:
    with pytest.raises(ValueError, match="uri must start with retained-law://celex"):
        eu_reul.main(
            Namespace(
                command="eu-reul",
                eu_reul_command="resolve",
                uri="invalid://not-retained-law",
                statute_xml="sample.xml",
                json=False,
            )
        )


def test_eu_reul_resolve_handles_query_variation_and_whitespace(monkeypatch, tmp_path, capsys) -> None:
    def _fake_parse(path: object, celex: str) -> IRStatute:
        return IRStatute(
            statute_id=celex,
            title="EU Test",
            body=IRNode(kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1",
                        children=(IRNode(kind=IRNodeKind.ITEM, label="1", text="nested"),),
                    ),),
            ),
        )

    monkeypatch.setattr(eu_reul, "parse_eu_regulation_ir", _fake_parse)

    xml_path = tmp_path / "fake_eu.xml"
    xml_path.write_text("<dummy/>")

    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="resolve",
            uri="  retained-law://celex/32016R0679/article/1?view=full#top  ",
            statute_xml=str(xml_path),
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["found"] is True
    node = payload["node"]
    assert node["label"] == "1"


def test_eu_reul_resolve_command_prints_node(monkeypatch, tmp_path, capsys) -> None:
    def _fake_parse(path: object, celex: str) -> IRStatute:
        assert celex == "32016R0679"
        assert str(path).endswith("sample.xml")
        return IRStatute(
            statute_id=celex,
            title="EU Test",
            body=IRNode(kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1",
                        children=(IRNode(kind=IRNodeKind.ITEM, label="1", text="Nested point"),),
                    ),),
            ),
        )

    monkeypatch.setattr(eu_reul, "parse_eu_regulation_ir", _fake_parse)
    uri = "retained-law://celex/32016R0679/article/1/point/1"
    xml_path = tmp_path / "sample.xml"
    xml_path.write_text("<dummy/>")

    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="resolve",
            uri=uri,
            statute_xml=str(xml_path),
            json=False,
        )
    )

    output = capsys.readouterr().out.strip().splitlines()
    assert output[0] == "item:1"
    assert output[1] == "Nested point"


def test_eu_reul_resolve_command_supports_json(monkeypatch, tmp_path, capsys) -> None:
    def _fake_parse(path: object, celex: str) -> IRStatute:
        return IRStatute(
            statute_id=celex,
            title="EU Test",
            body=IRNode(kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1", children=(IRNode(kind=IRNodeKind.ITEM, label="1"),)),),
            ),
        )

    monkeypatch.setattr(eu_reul, "parse_eu_regulation_ir", _fake_parse)
    xml_path = tmp_path / "sample.xml"
    xml_path.write_text("<dummy/>")

    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="resolve",
            uri="retained-law://celex/32016R0679/article/1/point/1",
            statute_xml=str(xml_path),
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["found"] is True
    node = payload["node"]
    assert node["kind"] == "item"
    assert node["label"] == "1"


def test_eu_reul_resolve_command_supports_json_with_real_xml(tmp_path, capsys) -> None:
    xml = textwrap.dedent(
        """\
        <ACT>
          <TITLE>Test EU Regulation</TITLE>
          <ENACTING.TERMS>
            <ARTICLE IDENTIFIER="art_1">
              <TI.ART>Article 1</TI.ART>
              <P>(1) First article text.</P>
            </ARTICLE>
          </ENACTING.TERMS>
        </ACT>
        """
    )
    xml_path = tmp_path / "32016R0679.xml"
    xml_path.write_text(xml)

    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="resolve",
            uri="retained-law://celex/32016R0679/article/1",
            statute_xml=str(xml_path),
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["found"] is True
    node = payload["node"]
    assert node["kind"] == "section"
    assert node["label"] == "1"
    assert "First article text." in (node.get("text", ""))


def test_eu_reul_resolve_command_returns_not_found(tmp_path, capsys) -> None:
    xml = "<ACT><TITLE>Test</TITLE></ACT>"
    xml_path = tmp_path / "blank.xml"
    xml_path.write_text(xml)

    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="resolve",
            uri="retained-law://celex/32016R0679/article/1/point/1",
            statute_xml=str(xml_path),
            json=False,
        )
    )

    assert capsys.readouterr().out.strip() == "not_found"


def test_eu_reul_resolve_command_supports_json_not_found(tmp_path, capsys) -> None:
    xml = "<ACT><TITLE>Test</TITLE></ACT>"
    xml_path = tmp_path / "blank.xml"
    xml_path.write_text(xml)

    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="resolve",
            uri="retained-law://celex/32016R0679/article/1",
            statute_xml=str(xml_path),
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["found"] is False
    assert payload["eu_statute_id"] == "32016R0679"


def test_eu_reul_resolve_rejects_invalid_path_depth(tmp_path) -> None:
    xml = "<ACT><TITLE>Test</TITLE></ACT>"
    xml_path = tmp_path / "blank.xml"
    xml_path.write_text(xml)

    with pytest.raises(ValueError, match="uri must match pattern"):
        eu_reul.main(
            Namespace(
                command="eu-reul",
                eu_reul_command="resolve",
                uri="retained-law://celex/32016R0679/article",
                statute_xml=str(xml_path),
                json=False,
            )
        )


def test_eu_reul_resolve_supports_case_insensitive_scheme_and_celex_query_tail(
    monkeypatch, tmp_path, capsys
) -> None:
    def _fake_parse(path: object, celex: str) -> IRStatute:
        return IRStatute(
            statute_id=celex,
            title="EU Test",
            body=IRNode(kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1", children=(IRNode(kind=IRNodeKind.ITEM, label="1", text="Nested point"),)),),
            ),
        )

    monkeypatch.setattr(eu_reul, "parse_eu_regulation_ir", _fake_parse)

    xml_path = tmp_path / "sample.xml"
    xml_path.write_text("<dummy/>")

    eu_reul.main(
        Namespace(
            command="eu-reul",
            eu_reul_command="resolve",
            uri="RETAINED-LAW://CELEX/32016R0679/ARTICLE/1/POINT/1?x=1#top",
            statute_xml=str(xml_path),
            json=False,
        )
    )

    output = capsys.readouterr().out.strip().splitlines()
    assert output == ["item:1", "Nested point"]


def test_eu_reul_main_rejects_unknown_subcommand(tmp_path) -> None:
    with pytest.raises(SystemExit):
        eu_reul.main(
            Namespace(
                command="eu-reul",
                eu_reul_command="invalid",
                uri="retained-law://celex/32016R0679/article/1",
                statute_xml=str(tmp_path / "noop.xml"),
                json=False,
            )
        )
