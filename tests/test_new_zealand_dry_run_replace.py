"""Structural whole-provision REPLACE dry-run kernel tests.

These exercise the cycle-5 structural-replace kernel: it extracts the new
provision body from an amending act's ``<amend>`` subtree, swaps it in for the
exact target node's subtree, and classifies whether the on-or-after oracle
subtree matches the candidate replacement (normalized). The kernel never
guesses a payload, never flattens a one-to-many expansion, and never counts a
subtree mismatch as agreement.
"""

from __future__ import annotations

import json

from lxml import etree

from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPLACE_FAMILY,
    NZ_DRY_RUN_NOT_IN_SCOPE_REPLACE_TARGET_NOT_CANDIDATE,
    NZ_DRY_RUN_REFUSED_NO_REPLACE_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_REFUSED_REPLACE_NO_AMENDING_WORK_RULE_ID,
    NZ_DRY_RUN_REFUSED_REPLACE_PAYLOAD_NOT_EXTRACTABLE_RULE_ID,
    NZ_DRY_RUN_REPLACE_AGREES_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_MISMATCH_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    NZDryRunReport,
    _amend_provision_overlaps_target_in_other_step,
    _instruction_target_label_path,
    _node_label_path,
    _paths_overlap,
    build_dry_run_replace,
    scope_from_arg,
)
from lawvm.new_zealand.source_tree import (
    NZ_STRUCTURAL_REPLACE_BLOCKED_AMBIGUOUS_MATCH,
    NZ_STRUCTURAL_REPLACE_BLOCKED_NO_AMEND_SUBTREE,
    NZ_STRUCTURAL_REPLACE_BLOCKED_NO_MATCHING_CHILD,
    NZStructuralReplacement,
    extract_structural_replacement,
)

_WORK_ID = "act_public_2005_99"
_AMENDING_WORK_ID = "act_public_2019_5"
_BEFORE_VERSION = "act_public_2005_99_en_2018-01-01"
_AFTER_VERSION = "act_public_2005_99_en_2019-10-24"
_AMENDING_VERSION = "act_public_2019_5_en_2019-10-24"
_HREF = "DLM9000010"

# Principal act before the amendment: section 41 carries the OLD body, sibling
# section 42 untouched.
_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLMa41" deletion-status=""><label>41</label><heading>Old heading</heading>
      <prov.body><para><text>41 Old heading The old body of section 41.</text></para></prov.body></prov>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where section 41 reflects the structural replacement; 42 untouched.
_AFTER_XML_AGREES = b"""\
<act>
  <body>
    <prov id="DLMa41" deletion-status=""><label>41</label><heading>New heading</heading>
      <prov.body><para><text>41 New heading The brand new body of section 41.</text></para></prov.body></prov>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where section 41 carries content that differs from the candidate
# replacement (another window change / wrong content) -> honest residual.
_AFTER_XML_MISMATCH = b"""\
<act>
  <body>
    <prov id="DLMa41" deletion-status=""><label>41</label><heading>Different heading</heading>
      <prov.body><para><text>41 Different heading An entirely different replacement body.</text></para></prov.body></prov>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where section 41 is gone entirely -> target_missing residual.
_AFTER_XML_TARGET_MISSING = b"""\
<act>
  <body>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# Amending act: provision at _HREF carries a "Replace section 41 with:" <amend>
# subtree with exactly one top-level <prov> labelled 41 (clean one-to-one).
_AMENDING_XML = b"""\
<act>
  <body>
    <prov id="DLM9000010"><label>10</label><heading>Amendments to principal Act</heading>
      <prov.body><subprov><label>1</label><para>
        <text><citation jurisdiction="nz"><extref href="DLMa41">section 41</extref></citation> is repealed and the following section substituted:</text>
        <amend>
          <prov id="newDLMa41"><label>41</label><heading>New heading</heading>
            <prov.body><para><text>41 New heading The brand new body of section 41.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
  </body>
</act>
"""

# Amending act with a one-to-many expansion ("the following subsections are
# substituted:" -> several new subprovs). Each affected subprov is its own
# upstream history-note witness, so the extractor selects the single child whose
# label matches the per-witness target leaf (subprov 2) and leaves the siblings
# (subprov 2A) to their own witnesses.
_AMENDING_XML_MULTI = b"""\
<act>
  <body>
    <prov id="DLM9000020"><label>20</label><heading>Expansion</heading>
      <prov.body><subprov><label>1</label><para>
        <text>Section 27(2) is repealed and the following subsections are substituted:</text>
        <amend>
          <subprov><label>2</label><para><text>2 New subsection two.</text></para></subprov>
          <subprov><label>2A</label><para><text>2A Inserted subsection two-A.</text></para></subprov>
        </amend>
      </para></subprov></prov.body></prov>
  </body>
