from __future__ import annotations

import json

from lawvm.core.ir import LegalAddress, LegalOperation, TextPatchSpec, TextSelector
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS,
    NZ_DRY_RUN_NOT_IN_SCOPE_NON_TEXT_REPLACE_FAMILY,
    NZ_DRY_RUN_NOT_IN_SCOPE_TEXT_REPLACE_UNSUPPORTED_SELECTOR,
    NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID,
    NZ_DRY_RUN_REFUSED_TEXT_OLD_TEXT_OCCURRENCE_MISMATCH_RULE_ID,
    NZ_DRY_RUN_REFUSED_TEXT_SCOPE_NOT_SINGLE_OCCURRENCE_RULE_ID,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_NEW_TEXT_ABSENT_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID,
    NZDryRunRefusal,
    build_dry_run_repeal,
    scope_from_arg,
)
from lawvm.new_zealand.effect_candidates import (
    NZCanonicalEffectCandidateReport,
    NZCanonicalEffectCandidateRow,
    build_effect_candidate_preflight,
)


_WORK_ID = "act_public_2005_87"
_BEFORE_VERSION = "act_public_2005_87_en_2017-04-19"
_AFTER_VERSION = "act_public_2005_87_en_2019-10-24"

# Section 108(1): before carries the old phrase once; sibling 108(2) untouched.
_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360602s1"><label>1</label><para><text>An application must be in the prescribed form.</text></para></subprov>
        <subprov id="DLM360602s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after: 108(1) reflects the substitution (prescribed form -> approved
# form), 108(2) unchanged.
_AFTER_XML_AGREES = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360602s1"><label>1</label><para><text>An application must be in the approved form.</text></para></subprov>
        <subprov id="DLM360602s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where the new_text contains the old_text as a substring: the
# oracle legitimately still carries "prescribed form" once (inside the new
# phrase). This is the new-superset-of-old case that must still agree.
_AFTER_XML_SUPERSET_AGREES = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360602s1"><label>1</label><para><text>An application must be in the prescribed form approved by the chief executive.</text></para></subprov>
        <subprov id="DLM360602s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where the substitution was NOT reflected: 108(1) still says
# "prescribed form" and nothing else changed.
_AFTER_XML_OLD_REMAINS = _BEFORE_XML

# On-or-after where another window change overwrote 108(1) entirely: neither the
# old nor the new phrase is present.
_AFTER_XML_NEW_ABSENT = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360602s1"><label>1</label><para><text>An application is dealt with under regulations.</text></para></subprov>
        <subprov id="DLM360602s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
  </body>
</act>
"""


# Deletion fixtures: an omit-only deletion removes "in the prescribed form"
# from 108(1). The agreeing oracle no longer carries that span; the residual
# oracle still carries it (deletion not reflected).
_AFTER_XML_DELETION_AGREES = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360602s1"><label>1</label><para><text>An application must be.</text></para></subprov>
        <subprov id="DLM360602s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
  </body>
</act>
"""

# Each-place fixtures: 108(1) carries the old phrase TWICE; an each-place
# substitution must rewrite BOTH occurrences. Sibling 108(2) untouched.
_BEFORE_XML_EACH_PLACE = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360602s1"><label>1</label><para><text>A party may apply to the Principal Judge, and the Principal Judge may make an order.</text></para></subprov>
        <subprov id="DLM360602s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after: BOTH occurrences rewritten (Principal Judge -> Chief Judge).
_AFTER_XML_EACH_PLACE_AGREES = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360602s1"><label>1</label><para><text>A party may apply to the Chief Judge, and the Chief Judge may make an order.</text></para></subprov>
        <subprov id="DLM360602s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where only ONE of the two occurrences was rewritten: the oracle
# still carries the old phrase once that the each-place candidate removed. This
# is a residual (substitution not fully reflected), never a false agreement.
_AFTER_XML_EACH_PLACE_ONE_REMAINS = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360602s1"><label>1</label><para><text>A party may apply to the Chief Judge, and the Principal Judge may make an order.</text></para></subprov>
        <subprov id="DLM360602s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
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


