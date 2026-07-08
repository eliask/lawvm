"""Tests for the cross-jurisdiction invariant-parity audit (registry XP-06).

Asserts:
* the matrix is built from REAL ApplyProfile registrations (not prose) — the
  AST scan reads the live ``boundary_mode``/resolver/``occupancy_mode`` literals,
  proven by mutating-the-expectation-tracks-the-source style checks;
* the matrix is TOTAL over the known frontends (every frontend × every gate);
* the EE-occupancy-block-vs-others divergence is surfaced as a typed
  ``INVARIANT_COVERAGE_DIVERGENCE`` row;
* the finding code is registered at role=observation (non-blocking).
"""

from __future__ import annotations

import ast
from pathlib import Path

from lawvm.core.cross_jurisdiction_parity import (
    CARRIER_PRESENCES,
    INVARIANT_COVERAGE_DIVERGENCE_CODE,
    KNOWN_FRONTENDS,
    PER_UNIT_INVARIANTS,
    InvariantCoverageDivergence,
    ParityMatrix,
    build_parity_matrix,
    build_report,
    classify_divergences,
    render_matrix,
)
from lawvm.core.observation_registry import get_finding_spec, is_registered_finding_kind


# ── The matrix is built from real registrations + total ───────────────────────


def test_matrix_is_total_over_known_frontends() -> None:
    matrix = build_parity_matrix()
    assert isinstance(matrix, ParityMatrix)
    assert matrix.is_total()
    # Every known frontend has a row.
    assert set(matrix.rows) == set(KNOWN_FRONTENDS)
    # Every row carries a mode for every per-unit invariant + every carrier.
    for fe in KNOWN_FRONTENDS:
        gates = matrix.rows[fe]
        assert set(gates.modes) == set(PER_UNIT_INVARIANTS)
        assert set(gates.carriers) == set(CARRIER_PRESENCES)
        for mode in gates.modes.values():
            assert mode in ("block", "observe", "off", "absent")
        assert gates.same_moment_path in (
            "op_ordering",
            "shared_generic_effect_adapter",
            "timeline_plane",
            "absent",
        )


def test_matrix_reference_is_fi_upper_bound() -> None:
    matrix = build_parity_matrix()
    assert matrix.reference == "fi"
    # FI is the upper bound: blocks on all four per-unit gates.
    for inv in PER_UNIT_INVARIANTS:
        assert matrix.rows["fi"].modes[inv] == "block"


def _read_apply_profile_keywords(source: Path, kw_name: str) -> str | None:
    """Read a keyword string-literal directly from the ApplyProfile(...) site.

    Independent re-implementation of the production scan, used to PROVE the
    matrix reflects the real source (not a hard-coded table): if someone edits
    the profile, this and the matrix move together.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (
            fn.id if isinstance(fn, ast.Name)
            else fn.attr if isinstance(fn, ast.Attribute)
            else None
        )
        if name != "ApplyProfile":
            continue
        for kw in node.keywords:
            if kw.arg == kw_name and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
    return None


def test_matrix_reflects_real_ee_profile_source() -> None:
    """EE's matrix row must mirror the literals in estonia/grafter.py verbatim."""
    repo_root = Path(__file__).resolve().parents[1]
    ee_grafter = repo_root / "src" / "lawvm" / "estonia" / "grafter.py"

    # The real registration says occupancy_mode="block" + boundary_mode="block":
    # EE flipped LS-01 to block after closing its boundary declaration artifact
    # and measuring its corpus boundary-clean (the SECOND enforcing apply-seam
    # gate, after LS-03 occupancy).
    assert _read_apply_profile_keywords(ee_grafter, "occupancy_mode") == "block"
    assert _read_apply_profile_keywords(ee_grafter, "boundary_mode") == "block"

    matrix = build_parity_matrix()
    # And the matrix agrees — proving it is derived from the source, not prose.
    assert matrix.rows["ee"].modes["LS-03"] == "block"
    assert matrix.rows["ee"].modes["LS-01"] == "block"


def test_matrix_reflects_real_no_profile_source() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    no_grafter = repo_root / "src" / "lawvm" / "norway" / "grafter.py"
    assert _read_apply_profile_keywords(no_grafter, "boundary_mode") == "off"

    matrix = build_parity_matrix()
    # NO models no occupancy -> off; EE is block. This is the real divergence.
    assert matrix.rows["no"].modes["LS-03"] == "off"
    assert matrix.rows["ee"].modes["LS-03"] == "block"


def test_matrix_models_nz_as_frontend_without_apply_seam() -> None:
    matrix = build_parity_matrix()
    nz = matrix.rows["nz"]

    assert set(nz.modes.values()) == {"absent"}
    assert nz.same_moment_path == "op_ordering"
    assert nz.carriers["same_moment_detector"] is True
    assert nz.carriers["write_receipt"] is True


def test_matrix_models_us_dry_run_conserved_account_carrier() -> None:
    matrix = build_parity_matrix()
    us = matrix.rows["us"]

    assert us.carriers["conserved_wrapper"] is True
    assert not [
        d
        for d in classify_divergences(matrix)
        if d.invariant == "conserved_wrapper"
    ]


# ── The EE-occupancy divergence is surfaced ───────────────────────────────────