</act>
"""


class _FakeArchive:
    def __init__(self, rows: dict[str, bytes]) -> None:
        self.rows = rows

    def get(self, locator: str, *, at: object | None = None) -> bytes | None:
        return self.rows.get(locator)

    def locators(self, pattern: str = "%") -> list[str]:
        prefix = pattern[:-1] if pattern.endswith("%") else pattern
        return sorted(locator for locator in self.rows if locator.startswith(prefix))

    def close(self) -> None:
        pass


def _version_detail(version_id: str, work: str, number: str, date: str) -> bytes:
    return json.dumps(
        {
            "version_id": version_id,
            "formats": [
                {
                    "type": "xml",
                    "url": f"https://www.legislation.govt.nz/act/public/{date[:4]}/{number}/en/{date}.xml",
                }
            ],
        }
    ).encode()


def _archive(after_xml: bytes, *, amending_xml: bytes = _AMENDING_XML) -> _FakeArchive:
    return _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{_BEFORE_VERSION}/": _version_detail(
                _BEFORE_VERSION, _WORK_ID, "99", "2018-01-01"
            ),
            "https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml": _BEFORE_XML,
            f"https://api.legislation.govt.nz/v0/versions/{_AFTER_VERSION}/": _version_detail(
                _AFTER_VERSION, _WORK_ID, "99", "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2019/99/en/2019-10-24.xml": after_xml,
            # Amending act latest version + locator resolution.
            f"https://api.legislation.govt.nz/v0/works/{_AMENDING_WORK_ID}/versions/": json.dumps(
                {"versions": [{"version_id": _AMENDING_VERSION, "date.as.at": "2019-10-24"}]}
            ).encode(),
            f"https://api.legislation.govt.nz/v0/versions/{_AMENDING_VERSION}/": _version_detail(
                _AMENDING_VERSION, _AMENDING_WORK_ID, "5", "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2019/5/en/2019-10-24.xml": amending_xml,
        }
    )


class _FakeTargetCandidate:
    def __init__(self, status: str, address: str, path: tuple[tuple[str, str], ...]) -> None:
        self.status = status
        self.address = address
        self.path = path


class _FakeWitnessRow:
    def __init__(
        self,
        *,
        row_id: str = "nz-opw-77",
        operation_family: str = "replaced",
        target_status: str = "candidate",
        target_path: tuple[tuple[str, str], ...] = (("section", "41"),),
        amended_provision: str = "Section 41",
        amendment_date_iso: str = "2019-10-24",
        amending_work_id: str = _AMENDING_WORK_ID,
        amending_provision_hrefs: tuple[str, ...] = (_HREF,),
    ) -> None:
        self.row_id = row_id
        self.operation_family = operation_family
        self.amended_provision = amended_provision
        self.amendment_date_iso = amendment_date_iso
        self.amending_work_id = amending_work_id
        self.amending_provision_hrefs = amending_provision_hrefs
        self.target_address_candidate = _FakeTargetCandidate(
            target_status, "section:41" if target_path == (("section", "41"),) else "", target_path
        )


class _FakeSurface:
    def __init__(self, rows: tuple[_FakeWitnessRow, ...]) -> None:
        self.rows = rows


def _run(after_xml: bytes, rows: tuple[_FakeWitnessRow, ...], *, amending_xml: bytes = _AMENDING_XML):
    archive = _archive(after_xml, amending_xml=amending_xml)
    return build_dry_run_replace(archive, work_id=_WORK_ID, surface=_FakeSurface(rows))


# --- Structural payload extractor unit tests. --------------------------------


def _amending_node(xml: bytes, href: str):
    root = etree.fromstring(xml)
    for element in root.iter():
        if isinstance(element.tag, str) and element.attrib.get("id") == href:
            return element
    raise AssertionError(f"href {href} not found")


def test_extractor_returns_clean_one_to_one_replacement() -> None:
    node = _amending_node(_AMENDING_XML, _HREF)
    result = extract_structural_replacement(node, target_leaf_kind="prov", target_leaf_label="41")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.kind == "prov"
    assert result.root.label == "41"
    assert "brand new body" in result.root.text


# A schedule-indirection REPLACE ("Amend the enactments specified in Schedule 1
# ... as set out in that schedule" delivering a whole-provision replace) resolves
# its payload from the schedule amendment group keyed to the base act, the same
# as the insert path.
_SCHEDULE_REPLACE_XML = b"""\
<act>
  <body>
    <prov id="OP"><label>9</label><heading>Consequential amendments</heading><prov.body>
      <subprov><label></label><para>
        <text>Amend the enactments specified in <citation jurisdiction="nz"><intref href="SCH1">Schedule 1</intref></citation> as set out in that schedule.</text>
      </para></subprov>
    </prov.body></prov>
  </body>
  <schedule id="SCH1"><label>1</label><heading>Consequential amendments</heading>
    <schedule.amendments>
      <schedule.amendments.group2 id="G_A"><heading>Forests Act 1949 (1949 No 19)</heading>
        <para><text>Replace <citation jurisdiction="nz"><extref href="x">section 67V</extref></citation> with:</text>
          <amend><prov><label>67V</label><para><text>67V Replacement section about resource management.</text></para></prov></amend>
        </para>
      </schedule.amendments.group2>
    </schedule.amendments>
  </schedule>
</act>
"""


# A lettered paragraph whose inline definitions/illustration table is emitted as
# a SEPARATE label-less ``<para><legtable>`` sibling AFTER the paragraph in the
# amend payload. The consolidation nests that table inside the paragraph, so the
# extractor must absorb the trailing table sibling into the paragraph's text. The
# accessibility ``<summary>`` caption is dropped (presentational metadata).
_TABLE_CONTINUATION_XML = b"""\
<act><body>
  <prov id="OP"><label>9</label><heading>Amend section 7</heading><prov.body>
    <subprov><label></label><para><text>In section 7(1), replace paragraph (d) with:</text>
      <amend increment="1">
        <label-para><label denominator="yes">d</label><para><text>school boards:</text></para></label-para>
        <para>
          <legtable>
            <summary>The following table is small in size and has 2 columns.</summary>
            <table>
              <tgroup cols="2">
                <tbody>
                  <row>
                    <entry><para><text>These are boards constituted under the Education and Training Act 2020</text></para></entry>
                    <entry><para><text>A body that is a board constituted under that Act</text></para></entry>
                  </row>
                </tbody>
              </tgroup>
            </table>
          </legtable>
        </para>
      </amend>
    </para></subprov>
  </prov.body></prov>
</body></act>
"""


def test_extractor_absorbs_trailing_table_continuation_sibling() -> None:
    node = _amending_node(_TABLE_CONTINUATION_XML, "OP")
    result = extract_structural_replacement(
        node, target_leaf_kind="label-para", target_leaf_label="d"
    )
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.kind == "label-para"
    assert result.root.label == "d"
    # The trailing table sibling's body is absorbed into the paragraph's text.
    assert "school boards:" in result.root.text
    assert "These are boards constituted under the Education and Training Act 2020" in result.root.text
    assert "A body that is a board constituted under that Act" in result.root.text
    # The accessibility caption is dropped (presentational metadata).
    assert "small in size" not in result.root.text


# Two lettered paragraphs (d and e), each followed by its OWN table-continuation
# sibling. Each paragraph must absorb only ITS table, never the next paragraph's.
_TWO_TABLE_CONTINUATION_XML = b"""\
<act><body>
  <prov id="OP"><label>9</label><heading>Amend section 7</heading><prov.body>
    <subprov><label></label><para><text>In section 7(1), replace paragraphs (d) and (e) with:</text>
      <amend increment="1">
        <label-para><label denominator="yes">d</label><para><text>school boards:</text></para></label-para>
        <para><legtable><table><tgroup cols="1"><tbody><row><entry><para><text>boards definition</text></para></entry></row></tbody></tgroup></table></legtable></para>
        <label-para><label denominator="yes">e</label><para><text>tertiary education institutions:</text></para></label-para>
        <para><legtable><table><tgroup cols="1"><tbody><row><entry><para><text>institutions definition</text></para></entry></row></tbody></tgroup></table></legtable></para>
      </amend>
    </para></subprov>
  </prov.body></prov>
