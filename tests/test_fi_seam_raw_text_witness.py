"""Witness/conservation coverage for FI raw-text-derived repeal/effect ops.

These tests pin the no-representation-regression witness exception: every legal
operation minted from — or dropped against — raw johtolause text must carry a
``witness_rule_id`` plus a typed Finding/Residual, never vanish silently.

Lanes covered:
- pure-kumotaan whole-section repeal injection (witnessed mint)
- replay-products cited-version ancestor snapshot drop (witnessed drop)
- frontend fallback heuristic op mint (witnessed mint)
- effect-lowering unrecognized commencement shape (non-optional typed residual)
"""
from __future__ import annotations

import datetime as dt

import lxml.etree as etree

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
)
from lawvm.core.semantic_types import IRNodeKind, MetaClauseKind, StructuralAction
from lawvm.finland.op_provenance import Recovered
from lawvm.finland.frontend_compile import (
    FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID,
    normalize_and_compile_ops,
)
from lawvm.finland.effect_lowering import lower_johto_effects
from lawvm.finland.kumotaan_replay import (
    FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID,
    _inject_pure_kumotaan_repeal_ops,
)
from lawvm.finland.replay_products import (
    FI_CITED_VERSION_SNAPSHOT_DROP_RULE_ID,
    _drop_cited_version_item_ancestor_snapshots,
)
from lawvm.finland.statute import ReplayState


# ---------------------------------------------------------------------------
# Rank 1 — pure-kumotaan whole-section repeal injection mints a witnessed op
# ---------------------------------------------------------------------------


def _single_section_state(label: str) -> ReplayState:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label=label,
                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
            ),
        ),
    )
    return ReplayState(ir=body)


def test_pure_kumotaan_repeal_injection_is_witnessed() -> None:
    state = _single_section_state("7")
    lo_ops: list[LegalOperation] = []

    result = _inject_pure_kumotaan_repeal_ops(
        lo_ops,
        amendment_id="2025/900",
        source_title="Laki",
        amendment_issue_date=dt.date(2025, 1, 1),
        kumotaan_labels=["7"],
        chap_map_sets=None,
        amendment_effective_date=dt.date(2025, 6, 1),
        state=state,
        source_raw_text="kumotaan 7 §",
    )

    # One REPEAL op injected, carrying the witness rule id.
    assert result.injected_count == 1
    assert len(lo_ops) == 1
    injected_op = lo_ops[0]
    assert injected_op.action is StructuralAction.REPEAL
    assert injected_op.witness_rule_id == FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID

    # The typed witness record matches the injected op and supports a finding.
    record = result.injected[0]
    assert record.op_id == injected_op.op_id
    assert record.rule_id == FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID
    assert record.target_unit_kind == "section"
    assert record.target_norm == "7"
    assert record.finding_detail()["rule_id"] == FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID


def test_pure_kumotaan_repeal_injection_negative_when_already_covered() -> None:
    """A section already covered by a parsed REPEAL must NOT be re-injected."""
    state = _single_section_state("7")
    existing = LegalOperation(
        op_id="parsed_repeal_7",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "7"),)),
        source=OperationSource(statute_id="2025/900"),
    )
    lo_ops: list[LegalOperation] = [existing]

    result = _inject_pure_kumotaan_repeal_ops(
        lo_ops,
        amendment_id="2025/900",
        source_title="Laki",
        amendment_issue_date=dt.date(2025, 1, 1),
        kumotaan_labels=["7"],
        chap_map_sets=None,
        amendment_effective_date=dt.date(2025, 6, 1),
        state=state,
        source_raw_text="kumotaan 7 §",
    )

    assert result.injected_count == 0
    assert result.injected == ()
    assert lo_ops == [existing]


# ---------------------------------------------------------------------------
# Rank 2 — cited-version ancestor snapshot drop is witnessed, never silent
# ---------------------------------------------------------------------------


def _snapshot_payload(*subsection_labels: str, text: str = "") -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=tuple(
            IRNode(kind=IRNodeKind.SUBSECTION, label=label, text=text)
            for label in subsection_labels
        ),
    )


def test_cited_version_ancestor_snapshot_drop_emits_residual() -> None:
    target = LegalAddress(path=(("section", "5"),))
    # Cited act's broader same-effective snapshot (more nodes AND materially more text).
    cited = LegalOperation(
        op_id="snapshot_section_5_cited",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=target,
        payload=_snapshot_payload("1", "2", "3", text="x" * 60),
        source=OperationSource(
            statute_id="2010/100",
            effective="2024-01-01",
        ),
    )
    # Later act's stale ancestor snapshot for a local item cited-version clause.
    stale = LegalOperation(
        op_id="snapshot_section_5_stale",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=target,
        payload=_snapshot_payload("1"),
        source=OperationSource(
            statute_id="2024/200",
            effective="2024-01-01",
            raw_text=(
                "muutetaan 5 §:n 1 kohta, sellaisena kuin se on laissa 100/2010"
            ),
        ),
    )

    result = _drop_cited_version_item_ancestor_snapshots([cited, stale])

    # Conservation: every input op is accounted for (accepted or rejected).
    assert len(result.filtered.accepted_items) + len(result.filtered.rejected_items) == 2
    accepted_ids = {op.op_id for op in result.filtered.accepted_items}
    # The cited (broader) snapshot is kept; the stale ancestor is dropped.
    assert "snapshot_section_5_cited" in accepted_ids
    assert "snapshot_section_5_stale" not in accepted_ids
    assert [rej.item.op_id for rej in result.filtered.rejected_items] == [
        "snapshot_section_5_stale"
    ]
    rejected = result.filtered.rejected_items[0]
    assert rejected.reason_code == FI_CITED_VERSION_SNAPSHOT_DROP_RULE_ID
    assert rejected.reason  # non-empty human reason
    assert rejected.blocking is False


