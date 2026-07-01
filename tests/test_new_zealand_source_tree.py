from __future__ import annotations

import pytest
from lxml import etree

from lawvm.new_zealand import source_tree
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


def test_legal_text_reuses_child_mode_cache_for_whitespace_structural_root() -> None:
    root = etree.fromstring(
        b"""\
<prov>
  <label>1</label>
  <subprov>
    <label>1</label>
    <para><text>Body <emphasis>text</emphasis>.</text></para>
  </subprov>
</prov>
"""
    )
    subprov = root.find(".//subprov")
    assert subprov is not None
    cache: dict[tuple[etree._Element, bool], str] = {}

    parent_text = source_tree._legal_text(root, cache=cache)
    child_mode_text = cache[(subprov, False)]
    child_text = source_tree._legal_text(subprov, cache=cache)

    assert parent_text == "1 1 Body text ."
    assert child_text == "1 Body text ."
    assert child_text == source_tree._normalize_text(child_mode_text)


def test_node_text_leaf_fast_path_matches_mixed_content_normalization() -> None:
    leaf = etree.fromstring(b"<label>  10\nA </label>")
    mixed = etree.fromstring(b"<text>A <emphasis>B</emphasis> C</text>")

    assert source_tree._node_text(leaf) == "10 A"
    assert source_tree._node_text(mixed) == "A B C"


def test_normalize_text_fast_path_preserves_whitespace_semantics() -> None:
    assert source_tree._normalize_text("") == ""
    assert source_tree._normalize_text("Section") == "Section"
    assert source_tree._normalize_text("  Section   10\nA\t") == "Section 10 A"
    assert source_tree._normalize_text("A\u00a0B") == "A B"


def test_schedule_indirection_detector_uses_caller_owned_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    node = etree.fromstring(
        b"""\
<prov>
  <label>1</label>
  <para><text>Amend the Acts set out in Schedules 1 to 4 of this Act.</text></para>
</prov>
"""
    )
    original = source_tree._node_text
    calls = 0

    def counting_node_text(element: etree._Element) -> str:
        nonlocal calls
        calls += 1
        return original(element)

    monkeypatch.setattr(source_tree, "_node_text", counting_node_text)
    cache: dict[tuple[object, ...], object] = {}

    assert source_tree._amending_node_is_schedule_indirection(node, cache=cache)
    assert source_tree._amending_node_is_schedule_indirection(node, cache=cache)
    assert calls == 1


def test_schedule_amends_for_base_work_uses_caller_owned_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    root = etree.fromstring(
        b"""\
<act>
  <schedule.amendments.group2>
    <heading>Forests Act 1949 (1949 No 19)</heading>
    <para><text>After section 67C, insert:</text>
      <amend><prov><label>67D</label><para><text>New text.</text></para></prov></amend>
    </para>
  </schedule.amendments.group2>
</act>
"""
    )
    original = source_tree._schedule_amendment_groups_for_base_work
    calls = 0

    def counting_groups(
        amending_root: etree._Element,
        *,
        base_work_year: str,
        base_work_number: str,
    ) -> list[etree._Element]:
        nonlocal calls
        calls += 1
        return original(
            amending_root,
            base_work_year=base_work_year,
            base_work_number=base_work_number,
        )

    monkeypatch.setattr(source_tree, "_schedule_amendment_groups_for_base_work", counting_groups)
    cache: dict[tuple[object, ...], object] = {}

    first = source_tree._schedule_amends_for_base_work(
        root,
        base_work_year="1949",
        base_work_number="19",
        cache=cache,
    )
    second = source_tree._schedule_amends_for_base_work(
        root,
        base_work_year="1949",
        base_work_number="19",
        cache=cache,
    )

    assert len(first) == 1
    assert second == first
    assert calls == 1


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