</body></act>
"""


def test_table_continuation_absorbs_only_own_paragraphs_table() -> None:
    node = _amending_node(_TWO_TABLE_CONTINUATION_XML, "OP")
    result_d = extract_structural_replacement(
        node, target_leaf_kind="label-para", target_leaf_label="d"
    )
    assert isinstance(result_d, NZStructuralReplacement)
    assert "boards definition" in result_d.root.text
    # Paragraph d must NOT absorb e's content or e's table.
    assert "tertiary education institutions" not in result_d.root.text
    assert "institutions definition" not in result_d.root.text


def test_schedule_indirection_resolves_replacement_for_base_work() -> None:
    node = _amending_node(_SCHEDULE_REPLACE_XML, "OP")
    result = extract_structural_replacement(
        node,
        target_leaf_kind="prov",
        target_leaf_label="67V",
        base_work_year="1949",
        base_work_number="19",
    )
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "67V"
    assert "resource management" in result.root.text


def test_schedule_indirection_replacement_without_base_work_blocks() -> None:
    # No base-work identity: the payload cannot be keyed to a schedule group, and
    # the operative section carries no inline amend subtree -> typed blocker.
    node = _amending_node(_SCHEDULE_REPLACE_XML, "OP")
    result = extract_structural_replacement(node, target_leaf_kind="prov", target_leaf_label="67V")
    assert isinstance(result, str)


def test_extractor_selects_witness_child_from_one_to_many_expansion() -> None:
    # The amend carries subprov 2 AND subprov 2A. The per-witness target leaf
    # (subprov 2) selects exactly its own child; the sibling 2A belongs to its
    # own witness and is not flattened into this replacement.
    node = _amending_node(_AMENDING_XML_MULTI, "DLM9000020")
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="2")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.kind == "subprov"
    assert result.root.label == "2"
    assert "New subsection two" in result.root.text
    assert "two-A" not in result.root.text


def test_extractor_selects_sibling_child_from_same_one_to_many_expansion() -> None:
    # A different witness over the SAME amend selects the OTHER child (subprov 2A)
    # — proving the multi-child amend serves per-witness extraction, not a flatten.
    node = _amending_node(_AMENDING_XML_MULTI, "DLM9000020")
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="2A")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.kind == "subprov"
    assert result.root.label == "2A"
    assert "two-A" in result.root.text


def test_extractor_blocks_unmatched_leaf_in_one_to_many_expansion() -> None:
    # A leaf absent from the multi-child amend (subprov 9) is a typed no-match,
    # never a spurious select of an unrelated sibling.
    node = _amending_node(_AMENDING_XML_MULTI, "DLM9000020")
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="9")
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_NO_MATCHING_CHILD


def test_extractor_blocks_when_no_amend_subtree() -> None:
    root = etree.fromstring(
        b"<act><body><prov id='X'><prov.body><para><text>plain prose, no amend.</text></para></prov.body></prov></body></act>"
    )
    node = next(e for e in root.iter() if e.attrib.get("id") == "X")
    result = extract_structural_replacement(node, target_leaf_kind="prov", target_leaf_label="41")
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_NO_AMEND_SUBTREE


def test_extractor_blocks_when_no_child_matches_target_leaf() -> None:
    node = _amending_node(_AMENDING_XML, _HREF)
    # The amend child is prov:41; asking for subprov:9 must not match.
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="9")
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_NO_MATCHING_CHILD


def test_extractor_blocks_ambiguous_multiple_amend_children() -> None:
    xml = b"""\
<act><body><prov id="AMB"><prov.body>
  <subprov><label>1</label><para><text>Replace section 41 with:</text>
    <amend><prov id="a"><label>41</label><para><text>41 First.</text></para></prov></amend></para></subprov>
  <subprov><label>2</label><para><text>Also replace section 41 with:</text>
    <amend><prov id="b"><label>41</label><para><text>41 Second.</text></para></prov></amend></para></subprov>
</prov.body></prov></body></act>
"""
    node = _amending_node(xml, "AMB")
    result = extract_structural_replacement(node, target_leaf_kind="prov", target_leaf_label="41")
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_AMBIGUOUS_MATCH


# An amending section with two instructions that each amend a DIFFERENT section's
# sub-provision with the SAME label (section 81(1) and section 88(1)): a leaf-only
# match across the node is ambiguous, but the cited section disambiguates exactly.
_AMENDING_XML_CROSS_SECTION = b"""\
<act>
  <body>
    <prov id="DLM9000030"><label>30</label><heading>Cross-section</heading>
      <prov.body>
        <para>
          <text>Replace <citation jurisdiction="nz"><extref href="DLMs81">section 81(1)</extref></citation> with:</text>
          <amend>
            <subprov><label>1</label><para><text>1 New section 81 subsection one.</text></para></subprov>
          </amend>
        </para>
        <para>
          <text>Replace <citation jurisdiction="nz"><extref href="DLMs88">section 88(1) to (4)</extref></citation> with:</text>
          <amend>
            <subprov><label>1</label><para><text>1 New section 88 subsection one.</text></para></subprov>
            <subprov><label>2</label><para><text>2 New section 88 subsection two.</text></para></subprov>
          </amend>
        </para>
      </prov.body></prov>
  </body>
</act>
"""


def test_extractor_disambiguates_cross_section_collision_by_provision() -> None:
    # subprov 1 exists under BOTH the section-81 and section-88 instructions.
    # Leaf-only is ambiguous; the witness section label selects exactly one.
    node = _amending_node(_AMENDING_XML_CROSS_SECTION, "DLM9000030")
    s81 = extract_structural_replacement(
        node, target_leaf_kind="subprov", target_leaf_label="1", target_provision_label="81"
    )
    assert isinstance(s81, NZStructuralReplacement)
    assert "section 81 subsection one" in s81.root.text
    s88 = extract_structural_replacement(
        node, target_leaf_kind="subprov", target_leaf_label="1", target_provision_label="88"
    )
    assert isinstance(s88, NZStructuralReplacement)
    assert "section 88 subsection one" in s88.root.text


def test_extractor_cross_section_collision_stays_ambiguous_without_provision() -> None:
    # Single-argument behaviour is unchanged: without the section label the same
    # cross-section collision is still a typed ambiguity, never a guess.
    node = _amending_node(_AMENDING_XML_CROSS_SECTION, "DLM9000030")
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="1")
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_AMBIGUOUS_MATCH


def test_extractor_same_section_ambiguity_stays_blocked_with_provision() -> None:
    # Two instructions citing the SAME section that each carry a subprov 1 cannot
    # be disambiguated by section; it stays a typed ambiguity even with the label.
    xml = b"""\
