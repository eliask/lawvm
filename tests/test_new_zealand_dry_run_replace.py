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
    NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_REFUSED_REPLACE_NO_AMENDING_WORK_RULE_ID,
    NZ_DRY_RUN_REFUSED_REPLACE_PAYLOAD_NOT_EXTRACTABLE_RULE_ID,
    NZ_DRY_RUN_REPLACE_AGREES_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_MISMATCH_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    build_dry_run_replace,
    scope_from_arg,
)
from lawvm.new_zealand.source_tree import (
    NZ_STRUCTURAL_REPLACE_BLOCKED_AMBIGUOUS_MATCH,
    NZ_STRUCTURAL_REPLACE_BLOCKED_MULTI_CHILD_EXPANSION,
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

# Amending act with a one-to-many expansion (target subprov 2 -> several new
# subprovs) so the extractor blocks it as a multi-child expansion.
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


def test_extractor_blocks_one_to_many_expansion() -> None:
    node = _amending_node(_AMENDING_XML_MULTI, "DLM9000020")
    result = extract_structural_replacement(node, target_leaf_kind="subprov", target_leaf_label="2")
    assert result == NZ_STRUCTURAL_REPLACE_BLOCKED_MULTI_CHILD_EXPANSION


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
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID
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
