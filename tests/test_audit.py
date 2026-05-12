from __future__ import annotations

from argparse import Namespace
import json

from lawvm.tools import cli
from lawvm.tools import audit as audit_tools
from lawvm.tools.audit import _compare_html_vs_xml_sections


def test_compare_html_vs_xml_sections_reconciles_unique_scoped_aliases() -> None:
    cons_xml = """<?xml version="1.0" encoding="utf-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <chapter eId="chp_3">
        <num>3 luku</num>
        <section eId="chp_3__sec_7">
          <num>7 §</num>
        </section>
        <section eId="chp_3__sec_8">
          <num>8 §</num>
        </section>
      </chapter>
    </body>
  </act>
</akomaNtoso>
"""
    cons_data = cons_xml.encode("utf-8")

    cons_eids, missing_from_xml, extra_in_xml, noncommensurable_reason = _compare_html_vs_xml_sections(
        cons_data,
        ["7 §", "8 §"],
    )

    assert cons_eids == ["chp_3__sec_7", "chp_3__sec_8"]
    assert missing_from_xml == []
    assert extra_in_xml == []
    assert noncommensurable_reason == ""


def test_cli_parser_accepts_audit_html_json() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["audit", "html", "1994/1205", "--json"])
    assert args.command == "audit"
    assert args.audit_cmd == "html"
    assert args.statute_ids == ["1994/1205"]
    assert args.json is True


def test_cli_parser_accepts_multiple_audit_html_statutes() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["audit", "html", "1997/660", "2015/364", "--json"])
    assert args.command == "audit"
    assert args.audit_cmd == "html"
    assert args.statute_ids == ["1997/660", "2015/364"]
    assert args.json is True


def test_cli_parser_accepts_audit_html_exclude_range_headings() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["audit", "html", "1997/660", "--exclude-range-headings"])
    assert args.command == "audit"
    assert args.audit_cmd == "html"
    assert args.statute_ids == ["1997/660"]
    assert args.exclude_range_headings is True


def test_finlex_html_url_accepts_hyphenated_old_statute_ids() -> None:
    assert audit_tools._finlex_html_url("1901/15-001") == (
        "https://www.finlex.fi/fi/laki/ajantasa/1901/19010015"
    )


def test_cmd_html_json_emits_structured_results(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        audit_tools,
        "_audit_html_one",
        lambda sid: audit_tools.HtmlAuditResult(
            sid=sid,
            cons_sections=12,
            cons_eids=["sec_1"],
            html_sections=13,
            html_labels=["1 §", "17 a §"],
            html_error="",
            missing_from_xml=["17 a §"],
            extra_in_xml=[],
            noncommensurable_reason="",
        ),
    )

    audit_tools.cmd_html(Namespace(statute_ids=["1995/1552"], from_file=None, json=True))

    out = capsys.readouterr().out
    assert '"sid": "1995/1552"' in out
    assert '"missing_from_xml": [' in out
    assert '"17 a §"' in out


def test_cmd_html_json_excludes_range_heading_rows(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        audit_tools,
        "_audit_html_one",
        lambda sid: audit_tools.HtmlAuditResult(
            sid=sid,
            cons_sections=12,
            cons_eids=["sec_1"],
            html_sections=13,
            html_labels=["107 a–108 §"],
            html_error="",
            missing_from_xml=["107 a–108 §"],
            extra_in_xml=[],
            noncommensurable_reason="",
        ),
    )

    audit_tools.cmd_html(Namespace(statute_ids=["2010/182"], from_file=None, json=True, exclude_range_headings=True))

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["skipped_range_headings"] == 1
    assert payload["results"] == []
    assert payload["skipped_range_heading_statutes"] == [
        {
            "sid": "2010/182",
            "rule_id": "fi_audit_html_presentation_range_heading_excluded",
            "phase": "adjudication",
            "family": "presentation_cleanup",
            "reason": "HTML range-heading presentation quirk excluded from audit denominator",
            "html_labels": ["107 a–108 §"],
        },
    ]