<act><body><prov id="SAME"><prov.body>
  <para><text>Replace <citation jurisdiction="nz"><extref href="x">section 16(1)</extref></citation> with:</text>
    <amend><subprov><label>1</label><para><text>1 First.</text></para></subprov></amend></para>
  <para><text>Also replace <citation jurisdiction="nz"><extref href="y">section 16(1)</extref></citation> with:</text>
    <amend><subprov><label>1</label><para><text>1 Second.</text></para></subprov></amend></para>
</prov.body></prov></body></act>
"""
    node = _amending_node(xml, "SAME")
    result = extract_structural_replacement(
        node, target_leaf_kind="subprov", target_leaf_label="1", target_provision_label="16"
    )
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_AMBIGUOUS_MATCH


# --- Nested-payload descendant matching. --------------------------------------
#
# The new/target leaf often lives INSIDE a newly-inserted Part or section in the
# amend subtree (a new section nested in a new ``<part>``/``<subpart>``, or a new
# subsection nested in a new section), not as a direct ``<amend>`` child. The
# descendant lane finds it; the top-level path is consulted first so a clean
# top-level extraction is unchanged. The >1-match ambiguity refusal still holds.

# Amend payload wraps a new section 147A inside a new Part 9 (the spec example).
_AMENDING_XML_NESTED_PART = b"""\
<act><body><prov id="NESTPART"><prov.body><para>
  <text>Replace the heading to Part 9 and the Part with:</text>
  <amend>
    <part><label>9</label><heading>New Part nine</heading>
      <prov><label>147A</label><heading>Nested section</heading>
        <prov.body><para><text>147A Nested section The nested body of new section 147A.</text></para></prov.body></prov>
    </part>
  </amend>
</para></prov.body></prov></body></act>
"""

# Amend payload wraps a new section nested inside a new SUBPART inside a new Part.
_AMENDING_XML_NESTED_SUBPART = b"""\
<act><body><prov id="NESTSUB"><prov.body><para>
  <text>Replace Part 3 with:</text>
  <amend>
    <part><label>3</label><heading>New Part three</heading>
      <subpart><label>1</label><heading>Subpart one</heading>
        <prov><label>84</label><heading>Deep section</heading>
          <prov.body><para><text>84 Deep section The deeply nested body of section 84.</text></para></prov.body></prov>
      </subpart>
    </part>
  </amend>
</para></prov.body></prov></body></act>
"""

# Amend payload wraps a new subsection nested inside a new section.
_AMENDING_XML_NESTED_SUBPROV = b"""\
<act><body><prov id="NESTSP"><prov.body><para>
  <text>Replace section 20 with:</text>
  <amend>
    <prov><label>20</label><heading>Replacement section</heading>
      <prov.body>
        <subprov><label>1</label><para><text>1 first subsection.</text></para></subprov>
        <subprov><label>2</label><para><text>2 the nested replacement subsection two.</text></para></subprov>
      </prov.body></prov>
  </amend>
</para></prov.body></prov></body></act>
"""


def test_extractor_descends_into_new_part_for_nested_section() -> None:
    # New section 147A lives inside the new Part 9 in the amend subtree, not as a
    # direct amend child. The descendant lane pulls it out cleanly.
    node = _amending_node(_AMENDING_XML_NESTED_PART, "NESTPART")
    result = extract_structural_replacement(node, target_leaf_kind="prov", target_leaf_label="147A")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.kind == "prov"
    assert result.root.label == "147A"
    assert "nested body of new section 147A" in result.root.text


def test_extractor_descends_through_subpart_for_nested_section() -> None:
    # Two container levels deep (part -> subpart -> prov) still resolves.
    node = _amending_node(_AMENDING_XML_NESTED_SUBPART, "NESTSUB")
    result = extract_structural_replacement(node, target_leaf_kind="prov", target_leaf_label="84")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "84"
    assert "deeply nested body" in result.root.text


def test_extractor_descends_into_new_section_for_nested_subsection() -> None:
    # A new subsection nested inside a new section (prov -> subprov) resolves, and
    # selecting the nested subprov 2 does not pull in subprov 1's text.
    node = _amending_node(_AMENDING_XML_NESTED_SUBPROV, "NESTSP")
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="2")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "2"
    assert "nested replacement subsection two" in result.root.text
    assert "first subsection" not in result.root.text


def test_extractor_descent_refuses_ambiguous_nested_leaf() -> None:
    # The SAME section label 147A appears nested under TWO different new Parts in
    # the amend subtree. The descendant lane must treat this as a genuine ambiguity
    # and refuse — never guess which Part's section is the target.
    xml = b"""\
<act><body><prov id="NESTAMB"><prov.body><para>
  <text>Replace the Parts with:</text>
  <amend>
    <part><label>9</label><heading>Part nine</heading>
      <prov><label>147A</label><heading>First</heading><prov.body><para><text>147A First nested.</text></para></prov.body></prov>
    </part>
    <part><label>10</label><heading>Part ten</heading>
      <prov><label>147A</label><heading>Second</heading><prov.body><para><text>147A Second nested.</text></para></prov.body></prov>
    </part>
  </amend>
</para></prov.body></prov></body></act>
"""
    node = _amending_node(xml, "NESTAMB")
    result = extract_structural_replacement(node, target_leaf_kind="prov", target_leaf_label="147A")
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_AMBIGUOUS_MATCH


def test_extractor_prefers_top_level_over_nested_when_both_present() -> None:
    # A top-level amend child labelled 5 AND a nested section 5 inside a new Part
    # both exist. The top-level path is consulted first and owns the extraction, so
    # the nested lane is not even reached — the top-level child is returned and the
    # nested one is left to its own witness (no spurious ambiguity).
    xml = b"""\