def _version_detail(version_id: str, date: str) -> bytes:
    return json.dumps(
        {
            "version_id": version_id,
            "formats": [
                {
                    "type": "xml",
                    "url": f"https://www.legislation.govt.nz/act/public/2005/87/en/{date}.xml",
                }
            ],
        }
    ).encode()


def _archive(after_xml: bytes) -> _FakeArchive:
    return _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{_BEFORE_VERSION}/": _version_detail(
                _BEFORE_VERSION, "2017-04-19"
            ),
            "https://www.legislation.govt.nz/act/public/2005/87/en/2017-04-19.xml": _BEFORE_XML,
            f"https://api.legislation.govt.nz/v0/versions/{_AFTER_VERSION}/": _version_detail(
                _AFTER_VERSION, "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2005/87/en/2019-10-24.xml": after_xml,
        }
    )


def _archive_each_place(after_xml: bytes) -> _FakeArchive:
    return _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{_BEFORE_VERSION}/": _version_detail(
                _BEFORE_VERSION, "2017-04-19"
            ),
            "https://www.legislation.govt.nz/act/public/2005/87/en/2017-04-19.xml": _BEFORE_XML_EACH_PLACE,
            f"https://api.legislation.govt.nz/v0/versions/{_AFTER_VERSION}/": _version_detail(
                _AFTER_VERSION, "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2005/87/en/2019-10-24.xml": after_xml,
        }
    )


def _text_replace_operation(
    *,
    old_text: str = "the prescribed form",
    new_text: str = "the approved form",
    occurrence: int = 1,
) -> LegalOperation:
    return LegalOperation(
        op_id=f"nz:{_WORK_ID}:nz-opw-1:text_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "108"), ("subsection", "1"))),
        payload=None,
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text=old_text, occurrence=occurrence),
            replacement=new_text,
        ),
        source=OperationSource(statute_id="act_public_2019_5", effective="2019-10-24"),
        provenance_tags=("new_zealand", "history_note", "candidate_only", "not_replayed"),
        witness_rule_id="nz_text_replace_candidate_from_direct_instruction_workqueue",
    )


def _text_replace_row(
    *,
    operation: LegalOperation | None = None,
    amendment_date_iso: str = "2019-10-24",
    target_resolution_status: str = "exact_source_path",
    old_text: str = "the prescribed form",
    new_text: str = "the approved form",
    occurrence: int = 1,
) -> NZCanonicalEffectCandidateRow:
    return NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-1",
        operation_row_id="nz-opw-1",
        effect_readiness_row_id="nz-readiness-1",
        candidate_status="candidate_emitted",
        action=str(StructuralAction.TEXT_REPLACE),
        target_address="section:108/subsection:1",
        operation=operation
        if operation is not None
        else _text_replace_operation(old_text=old_text, new_text=new_text, occurrence=occurrence),
        amendment_date_iso=amendment_date_iso,
        operation_family="amended",
        old_text=old_text,
        new_text=new_text,
        latest_oracle_target_resolution_status=target_resolution_status,
    )


def _blocked_text_replace_row() -> NZCanonicalEffectCandidateRow:
    return NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-2",
        operation_row_id="nz-opw-2",
        effect_readiness_row_id="nz-readiness-2",
        candidate_status="blocked",
        target_address="section:200",
        blocking_rule_id="nz_text_replace_candidate_latest_oracle_witness_unavailable",
        operation_family="amended",
    )


def _preflight_from_rows(rows: tuple[NZCanonicalEffectCandidateRow, ...]):
    report = NZCanonicalEffectCandidateReport(work_id=_WORK_ID, rows=rows)
    return build_effect_candidate_preflight(report)


def _run(after_xml: bytes, rows: tuple[NZCanonicalEffectCandidateRow, ...]):
    preflight = _preflight_from_rows(rows)
    archive = _archive(after_xml)
    return build_dry_run_repeal(
        archive,
        work_id=_WORK_ID,
        preflight=preflight,
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    )


