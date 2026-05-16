from __future__ import annotations

from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.tools.cli import _build_parser


def test_parse_nz_source_document_extracts_nodes_and_history() -> None:
    xml = b"""\
<act id="ACT1" date.as.at="2026-04-05" date.assent="1957-10-24" year="1957" act.no="87" act.type="public">
  <cover><title>Example Act 1957</title></cover>
  <body>
    <part id="P1">
      <label>1</label>
      <heading>Preliminary</heading>
      <prov id="S1">
        <label>1</label>
        <heading>Title</heading>
        <prov.body><subprov id="S1-1"><label>1</label><para><text>This Act is an example.</text></para></subprov></prov.body>
        <notes>
          <history-note id="HN1">
            <amended-provision>Section 1</amended-provision>
            <amending-operation>amended</amending-operation>
            <amendment-date>1 January 2025</amendment-date>
            <amending-provision href="LMS1">section 3</amending-provision>
            <amending-leg>Example Amendment Act 2025</amending-leg>
            Section 1: amended, on 1 January 2025, by section 3 of the Example Amendment Act 2025 (2025 No 4).
          </history-note>
        </notes>
      </prov>
    </part>
  </body>
</act>
"""

    document = parse_nz_source_document(xml, xml_locator="loc", version_id="vid")

    assert document.metadata["title"] == "Example Act 1957"
    assert document.summary()["nodes"] == 3
    assert document.summary()["node_kinds"] == {"part": 1, "prov": 1, "subprov": 1}
    prov = [node for node in document.nodes if node.kind == "prov"][0]
    assert prov.path == ("part:1", "prov:1")
    assert prov.xml_path == "/act/body/part/prov"
    assert prov.source_zone == "primary_body"
    assert prov.heading == "Title"
    assert prov.history[0].operation == "amended"
    assert prov.history[0].amendment_date_iso == "2025-01-01"
    assert prov.history[0].amending_work_id == "act_public_2025_4"
    assert prov.history[0].amending_provision_hrefs == ("LMS1",)
    assert "Section 1: amended" not in prov.text


def test_parse_nz_source_document_records_deletion_status() -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S2" deletion-status="repealed"><label>2</label><heading>Old section</heading></prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)

    assert document.nodes[0].deletion_status == "repealed"
    assert document.summary()["deleted_nodes"] == 1


def test_parse_nz_source_document_indexes_label_para_nodes_as_source_structure() -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov id="S21-1"><label>1</label>
        <para><text>Intro:</text>
          <label-para><label>a</label><para><text>Paragraph text:</text>
            <label-para><label>i</label><para><text>Subparagraph text.</text></para></label-para>
          </para></label-para>
        </para>
      </subprov>
    </prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)

    assert document.summary()["node_kinds"] == {"label-para": 2, "prov": 1, "subprov": 1}
    assert [node.path for node in document.nodes if node.kind == "label-para"] == [
        ("prov:21", "subprov:1", "label-para:a"),
        ("prov:21", "subprov:1", "label-para:a", "label-para:i"),
    ]


def test_parse_nz_source_document_preserves_non_structural_history_notes() -> None:
    xml = b"""\
<act>
  <cover><title>Example Act 1957</title></cover>
  <end>
    <notes>
      <history-note id="HN-front">
        Long Title: repealed, on 1 January 2025, by section 4 of the Example Amendment Act 2025 (2025 No 4).
      </history-note>
    </notes>
  </end>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading></prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)

    assert document.summary()["history_witnesses"] == 1
    assert document.document_history[0].xml_id == "HN-front"
    assert document.document_history[0].amending_work_id == "act_public_2025_4"


def test_parse_nz_source_document_marks_end_skeleton_nodes_without_dropping_them() -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Live title</heading></prov>
  </body>
  <end>
    <skeletons>
      <skeleton.act>
        <skeleton.act.body>
          <prov id="SK1"><label>1</label><heading>Historical title</heading></prov>
        </skeleton.act.body>
      </skeleton.act>
    </skeletons>
  </end>
</act>
"""

    document = parse_nz_source_document(xml)

    assert document.summary()["nodes"] == 2
    assert [node.source_zone for node in document.nodes] == ["primary_body", "end_skeleton"]
    assert document.nodes[1].xml_path == "/act/end/skeletons/skeleton.act/skeleton.act.body/prov"


def test_nz_source_summary_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "source-summary", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "source-summary"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
