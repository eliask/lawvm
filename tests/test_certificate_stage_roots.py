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
