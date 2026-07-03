"""EU bench comparator → unified contract BenchUnitResult (offline, deterministic).

EU is the last frontend to join the unified cross-jurisdiction bench contract
(``lawvm.core.bench_contract``). This gate proves the EU comparator
(``eu_oracle_divergence.eu_bench_unit_result``) end-to-end and network-free:

  1. It REGISTERS into the shared comparator registry (parity with UK/EE/NZ/US/SE).
  2. It maps EU's native per-article ``OracleComparison`` onto the two contract
     error axes with the residue-reconciliation invariant holding.
  3. A REAL-statute replay-vs-oracle bench runs the full PRODUCTION pipeline
     (``lower_amending_act`` → ``order_eu_ops`` → ``apply_eu_ops_conserved`` →
     ``build_consolidation_oracle``) over committed FMX4 fixtures of the degree-57
     stress base ``32016R0044`` and its real ANNEX-root amender ``32016R0466``, plus
     the synthetic corpus PIT set — producing a FROZEN bench account with no network.

The consolidation is editorial ("no legal value"); the comparator NEVER repairs
the replay toward it, so a per-article divergence is a first-class, typed bench
residue, not a silent correction.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from lawvm.core.bench_comparator_registry import (
    has_bench_comparator,
    run_bench_comparator,
)
from lawvm.core.bench_contract import (
    BenchStatus,
    check_residue_reconciliation,
)
from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.eu.eu_consolidation_oracle import build_consolidation_oracle
from lawvm.eu.eu_oracle_divergence import (
    ArticleDivergence,
    DivergenceKind,
    OracleComparison,
    eu_bench_unit_result,
)
from lawvm.eu.eu_ordering import order_eu_ops
from lawvm.eu.fmx4_amendment_grammar import lower_amending_act
from lawvm.eu.pipeline import apply_eu_ops_conserved

FIXTURES = Path(__file__).parent / "eu" / "fixtures"
BASE_CELEX = "32016R0044"
AS_OF = "2016-04-01"
_ANNEX_KIND = cast(IRNodeKind, "annex")


def _comparison(base_celex: str, kinds: list[DivergenceKind]) -> OracleComparison:
    """Build an ``OracleComparison`` carrying one article per requested kind."""
    cmp = OracleComparison(as_of=AS_OF, base_celex=base_celex)
    for i, k in enumerate(kinds):
        cmp.divergences.append(ArticleDivergence(article_label=str(i + 1), kind=k))
    return cmp


# --------------------------------------------------------------------------- #
# Registration parity                                                          #
# --------------------------------------------------------------------------- #


def test_eu_registered() -> None:
    from lawvm.eu import eu_oracle_divergence  # noqa: F401  (import triggers registration)

    assert has_bench_comparator("eu")


def test_eu_dispatch_through_registry_returns_contract_result() -> None:
    cmp = _comparison("32016R0044", ["agreement", "agreement"])
    r = run_bench_comparator("eu", cmp)
    assert r.bench_unit_status is BenchStatus.SCORED
    assert r.unit_id == "32016R0044"


# --------------------------------------------------------------------------- #
# Axis derivation + residue reconciliation                                     #
# --------------------------------------------------------------------------- #


def test_eu_perfect_agreement_no_residue() -> None:
    r = eu_bench_unit_result(_comparison("32099R0001", ["agreement", "agreement"]))
    assert r.bench_unit_status is BenchStatus.SCORED
    assert r.structural_err == 0.0
    assert r.text_err == 0.0  # co-present, none diverge
    assert dict(r.residue_buckets) == {}
    check_residue_reconciliation(r)


def test_eu_text_divergence_is_text_axis_not_structural() -> None:
    """A co-present article whose text differs is a TEXT error, never structural."""
    r = eu_bench_unit_result(
        _comparison("32016R0044", ["agreement", "text_divergence"])
    )
    assert r.structural_err == 0.0  # both present → no structural gap
    assert r.text_err == pytest.approx(0.5)  # 1 of 2 co-present articles diverge
    assert dict(r.residue_buckets) == {}  # no structural residue → reconciles
    check_residue_reconciliation(r)


def test_eu_one_sided_articles_drive_structural_err_and_typed_residue() -> None:
    r = eu_bench_unit_result(
        _comparison(
            "32016R0044",
            [
                "agreement",
                "present_in_replay_absent_in_oracle",
                "present_in_oracle_absent_in_replay",
            ],
        )
    )
    # 2 of 3 compared articles are one-sided.
    assert r.structural_err == pytest.approx(2 / 3)
    assert dict(r.residue_buckets) == {
        "deterministic_gap": 1,  # replay surplus
        "manual_frontier": 1,  # editorial-consolidation surplus
    }
    # Text axis: only the single agreement is co-present, none diverge.
    assert r.text_err == 0.0
    check_residue_reconciliation(r)


def test_eu_headline_is_worst_of_axes() -> None:
    # structural 0, text 0.5 → headline error 0.5 (worst-of, the Liebig bind).
    r = eu_bench_unit_result(
        _comparison("32016R0044", ["agreement", "text_divergence"])
    )
    assert r.headline_error() == pytest.approx(0.5)


def test_eu_no_co_present_leaves_text_axis_unattempted() -> None:
    """Only one-sided articles → text axis is None (not attempted), not a false 0."""
    r = eu_bench_unit_result(
        _comparison("32016R0044", ["present_in_oracle_absent_in_replay"])
    )
    assert r.structural_err == 1.0
    assert r.text_err is None
    assert dict(r.residue_buckets) == {"manual_frontier": 1}
    check_residue_reconciliation(r)


def test_eu_empty_comparison_is_non_scored_not_failure() -> None:
    r = eu_bench_unit_result(OracleComparison(as_of=AS_OF, base_celex="32016R0044"))
    assert r.bench_unit_status is BenchStatus.NO_TRUTH
    assert not r.is_failure


# --------------------------------------------------------------------------- #
# REAL-statute end-to-end bench (production pipeline, offline, FROZEN)          #
# --------------------------------------------------------------------------- #


def _real_replayed_pit() -> IRStatute:
    """Native replay of the REAL ANNEX-root amender 32016R0466 against a base
    carrying the targeted Annex III and Article 6 — the full production path."""
    base = IRStatute(
        statute_id=BASE_CELEX,
        title="base",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="6", text="Article 6 (native replay)."),
                IRNode(kind=_ANNEX_KIND, label="III", text="OLD Annex III listing."),
            ),
        ),
    )
    lowered = lower_amending_act(
        (FIXTURES / "amending_annex_root_excerpt.fmx4.xml").read_bytes(),
        "32016R0466",
        base_celex=BASE_CELEX,
        effective=AS_OF,
    )
    ordered = order_eu_ops(lowered.ops)
    return apply_eu_ops_conserved(base, list(ordered.ops)).statute


def test_real_statute_replay_vs_oracle_bench_frozen() -> None:
    """The REAL 32016R0044 triple through the production pipeline → a FROZEN,
    deterministic contract result. The pinned sector-0 consolidation manifestation
    (CONS.ACT/CONS.DOC shape) carries Articles 1 and 6; the replay carries Article
    6 and Annex III. The comparison is fully accounted and never repaired."""
    replayed = _real_replayed_pit()
    before6 = _section_text(replayed, "6")

    comparison = build_consolidation_oracle(
        replayed,
        base_celex=BASE_CELEX,
        as_of=AS_OF,
        fetch_consolidation=lambda _c: (
            FIXTURES / "consolidated_cons_act_excerpt.fmx4.xml"
        ).read_bytes(),
    )
    r = eu_bench_unit_result(comparison)

    # FROZEN account: Article 1 is oracle-only (manual_frontier), Article 6 is
    # co-present but editorially re-rendered (text_divergence).
    assert r.bench_unit_status is BenchStatus.SCORED
    assert r.unit_id == BASE_CELEX
    assert r.structural_err == pytest.approx(0.5)  # 1 of 2 compared is one-sided
    assert r.text_err == pytest.approx(1.0)  # the 1 co-present article diverges
    assert dict(r.residue_buckets) == {"manual_frontier": 1}
    assert r.headline_error() == pytest.approx(1.0)  # worst-of
    check_residue_reconciliation(r)

    # NEVER repaired: the native replay's Article 6 body is byte-identical after
    # the comparison, and the editorial consolidation text did not leak in.
    assert _section_text(replayed, "6") == before6
    assert "native replay" in before6


def test_corpus_pit_bench_agrees_frozen() -> None:
    """The synthetic corpus PIT-1 consolidation AGREES with the native replay on
    every article — a perfect (0-error, no-residue) bench unit through the
    production pipeline. This exercises the agreeing end of the axis range that the
    real triple (which diverges) does not."""
    from lawvm.eu.grafter import parse_eu_regulation_ir

    base = parse_eu_regulation_ir(
        FIXTURES / "corpus_base_act.fmx4.xml", celex="32099R0001"
    )
    lowered = lower_amending_act(
        (FIXTURES / "corpus_amender_a.fmx4.xml").read_bytes(),
        "32099R9001",
        base_celex="32099R0001",
        effective="2099-02-01",
    )
    ordered = order_eu_ops(list(lowered.ops))
    replayed = apply_eu_ops_conserved(base, list(ordered.ops)).statute

    comparison = build_consolidation_oracle(
        replayed,
        base_celex="32099R0001",
        as_of="2099-02-01",
        fetch_consolidation=lambda _c: (
            FIXTURES / "corpus_cons_pit1.fmx4.xml"
        ).read_bytes(),
    )
    r = eu_bench_unit_result(comparison)
    assert r.bench_unit_status is BenchStatus.SCORED
    assert r.structural_err == 0.0
    assert r.text_err == 0.0
    assert dict(r.residue_buckets) == {}
    assert r.headline_error() == 0.0
    check_residue_reconciliation(r)


def _section_text(statute: IRStatute, label: str) -> str:
    out = [""]

    def _walk(node: IRNode) -> None:
        if node.label == label and str(node.kind) == "section":
            out[0] = node.text or ""
        for child in node.children:
            _walk(child)

    _walk(statute.body)
    return out[0]
