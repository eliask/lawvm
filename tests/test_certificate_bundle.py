"""Tests for the EXPERIMENTAL one-statute certificate bundle writer.

Unit tests exercise the frozen §3.1.1 root constructors, the §5.2 status
algebra, and the §5.5 certification-status mapping synthetically. The
real-corpus tests build a bundle for 482/2024 (fixed-term with extension)
and are skipped when data/finlex.farchive is absent.

The bundle is a schema-pressure fixture: nothing here asserts or implies a
checked certificate or any VALID_* verdict (no checker exists).
"""

from __future__ import annotations

import filecmp
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from lawvm.core.write_receipt import WriteReceipt
from lawvm.tools.certificate_bundle import (
    CERTIFICATE_ROOT_PROFILE,
    D_POLICY_BINDINGS,
    PROFILE_ID,
    RECEIPT_TRANSITION_DIVERGENCE_CODE,
    SEAM_HASH_EXCLUDED_MEMBERS,
    SOURCE_ANCHOR_UNAVAILABLE_CODE,
    BundleSelfCheckError,
    BundleSpecError,
    build_diagnostic_registry_rows,
    build_disposition_matrix,
    build_policy_bindings,
    canonical_json_bytes,
    certification_status_for_row,
    compute_certificate_status,
    cross_check_transitions_against_receipts,
    disposition_matrix_root,
    leaf_hash,
    list_root,
    policy_bindings_root,
    projection_hash_view,
    projection_payload_hash,
    set_root,
    verify_bundle,
)

_CORPUS = Path("data/finlex.farchive")
_corpus_skip = pytest.mark.skipif(
    not _CORPUS.exists(),
    reason="data/finlex.farchive not present; skipping real-corpus bundle tests",
)

JsonObj = dict[str, Any]


# ---------------------------------------------------------------------------
# §3.1.1 root constructors
# ---------------------------------------------------------------------------


def test_leaf_hash_is_domain_tagged() -> None:
    obj = {"a": 1}
    assert leaf_hash("lawvm.x.v0", obj) != leaf_hash("lawvm.y.v0", obj)
    assert leaf_hash("lawvm.x.v0", obj).startswith("sha256:")


def test_canonical_json_is_sorted_ascii_compact() -> None:
    assert canonical_json_bytes({"b": "ä", "a": 1}) == b'{"a":1,"b":"\\u00e4"}'


def test_empty_list_and_set_roots_differ_per_domain_and_constructor() -> None:
    # §3.1.1: empty root = constructor over []; list vs set of the same
    # domain still differ; domains separate.
    assert list_root("lawvm.x.v0", []) != set_root("lawvm.x.v0", [])
    assert set_root("lawvm.x.v0", []) != set_root("lawvm.y.v0", [])


def test_set_root_orders_and_list_root_preserves_order() -> None:
    a = leaf_hash("lawvm.x.v0", 1)
    b = leaf_hash("lawvm.x.v0", 2)
    assert set_root("lawvm.x.v0", [a, b]) == set_root("lawvm.x.v0", [b, a])
    assert list_root("lawvm.x.v0", [a, b]) != list_root("lawvm.x.v0", [b, a])


def test_duplicate_leaves_forbidden() -> None:
    a = leaf_hash("lawvm.x.v0", 1)
    with pytest.raises(BundleSpecError):
        set_root("lawvm.x.v0", [a, a])
    with pytest.raises(BundleSpecError):
        list_root("lawvm.x.v0", [a, a])


# ---------------------------------------------------------------------------
# §3.4 projection-hash run-provenance normalization
# ---------------------------------------------------------------------------


def _seam_payloadish(engine: JsonObj) -> JsonObj:
    return {
        "schema": "lawvm.provision_state.v1",
        "provision_status": "selected",
        "statute_id": "482/2024",
        "hashes": {"derived_state_hash": "abc", "content_hash": "def"},
        "engine": engine,
    }


def test_projection_hash_invariant_to_engine_provenance() -> None:
    # §3.4: a commit/dirty-state change in the engine block MUST NOT move
    # the projection leaf hash (and hence certificate_root).
    clean = _seam_payloadish(
        {"producer": "lawvm", "git_commit": "a" * 40, "git_dirty": "false", "repository": "LawVM"}
    )
    dirty = _seam_payloadish(
        {"producer": "lawvm", "git_commit": "b" * 40, "git_dirty": "true", "repository": "wt"}
    )
    assert projection_payload_hash(clean, SEAM_HASH_EXCLUDED_MEMBERS) == projection_payload_hash(
        dirty, SEAM_HASH_EXCLUDED_MEMBERS
    )