def test_cmd_html_json_range_heading_skip_keeps_retained_rows(capsys, monkeypatch) -> None:
    def fake_audit(sid: str) -> audit_tools.HtmlAuditResult:
        if sid == "2010/182":
            return audit_tools.HtmlAuditResult(
                sid=sid,
                cons_sections=12,
                cons_eids=["sec_1"],
                html_sections=13,
                html_labels=["107 a–108 §"],
                html_error="",
                missing_from_xml=["107 a–108 §"],
                extra_in_xml=[],
                noncommensurable_reason="",
            )
        return audit_tools.HtmlAuditResult(
            sid=sid,
            cons_sections=1,
            cons_eids=["sec_1"],
            html_sections=1,
            html_labels=["1 §"],
            html_error="",
            missing_from_xml=[],
            extra_in_xml=[],
            noncommensurable_reason="",
        )

    monkeypatch.setattr(audit_tools, "_audit_html_one", fake_audit)

    audit_tools.cmd_html(
        Namespace(
            statute_ids=["2010/182", "1995/1552"],
            from_file=None,
            json=True,
            exclude_range_headings=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped_range_headings"] == 1
    assert [row["sid"] for row in payload["skipped_range_heading_statutes"]] == ["2010/182"]
    assert [row["sid"] for row in payload["results"]] == ["1995/1552"]


def test_audit_html_one_uses_structured_html_labels(monkeypatch) -> None:
    cons_xml = """<?xml version="1.0" encoding="utf-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <section eId="sec_1"><num>1 §</num></section>
      <section eId="sec_2"><num>2 §</num></section>
    </body>
  </act>
</akomaNtoso>
"""

    class _StubStore:
        def read_oracle(self, sid: str) -> bytes:
            assert sid == "1997/660"
            return cons_xml.encode("utf-8")

    import lawvm.finland.corpus as _corpus_mod
    monkeypatch.setattr(_corpus_mod, "get_corpus", lambda: _StubStore())
    monkeypatch.setattr(audit_tools, "_make_corpus_store", lambda: _StubStore())
    monkeypatch.setattr(
        audit_tools,
        "_structured_html_section_labels",
        lambda sid: (["1 §", "2 §"], ""),
    )

    result = audit_tools._audit_html_one("1997/660")

    assert result.html_labels == ["1 §", "2 §"]
    assert result.html_sections == 2
    assert result.missing_from_xml == []
    assert result.extra_in_xml == []
    assert result.noncommensurable_reason == ""


def test_corrigendum_count_reads_text_corpus(monkeypatch) -> None:
    monkeypatch.setattr(
        audit_tools,
        "load_patch_records",
        lambda: [
            {"lang": "fi", "statute_id": "1995/1552"},
            {"lang": "fi", "statute_id": "1995/1552"},
            {"lang": "sv", "statute_id": "1995/1552"},
            {"lang": "fi", "statute_id": "1997/660"},
        ],
    )

    assert audit_tools._corrigendum_count("1995/1552") == 2


def test_compare_html_vs_xml_sections_marks_duplicate_unscoped_oracle_labels_noncommensurable() -> None:
    cons_xml = """<?xml version="1.0" encoding="utf-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <part eId="part_1">
        <num>I osa</num>
        <chapter eId="part_1__chp_1">
          <num>1 luku</num>
          <section eId="part_1__chp_1__sec_1"><num>1 §</num></section>
        </chapter>
      </part>
      <part eId="part_2">
        <num>II osa</num>
        <chapter eId="part_2__chp_1">
          <num>1 luku</num>
          <section eId="part_2__chp_1__sec_1"><num>1 §</num></section>
        </chapter>
      </part>
    </body>
  </act>
</akomaNtoso>
"""
    cons_data = cons_xml.encode("utf-8")

    cons_eids, missing_from_xml, extra_in_xml, noncommensurable_reason = _compare_html_vs_xml_sections(
        cons_data,
        ["1 §"],
    )

    assert cons_eids == ["part_1__chp_1__sec_1", "part_2__chp_1__sec_1"]
    assert missing_from_xml == []
    assert extra_in_xml == []
    assert noncommensurable_reason == "duplicate_unscoped_oracle_labels:section:1"
