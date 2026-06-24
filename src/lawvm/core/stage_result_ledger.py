"""Canonical ``StageResult`` -> certificate-ledger row mapping.

WHAT THIS MODULE IS
===================
The load-bearing GLUE of the StageResult endgame's certificate-dossier waist
(WAIST #9, ``notes_internal/WAVE2_DESIGN.md``). It defines the ONE canonical,
field-for-field projection of the core stage accounts ::

    core.stage_result.Residual            -> a residual-ledger row dict
    core.phase_result.Finding             -> a finding-ledger row dict
    core.stage_result.CoverageCertificate -> a coverage row dict

onto the JSON-serialisable row shape the certificate dossier commits into its
per-stage subroots (``lawvm.tools.certificate_bundle`` consumes these rows and
hashes them with the EXISTING ``leaf_hash`` / ``set_root`` vocabulary — there is
no new hash machinery here, and this module imports nothing from the tools
layer, so ``core`` stays free of a tools dependency).

WHY A SEPARATE MAP (NOT the existing cert ``_residual_row`` / ``_finding_row``)
==============================================================================
The dossier's ``residue/residuals.jsonl`` / ``residue/findings.jsonl`` ledgers
carry the EXPERIMENTAL-WRITER's own residual taxonomy (the ``§5.4`` closed
``_RESIDUAL_KINDS`` vocabulary, registry-derived ``profile_effect``, etc.). A
core ``Residual`` is the GENERAL stage account (Pro §7) and its ``kind`` is the
core closed vocabulary (``out_of_scope`` / ``typed_residual`` /
``unowned_violation`` / ``benign_uninterpreted``), NOT the writer's §5.4 set.
The two are deliberately DIFFERENT ledgers: the per-stage subroots are an
ADDITIVE attribution layer (a checker can say WHICH stage diverged) and they do
NOT fold into the writer's flat ``residual_root`` / ``finding_root`` /
``coverage_root`` (those stay value-identical — 0-delta). Mixing the two
vocabularies into one ledger would either corrupt the §5.4 vocabulary check or
silently change the committed flat roots. Hence a distinct, self-contained map.

CANONICAL ORDERING
==================
Each ``*_subroot`` is built from a STAGE's account in deterministic order.
``stage_residual_rows`` / ``stage_finding_rows`` preserve the producer's tuple
order (the stage emits them in a meaningful sequence); the subroot constructor
in the tools layer uses ``set_root`` (order-independent by construction) so a
re-ordering producer cannot perturb the subroot — but duplicate rows are
forbidden by ``set_root`` (spec §3.1.1), which is the correct invariant for a
stage account (no two identical residual records).
"""

from __future__ import annotations

from typing import Any, Dict, List

from lawvm.core.phase_result import Finding
from lawvm.core.stage_result import CoverageCertificate, Residual, StageResult

__all__ = [
    "residual_row",
    "finding_row",
    "coverage_row",
    "stage_residual_rows",
    "stage_finding_rows",
    "stage_coverage_row",
]


def residual_row(residual: Residual) -> Dict[str, Any]:
    """Map a core :class:`Residual` to its canonical ledger-row dict.

    Field-for-field per WAIST #9: ``{kind, diagnostic_code(=kind), role,
    blocking, scope, source_text(=text), rule_id}`` plus the span coordinates
    the core ``Residual`` carries (self-evidencing per the diagnostics rule).
    ``diagnostic_code`` mirrors ``kind`` because a core residual's ``kind`` IS
    its diagnostic class (the writer-side §5.4 ledger splits the two; the core
    account does not). ``role`` is ``"residual"`` (a core ``Residual`` is the
    unaccounted half, never a registry-classified finding) and ``rule_id`` is
    empty (the core account does not name a producing rule — that lives in the
    FI per-family residual records that FEED the core one).
    """
    return {
        "kind": residual.kind,
        "diagnostic_code": residual.kind,
        "role": "residual",
        "blocking": residual.blocking,
        "scope": {
            "scope": residual.scope,
            "source_unit_id": residual.source_unit_id,
            "char_start": residual.char_start,
            "char_end": residual.char_end,
        },
        "reason": residual.reason,
        "source_text": residual.text,
        "rule_id": "",
    }


def finding_row(finding: Finding) -> Dict[str, Any]:
    """Map a core :class:`Finding` to its canonical ledger-row dict.

    Per WAIST #9: ``{diagnostic_code(=kind), role, blocking, scope, source_refs,
    phase(=stage), detail}``. ``scope`` carries the finding's ``source_statute``
    (a core ``Finding`` scopes to a statute, not an address/date-range like the
    writer's §5.x finding rows). ``source_refs`` is empty (the core finding does
    not enumerate source refs — those are on the writer's verdict rail).
    """
    return {
        "diagnostic_code": finding.kind,
        "role": str(finding.role),
        "blocking": finding.blocking,
        "scope": {"source_statute": finding.source_statute},
        "source_refs": [],
        "phase": finding.stage,
        "detail": dict(finding.detail),
    }


def coverage_row(coverage: CoverageCertificate) -> Dict[str, Any]:
    """Map a core :class:`CoverageCertificate` to its canonical coverage row.

    Per WAIST #9: ``{unit, total, owned, benign, residual, violation,
    totality_claimed, is_partition}``. ``is_partition`` is the COMPUTED totality
    verdict (the four classes sum to ``total`` AND totality is claimed) — it is
    committed so a checker reads the producer's totality claim AND its
    arithmetic outcome without recomputing the partition.
    """
    return {
        "unit": coverage.unit,
        "total": coverage.total,
        "owned": coverage.owned,
        "benign": coverage.benign,
        "residual": coverage.residual,
        "violation": coverage.violation,
        "totality_claimed": coverage.totality_claimed,
        "is_partition": coverage.is_partition(),
    }


def stage_residual_rows(stage: StageResult[Any]) -> List[Dict[str, Any]]:
    """The canonical residual rows for one stage account (producer order)."""
    return [residual_row(residual) for residual in stage.residuals]


def stage_finding_rows(stage: StageResult[Any]) -> List[Dict[str, Any]]:
    """The canonical finding rows for one stage account (producer order)."""
    return [finding_row(finding) for finding in stage.findings]


def stage_coverage_row(stage: StageResult[Any]) -> Dict[str, Any]:
    """The canonical coverage row for one stage account."""
    return coverage_row(stage.coverage)
