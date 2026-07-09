"""Materialize a conditional branch against enacted law — "if enacted, then …".

Hermetic tests exercise the pure structural apply + diff on a constructed
provision (INSERT/REPLACE/REPEAL, unsupported → finding, the never-replay-authorized
invariant). One slow test loads the REAL enacted 603/2006 §4 (full amendment
replay) and materializes the vm045 INSERT — the actual counterfactual law.
"""
from __future__ import annotations

import os

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.source_document import (
    AssuranceTier,
    CandidateOperation,
    ConditionalBranch,
    ProposalAuthorityStatus,
    SourceAnchor,
)
from lawvm.finland.source_document import (
    MaterializedProvision,
    apply_candidate_op,
    materialize_conditional_provision,
)

_ANCHOR = SourceAnchor(artifact_digest="a" * 64, locator="johtolause", page_num=10)


def _unit(label: str, text: str = "") -> IRNode:
    return IRNode(kind=IRNodeKind.P, label=label, text=text)


def _section4() -> IRNode:
    # §4 with momentti 1–4 (like the real 603/2006 §4).
    return IRNode(
        kind=IRNodeKind.HCONTAINER,
        label="4",
        children=tuple(_unit(str(i), f"momentti {i}") for i in range(1, 5)),
    )


def _op(action: str, ref: str = "section:4/subsection:5", payload: str = "the new momentti text") -> CandidateOperation:
    return CandidateOperation(
        action=action,
        target_statute_id="603/2006",
        target_provision_ref=ref,
        payload_text=payload,
        source_anchor=_ANCHOR,
        assurance_tier=AssuranceTier.MULTI_WITNESS_ADJUDICATED,
    )


def _branch(*ops: CandidateOperation) -> ConditionalBranch:
    return ConditionalBranch(
        branch_id="fi:he:VM045:00/2026:draft",
        condition="VM045:00/2026 enacted",
        candidate_ops=tuple(ops),
        authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
    )


def test_insert_appends_the_new_momentti() -> None:
    conditional, finding = apply_candidate_op(_section4(), _op("insert"))
    assert finding is None
    assert len(conditional.children) == 5  # momentti 1–4 + new 5
    new = conditional.children[-1]
    assert new.label == "5"
    assert new.text == "the new momentti text"
    assert new.attrs.get("conditional") == "1"


def test_replace_swaps_the_matching_unit() -> None:
    conditional, finding = apply_candidate_op(_section4(), _op("replace", ref="section:4/subsection:2"))
    assert finding is None
    assert len(conditional.children) == 4  # same count
    assert conditional.children[1].text == "the new momentti text"  # momentti 2 rewritten


def test_repeal_removes_the_unit() -> None:
    conditional, finding = apply_candidate_op(_section4(), _op("repeal", ref="section:4/subsection:3"))
    assert finding is None
    assert len(conditional.children) == 3
    assert [c.label for c in conditional.children] == ["1", "2", "4"]


def test_unsupported_action_is_a_finding_not_a_silent_noop() -> None:
    ir, finding = apply_candidate_op(_section4(), _op("move"))
    assert finding is not None and "move" in finding
    assert len(ir.children) == 4  # unchanged


def test_missing_replace_target_is_a_finding() -> None:
    _, finding = apply_candidate_op(_section4(), _op("replace", ref="section:4/subsection:9"))
    assert finding is not None and "not found" in finding


def test_materialize_produces_conditional_ir_and_diff() -> None:
    mat = materialize_conditional_provision(
        _section4(), _branch(_op("insert")), statute_id="603/2006", provision_ref="section:4/subsection:5"
    )
    assert isinstance(mat, MaterializedProvision)
    assert mat.replay_authorized is False
    assert len(mat.conditional_ir.children) == 5
    assert mat.findings == ()
    assert len(mat.diff_lines) == 1
    assert "gains unit 5" in mat.diff_lines[0] and "6" not in mat.diff_lines[0].split("→")[0]


def test_ops_for_other_statutes_are_skipped() -> None:
    other = CandidateOperation(
        action="insert",
        target_statute_id="999/2015",
        target_provision_ref="section:7/subsection:1",
        payload_text="other law",
        source_anchor=_ANCHOR,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
    )
    mat = materialize_conditional_provision(_section4(), _branch(_op("insert"), other), statute_id="603/2006")
    # only the 603/2006 op applied; the 999/2015 op is left for its own provision.
    assert len(mat.conditional_ir.children) == 5


def test_materialized_provision_is_never_replay_authorized() -> None:
    with pytest.raises(ValueError):
        MaterializedProvision(
            statute_id="603/2006",
            provision_ref="section:4",
            enacted_ir=_section4(),
            conditional_ir=_section4(),
            diff_lines=(),
            findings=(),
            replay_authorized=True,  # forbidden
        )


# --------------------------------------------------------------------------- #
# Slow: load the REAL enacted 603/2006 §4 and materialize the conditional      #
# --------------------------------------------------------------------------- #


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("LAWVM_CANONICAL_DATA_ROOT"), reason="needs LAWVM_CANONICAL_DATA_ROOT corpus"
)
def test_slow_materialize_real_603_2006_section4() -> None:
    import asyncio

    from lawvm.finland.source_document import load_enacted_provision

    enacted = asyncio.run(load_enacted_provision("603/2006", "4"))
    if enacted is None:
        pytest.skip("603/2006 §4 not resolvable in this corpus")
    n_before = len(enacted.children)
    mat = materialize_conditional_provision(
        enacted,
        _branch(_op("insert", payload="Sen lisäksi, mitä 1 momentissa säädetään, hakijalle palautetaan …")),
        statute_id="603/2006",
        provision_ref="section:4/subsection:5",
    )
    assert len(mat.conditional_ir.children) == n_before + 1  # §4 gains one momentti
    assert mat.conditional_ir.children[-1].label == "5"
    assert mat.replay_authorized is False
    assert any("gains unit 5" in d for d in mat.diff_lines)
