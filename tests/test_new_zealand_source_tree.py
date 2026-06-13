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


def test_amend_instructions_split_multi_instruction_provision() -> None:
    # One amending provision, two ``<text>`` instructions, each an exact
    # omit/substitute keyed to its own ``<extref>`` target. The flattened prose
    # path would collapse this into one multi-clause blocker; the typed reader
    # must yield two distinct exact instructions.
    xml = b"""\
<act>
  <body>
    <prov id="A5"><label>5</label><heading>Section 11 amended</heading>
      <prov.body>
        <subprov><label>1</label><para><text>
          <citation jurisdiction="nz"><extref href="DLM1">Section 11(4)</extref></citation>
          is amended by omitting <amend.in>The Schedule</amend.in>
          and substituting <amend.in><citation jurisdiction="nz">Schedule 1</citation></amend.in>.
        </text></para></subprov>
        <subprov><label>2</label><para><text>
          <citation jurisdiction="nz"><extref href="DLM1">Section 11(5)</extref></citation>
          is amended by omitting <amend.in>old phrase</amend.in>
          and substituting <amend.in>new phrase</amend.in>.
        </text></para></subprov>
      </prov.body>
    </prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)
    node = [n for n in document.nodes if n.xml_id == "A5"][0]

    assert len(node.amend_instructions) == 2
    first, second = node.amend_instructions
    assert first.verb == "omitting_substituting"
    assert first.target_citation == "Section 11(4)"
    assert first.old_text == "The Schedule"
    # The nested ``<citation>`` text is part of the new payload, not the target.
    assert first.new_text == "Schedule 1"
    assert first.each_place is False
    assert second.target_citation == "Section 11(5)"
    assert second.old_text == "old phrase"
    assert second.new_text == "new phrase"


def test_amend_instructions_read_replace_with_and_linkcontent_target() -> None:
    # Modern "replace X with Y" form, with the older ``<linkcontent>`` target
    # carrier (no ``<extref>``). Both the verb and the reconstructed target
    # citation must be recovered.
    xml = b"""\
<act>
  <body>
    <prov id="A6"><label>6</label><heading>Section 42 amended</heading>
      <prov.body><subprov><para><text>
        In <citation jurisdiction="nz">Section <linkcontent>42(3)</linkcontent></citation>,
        replace <amend.in>old rate</amend.in> with <amend.in>new rate</amend.in>.
      </text></para></subprov></prov.body>
    </prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)
    node = [n for n in document.nodes if n.xml_id == "A6"][0]

    assert len(node.amend_instructions) == 1
    instruction = node.amend_instructions[0]
    assert instruction.verb == "replace_with"
    assert instruction.target_citation == "Section 42(3)"
    assert instruction.old_text == "old rate"
    assert instruction.new_text == "new rate"


def test_amend_instructions_keep_insert_and_each_place_typed_not_guessed() -> None:
    # An insert instruction (one ``<amend.in>``) must carry its verb but no
    # old/new payload — it stays a typed not-yet-supported instruction, never a
    # guessed substitution. A wherever/in-each-place omit/substitute must be
    # flagged ``each_place`` while still carrying exact old/new.
    xml = b"""\
<act>
  <body>
    <prov id="A7"><label>7</label><heading>Mixed</heading>
      <prov.body>
        <subprov><label>1</label><para><text>
          <citation jurisdiction="nz"><extref href="DLM2">Section 17(2)(b)</extref></citation>
          is amended by inserting <amend.in>of a chief executive</amend.in> after <quote.in>required</quote.in>.
        </text></para></subprov>
        <subprov><label>2</label><para><text>
          <citation jurisdiction="nz"><extref href="DLM3">Section 48(3)</extref></citation>
          is amended by omitting <amend.in>widow</amend.in> wherever it appears
          and substituting in each case <amend.in>spouse or partner</amend.in>.
        </text></para></subprov>
      </prov.body>
    </prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)
    node = [n for n in document.nodes if n.xml_id == "A7"][0]

    insert, each_place = node.amend_instructions
    assert insert.verb == "inserting"
    assert insert.old_text == ""
    assert insert.new_text == ""
    assert each_place.verb == "omitting_substituting"
    assert each_place.each_place is True
    assert each_place.old_text == "widow"
    assert each_place.new_text == "spouse or partner"


def test_amend_instructions_empty_when_no_amend_in() -> None:
    # A global "every reference … in the principal Act" instruction carries no
    # ``<amend.in>`` pair and no resolvable target — it must yield no typed
    # instruction (stays a blocker downstream), not a guess.
    xml = b"""\
<act>
  <body>
    <prov id="A8"><label>8</label><heading>References</heading>
      <prov.body><subprov><para><text>The principal Act is amended by omitting every reference to the Superintendent, and substituting a reference to the Authority.</text></para></subprov></prov.body>
    </prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)
    node = [n for n in document.nodes if n.xml_id == "A8"][0]

    assert node.amend_instructions == ()


def test_nz_source_summary_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "source-summary", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "source-summary"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
