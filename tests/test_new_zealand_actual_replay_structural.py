"""Actual (canonical) replay of the STRUCTURAL families (replace + insert).

These promote dry-run-VERIFIED structural whole-provision REPLACE and whole/
nested INSERT operations into actual materialized transitions. The discipline is
identical to the leaf-local families: only ops whose dry-run proof agreed with
the on-or-after oracle (and left neighbours unchanged) are materialized; the
materialized after-tree is checked against the archived oracle with the same
family-specific agreement notion the dry-run used; any non-verified op in a
change window fails the WHOLE transition closed with a distinct named diagnostic.

The structural families are operation-surface driven, so these tests build the
dry-run from a fake operation surface (mirroring the dry-run kernel tests) and
pass it straight into ``build_actual_replay``.
"""

from __future__ import annotations

import json

from lawvm.new_zealand.actual_replay import (
    NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
    build_actual_replay,
)
from lawvm.new_zealand.effect_candidates import (
    NZCanonicalEffectCandidateReport,
    build_effect_candidate_preflight,
)

_WORK_ID = "act_public_2005_99"
_AMENDING_WORK_ID = "act_public_2019_5"
_BEFORE_VERSION = "act_public_2005_99_en_2018-01-01"
_AFTER_VERSION = "act_public_2005_99_en_2019-10-24"
_AMENDING_VERSION = "act_public_2019_5_en_2019-10-24"
_REPLACE_HREF = "DLM9000010"
_INSERT_HREF = "DLM9000020"
_AMENDMENT_DATE = "2019-10-24"


# Principal act before the amendment: section 41 (replace target, OLD body),
# section 18 (insert anchor) present, 18A absent, section 42 untouched neighbour.
_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLMa18" deletion-status=""><label>18</label><heading>Anchor section</heading>
      <prov.body><para><text>18 Anchor section The body of section 18.</text></para></prov.body></prov>
    <prov id="DLMa41" deletion-status=""><label>41</label><heading>Old heading</heading>
      <prov.body><para><text>41 Old heading The old body of section 41.</text></para></prov.body></prov>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# On-or-after where section 41 reflects the structural replacement AND new section
# 18A is present between 18 and 41 with its brand new body; 18 / 42 untouched.
_AFTER_XML_AGREES = b"""\
<act>
  <body>
    <prov id="DLMa18" deletion-status=""><label>18</label><heading>Anchor section</heading>
      <prov.body><para><text>18 Anchor section The body of section 18.</text></para></prov.body></prov>
    <prov id="DLMa18A" deletion-status=""><label>18A</label><heading>New inserted section</heading>
      <prov.body><para><text>18A New inserted section The brand new body of section 18A.</text></para></prov.body></prov>
    <prov id="DLMa41" deletion-status=""><label>41</label><heading>New heading</heading>
      <prov.body><para><text>41 New heading The brand new body of section 41.</text></para></prov.body></prov>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# Divergent oracle where the REPLACE did NOT happen (section 41 still carries
# different content): the replace proof cannot agree, so a transition containing
# it fails closed.
_AFTER_XML_REPLACE_DIVERGES = b"""\
<act>
  <body>
    <prov id="DLMa18" deletion-status=""><label>18</label><heading>Anchor section</heading>
      <prov.body><para><text>18 Anchor section The body of section 18.</text></para></prov.body></prov>
    <prov id="DLMa18A" deletion-status=""><label>18A</label><heading>New inserted section</heading>
      <prov.body><para><text>18A New inserted section The brand new body of section 18A.</text></para></prov.body></prov>
    <prov id="DLMa41" deletion-status=""><label>41</label><heading>Untouched heading</heading>
      <prov.body><para><text>41 Untouched heading Section 41 was never replaced in this oracle.</text></para></prov.body></prov>
    <prov id="DLMa42" deletion-status=""><label>42</label><heading>Neighbour</heading>
      <prov.body><para><text>42 Neighbour Neighbour body.</text></para></prov.body></prov>
  </body>
