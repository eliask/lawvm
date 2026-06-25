"""Structural whole-provision INSERT dry-run kernel tests.

These exercise the insert kernel: it extracts the new provision body from an
amending act's ``<amend>`` subtree (the per-witness label selects the single
inserted node even from a one-to-many "insert the following sections" subtree),
derives an anchor sibling from the inserted node's suffix-letter label
(``18A`` -> after ``18``), inserts the new node next to that anchor, and
classifies whether the on-or-after oracle carries the new node at its address
with matching content. The kernel never guesses a position, never counts an
absent/different new node as agreement, and proves the anchor + pre-existing
siblings unchanged (insertion is additive).
"""

from __future__ import annotations

import json

from lxml import etree

from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_INSERT_AGREES_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_CONTENT_MISMATCH_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_NOT_PRESENT_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_POSITION_MISMATCH_RULE_ID,
    NZ_DRY_RUN_NOT_IN_SCOPE_INSERT_TARGET_NOT_CANDIDATE,
    NZ_DRY_RUN_NOT_IN_SCOPE_NON_INSERT_FAMILY,
    NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_DERIVABLE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_IN_BEFORE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_NESTED_ANCHOR_NOT_DERIVABLE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_NO_AMENDING_WORK_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_PARENT_NOT_IN_BEFORE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_PAYLOAD_NOT_EXTRACTABLE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_TARGET_ALREADY_IN_BEFORE_RULE_ID,
    NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
    _derive_insert_anchor,
    _derive_nested_insert_anchor,
    _derive_top_level_insert_anchor,
    _insert_block_member_labels,
    _is_before_tree_dependent_insert_label,
    build_dry_run_insert,
    scope_from_arg,
)
from lawvm.new_zealand.source_tree import (
    NZ_STRUCTURAL_BLOCKED_SCHEDULE_GROUP_UNRESOLVED,
    NZ_STRUCTURAL_BLOCKED_SCHEDULE_NO_MATCHING_CHILD,
    NZ_STRUCTURAL_BLOCKED_SCHEDULE_UNRESOLVED_PLACEHOLDER,
    NZ_STRUCTURAL_INSERT_BLOCKED_AMBIGUOUS_MATCH,
    NZ_STRUCTURAL_INSERT_BLOCKED_NO_AMEND_SUBTREE,
    NZ_STRUCTURAL_INSERT_BLOCKED_NO_MATCHING_CHILD,
    NZ_STRUCTURAL_INSERT_BLOCKED_SCHEDULE_INDIRECTION,
    NZStructuralReplacement,
    extract_structural_insertion,
)

_WORK_ID = "act_public_2005_99"
_AMENDING_WORK_ID = "act_public_2019_5"
_BEFORE_VERSION = "act_public_2005_99_en_2018-01-01"
_AFTER_VERSION = "act_public_2005_99_en_2019-10-24"
_AMENDING_VERSION = "act_public_2019_5_en_2019-10-24"
_HREF = "DLM9000010"

# Principal act before the amendment: section 18 (anchor) present, 18A absent.
_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLMa18" deletion-status=""><label>18</label><heading>Anchor section</heading>
      <prov.body><para><text>18 Anchor section The body of section 18.</text></para></prov.body></prov>
    <prov id="DLMa19" deletion-status=""><label>19</label><heading>Neighbour</heading>
      <prov.body><para><text>19 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where the new section 18A is present between 18 and 19 with the
# brand new body; 18 and 19 untouched.
_AFTER_XML_AGREES = b"""\
<act>
  <body>
    <prov id="DLMa18" deletion-status=""><label>18</label><heading>Anchor section</heading>
      <prov.body><para><text>18 Anchor section The body of section 18.</text></para></prov.body></prov>
    <prov id="DLMa18A" deletion-status=""><label>18A</label><heading>New inserted section</heading>
      <prov.body><para><text>18A New inserted section The brand new body of section 18A.</text></para></prov.body></prov>
    <prov id="DLMa19" deletion-status=""><label>19</label><heading>Neighbour</heading>
      <prov.body><para><text>19 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where 18A is present but carries different content -> honest
# content-mismatch residual (never agreement).
_AFTER_XML_MISMATCH = b"""\
<act>
  <body>
    <prov id="DLMa18" deletion-status=""><label>18</label><heading>Anchor section</heading>
      <prov.body><para><text>18 Anchor section The body of section 18.</text></para></prov.body></prov>
    <prov id="DLMa18A" deletion-status=""><label>18A</label><heading>Different heading</heading>
      <prov.body><para><text>18A Different heading An entirely different inserted body.</text></para></prov.body></prov>
    <prov id="DLMa19" deletion-status=""><label>19</label><heading>Neighbour</heading>
      <prov.body><para><text>19 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where the new section 18A never appears -> not-present residual.
_AFTER_XML_NOT_PRESENT = _BEFORE_XML