def test_cited_version_snapshot_no_drop_keeps_all_ops() -> None:
    """An op without a cited-version item clause is kept and never residualized."""
    plain = LegalOperation(
        op_id="snapshot_section_9",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "9"),)),
        payload=_snapshot_payload("1"),
        source=OperationSource(
            statute_id="2024/200",
            effective="2024-01-01",
            raw_text="muutetaan 9 § seuraavasti",
        ),
    )

    result = _drop_cited_version_item_ancestor_snapshots([plain])

    assert [op.op_id for op in result.filtered.accepted_items] == ["snapshot_section_9"]
    assert result.filtered.rejected_items == ()


# ---------------------------------------------------------------------------
# Rank 3 — fallback heuristic op mint is witnessed on the production lane
# ---------------------------------------------------------------------------


def _twelve_section_master() -> ReplayState:
    return ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=tuple(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label=str(n),
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
                )
                for n in range(1, 13)
            ),
        )
    )


_JOHTO_ONLY_TREE = etree.fromstring(
    b'<akn xmlns=""><statute><johdantokappale>x</johdantokappale></statute></akn>'
)


def test_fallback_minted_op_emits_witnessed_finding_on_production_lane() -> None:
    """Drive the real frontend lane: a heuristic-minted op carries a finding.

    ``kumoaa 6 §`` uses a non-canonical verb the structured parser does not
    model, so the op is minted by ``parse_ops_fallback_heuristic`` from raw text.
    The mint must surface a witnessed finding on the PhaseResult ledger.
    """
    master = _twelve_section_master()
    phase = normalize_and_compile_ops(
        johto="kumoaa 6 §",
        muutos_tree=_JOHTO_ONLY_TREE,
        master=master,
        base_ir=master.ir,
        amendment_id="9999/1",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="9999/0",
        strict_profile=None,
    )

    op = phase.output[0]
    assert isinstance(op.provenance, Recovered) and op.provenance.from_fallback_provenance
    assert op.witness_rule_id == FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID

    fallback_findings = [
        f for f in phase.findings() if f.kind == "PARSE.FALLBACK_OP_FROM_RAW_TEXT"
    ]
    assert len(fallback_findings) == 1
    finding = fallback_findings[0]
    assert finding.role == "observation"
    assert finding.blocking is False
    assert finding.detail["fallback_source"] == "parse_ops_fallback_heuristic"
    assert finding.detail["rule_id"] == FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID
    assert finding.detail["op_type"] == "REPEAL"


def test_canonical_verb_does_not_trigger_fallback_finding() -> None:
    """Negative: canonical ``kumotaan`` is parsed structurally, no fallback mint."""
    master = _twelve_section_master()
    phase = normalize_and_compile_ops(
        johto="kumotaan 6 §",
        muutos_tree=_JOHTO_ONLY_TREE,
        master=master,
        base_ir=master.ir,
        amendment_id="9999/1",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="9999/0",
        strict_profile=None,
    )

    assert all(
        not (isinstance(op.provenance, Recovered) and op.provenance.from_fallback_provenance)
        for op in phase.output
    )
    assert not [
        f for f in phase.findings() if f.kind == "PARSE.FALLBACK_OP_FROM_RAW_TEXT"
    ]


# ---------------------------------------------------------------------------
# Rank 9 — unrecognized commencement shape → typed residual via non-optional sink
# ---------------------------------------------------------------------------


def test_recognized_meta_clause_with_no_effect_is_residualized_via_mandatory_sink() -> None:
    """A recognized clause that lowers to no intent flows into the mandatory sink.

    A delegation clause is recognized by ``extract_meta_clauses`` but produces no
    executable EffectIntent. The mandatory ``unsupported_out`` sink records it as
    a typed residual instead of dropping it silently.
    """
    johto = (
        "Valtioneuvosto voidaan antaa tarkempia säännöksiä tämän lain "
        "täytäntöönpanosta."
    )
    unsupported: list = []

    intents = lower_johto_effects(johto, unsupported_out=unsupported)

    assert intents == []
    assert len(unsupported) == 1
    record = unsupported[0]
    assert record.reason_code == "delegation_clause_not_executable_effect"
    assert record.clause_kind == MetaClauseKind.DELEGATION.value
    assert "säännöksiä" in record.raw_text


def test_unsupported_sink_is_mandatory() -> None:
    """The sink is non-optional: callers cannot silently drop unrecognized shapes.

    A keyword-only parameter with no default means omitting the sink is a call
    error, so the silent-drop path is closed structurally rather than by
    convention.
    """
    import inspect

    sig = inspect.signature(lower_johto_effects)
    sink = sig.parameters["unsupported_out"]
    assert sink.default is inspect.Parameter.empty
    assert sink.kind is inspect.Parameter.KEYWORD_ONLY


def test_commencement_with_date_does_not_residualize() -> None:
    """Negative: a well-formed commencement lowers to an intent, no residual."""
    johto = "Tämä laki tulee voimaan 1 päivänä maaliskuuta 2026."
    unsupported: list = []

    intents = lower_johto_effects(johto, unsupported_out=unsupported)

    assert len(intents) == 1
    assert unsupported == []