def test_projection_hash_sensitive_to_semantic_members() -> None:
    payload = _seam_payloadish({"git_commit": "a" * 40})
    changed = dict(payload, provision_status="expired")
    assert projection_payload_hash(payload, SEAM_HASH_EXCLUDED_MEMBERS) != projection_payload_hash(
        changed, SEAM_HASH_EXCLUDED_MEMBERS
    )


def test_projection_hash_view_drops_only_excluded_members() -> None:
    payload = _seam_payloadish({"git_commit": "a" * 40})
    view = projection_hash_view(payload, SEAM_HASH_EXCLUDED_MEMBERS)
    assert "engine" not in view
    assert set(view) == set(payload) - {"engine"}
    # The view is a hash input only; the original payload keeps the engine
    # block visible (§3.4: provenance stays in the artifact, not the hash).
    assert "engine" in payload


# ---------------------------------------------------------------------------
# §5.2 status algebra and §5.5 certification mapping (synthetic)
# ---------------------------------------------------------------------------


def _residual(
    effect: str,
    *,
    code: str = "TIME.TRIGGER_COVERAGE_INCOMPLETE",
    **scope: object,
) -> JsonObj:
    return {
        "diagnostic_code": code,
        "kind": "manual_frontier",
        "profile_effect": {PROFILE_ID: effect},
        "scope": {"address": scope.get("address"), "date_range": scope.get("date_range", [None, None])},
    }


_REGISTERED = frozenset({"TIME.TRIGGER_COVERAGE_INCOMPLETE", SOURCE_ANCHOR_UNAVAILABLE_CODE})


def test_status_algebra_blocking_residue_blocks() -> None:
    assert (
        compute_certificate_status(
            residual_rows=[_residual("blocks")],
            certification_statuses=["confirmed"],
            registered_codes=_REGISTERED,
        )
        == "blocked"
    )


def test_status_algebra_qualifying_row_qualifies() -> None:
    assert (
        compute_certificate_status(
            residual_rows=[_residual("qualifies")],
            certification_statuses=["confirmed", "qualified"],
            registered_codes=_REGISTERED,
        )
        == "qualified"
    )


def test_status_algebra_clean_without_blocking_or_qualifying_rows() -> None:
    assert (
        compute_certificate_status(
            residual_rows=[_residual("permits")],
            certification_statuses=["confirmed", "confirmed"],
            registered_codes=_REGISTERED,
        )
        == "clean"
    )


def test_status_algebra_unregistered_code_forces_blocked() -> None:
    assert (
        compute_certificate_status(
            residual_rows=[_residual("permits", code="NOT.REGISTERED")],
            certification_statuses=["confirmed"],
            registered_codes=_REGISTERED,
        )
        == "blocked"
    )


def test_status_algebra_blocked_or_unknown_row_blocks() -> None:
    for status in ("blocked", "unknown"):
        assert (
            compute_certificate_status(
                residual_rows=[],
                certification_statuses=["confirmed", status],
                registered_codes=_REGISTERED,
            )
            == "blocked"
        )


def test_status_algebra_missing_required_artifact_blocks() -> None:
    assert (
        compute_certificate_status(
            residual_rows=[],
            certification_statuses=["confirmed"],
            registered_codes=_REGISTERED,
            required_artifacts_present=False,
        )
        == "blocked"
    )


def test_certification_mapping_core_statuses() -> None:
    def status_for(seam_status: str) -> str:
        return certification_status_for_row(
            seam_status,
            row_address="section:1",
            row_interval=("2020-01-01", None),
            residual_rows=[],
        )

    assert status_for("selected") == "confirmed"
    assert status_for("absent") == "confirmed"
    assert status_for("expired") == "confirmed"
    assert status_for("expiry_unverified") == "blocked"
    assert status_for("address_not_found") == "blocked"
    assert status_for("unsupported_jurisdiction") == "not_applicable"


def test_certification_mapping_qualifying_residual_overrides_selected_only() -> None:
    qualifying = [
        _residual("qualifies", address="section:1", date_range=["2020-01-01", None]),
    ]
    assert (
        certification_status_for_row(
            "selected",
            row_address="section:1",
            row_interval=("2020-06-01", "2021-01-01"),
            residual_rows=qualifying,
        )
        == "qualified"
    )
    # Address scoping: a residual on another address does not qualify the row.
    assert (
        certification_status_for_row(
            "selected",
            row_address="section:2",
            row_interval=("2020-06-01", "2021-01-01"),
            residual_rows=qualifying,
        )
        == "confirmed"
    )
    # Temporal scoping: a residual entirely after the row interval does not.
    assert (
        certification_status_for_row(
            "selected",
            row_address="section:1",
            row_interval=("2019-01-01", "2019-06-01"),
            residual_rows=qualifying,
        )
        == "confirmed"
    )
    # §5.5: expired stays confirmed (non-live) — the override text covers
    # selected/absent only.
    assert (
        certification_status_for_row(
            "expired",
            row_address="section:1",
            row_interval=("2020-06-01", None),
            residual_rows=qualifying,
        )
        == "confirmed"
    )