def test_amend_instructions_extract_insert_after_anchor_and_each_place() -> None:
    # An insert-after instruction with an explicit ``<quote.in>`` anchor carries
    # its anchor, the inserted text, and the position — the unambiguous shape the
    # lowering supports. A wherever/in-each-place omit/substitute must be flagged
    # ``each_place`` while still carrying exact old/new.
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
    # Insert-after anchor extraction: anchor in <quote.in>, new text in <amend.in>.
    assert insert.anchor_text == "required"
    assert insert.new_text == "of a chief executive"
    assert insert.insert_position == "after"
    assert insert.old_text == ""
    assert insert.omit_only is False
    assert each_place.verb == "omitting_substituting"
    assert each_place.each_place is True
    assert each_place.old_text == "widow"
    assert each_place.new_text == "spouse or partner"


def test_amend_instructions_omit_only_carries_deletion_span() -> None:
    # "is amended by omitting <amend.in>X</amend.in>" with a single span and no
    # substitution must carry the omitted span as old_text and be flagged
    # ``omit_only`` (lowered downstream as a deletion to the empty string).
    xml = b"""\
<act>
  <body>
    <prov id="A9"><label>9</label><heading>Omit</heading>
      <prov.body><subprov><para><text>
        <citation jurisdiction="nz"><extref href="DLM5">Section 14(1)(b)</extref></citation>
        is amended by omitting <amend.in>for an unspecified period</amend.in>.
      </text></para></subprov></prov.body>
    </prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)
    node = [n for n in document.nodes if n.xml_id == "A9"][0]
    (omit,) = node.amend_instructions
    assert omit.verb == "omitting"
    assert omit.omit_only is True
    assert omit.old_text == "for an unspecified period"
    assert omit.new_text == ""
    assert omit.anchor_text == ""


def test_amend_instructions_insert_two_amend_in_no_anchor_stays_unparsed() -> None:
    # The older two-``<amend.in>`` insert form ("inserting X after Y") has no
    # ``<quote.in>`` anchor and an unreliable element order — it must NOT be
    # given a parsed anchor/new payload (refuse-don't-guess).
    xml = b"""\
<act>
  <body>
    <prov id="A10"><label>10</label><heading>Insert</heading>
      <prov.body><subprov><para><text>
        <citation jurisdiction="nz"><extref href="DLM6">Section 174(4)</extref></citation>
        is amended by inserting <amend.in>or suspending</amend.in> after <amend.in>dismissing</amend.in>.
      </text></para></subprov></prov.body>
    </prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)
    node = [n for n in document.nodes if n.xml_id == "A10"][0]
    (insert,) = node.amend_instructions
    assert insert.verb == "inserting"
    assert insert.anchor_text == ""
    assert insert.new_text == ""
    assert insert.insert_position == ""


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


def test_amend_instruction_prefilter_skips_unrelated_subtrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original = source_tree._amend_instructions

    def counting_amend_instructions(
        node: etree._Element,
    ) -> tuple[source_tree.NZAmendInstruction, ...]:
        calls.append(node.get("id", ""))
        return original(node)

    monkeypatch.setattr(source_tree, "_amend_instructions", counting_amend_instructions)
    xml = b"""\
<act>
  <body>
    <prov id="A6"><label>6</label><heading>Section 42 amended</heading>
      <prov.body><subprov id="A6-1"><para><text>
        In <citation jurisdiction="nz">Section <linkcontent>42(3)</linkcontent></citation>,
        replace <amend.in>old rate</amend.in> with <amend.in>new rate</amend.in>.
      </text></para></subprov></prov.body>
    </prov>
    <prov id="B7"><label>7</label><heading>Plain provision</heading>
      <prov.body><subprov id="B7-1"><para><text>No inline amendment payload.</text></para></subprov></prov.body>
    </prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)

    assert [row.new_text for node in document.nodes for row in node.amend_instructions]
    assert {row.new_text for node in document.nodes for row in node.amend_instructions} == {
        "new rate"
    }
    assert "A6" in calls
    assert "B7" not in calls
    assert "B7-1" not in calls


def test_legal_text_keeps_inline_cross_reference_in_document_order() -> None:
    # A consolidated body marks cross-references with nested
    # ``<citation><intref>section N</intref></citation>`` whose reference text
    # sits one level below the citation's own ``tail``. The flow text must come
    # out in true document order ("required by section 47, the Secretary ...
    # fixed by section 48 for the lodging"), not with the reference text floated
    # to the end of the run. The same logical content arrives as flat text in an
    # amending act's payload, so an out-of-order extraction here makes the two
    # sides compare unequal even when the content is identical.
    xml = b"""\
