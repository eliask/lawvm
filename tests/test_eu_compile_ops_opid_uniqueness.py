"""Regression: compile_ops_for_statute must mint globally-unique op_ids.

EUOpsParser.extract_ops mints op_ids on a per-CALL counter with no CELEX prefix
(``eu-compat-{n}-{i}`` / ``corrigenda-{n}``). When a base act has 2+ affecting
acts, compile_ops_for_statute calls extract_ops once per affecting act, so each
restarts its counter at 1 and the raw op_ids collide across acts. Since
apply_eu_ops_conserved keys its accepted/rejected partition on op_id and rejects
duplicates (fail-loud), the collision would block live replay of any base act
with more than one amender. The assembly seam prefixes each op_id with the
(unique) affecting-act CELEX to restore a provably-bijective partition.
"""
from __future__ import annotations

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.eu.pipeline import EUReplayPipeline, apply_eu_ops_conserved
from tests.test_eu_apply_conserved import _baseline_statute


def _colliding_op(section_label: str) -> LegalOperation:
    # Mirror the parser's un-prefixed, per-call-counter op_id scheme: both
    # affecting acts hand back this identical id.
    return LegalOperation(
        op_id="eu-compat-1-0",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        source=OperationSource(statute_id="unknown"),
    )


class _StubParser:
    diagnostics: tuple = ()

    def extract_ops(self, text: str):  # noqa: ARG002 — text drives nothing in the stub
        # Every call returns the SAME raw op_id — the collision the fix guards.
        return [_colliding_op(text)]


def test_compile_ops_for_statute_prefixes_opids_with_affecting_celex(monkeypatch) -> None:
    pipe = EUReplayPipeline.__new__(EUReplayPipeline)
    pipe.diagnostics = []
    pipe.parser_diagnostics = []
    pipe.parser = _StubParser()
    # Two distinct affecting acts, each yielding the same raw op_id "eu-compat-1-0".
    monkeypatch.setattr(pipe, "discover_affecting_acts", lambda celex: ["32001R0001", "32002R0002"])
    # fetch_amendment_text returns the section label the stub parser targets.
    monkeypatch.setattr(pipe, "fetch_amendment_text", lambda celex: "1" if celex == "32001R0001" else "2")

    ops = pipe.compile_ops_for_statute("32000R0000")

    assert len(ops) == 2
    op_ids = [op.op_id for op in ops]
    # Globally unique after prefixing — the raw collision is resolved.
    assert len(set(op_ids)) == 2, op_ids
    assert set(op_ids) == {"32001R0001-eu-compat-1-0", "32002R0002-eu-compat-1-0"}
    # Source is still rewritten to the affecting act.
    assert {op.source.statute_id for op in ops} == {"32001R0001", "32002R0002"}

    # The conserved partition no longer raises on the assembled set (it would have
    # raised ValueError "Duplicate op_ids" on the pre-fix collision).
    result = apply_eu_ops_conserved(_baseline_statute(), ops)
    assert result.filter_result is not None
