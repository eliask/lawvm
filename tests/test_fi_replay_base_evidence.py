from __future__ import annotations

from types import SimpleNamespace

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