def test_ee_occupancy_block_vs_others_divergence_surfaced() -> None:
    matrix = build_parity_matrix()
    divergences = classify_divergences(matrix)

    ls03 = [d for d in divergences if d.invariant == "LS-03"]
    d = next(
        d
        for d in ls03
        if d.kind == "enforced-here" and d.divergent_frontends == ("ee",)
    )
    assert isinstance(d, InvariantCoverageDivergence)
    # EE is the enforcing outlier.
    assert d.kind == "enforced-here"
    assert d.divergent_frontends == ("ee",)
    assert d.mode_map["ee"] == "block"
    # The siblings that model no occupancy stay off.
    assert d.mode_map["no"] == "off"
    assert d.mode_map["se"] == "off"
    assert d.mode_map["eu"] == "off"
    assert d.mode_map["nz"] == "absent"
    # The rationale classifies this as a justified jurisdiction difference.
    assert "JUSTIFIED" in d.rationale
    assert d.finding_code == INVARIANT_COVERAGE_DIVERGENCE_CODE

    nz_absent = next(
        d
        for d in ls03
        if d.kind == "absent-here" and d.divergent_frontends == ("nz",)
    )
    assert nz_absent.baseline_mode == "off"
    assert nz_absent.mode_map["nz"] == "absent"


def test_ee_boundary_block_vs_others_divergence_surfaced() -> None:
    """EE's LS-01 boundary block (the second enforcing gate) is a typed row."""
    matrix = build_parity_matrix()
    divergences = classify_divergences(matrix)

    ls01 = [d for d in divergences if d.invariant == "LS-01"]
    d = next(
        d
        for d in ls01
        if d.kind == "enforced-here" and d.divergent_frontends == ("ee",)
    )
    assert isinstance(d, InvariantCoverageDivergence)
    # EE is the enforcing outlier; the siblings stay off (seam observes).
    assert d.kind == "enforced-here"
    assert d.divergent_frontends == ("ee",)
    assert d.mode_map["ee"] == "block"
    assert d.mode_map["no"] == "off"
    assert d.mode_map["uk"] == "off"
    assert d.mode_map["nz"] == "absent"
    # Justified, measured flip — not a uniform-flip mandate.
    assert "JUSTIFIED" in d.rationale
    assert d.finding_code == INVARIANT_COVERAGE_DIVERGENCE_CODE

    nz_absent = next(
        d
        for d in ls01
        if d.kind == "absent-here" and d.divergent_frontends == ("nz",)
    )
    assert nz_absent.baseline_mode == "off"
    assert nz_absent.mode_map["nz"] == "absent"


def test_divergences_cover_all_per_unit_invariants_and_carrier_gaps() -> None:
    matrix = build_parity_matrix()
    divergences = classify_divergences(matrix)
    invariants_seen = {d.invariant for d in divergences}

    # Every per-unit gate diverges from the FI upper bound (FI blocks; siblings
    # observe/off), so all four appear.
    for inv in PER_UNIT_INVARIANTS:
        assert inv in invariants_seen, f"{inv} divergence missing"

    # EE and NZ both now emit per-op receipts, so there is no write_receipt
    # carrier divergence.
    write_receipt = [d for d in divergences if d.invariant == "write_receipt"]
    assert not write_receipt
    # US now exposes a dry-run section-surface conserved account, so the
    # conserved carrier is present across all sibling frontends.
    conserved = [d for d in divergences if d.invariant == "conserved_wrapper"]
    assert not conserved


def test_same_moment_path_kinds_preserve_phase_boundary() -> None:
    """UK shares the algorithm at effect-plane, not by inventing op ids."""
    matrix = build_parity_matrix()
    paths = matrix.same_moment_paths()

    assert paths["fi"] == "timeline_plane"
    assert paths["uk"] == "shared_generic_effect_adapter"
    for frontend in ("no", "se", "ee", "eu", "nz", "us"):
        assert paths[frontend] == "op_ordering"

    carrier = matrix.carriers_for("same_moment_detector")
    assert carrier["fi"] is False
    assert carrier["uk"] is True
    for frontend in ("no", "se", "ee", "eu", "nz", "us"):
        assert carrier[frontend] is True


def test_every_divergence_uses_the_registered_finding_code() -> None:
    matrix = build_parity_matrix()
    for d in classify_divergences(matrix):
        assert d.finding_code == INVARIANT_COVERAGE_DIVERGENCE_CODE


# ── The finding code is registered (role=observation, non-blocking) ───────────


def test_finding_code_registered_as_observation() -> None:
    assert is_registered_finding_kind(INVARIANT_COVERAGE_DIVERGENCE_CODE)
    spec = get_finding_spec(INVARIANT_COVERAGE_DIVERGENCE_CODE)
    assert spec is not None
    # Read-mostly analysis: never an authority, never blocking.
    assert spec.role == "observation"
    assert spec.is_observation
    assert spec.default_enforcement == "warn"


# ── The report renders ────────────────────────────────────────────────────────


def test_render_matrix_includes_every_frontend_and_invariant() -> None:
    matrix = build_parity_matrix()
    rendered = render_matrix(matrix)
    for fe in KNOWN_FRONTENDS:
        assert fe.upper() in rendered
    for inv in PER_UNIT_INVARIANTS:
        assert inv in rendered
    assert "effect-adapter" in rendered
    assert "op-order" in rendered
    assert "timeline" in rendered


def test_build_report_runs_and_mentions_divergences() -> None:
    report = build_report()
    assert "INVARIANT_COVERAGE_DIVERGENCE rows:" in report
    assert "LS-03" in report
    assert "occupancy" in report.lower()
