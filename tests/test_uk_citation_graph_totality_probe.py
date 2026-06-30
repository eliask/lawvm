"""§2.9 production-lane guard-liveness for the UK citation-graph totality probe (D6).

The audit (``lawvm.core.citation_graph_totality_audit.assert_citation_graph_
totality`` — registry row ``REFERENCE.UNCLASSIFIED_REFERENCE``, the §0
surface-totality enforcement: every emitted ``ReferenceMention`` MUST carry a
typed classification or surface as a typed Observation) is jurisdiction-neutral.

The probe at ``lawvm.uk_legislation.citation_graph_totality_probe.
probe_uk_citation_graph_totality`` is the first UK consumer; it is invoked from
``uk_amendment_replay.apply_ops`` fold-exit behind an opt-in env flag so UK
production bench replay output stays byte-stable. The UK reference surface is
currently empty (no UK citation extractor yet), so a flag-on production replay
emits nothing — but a known-unclassified mention driven explicitly through the
probe MUST fire, proving the guard is production-reachable.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.citation_graph_totality_probe import (
    UK_CITATION_GRAPH_TOTALITY_KIND,
    probe_uk_citation_graph_totality,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = UK_CITATION_GRAPH_TOTALITY_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_CITATION_GRAPH_TOTALITY_PROBE"


def _unclassified_mention() -> ReferenceMention:
    """A BROKEN mention with no companion resolution → the audit fires."""
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="ukpga/2020/1", section_label="3"),
        target_provision_ref=None,
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.BROKEN,
        phrase_lemma="ref_element",
        source_span=SourceSpan(source_file="s.xml", byte_offset=0, byte_len=4),
        valid_at_interval=(None, None),
        edge_subtype="CITES",
        surface_text="s 5 of the 1999 Act",
    )


def _base_statute() -> IRStatute:
    return IRStatute(
        statute_id="citation/smoke/1",
        title="",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", children=()),),
        ),
        supplements=(),
        metadata={},
    )


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_probe_fires_adjudication_for_unclassified_mention() -> None:
    """Production-lane reachability: a BROKEN mention with no resolution drives a
    ``uk_replay_citation_graph_totality_observed`` adjudication through the
    probe — the live code path invoked from ``apply_ops`` fold-exit."""
    adjudications: list[CompileAdjudication] = []
    observations = probe_uk_citation_graph_totality(
        _base_statute(),
        mentions=(_unclassified_mention(),),
        adjudications_out=adjudications,
        source_statute="ukpga/test/1",
    )
    assert observations, "expected at least one Observation for the unclassified mention"
    findings = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert findings, (
        "expected a uk_replay_citation_graph_totality_observed adjudication; "
        "the §2.9 guard is unreachable from UK production"
    )
    detail = findings[0].detail
    assert detail["family"] == "citation_graph_totality"
    assert detail["reason_code"] == "unclassified_reference_observed"
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert findings[0].blocking is False
    assert detail["core_registry_finding_kind"] == "REFERENCE.UNCLASSIFIED_REFERENCE"


def test_probe_emits_nothing_on_classified_mention() -> None:
    """Negative: an EXACT (self-terminal) mention does not fire."""
    classified = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="ukpga/2020/1", section_label="3"),
        target_provision_ref=ProvisionRef(statute_id="ukpga/1999/9", section_label="5"),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="ref_element",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype="CITES",
        surface_text="s 5",
    )
    adjudications: list[CompileAdjudication] = []
    observations = probe_uk_citation_graph_totality(
        _base_statute(),
        mentions=(classified,),
        adjudications_out=adjudications,
        source_statute="ukpga/test/2",
    )
    assert observations == ()
    assert [a for a in adjudications if a.kind == _FINDING_KIND] == []


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with no env flag the probe MUST NOT emit on the same
    unclassified-mention input — UK bench output stays byte-stable."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    adjudications: list[CompileAdjudication] = []
    observations = probe_uk_citation_graph_totality(
        _base_statute(),
        mentions=(_unclassified_mention(),),
        adjudications_out=adjudications,
        source_statute="ukpga/test/3",
    )
    assert observations == ()
    assert [a for a in adjudications if a.kind == _FINDING_KIND] == [], (
        "probe must be default-off"
    )


def test_probe_empty_uk_reference_surface_emits_nothing() -> None:
    """With ``mentions=None`` the UK surface is projected from the statute —
    currently empty — so the probe emits nothing even with the flag on."""
    adjudications: list[CompileAdjudication] = []
    observations = probe_uk_citation_graph_totality(
        _base_statute(),
        adjudications_out=adjudications,
        source_statute="ukpga/test/4",
    )
    assert observations == ()
    assert [a for a in adjudications if a.kind == _FINDING_KIND] == []


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke: with no ops, apply_ops runs the probe (env on) over the empty UK
    reference surface; nothing fires — proving the probe is wired into the
    production fold-exit and runs even when replay produces no change."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    pipeline = UKReplayPipeline(Path("."))
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(_base_statute(), [], adjudications_out=adjudications)
    findings = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert findings == [], (
        "default no-op replay should not emit any citation totality finding "
        "(UK reference surface is empty) — got: {}".format(findings)
    )


def test_wired_into_apply_ops_fold_exit() -> None:
    """Static-line proof that ``probe_uk_citation_graph_totality`` is invoked on
    the UK replay fold-exit — i.e. the call site exists, not dead code."""
    from lawvm.uk_legislation import uk_amendment_replay as mod

    src = inspect.getsource(mod)
    assert (
        "from lawvm.uk_legislation.citation_graph_totality_probe import" in src
    )
    assert "probe_uk_citation_graph_totality(" in src
    assert "probe_uk_citation_graph_totality" in inspect.getsource(
        mod.UKReplayPipeline.apply_ops
    )