# ---------------------------------------------------------------------------
# §3.5 diagnostic registry manifest rows
# ---------------------------------------------------------------------------


def test_registry_rows_shape_and_invariants() -> None:
    rows = build_diagnostic_registry_rows({"allows_estimated_dates": True})
    by_code = {r["code"]: r for r in rows}
    required_keys = {
        "code",
        "canonical_semantic_code",
        "deprecated_aliases",
        "introduced_in",
        "deprecated_in",
        "role",
        "allowed_residual_kinds",
        "profile_disposition",
        "jurisdiction_scope",
        "doctrine_scope",
        "surface_language",
        "surface_lexemes",
    }
    for row in rows:
        assert required_keys <= set(row), row["code"]
        assert row["role"] in ("observation", "obligation", "violation")
        assert row["profile_disposition"][PROFILE_ID] in ("blocks", "qualifies", "permits")
    # Typed fixed-term blocking codes map to kind=expiry_unverified (§5.4).
    assert by_code["TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE"]["allowed_residual_kinds"] == [
        "expiry_unverified"
    ]
    assert by_code["TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE"]["profile_disposition"][PROFILE_ID] == "blocks"
    # Non-expiry validity prose is an observation, never expiry_unverified.
    assert (
        "expiry_unverified"
        not in by_code["TEMPORAL.NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED"]["allowed_residual_kinds"]
    )
    # §11.3 alias migration metadata survives on the renamed code.
    assert by_code["TEMPORAL.NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED"]["deprecated_aliases"] == [
        "TEMPORAL.NON_VALIDITY_VOIMASSA_SUPPRESSED"
    ]
    # The writer-local anchor-gap code is registered and qualifies.
    anchor_row = by_code[SOURCE_ANCHOR_UNAVAILABLE_CODE]
    assert anchor_row["allowed_residual_kinds"] == ["source_anchor_unavailable"]
    assert anchor_row["profile_disposition"][PROFILE_ID] == "qualifies"
    # Profile channel gate softens the gated code's disposition.
    assert by_code["TIME.ESTIMATED_EFFECTIVE_DATE"]["profile_disposition"][PROFILE_ID] == "qualifies"
    ungated = build_diagnostic_registry_rows({"allows_estimated_dates": False})
    ungated_by_code = {r["code"]: r for r in ungated}
    assert ungated_by_code["TIME.ESTIMATED_EFFECTIVE_DATE"]["profile_disposition"][PROFILE_ID] == "blocks"


# ---------------------------------------------------------------------------
# Real-corpus bundle (482/2024 — fixed-term with extension)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bundle_482(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from lawvm.tools.certificate_bundle import build_certificate_bundle

    root = tmp_path_factory.mktemp("certbundle")
    out = root / "482_2024"
    build_certificate_bundle("482/2024", out, graph_store_root=root / "provenance_graph")
    return out


def _envelope(bundle_dir: Path) -> JsonObj:
    return json.loads((bundle_dir / "certificate.json").read_text(encoding="utf-8"))


@_corpus_skip
def test_bundle_layout_complete(bundle_482: Path) -> None:
    for rel in (
        "certificate.json",
        "sources/source_artifacts.json",
        "policy/strict_profile.json",
        "policy/interpretation_policy.json",
        "policy/projection_specs.json",
        "policy/diagnostic_registry.json",
        "policy/checker_contract.json",
        "trace/certified_tree_transitions.jsonl",
        "trace/certified_tree_transitions.root",
        "materialization/base_tree.json",
        "materialization/content_blobs.jsonl",
        "materialization/state_roots.jsonl",
        "projections/seam_rows.jsonl",
        "residue/residuals.jsonl",
        "residue/findings.jsonl",
        "coverage/source_unit_coverage.jsonl",
        "coverage/potential_operation_coverage.jsonl",
    ):
        assert (bundle_482 / rel).is_file(), rel
    # Every declared source locator resolves to bundled bytes (§11.3).
    identities = json.loads(
        (bundle_482 / "sources/source_artifacts.json").read_text(encoding="utf-8")
    )
    assert identities, "no bundled sources"
    for identity in identities:
        assert (bundle_482 / identity["locator"]).is_file()


@_corpus_skip
def test_every_envelope_root_recomputes_from_bundle_contents(bundle_482: Path) -> None:
    recomputed = verify_bundle(bundle_482)
    envelope = _envelope(bundle_482)
    for name, value in envelope["roots"].items():
        assert recomputed[name] == value, name