<act><body><prov id="TOPVSNEST"><prov.body><para>
  <text>Replace section 5 with, and add a Part:</text>
  <amend>
    <prov><label>5</label><heading>Top level</heading><prov.body><para><text>5 The top-level replacement body.</text></para></prov.body></prov>
    <part><label>2</label><heading>Part two</heading>
      <prov><label>5</label><heading>Nested decoy</heading><prov.body><para><text>5 A nested decoy.</text></para></prov.body></prov>
    </part>
  </amend>
</para></prov.body></prov></body></act>
"""
    node = _amending_node(xml, "TOPVSNEST")
    result = extract_structural_replacement(node, target_leaf_kind="prov", target_leaf_label="5")
    assert isinstance(result, NZStructuralReplacement)
    assert "top-level replacement body" in result.root.text
    assert "nested decoy" not in result.root.text


# --- Target-leaf KIND-ALIAS matching (subprov <-> label-para). ----------------
#
# NZ encodes the same interchangeable lettered-paragraph leaf as ``subprov`` in
# one place and ``label-para`` in another. The extractor matches across that
# alias on an exact label so the payload (present, only the leaf KIND differs) is
# no longer spuriously refused; the label still must match exactly, so a genuine
# both-kinds-present collision stays a typed ambiguity (no false positive).

# Amend payload carries the new leaf as a ``label-para`` while the target leaf is
# addressed as a ``subprov`` (and vice versa for the reverse direction below).
_AMENDING_XML_ALIAS_LABEL_PARA = b"""\
<act><body><prov id="ALIASLP"><prov.body><para>
  <text>Replace section 12(h) with:</text>
  <amend>
    <label-para><label>h</label><para><text>h the brand new lettered paragraph body.</text></para></label-para>
  </amend>
</para></prov.body></prov></body></act>
"""

_AMENDING_XML_ALIAS_SUBPROV = b"""\
<act><body><prov id="ALIASSP"><prov.body><para>
  <text>Replace section 12(2) with:</text>
  <amend>
    <subprov><label>2</label><para><text>2 the brand new subsection body.</text></para></subprov>
  </amend>
</para></prov.body></prov></body></act>
"""


def test_extractor_matches_subprov_target_against_label_para_payload() -> None:
    # subprov target leaf, label-para payload: the kind alias matches on label "h".
    node = _amending_node(_AMENDING_XML_ALIAS_LABEL_PARA, "ALIASLP")
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="h")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "h"
    assert "brand new lettered paragraph" in result.root.text


def test_extractor_matches_label_para_target_against_subprov_payload() -> None:
    # Reverse direction: label-para target leaf, subprov payload, label "2".
    node = _amending_node(_AMENDING_XML_ALIAS_SUBPROV, "ALIASSP")
    result = extract_structural_replacement(node, target_leaf_kind="label-para", target_leaf_label="2")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "2"
    assert "brand new subsection" in result.root.text


def test_extractor_kind_alias_does_not_collapse_genuine_ambiguity() -> None:
    # Both a subprov "a" AND a label-para "a" are present in the amend payload.
    # With the alias relaxing the kind, both match the target leaf on label "a" —
    # which is a genuine ambiguity that MUST stay blocked (no false positive).
    xml = b"""\
<act><body><prov id="ALIASAMB"><prov.body><para>
  <text>Replace section 5(a) with:</text>
  <amend>
    <subprov><label>a</label><para><text>a first candidate.</text></para></subprov>
    <label-para><label>a</label><para><text>a second candidate.</text></para></label-para>
  </amend>
</para></prov.body></prov></body></act>
"""
    node = _amending_node(xml, "ALIASAMB")
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="a")
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_AMBIGUOUS_MATCH


def test_extractor_kind_alias_only_subprov_label_para_not_other_kinds() -> None:
    # A coincidental numeric collision across structural levels (a schedule "3" in
    # the payload, a subprov "3" target) is NOT a genuine alias and must not match.
    xml = b"""\
<act><body><prov id="NOTALIAS"><prov.body><para>
  <text>Replace section 7(3) with:</text>
  <amend>
    <schedule><label>3</label><para><text>3 a schedule, not a subsection.</text></para></schedule>
  </amend>
</para></prov.body></prov></body></act>
"""
    node = _amending_node(xml, "NOTALIAS")
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="3")
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_NO_MATCHING_CHILD


# End-to-end: the live body encodes section 12(h) as a ``label-para`` and the
# amend payload carries the replacement as a ``subprov`` (alias). The kernel must
# normalize the extracted root's kind to the live-body target kind so the
# candidate subtree AGREES with the oracle (same label/text), not a spurious
# kind-only residual mismatch.
_ALIAS_E2E_BEFORE = b"""\
<act><body>
  <prov id="DLMs12" deletion-status=""><label>12</label><heading>Section twelve</heading><prov.body>
    <subprov id="DLMs12s1"><label>1</label><para><text>1 lead-in.</text>
      <label-para id="DLMs12h" deletion-status=""><label>h</label><para><text>h the old lettered paragraph body.</text></para></label-para>
    </para></subprov>
  </prov.body></prov>
</body></act>
"""

_ALIAS_E2E_AFTER = b"""\
<act><body>
  <prov id="DLMs12" deletion-status=""><label>12</label><heading>Section twelve</heading><prov.body>
    <subprov id="DLMs12s1"><label>1</label><para><text>1 lead-in.</text>
      <label-para id="DLMs12h" deletion-status=""><label>h</label><para><text>h the brand new lettered paragraph body.</text></para></label-para>
    </para></subprov>
  </prov.body></prov>
</body></act>
"""

_ALIAS_E2E_AMENDING = b"""\
<act><body><prov id="DLM9000010"><label>10</label><heading>Amend</heading><prov.body><para>
  <text>Replace section 12(1)(h) with:</text>
  <amend>
    <subprov><label>h</label><para><text>h the brand new lettered paragraph body.</text></para></subprov>
  </amend>