# Amending act: provision at _HREF carries an <amend> subtree inserting BOTH 18A
# and 18B ("the following sections are inserted") - a one-to-many subtree. The
# per-witness label selects the single inserted node (18A).
_AMENDING_XML = b"""\
<act>
  <body>
    <prov id="DLM9000010"><label>10</label><heading>New sections inserted</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following sections are inserted after <citation jurisdiction="nz"><extref href="DLMa18">section 18</extref></citation>:</text>
        <amend>
          <prov id="newDLMa18A"><label>18A</label><heading>New inserted section</heading>
            <prov.body><para><text>18A New inserted section The brand new body of section 18A.</text></para></prov.body></prov>
          <prov id="newDLMa18B"><label>18B</label><heading>Second inserted section</heading>
            <prov.body><para><text>18B Second inserted section The body of section 18B.</text></para></prov.body></prov>
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
    def __init__(self, target_address_status: str, address: str, path: tuple[tuple[str, str], ...]) -> None:
        self.target_address_status = target_address_status
        self.address = address
        self.path = path


def _addr_for_path(path: tuple[tuple[str, str], ...]) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in path)


class _FakeWitnessRow:
    def __init__(
        self,
        *,
        row_id: str = "nz-opw-60",
        operation_family: str = "inserted",
        target_status: str = "candidate",
        target_path: tuple[tuple[str, str], ...] = (("section", "18A"),),
        amended_provision: str = "Section 18A",
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
            target_status, _addr_for_path(target_path), target_path
        )


class _FakeSurface:
    def __init__(self, rows: tuple[_FakeWitnessRow, ...]) -> None:
        self.rows = rows


def _run(after_xml: bytes, rows: tuple[_FakeWitnessRow, ...], *, amending_xml: bytes = _AMENDING_XML):
    archive = _archive(after_xml, amending_xml=amending_xml)
    return build_dry_run_insert(archive, work_id=_WORK_ID, surface=_FakeSurface(rows))


# --- Anchor derivation unit tests. -------------------------------------------


def test_anchor_derivation_suffix_a_goes_after_bare_stem() -> None:
    assert _derive_insert_anchor("prov", "18A") == ("18", "after")
    assert _derive_insert_anchor("part", "5A") == ("5", "after")


def test_anchor_derivation_later_suffix_goes_after_previous_letter() -> None:
    assert _derive_insert_anchor("prov", "18B") == ("18A", "after")
    assert _derive_insert_anchor("prov", "24C") == ("24B", "after")


def test_anchor_derivation_refuses_non_suffixed_label() -> None:
    # A bare numeric label has no derivable anchor (would be a guessed position).
    assert _derive_insert_anchor("prov", "18") is None


def test_anchor_derivation_refuses_multi_letter_suffix() -> None:
    assert _derive_insert_anchor("prov", "18AA") is None


# --- Before-tree-dependent (bare-numeric / multi-letter) anchor unit tests. ---


def test_before_tree_dependent_label_classifies_bare_numeric_and_multi_letter() -> None:
    assert _is_before_tree_dependent_insert_label("7") is True
    assert _is_before_tree_dependent_insert_label("100") is True
    assert _is_before_tree_dependent_insert_label("14AB") is True
    assert _is_before_tree_dependent_insert_label("147ZA") is True
    # Single-trailing-letter convention is derived up front, not deferred.
    assert _is_before_tree_dependent_insert_label("18A") is False
    # Genuinely non-derivable labels are not deferred (refused up front).
    assert _is_before_tree_dependent_insert_label("IV") is False
    assert _is_before_tree_dependent_insert_label("") is False


def test_top_level_anchor_bare_numeric_after_greatest_smaller_predecessor() -> None:
    # Section 7 inserted where 6 (and lower) exist -> after 6.
    assert _derive_top_level_insert_anchor("7", ("4", "5", "6", "8", "9")) == ("6", "after")


def test_top_level_anchor_bare_numeric_uses_numeric_not_lexical_ordering() -> None:
    # 100 must anchor after 99 (numeric), not after "9" (lexical max of digits).
    assert _derive_top_level_insert_anchor("100", ("9", "10", "99", "101")) == ("99", "after")
    # 7 after 6 even though "60" sorts lexically after "7".
    assert _derive_top_level_insert_anchor("7", ("6", "60", "70")) == ("6", "after")


def test_top_level_anchor_bare_numeric_without_smaller_predecessor_refused() -> None:
    # 1 would be first; no smaller numeric sibling -> refuse (never guess append).
    assert _derive_top_level_insert_anchor("1", ("2", "3")) is None
    # Only larger or non-numeric siblings -> refuse.
    assert _derive_top_level_insert_anchor("5", ("6", "7")) is None


def test_top_level_anchor_bare_numeric_ignores_suffixed_siblings() -> None:
    # Suffixed siblings (6A) are not numeric predecessors of a bare numeric;
    # with only 6A present and no bare predecessor, refuse.
    assert _derive_top_level_insert_anchor("7", ("6A", "8")) is None


def test_top_level_anchor_multi_letter_suffix_after_stripped_predecessor() -> None:
    assert _derive_top_level_insert_anchor("14AB", ("14", "14A", "15")) == ("14A", "after")
    assert _derive_top_level_insert_anchor("147ZA", ("147", "147Z", "148")) == ("147Z", "after")
    assert _derive_top_level_insert_anchor("18AA", ("18", "18A")) == ("18A", "after")


def test_top_level_anchor_multi_letter_suffix_missing_predecessor_refused() -> None:
    # 14AB needs 14A present; only 14 present -> refuse (no guessed position).
    assert _derive_top_level_insert_anchor("14AB", ("14", "15")) is None


# --- New-node payload extractor unit tests. ----------------------------------


def _amending_node(xml: bytes, href: str):
    root = etree.fromstring(xml)
    for element in root.iter():
        if isinstance(element.tag, str) and element.attrib.get("id") == href:
            return element
    raise AssertionError(f"href {href} not found")


def test_extractor_selects_single_node_from_one_to_many_subtree() -> None:
    node = _amending_node(_AMENDING_XML, _HREF)
    # The amend subtree inserts both 18A and 18B; the label selects 18A cleanly.
    result = extract_structural_insertion(node, inserted_leaf_kind="prov", inserted_leaf_label="18A")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.kind == "prov"
    assert result.root.label == "18A"
    assert "brand new body" in result.root.text


def test_extractor_selects_the_other_node_by_label() -> None:
    node = _amending_node(_AMENDING_XML, _HREF)
    result = extract_structural_insertion(node, inserted_leaf_kind="prov", inserted_leaf_label="18B")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "18B"
    assert "section 18B" in result.root.text


def test_extractor_blocks_when_no_amend_subtree() -> None:
    root = etree.fromstring(
        b"<act><body><prov id='X'><prov.body><para><text>plain prose, no amend.</text></para></prov.body></prov></body></act>"
    )
    node = next(e for e in root.iter() if e.attrib.get("id") == "X")
    result = extract_structural_insertion(node, inserted_leaf_kind="prov", inserted_leaf_label="18A")
    assert result == NZ_STRUCTURAL_INSERT_BLOCKED_NO_AMEND_SUBTREE


def test_extractor_blocks_when_no_child_matches_inserted_leaf() -> None:
    node = _amending_node(_AMENDING_XML, _HREF)
    result = extract_structural_insertion(node, inserted_leaf_kind="prov", inserted_leaf_label="99A")
    assert result == NZ_STRUCTURAL_INSERT_BLOCKED_NO_MATCHING_CHILD


# --- Nested-payload descendant matching. --------------------------------------
#
# A newly-inserted section frequently lives INSIDE a newly-inserted Part/subpart
# in the amend subtree (the spec example: new section 147A inside new Part 9), not
# as a direct amend child. The descendant lane finds it; the top-level path is
# tried first so a clean top-level extraction is unchanged; >1-match still refuses.

_NESTED_INSERT_PART = b"""\
<act><body><prov id="INSNEST"><prov.body><para>
  <text>After Part 8, insert:</text>
  <amend>
    <part><label>9</label><heading>New Part nine</heading>
      <prov><label>147A</label><heading>New nested section</heading>
        <prov.body><para><text>147A New nested section The brand new body of section 147A.</text></para></prov.body></prov>
    </part>
  </amend>
</para></prov.body></prov></body></act>
"""

_NESTED_INSERT_SUBPART = b"""\
<act><body><prov id="INSSUB"><prov.body><para>
  <text>After Part 2, insert:</text>
  <amend>
    <part><label>3</label><heading>New Part three</heading>
      <subpart><label>1</label><heading>Subpart one</heading>
        <prov><label>40A</label><heading>Deep new section</heading>
          <prov.body><para><text>40A Deep new section The brand new deeply nested section 40A.</text></para></prov.body></prov>
      </subpart>
    </part>
  </amend>
</para></prov.body></prov></body></act>
"""


def test_insert_extractor_descends_into_new_part_for_nested_section() -> None:
    # New section 147A inside new Part 9 (spec example): the descendant lane pulls
    # the nested provision out as the inserted node.
    node = _amending_node(_NESTED_INSERT_PART, "INSNEST")
    result = extract_structural_insertion(node, inserted_leaf_kind="prov", inserted_leaf_label="147A")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.kind == "prov"
    assert result.root.label == "147A"
    assert "brand new body of section 147A" in result.root.text


def test_insert_extractor_descends_through_subpart() -> None:
    node = _amending_node(_NESTED_INSERT_SUBPART, "INSSUB")
    result = extract_structural_insertion(node, inserted_leaf_kind="prov", inserted_leaf_label="40A")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "40A"
    assert "deeply nested section 40A" in result.root.text


def test_insert_extractor_descent_refuses_ambiguous_nested_leaf() -> None:
    # The same inserted-section label nested under two different new Parts is a
    # genuine ambiguity the descendant lane must refuse (never guess).
    xml = b"""\
<act><body><prov id="INSNESTAMB"><prov.body><para>
  <text>Insert the new Parts:</text>
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
    node = _amending_node(xml, "INSNESTAMB")
    result = extract_structural_insertion(node, inserted_leaf_kind="prov", inserted_leaf_label="147A")
    assert result == NZ_STRUCTURAL_INSERT_BLOCKED_AMBIGUOUS_MATCH


def test_insert_extractor_prefers_top_level_over_nested() -> None:
    # A top-level inserted section 18A AND a nested decoy 18A inside a new Part: the
    # top-level path owns the extraction, so the nested decoy is never reached.
    xml = b"""\
<act><body><prov id="INSTOPVN"><prov.body><para>
  <text>Insert section 18A and a new Part:</text>
  <amend>
    <prov><label>18A</label><heading>Top level</heading><prov.body><para><text>18A The top-level inserted body.</text></para></prov.body></prov>
    <part><label>5</label><heading>Part five</heading>
      <prov><label>18A</label><heading>Nested decoy</heading><prov.body><para><text>18A A nested decoy.</text></para></prov.body></prov>
    </part>
  </amend>
</para></prov.body></prov></body></act>
"""
    node = _amending_node(xml, "INSTOPVN")
    result = extract_structural_insertion(node, inserted_leaf_kind="prov", inserted_leaf_label="18A")
    assert isinstance(result, NZStructuralReplacement)
    assert "top-level inserted body" in result.root.text
    assert "nested decoy" not in result.root.text


def test_extractor_blocks_genuinely_ambiguous_duplicate_label() -> None:
    xml = b"""\
<act><body><prov id="AMB"><prov.body><subprov><label>1</label><para>
  <text>The following sections are inserted:</text>
  <amend>
    <prov id="a"><label>18A</label><para><text>18A First.</text></para></prov>
    <prov id="b"><label>18A</label><para><text>18A Second duplicate.</text></para></prov>
  </amend></para></subprov></prov.body></prov></body></act>
"""
    node = _amending_node(xml, "AMB")
    result = extract_structural_insertion(node, inserted_leaf_kind="prov", inserted_leaf_label="18A")
    assert result == NZ_STRUCTURAL_INSERT_BLOCKED_AMBIGUOUS_MATCH


def test_insert_extractor_matches_subprov_target_against_label_para_payload() -> None:
    # The inserted leaf is addressed as a subprov but the amend payload encodes it
    # as a label-para (the interchangeable lettered-paragraph alias). The extractor
    # matches on the exact label across the kind alias.
    xml = b"""\
<act><body><prov id="ALIASINS"><prov.body><para>
  <text>In section 9, insert:</text>
  <amend>
    <label-para><label>fa</label><para><text>fa the newly inserted lettered paragraph.</text></para></label-para>
  </amend></para></prov.body></prov></body></act>
"""
    node = _amending_node(xml, "ALIASINS")
    result = extract_structural_insertion(node, inserted_leaf_kind="subprov", inserted_leaf_label="fa")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "fa"
    assert "newly inserted lettered paragraph" in result.root.text


