from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import lawvm.new_zealand.acquisition as nz_acquisition
import lawvm.new_zealand.effect_candidates as nz_effect_candidates
import lawvm.new_zealand.instruction_workqueue as nz_instruction_workqueue
import lawvm.new_zealand.operation_surface as nz_operation_surface
import lawvm.new_zealand.payload_surface as nz_payload_surface
import lawvm.new_zealand.source_tree as nz_source_tree


class _FakeArchive:
    def close(self) -> None:
        return None


def test_archived_effect_candidate_surface_reuses_built_surfaces(monkeypatch) -> None:
    operation_surface = SimpleNamespace(work_id="act_public_1992_47", rows=())
    payload_surface = SimpleNamespace(work_id="act_public_1992_47", rows=())
    effect_readiness = SimpleNamespace(work_id="act_public_1992_47", rows=())
    target_document = SimpleNamespace(version_id="v1")
    instruction_workqueue = SimpleNamespace(work_id="act_public_1992_47", rows=())
    result = SimpleNamespace(work_id="act_public_1992_47", rows=())
    calls: list[str] = []

    def build_operation(_db_path: Path, _work_id: str) -> object:
        calls.append("operation")
        return operation_surface

    def build_payload(_db_path: Path, _work_id: str, *, operation_surface: object | None = None) -> object:
        calls.append("payload")
        assert operation_surface is operation_surface_sentinel
        return payload_surface

    operation_surface_sentinel = operation_surface

    def build_readiness(
        _db_path: Path,
        _work_id: str,
        *,
        operation_surface: object | None = None,
        payload_surface: object | None = None,
    ) -> object:
        calls.append("readiness")
        assert operation_surface is operation_surface_sentinel
        assert payload_surface is payload_surface_sentinel
        return effect_readiness

    payload_surface_sentinel = payload_surface

    def build_workqueue(
        operation_surface_arg: object,
        payload_surface_arg: object,
        effect_readiness_arg: object,
        target_document_arg: object,
    ) -> object:
        calls.append("workqueue")
        assert operation_surface_arg is operation_surface_sentinel
        assert payload_surface_arg is payload_surface_sentinel
        assert effect_readiness_arg is effect_readiness_sentinel
        assert target_document_arg is target_document_sentinel
        return instruction_workqueue

    effect_readiness_sentinel = effect_readiness
    target_document_sentinel = target_document

    def build_candidates(
        archive: object,
        *,
        work_id: str,
        operation_surface: object,
        payload_surface: object,
        effect_readiness: object,
        instruction_workqueue: object,
    ) -> object:
        calls.append("candidates")
        assert isinstance(archive, _FakeArchive)
        assert work_id == "act_public_1992_47"
        assert operation_surface is operation_surface_sentinel
        assert payload_surface is payload_surface_sentinel
        assert effect_readiness is effect_readiness_sentinel
        assert instruction_workqueue is instruction_workqueue_sentinel
        return result

    instruction_workqueue_sentinel = instruction_workqueue

    monkeypatch.setattr(nz_source_tree, "parse_archived_work_latest", lambda *_a: target_document)
    monkeypatch.setattr(nz_operation_surface, "build_archived_work_operation_surface", build_operation)
    monkeypatch.setattr(nz_payload_surface, "build_archived_work_payload_surface", build_payload)
    monkeypatch.setattr(nz_effect_candidates, "build_archived_work_effect_readiness_surface", build_readiness)
    monkeypatch.setattr(nz_effect_candidates, "build_instruction_workqueue", build_workqueue)
    monkeypatch.setattr(nz_acquisition, "open_farchive", lambda _db_path: _FakeArchive())
    monkeypatch.setattr(
        nz_effect_candidates,
        "build_effect_candidate_surface_with_archived_source_witnesses",
        build_candidates,
    )

    assert (
        nz_effect_candidates.build_archived_work_effect_candidate_surface(
            Path("nz.farchive"),
            "act_public_1992_47",
        )
        is result
    )
    assert calls == ["operation", "payload", "readiness", "workqueue", "candidates"]


def test_archived_instruction_workqueue_reuses_built_surfaces(monkeypatch) -> None:
    operation_surface = SimpleNamespace(work_id="act_public_1992_47", rows=())
    payload_surface = SimpleNamespace(work_id="act_public_1992_47", rows=())
    effect_readiness = SimpleNamespace(work_id="act_public_1992_47", rows=())
    target_document = SimpleNamespace(version_id="v1")
    result = SimpleNamespace(work_id="act_public_1992_47", rows=())

    def build_payload(_db_path: Path, _work_id: str, *, operation_surface: object | None = None) -> object:
        assert operation_surface is operation_surface_sentinel
        return payload_surface

    operation_surface_sentinel = operation_surface

    def build_readiness(
        _db_path: Path,
        _work_id: str,
        *,
        operation_surface: object | None = None,
        payload_surface: object | None = None,
    ) -> object:
        assert operation_surface is operation_surface_sentinel
        assert payload_surface is payload_surface_sentinel
        return effect_readiness

    payload_surface_sentinel = payload_surface

    def build_workqueue(
        operation_surface_arg: object,
        payload_surface_arg: object,
        effect_readiness_arg: object,
        target_document_arg: object,
    ) -> object:
        assert operation_surface_arg is operation_surface_sentinel
        assert payload_surface_arg is payload_surface_sentinel
        assert effect_readiness_arg is effect_readiness_sentinel
        assert target_document_arg is target_document_sentinel
        return result

    effect_readiness_sentinel = effect_readiness
    target_document_sentinel = target_document

    monkeypatch.setattr(nz_instruction_workqueue, "parse_archived_work_latest", lambda *_a: target_document)
    monkeypatch.setattr(
        nz_instruction_workqueue,
        "build_archived_work_operation_surface",
        lambda *_a: operation_surface,
    )
    monkeypatch.setattr(nz_instruction_workqueue, "build_archived_work_payload_surface", build_payload)
    monkeypatch.setattr(
        nz_instruction_workqueue,
        "build_archived_work_effect_readiness_surface",
        build_readiness,
    )
    monkeypatch.setattr(nz_instruction_workqueue, "build_instruction_workqueue", build_workqueue)

    assert (
        nz_instruction_workqueue.build_archived_work_instruction_workqueue(
            Path("nz.farchive"),
            "act_public_1992_47",
        )
        is result
    )
