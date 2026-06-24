"""Tests for the identity-intrinsic + synthetic-label leak sweeps (LS-12 / LS-13).

A clean synthetic dossier (IRStatute body tree, ProvisionTimeline, edge payloads,
projection rows) sweeps green; a positional id or synthetic marker injected into
any one stored surface drives the matching sweep RED. ``attrs.source_rule_id`` is
the one sanctioned home for a synthesized rule id and stays green.
"""

from __future__ import annotations

import pytest

from lawvm.core.branch_authority import BranchGraphEdge
from lawvm.core.branch_projection import BranchImpactRow
from lawvm.core.identity_intrinsic_audit import (
    IdentityLeakError,
    sweep_positional_id_leaks,
    sweep_synthetic_label_leaks,
)
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    ProvisionTimeline,
    ProvisionVersion,
)
from lawvm.core.semantic_types import IRNodeKind


# ── Clean synthetic dossier ─────────────────────────────────────────────────────


def _clean_address() -> LegalAddress:
    return LegalAddress(path=(("section", "1"), ("subsection", "2")))


def _clean_body() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                text="Tätä lakia sovelletaan.",
                # The sanctioned home for a synthesized rule id.
                attrs={"source_rule_id": "fi_section_intro_normalized", "eId": "sec_1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="2",
                        text="Toinen momentti.",
                        attrs={"eId": "sec_1__subsec_2"},
                    ),
                ),
            ),
        ),
    )


def _clean_statute() -> IRStatute:
    return IRStatute(
        statute_id="999/2025",
        title="Testilaki",
        body=_clean_body(),
        metadata={"source_rule_id": "fi_statute_metadata_rule"},
    )


def _clean_timeline() -> ProvisionTimeline:
    return ProvisionTimeline(
        address=_clean_address(),
        versions=[
            ProvisionVersion(
                effective="2025-01-01",
                enacted="2024-12-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text="versio"),
            ),
        ],
    )


def _clean_edge() -> BranchGraphEdge:
    return BranchGraphEdge(
        branch_id="branch-a",
        edge_kind="would_amend",
        source_statute_id="999/2025",
        target_statute_id="111/2020",
        target_address="section:3",
        operation_id="op-7",
    )


def _clean_projection_row() -> BranchImpactRow:
    return BranchImpactRow(
        row_id="row-abc",
        branch_id="branch-a",
        edge_kind="amends",
        target_statute_id="111/2020",
        target_address="section:3",
        detail={"reason": "amendment", "source_rule_id": "fi_branch_projection_rule"},
    )


def _clean_dossier() -> dict[str, object]:
    return {
        "statute": _clean_statute(),
        "timelines": [_clean_timeline()],
        "edges": [_clean_edge()],
        "projection_rows": [_clean_projection_row()],
    }


# ── (a) clean tree passes both sweeps ───────────────────────────────────────────


def test_clean_dossier_passes_positional_sweep() -> None:
    report = sweep_positional_id_leaks(_clean_dossier())
    assert report.clean, report.findings


def test_clean_dossier_passes_synthetic_sweep() -> None:
    report = sweep_synthetic_label_leaks(_clean_dossier())
    assert report.clean, report.findings


def test_source_rule_id_synthetic_marker_is_sanctioned() -> None:
    # A synthetic marker living under attrs.source_rule_id is the one allowed home.
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        attrs={"source_rule_id": "fi_synthetic_n3_stub", "note": "n3"},
    )
    # The note=="n3" is NOT under source_rule_id, so the node as a whole is dirty,
    # but a node whose ONLY marker is under source_rule_id stays clean.
    clean_node = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        attrs={"source_rule_id": "fi_synthetic_n3_stub"},
    )
    assert sweep_synthetic_label_leaks(clean_node).clean
    assert not sweep_synthetic_label_leaks(node).clean  # the note key leaks


# ── (b) positional-id leaks go RED per surface ──────────────────────────────────


def test_positional_id_in_legal_address_path_is_red() -> None:
    dossier = _clean_dossier()
    bad_timeline = ProvisionTimeline(
        address=LegalAddress(path=(("section", "expr#42"),)),
        versions=[ProvisionVersion(effective="2025-01-01")],
    )
    dossier["timelines"] = [bad_timeline]
    report = sweep_positional_id_leaks(dossier)
    assert not report.clean
    assert any(f.vocab == "expr_counter" for f in report.findings)
    assert any("expr#42" in f.value for f in report.findings)


def test_positional_id_in_timeline_version_content_is_red() -> None:
    dossier = _clean_dossier()
    bad = ProvisionTimeline(
        address=_clean_address(),
        versions=[
            ProvisionVersion(
                effective="2025-01-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="tuple_index=3"),
            )
        ],
    )
    dossier["timelines"] = [bad]
    report = sweep_positional_id_leaks(dossier)
    assert not report.clean
    assert any(f.vocab == "tuple_index" for f in report.findings)


def test_positional_id_in_edge_payload_is_red() -> None:
    dossier = _clean_dossier()
    bad_edge = BranchGraphEdge(
        branch_id="branch-a",
        edge_kind="would_amend",
        target_statute_id="111/2020",
        target_address="row_ordinal=7",
    )
    dossier["edges"] = [bad_edge]
    report = sweep_positional_id_leaks(dossier)
    assert not report.clean
    assert any(f.vocab == "row_ordinal" for f in report.findings)


def test_positional_id_lxml_ptr_in_projection_row_is_red() -> None:
    row = BranchImpactRow(
        row_id="row-x",
        branch_id="branch-a",
        edge_kind="amends",
        target_statute_id="111/2020",
        detail={"node_id": "lxml_ptr=0x7f3ab1c0"},
    )
    report = sweep_positional_id_leaks(row)
    assert not report.clean
    assert any(f.vocab == "lxml_ptr" for f in report.findings)


def test_raise_if_dirty_fails_loud() -> None:
    report = sweep_positional_id_leaks(
        LegalAddress(path=(("section", "expr#1"),))
    )
    with pytest.raises(IdentityLeakError):
        report.raise_if_dirty()


# ── (b) synthetic-label leaks go RED per surface ────────────────────────────────


def test_synthetic_marker_in_legal_address_path_is_red() -> None:
    addr = LegalAddress(path=(("section", "1"), ("subsection", "sec_1__n3")))
    report = sweep_synthetic_label_leaks(addr)
    assert not report.clean
    assert any(f.vocab == "synthetic_n_ordinal" for f in report.findings)
    assert any("__n3" in f.value for f in report.findings)


def test_synthetic_marker_in_irnode_label_is_red() -> None:
    node = IRNode(kind=IRNodeKind.SUBSECTION, label="n5")
    report = sweep_synthetic_label_leaks(node)
    assert not report.clean
    assert any(f.vocab == "synthetic_n_ordinal" for f in report.findings)


def test_synthetic_test_marker_in_statute_id_is_red() -> None:
    statute = IRStatute(
        statute_id="__test__/9999/synthetic_source",
        title="Testilaki",
        body=IRNode(kind=IRNodeKind.BODY),
    )
    report = sweep_synthetic_label_leaks(statute)
    assert not report.clean
    assert any(f.vocab == "synthetic_test_marker" for f in report.findings)


def test_synthetic_marker_in_edge_payload_is_red() -> None:
    edge = BranchGraphEdge(
        branch_id="branch-a",
        edge_kind="would_amend",
        target_statute_id="111/2020",
        target_address="section:__n7",
    )
    report = sweep_synthetic_label_leaks(edge)
    assert not report.clean
    assert any(f.vocab == "synthetic_n_ordinal" for f in report.findings)