def test_insert_extractor_kind_alias_does_not_collapse_genuine_ambiguity() -> None:
    # Both a subprov "fa" AND a label-para "fa" are present; with the alias both
    # match the target leaf on label "fa" -> genuine ambiguity, stays blocked.
    xml = b"""\
<act><body><prov id="ALIASINSAMB"><prov.body><para>
  <text>In section 9, insert:</text>
  <amend>
    <subprov><label>fa</label><para><text>fa first.</text></para></subprov>
    <label-para><label>fa</label><para><text>fa second.</text></para></label-para>
  </amend></para></prov.body></prov></body></act>
"""
    node = _amending_node(xml, "ALIASINSAMB")
    result = extract_structural_insertion(node, inserted_leaf_kind="subprov", inserted_leaf_label="fa")
    assert result == NZ_STRUCTURAL_INSERT_BLOCKED_AMBIGUOUS_MATCH


_INSERT_CROSS_SECTION_XML = b"""\
<act><body><prov id="XSEC"><prov.body>
  <para><text>In <citation jurisdiction="nz"><extref href="s12">section 12</extref></citation>, insert:</text>
    <amend><subprov><label>4A</label><para><text>4A New section 12 subsection.</text></para></subprov></amend></para>
  <para><text>In <citation jurisdiction="nz"><extref href="s40">section 40</extref></citation>, insert:</text>
    <amend><subprov><label>4A</label><para><text>4A New section 40 subsection.</text></para></subprov></amend></para>
</prov.body></prov></body></act>
"""


def test_insert_extractor_disambiguates_cross_section_collision_by_provision() -> None:
    # subprov 4A is inserted into BOTH section 12 and section 40. Leaf-only is
    # ambiguous; the witness's enclosing section selects exactly one.
    node = _amending_node(_INSERT_CROSS_SECTION_XML, "XSEC")
    s12 = extract_structural_insertion(
        node, inserted_leaf_kind="subprov", inserted_leaf_label="4A", target_provision_label="12"
    )
    assert isinstance(s12, NZStructuralReplacement)
    assert "section 12 subsection" in s12.root.text
    s40 = extract_structural_insertion(
        node, inserted_leaf_kind="subprov", inserted_leaf_label="4A", target_provision_label="40"
    )
    assert isinstance(s40, NZStructuralReplacement)
    assert "section 40 subsection" in s40.root.text


def test_insert_extractor_cross_section_collision_stays_ambiguous_without_provision() -> None:
    # Single-argument behaviour unchanged: no section label -> typed ambiguity.
    node = _amending_node(_INSERT_CROSS_SECTION_XML, "XSEC")
    result = extract_structural_insertion(node, inserted_leaf_kind="subprov", inserted_leaf_label="4A")
    assert result == NZ_STRUCTURAL_INSERT_BLOCKED_AMBIGUOUS_MATCH


# --- Kernel apply + oracle classification tests. -----------------------------


def test_scope_from_arg_accepts_insert() -> None:
    assert scope_from_arg("selected-family-insert") == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT


def test_insert_adds_new_node_after_anchor_and_agrees_with_oracle() -> None:
    report = _run(_AFTER_XML_AGREES, (_FakeWitnessRow(),))

    summary = report.summary()
    assert summary["operation_family"] == "insert"
    assert summary["operations_dry_run"] == 1
    assert summary["operations_refused"] == 0
    assert summary["dry_run_oracle_agreements"] == 1
    assert summary["dry_run_oracle_residuals"] == 0
    assert summary["neighbors_unchanged_all"] is True
    assert summary["replay_claims"] is False
    assert summary["dry_run_claims"] is True

    proof = report.proofs[0]
    assert proof.action == "insert"
    # The new node lands among the anchor's siblings, addressed by its own label.
    assert proof.insert_new_node_source_path[-1] == "prov:18A"
    assert proof.insert_anchor_source_path[-1] == "prov:18"
    assert proof.insert_direction == "after"
    # Insert is additive: the new node did not exist before; neighbours unchanged.
    assert proof.target_digest_before == ""
    assert proof.target_digest_after
    assert proof.occupancy_before == "absent"
    assert proof.neighbors_unchanged is True
    # The anchor is an unchanged neighbour (equal before/after digests).
    assert proof.insert_anchor_digest_before == proof.insert_anchor_digest_after
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_INSERT_AGREES_RULE_ID
    assert proof.insert_amending_work_id == _AMENDING_WORK_ID
    assert proof.insert_amending_provision_href == _HREF
    assert proof.oracle_version_id == _AFTER_VERSION


def test_insert_residual_when_new_node_content_differs() -> None:
    report = _run(_AFTER_XML_MISMATCH, (_FakeWitnessRow(),))

    summary = report.summary()
    assert summary["dry_run_oracle_agreements"] == 0
    assert summary["dry_run_oracle_residuals"] == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "residual_insert_content_mismatch"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_INSERT_RESIDUAL_CONTENT_MISMATCH_RULE_ID
    # An honest mismatch is never counted as agreement.
    assert proof.insert_candidate_subtree_digest != proof.insert_oracle_subtree_digest
    assert proof.oracle_target_present is True


def test_insert_residual_when_new_node_absent_from_oracle() -> None:
    report = _run(_AFTER_XML_NOT_PRESENT, (_FakeWitnessRow(),))

    proof = report.proofs[0]
    assert proof.oracle_match == "residual_insert_not_present"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_INSERT_RESIDUAL_NOT_PRESENT_RULE_ID
    assert proof.oracle_target_present is False


def test_insert_added_family_is_in_scope() -> None:
    # The "added" history family drives the same insert kernel.
    report = _run(_AFTER_XML_AGREES, (_FakeWitnessRow(operation_family="added"),))
    assert report.summary()["dry_run_oracle_agreements"] == 1


