import pytest

from lawvm.core.certified_transition import (
    CertifiedTreeTransitionCore,
    certified_tree_transitions_from_receipt,
)
from lawvm.core.write_receipt import WriteReceipt


_HASH_A = "a" * 64
_HASH_B = "b" * 64


def test_created_receipt_projects_to_set_subtree_transition_core() -> None:
    receipt = WriteReceipt(
        op_id="op1",
        helper="test_helper",
        action="insert",
        bound_target_path=None,
        landed_primary_path=(("section", "1"),),
        created_paths=((("section", "1"),),),
        recovery_rule_ids=("test_insert_rule",),
        pre_hashes={"section:1": ""},
        post_hashes={"section:1": _HASH_A},
    )

    rows = certified_tree_transitions_from_receipt(
        receipt,
        effective_date="2020-01-01",
        source_refs=("fi.finlex.alkup.2020.1",),
    )

    assert rows == (
        CertifiedTreeTransitionCore(
            transition_id="t000001:2020-01-01:section:1",
            sequence=1,
            effective_date="2020-01-01",
            action="set_subtree",
            target_address="section:1",
            pre_hash="",
            post_hash=f"sha256:{_HASH_A}",
            payload_hash=f"sha256:{_HASH_A}",
            source_refs=("fi.finlex.alkup.2020.1",),
            source_anchors=(),
        ),
    )
    assert rows[0].to_jsonable_dict()["source_refs"] == ["fi.finlex.alkup.2020.1"]


def test_relabel_receipt_projects_to_delete_then_set_transition_cores() -> None:
    receipt = WriteReceipt(
        op_id="op2",
        helper="test_helper",
        action="relabel",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "1a"),),
        renumbered_paths=(((("section", "1"),), (("section", "1a"),)),),
        migration_rule_ids=("section_relabel_renumber",),
        pre_hashes={
            "section:1": _HASH_A,
            "section:1a": "",
        },
        post_hashes={
            "section:1": "",
            "section:1a": _HASH_B,
        },
    )

    rows = certified_tree_transitions_from_receipt(
        receipt,
        effective_date="2021-02-03",
        sequence_start=7,
    )

    assert [(row.sequence, row.action, row.target_address) for row in rows] == [
        (7, "delete_subtree", "section:1"),
        (8, "set_subtree", "section:1a"),
    ]
    assert rows[0].pre_hash == f"sha256:{_HASH_A}"
    assert rows[0].post_hash == ""
    assert rows[0].payload_hash == ""
    assert rows[1].pre_hash == ""
    assert rows[1].post_hash == f"sha256:{_HASH_B}"
    assert rows[1].payload_hash == f"sha256:{_HASH_B}"


def test_receipt_projection_rejects_missing_declared_hash_pair() -> None:
    receipt = WriteReceipt(
        op_id="op3",
        helper="test_helper",
        action="insert",
        bound_target_path=None,
        landed_primary_path=(("section", "1"),),
        created_paths=((("section", "1"),),),
        pre_hashes={"section:1": ""},
        post_hashes={},
    )

    with pytest.raises(ValueError, match="missing post_hashes"):
        certified_tree_transitions_from_receipt(receipt, effective_date="2020-01-01")


def test_receipt_projection_rejects_undeclared_hashes() -> None:
    receipt = WriteReceipt(
        op_id="op4",
        helper="test_helper",
        action="insert",
        bound_target_path=None,
        landed_primary_path=(("section", "1"),),
        created_paths=((("section", "1"),),),
        pre_hashes={"section:1": "", "section:2": ""},
        post_hashes={"section:1": _HASH_A},
    )

    with pytest.raises(ValueError, match="undeclared pre_hashes"):
        certified_tree_transitions_from_receipt(receipt, effective_date="2020-01-01")


def test_receipt_projection_rejects_bad_hash_shape() -> None:
    receipt = WriteReceipt(
        op_id="op5",
        helper="test_helper",
        action="replace",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "1"),),
        replaced_paths=((("section", "1"),),),
        pre_hashes={"section:1": _HASH_A},
        post_hashes={"section:1": "bad"},
    )

    with pytest.raises(ValueError, match="post_hash must be empty"):
        certified_tree_transitions_from_receipt(receipt, effective_date="2020-01-01")


def test_receipt_projection_rejects_declared_noop_hash_pair() -> None:
    receipt = WriteReceipt(
        op_id="op6",
        helper="test_helper",
        action="replace",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "1"),),
        replaced_paths=((("section", "1"),),),
        pre_hashes={"section:1": _HASH_A},
        post_hashes={"section:1": _HASH_A},
    )

    with pytest.raises(ValueError, match="pre_hash == post_hash"):
        certified_tree_transitions_from_receipt(receipt, effective_date="2020-01-01")


def test_receipt_projection_rejects_empty_declared_footprint() -> None:
    receipt = WriteReceipt(
        op_id="op7",
        helper="test_helper",
        action="replace",
        bound_target_path=None,
        landed_primary_path=None,
    )

    with pytest.raises(ValueError, match="no declared footprint"):
        certified_tree_transitions_from_receipt(receipt, effective_date="2020-01-01")