<act>
  <body>
    <prov id="S51"><label>51</label><heading>Ballots</heading>
      <prov.body><subprov id="S51-1"><label>1</label><para><text>If an election is required by <citation><intref>section 47</intref></citation>, the Secretary must, after the date fixed by <citation><intref>section 48</intref></citation> for the lodging of nominations, act.</text></para></subprov></prov.body>
    </prov>
  </body>
</act>
"""

    document = parse_nz_source_document(xml)
    subprov = [n for n in document.nodes if n.kind == "subprov"][0]

    # ``_legal_text`` keeps inline elements in document order. (The space before
    # the comma is a join artifact removed by the comparison-side normalizer; the
    # load-bearing property here is ORDER, not punctuation spacing.)
    assert subprov.text == (
        "1 If an election is required by section 47 , the Secretary must, "
        "after the date fixed by section 48 for the lodging of nominations, act."
    )
    # The reference text must not float to the end of the run.
    assert "required by section 47" in subprov.text
    assert subprov.text.index("section 47") < subprov.text.index("the Secretary")
    assert subprov.text.index("section 48") < subprov.text.index("for the lodging")


def test_legal_text_inline_payload_and_marked_up_body_extract_identically() -> None:
    # The dry-run structural-replace comparison relies on the amending act's
    # flat-text ``<amend>`` payload and the consolidated body extracting the
    # SAME string for the same logical content. Pin that equivalence directly:
    # a flat ``<text>`` and a citation-marked ``<text>`` with identical reading
    # order must yield byte-identical node text.
    flat = b"""\
<act><body>
  <prov id="F"><label>9</label><heading>H</heading>
    <prov.body><subprov id="F-1"><label>1</label><para><text>A jury list must not contain the name of a person under section 115 of the Electoral Act 1993 whose address is suppressed.</text></para></subprov></prov.body>
  </prov>
</body></act>
"""
    marked = b"""\
<act><body>
  <prov id="M"><label>9</label><heading>H</heading>
    <prov.body><subprov id="M-1"><label>1</label><para><text>A jury list must not contain the name of a person under <citation><intref>section 115 of the Electoral Act 1993</intref></citation> whose address is suppressed.</text></para></subprov></prov.body>
  </prov>
</body></act>
"""

    flat_text = [n for n in parse_nz_source_document(flat).nodes if n.kind == "subprov"][0].text
    marked_text = [n for n in parse_nz_source_document(marked).nodes if n.kind == "subprov"][0].text

    assert flat_text == marked_text
    assert "under section 115 of the Electoral Act 1993 whose address" in flat_text


def test_legal_text_excludes_notes_and_their_trailing_text() -> None:
    # The document-order walker must still contribute nothing for a notes /
    # history subtree — neither its text nor the tail that trails it — so a
    # history note never leaks into a node's legal text.
    xml = b"""\
<act><body>
  <prov id="N"><label>3</label><heading>H</heading>
    <prov.body><subprov id="N-1"><label>1</label><para><text>Operative text.</text></para></subprov></prov.body>
    <notes><history-note id="HN">Section 3: amended, on 1 January 2025.</history-note></notes>
  </prov>
</body></act>
"""

    prov = [n for n in parse_nz_source_document(xml).nodes if n.kind == "prov"][0]

    assert "Operative text." in prov.text
    assert "amended" not in prov.text


def test_legal_text_excludes_legtable_accessibility_summary() -> None:
    # A ``<legtable>``'s ``<summary>`` is the auto-generated screen-reader caption
    # ("The following table is small in size and has N columns…"), not operative
    # legal content. The PCO consolidation does not carry it into the operative
    # text, so it must be dropped from extraction — the table BODY is still kept.
    xml = b"""\