def _run_each_place(after_xml: bytes, rows: tuple[NZCanonicalEffectCandidateRow, ...]):
    preflight = _preflight_from_rows(rows)
    archive = _archive_each_place(after_xml)
    return build_dry_run_repeal(
        archive,
        work_id=_WORK_ID,
        preflight=preflight,
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    )


def test_scope_from_arg_accepts_text_replace() -> None:
    assert scope_from_arg("selected-family-text-replace") == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE


def test_text_replace_applies_substitution_and_agrees_with_oracle() -> None:
    report = _run(_AFTER_XML_AGREES, (_text_replace_row(),))

    summary = report.summary()
    assert summary["operation_family"] == "text_replace"
    assert summary["operations_dry_run"] == 1
    assert summary["operations_refused"] == 0
    assert summary["dry_run_oracle_agreements"] == 1
    assert summary["dry_run_oracle_residuals"] == 0
    assert summary["neighbors_unchanged_all"] is True
    assert summary["replay_claims"] is False
    assert summary["dry_run_claims"] is True

    proof = report.proofs[0]
    assert proof.action == str(StructuralAction.TEXT_REPLACE)
    assert proof.selected_source_path == ("prov:108", "subprov:1")
    # Boring kernel: node stays substantive and addressable; only text changed.
    assert proof.occupancy_before == "substantive"
    assert proof.occupancy_after == "substantive"
    assert proof.target_digest_before != proof.target_digest_after
    # Mutation boundary: old occurs once before, zero after; sibling untouched.
    assert proof.text_old_occurrences_before == 1
    assert proof.text_old_occurrences_after == 0
    assert proof.text_each_place is False
    assert proof.neighbors_unchanged is True
    # Oracle reflects the substitution.
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID
    assert proof.text_oracle_contains_new_text is True
    assert proof.oracle_version_id == _AFTER_VERSION


def test_text_replace_agrees_when_new_text_contains_old_text() -> None:
    # The new phrase contains the old phrase as a substring, so the oracle node
    # legitimately still carries the old phrase once. The after-node-vs-oracle
    # old-count parity keeps this an agreement, not a false residual.
    report = _run(_AFTER_XML_SUPERSET_AGREES, (_text_replace_row(new_text="the prescribed form approved by the chief executive"),))

    proof = report.proofs[0]
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID
    # After-node still carries old phrase once (inside new phrase); oracle too.
    assert proof.text_old_occurrences_after == 1
    assert proof.text_oracle_old_occurrences == 1
    assert proof.text_oracle_contains_new_text is True


def test_text_replace_residual_when_old_text_remains_in_oracle() -> None:
    # Oracle still carries the old phrase the substitution removed: NOT reflected.
    report = _run(_AFTER_XML_OLD_REMAINS, (_text_replace_row(),))

    summary = report.summary()
    assert summary["dry_run_oracle_agreements"] == 0
    assert summary["dry_run_oracle_residuals"] == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "residual_old_text_remains"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID
    assert proof.text_old_occurrences_after == 0
    assert proof.text_oracle_old_occurrences == 1


def test_text_replace_residual_when_new_text_absent_in_oracle() -> None:
    # Another window change overwrote the node; new phrase not present.
    report = _run(_AFTER_XML_NEW_ABSENT, (_text_replace_row(),))

    summary = report.summary()
    assert summary["dry_run_oracle_residuals"] == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "residual_new_text_absent"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_TEXT_RESIDUAL_NEW_TEXT_ABSENT_RULE_ID
    assert proof.text_oracle_contains_new_text is False


def test_text_replace_omit_only_deletion_applies_and_agrees() -> None:
    # An omit-only deletion (new_text empty) removes the span. The oracle no
    # longer carries it, so the deletion agrees.
    report = _run(
        _AFTER_XML_DELETION_AGREES,
        (_text_replace_row(old_text="in the prescribed form", new_text=""),),
    )

    summary = report.summary()
    assert summary["operations_dry_run"] == 1
    assert summary["operations_refused"] == 0
    assert summary["dry_run_oracle_agreements"] == 1
    assert summary["dry_run_oracle_residuals"] == 0

    proof = report.proofs[0]
    assert proof.action == str(StructuralAction.TEXT_REPLACE)
    assert proof.text_old_occurrences_before == 1
    assert proof.text_old_occurrences_after == 0
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID


