"""Auto-classified consolidation-error-candidate residual surface tests.

These exercise the typed divergence signal attached to residual mutation-boundary
proofs: each residual's diverging candidate-vs-oracle node-text pairs are
reconstructed and typed by ``classify_oracle_divergence``, folded into one
target-level ``divergence_class`` (``structural_nodeset`` / ``editorial`` /
``substantive``), with a non-commensurable-whole-node gate that types a
single-amendment payload compared against a further-amended structural container
OUT of the candidate set. The signal is ADDITIVE: it never changes
``oracle_match`` and the actual-replay path keeps refusing every non-"agrees" op.
"""

from __future__ import annotations

import json

from lawvm.new_zealand.dry_run import (
    NZ_DIVERGENCE_CLASS_EDITORIAL,
    NZ_DIVERGENCE_CLASS_STRUCTURAL_NODESET,
    NZ_DIVERGENCE_CLASS_SUBSTANTIVE,
    _NON_COMMENSURABLE_DESCENDANT_THRESHOLD,
    NZMutationBoundaryProof,
    NZNodeDivergence,
    _classify_oracle_target_divergence,
    _classify_oracle_text_divergence,
    _divergence_proof_fields,
    _is_non_commensurable_whole_node,
)
from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode


def _node(
    kind: str,
    path: tuple[str, ...],
    text: str,
    *,
    label: str = "",
    deletion_status: str = "",
) -> NZSourceNode:
    return NZSourceNode(
        kind=kind,
        path=path,
        xml_id="",
        xml_path="",
        source_zone="body",
        label=label,
        heading="",
        deletion_status=deletion_status,
        text=text,
        history=(),
    )


def _doc(nodes: tuple[NZSourceNode, ...]) -> NZSourceDocument:
    return NZSourceDocument(
        xml_locator="loc",
        version_id="v",
        metadata={},
        nodes=nodes,
        document_history=(),
    )


# --- _is_non_commensurable_whole_node predicate. ------------------------------


def test_non_commensurable_predicate_types_container_kinds() -> None:
    # Every structural container kind is non-commensurable regardless of size.
    for kind in ("part", "subpart", "crossheading", "prov"):
        assert _is_non_commensurable_whole_node(kind, 0) is True
        assert _is_non_commensurable_whole_node(kind, 500) is True


def test_non_commensurable_predicate_leaf_kinds_gated_by_descendant_backstop() -> None:
    # A non-container leaf is commensurable until its subtree balloons past the
    # data-justified backstop (strictly greater than 2x the genuine-leaf ceiling).
    assert _is_non_commensurable_whole_node("subprov", 0) is False
    assert _is_non_commensurable_whole_node("label-para", 11) is False
    assert _is_non_commensurable_whole_node("subprov", _NON_COMMENSURABLE_DESCENDANT_THRESHOLD) is False
    assert _is_non_commensurable_whole_node("subprov", _NON_COMMENSURABLE_DESCENDANT_THRESHOLD + 1) is True


# --- _classify_oracle_target_divergence (structural REPLACE/INSERT). ----------


def test_target_absent_in_oracle_classifies_to_none() -> None:
    candidate_root = _node("subprov", ("amend", "subprov:8"), "the candidate body")
    oracle = _doc(())  # target not present
    result = _classify_oracle_target_divergence(
        oracle, ("part:2", "prov:11", "subprov:8"), candidate_root=candidate_root, candidate_descendants=()
    )
    assert result.divergence_class is None
    assert result.node_pairs == ()
    assert result.non_commensurable_whole_node is False


def test_editorial_divergence_is_typed_editorial_not_substantive() -> None:
    # A trailing-period-only diff on a non-container leaf is editorial; the
    # non-commensurable gate is irrelevant (it only fires for substantive).
    candidate_root = _node("subprov", ("amend", "subprov:8"), "the same words")
    oracle = _doc((_node("subprov", ("part:2", "prov:11", "subprov:8"), "the same words."),))
    result = _classify_oracle_target_divergence(
        oracle, ("part:2", "prov:11", "subprov:8"), candidate_root=candidate_root, candidate_descendants=()
    )
    assert result.divergence_class == NZ_DIVERGENCE_CLASS_EDITORIAL
    assert result.non_commensurable_whole_node is False
    # The diverging pair is reconstructed (caller decides whether to retain it).
    assert len(result.node_pairs) == 1
    assert result.node_pairs[0].is_editorial is True


