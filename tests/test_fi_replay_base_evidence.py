from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from lawvm.finland.replay_base_evidence import (
    ReplayBaseEvidenceSeedRequest,
    seed_replay_base_evidence_signals,
)
from lawvm.finland.replay_pipeline import ReplaySignalBuffers


def test_seed_replay_base_evidence_signals_projects_base_observations_and_facts() -> None:
    signals = ReplaySignalBuffers.empty()
    ctx = SimpleNamespace(
        base_observations=(
            SimpleNamespace(
                kind="BASE_SOURCE_OBS",
                stage="source_load",
                detail={"note": "base witness"},
            ),
        ),
        source_normalization_facts=(
            SimpleNamespace(
                kind_value="base_tail_prose_absorb",
                path=("body:?", "section:17", "subsection:1"),
                before="old",
                after="new",
                basis_value="tail_prose_peer",
                confidence=1.0,
                explanation="Absorb tail prose.",
            ),
        ),
    )

    seed_replay_base_evidence_signals(
        ReplayBaseEvidenceSeedRequest(parent_id="1996/1261", ctx=ctx),
        signals=signals,
    )

    assert signals.elaboration_observations == [
        {
            "kind": "BASE_SOURCE_OBS",
            "stage": "source_load",
            "source_statute": "1996/1261",
            "target_unit_kind": "statute",
            "target_norm": "1996/1261",
            "target_chapter": "",
            "detail": {"note": "base witness"},
        },
        {
            "kind": "BASE_TAIL_PROSE_ABSORB",
            "stage": "source_normalize",
            "source_statute": "1996/1261",
            "target_unit_kind": "statute",
            "target_norm": "1996/1261",
            "target_chapter": "",
            "detail": {
                "path": ["body:?", "section:17", "subsection:1"],
                "before": "old",
                "after": "new",
                "basis": "tail_prose_peer",
                "confidence": 1.0,
                "explanation": "Absorb tail prose.",
            },
        },
    ]


def test_seed_replay_base_evidence_signals_tolerates_legacy_context_without_facts() -> None:
    signals = ReplaySignalBuffers.empty()
    ctx = SimpleNamespace(base_observations=())

    seed_replay_base_evidence_signals(
        ReplayBaseEvidenceSeedRequest(parent_id="1996/1261", ctx=ctx),
        signals=signals,
    )

    assert signals.elaboration_observations == []
    assert signals.findings == []


def test_seed_replay_base_evidence_signals_projects_ingest_observations_to_findings() -> None:
    """Witnessed XML->IR ingest observations reach the production findings ledger.

    ``StatuteContext.from_xml`` threads an ``_IngestSink`` through the real FI
    parse and folds the witnesses into
    ``ingest_metadata["xml_ingest_observations"]``, but that channel is only read
    by tests. The seeding step must project each governed SCAN.XML_INGEST_*
    observation into ``signals.findings`` (which becomes ``ReplayResult.findings``
    -> certificate ledger / oracle_check), so a dropped/positional/repaired
    source child is no longer a producer with only a test consumer.
    """
    signals = ReplaySignalBuffers.empty()
    ctx = SimpleNamespace(
        base_observations=(),
        ingest_metadata={
            "xml_ingest_observations": (
                {
                    "kind": "SCAN.XML_INGEST_POSITIONAL_LABEL",
                    "family": "xml_ingest",
                    "phase": "ingest",
                    "node_kind": "subsection",
                    "assigned_label": "1",
                    "parent_tag": "section",
                    "snippet": "Eka momentti.",
                },
                {
                    "kind": "SCAN.XML_INGEST_DROPPED_CHILD",
                    "family": "xml_ingest",
                    "phase": "ingest",
                    "tag": "foobar",
                    "parent_tag": "section",
                    "snippet": "",
                },
            ),
        },
    )

    seed_replay_base_evidence_signals(
        ReplayBaseEvidenceSeedRequest(parent_id="9/9999", ctx=ctx),
        signals=signals,
    )

    ingest_findings = [
        f for f in signals.findings if f.kind.startswith("SCAN.XML_INGEST_")
    ]
    assert {f.kind for f in ingest_findings} == {
        "SCAN.XML_INGEST_POSITIONAL_LABEL",
        "SCAN.XML_INGEST_DROPPED_CHILD",
    }
    for finding in ingest_findings:
        assert finding.role == "observation"
        assert finding.blocking is False
        assert finding.stage == "xml_ingest"
        assert finding.source_statute == "9/9999"
        # The triage witness (tag/snippet/label) is preserved on the finding.
        assert "message" in finding.detail
        assert "kind" not in finding.detail  # envelope kind became Finding.kind
    positional = next(
        f for f in ingest_findings if f.kind == "SCAN.XML_INGEST_POSITIONAL_LABEL"
    )
    assert positional.detail["assigned_label"] == "1"
    assert positional.detail["snippet"] == "Eka momentti."
    dropped = next(
        f for f in ingest_findings if f.kind == "SCAN.XML_INGEST_DROPPED_CHILD"
    )
    assert dropped.detail["tag"] == "foobar"


def test_replay_xml_surfaces_ingest_observation_findings_for_real_statute() -> None:
    """A real production statute whose base parse witnesses an ingest event must
    surface that witness on ``ReplayResult.findings`` (the production ledger that
    oracle_check and the certificate findings-ledger consume), not only on the
    test-read ``StatuteContext.ingest_metadata``.

    2016/866's base XML assigns positional labels to unlabelled subsections
    during XML->IR ingest; those guesses must reach the certificate-bound ledger.
    """
    from lawvm.tools.certificate_bundle import build_diagnostic_registry_rows
    from tests.corpus_pin_helpers import pinned_replay

    result = pinned_replay("2016/866", mode="official_consolidation", quiet=True)
    ingest_findings = [
        f for f in result.findings if f.kind.startswith("SCAN.XML_INGEST_")
    ]
    assert ingest_findings, (
        "ingest observations witnessed on the base parse must reach "
        "ReplayResult.findings, not terminate in StatuteContext.ingest_metadata"
    )
    assert any(
        f.kind == "SCAN.XML_INGEST_POSITIONAL_LABEL" for f in ingest_findings
    )
    for finding in ingest_findings:
        assert finding.role == "observation"
        assert finding.blocking is False
        assert finding.source_statute == "2016/866"

    # The certificate findings ledger (§5.7) requires every replay finding's kind
    # to be a registered diagnostic code; assert the ingest codes are registered
    # so the certificate consumer ledgers them rather than rejecting the bundle.
    registered_codes = {
        row["code"]
        for row in build_diagnostic_registry_rows(cast("dict[str, object]", {}))
    }
    for finding in ingest_findings:
        assert finding.kind in registered_codes
