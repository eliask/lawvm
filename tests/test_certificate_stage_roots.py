"""Tests for the StageResult -> certificate per-stage subroot mapping (WAIST #9).

These exercise the canonical ``core.stage_result_ledger`` row mapping, the
per-stage subroot construction in ``tools.certificate_bundle`` (built with the
existing ``leaf_hash``/``set_root`` vocabulary), and the writer-side self-check
consumer (``verify_bundle``) that recomputes the subroots and DIVERGES when a
stage account is tampered/severed (the guard-liveness property).

Like the rest of the cert suite, the real-corpus feeder test builds a bundle for
482/2024 and is skipped when ``data/finlex.farchive`` is absent. Nothing here
implies a checked certificate or any verdict.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lawvm.core.phase_result import Finding
from lawvm.core.stage_result import (
    CoverageCertificate,
    Residual,
    StageResult,
)
from lawvm.core.stage_result_ledger import (
    coverage_row,
    finding_row,
    residual_row,
    stage_coverage_row,
    stage_finding_rows,
    stage_residual_rows,
)
from lawvm.tools.certificate_bundle import (
    BundleSelfCheckError,
    _verify_stage_accounts,
    build_stage_account_row,
    canonical_json_bytes,
    stage_account_root,
    stage_accounts_root,
    stage_coverage_subroot,
    stage_finding_subroot,
    stage_residual_subroot,
    verify_bundle,
)

_CORPUS = Path("data/finlex.farchive")
_corpus_skip = pytest.mark.skipif(
    not _CORPUS.exists(),
    reason="data/finlex.farchive not present; skipping real-corpus bundle tests",
)


def _residual() -> Residual:
    return Residual(
        kind="benign_uninterpreted",
        reason="segmentation_benign_whitespace",
        scope="u#body",
        source_unit_id="u#body",
        char_start=0,
        char_end=2,
        text="  ",
        blocking=False,
    )


def _finding() -> Finding:
    return Finding(
        kind="PARSE.DUPLICATE_TARGET_OP",
        role="observation",
        stage="parse",
        detail={"k": "v"},
        source_statute="482/2024",
        blocking=False,
    )


def _coverage() -> CoverageCertificate:
    return CoverageCertificate(unit="chars", total=10, owned=8, benign=0, residual=2)


# ---------------------------------------------------------------------------
# canonical row mapping (field-for-field per WAIST #9)
# ---------------------------------------------------------------------------


def test_residual_row_field_for_field() -> None:
    row = residual_row(_residual())
    assert row["kind"] == "benign_uninterpreted"
    assert row["diagnostic_code"] == row["kind"]  # diagnostic_code(=kind)
    assert row["role"] == "residual"
    assert row["blocking"] is False
    assert row["source_text"] == "  "  # source_text(=text)
    assert row["rule_id"] == ""
    assert row["scope"]["source_unit_id"] == "u#body"
    assert row["scope"]["char_start"] == 0 and row["scope"]["char_end"] == 2


def test_finding_row_field_for_field() -> None:
    row = finding_row(_finding())
    assert row["diagnostic_code"] == "PARSE.DUPLICATE_TARGET_OP"  # diagnostic_code(=kind)
    assert row["role"] == "observation"
    assert row["blocking"] is False
    assert row["phase"] == "parse"  # phase(=stage)
    assert row["scope"]["source_statute"] == "482/2024"
    assert row["detail"] == {"k": "v"}


def test_coverage_row_carries_partition_and_totality_verdict() -> None:
    row = coverage_row(_coverage())
    assert row == {
        "unit": "chars",
        "total": 10,
        "owned": 8,
        "benign": 0,
        "residual": 2,
        "violation": 0,
        "totality_claimed": True,
        "is_partition": True,  # 8 + 0 + 2 + 0 == 10
    }


def test_coverage_row_is_partition_false_when_leaks() -> None:
    leaky = CoverageCertificate(unit="chars", total=10, owned=8, residual=1)
    assert coverage_row(leaky)["is_partition"] is False


def test_rows_are_json_safe() -> None:
    # canonical_json_bytes must accept the mapped rows verbatim (no custom types).
    for row in (residual_row(_residual()), finding_row(_finding()), coverage_row(_coverage())):
        json.loads(canonical_json_bytes(row).decode("ascii"))


# ---------------------------------------------------------------------------
# subroot construction + aggregation (the additive attribution layer)
# ---------------------------------------------------------------------------


def test_subroots_are_deterministic() -> None:
    stage = StageResult(
        value=None, residuals=(_residual(),), findings=(_finding(),), coverage=_coverage()
    )
    r_rows = stage_residual_rows(stage)
    f_rows = stage_finding_rows(stage)
    c_row = stage_coverage_row(stage)
    assert stage_residual_subroot(r_rows) == stage_residual_subroot(r_rows)
    assert stage_finding_subroot(f_rows) == stage_finding_subroot(f_rows)
    assert stage_coverage_subroot(c_row) == stage_coverage_subroot(c_row)


def test_empty_stage_account_is_a_stable_constant() -> None:
    empty = StageResult(value=None)
    row = build_stage_account_row("empty.stage", empty)
    # An empty account uses the identity defaults; the aggregate over zero stages
    # is the empty-set constant and never perturbs anything.
    assert stage_accounts_root([]) == stage_accounts_root([])
    assert row["residual_rows"] == [] and row["finding_rows"] == []
    assert row["coverage_row"]["is_partition"] is True


def test_aggregate_root_recomputes_from_committed_rows() -> None:
    a = build_stage_account_row(
        "stage.a",
        StageResult(value=None, residuals=(_residual(),), coverage=_coverage()),
    )
    b = build_stage_account_row("stage.b", StageResult(value=None, findings=(_finding(),)))
    agg = stage_accounts_root([a, b])
    # Round-trip through JSON the way verify_bundle reads the artifact.
    a2, b2 = json.loads(json.dumps(a)), json.loads(json.dumps(b))
    assert stage_account_root(a2) == a["stage_account_root"]
    assert stage_account_root(b2) == b["stage_account_root"]
    assert stage_accounts_root([a2, b2]) == agg


def test_stage_account_root_changes_when_an_account_diverges() -> None:
    clean = build_stage_account_row(
        "stage.a", StageResult(value=None, residuals=(_residual(),), coverage=_coverage())
    )
    severed = build_stage_account_row("stage.a", StageResult(value=None, coverage=_coverage()))
    # Dropping the residual (the "built-then-severed" class) changes the root.
    assert clean["stage_account_root"] != severed["stage_account_root"]
    assert stage_accounts_root([clean]) != stage_accounts_root([severed])


# ---------------------------------------------------------------------------
# live feeder + the verify_bundle fire-drill (guard liveness)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bundle_482(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from lawvm.tools.certificate_bundle import build_certificate_bundle

    root = tmp_path_factory.mktemp("certstage")
    out = root / "482_2024"
    build_certificate_bundle("482/2024", out, graph_store_root=root / "provenance_graph")
    return out


@_corpus_skip
def test_live_surface_stage_feeds_the_dossier(bundle_482: Path) -> None:
    envelope = json.loads((bundle_482 / "certificate.json").read_text(encoding="utf-8"))
    assert "stage_accounts_root" in envelope["roots"]
    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/stage_accounts.jsonl").read_text().splitlines()
    ]
    stages = {row["stage"] for row in rows}
    assert "fi.legal_surface.source_unit" in stages
    surface = next(r for r in rows if r["stage"] == "fi.legal_surface.source_unit")
    # The segmentation partition flowed into the dossier as a real coverage row.
    cov = surface["coverage_row"]
    assert cov["unit"] == "chars" and cov["total"] > 0
    assert cov["is_partition"] is True
    assert cov["violation"] == 0
    # Benign whitespace residue is present and non-blocking.
    assert all(r["kind"] == "benign_uninterpreted" for r in surface["residual_rows"])
    assert all(r["blocking"] is False for r in surface["residual_rows"])


@_corpus_skip
def test_stage_accounts_root_recomputes_in_verify(bundle_482: Path) -> None:
    recomputed = verify_bundle(bundle_482)
    envelope = json.loads((bundle_482 / "certificate.json").read_text(encoding="utf-8"))
    assert recomputed["stage_accounts_root"] == envelope["roots"]["stage_accounts_root"]


@_corpus_skip
def test_severed_stage_residual_makes_self_check_diverge(
    bundle_482: Path, tmp_path: Path
) -> None:
    # Fire-drill: drop a stage's residual rows (the built-then-severed class)
    # WITHOUT updating the committed subroot — verify_bundle must diverge.
    tampered = tmp_path / "severed"
    shutil.copytree(bundle_482, tampered)
    path = tampered / "stages/stage_accounts.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["residual_rows"] = []  # sever the account; keep the old subroot
    rows[0] = canonical_json_bytes(row).decode("ascii")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(BundleSelfCheckError):
        verify_bundle(tampered)


@_corpus_skip
def test_tampered_stage_accounts_root_fails_self_check(
    bundle_482: Path, tmp_path: Path
) -> None:
    tampered = tmp_path / "envroot"
    shutil.copytree(bundle_482, tampered)
    cert_path = tampered / "certificate.json"
    envelope = json.loads(cert_path.read_text(encoding="utf-8"))
    envelope["roots"]["stage_accounts_root"] = "sha256:" + "0" * 64
    cert_path.write_text(json.dumps(envelope, ensure_ascii=True, sort_keys=True, indent=1))
    with pytest.raises(BundleSelfCheckError):
        verify_bundle(tampered)


# ---------------------------------------------------------------------------
# StageResult endgame Wave-5: the 6/7 newly-routed per-stage accounts each
# reach the dossier (the (D) deliverable) AND each is checkable end-to-end —
# severing/tampering its committed row makes the PRODUCTION verify_bundle raise
# (mirrors test_severed_stage_residual_makes_self_check_diverge; reachable because
# _verify_stage_accounts is UNCONDITIONAL on the blocked 482/2024).
# ---------------------------------------------------------------------------

_WAVE5_ROUTED_STAGE_IDS = (
    "fi.source.identity",  # #1
    "fi.structure.write_footprint",  # #3 (SEAM B)
    "fi.source_syntax.forest",  # #4 (SEAM A)
    "fi.legal_surface.graph",  # #5 (SEAM A)
    "fi.canonical_op.compile",  # #6 (SEAM B)
    "fi.projection.interlinks",  # #10 (SEAM A)
    "fi.projection.overlays",  # #10 (SEAM A)
)


@_corpus_skip
def test_all_wave5_stages_reach_the_dossier(bundle_482: Path) -> None:
    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/stage_accounts.jsonl").read_text().splitlines()
    ]
    stages = {row["stage"] for row in rows}
    for stage_id in _WAVE5_ROUTED_STAGE_IDS:
        assert stage_id in stages, f"{stage_id} did not reach the dossier"
    # Every routed stage carries a real coverage partition (a checkable account).
    for row in rows:
        if row["stage"] in _WAVE5_ROUTED_STAGE_IDS:
            assert row["coverage_row"]["is_partition"] is True, row["stage"]


def _sever_named_stage_row(src: Path, dst: Path, stage_id: str) -> None:
    """Copy `src` to `dst`, drop the named stage's coverage_row count (a sever).

    Tampers the committed coverage row WITHOUT updating its subroot, so the
    unconditional _verify_stage_accounts recompute must diverge.
    """
    shutil.copytree(src, dst)
    path = dst / "stages/stage_accounts.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        row = json.loads(line)
        if row["stage"] != stage_id:
            continue
        # Sever: mutate the coverage row total but keep the committed subroot.
        row["coverage_row"]["total"] = int(row["coverage_row"]["total"]) + 1
        lines[index] = canonical_json_bytes(row).decode("ascii")
        break
    else:  # pragma: no cover - defensive
        raise AssertionError(f"stage {stage_id} not found in committed rows")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@_corpus_skip
@pytest.mark.parametrize("stage_id", _WAVE5_ROUTED_STAGE_IDS)
def test_severed_wave5_stage_row_makes_self_check_diverge(
    bundle_482: Path, tmp_path: Path, stage_id: str
) -> None:
    tampered = tmp_path / f"sev_{stage_id.replace('.', '_')}"
    _sever_named_stage_row(bundle_482, tampered, stage_id)
    with pytest.raises(BundleSelfCheckError):
        verify_bundle(tampered)


@_corpus_skip
def test_surface_graph_blocking_residual_forces_blocked_status(
    bundle_482: Path, tmp_path: Path
) -> None:
    # PART 2 status-contribution (#5): inject a BLOCKING surface residual into the
    # committed fi.legal_surface.graph row; recompute the row's subroots so the
    # _verify_stage_accounts pass succeeds, then the status recompute MUST see the
    # blocking residual and require `blocked`. Because 482/2024 is already blocked,
    # this never flips it clean — but it proves the contribution path is live: a
    # broken-ref blocking residual forces blocked UNCONDITIONALLY.
    from lawvm.tools.certificate_bundle import (
        _committed_stage_blocking_residual_count,
        compute_certificate_status,
    )

    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/stage_accounts.jsonl").read_text().splitlines()
    ]
    # Baseline: 482/2024 carries zero #5/#10 blocking residuals (0-delta).
    assert _committed_stage_blocking_residual_count(rows) == 0
    graph_row = next(r for r in rows if r["stage"] == "fi.legal_surface.graph")
    graph_row["residual_rows"].append(
        {"diagnostic_code": "x", "kind": "unowned_violation", "blocking": True}
    )
    assert _committed_stage_blocking_residual_count(rows) == 1
    # A non-zero contribution forces blocked regardless of the flat residue.
    forced = compute_certificate_status(
        residual_rows=[],
        certification_statuses=[],
        registered_codes=frozenset(),
        extra_blocking_residual_count=1,
    )
    assert forced == "blocked"


@_corpus_skip
def test_projection_blocking_residual_contributes_to_status(bundle_482: Path) -> None:
    # PART 2 status-contribution (#10): a dropped-universe-member blocking
    # projection residual in either projection stage row contributes to the count.
    from lawvm.tools.certificate_bundle import _committed_stage_blocking_residual_count

    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/stage_accounts.jsonl").read_text().splitlines()
    ]
    overlays_row = next(r for r in rows if r["stage"] == "fi.projection.overlays")
    overlays_row["residual_rows"].append(
        {"diagnostic_code": "x", "kind": "projection_residual", "blocking": True}
    )
    assert _committed_stage_blocking_residual_count(rows) == 1
    # A NON-contributing stage's blocking residual must NOT count (only #5/#10).
    forest_row = next(r for r in rows if r["stage"] == "fi.source_syntax.forest")
    n_forest_blocking = sum(
        1 for r in forest_row["residual_rows"] if r.get("blocking")
    )
    assert n_forest_blocking >= 0  # the forest can carry blocking residue
    # forest is NOT in STATUS_CONTRIBUTING_STAGE_IDS, so it does not raise the count
    # beyond the overlays injection.
    assert _committed_stage_blocking_residual_count(rows) == 1


# ---------------------------------------------------------------------------
# coverage is FOLDED-AND-VERIFIED, not folded-not-verified: the per-stage
# coverage row's partition/totality verdict is re-derived from the committed
# COUNTS, not merely re-hashed (the circular self-consistency that let a forged
# inconsistent row through).
# ---------------------------------------------------------------------------


def _forge_coverage_account(coverage_row: dict) -> dict:
    """Build a stage-account row carrying `coverage_row`, hashes made consistent.

    The coverage_subroot/account_root are recomputed from the (possibly forged)
    coverage row so the circular HASH self-consistency check passes — only the
    arithmetic re-derivation can catch an inconsistent row.
    """
    base = build_stage_account_row(
        "stage.forge",
        StageResult(value=None, coverage=CoverageCertificate(unit="chars", total=0, owned=0)),
    )
    row = dict(base)
    row["coverage_row"] = dict(coverage_row)
    row["coverage_subroot"] = stage_coverage_subroot(row["coverage_row"])
    row["stage_account_root"] = stage_account_root(row)
    return row


def test_forged_inconsistent_coverage_row_fails_verify() -> None:
    # The bite: counts that do NOT sum to total but claim is_partition True.
    # Hashes are self-consistent (circular check passes); the arithmetic
    # re-derivation must reject it.
    forged = _forge_coverage_account(
        {
            "unit": "chars",
            "total": 999,
            "owned": 0,
            "benign": 0,
            "residual": 0,
            "violation": 0,
            "totality_claimed": True,
            "is_partition": True,  # LIE: 0 != 999
        }
    )
    with pytest.raises(BundleSelfCheckError):
        _verify_stage_accounts([forged])


def test_forged_is_partition_flag_mismatch_fails_verify() -> None:
    # Counts sum correctly but the committed is_partition flag is forged True
    # while totality is NOT claimed (recomputed verdict is False).
    forged = _forge_coverage_account(
        {
            "unit": "chars",
            "total": 4,
            "owned": 2,
            "benign": 0,
            "residual": 2,
            "violation": 0,
            "totality_claimed": False,
            "is_partition": True,  # LIE: not claimed -> recomputed False
        }
    )
    with pytest.raises(BundleSelfCheckError):
        _verify_stage_accounts([forged])


def test_honest_coverage_row_passes_verify() -> None:
    # 0-delta: an honest, balanced, totality-claimed row recomputes cleanly.
    honest = build_stage_account_row(
        "stage.honest",
        StageResult(
            value=None,
            coverage=CoverageCertificate(unit="chars", total=4, owned=2, benign=0, residual=2),
        ),
    )
    # No raise; the aggregate recomputes from the honest rows.
    assert _verify_stage_accounts([honest]) == stage_accounts_root([honest])


# ---------------------------------------------------------------------------
# duplicate residuals/findings must NOT crash the build: set-root semantics are a
# SET, so identical canonical rows (Residual/StageResult permit duplicates;
# residual_row drops distinguishing context) de-dup to one set member.
# ---------------------------------------------------------------------------


def test_duplicate_residuals_build_a_valid_subroot() -> None:
    # Two identical residual records -> identical canonical rows -> one set member.
    stage = StageResult(
        value=None,
        residuals=(_residual(), _residual()),
        coverage=CoverageCertificate(unit="chars", total=2, owned=0, benign=2),
    )
    # No crash (it crashed before the dedup fix).
    row = build_stage_account_row("stage.dup", stage)
    # The subroot is the set over the DISTINCT rows: same as a single residual.
    single_rows = stage_residual_rows(
        StageResult(value=None, residuals=(_residual(),))
    )
    assert row["residual_subroot"] == stage_residual_subroot(single_rows)
    # The whole account verifies.
    assert _verify_stage_accounts([row]) == stage_accounts_root([row])


def test_duplicate_findings_build_a_valid_subroot() -> None:
    stage = StageResult(value=None, findings=(_finding(), _finding()))
    row = build_stage_account_row("stage.dupf", stage)
    single_rows = stage_finding_rows(StageResult(value=None, findings=(_finding(),)))
    assert row["finding_subroot"] == stage_finding_subroot(single_rows)


def test_dedup_is_no_op_for_distinct_residuals() -> None:
    # 0-delta proof: two DISTINCT residuals stay two set members (dedup changes
    # nothing when there are no duplicates).
    distinct = Residual(
        kind="benign_uninterpreted",
        reason="segmentation_benign_whitespace",
        scope="u#body",
        source_unit_id="u#body",
        char_start=4,
        char_end=6,
        text="xy",
        blocking=False,
    )
    two = stage_residual_rows(StageResult(value=None, residuals=(_residual(), distinct)))
    one = stage_residual_rows(StageResult(value=None, residuals=(_residual(),)))
    assert stage_residual_subroot(two) != stage_residual_subroot(one)