def test_substantive_divergence_on_leaf_is_substantive_and_commensurable() -> None:
    candidate_root = _node("subprov", ("amend", "subprov:8"), "the tax fraction of the original purchase price")
    oracle = _doc(
        (_node("subprov", ("part:2", "prov:11", "subprov:8"), "the rebate for goods received from an associate"),)
    )
    result = _classify_oracle_target_divergence(
        oracle, ("part:2", "prov:11", "subprov:8"), candidate_root=candidate_root, candidate_descendants=()
    )
    assert result.divergence_class == NZ_DIVERGENCE_CLASS_SUBSTANTIVE
    assert result.non_commensurable_whole_node is False
    assert result.node_pairs[0].is_editorial is False
    assert result.node_pairs[0].candidate_text
    assert result.node_pairs[0].oracle_text


def test_substantive_divergence_on_whole_part_is_non_commensurable() -> None:
    # A whole-Part target the oracle further amended: substantive by text but a
    # non-commensurable single-amendment-vs-container comparison -> typed out.
    candidate_root = _node("part", ("amend", "part:10"), "Part 10 our single-amendment payload heading", label="10")
    candidate_descendants = (_node("prov", ("amend", "part:10", "prov:108"), "our section 108 body", label="108"),)
    oracle = _doc(
        (
            _node("part", ("part:10",), "Part 10 the fully consolidated heading", label="10"),
            _node("prov", ("part:10", "prov:108"), "a fully consolidated section 108 body", label="108"),
        )
    )
    result = _classify_oracle_target_divergence(
        oracle, ("part:10",), candidate_root=candidate_root, candidate_descendants=candidate_descendants
    )
    assert result.divergence_class == NZ_DIVERGENCE_CLASS_SUBSTANTIVE
    assert result.non_commensurable_whole_node is True
    assert result.oracle_descendant_count == 1


def test_node_set_difference_is_structural_nodeset() -> None:
    # The subtree signature is label-stripped (kind-only relative paths), so a
    # node-set difference must be a difference in the (relative_path, kind) keys:
    # here the oracle carries an extra child of a DIFFERENT kind (label-para) the
    # candidate lacks, so the aligned key sets differ.
    candidate_root = _node("prov", ("amend", "prov:5"), "section body")
    candidate_descendants = (_node("subprov", ("amend", "prov:5", "subprov:1"), "subsection one"),)
    oracle = _doc(
        (
            _node("prov", ("prov:5",), "section body"),
            _node("subprov", ("prov:5", "subprov:1"), "subsection one"),
            _node("label-para", ("prov:5", "subprov:1", "label-para:a"), "paragraph a added by oracle"),
        )
    )
    result = _classify_oracle_target_divergence(
        oracle, ("prov:5",), candidate_root=candidate_root, candidate_descendants=candidate_descendants
    )
    assert result.divergence_class == NZ_DIVERGENCE_CLASS_STRUCTURAL_NODESET
    assert result.node_pairs == ()


# --- _classify_oracle_text_divergence (TEXT_REPLACE). -------------------------


def test_text_divergence_substantive_leaf_is_candidate_classifiable() -> None:
    oracle = _doc((_node("subprov", ("prov:3", "subprov:1"), "pay the levy within ninety days of demand"),))
    result = _classify_oracle_text_divergence(
        oracle, ("prov:3", "subprov:1"), candidate_after_text="pay the tax within thirty days of assessment"
    )
    assert result.divergence_class == NZ_DIVERGENCE_CLASS_SUBSTANTIVE
    assert result.non_commensurable_whole_node is False
    assert len(result.node_pairs) == 1


def test_text_divergence_editorial_case_only() -> None:
    oracle = _doc((_node("subprov", ("prov:3", "subprov:1"), "the Secretary of the Board"),))
    result = _classify_oracle_text_divergence(
        oracle, ("prov:3", "subprov:1"), candidate_after_text="the secretary of the Board"
    )
    assert result.divergence_class == NZ_DIVERGENCE_CLASS_EDITORIAL


# --- _divergence_proof_fields candidate-only text retention. ------------------


def _substantive_leaf_divergence() -> NZNodeDivergence:
    return NZNodeDivergence(
        relative_path="",
        kind="subprov",
        candidate_text="our payload",
        oracle_text="the oracle text",
        sub_family="substantive",
        is_editorial=False,
    )


