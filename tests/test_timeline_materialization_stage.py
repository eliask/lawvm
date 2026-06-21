"""Tests for the timeline/materialization StageResult waist (StageResult endgame).

Program spine: ``notes_internal/STAGERESULT_ENDGAME.md`` (WAIST #8 — the
built-then-severed coverage un-sever). The PIT materialization already computes a
rich :class:`~lawvm.core.timeline_results.MaterializationCoverage`; the plain
``materialize_pit`` path used to DISCARD it at ``return result.statute``. These
tests pin:

  (a) ``materialize_pit_staged(...).value == materialize_pit(...)`` (statute
      identity, 0-delta value path) and ``coverage.is_partition()``;
  (b) a clean materialization → ``coverage.violation == 0`` and a clean dossier;
  (c) the FIRE-DRILL: a ``degraded_missing_scope`` materialization, routed
      through the production cert build / ``verify_bundle``, makes a CLEAN
      certificate IMPOSSIBLE. The guard goes RED if the coverage is severed back
      to the discarding ``return result.statute`` (the account would be the
      identity-clean default and the branch could never fire).

The real-corpus dossier tests are skipped when ``data/finlex.farchive`` is absent
(like the rest of the cert suite).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.stage_result import StageResult
from lawvm.core.timeline import (
    compile_timelines,
    materialize_pit,
    materialize_pit_staged,
)
from lawvm.core.timeline_results import (
    MaterializationCoverage,
    MaterializationResult,
    materialization_result_to_stage_account,
)
from lawvm.tools.certificate_bundle import (
    STAGE_TIMELINE_MATERIALIZATION,
    BundleSelfCheckError,
    _require_clean_materialization_stage,
    _verify_materialization_stage_clean,
    build_stage_account_row,
    canonical_json_bytes,
    verify_bundle,
)

_CORPUS = Path("data/finlex.farchive")
_corpus_skip = pytest.mark.skipif(
    not _CORPUS.exists(),
    reason="data/finlex.farchive not present; skipping real-corpus bundle tests",
)


def _small_statute() -> IRStatute:
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                text="Ensimmainen pykala.",
                children=(
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Eka momentti."),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="2",
                text="Toinen pykala.",
            ),
        ),
    )
    return IRStatute(statute_id="test/1", title="Testilaki", body=body)


# ---------------------------------------------------------------------------
# (a) statute identity + partition (no corpus required)
# ---------------------------------------------------------------------------


def test_staged_value_equals_plain_materialize_pit() -> None:
    statute = _small_statute()
    timelines = compile_timelines(statute, [])
    as_of = "2020-01-01"

    staged = materialize_pit_staged(timelines, as_of, base=statute)
    plain = materialize_pit(timelines, as_of, base=statute)

    assert isinstance(staged, StageResult)
    # The value path is byte-identical: the un-sever only ADDS the account.
    assert staged.value == plain


def test_staged_coverage_is_a_partition() -> None:
    statute = _small_statute()
    timelines = compile_timelines(statute, [])
    staged = materialize_pit_staged(timelines, "2020-01-01", base=statute)

    coverage = staged.coverage
    assert coverage.unit == "addresses"
    assert coverage.totality_claimed is True
    # owned + benign + residual + violation == total (no leak).
    assert coverage.is_partition()
    # A clean compile selects addresses with no ambiguity and no blocking issue.
    assert coverage.owned == coverage.total
    assert coverage.residual == 0


# ---------------------------------------------------------------------------
# (b) clean materialization -> violation 0, clean guard passes
# ---------------------------------------------------------------------------


def test_clean_materialization_passes_the_clean_guard() -> None:
    statute = _small_statute()
    timelines = compile_timelines(statute, [])
    staged = materialize_pit_staged(timelines, "2020-01-01", base=statute)

    assert staged.coverage.violation == 0
    assert not staged.has_blocking_residual
    # The cert self-check guard accepts a clean materialization.
    _require_clean_materialization_stage(staged)


# ---------------------------------------------------------------------------
# (c) FIRE-DRILL: a degraded materialization forbids a clean claim
# ---------------------------------------------------------------------------


def _degraded_materialization_result() -> MaterializationResult:
    """A synthetic ``degraded_missing_scope`` PIT materialization result.

    This is what ``materialize_pit_ex`` returns when PIT selection could not be
    resolved without explicit scope — the case the plain ``materialize_pit`` path
    raises on and the discarding wrapper would have thrown the coverage away.
    """
    statute = IRStatute(
        statute_id="test/1",
        title="Testilaki",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
    )
    return MaterializationResult(
        materialization_status="degraded_missing_scope",
        statute=statute,
        required_dimensions=("territory",),
        certificate=MaterializationCoverage(
            as_of="2020-01-01",
            query_type="governing",
            selected_address_count=1,
            ambiguous_address_count=0,
            required_dimensions=("territory",),
        ),
    )


def test_degraded_materialization_account_carries_a_blocking_violation() -> None:
    staged = materialization_result_to_stage_account(_degraded_materialization_result())
    # The missing-scope degradation is an unowned-signal violation, not silent.
    assert staged.coverage.violation == 1
    assert staged.has_blocking_residual
    blocking = [r for r in staged.residuals if r.blocking]
    assert blocking and blocking[0].kind == "unowned_violation"
    assert blocking[0].reason == "materialization_degraded_missing_scope"


def test_degraded_materialization_forbids_clean_guard() -> None:
    """The load-bearing branch fires on a degraded materialization."""
    staged = materialization_result_to_stage_account(_degraded_materialization_result())
    with pytest.raises(BundleSelfCheckError):
        _require_clean_materialization_stage(staged)


def test_clean_default_stage_cannot_trip_the_guard() -> None:
    """Bite-proof: the IDENTITY-clean StageResult (what a severed/discarded path
    would leave) passes the guard — so the guard ONLY bites when a real degraded
    account is routed in. This is the anti-built-then-severed property: if the
    producer reverts to ``return result.statute`` (no account), the guard cannot
    fire and the fire-drill above goes RED."""
    severed_like = StageResult(value=None)  # identity defaults: clean coverage
    _require_clean_materialization_stage(severed_like)  # must NOT raise


def test_ambiguous_addresses_are_nonblocking_typed_residue() -> None:
    statute = IRStatute(
        statute_id="test/1",
        title="Testilaki",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
    )
    ambiguous = LegalAddress(path=(("section", "1"),))
    result = MaterializationResult(
        materialization_status="materialized",
        statute=statute,
        ambiguous_addresses=(ambiguous,),
        certificate=MaterializationCoverage(
            as_of="2020-01-01",
            query_type="governing",
            selected_address_count=2,
            ambiguous_address_count=1,
        ),
    )
    staged = materialization_result_to_stage_account(result)
    # Ambiguity is the tag-don't-guess frontier: residual, not violation.
    assert staged.coverage.violation == 0
    assert staged.coverage.residual == 1
    assert staged.coverage.is_partition()
    assert not staged.has_blocking_residual
    typed = [r for r in staged.residuals if r.kind == "typed_residual"]
    assert typed and typed[0].reason == "ambiguous_address"


# ---------------------------------------------------------------------------
# real-corpus dossier feeder + verify_bundle branch (the production consumer)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bundle_482(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from lawvm.tools.certificate_bundle import build_certificate_bundle

    root = tmp_path_factory.mktemp("matstage")
    out = root / "482_2024"
    build_certificate_bundle("482/2024", out, graph_store_root=root / "provenance_graph")
    return out


@_corpus_skip
def test_live_materialization_stage_feeds_the_dossier(bundle_482: Path) -> None:
    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/stage_accounts.jsonl").read_text().splitlines()
    ]
    stages = {row["stage"] for row in rows}
    assert STAGE_TIMELINE_MATERIALIZATION in stages
    mat = next(r for r in rows if r["stage"] == STAGE_TIMELINE_MATERIALIZATION)
    cov = mat["coverage_row"]
    assert cov["unit"] == "addresses" and cov["total"] > 0
    assert cov["is_partition"] is True
    # The green corpus materializes cleanly -> no unowned-signal violation.
    assert cov["violation"] == 0


@_corpus_skip
def test_real_bundle_verifies(bundle_482: Path) -> None:
    # The real dossier verifies end-to-end (the materialization branch is part of
    # the writer-side self-check and does not regress an otherwise-valid bundle).
    verify_bundle(bundle_482)


@_corpus_skip
def test_real_committed_materialization_rows_pass_the_verify_consumer(
    bundle_482: Path,
) -> None:
    # The PRODUCTION verify consumer (`_verify_materialization_stage_clean`, the
    # function `verify_bundle` calls) accepts the REAL committed stage-account
    # rows: the routed materialization coverage reached the dossier clean.
    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/stage_accounts.jsonl").read_text().splitlines()
    ]
    assert any(r["stage"] == STAGE_TIMELINE_MATERIALIZATION for r in rows)
    _verify_materialization_stage_clean(rows)  # must NOT raise


@_corpus_skip
def test_degraded_materialization_fails_the_verify_consumer(bundle_482: Path) -> None:
    """FIRE-DRILL through the PRODUCTION verify consumer: replace the routed
    materialization stage account with a degraded one (a real
    ``degraded_missing_scope`` materialization) and assert the exact consumer
    ``verify_bundle`` calls REFUSES to certify it clean.

    RED if the coverage is severed back to the discard: with the un-sever
    reverted the converter yields an identity-clean account, the degraded row
    carries ``violation == 0``, and this consumer cannot raise the
    materialization diagnostic (proven by reverting the converter -> "DID NOT
    RAISE" / message mismatch)."""
    real_rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/stage_accounts.jsonl").read_text().splitlines()
    ]
    degraded_row = build_stage_account_row(
        STAGE_TIMELINE_MATERIALIZATION,
        materialization_result_to_stage_account(_degraded_materialization_result()),
    )
    # Round-trip the row through JSON exactly as verify_bundle reads it from disk.
    degraded_row = json.loads(canonical_json_bytes(degraded_row).decode("ascii"))
    rows = [
        degraded_row if r["stage"] == STAGE_TIMELINE_MATERIALIZATION else r
        for r in real_rows
    ]
    with pytest.raises(
        BundleSelfCheckError, match="degraded materialization cannot be certified clean"
    ):
        _verify_materialization_stage_clean(rows)
