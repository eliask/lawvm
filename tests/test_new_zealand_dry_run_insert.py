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
    NZ_DRY_RUN_NOT_IN_SCOPE_INSERT_TARGET_NOT_CANDIDATE,
    NZ_DRY_RUN_NOT_IN_SCOPE_NON_INSERT_FAMILY,
    NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_DERIVABLE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_IN_BEFORE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_NESTED_ANCHOR_NOT_DERIVABLE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_NO_AMENDING_WORK_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_PARENT_NOT_IN_BEFORE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_PAYLOAD_NOT_EXTRACTABLE_RULE_ID,
    NZ_DRY_RUN_REFUSED_INSERT_TARGET_ALREADY_IN_BEFORE_RULE_ID,
    NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
    _derive_insert_anchor,
    _derive_nested_insert_anchor,
    build_dry_run_insert,
    scope_from_arg,
)
from lawvm.new_zealand.source_tree import (
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
    def __init__(self, status: str, address: str, path: tuple[tuple[str, str], ...]) -> None:
        self.status = status
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
    # A bare numeric inserted label (no suffix letter) has no derivable anchor.
    row = _FakeWitnessRow(target_path=(("section", "38"),), amended_provision="Section 38")
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
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID
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