def test_proof_fields_retain_texts_only_for_candidates() -> None:
    from lawvm.new_zealand.dry_run import NZTargetDivergence

    pair = _substantive_leaf_divergence()
    divergence = NZTargetDivergence(
        divergence_class=NZ_DIVERGENCE_CLASS_SUBSTANTIVE,
        sub_families=("substantive",),
        non_commensurable_whole_node=False,
        oracle_descendant_count=0,
        node_pairs=(pair,),
    )
    # Candidate (residual + boundary held + substantive + commensurable): retain.
    fields = _divergence_proof_fields("residual_replacement_mismatch", True, divergence)
    assert fields["divergence_node_pairs"] == (pair,)
    # Boundary broke -> NOT a candidate -> texts dropped (no bloat).
    fields_no_boundary = _divergence_proof_fields("residual_replacement_mismatch", False, divergence)
    assert fields_no_boundary["divergence_node_pairs"] == ()


def test_proof_fields_drop_texts_for_non_commensurable() -> None:
    from lawvm.new_zealand.dry_run import NZTargetDivergence

    divergence = NZTargetDivergence(
        divergence_class=NZ_DIVERGENCE_CLASS_SUBSTANTIVE,
        sub_families=("substantive",),
        non_commensurable_whole_node=True,
        oracle_descendant_count=521,
        node_pairs=(_substantive_leaf_divergence(),),
    )
    fields = _divergence_proof_fields("residual_replacement_mismatch", True, divergence)
    assert fields["non_commensurable_whole_node"] is True
    assert fields["divergence_node_pairs"] == ()  # non-commensurable is never a candidate


# --- is_consolidation_error_candidate predicate on the proof. -----------------


def _proof(**kwargs: object) -> NZMutationBoundaryProof:
    base: dict[str, object] = dict(
        op_id="op",
        action="replace",
        target_address="section:11(8D)",
        selected_source_path=("part:2", "prov:11", "subprov:8D"),
        target_xml_id="",
        target_digest_before="a",
        target_digest_after="b",
        operation_payload="",
        occupancy_before="substantive",
        occupancy_after="substantive",
        parent_source_path=("part:2", "prov:11"),
        parent_digest_before="p",
        parent_digest_after="p",
        unaffected_neighbor_paths=(),
        unaffected_neighbor_digests_before=(),
        unaffected_neighbor_digests_after=(),
        neighbors_unchanged=True,
        oracle_version_id="v",
        oracle_target_present=True,
        oracle_target_occupancy="substantive",
        oracle_match="residual_replacement_mismatch",
        oracle_match_rule_id="rid",
    )
    base.update(kwargs)
    return NZMutationBoundaryProof(**base)  # type: ignore[arg-type]


def test_candidate_predicate_true_for_substantive_commensurable_residual() -> None:
    proof = _proof(divergence_class=NZ_DIVERGENCE_CLASS_SUBSTANTIVE, non_commensurable_whole_node=False)
    assert proof.is_consolidation_error_candidate is True


def test_candidate_predicate_false_for_editorial() -> None:
    proof = _proof(divergence_class=NZ_DIVERGENCE_CLASS_EDITORIAL)
    assert proof.is_consolidation_error_candidate is False


def test_candidate_predicate_false_for_non_commensurable() -> None:
    proof = _proof(divergence_class=NZ_DIVERGENCE_CLASS_SUBSTANTIVE, non_commensurable_whole_node=True)
    assert proof.is_consolidation_error_candidate is False


def test_candidate_predicate_false_when_agrees() -> None:
    proof = _proof(oracle_match="agrees", divergence_class=None)
    assert proof.is_consolidation_error_candidate is False


def test_candidate_predicate_false_when_neighbors_changed() -> None:
    proof = _proof(divergence_class=NZ_DIVERGENCE_CLASS_SUBSTANTIVE, neighbors_unchanged=False)
    assert proof.is_consolidation_error_candidate is False


def test_proof_jsonable_carries_divergence_fields() -> None:
    pair = _substantive_leaf_divergence()
    proof = _proof(
        divergence_class=NZ_DIVERGENCE_CLASS_SUBSTANTIVE,
        divergence_sub_families=("substantive",),
        non_commensurable_whole_node=False,
        divergence_node_pairs=(pair,),
    )
    payload = proof.to_jsonable()
    assert payload["divergence_class"] == NZ_DIVERGENCE_CLASS_SUBSTANTIVE
    assert payload["divergence_sub_families"] == ["substantive"]
    assert payload["non_commensurable_whole_node"] is False
    assert payload["is_consolidation_error_candidate"] is True
    assert payload["divergence_node_pairs"][0]["candidate_text"] == "our payload"
    assert payload["divergence_node_pairs"][0]["oracle_text"] == "the oracle text"
    # Round-trips through json without error.
    json.dumps(payload)