def test_insert_refuses_when_anchor_not_derivable() -> None:
    # A Roman-numeral inserted label has no derivable anchor under any convention
    # (not single-suffix, not bare-numeric, not multi-letter suffix) -> refused up
    # front before any before-tree dependency.
    row = _FakeWitnessRow(target_path=(("section", "IV"),), amended_provision="Section IV")
    report = _run(_AFTER_XML_AGREES, (row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_DERIVABLE_RULE_ID


def test_nested_insert_refuses_when_anchor_not_derivable_from_sibling_group() -> None:
    # A nested bare-numeric subsection (9) whose numeric predecessor (8) is absent
    # from the sibling group is a renumber/append we will not guess. The payload
    # extracts cleanly, so the refusal is specifically the nested-anchor one.
    row = _FakeWitnessRow(
        target_path=(("section", "20"), ("subsection", "9")),
        amended_provision="Section 20(9)",
        amending_provision_hrefs=(_NESTED_HREF_GAP,),
    )
    report = _run_nested((row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_NESTED_ANCHOR_NOT_DERIVABLE_RULE_ID


def test_insert_refuses_when_new_node_already_present_in_before() -> None:
    # The before tree already carries section 18A, so inserting it is a no-op /
    # wrong-window case: the kernel refuses (an insert must ADD an absent node).
    before_with_18a = _AFTER_XML_AGREES  # this body already has 18A present
    archive = _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{_BEFORE_VERSION}/": _version_detail(
                _BEFORE_VERSION, _WORK_ID, "99", "2018-01-01"
            ),
            "https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml": before_with_18a,
            f"https://api.legislation.govt.nz/v0/versions/{_AFTER_VERSION}/": _version_detail(
                _AFTER_VERSION, _WORK_ID, "99", "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2019/99/en/2019-10-24.xml": _AFTER_XML_AGREES,
            f"https://api.legislation.govt.nz/v0/works/{_AMENDING_WORK_ID}/versions/": json.dumps(
                {"versions": [{"version_id": _AMENDING_VERSION, "date.as.at": "2019-10-24"}]}
            ).encode(),
            f"https://api.legislation.govt.nz/v0/versions/{_AMENDING_VERSION}/": _version_detail(
                _AMENDING_VERSION, _AMENDING_WORK_ID, "5", "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2019/5/en/2019-10-24.xml": _AMENDING_XML,
        }
    )
    report = build_dry_run_insert(archive, work_id=_WORK_ID, surface=_FakeSurface((_FakeWitnessRow(),)))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_TARGET_ALREADY_IN_BEFORE_RULE_ID


def test_insert_refuses_when_anchor_not_in_before() -> None:
    # Inserted node 18B has an extractable payload (the amend subtree carries 18B)
    # but its derived anchor is 18A, which is absent from the before tree.
    row = _FakeWitnessRow(target_path=(("section", "18B"),), amended_provision="Section 18B")
    report = _run(_AFTER_XML_AGREES, (row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_IN_BEFORE_RULE_ID


def test_insert_refuses_when_payload_not_extractable() -> None:
    # The witness inserts 18C, which the amend subtree (18A/18B) does not carry.
    row = _FakeWitnessRow(target_path=(("section", "18C"),), amended_provision="Section 18C")
    report = _run(_AFTER_XML_AGREES, (row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_PAYLOAD_NOT_EXTRACTABLE_RULE_ID


def test_insert_refuses_when_amending_work_unresolved() -> None:
    row = _FakeWitnessRow(amending_work_id="", amending_provision_hrefs=())
    report = _run(_AFTER_XML_AGREES, (row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_NO_AMENDING_WORK_RULE_ID


def test_insert_no_candidate_witness_refuses_whole_work() -> None:
    row = _FakeWitnessRow(operation_family="amended")
    report = _run(_AFTER_XML_AGREES, (row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    # The family-specific no-candidate rule (AGENTS §1.10 distinguishability): the
    # receipt must name this family's witness reader, not the repeal lane's.
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID
    completeness = report.scope_completeness
    assert completeness is not None
    assert completeness.not_in_scope_reason_counts.get(NZ_DRY_RUN_NOT_IN_SCOPE_NON_INSERT_FAMILY) == 1


def test_insert_scope_completeness_types_non_candidate_target() -> None:
    eligible = _FakeWitnessRow()
    non_candidate = _FakeWitnessRow(
        row_id="nz-opw-61",
        target_status="rejected",
        target_path=(("section", "21A"),),
        amended_provision="Section 21A",
    )
    report = _run(_AFTER_XML_AGREES, (eligible, non_candidate))

    completeness = report.scope_completeness
    assert completeness is not None
    assert completeness.family == "insert"
    assert completeness.total_repeal_operation_witnesses == 2  # both are insert-family
    assert completeness.repeal_witnesses_in_scope == 1
    assert (
        completeness.repeal_witnesses_not_in_scope_reason_counts.get(
            NZ_DRY_RUN_NOT_IN_SCOPE_INSERT_TARGET_NOT_CANDIDATE
        )
        == 1
    )
    assert report.summary()["dry_run_oracle_agreements"] == 1


def test_insert_agreement_surface_is_structural_insert_named() -> None:
    report = _run(_AFTER_XML_AGREES, (_FakeWitnessRow(),))
    surface = report.agreement_surface()
    assert surface["agreement_surface"] == "nz_dry_run_structural_insert"


# =============================================================================
# Whole-provision bare-numeric / multi-letter suffix insert (the anchor is an
# EXISTING sibling derived against the before-tree's top-level sibling group):
# "section 7 inserted" -> after section 6; "section 14AB inserted" -> after 14A.
# =============================================================================

# Before: sections 6 and 8 present (numeric gap at 7), plus section 14A (anchor
# for a multi-letter 14AB insert). Section 7 / 14AB absent.
_NUMERIC_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLMb6" deletion-status=""><label>6</label><heading>Section six</heading>
      <prov.body><para><text>6 Section six The body of section 6.</text></para></prov.body></prov>
    <prov id="DLMb8" deletion-status=""><label>8</label><heading>Section eight</heading>
      <prov.body><para><text>8 Section eight The body of section 8.</text></para></prov.body></prov>
    <prov id="DLMb14A" deletion-status=""><label>14A</label><heading>Section fourteen A</heading>
      <prov.body><para><text>14A Section fourteen A The body of section 14A.</text></para></prov.body></prov>
  </body>
</act>
"""

# After: section 7 present between 6 and 8 with the new body; 14AB present after
# 14A. Pre-existing sections untouched.
_NUMERIC_AFTER_XML_AGREES = b"""\
<act>
  <body>
    <prov id="DLMb6" deletion-status=""><label>6</label><heading>Section six</heading>
      <prov.body><para><text>6 Section six The body of section 6.</text></para></prov.body></prov>
    <prov id="DLMb7" deletion-status=""><label>7</label><heading>New section seven</heading>
      <prov.body><para><text>7 New section seven The brand new body of section 7.</text></para></prov.body></prov>
    <prov id="DLMb8" deletion-status=""><label>8</label><heading>Section eight</heading>
      <prov.body><para><text>8 Section eight The body of section 8.</text></para></prov.body></prov>
    <prov id="DLMb14A" deletion-status=""><label>14A</label><heading>Section fourteen A</heading>
      <prov.body><para><text>14A Section fourteen A The body of section 14A.</text></para></prov.body></prov>
    <prov id="DLMb14AB" deletion-status=""><label>14AB</label><heading>New section fourteen AB</heading>
      <prov.body><para><text>14AB New section fourteen AB The brand new body of section 14AB.</text></para></prov.body></prov>
  </body>
</act>
"""

# Amending act: a single amend subtree inserting section 7, and another inserting
# 14AB. Per-witness label selects the single inserted node.
_NUMERIC_AMENDING_XML = b"""\
<act>
  <body>
    <prov id="DLMins7"><label>3</label><heading>New section inserted</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following section is inserted after <citation jurisdiction="nz"><extref href="DLMb6">section 6</extref></citation>:</text>
        <amend>
          <prov id="newDLMb7"><label>7</label><heading>New section seven</heading>
            <prov.body><para><text>7 New section seven The brand new body of section 7.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
    <prov id="DLMins14AB"><label>4</label><heading>New section inserted</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following section is inserted after <citation jurisdiction="nz"><extref href="DLMb14A">section 14A</extref></citation>:</text>
        <amend>
          <prov id="newDLMb14AB"><label>14AB</label><heading>New section fourteen AB</heading>
            <prov.body><para><text>14AB New section fourteen AB The brand new body of section 14AB.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
    <prov id="DLMins8"><label>5</label><heading>Section eight (already present)</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following section is inserted:</text>
        <amend>
          <prov id="newDLMb8dup"><label>8</label><heading>Section eight</heading>
            <prov.body><para><text>8 Section eight The body of section 8.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
  </body>
</act>
"""

_NUMERIC_HREF_7 = "DLMins7"
_NUMERIC_HREF_14AB = "DLMins14AB"
_NUMERIC_HREF_8 = "DLMins8"


def _run_numeric(rows: tuple[_FakeWitnessRow, ...], *, after_xml: bytes = _NUMERIC_AFTER_XML_AGREES):
    base = _archive(after_xml, amending_xml=_NUMERIC_AMENDING_XML)
    base.rows["https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml"] = _NUMERIC_BEFORE_XML
    return build_dry_run_insert(base, work_id=_WORK_ID, surface=_FakeSurface(rows))


def test_bare_numeric_insert_lands_after_numeric_predecessor_and_agrees() -> None:
    # Section 7 inserted; predecessor 6 present (8 follows). Anchor = 6.
    row = _FakeWitnessRow(
        target_path=(("section", "7"),),
        amended_provision="Section 7",
        amending_provision_hrefs=(_NUMERIC_HREF_7,),
    )
    report = _run_numeric((row,))
    assert report.summary()["dry_run_oracle_agreements"] == 1
    assert report.summary()["operations_refused"] == 0
    proof = report.proofs[0]
    assert proof.insert_new_node_source_path[-1] == "prov:7"
    assert proof.insert_anchor_source_path[-1] == "prov:6"
    assert proof.insert_direction == "after"
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_INSERT_AGREES_RULE_ID
    assert proof.neighbors_unchanged is True


def test_multi_letter_suffix_insert_lands_after_stripped_predecessor_and_agrees() -> None:
    # Section 14AB inserted; predecessor 14A present. Anchor = 14A.
    row = _FakeWitnessRow(
        target_path=(("section", "14AB"),),
        amended_provision="Section 14AB",
        amending_provision_hrefs=(_NUMERIC_HREF_14AB,),
    )
    report = _run_numeric((row,))
    assert report.summary()["dry_run_oracle_agreements"] == 1
    proof = report.proofs[0]
    assert proof.insert_new_node_source_path[-1] == "prov:14AB"
    assert proof.insert_anchor_source_path[-1] == "prov:14A"
    assert proof.insert_direction == "after"
    assert proof.oracle_match == "agrees"


def test_bare_numeric_insert_refuses_when_no_smaller_predecessor() -> None:
    # Section 1 inserted but the before-tree has no smaller numeric sibling -> a
    # renumber/append we will not guess. Honest anchor-not-derivable refusal.
    amending = b"""\
<act>
  <body>
    <prov id="DLMins1"><label>3</label><heading>New section inserted</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following section is inserted:</text>
        <amend>
          <prov id="newDLMb1"><label>1</label><heading>New first section</heading>
            <prov.body><para><text>1 New first section The body of section 1.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
  </body>
</act>
"""
    base = _archive(_NUMERIC_AFTER_XML_AGREES, amending_xml=amending)
    base.rows["https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml"] = _NUMERIC_BEFORE_XML
    row = _FakeWitnessRow(
        target_path=(("section", "1"),),
        amended_provision="Section 1",
        amending_provision_hrefs=("DLMins1",),
    )
    report = build_dry_run_insert(base, work_id=_WORK_ID, surface=_FakeSurface((row,)))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_DERIVABLE_RULE_ID


def test_multi_letter_suffix_insert_refuses_when_predecessor_absent() -> None:
    # Section 14CD inserted but predecessor 14C absent from before-tree -> refuse.
    amending = b"""\
<act>
  <body>
    <prov id="DLMins14CD"><label>3</label><heading>New section inserted</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following section is inserted:</text>
        <amend>
          <prov id="newDLMb14CD"><label>14CD</label><heading>New section</heading>
            <prov.body><para><text>14CD New section The body.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
  </body>
</act>
"""
    base = _archive(_NUMERIC_AFTER_XML_AGREES, amending_xml=amending)
    base.rows["https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml"] = _NUMERIC_BEFORE_XML
    row = _FakeWitnessRow(
        target_path=(("section", "14CD"),),
        amended_provision="Section 14CD",
        amending_provision_hrefs=("DLMins14CD",),
    )
    report = build_dry_run_insert(base, work_id=_WORK_ID, surface=_FakeSurface((row,)))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_DERIVABLE_RULE_ID


def test_bare_numeric_insert_already_present_in_before_refuses() -> None:
    # Section 8 already exists in the before-tree -> not an insert (refuse).
    row = _FakeWitnessRow(
        target_path=(("section", "8"),),
        amended_provision="Section 8",
        amending_provision_hrefs=(_NUMERIC_HREF_8,),
    )
    report = _run_numeric((row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_TARGET_ALREADY_IN_BEFORE_RULE_ID


# --- Position guard: a derived anchor that is content-correct but lands the new
# node in the wrong position is a position residual, NEVER agreement. This is the
# block-insert hazard: section 8 inserted when the before-tree has only section 6
# derives anchor=6, but the oracle places 8 after section 7 (also newly present),
# so the oracle's real predecessor (7) != the derived anchor (6). -------------

# Before: only section 6 present (7, 8 absent). A bare-numeric insert of 8 derives
# anchor=6 (greatest numeric < 8).
_POS_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLMb6" deletion-status=""><label>6</label><heading>Section six</heading>
      <prov.body><para><text>6 Section six The body of section 6.</text></para></prov.body></prov>
  </body>
</act>
"""

# Oracle (after): 7 and 8 both present; 8's immediately-preceding sibling is 7,
# NOT the derived anchor 6. The 8 content matches the candidate payload.
_POS_AFTER_XML = b"""\
<act>
  <body>
    <prov id="DLMb6" deletion-status=""><label>6</label><heading>Section six</heading>
      <prov.body><para><text>6 Section six The body of section 6.</text></para></prov.body></prov>
    <prov id="DLMb7" deletion-status=""><label>7</label><heading>Section seven</heading>
      <prov.body><para><text>7 Section seven The body of section 7.</text></para></prov.body></prov>
    <prov id="DLMb8" deletion-status=""><label>8</label><heading>New section eight</heading>
      <prov.body><para><text>8 New section eight The brand new body of section 8.</text></para></prov.body></prov>
  </body>
</act>
"""

_POS_AMENDING_XML = b"""\
<act>
  <body>
    <prov id="DLMins8pos"><label>3</label><heading>New section inserted</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following section is inserted:</text>
        <amend>
          <prov id="newDLMb8pos"><label>8</label><heading>New section eight</heading>
            <prov.body><para><text>8 New section eight The brand new body of section 8.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
  </body>
</act>
"""


def test_bare_numeric_insert_position_mismatch_is_residual_not_agreement() -> None:
    # Section 8 inserted; before-tree has only 6 -> derived anchor 6. The oracle
    # places 8 after 7 (oracle predecessor != derived anchor) -> position
    # residual. Content matches, so this is NOT a content mismatch and NOT an
    # agreement: a derived position guess never masquerades as confirmed.
    base = _archive(_POS_AFTER_XML, amending_xml=_POS_AMENDING_XML)
    base.rows["https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml"] = _POS_BEFORE_XML
    row = _FakeWitnessRow(
        target_path=(("section", "8"),),
        amended_provision="Section 8",
        amending_provision_hrefs=("DLMins8pos",),
    )
    report = build_dry_run_insert(base, work_id=_WORK_ID, surface=_FakeSurface((row,)))
    assert report.summary()["dry_run_oracle_agreements"] == 0
    assert len(report.proofs) == 1
    proof = report.proofs[0]
    assert proof.insert_anchor_source_path[-1] == "prov:6"  # the (wrong) derived anchor
    assert proof.oracle_match == "residual_insert_position_mismatch"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_INSERT_RESIDUAL_POSITION_MISMATCH_RULE_ID


def test_bare_numeric_insert_position_match_agrees() -> None:
    # Same shape but the oracle places 8 immediately after 6 (no intervening 7),
    # so the oracle predecessor == derived anchor -> genuine agreement.
    after_xml = b"""\
<act>
  <body>
    <prov id="DLMb6" deletion-status=""><label>6</label><heading>Section six</heading>
      <prov.body><para><text>6 Section six The body of section 6.</text></para></prov.body></prov>
    <prov id="DLMb8" deletion-status=""><label>8</label><heading>New section eight</heading>
      <prov.body><para><text>8 New section eight The brand new body of section 8.</text></para></prov.body></prov>
  </body>
</act>
"""
    base = _archive(after_xml, amending_xml=_POS_AMENDING_XML)
    base.rows["https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml"] = _POS_BEFORE_XML
    row = _FakeWitnessRow(
        target_path=(("section", "8"),),
        amended_provision="Section 8",
        amending_provision_hrefs=("DLMins8pos",),
    )
    report = build_dry_run_insert(base, work_id=_WORK_ID, surface=_FakeSurface((row,)))
    assert report.summary()["dry_run_oracle_agreements"] == 1
    proof = report.proofs[0]
    assert proof.insert_anchor_source_path[-1] == "prov:6"
    assert proof.oracle_match == "agrees"


def test_multi_letter_suffix_prefers_prior_in_sequence_predecessor() -> None:
    # 14AC's predecessor is 14AB (prior in the suffix sequence) when present,
    # not the stripped 14A. Verified at the derivation level.
    assert _derive_top_level_insert_anchor("14AC", ("14A", "14AB", "15")) == ("14AB", "after")
    # Falls back to the stripped label when the prior-in-sequence is absent.
    assert _derive_top_level_insert_anchor("14AC", ("14A", "15")) == ("14A", "after")
    # 14AA strips to 14A (no prior-in-sequence since the final letter is A).
    assert _derive_top_level_insert_anchor("14AA", ("14", "14A")) == ("14A", "after")


# =============================================================================
# Block-insert anchor positioning. A whole new Part / a run of sequential new
# sections is inserted by ONE work: every member derives the SAME single
# existing before-tree predecessor as its anchor, but in the oracle each member
# after the first is immediately preceded by ANOTHER new block member. A
# co-member predecessor is oracle-confirmed position (a contiguous block
# landing), NOT a position residual; an intervening sibling this work does NOT
# insert is still a genuine residual (the honesty boundary holds).
# =============================================================================

# Before: only section 6 present. A 2-member numeric block (7, 8) is inserted.
_BLOCK_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLMb6" deletion-status=""><label>6</label><heading>Section six</heading>
      <prov.body><para><text>6 Section six The body of section 6.</text></para></prov.body></prov>
  </body>
</act>
"""

# Oracle: 6, 7, 8 contiguous. Section 8's immediately-preceding sibling is 7 (a
# co-inserted block member), NOT the single derived before-tree anchor 6.
_BLOCK_AFTER_XML = b"""\
<act>
  <body>
    <prov id="DLMb6" deletion-status=""><label>6</label><heading>Section six</heading>
      <prov.body><para><text>6 Section six The body of section 6.</text></para></prov.body></prov>
    <prov id="DLMb7" deletion-status=""><label>7</label><heading>New section seven</heading>
      <prov.body><para><text>7 New section seven The brand new body of section 7.</text></para></prov.body></prov>
    <prov id="DLMb8" deletion-status=""><label>8</label><heading>New section eight</heading>
      <prov.body><para><text>8 New section eight The brand new body of section 8.</text></para></prov.body></prov>
  </body>
</act>
"""

# Amending act: separate amend subtrees inserting 7 and 8.
_BLOCK_AMENDING_XML = b"""\
<act>
  <body>
    <prov id="DLMins7"><label>3</label><heading>New section inserted</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following section is inserted:</text>
        <amend>
          <prov id="newDLMb7"><label>7</label><heading>New section seven</heading>
            <prov.body><para><text>7 New section seven The brand new body of section 7.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
    <prov id="DLMins8"><label>4</label><heading>New section inserted</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following section is inserted:</text>
        <amend>
          <prov id="newDLMb8"><label>8</label><heading>New section eight</heading>
            <prov.body><para><text>8 New section eight The brand new body of section 8.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
  </body>
</act>
"""


def test_insert_block_member_labels_groups_by_parent_and_kind() -> None:
    # Top-level prov siblings group under the empty parent; a nested subsection
    # group keys on its own (parent, kind). The set is identity-only (labels),
    # never payload.
    rows = (
        _FakeWitnessRow(row_id="r7", target_path=(("section", "7"),)),
        _FakeWitnessRow(row_id="r8", target_path=(("section", "8"),)),
        _FakeWitnessRow(
            row_id="r-nested",
            target_path=(("section", "20"), ("subsection", "2A")),
        ),
    )
    groups = _insert_block_member_labels(rows)
    assert groups[((), "prov")] == frozenset({"7", "8"})
    assert groups[(("prov:20",), "subprov")] == frozenset({"2A"})


def _run_block(rows: tuple[_FakeWitnessRow, ...], *, after_xml: bytes = _BLOCK_AFTER_XML):
    base = _archive(after_xml, amending_xml=_BLOCK_AMENDING_XML)
    base.rows["https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml"] = _BLOCK_BEFORE_XML
    return build_dry_run_insert(base, work_id=_WORK_ID, surface=_FakeSurface(rows))


def test_block_insert_co_member_predecessor_agrees() -> None:
    # Section 7 and 8 are a 2-member block inserted by this work; before-tree has
    # only 6. Both derive anchor=6, but in the oracle 8 is preceded by 7 (a
    # co-member). The co-member predecessor is oracle-confirmed position: BOTH
    # members agree (7 anchors on the real 6; 8 lands after co-member 7).
    row7 = _FakeWitnessRow(
        row_id="nz-opw-7",
        target_path=(("section", "7"),),
        amended_provision="Section 7",
        amending_provision_hrefs=("DLMins7",),
    )
    row8 = _FakeWitnessRow(
        row_id="nz-opw-8",
        target_path=(("section", "8"),),
        amended_provision="Section 8",
        amending_provision_hrefs=("DLMins8",),
    )
    report = _run_block((row7, row8))
    assert report.summary()["operations_refused"] == 0
    assert report.summary()["dry_run_oracle_agreements"] == 2
    by_label = {p.insert_new_node_source_path[-1]: p for p in report.proofs}
    assert by_label["prov:7"].oracle_match == "agrees"
    assert by_label["prov:7"].insert_anchor_source_path[-1] == "prov:6"
    # Section 8 derives the (single) before-tree anchor 6 but agrees because its
    # oracle predecessor 7 is a co-inserted block member.
    assert by_label["prov:8"].oracle_match == "agrees"
    assert by_label["prov:8"].insert_anchor_source_path[-1] == "prov:6"
    # The co-member labels the dry-run admitted for oracle-position must be
    # carried on the proof so actual-replay's slice re-confirm can apply the SAME
    # carveout the dry-run verified under (no proof-schema without it). The set
    # is identity-only — labels, never payload — and contains the block members
    # this work inserts that were absent from the before tree (7 and 8 here).
    expected_co_labels = frozenset({"7", "8"})
    assert by_label["prov:7"].insert_co_inserted_block_labels == expected_co_labels
    assert by_label["prov:8"].insert_co_inserted_block_labels == expected_co_labels
    assert "insert_co_inserted_block_labels" in by_label["prov:7"].to_jsonable()
    assert by_label["prov:7"].to_jsonable()["insert_co_inserted_block_labels"] == sorted(expected_co_labels)


def test_block_insert_non_co_member_intervening_sibling_stays_residual() -> None:
    # Honesty boundary: this work inserts ONLY section 8. Section 7 in the oracle
    # is NOT a co-inserted member (some other act added it), so 8's oracle
    # predecessor 7 cannot confirm the position -> genuine position residual,
    # never laundered into agreement by the block lane.
    row8 = _FakeWitnessRow(
        row_id="nz-opw-8",
        target_path=(("section", "8"),),
        amended_provision="Section 8",
        amending_provision_hrefs=("DLMins8",),
    )
    report = _run_block((row8,))
    assert report.summary()["dry_run_oracle_agreements"] == 0
    assert len(report.proofs) == 1
    proof = report.proofs[0]
    assert proof.insert_anchor_source_path[-1] == "prov:6"
    assert proof.oracle_match == "residual_insert_position_mismatch"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_INSERT_RESIDUAL_POSITION_MISMATCH_RULE_ID


# =============================================================================
# Nested insert (a new subsection/paragraph/definition WITHIN an existing
# provision). The inserted node's address has more than one segment; its anchor
# + position are derived among the leaf's siblings under the resolved parent.
# =============================================================================

# Principal act before: section 20 has subsections 1 + 2 (anchor for 2A) and a
# definitions subsection 20(3) with def-paras (alpha-ordered) and a list
# subsection 20(4) with paragraphs (a)+(b) (anchor for (c)). 2A/the new
# definition/(c) are all absent.
_NESTED_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLMa20" deletion-status=""><label>20</label><heading>Host section</heading>
      <prov.body>
        <subprov id="s20-1"><label>1</label><para><text>1 First subsection body.</text></para></subprov>
        <subprov id="s20-2"><label>2</label><para><text>2 Second subsection body.</text></para></subprov>
        <subprov id="s20-3"><label>3</label><para>
          <text>3 In this section,-</text>
          <def-para id="d-banana"><para><text><def-term>banana</def-term> means a yellow fruit.</text></para></def-para>
          <def-para id="d-date"><para><text><def-term>date</def-term> means a sweet fruit.</text></para></def-para>
        </para></subprov>
        <subprov id="s20-4"><label>4</label><para>
          <text>4 The list is as follows-</text>
          <label-para id="lp-a"><label>a</label><para><text>a first item.</text></para></label-para>
          <label-para id="lp-b"><label>b</label><para><text>b second item.</text></para></label-para>
        </para></subprov>
      </prov.body></prov>
  </body>
</act>
"""

# On-or-after: 2A (between 2 and 3), the new definition "cherry" (alpha between
# banana and date), and paragraph (c) all present with their inserted content;
# pre-existing siblings untouched.
_NESTED_AFTER_XML_AGREES = b"""\
<act>
  <body>
    <prov id="DLMa20" deletion-status=""><label>20</label><heading>Host section</heading>
      <prov.body>
        <subprov id="s20-1"><label>1</label><para><text>1 First subsection body.</text></para></subprov>
        <subprov id="s20-2"><label>2</label><para><text>2 Second subsection body.</text></para></subprov>
        <subprov id="s20-2A"><label>2A</label><para><text>2A The brand new nested subsection body.</text></para></subprov>
        <subprov id="s20-3"><label>3</label><para>
          <text>3 In this section,-</text>
          <def-para id="d-banana"><para><text><def-term>banana</def-term> means a yellow fruit.</text></para></def-para>
          <def-para id="d-cherry"><para><text><def-term>cherry</def-term> means a small red fruit.</text></para></def-para>
          <def-para id="d-date"><para><text><def-term>date</def-term> means a sweet fruit.</text></para></def-para>
        </para></subprov>
        <subprov id="s20-4"><label>4</label><para>
          <text>4 The list is as follows-</text>
          <label-para id="lp-a"><label>a</label><para><text>a first item.</text></para></label-para>
          <label-para id="lp-b"><label>b</label><para><text>b second item.</text></para></label-para>
          <label-para id="lp-c"><label>c</label><para><text>c third item.</text></para></label-para>
        </para></subprov>
      </prov.body></prov>
  </body>
</act>
"""

_NESTED_AMENDING_WORK_ID = _AMENDING_WORK_ID
_NESTED_HREF_SUBPROV = "DLMn-sub"
_NESTED_HREF_DEF = "DLMn-def"
_NESTED_HREF_PARA = "DLMn-para"

# Amending act: three inserting provisions. The new subsection 2A is a direct
# amend child; the new definition "cherry" is wrapped in an intermediate <para>
# (the descendant-search path); paragraph (c) is a direct amend child.
_NESTED_AMENDING_XML = b"""\
<act>
  <body>
    <prov id="DLMn-sub"><label>10</label><heading>Insert subsection</heading>
      <prov.body><subprov><label>1</label><para>
        <text>In section 20, after subsection (2), insert-</text>
        <amend>
          <subprov id="newSub2A"><label>2A</label><para><text>2A The brand new nested subsection body.</text></para></subprov>
        </amend>
      </para></subprov></prov.body></prov>
    <prov id="DLMn-def"><label>11</label><heading>Insert definition</heading>
      <prov.body><subprov><label>1</label><para>
        <text>In section 20(3), insert in their appropriate alphabetical order-</text>
        <amend>
          <para>
            <def-para id="newDefCherry"><para><text><def-term>cherry</def-term> means a small red fruit.</text></para></def-para>
          </para>
        </amend>
      </para></subprov></prov.body></prov>
    <prov id="DLMn-para"><label>12</label><heading>Insert paragraph</heading>
      <prov.body><subprov><label>1</label><para>
        <text>In section 20(4), after paragraph (b), insert-</text>
        <amend>
          <label-para id="newParaC"><label>c</label><para><text>c third item.</text></para></label-para>
        </amend>
      </para></subprov></prov.body></prov>
    <prov id="DLMn-gap"><label>13</label><heading>Insert subsection with missing predecessor</heading>
      <prov.body><subprov><label>1</label><para>
        <text>In section 20, insert subsection (9)-</text>
        <amend>
          <subprov id="newSub9"><label>9</label><para><text>9 A subsection whose predecessor (8) does not exist.</text></para></subprov>
        </amend>
      </para></subprov></prov.body></prov>
  </body>
</act>
"""
_NESTED_HREF_GAP = "DLMn-gap"


def _nested_archive(after_xml: bytes = _NESTED_AFTER_XML_AGREES) -> _FakeArchive:
    base = _archive(after_xml, amending_xml=_NESTED_AMENDING_XML)
    base.rows["https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml"] = _NESTED_BEFORE_XML
    return base


def _run_nested(rows: tuple[_FakeWitnessRow, ...], *, after_xml: bytes = _NESTED_AFTER_XML_AGREES):
    archive = _nested_archive(after_xml)
    return build_dry_run_insert(archive, work_id=_WORK_ID, surface=_FakeSurface(rows))


# --- Nested anchor derivation unit tests. ------------------------------------


def test_nested_anchor_subsection_suffix_letter_after_stem() -> None:
    assert _derive_nested_insert_anchor("subprov", "2A", ("1", "2", "3")) == ("2", "after")


def test_nested_anchor_subsection_bare_numeric_after_predecessor() -> None:
    assert _derive_nested_insert_anchor("subprov", "4", ("1", "2", "3")) == ("3", "after")


def test_nested_anchor_bare_numeric_without_predecessor_refused() -> None:
    # Inserting subsection 5 when only 1..3 exist would guess the position.
    assert _derive_nested_insert_anchor("subprov", "5", ("1", "2", "3")) is None


def test_nested_anchor_paragraph_alpha_after_predecessor() -> None:
    assert _derive_nested_insert_anchor("label-para", "c", ("a", "b")) == ("b", "after")


def test_nested_anchor_paragraph_first_letter_refused() -> None:
    # Inserting paragraph (a) has no predecessor to anchor against.
    assert _derive_nested_insert_anchor("label-para", "a", ("b", "c")) is None


def test_nested_anchor_definition_alpha_after_preceding_term() -> None:
    # "cherry" sorts after "banana" and before "date".
    assert _derive_nested_insert_anchor("def-para", "cherry", ("banana", "date")) == ("banana", "after")


def test_nested_anchor_definition_first_term_goes_before_first_sibling() -> None:
    # A new term that sorts before every sibling is inserted before the first.
    assert _derive_nested_insert_anchor("def-para", "apple", ("banana", "date")) == ("banana", "before")


def test_nested_anchor_definition_empty_sibling_group_refused() -> None:
    assert _derive_nested_insert_anchor("def-para", "cherry", ()) is None


# --- Nested kernel agreement tests. ------------------------------------------


def test_nested_subsection_insert_agrees() -> None:
    row = _FakeWitnessRow(
        target_path=(("section", "20"), ("subsection", "2A")),
        amended_provision="Section 20(2A)",
        amending_provision_hrefs=(_NESTED_HREF_SUBPROV,),
    )
    report = _run_nested((row,))
    assert len(report.proofs) == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_INSERT_AGREES_RULE_ID
    assert proof.insert_new_node_source_path[-1] == "subprov:2A"
    assert proof.insert_anchor_source_path[-1] == "subprov:2"
    assert proof.insert_direction == "after"
    assert proof.neighbors_unchanged is True
    assert proof.occupancy_before == "absent"


def test_nested_definition_insert_agrees_with_alpha_anchor() -> None:
    row = _FakeWitnessRow(
        target_path=(("section", "20"), ("subsection", "3"), ("definition", "cherry")),
        amended_provision="Section 20(3) definition cherry",
        amending_provision_hrefs=(_NESTED_HREF_DEF,),
    )
    report = _run_nested((row,))
    assert len(report.proofs) == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "agrees"
    assert proof.insert_new_node_source_path[-1] == "def-para:cherry"
    assert proof.insert_anchor_source_path[-1] == "def-para:banana"
    assert proof.insert_direction == "after"


def test_nested_paragraph_insert_agrees() -> None:
    row = _FakeWitnessRow(
        target_path=(("section", "20"), ("subsection", "4"), ("paragraph", "c")),
        amended_provision="Section 20(4)(c)",
        amending_provision_hrefs=(_NESTED_HREF_PARA,),
    )
    report = _run_nested((row,))
    assert len(report.proofs) == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "agrees"
    assert proof.insert_new_node_source_path[-1] == "label-para:c"
    assert proof.insert_anchor_source_path[-1] == "label-para:b"


def test_nested_insert_not_present_is_residual_not_agreement() -> None:
    # On-or-after equals the before tree: the nested 2A never appears.
    row = _FakeWitnessRow(
        target_path=(("section", "20"), ("subsection", "2A")),
        amended_provision="Section 20(2A)",
        amending_provision_hrefs=(_NESTED_HREF_SUBPROV,),
    )
    report = _run_nested((row,), after_xml=_NESTED_BEFORE_XML)
    assert len(report.proofs) == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "residual_insert_not_present"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_INSERT_RESIDUAL_NOT_PRESENT_RULE_ID


def test_nested_insert_refuses_when_parent_not_in_before() -> None:
    # Section 99 does not exist in the before tree -> the nested parent cannot
    # be resolved (typed refusal, never a guessed insert location).
    row = _FakeWitnessRow(
        target_path=(("section", "99"), ("subsection", "2A")),
        amended_provision="Section 99(2A)",
        amending_provision_hrefs=(_NESTED_HREF_SUBPROV,),
    )
    report = _run_nested((row,))
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_INSERT_PARENT_NOT_IN_BEFORE_RULE_ID


# --- Extractor: deep def-para + schedule-indirection. ------------------------


def test_extractor_finds_definition_wrapped_in_intermediate_para() -> None:
    node = _amending_node(_NESTED_AMENDING_XML, _NESTED_HREF_DEF)
    result = extract_structural_insertion(node, inserted_leaf_kind="def-para", inserted_leaf_label="cherry")
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.kind == "def-para"
    assert result.root.label == "cherry"
    assert "small red fruit" in result.root.text


def test_extractor_refuses_schedule_indirection_amending_provision() -> None:
    xml = b"""\
<act><body><prov id="SCHED"><label>3</label><heading>Schedule amendments</heading>
  <prov.body>
    <subprov><label>1</label><para>
      <text>Amend the Acts set out in the tables in Schedules 1 to 32 of this Act, in each case,-</text>
    </para></subprov>
    <subprov><label>3</label><para>
      <amend>
        <subprov id="own3"><label>3</label><para><text>3 An order under this section is [ standard text ].</text></para></subprov>
      </amend>
    </para></subprov>
  </prov.body></prov></body></act>
"""
    node = _amending_node(xml, "SCHED")
    result = extract_structural_insertion(node, inserted_leaf_kind="subprov", inserted_leaf_label="3")
    assert result == NZ_STRUCTURAL_INSERT_BLOCKED_SCHEDULE_INDIRECTION


# --- Schedule-indirection payload RESOLUTION (follow the indirection). --------
#
# When the base work is known, a schedule-indirection amendment is no longer
# refused: the payload is read from the ``<schedule.amendments.group2>`` block
# keyed to the base act, in either the bare-``<para>`` or the ``<legtable>`` row
# shape. The same leaf-matchers run as for an inline ``<amend>`` subtree.

# group2 form: each amended act is one group keyed by its heading citation; the
# operative section delegates ("...specified in Schedule 1 ... as set out in that
# schedule"). Two different base acts share the amending act; keying by (year,
# number) selects exactly one group.
_SCHEDULE_GROUP_XML = b"""\
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
        <para><text>After <citation jurisdiction="nz"><extref href="x">section 67C(1)(g)(iii)</extref></citation>, insert:</text>
          <amend><label-para><label>iv</label><para><text>iv harvested from a forest under the Forests Act.</text></para></label-para></amend>
        </para>
      </schedule.amendments.group2>
      <schedule.amendments.group2 id="G_B"><heading>Crimes Act 1961 (1961 No 43)</heading>
        <para><text>After <citation jurisdiction="nz"><extref href="y">section 9(2)(a)</extref></citation>, insert:</text>
          <amend><label-para><label>iv</label><para><text>iv a Crimes Act paragraph, not the Forests one.</text></para></label-para></amend>
        </para>
      </schedule.amendments.group2>
    </schedule.amendments>
  </schedule>
</act>
"""


def test_schedule_indirection_resolves_payload_from_group_for_base_work() -> None:
    node = _amending_node(_SCHEDULE_GROUP_XML, "OP")
    result = extract_structural_insertion(
        node,
        inserted_leaf_kind="label-para",
        inserted_leaf_label="iv",
        base_work_year="1949",
        base_work_number="19",
    )
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "iv"
    assert "harvested from a forest" in result.root.text
    # The colliding 'iv' in the Crimes Act group is NOT chosen: keying by the base
    # act's (year, number) scopes to the Forests Act group only.
    assert "Crimes Act paragraph" not in result.root.text


def test_schedule_indirection_keys_other_base_work_to_other_group() -> None:
    node = _amending_node(_SCHEDULE_GROUP_XML, "OP")
    result = extract_structural_insertion(
        node,
        inserted_leaf_kind="label-para",
        inserted_leaf_label="iv",
        base_work_year="1961",
        base_work_number="43",
    )
    assert isinstance(result, NZStructuralReplacement)
    assert "Crimes Act paragraph" in result.root.text


def test_schedule_indirection_without_base_work_still_refused() -> None:
    # No base-work identity -> the payload cannot be keyed to a group; the typed
    # schedule-indirection blocker stands (no guess).
    node = _amending_node(_SCHEDULE_GROUP_XML, "OP")
    result = extract_structural_insertion(node, inserted_leaf_kind="label-para", inserted_leaf_label="iv")
    assert result == NZ_STRUCTURAL_INSERT_BLOCKED_SCHEDULE_INDIRECTION


def test_schedule_indirection_no_group_for_base_work_is_typed_blocker() -> None:
    # The base act has no schedule amendment group in this amending act.
    node = _amending_node(_SCHEDULE_GROUP_XML, "OP")
    result = extract_structural_insertion(
        node,
        inserted_leaf_kind="label-para",
        inserted_leaf_label="iv",
        base_work_year="2000",
        base_work_number="1",
    )
    assert result == NZ_STRUCTURAL_BLOCKED_SCHEDULE_GROUP_UNRESOLVED


# legtable form: the payload sits in the amendment column of a 3-column table
# (Location | Amendment | Code), one row per target. A descendant scan reaches
# the row's ``<amend>`` the same way as the bare-para form.
_SCHEDULE_LEGTABLE_XML = b"""\
<act>
  <body>
    <prov id="OP"><label>3</label><heading>Schedule amendments</heading><prov.body>
      <subprov><label>1</label><para>
        <text>Amend the Acts set out in the tables in <citation jurisdiction="nz"><intref href="SCH2">Schedules 1 to 32</intref></citation> of this Act.</text>
      </para></subprov>
    </prov.body></prov>
  </body>
  <schedule id="SCH2"><label>2</label><heading>Department of Corrections</heading>
    <schedule.amendments>
      <schedule.amendments.group2 id="G_C"><heading>Corrections Act 2004 (2004 No 50)</heading>
        <para><legtable><table><tgroup cols="3"><tbody>
          <row>
            <entry><para><text>After section 31(3)</text></para></entry>
            <entry/>
            <entry><para><text>Insert:</text>
              <amend><subprov><label>4</label><para><text>4 Rules under subsection (1) are made by the chief executive.</text></para></subprov></amend>
            </para></entry>
          </row>
        </tbody></tgroup></table></legtable></para>
      </schedule.amendments.group2>
    </schedule.amendments>
  </schedule>
</act>
"""


def test_schedule_indirection_resolves_payload_from_legtable_row() -> None:
    node = _amending_node(_SCHEDULE_LEGTABLE_XML, "OP")
    result = extract_structural_insertion(
        node,
        inserted_leaf_kind="subprov",
        inserted_leaf_label="4",
        base_work_year="2004",
        base_work_number="50",
    )
    assert isinstance(result, NZStructuralReplacement)
    assert result.root.label == "4"
    assert "made by the chief executive" in result.root.text


def test_schedule_indirection_no_matching_leaf_in_group_is_typed_blocker() -> None:
    node = _amending_node(_SCHEDULE_LEGTABLE_XML, "OP")
    result = extract_structural_insertion(
        node,
        inserted_leaf_kind="subprov",
        inserted_leaf_label="99",
        base_work_year="2004",
        base_work_number="50",
    )
    assert result == NZ_STRUCTURAL_BLOCKED_SCHEDULE_NO_MATCHING_CHILD


# Placeholder form: the omnibus operative section substitutes a ``[standard
# text]`` token after the schedule payload is laid down; the raw payload would be
# a known-wrong node, so it is refused as typed residue.
_SCHEDULE_PLACEHOLDER_XML = b"""\
<act>
  <body>
    <prov id="OP"><label>3</label><heading>Schedule amendments</heading><prov.body>
      <subprov><label>1</label><para>
        <text>Amend the Acts set out in the tables in <citation jurisdiction="nz"><intref href="SCH3">Schedules 1 to 32</intref></citation> of this Act.</text>
      </para></subprov>
    </prov.body></prov>
  </body>
  <schedule id="SCH3"><label>2</label><heading>Department of Corrections</heading>
    <schedule.amendments>
      <schedule.amendments.group2 id="G_D"><heading>Corrections Act 2004 (2004 No 50)</heading>
        <para><text>After <citation jurisdiction="nz"><extref href="z">section 2(2)</extref></citation>, insert:</text>
          <amend><subprov><label>3</label><para><text>3 An order under this section is [<emphasis style="italic">standard text</emphasis>].</text></para></subprov></amend>
        </para>
      </schedule.amendments.group2>
    </schedule.amendments>
  </schedule>
</act>
"""


def test_schedule_indirection_refuses_unresolved_placeholder_payload() -> None:
    node = _amending_node(_SCHEDULE_PLACEHOLDER_XML, "OP")
    result = extract_structural_insertion(
        node,
        inserted_leaf_kind="subprov",
        inserted_leaf_label="3",
        base_work_year="2004",
        base_work_number="50",
    )
    assert result == NZ_STRUCTURAL_BLOCKED_SCHEDULE_UNRESOLVED_PLACEHOLDER


def test_family_specific_no_candidate_rule_ids_are_distinguishable_lanes() -> None:
    # AGENTS §1.10 distinguishability: a "no candidate in this family" diagnostic
    # must name ITS family's witness reader, not the repeal lane's, so the receipt
    # tells the right next-step (add an insert witness reader vs a repeal one) and
    # is distinguishable from a genuine repeal-lane miss. The three rule_ids are
    # distinct constants on the dry-run module surface.
    from lawvm.new_zealand.dry_run import (
        NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID,
        NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
        NZ_DRY_RUN_REFUSED_NO_REPLACE_CANDIDATE_RULE_ID,
    )

    assert NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID == "nz_dry_run_refused_no_replayable_insert_candidate"
    assert NZ_DRY_RUN_REFUSED_NO_REPLACE_CANDIDATE_RULE_ID == "nz_dry_run_refused_no_replayable_replace_candidate"
    assert NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID == "nz_dry_run_refused_no_replayable_repeal_candidate"
    assert len({NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID, NZ_DRY_RUN_REFUSED_NO_REPLACE_CANDIDATE_RULE_ID, NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID}) == 3