<act><body>
  <prov id="N"><label>3</label><heading>H</heading>
    <prov.body><subprov id="N-1"><label>1</label>
      <para>
        <text>School boards:</text>
        <legtable>
          <summary>The following table is small in size and has 2 columns, but no headings.</summary>
          <table><tgroup cols="2"><tbody><row>
            <entry><para><text>Te Urewera</text></para></entry>
            <entry><para><text>Te Urewera Act 2014</text></para></entry>
          </row></tbody></tgroup></table>
        </legtable>
      </para>
    </subprov></prov.body>
  </prov>
</body></act>
"""

    node = [n for n in parse_nz_source_document(xml).nodes if n.kind == "subprov"][0]
    assert "small in size" not in node.text
    # The table body content is still extracted.
    assert "Te Urewera" in node.text
    assert "Te Urewera Act 2014" in node.text


def test_legal_text_excludes_cf_source_origin_footnote() -> None:
    # A ``<cf>`` is the "Compare:" source-origin footnote PCO appends to a
    # provision (e.g. ``<cf><citation>2008 No 72 s 79A</citation></cf>``) —
    # editorial provenance metadata, not operative legal content. An amending
    # act's payload carries no such footnote, so leaving it in a node's text
    # produces a spurious substantive divergence. It must be excluded.
    xml = b"""\
<act><body>
  <prov id="N"><label>79</label><heading>Purpose</heading>
    <prov.body><subprov id="N-1"><label>1</label><para><text>Operative text under this Act.</text></para></subprov></prov.body>
    <cf><citation jurisdiction="nz">2008 No 72 s 79A</citation></cf>
  </prov>
</body></act>
"""

    prov = [n for n in parse_nz_source_document(xml).nodes if n.kind == "prov"][0]
    assert "Operative text under this Act." in prov.text
    assert "2008 No 72" not in prov.text
    assert "s 79A" not in prov.text


def test_legal_text_skips_formula_eqn_lines_and_graphic_keeps_variable_defs() -> None:
    # An ``<eqn>`` math block renders the formula either as ``<eqn-line>`` text
    # fragments (an amending act payload) or as a ``<graphic>`` SVG image (the PCO
    # consolidation). Comparing image-vs-text for the same formula is a spurious
    # diff, so the formula rendering is dropped from extraction. The surrounding
    # ``where—`` prose and ``<variable-def>`` blocks are NOT formula and are kept.
    payload_xml = b"""\