</para></prov.body></prov></body></act>
"""


def _alias_archive() -> _FakeArchive:
    # Like _archive but with the alias before-tree (label-para target in the body)
    # and the alias amend payload (subprov), so the kernel must align the root kind.
    base = _archive(_ALIAS_E2E_AFTER, amending_xml=_ALIAS_E2E_AMENDING)
    base.rows["https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml"] = _ALIAS_E2E_BEFORE
    return base


def test_replace_kind_alias_agrees_after_root_kind_alignment() -> None:
    # Target addressed as paragraph (label-para) h under subsection 1 of section 12;
    # amend payload encodes it as a subprov. Extractor matches via alias; kernel
    # aligns the root kind to the live-body label-para so the oracle AGREES.
    row = _FakeWitnessRow(
        row_id="nz-opw-alias",
        target_path=(("section", "12"), ("subsection", "1"), ("paragraph", "h")),
        amended_provision="Section 12(1)(h)",
    )
    report = build_dry_run_replace(_alias_archive(), work_id=_WORK_ID, surface=_FakeSurface((row,)))
    summary = report.summary()
    assert summary["operations_dry_run"] == 1, report.refusals
    assert summary["dry_run_oracle_agreements"] == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_REPLACE_AGREES_RULE_ID
    assert proof.selected_source_path[-1] == "label-para:h"


# --- Kernel apply + oracle classification tests. -----------------------------


def test_scope_from_arg_accepts_replace() -> None:
    assert scope_from_arg("selected-family-replace") == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE


def test_replace_applies_subtree_and_agrees_with_oracle() -> None:
    report = _run(_AFTER_XML_AGREES, (_FakeWitnessRow(),))

    summary = report.summary()
    assert summary["operation_family"] == "replace"
    assert summary["operations_dry_run"] == 1
    assert summary["operations_refused"] == 0
    assert summary["dry_run_oracle_agreements"] == 1
    assert summary["dry_run_oracle_residuals"] == 0
    assert summary["neighbors_unchanged_all"] is True
    assert summary["replay_claims"] is False
    assert summary["dry_run_claims"] is True

    proof = report.proofs[0]
    assert proof.action == "replace"
    assert proof.selected_source_path == ("prov:41",)
    # Boring kernel: the target subtree is swapped; digest changes; siblings stay.
    assert proof.target_digest_before != proof.target_digest_after
    assert proof.neighbors_unchanged is True
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_REPLACE_AGREES_RULE_ID
    assert proof.replace_amending_work_id == _AMENDING_WORK_ID
    assert proof.replace_amending_provision_href == _HREF
    # Agreement is by NORMALIZED subtree signature, not raw digest: the candidate
    # (amend-payload xml_id/path) and oracle (live-body xml_id/path) nodes carry
    # different ids, so the raw subtree digests legitimately differ even on agree.
    assert proof.replace_candidate_subtree_digest
    assert proof.replace_oracle_subtree_digest
    assert proof.oracle_version_id == _AFTER_VERSION


# Oracle body where section 41 carries the SAME logical content as the flat
# amend payload, but cross-references are marked up with nested
# ``<citation><intref>...`` — the modern consolidated shape. With the
# document-order text walker the marked-up body extracts byte-identically to the
# flat payload, so the dry-run agrees; a flat ``text``+``tail`` walker would
# float the reference text to the end and report a false residual mismatch.
_AFTER_XML_AGREES_INLINE_MARKUP = b"""\
<act>
  <body>
    <prov id="DLMa41" deletion-status=""><label>41</label><heading>New heading</heading>
      <prov.body><para><text>41 New heading An order made under <citation><intref>section 12</intref></citation> takes effect on the date fixed by <citation><intref>section 13</intref></citation> for commencement.</text></para></prov.body></prov>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# Amending act whose flat ``<amend>`` payload carries the SAME content as
# ``_AFTER_XML_AGREES_INLINE_MARKUP`` but as plain text (no citation markup).
_AMENDING_XML_FLAT_REFS = b"""\
<act>
  <body>
    <prov id="DLM9000010"><label>10</label><heading>Amendments to principal Act</heading>
      <prov.body><subprov><label>1</label><para>
        <text><citation jurisdiction="nz"><extref href="DLMa41">section 41</extref></citation> is repealed and the following section substituted:</text>
        <amend>
          <prov id="newDLMa41"><label>41</label><heading>New heading</heading>
            <prov.body><para><text>41 New heading An order made under section 12 takes effect on the date fixed by section 13 for commencement.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
  </body>
</act>
"""


def test_replace_agrees_when_oracle_marks_up_cross_references_inline() -> None:
    # Regression for the inline-element text-ordering bug: identical logical
    # content (flat in the amend payload, citation-marked in the oracle body)
    # must compare EQUAL — the agreement rises because the text now extracts
    # identically, not because comparison got loose.
    report = _run(
        _AFTER_XML_AGREES_INLINE_MARKUP,
        (_FakeWitnessRow(),),
        amending_xml=_AMENDING_XML_FLAT_REFS,
    )

    summary = report.summary()
    assert summary["dry_run_oracle_agreements"] == 1
    assert summary["dry_run_oracle_residuals"] == 0
    proof = report.proofs[0]
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_REPLACE_AGREES_RULE_ID


def test_replace_residual_when_oracle_subtree_differs() -> None:
    report = _run(_AFTER_XML_MISMATCH, (_FakeWitnessRow(),))

    summary = report.summary()
    assert summary["dry_run_oracle_agreements"] == 0
    assert summary["dry_run_oracle_residuals"] == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "residual_replacement_mismatch"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_REPLACE_RESIDUAL_MISMATCH_RULE_ID
    # An honest mismatch is never counted as agreement.
    assert proof.replace_candidate_subtree_digest != proof.replace_oracle_subtree_digest


def test_replace_residual_when_target_missing_in_oracle() -> None:
    report = _run(_AFTER_XML_TARGET_MISSING, (_FakeWitnessRow(),))

    proof = report.proofs[0]
    assert proof.oracle_match == "target_missing"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID
    assert proof.oracle_target_present is False


def test_replace_substituted_family_is_in_scope() -> None:
    # The "substituted" history family drives the same kernel.
    report = _run(_AFTER_XML_AGREES, (_FakeWitnessRow(operation_family="substituted"),))
    assert report.summary()["dry_run_oracle_agreements"] == 1


