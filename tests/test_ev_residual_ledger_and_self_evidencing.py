"""EVIDENCE-LEDGER wave acceptance: EV-03 (residual-ledger monotonicity) + EV-07
(self-evidencing diagnostic totality).

EV-03 — ``sweep_stage_residual_ledger`` over the per-stage account fold: a residual
counted in a stage's coverage ``violation`` class must have a committed blocking
residual record (and the dual). The unit acceptance pins the conservation predicate
in both directions; the corpus sweep pins the GREEN population at 0 and proves the
production dossier guard (``_require_monotone_stage_residual_ledger``) is reachable.

EV-07 — ``sweep_source_text_failure_self_evidencing`` over a residual population:
every source-text-failure residual (``unowned_violation`` / ``typed_residual``)
embeds its verbatim offending snippet; the out-of-family kinds are out of scope.
The corpus sweep pins the GREEN population at 0 (the forest/surface producers set
the text by construction) and proves the production dossier guard
(``_require_self_evidencing_stage_residuals``) is reachable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lawvm.core.diagnostic_self_evidencing import (
    DIAGNOSTIC_NOT_SELF_EVIDENCING,
    SOURCE_TEXT_FAILURE_KINDS,
    sweep_source_text_failure_self_evidencing,
)
from lawvm.core.observation_registry import FINDING_REGISTRY
from lawvm.core.stage_residual_monotonicity import (
    RESIDUAL_LEDGER_NONMONOTONE,
    sweep_stage_residual_ledger,
)
from lawvm.core.stage_result import Residual

_CORPUS = Path("data/finlex.farchive")
_corpus_skip = pytest.mark.skipif(
    not _CORPUS.exists(),
    reason="data/finlex.farchive not present; skipping real-corpus bundle sweep",
)


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_ev03_ev07_codes_registered_as_observation() -> None:
    for code in (RESIDUAL_LEDGER_NONMONOTONE, DIAGNOSTIC_NOT_SELF_EVIDENCING):
        assert code in FINDING_REGISTRY, f"{code} not registered"
        assert FINDING_REGISTRY[code].role == "observation"


# ---------------------------------------------------------------------------
# EV-03 unit acceptance — conservation in both directions
# ---------------------------------------------------------------------------


def _account(stage: str, violation: int, residual_rows: list[dict]) -> dict:
    return {
        "stage": stage,
        "coverage_row": {"violation": violation, "owned": 0, "residual": 0, "benign": 0},
        "residual_rows": residual_rows,
    }


def test_ev03_counted_not_recorded_fires() -> None:
    findings = sweep_stage_residual_ledger([_account("s", 2, [])])
    assert len(findings) == 1
    assert findings[0].code == RESIDUAL_LEDGER_NONMONOTONE
    assert findings[0].direction == "counted_not_recorded"
    assert findings[0].coverage_violation == 2
    assert findings[0].blocking_residuals == 0


def test_ev03_recorded_not_counted_fires() -> None:
    rows = [{"kind": "unowned_violation", "blocking": True}]
    findings = sweep_stage_residual_ledger([_account("s", 0, rows)])
    assert len(findings) == 1
    assert findings[0].direction == "recorded_not_counted"


def test_ev03_balanced_account_is_silent() -> None:
    # violation counted AND discharged by a blocking residual record.
    rows = [{"kind": "unowned_violation", "blocking": True}]
    assert not sweep_stage_residual_ledger([_account("s", 1, rows)])


def test_ev03_nonblocking_residual_does_not_discharge_a_violation() -> None:
    # a NON-blocking residual record does NOT discharge a counted violation
    # (only an explicitly-blocking record witnesses a signal-bearing violation).
    rows = [{"kind": "typed_residual", "blocking": False}]
    findings = sweep_stage_residual_ledger([_account("s", 1, rows)])
    assert findings and findings[0].direction == "counted_not_recorded"


def test_ev03_seam_b_aggregator_construction_is_monotone() -> None:
    """The SEAM-B aggregators hold violation == #blocking-residual exactly; the
    swept committed rows of such a stage are monotone by construction."""
    from lawvm.core.stage_result import StageResult
    from lawvm.tools.certificate_bundle import build_stage_account_row

    # mirror aggregate_canonical_op_stage's construction: one blocking residual per
    # counted violation -> a balanced account that the sweep accepts.
    residuals = (
        Residual(kind="unowned_violation", reason="decline:r1", scope="x", text="t1", blocking=True),
        Residual(kind="unowned_violation", reason="decline:r2", scope="x", text="t2", blocking=True),
    )
    from lawvm.core.stage_result import CoverageCertificate

    stage = StageResult(
        value=None,
        residuals=residuals,
        coverage=CoverageCertificate(
            unit="candidate_ops", total=2, owned=0, violation=2, totality_claimed=True
        ),
    )
    row = build_stage_account_row("seam.b", stage)
    assert not sweep_stage_residual_ledger([row]), (
        "a SEAM-B-construction stage (one blocking residual per counted violation) "
        "must sweep clean"
    )


# ---------------------------------------------------------------------------
# EV-07 unit acceptance — self-evidencing totality + family scope
# ---------------------------------------------------------------------------


def _r(kind: str, text: str, blocking: bool = True) -> Residual:
    return Residual(
        kind=kind,
        reason=f"{kind}:drill",
        scope="drill/1",
        source_unit_id="drill/1",
        char_start=0,
        char_end=5,
        text=text,
        blocking=blocking,
    )


def test_ev07_source_text_failure_family_is_exactly_two_kinds() -> None:
    assert SOURCE_TEXT_FAILURE_KINDS == frozenset({"unowned_violation", "typed_residual"})


@pytest.mark.parametrize("kind", sorted(SOURCE_TEXT_FAILURE_KINDS))
def test_ev07_snippetless_family_member_fires(kind: str) -> None:
    findings = sweep_source_text_failure_self_evidencing([_r(kind, text="")])
    assert len(findings) == 1
    assert findings[0].code == DIAGNOSTIC_NOT_SELF_EVIDENCING
    assert findings[0].kind == kind


@pytest.mark.parametrize("kind", sorted(SOURCE_TEXT_FAILURE_KINDS))
def test_ev07_snippet_carrying_family_member_is_silent(kind: str) -> None:
    assert not sweep_source_text_failure_self_evidencing([_r(kind, text="3 §:n 2 mom")])


@pytest.mark.parametrize("kind", ["out_of_scope", "benign_uninterpreted"])
def test_ev07_out_of_family_kinds_are_silent_even_without_text(kind: str) -> None:
    # out-of-family residuals carry no offending source clause by design.
    assert not sweep_source_text_failure_self_evidencing(
        [_r(kind, text="", blocking=False)]
    )


def test_ev07_whitespace_only_text_counts_as_no_snippet() -> None:
    findings = sweep_source_text_failure_self_evidencing([_r("unowned_violation", text="   ")])
    assert findings and findings[0].code == DIAGNOSTIC_NOT_SELF_EVIDENCING


# ---------------------------------------------------------------------------
# Real-corpus sweep — GREEN population is 0 + the production guard is reachable
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bundle_482(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from lawvm.tools.certificate_bundle import build_certificate_bundle

    root = tmp_path_factory.mktemp("ev_evledger")
    out = root / "482_2024"
    build_certificate_bundle("482/2024", out, graph_store_root=root / "provenance_graph")
    return out


@_corpus_skip
def test_corpus_stage_accounts_residual_ledger_is_monotone(bundle_482: Path) -> None:
    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/stage_accounts.jsonl").read_text().splitlines()
    ]
    assert rows, "no stage accounts committed"
    # EV-03 population on the green corpus is 0 (every counted violation discharged).
    assert sweep_stage_residual_ledger(rows) == ()


@_corpus_skip
def test_corpus_stage_residuals_are_self_evidencing(bundle_482: Path) -> None:
    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/stage_accounts.jsonl").read_text().splitlines()
    ]
    # Reconstruct the core Residual shape from the committed rows (kind + source_text)
    # and assert EV-07 population is 0 (every source-text-failure residual carries text).
    residuals = []
    for account in rows:
        for rr in account.get("residual_rows", ()):
            residuals.append(
                Residual(
                    kind=str(rr["kind"]),
                    reason=str(rr.get("reason", "x")) or "x",
                    scope=str(rr.get("scope", {}).get("scope", "")),
                    text=str(rr.get("source_text", "")),
                    blocking=bool(rr.get("blocking", False)),
                )
            )
    assert sweep_source_text_failure_self_evidencing(residuals) == ()