<act><body>
  <prov id="P"><label>5</label><heading>H</heading>
    <prov.body><subprov id="P-1"><label>1C</label>
      <para>
        <text>calculated in accordance with the following formula:</text>
        <eqn id="E0">
          <table><tgroup cols="1"><tbody><row><entry>
            <para><eqn id="E1"><eqn-line>{[(1 + P1) &#215; (1 + P2)] &#8722; 1} &#215; 100</eqn-line></eqn></para>
          </entry></row></tbody></tgroup></table>
          <para><text>where&#8212;</text></para>
          <variable-def><variable>P1</variable><para><text>is the first percentage.</text></para></variable-def>
        </eqn>
      </para>
    </subprov></prov.body>
  </prov>
</body></act>
"""
    graphic_xml = b"""\
<act><body>
  <prov id="P"><label>5</label><heading>H</heading>
    <prov.body><subprov id="P-1"><label>1C</label>
      <para>
        <text>calculated in accordance with the following formula:</text>
        <eqn id="E0">
          <graphic alt-text="Formula" fileref="images/formula.svg"/>
          <para><text>where&#8212;</text></para>
          <variable-def><variable>P1</variable><para><text>is the first percentage.</text></para></variable-def>
        </eqn>
      </para>
    </subprov></prov.body>
  </prov>
</body></act>
"""

    payload_text = [n for n in parse_nz_source_document(payload_xml).nodes if n.kind == "subprov"][0].text
    graphic_text = [n for n in parse_nz_source_document(graphic_xml).nodes if n.kind == "subprov"][0].text

    # The formula rendering (eqn-line text / graphic image) is dropped from both.
    assert "{[(1 + P1)" not in payload_text
    # The variable-def prose and "where—" survive on both sides.
    assert "where—" in payload_text
    assert "is the first percentage." in payload_text
    # Image-vs-text formula no longer makes the two sides diverge.
    assert payload_text == graphic_text


def test_def_para_text_bounded_to_first_definition_when_two_are_packed() -> None:
    # Some amending acts pack two distinct definitions under ONE ``<def-para>``
    # element as a run of sibling direct ``<para>`` children, each opening with
    # its own ``<def-term>`` (2020/62 packs "smokeless tobacco product" and
    # "smoking cessation programme" this way). The official consolidation splits
    # them into one def-para per definition. The extracted node text must be
    # bounded to the FIRST (targeted) definition and not absorb the adjacent one.
    xml = b"""\
<act><body>
  <prov id="S2"><label>2</label><heading>Interpretation</heading>
    <prov.body><subprov id="S2-1"><label>1</label>
      <def-para id="DP">
        <para><text><def-term id="T1">smokeless tobacco product</def-term> means a tobacco product that does not involve combustion</text></para>
        <para><text><def-term id="T2">smoking cessation programme</def-term> means a funded programme to help smokers stop</text></para>
      </def-para>
    </subprov></prov.body>
  </prov>
</body></act>
"""

    def_paras = [n for n in parse_nz_source_document(xml).nodes if n.kind == "def-para"]
    assert len(def_paras) == 1
    node = def_paras[0]
    assert node.label == "smokeless tobacco product"
    assert node.text == (
        "smokeless tobacco product means a tobacco product that does not involve combustion"
    )
    # The adjacent definition must not leak into this node's text.
    assert "smoking cessation programme" not in node.text


def test_def_para_text_keeps_whole_definition_that_defines_two_related_terms() -> None:
    # A legitimate single definition may define two related terms together by
    # carrying a SECOND ``<def-term>`` LATER in its prose ("…and <def-term>vaping
    # </def-term> has a corresponding meaning"). The trailing term is not a
    # leading definition opener, so the definition must stay whole — never split.
    xml = b"""\
<act><body>
  <prov id="S2"><label>2</label><heading>Interpretation</heading>
    <prov.body><subprov id="S2-1"><label>1</label>
      <def-para id="DP">
        <para><text><def-term id="T1">to vape</def-term> means to inhale using a vaping device, and <def-term id="T2">vaping</def-term> has a corresponding meaning</text></para>
      </def-para>
    </subprov></prov.body>
  </prov>
</body></act>
"""

    def_paras = [n for n in parse_nz_source_document(xml).nodes if n.kind == "def-para"]
    assert len(def_paras) == 1
    node = def_paras[0]
    assert node.label == "to vape"
    assert node.text == (
        "to vape means to inhale using a vaping device, "
        "and vaping has a corresponding meaning"
    )


def test_def_para_with_leading_label_para_limbs_is_not_truncated() -> None:
    # A single definition whose body is a leading ``<para>`` followed by nested
    # ``<label-para>`` limbs (the common shape) must extract whole: only a
    # NON-FIRST direct ``<para>`` that itself opens with a ``<def-term>`` bounds
    # extraction, so label-para limbs are always retained.
    xml = b"""\
<act><body>
  <prov id="S2"><label>2</label><heading>Interpretation</heading>
    <prov.body><subprov id="S2-1"><label>1</label>
      <def-para id="DP">
        <para>
          <text><def-term id="T1">dedicated room</def-term> means an internal area used solely to&#8212;</text>
          <label-para><label denominator="yes">a</label><para><text>enable patients who smoke to smoke; or</text></para></label-para>
          <label-para><label denominator="yes">b</label><para><text>enable patients who vape to vape</text></para></label-para>
        </para>
      </def-para>
    </subprov></prov.body>
  </prov>
</body></act>
"""

    node = [n for n in parse_nz_source_document(xml).nodes if n.kind == "def-para"][0]
    assert "enable patients who smoke to smoke; or" in node.text
    assert "enable patients who vape to vape" in node.text


def test_nz_source_summary_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "source-summary", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "source-summary"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