</act>
"""

# Amending act: provision _REPLACE_HREF replaces section 41; provision
# _INSERT_HREF inserts section 18A after section 18.
_AMENDING_XML = b"""\
<act>
  <body>
    <prov id="DLM9000010"><label>10</label><heading>Replacement</heading>
      <prov.body><subprov><label>1</label><para>
        <text><citation jurisdiction="nz"><extref href="DLMa41">section 41</extref></citation> is repealed and the following section substituted:</text>
        <amend>
          <prov id="newDLMa41"><label>41</label><heading>New heading</heading>
            <prov.body><para><text>41 New heading The brand new body of section 41.</text></para></prov.body></prov>
        </amend>
      </para></subprov></prov.body></prov>
    <prov id="DLM9000020"><label>20</label><heading>Insertion</heading>
      <prov.body><subprov><label>1</label><para>
        <text>The following section is inserted after <citation jurisdiction="nz"><extref href="DLMa18">section 18</extref></citation>:</text>
        <amend>
          <prov id="newDLMa18A"><label>18A</label><heading>New inserted section</heading>
            <prov.body><para><text>18A New inserted section The brand new body of section 18A.</text></para></prov.body></prov>
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


def _version_detail(version_id: str, number: str, date: str) -> bytes:
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


def _archive(after_xml: bytes = _AFTER_XML_AGREES) -> _FakeArchive:
    return _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{_BEFORE_VERSION}/": _version_detail(
                _BEFORE_VERSION, "99", "2018-01-01"
            ),
            "https://www.legislation.govt.nz/act/public/2018/99/en/2018-01-01.xml": _BEFORE_XML,
            f"https://api.legislation.govt.nz/v0/versions/{_AFTER_VERSION}/": _version_detail(
                _AFTER_VERSION, "99", "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2019/99/en/2019-10-24.xml": after_xml,
            f"https://api.legislation.govt.nz/v0/works/{_AMENDING_WORK_ID}/versions/": json.dumps(
                {"versions": [{"version_id": _AMENDING_VERSION, "date.as.at": "2019-10-24"}]}
            ).encode(),
            f"https://api.legislation.govt.nz/v0/versions/{_AMENDING_VERSION}/": _version_detail(
                _AMENDING_VERSION, "5", "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2019/5/en/2019-10-24.xml": _AMENDING_XML,
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
        row_id: str,
        operation_family: str,
        target_path: tuple[tuple[str, str], ...],
        amending_provision_hrefs: tuple[str, ...],
        target_status: str = "candidate",
        amendment_date_iso: str = _AMENDMENT_DATE,
        amending_work_id: str = _AMENDING_WORK_ID,
    ) -> None:
        self.row_id = row_id
        self.operation_family = operation_family
        self.amended_provision = _addr_for_path(target_path)
        self.amendment_date_iso = amendment_date_iso
        self.amending_work_id = amending_work_id
        self.amending_provision_hrefs = amending_provision_hrefs
        self.target_address_candidate = _FakeTargetCandidate(
            target_status, _addr_for_path(target_path), target_path
        )


class _FakeSurface:
    def __init__(self, rows: tuple[_FakeWitnessRow, ...]) -> None:
        self.rows = rows


def _replace_row() -> _FakeWitnessRow:
    return _FakeWitnessRow(
        row_id="nz-opw-1",
        operation_family="substituted",
        target_path=(("section", "41"),),
        amending_provision_hrefs=(_REPLACE_HREF,),
    )


def _insert_row() -> _FakeWitnessRow:
    return _FakeWitnessRow(
        row_id="nz-opw-2",
        operation_family="inserted",
        target_path=(("section", "18A"),),
        amending_provision_hrefs=(_INSERT_HREF,),
    )


def _empty_preflight():
    # The structural families do not consume the preflight; an empty one is fine.
    report = NZCanonicalEffectCandidateReport(work_id=_WORK_ID, rows=())
    return build_effect_candidate_preflight(report)


def _run(after_xml: bytes, rows: tuple[_FakeWitnessRow, ...], families: tuple[str, ...]):
    return build_actual_replay(
        _archive(after_xml),
        work_id=_WORK_ID,
        preflight=_empty_preflight(),
        families=families,
        surface=_FakeSurface(rows),
    )


# --- 1. Whole-provision REPLACE materializes and the slice agrees -------------


def test_actual_replay_materializes_verified_replace_transition() -> None:
    report = _run(_AFTER_XML_AGREES, (_replace_row(),), ("replace",))
    summary = report.summary()

    assert summary["transitions_replayed"] == 1
    assert summary["ops_replayed"] == 1
    assert summary["target_slice_agreements"] == 1
    assert summary["all_slices_agree"] is True
    assert summary["replay_claims"] is True
    assert summary["dry_run_claims"] is False

    transition = report.transitions[0]
    assert transition.amendment_date_iso == _AMENDMENT_DATE
    assert transition.before_version_id == _BEFORE_VERSION
    assert transition.oracle_version_id == _AFTER_VERSION
    assert transition.target_slice_agrees is True
    # The materialized after-tree (the actual replay OUTPUT) carries the NEW body.
    after_by_path = {node.path: node for node in transition.materialized_after.nodes}
    s41 = after_by_path[("prov:41",)]
    assert "brand new body of section 41" in s41.text
    assert "old body" not in s41.text
    # Neighbour untouched.
    assert "Neighbour body." in after_by_path[("prov:42",)].text


# --- 2. Whole-provision INSERT materializes and the slice agrees --------------


def test_actual_replay_materializes_verified_insert_transition() -> None:
    report = _run(_AFTER_XML_AGREES, (_insert_row(),), ("insert",))
    summary = report.summary()

    assert summary["transitions_replayed"] == 1
    assert summary["ops_replayed"] == 1
    assert summary["target_slice_agreements"] == 1
    assert summary["all_slices_agree"] is True
    assert summary["replay_claims"] is True

    transition = report.transitions[0]
    after_by_path = {node.path: node for node in transition.materialized_after.nodes}
    # The new node 18A is present in the materialized after-tree with its body.
    assert ("prov:18A",) in after_by_path
    assert "brand new body of section 18A" in after_by_path[("prov:18A",)].text
    # The anchor (section 18) and the rest are still present (insert is additive).
    assert ("prov:18",) in after_by_path
    assert ("prov:41",) in after_by_path
    # The new node lands AFTER the anchor and BEFORE the next existing section.
    paths = [node.path for node in transition.materialized_after.nodes]
    assert paths.index(("prov:18A",)) == paths.index(("prov:18",)) + 1


# --- 3. Composite replace + insert in one transition -------------------------


def test_actual_replay_materializes_composite_replace_and_insert() -> None:
    report = _run(_AFTER_XML_AGREES, (_replace_row(), _insert_row()), ("replace", "insert"))
    summary = report.summary()

    # Both structural ops live in the same change window -> one transition, two ops.
    assert summary["transitions_replayed"] == 1
    assert summary["ops_replayed"] == 2
    assert summary["target_slice_agreements"] == 2
    assert summary["all_slices_agree"] is True

    transition = report.transitions[0]
    families = sorted(mutation.family for mutation in transition.mutations)
    assert families == ["insert", "replace"]
    after_by_path = {node.path: node for node in transition.materialized_after.nodes}
    assert "brand new body of section 41" in after_by_path[("prov:41",)].text
    assert "brand new body of section 18A" in after_by_path[("prov:18A",)].text


# --- 4. Agreement-surface labelling -----------------------------------------


def test_actual_replay_structural_surface_is_labeled_legal_text_state() -> None:
    report = _run(_AFTER_XML_AGREES, (_replace_row(), _insert_row()), ("replace", "insert"))
    surface = report.agreement_surface()
    assert surface["agreement_surface"] == "nz_actual_replay"
    assert surface["materialization_kind"] == "legal_text_state"
    assert surface["comparison_materialization_kind"] == "official_consolidation_view"
    assert all(residual["owner_phase"] == "actual_replay" for residual in surface["residuals"])


# --- 5. FAIL CLOSED: a non-agreeing structural op blocks the whole transition -


def test_actual_replay_fails_closed_when_replace_does_not_verify() -> None:
    # The oracle does NOT reflect the replace, so its dry-run proof cannot agree.
    # The whole transition is blocked and NOTHING materializes — even though the
    # insert op in the same window would verify on its own.
    report = _run(
        _AFTER_XML_REPLACE_DIVERGES, (_replace_row(), _insert_row()), ("replace", "insert")
    )
    summary = report.summary()

    assert summary["transitions_replayed"] == 0
    assert summary["ops_replayed"] == 0
    assert summary["transitions_refused"] >= 1
    assert summary["replay_claims"] is False

    refusal_rules = {refusal.rule_id for refusal in report.refusals}
    # The same-window verified insert op is reported as part of the blocked
    # transition, never partially materialized.
    assert NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID in refusal_rules


def test_actual_replay_residual_refusal_propagates_dry_run_divergence_class() -> None:
    # AGENTS §0 evidence propagation: when the dry-run proof for a refused op carried
    # a target-level divergence classification (``divergence_class``), the actual-replay
    # residual refusal MUST carry that classification forward onto its detail receipt —
    # the promotion plane may not silently lose the source-truth-bucket signal (the §0
    # deterministic-gap / manual-compilation-frontier / oracle-suspect tag) that the
    # dry-run plane computed. Strict-superset additive: no rule_id change, no
    # fail-closed behaviour change.
    report = _run(
        _AFTER_XML_REPLACE_DIVERGES, (_replace_row(),), ("replace",)
    )
    # The synthetic divergent oracle: section 41 is NOT replaced (oracle still carries
    # OLD heading + OLD body). The replace op fails dry-run under residual_replacement_mismatch.
    residual_refusals = [
        ref for ref in report.refusals
        if ref.rule_id == NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID
    ]
    assert residual_refusals, "expected at least one dry-run-residual refusal from the divergent replace"
    refusal = residual_refusals[0]
    detail = refusal.detail or {}
    # The residual refusal must carry the dry-run's divergence classification on its
    # receipt so the source-truth-bucket doesn't have to be re-derived from the dry-run plane.
    assert "divergence_class" in detail, (
        f"replace residual refusal detail lost divergence_class: keys={sorted(detail)}"
    )
    assert detail["divergence_class"] is not None
    # The dry-run's oracle_match rule id ALSO lifts to the actual-replay receipt so
    # the specific residual-variant (replacement_mismatch / target_missing /
    # content_mismatch / position_mismatch) is distinguishable per AGENTS §1.10.
    assert detail["oracle_match_rule_id"] == (
        "nz_dry_run_structural_replace_residual_replacement_mismatch_in_oracle"
    )


# --- 6. A structural family requested without a surface is not attempted ------


def test_actual_replay_structural_family_without_surface_is_not_attempted() -> None:
    # No surface provided: the structural family cannot even be attempted. It is
    # reported as family-not-attempted (a distinct named diagnostic), separate
    # from the fail-closed transition count, never silently dropped.
    report = build_actual_replay(
        _archive(),
        work_id=_WORK_ID,
        preflight=_empty_preflight(),
        families=("replace",),
        surface=None,
    )
    summary = report.summary()
    assert summary["transitions_replayed"] == 0
    assert summary["transitions_refused"] == 0
    assert summary["families_not_attempted"].get("replace") == 1
    not_attempted_rules = {
        refusal.rule_id for refusal in report.families_not_attempted
    }
    assert NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID in not_attempted_rules


# --- 7. Source-change-only style proof never reaches actual replay -----------


def test_actual_replay_structural_does_not_promote_non_candidate_target() -> None:
    # A replace witness whose target address is not an exact candidate is refused
    # by the dry-run kernel (not in scope), so it never reaches actual replay.
    non_candidate = _FakeWitnessRow(
        row_id="nz-opw-9",
        operation_family="substituted",
        target_path=(("section", "41"),),
        amending_provision_hrefs=(_REPLACE_HREF,),
        target_status="rejected",
    )
    report = _run(_AFTER_XML_AGREES, (non_candidate,), ("replace",))
    summary = report.summary()
    assert summary["transitions_replayed"] == 0
    assert summary["replay_claims"] is False
