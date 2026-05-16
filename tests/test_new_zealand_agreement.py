from __future__ import annotations

from lawvm.new_zealand.agreement import compare_source_documents
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.tools.cli import _build_parser


def test_compare_source_documents_reports_candidate_oracle_agreement_partitions() -> None:
    candidate_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>Same text</text></para></prov.body></prov>
    <prov id="S2"><label>2</label><heading>Changed</heading><prov.body><para><text>Candidate text</text></para></prov.body></prov>
    <prov id="S3"><label>3</label><heading>Candidate only</heading></prov>
  </body>
</act>
"""
    oracle_xml = b"""\
<act>
  <body>
    <prov id="S1O"><label>1</label><heading>Title</heading><prov.body><para><text>Same text</text></para></prov.body></prov>
    <prov id="S2O"><label>2</label><heading>Changed</heading><prov.body><para><text>Oracle text</text></para></prov.body></prov>
    <prov id="S4O"><label>4</label><heading>Oracle only</heading></prov>
  </body>
</act>
"""

    report = compare_source_documents(
        parse_nz_source_document(candidate_xml, xml_locator="candidate", version_id="candidate-v1"),
        parse_nz_source_document(oracle_xml, xml_locator="oracle", version_id="oracle-v1"),
    )

    assert report.to_jsonable()["report_kind"] == "candidate_oracle_source_tree_agreement"
    assert report.to_jsonable()["replay_claims"] is False
    assert report.summary()["agreement_status"] == "mismatch"
    assert report.summary()["status_counts"] == {
        "text_exact_identity_drift": 1,
        "changed": 1,
        "candidate_only": 1,
        "oracle_only": 1,
    }
    assert report.summary()["exact_ratio"] == 0.0


def test_compare_source_documents_reports_exact_agreement() -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>Same text</text></para></prov.body></prov>
  </body>
</act>
"""

    report = compare_source_documents(
        parse_nz_source_document(xml, xml_locator="candidate"),
        parse_nz_source_document(xml, xml_locator="oracle"),
    )

    assert report.summary()["agreement_status"] == "exact"
    assert report.summary()["exact_ratio"] == 1.0


def test_compare_source_documents_does_not_hide_history_drift() -> None:
    candidate_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>Same text</text></para></prov.body></prov>
  </body>
</act>
"""
    oracle_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>Same text</text></para></prov.body>
      <notes><history-note>Section 1: amended, on 1 January 2025, by section 3 of the Example Amendment Act 2025 (2025 No 4).</history-note></notes>
    </prov>
  </body>
</act>
"""

    report = compare_source_documents(
        parse_nz_source_document(candidate_xml, xml_locator="candidate"),
        parse_nz_source_document(oracle_xml, xml_locator="oracle"),
    )

    assert report.summary()["status_counts"] == {"text_exact_history_drift": 1}
    assert report.rows[0].candidate_history_count == 0
    assert report.rows[0].oracle_history_count == 1


def test_compare_source_documents_preserves_duplicate_source_paths() -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>First duplicate</heading></prov>
    <prov id="S1A"><label>1</label><heading>Second duplicate</heading></prov>
  </body>
</act>
"""

    report = compare_source_documents(
        parse_nz_source_document(xml, xml_locator="candidate"),
        parse_nz_source_document(xml, xml_locator="oracle"),
    )

    assert report.summary()["rows"] == 2
    assert report.summary()["status_counts"] == {"exact": 2}
    assert [row.path for row in report.rows] == [
        ("prov:1", "source-duplicate:S1"),
        ("prov:1", "source-duplicate:S1A"),
    ]


def test_nz_agreement_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(
        [
            "nz-corpus",
            "agreement",
            "--candidate-xml-locator",
            "candidate",
            "--oracle-xml-locator",
            "oracle",
        ]
    )

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "agreement"
    assert args.db == "data/nz_legislation.farchive"
    assert args.candidate_xml_locator == "candidate"
    assert args.oracle_xml_locator == "oracle"