def test_text_replace_omit_only_deletion_residual_when_span_remains() -> None:
    # The oracle still carries the omitted span: the deletion is NOT reflected.
    # ``_BEFORE_XML`` (where 108(1) still says "in the prescribed form") stands in
    # for an unchanged on-or-after oracle.
    report = _run(
        _BEFORE_XML,
        (_text_replace_row(old_text="in the prescribed form", new_text=""),),
    )

    summary = report.summary()
    assert summary["dry_run_oracle_agreements"] == 0
    assert summary["dry_run_oracle_residuals"] == 1
    proof = report.proofs[0]
    assert proof.oracle_match == "residual_old_text_remains"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID


def test_text_replace_each_place_applies_at_every_occurrence_and_agrees() -> None:
    # Each-place (occurrence 0): the before target carries the old phrase twice;
    # the kernel rewrites BOTH and the oracle reflects both. The mutation
    # boundary still holds — siblings/parent untouched.
    report = _run_each_place(
        _AFTER_XML_EACH_PLACE_AGREES,
        (_text_replace_row(occurrence=0, old_text="the Principal Judge", new_text="the Chief Judge"),),
    )

    summary = report.summary()
    assert summary["operations_dry_run"] == 1
    assert summary["operations_refused"] == 0
    assert summary["dry_run_oracle_agreements"] == 1
    assert summary["dry_run_oracle_residuals"] == 0
    assert summary["neighbors_unchanged_all"] is True

    proof = report.proofs[0]
    assert proof.text_each_place is True
    # Both occurrences existed before and were removed; oracle carries zero.
    assert proof.text_old_occurrences_before == 2
    assert proof.text_old_occurrences_after == 0
    assert proof.text_oracle_old_occurrences == 0
    assert proof.text_oracle_contains_new_text is True
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID
    assert proof.neighbors_unchanged is True


def test_text_replace_each_place_residual_when_one_occurrence_remains() -> None:
    # The oracle rewrote only ONE of the two occurrences: the each-place
    # candidate removed both, so the oracle's residual old-count exceeds the
    # candidate after-count. This is a typed residual, never a false agreement.
    report = _run_each_place(
        _AFTER_XML_EACH_PLACE_ONE_REMAINS,
        (_text_replace_row(occurrence=0, old_text="the Principal Judge", new_text="the Chief Judge"),),
    )

    summary = report.summary()
    assert summary["dry_run_oracle_agreements"] == 0
    assert summary["dry_run_oracle_residuals"] == 1
    proof = report.proofs[0]
    assert proof.text_each_place is True
    assert proof.text_old_occurrences_before == 2
    assert proof.text_old_occurrences_after == 0
    assert proof.text_oracle_old_occurrences == 1
    assert proof.oracle_match == "residual_old_text_remains"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID


def test_text_replace_each_place_refuses_when_old_text_absent() -> None:
    # Each-place still never guesses: a zero-occurrence old_text is a typed
    # refusal, not a silent no-op.
    report = _run_each_place(
        _AFTER_XML_EACH_PLACE_AGREES,
        (_text_replace_row(occurrence=0, old_text="a phrase not present", new_text="x"),),
    )

    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_TEXT_OLD_TEXT_OCCURRENCE_MISMATCH_RULE_ID


def test_text_replace_kernel_refuses_unsupported_selector_when_forced() -> None:
    # Defence in depth: a selector that is neither single-occurrence (1) nor
    # each-place (0) — here a specific occurrence 2 — is a typed refusal, never a
    # guessed edit.
    from lawvm.new_zealand.dry_run import _dry_run_one_text_replace

    operation = _text_replace_operation(occurrence=2)
    row = _text_replace_row(operation=operation, occurrence=2)
    refusal = _dry_run_one_text_replace(_archive(_AFTER_XML_AGREES), _WORK_ID, row, operation, {})
    assert isinstance(refusal, NZDryRunRefusal)
    assert refusal.rule_id == NZ_DRY_RUN_REFUSED_TEXT_SCOPE_NOT_SINGLE_OCCURRENCE_RULE_ID


