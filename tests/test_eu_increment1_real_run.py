"""Increment 1: real multi-act grammar run over the degree-57 stress base.

Goal 1 (full multi-act grammar run) + goal 4 (DOC/ANNEX-root hardening),
exercised OFFLINE on pinned excerpts of the REAL acquired amending acts of the
degree-57 stress base ``32016R0044`` (restrictive-measures regulation re Libya):

  * ``32016R0466`` — acquired as an ANNEX-root new-annex manifestation (the
    Office ships the replacement annex body). Lowers to ONE typed op,
    ``EU_FMX4.ANNEX_ROOT_REPLACE`` → ``annex:III``.
  * ``32016R0690`` — acquired as a DOC-root publication ENVELOPE (metadata only,
    no enacting terms). A typed ``eu_fmx4_grammar_envelope_no_enacting_terms``
    residual — never a crash, never a silent zero.

The pinned fixtures are faithful structural excerpts of the bytes in the live
``eu_cellar.farchive`` (the real acts carry a long sanctions listing of named
persons; that personal data is elided — only the FMX4 SHAPE is pinned). A
networked smoke (opt-in via ``LAWVM_EU_NETWORK_SMOKE=1``) runs the same lowering
over the REAL farchive bytes and asserts the same coverage shape.

This is the MEASURED real-data coverage (design §3.6 Increment 1): of the two
acquired amending-act manifestations, one lowers to a typed op and one is a typed
envelope residual. The base ``32016R0044`` itself is the BASE (26 substantive,
non-amending articles) — correctly 0 amendment ops, each article a typed
``uncovered_instruction`` (it is not an amending act).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.eu.eu_ordering import order_eu_ops
from lawvm.eu.fmx4_amendment_grammar import LoweringResult, lower_amending_act
from lawvm.eu.pipeline import apply_eu_ops_conserved

_ANNEX_KIND = cast(IRNodeKind, "annex")  # grafter vocabulary (see grafter.py)

FIXTURES = Path(__file__).parent / "eu" / "fixtures"
BASE_CELEX = "32016R0044"

# The two real acquired amending-act manifestations of the degree-57 base, with
# their date-of-application (entry-into-force) keys.
REAL_AMENDERS = [
    ("32016R0466", "amending_annex_root_excerpt.fmx4.xml", "2016-04-01"),
    ("32016R0690", "amending_doc_envelope_excerpt.fmx4.xml", "2016-05-04"),
]


def _lower_all() -> dict[str, LoweringResult]:
    out: dict[str, LoweringResult] = {}
    for celex, fixture, effective in REAL_AMENDERS:
        data = (FIXTURES / fixture).read_bytes()
        out[celex] = lower_amending_act(
            data, celex, base_celex=BASE_CELEX, effective=effective
        )
    return out


def test_annex_root_amender_lowers_to_typed_op() -> None:
    """32016R0466 (ANNEX-root) — the real degree-57 amending act — lowers."""
    results = _lower_all()
    r = results["32016R0466"]
    assert r.instruction_count == 1
    assert r.covered_count == 1
    op = r.ops[0]
    assert op.action == StructuralAction.REPLACE
    assert op.witness_rule_id == "EU_FMX4.ANNEX_ROOT_REPLACE"
    assert str(op.target) == "annex:III"
    assert op.source is not None and op.source.effective == "2016-04-01"


def test_doc_envelope_amender_is_typed_residual_not_crash() -> None:
    """32016R0690 (DOC envelope, metadata-only) — typed residual, no op."""
    results = _lower_all()
    r = results["32016R0690"]
    assert r.covered_count == 0
    assert r.instruction_count == 0
    diag_ids = [d.rule_id for d in r.diagnostics]
    assert diag_ids == ["eu_fmx4_grammar_envelope_no_enacting_terms"]


def test_measured_real_coverage_over_acquired_amenders() -> None:
    """The honest measured coverage number over the real acquired amenders:
    1 of 2 manifestations lowers to a typed op; the other is a typed residual.
    Every instruction is accounted for (op or diagnostic) — no silent loss."""
    results = _lower_all()
    total_instructions = sum(r.instruction_count for r in results.values())
    total_ops = sum(r.covered_count for r in results.values())
    total_diags = sum(len(r.diagnostics) for r in results.values())
    # 1 instruction total (the ANNEX replace); the DOC envelope contributes 0
    # instructions but 1 typed residual.
    assert total_instructions == 1
    assert total_ops == 1
    # Conservation: instructions == ops + per-instruction diagnostics, and the
    # envelope residual is counted separately (0-instruction manifestation).
    assert total_diags == 1  # the envelope residual
    # Coverage over countable instructions is 100% here; the honest finding is
    # that the acquired manifestations are annex/envelope shapes, NOT the
    # article-instruction shape, so the article grammar's reach is exercised by
    # the ANNEX-root path, not the ENACTING.TERMS path.
    assert total_ops / total_instructions == 1.0


def test_end_to_end_replay_orders_and_applies_real_amender_ops() -> None:
    """lower → order (date-of-application) → replay via the Wave-4 seam, on the
    real amender ops, against a base carrying the targeted annex."""
    base_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="6", text="Article 6 (original)."),
            IRNode(kind=_ANNEX_KIND, label="III", text="OLD Annex III listing."),
        ),
    )
    base = IRStatute(statute_id=BASE_CELEX, title="base", body=base_body)

    results = _lower_all()
    all_ops = [op for r in results.values() for op in r.ops]
    ordered = order_eu_ops(all_ops)
    result = apply_eu_ops_conserved(base, list(ordered.ops))

    # Conservation: every op applied or a typed RejectedItem.
    assert len(result.applied_ops) + len(result.skipped_items) == len(ordered.ops)
    assert len(result.applied_ops) == 1  # the annex replace

    # The base's Annex III was replaced with the new annex body.
    def _find(node: IRNode, label: str) -> IRNode | None:
        if node.label == label and str(node.kind) in ("annex", "schedule"):
            return node
        for child in node.children:
            hit = _find(child, label)
            if hit is not None:
                return hit
        return None

    annex = _find(result.statute.body, "III")
    assert annex is not None
    assert "OLD Annex III" not in (annex.text or "")
    assert "ANNEX III" in (annex.text or "") or "List" in (annex.text or "")


# --------------------------------------------------------------------------- #
# Networked smoke (opt-in): run the hardened grammar over the REAL farchive     #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    os.environ.get("LAWVM_EU_NETWORK_SMOKE") != "1",
    reason="reads the live eu_cellar.farchive; set LAWVM_EU_NETWORK_SMOKE=1 to run",
)
def test_real_farchive_amender_coverage_smoke() -> None:
    """Run the hardened grammar over the ACTUAL acquired bytes in the data-root's
    eu_cellar.farchive. Asserts the SAME coverage shape the pinned fixtures
    capture: the ANNEX-root act lowers, the DOC envelope is a typed residual."""
    data_root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not data_root:
        pytest.skip("LAWVM_CANONICAL_DATA_ROOT unset")
    fa_path = os.path.join(data_root, "data", "eu_cellar.farchive")
    if not os.path.exists(fa_path):
        pytest.skip("eu_cellar.farchive not present (acquisition not run)")
    from farchive import Farchive

    fa = Farchive(fa_path)
    try:
        d466 = fa.get("cellar://celex/32016R0466/enacted/eng/fmx4")
        d690 = fa.get("cellar://celex/32016R0690/enacted/eng/fmx4")
    finally:
        fa.close()
    if d466 is None or d690 is None:
        pytest.skip("real amender bytes absent from farchive")

    r466 = lower_amending_act(d466, "32016R0466", base_celex=BASE_CELEX)
    assert r466.covered_count == 1
    assert r466.ops[0].witness_rule_id == "EU_FMX4.ANNEX_ROOT_REPLACE"

    r690 = lower_amending_act(d690, "32016R0690", base_celex=BASE_CELEX)
    assert r690.covered_count == 0
    assert any(
        d.rule_id == "eu_fmx4_grammar_envelope_no_enacting_terms"
        for d in r690.diagnostics
    )