@_corpus_skip
def test_certificate_root_and_id_recompute(bundle_482: Path) -> None:
    envelope = _envelope(bundle_482)
    envelope_without_id = {k: v for k, v in envelope.items() if k != "certificate_id"}
    root = leaf_hash("lawvm.certificate.v0.root", envelope_without_id)
    assert envelope["certificate_id"] == root
    assert envelope["certificate_id"].startswith("sha256:")


@_corpus_skip
def test_projection_universe_reconciles(bundle_482: Path) -> None:
    envelope = _envelope(bundle_482)
    # Independent recompute: fold the trace per change date, count
    # (active address, interval) pairs per §5.5 all_address_interval_states.
    checkpoints = [
        json.loads(line)
        for line in (bundle_482 / "materialization/state_roots.jsonl").read_text().splitlines()
        if line.strip()
    ]
    transitions = [
        json.loads(line)
        for line in (bundle_482 / "trace/certified_tree_transitions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    state: set[str] = set()
    universe = 0
    by_date: dict[str, list[JsonObj]] = {}
    for row in transitions:
        by_date.setdefault(row["effective_date"], []).append(row)
    for checkpoint in checkpoints:
        for row in by_date.get(checkpoint["date"], []):
            if row["action"] == "set_subtree":
                state.add(row["target_address"])
            else:
                state.discard(row["target_address"])
        universe += len(state)
    coverage = envelope["projection_coverage"]["seam"]
    assert coverage["row_count"] + coverage["omitted_row_count"] == universe
    wrappers = [
        json.loads(line)
        for line in (bundle_482 / "projections/seam_rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(wrappers) == coverage["row_count"]


@_corpus_skip
def test_seam_rows_carry_parentage_and_payload_only_hash(bundle_482: Path) -> None:
    envelope = _envelope(bundle_482)
    wrappers = [
        json.loads(line)
        for line in (bundle_482 / "projections/seam_rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    specs = json.loads((bundle_482 / "policy/projection_specs.json").read_text(encoding="utf-8"))
    excluded = specs["projections"]["seam"]["hash_excluded_members"]
    assert excluded == ["engine"]
    for wrapper in wrappers:
        parentage = wrapper["certificate"]
        assert parentage["certificate_id"] == envelope["certificate_id"]
        assert parentage["projection_schema"] == "lawvm.provision_state.v1"
        assert parentage["projection_spec_version"] == "0.2"
        # §3.4: only the payload's hash view is hashed; parentage and the
        # run-provenance engine block never feed the hash.
        assert parentage["projection_hash"] == projection_payload_hash(
            wrapper["projection_payload"], excluded
        )
        assert "certificate" not in wrapper["projection_payload"]
        # The engine block stays VISIBLE on the emitted payload (§3.4).
        assert "engine" in wrapper["projection_payload"]
        assert wrapper["certification_status"] in (
            "confirmed",
            "qualified",
            "blocked",
            "not_applicable",
        )


@_corpus_skip
def test_482_certificate_status_computed_and_blocked(bundle_482: Path) -> None:
    """482/2024 replay carries a blocking finding; the algebra must say blocked.

    The clean/qualified arms of the algebra are covered synthetically above;
    this pins that the emitted status is COMPUTED from the rows, not chosen.
    """
    envelope = _envelope(bundle_482)
    residuals = [
        json.loads(line)
        for line in (bundle_482 / "residue/residuals.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    blocking = [r for r in residuals if r["profile_effect"].get(PROFILE_ID) == "blocks"]
    assert blocking, "expected at least one blocking residual for 482/2024"
    assert envelope["certificate_status"] == "blocked"
    assert envelope["residual_summary"]["blocking_count"] == len(blocking)
    # Fixed-term machinery exercised: the work-level expires_on dates are
    # committed boundary dates (§2.1) and the post-expiry interval emits
    # confirmed non-live `expired` seam rows.
    wrappers = [
        json.loads(line)
        for line in (bundle_482 / "projections/seam_rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expired_rows = [w for w in wrappers if w["projection_payload"]["provision_status"] == "expired"]
    assert expired_rows
    assert all(w["certification_status"] == "confirmed" for w in expired_rows)
    assert all(w["projection_payload"]["version"] is None for w in expired_rows)


@_corpus_skip
def test_blocking_findings_have_residual_rows(bundle_482: Path) -> None:
    findings = [
        json.loads(line)
        for line in (bundle_482 / "residue/findings.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    residuals = [
        json.loads(line)
        for line in (bundle_482 / "residue/residuals.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    refs = {ref for r in residuals for ref in r["finding_refs"]}
    for finding in findings:
        if finding["blocking"]:
            assert finding["finding_id"] in refs, finding["diagnostic_code"]


@_corpus_skip
def test_tampered_bundle_fails_self_check(bundle_482: Path, tmp_path: Path) -> None:
    tampered = tmp_path / "tampered"
    shutil.copytree(bundle_482, tampered)
    blobs_path = tampered / "materialization/content_blobs.jsonl"
    rows = blobs_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["content_json"]["text"] = (row["content_json"].get("text") or "") + " TAMPERED"
    rows[0] = canonical_json_bytes(row).decode("ascii")
    blobs_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(BundleSelfCheckError):
        verify_bundle(tampered)


@_corpus_skip
def test_tampered_envelope_root_fails_self_check(bundle_482: Path, tmp_path: Path) -> None:
    tampered = tmp_path / "tampered_envelope"
    shutil.copytree(bundle_482, tampered)
    cert_path = tampered / "certificate.json"
    envelope = json.loads(cert_path.read_text(encoding="utf-8"))
    envelope["roots"]["residual_root"] = "sha256:" + "0" * 64
    cert_path.write_text(json.dumps(envelope, ensure_ascii=True, sort_keys=True, indent=1))
    with pytest.raises(BundleSelfCheckError):
        verify_bundle(tampered)


@_corpus_skip
@pytest.mark.slow
def test_two_runs_byte_identical(bundle_482: Path, tmp_path: Path) -> None:
    from lawvm.tools.certificate_bundle import build_certificate_bundle

    second = tmp_path / "second"
    build_certificate_bundle("482/2024", second, graph_store_root=tmp_path / "graph")
    first_files = sorted(p.relative_to(bundle_482) for p in bundle_482.rglob("*") if p.is_file())
    second_files = sorted(p.relative_to(second) for p in second.rglob("*") if p.is_file())
    assert first_files == second_files
    mismatched = [
        str(rel)
        for rel in first_files
        if not filecmp.cmp(bundle_482 / rel, second / rel, shallow=False)
    ]
    assert mismatched == []


# ---------------------------------------------------------------------------
# Build-consumption registration (taint-checkable build liveness slice)
# ---------------------------------------------------------------------------


@_corpus_skip
def test_bundle_emission_registers_taint_checkable_build(bundle_482: Path) -> None:
    """Emission writes a BuildRecord keyed by the certificate root.

    The writer consumes no manual-claim assertions today, so the record must
    still exist with consumption_instrumented=True and count=0 — that is what
    distinguishes a clean build from an uninstrumented one.
    """
    from lawvm.core.build_consumption import (
        BuildConsumptionStatus,
        BuildRef,
        query_retraction_taint_for_build_refs,
    )
    from lawvm.core.provenance_graph import ArtifactRef
    from lawvm.core.provenance_graph_storage import GraphStore

    envelope = _envelope(bundle_482)
    certificate_root = envelope["certificate_id"]
    expected_build_id = f"cert:lawvm.certificate.v0.4.1:{certificate_root}"

    store = GraphStore(bundle_482.parent / "provenance_graph")
    index = store.load_build_record_index()
    assert expected_build_id in index
    record = index[expected_build_id]
    assert record.consumption_instrumented is True
    assert record.consumed_subject_count == 0
    assert record.artifact_ref.content_hash == certificate_root

    # The persisted graph snapshot carries the build node; the four-state
    # query reports the build CLEAN (known + instrumented + zero consumption).
    snapshots = sorted((store._root / "snapshots").glob("*.json"))
    assert snapshots
    graph = store.read_graph(snapshots[-1].stem)
    build_ref = BuildRef.mint(
        build_kind="cert",
        schema="lawvm.certificate.v0.4.1",
        content_hash=certificate_root,
        artifact_ref=ArtifactRef(
            artifact_type="certificate_bundle",
            artifact_id=certificate_root,
            content_hash=certificate_root,
        ),
    )
    (finding,) = query_retraction_taint_for_build_refs(graph, (build_ref,), {}, index)
    assert finding.status == BuildConsumptionStatus.CLEAN

    # Cert-root cycle guard: the consumption record is an emission sidecar,
    # never inside the bundle (else certificate_root <-> graph cycle).
    assert not list(bundle_482.rglob("*consumed_by_build*"))


# ---------------------------------------------------------------------------
# Receipt consistency cross-check (diff-vs-receipt divergence detector)
# ---------------------------------------------------------------------------


def _receipt(op_id: str, *body_relative_addrs: str) -> WriteReceipt:
    """A WriteReceipt whose declared footprint touches the given covering units.

    Footprint TreePaths begin at the addressable body root (rendered
    ``hcontainer:/`` under the receipt address grammar), matching real replay
    receipts; the remaining segments are the body-relative covering address the
    cross-check compares against certificate transition target addresses.
    """
    def _seg(token: str) -> tuple[str, str]:
        kind, _, label = token.partition(":")
        return (kind, label)

    replaced: tuple[tuple[tuple[str, str], ...], ...] = tuple(
        (("hcontainer", ""), *(_seg(token) for token in addr.split("/")))
        for addr in body_relative_addrs
    )
    return WriteReceipt(
        op_id=op_id,
        helper="test",
        action="replace",
        bound_target_path=None,
        landed_primary_path=None,
        replaced_paths=replaced,
    )


def _transition(transition_id: str, target_address: str) -> dict[str, Any]:
    return {
        "transition_id": transition_id,
        "target_address": target_address,
        "effective_date": "2010-01-01",
    }


def test_receipt_divergence_code_is_a_non_blocking_observation() -> None:
    rows = build_diagnostic_registry_rows({})
    (row,) = [r for r in rows if r["code"] == RECEIPT_TRANSITION_DIVERGENCE_CODE]
    assert row["role"] == "observation"
    assert row["allowed_residual_kinds"] == []
    # observation -> profile permits -> never blocks or qualifies the certificate.
    assert row["profile_disposition"][PROFILE_ID] == "permits"


def test_cross_check_quiet_on_consistent_replay() -> None:
    # Transition target explained by an attributed op's receipt footprint.
    transitions = [_transition("t1", "chapter:1/section:4/subsection:3")]
    op_transitions = {"op_a": ["t1"]}
    receipts = [_receipt("op_a", "chapter:1/section:4/subsection:3")]

    divergences, notes = cross_check_transitions_against_receipts(
        transition_rows=transitions,
        op_transitions=op_transitions,
        write_receipts=receipts,
    )
    assert divergences == []
    assert notes and "0 diff-vs-receipt divergence" in notes[0]


def test_cross_check_explains_via_ancestor_and_descendant_footprints() -> None:
    # A whole-section receipt explains a subsection transition (ancestor leg);
    # a subsection receipt explains the section transition it tiles (descendant).
    transitions = [
        _transition("t_sub", "chapter:1/section:4/subsection:2"),
        _transition("t_sec", "chapter:2/section:9"),
    ]
    op_transitions = {"op_sec": ["t_sub"], "op_subleg": ["t_sec"]}
    receipts = [
        _receipt("op_sec", "chapter:1/section:4"),
        _receipt("op_subleg", "chapter:2/section:9/subsection:1"),
    ]
    divergences, _ = cross_check_transitions_against_receipts(
        transition_rows=transitions,
        op_transitions=op_transitions,
        write_receipts=receipts,
    )
    assert divergences == []


def test_cross_check_fires_on_diff_vs_receipt_divergence() -> None:
    # Transition attributes to an op whose receipt footprint touches an
    # unrelated address: genuine divergence, one NON-BLOCKING finding.
    transitions = [_transition("t1", "chapter:1/section:4/subsection:3")]
    op_transitions = {"op_a": ["t1"]}
    receipts = [_receipt("op_a", "chapter:7/section:99")]

    divergences, notes = cross_check_transitions_against_receipts(
        transition_rows=transitions,
        op_transitions=op_transitions,
        write_receipts=receipts,
    )
    assert len(divergences) == 1
    div = divergences[0]
    assert div["transition_id"] == "t1"
    assert div["target_address"] == "chapter:1/section:4/subsection:3"
    assert div["attributed_op_ids"] == ["op_a"]
    assert div["attributed_ops_with_receipts"] == 1
    assert "1 diff-vs-receipt divergence" in notes[0]


def test_cross_check_quiet_on_legitimate_zero_receipt_transition() -> None:
    # Temporary-act lapse restoration / enacted-base first materialization:
    # the covering-state transition has NO attributed op, so no receipt is
    # expected. It is accounted as zero-receipt, never flagged.
    transitions = [_transition("t_expiry", "chapter:5/section:50/subsection:1")]
    op_transitions: dict[str, list[str]] = {}  # no op attributes to t_expiry
    receipts: list[WriteReceipt] = []

    divergences, notes = cross_check_transitions_against_receipts(
        transition_rows=transitions,
        op_transitions=op_transitions,
        write_receipts=receipts,
    )
    assert divergences == []
    assert "1 legitimate zero-receipt" in notes[0]
    assert "0 diff-vs-receipt divergence" in notes[0]


# ---------------------------------------------------------------------------
# BOOT-01: policy-bindings + disposition-matrix content binding (Pro §2/§8)
# ---------------------------------------------------------------------------


def test_disposition_matrix_is_engine_derived_and_rooted() -> None:
    # The matrix derives blocks/qualifies/permits from FINDING_REGISTRY under
    # the pinned profile fields. Flipping a profile gate changes a cell -> root.
    gated = build_disposition_matrix({"allows_estimated_dates": True})
    ungated = build_disposition_matrix({"allows_estimated_dates": False})
    assert (
        gated["TIME.ESTIMATED_EFFECTIVE_DATE"][PROFILE_ID] == "qualifies"
        and ungated["TIME.ESTIMATED_EFFECTIVE_DATE"][PROFILE_ID] == "blocks"
    )
    assert disposition_matrix_root(gated) != disposition_matrix_root(ungated)


def test_registry_rows_derive_disposition_from_matrix() -> None:
    # BOOT-01 item B: a row's profile_disposition equals the engine matrix cell;
    # the row never authors its own disposition independently.
    fields = {"allows_estimated_dates": False}
    matrix = build_disposition_matrix(fields)
    rows = build_diagnostic_registry_rows(fields)
    for row in rows:
        assert row["profile_disposition"] == matrix[row["code"]]


def test_policy_bindings_binds_real_roots_and_marks_absent_honestly() -> None:
    # DUAL-01: the absent selection-profile is an explicit null, never a fake
    # hash; the bound roots are exactly the values passed.
    pb = build_policy_bindings(
        diagnostic_registry_root="sha256:" + "a" * 64,
        profile_manifest_root="sha256:" + "b" * 64,
        disposition_matrix_root="sha256:" + "c" * 64,
        source_policy_root="sha256:" + "d" * 64,
        selection_profile_root=None,
    )
    assert pb["selection_profile_root"] is None
    assert pb["diagnostic_registry_root"] == "sha256:" + "a" * 64
    assert pb["schema"] == D_POLICY_BINDINGS
    # the root is sensitive to a bound member changing.
    other = dict(pb, disposition_matrix_root="sha256:" + "0" * 64)
    assert policy_bindings_root(pb) != policy_bindings_root(other)


@_corpus_skip
def test_policy_bindings_committed_in_cert_root_set(bundle_482: Path) -> None:
    env = _envelope(bundle_482)
    assert env["certificate_root_profile"] == CERTIFICATE_ROOT_PROFILE
    assert "policy_bindings_root" in env["roots"]
    assert env["root_members"] == sorted(env["roots"])
    pb = json.loads((bundle_482 / "policy/policy_bindings.json").read_text(encoding="utf-8"))
    # bound to the ACTUAL committed manifest roots; absent input is honest null.
    assert pb["diagnostic_registry_root"] == env["artifacts"]["diagnostic_registry_manifest"]["root"]
    assert pb["profile_manifest_root"] == env["artifacts"]["profile_manifest"]["root"]
    assert pb["disposition_matrix_root"] == env["artifacts"]["disposition_matrix_manifest"]["root"]
    assert pb["source_policy_root"] == env["artifacts"]["interpretation_policy_manifest"]["root"]
    assert pb["selection_profile_root"] is None


def _reroot_after_policy_forge(bundle_dir: Path) -> None:
    """Faithfully recompute every policy/cert root after a registry/matrix edit.

    Mirrors a real attacker who rewrites a policy input AND recomputes all
    dependent roots so the bundle is INTERNALLY self-consistent. The only thing
    that must still betray the forge is the engine re-derivation in verify_bundle.
    """
    matrix_path = bundle_dir / "policy/disposition_matrix.json"
    registry_path = bundle_dir / "policy/diagnostic_registry.json"
    pb_path = bundle_dir / "policy/policy_bindings.json"
    cert_path = bundle_dir / "certificate.json"

    matrix_doc = json.loads(matrix_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    pb = json.loads(pb_path.read_text(encoding="utf-8"))
    env = json.loads(cert_path.read_text(encoding="utf-8"))

    new_matrix_root = disposition_matrix_root(matrix_doc["matrix"])
    new_registry_root = leaf_hash("lawvm.diagnostic_registry.v0", registry)
    pb["disposition_matrix_root"] = new_matrix_root
    pb["diagnostic_registry_root"] = new_registry_root
    new_pb_root = policy_bindings_root(pb)

    env["artifacts"]["disposition_matrix_manifest"]["root"] = new_matrix_root
    env["artifacts"]["diagnostic_registry_manifest"]["root"] = new_registry_root
    env["artifacts"]["policy_bindings_manifest"]["root"] = new_pb_root
    env["roots"]["policy_bindings_root"] = new_pb_root

    matrix_path.write_text(json.dumps(matrix_doc, sort_keys=True), encoding="utf-8")
    registry_path.write_text(json.dumps(registry, sort_keys=True), encoding="utf-8")
    pb_path.write_text(json.dumps(pb, sort_keys=True), encoding="utf-8")

    env.pop("certificate_id", None)
    new_cert_root = leaf_hash("lawvm.certificate.v0.root", env)
    env["certificate_id"] = new_cert_root
    cert_path.write_text(json.dumps(env, sort_keys=True), encoding="utf-8")

    # A complete attacker also re-points the seam parentage at the new cert root,
    # so the bundle is FULLY internally consistent; the engine re-derivation is
    # then the SOLE remaining betrayal.
    seam_path = bundle_dir / "projections/seam_rows.jsonl"
    wrappers = [
        json.loads(line)
        for line in seam_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    for wrapper in wrappers:
        wrapper["certificate"]["certificate_id"] = new_cert_root
        wrapper["certificate"]["certificate_root"] = new_cert_root
    seam_path.write_text(
        "\n".join(canonical_json_bytes(w).decode("ascii") for w in wrappers) + "\n",
        encoding="utf-8",
    )


@_corpus_skip
def test_forged_registry_disposition_caught(bundle_482: Path, tmp_path: Path) -> None:
    # THE load-bearing BOOT-01 drill: a residual kind blocks under the original
    # registry; rewrite registry+matrix so that kind PERMITS; recompute every
    # dependent root (so the bundle is internally consistent); verify_bundle MUST
    # still fail because the disposition is re-derived from the engine.
    forged = tmp_path / "forged_registry"
    shutil.copytree(bundle_482, forged)

    env = _envelope(forged)
    residuals = [
        json.loads(line)
        for line in (forged / "residue/residuals.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    blocking_codes = sorted(
        {r["diagnostic_code"] for r in residuals if r["profile_effect"].get(PROFILE_ID) == "blocks"}
    )
    assert blocking_codes, "fixture must carry a blocking residual to forge"
    victim = blocking_codes[0]

    # Forge the matrix cell + registry row for the victim: blocks -> permits.
    matrix_path = forged / "policy/disposition_matrix.json"
    matrix_doc = json.loads(matrix_path.read_text(encoding="utf-8"))
    assert matrix_doc["matrix"][victim][PROFILE_ID] == "blocks"
    matrix_doc["matrix"][victim][PROFILE_ID] = "permits"
    matrix_path.write_text(json.dumps(matrix_doc, sort_keys=True), encoding="utf-8")

    registry_path = forged / "policy/diagnostic_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    for row in registry["rows"]:
        if row["code"] == victim:
            row["profile_disposition"][PROFILE_ID] = "permits"
    registry_path.write_text(json.dumps(registry, sort_keys=True), encoding="utf-8")

    _reroot_after_policy_forge(forged)

    with pytest.raises(BundleSelfCheckError) as exc:
        verify_bundle(forged)
    assert "BOOT-01" in str(exc.value)


@_corpus_skip
def test_forged_registry_without_reroot_also_caught(bundle_482: Path, tmp_path: Path) -> None:
    # Even a lazy attacker who edits the matrix but does NOT recompute roots is
    # caught (the engine re-derivation disagrees before any root check).
    forged = tmp_path / "forged_lazy"
    shutil.copytree(bundle_482, forged)
    matrix_path = forged / "policy/disposition_matrix.json"
    matrix_doc = json.loads(matrix_path.read_text(encoding="utf-8"))
    victim = next(
        c for c, cells in matrix_doc["matrix"].items() if cells[PROFILE_ID] == "blocks"
    )
    matrix_doc["matrix"][victim][PROFILE_ID] = "permits"
    matrix_path.write_text(json.dumps(matrix_doc, sort_keys=True), encoding="utf-8")
    with pytest.raises(BundleSelfCheckError):
        verify_bundle(forged)


@_corpus_skip
def test_row_authored_blocking_forged_derives_from_registry(
    bundle_482: Path, tmp_path: Path
) -> None:
    # Flip a residual row's cached profile_effect blocks->permits WITHOUT touching
    # the engine policy. verify_bundle derives the disposition from the engine, so
    # the tampered row field is rejected (the verdict is registry/engine-derived,
    # not row-authored).
    forged = tmp_path / "row_forged"
    shutil.copytree(bundle_482, forged)
    res_path = forged / "residue/residuals.jsonl"
    lines = [line for line in res_path.read_text(encoding="utf-8").splitlines() if line]
    rows = [json.loads(line) for line in lines]
    target = next(
        i for i, r in enumerate(rows) if r["profile_effect"].get(PROFILE_ID) == "blocks"
    )
    rows[target]["profile_effect"][PROFILE_ID] = "permits"
    res_path.write_text(
        "\n".join(canonical_json_bytes(r).decode("ascii") for r in rows) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(BundleSelfCheckError) as exc:
        verify_bundle(forged)
    assert "BOOT-01" in str(exc.value)
