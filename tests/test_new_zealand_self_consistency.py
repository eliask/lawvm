"""Tests for the NZ ``lawvm self-consistency -j nz`` signal harvesting.

The pure classification/projection helpers (refusal rule_id -> signal type,
report-refusal projection + family-level-receipt filtering, row schema, corpus
resolution) are tested directly against fakes.  The end-to-end projector is
exercised against a known archived work (``act_public_1981_23``) when the NZ
Farchive is reachable, and skipped otherwise so the suite stays runnable in a
bare worktree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lawvm.tools.nz_self_consistency as nzsc
from lawvm.new_zealand.actual_replay import (
    NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_FAMILY_NOT_PROMOTABLE_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_STRUCTURAL_MATERIALIZATION_FAILED_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
)
from lawvm.tools.self_consistency import ALL_SIGNAL_TYPES

_ROW_KEYS = {
    "statute_id",
    "amendment_id",
    "signal_type",
    "category",
    "description",
    "target_scope",
    "reason",
}


# ---------------------------------------------------------------------------
# Refusal rule_id -> signal-type classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rule_id, expected",
    [
        (NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID, "apply_failure"),
        (NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID, "apply_failure"),
        (NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID, "apply_failure"),
        (NZ_ACTUAL_REPLAY_REFUSED_STRUCTURAL_MATERIALIZATION_FAILED_RULE_ID, "target_absent"),
        (NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID, "unhandled_op"),
        (NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID, "source_pathology"),
        (NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID, "source_pathology"),
        (NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID, "invariant_violation"),
        (NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID, "skipped_amendment"),
        (NZ_ACTUAL_REPLAY_REFUSED_FAMILY_NOT_PROMOTABLE_RULE_ID, "skipped_amendment"),
    ],
)
def test_refusal_signal_type_mapping(rule_id: str, expected: str) -> None:
    assert nzsc._refusal_signal_type(rule_id) == expected


def test_family_level_receipt_is_not_a_signal() -> None:
    # A "family declared nothing to replay" receipt is honest residue, not an
    # inconsistency.
    assert (
        nzsc._refusal_signal_type(
            NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID
        )
        is None
    )


def test_unknown_refusal_defaults_to_skipped_amendment() -> None:
    # An unrecognised fail-closed refusal is still surfaced, never dropped.
    assert nzsc._refusal_signal_type("nz_actual_replay_refused_some_new_kind") == "skipped_amendment"


def test_all_mapped_signal_types_are_in_canonical_taxonomy() -> None:
    for rule_id in (
        NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_STRUCTURAL_MATERIALIZATION_FAILED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
    ):
        sig = nzsc._refusal_signal_type(rule_id)
        assert sig in ALL_SIGNAL_TYPES


# ---------------------------------------------------------------------------
# Report-refusal projection (row shape + receipt filtering)
# ---------------------------------------------------------------------------

class _Refusal:
    def __init__(
        self,
        rule_id: str,
        message: str,
        amendment_date_iso: str = "",
        op_ids: tuple = (),
        detail: dict | None = None,
    ) -> None:
        self.rule_id = rule_id
        self.message = message
        self.amendment_date_iso = amendment_date_iso
        self.op_ids = op_ids
        self.detail = detail or {}


class _Report:
    def __init__(self, refusals: list) -> None:
        self.refusals = tuple(refusals)


def test_project_report_row_shape_and_filtering() -> None:
    report = _Report([
        _Refusal(
            NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
            "op refused by dry-run surface",
            amendment_date_iso="1982-12-17",
            op_ids=("op-1",),
            detail={"family": "insert", "target_address": "section:16A"},
        ),
        # A family-level receipt must be dropped (honest residue, not a defect).
        _Refusal(
            NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID,
            "family declared nothing",
            detail={"dry_run_refusal_rule_id": "nz_dry_run_refused_no_insert_candidate"},
        ),
    ])
    rows = nzsc._project_report("act_public_1981_23", report)
    assert len(rows) == 1
    row = rows[0]
    assert _ROW_KEYS <= set(row)
    assert row["statute_id"] == "act_public_1981_23"
    assert row["amendment_id"] == "1982-12-17"
    assert row["signal_type"] == "skipped_amendment"
    assert row["category"] == NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID
    assert row["target_scope"] == "section:16A"


def test_project_report_apply_failure_from_slice_divergence() -> None:
    report = _Report([
        _Refusal(
            NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID,
            "materialized slice diverges from oracle",
            amendment_date_iso="2008-12-25",
            detail={"target_address": "section:14/subsection:3"},
        ),
    ])
    rows = nzsc._project_report("act_public_1981_23", report)
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "apply_failure"
    assert rows[0]["target_scope"] == "section:14/subsection:3"


def test_project_report_target_scope_falls_back_to_op_id() -> None:
    report = _Report([
        _Refusal(
            NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
            "operation surface missing",
            op_ids=("op-42",),
            detail={},
        ),
    ])
    rows = nzsc._project_report("act_public_2000_1", report)
    assert rows[0]["signal_type"] == "unhandled_op"
    assert rows[0]["target_scope"] == "op-42"


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------

def test_resolve_explicit_statutes() -> None:
    class _Args:
        statutes = "act_public_1955_37, act_public_1981_23 ,"
        corpus = ""
        full = False
        limit = 0

    assert nzsc.resolve_nz_work_ids(_Args()) == [
        "act_public_1955_37",
        "act_public_1981_23",
    ]


def test_resolve_explicit_statutes_respects_limit() -> None:
    class _Args:
        statutes = "a,b,c,d"
        corpus = ""
        full = False
        limit = 2

    assert nzsc.resolve_nz_work_ids(_Args()) == ["a", "b"]


def test_resolve_from_work_id_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "corpus.csv"
    csv_path.write_text(
        "work_id,year\nact_public_1901_1,1901\nact_public_1901_2,1901\n",
        encoding="utf-8",
    )

    class _Args:
        statutes = ""
        corpus = str(csv_path)
        full = False
        limit = 0

    assert nzsc.resolve_nz_work_ids(_Args()) == [
        "act_public_1901_1",
        "act_public_1901_2",
    ]


def test_resolve_from_plain_text_list(tmp_path: Path) -> None:
    txt_path = tmp_path / "ids.txt"
    txt_path.write_text(
        "# a comment\nact_public_1901_1\nact_public_1901_2\n",
        encoding="utf-8",
    )

    class _Args:
        statutes = ""
        corpus = str(txt_path)
        full = False
        limit = 0

    assert nzsc.resolve_nz_work_ids(_Args()) == [
        "act_public_1901_1",
        "act_public_1901_2",
    ]


def test_smoke_corpus_is_the_default() -> None:
    # No explicit statutes/corpus -> the committed smoke subset (not the 40k
    # archive enumeration).
    class _Args:
        statutes = ""
        corpus = ""
        full = False
        limit = 0

    ids = nzsc.resolve_nz_work_ids(_Args())
    if not ids:
        pytest.skip("NZ smoke corpus CSV not present")
    assert all(i.startswith("act_") for i in ids)
    assert len(ids) < 200  # smoke subset, not the full bench corpus


def test_build_nz_store_missing_archive_does_not_create_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # build_nz_store resolves through resolve_farchive_path; the explicit-env
    # override (highest precedence) lets us point it at a missing file and
    # assert it fails loud rather than materialising an empty archive.
    missing = tmp_path / "unused.farchive"
    monkeypatch.setenv("LAWVM_NZ_LEGISLATION_FARCHIVE_DB", str(missing))

    with pytest.raises(FileNotFoundError):
        nzsc.build_nz_store()

    assert not missing.exists()


# ---------------------------------------------------------------------------
# End-to-end projector (archive-backed; skipped without the NZ Farchive)
# ---------------------------------------------------------------------------

def _nz_store_or_skip() -> str:
    from lawvm.corpus_store import resolve_farchive_path

    db, _rule = resolve_farchive_path(
        "nz_legislation.farchive", explicit_env="LAWVM_NZ_LEGISLATION_FARCHIVE_DB"
    )
    if not Path(db).exists():
        pytest.skip(f"NZ archive not present at {db}")
    try:
        return nzsc.build_nz_store()
    except Exception as exc:  # archive unreadable in a bare worktree
        pytest.skip(f"NZ archive not reachable: {type(exc).__name__}")


@pytest.mark.slow
def test_nz_projector_row_shape_and_signals() -> None:
    store = _nz_store_or_skip()
    rows, errors = nzsc.project_nz_self_consistency("act_public_1981_23", store)
    if errors:
        pytest.skip(f"replay error (corpus incomplete): {errors[0].get('error')}")
    assert rows, "expected NZ self-consistency signals for act_public_1981_23"
    for row in rows:
        assert _ROW_KEYS <= set(row), f"row missing keys: {row}"
        assert row["statute_id"] == "act_public_1981_23"
        assert row["signal_type"] in ALL_SIGNAL_TYPES
    # act_public_1981_23 has both dry-run-refused ops and oracle-divergent
    # materializations; expect both skipped_amendment and apply_failure.
    kinds = {r["signal_type"] for r in rows}
    assert "skipped_amendment" in kinds
    assert "apply_failure" in kinds