def test_text_replace_unsupported_selector_typed_not_in_scope() -> None:
    # An occurrence-2 selector is not selected as in-scope (typed not-in-scope),
    # so the work refuses with no proof and the census names the reason.
    report = _run(_AFTER_XML_AGREES, (_text_replace_row(occurrence=2),))

    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID
    completeness = report.scope_completeness
    assert completeness is not None
    assert (
        completeness.repeal_witnesses_not_in_scope_reason_counts.get(
            NZ_DRY_RUN_NOT_IN_SCOPE_TEXT_REPLACE_UNSUPPORTED_SELECTOR
        )
        == 1
    )


def test_text_replace_refuses_when_old_text_not_single_occurrence() -> None:
    # old_text does not occur in the before target node at all.
    report = _run(_AFTER_XML_AGREES, (_text_replace_row(old_text="a phrase not present"),))

    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_TEXT_OLD_TEXT_OCCURRENCE_MISMATCH_RULE_ID


def test_text_replace_refuses_recovered_target() -> None:
    report = _run(
        _AFTER_XML_AGREES,
        (_text_replace_row(target_resolution_status="via_unlabeled_source_carrier"),),
    )

    # A recovered target is not even selected as in-scope, so the work refuses
    # with the no-candidate refusal and carries it as typed not-in-scope.
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID


def test_text_replace_kernel_refuses_recovered_target_when_forced() -> None:
    from lawvm.new_zealand.dry_run import _dry_run_one_text_replace

    operation = _text_replace_operation()
    row = _text_replace_row(operation=operation, target_resolution_status="via_unlabeled_source_carrier")
    refusal = _dry_run_one_text_replace(_archive(_AFTER_XML_AGREES), _WORK_ID, row, operation, {})
    assert isinstance(refusal, NZDryRunRefusal)
    assert refusal.rule_id == NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID


def test_text_replace_scope_completeness_types_other_witnesses() -> None:
    # One eligible text_replace + one blocked text_replace witness + one emitted
    # repeal candidate: the census types each honestly and never inflates
    # coverage.
    repeal = NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-3",
        operation_row_id="nz-opw-3",
        effect_readiness_row_id="nz-readiness-3",
        candidate_status="candidate_emitted",
        action=str(StructuralAction.REPEAL),
        target_address="section:300",
        operation=LegalOperation(
            op_id="nz:repeal:3",
            sequence=3,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "300"),)),
        ),
        operation_family="repealed",
        latest_oracle_target_resolution_status="exact_source_path",
    )
    report = _run(_AFTER_XML_AGREES, (_text_replace_row(), _blocked_text_replace_row(), repeal))

    completeness = report.scope_completeness
    assert completeness is not None
    assert completeness.family == "text_replace"
    assert completeness.is_partial is True
    # Two text_replace witnesses (one in scope, one blocked); the repeal is not
    # a text_replace witness so it is excluded from the family denominator.
    assert completeness.total_repeal_operation_witnesses == 2
    assert completeness.repeal_witnesses_in_scope == 1
    reasons = completeness.repeal_witnesses_not_in_scope_reason_counts
    assert reasons.get(NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS) == 1
    # The emitted repeal candidate is carried as a non-text-replace not-in-scope
    # reason in the work-wide census.
    all_reasons = completeness.not_in_scope_reason_counts
    assert all_reasons.get(NZ_DRY_RUN_NOT_IN_SCOPE_NON_TEXT_REPLACE_FAMILY) == 1


def test_text_replace_agreement_surface_is_text_replace_named() -> None:
    report = _run(_AFTER_XML_AGREES, (_text_replace_row(),))
    surface = report.agreement_surface()
    assert surface["agreement_surface"] == "nz_dry_run_text_replace"
    assert surface["comparison_kind"] == "dry_run_after_tree_vs_archived_on_or_after_xml"
    assert surface["materialization_kind"] == "proposed_future_branch"
    payload = report.to_jsonable()
    assert payload["replay_claims"] is False
    assert payload["dry_run_claims"] is True