def test_replace_refuses_when_payload_not_extractable() -> None:
    # The witness targets subprov:2 which the prov:41 amend payload does not carry
    # -> typed payload-not-extractable refusal, no proof.
    row = _FakeWitnessRow(target_path=(("section", "41"), ("subsection", "2")))
    report = _run(_AFTER_XML_AGREES, (row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_REPLACE_PAYLOAD_NOT_EXTRACTABLE_RULE_ID


def test_replace_refuses_when_amending_work_unresolved() -> None:
    row = _FakeWitnessRow(amending_work_id="", amending_provision_hrefs=())
    report = _run(_AFTER_XML_AGREES, (row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_REPLACE_NO_AMENDING_WORK_RULE_ID


def test_replace_no_candidate_witness_refuses_whole_work() -> None:
    # A non-replace witness is not in scope: the work refuses with the
    # no-candidate refusal and types the witness as not-in-scope.
    row = _FakeWitnessRow(operation_family="amended")
    report = _run(_AFTER_XML_AGREES, (row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    # The family-specific no-candidate rule (AGENTS §1.10 distinguishability): the
    # receipt must name this family's witness reader, not the repeal lane's.
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_NO_REPLACE_CANDIDATE_RULE_ID
    completeness = report.scope_completeness
    assert completeness is not None
    assert completeness.not_in_scope_reason_counts.get(NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPLACE_FAMILY) == 1


def test_replace_scope_completeness_types_non_candidate_target() -> None:
    # One eligible replace witness + one replace witness with a non-candidate
    # target: the census types each honestly and never inflates coverage.
    eligible = _FakeWitnessRow()
    non_candidate = _FakeWitnessRow(
        row_id="nz-opw-78",
        target_status="rejected",
        target_path=(("section", "99"),),
        amended_provision="Section 99",
    )
    report = _run(_AFTER_XML_AGREES, (eligible, non_candidate))

    completeness = report.scope_completeness
    assert completeness is not None
    assert completeness.family == "replace"
    assert completeness.total_repeal_operation_witnesses == 2  # both are replace-family
    assert completeness.repeal_witnesses_in_scope == 1
    assert (
        completeness.repeal_witnesses_not_in_scope_reason_counts.get(
            NZ_DRY_RUN_NOT_IN_SCOPE_REPLACE_TARGET_NOT_CANDIDATE
        )
        == 1
    )
    assert report.summary()["dry_run_oracle_agreements"] == 1


def test_replace_agreement_surface_is_structural_replace_named() -> None:
    report = _run(_AFTER_XML_AGREES, (_FakeWitnessRow(),))
    surface = report.agreement_surface()
    assert surface["agreement_surface"] == "nz_dry_run_structural_replace"


def test_replace_agreement_carries_no_divergence_signal() -> None:
    # An agreeing proof is never a candidate and carries no divergence class.
    report = _run(_AFTER_XML_AGREES, (_FakeWitnessRow(),))
    proof = report.proofs[0]
    assert proof.divergence_class is None
    assert proof.is_consolidation_error_candidate is False
    assert report.consolidation_error_candidates() == ()


def test_replace_substantive_whole_section_residual_is_non_commensurable() -> None:
    # The mismatch fixture replaces a whole section (prov, a container kind) whose
    # oracle body diverges substantively. The divergence is typed substantive but
    # the whole-section comparison is non-commensurable -> NOT a candidate.
    report = _run(_AFTER_XML_MISMATCH, (_FakeWitnessRow(),))
    proof = report.proofs[0]
    assert proof.oracle_match == "residual_replacement_mismatch"  # unchanged status
    assert proof.divergence_class == "substantive"
    assert proof.non_commensurable_whole_node is True
    assert proof.is_consolidation_error_candidate is False
    # Non-commensurable residuals retain no adjudication texts (kept out of set).
    assert proof.divergence_node_pairs == ()
    assert report.consolidation_error_candidates() == ()


# --- Intra-provision composition window-fit (target-citation overlap). --------


def test_instruction_target_label_path_parses_section_and_brackets() -> None:
    assert _instruction_target_label_path("Replace section 6(1)(a) with:") == ("6", "1", "a")
    assert _instruction_target_label_path("Section 358(1) is amended") == ("358", "1")
    assert _instruction_target_label_path("In section 6, after smoking, insert") == ("6",)
    # No section citation -> None (cannot be located, so not counted).
    assert _instruction_target_label_path("delete the second paragraph") is None


def test_paths_overlap_equal_ancestor_but_not_sibling() -> None:
    assert _paths_overlap(("6", "1", "a"), ("6", "1", "a")) is True  # equal
    assert _paths_overlap(("6", "1"), ("6", "1", "a")) is True  # ancestor reaches paragraph
    assert _paths_overlap(("6", "1", "a"), ("6", "1")) is True  # descendant
    assert _paths_overlap(("6", "1", "a"), ("6", "1", "c")) is False  # siblings
    assert _paths_overlap(("6", "1"), ("6", "2")) is False  # cousins


def test_node_label_path_drops_containers_keeps_provision_chain() -> None:
    assert _node_label_path(("part:1", "prov:6", "subprov:1", "label-para:a")) == ("6", "1", "a")
    assert _node_label_path(("part:14", "prov:358", "subprov:1")) == ("358", "1")


# An amending provision whose later each-place step inserts into an enclosing
# scope ("In section 6(1), after smoking, insert or vaping in each place") that
# reaches the paragraph this op replaces ("Replace section 6(1)(a) with:") — the
# oracle reflects the composed net effect, so the residual must be typed out.
_COMPOSED_EACH_PLACE_XML = b"""\
<act><body>
  <prov id="OP"><label>9</label><heading>Section 6 amended</heading><prov.body>
    <subprov><label>1</label><para><text>Replace <citation jurisdiction="nz"><extref href="x">section 6(1)(a)</extref></citation> with:</text>
      <amend><label-para><label denominator="yes">a</label><para><text>the smoking takes place only in dedicated rooms; and</text></para></label-para></amend>
    </para></subprov>
    <subprov><label>2</label><para><text>In <citation jurisdiction="nz"><extref href="y">section 6(1)</extref></citation>, after smoking, insert or vaping in each place.</text></para></subprov>
  </prov.body></prov>
</body></act>
"""

# A control: the provision's only other step targets a SIBLING paragraph
# (6(1)(c)), which does NOT overlap 6(1)(a), so the op's replace is the sole
# effect on 6(1)(a) and composition must NOT be detected.
_NON_OVERLAPPING_SIBLING_XML = b"""\
<act><body>
  <prov id="OP"><label>9</label><heading>Section 6 amended</heading><prov.body>
    <subprov><label>1</label><para><text>Replace <citation jurisdiction="nz"><extref href="x">section 6(1)(a)</extref></citation> with:</text>
      <amend><label-para><label denominator="yes">a</label><para><text>paragraph a body</text></para></label-para></amend>
    </para></subprov>
    <subprov><label>2</label><para><text>In <citation jurisdiction="nz"><extref href="z">section 6(1)(c)</extref></citation>, replace smoke with emissions.</text></para></subprov>
  </prov.body></prov>
</body></act>
"""


def test_overlap_detects_each_place_ancestor_step_composing_target() -> None:
    node = _amending_node(_COMPOSED_EACH_PLACE_XML, "OP")
    assert _amend_provision_overlaps_target_in_other_step(
        node, ("part:1", "prov:6", "subprov:1", "label-para:a")
    ) is True


def test_overlap_does_not_fire_for_non_overlapping_sibling_step() -> None:
    node = _amending_node(_NON_OVERLAPPING_SIBLING_XML, "OP")
    assert _amend_provision_overlaps_target_in_other_step(
        node, ("part:1", "prov:6", "subprov:1", "label-para:a")
    ) is False


# A later "add" step appending a sentence to the same node (s358(1)) after the
# omit/substitute step — the same shape as 2009/31 on the RMA s358(1).
_COMPOSED_ADD_XML = b"""\
<act><body>
  <prov id="OP"><label>9</label><heading>Section 358 amended</heading><prov.body>
    <subprov><label>1</label><para><text><citation jurisdiction="nz"><extref href="x">Section 358(1)</extref></citation> is amended by omitting section 357A and substituting section 357A(1)(a).</text></para></subprov>
    <subprov><label>2</label><para><text><citation jurisdiction="nz"><extref href="y">Section 358(1)</extref></citation> is amended by adding Appeals from objections are excluded.</text></para></subprov>
  </prov.body></prov>
</body></act>
"""


def test_overlap_detects_later_add_step_on_same_node() -> None:
    node = _amending_node(_COMPOSED_ADD_XML, "OP")
    assert _amend_provision_overlaps_target_in_other_step(
        node, ("part:14", "prov:358", "subprov:1")
    ) is True


# --- Omnibus-reparent manual-compilation frontier (§1.6 no unstated migration) ---
# Piece 1 (synth regression pinning the CURRENT no-migration-emitter behaviour).
# Anchors pieces 2-5 of the front-end migration-emitter lane:
#   1. this test (synth regression pinning current behaviour) — landed here;
#   2. front-end MigrationEvent emitter for the reparented provision range;
#   3. _resolve_target_nodes consults the migration map before returning empty;
#   4. real-corpus @slow regression on act_public_1992_122 @ 2010-04-01;
#   5. actual-replay promotion (migration-resolved proofs may promote cleanly).
#
# The omnibus amending act reparents a whole range of provisions (sections 84-110
# in act_public_1992_122) from Part 9 to Part 10 between archived snapshots.
# Because there is no crossing-archive migration map today, the dry-run resolves
# the target on the BEFORE path and fails to find it on the ORACLE's reparented
# path -- the §1.6 manual-compilation frontier residual (target_missing_in_oracle).
# The contract pinned here is the HONEST current behaviour; pieces 2-5 will flip
# it from honest-residual-to-conservatively-resolved WITHOUT silently widening
# the semantics.
_BEFORE_XML_REPARENT = b"""\
<act>
  <body>
    <part><label>9</label><heading>Part nine before</heading>
      <prov id="DLMa41" deletion-status=""><label>41</label><heading>Old heading</heading>
        <prov.body><para><text>41 Old heading The old body of section 41.</text></para></prov.body></prov>
    </part>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after snapshot post-amendment: section 41 is now under Part 10 (omnibus
# act reparented 41+ from Part 9 to Part 10 between snapshots). The new body
# reflects the replacement payload. Section 42 stays in-place untouched.
_AFTER_XML_REPARENTED = b"""\
<act>
  <body>
    <part><label>9</label><heading>Part nine after</heading>
      <prov id="DLMa_other" deletion-status=""><label>43</label><heading>Unrelated section in Part 9</heading>
        <prov.body><para><text>43 Placeholder Placeholder body.</text></para></prov.body></prov>
    </part>
    <part><label>10</label><heading>Part ten after</heading>
      <prov id="DLMa41" deletion-status=""><label>41</label><heading>New heading</heading>
        <prov.body><para><text>41 New heading The brand new body of section 41.</text></para></prov.body></prov>
    </part>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

_AMENDING_XML_REPARENT = b"""\
<act><body><prov id="DLM9000010"><label>10</label><heading>Amendments to principal Act</heading>
  <prov.body><subprov><label>1</label><para>
    <text><citation jurisdiction="nz"><extref href="DLMa41">section 41</extref></citation> is repealed and the following section substituted:</text>
    <amend>
      <prov id="newDLMa41"><label>41</label><heading>New heading</heading>
        <prov.body><para><text>41 New heading The brand new body of section 41.</text></para></prov.body></prov>
    </amend>
  </para></subprov></prov.body></prov></body></act>
"""


def _run_reparent(after_xml: bytes) -> NZDryRunReport:
    # The witness row declares the pre-migration target path. This is what the
    # real operation surface carries: the history note is anchored on section 41
    # under Part 9 (the address at BEFORE time).
    row = _FakeWitnessRow(
        target_path=(("part", "9"), ("section", "41")),
        amended_provision="Section 41",
    )
    archive = _archive(after_xml, amending_xml=_AMENDING_XML_REPARENT)
    archive.rows[
        "https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml"
    ] = _BEFORE_XML_REPARENT
    return build_dry_run_replace(archive, work_id=_WORK_ID, surface=_FakeSurface((row,)))


def test_replace_reparent_pinned_as_target_missing_in_oracle() -> None:
    # §1.6 manual-compilation-frontier synth regression.
    # Before piece 2 (front-end migration emitter for the reparented range), the
    # dry-run resolve_target_nodes is path-exact and matches no oracle node at
    # part:9/prov:41 (the section moved to part:10). The residual is honestly
    # classified as nz_dry_run_structural_replace_residual_target_missing_in_oracle
    # -- NOT a guessed agreement -- so the migration emitter lane is bounded
    # (piece 2 flips it to a migration-resolved target without forcing a sibling).
    report = _run_reparent(_AFTER_XML_REPARENTED)

    assert report.summary()["dry_run_oracle_agreements"] == 0
    assert report.summary()["dry_run_oracle_residuals"] == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "target_missing"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID
    assert proof.oracle_target_present is False
    # The pre-migration path is the resolved one (the section DID live there in
    # the before snapshot). piece 2 will carry the post-migration path as the
    # migration-resolved resolution lane; this assertion pins the current path
    # so the subsequent change is verifiable.
    assert proof.selected_source_path == ("part:9", "prov:41")
