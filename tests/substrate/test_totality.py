"""Pins for the within-work totality LENS (design §23) — the THIRD verdict axis.

Strategy mirrors ``test_checker.py``: build a tiny synthetic-but-conforming pack
whose base address tree, state selection rows, and proof residuals/coverage are
hand-constructed, run it through the production ``check`` path, and assert the
exact :class:`TotalityVerdict` + that the silent-gap fire drill fires.

Three criteria, three drills:

* a complete pack (every node owned, no residuals) → ``TOTAL``;
* the same pack + one typed residual → ``TOTAL_WITH_RESIDUALS`` (qualified,
  never silently ``TOTAL``);
* a node missing its selection entry → ``INCOMPLETE`` (the silent-gap drill).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from lawvm.substrate.canonical_json import JsonValue, wrap_row
from lawvm.substrate.checker import Checker, Pack, PackLayerData
from lawvm.substrate.manifest import PackLayer, PackManifest, PackProvenance
from lawvm.substrate.roots import set_root
from lawvm.substrate.totality import (
    TotalityShortfallCode,
    TotalityVerdict,
    compute_totality,
)

# --------------------------------------------------------------------------- #
# Fixture builders.                                                           #
# --------------------------------------------------------------------------- #


def _address_node(struct_node_id: str, address_path: str) -> dict[str, JsonValue]:
    return {
        "schema": "lawvm.address_node.v1",
        "struct_node_id": struct_node_id,
        "work_id": "fi:act:301/2004",
        "structural_kind": "section",
        "address_path": address_path,
    }


def _selection_row(address_id: str, status: str = "selected") -> dict[str, JsonValue]:
    return {
        "schema": "lawvm.selection_row.v1",
        "work_id": "fi:act:301/2004",
        "address_id": address_id,
        "selection_status": status,
        "selection_key": f"key:{address_id}:{status}",
    }


def _residual(kind: str | None, residual_id: str) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "schema": "lawvm.residual.v1",
        "residual_id": residual_id,
        "blocking": False,
        "detail": "synthetic",
        "subject": "source",
    }
    if kind is not None:
        body["kind"] = kind
    return body


def _coverage(coverage_class: str) -> dict[str, JsonValue]:
    return {
        "schema": "lawvm.coverage_row.v1",
        "coverage_row_id": f"cov:{coverage_class}",
        "coverage_class": coverage_class,
        "count": 1,
        "detail": "synthetic",
    }


def _wrapped(objects: list[Mapping[str, JsonValue]]) -> tuple[Mapping[str, JsonValue], ...]:
    return tuple(wrap_row(obj) for obj in objects)


def _layer(kind: str, domain: str, objects: list[Mapping[str, JsonValue]]) -> PackLayerData:
    rows = _wrapped(objects)
    hashes = [str(r["object_hash"]) for r in rows]
    return PackLayerData(
        kind=kind, domain=domain, root_fn="SetRoot", root=set_root(domain, hashes), rows=rows
    )


def _manifest(layers: Mapping[str, PackLayerData]) -> PackManifest:
    descriptors = tuple(
        PackLayer(
            kind=kind,
            path=f"{kind}/{kind}.jsonl",
            row_schema="lawvm.mixed",
            codec="identity",
            dict_id="",
            uncompressed_sha256="sha256:aa",
            storage_sha256="sha256:aa",
            root=data.root,
            root_fn=data.root_fn,
            row_count=len(data.rows),
        )
        for kind, data in layers.items()
    )
    return PackManifest(
        pack_kind="work_pack",
        work_ids=("fi:act:301/2004",),
        corpus_version="corpus:2026-06-22",
        identity_encoding="lawvm.canonical_json.v1",
        storage_codec="identity",
        dict_id="",
        profiles=("lawvm.canon.semantic_text.v1",),
        selection_profiles=("lawvm.selection.governing_text.v1",),
        schemas={},
        layers=descriptors,
        roots={},
        required_layers_for_browse=("base", "state"),
        required_layers_for_audit=("base", "state", "proof"),
        optional_layers=(),
        provenance=PackProvenance(
            lawvm_git_commit="abc",
            engine_version="lawvm-0.1",
            source_policy_id="archival_exact",
            checkable_source_bundle_policy="archival_exact",
            created_at="2026-06-22T00:00:00Z",
            dirty_tree=False,
        ),
    )


def _pack(
    *,
    address_nodes: list[dict[str, JsonValue]],
    selection_rows: list[dict[str, JsonValue]],
    residuals: list[dict[str, JsonValue]],
    coverage: list[dict[str, JsonValue]],
) -> Pack:
    base = _layer("base", "base", [*address_nodes])
    state = _layer("state", "state", [*selection_rows])
    proof = _layer("proof", "proof", [*residuals, *coverage])
    layers = {"base": base, "state": state, "proof": proof}
    return Pack(manifest=_manifest(layers), layers=layers)


# --------------------------------------------------------------------------- #
# The three lens tests (direct compute_totality).                            #
# --------------------------------------------------------------------------- #


def test_complete_pack_is_total() -> None:
    """Every addressable node owned + no residuals → TOTAL."""
    result = compute_totality(
        base_rows=_wrapped([_address_node("nid:1", "section:1"), _address_node("nid:2", "section:2")]),
        state_rows=_wrapped([_selection_row("nid:1"), _selection_row("nid:2")]),
        proof_rows=_wrapped([_coverage("owned")]),
    )
    assert result.verdict is TotalityVerdict.TOTAL
    assert result.addressable_nodes == 2
    assert result.owned_nodes == 2
    assert result.residual_count == 0
    assert result.shortfalls == ()


def test_typed_residual_qualifies_to_total_with_residuals() -> None:
    """Complete coverage + a TYPED residual → TOTAL_WITH_RESIDUALS, never silently TOTAL."""
    result = compute_totality(
        base_rows=_wrapped([_address_node("nid:1", "section:1")]),
        state_rows=_wrapped([_selection_row("nid:1")]),
        proof_rows=_wrapped([_residual("source_pathology", "res:1"), _coverage("residual")]),
    )
    assert result.verdict is TotalityVerdict.TOTAL_WITH_RESIDUALS
    assert result.residual_count == 1
    assert result.residual_kinds == ("source_pathology",)
    assert result.shortfalls == ()


def test_missing_selection_entry_is_incomplete_silent_gap() -> None:
    """A node with no selection entry AND no typed reason → INCOMPLETE (the fire drill)."""
    result = compute_totality(
        base_rows=_wrapped(
            [_address_node("nid:1", "section:1"), _address_node("nid:ORPHAN", "section:99")]
        ),
        state_rows=_wrapped([_selection_row("nid:1")]),  # nid:ORPHAN has NO row
        proof_rows=_wrapped([_coverage("owned")]),
    )
    assert result.verdict is TotalityVerdict.INCOMPLETE
    codes = {s.code for s in result.shortfalls}
    assert TotalityShortfallCode.UNOWNED_ADDRESSABLE_NODE in codes
    # The shortfall is self-evidencing: it names the orphaned address path.
    orphan = next(s for s in result.shortfalls if s.code is TotalityShortfallCode.UNOWNED_ADDRESSABLE_NODE)
    assert "section:99" in orphan.detail


def test_typed_non_selection_reason_owns_node() -> None:
    """A node covered by a TYPED non-selection status (e.g. absent) is owned, not a gap."""
    result = compute_totality(
        base_rows=_wrapped([_address_node("nid:1", "section:1")]),
        state_rows=_wrapped([_selection_row("nid:1", status="absent")]),
        proof_rows=(),
    )
    assert result.verdict is TotalityVerdict.TOTAL
    assert result.typed_non_selection_nodes == 1
    assert result.owned_nodes == 0


def test_untyped_residual_is_incomplete() -> None:
    """A residual with no ``kind`` is a silent drop → INCOMPLETE."""
    result = compute_totality(
        base_rows=_wrapped([_address_node("nid:1", "section:1")]),
        state_rows=_wrapped([_selection_row("nid:1")]),
        proof_rows=_wrapped([_residual(None, "res:untyped")]),
    )
    assert result.verdict is TotalityVerdict.INCOMPLETE
    assert any(
        s.code is TotalityShortfallCode.UNTYPED_RESIDUAL for s in result.shortfalls
    )


def test_unclassified_coverage_remainder_is_incomplete() -> None:
    """A coverage_class outside the four closed classes → INCOMPLETE."""
    result = compute_totality(
        base_rows=_wrapped([_address_node("nid:1", "section:1")]),
        state_rows=_wrapped([_selection_row("nid:1")]),
        proof_rows=_wrapped([_coverage("mystery_class")]),
    )
    assert result.verdict is TotalityVerdict.INCOMPLETE
    assert any(
        s.code is TotalityShortfallCode.UNCLASSIFIED_COVERAGE_REMAINDER
        for s in result.shortfalls
    )


def test_no_address_tree_is_not_computed() -> None:
    """A pack with no address tree (e.g. corpus shared-leaf pack) → NOT_COMPUTED."""
    result = compute_totality(base_rows=(), state_rows=(), proof_rows=())
    assert result.verdict is TotalityVerdict.NOT_COMPUTED


# --------------------------------------------------------------------------- #
# Through the production checker path (guard liveness).                       #
# --------------------------------------------------------------------------- #


def test_totality_reaches_checker_verdict() -> None:
    """The lens is wired into ``CheckerVerdict.totality`` via the production check()."""
    pack = _pack(
        address_nodes=[_address_node("nid:1", "section:1")],
        selection_rows=[_selection_row("nid:1")],
        residuals=[_residual("source_pathology", "res:1")],
        coverage=[_coverage("owned"), _coverage("residual")],
    )
    verdict = Checker().check(pack)
    assert verdict.totality.verdict is TotalityVerdict.TOTAL_WITH_RESIDUALS
    # The totality axis is reported in the wire format.
    totality_wire = verdict.to_canonical_dict()["totality"]
    assert isinstance(totality_wire, Mapping)
    totality_wire = cast("Mapping[str, JsonValue]", totality_wire)
    assert totality_wire["verdict"] == "TOTAL_WITH_RESIDUALS"


def test_incomplete_reaches_checker_verdict() -> None:
    """An INCOMPLETE pack surfaces through the production checker (the silent-gap drill)."""
    pack = _pack(
        address_nodes=[_address_node("nid:1", "section:1"), _address_node("nid:2", "section:2")],
        selection_rows=[_selection_row("nid:1")],  # nid:2 orphaned
        residuals=[],
        coverage=[_coverage("owned")],
    )
    verdict = Checker().check(pack)
    assert verdict.totality.verdict is TotalityVerdict.INCOMPLETE
    assert verdict.totality.shortfalls
